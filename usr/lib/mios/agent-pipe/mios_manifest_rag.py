# AI-hint: MiOS system and orchestration module providing mios manifest rag capabilities.
# AI-functions: from_dict, __init__, register_node, retrieve, _walk, _keyword_pruner, load_manifest_tree, ManifestNode, ManifestRAG

"""
mios_manifest_rag.py — task_287 MAO-04
Manifest-Guided Progressive-Disclosure Tree Retrieval.

Prevents cosine vector space collapse by navigating a hierarchical manifest
tree top-down (LLM-select pruning on node summaries) before executing vector
similarity on leaf documents.

manifest.json format at each node:
  {
    "summary": "<natural language description of this directory>",
    "children": ["subdir-a", "subdir-b", ...],
    "leaf_docs": [{"id": "...", "path": "...", "summary": "..."}]
  }
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)

# Callable type: (query, candidates: list[dict]) -> list[dict] (pruned & ranked)
NodePruner = Callable[[str, list[dict[str, Any]]], list[dict[str, Any]]]

@dataclass
class ManifestNode:
    path:     str
    summary:  str
    children: list[str]           = field(default_factory=list)
    leaf_docs: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, path: str, d: dict[str, Any]) -> "ManifestNode":
        return cls(
            path=path,
            summary=d.get("summary", ""),
            children=d.get("children", []),
            leaf_docs=d.get("leaf_docs", []),
        )

class ManifestRAG:
    """
    Progressive disclosure retrieval over a manifest tree.

    `pruner` selects and re-ranks child nodes or leaf documents by
    LLM-assisted relevance.  In dry-run / unit-test mode a simple
    substring keyword filter serves as the pruner.
    """

    def __init__(self, root_node: ManifestNode,
                 pruner: NodePruner | None = None,
                 max_depth: int = 6,
                 top_k: int = 5) -> None:
        self.root    = root_node
        self.pruner  = pruner or _keyword_pruner
        self.max_depth = max_depth
        self.top_k   = top_k
        self._node_registry: dict[str, ManifestNode] = {root_node.path: root_node}

    # ------------------------------------------------------------------
    def register_node(self, node: ManifestNode) -> None:
        self._node_registry[node.path] = node

    def retrieve(self, query: str) -> list[dict[str, Any]]:
        """
        Walk the manifest tree top-down, pruning irrelevant branches, and
        return the top-k leaf documents ranked by relevance.
        """
        results: list[dict[str, Any]] = []
        self._walk(self.root, query, depth=0, results=results)
        return results[:self.top_k]

    # ------------------------------------------------------------------
    def _walk(self, node: ManifestNode, query: str, depth: int,
              results: list[dict[str, Any]]) -> None:
        if depth >= self.max_depth:
            return

        # Prune and rank children
        child_candidates = [
            {"id": c, "summary": self._node_registry.get(c, ManifestNode(
                path=c, summary=c)).summary}
            for c in node.children
        ]
        if child_candidates:
            ranked = self.pruner(query, child_candidates)
            for child_meta in ranked:
                child_node = self._node_registry.get(child_meta["id"])
                if child_node:
                    self._walk(child_node, query, depth + 1, results)

        # Prune and rank leaf documents at this node
        if node.leaf_docs:
            ranked_leaves = self.pruner(query, node.leaf_docs)
            results.extend(ranked_leaves)

def _keyword_pruner(query: str,
                    candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Simple keyword-match pruner for unit tests.
    Keeps candidates whose summary contains any token from the query.
    Falls back to all candidates when nothing matches.
    """
    tokens = set(query.lower().split())
    scored = []
    for c in candidates:
        summary_tokens = set(c.get("summary", "").lower().split())
        score = len(tokens & summary_tokens)
        scored.append((score, c))
    scored.sort(key=lambda x: -x[0])
    relevant = [c for sc, c in scored if sc > 0]
    return relevant or candidates

def load_manifest_tree(root_path: str | Path) -> ManifestRAG:
    """
    Load a manifest tree from a directory hierarchy.
    Each directory with a `manifest.json` becomes a ManifestNode.
    """
    root_path = Path(root_path)
    rag: ManifestRAG | None = None
    for manifest_file in root_path.rglob("manifest.json"):
        try:
            data = json.loads(manifest_file.read_text())
            node_path = str(manifest_file.parent.relative_to(root_path))
            node = ManifestNode.from_dict(node_path, data)
            if rag is None:
                rag = ManifestRAG(root_node=node)
            else:
                rag.register_node(node)
        except Exception as exc:
            log.warning("ManifestRAG: skip %s: %s", manifest_file, exc)
    if rag is None:
        # Return an empty RAG with a blank root
        rag = ManifestRAG(root_node=ManifestNode(path=".", summary="root"))
    return rag
