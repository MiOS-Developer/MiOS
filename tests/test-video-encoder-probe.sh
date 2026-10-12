#!/usr/bin/env bash
# AI-hint: Integration test suite for multi-vendor hardware video encoder discovery and DMA-BUF capture bridge (task_479, task_2712).
# AI-doc: usr/share/doc/mios/manual/ch80-hardware-video-encoding.md
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ROOT_DIR="${ROOT_DIR%/}"
PROBE_BIN="${ROOT_DIR}/usr/libexec/mios/mios-video-encoder-probe"
QUADLET_FILE="${ROOT_DIR}/usr/share/containers/systemd/users/mios-sunshine.container"

VERBOSE=false
DRY_RUN=false
MOCK_MODE=false

pass_count=0
fail_count=0

show_help() {
    cat <<'EOF'
Usage: tests/test-video-encoder-probe.sh [OPTIONS]

Integration test suite for multi-vendor hardware video encoder discovery and
DMA-BUF capture bridge (task_479, task_2712).

Tests:
  Test 1: CLI and help verification.
  Test 2: Hardware encoder probe on synthetic/mock Intel QuickSync fixture (positive control).
  Test 3: Hardware encoder probe on synthetic/mock NVIDIA NVENC fixture (positive control).
  Test 4: Hardware encoder probe on synthetic/mock AMD AMF fixture (positive control).
  Test 5: Codec prioritization logic (AV1 > HEVC > AVC).
  Test 6: Negative control - software fallback when no hardware ASIC is present.
  Test 7: Quadlet container syntax validation.
  Test 8: Mock end-to-end configuration generation (--mock).

Options:
  -v, --verbose    Enable verbose diagnostic output
  --dry-run        Simulate test actions without modifying live system state
  --mock           Force isolated mock test execution
  -h, --help       Show this help message and exit
EOF
}

# Parse command-line arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        -v|--verbose)
            VERBOSE=true
            shift
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --mock)
            MOCK_MODE=true
            shift
            ;;
        -h|--help)
            show_help
            exit 0
            ;;
        *)
            echo "Unknown argument: $1" >&2
            show_help >&2
            exit 1
            ;;
    esac
done

log() {
    echo "[TEST] $*"
}

log_diag() {
    if [[ "$VERBOSE" == "true" ]]; then
        echo "  [DIAG] $*"
    fi
}

assert_pass() {
    pass_count=$((pass_count + 1))
    echo "  [PASS] $*"
}

assert_fail() {
    fail_count=$((fail_count + 1))
    echo "  [FAIL] $*" >&2
}

TMP_DIR="$(mktemp -d /tmp/test-video-probe.XXXXXX)"
trap 'rm -rf "${TMP_DIR}"' EXIT

log "Starting test suite: test-video-encoder-probe.sh"
log "Target probe: ${PROBE_BIN}"
log "Target Quadlet: ${QUADLET_FILE}"
log_diag "Execution mode: DRY_RUN=${DRY_RUN} MOCK_MODE=${MOCK_MODE}"

# ==============================================================================
# Test 1: CLI and help verification
# ==============================================================================
log "Test 1: CLI and help verification"

if [[ -f "$PROBE_BIN" ]]; then
    assert_pass "Probe script exists at ${PROBE_BIN}"
else
    assert_fail "Probe script missing at ${PROBE_BIN}"
fi

if [[ -x "$PROBE_BIN" ]]; then
    assert_pass "Probe script has executable bit (+x)"
else
    assert_fail "Probe script is not executable"
fi

# Top-level --help
set +e
help_out="$("$PROBE_BIN" --help 2>&1)"
help_rc=$?
set -e
if [[ $help_rc -eq 0 && "$help_out" == *"Multi-vendor hardware video encoder"* && "$help_out" == *"probe"* && "$help_out" == *"configure"* && "$help_out" == *"status"* ]]; then
    assert_pass "Top-level --help returned 0 with all subcommands documented"
else
    assert_fail "Top-level --help failed (rc=$help_rc)"
fi

# probe --help
set +e
probe_help_out="$("$PROBE_BIN" probe --help 2>&1)"
probe_help_rc=$?
set -e
if [[ $probe_help_rc -eq 0 && "$probe_help_out" == *"--json"* ]]; then
    assert_pass "'probe --help' documented --json flag"
else
    assert_fail "'probe --help' failed (rc=$probe_help_rc)"
fi

