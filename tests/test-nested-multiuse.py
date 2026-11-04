#!/usr/bin/env python3
# AI-hint: Real-binary checks of `mios-gen nested-session` launch targets, seat/user identity and device assignment (--plan, --dry-run, --list-devices) against a scratch SSOT; device resolution/classification/policy and inventory JSON run in the five native nested_session::tests::multiuse_devices_* private Host fixtures. No global kernel devices or compositor are created.
# AI-related: tools/native/mios-gen/src/nested_session.rs, tools/native/mios-gen/src/nested_devices.rs, usr/share/mios/mios.toml [desktop.nested], tests/test-nested-multiuse-live.py
# AI-functions: Uinput (opt-in live helper), Binary, TestTargets, TestDeviceBoundary
# MIOS_GEN selects an explicit candidate; otherwise discover a source-built or installed generator.
import fcntl, json, os, re, struct, subprocess, sys, tempfile, time, tomllib, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
def generator():
    explicit = os.environ.get('MIOS_GEN')
    if explicit:
        return explicit
    for path in (ROOT / 'tools/native/target/release/mios-gen', ROOT / 'tools/native/target/debug/mios-gen',
                 ROOT / 'usr/bin/mios-gen', Path('/usr/bin/mios-gen')):
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    raise RuntimeError('native mios-gen candidate missing')


GEN = generator()
SSOT = ROOT / 'usr/share/mios/mios.toml'

# Used only by the separately opt-in live suite; the ordinary suite never instantiates Uinput.
# linux/uinput.h, linux/input-event-codes.h
UI_SET_EVBIT, UI_SET_KEYBIT, UI_SET_ABSBIT = 0x40045564, 0x40045565, 0x40045567
UI_DEV_CREATE, UI_DEV_DESTROY = 0x5501, 0x5502
EV_SYN, EV_KEY, EV_ABS = 0, 1, 3
BTN_TRIGGER, BTN_THUMB = 0x120, 0x121
ABS_X, ABS_Y = 0, 1


class Uinput:
    """A private joystick (BTN_TRIGGER + ABS_X/Y, no BTN_SOUTH): a controller to the nested-session policy, but not a
    gamepad the desktop's input bridge adopts and not a device libinput handles, so the live desktop never acts on it."""

    def __init__(self, name):
        self.name = name
        self.fd = os.open('/dev/uinput', os.O_WRONLY | os.O_NONBLOCK)
        for ev in (EV_KEY, EV_ABS):
            fcntl.ioctl(self.fd, UI_SET_EVBIT, ev)
        for key in (BTN_TRIGGER, BTN_THUMB):
            fcntl.ioctl(self.fd, UI_SET_KEYBIT, key)
        for ab in (ABS_X, ABS_Y):
            fcntl.ioctl(self.fd, UI_SET_ABSBIT, ab)
        absmax, absmin = [0] * 64, [0] * 64
        absmax[ABS_X] = absmax[ABS_Y] = 255
        dev = struct.pack('80sHHHHi', name.encode()[:79], 0x06, 0x1234, 0x5678, 1, 0)
        dev += struct.pack('64i', *absmax) + struct.pack('64i', *absmin) + struct.pack('64i', *[0] * 64) * 2
        os.write(self.fd, dev)
        fcntl.ioctl(self.fd, UI_DEV_CREATE)
        self.node = None
        end = time.monotonic() + 5
        while time.monotonic() < end and not self.node:
            for e in sorted(Path('/sys/class/input').glob('event*')):
                try:
                    if (e / 'device/name').read_text().strip() == name:
                        self.node = f'/dev/input/{e.name}'
                except OSError:
                    pass
            time.sleep(0.05)
        if not self.node:
            self.close()
            raise RuntimeError(f'uinput device {name!r} did not appear')
        end = time.monotonic() + 5
        while time.monotonic() < end and not os.access(self.node, os.R_OK):
            time.sleep(0.05)

    def emit(self, typ, code, value):
        t = time.time()
        os.write(self.fd, struct.pack('llHHi', int(t), int((t % 1) * 1e6), typ, code, value))

    def press(self, code=BTN_TRIGGER):
        for v in (1, 0):
            self.emit(EV_KEY, code, v)
            self.emit(EV_SYN, 0, 0)

    def close(self):
        if self.fd is not None:
            try:
                fcntl.ioctl(self.fd, UI_DEV_DESTROY)
            finally:
                os.close(self.fd)
                self.fd = None


def toml_value(v):
    if isinstance(v, bool):
        return 'true' if v else 'false'
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v)
    return '[' + ', '.join(toml_value(x) for x in v) + ']'


def scratch_ssot(path, overrides):
    """The worktree SSOT with `overrides` {"table.path": {key: value}} applied by rewriting those keys in place
    (a key not present in the table is appended at the end of it)."""
    text = SSOT.read_text()
    for table, kv in overrides.items():
        m = re.search(r'^\[' + re.escape(table) + r'\]\n', text, re.M)
        assert m, table
        end = re.search(r'^\[', text[m.end():], re.M)
        body = text[m.end():m.end() + (end.start() if end else len(text))]
        for k, v in kv.items():
            line = f'{k} = {toml_value(v)}'
            body, n = re.subn(r'^' + re.escape(k) + r'\s*=.*$', line, body, count=1, flags=re.M)
            if not n:
                body = body.rstrip('\n') + '\n' + line + '\n\n'
        text = text[:m.end()] + body + text[m.end() + (end.start() if end else len(text) - m.end()):]
    path.write_text(text)
    tomllib.loads(text)
    return path


