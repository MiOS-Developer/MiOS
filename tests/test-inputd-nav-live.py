#!/usr/bin/env python3
# AI-hint: LIVE opt-in test (MIOS_LIVE_NESTED=1) of mios-inputd step I2 in an owned nested MiOS PiP session: a private uinput pad (xpad = analog-only triggers, then DualSense) drives the candidate fixture bridge, which projects nav output into the nested compositor; raw bytes reaching nested Alacritty probes prove key:, a right-stick flick moves nested focus, a private tmux server proves tmux discovery/verbs, the capture OSD fires once per capture/release; the I1 binary is the negative control.
# AI-related: tools/native/mios-inputd/src/nav.rs, tools/native/mios-inputd/src/runtime.rs, tests/test-inputd-nav.py, tests/test-inputd-ownership-live.py, docs/design/mios-global-bindings-tests.md (I2)
# AI-functions: check, Nested, Pad, Bridge, Probe
# Environment: MIOS_LIVE_NESTED=1 (else skip), MIOS_INPUTD_BIN = candidate (default installed), MIOS_INPUTD_OLD_BIN = the
# pre-I2 binary for the negative control, MIOS_VENDOR_TOML = the SSOT under test (default: this tree's). Nothing is sent
# to the primary compositor: every projection goes to the nested compositor named in its session.json, and no uinput
# keyboard is created (the fixture tier projects key: with send_key_state to the nested focused window).
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

TREE = Path(__file__).resolve().parents[1]
SSOT = Path(os.environ.get('MIOS_VENDOR_TOML', str(TREE / 'usr/share/mios/mios.toml')))
BIN = os.environ.get('MIOS_INPUTD_BIN', '/usr/libexec/mios/mios-inputd')
OLD = os.environ.get('MIOS_INPUTD_OLD_BIN', '')
SOUTH, EAST, WEST, NORTH, RT, LT, START, GUIDE, L3, R3 = 0x130, 0x131, 0x134, 0x133, 0x139, 0x138, 0x13b, 0x13c, 0x13d, 0x13e
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

tmp = Path(tempfile.mkdtemp(prefix='mios-inputd-i2-live-'))
sock = f'mios-i2-live-{os.getpid()}'
tmux_tmp = tmp / 'tmux'
tmux_tmp.mkdir(mode=0o700)
root = tmp / 'root'
(root / 'usr/share/mios').mkdir(parents=True)
text = SSOT.read_text()
text = text.replace('tmux_sockets = ["mios-human", "mios-nested"]', f'tmux_sockets = ["{sock}"]')
text = re.sub(r'capture_osd_argv = \[.*\]', 'capture_osd_argv = ["/usr/bin/logger", "--no-act", "-s", "{message}"]', text)
(root / 'usr/share/mios/mios.toml').write_text(text)
home = tmp / 'home'
(home / '.config').mkdir(parents=True)
env0 = dict(os.environ, MIOS_VENDOR_TOML=str(SSOT))
env0.pop('MIOS_TOML', None)
primary = sh(['pgrep', '-x', 'Hyprland', '-o']).stdout.strip()
launcher_log = open(tmp / 'launcher.log', 'w')
launcher = subprocess.Popen(['/usr/bin/mios-gen', 'nested-session', '--layout', 'pip'], env=env0, stdout=launcher_log, stderr=subprocess.STDOUT)
sdir = Path(os.environ['XDG_RUNTIME_DIR']) / f'mios-nested-desktop-{launcher.pid}'
npid = None
procs = []
tmux_env = dict(os.environ, TMUX_TMPDIR=str(tmux_tmp))
tmux_env.pop('TMUX', None)


def tmux(*a):
    return sh(['tmux', '-L', sock, *a], env=tmux_env, timeout=5)


class Nested:
    def __init__(self, s):
        self.env = dict(env0, XDG_RUNTIME_DIR=str(sdir), HYPRLAND_INSTANCE_SIGNATURE=s['instance']['instance'],
                        WAYLAND_DISPLAY=s['instance']['wl_socket'], HOME=str(home), XDG_CONFIG_HOME=str(home / '.config'))
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

    def axis(self, code, value, hold=0.35):
        self.send((3, code, value), hold=hold)

    def close(self):
        self.p.stdin.close()
        self.p.wait(timeout=5)


