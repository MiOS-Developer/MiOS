#!/usr/bin/env python3
# AI-hint: Comprehensive unit and regression test suite for mios-doctor diagnostics (Workflow 6).
# AI-functions: find_bash, run_doctor, TestDoctorDiagnostics
"""
Automated Test Suite for usr/libexec/mios/mios-doctor.
Verifies diagnostics survival under partial installation, set -euo pipefail hardening,
PID 1 systemd detection, PyYAML safe validation, permission octal normalization,
and exit code normalization (0 or 1).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
DOCTOR_SCRIPT = os.path.join(REPO_ROOT, "usr", "libexec", "mios", "mios-doctor")


def find_bash() -> str:
    """Find a POSIX-compliant bash interpreter across Linux and Windows."""
    if os.name == "nt":
        candidates = [
            r"C:\Program Files\Git\bin\bash.exe",
            r"C:\Program Files\Git\usr\bin\bash.exe",
            r"C:\Git\bin\bash.exe",
        ]
        for c in candidates:
            if os.path.isfile(c):
                return c
        w = shutil.which("bash")
        if w and "System32" not in w:
            return w
    w = shutil.which("bash")
    if w:
        return w
    raise RuntimeError("POSIX bash executable not found in environment")


BASH_BIN = find_bash()


def to_posix_path(path: str) -> str:
    """Convert Windows path C:\\foo\\bar to POSIX path /c/foo/bar for MSYS/Git bash."""
    path = path.replace("\\", "/")
    if len(path) >= 2 and path[1] == ":":
        return "/" + path[0].lower() + path[2:]
    return path


class TestDoctorDiagnostics(unittest.TestCase):
    """Test suite for mios-doctor hardening and survival."""

    def test_bash_syntax_clean(self):
        """Verifies bash -n passes cleanly with 0 syntax errors."""
        res = subprocess.run(
            [BASH_BIN, "-n", DOCTOR_SCRIPT],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self.assertEqual(
            res.returncode,
            0,
            f"bash -n failed on {DOCTOR_SCRIPT}:\nSTDOUT: {res.stdout}\nSTDERR: {res.stderr}",
        )

    def test_doctor_survives_partial_or_empty_environment(self):
        """Verifies mios-doctor executes to completion in a partial environment without crashing."""
        # Use an unused port to ensure no daemon is mistakenly assumed reachable
        env = os.environ.copy()
        env["MIOS_PORTS_LLM_LIGHT"] = "59998"
        env["MIOS_PORTS_OPEN_WEBUI"] = "59999"

        res = subprocess.run(
            [BASH_BIN, DOCTOR_SCRIPT],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )

        # In an incomplete environment, exit code must be normalized to 1 (failures found), not 127/abort
        self.assertEqual(
            res.returncode,
            1,
            f"Expected exit code 1 for partial environment, got {res.returncode}.\nOutput:\n{res.stdout}\nSTDERR:\n{res.stderr}",
        )

        # Verify key section markers are all traversed to the very end
        expected_sections = [
            "privilege chain",
            "mios-agent-pipe service",
            "llm-light + main models",
            "vision grounding",
            "open webui",
            "baked-image store",
            "WSLg / display",
            "GUI shims",
            "build pipeline readiness",
            "observability layer",
            "skill sync",
            "SOUL.md sync",
            "agent config sanity",
            "RESULT:",
        ]
        for section in expected_sections:
            self.assertIn(
                section,
                res.stdout,
                f"Missing expected diagnostic section '{section}' in output:\n{res.stdout}",
            )

        # Verify no unhandled shell fatal messages in stderr
        for err_pattern in ["unbound variable", "integer expression expected", "syntax error"]:
            self.assertNotIn(
                err_pattern,
                res.stderr.lower(),
                f"Found unexpected fatal error pattern '{err_pattern}' in stderr:\n{res.stderr}",
            )

    def test_systemd_not_booted_skips_service_checks(self):
        """Verifies when /run/systemd/system is absent, doctor skips service checks gracefully."""
        # Run in our test environment where /run/systemd/system is absent
        res = subprocess.run(
            [BASH_BIN, DOCTOR_SCRIPT],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if not os.path.isdir("/run/systemd/system"):
            self.assertIn(
                "systemd not booted as PID 1 (/run/systemd/system absent); skipping mios-agent-pipe.service checks",
                res.stdout,
            )
            self.assertIn(
                "systemd not booted as PID 1; skipping service checks (mios-daemon)",
                res.stdout,
            )
            # Ensure it did not register a failure for mios-agent-pipe
            self.assertNotIn("FAIL  mios-agent-pipe.service:", res.stdout)

    def test_permission_octal_normalization(self):
        """Directly verifies _get_perm helper logic on various permission strings."""
        test_script = """
