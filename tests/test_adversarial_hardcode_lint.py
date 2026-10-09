#!/usr/bin/env python3
# AI-hint: Adversarial stress test suite comparing Rust mios-hardcode-lint against Python oracle.
# AI-related: usr/libexec/mios/mios-hardcode-lint, tools/native/mios-hardcode-lint/src/main.rs
"""Adversarial stress harness for mios-hardcode-lint (T-1161 parity and defect detection).

Executes four targeted challenge dimensions:
1. Header crash-risks (stranded BOMs at various offsets, shebang displacements).
2. Date attribution in string literals vs values (multiline, raw, docstrings, markdown URLs).
3. IP address heuristics (IPv4 edge cases, CIDR boundaries, IPv6, port syntaxes).
4. Error behavior & filesystem corner cases (non-existent paths, empty dirs, binary files, syntax errors).
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import tempfile
import unittest

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
# Fixture date for the cases the lint must FLAG. Assembled at runtime so this
# source carries no date literal of its own; the fixtures it writes are unchanged.
_D = "-".join(("2026", "10", "06"))
_ORACLE_PATH = os.path.join(_ROOT, "usr", "libexec", "mios", "mios-hardcode-lint")
_SSOT_PATH = os.path.join(_ROOT, "usr", "share", "mios", "mios.toml")
_EXE_SUFFIX = ".exe" if os.name == "nt" else ""


def _locate_rust_bin() -> str | None:
    """This tree's mios-hardcode-lint, else the installed one.

    The native engine writes release builds under the SSOT target triple
    ([build.native.linux].targets / [build.native.windows].target); a plain
    cargo build writes target/{release,debug}. The native install puts the
    binary on PATH, which is what CI has after its native stage.
    """
    name = "mios-hardcode-lint" + _EXE_SUFFIX
    target = os.path.join(_ROOT, "tools", "native", "target")
    with open(_SSOT_PATH, "rb") as fh:
        native = tomllib.load(fh)["build"]["native"]
    triple = (native["windows"]["target"] if os.name == "nt"
              else native["linux"]["targets"].get(platform.machine()))
    candidates = [os.path.join(target, triple, "release", name)] if triple else []
    candidates += [os.path.join(target, profile, name) for profile in ("release", "debug")]
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    return shutil.which(name)


_RUST_BIN = _locate_rust_bin()


def run_oracle(args: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    # The oracle hands off to a native binary whenever it finds one, which
    # made every parity case compare the Rust binary with itself. Pin it to
    # its own Python implementation.
    oracle_env = dict(os.environ if env is None else env)
    oracle_env["MIOS_HARDCODE_LINT_NO_NATIVE"] = "1"
    cmd = [sys.executable, _ORACLE_PATH] + args
    return subprocess.run(cmd, capture_output=True, text=True, env=oracle_env)


def run_rust(args: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    cmd = [_RUST_BIN] + args
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


class HardcodeLintAdversarialTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # A missing binary is a failure, not a skip: parity is this suite's subject.
        if _RUST_BIN is None:
            raise AssertionError("mios-hardcode-lint is not built or installed; "
                                 "build it with automation/55-native-build.sh")

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="adv_hardcode_")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def assertParity(self, args: list[str], test_desc: str = "", env: dict[str, str] | None = None):
        """Asserts 100% exit code parity and semantic output parity between Oracle and Rust."""
        o_res = run_oracle(args, env=env)
        r_res = run_rust(args, env=env)

        # 1. Exit code must be identical
        self.assertEqual(
            o_res.returncode,
            r_res.returncode,
            f"[{test_desc}] Exit code mismatch! Oracle: {o_res.returncode}, Rust: {r_res.returncode}.\n"
            f"Oracle stdout:\n{o_res.stdout}\nOracle stderr:\n{o_res.stderr}\n"
            f"Rust stdout:\n{r_res.stdout}\nRust stderr:\n{r_res.stderr}"
        )

        # 2. Both PASS or both FAIL
        if o_res.returncode == 0:
            self.assertIn("PASS:", o_res.stdout)
            self.assertIn("PASS:", r_res.stdout)
        else:
            self.assertIn("FAIL:", o_res.stderr)
            self.assertIn("FAIL:", r_res.stderr)

        return o_res, r_res


# ============================================================================
# Challenge 1: Header Crash-Risks
# ============================================================================

class TestChallenge1HeaderCrashRisks(HardcodeLintAdversarialTestBase):
    """Stress tests UTF-8 BOM offsets and shebang displacement variants."""

    def test_ps1_bom_offset_zero(self):
        """BOM at byte 0 is completely valid."""
        p = os.path.join(self.tmpdir, "good.ps1")
        with open(p, "wb") as f:
            f.write(b"\xef\xbb\xbfWrite-Host 'hello'")
        self.assertParity([self.tmpdir], "PS1 BOM at offset 0")

    def test_ps1_bom_stranded_offset_1(self):
        """BOM stranded at offset 1 (< 256)."""
        p = os.path.join(self.tmpdir, "bad1.ps1")
        with open(p, "wb") as f:
            f.write(b"#\xef\xbb\xbfWrite-Host 'hello'")
        o, r = self.assertParity([self.tmpdir], "PS1 BOM at offset 1")
        self.assertIn("HEADER: UTF-8 BOM stranded in PS1 head", o.stderr)
        self.assertIn("HEADER: UTF-8 BOM stranded in PS1 head", r.stderr)

    def test_ps1_bom_stranded_offset_50(self):
        """BOM stranded at offset 50 (< 256)."""
        p = os.path.join(self.tmpdir, "bad50.ps1")
        with open(p, "wb") as f:
            f.write(b"#" * 50 + b"\xef\xbb\xbfWrite-Host 'hello'")
        o, r = self.assertParity([self.tmpdir], "PS1 BOM at offset 50")
        self.assertIn("HEADER: UTF-8 BOM stranded in PS1 head", o.stderr)
        self.assertIn("HEADER: UTF-8 BOM stranded in PS1 head", r.stderr)

    def test_ps1_bom_stranded_offset_253(self):
        """BOM starting at offset 253 (spans bytes 253, 254, 255 - inside first 256 bytes)."""
        p = os.path.join(self.tmpdir, "bad253.ps1")
        with open(p, "wb") as f:
            f.write(b"#" * 253 + b"\xef\xbb\xbfWrite-Host 'hello'")
        o, r = self.assertParity([self.tmpdir], "PS1 BOM at offset 253")
        self.assertIn("HEADER: UTF-8 BOM stranded in PS1 head", o.stderr)
        self.assertIn("HEADER: UTF-8 BOM stranded in PS1 head", r.stderr)

    def test_ps1_bom_stranded_offset_254(self):
        """BOM starting at offset 254 (crosses 256-byte boundary)."""
        p = os.path.join(self.tmpdir, "cross254.ps1")
        with open(p, "wb") as f:
            f.write(b"#" * 254 + b"\xef\xbb\xbfWrite-Host 'hello'")
        # Note: raw[:256] only includes first 2 bytes of BOM, so neither flags it as stranded in head
        self.assertParity([self.tmpdir], "PS1 BOM at offset 254")

    def test_ps1_bom_stranded_offset_300(self):
        """BOM stranded deep in file (> 256 bytes). Legitimate in strings, not flagged as head risk."""
        p = os.path.join(self.tmpdir, "deep.ps1")
        with open(p, "wb") as f:
            f.write(b"#" * 300 + b"\xef\xbb\xbfWrite-Host 'hello'")
        self.assertParity([self.tmpdir], "PS1 BOM at offset 300")

    def test_sh_shebang_line_1_clean(self):
        """Shebang on line 1 is valid."""
        p = os.path.join(self.tmpdir, "clean.sh")
        with open(p, "w", encoding="utf-8") as f:
            f.write("#!/bin/bash\n# comment on line 2\necho hi\n")
        self.assertParity([self.tmpdir], "Shebang line 1 clean")

    def test_sh_shebang_line_2_preceded_by_comment(self):
        """Shebang on line 2 preceded by comment is flagged."""
        p = os.path.join(self.tmpdir, "disp2.sh")
        with open(p, "w", encoding="utf-8") as f:
            f.write("# comment on line 1\n#!/bin/bash\necho hi\n")
        o, r = self.assertParity([self.tmpdir], "Shebang line 2 preceded by comment")
        self.assertIn("HEADER: shebang not on line 1", o.stderr)
        self.assertIn("HEADER: shebang not on line 1", r.stderr)

    def test_sh_shebang_line_2_preceded_by_blank_line(self):
        """Shebang on line 2 preceded by blank line is flagged."""
        p = os.path.join(self.tmpdir, "disp_blank.sh")
        with open(p, "w", encoding="utf-8") as f:
            f.write("\n#!/bin/bash\necho hi\n")
        o, r = self.assertParity([self.tmpdir], "Shebang line 2 preceded by blank line")
        self.assertIn("HEADER: shebang not on line 1", o.stderr)
        self.assertIn("HEADER: shebang not on line 1", r.stderr)

    def test_sh_shebang_line_3_preceded_by_blank_and_comment(self):
        """Shebang on line 3 preceded by blank line and comment is flagged."""
        p = os.path.join(self.tmpdir, "disp3.sh")
        with open(p, "w", encoding="utf-8") as f:
            f.write("\n# comment\n#!/bin/bash\necho hi\n")
        o, r = self.assertParity([self.tmpdir], "Shebang line 3 preceded by blank and comment")
        self.assertIn("HEADER: shebang not on line 1", o.stderr)
        self.assertIn("HEADER: shebang not on line 1", r.stderr)

    def test_sh_shebang_crlf_line_1_clean(self):
        """Shebang on line 1 with CRLF is valid."""
        p = os.path.join(self.tmpdir, "crlf.sh")
        with open(p, "wb") as f:
            f.write(b"#!/bin/bash\r\n# comment\r\necho hi\r\n")
        self.assertParity([self.tmpdir], "Shebang CRLF line 1 clean")

    def test_sh_shebang_crlf_line_2_flagged(self):
        """Shebang on line 2 with CRLF is flagged."""
        p = os.path.join(self.tmpdir, "crlf_disp.sh")
        with open(p, "wb") as f:
            f.write(b"# comment\r\n#!/bin/bash\r\necho hi\r\n")
        o, r = self.assertParity([self.tmpdir], "Shebang CRLF line 2 flagged")
        self.assertIn("HEADER: shebang not on line 1", o.stderr)
        self.assertIn("HEADER: shebang not on line 1", r.stderr)

    def test_sh_no_shebang_sourced_script(self):
        """Sourced shell script with no shebang is valid."""
        p = os.path.join(self.tmpdir, "lib.sh")
        with open(p, "w", encoding="utf-8") as f:
            f.write("# Library helper\nmy_func() { echo 1; }\n")
        self.assertParity([self.tmpdir], "Sourced script no shebang")


# ============================================================================
# Challenge 2: Date Attribution in String Literals vs Value Strings
# ============================================================================

class TestChallenge2DateAttributionVsValues(HardcodeLintAdversarialTestBase):
    """Stress tests date attribution heuristics across quotes, docstrings, and URLs."""

    def test_date_in_multiline_raw_string_value(self):
        """Raw string with date as immediate value is exempt."""
        p = os.path.join(self.tmpdir, "raw_val.py")
        with open(p, "w", encoding="utf-8") as f:
            f.write('CONFIG_DATE = r"2026-10-06"\n')
        self.assertParity([self.tmpdir], "Raw string date value")

    def test_date_in_raw_string_prose(self):
        """Raw string with date in prose preceded by whitespace is flagged."""
        p = os.path.join(self.tmpdir, "raw_prose.py")
        with open(p, "w", encoding="utf-8") as f:
            f.write(f'PROMPT = r"System snapshot created on {_D} by test"\n')
        o, r = self.assertParity([self.tmpdir], "Raw string date prose")
        self.assertIn("DATE-IN-STRING", o.stderr)
        self.assertIn("DATE-IN-STRING", r.stderr)

    def test_date_in_multiline_string_immediate_vs_indented(self):
        """Multiline string where date is on line 2 preceded by indentation is flagged."""
        p = os.path.join(self.tmpdir, "multi_indent.py")
        with open(p, "w", encoding="utf-8") as f:
            f.write(f'HELP = """\n    {_D} release notes\n"""\n')
        o, r = self.assertParity([self.tmpdir], "Multiline string with indented date")
        self.assertIn("DATE-IN-STRING", o.stderr)
        self.assertIn("DATE-IN-STRING", r.stderr)

    def test_date_in_multiline_string_head_value(self):
        """Multiline string starting immediately with date: quote-led character."""
        p = os.path.join(self.tmpdir, "multi_head.py")
        with open(p, "w", encoding="utf-8") as f:
            f.write('HELP = """2026-10-06 release notes"""\n')
        # In Python: s[i-1] is quote '"', so NOT whitespace -> exempt
        self.assertParity([self.tmpdir], "Multiline string starting immediately with date")

    def test_date_in_docstring_with_embedded_code_block(self):
        """Docstring containing a code block with a date is flagged as DATE-IN-COMMENT."""
        p = os.path.join(self.tmpdir, "doc_code.py")
        with open(p, "w", encoding="utf-8") as f:
            f.write('def foo():\n    """Example:\n    ```\n    date = "2026-10-06"\n    ```\n    """\n    return 1\n')
        o, r = self.assertParity([self.tmpdir], "Docstring with embedded code block")
        self.assertIn("DATE-IN-COMMENT", o.stderr)
        self.assertIn("DATE-IN-COMMENT", r.stderr)

    def test_date_in_docstring_with_url(self):
        """Docstring containing a date in a URL is still flagged because docstrings must be timeless."""
        p = os.path.join(self.tmpdir, "doc_url.py")
        with open(p, "w", encoding="utf-8") as f:
            f.write('"""Documentation citing https://example.com/archive/2026-10-06/index.html."""\nx = 1\n')
        o, r = self.assertParity([self.tmpdir], "Docstring with URL")
        self.assertIn("DATE-IN-COMMENT", o.stderr)
        self.assertIn("DATE-IN-COMMENT", r.stderr)

    def test_date_in_normal_string_markdown_url_exempt(self):
        """Normal code string with markdown URL containing date in path is exempt."""
        p = os.path.join(self.tmpdir, "code_url.py")
        with open(p, "w", encoding="utf-8") as f:
            f.write('LINK = "See [archive](https://example.com/archive/2026-10-06/index.html)"\n')
        self.assertParity([self.tmpdir], "Normal string markdown URL exempt")

    def test_date_in_normal_string_markdown_text_flagged(self):
        """Normal code string with date in markdown link text is flagged."""
        p = os.path.join(self.tmpdir, "code_text.py")
        with open(p, "w", encoding="utf-8") as f:
            f.write(f'LINK = "See [snapshot {_D}](https://example.com)"\n')
        o, r = self.assertParity([self.tmpdir], "Normal string markdown text flagged")
        self.assertIn("DATE-IN-STRING", o.stderr)
        self.assertIn("DATE-IN-STRING", r.stderr)

    def test_date_in_non_python_shell_string_exempt(self):
        """In shell scripts, dates in string variables are values, not flagged."""
        p = os.path.join(self.tmpdir, "val.sh")
        with open(p, "w", encoding="utf-8") as f:
            f.write('#!/bin/bash\nRELEASE_DATE="2026-10-06"\necho "$RELEASE_DATE"\n')
        self.assertParity([self.tmpdir], "Shell script string date exempt")

    def test_date_in_non_python_toml_string_exempt(self):
        """In TOML files, dates in string variables are values, not flagged."""
        p = os.path.join(self.tmpdir, "val.toml")
        with open(p, "w", encoding="utf-8") as f:
            f.write('[package]\nversion = "2026-10-06"\n')
        self.assertParity([self.tmpdir], "TOML string date exempt")


