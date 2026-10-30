#!/usr/bin/env python3
# AI-hint: LIVE opt-in test (MIOS_LIVE_NESTED=1) of mios-inputd step I3 in an owned nested MiOS PiP session: one fixture bridge follows the nested compositor AND a private logind state, so the same pad press lands in the nested window while this user's graphical session is active, in a pty standing in for the VT while the tty session is active (console bytes, O-6 West hold, C-17 VT switch instead of the room), nowhere (and ungrabbed) while the greeter owns the seat, and in the nested window again after; then a VT tmux client behind the C-18 console bridge gets tmux verbs and an evdev-pointer click as an SGR report. The pre-I3 binary is the negative control.
# AI-related: tools/native/mios-inputd/src/runtime.rs, tools/native/mios-inputd/src/console.rs, tests/test-inputd-console.py, tests/test-inputd-nav-live.py, docs/design/mios-global-bindings-tests.md (I3)
# AI-functions: check, Nested, Pad, Bridge, Probe, Vt
# Environment: MIOS_LIVE_NESTED=1 (else skip), MIOS_INPUTD_BIN = candidate (default installed), MIOS_INPUTD_OLD_BIN +
# MIOS_INPUTD_OLD_SSOT = the pre-I3 pair for the negative control, MIOS_VENDOR_TOML = the SSOT under test (default: this
# tree's). Nothing is sent to the primary compositor or to a real VT: projections go to the nested compositor named in
# its session.json or into a private pty; the logind state is a private copy; tmux runs with a private TMUX_TMPDIR.
import fcntl
import json
import os
from pathlib import Path
import pty
import re
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import threading
import time

TREE = Path(__file__).resolve().parents[1]
SSOT = Path(os.environ.get('MIOS_VENDOR_TOML', str(TREE / 'usr/share/mios/mios.toml')))
BIN = os.environ.get('MIOS_INPUTD_BIN', '/usr/libexec/mios/mios-inputd')
OLD = os.environ.get('MIOS_INPUTD_OLD_BIN', '')
OLD_SSOT = os.environ.get('MIOS_INPUTD_OLD_SSOT', '')
SOUTH, EAST, WEST, NORTH, LB, RB, L3, R3 = 0x130, 0x131, 0x134, 0x133, 0x136, 0x137, 0x13d, 0x13e
EVIOCGRAB = 0x40044590
res = []


def check(name, ok, detail=''):
    res.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'} {name} {detail}", flush=True)


def sh(argv, env=None, timeout=30, data=None):
    return subprocess.run(argv, capture_output=True, text=True, env=env, timeout=timeout, input=data)


def wait(pred, seconds=5.0, step=0.05):
    end = time.monotonic() + seconds
    while True:
        v = pred()
        if v or time.monotonic() > end:
            return v
        time.sleep(step)


if os.environ.get('MIOS_LIVE_NESTED') != '1' or not os.environ.get('HYPRLAND_INSTANCE_SIGNATURE'):
    print('skip: live nested-session test (set MIOS_LIVE_NESTED=1 inside a MiOS desktop session)')
    sys.exit(0)

tmp = Path(tempfile.mkdtemp(prefix='mios-inputd-i3-live-'))
sock = f'mios-i3-live-{os.getpid()}'
tmux_tmp = tmp / 'tmux'
tmux_tmp.mkdir(mode=0o700)


def private_root(name, ssot):
    r = tmp / name
    (r / 'usr/share/mios').mkdir(parents=True)
    text = Path(ssot).read_text()
    text = text.replace('tmux_sockets = ["mios-human", "mios-nested"]', f'tmux_sockets = ["{sock}"]')
    text = re.sub(r'capture_osd_argv = \[.*\]', 'capture_osd_argv = ["/usr/bin/logger", "--no-act", "-s", "{message}"]', text)
    (r / 'usr/share/mios/mios.toml').write_text(text)
    return r


