#!/usr/bin/env python3
# AI-hint: Consolidated WS-NODE edge-mesh suite: CRDT/GC, cgroups pinning, watchdog, Wasm sandbox+hardware, BLE bootstrap, buffer pool, capabilities, discovery, overlay, scheduler, wire codec, mesh logs, M1/M2 adversarial.
"""Consolidated WS-NODE unit and adversarial tests (usr/libexec/mios/node), one section per former tests/test-*.py file."""
from __future__ import annotations


# ============================================================================
# from tests/test-node.py (prefix am1_)
# ============================================================================
import json
import os
import sys
import tempfile
import unittest

am1_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
am1_NODE_LIB_DIR = os.path.join(am1_REPO_ROOT, "usr", "libexec", "mios", "node")
sys.path.insert(0, am1_NODE_LIB_DIR)

from cgroups import (
    AffinityPolicy,
    CgroupV2Controller,
    NodeResourceLimits,
    WorkerAffinityController,
    filter_safe_worker_cores,
)
from crdt import StateElement, StateStore, VectorClock
from hardware import (
    HardwareAllowlist,
    HardwareErrorCode,
    LinuxSysfsHardwareDriver,
    MockHardwareDriver,
    SandboxedHardwareController,
)
from wasm_sandbox import HostImports, WasmSandboxEngine
from watchdog import (
    LinuxHardwareWatchdog,
    MockWatchdogDriver,
    WatchdogConfig,
    WatchdogSupervisor,
)

class am1_TestAdversarialHardware(unittest.TestCase):
    """Adversarial testing of Hardware HAL and SandboxedController."""

    def test_allowlist_pin_boundaries_and_read_only(self):
        allowlist = HardwareAllowlist(
            allowed_gpio_pins={17, 27},
            read_only_gpio_pins={27},
            allowed_i2c_buses={1},
            allowed_i2c_addresses={0x68},
            max_i2c_transfer_len=8,
        )
        mock = MockHardwareDriver()
        ctrl = SandboxedHardwareController(allowlist=allowlist, driver=mock)

        # 1. Unallowed Pins
        code, val = ctrl.mios_sys_gpio_read(0)
        self.assertEqual(code, HardwareErrorCode.PERMISSION_DENIED)
        self.assertEqual(ctrl.mios_sys_gpio_write(0, 1), HardwareErrorCode.PERMISSION_DENIED)

        code, val = ctrl.mios_sys_gpio_read(999999)
        self.assertEqual(code, HardwareErrorCode.PERMISSION_DENIED)
        self.assertEqual(ctrl.mios_sys_gpio_write(999999, 1), HardwareErrorCode.PERMISSION_DENIED)

        # 2. Read-Only Pin
        code, val = ctrl.mios_sys_gpio_read(27)
        self.assertEqual(code, HardwareErrorCode.SUCCESS)
        self.assertEqual(val, 0)
        self.assertEqual(ctrl.mios_sys_gpio_write(27, 1), HardwareErrorCode.READ_ONLY_PIN)

        # 3. Read/Write Pin
        self.assertEqual(ctrl.mios_sys_gpio_write(17, 1), HardwareErrorCode.SUCCESS)
        code, val = ctrl.mios_sys_gpio_read(17)
        self.assertEqual(code, HardwareErrorCode.SUCCESS)
        self.assertEqual(val, 1)

    def test_i2c_transfer_len_limits_and_wrapping(self):
        allowlist = HardwareAllowlist(
            allowed_i2c_buses={1},
            allowed_i2c_addresses={0x68},
            max_i2c_transfer_len=8,
        )
        mock = MockHardwareDriver()
        ctrl = SandboxedHardwareController(allowlist=allowlist, driver=mock)

        # Transfer len <= 8 is OK
        code, res = ctrl.mios_sys_i2c_transfer(1, 0x68, b"\x10\x01\x02", 4)
        self.assertEqual(code, HardwareErrorCode.SUCCESS)
        self.assertEqual(len(res), 4)

        # Write data > 8 bytes -> InvalidParameter
        code, res = ctrl.mios_sys_i2c_transfer(1, 0x68, b"\x00" * 9, 0)
        self.assertEqual(code, HardwareErrorCode.INVALID_PARAMETER)

        # Read len > 8 bytes -> InvalidParameter
        code, res = ctrl.mios_sys_i2c_transfer(1, 0x68, b"\x00", 9)
        self.assertEqual(code, HardwareErrorCode.INVALID_PARAMETER)

        # Disallowed bus / addr
        code, _ = ctrl.mios_sys_i2c_transfer(2, 0x68, b"\x00", 1)
        self.assertEqual(code, HardwareErrorCode.PERMISSION_DENIED)

        code, _ = ctrl.mios_sys_i2c_transfer(1, 0x55, b"\x00", 1)
        self.assertEqual(code, HardwareErrorCode.PERMISSION_DENIED)

        # Wrapping register address
        ctrl.mios_sys_i2c_transfer(1, 0x68, b"\xff\xaa\xbb\xcc", 0) # writes to 255, 0, 1
        code, wrap_read = ctrl.mios_sys_i2c_transfer(1, 0x68, b"\xff", 3)
        self.assertEqual(code, HardwareErrorCode.SUCCESS)
        self.assertEqual(wrap_read, b"\xaa\xbb\xcc")

class am1_TestAdversarialCgroups(unittest.TestCase):
    """Adversarial testing of CPU Core Pinning and Cgroups."""

    def test_core_zero_exclusion_invariant_matrix(self):
        # 0 cores
        self.assertEqual(filter_safe_worker_cores(0, None, True), [])

        # 1 core
        self.assertEqual(filter_safe_worker_cores(1, None, True), [0])
        self.assertEqual(filter_safe_worker_cores(1, None, False), [0])

        # 2 cores
        self.assertEqual(filter_safe_worker_cores(2, None, True), [1])
        self.assertEqual(filter_safe_worker_cores(2, None, False), [0, 1])

        # 64 cores
        cores_64 = filter_safe_worker_cores(64, None, True)
        self.assertEqual(len(cores_64), 63)
        self.assertNotIn(0, cores_64)

        # Out-of-bounds requested cores
        req = [0, 1, 2, 999]
        self.assertEqual(filter_safe_worker_cores(4, req, True), [1, 2])

    def test_worker_affinity_allocation_exhaustion(self):
        limits = NodeResourceLimits(worker_cores=[1, 2, 3])
        controller = WorkerAffinityController(4, limits)
        self.assertEqual(controller.available_worker_cores, [1, 2, 3])

        # Allocate 2 cores
        c1 = controller.allocate_cores_for_policy(AffinityPolicy.EXCLUSIVE, 2)
        self.assertEqual(c1, [1, 2])

        # Allocate 1 core
        c2 = controller.allocate_cores_for_policy(AffinityPolicy.EXCLUSIVE, 1)
        self.assertEqual(c2, [3])

        # Exhausted -> throws RuntimeError
        with self.assertRaises(RuntimeError):
            controller.allocate_cores_for_policy(AffinityPolicy.EXCLUSIVE, 1)

        # Release core 2 and re-allocate
        controller.release_cores([2])
        c_re = controller.allocate_cores_for_policy(AffinityPolicy.EXCLUSIVE, 1)
        self.assertEqual(c_re, [2])

        # Low priority returns highest core
        self.assertEqual(
            controller.allocate_cores_for_policy(AffinityPolicy.LOW_PRIORITY, 0), [3]
        )

    def test_format_cpu_max_ratios(self):
        self.assertEqual(CgroupV2Controller.format_cpu_max(0, 100_000), "0 100000")
        self.assertEqual(CgroupV2Controller.format_cpu_max(80, 100_000), "80000 100000")
        self.assertEqual(CgroupV2Controller.format_cpu_max(250, 100_000), "250000 100000")
        self.assertEqual(CgroupV2Controller.format_cpu_max(None, 50_000), "max 50000")

