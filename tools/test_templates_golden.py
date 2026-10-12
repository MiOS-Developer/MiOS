#!/usr/bin/env python3
# AI-hint: Golden fixture test runner for mios-new template generator across all 20 template types.
# AI-doc: usr/share/doc/mios/manual/tools.md

import os
import sys
import unittest
import importlib.machinery
import importlib.util
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SYS_LIBEXEC = os.path.join(ROOT, "usr/libexec/mios")
# One file, one "--8<-- <type>" section per template type.
GOLDEN = os.path.join(ROOT, "tests/templates/golden.snap")

# Load extensionless script mios-new
mios_new_path = os.path.join(SYS_LIBEXEC, "mios-new")
loader = importlib.machinery.SourceFileLoader("mios_new", mios_new_path)
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
    rendered = re.sub(r"\d{4}-\d{2}-\d{2}", "2026-07-17", rendered)
    return rendered

def golden_sections(text):
    parts = re.split(r"^--8<-- (\S+)\n", text, flags=re.M)
    if parts[0]:
        raise ValueError("text before the first --8<-- marker")
    return dict(zip(parts[1::2], parts[2::2]))

class TestTemplatesGolden(unittest.TestCase):
    def test_all_templates_have_golden_fixtures(self):
        with open(GOLDEN, "r", encoding="utf-8", newline="") as f:
            sections = golden_sections(f.read())
        self.assertEqual(sorted(sections), sorted(TYPES), "golden.snap sections differ from TYPES")
        for t in TYPES:
            self.assertEqual(sections[t], render_for_type(t), f"Mismatch in golden snapshot for template '{t}'")

    def test_section_parser_is_two_sided(self):
        self.assertEqual(golden_sections("--8<-- a\nx\n--8<-- b\ny\n"), {"a": "x\n", "b": "y\n"})
        with self.assertRaises(ValueError):
            golden_sections("stray\n--8<-- a\nx\n")

if __name__ == "__main__":
    unittest.main()
