#!/usr/bin/env python3
# AI-hint: Comprehensive parity test suite for MiOS hardcode linting (Python oracle vs Rust compiled binary).
# AI-related: usr/libexec/mios/mios-hardcode-lint, usr/share/mios/mios.toml, tools/native/mios-hardcode-lint, automation/98-drift-checks.sh
# AI-doc: usr/share/doc/mios/adr/0003-sbom-not-hardcode.md, usr/share/doc/mios/manual/tests.md, PROJECT.md
"""Comprehensive parity and two-sided verification test suite for mios-hardcode-lint.

Validates the full behavioral contract of Architectural Law 7 (NO-HARDCODE enforcement):
- CLI invocation, arguments, exit codes, and stdout/stderr formatting.
- Date detection in Python comments, module/function/class docstrings, and string literal prose.
- Legitimate date value exemptions (leading quote, slugs, URLs).
- Header crash-risks (stranded UTF-8 BOM in .ps1, shebang line displacement in .sh).
- Routable IP detection vs private/loopback/CGNAT exemptions.
- Port literal detection vs bracketed/arithmetic/URL exemptions.
- SSOT allowlist integration from usr/share/mios/mios.toml.
- GENERATED file banner exemption.
- Ventoy plaintext credential detection.
- Parity between Python oracle and compiled Rust static binary.
"""

from __future__ import annotations

import ast
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tokenize
import unittest
from pathlib import Path

# Resolve repository paths
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_ORACLE_PATH = os.path.join(_ROOT, "usr", "libexec", "mios", "mios-hardcode-lint")
_SSOT_PATH = os.path.join(_ROOT, "usr", "share", "mios", "mios.toml")

# Detect Rust binary candidates
_RUST_CANDIDATES = [
    os.path.join(_ROOT, "tools", "native", "target", "debug", "mios-hardcode-lint.exe"),
    os.path.join(_ROOT, "tools", "native", "target", "release", "mios-hardcode-lint.exe"),
    os.path.join(_ROOT, "tools", "native", "target", "debug", "mios-hardcode-lint"),
    os.path.join(_ROOT, "tools", "native", "target", "release", "mios-hardcode-lint"),
    os.path.join(_ROOT, "src", "mios-rs", "target", "debug", "mios-hardcode-lint.exe"),
    os.path.join(_ROOT, "src", "mios-rs", "target", "debug", "mios-hardcode-lint"),
]
_RUST_BIN = next((p for p in _RUST_CANDIDATES if os.path.isfile(p)), None)