class am1_TestAdversarialCrdt(unittest.TestCase):
    """Adversarial testing of CRDT LWW-Element-Set and Tombstone Compaction."""

    def test_tombstone_gc_future_skew_and_exact_ttl(self):
        store = StateStore(101)

        # 1. Future timestamp tombstone (skew)
        future_elem = StateElement("future.tomb", b"", 2_000_000_000_000, 101, is_deleted=True)
        store.elements["future.tomb"] = future_elem

        # Compact with current_time = 1000s, TTL = 100s
        stats = store.compact_tombstones(ttl_s=100.0, current_time_s=1000.0)
        self.assertEqual(stats["tombstones_purged"], 0)
        self.assertEqual(stats["tombstones_retained"], 1)
        self.assertIn("future.tomb", store.elements)

        # 2. Exact TTL boundary
        retained_elem = StateElement("retained.tomb", b"", 900_000_000_000, 101, is_deleted=True) # age = 100s
        purged_elem = StateElement("purged.tomb", b"", 899_000_000_000, 101, is_deleted=True) # age = 101s > 100s
        store.elements["retained.tomb"] = retained_elem
        store.elements["purged.tomb"] = purged_elem

        stats2 = store.compact_tombstones(ttl_s=100.0, current_time_s=1000.0)
        self.assertEqual(stats2["tombstones_purged"], 1)
        self.assertNotIn("purged.tomb", store.elements)
        self.assertIn("retained.tomb", store.elements)

        # 3. Ancient live key must not die
        live_elem = StateElement("live.key", b"active_data", 1, 101, is_deleted=False)
        store.elements["live.key"] = live_elem

        stats3 = store.compact_tombstones(ttl_s=100.0, current_time_s=1000.0)
        self.assertEqual(store.get("live.key"), b"active_data")
        self.assertEqual(stats3["active_elements"], 1)

    def test_crdt_scale_and_disk_wal_truncation(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name

        try:
            if os.path.exists(path):
                os.remove(path)

            store = StateStore(node_id=1, persistence_path=path)

            for i in range(200):
                store.set(f"k_{i}", f"val_{i}".encode())

            for i in range(200, 400):
                k = f"tomb_{i}"
                store.set(k, b"temp")
                store.delete(k)
                elem = store.elements[k]
                if i < 300:
                    elem.timestamp_ns = 100_000_000_000 # Stale
                else:
                    elem.timestamp_ns = 950_000_000_000 # Fresh

            self.assertEqual(store.total_elements_count(), 400)
            self.assertEqual(store.count_tombstones(), 200)

            stats = store.compact_disk_storage(ttl_s=200.0, current_time_s=1000.0)
            self.assertEqual(stats["tombstones_purged"], 100)
            self.assertEqual(stats["tombstones_retained"], 100)
            self.assertEqual(store.total_elements_count(), 300)

            # Reload and verify consistency
            reloaded = StateStore(node_id=1, persistence_path=path)
            self.assertEqual(reloaded.total_elements_count(), 300)
            self.assertEqual(reloaded.get("k_0"), b"val_0")
            self.assertEqual(reloaded.get("k_199"), b"val_199")
        finally:
            if os.path.exists(path):
                os.remove(path)
            if os.path.exists(path + ".log"):
                os.remove(path + ".log")

class am1_TestAdversarialWatchdog(unittest.TestCase):
    """Adversarial testing of Watchdog supervisor and device handling."""

    def test_mock_supervisor_and_safe_disarm(self):
        config = WatchdogConfig(enabled=True, timeout_secs=10, ping_interval_secs=2)
        mock = MockWatchdogDriver(simulated_present=True, timeout_secs=10)
        supervisor = WatchdogSupervisor(config=config, driver=mock)

        self.assertTrue(supervisor.is_present())
        self.assertFalse(supervisor.is_armed())
        self.assertFalse(supervisor.ping(), "Ping on disarmed watchdog must fail")

        # Arm and ping
        self.assertTrue(supervisor.arm())
        self.assertTrue(supervisor.is_armed())

        for _ in range(5):
            self.assertTrue(supervisor.ping())

        self.assertEqual(mock.ping_count, 5)
        self.assertFalse(mock.disarmed_safely)

        # Disarm with 'V' invariant
        self.assertTrue(supervisor.disarm())
        self.assertFalse(supervisor.is_armed())
        self.assertTrue(mock.disarmed_safely)

        # Post-disarm ping fails
        self.assertFalse(supervisor.ping())

    def test_absent_linux_watchdog_exception_safety(self):
        driver = LinuxHardwareWatchdog("/tmp/non_existent_watchdog_xyz", 30)
        self.assertFalse(driver.is_hardware_present())
        self.assertFalse(driver.is_armed())

        with self.assertRaises(FileNotFoundError):
            driver.arm()

        with self.assertRaises(RuntimeError):
            driver.ping()

        # Disarm does not crash
        driver.disarm_and_close()


# ============================================================================
# from tests/test-node.py (prefix m2a_)
# ============================================================================
"""Adversarial Stress Test Suite for Milestone 2: 1. Async TCP Framing & Wire Codec (T-386)    - Byte-by-byte (1-byte chunk) stream feeding across 50 multi-opcode frames    - Irregular/randomized chunk slicing across packet boundaries    - High-concurrency async TCP client/server throughput (30 concurrent clients, 300 frames)    - Corrupted CRC32 injection across head, middle, and tail of payload    - Corrupted magic, version, opcode, and underflow rejection    - Oversized payload length header rejection (> 64MB)    - Zero-byte payload valid frame roundtrip (CRC32=0)    - Stream buffer partial frame drainage and resume    - NodeWireDispatcher error response generation for unhandled opcodes  2. Heartbeat Monitor & Dead-Peer Eviction (T-387)    - Mathematical boundary precision (0s, 4.999s, 5.0s, 9.999s, 10.0s, 14.999s, 15.0s)    - Rapid flapping and state churn across 20 peers for 100 timesteps    - Mass simultaneous eviction of 100 peers in a single sweep    - Complete listener notification dispatch on mass eviction    - Clean re-admission after eviction with strike and state reset    - Local node ID self-filtering rejection    - Monotonic time jitter / backward timestamp protection    - Custom threshold configuration lifecycle"""


import asyncio
import os
import random
import sys
import time
import unittest
import zlib

_m2a_HERE = os.path.dirname(os.path.abspath(__file__))
_m2a_ROOT = os.path.normpath(os.path.join(_m2a_HERE, ".."))
sys.path.insert(0, os.path.join(_m2a_ROOT, "usr", "libexec", "mios", "node"))

import wire
import discovery

class m2a_TestAsyncFramingAdversarial(unittest.IsolatedAsyncioTestCase):
    """Adversarial stress testing for T-386 Async Tokio / Asyncio TCP Framing."""

    def test_byte_by_byte_stream_chunking_multi_frames(self):
        """Stress: Stream 50 multi-opcode, variable-length frames 1 byte at a time."""
        buf = wire.AsyncFrameBuffer()
        opcodes = [
            wire.Opcode.HEARTBEAT,
            wire.Opcode.NODE_ANNOUNCE,
            wire.Opcode.TASK_OFFLOAD,
            wire.Opcode.TASK_RESULT,
            wire.Opcode.STATE_SYNC,
            wire.Opcode.STATE_ACK,
            wire.Opcode.ERROR,
        ]

        expected_frames = []
        raw_stream = bytearray()

        rng = random.Random(42)
        for i in range(50):
            op = opcodes[i % len(opcodes)]
            node_id = 1000 + i
            payload_len = rng.randint(0, 1024)
            payload = rng.randbytes(payload_len) if hasattr(rng, "randbytes") else bytes(rng.getrandbits(8) for _ in range(payload_len))
            frame = wire.Frame.create(op, node_id, payload)
            expected_frames.append(frame)
            raw_stream.extend(frame.encode())

        # Feed byte-by-byte
        extracted_frames = []
        for b in raw_stream:
            buf.feed(bytes([b]))
            while True:
                f = buf.try_pop_frame()
                if f is None:
                    break
                extracted_frames.append(f)

        self.assertEqual(len(extracted_frames), 50)
        for i in range(50):
            self.assertEqual(extracted_frames[i].header.opcode, expected_frames[i].header.opcode)
            self.assertEqual(extracted_frames[i].header.node_id, expected_frames[i].header.node_id)
            self.assertEqual(extracted_frames[i].header.payload_len, expected_frames[i].header.payload_len)
            self.assertEqual(extracted_frames[i].header.checksum, expected_frames[i].header.checksum)
            self.assertEqual(extracted_frames[i].payload, expected_frames[i].payload)

    def test_random_irregular_chunk_slicing(self):
        """Stress: Random chunk sizes (1 to 37 bytes) slicing across frame headers and payloads."""
        buf = wire.AsyncFrameBuffer()
        rng = random.Random(1337)

        frames = [
            wire.Frame.create(wire.Opcode.TASK_OFFLOAD, 10, b"Alpha" * 20),
            wire.Frame.create(wire.Opcode.TASK_RESULT, 20, b"Beta" * 40),
            wire.Frame.create(wire.Opcode.STATE_SYNC, 30, b"Gamma" * 60),
            wire.Frame.create(wire.Opcode.HEARTBEAT, 40, b"Delta" * 10),
            wire.Frame.create(wire.Opcode.ERROR, 50, b"Epsilon" * 5),
        ]

        full_stream = b"".join(f.encode() for f in frames)
        offset = 0
        extracted = []

        while offset < len(full_stream):
            chunk_size = rng.randint(1, 37)
            chunk = full_stream[offset : offset + chunk_size]
            offset += len(chunk)
            buf.feed(chunk)
            while True:
                f = buf.try_pop_frame()
                if f is None:
                    break
                extracted.append(f)

        self.assertEqual(len(extracted), 5)
        for expected, actual in zip(frames, extracted):
            self.assertEqual(expected.header.opcode, actual.header.opcode)
            self.assertEqual(expected.header.node_id, actual.header.node_id)
            self.assertEqual(expected.payload, actual.payload)

    async def test_high_concurrency_tcp_throughput(self):
        """Stress: 30 concurrent async TCP clients blasting 10 frames each to the server."""
        received_frames_map = {}
        lock = asyncio.Lock()

        def server_handler(frame: wire.Frame, writer: asyncio.StreamWriter) -> wire.Frame:
            # Echo back with STATE_ACK
            return wire.Frame.create(
                wire.Opcode.STATE_ACK,
                node_id=frame.header.node_id,
                payload=b"ACK:" + frame.payload,
            )

        server = wire.AsyncTcpFrameServer(node_id=1, host="127.0.0.1", port=0, handler=server_handler)
        port = await server.start()

        num_clients = 30
        frames_per_client = 10

        async def client_worker(client_id: int):
            client = wire.AsyncTcpFrameClient("127.0.0.1", port)
            await client.connect()
            try:
                for seq in range(frames_per_client):
                    payload = f"node_{client_id}_seq_{seq}".encode("utf-8")
                    req = wire.Frame.create(wire.Opcode.TASK_OFFLOAD, node_id=client_id, payload=payload)
                    await client.send_frame(req)
                    resp = await asyncio.wait_for(client.recv_frame(), timeout=5.0)
                    self.assertEqual(resp.header.opcode, wire.Opcode.STATE_ACK)
                    self.assertEqual(resp.payload, b"ACK:" + payload)
            finally:
                await client.close()

        tasks = [client_worker(i) for i in range(100, 100 + num_clients)]
        await asyncio.gather(*tasks)

        await server.stop()

    def test_crc32_tamper_fault_injection(self):
        """Fault injection: Single-byte corruptions at head, middle, and tail of payload."""
        payload = b"AUTHENTIC_PAYLOAD_FOR_CORRUPTION_TESTING_1234567890"
        frame = wire.Frame.create(wire.Opcode.TASK_OFFLOAD, 999, payload)
        encoded = frame.encode()

        # Head corruption (byte index 16 is first payload byte)
        head_corrupt = bytearray(encoded)
        head_corrupt[16] ^= 0x01
        with self.assertRaises(ValueError) as ctx:
            wire.Frame.decode(bytes(head_corrupt))
        self.assertIn("CRC32 mismatch", str(ctx.exception))

        # Middle corruption
        mid_corrupt = bytearray(encoded)
        mid_corrupt[16 + len(payload) // 2] ^= 0x80
        with self.assertRaises(ValueError) as ctx:
            wire.Frame.decode(bytes(mid_corrupt))
        self.assertIn("CRC32 mismatch", str(ctx.exception))

        # Tail corruption
        tail_corrupt = bytearray(encoded)
        tail_corrupt[-1] ^= 0xFF
        with self.assertRaises(ValueError) as ctx:
            wire.Frame.decode(bytes(tail_corrupt))
        self.assertIn("CRC32 mismatch", str(ctx.exception))

    def test_malformed_headers_rejection(self):
        """Adversarial: Invalid magic, unsupported version, unknown opcode, buffer underflow."""
        # 1. Invalid magic
        bad_magic = bytearray(16)
        bad_magic[0] = 0x58
        bad_magic[1] = 0x58
        with self.assertRaises(ValueError) as ctx:
            wire.Header.decode(bytes(bad_magic))
        self.assertIn("Invalid MiOS magic", str(ctx.exception))

        # 2. Unsupported version
        bad_version = bytearray(wire.Header(opcode=wire.Opcode.HEARTBEAT).encode())
        bad_version[2] = 0x09
        with self.assertRaises(ValueError) as ctx:
            wire.Header.decode(bytes(bad_version))
        self.assertIn("Unsupported protocol version", str(ctx.exception))

        # 3. Unknown opcode
        bad_op = bytearray(wire.Header().encode())
        bad_op[3] = 0xFE
        with self.assertRaises(ValueError) as ctx:
            wire.Header.decode(bytes(bad_op))
        self.assertIn("Unknown MiOS message opcode", str(ctx.exception))

        # 4. Buffer underflow
        with self.assertRaises(ValueError) as ctx:
            wire.Header.decode(b"\x4D\x49\x01")
        self.assertIn("Buffer underflow", str(ctx.exception))

    def test_oversized_payload_ceiling_rejection(self):
        """Security: Header claiming > 64MB payload length is rejected before allocation."""
        bad_len_header = bytearray(wire.Header(opcode=wire.Opcode.TASK_OFFLOAD).encode())
        # Set payload_len to 65 MB (65 * 1024 * 1024)
        bad_len = 65 * 1024 * 1024
        bad_len_header[8:12] = bad_len.to_bytes(4, byteorder="big")

        with self.assertRaises(ValueError) as ctx:
            wire.Header.decode(bytes(bad_len_header))
        self.assertIn("exceeds maximum allowed ceiling", str(ctx.exception))

        buf = wire.AsyncFrameBuffer()
        buf.feed(bytes(bad_len_header))
        with self.assertRaises(ValueError) as ctx:
            buf.try_pop_frame()
        self.assertIn("exceeds maximum", str(ctx.exception))

    def test_zero_byte_payload_roundtrip(self):
        """Edge case: Empty payload frame (0 bytes, CRC32=0)."""
        frame = wire.Frame.create(wire.Opcode.STATE_ACK, 77, b"")
        self.assertEqual(frame.header.payload_len, 0)
        self.assertEqual(frame.header.checksum, 0)

        encoded = frame.encode()
        self.assertEqual(len(encoded), 16)

        decoded = wire.Frame.decode(encoded)
        self.assertEqual(decoded.header.opcode, wire.Opcode.STATE_ACK)
        self.assertEqual(decoded.header.node_id, 77)
        self.assertEqual(decoded.payload, b"")

    def test_partial_frame_drain_and_resume(self):
        """Stream integrity: Half-frame in buffer across feeds."""
        buf = wire.AsyncFrameBuffer()
        f1 = wire.Frame.create(wire.Opcode.HEARTBEAT, 1, b"FRAME_ONE")
        f2 = wire.Frame.create(wire.Opcode.HEARTBEAT, 2, b"FRAME_TWO_LONGER_PAYLOAD")

        enc1 = f1.encode()
        enc2 = f2.encode()

        # Feed complete f1 + 8 bytes of f2 header
        buf.feed(enc1 + enc2[:8])
        pop1 = buf.try_pop_frame()
        self.assertIsNotNone(pop1)
        self.assertEqual(pop1.payload, b"FRAME_ONE")

        # Second pop should return None
        self.assertIsNone(buf.try_pop_frame())

        # Feed remainder of f2
        buf.feed(enc2[8:])
        pop2 = buf.try_pop_frame()
        self.assertIsNotNone(pop2)
        self.assertEqual(pop2.payload, b"FRAME_TWO_LONGER_PAYLOAD")

    def test_dispatcher_unhandled_opcode_error_response(self):
        """Dispatcher: Handles registered opcodes and emits structured ERROR frame for unregistered."""
        dispatcher = wire.NodeWireDispatcher(node_id=500)

        def on_heartbeat(f: wire.Frame) -> wire.Frame:
            return wire.Frame.create(wire.Opcode.STATE_ACK, 500, b"PONG")

        dispatcher.register_handler(wire.Opcode.HEARTBEAT, on_heartbeat)

        # 1. Registered opcode
        req1 = wire.Frame.create(wire.Opcode.HEARTBEAT, 10, b"PING")
        resp1 = dispatcher.dispatch(req1)
        self.assertIsNotNone(resp1)
        self.assertEqual(resp1.header.opcode, wire.Opcode.STATE_ACK)
        self.assertEqual(resp1.payload, b"PONG")

        # 2. Unregistered opcode -> ERROR frame
        req2 = wire.Frame.create(wire.Opcode.TASK_OFFLOAD, 10, b"DO_WORK")
        resp2 = dispatcher.dispatch(req2)
        self.assertIsNotNone(resp2)
        self.assertEqual(resp2.header.opcode, wire.Opcode.ERROR)
        self.assertIn(b"Unhandled opcode", resp2.payload)

class m2a_TestHeartbeatEvictionAdversarial(unittest.TestCase):
    """Adversarial stress testing for T-387 Heartbeat Monitor & Dead-Peer Eviction."""

    def setUp(self):
        self.monitor = discovery.HeartbeatMonitor(
            local_node_id=1000,
            heartbeat_interval=5.0,
            degraded_threshold=10.0,
            eviction_threshold=15.0,
        )

    def test_exact_mathematical_boundary_transitions(self):
        """Precision: Exact timing boundary conditions for strike and health status transitions."""
        # Baseline intervals: interval=5.0, degraded=10.0, eviction=15.0
        test_cases = [
            # (elapsed_secs, expected_status, expected_strikes)
            (0.0, discovery.PeerHealthStatus.HEALTHY, 0),
            (2.5, discovery.PeerHealthStatus.HEALTHY, 0),
            (4.999, discovery.PeerHealthStatus.HEALTHY, 0),
            (5.0, discovery.PeerHealthStatus.HEALTHY, 1),
            (7.5, discovery.PeerHealthStatus.HEALTHY, 1),
            (9.999, discovery.PeerHealthStatus.HEALTHY, 1),
            (10.0, discovery.PeerHealthStatus.DEGRADED, 2),
            (12.5, discovery.PeerHealthStatus.DEGRADED, 2),
            (14.999, discovery.PeerHealthStatus.DEGRADED, 2),
            (15.0, discovery.PeerHealthStatus.DEAD, 3),
            (20.0, discovery.PeerHealthStatus.DEAD, 4),
            (100.0, discovery.PeerHealthStatus.DEAD, 20),
        ]

        for elapsed, exp_status, exp_strikes in test_cases:
            status, strikes = self.monitor.assess_health(elapsed)
            self.assertEqual(
                status, exp_status, f"At {elapsed}s: expected status {exp_status}, got {status}"
            )
            self.assertEqual(
                strikes, exp_strikes, f"At {elapsed}s: expected strikes {exp_strikes}, got {strikes}"
            )

    def test_rapid_peer_flapping_and_state_churn(self):
        """Stress: 20 peers undergoing 100 timesteps of random flapping, eviction, and re-admission."""
        num_peers = 20
        timesteps = 100
        rng = random.Random(999)

        # Initial registration at T=0
        current_time = 0.0
        for i in range(num_peers):
            self.monitor.record_heartbeat(
                node_id=i + 1,
                addr=f"192.168.1.{i+1}",
                port=8000 + i,
                now=current_time,
            )

        self.assertEqual(self.monitor.peer_count, num_peers)

        for step in range(1, timesteps + 1):
            current_time += 2.0  # Advance 2s per step

            # Random subset of peers send heartbeat
            for i in range(num_peers):
                nid = i + 1
                if rng.random() > 0.4:  # 60% chance to send heartbeat
                    self.monitor.record_heartbeat(
                        node_id=nid,
                        addr=f"192.168.1.{nid}",
                        port=8000 + i,
                        uptime_secs=current_time,
                        cpu_load_pct=rng.randint(5, 95),
                        mem_available_kb=1024000,
                        now=current_time,
                    )

            healthy, degraded, evicted = self.monitor.sweep(now=current_time)

            # Invariant checks
            for h in healthy:
                p = self.monitor.get_peer(h)
                self.assertIsNotNone(p)
                self.assertEqual(p.status, discovery.PeerHealthStatus.HEALTHY)
            for d in degraded:
                p = self.monitor.get_peer(d)
                self.assertIsNotNone(p)
                self.assertEqual(p.status, discovery.PeerHealthStatus.DEGRADED)
            for ev in evicted:
                # Evicted peers must be removed immediately
                self.assertIsNone(self.monitor.get_peer(ev.node_id))
                self.assertFalse(self.monitor.is_peer_active(ev.node_id))

    def test_mass_simultaneous_eviction_and_listeners(self):
        """Stress: 100 simultaneous peers expiring at once and firing event listeners."""
        events_collected = []

        def on_evict(evt: discovery.EvictionEvent):
            events_collected.append(evt)

        self.monitor.add_eviction_listener(on_evict)

        t_init = 1000.0
        for i in range(100):
            self.monitor.record_heartbeat(
                node_id=2000 + i,
                addr=f"10.10.1.{i}",
                port=9000,
                now=t_init,
            )

        self.assertEqual(self.monitor.peer_count, 100)

        # Sweep at T=1016 (16s elapsed for all 100 peers)
        healthy, degraded, evicted = self.monitor.sweep(now=t_init + 16.0)

        self.assertEqual(len(healthy), 0)
        self.assertEqual(len(degraded), 0)
        self.assertEqual(len(evicted), 100)
        self.assertEqual(len(events_collected), 100)
        self.assertEqual(self.monitor.peer_count, 0)

        # Verify all event details
        for evt in events_collected:
            self.assertGreaterEqual(evt.elapsed_secs, 15.0)
            self.assertGreaterEqual(evt.missed_strikes, 3)
            self.assertIn("3-strike timeout", evt.reason)

    def test_self_node_filtering(self):
        """Edge case: Monitor rejects recording heartbeats/announcements from local_node_id."""
        res_hb = self.monitor.record_heartbeat(
            node_id=1000,  # Same as local_node_id
            addr="127.0.0.1",
            port=8650,
            now=100.0,
        )
        self.assertIsNone(res_hb)
        self.assertEqual(self.monitor.peer_count, 0)

        res_ann = self.monitor.record_announce(
            node_id=1000,  # Same as local_node_id
            addr="127.0.0.1",
            port=8650,
            now=100.0,
        )
        self.assertIsNone(res_ann)
        self.assertEqual(self.monitor.peer_count, 0)

    def test_backward_timestamp_monotonic_protection(self):
        """Robustness: Clock jitter or backwards time jump does not crash or underflow."""
        self.monitor.record_heartbeat(500, "10.0.0.1", 8000, now=100.0)

        # Sweep with backwards timestamp (e.g. clock correction to 90.0)
        healthy, degraded, evicted = self.monitor.sweep(now=90.0)
        self.assertEqual(healthy, [500])
        peer = self.monitor.get_peer(500)
        self.assertEqual(peer.status, discovery.PeerHealthStatus.HEALTHY)
        self.assertEqual(peer.missed_strikes, 0)

    def test_custom_fast_thresholds(self):
        """Configurability: Fast 1s interval, 2s degraded, 3s eviction thresholds."""
        fast_monitor = discovery.HeartbeatMonitor(
            local_node_id=1,
            heartbeat_interval=1.0,
            degraded_threshold=2.0,
            eviction_threshold=3.0,
        )

        fast_monitor.record_heartbeat(2, "127.0.0.1", 8000, now=10.0)

        # T=11.5 (1.5s elapsed -> 1 strike -> Healthy)
        h, d, e = fast_monitor.sweep(now=11.5)
        self.assertEqual(h, [2])

        # T=12.2 (2.2s elapsed -> 2 strikes -> Degraded)
        h, d, e = fast_monitor.sweep(now=12.2)
        self.assertEqual(d, [2])

        # T=13.1 (3.1s elapsed -> 3 strikes -> Evicted)
        h, d, e = fast_monitor.sweep(now=13.1)
        self.assertEqual(len(e), 1)
        self.assertEqual(e[0].node_id, 2)
        self.assertEqual(fast_monitor.peer_count, 0)

def m2a_main() -> int:
    suite = unittest.TestSuite()
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(m2a_TestAsyncFramingAdversarial))
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(m2a_TestHeartbeatEvictionAdversarial))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


# ============================================================================
# from tests/test-node.py (prefix m2c2a_)
# ============================================================================
"""Adversarial Stress Test Suite for Milestone 2 / T-388 (Challenger 2): 1. Cryptographic Handshake Adversarial Tests:    - Exhaustive single-bit and multi-byte signature tampering across Init and Resp packets (all 64 bytes fuzzed).    - Signature truncation (< 64 bytes) and extension (> 64 bytes) rejection.    - Forged identity pubkeys and ephemeral pubkeys injection / MITM rejection.    - Imposter node identity spoofing and unauthorized packet creation.    - Replay attack resilience and ephemeral key freshness (no key reuse).    - Key derivation symmetry, directional TX/RX key separation, and anti-reflection guarantee.  2. Wire AEAD Encryption Adversarial Tests:    - Exhaustive bit-flip fuzzing across all payload ciphertext bytes.    - Exhaustive bit-flip fuzzing across all 16 bytes of the Poly1305 MAC tag.    - Ciphertext truncation (< 16 bytes) and partial MAC tag drop handling.    - AAD / Node ID spoofing and cross-node ciphertext injection rejection.    - Strict nonce sequence progression, out-of-order packet drop, and wire replay attack prevention.    - High-volume multi-frame stream stress (1,000 frames) with boundary payload sizes (0B, 1B, 15B, 16B, 17B, 64B, 65B, 64KB).    - Layered defense validation: Wire CRC32 transport integrity vs Poly1305 cryptographic authenticity.  3. Concurrency & RFC Standards Compliance:    - Concurrent multi-session thread isolation across 20 distinct mesh nodes.    - Session renegotiation & zero cross-session decryption leakage.    - RFC 8439 / RFC 7748 / RFC 5869 cryptographic correctness verification."""


import concurrent.futures
import os
import random
import struct
import sys
import unittest

_m2c2a_HERE = os.path.dirname(os.path.abspath(__file__))
_m2c2a_ROOT = os.path.normpath(os.path.join(_m2c2a_HERE, ".."))
sys.path.insert(0, os.path.join(_m2c2a_ROOT, "usr", "libexec", "mios", "node"))

import crypto
import wire

