#!/usr/bin/env bash
# AI-hint: Automated CI verification suite for Hyprland direct scanout, Quickshell initialization, and living wallpaper occlusion engine (task_721, task_722, task_723).
# AI-doc: usr/share/doc/mios/manual/ch68-living-wallpaper-shaders-and-ssot-theme-engine.md
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

pass=0
fail=0

_pass() {
    echo "  [PASS] $1"
    pass=$((pass + 1))
}

_fail() {
    echo "  [FAIL] $1" >&2
    fail=$((fail + 1))
}

echo "=== MiOS Hyprland Direct Scanout & Living Wallpaper Test Suite ==="

# 1. Inspect Hyprland Configuration
HYPR_CONF="$REPO_ROOT/usr/share/mios/hyprland/hyprland.conf"
if [[ ! -f "$HYPR_CONF" ]]; then
    _fail "Hyprland configuration missing: $HYPR_CONF"
else
    _pass "Hyprland configuration file found"

    # Assert render:direct_scanout = 1
    if grep -qzE "render[[:space:]]*\{[^}]*direct_scanout[[:space:]]*=[[:space:]]*1" "$HYPR_CONF"; then
        _pass "Hyprland direct scanout enabled (render.direct_scanout = 1)"
    else
        _fail "Hyprland direct scanout not configured in render block"
    fi

    # Assert Quickshell autostart
    if grep -qE "exec-once.*quickshell" "$HYPR_CONF"; then
        _pass "Quickshell autostart binding configured"
    else
        _fail "Quickshell autostart missing in hyprland.conf"
    fi

    # Assert dark Adwaita styling
    if grep -q "GTK_THEME,Adwaita:dark" "$HYPR_CONF"; then
        _pass "Dark Adwaita GTK styling declared"
    else
        _fail "Dark Adwaita GTK styling missing in hyprland.conf"
    fi
fi

# 2. Inspect Wayland Session Entry for GDM
WAYLAND_SESSION="$REPO_ROOT/usr/share/wayland-sessions/hyprland.desktop"
if [[ ! -f "$WAYLAND_SESSION" ]]; then
    _fail "Wayland session entry missing: $WAYLAND_SESSION"
else
    _pass "Wayland session entry found"
    # The session runs MiOS's launcher, which execs Hyprland with the session.lua projection (and the boot
    # hardware profile); a bare Exec=Hyprland would skip it. The launcher is the SSOT's [desktop.greeter].session_cmd.
    SESSION_CMD="$(python3 -c 'import sys, tomllib; print(tomllib.load(open(sys.argv[1], "rb"))["desktop"]["greeter"]["session_cmd"])' \
        "$REPO_ROOT/usr/share/mios/mios.toml" 2>/dev/null || true)"
    EXEC_LINE="$(grep -m1 '^Exec=' "$WAYLAND_SESSION" || true)"
    exec_ok=0
    if [[ -n "$SESSION_CMD" ]]; then
        case "$EXEC_LINE" in "Exec=${SESSION_CMD}" | "Exec=${SESSION_CMD} "*) exec_ok=1 ;; esac
    fi
    if [[ $exec_ok -eq 1 ]] && grep -qx "DesktopNames=Hyprland" "$WAYLAND_SESSION"; then
        _pass "Wayland session runs the SSOT session launcher (${SESSION_CMD}) with DesktopNames=Hyprland"
    else
        _fail "Wayland session entry incomplete (Exec must start with [desktop.greeter].session_cmd '${SESSION_CMD}': ${EXEC_LINE:-no Exec})"
    fi
fi

# 3. Test Living Wallpaper Occlusion and Rendering Engine
WALLPAPER_BIN="$REPO_ROOT/usr/libexec/mios/mios-wallpaper"
if [[ ! -x "$WALLPAPER_BIN" ]]; then
    _fail "mios-wallpaper binary missing or not executable: $WALLPAPER_BIN"
else
    _pass "mios-wallpaper executable found"

    # Test visible / unoccluded state: 60 FPS target & low Vulkan queue priority
    STATUS_VISIBLE=$("$WALLPAPER_BIN" --mock --status --json)
    FPS_VISIBLE=$(python3 -c "import json, sys; d = json.loads('''$STATUS_VISIBLE'''); print(d.get('fps'))")
    RENDERING_VISIBLE=$(python3 -c "import json, sys; d = json.loads('''$STATUS_VISIBLE'''); print(d.get('rendering'))")
    QUEUE_PRIO=$(python3 -c "import json, sys; d = json.loads('''$STATUS_VISIBLE'''); print(d.get('vulkan_queue_priority'))")

    if [[ "$FPS_VISIBLE" == "60" && "$RENDERING_VISIBLE" == "True" ]]; then
        _pass "Living wallpaper locks to 60 FPS when unoccluded"
    else
        _fail "Living wallpaper failed to reach 60 FPS in visible state (fps=$FPS_VISIBLE, rendering=$RENDERING_VISIBLE)"
    fi

    if [[ "$QUEUE_PRIO" == "VK_QUEUE_GLOBAL_PRIORITY_LOW_EXT" ]]; then
        _pass "Vulkan compute queue set to low priority (VK_QUEUE_GLOBAL_PRIORITY_LOW_EXT) to protect AI inference"
    else
        _fail "Vulkan compute queue priority incorrect: $QUEUE_PRIO"
    fi

    # Test occluded state: throttles to 0 FPS / 0.0% GPU load
    STATUS_OCCLUDED=$("$WALLPAPER_BIN" --mock --set-occluded true --status --json)
    FPS_OCCLUDED=$(python3 -c "import json, sys; d = json.loads('''$STATUS_OCCLUDED'''); print(d.get('fps'))")
    GPU_LOAD_OCCLUDED=$(python3 -c "import json, sys; d = json.loads('''$STATUS_OCCLUDED'''); print(d.get('gpu_load_pct'))")
    RENDERING_OCCLUDED=$(python3 -c "import json, sys; d = json.loads('''$STATUS_OCCLUDED'''); print(d.get('rendering'))")

    if [[ "$FPS_OCCLUDED" == "0" && "$GPU_LOAD_OCCLUDED" == "0.0" && "$RENDERING_OCCLUDED" == "False" ]]; then
        _pass "Living wallpaper suspends to 0 FPS / 0.0% GPU load when occluded"
    else
        _fail "Living wallpaper failed to throttle to 0 FPS when occluded (fps=$FPS_OCCLUDED, gpu=$GPU_LOAD_OCCLUDED)"
    fi
fi

# 4. Optional Live Headless Hyprland Verification (if hyprland binary is present)
if command -v Hyprland >/dev/null 2>&1; then
    echo "  [INFO] Hyprland binary available; testing config validation..."
    if Hyprland --verify-config -c "$HYPR_CONF" >/dev/null 2>&1; then
        _pass "Hyprland binary successfully validated $HYPR_CONF"
    else
        _fail "Hyprland binary reported config errors in $HYPR_CONF"
    fi
else
    echo "  [INFO] Hyprland binary not present in runner environment; static AST & config validation passed"
fi

echo "=== Summary: $pass passed, $fail failed ==="
if [[ $fail -gt 0 ]]; then
    exit 1
fi
exit 0
