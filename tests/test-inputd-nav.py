#!/usr/bin/env python3
# AI-hint: Real-binary two-sided tests for mios-inputd step I2 (the nav grammar): uncaptured windows of every kind navigate (key:/pointer:/hypr:/tmux:), tap/hold/repeat/flick timing, thumb and Guide disambiguation, cursor mode keeps button meanings, grammar validation, the uinput virtual keyboard, and (fake compositor + private tmux server) tmux discovery, tmux verbs and the capture OSD.
# AI-related: tools/native/mios-inputd/src/nav.rs, tools/native/mios-inputd/src/router.rs, tools/native/mios-inputd/src/runtime.rs, docs/design/mios-global-bindings-tests.md (I2), usr/lib/udev/rules.d/70-mios-input.rules
# AI-functions: Base, Grammar, Timing, Projection, FakeCompositor, Bridge
# Environment: MIOS_INPUTD_BIN = the binary under test (default: installed). MIOS_INPUTD_OLD_BIN = the pre-I2 binary
# for the negative controls (skipped when absent; it reads the same SSOT and ignores the nav tables).
# MIOS_INPUTD_UINPUT_TEST=1 enables the private uinput tests (a grabbed fixture keyboard, a fixture pad driving the
# bridge against a fake compositor socket in a private XDG_RUNTIME_DIR, and a private tmux server). No physical device,
# no real compositor and no operator tmux server is ever touched.
import json
import os
from pathlib import Path
import pty
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
BIN = Path(os.environ.get('MIOS_INPUTD_BIN', '/usr/libexec/mios/mios-inputd'))
OLD = Path(os.environ.get('MIOS_INPUTD_OLD_BIN', '/nonexistent'))
UINPUT = os.environ.get('MIOS_INPUTD_UINPUT_TEST') == '1'
SOUTH, EAST, WEST, NORTH, LB, RB, LT, RT = 0x130, 0x131, 0x134, 0x133, 0x136, 0x137, 0x138, 0x139
SELECT, START, GUIDE, L3, R3 = 0x13a, 0x13b, 0x13c, 0x13d, 0x13e
STICK = {c: dict(min=-32768, max=32767, value=0) for c in ('0', '1', '3', '4')}
AXES = dict(STICK, **{'2': dict(min=0, max=255, value=0), '5': dict(min=0, max=255, value=0),
                      '16': dict(min=-1, max=1, value=0), '17': dict(min=-1, max=1, value=0)})


def key(code, value=1):
    return dict(kind=1, code=code, value=value)


def ab(code, value):
    return [dict(kind=3, code=code, value=value), dict(kind=0, code=0, value=0)]


def wait(ms):
    return [dict(tick_ms=ms)]


def tap(code, ms=50):
    return [key(code)] + wait(ms) + [key(code, 0)]


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not BIN.is_file():
            raise AssertionError(f'binary under test missing: {BIN}')
        cls.temp = tempfile.TemporaryDirectory(prefix='mios-inputd-i2-')
        cls.root = Path(cls.temp.name) / 'root'
        (cls.root / 'usr/share/mios').mkdir(parents=True)
        shutil.copy(ROOT / 'usr/share/mios/mios.toml', cls.root / 'usr/share/mios/mios.toml')
        cls.env = {k: v for k, v in os.environ.items() if not (k.startswith('MIOS_') and 'TOML' in k)}
        cls.env.pop('HYPRLAND_INSTANCE_SIGNATURE', None)
        cls.env['HOME'] = str(Path(cls.temp.name) / 'home')
        cls.env['XDG_CONFIG_HOME'] = str(Path(cls.temp.name) / 'home/.config')

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def run_bin(self, *args, binary=None, data=None, timeout=20, env=None):
        return subprocess.run([str(binary or BIN), '--root', str(self.root), *args], input=data, text=True,
                              capture_output=True, env=env or self.env, timeout=timeout)

    def replay(self, context, script, binary=None, **extra):
        payload = dict(context=context, axes=AXES, **extra)
        if binary is None:
            payload['script'] = script
        else:
            # The pre-I2 replay has no timed script: events only, 10 ms apart, one final tick.
            payload['events'] = [s for s in script if 'kind' in s]
        r = self.run_bin('replay', binary=binary, data=json.dumps(payload))
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def actions(self, context, script, **extra):
        return self.replay(context, script, **extra)['output']['actions']

    def need_old(self):
        if not OLD.is_file():
            self.skipTest('negative control needs MIOS_INPUTD_OLD_BIN (the pre-I2 binary)')

    def host_layer(self, text):
        path = self.root / 'etc/mios/mios.toml'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        self.addCleanup(path.unlink, missing_ok=True)


