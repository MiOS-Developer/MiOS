#!/usr/bin/env bash
# AI-hint: bash Provides idempotent shared helper functions, logging utilities, and environment resolution logic (masking, paths, globals) for MiOS build scripts a...
# AI-doc: usr/share/doc/mios/manual/lib.md

source "$(dirname "${BASH_SOURCE[0]}")/masking.sh"
source "$(dirname "${BASH_SOURCE[0]}")/paths.sh"

_mios_locate_userenv() {
    local self_dir
    self_dir="$(dirname "${BASH_SOURCE[0]}")"
    local candidates=(
        "${self_dir}/../../tools/lib/userenv.sh"
        "/tools/lib/userenv.sh"
        "/ctx/tools/lib/userenv.sh"
        "/usr/share/mios/tools/lib/userenv.sh"
    )
    for c in "${candidates[@]}"; do
        if [[ -f "$c" ]]; then
            printf '%s' "$c"
            return 0
        fi
    done
    return 1
}
_mios_userenv_path="$(_mios_locate_userenv 2>/dev/null || true)"
if [[ -n "$_mios_userenv_path" ]]; then
    # shellcheck source=/dev/null  # located at runtime: repo, /ctx or the image
    source "$_mios_userenv_path"
fi
unset _mios_userenv_path
unset -f _mios_locate_userenv

source "$(dirname "${BASH_SOURCE[0]}")/globals.sh"

_common_dir="$(dirname "${BASH_SOURCE[0]}")"
if [[ -f "${_common_dir}/../../usr/lib/mios/log.sh" ]]; then
    source "${_common_dir}/../../usr/lib/mios/log.sh"
elif [[ -f "/usr/lib/mios/log.sh" ]]; then
    source "/usr/lib/mios/log.sh"
fi

log_ts() { date '+%Y-%m-%d %H:%M:%S'; }
log()  { printf '[%s] ==> %s\n' "$(log_ts)" "$*"; }
warn() { printf '[%s] WARN: %s\n' "$(log_ts)" "$*" >&2; }
die()  { printf '[%s] ERROR: %s\n' "$(log_ts)" "$*" >&2; exit 1; }
diag() { printf '[%s] DIAG: %s\n' "$(log_ts)" "$*"; }

# Native configuration projections share one resolver and fail on older tools.
mios_project_config() {
    local root="$1" mode="$2" candidate binary=""
    shift 2
    for candidate in \
        "$root/tools/native/target/release/mios-unit-gen" \
        "$root/tools/native/target/debug/mios-unit-gen" \
        "$root/tools/native/target/release/mios-unit-gen.exe" \
        "$root/tools/native/target/debug/mios-unit-gen.exe" \
        /usr/libexec/mios/mios-unit-gen /opt/mios/bin/mios-unit-gen; do
        [[ -n "$candidate" && -x "$candidate" ]] && { binary="$candidate"; break; }
    done
    if [[ -z "$binary" ]]; then
        echo "[projection] mios-unit-gen is required; build it in MiOS-DEV" >&2
        return 1
    fi
    if ! "$binary" --list-projections | tr -d '\r' | grep -Fxq "$mode"; then
        echo "[projection] mios-unit-gen does not advertise $mode; rebuild it from this checkout" >&2
        return 1
    fi
    "$binary" "$mode" --root "$root" "$@"
}

if ! declare -f mios_log >/dev/null; then
    mios_log()  { log "$@"; }
    mios_ok()   { log "OK $*"; }
    mios_step() { log "STEP $*"; }
    mios_skip() { log "SKIP $*"; }
    mios_warn() { warn "$@"; }
    mios_err()  { warn "ERR $*"; }
fi

if command -v dnf5 &>/dev/null; then
    export DNF_BIN="dnf5"
else
    export DNF_BIN="dnf"
fi

if [[ -z "${DNF_SETOPT+x}" || "$(declare -p DNF_SETOPT 2>/dev/null)" != "declare -a"* ]]; then
    declare -ga DNF_SETOPT=(
        --setopt=install_weak_deps=False
        --setopt=timeout=10          # cut per-mirror connection attempt at 10 s
        --setopt=minrate=1k          # drop any mirror delivering < 1 kB/s after timeout
        --setopt=max_parallel_downloads=10  # pull from 10 mirrors simultaneously
        --setopt=ip_resolve=4        # prefer IPv4; many Fedora IPv6 paths time out in WSL2
        --setopt=cachedir=/var/cache/dnf5
        --setopt=keepcache=1
        --setopt=clean_requirements_on_remove=1
    )
fi
if [[ -z "${DNF_OPTS+x}" || "$(declare -p DNF_OPTS 2>/dev/null)" != "declare -a"* ]]; then
    declare -ga DNF_OPTS=(--allowerasing)
fi
export DNF_SETOPT_STR="${DNF_SETOPT[*]}"
export DNF_OPTS_STR="${DNF_OPTS[*]}"

export MIOS_VERSION_MANIFEST="${MIOS_VERSION_MANIFEST:-/tmp/mios-build-versions.tsv}"

record_version() {
    local component="$1" version="$2" resolved_to="${3:-}"
    if [[ ! -f "$MIOS_VERSION_MANIFEST" ]]; then
        printf 'component\tversion\tresolved_to\trecorded_at\n' > "$MIOS_VERSION_MANIFEST"
    fi
    printf '%s\t%s\t%s\t%s\n' \
        "$component" "$version" "$resolved_to" "$(log_ts)" \
        >> "$MIOS_VERSION_MANIFEST"
    log "Version: ${component} = ${version}${resolved_to:+}"
}
