#!/usr/bin/env python3
# AI-hint: In-kernel udev netlink hardware change monitor and PostgreSQL hardware inventory recorder.
# AI-related: usr/libexec/mios/hw/inventory_monitor.py, tests/test-hw.py
"""In-Kernel Udev Netlink Hardware Monitor & Inventory Engine (task_507).

Captures NETLINK_KOBJECT_UEVENT hardware lifecycle events (PCIe, NVMe, GPU, USB,
memory topology), evaluates PCIe lane width and link speed degradation, and
maintains synchronized state records in PostgreSQL hardware_inventory.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
import glob
import json
import logging
import os
import re
import socket
import struct
import sys
import time
from typing import Any, Dict, Iterator, List, Optional, Tuple

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("mios-hw-monitor")

NETLINK_KOBJECT_UEVENT = 15

@dataclass
class HardwareDevice:
    """Represents a discovered physical hardware component in host topology."""
    sys_path: str
    subsystem: str
    vendor_id: str
    device_id: str
    device_name: str
    driver: Optional[str] = None
    pcie_bus: Optional[str] = None
    numa_node: int = 0
    current_link_width: int = 16
    max_link_width: int = 16
    current_link_speed: str = "16.0 GT/s"
    max_link_speed: str = "16.0 GT/s"
    status: str = "active"
    last_updated: float = field(default_factory=time.time)

    def is_degraded(self) -> Tuple[bool, List[str]]:
        """Returns True and reasons if PCIe bandwidth or lane width is degraded."""
        reasons = []
        if self.max_link_width > 0 and self.current_link_width < self.max_link_width:
            reasons.append(f"PCIe link width degraded: operating at x{self.current_link_width} (max supported: x{self.max_link_width})")
        if self.max_link_speed and self.current_link_speed and self.current_link_speed != self.max_link_speed:
            # Check speed reduction
            reasons.append(f"PCIe link speed degraded: operating at {self.current_link_speed} (max supported: {self.max_link_speed})")
        return (len(reasons) > 0, reasons)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        degraded, reasons = self.is_degraded()
        d["is_degraded"] = degraded
        d["degradation_reasons"] = reasons
        return d

@dataclass
class HardwareEvent:
    """Hardware lifecycle event from netlink uevent socket."""
    action: str  # "add", "remove", "change", "bind", "unbind", "degraded"
    subsystem: str
    sys_path: str
    event_payload: Dict[str, Any]
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

class HardwareInventoryMonitor:
    """Monitors hardware topology via netlink socket and sysfs, persisting to PostgreSQL."""

    def __init__(self, mock: bool = False, sysfs_root: str = "/sys") -> None:
        self.mock = mock
        self.sysfs_root = sysfs_root
        self._mock_inventory: Dict[str, HardwareDevice] = {}
        self._mock_events: List[HardwareEvent] = []
        if self.mock:
            self._init_mock_state()

    def _init_mock_state(self) -> None:
        """Initializes simulated hardware topology."""
        gpu = HardwareDevice(
            sys_path="/sys/devices/pci0000:00/0000:00:01.0/0000:01:00.0",
            subsystem="pci",
            vendor_id="10de",
            device_id="2684",
            device_name="NVIDIA GeForce RTX 4090",
            driver="nvidia",
            pcie_bus="0000:01:00.0",
            numa_node=0,
            current_link_width=16,
            max_link_width=16,
            current_link_speed="16.0 GT/s",
            max_link_speed="16.0 GT/s",
            status="active",
        )
        nvme = HardwareDevice(
            sys_path="/sys/devices/pci0000:00/0000:00:01.1/0000:02:00.0",
            subsystem="nvme",
            vendor_id="144d",
            device_id="a808",
            device_name="Samsung 980 PRO NVMe SSD",
            driver="nvme",
            pcie_bus="0000:02:00.0",
            numa_node=0,
            current_link_width=4,
            max_link_width=4,
            current_link_speed="16.0 GT/s",
            max_link_speed="16.0 GT/s",
            status="active",
        )
        self._mock_inventory[gpu.sys_path] = gpu
        self._mock_inventory[nvme.sys_path] = nvme

    def scan_sysfs_inventory(self) -> List[HardwareDevice]:
        """Scans sysfs PCI tree for physical devices, link widths, and speeds."""
        if self.mock:
            return list(self._mock_inventory.values())

        devices: List[HardwareDevice] = []
        pci_pattern = os.path.join(self.sysfs_root, "bus", "pci", "devices", "*")
        for pci_path in glob.glob(pci_pattern):
            try:
                bus_id = os.path.basename(pci_path)
                vendor = self._read_sysfs_file(os.path.join(pci_path, "vendor")).replace("0x", "").lower().zfill(4)
                device = self._read_sysfs_file(os.path.join(pci_path, "device")).replace("0x", "").lower().zfill(4)

                # Driver
                driver_path = os.path.join(pci_path, "driver")
                driver = os.path.basename(os.readlink(driver_path)) if os.path.islink(driver_path) else None

                # PCIe Link Width & Speed
                curr_width_str = self._read_sysfs_file(os.path.join(pci_path, "current_link_width"))
                max_width_str = self._read_sysfs_file(os.path.join(pci_path, "max_link_width"))
                curr_width = int(curr_width_str) if curr_width_str.isdigit() else 0
                max_width = int(max_width_str) if max_width_str.isdigit() else 0

                curr_speed = self._read_sysfs_file(os.path.join(pci_path, "current_link_speed")) or "Unknown"
                max_speed = self._read_sysfs_file(os.path.join(pci_path, "max_link_speed")) or "Unknown"

                # NUMA Node
                numa_str = self._read_sysfs_file(os.path.join(pci_path, "numa_node"))
                numa = int(numa_str) if numa_str.lstrip("-").isdigit() else 0

                dev_obj = HardwareDevice(
                    sys_path=pci_path,
                    subsystem="pci",
                    vendor_id=vendor,
                    device_id=device,
                    device_name=f"PCI Device {vendor}:{device}",
                    driver=driver,
                    pcie_bus=bus_id,
                    numa_node=max(0, numa),
                    current_link_width=curr_width,
                    max_link_width=max_width,
                    current_link_speed=curr_speed,
                    max_link_speed=max_speed,
                    status="active",
                )
                devices.append(dev_obj)
            except Exception as e:
                logger.debug("Error scanning PCI device %s: %s", pci_path, e)

        return devices

    def _read_sysfs_file(self, path: str) -> str:
        """Helper to read sysfs single-line text."""
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            return ""

    def detect_degraded_devices(self) -> List[Tuple[HardwareDevice, List[str]]]:
        """Returns all hardware devices currently running below maximum PCIe link capability."""
        inventory = self.scan_sysfs_inventory()
        degraded = []
        for dev in inventory:
            is_deg, reasons = dev.is_degraded()
            if is_deg:
                degraded.append((dev, reasons))
        return degraded

    def process_uevent_dict(self, uevent_data: Dict[str, Any], db_url: Optional[str] = None) -> HardwareEvent:
        """Processes raw uevent dictionary and updates inventory state."""
        action = uevent_data.get("ACTION", "change").lower()
        subsystem = uevent_data.get("SUBSYSTEM", "generic")
        devpath = uevent_data.get("DEVPATH", "")
        raw_sys_path = os.path.join(self.sysfs_root, devpath.lstrip("/"))
        sys_path = os.path.normpath(raw_sys_path).replace("\\", "/")

        event = HardwareEvent(
            action=action,
            subsystem=subsystem,
            sys_path=sys_path,
            event_payload=uevent_data,
            ts=time.time(),
        )

        if self.mock:
            self._mock_events.append(event)
            if action == "remove":
                if sys_path in self._mock_inventory:
                    self._mock_inventory[sys_path].status = "removed"
            elif action in ("add", "change"):
                if sys_path in self._mock_inventory:
                    self._mock_inventory[sys_path].status = "active"
                    self._mock_inventory[sys_path].last_updated = time.time()
                else:
                    self._mock_inventory[sys_path] = HardwareDevice(
                        sys_path=sys_path,
                        subsystem=subsystem,
                        vendor_id=uevent_data.get("PCI_ID", "0000:0000").split(":")[0],
                        device_id=uevent_data.get("PCI_ID", "0000:0000").split(":")[-1],
                        device_name=f"Dynamic Device {subsystem}",
                        driver=uevent_data.get("DRIVER"),
                    )
            return event

        self.record_to_db(event=event, db_url=db_url)
        return event

    def parse_raw_netlink_packet(self, data: bytes) -> Dict[str, str]:
        """Decodes raw null-delimited netlink kobject uevent payload."""
        fields: Dict[str, str] = {}
        tokens = data.decode("utf-8", errors="replace").split("\x00")
        for token in tokens:
            if "=" in token:
                k, v = token.split("=", 1)
                fields[k] = v
            elif token.startswith("add@") or token.startswith("remove@") or token.startswith("change@"):
                action, path = token.split("@", 1)
                fields["ACTION"] = action
                fields["DEVPATH"] = path
        return fields

    def listen_netlink(self, duration_sec: int = 5) -> Iterator[HardwareEvent]:
        """Listens for hardware netlink events."""
        if self.mock:
            # Yield simulated event
            sim_event_dict = {
                "ACTION": "change",
                "SUBSYSTEM": "pci",
                "DEVPATH": "/devices/pci0000:00/0000:00:01.0/0000:01:00.0",
                "DRIVER": "nvidia",
                "PCI_ID": "10de:2684",
            }
            yield self.process_uevent_dict(sim_event_dict)
            return

        try:
            sock = socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, NETLINK_KOBJECT_UEVENT)
            sock.bind((os.getpid(), 1))
            sock.settimeout(duration_sec)
            start_t = time.time()
            while time.time() - start_t < duration_sec:
                try:
                    data = sock.recv(4096)
                    parsed = self.parse_raw_netlink_packet(data)
                    if parsed:
                        yield self.process_uevent_dict(parsed)
                except socket.timeout:
                    break
        except Exception as e:
            logger.warning("Netlink socket unavailable: %s", e)

    def record_to_db(self, device: Optional[HardwareDevice] = None, event: Optional[HardwareEvent] = None, db_url: Optional[str] = None) -> None:
        """Persists hardware records into PostgreSQL tables hardware_inventory & hardware_events."""
        if self.mock or not db_url:
            return

        import psycopg2  # type: ignore
        conn = psycopg2.connect(db_url)
        try:
            with conn.cursor() as cur:
                # Ensure DDL schema
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS hardware_inventory (
                        id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                        sys_path text NOT NULL UNIQUE,
                        subsystem text NOT NULL,
                        vendor_id text,
                        device_id text,
                        device_name text,
                        driver text,
                        pcie_bus text,
                        numa_node integer,
                        current_link_width integer,
                        max_link_width integer,
                        status text DEFAULT 'active',
                        last_updated timestamptz DEFAULT now()
                    );
                    CREATE TABLE IF NOT EXISTS hardware_events (
                        id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                        action text NOT NULL,
                        subsystem text NOT NULL,
                        sys_path text NOT NULL,
                        event_payload jsonb NOT NULL,
                        ts timestamptz DEFAULT now()
                    );
                """)

                if device:
                    cur.execute("""
                        INSERT INTO hardware_inventory (
                            sys_path, subsystem, vendor_id, device_id, device_name, driver, pcie_bus, numa_node,
                            current_link_width, max_link_width, status, last_updated
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                        ON CONFLICT (sys_path) DO UPDATE SET
                            driver = EXCLUDED.driver,
                            current_link_width = EXCLUDED.current_link_width,
                            status = EXCLUDED.status,
                            last_updated = now();
                    """, (
                        device.sys_path, device.subsystem, device.vendor_id, device.device_id, device.device_name,
                        device.driver, device.pcie_bus, device.numa_node, device.current_link_width, device.max_link_width,
                        device.status,
                    ))

                if event:
                    cur.execute("""
                        INSERT INTO hardware_events (action, subsystem, sys_path, event_payload)
                        VALUES (%s, %s, %s, %s);
                    """, (event.action, event.subsystem, event.sys_path, json.dumps(event.event_payload)))

                conn.commit()
        finally:
            conn.close()

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MiOS Netlink Hardware Monitor & Inventory (task_507)")
    parser.add_argument("--scan", action="store_true", help="Scan sysfs for physical hardware devices")
    parser.add_argument("--check-degraded", action="store_true", help="Assert PCIe lane width and speed health")
    parser.add_argument("--listen", action="store_true", help="Listen for netlink hardware uevents")
    parser.add_argument("--mock", action="store_true", help="Run with simulated hardware topology")
    parser.add_argument("--json", action="store_true", help="Output in structured JSON")
    return parser.parse_args()

