# AI-hint: Tests for task_685 & task_686: Netavark firewall isolation and port audit.
# AI-functions: test_lateral_traversal_blocked, test_zero_ports_leak_to_all_interfaces

"""Tests for task_685 & task_686: Netavark firewall isolation and port audit."""
import sys
sys.path.insert(0, "usr/libexec/mios/net")
from netavark_isolate import NetavarkIsolationManager

def test_lateral_traversal_blocked():
    """Verify inter-bridge packet traversal is 100% blocked between isolated networks."""
    mgr = NetavarkIsolationManager()
    mgr.add_bridge("net-agent", "10.88.1.0/24", isolated=True)
    mgr.add_bridge("net-database", "10.88.2.0/24", isolated=True)
    mgr.add_bridge("net-public", "10.88.3.0/24", isolated=True)

    # Inter-bridge packets must be dropped
    assert not mgr.evaluate_packet("net-agent", "net-database")
    assert not mgr.evaluate_packet("net-public", "net-agent")
    # Same-bridge packet allowed
    assert mgr.evaluate_packet("net-agent", "net-agent")

def test_zero_ports_leak_to_all_interfaces():
    """Verify 0.0.0.0 port bindings are rejected and only 127.0.0.1 / mesh IPs allowed."""
    mgr = NetavarkIsolationManager()
    assert not mgr.bind_port("rogue-service", "0.0.0.0", 8080)
    assert mgr.bind_port("agent-pipe", "127.0.0.1", 8640)
    assert mgr.bind_port("mesh-node", "10.0.0.5", 9090)

    for svc, binding in mgr.port_bindings.items():
        assert not binding.startswith("0.0.0.0"), f"Leaked 0.0.0.0 binding in {svc}"

if __name__ == "__main__":
    test_lateral_traversal_blocked()
    test_zero_ports_leak_to_all_interfaces()
    print("All task_685/task_686 tests passed.")
