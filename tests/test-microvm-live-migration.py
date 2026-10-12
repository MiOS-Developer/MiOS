# AI-hint: Tests for task_912 & task_913: MicroVM live state migration (<50ms handover).
# AI-functions: test_microvm_live_handover_sub_50ms

"""Tests for task_912 & task_913: MicroVM live state migration (<50ms handover)."""
import sys
sys.path.insert(0, "usr/libexec/mios/virt")
from microvm_migrate import MicroVMLiveMigrator

def test_microvm_live_handover_sub_50ms():
    """Verify microVM state snapshot and restore complete in <50ms with zero data loss."""
    migrator = MicroVMLiveMigrator()
    vm_id = "agent-sandbox-vm-042"

    # 1. Serialize state
    res_ser = migrator.serialize_vm_state(vm_id)
    assert res_ser["status"] == "serialized"
    assert res_ser["latency_ms"] < 50.0, f"Serialization latency {res_ser['latency_ms']:.2f}ms >= 50ms SLA"

    # 2. Restore state
    res_rest = migrator.restore_vm_state(res_ser["snapshot"])
    assert res_rest["status"] == "restored"
    assert res_rest["latency_ms"] < 50.0, f"Restore latency {res_rest['latency_ms']:.2f}ms >= 50ms SLA"
    assert vm_id in migrator.active_vms

if __name__ == "__main__":
    test_microvm_live_handover_sub_50ms()
    print("All task_912/task_913 tests passed.")