root = private_root('root', SSOT)
old_root = private_root('old-root', OLD_SSOT) if OLD and OLD_SSOT and Path(OLD).is_file() and Path(OLD_SSOT).is_file() else None
home = tmp / 'home'
(home / '.config').mkdir(parents=True)
uid = os.getuid()
state = tmp / 'logind'
for d in ('users', 'sessions', 'seats'):
    (state / d).mkdir(parents=True)


def seat(active):
    """This user's graphical session g2 (VT 2) and tty session c7 (VT 5); gr is the greeter."""
    (state / f'users/{uid}').write_text('STATE=active\nSESSIONS=c7 g2\nDISPLAY=g2\n')
    (state / 'sessions/g2').write_text(f'UID={uid}\nTYPE=wayland\nCLASS=user\nSEAT=seat0\nTTY=tty2\nVTNR=2\n')
    (state / 'sessions/c7').write_text(f'UID={uid}\nTYPE=tty\nCLASS=user\nSEAT=seat0\nTTY=tty5\nVTNR=5\n')
    (state / 'sessions/gr').write_text('UID=979\nTYPE=wayland\nCLASS=greeter\nSEAT=seat0\nVTNR=1\n')
    t = state / 'seats/.seat0'
    t.write_text(f'ACTIVE={active}\n')
    t.rename(state / 'seats/seat0')


seat('g2')
env0 = dict(os.environ, MIOS_VENDOR_TOML=str(SSOT))
env0.pop('MIOS_TOML', None)
primary = sh(['pgrep', '-x', 'Hyprland', '-o']).stdout.strip()
launcher_log = open(tmp / 'launcher.log', 'w')
launcher = subprocess.Popen(['/usr/bin/mios-gen', 'nested-session', '--layout', 'pip'], env=env0, stdout=launcher_log, stderr=subprocess.STDOUT)
sdir = Path(os.environ['XDG_RUNTIME_DIR']) / f'mios-nested-desktop-{launcher.pid}'
npid = None
procs = []
tmux_env = dict(os.environ, TMUX_TMPDIR=str(tmux_tmp), TERM='linux')
tmux_env.pop('TMUX', None)


def tmux(*a):
    return sh(['tmux', '-L', sock, *a], env=tmux_env, timeout=5)


class Nested:
    def __init__(self, s):
        self.env = dict(env0, XDG_RUNTIME_DIR=str(sdir), HYPRLAND_INSTANCE_SIGNATURE=s['instance']['instance'],
                        WAYLAND_DISPLAY=s['instance']['wl_socket'], HOME=str(home), XDG_CONFIG_HOME=str(home / '.config'),
                        TMUX_TMPDIR=str(tmux_tmp))
        self.env.pop('DISPLAY', None)
        self.tool_env = {k: v for k, v in self.env.items() if k not in ('MIOS_VENDOR_TOML', 'MIOS_TOML')}

    def ctl(self, *a):
        return sh(['hyprctl', *a], env=self.env)

    def eval(self, lua):
        return self.ctl('eval', lua)

    def clients(self):
        try:
            return json.loads(self.ctl('-j', 'clients').stdout or '[]')
        except json.JSONDecodeError:
            return []

    def window(self, cls):
        return next((c for c in self.clients() if c.get('class') == cls), None)

    def active(self):
        try:
            return json.loads(self.ctl('-j', 'activewindow').stdout or '{}').get('address')
        except json.JSONDecodeError:
            return None

    def focus(self, addr):
        self.eval(f"hl.dispatch(hl.dsp.focus({{window='address:{addr}'}}))")
        return wait(lambda: self.active() == addr, 5, 0.1)


