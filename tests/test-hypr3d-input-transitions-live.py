#!/usr/bin/env python3
# AI-hint: LIVE opt-in test (MIOS_LIVE_NESTED=1) of 2D<->3D keyboard input transitions in an owned nested MiOS PiP session: focus kept across mios-gen hyprland-3d-toggle, real key events (primary -> our PiP window -> nested compositor -> Hypr3D key hook -> focused window), room key ownership, the Super+Left Alt keyboard mode, the input:window capture contract, a window opened in the room, repeated cycles, and leaving the room with NO aim focus (rapid 0.4/0.15 s toggles, an immediate re-entry during the exit, the crosshair backed off every window, the 2D window closed in the room): a focused window that receives keys must always come back.
# AI-related: usr/share/mios/hyprland/patches/hypr3d-mios-input-transitions.patch, tools/native/mios-gen/src/render.rs (hyprland-3d-toggle), tools/native/mios-inputd/src/runtime.rs (input:window), usr/share/mios/hyprland/patches/hypr3d-mios-pip-theatre.patch (input_capture/input_owner), tools/native/mios-gen/src/nested_session.rs, tests/test-mios-controls-live.py
# Primary compositor: only the stock launcher's PiP adoption and send_key_state to OUR nested window. Everything else is
# dispatched to the nested compositor. The operator user tier is hash-checked before/after; nothing is written to it.
import hashlib, json, os, signal, subprocess, sys, tempfile, time
from pathlib import Path

OUT = Path(os.environ.get('MIOS_LIVE_OUT') or tempfile.mkdtemp(prefix='mios-hypr3d-input-'))
GEN = os.environ.get('MIOS_GEN', '/usr/bin/mios-gen')
INPUTD = os.environ.get('MIOS_INPUTD', '/usr/libexec/mios/mios-inputd')
SSOT = os.environ.get('MIOS_VENDOR_TOML') or str(Path(__file__).resolve().parents[1] / 'usr/share/mios/mios.toml')
USER_TIER = Path.home() / '.config/mios/mios.toml'
PRIMARY_PID = int(os.environ.get('PRIMARY_PID') or subprocess.run(['pgrep', '-x', 'Hyprland', '-o'], capture_output=True, text=True).stdout.strip() or 0)
CYCLES = int(os.environ.get('MIOS_TRANSITION_CYCLES', '3'))
REC = OUT / 'keys.txt'
SEARCH_LAYER = os.environ.get('MIOS_SEARCH_LAYER', 'mios-search')  # the Super-tap (SUPER + Super_L release) search surface
observed = []
results = []


def check(name, ok, detail=''):
    results.append((name, bool(ok), str(detail)))
    print(f"{'PASS' if ok else 'FAIL'} {name} {detail}", flush=True)
    return ok


def sh(args, env=None, timeout=30):
    return subprocess.run(args, capture_output=True, text=True, env=env, timeout=timeout)


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else 'absent'


def primary():
    pid = sh(['pgrep', '-x', 'Hyprland', '-o']).stdout.strip()
    return pid, open('/proc/self/mountinfo').read().count('fuse.portal')


