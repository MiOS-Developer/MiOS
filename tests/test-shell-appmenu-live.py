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

        def start_shell(qml, label, items):
            stop_shells()
            senv = dict(env, MIOS_NESTED_TOPBAR_ITEMS=items)
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
