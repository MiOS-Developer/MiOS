#!/usr/bin/env python3
# AI-hint: Multi-node dynamic AI workload partitioner and capability-aware task router (T-537, AGY-2135).
# AI-doc: usr/share/doc/mios/manual/ch82-mesh-workload-partitioning.md
"""Multi-node dynamic AI workload partitioner and capability-aware task router.

Discovers and tracks live node capabilities across a 2-6 bare-metal Blade mesh,
partitions composite agent workflows into discrete subtasks, dispatches them to
optimal nodes based on a capability matrix and live health/VRAM/queue telemetry,
and merges distributed outputs into unified OpenAI-compatible responses.
"""

from __future__ import annotations

import argparse
import concurrent.futures
from dataclasses import asdict, dataclass, field
import json
import os
import pathlib
import re
import sys
import time
from typing import Any, Dict, List, Optional, Tuple
import urllib.error
import urllib.parse
import urllib.request
import uuid

# ==============================================================================
# Role Definitions & Architectural Constants
# ==============================================================================

ROLE_HEAVY_REASONING = "heavy_reasoning"    # mios-llm-heavy, vLLM or SGLang per [ai].heavy_engine ([ports].llm_heavy, dGPU VFIO)
ROLE_CODING = "coding"                      # mios-opencode (served by [ports].llm_light)
ROLE_EMBEDDINGS = "embeddings"              # nomic-embed-text (served by [ports].llm_light)
ROLE_TOOL_SANDBOX = "tool_sandbox"          # bwrap / seccomp isolated execution
ROLE_LIGHT_CHAT = "light_chat"              # llama.cpp / llama-swap (mios-llm-light, [ports].llm_light)

SUPPORTED_ROLES = [
    ROLE_HEAVY_REASONING,
    ROLE_CODING,
    ROLE_EMBEDDINGS,
    ROLE_TOOL_SANDBOX,
    ROLE_LIGHT_CHAT,
]

STATUS_ONLINE = "online"
STATUS_DEGRADED = "degraded"
STATUS_OFFLINE = "offline"

DEFAULT_CLUSTER_NODES_PATH = "/run/mios/cluster/nodes.json"
_LIGHT_PORT = os.environ.get("MIOS_PORTS_LLM_LIGHT", "8500")
_HEAVY_PORT = os.environ.get("MIOS_PORTS_LLM_HEAVY", "8520")


# ==============================================================================
# Exceptions
# ==============================================================================

class MeshDistributorError(Exception):
    """Base exception for mesh distributor errors."""
    pass


class NoCapableNodeError(MeshDistributorError):
    """Raised when no online node satisfies the requested capability role."""
    pass


class PartitionError(MeshDistributorError):
    """Raised when workload decomposition fails."""
    pass


# ==============================================================================
# Data Models: Telemetry, Node, Subtask, Decision
# ==============================================================================

@dataclass
class NodeMetrics:
    """Live performance and health metrics for a mesh node."""
    latency_ms: float = 10.0
    active_queue_depth: int = 0
    gpu_vram_used_mb: int = 0
    gpu_vram_total_mb: int = 0
    cpu_load_pct: float = 0.0
    last_heartbeat: float = field(default_factory=time.time)

    @property
    def vram_utilization_ratio(self) -> float:
        if self.gpu_vram_total_mb <= 0:
            return 0.0
        return min(1.0, max(0.0, self.gpu_vram_used_mb / float(self.gpu_vram_total_mb)))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "latency_ms": round(self.latency_ms, 2),
            "active_queue_depth": self.active_queue_depth,
            "gpu_vram_used_mb": self.gpu_vram_used_mb,
            "gpu_vram_total_mb": self.gpu_vram_total_mb,
            "vram_utilization_pct": round(self.vram_utilization_ratio * 100.0, 1),
            "cpu_load_pct": round(self.cpu_load_pct, 1),
            "last_heartbeat": self.last_heartbeat,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "NodeMetrics":
        return cls(
            latency_ms=float(data.get("latency_ms", 10.0)),
            active_queue_depth=int(data.get("active_queue_depth", 0)),
            gpu_vram_used_mb=int(data.get("gpu_vram_used_mb", 0)),
            gpu_vram_total_mb=int(data.get("gpu_vram_total_mb", 0)),
            cpu_load_pct=float(data.get("cpu_load_pct", 0.0)),
            last_heartbeat=float(data.get("last_heartbeat", time.time())),
        )