class Bridge:
    def __init__(self, nested, pad, binary=BIN):
        argv = [binary, '--root', str(root), 'bridge', '--fixture-only', '--device', pad.path, '--live-context', '--pad-path', str(tmp / f'pad-{len(procs)}.bin')]
        self.p = subprocess.Popen(argv, env=nested.tool_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        procs.append(self.p)
        self.log = []
        for s in (self.p.stdout, self.p.stderr):
            threading.Thread(target=lambda s=s: [self.log.append(l.rstrip()) for l in s], daemon=True).start()
        self.up = wait(lambda: any('"mode":"bridge"' in l for l in self.log), 10)

    def records(self, key, since=0):
        out = []
        for l in self.log[since:]:
            try:
                v = json.loads(l)
            except json.JSONDecodeError:
                continue
            if key in v:
                out.append(v)
        return out

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
        cls_arg = '' if cls == 'Alacritty' else f'--class {cls} '
        n.eval(f"hl.dispatch(hl.dsp.exec_cmd('alacritty {cls_arg}-e sh -c \"stty raw -echo; exec cat > {self.path}\"'))")

    def mark(self):
        return self.path.stat().st_size

    def since(self, m):
        time.sleep(0.3)
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
    conf = tmp / 'tmux.conf'
    conf.write_text('set -g prefix C-b\n' + ''.join(f'bind {k} select-pane -{d}\n' for k, d in zip('hjkl', 'LDUR')))
    check('private tmux server', tmux('-f', str(conf), 'new-session', '-d', '-s', 't', '-x', '120', '-y', '40').returncode == 0
          and tmux('new-window', '-t', 't:').returncode == 0)
    gui = Probe(n, 'mios-i2-probe')            # an ordinary window (desktop-2d)
    game = Probe(n, 'steam_app_570')           # a game-class window: uncaptured, it navigates like any window
    term = Probe(n, 'Alacritty')               # a terminal without tmux (terminal_plain)
    n.eval(f"hl.dispatch(hl.dsp.exec_cmd('alacritty --class mios-i2-tmux -e env TMUX_TMPDIR={tmux_tmp} tmux -L {sock} attach -t t'))")
    classes = [gui.cls, game.cls, term.cls, 'mios-i2-tmux']
    check('probe windows mapped', wait(lambda: all(n.window(c) for c in classes), 30), [c for c in classes if not n.window(c)])
    check('tmux client attached', wait(lambda: tmux('list-clients').stdout.strip(), 10))
    # mios-i2-tmux is not a terminal class; the tmux profile is chosen by class 'terminal' only, so tag it as a
    # terminal through the SSOT copy's terminal context list.
    t = (root / 'usr/share/mios/mios.toml').read_text().replace('terminal = ["mios-terminal", "Alacritty", "kitty", "foot"]',
                                                               'terminal = ["mios-terminal", "Alacritty", "kitty", "foot", "mios-i2-tmux"]')
    (root / 'usr/share/mios/mios.toml').write_text(t)

    # Two probes side by side on the active workspace, opted out of PiP gravity (which keeps one visible window), so
    # directional focus has a geometric neighbour.
    ws = json.loads(n.ctl('-j', 'activeworkspace').stdout or '{}').get('id', 1)
    for i, cls in enumerate((gui.cls, game.cls)):
        a = n.window(cls)['address']
        n.eval(f"local t='address:{a}'; hl.dispatch(hl.dsp.window.tag({{window=t,tag='+mios-pip-disabled'}})); "
               f"hl.dispatch(hl.dsp.window.move({{workspace='{ws}',window=t,follow=false}})); hl.dispatch(hl.dsp.window.float({{window=t,action='enable'}})); "
               f"hl.dispatch(hl.dsp.window.resize({{window=t,x=420,y=300,exact=true}})); hl.dispatch(hl.dsp.window.move({{window=t,x={40 + i * 480},y=120}}))")
    time.sleep(0.8)
    print('  layout:', [(c['class'], c['at'], c['size'], c.get('floating'), c['workspace'].get('name')) for c in n.clients()], flush=True)
    if OLD and Path(OLD).is_file():
        pad = Pad('dualsense')
        br = Bridge(n, pad, OLD)
        check('negative-control bridge (pre-I2) up', br.up, br.log[-3:])
        n.focus(n.window(gui.cls)['address']); time.sleep(0.6)
        m = gui.mark(); pad.tap(SOUTH); pad.tap(0x134)
        got = gui.since(m)
        check('NEGATIVE: the pre-I2 bridge sends an uncaptured window no keys', got == b'', got)
        check('negative-control bridge exits cleanly', br.stop() == 0)
        pad.close()

    for layout in ('xpad', 'dualsense'):
        pad = Pad(layout)
        br = Bridge(n, pad)
        check(f'{layout}: candidate bridge owns only the fixture pad', br.up, br.log[-3:])
        for probe in (gui, game):
            w = n.window(probe.cls)
            check(f'{layout}/{probe.cls}: focused', n.focus(w['address']))
            time.sleep(0.6)
            m = probe.mark()
            pad.tap(SOUTH)                     # Enter
            pad.tap(WEST, 650)                 # hold = Shift+Tab
            pad.axis(17, -1); pad.axis(17, 0)  # HAT up = Up
            pad.tap(EAST)                      # Esc
            pad.tap(L3)                        # Ctrl+T (on release)
            if layout == 'xpad':
                pad.axis(5, 200); pad.axis(5, 0)   # analog-only RT edge = Ctrl+Tab
            got = probe.since(m)
            want = b'\r\x1b[Z\x1b[A\x1b\x14' + (b'\t' if layout == 'xpad' else b'')
            check(f'{layout}/{probe.cls}: South/West-hold/D-pad/East/L3{"/analog RT" if layout == "xpad" else ""} reach the window as keys', got == want, f'{got!r} want {want!r}')
        # Cursor mode keeps South = Enter; Start toggles it back.
        w = n.window(gui.cls); n.focus(w['address']); time.sleep(0.5)
        m = gui.mark(); pad.tap(START); pad.tap(SOUTH); pad.tap(START)
        check(f'{layout}: cursor mode keeps South = Enter', gui.since(m) == b'\r', gui.since(m))
        # Right-stick flick moves focus in the nested compositor. Control first: the direction in which the
        # compositor's own movefocus reaches a neighbour in this layout, and that neighbour.
        target = None
        for d, (code, v) in (('right', (3, 30000)), ('left', (3, -30000)), ('down', (4, 30000)), ('up', (4, -30000))):
            n.focus(w['address']); time.sleep(0.3)
            n.eval(f"hl.dispatch(hl.dsp.focus({{direction='{d}'}}))")
            got = wait(lambda: n.active() != w['address'] and n.active(), 1.5)
            if got:
                target = (d, code, v, got)
                break
        if target is None:
            check(f'{layout}: right-stick flick moves nested focus', False, 'control: the layout has no directional neighbour for the probe')
        else:
            d, code, v, want = target
            n.focus(w['address']); time.sleep(0.5)
            pad.axis(code, v); pad.axis(code, 0, hold=0.6)
            check(f'{layout}: right-stick flick {d} moves nested focus like the compositor movefocus', n.active() == want,
                  f"want={want} got={n.active()} projected={[r for r in br.records('projected') if r['projected'].startswith('hypr:')][-1:]}")
        # Terminal without tmux: East hold = Ctrl+C, and LT never runs tmux.
        tw = n.window(term.cls); n.focus(tw['address']); time.sleep(0.6)
        m = term.mark(); mt = len(br.records('tmux'))
        lt = (lambda: (pad.axis(2, 200), pad.axis(2, 0))) if layout == 'xpad' else (lambda: pad.tap(LT))  # xpad: analog-only LT
        pad.tap(EAST, 650); lt()
        check(f'{layout}: terminal_plain East hold = Ctrl+C', term.since(m) == b'\x03', term.since(m))
        check(f'{layout}: terminal_plain never runs tmux', len(br.records('tmux')) == mt)
        # Terminal with a tmux client: LT = previous window, a flick inside the window selects the pane only.
        xw = n.window('mios-i2-tmux'); n.focus(xw['address']); time.sleep(1.4)
        win = lambda: tmux('display', '-p', '-t', 't', '#{window_index}').stdout.strip()
        before_win = win(); lt(); time.sleep(0.3)
        check(f'{layout}: terminal_tmux LT = tmux previous window', win() != before_win and any(r['tmux'] == 'key p' and r['ok'] for r in br.records('tmux')),
              f"{before_win}->{win()} {br.records('tmux')[-1:]}")
        tmux('select-window', '-t', 't:0')
        if tmux('display', '-p', '-t', 't:0', '#{window_panes}').stdout.strip() == '1':
            tmux('split-window', '-h', '-t', 't:0')  # the new right pane is active
        else:
            tmux('select-pane', '-t', 't:0.1')
        tmux('select-layout', '-t', 't:0', 'even-horizontal')  # side by side: pane 1 has a neighbour on its left
        tmux('select-pane', '-t', 't:0.1')
        time.sleep(0.3)
        pane = lambda: tmux('display', '-p', '-t', 't', '#{pane_index}').stdout.strip()
        p0, a0 = pane(), n.active()
        tty = tmux('list-clients', '-F', '#{client_tty}').stdout.strip()
        print('  tmux before flick:', tmux('display', '-p', '-c', tty, '-t', tty, '#{session_name} #{window_index}.#{pane_index} at_left=#{pane_at_left} w=#{window_width} left=#{pane_left}').stdout.strip(),
              tmux('list-panes', '-s', '-t', 't', '-F', '#{window_index}.#{pane_index}:#{pane_left},#{pane_width},#{?pane_active,A,}').stdout.split(), flush=True)
        pad.axis(3, -30000); pad.axis(3, 0, hold=0.6)
        check(f'{layout}: tmux flick left inside the window selects the pane, not another window', pane() != p0 and n.active() == a0,
              f"pane {p0}->{pane()} active {a0}->{n.active()} {br.records('tmux')[-1:]}")
        # Capture OSD: one per confirmed capture and release, naming the class.
        n.focus(n.window(gui.cls)['address']); time.sleep(0.6)
        mo = len(br.records('osd'))
        pad.tap(NORTH); time.sleep(0.6)
        pad.send((1, L3, 1), (1, R3, 1), hold=0.8); pad.send((1, L3, 0), (1, R3, 0), hold=0.8)
        osd = [r['osd'] for r in br.records('osd')[mo:]]
        check(f'{layout}: capture OSD on capture and release', osd == [f'Controller -> {gui.cls}. Hold L3+R3 to release.', f'Controller -> MiOS. {gui.cls} released.'], osd)
        n.focus(n.window(game.cls)['address']); time.sleep(0.8)
        check(f'{layout}: a focus change shows no OSD', len(br.records('osd')) == mo + 2)
        check(f'{layout}: bridge exits cleanly on SIGTERM', br.stop() == 0, br.log[-3:])
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