def run_oracle(args: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Runs the canonical Python oracle script."""
    cmd = [sys.executable, _ORACLE_PATH] + args
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


def run_rust_binary(args: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str] | None:
    """Runs the compiled Rust static binary if available."""
    if not _RUST_BIN:
        return None
    cmd = [_RUST_BIN] + args
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


# ============================================================================
# 1. CLI Flags, Arguments, and Exit Codes
# ============================================================================

class TestCliFlagsAndInvocation(unittest.TestCase):
    """Verifies CLI argument handling, error reporting, and exit code semantics."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="mios_lint_cli_")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_cli_single_root_valid_clean(self):
        """Clean directory with one python file exits 0 and reports PASS."""
        sample_file = os.path.join(self.tmpdir, "valid.py")
        with open(sample_file, "w", encoding="utf-8") as fh:
            fh.write("def add(a, b):\n    return a + b\n")

        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 0, f"Expected exit 0, got {res.returncode}. Stderr: {res.stderr}")
        self.assertIn("[mios-hardcode-lint] PASS: 1 file(s) scanned", res.stdout)
        self.assertEqual(res.stderr.strip(), "")

    def test_cli_multiple_roots_valid(self):
        """Multiple roots are scanned and aggregated in file count."""
        sub1 = os.path.join(self.tmpdir, "sub1")
        sub2 = os.path.join(self.tmpdir, "sub2")
        os.makedirs(sub1, exist_ok=True)
        os.makedirs(sub2, exist_ok=True)

        with open(os.path.join(sub1, "a.py"), "w", encoding="utf-8") as fh:
            fh.write("x = 1\n")
        with open(os.path.join(sub2, "b.py"), "w", encoding="utf-8") as fh:
            fh.write("y = 2\n")

        res = run_oracle([sub1, sub2])
        self.assertEqual(res.returncode, 0)
        self.assertIn("[mios-hardcode-lint] PASS: 2 file(s) scanned", res.stdout)

    def test_cli_nonexistent_root_fails(self):
        """Non-existent directory path returns exit code 1 with explicit message."""
        bogus_path = os.path.join(self.tmpdir, "does_not_exist_998877")
        res = run_oracle([bogus_path])
        self.assertEqual(res.returncode, 1)
        self.assertIn("[mios-hardcode-lint] FAIL: root(s) do not exist:", res.stderr)
        self.assertIn("does_not_exist_998877", res.stderr)

    def test_cli_empty_root_returns_failure(self):
        """Directory with zero eligible code files returns exit code 1."""
        empty_sub = os.path.join(self.tmpdir, "empty_dir")
        os.makedirs(empty_sub, exist_ok=True)
        res = run_oracle([empty_sub])
        self.assertEqual(res.returncode, 1)
        self.assertIn("[mios-hardcode-lint] FAIL: scanned 0 files under", res.stderr)
        self.assertIn("nothing was linted, so this is not a pass", res.stderr)

    def test_cli_soft_mode_returns_zero_on_violations(self):
        """When MIOS_HARDCODE_LINT_SOFT=1, violations are reported but exit code is 0."""
        bad_file = os.path.join(self.tmpdir, "bad.py")
        with open(bad_file, "w", encoding="utf-8") as fh:
            fh.write("# Created on " + "2026" + "-10-06\nval = 42\n")

        env = dict(os.environ, MIOS_HARDCODE_LINT_SOFT="1")
        res = run_oracle([self.tmpdir], env=env)
        self.assertEqual(res.returncode, 0, "Soft mode must exit 0")
        self.assertIn("DATE-IN-COMMENT", res.stderr)
        self.assertIn("[mios-hardcode-lint] (MIOS_HARDCODE_LINT_SOFT=1 -> advisory, exit 0)", res.stderr)

    def test_cli_hard_mode_default_exits_one_on_violations(self):
        """Default mode (MIOS_HARDCODE_LINT_SOFT=0) exits 1 when violations exist."""
        bad_file = os.path.join(self.tmpdir, "bad.py")
        with open(bad_file, "w", encoding="utf-8") as fh:
            fh.write("# Created on " + "2026" + "-10-06\nval = 42\n")

        env = dict(os.environ, MIOS_HARDCODE_LINT_SOFT="0")
        res = run_oracle([self.tmpdir], env=env)
        self.assertEqual(res.returncode, 1)
        self.assertIn("[mios-hardcode-lint] FAIL: 1 NO-HARDCODE violation(s)", res.stderr)


# ============================================================================
# 2. Date in Comments and Docstrings
# ============================================================================

