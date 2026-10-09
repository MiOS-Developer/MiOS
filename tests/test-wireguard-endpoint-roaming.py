# AI-hint: Tests for task_697 & task_698: WireGuard endpoint roaming (<50ms) and PMTU clamping.
# AI-functions: test_endpoint_roaming_sub_50ms, test_pmtu_clamping

"""Tests for task_697 & task_698: WireGuard endpoint roaming (<50ms) and PMTU clamping."""
import sys
sys.path.insert(0, "usr/libexec/mios/net")
from wireguard_roam import WireGuardRoamingDaemon

def test_endpoint_roaming_sub_50ms():
    """Verify IP switch updates peer endpoint in <50ms."""
    daemon = WireGuardRoamingDaemon()
    pubkey = "test_wg_pubkey_abc123"
    daemon.register_peer(pubkey, "192.168.1.50:51820")

    latency_ms = daemon.handle_ip_change(pubkey, "10.0.0.99")
    assert latency_ms < 50.0, f"Roaming latency {latency_ms:.2f}ms >= 50ms SLA"
    assert daemon.peers[pubkey].endpoint == "10.0.0.99:51820"

def test_pmtu_clamping():
    """Verify interface MTU is clamped within 1280-1420 range."""
    daemon = WireGuardRoamingDaemon()
    pubkey = "test_wg_pubkey_abc123"
    daemon.register_peer(pubkey, "192.168.1.50:51820")

    assert daemon.clamp_pmtu(pubkey, 1500) == 1420
    assert daemon.clamp_pmtu(pubkey, 1200) == 1280
    assert daemon.clamp_pmtu(pubkey, 1360) == 1360

if __name__ == "__main__":
    test_endpoint_roaming_sub_50ms()
    test_pmtu_clamping()
    print("All task_697/task_698 tests passed.")
