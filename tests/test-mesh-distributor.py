#!/usr/bin/env python3
# AI-hint: Verification suite for multi-node dynamic AI workload partitioner and capability-aware task router (task_481, task_2714).
# AI-doc: usr/share/doc/mios/manual/ch82-mesh-workload-partitioning.md
"""Test suite for multi-node dynamic AI workload partitioner and capability-aware router."""

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
DISTRIBUTOR_SCRIPT = ROOT_DIR / "usr" / "lib" / "mios" / "agent-pipe" / "mios_mesh_distributor.py"
MANUAL_FILE = ROOT_DIR / "usr" / "share" / "doc" / "mios" / "manual" / "ch82-mesh-workload-partitioning.md"

# Import distributor library module directly
sys.path.insert(0, str(DISTRIBUTOR_SCRIPT.parent))
import mios_mesh_distributor as mmd

VERBOSE = False
DRY_RUN = False
MOCK_MODE = True

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
# Test 1: CLI and help verification
# ==============================================================================
def test_1_cli_and_help() -> None:
    log("Test 1: CLI and help verification")

    # 1a. Verify script existence and executable bit
    if DISTRIBUTOR_SCRIPT.exists():
        assert_pass(f"Distributor script exists at {DISTRIBUTOR_SCRIPT}")
    else:
        assert_fail(f"Distributor script missing at {DISTRIBUTOR_SCRIPT}")

    if os.access(DISTRIBUTOR_SCRIPT, os.X_OK):
        assert_pass("Distributor script has executable bit (+x)")
    else:
        assert_fail("Distributor script is not executable")

    # 1b. CLI help options (-h, --help)
    res_h = subprocess.run([sys.executable, str(DISTRIBUTOR_SCRIPT), "-h"], capture_output=True, text=True)
    if res_h.returncode == 0 and "usage:" in res_h.stdout and "route" in res_h.stdout and "status" in res_h.stdout:
        assert_pass("Flag -h displayed usage and exited 0")
    else:
        assert_fail("Flag -h failed", res_h.stderr)

    res_help = subprocess.run([sys.executable, str(DISTRIBUTOR_SCRIPT), "--help"], capture_output=True, text=True)
    if res_help.returncode == 0 and "usage:" in res_help.stdout:
        assert_pass("Flag --help displayed usage and exited 0")
    else:
        assert_fail("Flag --help failed", res_help.stderr)

    # 1c. Subcommand: status --mock
    res_status = subprocess.run([sys.executable, str(DISTRIBUTOR_SCRIPT), "status", "--mock"], capture_output=True, text=True)
    if res_status.returncode == 0:
        try:
            status_data = json.loads(res_status.stdout)
            if "total_nodes" in status_data and "nodes" in status_data and status_data["total_nodes"] >= 4:
                assert_pass("Subcommand 'status --mock' returned valid cluster topology JSON")
            else:
                assert_fail("Subcommand 'status --mock' missing expected keys", str(status_data))
        except json.JSONDecodeError as e:
            assert_fail("Subcommand 'status --mock' output not valid JSON", str(e))
    else:
        assert_fail("Subcommand 'status --mock' exited non-zero", res_status.stderr)

    # 1d. Subcommand: route --mock --dry-run
    res_route = subprocess.run(
        [sys.executable, str(DISTRIBUTOR_SCRIPT), "route", "--mock", "--dry-run", "--prompt", "Quick test prompt."],
        capture_output=True,
        text=True,
    )
    if res_route.returncode == 0:
        try:
            route_data = json.loads(res_route.stdout)
            if route_data.get("dry_run") is True and "partitions" in route_data:
                assert_pass("Subcommand 'route --mock --dry-run' returned valid partition blueprint")
            else:
                assert_fail("Subcommand 'route --mock --dry-run' payload mismatch", str(route_data))
        except json.JSONDecodeError as e:
            assert_fail("Subcommand 'route --mock --dry-run' output not valid JSON", str(e))
    else:
        assert_fail("Subcommand 'route --mock --dry-run' exited non-zero", res_route.stderr)

    # 1e. Verify manual chapter exists
    if MANUAL_FILE.exists():
        assert_pass(f"Manual documentation exists at {MANUAL_FILE}")
    else:
        assert_fail(f"Manual documentation missing at {MANUAL_FILE}")


