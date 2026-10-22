#!/usr/bin/env bash
# AI-hint: bash mios-flatpaks CLI: operator-friendly wrapper over `flatpak` for the system-wide Flatpak surface (list/add/remove/update/search/bake-stat...
# AI-doc: usr/share/doc/mios/manual/mios.md
set -euo pipefail

if [ "$(id -u)" -eq 0 ]; then
    if [ -n "${XDG_RUNTIME_DIR:-}" ] && [ "$(stat -c %u "$XDG_RUNTIME_DIR" 2>/dev/null)" != "0" ]; then
        unset XDG_RUNTIME_DIR
        if [ -d /run/user/0 ]; then
            export XDG_RUNTIME_DIR=/run/user/0
        fi
    fi
fi

cmd="${1:-list}"
shift || true

case "$cmd" in
    list|ls)
        flatpak list --system --app --columns=application,version,branch,origin "$@"
        ;;
    add|install)
        if [[ -z "${1:-}" ]]; then
            echo "Usage: mios-flatpaks add [remote] <ref>... OR mios-flatpaks add <remote:ref>..." >&2
            exit 2
        fi
        if [[ $EUID -ne 0 ]]; then exec sudo -E "$0" add "$@"; fi
        if [[ "$#" -gt 1 ]] && flatpak remotes --columns=name 2>/dev/null | grep -qx "${1}"; then
            local_remote="$1"
            shift
            flatpak install --system --noninteractive --assumeyes "$local_remote" "$@"
        else
            for arg in "$@"; do
                if [[ "$arg" == *:* ]]; then
                    r="${arg%%:*}"
                    pkg="${arg#*:}"
                    flatpak install --system --noninteractive --assumeyes "$r" "$pkg"
                elif [[ -x /usr/libexec/mios/mios-flatpak-install ]]; then
                    /usr/libexec/mios/mios-flatpak-install "$arg"
                else
                    flatpak install --system --noninteractive --assumeyes flathub "$arg"
                fi
            done
        fi
        ;;
    remove|uninstall|rm)
        if [[ -z "${1:-}" ]]; then
            echo "Mios-flatpaks remove <ref>" >&2
            exit 2
        fi
        if [[ $EUID -ne 0 ]]; then exec sudo -E "$0" remove "$@"; fi
        args=()
        for a in "$@"; do
            if [[ "$a" == *:* ]]; then
                args+=("${a#*:}")
            else
                args+=("$a")
            fi
        done
        flatpak uninstall --system --noninteractive --assumeyes "${args[@]}"
        ;;
    update|upgrade)
        if [[ $EUID -ne 0 ]]; then exec sudo -E "$0" update "$@"; fi
        flatpak update --system --noninteractive --assumeyes "$@"
        ;;
    search)
        if [[ -z "${1:-}" ]]; then
            echo "Mios-flatpaks search <term>" >&2
            exit 2
        fi
        flatpak search "$@"
        ;;
    bake-state)
        if [[ -r /usr/lib/mios/state/flatpak-bake.env ]]; then
            cat /usr/lib/mios/state/flatpak-bake.env
        else
            echo "No bake state recorded"
        fi
        ;;
    --help|-h|help)
        sed -n '2,15p' "$0" | sed 's/^# \?//'
        ;;
    *)
        echo "Mios-flatpaks: unknown verb '$cmd'" >&2
        exit 2
        ;;
esac
