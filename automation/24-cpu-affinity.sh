#!/usr/bin/env bash
# MIOS_APPLY_CLASS=universal
# AI-hint: Configures CPU affinity, systemd slice hierarchy (system.slice, user.slice, subagent.slice), discovers SMT topology, and validates Linux Core Scheduling (CONFIG_SCHED_CORE).
# AI-doc: usr/share/doc/mios/manual/automation.md
# AI-related: usr/libexec/mios/mios-core-sched, tests/test-core-sched.sh, usr/lib/systemd/system/subagent.slice
set -euo pipefail

for _mlog in "$(dirname "${BASH_SOURCE[0]}")/../usr/lib/mios/log.sh" /usr/lib/mios/log.sh; do
    if [ -r "$_mlog" ]; then
        # shellcheck source=usr/lib/mios/log.sh
        . "$_mlog"
        break
    fi
done

command -v mios_log &>/dev/null || mios_log() { echo "[24-cpu-affinity] $*"; }
command -v mios_ok &>/dev/null || mios_ok() { echo "[24-cpu-affinity] OK: $*"; }
command -v mios_warn &>/dev/null || mios_warn() { echo "[24-cpu-affinity] WARN: $*"; }

TARGET_ROOT="${CPU_AFFINITY_TARGET_ROOT:-}"  # test seam: write into a fixture root

mios_log "Starting CPU affinity and core scheduling configuration (task_802)"

# ---------------------------------------------------------------------------
# 1. Audit Kernel Core Scheduling Configuration (CONFIG_SCHED_CORE)
# ---------------------------------------------------------------------------
mios_log "Step 1: Auditing Linux kernel Core Scheduling support (CONFIG_SCHED_CORE)"

KVER=$(cat "${TARGET_ROOT}/tmp/mios-kver" 2>/dev/null || uname -r 2>/dev/null || echo "")
CONFIG_FOUND=0
SCHED_CORE_ACTIVE=0

CONFIG_CANDIDATES=(
    "${TARGET_ROOT}/boot/config-${KVER}"
    "${TARGET_ROOT}/lib/modules/${KVER}/config"
    "/boot/config-${KVER}"
    "/lib/modules/${KVER}/config"
)

for cfg in "${CONFIG_CANDIDATES[@]}"; do
    if [ -r "$cfg" ]; then
        CONFIG_FOUND=1
        if grep -q "^CONFIG_SCHED_CORE=y" "$cfg" 2>/dev/null; then
            SCHED_CORE_ACTIVE=1
            mios_ok "Kernel configuration confirms CONFIG_SCHED_CORE=y in $cfg"
            break
        fi
    fi
done

if [ "$SCHED_CORE_ACTIVE" -eq 0 ]; then
    if [ "$CONFIG_FOUND" -eq 1 ]; then
        mios_warn "Kernel config found but CONFIG_SCHED_CORE=y is not set; SMT sibling isolation will operate in degrade-open fallback mode"
    else
        mios_log "Kernel config not directly accessible in build environment; checking runtime capability"
        if [ -x "${TARGET_ROOT}/usr/libexec/mios/mios-core-sched" ]; then
            if "${TARGET_ROOT}/usr/libexec/mios/mios-core-sched" status 2>/dev/null | grep -q "Core Scheduling (PR_SCHED_CORE): ENABLED"; then
                SCHED_CORE_ACTIVE=1
                mios_ok "Runtime check confirms Linux Core Scheduling is supported"
            fi
        fi
        if [ "$SCHED_CORE_ACTIVE" -eq 0 ]; then
            mios_warn "CONFIG_SCHED_CORE unconfirmed; core scheduling utilities will gracefully degrade open if unsupported"
        fi
    fi
fi

# ---------------------------------------------------------------------------
# 2. Inspect CPU & SMT Hardware Topology
# ---------------------------------------------------------------------------
mios_log "Step 2: Inspecting SMT sibling topology and physical core count"

VAR_MIOS_DIR="${TARGET_ROOT}/var/lib/mios"
mkdir -p "${VAR_MIOS_DIR}"

SMT_CONTROL="unknown"
SMT_ACTIVE="false"
TOTAL_CPUS=1

if [ -f "/sys/devices/system/cpu/smt/control" ]; then
    SMT_CONTROL=$(cat /sys/devices/system/cpu/smt/control 2>/dev/null || echo "unknown")
fi

if [ -f "/sys/devices/system/cpu/smt/active" ]; then
    if [ "$(cat /sys/devices/system/cpu/smt/active 2>/dev/null || echo 0)" = "1" ]; then
        SMT_ACTIVE="true"
    fi
fi

if command -v nproc &>/dev/null; then
    TOTAL_CPUS=$(nproc 2>/dev/null || echo 1)
elif [ -d "/sys/devices/system/cpu" ]; then
    TOTAL_CPUS=$(find /sys/devices/system/cpu -maxdepth 1 -name "cpu[0-9]*" 2>/dev/null | wc -l || echo 1)
