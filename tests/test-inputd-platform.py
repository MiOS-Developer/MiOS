#!/usr/bin/env python3
# AI-hint: Real-binary two-sided tests for mios-inputd step I4 (the universal input platform): the Guide layer on every pad shape (HAT d-pad, LS_*/RS_* flicks, analog-only triggers, hypr:movewindow, volume/media), the C-19 system-monitor rename, the O-7 per-driver quirk table proven with uinput fixtures that emulate xpad (face order, dpad_to_buttons) and DualSense code layouts, the agent virtual controller (`vpad`: eased evdev stream, taps, idle exit, the MCP verb argv), and the nested-session contract (the host bridge never owns an assigned node or a seat pad; `bridge --nested` / `vpad assign` follow the session, ease its room packet, project keys into it and pass a captured pad through).
# AI-related: tools/native/mios-inputd/src/{router.rs,vpad.rs,nested.rs,runtime.rs,linux.rs,config.rs}, usr/share/mios/mios.toml [keybindings.global.controller.system_layer|quirks|agent_pad] [verbs.virtual_controller] [desktop.nested], docs/design/mios-global-bindings-tests.md (I4)
# AI-functions: Base, Grammar, Vpad, FakeCompositor, Fixture
# Environment: MIOS_INPUTD_BIN = the binary under test (default: installed). MIOS_INPUTD_OLD_BIN + MIOS_INPUTD_OLD_SSOT =
# the pre-I4 binary and SSOT for the negative controls (skipped when absent). MIOS_INPUTD_UINPUT_TEST=1 enables the
# private uinput tests: fixture pads and fixture-named agent pads (mios-input-fixture-*, which no live bridge opens), a
# fake compositor socket and a fake nested-session registry in a private XDG_RUNTIME_DIR. No physical device, no real
# compositor and no live session is touched.
import fcntl
import json
import os
from pathlib import Path
import re
import select
import shlex
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
BIN = Path(os.environ.get('MIOS_INPUTD_BIN', '/usr/libexec/mios/mios-inputd'))
OLD = Path(os.environ.get('MIOS_INPUTD_OLD_BIN', '/nonexistent'))
OLD_SSOT = Path(os.environ.get('MIOS_INPUTD_OLD_SSOT', '/nonexistent'))
UINPUT = os.environ.get('MIOS_INPUTD_UINPUT_TEST') == '1'
SOUTH, EAST, NORTH, WEST, LT, RT, GUIDE, START = 0x130, 0x131, 0x133, 0x134, 0x138, 0x139, 0x13c, 0x13b
STICK = {c: dict(min=-32768, max=32767, value=0) for c in ('0', '1', '3', '4')}
AXES = dict(STICK, **{'2': dict(min=0, max=255, value=0), '5': dict(min=0, max=255, value=0),
                      '16': dict(min=-1, max=1, value=0), '17': dict(min=-1, max=1, value=0)})
EVIOCGRAB = 0x40044590
EVENT = struct.Struct('llHHi')
PACKET = struct.Struct('<IIq4f4B8I')


def key(code, value=1):
    return dict(kind=1, code=code, value=value)


def ab(code, value):
    return [dict(kind=3, code=code, value=value), dict(kind=0, code=0, value=0)]


def wait(ms):
    return [dict(tick_ms=ms)]


def until(pred, seconds=5.0, step=0.05):
    end = time.monotonic() + seconds
    while True:
        v = pred()
        if v or time.monotonic() > end:
            return v
        time.sleep(step)


def start_time(pid):
    stat = Path(f'/proc/{pid}/stat').read_text()
    return int(stat.rsplit(')', 1)[1].split()[19])


class Reader:
    """An evdev reader of one node; with grab=True it holds EVIOCGRAB (private: nothing else sees the events)."""

    def __init__(self, path, grab=False):
        self.fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        if grab:
            fcntl.ioctl(self.fd, EVIOCGRAB, 1)

    def read(self, seconds=0.3):
        out, end = [], time.monotonic() + seconds
        while time.monotonic() < end:
            r, _, _ = select.select([self.fd], [], [], 0.02)
            if r:
                try:
                    data = os.read(self.fd, EVENT.size * 256)
                except BlockingIOError:
                    continue
                for i in range(0, len(data) - EVENT.size + 1, EVENT.size):
                    _, _, kind, code, value = EVENT.unpack_from(data, i)
                    out.append((kind, code, value))
        return out

    def start(self):
        """Collect in the background: evdev's per-client buffer is small (an unread ramp overflows it)."""
        self.got, self.running = [], True
        def loop():
            while self.running:
                self.got.extend(self.read(0.05))
        self.thread = threading.Thread(target=loop, daemon=True)
        self.thread.start()
        return self

    def stop(self):
        self.running = False
        self.thread.join(2)
        got, self.got = self.got, []
        return got

    def close(self):
        if getattr(self, 'running', False):
            self.stop()
        os.close(self.fd)


