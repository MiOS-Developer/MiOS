#!/bin/bash
# MIOS_APPLY_CLASS=universal
# AI-hint: Installs the core GNOME 50 desktop environment, including GDM, Wayland portals, and theme consistency for GTK/Qt, while configurin...
# AI-doc: usr/share/doc/mios/manual/automation.md
set -euo pipefail
# The log helper is resolved in checkout and installed layouts.
# shellcheck disable=SC1090
for _mlog in "$(dirname "${BASH_SOURCE[0]}")/../usr/lib/mios/log.sh" /usr/lib/mios/log.sh; do [ -r "$_mlog" ] && . "$_mlog" && break; done
source "$(dirname "$0")/lib/common.sh"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/packages.sh"

mios_log "Install GNOME 50 packages from mios.toml [packages.gnome]"
install_packages "gnome"

install_packages_optional "gnome-core-apps"

mios_log "Localsearch/tracker indexing disabled via static autostart override files in the usr/share/xdg/autostart/ overlay"

mios_log "Qt Adwaita theming provided by usr/lib/environment.d/60-mios-qt-adwaita.conf overlay"

mios_log "Install Bibata-Modern-Classic cursor"

# [build.pins.bibata]: the newest release, proven by GitHub's digest; when that is
# the pinned release, by the pinned sha256 (the vendored copy, offline). An empty
# upstream answer bakes the pin by name; an integrity mismatch aborts (exit 3).
# The upstream tarball's files are uid 1000; as root, plain tar would keep that owner on /usr.
# shellcheck source=automation/lib/upstream-latest.sh
source "${SCRIPT_DIR}/lib/upstream-latest.sh"
BIBATA_DIR="/usr/share/icons/Bibata-Modern-Classic"
_bibata_dl="$(mktemp -d)"
mios_pinned_asset bibata latest "$_bibata_dl" Bibata \
    || die "Bibata: no verified Bibata-Modern-Classic.tar.xz (see the MIOS-PIN error above)"
mkdir -p /usr/share/icons
tar -xf "$mios_pin_file" -C /usr/share/icons/ --no-same-owner \
    || die "Bibata: ${mios_pin_file##*/} ${mios_pin_version} did not extract"
rm -rf -- "$_bibata_dl"
[ -d "$BIBATA_DIR/cursors" ] || die "Bibata: ${mios_pin_version} carries no Bibata-Modern-Classic/cursors"
mios_record_resolved Bibata-Modern-Classic "v${mios_pin_version}" \
    "${mios_pin_url} (${mios_pin_source})" "$mios_pin_sha"
mios_ok "Bibata cursor installed: $(find "$BIBATA_DIR/cursors/" -mindepth 1 -maxdepth 1 | wc -l) cursors"

if [ -d "$BIBATA_DIR/cursors" ]; then
    update-alternatives --install /usr/share/icons/default/index.theme \
        x-cursor-theme /usr/share/icons/Bibata-Modern-Classic/cursor.theme 100 2>/dev/null || true
    mios_ok "X-cursor-theme alternative set to Bibata"
fi

mkdir -p /usr/share/cursors/xorg-x11
ln -sf /usr/share/icons/Bibata-Modern-Classic /usr/share/cursors/xorg-x11/Bibata-Modern-Classic 2>/dev/null || true

chmod -R a+rX "$BIBATA_DIR" 2>/dev/null || true

mios_log "Install Phosh mobile session"
install_packages_optional "phosh"
chmod +x /usr/local/bin/phosh-session-wrapper 2>/dev/null || true
mios_log "Configure Flatpak remotes"
if command -v flatpak &>/dev/null; then
    if [[ "${MIOS_ONLINE_BUILD:-0}" == "1" ]]; then
        flatpak remote-add --system --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo || true
        flatpak remote-add --system --if-not-exists flathub-beta https://flathub.org/beta-repo/flathub-beta.flatpakrepo || true
        flatpak remote-add --system --if-not-exists gnome-nightly https://nightly.gnome.org/gnome-nightly.flatpakrepo 2>/dev/null || true
    else
        mios_log "Offline build: skipping flatpak remote-add, assuming OCI baked archives"
    fi
    flatpak remote-modify --system --disable fedora 2>/dev/null || true
else
    mios_warn "Flatpak binary not found, skipping remote configuration"
fi

mios_log "Flatpaks installed on first boot"

mios_log "Project GTK palette, font and cursor defaults from layered SSOT"
python3 /usr/libexec/mios/mios-theme-render --gtk --config-root /etc/skel/.config

exit 0
