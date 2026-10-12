#!/usr/bin/env bash
# MIOS_APPLY_CLASS=universal
# AI-hint: Configures Fedora 44 repository metadata, sets `install_weak_deps=False` in DNF, and enforces priority 98 for base repos to ensure stable package resolution on ucore.
set -euo pipefail
# shellcheck source=/dev/null
for _mlog in "$(dirname "${BASH_SOURCE[0]}")/../usr/lib/mios/log.sh" /usr/lib/mios/log.sh; do [ -r "$_mlog" ] && . "$_mlog" && break; done
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/packages.sh"
source "${SCRIPT_DIR}/lib/common.sh"

mios_log "Set install_weak_deps=False globally"
DNF_CONF="/usr/lib/dnf/dnf.conf"
[[ -f "$DNF_CONF" ]] || DNF_CONF="/etc/dnf/dnf.conf"
if [[ -f "$DNF_CONF" ]]; then
    sed -i '/^install_weak_deps=/d' "$DNF_CONF" 2>/dev/null || true
    echo "Install_weak_deps=False" >> "$DNF_CONF"
fi

mios_log "Elevate base repos to priority 98"
if [[ -d /etc/yum.repos.d ]]; then
    for repo in /etc/yum.repos.d/fedora*.repo /etc/yum.repos.d/ublue-os*.repo; do
        if [[ -f "$repo" ]] && ! grep -q '^priority=' "$repo"; then
            sed -i '/^\[.*\]/a priority=98' "$repo"
        fi
    done
fi

_fver="${FEDORA_VERSION:-44}"

mios_log "Import Fedora ${_fver} GPG key"
GPG_KEY_PATH="/etc/pki/rpm-gpg/RPM-GPG-KEY-fedora-${_fver}-x86_64"
if [[ ! -f "$GPG_KEY_PATH" ]]; then
    $DNF_BIN "${DNF_SETOPT[@]}" install -y --skip-unavailable fedora-gpg-keys \
        || warn "[01-repos] fedora-gpg-keys import failed; continuing"
fi

mios_log "Add Fedora ${_fver} repository"
# Absolute path, never `command -v`: miosd installs to /usr/libexec/mios, which
# nothing puts on PATH at bake time, so the lookup this replaced could never
# succeed and the branch below it was dead on every build (task_962).
_miosd=""
_r05="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
for _c in "${MIOS_MIOSD_BIN:-}" \
          /usr/libexec/mios/miosd \
          "${_r05}/src/mios-rs/target/release/miosd" \
          "${_r05}/src/mios-rs/target/debug/miosd"; do
    if [[ -n "$_c" && -x "$_c" ]]; then _miosd="$_c"; break; fi
done

if [[ -z "$_miosd" ]]; then
    mios_err "Native miosd is required to render repos (task_962)"
    exit 1
fi

_online_flag=()
if [[ "${MIOS_ONLINE_BUILD:-0}" == "1" ]]; then
    _online_flag=(--online)
fi
"$_miosd" render-repos --fedora-version "$_fver" "${_online_flag[@]}"
mios_ok "Rendered fedora-${_fver}.repo via miosd"

mios_step "Phase 1: pre-upgrade core systemd/filesystem"
$DNF_BIN "${DNF_SETOPT[@]}" upgrade -y --allowerasing --skip-unavailable \
    dnf rpm fedora-release fedora-repos filesystem systemd glibc dbus-broker 2>&1 || {
    mios_warn "Dnf upgrade of systemd/glibc/dbus-broker/filesystem returned non-zero; continuing"
}

_THIRD_PARTY_EXCLUDES="shim-*,kernel*,tailscale*,crowdsec*,crowdsec-firewall-bouncer*"

mios_step "Phase 2: distro-upgrade and userspace alignment"
$DNF_BIN "${DNF_SETOPT[@]}" \
    --setopt=excludepkgs="${_THIRD_PARTY_EXCLUDES}" \
    upgrade --refresh -y --skip-unavailable || {
    mios_warn "Upgrade"
}
_dsync_ok=0
for _attempt in 1 2; do
    if $DNF_BIN "${DNF_SETOPT[@]}" \
            --setopt=excludepkgs="${_THIRD_PARTY_EXCLUDES}" \
            distro-sync -y --allowerasing --skip-unavailable; then
        _dsync_ok=1; break
    fi
    mios_warn "Distro-sync attempt $_attempt failed"
    $DNF_BIN clean metadata 2>/dev/null || true
done
if [[ $_dsync_ok -eq 0 ]]; then
    mios_warn "Distro-sync failed after 2 attempts"
    mios_log "Continuing; individual package installs will use available repos"
fi

$DNF_BIN clean metadata 2>/dev/null || true

mios_log "Query installed versions of systemd glibc dbus-broker filesystem via rpm -q"
rpm -q systemd glibc dbus-broker filesystem || true

# Every image profile includes repos; the virt phase does not run in core.
# Install selected build and service dependencies before native-build (phase
# 55). Core also omits the browser-bake phase that otherwise installs ai.
mios_log "Install selected MiOS self-development dependencies"
for _build_section in containers build-toolchain self-build devcontainer ai utils; do
    install_packages_strict "$_build_section"
done
