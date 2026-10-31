#!/usr/bin/env python3
# AI-hint: Real-binary two-sided tests for mios-inputd step I3 (the Linux console): console_tmux/console_plain profiles, O-6 West hold, C-23 console row validation, R-08 Guide, map --text, tmux:keymap; the C-18 console bridge (pty + private tmux server + a fixture gpm socket or evdev FIFO) turning pointer events into SGR 1006 reports only while tmux has mouse mode on; and (private uinput pad) the fixture bridge on a private logind state: console bytes into a pty that stands in for the VT, tmux verbs against the VT client, the graphical-VT switch, INV-8 release, and C-16 compositor rediscovery.
# AI-related: tools/native/mios-inputd/src/console.rs, tools/native/mios-inputd/src/runtime.rs, tools/native/mios-inputd/src/nav.rs, docs/design/mios-global-bindings-tests.md (I3), usr/libexec/mios/mios-terminal
# AI-functions: Base, Grammar, ConsoleBridge, GpmFixture, FixtureBridge, FakeCompositor
# Environment: MIOS_INPUTD_BIN = the binary under test (default: installed). MIOS_INPUTD_OLD_BIN + MIOS_INPUTD_OLD_SSOT =
# the pre-I3 binary and SSOT for the negative controls (skipped when absent). MIOS_INPUTD_UINPUT_TEST=1 enables the
# private uinput tests (a fixture pad). No VT, no physical device, no real gpm daemon, no real compositor and no
# operator tmux server is ever touched: tmux runs with a private TMUX_TMPDIR, gpm is a private socket, the "VT" is a pty.
import fcntl
import json
import os
from pathlib import Path
import pty
import re
import shutil
import signal
import socket
import struct
import subprocess
import tempfile
import termios
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
BIN = Path(os.environ.get('MIOS_INPUTD_BIN', '/usr/libexec/mios/mios-inputd'))
OLD = Path(os.environ.get('MIOS_INPUTD_OLD_BIN', '/nonexistent'))
OLD_SSOT = Path(os.environ.get('MIOS_INPUTD_OLD_SSOT', '/nonexistent'))
UINPUT = os.environ.get('MIOS_INPUTD_UINPUT_TEST') == '1'
SOUTH, EAST, WEST, NORTH, LB, RB, LT, RT = 0x130, 0x131, 0x134, 0x133, 0x136, 0x137, 0x138, 0x139
SELECT, START, GUIDE, L3, R3 = 0x13a, 0x13b, 0x13c, 0x13d, 0x13e
STICK = {c: dict(min=-32768, max=32767, value=0) for c in ('0', '1', '3', '4')}
AXES = dict(STICK, **{'2': dict(min=0, max=255, value=0), '5': dict(min=0, max=255, value=0),
                      '16': dict(min=-1, max=1, value=0), '17': dict(min=-1, max=1, value=0)})
EVIOCGRAB = 0x40044590
GPM_MOVE, GPM_DRAG, GPM_DOWN, GPM_UP, GPM_SINGLE = 1, 2, 4, 8, 16
GPM_B_LEFT = 4


def key(code, value=1):
    return dict(kind=1, code=code, value=value)


def ab(code, value):
    return [dict(kind=3, code=code, value=value), dict(kind=0, code=0, value=0)]


def wait(ms):
    return [dict(tick_ms=ms)]


def tap(code, ms=50):
    return [key(code)] + wait(ms) + [key(code, 0)]


def until(pred, seconds=5.0, step=0.05):
    end = time.monotonic() + seconds
    while True:
        v = pred()
        if v or time.monotonic() > end:
            return v
        time.sleep(step)


def gpm_event(buttons, x, y, kind, wdy=0, mods=0):
    """One Gpm_Event (gpm.h): buttons, modifiers, vc, dx, dy, x, y, type, clicks, margin, wdx, wdy (28 bytes)."""
    return struct.pack('=BBHhhhhiiihh', buttons, mods, 0, 0, 0, x, y, kind, 1, 0, 0, wdy)