class Grammar(Base):
    def test_c08_value_grammar_and_contract_inputs_are_validated(self):
        self.assertEqual(json.loads(self.run_bin('check').stdout)['unresolved_actions'], [])
        for body, why in [('BTN_SOUTH = ""', '"" is not a value'), ('BTN_SOUTH = "app"', 'namespace'),
                          ('BTN_SOUTH = "key:HYPER+a"', 'unknown modifier'), ('BTN_TL = "key:ENTER"', 'capture contract'),
                          ('BTN_NORTH = "key:ENTER"', 'capture contract'), ('BTN_MODE = ["key:ENTER", "key:ESC"]', 'Guide hold'),
                          ('BTN_SOUTH = "pointer:wheel-up"', 'only LS_*/RS_*')]:
            with self.subTest(body=body):
                self.host_layer(f'[keybindings.global.controller.nav.desktop]\n{body}\n')
                r = self.run_bin('check')
                self.assertNotEqual(r.returncode, 0, body)
                self.assertIn(why, r.stderr)
        self.host_layer('[keybindings.global.controller.nav.terminal_tmux]\nBTN_TL2 = "tmux:key r"\n')
        r = self.run_bin('check')
        self.assertIn('not a prefix-table key', r.stderr, 'tests.md C-30: a key the SSOT prefix table does not bind (r)')


    def test_c08_arrays_replace_atomically_across_tiers(self):
        self.host_layer('[keybindings.global.controller.nav.desktop]\nBTN_SOUTH = ["key:SPACE", "none"]\n')
        self.assertEqual(self.actions('desktop-2d', tap(SOUTH, 700)), [], 'the hold half is really unbound')
        self.assertEqual(self.actions('desktop-2d', tap(SOUTH, 50)), ['key:SPACE'])
        self.assertEqual(self.actions('desktop-2d', tap(EAST, 50)), ['key:ESC'], 'other inputs keep the vendor map')

    def test_o1_one_line_reverts_a_context_to_the_virtual_pad(self):
        self.host_layer('[keybindings.global.controller.nav.contexts]\ndesktop-2d = "vpad"\n')
        out = self.replay('desktop-2d', tap(SOUTH))
        self.assertEqual(out['output']['actions'], [])
        self.assertIn(dict(kind=1, code=SOUTH, value=1), out['output']['app'])
        self.assertEqual(self.actions('terminal', tap(SOUTH)), ['key:ENTER'], 'per-key table merge keeps terminal on nav')


