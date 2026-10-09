#!/usr/bin/env bash
# AI-hint: Verification suite for declarative SSOT blade pre-enrollment registry and TPM EK parser (task_473, task_2706).
# AI-doc: usr/share/doc/mios/manual/ch74-blade-pre-enrollment.md
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

VERBOSE=false
DRY_RUN=false

show_help() {
    cat <<'EOF'
Usage: test-blade-enroll.sh [OPTIONS]

Verification test suite for declarative SSOT blade pre-enrollment registry and
TPM 2.0 Endorsement Key (EK) fingerprint parser (task_473, task_2706).

Options:
  -v, --verbose       Enable verbose test logging
  --dry-run           Execute test assertions in dry-run mode
  --mock              Run tests with mock fixtures and assert mock behaviors
  -h, --help          Show this help message and exit
EOF
}

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
            shift
            ;;
        -h|--help)
            show_help
            exit 0
            ;;
        *)
            echo "Unknown argument: $1" >&2
            exit 1
            ;;
    esac
done

pass_count=0
fail_count=0

log() {
    echo "[test-blade-enroll] $*"
}

diag() {
    if [[ "$VERBOSE" == "true" ]]; then
        echo "  [diag] $*"
    fi
}

assert_pass() {
    local name="$1"
    echo "  PASS: $name"
    pass_count=$((pass_count + 1))
}

assert_fail() {
    local name="$1"
    local reason="${2:-assertion failed}"
    echo "  FAIL: $name - $reason" >&2
    fail_count=$((fail_count + 1))
}

TMP_DIR="$(mktemp -d /tmp/test-blade-enroll.XXXXXX)"
trap '[[ -n "${TMP_DIR:-}" && -d "$TMP_DIR" ]] && rm -rf "$TMP_DIR"' EXIT

ENROLL_TOOL="${ROOT_DIR}/usr/libexec/mios/mios-blade-enroll"

log "=== MiOS Blade Pre-Enrollment & TPM EK Parser Test Suite (task_473, task_2706) ==="

# -----------------------------------------------------------------------------
# Dry-run early exit mode
# -----------------------------------------------------------------------------
if [[ "$DRY_RUN" == "true" ]]; then
    log "Running in dry-run mode: verifying tool syntax and CLI dry-run flags"

    if bash -n "$0" && python3 -m py_compile "$ENROLL_TOOL"; then
        assert_pass "Python compilation and bash test script syntax check"
    else
        assert_fail "Syntax verification failed"
    fi

    if "$ENROLL_TOOL" --help >/dev/null; then
        assert_pass "mios-blade-enroll --help exit status 0"
    else
        assert_fail "mios-blade-enroll --help execution"
    fi

    if "$ENROLL_TOOL" manifest blade-mock-01 --mock --dry-run >/dev/null; then
        assert_pass "mios-blade-enroll manifest --mock --dry-run exit status 0"
    else
        assert_fail "mios-blade-enroll manifest --mock --dry-run execution"
    fi

    log "=== Test Summary (Dry-Run): $pass_count passed, $fail_count failed ==="
    exit 0
fi

# -----------------------------------------------------------------------------
# Test 1: CLI Tooling and Help Verification
# -----------------------------------------------------------------------------
log "Test 1: CLI tooling and help verification"

HELP_OUTPUT="$("$ENROLL_TOOL" --help)"
if echo "$HELP_OUTPUT" | grep -q "Declarative SSOT blade pre-enrollment registry"; then
    assert_pass "mios-blade-enroll --help displays application banner"
else
    assert_fail "mios-blade-enroll --help missing expected banner"
fi

if "$ENROLL_TOOL" status >/dev/null; then
    assert_pass "mios-blade-enroll status executed successfully on host config"
else
    assert_fail "mios-blade-enroll status execution failed"
fi

# -----------------------------------------------------------------------------
# Test 2: Parsing Valid Blade Declarations (Positive Control)
# -----------------------------------------------------------------------------
log "Test 2: Parsing valid blade declarations with valid TPM EK fingerprints (positive control)"

VALID_CONFIG="${TMP_DIR}/valid-blades.toml"
cat <<'EOF' > "$VALID_CONFIG"
[cluster.blades.blade-compute-01]
architecture     = "x86_64"
mac              = "52:54:00:12:34:56"
serial           = "SN-2026-TEST-001"
wireguard_ip     = "10.42.0.10/24"
wireguard_pubkey = "3cE4Wl5k5yQeM/9p2h+o0+o5k/3j1w4h8y1m8x0n4u8="
ek_fingerprint   = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
roles            = ["compute", "worker"]

