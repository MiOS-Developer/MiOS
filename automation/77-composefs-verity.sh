#!/usr/bin/env bash
# MIOS_APPLY_CLASS=bake-only
# AI-hint: Configures the `prepare-root.conf` file by reading the `[security].composefs_mode` setting from `mios.toml` to ...
# AI-doc: usr/share/doc/mios/manual/automation.md
set -euo pipefail
# shellcheck source=/dev/null  # Resolve the repository or installed FHS logging library.
for _mlog in "$(dirname "${BASH_SOURCE[0]}")/../usr/lib/mios/log.sh" /usr/lib/mios/log.sh; do [ -r "$_mlog" ] && . "$_mlog" && break; done
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/common.sh"
source "${SCRIPT_DIR}/lib/packages.sh"

_read_mios_scalar() {
    local table="$1" key="$2" toml_path
    toml_path="$(_resolve_mios_toml 2>/dev/null || true)"
    [[ -n "$toml_path" && -f "$toml_path" ]] || return 0
    awk -v table="$table" -v key="$key" '
        /^\[/ {
            in_section = 0
            line = $0
            sub(/^\[/, "", line); sub(/\][[:space:]]*$/, "", line)
            gsub(/[[:space:]]/, "", line)
            if (line == table) in_section = 1
            next
        }
        in_section {
            if (match($0, "^[[:space:]]*" key "[[:space:]]*=")) {
                value = $0
                sub(/^[^=]*=[[:space:]]*/, "", value)
                sub(/[[:space:]]*#.*$/, "", value)
                gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
                gsub(/^"|"$/, "", value)
                print value
                exit 0
            }
        }
    ' "$toml_path"
}

MODE="$(_read_mios_scalar security composefs_mode)"
MODE="${MODE:-verity}"
MASK_REMOUNT="$(_read_mios_scalar security mask_systemd_remount_fs)"
MASK_REMOUNT="${MASK_REMOUNT:-true}"

case "$MODE" in
    verity|yes|off) ;;
    *)
        mios_warn "Unknown composefs_mode='${MODE}', falling back to 'verity'"
        MODE="verity"
        ;;
esac

if [[ "$MODE" == "off" ]]; then
    mios_skip "composefs_mode=off -- honoring base image's prepare-root.conf"
    exit 0
fi

conf="${COMPOSEFS_CONF:-/usr/lib/ostree/prepare-root.conf}"
if [[ -f "$conf" ]]; then
    if [[ ! -f "${conf}.orig" ]]; then
        mios_log "Backing up existing $conf -> ${conf}.orig"
        cp -a "$conf" "${conf}.orig"
    fi
fi

mios_log "Writing $conf with composefs mode=${MODE}"
# The hint is part of the projection: the tracked copy is gated byte-for-byte
# against this output (check_composefs_projection) and must carry a header.
cat > "$conf" <<EOF
# AI-hint: ostree prepare-root (composefs mode, root/etc mount policy); generated from mios.toml [security].composefs_mode by automation/77-composefs-verity.sh.
[composefs]
enabled = ${MODE}

[root]
transient = false

[etc]
transient = false
EOF

if [[ "$MODE" == "verity" && "$MASK_REMOUNT" =~ ^(true|TRUE|1|yes|YES)$ ]]; then
    mios_log "Masking systemd-remount-fs.service"
    install -d -m 0755 /etc/systemd/system
    ln -sf /dev/null /etc/systemd/system/systemd-remount-fs.service
fi

mios_ok "Composefs mode=${MODE} configured"
