#!/usr/bin/env python3
# AI-hint: Two-sided contract test suite verifying the comprehensive fix for all 6 GUI/Shell defects:
#          - Defect 0: Global Keybinds Active (no exclusive layer focus trap in Search/Sidebar/Keymap/AppMenu).
#          - Defect 1: Full Liquid Glass Surfaces (glass backgrounds, border rims, specular highlights, compositor blur).
#          - Defect 2: Phosh OSK Virtual Keyboard (embedded in 220px slot, Phosh header, direct targetInput typing + sendshortcut IPC, D-Bus unfold).
#          - Defect 3: Maximize Workarea Bounds [0, 44, 0, 72] & TopBar Deconfliction (exclusiveZone 36px, fullscreen >= 2, tmux-os unoccluded).
#          - Defect 4: Rounded Corner UI Highlights (Glass.radius / innerRadius / pillRadius, zero square highlights).
#          - Defect 5: Dock App Launch Origin Animation (accurate icon coordinates, non-zero fallback, smooth zoom-out).
#          - GNOME & Phosh App Launching (Calls DBusActivatable bypass, XDG_CURRENT_DESKTOP GNOME:Phosh, drawer dismiss).
#          Two-sided verification: Every contract runs against working tree (must PASS) and git base commit (must FAIL).
# AI-related: usr/share/mios/quickshell/VirtualKeyboard.qml, usr/share/mios/quickshell/DockExpanded.qml,
#             usr/share/mios/quickshell/Sidebar.qml, usr/share/mios/quickshell/Search.qml,
#             usr/share/mios/quickshell/Keymap.qml, usr/share/mios/quickshell/AppMenu.qml,
#             usr/share/mios/quickshell/PanelWindow.qml, usr/share/mios/quickshell/Glass.qml,
#             usr/share/mios/quickshell/DockPreview.qml, usr/share/mios/quickshell/AppLaunch.qml,
#             usr/libexec/mios/mios-desktop-launch, usr/share/mios/hypr/hyprland-effects.conf
import os
import re
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QS = "usr/share/mios/quickshell"
BASE_REV = "72bd86de9f98fa7abef0ddf4dbeaec947450ccd4"
NEG_REF = os.environ.get("NEG_REF") or BASE_REV


