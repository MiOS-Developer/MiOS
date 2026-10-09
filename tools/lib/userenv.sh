#!/usr/bin/env bash
# AI-hint: bash Parses layered TOML configuration files (vendor, host, and user) to export unified MIOS_ environment variables for identity, locale, network, AI, an...
# AI-doc: usr/share/doc/mios/manual/lib.md

MIOS_VENDOR_TOML="${MIOS_VENDOR_TOML:-/usr/share/mios/mios.toml}"
MIOS_HOST_TOML="${MIOS_HOST_TOML:-/etc/mios/mios.toml}"
MIOS_CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/mios"
MIOS_USER_TOML="${MIOS_USER_TOML:-${MIOS_CONFIG_DIR}/mios.toml}"

MIOS_ROOT="${MIOS_ROOT:-}"
if [[ -z "$MIOS_ROOT" ]]; then
    if [[ "$MIOS_VENDOR_TOML" == *usr/share/mios/mios.toml ]]; then
        MIOS_ROOT="${MIOS_VENDOR_TOML%/usr/share/mios/mios.toml}"
        MIOS_ROOT="${MIOS_ROOT:-.}"
    else
        MIOS_ROOT="."
    fi
fi

_mios_load_unified() {
    local _use_rust=1
    if [[ "${MIOS_MIGRATION_USE_RUST_RESOLVER_SHELL:-true}" == "false" || "${MIOS_MIGRATION_USE_RUST_RESOLVER_SHELL:-true}" == "0" ]]; then
        _use_rust=0
    fi

    # 1. Native compiled resolver binary (primary tier)
    if [[ "$_use_rust" -eq 1 ]]; then
        # xtrace off: the ~200KB export block truncates the image build log.
        local _xt=""; case "$-" in *x*) _xt=1; set +x ;; esac
        local _r="" _native_exports=""
        command -v mios-resolver >/dev/null 2>&1 && _r=mios-resolver
        [[ -z "$_r" && -x /usr/libexec/mios/mios-resolver ]] && _r=/usr/libexec/mios/mios-resolver
        if [[ -n "$_r" ]]; then
            if ! _native_exports=$("$_r" --emit=shell); then
                [[ -z "$_xt" ]] || set -x
                return 1
            fi
            _native_exports="${_native_exports//$'\r'/}"
            if [[ -n "$_native_exports" ]] && eval "$_native_exports"; then
                [[ -z "$_xt" ]] || set -x
                return 0
            fi
            [[ -z "$_xt" ]] || set -x
            printf '[mios-resolver] native resolver produced no usable environment\n' >&2
            return 1
        fi
        [[ -z "$_xt" ]] || set -x
    fi

    # 2. Daemon resolver fallback
    if command -v miosd >/dev/null 2>&1; then
        local _d_exports=""
        if _d_exports=$(miosd resolve --shell 2>/dev/null | tr -d '\r') && [[ -n "$_d_exports" ]] && [[ $(wc -l <<< "$_d_exports") -gt 50 ]]; then
            eval "$_d_exports" && return 0
        fi
    fi

    # 3. Python SSOT resolver fallback
    local py_cmd="${MIOS_PYTHON_BIN:-}"
    if [[ -z "$py_cmd" ]]; then
        if python3 -c "import sys" >/dev/null 2>&1; then py_cmd="python3"
        elif python -c "import sys" >/dev/null 2>&1; then py_cmd="python"
        elif py -c "import sys" >/dev/null 2>&1; then py_cmd="py"
        fi
    fi

    if [[ -n "$py_cmd" ]]; then
        local _py_exports=""
        local _py_path="usr/lib/mios/mios_toml.py"
        if [[ ! -f "$_py_path" && -n "$MIOS_ROOT" && -f "$MIOS_ROOT/usr/lib/mios/mios_toml.py" ]]; then
            _py_path="$MIOS_ROOT/usr/lib/mios/mios_toml.py"
        fi
        if [[ -f "$_py_path" ]]; then
            if ! _py_exports=$(PYTHONIOENCODING=utf-8 "$py_cmd" "$_py_path" --emit=shell); then
                return 1
            fi
            _py_exports="${_py_exports//$'\r'/}"
            if [[ -n "$_py_exports" ]]; then
                eval "$_py_exports"
                unset MIOS_PYTHON_BIN 2>/dev/null || true
                return 0
            fi
        fi
    fi
}
_mios_load_unified || { return 1 2>/dev/null || exit 1; }

