#!/usr/bin/env python3
# AI-hint: Hardware Abstraction Layer & Wasm host imports for GPIO and I2C with allowlist enforcement.
# AI-related: usr/libexec/mios/node/wasm_sandbox.py, src/mios-rs/mios-node/src/device.rs, tests/test-node.py
"""
MiOS Edge Node Hardware Abstraction Layer (HAL) & Wasm Host Import Controller.
Enforces strict allowlist permissions for local hardware GPIO pins and I2C buses.
"""

from __future__ import annotations

import os
from enum import IntEnum
from typing import Dict, List, Optional, Set, Tuple

class HardwareErrorCode(IntEnum):
    SUCCESS = 0
    PERMISSION_DENIED = -1
    DEVICE_NOT_FOUND = -2
    INVALID_PARAMETER = -3
    IO_ERROR = -4
    READ_ONLY_PIN = -5

class HardwareAllowlist:
    """Configures permitted GPIO pins and I2C buses/addresses."""

    def __init__(
        self,
        allowed_gpio_pins: Optional[Set[int]] = None,
        read_only_gpio_pins: Optional[Set[int]] = None,
        allowed_i2c_buses: Optional[Set[int]] = None,
        allowed_i2c_addresses: Optional[Set[int]] = None,
        max_i2c_transfer_len: int = 256,
    ) -> None:
        self.allowed_gpio_pins = allowed_gpio_pins or {4, 17, 27, 22}
        self.read_only_gpio_pins = read_only_gpio_pins or {4}
        self.allowed_i2c_buses = allowed_i2c_buses or {1}
        self.allowed_i2c_addresses = allowed_i2c_addresses or {0x48, 0x68, 0x76, 0x77}
        self.max_i2c_transfer_len = max_i2c_transfer_len

class HardwareDriver:
    """Abstract base hardware driver."""

    def gpio_read(self, pin: int) -> int:
        raise NotImplementedError

    def gpio_write(self, pin: int, value: int) -> None:
        raise NotImplementedError

    def i2c_transfer(
        self, bus: int, addr: int, write_data: bytes, read_len: int
    ) -> bytes:
        raise NotImplementedError

class MockHardwareDriver(HardwareDriver):
    """In-memory Mock Hardware Driver for testing and headless execution."""

    def __init__(self) -> None:
        self.gpio_pins: Dict[int, int] = {}
        self.i2c_registers: Dict[Tuple[int, int, int], int] = {}

    def gpio_read(self, pin: int) -> int:
        return self.gpio_pins.get(pin, 0)

    def gpio_write(self, pin: int, value: int) -> None:
        self.gpio_pins[pin] = 1 if value != 0 else 0

    def i2c_transfer(
        self, bus: int, addr: int, write_data: bytes, read_len: int
    ) -> bytes:
        if write_data:
            start_reg = write_data[0]
            for idx, val in enumerate(write_data[1:]):
                reg = (start_reg + idx) & 0xFF
                self.i2c_registers[(bus, addr, reg)] = val

        read_buf = bytearray()
        if read_len > 0:
            start_reg = write_data[0] if write_data else 0
            for idx in range(read_len):
                reg = (start_reg + idx) & 0xFF
                read_buf.append(self.i2c_registers.get((bus, addr, reg), 0))

        return bytes(read_buf)

class LinuxSysfsHardwareDriver(HardwareDriver):
    """Linux Sysfs / I2C-dev hardware driver."""

    def __init__(
        self, sysfs_gpio_root: str = "/sys/class/gpio", dev_i2c_root: str = "/dev"
    ) -> None:
        self.sysfs_gpio_root = sysfs_gpio_root
        self.dev_i2c_root = dev_i2c_root

    def gpio_read(self, pin: int) -> int:
        path = os.path.join(self.sysfs_gpio_root, f"gpio{pin}", "value")
        if not os.path.exists(path):
            raise FileNotFoundError(f"GPIO pin {pin} not exported")
        with open(path, "r", encoding="utf-8") as f:
            return 1 if f.read().strip() == "1" else 0

    def gpio_write(self, pin: int, value: int) -> None:
        path = os.path.join(self.sysfs_gpio_root, f"gpio{pin}", "value")
        if not os.path.exists(path):
            raise FileNotFoundError(f"GPIO pin {pin} not exported")
        with open(path, "w", encoding="utf-8") as f:
            f.write("1" if value != 0 else "0")

    def i2c_transfer(
        self, bus: int, addr: int, write_data: bytes, read_len: int
    ) -> bytes:
        path = os.path.join(self.dev_i2c_root, f"i2c-{bus}")
        if not os.path.exists(path):
            raise FileNotFoundError(f"I2C bus {bus} not found")
        raise IOError("Direct /dev/i2c ioctl requires Linux root/i2c group")

class SandboxedHardwareController:
    """Enforces hardware allowlist constraints before invoking underlying driver."""

    def __init__(
        self,
        allowlist: Optional[HardwareAllowlist] = None,
        driver: Optional[HardwareDriver] = None,
    ) -> None:
        self.allowlist = allowlist or HardwareAllowlist()
        self.driver = driver or MockHardwareDriver()

    def mios_sys_gpio_read(self, pin: int) -> Tuple[HardwareErrorCode, int]:
        if pin not in self.allowlist.allowed_gpio_pins:
            return (HardwareErrorCode.PERMISSION_DENIED, 0)
        try:
            val = self.driver.gpio_read(pin)
            return (HardwareErrorCode.SUCCESS, val)
        except FileNotFoundError:
            return (HardwareErrorCode.DEVICE_NOT_FOUND, 0)
        except Exception:
            return (HardwareErrorCode.IO_ERROR, 0)

    def mios_sys_gpio_write(self, pin: int, value: int) -> HardwareErrorCode:
        if pin not in self.allowlist.allowed_gpio_pins:
            return HardwareErrorCode.PERMISSION_DENIED
        if pin in self.allowlist.read_only_gpio_pins:
            return HardwareErrorCode.READ_ONLY_PIN
        try:
            self.driver.gpio_write(pin, value)
            return HardwareErrorCode.SUCCESS
        except FileNotFoundError:
            return HardwareErrorCode.DEVICE_NOT_FOUND
        except Exception:
            return HardwareErrorCode.IO_ERROR

    def mios_sys_i2c_transfer(
        self, bus: int, addr: int, write_data: bytes, read_len: int
    ) -> Tuple[HardwareErrorCode, bytes]:
        if bus not in self.allowlist.allowed_i2c_buses:
            return (HardwareErrorCode.PERMISSION_DENIED, b"")
        if addr not in self.allowlist.allowed_i2c_addresses:
            return (HardwareErrorCode.PERMISSION_DENIED, b"")
        if (
            len(write_data) > self.allowlist.max_i2c_transfer_len
            or read_len > self.allowlist.max_i2c_transfer_len
        ):
            return (HardwareErrorCode.INVALID_PARAMETER, b"")
        try:
            res = self.driver.i2c_transfer(bus, addr, write_data, read_len)
            return (HardwareErrorCode.SUCCESS, res)
        except FileNotFoundError:
            return (HardwareErrorCode.DEVICE_NOT_FOUND, b"")
        except Exception:
            return (HardwareErrorCode.IO_ERROR, b"")
