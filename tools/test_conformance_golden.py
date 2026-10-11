#!/usr/bin/env python3
# AI-hint: Golden CLI fixture and template conformance test runner against committed independent golden.snap without auto-regeneration.
# AI-related: usr/libexec/mios/check-template-conformance, tests/templates/golden.snap, usr/libexec/mios/mios-new
# AI-functions: test_templates_match_independent_golden_snapshot, test_tampered_template_detection, test_conformant_mini_tree, test_missing_ai_hint_header

import hashlib
import importlib.machinery
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECK_BIN = os.path.join(ROOT, "usr/libexec/mios/check-template-conformance")
GOLDEN_SNAPSHOT = os.path.join(ROOT, "tests/templates/golden.snap")
SYS_LIBEXEC = os.path.join(ROOT, "usr/libexec/mios")

# Load extensionless script mios-new
loader = importlib.machinery.SourceFileLoader("mios_new", os.path.join(SYS_LIBEXEC, "mios-new"))
spec = importlib.util.spec_from_loader(loader.name, loader)
mios_new = importlib.util.module_from_spec(spec)
loader.exec_module(mios_new)

TYPES = [
    "adr", "roadmap-ws", "markdown-doc", "roadmap", "automation-step",
    "drift-check", "bash-verb", "bash-tool", "bash", "python-module",
    "python-test", "python-tool", "rust", "typescript", "powershell",
    "toml-config", "yaml", "json-schema", "systemd-unit", "quadlet"
]

def render_for_type(type_name):
    tmpl_path = os.path.join(ROOT, "usr/share/mios/templates", type_name)
    with open(tmpl_path, "r", encoding="utf-8") as f:
        content = f.read()
    name = "0012-sample-test" if type_name == "adr" else "sample-test"
    rendered = mios_new.render_template(content, name, type_name)
    return re.sub(r"\d{4}-\d{2}-\d{2}", "2026-07-17", rendered)

def parse_golden_sections(text):
    parts = re.split(r"^--8<-- (\S+)\n", text, flags=re.M)
    if parts[0]:
        raise ValueError("text before first --8<-- marker")
    return dict(zip(parts[1::2], parts[2::2]))

class TestConformanceGolden(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="mios_conform_test_")
        # Copy mios.toml for templates matching config
        os.makedirs(os.path.join(self.tmpdir, "usr/share/mios"), exist_ok=True)
        shutil.copy(
            os.path.join(ROOT, "usr/share/mios/mios.toml"),
            os.path.join(self.tmpdir, "usr/share/mios/mios.toml")
        )

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def run_check(self, root_dir, max_unconforming=0):
        env = os.environ.copy()
        env["MIOS_THEME_ROOT"] = ROOT
        env["MIOS_TOML_ROOT"] = root_dir
        cmd = [sys.executable, CHECK_BIN, "--root", root_dir, "--max-unconforming", str(max_unconforming)]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        return res.returncode, res.stdout, res.stderr

    def test_templates_match_independent_golden_snapshot(self):
        self.assertTrue(os.path.isfile(GOLDEN_SNAPSHOT), "Independent golden snapshot missing: tests/templates/golden.snap")
        with open(GOLDEN_SNAPSHOT, "rb") as f:
            sha_before = hashlib.sha256(f.read()).hexdigest()

        with open(GOLDEN_SNAPSHOT, "r", encoding="utf-8", newline="") as f:
            sections = parse_golden_sections(f.read())

        self.assertEqual(sorted(sections), sorted(TYPES), "Snapshot sections differ from TYPES")
        for t in TYPES:
            expected = render_for_type(t)
            actual = sections.get(t)
            self.assertEqual(actual, expected, f"Mismatch in golden snapshot for template '{t}'")

        with open(GOLDEN_SNAPSHOT, "rb") as f:
            sha_after = hashlib.sha256(f.read()).hexdigest()
        self.assertEqual(sha_before, sha_after, "Golden snapshot was modified during test run! Auto-regeneration is strictly forbidden.")

    def test_tampered_template_detection(self):
        # Two-sided negative control: perturbing rendered output triggers mismatch assertion
        rendered = render_for_type("rust")
        tampered = rendered + " "
        with open(GOLDEN_SNAPSHOT, "r", encoding="utf-8", newline="") as f:
            sections = parse_golden_sections(f.read())
        with self.assertRaises(AssertionError):
            self.assertEqual(sections.get("rust"), tampered)

    def test_conformant_mini_tree(self):
        # Create a conformant python tool
        bin_dir = os.path.join(self.tmpdir, "usr/libexec/mios")
        os.makedirs(bin_dir, exist_ok=True)
        fpath = os.path.join(bin_dir, "mios-test-tool.py")
        with open(fpath, "w", encoding="utf-8") as f:
            f.write("#!/usr/bin/env python3\n# AI-hint: Test tool\n# AI-related: none\n# AI-functions: main\n\ndef main():\n    pass\n")

        code, stdout, stderr = self.run_check(self.tmpdir)
        self.assertEqual(code, 0)
        self.assertIn("[conformance] checked=", stdout)
        self.assertIn("unconforming=0", stdout)

    def test_missing_ai_hint_header(self):
        bin_dir = os.path.join(self.tmpdir, "usr/libexec/mios")
        os.makedirs(bin_dir, exist_ok=True)
        fpath = os.path.join(bin_dir, "mios-no-hint.py")
        with open(fpath, "w", encoding="utf-8") as f:
            f.write("#!/usr/bin/env python3\n\ndef main():\n    pass\n")

        code, stdout, stderr = self.run_check(self.tmpdir)
        self.assertEqual(code, 1)
        self.assertIn("Missing AI-hint header", stderr)

if __name__ == "__main__":
    unittest.main()
