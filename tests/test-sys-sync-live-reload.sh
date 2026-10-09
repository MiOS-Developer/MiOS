#!/usr/bin/env bash
# AI-hint: Automated zero-reboot sysctl parameter application (<50ms) and live udev test suite (task_767).
# AI-doc: usr/share/doc/mios/manual/kernel.md
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SYNC_BIN="$REPO_ROOT/usr/libexec/mios/mios-sys-sync"

passed=0
failed=0

_pass() {
    echo "  [PASS] $1"
    passed=$((passed + 1))
}

_fail() {
    echo "  [FAIL] $1" >&2
    failed=$((failed + 1))
}

echo "[test-sys-sync] === MiOS Kernel Sysctl & Udev Live Synchronization Test Suite ==="

# 1. Binary existence and permissions
echo "[test-sys-sync] Test 1: Verifying mios-sys-sync binary"
if [[ -x "$SYNC_BIN" ]]; then
    _pass "Found executable $SYNC_BIN"
else
    _fail "$SYNC_BIN is missing or not executable"
fi

# 2. Dry-run and syntax validation over repository sysctl definitions
echo "[test-sys-sync] Test 2: Dry-run and parsing validation across repo sysctl configurations"
DRY_JSON=$("$SYNC_BIN" --dry-run --json)
STATUS=$(python3 -c "import json, sys; d = json.loads('''$DRY_JSON'''); print(d['status'])")
COUNT=$(python3 -c "import json, sys; d = json.loads('''$DRY_JSON'''); print(d['sysctl_parameters_applied'])")

if [[ "$STATUS" == "success" && "$COUNT" -ge 10 ]]; then
    _pass "Dry-run parsed $COUNT sysctl parameters with status '$STATUS'"
else
    _fail "Dry-run parsing failed (status=$STATUS, count=$COUNT)"
fi

# 3. Latency benchmark across 20 iterations (SLA < 50ms)
echo "[test-sys-sync] Test 3: Kernel parameter reload latency SLA benchmark (< 50ms)"
BENCH_JSON=$("$SYNC_BIN" --benchmark --dry-run --json)
AVG_LAT=$(python3 -c "import json, sys; d = json.loads('''$BENCH_JSON'''); print(d['avg_latency_ms'])")
SLA_MET=$(python3 -c "import json, sys; d = json.loads('''$BENCH_JSON'''); print(d['meets_latency_sla'])")

if [[ "$SLA_MET" == "True" ]] && python3 -c "import sys; sys.exit(0 if float('$AVG_LAT') < 50.0 else 1)"; then
    _pass "Average sysctl synchronization latency: ${AVG_LAT}ms (SLA target: < 50.0ms)"
else
    _fail "Latency benchmark exceeded 50ms SLA: ${AVG_LAT}ms"
fi

# Setup isolated mock root for file application and live udev testing
TEST_ROOT=$(mktemp -d /tmp/mios-sys-sync-test.XXXXXX)
trap 'rm -rf "$TEST_ROOT"' EXIT

mkdir -p "$TEST_ROOT/etc/sysctl.d"
mkdir -p "$TEST_ROOT/etc/udev/rules.d"
mkdir -p "$TEST_ROOT/proc/sys/fs"
mkdir -p "$TEST_ROOT/dev"

# 4. Atomic zero-reboot sysctl parameter application (fs.file-max)
echo "[test-sys-sync] Test 4: Dynamic sysctl parameter application (fs.file-max = 2097152)"
cat > "$TEST_ROOT/etc/sysctl.d/50-filemax.conf" <<'EOF'
# Test filemax tunable
fs.file-max = 2097152
EOF

APPLY_JSON=$("$SYNC_BIN" --mock-root "$TEST_ROOT" --sysctl --json)
APPLIED_VAL=$(cat "$TEST_ROOT/proc/sys/fs/file-max" 2>/dev/null || echo "")
SYSCTL_LAT=$(python3 -c "import json, sys; d = json.loads('''$APPLY_JSON'''); print(d['sysctl_latency_ms'])")

if [[ "$APPLIED_VAL" == "2097152" ]] && python3 -c "import sys; sys.exit(0 if float('$SYSCTL_LAT') < 50.0 else 1)"; then
    _pass "Applied fs.file-max=2097152 in ${SYSCTL_LAT}ms (Verified value: $APPLIED_VAL)"
else
    _fail "Failed to apply fs.file-max (val='$APPLIED_VAL', latency=${SYSCTL_LAT}ms)"