def input_event(kind, code, value):
    return struct.pack('=qqHHi', 0, 0, kind, code, value)


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not BIN.is_file():
            raise AssertionError(f'binary under test missing: {BIN}')
        cls.temp = tempfile.TemporaryDirectory(prefix='mios-inputd-i3-')
        cls.root = Path(cls.temp.name) / 'root'
        (cls.root / 'usr/share/mios').mkdir(parents=True)
        shutil.copy(ROOT / 'usr/share/mios/mios.toml', cls.root / 'usr/share/mios/mios.toml')
        cls.env = {k: v for k, v in os.environ.items() if not (k.startswith('MIOS_') and 'TOML' in k)}
        for k in ('HYPRLAND_INSTANCE_SIGNATURE', 'TMUX', 'TMUX_PANE', 'WAYLAND_DISPLAY', 'DISPLAY'):
            cls.env.pop(k, None)
        cls.env['HOME'] = str(Path(cls.temp.name) / 'home')
        cls.env['XDG_CONFIG_HOME'] = str(Path(cls.temp.name) / 'home/.config')
        cls.env['TERM'] = 'linux'
        cls.old_root = None
        if OLD.is_file() and OLD_SSOT.is_file():
            cls.old_root = Path(cls.temp.name) / 'old-root'
            (cls.old_root / 'usr/share/mios').mkdir(parents=True)
            shutil.copy(OLD_SSOT, cls.old_root / 'usr/share/mios/mios.toml')

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def run_bin(self, *args, binary=None, root=None, data=None, timeout=20, env=None):
        return subprocess.run([str(binary or BIN), '--root', str(root or self.root), *args], input=data, text=True,
                              capture_output=True, env=env or self.env, timeout=timeout)

    def replay(self, context, script, binary=None, root=None, **extra):
        payload = dict(context=context, axes=AXES, script=script, **extra)
        return self.run_bin('replay', binary=binary, root=root, data=json.dumps(payload))

    def actions(self, context, script, **extra):
        r = self.replay(context, script, **extra)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)['output']['actions']

    def need_old(self):
        if self.old_root is None:
            self.skipTest('negative control needs MIOS_INPUTD_OLD_BIN and MIOS_INPUTD_OLD_SSOT (the pre-I3 pair)')

    def host_layer(self, text):
        path = self.root / 'etc/mios/mios.toml'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        self.addCleanup(path.unlink, missing_ok=True)


