#!/usr/bin/env python3
# AI-hint: LIVE opt-in test (MIOS_LIVE_NESTED=1) of the HUD window contract in an owned nested PiP session: maximized
# windows stay inside the HUD's reserved rows, only a true fullscreen window auto-hides the status island and the dock,
# foreground windows (floating and newly focused tiled ones) are shown and focused above fullscreen content, the HUD is
# centred, the wheel on the island shows the volume as a percentage chip, and device batteries carry a device glyph and
# a level glyph. Every behaviour has a negative control (no shell -> no reserved rows; on_focus_under_fullscreen = 0;
# volume_scroll = false; the QML without the scroll-volume IPC). The primary compositor and shell are never touched; the
# default sink's volume is stepped up and back down by the same amount.
# AI-related: usr/share/mios/quickshell/PanelWindow.qml, usr/share/mios/quickshell/EdgeReveal.qml,
# usr/share/mios/quickshell/Nested.qml, usr/share/mios/mios.toml [desktop.topbar] [desktop.hyprland.config.misc],
# tests/test-quickshell-candidate-live.py, tests/test-shell-reserve.py
# AI-functions: main, check, shells_in
"""Usage:
  MIOS_LIVE_NESTED=1 env -u MIOS_TOML python3 tests/test-shell-hud-live.py
Optional: MIOS_QUICKSHELL (default /usr/bin/quickshell), MIOS_QS_QML (default this tree's Nested.qml),
MIOS_QS_OLD_QML (negative control QML without the scroll-volume IPC, default /usr/share/mios/quickshell/Nested.qml),
MIOS_LIVE_OUT (evidence directory: screenshots, shell logs, result.json)."""
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

TREE = Path(__file__).resolve().parents[1]
OUT = Path(os.environ.get('MIOS_LIVE_OUT') or tempfile.mkdtemp(prefix='mios-hud-live-'))
OUT.mkdir(parents=True, exist_ok=True)
QS = os.environ.get('MIOS_QUICKSHELL', '/usr/bin/quickshell')
QML = os.environ.get('MIOS_QS_QML', str(TREE / 'usr/share/mios/quickshell/Nested.qml'))
OLD_QML = os.environ.get('MIOS_QS_OLD_QML', '/usr/share/mios/quickshell/Nested.qml')
SSOT = str(TREE / 'usr/share/mios/mios.toml')
UID_RUNTIME = os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')
results = []


def check(name, ok, detail=''):
    results.append((name, bool(ok), str(detail)[:600]))
    print(f"{'PASS' if ok else 'FAIL'} {name} {str(detail)[:600]}", flush=True)


def sh(a, env=None, timeout=30):
    return subprocess.run(a, capture_output=True, text=True, env=env, timeout=timeout)


# Logical rows a maximized window must keep between the reserved HUD rows; a shorter nested output (the PiP host can
# shrink it) cannot show the window, and Hyprland then reports a non-positive size that the bounds alone accept.
MIN_FREE_ROWS = 120


def inside_reserved(c, res, height):
    """A maximized window is inside the reserved rows only with a visible size on a tall enough output."""
    at, size = c.get('at') or [0, 0], c.get('size') or [0, 0]
    if height - res[1] - res[3] < MIN_FREE_ROWS:
        return False
    if size[0] <= 0 or size[1] <= 0:
        return False
    return (c.get('fullscreen') == 1 and res[1] > 0 and res[3] > 0 and at[1] >= res[1]
            and at[1] + size[1] <= height - res[3])


def inside_reserved_pre_fix(c, res, height):
    """The check before the size/height guard, kept as the negative control of inside_reserved."""
    return (c.get('fullscreen') == 1 and res[1] > 0 and res[3] > 0 and c['at'][1] >= res[1]
            and c['at'][1] + c['size'][1] <= height - res[3])


def volume_now():
    """The default sink's (volume, muted) from WirePlumber, independent of the shell under test."""
    r = sh(['wpctl', 'get-volume', '@DEFAULT_AUDIO_SINK@'])
    try:
        return float(r.stdout.split()[1]), '[MUTED]' in r.stdout
    except (IndexError, ValueError):
        return None, None


