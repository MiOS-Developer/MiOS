#!/usr/bin/env python3
# AI-hint: Adversarial empirical challenge suite for R6.2: Worker Allocation, Recovery, and Multi-Session Isolation.
"""
Empirical adversarial verification suite for:
1. Fibonacci Worker Slots (pre-allocation, claiming, killing, self-healing, resource liveness).
2. Multi-Session Context Resolution & Security Boundaries (_human_tmux_context, invalid sockets, session adoption).
3. mios-terminal --new vs --bare flags and workspace adoption gating.
"""
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path("/").resolve()
RELAY = Path(os.environ.get("MIOS_MCP_SERVER") or ROOT / "usr/libexec/mios/mios-mcp-server")
os.environ["MIOS_TOML"] = str(ROOT / "usr/share/mios/mios.toml")
os.environ["MIOS_RESOLVER_NATIVE"] = "0"


def load_relay():
    loader = importlib.machinery.SourceFileLoader("mios_mcp_aio_relay", str(RELAY))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


relay = load_relay()


class TestFibonacciWorkerSlots(unittest.TestCase):
    def setUp(self):
        if shutil.which("tmux") is None:
            raise unittest.SkipTest("native tmux not found")
        self.tmpdir = tempfile.TemporaryDirectory(prefix="adv-r6-workers-")
        os.chmod(self.tmpdir.name, 0o700)
        self.socket = Path(self.tmpdir.name) / "default"
        self.session_base = f"adv-base-{secrets.token_hex(4)}"
        # Start base session with large geometry (200x60) so high-DPI desktop layout is active
        subprocess.check_call(
            ["tmux", "-S", str(self.socket), "-f", os.devnull,
             "new-session", "-d", "-s", self.session_base, "-x", "200", "-y", "60",
             "-P", "-F", "#{pane_id}", "sleep 3600"],
            timeout=5
        )

    def tearDown(self):
        try:
            subprocess.run(
                ["tmux", "-S", str(self.socket), "-f", os.devnull, "kill-server"],
                timeout=5, capture_output=True
            )
        except Exception:
            pass
        self.tmpdir.cleanup()

    def tmux(self, *args):
        return subprocess.check_output(
            ["tmux", "-S", str(self.socket), "-f", os.devnull, *args],
            text=True, timeout=5
        ).strip()

    def _pane_cells(self, target):
        layout = self.tmux("display-message", "-p", "-t", target, "#{window_layout}")
        return {
            f"%{m.group(5)}": (int(m.group(3)), int(m.group(4)), int(m.group(1)), int(m.group(2)))
            for m in re.finditer(r"(\d+)x(\d+),(\d+),(\d+),(\d+)(?=[,\]\}]|$)", layout)
        }

    def _spawn_dynamic_worker(self, head, slot, owner="test-agent"):
        wp = self.tmux("split-window", "-d", "-v", "-P", "-F", "#{pane_id}", "-t", head, "sleep 3600")
        for k, v in (
            ("@mios-workspace-worker", head),
            ("@mios-workspace-slot", str(slot)),
            ("@mios-workspace-dynamic", "1"),
            ("@mios-mcp-owner", owner),
        ):
            self.tmux("set-option", "-p", "-t", wp, k, v)
        self.tmux("select-pane", "-t", wp, "-T", f"MiOS worker {slot}")
        self.tmux("select-layout", "-t", head, "tiled")
        return wp

    def test_1a_worker_panes_not_prespawned_on_open_or_ensure(self):
        """Verify open and ensure do NOT pre-spawn empty sleep infinity worker panes (only 3 core panes + storage anchor)."""
        target_session = f"adv-ws-{secrets.token_hex(4)}"
        result = relay._workspace_call(
            "open",
            socket=str(self.socket),
            session=target_session,
            directory="/tmp",
            latch=f"mios-latch-{secrets.token_hex(4)}",
            command="sleep 3600",
            observer_command="sleep 3600",
            adapter=f"{sys.executable} {RELAY}"
        )
        self.assertIn("head", result)
        head = result["head"]
        self.assertEqual(result.get("worker_panes"), 0, f"Expected 0 pre-spawned worker panes, got {result}")

        self.tmux("resize-window", "-t", head, "-x", "200", "-y", "60")
        res_desktop = relay._workspace_call(
            "resize",
            socket=str(self.socket),
            session=target_session,
            head=head,
            adapter=f"{sys.executable} {RELAY}",
            observer_command="sleep 3600"
        )
        self.assertEqual(res_desktop.get("layout"), "desktop")
        self.assertEqual(res_desktop.get("workers"), [])

        win_panes = self.tmux(
            "list-panes", "-t", head, "-F",
            "#{pane_id}\t#{@mios-workspace-worker}\t#{pane_title}"
        ).splitlines()
        self.assertEqual(len(win_panes), 3, f"Expected 3 core panes (head, observer, resource), got: {win_panes}")
        for line in win_panes:
            parts = line.split("\t")
            self.assertEqual(parts[1], "", f"Unexpected worker tag on open: {line}")
            self.assertNotIn("Worker", parts[2], f"Unexpected empty worker title on open: {line}")

        all_panes = self.tmux(
            "list-panes", "-a", "-F",
            "#{pane_id}\t#{@mios-workspace-worker}\t#{@mios-workspace-reserved}\t#{@mios-workspace-anchor-for}\t#{pane_start_command}"
        ).splitlines()
        worker_panes = [p for p in all_panes if len(p.split("\t")) >= 2 and p.split("\t")[1] == head]
        self.assertEqual(len(worker_panes), 0, f"Expected 0 worker panes across server, found: {worker_panes}")
        sleep_inf = [p for p in all_panes if "sleep infinity" in p]
        self.assertEqual(len(sleep_inf), 1, f"Expected only storage anchor to run sleep infinity, found: {sleep_inf}")
        self.assertEqual(sleep_inf[0].split("\t")[3], head)

    def test_1b_dynamic_worker_spawn_spiral_and_retire(self):
        """Verify dynamic worker allocation places workers under the observer in the Fibonacci spiral and retiring restores 3-pane layout."""
        target_session = f"adv-claim-{secrets.token_hex(4)}"
        result = relay._workspace_call(
            "open",
            socket=str(self.socket),
            session=target_session,
            directory="/tmp",
            latch=f"mios-latch-{secrets.token_hex(4)}",
            command="sleep 3600",
            observer_command="sleep 3600",
            adapter=f"{sys.executable} {RELAY}"
        )
        head = result["head"]
        self.tmux("resize-window", "-t", head, "-x", "200", "-y", "60")
        res0 = relay._workspace_call(
            "resize",
            socket=str(self.socket),
            session=target_session,
            head=head,
            adapter=f"{sys.executable} {RELAY}",
            observer_command="sleep 3600"
        )
        obs = res0["observer"]
        cells0 = self._pane_cells(head)
        self.assertEqual(cells0[obs][3], 60, "Observer should fill 100% height before workers spawn")

        workers = []
        for slot in (1, 2, 3):
            wp = self._spawn_dynamic_worker(head, slot, owner=f"test-agent-worker-{slot}")
            workers.append(wp)
            res = relay._workspace_call(
                "resize",
                socket=str(self.socket),
                session=target_session,
                head=head,
                adapter=f"{sys.executable} {RELAY}",
                observer_command="sleep 3600"
            )
            self.assertEqual(res.get("layout"), "desktop")
            self.assertEqual(res.get("workers"), workers)
            cells = self._pane_cells(head)
            head_cell = cells[head]
            obs_cell = cells[obs]
            self.assertEqual(obs_cell[0], head_cell[2] + 1)
            self.assertEqual(obs_cell[1], 0)
            self.assertLess(obs_cell[3], 20, "Observer should slim down when workers are active")
            for wid in workers:
                w_cell = cells[wid]
                self.assertGreaterEqual(w_cell[0], head_cell[2] + 1, f"Worker {wid} must be in right column")
                self.assertGreaterEqual(w_cell[1], obs_cell[3] + 1, f"Worker {wid} must be underneath observer")

        for wid in workers:
            self.tmux("kill-pane", "-t", wid)
        res_retired = relay._workspace_call(
            "resize",
            socket=str(self.socket),
            session=target_session,
            head=head,
            adapter=f"{sys.executable} {RELAY}",
            observer_command="sleep 3600"
        )
        self.assertEqual(res_retired.get("workers"), [])
        cells_after = self._pane_cells(head)
        self.assertEqual(len(cells_after), 3)
        self.assertEqual(cells_after[obs][3], 60, "Observer must regain 100% height after workers retire")

    def test_1c_all_workers_killed_self_healing_via_resize(self):
        """When all dynamic worker panes are killed, --workspace-resize self-heals back to the 3-pane desktop layout with 0 dead/empty workers."""
        target_session = f"adv-killall-{secrets.token_hex(4)}"
        result = relay._workspace_call(
            "open",
            socket=str(self.socket),
            session=target_session,
            directory="/tmp",
            latch=f"mios-latch-{secrets.token_hex(4)}",
            command="sleep 3600",
            observer_command="sleep 3600",
            adapter=f"{sys.executable} {RELAY}"
        )
        head = result["head"]
        self.tmux("resize-window", "-t", head, "-x", "200", "-y", "60")

        worker_panes = [self._spawn_dynamic_worker(head, slot) for slot in range(1, 5)]
        res_with_workers = relay._workspace_call(
            "resize",
            socket=str(self.socket),
            session=target_session,
            head=head,
            adapter=f"{sys.executable} {RELAY}",
            observer_command="sleep 3600"
        )
        self.assertEqual(len(res_with_workers.get("workers", [])), 4)

        # Kill all 4 dynamic worker panes
        for wp in worker_panes:
            self.tmux("kill-pane", "-t", wp)

        # Trigger resize
        resize_result = relay._workspace_call(
            "resize",
            socket=str(self.socket),
            session=target_session,
            head=head,
            adapter=f"{sys.executable} {RELAY}",
            observer_command="sleep 3600"
        )
        self.assertTrue(resize_result.get("managed"))
        self.assertEqual(resize_result.get("layout"), "desktop")
        self.assertEqual(resize_result.get("workers"), [], "Resize must heal to 0 workers rather than re-spawning sleep reservations")

        panes_healed = self.tmux(
            "list-panes", "-a", "-F",
            "#{pane_id}\t#{@mios-workspace-worker}\t#{@mios-workspace-slot}"
        ).splitlines()
        new_workers = [p.split("\t") for p in panes_healed if len(p.split("\t")) >= 2 and p.split("\t")[1] == head]
        self.assertEqual(len(new_workers), 0, f"Expected 0 orphan/respawned workers, got: {new_workers}")
        win_panes = self.tmux("list-panes", "-t", head, "-F", "#{pane_id}").splitlines()
        self.assertEqual(len(win_panes), 3, f"Expected 3 healed core panes in desktop layout, got: {win_panes}")

    def test_1d_claim_and_kill_worker_preserves_remaining_and_workspace_integrity(self):
        """Spawn 4 dynamic workers, kill slot 2, and trigger resize: verifies active workers 1, 3, 4 survive uncorrupted."""
        target_session = f"adv-claim-kill-{secrets.token_hex(4)}"
        result = relay._workspace_call(
            "open",
            socket=str(self.socket),
            session=target_session,
            directory="/tmp",
            latch=f"mios-latch-{secrets.token_hex(4)}",
            command="sleep 3600",
            observer_command="sleep 3600",
            adapter=f"{sys.executable} {RELAY}"
        )
        head = result["head"]

        # Resize window to High-DPI Desktop (200x60)
        self.tmux("resize-window", "-t", head, "-x", "200", "-y", "60")
        relay._workspace_call("resize", socket=str(self.socket), session=target_session, head=head, adapter=f"{sys.executable} {RELAY}")

        w1 = self._spawn_dynamic_worker(head, 1, owner="active-agent-1")
        w2 = self._spawn_dynamic_worker(head, 2, owner="active-agent-2")
        w3 = self._spawn_dynamic_worker(head, 3, owner="active-agent-3")
        w4 = self._spawn_dynamic_worker(head, 4, owner="active-agent-4")
        relay._workspace_call("resize", socket=str(self.socket), session=target_session, head=head, adapter=f"{sys.executable} {RELAY}")

        # Kill slot 2
        self.tmux("kill-pane", "-t", w2)

        # Trigger resize
        res_after_kill = relay._workspace_call("resize", socket=str(self.socket), session=target_session, head=head, adapter=f"{sys.executable} {RELAY}")
        self.assertTrue(res_after_kill.get("managed"))
        self.assertEqual(res_after_kill.get("layout"), "desktop")
        # Workers list should now contain the remaining 3 workers: slot 1, slot 3, slot 4
        self.assertEqual(res_after_kill.get("workers", []), [w1, w3, w4])

        # Verify slot 1 metadata is untouched
        owner = self.tmux("display-message", "-p", "-t", w1, "#{@mios-mcp-owner}")
        self.assertEqual(owner, "active-agent-1", "Active worker pane owner must survive resize and sibling kill")

    def test_1e_resource_pane_kill_and_liveness_regeneration(self):
        """Test killing resource pane (btop/sleep); verify liveness probe detects dead pane and regenerates cleanly without ownership errors."""
        target_session = f"adv-reskill-{secrets.token_hex(4)}"
        result = relay._workspace_call(
            "open",
            socket=str(self.socket),
            session=target_session,
            directory="/tmp",
            latch=f"mios-latch-{secrets.token_hex(4)}",
            command="sleep 3600",
            observer_command="sleep 3600",
            adapter=f"{sys.executable} {RELAY}"
        )
        head = result["head"]

        # Resize to Desktop High-DPI (200x60) so desktop layout with resource pane is generated
        self.tmux("resize-window", "-t", head, "-x", "200", "-y", "60")
        res_desktop = relay._workspace_call("resize", socket=str(self.socket), session=target_session, head=head, adapter=f"{sys.executable} {RELAY}")
        self.assertEqual(res_desktop.get("layout"), "desktop")

        window_id = self.tmux("display-message", "-p", "-t", head, "#{window_id}")
        resource_pane = self.tmux("display-message", "-p", "-t", window_id, "#{@mios-workspace-resource}")
        self.assertTrue(resource_pane.startswith("%"), f"Expected resource pane id, got {resource_pane}")

        # Check resource pane owner
        owner = self.tmux("display-message", "-p", "-t", resource_pane, "#{@mios-workspace-resource-for}")
        self.assertEqual(owner, head)

        # Kill the resource pane!
        self.tmux("kill-pane", "-t", resource_pane)

        # Verify resource pane is dead (display-message -t resource_pane #{pane_id} does not return resource_pane)
        try:
            current_id = self.tmux("display-message", "-p", "-t", resource_pane, "#{pane_id}")
        except Exception:
            current_id = ""
        self.assertNotEqual(current_id, resource_pane, "Resource pane should no longer resolve to itself once killed")

        # Trigger workspace resize: must NOT throw "workspace resource ownership changed"!
        resize_result = relay._workspace_call(
            "resize",
            socket=str(self.socket),
            session=target_session,
            head=head,
            adapter=f"{sys.executable} {RELAY}",
            observer_command="sleep 3600"
        )
        self.assertTrue(resize_result.get("managed"))
        self.assertEqual(resize_result.get("layout"), "desktop")

        # Verify a new resource pane was spawned and tagged
        new_resource = self.tmux("display-message", "-p", "-t", window_id, "#{@mios-workspace-resource}")
        self.assertTrue(new_resource.startswith("%"), f"Expected new resource pane id, got {new_resource}")
        self.assertNotEqual(new_resource, resource_pane, "New resource pane must have different id")

        # Verify new resource pane is alive and tagged for head
        new_owner = self.tmux("display-message", "-p", "-t", new_resource, "#{@mios-workspace-resource-for}")
        self.assertEqual(new_owner, head)

    def test_1f_negative_control_prespawned_or_misplaced_worker_detected(self):
        """Negative control: verify planted sleep-infinity worker reservation or non-spiral placement triggers assertion failure."""
        target_session = f"adv-neg-{secrets.token_hex(4)}"
        result = relay._workspace_call(
            "open",
            socket=str(self.socket),
            session=target_session,
            directory="/tmp",
            latch=f"mios-latch-{secrets.token_hex(4)}",
            command="sleep 3600",
            observer_command="sleep 3600",
            adapter=f"{sys.executable} {RELAY}"
        )
        head = result["head"]
        self.tmux("resize-window", "-t", head, "-x", "200", "-y", "60")
        res0 = relay._workspace_call(
            "resize",
            socket=str(self.socket),
            session=target_session,
            head=head,
            adapter=f"{sys.executable} {RELAY}",
            observer_command="sleep 3600"
        )
        obs = res0["observer"]

        def assert_no_prespawn():
            all_panes = self.tmux(
                "list-panes", "-a", "-F",
                "#{pane_id}\t#{@mios-workspace-worker}\t#{@mios-workspace-reserved}\t#{pane_title}"
            ).splitlines()
            leaked = [p for p in all_panes if len(p.split("\t")) >= 2 and p.split("\t")[1] == head]
            self.assertEqual(len(leaked), 0, f"Pre-spawned worker detected: {leaked}")

        # Positive baseline passes
        assert_no_prespawn()

        # Negative control 1: plant a pre-spawned sleep infinity reservation and verify assert_no_prespawn fails
        planted = self.tmux("split-window", "-d", "-P", "-F", "#{pane_id}", "-t", head, "exec /usr/bin/sleep infinity")
        pid = self.tmux("display-message", "-p", "-t", planted, "#{pane_pid}")
        self.tmux("set-option", "-p", "-t", planted, "@mios-workspace-worker", head)
        self.tmux("set-option", "-p", "-t", planted, "@mios-workspace-slot", "1")
        self.tmux("set-option", "-p", "-t", planted, "@mios-workspace-reserved", pid)
        self.tmux("select-pane", "-t", planted, "-T", "Worker 1 (empty)")
        with self.assertRaises(AssertionError):
            assert_no_prespawn()

        # Negative control 2: corrupt layout to tiled (breaking Fibonacci spiral placement under observer) and verify spiral check fails
        self.tmux("select-layout", "-t", head, "tiled")
        cells_corrupted = self._pane_cells(head)

        def assert_spiral_under_observer(cells, worker_id):
            head_cell = cells[head]
            obs_cell = cells[obs]
            w_cell = cells[worker_id]
            self.assertEqual(obs_cell[0], head_cell[2] + 1)
            self.assertEqual(obs_cell[1], 0)
            self.assertGreaterEqual(w_cell[0], head_cell[2] + 1)
            self.assertGreaterEqual(w_cell[1], obs_cell[3] + 1)

        with self.assertRaises(AssertionError):
            assert_spiral_under_observer(cells_corrupted, planted)

        # Clean up planted pane and verify resize restores valid state
        self.tmux("kill-pane", "-t", planted)
        relay._workspace_call("resize", socket=str(self.socket), session=target_session, head=head, adapter=f"{sys.executable} {RELAY}")
        assert_no_prespawn()

    def test_1g_tmux_bridge_visible_allocate_retire_and_legacy_claim_release(self):
        """Exercise _TmuxBridge._visible_allocate / _visible_retire directly and legacy claim / release on pre-reserved slots."""
        import asyncio
        target_session = f"adv-bridge-{secrets.token_hex(4)}"
        result = relay._workspace_call(
            "open",
            socket=str(self.socket),
            session=target_session,
            directory="/tmp",
            latch=f"mios-latch-{secrets.token_hex(4)}",
            command="sleep 3600",
            observer_command="sleep 3600",
            adapter=f"{sys.executable} {RELAY}"
        )
        head = result["head"]
        self.tmux("resize-window", "-t", head, "-x", "200", "-y", "60")
        res0 = relay._workspace_call(
            "resize",
            socket=str(self.socket),
            session=target_session,
            head=head,
            adapter=f"{sys.executable} {RELAY}",
            observer_command="sleep 3600"
        )
        obs = res0["observer"]
        win_id = self.tmux("display-message", "-p", "-t", head, "#{window_id}")
        srv_pid = self.tmux("display-message", "-p", "-t", head, "#{pid}")

        bridge = relay._TmuxBridge(
            {"allowed_tools": [], "history_limit": 2000, "helper_prompt": "$ ", "helper_term": "xterm-256color"},
            desktop=True
        )
        bridge.visible = {"socket": str(self.socket), "pane": head, "window": win_id, "session": target_session, "tmux": f"{self.socket},{srv_pid},0"}
        bridge.env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "TERM": "xterm-256color"}

        # Allocate 3 dynamic workers via real _visible_allocate
        s1 = bridge._visible_allocate(1)
        s2 = bridge._visible_allocate(2)
        s3 = bridge._visible_allocate(3)
        self.assertEqual((s1, s2, s3), (1, 2, 3))
        self.assertEqual(len(bridge.visible_slots), 3)
        for slot in (1, 2, 3):
            rec = bridge.visible_slots[slot]
            self.assertTrue(rec["dynamic"])
            dyn_tag = self.tmux("display-message", "-p", "-t", rec["pane"], "#{@mios-workspace-dynamic}")
            self.assertEqual(dyn_tag, "1")
        cells = self._pane_cells(head)
        self.assertLess(cells[obs][3], 20, "Observer should slim down when _visible_allocate spawns workers")

        # Retire slot 2, leaving slots 1 and 3
        asyncio.run(bridge._visible_retire(2))
        self.assertEqual(sorted(bridge.visible_slots.keys()), [1, 3])
        # Retire all remaining slots and verify 3-pane layout with full-height observer is restored
        asyncio.run(bridge._visible_retire("all"))
        self.assertEqual(bridge.visible_slots, {})
        cells_after = self._pane_cells(head)
        self.assertEqual(len(cells_after), 3)
        self.assertEqual(cells_after[obs][3], 60)

        # Also exercise legacy claim / release on a pre-reserved slot for backward compatibility
        legacy = self.tmux("split-window", "-d", "-v", "-P", "-F", "#{pane_id}", "-t", head, "exec /usr/bin/sleep infinity")
        legacy_pid = self.tmux("display-message", "-p", "-t", legacy, "#{pane_pid}")
        for k, v in (
            ("@mios-workspace-worker", head),
            ("@mios-workspace-slot", "1"),
            ("@mios-workspace-reserved", legacy_pid),
        ):
            self.tmux("set-option", "-p", "-t", legacy, k, v)
        claimed = relay._workspace_call(
            "claim",
            {"socket": str(self.socket), "pane": head, "window": win_id, "session": target_session},
            slot=1,
            owner="legacy-owner",
            command="sleep 3600"
        )
        self.assertTrue(claimed.get("managed"))
        self.assertEqual(claimed.get("pane"), legacy)
        released = relay._workspace_call(
            "release",
            {"socket": str(self.socket), "pane": head, "window": win_id, "session": target_session},
            slot=1,
            owner="legacy-owner",
            pane=legacy
        )
        self.assertTrue(released.get("managed"))
        self.tmux("kill-pane", "-t", legacy)
        relay._workspace_call("resize", socket=str(self.socket), session=target_session, head=head, adapter=f"{sys.executable} {RELAY}")


