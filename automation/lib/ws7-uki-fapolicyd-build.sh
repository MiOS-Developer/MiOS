#!/usr/bin/env bash
# AI-hint: bash Builds a verity-rooted Unified Kernel Image (UKI) and configures fapolicyd in permissive mode based on mios.toml flags; use this t...
# AI-doc: usr/share/doc/mios/manual/lib.md
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"
source "${SCRIPT_DIR}/packages.sh"

_ws7_scalar() {
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

_ws7_is_true() {
    case "${1:-}" in
        true|TRUE|True|1|yes|YES|on|ON) return 0 ;;
        *) return 1 ;;
    esac
}

ws7_install_fapolicyd_observe() {
    local enable
    enable="$(_ws7_scalar security.fapolicyd_observe enable)"
    enable="${enable:-${MIOS_FAPOLICYD_OBSERVE_ENABLE:-false}}"
    if ! _ws7_is_true "$enable"; then
        log "[ws7] fapolicyd observe drop-in disabled"
        return 0
    fi

    local src="/usr/lib/fapolicyd/mios-ws7-permissive.conf"
    if [[ ! -f "$src" ]]; then
        warn "[ws7] observe drop-in $src missing"
        return 0
    fi

    log "[ws7] installing fapolicyd PERMISSIVE/observe config"
    install -d -m 0755 /etc/fapolicyd
    if [[ -f /etc/fapolicyd/fapolicyd.conf ]]; then
        cp -a /etc/fapolicyd/fapolicyd.conf /etc/fapolicyd/fapolicyd.conf.pre-ws7 || true
    fi
    install -m 0644 "$src" /etc/fapolicyd/fapolicyd.conf || warn "[ws7] could not install observe conf"

    local rules_src="/usr/lib/fapolicyd/rules.d/80-mios-agent-codegen.rules"
    if [[ -f "$rules_src" ]]; then
        local work snap scratch
        work="$(_ws7_scalar paths coderun_workspace_root)";   work="${work:-/var/home/mios/coderuns}"
        snap="$(_ws7_scalar paths coderun_snapshots_root)";   snap="${snap:-/var/home/mios/.coderun-snapshots}"
        scratch="$(_ws7_scalar paths ai_scratch_dir)";        scratch="${scratch:-/var/lib/mios/ai/scratch}"
        [[ "$work"    == */ ]] || work="${work}/"
        [[ "$snap"    == */ ]] || snap="${snap}/"
        [[ "$scratch" == */ ]] || scratch="${scratch}/"
        local dst="/etc/fapolicyd/rules.d/80-mios-agent-codegen.rules"
        install -d -m 0755 /etc/fapolicyd/rules.d
        sed -e "s#^allow perm=execute all : dir=/var/home/mios/coderuns/#allow perm=execute all : dir=${work}#" \
            -e "s#^allow perm=execute all : dir=/var/home/mios/.coderun-snapshots/#allow perm=execute all : dir=${snap}#" \
            -e "s#^allow perm=execute all : dir=/var/lib/mios/ai/scratch/#allow perm=execute all : dir=${scratch}#" \
            "$rules_src" > "$dst" 2>/dev/null \
            && log "[ws7] rendered codegen carve-out rules -> $dst" \
            || warn "[ws7] could not render carve-out rules"
    fi

    log "[ws7] wrote fapolicyd permissive=1 config to /etc/fapolicyd/fapolicyd.conf; fapolicyd logs matches, does not deny"
    log "[ws7] promotion to enforce is operator-gated"
}

ws7_build_verity_uki() {
    local enable
    enable="$(_ws7_scalar uki verity_uki_build)"
    enable="${enable:-${MIOS_UKI_VERITY_UKI_BUILD:-false}}"
    if ! _ws7_is_true "$enable"; then
        log "[ws7] verity-rooted UKI build disabled"
        return 0
    fi

    if ! command -v ukify >/dev/null 2>&1; then
        warn "[ws7] ukify not found"
        return 0
    fi

    local cmdline_file="/usr/lib/kernel/cmdline"
    local cmdline=""
    [[ -f "$cmdline_file" ]] && cmdline="$(tr -d '\n' < "$cmdline_file")"

    local kver kdir vmlinuz initrd out_dir out
    kver="$(find /usr/lib/modules/ -mindepth 1 -maxdepth 1 -printf '%f\n' 2>/dev/null | sort -V | tail -1)"
    if [[ -z "$kver" ]]; then
        warn "[ws7] no kernel under /usr/lib/modules"
        return 0
    fi
    kdir="/usr/lib/modules/${kver}"
    vmlinuz="${kdir}/vmlinuz"
    initrd="${kdir}/initramfs.img"
    out_dir="/usr/lib/modules/${kver}"
    out="${out_dir}/mios-verity.efi"

    if [[ ! -f "$vmlinuz" ]]; then
        warn "[ws7] vmlinuz missing at $vmlinuz"
        return 0
    fi

    log "[ws7] building verity-rooted UKI for kernel ${kver}"
    log "[ws7]   cmdline: ${cmdline:-<empty>}"

    local ukify_args=(build
        --linux="$vmlinuz"
        --uname="$kver"
        --cmdline="$cmdline"
        --output="$out"
    )
    [[ -f "$initrd" ]] && ukify_args+=(--initrd="$initrd")

    if ukify "${ukify_args[@]}" 2>&1; then
        log "[ws7] UKI artifact written: $out"
        log "[ws7] NOTE: this is an unsigned/un-installed ARTIFACT. It is NOT the"
        log "[ws7] active boot entry. Signing + install + rollback"
        log "[ws7] test are the documented operator promotion steps. Booting an"
        log "[ws7] unsigned/required UKI BRICKS BOOT"
        log "[ws7] verity.require kargs until the promotion procedure passes"
    else
        warn "[ws7] ukify build failed"
    fi
}

main() {
    log "[ws7] UKI + fapolicyd hardening build step"
    ws7_install_fapolicyd_observe || warn "[ws7] fapolicyd observe step degraded"
    ws7_build_verity_uki        || warn "[ws7] UKI build step degraded"
    log "[ws7] done"
    return 0
}

main "$@"
