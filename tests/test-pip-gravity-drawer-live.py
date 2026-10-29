#!/usr/bin/env python3
# AI-hint: LIVE opt-in test (MIOS_LIVE_NESTED=1): opening the app drawer (the dock morphing into a full pane) must not move or resize a PiP. Runs PiP gravity against an owned nested MiOS PiP session -- the OLD binary as the negative control (set MIOS_PIP_GRAVITY_OLD), then the candidate.
# AI-related: tools/native/mios-pip-gravity/src/hud.rs, usr/share/mios/quickshell/Sidebar.qml, usr/share/mios/mios.toml [desktop.pip_gravity]
# Live: does opening the app drawer (the dock morphing into a full pane) move or resize a PiP? Runs PiP gravity
# against an owned nested MiOS PiP session: first the installed binary (negative control: reproduces the bug), then the
# candidate (must hold the PiP still). The primary compositor is untouched.
import json, os, signal, subprocess, sys, tempfile, time
from pathlib import Path

OUT = Path(os.environ.get('MIOS_LIVE_OUT') or tempfile.mkdtemp(prefix='mios-pip-drawer-'))
OLD = os.environ.get('MIOS_PIP_GRAVITY_OLD', '')
NEW = os.environ.get('MIOS_PIP_GRAVITY', '/usr/bin/mios-pip-gravity')
SSOT = os.environ.get('MIOS_VENDOR_TOML', str(Path(__file__).resolve().parents[1] / 'usr/share/mios/mios.toml'))
results = []


def check(name, ok, detail=''):
    results.append((name, bool(ok), str(detail)))
    print(f"{'PASS' if ok else 'FAIL'} {name} {detail}", flush=True)


def sh(a, env=None, timeout=30):
    return subprocess.run(a, capture_output=True, text=True, env=env, timeout=timeout)