class Timing(Base):
    def test_c08_tap_hold_and_no_repeat_of_holds(self):
        self.assertEqual(self.actions('desktop-2d', tap(SOUTH, 100)), ['key:ENTER'])
        self.assertEqual(self.actions('desktop-2d', tap(SOUTH, 600)), ['key:SHIFT+ENTER'])
        self.assertEqual(self.actions('desktop-2d', tap(SOUTH, 2000)), ['key:SHIFT+ENTER'], 'a hold fires once')
        self.assertEqual(self.actions('desktop-2d', tap(WEST, 600)), ['key:SHIFT+TAB'])
        self.assertEqual(self.actions('desktop-2d', tap(EAST, 600)), ['key:ALT+LEFT'])

    def test_c08_dpad_hat_repeats_400_60(self):
        self.assertEqual(self.actions('desktop-2d', ab(17, -1) + wait(1000)), ['key:UP'] * 12)
        self.assertEqual(self.actions('desktop-2d', ab(16, 1) + wait(300) + ab(16, 0) + wait(1000)), ['key:RIGHT'])

    def test_inv6_focus_change_stops_repeat(self):
        out = self.actions('desktop-2d', [dict(context='desktop-2d')] + ab(17, 1) + wait(500) + [dict(context='game')] + wait(1000) + ab(17, 0))
        self.assertEqual(out, ['key:DOWN'] * 3, 'press + repeats at 400 and 460 ms, nothing after the focus moved')
        out = self.actions('desktop-2d', [key(SOUTH)] + wait(200) + [dict(context='app-capture')] + wait(600) + [key(SOUTH, 0)])
        self.assertEqual(out, [], 'a capture mid-hold cancels the hold')

    def test_every_uncaptured_window_kind_navigates_and_north_still_captures(self):
        for context in ('desktop-2d', 'game', 'vm-passthrough', 'terminal'):
            with self.subTest(context=context):
                self.assertEqual(self.actions(context, tap(SOUTH) + tap(RB) + tap(NORTH)),
                                 ['key:ENTER', 'mios:shaderglass toggle', 'input:window on'])

    def test_negative_old_binary_forwards_uncaptured_buttons_to_the_pad(self):
        self.need_old()
        for context in ('desktop-2d', 'game', 'terminal'):
            out = self.replay(context, tap(SOUTH), binary=OLD)
            self.assertEqual(out['output']['actions'], [], context)
            self.assertIn(dict(kind=1, code=SOUTH, value=1), out['output']['app'], f'pre-I2 {context}: raw pad button')

    def test_c13_thumbs_resolve_on_release_and_overlaps_cancel(self):
        self.assertEqual(self.actions('desktop-2d', tap(L3)), ['key:CTRL+t'])
        self.assertEqual(self.actions('desktop-2d', tap(R3)), ['key:CTRL+0'])
        fast = [key(L3), key(R3), key(L3, 0), key(R3, 0)]
        self.assertEqual(self.actions('desktop-2d', fast), ['mios:hyprland-3d-toggle'], 'the chord is the room toggle only')
        slow = [key(L3)] + wait(400) + [key(R3), key(R3, 0), key(L3, 0)]
        self.assertEqual(self.actions('desktop-2d', slow), [], 'a slow overlap fires no single click')
        self.assertEqual(self.actions('desktop-2d', ab(0, 30000) + tap(L3)), [], 'L3 while its stick is deflected')
        guide = [key(GUIDE), key(L3), key(R3), key(L3, 0), key(R3, 0), key(GUIDE, 0)]
        self.assertEqual(self.actions('desktop-2d', guide), [], 'Guide+L3+R3: neither lock nor the VM latch')
        self.assertEqual(self.actions('desktop-2d', [key(GUIDE), key(L3), key(L3, 0), key(GUIDE, 0)]), ['quickshell:lock'])

    def test_r26_guide_tap_is_the_drawer(self):
        self.assertEqual(self.actions('desktop-2d', tap(GUIDE, 100)), ['quickshell:drawer-toggle'])
        self.assertEqual(self.actions('desktop-2d', tap(GUIDE, 600)), [], 'a Guide hold is the modifier, never the drawer')
        self.assertEqual(self.actions('desktop-2d', [key(GUIDE)] + tap(LB) + [key(GUIDE, 0)]), ['hypr:workspace e-1'])
        self.assertEqual(self.actions('app-capture', tap(GUIDE, 100)), [], 'a captured app gets the Guide tap replayed')

    def test_c11_cursor_mode_keeps_meanings_rt_clicks_ls_scrolls_both_ways(self):
        out = self.replay('desktop-2d', tap(START, 10) + tap(SOUTH) + [key(RT)] + wait(700) + [key(RT, 0)] + ab(0, 32767) + wait(100))
        self.assertEqual(out['output']['actions'], ['input:cursor on', 'key:ENTER'], 'RT is a click, never key:CTRL+TAB')
        ptr = out['output']['pointer']
        self.assertIn(dict(kind=1, code=0x110, value=1), ptr)
        self.assertTrue(any(e['kind'] == 2 and e['code'] == 6 and e['value'] > 0 for e in ptr), 'REL_HWHEEL from the left stick X')
        if OLD.is_file():
            old = self.replay('desktop-2d', tap(START, 10) + tap(SOUTH), binary=OLD)
            self.assertEqual(old['output']['actions'], ['input:cursor on'], 'pre-I2 cursor mode dropped South')

    def test_sticks_scroll_and_flick(self):
        out = self.replay('desktop-2d', ab(1, -32767) + wait(200))
        wheel = sum(e['value'] for e in out['output']['pointer'] if e['kind'] == 2 and e['code'] == 8)
        self.assertGreater(wheel, 0)
        self.assertEqual(out['output']['actions'], [])
        self.assertEqual(self.actions('desktop-2d', ab(3, 30000) + wait(1500) + ab(3, 0) + ab(3, 30000)),
                         ['hypr:movefocus right', 'hypr:movefocus right'], 'one flick per deflection, never a repeat')

    def test_analog_only_triggers_are_digital_in_nav(self):
        self.assertEqual(self.actions('desktop-2d', ab(5, 200) + ab(5, 0), analog_triggers=True), ['key:CTRL+TAB'])
        self.assertEqual(self.actions('desktop-2d', ab(5, 200) + ab(5, 0)), [], 'a pad with digital triggers uses the button')


