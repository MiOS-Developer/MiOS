#!/usr/bin/env python3
# AI-hint: LIVE opt-in test (MIOS_LIVE_NESTED=1) of the mios-controls TUI inside an owned nested MiOS PiP session: compositor key path, every editor function, 2D, Hypr3D and the transitions (2D->3D, new window in the room, 3D->2D, fullscreen resize).
# AI-related: tools/native/mios-controls/src/tui.rs, tools/native/mios-gen/src/nested_session.rs, tests/test-mios-controls.py
# Live test of mios-controls inside an owned nested MiOS Hyprland PiP session: 2D, 3D (Hypr3D) and transitions.
# Touches the primary desktop only through the stock launcher (mios-gen nested-session, PiP) and never dispatches
# to the primary compositor. The app uses a scratch user tier; the operator's tier is hash-checked before/after.
import hashlib, json, os, signal, subprocess, sys, tempfile, time
from pathlib import Path

OUT = Path(os.environ.get('MIOS_LIVE_OUT') or tempfile.mkdtemp(prefix='mios-controls-live-'))
BIN = os.environ.get('MIOS_CONTROLS', '/usr/bin/mios-controls')
PRIMARY_PID = int(os.environ.get('PRIMARY_PID') or subprocess.run(['pgrep', '-x', 'Hyprland', '-o'], capture_output=True, text=True).stdout.strip() or 0)
PRIMARY_SIG = os.environ['HYPRLAND_INSTANCE_SIGNATURE']
USER_TIER = Path.home() / '.config/mios/mios.toml'
SCRATCH = OUT / 'scratch-user.toml'
SOCK = 'mios-controls-live'
TMUXDIR = OUT / 'tmux'
results = []


def log(msg):
    print(msg, flush=True)


def check(name, ok, detail=''):
    results.append((name, bool(ok), detail))
    log(f"{'PASS' if ok else 'FAIL'} {name} {detail}")
    return ok


def sh(args, env=None, timeout=30, check_rc=False):
    r = subprocess.run(args, capture_output=True, text=True, env=env, timeout=timeout)
    if check_rc and r.returncode:
        raise RuntimeError(f'{args}: {r.returncode} {r.stderr.strip()}')
    return r


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else 'absent'


def primary_ok():
    pid = sh(['pgrep', '-x', 'Hyprland', '-o']).stdout.strip()
    portal = open('/proc/self/mountinfo').read().count('fuse.portal')
    return pid == str(PRIMARY_PID) and portal == 1, f'pid={pid} fuse.portal={portal}'


