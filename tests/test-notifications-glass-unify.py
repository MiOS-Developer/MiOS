#!/usr/bin/env python3
# AI-hint: Two-sided automated test suite for MiOS Notification System Unification & Glass Specular Highlights.
# Verifies Keymap QML focus fix, Quickshell reload popup inhibition & routing, top-centered notification geometry,
# contour-following specular highlights (zero whiskers), persistent tray history, and Steam notification interception.
# AI-related: usr/share/mios/quickshell/Keymap.qml, usr/share/mios/quickshell/Config.qml,
# usr/share/mios/quickshell/GlassSurface.qml, usr/share/mios/quickshell/Notifications.qml,
# usr/share/mios/quickshell/NotifModel.qml, usr/share/mios/quickshell/NotificationCenter.qml,
# usr/share/mios/quickshell/Theme.qml, usr/share/mios/mios.toml, usr/share/mios/hyprland/hyprland.conf,
# usr/libexec/mios/mios-steam-notify-bridge, etc/systemd/user/mios-steam-bridge.service
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


class TestNotificationsGlassUnify(unittest.TestCase):

    # ---------------------------------------------------------------------------------------------
    # 1. Milestone 1: Keymap.qml Focus Property & Quickshell Reload Routing
    # ---------------------------------------------------------------------------------------------
    def test_m1_keymap_focus_grab_property(self):
        """Positive: HyprlandFocusGrab uses active: keymap.open and OnDemand keyboard focus.
        Negative: visible: property assignment does not exist on HyprlandFocusGrab."""
        keymap_path = os.path.join(ROOT, "usr/share/mios/quickshell/Keymap.qml")
        self.assertTrue(os.path.isfile(keymap_path), f"Missing {keymap_path}")

        with open(keymap_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Positive controls
        self.assertIn("active: keymap.open", content, "HyprlandFocusGrab must have active: keymap.open")
        self.assertIn(
            "WlrLayershell.keyboardFocus: open ? WlrKeyboardFocus.OnDemand : WlrKeyboardFocus.None",
            content,
            "Keymap must use OnDemand focus, not Exclusive",
        )
        self.assertIn("import Quickshell.Hyprland", content, "Keymap must import Quickshell.Hyprland")

        # Negative controls: must NOT use visible on HyprlandFocusGrab (causes fatal QML error)
        grab_match = re.search(r"HyprlandFocusGrab\s*\{([^}]+)\}", content, re.MULTILINE)
        self.assertIsNotNone(grab_match, "HyprlandFocusGrab block not found in Keymap.qml")
        grab_body = grab_match.group(1)
        self.assertNotIn("visible:", grab_body, "HyprlandFocusGrab must NOT have property 'visible:'")
        self.assertNotIn("Exclusive", content, "Keymap must NOT use WlrKeyboardFocus.Exclusive")

    def test_m1_quickshell_reload_routing(self):
        """Positive: Config.qml inhibits reload popup and connects onReloadFailed/Completed to notifBus.post.
        Negative: reload errors are not dropped or left unrouted."""
        config_path = os.path.join(ROOT, "usr/share/mios/quickshell/Config.qml")
        self.assertTrue(os.path.isfile(config_path), f"Missing {config_path}")

        with open(config_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Positive controls
        self.assertIn(
            "QS.Quickshell.inhibitReloadPopup()",
            content,
            "Config.qml must invoke inhibitReloadPopup() to suppress red-border popup",
        )
        self.assertIn(
            "target: QS.Quickshell",
            content,
            "Config.qml must have Connections targeting QS.Quickshell",
        )
        self.assertIn("function onReloadCompleted()", content)
        self.assertIn("function onReloadFailed(errorString)", content)
        self.assertIn("notifBus.post", content)
        self.assertIn(
            "MiOS.NotifModel.recordHistory(t)",
            content,
            "notifBus.post() must record incoming toasts to NotifModel history",
        )

        # Negative control
        self.assertNotIn("console.error(errorString)", content, "Errors must route to notifBus, not just console")

    # ---------------------------------------------------------------------------------------------
    # 2. Milestone 2: Top-Centered Positioning, Glass Specular & Tray History
    # ---------------------------------------------------------------------------------------------
    def test_m2_notifications_top_centered_geometry(self):
        """Positive: Notifications.qml anchors strictly centered at (W - w)/2 and y = 44px (8px below HUD).
        Negative: Notifications are not anchored to island offset, left edge, or zero gap."""
        notif_path = os.path.join(ROOT, "usr/share/mios/quickshell/Notifications.qml")
        self.assertTrue(os.path.isfile(notif_path), f"Missing {notif_path}")

        with open(notif_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Positive controls
        self.assertIn(
            "cardX: Math.round((outputWidth - cardWidth) / 2)",
            content,
            "cardX must be centered horizontally",
        )
        self.assertIn(
            "cardY: topbarTopMargin + topbarHeight + gap",
            content,
            "cardY must be topbarTopMargin + topbarHeight + gap (44px below HUD)",
        )
        self.assertIn(
            "cardRadius: Glass.radius * ui",
            content,
            "cardRadius must conform to Glass.radius",
        )
        self.assertIn("specular: true", content, "Notifications must enable specular: true")

        # Negative control: must not be hardcoded to islandRect x offset which can drift with mobile tabs
        self.assertNotIn(
            "cardX: Math.max(margin, Math.min(islandRect.x",
            content,
            "cardX must not depend on islandRect.x offset",
        )

    def test_m2_glass_surface_contour_specular(self):
        """Positive: GlassSurface.qml implements concentric contour-following specular highlight.
        Negative: Zero straight 1px line rectangle artifacts or whiskers sticking past curved corners."""
        glass_surface_path = os.path.join(ROOT, "usr/share/mios/quickshell/GlassSurface.qml")
        self.assertTrue(os.path.isfile(glass_surface_path), f"Missing {glass_surface_path}")

        with open(glass_surface_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Positive controls
        self.assertIn("property bool specular: false", content)
        self.assertIn("radius: Math.max(0, surface.r - Glass.hairline)", content)
        self.assertIn("clip: surface.specular", content)
        self.assertIn("GradientStop", content)

        # Negative controls: must NOT have straight 1px line rectangle with left/right anchors and height: 1
        whisker_pattern = re.compile(
            r"anchors\.left:\s*parent\.left;\s*anchors\.right:\s*parent\.right;\s*height:\s*1\b",
            re.MULTILINE,
        )
        self.assertIsNone(
            whisker_pattern.search(content),
            "GlassSurface.qml must NOT contain straight 1px horizontal rectangles that cause whiskers",
        )

    def test_m2_notif_model_persistent_history(self):
        """Positive: NotifModel.qml implements historyList and recordHistory(entry).
        Negative: history is not dropped or destroyed without persistent record."""
        model_path = os.path.join(ROOT, "usr/share/mios/quickshell/NotifModel.qml")
        self.assertTrue(os.path.isfile(model_path), f"Missing {model_path}")

        with open(model_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("property var historyList: []", content)
        self.assertIn("function recordHistory(entry)", content)
        self.assertIn("function clearHistory()", content)
        self.assertIn("function removeHistory(id)", content)
        # Placement (operator ruling: [desktop.notifications] position is "island" -- every notification grows out of
        # the status island, top centre): before the SSOT is read the fallback is "island", never "top-center".
        pos = re.search(r"readonly property string position:\s*([^\n]+)", content)
        self.assertIsNotNone(pos, "NotifModel.qml must declare readonly property string position")
        literals = re.findall(r'"([^"]*)"', pos.group(1).split("//", 1)[0])
        self.assertTrue(literals, f"NotifModel position has no literal fallback: {pos.group(1)}")
        self.assertEqual(literals[-1], "island", f"Default position must be island: {pos.group(1)}")
        self.assertNotIn("top-center", content, "NotifModel.qml must not spell the placement top-center")

    def test_m2_notification_center_component(self):
        """Positive: NotificationCenter.qml exists and renders persistent history with specular GlassSurface delegates."""
        nc_path = os.path.join(ROOT, "usr/share/mios/quickshell/NotificationCenter.qml")
        self.assertTrue(os.path.isfile(nc_path), f"Missing {nc_path}")

        with open(nc_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("MiOS.NotifModel.historyList", content)
        self.assertIn("MiOS.NotifModel.clearHistory()", content)
        self.assertIn("MiOS.NotifModel.removeHistory", content)
        self.assertIn("specular: true", content)
        self.assertIn("GlassSurface", content)

    def test_m2_mios_toml_position_default(self):
        """Positive: mios.toml sets [desktop.notifications] position to 'island' (operator ruling: notifications grow
        out of the status island, top centre). Negative: the 'top-center' spelling is gone."""
        toml_path = os.path.join(ROOT, "usr/share/mios/mios.toml")
        with open(toml_path, "r", encoding="utf-8") as f:
            content = f.read()

        sec_match = re.search(r"\[desktop\.notifications\][^\[]+", content)
        self.assertIsNotNone(sec_match, "[desktop.notifications] section not found")
        sec_body = sec_match.group(0)
        self.assertIn('position            = "island"', sec_body)
        self.assertNotIn('"top-center"', sec_body)
        with open(toml_path, "rb") as f:
            parsed = tomllib.load(f)
        self.assertEqual(parsed["desktop"]["notifications"]["position"], "island")

    def test_m2_theme_bridge_exposure(self):
        """Positive: quickshell-Theme.qml.tmpl and Theme.qml expose notificationPosition."""
        for path_suffix in ["usr/share/mios/theme/templates/quickshell-Theme.qml.tmpl", "usr/share/mios/quickshell/Theme.qml"]:
            p = os.path.join(ROOT, path_suffix)
            self.assertTrue(os.path.isfile(p), f"Missing {p}")
            with open(p, "r", encoding="utf-8") as f:
                c = f.read()
            self.assertIn("notificationPosition", c, f"notificationPosition not exposed in {path_suffix}")
        # The bridge's fallback is the SSOT key itself (rendered to "island"), never a hand-typed spelling.
        with open(os.path.join(ROOT, "usr/share/mios/theme/templates/quickshell-Theme.qml.tmpl"), encoding="utf-8") as f:
            tmpl = f.read()
        self.assertIn('property string notificationPosition: "@MIOS:desktop.notifications.position@"', tmpl)

    # ---------------------------------------------------------------------------------------------
    # 3. Milestone 3: Steam Notification Bridge & Hyprland Rules
    # ---------------------------------------------------------------------------------------------
    def test_m3_hyprland_rules(self):
        """Positive: hyprland.conf includes mios-notifications layer rules and steam toast suppression window rules.
        Negative: Steam raw toasts are not allowed to float visibly on screen."""
        hypr_path = os.path.join(ROOT, "usr/share/mios/hyprland/hyprland.conf")
        with open(hypr_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Layer rules
        self.assertIn("layerrule = blur, mios-notifications", content)
        self.assertIn("layerrule = ignorealpha 0.5, mios-notifications", content)
        self.assertIn("layerrule = xray 0, mios-notifications", content)

        # Window rules
        self.assertIn("class:^(steam|Steam)$", content)
        self.assertIn("title:^(notificationtoasts_.*)$", content)
        self.assertIn("opacity 0.0 override 0.0 override", content)
        self.assertIn("move -99999 -99999", content)

    def test_m3_steam_bridge_daemon(self):
        """Positive: mios-steam-notify-bridge handles Steam notification events.
        Negative: Non-steam or non-toast windows are not intercepted."""
        bridge_path = os.path.join(ROOT, "usr/libexec/mios/mios-steam-notify-bridge")
        self.assertTrue(os.path.isfile(bridge_path), f"Missing {bridge_path}")
        self.assertTrue(os.access(bridge_path, os.X_OK), f"{bridge_path} is not executable")

        # Positive test: simulate steam toast event
        test_event = "openwindow>>55a8e1b20,1,steam,notificationtoasts_48_desktop"
        cmd = [sys.executable, bridge_path, "--dry-run", "--test-event", test_event]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"Bridge failed on positive test: {res.stderr}")
        data = json.loads(res.stdout)
        self.assertTrue(data.get("intercepted"), "Steam toast event must be intercepted")
        self.assertEqual(data.get("class"), "steam")
        self.assertEqual(data.get("summary"), "Steam")

        # Negative test 1: non-steam window (e.g. Alacritty)
        neg_event_1 = "openwindow>>123456,1,alacritty,Terminal"
        res_neg1 = subprocess.run(
            [sys.executable, bridge_path, "--dry-run", "--test-event", neg_event_1],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(res_neg1.returncode, 0, "Non-steam window must not be intercepted")
        data_neg1 = json.loads(res_neg1.stdout)
        self.assertFalse(data_neg1.get("intercepted"), "Non-steam window must have intercepted=false")

        # Negative test 2: steam regular window (not a toast)
        neg_event_2 = "openwindow>>987654,1,steam,Steam Library"
        res_neg2 = subprocess.run(
            [sys.executable, bridge_path, "--dry-run", "--test-event", neg_event_2],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(res_neg2.returncode, 0, "Regular Steam window must not be intercepted")
        data_neg2 = json.loads(res_neg2.stdout)
        self.assertFalse(data_neg2.get("intercepted"), "Regular Steam window must have intercepted=false")

    def test_m3_systemd_user_service(self):
        """Positive: etc/systemd/user/mios-steam-bridge.service exists and is configured properly."""
        service_path = os.path.join(ROOT, "etc/systemd/user/mios-steam-bridge.service")
        self.assertTrue(os.path.isfile(service_path), f"Missing {service_path}")

        with open(service_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("ExecStart=/usr/libexec/mios/mios-steam-notify-bridge", content)
        self.assertIn("PartOf=mios-desktop.target", content)

    # ---------------------------------------------------------------------------------------------
    # 4. Milestone 4: QML Syntax & Lint Verification
    # ---------------------------------------------------------------------------------------------
    def test_m4_qmllint_verification(self):
        """Positive: Modified QML files parse with zero errors via /usr/lib64/qt6/bin/qmllint."""
        qmllint = "/usr/lib64/qt6/bin/qmllint"
        if not os.path.isfile(qmllint) or not os.access(qmllint, os.X_OK):
            qmllint = shutil.which("qmllint")
        if not qmllint:
            self.skipTest("qmllint not installed in environment")

        qml_files = [
            "usr/share/mios/quickshell/Keymap.qml",
            "usr/share/mios/quickshell/Config.qml",
            "usr/share/mios/quickshell/GlassSurface.qml",
            "usr/share/mios/quickshell/Notifications.qml",
            "usr/share/mios/quickshell/NotifModel.qml",
            "usr/share/mios/quickshell/NotificationCenter.qml",
            "usr/share/mios/quickshell/Theme.qml",
        ]

        for rel_path in qml_files:
            abs_path = os.path.join(ROOT, rel_path)
            self.assertTrue(os.path.isfile(abs_path), f"File {rel_path} does not exist")
            cmd = [qmllint, "-I", "/usr/lib64/qt6/qml", abs_path]
            res = subprocess.run(cmd, capture_output=True, text=True)
            self.assertEqual(
                res.returncode,
                0,
                f"qmllint failed on {rel_path} (code {res.returncode}):\n{res.stdout}\n{res.stderr}",
            )


if __name__ == "__main__":
    unittest.main()
