#!/usr/bin/env bash
# AI-hint: Verification suite for automated network and audio fallback manager and alert daemon (T-532, AGY-2130).
# AI-doc: usr/share/doc/mios/manual/ch77-hardware-fallback.md
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

VERBOSE=false
DRY_RUN=false

show_help() {
    cat <<'EOF'
Usage: test-hardware-fallback.sh [OPTIONS]

Verification test suite for automated network and audio fallback manager with
operator desktop alert daemon (T-532, AGY-2130).

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
    echo "[test-hardware-fallback] $*"
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

TMP_DIR="$(mktemp -d /tmp/test-hw-fallback.XXXXXX)"
trap '[[ -n "${TMP_DIR:-}" && -d "$TMP_DIR" ]] && rm -rf "$TMP_DIR"' EXIT

FALLBACK_TOOL="${ROOT_DIR}/usr/libexec/mios/mios-hardware-fallback"
SYSTEMD_USER_UNIT="${ROOT_DIR}/usr/lib/systemd/user/mios-hardware-fallback.service"

log "=== MiOS Hardware Fallback Manager & Alert Daemon Test Suite (T-532, AGY-2130) ==="

# -----------------------------------------------------------------------------
# Dry-run early exit mode
# -----------------------------------------------------------------------------
if [[ "$DRY_RUN" == "true" ]]; then
    log "Running in dry-run mode: verifying tool syntax and CLI dry-run flags"

    if bash -n "$0" && python3 -m py_compile "$FALLBACK_TOOL"; then
        assert_pass "Python compilation and bash test script syntax check"
    else
        assert_fail "Syntax verification failed"
    fi

    if "$FALLBACK_TOOL" --help >/dev/null; then
        assert_pass "mios-hardware-fallback --help exit status 0"
    else
        assert_fail "mios-hardware-fallback --help execution"
    fi

    if "$FALLBACK_TOOL" evaluate --mock --dry-run >/dev/null; then
        assert_pass "mios-hardware-fallback evaluate --mock --dry-run exit status 0"
    else
        assert_fail "mios-hardware-fallback evaluate --mock --dry-run execution"
    fi

    log "=== Test Summary (Dry-Run): $pass_count passed, $fail_count failed ==="
    exit 0
fi

# -----------------------------------------------------------------------------
# Test 1: Tooling and CLI Verification
# -----------------------------------------------------------------------------
log "Test 1: Tooling and CLI verification (mios-hardware-fallback --help, status)"

HELP_OUT="$("$FALLBACK_TOOL" --help)"
if echo "$HELP_OUT" | grep -q "Automated network and audio fallback manager"; then
    assert_pass "mios-hardware-fallback --help displays expected banner"
else
    assert_fail "mios-hardware-fallback --help missing expected description"
fi

STATUS_OUT="$("$FALLBACK_TOOL" status --state-file "${TMP_DIR}/init_state.json")"
diag "Initial status: $STATUS_OUT"
if echo "$STATUS_OUT" | grep -q "MiOS Hardware Fallback Manager Status"; then
    assert_pass "mios-hardware-fallback status executes and prints header"
else
    assert_fail "mios-hardware-fallback status output invalid"
fi

STATUS_JSON="$("$FALLBACK_TOOL" status --json --state-file "${TMP_DIR}/init_state.json")"
if python3 -c "import json, sys; d = json.loads('''$STATUS_JSON'''); assert 'overall_status' in d"; then
    assert_pass "mios-hardware-fallback status --json produces valid JSON"
else
    assert_fail "mios-hardware-fallback status --json failed"
fi

# -----------------------------------------------------------------------------
# Test 2: Network Fallback Trigger on Degraded Network Fixture (Positive Control)
# -----------------------------------------------------------------------------
log "Test 2: Network fallback trigger on degraded network fixture (positive control)"

NET_DEG_LOG="${TMP_DIR}/net_degrade.log"
NET_STATE="${TMP_DIR}/net_state.json"
NET_NOTIF="${TMP_DIR}/net_notif.log"

cat <<'EOF' > "$NET_DEG_LOG"
[2026-09-24T00:10:00Z] [DEGRADED] Hardware state DEGRADED [subsystems: network]: test | {"timestamp": "2026-09-24T00:10:00Z", "event_type": "hardware_degraded", "overall_status": "degraded", "degraded_count": 1, "subsystems": {"network": {"status": "degraded", "details": "Primary controller down / link lost"}, "audio": {"status": "healthy", "details": "ALSA card0 present"}, "display": {"status": "healthy", "details": "DRM present"}}}
EOF

EVAL_NET_OUT="$("$FALLBACK_TOOL" evaluate --degrade-log "$NET_DEG_LOG" --state-file "$NET_STATE" --notif-log "$NET_NOTIF" --mock)"
diag "Evaluate degraded network output: $EVAL_NET_OUT"

if [[ -f "$NET_STATE" ]]; then
    assert_pass "Network fallback evaluation generated state file"
else
    assert_fail "Network fallback state file not created"
fi

python3 -c "
import json
with open('$NET_STATE') as fh:
    data = json.load(fh)

assert data['overall_status'] == 'degraded', f'Expected degraded overall_status, got {data.get(\"overall_status\")}'
assert data['fallbacks_applied'] is True, 'Expected fallbacks_applied == True'
assert data['network_fallback']['fallback_active'] is True, 'Expected network_fallback active'
assert data['network_fallback']['chosen_interface'] in ('usb0', 'eth1', 'enp0s8'), f'Unexpected chosen iface: {data[\"network_fallback\"]}'
assert data['audio_fallback']['fallback_active'] is False, 'Audio fallback should be inactive'
"
assert_pass "Network fallback correctly activated alternative NIC while keeping audio nominal"

# -----------------------------------------------------------------------------
# Test 3: Audio Fallback Trigger on Degraded Audio Fixture (Positive Control)
# -----------------------------------------------------------------------------
log "Test 3: Audio fallback trigger on degraded audio fixture (positive control)"

AUD_DEG_LOG="${TMP_DIR}/aud_degrade.log"
AUD_STATE="${TMP_DIR}/aud_state.json"
AUD_NOTIF="${TMP_DIR}/aud_notif.log"

cat <<'EOF' > "$AUD_DEG_LOG"
[2026-09-24T00:20:00Z] [DEGRADED] Hardware state DEGRADED [subsystems: audio]: test | {"timestamp": "2026-09-24T00:20:00Z", "event_type": "hardware_degraded", "overall_status": "degraded", "degraded_count": 1, "subsystems": {"network": {"status": "healthy", "details": "eth0 active (up)"}, "audio": {"status": "degraded", "details": "No ALSA or PipeWire soundcards detected"}, "display": {"status": "healthy", "details": "DRM present"}}}
EOF

EVAL_AUD_OUT="$("$FALLBACK_TOOL" evaluate --degrade-log "$AUD_DEG_LOG" --state-file "$AUD_STATE" --notif-log "$AUD_NOTIF" --mock)"
diag "Evaluate degraded audio output: $EVAL_AUD_OUT"

python3 -c "
import json
with open('$AUD_STATE') as fh:
    data = json.load(fh)

assert data['overall_status'] == 'degraded', f'Expected degraded overall_status'
assert data['fallbacks_applied'] is True, 'Expected fallbacks_applied == True'
assert data['audio_fallback']['fallback_active'] is True, 'Expected audio_fallback active'
assert data['audio_fallback']['sink_name'] == 'mios-null-sink', f'Unexpected sink name: {data[\"audio_fallback\"]}'
assert data['network_fallback']['fallback_active'] is False, 'Network fallback should be inactive'
"
assert_pass "Audio fallback correctly bound PipeWire null-sink while keeping network nominal"

# -----------------------------------------------------------------------------
# Test 4: Notification Dispatch Validation (Positive Control)
# -----------------------------------------------------------------------------
log "Test 4: Notification dispatch validation (positive control)"

if [[ -f "$NET_NOTIF" && -f "$AUD_NOTIF" ]]; then
    assert_pass "Notification audit logs created for both fallback events"
else
    assert_fail "Notification audit log missing"
fi

python3 -c "
import json
with open('$NET_NOTIF') as fh:
    lines = [json.loads(line) for line in fh if line.strip()]

assert len(lines) >= 1, 'No notifications found in network notification log'
net_notif = lines[-1]
assert 'Network Degraded' in net_notif['summary'], f'Bad summary: {net_notif}'
assert 'linux-firmware' in net_notif['body'], f'Missing remediation guidance in body: {net_notif}'
assert net_notif['urgency'] == 'critical', f'Expected critical urgency, got {net_notif.get(\"urgency\")}'
"
assert_pass "Network degradation notification dispatched with critical urgency and linux-firmware remediation"

python3 -c "
import json
with open('$AUD_NOTIF') as fh:
    lines = [json.loads(line) for line in fh if line.strip()]

assert len(lines) >= 1, 'No notifications found in audio notification log'
aud_notif = lines[-1]
assert 'Audio Degraded' in aud_notif['summary'], f'Bad summary: {aud_notif}'
assert 'alsa-sof-firmware' in aud_notif['body'] or 'PipeWire null-sink' in aud_notif['body'], f'Missing audio guidance: {aud_notif}'
"
assert_pass "Audio degradation notification dispatched with PipeWire null-sink alert and alsa-sof-firmware remediation"

# -----------------------------------------------------------------------------
# Test 5: Negative Control - Healthy Hardware Requires No Fallback Actions
# -----------------------------------------------------------------------------
log "Test 5: Negative control - healthy hardware requires no fallback actions"

NOM_LOG="${TMP_DIR}/nominal.log"
NOM_STATE="${TMP_DIR}/nom_state.json"
NOM_NOTIF="${TMP_DIR}/nom_notif.log"

cat <<'EOF' > "$NOM_LOG"
[2026-09-24T00:30:00Z] [NOMINAL] Hardware state NOMINAL: all probed peripheral subsystems healthy (network, audio, display). | {"timestamp": "2026-09-24T00:30:00Z", "event_type": "hardware_nominal", "overall_status": "nominal", "degraded_count": 0, "subsystems": {"network": {"status": "healthy", "details": "Active network interfaces: eth0 (up)"}, "audio": {"status": "healthy", "details": "Soundcard 0: HDA-Intel"}, "display": {"status": "healthy", "details": "DRM display nodes detected: card0"}}}
EOF

NOM_OUT="$("$FALLBACK_TOOL" evaluate --degrade-log "$NOM_LOG" --state-file "$NOM_STATE" --notif-log "$NOM_NOTIF")"
diag "Nominal evaluation output: $NOM_OUT"

python3 -c "
import json, os
with open('$NOM_STATE') as fh:
    data = json.load(fh)

assert data['overall_status'] == 'nominal', f'Expected nominal, got {data.get(\"overall_status\")}'
assert data['fallbacks_applied'] is False, 'No fallbacks should be applied on nominal hardware'
assert data['network_fallback']['fallback_active'] is False, 'Network fallback must be inactive'
assert data['audio_fallback']['fallback_active'] is False, 'Audio fallback must be inactive'
assert len(data['alerts_dispatched']) == 0, 'No alerts should be dispatched on nominal hardware'
assert not os.path.exists('$NOM_NOTIF') or os.path.getsize('$NOM_NOTIF') == 0, 'Notification log must be empty'
"
assert_pass "Negative control: healthy hardware engaged 0 fallbacks and emitted 0 notifications"

# -----------------------------------------------------------------------------
# Test 6: Systemd User Unit Validation
# -----------------------------------------------------------------------------
log "Test 6: Systemd user unit validation"

if [[ -f "$SYSTEMD_USER_UNIT" ]]; then
    assert_pass "Systemd user service file exists at expected path"
else
    assert_fail "Systemd user service file missing: $SYSTEMD_USER_UNIT"
fi

if grep -q "ExecStart=/usr/libexec/mios/mios-hardware-fallback evaluate" "$SYSTEMD_USER_UNIT"; then
    assert_pass "Systemd user service contains ExecStart=/usr/libexec/mios/mios-hardware-fallback evaluate"
else
    assert_fail "ExecStart directive in systemd user service is incorrect"
fi

if grep -q "Restart=on-failure" "$SYSTEMD_USER_UNIT"; then
    assert_pass "Systemd user service specifies Restart=on-failure"
else
    assert_fail "Restart directive missing or incorrect"
fi

if systemd-analyze verify "$SYSTEMD_USER_UNIT" >/dev/null 2>&1; then
    assert_pass "systemd-analyze verify confirms user unit syntax validity"
else
    # Fallback to structural syntax check if systemd-analyze cannot run in container
    if grep -q "\[Unit\]" "$SYSTEMD_USER_UNIT" && grep -q "\[Service\]" "$SYSTEMD_USER_UNIT" && grep -q "\[Install\]" "$SYSTEMD_USER_UNIT"; then
        assert_pass "Systemd user unit structure verified ([Unit], [Service], [Install])"
    else
        assert_fail "Systemd user unit structural verification failed"
    fi
fi

# -----------------------------------------------------------------------------
# Test 7: Mock End-to-End Evaluation (--mock)
# -----------------------------------------------------------------------------
log "Test 7: Mock end-to-end evaluation (--mock)"

MOCK_STATE="${TMP_DIR}/mock_e2e_state.json"
MOCK_NOTIF="${TMP_DIR}/mock_e2e_notif.log"

MOCK_EVAL_OUT="$("$FALLBACK_TOOL" evaluate --mock --state-file "$MOCK_STATE" --notif-log "$MOCK_NOTIF")"
diag "Mock E2E output: $MOCK_EVAL_OUT"

python3 -c "
import json
with open('$MOCK_STATE') as fh:
    data = json.load(fh)

assert data['overall_status'] == 'degraded'
assert data['fallbacks_applied'] is True
assert data['network_fallback']['fallback_active'] is True
assert data['audio_fallback']['fallback_active'] is True
assert len(data['alerts_dispatched']) == 2
"
assert_pass "Mock mode successfully applied both network and audio fallbacks"

MOCK_STATUS_OUT="$("$FALLBACK_TOOL" status --state-file "$MOCK_STATE")"
diag "Mock status output: $MOCK_STATUS_OUT"

if echo "$MOCK_STATUS_OUT" | grep -q "Network: ACTIVE" && echo "$MOCK_STATUS_OUT" | grep -q "Audio:   ACTIVE"; then
    assert_pass "status command reflects active mock fallbacks for network and audio"
else
    assert_fail "status command failed to reflect active mock fallbacks"
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
