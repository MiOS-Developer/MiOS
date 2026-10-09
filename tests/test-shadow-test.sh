#!/usr/bin/env bash
# AI-hint: Integration test suite for shadow candidate daemon validator and query mirroring harness (task_485, task_2718).
# AI-doc: usr/share/doc/mios/manual/ch86-shadow-candidate-testing.md
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
SHADOW_BIN="${ROOT_DIR}/usr/libexec/mios/mios-shadow-test"

VERBOSE=0
MOCK_MODE=1
DRY_RUN=0

PASS_COUNT=0
FAIL_COUNT=0
TOTAL_TESTS=5

usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Integration test suite for mios-shadow-test (task_485, task_2718).

Options:
  -v, --verbose   Show diagnostic test output and detailed assertion traces
  --mock          Execute with mock daemons and synthetic fixtures (default: on)
  --dry-run       Test argument parsing and execution planning
  -h, --help      Show this help message and exit
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -v|--verbose)
            VERBOSE=1
            shift
            ;;
        --mock)
            MOCK_MODE=1
            shift
            ;;
        --dry-run)
            DRY_RUN=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown argument: $1" >&2
            usage
            exit 2
            ;;
    esac
done

# Temp directory setup and cleanup
TMP_DIR="$(mktemp -d /tmp/mios-shadow-test-suite.XXXXXX)"
cleanup() {
    rm -rf "${TMP_DIR}"
}
trap cleanup EXIT

log_test() {
    local num="$1"
    local name="$2"
    echo -e "\n=== [Test ${num}/${TOTAL_TESTS}] ${name} ==="
}

assert_pass() {
    local desc="$1"
    shift
    if [[ ${VERBOSE} -eq 1 ]]; then
        echo "  [RUN] ${desc} :: $*"
    fi
    local out
    if out="$("$@" 2>&1)"; then
        echo "  [PASS] ${desc}"
        PASS_COUNT=$((PASS_COUNT + 1))
        if [[ ${VERBOSE} -eq 1 ]]; then
            echo "${out}" | sed 's/^/    | /'
        fi
        return 0
    else
        local rc=$?
        echo "  [FAIL] ${desc} (exit code ${rc})" >&2
        echo "${out}" | sed 's/^/    | /' >&2
        FAIL_COUNT=$((FAIL_COUNT + 1))
        return 1
    fi
}

assert_fail() {
    local desc="$1"
    shift
    if [[ ${VERBOSE} -eq 1 ]]; then
        echo "  [RUN] ${desc} (expect failure) :: $*"
    fi
    local out
    if out="$("$@" 2>&1)"; then
        echo "  [FAIL] ${desc} (command succeeded unexpectedly)" >&2
        echo "${out}" | sed 's/^/    | /' >&2
        FAIL_COUNT=$((FAIL_COUNT + 1))
        return 1
    else
        local rc=$?
        echo "  [PASS] ${desc} (correctly failed with exit code ${rc})"
        PASS_COUNT=$((PASS_COUNT + 1))
        if [[ ${VERBOSE} -eq 1 ]]; then
            echo "${out}" | sed 's/^/    | /'
        fi
        return 0
    fi
}

assert_contains() {
    local desc="$1"
    local needle="$2"
    shift 2
    local out
    if [[ ${VERBOSE} -eq 1 ]]; then
        echo "  [RUN] ${desc} (expecting: '${needle}') :: $*"
    fi
    if out="$("$@" 2>&1)"; then
        if grep -qF -e "${needle}" <<< "${out}"; then
            echo "  [PASS] ${desc}"
            PASS_COUNT=$((PASS_COUNT + 1))
            if [[ ${VERBOSE} -eq 1 ]]; then
                echo "${out}" | sed 's/^/    | /'
            fi
            return 0
        else
            echo "  [FAIL] ${desc} - output does not contain '${needle}'" >&2
            echo "${out}" | sed 's/^/    | /' >&2
            FAIL_COUNT=$((FAIL_COUNT + 1))
            return 1
        fi
    else
        local rc=$?
        echo "  [FAIL] ${desc} (command failed with exit code ${rc})" >&2
        echo "${out}" | sed 's/^/    | /' >&2
        FAIL_COUNT=$((FAIL_COUNT + 1))
        return 1
    fi
}

echo "================================================================="
echo "  MiOS Shadow Dual-Process Validator Integration Test Suite      "
echo "  Target: ${SHADOW_BIN}                                          "
echo "  Mock Mode: ${MOCK_MODE} | Dry Run: ${DRY_RUN}                  "
echo "================================================================="

STATE_FILE="${TMP_DIR}/test-metrics.json"

# -----------------------------------------------------------------------------
# Test 1: CLI and help verification
# -----------------------------------------------------------------------------
log_test 1 "CLI and Help Verification"
assert_pass "Top-level --help exists and exits 0" "${SHADOW_BIN}" --help
assert_contains "Top-level help mentions mirror subcommand" "mirror" "${SHADOW_BIN}" --help
assert_contains "Top-level help mentions validate subcommand" "validate" "${SHADOW_BIN}" --help
assert_contains "Top-level help mentions status subcommand" "status" "${SHADOW_BIN}" --help
assert_pass "Subcommand mirror --help exits 0" "${SHADOW_BIN}" mirror --help
assert_contains "Subcommand mirror help documents --live and --candidate" "--candidate" "${SHADOW_BIN}" mirror --help
assert_pass "Subcommand validate --help exits 0" "${SHADOW_BIN}" validate --help
assert_contains "Subcommand validate help documents --candidate" "--candidate" "${SHADOW_BIN}" validate --help
assert_pass "Subcommand status --help exits 0" "${SHADOW_BIN}" status --help
assert_contains "Subcommand status help documents --json" "--json" "${SHADOW_BIN}" status --help

