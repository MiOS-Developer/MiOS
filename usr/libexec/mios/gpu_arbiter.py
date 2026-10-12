#!/usr/bin/env python3
# AI-hint: Dynamic GPU Arbiter state machine (R2 Part B). Coordinates vLLM sleep mode, container pause fallback, sysfs driver binding, and QMP device hotplug for the physical RTX dGPU between AI reasoning workloads and Windows gaming guest (mios-xbox) without host reboots.
# AI-doc: usr/share/doc/mios/manual/gpu-arbiter.md
# AI-related: usr/libexec/mios/mios-policy-arbiter, usr/share/containers/systemd/mios-llm-heavy.container, usr/lib/mios/agent-pipe/mios_pipe/routing/lanes_resolver.py
"""gpu_arbiter.py -- Dynamic GPU Arbiter State Machine."""

from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("mios-gpu-arbiter")

STATE_AI_EXCLUSIVE = "AI_EXCLUSIVE"
STATE_DRAINING_SLEEP = "DRAINING_SLEEP"
STATE_VM_ATTACHED = "VM_ATTACHED"
STATE_RESTORING_AI = "RESTORING_AI"

VALID_STATES = frozenset((
    STATE_AI_EXCLUSIVE,
    STATE_DRAINING_SLEEP,
    STATE_VM_ATTACHED,
    STATE_RESTORING_AI,
))

DEFAULT_PCI_DEVICES = ["0000:01:00.0", "0000:01:00.1"]
DEFAULT_XBOX_DOMAIN = "mios-xbox"
DEFAULT_QMP_SOCKET = "/var/run/libvirt/qemu/mios-xbox.qmp"
DEFAULT_VLLM_PORT = 8520
STATE_FILE_PRIMARY = "/run/mios/gpu-state"
STATE_FILE_SECONDARY = "/run/mios-gpu-state"


class InvalidStateTransitionError(ValueError):
    """Raised when an illegal GPU state machine transition is attempted."""
    pass


class QMPClient:
    """Lightweight QMP client for device_add and device_del hotplug operations."""

    def __init__(self, qmp_socket: Optional[str] = None, dry_run: bool = False):
        self.qmp_socket = qmp_socket or os.environ.get("MIOS_QMP_SOCKET", DEFAULT_QMP_SOCKET)
        self.dry_run = dry_run or (os.environ.get("MIOS_QMP_MOCK", "0") in ("1", "true", "yes"))

    def send_command(self, payload: Dict[str, Any]) -> Tuple[bool, Optional[Dict[str, Any]]]:
        if self.dry_run:
            logger.info("[MOCK-QMP] Executing command: %s", json.dumps(payload))
            return True, {"return": {}}

        if not self.qmp_socket or not os.path.exists(self.qmp_socket):
            logger.debug("QMP socket does not exist at %s (simulating mock)", self.qmp_socket)
            return True, {"return": {}}

        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(2.0)
            sock.connect(self.qmp_socket)

            # Read greeting banner
            initial = sock.recv(2048)
            logger.debug("QMP greeting: %s", initial)

            # Negotiate capabilities
            sock.sendall(b'{"execute": "qmp_capabilities"}\n')
            cap_resp = sock.recv(2048)
            logger.debug("QMP capabilities response: %s", cap_resp)

            # Send actual command
            cmd_bytes = (json.dumps(payload) + "\n").encode("utf-8")
            sock.sendall(cmd_bytes)
            resp = sock.recv(4096)
            sock.close()

            resp_obj = json.loads(resp.decode("utf-8")) if resp else {}
            if "error" in resp_obj:
                logger.warning("QMP error response: %s", resp_obj)
                return False, resp_obj
            return True, resp_obj
        except (socket.error, OSError, ValueError) as e:
            logger.warning("Failed executing QMP command on %s: %s", self.qmp_socket, e)
            return False, None

    def hotplug_gpu(self, pci_devices: List[str], bus: str = "pcie.1") -> bool:
        """Attach GPU functions (VGA and Audio) to guest domain via QMP device_add."""
        success = True
        vga_addr = pci_devices[0] if len(pci_devices) > 0 else "0000:01:00.0"
        audio_addr = pci_devices[1] if len(pci_devices) > 1 else "0000:01:00.1"

        # Host format can be either 01:00.0 or 0000:01:00.0
        vga_short = vga_addr.removeprefix("0000:")
        audio_short = audio_addr.removeprefix("0000:")

        # Add VGA device
        cmd_vga = {
            "execute": "device_add",
            "arguments": {
                "driver": "vfio-pci",
                "host": vga_short,
                "id": "gpu_vga",
                "bus": bus,
            }
        }
        ok1, _ = self.send_command(cmd_vga)
        if not ok1:
            success = False

        # Add Audio device
        cmd_audio = {
            "execute": "device_add",
            "arguments": {
                "driver": "vfio-pci",
                "host": audio_short,
                "id": "gpu_audio",
                "bus": bus,
            }
        }
        ok2, _ = self.send_command(cmd_audio)
        if not ok2:
            success = False

        return success

    def unplug_gpu(self) -> bool:
        """Detach GPU functions from guest domain via QMP device_del."""
        success = True
        cmd_vga = {"execute": "device_del", "arguments": {"id": "gpu_vga"}}
        ok1, _ = self.send_command(cmd_vga)
        if not ok1:
            success = False

        cmd_audio = {"execute": "device_del", "arguments": {"id": "gpu_audio"}}
        ok2, _ = self.send_command(cmd_audio)
        if not ok2:
            success = False

        return success


