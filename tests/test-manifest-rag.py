# AI-hint: Tests for task_287: mios_manifest_rag — manifest-guided progressive RAG.
# AI-related: mios_manifest_rag
# AI-functions: _build_tree, test_keyword_retrieval_returns_relevant_docs, test_irrelevant_query_falls_back_to_all, test_top_k_respected

"""Tests for task_287: mios_manifest_rag — manifest-guided progressive RAG."""
import sys
sys.path.insert(0, "usr/lib/mios/agent-pipe")
from mios_manifest_rag import ManifestRAG, ManifestNode

def _build_tree() -> ManifestRAG:
    root = ManifestNode(
        path=".",
        summary="MiOS agent runtime modules",
        children=["agent-pipe", "ai"],
        leaf_docs=[],
    )
    pipe = ManifestNode(
        path="agent-pipe",
        summary="OpenAI-compatible agent gateway, orchestration, routing",
        children=[],
        leaf_docs=[
            {"id": "server.py",    "summary": "HTTP gateway server for agent requests"},
            {"id": "mios_deliberate.py", "summary": "DCI deliberation and council"},
        ],
    )
    ai = ManifestNode(
        path="ai",
        summary="AI inference adapters, CUDA and ROCm backends",
        children=[],
        leaf_docs=[
            {"id": "cuda_graphs.py",   "summary": "CUDA graph capture replay"},
            {"id": "rocm_paged_attn.py", "summary": "ROCm paged attention"},
        ],
    )
    rag = ManifestRAG(root_node=root, top_k=5)
    rag.register_node(pipe)
    rag.register_node(ai)
    return rag

def test_keyword_retrieval_returns_relevant_docs():
    """Query for 'deliberation' returns the deliberation leaf doc."""
    rag = _build_tree()
    results = rag.retrieve("DCI deliberation council")
    ids = [r.get("id") for r in results]
    assert "mios_deliberate.py" in ids, f"Expected deliberate in {ids}"

def test_irrelevant_query_falls_back_to_all():
    """Query with no keyword match still returns all candidates."""
    rag = _build_tree()
    results = rag.retrieve("xyz_unknown_topic_zzz")
    assert len(results) > 0, "RAG must return results even on zero keyword hits"

def test_top_k_respected():
    """RAG never returns more than top_k results."""
    rag = _build_tree()
    rag.top_k = 2
    results = rag.retrieve("gateway inference CUDA")
    assert len(results) <= 2, f"Expected ≤2 results, got {len(results)}"

if __name__ == "__main__":
    test_keyword_retrieval_returns_relevant_docs()
    test_irrelevant_query_falls_back_to_all()
    test_top_k_respected()
    print("All task_287 tests passed.")