# configure --help
set +e
conf_help_out="$("$PROBE_BIN" configure --help 2>&1)"
conf_help_rc=$?
set -e
if [[ $conf_help_rc -eq 0 && "$conf_help_out" == *"--output"* ]]; then
    assert_pass "'configure --help' documented --output flag"
else
    assert_fail "'configure --help' failed (rc=$conf_help_rc)"
fi

# status --help
set +e
status_help_out="$("$PROBE_BIN" status --help 2>&1)"
status_help_rc=$?
set -e
if [[ $status_help_rc -eq 0 && -n "$status_help_out" ]]; then
    assert_pass "'status --help' returned 0"
else
    assert_fail "'status --help' failed (rc=$status_help_rc)"
fi

# ==============================================================================
# Test 2: Hardware encoder probe on synthetic/mock Intel QuickSync fixture (positive control)
# ==============================================================================
log "Test 2: Hardware encoder probe on synthetic/mock Intel QuickSync fixture (positive control)"

set +e
intel_json="$("$PROBE_BIN" probe --mock intel --json 2>&1)"
intel_rc=$?
set -e

if [[ $intel_rc -ne 0 ]]; then
    assert_fail "Intel QuickSync probe failed with exit code $intel_rc"
else
    log_diag "Intel probe output: $intel_json"
    intel_eval="$(python3 -c "
import sys, json
data = json.loads('''$intel_json''')
assert data['hardware_available'] is True, 'hardware_available is not True'
assert data['primary_vendor'] == 'intel', f'vendor was {data.get(\"primary_vendor\")}'
assert 'QuickSync' in data['primary_adapter']['asic'], 'QuickSync missing in asic'
assert data['primary_adapter']['recommended_encoder'] == 'vaapi', 'encoder is not vaapi'
assert data['dma_buf_zero_copy'] is True, 'dma_buf_zero_copy is not True'
assert 'AV1' in data['supported_codecs'], 'AV1 missing in codecs'
assert data['preferred_codec'] == 'AV1', f'preferred was {data.get(\"preferred_codec\")}'
print('OK')
" 2>&1)"
    if [[ "$intel_eval" == *"OK"* ]]; then
        assert_pass "Intel QuickSync correctly detected with VA-API encoder, AV1 codec, and DMA-BUF zero-copy"
    else
        assert_fail "Intel QuickSync evaluation assertion failed: $intel_eval"
    fi
fi

# ==============================================================================
# Test 3: Hardware encoder probe on synthetic/mock NVIDIA NVENC fixture (positive control)
# ==============================================================================
log "Test 3: Hardware encoder probe on synthetic/mock NVIDIA NVENC fixture (positive control)"

set +e
nvidia_json="$("$PROBE_BIN" probe --mock nvidia --json 2>&1)"
nvidia_rc=$?
set -e

if [[ $nvidia_rc -ne 0 ]]; then
    assert_fail "NVIDIA NVENC probe failed with exit code $nvidia_rc"
else
    log_diag "NVIDIA probe output: $nvidia_json"
    nvidia_eval="$(python3 -c "
import sys, json
data = json.loads('''$nvidia_json''')
assert data['hardware_available'] is True, 'hardware_available is not True'
assert data['primary_vendor'] == 'nvidia', f'vendor was {data.get(\"primary_vendor\")}'
assert 'NVENC' in data['primary_adapter']['asic'], 'NVENC missing in asic'
assert data['primary_adapter']['recommended_encoder'] == 'nvenc', 'encoder is not nvenc'
assert data['dma_buf_zero_copy'] is True, 'dma_buf_zero_copy is not True'
assert data['preferred_codec'] == 'AV1', f'preferred was {data.get(\"preferred_codec\")}'
print('OK')
" 2>&1)"
    if [[ "$nvidia_eval" == *"OK"* ]]; then
        assert_pass "NVIDIA NVENC correctly detected with NVENC encoder, AV1 codec, and DMA-BUF zero-copy"
    else
        assert_fail "NVIDIA NVENC evaluation assertion failed: $nvidia_eval"
    fi
fi

# ==============================================================================
# Test 4: Hardware encoder probe on synthetic/mock AMD AMF fixture (positive control)
# ==============================================================================
log "Test 4: Hardware encoder probe on synthetic/mock AMD AMF fixture (positive control)"

set +e
amd_json="$("$PROBE_BIN" probe --mock amd --json 2>&1)"
amd_rc=$?
set -e