fi

# Cache discovery into cpu-topology.json
cat > "${VAR_MIOS_DIR}/cpu-topology.json" <<EOF
{
  "smt_control": "${SMT_CONTROL}",
  "smt_active": ${SMT_ACTIVE},
  "total_cpus": ${TOTAL_CPUS},
  "sched_core_enabled": $([ "$SCHED_CORE_ACTIVE" -eq 1 ] && echo "true" || echo "false"),
  "configured_at": "$(date -u +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || echo "unknown")"
}
EOF
chmod 0644 "${VAR_MIOS_DIR}/cpu-topology.json"
mios_ok "CPU topology cached to ${VAR_MIOS_DIR}/cpu-topology.json (SMT=${SMT_CONTROL}, CPUs=${TOTAL_CPUS})"

# ---------------------------------------------------------------------------
# 3. Configure Systemd Slices and CPU Affinity Drop-ins
# ---------------------------------------------------------------------------
mios_log "Step 3: Configuring systemd slices and CPU weight / quota hierarchy"

SYSTEM_SLICE_D="${TARGET_ROOT}/usr/lib/systemd/system/system.slice.d"
USER_SLICE_D="${TARGET_ROOT}/usr/lib/systemd/system/user.slice.d"
SUBAGENT_SLICE_D="${TARGET_ROOT}/usr/lib/systemd/system/subagent.slice.d"

mkdir -p "${SYSTEM_SLICE_D}" "${USER_SLICE_D}" "${SUBAGENT_SLICE_D}"

# system.slice: High CPU priority for core system daemons
cat > "${SYSTEM_SLICE_D}/20-cpu-affinity.conf" <<'EOF'
# AI-hint: Prioritizes system infrastructure and critical background daemons over untrusted workloads (task_802).
# AI-related: automation/24-cpu-affinity.sh, usr/libexec/mios/mios-core-sched
[Slice]
CPUWeight=200
CPUAccounting=yes
IOAccounting=yes
EOF
chmod 0644 "${SYSTEM_SLICE_D}/20-cpu-affinity.conf"

# user.slice: Standard baseline CPU priority for interactive desktop applications
cat > "${USER_SLICE_D}/20-cpu-affinity.conf" <<'EOF'
# AI-hint: Interactive user session CPU weighting for responsive desktop rendering (task_802).
# AI-related: automation/24-cpu-affinity.sh
[Slice]
CPUWeight=100
CPUAccounting=yes
IOAccounting=yes
EOF
chmod 0644 "${USER_SLICE_D}/20-cpu-affinity.conf"

# subagent.slice: Constrained CPU weight, quota, and process limits for untrusted subagents
cat > "${SUBAGENT_SLICE_D}/20-cpu-affinity.conf" <<'EOF'
# AI-hint: Constrains untrusted subagent and sandbox processes to prevent CPU starvation and hardware SMT abuse (task_802).
# AI-related: usr/lib/systemd/system/subagent.slice, usr/libexec/mios/mios-core-sched
[Slice]
CPUWeight=50
CPUQuota=200%
TasksMax=256
CPUAccounting=yes
IOAccounting=yes
EOF
chmod 0644 "${SUBAGENT_SLICE_D}/20-cpu-affinity.conf"

# Ensure base subagent.slice definition is complete
SUBAGENT_SLICE="${TARGET_ROOT}/usr/lib/systemd/system/subagent.slice"
if [ ! -f "${SUBAGENT_SLICE}" ]; then
    cat > "${SUBAGENT_SLICE}" <<'EOF'
# AI-hint: MiOS Subagent Worker Slice with active ManagedOOMMemoryPressure=kill policy and CPU constraints (task_764, task_802).
# AI-related: automation/24-cpu-affinity.sh, usr/libexec/mios/mios-core-sched
[Unit]
Description=MiOS Subagent Worker Slice
Documentation=man:systemd.slice(5)
Before=slices.target

[Slice]
CPUWeight=50
CPUQuota=200%
TasksMax=256
CPUAccounting=yes
IOAccounting=yes
ManagedOOMMemoryPressure=kill
ManagedOOMMemoryPressureLimit=50%
ManagedOOMPreference=none
EOF
    chmod 0644 "${SUBAGENT_SLICE}"
    mios_ok "Created base subagent.slice definition"
fi

# ---------------------------------------------------------------------------
# 4. Verify Core Scheduling Utility Permissions
# ---------------------------------------------------------------------------
mios_log "Step 4: Verifying mios-core-sched utility permissions"

CORE_SCHED_BIN="${TARGET_ROOT}/usr/libexec/mios/mios-core-sched"
if [ -f "${CORE_SCHED_BIN}" ]; then
    chmod 0755 "${CORE_SCHED_BIN}"
    mios_ok "Verified executable permissions on ${CORE_SCHED_BIN}"
fi

mios_ok "CPU affinity, systemd slice hierarchy, and core scheduling configuration complete"
exit 0
