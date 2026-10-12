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

BIBATA_VER=$( (scurl -sL --connect-timeout 15 --max-time 30 \
    -H "Accept: application/vnd.github+json" "${MIOS_URL_BIBATA_API:-https://api.github.com/repos/ful1e5/Bibata_Cursor/releases/latest}" \
    | grep -m1 '"tag_name"' | sed 's/.*"v\?\([^"]*\)".*/\1/') 2>/dev/null || true)

[[ -n "$BIBATA_VER" ]] || die "Bibata: api.github.com release-latest lookup returned empty"
record_version bibata "v${BIBATA_VER}" "https://github.com/ful1e5/Bibata_Cursor/releases/tag/v${BIBATA_VER}"

_bibata_dl_default="https://github.com/ful1e5/Bibata_Cursor/releases/download/v{}/Bibata-Modern-Classic.tar.xz"
BIBATA_URL="${MIOS_URL_BIBATA_DL:-$_bibata_dl_default}"
BIBATA_URL="${BIBATA_URL//"{}"/${BIBATA_VER}}"
BIBATA_DIR="/usr/share/icons/Bibata-Modern-Classic"
mkdir -p /usr/share/icons

BIBATA_OK=0
_bibata_sum_default="https://github.com/ful1e5/Bibata_Cursor/releases/download/v{}/sha256-{}.txt"
BIBATA_SUM_URL="${MIOS_URL_BIBATA_SUM:-$_bibata_sum_default}"
BIBATA_SUM_URL="${BIBATA_SUM_URL//"{}"/${BIBATA_VER}}"

if [ -f "/usr/share/mios/vendored/cursors/bibata.tar.xz" ]; then
    mios_log "Found offline vendored bibata.tar.xz, extracting"
    if tar -xf "/usr/share/mios/vendored/cursors/bibata.tar.xz" -C /usr/share/icons/; then
        BIBATA_OK=1
    fi
else
    for attempt in 1 2 3; do
        mios_log "Download attempt $attempt/3"
        if scurl -fSL --connect-timeout 20 --max-time 120 --retry 2 --retry-delay 5 "$BIBATA_URL" -o /tmp/bibata.tar.xz; then
        if scurl -fsSL --connect-timeout 15 --max-time 30 "$BIBATA_SUM_URL" -o /tmp/bibata.sha256 2>/dev/null; then
            if (cd /tmp && grep "Bibata-Modern-Classic.tar.xz" bibata.sha256 | sha256sum -c -) 2>/dev/null; then
                mios_ok "Bibata sha256 verified"
            else
                mios_warn "Bibata sha256 mismatch or sidecar format mismatch"
            fi
            rm -f /tmp/bibata.sha256
        else
            mios_warn "Bibata sha256 sidecar unavailable"
        fi
        if tar -xf /tmp/bibata.tar.xz -C /usr/share/icons/; then
            sbom_dir="/usr/share/mios/artifacts/sbom"
            mkdir -p "$sbom_dir"
            sha=""
            if command -v sha256sum >/dev/null 2>&1; then
                sha="$(sha256sum /tmp/bibata.tar.xz | awk '{print $1}')"
            fi
            printf '%s\t%s\t%s\n' "Bibata-Modern-Classic" "${BIBATA_VER}" "${sha:-unknown}" >> "${sbom_dir}/binaries.tsv"

            rm -f /tmp/bibata.tar.xz
            BIBATA_OK=1
            break
        fi
    fi
        mios_warn "Attempt $attempt failed, retrying"
        sleep 5
    done
fi

if [ "$BIBATA_OK" -eq 0 ] || [ ! -d "$BIBATA_DIR/cursors" ]; then
    die "Bibata cursor download FAILED after 3 attempts"
fi
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
