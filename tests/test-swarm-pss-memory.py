# AI-hint: Tests for task_916 & task_917: Swarm PSS memory bounding (>1,500 tasks in <16GB).
# AI-functions: test_1500_tasks_within_16gb_budget

"""Tests for task_916 & task_917: Swarm PSS memory bounding (>1,500 tasks in <16GB)."""
import sys
sys.path.insert(0, "usr/lib/mios/agent-pipe")
from pss_regulator import PSSMemoryRegulator

def test_1500_tasks_within_16gb_budget():
    """Verify regulator supports 1,500 concurrent jcode worker tasks under 16GB."""
    regulator = PSSMemoryRegulator(max_budget_mb=16_000.0)

    # Admit 2 OpenCode containers (2 * 145MB = 290MB)
    assert regulator.admit_task("opencode-1", "opencode_container")
    assert regulator.admit_task("opencode-2", "opencode_container")

    # Admit 1,500 jcode workers (1,500 * 9.5MB = 14,250MB) -> Total: 14,540MB < 16,000MB
    for i in range(1500):
        ok = regulator.admit_task(f"jcode-worker-{i}", "jcode_worker")
        assert ok, f"Failed to admit jcode worker {i}"

    assert regulator.current_allocated_mb <= 16_000.0
    assert len(regulator.active_tasks) == 1502

if __name__ == "__main__":
    test_1500_tasks_within_16gb_budget()
    print("All task_916/task_917 tests passed.")