class Pad:
    def __init__(self, layout):
        self.p = subprocess.Popen([BIN, 'fixture-pad', '--layout', layout], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        procs.append(self.p)
        self.info = json.loads(self.p.stdout.readline())
        self.path = self.info['path']

    def send(self, *events, hold=0.05):
        self.p.stdin.write(json.dumps({'events': [list(e) for e in events]}) + '\n')
        self.p.stdin.flush()
        self.p.stdout.readline()
        time.sleep(hold)

    def tap(self, code, ms=60):
        self.send((1, code, 1), hold=ms / 1000)
        self.send((1, code, 0), hold=0.35)

    def grab_free(self):
        fd = os.open(self.path, os.O_RDONLY | os.O_NONBLOCK)
        try:
            fcntl.ioctl(fd, EVIOCGRAB, 1)
            fcntl.ioctl(fd, EVIOCGRAB, 0)
            return True
        except OSError:
            return False
        finally:
            os.close(fd)

    def close(self):
        self.p.stdin.close()
        self.p.wait(timeout=5)


class Vt:
    """A pty standing in for VT 5: argv reads it as fd 0; the bridge writes console bytes into its master."""

    def __init__(self, argv):
        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 30, 100, 0, 0))
        self.tty = os.ttyname(slave)
        self.p = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave, env=tmux_env, start_new_session=True)
        procs.append(self.p)
        os.close(slave)
        self.out = bytearray()

        def drain():
            try:
                while True:
                    b = os.read(self.master, 65536)
                    if not b:
                        return
                    self.out.extend(b)
            except OSError:
                return
        threading.Thread(target=drain, daemon=True).start()


class Bridge:
    def __init__(self, nested, pad, vt, binary=BIN, broot=root, seat_view=True):
        argv = [binary, '--root', str(broot), 'bridge', '--fixture-only', '--device', pad.path, '--live-context', '--pad-path', str(tmp / f'pad-{len(procs)}.bin')]
        if seat_view:
            argv += ['--logind-state', str(state), '--console-tty', vt.tty, '--console-sink-fd', str(vt.master)]
        self.p = subprocess.Popen(argv, env=nested.tool_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, pass_fds=(vt.master,))
        procs.append(self.p)
        self.log = []
        for s in (self.p.stdout, self.p.stderr):
            threading.Thread(target=lambda s=s: [self.log.append(l.rstrip()) for l in s], daemon=True).start()
        self.up = wait(lambda: any('"mode":"bridge"' in l for l in self.log) or self.p.poll() is not None, 10) and self.p.poll() is None

    def records(self, key, since=0):
        out = []
        for l in self.log[since:]:
            try:
                v = json.loads(l)
            except json.JSONDecodeError:
                continue
            if isinstance(v, dict) and key in v:
                out.append(v)
        return out

    def seat(self):
        r = self.records('seat')
        return r[-1]['seat'] if r else None

    def stop(self):
        self.p.send_signal(signal.SIGTERM)
        try:
            self.p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.p.kill()
        return self.p.returncode


class Probe:
    """A nested Alacritty whose pty is raw and copied to a file: the bytes are what the window received."""

    def __init__(self, n, cls):
        self.cls, self.path = cls, tmp / f'{cls}.bytes'
        self.path.write_bytes(b'')
        n.eval(f"hl.dispatch(hl.dsp.exec_cmd('alacritty --class {cls} -e sh -c \"stty raw -echo; exec cat > {self.path}\"'))")

    def mark(self):
        return self.path.stat().st_size

    def since(self, m):
        time.sleep(0.35)
        return self.path.read_bytes()[m:]


class FileVt(Vt):
    def __init__(self):
        self.path = tmp / 'vt.bytes'
        self.path.write_bytes(b'')
        super().__init__(['sh', '-c', f'stty raw -echo; exec cat > {self.path}'])

    def mark(self):
        return self.path.stat().st_size

    def since(self, m):
        time.sleep(0.35)
        return self.path.read_bytes()[m:]


