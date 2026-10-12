#!/usr/bin/env bash
# AI-hint: MiOS' init-user-space -- initializes XDG user configuration for MiOS.
# AI-related: /usr/share/mios/mios.toml.example, mios-dev, localhost:8080
# AI-functions: _render_unified, _get
set -euo pipefail

FORCE=""
[[ "${1:-}" == "--force" || "${1:-}" == "-f" ]] && FORCE=1

MIOS_CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/mios"
MIOS_DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/mios"
MIOS_CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/mios"
MIOS_STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/mios"
MIOS_UNIFIED="${MIOS_CONFIG_DIR}/mios.toml"
MIOS_TEMPLATE="${MIOS_TEMPLATE:-/usr/share/mios/mios.toml.example}"

mkdir -p "$MIOS_CONFIG_DIR" "$MIOS_DATA_DIR" "$MIOS_CACHE_DIR" "$MIOS_STATE_DIR"

_render_unified() {
    local legacy_user="" legacy_host="" legacy_flat="" legacy_base=""
    local legacy_localtag="" legacy_bib="" legacy_imagename=""

    _get() {
        grep -E "^$2\s*=" "$1" 2>/dev/null \
            | head -1 \
            | sed 's/.*=\s*"\?\([^"]*\)"\?.*/\1/' \
            | tr -d '"' || true
    }

    if [[ -f "${MIOS_CONFIG_DIR}/env.toml" ]]; then
        legacy_user=$(_get "${MIOS_CONFIG_DIR}/env.toml" MIOS_IDENTITY_USERNAME)
        legacy_host=$(_get "${MIOS_CONFIG_DIR}/env.toml" MIOS_IDENTITY_HOSTNAME)
        legacy_flat=$(_get "${MIOS_CONFIG_DIR}/env.toml" MIOS_DESKTOP_FLATPAKS)
        legacy_base=$(_get "${MIOS_CONFIG_DIR}/env.toml" MIOS_BASE_IMAGE)
        legacy_localtag=$(_get "${MIOS_CONFIG_DIR}/env.toml" MIOS_LOCAL_TAG)
    fi
    if [[ -f "${MIOS_CONFIG_DIR}/images.toml" ]]; then
        [[ -z "$legacy_base" ]] && legacy_base=$(_get "${MIOS_CONFIG_DIR}/images.toml" MIOS_BASE_IMAGE)
        legacy_bib=$(_get "${MIOS_CONFIG_DIR}/images.toml" MIOS_BIB_IMAGE)
        legacy_imagename=$(_get "${MIOS_CONFIG_DIR}/images.toml" MIOS_IMAGE_NAME)
    fi
    if [[ -f "${MIOS_CONFIG_DIR}/build.toml" && -z "$legacy_localtag" ]]; then
        legacy_localtag=$(_get "${MIOS_CONFIG_DIR}/build.toml" MIOS_LOCAL_TAG)
    fi
    local legacy_flatpaks_arr=()
    if [[ -f "${MIOS_CONFIG_DIR}/flatpaks.list" ]]; then
        while IFS= read -r line; do
            line=$(echo "$line" | sed -E 's/^\s+|\s+$//g')
            [[ -z "$line" || "$line" =~ ^# ]] && continue
            legacy_flatpaks_arr+=("$line")
        done < "${MIOS_CONFIG_DIR}/flatpaks.list"
    fi
    if [[ -n "$legacy_flat" && ${#legacy_flatpaks_arr[@]} -eq 0 ]]; then
        IFS=',' read -ra legacy_flatpaks_arr <<<"$legacy_flat"
    fi

    local legacy_role="" legacy_features=""
    if [[ -f "${MIOS_CONFIG_DIR}/profile.toml" ]]; then
        legacy_role=$(_get "${MIOS_CONFIG_DIR}/profile.toml" role)
        legacy_features=$(_get "${MIOS_CONFIG_DIR}/profile.toml" features)
    fi

    local legacy_env_pairs=()
    if [[ -f "${MIOS_CONFIG_DIR}/env" ]]; then
        while IFS= read -r line; do
            line=$(echo "$line" | sed -E 's/^\s+|\s+$//g')
            [[ -z "$line" || "$line" =~ ^# ]] && continue
            if [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)=(.+)$ ]]; then
                local k="${BASH_REMATCH[1]}" v="${BASH_REMATCH[2]}"
                v="${v#\"}"; v="${v%\"}"
                v="${v#\'}"; v="${v%\'}"
                legacy_env_pairs+=("$k=$v")
            fi
        done < "${MIOS_CONFIG_DIR}/env"
    fi

    local any_legacy=""
    [[ -n "$legacy_user$legacy_host$legacy_base$legacy_localtag$legacy_bib$legacy_imagename$legacy_role$legacy_features" ]] && any_legacy=1
    [[ ${#legacy_flatpaks_arr[@]} -gt 0 || ${#legacy_env_pairs[@]} -gt 0 ]] && any_legacy=1

    if [[ -z "$any_legacy" && -f "$MIOS_TEMPLATE" ]]; then
        cat "$MIOS_TEMPLATE"
        return
    fi

    cat <<TOML

[user]
$( [[ -n "$legacy_user" ]] && echo "name = \"$legacy_user\"" || echo "# name = \"mios\"" )
$( [[ -n "$legacy_host" ]] && echo "hostname = \"$legacy_host\"" || echo "# hostname = \"mios\"" )

[image]
$( [[ -n "$legacy_base" ]] && echo "base = \"$legacy_base\"" || echo "# base = \"ghcr.io/ublue-os/ucore-hci:stable-nvidia\"" )
$( [[ -n "$legacy_bib" ]] && echo "bib  = \"$legacy_bib\"" || echo "# bib  = \"quay.io/centos-bootc/bootc-image-builder:latest\"" )
$( [[ -n "$legacy_imagename" ]] && echo "name = \"$legacy_imagename\"" || echo "# name = \"ghcr.io/mios-dev/mios\"" )

[build]
$( [[ -n "$legacy_localtag" ]] && echo "local_tag = \"$legacy_localtag\"" || echo "# local_tag = \"localhost/mios:latest\"" )

[flatpaks]
TOML
    if [[ ${#legacy_flatpaks_arr[@]} -gt 0 ]]; then
        echo "Install = ["
        for f in "${legacy_flatpaks_arr[@]}"; do
            echo "    \"$f\","
        done
        echo "]"
    else
        echo "# install = []"
    fi
    cat <<TOML

[ai]

[blade]
TOML
    # [profile] is retired; the archetype has one canonical name, [blade].type.
    # Gate: check_role_ssot.
    if [[ -n "$legacy_role" ]]; then
        echo "type = \"$legacy_role\""
    else
        echo "# type = \"desktop\"   # hybrid|compute|controller|headless|desktop|endpoint"
    fi
    # Legacy `features` were never blade capabilities -- the shipped values were
    # ai/virtualization/k3s, none of which any archetype grants. Capabilities are
    # a closed set; add one with `mios blade add-capability <cap>`.
    if [[ -n "$legacy_features" ]]; then
        echo "# legacy features (not blade capabilities): $legacy_features"
    fi
    echo
    echo "[env]"
    if [[ ${#legacy_env_pairs[@]} -gt 0 ]]; then
        for kv in "${legacy_env_pairs[@]}"; do
            local ek="${kv%%=*}" ev="${kv#*=}"
            echo "$ek = \"$ev\""
        done
    else
        echo "# EDITOR = \"nvim\""
        echo "# PAGER  = \"less -R\""
    fi
}

if [[ -f "$MIOS_UNIFIED" && -z "$FORCE" ]]; then
    echo "[skip] $MIOS_UNIFIED already exists"
else
    _render_unified > "$MIOS_UNIFIED"
    chmod 0644 "$MIOS_UNIFIED"
    echo "[OK]   wrote $MIOS_UNIFIED"
fi

cat <<MSG

[OK] 'MiOS' user-space initialized
     Config:  $MIOS_CONFIG_DIR (mios.toml)
     Data:    $MIOS_DATA_DIR
     Cache:   $MIOS_CACHE_DIR
     State:   $MIOS_STATE_DIR

Edit ~/.config/mios/mios.toml to override vendor defaults, then run:
     just build
MSG
