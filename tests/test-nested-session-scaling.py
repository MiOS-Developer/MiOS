#!/usr/bin/env python3
# AI-hint: LIVE opt-in test (MIOS_LIVE_NESTED=1) of nested-session fit scaling, HUD scaling and the global session-scale binds (SUPER+CTRL +/-/0/wheel) in an owned nested MiOS PiP session: real key events, dispatcher, zoom, parent routing and the output path with a scratch user tier.
# AI-related: tools/native/mios-gen/src/nested_session.rs, tools/native/mios-gen/src/session_scale.rs, usr/share/mios/mios.toml [desktop.nested] [desktop.scaling] [keybindings.global]
# Live test of nested fit scaling and the global session-scale binds, in an owned nested MiOS session (PiP).
# Primary compositor: only the stock launcher's PiP adoption and key events sent to OUR nested window. No primary
# output is rescaled; the primary user tier is hash-checked; the verb's own persistence is exercised on a scratch tier.
import hashlib, json, math, os, re, signal, subprocess, sys, tempfile, time
from pathlib import Path

OUT = Path(os.environ.get('MIOS_LIVE_OUT') or tempfile.mkdtemp(prefix='mios-nested-scaling-'))
CAND = os.environ.get('MIOS_GEN', '/usr/bin/mios-gen')
WT_SSOT = Path(__file__).resolve().parents[1] / 'usr/share/mios/mios.toml'
TEST_SSOT = OUT / 'vendor-test.toml'
USER_TIER = Path.home() / '.config/mios/mios.toml'
PRIMARY_PID = int(os.environ.get('PRIMARY_PID') or subprocess.run(['pgrep', '-x', 'Hyprland', '-o'], capture_output=True, text=True).stdout.strip() or 0)
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
    mons = json.loads(sh(['hyprctl', '-j', 'monitors']).stdout)
    return pid, open('/proc/self/mountinfo').read().count('fuse.portal'), {m['name']: m['scale'] for m in mons}


def fit_scale(w, h, W=1280, H=800, lo=0.25, hi=2.0, zoom=1.0):
    s = max(lo, min(hi, min(w / W, h / H) * zoom))
    k = int(s * 120 + 0.5)
    for d in range(241):
        for c in (k - d, k + d):
            if math.ceil(lo * 120) <= c <= math.floor(hi * 120) and (w * 120) % c == 0 and (h * 120) % c == 0:
                return c / 120
    return 1.0


class Nested:
    def __init__(self, layout):
        self.layout = layout
        self.proc = self.dir = self.env = self.pid = None

    def start(self):
        env = dict(os.environ, MIOS_VENDOR_TOML=str(TEST_SSOT))
        self.proc = subprocess.Popen([CAND, 'nested-session', '--layout', self.layout], env=env, stdout=open(OUT / f'launcher-{self.layout}.log', 'w'), stderr=subprocess.STDOUT)
        self.dir = Path(os.environ['XDG_RUNTIME_DIR']) / f'mios-nested-desktop-{self.proc.pid}'
        end = time.monotonic() + 60
        while time.monotonic() < end:
            if self.proc.poll() is not None:
                raise RuntimeError(f'launcher exited: {(OUT / f"launcher-{self.layout}.log").read_text()[-600:]}')
            if (self.dir / 'session.json').is_file():
                d = json.loads((self.dir / 'session.json').read_text())
                inst = d['instance']
                self.pid = inst['pid']
                self.parent_window = d['parent_window']
                self.env = dict(os.environ, XDG_RUNTIME_DIR=str(self.dir), HYPRLAND_INSTANCE_SIGNATURE=inst['instance'], WAYLAND_DISPLAY=inst['wl_socket'], MIOS_VENDOR_TOML=str(TEST_SSOT))
                self.env.pop('DISPLAY', None)
                return
            time.sleep(0.2)
        raise RuntimeError('nested session did not publish session.json')

    def ctl(self, *a, env=None):
        return sh(['hyprctl', *a], env=env or self.env)

    def monitor(self):
        m = json.loads(self.ctl('-j', 'monitors').stdout)
        return m[0]

    def settle(self, pred, timeout=8.0):
        end = time.monotonic() + timeout
        m = self.monitor()
        while time.monotonic() < end:
            m = self.monitor()
            if pred(m):
                return True, m
            time.sleep(0.15)
        return False, m

    def shot(self, name):
        sh(['grim', str(OUT / f'{name}.png')], env=self.env)

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


