# AI-hint: Exercise the actual unified MiOS Monitor at compact, portrait and desktop sizes, including selection, resize and refresh failures.
# AI-related: /usr/lib/mios/mios_agent_tui.py, /usr/libexec/mios/mios-mon.py
# AI-functions: TestAgentTui

import copy
import asyncio
import threading
import importlib.machinery
import json
import os
import pty
import select
import signal
import socket
import tempfile
import termios
import time
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "usr/lib/mios"))
from mios_agent_tui import AgentView, InventoryDetails, SystemSummary, clean, peer_name, sync_rows
from textual.app import App
from textual.events import MouseScrollDown
from textual.widgets import Button, DataTable, Input, Static, TabbedContent

monitor = importlib.machinery.SourceFileLoader("mios_monitor_test", str(ROOT / "usr/libexec/mios/mios-mon.py")).load_module()
CLIENTS = [{"name": name, "installed": True, "mcp": name != "aider"}
           for name in ("claude", "codex", "gemini", "copilot", "opencode", "agy", "aider")]
SNAPSHOT = {"agents": [{"agent_id": "agy:unique", "kind": "agy", "label": "Antigravity · Active Orchestrator", "online": True, "pending": 2},
                        {"agent_id": "codex:unique", "kind": "codex", "label": "Codex · active integration", "online": True, "pending": 0}],
            "panes": [{"socket": "/private", "pane": f"%{i}", "role": f"W{i}", "command": "sleep", "dead": False} for i in range(1, 5)],
            "messages": [{"status": "queued"}, {"status": "received"}], "errors": []}


