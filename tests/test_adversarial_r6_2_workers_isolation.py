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
RELAY = ROOT / "usr/libexec/mios/mios-mcp-server"
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

    def test_1a_worker_panes_preallocated_and_tagged(self):
        """Verify all 4 reserved worker panes are pre-allocated with sleep infinity and properly tagged."""
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
        self.assertEqual(result.get("worker_panes"), 4, f"Expected 4 worker panes, got {result}")

        # List all panes across all sessions in this server
        panes = self.tmux(
            "list-panes", "-a", "-F",
            "#{pane_id}\t#{@mios-workspace-worker}\t#{@mios-workspace-slot}\t#{@mios-workspace-reserved}\t#{pane_pid}\t#{pane_current_command}\t#{pane_title}"
        ).splitlines()

        worker_panes = []
        for line in panes:
            parts = line.split("\t")
            if len(parts) >= 7 and parts[1] == head:
                worker_panes.append({
                    "pane_id": parts[0],
                    "worker_for": parts[1],
                    "slot": parts[2],
                    "reserved": parts[3],
                    "pid": parts[4],
                    "command": parts[5],
                    "title": parts[6]
                })

        self.assertEqual(len(worker_panes), 4, f"Expected 4 worker panes, found: {worker_panes}")
        slots_found = set()
        for w in worker_panes:
            slot_num = int(w["slot"])
            slots_found.add(slot_num)
            self.assertEqual(w["worker_for"], head)
            self.assertEqual(w["reserved"], w["pid"], f"Reserved PID should match pane PID: {w}")
            self.assertEqual(w["command"], "sleep", f"Expected command sleep, got {w['command']}")
            self.assertEqual(w["title"], f"Worker {slot_num} (empty)")

        self.assertEqual(slots_found, {1, 2, 3, 4}, f"Expected slots 1..4, found {slots_found}")

    def test_1b_claim_and_release_worker_pane(self):
        """Test claiming a worker pane, verifying metadata update and respawn, then releasing it."""
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

        # Claim slot 2
        claim_receipt = relay._workspace_call(
            "claim",
            socket=str(self.socket),
            session=target_session,
            head=head,
            slot=2,
            owner="test-agent-worker-2",
            command="sleep 1800"
        )
        self.assertTrue(claim_receipt.get("managed"))
        claimed_pane = claim_receipt["pane"]

        # Wait up to 1s for command to launch
        cmd = ""
        for _ in range(10):
            cmd = self.tmux("display-message", "-p", "-t", claimed_pane, "#{pane_current_command}")
            if cmd == "sleep":
                break
            time.sleep(0.1)

        # Check metadata on claimed pane
        owner = self.tmux("display-message", "-p", "-t", claimed_pane, "#{@mios-mcp-owner}")
        reserved = self.tmux("display-message", "-p", "-t", claimed_pane, "#{@mios-workspace-reserved}")
        self.assertEqual(owner, "test-agent-worker-2", "Owner should be set")
        self.assertEqual(reserved, "", "Reserved PID should be cleared on claim")
        self.assertEqual(cmd, "sleep", f"Command should be sleep, got {cmd}")

        # Attempt to claim already-claimed slot -> should return managed=False
        second_claim = relay._workspace_call(
            "claim",
            socket=str(self.socket),
            session=target_session,
            head=head,
            slot=2,
            owner="another-owner",
            command="sleep 500"
        )
        self.assertFalse(second_claim.get("managed"), "Cannot claim already claimed slot")

        # Release slot 2
        release_receipt = relay._workspace_call(
            "release",
            socket=str(self.socket),
            session=target_session,
            head=head,
            slot=2,
            owner="test-agent-worker-2",
            pane=claimed_pane
        )
        self.assertTrue(release_receipt.get("managed"))

        # Wait for release respawn
        cmd = ""
        for _ in range(10):
            cmd = self.tmux("display-message", "-p", "-t", claimed_pane, "#{pane_current_command}")
            if cmd == "sleep":
                break
            time.sleep(0.1)

        # Check metadata after release
        rel_owner = self.tmux("display-message", "-p", "-t", claimed_pane, "#{@mios-mcp-owner}")
        rel_reserved = self.tmux("display-message", "-p", "-t", claimed_pane, "#{@mios-workspace-reserved}")
        rel_pid = self.tmux("display-message", "-p", "-t", claimed_pane, "#{pane_pid}")
        self.assertEqual(rel_owner, "", "Owner should be cleared")
        self.assertEqual(rel_reserved, rel_pid, "Reserved PID should be reset to current PID")
        self.assertEqual(cmd, "sleep")

    def test_1c_all_workers_killed_self_healing_via_resize(self):
        """When all worker panes are killed, --workspace-resize self-heals by re-allocating 4 slots."""
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

        # Find worker panes
        panes = self.tmux(
            "list-panes", "-a", "-F",
            "#{pane_id}\t#{@mios-workspace-worker}\t#{@mios-workspace-slot}"
        ).splitlines()
        worker_panes = [p.split("\t")[0] for p in panes if len(p.split("\t")) >= 2 and p.split("\t")[1] == head]
        self.assertEqual(len(worker_panes), 4)

        # Kill all 4 worker panes
        for wp in worker_panes:
            self.tmux("kill-pane", "-t", wp)

        # Verify 0 workers remain
        panes_after_kill = self.tmux(
            "list-panes", "-a", "-F",
            "#{pane_id}\t#{@mios-workspace-worker}"
        ).splitlines()
        rem = [p for p in panes_after_kill if len(p.split("\t")) >= 2 and p.split("\t")[1] == head]
        self.assertEqual(len(rem), 0)

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

        # Verify all 4 worker slots are self-healed and re-allocated
        panes_healed = self.tmux(
            "list-panes", "-a", "-F",
            "#{pane_id}\t#{@mios-workspace-worker}\t#{@mios-workspace-slot}\t#{@mios-workspace-reserved}\t#{pane_pid}\t#{pane_title}"
        ).splitlines()
        new_workers = [p.split("\t") for p in panes_healed if len(p.split("\t")) >= 3 and p.split("\t")[1] == head]
        self.assertEqual(len(new_workers), 4, f"Expected 4 healed workers, got: {new_workers}")
        slots = {int(w[2]) for w in new_workers}
        self.assertEqual(slots, {1, 2, 3, 4})

    def test_1d_claim_and_kill_worker_preserves_remaining_and_workspace_integrity(self):
        """Claim slot 1, kill slot 2, and trigger resize: verifies active worker and remaining slots survive uncorrupted."""
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

        # Claim slot 1
        claim = relay._workspace_call("claim", socket=str(self.socket), session=target_session, head=head, slot=1, owner="active-agent-1", command="sleep 1000")
        self.assertTrue(claim.get("managed"))
        claimed_pane = claim["pane"]

        # Find worker for slot 2 and kill it
        panes = self.tmux("list-panes", "-a", "-F", "#{pane_id}\t#{@mios-workspace-worker}\t#{@mios-workspace-slot}").splitlines()
        slot2_pane = None
        for p in panes:
            parts = p.split("\t")
            if len(parts) >= 3 and parts[1] == head and parts[2] == "2":
                slot2_pane = parts[0]
        self.assertIsNotNone(slot2_pane, "Slot 2 pane should exist before kill")
        self.tmux("kill-pane", "-t", slot2_pane)

        # Trigger resize
        res_after_kill = relay._workspace_call("resize", socket=str(self.socket), session=target_session, head=head, adapter=f"{sys.executable} {RELAY}")
        self.assertTrue(res_after_kill.get("managed"))
        self.assertEqual(res_after_kill.get("layout"), "desktop")
        # Workers list should now contain the remaining 3 workers: claimed slot 1, slot 3, slot 4
        self.assertIn(claimed_pane, res_after_kill.get("workers", []))
        self.assertEqual(len(res_after_kill.get("workers", [])), 3)

        # Verify claimed pane metadata is untouched
        owner = self.tmux("display-message", "-p", "-t", claimed_pane, "#{@mios-mcp-owner}")
        self.assertEqual(owner, "active-agent-1", "Claimed pane owner must survive resize and sibling kill")

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


