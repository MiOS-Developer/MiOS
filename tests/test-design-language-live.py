#!/usr/bin/env python3
# AI-hint: LIVE opt-in (MIOS_LIVE_NESTED=1) design-language capture and bar-row alignment gate in an OWNED nested PiP
# session. Starts `mios-gen nested-session --layout pip` on the tree's SSOT (MIOS_TOML unset), replaces the session's
# shell with the Quickshell tree under test (Nested.qml of MIOS_QS_DIR, theme bridge generated from the same SSOT by that
# tree's mios-sync-theme), then captures every reachable surface in 2D and in the Hypr3D room (island, app-menu pill, the
# window controls tab raised into the bar row, every island popup, dock preview, search, maximized, fullscreen + edge
# reveal) and MEASURES the bar row: the vertical centre of the window-control buttons (pixels of their [colors] fills),
# the app-menu pill and the status island must agree within 1 px at scale 1, the gaps between buttons must equal the
# inset token and the gap between the controls tab and the first pill must equal the gap token ([theme.shell]).
# Negative control: run with MIOS_QS_DIR / MIOS_DESIGN_TREE pointing at main's tree; the alignment checks must FAIL.
# Never touches the primary compositor: every hyprctl / grim / quickshell call carries the nested session's env.
# AI-related: usr/share/mios/quickshell/Glass.qml, usr/share/mios/quickshell/GlassSurface.qml,
# usr/share/mios/quickshell/AppMenu.qml, usr/share/mios/quickshell/PanelWindow.qml, tests/test-design-language.py,
# tests/test-shell-hud-live.py (harness pattern), tests/fixtures/shell-hud/vptr.c
# AI-functions: main, check, sh, shells_in, build_vptr, blobs, measure_row
"""Usage:
  MIOS_LIVE_NESTED=1 env -u MIOS_TOML python3 tests/test-design-language-live.py
Optional: MIOS_DESIGN_TREE (tree whose SSOT, mios-sync-theme and Quickshell are under test; default this tree),
MIOS_QUICKSHELL (default /usr/bin/quickshell 0.2.1), MIOS_LIVE_OUT (evidence directory), MIOS_DESIGN_LABEL (file prefix),
MIOS_DESIGN_3D=0 (skip the Hypr3D room), MIOS_DESIGN_EXPECT_FAIL=1 (negative control: exit 0 when alignment fails)."""
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

if os.environ.get('MIOS_LIVE_NESTED') != '1':
    print('SKIP: set MIOS_LIVE_NESTED=1 to run against an owned nested MiOS session')
    sys.exit(0)

HERE = Path(__file__).resolve().parents[1]
TREE = Path(os.environ.get('MIOS_DESIGN_TREE') or HERE)
QS_DIR = TREE / 'usr/share/mios/quickshell'
SSOT = TREE / 'usr/share/mios/mios.toml'
SYNC = TREE / 'usr/libexec/mios/mios-sync-theme'
LABEL = os.environ.get('MIOS_DESIGN_LABEL', 'tree')
OUT = Path(os.environ.get('MIOS_LIVE_OUT') or tempfile.mkdtemp(prefix='mios-design-live-'))
OUT.mkdir(parents=True, exist_ok=True)
QS = os.environ.get('MIOS_QUICKSHELL', '/usr/bin/quickshell')
GEN = os.environ.get('MIOS_GEN_BIN', '/usr/bin/mios-gen')   # a tree whose SSOT carries design references needs its own mios-gen
UID_RUNTIME = os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')
results = []
metrics = {}


def check(name, ok, detail=''):
    results.append((name, bool(ok), str(detail)[:800]))
    print(f"{'PASS' if ok else 'FAIL'} {name} {str(detail)[:800]}", flush=True)