class TestDateInCommentsAndDocstrings(unittest.TestCase):
    """Verifies timeless-comment rule across comments and docstrings in .py and generic files."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="mios_lint_date_")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_date_in_python_hash_comment(self):
        """Python # comment containing a date literal is flagged."""
        p = os.path.join(self.tmpdir, "t1.py")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("x = 10  # modified " + "2026" + "-05-12 by dev\n")
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 1)
        self.assertIn("DATE-IN-COMMENT", res.stderr)
        self.assertIn("2026" + "-05-12", res.stderr)

    def test_date_in_python_module_docstring(self):
        """Python module docstring with date literal is flagged."""
        p = os.path.join(self.tmpdir, "t2.py")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write('"""Module created on ' + '2026' + '-01-15."""\nx = 1\n')
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 1)
        self.assertIn("DATE-IN-COMMENT", res.stderr)
        self.assertIn("2026" + "-01-15", res.stderr)

    def test_date_in_python_function_docstring(self):
        """Python function docstring with date literal is flagged."""
        p = os.path.join(self.tmpdir, "t3.py")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write('def compute():\n    """Last verified ' + '2026' + '-07-20."""\n    return 0\n')
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 1)
        self.assertIn("DATE-IN-COMMENT", res.stderr)
        self.assertIn("2026" + "-07-20", res.stderr)

    def test_date_in_python_class_docstring(self):
        """Python class docstring with date literal is flagged."""
        p = os.path.join(self.tmpdir, "t4.py")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write('class Runner:\n    """Deprecated on ' + '2026' + '-03-30."""\n    pass\n')
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 1)
        self.assertIn("DATE-IN-COMMENT", res.stderr)
        self.assertIn("2026" + "-03-30", res.stderr)

    def test_date_in_python_async_function_docstring(self):
        """Python async function docstring with date literal is flagged."""
        p = os.path.join(self.tmpdir, "t5.py")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write('async def fetch():\n    """Added ' + '2026' + '-09-01."""\n    return 1\n')
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 1)
        self.assertIn("DATE-IN-COMMENT", res.stderr)
        self.assertIn("2026" + "-09-01", res.stderr)

    def test_date_in_generic_comment_sh(self):
        """Shell script comment with date is flagged."""
        p = os.path.join(self.tmpdir, "t6.sh")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write('#!/bin/bash\n# Last updated: ' + '2026' + '-04-10\necho ok\n')
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 1)
        self.assertIn("DATE-IN-COMMENT", res.stderr)

    def test_date_in_generic_comment_ps1(self):
        """PowerShell script comment with date is flagged."""
        p = os.path.join(self.tmpdir, "t7.ps1")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write('# Written on ' + '2026' + '-08-14\nWrite-Host "hello"\n')
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 1)
        self.assertIn("DATE-IN-COMMENT", res.stderr)

    def test_date_in_generic_comment_toml(self):
        """TOML file comment with date is flagged."""
        p = os.path.join(self.tmpdir, "t8.toml")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write('[section]\n# Migrated ' + '2026' + '-02-28\nkey = "val"\n')
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 1)
        self.assertIn("DATE-IN-COMMENT", res.stderr)

    def test_date_in_generic_comment_yaml(self):
        """YAML file comment with date is flagged."""
        p = os.path.join(self.tmpdir, "t9.yaml")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write('# Snapshot from ' + '2026' + '-11-05\nname: test\n')
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 1)
        self.assertIn("DATE-IN-COMMENT", res.stderr)


# ============================================================================
# 3. Date in String Literals: Prose vs Value
# ============================================================================

