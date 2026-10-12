#!/usr/bin/env bash
# AI-hint: Verification suite for peripheral hardware health evaluator and non-fatal Greenboot degradation reporter (task_475, task_2708).
# AI-doc: usr/share/doc/mios/manual/ch75-greenboot-hardware-degrade.md
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ROOT_DIR="${ROOT_DIR%/}"
TARGET_SCRIPT="${ROOT_DIR}/usr/lib/greenboot/check/wanted.d/20-hardware-degrade.sh"

VERBOSE=false
DRY_RUN=false
MOCK_MODE=false

pass_count=0
fail_count=0

show_help() {
    cat <<'EOF'
Usage: test-greenboot-hardware-degrade.sh [OPTIONS]

Integration test suite for peripheral hardware health evaluator and non-fatal
Greenboot degradation reporter (task_475, task_2708).

Tests:
  Test 1: Script existence, executable bit, and help output.
  Test 2: Nominal hardware probe execution on current environment.
  Test 3: Synthetic degraded network controller probe (asserts degraded state logged and EXIT CODE IS 0).
  Test 4: Synthetic degraded audio controller probe (asserts degraded state logged and EXIT CODE IS 0).
  Test 5: Synthetic degraded display controller probe (asserts degraded state logged and EXIT CODE IS 0).
  Test 6: Verification that script NEVER returns non-zero exit code on peripheral driver failures.
  Test 7: Log file generation and structured event output format.

Options:
  -v, --verbose       Enable verbose test diagnostic output
  --dry-run           Run tests asserting dry-run evaluation behavior
  --mock              Run all tests using hermetic mock fixtures
  -h, --help          Show this help message and exit
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

# Create temporary working directory for test fixtures and logs
TMP_DIR="$(mktemp -d /tmp/test-hw-degrade.XXXXXX)"
TMP_LOG="${TMP_DIR}/test-hardware-degrade.log"
trap 'rm -rf "${TMP_DIR}"' EXIT

log "Starting test suite: test-greenboot-hardware-degrade.sh"
log "Target evaluator: ${TARGET_SCRIPT}"

# ==============================================================================
# Test 1: Script existence, executable bit, and help output
# ==============================================================================
log "Test 1: Script existence, executable bit, and help output"

if [[ -f "$TARGET_SCRIPT" ]]; then
    assert_pass "Evaluator script exists at ${TARGET_SCRIPT}"
else
    assert_fail "Evaluator script missing at ${TARGET_SCRIPT}"
fi

if [[ -x "$TARGET_SCRIPT" ]]; then
    assert_pass "Evaluator script has executable permissions (+x)"
else
    assert_fail "Evaluator script is not executable"
fi

# 1a. Help option -h
set +e
help_short_out="$("$TARGET_SCRIPT" -h 2>&1)"
help_short_rc=$?
set -e

if [[ $help_short_rc -eq 0 && "$help_short_out" == *"Usage:"* ]]; then
    assert_pass "Option -h displayed usage and exited 0"
else
    assert_fail "Option -h failed (exit $help_short_rc)"
fi

# 1b. Help option --help
set +e
help_long_out="$("$TARGET_SCRIPT" --help 2>&1)"
help_long_rc=$?
set -e

if [[ $help_long_rc -eq 0 && "$help_long_out" == *"Usage:"* ]]; then
    assert_pass "Option --help displayed usage and exited 0"
else
    assert_fail "Option --help failed (exit $help_long_rc)"
fi

# ==============================================================================
# Test 2: Nominal hardware probe execution on current environment
# ==============================================================================
log "Test 2: Nominal hardware probe execution on current environment"

set +e
if [[ "$DRY_RUN" == "true" ]]; then
    probe_out="$("$TARGET_SCRIPT" --dry-run 2>&1)"
    probe_rc=$?
elif [[ "$MOCK_MODE" == "true" ]]; then
    probe_out="$("$TARGET_SCRIPT" --mock-healthy --log-file "$TMP_LOG" 2>&1)"
    probe_rc=$?
else
    probe_out="$("$TARGET_SCRIPT" --log-file "$TMP_LOG" 2>&1)"
    probe_rc=$?
fi
set -e

log_diag "Probe output: ${probe_out}"

if [[ $probe_rc -eq 0 ]]; then
    assert_pass "Evaluator executed on current environment with exit code 0"
else
    assert_fail "Evaluator failed on current environment with exit code $probe_rc"
fi

if [[ "$probe_out" == *"[greenboot-hardware]"* ]]; then
    assert_pass "Evaluator emitted structured [greenboot-hardware] log header"
else
    assert_fail "Output missing [greenboot-hardware] tag"
fi

# ==============================================================================
# Test 3: Synthetic degraded network controller probe
# ==============================================================================
log "Test 3: Synthetic degraded network controller probe"

set +e
net_deg_out="$("$TARGET_SCRIPT" --mock-degrade network --log-file "$TMP_LOG" 2>&1)"
net_deg_rc=$?
set -e

log_diag "Degraded network output: ${net_deg_out}"

if [[ $net_deg_rc -eq 0 ]]; then
    assert_pass "Synthetic degraded network probe returned exit code 0 (non-blocking)"
else
    assert_fail "Synthetic degraded network probe exited non-zero: $net_deg_rc"
fi

if [[ "$net_deg_out" == *"DEGRADED"* && "$net_deg_out" == *"network"* ]]; then
    assert_pass "Degraded network state successfully registered and reported"
else
    assert_fail "Output did not indicate degraded network state: ${net_deg_out}"
fi

# ==============================================================================
# Test 4: Synthetic degraded audio controller probe
# ==============================================================================
log "Test 4: Synthetic degraded audio controller probe"

set +e
audio_deg_out="$("$TARGET_SCRIPT" --mock-degrade audio --log-file "$TMP_LOG" 2>&1)"
audio_deg_rc=$?
set -e

log_diag "Degraded audio output: ${audio_deg_out}"

if [[ $audio_deg_rc -eq 0 ]]; then
    assert_pass "Synthetic degraded audio probe returned exit code 0 (non-blocking)"
else
    assert_fail "Synthetic degraded audio probe exited non-zero: $audio_deg_rc"
fi

if [[ "$audio_deg_out" == *"DEGRADED"* && "$audio_deg_out" == *"audio"* ]]; then
    assert_pass "Degraded audio state successfully registered and reported"
else
    assert_fail "Output did not indicate degraded audio state: ${audio_deg_out}"
fi

# ==============================================================================
# Test 5: Synthetic degraded display controller probe
# ==============================================================================
log "Test 5: Synthetic degraded display controller probe"

set +e
disp_deg_out="$("$TARGET_SCRIPT" --mock-degrade display --log-file "$TMP_LOG" 2>&1)"
disp_deg_rc=$?
set -e

log_diag "Degraded display output: ${disp_deg_out}"

if [[ $disp_deg_rc -eq 0 ]]; then
    assert_pass "Synthetic degraded display probe returned exit code 0 (non-blocking)"
else
    assert_fail "Synthetic degraded display probe exited non-zero: $disp_deg_rc"
fi

if [[ "$disp_deg_out" == *"DEGRADED"* && "$disp_deg_out" == *"display"* ]]; then
    assert_pass "Degraded display state successfully registered and reported"
else
    assert_fail "Output did not indicate degraded display state: ${disp_deg_out}"
fi

# ==============================================================================
# Test 6: Non-fatal promotion guarantee under complete peripheral driver failures
# ==============================================================================
log "Test 6: Non-fatal promotion guarantee under complete peripheral driver failures"

# 6a. All peripherals degraded
set +e
all_deg_out="$("$TARGET_SCRIPT" --mock-degrade all --log-file "$TMP_LOG" 2>&1)"
all_deg_rc=$?
set -e

log_diag "All degraded output: ${all_deg_out}"

if [[ $all_deg_rc -eq 0 ]]; then
    assert_pass "All peripherals simultaneously degraded: exit code 0 (promotion guaranteed)"
else
    assert_fail "All peripherals degraded exited non-zero: $all_deg_rc"
fi

# 6b. Empty sysfs / proc fixture paths (missing devices and drivers)
EMPTY_FIXTURE_DIR="${TMP_DIR}/empty_fixtures"
mkdir -p "${EMPTY_FIXTURE_DIR}"

set +e
empty_fixture_out="$(
    MIOS_SYSFS_NET_DIR="${EMPTY_FIXTURE_DIR}/net" \
    MIOS_ASOUND_CARDS_FILE="${EMPTY_FIXTURE_DIR}/proc_asound_cards" \
    MIOS_DEV_DRI_DIR="${EMPTY_FIXTURE_DIR}/dev_dri" \
    MIOS_SYSFS_DRM_DIR="${EMPTY_FIXTURE_DIR}/sys_drm" \
    "$TARGET_SCRIPT" --log-file "$TMP_LOG" 2>&1
)"
empty_fixture_rc=$?
set -e

