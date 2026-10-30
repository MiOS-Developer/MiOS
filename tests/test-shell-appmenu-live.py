#!/usr/bin/env python3
# AI-hint: LIVE opt-in test (MIOS_LIVE_NESTED=1) of the global application-menu pills in an owned nested PiP session:
# a real GTK application exports a menubar (org.gtk.Menus), the real `miosd appmenu serve` exporter writes it to the
# nested runtime's appmenu.json, the shell under test (this tree's Nested.qml) shows the app's menus as glass pills
# and the exported item reaches the application through `miosd appmenu activate`. Negative controls: an app that
# exports no menubar gets the name pill only (nothing invented); with app_menu not listed in the island items the
# pills stay hidden; the live tree's Nested.qml (main) has no app-menu surface at all. Everything runs on a private
# D-Bus session bus, so the primary session's exporter and registrar are never touched.
# AI-related: usr/share/mios/quickshell/AppMenu.qml, usr/share/mios/quickshell/Nested.qml,
# src/mios-rs/miosd/src/appmenu.rs, tests/fixtures/shell-appmenu/menu_app.py, tests/test-shell-hud-live.py
# AI-functions: main, check, shells_in
"""Usage:
  MIOS_LIVE_NESTED=1 env -u MIOS_TOML python3 tests/test-shell-appmenu-live.py
Optional: MIOS_QUICKSHELL (default /usr/bin/quickshell), MIOS_MIOSD (default /usr/bin/miosd), MIOS_QS_QML (default this
tree's Nested.qml), MIOS_QS_OLD_QML (default /usr/share/mios/quickshell/Nested.qml), MIOS_LIVE_OUT (evidence dir)."""
import json
import os
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
OUT = Path(os.environ.get('MIOS_LIVE_OUT') or tempfile.mkdtemp(prefix='mios-appmenu-live-'))
OUT.mkdir(parents=True, exist_ok=True)
QS = os.environ.get('MIOS_QUICKSHELL', '/usr/bin/quickshell')
MIOSD = os.environ.get('MIOS_MIOSD', '/usr/bin/miosd')
QML = os.environ.get('MIOS_QS_QML', str(TREE / 'usr/share/mios/quickshell/Nested.qml'))
OLD_QML = os.environ.get('MIOS_QS_OLD_QML', '/usr/share/mios/quickshell/Nested.qml')
APP = str(TREE / 'tests/fixtures/shell-appmenu/menu_app.py')
SSOT = str(TREE / 'usr/share/mios/mios.toml')
UID_RUNTIME = os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')
results = []


def check(name, ok, detail=''):
    results.append((name, bool(ok), str(detail)[:600]))
    print(f"{'PASS' if ok else 'FAIL'} {name} {str(detail)[:600]}", flush=True)


def sh(a, env=None, timeout=30):
    return subprocess.run(a, capture_output=True, text=True, env=env, timeout=timeout)


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
    """The virtual-pointer fixture (tests/fixtures/shell-hud/vptr.c); None without gcc / wayland-scanner."""
    import shutil
    if not (shutil.which('gcc') and shutil.which('wayland-scanner') and shutil.which('pkg-config')):
        return None
    d.mkdir(parents=True, exist_ok=True)
    fx = TREE / 'tests/fixtures/shell-hud'
    xml = str(fx / 'wlr-virtual-pointer-unstable-v1.xml')
    ok = sh(['wayland-scanner', 'client-header', xml, str(d / 'vp-client-protocol.h')]).returncode == 0
    ok = ok and sh(['wayland-scanner', 'private-code', xml, str(d / 'vp-protocol.c')]).returncode == 0
    flags = sh(['pkg-config', '--cflags', '--libs', 'wayland-client']).stdout.split()
    ok = ok and sh(['gcc', '-O1', f'-I{d}', '-o', str(d / 'vptr'), str(fx / 'vptr.c'), str(d / 'vp-protocol.c'),
                    *flags]).returncode == 0
    return str(d / 'vptr') if ok else None


