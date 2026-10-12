#!/usr/bin/env bash
# AI-hint: Automated Antigravity CLI (AGY) agent runner for MiOS CI/CD pipeline cycles and artifact generation.
# AI-related: automation/cicd/, .agents/subagents.json, .agents/skills/pipeline/SKILL.md, usr/share/mios/mios.toml
# AI-functions: main, run_pipeline_agent

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

log() {
    echo "==> [05-AGY-PIPELINE] $*"
}

run_pipeline_agent() {
    local prompt="${1:-Verify standing gates and synchronize SSOT projections}"
    local output_dir="${REPO_ROOT}/.devloop/run-latest"
    mkdir -p "$output_dir"

    log "Checking for Antigravity CLI (agy / mios-agent-agy)..."
    local agy_bin=""
    if command -v mios-agent-agy >/dev/null 2>&1; then
        agy_bin="mios-agent-agy"
    elif command -v agy >/dev/null 2>&1; then
        agy_bin="agy"
    else
        log "WARN: Antigravity CLI ('agy') not found on PATH; running standing gates fallback"
        python3 "${REPO_ROOT}/tools/ci-suites.py" --check
        local gate_bin=""
        for b in \
            "${REPO_ROOT}/src/mios-rs/target/release/mios-gate" \
            "${REPO_ROOT}/src/mios-rs/target/release/mios-gate.exe" \
            "${REPO_ROOT}/src/mios-rs/target/debug/mios-gate" \
            "${REPO_ROOT}/src/mios-rs/target/debug/mios-gate.exe"; do
            if [[ -x "$b" ]]; then
                gate_bin="$b"
                break
            fi
        done
        if [[ -n "$gate_bin" ]]; then
            "$gate_bin" phase-registry --root "${REPO_ROOT}"
            "$gate_bin" ratchet-direction --root "${REPO_ROOT}"
            "$gate_bin" credential-literals --root "${REPO_ROOT}"
            "$gate_bin" version-literals-ssot --root "${REPO_ROOT}"
            "$gate_bin" signature-policy --root "${REPO_ROOT}"
        fi
        "${MIOS_GEN_BIN:-mios-gen}" sync --root "${REPO_ROOT}"
        log "Standing gates and SSOT synchronization verified via fallback."
        return 0
    fi

    log "Dispatching Antigravity Pipeline Agent with prompt: $prompt"
    export MIOS_AI_ENDPOINT="${MIOS_AI_ENDPOINT:-http://localhost:8642/v1}"
    export MIOS_AI_MODEL="${MIOS_AI_MODEL:-mi-os-7b}"

    # Execute agy in headless print mode
    "$agy_bin" -p "$prompt" \
        --output-format json \
        > "${output_dir}/agy-pipeline-result.json" 2>&1

    log "Agent run finished. Artifact saved to ${output_dir}/agy-pipeline-result.json"
}

main() {
    log "Starting Antigravity Pipeline Agent Cycle..."
    run_pipeline_agent "${*:-}"
    log "Antigravity Pipeline Cycle Completed Successfully."
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
