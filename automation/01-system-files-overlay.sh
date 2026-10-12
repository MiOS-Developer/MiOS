#!/bin/bash
# MIOS_APPLY_CLASS=universal
# AI-hint: Overlay script that maps the /ctx/ source directory onto the rootfs during build, specifically handling the /usr/local overlay directory structure.
# AI-doc: usr/share/doc/mios/manual/automation.md
set -euo pipefail

# shellcheck source=/dev/null
for _mlog in "$(dirname "${BASH_SOURCE[0]}")/../usr/lib/mios/log.sh" /usr/lib/mios/log.sh; do [ -r "$_mlog" ] && . "$_mlog" && break; done

source "$(dirname "$0")/lib/common.sh"

CTX="${CTX:-/ctx}"

mios_step "Rootfs-native overlay"

if [[ -f "${CTX}/VERSION" ]]; then
    install -d -m 0755 /usr/share/mios
    install -m 0644 "${CTX}/VERSION" /usr/share/mios/VERSION
    mios_ok "Staged /usr/share/mios/VERSION -> $"
fi

if [[ -d "${CTX}/usr/share/mios/branding" ]]; then
    install -d -m 0755 /usr/share/pixmaps /usr/share/icons/hicolor/256x256/apps
    if [[ -f "${CTX}/usr/share/mios/branding/icon.png" ]]; then
        cp -f "${CTX}/usr/share/mios/branding/icon.png" /usr/share/pixmaps/mios.png
        cp -f "${CTX}/usr/share/mios/branding/icon.png" /usr/share/icons/hicolor/256x256/apps/mios.png
        mios_ok "Staged /usr/share/pixmaps/mios.png and /usr/share/icons/hicolor/256x256/apps/mios.png"
    fi
fi

