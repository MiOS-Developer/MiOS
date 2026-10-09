#!/usr/bin/env python3
# AI-hint: Consolidated unit test suite for MiOS Git Operations domain (AST merge fuzzing, pre-commit lint hooks, and multi-master DAG reconciliation) (task_3353 / GATECAT-01).
# AI-related: usr/libexec/mios/git/merge_fuzzer.py, usr/libexec/mios/git/pre_commit.py, usr/libexec/mios/git/reconcile_dag.py
"""Consolidated Git Operations Domain Test Suite.

Consolidates:
- Differential AST Git merge fuzzing and conflict simulation (test-git-merge-fuzzer.py)
- Git pre-commit linter and commit message hook validator (test-git-pre-commit.py)
- Multi-master Git DAG reconciliation and consensus signing (test-git-reconcile.py)
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_GIT_DIR = os.path.join(_ROOT, "usr", "libexec", "mios", "git")

if _GIT_DIR not in sys.path:
    sys.path.insert(0, _GIT_DIR)

from merge_fuzzer import MergeFuzzHarness

# Dynamic loader for pre_commit
_PRE_COMMIT_PATH = os.path.join(_GIT_DIR, "pre_commit.py")
spec_pre = importlib.util.spec_from_file_location("pre_commit", _PRE_COMMIT_PATH)
if spec_pre and spec_pre.loader:
    pre_commit = importlib.util.module_from_spec(spec_pre)
    sys.modules[spec_pre.name] = pre_commit
    spec_pre.loader.exec_module(pre_commit)
else:
    raise ImportError(f"Could not load pre_commit module from {_PRE_COMMIT_PATH}")

# Dynamic loader for reconcile_dag
_RECONCILE_PATH = os.path.join(_GIT_DIR, "reconcile_dag.py")
spec_rec = importlib.util.spec_from_file_location("reconcile_dag", _RECONCILE_PATH)
if spec_rec and spec_rec.loader:
    reconcile_dag = importlib.util.module_from_spec(spec_rec)
    sys.modules[spec_rec.name] = reconcile_dag
    spec_rec.loader.exec_module(reconcile_dag)
else:
    raise ImportError(f"Could not load reconcile_dag module from {_RECONCILE_PATH}")


# ============================================================================
# Domain 5.1: Differential AST Git Merge Fuzzer
# (Migrated from tests/test-git-merge-fuzzer.py)
# ============================================================================

class TestMergeFuzzHarness(unittest.TestCase):
    """Unit tests for MiOS differential AST git merge fuzzer."""

    def setUp(self):
        self.harness = MergeFuzzHarness(seed=1337, dry_run=True)

    def test_mutate_python_source_preserves_syntax(self):
        code = "def calculate_hash():\n    return 42\n"
        mutated, logs = self.harness.mutate_python_source(code)
        self.assertNotEqual(code, mutated)
        self.assertTrue(len(logs) > 0)
        ast.parse(mutated)

    def test_simulate_3way_ast_merge(self):
        base = "def foo():\n    pass\n"
        branch_a = "def foo():\n    return 1\n"
        branch_b = "def foo():\n    return 2\n"
        res = self.harness.simulate_3way_ast_merge(base, branch_a, branch_b)
        self.assertEqual(res["status"], "success")
        self.assertTrue(res["syntax_valid"])


# ============================================================================
# Domain 5.2: Git Pre-Commit Linter and Hook Manager
# (Migrated from tests/test-git-pre-commit.py)
# ============================================================================

class TestPreCommitLinter(unittest.TestCase):
    """Unit tests for PreCommitLinter."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="mios-test-precommit-")
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_mock_pre_commit_pass(self):
        linter = pre_commit.PreCommitLinter(repo_root=str(self.root), mock=True)
        res = linter.run_pre_commit()
        self.assertEqual(res.status, "pass")
        self.assertEqual(len(res.findings), 0)
        self.assertEqual(res.files_checked, 3)

    def test_python_syntax_error_detection(self):
        linter = pre_commit.PreCommitLinter(repo_root=str(self.root), mock=False)
        bad_py = "def broken(\n    return 42\n"
        findings = linter.lint_python_content("bad.py", bad_py)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].rule, "python-syntax")

    def test_json_syntax_error_detection(self):
        linter = pre_commit.PreCommitLinter(repo_root=str(self.root), mock=False)
        bad_json = '{"key": "value", trailing: }'
        findings = linter.lint_json_content("bad.json", bad_json)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].rule, "json-syntax")

    def test_forbidden_vendor_ai_url_detection(self):
        linter = pre_commit.PreCommitLinter(repo_root=str(self.root), mock=False)
        bad_content = 'url = "https://api.openai.com/v1/chat/completions"\n'
        findings = linter.lint_security_and_vendor("client.py", bad_content)
        self.assertTrue(any(f.rule == "unified-ai-redirects" for f in findings))

    def test_secret_detection(self):
        linter = pre_commit.PreCommitLinter(repo_root=str(self.root), mock=False)
        bad_content = 'token = "sk-abcdef12345678901234567890"\n'
        findings = linter.lint_security_and_vendor("secret.py", bad_content)
        self.assertTrue(any(f.rule == "no-hardcoded-secrets" for f in findings))

    def test_conventional_commit_validation(self):
        linter = pre_commit.PreCommitLinter(repo_root=str(self.root), mock=True)
        valid_msgs = [
            "feat(gpu): add declarative MIG slicer and CDI generator",
            "fix(ci): wire radosgw port into ssot quadlet allowlists",
            "docs: update architecture manual with edge mesh diagrams",
            "refactor(agent-pipe): optimize temporal decay vector scoring",
        ]
        for msg in valid_msgs:
            ok, err = linter.validate_commit_message(msg)
            self.assertTrue(ok, f"Expected '{msg}' to be valid, got err: {err}")

        invalid_msgs = [
            "WIP: random changes",
            "fixed bug",
            "",
            "unknown_type: do something",
        ]
        for msg in invalid_msgs:
            ok, _ = linter.validate_commit_message(msg)
            self.assertFalse(ok, f"Expected '{msg}' to be invalid")

    def test_cli_execution_check_mock(self):
        test_args = ["pre_commit.py", "--check", "--mock", "--json"]
        with patch.object(sys, "argv", test_args):
            exit_code = pre_commit.main()
            self.assertEqual(exit_code, 0)

    def test_cli_execution_install_hook_mock(self):
        test_args = ["pre_commit.py", "--install-hook", "--mock", "--json"]
        with patch.object(sys, "argv", test_args):
            exit_code = pre_commit.main()
            self.assertEqual(exit_code, 0)

    def test_cli_execution_validate_msg_mock(self):
        test_args = ["pre_commit.py", "--validate-msg", "feat(sec): add fido2 enrollment", "--mock", "--json"]
        with patch.object(sys, "argv", test_args):
            exit_code = pre_commit.main()
            self.assertEqual(exit_code, 0)