if [[ $amd_rc -ne 0 ]]; then
    assert_fail "AMD AMF probe failed with exit code $amd_rc"
else
    log_diag "AMD probe output: $amd_json"
    amd_eval="$(python3 -c "
import sys, json
data = json.loads('''$amd_json''')
assert data['hardware_available'] is True, 'hardware_available is not True'
assert data['primary_vendor'] == 'amd', f'vendor was {data.get(\"primary_vendor\")}'
assert 'AMF' in data['primary_adapter']['asic'], 'AMF missing in asic'
assert data['primary_adapter']['recommended_encoder'] == 'amf', 'encoder is not amf'
assert data['dma_buf_zero_copy'] is True, 'dma_buf_zero_copy is not True'
assert data['preferred_codec'] == 'AV1', f'preferred was {data.get(\"preferred_codec\")}'
print('OK')
" 2>&1)"
    if [[ "$amd_eval" == *"OK"* ]]; then
        assert_pass "AMD AMF/VCN correctly detected with AMF encoder, AV1 codec, and DMA-BUF zero-copy"
    else
        assert_fail "AMD AMF evaluation assertion failed: $amd_eval"
    fi
fi

# ==============================================================================
# Test 5: Codec prioritization logic (AV1 > HEVC > AVC)
# ==============================================================================
log "Test 5: Codec prioritization logic (AV1 > HEVC > AVC)"

# Case 5a: Hardware supports AV1, HEVC, AVC -> must resolve to AV1
p1_json="$("$PROBE_BIN" probe --mock intel --mock-codecs "AV1,HEVC,AVC" --json)"
p1_codec="$(python3 -c "import json; print(json.loads('''$p1_json''')['preferred_codec'])")"
if [[ "$p1_codec" == "AV1" ]]; then
    assert_pass "Priority 1: Selected AV1 when [AV1, HEVC, AVC] available"
else
    assert_fail "Priority 1 failed: Expected AV1, got $p1_codec"
fi

# Case 5b: Hardware supports only HEVC, AVC -> must resolve to HEVC
p2_json="$("$PROBE_BIN" probe --mock intel --mock-codecs "HEVC,AVC" --json)"
p2_codec="$(python3 -c "import json; print(json.loads('''$p2_json''')['preferred_codec'])")"
if [[ "$p2_codec" == "HEVC" ]]; then
    assert_pass "Priority 2: Selected HEVC when [HEVC, AVC] available"
else
    assert_fail "Priority 2 failed: Expected HEVC, got $p2_codec"
fi

# Case 5c: Hardware supports only AVC -> must resolve to AVC
p3_json="$("$PROBE_BIN" probe --mock intel --mock-codecs "AVC" --json)"
p3_codec="$(python3 -c "import json; print(json.loads('''$p3_json''')['preferred_codec'])")"
if [[ "$p3_codec" == "AVC" ]]; then
    assert_pass "Priority 3: Selected AVC when only [AVC] available"
else
    assert_fail "Priority 3 failed: Expected AVC, got $p3_codec"
fi

# ==============================================================================
# Test 6: Negative control - software fallback when no hardware ASIC is present
# ==============================================================================
log "Test 6: Negative control - software fallback when no hardware ASIC is present"

set +e
none_json="$("$PROBE_BIN" probe --mock none --json 2>&1)"
none_rc=$?
set -e

if [[ $none_rc -ne 0 ]]; then
    assert_fail "Software fallback probe failed with exit code $none_rc"
else
    log_diag "None probe output: $none_json"
    none_eval="$(python3 -c "
import sys, json
data = json.loads('''$none_json''')
assert data['hardware_available'] is False, 'hardware_available should be False'
assert data['primary_vendor'] == 'none', f'vendor was {data.get(\"primary_vendor\")}'
assert data['primary_adapter']['recommended_encoder'] == 'software', 'encoder should be software'
assert data['dma_buf_zero_copy'] is False, 'dma_buf_zero_copy should be False'
assert 'shm' in data['active_capture_bridge'], 'bridge should be shm fallback'
print('OK')
" 2>&1)"
    if [[ "$none_eval" == *"OK"* ]]; then
        assert_pass "Software fallback engaged properly: hardware_available=False, encoder=software, dma_buf=False"
    else
        assert_fail "Software fallback assertion failed: $none_eval"
    fi
fi

# ==============================================================================
# Test 7: Quadlet container syntax validation
# ==============================================================================
log "Test 7: Quadlet container syntax validation"

