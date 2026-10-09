# AI-hint: Resolves and exports MiOS environment variables (MIOS_*) by merging layered TOML configs and .env files to provide a unified configuration for CLI tools, agents, and...
# AI-doc: usr/share/doc/mios/manual/profile.d.md
# shellcheck shell=sh

case "$-" in
    *i*) ;;
    *)
        if [ -r /usr/lib/mios/userenv.sh ] || [ -r /usr/share/mios/tools/lib/userenv.sh ]; then
            for _ue in /usr/lib/mios/userenv.sh /usr/share/mios/tools/lib/userenv.sh; do
                if [ -r "$_ue" ]; then
                    # shellcheck source=usr/lib/mios/userenv.sh
                    . "$_ue"
                    break
                fi
            done
            export MIOS_AI_ENDPOINT MIOS_AI_MODEL MIOS_AI_KEY BROWSER="${BROWSER:-/usr/libexec/mios/mios-open-url}" MIOS_BROWSER="${MIOS_BROWSER:-/usr/libexec/mios/mios-open-url}"
        fi
        return 0 2>/dev/null || exit 0
        ;;
esac

_mios_source_if_readable() {
    [ -r "$1" ] || return 0
    # shellcheck source=/dev/null  # operator env files (~/.env.mios, /etc/mios/env.d, install.env) exist only on the host
    . "$1"
}

_mios_source_if_readable "${HOME}/.env.mios"

if [ -d /etc/mios/env.d ]; then
    for _f in /etc/mios/env.d/*.env; do
        _mios_source_if_readable "$_f"
    done
    unset _f
fi

_mios_source_if_readable /etc/mios/install.env

_mios_source_if_readable "${HOME}/.config/mios/env"

for _ue in /usr/lib/mios/userenv.sh /usr/share/mios/tools/lib/userenv.sh; do
    if [ -r "$_ue" ]; then
        # shellcheck source=usr/lib/mios/userenv.sh
        . "$_ue"
        break
    fi
done
unset _ue

export MIOS_AI_ENDPOINT="${MIOS_AI_ENDPOINT:-http://localhost:${MIOS_PORTS_AGENT_PIPE:-8700}/v1}"
export MIOS_AI_GATEWAY_MODEL="${MIOS_AI_GATEWAY_MODEL:-MiOS-Agent}"
export MIOS_AI_MODEL="${MIOS_AI_MODEL:-granite4.1:8b}"
export MIOS_AI_EMBED_MODEL="${MIOS_AI_EMBED_MODEL:-nomic-embed-text}"
if [ -z "${MIOS_AI_KEY:-}" ] && [ -r /etc/mios/hermes/api.env ]; then
    MIOS_AI_KEY="$(awk -F= '/^API_SERVER_KEY=/ { gsub(/"/, "", $2); print $2; exit }' /etc/mios/hermes/api.env 2>/dev/null)"
fi
export MIOS_AI_KEY="${MIOS_AI_KEY:-}"

export MIOS_IDENTITY_USERNAME="${MIOS_IDENTITY_USERNAME:-${MIOS_IDENTITY_USERNAME:-mios}}"
export MIOS_IDENTITY_HOSTNAME="${MIOS_IDENTITY_HOSTNAME:-${MIOS_IDENTITY_HOSTNAME:-mios}}"
export MIOS_VERSION="${MIOS_VERSION:-}"

export MIOS_SHARE_DIR="${MIOS_SHARE_DIR:-/usr/share/mios}"
export MIOS_AI_DIR="${MIOS_AI_DIR:-/usr/share/mios/ai}"
export MIOS_AI_SCRATCH_DIR="${MIOS_AI_SCRATCH_DIR:-/var/lib/mios/ai/scratch}"
export MIOS_AI_MEMORY_DIR="${MIOS_AI_MEMORY_DIR:-/var/lib/mios/ai/memory}"
export BROWSER="${BROWSER:-/usr/libexec/mios/mios-open-url}"
export MIOS_BROWSER="${MIOS_BROWSER:-/usr/libexec/mios/mios-open-url}"

unset -f _mios_source_if_readable
