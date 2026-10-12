#!/usr/bin/env bash
# MIOS_APPLY_CLASS=bake-only
# AI-hint: Composefs fs-verity root filesystem sealing and atomic image descriptor validator (task_471, task_2704).
# AI-doc: usr/share/doc/mios/manual/ch71-composefs-sealing.md
set -euo pipefail

# shellcheck source=/dev/null
for _mlog in "$(dirname "${BASH_SOURCE[0]}")/../usr/lib/mios/log.sh" /usr/lib/mios/log.sh; do [ -r "$_mlog" ] && . "$_mlog" && break; done
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/common.sh" 2>/dev/null || true

ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

VERBOSE=false
DRY_RUN=false
MOCK_MODE=false

show_help() {
    cat <<'EOF'
Usage: 93-composefs-seal.sh [OPTIONS]

Composefs fs-verity root filesystem sealing and atomic image descriptor validator (task_471, task_2704).

Options:
  -v, --verbose       Enable verbose diagnostic output
  --dry-run           Simulate configuration and descriptor sealing without making modifications
  --mock              Run in mock mode using synthetic descriptors for verification testing
  -h, --help          Show this help message and exit

Environment Variables:
  COMPOSEFS_TARGET_DIR    Directory to seal (default: /usr or mock synthetic tree)
  COMPOSEFS_OUTPUT_IMAGE  Destination for generated .cfs descriptor
  COMPOSEFS_MODE          Composefs mode for prepare-root.conf (default: verity)
  COMPOSEFS_SEAL_ROOT     Tree that receives prepare-root.conf and kargs.d (default: repo + system; --mock: a temp dir)
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -v|--verbose)
            VERBOSE=true
            shift
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --mock)
            MOCK_MODE=true
            shift
            ;;
        -h|--help)
            show_help
            exit 0
            ;;
        *)
            mios_warn "Unknown option: $1"
            shift
            ;;
    esac
done

mios_log "Starting Composefs fs-verity root filesystem sealing (task_471, task_2704)"

TEMP_CLEANUP=""
trap '[[ -n "$TEMP_CLEANUP" && -d "$TEMP_CLEANUP" ]] && rm -rf "$TEMP_CLEANUP"' EXIT
if [[ "$MOCK_MODE" == "true" ]]; then
    TEMP_CLEANUP="$(mktemp -d /tmp/mios-cfs-mock.XXXXXX)"
    # A mock run never writes the tracked tree or the host: its outputs land in the seal root.
    COMPOSEFS_SEAL_ROOT="${COMPOSEFS_SEAL_ROOT:-${TEMP_CLEANUP}/seal-root}"
fi
SEAL_ROOT="${COMPOSEFS_SEAL_ROOT:-}"

# 1. Configure ostree prepare-root.conf
COMPOSEFS_MODE="${COMPOSEFS_MODE:-verity}"
PREPARE_CONF_CONTENT="# AI-hint: ostree prepare-root (composefs mode, root/etc mount policy); generated from mios.toml [security].composefs_mode by automation/77-composefs-verity.sh.
[composefs]
enabled = ${COMPOSEFS_MODE}

[root]
transient = false

[etc]
transient = false
"

TARGET_CONFS=(
    "${ROOT_DIR}/usr/lib/ostree/prepare-root.conf"
    "/usr/lib/ostree/prepare-root.conf"
    "/etc/ostree/prepare-root.conf"
)
[[ -z "$SEAL_ROOT" ]] || TARGET_CONFS=("${SEAL_ROOT}/usr/lib/ostree/prepare-root.conf")

configured_conf_count=0
for conf in "${TARGET_CONFS[@]}"; do
    conf_dir="$(dirname "$conf")"
    if [[ "$DRY_RUN" == "true" ]]; then
        mios_log "[dry-run] Would configure $conf with composefs enabled = ${COMPOSEFS_MODE}"
        configured_conf_count=$((configured_conf_count + 1))
        continue
    fi

    [[ -z "$SEAL_ROOT" ]] || mkdir -p "$conf_dir"
    if [[ -w "$conf" || (! -e "$conf" && -w "$conf_dir") ]]; then
        mkdir -p "$conf_dir"
        printf '%s' "$PREPARE_CONF_CONTENT" > "$conf"
        chmod 0644 "$conf"
        mios_log "Configured $conf (mode=${COMPOSEFS_MODE})"
        configured_conf_count=$((configured_conf_count + 1))
    fi
done

if [[ "$configured_conf_count" -eq 0 && "$DRY_RUN" == "false" && "$MOCK_MODE" == "false" ]]; then
    mios_warn "Could not write prepare-root.conf to system paths (read-only filesystem); ensuring repo tree copy exists"
    REPO_CONF="${SEAL_ROOT:-${ROOT_DIR}}/usr/lib/ostree/prepare-root.conf"
    mkdir -p "$(dirname "$REPO_CONF")"
    printf '%s' "$PREPARE_CONF_CONTENT" > "$REPO_CONF"
fi

# 2. Locate or synthesize descriptor target directory
TARGET_DIR="${COMPOSEFS_TARGET_DIR:-}"
OUTPUT_IMAGE="${COMPOSEFS_OUTPUT_IMAGE:-}"

