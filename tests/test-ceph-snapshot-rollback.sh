#!/usr/bin/env bash
# AI-hint: Automated CephFS snapshot creation (<10ms), retention rotation, and rollback test suite (task_739).
# AI-doc: usr/share/doc/mios/manual/ch66-v5-authority-inversion-and-cephfs-tiering.md
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SNAP_BIN="$REPO_ROOT/usr/libexec/mios/mios-ceph-snap"
SERVICE_UNIT="$REPO_ROOT/usr/lib/systemd/system/mios-ceph-snap.service"
TIMER_UNIT="$REPO_ROOT/usr/lib/systemd/system/mios-ceph-snap.timer"

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

echo "[test-ceph-snap] === MiOS CephFS Atomic Snapshot & Instant Rollback Test Suite ==="

# 1. Binary and systemd unit verification
echo "[test-ceph-snap] Test 1: Verifying binary and systemd units"
if [[ -x "$SNAP_BIN" ]]; then
    _pass "Found executable $SNAP_BIN"
else
    _fail "$SNAP_BIN is missing or not executable"
fi

if [[ -f "$SERVICE_UNIT" && -f "$TIMER_UNIT" ]]; then
    _pass "Found systemd service and timer units"
else
    _fail "Missing systemd unit files ($SERVICE_UNIT, $TIMER_UNIT)"
fi

# Setup isolated test workspace
WS_DIR=$(mktemp -d /tmp/mios-ceph-ws.XXXXXX)
trap 'rm -rf "$WS_DIR"' EXIT

# 2. Populate test workspace with sample files and compute baseline checksums
echo "[test-ceph-snap] Test 2: Populating test workspace with sample files"
mkdir -p "$WS_DIR/src" "$WS_DIR/models" "$WS_DIR/config"
for i in $(seq 1 15); do
    echo "content-data-chunk-$i" > "$WS_DIR/src/file_$i.txt"
    echo "model-weights-tensor-$i" > "$WS_DIR/models/tensor_$i.bin"
done
echo "initial-config-version-1" > "$WS_DIR/config/settings.json"

BASELINE_HASH=$(find "$WS_DIR" -type f ! -path '*/.*' -exec sha256sum {} + | sort | sha256sum | awk '{print $1}')
FILE_COUNT=$(find "$WS_DIR" -type f ! -path '*/.*' | wc -l)
_pass "Populated workspace with $FILE_COUNT files (Baseline tree SHA-256: ${BASELINE_HASH:0:16}...)"

# 3. Take atomic snapshot and verify <10ms SLA
echo "[test-ceph-snap] Test 3: Atomic snapshot creation (<10ms SLA)"
SNAP_OUT=$("$SNAP_BIN" create "$WS_DIR" --name "v1_clean" --json)
SNAP_LAT=$(python3 -c "import json, sys; d = json.loads('''$SNAP_OUT'''); print(d['creation_latency_ms'])")
SLA_OK=$(python3 -c "import json, sys; d = json.loads('''$SNAP_OUT'''); print(d['meets_latency_sla'])")

if [[ "$SLA_OK" == "True" ]] && python3 -c "import sys; sys.exit(0 if float('$SNAP_LAT') < 10.0 else 1)"; then
    _pass "Created atomic snapshot 'v1_clean' in ${SNAP_LAT}ms (SLA target: < 10.0ms)"
else
    _fail "Snapshot creation took ${SNAP_LAT}ms (exceeded 10ms SLA)"
fi

# 4. Corrupt workspace state
echo "[test-ceph-snap] Test 4: Mutating and corrupting workspace files"
# Corrupt existing files
echo "CORRUPTED DATA" > "$WS_DIR/src/file_1.txt"
echo "CORRUPTED DATA" > "$WS_DIR/models/tensor_10.bin"
# Delete a critical file
rm -f "$WS_DIR/config/settings.json"
# Add extraneous files
echo "unwanted malicious artifact" > "$WS_DIR/src/malicious_payload.sh"