class Grammar(Base):
    def test_c16_console_tmux_profile(self):
        vt = dict(tmux_client='/dev/pts/9')
        self.assertEqual(self.actions('console', tap(SOUTH, 700), **vt), ['key:ENTER'], 'R-15: no Shift+Enter hold on a VT')
        self.assertEqual(self.actions('console', tap(WEST, 50), **vt), ['key:TAB'])
        self.assertEqual(self.actions('console', tap(WEST, 700), **vt), ['key:SHIFT+TAB'], 'O-6: West hold = BTab, as on the desktop')
        self.assertEqual(self.actions('console', tap(NORTH), **vt), ['tmux:key z'], 'no window to capture: North zooms the pane')
        self.assertEqual(self.actions('console', tap(LB), **vt), ['tmux:session-prev'])
        self.assertEqual(self.actions('console', tap(RT), **vt), ['tmux:key n'])
        self.assertEqual(self.actions('console', tap(GUIDE), **vt), ['tmux:menu'])
        self.assertEqual(self.actions('console', tap(SELECT, 700), **vt), ['tmux:keymap'])
        self.assertEqual(self.actions('console', ab(1, -30000) + wait(800), **vt), ['tmux:page-up'] * 3)
        self.assertEqual(self.actions('console', [key(L3), key(R3)] + wait(800) + [key(L3, 0), key(R3, 0)], **vt),
                         ['mios:graphical-vt'], 'C-17: the toggle chord switches VT; its hold adds nothing')

    def test_c18_console_plain_profile_projects_only_vt_keys(self):
        self.assertEqual(self.actions('console', tap(SOUTH)), ['key:ENTER'])
        self.assertEqual(self.actions('console', tap(EAST, 700)), ['key:CTRL+c'])
        self.assertEqual(self.actions('console', tap(WEST, 700)), ['key:SHIFT+TAB'])
        self.assertEqual(self.actions('console', tap(LB)), ['key:SHIFT+TAB'], 'bumpers = BTab / Tab')
        self.assertEqual(self.actions('console', tap(RB)), ['key:TAB'])
        self.assertEqual(self.actions('console', tap(LT)), ['key:PAGEUP'], 'triggers = PageUp / PageDown')
        self.assertEqual(self.actions('console', tap(RT)), ['key:PAGEDOWN'])
        self.assertEqual(self.actions('console', ab(1, -30000) + wait(800)), ['key:UP'] * 3, 'the left stick is arrows, never PageUp')
        self.assertEqual(self.actions('console', ab(0, 30000) + wait(100)), ['key:RIGHT'])
        self.assertEqual(self.actions('console', tap(START)), ['input:cursor on'], 'Start = cursor mode, as on the desktop')
        for b in (NORTH, GUIDE, L3, SELECT):
            with self.subTest(button=hex(b)):
                self.assertEqual(self.actions('console', tap(b)), [], 'none on a bare VT')
        allowed = {'key:ENTER', 'key:ESC', 'key:CTRL+c', 'key:TAB', 'key:SHIFT+TAB', 'key:UP', 'key:DOWN', 'key:LEFT',
                   'key:RIGHT', 'key:PAGEUP', 'key:PAGEDOWN', 'pointer:cursor', 'mios:graphical-vt', 'none'}
        nav = json.loads(self.run_bin('map').stdout)['global']['controller']['nav']['console_plain']
        for inp, v in nav.items():
            for a in (v if isinstance(v, list) else [v]):
                self.assertIn(a, allowed, inp)

    def test_r08_guide_is_no_modifier_on_a_vt(self):
        vt = dict(tmux_client='/dev/pts/9')
        self.assertEqual(self.actions('console', [key(GUIDE)] + tap(LB) + [key(GUIDE, 0)], **vt), ['tmux:session-prev'])
        # Negative: where a compositor runs, Guide+LB is the system layer.
        self.assertEqual(self.actions('terminal', [key(GUIDE)] + tap(LB) + [key(GUIDE, 0)], **vt), ['hypr:workspace e-1'])
        # Negative: on the desktop the toggle chord is still the room toggle.
        self.assertEqual(self.actions('desktop-2d', [key(L3), key(R3)] + wait(50) + [key(L3, 0), key(R3, 0)]), ['mios:hyprland-3d-toggle'])

    def test_c23_console_rows_must_reach_a_vt(self):
        self.assertEqual(self.run_bin('check').returncode, 0, self.run_bin('check').stderr)
        for table, body, why in [('console_plain', 'BTN_SOUTH = "hypr:movefocus left"', 'cannot reach a VT'),
                                 ('console_tmux', 'BTN_MODE = "quickshell:drawer-toggle"', 'cannot reach a VT'),
                                 ('console_plain', 'RS_UP = "pointer:cursor"', 'only a button'),
                                 ('console_plain', 'TOGGLE_CHORD = ["none", "mios:graphical-vt"]', 'toggle chord takes one action'),
                                 ('desktop', 'TOGGLE_CHORD = "mios:graphical-vt"', 'capture contract'),
                                 ('contexts', 'console = "vpad"', 'no virtual-pad consumer')]:
            with self.subTest(body=body):
                self.host_layer(f'[keybindings.global.controller.nav.{table}]\n{body}\n')
                r = self.run_bin('check')
                self.assertNotEqual(r.returncode, 0, body)
                self.assertIn(why, r.stderr)
        self.host_layer('[keybindings.global.controller.nav.console_plain]\nBTN_NORTH = "key:ENTER"\n')
        self.assertEqual(self.run_bin('check').returncode, 0, 'a VT may claim the contract inputs')
        self.host_layer('[keybindings.global.controller.console]\nmouse = "always"\n')
        self.assertIn('auto, gpm, evdev or off', self.run_bin('check').stderr)

    def test_r21_map_text_and_keymap_popup(self):
        r = self.run_bin('map', '--text')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('console          console_tmux (tmux client) / console_plain', r.stdout)
        self.assertIn('BTN_WEST        tap key:TAB  hold key:SHIFT+TAB', r.stdout)
        r = self.run_bin('resolve-action', 'tmux:keymap', '--tmux-client', '/dev/pts/7', '--tmux-socket', '/tmp/t.sock')
        self.assertEqual(r.returncode, 0, r.stderr)
        argv = json.loads(r.stdout)['argv']
        self.assertEqual(argv[:6], ['/usr/bin/tmux', '-S', '/tmp/t.sock', 'display-popup', '-c', '/dev/pts/7'])
        self.assertEqual(argv[-3:-1], ['/usr/bin/less', '-R'], 'argv form: tmux runs no shell')
        r = self.run_bin('resolve-action', 'mios:graphical-vt')
        self.assertIn('CTRL+ALT+F', r.stdout)

    def test_negative_old_binary_has_no_console(self):
        self.need_old()
        r = self.run_bin('check', binary=OLD)
        self.assertNotEqual(r.returncode, 0, 'the pre-I3 binary rejects the I3 SSOT (console context)')
        r = self.replay('console', tap(SOUTH), binary=OLD, root=self.old_root)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('invalid replay context', r.stderr)
        self.assertNotEqual(self.run_bin('map', '--text', binary=OLD, root=self.old_root).returncode, 0)
        r = self.run_bin('resolve-action', 'tmux:keymap', '--tmux-client', '/dev/pts/7', binary=OLD, root=self.old_root)
        self.assertNotEqual(r.returncode, 0, 'the keymap popup had no projection before I3')
        r = self.run_bin('console-bridge', '--', '/bin/true', binary=OLD, root=self.old_root)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_mios_terminal_wraps_only_a_local_vt_client(self):
        text = (ROOT / 'usr/libexec/mios/mios-terminal').read_text()
        hook = text[text.index('# A local VT client runs under the native console bridge'):]
        self.assertIn('=~ ^/dev/tty[0-9]+$', hook, 'only a VT (never a pts: SSH, a desktop terminal)')
        self.assertIn('MIOS_CONSOLE_BRIDGE', hook, 'never nested inside its own bridge')
        self.assertIn('keybindings.global.controller.console bridge', hook)
        self.assertIn('exec /usr/libexec/mios/mios-inputd console-bridge -- tmux', hook)
        self.assertNotRegex(hook, r'python|perl|ruby|node', 'no interpreter at login')


