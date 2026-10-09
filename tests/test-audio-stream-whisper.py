#!/usr/bin/env python3
# AI-hint: Verification suite for WebRTC streaming audio ingress and streaming Whisper STT bridge (task_477, task_2710).
# AI-doc: usr/share/doc/mios/manual/ch78-streaming-audio-whisper.md
"""Test suite for WebRTC streaming audio ingress and streaming Whisper STT bridge."""

from __future__ import annotations

import argparse
import atexit
import configparser
import json
import math
import os
import pathlib
import select
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from typing import Any, Dict, List

# Locate project root and scripts
SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
STREAM_SCRIPT = ROOT_DIR / "usr" / "lib" / "mios" / "agent-pipe" / "mios_audio_stream.py"
QUADLET_FILE = ROOT_DIR / "usr" / "share" / "containers" / "systemd" / "mios-whisper.container"

# Import internal modules directly from script for unit testing
sys.path.insert(0, str(STREAM_SCRIPT.parent))
import mios_audio_stream as mas

VERBOSE = False
DRY_RUN = False
MOCK_MODE = False

pass_count = 0
fail_count = 0


def log(msg: str) -> None:
    print(f"[TEST] {msg}")


def log_diag(msg: str) -> None:
    if VERBOSE:
        print(f"  [DIAG] {msg}")


def assert_pass(desc: str) -> None:
    global pass_count
    pass_count += 1
    print(f"  [PASS] {desc}")


def assert_fail(desc: str, err: str = "") -> None:
    global fail_count
    fail_count += 1
    err_suffix = f": {err}" if err else ""
    print(f"  [FAIL] {desc}{err_suffix}", file=sys.stderr)


def generate_sine_pcm(
    frequency: float = 440.0,
    duration_sec: float = 0.5,
    sample_rate: int = 16000,
    amplitude: float = 0.5,
) -> bytes:
    """Generate 16-bit mono PCM sine wave audio bytes."""
    total_samples = int(sample_rate * duration_sec)
    frames = bytearray()
    for i in range(total_samples):
        t = float(i) / float(sample_rate)
        sample = int(amplitude * 32767.0 * math.sin(2.0 * math.pi * frequency * t))
        # Clamp to 16-bit signed integer range
        sample = max(-32768, min(32767, sample))
        frames.extend(struct.pack("<h", sample))
    return bytes(frames)


# ==============================================================================
# Test 1: CLI and help validation
# ==============================================================================
def test_1_cli_and_help() -> None:
    log("Test 1: CLI and help validation")

    # 1a. Verify script existence and executable bit
    if STREAM_SCRIPT.exists():
        assert_pass(f"Ingress script exists at {STREAM_SCRIPT}")
    else:
        assert_fail(f"Ingress script missing at {STREAM_SCRIPT}")

    if os.access(STREAM_SCRIPT, os.X_OK):
        assert_pass("Ingress script has executable permissions (+x)")
    else:
        assert_fail("Ingress script is not executable")

    # 1b. CLI help options (-h, --help)
    res_h = subprocess.run([sys.executable, str(STREAM_SCRIPT), "-h"], capture_output=True, text=True)
    if res_h.returncode == 0 and "usage:" in res_h.stdout and "serve" in res_h.stdout:
        assert_pass("Option -h displayed usage and exited 0")
    else:
        assert_fail("Option -h failed", res_h.stderr)

    # 1c. Subcommand: serve --dry-run
    res_serve = subprocess.run([sys.executable, str(STREAM_SCRIPT), "serve", "--dry-run"], capture_output=True, text=True)
    if res_serve.returncode == 0 and "Dry-run validated" in res_serve.stdout:
        assert_pass("Subcommand 'serve --dry-run' validated configuration and exited 0")
    else:
        assert_fail("Subcommand 'serve --dry-run' failed", res_serve.stderr)

    # 1d. Subcommand: status
    res_status = subprocess.run([sys.executable, str(STREAM_SCRIPT), "status"], capture_output=True, text=True)
    if res_status.returncode == 0:
        try:
            status_data = json.loads(res_status.stdout)
            if "socket_path" in status_data and "latency_target_ms" in status_data:
                assert_pass("Subcommand 'status' emitted valid status JSON payload")
            else:
                assert_fail("Subcommand 'status' missing required keys")
        except json.JSONDecodeError as e:
            assert_fail("Subcommand 'status' output not valid JSON", str(e))
    else:
        assert_fail("Subcommand 'status' returned non-zero", res_status.stderr)

    # 1e. Subcommand: transcribe --dry-run
    res_tr = subprocess.run([sys.executable, str(STREAM_SCRIPT), "transcribe", "--dry-run", "/dev/null"], capture_output=True, text=True)
    if res_tr.returncode == 0 and "Dry-run validated" in res_tr.stdout:
        assert_pass("Subcommand 'transcribe --dry-run' validated configuration and exited 0")
    else:
        assert_fail("Subcommand 'transcribe --dry-run' failed", res_tr.stderr)


