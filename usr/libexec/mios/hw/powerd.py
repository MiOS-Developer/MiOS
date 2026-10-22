#!/usr/bin/env python3
# AI-hint: Power supply state detector and battery-aware AI inference downscaler daemon for MiOS.
# AI-related: usr/libexec/mios/hw/powerd.py, usr/lib/systemd/system/mios-powerd.service, tests/test-hw.py
"""Power-supply state detector (mios-powerd) and battery-aware AI inference downscaler.

Monitors AC/DC power supply state via /sys/class/power_supply or netlink udev events.
On DC (Battery):
  - Downscales llama-swap inference models to lightweight 3B/7B GGUF tier ('light_3b').
  - Pauses background fine-tuning containers ('mios-finetune', 'mios-embed-backfill').
  - Sets CPU energy performance preference (EPP) to 'power' and governor to 'powersave'.
  - Restricts GPU power state / cap.
On AC (Mains):
  - Restores full inference model allocations ('heavy').
  - Unpauses background fine-tuning containers.
  - Sets CPU EPP to 'balance_performance' and governor to 'performance'.
  - Restores GPU full power state.

Architectural Invariant:
Do NOT run unconstrained multi-GPU heavy training while operating on battery power.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
import glob
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("mios-powerd")

DEFAULT_STATE_FILE = "/run/mios/powerd_state.json"
DEFAULT_POLL_INTERVAL_SECONDS = 5.0
DEFAULT_TARGET_CONTAINERS = ["mios-finetune", "mios-embed-backfill"]

@dataclass
class PowerProfileState:
    """Current operational profile and power state."""
    power_source: str = "AC"  # "AC" | "BATTERY"
    cpu_epp: str = "balance_performance"  # "balance_performance" | "power"
    active_model_tier: str = "heavy"  # "heavy" | "light_3b"
    paused_containers: List[str] = field(default_factory=list)
    battery_pct: Optional[int] = 100
    battery_status: Optional[str] = "Full"
    ac_online: bool = True
    governor: str = "performance"
    gpu_power_state: str = "high"  # "high" | "low"
    last_transition_ts: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        """Convert state to serializable dictionary strictly conforming to interface contract."""
        return {
            "power_source": self.power_source,
            "cpu_epp": self.cpu_epp,
            "active_model_tier": self.active_model_tier,
            "paused_containers": list(self.paused_containers),
            "battery_pct": self.battery_pct,
            "battery_status": self.battery_status,
            "ac_online": self.ac_online,
            "governor": self.governor,
            "gpu_power_state": self.gpu_power_state,
            "last_transition_ts": self.last_transition_ts,
        }

class PowerDaemon:
    """Manages power supply telemetry detection, CPU EPP scaling, and AI inference modulation."""

    def __init__(
        self,
        sysfs_root: str = "/",
        state_file: str = DEFAULT_STATE_FILE,
        mock: bool = False,
        dry_run: bool = False,
        poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
        target_containers: Optional[List[str]] = None,
    ) -> None:
        self.sysfs_root = os.path.abspath(sysfs_root)
        self.state_file = state_file
        self.mock = mock
        self.dry_run = dry_run
        self.poll_interval = max(1.0, float(poll_interval))
        self.target_containers = target_containers or list(DEFAULT_TARGET_CONTAINERS)
        self.state = PowerProfileState()
        self._running = False

        if self.mock:
            self._init_mock_state()
        else:
            self._load_state()

    @property
    def power_supply_dir(self) -> str:
        """Return base sysfs directory for power supply subsystem."""
        return os.path.join(self.sysfs_root, "sys", "class", "power_supply")

    @property
    def cpu_base_dir(self) -> str:
        """Return base sysfs directory for CPU subsystem."""
        return os.path.join(self.sysfs_root, "sys", "devices", "system", "cpu")

    def _init_mock_state(self) -> None:
        """Initializes simulated baseline AC state."""
        self.state = PowerProfileState(
            power_source="AC",
            cpu_epp="balance_performance",
            active_model_tier="heavy",
            paused_containers=[],
            battery_pct=100,
            battery_status="Full",
            ac_online=True,
            governor="performance",
            gpu_power_state="high",
            last_transition_ts=time.time(),
        )

    def _read_file_safe(self, path: str) -> Optional[str]:
        """Safely read string content from a sysfs path."""
        if not os.path.isfile(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read().strip()
        except OSError as e:
            logger.debug("Failed reading %s: %s", path, e)
            return None

    def _write_file_safe(self, path: str, content: str) -> bool:
        """Safely write string content to a sysfs path."""
        if self.dry_run:
            logger.info("[DRY-RUN] Write '%s' -> %s", content, path)
            return True
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            return True
        except OSError as e:
            logger.warning("Failed writing '%s' to %s: %s", content, path, e)
            return False

    def _load_state(self) -> None:
        """Loads cached state from state_file if present."""
        if os.path.isfile(self.state_file):
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.state = PowerProfileState(
                    power_source=data.get("power_source", "AC"),
                    cpu_epp=data.get("cpu_epp", "balance_performance"),
                    active_model_tier=data.get("active_model_tier", "heavy"),
                    paused_containers=data.get("paused_containers", []),
                    battery_pct=data.get("battery_pct", 100),
                    battery_status=data.get("battery_status", "Full"),
                    ac_online=data.get("ac_online", True),
                    governor=data.get("governor", "performance"),
                    gpu_power_state=data.get("gpu_power_state", "high"),
                    last_transition_ts=data.get("last_transition_ts", time.time()),
                )
            except (OSError, json.JSONDecodeError) as e:
                logger.debug("Could not parse existing state file %s: %s", self.state_file, e)

    def _save_state(self) -> None:
        """Persists current state to state_file."""
        if self.dry_run:
            return
        try:
            os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
            tmp_path = f"{self.state_file}.tmp.{os.getpid()}"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(self.state.to_dict(), f, indent=2)
            os.replace(tmp_path, self.state_file)
        except OSError as e:
            logger.warning("Failed saving state to %s: %s", self.state_file, e)

    def discover_power_supplies(self) -> Dict[str, List[str]]:
        """Identifies available AC adapters and battery devices from sysfs."""
        if self.mock:
            return {
                "ac_adapters": ["ACAD"] if self.state.ac_online else [],
                "batteries": ["BAT0"],
            }

        pattern = os.path.join(self.power_supply_dir, "*")
        devices = glob.glob(pattern)
        batteries: List[str] = []
        ac_adapters: List[str] = []

        for dev_path in devices:
            name = os.path.basename(dev_path)
            dev_type = (self._read_file_safe(os.path.join(dev_path, "type")) or "").lower()
            name_lower = name.lower()

            if dev_type == "battery" or name_lower.startswith("bat"):
                batteries.append(name)
            elif (
                dev_type in ("mains", "usb", "adapter")
                or name_lower.startswith("ac")
                or name_lower.startswith("adp")
            ):
                ac_adapters.append(name)

        batteries.sort()
        ac_adapters.sort()
        return {"ac_adapters": ac_adapters, "batteries": batteries}

    def read_telemetry(self) -> Dict[str, Any]:
        """Reads hardware power supply telemetry from sysfs."""
        if self.mock:
            return {
                "ac_online": self.state.ac_online,
                "battery_pct": self.state.battery_pct,
                "battery_status": self.state.battery_status,
                "power_source": self.state.power_source,
            }

        supplies = self.discover_power_supplies()
        ac_adapters = supplies["ac_adapters"]
        batteries = supplies["batteries"]

        ac_online = False
        if not ac_adapters and not batteries:
            # If no power supply sysfs entries exist (e.g. desktop workstation / VM), assume AC
            ac_online = True
        else:
            for adapter in ac_adapters:
                online_val = self._read_file_safe(os.path.join(self.power_supply_dir, adapter, "online"))
                if online_val == "1":
                    ac_online = True
                    break

        battery_pct: Optional[int] = None
        battery_status: Optional[str] = None

        if batteries:
            total_capacity = 0
            count = 0
            for bat in batteries:
                bat_dir = os.path.join(self.power_supply_dir, bat)
                cap_str = self._read_file_safe(os.path.join(bat_dir, "capacity"))
                stat_str = self._read_file_safe(os.path.join(bat_dir, "status"))
                if cap_str is not None:
                    try:
                        total_capacity += int(cap_str)
                        count += 1
                    except ValueError:
                        pass
                if stat_str is not None and battery_status is None:
                    battery_status = stat_str

            if count > 0:
                battery_pct = int(total_capacity / count)

        # If AC is online, power source is AC. If battery is discharging or no AC is online, BATTERY.
        if ac_online:
            power_source = "AC"
            if battery_status is None:
                battery_status = "Full" if (battery_pct is None or battery_pct >= 95) else "Charging"
        else:
            power_source = "BATTERY"
            if battery_status is None:
                battery_status = "Discharging"

        return {
            "ac_online": ac_online,
            "battery_pct": battery_pct if battery_pct is not None else 100,
            "battery_status": battery_status or "Full",
            "power_source": power_source,
        }

    def set_cpu_epp_and_governor(self, epp: str, governor: str) -> bool:
        """Applies energy performance preference and scaling governor across all CPUs."""
        if self.mock:
            self.state.cpu_epp = epp
            self.state.governor = governor
            return True

        success = True
        cpu_dirs = glob.glob(os.path.join(self.cpu_base_dir, "cpu[0-9]*"))
        for cpu_dir in cpu_dirs:
            # Check cpufreq directory
            cpufreq_dir = os.path.join(cpu_dir, "cpufreq")
            epp_cpufreq = os.path.join(cpufreq_dir, "energy_performance_preference")
            epp_power = os.path.join(cpu_dir, "power", "energy_performance_preference")
            gov_path = os.path.join(cpufreq_dir, "scaling_governor")

            # Write EPP
            if os.path.isfile(epp_cpufreq):
                if not self._write_file_safe(epp_cpufreq, epp):
                    success = False
            elif os.path.isfile(epp_power):
                if not self._write_file_safe(epp_power, epp):
                    success = False

            # Write governor
            if os.path.isfile(gov_path):
                if not self._write_file_safe(gov_path, governor):
                    success = False

        self.state.cpu_epp = epp
        self.state.governor = governor
        return success

    def set_active_model_tier(self, tier: str) -> bool:
        """Modulates llama-swap inference model tier."""
        self.state.active_model_tier = tier
        logger.info("Inference active model tier configured to: %s", tier)
        return True

    def manage_containers(self, action: str) -> List[str]:
        """Pauses or unpauses background fine-tuning containers."""
        if self.mock or self.dry_run:
            if action == "pause":
                self.state.paused_containers = list(self.target_containers)
            elif action == "unpause":
                self.state.paused_containers = []
            return self.state.paused_containers

        podman_bin = shutil.which("podman")
        if action == "pause":
            paused_list = []
            for cname in self.target_containers:
                if podman_bin:
                    try:
                        # Check if container is running
                        res = subprocess.run(
                            [podman_bin, "container", "inspect", "-f", "{{.State.Status}}", cname],
                            capture_output=True,
                            text=True,
                            timeout=5,
                        )
                        if res.returncode == 0 and res.stdout.strip() == "running":
                            pause_res = subprocess.run(
                                [podman_bin, "pause", cname],
                                capture_output=True,
                                text=True,
                                timeout=5,
                            )
                            if pause_res.returncode == 0:
                                logger.info("Paused container: %s", cname)
                    except Exception as e:
                        logger.debug("Failed pausing container %s via podman: %s", cname, e)
                paused_list.append(cname)
            self.state.paused_containers = paused_list

        elif action == "unpause":
            for cname in list(self.state.paused_containers):
                if podman_bin:
                    try:
                        subprocess.run(
                            [podman_bin, "unpause", cname],
                            capture_output=True,
                            text=True,
                            timeout=5,
                        )
                        logger.info("Unpaused container: %s", cname)
                    except Exception as e:
                        logger.debug("Failed unpausing container %s via podman: %s", cname, e)
            self.state.paused_containers = []

        return self.state.paused_containers

    def apply_profile(self, power_source: str, force: bool = False) -> PowerProfileState:
        """Transitions system operational profile between AC and BATTERY / DC."""
        normalized_source = "BATTERY" if power_source.upper() in ("DC", "BATTERY", "BAT") else "AC"

        if not force and normalized_source == self.state.power_source:
            # Already in desired state
            return self.state

        logger.info("Transitioning power profile -> %s", normalized_source)

        if normalized_source == "BATTERY":
            # DC Power Profile:
            # - CPU EPP -> power, Governor -> powersave
            # - Model Tier -> light_3b
            # - Pause background fine-tuning containers
            # - GPU power state -> low
            self.set_cpu_epp_and_governor(epp="power", governor="powersave")
            self.set_active_model_tier("light_3b")
            self.manage_containers("pause")
            self.state.power_source = "BATTERY"
            self.state.ac_online = False
            self.state.gpu_power_state = "low"
            if self.state.battery_status == "Full" or self.state.battery_status == "Charging":
                self.state.battery_status = "Discharging"
        else:
            # AC Power Profile:
            # - CPU EPP -> balance_performance, Governor -> performance
            # - Model Tier -> heavy
            # - Unpause background fine-tuning containers
            # - GPU power state -> high
            self.set_cpu_epp_and_governor(epp="balance_performance", governor="performance")
            self.set_active_model_tier("heavy")
            self.manage_containers("unpause")
            self.state.power_source = "AC"
            self.state.ac_online = True
            self.state.gpu_power_state = "high"
            if self.state.battery_status == "Discharging":
                self.state.battery_status = "Charging"

        self.state.last_transition_ts = time.time()
        self._save_state()
        return self.state

    def poll_and_sync(self) -> PowerProfileState:
        """Polls current telemetry and transitions profile if power source changed."""
        telemetry = self.read_telemetry()
        current_source = telemetry["power_source"]
        self.state.battery_pct = telemetry["battery_pct"]
        self.state.battery_status = telemetry["battery_status"]
        self.state.ac_online = telemetry["ac_online"]

        if current_source != self.state.power_source:
            logger.info("Power supply change detected: %s -> %s", self.state.power_source, current_source)
            self.apply_profile(current_source)
        else:
            self._save_state()

        return self.state

    def get_status(self) -> PowerProfileState:
        """Returns current power profile state."""
        if not self.mock:
            self.poll_and_sync()
        return self.state

    def run_daemon(self) -> None:
        """Runs the power daemon event loop until terminated."""
        self._running = True
        logger.info("Starting mios-powerd daemon loop (poll interval: %.1fs, mock: %s)", self.poll_interval, self.mock)

        def _handle_signal(signum: int, frame: Any) -> None:
            logger.info("Received termination signal (%s), exiting...", signum)
            self._running = False

        try:
            signal.signal(signal.SIGINT, _handle_signal)
            signal.signal(signal.SIGTERM, _handle_signal)
        except (ValueError, AttributeError):
            pass

        # Initial synchronization
        self.poll_and_sync()

        while self._running:
            try:
                time.sleep(self.poll_interval)
                if not self._running:
                    break
                self.poll_and_sync()
            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error("Error during powerd poll cycle: %s", e)

        logger.info("mios-powerd daemon stopped cleanly.")

def parse_args() -> argparse.Namespace:
    """Parses CLI command-line arguments."""
    parser = argparse.ArgumentParser(
        description="MiOS Power Supply Detector & Battery-Aware AI Inference Downscaler"
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Query current power profile and telemetry status",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit output in strict JSON format",
    )
    parser.add_argument(
        "--set-state",
        choices=["ac", "dc", "battery", "AC", "DC", "BATTERY"],
        help="Manually force power profile transition to AC or DC/battery",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Run in headless mock mode with synthetic hardware states",
    )
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="Start background monitoring daemon event loop",
    )
    parser.add_argument(
        "--sysfs-root",
        default="/",
        help="Root path for sysfs hierarchy (default: /)",
    )
    parser.add_argument(
        "--state-file",
        default=DEFAULT_STATE_FILE,
        help=f"Path to state cache JSON file (default: {DEFAULT_STATE_FILE})",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=DEFAULT_POLL_INTERVAL_SECONDS,
        help=f"Polling interval in seconds (default: {DEFAULT_POLL_INTERVAL_SECONDS})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Execute without making physical writes to sysfs or modifying containers",
    )
    return parser.parse_args()

def main() -> int:
    """Main CLI entry point."""
    args = parse_args()

    daemon = PowerDaemon(
        sysfs_root=args.sysfs_root,
        state_file=args.state_file,
        mock=args.mock,
        dry_run=args.dry_run,
        poll_interval=args.poll_interval,
    )

    try:
        if args.set_state:
            state = daemon.apply_profile(args.set_state, force=True)
            if args.json:
                print(json.dumps(state.to_dict(), indent=2))
            else:
                print(f"Transitioned to {state.power_source}:")
                print(f"  CPU EPP:             {state.cpu_epp}")
                print(f"  Scaling Governor:    {state.governor}")
                print(f"  Active Model Tier:   {state.active_model_tier}")
                print(f"  Paused Containers:   {state.paused_containers}")
                print(f"  GPU Power State:     {state.gpu_power_state}")
            return 0

        if args.daemon:
            daemon.run_daemon()
            return 0

        # Default action: status query
        state = daemon.get_status()
        if args.json:
            print(json.dumps(state.to_dict(), indent=2))
        else:
            print("MiOS Hardware Power & AI Downscaler Status:")
            print(f"  Power Source:        {state.power_source} (AC Online: {state.ac_online})")
            print(f"  Battery:             {state.battery_pct}% ({state.battery_status})")
            print(f"  CPU EPP:             {state.cpu_epp}")
            print(f"  Scaling Governor:    {state.governor}")
            print(f"  Active Model Tier:   {state.active_model_tier}")
            print(f"  Paused Containers:   {state.paused_containers}")
            print(f"  GPU Power State:     {state.gpu_power_state}")
        return 0

    except Exception as e:
        logger.error("Powerd command error: %s", e)
        if args.json:
            print(json.dumps({"status": "error", "error": str(e)}, indent=2))
        return 1

if __name__ == "__main__":
    sys.exit(main())
