#!/usr/bin/env python3
# AI-hint: LIVE opt-in test (MIOS_LIVE_NESTED=1) of the mios-controls device views inside an owned nested MiOS PiP session: keyboard, mouse, controller (Xbox / PlayStation / generic glyphs), bindings and devices drawings (a screenshot of each, plus TERM=linux), and a private grabbed uinput fixture pad driving the editor (d-pad, triggers, South) and capture-to-bind (grab proven by a second reader), a pad rebind with undo/redo, a refused colliding rebind and an apply to a scratch user tier.
# AI-related: tools/native/mios-controls/src/tui.rs, tools/native/mios-controls/src/pads.rs, tools/native/mios-controls/src/layout.rs, tests/fixtures/mios-controls/fixture_pad.py, tools/native/mios-gen/src/nested_session.rs, tests/test-mios-controls.py
"""Live device views of mios-controls in an owned nested PiP session, driven through the nested compositor's key path
and a private fixture pad.

Touches the primary desktop only through the stock launcher (mios-gen nested-session --layout pip). Every dispatch goes
to the NESTED compositor (its own instance signature and runtime dir); the operator's user tier is hash-checked. The app
reads only the fixture pad ([desktop.controls].pad_names from a private host-tier fragment), so a capture grabs only it.
Run it inside the shared nested-session lock:
  MIOS_LIVE_NESTED=1 MIOS_CONTROLS=<candidate> MIOS_TEST_ROOT=<worktree> \\
    flock /home/mios/mios-desktop-work-1/nested.lock python3 tests/test-mios-controls-visual-live.py
"""
import atexit
import hashlib
import json
import os
import select
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / 'tests' / 'fixtures' / 'mios-controls'))
from fixture_pad import FixturePad  # noqa: E402

ROOT = Path(os.environ.get('MIOS_TEST_ROOT', HERE))
# Evidence is opt-in: a caller-supplied MIOS_LIVE_OUT is kept; the temporary default is
# removed when the run exits, so a skipped or finished run leaves nothing behind.
KEEP_OUT = bool(os.environ.get('MIOS_LIVE_OUT'))
OUT = Path(os.environ.get('MIOS_LIVE_OUT') or tempfile.mkdtemp(prefix='mios-controls-visual-live-'))
if not KEEP_OUT:
    atexit.register(shutil.rmtree, OUT, True)
OUT_NOTE = str(OUT) if KEEP_OUT else 'removed at exit; set MIOS_LIVE_OUT=<dir> to keep it'
BIN = os.environ.get('MIOS_CONTROLS', '/usr/bin/mios-controls')
GEN = os.environ.get('MIOS_GEN', '/usr/bin/mios-gen')
USER_TIER = Path.home() / '.config/mios/mios.toml'
SCRATCH = OUT / 'scratch-user.toml'
TMUXDIR = OUT / 'tmux'
HOSTD = OUT / 'host.d'
SOCK = 'mios-controls-visual-live'
TAG = 'ctlvis'
results = []


def log(msg):
    print(msg, flush=True)


def check(name, ok, detail=''):
    results.append((name, bool(ok), str(detail)))
    log(f"{'PASS' if ok else 'FAIL'} {name} {detail}")
    return ok


def sh(args, env=None, timeout=30):
    return subprocess.run(args, capture_output=True, text=True, env=env, timeout=timeout)


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else 'absent'


