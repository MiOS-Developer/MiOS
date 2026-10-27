#!/usr/bin/env python3
# AI-hint: MiOS-Xbox GPU-less DISM build worker (xb-14, Q9): every value from the rendered xbox-spec.json ([windows.xbox.vm.worker], [windows.xbox.build]). The worker domain is derived from the base domain's INACTIVE XML (read-only) on a qcow2 copy-on-write overlay of its disk (the base disk/domain are never written), with every hostdev/ivshmem/TPM/SPICE device stripped (a hostdev is refused), plus ONE exchange disk the host formats and fills (job inputs) and later reads back read-only (ISO, result, logs) with libguestfs. WinRM signs in with the SSOT-declared credential source (the base guest's answer file), never printed. Concurrency/RAM/watchdog guards refuse to start next to the target or base VM.
# AI-related: usr/libexec/mios/win/xbox/build.py, usr/share/mios/windows/build/MiOS-Xbox-Worker.ps1, usr/share/mios/windows/build/New-MiOSXboxImage.ps1, tests/test-mios-xbox-build.py
# AI-functions: WorkerConfig, generate_worker_xml, validate_worker_xml, worker_credentials, virsh_file_access, preflight, prepare_exchange, define, start, wait_winrm, submit_job, poll_job, stop, drop_overlay, extract, cleanup, main
"""MiOS-Xbox build worker manager.

  worker.py --spec BUILD/xbox-spec.json xml        # print the worker domain XML (no side effects)
  worker.py --spec BUILD/xbox-spec.json preflight  # guards only
  worker.py --spec BUILD/xbox-spec.json stop|cleanup|assert-shutoff
(the full define -> run -> extract sequence is driven by build.py)."""

from __future__ import annotations

import argparse
import base64
import copy
import dataclasses
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from typing import Any, Callable, Dict, List, Optional, Tuple

UNS = "urn:schemas-microsoft-com:unattend"
EXCHANGE_LABEL = "MIOSXCHG"
EXCHANGE_ROOT = "mios-build"          # top directory on the exchange volume
GPT_BASIC_DATA = "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7"
WATCHDOG_STATE = "/sys/class/watchdog/watchdog0/state"
CONNECT = "qemu:///system"
# Devices the worker never carries: GPUs / passthrough, the Looking Glass framebuffer, TPM state of the
# base, SPICE agent channels, audio (and the SPICE audio backend, which needs SPICE graphics), USB
# redirection, watchdogs.
STRIP_DEVICES = ("hostdev", "shmem", "tpm", "channel", "sound", "audio", "redirdev", "smartcard", "watchdog", "filesystem")
# Windows client editions schedule on at most 2 sockets: the worker's vCPUs are cores of ONE socket.
BOOT_ID_PS = "(Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToUniversalTime().ToString('o')"
Runner = Callable[[List[str]], Tuple[int, str]]


class WorkerError(RuntimeError):
    """A worker guard or step failed: the build must stop (the base domain stays untouched)."""


def _default_runner(cmd: List[str], env: Optional[Dict[str, str]] = None, timeout: int = 3600) -> Tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, str(e)


@dataclasses.dataclass
class WorkerConfig:
    name: str
    base: str
    target: str
    memory_gib: int
    vcpus: int
    credential_source: str
    base_answer_file: str
    work_dir: str
    exchange_gib: int
    headroom_gib: int
    boot_timeout_s: int
    build_timeout_s: int
    poll_s: int
    guestfs_mem_mib: int
    winrm_port: int
    transport: str
    op_timeout_s: int
    read_timeout_s: int

    @classmethod
    def from_spec(cls, doc: Dict[str, Any], work_dir: str) -> "WorkerConfig":
        x = doc["spec"]
        w, b, p = x["vm"]["worker"], x["build"], x["probe"]
        if w["gpu_passthrough"]:
            raise WorkerError("windows.xbox.vm.worker.gpu_passthrough = true is refused (the worker never holds a GPU)")
        return cls(name=w["name"], base=w["overlay_of"], target=x["vm"]["name"], memory_gib=int(w["memory_gib"]),
                   vcpus=int(w["vcpus"]), credential_source=w["credential_source"], base_answer_file=w["base_answer_file"],
                   work_dir=work_dir, exchange_gib=int(b["exchange_disk_gib"]), headroom_gib=int(b["memory_headroom_gib"]),
                   boot_timeout_s=int(b["worker_boot_timeout_s"]), build_timeout_s=int(b["worker_build_timeout_s"]),
                   poll_s=int(b["worker_poll_s"]), guestfs_mem_mib=int(b["extract_memsize_mib"]),
                   winrm_port=int(p["winrm_port"]), transport=p["transport"], op_timeout_s=int(p["operation_timeout_s"]),
                   read_timeout_s=int(p["read_timeout_s"]))

    @property
    def overlay(self) -> str:
        return os.path.join(self.work_dir, "worker-overlay.qcow2")

    @property
    def exchange(self) -> str:
        return os.path.join(self.work_dir, "exchange.qcow2")

    @property
    def nvram(self) -> str:
        return os.path.join(self.work_dir, "worker-OVMF_VARS.fd")

    @property
    def xml_path(self) -> str:
        return os.path.join(self.work_dir, "worker-domain.xml")