class TestMultiSessionContextResolution(unittest.TestCase):
    def setUp(self):
        if shutil.which("tmux") is None:
            raise unittest.SkipTest("native tmux not found")
        self.tmpdir = tempfile.TemporaryDirectory(prefix="adv-r6-human-")
        os.chmod(self.tmpdir.name, 0o700)
        self.human_socket = Path(self.tmpdir.name) / "mios-human"
        subprocess.check_call(
            ["tmux", "-S", str(self.human_socket), "-f", os.devnull,
             "new-session", "-d", "-s", "tmux-os", "-x", "80", "-y", "24",
             "-P", "-F", "#{pane_id}", "sleep 3600"],
            timeout=5
        )

    def tearDown(self):
        try:
            subprocess.run(
                ["tmux", "-S", str(self.human_socket), "-f", os.devnull, "kill-server"],
                timeout=5, capture_output=True
            )
        except Exception:
            pass
        self.tmpdir.cleanup()

    def test_2a_human_tmux_context_with_non_mios_session_names(self):
        """Verify _human_tmux_context permits non-'mios' sessions ('tmux-os', 'tmux-os-2', 'test-human') on mios-human socket."""
        head_pane = subprocess.check_output(
            ["tmux", "-S", str(self.human_socket), "-f", os.devnull, "display-message", "-p", "-t", "tmux-os:0.0", "#{pane_id}"],
            text=True
        ).strip()
        ctx = relay._human_tmux_context(str(self.human_socket), head_pane)
        self.assertEqual(ctx["session"], "tmux-os")
        self.assertEqual(ctx["pane"], head_pane)
        self.assertEqual(ctx["socket"], str(self.human_socket))

        # 2. Create isolated test sessions on private mios-human socket: 'tmux-os-2' and 'test-human'
        for sname in ["tmux-os-2", "test-human"]:
            pane_id = subprocess.check_output(
                ["tmux", "-S", str(self.human_socket), "-f", os.devnull, "new-session", "-d", "-s", sname, "-P", "-F", "#{pane_id}", "sleep 3600"],
                text=True
            ).strip()
            try:
                # Call _human_tmux_context unwitnessed
                res_ctx = relay._human_tmux_context(str(self.human_socket), pane_id)
                self.assertEqual(res_ctx["session"], sname)
                self.assertEqual(res_ctx["pane"], pane_id)
            finally:
                subprocess.run(
                    ["tmux", "-S", str(self.human_socket), "-f", os.devnull, "kill-session", "-t", f"={sname}"],
                    capture_output=True
                )

    def test_2b_storage_session_resolution_to_main_window(self):
        """Verify a parked worker pane in mios-workspace-* resolves its session to the main window's session name."""
        head_pane = subprocess.check_output(
            ["tmux", "-S", str(self.human_socket), "-f", os.devnull, "display-message", "-p", "-t", "tmux-os:0.0", "#{pane_id}"],
            text=True
        ).strip()
        main_win = subprocess.check_output(
            ["tmux", "-S", str(self.human_socket), "-f", os.devnull, "display-message", "-p", "-t", head_pane, "#{window_id}"],
            text=True
        ).strip()
        subprocess.check_call(
            ["tmux", "-S", str(self.human_socket), "-f", os.devnull, "set-option", "-w", "-t", main_win, "@mios-workspace-head", head_pane]
        )
        subprocess.check_call(
            ["tmux", "-S", str(self.human_socket), "-f", os.devnull, "set-option", "-p", "-t", head_pane, "@mios-workspace-window", main_win]
        )
        parked_worker = subprocess.check_output(
            ["tmux", "-S", str(self.human_socket), "-f", os.devnull, "new-session", "-d", "-s", "mios-workspace-0", "-P", "-F", "#{pane_id}", "sleep 3600"],
            text=True
        ).strip()
        subprocess.check_call(
            ["tmux", "-S", str(self.human_socket), "-f", os.devnull, "set-option", "-p", "-t", parked_worker, "@mios-workspace-worker", head_pane]
        )
        ctx = relay._human_tmux_context(str(self.human_socket), parked_worker)
        self.assertEqual(ctx["session"], "tmux-os")
        self.assertEqual(ctx["pane"], parked_worker)

    def test_2c_security_boundary_insecure_socket_permissions_rejected(self):
        """Verify _human_tmux_context rejects sockets in directories with permissive permissions (not 0700)."""
        bad_dir = tempfile.TemporaryDirectory(prefix="adv-bad-perm-")
        try:
            # Set mode to 0777 (world-writable)
            os.chmod(bad_dir.name, 0o777)
            bad_sock = Path(bad_dir.name) / "mios-human"
            subprocess.run(
                ["tmux", "-S", str(bad_sock), "-f", os.devnull, "new-session", "-d", "-s", "badperm", "sleep 3600"],
                capture_output=True, check=True
            )
            pane = subprocess.check_output(
                ["tmux", "-S", str(bad_sock), "-f", os.devnull, "display-message", "-p", "#{pane_id}"],
                text=True
            ).strip()

            with self.assertRaises(ValueError) as err:
                relay._human_tmux_context(str(bad_sock), pane)
            self.assertIn("MiOS desktop socket must be private and owned by the caller", str(err.exception))
        finally:
            subprocess.run(["tmux", "-S", str(bad_sock), "-f", os.devnull, "kill-server"], capture_output=True)
            bad_dir.cleanup()

    def test_2d_security_boundary_non_native_socket_unwitnessed_rejected(self):
        """Verify non-native unwitnessed socket is rejected with invalid binding error."""
        tmpdir = tempfile.TemporaryDirectory(prefix="adv-non-native-")
        os.chmod(tmpdir.name, 0o700)
        sock = Path(tmpdir.name) / "custom-socket"
        try:
            pane = subprocess.check_output(
                ["tmux", "-S", str(sock), "-f", os.devnull, "new-session", "-d", "-s", "s1", "-P", "-F", "#{pane_id}", "sleep 3600"],
                text=True
            ).strip()

            # Ensure ambient TMUX does not witness this
            orig_tmux = os.environ.pop("TMUX", None)
            try:
                with self.assertRaises(ValueError) as err:
                    relay._human_tmux_context(str(sock), pane)
                self.assertIn("invalid native MiOS desktop tmux binding", str(err.exception))
            finally:
                if orig_tmux:
                    os.environ["TMUX"] = orig_tmux
        finally:
            subprocess.run(["tmux", "-S", str(sock), "-f", os.devnull, "kill-server"], capture_output=True)
            tmpdir.cleanup()

    def test_2e_non_utf8_locale_and_format_directory_safe(self):
        """Verify _human_tmux_context and workspace open/resize succeed under LC_ALL=C and do not format-expand directory names."""
        fmt_dir = Path(self.tmpdir.name) / "dir#{session_name}"
        fmt_dir.mkdir(exist_ok=True)
        orig_lc = os.environ.get("LC_ALL")
        orig_lang = os.environ.get("LANG")
        os.environ["LC_ALL"] = "C"
        os.environ["LANG"] = "C"
        try:
            head_pane = subprocess.check_output(
                ["tmux", "-u", "-S", str(self.human_socket), "-f", os.devnull, "display-message", "-p", "-t", "tmux-os:0.0", "#{pane_id}"],
                text=True
            ).strip()
            ctx = relay._human_tmux_context(str(self.human_socket), head_pane)
            self.assertEqual(ctx["session"], "tmux-os")
            self.assertEqual(ctx["pane"], head_pane)

            ws_res = relay._workspace_call(
                "open",
                socket=str(self.human_socket),
                session="c-locale-ws",
                directory=str(fmt_dir),
                latch=f"mios-latch-{secrets.token_hex(4)}",
                command="sleep 3600",
                observer_command="sleep 3600",
                adapter=f"{sys.executable} {RELAY}"
            )
            ws_head = ws_res["head"]
            subprocess.check_call(["tmux", "-u", "-S", str(self.human_socket), "-f", os.devnull, "resize-window", "-t", ws_head, "-x", "200", "-y", "60"])
            rsz = relay._workspace_call(
                "resize",
                socket=str(self.human_socket),
                session="c-locale-ws",
                head=ws_head,
                directory=str(fmt_dir),
                adapter=f"{sys.executable} {RELAY}",
                observer_command="sleep 3600"
            )
            self.assertTrue(rsz.get("managed"))
            self.assertEqual(rsz.get("layout"), "desktop")
            head_cwd = subprocess.check_output(
                ["tmux", "-u", "-S", str(self.human_socket), "-f", os.devnull, "display-message", "-p", "-t", ws_head, "#{pane_current_path}"],
                text=True
            ).strip()
            self.assertEqual(head_cwd, str(fmt_dir))
        finally:
            if orig_lc is None:
                os.environ.pop("LC_ALL", None)
            else:
                os.environ["LC_ALL"] = orig_lc
            if orig_lang is None:
                os.environ.pop("LANG", None)
            else:
                os.environ["LANG"] = orig_lang