if [[ -f "$QUADLET_FILE" ]]; then
    assert_pass "Quadlet container file exists at ${QUADLET_FILE}"
else
    assert_fail "Quadlet container file missing at ${QUADLET_FILE}"
fi

quadlet_eval="$(python3 -c "
import configparser
cp = configparser.ConfigParser(strict=False)
cp.read('${QUADLET_FILE}')
sections = cp.sections()

for req in ['Unit', 'Container', 'Install', 'Service']:
    assert req in sections, f'Missing required section {req}'

assert cp.get('Container', 'ContainerName', fallback='') == 'mios-sunshine', 'ContainerName not mios-sunshine'
assert 'sunshine' in cp.get('Container', 'Image', fallback=''), 'Image does not mention sunshine'

# Verify device and capability entries
raw = open('${QUADLET_FILE}').read()
assert 'AddDevice=/dev/dri' in raw, 'AddDevice=/dev/dri missing'
assert 'AddDevice=/dev/uinput' in raw, 'AddDevice=/dev/uinput missing'
assert 'CAP_SYS_NICE' in raw, 'CAP_SYS_NICE capability missing'
assert 'Network=host' in raw, 'Network=host missing'
assert '%h/.config/sunshine' in raw, 'User config volume missing'

print('OK')
" 2>&1)"

if [[ "$quadlet_eval" == *"OK"* ]]; then
    assert_pass "Quadlet syntax, sections, devices, and capabilities validated successfully"
else
    assert_fail "Quadlet validation failed: $quadlet_eval"
fi

# ==============================================================================
# Test 8: Mock end-to-end configuration generation (--mock)
# ==============================================================================
log "Test 8: Mock end-to-end configuration generation (--mock)"

MOCK_OUT_JSON="${TMP_DIR}/sunshine.json"
MOCK_OUT_CONF="${TMP_DIR}/sunshine.conf"

# Generate JSON configuration for Intel QuickSync
"$PROBE_BIN" configure --mock intel --output "$MOCK_OUT_JSON"
if [[ -f "$MOCK_OUT_JSON" ]]; then
    json_verify="$(python3 -c "
import json
with open('${MOCK_OUT_JSON}') as f:
    cfg = json.load(f)
assert cfg['encoder'] == 'vaapi', f'unexpected encoder: {cfg.get(\"encoder\")}'
assert cfg['codec'] == 'av1', f'unexpected codec: {cfg.get(\"codec\")}'
assert cfg['capture_mode'] == 'dmabuf', f'unexpected capture_mode: {cfg.get(\"capture_mode\")}'
assert cfg['dma_buf']['zero_copy'] is True, 'zero_copy is not True'
assert cfg['audio']['sink'] == 'mios-null-sink', 'audio sink not mios-null-sink'
print('OK')
")"
    if [[ "$json_verify" == *"OK"* ]]; then
        assert_pass "Generated Sunshine JSON configuration verified: encoder=vaapi, codec=av1, dmabuf=True"
    else
        assert_fail "JSON configuration content mismatch: $json_verify"
    fi
else
    assert_fail "Expected output file ${MOCK_OUT_JSON} was not generated"
fi

# Generate INI/CONF configuration for NVIDIA NVENC
"$PROBE_BIN" configure --mock nvidia --output "$MOCK_OUT_CONF"
if [[ -f "$MOCK_OUT_CONF" ]]; then
    conf_content="$(cat "$MOCK_OUT_CONF")"
    if [[ "$conf_content" == *"encoder = nvenc"* && "$conf_content" == *"codec = av1"* && "$conf_content" == *"capture_mode = dmabuf"* && "$conf_content" == *"audio_sink = mios-null-sink"* ]]; then
        assert_pass "Generated Sunshine CONF configuration verified: encoder=nvenc, codec=av1, dmabuf"
    else
        assert_fail "CONF configuration missing expected entries: $conf_content"
    fi
else
    assert_fail "Expected output file ${MOCK_OUT_CONF} was not generated"
fi

# ==============================================================================
# Summary
# ==============================================================================
echo ""
echo "============================================================"
echo " Test Summary: ${pass_count} passed, ${fail_count} failed"
echo "============================================================"

if [[ $fail_count -eq 0 ]]; then
    echo "[TEST RESULT] ALL TESTS PASSED (100% success)"
    exit 0
else
    echo "[TEST RESULT] VERIFICATION FAILED (${fail_count} failures)" >&2
    exit 1
fi