class Binary:
    def __init__(self, ssot):
        self.tmp = tempfile.TemporaryDirectory(prefix='mios-nested-multiuse-')
        user = Path(self.tmp.name) / 'user.toml'
        user.write_text('')
        (Path(self.tmp.name) / 'user.d').mkdir()
        self.env = {k: v for k, v in os.environ.items() if k not in ('MIOS_TOML', 'MIOS_VENDOR_TOML_D', 'MIOS_HOST_TOML', 'MIOS_HOST_TOML_D', 'HYPRLAND_INSTANCE_SIGNATURE', 'WAYLAND_DISPLAY')}
        self.env.update(MIOS_VENDOR_TOML=str(ssot), MIOS_HOST_TOML=str(Path(self.tmp.name) / "absent.toml"), MIOS_USER_TOML=str(user), MIOS_USER_TOML_D=str(Path(self.tmp.name) / 'user.d'),
                        XDG_RUNTIME_DIR=self.tmp.name)

    def run(self, *args):
        return subprocess.run([GEN, 'nested-session', *args], env=self.env, capture_output=True, text=True, timeout=60)

    def plan(self, *args):
        p = self.run('--plan', *args)
        if p.returncode:
            raise AssertionError(f'plan {args}: {p.stdout}{p.stderr}')
        return json.loads(p.stdout)


class TestTargets(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='mios-nested-multiuse-ssot-')
        cls.ssot = scratch_ssot(Path(cls.tmp.name) / 'vendor.toml', {})
        text = cls.ssot.read_text().replace('[desktop.nested.seats]\n', '[desktop.nested.seats]\np2 = { user = "Player Two", target = "output", output = "HDMI-A-1" }\n', 1)
        cls.ssot.write_text(text)
        tomllib.loads(text)
        cls.b = Binary(cls.ssot)

    @classmethod
    def tearDownClass(cls):
        cls.b.tmp.cleanup()
        cls.tmp.cleanup()

    def test_default_plan_is_the_unchanged_pip_session(self):
        p = self.b.plan()
        self.assertEqual((p['target'], p['seat'], p['tmux_socket_name'], p['scale_mode'], p['devices']), ('pip', '', 'mios-nested', 'fit', []))

    def test_seat_preset_and_cli_precedence(self):
        p = self.b.plan('--seat', 'p2')
        self.assertEqual((p['target'], p['output'], p['user'], p['tmux_socket_name'], p['scale_mode']), ('output', 'HDMI-A-1', 'Player Two', 'mios-nested-p2', 'native'))
        p = self.b.plan('--seat', 'p2', '--target', 'hypr3d', '--user', 'Guest')
        self.assertEqual((p['target'], p['user'], p['scale_mode']), ('hypr3d', 'Guest', 'fit'), 'CLI flags win over the seat preset')
        self.assertEqual(self.b.plan('--layout', 'window')['target'], 'window', '--layout stays an alias of --target')

    def test_invalid_launches_are_refused_without_starting(self):
        for args, needle in ((['--target', 'output'], 'needs an output'), (['--seat', 'Bad'], 'seat'), (['--target', 'pip', '--output', 'DP-1'], 'applies to the output and remote'),
                             (['--device', 'virtual-pad'], 'needs a seat')):
            p = self.b.run('--plan', *args)
            self.assertEqual(p.returncode, 1, (args, p.stdout))
            self.assertIn(needle, p.stdout + p.stderr, args)
        p = self.b.run('--plan', '--target', 'tv')
        self.assertEqual(p.returncode, 2, 'clap refuses an unknown target')
        self.assertFalse(list(Path(self.b.tmp.name).glob('mios-nested-desktop-*')), 'nothing started')

    def test_target_scale_mode_reaches_the_compositor_config(self):
        native = self.b.run('--dry-run', '--target', 'remote')
        fit = self.b.run('--dry-run')
        self.assertEqual((native.returncode, fit.returncode), (0, 0), native.stderr + fit.stderr)
        self.assertIn("local nested_fit={mode='native'", native.stdout)
        self.assertIn("local nested_fit={mode='fit'", fit.stdout)

    def test_launch_without_a_parent_compositor_fails_closed(self):
        p = self.b.run('--target', 'window')
        self.assertEqual(p.returncode, 1)
        self.assertIn('signature missing', p.stdout + p.stderr)
        self.assertFalse((Path(self.b.tmp.name) / 'mios/nested-sessions').exists() and list((Path(self.b.tmp.name) / 'mios/nested-sessions').glob('*.json')), 'no registry entry')


class TestDeviceBoundary(unittest.TestCase):
    """The real release CLI must fail closed without an accessible input node."""

    def test_missing_device_is_rejected_and_no_devices_overrides_ssot_default(self):
        with tempfile.TemporaryDirectory(prefix='mios-nested-missing-device-') as tmp:
            ssot = scratch_ssot(Path(tmp) / 'vendor.toml',
                                {'desktop.nested.devices': {'default': ['name:MiOS Missing Fixture Device']}})
            b = Binary(ssot)
            self.addCleanup(b.tmp.cleanup)
            failed = b.run('--plan')
            self.assertEqual(failed.returncode, 1)
            self.assertIn('no input device is named', failed.stdout + failed.stderr)
            self.assertEqual(b.plan('--no-devices')['devices'], [])
            for spec in ('/dev/input/event999999999', '/dev/null', '/dev/input/../null'):
                failed = b.run('--plan', '--device', spec)
                self.assertEqual(failed.returncode, 1, (spec, failed.stdout, failed.stderr))
            self.assertFalse(list(Path(b.tmp.name).glob('mios-nested-desktop-*')))


if __name__ == '__main__':
    unittest.main(verbosity=2)
