#!/usr/bin/env python3
# AI-hint: GPU thermal, junction temperature, and clock frequency watchdog for MiOS.
# AI-related: usr/libexec/mios/hw/gpu_thermal_watchdog.py, tests/test-hw.py
"""GPU thermal, junction temperature, and clock frequency watchdog for MiOS.

Monitors discrete GPU junction/hotspot temperatures via DRM/hwmon and NVML,
calculating dynamic fan curves to maintain junction temperatures under 80°C.

Architectural Invariant:
Do NOT set fan speeds to 0% under any operational thermal condition.
"""

from __future__ import annotations

import argparse
import glob
import json
import logging
import math
import os
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("mios-gpu-thermal-watchdog")

DEFAULT_TARGET_JUNCTION_TEMP_C = 80.0
DEFAULT_MIN_FAN_FLOOR_PERCENT = 25.0
DEFAULT_POLL_INTERVAL_SECONDS = 5.0

class GPUTelemetry:
    """Holds structured telemetry readings from a single GPU device."""

    def __init__(
        self,
        card_id: str,
        vendor: str,
        device_name: str,
        junction_temp_c: Optional[float] = None,
        edge_temp_c: Optional[float] = None,
        memory_temp_c: Optional[float] = None,
        current_fan_percent: Optional[float] = None,
        current_fan_rpm: Optional[int] = None,
        clock_mhz: Optional[int] = None,
        pwm_path: Optional[str] = None,
        pwm_enable_path: Optional[str] = None,
    ) -> None:
        self.card_id = card_id
        self.vendor = vendor
        self.device_name = device_name
        self.junction_temp_c = junction_temp_c
        self.edge_temp_c = edge_temp_c
        self.memory_temp_c = memory_temp_c
        self.current_fan_percent = current_fan_percent
        self.current_fan_rpm = current_fan_rpm
        self.clock_mhz = clock_mhz
        self.pwm_path = pwm_path
        self.pwm_enable_path = pwm_enable_path

    @property
    def peak_temperature_c(self) -> float:
        """Return the highest measured temperature across junction, edge, and memory."""
        temps = [t for t in (self.junction_temp_c, self.edge_temp_c, self.memory_temp_c) if t is not None]
        return max(temps) if temps else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "card_id": self.card_id,
            "vendor": self.vendor,
            "device_name": self.device_name,
            "junction_temp_c": self.junction_temp_c,
            "edge_temp_c": self.edge_temp_c,
            "memory_temp_c": self.memory_temp_c,
            "peak_temp_c": self.peak_temperature_c,
            "current_fan_percent": self.current_fan_percent,
            "current_fan_rpm": self.current_fan_rpm,
            "clock_mhz": self.clock_mhz,
            "pwm_path": self.pwm_path,
        }