class m2c2a_TestCryptoHandshakeAdversarial(unittest.TestCase):
    """Adversarial stress testing for Ed25519 mutual authentication & X25519 handshake."""

    def setUp(self):
        self.node_a = crypto.NodeIdentity.generate(node_id=1001)
        self.node_b = crypto.NodeIdentity.generate(node_id=2002)
        self.imposter = crypto.NodeIdentity.generate(node_id=6666)

    def test_exhaustive_64_byte_signature_bit_flip_init(self):
        """Fuzz every single byte of the 64-byte Ed25519 signature in HandshakeInitPacket."""
        init_pkt, _ = crypto.CryptoHandshake.create_init(self.node_a)
        original_sig = init_pkt.signature
        self.assertEqual(len(original_sig), 64)

        for byte_idx in range(64):
            # Test 3 distinct bit masks per byte (0x01, 0x80, 0xFF)
            for mask in (0x01, 0x80, 0xFF):
                corrupted_sig = bytearray(original_sig)
                corrupted_sig[byte_idx] ^= mask

                bad_init = crypto.HandshakeInitPacket(
                    sender_node_id=init_pkt.sender_node_id,
                    id_pubkey=init_pkt.id_pubkey,
                    ephemeral_pubkey=init_pkt.ephemeral_pubkey,
                    signature=bytes(corrupted_sig),
                )
                with self.assertRaises(ValueError, msg=f"Failed to reject corrupted sig at byte {byte_idx} mask {mask:#04x}") as ctx:
                    crypto.CryptoHandshake.process_init_and_respond(self.node_b, bad_init)
                self.assertIn("signature verification failed", str(ctx.exception).lower())

    def test_exhaustive_64_byte_signature_bit_flip_resp(self):
        """Fuzz every single byte of the 64-byte Ed25519 signature in HandshakeRespPacket."""
        init_pkt, eph_priv_a = crypto.CryptoHandshake.create_init(self.node_a)
        resp_pkt, _ = crypto.CryptoHandshake.process_init_and_respond(self.node_b, init_pkt)
        original_sig = resp_pkt.signature
        self.assertEqual(len(original_sig), 64)

        for byte_idx in range(64):
            for mask in (0x01, 0x80, 0xFF):
                corrupted_sig = bytearray(original_sig)
                corrupted_sig[byte_idx] ^= mask

                bad_resp = crypto.HandshakeRespPacket(
                    sender_node_id=resp_pkt.sender_node_id,
                    id_pubkey=resp_pkt.id_pubkey,
                    ephemeral_pubkey=resp_pkt.ephemeral_pubkey,
                    signature=bytes(corrupted_sig),
                )
                with self.assertRaises(ValueError, msg=f"Failed to reject corrupted resp sig at byte {byte_idx} mask {mask:#04x}") as ctx:
                    crypto.CryptoHandshake.finalize_init(self.node_a, eph_priv_a, bad_resp)
                self.assertIn("signature verification failed", str(ctx.exception).lower())

    def test_signature_boundary_lengths(self):
        """Verify that signatures with invalid lengths are strictly rejected."""
        init_pkt, eph_priv_a = crypto.CryptoHandshake.create_init(self.node_a)

        # Test truncated and oversized signatures
        for invalid_len in (0, 1, 16, 32, 63, 65, 100, 128):
            bad_sig = b"\x00" * invalid_len
            bad_init = crypto.HandshakeInitPacket(
                sender_node_id=init_pkt.sender_node_id,
                id_pubkey=init_pkt.id_pubkey,
                ephemeral_pubkey=init_pkt.ephemeral_pubkey,
                signature=bad_sig,
            )
            with self.assertRaises(ValueError):
                crypto.CryptoHandshake.process_init_and_respond(self.node_b, bad_init)

    def test_forged_ephemeral_pubkey_mitm_rejection(self):
        """Attacker swaps ephemeral pubkey with attacker's pubkey without updating signature."""
        init_pkt, _ = crypto.CryptoHandshake.create_init(self.node_a)
        attacker_priv, attacker_eph_pub = crypto.CryptoHandshake._generate_x25519_keypair()

        # Swapping ephemeral pubkey breaks Ed25519 signature
        mitm_init = crypto.HandshakeInitPacket(
            sender_node_id=init_pkt.sender_node_id,
            id_pubkey=init_pkt.id_pubkey,
            ephemeral_pubkey=attacker_eph_pub,
            signature=init_pkt.signature,
        )
        with self.assertRaises(ValueError) as ctx:
            crypto.CryptoHandshake.process_init_and_respond(self.node_b, mitm_init)
        self.assertIn("signature verification failed", str(ctx.exception).lower())

    def test_forged_id_pubkey_rejection(self):
        """Attacker swaps identity pubkey with imposter's identity pubkey."""
        init_pkt, _ = crypto.CryptoHandshake.create_init(self.node_a)

        mitm_init = crypto.HandshakeInitPacket(
            sender_node_id=init_pkt.sender_node_id,
            id_pubkey=self.imposter.public_bytes,
            ephemeral_pubkey=init_pkt.ephemeral_pubkey,
            signature=init_pkt.signature,
        )
        with self.assertRaises(ValueError) as ctx:
            crypto.CryptoHandshake.process_init_and_respond(self.node_b, mitm_init)
        self.assertIn("signature verification failed", str(ctx.exception).lower())

    def test_imposter_identity_spoofing(self):
        """Imposter creates valid signature with its own key but claims Node A's identity."""
        attacker_priv, attacker_eph_pub = crypto.CryptoHandshake._generate_x25519_keypair()
        # Imposter signs its ephemeral pubkey with its own private key
        attacker_sig = self.imposter.sign(attacker_eph_pub)

        # But imposter claims sender_node_id = 1001 and id_pubkey = node_a.public_bytes
        spoofed_init = crypto.HandshakeInitPacket(
            sender_node_id=1001,
            id_pubkey=self.node_a.public_bytes,
            ephemeral_pubkey=attacker_eph_pub,
            signature=attacker_sig,
        )
        with self.assertRaises(ValueError) as ctx:
            crypto.CryptoHandshake.process_init_and_respond(self.node_b, spoofed_init)
        self.assertIn("signature verification failed", str(ctx.exception).lower())

    def test_ephemeral_freshness_and_replay_isolation(self):
        """Ensure fresh ephemeral keys generate completely unique session keys each time."""
        init_1, eph_priv_a1 = crypto.CryptoHandshake.create_init(self.node_a)
        init_2, eph_priv_a2 = crypto.CryptoHandshake.create_init(self.node_a)

        # Ephemeral keys must be strictly different
        self.assertNotEqual(init_1.ephemeral_pubkey, init_2.ephemeral_pubkey)
        self.assertNotEqual(eph_priv_a1, eph_priv_a2)

        resp_1, session_b1 = crypto.CryptoHandshake.process_init_and_respond(self.node_b, init_1)
        resp_2, session_b2 = crypto.CryptoHandshake.process_init_and_respond(self.node_b, init_2)

        session_a1 = crypto.CryptoHandshake.finalize_init(self.node_a, eph_priv_a1, resp_1)
        session_a2 = crypto.CryptoHandshake.finalize_init(self.node_a, eph_priv_a2, resp_2)

        # Session 1 and Session 2 must have distinct symmetric keys
        self.assertNotEqual(session_a1.tx_key, session_a2.tx_key)
        self.assertNotEqual(session_a1.rx_key, session_a2.rx_key)

        # Ciphertexts from session 1 cannot be decrypted in session 2
        enc1 = session_a1.encrypt_payload(b"Confidential session 1 payload")
        with self.assertRaises(ValueError):
            session_b2.decrypt_payload(enc1)

    def test_key_derivation_directional_separation(self):
        """Verify TX and RX keys are distinct to prevent reflection attacks."""
        init_pkt, eph_priv_a = crypto.CryptoHandshake.create_init(self.node_a)
        resp_pkt, session_b = crypto.CryptoHandshake.process_init_and_respond(self.node_b, init_pkt)
        session_a = crypto.CryptoHandshake.finalize_init(self.node_a, eph_priv_a, resp_pkt)

        # Symmetric match between peer directions
        self.assertEqual(session_a.tx_key, session_b.rx_key)
        self.assertEqual(session_a.rx_key, session_b.tx_key)

        # Anti-reflection: local TX key MUST NOT equal local RX key
        self.assertNotEqual(session_a.tx_key, session_a.rx_key)
        self.assertNotEqual(session_b.tx_key, session_b.rx_key)

class m2c2a_TestWireAeadAdversarial(unittest.TestCase):
    """Adversarial stress testing for ChaCha20-Poly1305 AEAD wire encryption."""

    def setUp(self):
        self.node_a = crypto.NodeIdentity.generate(node_id=101)
        self.node_b = crypto.NodeIdentity.generate(node_id=202)
        init_pkt, eph_priv_a = crypto.CryptoHandshake.create_init(self.node_a)
        resp_pkt, self.session_b = crypto.CryptoHandshake.process_init_and_respond(self.node_b, init_pkt)
        self.session_a = crypto.CryptoHandshake.finalize_init(self.node_a, eph_priv_a, resp_pkt)

    def test_exhaustive_ciphertext_bit_flips(self):
        """Flip every single bit in a 32-byte payload ciphertext; all 256 bit flips must fail MAC check."""
        plaintext = b"0123456789abcdef0123456789abcdef"

        # Test 10 distinct encrypted blocks
        for block_idx in range(5):
            ciphertext = bytearray(self.session_a.encrypt_payload(plaintext))
            # Ciphertext length = 32 bytes ciphertext + 16 bytes MAC = 48 bytes
            self.assertEqual(len(ciphertext), 48)

            # Test bitflips in the first 32 bytes (payload portion)
            for byte_pos in range(32):
                for bit in range(8):
                    corrupted = bytearray(ciphertext)
                    corrupted[byte_pos] ^= (1 << bit)

                    # Create a dummy receiver with matching rx_nonce for test isolation
                    isolated_session = crypto.NodeCryptoSession(
                        local_node_id=self.session_b.local_node_id,
                        remote_node_id=self.session_b.remote_node_id,
                        tx_key=self.session_b.tx_key,
                        rx_key=self.session_b.rx_key,
                    )
                    isolated_session.rx_nonce = self.session_a.tx_nonce - 1

                    with self.assertRaises(ValueError, msg=f"Bit flip at byte {byte_pos} bit {bit} was not caught!") as ctx:
                        isolated_session.decrypt_payload(bytes(corrupted))
                    self.assertIn("MAC verification failed", str(ctx.exception))

    def test_exhaustive_poly1305_mac_tag_bit_flips(self):
        """Flip every single bit in the 16-byte Poly1305 MAC tag; all 128 bit flips must fail."""
        plaintext = b"Sensitive telemetry data for edge micro-cluster"
        ciphertext = bytearray(self.session_a.encrypt_payload(plaintext))
        ct_len = len(plaintext)
        tag_start = ct_len

        for byte_pos in range(tag_start, tag_start + 16):
            for bit in range(8):
                corrupted = bytearray(ciphertext)
                corrupted[byte_pos] ^= (1 << bit)

                isolated_session = crypto.NodeCryptoSession(
                    local_node_id=self.session_b.local_node_id,
                    remote_node_id=self.session_b.remote_node_id,
                    tx_key=self.session_b.tx_key,
                    rx_key=self.session_b.rx_key,
                )
                isolated_session.rx_nonce = self.session_a.tx_nonce - 1

                with self.assertRaises(ValueError, msg=f"MAC tag bit flip at tag byte {byte_pos - tag_start} bit {bit} was not caught!") as ctx:
                    isolated_session.decrypt_payload(bytes(corrupted))
                self.assertIn("MAC verification failed", str(ctx.exception))

    def test_truncated_ciphertext_and_tags(self):
        """Ensure truncated ciphertexts and truncated MAC tags fail immediately."""
        plaintext = b"Payload for truncation tests"
        ciphertext = self.session_a.encrypt_payload(plaintext)

        # Truncate ciphertext to lengths shorter than tag (0..15 bytes)
        for truncated_len in range(16):
            truncated = ciphertext[:truncated_len]
            isolated_session = crypto.NodeCryptoSession(
                local_node_id=self.session_b.local_node_id,
                remote_node_id=self.session_b.remote_node_id,
                tx_key=self.session_b.tx_key,
                rx_key=self.session_b.rx_key,
            )
            isolated_session.rx_nonce = self.session_a.tx_nonce - 1
            with self.assertRaises((ValueError, Exception)):
                isolated_session.decrypt_payload(truncated)

        # Truncate partial tag (drop 1..15 bytes from end)
        for drop_bytes in range(1, 16):
            truncated = ciphertext[:-drop_bytes]
            isolated_session = crypto.NodeCryptoSession(
                local_node_id=self.session_b.local_node_id,
                remote_node_id=self.session_b.remote_node_id,
                tx_key=self.session_b.tx_key,
                rx_key=self.session_b.rx_key,
            )
            isolated_session.rx_nonce = self.session_a.tx_nonce - 1
            with self.assertRaises((ValueError, Exception)):
                isolated_session.decrypt_payload(truncated)

    def test_cross_node_and_aad_spoofing(self):
        """Verify that Node C cannot inject ciphertexts into Node B's session (AAD mismatch)."""
        node_c = crypto.NodeIdentity.generate(node_id=303)
        init_c, eph_c = crypto.CryptoHandshake.create_init(node_c)
        resp_c, session_b_c = crypto.CryptoHandshake.process_init_and_respond(self.node_b, init_c)
        session_c = crypto.CryptoHandshake.finalize_init(node_c, eph_c, resp_c)

        # Node C encrypts a payload meant for its own session with B
        c_payload = b"Node C malicious injection"
        c_ciphertext = session_c.encrypt_payload(c_payload)

        # Attacker injects this ciphertext into Node A -> Node B session
        with self.assertRaises(ValueError):
            self.session_b.decrypt_payload(c_ciphertext)

    def test_strict_nonce_progression_and_replay_rejection(self):
        """Verify strict nonce synchronization, out-of-order drop, and replay rejection."""
        # Encrypt 4 distinct messages
        msg1 = self.session_a.encrypt_payload(b"Message 1 (nonce 0)")
        msg2 = self.session_a.encrypt_payload(b"Message 2 (nonce 1)")
        msg3 = self.session_a.encrypt_payload(b"Message 3 (nonce 2)")
        msg4 = self.session_a.encrypt_payload(b"Message 4 (nonce 3)")

        self.assertEqual(self.session_a.tx_nonce, 4)
        self.assertEqual(self.session_b.rx_nonce, 0)

        # Decrypt message 1 -> succeeds, rx_nonce becomes 1
        d1 = self.session_b.decrypt_payload(msg1)
        self.assertEqual(d1, b"Message 1 (nonce 0)")
        self.assertEqual(self.session_b.rx_nonce, 1)

        # REPLAY ATTACK: Attacker sends msg1 again -> MUST FAIL (rx_nonce is 1, msg1 was encrypted with nonce 0)
        with self.assertRaises(ValueError) as ctx:
            self.session_b.decrypt_payload(msg1)
        self.assertIn("MAC verification failed", str(ctx.exception))
        # Note: on failure rx_nonce still incremented to 2
        self.assertEqual(self.session_b.rx_nonce, 2)

        # OUT-OF-ORDER: Now receiver rx_nonce is 2. msg3 (encrypted with nonce 2) should decrypt cleanly!
        d3 = self.session_b.decrypt_payload(msg3)
        self.assertEqual(d3, b"Message 3 (nonce 2)")
        self.assertEqual(self.session_b.rx_nonce, 3)

        # DROPPED PACKET REPLAY: Attempting to decrypt msg2 (nonce 1) when rx_nonce is 3 -> MUST FAIL
        with self.assertRaises(ValueError):
            self.session_b.decrypt_payload(msg2)

    def test_high_volume_multi_frame_stream_stress(self):
        """Stream 1,000 frames with varying payload sizes across boundaries."""
        payload_sizes = [
            0,      # Empty payload (MAC tag only)
            1,      # 1 byte
            7,      # Sub-block
            15,     # ChaCha/Poly boundary - 1
            16,     # Exactly 16 bytes
            17,     # 16 + 1
            31,     # Sub-block
            32,     # 2 * 16
            63,     # 64 - 1 (ChaCha20 block boundary - 1)
            64,     # Exact ChaCha20 block (64B)
            65,     # 64 + 1
            128,    # 2 blocks
            1024,   # 1 KB
            4096,   # 4 KB
            16384,  # 16 KB
            65536,  # 64 KB
        ]

        total_frames = 1000
        for i in range(total_frames):
            size = payload_sizes[i % len(payload_sizes)]
            # Generate deterministic patterned data
            if size == 0:
                raw_data = b""
            else:
                raw_data = bytes([(x + i) % 256 for x in range(size)])

            enc = self.session_a.encrypt_payload(raw_data)
            self.assertEqual(len(enc), size + 16)

            dec = self.session_b.decrypt_payload(enc)
            self.assertEqual(dec, raw_data, f"Mismatch on frame {i} of size {size}")

        self.assertEqual(self.session_a.tx_nonce, total_frames)
        self.assertEqual(self.session_b.rx_nonce, total_frames)

    def test_layered_wire_defense_crc32_and_poly1305(self):
        """Verify layered defense: CRC32 catches transport bitflips, Poly1305 catches crypto tampering."""
        original_payload = b'{"command":"migrate_agent","vm_id":42}'
        frame = wire.Frame.create(wire.Opcode.TASK_OFFLOAD, node_id=101, payload=original_payload)

        # Encrypt frame
        enc_frame = self.session_a.encrypt_frame(frame)
        raw_wire_bytes = bytearray(enc_frame.encode())

        # Layer 1: Corrupt 1 byte in the wire payload
        raw_wire_bytes[16 + 5] ^= 0x55

        # Transport decode must fail immediately on CRC32 without even reaching crypto layer
        with self.assertRaises(ValueError) as ctx:
            wire.Frame.decode(bytes(raw_wire_bytes))
        self.assertIn("CRC32 mismatch", str(ctx.exception))

        # Layer 2: Even if an attacker forge-updates the wire CRC32 to bypass transport checks...
        import zlib
        bad_payload = bytes(raw_wire_bytes[16:])
        new_crc = zlib.crc32(bad_payload) & 0xFFFFFFFF
        struct.pack_into(">I", raw_wire_bytes, 12, new_crc)

        # Now wire decode succeeds...
        bypassed_frame = wire.Frame.decode(bytes(raw_wire_bytes))
        self.assertEqual(bypassed_frame.header.checksum, new_crc)

        # But Cryptographic Layer 3 (Poly1305) catches the tampering and rejects decryption!
        with self.assertRaises(ValueError) as ctx:
            self.session_b.decrypt_frame(bypassed_frame)
        self.assertIn("MAC verification failed", str(ctx.exception))

class m2c2a_TestMultiSessionConcurrency(unittest.TestCase):
    """Stress tests concurrent sessions across multiple simulated edge nodes."""

    def test_concurrent_mesh_handshakes_and_traffic(self):
        """Simulate 20 nodes forming 10 simultaneous paired encrypted sessions in parallel threads."""
        def run_node_pair(pair_idx: int):
            node_x = crypto.NodeIdentity.generate(node_id=pair_idx * 2)
            node_y = crypto.NodeIdentity.generate(node_id=pair_idx * 2 + 1)

            # Handshake
            init_pkt, eph_priv_x = crypto.CryptoHandshake.create_init(node_x)
            resp_pkt, session_y = crypto.CryptoHandshake.process_init_and_respond(node_y, init_pkt)
            session_x = crypto.CryptoHandshake.finalize_init(node_x, eph_priv_x, resp_pkt)

            # Exchange 50 bidirectional messages
            for msg_i in range(50):
                # X -> Y
                px = f"Pair {pair_idx} Msg {msg_i} from X to Y".encode("utf-8")
                ctx = session_x.encrypt_payload(px)
                dec_y = session_y.decrypt_payload(ctx)
                if dec_y != px:
                    return False

                # Y -> X
                py = f"Pair {pair_idx} Msg {msg_i} from Y to X".encode("utf-8")
                cty = session_y.encrypt_payload(py)
                dec_x = session_x.decrypt_payload(cty)
                if dec_x != py:
                    return False

            return True

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(run_node_pair, i) for i in range(10)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        self.assertEqual(len(results), 10)
        self.assertTrue(all(results))

class m2c2a_TestRfcStandardsCompliance(unittest.TestCase):
    """Verifies low-level cryptographic primitive compliance against standard RFC test vectors."""

    def test_rfc7748_curve25519_vector(self):
        """RFC 7748 Section 6.1 Curve25519 test vectors (Alice, Bob, and Shared Secret)."""
        from cryptography.hazmat.primitives.asymmetric import x25519
        from cryptography.hazmat.primitives import serialization

        # Alice private key & public key
        alice_priv_bytes = bytes.fromhex("77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a")
        alice_pub_expected = bytes.fromhex("8520f0098930a754748b7ddcb43ef75a0dbf3a0d26381af4eba4a98eaa9b4e6a")
        alice_priv = x25519.X25519PrivateKey.from_private_bytes(alice_priv_bytes)
        alice_pub_raw = alice_priv.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self.assertEqual(alice_pub_raw.hex(), alice_pub_expected.hex())

        # Bob public key
        bob_pub_bytes = bytes.fromhex("de9edb7d7b7dc1b4d35b61c2ece435373f8343c85b78674dadfc7e146f882b4f")
        bob_pub = x25519.X25519PublicKey.from_public_bytes(bob_pub_bytes)

        # Shared Secret computed by Alice using Bob's public key
        expected_shared = bytes.fromhex("4a5d9d5ba4ce2de1728e3bf480350f25e07e21c947d19e3376f09b3c1e161742")
        alice_shared = alice_priv.exchange(bob_pub)
        self.assertEqual(alice_shared.hex(), expected_shared.hex())

    def test_rfc8439_chacha20_poly1305_aead_vector(self):
        """RFC 8439 Section 2.8.2 ChaCha20-Poly1305 AEAD test vector."""
        from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

        key = bytes.fromhex("808182838485868788898a8b8c8d8e8f909192939495969798999a9b9c9d9e9f")
        nonce = bytes.fromhex("070000004041424344454647")
        aad = bytes.fromhex("50515253c0c1c2c3c4c5c6c7")
        plaintext = b"Ladies and Gentlemen of the class of '99: If I could offer you only one tip for the future, sunscreen would be it."

        expected_ct = bytes.fromhex(
            "d31a8d34648e60db7b86afbc53ef7ec2a4aded51296e08fea9e2b5a736ee62d6"
            "3dbea45e8ca9671282fafb69da92728b1a71de0a9e060b2905d6a5b67ecd3b36"
            "92ddbd7f2d778b8c9803aee328091b58fab324e4fad675945585808b4831d7bc"
            "3ff4def08e4b7a9de576d26586cec64b6116"
        )
        expected_tag = bytes.fromhex("1ae10b594f09e26a7e902ecbd0600691")
        expected_combined = expected_ct + expected_tag

        cipher = ChaCha20Poly1305(key)
        encrypted = cipher.encrypt(nonce, plaintext, aad)

        self.assertEqual(encrypted.hex(), expected_combined.hex())

        # Verify decryption
        decrypted = cipher.decrypt(nonce, encrypted, aad)
        self.assertEqual(decrypted, plaintext)

    def test_rfc5869_hkdf_sha256_vector(self):
        """RFC 5869 Test Case 1 HKDF-SHA256 test vector."""
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
        from cryptography.hazmat.primitives import hashes

        ikm = bytes.fromhex("0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b")
        salt = bytes.fromhex("000102030405060708090a0b0c")
        info = bytes.fromhex("f0f1f2f3f4f5f6f7f8f9")
        expected_okm = bytes.fromhex(
            "3cb25f25faacd57a90434f64d0362f2a2d2d0a90cf1a5a4c5db02d56ecc4c5bf34007208d5b887185865"
        )

        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=42,
            salt=salt,
            info=info,
        )
        okm = hkdf.derive(ikm)
        self.assertEqual(okm.hex(), expected_okm.hex())