@dataclass
class MeshNode:
    """Represents a bare-metal Blade or host in the 2-6 node mesh."""
    node_id: str
    hostname: str
    mesh_ip: str
    is_blade: bool = True
    status: str = STATUS_ONLINE
    roles: List[str] = field(default_factory=list)
    metrics: NodeMetrics = field(default_factory=NodeMetrics)
    endpoint_url: str = ""

    def has_capability(self, role: str) -> bool:
        """Check whether this node advertises the required role."""
        return role in self.roles

    def can_accept(self, role: str) -> bool:
        """Check if node can accept a task for the given role right now."""
        if self.status == STATUS_OFFLINE:
            return False
        if not self.has_capability(role):
            return False
        # If GPU workload, check that VRAM is not 100% saturated
        if role == ROLE_HEAVY_REASONING and self.metrics.gpu_vram_total_mb > 0:
            if self.metrics.vram_utilization_ratio >= 0.98:
                return False
        return True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "hostname": self.hostname,
            "mesh_ip": self.mesh_ip,
            "is_blade": self.is_blade,
            "status": self.status,
            "roles": list(self.roles),
            "metrics": self.metrics.to_dict(),
            "endpoint_url": self.endpoint_url,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MeshNode":
        metrics_raw = data.get("metrics", {})
        metrics = NodeMetrics.from_dict(metrics_raw) if isinstance(metrics_raw, dict) else NodeMetrics()
        return cls(
            node_id=str(data.get("node_id", "")),
            hostname=str(data.get("hostname", "")),
            mesh_ip=str(data.get("mesh_ip", "127.0.0.1")),
            is_blade=bool(data.get("is_blade", True)),
            status=str(data.get("status", STATUS_ONLINE)),
            roles=list(data.get("roles", [])),
            metrics=metrics,
            endpoint_url=str(data.get("endpoint_url", "")),
        )


@dataclass
class Subtask:
    """Discrete subtask extracted from an agent request."""
    subtask_id: str
    role: str
    prompt: str
    priority: int = 1
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "subtask_id": self.subtask_id,
            "role": self.role,
            "prompt": self.prompt,
            "priority": self.priority,
            "metadata": self.metadata,
        }


@dataclass
class RoutingDecision:
    """Capability routing decision mapping a subtask to an optimal node."""
    subtask_id: str
    role: str
    target_node_id: str
    target_node_ip: str
    score: float
    reason: str
    metrics_snapshot: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "subtask_id": self.subtask_id,
            "role": self.role,
            "target_node_id": self.target_node_id,
            "target_node_ip": self.target_node_ip,
            "score": round(self.score, 2),
            "reason": self.reason,
            "metrics_snapshot": self.metrics_snapshot,
        }


# ==============================================================================
# Mesh Topology Discovery & State Management
# ==============================================================================