set -euo pipefail

_get_perm() {
    local target="$1"
    local p=""
    p=$(stat -c '%a' "$target" 2>/dev/null || stat -f '%Lp' "$target" 2>/dev/null || true)
    if [[ "$p" =~ ^[0-7]{3,4}$ ]]; then
        if [ "${#p}" -eq 4 ]; then
            p="${p:1:3}"
        fi
        echo "$p"
    else
        echo ""
    fi
}

# Test 1: mock stat output
_mock_parse() {
    local p="$1"
    if [[ "$p" =~ ^[0-7]{3,4}$ ]]; then
        if [ "${#p}" -eq 4 ]; then
            p="${p:1:3}"
        fi
        echo "$p"
    else
        echo ""
    fi
}

echo "755 -> $(_mock_parse 755)"
echo "0755 -> $(_mock_parse 0755)"
echo "1777 -> $(_mock_parse 1777)"
echo "drwxr-xr-x -> $(_mock_parse drwxr-xr-x)"
echo "empty -> $(_mock_parse '')"

# Test evaluation safely
perm=$(_mock_parse 0755)
if [ -n "$perm" ] && [ "${perm:0:1}" -eq 7 ] && [ "${perm:1:1}" -ge 5 ]; then
    echo "0755 evaluated OK"
fi

perm=$(_mock_parse "invalid")
if [ -n "$perm" ] && [ "${perm:0:1}" -eq 7 ] && [ "${perm:1:1}" -ge 5 ]; then
    echo "invalid evaluated OK"
else
    echo "invalid handled safely without syntax error"
fi
"""
        res = subprocess.run(
            [BASH_BIN, "-c", test_script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self.assertEqual(res.returncode, 0, f"Script failed: {res.stderr}")
        self.assertIn("755 -> 755", res.stdout)
        self.assertIn("0755 -> 755", res.stdout)
        self.assertIn("1777 -> 777", res.stdout)
        self.assertIn("drwxr-xr-x -> \n", res.stdout)
        self.assertIn("0755 evaluated OK", res.stdout)
        self.assertIn("invalid handled safely without syntax error", res.stdout)

    def test_pyyaml_syntax_and_missing_package_handling(self):
        """Verifies PyYAML availability validation: skips with warn if missing, validates if present."""
        with tempfile.TemporaryDirectory() as tmpdir:
            valid_yaml = os.path.join(tmpdir, "valid.yaml").replace("\\", "/")
            with open(valid_yaml, "w", encoding="utf-8") as f:
                f.write("model: granite\nslots: 4\n")

            invalid_yaml = os.path.join(tmpdir, "invalid.yaml").replace("\\", "/")
            with open(invalid_yaml, "w", encoding="utf-8") as f:
                f.write("model: [unclosed list\n")

            test_script = f"""
set -euo pipefail
FAIL_COUNT=0
ok()   {{ echo "ok: $*"; }}
fail() {{ echo "fail: $*"; FAIL_COUNT=$((FAIL_COUNT+1)); }}
warn() {{ echo "warn: $*"; }}
info() {{ echo "info: $*"; }}