class TestMiosTerminalFlags(unittest.TestCase):
    def test_3a_cli_help_documents_new_and_bare(self):
        """Verify /usr/libexec/mios/mios-terminal --help documents both --new and --bare."""
        proc = subprocess.run(
            ["/usr/libexec/mios/mios-terminal", "--help"],
            capture_output=True, text=True, check=True
        )
        self.assertIn("--new", proc.stdout)
        self.assertIn("--bare", proc.stdout)
        self.assertIn("--session", proc.stdout)

    def test_3b_bare_sets_session_bare_env_and_new_does_not(self):
        """Verify mios-terminal --bare sets MIOS_SESSION_BARE=1 while --new creates a standard session without it."""
        script = Path("/usr/libexec/mios/mios-terminal").read_text()
        self.assertIn("_mios_bare_opt=0", script)
        self.assertIn("--bare) _mios_new=1; _mios_bare_opt=1;", script)
        self.assertIn("--new) _mios_new=1;", script)
        self.assertIn("(( _mios_bare_opt )) && _mios_bare=(-e MIOS_SESSION_BARE=1)", script)

    def test_3c_workspace_ensure_skips_bare_session(self):
        """Verify --workspace-ensure checks MIOS_SESSION_BARE=1 and exits 0 without adopting."""
        tmpdir = tempfile.TemporaryDirectory(prefix="adv-bare-ensure-")
        os.chmod(tmpdir.name, 0o700)
        sock = Path(tmpdir.name) / "default"
        try:
            # Create session with MIOS_SESSION_BARE=1
            pane = subprocess.check_output(
                ["tmux", "-S", str(sock), "-f", os.devnull, "new-session", "-d", "-s", "bare-test",
                 "-e", "MIOS_SESSION_BARE=1", "-P", "-F", "#{pane_id}", "sleep 3600"],
                text=True
            ).strip()

            # Run --workspace-ensure with TMUX set to this pane
            env = dict(os.environ, TMUX=f"{sock},1234,0", TMUX_PANE=pane)
            proc = subprocess.run(
                [sys.executable, str(RELAY), "--workspace-ensure"],
                env=env, capture_output=True, text=True
            )
            self.assertEqual(proc.returncode, 0)

            # Verify no observer or workspace tags were added
            window_head = subprocess.check_output(
                ["tmux", "-S", str(sock), "-f", os.devnull, "display-message", "-p", "-t", pane, "#{@mios-workspace-head}"],
                text=True
            ).strip()
            self.assertEqual(window_head, "", "Bare session should not have @mios-workspace-head set")
        finally:
            subprocess.run(["tmux", "-S", str(sock), "-f", os.devnull, "kill-server"], capture_output=True)
            tmpdir.cleanup()



