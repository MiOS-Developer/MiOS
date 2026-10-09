# AI-hint: Configures Wayland/X11 environment variables (DISPLAY, WAYLAND_DISPLAY, XDG_RUNTIME_DIR) and PulseAudio paths specifically for WSLg integration to ensure GUI applic...
# AI-doc: usr/share/doc/mios/manual/profile.d.md
# shellcheck shell=sh

[ -d /mnt/wslg ] || return 0

if [ -d "/run/user/$(id -u)" ]; then
    export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
    case "$XDG_RUNTIME_DIR" in
        /mnt/wslg/*)
            XDG_RUNTIME_DIR="/run/user/$(id -u)"
            export XDG_RUNTIME_DIR
            ;;
    esac
else
    export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/mnt/wslg/runtime-dir}"
fi
export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}"
export DISPLAY="${DISPLAY:-:0}"
export PULSE_SERVER="${PULSE_SERVER:-/mnt/wslg/PulseServer}"
export XDG_SESSION_TYPE=wayland

export XDG_CURRENT_DESKTOP="${XDG_CURRENT_DESKTOP:-GNOME}"

if [ -z "${DBUS_SESSION_BUS_ADDRESS:-}" ] && [ -S "/run/user/$(id -u)/bus" ]; then
    DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$(id -u)/bus"
    export DBUS_SESSION_BUS_ADDRESS
fi

if [ -z "${WSL_INTEROP:-}" ]; then
    _wsl_interop="$(find /run/WSL/ -maxdepth 1 -name '*_interop' 2>/dev/null | head -n1)"
    [ -n "$_wsl_interop" ] && export WSL_INTEROP="$_wsl_interop"
    unset _wsl_interop
fi

if [ -z "${GDK_BACKEND:-}" ]; then
    export GDK_BACKEND="${MIOS_WSL2_DESKTOP_COMPAT_GDK_BACKEND:-x11}"
fi
if [ -z "${MOZ_ENABLE_WAYLAND:-}" ]; then
    export MOZ_ENABLE_WAYLAND="${MIOS_WSL2_DESKTOP_COMPAT_MOZ_WAYLAND:-0}"
fi
if [ -z "${QT_QPA_PLATFORM:-}" ]; then
    export QT_QPA_PLATFORM="${MIOS_WSL2_DESKTOP_COMPAT_QT_PLATFORM:-xcb}"
fi

if command -v mios-cursor-apply >/dev/null 2>&1; then
    (mios-cursor-apply >/dev/null 2>&1 &) 2>/dev/null || true
fi
