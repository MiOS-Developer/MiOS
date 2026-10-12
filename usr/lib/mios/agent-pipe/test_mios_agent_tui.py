# AI-hint: Exercise the actual unified MiOS Monitor at compact, portrait and desktop sizes, including selection, resize and refresh failures.
# AI-related: /usr/lib/mios/mios_agent_tui.py, /usr/libexec/mios/mios-mon.py
# AI-functions: TestAgentTui

import copy
import importlib.machinery
import json
import os
import pty
import select
import signal
import termios
import time
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "usr/lib/mios"))
from mios_agent_tui import AgentView, clean, peer_name
from textual.widgets import DataTable, Input, Static, TabbedContent

monitor = importlib.machinery.SourceFileLoader("mios_monitor_test", str(ROOT / "usr/libexec/mios/mios-mon.py")).load_module()
CLIENTS = [{"name": name, "installed": True, "mcp": name != "aider"}
           for name in ("claude", "codex", "gemini", "copilot", "opencode", "agy", "aider")]
SNAPSHOT = {"agents": [{"agent_id": "agy:unique", "kind": "agy", "label": "Antigravity · Active Orchestrator", "online": True, "pending": 2},
                        {"agent_id": "codex:unique", "kind": "codex", "label": "Codex · active integration", "online": True, "pending": 0}],
            "panes": [{"socket": "/private", "pane": f"%{i}", "role": f"W{i}", "command": "sleep", "dead": False} for i in range(1, 5)],
            "messages": [{"status": "queued"}, {"status": "received"}], "errors": []}


