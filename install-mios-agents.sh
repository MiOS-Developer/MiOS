#!/usr/bin/env bash
# AI-hint: bash Installs wrapper scripts for mios-agent-claude, mios-agent-gemini, mios-agent-agy, and mios-llm to /usr/local/bin/ to inject the MiOS system prompt into various LLM ...
# AI-doc: usr/share/doc/mios/manual/root.md
set -euo pipefail

# The checkout this script lives in: a clone, or / on a deployed MiOS host.
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# MIOS_REQUIRE_AGREEMENT_ACK).
[[ -r "${repo_root}/automation/lib/agreements-banner.sh" ]] && \
    . "${repo_root}/automation/lib/agreements-banner.sh" && \
    mios_print_agreement_banner "install-mios-agents.sh"

# DESTDIR stages the install under another root (packaging, tests) and needs
# no privilege; a real install escalates only when it is not already root.
DESTDIR="${DESTDIR:-}"
as_root=()
if [[ -z "$DESTDIR" && "$(id -u)" -ne 0 ]]; then
    as_root=(sudo)
fi
bindir="${DESTDIR}/usr/local/bin"

# The canonical prompt is usr/share/mios/ai/system.md. The root system-prompt.md
# and /etc/mios/ai/system-prompt.md are redirectors to it, and /etc is the host
# override tier, so neither is copied anywhere. A MiOS image already ships the
# canonical file in its read-only /usr; only a host without it gets the
# checkout's copy.
PROMPT_SRC="${repo_root}/usr/share/mios/ai/system.md"
PROMPT_DST="${DESTDIR}/usr/share/mios/ai/system.md"

if [[ -r "$PROMPT_DST" ]]; then
    echo "Canonical system prompt already installed: $PROMPT_DST"
elif [[ -r "$PROMPT_SRC" ]]; then
    "${as_root[@]}" install -D -m 0644 "$PROMPT_SRC" "$PROMPT_DST"
    echo "Installed system prompt to $PROMPT_DST"
else
    echo "ERROR: Cannot find the canonical system prompt at $PROMPT_SRC" >&2
    echo "Run this script from a MiOS checkout (or from / on a MiOS host)." >&2
    exit 1
fi

"${as_root[@]}" mkdir -p "$bindir"

"${as_root[@]}" tee "$bindir/mios-agent-claude" > /dev/null <<'SCRIPT'
#!/usr/bin/env bash
set -euo pipefail
PROMPT_FILE=""
for p in /usr/share/mios/ai/system.md /etc/mios/ai/system-prompt.md /system-prompt.md "${PWD}/system-prompt.md" "${PWD}/CLAUDE.md"; do
    [[ -r "$p" ]] && PROMPT_FILE="$p" && break
done
[[ -z "$PROMPT_FILE" ]] && { echo "Mios-agent-claude: no system prompt found" >&2; exit 1; }
echo "[agent] launching with system prompt: $PROMPT_FILE" >&2
exec claude --append-system-prompt "$(cat "$PROMPT_FILE")" "$@"
SCRIPT
"${as_root[@]}" chmod 755 "$bindir/mios-agent-claude"
echo "Installed $bindir/mios-agent-claude"

"${as_root[@]}" tee "$bindir/mios-agent-gemini" > /dev/null <<'SCRIPT'
#!/usr/bin/env bash
set -euo pipefail
PROMPT_FILE=""
for p in /usr/share/mios/ai/system.md /etc/mios/ai/system-prompt.md /system-prompt.md "${PWD}/system-prompt.md" "${PWD}/GEMINI.md"; do
    [[ -r "$p" ]] && PROMPT_FILE="$p" && break
done
[[ -z "$PROMPT_FILE" ]] && { echo "Mios-agent-gemini: no system prompt found" >&2; exit 1; }
echo "[agent] launching with system prompt: $PROMPT_FILE" >&2
exec gemini --system-prompt "$(cat "$PROMPT_FILE")" "$@"
SCRIPT
"${as_root[@]}" chmod 755 "$bindir/mios-agent-gemini"
echo "Installed $bindir/mios-agent-gemini"

# The endpoint and model resolve from the layered SSOT via the resolver-rendered
# install.env (bare KEY=value); a caller's export wins, and an unresolved value
# is a named error, never a literal default.
"${as_root[@]}" tee "$bindir/mios-agent-agy" > /dev/null <<'SCRIPT'
#!/usr/bin/env bash
set -euo pipefail
PROMPT_FILE=""
for p in /usr/share/mios/ai/system.md /etc/mios/ai/system-prompt.md /system-prompt.md "${PWD}/system-prompt.md" "${PWD}/.agents/rules/AGENTS.md" "${PWD}/AGENTS.md" "${PWD}/GEMINI.md"; do
    [[ -r "$p" ]] && PROMPT_FILE="$p" && break
