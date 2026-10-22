#!/bin/bash
# AI-hint: Enumerate every visible .desktop entry inside the WSL distro
set -euo pipefail
shopt -s nullglob

declare -A _seen

for f in /var/lib/flatpak/exports/share/applications/*.desktop \
         /usr/share/applications/*.desktop \
         /var/home/*/.local/share/applications/*.desktop; do
    [ -e "$f" ] || continue
    base="$(basename "$f")"
    [ -n "${_seen[$base]:-}" ] && continue
    _seen[$base]=1

    name=$(sed -nE 's/^Name=(.*)$/\1/p' "$f" | head -1)
    exec=$(sed -nE 's/^Exec=(.*)$/\1/p' "$f" | head -1)
    icon=$(sed -nE 's/^Icon=(.*)$/\1/p' "$f" | head -1)
    nodisp=$(sed -nE 's/^NoDisplay=(.*)$/\1/p' "$f" | head -1)
    term=$(sed -nE 's/^Terminal=(.*)$/\1/p' "$f" | head -1)
    cat=$(sed -nE 's/^Categories=(.*)$/\1/p' "$f" | head -1)
    [ -z "$name" ] && continue
    [ "$nodisp" = "true" ] && continue
    printf '%s|%s|%s|%s|%s|%s|%s\n' "$name" "$exec" "$icon" "${nodisp:-false}" "${term:-false}" "$cat" "$f"
done