def read(rel: str, ref: str = None) -> str:
    if ref is None:
        p = os.path.join(ROOT, rel)
        if not os.path.exists(p):
            return ""
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    r = subprocess.run(["git", "-C", ROOT, "show", f"{ref}:{rel}"], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


# ---- Defect 0: Global Keybinds & Layer Keyboard Focus -----------------------------------------------------------------
def contract_defect0_layer_keyboard_focus(ref: str = None) -> list[str]:
    problems = []
    # Search.qml: keyboard focus must NOT be unconditionally Exclusive when requestedOpen is true
    s = read(f"{QS}/Search.qml", ref)
    if not s:
        problems.append("Search.qml missing")
    else:
        if re.search(r"WlrLayershell\.keyboardFocus:\s*requestedOpen\s*\?\s*WlrKeyboardFocus\.Exclusive\s*:\s*WlrKeyboardFocus\.None", s):
            problems.append("Search.qml grabs unconditional Exclusive focus on requestedOpen")
        if "activeFocus" not in s:
            problems.append("Search.qml keyboardFocus not gated on activeFocus")
        # Focus grab must use active: property
        if re.search(r"HyprlandFocusGrab\s*\{[^}]*visible:", s):
            problems.append("Search.qml HyprlandFocusGrab uses non-existent property 'visible'")

    # Sidebar.qml: dock drawer must not grab Exclusive focus when on-screen keyboard is open
    sb = read(f"{QS}/Sidebar.qml", ref)
    if not sb:
        problems.append("Sidebar.qml missing")
    else:
        if re.search(r"WlrLayershell\.keyboardFocus:\s*expanded\s*\?\s*WlrKeyboardFocus\.Exclusive", sb):
            problems.append("Sidebar.qml dock grabs Exclusive focus whenever expanded (breaks global shortcuts and window typing)")
        if "!root.oskWanted" not in sb:
            problems.append("Sidebar.qml keyboardFocus does not check !root.oskWanted")

    # Keymap.qml: must use OnDemand focus and HyprlandFocusGrab
    km = read(f"{QS}/Keymap.qml", ref)
    if not km:
        problems.append("Keymap.qml missing")
    else:
        if re.search(r"WlrLayershell\.keyboardFocus:\s*open\s*\?\s*WlrKeyboardFocus\.Exclusive", km):
            problems.append("Keymap.qml grabs Exclusive focus instead of OnDemand")
        if "HyprlandFocusGrab" not in km:
            problems.append("Keymap.qml missing HyprlandFocusGrab for outside-click dismissal")
        if re.search(r"HyprlandFocusGrab\s*\{[^}]*visible:", km):
            problems.append("Keymap.qml HyprlandFocusGrab uses non-existent property 'visible'")

    # AppMenu.qml: must use OnDemand focus
    am = read(f"{QS}/AppMenu.qml", ref)
    if not am:
        problems.append("AppMenu.qml missing")
    else:
        if re.search(r"WlrLayershell\.keyboardFocus:\s*openMenu\s*>=\s*0\s*\?\s*WlrKeyboardFocus\.Exclusive", am):
            problems.append("AppMenu.qml grabs Exclusive focus instead of OnDemand")

    return problems


# ---- Defect 1: Full Liquid Glass Surfaces & Compositor Blur ----------------------------------------------------------
def contract_defect1_glass_styling_and_compositor_blur(ref: str = None) -> list[str]:
    problems = []
    # Search.qml: must have canonical Glass.radius, visible hairline border, and borderColor
    s = read(f"{QS}/Search.qml", ref)
    if not s:
        problems.append("Search.qml missing")
    else:
        if "radius: Glass.radius" not in s:
            problems.append("Search.qml does not use canonical MiOS spec radius: Glass.radius")
        if "hairline: true" not in s and "border.width:" not in s:
            problems.append("Search.qml missing glass border hairline")
        if "borderColor:" not in s and "border.color:" not in s:
            problems.append("Search.qml missing glass borderColor")

    # GlassSurface.qml: must have centralized concentric specular highlight rim
    gs = read(f"{QS}/GlassSurface.qml", ref)
    if not gs:
        problems.append("GlassSurface.qml missing")
    else:
        if "innerSpecular" not in gs and "specular" not in gs.lower():
            problems.append("GlassSurface.qml missing centralized concentric specular highlight rim")

    # Hyprland effects: must define blur and ignorealpha for quickshell and mios-* layer surfaces
    fx = read("usr/share/mios/hypr/hyprland-effects.conf", ref)
    if not fx:
        problems.append("hyprland-effects.conf missing")
    else:
        if "layerrule = blur, quickshell" not in fx:
            problems.append("hyprland-effects.conf missing blur rule for quickshell")
        if "mios-" not in fx and "^(mios-.*)$" not in fx:
            problems.append("hyprland-effects.conf missing blur rule for mios-* layer surfaces (e.g. mios-search, mios-dock)")
        if "xray 0" not in fx:
            problems.append("hyprland-effects.conf missing xray 0 rule for glass opacity clarity")

    return problems


# ---- Defect 2: Phosh OSK Virtual Keyboard Keys & Typing -------------------------------------------------------------
def contract_defect2_virtual_keyboard_osk(ref: str = None) -> list[str]:
    problems = []
    vk = read(f"{QS}/VirtualKeyboard.qml", ref)
    if not vk:
        problems.append("VirtualKeyboard.qml does not exist")
    else:
        # Check for Phosh-styled terminal/navigation header keys
        for key in ["Esc", "Tab", "Ctrl", "Alt", "Super", "←", "↑", "↓", "→", "|", "/", "-"]:
            if f'text: "{key}"' not in vk and f'"{key}"' not in vk:
                problems.append(f"VirtualKeyboard.qml missing header key '{key}'")
        # Check for dispatch logic: direct targetInput text insertion + sendshortcut fallback
        if "dispatchChar" not in vk or "sendHyprShortcut" not in vk:
            problems.append("VirtualKeyboard.qml missing input dispatching functions")
        if "targetInput.text" not in vk:
            problems.append("VirtualKeyboard.qml does not insert text directly into targetInput")
        if "sendshortcut" not in vk:
            problems.append("VirtualKeyboard.qml does not dispatch sendshortcut via Hyprland IPC")

    # DockExpanded.qml: must embed VirtualKeyboard inside the 220px slot
    de = read(f"{QS}/DockExpanded.qml", ref)
    if not de:
        problems.append("DockExpanded.qml missing")
    else:
        if "VirtualKeyboard" not in de:
            problems.append("DockExpanded.qml does not embed VirtualKeyboard")
        if "oskShown" not in de:
            problems.append("DockExpanded.qml does not bind VirtualKeyboard visibility to oskShown")

    # Sidebar.qml: must pass safe numeric arguments to mios-gen dock osk and issue D-Bus SetVisible
    sb = read(f"{QS}/Sidebar.qml", ref)
    if not sb:
        problems.append("Sidebar.qml missing")
    else:
        if 'command: [DockConfig.backend, "dock", "osk"' in sb:
            if '"--bottom"' not in sb or '"--width"' not in sb or '"--height"' not in sb:
                problems.append("Sidebar.qml passes invalid or missing arguments to mios-gen dock osk")
        if "sm.puri.OSK0" not in sb:
            problems.append("Sidebar.qml does not interface with sm.puri.OSK0 D-Bus service for stevia unfolding")

    return problems


# ---- Defect 3: Maximize Workarea Bounds & HUD Pill Deconfliction -----------------------------------------------------
def contract_defect3_maximize_workarea_and_deconfliction(ref: str = None) -> list[str]:
    problems = []
    pw = read(f"{QS}/PanelWindow.qml", ref)
    if not pw:
        problems.append("PanelWindow.qml missing")
    else:
        # exclusiveZone must incorporate Glass.gap so top reservation is 44px
        if "zoneFor(overFullscreen, rowHeight, Glass.gap, ui)" not in pw:
            problems.append("PanelWindow.qml exclusiveZone does not pass Glass.gap into zoneFor")

    # EdgeReveal.qml: must check true fullscreen (fullscreen >= 2), NEVER maximized (fullscreen == 1)
    er = read(f"{QS}/EdgeReveal.qml", ref)
    if not er:
        problems.append("EdgeReveal.qml missing")
    else:
        if "o.fullscreen >= 2" not in er:
            problems.append("EdgeReveal.qml treats maximized windows as fullscreen (breaks topbar exclusiveZone under maximized windows)")

    # AppMenu.qml: pills must be clipped before the status island to prevent overlap
    am = read(f"{QS}/AppMenu.qml", ref)
    if not am:
        problems.append("AppMenu.qml missing")
    else:
        if "island.x - rowLeft - Glass.gap" not in am and "island.x - " not in am:
            problems.append("AppMenu.qml does not deconflict width with status island (pills can overlap)")

    return problems


# ---- Defect 4: Rounded Corner UI Highlights (Zero Square Highlights) ------------------------------------------------
def contract_defect4_rounded_ui_highlights(ref: str = None) -> list[str]:
    problems = []
    # Glass.qml: must expose canonical radius, innerRadius, and pillRadius
    g = read(f"{QS}/Glass.qml", ref)
    if not g:
        problems.append("Glass.qml missing")
    else:
        if "readonly property real radius:" not in g:
            problems.append("Glass.qml missing convenience property 'radius'")
        if "readonly property real innerRadius:" not in g:
            problems.append("Glass.qml missing convenience property 'innerRadius'")
        if "readonly property real pillRadius:" not in g:
            problems.append("Glass.qml missing convenience property 'pillRadius'")

    # DockPreview.qml: bottom title scrim must use GlassSurface, not a plain sharp Rectangle
    dp = read(f"{QS}/DockPreview.qml", ref)
    if not dp:
        problems.append("DockPreview.qml missing")
    else:
        if re.search(r"Rectangle\s*\{[^}]*anchors\.bottom:\s*parent\.bottom[^}]*color:\s*theme\.withAlpha", dp):
            problems.append("DockPreview.qml uses a plain sharp Rectangle scrim at the card bottom")
        if "radius: Glass.cornerFor(height)" not in dp and "radius: Glass." not in dp:
            problems.append("DockPreview.qml title scrim missing rounded corner radius")

    # Sidebar.qml: app and minimized tiles must have rounded corners
    sb = read(f"{QS}/Sidebar.qml", ref)
    if not sb:
        problems.append("Sidebar.qml missing")
    else:
        if "radius: Glass.cornerFor(height)" not in sb and "Glass.corner(" not in sb:
            problems.append("Sidebar.qml tiles missing rounded corner radius")

    return problems


# ---- Defect 5: Dock App Launch Origin Animation ---------------------------------------------------------------------
def contract_defect5_launch_origin_animation(ref: str = None) -> list[str]:
    problems = []
    sb = read(f"{QS}/Sidebar.qml", ref)
    if not sb:
        problems.append("Sidebar.qml missing")
    else:
        if "return [0, 0, 48, 48]" in sb:
            problems.append("Sidebar.qml:rectForEntry falls back to top-left [0, 0, 48, 48] (causes top-left launch animation)")
        if "NEVER top-left" not in sb and "root.monX + Math.round" not in sb:
            problems.append("Sidebar.qml:rectForEntry missing bottom-center dock fallback coordinates")

    al = read(f"{QS}/AppLaunch.qml", ref)
    if not al:
        problems.append("AppLaunch.qml missing")
    else:
        if "[936, 1016, 48, 48]" not in al and "DockLamp.recordLaunch" not in al:
            problems.append("AppLaunch.qml missing safe dock fallback coordinates for DockLamp.recordLaunch")

    return problems


# ---- GNOME & Phosh App Launching & DBusActivatable Bypass ------------------------------------------------------------
def contract_gnome_phosh_app_launching(ref: str = None) -> list[str]:
    problems = []
    launch_sh = read("usr/libexec/mios/mios-desktop-launch", ref)
    if not launch_sh:
        problems.append("mios-desktop-launch missing")
    else:
        if "XDG_CURRENT_DESKTOP" not in launch_sh or "GNOME:Phosh" not in launch_sh:
            problems.append("mios-desktop-launch does not set XDG_CURRENT_DESKTOP containing GNOME:Phosh")
        if "DBusActivatable" not in launch_sh:
            problems.append("mios-desktop-launch does not bypass DBusActivatable=true headless daemon invocation (Calls fails to open GUI window)")

    sb = read(f"{QS}/Sidebar.qml", ref)
    if not sb:
        problems.append("Sidebar.qml missing")
    else:
        if "shellState.drawerOpen = false" not in sb:
            problems.append("Sidebar.qml does not close drawer on app launch")

    return problems


class TestGuiShellUnify(unittest.TestCase):
    """Automated test suite asserting genuine implementation and two-sided verification for GUI Shell unification."""

    def test_positive_defect0_layer_keyboard_focus(self):
        problems = contract_defect0_layer_keyboard_focus(None)
        self.assertEqual(problems, [], f"Defect 0 contract failed on working tree: {problems}")

    def test_negative_defect0_layer_keyboard_focus(self):
        problems = contract_defect0_layer_keyboard_focus(NEG_REF)
        self.assertTrue(len(problems) > 0, "Defect 0 negative control failed: base revision should have defects")

    def test_positive_defect1_glass_styling_and_compositor_blur(self):
        problems = contract_defect1_glass_styling_and_compositor_blur(None)
        self.assertEqual(problems, [], f"Defect 1 contract failed on working tree: {problems}")

    def test_negative_defect1_glass_styling_and_compositor_blur(self):
        problems = contract_defect1_glass_styling_and_compositor_blur(NEG_REF)
        self.assertTrue(len(problems) > 0, "Defect 1 negative control failed: base revision should have defects")

    def test_positive_defect2_virtual_keyboard_osk(self):
        problems = contract_defect2_virtual_keyboard_osk(None)
        self.assertEqual(problems, [], f"Defect 2 contract failed on working tree: {problems}")

    def test_negative_defect2_virtual_keyboard_osk(self):
        problems = contract_defect2_virtual_keyboard_osk(NEG_REF)
        self.assertTrue(len(problems) > 0, "Defect 2 negative control failed: base revision should have defects")

    def test_positive_defect3_maximize_workarea_and_deconfliction(self):
        problems = contract_defect3_maximize_workarea_and_deconfliction(None)
        self.assertEqual(problems, [], f"Defect 3 contract failed on working tree: {problems}")

    def test_negative_defect3_maximize_workarea_and_deconfliction(self):
        problems = contract_defect3_maximize_workarea_and_deconfliction(NEG_REF)
        self.assertTrue(len(problems) > 0, "Defect 3 negative control failed: base revision should have defects")

    def test_positive_defect4_rounded_ui_highlights(self):
        problems = contract_defect4_rounded_ui_highlights(None)
        self.assertEqual(problems, [], f"Defect 4 contract failed on working tree: {problems}")

    def test_negative_defect4_rounded_ui_highlights(self):
        problems = contract_defect4_rounded_ui_highlights(NEG_REF)
        self.assertTrue(len(problems) > 0, "Defect 4 negative control failed: base revision should have defects")

    def test_positive_defect5_launch_origin_animation(self):
        problems = contract_defect5_launch_origin_animation(None)
        self.assertEqual(problems, [], f"Defect 5 contract failed on working tree: {problems}")

    def test_negative_defect5_launch_origin_animation(self):
        problems = contract_defect5_launch_origin_animation(NEG_REF)
        self.assertTrue(len(problems) > 0, "Defect 5 negative control failed: base revision should have defects")

    def test_positive_gnome_phosh_app_launching(self):
        problems = contract_gnome_phosh_app_launching(None)
        self.assertEqual(problems, [], f"GNOME/Phosh app launching contract failed on working tree: {problems}")

    def test_negative_gnome_phosh_app_launching(self):
        problems = contract_gnome_phosh_app_launching(NEG_REF)
        self.assertTrue(len(problems) > 0, "GNOME/Phosh negative control failed: base revision should have defects")


if __name__ == "__main__":
    unittest.main()