def restore_volume(saved):
    level, muted = saved
    if level is None:
        return
    sh(['wpctl', 'set-volume', '@DEFAULT_AUDIO_SINK@', f'{level:.2f}'])
    sh(['wpctl', 'set-mute', '@DEFAULT_AUDIO_SINK@', '1' if muted else '0'])


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
    """The virtual-pointer fixture, compiled into the evidence directory; None without gcc / wayland-scanner."""
    if not (shutil.which('gcc') and shutil.which('wayland-scanner') and shutil.which('pkg-config')):
        return None
    d.mkdir(parents=True, exist_ok=True)
    fx = TREE / 'tests/fixtures/shell-hud'
    ok = sh(['wayland-scanner', 'client-header', str(fx / 'wlr-virtual-pointer-unstable-v1.xml'),
             str(d / 'vp-client-protocol.h')]).returncode == 0
    ok = ok and sh(['wayland-scanner', 'private-code', str(fx / 'wlr-virtual-pointer-unstable-v1.xml'),
                    str(d / 'vp-protocol.c')]).returncode == 0
    flags = sh(['pkg-config', '--cflags', '--libs', 'wayland-client']).stdout.split()
    ok = ok and sh(['gcc', '-O1', f'-I{d}', '-o', str(d / 'vptr'), str(fx / 'vptr.c'), str(d / 'vp-protocol.c'),
                    *flags]).returncode == 0
    return str(d / 'vptr') if ok else None


