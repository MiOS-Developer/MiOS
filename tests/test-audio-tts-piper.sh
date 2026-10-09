#!/usr/bin/env bash
# AI-hint: Verification wrapper for concurrent streaming Piper/Kokoro TTS audio synthesis and PipeWire buffer feeder (task_478, task_2711).
# AI-doc: usr/share/doc/mios/manual/ch79-streaming-tts-piper.md
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_TEST="${SCRIPT_DIR}/test-audio-tts-piper.py"

if [[ ! -f "${PYTHON_TEST}" ]]; then
    echo "[TEST] Error: ${PYTHON_TEST} not found" >&2
    exit 1
fi

exec python3 "${PYTHON_TEST}" "$@"