class Nested:
    def __init__(self):
        self.proc = self.dir = self.env = self.pid = self.parent_window = None

    def start(self):
        env = dict(os.environ, MIOS_VENDOR_TOML=SSOT)
        self.proc = subprocess.Popen([GEN, 'nested-session', '--layout', 'pip'], env=env,
                                     stdout=open(OUT / 'launcher.log', 'w'), stderr=subprocess.STDOUT)
        self.dir = Path(os.environ['XDG_RUNTIME_DIR']) / f'mios-nested-desktop-{self.proc.pid}'
        end = time.monotonic() + 60
        while time.monotonic() < end:
            if self.proc.poll() is not None:
                raise RuntimeError(f'launcher exited: {(OUT / "launcher.log").read_text()[-600:]}')
            if (self.dir / 'session.json').is_file():
                d = json.loads((self.dir / 'session.json').read_text())
                inst = d['instance']
                self.pid, self.parent_window = inst['pid'], d['parent_window']
                self.env = dict(os.environ, XDG_RUNTIME_DIR=str(self.dir), HYPRLAND_INSTANCE_SIGNATURE=inst['instance'],
                                WAYLAND_DISPLAY=inst['wl_socket'], MIOS_VENDOR_TOML=SSOT)
                self.env.pop('DISPLAY', None)
                return
            time.sleep(0.2)
        raise RuntimeError('nested session did not publish session.json')

    def eval(self, lua):
        return sh(['hyprctl', 'eval', lua], env=self.env).stdout.strip()

    def clients(self):
        try:
            return json.loads(sh(['hyprctl', '-j', 'clients'], env=self.env).stdout)
        except ValueError:
            return []

    def active(self):
        try:
            return json.loads(sh(['hyprctl', '-j', 'activewindow'], env=self.env).stdout).get('address')
        except ValueError:
            return None

    def room(self):
        return self.eval("if not (hl.plugin.hypr3d and hl.plugin.hypr3d.active()) then error('flat',0) end") == 'ok'

    def owner(self):
        r = self.eval("local p=hl.plugin.hypr3d; if not (p and p.input_owner) then error('no input_owner',0) end; error('OWNER:'..string.format('0x%x', p.input_owner()),0)")
        return r.split('OWNER:', 1)[1].strip() if 'OWNER:' in r else r

    def focus(self, addr):
        self.eval(f"hl.dispatch(hl.dsp.focus({{window='address:{addr}'}}))")

    def layers(self):
        try:
            lj = json.loads(sh(['hyprctl', '-j', 'layers'], env=self.env).stdout)
            return {l['namespace'] for mon in lj.values() for lvl in mon['levels'].values() for l in lvl}
        except (ValueError, KeyError, AttributeError):
            return set()

    def search_open(self):
        return SEARCH_LAYER in self.layers()

    def dismiss_search(self, window):
        """Close the Super-tap search if it holds the keyboard (Escape goes to it), so later keys reach windows."""
        if self.search_open():
            tap(window, 'Escape')
            settle(lambda: not self.search_open(), 4)

    def toggle(self):
        return sh([GEN, 'hyprland-3d-toggle'], env=self.env, timeout=60)

    def shot(self, name):
        sh(['grim', str(OUT / f'{name}.png')], env=self.env)

    def wait_window(self, cls, timeout=20):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            w = next((c for c in self.clients() if c.get('class') == cls), None)
            if w:
                return w
            time.sleep(0.2)
        return None

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
                self.proc.kill()


def sk(window, mods, key, state):
    """One key event, sent by the primary compositor to OUR nested PiP window only (the real keyboard path)."""
    sh(['hyprctl', 'eval', f"hl.dispatch(hl.dsp.send_key_state({{mods='{mods}',key='{key}',state='{state}',window='address:{window}'}}))"])


def tap(window, key):
    sk(window, '', key, 'down')
    sk(window, '', key, 'up')
    time.sleep(0.15)


def chord(window, key, mod_key='Super_L', mod='SUPER'):
    sk(window, '', mod_key, 'down')
    sk(window, mod, key, 'down')
    sk(window, mod, key, 'up')
    sk(window, '', mod_key, 'up')
    time.sleep(0.3)


def recorded():
    return REC.read_text(errors='replace') if REC.exists() else ''


def typed(window, key, want, timeout=3.0):
    """Tap `key`; True when the recorder received exactly `want` more characters."""
    before = recorded()
    tap(window, key)
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        now = recorded()
        if now != before and now.startswith(before):
            return now[len(before):] == want, now[len(before):]
        time.sleep(0.05)
    return want == '', ''


def observe(name, detail):
    """A recorded fact that is not part of the pass/fail contract (reported in results.json)."""
    observed.append((name, str(detail)))
    print(f'OBSERVE {name} {detail}', flush=True)


def settle(pred, timeout=6.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.15)
    return pred()