def parent_key(window, mods, key):
    """SUPER+CTRL+key as a keyboard produces it (modifier keys held, key, releases), delivered by the primary
    compositor to OUR nested window only."""
    def sk(m, k, st):
        sh(['hyprctl', 'eval', f"hl.dispatch(hl.dsp.send_key_state({{mods='{m}',key='{k}',state='{st}',window='address:{window}'}}))"])
    sk('', 'Super_L', 'down'); sk('SUPER', 'Control_L', 'down')
    sk(mods, key, 'down'); sk(mods, key, 'up')
    sk('SUPER', 'Control_L', 'up'); sk('', 'Super_L', 'up')
    time.sleep(0.25)


def run_layout(layout, user_before):
    n = Nested(layout)
    try:
        n.start()
        check(f'[{layout}] nested session started', True, n.dir.name)
        ok, m = n.settle(lambda m: abs(m['scale'] - fit_scale(m['width'], m['height'])) < 1e-3)
        lw, lh = m['width'] / m['scale'], m['height'] / m['scale']
        check(f'[{layout}] output scales to fit the 1280x800 desktop', ok, f"{m['width']}x{m['height']} scale={m['scale']} logical={lw:.0f}x{lh:.0f} expected={fit_scale(m['width'], m['height'])}")
        check(f'[{layout}] logical size whole (no compositor substitution)', abs(lw - round(lw)) < 1e-3 and abs(lh - round(lh)) < 1e-3, f'{lw}x{lh}')
        log = (n.dir / 'desktop.log').read_text(errors='replace') if (n.dir / 'desktop.log').exists() else ''
        check(f'[{layout}] compositor reports no invalid scale', 'nvalid scale' not in log)
        if layout == 'pip':
            check('[pip] PiP shows the whole desktop scaled down (scale < 1)', m['scale'] < 1.0, m['scale'])
        # The HUD (topbar, dock) must shrink with the session: wait for the nested shell's layers, then compare their
        # on-screen size (logical x scale) with the PiP.
        layers = {}
        for _ in range(100):
            try:
                lj = json.loads(n.ctl('-j', 'layers').stdout)
                layers = {l['namespace']: l for mon in lj.values() for lvl in mon['levels'].values() for l in lvl}
            except (ValueError, KeyError, AttributeError):
                layers = {}
            if len([k for k in layers if 'mios' in k.lower() or 'quickshell' in k.lower()]) >= 2:
                break
            time.sleep(0.2)
        time.sleep(1.0)
        hud = {k: (v['w'], v['h']) for k, v in layers.items()}
        print('nested layers (logical):', hud, flush=True)
        sc = m['scale']
        big = {k: (round(w * sc), round(h * sc)) for k, (w, h) in hud.items() if w * h > 0}
        print('nested layers on screen (px):', big, flush=True)
        check(f'[{layout}] HUD layers present in the nested session', len(hud) >= 2, list(hud))
        check(f'[{layout}] HUD drawn scaled down with the session (no layer wider/taller on screen than the PiP, bars thin)',
              all(w <= m['width'] + 1 and h <= m['height'] + 1 for w, h in big.values()) and all(h <= m['height'] * 0.2 for k, (w, h) in big.items() if w >= m['width'] * 0.5),
              big)
        n.shot(f'{layout}-01-fit')
        base = m['scale']
        binds = n.ctl('-j', 'binds').stdout
        for key in ['equal', 'minus', '0', 'mouse_up', 'mouse_down']:
            check(f'[{layout}] nested projection binds SUPER+CTRL+{key}', re.search(rf'"key":\s*"{re.escape(key)}"', binds) is not None)
        # Real key events into the nested compositor -> its bind -> mios-inputd dispatch -> session-scale -> self zoom.
        parent_key(n.parent_window, 'SUPER CTRL', 'equal')
        ok, m = n.settle(lambda m: m['scale'] > base + 1e-6)
        check(f'[{layout}] SUPER+CTRL+= zooms the nested session in', ok, f"{base} -> {m['scale']}")
        if not ok:
            # Stage diagnostics: the Lua hook alone, then the dispatcher alone, then the bind table entry.
            r = n.ctl('eval', "mios_nested_zoom('up')")
            ok2, m2 = n.settle(lambda m: m['scale'] > base + 1e-6, 4)
            check(f'[{layout}] diag: mios_nested_zoom hook zooms', ok2, f"{r.stdout.strip()} -> {m2['scale']}")
            n.ctl('eval', "mios_nested_zoom('reset')"); n.settle(lambda m: abs(m['scale'] - base) < 1e-6, 4)
            r = sh(['/usr/libexec/mios/mios-inputd', 'dispatch', 'mios:session-scale-up'], env=n.env)
            ok3, m3 = n.settle(lambda m: m['scale'] > base + 1e-6, 4)
            check(f'[{layout}] diag: mios-inputd dispatch -> verb zooms', ok3, f"rc={r.returncode} {r.stdout.strip()[-120:]} {r.stderr.strip()[-200:]} -> {m3['scale']}")
            n.ctl('eval', "mios_nested_zoom('reset')"); n.settle(lambda m: abs(m['scale'] - base) < 1e-6, 4)
            b = [x for x in json.loads(binds) if x.get('key') == 'equal' and x.get('modmask')]
            print('diag binds for equal:', json.dumps(b)[:600], flush=True)
        n.shot(f'{layout}-02-zoom-in')
        parent_key(n.parent_window, 'SUPER CTRL', 'minus')
        parent_key(n.parent_window, 'SUPER CTRL', 'minus')
        ok, m = n.settle(lambda m: m['scale'] < base - 1e-6)
        check(f'[{layout}] SUPER+CTRL+- (x2) zooms out below fit', ok, f"-> {m['scale']}")
        n.shot(f'{layout}-03-zoom-out')
        parent_key(n.parent_window, 'SUPER CTRL', '0')
        ok, m = n.settle(lambda m: abs(m['scale'] - base) < 1e-6)
        check(f'[{layout}] SUPER+CTRL+0 resets to fit', ok, f"-> {m['scale']}")
        check(f'[{layout}] nested zoom never writes the operator user tier', sha(USER_TIER) == user_before)

        # Parent routing, live: a scratch "parent" runtime whose session.json names the nested compositor's focused
        # window as a nested session; the verb must zoom that session through its own instance.
        # short path: a Unix socket path must fit in 108 bytes
        scratch_rt = Path(os.environ['XDG_RUNTIME_DIR']) / f'mios-scale-test-{layout}'
        scratch_rt.mkdir(mode=0o700, exist_ok=True)
        hl = scratch_rt / 'hypr'
        if hl.is_symlink() or hl.exists():
            hl.unlink()
        hl.symlink_to(n.dir / 'hypr')
        active = json.loads(n.ctl('-j', 'activewindow').stdout or '{}').get('address')
        target = scratch_rt / 'mios-nested-desktop-route'
        target.mkdir(exist_ok=True)
        (target / 'session.json').write_text(json.dumps({'runtime': str(n.dir), 'instance': {'instance': n.env['HYPRLAND_INSTANCE_SIGNATURE']}, 'parent_window': active or '0x0'}))
        envp = dict(n.env, XDG_RUNTIME_DIR=str(scratch_rt), MIOS_USER_TOML=str(OUT / 'scratch-user.toml'))
        if active:
            r = sh([CAND, 'session-scale', 'up'], env=envp)
            ok, m = n.settle(lambda m: m['scale'] > base + 1e-6)
            check(f'[{layout}] parent routing: focused nested window -> that session zooms', r.returncode == 0 and 'nested session zoom' in r.stdout and ok, f"{r.stdout.strip()} {r.stderr.strip()} -> {m['scale']}")
            sh([CAND, 'session-scale', 'reset'], env=envp)
            n.settle(lambda m: abs(m['scale'] - base) < 1e-6)
        else:
            check(f'[{layout}] parent routing (needs a focused window in the nested session)', False, 'no active window')

        # Primary-output path, live, on the nested compositor standing in as a top-level session (scratch runtime
        # without a nested descriptor, scratch user tier): step, persist, mark explicit, reset.
        (target / 'session.json').unlink()
        scratch_user = OUT / 'scratch-user.toml'
        scratch_user.write_text('[identity]\nkeep = "this"\n')
        os.chmod(scratch_user, 0o600)
        r = sh([CAND, 'session-scale', 'up'], env=envp)
        ok, m = n.settle(lambda m: m['scale'] > base + 1e-6)
        txt = scratch_user.read_text()
        name = m['name']
        saved = re.search(rf'\[desktop\.hyprland\.monitors\.{re.escape(name)}\]\s*\nscale\s*=\s*([\d.]+)', txt)
        check(f'[{layout}] output path: scale stepped live', ok and r.returncode == 0, f"{r.stdout.strip()} {r.stderr.strip()}")
        check(f'[{layout}] output path: compositor kept the exact scale (valid grid)', saved and abs(float(saved.group(1)) - m['scale']) < 1e-6, f"saved={saved and saved.group(1)} live={m['scale']}")
        check(f'[{layout}] output path: saved privately, other tables byte-exact', txt.startswith('[identity]\nkeep = "this"\n') and (scratch_user.stat().st_mode & 0o777) == 0o600)
        explicit = n.ctl('eval', f"if not (MIOS_SCALE_EXPLICIT and MIOS_SCALE_EXPLICIT['{name}']) then error('not explicit',0) end").stdout.strip()
        check(f'[{layout}] output path: marked explicit live (policy will not revert it)', explicit == 'ok', explicit)
        r = sh([CAND, 'session-scale', 'reset'], env=envp)
        txt = scratch_user.read_text()
        check(f'[{layout}] output path: reset removes the saved scale', r.returncode == 0 and 'monitors' not in txt and txt.startswith('[identity]'), f"{r.stdout.strip()} {r.stderr.strip()}")
        n.shot(f'{layout}-04-after-reset')
    except Exception as e:
        check(f'[{layout}] driver', False, repr(e))
    finally:
        n.stop()
        import shutil
        shutil.rmtree(Path(os.environ['XDG_RUNTIME_DIR']) / f'mios-scale-test-{layout}', ignore_errors=True)
        check(f'[{layout}] nested runtime cleaned up', n.dir is None or not n.dir.exists(), n.dir)