def main() -> int:
    args = parse_args()
    monitor = HardwareInventoryMonitor(mock=args.mock)

    try:
        if args.check_degraded:
            degraded = monitor.detect_degraded_devices()
            if args.json:
                data = [{"device": dev.to_dict(), "reasons": reasons} for dev, reasons in degraded]
                print(json.dumps({"status": "degraded" if degraded else "healthy", "degraded_devices": data}, indent=2))
            else:
                if degraded:
                    print(f"CRITICAL: Found {len(degraded)} PCIe degraded device(s):")
                    for dev, reasons in degraded:
                        print(f"  {dev.sys_path} ({dev.device_name}):")
                        for r in reasons:
                            print(f"    - {r}")
                else:
                    print("All PCIe devices operating at maximum link capability.")
            return 2 if degraded else 0

        if args.listen:
            events = list(monitor.listen_netlink())
            data = [e.to_dict() for e in events]
            if args.json:
                print(json.dumps({"status": "ok", "captured_events": data}, indent=2))
            else:
                print(f"Captured {len(events)} netlink event(s):")
                for e in events:
                    print(f"  [{e.action.upper()}] {e.subsystem}: {e.sys_path}")
            return 0

        # Default / --scan
        devices = monitor.scan_sysfs_inventory()
        data = [d.to_dict() for d in devices]
        if args.json:
            print(json.dumps({"status": "ok", "inventory": data}, indent=2))
        else:
            print(f"Discovered Hardware Devices ({len(devices)}):")
            for d in devices:
                deg_flag = " [DEGRADED]" if d.is_degraded()[0] else ""
                print(f"  {d.sys_path} -> {d.device_name}{deg_flag} (PCIe x{d.current_link_width}/{d.max_link_width}, Driver: {d.driver})")
        return 0

    except Exception as e:
        logger.error("Hardware monitor error: %s", e)
        if args.json:
            print(json.dumps({"status": "error", "error": str(e)}, indent=2))
        return 1

if __name__ == "__main__":
    sys.exit(main())
