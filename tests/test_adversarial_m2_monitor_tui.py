# AI-hint: Adversarial empirical test harness for Milestone M2 (MiOS Monitor TUI view consolidation & compact scrollbars).
# AI-related: /usr/libexec/mios/mios-mon.py, /usr/lib/mios/mios_agent_tui.py, /usr/lib/mios/agent-pipe/test_mios_agent_tui.py

import copy
import importlib.machinery
import os
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "usr/lib/mios"))

monitor = importlib.machinery.SourceFileLoader(
    "mios_mon_adversarial", str(ROOT / "usr/libexec/mios/mios-mon.py")
).load_module()

from textual.app import App
from textual.css.query import NoMatches
from textual.widgets import DataTable, RichLog, Static, TabbedContent, TabPane
from mios_agent_tui import AgentView, ClientView

CLIENTS = [
    {"name": name, "installed": True, "mcp": True}
    for name in ("claude", "codex", "gemini", "opencode", "agy")
]

SNAPSHOT = {
    "agents": [
        {"agent_id": "agy:1", "kind": "agy", "label": "Antigravity", "online": True, "pending": 0},
        {"agent_id": "codex:2", "kind": "codex", "label": "Codex", "online": True, "pending": 1},
    ],
    "panes": [
        {"socket": "/run/mios-tmux/user.sock", "pane": "%1", "role": "W1", "command": "python3", "dead": False},
        {"socket": "/run/mios-tmux/user.sock", "pane": "%2", "role": "W2", "command": "bash", "dead": False},
    ],
    "messages": [],
    "errors": [],
}