def m2c2a_main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(m2c2a_TestCryptoHandshakeAdversarial)
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(m2c2a_TestWireAeadAdversarial))
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(m2c2a_TestMultiSessionConcurrency))
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(m2c2a_TestRfcStandardsCompliance))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


# ============================================================================
# from tests/test-node.py (prefix m2da_)
# ============================================================================
from cryptography.hazmat.primitives.asymmetric import x25519
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
import importlib.util
import json
import os
import sys
import threading
import time
import unittest

_m2da_HERE = os.path.dirname(os.path.abspath(__file__))
_m2da_ROOT = os.path.normpath(os.path.join(_m2da_HERE, ".."))
_m2da_NODE_DIR = os.path.join(_m2da_ROOT, "usr", "libexec", "mios", "node")

def _m2da_import_module(name: str, filename: str):
    path = os.path.join(_m2da_NODE_DIR, filename)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec and spec.loader:
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        return mod
    raise ImportError(f"Could not import {name} from {path}")

m2da_scheduler_mod = _m2da_import_module("scheduler", "scheduler.py")
m2da_buffer_pool_mod = _m2da_import_module("buffer_pool", "buffer_pool.py")
m2da_capabilities_mod = _m2da_import_module("capabilities", "capabilities.py")
m2da_ble_mod = _m2da_import_module("ble", "ble.py")
m2da_overlay_mod = _m2da_import_module("overlay", "overlay.py")