def main():
    saved_volume = [None]   # (level, muted) of the default sink before any volume check
    launch_env = dict(os.environ, MIOS_VENDOR_TOML=SSOT)
    launch_env.pop('MIOS_TOML', None)
    launcher = subprocess.Popen(['/usr/bin/mios-gen', 'nested-session', '--layout', 'pip'], env=launch_env,
                                stdout=open(OUT / 'launcher.log', 'w'), stderr=subprocess.STDOUT, start_new_session=True)
    d = Path(UID_RUNTIME) / f'mios-nested-desktop-{launcher.pid}'
    env = nested_pid = None
    shell = None
    try:
        for _ in range(300):
            if (d / 'session.json').is_file():
                s = json.loads((d / 'session.json').read_text())
                nested_pid = s['instance']['pid']
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

        def clients():
            try:
                return json.loads(hc('-j', 'clients') or '[]')
            except ValueError:
                return []

        def layers():
            out = {}
            try:
                for mon in json.loads(hc('-j', 'layers')).values():
                    for lvl in mon['levels'].values():
                        for l in lvl:
                            out[l['namespace']] = (l['x'], l['y'], l['w'], l['h'])
            except (ValueError, KeyError, AttributeError):
                pass
            return out

        def monitor():
            try:
                return (json.loads(hc('-j', 'monitors') or '[{}]') or [{}])[0]
            except ValueError:
                return {}

        def logical():
            m = monitor()
            sc = m.get('scale') or 1
            tr = m.get('transform', 0) % 2
            w, h = (m.get('height', 0), m.get('width', 0)) if tr else (m.get('width', 0), m.get('height', 0))
            return round(w / sc), round(h / sc)

        def shot(name):
            sh(['grim', str(OUT / f'{name}.png')], env=env)

        # a real pointer in the nested session (zwlr_virtual_pointer_v1), built from the fixture
        vptr = build_vptr(OUT / 'vptr')

        def ptr(x, y, mode):
            w, h = logical()
            name = monitor().get('name', '')
            r = sh([vptr, name, str(int(x)), str(int(y)), mode], env=dict(env, VPTR_W=str(w), VPTR_H=str(h)))
            return r.returncode == 0

        def real_wheel(x, y, notches):
            # Hyprland sends no pointer enter to a surface that mapped under a stationary pointer: leave, re-enter
            ptr(x, y + 300, 'hover')
            time.sleep(0.3)
            ptr(x, y, 'hover')
            time.sleep(0.4)
            return ptr(x, y, f'scroll:{notches}')

        def island_centre(qml):
            st_ = ipc(qml, 'topbar', 'state') or {}
            ly = layers().get('mios-topbar')
            if not ly:
                return None
            hud = st_.get('hud') or {}
            x = hud['x'] + hud['width'] / 2 if hud.get('width') else ly[0] + ly[2] / 2
            return x, ly[1] + ly[3] / 2

        # The shell under test: a private copy of the theme bridge (the negative control flips volume_scroll in it),
        # PipeWire from the user's runtime (the nested runtime has no PipeWire socket), batteries shown in the island.
        bridge_src = Path(os.environ.get('MIOS_THEME_BRIDGE') or Path.home() / '.local/state/mios/theme/theme.json')
        bridge = OUT / 'theme.json'
        base_bridge = json.loads(bridge_src.read_text()) if bridge_src.is_file() else {}
        bridge.write_text(json.dumps(base_bridge))
        shell_env = dict(env, PIPEWIRE_RUNTIME_DIR=UID_RUNTIME, MIOS_THEME_BRIDGE=str(bridge),
                         MIOS_NESTED_TOPBAR_ITEMS='clock,fps,workspaces,batteries')

        def stop_shells():
            for pid in shells_in(str(d)):
                os.kill(pid, signal.SIGTERM)
            for _ in range(50):
                if not shells_in(str(d)):
                    break
                time.sleep(0.1)

        def start_shell(qml, label):
            stop_shells()
            p = subprocess.Popen(['bwrap', '--die-with-parent', '--bind', '/', '/', '--dev-bind', '/dev', '/dev',
                                  '--ro-bind', str(d / 'input'), '/dev/input', '--', QS, '--no-duplicate', '--path', qml],
                                 env=shell_env, stdout=open(OUT / f'shell-{label}.log', 'w'), stderr=subprocess.STDOUT)
            for _ in range(150):
                if ipc(qml, 'topbar', 'state') is not None:
                    break
                time.sleep(0.2)
            time.sleep(2.0)
            return p

        def ipc(qml, *call):
            r = sh([QS, 'ipc', '--path', qml, '--any-display', 'call', *call], env=shell_env)
            try:
                return json.loads(r.stdout)
            except ValueError:
                return None if r.returncode else r.stdout.strip()

        for _ in range(100):
            if shells_in(str(d)) and any(c['class'] == 'mios-nested-terminal' for c in clients()):
                break
            time.sleep(0.2)
        shell = start_shell(QML, 'tree')
        st = ipc(QML, 'topbar', 'state')
        check('shell under test is up (topbar IPC)', isinstance(st, dict), type(st))
        term = next((c for c in clients() if c['class'] == 'mios-nested-terminal'), None)
        check('nested terminal window present', term is not None)
        if not isinstance(st, dict) or term is None:
            return
        A = term['address']
        W, H = logical()

        # ---- centred HUD -------------------------------------------------------------------------------------------
        ls = layers()
        for ns in ('mios-topbar', 'mios-dock'):
            r = ls.get(ns)
            check(f'{ns} is centred on the output', r is not None and abs((r[0] + r[2] / 2) - W / 2) <= 1.5, (r, W))
        shot('hud-initial')

        # ---- maximize stays inside the reserved rows -----------------------------------------------------------------
        # the guard itself: the verifier's degenerate run (output shrunk to 328 rows under 176+288 reserved, window
        # size [444,-142]) passed the old bounds check; a real maximized window passes both
        degenerate = ({'fullscreen': 1, 'at': [0, 176], 'size': [444, -142]}, [0, 176, 0, 288], 328)
        sane = ({'fullscreen': 1, 'at': [0, 44], 'size': [1280, 604]}, [0, 44, 0, 72], 720)
        check('NEGATIVE CONTROL: the old maximize check accepts a window with no visible size; the guarded one rejects it',
              inside_reserved_pre_fix(*degenerate) and not inside_reserved(*degenerate)
              and inside_reserved_pre_fix(*sane) and inside_reserved(*sane))

        def maximize_inside():
            ev(f"hl.dispatch(hl.dsp.window.fullscreen({{mode='maximized', action='set', window='address:{A}'}}))")
            time.sleep(1.5)
            c = next((c for c in clients() if c['address'] == A), {})
            res = monitor().get('reserved') or [0, 0, 0, 0]
            height = logical()[1]
            ls = layers()
            ok = inside_reserved(c, res, height) and 'mios-topbar' in ls and 'mios-dock' in ls
            ev(f"hl.dispatch(hl.dsp.window.fullscreen({{mode='maximized', action='unset', window='address:{A}'}}))")
            time.sleep(1.0)
            return ok, {'window': (c.get('at'), c.get('size'), c.get('fullscreen')), 'reserved': res, 'layers': sorted(ls),
                        'height': height, 'free_rows': height - res[1] - res[3], 'min_free_rows': MIN_FREE_ROWS}

        ok, det = maximize_inside()
        shot('maximized')
        check('maximized window stays inside the HUD reserved rows; HUD stays shown', ok, det)
        stop_shells()
        time.sleep(1.0)
        ok_neg, det_neg = maximize_inside()
        check('NEGATIVE CONTROL: without the shell nothing is reserved and the check rejects the maximized window',
              not ok_neg, det_neg)
        shell = start_shell(QML, 'tree2')

        # ---- only a true fullscreen window auto-hides the HUD ---------------------------------------------------------
        ev(f"hl.dispatch(hl.dsp.focus({{window='address:{A}'}}))")
        ev(f"hl.dispatch(hl.dsp.window.fullscreen({{mode='fullscreen', action='set', window='address:{A}'}}))")
        time.sleep(2.0)
        ls, res = layers(), monitor().get('reserved')
        shot('fullscreen')
        check('a true fullscreen window auto-hides the island and the dock (reserved 0, hot zones only)',
              'mios-topbar' not in ls and 'mios-dock' not in ls and res == [0, 0, 0, 0], (sorted(ls), res))

        # ---- foreground windows over fullscreen content ---------------------------------------------------------------
        ev("hl.dispatch(hl.dsp.exec_cmd('alacritty --class mios-hud-fg -e sleep 600', {float=true}))")
        fg = None
        for _ in range(50):
            fg = next((c for c in clients() if c['class'] == 'mios-hud-fg'), None)
            if fg and fg['mapped'] and fg['size'][0] > 0:
                break
            time.sleep(0.2)
        time.sleep(1.0)
        act = json.loads(hc('-j', 'activewindow') or '{}')
        tc = next((c for c in clients() if c['address'] == A), {})
        shot('floating-over-fullscreen')
        check('a floating window opens above the fullscreen window, focused, fullscreen kept',
              fg is not None and act.get('address') == fg['address'] and tc.get('fullscreen') == 2,
              (act.get('class'), tc.get('fullscreen')))
        if fg:
            ev(f"hl.dispatch(hl.dsp.window.close({{window='address:{fg['address']}'}}))")
        time.sleep(1.0)

        def tiled_over_fullscreen(mode):
            ev(f"hl.config({{misc={{on_focus_under_fullscreen={mode}}}}})")
            ev(f"hl.dispatch(hl.dsp.window.fullscreen({{mode='fullscreen', action='set', window='address:{A}'}}))")
            time.sleep(1.0)
            ev("hl.dispatch(hl.dsp.exec_cmd('alacritty --class mios-hud-tiled -e sleep 600'))")
            t = None
            for _ in range(40):
                t = next((c for c in clients() if c['class'] == 'mios-hud-tiled'), None)
                if t and t['size'][0] > 0:
                    break
                time.sleep(0.2)
            time.sleep(1.0)
            t = next((c for c in clients() if c['class'] == 'mios-hud-tiled'), None)
            act = json.loads(hc('-j', 'activewindow') or '{}')
            ok = bool(t) and act.get('address') == t['address'] and t['size'][0] > 0 and t['size'][1] > 0
            det = (act.get('class'), t and (t['at'], t['size']))
            shot(f'tiled-over-fullscreen-{mode}')
            if t:
                ev(f"hl.dispatch(hl.dsp.window.close({{window='address:{t['address']}'}}))")
            time.sleep(0.8)
            ev(f"hl.dispatch(hl.dsp.window.fullscreen({{mode='fullscreen', action='unset', window='address:{A}'}}))")
            time.sleep(0.8)
            return ok, det

        configured = sh(['env', '-u', 'MIOS_TOML', '/usr/bin/mios-toml-get', 'desktop.hyprland.config.misc',
                         'on_focus_under_fullscreen'], env=launch_env).stdout.strip()
        check('SSOT pins on_focus_under_fullscreen = 2 (also the Hyprland 0.56 default)', configured == '2', configured)
        ok, det = tiled_over_fullscreen(2)
        check('a newly focused tiled window comes to the front over fullscreen content and takes input', ok, det)
        ok_neg, det_neg = tiled_over_fullscreen(0)
        check('NEGATIVE CONTROL: on_focus_under_fullscreen = 0 leaves the new window hidden behind the fullscreen one',
              not ok_neg, det_neg)
        ev("hl.config({misc={on_focus_under_fullscreen=2}})")

        # ---- scroll-volume percentage ------------------------------------------------------------------------------
        st = ipc(QML, 'topbar', 'state')
        vol = st.get('volume') if isinstance(st, dict) else None
        if not vol or vol.get('percent', -1) < 0:
            check('default sink reachable for the volume checks', False, vol)
        else:
            saved_volume[0] = volume_now()
            check('the default sink is restored from WirePlumber afterwards (level and mute)', saved_volume[0][0] is not None,
                  saved_volume[0])
            before = vol['percent']
            step = int(vol['step_pct'])
            up = 1 if before + step <= 100 else -1
            got = ipc(QML, 'topbar', 'volumeWheel', str(120 * up))
            time.sleep(0.4)
            mid = ipc(QML, 'topbar', 'state')['volume']
            shot('volume-chip')
            check('one wheel notch on the island steps the volume and shows the percentage chip (sound item hidden)',
                  got == before + up * step and mid['osd'] and mid['chip'] and not mid['sound_item']
                  and mid['text'].endswith(f'{before + up * step}%'), (before, got, mid))
            time.sleep(max(0.5, vol['osd_ms'] / 1000) + 0.7)
            gone = ipc(QML, 'topbar', 'state')['volume']
            check('the chip hides after volume_osd_ms', not gone['osd'] and not gone['chip'], gone)
            back = ipc(QML, 'topbar', 'volumeWheel', str(-120 * up))
            time.sleep(0.4)
            check('the opposite notch restores the original volume', back == before, (before, back))
            # high-resolution wheel: two half notches make one step
            half1 = ipc(QML, 'topbar', 'volumeWheel', str(60 * up))
            half2 = ipc(QML, 'topbar', 'volumeWheel', str(60 * up))
            ipc(QML, 'topbar', 'volumeWheel', str(-120 * up))
            check('high-resolution wheel deltas accumulate to one notch', half1 == -1 and half2 == before + up * step,
                  (half1, half2))
            time.sleep(0.4)
            nb = dict(base_bridge)
            nb['topbar'] = dict(base_bridge.get('topbar') or {}, volume_scroll=False)
            bridge.write_text(json.dumps(nb))
            time.sleep(1.5)
            neg = ipc(QML, 'topbar', 'volumeWheel', str(120 * up))
            neg_state = ipc(QML, 'topbar', 'state')['volume']
            check('NEGATIVE CONTROL: volume_scroll = false ignores the wheel (no step, no chip)',
                  neg == -1 and not neg_state['chip'] and neg_state['percent'] == before, (neg, neg_state))
            bridge.write_text(json.dumps(base_bridge))
            time.sleep(1.0)
            final = ipc(QML, 'topbar', 'state')['volume']['percent']
            check('volume left as found', final == before, (before, final))

            # wheel-up never lowers a sink that sits above volume_max_pct (set the maximum below the current level)
            if before > step:
                nb = dict(base_bridge)
                nb['topbar'] = dict(base_bridge.get('topbar') or {}, volume_max_pct=before - step)
                bridge.write_text(json.dumps(nb))
                time.sleep(1.5)
                over = ipc(QML, 'topbar', 'volumeWheel', '120')
                time.sleep(0.4)
                over_state = ipc(QML, 'topbar', 'state')['volume']
                check('wheel-up on a sink above volume_max_pct keeps its level (never lowers it)',
                      over == before and over_state['percent'] == before, (before, before - step, over, over_state['percent']))
                bridge.write_text(json.dumps(base_bridge))
                time.sleep(1.0)
                restore_volume(saved_volume[0])
                time.sleep(0.4)

            # the real wheel over the island (zwlr_virtual_pointer), not the IPC
            centre = island_centre(QML)
            if vptr and centre:
                level0 = volume_now()[0]
                real_wheel(centre[0], centre[1], up)
                time.sleep(0.6)
                level1 = volume_now()[0]
                chip = ipc(QML, 'topbar', 'state')['volume']
                shot('volume-real-wheel')
                check('a real wheel notch over the island steps the volume once and shows the chip',
                      level0 is not None and level1 is not None and round((level1 - level0) * 100) == up * step and chip['chip'],
                      (level0, level1, chip))
                real_wheel(centre[0], centre[1], -up)
                time.sleep(0.6)
                check('the opposite real notch restores it', volume_now()[0] == level0, (level0, volume_now()))
            else:
                check('real pointer available for the wheel check (gcc, wayland-scanner, island mapped)', False,
                      (vptr, centre))
            restore_volume(saved_volume[0])
            time.sleep(0.4)
            check('volume and mute left as found (WirePlumber)', volume_now() == saved_volume[0],
                  (saved_volume[0], volume_now()))
            if Path(OLD_QML).is_file() and Path(OLD_QML).resolve() != Path(QML).resolve():
                start_shell(OLD_QML, 'old')
                old = sh([QS, 'ipc', '--path', OLD_QML, '--any-display', 'call', 'topbar', 'volumeWheel', '120'], env=shell_env)
                old_state = ipc(OLD_QML, 'topbar', 'state') or {}
                check('NEGATIVE CONTROL: the previous shell QML has no scroll-volume percentage (no IPC, no volume state)',
                      # quickshell ipc exits 0 and prints "Function not found." for an unknown IPC function
                      (old.returncode != 0 or 'not found' in (old.stdout + old.stderr).lower()) and 'volume' not in old_state,
                      (old.returncode, old.stdout[:120], old.stderr[:120]))
                centre = island_centre(OLD_QML)
                if vptr and centre:
                    level0 = volume_now()[0]
                    real_wheel(centre[0], centre[1], up)
                    time.sleep(0.6)
                    level1 = volume_now()[0]
                    restore_volume(saved_volume[0])
                    check('NEGATIVE CONTROL: the previous shell QML ignores the same real wheel over the island',
                          level0 is not None and level1 == level0, (level0, level1))
                shell = start_shell(QML, 'tree3')

        # ---- device battery glyphs ------------------------------------------------------------------------------
        st = ipc(QML, 'topbar', 'state')
        bats = st.get('batteries', []) if isinstance(st, dict) else []
        levels = {chr(c) for c in range(0xF0079, 0xF0083)} | {'\U000f008e', '\U000f0084'}
        if not bats:
            print('SKIP batteries: UPower reports no battery device on this host')
        for b in bats:
            system = b['kind'] == 'BAT'
            want = b['level_icon'] if system else b['icon'] + ' ' + b['level_icon']
            check(f"battery '{b['name']}' shows {'its level glyph' if system else 'a device glyph and a level glyph'}",
                  b['glyphs'] == want and b['level_icon'] in levels and (system or b['icon'] not in levels), b)
        shot('hud-final')
        (OUT / 'result.json').write_text(json.dumps([{'check': n, 'ok': o, 'detail': d_} for n, o, d_ in results], indent=1))
    except Exception as e:  # report, then clean up
        check('driver', False, repr(e))
    finally:
        if saved_volume[0]:
            restore_volume(saved_volume[0])
        if env:
            for pid in shells_in(str(d)):
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        if nested_pid:
            try:
                os.kill(nested_pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        try:
            launcher.wait(timeout=15)
        except subprocess.TimeoutExpired:
            launcher.terminate()
    print(f'evidence: {OUT}')


main()
failed = [r for r in results if not r[1]]
print(f"{len(results) - len(failed)}/{len(results)} passed")
sys.exit(1 if failed else 0)
