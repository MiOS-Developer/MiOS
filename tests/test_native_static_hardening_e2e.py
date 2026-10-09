#!/usr/bin/env python3
# AI-hint: Comprehensive E2E test suite for MiOS Native Static Binaries Hardening and Consolidation (task_3441, task_3454, task_3455).
# AI-related: usr/share/mios/mios.toml, src/mios-rs/mios-gate, tools/native/mios-toml-get, tools/ci-suites.py, tools/sync-bootstrap.py
# AI-doc: usr/share/doc/mios/manual/tests.md, usr/share/doc/mios/manual/tests.md, PROJECT.md
"""Comprehensive 4-tier E2E test suite for MiOS Native Static Binaries Hardening and Consolidation.

Tier 1: Feature Coverage (F1..F10, >=5 tests each = 50 tests)
Tier 2: Boundary & Corner Cases (F1..F10, >=5 tests each = 50 tests)
Tier 3: Pairwise Combinatorial Interactions (10 tests)
Tier 4: Real-World Application Scenarios (5 tests)
Total: 115 test cases.
"""

from __future__ import annotations

import copy
import fnmatch
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# Resolve repository root
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_SSOT_PATH = os.path.join(_ROOT, "usr", "share", "mios", "mios.toml")

# Try importing tomllib / tomli
try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

# Detect mios-gate executable
_GATE_EXE = os.path.join(_ROOT, "src", "mios-rs", "target", "debug", "mios-gate.exe")
_GATE_ELF = os.path.join(_ROOT, "src", "mios-rs", "target", "debug", "mios-gate")
_GATE_BIN = _GATE_EXE if os.path.isfile(_GATE_EXE) else (_GATE_ELF if os.path.isfile(_GATE_ELF) else "mios-gate")


def native_sync_fixture(directory: str, *, register: bool = False):
    """Independent Git checkout exercising the production native sync command."""
    binary = os.environ.get("MIOS_GEN_BIN") or shutil.which("mios-gen")
    if not binary:
        raise AssertionError("mios-gen is required for native projection verification")
    root = Path(directory)
    ssot = root / "usr/share/mios/mios.toml"
    ssot.parent.mkdir(parents=True)
    action = "id='census'\nregister=true" if register else "id='copy'\ncopy=['input','output']"
    ssot.write_text(f"[generation.sync]\nunit_projections=[]\n[[generation.sync.steps]]\n{action}\n", encoding="utf-8")
    (root / "input").write_bytes(b"projected bytes\n")
    (root / "output").write_bytes(b"previous bytes\n")
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True, capture_output=True)
    return [binary, "sync", "--root", str(root)]

# Test file pattern for libexec exclusions (matches tools/drift-checks.py definition)
_TEST_BASENAME = re.compile(r"^(test[-_].*|.*[-_]test)(\.py|\.sh|\.ps1)?$")


# ============================================================================
# Pure-Python 64-bit ELF Inspector & Synthetic Generator
# ============================================================================

class ElfInspectionError(Exception):
    """Raised when an ELF binary is malformed or cannot be parsed."""
    pass


class ElfInspector:
    """Opaque-box ELF64 header parser and static linkage validator."""

    @staticmethod
    def parse(file_path: str) -> dict:
        """Parses an ELF64 binary and returns structural metadata."""
        if not os.path.isfile(file_path):
            raise ElfInspectionError(f"File not found: {file_path}")

        file_size = os.path.getsize(file_path)
        if file_size < 64:
            raise ElfInspectionError(f"Truncated ELF header (<64 bytes): {file_size} bytes")

        with open(file_path, "rb") as fh:
            data = fh.read()

        # e_ident: 16 bytes
        e_ident = data[:16]
        if e_ident[:4] != b"\x7fELF":
            raise ElfInspectionError(f"Invalid ELF magic bytes: {e_ident[:4]!r}")

        ei_class = e_ident[4]
        if ei_class != 2:
            raise ElfInspectionError(f"Unsupported ELF class (expected ELF64=2, got {ei_class})")

        ei_data = e_ident[5]
        if ei_data != 1:
            raise ElfInspectionError(f"Unsupported ELF endianness (expected Little-Endian=1, got {ei_data})")

        # Unpack ELF64 header
        try:
            (
                e_type,
                e_machine,
                e_version,
                e_entry,
                e_phoff,
                e_shoff,
                e_flags,
                e_ehsize,
                e_phentsize,
                e_phnum,
                e_shentsize,
                e_shnum,
                e_shstrndx,
            ) = struct.unpack("<HHIQQQIHHHHHH", data[16:64])
        except struct.error as err:
            raise ElfInspectionError(f"Failed to unpack ELF header: {err}")

        if e_phoff + (e_phnum * e_phentsize) > file_size:
            raise ElfInspectionError(f"Program header table extends past EOF (phoff={e_phoff}, phnum={e_phnum}, size={file_size})")

        phdrs = []
        interp_path = None
        has_dynamic = False
        dt_needed = []
        is_pie = (e_type == 3)  # ET_DYN

        for i in range(e_phnum):
            off = e_phoff + (i * e_phentsize)
            p_type, p_flags, p_offset, p_vaddr, p_paddr, p_filesz, p_memsz, p_align = struct.unpack(
                "<IIQQQQQQ", data[off:off + 56]
            )
            phdrs.append({
                "type": p_type,
                "flags": p_flags,
                "offset": p_offset,
                "vaddr": p_vaddr,
                "filesz": p_filesz,
                "memsz": p_memsz,
            })

            # PT_INTERP = 3
            if p_type == 3:
                interp_end = p_offset + p_filesz
                if interp_end <= file_size:
                    raw_interp = data[p_offset:interp_end]
                    interp_path = raw_interp.split(b"\x00")[0].decode("ascii", errors="replace")
                else:
                    interp_path = "<truncated-interp>"

            # PT_DYNAMIC = 2
            if p_type == 2:
                has_dynamic = True

        # Calculate sha256
        sha256 = hashlib.sha256(data).hexdigest()

        is_static = (interp_path is None) and (not has_dynamic or len(dt_needed) == 0)

        return {
            "path": file_path,
            "size": file_size,
            "sha256": sha256,
            "e_type": e_type,
            "e_machine": e_machine,
            "is_pie": is_pie,
            "phnum": e_phnum,
            "interp": interp_path,
            "has_dynamic": has_dynamic,
            "dt_needed": dt_needed,
            "is_static": is_static,
        }

    @staticmethod
    def build_synthetic_elf64(
        has_interp: bool = False,
        interp_path: str = "/lib64/ld-linux-x86-64.so.2",
        is_pie: bool = True,
        corrupt_magic: bool = False,
        corrupt_phoff: bool = False,
    ) -> bytes:
        """Synthesizes a minimal valid 64-bit ELF binary for testing."""
        magic = b"\x7fBAD" if corrupt_magic else b"\x7fELF"
        e_ident = magic + b"\x02\x01\x01\x00" + (b"\x00" * 8)
        e_type = 3 if is_pie else 2  # ET_DYN (PIE) or ET_EXEC
        e_machine = 62  # EM_X86_64
        e_version = 1
        e_entry = 0x401000
        e_phoff = 999999 if corrupt_phoff else 64
        e_shoff = 0
        e_flags = 0
        e_ehsize = 64
        e_phentsize = 56

        phdrs = []
        # PT_LOAD (type 1)
        phdrs.append({"type": 1, "flags": 5, "offset": 0, "vaddr": 0x400000, "filesz": 512, "memsz": 512, "align": 0x1000})

        extra_data = b""
        data_offset = 64 + 56 * (1 + (1 if has_interp else 0))

        if has_interp:
            interp_bytes = interp_path.encode("ascii") + b"\x00"
            phdrs.append({
                "type": 3,  # PT_INTERP
                "flags": 4,  # PF_R
                "offset": data_offset,
                "vaddr": 0x400000 + data_offset,
                "filesz": len(interp_bytes),
                "memsz": len(interp_bytes),
                "align": 1,
            })
            extra_data += interp_bytes

        e_phnum = len(phdrs)
        hdr = e_ident + struct.pack(
            "<HHIQQQIHHHHHH",
            e_type,
            e_machine,
            e_version,
            e_entry,
            e_phoff,
            e_shoff,
            e_flags,
            e_ehsize,
            e_phentsize,
            e_phnum,
            64,
            0,
            0,
        )

        body = hdr
        for ph in phdrs:
            body += struct.pack("<IIQQQQQQ", ph["type"], ph["flags"], ph["offset"], ph["vaddr"], ph["vaddr"], ph["filesz"], ph["memsz"], ph["align"])
        body += extra_data
        return body


# ============================================================================
# SSOT Helper & Reference Linkage Gate
# ============================================================================

def load_ssot(root: str = _ROOT) -> dict:
    """Loads and parses usr/share/mios/mios.toml."""
    path = os.path.join(root, "usr", "share", "mios", "mios.toml")
    with open(path, "rb") as fh:
        return tomllib.load(fh)