# ---------------------------------------------------------------------------
# domain XML (pure)
# ---------------------------------------------------------------------------

def validate_worker_xml(xml: "ET.Element | str", cfg: Optional[WorkerConfig] = None) -> None:
    """Refuse a worker definition that could hold a GPU or touch the base disk."""
    root = ET.fromstring(xml) if isinstance(xml, str) else xml
    if root.findall(".//hostdev"):
        raise WorkerError("worker XML contains a hostdev: refused (the worker never holds a GPU)")
    for tag in ("shmem", "tpm"):
        if root.findall(f".//devices/{tag}"):
            raise WorkerError(f"worker XML contains a {tag} device: refused")
    for iface in root.findall(".//devices/interface"):
        if iface.get("type") == "hostdev":
            raise WorkerError("worker XML contains a hostdev interface: refused")
    spice = any(g.get("type") == "spice" for g in root.findall(".//devices/graphics"))
    if not spice and any(a.get("type") == "spice" for a in root.findall(".//devices/audio")):
        raise WorkerError("worker XML has a SPICE audio backend without SPICE graphics (libvirt refuses it)")
    topo = root.find("./cpu/topology")
    if topo is not None and int(topo.get("sockets", "1")) > 2:
        raise WorkerError("worker XML has more than 2 CPU sockets (Windows client editions use only 2)")
    if cfg is not None:
        if (root.findtext("name") or "") != cfg.name:
            raise WorkerError("worker XML name differs from windows.xbox.vm.worker.name")
        disks = [d.find("source").get("file") for d in root.findall(".//devices/disk")
                 if d.find("source") is not None and d.find("source").get("file")]
        for f in disks:
            if os.path.realpath(f) not in (os.path.realpath(cfg.overlay), os.path.realpath(cfg.exchange)):
                raise WorkerError(f"worker XML attaches {f}: only the overlay and the exchange disk are allowed")
        nv = root.find("./os/nvram")
        if nv is not None and os.path.realpath((nv.text or "").strip()) != os.path.realpath(cfg.nvram):
            raise WorkerError("worker XML points at another domain's NVRAM")


def base_disk(base_xml: str) -> Tuple[str, str, str]:
    """(file, bus, format) of the base domain's first disk device."""
    root = ET.fromstring(base_xml)
    for d in root.findall(".//devices/disk"):
        if d.get("device") == "disk" and d.find("source") is not None and d.find("source").get("file"):
            t = d.find("target")
            drv = d.find("driver")
            return d.find("source").get("file"), (t.get("bus") if t is not None else "sata"), \
                (drv.get("type") if drv is not None else "qcow2")
    raise WorkerError("base domain has no file-backed system disk")


