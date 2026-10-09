#!/usr/bin/env python3
# AI-hint: Automated unit test suite for task_500 AST Merge Syntax Gate & Regression Suite.
# AI-related: usr/libexec/mios/git/ast_merge.py, tests/test-ast-merge.py
"""Automated unit test suite for Tree-Sitter / AST Semantic Merge Resolver (task_500)."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_MODULE_PATH = os.path.join(_ROOT, "usr", "libexec", "mios", "git", "ast_merge.py")

spec = importlib.util.spec_from_file_location("ast_merge", _MODULE_PATH)
if spec and spec.loader:
    ast_merge = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = ast_merge
    spec.loader.exec_module(ast_merge)
else:
    raise ImportError(f"Could not load ast_merge module from {_MODULE_PATH}")

class TestAstMerge(unittest.TestCase):
    """Validates multi-language semantic 3-way AST merge resolution and conflict detection."""

    def setUp(self) -> None:
        self.resolver = ast_merge.AstMergeResolver(mock=True)

    def test_python_non_overlapping_functions(self) -> None:
        """Asserts clean resolution of distinct function additions in Python."""
        base = "def existing():\n    return 0\n"
        ours = "def existing():\n    return 0\n\ndef helper_ours():\n    return 1\n"
        theirs = "def existing():\n    return 0\n\ndef helper_theirs():\n    return 2\n"

        res = self.resolver.merge(base, ours, theirs, filename="sample.py")
        self.assertEqual(res.status, "success")
        self.assertIn("def helper_ours():", res.merged_content)
        self.assertIn("def helper_theirs():", res.merged_content)
        self.assertIn("def existing():", res.merged_content)

    def test_python_import_deduplication(self) -> None:
        """Asserts that concurrent import additions from ours and theirs are merged and deduplicated."""
        base = "import os\n"
        ours = "import os\nimport sys\n"
        theirs = "import os\nimport json\n"

        res = self.resolver.merge(base, ours, theirs, filename="sample.py")
        self.assertEqual(res.status, "success")
        self.assertIn("import json", res.merged_content)
        self.assertIn("import sys", res.merged_content)

    def test_python_semantic_conflict(self) -> None:
        """Asserts that modifying the same function in incompatible ways generates an AST conflict."""
        base = "def calculate(x):\n    return x + 1\n"
        ours = "def calculate(x):\n    return x + 10\n"
        theirs = "def calculate(x):\n    return x * 100\n"

        res = self.resolver.merge(base, ours, theirs, filename="sample.py")
        self.assertEqual(res.status, "conflict")
        self.assertGreater(len(res.conflict_nodes), 0)
        self.assertIn("calculate", res.conflict_nodes[0])

    def test_toml_dictionary_merge(self) -> None:
        """Asserts 3-way merge on TOML structures without clobbering keys."""
        base = '[section]\nkey1 = "val1"\n'
        ours = '[section]\nkey1 = "val1"\nkey_ours = "val_ours"\n'
        theirs = '[section]\nkey1 = "val1"\nkey_theirs = "val_theirs"\n'

        res = self.resolver.merge(base, ours, theirs, filename="config.toml")
        self.assertEqual(res.status, "success")
        self.assertIn("key_ours", res.merged_content)
        self.assertIn("key_theirs", res.merged_content)

    def test_json_dictionary_merge(self) -> None:
        """Asserts 3-way merge on JSON structures."""
        base = '{"shared": 1}'
        ours = '{"shared": 1, "feature_a": true}'
        theirs = '{"shared": 1, "feature_b": false}'

        res = self.resolver.merge(base, ours, theirs, filename="data.json")
        self.assertEqual(res.status, "success")
        parsed = json.loads(res.merged_content)
        self.assertEqual(parsed["shared"], 1)
        self.assertTrue(parsed["feature_a"])
        self.assertFalse(parsed["feature_b"])

    def test_shell_function_merge(self) -> None:
        """Asserts function extraction and merge for Shell scripts."""
        base = "func_base() {\n  echo base\n}\n"
        ours = "func_base() {\n  echo base\n}\nfunc_ours() {\n  echo ours\n}\n"
        theirs = "func_base() {\n  echo base\n}\nfunc_theirs() {\n  echo theirs\n}\n"

        res = self.resolver.merge(base, ours, theirs, filename="script.sh")
        self.assertEqual(res.status, "success")
        self.assertIn("func_ours", res.merged_content)
        self.assertIn("func_theirs", res.merged_content)

    def test_python_syntax_error_fallback(self) -> None:
        """Asserts that Python syntax errors gracefully fall back to line merge rather than deleting nodes."""
        base = "def valid():\n    return 1\n"
        ours = "def valid():\n    return 1\n"
        theirs = "def valid() incomplete syntax:\n"

        res = self.resolver.merge(base, ours, theirs, filename="broken.py")
        # Since base == ours, line fallback successfully resolves to theirs (or conflict if lines differ)
        self.assertEqual(res.status, "success")
        self.assertEqual(res.merged_content, theirs)

        # Conflicting syntax error with line changes
        ours_changed = "def valid():\n    return 2\n"
        res2 = self.resolver.merge(base, ours_changed, theirs, filename="broken.py")
        self.assertEqual(res2.status, "conflict")

    def test_cli_mock_execution(self) -> None:
        """Asserts CLI execution with --mock --json."""
        with patch("sys.argv", ["ast_merge.py", "--mock", "--json"]):
            with patch("builtins.print") as mock_print:
                ret = ast_merge.main()
                self.assertEqual(ret, 0)
                mock_print.assert_called()
                parsed = json.loads(mock_print.call_args[0][0])
                self.assertEqual(parsed["status"], "success")

if __name__ == "__main__":
    unittest.main()