for _vdir in "${CTX}/usr/share/mios/vendored" /usr/share/mios/vendored; do
    if [[ -d "$_vdir" && -w "$_vdir" ]]; then
        for _bundle in "$_vdir"/*.tar; do
            [[ -f "$_bundle" ]] || continue
            tar -xf "$_bundle" -C "$_vdir"
        done
    fi
done

if [[ -d "${CTX}/usr" ]]; then
    mios_log "Stage 1: overlay usr"
    tar -C "${CTX}/usr" -cf - --exclude='./local' . | tar -C /usr --no-overwrite-dir -xf -
    if [[ -d /usr/share/mios/vendored && -w /usr/share/mios/vendored ]]; then
        for _bundle in /usr/share/mios/vendored/*.tar; do
            [[ -f "$_bundle" ]] || continue
            tar -xf "$_bundle" -C /usr/share/mios/vendored
        done
    fi
fi

if [[ -d "${CTX}/usr/local" ]]; then
    mios_log "Stage 2: overlay /usr/local"
    if [[ -L /usr/local ]]; then
        local_target="$(readlink -f /usr/local 2>/dev/null || true)"
        mios_log "/usr/local symlink -> ${local_target}; skip /var write"
    else
        mios_log "/usr/local real directory; write directly"
        tar -C "${CTX}/usr/local" -cf - . | tar -C /usr/local --no-overwrite-dir -xf -
    fi
fi

if [[ -d "${CTX}/etc" ]]; then
    mios_log "Stage 3: overlay etc"
    tar -C "${CTX}/etc" -cf - --exclude='./containers/systemd' --exclude='./systemd' . | tar -C /etc --no-overwrite-dir -xf -
fi

if [[ -f "${CTX}/etc/wsl.conf" ]]; then
    tmp_wsl=$(mktemp)
    sed -e '1s/^\xEF\xBB\xBF//' -e 's/\r$//' "${CTX}/etc/wsl.conf" > "$tmp_wsl"
    install -m 0644 -o root -g root -T "$tmp_wsl" /etc/wsl.conf
    rm -f "$tmp_wsl"
    mios_ok "Stage 3a: force-installed /etc/wsl.conf"
fi
if [[ -f "${CTX}/usr/lib/wsl.conf" ]]; then
    tmp_wsl=$(mktemp)
    sed -e '1s/^\xEF\xBB\xBF//' -e 's/\r$//' "${CTX}/usr/lib/wsl.conf" > "$tmp_wsl"
    install -m 0644 -o root -g root -T "$tmp_wsl" /usr/lib/wsl.conf
    rm -f "$tmp_wsl"
    mios_ok "Stage 3a: force-installed /usr/lib/wsl.conf reference"
fi

if [[ -d "${CTX}/home" ]]; then
    mios_log "Stage 5: /ctx/home detected"
    install -d -m 0755 /etc/skel
    tar -C "${CTX}/home" -cf - . | tar -C /etc/skel --no-overwrite-dir --strip-components=1 -xf - 2>/dev/null || true
fi

if [[ -d "${CTX}/.dotfiles" ]]; then
    mios_log "Stage 5b: deploy .dotfiles to /usr/share/mios/dotfiles and /etc/skel"
    install -d -m 0755 /usr/share/mios/dotfiles
    cp -a "${CTX}/.dotfiles/." /usr/share/mios/dotfiles/
    if [[ -d "${CTX}/.dotfiles/vscode" ]]; then
        install -d -m 0755 /etc/skel/.vscode /etc/skel/.config/Code/User
        cp -f "${CTX}/.dotfiles/vscode/settings.json" /etc/skel/.vscode/settings.json 2>/dev/null || true
        cp -f "${CTX}/.dotfiles/vscode/settings.json" /etc/skel/.config/Code/User/settings.json 2>/dev/null || true
    fi
    if [[ -d "${CTX}/.dotfiles/code-server" ]]; then
        install -d -m 0755 /etc/skel/.local/share/code-server/User
        cp -f "${CTX}/.dotfiles/code-server/settings.json" /etc/skel/.local/share/code-server/User/settings.json 2>/dev/null || true
    fi
fi

mios_step "Normalize systemd file permissions"
find /usr/lib/systemd -type f \( -name "*.service" -o -name "*.socket" -o -name "*.timer" -o -name "*.mount" -o -name "*.conf" -o -name "*.target" -o -name "*.path" -o -name "*.slice" -o -name "*.preset" -o -name "*.automount" -o -name "*.swap" \) -exec chmod 644 {} \; 2>/dev/null || true
find /usr/lib/systemd -type d -exec chmod 755 {} \; 2>/dev/null || true

mios_step "Normalize udev/tmpfiles/sysusers/modprobe permissions"
for d in \
    /usr/lib/udev/rules.d \
    /usr/lib/tmpfiles.d \
    /usr/lib/sysusers.d \
    /usr/lib/modprobe.d \
    /usr/lib/sysctl.d \
    /usr/lib/binfmt.d \
    /etc/udev/rules.d \
    /etc/tmpfiles.d \
    /etc/sysusers.d \
    /etc/modprobe.d \
    /etc/sysctl.d
do
    [[ -d "$d" ]] || continue
    find "$d" -type f -exec chmod 0644 {} + 2>/dev/null || true
    find "$d" -type d -exec chmod 0755 {} + 2>/dev/null || true
done

_dev_net_mode="${MIOS_WSL2_DEV_VM_QUADLET_NETWORK_MODE:-host}"
if [[ "${_dev_net_mode}" == "bridge" ]]; then
    mios_log "[wsl2.dev_vm].quadlet_network_mode=bridge"
    shopt -s nullglob
    for d in /etc/containers/systemd/*.container.d/*-host-network.conf; do
        mios_log "Removed: $d"
        rm -f "$d"
    done
    shopt -u nullglob
fi

BDIR="/usr/lib/bootc/bound-images.d"
install -d -m 0755 "${BDIR}"
# Absolute path, never `command -v`: miosd installs to /usr/libexec/mios, which
# nothing puts on PATH at bake time, so the lookup this replaced could never
# succeed and the branch below it was dead on every build (task_962).
_miosd=""
for _c in "${MIOS_MIOSD_BIN:-}" \
          /usr/libexec/mios/miosd \
          "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/src/mios-rs/target/release/miosd" \
          "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/src/mios-rs/target/debug/miosd"; do
    if [[ -n "$_c" && -x "$_c" ]]; then _miosd="$_c"; break; fi
done

if [[ -z "$_miosd" ]]; then
    mios_err "Native miosd is required for overlay-bind-images (task_962)"
    exit 1
fi

MIOS_TOML="${MIOS_TOML:-/usr/share/mios/mios.toml}" "$_miosd" overlay-bind-images --dest "${BDIR}"
mios_ok "LBI binding completed via miosd"

mios_step "Pathing compatibility symlinks"

if [ ! -L /home ] && [ -d /home ] && [ ! "$(ls -A /home)" ]; then
    # shellcheck disable=SC2114  # guarded above: only an EMPTY, non-symlink /home,
    # which is the bootc layout's placeholder before it becomes /var/home
    rm -rf /home
    ln -sf /var/home /home
    mios_ok "Path: symlinked /home -> /var/home"
elif [ ! -e /home ]; then
    ln -sf /var/home /home
    mios_ok "Path: created /home -> /var/home symlink"
fi

if [[ -d /usr/libexec/mios ]]; then
    mios_log "Set executable bit on /usr/libexec/mios/*"
    find /usr/libexec/mios -type f -exec chmod +x {} + || true
fi

if [[ "${MIOS_INSTALL_MODE:-}" != "fhs" ]]; then
    if [[ ! -e "/usr/share/mios/k3s-manifests" ]]; then
        ln -sf "k3s/generated" "/usr/share/mios/k3s-manifests"
        mios_ok "Path: symlinked /usr/share/mios/k3s-manifests -> k3s/generated"
    fi
else
    if [[ -L "/usr/share/mios/k3s-manifests" ]]; then
        rm -f "/usr/share/mios/k3s-manifests"
    fi
    mkdir -p "/usr/share/mios/k3s-manifests"
fi

mios_step "Relabel overlaid files"
restorecon -RFv /usr/ 2>/dev/null || true
restorecon -RFv /etc/ 2>/dev/null || true

mios_ok "Overlay complete"