class TestAgentTui(unittest.IsolatedAsyncioTestCase):
    async def test_keyed_rows_preserve_selection_top_anchor_and_horizontal_scroll(self):
        class TableApp(App):
            def compose(self):
                yield DataTable(id="anchor", cursor_type="row")
            def on_mount(self):
                self.query_one(DataTable).add_column("Wide values", width=100)

        app = TableApp()
        async with app.run_test(size=(34, 12)) as pilot:
            table = app.query_one(DataTable)
            rows = [(f"row-{i:02}", (f"Value {i}",)) for i in range(70)]
            sync_rows(table, rows, ordered=True)
            await pilot.pause()
            table.move_cursor(row=45, scroll=False)
            table.scroll_to(x=13, y=30, animate=False, force=True, immediate=True)
            await pilot.pause()
            selected = "row-45"
            with patch.object(table, "clear", wraps=table.clear) as clear, \
                 patch.object(table, "update_cell", wraps=table.update_cell) as update:
                sync_rows(table, rows, ordered=True)
                await pilot.pause()
                clear.assert_not_called()
                update.assert_not_called()
            self.assertEqual(table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value, selected)
            self.assertEqual((table.scroll_x, table.scroll_y), (13, 30))

            # A status-sort change moves another row above the viewport; the
            # selected row and the visible top row are separate stable keys.
            reordered = rows[55:56] + rows[:55] + rows[56:]
            sync_rows(table, reordered, ordered=True)
            await pilot.pause()
            self.assertEqual(table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value, selected)
            self.assertEqual(table.ordered_rows[int(table.scroll_y)].key.value, "row-30")
            self.assertEqual(table.scroll_x, 13)

            # Removing both keys keeps their next retained neighbours rather
            # than jumping selection or viewport to the first row.
            remaining = [row for row in reordered if row[0] not in {"row-30", selected}]
            sync_rows(table, remaining, ordered=True)
            await pilot.pause()
            self.assertEqual(table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value, "row-46")
            self.assertEqual(table.ordered_rows[int(table.scroll_y)].key.value, "row-31")
            await pilot.resize_terminal(30, 10)
            await pilot.pause()
            sync_rows(table, remaining, ordered=True)
            await pilot.pause()
            self.assertEqual(table.ordered_rows[int(table.scroll_y)].key.value, "row-31")
            self.assertEqual(table.scroll_x, 13)
            sync_rows(table, [], ordered=True)
            await pilot.pause()
            self.assertEqual(table.row_count, 0)

    async def test_user_wheel_between_refresh_and_layout_wins_over_saved_anchor(self):
        class TableApp(App):
            def compose(self):
                yield DataTable(id="anchor", cursor_type="row")
            def on_mount(self):
                self.query_one(DataTable).add_column("Wide values", width=100)

        app = TableApp()
        async with app.run_test(size=(34, 12)) as pilot:
            table = app.query_one(DataTable)
            rows = [(f"row-{i:02}", (f"Value {i}",)) for i in range(70)]
            sync_rows(table, rows, ordered=True)
            await pilot.pause()
            table.move_cursor(row=45, scroll=False)
            table.scroll_to(x=13, y=30, animate=False, force=True, immediate=True)
            await pilot.pause()
            callbacks = []
            reordered = rows[55:56] + rows[:55] + rows[56:]
            # Delay just this refresh's final-layout callback so actual wheel
            # dispatch deterministically arrives in the vulnerable interval.
            with patch.object(table, "call_after_refresh", side_effect=lambda callback: callbacks.append(callback)):
                sync_rows(table, reordered, ordered=True)
            self.assertEqual(len(callbacks), 1)
            self.assertEqual(table.ordered_rows[int(table.scroll_y)].key.value, "row-30")
            before = table.scroll_y
            table.post_message(MouseScrollDown(table, 2, 2, 0, 1, 0, False, False, False))
            await pilot.pause()
            self.assertGreater(table.scroll_y, before, "dispatch the real widget wheel handler")
            wheel_position = (table.scroll_x, table.scroll_y)
            for callback in callbacks:
                callback()
            await pilot.pause()
            self.assertEqual((table.scroll_x, table.scroll_y), wheel_position)
            self.assertEqual(table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value, "row-45")

    async def test_agent_timer_collector_does_not_hold_widget_input(self):
        app = self.app(mode="agents")
        entered, release = threading.Event(), threading.Event()
        calls = []
        def slow_observer():
            calls.append(threading.get_ident())
            entered.set()
            if not release.wait(5):
                raise RuntimeError("test did not release observer")
            return copy.deepcopy(SNAPSHOT)

        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            view = app.query_one(AgentView)
            view.observer = slow_observer
            try:
                view.schedule_refresh()
                for _ in range(100):
                    await pilot.pause(.01)
                    if entered.is_set():
                        break
                self.assertTrue(entered.is_set())
                for _ in range(20):
                    view.schedule_refresh()
                await pilot.press("ctrl+f")
                await pilot.pause()
                self.assertEqual(app.focused.id, "agent-search")
                self.assertEqual(len(calls), 1)
                self.assertNotEqual(calls[0], threading.get_ident())
            finally:
                release.set()
                await view.workers.wait_for_complete()

    async def test_system_python_ui_receives_global_rows_from_mcp_interpreter(self):
        from types import SimpleNamespace
        local={"agents":[{"agent_id":"local","source":"local-relay","online":True}],
               "messages":[],"panes":[],"sessions":[],"subagents":[],"errors":[],
               "sources":[{"source":"local-relay","state":"ready"}]}
        frame={**local,"agents":[*local["agents"],{"agent_id":"system","source":"system/root-relay","online":True}],
               "sources":[*local["sources"],{"source":"system","state":"ready"}]}
        command=[sys.executable,"-I","-c","import time;print("+repr(json.dumps(frame))+",flush=True);time.sleep(30)"]
        request={"state":"/private","observation_command":command,"observation_request":{
                 "config":{"binary":"/native-relay","max_state_bytes":65536},"observation":{"refresh_s":.05}}}
        app=monitor.MiosMonitorApp(ui_request=request,ui_mode="agents",collectors=False)
        app.enable_agent_federation=True
        with patch.object(monitor.subprocess,"run",return_value=SimpleNamespace(returncode=0,
                stdout=json.dumps({"ok":True,"result":local}),stderr="")):
            async with app.run_test(size=(80,24)) as pilot:
                for _ in range(20):
                    await pilot.pause(.05)
                    if app.agent_inventory and len(app.observe_agents()["agents"])==2:break
                result=app.observe_agents()
                self.assertEqual([row["agent_id"] for row in result["agents"]],["local","system"])
                self.assertEqual(result["online_total"],2)
                await pilot.click("#scope-agents")
                await pilot.pause()
                self.assertEqual(app.query_one("#peer-table",DataTable).row_count,2)

    def app(self, mode="clients", observer=None, agents=None):
        return monitor.MiosMonitorApp(ui_request={"agents": agents or CLIENTS}, ui_mode=mode,
                                      observer=observer or (lambda: copy.deepcopy(SNAPSHOT)), collectors=False)

    async def test_hidden_monitor_logs_bound_pending_and_rendered_history(self):
        # Most log views have never received a nonzero size while another tab
        # is selected. RichLog's max_lines alone does not bound their queue.
        with patch.object(monitor, "monitor_sources_config", return_value={"scrollback_rows": 100}):
            app = self.app(mode="clients")
            async with app.run_test(size=(140, 40)) as pilot:
                logs = list(app.query(monitor.MonitorLog))
                self.assertEqual({log.id for log in logs},
                                 {"ai-log-box", "log-box", "build-log-box", "flash-log-box"})
                for log in logs:
                    self.assertEqual(log.max_lines, 100)
                    for index in range(500):
                        log.write(f"retained-{index:04}")
                    self.assertLessEqual(len(log._deferred_renders), 100, log.id)
                    self.assertLessEqual(len(log.lines), 100, log.id)
                for tab, log_id in (("tab-agents", "ai-log-box"), ("tab-global", "log-box"),
                                    ("tab-build", "build-log-box"), ("tab-flash", "flash-log-box")):
                    app.query_one(TabbedContent).active = tab
                    await pilot.pause()
                    log = app.query_one("#" + log_id, monitor.MonitorLog)
                    self.assertTrue(log.lines, log_id)
                    self.assertLessEqual(len(log.lines), 100, log_id)
                    self.assertFalse(log._deferred_renders, log_id)
                    text = "".join(segment.text for segment in log.lines[-1])
                    self.assertIn("retained-0499", text, log_id)

    async def test_log_expiry_preserves_scrolled_content_and_follow_mode(self):
        class LogApp(App):
            def compose(self):
                yield monitor.MonitorLog(id="reader", max_lines=100, min_width=1)

        app = LogApp()
        async with app.run_test(size=(60, 14)) as pilot:
            log = app.query_one("#reader", monitor.MonitorLog)
            for index in range(100):
                log.write(f"line-{index:04}")
            await pilot.pause()
            log.scroll_to(y=40, animate=False, force=True, immediate=True)
            top = "".join(segment.text for segment in log.lines[int(log.scroll_y)])
            for index in range(100, 115):
                log.write(f"line-{index:04}")
            await pilot.pause()
            self.assertEqual(len(log.lines), 100)
            self.assertEqual(log.scroll_y, 25)
            self.assertEqual("".join(segment.text for segment in log.lines[int(log.scroll_y)]), top)
            await pilot.resize_terminal(45, 12)
            await pilot.pause()
            log.write("line-0115")
            await pilot.pause()
            self.assertEqual(log.scroll_y, 24)
            self.assertEqual("".join(segment.text for segment in log.lines[int(log.scroll_y)]), top)
            log.scroll_end(animate=False, immediate=True)
            log.write("line-0116")
            await pilot.pause()
            self.assertTrue(log.is_vertical_scroll_end)

    async def test_all_clients_fit_and_select_at_native_half_width(self):
        app = self.app()
        async with app.run_test(size=(35, 19)) as pilot:
            await pilot.pause()
            table = app.query_one("#client-table", DataTable)
            self.assertEqual(table.row_count, 7)
            self.assertGreaterEqual(table.size.height, 8)
            self.assertEqual(table.get_row_at(6)[1].plain, "aider")
            self.assertLessEqual(table.region.right, 35)
            self.assertGreaterEqual(table.styles.scrollbar_size_vertical, 0)
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
            for id in ("peer-table",):
                table = app.query_one(f"#{id}", DataTable)
                self.assertGreaterEqual(table.styles.scrollbar_size_vertical, 0)
                self.assertFalse(table.horizontal_scrollbar.display)
                self.assertEqual(table.styles.scrollbar_size_vertical, 1)
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
            await pilot.pause()  # initial after-refresh focus must be settled
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
                    for id in ("peer-table",):
                        table = app.query_one(f"#{id}", DataTable)
                        self.assertGreaterEqual(table.size.height, 1)
                        self.assertGreater(table.size.width, 0)
                        self.assertLessEqual(table.region.right, size[0])
                        if size in ((35, 19), (44, 19)):
                            self.assertGreaterEqual(table.styles.scrollbar_size_vertical, 0)
                            self.assertFalse(table.horizontal_scrollbar.display)
                            self.assertEqual(table.styles.scrollbar_size_vertical, 1)
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
                # Textual may enable an overflow widget internally while its
                # zero-size gutter keeps it absent from the rendered layout.
                self.assertEqual(client_table.scrollbars_space, (0, 0))
                self.assertEqual(client_table.styles.scrollbar_size_vertical, 0)
                self.assertEqual(client_table.styles.scrollbar_size_horizontal, 0)
                await pilot.press(*(["down"] * len(CLIENTS)), "enter")
                await pilot.pause()
            self.assertEqual(app.return_value, "aider", "last client stays reachable in the short viewport")

    async def test_poll_preserves_cursor_and_failure_is_visible(self):
        app = self.app(mode="agents")
        async with app.run_test(size=(44, 19)) as pilot:
            await pilot.pause()
            view = app.query_one(AgentView)
            table = app.query_one("#peer-table", DataTable)
            view.set_scope("agents")
            table.move_cursor(row=1)
            snapshot = copy.deepcopy(SNAPSHOT)
            snapshot["agents"][1]["pending"] = 9
            view.render_snapshot(snapshot)
            self.assertEqual(table.cursor_row, 1)
            self.assertIn("Q:9", view.inventory[table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value]["detail"])
            self.assertIn("queued", str(app.query_one("#receipt", Static).render()))
            self.assertTrue(any(row["cells"][1] == "Empty" for row in view.inventory.values()))
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
            for id in ("peer-table",):
                table = app.query_one(f"#{id}", DataTable)
                self.assertGreaterEqual(table.styles.scrollbar_size_vertical, 0)
                self.assertFalse(table.horizontal_scrollbar.display)
                self.assertEqual(table.styles.scrollbar_size_vertical, 1)
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

    async def test_global_inventory_search_scopes_and_last_row_reachable(self):
        data = copy.deepcopy(SNAPSHOT)
        data["agents"] = [{"agent_id": f"node:agent:{i:03}", "label": f"Agent {i:03}", "online": i == 95,
                           "source": "tailnet-node", "pending": i} for i in range(96)]
        data["sessions"] = [{"session_id": f"session:{i}", "session_name": f"Session {i}", "source": "tailnet-node"} for i in range(48)]
        data["subagents"] = [{"agent_id": "child:one", "label": "Child one", "parent_agent_id": "node:agent:095", "online": True, "source": "second-node"}]
        app = self.app(mode="agents", observer=lambda: copy.deepcopy(data))
        async with app.run_test(size=(44, 19)) as pilot:
            await pilot.pause()
            view = app.query_one(AgentView)
            table = app.query_one("#peer-table", DataTable)
            self.assertEqual(table.row_count, 149)
            self.assertIn("All", app.query_one("#scope-all", Button).render_line(0).text)
            self.assertEqual(table.get_row_at(0)[0].plain, "Agent 095")
            table.focus()
            await pilot.press("ctrl+end")
            await pilot.pause()
            self.assertEqual(table.cursor_row, table.row_count - 1)
            selected = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
            view.render_snapshot(copy.deepcopy(data))
            self.assertEqual(table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value, selected)
            view.set_scope("subagents")
            self.assertEqual(table.row_count, 1)
            self.assertEqual(table.get_row_at(0)[0].plain, "Child one")
            view.set_scope("sessions")
            self.assertEqual(table.row_count, 52)
            view.set_scope("all")
            search = app.query_one("#agent-search", Input)
            search.value = "node:agent:095"
            await pilot.pause()
            self.assertEqual(table.row_count, 2, "search also finds the child's parent")
            search.value = "second-node"
            await pilot.pause()
            self.assertEqual(table.row_count, 1)
            self.assertIn("parent: node:agent:095", str(app.query_one("#agent-detail", Static).render()))
            table.focus()
            await pilot.press("enter")
            await pilot.pause()
            self.assertIsInstance(app.screen, InventoryDetails)
            self.assertIn("second-node", app.screen.detail)
            self.assertIn("node:agent:095", app.screen.detail)
            await pilot.press("escape")
            await pilot.pause()
            self.assertNotIsInstance(app.screen, InventoryDetails)
            for size in ((35, 12), (80, 24), (180, 50)):
                await pilot.resize_terminal(*size)
                await pilot.pause()
                self.assertGreaterEqual(table.size.height, 1)
                self.assertLessEqual(table.region.right, size[0])

    async def test_partial_source_keeps_rows_and_shows_unknown_presence(self):
        data = copy.deepcopy(SNAPSHOT)
        data["agents"][0]["stale"] = True
        data["sources"] = [{"source":"tailnet-node", "state":"unavailable"}]
        data["errors"] = [{"error":"incomplete"}]
        app = self.app(mode="agents", observer=lambda: copy.deepcopy(data))
        async with app.run_test(size=(44,19)) as pilot:
            await pilot.pause()
            view = app.query_one(AgentView)
            self.assertTrue(any(row["cells"][1] == "Unknown" for row in view.inventory.values()))
            self.assertIn("partial", str(app.query_one("#agent-error", Static).render()))
            self.assertIn("1 online / 2", str(app.query_one("#relay-count", Static).render()))

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
            (7, "[]", "", "Observer exit 7"),
            (2, '{"ok": false, "error": "agent state exceeds observer limit"}', "", "exceeds observer limit"),
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
            view.set_scope("agents")
            table.move_cursor(row=1)
            saved = copy.deepcopy(view.snapshot)
            receipt = str(app.query_one("#receipt", Static).render())
            view.observer = app.observe_agents
            with patch.object(monitor.subprocess, "run") as run:
                run.return_value.returncode = 0
                run.return_value.stdout = json.dumps({"ok": False, "error": "registry unavailable"})
                await view.refresh_snapshot()
                self.assertTrue(view.snapshot["stale"])
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
                self.assertIn("Q:9", view.inventory[table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value]["detail"])
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
                        try:
                            output.extend(os.read(terminal, 65536))
                        except OSError:
                            self.fail(f"TUI exited before {pattern!r}: {bytes(output)[-9000:]!r}")
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