class VLLMSleepClient:
    """Coordinates vLLM sleep/wake_up calls and container pause fallback."""

    def __init__(self, port: Optional[int] = None, container_name: str = "mios-llm-heavy"):
        self.port = port or int(os.environ.get("MIOS_PORTS_VLLM", DEFAULT_VLLM_PORT))
        self.container_name = container_name

    def sleep(self, timeout: float = 2.0) -> bool:
        """Call POST /sleep on vLLM to release GPU VRAM allocations."""
        url = f"http://127.0.0.1:{self.port}/sleep"
        req = urllib.request.Request(url, data=b"{}", headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    logger.info("vLLM sleep succeeded on port %d", self.port)
                    return True
        except (urllib.error.URLError, socket.error, TimeoutError) as e:
            logger.debug("vLLM /sleep endpoint unavailable or failed (%s); will use container pause fallback", e)
        return False

    def wake_up(self, timeout: float = 5.0) -> bool:
        """Call POST /wake_up on vLLM to reload weights and reactivate model."""
        url = f"http://127.0.0.1:{self.port}/wake_up"
        req = urllib.request.Request(url, data=b"{}", headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    logger.info("vLLM wake_up succeeded on port %d", self.port)
                    return True
        except (urllib.error.URLError, socket.error, TimeoutError) as e:
            logger.debug("vLLM /wake_up endpoint unavailable or failed (%s)", e)
        return False

    def pause_container(self) -> bool:
        """Fallback: pause container via podman pause mios-llm-heavy."""
        try:
            res = subprocess.run(["podman", "pause", self.container_name],
                                 capture_output=True, text=True, check=False)
            if res.returncode == 0:
                logger.info("Successfully paused container %s", self.container_name)
                return True
            logger.debug("podman pause returned %d: %s", res.returncode, res.stderr)
        except (FileNotFoundError, OSError) as e:
            logger.debug("podman not found or failed: %s", e)
        return True  # degrade gracefully in environments without podman daemon

    def unpause_container(self) -> bool:
        """Unpause container via podman unpause mios-llm-heavy."""
        try:
            res = subprocess.run(["podman", "unpause", self.container_name],
                                 capture_output=True, text=True, check=False)
            if res.returncode == 0:
                logger.info("Successfully unpaused container %s", self.container_name)
                return True
            logger.debug("podman unpause returned %d: %s", res.returncode, res.stderr)
        except (FileNotFoundError, OSError) as e:
            logger.debug("podman not found or failed: %s", e)
        return True


def bind_pci_driver(pci_address: str, driver_name: str) -> bool:
    """Linux sysfs helper: unbind device from current driver and bind to driver_name."""
    sysfs_pci = f"/sys/bus/pci/devices/{pci_address}"
    if not os.path.exists(sysfs_pci):
        logger.debug("Sysfs PCI device %s not present (simulating)", pci_address)
        return True

    try:
        # 1. Unbind from existing driver if bound
        current_driver = os.path.join(sysfs_pci, "driver")
        if os.path.exists(current_driver):
            unbind_path = os.path.join(current_driver, "unbind")
            with open(unbind_path, "w", encoding="utf-8") as f:
                f.write(pci_address)

        # 2. Bind to target driver
        bind_path = f"/sys/bus/pci/drivers/{driver_name}/bind"
        if os.path.exists(bind_path):
            with open(bind_path, "w", encoding="utf-8") as f:
                f.write(pci_address)
        return True
    except (OSError, IOError) as e:
        logger.warning("Sysfs PCI driver binding failed for %s -> %s: %s", pci_address, driver_name, e)
        return False


class GPUArbiter:
    """Dynamic GPU Arbiter managing physical RTX dGPU lifecycle across AI and Gaming."""

    def __init__(
        self,
        domain: str = DEFAULT_XBOX_DOMAIN,
        pci_devices: Optional[List[str]] = None,
        qmp_socket: Optional[str] = None,
        vllm_port: Optional[int] = None,
        state_file: Optional[str] = None,
    ):
        self.domain = domain
        self.pci_devices = pci_devices or list(DEFAULT_PCI_DEVICES)
        self.state_file = state_file or os.environ.get("MIOS_GPU_STATE_FILE", STATE_FILE_PRIMARY)
        self.state = STATE_AI_EXCLUSIVE
        self.held_by = "ai"
        self.vllm_sleeping = False
        self.container_paused = False

        self.qmp = QMPClient(qmp_socket)
        self.vllm = VLLMSleepClient(vllm_port)
        self.publish_state()

    def get_state(self) -> Dict[str, Any]:
        """Return current arbiter status dictionary."""
        return {
            "state": self.state,
            "held_by": self.held_by,
            "vllm_sleeping": self.vllm_sleeping,
            "container_paused": self.container_paused,
            "pci_devices": self.pci_devices,
            "domain": self.domain,
        }

    def publish_state(self) -> None:
        """Write current state string to /run/mios/gpu-state for zero-latency local checks."""
        for path in (self.state_file, STATE_FILE_SECONDARY):
            try:
                parent = os.path.dirname(path)
                if parent and not os.path.exists(parent):
                    os.makedirs(parent, exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(self.state + "\n")
            except Exception as e:
                logger.debug("Could not write GPU state to %s: %s", path, e)

    def claim_gaming(self, timeout: float = 5.0) -> Dict[str, Any]:
        """Coordinate transition AI_EXCLUSIVE -> DRAINING_SLEEP -> VM_ATTACHED."""
        if self.state == STATE_VM_ATTACHED:
            return {"ok": True, "state": self.state, "message": "Already claimed for gaming", "held_by": "gaming"}

        if self.state != STATE_AI_EXCLUSIVE:
            raise InvalidStateTransitionError(
                f"Cannot claim gaming from state '{self.state}'. Must be in '{STATE_AI_EXCLUSIVE}'."
            )

        previous_state = self.state

        # 1. State: DRAINING_SLEEP
        self.state = STATE_DRAINING_SLEEP
        self.publish_state()
        logger.info("Transitioned to %s: releasing VRAM buffers", self.state)

        # 2. Invoke vLLM sleep mode; if unresponsive, fallback to container pause
        sleep_ok = self.vllm.sleep(timeout=2.0)
        if sleep_ok:
            self.vllm_sleeping = True
            self.container_paused = False
        else:
            logger.info("vLLM sleep unavailable/timed out, invoking container pause fallback")
            self.vllm.pause_container()
            self.container_paused = True
            self.vllm_sleeping = False

        # 3. Unbind PCI device from nvidia and bind to vfio-pci
        for pci_dev in self.pci_devices:
            bind_pci_driver(pci_dev, "vfio-pci")

        # 4. QMP device_add hotplug to mios-xbox
        qmp_ok = self.qmp.hotplug_gpu(self.pci_devices)
        logger.info("QMP device_add hotplug result: %s", qmp_ok)

        # 5. State: VM_ATTACHED
        self.state = STATE_VM_ATTACHED
        self.held_by = "gaming"
        self.publish_state()
        logger.info("Transitioned to %s: GPU attached to gaming guest", self.state)

        return {
            "ok": True,
            "state": self.state,
            "previous_state": previous_state,
            "held_by": self.held_by,
            "vllm_sleeping": self.vllm_sleeping,
            "container_paused": self.container_paused,
        }

    def release_gaming(self, timeout: float = 5.0) -> Dict[str, Any]:
        """Coordinate transition VM_ATTACHED -> RESTORING_AI -> AI_EXCLUSIVE."""
        if self.state == STATE_AI_EXCLUSIVE:
            return {"ok": True, "state": self.state, "message": "Already released to AI", "held_by": "ai"}

        if self.state != STATE_VM_ATTACHED:
            raise InvalidStateTransitionError(
                f"Cannot release gaming from state '{self.state}'. Must be in '{STATE_VM_ATTACHED}'."
            )

        previous_state = self.state

        # 1. State: RESTORING_AI
        self.state = STATE_RESTORING_AI
        self.publish_state()
        logger.info("Transitioned to %s: detaching GPU from gaming guest", self.state)

        # 2. QMP device_del unplug from mios-xbox
        qmp_ok = self.qmp.unplug_gpu()
        logger.info("QMP device_del unplug result: %s", qmp_ok)

        # 3. Rebind PCI device from vfio-pci back to nvidia
        for pci_dev in self.pci_devices:
            driver_target = "snd_hda_intel" if pci_dev.endswith(".1") else "nvidia"
            bind_pci_driver(pci_dev, driver_target)

        # 4. Restore vLLM: unpause container if paused, then wake_up
        if self.container_paused:
            self.vllm.unpause_container()
            self.container_paused = False

        if self.vllm_sleeping:
            self.vllm.wake_up(timeout=timeout)
            self.vllm_sleeping = False

        # 5. State: AI_EXCLUSIVE
        self.state = STATE_AI_EXCLUSIVE
        self.held_by = "ai"
        self.publish_state()
        logger.info("Transitioned to %s: GPU restored to AI cognitive plane", self.state)

        return {
            "ok": True,
            "state": self.state,
            "previous_state": previous_state,
            "held_by": self.held_by,
            "vllm_sleeping": self.vllm_sleeping,
            "container_paused": self.container_paused,
        }

    def force_state(self, new_state: str) -> None:
        """Manual or test override of GPU arbiter state."""
        if not isinstance(new_state, str) or new_state not in VALID_STATES:
            raise InvalidStateTransitionError(f"Invalid state '{new_state}'. Allowed: {sorted(VALID_STATES)}")
        self.state = new_state
        self.held_by = "ai" if new_state == STATE_AI_EXCLUSIVE else "gaming"
        self.publish_state()


# Global arbiter singleton
_GLOBAL_ARBITER: Optional[GPUArbiter] = None


def get_gpu_arbiter() -> GPUArbiter:
    """Return or initialize global GPUArbiter instance."""
    global _GLOBAL_ARBITER
    if _GLOBAL_ARBITER is None:
        _GLOBAL_ARBITER = GPUArbiter()
    return _GLOBAL_ARBITER
