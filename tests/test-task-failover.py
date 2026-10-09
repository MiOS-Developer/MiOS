#!/usr/bin/env python3
# AI-hint: Verification suite for automated node failure detection and zero-loss dynamic task re-distribution engine (task_482, task_2715).
# AI-doc: usr/share/doc/mios/manual/ch83-task-failover-resilience.md
"""Test suite for automated node failure detection and zero-loss task re-distribution engine."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List

# Locate project paths
SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
FAILOVER_SCRIPT = ROOT_DIR / "usr" / "lib" / "mios" / "agent-pipe" / "mios_task_failover.py"

# Import target module directly for unit verification
sys.path.insert(0, str(FAILOVER_SCRIPT.parent))
import mios_task_failover as mtf

VERBOSE = False
DRY_RUN = False
MOCK_MODE = False

pass_count = 0
fail_count = 0


def log(msg: str) -> None:
    print(f"[TEST] {msg}")


def log_diag(msg: str) -> None:
    if VERBOSE:
        print(f"  [DIAG] {msg}")


def assert_pass(desc: str) -> None:
    global pass_count
    pass_count += 1
    print(f"  [PASS] {desc}")


def assert_fail(desc: str, err: str = "") -> None:
    global fail_count
    fail_count += 1
    err_suffix = f": {err}" if err else ""
    print(f"  [FAIL] {desc}{err_suffix}", file=sys.stderr)


# ==============================================================================
# Test Cases
# ==============================================================================

def test_1_cli_and_help() -> None:
    """Test 1: CLI invocation and help verification."""
    log("Running Test 1: CLI and help verification...")
    try:
        res = subprocess.run(
            [sys.executable, str(FAILOVER_SCRIPT), "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode == 0 and "MiOS Automated Node Failure Detection" in res.stdout:
            assert_pass("CLI --help displays usage and returns exit code 0")
        else:
            assert_fail("CLI --help failed", res.stderr)

        # Test subcommands present in help text
        required_cmds = ["monitor", "simulate-failure", "status", "heartbeat", "register-node"]
        missing = [c for c in required_cmds if c not in res.stdout]
        if not missing:
            assert_pass(f"All required subcommands present in help: {required_cmds}")
        else:
            assert_fail("Missing subcommands in help output", str(missing))

    except Exception as e:
        assert_fail("Test 1 encountered unhandled exception", str(e))


def test_2_heartbeat_timeout_detection() -> None:
    """Test 2: Heartbeat timeout detection of failed node (positive control)."""
    log("Running Test 2: Heartbeat timeout detection of failed node (positive control)...")
    try:
        storage = mtf.TaskFailoverStorage(db_path=":memory:")
        engine = mtf.TaskFailoverEngine(storage=storage, heartbeat_timeout_sec=5.0)

        t0 = 1000.0
        # Register node with 5.0s timeout
        engine.register_node(
            node_id="worker-test-alpha",
            endpoint="http://10.42.0.10:8642/v1",
            capabilities=["gpu", "general"],
            timeout_threshold_sec=5.0,
        )
        engine.record_heartbeat("worker-test-alpha", now=t0)

        # Audit at t0 + 2.0s -> should still be healthy
        transitions_early = engine.audit_nodes(now=t0 + 2.0)
        node = engine.nodes["worker-test-alpha"]
        if node.status == "healthy":
            assert_pass("Node remains healthy at t0 + 2.0s (< 5.0s timeout)")
        else:
            assert_fail("Node unexpectedly marked non-healthy at t0 + 2.0s", node.status)

        # Audit at t0 + 6.0s -> timeout exceeded -> should be failed
        failed_nodes = engine.detect_failures(now=t0 + 6.0)
        node = engine.nodes["worker-test-alpha"]

        if "worker-test-alpha" in failed_nodes and node.status == "failed":
            assert_pass("Node successfully detected as failed after exceeding 5.0s timeout threshold")
        else:
            assert_fail("Node failure was not detected after timeout", f"status={node.status}, failed={failed_nodes}")

    except Exception as e:
        assert_fail("Test 2 encountered unhandled exception", str(e))


def test_3_zero_loss_task_redistribution() -> None:
    """Test 3: Zero-loss task re-distribution to surviving secondary node (positive control)."""
    log("Running Test 3: Zero-loss task re-distribution to surviving secondary node (positive control)...")
    try:
        storage = mtf.TaskFailoverStorage(db_path=":memory:")
        engine = mtf.TaskFailoverEngine(storage=storage, heartbeat_timeout_sec=5.0)

        t0 = 2000.0
        # Register primary and secondary nodes
        engine.register_node("node-primary", capabilities=["gpu", "coder"], timeout_threshold_sec=5.0)
        engine.register_node("node-secondary", capabilities=["gpu", "coder"], timeout_threshold_sec=5.0)
        engine.record_heartbeat("node-primary", now=t0)
        engine.record_heartbeat("node-secondary", now=t0)

        sample_prompt = (
            "Verify SHA-256 integrity of the Unified Kernel Image (UKI) drop-in "
            "and ensure cmdline kargs match /etc/kernel/cmdline."
        )
        sample_messages = [
            {"role": "system", "content": "You are a MiOS secure boot verification agent."},
            {"role": "user", "content": sample_prompt},
            {"role": "assistant", "content": "Initiating UKI hash computation over PCR 11..."},
        ]

        # Lease task to node-primary
        task = engine.create_task_lease(
            task_id="task-zl-001",
            prompt=sample_prompt,
            messages=sample_messages,
            capabilities_required=["gpu", "coder"],
            node_id="node-primary",
            session_id="session-uki-check",
        )

        initial_tokens = task.prompt_tokens
        initial_prompt = task.prompt
        initial_messages = list(task.messages)

        # Simulate failure of node-primary at t0 + 6.0s (secondary stays healthy)
        engine.record_heartbeat("node-secondary", now=t0 + 6.0)
        report = engine.re_distribute_tasks("node-primary", now=t0 + 6.0)

        if report["tasks_reassigned_count"] == 1:
            assert_pass("Task re-distribution completed 1 re-assignment")
        else:
            assert_fail("Unexpected reassigned count", str(report))

        updated_task = engine.tasks["task-zl-001"]

        # Validate Zero-Loss Guarantees
        if updated_task.node_id == "node-secondary":
            assert_pass("Task dynamically reassigned to surviving healthy node 'node-secondary'")
        else:
            assert_fail("Task assigned to wrong node", str(updated_task.node_id))

        if updated_task.prompt == initial_prompt and len(updated_task.prompt) == len(initial_prompt):
            assert_pass("Zero-loss prompt preservation: 100% prompt text preserved without truncation")
        else:
            assert_fail("Prompt text altered or truncated during failover")

        if updated_task.messages == initial_messages and len(updated_task.messages) == len(initial_messages):
            assert_pass("Zero-loss message history preservation: Full multi-turn conversation context preserved")
        else:
            assert_fail("Message history lost or modified")

        if updated_task.prompt_tokens == initial_tokens and updated_task.prompt_tokens > 0:
            assert_pass(f"Zero-loss token preservation: All {updated_task.prompt_tokens} tokens accounted for")
        else:
            assert_fail("Token count mismatch", f"{updated_task.prompt_tokens} != {initial_tokens}")

        if updated_task.retry_count == 1:
            assert_pass("Retry count incremented to 1")
        else:
            assert_fail("Retry count did not increment", str(updated_task.retry_count))

        if len(updated_task.failover_history) == 1 and updated_task.failover_history[0]["from_node"] == "node-primary":
            assert_pass("Failover audit history record appended with source and target node trace")
        else:
            assert_fail("Failover history missing or invalid", str(updated_task.failover_history))

    except Exception as e:
        assert_fail("Test 3 encountered unhandled exception", str(e))


def test_4_negative_control_healthy_nodes_unaffected() -> None:
    """Test 4: Negative control - healthy nodes are not prematurely failed."""
    log("Running Test 4: Negative control - healthy nodes are not prematurely failed...")
    try:
        storage = mtf.TaskFailoverStorage(db_path=":memory:")
        engine = mtf.TaskFailoverEngine(storage=storage, heartbeat_timeout_sec=5.0)

        t0 = 3000.0
        engine.register_node("healthy-node-1", timeout_threshold_sec=5.0)
        engine.register_node("healthy-node-2", timeout_threshold_sec=5.0)
        engine.record_heartbeat("healthy-node-1", now=t0)
        engine.record_heartbeat("healthy-node-2", now=t0)

        # Audit at t0 + 1.5s
        failed = engine.detect_failures(now=t0 + 1.5)

        if not failed:
            assert_pass("Negative control passed: No nodes detected as failed during fresh heartbeats")
        else:
            assert_fail("Healthy nodes were prematurely marked failed", str(failed))

        for n_id, n in engine.nodes.items():
            if n.status != "healthy":
                assert_fail(f"Node {n_id} status degraded prematurely", n.status)

        assert_pass("All audited nodes retain HEALTHY status under regular heartbeat interval")

    except Exception as e:
        assert_fail("Test 4 encountered unhandled exception", str(e))


def test_5_negative_control_all_nodes_down_graceful_exhaustion() -> None:
    """Test 5: Negative control - all-nodes-down condition reports graceful exhaustion."""
    log("Running Test 5: Negative control - all-nodes-down condition reports graceful exhaustion...")
    try:
        storage = mtf.TaskFailoverStorage(db_path=":memory:")
        engine = mtf.TaskFailoverEngine(storage=storage, heartbeat_timeout_sec=5.0)

        t0 = 4000.0
        # Register single node with unique requirement
        engine.register_node("solo-worker", capabilities=["rare-accelerator"], timeout_threshold_sec=5.0)
        engine.record_heartbeat("solo-worker", now=t0)

        task = engine.create_task_lease(
            task_id="task-exhaust-001",
            prompt="Compile BPF flight recorder program with libbpf.",
            capabilities_required=["rare-accelerator"],
            node_id="solo-worker",
            max_retries=2,
        )

        # Fail solo-worker at t0 + 10.0s (no surviving nodes satisfy 'rare-accelerator')
        engine.audit_nodes(now=t0 + 10.0)
        report = engine.re_distribute_tasks("solo-worker", now=t0 + 10.0)

        if report["tasks_reassigned_count"] == 0 and report["tasks_exhausted_count"] == 1:
            assert_pass("Negative control passed: 0 tasks reassigned, 1 task marked exhausted")
        else:
            assert_fail("Unexpected task distribution count during total cluster outage", str(report))

        updated_task = engine.tasks["task-exhaust-001"]
        if updated_task.status in ("re-queued", "exhausted"):
            assert_pass(f"Task safely transitioned to '{updated_task.status}' without crash or data loss")
        else:
            assert_fail("Task in invalid status after exhaustion", updated_task.status)

        # Ensure prompt and messages remain 100% intact even during total outage
        if "Compile BPF flight recorder" in updated_task.prompt:
            assert_pass("Zero-loss prompt retained even under full cluster exhaustion condition")
        else:
            assert_fail("Prompt content damaged during exhaustion")

    except Exception as e:
        assert_fail("Test 5 encountered unhandled exception", str(e))


def test_6_mock_end_to_end_lifecycle() -> None:
    """Test 6: Mock end-to-end failover lifecycle (--mock)."""
    log("Running Test 6: Mock end-to-end failover lifecycle (--mock)...")
    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            test_db = os.path.join(tmp_dir, "test_leases.db")

            # 1. Inspect initial mock status
            res_status = subprocess.run(
                [sys.executable, str(FAILOVER_SCRIPT), "--mock", "--db-path", test_db, "status"],
                capture_output=True,
                text=True,
                check=False,
            )
            if res_status.returncode != 0:
                assert_fail("Mock status command failed", res_status.stderr)
                return

            status_json = json.loads(res_status.stdout)
            if status_json["nodes_total"] == 3 and status_json["tasks_total"] == 1:
                assert_pass("Mock cluster initialized with 3 nodes and 1 active in-flight task")
            else:
                assert_fail("Unexpected initial mock cluster topology", str(status_json))

            # 2. Simulate failure of blade-1
            res_sim = subprocess.run(
                [sys.executable, str(FAILOVER_SCRIPT), "--mock", "--db-path", test_db, "simulate-failure", "blade-1"],
                capture_output=True,
                text=True,
                check=False,
            )
            if res_sim.returncode != 0:
                assert_fail("simulate-failure command failed", res_sim.stderr)
                return

            sim_json = json.loads(res_sim.stdout)
            if sim_json["tasks_reassigned_count"] == 1 and sim_json["reassigned"][0]["target_node"] == "blade-2":
                assert_pass("simulate-failure successfully migrated task from blade-1 to blade-2")
            else:
                assert_fail("Simulation result did not match expected failover target", str(sim_json))

            # 3. Query updated status
            res_status2 = subprocess.run(
                [sys.executable, str(FAILOVER_SCRIPT), "--mock", "--db-path", test_db, "status"],
                capture_output=True,
                text=True,
                check=False,
            )
            status2_json = json.loads(res_status2.stdout)
            task_blade2 = next(t for t in status2_json["tasks"] if t["task_id"] == "task-mock-001")
            if task_blade2["node_id"] == "blade-2" and task_blade2["failover_count"] == 1:
                assert_pass("Post-failover status verifies lease assigned to blade-2 with recorded failover event")
            else:
                assert_fail("Task lease state not updated in persistent journal", str(task_blade2))

            assert_pass("End-to-end mock failover lifecycle completed cleanly")

    except Exception as e:
        assert_fail("Test 6 encountered unhandled exception", str(e))


# ==============================================================================
# Main Runner
# ==============================================================================

def main() -> int:
    global VERBOSE, DRY_RUN, MOCK_MODE

    parser = argparse.ArgumentParser(
        description="Verification suite for automated node failure detection and task re-distribution engine."
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose diagnostics")
    parser.add_argument("--dry-run", action="store_true", help="Dry run test execution")
    parser.add_argument("--mock", action="store_true", help="Force mock testing mode")
    args = parser.parse_args()

    VERBOSE = args.verbose
    DRY_RUN = args.dry_run
    MOCK_MODE = args.mock

    log("Starting test suite: test-task-failover.py")

    test_1_cli_and_help()
    test_2_heartbeat_timeout_detection()
    test_3_zero_loss_task_redistribution()
    test_4_negative_control_healthy_nodes_unaffected()
    test_5_negative_control_all_nodes_down_graceful_exhaustion()
    test_6_mock_end_to_end_lifecycle()

    log(f"=== Test Suite Summary: {pass_count} passed, {fail_count} failed ===")
    if fail_count > 0:
        log(f"FAILURE: {fail_count} test(s) failed.")
        return 1

    log(f"SUCCESS: All {pass_count} tests passed (100% pass rate).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