class MeshTopology:
    """Maintains state, discovery, and telemetry ledger across mesh nodes."""

    def __init__(self, verbose: bool = False):
        self.nodes: Dict[str, MeshNode] = {}
        self.ledger: List[Dict[str, Any]] = []
        self.verbose = verbose

    def add_node(self, node: MeshNode) -> None:
        self.nodes[node.node_id] = node

    def get_node(self, node_id: str) -> Optional[MeshNode]:
        return self.nodes.get(node_id)

    def online_nodes(self) -> List[MeshNode]:
        return [n for n in self.nodes.values() if n.status != STATUS_OFFLINE]

    def record_routing_event(self, decision: RoutingDecision, duration_ms: float = 0.0) -> None:
        event = {
            "timestamp": time.time(),
            "decision": decision.to_dict(),
            "duration_ms": round(duration_ms, 2),
        }
        self.ledger.append(event)
        # Cap ledger length to prevent memory leak
        if len(self.ledger) > 1000:
            self.ledger.pop(0)

    def load_from_dict(self, data: Dict[str, Any]) -> None:
        self.nodes.clear()
        nodes_list = data.get("nodes", [])
        if isinstance(nodes_list, list):
            for item in nodes_list:
                if isinstance(item, dict):
                    node = MeshNode.from_dict(item)
                    self.nodes[node.node_id] = node
        elif isinstance(nodes_list, dict):
            for k, item in nodes_list.items():
                if isinstance(item, dict):
                    if "node_id" not in item:
                        item["node_id"] = k
                    node = MeshNode.from_dict(item)
                    self.nodes[node.node_id] = node

    def load_from_file_or_string(self, source: str) -> None:
        if os.path.isfile(source):
            with open(source, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = json.loads(source)
        self.load_from_dict(data)

    def load_mock_topology(self) -> None:
        """Initializes a realistic 4-node bare-metal Blade mesh topology."""
        self.nodes.clear()

        # Blade 01: Primary heavy reasoning & coding compute node
        n1 = MeshNode(
            node_id="blade-01",
            hostname="blade-01.mesh.local",
            mesh_ip="10.244.0.1",
            is_blade=True,
            status=STATUS_ONLINE,
            roles=[ROLE_HEAVY_REASONING, ROLE_CODING, ROLE_TOOL_SANDBOX],
            metrics=NodeMetrics(
                latency_ms=8.5,
                active_queue_depth=1,
                gpu_vram_used_mb=4096,
                gpu_vram_total_mb=24576,
                cpu_load_pct=22.4,
            ),
            endpoint_url=f"http://10.244.0.1:{_HEAVY_PORT}/v1",
        )

        # Blade 02: Light inference, coding, and fast vector embeddings
        n2 = MeshNode(
            node_id="blade-02",
            hostname="blade-02.mesh.local",
            mesh_ip="10.244.0.2",
            is_blade=True,
            status=STATUS_ONLINE,
            roles=[ROLE_CODING, ROLE_EMBEDDINGS, ROLE_LIGHT_CHAT],
            metrics=NodeMetrics(
                latency_ms=12.0,
                active_queue_depth=0,
                gpu_vram_used_mb=0,
                gpu_vram_total_mb=0,
                cpu_load_pct=15.0,
            ),
            endpoint_url=f"http://10.244.0.2:{_LIGHT_PORT}/v1",
        )

        # Blade 03: Secondary heavy reasoning node with higher load and VRAM occupancy
        n3 = MeshNode(
            node_id="blade-03",
            hostname="blade-03.mesh.local",
            mesh_ip="10.244.0.3",
            is_blade=True,
            status=STATUS_ONLINE,
            roles=[ROLE_HEAVY_REASONING, ROLE_LIGHT_CHAT],
            metrics=NodeMetrics(
                latency_ms=16.8,
                active_queue_depth=4,
                gpu_vram_used_mb=18432,
                gpu_vram_total_mb=24576,
                cpu_load_pct=68.5,
            ),
            endpoint_url=f"http://10.244.0.3:{_HEAVY_PORT}/v1",
        )

        # Blade 04: Dedicated sandbox & embeddings node (Degraded network latency)
        n4 = MeshNode(
            node_id="blade-04",
            hostname="blade-04.mesh.local",
            mesh_ip="10.244.0.4",
            is_blade=True,
            status=STATUS_DEGRADED,
            roles=[ROLE_TOOL_SANDBOX, ROLE_EMBEDDINGS, ROLE_LIGHT_CHAT],
            metrics=NodeMetrics(
                latency_ms=45.2,
                active_queue_depth=2,
                gpu_vram_used_mb=0,
                gpu_vram_total_mb=0,
                cpu_load_pct=35.0,
            ),
            endpoint_url=f"http://10.244.0.4:{_LIGHT_PORT}/v1",
        )

        self.nodes = {n.node_id: n for n in [n1, n2, n3, n4]}

    def discover(self, source: Optional[str] = None, mock: bool = False) -> None:
        """Discovers topology from file, cluster path, or mock mode."""
        if mock:
            self.load_mock_topology()
            return

        if source:
            self.load_from_file_or_string(source)
            return

        # Attempt default cluster topology path
        if os.path.exists(DEFAULT_CLUSTER_NODES_PATH):
            try:
                self.load_from_file_or_string(DEFAULT_CLUSTER_NODES_PATH)
                return
            except Exception as e:
                if self.verbose:
                    print(f"[WARN] Failed to read {DEFAULT_CLUSTER_NODES_PATH}: {e}", file=sys.stderr)

        # Fallback to single local host node
        local_node = MeshNode(
            node_id="local-blade",
            hostname="localhost",
            mesh_ip="127.0.0.1",
            is_blade=True,
            status=STATUS_ONLINE,
            roles=[ROLE_LIGHT_CHAT, ROLE_EMBEDDINGS, ROLE_CODING, ROLE_TOOL_SANDBOX],
            metrics=NodeMetrics(latency_ms=1.0, active_queue_depth=0),
            endpoint_url=f"http://127.0.0.1:{_LIGHT_PORT}/v1",
        )
        self.nodes = {local_node.node_id: local_node}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_nodes": len(self.nodes),
            "online_nodes": len(self.online_nodes()),
            "nodes": [n.to_dict() for n in self.nodes.values()],
            "ledger_size": len(self.ledger),
            "ledger_sample": self.ledger[-5:],
        }