class Projection(Base):
    def test_c09_c30_terminal_profile_and_tmux_argv(self):
        out = self.replay('terminal', tap(LT) + tap(SELECT) + ab(3, -30000), tmux_client='/dev/pts/7')
        self.assertEqual(out['profile'], 'terminal_tmux')
        self.assertEqual(out['output']['actions'], ['tmux:key p', 'tmux:key w', 'tmux:focus left'])
        self.assertEqual(out['resolved'][0]['projection'][3:], ['send-keys', '-c', '/dev/pts/7', '-K', 'C-b', 'p'])
        self.assertEqual(out['resolved'][2]['projection']['at_edge'], 'hypr:movefocus left')
        plain = self.replay('terminal', tap(LT) + tap(L3))
        self.assertEqual(plain['profile'], 'terminal_plain')
        self.assertEqual(plain['output']['actions'], ['key:SHIFT+PAGEUP', 'exec:mios-terminal'])
        self.assertFalse(any(a.startswith('tmux:') for a in plain['output']['actions']), 'no tmux without a client')
        strokes = plain['resolved'][0]['projection']['events']
        self.assertNotIn(dict(kind=1, code=48, value=1), strokes, 'a bare shell never receives Ctrl+B')
        r = self.run_bin('resolve-action', 'tmux:key r', '--tmux-client', '/dev/pts/7')
        self.assertNotEqual(r.returncode, 0)
        r = json.loads(self.run_bin('resolve-action', 'tmux:menu', '--tmux-client', '/dev/pts/7').stdout)
        self.assertEqual(r['argv'][3:], ['send-keys', '-c', '/dev/pts/7', '-K', 'C-b', 'Space'], 'U1: prefix + menu_key opens the MiOS menu')

    def test_c07_key_projection_is_an_atomic_us_stroke(self):
        r = json.loads(self.run_bin('resolve-action', 'key:CTRL+SHIFT+TAB').stdout)
        keys = [(e['code'], e['value']) for e in r['argv']['events'] if e['kind'] == 1]
        self.assertEqual(keys, [(29, 1), (42, 1), (15, 1), (15, 0), (42, 0), (29, 0)])
        self.assertEqual(r['argv']['keysym'], ['Tab', 'CTRL SHIFT'])

    def test_c07_udev_rule_classifies_only_the_keyboard_before_the_catch_all(self):
        rules = (ROOT / 'usr/lib/udev/rules.d/70-mios-input.rules').read_text().splitlines()
        kbd = next(i for i, l in enumerate(rules) if 'mios-vctl-keyboard' in l and not l.startswith('#'))
        catch = next(i for i, l in enumerate(rules) if 'ATTRS{name}=="mios-vctl-*"' in l)
        self.assertLess(kbd, catch)
        self.assertIn('ID_INPUT_KEYBOARD}="1"', rules[kbd])
        self.assertIn('GOTO="mios_input_end"', rules[kbd])
        self.assertNotIn('LIBINPUT_IGNORE_DEVICE', rules[kbd])
        self.assertIn('LIBINPUT_IGNORE_DEVICE}="1"', rules[catch], 'the virtual pads stay hidden from libinput')
        if shutil.which('udevadm'):
            r = subprocess.run(['udevadm', 'verify', '--no-style', str(ROOT / 'usr/lib/udev/rules.d/70-mios-input.rules')], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    @unittest.skipUnless(UINPUT, 'set MIOS_INPUTD_UINPUT_TEST=1 for the private uinput keyboard')
    def test_c07_uinput_keyboard_writes_the_strokes(self):
        r = self.run_bin('fixture-keys', 'key:ENTER', 'key:SHIFT+ENTER', 'key:CTRL+t')
        self.assertEqual(r.returncode, 0, r.stderr)
        v = json.loads(r.stdout)
        self.assertTrue(v['rejected_by_discovery'], 'the bridge never grabs its own virtual keyboard')
        self.assertEqual([[tuple(k) for k in s] for s in v['strokes']],
                         [[(28, 1), (28, 0)], [(42, 1), (28, 1), (28, 0), (42, 0)], [(29, 1), (20, 1), (20, 0), (29, 0)]])
        # Negative: under the catch-all a non-keyboard mios-vctl-* name is hidden from libinput (why C-07 adds a line).
        self.assertTrue(v['keyboard'].startswith('mios-vctl-') and v['keyboard'] != 'mios-vctl-keyboard')


class FakeCompositor:
    """A Hyprland IPC socket in a private XDG_RUNTIME_DIR: answers the bridge's context and ownership Lua, records
    key/focus projections. It never forwards anything anywhere."""

    def __init__(self, runtime, sig, pid):
        self.dir = Path(runtime) / 'hypr' / sig
        self.dir.mkdir(parents=True)
        self.state = dict(cls='org.test.App', address='0xabc', pid=pid, captured=False, refuse=False)
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
            if 'MIOS_INPUT_CONTEXT' in req:
                s = self.state
                reply = 'MIOS_INPUT_CONTEXT:' + json.dumps(dict(**{'class': s['cls']}, address=s['address'], pid=s['pid'], room=False,
                                                                 captured=s['captured'], released=False, fullscreen=0, handler=''))
            elif 'MIOS_INPUT_WINDOW_OK' in req:
                mode = re.search(r'local mode="(\w+)"', req).group(1)
                self.calls.append(('window', mode))
                if self.state['refuse']:
                    reply = 'MIOS_INPUT_WINDOW_FAIL: refused by the test'
                else:
                    self.state['captured'] = mode != 'off'
                    reply = f"MIOS_INPUT_WINDOW_OK:{self.state['address']} {self.state['cls']}"
            elif 'send_key_state' in req:
                self.calls.append(('keys', re.findall(r"key=\"([^\"]*)\",state='(\w+)'", req)))
            elif 'hl.dsp.focus' in req:
                self.calls.append(('focus', re.search(r'direction="(\w+)"', req).group(1)))
            try:
                c.sendall(reply.encode())
            finally:
                c.close()

    def close(self):
        self.alive = False
        self.sock.close()


@unittest.skipUnless(UINPUT, 'set MIOS_INPUTD_UINPUT_TEST=1 for the fixture pad + fake compositor + private tmux tests')
class Bridge(Base):
    """The candidate bridge on the fixture tier (--live-context) against a fake compositor and a private tmux server."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='mios-inputd-i2-bridge-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.sock_name = f'mios-inputd-i2-{os.getpid()}'
        text = (ROOT / 'usr/share/mios/mios.toml').read_text()
        text = text.replace('tmux_sockets = ["mios-human", "mios-nested"]', f'tmux_sockets = ["{self.sock_name}"]')
        self.osd_log = self.tmp / 'osd.log'
        text = re.sub(r'capture_osd_argv = \[.*\]', 'capture_osd_argv = ["/usr/bin/logger", "--no-act", "-s", "{message}"]', text)
        (self.root / 'usr/share/mios/mios.toml').write_text(text)
        self.addCleanup(shutil.copy, ROOT / 'usr/share/mios/mios.toml', self.root / 'usr/share/mios/mios.toml')
        self.procs = []
        self.addCleanup(self.stop_all)

    def stop_all(self):
        for p in self.procs:
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait()
        subprocess.run(['tmux', '-L', self.sock_name, 'kill-server'], capture_output=True)
        tmpdir = Path(os.environ.get('TMUX_TMPDIR') or '/tmp') / f'tmux-{os.getuid()}' / self.sock_name
        tmpdir.unlink(missing_ok=True)

    def start(self, binary=None, focus_pid=None, extra_env=None):
        runtime = self.tmp / 'run'
        runtime.mkdir(mode=0o700, exist_ok=True)
        sig = f'fake_{len(self.procs)}'
        fake = FakeCompositor(runtime, sig, focus_pid or os.getpid())
        self.addCleanup(fake.close)
        self.pad = subprocess.Popen([str(BIN), 'fixture-pad', '--layout', 'dualsense'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.procs.append(self.pad)
        info = json.loads(self.pad.stdout.readline())
        env = dict(self.env, XDG_RUNTIME_DIR=str(runtime), HYPRLAND_INSTANCE_SIGNATURE=sig, **(extra_env or {}))
        br = subprocess.Popen([str(binary or BIN), '--root', str(self.root), 'bridge', '--fixture-only', '--device', info['path'],
                               '--live-context', '--pad-path', str(self.tmp / 'pad.bin')], env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True)
        self.procs.append(br)
        log = []
        for stream in (br.stdout, br.stderr):
            threading.Thread(target=lambda s=stream: [log.append(l.rstrip()) for l in s], daemon=True).start()
        end = time.monotonic() + 10
        while time.monotonic() < end and not any('"mode":"bridge"' in l for l in log):
            time.sleep(0.05)
        self.assertTrue(any('"mode":"bridge"' in l for l in log), log[-5:])
        time.sleep(0.3)
        return fake, log

    def send(self, *events, hold=0.05):
        self.pad.stdin.write(json.dumps({'events': [list(e) for e in events]}) + '\n')
        self.pad.stdin.flush()
        self.pad.stdout.readline()
        time.sleep(hold)

    def press(self, code, ms=50):
        self.send((1, code, 1), hold=ms / 1000)
        self.send((1, code, 0), hold=0.3)

    def lines(self, log, key):
        out = []
        for l in log:
            try:
                v = json.loads(l)
            except json.JSONDecodeError:
                continue
            if key in v:
                out.append(v)
        return out

    def test_c32_capture_osd_once_per_confirmed_capture_and_release(self):
        fake, log = self.start()
        self.press(SOUTH)
        self.assertIn(('keys', [('Return', 'down'), ('Return', 'up')]), fake.calls, 'key:ENTER projected into the followed compositor')
        self.press(NORTH)
        time.sleep(0.4)
        self.send((1, L3, 1), (1, R3, 1), hold=0.8)
        self.send((1, L3, 0), (1, R3, 0), hold=0.5)
        osd = [v['osd'] for v in self.lines(log, 'osd') if v['ok']]
        self.assertEqual(osd, ['Controller -> org.test.App. Hold L3+R3 to release.', 'Controller -> MiOS. org.test.App released.'], log[-8:])
        # Negative: a focus change without a capture change, and a refused capture, notify nothing.
        fake.state.update(cls='org.other.App', address='0xdef')
        time.sleep(0.4)
        fake.state['refuse'] = True
        self.press(NORTH)
        time.sleep(0.5)
        self.assertEqual(len([v for v in self.lines(log, 'osd')]), 2, log[-6:])
        self.assertTrue(any(v.get('ownership') == 'on' and v.get('ok') is False for v in self.lines(log, 'ownership')))

    def test_c32_negative_old_binary_never_notifies(self):
        self.need_old()
        fake, log = self.start(binary=OLD)
        self.press(NORTH)
        time.sleep(0.5)
        self.assertIn(('window', 'on'), fake.calls)
        self.assertEqual(self.lines(log, 'osd'), [])

    def tmux(self, *a):
        return subprocess.run(['tmux', '-L', self.sock_name, *a], capture_output=True, text=True, timeout=5)

    def test_c09_c30_private_tmux_client_is_found_and_driven(self):
        conf = self.tmp / 'tmux.conf'
        conf.write_text('set -g prefix C-b\n' + ''.join(f'bind {k} select-pane -{d}\n' for k, d in zip('hjkl', 'LDUR')))
        self.assertEqual(self.tmux('-f', str(conf), 'new-session', '-d', '-s', 't', '-x', '100', '-y', '30').returncode, 0)
        self.assertEqual(self.tmux('new-window', '-t', 't:').returncode, 0)
        master, slave = pty.openpty()
        import fcntl
        import struct
        import termios
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 30, 100, 0, 0))
        term = subprocess.Popen(['/bin/sh', '-c', f'tmux -L {self.sock_name} attach -t t; :'], stdin=slave, stdout=slave, stderr=slave, start_new_session=True)
        self.procs.append(term)
        os.close(slave)
        def drain():
            try:
                while os.read(master, 65536):
                    pass
            except OSError:
                pass  # the client exited (EIO on the master)
        threading.Thread(target=drain, daemon=True).start()
        end = time.monotonic() + 5
        while time.monotonic() < end and not self.tmux('list-clients').stdout.strip():
            time.sleep(0.05)
        # The bridge's own environment names a pane of this server (%0, the left pane once split): the verbs must
        # still resolve against the target client, never the caller's TMUX_PANE (a live-run regression).
        fake, log = self.start(focus_pid=term.pid, extra_env=dict(TMUX='/nonexistent,1,0', TMUX_PANE='%0'))
        fake.state['cls'] = 'Alacritty'
        time.sleep(1.2)
        win = lambda: self.tmux('display', '-p', '-t', 't', '#{window_index}').stdout.strip()
        before = win()
        self.press(LT)
        time.sleep(0.4)
        self.assertNotEqual(win(), before, f'LT = tmux previous window ({log[-4:]})')
        self.assertTrue(any(v.get('tmux') == 'key p' and v['ok'] for v in self.lines(log, 'tmux')))
        # One pane: a left flick is at the edge, so the compositor moves focus and tmux does nothing.
        self.send((3, 3, -30000), hold=0.3)
        self.send((3, 3, 0), hold=0.4)
        self.assertIn(('focus', 'left'), fake.calls)
        # Two panes, the right one active: the flick selects the left pane and never reaches the compositor.
        self.tmux('split-window', '-h', '-t', 't')
        pane = lambda: self.tmux('display', '-p', '-t', 't', '#{pane_index}').stdout.strip()
        right = pane()
        focus_before = fake.calls.count(('focus', 'left'))
        self.send((3, 3, -30000), hold=0.3)
        self.send((3, 3, 0), hold=0.5)
        self.assertNotEqual(pane(), right, log[-4:])
        self.assertEqual(fake.calls.count(('focus', 'left')), focus_before, 'inside the window tmux owns the flick')
        # Negative: the same pad press with the terminal not running tmux (a plain shell) never runs tmux.
        fake.state['pid'] = self.pad.pid  # a process with no tmux below it
        time.sleep(1.3)
        n = len(self.lines(log, 'tmux'))
        self.press(LT)
        self.assertEqual(len(self.lines(log, 'tmux')), n)
        self.assertIn(('keys', [('Prior', 'down'), ('Prior', 'up')]), fake.calls, 'terminal_plain: Shift+PageUp')


if __name__ == '__main__':
    unittest.main()
