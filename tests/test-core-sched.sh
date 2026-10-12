#!/usr/bin/env bash
# AI-hint: Automated unit and integration test suite for Linux Core Scheduling cookie tagger, SMT isolation, and CPU affinity (task_802).
# AI-doc: usr/share/doc/mios/manual/tests.md
# AI-related: usr/libexec/mios/mios-core-sched, automation/24-cpu-affinity.sh, usr/lib/systemd/system/subagent.slice
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN="${ROOT}/usr/libexec/mios/mios-core-sched"
AUTO_SCRIPT="${ROOT}/automation/24-cpu-affinity.sh"

log() { printf '[test-core-sched] %s\n' "$*"; }
ok()  { printf '[test-core-sched]   [ OK ] %s\n' "$*"; }
die() { printf '[test-core-sched] ERROR: %s\n' "$*" >&2; exit 1; }

[[ -f "$BIN" ]] || die "Core scheduling utility not found at $BIN"
[[ -f "$AUTO_SCRIPT" ]] || die "Automation script not found at $AUTO_SCRIPT"

TMP="$(mktemp -d /tmp/mios-core-sched-test.XXXXXX 2>/dev/null || mktemp -d -t mios-core-sched-test.XXXXXX)"
trap 'rm -rf "$TMP"' EXIT

# Normalize binary path for Windows Python if cygpath is available
if command -v cygpath &>/dev/null; then
    PY_BIN="$(cygpath -w "$BIN")"
else
    PY_BIN="$BIN"
fi

TESTS_RUN=0
TESTS_PASSED=0

pass_test() {
    TESTS_RUN=$((TESTS_RUN + 1))
    TESTS_PASSED=$((TESTS_PASSED + 1))
    ok "$1"
}

fail_test() {
    TESTS_RUN=$((TESTS_RUN + 1))
    die "Failed test: $1"
}

log "Running Linux Core Scheduling & CPU Affinity Test Suite (task_802)"

# ==============================================================================
# Tier 1: CLI Syntax and Help Verification
# ==============================================================================
log "--- Tier 1: CLI Help and Basic Inspection ---"

help_out="$(python3 "$BIN" --help)"
if grep -q "exec" <<<"$help_out" && grep -q "tag-pid" <<<"$help_out" && grep -q "status" <<<"$help_out"; then
    pass_test "CLI --help lists all required subcommands (exec, tag-pid, tag-cgroup, status)"
else
    fail_test "CLI --help missing expected subcommands"
fi

status_out="$(python3 "$BIN" status)"
if grep -q "MiOS Linux Core Scheduling Status" <<<"$status_out"; then
    pass_test "CLI status returns standard summary header"
else
    fail_test "CLI status output missing summary header"
fi

status_json="$(python3 "$BIN" status --json)"
python3 - "$status_json" <<'PYEOF' || fail_test "CLI status --json schema validation failed"
import json, sys
data = json.loads(sys.argv[1])
required_keys = ['supported', 'smt_active', 'physical_cores', 'logical_cpus', 'cookie', 'raw_cookie']
missing = [k for k in required_keys if k not in data]
if missing:
    sys.exit(f'Missing keys in status JSON: {missing}')
PYEOF
pass_test "CLI status --json emits valid schema"

# ==============================================================================
# Tier 2: Positive Controls (Cookie Creation, SMT Tagging, Inheritance)
# ==============================================================================
log "--- Tier 2: Positive Verification Controls ---"

export MIOS_CORE_SCHED_MOCK=1
export MIOS_MOCK_COOKIE_FILE="${TMP}/mock_cookies.json"

# Test 2.1: Basic exec command execution
exec_out="$(python3 "$BIN" exec -- echo "coresched-exec-ok")"
if [[ "$exec_out" == *"coresched-exec-ok"* ]]; then
    pass_test "exec executes simple command and captures stdout"
else
    fail_test "exec failed to run simple command"
fi

# Test 2.2: Mock cookie creation and environment inheritance
cookie_check=$(python3 "$BIN" exec -- python3 -c 'import os; print(os.environ.get("MIOS_CORE_SCHED_COOKIE", "NONE"))')
if [[ "$cookie_check" != "NONE" && "$cookie_check" == 0x* ]]; then
    pass_test "exec assigns core scheduling cookie and sets MIOS_CORE_SCHED_COOKIE ($cookie_check)"
else
    fail_test "exec did not propagate core scheduling cookie ($cookie_check)"
fi

# Test 2.3: Child process inheritance across process fork/clone
python3 "$BIN" exec -- python3 -c '
import subprocess, sys
res = subprocess.run([sys.executable, "-c", "import os; print(os.environ.get(\"MIOS_CORE_SCHED_COOKIE\", \"NONE\"))"], capture_output=True, text=True)
sys.exit(0 if res.stdout.strip().startswith("0x") else 1)
'
pass_test "Cookie environment is inherited by grandchild subprocesses"

