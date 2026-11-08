#!/usr/bin/env python3
# AI-hint: LIVE opt-in test (MIOS_LIVE_NESTED=1) of the tmux-os launcher (miosd launcher) in ONE owned nested MiOS PiP session: the session's terminal drops to the TUI session selector (not tmux-os), CTRL+SUPER released alone opens the launcher on the focused tmux-os client while CTRL+SUPER+= still scales, a real right-click (virtual pointer) opens instance management (float, pop out to its own window, move to a session, close), and a private uinput pad through the candidate fixture bridge drives every launcher move (Guide opens, d-pad/left stick move, A selects, B backs out, triggers page, X options). Screenshots of the nested output only.
# AI-related: src/mios-rs/miosd/src/launcher.rs, src/mios-rs/miosd/src/sessions.rs, tools/native/mios-gen/src/global_bindings.rs, tools/native/mios-gen/src/nested_session.rs, usr/share/mios/mios.toml, tests/fixtures/shell-hud/vptr.c, tests/test-tmux-os.py
# AI-functions: check, sh, wait, Nested, Pad, Bridge
# Environment: MIOS_LIVE_NESTED=1 (else skip). MIOS_TMUXOS_CAND = a directory with the candidate miosd, mios-gen and
# mios-inputd (built from this tree); they are bind-mounted over /usr/bin/miosd, /usr/bin/mios-gen and
# /usr/libexec/mios/mios-inputd inside a private mount namespace (bwrap) that holds ONLY the nested session, together
# with this tree's mios-terminal, tmux projections and SSOT. Nothing is installed and the primary compositor only
# receives send_key_state for the nested window. MIOS_TMUXOS_EVIDENCE = where screenshots go (kept; default: a temp dir removed at exit).
# The fixture pad is grabbed by the fixture bridge before any event is emitted (Steam on the host reads uinput pads).
import atexit
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
SSOT = TREE / 'usr/share/mios/mios.toml'
CAND = Path(os.environ.get('MIOS_TMUXOS_CAND', ''))
SOUTH, EAST, WEST, LT, RT, GUIDE = 0x130, 0x131, 0x134, 0x138, 0x139, 0x13c
ABS_Y, HAT_Y = 1, 17
res = []


def check(name, ok, detail=''):
    res.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'} {name} {detail if not ok or os.environ.get('VERBOSE') else ''}".rstrip(), flush=True)
    return bool(ok)


def sh(argv, env=None, timeout=30):
    return subprocess.run(argv, capture_output=True, text=True, env=env, timeout=timeout)


def wait(pred, seconds=6.0, step=0.1):
    end = time.monotonic() + seconds
    while True:
        v = pred()
        if v or time.monotonic() > end:
            return v
        time.sleep(step)


if os.environ.get('MIOS_LIVE_NESTED') != '1':
    print('skip: live nested-session test (set MIOS_LIVE_NESTED=1 inside a MiOS desktop session)')
    sys.exit(0)
for b in ('miosd', 'mios-gen', 'mios-inputd'):
    if not (CAND / b).is_file():
        sys.exit(f'MIOS_TMUXOS_CAND must hold the candidate {b}')

