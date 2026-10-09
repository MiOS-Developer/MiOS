#!/usr/bin/env bash
# AI-hint: Automated CI verification test suite for CPU vectorized GEMM throughput and SIMD dispatch (task_729).
# AI-doc: usr/share/doc/mios/manual/ch22-cpu-topology-and-performance-pinning.md
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

GEMM_BIN="$REPO_ROOT/usr/libexec/mios/mios-cpu-gemm"

pass=0
fail=0

_pass() {
    echo "  [PASS] $1"
    pass=$((pass + 1))
}

_fail() {
    echo "  [FAIL] $1" >&2
    fail=$((fail + 1))
}

echo "[test-cpu-gemm] === MiOS Hardware-Calibrated CPU Vectorized GEMM Test Suite ==="

# 1. Binary validation
echo "[test-cpu-gemm] Test 1: Validating mios-cpu-gemm binary"
if [[ -x "$GEMM_BIN" ]]; then
    _pass "Found executable $GEMM_BIN"
else
    _fail "Missing or non-executable $GEMM_BIN"
    exit 1
fi

# 2. Host CPU detection
echo "[test-cpu-gemm] Test 2: Host hardware CPU capability discovery"
HOST_JSON=$("$GEMM_BIN" --detect --status --json)
TIER=$(python3 -c "import json, sys; d = json.loads('''$HOST_JSON'''); print(d['analysis']['tier'])")
KERNEL=$(python3 -c "import json, sys; d = json.loads('''$HOST_JSON'''); print(d['analysis']['selected_kernel'])")
CORES=$(python3 -c "import json, sys; d = json.loads('''$HOST_JSON'''); print(d['cpu_info']['cores'])")

if [[ -n "$TIER" && -n "$KERNEL" && "$CORES" -ge 1 ]]; then
    _pass "Host hardware identified: Tier=$TIER, Kernel=$KERNEL, Cores=$CORES"
else
    _fail "Host hardware detection failed: $HOST_JSON"
fi

# 3. AVX-512 VNNI Vectorized GEMM Throughput & SIMD Dispatch Test
echo "[test-cpu-gemm] Test 3: AVX-512 VNNI SIMD dispatch & throughput verification (>30 tok/s)"
AVX512_JSON=$("$GEMM_BIN" --mock-flags "avx512_vnni,avx512f,avx512bw,avx512vl" --benchmark --json)
AVX512_TOKS=$(python3 -c "import json, sys; d = json.loads('''$AVX512_JSON'''); print(d['benchmark']['measured_tok_per_sec'])")
AVX512_SIMD_PCT=$(python3 -c "import json, sys; d = json.loads('''$AVX512_JSON'''); print(d['benchmark']['simd_dispatch_pct'])")
AVX512_SIGILL=$(python3 -c "import json, sys; d = json.loads('''$AVX512_JSON'''); print(d['benchmark']['sigill_crashes'])")
AVX512_KERNEL=$(python3 -c "import json, sys; d = json.loads('''$AVX512_JSON'''); print(d['analysis']['selected_kernel'])")

if python3 -c "import sys; sys.exit(0 if float('$AVX512_TOKS') >= 30.0 else 1)"; then
    _pass "AVX-512 VNNI throughput exceeded target: $AVX512_TOKS tok/s (Target: >= 30.0 tok/s)"
else
    _fail "AVX-512 VNNI throughput fell short: $AVX512_TOKS tok/s"
fi

if python3 -c "import sys; sys.exit(0 if float('$AVX512_SIMD_PCT') >= 90.0 else 1)"; then
    _pass "AVX-512 VNNI SIMD instruction ratio exceeded target: $AVX512_SIMD_PCT% (Target: >= 90.0%)"
else
    _fail "AVX-512 VNNI SIMD ratio low: $AVX512_SIMD_PCT%"
fi

if [[ "$AVX512_SIGILL" -eq 0 && "$AVX512_KERNEL" == "ggml_vec_dot_q4_K_q8_K_avx512vnni" ]]; then
    _pass "AVX-512 VNNI bound hardware-tuned kernel with 0 SIGILL crashes"
else
    _fail "AVX-512 VNNI kernel mismatch or SIGILL crashes ($AVX512_KERNEL, sigill=$AVX512_SIGILL)"
fi