class TestDesktopIdleCollection(unittest.IsolatedAsyncioTestCase):
    def app(self, observer=None, desktop_panel=True):
        return monitor.MiosMonitorApp(desktop_panel=desktop_panel, ui_mode="agents", collectors=False,
                                      ui_request={"observation_request": {"observation": {"refresh_s": .05}}},
                                      observer=observer or (lambda: copy.deepcopy(SNAPSHOT)))

    def test_attachment_probe_requires_exact_private_own_panel_witness(self):
        cfg = {"keybindings": {"tmux": {"desktop": {"binary": "/private/tmux", "command_timeout_ms": 3000}}}}
        master, slave = pty.openpty()
        try:
            terminal = os.fstat(slave)
            with tempfile.TemporaryDirectory(prefix="mios-desktop-attachment-") as root, \
                 socket.socket(socket.AF_UNIX) as server:
                path = os.path.join(root, "desktop")
                server.bind(path)
                reply = SimpleNamespace(returncode=0, stdout="")
                fields = [path, "1234", "%7", str(os.getpid()), os.ttyname(slave), "0", "1"]
                with patch.dict(os.environ, TMUX=path + ",1234,0", TMUX_PANE="%7"), \
                     patch.object(monitor, "IS_WINDOWS", False), \
                     patch.object(monitor, "monitor_config", return_value=cfg), \
                     patch.object(monitor.os, "fstat", return_value=terminal), \
                     patch.object(monitor.subprocess, "run", return_value=reply) as run:
                    for count, expected in (("0", False), ("1", True), ("2", True)):
                        fields[5] = count
                        reply.stdout = "|".join(fields) + "\n"
                        self.assertIs(monitor.desktop_attached(), expected)
                    for index, value in ((0, path + "-other"), (1, "9999"), (2, "%8"),
                                         (3, str(os.getpid() + 1)), (4, os.devnull), (5, "unknown"), (6, "")):
                        broken = list(fields)
                        broken[index] = value
                        reply.stdout = "|".join(broken) + "\n"
                        self.assertIsNone(monitor.desktop_attached(), (index, value))
                    run.reset_mock()
                    os.chmod(root, 0o755)
                    self.assertIsNone(monitor.desktop_attached())
                    run.assert_not_called()
                    os.chmod(root, 0o700)
        finally:
            os.close(master)
            os.close(slave)

    async def test_detach_pauses_inventory_and_owned_process_then_attach_resumes(self):
        from mios_mcp_federation import AgentObservationProcess
        frame = {**SNAPSHOT, "sources": [], "sessions": [], "subagents": []}
        command = [sys.executable, "-I", "-c", "import time;print(" + repr(json.dumps(frame))
                   + ",flush=True);time.sleep(30)"]
        calls = []
        app = self.app(observer=lambda: calls.append(True) or copy.deepcopy(SNAPSHOT))
        inventory = app.agent_inventory = AgentObservationProcess(command, 65536)
        app._owned_observation = True
        async with app.run_test(size=(80, 12)) as pilot:
            inventory.start_background()
            for _ in range(40):
                if inventory.latest is not None:
                    break
                await pilot.pause(.05)
            self.assertIsNotNone(inventory.latest)
            original = inventory.process
            view = app.query_one(AgentView)
            await view.workers.wait_for_complete()
            retained = copy.deepcopy(view.snapshot)
            app.collectors_enabled = True
            with patch.object(monitor, "desktop_attached", return_value=True):
                await app.refresh_desktop_attachment()
            self.assertIs(inventory.process, original)
            self.assertIsNone(original.returncode)
            with patch.object(monitor, "desktop_attached", return_value=False):
                await app.refresh_desktop_attachment()
                await app.refresh_desktop_attachment()
            self.assertTrue(app._collection_paused)
            self.assertTrue(view.refresh_paused)
            self.assertIsNotNone(original.returncode)
            self.assertIsNone(inventory.task)
            count = len(calls)
            view.schedule_refresh()
            await view.refresh_snapshot()
            await pilot.pause(.1)
            self.assertEqual(len(calls), count)
            self.assertEqual(view.snapshot, retained)
            self.assertFalse(app._start_snapshot("must-stay-idle", lambda: self.fail("detached collector ran"), lambda _: None))
            with patch.object(monitor, "desktop_attached", return_value=True):
                await app.refresh_desktop_attachment()
            for _ in range(40):
                if inventory.process is not original and inventory.process.returncode is None:
                    break
                await pilot.pause(.05)
            self.assertFalse(app._collection_paused)
            self.assertFalse(view.refresh_paused)
            self.assertIsNot(inventory.process, original)
            self.assertIsNone(inventory.process.returncode)
            await pilot.pause(.1)
            self.assertGreater(len(calls), count)

    async def test_unknown_attachment_and_standalone_leave_collectors_enabled(self):
        app = self.app()
        async with app.run_test(size=(80, 12)):
            app.collectors_enabled = True
            app._collection_paused = True
            app.query_one(AgentView).set_refresh_paused(True)
            with patch.object(monitor, "desktop_attached", return_value=None) as probe:
                await app.refresh_desktop_attachment()
                self.assertFalse(app._collection_paused)
                self.assertFalse(app.query_one(AgentView).refresh_paused)
                probe.assert_called_once()
            app.desktop_panel = False
            with patch.object(monitor, "desktop_attached") as probe:
                await app.refresh_desktop_attachment()
                probe.assert_not_called()
            self.assertFalse(app._collection_paused)

    async def test_attachment_poll_coalesces_without_blocking_navigation(self):
        entered, release = threading.Event(), threading.Event()
        def probe():
            entered.set()
            if not release.wait(3):
                raise RuntimeError("attachment probe not released")
            return False
        app = self.app()
        async with app.run_test(size=(80, 12)) as pilot:
            app.collectors_enabled = True
            with patch.object(monitor, "desktop_attached", side_effect=probe) as read:
                pending = asyncio.create_task(app.refresh_desktop_attachment())
                try:
                    self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                    for _ in range(20):
                        await app.refresh_desktop_attachment()
                    await pilot.press("ctrl+f")
                    self.assertEqual(app.focused.id, "agent-search")
                    self.assertEqual(read.call_count, 1)
                finally:
                    release.set()
                    await pending