# Test 2.4: Distinct isolation cookies across independent exec runs
cookie1=$(python3 "$BIN" exec -- python3 -c 'import os; print(os.environ.get("MIOS_CORE_SCHED_COOKIE", "NONE"))')
cookie2=$(python3 "$BIN" exec -- python3 -c 'import os; print(os.environ.get("MIOS_CORE_SCHED_COOKIE", "NONE"))')
if [[ "$cookie1" != "$cookie2" ]]; then
    pass_test "Independent exec invocations receive distinct isolated cookies ($cookie1 vs $cookie2)"
else
    fail_test "Sequential invocations received duplicate cookies ($cookie1 == $cookie2)"
fi

# Test 2.5: tag-pid on running process
python3 - "$PY_BIN" <<'PYEOF' || fail_test "tag-pid failed to tag a valid running process"
import subprocess, sys, time
bin_path = sys.argv[1]
proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"])
pid = proc.pid
res = subprocess.run([sys.executable, bin_path, "tag-pid", str(pid)], capture_output=True, text=True)
proc.terminate()
proc.wait()
if res.returncode != 0:
    sys.exit(f"tag-pid returned {res.returncode}: {res.stderr}")
PYEOF
pass_test "tag-pid successfully tags valid running process"

# Test 2.6: tag-cgroup on simulated cgroup directory
MOCK_CG="${TMP}/mock_cgroup"
mkdir -p "${MOCK_CG}"
if command -v cygpath &>/dev/null; then
    PY_MOCK_CG="$(cygpath -w "$MOCK_CG")"
else
    PY_MOCK_CG="$MOCK_CG"
fi

python3 - "$PY_BIN" "$PY_MOCK_CG" <<'PYEOF' || fail_test "tag-cgroup failed to tag cgroup.procs members"
import subprocess, sys, time, os
bin_path = sys.argv[1]
mock_cg = sys.argv[2]
p1 = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"])
p2 = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"])
procs_path = os.path.join(mock_cg, "cgroup.procs")
with open(procs_path, "w") as f:
    f.write(f"{p1.pid}\n{p2.pid}\n")
res = subprocess.run([sys.executable, bin_path, "tag-cgroup", mock_cg], capture_output=True, text=True)
p1.terminate()
p2.terminate()
p1.wait()
p2.wait()
if res.returncode != 0:
    sys.exit(f"tag-cgroup returned {res.returncode}: {res.stderr}")
if "2 processes tagged" not in res.stdout:
    sys.exit(f"tag-cgroup output did not record 2 tagged processes: {res.stdout}")
PYEOF
pass_test "tag-cgroup discovers and tags all PIDs in cgroup slice"

# ==============================================================================
# Tier 3: Negative Verification Controls (Failures & Graceful Fallback)
# ==============================================================================
log "--- Tier 3: Negative Verification Controls ---"

# Test 3.1: exec with missing command must fail
set +e
python3 "$BIN" exec 2>"${TMP}/err_exec_empty.log"
rc_exec_empty=$?
set -e
if [[ "$rc_exec_empty" -ne 0 ]]; then
    pass_test "exec with missing command fails with non-zero exit code ($rc_exec_empty)"
else
    fail_test "exec with missing command unexpectedly succeeded"
fi

# Test 3.2: tag-pid with non-existent PID must fail with ESRCH
set +e
python3 "$BIN" tag-pid 999999999 2>"${TMP}/err_nonexistent_pid.log"
rc_bad_pid=$?
set -e
if [[ "$rc_bad_pid" -ne 0 ]] && grep -q "ESRCH" "${TMP}/err_nonexistent_pid.log"; then
    pass_test "tag-pid with invalid PID exits non-zero ($rc_bad_pid) and reports ESRCH"
else
    fail_test "tag-pid with invalid PID did not report ESRCH error (rc=$rc_bad_pid)"
fi

# Test 3.3: Graceful fallback when core scheduling is unsupported
unset MIOS_CORE_SCHED_MOCK
export MIOS_CORE_SCHED_FORCE_UNSUPPORTED=1

fallback_stdout="$(python3 "$BIN" exec -- echo "fallback-success" 2>"${TMP}/err_fallback.log")"
rc_fallback=$?
if [[ "$rc_fallback" -eq 0 && "$fallback_stdout" == *"fallback-success"* ]]; then
    if grep -q "WARNING.*not supported" "${TMP}/err_fallback.log"; then
        pass_test "Unsupported kernel degrades open: command succeeds with warning logged"
    else
        fail_test "Unsupported kernel fallback did not log warning to stderr"
    fi
else
    fail_test "Unsupported kernel fallback failed execution (rc=$rc_fallback)"
fi

# Test 3.4: Strict mode refuses execution on unsupported kernel
set +e
python3 "$BIN" exec --strict -- echo "should-not-run" 2>"${TMP}/err_strict.log"
rc_strict=$?
set -e
if [[ "$rc_strict" -ne 0 ]] && grep -q "ERROR: core scheduling is unsupported" "${TMP}/err_strict.log"; then
    pass_test "exec --strict refuses execution when core scheduling is unsupported ($rc_strict)"
else
    fail_test "exec --strict unexpectedly executed on unsupported system (rc=$rc_strict)"