def try_grab(path):
    """An exclusive reader of `path`, or None when another reader (a bridge) holds the grab. Holding it also keeps
    every other evdev reader on the host (SDL apps, a game launcher's desktop mapping) from seeing the test's events."""
    try:
        return Reader(path, grab=True)
    except OSError:
        return None


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not BIN.is_file():
            raise AssertionError(f'binary under test missing: {BIN}')
        cls.temp = tempfile.TemporaryDirectory(prefix='mios-inputd-i4-')
        cls.root = Path(cls.temp.name) / 'root'
        (cls.root / 'usr/share/mios').mkdir(parents=True)
        shutil.copy(ROOT / 'usr/share/mios/mios.toml', cls.root / 'usr/share/mios/mios.toml')
        cls.old_root = Path(cls.temp.name) / 'old-root'
        if OLD_SSOT.is_file():
            (cls.old_root / 'usr/share/mios').mkdir(parents=True)
            shutil.copy(OLD_SSOT, cls.old_root / 'usr/share/mios/mios.toml')
        cls.env = {k: v for k, v in os.environ.items() if not (k.startswith('MIOS_') and 'TOML' in k)}
        for k in ('HYPRLAND_INSTANCE_SIGNATURE', 'WAYLAND_DISPLAY', 'DISPLAY'):
            cls.env.pop(k, None)
        cls.env['HOME'] = str(Path(cls.temp.name) / 'home')
        cls.env['XDG_CONFIG_HOME'] = str(Path(cls.temp.name) / 'home/.config')
        cls.runtime = Path(cls.temp.name) / 'run'
        cls.runtime.mkdir(mode=0o700)
        cls.env['XDG_RUNTIME_DIR'] = str(cls.runtime)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def run_bin(self, *args, binary=None, root=None, data=None, timeout=20, env=None):
        return subprocess.run([str(binary or BIN), '--root', str(root or self.root), *args], input=data, text=True,
                              capture_output=True, env=env or self.env, timeout=timeout)

    def replay(self, context, script, binary=None, root=None, **extra):
        payload = dict(context=context, axes=AXES, **extra)
        if binary is None:
            payload['script'] = script
        else:
            payload['events'] = [s for s in script if 'kind' in s]
        r = self.run_bin('replay', binary=binary, root=root, data=json.dumps(payload))
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def need_old(self):
        if not (OLD.is_file() and OLD_SSOT.is_file()):
            self.skipTest('negative control needs MIOS_INPUTD_OLD_BIN + MIOS_INPUTD_OLD_SSOT (the pre-I4 pair)')

    def host_layer(self, text):
        path = self.root / 'etc/mios/mios.toml'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        self.addCleanup(path.unlink, missing_ok=True)


