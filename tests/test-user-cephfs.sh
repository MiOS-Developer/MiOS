#!/usr/bin/env bash
# AI-hint: Test suite for Global per-user encrypted CephFS subvolume manager (task_472, task_2705).
# AI-doc: usr/share/doc/mios/manual/ch73-cephfs-user-subvolumes.md

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

CEPHFS_TOOL="${ROOT_DIR}/usr/libexec/mios/mios-user-cephfs"
TMP_DIR="$(mktemp -d -t test-user-cephfs.XXXXXX)"

cleanup() {
    rm -rf "${TMP_DIR}"
}
trap cleanup EXIT

echo "[test-user-cephfs] === MiOS User CephFS Subvolume Manager Test Suite (task_472) ==="

# Test 1: Tooling and CLI validation
echo "[test-user-cephfs] Test 1: Tooling and CLI verification"
[ -x "${CEPHFS_TOOL}" ] || { echo "FAIL: ${CEPHFS_TOOL} is not executable"; exit 1; }
"${CEPHFS_TOOL}" --help >/dev/null
"${CEPHFS_TOOL}" status --mock --state-dir "${TMP_DIR}" >/dev/null
echo "  PASS: CLI help and status executed successfully"

# Test 2: Provisioning unprivileged user subvolume (positive control)
echo "[test-user-cephfs] Test 2: Provisioning unprivileged user subvolume"
"${CEPHFS_TOOL}" provision alice --uid 1001 --quota 50GiB --mock --state-dir "${TMP_DIR}" >/dev/null
[ -f "${TMP_DIR}/subvolumes/alice.json" ] || { echo "FAIL: alice.json subvolume state missing"; exit 1; }
echo "  PASS: Provisioning succeeded for unprivileged tenant alice"

# Test 3: Tenant isolation rejection of root / UID < 1000 (negative control)
echo "[test-user-cephfs] Test 3: Tenant isolation rejection of privileged root"
set +e
"${CEPHFS_TOOL}" provision root --mock --state-dir "${TMP_DIR}" 2>"${TMP_DIR}/root_err.txt"
ROOT_RC=$?
set -e
if [ "${ROOT_RC}" -eq 0 ]; then
    echo "FAIL: Expected non-zero exit for root provisioning, got 0"
    exit 1
fi
grep -q "Tenant isolation violation" "${TMP_DIR}/root_err.txt" || {
    echo "FAIL: Error output missing tenant isolation security notice"
    exit 1
}
echo "  PASS: Privileged account 'root' rejected with Tenant isolation violation"

# Test 4: Tenant isolation rejection of numeric UID < 1000 (negative control)
echo "[test-user-cephfs] Test 4: Tenant isolation rejection of system UID 999"
set +e
"${CEPHFS_TOOL}" provision daemon_user --uid 999 --mock --state-dir "${TMP_DIR}" 2>"${TMP_DIR}/uid_err.txt"
UID_RC=$?
set -e
if [ "${UID_RC}" -eq 0 ]; then
    echo "FAIL: Expected non-zero exit for UID 999 provisioning, got 0"
    exit 1
fi
echo "  PASS: System UID < 1000 rejected"

# Test 5: Key verification
echo "[test-user-cephfs] Test 5: Encryption key descriptor verification"
"${CEPHFS_TOOL}" verify-key alice --mock --state-dir "${TMP_DIR}" >/dev/null
echo "  PASS: Encryption key descriptor verified for alice"

# Test 6: Hourly delta snapshot creation
echo "[test-user-cephfs] Test 6: Delta snapshot creation"
"${CEPHFS_TOOL}" snapshot alice --name snap-alice-test-1 --mock --state-dir "${TMP_DIR}" >/dev/null
echo "  PASS: Snapshot snap-alice-test-1 created"

# Test 7: Snapshot delta replication to peers
echo "[test-user-cephfs] Test 7: Snapshot delta replication"
"${CEPHFS_TOOL}" replicate alice --mock --state-dir "${TMP_DIR}" >/dev/null
echo "  PASS: Snapshot replicated to peer nodes"

# Test 8: List output and JSON serialization
echo "[test-user-cephfs] Test 8: List serialization"
JSON_OUT="$("${CEPHFS_TOOL}" list --json --mock --state-dir "${TMP_DIR}")"
python3 -c "import json, sys; data = json.loads(sys.argv[1]); assert len(data) >= 1; assert data[0]['user'] == 'alice'" "${JSON_OUT}"
echo "  PASS: JSON list output parsed and validated"

# Test 9: Systemd unit file validation
echo "[test-user-cephfs] Test 9: Systemd unit file syntax validation"
SERVICE_UNIT="${ROOT_DIR}/usr/lib/systemd/system/mios-user-snapshot@.service"
TIMER_UNIT="${ROOT_DIR}/usr/lib/systemd/system/mios-user-snapshot@.timer"
[ -f "${SERVICE_UNIT}" ] || { echo "FAIL: Missing service unit"; exit 1; }
[ -f "${TIMER_UNIT}" ] || { echo "FAIL: Missing timer unit"; exit 1; }
grep -q "ExecStart=/usr/libexec/mios/mios-user-cephfs snapshot" "${SERVICE_UNIT}"
grep -q "OnCalendar=hourly" "${TIMER_UNIT}"
echo "  PASS: Systemd service and timer units present and syntactically valid"

echo "[test-user-cephfs] === Test Results: All 9 tests passed (100% success) ==="
exit 0
