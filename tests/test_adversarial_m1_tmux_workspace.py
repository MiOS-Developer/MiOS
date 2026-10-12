#!/usr/bin/env python3
# AI-hint: Adversarial suite for M1 tmux workspace recovery: recreated, killed and empty-server sessions.
"""
Adversarial empirical challenge suite for Milestone M1 (R1 Tmux Workspace Recovery).
Tests session recreation on missing/killed sessions, empty tmux server recovery,
dead pane resize graceful shielding, and invalid directory rejection.
"""
import asyncio
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
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


class TestAdversarialM1TmuxRecovery(unittest.TestCase):
    def setUp(self):
        if shutil.which("tmux") is None:
            raise unittest.SkipTest("native tmux not found")
        self.tmpdir = tempfile.TemporaryDirectory(prefix="adv-m1-tmux-")
        # Ensure directory permissions are 0700 per socket privacy rules
        os.chmod(self.tmpdir.name, 0o700)
        self.socket = Path(self.tmpdir.name) / "default"
        self.session_base = f"adv-base-{secrets.token_hex(4)}"
        # Start base session so tmux server is alive
        subprocess.check_call(
            ["tmux", "-S", str(self.socket), "-f", os.devnull,
             "new-session", "-d", "-s", self.session_base, "-P", "-F", "#{pane_id}", "sleep 3600"],
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

    def test_challenge_1a_target_session_missing_clean_recreation(self):
        """Challenge 1A: Target session does not exist; _workspace_call creates it via new-session."""
        target_session = f"adv-target-{secrets.token_hex(4)}"
        # Verify session does not exist
        res = subprocess.run(
            ["tmux", "-S", str(self.socket), "-f", os.devnull, "has-session", "-t", f"={target_session}"],
            capture_output=True
        )
        self.assertNotEqual(res.returncode, 0, "Target session should not exist initially")

        # Open workspace with missing session
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

        self.assertIn("head", result, "Expected head pane in result")
        self.assertTrue(result["head"].startswith("%"), f"Expected % pane ID, got {result['head']}")

        # Verify session now exists
        res = subprocess.run(
            ["tmux", "-S", str(self.socket), "-f", os.devnull, "has-session", "-t", f"={target_session}"],
            capture_output=True
        )
        self.assertEqual(res.returncode, 0, f"Session {target_session} should have been created")

    def test_challenge_1b_target_session_killed_and_recreated(self):
        """Challenge 1B: Target session is killed and then recreated cleanly via _workspace_call."""
        target_session = f"adv-killed-{secrets.token_hex(4)}"

        # 1. Create first time
        result1 = relay._workspace_call(
            "open",
            socket=str(self.socket),
            session=target_session,
            directory="/tmp",
            latch=f"mios-latch-{secrets.token_hex(4)}",
            command="sleep 3600",
            observer_command="sleep 3600",
            adapter=f"{sys.executable} {RELAY}"
        )
        head1 = result1["head"]
        self.assertTrue(head1.startswith("%"))

        # 2. Kill the session
        self.tmux("kill-session", "-t", f"={target_session}")
        res = subprocess.run(
            ["tmux", "-S", str(self.socket), "-f", os.devnull, "has-session", "-t", f"={target_session}"],
            capture_output=True
        )
        self.assertNotEqual(res.returncode, 0, "Session should be terminated")

        # 3. Call _workspace_call again: must recreate cleanly without error
        result2 = relay._workspace_call(
            "open",
            socket=str(self.socket),
            session=target_session,
            directory="/tmp",
            latch=f"mios-latch-{secrets.token_hex(4)}",
            command="sleep 3600",
            observer_command="sleep 3600",
            adapter=f"{sys.executable} {RELAY}"
        )
        head2 = result2["head"]
        self.assertTrue(head2.startswith("%"))

        # Verify session is alive again
        res = subprocess.run(
            ["tmux", "-S", str(self.socket), "-f", os.devnull, "has-session", "-t", f"={target_session}"],
            capture_output=True
        )
        self.assertEqual(res.returncode, 0, f"Session {target_session} should have been recreated")

    def test_challenge_1c_empty_tmux_server_session_creation(self):
        """Challenge 1C: Server is active with 0 sessions (list-panes -a returns empty/error)."""
        # Use a fresh isolated socket directory for 0-session server
        empty_dir = tempfile.TemporaryDirectory(prefix="adv-m1-empty-")
        os.chmod(empty_dir.name, 0o700)
        empty_sock = Path(empty_dir.name) / "default"
        try:
            start_res = subprocess.run(
                ["tmux", "-S", str(empty_sock), "-f", os.devnull, "start-server"],
                capture_output=True, text=True
            )
            self.assertEqual(start_res.returncode, 0, f"start-server failed: {start_res.stderr}")
            subprocess.run(
                ["tmux", "-S", str(empty_sock), "-f", os.devnull, "set-option", "-s", "exit-empty", "off"],
                capture_output=True
            )

            # list-panes -a should fail or return empty on 0-session server
            lp = subprocess.run(
                ["tmux", "-S", str(empty_sock), "-f", os.devnull, "list-panes", "-a"],
                capture_output=True, text=True
            )
            self.assertNotEqual(lp.returncode, 0, "list-panes -a on 0-session server should exit non-zero")

            fresh_session = f"adv-fresh-{secrets.token_hex(4)}"
            result = relay._workspace_call(
                "open",
                socket=str(empty_sock),
                session=fresh_session,
                directory="/tmp",
                latch=f"mios-latch-{secrets.token_hex(4)}",
                command="sleep 3600",
                observer_command="sleep 3600",
                adapter=f"{sys.executable} {RELAY}"
            )
            self.assertIn("head", result)
            self.assertTrue(result["head"].startswith("%"))

            # Session must exist on empty_sock
            res = subprocess.run(
                ["tmux", "-S", str(empty_sock), "-f", os.devnull, "has-session", "-t", f"={fresh_session}"],
                capture_output=True
            )
            self.assertEqual(res.returncode, 0, f"Session {fresh_session} should exist")
        finally:
            subprocess.run(["tmux", "-S", str(empty_sock), "-f", os.devnull, "kill-server"], capture_output=True)
            empty_dir.cleanup()

    def test_challenge_2a_dead_pane_resize_nonexistent_pane(self):
        """Challenge 2A: Resizing a non-existent pane (%999999) returns {'managed': False} without panic."""
        receipt = relay._workspace_call(
            "resize",
            {"socket": str(self.socket), "pane": "%999999"}
        )
        self.assertEqual(receipt, {"managed": False}, f"Expected {{'managed': False}}, got {receipt}")

        # Also test with head in direct arguments
        receipt2 = relay._workspace_call(
            "resize",
            socket=str(self.socket),
            head="%999999"
        )
        self.assertEqual(receipt2, {"managed": False}, f"Expected {{'managed': False}}, got {receipt2}")

    def test_challenge_2b_dead_pane_resize_killed_head(self):
        """Challenge 2B: Resizing a previously valid head pane that was killed returns {'managed': False}."""
        target_session = f"adv-resize-kill-{secrets.token_hex(4)}"
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

        # Kill the head pane
        self.tmux("kill-pane", "-t", head)

        # Call resize on the killed head pane
        receipt = relay._workspace_call(
            "resize",
            {"socket": str(self.socket), "pane": head}
        )
        self.assertEqual(receipt, {"managed": False}, f"Expected {{'managed': False}} for killed head, got {receipt}")

    def test_challenge_2c_cli_workspace_resize_exception_shielding(self):
        """Challenge 2C: CLI hook --workspace-resize shields dead panes and exits code 0."""
        # Call CLI hook with dead pane and socket
        cmd = [
            sys.executable,
            str(RELAY),
            "--workspace-resize",
            "%999999",
            str(self.socket)
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        self.assertEqual(proc.returncode, 0, f"CLI hook should exit 0, got {proc.returncode}. Stderr: {proc.stderr}")

    def test_challenge_3a_invalid_directory_rejection_nonexistent(self):
        """Challenge 3A: Non-existent directory raises structured RuntimeError without leaving dirty state."""
        panes_before = self.tmux("list-panes", "-a", "-F", "#{pane_id}").splitlines()
        bad_dir = f"/tmp/nonexistent-adv-dir-{secrets.token_hex(8)}"

        with self.assertRaises(RuntimeError) as ctx:
            relay._workspace_call(
                "open",
                socket=str(self.socket),
                session=f"adv-baddir-{secrets.token_hex(4)}",
                directory=bad_dir,
                latch=f"mios-latch-{secrets.token_hex(4)}",
                command="sleep 3600",
                observer_command="sleep 3600",
                adapter=f"{sys.executable} {RELAY}"
            )
        self.assertIn("workspace directory must be an existing absolute path", str(ctx.exception))

        panes_after = self.tmux("list-panes", "-a", "-F", "#{pane_id}").splitlines()
        self.assertEqual(panes_before, panes_after, "No new panes should be leaked on directory error")

    def test_challenge_3b_invalid_directory_rejection_relative_path(self):
        """Challenge 3B: Relative directory path is rejected with structured error."""
        with self.assertRaises(RuntimeError) as ctx:
            relay._workspace_call(
                "open",
                socket=str(self.socket),
                session=f"adv-reldir-{secrets.token_hex(4)}",
                directory="relative/path/not/absolute",
                latch=f"mios-latch-{secrets.token_hex(4)}",
                command="sleep 3600",
                observer_command="sleep 3600",
                adapter=f"{sys.executable} {RELAY}"
            )
        self.assertIn("workspace directory must be an existing absolute path", str(ctx.exception))

    def test_challenge_3c_invalid_directory_rejection_regular_file(self):
        """Challenge 3C: Existing path that is a regular file (not a dir) is rejected."""
        with self.assertRaises(RuntimeError) as ctx:
            relay._workspace_call(
                "open",
                socket=str(self.socket),
                session=f"adv-filedir-{secrets.token_hex(4)}",
                directory="/etc/passwd",
                latch=f"mios-latch-{secrets.token_hex(4)}",
                command="sleep 3600",
                observer_command="sleep 3600",
                adapter=f"{sys.executable} {RELAY}"
            )
        self.assertIn("workspace directory must be an existing absolute path", str(ctx.exception))

    def test_challenge_4_directory_fallback_when_omitted(self):
        """Challenge 4: When directory argument is omitted, falls back cleanly to cwd or /tmp."""
        target_session = f"adv-fallback-{secrets.token_hex(4)}"
        result = relay._workspace_call(
            "open",
            socket=str(self.socket),
            session=target_session,
            latch=f"mios-latch-{secrets.token_hex(4)}",
            command="sleep 3600",
            observer_command="sleep 3600",
            adapter=f"{sys.executable} {RELAY}"
        )
        self.assertIn("head", result)
        self.assertTrue(result["head"].startswith("%"))

    def test_challenge_5_concurrent_workspace_open_on_missing_session(self):
        """Challenge 5: Concurrent workspace open calls on a missing session serialize via lock."""
        import concurrent.futures
        target_session = f"adv-concurrent-{secrets.token_hex(4)}"

        def worker_open(idx):
            return relay._workspace_call(
                "open",
                socket=str(self.socket),
                session=target_session,
                directory="/tmp",
                latch=f"mios-latch-{secrets.token_hex(4)}-{idx}",
                command="sleep 3600",
                observer_command="sleep 3600",
                adapter=f"{sys.executable} {RELAY}"
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(worker_open, i) for i in range(4)]
            results = [f.result(timeout=15) for f in futures]

        self.assertEqual(len(results), 4)
        for r in results:
            self.assertIn("head", r)
            self.assertTrue(r["head"].startswith("%"))

        # Session must exist
        res = subprocess.run(
            ["tmux", "-S", str(self.socket), "-f", os.devnull, "has-session", "-t", f"={target_session}"],
            capture_output=True
        )
        self.assertEqual(res.returncode, 0)

    def test_challenge_6_dead_pane_resize_stress_repeated(self):
        """Challenge 6: Stress test dead pane resize with rapid repeated queries on multiple fake panes."""
        for fake_id in ["%123456", "%999999", "%000000", "%987654"]:
            res = relay._workspace_call("resize", {"socket": str(self.socket), "pane": fake_id})
            self.assertEqual(res, {"managed": False})



if __name__ == "__main__":
    unittest.main(verbosity=2)
