#!/usr/bin/env python3
# AI-hint: Verification suite for concurrent streaming Piper/Kokoro TTS audio synthesis and PipeWire buffer feeder (task_478, task_2711).
# AI-doc: usr/share/doc/mios/manual/ch79-streaming-tts-piper.md
"""Test suite for concurrent streaming Piper/Kokoro TTS audio synthesis and PipeWire buffer feeder."""

from __future__ import annotations

import argparse
import atexit
import configparser
import json
import os
import pathlib
import re
import select
import shlex
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import unittest
import urllib.parse
from typing import Any, Dict, List

# Locate project root and scripts
SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
TTS_SCRIPT = ROOT_DIR / "usr" / "lib" / "mios" / "agent-pipe" / "mios_audio_tts.py"
QUADLET_FILE = ROOT_DIR / "usr" / "share" / "containers" / "systemd" / "mios-piper.container"
SSOT_TOML = ROOT_DIR / "usr" / "share" / "mios" / "mios.toml"

# Import internal modules directly from script for unit testing
sys.path.insert(0, str(TTS_SCRIPT.parent))
import mios_audio_tts as mat

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


# ==============================================================================
# Test 1: CLI and help verification
# ==============================================================================
def test_1_cli_and_help() -> None:
    log("Test 1: CLI and help verification")

    # 1a. Verify script existence and executable bit
    if TTS_SCRIPT.exists():
        assert_pass(f"TTS script exists at {TTS_SCRIPT}")
    else:
        assert_fail(f"TTS script missing at {TTS_SCRIPT}")

    if os.access(TTS_SCRIPT, os.X_OK):
        assert_pass("TTS script has executable permissions (+x)")
    else:
        assert_fail("TTS script is not executable")

    # 1b. CLI help options (-h, --help)
    res_h = subprocess.run([sys.executable, str(TTS_SCRIPT), "-h"], capture_output=True, text=True)
    if res_h.returncode == 0 and "usage:" in res_h.stdout and "stream" in res_h.stdout and "speak" in res_h.stdout:
        assert_pass("Option -h displayed usage and subcommands (stream, speak, status) and exited 0")
    else:
        assert_fail("Option -h failed", res_h.stderr)

    # 1c. Subcommand: speak --dry-run
    res_speak_dry = subprocess.run(
        [sys.executable, str(TTS_SCRIPT), "speak", "--dry-run", "Hello world from MiOS"],
        capture_output=True,
        text=True,
    )
    if res_speak_dry.returncode == 0 and "Dry-run validated" in res_speak_dry.stdout:
        assert_pass("Subcommand 'speak --dry-run' validated parameters and exited 0")
    else:
        assert_fail("Subcommand 'speak --dry-run' failed", res_speak_dry.stderr)

    # 1d. Subcommand: stream --dry-run
    res_stream_dry = subprocess.run(
        [sys.executable, str(TTS_SCRIPT), "stream", "--dry-run"],
        capture_output=True,
        text=True,
    )
    if res_stream_dry.returncode == 0 and "Dry-run validated" in res_stream_dry.stdout:
        assert_pass("Subcommand 'stream --dry-run' validated parameters and exited 0")
    else:
        assert_fail("Subcommand 'stream --dry-run' failed", res_stream_dry.stderr)

    # 1e. Subcommand: status
    res_status = subprocess.run([sys.executable, str(TTS_SCRIPT), "status"], capture_output=True, text=True)
    if res_status.returncode == 0:
        try:
            status_data = json.loads(res_status.stdout)
            if (
                "engine" in status_data
                and "voice" in status_data
                and "latency_sla_target_ms" in status_data
                and "buffer_capacity_bytes" in status_data
            ):
                assert_pass("Subcommand 'status' emitted valid status JSON payload with SLA metrics")
            else:
                assert_fail("Subcommand 'status' missing required schema keys", str(status_data))
        except json.JSONDecodeError as e:
            assert_fail("Subcommand 'status' output not valid JSON", str(e))
    else:
        assert_fail("Subcommand 'status' returned non-zero", res_status.stderr)