class Nested:
    def __init__(self):
        self.proc = None
        self.dir = None
        self.env = None
        self.pid = None

    def start(self):
        env = dict(os.environ)
        self.proc = subprocess.Popen([os.environ.get('MIOS_GEN', '/usr/bin/mios-gen'), 'nested-session', '--layout', 'pip'], env=env, stdout=open(OUT / 'launcher.log', 'w'), stderr=subprocess.STDOUT)
        self.dir = Path(env['XDG_RUNTIME_DIR']) / f'mios-nested-desktop-{self.proc.pid}'
        end = time.monotonic() + 60
        while time.monotonic() < end:
            if self.proc.poll() is not None:
                raise RuntimeError(f'launcher exited {self.proc.returncode}: {(OUT / "launcher.log").read_text()[-800:]}')
            if self.dir.is_dir():
                inst = sh(['hyprctl', '-j', 'instances'], env=dict(os.environ, XDG_RUNTIME_DIR=str(self.dir))).stdout
                try:
                    v = json.loads(inst)
                except ValueError:
                    v = []
                if v:
                    self.pid = v[0]['pid']
                    self.env = dict(os.environ, XDG_RUNTIME_DIR=str(self.dir), HYPRLAND_INSTANCE_SIGNATURE=v[0]['instance'], WAYLAND_DISPLAY=v[0]['wl_socket'])
                    self.env.pop('DISPLAY', None)
                    return
            time.sleep(0.2)
        raise RuntimeError('nested compositor did not start')

    def ctl(self, *a, timeout=20):
        return sh(['hyprctl', *a], env=self.env, timeout=timeout)

    def eval(self, lua, timeout=20):
        return self.ctl('eval', lua, timeout=timeout)

    def clients(self):
        try:
            return json.loads(self.ctl('-j', 'clients').stdout)
        except ValueError:
            return []

    def active(self):
        try:
            return json.loads(self.ctl('-j', 'activewindow').stdout)
        except ValueError:
            return {}

    def shot(self, name):
        p = OUT / f'{name}.png'
        r = sh(['grim', str(p)], env=self.env, timeout=30)
        return p if r.returncode == 0 else None

    def stop(self):
        if self.pid:
            try:
                os.kill(self.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        if self.proc:
            try:
                self.proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.proc.terminate()
                self.proc.wait(timeout=10)


def tmux(*a):
    return sh(['tmux', '-L', SOCK, *a], env=dict(os.environ, TMUX_TMPDIR=str(TMUXDIR)), timeout=10)


def screen():
    return tmux('capture-pane', '-p', '-t', 'c').stdout


def wait_screen(want, timeout=15.0):
    end = time.monotonic() + timeout
    s = ''
    while time.monotonic() < end:
        s = screen()
        if (want(s) if callable(want) else want in s):
            return True, s
        time.sleep(0.1)
    return False, s


def main():
    TMUXDIR.mkdir(mode=0o700, exist_ok=True)
    if SCRATCH.exists():
        SCRATCH.unlink()
    user_before = sha(USER_TIER)
    ok, d = primary_ok()
    if not check('preflight primary compositor + portal', ok, d):
        return 1
    tmux('kill-server')
    env_app = f'env MIOS_USER_TOML={SCRATCH} TERM=xterm-256color {BIN} --root /; echo EXITED:$?; sleep 600'
    r = tmux('-f', '/dev/null', 'new-session', '-d', '-s', 'c', '-x', '170', '-y', '46', env_app)
    check('private tmux server for the app', r.returncode == 0, r.stderr.strip())
    n = Nested()
    try:
        n.start()
        check('nested PiP session started', True, f'dir={n.dir.name} nested_pid={n.pid}')
        attach = f"env TMUX_TMPDIR={TMUXDIR} tmux -L {SOCK} attach -t c"
        n.eval(f"hl.dispatch(hl.dsp.exec_cmd({json.dumps('alacritty --class mios-controls-live -o font.size=9 -e ' + attach)}))")
        win = None
        for _ in range(100):
            win = next((c for c in n.clients() if c.get('class') == 'mios-controls-live'), None)
            if win:
                break
            time.sleep(0.2)
        if not check('app terminal mapped in nested session', win, ''):
            return 1
        addr = win['address']
        n.eval(f"hl.dispatch(hl.dsp.focus({{window='address:{addr}'}}))")

        def key(k, mods=''):
            for st in ('down', 'up'):
                n.eval(f"hl.dispatch(hl.dsp.send_key_state({{mods='{mods}',key='{k}',state='{st}',window='address:{addr}'}}))")
            time.sleep(0.08)

        def text(s):
            tmux('send-keys', '-t', 'c', '-l', s)

        ok, s = wait_screen('MiOS Controls', 20)
        check('2D: TUI renders in the nested terminal', ok)
        ok, s = wait_screen(lambda s: 'valid:' in s, 60)
        check('2D: background validation reports the saved map valid', ok, next((l.strip() for l in s.splitlines() if 'valid' in l), ''))
        n.shot('01-2d-start')

        # ---- 2D functions through the compositor key path ----
        key('bracketright')
        check('2D: context selector (compositor key ])', wait_screen('[desktop-2d]')[0])
        key('Tab')  # Bindings -> Peripherals
        key('Down')
        key('Return')
        ok, s = wait_screen(' Controller ')
        check('2D: peripheral selector -> controller diagram', ok)
        for _ in range(11):  # walk to BTN_NORTH
            key('Right')
        ok, s = wait_screen('Hold L3+R3')
        check('2D: North shows take/return-control timing', ok)
        key('Left')  # Guide button
        key('Left')
        ok, s = wait_screen('Guide (BTN_MODE)')
        check('2D: Guide button shows its system-layer role', ok and 'hold: system layer' in s)
        n.shot('02-2d-controller')
        for _ in range(3):
            key('Right')  # -> West
        key('Return')
        ok, _ = wait_screen('Guide + BTN_WEST action')
        check('2D: Enter opens the Guide-action editor', ok)
        tmux('send-keys', '-t', 'c', 'C-u')
        text('exec:foot')
        key('Return')
        ok, s = wait_screen('Guide + BTN_WEST = exec:foot')
        check('2D: Guide+West edited in the draft', ok and 'DRAFT' in s)
        ok, s = wait_screen(lambda s: 'valid:' in s and 'validating' not in s, 60)
        check('2D: draft revalidated clean', ok)
        key('Tab')  # -> Bindings
        key('Home')
        key('Down')  # terminal binding
        key('c')
        tmux('send-keys', '-t', 'c', 'C-u')
        text('SUPER+c')
        key('Return')
        ok, s = wait_screen('new finding', 60)
        check('2D: colliding chord flagged (negative control)', ok and '!' in s)
        n.shot('03-2d-collision')
        key('w')
        ok, s = wait_screen('apply blocked')
        check('2D: apply blocked while the draft adds a finding', ok and not SCRATCH.exists())
        key('c')
        tmux('send-keys', '-t', 'c', 'C-u')
        text('SUPER+F8')
        key('Return')
        ok, s = wait_screen(lambda s: 'valid:' in s and 'validating' not in s, 60)
        check('2D: free chord validates (positive control)', ok)
        key('w')
        ok, _ = wait_screen('Write the draft to')
        check('2D: apply asks for confirmation', ok)
        key('y')
        ok, s = wait_screen('applied to', 30)
        data = SCRATCH.read_text() if SCRATCH.exists() else ''
        check('2D: apply wrote the scratch user tier only', ok and 'SUPER+F8' in data and 'exec:foot' in data and (SCRATCH.stat().st_mode & 0o777) == 0o600)
        n.shot('04-2d-applied')

        # ---- transition 2D -> 3D ----
        r = sh(['/usr/bin/mios-gen', 'hyprland-3d-toggle'], env=n.env, timeout=60)
        probe = n.eval("if not (hl.plugin.hypr3d and hl.plugin.hypr3d.active()) then error('room inactive',0) end").stdout.strip()
        check('transition: nested session entered Hypr3D', r.returncode == 0 and probe == 'ok', f'rc={r.returncode} probe={probe}')
        time.sleep(1.5)
        n.shot('05-3d-room')
        aw = n.active().get('address')
        check('3D: app terminal kept focus across the transition', aw == addr, f'active={aw}')
        n.eval(f"hl.dispatch(hl.dsp.focus({{window='address:{addr}'}}))")
        key('slash', 'SHIFT')
        ok, s = wait_screen('MOVING AROUND')
        check('3D: compositor keys still reach the TUI (help)', ok)
        n.shot('06-3d-help')
        key('Escape')
        key('r', 'SHIFT')
        ok, _ = wait_screen('vendor/host defaults? (y/n)')
        check('3D: restore-defaults confirmation', ok)
        key('y')
        wait_screen('vendor/host defaults (apply', 10)
        ok, s = wait_screen(lambda s: 'valid:' in s and 'validating' not in s, 60)
        for _ in range(4):
            key('w')
            if wait_screen('Write the draft to', 4)[0]:
                break
            time.sleep(1)
        key('y')
        end = time.monotonic() + 30
        data = ''
        while time.monotonic() < end:
            data = SCRATCH.read_text() if SCRATCH.exists() else ''
            if 'keybindings' not in data:
                break
            time.sleep(0.2)
        check('3D: restore + apply removed the overrides', 'keybindings' not in data, screen().splitlines()[-3:] if 'keybindings' in data else '')
        # a second window opened in the room (spawn path) while the TUI keeps working
        n.eval("hl.dispatch(hl.dsp.exec_cmd('alacritty --class mios-controls-live-2 -e sleep 120'))")
        second = None
        for _ in range(60):
            second = next((c for c in n.clients() if c.get('class') == 'mios-controls-live-2'), None)
            if second:
                break
            time.sleep(0.2)
        check('3D: a new window opens in the room', second)
        time.sleep(1.0)
        n.shot('07-3d-two-windows')
        n.eval(f"hl.dispatch(hl.dsp.focus({{window='address:{addr}'}}))")
        key('bracketright')
        ok, _ = wait_screen('[desktop-3d]')
        check('3D: TUI responds after focus returns', ok)

        # ---- transition 3D -> 2D, then a resize transition ----
        r = sh(['/usr/bin/mios-gen', 'hyprland-3d-toggle'], env=n.env, timeout=60)
        probe = n.eval("if hl.plugin.hypr3d and hl.plugin.hypr3d.active() then error('room still active',0) end").stdout.strip()
        check('transition: nested session back to 2D', r.returncode == 0 and probe == 'ok', f'rc={r.returncode} probe={probe}')
        time.sleep(1.0)
        n.shot('08-2d-back')
        n.eval(f"hl.dispatch(hl.dsp.focus({{window='address:{addr}'}}))")
        size_tiled = tmux('display-message', '-p', '-t', 'c', '#{window_width}x#{window_height}').stdout.strip()
        n.eval("hl.dispatch(hl.dsp.window.fullscreen({['mode']='fullscreen',['action']='toggle'}))")
        time.sleep(1.0)
        size_full = tmux('display-message', '-p', '-t', 'c', '#{window_width}x#{window_height}').stdout.strip()
        key('Tab')
        ok, s = wait_screen('MiOS Controls')
        check('transition: fullscreen resize re-lays out the TUI', ok and size_full != size_tiled, f'{size_tiled} -> {size_full}')
        n.shot('09-2d-fullscreen')
        n.eval("hl.dispatch(hl.dsp.window.fullscreen({['mode']='fullscreen',['action']='toggle'}))")
        time.sleep(0.8)
        key('q')
        if wait_screen('discard the unsaved draft', 3)[0]:
            key('y')
        ok, _ = wait_screen('EXITED:0')
        check('quit cleanly (draft saved, no prompt)', ok)
        n.shot('10-2d-exit')
    except Exception as e:
        check('driver', False, repr(e))
    finally:
        n.stop()
        tmux('kill-server')
        ok, d = primary_ok()
        check('postflight primary compositor + portal unchanged', ok, d)
        check('operator user tier untouched', sha(USER_TIER) == user_before)
        check('nested runtime cleaned up', n.dir is None or not n.dir.exists(), str(n.dir))
    passed = sum(1 for r in results if r[1])
    (OUT / 'results.json').write_text(json.dumps([{'name': a, 'pass': b, 'detail': c} for a, b, c in results], indent=1))
    log(f'{passed}/{len(results)} passed')
    return 0 if passed == len(results) else 1


if __name__ == '__main__':
    if os.environ.get('MIOS_LIVE_NESTED') != '1' or not os.environ.get('HYPRLAND_INSTANCE_SIGNATURE'):
        print('skip: live nested-session test (set MIOS_LIVE_NESTED=1 inside a MiOS desktop session)')
        sys.exit(0)
    sys.exit(main())
