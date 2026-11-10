#!/usr/bin/env python3
# AI-hint: Replay MiOS-Mons resize and scroll regressions in owned Linux PTYs.
# AI-related: usr/share/mios/mios.toml, tests/test-tmux-os.py
# AI-functions: TestMiOSMonsRuntime, OwnedTerminal, Screen, catalogue_binary
"""Native runtime checks; no display, tmux server, or existing save is touched.

Run on Linux with the installed MiOS-Mons executable, or MIOS_MONS_BIN set to
an executable under test. The SSOT catalogue supplies the default binary.
MIOS_MONS_BASELINE_BIN enables an ABBA resize comparison; its CPU/output ratios
are reported rather than gated on machine-dependent absolute timing. Optional
MIOS_MONS_REPORT creates a new JSON receipt. All test children, PTYs, homes and
saves belong to this invocation and are cleaned up, including on assertion errors.
"""

import codecs
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pty
import select
import shutil
import signal
import struct
import sys
import tempfile
import termios
import time
import tomllib
import unicodedata
import unittest


SEED = 513
WHEEL_DOWN = b"\x1b[<65;5;5M"
WHEEL_UP = b"\x1b[<64;5;5M"


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def catalogue_binary():
    override = os.environ.get("MIOS_MONS_BIN")
    if override:
        return executable(override)
    root = Path(__file__).resolve().parents[1]
    for catalogue in (root / "usr/share/mios/mios.toml", Path("/usr/share/mios/mios.toml")):
        if not catalogue.is_file():
            continue
        with catalogue.open("rb") as stream:
            entries = tomllib.load(stream).get("terminal", {}).get("tuis", [])
        for entry in entries:
            if entry.get("label") == "MiOS-Mons":
                argv = entry.get("argv", [])
                if argv:
                    return executable(argv[0])
    raise unittest.SkipTest("MiOS-Mons is not installed; set MIOS_MONS_BIN for native checks")


def executable(value):
    resolved = shutil.which(value)
    if not resolved:
        if os.environ.get("MIOS_MONS_BIN") or os.environ.get("MIOS_MONS_BASELINE_BIN"):
            raise ValueError("Requested MiOS-Mons executable is unavailable: " + value)
        raise unittest.SkipTest("MiOS-Mons catalogue executable is not installed")
    return Path(resolved).resolve()