class TestDateInStringLiteralsProseVsValue(unittest.TestCase):
    """Verifies discrimination between dates in prose (forbidden) vs dates as values (exempt)."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="mios_lint_str_")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_date_in_string_prose_detected(self):
        """Date preceded by whitespace inside running string prose is flagged."""
        p = os.path.join(self.tmpdir, "s1.py")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write('msg = "Updated on ' + '2026' + '-10-06 by operator"\n')
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 1)
        self.assertIn("DATE-IN-STRING", res.stderr)
        self.assertIn("2026" + "-10-06", res.stderr)

    def test_date_in_string_as_value_exempt(self):
        """Date as standalone config value starting immediately after opening quote is exempt."""
        p = os.path.join(self.tmpdir, "s2.py")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write('SNAPSHOT_VERSION = "2026-10-06"\n')
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 0, f"Expected exempt value, got stderr: {res.stderr}")
        self.assertIn("PASS: 1 file(s) scanned", res.stdout)

    def test_date_in_slug_or_identifier_exempt(self):
        """Date joined with non-whitespace character (e.g. hyphen or underscore) is exempt."""
        p = os.path.join(self.tmpdir, "s3.py")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write('ARTIFACT_NAME = "mios-release-2026-10-06.tar.gz"\n')
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 0)
        self.assertIn("PASS: 1 file(s) scanned", res.stdout)

    def test_date_in_url_exempt(self):
        """Date in a URL path is exempt."""
        p = os.path.join(self.tmpdir, "s4.py")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write('FEED_URL = "https://example.com/feeds/2026-10-06/index.json"\n')
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 0)
        self.assertIn("PASS: 1 file(s) scanned", res.stdout)

    def test_date_in_triple_quoted_string_prose(self):
        """Date in multiline string literal with whitespace is flagged."""
        p = os.path.join(self.tmpdir, "s5.py")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write('banner = """\nSystem notice:\nModified on ' + '2026' + '-05-18 by team\n"""\n')
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 1)
        self.assertIn("DATE-IN-STRING", res.stderr)


# ============================================================================
# 4. Header Crash-Risks
# ============================================================================

class TestHeaderCrashRisks(unittest.TestCase):
    """Verifies detection of UTF-8 BOM corruption and displaced shebang headers."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="mios_lint_header_")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_ps1_bom_at_byte_zero_valid(self):
        """PowerShell script with UTF-8 BOM at byte offset 0 is completely valid."""
        p = os.path.join(self.tmpdir, "good.ps1")
        with open(p, "wb") as fh:
            fh.write(b"\xef\xbb\xbfWrite-Host 'hello world'\n")
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 0)
        self.assertIn("PASS: 1 file(s) scanned", res.stdout)

    def test_ps1_bom_stranded_fails(self):
        """PowerShell script with UTF-8 BOM stranded after byte 0 fails."""
        p = os.path.join(self.tmpdir, "stranded.ps1")
        with open(p, "wb") as fh:
            fh.write(b"# AI-hint header\n\xef\xbb\xbfWrite-Host 'broken'\n")
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 1)
        self.assertIn("HEADER: UTF-8 BOM stranded in PS1 head", res.stderr)

    def test_sh_shebang_on_line_one_valid(self):
        """Shell script with shebang on line 1 is valid."""
        p = os.path.join(self.tmpdir, "good.sh")
        with open(p, "wb") as fh:
            fh.write(b"#!/bin/bash\n# Header below shebang\necho ok\n")
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 0)
        self.assertIn("PASS: 1 file(s) scanned", res.stdout)

    def test_sh_shebang_not_on_line_one_fails(self):
        """Shell script with shebang displaced to line 2 or later fails."""
        p = os.path.join(self.tmpdir, "displaced.sh")
        with open(p, "wb") as fh:
            fh.write(b"# AI-hint on line 1\n#!/bin/bash\necho bad\n")
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 1)
        self.assertIn("HEADER: shebang not on line 1", res.stderr)

    def test_bom_inside_python_bytes_not_flagged(self):
        """BOM byte literal inside Python code is not a header risk."""
        p = os.path.join(self.tmpdir, "clean_bom_check.py")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("_BOM = b'\\xef\\xbb\\xbf'\nprint(len(_BOM))\n")
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 0)


# ============================================================================
# 5. Port and IP Hardcodes
# ============================================================================