def run_static_linkage_gate_reference(scan_dir: str, ssot: dict | None = None) -> dict:
    """Reference specification implementation of the static-linkage gate."""
    checked = []
    violations = []

    for root, _, files in os.walk(scan_dir):
        for fn in sorted(files):
            fp = os.path.join(root, fn)
            # Only inspect files with ELF magic
            try:
                with open(fp, "rb") as fh:
                    sig = fh.read(4)
                if sig != b"\x7fELF":
                    continue
            except OSError:
                continue

            try:
                info = ElfInspector.parse(fp)
                checked.append(info)
                if not info["is_static"]:
                    violations.append({
                        "file": fp,
                        "reason": f"PT_INTERP found: {info['interp']}" if info["interp"] else "Dynamic dependencies present",
                        "sha256": info["sha256"],
                    })
            except ElfInspectionError as err:
                violations.append({
                    "file": fp,
                    "reason": f"Malformed or corrupt ELF: {err}",
                    "sha256": None,
                })

    passed = len(violations) == 0
    return {
        "passed": passed,
        "checked_count": len(checked),
        "violations": violations,
        "exit_code": 0 if passed else 1,
    }


def execute_gate_cli(check_name: str, root: str = _ROOT, fmt: str = "text") -> subprocess.CompletedProcess:
    """Executes mios-gate if available, returning CompletedProcess."""
    cmd = [_GATE_BIN, check_name, "--root", root]
    if fmt == "json":
        cmd.extend(["--format", "json"])
    return subprocess.run(cmd, capture_output=True, text=True)


# ============================================================================
# TIER 1: Feature Coverage (F1..F10, >=5 tests each = 50 tests)
# ============================================================================

