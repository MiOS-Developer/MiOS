#!/bin/bash
# MIOS_APPLY_CLASS=universal
# AI-hint: Installs operator-selected Flatpaks into the system image during the build process to ensure the final deployment (ISO, VHD...
# AI-doc: usr/share/doc/mios/manual/automation.md
set -euo pipefail
# shellcheck source=usr/lib/mios/log.sh
for _mlog in "$(dirname "${BASH_SOURCE[0]}")/../usr/lib/mios/log.sh" /usr/lib/mios/log.sh; do [ -r "$_mlog" ] && . "$_mlog" && break; done
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Resolver defaults must not replace an explicit build-argument selection.
_selected_flatpaks="${MIOS_DESKTOP_FLATPAKS-}"
_selected_flatpaks_set="${MIOS_DESKTOP_FLATPAKS+x}"
source "${SCRIPT_DIR}/lib/common.sh"

FLATPAK_LIST="${MIOS_DESKTOP_FLATPAKS:-}"
if [[ -n "$_selected_flatpaks_set" ]]; then
    FLATPAK_LIST="$_selected_flatpaks"
    export MIOS_DESKTOP_FLATPAKS="$FLATPAK_LIST"
elif [[ -r /tmp/build/usr/share/mios/flatpak-list ]]; then
    FLATPAK_LIST="$(tr '\n' ',' < /tmp/build/usr/share/mios/flatpak-list | sed 's/,*$//')"
fi
if [[ -z "$FLATPAK_LIST" && -z "$_selected_flatpaks_set" ]] && [[ -r /tmp/build/mios.toml ]]; then
    FLATPAK_LIST="$(awk '/^\[desktop\]/,/^\[/{ if ($0 ~ /^\[desktop\]/) next; if ($0 ~ /^\[/) exit; print }' \
                   /tmp/build/mios.toml \
        | grep -oE '"[^"]+"' \
        | tr -d '"' \
        | grep -E '^[A-Za-z][A-Za-z0-9_-]*(\.[A-Za-z][A-Za-z0-9_-]*){2,}$' \
        | tr '\n' ',' \
        | sed 's/,*$//')"
fi

if [[ -z "${FLATPAK_LIST// /}" ]]; then
    mios_skip "no Flatpaks selected (mios.toml [desktop].flatpaks empty)"
    exit 0
fi

if ! command -v flatpak >/dev/null 2>&1; then
    mios_err "Selected Flatpaks require the Flatpak binary"
    exit 1
fi

# Fedora's OCI remote needs a session bus even in a headless image build.
# Always use a private, live bus rather than inheriting a stale desktop address.
if [[ "${1:-}" != --mios-flatpak-session ]]; then
    command -v dbus-run-session >/dev/null 2>&1 || {
        mios_err "Selected Flatpaks require dbus-run-session (dbus-daemon)"
        exit 1
    }
    exec dbus-run-session -- bash "$0" --mios-flatpak-session "$@"
fi
shift

mios_log "Selected refs: ${FLATPAK_LIST}"
mios_log "System-wide install"

INSTALLED=0
FAILED=0
IFS=',' read -ra REFS <<< "$FLATPAK_LIST"
for raw in "${REFS[@]}"; do
    ref="$(echo "$raw" | xargs)"
    [[ -z "$ref" ]] && continue

    case "$ref" in
        \#*) continue ;;
    esac

    case "$ref" in
        *:*)
            remote="${ref%%:*}"
            app="${ref#*:}"
            ;;
        *)
            remote="flathub"
            app="$ref"
            ;;
    esac

    if ! flatpak remote-list --system --columns=name | grep -Fxq -- "$remote"; then
        case "$remote" in
            flathub)
                flatpak remote-add --system --if-not-exists flathub \
                    https://dl.flathub.org/repo/flathub.flatpakrepo ;;
            flathub-beta)
                flatpak remote-add --system --if-not-exists flathub-beta \
                    https://flathub.org/beta-repo/flathub-beta.flatpakrepo ;;
            gnome-nightly)
                flatpak remote-add --system --if-not-exists gnome-nightly \
                    https://nightly.gnome.org/gnome-nightly.flatpakrepo ;;
            fedora)
                flatpak remote-add --system --if-not-exists fedora \
                    oci+https://registry.fedoraproject.org ;;
            *)
                mios_err "Unknown remote '$remote' for $ref"
                exit 1 ;;
        esac
    fi

    local_flatpak=""
    if [ -f "${MIOS_SHARE_DIR}/vendored/${app}.flatpak" ]; then
        local_flatpak="${MIOS_SHARE_DIR}/vendored/${app}.flatpak"
    fi

    mios_log "Installing ${app}"
    if [ -n "$local_flatpak" ]; then
        mios_log "Offline vendored flatpak file: ${local_flatpak}"
        install_cmd=(flatpak install --system --noninteractive --assumeyes --or-update "$local_flatpak")
    else
        install_cmd=(flatpak install --system --noninteractive --assumeyes --or-update "$remote" "$app")
    fi

    set +e
    install_out=$("${install_cmd[@]}" 2>&1)
    install_status=$?
    set -e

    if [[ -n "$install_out" ]]; then
        # apply_extra's preceding stderr contains the cause; keep it intact.
        printf '%s\n' "$install_out"
    fi

    if [[ $install_status -eq 0 ]]; then
        INSTALLED=$((INSTALLED + 1))
    else
        FAILED=$((FAILED + 1))
        mios_warn "${remote}:${app} install returned non-zero"
    fi
done

if (( FAILED > 0 )); then
    mios_err "${INSTALLED} refs installed, ${FAILED} failed"
else
    mios_ok "${INSTALLED} refs installed, 0 failed"
fi

install -d -m 0755 "${MIOS_USR_DIR}/state"
{
    printf 'MIOS_FLATPAK_BAKE_DATE=%s\n' "$(date -u +%FT%TZ)"
    printf 'MIOS_FLATPAK_BAKE_INSTALLED=%d\n' "$INSTALLED"
    printf 'MIOS_FLATPAK_BAKE_FAILED=%d\n'    "$FAILED"
    printf 'MIOS_FLATPAK_BAKE_LIST=%q\n'      "$FLATPAK_LIST"
} > "${MIOS_USR_DIR}/state/flatpak-bake.env"
chmod 0644 "${MIOS_USR_DIR}/state/flatpak-bake.env"

(( FAILED == 0 ))