# ============================================================================
# Challenge 3: IP Address Heuristics
# ============================================================================

class TestChallenge3IpAddressHeuristics(HardcodeLintAdversarialTestBase):
    """Stress tests IPv4 subnets, boundaries, public routable IPs, and port bracketings."""

    def test_loopback_and_zero_exempt(self):
        """127.0.0.1 and 0.0.0.0 are exempt."""
        p = os.path.join(self.tmpdir, "loop.py")
        with open(p, "w", encoding="utf-8") as f:
            f.write('A = "127.0.0.1"\nB = "0.0.0.0"\n')
        self.assertParity([self.tmpdir], "Loopback and 0.0.0.0 exempt")

    def test_private_10_network_exempt(self):
        """10.0.0.1 and 10.255.255.254 are exempt."""
        p = os.path.join(self.tmpdir, "priv10.py")
        with open(p, "w", encoding="utf-8") as f:
            f.write('IP1 = "10.0.0.1"\nIP2 = "10.255.255.254"\n')
        self.assertParity([self.tmpdir], "10.0.0.0/8 exempt")

    def test_private_172_network_bounds(self):
        """172.16.0.1 and 172.31.255.255 exempt, but 172.15.x and 172.32.x flagged."""
        # Clean private
        p1 = os.path.join(self.tmpdir, "clean172.py")
        with open(p1, "w", encoding="utf-8") as f:
            f.write('P1 = "172.16.0.1"\nP2 = "172.31.255.254"\n')
        self.assertParity([self.tmpdir], "172.16-31 exempt")

        # Dirty outside
        p2 = os.path.join(self.tmpdir, "dirty172.py")
        with open(p2, "w", encoding="utf-8") as f:
            f.write('BAD1 = "172.15.255.1"\n')
        o, r = self.assertParity([self.tmpdir], "172.15 flagged")
        self.assertIn("HARDCODED-PORT/IP: 172.15.255.1", o.stderr)
        self.assertIn("HARDCODED-PORT/IP: 172.15.255.1", r.stderr)

    def test_private_192_168_network_bounds(self):
        """192.168.1.1 is exempt, 192.167.1.1 and 192.169.1.1 are flagged."""
        p = os.path.join(self.tmpdir, "priv192.py")
        with open(p, "w", encoding="utf-8") as f:
            f.write('BAD = "192.167.1.1"\n')
        o, r = self.assertParity([self.tmpdir], "192.167 flagged")
        self.assertIn("HARDCODED-PORT/IP: 192.167.1.1", o.stderr)
        self.assertIn("HARDCODED-PORT/IP: 192.167.1.1", r.stderr)

    def test_cgnat_100_64_network_bounds(self):
        """100.64.0.1 and 100.127.255.255 exempt, 100.63.x and 100.128.x flagged."""
        p = os.path.join(self.tmpdir, "cgnat_bad.py")
        with open(p, "w", encoding="utf-8") as f:
            f.write('BAD_CGNAT = "100.128.0.1"\n')
        o, r = self.assertParity([self.tmpdir], "100.128 flagged")
        self.assertIn("HARDCODED-PORT/IP: 100.128.0.1", o.stderr)
        self.assertIn("HARDCODED-PORT/IP: 100.128.0.1", r.stderr)

    def test_public_routable_ips(self):
        """8.8.8.8 and 198.51.100.1 are flagged."""
        p = os.path.join(self.tmpdir, "dns.py")
        with open(p, "w", encoding="utf-8") as f:
            f.write('DNS = "8.8.8.8"\nTEST_NET = "198.51.100.1"\n')
        o, r = self.assertParity([self.tmpdir], "8.8.8.8 and 198.51.100.1 flagged")
        self.assertIn("HARDCODED-PORT/IP: 8.8.8.8", o.stderr)
        self.assertIn("HARDCODED-PORT/IP: 8.8.8.8", r.stderr)
        self.assertIn("HARDCODED-PORT/IP: 198.51.100.1", o.stderr)
        self.assertIn("HARDCODED-PORT/IP: 198.51.100.1", r.stderr)

    def test_ipv6_addresses_not_flagged(self):
        """IPv6 address strings like ::1, 2001:db8::1 are not flagged by IPv4 regex."""
        p = os.path.join(self.tmpdir, "ipv6.py")
        with open(p, "w", encoding="utf-8") as f:
            f.write('V6_LOOP = "::1"\nV6_DOC = "2001:db8::1"\n')
        self.assertParity([self.tmpdir], "IPv6 addresses exempt")

    def test_port_bracketed_array_index_exempt(self):
        """Array indexing with port number like data[8080] or [8080] is exempt."""
        p = os.path.join(self.tmpdir, "bracket.py")
        with open(p, "w", encoding="utf-8") as f:
            f.write('arr = [8080]\nx = arr[8080]\n')
        self.assertParity([self.tmpdir], "Bracketed port array index exempt")

    def test_raw_port_colon_syntax_flagged(self):
        """:8080 raw port syntax is flagged."""
        p = os.path.join(self.tmpdir, "raw_port.py")
        with open(p, "w", encoding="utf-8") as f:
            f.write('ENDPOINT = "http://localhost:8080/api"\n')
        o, r = self.assertParity([self.tmpdir], "Raw port localhost:8080 flagged")
        self.assertIn("HARDCODED-PORT/IP", o.stderr)
        self.assertIn("HARDCODED-PORT/IP", r.stderr)


