#!/usr/bin/env python3
"""Native MiOS-Tmux focus/zoom/menu acceptance in one disposable server.

This is an executable acceptance fixture, not a source-text substitute. Run it
only with the newly built, checksum-pinned backend. It never opens an existing
operator socket, physical input device, login service, or application save.
"""

import argparse
import errno
import fcntl
import hashlib
import os
from pathlib import Path
import pty
import select
import signal
import socket
import stat
import struct
import subprocess
import tempfile
import termios
import time
import unittest


ARGS = None
CLIENT_FORMAT = (
    "#{client_pid}|#{client_name}|#{client_instance}|#{session_id}|"
    "#{client_window_id}|#{client_pane_id}|#{client_pane_pid}|"
    "#{client_window_zoomed_flag}|#{pane_id}|#{client_flags}|"
    "#{client_window_action_version}"
)


def process(pid):
    root = Path("/proc") / str(pid)
    fields = (root / "stat").read_text().rsplit(")", 1)[1].split()
    return root.stat().st_uid, int(fields[19])


class NativeFocus(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.clients = []
        cls.owned_clients = []
        cls.jobs = []
        cls.server_pid = cls.server_identity = cls.socket_identity = None
        cls.directory = None
        cls.addClassCleanup(cls.cleanup)
        cls.menu_counter = 0
        cls.directory = Path(tempfile.mkdtemp(prefix="mios-focus-", dir="/tmp"))
        cls.directory.chmod(0o700)
        cls.socket = cls.directory / "server.sock"
        cls.binary = Path(ARGS.binary).resolve(strict=True)
        assert cls.binary.is_file()
        assert hashlib.sha256(cls.binary.read_bytes()).hexdigest() == ARGS.sha256
        cls.environment = {
            key: value for key, value in os.environ.items() if key not in {"TMUX", "TMUX_PANE"}
        }
        cls.environment["TERM"] = "xterm-256color"
        cls.environment["LC_ALL"] = "C.UTF-8"
        cls.run_cli(
            "-f", "/dev/null", "new-session", "-d", "-s", "proof",
            "-x", "100", "-y", "32", "/usr/bin/sleep", "180"
        )
        cls.server_pid = int(cls.run_cli("display-message", "-p", "#{pid}"))
        cls.server_identity = process(cls.server_pid)
        cls.socket_identity = (cls.socket.stat().st_dev, cls.socket.stat().st_ino)
        cls.run_cli("set-option", "-g", "status", "off")
        cls.run_cli("set-option", "-g", "mouse", "on")
        cls.base = cls.run_cli("display-message", "-p", "#{pane_id}")
        cls.split = cls.run_cli(
            "split-window", "-d", "-h", "-t", cls.base, "-P", "-F", "#{pane_id}",
            "/usr/bin/sleep", "180"
        )
        cls.floating = cls.run_cli(
            "split-window", "-d", "-f", "-t", cls.base, "-P", "-F", "#{pane_id}",
            "/usr/bin/sleep", "180"
        )
        cls.run_cli(
            "float-pane", "-d", "-t", cls.floating,
            "-x", "48", "-y", "16", "-X", "8", "-Y", "6"
        )
        for key, pane in [("F9", cls.split), ("F10", cls.floating), ("F11", cls.base)]:
            cls.run_cli("bind-key", "-T", "root", key, "select-pane", "-t", pane)
        for _ in range(2):
            cls.clients.append(cls.new_client())
        cls.await_value(lambda: len(cls.client_rows()), lambda count: count == 2)
        for row in cls.client_rows().values():
            assert row[10] == "1", "this fixture requires the new guarded focus protocol"
            assert "active-pane" in row[9]

    @classmethod
    def new_client(cls, flags="active-pane"):
        pid, master = pty.fork()
        if pid == 0:
            fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", 32, 100, 0, 0))
            os.execve(
                cls.binary,
                [str(cls.binary), "-S", str(cls.socket), "attach-session",
                 "-f", flags, "-t", "proof"],
                cls.environment,
            )
        os.set_blocking(master, False)
        client = {"pid": pid, "master": master, "identity": process(pid)}
        cls.owned_clients.append(client)
        return client

    @classmethod
    def run_cli(cls, *arguments, check=True):
        result = subprocess.run(
            [str(cls.binary), "-S", str(cls.socket), *map(str, arguments)],
            env=cls.environment, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5,
        )
        if check and result.returncode:
            raise RuntimeError("private tmux command failed: " + result.stderr.decode(errors="replace")[:500])
        return result.stdout.decode().strip() if check else result

    @classmethod
    def drain(cls):
        text = bytearray()
        for client in cls.clients:
            try:
                while select.select([client["master"]], [], [], 0)[0]:
                    block = os.read(client["master"], 16384)
                    if not block:
                        break
                    text.extend(block)
            except (BlockingIOError, OSError):
                pass
        return bytes(text)

    @classmethod
    def await_value(cls, read, predicate, timeout=6):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            value = read()
            if predicate(value):
                return value
            cls.drain()
            time.sleep(0.03)
        raise AssertionError("bounded private fixture condition did not settle")

    @classmethod
    def client_rows(cls):
        result = {}
        for line in cls.run_cli("list-clients", "-F", CLIENT_FORMAT).splitlines():
            fields = line.split("|")
            if len(fields) == 11:
                result[int(fields[0])] = fields
        return result

    @classmethod
    def select_for(cls, client, key, pane):
        sequence = {"F9": b"\x1b[20~", "F10": b"\x1b[21~", "F11": b"\x1b[23~"}[key]
        os.write(client["master"], sequence)
        cls.await_value(
            lambda: cls.client_rows().get(client["pid"], []),
            lambda row: len(row) == 11 and row[5] == pane,
        )

    @classmethod
    def ticket(cls, client):
        row = cls.client_rows()[client["pid"]]
        return ["-t", row[1], "-C", row[0], "-I", row[2], "-s", row[3],
                "-w", row[4], "-p", row[5], "-P", row[6], "zoom"]

    @classmethod
    def pane_state(cls):
        lines = cls.run_cli(
            "list-panes", "-a", "-F",
            "#{pane_id}|#{pane_pid}|#{pane_tty}|#{pane_left}|#{pane_top}|"
            "#{pane_width}|#{pane_height}|#{pane_floating_flag}",
        ).splitlines()
        return {line.split("|", 1)[0]: line for line in lines}

    @classmethod
    def zoomed(cls):
        return cls.run_cli("display-message", "-p", "-t", cls.base, "#{window_zoomed_flag}")

    def assert_zoom(self, client):
        completed = self.run_cli("client-window-action", *self.ticket(client), check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
        self.assertEqual(completed.stdout, b"ok\n")
        self.assertEqual(completed.stderr, b"")

    def setUp(self):
        # Ctrl-G cancels native menus without leaving an ambiguous Escape
        # prefix that could turn the immediately following F9 into M-F9.
        for client in self.clients:
            os.write(client["master"], b"\x07")
        self.drain()
        if self.zoomed() == "1":
            self.run_cli("resize-pane", "-Z", "-t", self.base)
        self.run_cli("select-pane", "-t", self.base)
        self.select_for(self.clients[0], "F9", self.split)
        # Establish an explicit second-client override, even when it equals
        # the window default. A no-op select alone would not create one.
        self.select_for(self.clients[1], "F9", self.split)
        self.select_for(self.clients[1], "F11", self.base)
        self.run_cli("set-option", "-gu", "@mios-focus-proof-choice")

    def test_actual_client_focus_differs_from_default_and_tiled_zoom_restores(self):
        first, second = self.clients
        rows = self.client_rows()
        self.assertEqual(rows[first["pid"]][5], self.split)
        self.assertEqual(rows[first["pid"]][8], self.base)
        self.assertEqual(rows[second["pid"]][5], self.base)
        before = self.pane_state()
        identities = {pane: process(int(row.split("|")[1])) for pane, row in before.items()}
        self.assert_zoom(first)
        self.assertEqual(self.zoomed(), "1")
        self.assertEqual(self.client_rows()[first["pid"]][5], self.split)
        self.assertEqual(self.client_rows()[second["pid"]][5], self.base)
        self.assert_zoom(first)
        self.assertEqual(self.zoomed(), "0")
        self.assertEqual(self.pane_state(), before)
        self.assertEqual(
            {pane: process(int(row.split("|")[1])) for pane, row in before.items()}, identities
        )

    def test_floating_zoom_keeps_geometry_process_and_pty(self):
        client = self.clients[0]
        self.select_for(client, "F10", self.floating)
        before = self.pane_state()
        self.assertEqual(before[self.floating].split("|")[-1], "1")
        for expected in ["1", "0"]:
            self.assert_zoom(client)
            self.assertEqual(self.zoomed(), expected)
        self.assertEqual(self.pane_state(), before)

    def test_stale_focus_and_each_identity_field_are_rejected_without_layout_change(self):
        client = self.clients[0]
        ticket = self.ticket(client)
        before = self.pane_state()
        self.select_for(client, "F11", self.base)
        self.assertNotEqual(self.run_cli("client-window-action", *ticket, check=False).returncode, 0)
        self.assertEqual(self.pane_state(), before)
        self.select_for(client, "F9", self.split)
        for flag, replacement in [
            ("-t", "/dev/pts/unknown-mios-proof"), ("-C", str(client["pid"] + 1)),
            ("-I", str(int(ticket[ticket.index("-I") + 1]) + 1)),
            ("-s", "$4294967295"), ("-w", "@4294967295"),
            ("-p", self.base), ("-P", "2147483647"),
        ]:
            changed = list(ticket)
            changed[changed.index(flag) + 1] = replacement
            with self.subTest(flag=flag):
                self.assertNotEqual(self.run_cli("client-window-action", *changed, check=False).returncode, 0)
                self.assertEqual(self.zoomed(), "0")
                self.assertEqual(self.pane_state(), before)
        for flag in ["-t", "-C", "-I", "-s", "-w", "-p", "-P"]:
            changed = list(ticket)
            offset = changed.index(flag)
            del changed[offset:offset + 2]
            with self.subTest(missing=flag):
                self.assertNotEqual(self.run_cli("client-window-action", *changed, check=False).returncode, 0)
                self.assertEqual(self.pane_state(), before)
        changed = list(ticket)
        changed[-1] = "unsupported"
        self.assertNotEqual(self.run_cli("client-window-action", *changed, check=False).returncode, 0)
        self.assertEqual(self.pane_state(), before)

    def test_readonly_client_and_hidden_independent_pane_are_refused(self):
        first, second = self.clients
        # Read-only is deliberately irreversible on a client. Use a third,
        # disposable attachment instead of weakening its flag for later tests.
        readonly = self.new_client("active-pane,read-only")
        self.await_value(self.client_rows, lambda rows: readonly["pid"] in rows)
        ticket = self.ticket(readonly)
        self.assertNotEqual(self.run_cli("client-window-action", *ticket, check=False).returncode, 0)
        self.run_cli("detach-client", "-t", ticket[1])
        self.assert_zoom(first)
        self.assertNotEqual(
            self.run_cli("client-window-action", *self.ticket(second), check=False).returncode, 0
        )
        self.assertEqual(self.zoomed(), "1")
        self.assert_zoom(first)

    def open_menu(self, *, choice="0", stay=False, yes_first=False, empty=False):
        self.__class__.menu_counter += 1
        title = "MiOSProofMenu" + str(self.menu_counter)
        self.drain()
        client = self.clients[0]
        args = [str(self.binary), "-S", str(self.socket), "display-menu", "-c",
                self.client_rows()[client["pid"]][1], "-t", self.split,
                "-x", "8", "-y", "20", "-T", title]
        if not empty:
            args += ["-C", choice]
        if stay:
            args += ["-O"]
        no = ["Cancel", "n", "set-option -g @mios-focus-proof-choice cancel"]
        yes = ["Yes", "y", "set-option -g @mios-focus-proof-choice yes"]
        items = [*(yes + no if yes_first else no + yes), "", "-Unavailable", "d",
                 "set-option -g @mios-focus-proof-choice BAD"]
        child = subprocess.Popen(
            args + items, env=self.environment, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.jobs.append(child)
        self.await_value(self.drain, lambda data: title.encode() in data)
        return child

    def finish_menu(self, child, data):
        os.write(self.clients[0]["master"], data)
        child.communicate(timeout=5)
        self.assertEqual(child.returncode, 0)
        return self.run_cli("show-options", "-gqv", "@mios-focus-proof-choice")

    def test_explicit_initial_cancel_and_empty_selection_remain_safe_with_mouse(self):
        child = self.open_menu(choice="1", yes_first=True)
        self.assertEqual(self.finish_menu(child, b"\r"), "cancel")
        self.run_cli("set-option", "-gu", "@mios-focus-proof-choice")
        child = self.open_menu(choice="-")
        self.assertEqual(self.finish_menu(child, b"\r"), "")
        child = self.open_menu()
        self.assertEqual(self.finish_menu(child, b"\x1b"), "")
        child = self.open_menu()
        self.assertEqual(self.finish_menu(child, b"\x07"), "")
        # Omitting -C preserves the prior keyboard default and key activation.
        child = self.open_menu(empty=True)
        self.assertEqual(self.finish_menu(child, b"\r"), "cancel")

    def test_direct_click_selects_its_row_without_prior_motion(self):
        # Four rows plus two border rows: -y20 -> py14; Yes is row16.
        for stay in [False, True]:
            with self.subTest(stay=stay):
                self.run_cli("set-option", "-gu", "@mios-focus-proof-choice")
                child = self.open_menu(stay=stay)
                self.assertEqual(
                    self.finish_menu(child, b"\x1b[<0;11;17M\x1b[<0;11;17m"), "yes"
                )

    def test_disabled_separator_border_and_outside_click_do_not_activate_default(self):
        # Same geometry: separator row17, disabled row18, left border x8.
        for stay in [False, True]:
            for x, y in [(10, 17), (10, 18), (8, 15), (2, 2)]:
                with self.subTest(x=x, y=y, stay=stay):
                    self.run_cli("set-option", "-gu", "@mios-focus-proof-choice")
                    child = self.open_menu(stay=stay)
                    data = f"\x1b[<0;{x + 1};{y + 1}M\x1b[<0;{x + 1};{y + 1}m".encode()
                    if stay:
                        data += b"\x1b"  # Disabled/separator leave -O open.
                    self.assertEqual(self.finish_menu(child, data), "")

    @classmethod
    def cleanup(cls):
        for job in cls.jobs:
            if job.poll() is None:
                job.terminate()
                try:
                    job.communicate(timeout=3)
                except subprocess.TimeoutExpired:
                    job.kill()
                    job.communicate(timeout=3)
        # Only the exact server process and socket created by this fixture
        # may receive commands. Never reconnect to a replaced socket.
        if cls.server_pid is not None and cls.same_process(cls.server_pid, cls.server_identity):
            metadata = cls.socket.lstat()
            if (metadata.st_dev, metadata.st_ino) != cls.socket_identity:
                raise AssertionError("private socket identity changed; refusing cleanup")
            try:
                cls.run_cli("kill-server")
            except (RuntimeError, subprocess.TimeoutExpired):
                pass
            cls.stop_owned_process(cls.server_pid, cls.server_identity)
        for client in cls.owned_clients:
            cls.stop_owned_process(client["pid"], client["identity"], reap=True)
            os.close(client["master"])
        if cls.directory is None:
            return
        # Some tmux revisions leave a socket after shutdown. Remove it only
        # after exact-server death, unchanged inode, same ownership/type, and
        # a failed local connection. Unexpected files remain as evidence.
        for path in cls.directory.iterdir():
            metadata = path.lstat()
            if metadata.st_uid != os.getuid():
                raise AssertionError("private fixture path ownership changed")
            if path.name == "server.sock" and stat.S_ISSOCK(metadata.st_mode):
                if (metadata.st_dev, metadata.st_ino) != cls.socket_identity:
                    raise AssertionError("private socket identity changed")
                if cls.same_process(cls.server_pid, cls.server_identity):
                    raise AssertionError("private server is still live")
                with socket.socket(socket.AF_UNIX) as probe:
                    probe.settimeout(0.2)
                    try:
                        probe.connect(str(path))
                    except OSError as error:
                        if error.errno not in {errno.ECONNREFUSED, errno.ENOENT}:
                            raise
                    else:
                        raise AssertionError("private socket still accepts connections")
                path.unlink()
            elif path.name == "server.sock.lock" and stat.S_ISREG(metadata.st_mode):
                path.unlink()
        cls.directory.rmdir()

    @staticmethod
    def same_process(pid, identity):
        if pid is None or identity is None:
            return False
        try:
            if process(pid) != identity:
                return False
            fields = (Path("/proc") / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()
            return fields[0] != "Z"
        except (FileNotFoundError, ProcessLookupError):
            return False

    @classmethod
    def stop_owned_process(cls, pid, identity, *, reap=False):
        for action in [signal.SIGTERM, signal.SIGKILL]:
            if cls.same_process(pid, identity):
                os.kill(pid, action)
            end = time.monotonic() + 2
            while time.monotonic() < end:
                if reap:
                    try:
                        if os.waitpid(pid, os.WNOHANG)[0] == pid:
                            return
                    except ChildProcessError:
                        return
                if not cls.same_process(pid, identity):
                    return
                time.sleep(0.02)
        raise AssertionError("owned fixture process did not stop within bounds")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True)
    parser.add_argument("--sha256", required=True)
    ARGS, remaining = parser.parse_known_args()
    assert len(ARGS.sha256) == 64 and all(c in "0123456789abcdef" for c in ARGS.sha256)
    unittest.main(argv=[__file__, *remaining])