# ==============================================================================
# Capability-Aware Task Router
# ==============================================================================

class CapabilityAwareRouter:
    """Scores candidate mesh nodes and binds subtasks to optimal nodes."""

    def __init__(self, topology: MeshTopology, verbose: bool = False):
        self.topology = topology
        self.verbose = verbose

    def score_node(self, node: MeshNode, role: str) -> float:
        """Calculates score for a node executing a specific role (higher is better)."""
        score = 100.0

        # Status impact
        if node.status == STATUS_DEGRADED:
            score -= 30.0
        elif node.status == STATUS_OFFLINE:
            return -1000.0

        # Queue depth penalty (congestion control)
        # Each active queued request degrades responsiveness
        score -= float(node.metrics.active_queue_depth) * 12.0

        # Latency penalty (RTT / network distance)
        score -= min(30.0, node.metrics.latency_ms * 0.5)

        # VRAM utilization penalty for GPU workloads
        if role == ROLE_HEAVY_REASONING:
            if node.metrics.gpu_vram_total_mb > 0:
                vram_ratio = node.metrics.vram_utilization_ratio
                score -= vram_ratio * 40.0
            else:
                # Heavy reasoning on CPU is heavily penalized
                score -= 60.0

        # CPU load penalty
        score -= (node.metrics.cpu_load_pct / 100.0) * 15.0

        # Architectural Invariant: Bare-metal Blade preference
        # Hardware dGPU VFIO and heavy models belong on bare-metal Blades
        if node.is_blade:
            score += 10.0
        else:
            if role == ROLE_HEAVY_REASONING:
                score -= 25.0

        return max(0.0, score)

    def route_subtask(self, subtask: Subtask) -> RoutingDecision:
        """Finds the optimal online node possessing the subtask's required role."""
        candidates = [n for n in self.topology.nodes.values() if n.can_accept(subtask.role)]

        if not candidates:
            # Check if role is completely absent across all known nodes
            all_known_with_role = [n for n in self.topology.nodes.values() if n.has_capability(subtask.role)]
            if not all_known_with_role:
                raise NoCapableNodeError(
                    f"No node in mesh possesses required capability '{subtask.role}'."
                )
            else:
                raise NoCapableNodeError(
                    f"Nodes with capability '{subtask.role}' exist, but none are currently available or online."
                )

        scored: List[Tuple[float, MeshNode]] = []
        for node in candidates:
            s = self.score_node(node, subtask.role)
            scored.append((s, node))

        # Sort descending by score
        scored.sort(key=lambda x: x[0], reverse=True)
        best_score, best_node = scored[0]

        reason = (
            f"Selected {best_node.node_id} (score {best_score:.1f}) for role '{subtask.role}' "
            f"[queue={best_node.metrics.active_queue_depth}, latency={best_node.metrics.latency_ms:.1f}ms, "
            f"vram_util={best_node.metrics.vram_utilization_ratio*100:.1f}%]"
        )

        decision = RoutingDecision(
            subtask_id=subtask.subtask_id,
            role=subtask.role,
            target_node_id=best_node.node_id,
            target_node_ip=best_node.mesh_ip,
            score=best_score,
            reason=reason,
            metrics_snapshot=best_node.metrics.to_dict(),
        )

        self.topology.record_routing_event(decision)
        return decision