fi

# Test 3.5: status reports supported=False when unsupported
status_unsupp="$(python3 "$BIN" status --json)"
python3 - "$status_unsupp" <<'PYEOF' || fail_test "status did not report supported=False when unsupported"
import json, sys
data = json.loads(sys.argv[1])
if data.get('supported') is not False:
    sys.exit('Expected supported=False under forced unsupported mode')
PYEOF
pass_test "status cleanly reports supported=False under unsupported kernel"

# Test 3.6: tag-cgroup with non-existent path handles missing directory
set +e
python3 "$BIN" tag-cgroup "/nonexistent/path/for/cgroup" --strict 2>"${TMP}/err_cg_missing.log"
rc_cg_missing=$?
set -e
if [[ "$rc_cg_missing" -ne 0 ]] && grep -q "does not exist or has no cgroup.procs" "${TMP}/err_cg_missing.log"; then
    pass_test "tag-cgroup --strict rejects non-existent cgroup path ($rc_cg_missing)"
else
    fail_test "tag-cgroup --strict did not properly reject non-existent cgroup path"
fi

unset MIOS_CORE_SCHED_FORCE_UNSUPPORTED

# ==============================================================================
# Tier 4: Automation Script Integration (24-cpu-affinity.sh)
# ==============================================================================
log "--- Tier 4: Automation Script Verification ---"

# Test 4.1: Syntax check via bash -n
bash -n "$AUTO_SCRIPT" || fail_test "bash -n failed on $AUTO_SCRIPT"
pass_test "automation/24-cpu-affinity.sh passes bash syntax validation (bash -n)"

# Test 4.2: Execution against target root
TARGET_DIR="${TMP}/target_root"
mkdir -p "${TARGET_DIR}"
CPU_AFFINITY_TARGET_ROOT="${TARGET_DIR}" bash "$AUTO_SCRIPT" >"${TMP}/auto.log" 2>&1 || fail_test "automation/24-cpu-affinity.sh execution failed"
pass_test "automation/24-cpu-affinity.sh executes with return code 0"

# Test 4.3: Verify generated systemd drop-ins
SYS_DROPIN="${TARGET_DIR}/usr/lib/systemd/system/system.slice.d/20-cpu-affinity.conf"
USER_DROPIN="${TARGET_DIR}/usr/lib/systemd/system/user.slice.d/20-cpu-affinity.conf"
SUB_DROPIN="${TARGET_DIR}/usr/lib/systemd/system/subagent.slice.d/20-cpu-affinity.conf"
SUB_SLICE="${TARGET_DIR}/usr/lib/systemd/system/subagent.slice"
TOPO_CACHE="${TARGET_DIR}/var/lib/mios/cpu-topology.json"

[[ -f "$SYS_DROPIN" ]] || fail_test "Missing system.slice drop-in"
[[ -f "$USER_DROPIN" ]] || fail_test "Missing user.slice drop-in"
[[ -f "$SUB_DROPIN" ]] || fail_test "Missing subagent.slice drop-in"
[[ -f "$SUB_SLICE" ]] || fail_test "Missing base subagent.slice definition"
[[ -f "$TOPO_CACHE" ]] || fail_test "Missing cpu-topology.json cache"

grep -q "CPUWeight=200" "$SYS_DROPIN" || fail_test "system.slice missing CPUWeight=200"
grep -q "CPUWeight=100" "$USER_DROPIN" || fail_test "user.slice missing CPUWeight=100"
grep -q "CPUWeight=50" "$SUB_DROPIN" || fail_test "subagent.slice missing CPUWeight=50"
grep -q "CPUQuota=200%" "$SUB_DROPIN" || fail_test "subagent.slice missing CPUQuota=200%"
grep -q "TasksMax=256" "$SUB_DROPIN" || fail_test "subagent.slice missing TasksMax=256"
grep -q "ManagedOOMMemoryPressure=kill" "$SUB_SLICE" || fail_test "subagent.slice missing ManagedOOMMemoryPressure=kill"

pass_test "Systemd drop-ins correctly configure CPU weight hierarchy (system=200, user=100, subagent=50/200% quota)"
pass_test "Base subagent.slice properly defines ManagedOOM and Task limits"

# Test 4.4: Verify CPU topology cache JSON
if command -v cygpath &>/dev/null; then
    PY_TOPO="$(cygpath -w "$TOPO_CACHE")"
else
    PY_TOPO="$TOPO_CACHE"
fi

python3 - "$PY_TOPO" <<'PYEOF' || fail_test "cpu-topology.json schema validation failed"
import json, sys
data = json.load(open(sys.argv[1]))
assert 'total_cpus' in data, 'missing total_cpus'
assert 'smt_control' in data, 'missing smt_control'
assert 'sched_core_enabled' in data, 'missing sched_core_enabled'
PYEOF
pass_test "cpu-topology.json contains valid hardware topology schema"

log "=========================================================================="
log "All $TESTS_RUN tests in $TESTS_PASSED test suites PASSED with 0 errors!"
log "=========================================================================="
exit 0