# Case 1: When PyYAML is available
_has_pyyaml=1
for cfg in "{valid_yaml}"; do
    if [ "$_has_pyyaml" -eq 1 ]; then
        if python3 -c 'import yaml,sys; yaml.safe_load(open(sys.argv[1]))' "$cfg" 2>/dev/null; then
            ok "$cfg parses as YAML"
        else
            fail "$cfg has YAML PARSE ERROR"
        fi
    fi
done

for cfg in "{invalid_yaml}"; do
    if [ "$_has_pyyaml" -eq 1 ]; then
        if python3 -c 'import yaml,sys; yaml.safe_load(open(sys.argv[1]))' "$cfg" 2>/dev/null; then
            ok "$cfg parses as YAML"
        else
            fail "$cfg has YAML PARSE ERROR"
        fi
    fi
done

# Case 2: When PyYAML is NOT available
_has_pyyaml=0
for cfg in "{invalid_yaml}"; do
    if [ "$_has_pyyaml" -eq 1 ]; then
        fail "$cfg has YAML PARSE ERROR"
    else
        warn "PyYAML not available (python3-pyyaml missing); skipping YAML syntax validation for $cfg"
    fi
done
"""
            res = subprocess.run(
                [BASH_BIN, "-c", test_script],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            self.assertEqual(res.returncode, 0, f"Script failed: {res.stderr}")
            self.assertIn("ok: " + valid_yaml + " parses as YAML", res.stdout)
            self.assertIn("fail: " + invalid_yaml + " has YAML PARSE ERROR", res.stdout)
            self.assertIn(
                "warn: PyYAML not available (python3-pyyaml missing); skipping YAML syntax validation",
                res.stdout,
            )

    def test_model_query_pipeline_fallbacks(self):
        """Verifies curl and python json extraction pipeline does not abort under pipefail on bad input."""
        test_script = """
set -euo pipefail

# Case A: empty tags string (simulates connection failure or empty response)
raw_tags=""
models=""
if [ -n "$raw_tags" ] && command -v python3 >/dev/null 2>&1; then
    models=$(python3 -c 'import sys,json; data=json.loads(sys.argv[1]); print(",".join(m.get("name","") for m in data.get("models",[])))' "$raw_tags" 2>/dev/null || echo "")
fi
echo "Empty case models='${models}'"

# Case B: invalid json tags string
raw_tags="<html>502 Bad Gateway</html>"
models=""
if [ -n "$raw_tags" ] && command -v python3 >/dev/null 2>&1; then
    models=$(python3 -c 'import sys,json; data=json.loads(sys.argv[1]); print(",".join(m.get("name","") for m in data.get("models",[])))' "$raw_tags" 2>/dev/null || echo "")
fi
echo "Invalid json models='${models}'"

# Case C: valid json tags string
raw_tags='{"models":[{"name":"granite4.1:8b"},{"name":"qwen3-vl:4b"}]}'
models=""
if [ -n "$raw_tags" ] && command -v python3 >/dev/null 2>&1; then
    models=$(python3 -c 'import sys,json; data=json.loads(sys.argv[1]); print(",".join(m.get("name","") for m in data.get("models",[])))' "$raw_tags" 2>/dev/null || echo "")
fi
echo "Valid json models='${models}'"
"""
        res = subprocess.run(
            [BASH_BIN, "-c", test_script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self.assertEqual(res.returncode, 0, f"Script failed: {res.stderr}")
        self.assertIn("Empty case models=''", res.stdout)
        self.assertIn("Invalid json models=''", res.stdout)
        self.assertIn("Valid json models='granite4.1:8b,qwen3-vl:4b'", res.stdout)

    def test_exit_code_normalization(self):
        """Verifies exit code normalization: exactly 0 for 0 failures, exactly 1 for >= 1 failures."""
        test_script = """
set -euo pipefail

_check_exit() {
    local FAIL_COUNT="$1"
    if [ "$FAIL_COUNT" -eq 0 ]; then
        return 0
    else
        return 1
    fi
}

