# AI-hint: Tests for task_709 & task_710: DKMS container build (<15s) and MOK signing.
# AI-functions: test_dkms_build_and_sign_sub_15s, test_dkms_binary_caching

"""Tests for task_709 & task_710: DKMS container build (<15s) and MOK signing."""
import sys
sys.path.insert(0, "usr/libexec/mios/kernel")
from dkms_engine import DKMSSandboxEngine

def test_dkms_build_and_sign_sub_15s():
    """Verify DKMS builds test module in <15s with valid MOK signature."""
    engine = DKMSSandboxEngine()
    fake_driver_src = b"MODULE_LICENSE('GPL'); int init_module(void) { return 0; }"
    res = engine.build_module("test_driver", fake_driver_src)

    assert res["status"] == "compiled_and_signed"
    assert res["latency_s"] < 15.0, f"Build latency {res['latency_s']:.2f}s >= 15s SLA"
    assert res["module"].signed
    assert res["module"].cached_path.endswith(".ko")

def test_dkms_binary_caching():
    """Verify second build hits binary cache instantly without re-compilation."""
    engine = DKMSSandboxEngine()
    src = b"FAST_CACHED_DRIVER"
    res1 = engine.build_module("cached_driver", src)
    res2 = engine.build_module("cached_driver", src)
    assert res2["status"] == "cached"
    assert res2["latency_s"] < 0.1, "Cache hit must return instantly"

if __name__ == "__main__":
    test_dkms_build_and_sign_sub_15s()
    test_dkms_binary_caching()
    print("All task_709/task_710 tests passed.")