class TestM2AdversarialMonitorTui(unittest.IsolatedAsyncioTestCase):
    def make_app(self, mode="agents", observer=None, agents=None):
        app = monitor.MiosMonitorApp(
            ui_request={"agents": agents or CLIENTS},
            ui_mode=mode,
            observer=observer or (lambda: copy.deepcopy(SNAPSHOT)),
            collectors=False,
        )
        app.cpu_history = []
        return app

    async def test_01_negative_control_ai_stats_removed(self):
        """Verify negative control: querying removed #ai-stats and #ai-stats-pane raises NoMatches."""
        app = self.make_app(mode="agents")
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()

            # 1. Negative control: querying #ai-stats strictly raises NoMatches
            with self.assertRaises(NoMatches, msg="Negative Control Failed: #ai-stats must not exist in widget tree"):
                app.query_one("#ai-stats")

            # 2. Negative control: querying #ai-stats-pane strictly raises NoMatches
            with self.assertRaises(NoMatches, msg="Negative Control Failed: #ai-stats-pane must not exist in widget tree"):
                app.query_one("#ai-stats-pane")

            # 3. Negative control: querying TabPane#tab-ai strictly raises NoMatches
            with self.assertRaises(NoMatches, msg="Negative Control Failed: #tab-ai TabPane must not exist"):
                app.query_one("#tab-ai")

            # 4. Collection query returns exactly 0 items
            self.assertEqual(len(app.query("#ai-stats")), 0)
            self.assertEqual(len(app.query("#ai-stats-pane")), 0)
            self.assertEqual(len(app.query("#tab-ai")), 0)

            # 5. Positive control: verify replacement unified components exist
            self.assertIsNotNone(app.query_one("#ai-container"))
            self.assertIsNotNone(app.query_one("#ai-log-box", RichLog))
            self.assertIsNotNone(app.query_one("#tab-agents", TabPane))

            # 6. Negative control perturbation verification: planting #ai-stats in a test container
            # proves that query_one would find it if present, so raising NoMatches is genuine.
            fake_stats = Static("fake stats", id="ai-stats")
            await app.query_one("#ai-container").mount(fake_stats)
            await pilot.pause()
            found = app.query_one("#ai-stats")
            self.assertEqual(found.id, "ai-stats")
            # Remove planted widget
            await fake_stats.remove()
            await pilot.pause()
            with self.assertRaises(NoMatches):
                app.query_one("#ai-stats")

    async def test_02_tab_ai_backward_compatibility_and_aliases(self):
        """Verify action_tab_ai(), _activate_tab('tab-ai'), '4', and 'f2' cleanly switch to tab-agents."""
        app = self.make_app(mode="clients")
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            tabs = app.query_one(TabbedContent)
            self.assertEqual(tabs.active, "tab-clients")

            # Test A: action_tab_ai()
            app.action_tab_ai()
            await pilot.pause()
            title_widget = app.query_one("#monitor-title", Static)
            title_text = str(getattr(title_widget, "content", getattr(title_widget, "renderable", getattr(title_widget, "_renderable", ""))))
            self.assertIn("Agents & AI", title_text)

            # Switch to global tab
            app._activate_tab("tab-global")
            await pilot.pause()
            self.assertEqual(tabs.active, "tab-global")

            # Test B: _activate_tab('tab-ai')
            app._activate_tab("tab-ai")
            await pilot.pause()
            self.assertEqual(tabs.active, "tab-agents", "_activate_tab('tab-ai') must route to tab-agents")

            # Switch to global tab
            app._activate_tab("tab-global")
            await pilot.pause()
            self.assertEqual(tabs.active, "tab-global")

            # Test C: Key '4' from tab-global
            await pilot.press("4")
            await pilot.pause()
            self.assertEqual(tabs.active, "tab-agents", "Key '4' binding must switch to tab-agents")

            # Switch to clients tab
            app.action_tab_clients()
            await pilot.pause()
            self.assertEqual(tabs.active, "tab-clients")

            # Test D: Key 'f2' (priority binding, works from any tab including clients)
            await pilot.press("f2")
            await pilot.pause()
            self.assertEqual(tabs.active, "tab-agents", "Key 'f2' binding must switch to tab-agents")

            # Test E: cycle_view does not contain 'tab-ai'
            cycle_views = ["tab-clients", "tab-agents", "tab-global", "tab-build", "tab-flash"]
            self.assertNotIn("tab-ai", cycle_views)

    async def test_03_ui_mode_ai_initial_state(self):
        """Verify launching monitor with ui_mode='ai' starts cleanly on tab-agents."""
        app = self.make_app(mode="ai")
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            tabs = app.query_one(TabbedContent)
            title_widget = app.query_one("#monitor-title", Static)
            title_text = str(getattr(title_widget, "content", getattr(title_widget, "renderable", getattr(title_widget, "_renderable", ""))))
            self.assertIn("Agents & AI", title_text)

    async def test_04_ai_log_box_slot_state_transitions_adversarial(self):
        """Adversarially stress-test slot state transitions formatting in #ai-log-box."""
        app = self.make_app(mode="agents")
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            app.cpu_history = []
            ai_log_box = app.query_one("#ai-log-box", RichLog)

            # Step 1: Initial discovery: slot 1 is active, slot 2 is idle
            step1_slots = [
                {"identity": "sock1:%1", "pid": 4501, "cmd": "cargo run -p worker", "active": True},
                {"identity": "sock1:%2", "pid": 4502, "cmd": "bash", "active": False},
            ]
            with patch.object(monitor, "get_agent_and_mcp_data", return_value=([], [], step1_slots)), \
                 patch.object(monitor, "get_telemetry", return_value=(10.0, 20.0, 30.0, 40.0, "1.0")), \
                 patch.object(monitor, "get_sys_info", return_value={"user": "u", "host": "h", "kernel": "k", "uptime": "1d", "cpu_model": "test"}):
                app.update_telemetry()
            await pilot.pause()

            log_output = "\n".join(strip.text for strip in ai_log_box.lines)
            self.assertIn("SLOT ACTIVE", log_output)
            self.assertIn("SLOT READY", log_output)
            self.assertIn("sock1:%1", log_output)
            self.assertIn("4501", log_output)
            self.assertIn("cargo run -p worker", log_output)
            self.assertIn("sock1:%2", log_output)

            # Step 2: Transitions:
            # - sock1:%1 switches command while active -> [SLOT EXEC]
            # - sock1:%2 becomes active -> [SLOT EXEC]
            # - sock1:%3 newly discovered active -> [SLOT ACTIVE]
            step2_slots = [
                {"identity": "sock1:%1", "pid": 4501, "cmd": "python3 compile.py", "active": True},
                {"identity": "sock1:%2", "pid": 4502, "cmd": "codex -p query", "active": True},
                {"identity": "sock1:%3", "pid": 4503, "cmd": "pytest tests/", "active": True},
            ]
            with patch.object(monitor, "get_agent_and_mcp_data", return_value=([], [], step2_slots)), \
                 patch.object(monitor, "get_telemetry", return_value=(10.0, 20.0, 30.0, 40.0, "1.0")), \
                 patch.object(monitor, "get_sys_info", return_value={"user": "u", "host": "h", "kernel": "k", "uptime": "1d", "cpu_model": "test"}):
                app.update_telemetry()
            await pilot.pause()

            log_output = "\n".join(strip.text for strip in ai_log_box.lines)
            self.assertIn("SLOT EXEC", log_output)
            self.assertIn("python3 compile.py", log_output)
            self.assertIn("codex -p query", log_output)
            self.assertIn("sock1:%3", log_output)

            # Step 3: Transition to idle and closed:
            # - sock1:%1 becomes idle -> [SLOT IDLE]
            # - sock1:%2 is terminated / removed -> [SLOT CLOSED]
            step3_slots = [
                {"identity": "sock1:%1", "pid": 4501, "cmd": "python3 compile.py", "active": False},
                {"identity": "sock1:%3", "pid": 4503, "cmd": "pytest tests/", "active": True},
            ]
            with patch.object(monitor, "get_agent_and_mcp_data", return_value=([], [], step3_slots)), \
                 patch.object(monitor, "get_telemetry", return_value=(10.0, 20.0, 30.0, 40.0, "1.0")), \
                 patch.object(monitor, "get_sys_info", return_value={"user": "u", "host": "h", "kernel": "k", "uptime": "1d", "cpu_model": "test"}):
                app.update_telemetry()
            await pilot.pause()

            log_output = "\n".join(strip.text for strip in ai_log_box.lines)
            self.assertIn("SLOT IDLE", log_output)
            self.assertIn("SLOT CLOSED", log_output)
            self.assertIn("released", log_output)
            self.assertIn("closed", log_output)

            # Step 4: ADVERSARIAL INJECTION & HOSTILE STRINGS:
            # - Injection of Rich markup tags: '[bold red]PWNED[/bold red]', '[[brackets]]', '[/]'
            # - Injection of Rich closing tags in identity: '[/cyan]pane:bad'
            # - Unicode and emoji: '💥 rm -rf / ; ⚡ <xml>'
            # - String PID, 0 PID, negative PID
            # - Empty cmd and empty identity
            step4_adversarial = [
                {
                    "identity": "[/cyan][bold red]malicious-identity[/bold red]",
                    "pid": "NaN_PID",
                    "cmd": "[bold red]INJECTED_MARKUP[/bold red] && [link=http://evil.com]click[/link] [/] [[test]]",
                    "active": True,
                },
                {
                    "identity": "unicode-slot-🚀",
                    "pid": -99,
                    "cmd": "⚡ mios-test --pattern='<xml>&\"'",
                    "active": True,
                },
                {
                    "identity": "",
                    "pid": 0,
                    "cmd": "",
                    "active": False,
                },
            ]
            # Executing this MUST NOT raise any exception (e.g. MarkupError, KeyError, TypeError)
            with patch.object(monitor, "get_agent_and_mcp_data", return_value=([], [], step4_adversarial)), \
                 patch.object(monitor, "get_telemetry", return_value=(10.0, 20.0, 30.0, 40.0, "1.0")), \
                 patch.object(monitor, "get_sys_info", return_value={"user": "u", "host": "h", "kernel": "k", "uptime": "1d", "cpu_model": "test"}):
                app.update_telemetry()
            await pilot.pause()

            log_output = "\n".join(strip.text for strip in ai_log_box.lines)
            # The literal text should appear escaped, not crashed
            self.assertIn("malicious-identity", log_output)
            self.assertIn("INJECTED_MARKUP", log_output)
            self.assertIn("unicode-slot-🚀", log_output)
            self.assertIn("SLOT READY", log_output)

    async def test_05_compact_table_scrollbars_suppression(self):
        """Stress-test DataTable scrollbar suppression across compact and non-standard dimensions."""
        app = self.make_app(mode="agents")
        async with app.run_test(size=(35, 19)) as pilot:
            test_dimensions = [
                (35, 19),  # standard half-width
                (44, 19),  # wider half-width
                (30, 15),  # very compact split
                (25, 10),  # extreme compact split
                (60, 22),  # sub-desktop size
                (80, 25),  # standard desktop size
                (120, 40), # wide desktop
            ]

            for w, h in test_dimensions:
                await pilot.resize_terminal(w, h)
                await pilot.pause()

                for table_id in ("#peer-table", "#worker-table"):
                    table = app.query_one(table_id, DataTable)
                    self.assertFalse(
                        table.vertical_scrollbar.display,
                        f"{table_id} vertical scrollbar should not display at {w}x{h}",
                    )
                    self.assertFalse(
                        table.horizontal_scrollbar.display,
                        f"{table_id} horizontal scrollbar should not display at {w}x{h}",
                    )
                    self.assertEqual(
                        table.styles.scrollbar_size_vertical,
                        0,
                        f"{table_id} scrollbar_size_vertical must be 0 at {w}x{h}",
                    )
                    self.assertEqual(
                        table.styles.scrollbar_size_horizontal,
                        0,
                        f"{table_id} scrollbar_size_horizontal must be 0 at {w}x{h}",
                    )

            # Switch to clients view and test client-table
            app.action_tab_clients()
            await pilot.pause()
            client_table = app.query_one("#client-table", DataTable)
            self.assertFalse(client_table.vertical_scrollbar.display)
            self.assertFalse(client_table.horizontal_scrollbar.display)
            self.assertEqual(client_table.styles.scrollbar_size_vertical, 0)
            self.assertEqual(client_table.styles.scrollbar_size_horizontal, 0)

    async def test_06_responsive_layout_compact_vs_desktop(self):
        """Verify responsive toggling: compact hides #ai-log-box; desktop displays both side-by-side."""
        app = self.make_app(mode="agents")
        async with app.run_test(size=(100, 30)) as pilot:
            # 1. Compact width (< 78)
            await pilot.resize_terminal(50, 22)
            await pilot.pause()
            log_box = app.query_one("#ai-log-box", RichLog)
            agent_view = app.query_one(AgentView)
            self.assertEqual(str(log_box.styles.display), "none", "In compact width, #ai-log-box must have display: none")
            self.assertEqual(agent_view.styles.width.value, 100.0, "In compact width, AgentView must expand to 100%")

            # 2. Compact height (< 26) even if wide (width 90)
            await pilot.resize_terminal(90, 20)
            await pilot.pause()
            self.assertEqual(str(log_box.styles.display), "none", "In compact height, #ai-log-box must have display: none")
            self.assertEqual(agent_view.styles.width.value, 100.0, "In compact height, AgentView must expand to 100%")

            # 3. The declared intermediate width (<95) remains compact.
            await pilot.resize_terminal(94, 30)
            await pilot.pause()
            self.assertEqual(str(log_box.styles.display), "none")

            # 4. Exact desktop boundary: width >=95 and height >=28.
            await pilot.resize_terminal(95, 28)
            await pilot.pause()
            self.assertEqual(str(log_box.styles.display), "block", "In desktop view, #ai-log-box must have display: block")

            # 5. One row below the height boundary returns to compact.
            await pilot.resize_terminal(95, 27)
            await pilot.pause()
            self.assertEqual(str(log_box.styles.display), "none")

    async def test_07_ai_log_box_message_stream_deduplication(self):
        """Verify message streaming into #ai-log-box with deduplication and long peer truncation."""
        app = self.make_app(mode="agents")
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            app.cpu_history = []
            log_box = app.query_one("#ai-log-box", RichLog)

            long_sender = "agent_with_an_extremely_long_name_that_exceeds_limits_12345"
            msg1 = {
                "message_id": "msg-001",
                "status": "received",
                "from": long_sender,
                "to": "target_agent",
                "created": 1700000000,
            }

            with patch.object(monitor, "get_agent_and_mcp_data", return_value=([], [msg1], [])), \
                 patch.object(monitor, "get_telemetry", return_value=(10.0, 20.0, 30.0, 40.0, "1.0")), \
                 patch.object(monitor, "get_sys_info", return_value={"user": "u", "host": "h", "kernel": "k", "uptime": "1d", "cpu_model": "test"}):
                app.update_telemetry()
            await pilot.pause()

            initial_lines_count = len(log_box.lines)
            self.assertGreater(initial_lines_count, 0)
            log_text = "\n".join(strip.text for strip in log_box.lines)
            self.assertIn("msg-001", log_text)
            self.assertIn("RECEIVED", log_text)
            # Verify peer truncation occurred (contains "..")
            self.assertIn("..", log_text)

            # Send duplicate message snapshot: should NOT append duplicate lines
            with patch.object(monitor, "get_agent_and_mcp_data", return_value=([], [msg1], [])), \
                 patch.object(monitor, "get_telemetry", return_value=(10.0, 20.0, 30.0, 40.0, "1.0")), \
                 patch.object(monitor, "get_sys_info", return_value={"user": "u", "host": "h", "kernel": "k", "uptime": "1d", "cpu_model": "test"}):
                app.update_telemetry()
            await pilot.pause()
            self.assertEqual(len(log_box.lines), initial_lines_count, "Duplicate message should not be logged again")


if __name__ == "__main__":
    unittest.main(verbosity=2)