def main():
    portal0 = open('/proc/self/mountinfo').read().count('fuse.portal')
    launcher = subprocess.Popen(['/usr/bin/mios-gen', 'nested-session', '--layout', 'pip'], stdout=open(OUT / 'launcher.log', 'w'), stderr=subprocess.STDOUT)
    d = Path(os.environ['XDG_RUNTIME_DIR']) / f'mios-nested-desktop-{launcher.pid}'
    env = None
    nested_pid = None
    daemon = None
    try:
        for _ in range(300):
            if (d / 'session.json').is_file():
                s = json.loads((d / 'session.json').read_text())
                nested_pid = s['instance']['pid']
                env = dict(os.environ, XDG_RUNTIME_DIR=str(d), HYPRLAND_INSTANCE_SIGNATURE=s['instance']['instance'], WAYLAND_DISPLAY=s['instance']['wl_socket'], MIOS_VENDOR_TOML=SSOT)
                env.pop('DISPLAY', None)
                break
            time.sleep(0.2)
        check('nested PiP session', env is not None, d.name)

        def ctl(*a):
            return sh(['hyprctl', *a], env=env)

        def layer(ns):
            try:
                for mon in json.loads(ctl('-j', 'layers').stdout).values():
                    for lvl in mon['levels'].values():
                        for l in lvl:
                            if l['namespace'] == ns:
                                return (l['x'], l['y'], l['w'], l['h'])
            except (ValueError, KeyError):
                pass
            return None

        for _ in range(100):
            if layer('mios-dock'):
                break
            time.sleep(0.2)
        check('nested shell dock present', layer('mios-dock'), layer('mios-dock'))
        ctl('eval', "hl.dispatch(hl.dsp.exec_cmd('alacritty --title Picture-in-Picture --class mios-pip-test -e sleep 900'))")

        def pip():
            for c in json.loads(ctl('-j', 'clients').stdout or '[]'):
                if c.get('class') == 'mios-pip-test':
                    return c
            return None

        for _ in range(100):
            if pip():
                break
            time.sleep(0.2)
        check('PiP-matching window mapped', pip())

        def drawer(open_):
            # toggle until the dock layer is (not) a full pane
            for _ in range(3):
                l = layer('mios-dock')
                is_open = bool(l and l[3] > 300)
                if is_open == open_:
                    return True
                r = sh(['quickshell', 'ipc', '--path', '/usr/share/mios/quickshell/Nested.qml', '--any-display', 'call', 'drawer', 'toggle'], env=env)
                if r.returncode or r.stderr.strip():
                    print('ipc:', r.returncode, r.stdout.strip()[:200], r.stderr.strip()[:300], flush=True)
                    r2 = sh(['quickshell', 'ipc', '--path', '/usr/share/mios/quickshell/Nested.qml', '--any-display', 'show'], env=env)
                    print('ipc show:', r2.returncode, r2.stdout.strip()[:400], r2.stderr.strip()[:200], flush=True)
                time.sleep(1.0)
            l = layer('mios-dock')
            return bool(l and l[3] > 300) == open_

        def run(binary, label):
            nonlocal daemon
            daemon = subprocess.Popen([binary], env=env, stdout=open(OUT / f'daemon-{label}.log', 'w'), stderr=subprocess.STDOUT)
            # let gravity adopt and settle the PiP
            time.sleep(4.0)
            c = pip()
            before = (tuple(c['at']), tuple(c['size'])) if c else None
            check(f'[{label}] PiP adopted and settled', c and c.get('floating') and c.get('pinned'), before)
            opened = drawer(True)
            samples = []
            t_end = time.monotonic() + 4.0
            while time.monotonic() < t_end:
                c = pip()
                if c:
                    samples.append((tuple(c['at']), tuple(c['size'])))
                time.sleep(0.1)
            drawer(False)
            time.sleep(2.0)
            c = pip()
            after = (tuple(c['at']), tuple(c['size'])) if c else None
            moved = [s for s in samples if s != before]
            daemon.send_signal(signal.SIGTERM)
            daemon.wait(timeout=10)
            daemon = None
            return opened, before, moved, after

        if OLD:
          opened, before, moved, after = run(OLD, 'installed')
          check('[installed] drawer opened (dock surface became a full pane)', opened, layer('mios-dock'))
          check('[installed] NEGATIVE CONTROL: the open drawer moves/resizes the PiP (the reported bug)', len(moved) > 0, f'{len(moved)} moved samples, e.g. {moved[:1]} from {before}')
        # put the PiP back where it was for a fair second run
        if OLD and before:
            (x, y), (w, h) = before
            ctl('eval', f"for _,c in ipairs(hl.get_windows()) do if c.class=='mios-pip-test' then hl.dispatch(hl.dsp.window.resize({{x={w},y={h},window='address:'..c.address}})) hl.dispatch(hl.dsp.window.move({{x={x},y={y},window='address:'..c.address}})) end end")
            time.sleep(1.0)
        opened, before, moved, after = run(NEW, 'candidate')
        check('[candidate] drawer opened (dock surface became a full pane)', opened)
        check('[candidate] the open drawer leaves the PiP exactly where and as big as it was', opened and before and not moved, f'{len(moved)} moved samples {moved[:1]} before={before}')
        check('[candidate] PiP unchanged after the drawer closes', after == before, f'{before} -> {after}')
    except Exception as e:
        check('driver', False, repr(e))
    finally:
        if daemon:
            daemon.kill()
        if nested_pid:
            try:
                os.kill(nested_pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        try:
            launcher.wait(timeout=30)
        except subprocess.TimeoutExpired:
            launcher.kill()
        check('nested runtime cleaned up', not d.exists())
        check('primary unchanged', sh(['pgrep', '-x', 'Hyprland', '-o']).stdout.strip() == os.environ.get('PRIMARY_PID', sh(['pgrep', '-x', 'Hyprland', '-o']).stdout.strip()) and open('/proc/self/mountinfo').read().count('fuse.portal') == portal0)
    n = sum(1 for r in results if r[1])
    print(f'{n}/{len(results)} passed')
    return 0 if n == len(results) else 1


if __name__ == '__main__':
    if os.environ.get('MIOS_LIVE_NESTED') != '1' or not os.environ.get('HYPRLAND_INSTANCE_SIGNATURE'):
        print('skip: live nested-session test (set MIOS_LIVE_NESTED=1 inside a MiOS desktop session)')
        sys.exit(0)
    sys.exit(main())
