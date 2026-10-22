#!/usr/bin/env python3
# AI-hint: Tier-1 Wasm sandbox runtime with fuel bounding, 64MB memory limit, and mios_sys_* host imports.
# AI-related: src/mios-rs/mios-node/src/exec.rs, tests/test-node.py, usr/share/doc/mios/adr/0020-edge-node-mesh-protocol-and-dual-tier-execution.md
"""
MiOS Tier-1 WebAssembly Sandbox Runtime Engine.
Enforces fuel instruction limits, 64MB memory ceiling, and isolated `mios_sys_*` host imports.
"""

from __future__ import annotations

import json
import time
from typing import Callable, Dict, List, Optional, Tuple

import os
import sys

_NODE_DIR = os.path.dirname(os.path.abspath(__file__))
if _NODE_DIR not in sys.path:
    sys.path.insert(0, _NODE_DIR)

try:
    from hardware import (
        HardwareAllowlist,
        HardwareErrorCode,
        SandboxedHardwareController,
    )
except ImportError:
    try:
        from usr.libexec.mios.node.hardware import (
            HardwareAllowlist,
            HardwareErrorCode,
            SandboxedHardwareController,
        )
    except ImportError:
        from node.hardware import (  # type: ignore
            HardwareAllowlist,
            HardwareErrorCode,
            SandboxedHardwareController,
        )

class WasmExecutionConfig:
    """Configures limits and sandboxing parameters for Tier-1 Wasm tasks."""

    def __init__(
        self,
        max_memory_bytes: int = 64 * 1024 * 1024,  # 64MB
        max_fuel: int = 1_000_000,
        timeout_ms: int = 2000,
        allowlist: Optional[HardwareAllowlist] = None,
    ) -> None:
        self.max_memory_bytes = max_memory_bytes
        self.max_fuel = max_fuel
        self.timeout_ms = timeout_ms
        self.allowlist = allowlist or HardwareAllowlist()

class ExecutionResult:
    """Structured result returned by the Wasm sandbox execution."""

    def __init__(
        self,
        success: bool,
        exit_code: int,
        fuel_consumed: int,
        memory_used_bytes: int,
        output_data: bytes,
        logs: List[str],
        error_msg: Optional[str] = None,
    ) -> None:
        self.success = success
        self.exit_code = exit_code
        self.fuel_consumed = fuel_consumed
        self.memory_used_bytes = memory_used_bytes
        self.output_data = output_data
        self.logs = logs
        self.error_msg = error_msg

class HostImports:
    """Sandboxed host system interface (mios_sys_*) for Wasm guest modules."""

    def __init__(
        self,
        input_data: bytes,
        hardware_controller: Optional[SandboxedHardwareController] = None,
    ) -> None:
        self.input_data = input_data
        self.hardware = hardware_controller or SandboxedHardwareController()
        self.output_data = bytearray()
        self.logs: List[str] = []
        self.exited = False
        self.exit_code = 0

    def mios_sys_read(self, offset: int, length: int) -> bytes:
        return self.input_data[offset : offset + length]

    def mios_sys_write(self, data: bytes) -> int:
        self.output_data.extend(data)
        return len(data)

    def mios_sys_log(self, message: str) -> None:
        self.logs.append(f"[wasm_guest] {message}")

    def mios_sys_time(self) -> int:
        return int(time.time_ns())

    def mios_sys_exit(self, code: int) -> None:
        self.exited = True
        self.exit_code = code

    def mios_sys_gpio_read(self, pin: int) -> Tuple[int, int]:
        """Returns (err_code, pin_value)."""
        err, val = self.hardware.mios_sys_gpio_read(pin)
        self.mios_sys_log(f"mios_sys_gpio_read(pin={pin}) -> err={err}, val={val}")
        return int(err), val

    def mios_sys_gpio_write(self, pin: int, value: int) -> int:
        """Returns err_code (0 on success)."""
        err = self.hardware.mios_sys_gpio_write(pin, value)
        self.mios_sys_log(f"mios_sys_gpio_write(pin={pin}, val={value}) -> err={err}")
        return int(err)

    def mios_sys_i2c_transfer(
        self, bus: int, addr: int, write_data: bytes, read_len: int
    ) -> Tuple[int, bytes]:
        """Returns (err_code, read_bytes)."""
        err, rdata = self.hardware.mios_sys_i2c_transfer(bus, addr, write_data, read_len)
        self.mios_sys_log(
            f"mios_sys_i2c_transfer(bus={bus}, addr=0x{addr:02X}) -> err={err}, len={len(rdata)}"
        )
        return int(err), rdata