# ==============================================================================
# Workload Partitioner (Composite Workflow Analyzer)
# ==============================================================================

class WorkloadPartitioner:
    """Analyzes composite agent requests and decomposes them into discrete subtasks."""

    def __init__(self, verbose: bool = False):
        self.verbose = verbose

    def classify_single_prompt(self, prompt: str) -> str:
        """Classifies a prompt into its primary execution role."""
        lower = prompt.lower()

        # Embeddings / vector operations
        if any(k in lower for k in ["embed", "nomic-embed", "vector search", "semantic search", "embedding similarity", "embeddings"]):
            return ROLE_EMBEDDINGS

        # Tool execution / sandbox
        if any(k in lower for k in ["sandbox", "run command", "run test", "execute bash", "compile", "bwrap", "seccomp", "execute script"]):
            return ROLE_TOOL_SANDBOX

        # Heavy reasoning / deep architecture / proof
        if any(k in lower for k in ["prove", "deep reasoning", "vllm", "sglang", "architectural invariant", "formal verification", "complex math", "multi-step planning"]):
            return ROLE_HEAVY_REASONING

        # Code generation / refactoring / debugging
        if any(k in lower for k in ["def ", "class ", "function", "write python", "implement", "refactor", "bugfix", "write code", "opencode"]):
            return ROLE_CODING

        return ROLE_LIGHT_CHAT

    def analyze_and_partition(self, prompt: str, forced_role: Optional[str] = None) -> List[Subtask]:
        """Decomposes an incoming prompt into one or more subtasks."""
        if forced_role:
            if forced_role not in SUPPORTED_ROLES:
                raise PartitionError(f"Unsupported role '{forced_role}'. Supported: {SUPPORTED_ROLES}")
            return [Subtask(
                subtask_id=f"task-{uuid.uuid4().hex[:8]}",
                role=forced_role,
                prompt=prompt,
                priority=1,
            )]

        subtasks: List[Subtask] = []

        # Check for explicit multi-step or composite instructions
        # Example pattern: "Step 1: ... Step 2: ... Step 3: ..."
        step_matches = list(re.finditer(r"(?:^|\n)\s*(?:Step\s*(\d+)|\d+\.)[:\s]+(.*?)(?=(?:\n\s*(?:Step\s*\d+|\d+\.)[:\s]+)|\Z)", prompt, re.DOTALL | re.IGNORECASE))

        if len(step_matches) >= 2:
            for idx, m in enumerate(step_matches, 1):
                step_text = m.group(2).strip()
                if not step_text:
                    continue
                step_role = self.classify_single_prompt(step_text)
                subtasks.append(Subtask(
                    subtask_id=f"subtask-{idx}-{uuid.uuid4().hex[:6]}",
                    role=step_role,
                    prompt=step_text,
                    priority=idx,
                    metadata={"step_index": idx},
                ))
            if subtasks:
                return subtasks

        # Check for compound sentence directives (e.g. "search embeddings for X, write code for Y, and run in sandbox Z")
        has_embed = bool(re.search(r"\b(embed|vector search|semantic search)\b", prompt, re.IGNORECASE))
        has_code = bool(re.search(r"\b(write|implement|code|refactor|function)\b", prompt, re.IGNORECASE))
        has_sandbox = bool(re.search(r"\b(sandbox|execute|run tests?|compile)\b", prompt, re.IGNORECASE))
        has_heavy = bool(re.search(r"\b(prove|architectural reasoning|deep planning|formal verification)\b", prompt, re.IGNORECASE))

        detected_roles = []
        if has_embed:
            detected_roles.append((ROLE_EMBEDDINGS, "Extract semantic vector embeddings and search context."))
        if has_heavy:
            detected_roles.append((ROLE_HEAVY_REASONING, "Perform deep architectural reasoning and constraint verification."))
        if has_code:
            detected_roles.append((ROLE_CODING, "Synthesize robust implementation code and tests."))
        if has_sandbox:
            detected_roles.append((ROLE_TOOL_SANDBOX, "Execute isolated unit test verification inside container sandbox."))

        if len(detected_roles) >= 2:
            for idx, (r, desc) in enumerate(detected_roles, 1):
                subtasks.append(Subtask(
                    subtask_id=f"comp-{idx}-{uuid.uuid4().hex[:6]}",
                    role=r,
                    prompt=f"[{r.upper()}] {desc} Target query: {prompt}",
                    priority=idx,
                    metadata={"composite": True, "clause_index": idx},
                ))
            return subtasks

        # Single subtask fallback
        primary_role = self.classify_single_prompt(prompt)
        return [Subtask(
            subtask_id=f"subtask-1-{uuid.uuid4().hex[:6]}",
            role=primary_role,
            prompt=prompt,
            priority=1,
            metadata={"composite": False},
        )]


