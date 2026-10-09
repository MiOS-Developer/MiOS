#!/usr/bin/env python3
# AI-hint: Integration test suite for on-device OCR regex credential masking pipeline (task_484, task_2717).
# AI-doc: usr/share/doc/mios/manual/ch85-ocr-credential-masking.md
"""Integration test suite for mios_ocr_mask.py.

Verifies CLI commands, regex pattern matching (positive controls), coordinate
masking on synthetic frames, negative controls (benign text & invalid inputs),
and end-to-end mock lifecycle.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from typing import List, Tuple

# Locate mios_ocr_mask script
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OCR_MASK_BIN = os.path.join(REPO_ROOT, "usr/lib/mios/agent-pipe/mios_ocr_mask.py")

# Ensure agent-pipe is in sys.path for direct module testing
sys.path.insert(0, os.path.join(REPO_ROOT, "usr/lib/mios/agent-pipe"))
import mios_ocr_mask  # noqa: E402


def run_cmd(args: List[str]) -> subprocess.CompletedProcess:
    """Execute mios_ocr_mask.py CLI subprocess."""
    cmd = [sys.executable, OCR_MASK_BIN] + args
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


# ============================================================================
# Test 1: CLI and help verification
# ============================================================================

def test_1_cli_and_help() -> None:
    print("[test 1] CLI and help verification...")

    # Test 1a: --help
    res = run_cmd(["--help"])
    assert res.returncode == 0, f"Expected returncode 0 for --help, got {res.returncode}"
    assert "scan" in res.stdout
    assert "mask" in res.stdout
    assert "status" in res.stdout

    # Test 1b: status subcommand
    res = run_cmd(["status", "--mock"])
    assert res.returncode == 0, f"Expected returncode 0 for status, got {res.returncode}. Stderr: {res.stderr}"
    data = json.loads(res.stdout)
    assert data.get("status") == "ok", f"Unexpected status: {data}"
    assert data.get("ocr_backend") == "mock"
    assert data.get("rules_count", 0) >= 7
    rule_ids = {r["id"] for r in data.get("rules", [])}
    assert "api_key_openai" in rule_ids
    assert "api_key_github" in rule_ids
    assert "api_key_aws" in rule_ids
    assert "auth_bearer" in rule_ids
    assert "private_key" in rule_ids
    assert "credit_card" in rule_ids
    assert "password_assignment" in rule_ids

    # Test 1c: Invalid subcommand
    res = run_cmd(["invalid_command_xyz"])
    assert res.returncode != 0, "Expected non-zero exit for invalid command"

    print("  -> ok: CLI help and status verified")


# ============================================================================
# Test 2: Regex pattern matching on credential fixtures (Positive Controls)
# ============================================================================

def test_2_regex_pattern_matching() -> None:
    print("[test 2] Regex pattern matching on credential fixtures (positive control)...")

    masker = mios_ocr_mask.CredentialMasker()
    dummy_bbox = (10, 20, 100, 30)

    # 2a: OpenAI API keys
    openai_fixtures = [
        "sk-abcdef123456789012345678",
        "sk-proj-abc12345678901234567890abcdef",
        "export OPENAI_API_KEY=sk-proj-9876543210zyxwvutsrqponmlkjihgfedcba",
    ]
    for text in openai_fixtures:
        detected = masker.audit_text(text, dummy_bbox)
        assert any(d.rule_id == "api_key_openai" for d in detected), f"Failed to detect OpenAI key in '{text}'"
        match = next(d for d in detected if d.rule_id == "api_key_openai")
        assert match.redacted_preview.startswith("sk-")
        assert "*" in match.redacted_preview

    # 2b: GitHub Tokens
    github_fixtures = [
        "ghp_1234567890abcdefghijklmnopqrstuvwxyz",
        "gho_0987654321zyxwvutsrqponmlkjihgfedcba",
        "ghs_abcdef1234567890abcdef1234567890abcd",
        "Authorization: token ghp_1234567890abcdefghijklmnopqrstuvwxyz",
    ]
    for text in github_fixtures:
        detected = masker.audit_text(text, dummy_bbox)
        assert any(d.rule_id == "api_key_github" for d in detected), f"Failed to detect GitHub token in '{text}'"
        match = next(d for d in detected if d.rule_id == "api_key_github")
        assert match.redacted_preview.startswith("gh")
        assert "*" in match.redacted_preview

    # 2c: AWS Access Key IDs
    aws_fixtures = [
        "AKIAIOSFODNN7EXAMPLE",
        "ASIAIOSFODNN7EXAMPLE",
        "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE",
    ]
    for text in aws_fixtures:
        detected = masker.audit_text(text, dummy_bbox)
        assert any(d.rule_id == "api_key_aws" for d in detected), f"Failed to detect AWS key in '{text}'"
        match = next(d for d in detected if d.rule_id == "api_key_aws")
        assert match.redacted_preview.startswith("AKIA") or match.redacted_preview.startswith("ASIA")
        assert match.redacted_preview == "AKIA****************" or match.redacted_preview == "ASIA****************"

    # 2d: Bearer Tokens
    bearer_fixtures = [
        "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.dummy_token_payload",
        "authorization: bearer 1234567890abcdefghijklmnopqrstuvwxyz",
    ]
    for text in bearer_fixtures:
        detected = masker.audit_text(text, dummy_bbox)
        assert any(d.rule_id == "auth_bearer" for d in detected), f"Failed to detect Bearer token in '{text}'"
        match = next(d for d in detected if d.rule_id == "auth_bearer")
        assert "Bearer" in match.redacted_preview or "bearer" in match.redacted_preview.lower()
        assert "*" in match.redacted_preview

    # 2e: Private Key Headers
    privkey_fixtures = [
        "-----BEGIN PRIVATE KEY-----",
        "-----BEGIN RSA PRIVATE KEY-----",
        "-----BEGIN OPENSSH PRIVATE KEY-----",
        "-----BEGIN EC PRIVATE KEY-----",
    ]
    for text in privkey_fixtures:
        detected = masker.audit_text(text, dummy_bbox)
        assert any(d.rule_id == "private_key" for d in detected), f"Failed to detect Private Key in '{text}'"

    # 2f: Credit Card Numbers (Luhn Valid)
    valid_cc_fixtures = [
        "4111 1111 1111 1111",      # Standard Visa test card (Luhn valid)
        "4111-1111-1111-1111",      # Hyphen separated
        "4111111111111111",           # Raw 16 digits
        "Payment card: 4111 1111 1111 1111 Expiry: 12/28",
    ]
    for text in valid_cc_fixtures:
        detected = masker.audit_text(text, dummy_bbox)
        assert any(d.rule_id == "credit_card" for d in detected), f"Failed to detect valid credit card in '{text}'"
        match = next(d for d in detected if d.rule_id == "credit_card")
        assert match.redacted_preview.endswith("1111")
        assert "****" in match.redacted_preview

    # 2g: Passwords and Secret Key Assignments
    secret_fixtures = [
        'password = "SuperSecretAdminPassword123!"',
        'passwd: "MySecurePassword2026"',
        'secret_key = "sk_live_verysecretstring"',
        'client_secret: "abcdef1234567890secret"',
    ]
    for text in secret_fixtures:
        detected = masker.audit_text(text, dummy_bbox)
        assert any(d.category == "secrets" for d in detected), f"Failed to detect secret assignment in '{text}'"

    print("  -> ok: positive control credential patterns matched successfully")


# ============================================================================
# Test 3: Bounding box coordinate masking on synthetic vision frame
# ============================================================================

def test_3_bounding_box_masking() -> None:
    print("[test 3] Bounding box coordinate masking on synthetic vision frame (positive control)...")

    with tempfile.TemporaryDirectory() as tmpdir:
        input_path = os.path.join(tmpdir, "test_white_frame.png")
        output_path = os.path.join(tmpdir, "test_masked_frame.png")

        # Create a 200x200 solid white frame (RGB 255, 255, 255)
        width, height = 200, 200
        white_pixels = bytearray([255, 255, 255] * (width * height))
        frame = mios_ocr_mask.VisionFrame(width, height, channels=3, data=white_pixels)
        frame.save(input_path)

        # Apply mask directly at box [40, 50, 60, 30] (x=40..100, y=50..80)
        mask_box = (40, 50, 60, 30)
        frame.mask_rect(mask_box[0], mask_box[1], mask_box[2], mask_box[3], color=(0, 0, 0), margin=2)
        frame.save(output_path)

        # Reload masked image and inspect pixel values
        loaded = mios_ocr_mask.VisionFrame.load(output_path)
        assert loaded.width == width and loaded.height == height

        # Pixels inside the masked area must be solid black (0, 0, 0)
        inside_coords = [(45, 55), (70, 65), (95, 75), (40, 50)]
        for cx, cy in inside_coords:
            px = loaded.get_pixel(cx, cy)
            assert px == (0, 0, 0), f"Pixel at ({cx}, {cy}) expected (0,0,0), got {px}"

        # Pixels outside the masked area must remain pure white (255, 255, 255)
        outside_coords = [(10, 10), (20, 20), (150, 150), (180, 180), (35, 50)]  # (35, 50) is outside margin 2
        for cx, cy in outside_coords:
            px = loaded.get_pixel(cx, cy)
            assert px == (255, 255, 255), f"Pixel at ({cx}, {cy}) expected (255,255,255), got {px}"

    print("  -> ok: bounding box masking correctly replaced pixel values with solid mask")


# ============================================================================
# Test 4: Negative control - public / benign text is NOT redacted
# ============================================================================

def test_4_negative_control_benign_text() -> None:
    print("[test 4] Negative control - public / benign text is NOT redacted...")

    masker = mios_ocr_mask.CredentialMasker()
    dummy_bbox = (10, 20, 100, 30)

    benign_fixtures = [
        "Welcome to MiOS Linux Workstation (bootc / ostree)",
        "System status: ALL 14 CLUSTER SERVICES HEALTHY",
        "Listening on 127.0.0.1:8642 (HTTP / REST API)",
        "User: alice.engineer@mios.local (UID: 1000, GID: 1000)",
        "Phone: +1 (555) 019-2834, Fax: +1 (555) 019-2835",
        "Commit hash: 9d8b7a6c5e4f32101234567890abcdef12345678",
        "Dimensions: 1920x1080, DPI: 96, Refresh: 60Hz",
        "1234 5678 9012 3456",  # 16-digit sequence with INVALID Luhn checksum
        "9876 5432 1098 7654",  # Invalid Luhn number
    ]

    for text in benign_fixtures:
        detected = masker.audit_text(text, dummy_bbox)
        assert len(detected) == 0, f"False positive! Benign text '{text}' flagged as: {detected}"

    print("  -> ok: negative control passed, zero false positives on benign text")


# ============================================================================
# Test 5: Negative control - empty or malformed image input handling
# ============================================================================

def test_5_negative_control_malformed_inputs() -> None:
    print("[test 5] Negative control - empty or malformed image input handling...")

    with tempfile.TemporaryDirectory() as tmpdir:
        # 5a: Zero-byte empty file
        empty_path = os.path.join(tmpdir, "empty.png")
        with open(empty_path, "wb") as f:
            pass  # 0 bytes

        res_scan_empty = run_cmd(["scan", "--input", empty_path, "--mock"])
        assert res_scan_empty.returncode != 0, f"Expected non-zero exit for empty file in scan, got {res_scan_empty.returncode}"
        assert "empty or malformed" in res_scan_empty.stderr.lower() or "empty" in res_scan_empty.stderr.lower()

        res_mask_empty = run_cmd(["mask", "--input", empty_path, "--output", os.path.join(tmpdir, "out.png"), "--mock"])
        assert res_mask_empty.returncode != 0, f"Expected non-zero exit for empty file in mask, got {res_mask_empty.returncode}"

        # 5b: Corrupted header / random binary noise
        corrupt_path = os.path.join(tmpdir, "corrupt.png")
        with open(corrupt_path, "wb") as f:
            f.write(b"NOT_A_VALID_IMAGE_HEADER_1234567890_GARBAGE_BYTES")

        res_scan_corrupt = run_cmd(["scan", "--input", corrupt_path, "--mock"])
        assert res_scan_corrupt.returncode != 0, f"Expected non-zero exit for corrupt file, got {res_scan_corrupt.returncode}"
        assert "empty or malformed" in res_scan_corrupt.stderr.lower() or "malformed" in res_scan_corrupt.stderr.lower()

        # 5c: Non-existent file
        res_nonexistent = run_cmd(["scan", "--input", os.path.join(tmpdir, "does_not_exist.png"), "--mock"])
        assert res_nonexistent.returncode != 0, "Expected non-zero exit for missing file"

    print("  -> ok: malformed and empty inputs rejected cleanly with non-zero exit codes")


# ============================================================================
# Test 6: Mock end-to-end OCR masking lifecycle (--mock)
# ============================================================================

def test_6_mock_end_to_end_lifecycle() -> None:
    print("[test 6] Mock end-to-end OCR masking lifecycle (--mock)...")

    with tempfile.TemporaryDirectory() as tmpdir:
        img_path = os.path.join(tmpdir, "synthetic_frame.png")
        sidecar_path = os.path.join(tmpdir, "synthetic_frame.ocr.json")
        masked_path = os.path.join(tmpdir, "synthetic_frame_masked.png")
        dry_run_path = os.path.join(tmpdir, "synthetic_frame_dryrun.png")

        # 1. Create a 300x200 white frame
        w, h = 300, 200
        frame = mios_ocr_mask.VisionFrame(w, h, channels=3, data=bytearray([255, 255, 255] * (w * h)))
        frame.save(img_path)

        # 2. Write a mock OCR sidecar JSON
        mock_annotations = [
            {
                "text": "AWS IAM Key: AKIAIOSFODNN7EXAMPLE",
                "bbox": [50, 40, 200, 25],
                "confidence": 0.99,
            },
            {
                "text": "System Hostname: mios-metal-blade-01",
                "bbox": [50, 80, 220, 25],
                "confidence": 0.98,
            },
            {
                "text": "User Token: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.secretpayload",
                "bbox": [50, 120, 240, 25],
                "confidence": 0.97,
            },
        ]
        with open(sidecar_path, "w", encoding="utf-8") as f:
            json.dump(mock_annotations, f, indent=2)

        # 3. Test `scan --input ... --mock`
        res_scan = run_cmd(["scan", "--input", img_path, "--mock"])
        assert res_scan.returncode == 0, f"scan failed: {res_scan.stderr}"
        scan_data = json.loads(res_scan.stdout)
        assert scan_data.get("status") == "success"
        assert scan_data.get("detected_count") == 2, f"Expected 2 detected credentials, got {scan_data.get('detected_count')}"
        detected_rules = {c["rule_id"] for c in scan_data.get("credentials", [])}
        assert "api_key_aws" in detected_rules
        assert "auth_bearer" in detected_rules

        # 4. Test `mask --input ... --output ... --mock --dry-run`
        res_dry = run_cmd(["mask", "--input", img_path, "--output", dry_run_path, "--mock", "--dry-run"])
        assert res_dry.returncode == 0, f"dry-run mask failed: {res_dry.stderr}"
        dry_data = json.loads(res_dry.stdout)
        assert dry_data.get("dry_run") is True
        assert not os.path.exists(dry_run_path), "Dry run should NOT create output file"

        # 5. Test `mask --input ... --output ... --mock`
        res_mask = run_cmd(["mask", "--input", img_path, "--output", masked_path, "--mock"])
        assert res_mask.returncode == 0, f"mask failed: {res_mask.stderr}"
        mask_data = json.loads(res_mask.stdout)
        assert mask_data.get("status") == "success"
        assert mask_data.get("redacted_count") == 2
        assert os.path.exists(masked_path), "Masked output file must exist"

        # 6. Verify masked pixel content
        masked_frame = mios_ocr_mask.VisionFrame.load(masked_path)
        # Check AWS Key box [50, 40, 200, 25] -> inside pixel (60, 50) must be (0, 0, 0)
        assert masked_frame.get_pixel(60, 50) == (0, 0, 0), "Credential region was not blacked out!"
        # Check Bearer token box [50, 120, 240, 25] -> inside pixel (60, 130) must be (0, 0, 0)
        assert masked_frame.get_pixel(60, 130) == (0, 0, 0), "Bearer token region was not blacked out!"
        # Check Benign box [50, 80, 220, 25] -> pixel (60, 90) must remain white (255, 255, 255)
        assert masked_frame.get_pixel(60, 90) == (255, 255, 255), "Benign region was incorrectly blacked out!"

    print("  -> ok: mock end-to-end OCR masking lifecycle succeeded")


# ============================================================================
# Main Runner
# ============================================================================

def main() -> int:
    print("======================================================================")
    print("Running MiOS OCR Credential Masking Integration Test Suite (task_484)")
    print("======================================================================")

    test_1_cli_and_help()
    test_2_regex_pattern_matching()
    test_3_bounding_box_masking()
    test_4_negative_control_benign_text()
    test_5_negative_control_malformed_inputs()
    test_6_mock_end_to_end_lifecycle()

    print("======================================================================")
    print("ALL TESTS PASSED (6/6)")
    print("======================================================================")
    return 0


if __name__ == "__main__":
    sys.exit(main())
