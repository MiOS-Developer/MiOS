#!/usr/bin/env python3
# AI-hint: Hardware watchdog timer integration (/dev/watchdog) with safe 'V' magic close.
# AI-related: src/mios-rs/mios-node/src/device.rs, tests/test-node.py
"""
MiOS Hardware Watchdog Controller & Supervisor.
Manages `/dev/watchdog` hardware timer keepalives and safe magic close ('V') shutdown.
"""

from __future__ import annotations

import os
import time
from typing import Optional

class WatchdogConfig:
    """Watchdog timer settings and keepalive interval."""

    def __init__(
        self,
        enabled: bool = True,
        device_path: str = "/dev/watchdog",
        timeout_secs: int = 30,
        ping_interval_secs: int = 5,
        use_systemd_notify: bool = True,
    ) -> None:
        self.enabled = enabled
        self.device_path = device_path
        self.timeout_secs = timeout_secs
        self.ping_interval_secs = ping_interval_secs
        self.use_systemd_notify = use_systemd_notify

class WatchdogDriver:
    """Abstract base watchdog driver."""

    def arm(self) -> None:
        raise NotImplementedError

    def ping(self) -> None:
        raise NotImplementedError

    def set_timeout(self, timeout_secs: int) -> int:
        raise NotImplementedError

    def get_timeout(self) -> int:
        raise NotImplementedError

    def disarm_and_close(self) -> None:
        raise NotImplementedError

    def is_hardware_present(self) -> bool:
        raise NotImplementedError

    def is_armed(self) -> bool:
        raise NotImplementedError

class LinuxHardwareWatchdog(WatchdogDriver):
    """Linux `/dev/watchdog` driver with 'V' safe close."""

    def __init__(
        self, device_path: str = "/dev/watchdog", timeout_secs: int = 30
    ) -> None:
        self.device_path = device_path
        self.timeout_secs = timeout_secs
        self.file_fd: Optional[int] = None
        self.is_present = os.path.exists(device_path)

    def arm(self) -> None:
        if not self.is_present:
            raise FileNotFoundError(f"Watchdog device {self.device_path} not found")
        if self.file_fd is None:
            self.file_fd = os.open(self.device_path, os.O_WRONLY)

    def ping(self) -> None:
        if self.file_fd is not None:
            os.write(self.file_fd, b"\0")
        else:
            raise RuntimeError("Watchdog not armed")

    def set_timeout(self, timeout_secs: int) -> int:
        self.timeout_secs = timeout_secs
        return self.timeout_secs

    def get_timeout(self) -> int:
        return self.timeout_secs

    def disarm_and_close(self) -> None:
        if self.file_fd is not None:
            try:
                # Strict Invariant: write 'V' magic char to disarm before close
                os.write(self.file_fd, b"V")
            except Exception:
                pass
            os.close(self.file_fd)
            self.file_fd = None

    def is_hardware_present(self) -> bool:
        return self.is_present

    def is_armed(self) -> bool:
        return self.file_fd is not None

class MockWatchdogDriver(WatchdogDriver):
    """In-memory mock watchdog driver for headless test environments."""

    def __init__(
        self, simulated_present: bool = True, timeout_secs: int = 30
    ) -> None:
        self.simulated_present = simulated_present
        self.timeout_secs = timeout_secs
        self.armed = False
        self.ping_count = 0
        self.last_ping: Optional[float] = None
        self.disarmed_safely = False

    def arm(self) -> None:
        if not self.simulated_present:
            raise RuntimeError("Hardware watchdog absent")
        self.armed = True
        self.disarmed_safely = False
        self.last_ping = time.time()

    def ping(self) -> None:
        if not self.armed:
            raise RuntimeError("Cannot ping disarmed watchdog")
        self.ping_count += 1
        self.last_ping = time.time()

    def set_timeout(self, timeout_secs: int) -> int:
        self.timeout_secs = timeout_secs
        return self.timeout_secs

    def get_timeout(self) -> int:
        return self.timeout_secs

    def disarm_and_close(self) -> None:
        if self.armed:
            self.armed = False
            self.disarmed_safely = True

    def is_hardware_present(self) -> bool:
        return self.simulated_present

    def is_armed(self) -> bool:
        return self.armed

class WatchdogSupervisor:
    """Supervises watchdog arming, keepalive loop, and graceful termination."""

    def __init__(
        self,
        config: Optional[WatchdogConfig] = None,
        driver: Optional[WatchdogDriver] = None,
    ) -> None:
        self.config = config or WatchdogConfig()
        self.driver = driver or MockWatchdogDriver(
            simulated_present=True, timeout_secs=self.config.timeout_secs
        )

    def arm(self) -> bool:
        if not self.config.enabled:
            return False
        try:
            self.driver.arm()
            return True
        except Exception:
            return False

    def ping(self) -> bool:
        if not self.is_armed():
            return False
        try:
            self.driver.ping()
            return True
        except Exception:
            return False

    def disarm(self) -> bool:
        try:
            self.driver.disarm_and_close()
            return True
        except Exception:
            return False

    def is_armed(self) -> bool:
        return self.driver.is_armed()

    def is_present(self) -> bool:
        return self.driver.is_hardware_present()