# -----------------------------------------------------------------------------
# Test 2: Dual query mirroring and parity evaluation (Positive Control)
# -----------------------------------------------------------------------------
log_test 2 "Dual Query Mirroring & Parity Evaluation (Positive Control)"
assert_pass "Clean state file" "${SHADOW_BIN}" --state-file "${STATE_FILE}" status --clear
assert_contains "Mirroring runs with 100% parity on mock daemons" \
    "Parity Score:        100.0%" \
    "${SHADOW_BIN}" --state-file "${STATE_FILE}" mirror --mock

assert_contains "Mirroring verdict is PASS" \
    "Final Verdict:       PASS" \
    "${SHADOW_BIN}" --state-file "${STATE_FILE}" mirror --mock

# -----------------------------------------------------------------------------
# Test 3: Candidate error/divergence detection (Negative Control)
# -----------------------------------------------------------------------------
log_test 3 "Candidate Error & Divergence Detection (Negative Control)"
assert_fail "Divergence simulation exits non-zero (divergence caught)" \
    "${SHADOW_BIN}" --state-file "${STATE_FILE}" mirror --mock --simulate-divergence

assert_contains "Divergence report detects failure verdict" \
    "Final Verdict:       FAIL" \
    bash -c "${SHADOW_BIN} --state-file ${STATE_FILE} mirror --mock --simulate-divergence || true"

assert_contains "Divergence report identifies schema or status mismatch" \
    "Status code mismatch" \
    bash -c "${SHADOW_BIN} --state-file ${STATE_FILE} mirror --mock --simulate-divergence || true"

# -----------------------------------------------------------------------------
# Test 4: Mutating query filter (Negative Control: ensures mutations are blocked)
# -----------------------------------------------------------------------------
log_test 4 "Mutating Query Filter Guard (Negative Control)"
MUTATING_REQ_FILE="${TMP_DIR}/mutating_requests.json"
cat <<'EOF' > "${MUTATING_REQ_FILE}"
[
  {"method": "GET", "path": "/v1/models"},
  {"method": "POST", "path": "/v1/write", "body": {"payload": "forbidden_write"}},
  {"method": "DELETE", "path": "/v1/models/test-model"},
  {"method": "PUT", "path": "/v1/config", "body": {"timeout": 10}},
  {"method": "POST", "path": "/v1/chat/completions", "body": {"model": "default", "messages": [{"role": "user", "content": "ping"}]}}
]
EOF

assert_contains "Mutation Guard blocks POST /v1/write" \
    "[GUARD] Blocked state-mutating request: POST /v1/write" \
    "${SHADOW_BIN}" --state-file "${STATE_FILE}" mirror --mock --requests "${MUTATING_REQ_FILE}"

assert_contains "Mutation Guard blocks DELETE method" \
    "[GUARD] Blocked state-mutating request: DELETE /v1/models/test-model" \
    "${SHADOW_BIN}" --state-file "${STATE_FILE}" mirror --mock --requests "${MUTATING_REQ_FILE}"

assert_contains "Mutation Guard blocks PUT method" \
    "[GUARD] Blocked state-mutating request: PUT /v1/config" \
    "${SHADOW_BIN}" --state-file "${STATE_FILE}" mirror --mock --requests "${MUTATING_REQ_FILE}"

assert_contains "All 3 mutating requests filtered from candidate dispatch" \
    "Blocked Mutations:   3 (Filtered by Mutation Guard)" \
    "${SHADOW_BIN}" --state-file "${STATE_FILE}" mirror --mock --requests "${MUTATING_REQ_FILE}"

# -----------------------------------------------------------------------------
# Test 5: Mock shadow validation run (--mock)
# -----------------------------------------------------------------------------
log_test 5 "Mock Shadow Validation Run & Canary Promotion Gate (--mock)"
assert_pass "Validate subcommand executes mock shadow loop cleanly" \
    "${SHADOW_BIN}" --state-file "${STATE_FILE}" validate --candidate mock-candidate-image:latest --mock

assert_contains "Promotion Gate emits PROMOTION_ALLOWED verdict" \
    "VERDICT: PROMOTION_ALLOWED" \
    "${SHADOW_BIN}" --state-file "${STATE_FILE}" validate --candidate mock-candidate-image:latest --mock

assert_contains "Status subcommand reflects recorded validations in JSON" \
    "\"last_verdict\": \"PASS\"" \
    "${SHADOW_BIN}" --state-file "${STATE_FILE}" status --json

# -----------------------------------------------------------------------------
# Final Summary
# -----------------------------------------------------------------------------
echo -e "\n================================================================="
echo "  TEST SUMMARY"
echo "================================================================="
echo "  Total Assertions: $((PASS_COUNT + FAIL_COUNT))"
echo "  Passed:           ${PASS_COUNT}"
echo "  Failed:           ${FAIL_COUNT}"
echo "================================================================="

if [[ ${FAIL_COUNT} -eq 0 ]]; then
    echo "  RESULT: ALL TESTS PASSED (100% success)"
    exit 0
else
    echo "  RESULT: ${FAIL_COUNT} ASSERTIONS FAILED" >&2
    exit 1
fi