def generate_worker_xml(base_xml: str, cfg: WorkerConfig) -> str:
    """The worker definition derived from the base domain's inactive XML: same firmware, machine, CPU and
    NIC model (the installed guest has drivers for them); name/uuid/memory/vcpus from the spec; disks =
    overlay + exchange; every passthrough/shared-memory/TPM/agent device stripped; VNC on localhost."""
    root = ET.fromstring(base_xml)
    if root.tag != "domain":
        raise WorkerError("base XML is not a libvirt domain")
    _, bus, _ = base_disk(base_xml)
    dom = copy.deepcopy(root)
    for tag in ("uuid", "title", "metadata", "description", "currentMemory", "memory", "vcpu", "iothreads",
                "cputune", "numatune", "memoryBacking"):
        for e in dom.findall(tag):
            dom.remove(e)
    dom.find("name").text = cfg.name
    idx = list(dom).index(dom.find("name")) + 1
    for i, (tag, attrs, text) in enumerate((
            ("description", {}, "MiOS-Xbox GPU-less DISM build worker (copy-on-write overlay; usr/libexec/mios/win/xbox/worker.py)"),
            ("memory", {"unit": "GiB"}, str(cfg.memory_gib)),
            ("currentMemory", {"unit": "GiB"}, str(cfg.memory_gib)),
            ("vcpu", {"placement": "static"}, str(cfg.vcpus)))):
        e = ET.Element(tag, attrs)
        e.text = text
        dom.insert(idx + i, e)
    cpu = dom.find("cpu")
    if cpu is not None:
        topo = cpu.find("topology")
        if topo is not None:
            cpu.remove(topo)
        # libvirt's default without <topology> is one socket per vCPU; Windows Pro would use only 2 of them.
        cpu.insert(0, ET.Element("topology", {"sockets": "1", "dies": "1", "clusters": "1",
                                              "cores": str(cfg.vcpus), "threads": "1"}))
    for e in dom.findall("pm"):
        dom.remove(e)
    pm = ET.Element("pm")   # a sleeping worker would stall the build job: no S3/S4 at all
    ET.SubElement(pm, "suspend-to-mem", {"enabled": "no"})
    ET.SubElement(pm, "suspend-to-disk", {"enabled": "no"})
    feats = dom.find("features")
    dom.insert(list(dom).index(feats) + 1 if feats is not None else len(dom), pm)
    os_el = dom.find("os")
    if os_el is None:
        raise WorkerError("base XML has no <os>")
    nv = os_el.find("nvram")
    if nv is not None:
        nv.text = cfg.nvram
        for a in ("template", "templateFormat"):
            nv.attrib.pop(a, None)
    for bo in os_el.findall("boot"):
        os_el.remove(bo)
    ET.SubElement(os_el, "boot", {"dev": "hd"})
    dev = dom.find("devices")
    for child in list(dev):
        if child.tag in STRIP_DEVICES or child.tag == "disk" or child.tag == "controller" and child.get("type") == "virtio-serial":
            dev.remove(child)
        elif child.tag == "graphics":
            dev.remove(child)
        elif child.tag == "interface":
            if child.get("type") == "hostdev":
                raise WorkerError("base domain NIC is a hostdev; the worker cannot reuse it")
            for a in child.findall("address"):
                child.remove(a)
        elif child.tag == "video":
            for a in child.findall("address"):
                child.remove(a)
    letters = iter("abcdefgh")

    def disk(path: str, fmt: str) -> None:
        d = ET.SubElement(dev, "disk", {"type": "file", "device": "disk"})
        ET.SubElement(d, "driver", {"name": "qemu", "type": fmt, "discard": "unmap"})
        ET.SubElement(d, "source", {"file": path})
        ET.SubElement(d, "target", {"dev": ("vd" if bus == "virtio" else "sd") + next(letters), "bus": bus})

    disk(cfg.overlay, "qcow2")
    disk(cfg.exchange, "qcow2")
    ET.SubElement(dev, "graphics", {"type": "vnc", "port": "-1", "autoport": "yes", "listen": "127.0.0.1"})
    validate_worker_xml(dom, cfg)
    ET.indent(dom, space="  ")
    return ET.tostring(dom, encoding="unicode")


# ---------------------------------------------------------------------------
# credentials (SSOT-declared source; never printed)
# ---------------------------------------------------------------------------

def worker_credentials(cfg: WorkerConfig) -> Tuple[str, str]:
    if cfg.credential_source != "base-answer-file":
        raise WorkerError(f"windows.xbox.vm.worker.credential_source {cfg.credential_source!r} unsupported")
    try:
        root = ET.parse(cfg.base_answer_file).getroot()
    except (OSError, ET.ParseError) as e:
        raise WorkerError(f"windows.xbox.vm.worker.base_answer_file unreadable ({type(e).__name__})") from None
    for la in root.iter(f"{{{UNS}}}LocalAccount"):
        groups = [g.strip().lower() for g in (la.findtext(f"{{{UNS}}}Group") or "").split(";")]
        if "administrators" not in groups:
            continue
        name = (la.findtext(f"{{{UNS}}}Name") or "").strip()
        pe = la.find(f"{{{UNS}}}Password")
        if not name or pe is None:
            continue
        val = pe.findtext(f"{{{UNS}}}Value") or ""
        if (pe.findtext(f"{{{UNS}}}PlainText") or "true").strip().lower() == "false":
            try:
                val = base64.b64decode(val).decode("utf-16-le")
            except ValueError:
                raise WorkerError("base answer file password encoding unsupported") from None
            if val.endswith("Password"):
                val = val[: -len("Password")]
        if val:
            return name, val
    raise WorkerError("windows.xbox.vm.worker.base_answer_file has no Administrators LocalAccount with a password")


# ---------------------------------------------------------------------------
# guards
# ---------------------------------------------------------------------------

def domstate(name: str, runner: Runner = _default_runner) -> Optional[str]:
    rc, out = runner(["virsh", "-c", CONNECT, "domstate", name])
    return out.strip().lower() if rc == 0 else None