class TestAgentTui(unittest.IsolatedAsyncioTestCase):
    def app(self, mode="clients", observer=None, agents=None):
        return monitor.MiosMonitorApp(ui_request={"agents": agents or CLIENTS}, ui_mode=mode,
                                      observer=observer or (lambda: copy.deepcopy(SNAPSHOT)), collectors=False)

    async def test_all_clients_fit_and_select_at_native_half_width(self):
        app = self.app()
        async with app.run_test(size=(35, 19)) as pilot:
            await pilot.pause()
            table = app.query_one("#client-table", DataTable)
            self.assertEqual(table.row_count, 7)
            self.assertGreaterEqual(table.size.height, 8)
            self.assertEqual(table.get_row_at(6)[1].plain, "aider")
            self.assertLessEqual(table.region.right, 35)
            self.assertFalse(table.vertical_scrollbar.display)
            self.assertFalse(table.horizontal_scrollbar.display)
            self.assertEqual(table.styles.scrollbar_size_vertical, 0)
            self.assertEqual(table.styles.scrollbar_size_horizontal, 0)
            await pilot.press("7", "enter")
            await pilot.pause()
        self.assertEqual(app.return_value, "aider")

    async def test_observer_starts_on_agents_without_focusing_the_hidden_chooser(self):
        app = self.app(mode="agents")
        async with app.run_test(size=(44, 19)) as pilot:
            await pilot.pause()
            self.assertEqual(app.query_one(TabbedContent).active, "tab-agents")
            self.assertEqual(app.focused.id, "peer-table")
            for id in ("peer-table", "worker-table"):
                table = app.query_one(f"#{id}", DataTable)
                self.assertFalse(table.vertical_scrollbar.display)
                self.assertFalse(table.horizontal_scrollbar.display)
                self.assertEqual(table.styles.scrollbar_size_vertical, 0)
                self.assertEqual(table.styles.scrollbar_size_horizontal, 0)

    async def test_missing_and_invalid_clients_cannot_launch(self):
        clients = copy.deepcopy(CLIENTS)
        clients[0]["installed"] = False
        clients.append({"name": "x;touch /tmp/surprise", "installed": True})
        app = self.app(agents=clients)
        async with app.run_test(size=(35, 19)) as pilot:
            await pilot.press("1", "enter")
            await pilot.pause()
            self.assertIsNone(app.return_value)
            self.assertEqual(app.query_one("#client-table", DataTable).row_count, 7)
            self.assertIn("installed", str(app.query_one("#client-status", Static).render()))
            app.query_one(Input).value = "codex"
            await pilot.press("enter")
            await pilot.pause()
        self.assertEqual(app.return_value, "codex")

    async def test_keyboard_table_selection(self):
        app = self.app()
        async with app.run_test(size=(35, 19)) as pilot:
            await pilot.press("down", "down", "enter")
            await pilot.pause()
        self.assertEqual(app.return_value, "codex")

    async def test_views_share_one_app_and_resize_without_restarting(self):
        with patch.object(monitor, "get_services", return_value=[("agent-pipe", 0, True)]), patch.object(monitor, "get_telemetry", return_value=(10, 20, 30, 0, "1.0")):
            app = self.app()
            async with app.run_test(size=(35, 19)) as pilot:
                for size in ((44, 19), (46, 30), (80, 20), (180, 50), (35, 12)):
                    await pilot.resize_terminal(*size)
                    await pilot.press("f2")
                    await pilot.pause()
                    self.assertEqual(app.query_one(TabbedContent).active, "tab-agents")
                    for id in ("peer-table", "worker-table"):
                        table = app.query_one(f"#{id}", DataTable)
                        self.assertGreaterEqual(table.size.height, 2)
                        self.assertGreater(table.size.width, 0)
                        self.assertLessEqual(table.region.right, size[0])
                        if size in ((35, 19), (44, 19)):
                            self.assertFalse(table.vertical_scrollbar.display)
                            self.assertFalse(table.horizontal_scrollbar.display)
                            self.assertEqual(table.styles.scrollbar_size_vertical, 0)
                            self.assertEqual(table.styles.scrollbar_size_horizontal, 0)
                    await pilot.press("f3")
                    await pilot.pause()
                    self.assertEqual(app.query_one(TabbedContent).active, "tab-global")
                    await pilot.press("4")
                    await pilot.pause()
                    self.assertEqual(app.query_one(TabbedContent).active, "tab-agents")
                    app.action_tab_ai()
                    await pilot.pause()
                    self.assertEqual(app.query_one(TabbedContent).active, "tab-agents")
                    app._activate_tab("tab-ai")
                    await pilot.pause()
                    self.assertEqual(app.query_one(TabbedContent).active, "tab-agents")
                await pilot.press("f1")
                await pilot.pause()
                self.assertEqual(app.query_one("#client-table", DataTable).row_count, 7)
                client_table = app.query_one("#client-table", DataTable)
                self.assertFalse(client_table.vertical_scrollbar.display)
                self.assertFalse(client_table.horizontal_scrollbar.display)
                self.assertEqual(client_table.styles.scrollbar_size_vertical, 0)
                self.assertEqual(client_table.styles.scrollbar_size_horizontal, 0)

    async def test_poll_preserves_cursor_and_failure_is_visible(self):
        app = self.app(mode="agents")
        async with app.run_test(size=(44, 19)) as pilot:
            await pilot.pause()
            view = app.query_one(AgentView)
            table = app.query_one("#peer-table", DataTable)
            table.move_cursor(row=1)
            snapshot = copy.deepcopy(SNAPSHOT)
            snapshot["agents"][1]["pending"] = 9
            view.render_snapshot(snapshot)
            self.assertEqual(table.cursor_row, 1)
            self.assertEqual(table.get_row_at(1)[2].plain, "9")
            self.assertIn("queued", str(app.query_one("#receipt", Static).render()))
            self.assertEqual(app.query_one("#worker-table", DataTable).get_row_at(0)[2].plain, "Empty")
            def fail(): raise RuntimeError("offline")
            view.observer = fail
            await view.refresh_snapshot()
            self.assertIn("unavailable", str(app.query_one("#agent-error", Static).render()))
            self.assertEqual(table.row_count, 2)

    async def test_compact_table_scrollbars_and_tab_ai_compatibility(self):
        app = self.app(mode="ai")
        async with app.run_test(size=(35, 19)) as pilot:
            await pilot.pause()
            self.assertEqual(app.query_one(TabbedContent).active, "tab-agents")
            for id in ("peer-table", "worker-table"):
                table = app.query_one(f"#{id}", DataTable)
                self.assertFalse(table.vertical_scrollbar.display)
                self.assertFalse(table.horizontal_scrollbar.display)
                self.assertEqual(table.styles.scrollbar_size_vertical, 0)
                self.assertEqual(table.styles.scrollbar_size_horizontal, 0)
            app._activate_tab("tab-clients")
            await pilot.pause()
            self.assertEqual(app.query_one(TabbedContent).active, "tab-clients")
            client_table = app.query_one("#client-table", DataTable)
            self.assertFalse(client_table.vertical_scrollbar.display)
            self.assertFalse(client_table.horizontal_scrollbar.display)
            self.assertEqual(client_table.styles.scrollbar_size_vertical, 0)
            self.assertEqual(client_table.styles.scrollbar_size_horizontal, 0)
            app.action_tab_ai()
            await pilot.pause()
            self.assertEqual(app.query_one(TabbedContent).active, "tab-agents")

    def test_peer_labels_are_literal_and_identifiable(self):
        self.assertEqual(peer_name(SNAPSHOT["agents"][0]), "Agy Orchestrator")
        self.assertEqual(clean("[red]x\x1b\u202ey"), "[red]xy")

    def test_native_observation_receipt_is_unwrapped_and_failure_is_not_an_empty_registry(self):
        app = self.app()
        app.ui_request.update(state="/private", observation_request={"config": {"binary": "/native-relay"}})
        with patch.object(monitor.subprocess, "run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = json.dumps({"ok": True, "result": SNAPSHOT})
            self.assertEqual(app.observe_agents(), SNAPSHOT)
            run.return_value.stdout = json.dumps({"ok": False, "error": "registry unavailable"})
            with self.assertRaisesRegex(RuntimeError, "registry unavailable"):
                app.observe_agents()

    def test_native_observation_rejects_failed_and_malformed_receipts(self):
        app = self.app()
        app.ui_request.update(state="/private", observation_request={"config": {"binary": "/native-relay"}})
        cases = [
            (1, "", "observer denied", "observer denied"),
            (7, "", "", "Observer exit 7"),
            (0, "not-json", "", "observation failed"),
            (0, "[]", "", "Invalid native observation receipt"),
            (0, '{"ok": true, "result": []}', "", "Invalid native observation receipt"),
            (0, '{"ok": 1, "result": {}}', "", "Invalid native observation receipt"),
            (0, '{"ok": true, "result": {}}', "", "Invalid native observation snapshot"),
        ]
        for key in ("agents", "panes", "messages", "errors"):
            snapshot = copy.deepcopy(SNAPSHOT)
            snapshot[key] = None
            cases.append((0, json.dumps({"ok": True, "result": snapshot}), "", "Invalid native observation snapshot"))
        for code, output, error, expected in cases:
            with self.subTest(code=code, output=output), patch.object(monitor.subprocess, "run") as run:
                run.return_value.returncode, run.return_value.stdout, run.return_value.stderr = code, output, error
                with self.assertRaisesRegex(RuntimeError, expected):
                    app.observe_agents()
        for error, expected in ((monitor.subprocess.TimeoutExpired("observer", 10), "timed out"),
                                (FileNotFoundError("observer missing"), "observer missing")):
            with self.subTest(error=error), patch.object(monitor.subprocess, "run", side_effect=error):
                with self.assertRaisesRegex(RuntimeError, expected):
                    app.observe_agents()
        for request, expected in (({}, "configuration unavailable"),
                                  ({"observation_request": {"config": {}}}, "binary")):
            with self.subTest(request=request):
                app.ui_request = request
                with self.assertRaisesRegex(RuntimeError, expected):
                    app.observe_agents()

    async def test_native_observation_failure_keeps_last_snapshot_until_recovery(self):
        app = self.app(mode="agents")
        app.ui_request.update(state="/private", observation_request={"config": {"binary": "/native-relay"}})
        async with app.run_test(size=(44, 19)) as pilot:
            await pilot.pause()
            view = app.query_one(AgentView)
            table = app.query_one("#peer-table", DataTable)
            table.move_cursor(row=1)
            saved = copy.deepcopy(view.snapshot)
            receipt = str(app.query_one("#receipt", Static).render())
            view.observer = app.observe_agents
            with patch.object(monitor.subprocess, "run") as run:
                run.return_value.returncode = 0
                run.return_value.stdout = json.dumps({"ok": False, "error": "registry unavailable"})
                await view.refresh_snapshot()
                self.assertEqual(view.snapshot, saved)
                self.assertEqual(table.row_count, 2)
                self.assertEqual(table.cursor_row, 1)
                self.assertEqual(str(app.query_one("#receipt", Static).render()), receipt)
                self.assertIn("unavailable", str(app.query_one("#agent-error", Static).render()))
                recovered = copy.deepcopy(SNAPSHOT)
                recovered["agents"][1]["pending"] = 9
                run.return_value.stdout = json.dumps({"ok": True, "result": recovered})
                await view.refresh_snapshot()
                self.assertEqual(view.snapshot, recovered)
                self.assertEqual(table.cursor_row, 1)
                self.assertEqual(table.get_row_at(1)[2].plain, "9")
                self.assertNotIn("unavailable", str(app.query_one("#agent-error", Static).render()))

    def test_native_observation_accepts_a_healthy_empty_registry(self):
        app = self.app()
        app.ui_request.update(state="/private", observation_request={"config": {"binary": "/native-relay"}})
        snapshot = {key: [] for key in ("agents", "panes", "messages", "errors")}
        with patch.object(monitor.subprocess, "run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = json.dumps({"ok": True, "result": snapshot})
            self.assertEqual(app.observe_agents(), snapshot)

    def test_public_monitor_reads_keys_and_mouse_from_tty_after_piped_configuration(self):
        reader, writer = os.pipe()
        pid, terminal = pty.fork()
        if pid == 0:
            os.close(writer)
            os.dup2(reader, 0)
            os.close(reader)
            os.environ["TERM"] = "xterm-256color"
            os.environ["PYTHONPATH"] = str(ROOT / "usr/lib/mios")
            os.execl(sys.executable, sys.executable, str(ROOT / "usr/libexec/mios/mios-mon.py"),
                     "--ui-mode", "agents", "--ui-request-stdin")
        os.close(reader)
        output = bytearray()
        exited = False
        try:
            os.write(writer, json.dumps({"agents": CLIENTS}).encode())
            os.close(writer)
            writer = -1
            def read_until(pattern, timeout=8):
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    if pattern in output:
                        return
                    if select.select([terminal], [], [], 0.1)[0]:
                        output.extend(os.read(terminal, 65536))
                self.fail(f"TTY did not render {pattern!r}: {bytes(output)[-1500:]!r}")
            read_until(b"Relay:")
            self.assertFalse(termios.tcgetattr(terminal)[3] & termios.ECHO, "input must be in raw mode")
            self.assertNotIn(b"Choose a head CLI", output)
            os.write(terminal, b"\x1b[<35;4;4M\x1bOP")  # mouse motion, then F1
            read_until(b"Choose a head CLI")
            self.assertNotIn(b"^[[<35;4;4M", output, "mouse reports must not echo as text")
            os.write(terminal, b"\x1b")
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                done, status = os.waitpid(pid, os.WNOHANG)
                if done:
                    exited = True
                    self.assertEqual(os.waitstatus_to_exitcode(status), 0)
                    break
                time.sleep(0.05)
            self.assertTrue(exited, "Escape must close the actual TUI")
        finally:
            if writer >= 0:
                os.close(writer)
            if not exited:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
            os.close(terminal)


if __name__ == "__main__":
    unittest.main(verbosity=2)
