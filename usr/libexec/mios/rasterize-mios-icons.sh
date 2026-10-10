#!/bin/bash
# AI-hint: Read icon names on stdin, resolve each to a real file inside the
# AI-related: mios-icon-stage
# AI-functions: resolve_icon
set -euo pipefail

TARGET_DIR=/tmp/mios-icon-stage
mkdir -p "$TARGET_DIR"

resolve_icon() {
    local name="$1"
    if [[ "$name" == /* ]] && [ -e "$name" ]; then echo "$name"; return; fi
    for sz in 512x512 256x256 128x128 64x64 48x48 scalable 32x32 24x24 16x16; do
        for base in /usr/share/icons/hicolor /var/lib/flatpak/exports/share/icons/hicolor; do
            for ext in png svg xpm; do
                f="$base/$sz/apps/$name.$ext"
                [ -e "$f" ] && { echo "$f"; return; }
            done
        done
    done
    for f in "/usr/share/pixmaps/$name.png" "/usr/share/pixmaps/$name.svg" "/usr/share/pixmaps/$name"; do
        [ -e "$f" ] && { echo "$f"; return; }
    done
    [ -e /usr/share/mios/branding/icon.png ] && { echo /usr/share/mios/branding/icon.png; return; }
    return 1
}

while IFS= read -r name; do
    [ -z "$name" ] && continue
    src=$(resolve_icon "$name")
    if [ -z "$src" ]; then
        printf '%s|MISSING\n' "$name"
        continue
    fi
    out="$TARGET_DIR/$name.png"
    mime=$(file -Lb --mime-type "$src" 2>/dev/null)
    case "$mime" in
        image/svg+xml)
            if command -v rsvg-convert >/dev/null 2>&1; then
                rsvg-convert -w 256 -h 256 -o "$out" "$src" 2>/dev/null || cp -f "$src" "$out"
            elif command -v magick >/dev/null 2>&1; then
                magick -background none -density 256 "$src" -resize 256x256 "$out" 2>/dev/null || cp -f "$src" "$out"
            elif command -v convert >/dev/null 2>&1; then
                convert -background none -density 256 "$src" -resize 256x256 "$out" 2>/dev/null || cp -f "$src" "$out"
            else
                cp -f "$src" "$out"
            fi
            ;;
        image/png|image/jpeg|image/x-icon|image/vnd.microsoft.icon)
            cp -f "$src" "$out"
            ;;
        image/x-xpixmap|image/x-pixmap)
            command -v convert >/dev/null 2>&1 && convert "$src" "$out" 2>/dev/null || cp -f "$src" "$out"
            ;;
        *)
            cp -f "$src" "$out" 2>/dev/null
            ;;
    esac
    if [ -s "$out" ]; then
        printf '%s|%s\n' "$name" "$out"
    else
        printf '%s|FAILED\n' "$name"
    fi
done