done
[[ -z "$PROMPT_FILE" ]] && { echo "Mios-agent-agy: no system prompt found" >&2; exit 1; }
_mios_env() {
    local f="${MIOS_ETC_DIR:-/etc/mios}/install.env"
    [[ -r "$f" ]] || return 0
    sed -n "s/^$1=//p" "$f" | tail -n 1
}
export MIOS_AI_ENDPOINT="${MIOS_AI_ENDPOINT:-$(_mios_env MIOS_AI_ENDPOINT)}"
export MIOS_AI_MODEL="${MIOS_AI_MODEL:-$(_mios_env MIOS_AI_MODEL)}"
: "${MIOS_AI_ENDPOINT:?MIOS_AI_ENDPOINT is unset -- ${MIOS_ETC_DIR:-/etc/mios}/install.env is missing or stale}"
: "${MIOS_AI_MODEL:?MIOS_AI_MODEL is unset -- ${MIOS_ETC_DIR:-/etc/mios}/install.env is missing or stale}"
if [[ -z "${DBUS_SESSION_BUS_ADDRESS:-}" ]] && command -v dbus-launch >/dev/null 2>&1; then
    eval "$(dbus-launch --sh-syntax 2>/dev/null || true)"
fi
echo "[agent] launching agy with prompt: $PROMPT_FILE" >&2
exec agy "$@"
SCRIPT
"${as_root[@]}" chmod 755 "$bindir/mios-agent-agy"
echo "Installed $bindir/mios-agent-agy"

"${as_root[@]}" tee "$bindir/mios-llm" > /dev/null <<'SCRIPT'
#!/usr/bin/env bash
set -euo pipefail
PROMPT_FILE=""
for p in /usr/share/mios/ai/system.md /etc/mios/ai/system-prompt.md /system-prompt.md "${PWD}/system-prompt.md"; do
    [[ -r "$p" ]] && PROMPT_FILE="$p" && break
done
[[ -z "$PROMPT_FILE" ]] && { echo "Mios-llm: no system prompt found" >&2; exit 1; }
_mios_env() {
    local f="${MIOS_ETC_DIR:-/etc/mios}/install.env"
    [[ -r "$f" ]] || return 0
    sed -n "s/^$1=//p" "$f" | tail -n 1
}
ENDPOINT="${MIOS_AI_ENDPOINT:-$(_mios_env MIOS_AI_ENDPOINT)}"
MODEL="${MIOS_AI_MODEL:-$(_mios_env MIOS_AI_MODEL)}"
: "${ENDPOINT:?MIOS_AI_ENDPOINT is unset -- ${MIOS_ETC_DIR:-/etc/mios}/install.env is missing or stale}"
: "${MODEL:?MIOS_AI_MODEL is unset -- ${MIOS_ETC_DIR:-/etc/mios}/install.env is missing or stale}"
USER_PROMPT="${*:-What are the six 'MiOS' Architectural Laws, in order?}"
echo "[agent] $MODEL @ $ENDPOINT prompt: $PROMPT_FILE" >&2
curl -sS "$ENDPOINT/chat/completions" \
    -H "Content-Type: application/json" \
    -d "$(jq -n --arg model "$MODEL" --arg sys "$(cat "$PROMPT_FILE")" --arg user "$USER_PROMPT" \
        '{model:$model, messages:[{role:"system",content:$sys},{role:"user",content:$user}], stream:false}')" \
    | jq -r '.choices[0].message.content'
SCRIPT
"${as_root[@]}" chmod 755 "$bindir/mios-llm"
echo "Installed $bindir/mios-llm"

echo ""
echo "=== Verification ==="
echo "Prompt file:"
ls -la "$PROMPT_DST"
echo ""
echo "First line:"
head -1 "$PROMPT_DST"
echo ""
echo "Launcher path check:"
grep "for p in" "$bindir/mios-agent-claude"
echo ""
echo "Done. Run:"
echo "  mios-agent-claude     # if the 'claude' CLI binary is installed"
echo "  mios-agent-gemini     # if the 'gemini' CLI binary is installed"
echo "  mios-agent-agy        # if the 'agy' CLI binary is installed"
echo "  mios-llm 'your question here'   # vendor-neutral, OpenAI /v1 only"