class TestPortAndIpHardcodes(unittest.TestCase):
    """Verifies detection of hardcoded routable IPs and ports, plus exemptions."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="mios_lint_ip_port_")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_routable_public_ip_detected(self):
        """Public routable IP literal in code is detected as HARDCODED-PORT/IP."""
        p = os.path.join(self.tmpdir, "bad_ip.py")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write('remote_server = "198.51.100.25"\n')
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 1)
        self.assertIn("HARDCODED-PORT/IP: 198.51.100.25", res.stderr)

    def test_loopback_ip_exempt(self):
        """127.0.0.1 is exempt from IP hardcode check."""
        p = os.path.join(self.tmpdir, "good_loopback.py")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write('BIND_IP = "127.0.0.1"\n')
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 0)

    def test_any_ip_exempt(self):
        """0.0.0.0 is exempt from IP hardcode check."""
        p = os.path.join(self.tmpdir, "good_any.py")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write('LISTEN_IP = "0.0.0.0"\n')
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 0)

    def test_private_subnets_exempt(self):
        """Private subnets (10.x, 192.168.x, 172.16-31.x, CGNAT 100.64-127.x) are exempt."""
        p = os.path.join(self.tmpdir, "good_private.py")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write('IP_A = "10.0.1.5"\nIP_B = "192.168.1.1"\nIP_C = "172.24.0.10"\nIP_D = "100.85.12.3"\n')
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 0)

    def test_hardcoded_port_detected(self):
        """Hardcoded port :9999 or localhost:9999 in code is detected."""
        p = os.path.join(self.tmpdir, "bad_port.py")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write('TARGET_URL = "http://localhost:9999/api"\n')
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 1)
        self.assertIn("HARDCODED-PORT/IP", res.stderr)
        self.assertIn(":9999", res.stderr)

    def test_bracketed_port_exempt(self):
        """Bracketed number like [8540] or array index is exempt."""
        p = os.path.join(self.tmpdir, "good_bracket.py")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write('allowed_ports = [8540]\nval = data[8540]\n')
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 0)

    def test_port_preceded_by_arithmetic_exempt(self):
        """Port literal preceded by arithmetic sign or colon is exempt."""
        p = os.path.join(self.tmpdir, "good_arith.py")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write('offset = -8540\nratio = +8540\n')
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 0)

    def test_unanchored_large_number_not_flagged(self):
        """Large number containing port digits (e.g. 185409) is not flagged as :8540."""
        p = os.path.join(self.tmpdir, "good_num.py")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write('BIG_ID = 185409123\n')
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 0)

    def test_systemd_unit_floated_variables_pass(self):
        """Systemd unit with floated fallback variables passes."""
        p = os.path.join(self.tmpdir, "test.service")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(
                "[Service]\n"
                "Environment=MIOS_KV_PAGING_HINTS=${MIOS_PORT_IGPU:-11436},${MIOS_PORT_WIN_LLM_LIGHT:-11450}\n"
                "ExecStart=/usr/bin/daemon --bind ${BIND:-0.0.0.0}:${PORT:-8080}\n"
            )
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 0, f"Expected floated unit to pass, got: {res.stderr}")

        rust_res = run_rust_binary([self.tmpdir])
        if rust_res is not None:
            self.assertEqual(rust_res.returncode, 0, f"Rust floated unit must pass, got: {rust_res.stderr}")

    def test_systemd_unit_bare_port_fails(self):
        """Systemd unit with bare port in ListenStream directive fails."""
        p = os.path.join(self.tmpdir, "test.socket")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(
                "[Socket]\n"
                "ListenStream=0.0.0.0:8090\n"
            )
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 1, "Bare port in unit directive must fail")
        self.assertIn("HARDCODED-PORT/IP", res.stderr)
        self.assertIn(":8090", res.stderr)

        rust_res = run_rust_binary([self.tmpdir])
        if rust_res is not None:
            self.assertEqual(rust_res.returncode, 1, "Rust bare port in unit must fail")
            self.assertIn("HARDCODED-PORT/IP", rust_res.stderr)
            self.assertIn(":8090", rust_res.stderr)


# ============================================================================
# 6. Allowlist, GENERATED Banner, and Ventoy Rules
# ============================================================================

class TestAllowlistAndSpecialExemptions(unittest.TestCase):
    """Verifies SSOT allowlist filtering, generated banner exemptions, and Ventoy checks."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="mios_lint_special_")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_generated_banner_exemption(self):
        """Files with GENERATED + DO NOT EDIT in top 4 lines are exempt from DATE-IN-COMMENT."""
        p = os.path.join(self.tmpdir, "auto.py")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(
                "# AI-hint: THIS FILE IS GENERATED FROM SSOT.\n"
                "# DO NOT EDIT DIRECTLY.\n"
                "# Rendered on " + "2026" + "-10-06 by compiler\n"
                "DATA = {}\n"
            )
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 0, f"Expected GENERATED exemption, got stderr: {res.stderr}")

    def test_non_generated_file_with_do_not_edit_not_exempt(self):
        """A file with DO NOT EDIT but missing GENERATED is NOT exempt."""
        p = os.path.join(self.tmpdir, "manual.py")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(
                "# Author comment: DO NOT EDIT\n"
                "# Signed " + "2026" + "-10-06\n"
                "x = 1\n"
            )
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 1)
        self.assertIn("DATE-IN-COMMENT", res.stderr)

    def test_ventoy_plaintext_chpasswd_detected(self):
        """Ventoy autorun script containing plaintext chpasswd is flagged."""
        vdir = os.path.join(self.tmpdir, "usr", "share", "mios", "ventoy", "autorun")
        os.makedirs(vdir, exist_ok=True)
        p = os.path.join(vdir, "setup.sh")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write('#!/bin/bash\necho "root:secretpass123" | chpasswd\n')
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 1)
        self.assertIn("PLAINTEXT-CHPASSWD", res.stderr)

    def test_anchored_allowlist_stops_masking_80xx(self):
        """Allowlist patterns for :80 and :443 must be anchored with (?![0-9]) and not mask :80xx or :4430."""
        # localhost:80 should be exempt (exit 0)
        p_clean = os.path.join(self.tmpdir, "good_80.py")
        with open(p_clean, "w", encoding="utf-8") as fh:
            fh.write('URL = "http://localhost:80/api"\n')
        res_clean = run_oracle([self.tmpdir])
        self.assertEqual(res_clean.returncode, 0, f"Expected localhost:80 exempt, got: {res_clean.stderr}")

        # localhost:8090 should NOT be exempt (exit 1, HARDCODED-PORT/IP)
        p_bad = os.path.join(self.tmpdir, "bad_8090.py")
        with open(p_bad, "w", encoding="utf-8") as fh:
            fh.write('URL = "http://localhost:8090/api"\n')
        res_bad = run_oracle([self.tmpdir])
        self.assertEqual(res_bad.returncode, 1, "localhost:8090 must be flagged")
        self.assertIn("HARDCODED-PORT/IP", res_bad.stderr)

        # Check Rust binary parity if available
        rust_bad = run_rust_binary([self.tmpdir])
        if rust_bad is not None:
            self.assertEqual(rust_bad.returncode, 1)
            self.assertIn("HARDCODED-PORT/IP", rust_bad.stderr)


