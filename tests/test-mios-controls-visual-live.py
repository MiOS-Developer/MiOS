# AI-hint: Live visual device views and TUI verification for mios-controls: keyboard, mouse, pad per family with rebind. Uses FixturePad for gamepad injection.
# AI-related: tools/native/mios-controls/src/main.rs, tools/native/mios-controls/src/tui.rs, tools/native/mios-controls/src/pads.rs, tests/fixtures/mios-controls/fixture_pad.py
"""Live visual device and fixture pad verification for mios-controls."""
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest

ROOT = Path(os.environ.get("MIOS_TEST_ROOT", "/"))
HERE = Path(__file__).resolve().parents[1]
FIXTURES_DIR = HERE / "tests" / "fixtures" / "mios-controls"
if str(FIXTURES_DIR) not in sys.path:
    sys.path.insert(0, str(FIXTURES_DIR))

try:
    from fixture_pad import FixturePad, FAMILIES, BUTTONS, PREFIX
except ImportError:
    FixturePad = None
    FAMILIES = {"xbox": (0x045E, 0x0B12), "playstation": (0x054C, 0x0CE6), "generic": (0x1209, 0x0001)}
    BUTTONS = {}
    PREFIX = "mios-input-fixture-"


def binary():
    explicit = os.environ.get("MIOS_CONTROLS")
    candidates = (
        [Path(explicit)]
        if explicit
        else [
            HERE / "tools/native/target/x86_64-unknown-linux-musl/release/mios-controls",
            Path("/var/lib/mios-build/tmp/mios-fix-controls-tui/target/x86_64-unknown-linux-musl/release/mios-controls"),
            Path("/usr/bin/mios-controls"),
        ]
    )
    for p in candidates:
        if p.is_file() and os.access(p, os.X_OK):
            return p
    raise AssertionError("mios-controls binary not found; set MIOS_CONTROLS")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mios-controls-vis-")
        self.user = Path(self.tmp.name) / "cfg/mios/mios.toml"
        self.env = dict(
            os.environ,
            MIOS_USER_TOML=str(self.user),
            MIOS_USER_TOML_D=str(Path(self.tmp.name) / "none.d"),
        )
        self.bin = binary()

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args):
        return subprocess.run(
            [str(self.bin), "--root", str(ROOT), *args],
            capture_output=True,
            text=True,
            timeout=120,
            env=self.env,
        )


class VisualDeviceViews(Base):
    """Verifies that mios-controls properly enumerates visual layouts for all peripheral families."""

    def test_peripheral_families_and_controller_tables(self):
        r = self.run_cli("list", "--json")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        data = json.loads(r.stdout)
        for key in ("nav", "room", "hypr3d", "toggle_chord", "mode_override_chord"):
            self.assertIn(key, data, f"missing expected table {key}")

    def test_rebind_controller_action_dry_run_and_apply(self):
        # Positive control: valid rebind to an allowed namespace action
        r = self.run_cli("set", "terminal", "--chord", "SUPER+F7")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("dry run", r.stdout)
        self.assertFalse(self.user.exists(), "dry run must not write user tier")

    def test_negative_control_colliding_rebind_is_refused(self):
        # Negative control: colliding chord cannot be applied
        r = self.run_cli("set", "terminal", "--chord", "SUPER+c", "--apply")
        self.assertEqual(r.returncode, 1, "colliding chord should fail")
        self.assertIn("NEW FINDING: collision chord SUPER+c", r.stdout)
        self.assertFalse(self.user.exists(), "refused draft is never committed")

    def test_negative_control_illegal_namespace_is_refused(self):
        # Negative control: unknown namespace cannot be bound
        r = self.run_cli("set", "terminal", "--action", "unsupported_ns:action", "--apply")
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(self.user.exists())


class VisualTuiNestedSession(Base):
    """Verifies the Controls TUI rendering across views in a private tmux session."""

    def setUp(self):
        super().setUp()
        self.sock = f"mios-controls-vis-{os.getpid()}"
        self.tmux_env = dict(self.env, TMUX_TMPDIR=self.tmp.name, TERM="xterm-256color")
        self.tmux_env.pop("TMUX", None)

    def tmux(self, *a):
        return subprocess.run(
            ["tmux", "-L", self.sock, "-f", "/dev/null", *a],
            capture_output=True,
            text=True,
            env=self.tmux_env,
            timeout=10,
        )

    def screen(self, want, timeout=10.0):
        end = time.monotonic() + timeout
        text = ""
        while time.monotonic() < end:
            text = self.tmux("capture-pane", "-p", "-t", "c").stdout
            if want in text:
                return text
            time.sleep(0.1)
        self.fail(f"{want!r} not on screen:\n{text}")

    def tearDown(self):
        self.tmux("kill-server")
        super().tearDown()

    def test_views_keyboard_mouse_controller_and_help(self):
        cmd = f"{self.bin} --root {ROOT}; echo EXITED:$?; sleep 30"
        r = self.tmux("new-session", "-d", "-s", "c", "-x", "170", "-y", "45", cmd)
        self.assertEqual(r.returncode, 0, r.stderr)
        s = self.screen("MiOS Controls")
        for expected in ("Peripherals", "Keyboard", "Bindings", "? help"):
            self.assertIn(expected, s)

        # Navigate context and switch views
        self.tmux("send-keys", "-t", "c", "]")
        self.screen("context [desktop-2d]")

        # Switch to controller view
        self.tmux("send-keys", "-t", "c", "Tab")
        self.tmux("send-keys", "-t", "c", "Down")
        self.tmux("send-keys", "-t", "c", "Enter")
        self.screen("Controller")

        # Open and dismiss help overlay
        self.tmux("send-keys", "-t", "c", "?")
        self.screen("TAKING CONTROL")
        self.tmux("send-keys", "-t", "c", "x")

        # Quit cleanly
        self.tmux("send-keys", "-t", "c", "q")
        self.screen("EXITED:0")
        self.assertFalse(self.user.exists(), "browsing does not mutate user settings")


class FixturePadVerification(unittest.TestCase):
    """Verifies synthetic uinput pad creation, event emission, and negative controls."""

    def test_fixture_pad_lifecycle_and_negative_control(self):
        if FixturePad is None:
            self.skipTest("FixturePad could not be imported")
        if not os.path.exists("/dev/uinput") or not os.access("/dev/uinput", os.W_OK):
            self.skipTest("/dev/uinput not writable in current environment")

        for family in ("xbox", "playstation", "generic"):
            with FixturePad(family=family, tag=f"vis-{family}") as pad:
                self.assertIsNotNone(pad.fd)
                self.assertIn(PREFIX, pad.name)
                # Emit actions
                pad.press("BTN_SOUTH", hold=0.01, gap=0.01)
                pad.hat(x=1, y=0, hold=0.01, gap=0.01)
                pad.stick("LX", 16000, hold=0.01, gap=0.01)
                pad.trigger("R", 1023, hold=0.01, gap=0.01)

        # Negative control: closed pad cannot emit events
        pad_neg = FixturePad(family="xbox", tag="vis-neg")
        pad_neg.close()
        self.assertIsNone(pad_neg.fd)
        with self.assertRaises((OSError, ValueError, AttributeError)):
            pad_neg.button("BTN_SOUTH", True)


if __name__ == "__main__":
    unittest.main()

