#!/bin/bash
# AI-hint: Private camera lab for tests/fixtures/mios-rdp/camera.py inside ONE nested MiOS session runtime R: its own D-Bus bus, PipeWire, WirePlumber with every hardware monitor disabled (no ALSA, V4L2, libcamera or Bluetooth device is opened) and xdg-desktop-portal (Camera portal for picker probes and OBS). The lab's own permission store holds the "allow camera" answer for host apps (the lab has no Access-dialog backend). Nothing uses the operator's runtime dir, bus or PipeWire graph.
# AI-related: tests/fixtures/mios-rdp/camera.py, tests/fixtures/mios-rdp/obs.py, usr/share/doc/mios/remote-camera.md, tests/test-document-portal-isolation.py
# AI-functions: main, lab_env, up, down

set -euo pipefail

usage() {
    cat >&2 <<'EOF'
Usage: camlab.sh up R | down R | env R [VAR=val ...] command...
  R is a live nested session runtime, /run/user/<uid>/mios-nested-desktop-<pid>
  (from `mios-gen nested-session`'s session.json). Lab state lives in R/camlab-state.
EOF
    exit 2
}

# Every lab process gets this explicit environment and nothing inherited: a bus or
# portal started with the operator's XDG_RUNTIME_DIR would replace the operator's
# document portal (see tests/test-document-portal-isolation.py).
lab_env() {
    local state=$R/camlab-state
    LAB=(env -i "HOME=$state/home" PATH=/usr/bin:/bin LANG=C.UTF-8 "XDG_RUNTIME_DIR=$R" "PIPEWIRE_RUNTIME_DIR=$R"
         "DBUS_SESSION_BUS_ADDRESS=unix:path=$R/camlab-bus" "XDG_CONFIG_HOME=$state/config" "XDG_STATE_HOME=$state/state"
         "XDG_DATA_HOME=$state/data" "XDG_CACHE_HOME=$state/cache" XDG_CURRENT_DESKTOP=mios-camlab)
}

up() {
    local state=$R/camlab-state
    [[ ! -e $R/camlab.pids ]] || { echo "lab already up in $R" >&2; exit 1; }
    mkdir -p "$state"/{home,state,data,cache} "$state/config/wireplumber/wireplumber.conf.d"
    cat > "$state/config/wireplumber/wireplumber.conf.d/50-camlab.conf" <<'EOF'
wireplumber.profiles = {
  main = {
    monitor.alsa = disabled
    monitor.alsa-midi = disabled
    monitor.bluez = disabled
    monitor.bluez-midi = disabled
    monitor.bluez.seat-monitoring = disabled
    monitor.v4l2 = disabled
    monitor.libcamera = disabled
  }
}
EOF
    : > "$R/camlab.pids"
    "${LAB[@]}" setsid dbus-daemon --session --nofork --nopidfile "--address=unix:path=$R/camlab-bus" >"$R/camlab-dbus.log" 2>&1 &
    echo $! >> "$R/camlab.pids"
    for _ in $(seq 50); do [[ -S $R/camlab-bus ]] && break; sleep 0.1; done
    "${LAB[@]}" setsid pipewire >"$R/camlab-pw.log" 2>&1 &
    echo $! >> "$R/camlab.pids"
    for _ in $(seq 50); do [[ -S $R/pipewire-0 ]] && break; sleep 0.1; done
    "${LAB[@]}" setsid wireplumber >"$R/camlab-wp.log" 2>&1 &
    echo $! >> "$R/camlab.pids"
    sleep 1
    "${LAB[@]}" setsid /usr/libexec/xdg-desktop-portal >"$R/camlab-xdp.log" 2>&1 &
    echo $! >> "$R/camlab.pids"
    sleep 2
    # The operator's "Allow camera" answer for host (unsandboxed) apps, in the lab's own
    # permission store; without it OBS's AccessCamera is denied (no dialog backend here).
    "${LAB[@]}" busctl --user call org.freedesktop.impl.portal.PermissionStore /org/freedesktop/impl/portal/PermissionStore \
        org.freedesktop.impl.portal.PermissionStore SetPermission sbssas devices true camera "" 1 yes
    tr '\n' ' ' < "$R/camlab.pids"
    echo
}

down() {
    local p
    if [[ -f $R/camlab.pids ]]; then
        while read -r p; do kill "$p" 2>/dev/null || true; done < "$R/camlab.pids"
    fi
    sleep 1
    # Bus-activated helpers (permission store, document portal, gvfs, keyring) carry the lab
    # bus address AND the lab runtime dir; nothing else is touched.
    for p in $(pgrep -u "$(id -u)" || true); do
        tr '\0' '\n' 2>/dev/null < "/proc/$p/environ" | grep -qE "^DBUS_SESSION_BUS_ADDRESS=unix:path=$R/camlab-bus(,|$)" || continue
        [[ $(tr '\0' '\n' 2>/dev/null < "/proc/$p/environ" | sed -n 's/^XDG_RUNTIME_DIR=//p') == "$R" ]] && kill "$p" 2>/dev/null || true
    done
    sleep 1
    for p in doc gvfs; do
        if mountpoint -q "$R/$p" 2>/dev/null; then fusermount3 -u "$R/$p" || true; fi
    done
    rm -f "$R/camlab.pids"
}

main() {
    [[ $# -ge 2 ]] || usage
    local cmd=$1
    R=$2
    shift 2
    [[ $R == /run/user/$(id -u)/mios-nested-desktop-* && -d $R ]] || { echo "not a nested runtime: $R" >&2; exit 2; }
    [[ $(readlink -f "$R") != "/run/user/$(id -u)" ]] || exit 2
    lab_env
    case $cmd in
        up) up ;;
        down) down ;;
        env)
            local extra=()
            while [[ $# -gt 0 && $1 == *=* ]]; do extra+=("$1"); shift; done
            [[ $# -gt 0 ]] || usage
            exec "${LAB[@]}" "${extra[@]}" "$@"
            ;;
        *) usage ;;
    esac
}

main "$@"