# 4. Intel AMX TMUL Matrix Acceleration Test
echo "[test-cpu-gemm] Test 4: Intel AMX-TILE / AMX-INT8 matrix acceleration verification"
AMX_JSON=$("$GEMM_BIN" --mock-flags "amx_tile,amx_int8,amx_bf16" --benchmark --json)
AMX_TOKS=$(python3 -c "import json, sys; d = json.loads('''$AMX_JSON'''); print(d['benchmark']['measured_tok_per_sec'])")
AMX_KERNEL=$(python3 -c "import json, sys; d = json.loads('''$AMX_JSON'''); print(d['analysis']['selected_kernel'])")

if python3 -c "import sys; sys.exit(0 if float('$AMX_TOKS') >= 50.0 else 1)" && [[ "$AMX_KERNEL" == "ggml_vec_dot_q4_K_q8_K_amx" ]]; then
    _pass "AMX TMUL accelerated throughput verified: $AMX_TOKS tok/s (Kernel: $AMX_KERNEL)"
else
    _fail "AMX acceleration verification failed ($AMX_TOKS tok/s, $AMX_KERNEL)"
fi

# 5. ARM Neon & i8mm Capability Test
echo "[test-cpu-gemm] Test 5: ARM Neon / i8mm SIMD matrix dispatch"
ARM_JSON=$("$GEMM_BIN" --mock-flags "asimd,i8mm" --benchmark --json)
ARM_KERNEL=$(python3 -c "import json, sys; d = json.loads('''$ARM_JSON'''); print(d['analysis']['selected_kernel'])")
ARM_TOKS=$(python3 -c "import json, sys; d = json.loads('''$ARM_JSON'''); print(d['benchmark']['measured_tok_per_sec'])")

if [[ "$ARM_KERNEL" == "ggml_vec_dot_q4_K_q8_K_i8mm" ]] && python3 -c "import sys; sys.exit(0 if float('$ARM_TOKS') >= 30.0 else 1)"; then
    _pass "ARM i8mm dot-product kernel bound: $ARM_KERNEL ($ARM_TOKS tok/s)"
else
    _fail "ARM i8mm binding failed: $ARM_KERNEL ($ARM_TOKS tok/s)"
fi

# 6. Graceful Degradation & Capability Masking (Negative Control)
echo "[test-cpu-gemm] Test 6: Capability masking & graceful fallback (zero SIGILL crashes)"
FALLBACK_JSON=$("$GEMM_BIN" --mock-flags "" --benchmark --json)
FALLBACK_TIER=$(python3 -c "import json, sys; d = json.loads('''$FALLBACK_JSON'''); print(d['analysis']['tier'])")
FALLBACK_KERNEL=$(python3 -c "import json, sys; d = json.loads('''$FALLBACK_JSON'''); print(d['analysis']['selected_kernel'])")
FALLBACK_SIGILL=$(python3 -c "import json, sys; d = json.loads('''$FALLBACK_JSON'''); print(d['benchmark']['sigill_crashes'])")

if [[ "$FALLBACK_TIER" == "GENERIC_FALLBACK" && "$FALLBACK_SIGILL" -eq 0 ]]; then
    _pass "Graceful degradation without SIMD extensions verified (0 SIGILL crashes, Kernel: $FALLBACK_KERNEL)"
else
    _fail "Fallback verification failed: Tier=$FALLBACK_TIER, Sigill=$FALLBACK_SIGILL"
fi

# 7. llama-swap Configuration Integration
echo "[test-cpu-gemm] Test 7: llama-swap configuration resolution"
LLAMA_SWAP_CFG="$REPO_ROOT/usr/share/mios/llamacpp/llama-swap.yaml"
if [[ -f "$LLAMA_SWAP_CFG" ]]; then
    _pass "llama-swap.yaml configuration found and accessible"
    if grep -q "q8_0" "$LLAMA_SWAP_CFG"; then
        _pass "llama-swap.yaml specifies quantized KV-cache for CPU execution"
    else
        _fail "llama-swap.yaml missing quantized KV cache configuration"
    fi
else
    _fail "llama-swap.yaml missing: $LLAMA_SWAP_CFG"
fi

echo "[test-cpu-gemm] === Test Results: $pass passed, $fail failed ==="
if [[ $fail -gt 0 ]]; then
    exit 1
fi

echo "[test-cpu-gemm] SUCCESS: All $pass tests passed (100% success)."
