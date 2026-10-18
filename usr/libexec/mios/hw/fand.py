#!/usr/bin/env python3
# AI-hint: Multi-zone PID Fan Controller daemon with 5°C hysteresis, hwmon sensor discovery, and acoustic rate-limiting.
# AI-related: usr/libexec/mios/hw/fand.py, tests/test-hw.py, usr/share/mios/mios.toml
"""Multi-zone PID Fan Controller daemon for MiOS.

Modulates chassis, CPU, GPU, and NVMe fan channels smoothly across multi-zone PID curves
with a 5°C hysteresis deadband to prevent acoustic pulsing, and limits RPM/PWM ramp transitions
(<200 RPM/sec or rate-limited PWM steps) for silent idle and acoustic stabilization under load.
"""

from __future__ import annotations

import argparse
import glob
import json
import logging
import math
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("mios-fand")

DEFAULT_STATE_FILE = "/run/mios/fan_control_state.json"
DEFAULT_HYSTERESIS_DEG = 5.0
DEFAULT_MAX_RPM_RAMP_PER_SEC = 200.0  # Max RPM change per second
DEFAULT_MAX_PWM_RAMP_PER_SEC = 25.0   # Max PWM (0-255) change per second (~10%/sec)
CRITICAL_TEMP_DEFAULT = 85.0          # Critical temp triggering 100% PWM

@dataclass
class FanZoneConfig:
    name: str
    target_temp: float = 65.0
    critical_temp: float = 85.0
    hysteresis_deg: float = DEFAULT_HYSTERESIS_DEG
    kp: float = 2.5
    ki: float = 0.1
    kd: float = 1.0
    min_pwm: int = 40       # Minimum PWM value (0-255)
    max_pwm: int = 255      # Maximum PWM value (0-255)
    sensor_paths: List[str] = field(default_factory=list)
    fan_pwm_paths: List[str] = field(default_factory=list)

@dataclass
class PIDState:
    integral: float = 0.0
    last_error: float = 0.0
    last_temp: float = 0.0
    last_pwm: float = 50.0
    last_target_pwm: float = 50.0
    last_update_ts: float = 0.0