def mem_available_gib() -> float:
    with open("/proc/meminfo", encoding="ascii") as fh:
        for ln in fh:
            if ln.startswith("MemAvailable:"):
                return int(ln.split()[1]) / (1024 * 1024)
    raise WorkerError("MemAvailable not in /proc/meminfo")


def watchdog_inactive() -> bool:
    """Reads the sysfs state only -- never opens /dev/watchdog*."""
    try:
        with open(WATCHDOG_STATE, encoding="ascii") as fh:
            return fh.read().strip() == "inactive"
    except FileNotFoundError:
        return True


VIRSH_PROBE_XML = "<domain type='test'><name>mios-virsh-probe</name><memory>1024</memory><os><type>hvm</type></os></domain>\n"


def virsh_file_access(work_dir: str, runner: Runner = _default_runner) -> None:
    """virsh must read files this process writes under work_dir: ``virsh define`` reads the worker XML client-side.
    Under SELinux a virsh exec'd from a confined system domain (initrc_t, e.g. a shell script started by systemd-run)
    runs as virsh_t and gets "Failed to open file ...: Permission denied" -- which surfaced only at the worker, after
    the 35-minute stage. The probe defines a throw-away domain in libvirt's in-process test driver (test:///default):
    nothing on the host is defined or changed."""
    os.makedirs(work_dir, mode=0o700, exist_ok=True)
    fd, probe = tempfile.mkstemp(prefix=".virsh-probe.", suffix=".xml", dir=work_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(VIRSH_PROBE_XML)
        rc, out = runner(["virsh", "-c", "test:///default", "define", probe])
    finally:
        os.unlink(probe)
    if rc:
        raise WorkerError(f"virsh cannot read files the build writes under {work_dir} ({out.strip()[-200:]}): run "
                          "build.py with sudo from a login shell, or systemd-run the python interpreter directly "
                          "(a shell script under systemd-run runs as initrc_t, and SELinux confines its virsh)")


def preflight(cfg: WorkerConfig, runner: Runner = _default_runner, mem: Callable[[], float] = mem_available_gib,
              wd: Callable[[], bool] = watchdog_inactive) -> None:
    if not wd():
        raise WorkerError("hardware watchdog is armed: refusing to start a VM")
    virsh_file_access(cfg.work_dir, runner)
    for dom in (cfg.target, cfg.base):
        st = domstate(dom, runner)
        if st and "shut off" not in st:
            raise WorkerError(f"{dom} is {st}: the worker never runs next to the target or base VM (cold-stop it first)")
    st = domstate(cfg.name, runner)
    if st and "shut off" not in st:
        raise WorkerError(f"{cfg.name} is already {st}")
    need = cfg.memory_gib + cfg.headroom_gib
    have = mem()
    if have < need:
        raise WorkerError(f"MemAvailable {have:.1f} GiB < worker {cfg.memory_gib} GiB + headroom {cfg.headroom_gib} GiB")


# ---------------------------------------------------------------------------
# exchange disk (host side; libguestfs, read-write only while the worker is off)
# ---------------------------------------------------------------------------

def _guestfs_env(cfg: WorkerConfig) -> Dict[str, str]:
    env = dict(os.environ)
    env["LIBGUESTFS_MEMSIZE"] = str(cfg.guestfs_mem_mib)
    env["LIBGUESTFS_BACKEND"] = "direct"
    return env


def prepare_exchange(cfg: WorkerConfig, job_dir: str, runner: Callable[..., Tuple[int, str]] = _default_runner) -> None:
    """Thin qcow2 -> GPT + one NTFS basic-data partition labelled MIOSXCHG -> copy the job tree in."""
    if not os.path.isdir(job_dir):
        raise WorkerError(f"job directory {job_dir} missing")
    os.makedirs(cfg.work_dir, mode=0o700, exist_ok=True)
    if os.path.exists(cfg.exchange):
        os.unlink(cfg.exchange)
    rc, out = runner(["qemu-img", "create", "-q", "-f", "qcow2", cfg.exchange, f"{cfg.exchange_gib}G"])
    if rc:
        raise WorkerError(f"qemu-img create exchange failed: {out[-300:]}")
    script = "\n".join([
        "run", "part-disk /dev/sda gpt", f"part-set-gpt-type /dev/sda 1 {GPT_BASIC_DATA}",
        f"mkfs ntfs /dev/sda1 label:{EXCHANGE_LABEL}", "mount /dev/sda1 /", f"mkdir /{EXCHANGE_ROOT}",
        f"copy-in {job_dir} /{EXCHANGE_ROOT}/", f"mkdir /{EXCHANGE_ROOT}/out", "umount-all", ""])
    rc, out = _guestfish(cfg, script, "--rw", runner)
    if rc:
        raise WorkerError(f"guestfish could not prepare the exchange disk: {out[-400:]}")


def _guestfish(cfg: WorkerConfig, script: str, mode: str, runner: Callable[..., Tuple[int, str]]) -> Tuple[int, str]:
    sp = os.path.join(cfg.work_dir, f".guestfish-{os.getpid()}.fish")
    with open(sp, "w", encoding="utf-8") as fh:
        fh.write(script)
    try:
        if runner is _default_runner:
            return _default_runner(["guestfish", mode, "-a", cfg.exchange, "-f", sp], env=_guestfs_env(cfg))
        return runner(["guestfish", mode, "-a", cfg.exchange, "-f", sp])
    finally:
        os.unlink(sp)


def drop_overlay(cfg: WorkerConfig, runner: Runner = _default_runner) -> int:
    """Remove the worker's throwaway overlay once the worker is shut off, BEFORE the copy-out: the copied-out ISO then
    never shares the build filesystem with the overlay (build-20261023T224037Z ran /var/lib/mios-build down to 5.6 GB
    before its copy-out). The copy-out reads only the exchange disk. Returns the bytes freed."""
    st = domstate(cfg.name, runner)
    if st and "shut off" not in st:
        raise WorkerError(f"{cfg.name} is {st}: its overlay is removed only after the worker stopped")
    try:
        size = os.stat(cfg.overlay).st_blocks * 512
        os.unlink(cfg.overlay)
    except FileNotFoundError:
        return 0
    except OSError as e:
        raise WorkerError(f"cannot remove the worker overlay: {e}") from None
    return size


def extract(cfg: WorkerConfig, dest: str, runner: Callable[..., Tuple[int, str]] = _default_runner) -> str:
    """Read-only copy-out of <exchange>/mios-build/out (worker shut off)."""
    st = domstate(cfg.name, runner)
    if st and "shut off" not in st:
        raise WorkerError(f"{cfg.name} is {st}: the exchange disk is read only after the worker stopped")
    os.makedirs(dest, mode=0o700, exist_ok=True)
    script = "\n".join(["run", "mount-ro /dev/sda1 /", f"copy-out /{EXCHANGE_ROOT}/out {dest}", "umount-all", ""])
    rc, out = _guestfish(cfg, script, "--ro", runner)
    if rc:
        raise WorkerError(f"guestfish copy-out failed: {out[-400:]}")
    return os.path.join(dest, "out")


# ---------------------------------------------------------------------------
# lifecycle
# ---------------------------------------------------------------------------

def _qemu_access(cfg: WorkerConfig) -> None:
    """Ensure the qemu libvirt runtime user (when running as root) can traverse to and write the worker images."""
    if os.geteuid() != 0:
        return
    try:
        import grp, pwd
        uid, gid = pwd.getpwnam("qemu").pw_uid, grp.getgrnam("qemu").gr_gid
    except KeyError:
        return
    d = os.path.abspath(cfg.work_dir)
    while d and d != "/" and d.startswith("/var/lib/mios-build"):
        st = os.stat(d)
        os.chown(d, st.st_uid, gid)
        os.chmod(d, (st.st_mode & 0o777) | 0o710)
        d = os.path.dirname(d)
    for f in (cfg.overlay, cfg.exchange, cfg.nvram):
        if os.path.exists(f):
            os.chown(f, uid, gid)
            os.chmod(f, 0o660)


def define(cfg: WorkerConfig, runner: Runner = _default_runner) -> str:
    rc, base_xml = runner(["virsh", "-c", CONNECT, "dumpxml", "--inactive", cfg.base])
    if rc:
        raise WorkerError(f"cannot read the base domain {cfg.base} (read-only dumpxml): {base_xml[-200:]}")
    src, _, fmt = base_disk(base_xml)
    if fmt != "qcow2":
        raise WorkerError(f"base disk format {fmt!r}: the overlay needs a qcow2 backing file")
    xml = generate_worker_xml(base_xml, cfg)
    os.makedirs(cfg.work_dir, mode=0o700, exist_ok=True)
    if os.path.exists(cfg.overlay):
        os.unlink(cfg.overlay)
    rc, out = runner(["qemu-img", "create", "-q", "-f", "qcow2", "-F", "qcow2", "-b", src, cfg.overlay])
    if rc:
        raise WorkerError(f"overlay creation failed: {out[-300:]}")
    root = ET.fromstring(base_xml)
    bnv = root.find("./os/nvram")
    tmpl = (bnv.get("template") if bnv is not None else None) or ""
    vars_src = (bnv.text or "").strip() if bnv is not None else ""
    src_vars = vars_src if vars_src and os.path.isfile(vars_src) else tmpl
    if not src_vars or not os.path.isfile(src_vars):
        raise WorkerError("no NVRAM source for the worker (base vars or template)")
    shutil.copyfile(src_vars, cfg.nvram)   # a copy: the base NVRAM is never written
    _qemu_access(cfg)
    with open(cfg.xml_path, "w", encoding="utf-8") as fh:
        fh.write(xml)
    rc, out = runner(["virsh", "-c", CONNECT, "define", cfg.xml_path])
    if rc:
        raise WorkerError(f"virsh define failed: {out[-300:]}")
    return xml


def start(cfg: WorkerConfig, runner: Runner = _default_runner, **guards: Any) -> None:
    preflight(cfg, runner, **guards)
    rc, out = runner(["virsh", "-c", CONNECT, "start", cfg.name])
    if rc:
        raise WorkerError(f"virsh start {cfg.name} failed: {out[-300:]}")


def stop(cfg: WorkerConfig, runner: Runner = _default_runner, sleep: Callable[[float], None] = time.sleep,
         grace_s: int = 120) -> None:
    """Graceful shutdown; destroy only if still running after grace_s."""
    st = domstate(cfg.name, runner)
    if not st or "shut off" in st:
        return
    runner(["virsh", "-c", CONNECT, "shutdown", cfg.name])
    waited = 0
    while waited < grace_s:
        sleep(5)
        waited += 5
        if "shut off" in (domstate(cfg.name, runner) or "shut off"):
            return
    runner(["virsh", "-c", CONNECT, "destroy", cfg.name])


def cleanup(cfg: WorkerConfig, runner: Runner = _default_runner, sleep: Callable[[float], None] = time.sleep) -> None:
    """Remove the worker domain, its overlay, NVRAM copy and the exchange disk (it carried the answer file)."""
    stop(cfg, runner, sleep)
    if domstate(cfg.name, runner) is not None:
        runner(["virsh", "-c", CONNECT, "undefine", cfg.name, "--keep-nvram"])
    for p in (cfg.overlay, cfg.exchange, cfg.nvram, cfg.xml_path):
        if os.path.exists(p):
            os.unlink(p)


def guest_ip(cfg: WorkerConfig, runner: Runner = _default_runner) -> Optional[str]:
    for src in ("lease", "agent", "arp"):
        rc, out = runner(["virsh", "-c", CONNECT, "domifaddr", cfg.name, "--source", src])
        m = re.search(r"ipv4\s+(\d+\.\d+\.\d+\.\d+)", out) if rc == 0 else None
        if m:
            return m.group(1)
    return None


def _session(cfg: WorkerConfig, ip: str) -> Any:
    try:
        import winrm  # type: ignore
    except ImportError:
        raise WorkerError("pywinrm missing: run build.py with windows.xbox.probe.python") from None
    user, pw = worker_credentials(cfg)
    try:
        return winrm.Session(f"http://{ip}:{cfg.winrm_port}/wsman", auth=(user, pw), transport=cfg.transport,
                             operation_timeout_sec=cfg.op_timeout_s, read_timeout_sec=cfg.read_timeout_s)
    finally:
        pw = ""


# WSMan faults Windows raises while it creates the shell for a request, before the command runs, so a retry is safe.
# 0x800703FA ERROR_KEY_DELETED ("Illegal operation attempted on a registry key that has been marked for deletion"):
# the User Profile Service unloaded the signing-in account's hive under WinRM between two shells (build
# 20261024T044408Z: submit_job's shell, right after quiesce). It clears once the profile is reloaded.
TRANSIENT_WSMAN_FAULTS = (0x800703FA,)
WINRM_RETRIES = 6
WINRM_RETRY_S = 10.0


def _winrm_failure(e: BaseException) -> bool:
    """pywinrm's WinRMError family (WSMan fault, transport, timeout) or the requests/socket OSError beneath it."""
    return isinstance(e, OSError) or any(c.__module__.split(".")[0] == "winrm" for c in type(e).__mro__)


def run_ps(sess: Any, script: str, sleep: Callable[[float], None] = time.sleep, retries: int = WINRM_RETRIES) -> str:
    """One PowerShell script over WinRM. A transient shell-creation fault is retried; any other WinRM failure is a
    WorkerError (the build stage fails named and its cleanup runs), never a raw pywinrm traceback."""
    for attempt in range(retries + 1):
        try:
            r = sess.run_ps(script)
            break
        except Exception as e:   # noqa: BLE001 -- narrowed by _winrm_failure; anything else propagates unchanged
            if not _winrm_failure(e):
                raise
            code = getattr(e, "wsman_fault_code", None)
            if code in TRANSIENT_WSMAN_FAULTS and attempt < retries:
                sleep(WINRM_RETRY_S)
                continue
            why = f"WSMan fault {code:#010x}" if isinstance(code, int) else type(e).__name__
            raise WorkerError(f"worker WinRM call failed ({why}, attempt {attempt + 1}/{retries + 1}): {str(e)[:300]}") from None
    if r.status_code:
        raise WorkerError(f"worker command failed rc={r.status_code}: {r.std_err.decode(errors='replace')[-600:]}")
    return r.std_out.decode("utf-8", "replace")


def wait_winrm(cfg: WorkerConfig, runner: Runner = _default_runner, sleep: Callable[[float], None] = time.sleep,
               session_factory: Optional[Callable[[WorkerConfig, str], Any]] = None) -> Any:
    deadline = time.monotonic() + cfg.boot_timeout_s
    last = "no address"
    while time.monotonic() < deadline:
        ip = guest_ip(cfg, runner)
        if ip:
            sess = (session_factory or _session)(cfg, ip)
            try:
                with socket.create_connection((ip, cfg.winrm_port), timeout=5):
                    pass
                run_ps(sess, "$PSVersionTable.PSVersion.Major", sleep)
                return sess
            except (OSError, WorkerError) as e:
                last = f"{ip}: {type(e).__name__}"
        sleep(cfg.poll_s)
    raise WorkerError(f"worker WinRM not reachable within {cfg.boot_timeout_s}s ({last})")


_LOCATE = f"$v = Get-Volume -FileSystemLabel '{EXCHANGE_LABEL}' -ErrorAction SilentlyContinue | Select-Object -First 1; " \
          f"if (-not $v) {{ Get-Disk | Where-Object IsOffline | Set-Disk -IsOffline $false; Start-Sleep 5; " \
          f"$v = Get-Volume -FileSystemLabel '{EXCHANGE_LABEL}' | Select-Object -First 1 }}; " \
          f"if ($v -and -not $v.DriveLetter) {{ $v | Get-Partition | Add-PartitionAccessPath -AssignDriveLetter -ErrorAction SilentlyContinue; " \
          f"Start-Sleep 2; $v = Get-Volume -FileSystemLabel '{EXCHANGE_LABEL}' | Select-Object -First 1 }}; " \
          f"if (-not $v -or -not $v.DriveLetter) {{ throw 'exchange volume {EXCHANGE_LABEL} not mounted' }}; " \
          f"$dl = ([string]$v.DriveLetter).Trim([char]0).Trim(); " \
          f"if (-not $dl) {{ throw 'exchange volume {EXCHANGE_LABEL} has an empty DriveLetter' }}; " \
          f"$root = ('{{0}}:\\{EXCHANGE_ROOT}' -f $dl)"


QUIESCE_SERVICES = ("wuauserv", "UsoSvc", "WaaSMedicSvc", "DoSvc")


def quiesce(sess: Any, sleep: Callable[[float], None] = time.sleep) -> str:
    """Make the throwaway worker overlay safe for a long unattended job: no Windows Update (no reboot in the
    middle of a conversion), no sleep/hibernate, and Defender skips the exchange volume (UUP conversion +
    DISM on it). Protected services that refuse to stop are reported, not fatal; only the overlay changes."""
    svcs = ",".join(f"'{s}'" for s in QUIESCE_SERVICES)
    return run_ps(sess, _LOCATE + "; $ErrorActionPreference = 'Continue'; $r = @(); "
                  f"foreach ($s in @({svcs})) {{ Stop-Service -Name $s -Force -ErrorAction SilentlyContinue; "
                  "Set-Service -Name $s -StartupType Disabled -ErrorAction SilentlyContinue; "
                  "$st = (Get-Service -Name $s -ErrorAction SilentlyContinue).Status; $r += ('{0}={1}' -f $s, $st) }; "
                  "$au = 'HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows\\WindowsUpdate\\AU'; New-Item -Path $au -Force | Out-Null; "
                  "Set-ItemProperty -Path $au -Name NoAutoUpdate -Value 1 -Type DWord; "
                  "Set-ItemProperty -Path $au -Name NoAutoRebootWithLoggedOnUsers -Value 1 -Type DWord; "
                  "foreach ($a in @('standby-timeout-ac', 'hibernate-timeout-ac', 'monitor-timeout-ac')) { & powercfg.exe /change $a 0 | Out-Null }; "
                  "& powercfg.exe /hibernate off | Out-Null; "
                  "Add-MpPreference -ExclusionPath ('{0}:\\' -f $v.DriveLetter) -ErrorAction SilentlyContinue; "
                  "'quiesced ' + ($r -join ' ')", sleep).strip()


def run_exchange_script(sess: Any, script: str, args: str = "") -> str:
    """Run <exchange>\\mios-build\\job\\<script> in the WinRM session (process-scope execution policy only)."""
    if not re.fullmatch(r"[\w.\-]+\.ps1", script):
        raise WorkerError(f"refusing script name {script!r}")
    return run_ps(sess, _LOCATE + "; Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force; "
                  f"& (Join-Path $root 'job\\{script}') -Root $root {args}")


def boot_id(sess: Any) -> str:
    return run_ps(sess, BOOT_ID_PS).strip()


def reboot_and_wait(cfg: WorkerConfig, sess: Any, runner: Runner = _default_runner,
                    sleep: Callable[[float], None] = time.sleep,
                    session_factory: Optional[Callable[[WorkerConfig, str], Any]] = None) -> Any:
    """Restart the worker guest and return a session from the NEW boot (the boot time must change)."""
    before = boot_id(sess)
    run_ps(sess, "& shutdown.exe /r /t 5 /f /d p:4:1 | Out-Null; 'rebooting'")
    deadline = time.monotonic() + cfg.boot_timeout_s
    last = "not back"
    sleep(30)
    while time.monotonic() < deadline:
        try:
            new = wait_winrm(cfg, runner, sleep=sleep, session_factory=session_factory)
            bid = boot_id(new)
            if bid and bid != before:
                return new
            last = "same boot"
        except WorkerError as e:
            last = str(e)
        sleep(cfg.poll_s)
    raise WorkerError(f"worker did not come back from the reboot within {cfg.boot_timeout_s}s ({last})")


def submit_job(sess: Any, sleep: Callable[[float], None] = time.sleep) -> None:
    """Run the job as SYSTEM in a scheduled task (survives WinRM timeouts; DISM needs full rights)."""
    run_ps(sess, _LOCATE + "; "
           "$a = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ('-NoProfile -ExecutionPolicy Bypass -File \"{0}\\job\\MiOS-Xbox-Worker.ps1\" -Root \"{0}\"' -f $root); "
           "$p = New-ScheduledTaskPrincipal -UserId 'S-1-5-18' -LogonType ServiceAccount -RunLevel Highest; "
           "$s = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 12) -AllowStartIfOnBatteries; "
           "Register-ScheduledTask -TaskPath '\\MiOS\\' -TaskName 'MiOS-Xbox-Build' -Action $a -Principal $p -Settings $s -Force | Out-Null; "
           "Start-ScheduledTask -TaskPath '\\MiOS\\' -TaskName 'MiOS-Xbox-Build'", sleep)


def poll_job(cfg: WorkerConfig, sess: Any, sleep: Callable[[float], None] = time.sleep,
             log: Callable[[str], None] = lambda m: None) -> Dict[str, Any]:
    deadline = time.monotonic() + cfg.build_timeout_s
    last = ""
    while time.monotonic() < deadline:
        txt = run_ps(sess, _LOCATE + "; $f = Join-Path $root 'out\\status.json'; if (Test-Path $f) { Get-Content -Raw $f } else { '{}' }", sleep)
        try:
            st = json.loads(txt.strip() or "{}")
        except ValueError:
            st = {}
        stage = f"{st.get('stage', '')}:{st.get('status', '')}"
        if stage != last:
            log(f"worker {stage}")
            last = stage
        if st.get("status") in ("done", "failed"):
            return st
        sleep(cfg.poll_s)
    raise WorkerError(f"worker job did not finish within {cfg.build_timeout_s}s (last {last})")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="worker.py", description="MiOS-Xbox build worker manager")
    ap.add_argument("--spec", required=True, help="rendered xbox-spec.json (render_spec.py)")
    ap.add_argument("--work-dir", help="default: <windows.xbox.build.work_root>/worker")
    ap.add_argument("action", choices=["xml", "preflight", "stop", "cleanup", "assert-shutoff"])
    a = ap.parse_args(argv)
    try:
        with open(a.spec, encoding="utf-8") as fh:
            doc = json.load(fh)
        cfg = WorkerConfig.from_spec(doc, a.work_dir or os.path.join(doc["spec"]["build"]["work_root"], "worker"))
        if a.action == "xml":
            rc, base_xml = _default_runner(["virsh", "-c", CONNECT, "dumpxml", "--inactive", cfg.base])
            if rc:
                raise WorkerError(f"cannot read the base domain {cfg.base}")
            print(generate_worker_xml(base_xml, cfg))
        elif a.action == "preflight":
            preflight(cfg)
            print("worker preflight ok")
        elif a.action == "stop":
            stop(cfg)
        elif a.action == "cleanup":
            cleanup(cfg)
        else:
            st = domstate(cfg.name)
            if st and "shut off" not in st:
                raise WorkerError(f"{cfg.name} is {st}")
            print(f"{cfg.name} is shut off")
    except (WorkerError, OSError, KeyError, ValueError) as e:
        print(f"worker: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