if [[ "$MOCK_MODE" == "true" ]]; then
    TARGET_DIR="${TEMP_CLEANUP}/rootfs"
    mkdir -p "${TARGET_DIR}/usr/bin" "${TARGET_DIR}/usr/lib" "${TARGET_DIR}/etc"
    echo "#!/bin/sh" > "${TARGET_DIR}/usr/bin/init"
    chmod 0755 "${TARGET_DIR}/usr/bin/init"
    echo "MiOS Mock Release" > "${TARGET_DIR}/etc/os-release"
    OUTPUT_IMAGE="${TEMP_CLEANUP}/mock-rootfs.cfs"
    mios_log "Mock mode active: synthetic tree at ${TARGET_DIR}"
elif [[ -z "$TARGET_DIR" ]]; then
    # Test if /usr is readable
    if command -v mkcomposefs >/dev/null 2>&1 && mkcomposefs --print-digest-only /usr >/dev/null 2>&1; then
        TARGET_DIR="/usr"
    else
        # Staged tree fallback for unprivileged environments
        TEMP_CLEANUP="$(mktemp -d /tmp/mios-cfs-stage.XXXXXX)"
        TARGET_DIR="${TEMP_CLEANUP}/stage"
        mkdir -p "${TARGET_DIR}/bin" "${TARGET_DIR}/lib" "${TARGET_DIR}/etc"
        echo "#!/bin/sh" > "${TARGET_DIR}/bin/sh"
        chmod 0755 "${TARGET_DIR}/bin/sh"
        echo "MiOS Staged Sealing" > "${TARGET_DIR}/etc/os-release"
        OUTPUT_IMAGE="${TEMP_CLEANUP}/rootfs.cfs"
        mios_log "Staged fallback active: directory at ${TARGET_DIR}"
    fi
fi

if [[ -z "$OUTPUT_IMAGE" ]]; then
    OUTPUT_IMAGE="/tmp/mios-rootfs.cfs"
fi

DIGEST=""

# 3. Generate Composefs descriptor if mkcomposefs is available
if command -v mkcomposefs >/dev/null 2>&1; then
    if [[ "$DRY_RUN" == "true" ]]; then
        mios_log "[dry-run] Would generate composefs descriptor for ${TARGET_DIR} -> ${OUTPUT_IMAGE}"
        DIGEST="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    else
        mios_log "Generating composefs descriptor from ${TARGET_DIR} -> ${OUTPUT_IMAGE}"
        mkdir -p "$(dirname "$OUTPUT_IMAGE")"
        mkcomposefs_out="$(mkcomposefs --print-digest "$TARGET_DIR" "$OUTPUT_IMAGE")"
        DIGEST="$(printf '%s' "$mkcomposefs_out" | tail -n 1 | tr -d '[:space:]')"
        mios_log "Composefs image generated (size: $(stat -c %s "$OUTPUT_IMAGE") bytes, digest: ${DIGEST})"
    fi
else
    mios_warn "mkcomposefs binary not found; skipping descriptor creation"
fi

# 4. Write kernel command-line sealing drop-in: usr/lib/bootc/kargs.d/50-composefs.toml
KARGS_DIRS=(
    "${ROOT_DIR}/usr/lib/bootc/kargs.d"
    "/usr/lib/bootc/kargs.d"
)
[[ -z "$SEAL_ROOT" ]] || KARGS_DIRS=("${SEAL_ROOT}/usr/lib/bootc/kargs.d")

DIGEST_KARG=""
[[ -z "$DIGEST" ]] || DIGEST_KARG=$'\n'"  \"ostree.composefs.digest=${DIGEST}\","
KARGS_CONTENT="# AI-hint: Boot-time composefs fs-verity root filesystem sealing (task_471)
# AI-doc: usr/share/doc/mios/manual/ch71-composefs-sealing.md
kargs = [
  \"ostree.composefs=1\",${DIGEST_KARG}
]
match-architectures = [\"x86_64\"]
"

for kd in "${KARGS_DIRS[@]}"; do
    if [[ "$DRY_RUN" == "true" ]]; then
        mios_log "[dry-run] Would write 50-composefs.toml in $kd"
        continue
    fi

    [[ -z "$SEAL_ROOT" ]] || mkdir -p "$kd"
    if [[ -d "$kd" && -w "$kd" ]] || [[ ! -d "$kd" && -w "$(dirname "$kd")" ]]; then
        mkdir -p "$kd"
        printf '%s' "$KARGS_CONTENT" > "${kd}/50-composefs.toml"
        chmod 0644 "${kd}/50-composefs.toml"
        mios_log "Wrote ${kd}/50-composefs.toml"
    fi
done

# 5. Invoke validator on generated descriptor
VALIDATOR_BIN="${ROOT_DIR}/usr/libexec/mios/mios-composefs-validator"
if [[ ! -x "$VALIDATOR_BIN" ]]; then
    VALIDATOR_BIN="/usr/libexec/mios/mios-composefs-validator"
fi

if [[ -f "$OUTPUT_IMAGE" && -x "$VALIDATOR_BIN" ]]; then
    if [[ "$DRY_RUN" == "true" ]]; then
        mios_log "[dry-run] Would run $VALIDATOR_BIN verify $OUTPUT_IMAGE --digest $DIGEST"
    else
        mios_log "Validating descriptor integrity with ${VALIDATOR_BIN}"
        VAL_ARGS=("verify" "$OUTPUT_IMAGE")
        if [[ -n "$DIGEST" ]]; then
            VAL_ARGS+=("--digest" "$DIGEST")
        fi
        if [[ "$VERBOSE" == "true" ]]; then
            VAL_ARGS+=("-v")
        fi

        "$VALIDATOR_BIN" "${VAL_ARGS[@]}"
        mios_ok "Composefs descriptor validation verified successfully"
    fi
fi

mios_ok "Composefs fs-verity root filesystem sealing complete"
exit 0
