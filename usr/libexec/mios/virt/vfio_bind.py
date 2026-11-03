#!/usr/bin/env python3
# AI-hint: Dynamic runtime VFIO device unbind from host drivers and rebind to vfio-pci (task_358).
# AI-related: tests/test-virt.py, usr/share/doc/mios/manual/ch21-looking-glass-b7-and-kvmfr.md
"""
MiOS Dynamic Runtime VFIO Device Unbind and Rebind Utility.
Safely switches PCIe devices (GPUs, Audio companions) between host drivers (nvidia, amdgpu, i915, nouveau)
and vfio-pci without rebooting using sysfs driver_override, bind, and unbind interfaces.
Prevents unbinding primary host display rendering Wayland compositors unless explicitly forced.
Enforces whole-device passthrough across all slot siblings and verified IOMMU isolation.
The process lock serializes this binder; callers must coordinate VM/container launchers
through their resource lease. Fresh owner checks refuse active users, including during
rollback. Driver readback proves binding, not inference health. CDI remains caller-owned.
"""

from __future__ import annotations

import argparse
import dataclasses
import signal
import glob
import stat
import contextlib
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
try:
    import fcntl
except ImportError:
    fcntl = None
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

BDF_PATTERN = re.compile(r"^([0-9a-fA-F]{4})[:_]([0-9a-fA-F]{2})[:_]([0-9a-fA-F]{2})\.([0-7])$")

def normalize_bdf(bdf_str: str) -> str:
    """Normalizes BDF to canonical domain:bus:slot.func format."""
    raw = bdf_str.strip()
    m = BDF_PATTERN.match(raw)
    if not m:
        short_m = re.match(r"^([0-9a-fA-F]{2})[:_]([0-9a-fA-F]{2})\.([0-7])$", raw)
        if short_m:
            return f"0000:{short_m.group(1).lower()}:{short_m.group(2).lower()}.{short_m.group(3)}"
        raise ValueError(f"Invalid PCI BDF identifier format: {bdf_str}")
    return f"{m.group(1).lower()}:{m.group(2).lower()}:{m.group(3).lower()}.{m.group(4)}"

def sanitize_bdf_for_fs(bdf_str: str) -> str:
    """Sanitizes BDF string for filesystems (like NTFS) that forbid colons."""
    if os.name == "nt":
        return bdf_str.replace(":", "_")
    return bdf_str

class BindingError(RuntimeError):
    """State, ownership or kernel operation could not be verified."""


@dataclasses.dataclass(frozen=True)
class DeviceState:
    bdf: str
    vendor_id: str
    device_id: str
    current_driver: Optional[str]
    driver_override: Optional[str]
    boot_vga: bool
    slot_address: str
    class_code: str = ""
    modalias: str = ""
    iommu_group: Optional[str] = None


