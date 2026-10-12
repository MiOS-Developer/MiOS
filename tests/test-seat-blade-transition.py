# AI-hint: Tests for task_914 & task_915: Seat-to-Blade profile transition and zero resource leaks.
# AI-related: llama-rpc-server.service
# AI-functions: test_seat_to_blade_transition

"""Tests for task_914 & task_915: Seat-to-Blade profile transition and zero resource leaks."""
import sys
sys.path.insert(0, "usr/libexec/mios/node")
from topology_switch import DynamicTopologySwitcher

def test_seat_to_blade_transition():
    """Verify seamless transition from Seat to Blade mode cleans up desktop services."""
    switcher = DynamicTopologySwitcher(initial_mode="seat")
    assert switcher.current_profile.mode == "seat"
    assert "gnome-shell" in switcher.current_profile.active_services

    res = switcher.transition_to("blade")
    assert res["status"] == "transitioned"
    assert switcher.current_profile.mode == "blade"
    assert "llama-rpc-server.service" in switcher.current_profile.active_services
    assert "gnome-shell" not in switcher.current_profile.active_services
    assert switcher.current_profile.vram_allocated_mb == 0

if __name__ == "__main__":
    test_seat_to_blade_transition()
    print("All task_914/task_915 tests passed.")