# ==============================================================================
# Test 2: Capability routing match (positive control)
# ==============================================================================
def test_2_capability_routing_match() -> None:
    log("Test 2: Capability routing match (positive control)")

    topo = mmd.MeshTopology()
    topo.discover(mock=True)
    router = mmd.CapabilityAwareRouter(topo)

    # 2a. Route heavy reasoning -> blade-01 (GPU vLLM lane, high VRAM headroom)
    subtask_heavy = mmd.Subtask(
        subtask_id="t-reason-1",
        role=mmd.ROLE_HEAVY_REASONING,
        prompt="Prove architectural convergence and formal invariants.",
    )
    dec_heavy = router.route_subtask(subtask_heavy)
    log_diag(f"Heavy reasoning decision: {dec_heavy.to_dict()}")

    # Both blade-01 and blade-03 have heavy_reasoning, but blade-01 has lower queue (1 vs 4)
    # and lower VRAM usage (4096MB vs 18432MB)
    if dec_heavy.target_node_id == "blade-01":
        assert_pass(f"Heavy reasoning routed to optimal GPU node '{dec_heavy.target_node_id}' with score {dec_heavy.score}")
    else:
        assert_fail(f"Heavy reasoning routed to unexpected node '{dec_heavy.target_node_id}' (expected blade-01)")

    # 2b. Route embeddings -> blade-02 (nomic-embed, online, queue 0)
    subtask_embed = mmd.Subtask(
        subtask_id="t-embed-1",
        role=mmd.ROLE_EMBEDDINGS,
        prompt="Calculate semantic vector embeddings for chunk similarity.",
    )
    dec_embed = router.route_subtask(subtask_embed)
    log_diag(f"Embeddings decision: {dec_embed.to_dict()}")

    # blade-02 and blade-04 have embeddings, but blade-02 is online with queue 0, while blade-04 is degraded
    if dec_embed.target_node_id == "blade-02":
        assert_pass(f"Embeddings routed to optimal light inference node '{dec_embed.target_node_id}' (queue={dec_embed.metrics_snapshot['active_queue_depth']})")
    else:
        assert_fail(f"Embeddings routed to unexpected node '{dec_embed.target_node_id}' (expected blade-02)")

    # 2c. Route tool sandbox -> blade-01
    subtask_sandbox = mmd.Subtask(
        subtask_id="t-sandbox-1",
        role=mmd.ROLE_TOOL_SANDBOX,
        prompt="Run unit tests inside bwrap seccomp sandbox.",
    )
    dec_sandbox = router.route_subtask(subtask_sandbox)
    log_diag(f"Tool sandbox decision: {dec_sandbox.to_dict()}")

    if dec_sandbox.target_node_id == "blade-01":
        assert_pass(f"Tool sandbox routed to online sandbox node '{dec_sandbox.target_node_id}'")
    else:
        assert_fail(f"Tool sandbox routed to unexpected node '{dec_sandbox.target_node_id}'")


# ==============================================================================
# Test 3: Composite workflow decomposition into parallel subtasks (positive control)
# ==============================================================================
def test_3_composite_workflow_decomposition() -> None:
    log("Test 3: Composite workflow decomposition into parallel subtasks (positive control)")

    distributor = mmd.MeshDistributor()
    distributor.topology.discover(mock=True)

    # 3a. Multi-step explicit prompt
    composite_prompt = (
        "Step 1: Extract vector embeddings from nomic-embed database.\n"
        "Step 2: Synthesize a python class for token bucket rate limiting.\n"
        "Step 3: Run unit tests inside container sandbox."
    )

    subtasks = distributor.partitioner.analyze_and_partition(composite_prompt)
    if len(subtasks) == 3:
        assert_pass(f"Multi-step prompt successfully decomposed into {len(subtasks)} discrete subtasks")
    else:
        assert_fail(f"Expected 3 subtasks, got {len(subtasks)}: {[s.to_dict() for s in subtasks]}")

    expected_roles = [mmd.ROLE_EMBEDDINGS, mmd.ROLE_CODING, mmd.ROLE_TOOL_SANDBOX]
    actual_roles = [s.role for s in subtasks]
    if actual_roles == expected_roles:
        assert_pass(f"Subtask roles correctly classified: {actual_roles}")
    else:
        assert_fail(f"Subtask roles mismatch. Expected {expected_roles}, got {actual_roles}")

    # 3b. Dispatch and gather in mock mode
    response = distributor.dispatch_workload(composite_prompt, mock=True)
    log_diag(f"Dispatched composite response: {json.dumps(response, indent=2)}")

    # Verify standard OpenAI chat completion schema
    if (
        response.get("object") == "chat.completion"
        and "choices" in response
        and len(response["choices"]) > 0
        and response["choices"][0]["message"]["role"] == "assistant"
    ):
        assert_pass("Merged composite response satisfies standard OpenAI /v1/chat/completions wire format")
    else:
        assert_fail("Response failed OpenAI schema validation", str(response))

    # Verify mesh routing metadata
    mesh_meta = response.get("mesh_routing", {})
    if mesh_meta.get("partitioned") is True and mesh_meta.get("subtask_count") == 3:
        assert_pass("Response metadata accurately recorded multi-subtask partitioning")
    else:
        assert_fail("Mesh routing metadata missing or incorrect", str(mesh_meta))

    dispatches = mesh_meta.get("dispatches", [])
    if len(dispatches) == 3 and all(d.get("status") == "success" for d in dispatches):
        assert_pass(f"All {len(dispatches)} distributed node dispatches succeeded and gathered")
    else:
        assert_fail("Dispatches incomplete or contained errors", str(dispatches))