# ==============================================================================
# Mesh Workload Distributor & Dispatcher
# ==============================================================================

class MeshDistributor:
    """High-level facade coordinating topology, partitioning, dispatch, and gathering."""

    def __init__(self, topology: Optional[MeshTopology] = None, verbose: bool = False):
        self.verbose = verbose
        self.topology = topology or MeshTopology(verbose=verbose)
        self.router = CapabilityAwareRouter(self.topology, verbose=verbose)
        self.partitioner = WorkloadPartitioner(verbose=verbose)

    def execute_subtask_mock(self, subtask: Subtask, decision: RoutingDecision) -> Dict[str, Any]:
        """Simulates remote node inference execution in mock mode."""
        time.sleep(0.01)  # Simulated network latency
        node = self.topology.get_node(decision.target_node_id)
        latency = node.metrics.latency_ms if node else 10.0

        if subtask.role == ROLE_HEAVY_REASONING:
            content = f"[{decision.target_node_id} / vLLM heavy] Formal proof completed: Verified architectural invariants and optimal convergence under resource bounds."
        elif subtask.role == ROLE_CODING:
            content = f"[{decision.target_node_id} / mios-opencode] Code synthesis complete: Generated typed implementation with defensive boundary checks."
        elif subtask.role == ROLE_EMBEDDINGS:
            content = f"[{decision.target_node_id} / nomic-embed] Vector embeddings generated: 768-dimensional float32 vector calculated with 0.94 similarity match."
        elif subtask.role == ROLE_TOOL_SANDBOX:
            content = f"[{decision.target_node_id} / sandbox] Isolated bubblewrap execution passed: 0 exit code, 4 tests passed, 0 violations."
        else:
            content = f"[{decision.target_node_id} / light_chat] Response synthesized successfully."

        return {
            "subtask_id": subtask.subtask_id,
            "role": subtask.role,
            "target_node_id": decision.target_node_id,
            "target_node_ip": decision.target_node_ip,
            "latency_ms": latency,
            "content": content,
            "status": "success",
            "prompt_tokens": max(5, len(subtask.prompt) // 4),
            "completion_tokens": max(10, len(content) // 4),
        }

    def execute_subtask_live(self, subtask: Subtask, decision: RoutingDecision) -> Dict[str, Any]:
        """Dispatches an HTTP request to the target node's OpenAI-compatible endpoint."""
        node = self.topology.get_node(decision.target_node_id)
        if not node or not node.endpoint_url:
            raise MeshDistributorError(f"Node {decision.target_node_id} lacks endpoint URL.")

        url = f"{node.endpoint_url.rstrip('/')}/chat/completions"
        payload = {
            "model": "default",
            "messages": [{"role": "user", "content": subtask.prompt}],
            "temperature": 0.2,
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=10.0) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            elapsed_ms = (time.time() - t0) * 1000.0
            choice = body.get("choices", [{}])[0]
            content = choice.get("message", {}).get("content", "")
            usage = body.get("usage", {})
            return {
                "subtask_id": subtask.subtask_id,
                "role": subtask.role,
                "target_node_id": decision.target_node_id,
                "target_node_ip": decision.target_node_ip,
                "latency_ms": elapsed_ms,
                "content": content,
                "status": "success",
                "prompt_tokens": usage.get("prompt_tokens", len(subtask.prompt) // 4),
                "completion_tokens": usage.get("completion_tokens", len(content) // 4),
            }
        except Exception as e:
            raise MeshDistributorError(f"Remote dispatch to {url} failed: {e}") from e

    def dispatch_workload(
        self,
        prompt: str,
        role_override: Optional[str] = None,
        mock: bool = False,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Full pipeline: Partition -> Route -> Dispatch -> Gather & Merge."""
        # Step 1: Workload Partitioning
        subtasks = self.partitioner.analyze_and_partition(prompt, forced_role=role_override)
        if self.verbose:
            print(f"[INFO] Partitioned into {len(subtasks)} subtask(s):", file=sys.stderr)
            for st in subtasks:
                print(f"  - [{st.subtask_id}] role={st.role}: {st.prompt[:60]}...", file=sys.stderr)

        # Step 2: Capability-Aware Routing
        decisions: List[Tuple[Subtask, RoutingDecision]] = []
        for st in subtasks:
            decision = self.router.route_subtask(st)
            decisions.append((st, decision))
            if self.verbose:
                print(f"[INFO] Routed {st.subtask_id} -> {decision.target_node_id} (Score: {decision.score})", file=sys.stderr)

        # Dry-run returns the routing blueprint without network dispatches
        if dry_run:
            return {
                "dry_run": True,
                "subtask_count": len(subtasks),
                "partitions": [
                    {
                        "subtask": st.to_dict(),
                        "routing": dec.to_dict(),
                    }
                    for st, dec in decisions
                ],
            }

        # Step 3: Parallel Dispatch & Gathering
        results: List[Dict[str, Any]] = []
        if mock:
            for st, dec in decisions:
                results.append(self.execute_subtask_mock(st, dec))
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(decisions))) as executor:
                future_map = {
                    executor.submit(self.execute_subtask_live, st, dec): (st, dec)
                    for st, dec in decisions
                }
                for fut in concurrent.futures.as_completed(future_map):
                    st, dec = future_map[fut]
                    try:
                        res = fut.result()
                        results.append(res)
                    except Exception as e:
                        results.append({
                            "subtask_id": st.subtask_id,
                            "role": st.role,
                            "target_node_id": dec.target_node_id,
                            "target_node_ip": dec.target_node_ip,
                            "status": "error",
                            "error": str(e),
                            "content": f"[Error on {dec.target_node_id}]: {e}",
                            "prompt_tokens": 0,
                            "completion_tokens": 0,
                        })

        # Ensure results maintain original subtask ordering
        subtask_order = {st.subtask_id: idx for idx, (st, _) in enumerate(decisions)}
        results.sort(key=lambda r: subtask_order.get(r["subtask_id"], 0))

        # Step 4: Merge distributed results into unified OpenAI-compatible payload
        merged_content = "\n\n".join(r.get("content", "") for r in results)
        total_prompt_tokens = sum(r.get("prompt_tokens", 0) for r in results)
        total_completion_tokens = sum(r.get("completion_tokens", 0) for r in results)

        openai_response = {
            "id": f"chatcmpl-mesh-{uuid.uuid4().hex[:12]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": "mios-mesh-distributor",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": merged_content,
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": total_prompt_tokens,
                "completion_tokens": total_completion_tokens,
                "total_tokens": total_prompt_tokens + total_completion_tokens,
            },
            "mesh_routing": {
                "partitioned": len(subtasks) > 1,
                "subtask_count": len(subtasks),
                "dispatches": [
                    {
                        "subtask_id": r.get("subtask_id"),
                        "role": r.get("role"),
                        "node_id": r.get("target_node_id"),
                        "node_ip": r.get("target_node_ip"),
                        "latency_ms": r.get("latency_ms", 0.0),
                        "status": r.get("status"),
                    }
                    for r in results
                ],
            },
        }

        return openai_response


# ==============================================================================
# CLI Entry Point
# ==============================================================================

def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--nodes",
        dest="nodes",
        metavar="JSON_OR_FILE",
        help="Path to nodes JSON file or raw JSON string specifying mesh topology.",
    )
    common.add_argument(
        "--mock",
        action="store_true",
        help="Simulate a 4-node bare-metal Blade mesh cluster topology.",
    )
    common.add_argument(
        "--dry-run",
        action="store_true",
        help="Calculate partitions and routing decisions without making network dispatches.",
    )
    common.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable detailed diagnostic logging.",
    )

    parser = argparse.ArgumentParser(
        prog="mios_mesh_distributor.py",
        description="Multi-node dynamic AI workload partitioner and capability-aware task router (T-537, AGY-2135).",
        parents=[common],
    )

    subparsers = parser.add_subparsers(dest="command", help="Subcommand to execute")

    # Command: route
    route_cmd = subparsers.add_parser(
        "route",
        help="Demonstrate capability routing decision for a prompt.",
        parents=[common],
    )
    route_cmd.add_argument(
        "--prompt",
        default="Perform deep architectural reasoning and write python implementation.",
        help="Prompt text to analyze, partition, and route.",
    )
    route_cmd.add_argument(
        "--role",
        choices=SUPPORTED_ROLES,
        default=None,
        help="Force a specific capability role instead of dynamic classification.",
    )

    # Command: status
    status_cmd = subparsers.add_parser(
        "status",
        help="Display mesh node topology, known capabilities, and ledger.",
        parents=[common],
    )

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    topo = MeshTopology(verbose=args.verbose)
    topo.discover(source=args.nodes, mock=args.mock)
    distributor = MeshDistributor(topology=topo, verbose=args.verbose)

    if args.command == "status":
        status_data = topo.to_dict()
        print(json.dumps(status_data, indent=2))
        return 0

    if args.command == "route":
        try:
            result = distributor.dispatch_workload(
                prompt=args.prompt,
                role_override=args.role,
                mock=args.mock,
                dry_run=args.dry_run,
            )
            print(json.dumps(result, indent=2))
            return 0
        except MeshDistributorError as e:
            print(json.dumps({"error": str(e), "type": type(e).__name__}, indent=2), file=sys.stderr)
            return 1
        except Exception as e:
            print(f"Unexpected error: {e}", file=sys.stderr)
            return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
