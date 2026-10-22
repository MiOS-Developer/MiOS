#!/usr/bin/env bash
# AI-hint: Automated CI verification test suite for MiOS Quickshell declarative QML desktop shell and Hyprland integration.
# AI-related: usr/share/mios/quickshell/, usr/share/mios/hyprland/hyprland.conf, automation/66-bake-quickshell.sh, usr/share/mios/mios.toml
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

echo "[test-quickshell-shell] === MiOS Quickshell Desktop Environment & QML Shell Test Suite ==="

# 1. Verify existence of all core Quickshell QML files
QML_DIR="$REPO_ROOT/usr/share/mios/quickshell"
REQUIRED_QMLS=(
    "Config.qml"
    "PanelWindow.qml"
    "Sidebar.qml"
    "Notifications.qml"
    "NotifModel.qml"
    "PortalData.qml"
    "Theme.qml"
)

echo "[test-quickshell-shell] Test 1: Verifying Quickshell QML surfaces"
for qml in "${REQUIRED_QMLS[@]}"; do
    if [[ -f "$QML_DIR/$qml" ]]; then
        _pass "Found QML surface: $qml"
    else
        _fail "Missing required QML surface: $qml"
    fi
done

# 2. Config.qml Root composition
echo "[test-quickshell-shell] Test 2: ShellRoot composition in Config.qml"
CONFIG_QML="$QML_DIR/Config.qml"
if grep -q "ShellRoot" "$CONFIG_QML" && \
   grep -q "PanelWindow" "$CONFIG_QML" && \
   grep -q "Sidebar" "$CONFIG_QML" && \
   grep -q "Notifications" "$CONFIG_QML"; then
    _pass "Config.qml accurately composes PanelWindow, Sidebar, and Notifications"
else
    _fail "Config.qml missing core shell surface instantiations"
fi

# 3. Theme.qml SSOT & FileView bridge
echo "[test-quickshell-shell] Test 3: SSOT Theme bridge in Theme.qml"
THEME_QML="$QML_DIR/Theme.qml"
if grep -Fq 'path: QS.Quickshell.env("MIOS_THEME_BRIDGE") || "/etc/mios/theme/theme.json"' "$THEME_QML" && \
   grep -q 'watchChanges: true' "$THEME_QML" && \
   grep -q 'JSON.parse' "$THEME_QML"; then
    _pass "Theme.qml dynamically watches /etc/mios/theme/theme.json via Quickshell.Io FileView"
else
    _fail "Theme.qml does not implement dynamic JSON theme bridge"
fi

# Verify fallback palette consistency with SSOT mios.toml
if grep -q 'property string bg:' "$THEME_QML" && \
   grep -q 'property string accent:' "$THEME_QML" && \
   grep -q 'property string cursor:' "$THEME_QML"; then
    _pass "Theme.qml defines typed fallback properties for colors and fonts"
else
    _fail "Theme.qml missing core color property definitions"
fi

# 4. Hyprland Integration
echo "[test-quickshell-shell] Test 4: Hyprland configuration integration"
HYPR_CONF="$REPO_ROOT/usr/share/mios/hyprland/hyprland.conf"
if grep -q "quickshell" "$HYPR_CONF"; then
    _pass "Hyprland conf contains quickshell autostart hook"
else
    _fail "Hyprland conf missing quickshell autostart hook"
fi

# 5. Automation bake script validation
echo "[test-quickshell-shell] Test 5: Automation bake script preservation"
BAKE_SCRIPT="$REPO_ROOT/automation/66-bake-quickshell.sh"
if grep -q "Config.qml" "$BAKE_SCRIPT" && ! grep -q "color: \"#1e1e2e\"" "$BAKE_SCRIPT"; then
    _pass "66-bake-quickshell.sh preserves canonical Config.qml without hardcoded color clobbering"
else
    _fail "66-bake-quickshell.sh clobbers or contains hardcoded theme colors"
fi

# 6. Wayland Session Desktop Entry
echo "[test-quickshell-shell] Test 6: Display Manager Wayland session"
SESSION_DESKTOP="$REPO_ROOT/usr/share/wayland-sessions/hyprland.desktop"
if [[ -f "$SESSION_DESKTOP" ]] && grep -q "Exec=/usr/bin/mios-gen hyprland-session --hardware-profile igpu" "$SESSION_DESKTOP"; then
    _pass "Wayland session entry hyprland.desktop is present and valid"
else
    _fail "Wayland session entry hyprland.desktop is invalid or missing"
fi
DGPU_DESKTOP="$REPO_ROOT/usr/share/wayland-sessions/mios-desktop-dgpu.desktop"
if [[ -f "$DGPU_DESKTOP" ]] && grep -q "Exec=/usr/bin/mios-gen hyprland-session --hardware-profile dgpu" "$DGPU_DESKTOP"; then
    _pass "dGPU session uses the shared launcher with its explicit hardware profile"
else
    _fail "dGPU session entry is invalid or missing"
fi

echo "[test-quickshell-shell] === Test Results: $pass passed, $fail failed ==="
if [[ $fail -gt 0 ]]; then
    exit 1
fi

echo "[test-quickshell-shell] SUCCESS: All $pass tests passed (100% success)."
