# AI-hint: Tests for task_693 & task_694: Quadlet pre-warm and Day-0 offline start.
# AI-functions: test_offline_container_startup_speed

"""Tests for task_693 & task_694: Quadlet pre-warm and Day-0 offline start."""
import sys
sys.path.insert(0, "usr/libexec/mios/deploy")
from quadlet_prewarm import QuadletPrewarmer

def test_offline_container_startup_speed():
    """Verify pre-warmed Quadlet containers start in <100ms with 0 network requests."""
    prewarmer = QuadletPrewarmer()
    quadlets = ["pgvector", "open-webui", "searxng", "llm-light", "forgejo"]
    prewarmer.prewarm_quadlets(quadlets)

    for q in quadlets:
        res = prewarmer.simulate_day0_offline_start(q)
        assert res["status"] == "healthy"
        assert res["startup_latency_ms"] < 100.0, f"Startup {res['startup_latency_ms']:.2f}ms >= 100ms SLA"
        assert res["network_requests"] == 0

if __name__ == "__main__":
    test_offline_container_startup_speed()
    print("All task_693/task_694 tests passed.")
