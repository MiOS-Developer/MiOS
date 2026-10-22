#!/usr/bin/env bash
# AI-hint: bash Documented in INDEX.md sec 4 as the canonical CLI for inspecting the AI-related: /etc/mios/env.d/, /etc/mios/install.env, /etc/mios/env.d, /u...
# AI-doc: usr/share/doc/mios/manual/mios.md
set -euo pipefail

LAYERS=(
    "${HOME}/.env.mios"
)
if [[ -d /etc/mios/env.d ]]; then
    while IFS= read -r -d '' f; do
        LAYERS+=("$f")
    done < <(find /etc/mios/env.d -maxdepth 1 -name '*.env' -print0 2>/dev/null | sort -z)
fi
LAYERS+=(
    "/etc/mios/install.env"
    "${HOME}/.config/mios/env"
)

_load_layers() {
    local layer
    for layer in "${LAYERS[@]}"; do
        [[ -r "$layer" ]] || continue
        # shellcheck source=/dev/null  # SSOT env layers are host/user runtime files
        . "$layer"
    done
}

_load_toml_layers() {
    local userenv_lib
    for userenv_lib in /usr/lib/mios/userenv.sh /usr/share/mios/tools/lib/userenv.sh; do
        if [[ -r "$userenv_lib" ]]; then
            # shellcheck source=/dev/null  # first readable of two install locations
            . "$userenv_lib"
            return 0
        fi
    done
}

_print_kv() {
    while IFS= read -r line; do
        printf '%s\n' "$line"
    done < <(compgen -A variable | grep -E '^MIOS_' | sort | while read -r v; do
        printf '%s=%q\n' "$v" "${!v-}"
    done)
}

_print_json() {
    printf '{\n'
    local first=1
    while IFS= read -r v; do
        local val="${!v-}"
        val=${val//\\/\\\\}
        val=${val//\"/\\\"}
        val=${val//$'\n'/\\n}
        val=${val//$'\t'/\\t}
        if [[ $first -eq 1 ]]; then first=0; else printf ',\n'; fi
        printf '  "%s": "%s"' "$v" "$val"
    done < <(compgen -A variable | grep -E '^MIOS_' | sort)
    printf '\n}\n'
}

_print_explain() {
    declare -A SOURCE_OF
    local layer
    for layer in "${LAYERS[@]}"; do
        [[ -r "$layer" ]] || continue
        while IFS= read -r key; do
            [[ -n "$key" ]] && SOURCE_OF["$key"]="$layer"
        done < <(grep -oE '^[[:space:]]*MIOS_[A-Z0-9_]+(?==)' "$layer" 2>/dev/null \
            | sed -E 's/^[[:space:]]*//; s/=$//' || true)
    done

    while IFS= read -r v; do
        printf '%-32s = %-30s [%s]\n' \
            "$v" "${!v-}" "${SOURCE_OF[$v]:-runtime/default}"
    done < <(compgen -A variable | grep -E '^MIOS_' | sort)
}

_print_unset() {
    while IFS= read -r v; do
        printf 'unset %s\n' "$v"
    done < <(compgen -A variable | grep -E '^MIOS_' | sort)
}

_help() {
    sed -n '2,/^set -euo/p' "$0" | sed -n '/^# /p' | sed 's/^# //'
}

case "${1:-}" in
    -h|--help)
        _help
        exit 0
        ;;
    --json)
        _load_layers
        _load_toml_layers 2>/dev/null || true
        _print_json
        ;;
    --explain)
        _load_layers
        _load_toml_layers 2>/dev/null || true
        _print_explain
        ;;
    --unset)
        _load_layers
        _load_toml_layers 2>/dev/null || true
        _print_unset
        ;;
    "")
        _load_layers
        _load_toml_layers 2>/dev/null || true
        _print_kv
        ;;
    MIOS_*)
        _load_layers
        _load_toml_layers 2>/dev/null || true
        var="$1"
        printf '%s\n' "${!var-}"
        ;;
    *)
        printf 'mios-env: unknown argument %q (try --help)\n' "$1" >&2
        exit 2
        ;;
esac