try:
    def session():
        try:
            v = json.loads((sdir / 'session.json').read_text())
        except (OSError, json.JSONDecodeError):
            return None
        return v if v.get('instance', {}).get('pid') and v.get('parent_window') else None
    s = wait(session, 240, 0.2)
    if not s:
        raise SystemExit('nested session did not publish session.json: ' + (tmp / 'launcher.log').read_text()[-600:])
    npid = s['instance']['pid']
    n = Nested(s)
    check('nested session up', n.ctl('-j', 'version').returncode == 0)
    gui = Probe(n, 'mios-i3-probe')
    check('probe window mapped', wait(lambda: n.window(gui.cls), 30))
    addr = n.window(gui.cls)['address']
    check('probe focused', n.focus(addr))
    time.sleep(0.5)
    pad = Pad('dualsense')
    vt = FileVt()

    if old_root is not None:
        # NEGATIVE: the pre-I3 bridge has no seat view: it refuses --logind-state, and without it a pad press still
        # lands in the nested window while this user is on the VT.
        seat('c7')
        br = Bridge(n, pad, vt, binary=OLD, broot=old_root)
        check('NEGATIVE: the pre-I3 bridge cannot read the seat (--logind-state refused)', not br.up and br.p.returncode == 2, br.log[-2:])
        br = Bridge(n, pad, vt, binary=OLD, broot=old_root, seat_view=False)
        check('negative-control bridge (pre-I3) up', br.up, br.log[-3:])
        time.sleep(0.6)
        m, mv = gui.mark(), vt.mark()
        pad.tap(SOUTH)
        got, gv = gui.since(m), vt.since(mv)
        check('NEGATIVE: with this user on the VT, the pre-I3 bridge still types into the desktop window', got == b'\r' and gv == b'', (got, gv))
        check('negative-control bridge exits cleanly', br.stop() == 0)
        seat('g2')

    br = Bridge(n, pad, vt)
    check('candidate bridge up', br.up, br.log[-3:])
    check('graphical seat followed', wait(lambda: br.seat() in ('graphical', 'owned'), 5), br.records('seat')[-2:])
    time.sleep(0.8)
    m, mv = gui.mark(), vt.mark()
    pad.tap(SOUTH)
    got, gv = gui.since(m), vt.since(mv)
    check('graphical session active: South reaches the nested window, not the VT', got == b'\r' and gv == b'', (got, gv))

    seat('c7')
    check('tty session active: console context on the VT', wait(lambda: any(r['seat'] == 'console' for r in br.records('seat')), 5), br.records('seat')[-2:])
    time.sleep(0.5)
    m, mv = gui.mark(), vt.mark()
    pad.tap(SOUTH)
    pad.tap(WEST, 700)
    pad.tap(EAST, 700)
    pad.send((3, 1, -30000), hold=0.2); pad.send((3, 1, 0), hold=0.2)
    pad.tap(0x139)
    got, gv = gui.since(m), vt.since(mv)
    check('console: Enter, BTab (ESC TAB), Ctrl+C, Up, PageDown reach the VT and nothing reaches the desktop',
          gv == b'\r\x1b\t\x03\x1b[A\x1b[6~' and got == b'', (gv, got))
    ns = len(br.records('vt_switch'))
    pad.send((1, L3, 1), (1, R3, 1), hold=0.1); pad.send((1, L3, 0), (1, R3, 0), hold=0.5)
    sw = br.records('vt_switch')[ns:]
    room = [r for r in br.records('action') if r.get('action') == 'mios:hyprland-3d-toggle']
    check('C-17: L3+R3 on the VT switches to the graphical VT (Ctrl+Alt+F2), never the room',
          [(r['vt_switch'], r['submitted']) for r in sw] == [('CTRL+ALT+F2', True)] and not room, (sw, room))

    seat('gr')
    check('INV-8: the greeter owns the seat -> the grab is released', wait(lambda: br.seat() == 'released', 5) and pad.grab_free(), br.records('seat')[-2:])
    m, mv = gui.mark(), vt.mark()
    pad.tap(SOUTH)
    got, gv = gui.since(m), vt.since(mv)
    check('INV-8: nothing is synthesized for another session', got == b'' and gv == b'', (got, gv))

    seat('g2')
    check('back on the desktop: the pad is owned again', wait(lambda: br.seat() in ('owned', 'graphical') and not pad.grab_free(), 5), br.records('seat')[-3:])
    n.focus(addr)
    time.sleep(0.6)
    m, mv = gui.mark(), vt.mark()
    pad.tap(SOUTH)
    got, gv = gui.since(m), vt.since(mv)
    check('back on the desktop: South reaches the nested window again', got == b'\r' and gv == b'', (got, gv))
    check('candidate bridge exits cleanly', br.stop() == 0, br.log[-3:])

    # A VT tmux client behind the C-18 console bridge: tmux verbs from the pad, and an evdev pointer click as SGR.
    conf = tmp / 'tmux.conf'
    conf.write_text('set -g prefix C-b\nset -g mouse on\n')
    check('private tmux server', all(tmux('-f', str(conf), 'new-session', '-d', '-s', s, '-x', '100', '-y', '29').returncode == 0 for s in ('s1', 's2'))
          and tmux('split-window', '-h', '-t', 's2').returncode == 0)
    fifo = tmp / 'pointer.fifo'
    os.mkfifo(fifo)
    hold = os.open(fifo, os.O_RDWR | os.O_NONBLOCK)
    blog = tmp / 'console-bridge.log'
    tvt = Vt([BIN, '--root', str(root), 'console-bridge', '--mouse', 'evdev', '--pointer-device', str(fifo), '--log', str(blog), '--',
              'tmux', '-L', sock, 'attach', '-t', 's2'])
    check('VT tmux client attached through the console bridge', wait(lambda: tmux('list-clients').stdout.strip(), 8))
    seat('c7')
    br = Bridge(n, pad, tvt)
    check('candidate bridge up (VT tmux)', br.up and wait(lambda: any(r['seat'] == 'console' for r in br.records('seat')), 5), br.log[-3:])
    time.sleep(1.5)
    sess = lambda: tmux('list-clients', '-F', '#{client_session}').stdout.strip()
    pad.tap(LB)
    check('console_tmux: LB = previous tmux session for the VT client', wait(lambda: sess() == 's1', 3), (sess(), br.records('tmux')[-1:]))
    pad.tap(RB)
    check('console_tmux: RB = next tmux session', wait(lambda: sess() == 's2', 3), sess())
    pane = lambda: tmux('display', '-p', '-t', 's2', '#{pane_index}').stdout.strip()
    bl = lambda: [json.loads(l) for l in blog.read_text().splitlines() if l.strip()] if blog.exists() else []
    check('C-18: the console bridge holds the evdev pointer while tmux tracks the mouse', wait(lambda: any(r.get('evdev') == 'open' for r in bl()), 5), bl()[-2:])
    os.write(hold, struct.pack('=qqHHi', 0, 0, 2, 0, -320) + struct.pack('=qqHHi', 0, 0, 0, 0, 0))
    os.write(hold, struct.pack('=qqHHi', 0, 0, 1, 0x110, 1) + struct.pack('=qqHHi', 0, 0, 0, 0, 0) + struct.pack('=qqHHi', 0, 0, 1, 0x110, 0) + struct.pack('=qqHHi', 0, 0, 0, 0, 0))
    check('C-18: a pointer click reaches tmux as SGR 1006 and selects the left pane', wait(lambda: pane() == '0', 4)
          and any('\x1b[<0;10;15M' in r.get('reports', '') for r in bl()), (pane(), bl()[-2:]))
    check('C-18: the VT pty never entered mouse reporting', not any(m in bytes(tvt.out) for m in (b'\x1b[?1000h', b'\x1b[?1006h')))
    check('candidate bridge exits cleanly (VT tmux)', br.stop() == 0, br.log[-3:])
    os.close(hold)
    pad.close()
finally:
    for p in procs:
        if p.poll() is None:
            p.kill()
            p.wait()
    tmux('kill-server')
    if npid:
        try:
            os.kill(npid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        launcher.wait(timeout=30)
    except subprocess.TimeoutExpired:
        launcher.kill()
    shutil.rmtree(tmp, ignore_errors=True)
    check('nested cleaned up', not sdir.exists())
    check('primary compositor unchanged', sh(['pgrep', '-x', 'Hyprland', '-o']).stdout.strip() == primary)
    check('no fixture pad left behind', not any(n.read_text().startswith('mios-input-fixture-') for n in Path('/sys/class/input').glob('event*/device/name')))
print(f'{sum(res)}/{len(res)} passed')
sys.exit(0 if res and all(res) else 1)
