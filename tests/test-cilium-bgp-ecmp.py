# AI-hint: Tests for task_703 & task_704: Cilium BGP VIP announcement & sub-100ms BFD failover.
# AI-functions: test_bgp_vip_announcement, test_bfd_sub_100ms_failover

"""Tests for task_703 & task_704: Cilium BGP VIP announcement & sub-100ms BFD failover."""
import sys
sys.path.insert(0, "usr/libexec/mios/net")
from cilium_bgp import CiliumBGPManager

def test_bgp_vip_announcement():
    """Verify BGP announces dual-stack VIPs to upstream peers."""
    mgr = CiliumBGPManager(local_asn=64512)
    mgr.peer_router("10.0.0.1", asn=64513)
    mgr.peer_router("10.0.0.2", asn=64513)

    announced = mgr.announce_vip("192.168.100.50")
    assert announced == 2
    assert "192.168.100.50" in mgr.peers["10.0.0.1"].announced_vips

def test_bfd_sub_100ms_failover():
    """Verify BFD detects node failure and withdraws path in <100ms."""
    mgr = CiliumBGPManager()
    mgr.peer_router("10.0.0.1", asn=64513)
    mgr.announce_vip("192.168.100.50")

    latency_ms = mgr.trigger_bfd_failover("10.0.0.1")
    assert latency_ms < 100.0, f"BFD failover {latency_ms:.2f}ms >= 100ms SLA"
    assert mgr.peers["10.0.0.1"].session_state == "idle"

if __name__ == "__main__":
    test_bgp_vip_announcement()
    test_bfd_sub_100ms_failover()
    print("All task_703/task_704 tests passed.")