def primary_pid():
    """The primary compositor: the Hyprland of the newest /run/user/<uid>/hypr instance (never an inherited one)."""
    hypr = Path(os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')) / 'hypr'
    newest = max((p for p in hypr.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime, default=None)
    lock = newest / 'hyprland.lock' if newest else None
    if lock and lock.exists():
        first = lock.read_text().split('\n', 1)[0].strip()
        if first.isdigit():
            return int(first)
    return 0


def primary_ok(pid):
    alive = pid > 0 and Path(f'/proc/{pid}').exists()
    portal = open('/proc/self/mountinfo').read().count('fuse.portal')
    return alive and portal == 1, f'pid={pid} alive={alive} fuse.portal={portal}'


class Nested:
    def __init__(self):
        self.proc = None
        self.desc = None
        self.env = None

    def start(self):
        env = {k: v for k, v in os.environ.items() if k not in ('MIOS_TOML', 'HYPRLAND_INSTANCE_SIGNATURE', 'WAYLAND_DISPLAY')}
        env['MIOS_VENDOR_TOML'] = str(ROOT / 'usr/share/mios/mios.toml')
        self.proc = subprocess.Popen([GEN, 'nested-session', '--layout', 'pip', '--no-devices'], env=env,
                                     stdout=open(OUT / 'launcher.log', 'w'), stderr=subprocess.STDOUT, start_new_session=True)
        path = Path(env.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')) / f'mios-nested-desktop-{self.proc.pid}' / 'session.json'
        end = time.monotonic() + 90
        while time.monotonic() < end:
            if self.proc.poll() is not None:
                raise RuntimeError(f'launcher exited {self.proc.returncode}: {(OUT / "launcher.log").read_text()[-800:]}')
            try:
                d = json.loads(path.read_text())
            except (OSError, ValueError):
                d = None
            if d and d.get('instance') and d.get('wayland_display') and d.get('compositor_pid'):
                self.desc = d
                self.env = {k: v for k, v in os.environ.items() if k not in ('DISPLAY', 'MIOS_TOML')}
                self.env.update(XDG_RUNTIME_DIR=d['runtime'], HYPRLAND_INSTANCE_SIGNATURE=d['instance'], WAYLAND_DISPLAY=d['wayland_display'])
                if self.ctl('-j', 'monitors').returncode == 0:
                    return
            time.sleep(0.25)
        raise RuntimeError('nested compositor did not come up (no session.json)')

    def ctl(self, *a, timeout=20):
        return sh(['hyprctl', *a], env=self.env, timeout=timeout)

    def eval(self, lua):
        return self.ctl('eval', lua)

    def clients(self):
        try:
            return json.loads(self.ctl('-j', 'clients').stdout)
        except ValueError:
            return []

    def shot(self, name):
        p = OUT / f'{name}.png'
        r = sh(['grim', str(p)], env=self.env, timeout=30)
        (OUT / f'{name}.txt').write_text(screen())
        return p if r.returncode == 0 and p.exists() else None

    def stop(self):
        pid = (self.desc or {}).get('compositor_pid')
        for p in (pid, self.proc.pid if self.proc else None):
            if p:
                try:
                    os.kill(p, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        if self.proc:
            try:
                self.proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(self.proc.pid, signal.SIGKILL)
                self.proc.wait(timeout=10)


def tmux(*a):
    env = dict(os.environ, TMUX_TMPDIR=str(TMUXDIR))
    env.pop('TMUX', None)
    return sh(['tmux', '-L', SOCK, *a], env=env, timeout=10)


def screen(target='c'):
    return tmux('capture-pane', '-p', '-t', target).stdout


def wait_screen(want, timeout=15.0, target='c'):
    end = time.monotonic() + timeout
    s = ''
    while time.monotonic() < end:
        s = screen(target)
        if want(s) if callable(want) else want in s:
            return True, s
        time.sleep(0.1)
    return False, s


def line(s, n):
    rows = s.splitlines()
    return rows[n] if len(rows) > n else ''


def grabbed(node, press):
    """True when a second reader of the fixture's event node sees nothing while `press` runs (EVIOCGRAB held)."""
    fd = os.open(f'/dev/input/{node}', os.O_RDONLY | os.O_NONBLOCK)
    try:
        while select.select([fd], [], [], 0)[0]:
            os.read(fd, 24 * 64)
        press()
        got = select.select([fd], [], [], 0.3)[0]
        data = os.read(fd, 24 * 64) if got else b''
        keys = [struct.unpack('llHHi', data[i:i + 24]) for i in range(0, len(data) - 23, 24)]
        return not any(k[2] == 1 for k in keys)
    finally:
        os.close(fd)


def main():
    for d in (TMUXDIR, HOSTD):
        d.mkdir(mode=0o700, exist_ok=True)
    for frag in sorted(Path('/etc/mios/mios.d').glob('*.toml')):
        try:
            (HOSTD / frag.name).write_text(frag.read_text())
        except OSError:
            pass
    (HOSTD / '99-controls-live.toml').write_text(f'[desktop.controls]\npad_names = ["mios-input-fixture-{TAG}"]\ncapture_timeout_ms = 8000\n')
    if SCRATCH.exists():
        SCRATCH.unlink()
    user_before = sha(USER_TIER)
    ppid = primary_pid()
    ok, d = primary_ok(ppid)
    if not check('preflight: primary compositor + document portal', ok, d):
        return 1
    adm = sh([GEN, 'mem-admit', '--wait'], timeout=900)
    if not check('memory admission', adm.returncode == 0, adm.stdout.strip()):
        return 1
    pad = FixturePad(family='xbox', tag=TAG)
    check('fixture pad created', pad.event, f'{pad.name} {pad.event}')
    n = Nested()
    tmux('kill-server')
    try:
        app = (f'env -u MIOS_TOML -u MIOS_VENDOR_TOML MIOS_USER_TOML={SCRATCH} MIOS_USER_TOML_D={OUT}/none.d '
               f'MIOS_HOST_TOML_D={HOSTD} TERM=xterm-256color {BIN} --root {ROOT}; echo EXITED:$?; sleep 600')
        r = tmux('-f', '/dev/null', 'new-session', '-d', '-s', 'c', '-x', '170', '-y', '46', app)
        check('private tmux server for the app', r.returncode == 0, r.stderr.strip())
        tmux('set', '-g', 'focus-events', 'on')
        n.start()
        check('nested PiP session started', True, f"instance={n.desc['instance']} compositor={n.desc['compositor_pid']}")
        attach = f'env TMUX_TMPDIR={TMUXDIR} tmux -L {SOCK} attach -t c'
        n.eval(f"hl.dispatch(hl.dsp.exec_cmd({json.dumps('alacritty --class mios-controls-visual -o font.size=8 -e ' + attach)}))")
        win = None
        for _ in range(150):
            win = next((c for c in n.clients() if c.get('class') == 'mios-controls-visual'), None)
            if win:
                break
            time.sleep(0.2)
        if not check('app terminal mapped in the nested session', win):
            return 1
        addr = win['address']
        n.eval(f"hl.dispatch(hl.dsp.focus({{window='address:{addr}'}}))")
        n.eval("hl.dispatch(hl.dsp.window.fullscreen({['mode']='fullscreen',['action']='toggle'}))")

        def key(k, mods=''):
            for st in ('down', 'up'):
                n.eval(f"hl.dispatch(hl.dsp.send_key_state({{mods='{mods}',key='{k}',state='{st}',window='address:{addr}'}}))")
            time.sleep(0.1)

        ok, s = wait_screen(lambda s: 'valid:' in s, 60)
        check('TUI renders and validates the saved map', ok, line(s, 0))
        ok, s = wait_screen(f'mios-input-fixture-{TAG}', 10)
        check('pad thread reads only the fixture pad', ok and 'AnnePro' not in s.splitlines()[-1], s.splitlines()[-1][-90:] if s else '')

        # ---- every device view, through the nested compositor's key path ----
        key('1')
        ok, s = wait_screen(lambda s: 'Keyboard' in s and 'Space' in s and 'Bksp' in s)
        check('keyboard view: the full layout is drawn', ok)
        key('c')
        wait_screen('CAPTURE', 5)
        key('x')
        ok, s = wait_screen('key x (')
        check('keyboard capture-to-bind selects the pressed key and lists its uses', ok and 'tmux-os' in s)
        check('screenshot keyboard', n.shot('01-keyboard'))
        key('2')
        ok, s = wait_screen(lambda s: 'Mouse' in s and 'Side' in s and 'Extra' in s)
        check('mouse view: buttons, wheel and side buttons drawn', ok)
        check('screenshot mouse', n.shot('02-mouse'))
        key('3')
        ok, s = wait_screen(f'Controller: Xbox (mios-input-fixture-{TAG}')
        check('controller view: the fixture pad is drawn with the Xbox family', ok and 'LB' in s and 'RT' in s, line(s, 3)[:80])
        check('screenshot controller xbox', n.shot('03-controller-xbox'))
        key('f')
        ok, s = wait_screen('Controller: PlayStation')
        check('family cycle: PlayStation glyphs', ok and 'L1' in s and 'Options' in s)
        check('screenshot controller playstation', n.shot('04-controller-playstation'))
        key('f')
        key('f')
        ok, s = wait_screen('Controller: Generic')
        check('family cycle: generic glyphs', ok and 'Guide' in s)
        check('screenshot controller generic', n.shot('05-controller-generic'))
        key('f')  # back to the device's own family
        wait_screen('Controller: Xbox', 5)
        key('4')
        ok, s = wait_screen(lambda s: 'Bindings' in s and 'app-drawer' in s)
        check('bindings view', ok)
        check('screenshot bindings', n.shot('06-bindings'))
        key('5')
        ok, s = wait_screen(f'mios-input-fixture-{TAG}')
        check('devices view lists the fixture pad', ok)
        check('screenshot devices', n.shot('07-devices'))

        # ---- the fixture pad drives the editor ----
        pad.trigger('L', 1023)  # LT = previous view: Devices -> Bindings
        ok, _ = wait_screen(lambda s: 'app-drawer' in s and 'Devices' in s, 5)
        check('pad LT: previous view', ok)
        pad.trigger('L', 1023)  # -> Controller
        ok, s = wait_screen(f'Controller: Xbox (mios-input-fixture-{TAG}', 5)
        check('pad LT again: the controller view', ok)
        before = line(s, 3)
        pad.hat(x=1)
        ok, s = wait_screen(lambda s: line(s, 3) != before, 5)
        check('pad d-pad moves the cursor on the drawing', ok, line(s, 3)[118:170])
        pad.press('BTN_SOUTH')
        ok, s = wait_screen(lambda s: True, 1)
        pad.press('BTN_EAST')

        # ---- capture-to-bind from the pad, with the grab proven by a second reader ----
        key('c')
        ok, s = wait_screen('CAPTURE', 5)
        check('capture armed', ok)
        ok, s = wait_screen('grabbed', 5)
        check('capture grabs the fixture pad (status)', ok, s.splitlines()[-2][:120] if s else '')
        g = grabbed(pad.event, lambda: pad.press('BTN_NORTH'))
        ok, s = wait_screen('Y (BTN_NORTH)', 5)
        check('capture from the pad selects the pressed button', ok, line(s, 3)[118:170])
        check('while capturing, a second reader sees no events (EVIOCGRAB held)', g)
        g2 = grabbed(pad.event, lambda: pad.press('BTN_START'))
        check('negative control: after the capture the pad is released (a second reader sees events)', not g2)
        wait_screen(lambda s: True, 0.5)
        key('Escape')  # close the help Start may have opened
        check('screenshot pad capture', n.shot('08-pad-capture'))

        # ---- rebind a Guide-layer button with the pad, undo / redo, apply ----
        key('slash')
        tmux('send-keys', '-t', 'c', '-l', 'launch-browser')
        wait_screen('Search "launch-browser"', 5)
        key('Return')
        key('Return')
        ok, _ = wait_screen('Guide + BTN_NORTH on the Controller drawing', 5)
        check('search jumps to the Guide-layer use', ok)
        key('r')
        ok, _ = wait_screen('REBIND Guide + BTN_NORTH', 5)
        check('rebind pending', ok)
        key('c')
        wait_screen('CAPTURE', 5)
        pad.press('BTN_WEST')
        ok, s = wait_screen('Guide + BTN_NORTH <-> Guide + BTN_WEST (swapped)', 30)
        check('pad capture finishes the rebind (swap)', ok, s.splitlines()[-2][:120] if s else '')
        ok, s = wait_screen(lambda s: 'DRAFT' in line(s, 0) and 'valid: ' in s and 'finding(s)' in s and 'new finding' not in s, 60)
        check('draft revalidated clean', ok)
        check('screenshot pad rebind', n.shot('09-pad-rebind'))
        key('u')
        ok, s = wait_screen(lambda s: '| saved |' in line(s, 0), 5)
        check('undo returns to the saved map', ok)
        key('U', 'SHIFT')
        ok, s = wait_screen(lambda s: 'DRAFT' in line(s, 0), 5)
        check('redo restores the draft', ok)
        wait_screen(lambda s: 'validating' not in s and 'valid: ' in s, 60)
        key('w')
        wait_screen('Write the draft', 5)
        key('y')
        ok, _ = wait_screen('applied to', 30)
        data = SCRATCH.read_text() if SCRATCH.exists() else ''
        check('apply wrote the scratch user tier only (0600)', ok and 'BTN_WEST = "mios:launch-browser"' in data and (SCRATCH.stat().st_mode & 0o777) == 0o600, data.strip()[:200])

        # ---- negative control: a rebind that does not project is refused ----
        before_apply = sha(SCRATCH)
        key('1')
        for _ in range(10):
            key('l')
        ok, _ = wait_screen('CTRL+b then  (l', 5)
        key('c')
        wait_screen('CAPTURE', 5)
        key('x')
        wait_screen('key x (', 5)
        key('r')
        key('c')
        wait_screen('CAPTURE', 5)
        key('k')
        ok, s = wait_screen('new finding', 60)
        check('negative control: tmux rebind onto a used key adds a finding', ok and '!tmux-os' in s)
        check('screenshot conflict', n.shot('10-conflict'))
        key('w')
        ok, _ = wait_screen('apply blocked', 5)
        check('negative control: apply is blocked and nothing is written', ok and sha(SCRATCH) == before_apply)
        key('z')  # throw the refused draft away
        key('y')

        # ---- transition 2D -> Hypr3D room -> 2D: the views keep working ----
        r = sh([GEN, 'hyprland-3d-toggle'], env=n.env, timeout=60)
        probe = n.eval("if not (hl.plugin.hypr3d and hl.plugin.hypr3d.active()) then error('room inactive',0) end").stdout.strip()
        if check('transition: the nested session entered the Hypr3D room', r.returncode == 0 and probe == 'ok', f'rc={r.returncode} probe={probe}'):
            time.sleep(1.5)
            n.eval(f"hl.dispatch(hl.dsp.focus({{window='address:{addr}'}}))")
            key('3')
            ok, _ = wait_screen('Controller: Xbox', 10)
            check('3D: compositor keys still reach the TUI (controller view)', ok)
            check('screenshot 3D room', n.shot('12-hypr3d-controller'))
            r = sh([GEN, 'hyprland-3d-toggle'], env=n.env, timeout=60)
            probe = n.eval("if hl.plugin.hypr3d and hl.plugin.hypr3d.active() then error('room still active',0) end").stdout.strip()
            check('transition: back to 2D', r.returncode == 0 and probe == 'ok', f'rc={r.returncode} probe={probe}')
            n.eval(f"hl.dispatch(hl.dsp.focus({{window='address:{addr}'}}))")
            key('1')
            ok, _ = wait_screen(lambda s: 'Bksp' in s, 10)
            check('2D again: keyboard view', ok)
        key('q')
        if wait_screen('discard', 3)[0]:
            key('y')
        ok, _ = wait_screen('EXITED:0', 10)
        check('quit cleanly', ok)

        # ---- the VT console drawing ----
        vt = (f'env -u MIOS_TOML -u MIOS_VENDOR_TOML MIOS_USER_TOML={SCRATCH} MIOS_USER_TOML_D={OUT}/none.d '
              f'MIOS_HOST_TOML_D={HOSTD} TERM=linux {BIN} --root {ROOT}; echo EXITED:$?; sleep 600')
        tmux('new-session', '-d', '-s', 'v', '-x', '170', '-y', '46', vt)
        tmux('switch-client', '-t', 'v')
        ok, s = wait_screen('MiOS Controls', 20, 'v')
        key('3')
        ok, s = wait_screen('Controller: Xbox', 5, 'v')
        odd = {c for c in s if ord(c) > 127 and not 0x2500 <= ord(c) < 0x2580}
        check('TERM=linux: ASCII labels and single box lines only', ok and not odd, ''.join(sorted(odd)))
        p = OUT / '11-vt-controller.png'
        sh(['grim', str(p)], env=n.env, timeout=30)
        (OUT / '11-vt-controller.txt').write_text(s)
        check('screenshot VT controller', p.exists())
        key('q')
    except Exception as e:  # noqa: BLE001 -- the driver reports, the finally cleans up
        check('driver', False, repr(e))
    finally:
        n.stop()
        tmux('kill-server')
        pad.close()
        ok, d = primary_ok(ppid)
        check('postflight: primary compositor + portal unchanged', ok, d)
        check('operator user tier untouched', sha(USER_TIER) == user_before)
        check('nested compositor gone', not (n.desc and Path(f"/proc/{n.desc['compositor_pid']}").exists()))
    passed = sum(1 for r in results if r[1])
    (OUT / 'results.json').write_text(json.dumps([{'name': a, 'pass': b, 'detail': c} for a, b, c in results], indent=1))
    log(f'{passed}/{len(results)} passed; evidence in {OUT_NOTE}')
    return 0 if passed == len(results) else 1


if __name__ == '__main__':
    if os.environ.get('MIOS_LIVE_NESTED') != '1':
        print('skip: live nested-session test (set MIOS_LIVE_NESTED=1 inside a MiOS desktop session)')
        sys.exit(0)
    sys.exit(main())
