#!/usr/bin/env bash
# AI-hint: bash Shell entrypoint for the A2A federation loopback smoke test (roadmap B5 / task_68).
# AI-doc: usr/share/doc/mios/manual/tests.md
set -euo pipefail

if [[ -z "${MIOS_AGENT_PIPE_URL:-}" && -r /etc/mios/install.env ]]; then
    . /etc/mios/install.env || true
    if [[ -n "${MIOS_PORTS_AGENT_PIPE:-}" ]]; then
        export MIOS_AGENT_PIPE_URL="http://127.0.0.1:${MIOS_PORTS_AGENT_PIPE}"
    fi
fi

TESTER="$(command -v mios-a2a-test || echo /usr/libexec/mios/mios-a2a-test)"

echo "[test-a2a-loopback] driving A2A loopback round-trip via ${MIOS_AGENT_PIPE_URL:-default endpoint}"
exec "$TESTER" --loopback "$@"