class TestMcpToolResults(unittest.IsolatedAsyncioTestCase):
    """Array payloads stay JSON text; only objects use MCP structured content."""

    @staticmethod
    def bridge():
        from unittest.mock import AsyncMock, Mock
        bridge = object.__new__(relay._TmuxBridge)
        bridge.visible = None
        bridge.visible_slots = {}
        bridge.owner_id = "mios-result-test"
        bridge.config = {"timeout_s": 5}
        bridge.retirement_tasks = {}
        bridge.client = Mock()
        bridge.client.call_tool = AsyncMock()
        bridge._schedule_retirement = Mock()
        return bridge

    def assert_wire(self, result, expected):
        from mcp import types
        from mcp_types.methods import serialize_server_result
        wire = result.model_dump(by_alias=True, exclude_none=True)
        # Exercise the server serializer too: SDK result models may accept broader
        # values than the negotiated wire version.
        serialize_server_result("tools/call", "2025-03-26", wire)
        self.assertEqual(json.loads(result.content[0].text), expected)
        self.assertEqual(wire.get("structuredContent"), expected if isinstance(expected, dict) else None)
        types.CallToolResult.model_validate(wire)

    async def test_visible_list_slots_preserves_array_as_json_text(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        bridge = self.bridge()
        bridge.visible = {"socket": "/mios-private-result-test"}
        bridge.visible_slots = {2: {"pane": "%7", "upstream": 9}}
        with patch.object(relay.subprocess, "run", return_value=SimpleNamespace(stdout="%7|mios-result-test|0\n%8|another-owner|0\n")):
            result = await bridge._upstream_call("list-slots", {})
        self.assert_wire(result, [{"slot": 2, "paneId": "%7", "isolated": False, "origin": "created", "isAlive": True}])

    async def test_visible_rewrite_keeps_array_out_of_structured_content(self):
        from mcp import types
        bridge = self.bridge()
        bridge.visible = {"socket": "/mios-private-result-test"}
        payload = [{"slot": 1, "action": "killed"}]
        bridge.client.call_tool.return_value = types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(payload))])
        self.assert_wire(await bridge._upstream_call("notify", {}), payload)

    async def test_list_slots_and_close_pane_serialize_array_results(self):
        from mcp import types
        from unittest.mock import AsyncMock
        for upstream, args, slot, payload in (
            ("list-slots", {}, None, [{"slot": 1, "isAlive": True}]),
            ("list-slots", {}, None, []),
            ("close-pane", {"slot": 1}, 1, [{"slot": 1, "action": "killed"}]),
        ):
            with self.subTest(upstream=upstream, payload=payload):
                bridge = self.bridge()
                bridge._upstream_call = AsyncMock(return_value=types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(payload))]))
                self.assert_wire(await bridge._call_locked(upstream, args, slot), payload)

    async def test_object_results_still_include_structured_content(self):
        from mcp import types
        from unittest.mock import AsyncMock
        bridge = self.bridge()
        payload = {"slot": 1, "isAlive": True}
        bridge._upstream_call = AsyncMock(return_value=types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(payload))]))
        self.assert_wire(await bridge._call_locked("pane-state", {"slot": 1}, 1), payload)

    def test_negative_array_structured_content_is_rejected_by_wire_schema(self):
        from mcp_types.methods import serialize_server_result
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            serialize_server_result("tools/call", "2025-03-26", {"content": [{"type": "text", "text": "[]"}], "structuredContent": []})


if __name__ == "__main__":
    unittest.main(verbosity=2)