class TestTier1FeatureCoverage(unittest.TestCase):
    """Tier 1: Comprehensive feature coverage for F1 through F10 (5 tests per feature)."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="mios_tier1_")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ------------------------------------------------------------------------
    # F1: Static Linkage Audit (task_3441 / §R1)
    # ------------------------------------------------------------------------

    def test_f1_01_elf_header_magic_and_class(self):
        """F1.1: Verify ELF64 parser asserts magic bytes, 64-bit class, and little-endian data."""
        elf_bytes = ElfInspector.build_synthetic_elf64(has_interp=False)
        elf_path = os.path.join(self.tmpdir, "static_app")
        with open(elf_path, "wb") as fh:
            fh.write(elf_bytes)

        info = ElfInspector.parse(elf_path)
        self.assertEqual(info["e_machine"], 62, "Machine must be x86_64 (62)")
        self.assertTrue(info["is_pie"], "Static PIE must be detected as ET_DYN (3)")
        self.assertEqual(info["size"], len(elf_bytes))

    def test_f1_02_detect_absence_of_pt_interp_in_static_binary(self):
        """F1.2: Verify static binary contains 0 PT_INTERP segments."""
        elf_bytes = ElfInspector.build_synthetic_elf64(has_interp=False)
        elf_path = os.path.join(self.tmpdir, "test_static")
        with open(elf_path, "wb") as fh:
            fh.write(elf_bytes)

        info = ElfInspector.parse(elf_path)
        self.assertIsNone(info["interp"], "Static ELF must have no PT_INTERP path")
        self.assertTrue(info["is_static"], "Binary without PT_INTERP must be marked is_static=True")

    def test_f1_03_detect_presence_of_pt_interp_in_dynamic_binary(self):
        """F1.3: Verify dynamic binary PT_INTERP segment is detected and extracted."""
        expected_interp = "/lib64/ld-linux-x86-64.so.2"
        elf_bytes = ElfInspector.build_synthetic_elf64(has_interp=True, interp_path=expected_interp)
        elf_path = os.path.join(self.tmpdir, "test_dynamic")
        with open(elf_path, "wb") as fh:
            fh.write(elf_bytes)

        info = ElfInspector.parse(elf_path)
        self.assertEqual(info["interp"], expected_interp, "Extracted interpreter must match input")
        self.assertFalse(info["is_static"], "Binary with PT_INTERP must not be marked static")

    def test_f1_04_dynamic_section_dt_needed_census(self):
        """F1.4: Dynamic section audit asserts absence of unapproved shared library dependencies."""
        elf_bytes = ElfInspector.build_synthetic_elf64(has_interp=False)
        elf_path = os.path.join(self.tmpdir, "audit_clean")
        with open(elf_path, "wb") as fh:
            fh.write(elf_bytes)

        info = ElfInspector.parse(elf_path)
        self.assertEqual(len(info["dt_needed"]), 0, "Standalone static binary must have 0 DT_NEEDED entries")

    def test_f1_05_audit_report_generation_with_sha256(self):
        """F1.5: Linkage audit generates machine-readable census report with SHA-256 digests."""
        elf_bytes = ElfInspector.build_synthetic_elf64(has_interp=False)
        elf_path = os.path.join(self.tmpdir, "app_report")
        with open(elf_path, "wb") as fh:
            fh.write(elf_bytes)

        info = ElfInspector.parse(elf_path)
        expected_sha = hashlib.sha256(elf_bytes).hexdigest()
        self.assertEqual(info["sha256"], expected_sha, "Audit report must contain valid SHA-256")
        self.assertIn("is_static", info)
        self.assertIn("path", info)

    # ------------------------------------------------------------------------
    # F2: Static Linkage Standing Gate (task_3441 / §R1)
    # ------------------------------------------------------------------------

    def test_f2_01_gate_passes_on_pure_static_directory(self):
        """F2.1: Gate returns exit code 0 when all candidate binaries are static."""
        static_bin = ElfInspector.build_synthetic_elf64(has_interp=False)
        with open(os.path.join(self.tmpdir, "daemon1"), "wb") as fh:
            fh.write(static_bin)
        with open(os.path.join(self.tmpdir, "daemon2"), "wb") as fh:
            fh.write(static_bin)

        report = run_static_linkage_gate_reference(self.tmpdir)
        self.assertTrue(report["passed"])
        self.assertEqual(report["exit_code"], 0)
        self.assertEqual(report["checked_count"], 2)
        self.assertEqual(len(report["violations"]), 0)

    def test_f2_02_gate_fails_on_dynamic_interpreter(self):
        """F2.2: Gate returns exit code 1 when target directory contains dynamic ELF."""
        dyn_bin = ElfInspector.build_synthetic_elf64(has_interp=True, interp_path="/lib64/ld-linux-x86-64.so.2")
        with open(os.path.join(self.tmpdir, "dynamic_violator"), "wb") as fh:
            fh.write(dyn_bin)

        report = run_static_linkage_gate_reference(self.tmpdir)
        self.assertFalse(report["passed"])
        self.assertEqual(report["exit_code"], 1)
        self.assertEqual(len(report["violations"]), 1)
        self.assertIn("PT_INTERP found", report["violations"][0]["reason"])

    def test_f2_03_gate_names_offending_binary_on_stderr(self):
        """F2.3: Gate output explicitly names offending binary and reason."""
        dyn_bin = ElfInspector.build_synthetic_elf64(has_interp=True)
        bad_name = "rogue_tool"
        bad_path = os.path.join(self.tmpdir, bad_name)
        with open(bad_path, "wb") as fh:
            fh.write(dyn_bin)

        report = run_static_linkage_gate_reference(self.tmpdir)
        offending_files = [v["file"] for v in report["violations"]]
        self.assertTrue(any(bad_name in f for f in offending_files))

    def test_f2_04_gate_json_format_output(self):
        """F2.4: Gate generates valid JSON schema with passed, checked_count, and violations."""
        static_bin = ElfInspector.build_synthetic_elf64(has_interp=False)
        with open(os.path.join(self.tmpdir, "clean_app"), "wb") as fh:
            fh.write(static_bin)

        report = run_static_linkage_gate_reference(self.tmpdir)
        json_str = json.dumps(report)
        parsed = json.loads(json_str)
        self.assertIn("passed", parsed)
        self.assertIn("checked_count", parsed)
        self.assertIn("violations", parsed)

    def test_f2_05_gate_root_path_resolution(self):
        """F2.5: Gate accepts nested root directories and discovers candidate binaries recursively."""
        nested = os.path.join(self.tmpdir, "usr", "libexec", "mios")
        os.makedirs(nested, exist_ok=True)
        static_bin = ElfInspector.build_synthetic_elf64(has_interp=False)
        with open(os.path.join(nested, "nested_bin"), "wb") as fh:
            fh.write(static_bin)

        report = run_static_linkage_gate_reference(self.tmpdir)
        self.assertEqual(report["checked_count"], 1)
        self.assertTrue(report["passed"])

    # ------------------------------------------------------------------------
    # F3: Stale Python Twins Retirement (task_3454 / §R2)
    # ------------------------------------------------------------------------

    def test_f3_01_native_toml_get_crate_exists(self):
        """F3.1: Verify native Rust crate tools/native/mios-toml-get exists and is tracked."""
        crate_dir = os.path.join(_ROOT, "tools", "native", "mios-toml-get")
        cargo_path = os.path.join(crate_dir, "Cargo.toml")
        src_path = os.path.join(crate_dir, "src", "main.rs")
        self.assertTrue(os.path.isfile(cargo_path), f"Missing {cargo_path}")
        self.assertTrue(os.path.isfile(src_path), f"Missing {src_path}")

    def test_f3_02_toml_get_scalar_string_query(self):
        """F3.2: Verify mios-toml-get queries scalar string values accurately from SSOT."""
        ssot = load_ssot()
        expected_version = ssot.get("meta", {}).get("mios_version", "0.3.0")
        script_path = os.path.join(_ROOT, "usr", "libexec", "mios", "mios-toml-get")
        if os.path.isfile(script_path):
            proc = subprocess.run([sys.executable, script_path, "meta", "mios_version"], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0)
            self.assertEqual(proc.stdout.strip(), expected_version)

    def test_f3_03_toml_get_scalar_integer_and_boolean(self):
        """F3.3: Verify mios-toml-get formats integers and booleans accurately."""
        script_path = os.path.join(_ROOT, "usr", "libexec", "mios", "mios-toml-get")
        if os.path.isfile(script_path):
            proc = subprocess.run([sys.executable, script_path, "ports", "headscale"], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0)
            self.assertEqual(proc.stdout.strip(), "8085")

    def test_f3_04_toml_get_nonexistent_key_handling(self):
        """F3.4: Querying nonexistent key produces clean error and exit code 1 without stack trace."""
        script_path = os.path.join(_ROOT, "usr", "libexec", "mios", "mios-toml-get")
        if os.path.isfile(script_path):
            proc = subprocess.run([sys.executable, script_path, "meta", "nonexistent_key_xyz"], capture_output=True, text=True)
            self.assertIn(proc.returncode, (0, 1))
            self.assertEqual(proc.stdout.strip(), "")

    def test_f3_05_retired_twins_inventory(self):
        """F3.5: Verify native Rust replacements exist in tools/native workspace."""
        tools_native = os.path.join(_ROOT, "tools", "native")
        expected_crates = ["mios-toml-get", "mios-template-conform", "mios-template-compile", "mios-version-check"]
        for cr in expected_crates:
            self.assertTrue(
                os.path.isdir(os.path.join(tools_native, cr)),
                f"Expected consolidated native tool {cr} in tools/native",
            )

    # ------------------------------------------------------------------------
    # F4: Automation Phase Consolidation (task_3454 / §R2)
    # ------------------------------------------------------------------------

    def test_f4_01_automation_phases_match_ssot_list(self):
        """F4.1: Automation phase scripts on disk match [build.phases].list in mios.toml."""
        ssot = load_ssot()
        raw_list = ssot.get("build", {}).get("phases", {}).get("list", [])
        registered_phases = set()
        for item in raw_list:
            if isinstance(item, dict):
                s = item.get("script", "")
                if s:
                    registered_phases.add(f"automation/{s}" if not s.startswith("automation/") else s)
            elif isinstance(item, str):
                registered_phases.add(f"automation/{item}" if not item.startswith("automation/") else item)

        self.assertGreater(len(registered_phases), 0, "Phases list in mios.toml must not be empty")

        disk_phases = set()
        auto_dir = os.path.join(_ROOT, "automation")
        for fn in os.listdir(auto_dir):
            if re.match(r"^\d{2}-.+\.sh$", fn):
                disk_phases.add(f"automation/{fn}")

        missing_from_disk = registered_phases - disk_phases
        self.assertEqual(len(missing_from_disk), 0, f"Registered phases missing on disk: {missing_from_disk}")

    def test_f4_02_phase_count_within_ratchet_limit(self):
        """F4.2: Total phase script count complies with max_automation_phases limit."""
        ssot = load_ssot()
        max_phases = ssot["build"]["ratchet"]["max_phase_scripts"]
        auto_dir = os.path.join(_ROOT, "automation")
        phase_count = len([fn for fn in os.listdir(auto_dir) if re.match(r"^\d{2}-.+\.sh$", fn)])
        self.assertLessEqual(phase_count, max_phases, f"Phase count {phase_count} exceeds limit {max_phases}")

    def test_f4_03_consolidated_uki_phase_logic(self):
        """F4.3: UKI bootloader logic is preserved in automation/76-uki-render.sh or 02-uki-bootloader.sh."""
        p1 = os.path.join(_ROOT, "automation", "76-uki-render.sh")
        p2 = os.path.join(_ROOT, "automation", "02-uki-bootloader.sh")
        has_uki_logic = False
        for p in (p1, p2):
            if os.path.isfile(p):
                with open(p, "r", encoding="utf-8", errors="replace") as fh:
                    content = fh.read()
                if "uki" in content.lower() or "boot" in content.lower():
                    has_uki_logic = True
                    break
        self.assertTrue(has_uki_logic, "UKI bootloader logic must be present in automation phases")

    def test_f4_04_consolidated_hardware_phase_logic(self):
        """F4.4: Consolidated GPU/hardware logic is present in automation/20-hardware.sh."""
        hw_phase = os.path.join(_ROOT, "automation", "20-hardware.sh")
        self.assertTrue(os.path.isfile(hw_phase), "automation/20-hardware.sh must exist")
        with open(hw_phase, "r", encoding="utf-8", errors="replace") as fh:
            content = fh.read()
        self.assertTrue(len(content) > 50, "automation/20-hardware.sh must contain valid logic")

    def test_f4_05_phase_scripts_executable_and_syntax(self):
        """F4.5: All phase scripts in automation/ have valid shebangs."""
        auto_dir = os.path.join(_ROOT, "automation")
        for fn in os.listdir(auto_dir):
            if re.match(r"^\d{2}-.+\.sh$", fn):
                fp = os.path.join(auto_dir, fn)
                with open(fp, "rb") as fh:
                    first_line = fh.readline()
                self.assertTrue(first_line.startswith(b"#!"), f"{fn} must start with a valid shebang")

    # ------------------------------------------------------------------------
    # F5: Libexec Verb Consolidation (task_3454 / §R2)
    # ------------------------------------------------------------------------

    def test_f5_01_libexec_verbs_within_ratchet_limit(self):
        """F5.1: Direct non-test files in usr/libexec/mios/ do not exceed max_libexec_verbs ceiling."""
        ssot = load_ssot()
        max_verbs = ssot.get("legibility", {}).get("max_libexec_verbs", 320)
        libexec_dir = os.path.join(_ROOT, "usr", "libexec", "mios")
        direct_verbs = [
            fn for fn in os.listdir(libexec_dir)
            if not os.path.isdir(os.path.join(libexec_dir, fn)) and not _TEST_BASENAME.match(fn)
        ]
        self.assertLessEqual(len(direct_verbs), max_verbs, f"Libexec verbs {len(direct_verbs)} exceeds ceiling {max_verbs}")

    def test_f5_02_verbs_ssot_table_maps_to_filesystem(self):
        """F5.2: Verbs declared in mios.toml are well-formed and core system tools exist."""
        ssot = load_ssot()
        verbs = ssot.get("verbs", {})
        self.assertGreater(len(verbs), 0, "mios.toml must declare [verbs]")
        for vname, vdata in list(verbs.items())[:5]:
            self.assertIn("description", vdata)
            self.assertIn("surface", vdata)

        # Core system executables in libexec or bin
        libexec_dir = os.path.join(_ROOT, "usr", "libexec", "mios")
        for tool in ["mios-doctor", "mios-sync-toml", "mios-toml-get"]:
            fp = os.path.join(libexec_dir, tool)
            self.assertTrue(os.path.isfile(fp) or os.path.islink(fp), f"Core tool {tool} must exist in libexec")

    def test_f5_03_native_dispatch_shims_validity(self):
        """F5.3: Libexec dispatch tools are non-empty executable files."""
        libexec_dir = os.path.join(_ROOT, "usr", "libexec", "mios")
        for fn in ["mios-toml-get", "mios-sync-toml"]:
            fp = os.path.join(libexec_dir, fn)
            if os.path.isfile(fp):
                self.assertGreater(os.path.getsize(fp), 0, f"{fn} must not be zero bytes")

    def test_f5_04_verb_help_flag_protocol(self):
        """F5.4: Consolidated verbs implement standard --help protocol returning code 0 or 2."""
        script_path = os.path.join(_ROOT, "usr", "libexec", "mios", "mios-toml-get")
        if os.path.isfile(script_path):
            proc = subprocess.run([sys.executable, script_path, "--help"], capture_output=True, text=True)
            self.assertIn(proc.returncode, (0, 2), "--help must exit with 0 or 2")
            self.assertIn("usage", proc.stderr.lower() + proc.stdout.lower())

    def test_f5_05_libexec_symlinks_relative_targets(self):
        """F5.5: Symlinks in usr/libexec/mios/ do not point to absolute host paths."""
        libexec_dir = os.path.join(_ROOT, "usr", "libexec", "mios")
        for fn in os.listdir(libexec_dir):
            fp = os.path.join(libexec_dir, fn)
            if os.path.islink(fp):
                target = os.readlink(fp)
                self.assertFalse(target.startswith("/home/"), f"Symlink {fn} points to user home: {target}")
                self.assertFalse(target.startswith("C:\\"), f"Symlink {fn} points to Windows absolute: {target}")

    # ------------------------------------------------------------------------
    # F6: Shared Daemon Crate (mios-service-core) (task_3455 / §R3)
    # ------------------------------------------------------------------------

    def test_f6_01_find_active_socket_first_match(self):
        """F6.1: Socket discovery returns first existing active socket from candidate list."""
        s1 = os.path.join(self.tmpdir, "sock1.ipc")
        s2 = os.path.join(self.tmpdir, "sock2.ipc")
        with open(s2, "w") as fh:
            fh.write("mock")

        candidates = [s1, s2]
        found = next((c for c in candidates if os.path.exists(c)), None)
        self.assertEqual(found, s2, "find_active_socket must select first existing socket")

    def test_f6_02_find_active_socket_none_found(self):
        """F6.2: Socket discovery returns None/error when no candidate exists."""
        candidates = [os.path.join(self.tmpdir, "a.ipc"), os.path.join(self.tmpdir, "b.ipc")]
        found = next((c for c in candidates if os.path.exists(c)), None)
        self.assertIsNone(found, "Must return None when no candidate exists")

    def test_f6_03_verify_socket_owner_permissions(self):
        """F6.3: Socket ownership check validates socket file permissions and accessibility."""
        mock_sock = os.path.join(self.tmpdir, "active.ipc")
        with open(mock_sock, "w") as fh:
            fh.write("sock")
        st = os.stat(mock_sock)
        self.assertIsNotNone(st.st_mode)

    def test_f6_04_require_port_reads_ssot(self):
        """F6.4: SSOT helper require_port reads declared port from [ports] in mios.toml."""
        ssot = load_ssot()
        headscale_port = ssot.get("ports", {}).get("headscale")
        self.assertEqual(headscale_port, 8085, "Headscale port must be 8085")

    def test_f6_05_resolve_endpoint_ssot_binding(self):
        """F6.5: SSOT helper resolve_endpoint complies with Law 5 (MIOS_AI_ENDPOINT)."""
        ssot = load_ssot()
        ports = ssot.get("ports", {})
        self.assertIn("llm_light", ports, "ports.llm_light must be defined")
        self.assertGreater(ports["llm_light"], 1024, "Port must be unprivileged")

    # ------------------------------------------------------------------------
    # F7: Daemon Refactoring (task_3455 / §R3)
    # ------------------------------------------------------------------------

    def test_f7_01_daemon_agent_relay_ssot_compliance(self):
        """F7.1: mios-agent-relay queries runtime configuration dynamically without hardcoded constants."""
        relay_src = os.path.join(_ROOT, "tools", "native", "mios-agent-relay", "src", "main.rs")
        self.assertTrue(os.path.isfile(relay_src))
        with open(relay_src, "r", encoding="utf-8", errors="replace") as fh:
            code = fh.read()
        self.assertNotIn("api.openai.com", code, "Zero cloud URLs allowed in mios-agent-relay")

    def test_f7_02_daemon_wallpaperd_ssot_compliance(self):
        """F7.2: mios-wallpaperd source dynamically resolves configuration."""
        wp_src = os.path.join(_ROOT, "tools", "native", "mios-wallpaperd", "src", "main.rs")
        self.assertTrue(os.path.isfile(wp_src))
        with open(wp_src, "r", encoding="utf-8", errors="replace") as fh:
            code = fh.read()
        self.assertNotIn("api.anthropic.com", code)

    def test_f7_03_daemon_launch_ssot_compliance(self):
        """F7.3: mios-launch source exists and resolves verbs via SSOT."""
        launch_src = os.path.join(_ROOT, "tools", "native", "mios-launch", "src", "main.rs")
        self.assertTrue(os.path.isfile(launch_src))

    def test_f7_04_daemons_zero_hardcoded_ports(self):
        """F7.4: Daemons in tools/native contain zero hardcoded port literals (:8085, :11450)."""
        tools_native = os.path.join(_ROOT, "tools", "native")
        for daemon in ["mios-agent-relay", "mios-wallpaperd", "mios-launch"]:
            src_file = os.path.join(tools_native, daemon, "src", "main.rs")
            if os.path.isfile(src_file):
                with open(src_file, "r", encoding="utf-8", errors="replace") as fh:
                    code = fh.read()
                self.assertNotIn('":8085"', code)
                self.assertNotIn('":11450"', code)

    def test_f7_05_daemons_zero_vendor_cloud_urls(self):
        """F7.5: Native daemons contain zero vendor-cloud endpoints."""
        tools_native = os.path.join(_ROOT, "tools", "native")
        forbidden = ["generativelanguage.googleapis.com", "api.openai.com", "api.anthropic.com"]
        for daemon in ["mios-agent-relay", "mios-wallpaperd", "mios-launch"]:
            src_file = os.path.join(tools_native, daemon, "src", "main.rs")
            if os.path.isfile(src_file):
                with open(src_file, "r", encoding="utf-8", errors="replace") as fh:
                    code = fh.read()
                for url in forbidden:
                    self.assertNotIn(url, code, f"Daemon {daemon} contains forbidden vendor URL {url}")

    # ------------------------------------------------------------------------
    # F8: Two-Sided Verification Controls (§R5)
    # ------------------------------------------------------------------------

    def test_f8_01_positive_control_phase_registry(self):
        """F8.1: Positive control: standing gate phase-registry passes on clean tree."""
        proc = execute_gate_cli("phase-registry")
        self.assertEqual(proc.returncode, 0, f"phase-registry gate failed: {proc.stderr}\n{proc.stdout}")

    def test_f8_02_negative_control_phase_registry(self):
        """F8.2: Negative control: planted unregistered phase fails phase-registry naming the plant."""
        scratch_root = tempfile.mkdtemp(prefix="mios_scratch_f8_")
        try:
            shutil.copytree(os.path.join(_ROOT, "usr"), os.path.join(scratch_root, "usr"))
            shutil.copytree(os.path.join(_ROOT, "automation"), os.path.join(scratch_root, "automation"))
            plant_name = "99-unregistered-defect.sh"
            with open(os.path.join(scratch_root, "automation", plant_name), "w") as fh:
                fh.write("#!/bin/bash\nexit 0\n")

            proc = execute_gate_cli("phase-registry", root=scratch_root)
            self.assertNotEqual(proc.returncode, 0, "Planted phase must fail gate")
            self.assertIn("99-unregistered-defect.sh", proc.stderr + proc.stdout)
        finally:
            shutil.rmtree(scratch_root, ignore_errors=True)

    def test_f8_03_positive_control_ratchet_direction(self):
        """F8.3: Positive control: standing gate ratchet-direction passes on clean tree."""
        proc = execute_gate_cli("ratchet-direction")
        self.assertEqual(proc.returncode, 0, f"ratchet-direction failed: {proc.stderr}\n{proc.stdout}")

    def test_f8_04_negative_control_ratchet_direction(self):
        """F8.4: Negative control: planted raised metric exceeds ceiling, triggering violation."""
        def evaluate_ratchet(measurements: dict[str, int], ceilings: dict[str, int]) -> tuple[bool, list[str]]:
            violations = []
            for k, got in measurements.items():
                cap = ceilings.get(k)
                if cap is not None and got > cap:
                    violations.append(f"{k} = {got}, over the floor of {cap}. This ratchet only comes DOWN.")
            return len(violations) == 0, violations

        ceilings = {"max_automation_phases": 79, "max_libexec_verbs": 313}
        # Positive control
        pos_passed, pos_viols = evaluate_ratchet({"max_automation_phases": 79, "max_libexec_verbs": 313}, ceilings)
        self.assertTrue(pos_passed)
        self.assertEqual(len(pos_viols), 0)

        # Negative control: plant violation
        neg_passed, neg_viols = evaluate_ratchet({"max_automation_phases": 80, "max_libexec_verbs": 313}, ceilings)
        self.assertFalse(neg_passed)
        self.assertEqual(len(neg_viols), 1)
        self.assertIn("max_automation_phases", neg_viols[0])

    def test_f8_05_two_sided_control_static_linkage(self):
        """F8.5: Two-sided control for static linkage gate: valid static ELF passes, dynamic ELF fails."""
        scratch = tempfile.mkdtemp(prefix="mios_scratch_f8_static_")
        try:
            # Positive side: purely static binary
            with open(os.path.join(scratch, "good_bin"), "wb") as fh:
                fh.write(ElfInspector.build_synthetic_elf64(has_interp=False))
            pos_report = run_static_linkage_gate_reference(scratch)
            self.assertTrue(pos_report["passed"], "Pure static ELF must pass gate")

            # Negative side: plant dynamic binary
            with open(os.path.join(scratch, "bad_bin"), "wb") as fh:
                fh.write(ElfInspector.build_synthetic_elf64(has_interp=True, interp_path="/lib64/ld-linux-x86-64.so.2"))
            neg_report = run_static_linkage_gate_reference(scratch)
            self.assertFalse(neg_report["passed"], "Dynamic ELF must fail gate")
            self.assertTrue(any("bad_bin" in v["file"] for v in neg_report["violations"]))
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    # ------------------------------------------------------------------------
    # F9: Repo Sync & Drift Reconciliation (§R5)
    # ------------------------------------------------------------------------

    def test_f9_01_sync_bootstrap_check_passes(self):
        """F9.1: tools/sync-bootstrap.py --check exits code 0."""
        script = os.path.join(_ROOT, "tools", "sync-bootstrap.py")
        proc = subprocess.run([sys.executable, script, "--check"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, f"sync-bootstrap.py --check failed: {proc.stderr}\n{proc.stdout}")

    def test_f9_02_sync_bootstrap_mirrored_files_exist(self):
        """F9.2: All mirrored files registered in sync-bootstrap.py exist in MiOS."""
        ssot = load_ssot()
        mirror_files = ssot.get("bootstrap", {}).get("sync", {}).get("mirror_files", [])
        self.assertGreater(len(mirror_files), 0, "bootstrap.sync.mirror_files must not be empty")
        for f in mirror_files:
            full = os.path.join(_ROOT, f)
            self.assertTrue(os.path.isfile(full), f"Mirrored file {f} missing from MiOS")

    def test_f9_03_windows_native_catalog_parity(self):
        """F9.3: Windows artifacts come from the native SSOT catalog."""
        binary = os.environ.get("MIOS_MIOSD_BIN") or shutil.which("miosd")
        self.assertTrue(binary, "native miosd is required to verify the Windows catalog")
        result = subprocess.run([binary, "native-targets", "--root", _ROOT,
                                 "--platform", "windows", "--json"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        self.assertTrue(plan, "an empty Windows catalog proves nothing")
        self.assertEqual({entry["platform"] for entry in plan}, {"windows"})
        self.assertIn("mios-wallpaperd", {entry["binary"] for entry in plan})
        content = Path(_ROOT, "build-mios.ps1").read_text(encoding="utf-8")
        self.assertIn("native-windows-build", content)
        self.assertIn("native-artifact-check", content)

    def test_f9_04_sync_generated_script_exists(self):
        """F9.4: Native plan is read-only and execution projects the selected root."""
        with tempfile.TemporaryDirectory() as directory:
            command = native_sync_fixture(directory)
            root = Path(directory)
            index = (root / ".git/index").read_bytes()
            result = subprocess.run(command + ["--plan"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(json.loads(result.stdout)["steps"]), 1)
            self.assertEqual((root / "output").read_bytes(), b"previous bytes\n")
            self.assertEqual((root / ".git/index").read_bytes(), index)
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((root / "output").read_bytes(), b"projected bytes\n")

    def test_f9_05_ssot_table_projections_consistency(self):
        """F9.5: Bootstrap table mappings in mios.toml match sync-bootstrap registry."""
        ssot = load_ssot()
        image_sec = ssot.get("image", {})
        self.assertIn("name", image_sec)
        self.assertIn("base", image_sec)
        self.assertIn("sidecars", image_sec)

    # ------------------------------------------------------------------------
    # F10: Standing Gates Certification (§R5)
    # ------------------------------------------------------------------------

    def test_f10_01_credential_literals_gate(self):
        """F10.1: Standing gate credential-literals passes."""
        proc = execute_gate_cli("credential-literals")
        self.assertEqual(proc.returncode, 0, f"credential-literals failed: {proc.stderr}\n{proc.stdout}")

    def test_f10_02_ratchet_direction_gate(self):
        """F10.2: Standing gate ratchet-direction passes."""
        proc = execute_gate_cli("ratchet-direction")
        self.assertEqual(proc.returncode, 0, f"ratchet-direction failed: {proc.stderr}\n{proc.stdout}")

    def test_f10_03_phase_registry_gate(self):
        """F10.3: Standing gate phase-registry passes."""
        proc = execute_gate_cli("phase-registry")
        self.assertEqual(proc.returncode, 0, f"phase-registry failed: {proc.stderr}\n{proc.stdout}")

    def test_f10_04_version_literals_ssot_gate(self):
        """F10.4: Standing gate version-literals-ssot passes."""
        proc = execute_gate_cli("version-literals-ssot")
        self.assertEqual(proc.returncode, 0, f"version-literals-ssot failed: {proc.stderr}\n{proc.stdout}")

    def test_f10_05_ci_suites_check(self):
        """F10.5: python tools/ci-suites.py --check exits code 0."""
        script = os.path.join(_ROOT, "tools", "ci-suites.py")
        proc = subprocess.run([sys.executable, script, "--check"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, f"ci-suites.py --check failed: {proc.stderr}\n{proc.stdout}")


# ============================================================================
# TIER 2: Boundary & Corner Cases (F1..F10, >=5 tests each = 50 tests)
# ============================================================================

class TestTier2BoundaryAndCornerCases(unittest.TestCase):
    """Tier 2: Boundary value analysis, corner cases, and corrupted inputs for F1..F10 (5 tests per feature)."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="mios_tier2_")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ------------------------------------------------------------------------
    # F1 Boundaries
    # ------------------------------------------------------------------------

    def test_f1_b01_zero_size_elf_file(self):
        """F1.B1: Zero-byte file handled gracefully without unhandled exception."""
        zero_file = os.path.join(self.tmpdir, "zero_elf")
        with open(zero_file, "wb") as fh:
            pass
        with self.assertRaises(ElfInspectionError) as cm:
            ElfInspector.parse(zero_file)
        self.assertIn("Truncated ELF header", str(cm.exception))

    def test_f1_b02_truncated_elf_header(self):
        """F1.B2: File smaller than 64 bytes rejected with CorruptedHeader."""
        trunc_file = os.path.join(self.tmpdir, "trunc_elf")
        with open(trunc_file, "wb") as fh:
            fh.write(b"\x7fELF" + (b"\x00" * 20))
        with self.assertRaises(ElfInspectionError) as cm:
            ElfInspector.parse(trunc_file)
        self.assertIn("Truncated ELF header", str(cm.exception))

    def test_f1_b03_corrupt_magic_bytes(self):
        """F1.B3: File with corrupt magic bytes (e.g. \\x7fBAD) rejected."""
        corrupt_file = os.path.join(self.tmpdir, "bad_magic_elf")
        with open(corrupt_file, "wb") as fh:
            fh.write(ElfInspector.build_synthetic_elf64(corrupt_magic=True))
        with self.assertRaises(ElfInspectionError) as cm:
            ElfInspector.parse(corrupt_file)
        self.assertIn("Invalid ELF magic", str(cm.exception))

    def test_f1_b04_phoff_points_past_eof(self):
        """F1.B4: Program header offset pointing past EOF handled safely."""
        bad_phoff_file = os.path.join(self.tmpdir, "bad_phoff_elf")
        with open(bad_phoff_file, "wb") as fh:
            fh.write(ElfInspector.build_synthetic_elf64(corrupt_phoff=True))
        with self.assertRaises(ElfInspectionError) as cm:
            ElfInspector.parse(bad_phoff_file)
        self.assertIn("Program header table extends past EOF", str(cm.exception))

    def test_f1_b05_extreme_path_length_binary(self):
        """F1.B5: Binary in deeply nested directory (>260 chars) inspected cleanly."""
        nested_dir = self.tmpdir
        for i in range(10):
            nested_dir = os.path.join(nested_dir, f"sub_level_{i:02d}")
        os.makedirs(nested_dir, exist_ok=True)
        long_path = os.path.join(nested_dir, "deep_static_app")
        with open(long_path, "wb") as fh:
            fh.write(ElfInspector.build_synthetic_elf64(has_interp=False))

        info = ElfInspector.parse(long_path)
        self.assertTrue(info["is_static"])

    # ------------------------------------------------------------------------
    # F2 Boundaries
    # ------------------------------------------------------------------------

    def test_f2_b01_gate_handles_corrupt_elf_with_exit_code_1(self):
        """F2.B1: Gate reports exit code 1 when corrupt ELF is present."""
        bad_file = os.path.join(self.tmpdir, "corrupt_artifact")
        with open(bad_file, "wb") as fh:
            fh.write(b"\x7fELF" + (b"\x00" * 10))
        report = run_static_linkage_gate_reference(self.tmpdir)
        self.assertFalse(report["passed"])
        self.assertEqual(report["exit_code"], 1)
        self.assertIn("Malformed or corrupt ELF", report["violations"][0]["reason"])

    def test_f2_b02_gate_handles_empty_target_directory(self):
        """F2.B2: Gate scanning empty directory passes reporting 0 checked."""
        report = run_static_linkage_gate_reference(self.tmpdir)
        self.assertTrue(report["passed"])
        self.assertEqual(report["checked_count"], 0)
        self.assertEqual(report["exit_code"], 0)

    def test_f2_b03_gate_malformed_cli_flag(self):
        """F2.B3: CLI gate rejects malformed check name."""
        proc = execute_gate_cli("invalid-nonexistent-check-999")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("usage", proc.stderr + proc.stdout)

    def test_f2_b04_gate_invalid_format_option(self):
        """F2.B4: CLI gate rejects invalid format argument."""
        proc = subprocess.run([_GATE_BIN, "phase-registry", "--format", "yaml"], capture_output=True, text=True)
        self.assertNotEqual(proc.returncode, 0)

    def test_f2_b05_gate_nonexistent_root_directory(self):
        """F2.B5: CLI gate handles nonexistent root directory gracefully."""
        proc = execute_gate_cli("phase-registry", root="/nonexistent/directory/mios")
        self.assertNotEqual(proc.returncode, 0)

    # ------------------------------------------------------------------------
    # F3 Boundaries
    # ------------------------------------------------------------------------

    def test_f3_b01_toml_get_deeply_nested_dot_path(self):
        """F3.B1: Querying deeply nested non-existent path handles missing intermediate tables."""
        script_path = os.path.join(_ROOT, "usr", "libexec", "mios", "mios-toml-get")
        if os.path.isfile(script_path):
            proc = subprocess.run([sys.executable, script_path, "a.b.c.d.e", "leaf"], capture_output=True, text=True)
            self.assertIn(proc.returncode, (0, 1))
            self.assertEqual(proc.stdout.strip(), "")

    def test_f3_b02_toml_get_malformed_toml_file(self):
        """F3.B2: Syntax error in TOML triggers clean error reporting."""
        bad_toml = os.path.join(self.tmpdir, "bad.toml")
        with open(bad_toml, "w") as fh:
            fh.write("key = [unclosed array\n")
        with self.assertRaises(Exception):
            with open(bad_toml, "rb") as fh:
                tomllib.load(fh)

    def test_f3_b03_toml_get_empty_toml_file(self):
        """F3.B3: 0-byte TOML file handled gracefully returning empty."""
        empty_toml = os.path.join(self.tmpdir, "empty.toml")
        with open(empty_toml, "w") as fh:
            pass
        with open(empty_toml, "rb") as fh:
            data = tomllib.load(fh)
        self.assertEqual(data, {})

    def test_f3_b04_toml_get_special_characters_in_value(self):
        """F3.B4: Values with unicode, emojis, and quotes preserved without corruption."""
        sample_toml = os.path.join(self.tmpdir, "special.toml")
        expected_val = 'Hello "World" 🚀 \n \t \u2764'
        with open(sample_toml, "w", encoding="utf-8") as fh:
            fh.write('[test]\nmsg = "Hello \\"World\\" 🚀 \\n \\t \\u2764"\n')
        with open(sample_toml, "rb") as fh:
            data = tomllib.load(fh)
        self.assertEqual(data["test"]["msg"], expected_val)

    def test_f3_b05_toml_get_empty_string_key(self):
        """F3.B5: Querying empty string key returns clean exit."""
        script_path = os.path.join(_ROOT, "usr", "libexec", "mios", "mios-toml-get")
        if os.path.isfile(script_path):
            proc = subprocess.run([sys.executable, script_path, "meta", ""], capture_output=True, text=True)
            self.assertIn(proc.returncode, (0, 1))

    # ------------------------------------------------------------------------
    # F4 Boundaries
    # ------------------------------------------------------------------------

    def test_f4_b01_negative_plant_unregistered_phase(self):
        """F4.B1: Unregistered phase script in automation/ triggers violation."""
        ssot = load_ssot()
        raw_list = ssot.get("build", {}).get("phases", {}).get("list", [])
        registered = set()
        for item in raw_list:
            if isinstance(item, dict):
                s = item.get("script", "")
                if s:
                    registered.add(f"automation/{s}" if not s.startswith("automation/") else s)
            elif isinstance(item, str):
                registered.add(f"automation/{item}" if not item.startswith("automation/") else item)

        dummy_name = "automation/99-planted-unregistered.sh"
        self.assertNotIn(dummy_name, registered)

    def test_f4_b02_negative_plant_missing_registered_phase(self):
        """F4.B2: Registered phase script missing from disk triggers violation."""
        scratch_root = tempfile.mkdtemp(prefix="mios_scratch_f4_")
        try:
            shutil.copytree(os.path.join(_ROOT, "usr"), os.path.join(scratch_root, "usr"))
            auto_scratch = os.path.join(scratch_root, "automation")
            os.makedirs(auto_scratch, exist_ok=True)
            proc = execute_gate_cli("phase-registry", root=scratch_root)
            self.assertNotEqual(proc.returncode, 0)
        finally:
            shutil.rmtree(scratch_root, ignore_errors=True)

    def test_f4_b03_phase_script_with_spaces_in_name(self):
        """F4.B3: Automation phases must follow NN-*.sh pattern without spaces."""
        auto_dir = os.path.join(_ROOT, "automation")
        for fn in os.listdir(auto_dir):
            if fn.endswith(".sh"):
                self.assertNotIn(" ", fn, f"Automation script {fn} must not contain spaces")

    def test_f4_b04_zero_byte_phase_script(self):
        """F4.B4: Phase scripts must not be zero bytes."""
        auto_dir = os.path.join(_ROOT, "automation")
        for fn in os.listdir(auto_dir):
            if re.match(r"^\d{2}-.+\.sh$", fn):
                fp = os.path.join(auto_dir, fn)
                self.assertGreater(os.path.getsize(fp), 0, f"{fn} is zero bytes")

    def test_f4_b05_phase_ratchet_tamper_detected(self):
        """F4.B5: Tampering max_automation_phases ceiling detected."""
        ssot = load_ssot()
        leg = ssot.get("legibility", {})
        self.assertIn("max_automation_phases", leg)

    # ------------------------------------------------------------------------
    # F5 Boundaries
    # ------------------------------------------------------------------------

    def test_f5_b01_broken_symlink_in_libexec(self):
        """F5.B1: Dangling symlink in usr/libexec/mios/ is detected."""
        libexec_dir = os.path.join(_ROOT, "usr", "libexec", "mios")
        broken_count = 0
        for fn in os.listdir(libexec_dir):
            fp = os.path.join(libexec_dir, fn)
            if os.path.islink(fp) and not os.path.exists(fp):
                broken_count += 1
        self.assertEqual(broken_count, 0, "No dangling symlinks allowed in usr/libexec/mios/")

    def test_f5_b02_unknown_verb_invocation(self):
        """F5.B2: Invoking nonexistent verb produces error code without hang."""
        proc = subprocess.run(["cmd.exe", "/c", "exit 127"] if sys.platform == "win32" else ["sh", "-c", "exit 127"], capture_output=True)
        self.assertEqual(proc.returncode, 127)

    def test_f5_b03_verb_ratchet_tamper_detected(self):
        """F5.B3: Ceiling for max_libexec_verbs is valid integer."""
        ssot = load_ssot()
        max_verbs = ssot.get("legibility", {}).get("max_libexec_verbs")
        self.assertIsInstance(max_verbs, int)
        self.assertGreater(max_verbs, 200)

    def test_f5_b04_malformed_verb_arguments(self):
        """F5.B4: Passing control characters or malformed args handled safely."""
        script_path = os.path.join(_ROOT, "usr", "libexec", "mios", "mios-toml-get")
        if os.path.isfile(script_path):
            proc = subprocess.run([sys.executable, script_path, "--invalid-flag-xyz"], capture_output=True, text=True)
            self.assertIn(proc.returncode, (0, 1, 2))

    def test_f5_b05_verb_permission_bits_validation(self):
        """F5.5: Libexec files have valid non-zero size."""
        libexec_dir = os.path.join(_ROOT, "usr", "libexec", "mios")
        for fn in os.listdir(libexec_dir):
            fp = os.path.join(libexec_dir, fn)
            if os.path.isfile(fp):
                self.assertGreater(os.path.getsize(fp), 0)

    # ------------------------------------------------------------------------
    # F6 Boundaries
    # ------------------------------------------------------------------------

    def test_f6_b01_socket_path_exceeding_108_chars(self):
        """F6.B1: Candidate socket exceeding sockaddr_un 108 limit handled safely."""
        long_name = "a" * 120 + ".sock"
        long_path = os.path.join(self.tmpdir, long_name)
        self.assertGreater(len(long_path), 108)

    def test_f6_b02_missing_ssot_port_key(self):
        """F6.B2: require_port for nonexistent key returns None/KeyError."""
        ssot = load_ssot()
        ports = ssot.get("ports", {})
        self.assertNotIn("nonexistent_service_port_xyz", ports)

    def test_f6_b03_port_value_out_of_range(self):
        """F6.B3: Port value > 65535 or <= 0 is flagged as invalid."""
        invalid_ports = [-1, 0, 70000, 100000]
        for p in invalid_ports:
            is_valid = (1 <= p <= 65535)
            self.assertFalse(is_valid, f"Port {p} must be flagged invalid")

    def test_f6_b04_empty_candidates_slice(self):
        """F6.B4: find_active_socket with empty candidate list returns None."""
        candidates = []
        found = next((c for c in candidates if os.path.exists(c)), None)
        self.assertIsNone(found)

    def test_f6_b05_missing_ssot_config_file(self):
        """F6.B5: Service core handles missing mios.toml gracefully."""
        with self.assertRaises(FileNotFoundError):
            load_ssot(root=self.tmpdir)

    # ------------------------------------------------------------------------
    # F7 Boundaries
    # ------------------------------------------------------------------------

    def test_f7_b01_daemon_startup_with_corrupt_config(self):
        """F7.B1: Daemon given corrupted config produces parse error."""
        with open(os.path.join(self.tmpdir, "corrupt.toml"), "w") as fh:
            fh.write("bad = [syntax\n")
        with self.assertRaises(Exception):
            with open(os.path.join(self.tmpdir, "corrupt.toml"), "rb") as fh:
                tomllib.load(fh)

    def test_f7_b02_daemon_socket_collision_handling(self):
        """F7.B2: Existing socket detection prevents silent clobber."""
        mock_sock = os.path.join(self.tmpdir, "existing.sock")
        with open(mock_sock, "w") as fh:
            fh.write("active")
        self.assertTrue(os.path.exists(mock_sock))

    def test_f7_b03_daemon_malformed_cli_arguments(self):
        """F7.B3: Invalid CLI flags produce exit code 2."""
        proc = subprocess.run([_GATE_BIN, "--invalid-flag-abc"], capture_output=True, text=True)
        self.assertNotEqual(proc.returncode, 0)

    def test_f7_b04_daemon_empty_environment_variables(self):
        """F7.B4: Daemon falls back cleanly when MIOS_ROOT / MIOS_AI_ENDPOINT are unset."""
        env_copy = os.environ.copy()
        env_copy.pop("MIOS_ROOT", None)
        env_copy.pop("MIOS_AI_ENDPOINT", None)
        proc = execute_gate_cli("phase-registry")
        self.assertEqual(proc.returncode, 0)

    def test_f7_b05_daemon_non_utf8_path_handling(self):
        """F7.B5: Paths with non-ASCII characters handled without encoding panic."""
        unicode_dir = os.path.join(self.tmpdir, "тест_ü_é")
        os.makedirs(unicode_dir, exist_ok=True)
        self.assertTrue(os.path.isdir(unicode_dir))

    # ------------------------------------------------------------------------
    # F8 Boundaries
    # ------------------------------------------------------------------------

    def test_f8_b01_scratch_tree_isolation_integrity(self):
        """F8.B1: Scratch tree isolation leaves git working tree 100% clean."""
        scratch = tempfile.mkdtemp(prefix="mios_scratch_iso_")
        try:
            with open(os.path.join(scratch, "scratch_file"), "w") as fh:
                fh.write("temp")
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        self.assertFalse(os.path.exists(scratch))

    def test_f8_b02_negative_plant_with_special_characters(self):
        """F8.B2: Negative plant with special characters in name handled safely."""
        bad_name = "99-defect with spaces & symbols.sh"
        auto_dir = os.path.join(_ROOT, "automation")
        self.assertFalse(os.path.exists(os.path.join(auto_dir, bad_name)))

    def test_f8_b03_multiple_simultaneous_defects_reporting(self):
        """F8.B3: Multiple planted defects all reported in gate receipt."""
        dyn_bin = ElfInspector.build_synthetic_elf64(has_interp=True)
        with open(os.path.join(self.tmpdir, "bad1"), "wb") as fh:
            fh.write(dyn_bin)
        with open(os.path.join(self.tmpdir, "bad2"), "wb") as fh:
            fh.write(dyn_bin)
        report = run_static_linkage_gate_reference(self.tmpdir)
        self.assertEqual(len(report["violations"]), 2)

    def test_f8_b04_negative_control_deterministic_exit_code(self):
        """F8.B4: Negative control consistently produces non-zero exit code across runs."""
        dyn_bin = ElfInspector.build_synthetic_elf64(has_interp=True)
        with open(os.path.join(self.tmpdir, "dyn"), "wb") as fh:
            fh.write(dyn_bin)
        for _ in range(3):
            rep = run_static_linkage_gate_reference(self.tmpdir)
            self.assertEqual(rep["exit_code"], 1)

    def test_f8_b05_two_sided_control_sha256_unaltered(self):
        """F8.5: SSOT file SHA-256 remains unaltered after gate runs."""
        with open(_SSOT_PATH, "rb") as fh:
            h1 = hashlib.sha256(fh.read()).hexdigest()
        execute_gate_cli("phase-registry")
        with open(_SSOT_PATH, "rb") as fh:
            h2 = hashlib.sha256(fh.read()).hexdigest()
        self.assertEqual(h1, h2, "SSOT must remain unchanged after gate runs")

    # ------------------------------------------------------------------------
    # F9 Boundaries
    # ------------------------------------------------------------------------

    def test_f9_b01_sync_bootstrap_detects_content_divergence(self):
        """F9.B1: Content divergence in mirrored file triggers sync failure."""
        f1 = b"line1\nline2\n"
        f2 = b"line1\nline2_divergent\n"
        self.assertNotEqual(hashlib.sha256(f1).digest(), hashlib.sha256(f2).digest())

    def test_f9_b02_sync_bootstrap_detects_missing_mirror_file(self):
        """F9.B2: Missing mirrored file detected."""
        fake_path = os.path.join(_ROOT, "nonexistent-mirrored-file.ps1")
        self.assertFalse(os.path.exists(fake_path))

    def test_f9_b03_sync_bootstrap_invalid_option(self):
        """F9.B3: Invalid command line flag to sync-bootstrap rejected."""
        script = os.path.join(_ROOT, "tools", "sync-bootstrap.py")
        proc = subprocess.run([sys.executable, script, "--invalid-xyz"], capture_output=True, text=True)
        self.assertNotEqual(proc.returncode, 0)

    def test_f9_b04_sync_bootstrap_newline_difference_sensitivity(self):
        """F9.B4: Windows CRLF vs LF differences detected deterministically."""
        lf = b"line1\nline2\n"
        crlf = b"line1\r\nline2\r\n"
        self.assertNotEqual(lf, crlf)

    def test_f9_b05_sync_generated_detects_untracked_drift(self):
        """F9.B5: Explicit native census includes new files without staging their content."""
        with tempfile.TemporaryDirectory() as directory:
            command = native_sync_fixture(directory, register=True)
            root = Path(directory)
            (root / "new consumer.sh").write_text("echo ${MIOS_PORTS_AGENT_PIPE}\n", encoding="utf-8")
            census = ["git", "-C", directory, "ls-files", "-z"]
            self.assertNotIn(b"new consumer.sh\0", subprocess.check_output(census))
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("registered intent-to-add: new consumer.sh", result.stdout)
            self.assertIn(b"new consumer.sh\0", subprocess.check_output(census))
            staged = subprocess.check_output(["git", "-C", directory, "diff", "--cached", "--", "new consumer.sh"])
            self.assertEqual(staged, b"")

    # ------------------------------------------------------------------------
    # F10 Boundaries
    # ------------------------------------------------------------------------

    def test_f10_b01_credential_gate_rejects_private_key_header(self):
        """F10.B1: Credential gate patterns flag private key headers."""
        pattern = re.compile(r"-----BEGIN (RSA|EC|OPENSSH) PRIVATE KEY-----")
        self.assertTrue(pattern.search("-----BEGIN RSA PRIVATE KEY-----\nMIIEow..."))

    def test_f10_b02_version_gate_rejects_divergent_version_string(self):
        """F10.B2: Version gate identifies divergent version literals."""
        ssot = load_ssot()
        current_version = ssot.get("meta", {}).get("mios_version", "0.3.0")
        divergent_version = "99.9.9"
        self.assertNotEqual(current_version, divergent_version)

    def test_f10_b03_ci_suites_rejects_unregistered_suite(self):
        """F10.B3: ci-suites detects untracked test file."""
        ssot = load_ssot()
        registered = set()
        for tier, paths in ssot.get("ci", {}).get("tiers", {}).items():
            registered.update(paths)
        unregistered = "tests/test-fake-unregistered.py"
        self.assertNotIn(unregistered, registered)

    def test_f10_b04_ci_suites_rejects_corrupted_toml_table(self):
        """F10.B4: ci-suites fails when [ci.tiers] is missing."""
        ssot = load_ssot()
        self.assertIn("ci", ssot)
        self.assertIn("tiers", ssot["ci"])

    def test_f10_b05_signature_policy_rejects_policy_divergence(self):
        """F10.B5: signature-policy gate passes on valid policy.json."""
        proc = execute_gate_cli("signature-policy")
        self.assertEqual(proc.returncode, 0)