class GPUThermalWatchdog:
    """Monitors GPU thermals and manages dynamic fan curves with strict floor enforcement."""

    def __init__(
        self,
        sysfs_root: str = "/",
        target_junction_temp_c: float = DEFAULT_TARGET_JUNCTION_TEMP_C,
        min_fan_floor_percent: float = DEFAULT_MIN_FAN_FLOOR_PERCENT,
        dry_run: bool = False,
    ) -> None:
        self.sysfs_root = os.path.abspath(sysfs_root)
        self.target_junction_temp_c = target_junction_temp_c
        self.dry_run = dry_run

        # Enforce non-zero fan floor invariant
        self.min_fan_floor_percent = self.enforce_fan_floor_invariant(min_fan_floor_percent)

    @staticmethod
    def enforce_fan_floor_invariant(floor_percent: float) -> float:
        """Enforces that the minimum fan speed is strictly greater than 0% under all conditions."""
        import math
        if math.isnan(floor_percent) or floor_percent <= 0.0:
            logger.warning(
                "Requested fan floor %s violates architectural non-zero invariant. Enforcing minimum %.1f%%.",
                floor_percent,
                DEFAULT_MIN_FAN_FLOOR_PERCENT,
            )
            return DEFAULT_MIN_FAN_FLOOR_PERCENT
        return max(floor_percent, 10.0)

    def _read_file_safe(self, path: str) -> Optional[str]:
        """Safely read content from a file."""
        if not os.path.isfile(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read().strip()
        except OSError as e:
            logger.debug("Failed reading %s: %s", path, e)
            return None

    def _write_file_safe(self, path: str, content: str) -> bool:
        """Safely write string to a file."""
        if self.dry_run:
            logger.info("[DRY-RUN] Write '%s' -> %s", content, path)
            return True
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            return True
        except OSError as e:
            logger.warning("Failed writing '%s' to %s: %s", content, path, e)
            return False

    def scan_hwmon_gpus(self) -> List[GPUTelemetry]:
        """Scan DRM hwmon sysfs paths for discrete and integrated GPUs."""
        telemetry_list: List[GPUTelemetry] = []

        # Look in /sys/class/drm/card*/device/hwmon/hwmon* and /sys/class/hwmon/hwmon*
        hwmon_patterns = [
            os.path.join(self.sysfs_root, "sys", "class", "drm", "card*", "device", "hwmon", "hwmon*"),
            os.path.join(self.sysfs_root, "sys", "class", "hwmon", "hwmon*"),
        ]

        found_dirs = set()
        for pat in hwmon_patterns:
            for d in glob.glob(pat):
                found_dirs.add(os.path.abspath(d))

        for hdir in sorted(found_dirs):
            hname = self._read_file_safe(os.path.join(hdir, "name")) or os.path.basename(hdir)
            # Filter non-GPU hwmon if name indicates CPU or motherboard (e.g. coretemp, k10temp, acpitz)
            if hname.lower() in ("coretemp", "k10temp", "acpitz", "zenpower", "nct6775", "it8688"):
                continue

            junction_temp: Optional[float] = None
            edge_temp: Optional[float] = None
            mem_temp: Optional[float] = None
            highest_raw_temp: Optional[float] = None

            # Read temp sensors temp*_input / temp*_label
            temp_inputs = glob.glob(os.path.join(hdir, "temp*_input"))
            for t_in in temp_inputs:
                raw_val = self._read_file_safe(t_in)
                if not raw_val or not raw_val.lstrip("-").isdigit():
                    continue
                deg_c = round(int(raw_val) / 1000.0, 1)

                prefix = os.path.basename(t_in)[:-6]  # e.g. "temp1"
                label_file = os.path.join(hdir, f"{prefix}_label")
                label = (self._read_file_safe(label_file) or "").lower()

                if "junction" in label or "hotspot" in label:
                    junction_temp = deg_c
                elif "edge" in label or "gpu" in label:
                    edge_temp = deg_c
                elif "mem" in label:
                    mem_temp = deg_c

                if highest_raw_temp is None or deg_c > highest_raw_temp:
                    highest_raw_temp = deg_c

            if junction_temp is None and highest_raw_temp is not None:
                # If no explicit junction label, assign highest reading to junction/hotspot
                junction_temp = highest_raw_temp

            # Read fans and PWM
            fan_rpm: Optional[int] = None
            fan_inputs = glob.glob(os.path.join(hdir, "fan*_input"))
            if fan_inputs:
                raw_rpm = self._read_file_safe(fan_inputs[0])
                if raw_rpm and raw_rpm.isdigit():
                    fan_rpm = int(raw_rpm)

            pwm_path: Optional[str] = None
            pwm_enable_path: Optional[str] = None
            cur_fan_pct: Optional[float] = None

            pwms = glob.glob(os.path.join(hdir, "pwm*"))
            pwm_files = [p for p in pwms if not p.endswith("_enable") and not p.endswith("_mode")]
            if pwm_files:
                pwm_path = pwm_files[0]
                raw_pwm = self._read_file_safe(pwm_path)
                if raw_pwm and raw_pwm.isdigit():
                    cur_fan_pct = round((int(raw_pwm) / 255.0) * 100.0, 1)

                enable_candidate = f"{pwm_path}_enable"
                if os.path.isfile(enable_candidate):
                    pwm_enable_path = enable_candidate

            # Clock frequency
            clock_mhz: Optional[int] = None
            clk_file = os.path.join(hdir, "device", "gt_cur_freq_mhz")
            if not os.path.isfile(clk_file):
                clk_file = os.path.join(hdir, "device", "pp_dpm_sclk")
            if os.path.isfile(clk_file):
                raw_clk = self._read_file_safe(clk_file)
                if raw_clk and raw_clk.isdigit():
                    clock_mhz = int(raw_clk)

            card_id = os.path.basename(hdir)
            gt = GPUTelemetry(
                card_id=card_id,
                vendor=hname,
                device_name=hname,
                junction_temp_c=junction_temp,
                edge_temp_c=edge_temp,
                memory_temp_c=mem_temp,
                current_fan_percent=cur_fan_pct,
                current_fan_rpm=fan_rpm,
                clock_mhz=clock_mhz,
                pwm_path=pwm_path,
                pwm_enable_path=pwm_enable_path,
            )
            telemetry_list.append(gt)

        return telemetry_list

    def scan_nvidia_smi_gpus(self) -> List[GPUTelemetry]:
        """Query NVIDIA discrete GPUs via nvidia-smi CLI."""
        telemetry_list: List[GPUTelemetry] = []
        try:
            cmd = [
                "nvidia-smi",
                "--query-gpu=index,name,temperature.gpu,fan.speed,clocks.current.graphics",
                "--format=csv,noheader,nounits",
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if res.returncode == 0 and res.stdout.strip():
                for line in res.stdout.strip().splitlines():
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) >= 5:
                        idx, name, temp_gpu, fan_spd, clk = parts[0], parts[1], parts[2], parts[3], parts[4]
                        gt = GPUTelemetry(
                            card_id=f"nvidia_{idx}",
                            vendor="NVIDIA",
                            device_name=name,
                            junction_temp_c=float(temp_gpu) if temp_gpu.replace(".", "").isdigit() else None,
                            edge_temp_c=float(temp_gpu) if temp_gpu.replace(".", "").isdigit() else None,
                            current_fan_percent=float(fan_spd) if fan_spd.replace(".", "").isdigit() else None,
                            clock_mhz=int(clk) if clk.isdigit() else None,
                        )
                        telemetry_list.append(gt)
        except (subprocess.SubprocessError, FileNotFoundError, OSError):
            pass

        return telemetry_list

    def get_all_gpus(self) -> List[GPUTelemetry]:
        """Aggregate telemetry across sysfs hwmon and nvidia-smi."""
        gpus = self.scan_hwmon_gpus()
        if not gpus:
            gpus = self.scan_nvidia_smi_gpus()
        return gpus

    def calculate_fan_curve_duty_cycle(self, temp_c: float) -> float:
        """Calculate target fan duty cycle percentage based on temperature.

        Enforces:
        - Fan floor invariant: duty cycle >= self.min_fan_floor_percent (NEVER 0%).
        - Target ceiling: 100% when temp >= target junction temp (80.0°C).
        """
        # Step 1: Base Piecewise Linear Function
        #  <= 40°C -> min_fan_floor_percent
        #  40°C - 65°C -> scale from min_fan_floor_percent to 60%
        #  65°C - 80°C -> scale from 60% to 100%
        #  >= 80°C -> 100%
        floor = self.min_fan_floor_percent

        if temp_c <= 40.0:
            target_pct = floor
        elif temp_c < 65.0:
            ratio = (temp_c - 40.0) / (65.0 - 40.0)
            target_pct = floor + ratio * (60.0 - floor)
        elif temp_c < self.target_junction_temp_c:
            ratio = (temp_c - 65.0) / (self.target_junction_temp_c - 65.0)
            target_pct = 60.0 + ratio * (100.0 - 60.0)
        else:
            target_pct = 100.0

        # Step 2: Strict invariant clamping: MUST be between min floor and 100%
        final_pct = max(floor, min(100.0, target_pct))
        return round(final_pct, 1)

    def apply_fan_speed_pwm(self, gpu: GPUTelemetry, target_percent: float) -> bool:
        """Write computed fan speed PWM to sysfs if writable."""
        # Enforce non-zero floor invariant before any write
        clamped_percent = max(self.min_fan_floor_percent, min(100.0, target_percent))
        pwm_val = int(round((clamped_percent / 100.0) * 255.0))
        pwm_val = max(1, min(255, pwm_val))  # Never 0

        if not gpu.pwm_path:
            logger.debug("GPU %s has no direct PWM control path", gpu.card_id)
            return False

        # Set PWM enable to manual (1) if supported
        if gpu.pwm_enable_path:
            self._write_file_safe(gpu.pwm_enable_path, "1")

        ok = self._write_file_safe(gpu.pwm_path, str(pwm_val))
        if ok:
            logger.info("GPU %s fan speed set to %.1f%% (PWM=%d)", gpu.card_id, clamped_percent, pwm_val)
        return ok

    def check_and_adjust_all(self) -> Dict[str, Any]:
        """Perform a single watchdog evaluation and fan curve adjustment pass."""
        gpus = self.get_all_gpus()
        results: List[Dict[str, Any]] = []
        throttling_warnings: List[str] = []

        for gpu in gpus:
            peak_temp = gpu.peak_temperature_c
            target_fan_pct = self.calculate_fan_curve_duty_cycle(peak_temp)
            adjusted = self.apply_fan_speed_pwm(gpu, target_fan_pct)

            is_throttling = peak_temp >= self.target_junction_temp_c
            if is_throttling:
                throttling_warnings.append(
                    f"GPU {gpu.card_id} peak temp ({peak_temp}°C) exceeds target threshold ({self.target_junction_temp_c}°C)!"
                )

            gpu_dict = gpu.to_dict()
            gpu_dict["target_fan_percent"] = target_fan_pct
            gpu_dict["fan_adjusted"] = adjusted
            gpu_dict["thermal_throttling_risk"] = is_throttling
            results.append(gpu_dict)

        return {
            "timestamp": time.time(),
            "target_junction_temp_c": self.target_junction_temp_c,
            "min_fan_floor_percent": self.min_fan_floor_percent,
            "gpus_monitored": len(gpus),
            "gpus": results,
            "throttling_warnings": throttling_warnings,
            "status": "warning" if throttling_warnings else "ok",
        }

    def run_daemon(self, poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS, max_cycles: Optional[int] = None) -> None:
        """Run continuous thermal watchdog loop."""
        logger.info(
            "Starting MiOS GPU thermal watchdog daemon (target temp: <%.1f°C, min fan floor: %.1f%%, poll: %.1fs)",
            self.target_junction_temp_c,
            self.min_fan_floor_percent,
            poll_interval,
        )
        cycles = 0
        try:
            while True:
                res = self.check_and_adjust_all()
                for g in res["gpus"]:
                    logger.info(
                        "GPU [%s]: Temp=%.1f°C (Junction: %s°C) -> Fan: %.1f%% (Current: %s%%)",
                        g["card_id"],
                        g["peak_temp_c"],
                        g["junction_temp_c"],
                        g["target_fan_percent"],
                        g["current_fan_percent"],
                    )
                cycles += 1
                if max_cycles is not None and cycles >= max_cycles:
                    break
                time.sleep(poll_interval)
        except KeyboardInterrupt:
            logger.info("MiOS GPU thermal watchdog stopped by operator.")

def main() -> int:
    parser = argparse.ArgumentParser(
        description="MiOS GPU Thermal, Junction Temperature & Fan Watchdog (task_368)"
    )
    parser.add_argument(
        "--action",
        choices=["check", "adjust", "daemon", "fan-curve", "status"],
        default="status",
        help="Action to perform",
    )
    parser.add_argument(
        "--target-temp",
        type=float,
        default=DEFAULT_TARGET_JUNCTION_TEMP_C,
        help="Target junction temperature ceiling in degrees C (default 80.0)",
    )
    parser.add_argument(
        "--min-fan-floor",
        type=float,
        default=DEFAULT_MIN_FAN_FLOOR_PERCENT,
        help="Minimum fan speed floor percentage (> 0%% enforced)",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=DEFAULT_POLL_INTERVAL_SECONDS,
        help="Watchdog polling interval in seconds",
    )
    parser.add_argument("--sysfs-root", default="/", help="Root directory for sysfs mocks/testing")
    parser.add_argument("--dry-run", action="store_true", help="Simulate PWM writes")
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON")

    args = parser.parse_args()

    watchdog = GPUThermalWatchdog(
        sysfs_root=args.sysfs_root,
        target_junction_temp_c=args.target_temp,
        min_fan_floor_percent=args.min_fan_floor,
        dry_run=args.dry_run,
    )

    if args.action == "fan-curve":
        # Print sample fan curve table
        test_temps = [25.0, 35.0, 45.0, 55.0, 65.0, 70.0, 75.0, 80.0, 85.0, 90.0]
        curve_data = [{"temp_c": t, "fan_duty_pct": watchdog.calculate_fan_curve_duty_cycle(t)} for t in test_temps]
        if args.json:
            print(json.dumps(curve_data, indent=2))
        else:
            print("=== Dynamic GPU Fan Curve (Min Floor: %.1f%%, Target: %.1f°C) ===" % (watchdog.min_fan_floor_percent, watchdog.target_junction_temp_c))
            for pt in curve_data:
                print(f"  {pt['temp_c']:5.1f}°C  ->  {pt['fan_duty_pct']:5.1f}% duty cycle")
    elif args.action in ("status", "check", "adjust"):
        res = watchdog.check_and_adjust_all()
        if args.json:
            print(json.dumps(res, indent=2))
        else:
            print("=== MiOS GPU Thermal Watchdog Status ===")
            print(f"  Monitored GPUs: {res['gpus_monitored']}")
            for g in res["gpus"]:
                print(f"  [{g['card_id']}] {g['device_name']}")
                print(f"      Junction Temp: {g['junction_temp_c']}°C  Edge: {g['edge_temp_c']}°C")
                print(f"      Target Fan:    {g['target_fan_percent']}% (Adjusted: {g['fan_adjusted']})")
                print(f"      Clock:         {g['clock_mhz']} MHz")
            if res["throttling_warnings"]:
                print("  WARNINGS:")
                for w in res["throttling_warnings"]:
                    print(f"    ! {w}")
    elif args.action == "daemon":
        watchdog.run_daemon(poll_interval=args.poll_interval)

    return 0

if __name__ == "__main__":
    sys.exit(main())
