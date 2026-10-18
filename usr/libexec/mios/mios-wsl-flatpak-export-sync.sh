#!/usr/bin/env bash
# AI-hint: Mirror flatpak's `.desktop` + icon exports into the system XDG dirs so
# AI-related: /usr/libexec/mios/mios-wsl-flatpak-export-sync.sh, mios-wsl-flatpak-export-sync, mios-wsl-flatpak-export-sync.service
# AI-functions: _log

set -euo pipefail

_log() { echo "[wsl-flatpak-export-sync] $*" >&2; }

FLATPAK_APPS=/var/lib/flatpak/exports/share/applications
FLATPAK_ICONS=/var/lib/flatpak/exports/share/icons
SYS_APPS=/usr/share/applications
SYS_ICONS=/usr/share/icons

if [[ ! -d "$FLATPAK_APPS" ]]; then
    _log "no flatpak exports dir at $FLATPAK_APPS -- skipping"
    exit 0
fi

install -d -m 0755 "$SYS_APPS" "$SYS_ICONS"

mirrored=0
for src in "$FLATPAK_APPS"/*.desktop; do
    [[ -f "$src" ]] || continue
    base="$(basename "$src")"
    dst="$SYS_APPS/$base"
    if [[ -e "$dst" && ! -L "$dst" ]]; then
        continue
    fi
    if [[ -L "$dst" && "$(readlink "$dst")" == "$src" ]]; then
        continue
    fi
    ln -sf "$src" "$dst"
    mirrored=$((mirrored + 1))
done
_log "mirrored $mirrored flatpak .desktop entries -> $SYS_APPS"

if [[ -d "$FLATPAK_ICONS/hicolor" ]]; then
    install -d -m 0755 "$SYS_ICONS/hicolor"
    icons_mirrored=0
    while IFS= read -r -d '' iconfile; do
        rel="${iconfile#$FLATPAK_ICONS/}"     # e.g. "hicolor/scalable/apps/com.google.ChromeDev.svg"
        dst="$SYS_ICONS/$rel"
        if [[ -e "$dst" && ! -L "$dst" ]]; then continue; fi
        if [[ -L "$dst" && "$(readlink "$dst")" == "$iconfile" ]]; then continue; fi
        install -d -m 0755 "$(dirname "$dst")"
        ln -sf "$iconfile" "$dst"
        icons_mirrored=$((icons_mirrored + 1))
    done < <(find -L "$FLATPAK_ICONS/hicolor" -type f \( -name '*.png' -o -name '*.svg' -o -name '*.xpm' \) -print0)
    _log "mirrored $icons_mirrored flatpak icon files -> $SYS_ICONS/hicolor"
fi

gc=0
for link in "$SYS_APPS"/*.desktop; do
    [[ -L "$link" ]] || continue
    target="$(readlink "$link")"
    if [[ "$target" == "$FLATPAK_APPS/"* && ! -e "$target" ]]; then
        rm -f "$link"
        gc=$((gc + 1))
    fi
done
[[ $gc -gt 0 ]] && _log "GC: removed $gc stale flatpak .desktop links"

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database -q "$SYS_APPS" || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q -t -f "$SYS_ICONS/hicolor" 2>/dev/null || true
fi

_log "done"