def sh(a, env=None, timeout=30):
    try:
        return subprocess.run(a, capture_output=True, text=True, env=env, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        return subprocess.CompletedProcess(a, 124, e.stdout or '', e.stderr or '')


def shells_in(runtime):
    """Quickshell processes of this nested session only (matched by XDG_RUNTIME_DIR), never the primary shell."""
    pids = []
    for p in Path('/proc').iterdir():
        if not p.name.isdigit():
            continue
        try:
            if p.joinpath('comm').read_text().strip() not in ('quickshell', 'qs', '.quickshell-wr'):
                continue
            envs = p.joinpath('environ').read_bytes().split(b'\0')
        except OSError:
            continue
        if f'XDG_RUNTIME_DIR={runtime}'.encode() in envs:
            pids.append(int(p.name))
    return pids


def build_vptr(d):
    if not (shutil.which('gcc') and shutil.which('wayland-scanner') and shutil.which('pkg-config')):
        return None
    d.mkdir(parents=True, exist_ok=True)
    fx = HERE / 'tests/fixtures/shell-hud'
    xml = str(fx / 'wlr-virtual-pointer-unstable-v1.xml')
    ok = sh(['wayland-scanner', 'client-header', xml, str(d / 'vp-client-protocol.h')]).returncode == 0
    ok = ok and sh(['wayland-scanner', 'private-code', xml, str(d / 'vp-protocol.c')]).returncode == 0
    flags = sh(['pkg-config', '--cflags', '--libs', 'wayland-client']).stdout.split()
    ok = ok and sh(['gcc', '-O1', f'-I{d}', '-o', str(d / 'vptr'), str(fx / 'vptr.c'), str(d / 'vp-protocol.c'),
                    *flags]).returncode == 0
    return str(d / 'vptr') if ok else None


def hexrgb(h):
    h = h.lstrip('#')
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def blobs(png, colours, band, tol=60, min_px=12):
    """Connected components of pixels near each [colors] fill inside `band` (x0, y0, x1, y1, device px).
    Returns {name: [(x0, y0, x1, y1, n), ...]} sorted left to right."""
    from PIL import Image
    im = Image.open(png).convert('RGB')
    W, H = im.size
    x0, y0, x1, y1 = (max(0, band[0]), max(0, band[1]), min(W, band[2]), min(H, band[3]))
    px = im.load()
    out = {}
    for name, c in colours.items():
        seen = set()
        comps = []
        for y in range(y0, y1):
            for x in range(x0, x1):
                if (x, y) in seen:
                    continue
                p = px[x, y]
                if sum(abs(p[i] - c[i]) for i in range(3)) > tol:
                    continue
                stack, pts = [(x, y)], []
                seen.add((x, y))
                while stack:
                    a, b = stack.pop()
                    pts.append((a, b))
                    for na, nb in ((a + 1, b), (a - 1, b), (a, b + 1), (a, b - 1)):
                        if x0 <= na < x1 and y0 <= nb < y1 and (na, nb) not in seen:
                            q = px[na, nb]
                            if sum(abs(q[i] - c[i]) for i in range(3)) <= tol:
                                seen.add((na, nb))
                                stack.append((na, nb))
                if len(pts) >= min_px:
                    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
                    comps.append((min(xs), min(ys), max(xs) + 1, max(ys) + 1, len(pts)))
        out[name] = sorted(comps)
    return out


# A private user tier for the owned session (never the operator's): the PiP viewport at 1:1 (scale 1, so the bar row is
# measured in real pixels), the desktop layout (no mobile verdict on the small viewport) and the controls tab raised on
# the focused window without a pointer (reveal_on = focus) so the row is measured deterministically.
USER_TOML = '''[desktop.mobile]
enabled = false
[desktop.nested.targets.pip]
scale_mode = "native"
[desktop.hyprland.hyprbars.config]
reveal_on = "focus"
'''


def main():
    user = OUT / f'{LABEL}-user.toml'
    user.write_text(USER_TOML)
    (OUT / f'{LABEL}-user.d').mkdir(exist_ok=True)
    launch_env = dict(os.environ, MIOS_VENDOR_TOML=str(SSOT), MIOS_USER_TOML=str(user), MIOS_USER_TOML_D=str(OUT / f'{LABEL}-user.d'))
    launch_env.pop('MIOS_TOML', None)
    launcher = subprocess.Popen([GEN, 'nested-session', '--layout', 'pip'], env=launch_env,
                                stdout=open(OUT / f'{LABEL}-launcher.log', 'w'), stderr=subprocess.STDOUT,
                                start_new_session=True)
    d = Path(UID_RUNTIME) / f'mios-nested-desktop-{launcher.pid}'
    env = None
    try:
        for _ in range(300):
            if (d / 'session.json').is_file():
                s = json.loads((d / 'session.json').read_text())
                env = dict(launch_env, XDG_RUNTIME_DIR=str(d), HYPRLAND_INSTANCE_SIGNATURE=s['instance']['instance'],
                           WAYLAND_DISPLAY=s['instance']['wl_socket'], QT_QPA_PLATFORM='wayland')
                env.pop('DISPLAY', None)
                break
            time.sleep(0.2)
        check('owned nested PiP session', env is not None, d.name)
        if env is None:
            return

        def hc(*a):
            return sh(['hyprctl', *a], env=env).stdout

        def ev(lua):
            return hc('eval', lua)

        def jhc(*a, default=None):
            try:
                return json.loads(hc('-j', *a) or 'null')
            except ValueError:
                return default

        def clients():
            return jhc('clients', default=[]) or []

        def layers():
            out = {}
            for mon in (jhc('layers', default={}) or {}).values():
                for lvl in mon.get('levels', {}).values():
                    for ly in lvl:
                        out.setdefault(ly['namespace'], (ly['x'], ly['y'], ly['w'], ly['h']))
            return out

        def monitor():
            return (jhc('monitors', default=[{}]) or [{}])[0]

        def logical():
            m = monitor()
            sc = m.get('scale') or 1
            return round(m.get('width', 0) / sc), round(m.get('height', 0) / sc)

        shots = []

        def shot(name):
            p = OUT / f'{LABEL}-{name}.png'
            sh(['grim', str(p)], env=env)
            shots.append(str(p))
            return p

        vptr = build_vptr(OUT / 'vptr')

        def ptr(x, y, mode='hover'):
            if not vptr:
                return False
            w, h = logical()
            return sh([vptr, monitor().get('name', ''), str(int(x)), str(int(y)), mode],
                      env=dict(env, VPTR_W=str(w), VPTR_H=str(h))).returncode == 0

        # theme bridge from the tree's own SSOT and generator (no host or user layer: the vendor design under test)
        bridge_dir = OUT / f'{LABEL}-bridge'
        r = sh(['bash', str(SYNC), '--bridge-only'],
               env=dict(os.environ, MIOS_VENDOR_TOML=str(SSOT), MIOS_HOST_TOML='/nonexistent',
                        MIOS_USER_TOML='/nonexistent', MIOS_THEME_OUT_DIR=str(bridge_dir), MIOS_LIB_DIR='/usr/lib/mios'))
        check('theme bridge generated from the tree SSOT', (bridge_dir / 'theme.json').is_file(), r.stderr[-300:])
        bridge = json.loads((bridge_dir / 'theme.json').read_text())
        shell_env = dict(env, PIPEWIRE_RUNTIME_DIR=UID_RUNTIME, MIOS_THEME_BRIDGE=str(bridge_dir / 'theme.json'),
                         MIOS_NESTED_TOPBAR_ITEMS=os.environ.get('MIOS_DESIGN_ITEMS', 'app_menu,clock'))
        qml = str(QS_DIR / 'Nested.qml')

        def ipc(*call):
            r = sh([QS, 'ipc', '--path', qml, '--any-display', 'call', *call], env=shell_env)
            try:
                return json.loads(r.stdout)
            except ValueError:
                return None if r.returncode else r.stdout.strip()

        def stop_shells():
            for pid in shells_in(str(d)):
                os.kill(pid, signal.SIGTERM)
            for _ in range(50):
                if not shells_in(str(d)):
                    break
                time.sleep(0.1)

        for _ in range(100):
            if shells_in(str(d)) and any(c['class'] == 'mios-nested-terminal' for c in clients()):
                break
            time.sleep(0.2)
        stop_shells()
        subprocess.Popen(['bwrap', '--die-with-parent', '--bind', '/', '/', '--dev-bind', '/dev', '/dev',
                          '--ro-bind', str(d / 'input'), '/dev/input', '--', QS, '--no-duplicate', '--path', qml],
                         env=shell_env, stdout=open(OUT / f'{LABEL}-shell.log', 'w'), stderr=subprocess.STDOUT)
        for _ in range(150):
            if isinstance(ipc('topbar', 'state'), dict):
                break
            time.sleep(0.2)
        time.sleep(2.5)
        st = ipc('topbar', 'state')
        check('shell under test is up', isinstance(st, dict), type(st))
        # The session's own terminal (tmux, agent lanes) is closed: only a fixture window is ever captured.
        for c in clients():
            if c['class'] == 'mios-nested-terminal':
                ev(f"hl.dispatch(hl.dsp.window.close({{window='address:{c['address']}'}}))")
        ev("hl.dispatch(hl.dsp.exec_cmd('alacritty --class Alacritty -e sleep 900'))")
        term = None
        for _ in range(60):
            term = next((c for c in clients() if c['class'] == 'Alacritty' and c.get('mapped', True)), None)
            if term and term['size'][0] > 0:
                break
            time.sleep(0.2)
        time.sleep(1.5)
        check('fixture window present (Alacritty, no session content)', term is not None)
        if not isinstance(st, dict) or term is None:
            return
        A = term['address']
        W, H = logical()
        scale = monitor().get('scale') or 1
        metrics['output'] = {'logical': [W, H], 'scale': scale}
        shot('hud-idle')

        def raise_tab(win):
            """Hover the window's top-left (the controls tab's reveal zone) so hyprbars raises its tab."""
            c = next((c for c in clients() if c['address'] == win), None)
            if not c:
                return None
            x, y = c['at']
            pad = (bridge.get('hyprbars') or {}).get('bar_padding', 52)
            ptr(x + W, y + H)    # leave, then re-enter (no enter event under a stationary pointer)
            time.sleep(0.3)
            ptr(x + pad + 20, max(1, y - 8))
            time.sleep(1.2)
            return c

        colours = {k: hexrgb(bridge[k]) for k in ('error', 'warning', 'success')}

        def measure_row(png, label):
            """The bar row: button centres (pixels), pill and island centres (layer geometry), gaps."""
            ly = layers()
            app = ipc('appmenu', 'state') or {}
            top = ipc('topbar', 'state') or {}
            row = {}
            band = (0, 0, int(W * scale / 2), int((bridge['shell']['edge_margin_px'] + bridge['shell']['top_height_px'] + 12) * scale))
            b = blobs(png, colours, band, tol=90, min_px=int(20 * scale * scale))
            btn = []
            for name in ('error', 'warning', 'success'):
                if b.get(name):
                    c = max(b[name], key=lambda t: t[4])
                    btn.append({'name': name, 'x0': c[0] / scale, 'x1': c[2] / scale, 'cy': (c[1] + c[3]) / 2 / scale,
                                'h': (c[3] - c[1]) / scale})
            btn.sort(key=lambda t: t['x0'])
            row['buttons'] = btn
            if 'mios-appmenu' in ly:
                x, y, w, h = ly['mios-appmenu']
                row['pill'] = {'x': x, 'y': y, 'w': w, 'h': h, 'cy': y + h / 2}
            isl = (top.get('island') or {})
            if 'mios-topbar' in ly and isl:
                y = ly['mios-topbar'][1] + (isl.get('px') or {}).get('y', 0) * 0
                row['island'] = {'y': ly['mios-topbar'][1], 'h': isl.get('height'), 'cy': ly['mios-topbar'][1] + (isl.get('height') or 0) / 2}
            row['tabs'] = app.get('tabs')
            row['row_left'] = app.get('row_left')
            metrics[label] = row
            return row

        # ---- the controls tab raised into the bar row next to the app-menu pill (the operator's crop) ---------------
        ev(f"hl.dispatch(hl.dsp.focus({{window='address:{A}'}}))")
        ev(f"hl.dispatch(hl.dsp.window.fullscreen({{mode='maximized', action='set', window='address:{A}'}}))")
        time.sleep(1.5)
        raise_tab(A)
        p = shot('maximized-tab-row')
        row = measure_row(p, 'maximized')
        ins = bridge['shell'].get('inset_px')
        gap = bridge['shell']['edge_margin_px']
        cys = [t['cy'] for t in row.get('buttons', [])] + [row[k]['cy'] for k in ('pill', 'island') if k in row]
        check('bar row elements measured (3 buttons, pill, island)', len(row.get('buttons', [])) == 3 and 'pill' in row and 'island' in row,
              json.dumps(row)[:600])
        check('bar row: buttons, app-menu pill and island share one vertical centre (<= 1 px at scale 1)',
              len(cys) >= 4 and max(cys) - min(cys) <= 1.0, [round(c, 2) for c in cys])
        bts = row.get('buttons', [])
        gaps = [round(bts[i + 1]['x0'] - bts[i]['x1'], 2) for i in range(len(bts) - 1)]
        check('bar row: gaps between window buttons equal the inset token', ins is not None and len(gaps) == 2 and all(abs(g - ins) <= 1.0 for g in gaps),
              {'gaps': gaps, 'inset_px': ins})
        # the tab's body edge: its published extent includes the fillet that flares into the window edge
        R, barh = bridge.get('radius_px', 28), bridge['hyprbars'].get('bar_height', bridge['shell']['top_height_px'])
        fillet = min(R / 2, barh / 2)
        tab_right = max(t['right'] for t in row['tabs']) - fillet if row.get('tabs') else None
        pg = round(row['pill']['x'] - tab_right, 2) if tab_right is not None and 'pill' in row else None
        check('bar row: controls tab to app-menu pill gap equals the gap token', pg is not None and abs(pg - gap) <= 1.0,
              {'gap': pg, 'edge_margin_px': gap})

        # ---- floating window: the tab protrudes above its own window ---------------------------------------------
        ev(f"hl.dispatch(hl.dsp.window.fullscreen({{mode='maximized', action='unset', window='address:{A}'}}))")
        time.sleep(1.0)
        raise_tab(A)
        shot('window-tab')

        # ---- island popups (the controls) -------------------------------------------------------------------------
        for name, call in (('calendar', 'openCalendar'), ('power', 'openPower'), ('sysmon', 'openSysmon'),
                           ('settings', 'openSettings')):
            ipc('topbar', call)
            time.sleep(1.0)
            shot(f'popup-{name}')
            ipc('topbar', 'close')
            time.sleep(0.6)
        ipc('appmenu', 'openMenu', '0')
        time.sleep(1.0)
        shot('popup-appmenu')
        ipc('appmenu', 'close')
        time.sleep(0.5)

        # ---- dock preview, search ----------------------------------------------------------------------------------
        ipc('dock-preview', 'window', A)
        time.sleep(1.5)
        shot('dock-preview')
        ipc('dock-preview', 'close')
        time.sleep(0.5)
        ipc('search', 'toggle')
        time.sleep(1.2)
        shot('search')
        ipc('search', 'toggle')
        time.sleep(0.6)
        ipc('drawer', 'toggle')
        time.sleep(1.5)
        shot('drawer')
        ipc('drawer', 'toggle')
        time.sleep(1.0)

        # ---- fullscreen + top edge reveal ---------------------------------------------------------------------------
        ev(f"hl.dispatch(hl.dsp.window.fullscreen({{mode='fullscreen', action='set', window='address:{A}'}}))")
        time.sleep(1.5)
        shot('fullscreen')
        ptr(W / 2, H / 2)
        time.sleep(0.3)
        ptr(W / 4, 0)
        time.sleep(1.5)
        p = shot('fullscreen-reveal')
        measure_row(p, 'fullscreen_reveal')
        ev(f"hl.dispatch(hl.dsp.window.fullscreen({{mode='fullscreen', action='unset', window='address:{A}'}}))")
        ptr(W / 2, H / 2)
        time.sleep(1.0)

        # ---- Hypr3D room ------------------------------------------------------------------------------------------
        if os.environ.get('MIOS_DESIGN_3D', '1') != '0':
            r = sh([GEN, 'hyprland-3d-toggle'], env=env, timeout=60)
            time.sleep(3.0)
            shot('hypr3d-room')
            ipc('topbar', 'openPower')
            time.sleep(1.2)
            shot('hypr3d-room-popup')
            ipc('topbar', 'close')
            time.sleep(0.5)
            sh([GEN, 'hyprland-3d-toggle'], env=env, timeout=60)
            time.sleep(1.5)
            check('Hypr3D room toggled in the owned session', r.returncode == 0, (r.stdout + r.stderr)[-200:])
        metrics['shots'] = shots
    finally:
        # the session's tmux server outlives the launcher: stop it through its own socket
        sock = d / 'tmux' / f'tmux-{os.getuid()}' / 'mios-nested'
        if sock.exists():
            sh(['tmux', '-S', str(sock), 'kill-server'])
        try:
            os.killpg(launcher.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            launcher.wait(timeout=20)
        except subprocess.TimeoutExpired:
            os.killpg(launcher.pid, signal.SIGKILL)
        (OUT / f'{LABEL}-result.json').write_text(json.dumps({'results': results, 'metrics': metrics}, indent=1))
    failed = [r for r in results if not r[1]]
    align = [r for r in results if r[0].startswith('bar row:')]
    if os.environ.get('MIOS_DESIGN_EXPECT_FAIL') == '1':
        ok = any(not r[1] for r in align)
        print(f"NEGATIVE CONTROL {'PASS' if ok else 'FAIL'}: alignment checks fail on this tree" if ok else
              'NEGATIVE CONTROL FAIL: the tree passed the alignment checks')
        sys.exit(0 if ok else 1)
    print(f'{len(results) - len(failed)}/{len(results)} passed; evidence {OUT}')
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
