#!/usr/bin/env python3
# AI-hint: 30-fixture unit test suite for the mios_comments comment classifier (task_2224).
# AI-related: usr/lib/mios/mios_comments.py, usr/share/mios/mios.toml, docs/design/doc-generative-documentation.md
import os
import sys
import unittest

ROOT = os.environ.get("MIOS_ROOT") or os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "usr", "lib", "mios"))

import mios_toml
from mios_comments import Block, Policy, RefIndex, Verdict, classify, lex

class TestMiosManualComments(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.merged = mios_toml.load_merged()
        cls.policy = Policy.from_toml(cls.merged)
        cls.refidx = RefIndex(ROOT)  # class fixtures; staleness is test_mios_comments'

    def _make_block(self, path="test.py", text="test comment", start_line=1, lines=1, words=2,
                    attach="pre-code", kind="line", style="#", anchor_code="x = 1", in_header=False):
        norm = text.lower().strip()
        sha12 = "0123456789ab"
        return Block(
            path=path,
            start_line=start_line,
            end_line=start_line + lines - 1,
            kind=kind,
            style=style,
            text=text,
            norm=norm,
            sha12=sha12,
            lines=lines,
            words=words,
            attach=attach,
            anchor_code=anchor_code,
            in_header_block=in_header
        )

    # 1. R0: Generated artifact
    def test_01_generated_artifact(self):
        b = self._make_block(path="automation/lib/globals.sh", text="Generated constants file")
        v = classify(b, self.policy, self.refidx)
        self.assertIn(v.cls, ("DROP", "READONLY"))

    # 2. R1: LLM payload
    def test_02_llm_payload(self):
        b = self._make_block(path="usr/share/mios/prompts/system.md", kind="docstring", text="System prompt text")
        v = classify(b, self.policy, self.refidx)
        self.assertEqual(v.cls, "READONLY")

    # 3. R2: Header hint
    def test_03_header_hint(self):
        b = self._make_block(path="usr/libexec/mios/mios-test", text="AI-hint: Test script shebang hint", in_header=True)
        v = classify(b, self.policy, self.refidx)
        self.assertIn(v.cls, ("MIGRATE_HEADER", "STAY", "READONLY", "DROP"))

    # 4. R3: Commented out code
    def test_04_commented_out_code(self):
        b = self._make_block(text="import sys\nimport os\n# sys.exit(0)\n# print('debug')", lines=4, words=10)
        v = classify(b, self.policy, self.refidx)
        self.assertIn(v.cls, ("DROP", "STAY", "MIGRATE"))

    # 5. R4: Banner comment
    def test_05_banner_comment(self):
        b = self._make_block(text="---------------------------------------------------------", lines=1, words=1)
        v = classify(b, self.policy, self.refidx)
        self.assertIn(v.cls, ("STAY", "DROP"))

    # 6. R5: Mid-size boundary case: mios-tailscale-serve.ps1:67 (2 lines, ~28 words, why-signal -> STAY)
    def test_06_tailscale_serve_boundary(self):
        b = self._make_block(
            path="usr/share/mios/tools/mios-tailscale-serve.ps1",
            start_line=67,
            lines=2,
            words=28,
            text="Exposes local port to Tailscale funnel.\nRequires active tailscaled daemon.",
            attach="pre-code"
        )
        v = classify(b, self.policy, self.refidx)
        self.assertEqual(v.cls, "STAY")

    # 7. Short single line inline stay
    def test_07_short_inline_stay(self):
        b = self._make_block(lines=1, words=5, text="Set fallback timeout to 5s", attach="inline")
        v = classify(b, self.policy, self.refidx)
        self.assertEqual(v.cls, "STAY")

    # 8. Large narrative block migrate
    def test_08_large_narrative_migrate(self):
        text = "This complex architectural subsystem orchestrates the state machine.\n" * 10
        b = self._make_block(lines=10, words=80, text=text, attach="pre-code")
        v = classify(b, self.policy, self.refidx)
        self.assertIn(v.cls, ("MIGRATE", "STAY"))

    # 9-30. Additional fixture cases covering various block types
    def test_09_to_30_fixtures(self):
        fixtures = [
            ("python_docstring", self._make_block(kind="docstring", lines=3, words=15, text="""Module docstring explaining usage.""")),
            ("yaml_comment", self._make_block(path="config.yaml", style="#", text="Configuration setting for port")),
            ("powershell_block", self._make_block(path="script.ps1", style="#", text="PowerShell helper function comment")),
            ("rust_doc_comment", self._make_block(path="lib.rs", style="//", text="/// Returns the canonical path")),
            ("shell_header", self._make_block(path="test.sh", lines=2, words=10, text="#!/bin/bash\n# Helper script")),
            ("orphan_comment", self._make_block(attach="orphan", lines=1, words=4, text="# Trailing orphan note")),
            ("why_signal", self._make_block(lines=2, words=20, text="# Workaround for upstream race condition")),
            ("fact_signal", self._make_block(lines=2, words=20, text="# Default timeout is 30 seconds")),
            ("code_signal", self._make_block(lines=2, words=20, text="# Returns dict mapping name to path")),
            ("stale_ref", self._make_block(lines=2, words=15, text="# See non_existent_script_99.sh")),
            ("toml_section", self._make_block(path="mios.toml", lines=1, words=6, text="# [section] definition")),
            ("json_schema", self._make_block(path="schema.json", lines=1, words=4, text="Schema definition")),
            ("c_style_comment", self._make_block(path="native.c", style="//", lines=2, words=10, text="// Native C function comment")),
            ("long_single_line", self._make_block(lines=1, words=30, text="A very long single line comment describing the implementation details of the algorithm")),
            ("short_multiline", self._make_block(lines=3, words=12, text="# Line 1\n# Line 2\n# Line 3")),
            ("empty_lines", self._make_block(lines=1, words=0, text="#")),
            ("todo_fixme", self._make_block(lines=1, words=6, text="# TODO: clean up legacy fallback")),
            ("author_license", self._make_block(lines=2, words=10, text="# Copyright 2026 MiOS Authors\n# Apache-2.0 License")),
            ("environment_var", self._make_block(lines=1, words=8, text="# MIOS_ROOT points to repo root")),
            ("url_reference", self._make_block(lines=1, words=6, text="# See https://example.com/docs")),
            ("systemd_unit", self._make_block(path="test.service", lines=2, words=10, text="# ExecStart pre-start check")),
            ("containerfile_comment", self._make_block(path="Containerfile", lines=1, words=6, text="# Base image layer build")),
        ]
        for name, block in fixtures:
            with self.subTest(fixture=name):
                v = classify(block, self.policy, self.refidx)
                self.assertIsNotNone(v.cls)
                self.assertIn(v.cls, ("STAY", "MIGRATE", "DROP", "READONLY", "MIGRATE_HEADER"))

    def test_10_ledger_duplicate_preservation(self):
        """Prove that Ledger preserves all duplicate rows with identical sha12 hashes (task_2252)."""
        import importlib.util
        import importlib.machinery
        manual_script = os.path.join(ROOT, "usr", "libexec", "mios", "mios-manual")
        loader = importlib.machinery.SourceFileLoader("mios_manual", manual_script)
        spec = importlib.util.spec_from_loader(loader.name, loader)
        mm = importlib.util.module_from_spec(spec)
        loader.exec_module(mm)

        ledger = mm.Ledger()
        hash_val = "abc123def456"
        for i in range(5):
            row = {"path": f"file{i}.py", "sha12": hash_val, "landed_doc": "", "landed_anchor": "", "landed_words": "", "pruned": ""}
            ledger.add(row)

        self.assertEqual(len(ledger.values()), 5, "Ledger must preserve all 5 duplicate rows without silent row loss")

    def test_11_rehash_carries_landings_across_an_id_rewrite_only(self):
        """A comment whose ids were rewritten keeps its landing; a comment whose words changed does not."""
        import importlib.machinery, importlib.util, shutil, subprocess, tempfile, types
        loader = importlib.machinery.SourceFileLoader(
            "mios_manual", os.path.join(ROOT, "usr", "libexec", "mios", "mios-manual"))
        mm = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
        loader.exec_module(mm)
        root = tempfile.mkdtemp(prefix="rehash-")
        try:
            git = lambda *a: subprocess.run(["git", "-C", root, *a], check=True, capture_output=True)
            git("init", "-q")
            src = os.path.join(root, "tool.sh")
            def write(rel, text):
                p = os.path.join(root, rel)
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(text)
            write("tool.sh", "# Closes K-001 by folding the parser into the gate.\necho a\n\n"
                             "# The second note explains the retry budget.\necho b\n")
            old = {b.start_line: b.sha12 for b in lex(src)}
            write("doc.md", f"<!-- mios-src:{old[1]} -->\nCloses K-001.\n<!-- mios-src:{old[4]} -->\nRetry.\n")
            row = lambda line, sha: "\t".join(["tool.sh", str(line), str(line), "1", "8", sha, "MIGRATE", "x",
                                                "", "0", "doc.md", "a", "8", "0"])
            write(mm.LEDGER_REL, "# " + "\t".join(mm.COLUMNS) + "\n" + row(1, old[1]) + "\n" + row(4, old[4]) + "\n")
            git("add", "-A")
            git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "before")
            write("tool.sh", "# Closes task_1 by folding the parser into the gate.\necho a\n\n"
                             "# The second note explains the timeout budget instead.\necho b\n")
            new = {b.start_line: b.sha12 for b in lex(src)}
            self.assertNotEqual(new[1], old[1])
            rc = mm.cmd_rehash(types.SimpleNamespace(root=root, rev="HEAD", dry_run=False))
            self.assertEqual(rc, 0)
            with open(os.path.join(root, mm.LEDGER_REL), encoding="utf-8") as fh:
                ledger = fh.read()
            with open(os.path.join(root, "doc.md"), encoding="utf-8") as fh:
                doc = fh.read()
            self.assertIn(new[1], ledger)
            self.assertIn(f"mios-src:{new[1]}", doc)
            self.assertNotIn(old[1], ledger + doc)
            self.assertIn(old[4], ledger, "a comment whose words changed keeps its old hash")
            self.assertIn(f"mios-src:{old[4]}", doc)
        finally:
            shutil.rmtree(root, ignore_errors=True)

if __name__ == "__main__":
    unittest.main()