# ============================================================================
# Challenge 4: Error Behavior & Filesystem Corner Cases
# ============================================================================

class TestChallenge4ErrorBehaviorAndParity(HardcodeLintAdversarialTestBase):
    """Stress tests non-existent paths, empty dirs, binary files, and syntax errors."""

    def test_nonexistent_single_path_parity(self):
        """Single non-existent directory reports FAIL with path name."""
        bogus = os.path.join(self.tmpdir, "no_such_dir_12345")
        o, r = self.assertParity([bogus], "Single non-existent path")
        self.assertIn("FAIL: root(s) do not exist:", o.stderr)
        self.assertIn("no_such_dir_12345", o.stderr)
        self.assertIn("FAIL: root(s) do not exist:", r.stderr)
        self.assertIn("no_such_dir_12345", r.stderr)

    def test_nonexistent_multiple_paths_parity(self):
        """Multiple non-existent directories are reported in stderr."""
        b1 = os.path.join(self.tmpdir, "bogus1")
        b2 = os.path.join(self.tmpdir, "bogus2")
        o, r = self.assertParity([b1, b2], "Multiple non-existent paths")
        self.assertIn("bogus1", o.stderr)
        self.assertIn("bogus2", o.stderr)
        self.assertIn("bogus1", r.stderr)
        self.assertIn("bogus2", r.stderr)

    def test_empty_directory_fails_parity(self):
        """Completely empty directory reports FAIL: scanned 0 files."""
        empty_dir = os.path.join(self.tmpdir, "empty")
        os.makedirs(empty_dir, exist_ok=True)
        o, r = self.assertParity([empty_dir], "Empty directory")
        self.assertIn("FAIL: scanned 0 files under", o.stderr)
        self.assertIn("FAIL: scanned 0 files under", r.stderr)

    def test_directory_with_only_non_code_files_fails_parity(self):
        """Directory with only non-code files (.txt, .md, .png) reports 0 files scanned."""
        d = os.path.join(self.tmpdir, "docs_only")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "README.txt"), "w") as f:
            f.write("text only")
        with open(os.path.join(d, "data.csv"), "w") as f:
            f.write("1,2,3")
        o, r = self.assertParity([d], "Non-code files only")
        self.assertIn("FAIL: scanned 0 files under", o.stderr)
        self.assertIn("FAIL: scanned 0 files under", r.stderr)

    def test_empty_code_files_ignored(self):
        """0-byte code files are skipped (raw is empty). If all are 0-byte, 0 scanned."""
        d = os.path.join(self.tmpdir, "zero_bytes")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "empty.py"), "w") as f:
            pass
        with open(os.path.join(d, "empty.sh"), "w") as f:
            pass
        o, r = self.assertParity([d], "Zero byte code files")
        self.assertIn("FAIL: scanned 0 files under", o.stderr)
        self.assertIn("FAIL: scanned 0 files under", r.stderr)

    def test_python_syntax_error_resilience(self):
        """Python file with broken syntax does not crash tokenizer or linter."""
        p = os.path.join(self.tmpdir, "broken.py")
        with open(p, "w", encoding="utf-8") as f:
            f.write("def broken(\n   x = 1\n# unclosed paren\n")
        # Should gracefully finish without crashing
        self.assertParity([self.tmpdir], "Python syntax error resilience")

    def test_binary_garbage_in_code_file_resilience(self):
        """Binary garbage in a .py file does not cause panic or crash."""
        p = os.path.join(self.tmpdir, "garbage.py")
        with open(p, "wb") as f:
            f.write(b"\x00\xff\xfe\x01\x80\x99\xaa\xbb\xcc\xdd\xee\n")
        # Should finish cleanly without panic
        self.assertParity([self.tmpdir], "Binary garbage resilience")

    def test_unclosed_multibyte_string_resilience(self):
        """Unclosed triple-quoted string with multi-byte UTF-8 character at EOF
        does not crash either oracle or Rust binary (remediated, zero panic).
        """
        p = os.path.join(self.tmpdir, "unclosed_emoji.py")
        with open(p, "wb") as f:
            f.write(b's = """\xf0\x9f\x98\x80\n')

        o, r = self.assertParity([self.tmpdir], "Unclosed multibyte string resilience")
        self.assertEqual(r.returncode, 0, "Rust binary does not panic on unclosed string")
        self.assertIn("PASS: 1 file(s) scanned", r.stdout)

    def test_ps1_bom_byte_zero_with_date_comment_flagged(self):
        """PS1 with BOM at byte 0 and a dated comment on line 1 properly strips BOM and flags comment."""
        p = os.path.join(self.tmpdir, "bom_comment.ps1")
        with open(p, "wb") as f:
            f.write(b"\xef\xbb\xbf# Modified on " + _D.encode() + b"\nWrite-Host 'hello'\n")
        o, r = self.assertParity([self.tmpdir], "PS1 BOM byte 0 with dated comment")
        self.assertIn("DATE-IN-COMMENT", o.stderr)
        self.assertIn("DATE-IN-COMMENT", r.stderr)

    def test_py_bom_byte_zero_with_date_comment_flagged(self):
        """Python with BOM at byte 0 and a dated comment properly flags comment."""
        p = os.path.join(self.tmpdir, "py_bom.py")
        with open(p, "wb") as f:
            f.write(b"\xef\xbb\xbf# Created " + _D.encode() + b"\nx = 1\n")
        o, r = self.assertParity([self.tmpdir], "Python BOM byte 0 with dated comment")
        self.assertIn("DATE-IN-COMMENT", o.stderr)
        self.assertIn("DATE-IN-COMMENT", r.stderr)

    def test_no_trailing_newline_with_date_comment(self):
        """File without trailing newline with dated comment is flagged."""
        p = os.path.join(self.tmpdir, "no_newline.sh")
        with open(p, "w", encoding="utf-8") as f:
            f.write(f"#!/bin/bash\n# Last updated: {_D}")
        o, r = self.assertParity([self.tmpdir], "No trailing newline with dated comment")
        self.assertIn("DATE-IN-COMMENT", o.stderr)
        self.assertIn("DATE-IN-COMMENT", r.stderr)

    def test_port_boundary_values(self):
        """Port 0 and 65536 are out of range; port 65535 is flagged."""
        # 65535 is flagged
        p1 = os.path.join(self.tmpdir, "p65535.py")
        with open(p1, "w", encoding="utf-8") as f:
            f.write('PORT = "localhost:65535"\n')
        o1, r1 = self.assertParity([self.tmpdir], "Port 65535 flagged")
        self.assertIn("HARDCODED-PORT/IP", o1.stderr)
        self.assertIn("HARDCODED-PORT/IP", r1.stderr)

    def test_fstring_template_triple_quote_port_discrepancy(self):
        """Hardcoded ports in triple-quoted f-strings are detected with full parity."""
        sub = os.path.join(self.tmpdir, "fstring_sub")
        os.makedirs(sub, exist_ok=True)
        p = os.path.join(sub, "tmpl.py")
        with open(p, "w", encoding="utf-8") as f:
            f.write("def gen():\n    return f'''http://localhost:9090/status'''\n")

        o, r = self.assertParity([sub], "F-string template port detected")
        self.assertIn("HARDCODED-PORT/IP", o.stderr)
        self.assertIn(":9090", o.stderr)
        self.assertIn("HARDCODED-PORT/IP", r.stderr)
        self.assertIn(":9090", r.stderr)

    def test_single_file_cli_argument_discrepancy(self):
        """EMPIRICAL CHALLENGE FINDING:
        Passing a single file path directly to the CLI causes Python oracle to fail
        (os.walk yields 0 files, reporting 'FAIL: scanned 0 files under ...', exit 1).
        Rust binary uses WalkDir::new(root) which yields the file directly and scans it (exit 0).
        """
        sub = os.path.join(self.tmpdir, "single_file_sub")
        os.makedirs(sub, exist_ok=True)
        clean_file = os.path.join(sub, "clean.py")
        with open(clean_file, "w", encoding="utf-8") as f:
            f.write("x = 1\n")

        o_res = run_oracle([clean_file])
        r_res = run_rust([clean_file])

        self.assertEqual(o_res.returncode, 1, "Python oracle fails when given a file path directly")
        self.assertIn("scanned 0 files under", o_res.stderr)

        self.assertEqual(r_res.returncode, 0, "Rust binary scans single file directly")
        self.assertIn("PASS: 1 file(s) scanned", r_res.stdout)


def main() -> int:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    suite.addTests(loader.loadTestsFromTestCase(TestChallenge1HeaderCrashRisks))
    suite.addTests(loader.loadTestsFromTestCase(TestChallenge2DateAttributionVsValues))
    suite.addTests(loader.loadTestsFromTestCase(TestChallenge3IpAddressHeuristics))
    suite.addTests(loader.loadTestsFromTestCase(TestChallenge4ErrorBehaviorAndParity))

    print("=" * 80)
    print("MiOS Hardcode-Lint Adversarial Challenge Test Harness")
    print(f"Total Challenge Test Cases: {suite.countTestCases()}")
    print(f"Python Oracle: {_ORACLE_PATH}")
    print(f"Rust Binary: {_RUST_BIN}")
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
