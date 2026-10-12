#!/usr/bin/env python3
# AI-hint: Verification suite for ATSPI accessibility tree sensitive widget coordinate detector and Wayland frame blur filter (task_483, task_2716).
# AI-doc: usr/share/doc/mios/manual/ch84-vision-redaction.md
"""Test suite for ATSPI accessibility tree sensitive widget coordinate detector and Wayland frame blur filter."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import struct
import subprocess
import sys
import tempfile
import zlib
from typing import Any, Dict, List, Tuple

# Locate project paths
SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
REDACT_SCRIPT = ROOT_DIR / "usr" / "lib" / "mios" / "agent-pipe" / "mios_vision_redact.py"

# Import target module directly for unit verification
sys.path.insert(0, str(REDACT_SCRIPT.parent))
import mios_vision_redact as mvr

VERBOSE = False
DRY_RUN = False
MOCK_MODE = True

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
# Helper Utilities
# ==============================================================================

def create_synthetic_png(
    path: pathlib.Path,
    width: int,
    height: int,
    pattern: str = "checkerboard",
) -> None:
    """Generates a synthetic PNG image with high-frequency contrast patterns."""
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter type 0
        for x in range(width):
            if pattern == "checkerboard":
                val = 255 if ((x // 4) + (y // 4)) % 2 == 0 else 0
            elif pattern == "stripes":
                val = 255 if (x % 4 < 2) else 0
            else:
                val = (x * 7 + y * 13) % 256
            raw.extend([val, val, val])

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    hdr = b"\x89PNG\r\n\x1a\n"
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    idat = chunk(b"IDAT", zlib.compress(bytes(raw)))
    iend = chunk(b"IEND", b"")

    with open(path, "wb") as f:
        f.write(hdr + ihdr + idat + iend)


def calculate_region_variance(frame: mvr.FrameBuffer, x: int, y: int, w: int, h: int) -> float:
    """Calculates channel-0 pixel variance across a defined rectangular region."""
    pixels: List[int] = []
    for py in range(y, min(y + h, frame.height)):
        row_off = py * frame.width * frame.channels
        for px in range(x, min(x + w, frame.width)):
            pixels.append(frame.data[row_off + px * frame.channels])
    if not pixels:
        return 0.0
    mean = sum(pixels) / len(pixels)
    return sum((p - mean) ** 2 for p in pixels) / len(pixels)


# ==============================================================================
# Test Cases
# ==============================================================================

def test_1_cli_and_help() -> None:
    """Test 1: CLI invocation and help verification."""
    log("Running Test 1: CLI and help verification...")
    try:
        # Top-level --help
        res = subprocess.run(
            [sys.executable, str(REDACT_SCRIPT), "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode == 0 and "ATSPI accessibility tree sensitive widget coordinate detector" in res.stdout:
            assert_pass("CLI --help displays usage and returns exit code 0")
        else:
            assert_fail("CLI --help failed", res.stderr)

        # Verify all required subcommands and flags are in help text
        required_elements = ["scan", "redact", "status", "--mock", "--dry-run", "-v"]
        missing = [elem for elem in required_elements if elem not in res.stdout]
        if not missing:
            assert_pass(f"All required subcommands and flags present in help: {required_elements}")
        else:
            assert_fail("Missing subcommands/flags in help text", str(missing))

        # Test subcommand --help for scan, redact, status
        for subcmd in ["scan", "redact", "status"]:
            sub_res = subprocess.run(
                [sys.executable, str(REDACT_SCRIPT), subcmd, "--help"],
                capture_output=True,
                text=True,
                check=False,
            )
            if sub_res.returncode == 0:
                assert_pass(f"Subcommand '{subcmd} --help' exited cleanly with code 0")
            else:
                assert_fail(f"Subcommand '{subcmd} --help' failed", sub_res.stderr)

    except Exception as e:
        assert_fail("Test 1 encountered unhandled exception", str(e))


def test_2_detection_password_widgets() -> None:
    """Test 2: Detection of password widgets via mock ATSPI tree (positive control)."""
    log("Running Test 2: Detection of password widgets via mock ATSPI tree (positive control)...")
    try:
        # 1. Direct Python module API query
        widgets = mvr.query_atspi(mock=True)
        if len(widgets) >= 3:
            assert_pass(f"Mock ATSPI query returned {len(widgets)} sensitive widgets")
        else:
            assert_fail("Mock ATSPI query returned fewer widgets than expected", f"count={len(widgets)}")

        # Verify specific role, state, attribute matches
        roles_found = {w.role for w in widgets}
        reasons_found = {w.reason for w in widgets}

        # Check for ROLE_PASSWORD_TEXT detection
        has_password_role = any("PASSWORD" in r.upper() for r in roles_found)
        if has_password_role:
            assert_pass("Positive control: Detected sensitive widget with ROLE_PASSWORD_TEXT")
        else:
            assert_fail("Failed to detect widget with ROLE_PASSWORD_TEXT", str(roles_found))

        # Check for STATE_PROTECTED detection
        has_protected_state = any("protected" in reason.lower() for reason in reasons_found)
        if has_protected_state:
            assert_pass("Positive control: Detected sensitive widget with STATE_PROTECTED")
        else:
            assert_fail("Failed to detect widget with STATE_PROTECTED", str(reasons_found))

        # Check for security attribute detection
        has_security_attr = any("attribute:" in reason for reason in reasons_found)
        if has_security_attr:
            assert_pass("Positive control: Detected sensitive widget with security/password attributes")
        else:
            assert_fail("Failed to detect widget with security attributes", str(reasons_found))

        # Verify screen bounding boxes are strictly valid positive pixel coordinates
        all_valid_coords = all(
            w.bounds.x >= 0 and w.bounds.y >= 0 and w.bounds.width > 0 and w.bounds.height > 0
            for w in widgets
        )
        if all_valid_coords:
            assert_pass("All detected sensitive widgets have valid pixel bounding box coordinates")
        else:
            assert_fail("One or more widgets have invalid bounding box coordinates")

        # 2. CLI invocation: scan --mock
        cli_res = subprocess.run(
            [sys.executable, str(REDACT_SCRIPT), "scan", "--mock"],
            capture_output=True,
            text=True,
            check=False,
        )
        if cli_res.returncode == 0:
            parsed = json.loads(cli_res.stdout)
            if parsed.get("status") == "ok" and parsed.get("count") >= 3:
                assert_pass("CLI 'scan --mock' returns JSON with sensitive widget coordinates")
            else:
                assert_fail("CLI 'scan --mock' returned invalid JSON payload", cli_res.stdout)
        else:
            assert_fail("CLI 'scan --mock' exited non-zero", cli_res.stderr)

    except Exception as e:
        assert_fail("Test 2 encountered unhandled exception", str(e))


def test_3_coordinate_blur_positive_control() -> None:
    """Test 3: Coordinate bounding box calculation and Gaussian blur application on synthetic frame (positive control)."""
    log("Running Test 3: Coordinate bounding box calculation and Gaussian blur application (positive control)...")
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            in_path = pathlib.Path(tmpdir) / "frame_sharp.png"
            out_path = pathlib.Path(tmpdir) / "frame_blurred.png"

            # Create synthetic 400x300 image with high-frequency checkerboard pattern
            create_synthetic_png(in_path, width=400, height=300, pattern="checkerboard")

            # Load frame into FrameBuffer
            frame_sharp = mvr.FrameBuffer.load(in_path)
            bx, by, bw, bh = 100, 80, 180, 40
            bbox = mvr.BoundingBox(x=bx, y=by, width=bw, height=bh)

            var_before = calculate_region_variance(frame_sharp, bx, by, bw, bh)
            log_diag(f"Pre-blur variance in bounding box: {var_before:.2f}")

            # Apply Gaussian blur redaction
            cfg = mvr.RedactionConfig(filter_type="gaussian", radius=15, padding=4)
            frame_blurred = frame_sharp.clone()
            frame_blurred.redact_box(bbox, cfg)
            frame_blurred.save(out_path)

            # Re-load saved frame to verify file serialization roundtrip
            frame_reloaded = mvr.FrameBuffer.load(out_path)
            var_after = calculate_region_variance(frame_reloaded, bx, by, bw, bh)
            log_diag(f"Post-blur variance in bounding box: {var_after:.2f}")

            # Positive Control: Variance must decrease significantly (by at least 80%)
            if var_after < (var_before * 0.20):
                assert_pass(
                    f"Positive control: Gaussian blur significantly reduced pixel variance ({var_before:.1f} -> {var_after:.1f})"
                )
            else:
                assert_fail(
                    "Positive control: Pixel variance not reduced as expected",
                    f"before={var_before:.1f}, after={var_after:.1f}",
                )

            # Verify that pixels outside the padded region are completely unmodified (diff == 0)
            pad = cfg.padding
            x1, y1, x2, y2 = bbox.pad(pad, frame_sharp.width, frame_sharp.height)
            outside_unchanged = True
            for py in range(0, 50):  # Top-left region well outside bounding box
                for px in range(0, 50):
                    idx = (py * frame_sharp.width + px) * frame_sharp.channels
                    for c in range(frame_sharp.channels):
                        if frame_sharp.data[idx + c] != frame_reloaded.data[idx + c]:
                            outside_unchanged = False
                            break

            if outside_unchanged:
                assert_pass("Positive control: Un-redacted background pixels remain 100% bit-identical")
            else:
                assert_fail("Pixels outside the bounding box were unexpectedly modified during blur")

        except Exception as e:
            assert_fail("Test 3 encountered unhandled exception", str(e))


def test_4_negative_control_non_sensitive_widgets() -> None:
    """Test 4: Negative control - non-sensitive widgets (standard text, buttons) are NOT blurred."""
    log("Running Test 4: Negative control - non-sensitive widgets are NOT blurred...")
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            # Construct mock widget set with 1 sensitive and 2 non-sensitive widgets
            mock_widgets = [
                {
                    "id": "mock.password",
                    "name": "UserPassword",
                    "role": "ROLE_PASSWORD_TEXT",
                    "states": ["STATE_ENABLED", "STATE_VISIBLE", "STATE_PROTECTED"],
                    "attributes": {"input-type": "password"},
                    "bounds": {"x": 50, "y": 50, "width": 120, "height": 30},
                },
                {
                    "id": "mock.username",
                    "name": "UserName",
                    "role": "ROLE_TEXT",
                    "states": ["STATE_ENABLED", "STATE_VISIBLE"],
                    "attributes": {"input-type": "text"},
                    "bounds": {"x": 50, "y": 120, "width": 120, "height": 30},
                },
                {
                    "id": "mock.button",
                    "name": "SubmitButton",
                    "role": "ROLE_PUSH_BUTTON",
                    "states": ["STATE_ENABLED", "STATE_VISIBLE"],
                    "attributes": {"action": "click"},
                    "bounds": {"x": 50, "y": 190, "width": 80, "height": 30},
                },
            ]

            # Verify classification logic directly
            detected_sensitive = mvr.query_atspi(mock=True, mock_data=mock_widgets)
            detected_ids = [w.widget_id for w in detected_sensitive]

            if detected_ids == ["mock.password"]:
                assert_pass("Negative control: Non-sensitive widgets (ROLE_TEXT, ROLE_PUSH_BUTTON) excluded by detector")
            else:
                assert_fail("Detector improperly classified non-sensitive widgets as sensitive", str(detected_ids))

            # Create synthetic test frame
            in_path = pathlib.Path(tmpdir) / "ui_frame.png"
            out_path = pathlib.Path(tmpdir) / "ui_frame_redacted.png"
            create_synthetic_png(in_path, width=300, height=300, pattern="stripes")

            orig_frame = mvr.FrameBuffer.load(in_path)

            # Redact targeting only detected widgets
            res = mvr.redact_frame(
                input_path=in_path,
                output_path=out_path,
                widgets=detected_sensitive,
                config=mvr.RedactionConfig(filter_type="gaussian", radius=10, padding=2),
                force_pure_python=True,
            )

            redacted_frame = mvr.FrameBuffer.load(out_path)

            # Check 1: Sensitive widget region (50, 50, 120, 30) MUST be altered
            pwd_diffs = sum(
                1
                for y in range(50, 80)
                for x in range(50, 170)
                if orig_frame.data[(y * 300 + x) * 3] != redacted_frame.data[(y * 300 + x) * 3]
            )
            if pwd_diffs > 0:
                assert_pass("Sensitive widget area was successfully redacted and modified")
            else:
                assert_fail("Sensitive widget area was unexpectedly unchanged")

            # Check 2: Username field region (50, 120, 120, 30) MUST be 100% untouched
            user_diffs = sum(
                1
                for y in range(120, 150)
                for x in range(50, 170)
                if orig_frame.data[(y * 300 + x) * 3] != redacted_frame.data[(y * 300 + x) * 3]
            )
            if user_diffs == 0:
                assert_pass("Negative control: Non-sensitive username field region remained 100% UNTOUCHED (0 diffs)")
            else:
                assert_fail("Non-sensitive username field region was incorrectly altered", f"diff_count={user_diffs}")

            # Check 3: Button region (50, 190, 80, 30) MUST be 100% untouched
            btn_diffs = sum(
                1
                for y in range(190, 220)
                for x in range(50, 130)
                if orig_frame.data[(y * 300 + x) * 3] != redacted_frame.data[(y * 300 + x) * 3]
            )
            if btn_diffs == 0:
                assert_pass("Negative control: Non-sensitive button region remained 100% UNTOUCHED (0 diffs)")
            else:
                assert_fail("Non-sensitive button region was incorrectly altered", f"diff_count={btn_diffs}")

        except Exception as e:
            assert_fail("Test 4 encountered unhandled exception", str(e))


def test_5_negative_control_invalid_frame_and_file_handling() -> None:
    """Test 5: Negative control - invalid frame format or non-existent file handling exits non-zero cleanly."""
    log("Running Test 5: Negative control - invalid frame and missing file handling...")
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            non_existent_file = pathlib.Path(tmpdir) / "does_not_exist_file_9999.png"
            dummy_out = pathlib.Path(tmpdir) / "out.png"

            # 1. Non-existent input file
            res_missing = subprocess.run(
                [
                    sys.executable,
                    str(REDACT_SCRIPT),
                    "redact",
                    "--input",
                    str(non_existent_file),
                    "--output",
                    str(dummy_out),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if res_missing.returncode != 0 and (
                "does not exist" in res_missing.stderr or "Redaction failed" in res_missing.stderr
            ):
                assert_pass("Negative control: Non-existent input file handled cleanly with non-zero exit")
            else:
                assert_fail("Non-existent input file did not fail cleanly", f"code={res_missing.returncode}")

            # 2. Corrupted / invalid image format file
            corrupt_file = pathlib.Path(tmpdir) / "corrupt_data.png"
            with open(corrupt_file, "wb") as f:
                f.write(b"NOT_A_VALID_IMAGE_HEADER_GARBAGE_BYTES_1234567890")

            res_corrupt = subprocess.run(
                [
                    sys.executable,
                    str(REDACT_SCRIPT),
                    "redact",
                    "--input",
                    str(corrupt_file),
                    "--output",
                    str(dummy_out),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if res_corrupt.returncode != 0 and (
                "Unsupported or corrupted" in res_corrupt.stderr or "Redaction failed" in res_corrupt.stderr
            ):
                assert_pass("Negative control: Corrupted image format handled cleanly with non-zero exit")
            else:
                assert_fail("Corrupted image format did not fail cleanly", f"code={res_corrupt.returncode}")

            # 3. Missing required CLI arguments (missing --output)
            res_args = subprocess.run(
                [sys.executable, str(REDACT_SCRIPT), "redact", "--input", str(corrupt_file)],
                capture_output=True,
                text=True,
                check=False,
            )
            if res_args.returncode != 0 and "required" in res_args.stderr:
                assert_pass("Negative control: Missing required CLI arguments exits non-zero with syntax help")
            else:
                assert_fail("Missing CLI arguments did not fail cleanly", f"code={res_args.returncode}")

        except Exception as e:
            assert_fail("Test 5 encountered unhandled exception", str(e))


def test_6_mock_end_to_end_lifecycle() -> None:
    """Test 6: Mock end-to-end redaction lifecycle (--mock)."""
    log("Running Test 6: Mock end-to-end redaction lifecycle (--mock)...")
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            # 1. Test status --mock
            status_res = subprocess.run(
                [sys.executable, str(REDACT_SCRIPT), "status", "--mock", "--json"],
                capture_output=True,
                text=True,
                check=False,
            )
            if status_res.returncode == 0:
                status_json = json.loads(status_res.stdout)
                if status_json.get("atspi_connected") is True and status_json.get("backend") == "mock":
                    assert_pass("End-to-end lifecycle: 'status --mock' verifies mock ATSPI bus and capabilities")
                else:
                    assert_fail("Status JSON output missing expected mock attributes", status_res.stdout)
            else:
                assert_fail("Status command failed", status_res.stderr)

            # 2. Test scan --mock
            scan_res = subprocess.run(
                [sys.executable, str(REDACT_SCRIPT), "scan", "--mock"],
                capture_output=True,
                text=True,
                check=False,
            )
            if scan_res.returncode == 0:
                scan_json = json.loads(scan_res.stdout)
                widget_count = scan_json.get("count", 0)
                if widget_count >= 3:
                    assert_pass(f"End-to-end lifecycle: 'scan --mock' discovered {widget_count} sensitive fields")
                else:
                    assert_fail(f"Scan discovered fewer fields than expected ({widget_count})")
            else:
                assert_fail("Scan command failed", scan_res.stderr)

            # 3. Create full Wayland captured frame
            frame_in = pathlib.Path(tmpdir) / "wayland_screen.png"
            frame_out_gaussian = pathlib.Path(tmpdir) / "wayland_screen_redacted_gaussian.png"
            frame_out_pixelate = pathlib.Path(tmpdir) / "wayland_screen_redacted_pixelate.png"
            create_synthetic_png(frame_in, width=800, height=600, pattern="checkerboard")

            # 4. Execute redact with Gaussian blur filter
            redact_res = subprocess.run(
                [
                    sys.executable,
                    str(REDACT_SCRIPT),
                    "redact",
                    "--mock",
                    "--input",
                    str(frame_in),
                    "--output",
                    str(frame_out_gaussian),
                    "--filter",
                    "gaussian",
                    "--radius",
                    "15",
                    "--padding",
                    "4",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if redact_res.returncode == 0 and frame_out_gaussian.exists():
                assert_pass("End-to-end lifecycle: Gaussian blur redaction executed cleanly, output generated")
            else:
                assert_fail("Gaussian blur redaction failed", redact_res.stderr)

            # 5. Execute redact with Pixelation filter
            pixel_res = subprocess.run(
                [
                    sys.executable,
                    str(REDACT_SCRIPT),
                    "redact",
                    "--mock",
                    "--input",
                    str(frame_in),
                    "--output",
                    str(frame_out_pixelate),
                    "--filter",
                    "pixelate",
                    "--pixelate-block",
                    "12",
                    "--padding",
                    "4",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if pixel_res.returncode == 0 and frame_out_pixelate.exists():
                assert_pass("End-to-end lifecycle: Pixelate filter executed cleanly, output generated")
            else:
                assert_fail("Pixelate filter redaction failed", pixel_res.stderr)

            # 6. Verify image formats and dimensions preserved
            fb_in = mvr.FrameBuffer.load(frame_in)
            fb_out_g = mvr.FrameBuffer.load(frame_out_gaussian)
            fb_out_p = mvr.FrameBuffer.load(frame_out_pixelate)

            if (fb_in.width, fb_in.height) == (fb_out_g.width, fb_out_g.height) == (fb_out_p.width, fb_out_p.height):
                assert_pass(f"Output frames maintain identical resolution ({fb_in.width}x{fb_in.height})")
            else:
                assert_fail("Output frame resolution mismatched with input")

        except Exception as e:
            assert_fail("Test 6 encountered unhandled exception", str(e))


# ==============================================================================
# Runner
# ==============================================================================

def main() -> int:
    global VERBOSE, DRY_RUN, MOCK_MODE
    parser = argparse.ArgumentParser(description="Test suite for mios_vision_redact")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose diagnostics")
    parser.add_argument("--dry-run", action="store_true", help="Dry run verification")
    parser.add_argument("--mock", action="store_true", default=True, help="Run in mock mode (default: True)")
    args = parser.parse_args()

    VERBOSE = args.verbose
    DRY_RUN = args.dry_run
    MOCK_MODE = args.mock

    print("=" * 70)
    print("MiOS Vision Redaction Test Suite (task_483, task_2716)")
    print("=" * 70)

    test_1_cli_and_help()
    test_2_detection_password_widgets()
    test_3_coordinate_blur_positive_control()
    test_4_negative_control_non_sensitive_widgets()
    test_5_negative_control_invalid_frame_and_file_handling()
    test_6_mock_end_to_end_lifecycle()

    print("=" * 70)
    print(f"Results: {pass_count} PASSED, {fail_count} FAILED")
    print("=" * 70)

    if fail_count > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
