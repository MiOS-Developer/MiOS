#!/usr/bin/env bash
# AI-hint: Git pre-commit hook script integrating Darwin Gödel Machine (DGM) capability regression gates.
# AI-related: usr/libexec/mios/mios-bench, usr/lib/mios/agent-pipe/mios_pipe/scheduler/bench.py, tests/test-dgm-loop.py

set -euo pipefail

BASELINE=""
CANDIDATE=""
K="8"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --baseline)
            BASELINE="$2"
            shift 2
            ;;
        --candidate)
            CANDIDATE="$2"
            shift 2
            ;;
        --k)
            K="$2"
            shift 2
            ;;
        *)
            if [[ -z "$CANDIDATE" ]]; then
                CANDIDATE="$1"
                shift
            else
                shift
            fi
            ;;
    esac
done

if [[ -z "$BASELINE" || -z "$CANDIDATE" ]]; then
    echo "Usage: 10-dgm-bench.sh --baseline <baseline.json> --candidate <candidate.json> [--k <k>]" >&2
    exit 1
fi

BENCH_BIN="${MIOS_BENCH_BIN:-/usr/libexec/mios/mios-bench}"

if output=$($BENCH_BIN gate "$CANDIDATE" --baseline "$BASELINE" --k "$K" 2>&1); then
    echo "$output"
    echo "Positive delta verified: mutation approved for commit."
    exit 0
else
    echo "$output" >&2
    echo "BLOCKED: candidate regression detected by DGM pre-commit gate." >&2
    exit 1
fi