# ==============================================================================
# Test 4: Negative control - detects and handles when no node has capability
# ==============================================================================
def test_4_negative_control_missing_capability() -> None:
    log("Test 4: Negative control - detects and handles when no node has capability")

    # 4a. Cluster where required role is completely absent
    limited_topology = mmd.MeshTopology()
    n_cpu_only = mmd.MeshNode(
        node_id="cpu-only-blade",
        hostname="cpu01.mesh.local",
        mesh_ip="10.244.0.10",
        is_blade=True,
        status=mmd.STATUS_ONLINE,
        roles=[mmd.ROLE_LIGHT_CHAT, mmd.ROLE_CODING],
        metrics=mmd.NodeMetrics(),
    )
    limited_topology.add_node(n_cpu_only)

    router_limited = mmd.CapabilityAwareRouter(limited_topology)

    # Request heavy_reasoning which requires GPU VFIO that cpu-only blade lacks
    subtask_heavy = mmd.Subtask(
        subtask_id="t-unsupported",
        role=mmd.ROLE_HEAVY_REASONING,
        prompt="Requires vLLM with 24GB VRAM.",
    )

    try:
        router_limited.route_subtask(subtask_heavy)
        assert_fail("Router failed to raise NoCapableNodeError when capability is absent")
    except mmd.NoCapableNodeError as e:
        assert_pass(f"Negative Control: Router cleanly raised NoCapableNodeError: {e}")
    except Exception as e:
        assert_fail("Unexpected exception type raised", f"{type(e).__name__}: {e}")

    # 4b. Cluster where nodes with the role exist, but ALL are offline
    offline_topology = mmd.MeshTopology()
    n_offline_gpu = mmd.MeshNode(
        node_id="offline-blade",
        hostname="off01.mesh.local",
        mesh_ip="10.244.0.11",
        is_blade=True,
        status=mmd.STATUS_OFFLINE,
        roles=[mmd.ROLE_HEAVY_REASONING],
        metrics=mmd.NodeMetrics(),
    )
    offline_topology.add_node(n_offline_gpu)

    router_offline = mmd.CapabilityAwareRouter(offline_topology)
    try:
        router_offline.route_subtask(subtask_heavy)
        assert_fail("Router failed to raise NoCapableNodeError when capable node is offline")
    except mmd.NoCapableNodeError as e:
        assert_pass(f"Negative Control: Router rejected offline node with NoCapableNodeError: {e}")

    # 4c. CLI negative control with non-zero exit code
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump({"nodes": [n_cpu_only.to_dict()]}, f)
        temp_topo_path = f.name

    try:
        res_neg = subprocess.run(
            [
                sys.executable,
                str(DISTRIBUTOR_SCRIPT),
                "route",
                "--nodes", temp_topo_path,
                "--role", mmd.ROLE_HEAVY_REASONING,
                "--prompt", "Test heavy prompt.",
            ],
            capture_output=True,
            text=True,
        )
        if res_neg.returncode != 0 and "NoCapableNodeError" in res_neg.stderr:
            assert_pass(f"CLI gracefully returned exit code {res_neg.returncode} on missing capability error")
        else:
            assert_fail("CLI failed to return error code on missing capability", f"code={res_neg.returncode}, stderr={res_neg.stderr}")
    finally:
        if os.path.exists(temp_topo_path):
            os.remove(temp_topo_path)