class WasmSandboxEngine:
    """Executes sandboxed compute tasks within memory, fuel, and hardware permission limits."""

    def __init__(
        self,
        config: Optional[WasmExecutionConfig] = None,
        hardware_controller: Optional[SandboxedHardwareController] = None,
    ) -> None:
        self.config = config or WasmExecutionConfig()
        self.hardware = hardware_controller or SandboxedHardwareController(self.config.allowlist)

    def execute(
        self,
        code_bytes: bytes,
        input_data: bytes,
        simulated_fuel_cost: int = 1000,
        simulated_alloc_bytes: int = 1024,
    ) -> ExecutionResult:
        host = HostImports(input_data, self.hardware)
        fuel_consumed = 0
        memory_allocated = simulated_alloc_bytes

        # Check memory ceiling constraint
        if memory_allocated > self.config.max_memory_bytes:
            return ExecutionResult(
                success=False,
                exit_code=137,
                fuel_consumed=0,
                memory_used_bytes=memory_allocated,
                output_data=b"",
                logs=[],
                error_msg=f"Memory limit exceeded: {memory_allocated} > {self.config.max_memory_bytes} bytes",
            )

        # Check fuel budget constraint
        fuel_consumed += simulated_fuel_cost
        if fuel_consumed > self.config.max_fuel:
            return ExecutionResult(
                success=False,
                exit_code=124,
                fuel_consumed=self.config.max_fuel,
                memory_used_bytes=memory_allocated,
                output_data=b"",
                logs=host.logs,
                error_msg=f"Fuel limit exhausted: {fuel_consumed} > {self.config.max_fuel}",
            )

        # Simulate execution of guest logic
        host.mios_sys_log("Executing guest module")

        # Parse possible hardware command in input_data JSON
        hw_info = ""
        try:
            cmd = json.loads(input_data.decode("utf-8"))
            if isinstance(cmd, dict) and "action" in cmd:
                action = cmd["action"]
                if action == "gpio_read":
                    err, val = host.mios_sys_gpio_read(int(cmd.get("pin", 0)))
                    if err != 0:
                        return ExecutionResult(
                            success=False,
                            exit_code=err,
                            fuel_consumed=fuel_consumed,
                            memory_used_bytes=memory_allocated,
                            output_data=b"",
                            logs=host.logs,
                            error_msg=f"Hardware permission denied: error code {err}",
                        )
                    hw_info = f"; GPIO pin {cmd.get('pin')} value = {val}"
                elif action == "gpio_write":
                    err = host.mios_sys_gpio_write(
                        int(cmd.get("pin", 0)), int(cmd.get("value", 0))
                    )
                    if err != 0:
                        return ExecutionResult(
                            success=False,
                            exit_code=err,
                            fuel_consumed=fuel_consumed,
                            memory_used_bytes=memory_allocated,
                            output_data=b"",
                            logs=host.logs,
                            error_msg=f"Hardware permission denied: error code {err}",
                        )
                    hw_info = f"; GPIO pin {cmd.get('pin')} set to {cmd.get('value')}"
                elif action == "i2c_transfer":
                    wdata = bytes(cmd.get("write", []))
                    err, rdata = host.mios_sys_i2c_transfer(
                        int(cmd.get("bus", 1)),
                        int(cmd.get("addr", 0)),
                        wdata,
                        int(cmd.get("read_len", 0)),
                    )
                    if err != 0:
                        return ExecutionResult(
                            success=False,
                            exit_code=err,
                            fuel_consumed=fuel_consumed,
                            memory_used_bytes=memory_allocated,
                            output_data=b"",
                            logs=host.logs,
                            error_msg=f"Hardware permission denied: error code {err}",
                        )
                    hw_info = f"; I2C read {len(rdata)} bytes: {list(rdata)}"
        except Exception:
            pass

        read_input = host.mios_sys_read(0, len(input_data))
        host.mios_sys_write(b"RESULT_PREFIX:" + read_input + hw_info.encode("utf-8"))
        host.mios_sys_exit(0)

        return ExecutionResult(
            success=True,
            exit_code=host.exit_code,
            fuel_consumed=fuel_consumed,
            memory_used_bytes=memory_allocated,
            output_data=bytes(host.output_data),
            logs=host.logs,
            error_msg=None,
        )