class TestMultiSessionContextResolution(unittest.TestCase):
    def setUp(self):
        if shutil.which("tmux") is None:
            raise unittest.SkipTest("native tmux not found")
        self.human_socket = Path("/tmp/tmux-1000/mios-human")
        if not self.human_socket.exists():
            raise unittest.SkipTest("mios-human socket not running")

    def test_2a_human_tmux_context_with_non_mios_session_names(self):
        """Verify _human_tmux_context permits non-'mios' sessions ('tmux-os', 'tmux-os-2', 'test-human') on mios-human socket."""
        # 1. Test live session tmux-os without touching pane %1
        head_pane = subprocess.check_output(
            ["tmux", "-S", str(self.human_socket), "display-message", "-p", "-t", "tmux-os:0.0", "#{pane_id}"],
            text=True
        ).strip()
        ctx = relay._human_tmux_context(str(self.human_socket), head_pane)
        self.assertEqual(ctx["session"], "tmux-os")
        self.assertEqual(ctx["pane"], head_pane)
        self.assertEqual(ctx["socket"], str(self.human_socket))

        # 2. Create isolated test sessions on mios-human: 'tmux-os-2' and 'test-human'
        for sname in ["tmux-os-2", "test-human"]:
            pane_id = subprocess.check_output(
                ["tmux", "-S", str(self.human_socket), "new-session", "-d", "-s", sname, "-P", "-F", "#{pane_id}", "sleep 3600"],
                text=True
            ).strip()
            try:
                # Call _human_tmux_context unwitnessed
                res_ctx = relay._human_tmux_context(str(self.human_socket), pane_id)
                self.assertEqual(res_ctx["session"], sname)
                self.assertEqual(res_ctx["pane"], pane_id)
            finally:
                subprocess.run(
                    ["tmux", "-S", str(self.human_socket), "kill-session", "-t", f"={sname}"],
                    capture_output=True
                )

    def test_2b_storage_session_resolution_to_main_window(self):
        """Verify a parked worker pane in mios-workspace-* resolves its session to the main window's session name."""
        # Query any parked worker pane on mios-human
        panes = subprocess.check_output(
            ["tmux", "-S", str(self.human_socket), "list-panes", "-a", "-F", "#{pane_id}\t#{session_name}\t#{@mios-workspace-worker}"],
            text=True
        ).splitlines()
        parked_worker = None
        for p in panes:
            parts = p.split("\t")
            if len(parts) >= 3 and parts[1].startswith("mios-workspace-") and parts[2].startswith("%"):
                parked_worker = parts[0]
                break

        if parked_worker:
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