# ============================================================================
# Domain 5.3: Multi-Master Git DAG Reconciliation
# (Migrated from tests/test-git-reconcile.py)
# ============================================================================

class TestGitReconcile(unittest.TestCase):
    """Validates LCA computation, divergent branch reconciliation, and consensus signing."""

    def setUp(self) -> None:
        self.engine = reconcile_dag.DagReconcileEngine(mock=True)

    def test_find_lca_divergent(self) -> None:
        lca = self.engine.find_lca("l2222222", "p2222222")
        self.assertEqual(lca, "a1b2c3d4")

    def test_find_lca_same_commit(self) -> None:
        lca = self.engine.find_lca("l1111111", "l1111111")
        self.assertEqual(lca, "l1111111")

    def test_calculate_reconciliation_plan_consensus(self) -> None:
        plan = self.engine.calculate_reconciliation_plan("main", "peer/main", "l2222222", "p2222222")
        self.assertEqual(plan.strategy, "consensus_merge")
        self.assertEqual(plan.lca_hash, "a1b2c3d4")
        self.assertEqual(plan.commits_to_apply, ["p1111111", "p2222222"])

    def test_calculate_reconciliation_plan_fast_forward(self) -> None:
        plan = self.engine.calculate_reconciliation_plan("main", "peer/main", "a1b2c3d4", "l2222222")
        self.assertEqual(plan.strategy, "fast_forward")
        self.assertEqual(plan.commits_to_apply, ["l1111111", "l2222222"])

    def test_sign_consensus_commit(self) -> None:
        commit, record = self.engine.sign_consensus_commit(
            parent_hashes=["l2222222", "p2222222"],
            tree_hash="tree_reconciled_01",
            node_id="node-blade-4",
            signing_key="ed25519_secret_blade4",
        )
        self.assertIsNotNone(commit.signature)
        self.assertEqual(commit.parent_hashes, ["l2222222", "p2222222"])
        self.assertIn("node-blade-4", record.participating_nodes)
        self.assertIn("node-blade-4", record.signatures)

    def test_full_reconcile_workflow(self) -> None:
        res = self.engine.reconcile(
            local_branch="main",
            peer_branch="peer/main",
            local_head="l2222222",
            peer_head="p2222222",
            node_id="node-local",
        )
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["strategy"], "consensus_merge")
        self.assertIn("consensus_commit", res)

    def test_sync_remotes_mock(self) -> None:
        sync_res = self.engine.sync_remotes(repo_path=".", remotes=["forgejo", "github"])
        self.assertEqual(sync_res["status"], "success")
        self.assertEqual(sync_res["synced_remotes"]["forgejo"], "synced")
        self.assertEqual(sync_res["synced_remotes"]["github"], "synced")

    def test_cli_mock_json(self) -> None:
        with patch("sys.argv", ["reconcile_dag.py", "--mock", "--json"]):
            with patch("builtins.print") as mock_print:
                ret = reconcile_dag.main()
                self.assertEqual(ret, 0)
                mock_print.assert_called()
                parsed = json.loads(mock_print.call_args[0][0])
                self.assertEqual(parsed["status"], "success")


if __name__ == "__main__":
    unittest.main()