fi

# 5. Multi-parameter atomic configuration
echo "[test-sys-sync] Test 5: Multi-parameter atomic synchronization"
cat > "$TEST_ROOT/etc/sysctl.d/60-network-memory.conf" <<'EOF'
vm.max_map_count = 1048576
net.ipv4.ip_forward = 1
kernel.pid_max = 4194304
EOF

"$SYNC_BIN" --mock-root "$TEST_ROOT" --sysctl --json >/dev/null
VM_VAL=$(cat "$TEST_ROOT/proc/sys/vm/max_map_count" 2>/dev/null || echo "")
NET_VAL=$(cat "$TEST_ROOT/proc/sys/net/ipv4/ip_forward" 2>/dev/null || echo "")
PID_VAL=$(cat "$TEST_ROOT/proc/sys/kernel/pid_max" 2>/dev/null || echo "")

if [[ "$VM_VAL" == "1048576" && "$NET_VAL" == "1" && "$PID_VAL" == "4194304" ]]; then
    _pass "Atomic multi-parameter sync verified (vm.max_map_count=$VM_VAL, ip_forward=$NET_VAL, pid_max=$PID_VAL)"
else
    _fail "Multi-parameter sync mismatch (vm=$VM_VAL, net=$NET_VAL, pid=$PID_VAL)"
fi

# 6. Live udev rule reload and device symlink creation
echo "[test-sys-sync] Test 6: Live udev rule reload (< 100ms) and symlink verification"
cat > "$TEST_ROOT/etc/udev/rules.d/70-mios-nvme.rules" <<'EOF'
# MiOS NVMe high-performance storage rule
KERNEL=="nvme0n1", SYMLINK+="disk/by-id/mios-fast-storage"
EOF

UDEV_JSON=$("$SYNC_BIN" --mock-root "$TEST_ROOT" --udev --json)
UDEV_OK=$(python3 -c "import json, sys; d = json.loads('''$UDEV_JSON'''); print(d['udev_reload_ok'])")
UDEV_LAT=$(python3 -c "import json, sys; d = json.loads('''$UDEV_JSON'''); print(d['udev_latency_ms'])")

if [[ "$UDEV_OK" == "True" && -e "$TEST_ROOT/dev/disk/by-id/mios-fast-storage" ]] && python3 -c "import sys; sys.exit(0 if float('$UDEV_LAT') < 100.0 else 1)"; then
    _pass "Udev rule reloaded in ${UDEV_LAT}ms and created /dev/disk/by-id/mios-fast-storage"
else
    _fail "Udev rule reload failed (ok=$UDEV_OK, lat=${UDEV_LAT}ms, file_exists=$(test -e "$TEST_ROOT/dev/disk/by-id/mios-fast-storage" && echo 'yes' || echo 'no'))"
fi

# 7. Audit and system events journal logging
echo "[test-sys-sync] Test 7: System event logging verification"
LOG_FILE="$TEST_ROOT/var/log/mios/system_events.log"
if [[ -f "$LOG_FILE" ]] && grep -q '"event": "sys_sync"' "$LOG_FILE"; then
    _pass "System synchronization event recorded in $LOG_FILE"
else
    _fail "System synchronization event missing in $LOG_FILE"
fi

# 8. Negative Control: Malformed comments and invalid lines gracefully handled
echo "[test-sys-sync] Test 8: Negative control: malformed syntax resilience"
cat > "$TEST_ROOT/etc/sysctl.d/99-malformed.conf" <<'EOF'
# Valid comment
; Semicolon comment
this line is missing equal sign
= missing key
key_without_val =
valid.parameter = 12345
EOF

"$SYNC_BIN" --mock-root "$TEST_ROOT" --sysctl --json >/dev/null
NEG_VAL=$(cat "$TEST_ROOT/proc/sys/valid/parameter" 2>/dev/null || echo "")

if [[ "$NEG_VAL" == "12345" ]]; then
    _pass "Engine gracefully ignored malformed lines and applied valid parameter 'valid.parameter=12345'"
else
    _fail "Negative control failed: malformed file broke parameter application (val='$NEG_VAL')"
fi

echo "[test-sys-sync] === Test Results: $passed passed, $failed failed ==="

if [[ "$failed" -eq 0 ]]; then
    echo "[test-sys-sync] SUCCESS: All $passed tests passed (100% success)."
    exit 0
else
    echo "[test-sys-sync] FAILURE: $failed test(s) failed." >&2
    exit 1
fi