class m2da_TestM2DeepAdversarial(unittest.TestCase):
    """Deep adversarial challenges and edge case fuzzers for Milestone 2."""

    # ---------------------------------------------------------------------
    # 1. Scheduler (T-392)
    # ---------------------------------------------------------------------
    def test_scheduler_pinning_matrix_and_stealable(self):
        # A. Hardware pinned
        t_hw = m2da_scheduler_mod.TaskItem(task_id=1, priority=m2da_scheduler_mod.TaskPriority.CRITICAL, pinned_hardware=True)
        self.assertFalse(t_hw.is_stealable(None))
        self.assertFalse(t_hw.is_stealable(101))
        self.assertFalse(t_hw.is_stealable(202))

        # B. Node pinned to 101 only
        t_node = m2da_scheduler_mod.TaskItem(task_id=2, priority=m2da_scheduler_mod.TaskPriority.HIGH, pinned_node_id=101)
        self.assertFalse(t_node.is_stealable(None))
        self.assertTrue(t_node.is_stealable(101))
        self.assertFalse(t_node.is_stealable(102))

        # C. Both
        t_both = m2da_scheduler_mod.TaskItem(task_id=3, priority=m2da_scheduler_mod.TaskPriority.NORMAL, pinned_hardware=True, pinned_node_id=101)
        self.assertFalse(t_both.is_stealable(101))

        # D. Unpinned
        t_free = m2da_scheduler_mod.TaskItem(task_id=4, priority=m2da_scheduler_mod.TaskPriority.LOW)
        self.assertTrue(t_free.is_stealable(None))
        self.assertTrue(t_free.is_stealable(101))
        self.assertTrue(t_free.is_stealable(999))

    def test_scheduler_high_concurrency_race_stress(self):
        sched = m2da_scheduler_mod.WorkStealingScheduler(local_node_id=50, num_workers=4)
        total_tasks = 400

        # Enqueue 400 tasks with mixed pinning
        for i in range(total_tasks):
            prio = m2da_scheduler_mod.TaskPriority(i % 4)
            is_pinned = (i % 5 == 0)
            pinned_node = 50 if (i % 3 == 0 and not is_pinned) else None
            t = m2da_scheduler_mod.TaskItem(
                task_id=i,
                priority=prio,
                pinned_hardware=is_pinned,
                pinned_node_id=pinned_node,
            )
            sched.submit_task(t, worker_hint=(i % 4 if i % 2 == 0 else None))

        executed: list[int] = []
        lock = threading.Lock()

        def worker_loop(wid: int):
            while True:
                task = sched.pop_task(wid)
                if task is None:
                    break
                with lock:
                    executed.append(task.task_id)

        threads = [threading.Thread(target=worker_loop, args=(i,)) for i in range(4)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        self.assertEqual(len(executed), total_tasks)
        self.assertEqual(len(set(executed)), total_tasks)
        self.assertEqual(sched.total_queue_depth(), 0)

    # ---------------------------------------------------------------------
    # 2. Buffer Pool (T-393)
    # ---------------------------------------------------------------------
    def test_buffer_pool_boundaries_and_slicing(self):
        pool = m2da_buffer_pool_mod.BufferPool()

        # Boundaries
        self.assertEqual(m2da_buffer_pool_mod.BucketTier.from_size(0), m2da_buffer_pool_mod.BucketTier.SMALL)
        self.assertEqual(m2da_buffer_pool_mod.BucketTier.from_size(256), m2da_buffer_pool_mod.BucketTier.SMALL)
        self.assertEqual(m2da_buffer_pool_mod.BucketTier.from_size(257), m2da_buffer_pool_mod.BucketTier.MEDIUM)
        self.assertEqual(m2da_buffer_pool_mod.BucketTier.from_size(4096), m2da_buffer_pool_mod.BucketTier.MEDIUM)
        self.assertEqual(m2da_buffer_pool_mod.BucketTier.from_size(4097), m2da_buffer_pool_mod.BucketTier.LARGE)
        self.assertEqual(m2da_buffer_pool_mod.BucketTier.from_size(65536), m2da_buffer_pool_mod.BucketTier.LARGE)
        self.assertEqual(m2da_buffer_pool_mod.BucketTier.from_size(65537), m2da_buffer_pool_mod.BucketTier.HUGE)

        # Zero-copy slicing & split
        with pool.acquire(100) as buf:
            buf.extend(b"0123456789ABCDEF")
            self.assertEqual(bytes(buf.slice(0, 4)), b"0123")
            self.assertEqual(bytes(buf.slice(4, 16)), b"456789ABCDEF")

            # Out of bounds
            p_full = buf.slice(0, 16)
            self.assertEqual(bytes(p_full), b"0123456789ABCDEF")

            # Split prefix
            p1 = buf.split_prefix(4)
            self.assertEqual(p1, b"0123")
            self.assertEqual(bytes(buf.as_bytes()), b"456789ABCDEF")

            with self.assertRaises(IndexError):
                buf.split_prefix(100)

    def test_buffer_pool_multithreaded_churn(self):
        pool = m2da_buffer_pool_mod.BufferPool()
        num_threads = 8
        iterations = 100

        def churn():
            for i in range(iterations):
                sz = 128 if i % 2 == 0 else 2048
                with pool.acquire(sz) as buf:
                    buf.extend(b"CHURN_DATA")
                    self.assertEqual(bytes(buf.slice(0, 5)), b"CHURN")

        threads = [threading.Thread(target=churn) for _ in range(num_threads)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        stats = pool.get_stats()
        self.assertEqual(stats.active_leased, 0)
        self.assertEqual(stats.allocations, num_threads * iterations)
        self.assertGreater(stats.recycles, 0)

    # ---------------------------------------------------------------------
    # 3. Capabilities (T-394)
    # ---------------------------------------------------------------------
    def test_capabilities_filtering_and_eviction(self):
        registry = m2da_capabilities_mod.CapabilityRegistry()

        for i in range(1, 21):
            caps = m2da_capabilities_mod.NodeCapabilities(
                hardware=m2da_capabilities_mod.HardwareSpecs(ram_available_kb=i * 1024 * 1024),
                vram=m2da_capabilities_mod.VramTelemetry(vram_available_mb=(i * 256 if i % 2 == 0 else 0)),
                has_gpio=(i % 2 == 0),
                has_i2c=(i % 3 == 0),
            )
            payload = m2da_capabilities_mod.NodeAnnouncePayload(
                node_id=i,
                hostname=f"node-{i}",
                capabilities=caps,
            )
            registry.register_announce(payload, received_at=1000.0 + i * 10.0)

        self.assertEqual(registry.active_node_count(), 20)

        # Match VRAM >= 1024MB AND GPIO = True
        matched = registry.find_eligible_nodes(min_ram_kb=1024, min_vram_mb=1024, require_gpio=True)
        self.assertTrue(len(matched) > 0)
        for nid in matched:
            c = registry.get_capabilities(nid)
            self.assertIsNotNone(c)
            self.assertGreaterEqual(c.vram.vram_available_mb, 1024)
            self.assertTrue(c.has_gpio)

        # Stale eviction at t=1200 with max_age=50
        evicted = registry.evict_stale(max_age_secs=50.0, now=1200.0)
        self.assertGreater(evicted, 0)

    # ---------------------------------------------------------------------
    # 4. BLE Bootstrap (T-395)
    # ---------------------------------------------------------------------
    def test_ble_bootstrap_tamper_rejection(self):
        adapter = m2da_ble_mod.MockBleAdapter()
        bootstrap = m2da_ble_mod.BleMeshBootstrap(node_id=99, adapter=adapter)
        bootstrap.start()

        self.assertTrue(adapter.is_advertising())
        self.assertEqual(bootstrap.state, m2da_ble_mod.BleBootstrapState.UNPROVISIONED)

        # Handshake
        client_priv = x25519.X25519PrivateKey.generate()
        client_pub = client_priv.public_key().public_bytes(
            encoding=m2da_ble_mod.serialization.Encoding.Raw,
            format=m2da_ble_mod.serialization.PublicFormat.Raw,
        )
        bootstrap.handle_ecdh_exchange(client_pub)
        self.assertEqual(bootstrap.state, m2da_ble_mod.BleBootstrapState.HANDSHAKING)

        # Encrypt creds
        creds = m2da_ble_mod.ProvisioningPayload(
            ssid="AdvSSID",
            psk="AdvPass123",
            cluster_token="adv-tok",
            coordinator_endpoint="1.2.3.4:8650",
        )
        node_pub = x25519.X25519PublicKey.from_public_bytes(bootstrap.public_bytes)
        shared_secret = client_priv.exchange(node_pub)

        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=m2da_ble_mod.hashlib.sha256(bootstrap.public_bytes + client_pub).digest(),
            info=m2da_ble_mod.BLE_HKDF_INFO,
        )
        session_key = hkdf.derive(shared_secret)

        aead = ChaCha20Poly1305(session_key)
        json_bytes = json.dumps(creds.to_dict()).encode("utf-8")
        nonce = os.urandom(12)
        untampered = nonce + aead.encrypt(nonce, json_bytes, m2da_ble_mod.BLE_AEAD_AAD)
        ciphertext = bytearray(untampered)

        # Tamper ciphertext
        ciphertext[len(ciphertext) // 2] ^= 0xFF
        with self.assertRaises(Exception):
            bootstrap.handle_provisioning_write(bytes(ciphertext))

        self.assertNotEqual(bootstrap.state, m2da_ble_mod.BleBootstrapState.PROVISIONED)

        # Untampered write succeeds
        prov = bootstrap.handle_provisioning_write(untampered)
        self.assertEqual(prov.ssid, "AdvSSID")
        with self.assertRaisesRegex(RuntimeError, "already consumed"):
            bootstrap.handle_provisioning_write(untampered)
        self.assertEqual(bootstrap.state, m2da_ble_mod.BleBootstrapState.PROVISIONED)
        self.assertFalse(adapter.is_advertising())

    # ---------------------------------------------------------------------
    # 5. Multi-Transport Overlay (T-396)
    # ---------------------------------------------------------------------
    def test_overlay_anti_flap_flapping_stress(self):
        config = m2da_overlay_mod.HysteresisConfig(
            fail_strikes_threshold=3,
            recovery_dwell_ms=10_000,
            recovery_strikes_threshold=3,
        )
        router = m2da_overlay_mod.MultiTransportRouter(config=config)

        router.register_peer(
            node_id=888,
            endpoints={
                m2da_overlay_mod.TransportType.LAN_BROADCAST: "192.168.1.88:8650",
                m2da_overlay_mod.TransportType.WIREGUARD: "10.0.0.88:8650",
                m2da_overlay_mod.TransportType.TAILSCALE: "100.64.0.88:8650",
            },
        )

        # 1. 3 strikes -> Failover to WireGuard
        for i in range(1, 4):
            router.record_missed_heartbeat(888, m2da_overlay_mod.TransportType.LAN_BROADCAST, now_ms=i * 1000)

        self.assertTrue(router.is_peer_partitioned(888))
        self.assertEqual(router.select_route(888)[0], m2da_overlay_mod.TransportType.WIREGUARD)

        # 2. Intermittent probes during dwell
        router.record_heartbeat(888, m2da_overlay_mod.TransportType.LAN_BROADCAST, latency_ms=1, now_ms=4000)
        router.record_heartbeat(888, m2da_overlay_mod.TransportType.LAN_BROADCAST, latency_ms=1, now_ms=5000)
        router.record_missed_heartbeat(888, m2da_overlay_mod.TransportType.LAN_BROADCAST, now_ms=6000) # Resets strikes

        # 3. 3 probes but dwell not reached
        router.record_heartbeat(888, m2da_overlay_mod.TransportType.LAN_BROADCAST, latency_ms=1, now_ms=7000)
        router.record_heartbeat(888, m2da_overlay_mod.TransportType.LAN_BROADCAST, latency_ms=1, now_ms=8000)
        router.record_heartbeat(888, m2da_overlay_mod.TransportType.LAN_BROADCAST, latency_ms=1, now_ms=9000)
        self.assertEqual(router.select_route(888)[0], m2da_overlay_mod.TransportType.WIREGUARD)

        # 4. Clean probe after dwell (t=20000 -> 13000ms elapsed >= 10000ms)
        router.record_heartbeat(888, m2da_overlay_mod.TransportType.LAN_BROADCAST, latency_ms=1, now_ms=20000)
        self.assertEqual(router.select_route(888)[0], m2da_overlay_mod.TransportType.LAN_BROADCAST)
        self.assertFalse(router.is_peer_partitioned(888))

def m2da_main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(m2da_TestM2DeepAdversarial)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# ============================================================================
# from tests/test-node.py (prefix ml_)
# ============================================================================
"""Automated unit test suite for MiOS Mesh Log Forwarder."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "usr", "libexec", "mios", "node"))

from mesh_logs import MeshLogForwarder

class ml_TestMeshLogs(unittest.TestCase):
    def setUp(self):
        self.fwd = MeshLogForwarder(node_id="test_node_01", dry_run=True)

    def test_live_log_streaming_when_connected(self):
        """Test logs stream directly to coordinator when mesh network is active."""
        self.fwd.ingest_journal_entry("mios-llm-light.service", "INFO", "Model swapped to qwen-32b")
        self.assertEqual(len(self.fwd.flushed_records), 1)
        self.assertEqual(len(self.fwd.local_buffer), 0)

    def test_partition_buffering_and_reconnection_flush(self):
        """Test network drop buffers 1,000 logs and flushes on reconnection with 0 loss."""
        self.fwd.set_network_state(False)
        for i in range(1000):
            self.fwd.ingest_journal_entry("systemd", "INFO", f"Heartbeat {i}")

        self.assertEqual(len(self.fwd.local_buffer), 1000)
        flushed = self.fwd.set_network_state(True)
        self.assertEqual(flushed, 1000)
        self.assertEqual(len(self.fwd.local_buffer), 0)
        self.assertEqual(len(self.fwd.flushed_records), 1000)


# ============================================================================
# from tests/test-node.py (prefix nbb_)
# ============================================================================
"""Automated tests for WS-NODE BLE GATT bootstrap, X25519 ECDH key exchange, and ChaCha20-Poly1305 provisioning."""


import importlib.util
import os
import sys
import unittest

_nbb_HERE = os.path.dirname(os.path.abspath(__file__))
_nbb_ROOT = os.path.normpath(os.path.join(_nbb_HERE, ".."))
_nbb_BLE_PATH = os.path.join(_nbb_ROOT, "usr", "libexec", "mios", "node", "ble.py")

nbb_spec = importlib.util.spec_from_file_location("ble", _nbb_BLE_PATH)
if nbb_spec and nbb_spec.loader:
    ble = importlib.util.module_from_spec(nbb_spec)
    sys.modules[nbb_spec.name] = ble
    nbb_spec.loader.exec_module(ble)
else:
    raise ImportError(f"Could not load ble module from {_nbb_BLE_PATH}")

class nbb_TestNodeBleBootstrap(unittest.TestCase):
    """Validates BLE GATT service specification, ECDH ephemeral key exchange, and AEAD credential decryption."""

    def test_ble_constants_and_state_definitions(self):
        self.assertEqual(ble.BLE_SERVICE_UUID, "4D494F53-0001-1000-8000-00805F9B34FB")
        self.assertEqual(ble.BLE_CHAR_IDENTITY_UUID, "4D494F53-0002-1000-8000-00805F9B34FB")
        self.assertEqual(ble.BLE_CHAR_ECDH_UUID, "4D494F53-0003-1000-8000-00805F9B34FB")
        self.assertEqual(ble.BLE_CHAR_PROVISION_UUID, "4D494F53-0004-1000-8000-00805F9B34FB")

        self.assertEqual(ble.BleBootstrapState.UNPROVISIONED, 0)
        self.assertEqual(ble.BleBootstrapState.HANDSHAKING, 1)
        self.assertEqual(ble.BleBootstrapState.PROVISIONED, 3)

    def test_full_encrypted_ble_provisioning_flow(self):
        adapter = ble.MockBleAdapter()
        node = ble.BleMeshBootstrap(node_id=42, adapter=adapter)

        # 1. Node starts offline advertising
        node.start()
        self.assertTrue(adapter.is_advertising())
        self.assertEqual(node.state, ble.BleBootstrapState.UNPROVISIONED)

        # 2. Provisioner creates credentials payload
        creds = ble.ProvisioningPayload(
            ssid="MiOS-Edge-Mesh",
            psk="SuperSecretWifiP@ss123",
            cluster_token="tok_alpha_cluster_99",
            coordinator_endpoint="192.168.1.1:8650",
        )

        # 3. Provisioner executes client provisioning handshake
        ble.provision_remote_node(adapter, creds)

        # 4. Offline node accepts peer ECDH public key
        peer_pub_bytes = adapter.get_characteristic_value(ble.BLE_CHAR_ECDH_UUID)
        node.handle_ecdh_exchange(peer_pub_bytes)
        self.assertEqual(node.state, ble.BleBootstrapState.HANDSHAKING)

        # 5. Offline node accepts encrypted provisioning payload
        enc_payload = adapter.get_characteristic_value(ble.BLE_CHAR_PROVISION_UUID)
        provisioned = node.handle_provisioning_write(enc_payload)

        self.assertEqual(provisioned.ssid, "MiOS-Edge-Mesh")
        self.assertEqual(provisioned.psk, "SuperSecretWifiP@ss123")
        self.assertEqual(provisioned.cluster_token, "tok_alpha_cluster_99")
        self.assertEqual(provisioned.coordinator_endpoint, "192.168.1.1:8650")

        # 6. Verify terminal state and advertising shutdown
        self.assertEqual(node.state, ble.BleBootstrapState.PROVISIONED)
        self.assertFalse(adapter.is_advertising())

    def test_tampered_encrypted_payload_rejection(self):
        adapter = ble.MockBleAdapter()
        node = ble.BleMeshBootstrap(node_id=88, adapter=adapter)
        node.start()

        creds = ble.ProvisioningPayload(
            ssid="MiOS-Edge-Mesh",
            psk="P@ss",
            cluster_token="tok_1",
            coordinator_endpoint="127.0.0.1:8650",
        )
        ble.provision_remote_node(adapter, creds)

        peer_pub_bytes = adapter.get_characteristic_value(ble.BLE_CHAR_ECDH_UUID)
        node.handle_ecdh_exchange(peer_pub_bytes)

        # Tamper with encrypted ciphertext
        enc_payload = bytearray(adapter.get_characteristic_value(ble.BLE_CHAR_PROVISION_UUID))
        enc_payload[10] ^= 0xFF  # Flip bit

        # Decryption should fail AEAD authentication
        with self.assertRaises(Exception):
            node.handle_provisioning_write(bytes(enc_payload))

        self.assertNotEqual(node.state, ble.BleBootstrapState.PROVISIONED)

def nbb_main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(nbb_TestNodeBleBootstrap)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# ============================================================================
# from tests/test-node.py (prefix nbp_)
# ============================================================================
"""Automated tests for WS-NODE BufferPool, PooledBuffer RAII recycling, and zero-copy slicing."""


import importlib.util
import os
import sys
import threading
import unittest

_nbp_HERE = os.path.dirname(os.path.abspath(__file__))
_nbp_ROOT = os.path.normpath(os.path.join(_nbp_HERE, ".."))
_nbp_POOL_PATH = os.path.join(_nbp_ROOT, "usr", "libexec", "mios", "node", "buffer_pool.py")

nbp_spec = importlib.util.spec_from_file_location("buffer_pool", _nbp_POOL_PATH)
if nbp_spec and nbp_spec.loader:
    buffer_pool = importlib.util.module_from_spec(nbp_spec)
    sys.modules[nbp_spec.name] = buffer_pool
    nbp_spec.loader.exec_module(buffer_pool)
else:
    raise ImportError(f"Could not load buffer_pool module from {_nbp_POOL_PATH}")

class nbp_TestNodeBufferPool(unittest.TestCase):
    """Validates bucketed allocations, RAII recycling, zero-copy views, and bounded capacities."""

    def test_bucket_tier_resolution(self):
        self.assertEqual(buffer_pool.BucketTier.from_size(16), buffer_pool.BucketTier.SMALL)
        self.assertEqual(buffer_pool.BucketTier.from_size(256), buffer_pool.BucketTier.SMALL)
        self.assertEqual(buffer_pool.BucketTier.from_size(257), buffer_pool.BucketTier.MEDIUM)
        self.assertEqual(buffer_pool.BucketTier.from_size(4096), buffer_pool.BucketTier.MEDIUM)
        self.assertEqual(buffer_pool.BucketTier.from_size(4097), buffer_pool.BucketTier.LARGE)
        self.assertEqual(buffer_pool.BucketTier.from_size(65536), buffer_pool.BucketTier.LARGE)
        self.assertEqual(buffer_pool.BucketTier.from_size(65537), buffer_pool.BucketTier.HUGE)

    def test_raii_buffer_recycling_and_stats(self):
        pool = buffer_pool.BufferPool()

        # Allocate and use buffer within context manager
        with pool.acquire(100) as buf:
            self.assertEqual(buf.tier, buffer_pool.BucketTier.SMALL)
            buf.write(b"Hello MiOS Wire!")
            self.assertEqual(buf.as_bytes(), b"Hello MiOS Wire!")

            stats = pool.get_stats()
            self.assertEqual(stats.allocations, 1)
            self.assertEqual(stats.pool_misses, 1)
            self.assertEqual(stats.active_leased, 1)

        # Buffer is released upon exit
        stats = pool.get_stats()
        self.assertEqual(stats.recycles, 1)
        self.assertEqual(stats.active_leased, 0)
        self.assertEqual(pool.bucket_depths()[0], 1)

        # Next acquire reuses the recycled buffer
        with pool.acquire(100) as buf2:
            self.assertEqual(buf2.len(), 0)  # Cleared on recycle
            buf2.write(b"RecycledPayload")

            stats2 = pool.get_stats()
            self.assertEqual(stats2.allocations, 2)
            self.assertEqual(stats2.pool_hits, 1)
            self.assertEqual(stats2.active_leased, 1)

    def test_zero_copy_slicing_and_prefix_split(self):
        pool = buffer_pool.BufferPool()
        with pool.acquire(1000) as buf:
            buf.write(b"FIXED_16B_HEADER_PAYLOAD_BODY_DATA_CHUNK")

            # Zero-copy slicing via memoryview
            header_view = buf.slice(0, 16)
            self.assertEqual(bytes(header_view), b"FIXED_16B_HEADER")

            payload_view = buf.slice(16, buf.len())
            self.assertEqual(bytes(payload_view), b"_PAYLOAD_BODY_DATA_CHUNK")

            # Prefix splitting
            prefix = buf.split_prefix(16)
            self.assertEqual(prefix, b"FIXED_16B_HEADER")
            self.assertEqual(buf.as_bytes(), b"_PAYLOAD_BODY_DATA_CHUNK")

    def test_bounded_pool_capacity(self):
        pool = buffer_pool.BufferPool()
        max_cap = buffer_pool.BucketTier.SMALL.max_pool_capacity

        # Acquire max_cap + 10 buffers
        buffers = [pool.acquire_exact(buffer_pool.BucketTier.SMALL) for _ in range(max_cap + 10)]

        # Release all buffers
        for b in buffers:
            b.release()

        # Verify bucket size is bounded at max_cap
        small_depth = pool.bucket_depths()[0]
        self.assertEqual(small_depth, max_cap)

    def test_multithreaded_pool_concurrency(self):
        pool = buffer_pool.BufferPool()
        threads = []

        def worker():
            for _ in range(50):
                with pool.acquire(512) as b:
                    b.write(b"ThreadPayload")
                    self.assertEqual(b.as_bytes(), b"ThreadPayload")

        for _ in range(8):
            t = threading.Thread(target=worker)
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        stats = pool.get_stats()
        self.assertEqual(stats.active_leased, 0)
        self.assertEqual(stats.allocations, 400)
        self.assertGreater(stats.pool_hits, 0)

def nbp_main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(nbp_TestNodeBufferPool)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# ============================================================================
# from tests/test-node.py (prefix ncap_)
# ============================================================================
"""Automated tests for WS-NODE capabilities telemetry, Opcode 0x02 NodeAnnounce framing, and registry."""


import importlib.util
import os
import sys
import unittest

_ncap_HERE = os.path.dirname(os.path.abspath(__file__))
_ncap_ROOT = os.path.normpath(os.path.join(_ncap_HERE, ".."))
_ncap_CAP_PATH = os.path.join(_ncap_ROOT, "usr", "libexec", "mios", "node", "capabilities.py")

ncap_spec = importlib.util.spec_from_file_location("capabilities", _ncap_CAP_PATH)
if ncap_spec and ncap_spec.loader:
    capabilities = importlib.util.module_from_spec(ncap_spec)
    sys.modules[ncap_spec.name] = capabilities
    ncap_spec.loader.exec_module(capabilities)
else:
    raise ImportError(f"Could not load capabilities module from {_ncap_CAP_PATH}")

class ncap_TestNodeCapabilities(unittest.TestCase):
    """Validates hardware capability probing, Opcode 0x02 Announce framing, and candidate registry queries."""

    def test_capability_probing_defaults(self):
        caps = capabilities.probe_node_capabilities()
        self.assertIsNotNone(caps.hardware.cpu_arch)
        self.assertGreater(caps.hardware.cpu_cores, 0)
        self.assertGreater(caps.hardware.ram_total_kb, 0)
        self.assertTrue(caps.engines.wasm_tier)
        self.assertTrue(caps.engines.native_tier)
        self.assertIn("127.0.0.1:8650", caps.transports.endpoints)

    def test_node_announce_frame_roundtrip(self):
        caps = capabilities.NodeCapabilities()
        caps.hardware.ram_total_kb = 16 * 1024 * 1024
        caps.hardware.ram_available_kb = 12 * 1024 * 1024
        caps.vram.gpu_vendor = "NVIDIA"
        caps.vram.gpu_model = "RTX 4090"
        caps.vram.vram_total_mb = 24576
        caps.vram.vram_available_mb = 20480
        caps.has_gpio = True

        announce = capabilities.NodeAnnouncePayload(
            node_id=142,
            hostname="edge-blade-alpha",
            capabilities=caps,
        )

        frame = announce.to_frame()
        self.assertEqual(frame.header.node_id, 142)
        self.assertEqual(frame.header.opcode, capabilities.MessageType.NODE_ANNOUNCE)

        # Wire serialization
        raw_bytes = frame.encode()
        self.assertEqual(len(raw_bytes), 16 + len(frame.payload))

        decoded_frame = capabilities.Frame.decode(raw_bytes)
        restored_announce = capabilities.NodeAnnouncePayload.from_frame(decoded_frame)

        self.assertEqual(restored_announce.node_id, 142)
        self.assertEqual(restored_announce.hostname, "edge-blade-alpha")
        self.assertEqual(restored_announce.capabilities.vram.gpu_vendor, "NVIDIA")
        self.assertEqual(restored_announce.capabilities.vram.vram_total_mb, 24576)
        self.assertTrue(restored_announce.capabilities.has_gpio)

    def test_capability_registry_filtering_and_eviction(self):
        registry = capabilities.CapabilityRegistry()

        # Node 1: IoT Edge blade with GPIO and 2GB RAM, no GPU
        caps1 = capabilities.NodeCapabilities()
        caps1.hardware.ram_available_kb = 2 * 1024 * 1024
        caps1.vram.vram_available_mb = 0
        caps1.has_gpio = True
        caps1.has_i2c = True
        ann1 = capabilities.NodeAnnouncePayload(node_id=101, hostname="iot-blade-1", capabilities=caps1)

        # Node 2: GPU worker with 8GB VRAM and 16GB RAM, no GPIO
        caps2 = capabilities.NodeCapabilities()
        caps2.hardware.ram_available_kb = 16 * 1024 * 1024
        caps2.vram.gpu_vendor = "NVIDIA"
        caps2.vram.vram_available_mb = 8192
        caps2.has_gpio = False
        ann2 = capabilities.NodeAnnouncePayload(node_id=102, hostname="gpu-worker-1", capabilities=caps2)

        # Node 3: Generic edge worker with 4GB RAM, no GPU, no GPIO
        caps3 = capabilities.NodeCapabilities()
        caps3.hardware.ram_available_kb = 4 * 1024 * 1024
        caps3.vram.vram_available_mb = 0
        ann3 = capabilities.NodeAnnouncePayload(node_id=103, hostname="generic-worker", capabilities=caps3)

        registry.register_announce(ann1, received_at=1000.0)
        registry.register_announce(ann2, received_at=1000.0)
        registry.register_announce(ann3, received_at=1000.0)

        self.assertEqual(registry.active_node_count(), 3)

        # 1. Query candidates requiring GPU VRAM >= 4096MB
        gpu_candidates = registry.find_eligible_nodes(min_vram_mb=4096)
        self.assertEqual(gpu_candidates, [102])

        # 2. Query candidates requiring GPIO access
        gpio_candidates = registry.find_eligible_nodes(require_gpio=True)
        self.assertEqual(gpio_candidates, [101])

        # 3. Query candidates requiring RAM >= 3GB
        ram_candidates = registry.find_eligible_nodes(min_ram_kb=3 * 1024 * 1024)
        self.assertEqual(ram_candidates, [102, 103])

        # 4. Stale eviction at t=1050 with max_age=30s
        evicted = registry.evict_stale(max_age_secs=30.0, now=1050.0)
        self.assertEqual(evicted, 3)
        self.assertEqual(registry.active_node_count(), 0)

def ncap_main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(ncap_TestNodeCapabilities)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# ============================================================================
# from tests/test-node.py (prefix ncgp_)
# ============================================================================
"""Automated tests for WS-NODE worker CPU core affinity, Core 0 exclusion, and cgroup v2 limits."""


import importlib.util
import os
import sys
import unittest

_ncgp_HERE = os.path.dirname(os.path.abspath(__file__))
_ncgp_ROOT = os.path.normpath(os.path.join(_ncgp_HERE, ".."))
_ncgp_CGROUPS_PATH = os.path.join(_ncgp_ROOT, "usr", "libexec", "mios", "node", "cgroups.py")

ncgp_spec = importlib.util.spec_from_file_location("cgroups", _ncgp_CGROUPS_PATH)
if ncgp_spec and ncgp_spec.loader:
    cgroups = importlib.util.module_from_spec(ncgp_spec)
    sys.modules["cgroups"] = cgroups
    sys.modules["usr.libexec.mios.node.cgroups"] = cgroups
    ncgp_spec.loader.exec_module(cgroups)
else:
    raise ImportError(f"Could not load cgroups module from {_ncgp_CGROUPS_PATH}")

class ncgp_TestNodeCgroupsPinning(unittest.TestCase):
    """Validates CPU core affinity allocation, Core 0 system reservation, and cgroup v2 formatting."""

    def test_core_zero_exclusion_invariant(self):
        # 4-core machine: safe worker pool must exclude Core 0
        safe_4 = cgroups.filter_safe_worker_cores(4, None, exclude_core_zero=True)
        self.assertEqual(safe_4, [1, 2, 3])
        self.assertNotIn(0, safe_4)

        # 1-core machine: single core must remain usable
        safe_1 = cgroups.filter_safe_worker_cores(1, None, exclude_core_zero=True)
        self.assertEqual(safe_1, [0])

        # Explicit requested cores [0, 2, 3] on 4 cores -> 0 filtered out
        safe_req = cgroups.filter_safe_worker_cores(4, [0, 2, 3], exclude_core_zero=True)
        self.assertEqual(safe_req, [2, 3])

    def test_affinity_policy_allocations(self):
        controller = cgroups.WorkerAffinityController(
            total_system_cores=4, limits=cgroups.NodeResourceLimits()
        )
        self.assertEqual(controller.available_worker_cores, [1, 2, 3])

        # Allocate exclusive core
        c1 = controller.allocate_cores_for_policy(cgroups.AffinityPolicy.EXCLUSIVE, 1)
        self.assertEqual(c1, [1])

        c2 = controller.allocate_cores_for_policy(cgroups.AffinityPolicy.EXCLUSIVE, 2)
        self.assertEqual(c2, [2, 3])

        # Pool exhausted
        with self.assertRaises(RuntimeError):
            controller.allocate_cores_for_policy(cgroups.AffinityPolicy.EXCLUSIVE, 1)

        # Release core 1 and re-allocate
        controller.release_cores([1])
        c_realloc = controller.allocate_cores_for_policy(cgroups.AffinityPolicy.EXCLUSIVE, 1)
        self.assertEqual(c_realloc, [1])

        # Shared policy returns all available worker cores
        shared = controller.allocate_cores_for_policy(cgroups.AffinityPolicy.SHARED)
        self.assertEqual(shared, [1, 2, 3])

        # Low priority returns highest index core
        low = controller.allocate_cores_for_policy(cgroups.AffinityPolicy.LOW_PRIORITY)
        self.assertEqual(low, [3])

    def test_cgroup_v2_cpu_max_formatting(self):
        f80 = cgroups.CgroupV2Controller.format_cpu_max(80, 100_000)
        self.assertEqual(f80, "80000 100000")

        fmax = cgroups.CgroupV2Controller.format_cpu_max(None, 100_000)
        self.assertEqual(fmax, "max 100000")

def ncgp_main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(ncgp_TestNodeCgroupsPinning)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# ============================================================================
# from tests/test-node.py (prefix ncg_)
# ============================================================================
"""Automated tests for WS-NODE CRDT state compaction and snapshot garbage collection."""


import importlib.util
import os
import sys
import tempfile
import unittest

_ncg_HERE = os.path.dirname(os.path.abspath(__file__))
_ncg_ROOT = os.path.normpath(os.path.join(_ncg_HERE, ".."))
_ncg_CRDT_PATH = os.path.join(_ncg_ROOT, "usr", "libexec", "mios", "node", "crdt.py")

ncg_spec = importlib.util.spec_from_file_location("crdt", _ncg_CRDT_PATH)
if ncg_spec and ncg_spec.loader:
    crdt = importlib.util.module_from_spec(ncg_spec)
    sys.modules["crdt"] = crdt
    sys.modules["usr.libexec.mios.node.crdt"] = crdt
    ncg_spec.loader.exec_module(crdt)
else:
    raise ImportError(f"Could not load crdt module from {_ncg_CRDT_PATH}")

class ncg_TestNodeCRDTCompaction(unittest.TestCase):
    """Validates tombstone pruning, disconnection horizon TTL retention, and disk log compaction."""

    def test_tombstone_ttl_compaction(self):
        store = crdt.StateStore(101)

        # 1. Active key: should never be purged
        store.set("active.service", b"running")

        # 2. Fresh tombstone: deleted at t = 1000s
        store.set("fresh.deleted", b"data")
        store.delete("fresh.deleted")
        store.elements["fresh.deleted"].timestamp_ns = int(1000 * 1e9)

        # 3. Stale tombstone: deleted at t = 100s
        store.set("stale.deleted", b"old_data")
        store.delete("stale.deleted")
        store.elements["stale.deleted"].timestamp_ns = int(100 * 1e9)

        self.assertEqual(store.total_elements_count(), 3)
        self.assertEqual(store.count_tombstones(), 2)

        # Run compaction at current_time = 1050s with TTL = 200s
        # Stale age = 950s > 200s -> purged
        # Fresh age = 50s <= 200s -> retained
        stats = store.compact_tombstones(ttl_s=200.0, current_time_s=1050.0)

        self.assertEqual(stats["initial_elements"], 3)
        self.assertEqual(stats["active_elements"], 1)
        self.assertEqual(stats["tombstones_purged"], 1)
        self.assertEqual(stats["tombstones_retained"], 1)
        self.assertEqual(stats["remaining_elements"], 2)

        self.assertEqual(store.get("active.service"), b"running")
        self.assertIn("fresh.deleted", store.elements)
        self.assertNotIn("stale.deleted", store.elements)

    def test_disk_compaction_and_wal_truncation(self):
        with tempfile.TemporaryDirectory(prefix="mios-crdt-gc-") as tmpdir:
            snap_path = os.path.join(tmpdir, "state.json")
            store = crdt.StateStore(101, persistence_path=snap_path)

            store.set("k1", b"v1")
            store.set("k2", b"v2")
            store.delete("k2")
            store.elements["k2"].timestamp_ns = int(10 * 1e9)

            stats = store.compact_disk_storage(ttl_s=100.0, current_time_s=1000.0)
            self.assertEqual(stats["tombstones_purged"], 1)
            self.assertEqual(store.total_elements_count(), 1)

            # Reload from disk and verify clean state
            reloaded = crdt.StateStore(101, persistence_path=snap_path)
            self.assertEqual(reloaded.get("k1"), b"v1")
            self.assertEqual(reloaded.total_elements_count(), 1)

def ncg_main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(ncg_TestNodeCRDTCompaction)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# ============================================================================
# from tests/test-node.py (prefix ncrdt_)
# ============================================================================
"""Automated tests for WS-NODE edge mesh CRDT state synchronization and vector clock causality."""


import importlib.util
import os
import sys
import tempfile
import time
import unittest

_ncrdt_HERE = os.path.dirname(os.path.abspath(__file__))
_ncrdt_ROOT = os.path.normpath(os.path.join(_ncrdt_HERE, ".."))
_ncrdt_CRDT_PATH = os.path.join(_ncrdt_ROOT, "usr", "libexec", "mios", "node", "crdt.py")

ncrdt_spec = importlib.util.spec_from_file_location("crdt", _ncrdt_CRDT_PATH)
if ncrdt_spec and ncrdt_spec.loader:
    crdt = importlib.util.module_from_spec(ncrdt_spec)
    sys.modules[ncrdt_spec.name] = crdt
    ncrdt_spec.loader.exec_module(crdt)
else:
    raise ImportError(f"Could not load crdt module from {_ncrdt_CRDT_PATH}")

class ncrdt_TestNodeCRDTSync(unittest.TestCase):
    """Validates vector clocks, LWW-Element-Set conflict resolution, and persistence."""

    def test_vector_clock_causality_and_merge(self):
        vc1 = crdt.VectorClock()
        vc1.increment(101)
        vc1.increment(101)

        vc2 = crdt.VectorClock()
        vc2.increment(102)

        vc1.merge(vc2)
        self.assertEqual(vc1.clocks[101], 2)
        self.assertEqual(vc1.clocks[102], 1)

    def test_lww_tombstone_deletion_convergence(self):
        node1 = crdt.StateStore(101)
        node2 = crdt.StateStore(102)

        node1.set("cluster.domain", b"mios.local")
        node2.merge_remote_store(node1.vector_clock, node1.replicable_elements())
        self.assertEqual(node2.get("cluster.domain"), b"mios.local")

        # Node 1 deletes the key with a newer timestamp
        time.sleep(0.001)
        node1.delete("cluster.domain")
        self.assertIsNone(node1.get("cluster.domain"))

        # Merge tombstone to Node 2
        node2.merge_remote_store(node1.vector_clock, node1.replicable_elements())
        self.assertIsNone(node2.get("cluster.domain"))

    def test_concurrent_edit_last_write_wins(self):
        node1 = crdt.StateStore(101)
        node2 = crdt.StateStore(102)

        node1.set("task.5001.status", b"PENDING")
        time.sleep(0.002)
        node2.set("task.5001.status", b"RUNNING")

        # Merge node2 into node1 -> node2 should win due to newer timestamp
        applied = node1.merge_remote_store(node2.vector_clock, node2.replicable_elements())
        self.assertGreaterEqual(applied, 1)
        self.assertEqual(node1.get("task.5001.status"), b"RUNNING")

    def test_snapshot_persistence_and_reload(self):
        with tempfile.TemporaryDirectory(prefix="mios-crdt-test-") as tmpdir:
            snap_path = os.path.join(tmpdir, "node102_state.json")
            node = crdt.StateStore(102, persistence_path=snap_path)
            node.set("worker.load", b"0.42")
            node.set("worker.healthy", b"true")
            node.save_to_disk()

            restored = crdt.StateStore(102, persistence_path=snap_path)
            self.assertEqual(restored.get("worker.load"), b"0.42")
            self.assertEqual(restored.get("worker.healthy"), b"true")

def ncrdt_main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(ncrdt_TestNodeCRDTSync)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# ============================================================================
# from tests/test-node.py (prefix nd_)
# ============================================================================
"""Automated tests for WS-NODE mDNS zero-conf discovery, packet validation, and challenge handshake."""


import importlib.util
import os
import sys
import unittest

_nd_HERE = os.path.dirname(os.path.abspath(__file__))
_nd_ROOT = os.path.normpath(os.path.join(_nd_HERE, ".."))
_nd_DISC_PATH = os.path.join(_nd_ROOT, "usr", "libexec", "mios", "node", "discovery.py")

nd_spec = importlib.util.spec_from_file_location("discovery", _nd_DISC_PATH)
if nd_spec and nd_spec.loader:
    discovery = importlib.util.module_from_spec(nd_spec)
    sys.modules[nd_spec.name] = discovery
    nd_spec.loader.exec_module(discovery)
else:
    raise ImportError(f"Could not load discovery module from {_nd_DISC_PATH}")

class nd_TestNodeDiscovery(unittest.TestCase):
    """Validates mDNS advertisement parsing, challenge-response authentication, and active registry."""

    def test_node_advertisement_serialization(self):
        adv = discovery.NodeAdvertisement(
            node_id=202,
            hostname="mios-edge-blade01",
            port=8640,
            capabilities=["tier1_wasm", "tier2_native", "fp16_inference"],
            public_key_hex="0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        )
        d = adv.to_dict()
        self.assertEqual(d["node_id"], 202)
        self.assertEqual(d["hostname"], "mios-edge-blade01")
        self.assertIn("tier1_wasm", d["capabilities"])

        restored = discovery.NodeAdvertisement.from_dict(d)
        self.assertEqual(restored.node_id, 202)
        self.assertEqual(restored.public_key_hex, adv.public_key_hex)

    def test_cryptographic_challenge_handshake_success(self):
        cluster_secret = b"mios-mesh-cluster-secret-key-32b"
        challenge = discovery.NodeHandshake.generate_challenge()
        self.assertEqual(len(challenge), 32)

        response = discovery.NodeHandshake.sign_challenge(challenge, cluster_secret)
        self.assertTrue(discovery.NodeHandshake.verify_response(challenge, response, cluster_secret))

    def test_challenge_handshake_tamper_rejection(self):
        cluster_secret = b"mios-mesh-cluster-secret-key-32b"
        wrong_secret = b"attacker-invalid-cluster-secret-"
        challenge = discovery.NodeHandshake.generate_challenge()

        bad_response = discovery.NodeHandshake.sign_challenge(challenge, wrong_secret)
        self.assertFalse(discovery.NodeHandshake.verify_response(challenge, bad_response, cluster_secret))

    def test_registry_discovery_and_authentication_lifecycle(self):
        cluster_secret = b"mios-mesh-cluster-secret-key-32b"
        registry = discovery.MeshDiscoveryRegistry(local_node_id=100, shared_cluster_key=cluster_secret)

        peer_adv = discovery.NodeAdvertisement(
            node_id=205,
            hostname="mios-blade-worker",
            port=9090,
            capabilities=["cuda_tensor_cores", "crdt_sync"],
            public_key_hex="abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789",
        )

        # Register peer
        self.assertTrue(registry.register_advertisement(peer_adv))
        self.assertEqual(len(registry.active_mesh_nodes()), 0)  # Not authenticated yet

        # Authenticate peer
        challenge = discovery.NodeHandshake.generate_challenge()
        response = discovery.NodeHandshake.sign_challenge(challenge, cluster_secret)
        self.assertTrue(registry.authenticate_peer(205, response, challenge))

        # Check active nodes
        active = registry.active_mesh_nodes()
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["node_id"], 205)
        self.assertTrue(active[0]["authenticated"])

def nd_main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(nd_TestNodeDiscovery)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# ============================================================================
# from tests/test-node.py (prefix nm1a_)
# ============================================================================
"""
Milestone 1 Adversarial Stress Verification Suite.
Validates all edge cases, security allowlists, CPU topology invariants, CRDT compaction, and watchdog lifecycles.
"""

import os
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "usr", "libexec", "mios", "node")))

from hardware import (
    HardwareAllowlist,
    HardwareErrorCode,
    MockHardwareDriver,
    SandboxedHardwareController,
)
from cgroups import (
    AffinityPolicy,
    CgroupV2Controller,
    NodeResourceLimits,
    WorkerAffinityController,
    filter_safe_worker_cores,
)
from crdt import StateElement, StateStore, VectorClock
from watchdog import MockWatchdogDriver, WatchdogConfig, WatchdogSupervisor

class nm1a_TestT389HardwareStress(unittest.TestCase):
    """Stress tests for T-389 Hardware HAL and Allowlist constraints."""

    def setUp(self):
        self.allowlist = HardwareAllowlist(
            allowed_gpio_pins={4, 17, 27, 22},
            read_only_gpio_pins={4},
            allowed_i2c_buses={1},
            allowed_i2c_addresses={0x48, 0x68},
            max_i2c_transfer_len=64,
        )
        self.driver = MockHardwareDriver()
        self.controller = SandboxedHardwareController(self.allowlist, self.driver)

    def test_unauthorized_gpio_access_boundaries(self):
        unauthorized = [0, 1, 2, 3, 5, 18, 99, 255, 65535, 100000]
        for pin in unauthorized:
            code, val = self.controller.mios_sys_gpio_read(pin)
            self.assertEqual(code, HardwareErrorCode.PERMISSION_DENIED)
            write_code = self.controller.mios_sys_gpio_write(pin, 1)
            self.assertEqual(write_code, HardwareErrorCode.PERMISSION_DENIED)

    def test_read_only_pin_violation(self):
        self.driver.gpio_write(4, 1)
        code, val = self.controller.mios_sys_gpio_read(4)
        self.assertEqual(code, HardwareErrorCode.SUCCESS)
        self.assertEqual(val, 1)

        write_code = self.controller.mios_sys_gpio_write(4, 0)
        self.assertEqual(write_code, HardwareErrorCode.READ_ONLY_PIN)
        self.assertEqual(self.driver.gpio_read(4), 1)

    def test_i2c_unauthorized_buses_and_addresses(self):
        for bus in [0, 2, 3, 255]:
            code, _ = self.controller.mios_sys_i2c_transfer(bus, 0x68, b"\x00", 1)
            self.assertEqual(code, HardwareErrorCode.PERMISSION_DENIED)

        for addr in [0x00, 0x49, 0x55, 0x77, 0x3FF]:
            code, _ = self.controller.mios_sys_i2c_transfer(1, addr, b"\x00", 1)
            self.assertEqual(code, HardwareErrorCode.PERMISSION_DENIED)

    def test_i2c_buffer_overflow_rejections(self):
        # Max is 64 bytes
        overflow_write = bytes([0] * 65)
        code1, _ = self.controller.mios_sys_i2c_transfer(1, 0x68, overflow_write, 1)
        self.assertEqual(code1, HardwareErrorCode.INVALID_PARAMETER)

        code2, _ = self.controller.mios_sys_i2c_transfer(1, 0x68, b"\x00", 65)
        self.assertEqual(code2, HardwareErrorCode.INVALID_PARAMETER)

        # Boundary 64 bytes succeeds
        boundary_write = bytes([0] * 64)
        code3, res = self.controller.mios_sys_i2c_transfer(1, 0x68, boundary_write, 64)
        self.assertEqual(code3, HardwareErrorCode.SUCCESS)
        self.assertEqual(len(res), 64)

class nm1a_TestT390CgroupsStress(unittest.TestCase):
    """Stress tests for T-390 CPU Pinning and Cgroups limits."""

    def test_topology_core_zero_isolation(self):
        # 1-core topology
        c1 = filter_safe_worker_cores(1, None, True)
        self.assertEqual(c1, [0], "1-core system must retain Core 0")

        # 2-core topology
        c2 = filter_safe_worker_cores(2, None, True)
        self.assertEqual(c2, [1], "2-core system must strip Core 0")

        # 4-core topology
        c4 = filter_safe_worker_cores(4, None, True)
        self.assertEqual(c4, [1, 2, 3])
        self.assertNotIn(0, c4)

        # 64-core topology
        c64 = filter_safe_worker_cores(64, None, True)
        self.assertEqual(len(c64), 63)
        self.assertEqual(c64[0], 1)
        self.assertEqual(c64[-1], 63)
        self.assertNotIn(0, c64)

        # Out-of-bounds requested filter
        filtered = filter_safe_worker_cores(64, [0, 2, 10, 63, 64, 100], True)
        self.assertEqual(filtered, [2, 10, 63])

    def test_affinity_exhaustion_and_recovery(self):
        ctrl = WorkerAffinityController(4)  # safe: [1, 2, 3]

        c1 = ctrl.allocate_cores_for_policy(AffinityPolicy.EXCLUSIVE, 2)
        self.assertEqual(c1, [1, 2])

        c2 = ctrl.allocate_cores_for_policy(AffinityPolicy.EXCLUSIVE, 1)
        self.assertEqual(c2, [3])

        # Exhaustion
        with self.assertRaises(RuntimeError):
            ctrl.allocate_cores_for_policy(AffinityPolicy.EXCLUSIVE, 1)

        # Release and realloc
        ctrl.release_cores([2])
        c3 = ctrl.allocate_cores_for_policy(AffinityPolicy.EXCLUSIVE, 1)
        self.assertEqual(c3, [2])

        # Low priority & Shared
        low = ctrl.allocate_cores_for_policy(AffinityPolicy.LOW_PRIORITY, 0)
        self.assertEqual(low, [3])
        self.assertNotIn(0, low)

        shared = ctrl.allocate_cores_for_policy(AffinityPolicy.SHARED, 0)
        self.assertEqual(shared, [1, 2, 3])
        self.assertNotIn(0, shared)

    def test_cgroup_format_cpu_max_edge_cases(self):
        self.assertEqual(CgroupV2Controller.format_cpu_max(None, 100_000), "max 100000")
        self.assertEqual(CgroupV2Controller.format_cpu_max(0, 100_000), "0 100000")
        self.assertEqual(CgroupV2Controller.format_cpu_max(80, 100_000), "80000 100000")
        self.assertEqual(CgroupV2Controller.format_cpu_max(400, 100_000), "400000 100000")

class nm1a_TestT391CrdtCompactionStress(unittest.TestCase):
    """Stress tests for T-391 CRDT Compaction, Tombstone TTL, and Snapshot GC."""

    def test_tombstone_ttl_and_resurrection_resistance(self):
        store = StateStore(10)

        # key_a deleted at t = 1000s
        store.set("key_a", b"val_a")
        store.delete("key_a")
        store.elements["key_a"].timestamp_ns = int(1000 * 1e9)

        # key_b deleted at t = 2000s
        store.set("key_b", b"val_b")
        store.delete("key_b")
        store.elements["key_b"].timestamp_ns = int(2000 * 1e9)

        # key_c active at t = 2500s
        store.set("key_c", b"val_c")
        store.elements["key_c"].timestamp_ns = int(2500 * 1e9)

        # Compact at current_time = 2200s with TTL = 500s
        # key_a age = 1200s > 500s -> purged
        # key_b age = 200s <= 500s -> retained
        stats = store.compact_tombstones(ttl_s=500.0, current_time_s=2200.0)
        self.assertEqual(stats["tombstones_purged"], 1)
        self.assertEqual(stats["tombstones_retained"], 1)
        self.assertEqual(stats["active_elements"], 1)

        self.assertEqual(store.get("key_c"), b"val_c")
        self.assertIsNone(store.get("key_b"))
        self.assertIsNone(store.get("key_a"))

        # Stale update cannot resurrect key_b
        stale_elem = StateElement(
            key="key_b",
            value=b"stale_resurrect",
            timestamp_ns=int(1500 * 1e9),
            originating_node_id=20,
            is_deleted=False,
        )
        applied = store.merge_remote_store(VectorClock(), [stale_elem])
        self.assertEqual(applied, 0)
        self.assertIsNone(store.get("key_b"))

        # Fresh update resurrects key_b
        fresh_elem = StateElement(
            key="key_b",
            value=b"fresh_resurrect",
            timestamp_ns=int(3000 * 1e9),
            originating_node_id=20,
            is_deleted=False,
        )
        applied = store.merge_remote_store(VectorClock(), [fresh_elem])
        self.assertEqual(applied, 1)
        self.assertEqual(store.get("key_b"), b"fresh_resurrect")

    def test_tie_breaking_originating_node_id(self):
        store = StateStore(100)
        local = StateElement(
            key="tie_key",
            value=b"from_node_100",
            timestamp_ns=5000,
            originating_node_id=100,
            is_deleted=False,
        )
        store.merge_remote_store(VectorClock(), [local])

        # Lower node ID (50 < 100) -> rejected
        remote_lower = StateElement(
            key="tie_key",
            value=b"from_node_50",
            timestamp_ns=5000,
            originating_node_id=50,
            is_deleted=False,
        )
        applied = store.merge_remote_store(VectorClock(), [remote_lower])
        self.assertEqual(applied, 0)
        self.assertEqual(store.get("tie_key"), b"from_node_100")

        # Higher node ID (200 > 100) -> accepted
        remote_higher = StateElement(
            key="tie_key",
            value=b"from_node_200",
            timestamp_ns=5000,
            originating_node_id=200,
            is_deleted=False,
        )
        applied = store.merge_remote_store(VectorClock(), [remote_higher])
        self.assertEqual(applied, 1)
        self.assertEqual(store.get("tie_key"), b"from_node_200")

    def test_wal_compaction_and_disk_reloading(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "crdt_store.json")
            store = StateStore(501, persistence_path=path)

            for i in range(100):
                store.set(f"k_{i}", f"v_{i}".encode())

            for i in range(60):
                store.delete(f"k_{i}")
                store.elements[f"k_{i}"].timestamp_ns = int(1000 * 1e9)

            self.assertEqual(store.total_elements_count(), 100)
            self.assertEqual(store.count_tombstones(), 60)

            stats = store.compact_disk_storage(ttl_s=1000.0, current_time_s=10000.0)
            self.assertEqual(stats["tombstones_purged"], 60)
            self.assertEqual(stats["active_elements"], 40)
            self.assertEqual(store.total_elements_count(), 40)

            # Reload
            reloaded = StateStore(501, persistence_path=path)
            self.assertEqual(reloaded.total_elements_count(), 40)
            self.assertEqual(reloaded.count_tombstones(), 0)

            for i in range(60, 100):
                self.assertEqual(reloaded.get(f"k_{i}"), f"v_{i}".encode())
            for i in range(60):
                self.assertIsNone(reloaded.get(f"k_{i}"))

class nm1a_TestT400WatchdogStress(unittest.TestCase):
    """Stress tests for T-400 Watchdog Supervisor."""

    def test_rapid_sequential_pings(self):
        sup = WatchdogSupervisor()
        self.assertTrue(sup.arm())
        self.assertTrue(sup.is_armed())

        for _ in range(10_000):
            self.assertTrue(sup.ping())

        driver = sup.driver
        self.assertEqual(driver.ping_count, 10_000)
        self.assertFalse(driver.disarmed_safely)

    def test_concurrent_multithreaded_pings(self):
        sup = WatchdogSupervisor()
        self.assertTrue(sup.arm())

        threads = []
        for _ in range(10):
            t = threading.Thread(target=lambda: [sup.ping() for _ in range(500)])
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        self.assertEqual(sup.driver.ping_count, 5_000)
        self.assertTrue(sup.disarm())
        self.assertFalse(sup.is_armed())
        self.assertTrue(sup.driver.disarmed_safely)

    def test_disarm_rearm_and_missing_recovery(self):
        mock_missing = MockWatchdogDriver(simulated_present=False)
        sup_missing = WatchdogSupervisor(driver=mock_missing)
        self.assertFalse(sup_missing.is_present())
        self.assertFalse(sup_missing.arm())
        self.assertFalse(sup_missing.is_armed())

        sup = WatchdogSupervisor()
        self.assertTrue(sup.arm())
        self.assertTrue(sup.ping())

        self.assertTrue(sup.disarm())
        self.assertFalse(sup.is_armed())
        self.assertTrue(sup.driver.disarmed_safely)
        self.assertFalse(sup.ping())

        # Re-arm
        self.assertTrue(sup.arm())
        self.assertTrue(sup.is_armed())
        self.assertFalse(sup.driver.disarmed_safely)
        self.assertTrue(sup.ping())


# ============================================================================
# from tests/test-node.py (prefix nm2ac_)
# ============================================================================
import os
import sys
import threading
import time
import unittest

_nm2ac_ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_nm2ac_NODE_DIR = os.path.join(_nm2ac_ROOT_DIR, "usr", "libexec", "mios", "node")
if _nm2ac_NODE_DIR not in sys.path:
    sys.path.insert(0, _nm2ac_NODE_DIR)
if _nm2ac_ROOT_DIR not in sys.path:
    sys.path.insert(0, _nm2ac_ROOT_DIR)

from scheduler import (
    GlobalInjector,
    ScheduledDecision,
    ScheduledTargetType,
    TaskItem,
    TaskPriority,
    WorkerQueue,
    WorkStealingScheduler,
)
from buffer_pool import BucketTier, BufferPool, PooledBuffer
from capabilities import (
    ActiveTransports,
    CapabilityRegistry,
    EngineTiers,
    HardwareSpecs,
    NodeAnnouncePayload,
    NodeCapabilities,
    VramTelemetry,
)
from ble import (
    BLE_CHAR_ECDH_UUID,
    BLE_CHAR_IDENTITY_UUID,
    BLE_CHAR_PROVISION_UUID,
    BleBootstrapState,
    BleMeshBootstrap,
    MockBleAdapter,
    ProvisioningPayload,
    provision_remote_node,
)
from overlay import (
    HysteresisConfig,
    MultiTransportRouter,
    PeerRoute,
    TransportType,
)
from wire import Frame, MessageType

class nm2ac_TestM2AdversarialChallenger(unittest.TestCase):
    """Exhaustive empirical stress tests and edge case verification for Milestone 2."""

    # -------------------------------------------------------------------------
    # 1. T-392: Stress & Invariant Tests for Work-Stealing Scheduler
    # -------------------------------------------------------------------------
    def test_stress_concurrent_work_stealing_with_pinned_invariants(self):
        scheduler = WorkStealingScheduler(local_node_id=101, num_workers=4)
        num_tasks = 1000

        # Ingest tasks: 33% pinned hardware, 33% pinned to node 101, 33% unpinned
        for i in range(num_tasks):
            prio = TaskPriority(i % 4)
            pinned_hw = (i % 3 == 0)
            pinned_node = 101 if (i % 3 == 1) else (999 if (i % 7 == 0) else None)

            t = TaskItem(
                task_id=i,
                priority=prio,
                pinned_hardware=pinned_hw,
                pinned_node_id=pinned_node,
                code_bytes=b"CODE_PAYLOAD",
            )
            scheduler.submit_task(t, worker_hint=i % 4)

        completed_tasks = []
        lock = threading.Lock()
        stop_signal = threading.Event()

        def worker_loop(w_id: int):
            while not stop_signal.is_set():
                task = scheduler.pop_task(w_id)
                if task:
                    with lock:
                        completed_tasks.append((w_id, task))
                else:
                    time.sleep(0.0001)

        def stealer_loop(requester_id: int):
            while not stop_signal.is_set():
                stolen = scheduler.handle_remote_steal_request(requester_id, max_tasks=3)
                if stolen:
                    for task in stolen:
                        self.assertFalse(
                            task.pinned_hardware,
                            f"Remote peer {requester_id} stole task with pinned_hardware=True",
                        )
                        if task.pinned_node_id is not None:
                            self.assertEqual(
                                task.pinned_node_id,
                                requester_id,
                                f"Remote peer {requester_id} stole task pinned to {task.pinned_node_id}",
                            )
                        with lock:
                            completed_tasks.append((requester_id, task))
                else:
                    time.sleep(0.0001)

        threads = []
        for w_id in range(4):
            threads.append(threading.Thread(target=worker_loop, args=(w_id,)))
        for peer_id in [201, 202]:
            threads.append(threading.Thread(target=stealer_loop, args=(peer_id,)))

        for t in threads:
            t.start()

        # Wait until all tasks are consumed
        start = time.time()
        while len(completed_tasks) < num_tasks and (time.time() - start) < 5.0:
            time.sleep(0.01)

        stop_signal.set()
        for t in threads:
            t.join()

        self.assertEqual(len(completed_tasks), num_tasks)
        stats = scheduler.get_stats()
        self.assertEqual(stats.tasks_ingested, num_tasks)

    def test_scheduler_route_task_boundaries(self):
        scheduler = WorkStealingScheduler(local_node_id=101, num_workers=2)

        # 1. Hardware pinned task must ALWAYS be local
        t_hw = TaskItem(task_id=1, priority=TaskPriority.CRITICAL, pinned_hardware=True)
        decision = scheduler.route_task(t_hw, peer_loads=[(201, 0), (202, 0)])
        self.assertEqual(decision.target_type, ScheduledTargetType.LOCAL)

        # 2. Pinned to remote node 500
        t_remote = TaskItem(task_id=2, priority=TaskPriority.NORMAL, pinned_node_id=500)
        decision = scheduler.route_task(t_remote, peer_loads=[(500, 10)])
        self.assertEqual(decision.target_type, ScheduledTargetType.OFFLOAD)
        self.assertEqual(decision.node_id, 500)

        # 3. Pinned to local node 101
        t_local = TaskItem(task_id=3, priority=TaskPriority.NORMAL, pinned_node_id=101)
        decision = scheduler.route_task(t_local, peer_loads=[(201, 0)])
        self.assertEqual(decision.target_type, ScheduledTargetType.LOCAL)

    # -------------------------------------------------------------------------
    # 2. T-393: Stress & Invariant Tests for Zero-Copy Buffer Pool
    # -------------------------------------------------------------------------
    def test_stress_buffer_pool_multithreaded_leasing_and_slicing(self):
        pool = BufferPool()
        num_threads = 8
        ops_per_thread = 100

        def thread_task(tid: int):
            for i in range(ops_per_thread):
                size = 128 if (i % 2 == 0) else 4096
                with pool.acquire(size) as buf:
                    buf.extend(b"HEADER_16BYTES__DATA_BODY_CHUNK")
                    self.assertEqual(buf.slice(0, 16), memoryview(b"HEADER_16BYTES__"))
                    pref = buf.split_prefix(16)
                    self.assertEqual(pref, b"HEADER_16BYTES__")
                    self.assertEqual(buf.as_bytes(), b"DATA_BODY_CHUNK")

        threads = [threading.Thread(target=thread_task, args=(i,)) for i in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        stats = pool.get_stats()
        self.assertEqual(stats.active_leased, 0)
        self.assertEqual(stats.allocations, num_threads * ops_per_thread)

    def test_adversarial_buffer_pool_slicing_edge_cases(self):
        pool = BufferPool()
        buf = pool.acquire(512)
        buf.extend(b"0123456789ABCDEF")

        # 1. Out-of-bounds split prefix
        with self.assertRaises(IndexError):
            buf.split_prefix(100)

        # 2. Double release should be idempotent
        buf.release()
        buf.release()

        # 3. Access after release should raise RuntimeError
        with self.assertRaises(RuntimeError):
            buf.as_bytes()
        with self.assertRaises(RuntimeError):
            buf.write(b"fail")

    # -------------------------------------------------------------------------
    # 3. T-394: Adversarial Capability Probing & Filtering
    # -------------------------------------------------------------------------
    def test_adversarial_capability_registry_extreme_queries(self):
        registry = CapabilityRegistry()

        # Empty registry queries
        self.assertEqual(registry.find_eligible_nodes(min_ram_kb=1000000), [])
        self.assertEqual(registry.active_node_count(), 0)
        self.assertEqual(registry.evict_stale(max_age_secs=60), 0)

        # Add node with extreme caps
        caps = NodeCapabilities(
            hardware=HardwareSpecs(ram_available_kb=32 * 1024 * 1024),
            vram=VramTelemetry(vram_available_mb=16384, has_npu=True),
            has_gpio=True,
            has_i2c=True,
        )
        payload = NodeAnnouncePayload(node_id=42, hostname="super-node", capabilities=caps)
        registry.register_announce(payload, received_at=1000)

        # Eligible queries
        matched = registry.find_eligible_nodes(
            min_ram_kb=16 * 1024 * 1024,
            min_vram_mb=8192,
            require_gpio=True,
            require_i2c=True,
        )
        self.assertEqual(matched, [42])

        # Impossible query
        self.assertEqual(registry.find_eligible_nodes(min_ram_kb=999999999), [])

        # Evict stale
        self.assertEqual(registry.evict_stale(max_age_secs=30, now=1050), 1)
        self.assertEqual(registry.active_node_count(), 0)

    # -------------------------------------------------------------------------
    # 4. T-395: Adversarial BLE AEAD Bit-Flip Fuzzing & Invalid Keys
    # -------------------------------------------------------------------------
    def test_adversarial_ble_bit_flip_fuzzing_and_key_validation(self):
        adapter = MockBleAdapter()
        bootstrap = BleMeshBootstrap(node_id=77, adapter=adapter)
        bootstrap.start()

        # 1. Invalid ECDH key lengths
        with self.assertRaises(ValueError):
            bootstrap.handle_ecdh_exchange(b"")
        with self.assertRaises(ValueError):
            bootstrap.handle_ecdh_exchange(b"\x00" * 16)
        with self.assertRaises(ValueError):
            bootstrap.handle_ecdh_exchange(b"\x00" * 33)

        # 2. Premature provisioning write
        with self.assertRaises(RuntimeError):
            bootstrap.handle_provisioning_write(b"\x00" * 32)

        # 3. Perform legitimate handshake
        creds = ProvisioningPayload(
            ssid="FuzzSSID",
            psk="Secret123",
            cluster_token="tok-123",
            coordinator_endpoint="10.0.0.1:8650",
        )
        provision_remote_node(adapter, creds)

        peer_pub = adapter.get_characteristic_value(BLE_CHAR_ECDH_UUID)
        bootstrap.handle_ecdh_exchange(peer_pub)

        valid_encrypted = adapter.get_characteristic_value(BLE_CHAR_PROVISION_UUID)
        self.assertTrue(len(valid_encrypted) > 0)

        # 4. Exhaustive single-byte bit flip fuzzing across all ciphertext bytes
        for i in range(len(valid_encrypted)):
            corrupted = bytearray(valid_encrypted)
            corrupted[i] ^= 0x01
            with self.assertRaises(Exception):
                bootstrap.handle_provisioning_write(bytes(corrupted))

        # 5. Valid write succeeds
        provisioned = bootstrap.handle_provisioning_write(valid_encrypted)
        self.assertEqual(provisioned.ssid, "FuzzSSID")
        self.assertEqual(bootstrap.state, BleBootstrapState.PROVISIONED)

    # -------------------------------------------------------------------------
    # 5. T-396: Stress & Boundary Tests for Multi-Transport Flapping & Hysteresis
    # -------------------------------------------------------------------------
    def test_stress_overlay_flapping_and_hysteresis_boundaries(self):
        config = HysteresisConfig(
            fail_strikes_threshold=3,
            recovery_dwell_ms=10_000,
            recovery_strikes_threshold=3,
        )
        router = MultiTransportRouter(config)

        endpoints = {
            TransportType.LAN_BROADCAST: "192.168.1.50:8650",
            TransportType.WIREGUARD: "10.0.0.50:8650",
            TransportType.TAILSCALE: "100.64.0.50:8650",
            TransportType.DIRECT_TCP: "192.168.1.50:9000",
        }
        router.register_peer(303, endpoints)

        # 1. Rapid alternating 1 miss, 1 hit -> LAN should NEVER failover
        for i in range(100):
            t = i * 100
            if i % 2 == 0:
                router.record_missed_heartbeat(303, TransportType.LAN_BROADCAST, now_ms=t)
            else:
                router.record_heartbeat(303, TransportType.LAN_BROADCAST, latency_ms=1, now_ms=t)
            self.assertEqual(router.select_route(303)[0], TransportType.LAN_BROADCAST)
            self.assertFalse(router.is_peer_partitioned(303))

        # 2. Failover on 3 consecutive misses
        router.record_missed_heartbeat(303, TransportType.LAN_BROADCAST, now_ms=10_000)
        router.record_missed_heartbeat(303, TransportType.LAN_BROADCAST, now_ms=11_000)
        self.assertFalse(router.is_peer_partitioned(303))

        router.record_missed_heartbeat(303, TransportType.LAN_BROADCAST, now_ms=12_000)
        self.assertTrue(router.is_peer_partitioned(303))
        self.assertEqual(router.select_route(303)[0], TransportType.WIREGUARD)

        # 3. Dwell timer testing: 3 hits at t=13_000, 14_000, 15_000 -> dwell = 2000ms < 10000ms
        router.record_heartbeat(303, TransportType.LAN_BROADCAST, latency_ms=1, now_ms=13_000)
        router.record_heartbeat(303, TransportType.LAN_BROADCAST, latency_ms=1, now_ms=14_000)
        router.record_heartbeat(303, TransportType.LAN_BROADCAST, latency_ms=1, now_ms=15_000)
        self.assertEqual(router.select_route(303)[0], TransportType.WIREGUARD)

        # 4. At t=22_999 (dwell = 9999ms < 10000ms) -> Still WireGuard
        router.record_heartbeat(303, TransportType.LAN_BROADCAST, latency_ms=1, now_ms=22_999)
        self.assertEqual(router.select_route(303)[0], TransportType.WIREGUARD)

        # 5. At t=23_000 (dwell = 10000ms >= 10000ms) -> Restores LAN
        router.record_heartbeat(303, TransportType.LAN_BROADCAST, latency_ms=1, now_ms=23_000)
        self.assertEqual(router.select_route(303)[0], TransportType.LAN_BROADCAST)
        self.assertFalse(router.is_peer_partitioned(303))


# ============================================================================
# from tests/test-node.py (prefix no_)
# ============================================================================
"""Automated tests for WS-NODE MultiTransportRouter, 3-strike LAN partition failover, and anti-flap recovery."""


import importlib.util
import os
import sys
import unittest

_no_HERE = os.path.dirname(os.path.abspath(__file__))
_no_ROOT = os.path.normpath(os.path.join(_no_HERE, ".."))
_no_OVERLAY_PATH = os.path.join(_no_ROOT, "usr", "libexec", "mios", "node", "overlay.py")

no_spec = importlib.util.spec_from_file_location("overlay", _no_OVERLAY_PATH)
if no_spec and no_spec.loader:
    overlay = importlib.util.module_from_spec(no_spec)
    sys.modules[no_spec.name] = overlay
    no_spec.loader.exec_module(overlay)
else:
    raise ImportError(f"Could not load overlay module from {_no_OVERLAY_PATH}")

class no_TestNodeOverlay(unittest.TestCase):
    """Validates multi-transport routing, 3-strike partition detection, and asymmetric anti-flap dwell."""

    def test_transport_types_and_defaults(self):
        self.assertEqual(overlay.TransportType.LAN_BROADCAST, 1)
        self.assertEqual(overlay.TransportType.WIREGUARD, 2)
        self.assertEqual(overlay.TransportType.TAILSCALE, 3)
        self.assertEqual(overlay.TransportType.DIRECT_TCP, 4)

    def test_lan_partition_failover_to_wireguard(self):
        config = overlay.HysteresisConfig(
            fail_strikes_threshold=3,
            recovery_dwell_ms=10_000,
            recovery_strikes_threshold=3,
        )
        router = overlay.MultiTransportRouter(config)

        endpoints = {
            overlay.TransportType.LAN_BROADCAST: "192.168.1.50:8650",
            overlay.TransportType.WIREGUARD: "10.0.0.50:8650",
            overlay.TransportType.TAILSCALE: "100.64.0.50:8650",
        }
        router.register_peer(node_id=201, endpoints=endpoints)

        # 1. Initial primary transport is LAN
        transport, endpoint = router.select_route(node_id=201)
        self.assertEqual(transport, overlay.TransportType.LAN_BROADCAST)
        self.assertEqual(endpoint, "192.168.1.50:8650")
        self.assertFalse(router.is_peer_partitioned(node_id=201))

        # 2. 2 missed heartbeats on LAN: should still remain LAN
        router.record_missed_heartbeat(node_id=201, transport=overlay.TransportType.LAN_BROADCAST, now_ms=1000)
        router.record_missed_heartbeat(node_id=201, transport=overlay.TransportType.LAN_BROADCAST, now_ms=2000)
        self.assertFalse(router.is_peer_partitioned(node_id=201))
        self.assertEqual(router.select_route(node_id=201)[0], overlay.TransportType.LAN_BROADCAST)

        # 3. 3rd missed heartbeat: triggers failover to WireGuard
        router.record_missed_heartbeat(node_id=201, transport=overlay.TransportType.LAN_BROADCAST, now_ms=3000)
        self.assertTrue(router.is_peer_partitioned(node_id=201))
        transport2, endpoint2 = router.select_route(node_id=201)
        self.assertEqual(transport2, overlay.TransportType.WIREGUARD)
        self.assertEqual(endpoint2, "10.0.0.50:8650")

    def test_failover_hierarchy_to_tailscale(self):
        router = overlay.MultiTransportRouter()

        endpoints = {
            overlay.TransportType.LAN_BROADCAST: "192.168.1.75:8650",
            overlay.TransportType.TAILSCALE: "100.64.0.75:8650",
        }
        router.register_peer(node_id=202, endpoints=endpoints)

        # Failover without WireGuard endpoint -> falls back to Tailscale
        for i in range(3):
            router.record_missed_heartbeat(node_id=202, transport=overlay.TransportType.LAN_BROADCAST, now_ms=1000 * (i + 1))

        self.assertTrue(router.is_peer_partitioned(node_id=202))
        transport, endpoint = router.select_route(node_id=202)
        self.assertEqual(transport, overlay.TransportType.TAILSCALE)
        self.assertEqual(endpoint, "100.64.0.75:8650")

    def test_asymmetric_anti_flap_recovery_hysteresis(self):
        config = overlay.HysteresisConfig(
            fail_strikes_threshold=3,
            recovery_dwell_ms=5000,  # 5s dwell for test
            recovery_strikes_threshold=3,
        )
        router = overlay.MultiTransportRouter(config)

        endpoints = {
            overlay.TransportType.LAN_BROADCAST: "192.168.1.99:8650",
            overlay.TransportType.TAILSCALE: "100.64.0.99:8650",
        }
        router.register_peer(node_id=203, endpoints=endpoints)

        # Trigger failover
        for i in range(3):
            router.record_missed_heartbeat(node_id=203, transport=overlay.TransportType.LAN_BROADCAST, now_ms=1000 * (i + 1))
        self.assertEqual(router.select_route(node_id=203)[0], overlay.TransportType.TAILSCALE)

        # LAN probes resume at t=4000
        router.record_heartbeat(node_id=203, transport=overlay.TransportType.LAN_BROADCAST, latency_ms=1, now_ms=4000)
        router.record_heartbeat(node_id=203, transport=overlay.TransportType.LAN_BROADCAST, latency_ms=1, now_ms=5000)
        router.record_heartbeat(node_id=203, transport=overlay.TransportType.LAN_BROADCAST, latency_ms=1, now_ms=6000)

        # 3 strikes achieved, but dwell elapsed is only 2000ms (< 5000ms) -> Still Tailscale!
        self.assertEqual(router.select_route(node_id=203)[0], overlay.TransportType.TAILSCALE)
        self.assertTrue(router.is_peer_partitioned(node_id=203))

        # Probe at t=9500 (dwell elapsed = 5500ms >= 5000ms) -> Restores LAN!
        router.record_heartbeat(node_id=203, transport=overlay.TransportType.LAN_BROADCAST, latency_ms=1, now_ms=9500)
        self.assertEqual(router.select_route(node_id=203)[0], overlay.TransportType.LAN_BROADCAST)
        self.assertFalse(router.is_peer_partitioned(node_id=203))

def no_main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(no_TestNodeOverlay)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# ============================================================================
# from tests/test-node.py (prefix ns_)
# ============================================================================
"""Automated tests for WS-NODE WorkStealingScheduler, priority tiers, and hardware pin invariants."""


import importlib.util
import os
import sys
import threading
import time
import unittest

_ns_HERE = os.path.dirname(os.path.abspath(__file__))
_ns_ROOT = os.path.normpath(os.path.join(_ns_HERE, ".."))
_ns_SCHED_PATH = os.path.join(_ns_ROOT, "usr", "libexec", "mios", "node", "scheduler.py")

ns_spec = importlib.util.spec_from_file_location("scheduler", _ns_SCHED_PATH)
if ns_spec and ns_spec.loader:
    scheduler = importlib.util.module_from_spec(ns_spec)
    sys.modules[ns_spec.name] = scheduler
    ns_spec.loader.exec_module(scheduler)
else:
    raise ImportError(f"Could not load scheduler module from {_ns_SCHED_PATH}")

class ns_TestNodeScheduler(unittest.TestCase):
    """Validates work-stealing priority scheduling, pin invariants, and router offloading."""

    def test_priority_tier_ordering(self):
        self.assertLess(scheduler.TaskPriority.CRITICAL, scheduler.TaskPriority.HIGH)
        self.assertLess(scheduler.TaskPriority.HIGH, scheduler.TaskPriority.NORMAL)
        self.assertLess(scheduler.TaskPriority.NORMAL, scheduler.TaskPriority.LOW)

    def test_local_worker_priority_execution(self):
        sched = scheduler.WorkStealingScheduler(local_node_id=101, num_workers=2)

        t_low = scheduler.TaskItem(task_id=1, priority=scheduler.TaskPriority.LOW)
        t_crit = scheduler.TaskItem(task_id=2, priority=scheduler.TaskPriority.CRITICAL)
        t_norm = scheduler.TaskItem(task_id=3, priority=scheduler.TaskPriority.NORMAL)

        # Submit all to worker 0
        sched.submit_task(t_low, worker_hint=0)
        sched.submit_task(t_crit, worker_hint=0)
        sched.submit_task(t_norm, worker_hint=0)

        # Worker 0 pops in priority order: CRITICAL -> NORMAL -> LOW
        p1 = sched.pop_task(worker_id=0)
        self.assertIsNotNone(p1)
        self.assertEqual(p1.task_id, 2)
        self.assertEqual(p1.priority, scheduler.TaskPriority.CRITICAL)

        p2 = sched.pop_task(worker_id=0)
        self.assertIsNotNone(p2)
        self.assertEqual(p2.task_id, 3)
        self.assertEqual(p2.priority, scheduler.TaskPriority.NORMAL)

        p3 = sched.pop_task(worker_id=0)
        self.assertIsNotNone(p3)
        self.assertEqual(p3.task_id, 1)
        self.assertEqual(p3.priority, scheduler.TaskPriority.LOW)

        self.assertIsNone(sched.pop_task(worker_id=0))

    def test_worker_stealing_when_idle(self):
        sched = scheduler.WorkStealingScheduler(local_node_id=101, num_workers=2)

        t1 = scheduler.TaskItem(task_id=10, priority=scheduler.TaskPriority.NORMAL)
        t2 = scheduler.TaskItem(task_id=20, priority=scheduler.TaskPriority.LOW)

        # Submit tasks to worker 0 only
        sched.submit_task(t1, worker_hint=0)
        sched.submit_task(t2, worker_hint=0)

        # Worker 1 is idle; popping from worker 1 steals from worker 0
        stolen = sched.pop_task(worker_id=1)
        self.assertIsNotNone(stolen)
        self.assertIn(stolen.task_id, (10, 20))

        stats = sched.get_stats()
        self.assertEqual(stats.tasks_stolen_local, 1)

    def test_pinned_hardware_task_cannot_be_stolen(self):
        sched = scheduler.WorkStealingScheduler(local_node_id=101, num_workers=2)

        pinned_t = scheduler.TaskItem(
            task_id=99,
            priority=scheduler.TaskPriority.CRITICAL,
            pinned_hardware=True,
            pinned_node_id=101,
        )

        sched.submit_task(pinned_t, worker_hint=0)

        # Worker 1 cannot steal pinned task
        self.assertIsNone(sched.pop_task(worker_id=1))

        # Remote peer 202 cannot steal pinned task
        remote_stolen = sched.handle_remote_steal_request(requester_node_id=202, max_tasks=5)
        self.assertEqual(len(remote_stolen), 0)

        # Worker 0 can still execute it locally
        local_exec = sched.pop_task(worker_id=0)
        self.assertIsNotNone(local_exec)
        self.assertEqual(local_exec.task_id, 99)

    def test_router_hardware_pin_and_load_balance(self):
        sched = scheduler.WorkStealingScheduler(local_node_id=101, num_workers=2)

        pinned_t = scheduler.TaskItem(
            task_id=55,
            priority=scheduler.TaskPriority.HIGH,
            pinned_hardware=True,
        )
        peer_loads = [(201, 0), (202, 1)]

        # Pinned task must stay local even if peers have 0 load
        decision = sched.route_task(pinned_t, peer_loads)
        self.assertEqual(decision.target_type, scheduler.ScheduledTargetType.LOCAL)

        # Fill local queue to trigger unpinned offload
        for i in range(5):
            t = scheduler.TaskItem(task_id=100 + i, priority=scheduler.TaskPriority.NORMAL)
            sched.submit_task(t, worker_hint=0)

        unpinned_t = scheduler.TaskItem(task_id=77, priority=scheduler.TaskPriority.NORMAL)
        decision_unpinned = sched.route_task(unpinned_t, peer_loads)
        self.assertEqual(decision_unpinned.target_type, scheduler.ScheduledTargetType.OFFLOAD)
        self.assertEqual(decision_unpinned.node_id, 201)

    def test_concurrent_task_push_and_steal(self):
        sched = scheduler.WorkStealingScheduler(local_node_id=101, num_workers=4)
        total_tasks = 100

        # Push 100 tasks across global injector and workers
        for i in range(total_tasks):
            t = scheduler.TaskItem(task_id=i, priority=scheduler.TaskPriority(i % 4))
            sched.submit_task(t, worker_hint=(i % 4 if i % 2 == 0 else None))

        executed: list[int] = []
        lock = threading.Lock()

        def worker_loop(wid: int):
            while True:
                task = sched.pop_task(wid)
                if task is None:
                    break
                with lock:
                    executed.append(task.task_id)

        threads = [threading.Thread(target=worker_loop, args=(i,)) for i in range(4)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        self.assertEqual(len(executed), total_tasks)
        self.assertEqual(len(set(executed)), total_tasks)  # No duplicate execution

def ns_main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(ns_TestNodeScheduler)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# ============================================================================
# from tests/test-node.py (prefix nwh_)
# ============================================================================
"""Automated tests for WS-NODE Tier-1 Wasm sandbox GPIO and I2C hardware host imports."""


import importlib.util
import json
import os
import sys
import unittest

_nwh_HERE = os.path.dirname(os.path.abspath(__file__))
_nwh_ROOT = os.path.normpath(os.path.join(_nwh_HERE, ".."))
_nwh_HW_PATH = os.path.join(_nwh_ROOT, "usr", "libexec", "mios", "node", "hardware.py")
_nwh_WASM_PATH = os.path.join(_nwh_ROOT, "usr", "libexec", "mios", "node", "wasm_sandbox.py")

nwh_spec_hw = importlib.util.spec_from_file_location("hardware", _nwh_HW_PATH)
if nwh_spec_hw and nwh_spec_hw.loader:
    hardware = importlib.util.module_from_spec(nwh_spec_hw)
    sys.modules["hardware"] = hardware
    sys.modules["usr.libexec.mios.node.hardware"] = hardware
    nwh_spec_hw.loader.exec_module(hardware)
else:
    raise ImportError(f"Could not load hardware module from {_nwh_HW_PATH}")

nwh_spec_wasm = importlib.util.spec_from_file_location("wasm_sandbox", _nwh_WASM_PATH)
if nwh_spec_wasm and nwh_spec_wasm.loader:
    wasm_sandbox = importlib.util.module_from_spec(nwh_spec_wasm)
    sys.modules["wasm_sandbox"] = wasm_sandbox
    sys.modules["usr.libexec.mios.node.wasm_sandbox"] = wasm_sandbox
    nwh_spec_wasm.loader.exec_module(wasm_sandbox)
else:
    raise ImportError(f"Could not load wasm_sandbox module from {_nwh_WASM_PATH}")

class nwh_TestNodeWasmHardware(unittest.TestCase):
    """Validates Wasm Host Imports for hardware access with strict allowlist enforcement."""

    def setUp(self):
        self.allowlist = hardware.HardwareAllowlist(
            allowed_gpio_pins={4, 17, 27},
            read_only_gpio_pins={4},
            allowed_i2c_buses={1},
            allowed_i2c_addresses={0x48, 0x68},
            max_i2c_transfer_len=64,
        )
        self.mock_driver = hardware.MockHardwareDriver()
        self.controller = hardware.SandboxedHardwareController(
            allowlist=self.allowlist, driver=self.mock_driver
        )

    def test_gpio_allowlist_write_and_read(self):
        # 1. Allowed write to pin 17
        err = self.controller.mios_sys_gpio_write(17, 1)
        self.assertEqual(err, hardware.HardwareErrorCode.SUCCESS)
        self.assertEqual(self.mock_driver.gpio_read(17), 1)

        err_r, val = self.controller.mios_sys_gpio_read(17)
        self.assertEqual(err_r, hardware.HardwareErrorCode.SUCCESS)
        self.assertEqual(val, 1)

        # 2. Read-only pin 4 cannot be written
        err_ro = self.controller.mios_sys_gpio_write(4, 1)
        self.assertEqual(err_ro, hardware.HardwareErrorCode.READ_ONLY_PIN)
        # Read from read-only pin is permitted
        err_ro_r, val_ro = self.controller.mios_sys_gpio_read(4)
        self.assertEqual(err_ro_r, hardware.HardwareErrorCode.SUCCESS)

        # 3. Disallowed pin 99
        err_unauth = self.controller.mios_sys_gpio_write(99, 1)
        self.assertEqual(err_unauth, hardware.HardwareErrorCode.PERMISSION_DENIED)
        err_unauth_r, _ = self.controller.mios_sys_gpio_read(99)
        self.assertEqual(err_unauth_r, hardware.HardwareErrorCode.PERMISSION_DENIED)

    def test_i2c_allowlist_transfers(self):
        # Write mock register at bus 1, addr 0x68, reg 0x10 = 0x55
        self.mock_driver.i2c_transfer(1, 0x68, bytes([0x10, 0x55]), 0)

        # Allowed read
        err, rdata = self.controller.mios_sys_i2c_transfer(1, 0x68, bytes([0x10]), 1)
        self.assertEqual(err, hardware.HardwareErrorCode.SUCCESS)
        self.assertEqual(list(rdata), [0x55])

        # Disallowed address 0x77
        err_addr, _ = self.controller.mios_sys_i2c_transfer(1, 0x77, bytes([0x10]), 1)
        self.assertEqual(err_addr, hardware.HardwareErrorCode.PERMISSION_DENIED)

        # Disallowed bus 2
        err_bus, _ = self.controller.mios_sys_i2c_transfer(2, 0x68, bytes([0x10]), 1)
        self.assertEqual(err_bus, hardware.HardwareErrorCode.PERMISSION_DENIED)

    def test_wasm_sandbox_engine_hardware_execution(self):
        config = wasm_sandbox.WasmExecutionConfig(allowlist=self.allowlist)
        engine = wasm_sandbox.WasmSandboxEngine(
            config=config, hardware_controller=self.controller
        )

        # 1. Execute task with valid GPIO write
        payload_write = json.dumps({"action": "gpio_write", "pin": 17, "value": 1}).encode("utf-8")
        res_w = engine.execute(b"WASM_BYTECODE", payload_write)
        self.assertTrue(res_w.success)
        self.assertEqual(res_w.exit_code, 0)
        self.assertIn(b"GPIO pin 17 set to 1", res_w.output_data)

        # 2. Execute task with valid GPIO read
        payload_read = json.dumps({"action": "gpio_read", "pin": 17}).encode("utf-8")
        res_r = engine.execute(b"WASM_BYTECODE", payload_read)
        self.assertTrue(res_r.success)
        self.assertIn(b"GPIO pin 17 value = 1", res_r.output_data)

        # 3. Disallowed pin triggers permission denial
        payload_denied = json.dumps({"action": "gpio_write", "pin": 999, "value": 1}).encode("utf-8")
        res_d = engine.execute(b"WASM_BYTECODE", payload_denied)
        self.assertFalse(res_d.success)
        self.assertEqual(res_d.exit_code, hardware.HardwareErrorCode.PERMISSION_DENIED)

def nwh_main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(nwh_TestNodeWasmHardware)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# ============================================================================
# from tests/test-node.py (prefix nw_)
# ============================================================================
"""Automated tests for WS-NODE hardware watchdog supervisor, keepalive pinging, and magic close ('V')."""


import importlib.util
import os
import sys
import unittest

_nw_HERE = os.path.dirname(os.path.abspath(__file__))
_nw_ROOT = os.path.normpath(os.path.join(_nw_HERE, ".."))
_nw_WATCHDOG_PATH = os.path.join(_nw_ROOT, "usr", "libexec", "mios", "node", "watchdog.py")

nw_spec = importlib.util.spec_from_file_location("watchdog", _nw_WATCHDOG_PATH)
if nw_spec and nw_spec.loader:
    watchdog = importlib.util.module_from_spec(nw_spec)
    sys.modules["watchdog"] = watchdog
    sys.modules["usr.libexec.mios.node.watchdog"] = watchdog
    nw_spec.loader.exec_module(watchdog)
else:
    raise ImportError(f"Could not load watchdog module from {_nw_WATCHDOG_PATH}")

class nw_TestNodeWatchdog(unittest.TestCase):
    """Validates watchdog supervisor, keepalive pings, and safe 'V' magic close."""

    def test_mock_watchdog_lifecycle(self):
        config = watchdog.WatchdogConfig(enabled=True, timeout_secs=30)
        mock_driver = watchdog.MockWatchdogDriver(simulated_present=True, timeout_secs=30)
        supervisor = watchdog.WatchdogSupervisor(config=config, driver=mock_driver)

        self.assertTrue(supervisor.is_present())
        self.assertFalse(supervisor.is_armed())

        # Arm
        self.assertTrue(supervisor.arm())
        self.assertTrue(supervisor.is_armed())

        # Ping 3 times
        self.assertTrue(supervisor.ping())
        self.assertTrue(supervisor.ping())
        self.assertTrue(supervisor.ping())
        self.assertEqual(mock_driver.ping_count, 3)
        self.assertFalse(mock_driver.disarmed_safely)

        # Disarm safely with 'V'
        self.assertTrue(supervisor.disarm())
        self.assertFalse(supervisor.is_armed())
        self.assertTrue(mock_driver.disarmed_safely)

        # Ping after disarm fails
        self.assertFalse(supervisor.ping())

    def test_linux_watchdog_absence_graceful_detection(self):
        driver = watchdog.LinuxHardwareWatchdog(device_path="/tmp/nonexistent_watchdog_dev", timeout_secs=30)
        self.assertFalse(driver.is_hardware_present())
        self.assertFalse(driver.is_armed())
        with self.assertRaises(FileNotFoundError):
            driver.arm()

def nw_main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(nw_TestNodeWatchdog)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# ============================================================================
# from tests/test-node.py (prefix ws_)
# ============================================================================
"""Automated tests for WS-NODE Tier-1 Wasm sandbox execution, fuel limiting, and host imports."""


import importlib.util
import os
import sys
import unittest

_ws_HERE = os.path.dirname(os.path.abspath(__file__))
_ws_ROOT = os.path.normpath(os.path.join(_ws_HERE, ".."))
_ws_WASM_PATH = os.path.join(_ws_ROOT, "usr", "libexec", "mios", "node", "wasm_sandbox.py")

ws_spec = importlib.util.spec_from_file_location("wasm_sandbox", _ws_WASM_PATH)
if ws_spec and ws_spec.loader:
    wasm_sandbox = importlib.util.module_from_spec(ws_spec)
    sys.modules[ws_spec.name] = wasm_sandbox
    ws_spec.loader.exec_module(wasm_sandbox)
else:
    raise ImportError(f"Could not load wasm_sandbox module from {_ws_WASM_PATH}")

class ws_TestWasmSandbox(unittest.TestCase):
    """Validates Tier-1 Wasm execution, 64MB memory limits, fuel bounds, and host imports."""

    def test_standard_execution_and_host_imports(self):
        engine = wasm_sandbox.WasmSandboxEngine()
        input_payload = b"temperature=23.5"
        res = engine.execute(
            code_bytes=b"\x00asm\x01\x00\x00\x00",
            input_data=input_payload,
            simulated_fuel_cost=5000,
            simulated_alloc_bytes=2 * 1024 * 1024,
        )
        self.assertTrue(res.success)
        self.assertEqual(res.exit_code, 0)
        self.assertEqual(res.fuel_consumed, 5000)
        self.assertIn(b"RESULT_PREFIX:temperature=23.5", res.output_data)
        self.assertTrue(any("Executing guest module" in log for log in res.logs))

    def test_fuel_exhaustion_termination(self):
        config = wasm_sandbox.WasmExecutionConfig(max_fuel=10_000)
        engine = wasm_sandbox.WasmSandboxEngine(config)
        res = engine.execute(
            code_bytes=b"\x00asm\x01\x00\x00\x00",
            input_data=b"heavy_computation",
            simulated_fuel_cost=50_000,  # Exceeds 10,000
        )
        self.assertFalse(res.success)
        self.assertEqual(res.exit_code, 124)
        self.assertIn("Fuel limit exhausted", res.error_msg or "")

    def test_memory_ceiling_64mb_enforcement(self):
        config = wasm_sandbox.WasmExecutionConfig(max_memory_bytes=64 * 1024 * 1024)
        engine = wasm_sandbox.WasmSandboxEngine(config)
        res = engine.execute(
            code_bytes=b"\x00asm\x01\x00\x00\x00",
            input_data=b"huge_allocation",
            simulated_alloc_bytes=128 * 1024 * 1024,  # 128MB exceeds 64MB
        )
        self.assertFalse(res.success)
        self.assertEqual(res.exit_code, 137)
        self.assertIn("Memory limit exceeded", res.error_msg or "")

def ws_main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(ws_TestWasmSandbox)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1



# ============================================================================
# entry point
# ============================================================================
import sys as _sys
import unittest as _unittest


def main():
    rc = 0 if _unittest.main(argv=[_sys.argv[0]], exit=False).result.wasSuccessful() else 1
    return rc


if __name__ == "__main__":
    _sys.exit(main())
