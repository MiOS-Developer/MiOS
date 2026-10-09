#!/usr/bin/env bash
# AI-hint: Automated CI test suite for systemd-oomd PSI pressure manager, subagent worker eviction, and daemon protection (task_764, task_765).
# AI-related: etc/systemd/oomd.conf, usr/lib/systemd/oomd.conf.d/10-mios-oomd.conf, usr/libexec/mios/kernel/oomd_psi.py, tools/ci-suites.py
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OOMD_CONF="${ROOT_DIR}/etc/systemd/oomd.conf"
VENDOR_OOMD_CONF="${ROOT_DIR}/usr/lib/systemd/oomd.conf.d/10-mios-oomd.conf"
SYSTEM_SLICE_CONF="${ROOT_DIR}/usr/lib/systemd/system/system.slice.d/10-oom-protect.conf"
INIT_SCOPE_CONF="${ROOT_DIR}/usr/lib/systemd/system/init.scope.d/10-oom-protect.conf"
USER_SVC_CONF="${ROOT_DIR}/usr/lib/systemd/system/user@.service.d/10-oom-kill.conf"
SUBAGENT_SLICE="${ROOT_DIR}/usr/lib/systemd/system/subagent.slice"
OOM_PSI_PY="${ROOT_DIR}/usr/libexec/mios/kernel/oomd_psi.py"

pass_count=0
fail_count=0

assert_pass() {
    echo "  PASS: $1"
    pass_count=$((pass_count + 1))
}

assert_fail() {
    echo "  FAIL: $1" >&2
    fail_count=$((fail_count + 1))
}

echo "[test-systemd-oomd-psi] === MiOS Systemd-OOMD PSI Memory Pressure Test Suite ==="

# 1. Verify etc/systemd/oomd.conf configuration
if [[ -f "$OOMD_CONF" ]]; then
    if grep -q "DefaultMemoryPressureLimit=50%" "$OOMD_CONF" && grep -q "DefaultMemoryPressureDurationSec=5s" "$OOMD_CONF"; then
        assert_pass "etc/systemd/oomd.conf configures 50% PSI pressure limit and 5s duration"
    else
        assert_fail "etc/systemd/oomd.conf missing required 50% / 5s settings"
    fi
else
    assert_fail "Missing $OOMD_CONF"
fi

# 2. Verify vendor usr/lib/systemd/oomd.conf.d/10-mios-oomd.conf
if [[ -f "$VENDOR_OOMD_CONF" ]]; then
    assert_pass "Vendor oomd drop-in exists at $VENDOR_OOMD_CONF"
else
    assert_fail "Missing vendor oomd drop-in at $VENDOR_OOMD_CONF"
fi

# 3. Verify system.slice protection
if [[ -f "$SYSTEM_SLICE_CONF" ]]; then
    if grep -q "OOMScoreAdjust=-1000" "$SYSTEM_SLICE_CONF" && grep -q "ManagedOOMPreference=omit" "$SYSTEM_SLICE_CONF"; then
        assert_pass "system.slice protected with OOMScoreAdjust=-1000 and ManagedOOMPreference=omit"
    else
        assert_fail "system.slice missing protection parameters"
    fi
else
    assert_fail "Missing $SYSTEM_SLICE_CONF"
fi

# 4. Verify init.scope protection
if [[ -f "$INIT_SCOPE_CONF" ]]; then
    if grep -q "OOMScoreAdjust=-1000" "$INIT_SCOPE_CONF" && grep -q "ManagedOOMPreference=omit" "$INIT_SCOPE_CONF"; then
        assert_pass "init.scope protected with OOMScoreAdjust=-1000 and ManagedOOMPreference=omit"
    else
        assert_fail "init.scope missing protection parameters"
    fi
else
    assert_fail "Missing $INIT_SCOPE_CONF"
fi