def exited_with_keys(n, pw, addr, tag, in_room):
    """After a room exit: the recorder is focused and a typed key reaches it."""
    check(f'{tag}: 3D -> 2D', settle(lambda: not n.room(), 15))
    time.sleep(1.0)
    act = n.active()
    check(f'{tag}: a focused window comes back after the exit', act == addr, f'in_room={in_room} after={act} want={addr}')
    ok, got = typed(pw, 'k', 'k')
    check(f'{tag}: keys reach it after the exit', ok, repr(got))
    n.dismiss_search(pw)
    n.focus(addr)
    settle(lambda: n.active() == addr, 4)


def no_aim_exits(n, pw, addr):
    """Room exits where the room has no aim focus: the 2D keyboard owner (or the newest still-shown window) gets it back."""
    for dwell in (0.4, 0.15):
        n.focus(addr)
        settle(lambda: n.active() == addr, 4)
        n.toggle()
        check(f'rapid toggle {dwell}s: 2D -> 3D', settle(n.room, 10))
        time.sleep(dwell)
        in_room = n.active()
        n.toggle()
        exited_with_keys(n, pw, addr, f'rapid toggle {dwell}s', in_room)
    # Re-entry while the exit is still finishing (the deferred teardown is skipped), then out again quickly.
    n.toggle()
    settle(n.room, 10)
    time.sleep(1.0)
    n.toggle()
    n.toggle()
    check('re-entry during the exit: back in the room', settle(n.room, 10))
    time.sleep(0.3)
    in_room = n.active()
    n.toggle()
    exited_with_keys(n, pw, addr, 're-entry during the exit', in_room)
    # Back off every window: the crosshair rests on the floor, so the room has no focus at exit.
    n.toggle()
    settle(n.room, 10)
    time.sleep(1.5)
    sk(pw, '', 's', 'down')
    time.sleep(2.5)
    sk(pw, '', 's', 'up')
    time.sleep(1.0)
    in_room = n.active()
    observe('backed off: focus in the room', in_room)
    n.shot('06-3d-backed-off')
    n.toggle()
    exited_with_keys(n, pw, addr, 'backed off (no aim focus)', in_room)
    # The 2D keyboard owner is closed in the room and nothing is aimed: the newest still-shown window takes it.
    n.eval("hl.dispatch(hl.dsp.exec_cmd('alacritty --class mios-input-gone -e sleep 300'))")
    gone = n.wait_window('mios-input-gone')
    if check('closed 2D owner: a second window mapped', gone):
        n.focus(gone['address'])
        check('closed 2D owner: it has the keyboard in 2D', settle(lambda: n.active() == gone['address'], 5), f'active={n.active()}')
        n.toggle()
        settle(n.room, 10)
        time.sleep(1.0)
        try:
            os.kill(gone['pid'], signal.SIGTERM)  # our own terminal in our own nested session
        except ProcessLookupError:
            pass
        settle(lambda: not any(c.get('address') == gone['address'] for c in n.clients()), 6)
        sk(pw, '', 's', 'down')
        time.sleep(1.5)
        sk(pw, '', 's', 'up')
        time.sleep(1.0)
        in_room = n.active()
        n.toggle()
        exited_with_keys(n, pw, addr, 'closed 2D owner (no aim focus)', in_room)


