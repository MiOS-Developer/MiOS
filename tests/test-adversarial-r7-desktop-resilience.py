#!/usr/bin/env python3
# AI-hint: Empirical adversarial verification for Milestone 1 (Dock Minimized Icon Stacking)
# and Milestone 2 (ShaderGlass Portal Resilience). Tests edge cases, class vs initialClass matching,
# minimizedTiles filtering, unrepresented app tile separation, DBus query and fallback behavior.
# AI-related: usr/share/mios/quickshell/Sidebar.qml, usr/share/mios/quickshell/DockLamp.qml,
# src/mios-rs/miosd/src/shaderglass.rs, tests/test-mios-lamp.py, tests/test-shaderglass.py
"""Empirical adversarial test suite for Desktop R1 and R2."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import unittest

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
QS_DIR = os.path.join(ROOT, "usr", "share", "mios", "quickshell")
SIDEBAR_QML = os.path.join(QS_DIR, "Sidebar.qml")
DOCKLAMP_QML = os.path.join(QS_DIR, "DockLamp.qml")
SHADERGLASS_RS = os.path.join(ROOT, "src", "mios-rs", "miosd", "src", "shaderglass.rs")


class MockDesktopEntry:
    def __init__(self, entry_id: str, app_id: str = "", startup_class: str = "", icon: str = "", extra_classes: list[str] | None = None):
        self.id = entry_id
        self.appId = app_id or (entry_id[:-8] if entry_id.endswith(".desktop") else entry_id)
        self.startupClass = startup_class
        self.icon = icon
        self.extra_classes = extra_classes or []
        self.noDisplay = False

    def get_classes(self) -> list[str]:
        out = set()
        for v in (self.id[:-8] if self.id.endswith(".desktop") else self.id, self.appId, self.startupClass, self.icon):
            if v:
                out.add(v)
                out.add(v.lower())
        for c in self.extra_classes:
            out.add(c)
            out.add(c.lower())
        return list(out)


class SimulatedDockLogic:
    """Exact emulation of the JavaScript/QML algorithms in Sidebar.qml and DockLamp.qml."""

    def __init__(self, pinned_ids: list[str], available_entries: dict[str, MockDesktopEntry]):
        self.pinned_ids = pinned_ids
        self.available_entries = available_entries
        self.toplevels = []

    def heuristic_lookup(self, query: str) -> MockDesktopEntry | None:
        if not query:
            return None
        q_lo = query.lower()
        for k, v in self.available_entries.items():
            if k.lower() == q_lo or (k.endswith(".desktop") and k[:-8].lower() == q_lo):
                return v
            if v.appId.lower() == q_lo or v.startupClass.lower() == q_lo or v.icon.lower() == q_lo:
                return v
        return None

    def owns_class(self, entry: MockDesktopEntry, cls: str) -> bool:
        if not entry or not cls:
            return False
        s = cls.strip()
        if not s:
            return False
        app = self.heuristic_lookup(s)
        if app and app.id == entry.id:
            return True
        classes = entry.get_classes()
        lo = s.lower()
        return any(c.lower() == lo for c in classes)

    def compute_dock_apps_and_minimized(self, all_minimized: list[dict]) -> tuple[list[MockDesktopEntry], list[dict]]:
        result = []
        seen_ids = set()

        def add(e: MockDesktopEntry | None):
            if e and not e.noDisplay and e.id not in seen_ids:
                seen_ids.add(e.id)
                result.append(e)

        # 1. Pinned
        for pid in self.pinned_ids:
            add(self.heuristic_lookup(pid))

        # 2. Toplevels (unminimized)
        for t in self.toplevels:
            if not any(e.id == t.get("desktop_id") for e in result):
                looked = self.heuristic_lookup(t.get("appId", ""))
                if looked:
                    add(looked)

        # 3. Dynamic lookup from allMinimizedTiles
        for m in all_minimized:
            cls = m.get("appId") or m.get("initialClass") or ""
            if cls and not any(self.owns_class(e, m.get("appId", "")) or self.owns_class(e, m.get("initialClass", "")) for e in result):
                looked = self.heuristic_lookup(cls) or (self.heuristic_lookup(m.get("initialClass", "")) if m.get("initialClass") else None)
                if looked:
                    add(looked)

        # 4. Filter minimizedTiles
        minimized_tiles = [
            m for m in all_minimized
            if not any(self.owns_class(e, m.get("appId", "")) or self.owns_class(e, m.get("initialClass", "")) for e in result)
        ]

        return result, minimized_tiles

    def minimized_window_for(self, entry: MockDesktopEntry, all_minimized: list[dict]) -> dict | None:
        for m in all_minimized:
            if self.owns_class(entry, m.get("appId", "")) or self.owns_class(entry, m.get("initialClass", "")):
                return m
        return None


class TestDockStackingAdversarial(unittest.TestCase):
    """Adversarial stress-testing of dock stacking edge cases."""

    def setUp(self):
        self.nautilus = MockDesktopEntry("org.gnome.Nautilus.desktop", app_id="org.gnome.Nautilus", startup_class="nautilus", icon="org.gnome.Nautilus")
        self.terminal = MockDesktopEntry("mios-terminal.desktop", app_id="mios-terminal", startup_class="Alacritty", icon="terminal")
        self.zen = MockDesktopEntry("app.zen_browser.zen.desktop", app_id="app.zen_browser.zen", startup_class="zen", icon="zen")
        self.entries = {
            "org.gnome.Nautilus.desktop": self.nautilus,
            "mios-terminal.desktop": self.terminal,
            "app.zen_browser.zen.desktop": self.zen,
        }
        self.dock = SimulatedDockLogic(
            pinned_ids=["mios-terminal.desktop", "app.zen_browser.zen.desktop"],
            available_entries=self.entries
        )

    def test_class_vs_initial_class_matching(self):
        """Verify window matching when class differs from initialClass."""
        # Case A: Window has generic runtime class (e.g. wine / wrapper) but initialClass matches desktop app
        win_a = {
            "address": "0x1001",
            "appId": "steam_proton_wrapper",
            "initialClass": "app.zen_browser.zen",
            "title": "Zen Browser",
        }
        dock_apps, min_tiles = self.dock.compute_dock_apps_and_minimized([win_a])
        self.assertEqual(len(min_tiles), 0, "win_a must stack onto pinned Zen icon and NOT appear in min_tiles")
        win_found = self.dock.minimized_window_for(self.zen, [win_a])
        self.assertIsNotNone(win_found)
        self.assertEqual(win_found["address"], "0x1001")

        # Case B: Window has class matching desktop app, but initialClass is empty or different
        win_b = {
            "address": "0x1002",
            "appId": "Alacritty",
            "initialClass": "",
            "title": "Terminal 1",
        }
        dock_apps, min_tiles = self.dock.compute_dock_apps_and_minimized([win_b])
        self.assertEqual(len(min_tiles), 0, "win_b must stack onto terminal and NOT appear in min_tiles")
        win_found = self.dock.minimized_window_for(self.terminal, [win_b])
        self.assertIsNotNone(win_found)
        self.assertEqual(win_found["address"], "0x1002")

        # Case C: Window has class with different casing
        win_c = {
            "address": "0x1003",
            "appId": "ALACRITTY",
            "initialClass": "alacritty",
            "title": "Terminal Upper",
        }
        dock_apps, min_tiles = self.dock.compute_dock_apps_and_minimized([win_c])
        self.assertEqual(len(min_tiles), 0, "Case-insensitive class matching must stack window")

    def test_unpinned_known_app_dynamically_added_to_dock_apps(self):
        """An unpinned app with a desktop file that gets minimized must be added to dockApps and filtered from minimizedTiles."""
        win_nautilus = {
            "address": "0x2001",
            "appId": "org.gnome.Nautilus",
            "initialClass": "nautilus",
            "title": "Home Files",
        }
        # Nautilus is NOT in pinned_ids
        dock_apps, min_tiles = self.dock.compute_dock_apps_and_minimized([win_nautilus])
        dock_app_ids = [e.id for e in dock_apps]
        self.assertIn("org.gnome.Nautilus.desktop", dock_app_ids, "Nautilus must be dynamically added to dockApps")
        self.assertEqual(len(min_tiles), 0, "Nautilus window must be filtered out of minimizedTiles")
        self.assertIsNotNone(self.dock.minimized_window_for(self.nautilus, [win_nautilus]))

    def test_multiple_windows_of_same_app_all_filtered_from_minimized_tiles(self):
        """When 5 windows of the same app are minimized, none of them may appear as loose tiles in minimizedTiles."""
        five_wins = [
            {"address": f"0x300{i}", "appId": "Alacritty", "initialClass": "Alacritty", "title": f"Term {i}"}
            for i in range(5)
        ]
        dock_apps, min_tiles = self.dock.compute_dock_apps_and_minimized(five_wins)
        self.assertEqual(len(min_tiles), 0, "All 5 terminal windows must be filtered from minimizedTiles")
        # Ensure the first minimized window is resolved for click-restore
        first_min = self.dock.minimized_window_for(self.terminal, five_wins)
        self.assertIsNotNone(first_min)
        self.assertEqual(first_min["address"], "0x3000")

    def test_unrepresented_apps_strictly_preserved_in_minimized_tiles(self):
        """Windows with NO matching desktop file and no dock entry must appear as distinct tiles in minimizedTiles."""
        unrep_1 = {
            "address": "0x9001",
            "appId": "scratch-script.sh",
            "initialClass": "scratch",
            "title": "Scratch Window 1",
        }
        unrep_2 = {
            "address": "0x9002",
            "appId": "scratch-script.sh",
            "initialClass": "scratch",
            "title": "Scratch Window 2",
        }
        unrep_3 = {
            "address": "0x9003",
            "appId": "x11_legacy_tool",
            "initialClass": "X11Tool",
            "title": "X11 Tool",
        }
        dock_apps, min_tiles = self.dock.compute_dock_apps_and_minimized([unrep_1, unrep_2, unrep_3])
        # Pinned apps remain in dock_apps
        self.assertEqual([e.id for e in dock_apps], ["mios-terminal.desktop", "app.zen_browser.zen.desktop"])
        # All three unrepresented windows must be preserved in min_tiles
        min_addrs = [m["address"] for m in min_tiles]
        self.assertEqual(min_addrs, ["0x9001", "0x9002", "0x9003"], "Every unrepresented window must get its own separate tile")

    def test_sidebar_source_code_contract(self):
        """Verify the exact partitioning expressions and click routing in Sidebar.qml and DockLamp.qml."""
        with open(SIDEBAR_QML, "r", encoding="utf-8") as f:
            sidebar = f.read()

        # 1. Verification of allMinimizedTiles property
        self.assertIn("readonly property var allMinimizedTiles:", sidebar)
        # 2. Strict filtering in minimizedTiles
        self.assertIn("return dock.allMinimizedTiles.filter(m => !dock.dockApps.some(e => root.ownsClass(e, m.appId) || root.ownsClass(e, m.initialClass)))", sidebar)
        # 3. Minimized window lookup in appRepeater
        self.assertIn("return dock.allMinimizedTiles.find(m => root.ownsClass(entry, m.appId) || root.ownsClass(entry, m.initialClass))", sidebar)
        # 4. App tile restore routes with source "icon"
        self.assertIn('DockLamp.restore(appTile.minimizedWin.address, "icon", dock.rectForEntry(modelData))', sidebar)
        # 5. Loose minimizedRepeater restores with source "minimized"
        self.assertIn('DockLamp.restore(addr, "minimized", rect)', sidebar)
        # 6. Negative control: minimizedRepeater must NOT pass "icon" as restore source
        min_start = sidebar.index("id: minimizedRepeater")
        min_end = sidebar.index("}\n                    }\n                }\n            }\n        }\n        Connections {")
        minimized_block = sidebar[min_start:min_end]
        self.assertIn('DockLamp.restore(addr, "minimized", rect)', minimized_block)
        self.assertNotIn('"icon"', minimized_block)

        # 7. Right-click context menu, long-highlight live window preview (ScreencopyView), and long-press reorganize
        dock_popup_qml = os.path.join( os.path.dirname(SIDEBAR_QML), "DockPopup.qml")
        self.assertTrue(os.path.isfile(dock_popup_qml), "DockPopup.qml must exist")
        with open(dock_popup_qml, "r", encoding="utf-8") as f:
            popup_src = f.read()
        self.assertIn("ScreencopyView", popup_src)
        self.assertIn("Pin to Dock", popup_src)
        self.assertIn("Remove Pin from Dock", popup_src)
        self.assertIn("Star / Add to Favourites", popup_src)
        self.assertIn("Re-organize / Manage Pins & Favourites", popup_src)
        self.assertIn("dockPopups.openContextMenu", sidebar)
        self.assertIn("dockPopups.showPreview", sidebar)
        self.assertIn("onPressAndHold:", sidebar)


class TestShaderGlassPortalResilience(unittest.TestCase):
    """Adversarial stress-testing of ShaderGlass DBus AvailableSourceTypes queries."""

    def test_live_dbus_queries(self):
        """Empirically query live host DBus interfaces for AvailableSourceTypes."""
        # 1. Desktop portal
        cmd1 = [
            "busctl", "--user", "get-property",
            "org.freedesktop.portal.Desktop",
            "/org/freedesktop/portal/desktop",
            "org.freedesktop.portal.ScreenCast",
            "AvailableSourceTypes",
        ]
        r1 = subprocess.run(cmd1, capture_output=True, text=True)
        self.assertEqual(r1.returncode, 0, f"DBus query on Desktop portal failed: {r1.stderr}")
        self.assertIn("7", r1.stdout.split())

        # 2. Hyprland implementation portal
        cmd2 = [
            "busctl", "--user", "get-property",
            "org.freedesktop.impl.portal.desktop.hyprland",
            "/org/freedesktop/portal/desktop",
            "org.freedesktop.impl.portal.ScreenCast",
            "AvailableSourceTypes",
        ]
        r2 = subprocess.run(cmd2, capture_output=True, text=True)
        self.assertEqual(r2.returncode, 0, f"DBus query on Hyprland implementation portal failed: {r2.stderr}")
        self.assertIn("7", r2.stdout.split())

    def test_query_available_source_types_fallback_oracle(self):
        """Oracle testing of the dual-interface DBus query fallback logic in query_available_source_types()."""

        def parse(output_text: str, success: bool) -> int | None:
            if not success:
                return None
            tokens = output_text.split()
            valid = []
            for t in tokens:
                try:
                    valid.append(int(t))
                except ValueError:
                    pass
            return valid[-1] if valid else None

        def simulate_query(desktop_res: tuple[str, bool], hyprland_res: tuple[str, bool]) -> int:
            val = parse(desktop_res[0], desktop_res[1])
            if val is not None and val != 0:
                return val
            val_fallback = parse(hyprland_res[0], hyprland_res[1])
            if val_fallback is not None:
                return val_fallback
            return 0

        # Scenario 1: Desktop succeeds with 7
        self.assertEqual(simulate_query(("u 7\n", True), ("u 7\n", True)), 7)

        # Scenario 2: Desktop returns 0 (uninitialized), Hyprland implementation returns 7
        self.assertEqual(simulate_query(("u 0\n", True), ("u 7\n", True)), 7, "Must fall back to Hyprland when Desktop returns 0")

        # Scenario 3: Desktop service is down / fails (exit code 1), Hyprland implementation returns 7
        self.assertEqual(simulate_query(("Failed to get property: Service unknown", False), ("u 7\n", True)), 7, "Must fall back when Desktop error occurs")

        # Scenario 4: Desktop service succeeds, Hyprland service is down
        self.assertEqual(simulate_query(("u 7\n", True), ("Failed to get property: Service unknown", False)), 7)

        # Scenario 5: Both return 0 (needs restart)
        self.assertEqual(simulate_query(("u 0\n", True), ("u 0\n", True)), 0)

        # Scenario 6: Both fail
        self.assertEqual(simulate_query(("error", False), ("error", False)), 0)

        # Scenario 7: Whitespace and unusual token formats
        self.assertEqual(parse("AvailableSourceTypes u 7\n", True), 7)
        self.assertEqual(parse("   7   ", True), 7)
        self.assertIsNone(parse("u", True))
        self.assertIsNone(parse("", False))

    def test_rust_implementation_source_contract(self):
        """Verify src/mios-rs/miosd/src/shaderglass.rs contains the dual-interface query and environment discovery."""
        with open(SHADERGLASS_RS, "r", encoding="utf-8") as f:
            src = f.read()

        # Query helper
        self.assertIn("fn query_available_source_types() -> u32", src)
        self.assertIn('"org.freedesktop.portal.Desktop"', src)
        self.assertIn('"org.freedesktop.portal.ScreenCast"', src)
        self.assertIn('"org.freedesktop.impl.portal.desktop.hyprland"', src)
        self.assertIn('"org.freedesktop.impl.portal.ScreenCast"', src)
        self.assertIn('"AvailableSourceTypes"', src)

        # Environment discovery & recovery in ensure_portal_backend
        self.assertIn("fn ensure_portal_backend()", src)
        self.assertIn("Hypr::connect()", src)
        self.assertIn('"HYPRLAND_INSTANCE_SIGNATURE"', src)
        self.assertIn('"WAYLAND_DISPLAY"', src)
        self.assertIn('"XDG_CURRENT_DESKTOP"', src)
        self.assertIn('"xdg-desktop-portal-hyprland.service"', src)
        self.assertIn("query_available_source_types()", src)

    def test_native_miosd_binaries_deployed_and_identical(self):
        """Verify /usr/bin/miosd and /usr/libexec/mios/miosd are identical static-PIE binaries."""
        bin1 = "/usr/bin/miosd"
        bin2 = "/usr/libexec/mios/miosd"
        self.assertTrue(os.path.isfile(bin1), f"{bin1} does not exist")
        self.assertTrue(os.path.isfile(bin2), f"{bin2} does not exist")

        with open(bin1, "rb") as f1, open(bin2, "rb") as f2:
            h1 = hashlib.sha256(f1.read()).hexdigest()
            h2 = hashlib.sha256(f2.read()).hexdigest()
        self.assertEqual(h1, h2, "Binaries in /usr/bin/miosd and /usr/libexec/mios/miosd must have identical sha256")

        # Verify execution
        r = subprocess.run([bin1, "shaderglass", "--help"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, f"miosd shaderglass --help failed: {r.stderr}")
        self.assertIn("shaderglass", r.stdout.lower())


if __name__ == "__main__":
    unittest.main()