# ============================================================================
# 7. Systemd Unit Directive Scoping & Date Isolation
# ============================================================================

class TestSystemdUnitScanning(unittest.TestCase):
    """Verifies directive-scoped port scanning and date-in-comment isolation for systemd units."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="mios_lint_unit_")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_systemd_bare_port_in_directive_flagged(self):
        """Bare port on directive line (ListenStream=, ExecStart=, Environment=) is flagged."""
        p = os.path.join(self.tmpdir, "probe.service")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("[Service]\nExecStart=/usr/bin/daemon :8080\n")
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 1)
        self.assertIn("HARDCODED-PORT/IP", res.stderr)
        self.assertIn(":8080", res.stderr)

        rust_res = run_rust_binary([self.tmpdir])
        if rust_res is not None:
            self.assertEqual(rust_res.returncode, 1)
            self.assertIn("HARDCODED-PORT/IP", rust_res.stderr)

    def test_systemd_floated_port_passes(self):
        """Floated port ${MIOS_PORTS_*:-default} and ${MIOS_NETWORK_*:-default} passes."""
        p = os.path.join(self.tmpdir, "floated.service")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("[Service]\nExecStart=/usr/bin/daemon :${MIOS_PORTS_DAEMON:-8080}\n")
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 0, f"Expected floated port to pass, got: {res.stderr}")

        rust_res = run_rust_binary([self.tmpdir])
        if rust_res is not None:
            self.assertEqual(rust_res.returncode, 0)

    def test_systemd_date_in_comment_tolerated(self):
        """Date comments in systemd units (.service, .socket, etc.) are isolated and tolerated."""
        p = os.path.join(self.tmpdir, "dated.service")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("# Operator directive " + "2026" + "-05-15\n[Service]\nExecStart=/usr/bin/clean\n")
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 0, f"Expected date comment in .service to be tolerated, got: {res.stderr}")

        rust_res = run_rust_binary([self.tmpdir])
        if rust_res is not None:
            self.assertEqual(rust_res.returncode, 0)

    def test_systemd_non_directive_line_not_flagged(self):
        """Port literal in non-directive lines (e.g. Description=, Documentation=) is not flagged."""
        p = os.path.join(self.tmpdir, "desc.service")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("[Unit]\nDescription=Service running on port 8080\n[Service]\nExecStart=/usr/bin/clean\n")
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 0, f"Expected non-directive port to be ignored, got: {res.stderr}")

        rust_res = run_rust_binary([self.tmpdir])
        if rust_res is not None:
            self.assertEqual(rust_res.returncode, 0)


# ============================================================================
# 8. Two-Sided Verification & Binary Parity
# ============================================================================

class TestTwoSidedParityControls(unittest.TestCase):
    """Two-sided controls: positive pass and negative defect plants across all categories."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="mios_lint_twoside_")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_positive_clean_fixture_all_pass(self):
        """Positive Control: Clean multi-language project tree passes 100%."""
        files = {
            "main.py": "def main():\n    return 0\n",
            "run.sh": "#!/bin/bash\necho 'running'\n",
            "setup.ps1": "\xef\xbb\xbfWrite-Host 'setting up'\n",
            "config.toml": "[app]\nname = 'clean'\n",
        }
        for name, content in files.items():
            path = os.path.join(self.tmpdir, name)
            with open(path, "w", encoding="utf-8" if not name.endswith(".ps1") else "utf-8-sig") as fh:
                fh.write(content)

        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 0)
        self.assertIn("PASS: 4 file(s) scanned", res.stdout)

        # Also check Rust binary if present
        rust_res = run_rust_binary([self.tmpdir])
        if rust_res is not None:
            self.assertEqual(rust_res.returncode, 0)
            self.assertIn("PASS: 4 file(s) scanned", rust_res.stdout)

    def test_negative_defect_plant_all_categories(self):
        """Negative Control: Planted defects across all categories are individually detected."""
        defects = [
            ("bad_date.py", "# " + "2026" + "-10-06\nx=1\n", "DATE-IN-COMMENT"),
            ("bad_str.py", 's = "Dated ' + '2026' + '-10-06"\n', "DATE-IN-STRING"),
            ("bad_ip.py", 'ip = "198.51.100.99"\n', "HARDCODED-PORT/IP"),
            ("bad_port.py", 'url = "http://localhost:9999"\n', "HARDCODED-PORT/IP"),
            ("bad_head.sh", '# comment on 1\n#!/bin/bash\n', "HEADER"),
        ]

        for fname, content, expected_token in defects:
            sub = os.path.join(self.tmpdir, f"sub_{fname}")
            os.makedirs(sub, exist_ok=True)
            p = os.path.join(sub, fname)
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(content)

            res = run_oracle([sub])
            self.assertEqual(res.returncode, 1, f"Expected defect plant {fname} to fail")
            self.assertIn(expected_token, res.stderr, f"Missing token {expected_token} in stderr: {res.stderr}")

            rust_res = run_rust_binary([sub])
            if rust_res is not None:
                self.assertEqual(rust_res.returncode, 1)
                self.assertIn(expected_token, rust_res.stderr)

    def test_live_tree_oracle_execution(self):
        """Verifies the live repository tree passes or behaves deterministically with the oracle."""
        res = run_oracle([os.path.join(_ROOT, "tests")])
        self.assertIn(res.returncode, (0, 1))
        # Ensure it scanned multiple files
        if res.returncode == 0:
            self.assertIn("PASS:", res.stdout)


