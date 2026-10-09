#!/usr/bin/env bash
# MIOS_APPLY_CLASS=bake-only
# AI-hint: Processes boot arguments from kargs.d/*.toml files into a single string at /usr/lib/kernel/cmdline to prepare the Unified Kernel Image (UKI) during the build or deployment phase.
set -euo pipefail

# shellcheck source=/dev/null
for _mlog in "$(dirname "${BASH_SOURCE[0]}")/../usr/lib/mios/log.sh" /usr/lib/mios/log.sh; do [ -r "$_mlog" ] && . "$_mlog" && break; done

mios_log "Render kernel cmdline from bootc kargs.d/*.toml for the UKI"

source "$(dirname "$0")/lib/packages.sh"
source "$(dirname "$0")/lib/common.sh"

if ! rpm -q systemd-ukify >/dev/null 2>&1; then
    mios_log "Systemd-ukify not found; reinstalling via mios.toml [packages.uki]"
    install_packages_strict "uki"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
KERNEL_CMDLINE_DST="/usr/lib/kernel/cmdline"
install -d -m 0755 /usr/lib/kernel

# Folded from 02-uki-bootloader.sh (task_3454): ensure 02-security.conf cmdline drop-in exists
install -d -m 0755 "${ROOT}/etc/cmdline.d"
[[ -f "${ROOT}/etc/cmdline.d/02-security.conf" ]] || echo "module.sig_enforce=1 lockdown=confidentiality" > "${ROOT}/etc/cmdline.d/02-security.conf"

# Absolute path, never `command -v`: miosd installs to /usr/libexec/mios, which
# nothing puts on PATH at bake time, so the lookup this replaced could never
# succeed and the branch below it was dead on every build (task_962).
_miosd=""
for _c in "${MIOS_MIOSD_BIN:-}" \
          /usr/libexec/mios/miosd \
          "${ROOT}/src/mios-rs/target/release/miosd" \
          "${ROOT}/src/mios-rs/target/debug/miosd"; do
    if [[ -n "$_c" && -x "$_c" ]]; then _miosd="$_c"; break; fi
done

# The daemon and CLI call the same Rust projection library. Pass the root
# explicitly so the input directory and output file cannot follow the cwd.
if [[ -n "$_miosd" ]]; then
    mios_log "Render UKI cmdline via miosd"
    MIOS_ROOT="$ROOT" "$_miosd" render-uki-cmdline
else
    _unit_gen=""
    for _c in /usr/libexec/mios/mios-unit-gen \
              "${ROOT}/tools/native/target/release/mios-unit-gen" \
              "${ROOT}/tools/native/target/debug/mios-unit-gen"; do
        if [[ -n "$_c" && -x "$_c" ]]; then _unit_gen="$_c"; break; fi
    done
    if [[ -z "$_unit_gen" ]] || ! "$_unit_gen" --list-projections | grep -Fxq uki-cmdline; then
        mios_err "mios-unit-gen with uki-cmdline support is required; rebuild it in MiOS-DEV"
        exit 1
    fi
    mios_log "Render UKI cmdline via mios-unit-gen"
    "$_unit_gen" uki-cmdline --root "$ROOT"
fi

if [[ "${ROOT}/usr/lib/kernel/cmdline" != "${KERNEL_CMDLINE_DST}" ]]; then
    install -D -m 0644 "${ROOT}/usr/lib/kernel/cmdline" "${KERNEL_CMDLINE_DST}"
fi

CMDLINE=$(cat "${KERNEL_CMDLINE_DST}" | xargs)
if [ -z "$CMDLINE" ]; then
    mios_warn "/usr/lib/kernel/cmdline empty"
fi

mios_ok "Rendered UKI cmdline: $CMDLINE"
mios_ok "UKI cmdline rendered"