[cluster.blades.blade-compute-01.pcr_baselines]
0  = "0000000000000000000000000000000000000000000000000000000000000000"
7  = "7777777777777777777777777777777777777777777777777777777777777777"
11 = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

[cluster.blades.blade-storage-02]
architecture     = "aarch64"
mac              = "52:54:00:ab:cd:ef"
serial           = "SN-2026-TEST-002"
wireguard_ip     = "10.42.0.12/24"
ek_fingerprint   = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
roles            = ["storage"]
EOF

PARSE_OUTPUT="$("$ENROLL_TOOL" parse --config "$VALID_CONFIG")"
diag "$PARSE_OUTPUT"

if echo "$PARSE_OUTPUT" | grep -q "Successfully parsed and validated 2 blade declaration(s)"; then
    assert_pass "Positive control: parsed and validated 2 declared blades"
else
    assert_fail "Positive control parse failed"
fi

if echo "$PARSE_OUTPUT" | grep -q "blade-compute-01" && echo "$PARSE_OUTPUT" | grep -q "blade-storage-02"; then
    assert_pass "Positive control: output references both blade IDs"
else
    assert_fail "Positive control missing expected blade IDs in output"
fi

# -----------------------------------------------------------------------------
# Test 3: Negative Control - Rejection of Wildcard ('*') TPM EK Fingerprints
# -----------------------------------------------------------------------------
log "Test 3: Negative control - rejection of wildcard ('*') TPM EK fingerprints"

WILDCARD_CONFIG="${TMP_DIR}/wildcard-ek.toml"
cat <<'EOF' > "$WILDCARD_CONFIG"
[cluster.blades.blade-wildcard]
architecture   = "x86_64"
mac            = "52:54:00:12:34:56"
serial         = "SN-BAD-WILDCARD"
wireguard_ip   = "10.42.0.99"
ek_fingerprint = "*"
EOF

set +e
ERR_WILDCARD="$("$ENROLL_TOOL" parse --config "$WILDCARD_CONFIG" 2>&1)"
RET_WILDCARD=$?
set -e

diag "Exit: $RET_WILDCARD, Output: $ERR_WILDCARD"

if [[ $RET_WILDCARD -ne 0 ]]; then
    assert_pass "Wildcard TPM EK rejection returned non-zero exit code ($RET_WILDCARD)"
else
    assert_fail "Wildcard TPM EK was unexpectedly permitted (exit code 0)"
fi

if echo "$ERR_WILDCARD" | grep -qi "Wildcard ('\*') TPM EK certificates or fingerprints are strictly forbidden"; then
    assert_pass "Wildcard rejection error message contains explicit security notice"
else
    assert_fail "Wildcard rejection missing required security message"
fi

# -----------------------------------------------------------------------------
# Test 4: Negative Control - Rejection of Missing / Empty TPM EK Fingerprints
# -----------------------------------------------------------------------------
log "Test 4: Negative control - rejection of missing / empty TPM EK fingerprints"

EMPTY_CONFIG="${TMP_DIR}/empty-ek.toml"
cat <<'EOF' > "$EMPTY_CONFIG"
[cluster.blades.blade-empty-ek]
architecture   = "x86_64"
mac            = "52:54:00:12:34:56"
serial         = "SN-BAD-EMPTY"
wireguard_ip   = "10.42.0.98"
ek_fingerprint = ""
EOF

set +e
ERR_EMPTY="$("$ENROLL_TOOL" parse --config "$EMPTY_CONFIG" 2>&1)"
RET_EMPTY=$?
set -e

diag "Empty EK Exit: $RET_EMPTY, Output: $ERR_EMPTY"

if [[ $RET_EMPTY -ne 0 ]]; then
    assert_pass "Empty TPM EK returned non-zero exit code ($RET_EMPTY)"
else
    assert_fail "Empty TPM EK unexpectedly passed validation"
fi

MISSING_CONFIG="${TMP_DIR}/missing-ek.toml"
cat <<'EOF' > "$MISSING_CONFIG"
[cluster.blades.blade-missing-ek]
architecture = "x86_64"
mac          = "52:54:00:12:34:56"
serial       = "SN-BAD-MISSING"
wireguard_ip = "10.42.0.97"
EOF

