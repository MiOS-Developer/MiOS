#!/usr/bin/env python3
# AI-hint: LIVE opt-in test (MIOS_LIVE_NESTED=1) of the G1/U1 generator outputs in owned nested MiOS PiP sessions, candidate launcher vs the installed one (negative control): bypass_mod escapes act over a captured fullscreen app (session scale), Super+C stays with the app, exactly one Print handle plus the Super+Ctrl+Print escape, literal workspace binds, the capture predicate and the virtual-keyboard layout rule; the MiOS tmux menu opens and acts in a private tmux server inside the session, by real prefix keys and by the controller's client-exact route (@mios-menu-keys). Real key events; primary untouched.
# AI-related: tools/native/mios-gen/src/global_bindings.rs, tools/native/mios-gen/src/render.rs, tools/native/mios-unit-gen/src/lib.rs, usr/share/mios/mios.toml [keybindings.global] [keybindings.tmux], tests/test-global-bindings-s0-live.py
# Env: MIOS_GEN = candidate mios-gen (the launcher whose projection is under test), MIOS_GEN_OLD = negative control
# launcher (default /usr/bin/mios-gen), MIOS_VENDOR_TOML = SSOT (default: this tree's), MIOS_TMUX_CONF = generated
# tmux conf (default: this tree's), MIOS_LIVE_OUT = evidence directory. The primary compositor only receives
# send_key_state events addressed to OUR nested window; each nested Hyprland is SIGTERMed at the end.
import json, os, re, signal, subprocess, sys, tempfile, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAND = os.environ.get('MIOS_GEN', '/usr/bin/mios-gen')
OLD = os.environ.get('MIOS_GEN_OLD', '/usr/bin/mios-gen')
SSOT = os.environ.get('MIOS_VENDOR_TOML', str(ROOT / 'usr/share/mios/mios.toml'))
CONF = Path(os.environ.get('MIOS_TMUX_CONF', ROOT / 'usr/share/mios/tmux/mios-keys.tmux.conf'))
OUT = Path(os.environ.get('MIOS_LIVE_OUT') or tempfile.mkdtemp(prefix='mios-g1-live-'))
results = []


def check(name, ok, detail=''):
    results.append((name, bool(ok), str(detail)))
    print(f"{'PASS' if ok else 'FAIL'} {name} {detail}", flush=True)
    return ok


def sh(args, env=None, timeout=30):
    return subprocess.run(args, capture_output=True, text=True, env=env, timeout=timeout)


def primary():
    mons = json.loads(sh(['hyprctl', '-j', 'monitors']).stdout)
    # The primary's own document-portal mounts; nested sessions (ours and other lanes') mount theirs under their runtime.
    portal = sum(1 for l in open('/proc/self/mountinfo') if 'fuse.portal' in l and 'mios-nested-desktop-' not in l)
    return sh(['pgrep', '-x', 'Hyprland', '-o']).stdout.strip(), portal, {m['name']: m['scale'] for m in mons}


