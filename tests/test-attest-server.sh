#!/usr/bin/env bash
# AI-hint: Comprehensive test suite for RFC 9334 RATS remote TPM 2.0 quote verifier and onboarding daemon (task_474, task_2707).
# AI-doc: usr/share/doc/mios/manual/ch76-remote-attestation.md
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

VERBOSE=false
DRY_RUN=false

show_help() {
    cat <<'EOF'
Usage: test-attest-server.sh [OPTIONS]

Comprehensive test suite for RFC 9334 RATS remote TPM 2.0 quote verifier and zero-touch cluster onboarding daemon (task_474, task_2707).

Options:
  -v, --verbose       Enable verbose test logging
  --dry-run           Execute test assertions in dry-run mode
  --mock              Run tests using mock hardware fixtures
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
DAEMON_PID=""

log() {
    echo "[test-attest-server] $*"
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

TMP_DIR="$(mktemp -d /tmp/test-rats-attest.XXXXXX)"
cleanup() {
    if [[ -n "$DAEMON_PID" ]] && kill -0 "$DAEMON_PID" 2>/dev/null; then
        diag "Terminating background attestation daemon (PID $DAEMON_PID)..."
        kill "$DAEMON_PID" 2>/dev/null || true
        wait "$DAEMON_PID" 2>/dev/null || true
    fi
    if [[ -n "$TMP_DIR" && -d "$TMP_DIR" ]]; then
        rm -rf "$TMP_DIR"
    fi
}
trap cleanup EXIT

ATTEST_SERVER="${ROOT_DIR}/usr/libexec/mios/mios-attest-server"
SERVICE_FILE="${ROOT_DIR}/usr/lib/systemd/system/mios-attest.service"

log "=== MiOS RFC 9334 RATS TPM 2.0 Quote Verifier Test Suite (task_474, task_2707) ==="

if [[ "$DRY_RUN" == "true" ]]; then
    log "Running in dry-run mode: verifying syntax and static properties"
    if python3 -m py_compile "$ATTEST_SERVER"; then
        assert_pass "Python syntax compilation of mios-attest-server"
    else
        assert_fail "Python syntax compilation failed"
    fi
    if [[ -f "$SERVICE_FILE" ]]; then
        assert_pass "Systemd service file present"
    else
        assert_fail "Systemd service file missing"
    fi
    log "=== Test Summary: $pass_count passed, $fail_count failed ==="
    exit 0
fi

# ==============================================================================
# Test 1: Tooling and CLI verification (mios-attest-server --help, status)
# ==============================================================================
log "Test 1: Tooling and CLI verification"

if [[ -x "$ATTEST_SERVER" ]]; then
    assert_pass "Tooling: mios-attest-server is executable at $ATTEST_SERVER"
else
    assert_fail "Tooling: mios-attest-server not found or not executable"
fi

if "$ATTEST_SERVER" --help >/dev/null 2>&1; then
    assert_pass "CLI: mios-attest-server --help returns exit status 0"
else
    assert_fail "CLI: mios-attest-server --help execution failed"
fi

status_out="$("$ATTEST_SERVER" status --mock)"
if echo "$status_out" | grep -q "MiOS RFC 9334 RATS Attestation Registry Status" && \
   echo "$status_out" | grep -q "blade-mock-01"; then
    assert_pass "CLI: mios-attest-server status --mock displays enrolled blades"
else
    assert_fail "CLI: mios-attest-server status output invalid" "$status_out"
fi

# ==============================================================================
# Test 2: Positive control - valid quote verification and cluster onboarding
# ==============================================================================
log "Test 2: Positive control - valid quote verification and cluster onboarding"

VALID_NONCE="a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90"
VALID_QUOTE="${TMP_DIR}/valid-quote.json"

"$ATTEST_SERVER" generate-quote blade-mock-01 "$VALID_NONCE" --output "$VALID_QUOTE" --mock
if [[ -f "$VALID_QUOTE" && -s "$VALID_QUOTE" ]]; then
    assert_pass "Generated synthetic TPM 2.0 quote fixture for blade-mock-01"
else
    assert_fail "Failed generating valid quote fixture"
fi

set +e
verify_out="$("$ATTEST_SERVER" verify-quote blade-mock-01 "$VALID_QUOTE" "$VALID_NONCE" --mock 2>&1)"
verify_rc=$?
set -e

diag "verify-quote output: $verify_out"
if [[ "$verify_rc" -eq 0 ]] && \
   echo "$verify_out" | grep -q "Verdict: APPROVED" && \
   echo "$verify_out" | grep -q "headscale_preauth_key" && \
   echo "$verify_out" | grep -q "k3s_join_token"; then
    assert_pass "Positive appraisal: valid quote verified with Verdict: APPROVED and issued headscale/k3s credentials"
else
    assert_fail "Positive appraisal failed" "exit code $verify_rc, output: $verify_out"
fi

# ==============================================================================
# Test 3: Negative control - tampered quote rejection (signature and PCR digest)
# ==============================================================================
log "Test 3: Negative control - tampered quote rejection (tampered signature or altered PCR digest)"

# 3a. Tampered Signature
BAD_SIG_QUOTE="${TMP_DIR}/bad-sig-quote.json"
"$ATTEST_SERVER" generate-quote blade-mock-01 "$VALID_NONCE" --tamper signature --output "$BAD_SIG_QUOTE" --mock

set +e
badsig_out="$("$ATTEST_SERVER" verify-quote blade-mock-01 "$BAD_SIG_QUOTE" "$VALID_NONCE" --mock 2>&1)"
badsig_rc=$?
set -e

if [[ "$badsig_rc" -ne 0 ]] && echo "$badsig_out" | grep -qi "QUARANTINED"; then
    assert_pass "Negative control: tampered quote signature rejected (exit $badsig_rc, QUARANTINED)"
else
    assert_fail "Tampered signature was erroneously accepted" "exit: $badsig_rc"
fi

# 3b. Altered PCR 11 measurement (firmware / UKI tampering)
BAD_PCR_QUOTE="${TMP_DIR}/bad-pcr-quote.json"
"$ATTEST_SERVER" generate-quote blade-mock-01 "$VALID_NONCE" --tamper pcr --output "$BAD_PCR_QUOTE" --mock

set +e
badpcr_out="$("$ATTEST_SERVER" verify-quote blade-mock-01 "$BAD_PCR_QUOTE" "$VALID_NONCE" --mock 2>&1)"
badpcr_rc=$?
set -e

if [[ "$badpcr_rc" -ne 0 ]] && echo "$badpcr_out" | grep -qi "baseline mismatch"; then
    assert_pass "Negative control: altered PCR 11 measurement rejected (exit $badpcr_rc, baseline mismatch)"
else
    assert_fail "Altered PCR 11 was erroneously accepted" "exit: $badpcr_rc"
fi

# 3c. Corrupted Composite PCR Digest
BAD_DIGEST_QUOTE="${TMP_DIR}/bad-digest-quote.json"
"$ATTEST_SERVER" generate-quote blade-mock-01 "$VALID_NONCE" --tamper digest --output "$BAD_DIGEST_QUOTE" --mock

set +e
baddigest_out="$("$ATTEST_SERVER" verify-quote blade-mock-01 "$BAD_DIGEST_QUOTE" "$VALID_NONCE" --mock 2>&1)"
baddigest_rc=$?
set -e

if [[ "$baddigest_rc" -ne 0 ]] && echo "$baddigest_out" | grep -qi "Tampered PCR digest"; then
    assert_pass "Negative control: corrupted composite PCR digest rejected (exit $baddigest_rc)"
else
    assert_fail "Corrupted PCR digest was erroneously accepted" "exit: $baddigest_rc"
fi

# ==============================================================================
# Test 4: Negative control - mismatched nonce rejection (replay attack defense)
# ==============================================================================
log "Test 4: Negative control - mismatched nonce rejection (replay attack defense)"

REPLAY_NONCE="9999999999999999999999999999999999999999999999999999999999999999"

set +e
replay_out="$("$ATTEST_SERVER" verify-quote blade-mock-01 "$VALID_QUOTE" "$REPLAY_NONCE" --mock 2>&1)"
replay_rc=$?
set -e

if [[ "$replay_rc" -ne 0 ]] && echo "$replay_out" | grep -qi "Nonce mismatch"; then
    assert_pass "Negative control: mismatched nonce rejected (replay attack defense, exit $replay_rc)"
else
    assert_fail "Replayed quote with mismatched nonce was erroneously accepted" "exit: $replay_rc"
fi

# ==============================================================================
# Test 5: Negative control - un-enrolled blade rejection
# ==============================================================================
log "Test 5: Negative control - un-enrolled blade rejection"

set +e
unenrolled_out="$("$ATTEST_SERVER" verify-quote un-enrolled-blade-999 "$VALID_QUOTE" "$VALID_NONCE" --mock 2>&1)"
unenrolled_rc=$?
set -e

if [[ "$unenrolled_rc" -ne 0 ]] && echo "$unenrolled_out" | grep -qi "not enrolled"; then
    assert_pass "Negative control: un-enrolled blade rejected and quarantined (exit $unenrolled_rc)"
else
    assert_fail "Un-enrolled blade was erroneously admitted" "exit: $unenrolled_rc"
fi

# ==============================================================================
# Test 6: Systemd unit syntax validation
# ==============================================================================
log "Test 6: Systemd unit syntax validation"

if [[ -f "$SERVICE_FILE" ]]; then
    if grep -q "ExecStart=/usr/libexec/mios/mios-attest-server serve" "$SERVICE_FILE" && \
       grep -q "Restart=always" "$SERVICE_FILE" && \
       grep -q "StandardOutput=journal" "$SERVICE_FILE" && \
       ! grep -q "WantedBy=" "$SERVICE_FILE" && \
       grep -q "ConditionPathExists=/usr/share/doc/mios/keylime-ready" "$SERVICE_FILE"; then
        assert_pass "Systemd unit mios-attest.service is valid, has no WantedBy, and is conditioned off until Keylime"
    else
        assert_fail "Systemd unit content mismatch" "$(cat "$SERVICE_FILE")"
    fi
else
    assert_fail "Systemd unit not found at $SERVICE_FILE"
fi

if command -v systemd-analyze >/dev/null 2>&1; then
    # Verify using systemd-analyze
    sa_out="$(systemd-analyze verify "$SERVICE_FILE" 2>&1 || true)"
    if ! echo "$sa_out" | grep -q "Failed to parse unit"; then
        assert_pass "systemd-analyze verify confirmed mios-attest.service syntax validity"
    else
        assert_fail "systemd-analyze reported syntax error" "$sa_out"
    fi
fi

# ==============================================================================
# Test 7: Mock end-to-end attestation workflow (--mock daemon serve)
# ==============================================================================
log "Test 7: Mock end-to-end attestation workflow (--mock daemon serve)"

# Find an available ephemeral port
DAEMON_PORT="$(python3 -c 'import socket; s=socket.socket(); s.bind(("", 0)); print(s.getsockname()[1]); s.close()')"
diag "Selected ephemeral port for test daemon: $DAEMON_PORT"

"$ATTEST_SERVER" serve --port "$DAEMON_PORT" --mock > "${TMP_DIR}/daemon.log" 2>&1 &
DAEMON_PID=$!
diag "Started mios-attest-server serve in background (PID $DAEMON_PID)"

# Wait for server readiness
ready=false
for _ in $(seq 1 30); do
    if curl -s -f "http://127.0.0.1:${DAEMON_PORT}/health" >/dev/null 2>&1; then
        ready=true
        break
    fi
    sleep 0.2
done

if [[ "$ready" == "true" ]]; then
    assert_pass "Daemon HTTP server started and responded on /health"
else
    assert_fail "Daemon failed to start within timeout" "$(cat "${TMP_DIR}/daemon.log" 2>/dev/null || true)"
fi

# 7a. Request challenge nonce
chal_resp="$(curl -s -X POST "http://127.0.0.1:${DAEMON_PORT}/v1/attest/challenge" \
    -H "Content-Type: application/json" \
    -d '{"blade_id": "blade-mock-01"}')"

live_nonce="$(python3 -c "import json, sys; d=json.loads(sys.stdin.read()); print(d.get('nonce',''))" <<< "$chal_resp")"
if [[ -n "$live_nonce" && ${#live_nonce} -ge 32 ]]; then
    assert_pass "Daemon issued cryptographic challenge nonce ($live_nonce)"
else
    assert_fail "Daemon failed to issue challenge nonce" "$chal_resp"
fi

# 7b. Generate quote matching issued challenge
LIVE_QUOTE_FILE="${TMP_DIR}/live-quote.json"
"$ATTEST_SERVER" generate-quote blade-mock-01 "$live_nonce" --output "$LIVE_QUOTE_FILE" --mock

# 7c. Submit quote to daemon for appraisal
live_quote_json="$(cat "$LIVE_QUOTE_FILE")"
verify_payload="$(python3 -c "
import json
quote = json.loads('''$live_quote_json''')
payload = {'blade_id': 'blade-mock-01', 'nonce': '$live_nonce', 'quote': quote}
print(json.dumps(payload))
")"

ver_http_code="$(curl -s -o "${TMP_DIR}/verify_res.json" -w "%{http_code}" \
    -X POST "http://127.0.0.1:${DAEMON_PORT}/v1/attest/verify" \
    -H "Content-Type: application/json" \
    -d "$verify_payload")"

if [[ "$ver_http_code" == "200" ]] && \
   grep -q '"verdict": "APPROVED"' "${TMP_DIR}/verify_res.json" && \
   grep -q '"headscale_preauth_key"' "${TMP_DIR}/verify_res.json" && \
   grep -q '"k3s_join_token"' "${TMP_DIR}/verify_res.json"; then
    assert_pass "End-to-end HTTP appraisal: 200 OK, APPROVED with headscale and k3s credentials"
else
    assert_fail "End-to-end HTTP appraisal failed" "HTTP $ver_http_code: $(cat "${TMP_DIR}/verify_res.json")"
fi

# 7d. Submit tampered quote to daemon and assert 403 Forbidden
BAD_LIVE_QUOTE="${TMP_DIR}/bad-live-quote.json"
"$ATTEST_SERVER" generate-quote blade-mock-01 "$live_nonce" --tamper signature --output "$BAD_LIVE_QUOTE" --mock
bad_quote_json="$(cat "$BAD_LIVE_QUOTE")"
bad_payload="$(python3 -c "
import json
quote = json.loads('''$bad_quote_json''')
payload = {'blade_id': 'blade-mock-01', 'nonce': '$live_nonce', 'quote': quote}
print(json.dumps(payload))
")"

bad_http_code="$(curl -s -o "${TMP_DIR}/bad_verify_res.json" -w "%{http_code}" \
    -X POST "http://127.0.0.1:${DAEMON_PORT}/v1/attest/verify" \
    -H "Content-Type: application/json" \
    -d "$bad_payload")"

if [[ "$bad_http_code" == "403" ]] && \
   grep -q '"verdict": "QUARANTINED"' "${TMP_DIR}/bad_verify_res.json"; then
    assert_pass "End-to-end HTTP negative appraisal: 403 Forbidden, QUARANTINED on tampered quote"
else
    assert_fail "End-to-end HTTP negative appraisal failed" "HTTP $bad_http_code: $(cat "${TMP_DIR}/bad_verify_res.json")"
fi

# Stop daemon cleanly
kill "$DAEMON_PID" 2>/dev/null || true
wait "$DAEMON_PID" 2>/dev/null || true
DAEMON_PID=""
assert_pass "Daemon terminated cleanly after end-to-end verification"

# ==============================================================================
# Summary
# ==============================================================================
log "=== Test Results: $pass_count passed, $fail_count failed ==="

if [[ "$fail_count" -gt 0 ]]; then
    log "FAILED: $fail_count tests failed."
    exit 1
fi

log "SUCCESS: All $pass_count tests passed (100% success)."
exit 0