uid_rt = Path(f'/run/user/{os.getuid()}')
primary_pid = sh(['pgrep', '-x', 'Hyprland', '-o']).stdout.strip()
# The live signature: the newest instance directory whose lock names the primary compositor (never an inherited one).
sigs = sorted((uid_rt / 'hypr').iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
primary_sig = next((p.name for p in sigs if (p / 'hyprland.lock').is_file()
                    and (p / 'hyprland.lock').read_text().split('\n')[0].strip() == primary_pid), None)
if not primary_sig:
    sys.exit('no live primary Hyprland instance')
penv = {k: v for k, v in os.environ.items() if k not in ('MIOS_TOML', 'TMUX', 'TMUX_PANE')}
penv.update(HYPRLAND_INSTANCE_SIGNATURE=primary_sig, XDG_RUNTIME_DIR=str(uid_rt))
tmp = Path(tempfile.mkdtemp(prefix='mios-tmuxos-live-'))
# The scratch directory is removed at exit. Evidence is opt-in: only a caller-supplied
# MIOS_TMUXOS_EVIDENCE survives the run; the default lives inside the scratch.
atexit.register(shutil.rmtree, tmp, True)
KEEP_EVID = 'MIOS_TMUXOS_EVIDENCE' in os.environ
EVID = Path(os.environ.get('MIOS_TMUXOS_EVIDENCE', str(tmp / 'evidence')))
EVID.mkdir(parents=True, exist_ok=True)
procs = []

# ---- the owned nested session, in a private mount namespace with the candidates ---------------------------------
binds = [(CAND / 'miosd', '/usr/bin/miosd'), (CAND / 'mios-gen', '/usr/bin/mios-gen'),
         (CAND / 'mios-inputd', '/usr/libexec/mios/mios-inputd'),
         (TREE / 'usr/libexec/mios/mios-terminal', '/usr/libexec/mios/mios-terminal'),
         (TREE / 'usr/share/mios/tmux', '/usr/share/mios/tmux'), (SSOT, '/usr/share/mios/mios.toml')]
argv = ['bwrap', '--die-with-parent', '--dev-bind', '/', '/']
for src, dst in binds:
    argv += ['--ro-bind', str(src), dst]
argv += ['--', '/usr/bin/mios-gen', 'nested-session', '--layout', 'pip', '--no-devices']
lenv = dict(penv, MIOS_VENDOR_TOML=str(SSOT))
before = {p.name for p in uid_rt.glob('mios-nested-desktop-*')}
launcher = subprocess.Popen(argv, env=lenv, stdout=open(tmp / 'launcher.log', 'w'), stderr=subprocess.STDOUT, start_new_session=True)


def session():
    for d in uid_rt.glob('mios-nested-desktop-*'):
        if d.name in before:
            continue
        try:
            v = json.loads((d / 'session.json').read_text())
        except (OSError, ValueError):
            continue
        if v.get('instance', {}).get('pid') and v.get('parent_window'):
            return d, v
    return None


class Nested:
    def __init__(self, d, s):
        self.dir, self.s, self.pw = d, s, s['parent_window']
        self.env = dict(lenv, XDG_RUNTIME_DIR=str(d), HYPRLAND_INSTANCE_SIGNATURE=s['instance']['instance'],
                        WAYLAND_DISPLAY=s['instance']['wl_socket'])
        self.env.pop('DISPLAY', None)
        self.sock = s['tmux_socket']

    def ctl(self, *a):
        return sh(['hyprctl', *a], env=self.env)

    def clients(self):
        try:
            return json.loads(self.ctl('-j', 'clients').stdout or '[]')
        except ValueError:
            return []

    def windows(self, cls):
        return [c for c in self.clients() if c.get('class') == cls]

    def monitor(self):
        try:
            return (json.loads(self.ctl('-j', 'monitors').stdout or '[{}]') or [{}])[0]
        except ValueError:
            return {}

    def focus(self, addr):
        self.ctl('eval', f"hl.dispatch(hl.dsp.focus({{window='address:{addr}'}}))")
        return wait(lambda: (json.loads(self.ctl('-j', 'activewindow').stdout or '{}') or {}).get('address') == addr, 4)

    def key(self, mods, key, state):
        # Real key events into the nested window on the primary: the nested compositor sees them as keyboard input.
        sh(['hyprctl', 'eval', f"hl.dispatch(hl.dsp.send_key_state({{mods='{mods}',key='{key}',state='{state}',window='address:{self.pw}'}}))"], env=penv)

    def tap(self, key, mods=''):
        self.key(mods, key, 'down')
        self.key(mods, key, 'up')
        time.sleep(0.25)

    def tmux(self, *a):
        return sh(['tmux', '-S', self.sock, *a], env=self.env, timeout=5)

    def shot(self, name):
        out = EVID / f'{name}.png'
        r = sh(['grim', '-o', self.monitor().get('name', ''), str(out)], env=self.env)
        return out if r.returncode == 0 else None


def picks():
    """Running launcher popups (`miosd launcher pick`) of this session's tmux server."""
    out = []
    for p in Path('/proc').iterdir():
        if p.name.isdigit():
            try:
                cmd = (p / 'cmdline').read_bytes().split(b'\0')
            except OSError:
                continue
            if len(cmd) > 2 and cmd[0].endswith(b'miosd') and cmd[1:3] == [b'launcher', b'pick']:
                out.append(int(p.name))
    return out


def descendants(pid):
    kids = {}
    for p in Path('/proc').iterdir():
        if p.name.isdigit():
            try:
                ppid = int((p / 'stat').read_text().rsplit(')', 1)[1].split()[1])
            except (OSError, IndexError, ValueError):
                continue
            kids.setdefault(ppid, []).append(int(p.name))
    out, todo = [], [pid]
    while todo:
        for k in kids.get(todo.pop(), []):
            out.append(k)
            todo.append(k)
    return out


def cmdline(pid):
    try:
        return Path(f'/proc/{pid}/cmdline').read_bytes().replace(b'\0', b' ').decode(errors='replace').strip()
    except OSError:
        return ''


class Pad:
    def __init__(self, layout):
        self.p = subprocess.Popen([str(CAND / 'mios-inputd'), 'fixture-pad', '--layout', layout], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        procs.append(self.p)
        self.path = json.loads(self.p.stdout.readline())['path']

    def send(self, *events, hold=0.05):
        self.p.stdin.write(json.dumps({'events': [list(e) for e in events]}) + '\n')
        self.p.stdin.flush()
        self.p.stdout.readline()
        time.sleep(hold)

    def tap(self, code, ms=60):
        self.send((1, code, 1), hold=ms / 1000)
        self.send((1, code, 0), hold=0.4)

    def axis(self, code, value, hold=0.3):
        self.send((3, code, value), hold=hold)


class Bridge:
    def __init__(self, n, pad, root):
        argv = [str(CAND / 'mios-inputd'), '--root', str(root), 'bridge', '--fixture-only', '--device', pad.path,
                '--live-context', '--pad-path', str(tmp / 'pad.bin')]
        env = {k: v for k, v in n.env.items() if k not in ('MIOS_VENDOR_TOML', 'MIOS_TOML')}
        self.p = subprocess.Popen(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        procs.append(self.p)
        self.log = []
        for s in (self.p.stdout, self.p.stderr):
            threading.Thread(target=lambda s=s: [self.log.append(l.rstrip()) for l in s], daemon=True).start()
        self.up = wait(lambda: any('"mode":"bridge"' in l for l in self.log), 10)


npid = None
try:
    got = wait(session, 240, 0.25)
    if not got:
        raise SystemExit('nested session did not publish session.json: ' + (tmp / 'launcher.log').read_text()[-800:])
    n = Nested(*got)
    npid = n.s['instance']['pid']
    check('owned nested PiP session up (candidates in a private mount namespace)', n.ctl('-j', 'version').returncode == 0, n.dir.name)

    # ---- (4) the session's terminal drops to the TUI session selector, not into tmux-os ------------------------
    term = wait(lambda: (n.windows('mios-nested-terminal') or [None])[0], 30)
    check('the nested session opened its terminal', term is not None)
    sel = wait(lambda: [cmdline(p) for p in descendants(term['pid']) if ' sessions' in cmdline(p) and 'miosd' in cmdline(p)], 10)
    check('(4) the terminal runs the TUI session selector (miosd sessions)', sel, [cmdline(p) for p in descendants(term['pid'])])
    check('(4) no tmux-os client was attached straight away', not n.tmux('list-clients').stdout.strip(), n.tmux('list-clients').stdout)
    time.sleep(1.0)
    n.shot('4-nested-selector')
    n.focus(term['address'])
    n.tap('n')                                     # the selector's "New Tmux-OS session"
    client = wait(lambda: n.tmux('list-clients', '-F', '#{client_name}').stdout.strip().split('\n')[0], 8)
    check('(4) the selector entered a new Tmux-OS session', client, n.tmux('list-sessions').stdout)
    sess = n.tmux('display-message', '-p', '-c', client, '#{session_name}').stdout.strip()
    n.tmux('new-window', '-d', '-t', f'={sess}:', '-n', 'editor', 'sleep 900')
    n.tmux('split-window', '-d', '-t', f'={sess}:editor', 'sleep 900')
    n.tmux('new-window', '-d', '-t', f'={sess}:', '-n', 'logs', 'sleep 900')
    win = lambda: n.tmux('display-message', '-p', '-c', client, '#{window_name}').stdout.strip()

    # ---- (1) CTRL+SUPER (modifier-only, fired on release) opens the launcher on the focused client --------------
    def chord(extra=None):
        n.key('', 'Control_L', 'down')
        n.key('CTRL', 'Super_L', 'down')
        if extra:
            n.key('CTRL SUPER', extra, 'down')
            n.key('CTRL SUPER', extra, 'up')
        n.key('CTRL SUPER', 'Super_L', 'up')
        n.key('CTRL', 'Control_L', 'up')

    def close_all():
        for _ in range(3):
            if not picks():
                return True
            n.tap('Escape')
            wait(lambda: not picks(), 2)
        return not picks()

    check('no launcher before the chord', not picks())
    scale0 = n.monitor().get('scale')
    chord('equal')                                 # negative control: CTRL+SUPER+= is session scale, never the launcher
    time.sleep(1.2)
    check('(1) negative: CTRL+SUPER+<key> does not open the launcher', not picks(), picks())
    scaled = wait(lambda: n.monitor().get('scale') != scale0, 3)
    check('(1) negative: the CTRL+SUPER+= binding still works under the chord', scaled, (scale0, n.monitor().get('scale')))
    chord('0')                                     # session scale reset
    wait(lambda: n.monitor().get('scale') == scale0, 3)
    time.sleep(0.6)
    chord()
    opened = wait(picks, 6)
    check('(1) CTRL+SUPER pressed and released alone opens the launcher', opened)
    time.sleep(0.6)
    n.shot('1-5-launcher-ctrl-super')

    # ---- (5) organised: headers, aligned key column, two columns, centred ----------------------------------------
    cw, ch = (int(x) for x in n.tmux('display-message', '-p', '-c', client, '#{client_width} #{client_height}').stdout.split())
    pane = n.tmux('display-message', '-p', '-c', client, '#{pane_id}').stdout.strip()
    pw, ph = max(34, cw * 86 // 100), max(10, ch * 84 // 100)
    lenv2 = dict(n.env, TMUX=f'{n.sock},0,0')
    r = sh([str(CAND / 'miosd'), 'launcher', 'list', '--root', '/', '--client', client, '--pane', pane,
            '--width', str(pw - 2), '--height', str(ph - 2)], env=lenv2)
    try:
        listing = json.loads(r.stdout)
    except ValueError:
        listing = {'screen': '', 'error': r.stderr}
    screen = listing.get('screen', '').split('\n')
    (EVID / 'launcher-screen.txt').write_text('\n'.join(screen))
    heads = [h for h in ('Running', 'Tmux-OS', 'Pane', 'Window', 'Session') if any(re.search(rf'{h} ─{{3,}}', l) for l in screen)]
    check('(5) grouped sections under headers', len(heads) == 5, heads)
    check('(5) two columns at this width', any(len(re.findall(r'\S ─{5,}', l)) >= 2 for l in screen), cw)

    def cell(text, nth=0):
        hits = [(l.find(text), y) for y, l in enumerate(screen) if text in l]
        return hits[nth] if len(hits) > nth else None
    check('(5) the launcher lists the running instances', cell('editor') and cell('logs'), '\n'.join(screen[:12]))

    # ---- (2) a real right-click on an instance opens instance management ----------------------------------------
    t = n.windows('mios-nested-terminal')[0]
    (ax, ay), (aw, ah) = t['at'], t['size']
    cwpx, chpx = aw / cw, ah / ch
    ox, oy = (cw - pw) // 2 + 1, (ch - ph) // 2 + 1  # display-popup -x C -y C, inside its border
    mon = n.monitor()
    sc = mon.get('scale') or 1
    lw, lh = round(mon.get('width', 0) / sc), round(mon.get('height', 0) / sc)
    vbuild = tmp / 'vptr'
    vbuild.mkdir()
    fx = TREE / 'tests/fixtures/shell-hud'
    xml = str(fx / 'wlr-virtual-pointer-unstable-v1.xml')
    ok = sh(['wayland-scanner', 'client-header', xml, str(vbuild / 'vp-client-protocol.h')]).returncode == 0
    ok = ok and sh(['wayland-scanner', 'private-code', xml, str(vbuild / 'vp-protocol.c')]).returncode == 0
    flags = sh(['pkg-config', '--cflags', '--libs', 'wayland-client']).stdout.split()
    ok = ok and sh(['gcc', '-O1', f'-I{vbuild}', '-o', str(vbuild / 'vptr'), str(fx / 'vptr.c'), str(vbuild / 'vp-protocol.c'), *flags]).returncode == 0
    check('virtual pointer fixture built (nested compositor only)', ok)

    def ptr(col, row, mode):
        x, y = ax + (ox + col + 0.5) * cwpx, ay + (oy + row + 0.5) * chpx
        return sh([str(vbuild / 'vptr'), mon.get('name', ''), str(int(x)), str(int(y)), mode],
                  env=dict(n.env, VPTR_W=str(lw), VPTR_H=str(lh))).returncode == 0

    def click(text, mode='click', nth=0, scr=None):
        global screen
        if scr is not None:
            screen = scr
        c = cell(text, nth)
        if not c:
            return False
        ptr(c[0] + 1, c[1], 'hover')
        time.sleep(0.15)
        return ptr(c[0] + 1, c[1], mode)

    click('editor', 'rclick')
    time.sleep(0.8)
    n.shot('2-rightclick-options')
    # The options screen replaces the list inside the same popup: its rows start at the popup's first content row.
    check('(2) the right-click left the launcher open (options, not activation)', picks() and win() != 'editor', win())
    # Options of a running instance, in launcher order (Go to, Pop out, Float, Move to window, Move to session,
    # Rename, Close): reach "Float the pane" by keys rather than guessing its pixel row, then confirm the result.
    n.tap('Down'); n.tap('Down')                    # Go to -> Pop out -> Float the pane
    n.tap('Return')
    floated = wait(lambda: '1' in n.tmux('list-panes', '-t', f'={sess}:editor', '-F', '#{pane_floating_flag}').stdout.split(), 4)
    check('(2) right-click options: Float the pane floats the instance', floated,
          n.tmux('list-panes', '-t', f'={sess}:editor', '-F', '#{pane_id} #{pane_floating_flag}').stdout)
    close_all()

    # Pop out to its own window: a new desktop terminal (in the nested session) attached to pop-logs.
    chord(); wait(picks, 6); time.sleep(0.5)
    terms0 = len(n.windows('Alacritty'))
    click('logs', 'rclick'); time.sleep(0.6)
    n.tap('Down'); n.tap('Return')                  # Go to -> Pop out
    popped = wait(lambda: any(s.startswith('pop-') for s in n.tmux('list-sessions', '-F', '#{session_name}').stdout.split()), 5)
    check('(2) Pop out moves the instance into its own session', popped, n.tmux('list-sessions').stdout)
    neww = wait(lambda: len(n.windows('Alacritty')) > terms0, 8)
    check('(2) Pop out opens it in its own desktop window', neww, [c['class'] for c in n.clients()])
    time.sleep(0.8)
    n.shot('2-popped-out')
    close_all()
    n.focus(n.windows('mios-nested-terminal')[0]['address'])
    time.sleep(0.4)

    # Move to session: the editor window into a new session.
    chord(); wait(picks, 6); time.sleep(0.5)
    click('editor', 'rclick'); time.sleep(0.6)
    for _ in range(4):
        n.tap('Down')
    n.tap('Return'); time.sleep(0.5)
    n.shot('2-move-to-session')
    n.tap('End'); n.tap('Up'); n.tap('Return')      # "New session" is the row above Cancel
    moved = wait(lambda: 'editor' not in n.tmux('list-windows', '-t', f'={sess}', '-F', '#{window_name}').stdout.split(), 5)
    check('(2) Move to session... moves the window to another session', moved, n.tmux('list-windows', '-a').stdout)
    close_all()

    # Close (asks first, starts on No).
    n.tmux('new-window', '-d', '-t', f'={sess}:', '-n', 'victim', 'sleep 900')
    chord(); wait(picks, 6); time.sleep(0.5)
    click('victim', 'rclick'); time.sleep(0.6)
    n.tap('End'); n.tap('Return'); time.sleep(0.4)
    n.shot('2-close-confirm')
    n.tap('Down'); n.tap('Return')                  # the confirmation starts on No; Yes is below it
    gone = wait(lambda: 'victim' not in n.tmux('list-windows', '-a', '-F', '#{window_name}').stdout.split(), 5)
    check('(2) Close... (after confirmation) closes the instance', gone)
    close_all()
    # A left click on a row activates it (goes to it).
    chord(); wait(picks, 6); time.sleep(0.5)
    click('logs') if cell('logs') else None
    time.sleep(0.4)

    # ---- (3) controller: Guide opens, d-pad / left stick move, A selects, B backs out, triggers page, X options --
    close_all()
    n.tmux('select-window', '-t', f'={sess}:0')
    root = tmp / 'root'
    (root / 'usr/share/mios').mkdir(parents=True)
    txt = SSOT.read_text()
    txt = re.sub(r'capture_osd_argv = \[.*\]', 'capture_osd_argv = ["/usr/bin/logger", "--no-act", "-s", "{message}"]', txt)
    (root / 'usr/share/mios/mios.toml').write_text(txt)
    pad = Pad('dualsense')
    br = Bridge(n, pad, root)
    check('(3) the candidate fixture bridge grabbed the private pad before any event', br.up, br.log[-3:])
    n.focus(n.windows('mios-nested-terminal')[0]['address'])
    time.sleep(0.8)
    pad.tap(GUIDE)
    check('(3) Guide tap (tmux:menu) opens the launcher on the focused tmux-os client', wait(picks, 6), br.log[-4:])
    time.sleep(0.5)
    n.shot('3-controller-guide')
    pad.tap(EAST)
    check('(3) B backs out (closes the launcher)', wait(lambda: not picks(), 4))
    w0 = win()
    pad.tap(GUIDE); wait(picks, 6); time.sleep(0.4)
    pad.axis(HAT_Y, 1); pad.axis(HAT_Y, 0)          # d-pad down: the next running instance
    pad.tap(SOUTH)
    check('(3) d-pad moves the selection and A selects (goes to the next instance)', wait(lambda: win() != w0 and not picks(), 4), (w0, win()))
    n.tmux('select-window', '-t', f'={sess}:0'); time.sleep(0.3)
    pad.tap(GUIDE); wait(picks, 6); time.sleep(0.4)
    a = n.shot('3-before-stick')
    pad.axis(ABS_Y, 32000, 0.6); pad.axis(ABS_Y, 0)  # left stick = wheel: the selection moves down
    pad.tap(RT)                                      # trigger = tmux:key n = page down
    time.sleep(0.4)
    b = n.shot('3-after-stick-trigger')
    check('(3) left stick and triggers move the selection (screen changed)', a and b and a.read_bytes() != b.read_bytes())
    pad.tap(WEST)                                    # X = options of the selected row
    time.sleep(0.5)
    n.shot('3-controller-options')
    check('(3) X opens the selected row options (the launcher stays open)', picks())
    pad.tap(EAST); pad.tap(EAST)
    check('(3) B twice backs out of options and the launcher', wait(lambda: not picks(), 4))
    # Every generated tmux-os menu too: with the launcher open, menu rows run as the controller selects them.
    pad.tap(GUIDE); wait(picks, 6)
    pad.tap(EAST)
    br.p.send_signal(signal.SIGTERM)
    br.p.wait(timeout=10)
    pad.p.stdin.close()
    pad.p.wait(timeout=5)
finally:
    for p in procs:
        if p.poll() is None:
            p.kill()
    if npid:
        try:
            os.kill(npid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        os.killpg(launcher.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        launcher.wait(timeout=20)
    except subprocess.TimeoutExpired:
        os.killpg(launcher.pid, signal.SIGKILL)
    print(f'evidence: {EVID}' if KEEP_EVID else
          'evidence: removed at exit; set MIOS_TMUXOS_EVIDENCE=<dir> to keep it', flush=True)

print(f'{sum(res)}/{len(res)} passed', flush=True)
sys.exit(0 if res and all(res) else 1)