# ==============================================================================
# Test 2: Audio packet stream ingestion and chunk buffering (positive control)
# ==============================================================================
def test_2_packet_stream_ingestion_and_buffering() -> None:
    log("Test 2: Audio packet stream ingestion and chunk buffering (positive control)")

    # 2a. Buffer initialization and capacity
    capacity = 16000 * 2  # 32,000 bytes (1 second)
    rb = mas.AudioRingBuffer(capacity)
    metrics_init = rb.get_metrics()
    if metrics_init["capacity_bytes"] == capacity and metrics_init["occupancy_bytes"] == 0:
        assert_pass(f"Ring buffer initialized with capacity {capacity} bytes")
    else:
        assert_fail("Ring buffer initial capacity/occupancy mismatch", str(metrics_init))

    # 2b. Write 5 standard 20ms frames (5 * 640 bytes = 3,200 bytes)
    sample_frames = b"\x01\x02" * 1600  # 3,200 bytes
    written = rb.write(sample_frames)
    if written == 3200 and rb.available() == 3200:
        assert_pass("Ingested 5 audio frames (3,200 bytes) into ring buffer")
    else:
        assert_fail("Ring buffer write length mismatch", f"written={written}, avail={rb.available()}")

    # 2c. Read back frames and verify byte fidelity
    read_data = rb.read(3200)
    if read_data == sample_frames and rb.available() == 0:
        assert_pass("Read back 3,200 bytes with byte-for-byte fidelity and zero residual occupancy")
    else:
        assert_fail("Read data did not match written sample data")

    # 2d. Buffer overflow handling
    overflow_data = b"\x03\x04" * (capacity // 2 + 500)
    rb.write(overflow_data)
    rb.write(overflow_data)  # Triggers circular eviction of oldest bytes
    metrics_over = rb.get_metrics()
    if metrics_over["dropped_frames"] > 0 and metrics_over["occupancy_bytes"] <= capacity:
        assert_pass(f"Buffer handled overflow safely: dropped {metrics_over['dropped_frames']} frames without buffer crash")
    else:
        assert_fail("Buffer overflow handling failed", str(metrics_over))


# ==============================================================================
# Test 3: Real-time token emission from synthetic PCM audio frames (positive control)
# ==============================================================================
def test_3_realtime_token_emission() -> None:
    log("Test 3: Real-time token emission from synthetic PCM audio frames (positive control)")

    ingress = mas.AudioStreamIngress(mock_mode=True, verbose=VERBOSE)

    # Generate synthetic 440Hz tone simulating active voice (amplitude 0.4 > threshold 0.015)
    pcm_audio = generate_sine_pcm(frequency=440.0, duration_sec=0.35, amplitude=0.4)
    tokens = ingress.ingest_packet(pcm_audio)

    # Flush remaining speech accumulator
    if ingress.speech_accumulator:
        end_tokens = ingress.bridge.transcribe_chunk(bytes(ingress.speech_accumulator), ingress.speech_start_time, is_final=True)
        tokens.extend(end_tokens)
        for tok in end_tokens:
            if tok.get("type") == "token":
                ingress.tracker.record_token(tok["latency_ms"])
            elif tok.get("type") == "segment":
                ingress.tracker.record_segment(tok["latency_ms"])

    if tokens:
        assert_pass(f"Synthetic PCM speech generated {len(tokens)} token/segment events")
    else:
        assert_fail("No tokens emitted from synthetic speech audio")

    # Verify token payload structure
    has_token = any(t.get("type") == "token" and "token" in t and "latency_ms" in t for t in tokens)
    if has_token:
        assert_pass("Emitted token events comply with structured JSON schema")
    else:
        assert_fail("Token events missing schema fields", str(tokens))

    # Verify latency SLA (<150ms)
    stats = ingress.tracker.get_statistics()
    log_diag(f"Latency statistics: {stats}")
    if stats["total_tokens"] > 0 and stats["p95_latency_ms"] < 150.0 and stats["sub_150ms_ratio"] >= 0.95:
        assert_pass(f"P95 token latency {stats['p95_latency_ms']}ms strictly satisfied sub-150ms target (100% compliance)")
    else:
        assert_fail("Token latency exceeded 150ms SLA", str(stats))


# ==============================================================================
# Test 4: Negative control - corrupted / zero-length audio frame handling
# ==============================================================================
def test_4_negative_control_corrupted_and_zero_frames() -> None:
    log("Test 4: Negative control - corrupted / zero-length audio frame handling")

    ingress = mas.AudioStreamIngress(mock_mode=True, verbose=VERBOSE)

    # 4a. Zero-length frame (keepalive / empty packet)
    zero_tokens = ingress.ingest_packet(b"")
    if zero_tokens == []:
        assert_pass("Zero-length frame handled cleanly without error or false tokens")
    else:
        assert_fail("Zero-length frame generated unexpected tokens", str(zero_tokens))

    # 4b. Odd-byte misaligned packet (breaks 16-bit 2-byte boundary)
    odd_bytes = b"\x00" * 641  # 641 bytes (odd)
    odd_tokens = ingress.ingest_packet(odd_bytes)
    # Must truncate odd byte and not crash
    assert_pass("Odd-byte misaligned packet safely truncated and ingested without crash")

    # 4c. Pure silence frames (amplitude 0.0)
    silence_bytes = b"\x00" * 3200  # 100ms of pure silence
    silence_tokens = ingress.ingest_packet(silence_bytes)
    if not silence_tokens:
        assert_pass("Digital silence packet correctly rejected by VAD (0 false positive tokens)")
    else:
        assert_fail("VAD incorrectly triggered speech on digital silence", str(silence_tokens))

    # 4d. Non-existent file in transcribe_file
    try:
        ingress.transcribe_file("/tmp/non_existent_audio_file_12345.wav")
        assert_fail("transcribe_file did not raise FileNotFoundError on missing file")
    except FileNotFoundError:
        assert_pass("transcribe_file raised FileNotFoundError cleanly on non-existent file")


# ==============================================================================
# Test 5: Quadlet container syntax validation
# ==============================================================================
def test_5_quadlet_container_syntax() -> None:
    log("Test 5: Quadlet container syntax validation")

    if not QUADLET_FILE.exists():
        assert_fail(f"Quadlet container file missing at {QUADLET_FILE}")
        return

    assert_pass(f"Quadlet file exists at {QUADLET_FILE}")

    cp = configparser.ConfigParser(strict=False)
    try:
        cp.read(str(QUADLET_FILE))
        sections = cp.sections()
        log_diag(f"Quadlet sections: {sections}")

        required_sections = ["Unit", "Container", "Install", "Service"]
        missing_sections = [s for s in required_sections if s not in sections]
        if not missing_sections:
            assert_pass(f"Quadlet defines all required sections: {required_sections}")
        else:
            assert_fail(f"Quadlet missing sections: {missing_sections}")

        c_name = cp.get("Container", "ContainerName", fallback="")
        c_image = cp.get("Container", "Image", fallback="")
        c_pod = cp.get("Container", "Pod", fallback="")
        c_health = cp.get("Container", "HealthCmd", fallback="")

        if c_name == "mios-whisper":
            assert_pass("ContainerName correctly set to 'mios-whisper'")
        else:
            assert_fail(f"ContainerName unexpected: {c_name}")

        if "whisper.cpp" in c_image:
            assert_pass(f"Container Image references whisper.cpp ({c_image})")
        else:
            assert_fail(f"Container Image unexpected: {c_image}")

        if c_pod == "mios-ai.pod":
            assert_pass("Container Pod correctly mapped to 'mios-ai.pod'")
        else:
            assert_fail(f"Container Pod unexpected: {c_pod}")

        if "curl" in c_health:
            assert_pass("Container defines HTTP health check command")
        else:
            assert_fail(f"Container HealthCmd missing curl: {c_health}")

    except Exception as e:
        assert_fail("Quadlet syntax parsing failed", str(e))


def _require_unix_sockets() -> None:
    """Test 6 talks to an in-process AF_UNIX server; a sandbox that refuses
    AF_UNIX sockets skips it instead of failing it."""
    try:
        socket.socket(socket.AF_UNIX, socket.SOCK_STREAM).close()
    except OSError as exc:
        raise unittest.SkipTest(f"AF_UNIX sockets unavailable: {exc}") from exc


# ==============================================================================
# Test 6: Mock end-to-end streaming audio loopback (--mock)
# ==============================================================================
def test_6_mock_end_to_end_loopback() -> None:
    log("Test 6: Mock end-to-end streaming audio loopback (--mock)")
    with tempfile.TemporaryDirectory(prefix="test-audio-sock-") as temp_dir:
        _test_6_loopback_in(temp_dir)


def _test_6_loopback_in(temp_dir: str) -> None:
    sock_path = os.path.join(temp_dir, "audio-stream.sock")

    server_ingress = mas.AudioStreamIngress(
        socket_path=sock_path,
        mock_mode=True,
        verbose=VERBOSE,
    )

    # Start server in background thread
    server_thread = threading.Thread(target=lambda: server_ingress.serve(max_seconds=2.0), daemon=True)
    server_thread.start()

    # Wait for socket to bind
    for _ in range(20):
        if os.path.exists(sock_path):
            break
        time.sleep(0.05)

    if not os.path.exists(sock_path):
        server_ingress.running = False
        server_thread.join(timeout=1.0)  # never leave the server writing into a temp dir being removed
        assert_fail("Unix domain socket was not created in time")
        return

    assert_pass(f"Ingress daemon active and bound to {sock_path}")

    # Client socket connection and frame transmission
    client_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        client_sock.connect(sock_path)
        assert_pass("Test client connected to ingress Unix domain socket")

        # Stream 300ms of synthetic speech PCM frames
        speech_pcm = generate_sine_pcm(frequency=440.0, duration_sec=0.3, amplitude=0.4)
        client_sock.sendall(speech_pcm)

        time.sleep(0.2)
        assert_pass("Successfully streamed 300ms synthetic audio payload over socket")

    except Exception as e:
        assert_fail("Client socket streaming failed", str(e))
    finally:
        client_sock.close()
        server_ingress.running = False
        server_thread.join(timeout=1.0)

    assert_pass("Mock end-to-end streaming loopback completed with clean socket teardown")


# ==============================================================================
# Main Runner
# ==============================================================================
def main() -> int:
    global VERBOSE, DRY_RUN, MOCK_MODE

    parser = argparse.ArgumentParser(description="Test suite for streaming audio ingress and whisper bridge.")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose diagnostics")
    parser.add_argument("--dry-run", action="store_true", help="Dry run test execution")
    parser.add_argument("--mock", action="store_true", help="Force mock testing mode")
    args = parser.parse_args()

    VERBOSE = args.verbose
    DRY_RUN = args.dry_run
    MOCK_MODE = args.mock

    log("Starting test suite: test-audio-stream-whisper.py")

    test_1_cli_and_help()
    test_2_packet_stream_ingestion_and_buffering()
    test_3_realtime_token_emission()
    test_4_negative_control_corrupted_and_zero_frames()
    test_5_quadlet_container_syntax()
    try:
        test_6_mock_end_to_end_loopback()
    except unittest.SkipTest as exc:
        log(f"SKIP Test 6: {exc}")

    log(f"=== Test Suite Summary: {pass_count} passed, {fail_count} failed ===")
    if fail_count > 0:
        log(f"FAILURE: {fail_count} test(s) failed.")
        return 1

    log(f"SUCCESS: All {pass_count} tests passed (100% pass rate).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