class Nested:
    def __init__(self, launcher, tag):
        self.launcher, self.tag = launcher, tag
        self.proc = self.dir = self.env = self.pid = self.parent = None

    def start(self):
        env = dict(os.environ, MIOS_VENDOR_TOML=SSOT)
        env.pop('MIOS_TOML', None)
        self.proc = subprocess.Popen([self.launcher, 'nested-session', '--layout', 'pip'], env=env, stdout=open(OUT / f'launcher-{self.tag}.log', 'w'), stderr=subprocess.STDOUT)
        self.dir = Path(os.environ['XDG_RUNTIME_DIR']) / f'mios-nested-desktop-{self.proc.pid}'
        end = time.monotonic() + 60
        while time.monotonic() < end:
            if self.proc.poll() is not None:
                raise RuntimeError(f'launcher exited: {(OUT / f"launcher-{self.tag}.log").read_text()[-600:]}')
            if (self.dir / 'session.json').is_file():
                d = json.loads((self.dir / 'session.json').read_text())
                self.pid, self.parent = d['instance']['pid'], d['parent_window']
                self.env = dict(env, XDG_RUNTIME_DIR=str(self.dir), HYPRLAND_INSTANCE_SIGNATURE=d['instance']['instance'], WAYLAND_DISPLAY=d['instance']['wl_socket'])
                self.env.pop('DISPLAY', None)
                return
            time.sleep(0.2)
        raise RuntimeError('nested session did not publish session.json')

    def ctl(self, *a):
        return sh(['hyprctl', *a], env=self.env)

    def ev(self, code):
        """Evaluate Lua in the nested compositor; a value comes back through error(v, 0) ("error: <v>")."""
        out = self.ctl('eval', code).stdout.strip()
        return out[len('error: '):] if out.startswith('error: ') else out

    def clients(self):
        try:
            return json.loads(self.ctl('-j', 'clients').stdout or '[]')
        except ValueError:
            return []

    def client(self, cls):
        return next((c for c in self.clients() if c.get('class') == cls), None)

    def scale(self):
        return json.loads(self.ctl('-j', 'monitors').stdout)[0]['scale']

    def wait(self, pred, timeout=8.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            v = pred()
            if v:
                return v
            time.sleep(0.15)
        return pred()

    def key(self, mods, key):
        """A chord as a keyboard produces it: modifier keys held as separate events, then the key, to OUR window."""
        def sk(m, k, st):
            sh(['hyprctl', 'eval', f"hl.dispatch(hl.dsp.send_key_state({{mods='{m}',key='{k}',state='{st}',window='address:{self.parent}'}}))"])
        held = []
        if 'SUPER' in mods:
            held.append(('', 'Super_L', 'SUPER'))
        if 'CTRL' in mods:
            held.append(('SUPER' if 'SUPER' in mods else '', 'Control_L', 'SUPER CTRL' if 'SUPER' in mods else 'CTRL'))
        if mods == 'SHIFT':
            held.append(('', 'Shift_L', 'SHIFT'))
        for m, k, _ in held:
            sk(m, k, 'down')
        sk(mods, key, 'down'); sk(mods, key, 'up')
        for m, k, after in reversed(held):
            sk(after, k, 'up')
        time.sleep(0.5)

    def shot(self, name):
        sh(['grim', str(OUT / f'{self.tag}-{name}.png')], env=self.env)

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


def shots_in(runtime):
    """mios-shot processes started by THIS nested session (their XDG_RUNTIME_DIR is its runtime)."""
    out = []
    for p in Path('/proc').iterdir():
        if not p.name.isdigit():
            continue
        try:
            if b'mios-shot' not in (p / 'cmdline').read_bytes():
                continue
            if f'XDG_RUNTIME_DIR={runtime}'.encode() in (p / 'environ').read_bytes().split(b'\0'):
                out.append(int(p.name))
        except OSError:
            pass
    return out


def cancel_shot(n, pids):
    """Cancel a running region screenshot the way a user does (Escape to the selector), so the shell's selector
    releases the keyboard; SIGTERM only what is still alive afterwards."""
    if not pids:
        return
    n.key('', 'Escape')
    alive = n.wait(lambda: not [p for p in pids if Path(f'/proc/{p}').exists()] or None, 4)
    for p in pids:
        try:
            os.kill(p, signal.SIGTERM)
        except ProcessLookupError:
            pass
    check(f'[{n.tag}] screenshot cancelled by Escape (selector released the keyboard)', alive, pids)


def binds_of(n):
    try:
        return json.loads(n.ctl('-j', 'binds').stdout or '[]')
    except ValueError:
        return []


def session(launcher, tag, cand):
    """Run the G1 checks in one nested session. cand=True asserts the G1 behavior; cand=False records the outcomes
    the negative control must show (returned as a dict)."""
    seen = {}
    rec = (lambda name, ok, detail='': check(f'[{tag}] {name}', ok, detail)) if cand else (lambda name, ok, detail='': seen.__setitem__(name, bool(ok)) or print(f'[{tag}] {"yes" if ok else "no "} {name} {detail}', flush=True))
    n = Nested(launcher, tag)
    try:
        n.start()
        check(f'[{tag}] nested session started', True, n.dir.name)
        lua = (n.dir / 'hyprland.lua').read_text()
        errs = n.ctl('configerrors').stdout.strip()
        rec('no config errors', not errs or 'no errors' in errs.lower(), errs[:200])
        b = binds_of(n)
        prints = [x for x in b if str(x.get('key', '')).lower() == 'print' and x.get('modmask') == 0]
        rec('exactly one Print handle (no double bind)', len(prints) == 1, len(prints))
        rec('SUPER+CTRL+Print escape bound', any(str(x.get('key', '')).lower() == 'print' and x.get('modmask') == 68 for x in b))
        ws = {(str(x.get('key')), x.get('modmask')) for x in b}
        rec('20 workspace binds present (SUPER/SUPER+SHIFT + 1..0)', all((str(k), m) in ws for k in [*range(1, 10), 0] for m in (64, 65)))
        rec('workspace binds emitted literally (gate-auditable)', len(re.findall(r'^hl\.bind\("SUPER \+ (?:SHIFT \+ )?\d"', lua, re.M)) == 20 and not re.search(r'\.\. key, hl\.dsp\.(?:focus|window\.move)', lua))
        rec('virtual keyboard pinned to the us layout', re.search(r'hl\.device\(\{name = "mios-vctl-keyboard", kb_layout = "us"\}\)', lua) is not None)
        check(f'[{tag}] compositor has the per-device API the rule uses', n.ev("if type(hl.device)~='function' then error('hl.device missing',0) end") == 'ok')
        log = (n.dir / 'desktop.log').read_text(errors='replace') if (n.dir / 'desktop.log').exists() else ''
        failed = [l for l in log.splitlines() if 'section failed' in l]
        check(f'[{tag}] no generated section failed at load', not failed, failed[:3])
        rec('one capture predicate exported', n.ev("if type(MIOS_INPUT_POLICY.captured)~='function' then error('missing',0) end") == 'ok')
        n.ctl('eval', "hl.dispatch(hl.dsp.exec_cmd('alacritty --class mios-g1-test -e sleep 300'))")
        w = n.wait(lambda: n.client('mios-g1-test'), 20)
        check(f'[{tag}] test window mapped', w)
        n.ctl('eval', f"hl.dispatch(hl.dsp.focus({{window='address:{w['address']}'}}))")
        time.sleep(0.4)
        n.key('SUPER CTRL', 'F11')
        fs = n.wait(lambda: (n.client('mios-g1-test') or {}).get('fullscreen') == 2, 5)
        check(f'[{tag}] test window fullscreen (captured)', fs, (n.client('mios-g1-test') or {}).get('fullscreen'))
        ctx = n.ev("error(MIOS_INPUT_POLICY.context(),0)")
        check(f'[{tag}] policy context is app-capture', ctx == 'app-capture', ctx)
        if cand:
            cap = n.ev(f"local w=hl.get_active_window();error(tostring(MIOS_INPUT_POLICY.captured(w)),0)")
            rec('captured(focused fullscreen window) == true', cap == 'true', cap)
        base = n.scale()
        n.shot('01-captured')
        n.key('SUPER CTRL', 'equal')
        up = n.wait(lambda: n.scale() > base + 1e-6, 5)
        rec('SUPER+CTRL+= zooms the session over a captured fullscreen app (bypass_mod escape)', up, f'{base} -> {n.scale()}')
        n.ev("mios_nested_zoom('reset')")
        n.wait(lambda: abs(n.scale() - base) < 1e-6, 4)
        n.key('SUPER', 'c')
        time.sleep(0.6)
        check(f'[{tag}] SUPER+C stays with the captured app (window not closed)', n.client('mios-g1-test') is not None)
        before = shots_in(n.dir)
        n.key('', 'Print')
        time.sleep(1.0)
        plain = [p for p in shots_in(n.dir) if p not in before]
        rec('plain Print stays with the captured app (no screenshot)', not plain, plain)
        cancel_shot(n, plain)
        n.key('SUPER CTRL', 'Print')
        got = n.wait(lambda: [p for p in shots_in(n.dir) if p not in before], 4)
        rec('SUPER+CTRL+Print starts the region screenshot over the captured app', bool(got), got)
        n.shot('02-shot')
        cancel_shot(n, got or [])
        n.ctl('eval', f"hl.dispatch(hl.dsp.focus({{window='address:{w['address']}'}}))")
        n.key('SUPER CTRL', 'F11')
        left = n.wait(lambda: (n.client('mios-g1-test') or {}).get('fullscreen') == 0, 5)
        check(f'[{tag}] left fullscreen', left)
        check(f'[{tag}] uncaptured context again', n.ev("error(MIOS_INPUT_POLICY.context(),0)") != 'app-capture')
        n.key('SUPER', '2')
        ws2 = n.wait(lambda: json.loads(n.ctl('-j', 'activeworkspace').stdout).get('id') == 2, 4)
        rec('SUPER+2 (literal workspace bind) switches workspace', ws2)
        n.key('SUPER', '1')
        n.wait(lambda: json.loads(n.ctl('-j', 'activeworkspace').stdout).get('id') == 1, 4)
        n.ctl('eval', f"hl.dispatch(hl.dsp.focus({{window='address:{w['address']}'}}))")
        n.key('SUPER', 'c')
        closed = n.wait(lambda: n.client('mios-g1-test') is None, 5)
        check(f'[{tag}] positive control: SUPER+C closes the uncaptured window', closed)
        if cand:
            tmux_menu(n)
    except Exception as e:
        check(f'[{tag}] driver', False, repr(e))
    finally:
        n.stop()
        check(f'[{tag}] nested runtime cleaned up', n.dir is None or not n.dir.exists(), n.dir)
    return seen


def tmux_menu(n):
    """U1 live: the generated conf in a PRIVATE tmux server inside the nested session; real keys pick the item."""
    tmpdir = OUT / 'tmux'
    tmpdir.mkdir(exist_ok=True)
    os.chmod(tmpdir, 0o700)
    sock = f'mios-u1-live-{os.getpid()}'
    tenv = dict(n.env, TMUX_TMPDIR=str(tmpdir))
    tenv.pop('TMUX', None); tenv.pop('TMUX_PANE', None)
    T = lambda *a: sh(['tmux', '-L', sock, *a], env=tenv)
    try:
        cmd = f"alacritty --class mios-u1-tmux -e env TMUX_TMPDIR={tmpdir} tmux -L {sock} -f {CONF} new-session -s u1"
        n.ctl('eval', f"hl.dispatch(hl.dsp.exec_cmd({json.dumps(cmd)}))")
        w = n.wait(lambda: n.client('mios-u1-tmux'), 20)
        client = n.wait(lambda: T('list-clients', '-F', '#{client_name}').stdout.strip(), 10)
        check('[u1] tmux client attached in the nested session', w and client, client)
        n.ctl('eval', f"hl.dispatch(hl.dsp.focus({{window='address:{w['address']}'}}))")
        T('split-window', '-t', 'u1')
        z = lambda: T('display', '-p', '-t', 'u1', '#{window_zoomed_flag}').stdout.strip()
        active = n.wait(lambda: json.loads(n.ctl('-j', 'activewindow').stdout or '{}').get('class') == 'mios-u1-tmux', 4)
        check('[u1] the tmux window has keyboard focus', active, n.ctl('-j', 'activewindow').stdout[:120])
        keys = T('show', '-sv', '@mios-menu-keys').stdout.split()
        check('[u1] the server publishes the client-exact menu route', keys == ['C-b', 'Space'], keys)
        # Keyboard path, real key events: prefix Space opens the MiOS menu, Shift+P (its accelerator) enters Pane >,
        # z zooms.
        z0 = z()
        n.key('CTRL', 'b'); n.key('', 'space')
        n.shot('03-menu')
        n.key('SHIFT', 'p'); n.key('', 'z')
        check('[u1] real keys: prefix Space -> Pane > Zoom acts', z0 == '0' and n.wait(lambda: z() == '1', 4), f'{z0} -> {z()}')
        if z() == '1':
            T('resize-pane', '-Z', '-t', 'u1')
        T('send-keys', '-c', client, '-K', 'Escape')
        # Controller path (tmux:menu): send-keys -c <client> -K <route>, then the item keys on that same client.
        r = T('send-keys', '-c', client, '-K', *keys)
        for k in ('P', 'z'):
            time.sleep(0.4)
            T('send-keys', '-c', client, '-K', k)
        check('[u1] controller route (send-keys -c <client> -K C-b Space, P, z) picks the item', r.returncode == 0 and n.wait(lambda: z() == '1', 4), r.stderr.strip())
        check('[u1] linux:mouse terminal feature active', 'linux:mouse' in T('show', '-s', 'terminal-features').stdout)
    except Exception as e:
        check('[u1] driver', False, repr(e))
    finally:
        T('kill-server')


def main():
    pid, portal, scales = primary()
    check('preflight primary', pid and portal >= 0, f'pid={pid} portal={portal} scales={scales}')
    session(CAND, 'cand', True)
    if OLD and Path(OLD).resolve() != Path(CAND).resolve():
        seen = session(OLD, 'old', False)
        # Negative control: the pre-G1 generator double-binds Print, loops the workspace binds, has no predicate and no
        # layout rule, and disables the session-scale handles over a captured app.
        for name in ['exactly one Print handle (no double bind)', 'workspace binds emitted literally (gate-auditable)',
                     'virtual keyboard pinned to the us layout', 'one capture predicate exported',
                     'SUPER+CTRL+= zooms the session over a captured fullscreen app (bypass_mod escape)']:
            check(f'negative control (old launcher fails): {name}', seen.get(name) is False)
    else:
        check('negative-control launcher differs from the candidate', False, OLD)
    pid2, portal2, scales2 = primary()
    check('postflight primary unchanged (pid, portal, output scales)', (pid2, portal2, scales2) == (pid, portal, scales), f'{pid2} {portal2} {scales2}')
    passed = sum(1 for r in results if r[1])
    (OUT / 'results.json').write_text(json.dumps([{'name': a, 'pass': b, 'detail': c} for a, b, c in results], indent=1))
    print(f'{passed}/{len(results)} passed  (evidence: {OUT})')
    return 0 if passed == len(results) else 1


if __name__ == '__main__':
    if os.environ.get('MIOS_LIVE_NESTED') != '1' or not os.environ.get('HYPRLAND_INSTANCE_SIGNATURE'):
        print('skip: live nested-session test (set MIOS_LIVE_NESTED=1 inside a MiOS desktop session)')
        sys.exit(0)
    sys.exit(main())