class Grammar(Base):
    def test_c19_system_monitor_is_a_mios_action_and_the_old_alias_is_gone(self):
        self.assertEqual(json.loads(self.run_bin('check').stdout)['unresolved_actions'], [])
        r = self.run_bin('resolve-action', 'mios:system-monitor')
        self.assertEqual(r.returncode, 0, r.stderr)
        argv = json.loads(r.stdout)['argv']
        self.assertEqual(argv[0], 'alacritty')
        self.assertEqual(argv[-2:], ['--action', 'system'])
        r = self.run_bin('resolve-action', 'quickshell:monitor-toggle')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('no installed IPC handler', r.stderr)
        for a, prog in [('mios:volume up', '/usr/bin/wpctl'), ('mios:volume down', '/usr/bin/wpctl'), ('mios:media previous', '/usr/bin/playerctl'), ('mios:media next', '/usr/bin/playerctl')]:
            self.assertEqual(json.loads(self.run_bin('resolve-action', a).stdout)['argv'][0], prog, a)
        mv = json.loads(self.run_bin('resolve-action', 'hypr:movewindow right').stdout)['argv']
        self.assertEqual(mv[2], 'hl.dispatch(hl.dsp.window.move({direction="right"}))')
        self.need_old()
        r = self.run_bin('resolve-action', 'quickshell:monitor-toggle', binary=OLD, root=self.old_root)
        self.assertEqual(r.returncode, 0, 'negative control: the pre-I4 pair still uses the quickshell alias')
        r = self.run_bin('resolve-action', 'hypr:movewindow right', binary=OLD, root=self.old_root)
        self.assertNotEqual(r.returncode, 0, 'negative control: the pre-I4 binary has no movewindow')

    def test_i4_ssot_needs_the_i4_binary(self):
        self.need_old()
        r = self.run_bin('check', binary=OLD)
        self.assertNotEqual(r.returncode, 0, 'the pre-I4 binary must refuse the I4 SSOT (LS_*/RS_* rows), not ignore them')
        self.assertRegex(r.stderr, r'unknown controller button (LS|RS)_|agent_pad|quirks|unknown field')

    def test_c15b_guide_layer_on_hat_sticks_and_analog_triggers(self):
        guide = [key(GUIDE)] + wait(20)
        cases = [
            ('HAT left', guide + ab(16, -1) + wait(800) + ab(16, 0) + [key(GUIDE, 0)], False, ['hypr:movefocus left']),
            ('LS up once per flick', guide + ab(1, -32767) + wait(800) + ab(1, -31000) + ab(1, 0) + ab(1, -32767) + [key(GUIDE, 0)], False, ['mios:volume up', 'mios:volume up']),
            ('LS left/right', guide + ab(0, -32767) + ab(0, 0) + ab(0, 32767) + [key(GUIDE, 0)], False, ['mios:media previous', 'mios:media next']),
            ('RS right', guide + ab(3, 32767) + [key(GUIDE, 0)], False, ['hypr:movewindow right']),
            ('analog LT', guide + ab(2, 255) + ab(2, 0) + [key(GUIDE, 0)], True, ['hypr:movetoworkspace e-1']),
            ('Start', guide + [key(START), key(START, 0), key(GUIDE, 0)], False, ['mios:system-monitor']),
        ]
        for name, script, analog, want in cases:
            with self.subTest(name):
                out = self.replay('desktop-2d', script, analog_triggers=analog)
                self.assertEqual(out['output']['actions'], want)
                self.assertEqual([e for e in out['output']['pointer'] if e['kind'] == 2], [], 'no pointer motion or wheel under Guide')
                # Only the Guide press's release-to-rest frame reaches the virtual pad: no key, no deflected axis.
                self.assertEqual([e for e in out['output']['app'] if e['kind'] == 1 or (e['kind'] == 3 and e['value'] != 0)], [])
        out = self.replay('desktop-2d', [key(GUIDE)] + ab(3, 32767))
        self.assertIn('hl.dsp.window.move', json.dumps(out['resolved']))
        # Without Guide the HAT is nav arrows; in a capture it reaches the app unchanged.
        self.assertEqual(self.replay('desktop-2d', ab(16, -1))['output']['actions'], ['key:LEFT'])
        cap = self.replay('app-capture', ab(16, -1) + [key(GUIDE)] + ab(16, 1) + [key(GUIDE, 0)])
        self.assertEqual(cap['output']['actions'], [])
        self.assertIn(dict(kind=3, code=16, value=-1), cap['output']['app'])
        self.need_old()
        old = self.replay('desktop-2d', [key(GUIDE)] + ab(16, -1) + ab(3, 32767) + [key(GUIDE, 0)], binary=OLD, root=self.old_root)
        self.assertFalse({'hypr:movefocus left', 'hypr:movewindow right'} & set(old['output']['actions']),
                         'negative control: the pre-I4 router swallows the HAT and the sticks under Guide')
        self.assertIn('quickshell:drawer-toggle', old['output']['actions'], 'and its quick Guide+HAT also opened the drawer (fixed: a layer action is no Guide tap)')

    def test_o7_quirk_table_is_validated(self):
        for body, why in [('remap = { BTN_NORTH = "BTN_WEST", BTN_SOUTH = "BTN_WEST" }', 'one-to-one'),
                          ('remap = { "0x999" = "BTN_WEST" }', 'outside the evdev button range'),
                          ('ids = ["45e:28e"]', 'vvvv:pppp')]:
            with self.subTest(body=body):
                self.host_layer(f'[keybindings.global.controller.quirks.xpad]\n{body}\n')
                r = self.run_bin('check')
                self.assertNotEqual(r.returncode, 0)
                self.assertIn(why, r.stderr)
        self.host_layer('[keybindings.global.controller.agent_pad]\nrate_hz = 0\n')
        self.assertIn('rate_hz', self.run_bin('check').stderr)