def main():
    launch_env = dict(os.environ, MIOS_VENDOR_TOML=SSOT)
    launch_env.pop('MIOS_TOML', None)
    launcher = subprocess.Popen(['/usr/bin/mios-gen', 'nested-session', '--layout', 'pip'], env=launch_env,
                                stdout=open(OUT / 'launcher.log', 'w'), stderr=subprocess.STDOUT, start_new_session=True)
    d = Path(UID_RUNTIME) / f'mios-nested-desktop-{launcher.pid}'
    env = nested_pid = None
    helpers = []  # our own processes: private bus, exporter, fixture apps, shells
    bus_pid = None
    try:
        for _ in range(300):
            if (d / 'session.json').is_file():
                s = json.loads((d / 'session.json').read_text())
                nested_pid = s['instance']['pid']
                env = dict(launch_env, XDG_RUNTIME_DIR=str(d), HYPRLAND_INSTANCE_SIGNATURE=s['instance']['instance'],
                           WAYLAND_DISPLAY=s['instance']['wl_socket'], QT_QPA_PLATFORM='wayland', GDK_BACKEND='wayland')
                env.pop('DISPLAY', None)
                break
            time.sleep(0.2)
        check('owned nested PiP session', env is not None, d.name)
        if env is None:
            return

        # A private session bus: the exporter, the fixture apps and the shell under test share it; the primary
        # session's mios-appmenu (and its AppMenu registrar) never see any of this.
        r = sh(['dbus-daemon', '--session', '--fork', '--print-address=1', '--print-pid=1',
                f'--address=unix:path={OUT}/bus'])
        lines = r.stdout.split()
        check('private D-Bus session bus', r.returncode == 0 and len(lines) >= 2, r.stderr[:200])
        if r.returncode != 0:
            return
        bus_pid = int(lines[-1])
        env['DBUS_SESSION_BUS_ADDRESS'] = lines[0]
        env.pop('AT_SPI_BUS_ADDRESS', None)
        env['NO_AT_BRIDGE'] = '1'

        def hc(*a):
            return sh(['hyprctl', *a], env=env).stdout

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

        def shot(name):
            sh(['grim', str(OUT / f'{name}.png')], env=env)

        def stop_shells():
            for pid in shells_in(str(d)):
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            for _ in range(50):
                if not shells_in(str(d)):
                    break
                time.sleep(0.1)

        def ipc(qml, *call):
            r = sh([QS, 'ipc', '--path', qml, '--any-display', 'call', *call], env=env)
            try:
                return json.loads(r.stdout)
            except ValueError:
                return None if r.returncode or 'not found' in r.stdout.lower() else r.stdout.strip()

        def monitor():
            try:
                return (json.loads(hc('-j', 'monitors') or '[{}]') or [{}])[0]
            except ValueError:
                return {}

        def logical():
            m = monitor()
            sc = m.get('scale') or 1
            w, h = (m.get('height', 0), m.get('width', 0)) if m.get('transform', 0) % 2 else (m.get('width', 0), m.get('height', 0))
            return round(w / sc), round(h / sc)

        # The PiP output is usually narrower than the [desktop.mobile] breakpoint, so the session's own policy calls it
        # mobile, which hides the sound item and the app menu. The app-menu checks are about the desktop island: hand
        # the shell under test a desktop verdict for this output (the policy file itself is left untouched).
        desk_json = OUT / 'desktop-outputs.json'

        def write_desktop_verdict():
            try:
                j = json.loads((d / 'mios-mobile-outputs.json').read_text())
            except (OSError, ValueError):
                j = {'version': 1, 'outputs': {}, 'config': {}}
            verdicts = {name: bool(o.get('mobile')) for name, o in j.get('outputs', {}).items()}
            for o in j.get('outputs', {}).values():
                o['mobile'] = False
            desk_json.write_text(json.dumps(j))
            return verdicts

        vptr = build_vptr(OUT / 'vptr')

        def ptr(x, y, mode):
            w, h = logical()
            r = sh([vptr, monitor().get('name', ''), str(int(x)), str(int(y)), mode],
                   env=dict(env, VPTR_W=str(w), VPTR_H=str(h)))
            return r.returncode == 0

        def click(x, y):
            # Hyprland sends no pointer enter to a surface that mapped under a stationary pointer: leave, re-enter
            ptr(x, y + 300, 'hover')
            time.sleep(0.2)
            ptr(x, y, 'hover')
            time.sleep(0.2)
            return ptr(x, y, 'click')

        def start_shell(qml, label, items, desktop=True):
            stop_shells()
            senv = dict(env, MIOS_NESTED_TOPBAR_ITEMS=items)
            if desktop:
                senv['MIOS_MOBILE_OUTPUTS_JSON'] = str(desk_json)
            p = subprocess.Popen(['bwrap', '--die-with-parent', '--bind', '/', '/', '--dev-bind', '/dev', '/dev',
                                  '--ro-bind', str(d / 'input'), '/dev/input', '--', QS, '--no-duplicate', '--path', qml],
                                 env=senv, stdout=open(OUT / f'shell-{label}.log', 'w'), stderr=subprocess.STDOUT)
            helpers.append(p)
            for _ in range(150):
                if ipc(qml, 'topbar', 'state') is not None:
                    break
                time.sleep(0.2)
            time.sleep(1.5)
            return p

        def snapshot():
            try:
                return json.loads((d / 'mios' / 'appmenu.json').read_text())
            except (OSError, ValueError):
                return {}

        def launch_app(app_id, marker, *extra):
            p = subprocess.Popen(['python3', '-I', APP, app_id, str(marker), *extra], env=env,
                                 stdout=open(OUT / f'app-{app_id}.log', 'w'), stderr=subprocess.STDOUT)
            helpers.append(p)
            c = None
            for _ in range(100):
                c = next((c for c in clients() if c['class'] == app_id), None)
                if c:
                    break
                time.sleep(0.2)
            if c:
                hc('eval', f"hl.dispatch(hl.dsp.focus({{window='address:{c['address']}'}}))")
            return p, c

        def wait_snapshot(app_id, want_menus, secs=15):
            s = {}
            for _ in range(int(secs * 5)):
                s = snapshot()
                if s.get('app_id') == app_id and (bool(s.get('menus')) == want_menus):
                    return s
                time.sleep(0.2)
            return s

        def wait_state(qml, pred, secs=8):
            st = None
            for _ in range(int(secs * 5)):
                st = ipc(qml, 'appmenu', 'state')
                if isinstance(st, dict) and pred(st):
                    return st
                time.sleep(0.2)
            return st

        for _ in range(100):
            if shells_in(str(d)) and any(c['class'] == 'mios-nested-terminal' for c in clients()):
                break
            time.sleep(0.2)

        policy = write_desktop_verdict()
        check('the shell under test gets a desktop verdict for the PiP output (policy verdict recorded)',
              desk_json.is_file(), {'policy_mobile': policy, 'logical': logical()})
        exporter = subprocess.Popen([MIOSD, 'appmenu', 'serve'], env=env,
                                    stdout=open(OUT / 'exporter.log', 'w'), stderr=subprocess.STDOUT)
        helpers.append(exporter)
        start_shell(QML, 'tree', 'clock,fps,workspaces,app_menu')

        # ---- an app that exports a real menubar --------------------------------------------------------------------
        marker = OUT / 'marker.txt'
        marker.unlink(missing_ok=True)
        _app, c = launch_app('org.mios.MenuFixture', marker)
        check('fixture app with a menubar is mapped in the nested session', c is not None)
        snap = wait_snapshot('org.mios.MenuFixture', True)
        labels = [m.get('label') for m in snap.get('menus', [])]
        check('miosd appmenu exports the focused app\'s real menubar (GTK org.gtk.Menus)',
              snap.get('source') == 'gtk' and labels == ['File', 'View'] and bool(c) and snap.get('address') == c['address'],
              {k: snap.get(k) for k in ('app_id', 'source', 'address')} | {'menus': labels})
        st = wait_state(QML, lambda s: s.get('menus') == ['File', 'View'] and s.get('visible'))
        check('the shell shows the app name and one glass pill per exported menu',
              isinstance(st, dict) and st.get('visible') and st.get('menus') == ['File', 'View']
              and st.get('app_id') == 'org.mios.MenuFixture', st)
        ly = layers()
        check('the app-menu layer surface is mapped (mios-appmenu)', 'mios-appmenu' in ly, sorted(ly))
        shot('appmenu-pills')
        ipc(QML, 'appmenu', 'openMenu', '0')
        time.sleep(1.0)
        st_open = ipc(QML, 'appmenu', 'state')
        check('opening the first pill grows its dropdown', isinstance(st_open, dict) and st_open.get('open_menu') == 0
              and (st_open.get('dropdown') or 0) > 0.5, st_open)
        shot('appmenu-dropdown')
        ipc(QML, 'appmenu', 'close')
        item = next((i for i in (snap.get('menus') or [{}])[0].get('items', []) if i.get('label') == 'Mark'), None)
        disabled = next((i for i in (snap.get('menus') or [{}])[0].get('items', []) if i.get('label') == 'Disabled'), None)
        check('exported item states come from the app (Mark enabled, Disabled disabled)',
              bool(item) and item.get('enabled') and bool(disabled) and not disabled.get('enabled'), (item, disabled))
        if item:
            r = sh([MIOSD, 'appmenu', 'activate', snap['app_id'], item['id'], '--window', snap['address']], env=env)
            for _ in range(25):
                if marker.is_file():
                    break
                time.sleep(0.2)
            check('activating the exported item reaches the application', r.returncode == 0 and marker.is_file(),
                  (r.returncode, r.stderr[:200]))

        # ---- the same through a REAL pointer: click the pill, then the item ------------------------------------------
        if vptr and item and isinstance(st, dict):
            marker.unlink(missing_ok=True)
            ly = layers().get('mios-appmenu')
            opened = None
            if ly:
                y = ly[1] + min(ly[3], 40) / 2
                left = int(ly[0] + st.get('row_left', 8))
                for x in range(left, left + int(st.get('width', 300)), 6):   # scan the pill row left to right
                    click(x, y)
                    time.sleep(0.35)
                    s2 = ipc(QML, 'appmenu', 'state') or {}
                    if s2.get('open_menu', -1) >= 0:
                        opened = (x, y, s2.get('open_menu'))
                        break
            shot('appmenu-real-click-open')
            check('a REAL pointer click on the File pill opens its dropdown', opened is not None and opened[2] == 0, opened)
            hit = None
            if opened and opened[2] == 0:
                time.sleep(0.6)
                for y in range(int(opened[1] + 14), int(opened[1] + 140), 6):   # the "Mark" row sits under the pill
                    if (ipc(QML, 'appmenu', 'state') or {}).get('open_menu', -1) != 0:
                        click(opened[0], opened[1])
                        time.sleep(0.6)
                    ptr(opened[0] + 30, y, 'hover')
                    time.sleep(0.25)
                    ptr(opened[0] + 30, y, 'click')
                    for _ in range(6):
                        if marker.is_file():
                            break
                        time.sleep(0.2)
                    if marker.is_file():
                        hit = (opened[0] + 30, y)
                        break
            check('a REAL pointer click on the dropdown item "Mark" reaches the application', hit is not None, hit)
            ipc(QML, 'appmenu', 'close')
        else:
            check('real pointer available for the click checks (gcc, wayland-scanner, exported item)', False, (vptr, item))

        # ---- NEGATIVE CONTROL: an app without a menubar gets its name only -------------------------------------------
        _bare, c2 = launch_app('org.mios.MenuFixtureBare', OUT / 'marker-bare.txt', '--no-menus')
        snap2 = wait_snapshot('org.mios.MenuFixtureBare', False)
        st2 = wait_state(QML, lambda s: s.get('app_id') == 'org.mios.MenuFixtureBare')
        check('NEGATIVE CONTROL: an app that exports no menubar shows its name pill only (no invented menus)',
              bool(c2) and snap2.get('app_id') == 'org.mios.MenuFixtureBare' and not snap2.get('menus')
              and isinstance(st2, dict) and st2.get('visible') and st2.get('menus') == [], (snap2, st2))
        shot('appmenu-bare')
        if c:
            hc('eval', f"hl.dispatch(hl.dsp.focus({{window='address:{c['address']}'}}))")
            back = wait_state(QML, lambda s: s.get('menus') == ['File', 'View'])
            check('focus back on the exporting app brings its pills back', isinstance(back, dict)
                  and back.get('menus') == ['File', 'View'], back)

        # ---- NEGATIVE CONTROL: app_menu not listed in the island items -------------------------------------------------
        start_shell(QML, 'tree-off', 'clock,fps,workspaces')
        st3 = wait_state(QML, lambda s: s.get('app_id') == 'org.mios.MenuFixture')
        check('NEGATIVE CONTROL: without app_menu in the island items the pills stay hidden',
              isinstance(st3, dict) and not st3.get('visible'), st3)

        # ---- NEGATIVE CONTROL: under the session's own (mobile) verdict the desktop app menu is hidden --------------
        if any(policy.values()):
            start_shell(QML, 'tree-mobile', 'clock,fps,workspaces,app_menu', desktop=False)
            st4 = wait_state(QML, lambda s: s.get('app_id') == 'org.mios.MenuFixture')
            check('NEGATIVE CONTROL: with the PiP output\'s own mobile verdict the app-menu pills stay hidden '
                  '(why the checks above pass a desktop verdict)', isinstance(st4, dict) and not st4.get('visible'), st4)

        # ---- NEGATIVE CONTROL: main's nested shell has no app-menu surface ----------------------------------------
        if Path(OLD_QML).is_file() and Path(OLD_QML).resolve() != Path(QML).resolve():
            start_shell(OLD_QML, 'old', 'clock,fps,workspaces,app_menu')
            old = ipc(OLD_QML, 'appmenu', 'state')
            time.sleep(1.0)
            check('NEGATIVE CONTROL: the previous nested shell QML shows no app menus (no appmenu IPC, no layer)',
                  old is None and 'mios-appmenu' not in layers(), (old, sorted(layers())))
            shot('appmenu-old-shell')
    except Exception as e:  # noqa: BLE001 - report, then always clean up
        check('driver', False, repr(e))
    finally:
        for p in reversed(helpers):
            if p.poll() is None:
                p.terminate()
        for p in helpers:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        if env:
            for pid in shells_in(str(d)):
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        if bus_pid:
            try:
                os.kill(bus_pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        if nested_pid:
            try:
                os.kill(nested_pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        try:
            launcher.wait(timeout=20)
        except subprocess.TimeoutExpired:
            os.killpg(launcher.pid, signal.SIGTERM)
        (OUT / 'result.json').write_text(json.dumps([{'name': n, 'ok': o, 'detail': t} for n, o, t in results], indent=1))
        print(f'evidence: {OUT}')
        print(f"{sum(o for _, o, _ in results)}/{len(results)} passed")
    sys.exit(0 if results and all(o for _, o, _ in results) else 1)


if __name__ == '__main__':
    main()