log_diag "Empty fixtures output: ${empty_fixture_out}"

if [[ $empty_fixture_rc -eq 0 ]]; then
    assert_pass "Empty hardware fixtures (missing drivers/nodes): exit code 0 (promotion guaranteed)"
else
    assert_fail "Empty hardware fixtures returned non-zero exit code: $empty_fixture_rc"
fi

# 6c. Internal mock simulation runner
set +e
mock_sim_out="$("$TARGET_SCRIPT" --mock 2>&1)"
mock_sim_rc=$?
set -e

log_diag "Mock simulation output: ${mock_sim_out}"

if [[ $mock_sim_rc -eq 0 && "$mock_sim_out" == *"5 passed, 0 failed"* ]]; then
    assert_pass "Evaluator self-test --mock verified 5/5 scenarios exit 0"
else
    assert_fail "Evaluator self-test --mock failed: rc=$mock_sim_rc, out=$mock_sim_out"
fi

# 6d. Dry run execution
set +e
dry_run_out="$("$TARGET_SCRIPT" --dry-run 2>&1)"
dry_run_rc=$?
set -e

log_diag "Dry run output: ${dry_run_out}"

if [[ $dry_run_rc -eq 0 ]]; then
    assert_pass "Dry-run execution completed with exit code 0"
else
    assert_fail "Dry-run execution failed with exit code $dry_run_rc"