# ==============================================================================
# Test 5: Mock multi-node cluster topology simulation (--mock)
# ==============================================================================
def test_5_mock_cluster_topology_simulation() -> None:
    log("Test 5: Mock multi-node cluster topology simulation (--mock)")

    distributor = mmd.MeshDistributor()
    distributor.topology.discover(mock=True)

    nodes = distributor.topology.nodes
    if len(nodes) == 4:
        assert_pass(f"Mock topology initialized {len(nodes)} heterogeneous Blade nodes")
    else:
        assert_fail(f"Mock topology node count mismatch (expected 4, got {len(nodes)})")

    # 5a. Verify all simulated nodes are bare-metal Blades (Invariant 5)
    all_blades = all(n.is_blade for n in nodes.values())
    if all_blades:
        assert_pass("All cluster nodes uphold bare-metal Blade hardware ownership invariant")
    else:
        assert_fail("Non-blade nodes detected in bare-metal mesh simulation")

    # 5b. Dynamic load balancing / congestion shift:
    # Artificially congest blade-01 (increase active_queue_depth to 8)
    # Router should now favor blade-03 for heavy reasoning despite blade-03 having more VRAM used
    b1 = distributor.topology.get_node("blade-01")
    b3 = distributor.topology.get_node("blade-03")
    assert b1 is not None and b3 is not None

    orig_q1 = b1.metrics.active_queue_depth
    try:
        b1.metrics.active_queue_depth = 10
        st = mmd.Subtask("st-shift", mmd.ROLE_HEAVY_REASONING, "Heavy proof under congestion.")
        dec_shifted = distributor.router.route_subtask(st)
        log_diag(f"Congestion shift decision: {dec_shifted.to_dict()}")

        if dec_shifted.target_node_id == "blade-03":
            assert_pass(f"Congestion control shifted workload from busy blade-01 (q=10) to blade-03 (score={dec_shifted.score})")
        else:
            assert_fail(f"Workload was not shifted away from congested node. Target: {dec_shifted.target_node_id}")
    finally:
        b1.metrics.active_queue_depth = orig_q1

    # 5c. Degraded state handling:
    # blade-04 is degraded with latency 45.2ms, blade-02 is online with latency 12.0ms
    b4 = distributor.topology.get_node("blade-04")
    assert b4 is not None
    score_b2 = distributor.router.score_node(nodes["blade-02"], mmd.ROLE_EMBEDDINGS)
    score_b4 = distributor.router.score_node(b4, mmd.ROLE_EMBEDDINGS)

    log_diag(f"Scores for embeddings: blade-02={score_b2}, blade-04 (degraded)={score_b4}")
    if score_b2 > score_b4 and (score_b2 - score_b4) >= 30.0:
        assert_pass(f"Degraded node penalty successfully lowered blade-04 score by {score_b2 - score_b4:.1f} points")
    else:
        assert_fail("Degraded node was not sufficiently penalized", f"b2={score_b2}, b4={score_b4}")

    # 5d. End-to-end full execution across all 4 nodes in cluster
    res_cli = subprocess.run(
        [
            sys.executable,
            str(DISTRIBUTOR_SCRIPT),
            "route",
            "--mock",
            "--prompt",
            "Perform deep architectural reasoning and write python implementation.",
        ],
        capture_output=True,
        text=True,
    )
    if res_cli.returncode == 0:
        output_json = json.loads(res_cli.stdout)
        if "id" in output_json and "choices" in output_json and "mesh_routing" in output_json:
            assert_pass("End-to-end mock cluster execution produced complete OpenAI payload via CLI")
        else:
            assert_fail("CLI execution payload missing required OpenAI fields", str(output_json))
    else:
        assert_fail("CLI execution failed", res_cli.stderr)


# ==============================================================================
# Main Runner
# ==============================================================================
def main() -> int:
    global VERBOSE, DRY_RUN, MOCK_MODE

    parser = argparse.ArgumentParser(description="Test suite for mios_mesh_distributor.py")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose test logs")
    parser.add_argument("--dry-run", action="store_true", help="Dry run tests without execution")
    parser.add_argument("--mock", action="store_true", default=True, help="Enable mock cluster simulation")
    args = parser.parse_args()

    VERBOSE = args.verbose
    DRY_RUN = args.dry_run
    MOCK_MODE = args.mock

    print("======================================================================")
    print("MiOS Multi-Node AI Workload Partitioner & Router Test Suite (task_481)")
    print("======================================================================")

    test_1_cli_and_help()
    test_2_capability_routing_match()
    test_3_composite_workflow_decomposition()
    test_4_negative_control_missing_capability()
    test_5_mock_cluster_topology_simulation()

    print("======================================================================")
    total = pass_count + fail_count
    print(f"Results: {pass_count}/{total} passed, {fail_count} failed.")

    if fail_count > 0:
        print("TEST SUITE FAILED", file=sys.stderr)
        return 1

    print("ALL TESTS PASSED (100% SUCCESS)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