# 5. Verify user@.service configuration
if [[ -f "$USER_SVC_CONF" ]]; then
    if grep -q "ManagedOOMMemoryPressure=kill" "$USER_SVC_CONF" && grep -q "ManagedOOMPreference=avoid" "$USER_SVC_CONF"; then
        assert_pass "user@.service configured with ManagedOOMMemoryPressure=kill and ManagedOOMPreference=avoid"
    else
        assert_fail "user@.service missing ManagedOOMMemoryPressure=kill"
    fi
else
    assert_fail "Missing $USER_SVC_CONF"
fi

# 6. Verify subagent.slice configuration
if [[ -f "$SUBAGENT_SLICE" ]]; then
    if grep -q "ManagedOOMMemoryPressure=kill" "$SUBAGENT_SLICE"; then
        assert_pass "subagent.slice configured with ManagedOOMMemoryPressure=kill"
    else
        assert_fail "subagent.slice missing ManagedOOMMemoryPressure=kill"
    fi
else
    assert_fail "Missing $SUBAGENT_SLICE"
fi

# 7. Run synthetic memory balloon simulation in subagent slice
echo "[test-systemd-oomd-psi] Simulating memory exhaustion in subagent slice..."
sim_output="$(python3 "$OOM_PSI_PY" --test-balloon --slice "subagent.slice" --victim "worker-balloon.service" --pressure 95.0)"

duration="$(echo "$sim_output" | python3 -c "import sys, json; data=json.load(sys.stdin); print(data.get('eviction_duration_sec', 999))")"
victim_evicted="$(echo "$sim_output" | python3 -c "import sys, json; data=json.load(sys.stdin); print(data.get('victim_evicted', False))")"
protected_intact="$(echo "$sim_output" | python3 -c "import sys, json; data=json.load(sys.stdin); print(data.get('protected_services_intact', False))")"

if python3 -c "import sys; sys.exit(0 if float('$duration') < 5.0 else 1)"; then
    assert_pass "Eviction triggered in < 5.0s (measured: ${duration}s)"
else
    assert_fail "Eviction exceeded 5.0s (measured: ${duration}s)"
fi

if [[ "$victim_evicted" == "True" ]]; then
    assert_pass "Targeted subagent worker cgroup successfully evicted"
else
    assert_fail "Subagent worker cgroup was not evicted"
fi

if [[ "$protected_intact" == "True" ]]; then
    assert_pass "Core system daemons (systemd, journald, sshd, hyprland, pgvector, llm-light) suffered 0 kills"
else
    assert_fail "Protected system services were compromised"
fi

# 8. Verify forensic audit logging with cgroup attribution
python3 - << 'EOF'
import sys
import os

ROOT_DIR = os.environ.get("ROOT_DIR", ".")
sys.path.insert(0, os.path.join(ROOT_DIR, "usr/libexec/mios/kernel"))
from oomd_psi import OOMDPressureManager

mgr = OOMDPressureManager(dry_run=True)
mgr.evaluate_pressure_stall("subagent.slice", 90.0, ["worker-ai.service"], duration_sec=1.2)
events = mgr.get_logged_events()
assert len(events) >= 1, "Expected security_events log entry"
ev = events[0]
assert ev["event_type"] == "OOM_EVICTION", "Wrong event type"
assert "subagent.slice/worker-ai.service" in ev["attribution"], f"Attribution mismatch: {ev['attribution']}"
assert ev["psi_pct"] == 90.0, "PSI percentage mismatch"
print("  PASS: Forensic security audit event logged with full cgroup attribution")
EOF
pass_count=$((pass_count + 1))

echo "[test-systemd-oomd-psi] Summary: $pass_count passed, $fail_count failed."
if [[ $fail_count -eq 0 ]]; then
    echo "[test-systemd-oomd-psi] SUCCESS: All test assertions passed!"
    exit 0
else
    echo "[test-systemd-oomd-psi] FAILED: $fail_count test(s) failed." >&2
    exit 1
fi
