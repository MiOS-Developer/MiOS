#!/usr/bin/env python3
# AI-hint: Characterization tests for MiOS native AI metadata extractor and validator.
# AI-related: usr/libexec/mios/mios-ai-metadata.py, usr/lib/mios/schemas/ai_metadata.schema.json
# AI-functions: TestAIMetadata, main

import io
import contextlib
import tempfile
import unittest
import os
import sys
import subprocess
from pathlib import Path
from unittest.mock import patch

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import importlib.util
spec = importlib.util.spec_from_file_location("mios_ai_metadata", os.path.join(_HERE, "mios-ai-metadata.py"))
mios_ai_metadata = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mios_ai_metadata)


class TestAIMetadata(unittest.TestCase):
    def _git(self, root, *args, input=None):
        return subprocess.run(
            ["git", *args], cwd=root, input=input, text=True,
            check=True, capture_output=True,
        ).stdout.strip()

    def _source_index(self, root):
        self._git(root, "init", "-q")
        Path(root, "canonical.conf").write_text("# AI-hint: Canonical source.\n", encoding="utf-8")
        Path(root, "alias.conf").write_text("# AI-hint: Alias must not be indexed.\n", encoding="utf-8")
        self._git(root, "add", "canonical.conf")
        oid = self._git(root, "hash-object", "-w", "--stdin", input="canonical.conf")
        self._git(root, "update-index", "--add", "--cacheinfo", f"120000,{oid},alias.conf")
        return oid

    def test_index_modes_exclude_link_placeholders_from_both_censuses(self):
        with tempfile.TemporaryDirectory() as root:
            self._source_index(root)
            catalog = mios_ai_metadata.build_metadata_catalog(root)
            self.assertEqual(catalog["total_files_scanned"], 2)
            self.assertEqual([entry["path"] for entry in catalog["entries"]], ["canonical.conf"])
            from mios_comments import iter_source_files
            self.assertEqual([rel for rel, _ in iter_source_files(root)], ["canonical.conf"])
            Path(root, "untracked.conf").write_text("# AI-hint: Private untracked source.\n", encoding="utf-8")
            self.assertEqual(mios_ai_metadata.build_metadata_catalog(root), catalog)

    @unittest.skipIf(os.name == "nt", "real link control runs on the Linux builder")
    def test_real_link_and_placeholder_generate_identical_catalogs(self):
        with tempfile.TemporaryDirectory() as root:
            self._source_index(root)
            before = mios_ai_metadata.build_metadata_catalog(root)
            Path(root, "alias.conf").unlink()
            Path(root, "alias.conf").symlink_to("canonical.conf")
            self.assertEqual(mios_ai_metadata.build_metadata_catalog(root), before)
            from mios_comments import iter_source_files
            self.assertEqual([rel for rel, _ in iter_source_files(root)], ["canonical.conf"])

    def test_unmerged_index_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            oid = self._source_index(root)
            self._git(root, "update-index", "-z", "--index-info", input=f"0 {'0' * 40}\talias.conf\0" f"120000 {oid} 1\talias.conf\0")
            with self.assertRaisesRegex(RuntimeError, "unmerged source index entry: alias.conf"):
                mios_ai_metadata.build_metadata_catalog(root)

    def test_unreadable_git_checkout_does_not_scan_untracked_content(self):
        with tempfile.TemporaryDirectory() as root:
            self._source_index(root)
            with patch("mios_comments.subprocess.run", side_effect=OSError("git unavailable")):
                with self.assertRaisesRegex(RuntimeError, "cannot read the tracked source index"):
                    mios_ai_metadata.build_metadata_catalog(root)

    def test_standalone_source_fixture_is_supported(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root, "canonical.conf").write_text("# AI-hint: Standalone source.\n", encoding="utf-8")
            with patch.dict(os.environ, {"GIT_DIR": os.path.join(root, "nonexistent_git_dir")}):
                self.assertEqual(mios_ai_metadata.build_metadata_catalog(root)["total_metadata_entries"], 1)

    def test_extract_python_metadata(self):
        content = """#!/usr/bin/env python3
# AI-hint: Test python module purpose.
# AI-related: /etc/mios/foo.conf, mios-service
# AI-functions: foo, bar, BazClass
# AI-doc: usr/share/doc/mios/manual/tests.md

def foo(): pass
"""
        meta = mios_ai_metadata.extract_ai_header_metadata(content, "usr/lib/test.py")
        self.assertIsNotNone(meta)
        self.assertEqual(meta["hint"], "Test python module purpose.")
        self.assertEqual(meta["related"], ["/etc/mios/foo.conf", "mios-service"])
        self.assertEqual(meta["functions"], ["foo", "bar", "BazClass"])
        self.assertEqual(meta["doc"], "usr/share/doc/mios/manual/tests.md")
        self.assertEqual(meta["comment_style"], "hash")
        self.assertTrue(meta["has_shebang"])

    def test_extract_markdown_metadata(self):
        content = """<!-- AI-hint: Markdown guide for operators. -->
<!-- AI-related: /usr/share/doc/mios/concepts/architecture.md -->
# Guide Title
"""
        meta = mios_ai_metadata.extract_ai_header_metadata(content, "docs/guide.md")
        self.assertIsNotNone(meta)
        self.assertEqual(meta["hint"], "Markdown guide for operators.")
        self.assertEqual(meta["related"], ["/usr/share/doc/mios/concepts/architecture.md"])
        self.assertEqual(meta["comment_style"], "xml")
        self.assertFalse(meta["has_shebang"])

    def test_extract_typescript_metadata(self):
        content = """// AI-hint: TypeScript service schema.
// AI-related: schema.ts
// AI-functions: buildSchema, validate

export function buildSchema() {}
"""
        meta = mios_ai_metadata.extract_ai_header_metadata(content, "src/schema.ts")
        self.assertIsNotNone(meta)
        self.assertEqual(meta["hint"], "TypeScript service schema.")
        self.assertEqual(meta["related"], ["schema.ts"])
        self.assertEqual(meta["functions"], ["buildSchema", "validate"])
        self.assertEqual(meta["comment_style"], "slash")

    def test_schema_compliance_predicate(self):
        catalog = {
            "format": "openai_strict_schema_v1",
            "entries": [
                {
                    "path": "test/path.sh",
                    "hint": "Test hint",
                    "related": ["dep.sh"],
                    "functions": ["run"],
                    "doc": None,
                    "comment_style": "hash",
                    "has_shebang": True,
                }
            ],
        }
        self.assertTrue(mios_ai_metadata.validate_schema_compliance(catalog))

    def _catalog(self, hint):
        return {
            "format": "openai_strict_schema_v1",
            "total_metadata_entries": 1,
            "entries": [
                {
                    "path": "usr/lib/a.sh",
                    "hint": hint,
                    "related": [],
                    "functions": [],
                    "doc": None,
                    "comment_style": "hash",
                    "has_shebang": True,
                }
            ],
        }

    def _write(self, d, text):
        path = os.path.join(d, "metadata.json")
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        return path

    def test_check_fresh_passes_on_identical_export(self):
        cat = self._catalog("same")
        with tempfile.TemporaryDirectory() as d:
            path = self._write(d, mios_ai_metadata.render_catalog_json(cat))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(mios_ai_metadata.check_export_fresh(cat, path), 0)

    def test_check_fresh_names_the_stale_entry(self):
        stale = mios_ai_metadata.render_catalog_json(self._catalog("old"))
        with tempfile.TemporaryDirectory() as d:
            path = self._write(d, stale)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                rc = mios_ai_metadata.check_export_fresh(self._catalog("new"), path)
        self.assertEqual(rc, 1)
        self.assertIn("entry usr/lib/a.sh: differs in hint", err.getvalue())

    def test_check_fresh_catches_formatting_only_drift(self):
        cat = self._catalog("same")
        with tempfile.TemporaryDirectory() as d:
            path = self._write(d, mios_ai_metadata.render_catalog_json(cat) + "\n")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                rc = mios_ai_metadata.check_export_fresh(cat, path)
        self.assertEqual(rc, 1)
        self.assertIn("formatting drift", err.getvalue())

    def test_diff_reports_added_and_removed_entries(self):
        old = self._catalog("x")
        new = self._catalog("x")
        new["entries"][0] = dict(new["entries"][0], path="usr/lib/b.sh")
        lines = mios_ai_metadata.diff_catalog_entries(old, new)
        self.assertIn("entry usr/lib/b.sh: has an AI header but is missing from the tracked file", lines)
        self.assertTrue(any(l.startswith("entry usr/lib/a.sh: in the tracked file") for l in lines))


if __name__ == "__main__":
    unittest.main()