class VFIOBinder:
    """Checked whole-slot transactions. Simulation requires explicit private fixtures."""

    def __init__(self, sysfs_root: str = "/sys", mock: bool = False) -> None:
        self.sysfs_root = os.path.realpath(sysfs_root)
        self.mock = mock
        self._locked = False
        self._transaction_states = []
        if mock and self.sysfs_root == os.path.realpath("/sys"):
            raise BindingError("Mock mode requires a private synthetic sysfs root")
        self.proc_root = os.path.join(self.sysfs_root, "proc") if mock else "/proc"
        self.lock_path = (os.path.join(self.sysfs_root, ".mios-vfio.lock") if mock
                          else "/run/lock/mios-vfio-bind.lock")

    def _get_device_path(self, bdf: str) -> str:
        norm = normalize_bdf(bdf)
        devices = os.path.join(self.sysfs_root, "bus/pci/devices")
        for name in (norm, norm.replace(":", "_")):
            path = os.path.join(devices, name)
            if os.path.isdir(path):
                if os.path.commonpath((self.sysfs_root, os.path.realpath(path))) != self.sysfs_root:
                    raise BindingError(f"Device {norm} resolves outside sysfs")
                return path
        raise BindingError(f"PCI device {norm} is absent")

    def _get_driver_path(self, driver_name: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", driver_name):
            raise BindingError("Invalid kernel driver name")
        return os.path.join(self.sysfs_root, "bus/pci/drivers", driver_name)

    def _read_sysfs(self, path: str, default: Optional[str] = None) -> str:
        try:
            return Path(path).read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            if default is not None:
                return default
            raise BindingError(f"Required sysfs attribute absent: {path}")
        except OSError as exc:
            raise BindingError(f"Unreadable sysfs attribute: {path}: {exc.strerror}") from exc

    def _write_sysfs(self, path: str, data: str) -> bool:
        """Write existing attributes only; never create directories or kernel state."""
        if not self._locked:
            raise BindingError("Sysfs writes require the transaction lock")
        if os.path.commonpath((self.sysfs_root, os.path.realpath(path))) != self.sysfs_root:
            raise BindingError("Sysfs write escaped its root")
        flags = os.O_WRONLY | os.O_CLOEXEC | os.O_NOFOLLOW
        if self.mock:
            flags |= os.O_TRUNC
        fd = os.open(path, flags)
        try:
            payload = data.encode("ascii")
            if os.write(fd, payload) != len(payload):
                raise BindingError(f"Short sysfs write: {path}")
        finally:
            os.close(fd)
        if self.mock:
            self._simulate_write(path, data.strip())
        return True

    def _require_write(self, path: str, data: str) -> None:
        if not self._write_sysfs(path, data):
            raise BindingError(f"Kernel write failed: {path}")

    def _simulate_write(self, path: str, value: str) -> None:
        """Kernel side effects for explicit private fixtures only."""
        if not self.mock or self.sysfs_root == os.path.realpath("/sys"):
            raise BindingError("Synthetic state is forbidden on real sysfs")
        if os.path.basename(path) == "driver_override":
            return
        device = self._get_device_path(value)
        link = os.path.join(device, "driver")
        if os.path.basename(path) == "unbind":
            expected = os.path.basename(os.path.dirname(path))
            if self.get_device_state(value).current_driver != expected:
                raise BindingError("Fixture unbind driver mismatch")
            os.unlink(link)
        elif os.path.basename(path) == "drivers_probe":
            state = self.get_device_state(value)
            if state.current_driver:
                raise BindingError("Fixture probe of a bound device")
            driver = state.driver_override or self._read_sysfs(os.path.join(device, "native_driver"))
            target = self._get_driver_path(driver)
            if not os.path.isdir(target):
                raise BindingError("Fixture driver is absent")
            os.symlink(target, link)
        else:
            raise BindingError("Unsupported synthetic sysfs operation")

    def get_device_state(self, bdf: str) -> DeviceState:
        norm = normalize_bdf(bdf)
        device = self._get_device_path(norm)
        vendor = self._read_sysfs(os.path.join(device, "vendor"))
        product = self._read_sysfs(os.path.join(device, "device"))
        if not all(re.fullmatch(r"0x[0-9a-fA-F]{4}", value) for value in (vendor, product)):
            raise BindingError(f"Invalid PCI identity for {norm}")
        link = os.path.join(device, "driver")
        driver = None
        if os.path.lexists(link):
            if not os.path.islink(link) or not os.path.isdir(link):
                raise BindingError(f"Invalid driver link for {norm}")
            driver = os.path.basename(os.path.realpath(link))
            if os.path.realpath(link) != os.path.realpath(self._get_driver_path(driver)):
                raise BindingError(f"Unexpected driver link for {norm}")
        override = self._read_sysfs(os.path.join(device, "driver_override"))
        group_link = os.path.join(device, "iommu_group")
        group = os.path.basename(os.path.realpath(group_link)) if os.path.isdir(group_link) else None
        if group is not None and not group.isdecimal():
            raise BindingError(f"Invalid IOMMU group for {norm}")
        return DeviceState(norm, vendor.lower(), product.lower(), driver,
            override if override and override != "(null)" else None,
            self._read_sysfs(os.path.join(device, "boot_vga"), "0") == "1",
            norm.rsplit(".", 1)[0], self._read_sysfs(os.path.join(device, "class"), ""),
            self._read_sysfs(os.path.join(device, "modalias"), ""), group)

    def is_primary_gpu(self, bdf: str) -> bool:
        return self.get_device_state(bdf).boot_vga

    def get_slot_siblings(self, bdf: str) -> List[str]:
        norm = normalize_bdf(bdf)
        self._get_device_path(norm)
        slot = norm.rsplit(".", 1)[0]
        siblings = []
        for entry in os.listdir(os.path.join(self.sysfs_root, "bus/pci/devices")):
            try:
                candidate = normalize_bdf(entry)
            except ValueError:
                continue
            if candidate.rsplit(".", 1)[0] == slot:
                siblings.append(candidate)
        if norm not in siblings:
            raise BindingError("Target disappeared while enumerating its slot")
        return sorted(set(siblings))

    def _snapshot(self, bdf: str) -> List[DeviceState]:
        states = [self.get_device_state(name) for name in self.get_slot_siblings(bdf)]
        names = {state.bdf for state in states}
        for state in states:
            if not state.iommu_group:
                raise BindingError(f"No verified IOMMU group for {state.bdf}")
            group = os.path.join(self.sysfs_root, "kernel/iommu_groups", state.iommu_group, "devices")
            members = {normalize_bdf(name) for name in os.listdir(group)}
            if state.bdf not in members or not members <= names:
                raise BindingError(f"IOMMU group {state.iommu_group} includes devices outside this slot")
        return states

    def _run(self, command: List[str]) -> str:
        result = subprocess.run(command, text=True, capture_output=True, timeout=10, check=False)
        if result.returncode:
            raise BindingError(f"{command[0]} could not verify or prepare device ownership")
        return result.stdout

    def _live_domain_xml(self) -> List[str]:
        if self.mock:
            value = json.loads(self._read_sysfs(os.path.join(self.sysfs_root, "live_domains.json")))
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise BindingError("Invalid explicit VM fixture")
            return value
        names = self._run(["virsh", "--connect", "qemu:///system", "list", "--name"]).splitlines()
        return [self._run(["virsh", "--connect", "qemu:///system", "dumpxml", name])
                for name in names if name.strip()]

    def _owners(self, states: List[DeviceState]) -> List[dict]:
        names = {state.bdf for state in states}
        groups = {state.iommu_group for state in states}
        owners = []
        for xml in self._live_domain_xml():
            domain = ET.fromstring(xml)
            for address in domain.findall(".//hostdev[@type='pci']/source/address"):
                bdf = (f"{int(address.attrib['domain'], 16):04x}:"
                       f"{int(address.attrib['bus'], 16):02x}:"
                       f"{int(address.attrib['slot'], 16):02x}.{int(address.attrib['function'], 16)}")
                if bdf in names:
                    owners.append({"kind": "live_vm", "resource": bdf})
        device_nodes = set()
        device_numbers = set()
        known_nodes = set()
        for cls, devdir in (("drm", "/dev/dri"), ("sound", "/dev/snd")):
            directory = os.path.join(self.sysfs_root, "class", cls)
            if not os.path.isdir(directory):
                continue
            for entry in os.listdir(directory):
                known_nodes.add(f"{devdir}/{entry}")
                device = os.path.join(directory, entry, "device")
                components = set(os.path.realpath(device).split(os.sep))
                if names & {part.replace("_", ":") for part in components}:
                    device_nodes.add(f"{devdir}/{entry}")
                    number = self._read_sysfs(os.path.join(directory, entry, "dev"), "")
                    if number:
                        major, minor = number.split(":", 1)
                        device_numbers.add((int(major), int(minor)))
        nvidia = any(state.vendor_id == "0x10de" for state in states)
        nvidia_majors = set()
        if nvidia:
            devroot = os.path.join(self.sysfs_root, "dev") if self.mock else "/dev"
            for path in glob.glob(os.path.join(devroot, "nvidia*")) + glob.glob(os.path.join(devroot, "nvidia-caps/*")):
                try:
                    metadata = os.stat(path)
                except FileNotFoundError:
                    continue
                if stat.S_ISCHR(metadata.st_mode):
                    nvidia_majors.add(os.major(metadata.st_rdev))
        def held_number(path: str) -> bool:
            metadata = os.stat(path)
            if not stat.S_ISCHR(metadata.st_mode):
                return False
            major, minor = os.major(metadata.st_rdev), os.minor(metadata.st_rdev)
            return (major, minor) in device_numbers or major in nvidia_majors
        def held(resource: str) -> bool:
            resource = resource.removesuffix(" (deleted)")
            if resource in device_nodes:
                return True
            if resource.startswith("/dev/dri/") and resource not in known_nodes:
                return True  # a stale DRM handle cannot be attributed safely
            if resource.startswith("/dev/vfio/"):
                if resource.rsplit("/", 1)[-1] in groups or "/devices/" in resource:
                    return True
            if resource == "/dev/iommu" or ("anon_inode" in resource and "vfio" in resource.lower()):
                return True  # these handles cannot be safely attributed to one group
            if nvidia and re.fullmatch(r"/dev/nvidia(?:[0-9]+|ctl|-uvm(?:-tools)?|-modeset|-caps/nvidia-cap[0-9]+)", resource):
                return True
            return any(f"/{name}/resource" in resource for name in names)
        for pid in os.listdir(self.proc_root):
            if not pid.isdecimal():
                continue
            proc = os.path.join(self.proc_root, pid)
            try:
                fd_dir = os.path.join(proc, "fd")
                for fd in os.listdir(fd_dir):
                    try:
                        resource = os.readlink(os.path.join(fd_dir, fd))
                    except FileNotFoundError:
                        continue
                    if held(resource):
                        owners.append({"pid": int(pid), "kind": "open_device", "resource": resource})
                    else:
                        try:
                            if held_number(os.path.join(fd_dir, fd)):
                                owners.append({"pid": int(pid), "kind": "open_device", "resource": resource})
                        except FileNotFoundError:
                            continue
                for line in Path(proc, "maps").read_text().splitlines():
                    fields = line.split(None, 5)
                    if len(fields) == 6:
                        resource = fields[5]
                        if held(resource):
                            owners.append({"pid": int(pid), "kind": "mapped_device", "resource": resource})
                        elif resource.startswith("/dev/"):
                            try:
                                mapped = os.path.join(proc, "map_files", fields[0])
                                if held_number(mapped):
                                    owners.append({"pid": int(pid), "kind": "mapped_device", "resource": resource})
                            except FileNotFoundError:
                                if os.path.exists(proc):
                                    raise BindingError(f"Cannot attribute mapped device for pid {pid}")
            except FileNotFoundError:
                if os.path.exists(proc):
                    raise BindingError(f"Incomplete process ownership evidence for pid {pid}")
            except PermissionError as exc:
                raise BindingError(f"Cannot inspect device ownership for pid {pid}") from exc
        return owners

    def _assert_idle(self, states: List[DeviceState]) -> None:
        owners = self._owners(states)
        if owners:
            raise BindingError("Device is in use: " + json.dumps(owners[:16], sort_keys=True))

    @contextlib.contextmanager
    def _lock(self):
        if self._locked:
            raise BindingError("A transaction is already active on this binder")
        if fcntl is None:
            raise BindingError("Kernel driver transactions require POSIX process locking")
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            metadata = os.fstat(fd)
            if metadata.st_uid != os.geteuid() or metadata.st_nlink != 1 or metadata.st_mode & 0o022:
                raise BindingError("Unsafe transaction lock ownership or permissions")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._locked = True
            yield
        finally:
            self._locked = False
            self._transaction_states = []
            os.close(fd)

    def _native_driver(self, state: DeviceState, requested: Optional[str]) -> str:
        if self.mock:
            native = self._read_sysfs(os.path.join(self._get_device_path(state.bdf), "native_driver"))
            if requested and requested != native:
                raise BindingError(f"Requested driver does not match {state.bdf} modalias")
            return requested or native
        if not state.modalias or not re.fullmatch(r"pci:[A-Za-z0-9]+", state.modalias):
            raise BindingError(f"No valid PCI modalias for {state.bdf}")
        modules = {name.replace("-", "_") for name in self._run(
            ["modprobe", "--resolve-alias", state.modalias]).splitlines()}
        def loaded_candidates():
            candidates = []
            directory = os.path.join(self.sysfs_root, "bus/pci/drivers")
            for driver in os.listdir(directory):
                module = os.path.join(directory, driver, "module")
                if driver not in ("vfio-pci", "pci-stub") and os.path.islink(module):
                    if os.path.basename(os.path.realpath(module)).replace("-", "_") in modules:
                        candidates.append(driver)
            return candidates
        candidates = loaded_candidates()
        if requested:
            self._get_driver_path(requested)  # validate the explicit kernel name
            if requested not in candidates and requested.replace("-", "_") not in modules:
                raise BindingError(f"Requested driver is not a modalias match for {state.bdf}")
            # A display module may need the device released before it can initialize.
            # Load it after unbind, while driver_override permits only this driver.
            return requested
        if not candidates:
            self._run(["modprobe", "--use-blacklist", "--", state.modalias])
            candidates = loaded_candidates()
        if len(candidates) != 1:
            raise BindingError(f"Native driver for {state.bdf} is ambiguous or unavailable")
        return candidates[0]

    def unbind_device(self, bdf: str) -> bool:
        """Checked internal step under the transaction lock and ownership checks."""
        if not self._locked or not self._transaction_states:
            raise BindingError("Unbind requires a checked whole-slot transaction")
        self._assert_idle(self._transaction_states)
        state = self.get_device_state(bdf)
        original = next(item for item in self._transaction_states if item.bdf == state.bdf)
        self._same_device(state, original)
        if state.current_driver:
            path = os.path.join(self._get_driver_path(state.current_driver), "unbind")
            self._require_write(path, state.bdf + "\n")
            if self.get_device_state(bdf).current_driver is not None:
                raise BindingError(f"Kernel did not unbind {state.bdf}")
        return True

    def _override(self, bdf: str, driver: Optional[str]) -> None:
        original = next(item for item in self._transaction_states if item.bdf == normalize_bdf(bdf))
        self._same_device(self.get_device_state(bdf), original)
        self._require_write(os.path.join(self._get_device_path(bdf), "driver_override"), (driver or "") + "\n")
        if self.get_device_state(bdf).driver_override != driver:
            raise BindingError(f"Kernel did not apply override for {bdf}")

    def _probe(self, bdf: str, driver: str) -> None:
        self._assert_idle(self._transaction_states)
        original = next(item for item in self._transaction_states if item.bdf == normalize_bdf(bdf))
        self._same_device(self.get_device_state(bdf), original)
        current = self.get_device_state(bdf).current_driver
        if current == driver:
            return  # module registration may already have bound the permitted driver
        if current is not None:
            raise BindingError(f"Unexpected driver appeared while probing {bdf}")
        self._require_write(os.path.join(self.sysfs_root, "bus/pci/drivers_probe"), bdf + "\n")
        if self.get_device_state(bdf).current_driver != driver:
            raise BindingError(f"Kernel did not bind {bdf} to {driver}")

    def _same_device(self, current: DeviceState, original: DeviceState) -> None:
        if dataclasses.replace(current, current_driver=original.current_driver,
                               driver_override=original.driver_override) != original:
            raise BindingError(f"PCI identity or isolation changed for {original.bdf}")

    def _rollback(self, states: List[DeviceState]) -> dict:
        errors = []
        for original in reversed(states):
            try:
                self._assert_idle(states)
                current = self.get_device_state(original.bdf)
                self._same_device(current, original)
                if current == original:
                    continue
                if current.current_driver != original.current_driver:
                    self._override(original.bdf, original.current_driver or "none")
                    self.unbind_device(original.bdf)
                    if original.current_driver:
                        self._probe(original.bdf, original.current_driver)
                self._override(original.bdf, original.driver_override)
                if self.get_device_state(original.bdf) != original:
                    raise BindingError("Rollback readback differs from original state")
            except Exception as exc:
                errors.append({"bdf": original.bdf, "error": str(exc)})
        observed = []
        for original in states:
            try:
                current = self.get_device_state(original.bdf)
                observed.append(dataclasses.asdict(current))
                if current != original:
                    errors.append({"bdf": original.bdf, "error": "Original binding was not restored"})
            except Exception as exc:
                errors.append({"bdf": original.bdf, "error": str(exc)})
        try:
            if self._snapshot(states[0].bdf) != states:
                errors.append({"error": "Original whole-slot topology or state was not restored"})
        except Exception as exc:
            errors.append({"error": str(exc)})
        return {"success": not errors, "errors": errors, "observed": observed}

    def plan(self, bdf: str, *, to_host: bool = False, force: bool = False) -> Dict[str, Any]:
        states = self._snapshot(bdf)
        self._assert_idle(states)
        if not force and any(state.boot_vga for state in states):
            raise BindingError("A slot function is the primary host display (boot_vga=1); Wayland ownership must be released")
        return {"schema_version": 1, "simulated": self.mock,
                "status": "planned", "target_bdf": normalize_bdf(bdf), "bound": False,
                "target_driver": "host_native" if to_host else "vfio-pci",
                "slot_devices": [dataclasses.asdict(state) for state in states]}

    def _require_runtime(self) -> None:
        if not self.mock and self.sysfs_root != os.path.realpath("/sys"):
            raise BindingError("Synthetic roots require explicit mock mode for mutation")
        if not self.mock and os.geteuid() != 0:
            raise BindingError("Kernel driver changes require root ownership inspection")

    def _transition(self, bdf: str, to_host: bool, host_driver=None, force=False) -> Dict[str, Any]:
        norm = normalize_bdf(bdf)
        result = {"schema_version": 1, "simulated": self.mock,
                  "status": "refused", "target_bdf": norm, "bound": False,
                  "before": [], "after": [], "rollback": None,
                  "target_driver": "host_native" if to_host else "vfio-pci",
                  "invariants": {"gpu_fractioning_limit": "Whole-device VFIO ownership is required.",
                                 "venus_vs_cuda": "Guest CUDA requires whole-device VFIO passthrough."}}
        try:
            self._require_runtime()
            with self._lock():
                self.plan(norm, to_host=to_host, force=force)
                originals = self._snapshot(norm)
                result["before"] = [dataclasses.asdict(state) for state in originals]
                self._transaction_states = originals
                self._assert_idle(originals)
                try:
                    if to_host:
                        targets = {state.bdf: self._native_driver(state, host_driver if state.bdf == norm else None)
                                   for state in originals}
                    else:
                        if not self.mock:
                            self._run(["modprobe", "--", "vfio-pci"])
                        targets = {state.bdf: "vfio-pci" for state in originals}
                    if self._snapshot(norm) != originals:
                        raise BindingError("Device state changed during driver preparation")
                    expected = list(originals)
                    for index, state in enumerate(originals):
                        self._assert_idle(originals)
                        if self._snapshot(norm) != expected:
                            raise BindingError("Slot state changed during transaction")
                        target = targets[state.bdf]
                        self._override(state.bdf, target)
                        if self.get_device_state(state.bdf).current_driver != target:
                            self.unbind_device(state.bdf)
                            if to_host and not self.mock:
                                self._run(["modprobe", "--use-blacklist", "--", state.modalias])
                            self._probe(state.bdf, target)
                        expected[index] = self.get_device_state(state.bdf)
                    if to_host:
                        for index, state in enumerate(originals):
                            if self._snapshot(norm) != expected:
                                raise BindingError("Slot state changed before clearing overrides")
                            self._override(state.bdf, None)
                            expected[index] = self.get_device_state(state.bdf)
                    final = self._snapshot(norm)
                    if len(final) != len(originals) or any(state.current_driver != targets[state.bdf]
                           or state.driver_override != (None if to_host else "vfio-pci") for state in final):
                        raise BindingError("Final whole-slot state differs from requested ownership")
                    for state, original in zip(final, originals):
                        self._same_device(state, original)
                except BaseException as exc:
                    rollback = self._rollback(originals)
                    result.update(status="failed", error=str(exc) or type(exc).__name__, rollback=rollback,
                                  after=rollback["observed"])
                    return result
                result.update(status="success", bound=True,
                    after=[dataclasses.asdict(state) for state in final],
                    siblings=[{"bdf": state.bdf, "previous_driver": original.current_driver,
                               "new_driver": state.current_driver, "override": state.driver_override,
                               "success": True} for original, state in zip(originals, final)],
                    message=f"Verified {len(final)} functions on slot {norm.rsplit('.', 1)[0]}")
                return result
        except Exception as exc:
            result["error"] = str(exc)
            return result

    def bind_to_vfio(self, bdf: str, force: bool = False) -> Dict[str, Any]:
        return self._transition(bdf, False, force=force)

    def rebind_to_host(self, bdf: str, host_driver: Optional[str] = None) -> Dict[str, Any]:
        return self._transition(bdf, True, host_driver=host_driver)

    def get_status(self, bdf: Optional[str] = None) -> Dict[str, Any]:
        if not bdf:
            raise BindingError("A target PCI BDF is required")
        target = normalize_bdf(bdf)
        states = [self.get_device_state(name) for name in self.get_slot_siblings(target)]
        return {"schema_version": 1, "simulated": self.mock,
                "target_bdf": target, "is_primary_gpu": any(state.boot_vga for state in states),
                "slot_devices": [{**dataclasses.asdict(state), "is_vfio": state.current_driver == "vfio-pci"}
                                 for state in states]}


def main() -> int:
    parser = argparse.ArgumentParser(description="MiOS checked whole-slot PCI driver transactions")
    parser.add_argument("--device", "-d", help="Target PCI BDF")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--to-vfio", action="store_true")
    actions.add_argument("--to-host", action="store_true")
    actions.add_argument("--status", action="store_true")
    parser.add_argument("--plan", action="store_true", help="Read ownership and topology without driver changes")
    parser.add_argument("--driver", help="Native driver override for the requested function only")
    parser.add_argument("--force", action="store_true", help="Allow boot_vga only after all process/VM owners are released")
    parser.add_argument("--sysfs-root", default="/sys")
    parser.add_argument("--mock", action="store_true", help="Explicit private kernel/ownership fixtures only")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if not args.device:
        parser.error("--device is required; PCI ownership is never inferred from a fixed slot")
    def terminate(signum, frame):
        raise InterruptedError("Driver transaction interrupted")
    previous_term = signal.signal(signal.SIGTERM, terminate)
    try:
        binder = VFIOBinder(args.sysfs_root, mock=args.mock)
        if args.plan:
            result = binder.plan(args.device, to_host=args.to_host, force=args.force)
        elif args.to_vfio:
            result = binder.bind_to_vfio(args.device, force=args.force)
        elif args.to_host:
            result = binder.rebind_to_host(args.device, host_driver=args.driver)
        else:
            result = binder.get_status(args.device)
    except Exception as exc:
        result = {"schema_version": 1, "simulated": args.mock,
                  "status": "refused", "bound": False, "error": str(exc)}
    finally:
        signal.signal(signal.SIGTERM, previous_term)
    print(json.dumps(result, indent=2) if args.json else json.dumps(result))
    return 0 if result.get("status", "success") in ("success", "planned") else 1


if __name__ == "__main__":
    sys.exit(main())