case "${MIOS_PGVECTOR_LISTEN_LOOPBACK:-true}" in
    false|False|FALSE|0|no|off) export MIOS_PG_BIND_ADDR="0.0.0.0" ;;
    *)                          export MIOS_PG_BIND_ADDR="127.0.0.1" ;;
esac

_mios_legacy_get() {
    local file="$1" key="$2"
    grep -E "^${key}\s*=" "$file" 2>/dev/null \
        | head -1 \
        | sed 's/.*=\s*"\?\([^"]*\)"\?.*/\1/' \
        | tr -d '"' || true
}

if [[ -z "${MIOS_IDENTITY_USERNAME:-}" && ! -f "$MIOS_USER_TOML" && ! -f "$MIOS_HOST_TOML" ]]; then
    if [[ -f "${MIOS_CONFIG_DIR}/env.toml" ]]; then
        f="${MIOS_CONFIG_DIR}/env.toml"
        for key in MIOS_IDENTITY_USERNAME MIOS_IDENTITY_HOSTNAME MIOS_DESKTOP_FLATPAKS MIOS_BASE_IMAGE MIOS_LOCAL_TAG; do
            val="$(_mios_legacy_get "$f" "$key")"
            [[ -z "$val" ]] || export "$key=$val"
        done
    fi
    if [[ -f "${MIOS_CONFIG_DIR}/images.toml" ]]; then
        f="${MIOS_CONFIG_DIR}/images.toml"
        for key in MIOS_BASE_IMAGE MIOS_BIB_IMAGE MIOS_IMAGE_NAME; do
            val="$(_mios_legacy_get "$f" "$key")"
            [[ -z "$val" ]] || export "$key=$val"
        done
    fi
    if [[ -f "${MIOS_CONFIG_DIR}/build.toml" ]]; then
        val="$(_mios_legacy_get "${MIOS_CONFIG_DIR}/build.toml" MIOS_LOCAL_TAG)"
        [[ -z "$val" ]] || export "MIOS_LOCAL_TAG=$val"
    fi
    if [[ -f "${MIOS_CONFIG_DIR}/flatpaks.list" ]]; then
        flat=$(grep -vE '^\s*(#|$)' "${MIOS_CONFIG_DIR}/flatpaks.list" 2>/dev/null | paste -sd,)
        [[ -z "$flat" ]] || export "MIOS_DESKTOP_FLATPAKS=$flat"
    fi
    if [[ -f "${MIOS_CONFIG_DIR}/env" ]]; then
        set -a
        source "${MIOS_CONFIG_DIR}/env"
        set +a
    fi
fi

_ssot_lint_ports_dummy=(
    "MIOS_PORTS_AGENT_PIPE"
    "MIOS_PORTS_CHROME_CDP"
    "MIOS_PORTS_COCKPIT_LINK"
    "MIOS_PORTS_CPU_NODE"
    "MIOS_PORTS_CRAWL4AI"
    "MIOS_PORTS_FIRECRAWL"
    "MIOS_PORTS_FORGE_HTTP"
    "MIOS_PORTS_FORGE_SSH"
    "MIOS_PORTS_GUACD"
    "MIOS_PORTS_LLM_HEAVY"
    "MIOS_PORTS_LLM_LIGHT"
    "MIOS_PORTS_NODE"
    "MIOS_PORTS_OPEN_WEBUI"
    "MIOS_PORTS_OTELCOL_OTLP"
    "MIOS_PORTS_OTELCOL_UI"
    "MIOS_PORTS_PGVECTOR"
    "MIOS_PORTS_PIPER"
    "MIOS_PORTS_PXE_HUB_API"
    "MIOS_PORTS_RADOSGW"
    "MIOS_PORTS_REDIS"
    "MIOS_PORTS_SEARXNG"
    "MIOS_PORTS_WHISPER"
    "MIOS_VERSIONS_FEDORA"
)