def main():
    user_before = sha(USER_TIER)
    pid, portal = primary()
    # The portal mount count depends on the caller's mount namespace (0 or 1); it must only stay unchanged.
    if not check('preflight primary compositor (portal recorded)', pid == str(PRIMARY_PID) and portal in (0, 1), f'pid={pid} portal={portal}'):
        return 1
    n = Nested()
    try:
        n.start()
        check('nested PiP session started', True, f'{n.dir.name} nested_pid={n.pid} gen={GEN}')
        recorder = f"stty -icanon -echo min 1; exec dd bs=1 of={REC} status=none"
        n.eval(f"hl.dispatch(hl.dsp.exec_cmd({json.dumps('alacritty --class mios-input-rec -e sh -c ' + json.dumps(recorder))}))")
        win = n.wait_window('mios-input-rec')
        if not check('recorder terminal mapped in the nested session', win):
            return 1
        addr = win['address']
        n.focus(addr)
        settle(lambda: n.active() == addr and REC.exists(), 10)
        pw = n.parent_window

        # ---- 2D baseline ----
        ok, got = typed(pw, 'k', 'k')
        check('2D: a key reaches the focused window', ok, repr(got))
        ok, got = typed(pw, 'w', 'w')
        check('2D: a movement key (w) is plain text in 2D', ok, repr(got))
        n.shot('01-2d')
        chord(pw, 'Alt_L')
        observe('2D control: Super+Left Alt opened the Super-tap search', n.search_open())
        n.dismiss_search(pw)
        n.focus(addr)

        second = None
        for cycle in range(1, CYCLES + 1):
            tag = f'cycle {cycle}'
            r = n.toggle()
            check(f'{tag}: 2D -> 3D (hyprland-3d-toggle)', r.returncode == 0 and settle(n.room), f'rc={r.returncode} {r.stderr.strip()[-200:]}')
            time.sleep(1.2)
            check(f'{tag}: focus kept across 2D -> 3D', n.active() == addr, f'active={n.active()} want={addr}')
            ok, got = typed(pw, 'k', 'k')
            check(f'{tag}: 3D room: a non-movement key reaches the focused window', ok, repr(got))
            ok, got = typed(pw, 'w', '')
            check(f'{tag}: 3D room: a movement key (w) belongs to the room (negative control)', ok, repr(got))
            if cycle == 1:
                n.shot('02-3d-room')
                # The mapped plugin file of the nested compositor (hyprctl lists names only).
                try:
                    maps = Path(f'/proc/{n.pid}/maps').read_text()
                except OSError as e:
                    maps = str(e)
                h3d = sorted({l.split(None, 5)[-1] for l in maps.splitlines() if 'hypr3d' in l and '/' in l})
                want = os.environ.get('MIOS_EXPECT_HYPR3D')
                if want:
                    check('3D: the expected Hypr3D build is the loaded one', any(want in x for x in h3d), h3d)
                else:
                    observe('3D: loaded Hypr3D', h3d)
                # Keyboard mode chord: Super + Left Alt -> Window mode, keys belong to the window. In window mode the
                # chord's Super release can also open the Super-tap search (it does not in 2D): observed, then
                # dismissed so the window keeps the keyboard.
                chord(pw, 'Alt_L')
                observe('3D: Super+Left Alt also opened the Super-tap search (known; not part of this contract)', settle(n.search_open, 2.0))
                n.dismiss_search(pw)
                n.focus(addr)
                ok, got = typed(pw, 'w', 'w')
                check('3D: Super+Left Alt (window mode) gives movement keys to the window', ok, repr(got))
                chord(pw, 'Alt_L')
                n.dismiss_search(pw)
                n.focus(addr)
                ok, got = typed(pw, 'w', '')
                check('3D: Super+Left Alt again returns the keys to the room', ok, repr(got))
                # Input ownership contract (mios-inputd input:window, the North tap): capture, then release.
                r = sh([INPUTD, 'dispatch', 'input:window on'], env=n.env)
                own = n.owner()
                check('3D: input:window on captures the focused room window', r.returncode == 0 and own == addr,
                      f'rc={r.returncode} owner={own} want={addr} {r.stderr.strip()[-200:]}')
                ok, got = typed(pw, 'w', 'w')
                check('3D: a captured window receives every key', ok, repr(got))
                r = sh([INPUTD, 'dispatch', 'input:window off'], env=n.env)
                own = n.owner()
                check('3D: input:window off releases to the room', r.returncode == 0 and own == '0x0', f'rc={r.returncode} owner={own}')
                ok, got = typed(pw, 'w', '')
                check('3D: after release a movement key belongs to the room again', ok, repr(got))
                # A window opened in the room takes focus; focus can return to the recorder and keys follow it.
                n.eval("hl.dispatch(hl.dsp.exec_cmd('alacritty --class mios-input-second -e sleep 300'))")
                second = n.wait_window('mios-input-second')
                check('3D: a new window opens in the room', second)
                if second:
                    check('3D: the new window takes focus', settle(lambda: n.active() == second['address'], 5), f'active={n.active()}')
                time.sleep(0.8)
                n.shot('03-3d-two-windows')
                n.focus(addr)
                settle(lambda: n.active() == addr, 4)
                ok, got = typed(pw, 'k', 'k')
                check('3D: keys follow focus back to the recorder', ok, repr(got))
            r = n.toggle()
            check(f'{tag}: 3D -> 2D (hyprland-3d-toggle)', r.returncode == 0 and settle(lambda: not n.room()), f'rc={r.returncode} {r.stderr.strip()[-200:]}')
            time.sleep(1.0)
            check(f'{tag}: focus kept across 3D -> 2D', n.active() == addr, f'active={n.active()} want={addr}')
            ok, got = typed(pw, 'w', 'w')
            check(f'{tag}: 2D again: movement key is plain text (room released the keyboard)', ok, repr(got))
            if cycle == 1 and second:
                # In the room focus follows the crosshair (by design): the floating window opened there sits in
                # front of the recorder, so later cycles run without it.
                try:
                    os.kill(second['pid'], signal.SIGTERM)  # our own terminal in our own nested session
                except ProcessLookupError:
                    pass
                settle(lambda: not any(c.get('address') == second['address'] for c in n.clients()), 6)
                n.focus(addr)
                settle(lambda: n.active() == addr, 4)

        # ---- the real keybind: Super+F12 through the nested projection ----
        chord(pw, 'F12')
        check('Super+F12 chord: nested bind enters 3D', settle(n.room, 15))
        check('Super+F12 chord: did not also open the Super-tap search', not n.search_open())
        n.dismiss_search(pw)
        time.sleep(1.2)
        check('Super+F12 chord: focus kept', n.active() == addr, f'active={n.active()}')
        ok, got = typed(pw, 'k', 'k')
        check('Super+F12 chord: keys still reach the window in the room', ok, repr(got))
        n.shot('04-3d-keybind')
        chord(pw, 'F12')
        check('Super+F12 chord: nested bind returns to 2D', settle(lambda: not n.room(), 15))
        check('Super+F12 chord (exit): did not also open the Super-tap search', not n.search_open())
        n.dismiss_search(pw)
        time.sleep(1.0)
        ok, got = typed(pw, 'w', 'w')
        check('Super+F12 chord: 2D keys reach the window', ok, repr(got))
        n.shot('05-2d-final')

        no_aim_exits(n, pw, addr)
    except Exception as e:
        check('driver', False, repr(e))
    finally:
        n.stop()
        check('nested runtime cleaned up', n.dir is None or settle(lambda: not n.dir.exists(), 15), n.dir)
    pid2, portal2 = primary()
    check('postflight primary unchanged (pid, portal)', (pid2, portal2) == (pid, portal), f'{pid2} {portal2}')
    check('operator user tier untouched', sha(USER_TIER) == user_before)
    passed = sum(1 for r in results if r[1])
    (OUT / 'results.json').write_text(json.dumps({'checks': [{'name': a, 'pass': b, 'detail': c} for a, b, c in results],
                                                  'observed': [{'name': a, 'detail': d} for a, d in observed]}, indent=1))
    print(f'{passed}/{len(results)} passed  (out: {OUT})')
    return 0 if passed == len(results) else 1


if __name__ == '__main__':
    if os.environ.get('MIOS_LIVE_NESTED') != '1' or not os.environ.get('HYPRLAND_INSTANCE_SIGNATURE'):
        print('skip: live nested-session test (set MIOS_LIVE_NESTED=1 inside a MiOS desktop session)')
        sys.exit(0)
    sys.exit(main())