set +e
ERR_MISSING="$("$ENROLL_TOOL" parse --config "$MISSING_CONFIG" 2>&1)"
RET_MISSING=$?
set -e

diag "Missing EK Exit: $RET_MISSING, Output: $ERR_MISSING"

if [[ $RET_MISSING -ne 0 ]]; then
    assert_pass "Missing TPM EK returned non-zero exit code ($RET_MISSING)"
else
    assert_fail "Missing TPM EK unexpectedly passed validation"
fi

if echo "$ERR_MISSING" | grep -qi "missing required TPM EK fingerprint"; then
    assert_pass "Missing EK error message confirms missing fingerprint"
else
    assert_fail "Missing EK error message missing expected text"
fi

# -----------------------------------------------------------------------------
# Test 5: Negative Control - Rejection of Invalid MAC Address Format
# -----------------------------------------------------------------------------
log "Test 5: Negative control - rejection of invalid MAC address format"

BAD_MAC_CONFIG="${TMP_DIR}/bad-mac.toml"
cat <<'EOF' > "$BAD_MAC_CONFIG"
[cluster.blades.blade-bad-mac]
architecture   = "x86_64"
mac            = "52:54:00:12:34:ZZ"
serial         = "SN-BAD-MAC"
wireguard_ip   = "10.42.0.96"
ek_fingerprint = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
EOF

set +e
ERR_MAC="$("$ENROLL_TOOL" parse --config "$BAD_MAC_CONFIG" 2>&1)"
RET_MAC=$?
set -e

diag "Bad MAC Exit: $RET_MAC, Output: $ERR_MAC"

if [[ $RET_MAC -ne 0 ]]; then
    assert_pass "Invalid MAC address returned non-zero exit code ($RET_MAC)"
else
    assert_fail "Invalid MAC address unexpectedly passed validation"
fi

if echo "$ERR_MAC" | grep -qi "Invalid MAC address format"; then
    assert_pass "Error message correctly identifies invalid MAC address format"
else
    assert_fail "Missing invalid MAC address format diagnostic in output"
fi

# -----------------------------------------------------------------------------
# Test 6: Admission Policy Manifest Generation for Coordinator
# -----------------------------------------------------------------------------
log "Test 6: Admission policy manifest generation for coordinator"

MANIFEST_OUT="${TMP_DIR}/admission/blade-compute-01.json"
"$ENROLL_TOOL" manifest blade-compute-01 --config "$VALID_CONFIG" --output "$MANIFEST_OUT"

if [[ -f "$MANIFEST_OUT" ]]; then
    assert_pass "Admission manifest file generated at expected location"
else
    assert_fail "Admission manifest was not created"
fi

# Validate JSON content using Python
python3 -c "
import json, sys
with open('$MANIFEST_OUT') as fh:
    doc = json.load(fh)

assert doc['blade_id'] == 'blade-compute-01', f'blade_id mismatch: {doc}'
assert doc['status'] == 'pre-enrolled', f'status mismatch: {doc}'
assert doc['admission_verdict'] == 'APPROVED', f'admission_verdict mismatch: {doc}'
assert doc['hardware']['architecture'] == 'x86_64'
assert doc['hardware']['mac_address'] == '52:54:00:12:34:56'
assert doc['security']['tpm_ek_fingerprint'] == 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'
assert '0' in doc['security']['allowed_pcr_baselines']
assert '7' in doc['security']['allowed_pcr_baselines']
assert '11' in doc['security']['allowed_pcr_baselines']
assert 'compute' in doc['authorized_roles']
assert 'created_at' in doc
"
assert_pass "Admission manifest JSON schema and fields validated"

# Test manifest-all command
MANIFEST_ALL_DIR="${TMP_DIR}/admission_all"
"$ENROLL_TOOL" manifest-all --config "$VALID_CONFIG" --dir "$MANIFEST_ALL_DIR"

if [[ -f "${MANIFEST_ALL_DIR}/blade-compute-01.json" && -f "${MANIFEST_ALL_DIR}/blade-storage-02.json" ]]; then
    assert_pass "manifest-all generated policy files for all declared blades"
else
    assert_fail "manifest-all failed to produce all expected blade policy files"