def main():
    text = WT_SSOT.read_text()
    text = text.replace('["/usr/bin/mios-gen", "session-scale",', f'["{CAND}", "session-scale",')
    TEST_SSOT.write_text(text)
    os.chmod(TEST_SSOT, 0o644)
    user_before = sha(USER_TIER)
    pid, portal, scales = primary()
    check('preflight primary', pid == str(PRIMARY_PID) and portal == 1, f'pid={pid} portal={portal} scales={scales}')
    for layout in ['pip']:  # nested sessions open as PiP, globally
        run_layout(layout, user_before)
    pid2, portal2, scales2 = primary()
    check('postflight primary unchanged (pid, portal, output scales)', (pid2, portal2, scales2) == (pid, portal, scales), f'{pid2} {portal2} {scales2}')
    check('operator user tier untouched', sha(USER_TIER) == user_before)
    passed = sum(1 for r in results if r[1])
    (OUT / 'results.json').write_text(json.dumps([{'name': a, 'pass': b, 'detail': c} for a, b, c in results], indent=1))
    print(f'{passed}/{len(results)} passed')
    return 0 if passed == len(results) else 1


if __name__ == '__main__':
    if os.environ.get('MIOS_LIVE_NESTED') != '1' or not os.environ.get('HYPRLAND_INSTANCE_SIGNATURE'):
        print('skip: live nested-session test (set MIOS_LIVE_NESTED=1 inside a MiOS desktop session)')
        sys.exit(0)
    sys.exit(main())