class Screen:
    """Retain a terminal grid across differential frames, not stale raw matches.

    Only terminal operations emitted by the runtime's text renderer are needed.
    OSC, SGR and private input modes do not contribute visible text.
    """

    def __init__(self, width, height):
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.state = "text"
        self.control = ""
        self.saved = (0, 0)
        self.resize(width, height)

    def resize(self, width, height):
        self.width, self.height = width, height
        self.cells = [[" "] * width for _ in range(height)]
        self.x = self.y = 0

    def feed(self, data):
        for char in self.decoder.decode(data):
            if self.state == "osc":
                if char == "\x07":
                    self.state = "text"
                elif char == "\x1b":
                    self.state = "osc-escape"
                continue
            if self.state == "osc-escape":
                self.state = "text" if char == "\\" else "osc"
                continue
            if self.state == "escape":
                self.state = {"[": "csi", "]": "osc"}.get(char, "text")
                self.control = ""
                continue
            if self.state == "csi":
                if "@" <= char <= "~":
                    self.csi(char, self.control)
                    self.state = "text"
                elif len(self.control) < 128:
                    self.control += char
                else:
                    self.state = "text"
                continue
            if char == "\x1b":
                self.state = "escape"
            elif char == "\r":
                self.x = 0
            elif char == "\n":
                self.y = min(self.height - 1, self.y + 1)
            elif char == "\b":
                self.x = max(0, self.x - 1)
            elif char == "\t":
                self.x = min(self.width - 1, (self.x // 8 + 1) * 8)
            elif char >= " " and char != "\x7f" and not unicodedata.combining(char):
                if self.x < self.width:
                    self.cells[self.y][self.x] = char
                self.x += 2 if unicodedata.east_asian_width(char) in "WF" else 1

    def csi(self, final, params):
        if params.startswith(("?", ">", "=")):
            return
        try:
            values = [int(part or "0") for part in params.split(";")]
        except ValueError:
            return
        first = values[0] if values else 0
        distance = first or 1
        if final in "Hf":
            self.y = min(self.height - 1, max(0, distance - 1))
            self.x = min(self.width - 1, max(0, (values[1] or 1) - 1)) if len(values) > 1 else 0
        elif final == "G":
            self.x = min(self.width - 1, distance - 1)
        elif final == "d":
            self.y = min(self.height - 1, distance - 1)
        elif final in "ABCD":
            if final == "A":
                self.y = max(0, self.y - distance)
            elif final == "B":
                self.y = min(self.height - 1, self.y + distance)
            elif final == "C":
                self.x = min(self.width - 1, self.x + distance)
            else:
                self.x = max(0, self.x - distance)
        elif final == "J":
            if first in (2, 3):
                self.cells = [[" "] * self.width for _ in range(self.height)]
            else:
                for y in range(self.height):
                    for x in range(self.width):
                        if (first == 0 and (y, x) >= (self.y, self.x)) or (
                            first == 1 and (y, x) <= (self.y, self.x)
                        ):
                            self.cells[y][x] = " "
        elif final == "K":
            start, end = (0, self.width) if first == 2 else (
                (0, self.x + 1) if first == 1 else (self.x, self.width)
            )
            self.cells[self.y][start:end] = [" "] * (end - start)
        elif final == "s":
            self.saved = self.x, self.y
        elif final == "u":
            self.x, self.y = self.saved

    def text(self):
        return "\n".join("".join(row).rstrip() for row in self.cells)


class OwnedTerminal:
    """An unreaped direct child is the only process this object can signal."""

    def __init__(self, binary, save, home, size=(120, 40), observe_screen=True):
        self.pid = None
        self.fd = None
        self.status = None
        self.output_bytes = 0
        self.screen = Screen(*size)
        self.observe_screen = observe_screen
        self.binary = binary
        self.save = save
        self.home = home
        self.initial_size = size

    @staticmethod
    def set_size(fd, width, height):
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", height, width, 0, 0))

    def __enter__(self):
        self.home.mkdir()
        pid, fd = pty.fork()
        if pid == 0:
            try:
                self.set_size(0, *self.initial_size)
                env = {
                    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                    "HOME": str(self.home),
                    "XDG_CONFIG_HOME": str(self.home / ".config"),
                    "XDG_STATE_HOME": str(self.home / ".state"),
                    "XDG_CACHE_HOME": str(self.home / ".cache"),
                    "TERM": "xterm-256color",
                    "LANG": "C.UTF-8",
                }
                argv = [str(self.binary), "--sample", "--seed", str(SEED), "--save", str(self.save),
                        "--renderer", "half-block", "--color", "rgb", "--dialogs", "inline",
                        "--camera", "room", "--zoom", "100"]
                os.execve(self.binary, argv, env)
            except BaseException as error:
                os.write(2, (str(error) + "\n").encode())
                os._exit(127)
        self.pid, self.fd = pid, fd
        try:
            os.set_blocking(fd, False)
            self.wait_for(lambda: self.output_bytes > 1000, "initial frame")
            self.drain(0.2)
            return self
        except BaseException:
            self.close()
            raise

    def poll(self):
        if self.status is None:
            done, status = os.waitpid(self.pid, os.WNOHANG)
            if done:
                self.status = status
        return self.status

    def drain(self, duration):
        end = time.monotonic() + duration
        while time.monotonic() < end:
            readable, _, _ = select.select([self.fd], [], [], max(0, min(0.01, end - time.monotonic())))
            if readable:
                try:
                    data = os.read(self.fd, 262144)
                except BlockingIOError:
                    continue
                except OSError as error:
                    if error.errno != errno.EIO:
                        raise
                    break
                if not data:
                    break
                self.output_bytes += len(data)
                if self.observe_screen:
                    self.screen.feed(data)

    def wait_for(self, condition, description, timeout=5):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if condition():
                return
            if self.poll() is not None:
                break
            self.drain(0.03)
        raise AssertionError("MiOS-Mons did not reach " + description + "\n" + self.screen.text())

    def send(self, data):
        offset = 0
        end = time.monotonic() + 5
        while offset < len(data):
            if self.poll() is not None:
                raise AssertionError("Owned MiOS-Mons child exited before input completed")
            try:
                offset += os.write(self.fd, data[offset:])
            except BlockingIOError:
                self.drain(0.01)
            if time.monotonic() >= end:
                raise AssertionError("Owned MiOS-Mons input timed out")

    def resize(self, width, height):
        if self.poll() is not None:
            raise AssertionError("Owned MiOS-Mons child exited before resize")
        self.set_size(self.fd, width, height)
        self.screen.resize(width, height)
        # TIOCSWINSZ already signals the foreground child; this also replays
        # redundant notifications without ever signalling a shared process.
        os.kill(self.pid, signal.SIGWINCH)

    def cpu_ticks(self):
        if self.poll() is not None:
            raise AssertionError("Owned MiOS-Mons child exited before CPU sample")
        fields = Path(f"/proc/{self.pid}/stat").read_text().rsplit(")", 1)[1].split()
        return int(fields[11]) + int(fields[12])

    def quit(self):
        self.send(b"q")
        self.wait_for(lambda: self.poll() is not None, "clean quit", timeout=5)
        code = os.waitstatus_to_exitcode(self.status)
        if code != 0:
            raise AssertionError(f"Owned MiOS-Mons child exited {code}\n{self.screen.text()}")
        return code

    def close(self):
        try:
            # An unreaped direct child cannot have its PID reused. Never signal
            # after waitpid has reaped it, and never use kill(pid, 0) as ownership.
            if self.pid is not None and self.poll() is None:
                os.kill(self.pid, signal.SIGKILL)
                _, self.status = os.waitpid(self.pid, 0)
        finally:
            if self.fd is not None:
                os.close(self.fd)
                self.fd = None

    def __exit__(self, *_):
        self.close()


@unittest.skipUnless(sys.platform.startswith("linux") and Path("/proc/self/stat").is_file(),
                     "Native Linux PTY and per-process CPU accounting required")
class TestMiOSMonsRuntime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.binary = catalogue_binary()
        cls.baseline = executable(os.environ["MIOS_MONS_BASELINE_BIN"]) if os.environ.get(
            "MIOS_MONS_BASELINE_BIN") else None
        cls.artifacts = {"candidate": {"path": str(cls.binary), "sha256": sha256(cls.binary)}}
        if cls.baseline:
            cls.artifacts["baseline"] = {"path": str(cls.baseline), "sha256": sha256(cls.baseline)}
        cls.sandbox = tempfile.TemporaryDirectory(prefix="mios-mons-runtime-")
        cls.addClassCleanup(cls.sandbox.cleanup)
        cls.root = Path(cls.sandbox.name)
        cls.report = {"scope": "owned private PTYs only", "seed": SEED, "artifacts": cls.artifacts,
                      "resize_order": "ABBA" if cls.baseline else "candidate", "results": []}
        cls.addClassCleanup(cls.write_report)
        cls.template = cls.root / "paused.json"
        with OwnedTerminal(cls.binary, cls.template, cls.root / "bootstrap-home") as terminal:
            terminal.send(b"p")
            terminal.drain(0.2)
            terminal.quit()
        state = json.loads(cls.template.read_bytes()).get("game", {})
        if state.get("paused") is not True or state.get("genesis_seed") != SEED:
            raise AssertionError("Bootstrap did not create the requested paused game")
        cls.template_bytes = cls.template.read_bytes()
        cls.report["paused_save_sha256"] = sha256(cls.template)

    @classmethod
    def write_report(cls):
        for artifact in cls.artifacts.values():
            artifact["sha256_after"] = sha256(artifact["path"])
        output = json.dumps(cls.report, indent=2, sort_keys=True) + "\n"
        if os.environ.get("MIOS_MONS_REPORT"):
            with Path(os.environ["MIOS_MONS_REPORT"]).open("x") as stream:
                stream.write(output)
        print(output, end="")

    def saved_copy(self, name):
        path = self.root / (name + ".json")
        path.write_bytes(self.template_bytes)
        return path

    def assert_save_unchanged(self, path):
        self.assertEqual(path.read_bytes(), self.template_bytes,
                         "Paused game changed during navigation/resize-only replay")

    def test_resize_flood_paused_save_identity(self):
        order = [("baseline", self.baseline), ("candidate", self.binary),
                 ("candidate", self.binary), ("baseline", self.baseline)] if self.baseline else [
                     ("candidate", self.binary)]
        for index, (label, binary) in enumerate(order):
            with self.subTest(index=index, binary=label):
                save = self.saved_copy(f"resize-{index}")
                # Drain byte counts without parsing the scene during CPU
                # measurement: terminal-emulator cost must not throttle either
                # binary's output or distort the resize input cadence.
                with OwnedTerminal(binary, save, self.root / f"resize-home-{index}",
                                   observe_screen=False) as terminal:
                    before_cpu, before_bytes = terminal.cpu_ticks(), terminal.output_bytes
                    start = time.monotonic()
                    for step in range(150):
                        terminal.resize(*((160, 50) if step % 2 == 0 else (120, 40)))
                        terminal.drain(max(0, start + (step + 1) / 100 - time.monotonic()))
                    elapsed = time.monotonic() - start
                    cpu = (terminal.cpu_ticks() - before_cpu) / os.sysconf("SC_CLK_TCK")
                    emitted = terminal.output_bytes - before_bytes
                    terminal.resize(120, 40)
                    terminal.drain(0.35)
                    code = terminal.quit()
                self.assert_save_unchanged(save)
                self.report["results"].append({"scenario": "resize", "label": label, "index": index,
                    "resize_events": 150, "target_hz": 100, "cpu_seconds": cpu,
                    "output_bytes": emitted, "wall_seconds": elapsed, "quit_exit": code,
                    "final_size": [120, 40], "save_sha256": sha256(save)})
        if self.baseline:
            rows = [row for row in self.report["results"] if row["scenario"] == "resize"]
            baseline = [row for row in rows if row["label"] == "baseline"]
            candidate = [row for row in rows if row["label"] == "candidate"]
            self.report["candidate_to_baseline"] = {
                metric: sum(row[metric] for row in candidate) / sum(row[metric] for row in baseline)
                if sum(row[metric] for row in baseline) else None
                for metric in ("cpu_seconds", "output_bytes")}

    def test_wheel_order_and_selection_survive_idle_resize(self):
        for name, burst in (("settled-reference", False), ("wheel-burst", True)):
            with self.subTest(trace=name):
                save = self.saved_copy(name)
                with OwnedTerminal(self.binary, save, self.root / (name + "-home"), (100, 34)) as terminal:
                    terminal.send(b"o")
                    terminal.wait_for(lambda: "MAIN MENU" in terminal.screen.text(), "main menu")
                    if burst:
                        # 240 net-zero wheel events, then six net steps and an
                        # activation barrier. No event may be reordered or lost.
                        terminal.send((WHEEL_DOWN + WHEEL_UP) * 120 + WHEEL_DOWN * 7 + WHEEL_UP)
                        terminal.drain(0.35)
                        terminal.resize(64, 22)
                        terminal.drain(0.2)
                        terminal.resize(100, 34)
                        terminal.drain(0.25)
                    else:
                        for _ in range(6):
                            terminal.send(WHEEL_DOWN)
                            terminal.drain(0.07)
                    terminal.send(b"\r")
                    terminal.wait_for(lambda: "SPRITE STUDIO" in terminal.screen.text(), "sprite studio")
                    code = terminal.quit()
                self.assert_save_unchanged(save)
                self.report["results"].append({"scenario": name, "menu": "SPRITE STUDIO",
                    "quit_exit": code, "save_sha256": sha256(save)})

    def test_help_scroll_survives_idle_and_geometry_round_trip(self):
        save = self.saved_copy("help-scroll")
        with OwnedTerminal(self.binary, save, self.root / "help-home", (80, 18)) as terminal:
            terminal.send(b"?")
            terminal.wait_for(lambda: "Global detail:" in terminal.screen.text(), "help first page")
            terminal.send(WHEEL_DOWN * 6)
            terminal.wait_for(lambda: "Global detail:" not in terminal.screen.text(), "scrolled help")
            terminal.drain(0.4)
            self.assertNotIn("Global detail:", terminal.screen.text(), "Help reset during idle")
            terminal.resize(64, 14)
            terminal.drain(0.2)
            terminal.resize(80, 18)
            terminal.drain(0.3)
            self.assertNotIn("Global detail:", terminal.screen.text(), "Help reset during resize")
            self.assertIn("HELP", terminal.screen.text(), "Help vanished during resize")
            terminal.send(WHEEL_UP * 20)
            terminal.wait_for(lambda: "Global detail:" in terminal.screen.text(), "help scroll back to top")
            code = terminal.quit()
        self.assert_save_unchanged(save)
        self.report["results"].append({"scenario": "help-scroll-round-trip", "quit_exit": code,
                                       "save_sha256": sha256(save)})


if __name__ == "__main__":
    unittest.main()