@unittest.skipUnless(UINPUT, 'set MIOS_INPUTD_UINPUT_TEST=1 for the private uinput pads')
class Vpad(Base):
    def setUp(self):
        self.pads = []
        self.addCleanup(self.destroy_all)

    def destroy_all(self):
        for p in self.pads:
            self.run_bin('vpad', 'destroy', '--pad', p)
        for p in self.pads:
            until(lambda: not (self.runtime / f'mios-inputd/vpad/{p}.sock').exists(), 5)

    def create(self, pad, *extra):
        r = self.run_bin('vpad', 'create', '--pad', pad, '--fixture', *extra)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.pads.append(pad)
        out = json.loads(r.stdout)
        self.assertTrue(out['ok'])
        self.assertEqual(out['created']['name'], f'mios-input-fixture-vpad-{pad}')
        return out['created']

    def vp(self, op, pad, *args):
        r = self.run_bin('vpad', op, '--pad', pad, *args, timeout=40)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_an_eased_stick_stream_never_jumps_and_buttons_tap(self):
        c = self.create('ease1')
        node = c['path']
        rd = Reader(node, grab=True)
        self.addCleanup(rd.close)
        rd.start()
        self.vp('stick', 'ease1', '--stick', 'left', '--y', '-1', '--ms', '400', '--wait')
        time.sleep(0.1)
        ev = rd.stop()
        ys = [v for k, c_, v in ev if k == 3 and c_ == 1]
        self.assertGreaterEqual(len(ys), 40, f'sampled, not stepped: {len(ys)} values')
        self.assertEqual(ys[-1], -32767)
        steps = [abs(b - a) for a, b in zip([0] + ys, ys)]
        self.assertLessEqual(max(steps), 4000, f'no teleport: largest step {max(steps)} of 32767 (scheduling jitter allowed)')
        self.assertTrue(all(b <= a for a, b in zip(ys, ys[1:])), 'monotone ramp')
        self.assertLess(steps[0], 300, 'eased start')
        self.assertLess(steps[-1], 300, 'eased stop')
        # hold_ms: ramp, hold, ease back to rest by itself.
        rd.start()
        self.vp('stick', 'ease1', '--stick', 'right', '--x', '0.6', '--ms', '100', '--hold-ms', '150', '--wait')
        time.sleep(0.1)
        xs = [v for k, c_, v in rd.stop() if k == 3 and c_ == 3]
        self.assertIn(round(0.6 * 32767), xs)
        self.assertEqual(xs[-1], 0)
        rd.start()
        self.vp('button', 'ease1', '--button', 'BTN_SOUTH', '--action', 'tap', '--ms', '60', '--wait')
        self.vp('dpad', 'ease1', '--dir', 'left', '--wait')
        self.vp('trigger', 'ease1', '--side', 'right', '--value', '1', '--ms', '100', '--wait')
        time.sleep(0.1)
        ev = rd.stop()
        self.assertIn((1, SOUTH, 1), ev)
        self.assertIn((1, SOUTH, 0), ev)
        self.assertIn((3, 16, -1), ev)
        self.assertIn((3, 16, 0), ev, 'a d-pad tap re-centres')
        self.assertIn((1, RT, 1), ev, 'the trigger button follows the analog value')
        st = self.vp('status', 'ease1')
        self.assertEqual(st['left'], [0.0, -1.0])
        self.vp('release', 'ease1', '--ms', '100', '--wait')
        self.assertEqual(self.vp('status', 'ease1')['triggers'], [0.0, 0.0])
        self.assertEqual([p['pad'] for p in json.loads(self.run_bin('vpad', 'list').stdout)], ['ease1'])
        self.vp('destroy', 'ease1')
        self.assertTrue(until(lambda: not Path(node).exists() or 'mios-input-fixture-vpad-ease1' not in Path('/proc/bus/input/devices').read_text(), 5), 'the pad is destroyed with its holder')
        self.assertEqual(json.loads(self.run_bin('vpad', 'list').stdout), [])
        r = self.run_bin('vpad', 'status', '--pad', 'ease1')
        self.assertNotEqual(r.returncode, 0)

    def test_contrast_a_raw_fixture_step_is_what_the_pad_never_does(self):
        pad = subprocess.Popen([str(BIN), 'fixture-pad', '--layout', 'dualsense'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.addCleanup(lambda: (pad.stdin.close(), pad.wait(5)))
        info = json.loads(pad.stdout.readline())
        rd = Reader(info['path'], grab=True)
        self.addCleanup(rd.close)
        pad.stdin.write(json.dumps({'events': [[3, 1, -32767]]}) + '\n')
        pad.stdin.flush()
        pad.stdout.readline()
        ys = [v for k, c, v in rd.read(0.2) if k == 3 and c == 1]
        self.assertEqual(ys, [-32767], 'a raw write teleports the stick in one sample')

    def test_requests_are_validated_and_the_old_binary_has_no_pad(self):
        self.create('val1')
        for args, why in [(['stick', '--ease', 'bounce'], 'ease'), (['button', '--button', 'BTN_TL2'], 'analog'), (['dpad', '--dir', 'sideways'], 'dpad dir')]:
            r = self.run_bin('vpad', args[0], '--pad', 'val1', *args[1:])
            self.assertNotEqual(r.returncode, 0, args)
            self.assertIn(why, r.stderr)
        r = self.run_bin('vpad', 'create', '--pad', 'val1', '--fixture')
        self.assertNotEqual(r.returncode, 0, 'one holder per pad')
        self.assertIn('already exists', r.stderr)
        r = self.run_bin('vpad', 'create', '--pad', '../x', '--fixture')
        self.assertNotEqual(r.returncode, 0)
        if OLD.is_file():
            r = self.run_bin('vpad', 'create', '--pad', 'old', binary=OLD, root=self.old_root if OLD_SSOT.is_file() else None)
            self.assertNotEqual(r.returncode, 0, 'negative control: the pre-I4 binary has no agent pad')

    def test_the_mcp_verb_argv_drives_the_pad(self):
        sys.path.insert(0, '/usr/lib/mios/agent-pipe')
        try:
            from mios_template import compile_template
        except Exception as e:  # pragma: no cover - the agent plane is not installed
            self.skipTest(f'agent-pipe template renderer unavailable: {e}')
        import tomllib
        verb = tomllib.loads((ROOT / 'usr/share/mios/mios.toml').read_text())['verbs']['virtual_controller']
        c = self.create('mcp1')
        rd = Reader(c['path'], grab=True)
        self.addCleanup(rd.close)
        line = compile_template(verb['cmd']).render('virtual_controller', dict(op='stick', pad='mcp1', stick='left', x=0.5, y=0, ms=120, wait=True))
        argv = shlex.split(line)
        self.assertEqual(argv[:3], ['/usr/libexec/mios/mios-inputd', 'vpad', 'stick'])
        r = subprocess.run([str(BIN), '--root', str(self.root)] + argv[1:], capture_output=True, text=True, env=self.env, timeout=20)
        self.assertEqual(r.returncode, 0, r.stderr + line)
        self.assertEqual(json.loads(r.stdout)['wait']['state']['left'], [0.5, 0.0])
        self.assertIsNone(compile_template(verb['cmd']).render('virtual_controller', dict(pad='mcp1')), 'op is required')

    def test_an_idle_pad_exits_and_takes_its_device_with_it(self):
        self.host_layer('[keybindings.global.controller.agent_pad]\nidle_exit_s = 10\n')
        c = self.create('idle1')
        self.assertTrue(until(lambda: not Path(f"/proc/{c['pid']}").exists(), 16, 0.25), 'the holder exits at rest after idle_exit_s')
        self.assertNotIn('mios-input-fixture-vpad-idle1', Path('/proc/bus/input/devices').read_text())


class FakeCompositor:
    """A Hyprland IPC socket in a private runtime: answers the context and ownership Lua and records projections."""

    def __init__(self, runtime, sig, pid):
        self.dir = Path(runtime) / 'hypr' / sig
        self.dir.mkdir(parents=True)
        self.state = dict(cls='org.test.App', address='0xabc', pid=pid, captured=False, room=False)
        self.calls = []
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(str(self.dir / '.socket.sock'))
        self.sock.listen(16)
        self.alive = True
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        while self.alive:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            c.settimeout(0.05)
            data = b''
            try:
                while True:
                    chunk = c.recv(65536)
                    if not chunk:
                        break
                    data += chunk
            except socket.timeout:
                pass
            req = data.decode(errors='replace')
            reply = 'ok'
            s = self.state
            if 'MIOS_INPUT_CONTEXT' in req:
                reply = 'MIOS_INPUT_CONTEXT:' + json.dumps({'class': s['cls'], 'address': s['address'], 'pid': s['pid'], 'room': s['room'],
                                                            'captured': s['captured'], 'released': False, 'fullscreen': 0, 'handler': ''})
            elif 'MIOS_INPUT_WINDOW_OK' in req:
                mode = re.search(r'local mode="(\w+)"', req).group(1)
                self.calls.append(('window', mode))
                s['captured'] = mode != 'off'
                reply = f"MIOS_INPUT_WINDOW_OK:{s['address']} {s['cls']}"
            elif 'send_key_state' in req:
                self.calls.append(('keys', re.findall(r"key=\"([^\"]*)\",state='(\w+)'", req)))
            elif 'hl.dsp.window.move' in req:
                self.calls.append(('move', re.search(r'direction="(\w+)"', req).group(1)))
            elif 'hl.dsp.focus' in req:
                m = re.search(r'direction="(\w+)"', req)
                self.calls.append(('focus', m.group(1) if m else req))
            try:
                c.sendall(reply.encode())
            finally:
                c.close()

    def close(self):
        self.alive = False
        self.sock.close()

    def keys(self):
        return [k for kind, v in self.calls if kind == 'keys' for k, st in v if st == 'down']


@unittest.skipUnless(UINPUT, 'set MIOS_INPUTD_UINPUT_TEST=1 for the fixture pads, fake compositor and fake nested registry')
class Fixture(Base):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='mios-inputd-i4-fx-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.procs = []
        self.addCleanup(self.stop_all)
        self.reg = self.runtime / 'mios/nested-sessions'
        self.reg.mkdir(parents=True, exist_ok=True)

    def stop_all(self):
        for p in reversed(self.procs):
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait()
        for f in self.reg.glob('*.json'):
            f.unlink()
        for f in (self.runtime / 'mios-inputd/vpad').glob('*.sock') if (self.runtime / 'mios-inputd/vpad').is_dir() else []:
            self.run_bin('vpad', 'destroy', '--pad', f.stem)

    def spawn(self, argv, env=None, **kw):
        p = subprocess.Popen(argv, env=env or self.env, **kw)
        self.procs.append(p)
        return p

    def fixture_pad(self, layout='dualsense'):
        pad = self.spawn([str(BIN), 'fixture-pad', '--layout', layout], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        info = json.loads(pad.stdout.readline())
        pad.info = info
        return pad

    def send(self, pad, *events, hold=0.05):
        pad.stdin.write(json.dumps({'events': [list(e) for e in events]}) + '\n')
        pad.stdin.flush()
        pad.stdout.readline()
        time.sleep(hold)

    def logged(self, proc):
        log = []
        for stream in (proc.stdout, proc.stderr):
            threading.Thread(target=lambda s=stream: [log.append(l.rstrip()) for l in s], daemon=True).start()
        return log

    def compositor(self, runtime, sig, **state):
        runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
        fake = FakeCompositor(runtime, sig, os.getpid())
        fake.state.update(state)
        self.addCleanup(fake.close)
        return fake

    def bridge(self, device, binary=None, sig='fake_fx', **state):
        fake = self.compositor(self.runtime, sig, **state)
        env = dict(self.env, HYPRLAND_INSTANCE_SIGNATURE=sig)
        br = self.spawn([str(binary or BIN), '--root', str(self.root), 'bridge', '--fixture-only', '--device', device, '--live-context',
                         '--pad-path', str(self.tmp / 'pad.bin')], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        log = self.logged(br)
        return fake, br, log

    def ready(self, log, what='"mode":"bridge"', seconds=10):
        ok = until(lambda: any(what in l for l in log), seconds)
        self.assertTrue(ok, log[-8:])
        time.sleep(0.3)

    # --- O-7 -----------------------------------------------------------------------------------------------------
    def test_o7_xpad_face_order_is_fixed_and_dualsense_needs_no_fixup(self):
        for layout, top, left in [('xpad', WEST, NORTH), ('dualsense', NORTH, WEST)]:
            with self.subTest(layout=layout):
                pad = self.fixture_pad(layout)
                fake, br, log = self.bridge(pad.info['path'], sig=f'fake_{layout}')
                self.ready(log)
                self.assertEqual(any('"quirks":["xpad"]' in l for l in log), layout == 'xpad', log[:6])
                self.send(pad, (1, left, 1), hold=0.05)
                self.send(pad, (1, left, 0), hold=0.4)
                self.assertIn('Tab', fake.keys(), f'{layout}: the physical left button is West (Tab)')
                self.send(pad, (1, top, 1), hold=0.05)
                self.send(pad, (1, top, 0), hold=0.6)
                self.assertIn(('window', 'on'), fake.calls, f'{layout}: the physical top button is North (capture)')
                br.terminate(); br.wait(5)

    def test_o7_negative_the_pre_i4_binary_reads_xpad_top_as_west(self):
        self.need_old()
        pad = self.fixture_pad('xpad')
        root = self.old_root
        fake = self.compositor(self.runtime, 'fake_old')
        env = dict(self.env, HYPRLAND_INSTANCE_SIGNATURE='fake_old')
        br = self.spawn([str(OLD), '--root', str(root), 'bridge', '--fixture-only', '--device', pad.info['path'], '--live-context',
                         '--pad-path', str(self.tmp / 'pad.bin')], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        log = self.logged(br)
        self.ready(log)
        self.send(pad, (1, WEST, 1), hold=0.05)
        self.send(pad, (1, WEST, 0), hold=0.6)
        self.assertNotIn(('window', 'on'), fake.calls)
        self.assertIn('Tab', fake.keys(), 'without the quirk the Xbox top button types Tab')

    def test_o7_xpad_dpad_buttons_navigate_like_a_hat(self):
        pad = self.fixture_pad('xpad-dpad-buttons')
        fake, br, log = self.bridge(pad.info['path'], sig='fake_dpadb')
        self.ready(log)
        for code in (0x2c0, 0x2c3):
            self.send(pad, (1, code, 1), hold=0.05)
            self.send(pad, (1, code, 0), hold=0.2)
        self.assertEqual([k for k in fake.keys() if k in ('Left', 'Down', 'Up', 'Right')], ['Left', 'Down'])
        self.send(pad, (1, GUIDE, 1), hold=0.05)
        self.send(pad, (1, 0x2c1, 1), (1, 0x2c1, 0), hold=0.05)
        self.send(pad, (1, GUIDE, 0), hold=0.3)
        self.assertIn(('focus', 'right'), fake.calls, 'Guide + the d-pad button focuses (C-15a on a dpad_to_buttons pad)')

    def test_o7_a_captured_app_gets_the_drivers_own_codes(self):
        pad = self.fixture_pad('xpad')
        fake, br, log = self.bridge(pad.info['path'], sig='fake_cap', captured=True)
        self.ready(log)
        clone = f"mios-vctl-{Path(pad.info['path']).name}"
        text = Path('/proc/bus/input/devices').read_text()
        block = next(b for b in text.split('\n\n') if f'Name="{clone}"' in b)
        node = '/dev/input/' + re.search(r'event\d+', block.split('Handlers=')[1]).group(0)
        rd = Reader(node, grab=True)
        self.addCleanup(rd.close)
        self.send(pad, (1, WEST, 1), hold=0.1)
        self.send(pad, (1, WEST, 0), hold=0.1)
        keys = [(c, v) for k, c, v in rd.read(0.3) if k == 1]
        self.assertIn((WEST, 1), keys, 'the clone reports the code xpad reported (the app maps by its own database)')
        self.assertNotIn((NORTH, 1), keys)

    # --- C-15b on a real HAT pad ---------------------------------------------------------------------------------
    def test_c15b_guide_layer_on_a_hat_pad(self):
        pad = self.fixture_pad('dualsense')
        fake, br, log = self.bridge(pad.info['path'], sig='fake_guide')
        self.ready(log)
        self.send(pad, (3, 16, -1), hold=0.1)
        self.send(pad, (3, 16, 0), hold=0.2)
        self.assertIn('Left', fake.keys(), 'without Guide the HAT is an arrow key')
        self.send(pad, (1, GUIDE, 1), hold=0.05)
        self.send(pad, (3, 16, -1), hold=0.6)
        self.send(pad, (3, 16, 0), hold=0.05)
        self.send(pad, (3, 3, 32767), hold=0.1)
        self.send(pad, (3, 3, 0), hold=0.05)
        self.send(pad, (3, 1, -32767), hold=0.1)
        self.send(pad, (3, 1, 0), hold=0.05)
        self.send(pad, (1, GUIDE, 0), hold=0.4)
        self.assertEqual([c for c in fake.calls if c[0] == 'focus'], [('focus', 'left')], 'one focus per press, no repeat')
        self.assertIn(('move', 'right'), fake.calls)
        self.assertTrue(any('"action":"mios:volume up"' in l for l in log), log[-10:])
        self.assertEqual(sum('"action":"mios:volume up"' in l for l in log), 1)

    # --- the nested-session contract -----------------------------------------------------------------------------
    def registry(self, seat, node, launcher, runtime=None, sig='sess_sig', pad_state=None, state='running'):
        entry = dict(seat=seat, launcher_pid=launcher.pid, launcher_start=start_time(launcher.pid), state=state,
                     runtime=str(runtime or self.tmp / 'sess'), instance=sig, wayland_display='wayland-test',
                     pad_state=str(pad_state) if pad_state else None,
                     devices=[dict(path=node, kind='controller', siblings=[], name='', spec='', virtual=True)])
        p = self.reg / f'{seat}.json'
        p.write_text(json.dumps(entry))
        return p

    def launcher(self):
        return self.spawn(['sleep', '120'])

    def test_the_host_bridge_never_owns_a_node_a_nested_session_holds(self):
        pad = self.fixture_pad('dualsense')
        self.registry('p2', pad.info['path'], self.launcher())
        fake, br, log = self.bridge(pad.info['path'], sig='fake_skip')
        self.ready(log, '"nested":"skipped"')
        self.assertFalse(any('"mode":"bridge"' in l for l in log), 'the assigned node was opened')
        rd = try_grab(pad.info['path'])
        self.assertIsNotNone(rd, 'not grabbed by the host bridge: the session owns its node')
        self.addCleanup(rd.close)
        self.send(pad, (1, SOUTH, 1), (1, SOUTH, 0), hold=0.3)
        self.assertIn((1, SOUTH, 1), rd.read(0.2))
        self.assertEqual(fake.keys(), [])
        if OLD.is_file() and OLD_SSOT.is_file():
            br.terminate(); br.wait(5)
            pad2 = self.fixture_pad('dualsense')
            self.registry('p3', pad2.info['path'], self.launcher())
            fake2 = self.compositor(self.runtime, 'fake_skip_old')
            env = dict(self.env, HYPRLAND_INSTANCE_SIGNATURE='fake_skip_old')
            ob = self.spawn([str(OLD), '--root', str(self.old_root), 'bridge', '--fixture-only', '--device', pad2.info['path'], '--live-context',
                             '--pad-path', str(self.tmp / 'pad-old.bin')], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            olog = self.logged(ob)
            self.ready(olog)
            self.assertIsNone(try_grab(pad2.info['path']), 'negative control: the pre-I4 bridge grabbed the assigned node')
            self.send(pad2, (1, SOUTH, 1), (1, SOUTH, 0), hold=0.4)
            self.assertIn('Return', fake2.keys(), 'negative control: the pre-I4 bridge owns a node the session was given')

    def test_a_node_assigned_after_the_bridge_owned_it_is_handed_over(self):
        pad = self.fixture_pad('dualsense')
        fake, br, log = self.bridge(pad.info['path'], sig='fake_handover')
        self.ready(log)
        self.registry('p4', pad.info['path'], self.launcher())
        self.ready(log, '"nested":"released"', 6)
        rd = try_grab(pad.info['path'])
        self.assertIsNotNone(rd, 'the grab was released to the session')
        self.addCleanup(rd.close)
        self.send(pad, (1, SOUTH, 1), (1, SOUTH, 0), hold=0.3)
        self.assertIn((1, SOUTH, 1), rd.read(0.2))
        self.assertNotIn('Return', fake.keys())

    def test_a_seat_pad_is_never_the_hosts(self):
        self.host_layer('[desktop.nested.devices]\nvirtual_pad_name = "mios-input-fixture-vpad-{seat}"\n')
        r = self.run_bin('vpad', 'create', '--pad', 'seat1', '--fixture')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.addCleanup(self.run_bin, 'vpad', 'destroy', '--pad', 'seat1')
        node = json.loads(r.stdout)['created']['path']
        fake, br, log = self.bridge(node, sig='fake_seatpad')
        self.ready(log, '"nested":"seat-pad"')
        self.assertFalse(any('"mode":"bridge"' in l for l in log))

    def session(self, seat, node, room=True, captured=False):
        runtime = self.tmp / f'sess-{seat}'
        sig = f'sess_{seat}'
        fake = self.compositor(runtime, sig, room=room, captured=captured)
        pad_state = runtime / 'tmp/mios-hypr3d-pad.bin'
        pad_state.parent.mkdir()
        launcher = self.launcher()
        self.registry(seat, node, launcher, runtime=runtime, sig=sig, pad_state=pad_state)
        return fake, pad_state, launcher

    def ly(self, pad_state):
        try:
            b = pad_state.read_bytes()
        except OSError:
            return None
        return PACKET.unpack(b)[4] if len(b) == 68 else None

    def test_vpad_assign_follows_the_session_and_eases_its_room(self):
        c = json.loads(self.run_bin('vpad', 'create', '--pad', 'room1', '--fixture').stdout)['created']
        self.addCleanup(self.run_bin, 'vpad', 'destroy', '--pad', 'room1')
        fake, pad_state, launcher = self.session('room1', c['path'])
        r = self.run_bin('vpad', 'assign', '--pad', 'room1')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)['assigned'], 'room1')
        self.assertTrue(until(lambda: self.ly(pad_state) is not None, 10), 'the bridge writes the session room packet')
        time.sleep(0.5)
        self.run_bin('vpad', 'stick', '--pad', 'room1', '--stick', 'left', '--y', '-1', '--ms', '800')
        series, end = [], time.monotonic() + 1.2
        while time.monotonic() < end:
            series.append(self.ly(pad_state))
            time.sleep(0.01)
        vals = [v for v in series if v is not None]
        mids = sorted({round(v, 3) for v in vals if -0.95 < v < -0.05})
        self.assertGreaterEqual(len(mids), 8, f'the room sees a ramp, not a step: {mids}')
        self.assertLess(min(vals), -0.9)
        self.assertTrue(all(b <= a + 1e-6 for a, b in zip(vals, vals[1:])), 'monotone forward ramp')
        # Keys project into the session's compositor (desktop-2d there), never the host keyboard.
        fake.state['room'] = False
        time.sleep(0.3)
        self.run_bin('vpad', 'release', '--pad', 'room1', '--ms', '80', '--wait')
        self.run_bin('vpad', 'button', '--pad', 'room1', '--button', 'BTN_SOUTH', '--wait')
        self.assertTrue(until(lambda: 'Return' in fake.keys(), 3), fake.calls[-5:])
        # A captured app in the session reads the node itself: the grab is released while it owns the pad.
        blog = self.runtime / 'mios-inputd/vpad/room1-bridge.log'
        n_pass, n_grab = blog.read_text().count('"nested":"passthrough"'), blog.read_text().count('"nested":"grab"')
        fake.state['captured'] = True
        self.assertTrue(until(lambda: blog.read_text().count('"nested":"passthrough"') > n_pass, 3))
        rd = try_grab(c['path'])
        self.assertIsNotNone(rd, 'passthrough while captured: the session app may own the node\n' + blog.read_text()[-1500:])
        self.run_bin('vpad', 'button', '--pad', 'room1', '--button', 'BTN_EAST', '--wait')
        self.assertIn((1, EAST, 1), rd.read(0.3))
        self.assertNotIn('Escape', fake.keys())
        rd.close()
        fake.state['captured'] = False
        self.assertTrue(until(lambda: blog.read_text().count('"nested":"grab"') > n_grab, 3), 'released capture: the bridge grabs again')
        self.assertIsNone(try_grab(c['path']))
        # The session ends: its bridge exits by itself.
        launcher.terminate(); launcher.wait(5)
        self.assertTrue(until(lambda: '"nested":"ended"' in (self.runtime / 'mios-inputd/vpad/room1-bridge.log').read_text(), 6))
        info = json.loads(self.run_bin('vpad', 'info', '--pad', 'room1').stdout)
        self.assertTrue(until(lambda: json.loads(self.run_bin('vpad', 'info', '--pad', 'room1').stdout)['assigned'] is None, 5), info)

    def test_assign_refuses_a_session_that_does_not_hold_the_pad(self):
        c = json.loads(self.run_bin('vpad', 'create', '--pad', 'room2', '--fixture').stdout)['created']
        self.addCleanup(self.run_bin, 'vpad', 'destroy', '--pad', 'room2')
        self.session('room2', '/dev/input/event999')
        r = self.run_bin('vpad', 'assign', '--pad', 'room2')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('does not hold this pad', r.stderr)
        r = self.run_bin('vpad', 'assign', '--pad', 'room2', '--seat', 'nobody')
        self.assertIn('no live nested session', r.stderr)

    def test_contrast_a_raw_step_reaches_the_room_in_one_sample(self):
        pad = self.fixture_pad('dualsense')
        fake, pad_state, _ = self.session('step1', pad.info['path'])
        br = self.spawn([str(BIN), '--root', str(self.root), 'bridge', '--nested', 'step1', '--fixture-only'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        log = self.logged(br)
        self.ready(log)
        self.assertTrue(until(lambda: self.ly(pad_state) is not None, 10))
        time.sleep(0.3)
        self.send(pad, (3, 1, -32767), hold=0)
        series, end = [], time.monotonic() + 0.4
        while time.monotonic() < end:
            series.append(self.ly(pad_state))
            time.sleep(0.005)
        mids = {round(v, 3) for v in series if v is not None and -0.95 < v < -0.05}
        self.assertLessEqual(len(mids), 1, f'a raw step has no ramp: {mids}')
        if OLD.is_file() and OLD_SSOT.is_file():
            r = subprocess.run([str(OLD), '--root', str(self.old_root), 'bridge', '--nested', 'step1', '--fixture-only'], capture_output=True, text=True, env=self.env, timeout=10)
            self.assertNotEqual(r.returncode, 0, 'negative control: the pre-I4 binary cannot follow a nested session')
            self.assertIn('--nested', r.stderr)


if __name__ == '__main__':
    unittest.main(verbosity=2)