class GpmFixture:
    """A private gpm client socket: records each Gpm_Connect, then the test pushes Gpm_Event records."""

    def __init__(self, path):
        self.path = path
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(str(path))
        self.sock.listen(4)
        self.conns = []
        self.closed = []
        self.alive = True
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        while self.alive:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            c.settimeout(2)
            try:
                req = c.recv(16)
            except OSError:
                req = b''
            if len(req) == 16:
                em, dm, mn, mx, pid, vc = struct.unpack('=HHHHii', req)
                self.conns.append(dict(conn=c, event_mask=em, default_mask=dm, pid=pid, vc=vc))
                threading.Thread(target=self.watch, args=(c,), daemon=True).start()

    def watch(self, c):
        c.settimeout(None)
        try:
            while c.recv(64):
                pass
        except OSError:
            pass
        self.closed.append(c)

    def send(self, ev):
        self.conns[-1]['conn'].sendall(ev)

    def close(self):
        self.alive = False
        self.sock.close()
        for c in self.conns:
            c['conn'].close()


class ConsoleBridge(Base):
    """C-18: a pty stands in for the VT; the bridge wraps a private tmux client; gpm is a private socket."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='mios-inputd-i3-bridge-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.tmux_tmp = self.tmp / 'tmux'
        self.tmux_tmp.mkdir(mode=0o700)
        self.sock = f'mios-inputd-i3-{os.getpid()}'
        self.tenv = dict(self.env, TMUX_TMPDIR=str(self.tmux_tmp))
        self.procs = []
        self.addCleanup(self.stop_all)
        conf = self.tmp / 'tmux.conf'
        conf.write_text('set -g prefix C-b\nset -g mouse off\nset -g status on\n')
        self.assertEqual(self.tmux('-f', str(conf), 'new-session', '-d', '-s', 't', '-x', '100', '-y', '29').returncode, 0)
        self.assertEqual(self.tmux('split-window', '-h', '-t', 't').returncode, 0)

    def tmux(self, *a):
        return subprocess.run(['tmux', '-L', self.sock, *a], capture_output=True, text=True, timeout=5, env=self.tenv)

    def stop_all(self):
        for p in self.procs:
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait()
        self.tmux('kill-server')

    def vt(self, argv, client=True):
        """Run argv on a fresh pty (the "VT", 100x30); return (process, received-bytes buffer)."""
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 30, 100, 0, 0))
        p = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave, env=self.tenv, start_new_session=True)
        self.procs.append(p)
        os.close(slave)
        out = bytearray()

        def drain():
            try:
                while True:
                    b = os.read(master, 65536)
                    if not b:
                        return
                    out.extend(b)
            except OSError:
                return
        threading.Thread(target=drain, daemon=True).start()
        if client:
            self.assertTrue(until(lambda: self.tmux('list-clients').stdout.strip(), 8), 'the client attached')
        return p, out

    def bridge(self, *opts, binary=None):
        self.log = self.tmp / 'bridge.log'
        argv = [str(binary or BIN), '--root', str(self.root), 'console-bridge', '--vt', '7', '--log', str(self.log), *opts,
                '--', 'tmux', '-L', self.sock, 'attach', '-t', 't']
        return self.vt(argv)

    def records(self):
        try:
            return [json.loads(l) for l in self.log.read_text().splitlines() if l.strip()]
        except OSError:
            return []

    def pane(self):
        return self.tmux('display', '-p', '-t', 't', '#{pane_index}').stdout.strip()

    def test_c18_gpm_clicks_reach_tmux_only_while_mouse_mode_is_on(self):
        gpm = GpmFixture(self.tmp / 'gpmctl')
        self.addCleanup(gpm.close)
        p, vt = self.bridge('--mouse', 'gpm', '--gpm-socket', str(gpm.path))
        self.assertEqual(self.pane(), '1', 'the split left the right pane active')
        time.sleep(2.5)
        self.assertEqual(gpm.conns, [], 'mouse mode off: the bridge holds no gpm connection (gpm selection stays usable)')
        self.tmux('set', '-g', 'mouse', 'on')
        self.assertTrue(until(lambda: gpm.conns, 6), f'mouse mode on: the bridge connects to gpm ({self.records()[-3:]})')
        c = gpm.conns[-1]
        self.assertEqual((c['vc'], c['event_mask']), (7, 0xffff), 'routed by this VT, every event')
        self.assertTrue(c['default_mask'] & GPM_MOVE, 'gpm keeps drawing its pointer')
        self.assertTrue(any(r.get('mouse_mode') not in (None, 'Off') and r.get('sgr') for r in self.records()), self.records())
        gpm.send(gpm_event(GPM_B_LEFT, 10, 5, GPM_DOWN | GPM_SINGLE))
        gpm.send(gpm_event(GPM_B_LEFT, 10, 5, GPM_UP | GPM_SINGLE))
        self.assertTrue(until(lambda: self.pane() == '0', 4), f'a gpm click selects the left pane ({self.records()[-3:]})')
        self.assertTrue(any('\x1b[<0;10;5M' in r.get('reports', '') for r in self.records()), 'SGR 1006 press report')
        gpm.send(gpm_event(0, 70, 8, GPM_MOVE, wdy=1))
        in_mode = lambda: self.tmux('display', '-p', '-t', 't:.1', '#{pane_in_mode}').stdout.strip() == '1'
        self.assertTrue(until(in_mode, 4), 'wheel up over the right pane enters copy mode there')
        received = bytes(vt)
        for mode in (b'\x1b[?1000h', b'\x1b[?1002h', b'\x1b[?1006h', b'\x1b[?1003h'):
            self.assertNotIn(mode, received, f'the VT itself never enters mouse reporting ({mode!r})')
        # Negative: mouse mode off again -> the bridge drops gpm, so nothing reaches tmux.
        self.tmux('set', '-g', 'mouse', 'off')
        self.assertTrue(until(lambda: gpm.closed, 4), 'gpm connection released with mouse mode off')
        self.assertTrue(any(r.get('gpm') == 'disconnected' for r in self.records()))
        self.tmux('send-keys', '-t', 't:.1', '-X', 'cancel')
        self.tmux('select-pane', '-t', 't:.1')
        time.sleep(0.5)
        n = len([r for r in self.records() if 'reports' in r])
        time.sleep(1)
        self.assertEqual(len([r for r in self.records() if 'reports' in r]), n)
        self.assertEqual(self.pane(), '1')
        p.terminate()

    def test_c18_negative_a_direct_client_puts_the_vt_in_mouse_mode(self):
        # Contrast control: without the bridge, tmux with mouse on asks the VT itself for mouse reporting.
        self.tmux('set', '-g', 'mouse', 'on')
        p, vt = self.vt(['tmux', '-L', self.sock, 'attach', '-t', 't'])
        self.assertTrue(until(lambda: b'\x1b[?1000h' in bytes(vt), 4), bytes(vt)[-200:])

    def test_c18_evdev_pointer_source(self):
        fifo = self.tmp / 'pointer.fifo'
        os.mkfifo(fifo)
        hold = os.open(fifo, os.O_RDWR | os.O_NONBLOCK)
        self.addCleanup(os.close, hold)
        self.tmux('set', '-g', 'mouse', 'on')
        p, vt = self.bridge('--mouse', 'evdev', '--pointer-device', str(fifo))
        self.assertTrue(until(lambda: any(r.get('evdev') == 'open' for r in self.records()), 6), self.records())
        # The pointer starts at the centre cell (50, 15); 8x16 px cells: 320 px left = 40 cells.
        os.write(hold, input_event(2, 0, -320) + input_event(0, 0, 0))
        os.write(hold, input_event(1, 0x110, 1) + input_event(0, 0, 0) + input_event(1, 0x110, 0) + input_event(0, 0, 0))
        self.assertTrue(until(lambda: self.pane() == '0', 4), f'an evdev click at (10, 15) selects the left pane ({self.records()[-3:]})')
        self.assertTrue(any('\x1b[<0;10;15M' in r.get('reports', '') for r in self.records()), self.records()[-3:])

    def test_c18_mouse_off_source_never_reports(self):
        gpm = GpmFixture(self.tmp / 'gpmctl')
        self.addCleanup(gpm.close)
        self.tmux('set', '-g', 'mouse', 'on')
        self.bridge('--mouse', 'off', '--gpm-socket', str(gpm.path))
        time.sleep(3)
        self.assertEqual(gpm.conns, [], '--mouse off holds no pointer source')

    def test_c18_window_size_and_exit_status_pass_through(self):
        self.log = self.tmp / 'bridge.log'
        size = self.tmp / 'size'
        p, vt = self.vt([str(BIN), '--root', str(self.root), 'console-bridge', '--mouse', 'off', '--log', str(self.log), '--',
                         'sh', '-c', f'stty size > {size}; tty >> {size}; exit 7'], client=False)
        self.assertEqual(p.wait(timeout=10), 7, "the bridge exits with the client's status")
        lines = size.read_text().split('\n')
        self.assertEqual(lines[0], '30 100', 'the client pty has the VT size')
        self.assertTrue(lines[1].startswith('/dev/pts/'), 'the client runs on its own pty')
        self.assertTrue(any(r.get('console_bridge') == 'exited' and r.get('status') == 7 for r in self.records()))

    def test_c18_negative_old_binary_has_no_bridge(self):
        if not OLD.is_file():
            self.skipTest('needs MIOS_INPUTD_OLD_BIN')
        r = subprocess.run([str(OLD), 'console-bridge', '--', 'tmux'], capture_output=True, text=True, env=self.tenv)
        self.assertEqual(r.returncode, 2, r.stderr)


class FakeCompositor:
    """A Hyprland IPC socket (+ hyprland.lock naming a live stand-in pid) in a private XDG_RUNTIME_DIR: answers the
    context query and records key projections."""

    def __init__(self, runtime, sig, focus_pid):
        self.dir = Path(runtime) / 'hypr' / sig
        self.dir.mkdir(parents=True)
        self.stand_in = subprocess.Popen(['sleep', '600'])
        (self.dir / 'hyprland.lock').write_text(f'{self.stand_in.pid}\nwayland-1\n')
        self.state = dict(cls='org.test.App', address='0xabc', pid=focus_pid)
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
                                                                 captured=False, released=False, fullscreen=0, handler=''))
            elif 'send_key_state' in req:
                self.calls.append(('keys', re.findall(r"key=\"([^\"]*)\",state='(\w+)'", req)))
            try:
                c.sendall(reply.encode())
            except OSError:
                pass
            finally:
                c.close()

    def close(self):
        """The compositor dies: socket gone, its process gone."""
        if self.alive:
            self.alive = False
            self.sock.close()
            self.stand_in.kill()
            self.stand_in.wait()


@unittest.skipUnless(UINPUT, 'set MIOS_INPUTD_UINPUT_TEST=1 for the fixture pad + private logind state + pty VT tests')
class FixtureBridge(Base):
    """The candidate bridge on the fixture tier with a private logind state: the console context, INV-8, C-17, C-16."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='mios-inputd-i3-fixture-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.tmux_tmp = self.tmp / 'tmux'
        self.tmux_tmp.mkdir(mode=0o700)
        self.sock_name = f'mios-inputd-i3f-{os.getpid()}'
        text = (ROOT / 'usr/share/mios/mios.toml').read_text()
        text = text.replace('tmux_sockets = ["mios-human", "mios-nested"]', f'tmux_sockets = ["{self.sock_name}"]')
        (self.root / 'usr/share/mios/mios.toml').write_text(text)
        self.addCleanup(shutil.copy, ROOT / 'usr/share/mios/mios.toml', self.root / 'usr/share/mios/mios.toml')
        self.tenv = dict(self.env, TMUX_TMPDIR=str(self.tmux_tmp))
        self.procs = []
        self.addCleanup(self.stop_all)
        self.uid = os.getuid()
        self.state = self.tmp / 'logind'
        for d in ('users', 'sessions', 'seats'):
            (self.state / d).mkdir(parents=True)
        self.logind(active='c7', graphical=True)

    def logind(self, active, graphical=True):
        """This user's graphical session g2 (VT 2) and tty session c7 (VT 5); the greeter is session gr."""
        sessions = 'c7' + (' g2' if graphical else '')
        (self.state / f'users/{self.uid}').write_text(f'STATE=active\nSESSIONS={sessions}\n' + ('DISPLAY=g2\n' if graphical else ''))
        (self.state / 'sessions/c7').write_text(f'UID={self.uid}\nTYPE=tty\nCLASS=user\nSEAT=seat0\nTTY=tty5\nVTNR=5\n')
        if graphical:
            (self.state / 'sessions/g2').write_text(f'UID={self.uid}\nTYPE=wayland\nCLASS=user\nSEAT=seat0\nTTY=tty2\nVTNR=2\n')
        (self.state / 'sessions/gr').write_text('UID=979\nTYPE=wayland\nCLASS=greeter\nSEAT=seat0\nVTNR=1\n')
        tmp = self.state / 'seats/.seat0'
        tmp.write_text(f'ACTIVE={active}\n')
        tmp.rename(self.state / 'seats/seat0')

    def tmux(self, *a):
        return subprocess.run(['tmux', '-L', self.sock_name, *a], capture_output=True, text=True, timeout=5, env=self.tenv)

    def stop_all(self):
        for p in reversed(self.procs):
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait()
        self.tmux('kill-server')

    def vt(self, argv):
        """The pty standing in for VT 5: the program reads it as fd 0; the bridge writes console bytes to its master."""
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 30, 100, 0, 0))
        tty = os.ttyname(slave)
        p = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave, env=self.tenv, start_new_session=True)
        self.procs.append(p)
        os.close(slave)

        def drain():
            try:
                while os.read(master, 65536):
                    pass
            except OSError:
                pass
        threading.Thread(target=drain, daemon=True).start()
        return master, tty

    def start(self, master, tty, binary=None, extra=(), env=None, root=None):
        self.pad = subprocess.Popen([str(BIN), 'fixture-pad', '--layout', 'dualsense'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.procs.insert(0, self.pad)
        self.pad_info = json.loads(self.pad.stdout.readline())
        argv = [str(binary or BIN), '--root', str(root or self.root), 'bridge', '--fixture-only', '--device', self.pad_info['path'],
                '--pad-path', str(self.tmp / 'pad.bin'), *extra]
        if master is not None:
            argv += ['--logind-state', str(self.state), '--console-tty', tty, '--console-sink-fd', str(master)]
        br = subprocess.Popen(argv, env=env or self.tenv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                              pass_fds=(master,) if master is not None else ())
        self.procs.append(br)
        self.br = br
        self.log = []
        for stream in (br.stdout, br.stderr):
            threading.Thread(target=lambda s=stream: [self.log.append(l.rstrip()) for l in s], daemon=True).start()
        return br

    def records(self, key):
        out = []
        for l in list(self.log):
            try:
                v = json.loads(l)
            except json.JSONDecodeError:
                continue
            if isinstance(v, dict) and key in v:
                out.append(v)
        return out

    def send(self, *events, hold=0.05):
        self.pad.stdin.write(json.dumps({'events': [list(e) for e in events]}) + '\n')
        self.pad.stdin.flush()
        self.pad.stdout.readline()
        time.sleep(hold)

    def press(self, code, ms=60):
        self.send((1, code, 1), hold=ms / 1000)
        self.send((1, code, 0), hold=0.35)

    def grab_free(self):
        """INV-8: whether another reader could grab the pad now (the bridge holds no EVIOCGRAB)."""
        fd = os.open(self.pad_info['path'], os.O_RDONLY | os.O_NONBLOCK)
        try:
            fcntl.ioctl(fd, EVIOCGRAB, 1)
            fcntl.ioctl(fd, EVIOCGRAB, 0)
            return True
        except OSError:
            return False
        finally:
            os.close(fd)

    def test_c16_bare_vt_receives_console_bytes_and_inv8_releases(self):
        out = self.tmp / 'vt.bytes'
        master, tty = self.vt(['sh', '-c', f'stty raw -echo; exec cat > {out}'])
        self.start(master, tty)
        self.assertTrue(until(lambda: self.records('seat') and self.records('mode'), 10), self.log[-5:])
        self.assertTrue(until(lambda: any(r['seat'] == 'console' for r in self.records('seat')), 5), self.log[-5:])
        time.sleep(0.5)
        self.assertFalse(self.grab_free(), 'this user is active on the VT: the bridge owns the pad')
        mark = lambda: out.stat().st_size
        got = lambda m: (time.sleep(0.3), out.read_bytes()[m:])[1]
        m = mark(); self.press(SOUTH)
        self.assertEqual(got(m), b'\r', 'South = Enter on the VT')
        m = mark(); self.press(WEST, 700)
        self.assertEqual(got(m), b'\x1b\t', 'O-6: West hold = Shift+Tab = Meta_Tab = ESC TAB (linux kcbt)')
        m = mark(); self.press(EAST, 700)
        self.assertEqual(got(m), b'\x03')
        m = mark(); self.press(RT)
        self.assertEqual(got(m), b'\x1b[6~', 'RT = PageDown')
        m = mark(); self.send((3, 1, -30000), hold=0.2); self.send((3, 1, 0), hold=0.2)
        self.assertEqual(got(m), b'\x1b[A', 'the left stick is an arrow on a bare VT')
        m = mark(); self.press(NORTH)
        self.assertEqual(got(m), b'', 'North is none on a bare VT (nothing to capture)')
        n = len(self.records('vt_switch'))
        m = mark(); self.send((1, L3, 1), (1, R3, 1), hold=0.1); self.send((1, L3, 0), (1, R3, 0), hold=0.4)
        sw = self.records('vt_switch')[n:]
        self.assertEqual([(s['vt_switch'], s['graphical_vt'], s['submitted']) for s in sw], [('CTRL+ALT+F2', 2, True)], self.log[-4:])
        self.assertEqual(got(m), b'', 'a VT switch delivers no bytes')
        self.assertFalse(any(r.get('action') == 'mios:hyprland-3d-toggle' for r in self.records('action')), 'no room on a VT')
        # INV-8: the greeter takes the seat -> the grab is released and nothing is projected.
        self.logind(active='gr')
        self.assertTrue(until(lambda: self.records('seat') and self.records('seat')[-1].get('seat') == 'released', 5), self.log[-4:])
        self.assertTrue(self.grab_free(), 'another session can own the pad')
        # The other session owns it now: the test holds that exclusive grab while the pad moves (a fixture pad never
        # emits ungrabbed: a host app such as Steam reads every uinput pad), and released, the bridge projects nothing.
        other = os.open(self.pad_info['path'], os.O_RDONLY | os.O_NONBLOCK)
        try:
            fcntl.ioctl(other, EVIOCGRAB, 1)
            m = mark(); self.press(SOUTH)
            self.assertEqual(got(m), b'', 'nothing is synthesized for another session')
        finally:
            os.close(other)
        self.logind(active='c7')
        self.assertTrue(until(lambda: self.records('seat')[-1].get('seat') == 'owned', 5), self.log[-4:])
        m = mark(); self.press(SOUTH)
        self.assertEqual(got(m), b'\r', 'back on this user\'s VT: owned again')

    def test_c17_negative_no_graphical_session_sends_nothing(self):
        self.logind(active='c7', graphical=False)
        out = self.tmp / 'vt.bytes'
        master, tty = self.vt(['sh', '-c', f'stty raw -echo; exec cat > {out}'])
        self.start(master, tty)
        self.assertTrue(until(lambda: any(r['seat'] == 'console' for r in self.records('seat')), 10), self.log[-5:])
        time.sleep(0.4)
        self.send((1, L3, 1), (1, R3, 1), hold=0.1); self.send((1, L3, 0), (1, R3, 0), hold=0.4)
        self.assertEqual([(s['vt_switch'], s['submitted']) for s in self.records('vt_switch')], [(None, False)])
        self.assertEqual(out.read_bytes(), b'')

    def test_c16_vt_tmux_client_behind_the_console_bridge(self):
        conf = self.tmp / 'tmux.conf'
        conf.write_text('set -g prefix C-b\nset -g mouse off\n')
        for s in ('s1', 's2'):
            self.assertEqual(self.tmux('-f', str(conf), 'new-session', '-d', '-s', s, '-x', '100', '-y', '29').returncode, 0)
        self.tmux('split-window', '-h', '-t', 's2')
        master, tty = self.vt([str(BIN), '--root', str(self.root), 'console-bridge', '--mouse', 'off', '--',
                               'tmux', '-L', self.sock_name, 'attach', '-t', 's2'])
        self.assertTrue(until(lambda: self.tmux('list-clients').stdout.strip(), 8))
        self.start(master, tty)
        self.assertTrue(until(lambda: any(r['seat'] == 'console' for r in self.records('seat')), 10), self.log[-5:])
        time.sleep(1.5)  # /proc discovery at most once a second
        session = lambda: self.tmux('list-clients', '-F', '#{client_session}').stdout.strip()
        self.assertEqual(session(), 's2')
        self.press(LB)
        self.assertTrue(until(lambda: session() == 's1', 3), f'LB = previous tmux session on the VT ({self.records("tmux")[-2:]})')
        tm = self.records('tmux')
        self.assertTrue(tm and tm[-1]['ok'] and tm[-1]['client'].startswith('/dev/pts/'), tm[-1:])
        self.assertNotEqual(tm[-1]['client'], tty, "the verb ran against the client's own pty below the bridge")
        self.press(RB)
        self.assertTrue(until(lambda: session() == 's2', 3))
        zoomed = lambda: self.tmux('display', '-p', '-t', 's2', '#{window_zoomed_flag}').stdout.strip() == '1'
        self.press(NORTH)
        self.assertTrue(until(zoomed, 3), 'North = zoom pane (tmux:key z) on a VT with tmux')
        # Negative: the right stick stops at the pane edge on a VT (no compositor to fall through to).
        self.tmux('resize-pane', '-Z', '-t', 's2')
        self.tmux('select-pane', '-t', 's2:.0')
        self.send((3, 3, -30000), hold=0.3); self.send((3, 3, 0), hold=0.5)
        self.assertTrue(any('edge' in ' '.join(r.get('ran') or []) for r in self.records('tmux')), self.records('tmux')[-1:])

    def test_c16_compositor_rediscovery(self):
        runtime = self.tmp / 'run'
        runtime.mkdir(mode=0o700)
        a = FakeCompositor(runtime, 'sig_a', os.getpid())
        self.addCleanup(a.close)
        env = dict(self.tenv, XDG_RUNTIME_DIR=str(runtime), HYPRLAND_INSTANCE_SIGNATURE='sig_a')
        self.start(None, None, extra=('--live-context', '--rediscover'), env=env)
        self.assertTrue(until(lambda: self.records('mode'), 10), self.log[-5:])
        time.sleep(0.5)
        self.press(SOUTH)
        self.assertTrue(until(lambda: ('keys', [('Return', 'down'), ('Return', 'up')]) in a.calls, 3), self.log[-4:])
        a.close()
        time.sleep(0.3)
        b = FakeCompositor(runtime, 'sig_b', os.getpid())
        self.addCleanup(b.close)
        self.assertTrue(until(lambda: any(r.get('signature') == 'sig_b' for r in self.records('compositor')), 6), self.log[-5:])
        time.sleep(0.5)
        self.press(SOUTH)
        self.assertTrue(until(lambda: ('keys', [('Return', 'down'), ('Return', 'up')]) in b.calls, 3),
                        'after a compositor restart the new socket is found without restarting the bridge')

    def test_c16_negative_without_rediscovery_the_stale_signature_stays(self):
        runtime = self.tmp / 'run'
        runtime.mkdir(mode=0o700)
        a = FakeCompositor(runtime, 'sig_a', os.getpid())
        self.addCleanup(a.close)
        env = dict(self.tenv, XDG_RUNTIME_DIR=str(runtime), HYPRLAND_INSTANCE_SIGNATURE='sig_a')
        old = self.old_root is not None
        self.start(None, None, binary=OLD if old else None, root=self.old_root if old else None, extra=('--live-context',), env=env)
        self.assertTrue(until(lambda: self.records('mode') or any('"mode":"bridge"' in l for l in self.log), 10), self.log[-5:])
        time.sleep(0.5)
        self.press(SOUTH)
        self.assertTrue(until(lambda: ('keys', [('Return', 'down'), ('Return', 'up')]) in a.calls, 3), 'it follows the first compositor')
        a.close()
        b = FakeCompositor(runtime, 'sig_b', os.getpid())
        self.addCleanup(b.close)
        time.sleep(1.5)
        self.press(SOUTH)
        time.sleep(0.5)
        self.assertEqual(b.calls, [], 'the old behaviour: a restarted compositor is never followed')

    def test_negative_old_binary_cannot_read_the_seat(self):
        if not OLD.is_file():
            self.skipTest('needs MIOS_INPUTD_OLD_BIN')
        out = self.tmp / 'vt.bytes'
        master, tty = self.vt(['sh', '-c', f'stty raw -echo; exec cat > {out}'])
        br = self.start(master, tty, binary=OLD)
        self.assertEqual(br.wait(timeout=10), 2, 'the pre-I3 bridge has no seat view (unknown --logind-state)')


if __name__ == '__main__':
    unittest.main()