CORRUPT_HASH=$(find "$WS_DIR" -type f ! -path '*/.*' -exec sha256sum {} + | sort | sha256sum | awk '{print $1}')
if [[ "$CORRUPT_HASH" != "$BASELINE_HASH" ]]; then
    _pass "Workspace state corrupted successfully for test (Corrupted tree SHA-256: ${CORRUPT_HASH:0:16}...)"
else
    _fail "Workspace state corruption failed"
fi

# 5. Instant rollback to snapshot
echo "[test-ceph-snap] Test 5: Instant rollback (<10ms SLA)"
ROLLBACK_OUT=$("$SNAP_BIN" rollback "$WS_DIR" --name "v1_clean" --json)
ROLLBACK_LAT=$(python3 -c "import json, sys; d = json.loads('''$ROLLBACK_OUT'''); print(d['rollback_latency_ms'])")
ROLLBACK_SLA=$(python3 -c "import json, sys; d = json.loads('''$ROLLBACK_OUT'''); print(d['meets_latency_sla'])")

if [[ "$ROLLBACK_SLA" == "True" ]] && python3 -c "import sys; sys.exit(0 if float('$ROLLBACK_LAT') < 10.0 else 1)"; then
    _pass "Instant rollback completed in ${ROLLBACK_LAT}ms (SLA target: < 10.0ms)"
else
    _fail "Rollback latency took ${ROLLBACK_LAT}ms (exceeded 10ms SLA)"
fi

# 6. Verify 100% SHA-256 parity and removal of extraneous files
echo "[test-ceph-snap] Test 6: Verifying 100% SHA-256 data integrity and file tree parity"
RESTORED_HASH=$(find "$WS_DIR" -type f ! -path '*/.*' -exec sha256sum {} + | sort | sha256sum | awk '{print $1}')

if [[ "$RESTORED_HASH" == "$BASELINE_HASH" && ! -e "$WS_DIR/src/malicious_payload.sh" && -f "$WS_DIR/config/settings.json" ]]; then
    _pass "Restored workspace matches 100% baseline SHA-256 ($RESTORED_HASH) and removed extraneous files"
else
    _fail "Restored workspace hash mismatch ($RESTORED_HASH != $BASELINE_HASH)"
fi

# 7. Retention rotation test
echo "[test-ceph-snap] Test 7: Snapshot retention rotation (hourly=12)"
# Generate 25 mock snapshots
for s in $(seq 1 25); do
    mkdir -p "$WS_DIR/.snap/mock_snap_$s"
done

PRUNE_OUT=$("$SNAP_BIN" prune "$WS_DIR" --hourly 12 --daily 7 --weekly 4 --json)
PRUNED_COUNT=$(python3 -c "import json, sys; d = json.loads('''$PRUNE_OUT'''); print(d['pruned'])")
REMAINING_COUNT=$(python3 -c "import json, sys; d = json.loads('''$PRUNE_OUT'''); print(d['remaining'])")

if [[ "$PRUNED_COUNT" -gt 0 && "$REMAINING_COUNT" -eq 12 ]]; then
    _pass "Retention pruner pruned $PRUNED_COUNT excess snapshots, preserving exact limit of $REMAINING_COUNT"
else
    _fail "Retention pruning failed (pruned=$PRUNED_COUNT, remaining=$REMAINING_COUNT)"
fi

# 8. Negative Control: Rollback to non-existent snapshot
echo "[test-ceph-snap] Test 8: Negative control: non-existent snapshot rollback rejection"
if "$SNAP_BIN" rollback "$WS_DIR" --name "ghost_snapshot_does_not_exist" 2>/dev/null; then
    _fail "Rollback to non-existent snapshot unexpectedly succeeded"
else
    _pass "Rollback to non-existent snapshot failed cleanly with exit code > 0"
fi

echo "[test-ceph-snap] === Test Results: $passed passed, $failed failed ==="

if [[ "$failed" -eq 0 ]]; then
    echo "[test-ceph-snap] SUCCESS: All $passed tests passed (100% success)."
    exit 0
else
    echo "[test-ceph-snap] FAILURE: $failed test(s) failed." >&2
    exit 1
fi