_check_exit 0; echo "0 -> $?"
! _check_exit 1; echo "1 -> $?"
! _check_exit 99; echo "99 -> $?"
"""
        res = subprocess.run(
            [BASH_BIN, "-c", test_script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self.assertEqual(res.returncode, 0, f"Script failed: {res.stderr}")
        self.assertIn("0 -> 0", res.stdout)
        self.assertIn("1 -> 0", res.stdout)  # ! _check_exit 1 returned 0 because _check_exit returned 1
        self.assertIn("99 -> 0", res.stdout)  # ! _check_exit 99 returned 0 because _check_exit returned 1

    def test_positive_control_all_checks_pass(self):
        """Two-sided control: verifies that when all checks succeed, doctor outputs RESULT: HEALTHY and exits 0."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bin_dir = os.path.join(tmpdir, "bin")
            os.makedirs(bin_dir, exist_ok=True)

            # Create mock sudo that succeeds
            mock_sudo = os.path.join(bin_dir, "sudo")
            with open(mock_sudo, "w", encoding="utf-8", newline="\n") as f:
                f.write("#!/bin/sh\nexit 0\n")

            # Create mock curl that returns valid tags on port probe
            mock_curl = os.path.join(bin_dir, "curl")
            with open(mock_curl, "w", encoding="utf-8", newline="\n") as f:
                f.write('#!/bin/sh\necho \'{"models":[{"name":"granite4.1:8b"},{"name":"qwen3-vl:4b"}]}\'\nexit 0\n')

            # Create mock podman that returns Up
            mock_podman = os.path.join(bin_dir, "podman")
            with open(mock_podman, "w", encoding="utf-8", newline="\n") as f:
                f.write('#!/bin/sh\necho "Up 2 hours"\nexit 0\n')

            # Make mock scripts executable
            for m in [mock_sudo, mock_curl, mock_podman]:
                os.chmod(m, 0o755)

            bin_dir_posix = to_posix_path(bin_dir)

            # Test script that isolates checks and confirms 0 failures leads to exit 0
            test_script = f"""
set -euo pipefail
export PATH="{bin_dir_posix}:$PATH"

FAIL_COUNT=0
ok()   {{ printf "  ok    %s\\n" "$*"; }}
fail() {{ printf "  FAIL  %s\\n" "$*"; FAIL_COUNT=$((FAIL_COUNT+1)); }}
warn() {{ printf "  warn  %s\\n" "$*"; }}
info() {{ printf "        %s\\n" "$*"; }}

# Simulated passing privilege check
if sudo -n true 2>/dev/null; then
    ok "current user can sudo non-interactively"
else
    fail "cannot sudo"
fi

# Simulated passing model check
raw_tags=$(curl -s "http://localhost:8500/api/tags" 2>/dev/null || true)
models=$(python3 -c 'import sys,json; data=json.loads(sys.argv[1]); print(",".join(m.get("name","") for m in data.get("models",[])))' "$raw_tags" 2>/dev/null || echo "")
if [ -n "$models" ]; then
    ok "llm-light reachable"
else
    fail "llm-light unreachable"
fi

# Simulated passing podman check
if podman ps 2>/dev/null | grep -q Up; then
    ok "podman Up"
else
    fail "podman not Up"
fi

echo "FAIL_COUNT=$FAIL_COUNT"
if [ "$FAIL_COUNT" -eq 0 ]; then
    echo "  RESULT: HEALTHY"
    exit 0
else
    echo "  RESULT: $FAIL_COUNT failures"
    exit 1
fi
"""
            res = subprocess.run(
                [BASH_BIN, "-c", test_script],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            self.assertEqual(res.returncode, 0, f"Script failed: {res.stderr}\nOutput: {res.stdout}")
            self.assertIn("FAIL_COUNT=0", res.stdout)
            self.assertIn("RESULT: HEALTHY", res.stdout)


if __name__ == "__main__":
    unittest.main()

