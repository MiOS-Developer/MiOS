#!/usr/bin/env bash
# AI-hint: Greenboot wanted check to monitor CephFS health and pool utilization, logging warnings to pgvector without triggering system rollback.
# AI-related: greenboot, mios-pg-query, [storage.cephfs]

set -uo pipefail

if [ -f /etc/profile.d/mios-env.sh ]; then
    source /etc/profile.d/mios-env.sh
fi

if [ "${MIOS_STORAGE_CEPHFS_ENABLE:-false}" != "true" ]; then
    exit 0
fi

exit_code=0
failure_details=""

log_health_failure() {
    local check_name="$1"
    local message="$2"
    failure_details="${failure_details}${check_name}: ${message}; "
    echo "[greenboot-cephfs] [FAIL] ${check_name}: ${message}" >&2
}

if command -v ceph >/dev/null 2>&1; then
    _health_output=$(ceph health 2>/dev/null || true)
    if echo "$_health_output" | grep -q "HEALTH_ERR"; then
        log_health_failure "ceph_health" "Cluster is in HEALTH_ERR state: $_health_output"
        exit_code=1
    fi
else
    log_health_failure "ceph_cli" "ceph command not found"
    exit_code=1
fi

if command -v ceph >/dev/null 2>&1 && command -v jq >/dev/null 2>&1; then
    _df_output=$(ceph df --format json 2>/dev/null || true)
    if [ -n "$_df_output" ]; then
        _over_limit=$(echo "$_df_output" | jq -r '.pools[] | select(.stats.percent_used >= 90) | "\(.name): \(.stats.percent_used)%"' 2>/dev/null || true)
        if [ -n "$_over_limit" ]; then
            log_health_failure "pool_capacity" "Ceph pool(s) exceed 90% utilization: $_over_limit"
            exit_code=1
        fi
    fi
fi

if command -v ceph >/dev/null 2>&1; then
    if ! ceph fs status 2>/dev/null | grep -q "active"; then
        log_health_failure "mds_status" "No active CephFS MDS daemons found"
        exit_code=1
    fi
fi

_op_user="${MIOS_IDENTITY_USERNAME:-mios}"
if command -v findmnt >/dev/null 2>&1; then
    if ! findmnt "/home/$_op_user" -t ceph >/dev/null 2>&1; then
        log_health_failure "user_mount" "CephFS mount for user $_op_user is not active on /home/$_op_user"
        exit_code=1
    fi
fi

if [ "$exit_code" -ne 0 ]; then
    if command -v mios-pg-query >/dev/null 2>&1; then
        _summary="CephFS health check failed: $failure_details"
        _payload="{\"detail\": \"$failure_details\", \"status\": \"failed\"}"
        mios-pg-query "INSERT INTO event(kind, source, severity, summary, payload) VALUES (\$1, \$2, \$3, \$4, \$5::jsonb)" \
            "storage_health" "cephfs" "warn" "$_summary" "$_payload" >/dev/null 2>&1 || true
    fi
fi

exit "$exit_code"