fi

# ==============================================================================
# Test 7: Log file generation and structured event output format
# ==============================================================================
log "Test 7: Log file generation and structured event output format"

if [[ -f "$TMP_LOG" && -s "$TMP_LOG" ]]; then
    assert_pass "Log file generated and populated at ${TMP_LOG}"
else
    assert_fail "Log file missing or empty at ${TMP_LOG}"
fi

# Validate JSON structure in log file
set +e
python3 -c "
import json, re, sys

log_file = '${TMP_LOG}'
with open(log_file, 'r', encoding='utf-8') as f:
    lines = [line.strip() for line in f if line.strip()]

if not lines:
    print('Error: Log file has no lines', file=sys.stderr)
    sys.exit(1)

# Inspect the most recent log entry
last_line = lines[-1]
match = re.search(r'EVENT_JSON:\s*(\{.*\})', last_line)
if not match:
    print('Error: Could not locate EVENT_JSON payload in line: ' + last_line, file=sys.stderr)
    sys.exit(1)

json_str = match.group(1)
data = json.loads(json_str)

required_keys = ['timestamp', 'event_type', 'overall_status', 'degraded_count', 'policy', 'action', 'subsystems']
for k in required_keys:
    if k not in data:
        print(f'Error: Missing required root key: {k}', file=sys.stderr)
        sys.exit(1)

if data['policy'] != 'degrade-not-refuse':
    print(f'Error: Unexpected policy: {data[\"policy\"]}', file=sys.stderr)
    sys.exit(1)

if data['action'] != 'log_and_promote':
    print(f'Error: Unexpected action: {data[\"action\"]}', file=sys.stderr)
    sys.exit(1)

subsystems = data['subsystems']
for sub in ['network', 'audio', 'display']:
    if sub not in subsystems:
        print(f'Error: Missing subsystem: {sub}', file=sys.stderr)
        sys.exit(1)
    if 'status' not in subsystems[sub] or 'details' not in subsystems[sub]:
        print(f'Error: Incomplete subsystem entry for {sub}', file=sys.stderr)
        sys.exit(1)

print('JSON schema validation successful')
"
json_check_rc=$?
set -e

if [[ $json_check_rc -eq 0 ]]; then
    assert_pass "Structured event JSON payload validated successfully (schema, keys, degrade-not-refuse policy)"
else
    assert_fail "Structured event JSON validation failed"
fi

# ==============================================================================
# Summary
# ==============================================================================
log "=== Test Suite Summary: $pass_count passed, $fail_count failed ==="

if [[ $fail_count -gt 0 ]]; then
    log "FAILURE: $fail_count test(s) failed."
    exit 1
fi

log "SUCCESS: All $pass_count tests passed (100% pass rate)."
exit 0