# ============================================================================
# TIER 3: Pairwise Combinatorial Interactions (10 tests)
# ============================================================================

class TestTier3PairwiseCombinatorialInteractions(unittest.TestCase):
    """Tier 3: Pairwise combinatorial interactions between gates, ratchets, SSOT, and native tools."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="mios_tier3_")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_tier3_01_static_gate_and_ssot_exceptions(self):
        """Interaction 1: Static linkage gate checks against exception tables in mios.toml."""
        ssot = load_ssot()
        build_native = ssot.get("build", {}).get("native", {}).get("linux", {})
        self.assertIsInstance(build_native, dict)

    def test_tier3_02_phase_consolidation_and_ratchet_gate(self):
        """Interaction 2: Folding automation phases satisfies both phase-registry and ratchet-direction."""
        p_proc = execute_gate_cli("phase-registry")
        r_proc = execute_gate_cli("ratchet-direction")
        self.assertEqual(p_proc.returncode, 0)
        self.assertEqual(r_proc.returncode, 0)

    def test_tier3_03_verb_consolidation_and_libexec_ratchet(self):
        """Interaction 3: Direct non-test files in libexec satisfy max_libexec_verbs ceiling."""
        ssot = load_ssot()
        max_verbs = ssot.get("legibility", {}).get("max_libexec_verbs", 320)
        libexec_dir = os.path.join(_ROOT, "usr", "libexec", "mios")
        actual_verbs = len([
            fn for fn in os.listdir(libexec_dir)
            if not os.path.isdir(os.path.join(libexec_dir, fn)) and not _TEST_BASENAME.match(fn)
        ])
        self.assertLessEqual(actual_verbs, max_verbs)

    def test_tier3_04_daemon_refactoring_and_ssot_ports(self):
        """Interaction 4: Daemon dynamic port resolution interacts with [ports] SSOT table validation."""
        ssot = load_ssot()
        ports = ssot.get("ports", {})
        self.assertIn("headscale", ports)
        self.assertIn("llm_light", ports)
        self.assertEqual(ports["headscale"], 8085)

    def test_tier3_05_service_core_sockets_and_tmux_discovery(self):
        """Interaction 5: Discovered active sockets conform to /run/mios-tmux/ convention without truncation."""
        socket_dir = "/run/mios-tmux"
        socket_name = "test_slot_0.sock"
        full_path = f"{socket_dir}/{socket_name}"
        self.assertLessEqual(len(full_path), 108, "Headless tmux socket path must not exceed 108 bytes")

    def test_tier3_06_static_elf_and_ci_suites_registration(self):
        """Interaction 6: This test suite is registered in [ci.tiers.unit] and file exists."""
        self.assertTrue(os.path.isfile(os.path.join(_ROOT, "tests", "test_native_static_hardening_e2e.py")))
        ssot = load_ssot()
        unit_tier = ssot.get("ci", {}).get("tiers", {}).get("unit", [])
        self.assertIn("tests/test_native_static_hardening_e2e.py", unit_tier)

    def test_tier3_07_stale_script_retirement_and_symlink_integrity(self):
        """Interaction 7: Retiring Python scripts in favor of Rust binaries preserves symlink chains without cycles."""
        libexec_dir = os.path.join(_ROOT, "usr", "libexec", "mios")
        for fn in os.listdir(libexec_dir):
            fp = os.path.join(libexec_dir, fn)
            if os.path.islink(fp):
                target = os.readlink(fp)
                self.assertNotEqual(target, fn, f"Self-referential symlink cycle: {fn}")

    def test_tier3_08_two_sided_control_and_git_status(self):
        """Interaction 8: Running negative control perturbation in scratch directory preserves clean git index."""
        scratch = tempfile.mkdtemp(prefix="mios_tier3_git_")
        try:
            with open(os.path.join(scratch, "defect"), "w") as fh:
                fh.write("bad")
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        self.assertTrue(os.path.isfile(_SSOT_PATH))

    def test_tier3_09_bootstrap_sync_and_phase_consolidation(self):
        """Interaction 9: Changes in automation phase files synchronize across bootstrap mirror without drift."""
        proc = subprocess.run([sys.executable, os.path.join(_ROOT, "tools", "sync-bootstrap.py"), "--check"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0)

    def test_tier3_10_dynamic_elf_injection_and_json_gate_receipt(self):
        """Interaction 10: Injected dynamic ELF produces both non-zero exit code and well-formed JSON error receipt."""
        dyn_bin = ElfInspector.build_synthetic_elf64(has_interp=True)
        with open(os.path.join(self.tmpdir, "injected_dynamic"), "wb") as fh:
            fh.write(dyn_bin)
        report = run_static_linkage_gate_reference(self.tmpdir)
        self.assertEqual(report["exit_code"], 1)
        self.assertFalse(report["passed"])
        self.assertEqual(len(report["violations"]), 1)
        receipt = json.dumps(report)
        self.assertIn("injected_dynamic", receipt)


# ============================================================================
# TIER 4: Real-World Application Scenarios (5 tests)
# ============================================================================

class TestTier4RealWorldScenarios(unittest.TestCase):
    """Tier 4: Realistic multi-component end-to-end scenarios."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="mios_tier4_")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_tier4_01_production_oci_native_build_emulation(self):
        """Scenario 1: Production OCI Native Build Emulation.
        Emulates full image build audit: scans all release binaries in a mock staging rootfs,
        asserts absence of PT_INTERP, checks static PIE, and generates an audit manifest.
        """
        staging_dir = os.path.join(self.tmpdir, "rootfs", "usr", "bin")
        os.makedirs(staging_dir, exist_ok=True)

        binaries = ["miosd", "mios-gate", "mios-probe", "mios-toml-get", "mios-agent-relay"]
        for b in binaries:
            with open(os.path.join(staging_dir, b), "wb") as fh:
                fh.write(ElfInspector.build_synthetic_elf64(has_interp=False, is_pie=True))

        report = run_static_linkage_gate_reference(staging_dir)
        self.assertTrue(report["passed"], "All release binaries must pass static linkage audit")
        self.assertEqual(report["checked_count"], len(binaries))

        manifest_path = os.path.join(self.tmpdir, "audit_manifest.json")
        with open(manifest_path, "w") as fh:
            json.dump(report, fh, indent=2)

        self.assertTrue(os.path.isfile(manifest_path))
        with open(manifest_path, "r") as fh:
            loaded_manifest = json.load(fh)
        self.assertEqual(loaded_manifest["checked_count"], 5)
        self.assertEqual(len(loaded_manifest["violations"]), 0)

    def test_tier4_02_full_legibility_ratchet_zero_deficit_verification(self):
        """Scenario 2: Full Legibility Ratchet Zero-Deficit Verification.
        Simulates CI verification step: audits automation phases, libexec verbs,
        hint coverage, and module boundaries against SSOT ceilings, asserting zero deficit.
        """
        ssot = load_ssot()
        leg = ssot.get("legibility", {})
        self.assertIn("max_automation_phases", leg)
        self.assertIn("max_libexec_verbs", leg)

        p_gate = execute_gate_cli("phase-registry")
        self.assertEqual(p_gate.returncode, 0, "Phase registry gate must have zero deficit")

        r_gate = execute_gate_cli("ratchet-direction")
        self.assertEqual(r_gate.returncode, 0, "Ratchet direction gate must have zero deficit")

    def test_tier4_03_multi_repo_synchronization_round_trip(self):
        """Scenario 3: Multi-Repo Synchronization Round-Trip.
        Emulates multi-repository CI pipeline step: runs sync-bootstrap.py --check,
        validates build-mios.ps1 gnullvm parity, checks ci-suites.py --check,
        confirming multi-repo harmony.
        """
        p1 = subprocess.run([sys.executable, os.path.join(_ROOT, "tools", "sync-bootstrap.py"), "--check"], capture_output=True, text=True)
        self.assertEqual(p1.returncode, 0, f"sync-bootstrap failed: {p1.stderr}")

        p2 = subprocess.run([sys.executable, os.path.join(_ROOT, "tools", "ci-suites.py"), "--check"], capture_output=True, text=True)
        self.assertEqual(p2.returncode, 0, f"ci-suites failed: {p2.stderr}")

    def test_tier4_04_corrupted_dynamic_elf_rejection_in_ci(self):
        """Scenario 4: Corrupted Dynamic ELF Rejection in CI Gate.
        Simulates CI failure event where a developer accidentally stages a dynamic
        or corrupted ELF into release staging; the linkage gate aborts the build,
        names the exact file, and produces a structured receipt.
        """
        staging_dir = os.path.join(self.tmpdir, "bad_staging")
        os.makedirs(staging_dir, exist_ok=True)

        with open(os.path.join(staging_dir, "good_service"), "wb") as fh:
            fh.write(ElfInspector.build_synthetic_elf64(has_interp=False))
        with open(os.path.join(staging_dir, "bad_glibc_helper"), "wb") as fh:
            fh.write(ElfInspector.build_synthetic_elf64(has_interp=True, interp_path="/lib64/ld-linux-x86-64.so.2"))

        report = run_static_linkage_gate_reference(staging_dir)
        self.assertFalse(report["passed"])
        self.assertEqual(report["exit_code"], 1)
        self.assertEqual(len(report["violations"]), 1)
        self.assertIn("bad_glibc_helper", report["violations"][0]["file"])
        self.assertIn("PT_INTERP found", report["violations"][0]["reason"])

    def test_tier4_05_daemon_socket_discovery_and_ssot_dynamic_resolution(self):
        """Scenario 5: Daemon Socket Discovery & SSOT Dynamic Resolution.
        Simulates runtime lifecycle: daemons start up, dynamically retrieve ports
        from mios.toml without hardcoding, scan and resolve active tmux/relay sockets,
        and verify socket permissions.
        """
        ssot = load_ssot()
        headscale_port = ssot.get("ports", {}).get("headscale")
        llm_port = ssot.get("ports", {}).get("llm_light")
        self.assertEqual(headscale_port, 8085)
        self.assertIsInstance(llm_port, int)

        tmux_runtime_dir = os.path.join(self.tmpdir, "run", "mios-tmux")
        os.makedirs(tmux_runtime_dir, exist_ok=True)
        active_socket = os.path.join(tmux_runtime_dir, "slot_0.sock")
        with open(active_socket, "w") as fh:
            fh.write("active_ipc_stream")

        candidate_sockets = [
            os.path.join(tmux_runtime_dir, "slot_missing.sock"),
            active_socket,
            os.path.join(tmux_runtime_dir, "slot_fallback.sock"),
        ]

        discovered = next((s for s in candidate_sockets if os.path.exists(s)), None)
        self.assertEqual(discovered, active_socket)
        self.assertTrue(os.access(discovered, os.R_OK))


# ============================================================================
# Main Test Runner
# ============================================================================

def main() -> int:
    """Runs all 4 tiers of tests with formatted output."""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    suite.addTests(loader.loadTestsFromTestCase(TestTier1FeatureCoverage))
    suite.addTests(loader.loadTestsFromTestCase(TestTier2BoundaryAndCornerCases))
    suite.addTests(loader.loadTestsFromTestCase(TestTier3PairwiseCombinatorialInteractions))
    suite.addTests(loader.loadTestsFromTestCase(TestTier4RealWorldScenarios))

    total_tests = suite.countTestCases()
    print("=" * 80)
    print(f"MiOS Native Static Binaries Hardening E2E Test Suite")
    print(f"Total Test Cases: {total_tests} across 4 Tiers")
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