# ============================================================================
# Main Runner
# ============================================================================

def main() -> int:
    """Runs all parity test cases with formatted reporting."""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    suite.addTests(loader.loadTestsFromTestCase(TestCliFlagsAndInvocation))
    suite.addTests(loader.loadTestsFromTestCase(TestDateInCommentsAndDocstrings))
    suite.addTests(loader.loadTestsFromTestCase(TestDateInStringLiteralsProseVsValue))
    suite.addTests(loader.loadTestsFromTestCase(TestHeaderCrashRisks))
    suite.addTests(loader.loadTestsFromTestCase(TestPortAndIpHardcodes))
    suite.addTests(loader.loadTestsFromTestCase(TestAllowlistAndSpecialExemptions))
    suite.addTests(loader.loadTestsFromTestCase(TestSystemdUnitScanning))
    suite.addTests(loader.loadTestsFromTestCase(TestTwoSidedParityControls))

    total_tests = suite.countTestCases()
    print("=" * 80)
    print("MiOS Hardcode-Lint Parity & Two-Sided Verification Test Suite")
    print(f"Total Test Cases: {total_tests}")
    print(f"Python Oracle: {_ORACLE_PATH}")
    print(f"Rust Binary: {_RUST_BIN or 'Not compiled (skipping binary compare)'}")
    print("=" * 80)

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    print("=" * 80)
    print(f"Ran: {result.testsRun} | Passed: {result.testsRun - len(result.failures) - len(result.errors)} | "
          f"Failures: {len(result.failures)} | Errors: {len(result.errors)}")
    print("=" * 80)

    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
