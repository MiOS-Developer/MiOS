# AI-hint: Tests for task_687 & task_688: Varlink schema validation and sub-1ms RPC.
# AI-related: usr/lib/mios/ipc/varlink_activator.py
# AI-functions: _create_server, test_varlink_valid_rpc_sub_1ms, test_varlink_missing_param_error

"""Tests for task_687 & task_688: Varlink schema validation and sub-1ms RPC."""
import sys, json
sys.path.insert(0, "usr/lib/mios/ipc")
from varlink_activator import VarlinkServer, VarlinkInterface

def _create_server() -> VarlinkServer:
    server = VarlinkServer()
    model_iface = VarlinkInterface("org.mios.Model")
    model_iface.define_method(
        name="Swap",
        required_fields=["model_name", "target_gpu"],
        handler=lambda p: {"status": "swapped", "model": p["model_name"]}
    )
    server.register(model_iface)
    return server

def test_varlink_valid_rpc_sub_1ms():
    """Verify valid Varlink RPC executes with sub-1.0ms latency."""
    server = _create_server()
    payload = json.dumps({
        "method": "org.mios.Model.Swap",
        "parameters": {"model_name": "qwen-2.5-7b", "target_gpu": 0}
    })

    reply_str = server.handle_rpc(payload)
    reply = json.loads(reply_str)
    assert "parameters" in reply
    assert reply["parameters"]["status"] == "swapped"
    assert reply["_latency_ms"] < 1.0, f"RPC latency {reply['_latency_ms']:.3f}ms >= 1.0ms SLA"

def test_varlink_missing_param_error():
    """Verify missing required parameter returns org.varlink.service.InvalidParameter."""
    server = _create_server()
    payload = json.dumps({
        "method": "org.mios.Model.Swap",
        "parameters": {"model_name": "qwen-2.5-7b"} # missing target_gpu
    })
    reply = json.loads(server.handle_rpc(payload))
    assert reply.get("error") == "org.varlink.service.InvalidParameter"
    assert reply["parameters"]["parameter"] == "target_gpu"

if __name__ == "__main__":
    test_varlink_valid_rpc_sub_1ms()
    test_varlink_missing_param_error()
    print("All task_687/task_688 tests passed.")