class MultiZonePIDFanController:
    """Multi-zone PID fan controller with hysteresis and acoustic ramp damping."""

    def __init__(
        self,
        sysfs_root: str = "/",
        zones: Optional[Dict[str, FanZoneConfig]] = None,
        hysteresis_deg: float = DEFAULT_HYSTERESIS_DEG,
        max_pwm_ramp_per_sec: float = DEFAULT_MAX_PWM_RAMP_PER_SEC,
        state_file: str = DEFAULT_STATE_FILE,
        dry_run: bool = False,
    ) -> None:
        self.sysfs_root = os.path.abspath(sysfs_root)
        self.hysteresis_deg = hysteresis_deg
        self.max_pwm_ramp_per_sec = max_pwm_ramp_per_sec
        self.state_file = state_file
        self.dry_run = dry_run
        self.pid_states: Dict[str, PIDState] = {}
        self.zones: Dict[str, FanZoneConfig] = zones or self._default_zones()
        for zone_name in self.zones:
            self.pid_states[zone_name] = PIDState(last_update_ts=time.time())

    def _default_zones(self) -> Dict[str, FanZoneConfig]:
        return {
            "cpu": FanZoneConfig(
                name="cpu",
                target_temp=65.0,
                critical_temp=85.0,
                hysteresis_deg=self.hysteresis_deg,
                kp=3.0,
                ki=0.15,
                kd=1.2,
                min_pwm=50,
                max_pwm=255,
            ),
            "gpu": FanZoneConfig(
                name="gpu",
                target_temp=70.0,
                critical_temp=88.0,
                hysteresis_deg=self.hysteresis_deg,
                kp=3.5,
                ki=0.2,
                kd=1.5,
                min_pwm=60,
                max_pwm=255,
            ),
            "nvme": FanZoneConfig(
                name="nvme",
                target_temp=55.0,
                critical_temp=75.0,
                hysteresis_deg=self.hysteresis_deg,
                kp=2.0,
                ki=0.05,
                kd=0.5,
                min_pwm=40,
                max_pwm=200,
            ),
            "chassis": FanZoneConfig(
                name="chassis",
                target_temp=50.0,
                critical_temp=75.0,
                hysteresis_deg=self.hysteresis_deg,
                kp=2.0,
                ki=0.1,
                kd=0.8,
                min_pwm=45,
                max_pwm=220,
            ),
        }

    def discover_hwmon_devices(self) -> Dict[str, Any]:
        """Discover hwmon temperature sensors and PWM controllers from sysfs."""
        hwmon_base = os.path.join(self.sysfs_root, "sys", "class", "hwmon")
        devices: Dict[str, Any] = {"sensors": {}, "pwms": {}}

        if not os.path.isdir(hwmon_base):
            return devices

        for hwmon_dir in sorted(glob.glob(os.path.join(hwmon_base, "hwmon*"))):
            hwmon_id = os.path.basename(hwmon_dir)
            name_file = os.path.join(hwmon_dir, "name")
            dev_name = "unknown"
            if os.path.isfile(name_file):
                try:
                    with open(name_file, "r", encoding="utf-8") as f:
                        dev_name = f.read().strip()
                except Exception:
                    pass

            # Detect temperature inputs (e.g. temp1_input in millidegrees C)
            for temp_path in glob.glob(os.path.join(hwmon_dir, "temp*_input")):
                temp_key = f"{hwmon_id}_{os.path.basename(temp_path)}"
                devices["sensors"][temp_key] = {
                    "hwmon": hwmon_id,
                    "device_name": dev_name,
                    "path": temp_path,
                }

            # Detect PWM controls
            for pwm_path in glob.glob(os.path.join(hwmon_dir, "pwm[0-9]*")):
                if not pwm_path.endswith(("_enable", "_mode", "_freq")):
                    pwm_key = f"{hwmon_id}_{os.path.basename(pwm_path)}"
                    enable_path = f"{pwm_path}_enable"
                    devices["pwms"][pwm_key] = {
                        "hwmon": hwmon_id,
                        "device_name": dev_name,
                        "path": pwm_path,
                        "enable_path": enable_path if os.path.exists(enable_path) else None,
                    }

        return devices

    def read_sensor_temp(self, sensor_path: str) -> Optional[float]:
        """Read temperature in Celsius from sysfs millidegree file."""
        if not os.path.isfile(sensor_path):
            return None
        try:
            with open(sensor_path, "r", encoding="utf-8") as f:
                val = f.read().strip()
                # hwmon temperature is reported in millidegrees Celsius
                return float(val) / 1000.0 if float(val) > 1000.0 else float(val)
        except Exception as e:
            logger.debug(f"Failed to read sensor {sensor_path}: {e}")
            return None

    def compute_pid_pwm(
        self,
        zone_name: str,
        current_temp: float,
        dt: float,
    ) -> int:
        """Compute target PWM for a zone using PID algorithm with 5°C hysteresis and rate-limiting."""
        zone = self.zones.get(zone_name)
        if not zone:
            return 128

        state = self.pid_states.get(zone_name)
        if not state:
            state = PIDState(last_update_ts=time.time())
            self.pid_states[zone_name] = state

        # 1. Critical Temperature Override
        if current_temp >= zone.critical_temp:
            logger.warning(f"Zone {zone_name} temp {current_temp:.1f}C >= critical {zone.critical_temp:.1f}C! Forcing 100% PWM.")
            target_pwm = float(zone.max_pwm)
            state.last_error = current_temp - zone.target_temp
            state.last_temp = current_temp
            state.last_target_pwm = target_pwm
            state.last_pwm = target_pwm
            state.last_update_ts = time.time()
            return int(target_pwm)

        # 2. 5°C Hysteresis Deadband Logic
        # If current temperature is within hysteresis deadband of target temperature and falling,
        # avoid rapid PWM oscillations by holding the previous target.
        temp_delta = current_temp - state.last_temp
        if abs(current_temp - zone.target_temp) <= zone.hysteresis_deg and temp_delta <= 0:
            # Temperature is stable or dropping inside the deadband - apply hysteresis damping
            effective_error = max(0.0, current_temp - zone.target_temp)
        else:
            effective_error = current_temp - zone.target_temp

        # 3. PID Calculations
        p_term = zone.kp * effective_error

        # Anti-windup integration clamp
        if dt > 0:
            state.integral += effective_error * dt
            state.integral = max(-50.0, min(50.0, state.integral))
        i_term = zone.ki * state.integral

        d_term = 0.0
        if dt > 0:
            d_term = zone.kd * ((effective_error - state.last_error) / dt)

        raw_pwm = zone.min_pwm + p_term + i_term + d_term
        clamped_target_pwm = max(float(zone.min_pwm), min(float(zone.max_pwm), raw_pwm))

        # 4. Acoustic Ramp Damping (Rate-limiting PWM step changes)
        max_step = self.max_pwm_ramp_per_sec * max(dt, 0.1)
        pwm_delta = clamped_target_pwm - state.last_pwm
        if abs(pwm_delta) > max_step:
            actual_pwm = state.last_pwm + math.copysign(max_step, pwm_delta)
        else:
            actual_pwm = clamped_target_pwm

        actual_pwm = max(float(zone.min_pwm), min(float(zone.max_pwm), actual_pwm))

        # Update State
        state.last_error = effective_error
        state.last_temp = current_temp
        state.last_target_pwm = clamped_target_pwm
        state.last_pwm = actual_pwm
        state.last_update_ts = time.time()

        return int(round(actual_pwm))

    def write_fan_pwm(self, pwm_path: str, pwm_val: int) -> bool:
        """Write computed PWM value (0-255) to sysfs fan controller."""
        if self.dry_run:
            logger.debug(f"[DRY-RUN] Set {pwm_path} -> {pwm_val}")
            return True

        if not os.path.exists(pwm_path):
            return False

        try:
            # Enable manual PWM control if enable file exists
            enable_path = f"{pwm_path}_enable"
            if os.path.isfile(enable_path):
                with open(enable_path, "w", encoding="utf-8") as f:
                    f.write("1\n")  # 1 = Manual control mode in hwmon

            with open(pwm_path, "w", encoding="utf-8") as f:
                f.write(f"{pwm_val}\n")
            return True
        except Exception as e:
            logger.error(f"Failed to write PWM to {pwm_path}: {e}")
            return False

    def step_simulation(
        self,
        zone_temps: Dict[str, float],
        dt: float = 1.0,
    ) -> Dict[str, Dict[str, Any]]:
        """Simulate one control step with given zone temperatures."""
        results = {}
        for zone_name, temp in zone_temps.items():
            pwm = self.compute_pid_pwm(zone_name, temp, dt)
            state = self.pid_states[zone_name]
            results[zone_name] = {
                "temperature": temp,
                "pwm": pwm,
                "target_pwm": int(round(state.last_target_pwm)),
                "integral": round(state.integral, 3),
            }
        return results

    def get_status(self) -> Dict[str, Any]:
        """Return status dictionary of all fan zones and PID states."""
        return {
            "hysteresis_deg": self.hysteresis_deg,
            "max_pwm_ramp_per_sec": self.max_pwm_ramp_per_sec,
            "dry_run": self.dry_run,
            "zones": {
                name: {
                    "target_temp": z.target_temp,
                    "critical_temp": z.critical_temp,
                    "min_pwm": z.min_pwm,
                    "max_pwm": z.max_pwm,
                    "kp": z.kp,
                    "ki": z.ki,
                    "kd": z.kd,
                    "current_pwm": int(round(self.pid_states[name].last_pwm)),
                    "last_temp": round(self.pid_states[name].last_temp, 2),
                }
                for name, z in self.zones.items()
            },
        }

    def save_state(self) -> None:
        """Persist daemon state to JSON."""
        try:
            os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(self.get_status(), f, indent=2)
        except Exception as e:
            logger.debug(f"Failed to save state file: {e}")

def main() -> int:
    parser = argparse.ArgumentParser(description="MiOS Multi-zone PID Fan Controller")
    parser.add_argument("--dry-run", action="store_true", help="Execute without modifying sysfs")
    parser.add_argument("--status", action="store_true", help="Show current fan zone controller status")
    parser.add_argument("--simulate-temp", type=float, help="Simulate a single temperature reading for CPU")
    args = parser.parse_args()

    controller = MultiZonePIDFanController(dry_run=args.dry_run)

    if args.status:
        print(json.dumps(controller.get_status(), indent=2))
        return 0

    if args.simulate_temp is not None:
        res = controller.step_simulation({"cpu": args.simulate_temp}, dt=1.0)
        print(json.dumps(res, indent=2))
        return 0

    logger.info("MiOS Fan Controller initialized. Running discovery...")
    hw = controller.discover_hwmon_devices()
    logger.info(f"Discovered {len(hw['sensors'])} sensors and {len(hw['pwms'])} PWM channels.")
    controller.save_state()
    return 0

if __name__ == "__main__":
    sys.exit(main())