class TestDesktopPanel(unittest.IsolatedAsyncioTestCase):
    def app(self):
        app = monitor.MiosMonitorApp(desktop_panel=True, collectors=False,
                                    observer=lambda: copy.deepcopy(SNAPSHOT))
        app.panel_policy.update(default_view="agents", compact_columns=78, compact_rows=12,
                                overview_columns=78, overview_rows=12, poll_s=5)
        return app

    def test_standalone_entry_resolves_native_relay_request_once(self):
        request = {"observation_request": {"config": {"binary": "/private/relay"}},
                   "observation_command": ["/private/python", "/private/server", "--observe-stream"],
                   "state": "/private/state", "agents": []}
        reply = type("Reply", (), {"stdout": json.dumps(request)})()
        for mode in ("--desktop-panel", "--client-chooser"):
            with self.subTest(mode=mode), \
                 patch.object(sys, "argv", ["mios-mon.py", mode, "--root", "/private-root"]), \
                 patch.object(monitor, "monitor_config", return_value={"mcp": {"python": "/private/python"}}), \
                 patch.object(monitor.subprocess, "run", return_value=reply) as run, \
                 patch.object(monitor, "_run_monitor_app", return_value=None) as launch:
                monitor.main()
            run.assert_called_once()
            self.assertEqual(run.call_args.args[0][-1], "--monitor-request")
            self.assertEqual(run.call_args.args[0][0], "/private/python")
            self.assertEqual(launch.call_args.kwargs["ui_request"], request)
            self.assertEqual(launch.call_args.kwargs["desktop_panel"], mode == "--desktop-panel")
            self.assertEqual(launch.call_args.kwargs["client_chooser"], mode == "--client-chooser")
            self.assertEqual(launch.call_args.kwargs["root"], "/private-root")

    async def test_one_app_keeps_navigation_and_inventory_through_tiny_resizes(self):
        app = self.app()
        with patch.object(app, "_start_snapshot", return_value=True) as collect:
            async with app.run_test(size=(80, 12)) as pilot:
                await pilot.pause()
                inventory = app.query_one(AgentView)
                table = app.query_one("#peer-table", DataTable)
                self.assertEqual(app.focused, table)
                self.assertEqual(len(app.query("#monitor-title")), 0)
                await pilot.click("#panel-nav-tasks")
                self.assertEqual(app.query_one(TabbedContent).active, "tab-tasks")
                for key, expected in (("f2", "tab-global"), ("f3", "tab-logs"),
                                      ("f4", "tab-tasks"), ("f5", "tab-input"), ("f1", "tab-agents")):
                    await pilot.press(key)
                    self.assertEqual(app.query_one(TabbedContent).active, expected)
                for width, height in ((35, 8), (20, 4), (8, 2), (120, 18), (80, 12)):
                    await pilot.resize_terminal(width, height)
                    await pilot.pause()
                    self.assertIs(app.query_one(AgentView), inventory)
                    self.assertIs(app.query_one("#peer-table", DataTable), table)
                    nav = app.query_one("#desktop-nav")
                    self.assertEqual(nav.size.height, 1)
                    self.assertEqual(str(nav.styles.height), "1")
                    for button in app.query("#desktop-nav Button"):
                        self.assertEqual(button.region.height, 1)
                        self.assertLessEqual(button.region.right, width)
                    self.assertGreaterEqual(table.size.height, 1)
                    self.assertLessEqual(table.region.right, width)
                await pilot.press("q", "escape")
                self.assertTrue(app.is_running)
                self.assertEqual(app.query_one(TabbedContent).active, "tab-agents")
                collect.assert_not_called()

    async def test_all_task_rows_and_independent_scroll_anchor_survive_refresh_and_resize(self):
        app = self.app()
        async with app.run_test(size=(80, 12)) as pilot:
            await pilot.pause()
            app._activate_tab("tab-tasks")
            table = app.query_one("#panel-tasks", DataTable)
            rows = [(f"task-{i}", (str(i), f"P1 · Work {i} · Owner")) for i in range(300)]
            app.apply_panel_view("tab-tasks", {"data": rows})
            await pilot.pause()
            self.assertEqual(table.row_count, 300)
            table.move_cursor(row=180, scroll=False)
            table.scroll_to(y=160, animate=False, force=True, immediate=True)
            await pilot.pause()
            reordered = rows[290:291] + rows[:290] + rows[291:]
            app.apply_panel_view("tab-tasks", {"data": reordered})
            await pilot.pause()
            self.assertEqual(table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value, "task-180")
            self.assertEqual(table.ordered_rows[int(table.scroll_y)].key.value, "task-160")
            await pilot.resize_terminal(35, 6)
            app.apply_panel_view("tab-tasks", {"data": reordered})
            await pilot.pause()
            self.assertEqual(table.ordered_rows[int(table.scroll_y)].key.value, "task-160")
            app.apply_panel_view("tab-tasks", {"unavailable": True})
            self.assertEqual(table.row_count, 300)
            self.assertEqual(table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value, "task-180")

    async def test_input_failure_retains_rows_but_invalidates_live_facts(self):
        app = self.app()
        async with app.run_test(size=(60, 10)) as pilot:
            await pilot.pause()
            app._activate_tab("tab-input")
            table = app.query_one("#panel-input", DataTable)
            app.apply_panel_view("tab-input", {"data": [("daemon", ("Input daemon", "Online")),
                                  ("target", ("Desktop target", "Unavailable")),
                                  ("device:one", ("Controller", "Rearmed"))]})
            await pilot.pause()
            table.move_cursor(row=2, scroll=False)
            app.apply_panel_view("tab-input", {"unavailable": True})
            await pilot.pause()
            self.assertEqual(table.row_count, 3)
            self.assertEqual(str(table.get_row("daemon")[1]), "Unknown")
            self.assertEqual(str(table.get_row("device:one")[1]), "Unknown")
            self.assertEqual(table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value, "device:one")

    async def test_view_key_bursts_and_resize_cannot_restart_collection(self):
        app = self.app()
        async with app.run_test(size=(80, 12)) as pilot:
            await pilot.pause()
            app.collectors_enabled = True
            with patch.object(app, "_start_snapshot", return_value=True) as collect:
                app._activate_tab("tab-global")
                for _ in range(30):
                    app.update_panel_view()
                    app.apply_responsive_layout(60, 9)
                self.assertEqual(collect.call_count, 1)
                app._activate_tab("tab-tasks")
                self.assertEqual(collect.call_count, 2)
                app._activate_tab("tab-global")
                self.assertEqual(collect.call_count, 2)
            app.collectors_enabled = False

    async def test_panel_journal_is_bounded_and_does_not_force_reader_to_bottom(self):
        settings = {"scrollback_rows": 100, "batch_size": 20, "flush_interval_s": 1}
        with patch.object(monitor, "monitor_sources_config", return_value=settings):
            app = self.app()
            async with app.run_test(size=(60, 10)) as pilot:
                await pilot.pause()
                app._activate_tab("tab-logs")
                log = app.query_one("#log-box", monitor.MonitorLog)
                for i in range(200):
                    app.write_monitor_log(log, f"Journal line {i}")
                self.assertEqual(len(app._panel_log_lines), 100)
                app.flush_panel_logs()
                await pilot.pause()
                log.scroll_to(y=3, animate=False, force=True, immediate=True)
                await pilot.pause()
                before = log.scroll_y
                app.flush_panel_logs()
                await pilot.pause()
                self.assertEqual(log.scroll_y, before)
                self.assertEqual(len(app._panel_log_lines), 60)

    async def test_overview_keeps_one_widget_pair_and_selected_view_at_ssot_breakpoints(self):
        app = self.app()
        app.panel_policy.update(overview_columns=90, overview_rows=10)
        with patch.object(app, "_start_snapshot", return_value=True) as collect:
            async with app.run_test(size=(120, 18)) as pilot:
                await pilot.pause()
                agents, system = app.query_one(AgentView), app.query_one(SystemSummary)
                tabs = app.query_one(TabbedContent)
                self.assertEqual(tabs.active, "tab-agents")
                self.assertTrue(app._panel_overview_split)
                self.assertTrue(agents.display and system.display)
                self.assertLessEqual(agents.region.right, system.region.x)
                self.assertGreater(system.size.width, 20)
                ids = [widget.id for widget in app.query("*") if widget.id]
                self.assertEqual(len(ids), len(set(ids)))
                for size, split in (((89, 18), False), ((90, 9), False), ((90, 10), True)):
                    await pilot.resize_terminal(*size)
                    await pilot.pause()
                    self.assertEqual(app._panel_overview_split, split)
                    self.assertTrue(agents.display)
                    self.assertEqual(system.display, split)
                    self.assertIs(app.query_one(AgentView), agents)
                    self.assertIs(app.query_one(SystemSummary), system)
                await pilot.resize_terminal(60, 8)
                await pilot.press("f2")
                self.assertEqual(tabs.active, "tab-global")
                self.assertTrue(system.display)
                self.assertFalse(agents.display)
                await pilot.resize_terminal(120, 18)
                await pilot.pause()
                self.assertEqual(tabs.active, "tab-global")
                self.assertTrue(agents.display and system.display)
                await pilot.resize_terminal(60, 8)
                await pilot.pause()
                self.assertIs(app.focused, app.query_one("#metric-table"))
                for overview_rows, expected_split in ((8, True), (12, False)):
                    app.panel_policy.update(overview_columns=78, overview_rows=overview_rows)
                    await pilot.resize_terminal(98, 9)
                    app.apply_responsive_layout(98, 9)
                    await pilot.pause()
                    self.assertEqual(app._panel_overview_split, expected_split)
                    self.assertEqual(agents.display, expected_split)
                    self.assertTrue(system.display)
                    self.assertLessEqual(system.region.right, 98)
                    self.assertIs(app.query_one(SystemSummary), system)
                collect.assert_not_called()

    async def test_overview_visibility_preserves_inventory_and_summary_scroll(self):
        snapshot = {"agents": [{"agent_id": f"agent-{i:03}", "label": f"Agent {i:03}", "online": True}
                               for i in range(300)], "panes": [], "messages": [], "errors": []}
        app = monitor.MiosMonitorApp(desktop_panel=True, collectors=False, observer=lambda: copy.deepcopy(snapshot))
        app.panel_policy.update(default_view="agents", compact_columns=78, compact_rows=12,
                                overview_columns=90, overview_rows=10, poll_s=5)
        async with app.run_test(size=(120, 18)) as pilot:
            await pilot.pause()
            table = app.query_one("#peer-table", DataTable)
            summary = app.query_one(SystemSummary)
            summary.render_snapshot((10, 20, 30, 0, "1.0"), [(f"Service {i}", 0, True) for i in range(60)])
            await pilot.pause()
            table.move_cursor(row=180, scroll=False)
            table.scroll_to(y=160, animate=False, force=True, immediate=True)
            summary.scroll_to(y=12, animate=False, force=True, immediate=True)
            await pilot.pause()
            key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
            top = table.ordered_rows[int(table.scroll_y)].key.value
            summary_y = summary.scroll_y
            self.assertGreater(summary_y, 0)
            await pilot.resize_terminal(60, 8)
            await pilot.press("f2")
            await pilot.pause()
            await pilot.resize_terminal(120, 18)
            await pilot.pause()
            self.assertEqual(table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value, key)
            self.assertEqual(table.ordered_rows[int(table.scroll_y)].key.value, top)
            self.assertEqual(summary.scroll_y, summary_y)
            services = summary.query_one("#summary-services", DataTable)
            self.assertGreaterEqual(services.region.height, services.virtual_size.height)
            summary.scroll_to(y=summary.max_scroll_y, animate=False, force=True, immediate=True)
            await pilot.pause()
            self.assertLessEqual(services.region.bottom, summary.content_region.bottom)
            self.assertGreater(services.region.bottom, summary.content_region.y)

    async def test_overview_uses_existing_system_job_without_collecting_on_resize(self):
        app = self.app()
        app.panel_policy.update(overview_columns=90, overview_rows=10)
        async with app.run_test(size=(60, 8)) as pilot:
            await pilot.pause()
            app.collectors_enabled = True
            with patch.object(app, "_start_snapshot", return_value=True) as collect:
                app.update_panel_view()
                collect.assert_not_called()
                for _ in range(10):
                    app.apply_responsive_layout(120, 18)
                collect.assert_not_called()
                app.update_panel_view()
                self.assertEqual(collect.call_count, 1)
                self.assertEqual(collect.call_args.args[0], "panel-tab-global")
                app._activate_tab("tab-global")
                app._activate_tab("tab-agents")
                app.apply_responsive_layout(60, 8)
                app.apply_responsive_layout(120, 18)
                app.update_panel_view()
                self.assertEqual(collect.call_count, 1)
            app.collectors_enabled = False

    async def test_lean_chooser_mounts_only_clients_without_collectors_across_input_and_resize(self):
        with patch.object(monitor, "start_status_collectors") as status, \
             patch.object(monitor.MiosMonitorApp, "observe_agents", side_effect=AssertionError("chooser observed agents")) as observe, \
             patch.object(monitor.MiosMonitorApp, "_start_snapshot", side_effect=AssertionError("chooser started collector")) as collect:
            app = monitor.MiosMonitorApp(client_chooser=True, ui_mode="clients", ui_request={"agents": CLIENTS})
            async with app.run_test(size=(50, 16)) as pilot:
                await pilot.pause()
                chooser = app.query_one(monitor.ClientView)
                self.assertEqual(len(app.query(AgentView)), 0)
                self.assertEqual(len(app.query(SystemSummary)), 0)
                self.assertEqual(len(app.query(TabbedContent)), 0)
                self.assertFalse(app.collectors_enabled)
                self.assertFalse(app.enable_agent_federation)
                for size in ((35, 10), (20, 5), (90, 24), (50, 16)):
                    await pilot.resize_terminal(*size)
                    await pilot.press("down", "up", "f1", "f2", "ctrl+right", "minus")
                    await pilot.pause()
                    self.assertIs(app.query_one(monitor.ClientView), chooser)
                    self.assertEqual(app.query_one("#client-table", DataTable).row_count, len(CLIENTS))
                self.assertFalse(app.collectors_started)
                self.assertEqual(app._snapshot_jobs, set())
                self.assertIsNone(app.agent_inventory)
                self.assertIsNone(app.agent_federation)
                observe.assert_not_called()
                collect.assert_not_called()
                status.assert_not_called()

    async def test_lean_chooser_validates_catalogue_then_selects_by_keyboard_and_mouse(self):
        catalogue = [{"name": "ready", "installed": True, "mcp": True},
                     {"name": "missing", "installed": False},
                     {"name": "invalid;command", "installed": True}]
        for selection in ("keyboard", "mouse"):
            app = monitor.MiosMonitorApp(client_chooser=True, ui_mode="clients", ui_request={"agents": catalogue})
            async with app.run_test(size=(50, 12)) as pilot:
                await pilot.pause()
                self.assertEqual(app.query_one("#client-table", DataTable).row_count, 2)
                field = app.query_one("#client-input", Input)
                for invalid in ("missing", "unknown", "invalid;command"):
                    field.value = invalid
                    await pilot.press("enter")
                    self.assertTrue(app.is_running)
                    self.assertIsNone(app.return_value)
                if selection == "keyboard":
                    field.value = "1"
                    await pilot.press("enter")
                else:
                    await pilot.click("#client-table", offset=(4, 1))
                await pilot.pause()
            self.assertEqual(app.return_value, "ready")


if __name__ == "__main__":
    unittest.main(verbosity=2)