fi

# -----------------------------------------------------------------------------
# Test 7: EK Verification Against Registered Blades (Matching vs Mismatch)
# -----------------------------------------------------------------------------
log "Test 7: EK verification against registered blades (matching vs mismatch)"

# 7a: Matching fingerprint
MATCH_OUT="$("$ENROLL_TOOL" verify-ek blade-compute-01 e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 --config "$VALID_CONFIG")"
diag "Matching verify: $MATCH_OUT"
if echo "$MATCH_OUT" | grep -q "\[OK\] TPM EK fingerprint verified"; then
    assert_pass "EK verification succeeded for matching candidate fingerprint"
else
    assert_fail "EK verification failed for matching candidate fingerprint"
fi

# 7b: Mismatched fingerprint
set +e
MISMATCH_ERR="$("$ENROLL_TOOL" verify-ek blade-compute-01 ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad --config "$VALID_CONFIG" 2>&1)"
RET_MISMATCH=$?
set -e

diag "Mismatch verify: Exit: $RET_MISMATCH, Output: $MISMATCH_ERR"
if [[ $RET_MISMATCH -ne 0 ]]; then
    assert_pass "EK verification returned non-zero exit code for mismatched fingerprint"
else
    assert_fail "EK verification unexpectedly succeeded for mismatched fingerprint"
fi

if echo "$MISMATCH_ERR" | grep -qi "TPM EK fingerprint mismatch"; then
    assert_pass "Mismatch error diagnostic properly reported"
else
    assert_fail "Missing mismatch diagnostic in verify-ek output"
fi

# 7c: Wildcard candidate rejection in verify-ek
set +e
WILD_VERIFY_ERR="$("$ENROLL_TOOL" verify-ek blade-compute-01 "*" --config "$VALID_CONFIG" 2>&1)"
RET_WILD_VERIFY=$?
set -e

if [[ $RET_WILD_VERIFY -ne 0 ]]; then
    assert_pass "Wildcard argument to verify-ek strictly rejected"
else
    assert_fail "Wildcard argument to verify-ek was permitted: $WILD_VERIFY_ERR"
fi

# -----------------------------------------------------------------------------
# Test 8: Mock Blade Hardware Enrollment Workflow (--mock)
# -----------------------------------------------------------------------------
log "Test 8: Mock blade hardware enrollment workflow (--mock)"

MOCK_STATUS="$("$ENROLL_TOOL" status --mock)"
diag "Mock status: $MOCK_STATUS"
if echo "$MOCK_STATUS" | grep -q "blade-mock-01" && echo "$MOCK_STATUS" | grep -q "blade-mock-02"; then
    assert_pass "Mock mode status lists synthetic mock blade records"
else
    assert_fail "Mock mode status missing expected mock blades"
fi

MOCK_PARSE="$("$ENROLL_TOOL" parse --mock)"
if echo "$MOCK_PARSE" | grep -q "Successfully parsed and validated 2 blade declaration(s)"; then
    assert_pass "Mock mode parse validated mock blades"
else
    assert_fail "Mock mode parse failed"
fi

MOCK_VERIFY="$("$ENROLL_TOOL" verify-ek blade-mock-01 e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 --mock)"
if echo "$MOCK_VERIFY" | grep -q "\[OK\] TPM EK fingerprint verified"; then
    assert_pass "Mock mode EK verification succeeded for blade-mock-01"
else
    assert_fail "Mock mode EK verification failed for blade-mock-01"
fi

MOCK_DIR="${TMP_DIR}/mock_out"
"$ENROLL_TOOL" manifest-all --dir "$MOCK_DIR" --mock >/dev/null
if [[ -f "${MOCK_DIR}/blade-mock-01.json" && -f "${MOCK_DIR}/blade-mock-02.json" ]]; then
    assert_pass "Mock mode manifest-all generated both mock admission policies"
else
    assert_fail "Mock mode manifest-all failed to write mock manifests"
fi

# -----------------------------------------------------------------------------
# Final Summary
# -----------------------------------------------------------------------------
log "=== Test Results: $pass_count passed, $fail_count failed ==="

if [[ $fail_count -gt 0 ]]; then
    echo "ERROR: One or more test assertions failed!" >&2
    exit 1
fi

echo "ALL TESTS PASSED SUCCESSFULLY (100% pass)."
exit 0