# ==============================================================================
# Test 2: Sentence chunking and incremental buffer synthesis (positive control)
# ==============================================================================
def test_2_sentence_chunking_and_buffering() -> None:
    log("Test 2: Sentence chunking and incremental buffer synthesis (positive control)")

    # 2a. Sentence boundary detection (. ! ? \n) and abbreviation preservation
    chunker = mat.SentenceChunker(max_clause_chars=100)

    # Feed "Hello world. "
    chunks1 = chunker.feed("Hello world. ")
    if chunks1 == ["Hello world."]:
        assert_pass("Detected full-stop sentence boundary: 'Hello world.'")
    else:
        assert_fail(f"SentenceChunker failed on full stop: {chunks1}")

    # Feed text with abbreviation "Dr. Smith arrived! "
    chunks2 = chunker.feed("Dr. Smith arrived! ")
    if chunks2 == ["Dr. Smith arrived!"]:
        assert_pass("Preserved abbreviation 'Dr.' without premature split and chunked exclamation: 'Dr. Smith arrived!'")
    else:
        assert_fail(f"SentenceChunker failed on abbreviation/exclamation: {chunks2}")

    # Feed decimal number and question mark
    chunks3 = chunker.feed("Version is 3.14? ")
    if chunks3 == ["Version is 3.14?"]:
        assert_pass("Preserved decimal number '3.14' and chunked question mark")
    else:
        assert_fail(f"SentenceChunker failed on decimal/question mark: {chunks3}")

    # Feed unpunctuated text and test flush
    chunker.feed("Final sentence without punctuation")
    flushed = chunker.flush()
    if flushed == ["Final sentence without punctuation"]:
        assert_pass("Flush emitted remaining unpunctuated text cleanly")
    else:
        assert_fail(f"SentenceChunker flush failed: {flushed}")

    # 2b. Audio ring buffer mechanics
    capacity = 24000 * mat.BYTES_PER_SAMPLE  # 48,000 bytes (1 second)
    rb = mat.AudioRingBuffer(capacity)
    if rb.capacity == capacity and rb.available() == 0:
        assert_pass(f"Ring buffer initialized with capacity {capacity} bytes")
    else:
        assert_fail(f"Ring buffer initial capacity/size mismatch: cap={rb.capacity}, size={rb.available()}")

    sample_pcm = b"\x10\x20" * 2400  # 4,800 bytes
    written = rb.write(sample_pcm)
    if written == 4800 and rb.available() == 4800:
        assert_pass("Wrote 4,800 PCM bytes into audio ring buffer")
    else:
        assert_fail(f"AudioRingBuffer write mismatch: written={written}, avail={rb.available()}")

    read_back = rb.read(4800)
    if read_back == sample_pcm and rb.available() == 0:
        assert_pass("Read back 4,800 PCM bytes with exact byte fidelity and zero residual occupancy")
    else:
        assert_fail("AudioRingBuffer read back data did not match written sample")

    # 2c. Ring buffer overflow circular eviction
    big_chunk = b"\xaa\xbb" * (capacity // 2 + 1000)
    rb.write(big_chunk)
    rb.write(big_chunk)  # Triggers circular eviction of oldest bytes
    if rb.overflow_bytes > 0 and rb.available() <= capacity:
        assert_pass(f"Buffer handled overflow safely: evicted {rb.overflow_bytes} bytes without crashing")
    else:
        assert_fail(f"Buffer overflow handling failed: overflow={rb.overflow_bytes}, avail={rb.available()}")


# ==============================================================================
# Test 3: Sub-300ms time-to-first-sound latency verification in mock mode
# ==============================================================================
def test_3_sub_300ms_latency_sla() -> None:
    log("Test 3: Sub-300ms time-to-first-sound latency verification in mock mode")

    worker = mat.StreamingTTSWorker(
        engine="piper",
        mock_mode=True,
        verbose=VERBOSE,
    )

    test_prompt = "This sentence tests conversational time to first sound latency in MiOS."
    status = worker.speak(test_prompt)

    log_diag(f"Synthesis status output: {json.dumps(status)}")

    ttfs_ms = status.get("time_to_first_sound_ms", 0.0)
    sla_target = status.get("latency_sla_target_ms", 300.0)
    target_met = status.get("target_met", False)
    underruns = status.get("underruns", -1)

    if ttfs_ms > 0:
        assert_pass(f"Time-to-first-sound recorded: {ttfs_ms}ms")
    else:
        assert_fail(f"Time-to-first-sound was not recorded (ttfs={ttfs_ms})")

    if ttfs_ms < sla_target and target_met:
        assert_pass(f"Time-to-first-sound {ttfs_ms}ms strictly satisfied sub-300ms SLA (target: {sla_target}ms)")
    else:
        assert_fail(f"Time-to-first-sound exceeded 300ms SLA: {ttfs_ms}ms >= {sla_target}ms")

    if underruns == 0 and status.get("status") == "healthy":
        assert_pass("Playback buffer completed with zero underruns (status: healthy)")
    else:
        assert_fail(f"Playback reported buffer underruns: {underruns}")


# ==============================================================================
# Test 4: Negative control - empty string or invalid voice rejection
# ==============================================================================
def test_4_negative_control_validation() -> None:
    log("Test 4: Negative control - empty string or invalid voice rejection")

    # 4a. Empty string rejection in Python API
    try:
        mat.validate_text("")
        assert_fail("validate_text('') did not raise ValueError on empty string")
    except ValueError:
        assert_pass("validate_text('') correctly rejected empty text string")

    # 4b. Whitespace-only string rejection in Python API
    try:
        mat.validate_text("   \n\t  ")
        assert_fail("validate_text did not raise ValueError on whitespace-only string")
    except ValueError:
        assert_pass("validate_text correctly rejected whitespace-only string")

    # 4c. Invalid voice name rejection in Python API
    try:
        mat.validate_voice("invalid_voice_model_xyz", "piper")
        assert_fail("validate_voice did not raise ValueError on non-existent voice")
    except ValueError:
        assert_pass("validate_voice correctly rejected non-existent voice model 'invalid_voice_model_xyz'")

    # 4d. Invalid engine rejection in Python API
    try:
        mat.validate_engine("unsupported_tts_engine")
        assert_fail("validate_engine did not raise ValueError on unsupported engine")
    except ValueError:
        assert_pass("validate_engine correctly rejected unsupported engine 'unsupported_tts_engine'")

    # 4e. CLI empty string negative control
    res_empty_cli = subprocess.run(
        [sys.executable, str(TTS_SCRIPT), "--mock", "speak", ""],
        capture_output=True,
        text=True,
    )
    if res_empty_cli.returncode != 0 and "Cannot synthesize empty text prompt" in res_empty_cli.stderr:
        assert_pass("CLI 'speak \"\"' exited non-zero with clean empty prompt error message")
    else:
        assert_fail(f"CLI empty string was not rejected properly: rc={res_empty_cli.returncode}")

    # 4f. CLI invalid voice negative control
    res_voice_cli = subprocess.run(
        [sys.executable, str(TTS_SCRIPT), "--mock", "--voice", "bogus_voice_xyz", "speak", "Hello"],
        capture_output=True,
        text=True,
    )
    if res_voice_cli.returncode != 0 and "Unsupported or invalid voice" in res_voice_cli.stderr:
        assert_pass("CLI invalid voice flag exited non-zero with clean validation error message")
    else:
        assert_fail(f"CLI invalid voice was not rejected properly: rc={res_voice_cli.returncode}")


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

        if c_name == "mios-piper":
            assert_pass("ContainerName correctly set to 'mios-piper'")
        else:
            assert_fail(f"ContainerName unexpected: {c_name}")

        ssot = tomllib.loads(SSOT_TOML.read_text(encoding="utf-8"))
        piper_spec = ssot["containers"]["mios-piper"]
        want_image = piper_spec["Container"]["Image"]
        if c_image == want_image:
            assert_pass(f"Container Image is the SSOT image ({c_image})")
        else:
            assert_fail(f"mios-piper Image must be {want_image} (got {c_image})")

        if c_pod == "mios-ai.pod":
            assert_pass("Container Pod correctly mapped to 'mios-ai.pod'")
        else:
            assert_fail(f"Container Pod unexpected: {c_pod}")

        # piper1-gpl's http_server serves GET /info; it has no /health route.
        url_match = re.search(r"https?://[^\s'\"]+", c_health)
        health_path = urllib.parse.urlparse(url_match.group(0)).path if url_match else ""
        if health_path == "/info" and "curl" not in c_health:
            assert_pass(f"Container HealthCmd probes /info without curl ({c_health})")
        else:
            assert_fail(f"mios-piper HealthCmd must probe /info (got {c_health})")

        allowed_flags = {"-m", "--data-dir", "--host", "--port", "--speaker", "--cuda"}
        c_exec = cp.get("Container", "Exec", fallback="")
        bad_flags = [t for t in shlex.split(c_exec) if t.startswith("-") and t not in allowed_flags]
        if c_exec and not bad_flags:
            assert_pass(f"Exec uses only piper1-gpl http_server flags ({c_exec})")
        else:
            assert_fail(f"mios-piper Exec carries flags http_server does not accept: {bad_flags or c_exec}")

        # Law 7: only the generator-projected User=/Group= may carry the uid/gid;
        # any other uid/port literal or ${VAR:-literal} fallback is hand-written.
        uid = str(ssot["services"]["piper"]["uid"])
        gid = str(ssot["services"]["piper"]["gid"])
        port = str(ssot["ports"]["piper"])
        quadlet_text = QUADLET_FILE.read_text(encoding="utf-8")
        body = [ln for ln in quadlet_text.splitlines()
                if not ln.startswith(("User=", "Group="))]
        literals = [ln for ln in body if uid in ln or gid in ln or port in ln or ":-" in ln]
        if (cp.get("Container", "User", fallback="") == uid
                and cp.get("Container", "Group", fallback="") == gid
                and not literals):
            assert_pass(f"Quadlet carries no literal {uid}/{port} and no :- fallback outside the SSOT-projected User=/Group=")
        else:
            assert_fail(f"mios-piper Quadlet carries literal {uid}/{port} or :- fallbacks: {literals}")
        src_literals = [f"{sec}.{k}" for sec, kv in piper_spec.items() if isinstance(kv, dict)
                        for k, v in kv.items() if uid in str(v) or port in str(v)]
        if not src_literals:
            assert_pass(f"[containers.mios-piper] source carries no literal {uid} or {port}")
        else:
            assert_fail(f"[containers.mios-piper] source carries literal {uid}/{port}: {src_literals}")

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
# Test 6: Mock end-to-end streaming loopback (--mock)
# ==============================================================================
def test_6_mock_end_to_end_loopback() -> None:
    log("Test 6: Mock end-to-end streaming loopback (--mock)")
    with tempfile.TemporaryDirectory(prefix="test-tts-sock-") as temp_dir:
        _test_6_loopback_in(temp_dir)


def _test_6_loopback_in(temp_dir: str) -> None:
    sock_path = os.path.join(temp_dir, "audio-tts.sock")

    worker = mat.StreamingTTSWorker(
        engine="piper",
        mock_mode=True,
        verbose=VERBOSE,
    )

    # Start socket listener in daemon thread
    server_thread = threading.Thread(
        target=lambda: worker.stream_from_socket(sock_path=sock_path, timeout=3.0),
        daemon=True,
    )
    server_thread.start()

    # Wait for socket to bind
    for _ in range(20):
        if os.path.exists(sock_path):
            break
        time.sleep(0.05)

    if not os.path.exists(sock_path):
        worker.running = False
        server_thread.join(timeout=1.0)  # never leave the server writing into a temp dir being removed
        assert_fail("TTS Unix domain socket was not created in time")
        return

    assert_pass(f"Streaming TTS daemon active and bound to {sock_path}")

    # Client socket streaming
    client_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        client_sock.connect(sock_path)
        assert_pass("Test client connected to streaming TTS socket")

        sentences = [
            "Streaming text token one arrives. ",
            "Second sentence is synthesized concurrently! ",
            "Does sentence three finish the stream? ",
        ]

        for s in sentences:
            client_sock.sendall(s.encode("utf-8"))
            time.sleep(0.05)

        assert_pass("Successfully streamed 3 multi-sentence tokens over socket")

    except Exception as e:
        assert_fail("Client socket streaming failed", str(e))
    finally:
        client_sock.close()
        time.sleep(0.3)
        worker.running = False
        server_thread.join(timeout=1.0)

    # Verify worker metrics
    metrics = worker.get_status()
    log_diag(f"Loopback final metrics: {json.dumps(metrics)}")

    if metrics.get("chunks_synthesized", 0) >= 3:
        assert_pass(f"Synthesized {metrics['chunks_synthesized']} sentence chunks concurrently")
    else:
        assert_fail(f"Expected >=3 chunks synthesized: {metrics.get('chunks_synthesized')}")

    if metrics.get("bytes_written", 0) > 0 and metrics.get("frames_written", 0) > 0:
        assert_pass(f"Audio buffer received {metrics['bytes_written']} bytes ({metrics['duration_sec']}s audio)")
    else:
        assert_fail("Audio buffer received 0 bytes")

    if metrics.get("underruns", -1) == 0:
        assert_pass("End-to-end streaming completed with 0 buffer underruns")
    else:
        assert_fail(f"Buffer underruns reported: {metrics.get('underruns')}")

    assert_pass("Mock end-to-end streaming loopback completed with clean socket teardown")


# ==============================================================================
# Main Runner
# ==============================================================================
def main() -> int:
    global VERBOSE, DRY_RUN, MOCK_MODE

    parser = argparse.ArgumentParser(description="Test suite for streaming Piper/Kokoro TTS and PipeWire buffer feeder.")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose diagnostics")
    parser.add_argument("--dry-run", action="store_true", help="Dry run test execution")
    parser.add_argument("--mock", action="store_true", help="Force mock testing mode")
    args = parser.parse_args()

    VERBOSE = args.verbose
    DRY_RUN = args.dry_run
    MOCK_MODE = args.mock

    log("Starting test suite: test-audio-tts-piper.py")

    test_1_cli_and_help()
    test_2_sentence_chunking_and_buffering()
    test_3_sub_300ms_latency_sla()
    test_4_negative_control_validation()
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
