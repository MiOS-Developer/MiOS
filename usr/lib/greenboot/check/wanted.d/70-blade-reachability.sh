#!/bin/bash
# AI-hint: Non-critical greenboot check that probes blade reachability per ADR-0016 §8 / AGY-1600. Records reachability state without triggering a rollback unless blade_reachability_critical = true.
# AI-related: /usr/share/mios/mios.toml, /etc/mios/mios.toml, /usr/lib/greenboot/check/required.d/40-mios-ai-plane.sh
set -euo pipefail

log()  { echo "[mios-greenboot] $*"; }
warn() { echo "[mios-greenboot] WARNING: $*" >&2; }

# Sourced globals or defaults
PORT="${MIOS_PORTS_AGENT_PIPE:-8700}"
HOST="${MIOS_BLADE_HOST:-127.0.0.1}"

log "Checking blade reachability at http://${HOST}:${PORT}/v1/cluster/health..."

if command -v curl >/dev/null 2>&1; then
    if curl -sSf -m 3 "http://${HOST}:${PORT}/v1/cluster/health" >/dev/null 2>&1; then
        log "SUCCESS: blade is reachable"
        exit 0
    elif curl -sSf -m 3 "http://${HOST}:${PORT}/v1/models" >/dev/null 2>&1; then
        log "SUCCESS: blade endpoint is reachable (/v1/models)"
        exit 0
    fi
fi

CRITICAL="${MIOS_BLADE_REACHABILITY_CRITICAL:-false}"
if [[ "$CRITICAL" == "true" ]]; then
    warn "ERROR: blade is unreachable and blade_reachability_critical = true"
    exit 1
else
    log "INFO: blade unreachable (advisory check per ADR-0016 §8, boot continues)"
    exit 0
fi
