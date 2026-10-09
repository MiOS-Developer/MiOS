#!/usr/bin/env python3
# AI-hint: Guest virtual ACPI battery and power state passthrough daemon for MiOS.
# AI-related: usr/libexec/mios/hw/battery_passthrough.py, tests/test-hw.py
"""Guest virtual ACPI battery and power state passthrough daemon for MiOS.

Reads physical power supply state from /sys/class/power_supply/ (BAT0, AC, etc.)
and generates QMP/ACPI event notifications for guest virtual machines.

Architectural Invariant:
Do NOT poll power supply sysfs files faster than once every 5 seconds to conserve CPU power.
"""

from __future__ import annotations

import argparse
import glob
import json
import logging
import math
import os
import socket
import sys
import time
from typing import Any, Dict, List, Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("mios-battery-passthrough")

MINIMUM_POLL_INTERVAL_SECONDS = 5.0
DEFAULT_POLL_INTERVAL_SECONDS = 5.0

class BatteryTelemetryReader:
    """Reads and parses battery and AC adapter telemetry from sysfs."""

    def __init__(self, sysfs_root: str = "/") -> None:
        self.sysfs_root = os.path.abspath(sysfs_root)

    @property
    def power_supply_dir(self) -> str:
        """Return base directory for power supply devices in sysfs."""
        return os.path.join(self.sysfs_root, "sys", "class", "power_supply")

    def _read_file_safe(self, path: str) -> Optional[str]:
        """Safely read string from sysfs file."""
        if not os.path.isfile(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read().strip()
        except OSError as e:
            logger.debug("Failed reading sysfs path %s: %s", path, e)
            return None

    def _read_int_safe(self, path: str) -> Optional[int]:
        """Safely read integer from sysfs file."""
        val = self._read_file_safe(path)
        if val is None:
            return None
        try:
            return int(val)
        except ValueError:
            return None

    def discover_supplies(self) -> Dict[str, List[str]]:
        """Identify available batteries and AC adapters."""
        pattern = os.path.join(self.power_supply_dir, "*")
        devices = glob.glob(pattern)
        batteries = []
        ac_adapters = []

        for dev_path in devices:
            name = os.path.basename(dev_path)
            dev_type = self._read_file_safe(os.path.join(dev_path, "type")) or ""
            dev_type_lower = dev_type.lower()
            name_lower = name.lower()

            if dev_type_lower == "battery" or name_lower.startswith("bat"):
                batteries.append(name)
            elif (
                dev_type_lower in ("mains", "usb", "adapter")
                or name_lower.startswith("ac")
                or name_lower.startswith("adp")
            ):
                ac_adapters.append(name)

        batteries.sort()
        ac_adapters.sort()
        return {"batteries": batteries, "ac_adapters": ac_adapters}

    def read_ac_status(self) -> Dict[str, Any]:
        """Read state of AC adapter(s)."""
        supplies = self.discover_supplies()
        adapters = supplies["ac_adapters"]
        online_count = 0
        details = []

        for adapter_name in adapters:
            adapter_dir = os.path.join(self.power_supply_dir, adapter_name)
            online_val = self._read_int_safe(os.path.join(adapter_dir, "online"))
            is_online = online_val == 1 if online_val is not None else False
            if is_online:
                online_count += 1
            details.append({"name": adapter_name, "online": is_online})

        # If no AC adapters found in sysfs (e.g. desktop), default to online
        is_ac_online = online_count > 0 if adapters else True

        return {
            "ac_online": is_ac_online,
            "adapters": details,
            "has_adapter": len(adapters) > 0,
        }

    def read_battery_status(self, battery_name: Optional[str] = None) -> Dict[str, Any]:
        """Read detailed telemetry for a single battery or aggregate all batteries."""
        supplies = self.discover_supplies()
        batteries = supplies["batteries"]

        if not batteries:
            return {
                "present": False,
                "status": "Unknown",
                "capacity_percent": 100,
                "is_charging": False,
                "is_discharging": False,
                "estimated_runtime_minutes": None,
                "batteries": [],
            }

        target_bats = [battery_name] if battery_name and battery_name in batteries else batteries
        battery_details: List[Dict[str, Any]] = []
        total_energy_now = 0
        total_energy_full = 0
        total_power_now = 0
        status_set = set()

        for bname in target_bats:
            bdir = os.path.join(self.power_supply_dir, bname)
            status = self._read_file_safe(os.path.join(bdir, "status")) or "Unknown"
            capacity = self._read_int_safe(os.path.join(bdir, "capacity")) or 0
            voltage_now = self._read_int_safe(os.path.join(bdir, "voltage_now"))  # in microvolts
            current_now = self._read_int_safe(os.path.join(bdir, "current_now"))  # in microamps
            power_now = self._read_int_safe(os.path.join(bdir, "power_now"))  # in microwatts
            charge_now = self._read_int_safe(os.path.join(bdir, "charge_now"))  # in microamp-hours
            charge_full = self._read_int_safe(os.path.join(bdir, "charge_full"))  # in microamp-hours
            energy_now = self._read_int_safe(os.path.join(bdir, "energy_now"))  # in microwatt-hours
            energy_full = self._read_int_safe(os.path.join(bdir, "energy_full"))  # in microwatt-hours
            model_name = self._read_file_safe(os.path.join(bdir, "model_name")) or "Generic"
            manufacturer = self._read_file_safe(os.path.join(bdir, "manufacturer")) or "Generic"

            # Derive energy if not directly reported but charge & voltage are available
            if energy_now is None and charge_now is not None and voltage_now is not None:
                energy_now = int((charge_now * voltage_now) / 1_000_000)
            if energy_full is None and charge_full is not None and voltage_now is not None:
                energy_full = int((charge_full * voltage_now) / 1_000_000)
            if power_now is None and current_now is not None and voltage_now is not None:
                power_now = int((current_now * voltage_now) / 1_000_000)

            if energy_now is not None:
                total_energy_now += energy_now
            if energy_full is not None:
                total_energy_full += energy_full
            if power_now is not None:
                total_power_now += power_now

            status_set.add(status)

            battery_details.append({
                "name": bname,
                "status": status,
                "capacity_percent": capacity,
                "voltage_uv": voltage_now,
                "current_ua": current_now,
                "power_uw": power_now,
                "charge_now_uah": charge_now,
                "charge_full_uah": charge_full,
                "energy_now_uwh": energy_now,
                "energy_full_uwh": energy_full,
                "model_name": model_name,
                "manufacturer": manufacturer,
            })

        # Calculate aggregated capacity
        if total_energy_full > 0:
            agg_capacity = round((total_energy_now / total_energy_full) * 100, 1)
        elif battery_details:
            agg_capacity = round(sum(b["capacity_percent"] for b in battery_details) / len(battery_details), 1)
        else:
            agg_capacity = 100.0

        # Determine primary status
        if "Discharging" in status_set:
            primary_status = "Discharging"
        elif "Charging" in status_set:
            primary_status = "Charging"
        elif "Full" in status_set:
            primary_status = "Full"
        elif status_set:
            primary_status = next(iter(status_set))
        else:
            primary_status = "Unknown"

        # Calculate estimated remaining discharge / charge runtime in minutes
        estimated_runtime: Optional[float] = None
        if primary_status == "Discharging" and total_power_now > 0 and total_energy_now > 0:
            estimated_runtime = round((total_energy_now / total_power_now) * 60, 1)
        elif primary_status == "Charging" and total_power_now > 0 and total_energy_full > total_energy_now:
            remaining_to_charge = total_energy_full - total_energy_now
            estimated_runtime = round((remaining_to_charge / total_power_now) * 60, 1)

        return {
            "present": True,
            "status": primary_status,
            "capacity_percent": agg_capacity,
            "is_charging": primary_status == "Charging",
            "is_discharging": primary_status == "Discharging",
            "estimated_runtime_minutes": estimated_runtime,
            "total_energy_now_uwh": total_energy_now,
            "total_energy_full_uwh": total_energy_full,
            "total_power_now_uw": total_power_now,
            "batteries": battery_details,
        }

    def get_full_power_snapshot(self) -> Dict[str, Any]:
        """Aggregate battery and AC adapter state into a unified telemetry snapshot."""
        ac_info = self.read_ac_status()
        bat_info = self.read_battery_status()
        return {
            "timestamp": time.time(),
            "ac_online": ac_info["ac_online"],
            "battery_present": bat_info["present"],
            "status": bat_info["status"],
            "capacity_percent": bat_info["capacity_percent"],
            "is_charging": bat_info["is_charging"],
            "is_discharging": bat_info["is_discharging"],
            "estimated_runtime_minutes": bat_info["estimated_runtime_minutes"],
            "ac_details": ac_info,
            "battery_details": bat_info,
        }

class BatteryPassthroughDaemon:
    """Manages periodic battery telemetry synchronization and QMP event delivery."""

    def __init__(
        self,
        sysfs_root: str = "/",
        domain: Optional[str] = None,
        qmp_socket: Optional[str] = None,
        poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
        dry_run: bool = False,
    ) -> None:
        self.reader = BatteryTelemetryReader(sysfs_root=sysfs_root)
        self.domain = domain
        self.qmp_socket = qmp_socket or (f"/var/run/libvirt/qemu/{domain}.qmp" if domain else None)
        self.dry_run = dry_run

        # Enforce rate-limiting invariant
        self.poll_interval = self.validate_and_clamp_poll_interval(poll_interval)

    @staticmethod
    def validate_and_clamp_poll_interval(interval: float) -> float:
        """Ensure polling interval conforms to the >= 5.0 second architectural constraint."""
        import math
        if math.isnan(interval) or interval < MINIMUM_POLL_INTERVAL_SECONDS:
            logger.warning(
                "Requested poll interval %s violates architectural minimum (>= %.1fs). Clamping to %.1fs.",
                interval,
                MINIMUM_POLL_INTERVAL_SECONDS,
                MINIMUM_POLL_INTERVAL_SECONDS,
            )
            return MINIMUM_POLL_INTERVAL_SECONDS
        return interval

    def format_qmp_battery_event(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """Format an ACPI / QMP guest event structure from power telemetry."""
        return {
            "execute": "guest-exec",
            "arguments": {
                "path": "/usr/libexec/acpi-battery-event",
                "arg": [
                    f"--status={snapshot['status']}",
                    f"--capacity={snapshot['capacity_percent']}",
                    f"--ac-online={'1' if snapshot['ac_online'] else '0'}",
                    f"--charging={'1' if snapshot['is_charging'] else '0'}",
                    f"--discharging={'1' if snapshot['is_discharging'] else '0'}",
                    f"--runtime-mins={snapshot['estimated_runtime_minutes'] or 0}",
                ],
                "capture-output": False,
            },
            "telemetry_payload": {
                "event": "ACPI_POWER_STATUS_CHANGE",
                "data": {
                    "ac_online": snapshot["ac_online"],
                    "battery_status": snapshot["status"],
                    "battery_level": snapshot["capacity_percent"],
                    "estimated_minutes": snapshot["estimated_runtime_minutes"],
                },
            },
        }

    def send_qmp_payload(self, payload: Dict[str, Any]) -> bool:
        """Send QMP command to domain socket if socket exists."""
        if not self.qmp_socket or self.dry_run:
            logger.info("[DRY-RUN/NO-SOCKET] QMP payload ready: %s", json.dumps(payload["telemetry_payload"]))
            return True

        if not os.path.exists(self.qmp_socket):
            logger.debug("QMP socket does not exist at %s", self.qmp_socket)
            return False

        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(2.0)
            sock.connect(self.qmp_socket)
            # Initial QMP handshake negotiation
            initial = sock.recv(1024)
            logger.debug("QMP initial handshake: %s", initial)
            sock.sendall(b'{"execute": "qmp_capabilities"}\n')
            cap_resp = sock.recv(1024)
            logger.debug("QMP cap response: %s", cap_resp)

            # Send payload
            cmd_bytes = (json.dumps(payload) + "\n").encode("utf-8")
            sock.sendall(cmd_bytes)
            resp = sock.recv(1024)
            logger.debug("QMP command response: %s", resp)
            sock.close()
            return True
        except (socket.error, OSError) as e:
            logger.warning("Failed sending QMP command to %s: %s", self.qmp_socket, e)
            return False

    def sync_once(self) -> Dict[str, Any]:
        """Execute a single telemetry read and forward cycle."""
        snapshot = self.reader.get_full_power_snapshot()
        qmp_event = self.format_qmp_battery_event(snapshot)
        sent = self.send_qmp_payload(qmp_event)
        return {
            "snapshot": snapshot,
            "qmp_event": qmp_event,
            "qmp_delivered": sent,
            "domain": self.domain,
        }

    def run_daemon(self, max_iterations: Optional[int] = None) -> None:
        """Run continuous battery telemetry sync loop respecting poll interval floor."""
        logger.info(
            "Starting MiOS battery passthrough daemon for domain '%s' (poll interval: %.2fs)",
            self.domain or "all",
            self.poll_interval,
        )
        iterations = 0
        try:
            while True:
                res = self.sync_once()
                snap = res["snapshot"]
                logger.info(
                    "Battery telemetry: Status=%s, Cap=%.1f%%, AC=%s, Est=%s mins",
                    snap["status"],
                    snap["capacity_percent"],
                    snap["ac_online"],
                    snap["estimated_runtime_minutes"],
                )
                iterations += 1
                if max_iterations is not None and iterations >= max_iterations:
                    break
                time.sleep(self.poll_interval)
        except KeyboardInterrupt:
            logger.info("MiOS battery passthrough daemon stopped by operator.")

def main() -> int:
    parser = argparse.ArgumentParser(
        description="MiOS Guest Virtual ACPI Battery Passthrough Daemon (task_365)"
    )
    parser.add_argument(
        "--action",
        choices=["status", "once", "daemon", "discover"],
        default="status",
        help="Operation to perform",
    )
    parser.add_argument("--domain", default=None, help="Target libvirt VM domain name")
    parser.add_argument("--qmp-socket", default=None, help="Direct path to QMP unix socket")
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=DEFAULT_POLL_INTERVAL_SECONDS,
        help="Polling interval in seconds (enforced >= 5.0s)",
    )
    parser.add_argument("--sysfs-root", default="/", help="Sysfs root path for mocks/testing")
    parser.add_argument("--dry-run", action="store_true", help="Simulate QMP delivery")
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON")

    args = parser.parse_args()

    daemon = BatteryPassthroughDaemon(
        sysfs_root=args.sysfs_root,
        domain=args.domain,
        qmp_socket=args.qmp_socket,
        poll_interval=args.poll_interval,
        dry_run=args.dry_run,
    )

    if args.action == "discover":
        supplies = daemon.reader.discover_supplies()
        if args.json:
            print(json.dumps(supplies, indent=2))
        else:
            print(f"Batteries: {supplies['batteries']}")
            print(f"AC Adapters: {supplies['ac_adapters']}")
    elif args.action in ("status", "once"):
        res = daemon.sync_once()
        if args.json:
            print(json.dumps(res, indent=2))
        else:
            snap = res["snapshot"]
            print("=== MiOS Battery & Power Supply Telemetry ===")
            print(f"  AC Online:        {snap['ac_online']}")
            print(f"  Battery Present:  {snap['battery_present']}")
            print(f"  Status:           {snap['status']}")
            print(f"  Capacity:         {snap['capacity_percent']}%")
            print(f"  Est. Runtime:     {snap['estimated_runtime_minutes']} minutes")
            print(f"  QMP Delivered:    {res['qmp_delivered']}")
    elif args.action == "daemon":
        daemon.run_daemon()
    return 0

if __name__ == "__main__":
    sys.exit(main())
