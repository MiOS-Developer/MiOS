# AI-hint: Tests for task_689 & task_690: systemd-homed LUKS2 unlock, key zeroization & migration.
# AI-functions: test_homed_unlock_sub_200ms, test_homed_lock_and_key_zeroization

"""Tests for task_689 & task_690: systemd-homed LUKS2 unlock, key zeroization & migration."""
import sys
sys.path.insert(0, "usr/libexec/mios/sec")
from systemd_homed import SystemdHomedManager

def test_homed_unlock_sub_200ms():
    """Verify systemd-homed enclave unlocks in <200ms with TPM2/FIDO2."""
    mgr = SystemdHomedManager()
    mgr.create_user_enclave("mios")
    res = mgr.unlock_enclave("mios", pin="1234")
    assert res["status"] == "unlocked"
    assert res["latency_ms"] < 200.0, f"Unlock latency {res['latency_ms']:.2f}ms >= 200ms SLA"

def test_homed_lock_and_key_zeroization():
    """Verify session logout deactivates LUKS and purges key material from RAM."""
    mgr = SystemdHomedManager()
    mgr.create_user_enclave("mios")
    mgr.unlock_enclave("mios")
    assert "mios" in mgr.keys_in_ram

    mgr.lock_and_zeroize("mios")
    assert mgr.enclaves["mios"].state == "locked"
    assert "mios" not in mgr.keys_in_ram

if __name__ == "__main__":
    test_homed_unlock_sub_200ms()
    test_homed_lock_and_key_zeroization()
    print("All task_689/task_690 tests passed.")
