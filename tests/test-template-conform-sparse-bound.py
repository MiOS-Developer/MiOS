#!/usr/bin/env python3
# AI-hint: Regression test verifying check-template-conformance bounds memory traversal over large sparse files.
# AI-related: usr/libexec/mios/check-template-conformance, usr/share/mios/templates/, usr/share/mios/mios.toml
# AI-doc: usr/share/doc/mios/manual/tests.md
"""Verify check-template-conformance traverses roots with large sparse files safely and bounds memory."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.environ.get("MIOS_DRIFT_ROOT") or os.environ.get("MIOS_ROOT") or "/"
_CHECK_SCRIPT = os.path.join(_ROOT, "usr/libexec/mios/check-template-conformance")
if not os.path.isfile(_CHECK_SCRIPT):
    candidate = os.path.normpath(os.path.join(_HERE, "..", "usr/libexec/mios/check-template-conformance"))
    if os.path.isfile(candidate):
        _CHECK_SCRIPT = candidate
    else:
        _CHECK_SCRIPT = "/usr/libexec/mios/check-template-conformance"



def test_positive_control() -> None:
    assert os.path.isfile(_CHECK_SCRIPT), f"Missing conformance checker: {_CHECK_SCRIPT}"
    with tempfile.TemporaryDirectory() as tmpdir:
        # 1. Create a 4 GiB sparse file via truncate
        sparse_dir = os.path.join(tmpdir, "var/log")
        os.makedirs(sparse_dir, exist_ok=True)
        sparse_file = os.path.join(sparse_dir, "sparse_4gib.bin")
        with open(sparse_file, "wb") as f:
            f.truncate(4 * 1024 * 1024 * 1024)

        # 2. Create a conforming template-governed file
        auto_dir = os.path.join(tmpdir, "automation")
        os.makedirs(auto_dir, exist_ok=True)
        conforming_file = os.path.join(auto_dir, "test-script.sh")
        with open(conforming_file, "w", encoding="utf-8") as f:
            f.write("#!/bin/bash\n# AI-hint: Conforming automation script\necho ok\n")

        # 3. Execute check-template-conformance and verify timing (< 2s) and returncode 0
        t0 = time.perf_counter()
        proc = subprocess.run(
            [sys.executable, _CHECK_SCRIPT, "--root", tmpdir, "--max-unconforming", "0"],
            capture_output=True,
            text=True,
        )
        elapsed = time.perf_counter() - t0

        assert elapsed < 2.0, f"check-template-conformance took too long on sparse file: {elapsed:.3f}s >= 2.0s"
        assert proc.returncode == 0, f"Expected returncode 0 on conforming root, got {proc.returncode}:\nStdout: {proc.stdout}\nStderr: {proc.stderr}"


def test_negative_control() -> None:
    assert os.path.isfile(_CHECK_SCRIPT), f"Missing conformance checker: {_CHECK_SCRIPT}"
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a non-conforming template-governed file (missing required AI-hint header)
        auto_dir = os.path.join(tmpdir, "automation")
        os.makedirs(auto_dir, exist_ok=True)
        bad_file = os.path.join(auto_dir, "unconforming.sh")
        with open(bad_file, "w", encoding="utf-8") as f:
            f.write("#!/bin/bash\necho missing header\n")

        proc = subprocess.run(
            [sys.executable, _CHECK_SCRIPT, "--root", tmpdir, "--max-unconforming", "0"],
            capture_output=True,
            text=True,
        )
        assert proc.returncode != 0, f"Negative control failed: expected non-zero exit on unconforming file, got {proc.returncode}"


def main() -> int:
    print("[test-template-conform-sparse-bound] Running positive control with 4 GiB sparse file (< 2.0s)...")
    test_positive_control()
    print("[test-template-conform-sparse-bound] Running negative control with unconforming fixture...")
    test_negative_control()
    print("[test-template-conform-sparse-bound] PASS: check-template-conformance bounded over sparse files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
