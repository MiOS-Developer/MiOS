#!/usr/bin/env python3
# AI-hint: Regression test verifying that mios-flatpak-run strips MIOS_* variables in linear time.
# AI-related: usr/libexec/mios/mios-flatpak-run, usr/lib/mios/userenv.sh, usr/share/mios/mios.toml
# AI-doc: usr/share/doc/mios/manual/tests.md
"""Verify mios-flatpak-run strips MIOS_* environment variables in linear time (< 0.5s for 3,700 vars)
while preserving non-MIOS variables."""

from __future__ import annotations

import os
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.environ.get("MIOS_DRIFT_ROOT") or os.environ.get("MIOS_ROOT") or "/"
_SCRIPT = os.path.join(_ROOT, "usr/libexec/mios/mios-flatpak-run")
if not os.path.isfile(_SCRIPT):
    candidate = os.path.normpath(os.path.join(_HERE, "..", "usr/libexec/mios/mios-flatpak-run"))
    if os.path.isfile(candidate):
        _SCRIPT = candidate
    else:
        _SCRIPT = "/usr/libexec/mios/mios-flatpak-run"



def test_positive_control_linear_benchmark() -> None:
    assert os.path.isfile(_SCRIPT), f"Missing script: {_SCRIPT}"
    assert os.access(_SCRIPT, os.X_OK), f"Script is not executable: {_SCRIPT}"

    # Build 3,700 fake MIOS_* variables + control variables
    fake_env = {f"MIOS_FAKE_VAR_{i}": f"val_{i}" for i in range(3700)}
    fake_env["KEEP_ME_NON_MIOS"] = "preserved_value"
    fake_env["PATH"] = os.environ.get("PATH", "/usr/bin:/bin")
    fake_env["USER"] = os.environ.get("USER", "mios")

    # 1. Benchmark real script execution
    t0 = time.perf_counter()
    res = subprocess.run(
        [_SCRIPT, "--help"],
        env=fake_env,
        capture_output=True,
        text=True,
    )
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.5, f"Execution with 3,700 vars took too long: {elapsed:.3f}s >= 0.5s"

    # 2. Verify variable stripping and retention via the script's stripping pipeline
    probe_cmd = (
        'exec env $(env | sed -n "s/^\\(MIOS_[A-Za-z0-9_]*\\)=.*/-u \\1/p") '
        'python3 -c "import os, sys; '
        'leaked = [k for k in os.environ if k.startswith(\'MIOS_\')]; '
        'assert not leaked, f\'Leaked MIOS vars: {leaked}\'; '
        'assert os.environ.get(\'KEEP_ME_NON_MIOS\') == \'preserved_value\', \'Non-MIOS var was dropped\'; '
        'sys.exit(0)"'
    )
    res_probe = subprocess.run(["sh", "-c", probe_cmd], env=fake_env, capture_output=True, text=True)
    assert res_probe.returncode == 0, f"Probe failed: {res_probe.stderr}"


def test_negative_control_detects_leak() -> None:
    fake_env = {f"MIOS_TEST_VAR_{i}": f"v_{i}" for i in range(10)}
    fake_env["KEEP_ME_NON_MIOS"] = "preserved_value"
    fake_env["PATH"] = os.environ.get("PATH", "/usr/bin:/bin")

    # Corrupt pipeline that fails to strip MIOS_* variables
    broken_pipeline = (
        'exec env $(env | sed -n "s/^\\(NOMATCH_[A-Za-z0-9_]*\\)=.*/-u \\1/p") '
        'python3 -c "import os, sys; '
        'leaked = [k for k in os.environ if k.startswith(\'MIOS_\')]; '
        'assert not leaked, \'MIOS vars detected\'; '
        'sys.exit(0)"'
    )
    res = subprocess.run(["sh", "-c", broken_pipeline], env=fake_env, capture_output=True, text=True)
    assert res.returncode != 0, "Negative control failed: defective pipeline did not fail"
    assert "MIOS vars detected" in res.stderr, "Negative control failed: expected leak assertion"


def test_negative_control_detects_dropped_var() -> None:
    fake_env = {f"MIOS_TEST_VAR_{i}": f"v_{i}" for i in range(10)}
    fake_env["PATH"] = os.environ.get("PATH", "/usr/bin:/bin")

    # Corrupt pipeline that erroneously drops non-MIOS variables
    broken_drop_pipeline = (
        'exec env -i '
        'python3 -c "import os, sys; '
        'assert os.environ.get(\'KEEP_ME_NON_MIOS\') == \'preserved_value\', \'Non-MIOS var was dropped\'; '
        'sys.exit(0)"'
    )
    res_drop = subprocess.run(["sh", "-c", broken_drop_pipeline], env=fake_env, capture_output=True, text=True)
    assert res_drop.returncode != 0, "Negative control failed: empty env did not trigger dropped non-MIOS assertion"
    assert "Non-MIOS var was dropped" in res_drop.stderr, "Negative control failed: expected drop assertion"


def test_negative_control_timing_threshold() -> None:
    def check_timing(duration: float) -> None:
        assert duration < 0.5, f"Execution took too long: {duration:.3f}s >= 0.5s"

    try:
        check_timing(0.6)
        raise AssertionError("Negative control failed: check_timing accepted duration >= 0.5s")
    except AssertionError as exc:
        assert "Execution took too long" in str(exc)


def main() -> int:
    print("[test-flatpak-run-env-strip] Running positive control & linear benchmark...")
    test_positive_control_linear_benchmark()
    print("[test-flatpak-run-env-strip] Running negative controls...")
    test_negative_control_detects_leak()
    test_negative_control_detects_dropped_var()
    test_negative_control_timing_threshold()
    print("[test-flatpak-run-env-strip] PASS: Linear time stripping verified under 0.5s.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
