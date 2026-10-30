#!/usr/bin/env python3
# AI-hint: LIVE opt-in test (MIOS_LIVE_NESTED=1): runs the installed Quickshell (negative control) and a staged
# candidate (MIOS_QUICKSHELL_CANDIDATE) with the MiOS nested shell QML in an owned nested PiP session, drives rapid
# toplevel churn (windows hopping between fresh named workspaces) and an event-socket stall (the shell is stopped
# while the churn goes on, so Hyprland closes its event socket as it does for any client that falls behind), then
# checks the shell's Qt property bindings (EdgeReveal.qml binding probe, IPC reserve-<output> probe) and the
# consequences: a real fullscreen window must auto-hide the HUD rows and a maximized window must stay inside them.
# Negative controls: the installed 0.2.1 (dacfa9d) leaks a Qt property update group in
# HyprlandToplevel::updateFromObject() and freezes; an upstream build without the carried event-socket reconnect
# (MIOS_QUICKSHELL_UNPATCHED) goes deaf after the socket drop. The patched candidate must do neither.
# The primary compositor is untouched.
# AI-related: src/quickshell/check-update-groups.py, src/quickshell/patches/hyprland-toplevel-update-group.patch,
# src/quickshell/patches/hyprland-event-socket-reconnect.patch,
# automation/66-bake-quickshell.sh, usr/share/mios/quickshell/EdgeReveal.qml, tests/test-shell-reserve.py,
# usr/share/doc/mios/quickshell-candidate.md
"""Usage:
  MIOS_LIVE_NESTED=1 MIOS_QUICKSHELL_CANDIDATE=/path/to/staged/usr/bin/quickshell \
    env -u MIOS_TOML python3 tests/test-quickshell-candidate-live.py
Optional: MIOS_QUICKSHELL_OLD (default /usr/bin/quickshell), MIOS_QUICKSHELL_UNPATCHED (the same upstream ref built
without the reconnect patch: negative control), MIOS_QUICKSHELL_QML (default the live Nested.qml),
MIOS_LIVE_OUT (evidence dir), MIOS_CHURN_SECONDS (per round, default 15), MIOS_CHURN_ROUNDS (default 3)."""
import json
import os
import random
import re
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

OUT = Path(os.environ.get('MIOS_LIVE_OUT') or tempfile.mkdtemp(prefix='mios-qs-candidate-'))
OUT.mkdir(parents=True, exist_ok=True)
OLD = os.environ.get('MIOS_QUICKSHELL_OLD', '/usr/bin/quickshell')
NEW = os.environ.get('MIOS_QUICKSHELL_CANDIDATE', '')
UNPATCHED = os.environ.get('MIOS_QUICKSHELL_UNPATCHED', '')
QML = os.environ.get('MIOS_QUICKSHELL_QML', '/usr/share/mios/quickshell/Nested.qml')
SSOT = os.environ.get('MIOS_VENDOR_TOML', str(Path(__file__).resolve().parents[1] / 'usr/share/mios/mios.toml'))
SECS = float(os.environ.get('MIOS_CHURN_SECONDS', '15'))
ROUNDS = int(os.environ.get('MIOS_CHURN_ROUNDS', '3'))
results = []


def check(name, ok, detail=''):
    results.append((name, bool(ok), str(detail)))
    print(f"{'PASS' if ok else 'FAIL'} {name} {detail}", flush=True)


def sh(a, env=None, timeout=30):
    return subprocess.run(a, capture_output=True, text=True, env=env, timeout=timeout)


def shells_in(runtime):
    """Quickshell processes whose XDG_RUNTIME_DIR is this nested session's (never the primary shell)."""
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
    if not NEW or not os.access(NEW, os.X_OK):
        check('candidate binary given (MIOS_QUICKSHELL_CANDIDATE)', False, NEW or 'unset')
        return
    check('installed and candidate versions', True,
          f"{sh([OLD, '--version']).stdout.strip()} | {sh([NEW, '--version']).stdout.strip()}")
    launch_env = dict(os.environ, MIOS_VENDOR_TOML=SSOT)
    launch_env.pop('MIOS_TOML', None)
    launcher = subprocess.Popen(['/usr/bin/mios-gen', 'nested-session', '--layout', 'pip'], env=launch_env,
                                stdout=open(OUT / 'launcher.log', 'w'), stderr=subprocess.STDOUT, start_new_session=True)
    d = Path(os.environ.get('XDG_RUNTIME_DIR', '/run/user/%d' % os.getuid())) / f'mios-nested-desktop-{launcher.pid}'
    env = nested_pid = shell = None
    try:
        for _ in range(300):
            if (d / 'session.json').is_file():
                s = json.loads((d / 'session.json').read_text())
                nested_pid = s['instance']['pid']
                env = dict(launch_env, XDG_RUNTIME_DIR=str(d), HYPRLAND_INSTANCE_SIGNATURE=s['instance']['instance'],
                           WAYLAND_DISPLAY=s['instance']['wl_socket'], QT_QPA_PLATFORM='wayland',
                           PIPEWIRE_RUNTIME_DIR=os.environ.get('XDG_RUNTIME_DIR', '/run/user/%d' % os.getuid()))
                env.pop('DISPLAY', None)
                break
            time.sleep(0.2)
        check('owned nested PiP session', env is not None, d.name)
        if env is None:
            return

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
            except (ValueError, KeyError):
                pass
            return out

        def monitor():
            return (json.loads(hc('-j', 'monitors') or '[{}]') or [{}])[0]

        # the session's own shell goes; each binary under test runs in the same sandbox the session uses
        for _ in range(100):
            if shells_in(str(d)):
                break
            time.sleep(0.2)
        time.sleep(2.0)
        for pid in shells_in(str(d)):
            os.kill(pid, signal.SIGTERM)
        time.sleep(1.0)
        check("session's own shell stopped (only this nested session's)", not shells_in(str(d)))
        for _ in range(4):
            hc('eval', "hl.dispatch(hl.dsp.exec_cmd('alacritty --class mios-qs-churn -e sleep 900'))")
        for _ in range(100):
            if sum(c['class'] == 'mios-qs-churn' for c in clients()) >= 4:
                break
            time.sleep(0.2)
        churners = [c['address'] for c in clients() if c['class'] == 'mios-qs-churn']
        check('churn windows mapped', len(churners) >= 4, len(churners))
        output = monitor().get('name', '')

        def ipc(binary, *call):
            r = sh([binary, 'ipc', '--path', QML, '--any-display', 'call', *call], env=env)
            try:
                return json.loads(r.stdout)
            except ValueError:
                return None

        def churn(seconds):
            n, t_end = 0, time.monotonic() + seconds
            while time.monotonic() < t_end:
                batch = []
                for a in churners:
                    n += 1
                    batch.append(f"hl.dispatch(hl.dsp.window.move({{workspace='name:churn{n % 7}', follow=false, window='address:{a}'}}))")
                hc('eval', ' '.join(batch))
                n += random.randint(0, 3)
            hc('eval', ' '.join(f"hl.dispatch(hl.dsp.window.move({{workspace='1', follow=false, window='address:{a}'}}))" for a in churners))
            return n

        def fullscreen_hides_hud():
            term = next((c for c in clients() if c['class'] == 'mios-nested-terminal'), None) or \
                next((c for c in clients() if c['class'] != 'mios-qs-churn'), None)
            if not term:
                return None, 'no window to fullscreen'
            a = term['address']
            hc('eval', f"hl.dispatch(hl.dsp.focus({{window='address:{a}'}}))")
            hc('eval', f"hl.dispatch(hl.dsp.window.fullscreen({{mode='fullscreen', action='set', window='address:{a}'}}))")
            time.sleep(2.5)
            ls, res = layers(), monitor().get('reserved')
            hc('eval', f"hl.dispatch(hl.dsp.window.fullscreen({{mode='fullscreen', action='unset', window='address:{a}'}}))")
            time.sleep(1.5)
            hidden = 'mios-topbar' not in ls and res == [0, 0, 0, 0]
            return hidden, f"layers={sorted(ls)} reserved={res}"

        def out_size():
            m = monitor()
            return (m.get('width'), m.get('height'), m.get('scale'), m.get('transform'))

        def maximize_inside():
            term = next((c for c in clients() if c['class'] == 'mios-nested-terminal'), None)
            if not term:
                return None, 'no window to maximize'
            a = term['address']
            detail = ''
            for _ in range(3):   # the PiP host may resize the nested output meanwhile: measure on a stable size
                before = out_size()
                hc('eval', f"hl.dispatch(hl.dsp.window.fullscreen({{mode='maximized', action='set', window='address:{a}'}}))")
                time.sleep(1.5)
                c = next((c for c in clients() if c['address'] == a), {})
                m = monitor()
                res = m.get('reserved') or [0, 0, 0, 0]
                sc = m.get('scale') or 1
                hgt = round((m.get('width') if m.get('transform', 0) % 2 else m.get('height', 0)) / sc)
                hc('eval', f"hl.dispatch(hl.dsp.window.fullscreen({{mode='maximized', action='unset', window='address:{a}'}}))")
                time.sleep(1.0)
                ok = (c.get('fullscreen') == 1 and res[1] > 0 and res[3] > 0 and c['at'][1] >= res[1]
                      and c['at'][1] + c['size'][1] <= hgt - res[3])
                detail = f"window={c.get('at')}+{c.get('size')} reserved={res} height={hgt}"
                if out_size() == before:
                    return ok, detail
            return None, 'output kept resizing: ' + detail

        def stall(pid, seconds=4.0):
            # stop the shell while the churn goes on: Hyprland closes the event socket of a client that stops reading
            os.kill(pid, signal.SIGSTOP)
            try:
                return churn(seconds)
            finally:
                os.kill(pid, signal.SIGCONT)

        def no_shells():
            # every earlier shell of this session gone before the next one starts (--no-duplicate, stall target)
            for i in range(100):
                left = shells_in(str(d))
                if not left:
                    return
                for pid in left:
                    try:
                        os.kill(pid, signal.SIGKILL if i > 50 else signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                time.sleep(0.1)

        def run(binary, label):
            no_shells()
            log = open(OUT / f'shell-{label}.log', 'w')
            p = subprocess.Popen(['bwrap', '--die-with-parent', '--bind', '/', '/', '--dev-bind', '/dev', '/dev',
                                  '--ro-bind', str(d / 'input'), '/dev/input', '--', binary, '--no-duplicate', '--path', QML],
                                 env=env, stdout=log, stderr=subprocess.STDOUT)
            ready = None
            for _ in range(150):
                ready = ipc(binary, f'reserve-{output}', 'probe')
                if ready is not None:
                    break
                time.sleep(0.2)
            check(f'[{label}] shell up (reserve-{output} IPC)', ready is not None, ready)
            qs_pid = next((q for q in shells_in(str(d))), None)
            time.sleep(3.0)
            pre = ipc(binary, f'reserve-{output}', 'probe') or {}
            check(f'[{label}] bindings follow their inputs before churn', pre.get('qt_bindings_frozen') is False, pre)
            frozen_after, moves = None, 0
            for rnd in range(ROUNDS):
                moves += churn(SECS)
                time.sleep(2.0)
                frozen_after = (ipc(binary, f'reserve-{output}', 'probe') or {}).get('qt_bindings_frozen')
                if frozen_after:
                    break
            moves += stall(qs_pid) if qs_pid else 0
            time.sleep(4.0)
            sh(['grim', str(OUT / f'{label}-after-churn.png')], env=env)
            hid, detail = fullscreen_hides_hud()
            sh(['grim', str(OUT / f'{label}-after-fullscreen.png')], env=env)
            inside, mdetail = maximize_inside()
            sh(['grim', str(OUT / f'{label}-after-maximize.png')], env=env)
            alive = p.poll() is None
            log.flush()
            text = (OUT / f'shell-{label}.log').read_text(errors='replace')
            dropped = 'PeerClosedError' in text or 'event socket disconnected' in text
            reconnected = 'event socket reconnected' in text
            p.send_signal(signal.SIGTERM)
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
            no_shells()
            return {'frozen': frozen_after, 'moves': moves, 'hud_hid_on_fullscreen': hid, 'detail': detail, 'alive': alive,
                    'maximize_inside': inside, 'maximize_detail': mdetail, 'socket_dropped': dropped, 'reconnected': reconnected}

        def regression(binary, label, seconds=20.0):
            """The full desktop shell (Config.qml next to the tested QML) on a private session bus and a private, empty
            XDG_CONFIG_HOME (no user TOML can be written), inside this nested session: QML diagnostics only."""
            full = str(Path(QML).with_name('Config.qml'))
            cfg = tempfile.mkdtemp(prefix='mios-qs-regress-cfg-')
            renv = dict(env, XDG_CONFIG_HOME=cfg, MIOS_USER_TOML=str(Path(cfg) / 'mios.toml'))
            renv.pop('DBUS_SESSION_BUS_ADDRESS', None)
            logp = OUT / f'regression-{label}.log'
            p = subprocess.Popen(['dbus-run-session', '--', 'bwrap', '--die-with-parent', '--bind', '/', '/', '--dev-bind', '/dev', '/dev',
                                  '--ro-bind', str(d / 'input'), '/dev/input', '--', binary, '--no-duplicate', '--path', full],
                                 env=renv, stdout=open(logp, 'w'), stderr=subprocess.STDOUT, start_new_session=True)
            time.sleep(seconds)
            alive = p.poll() is None
            for pid in shells_in(str(d)):
                os.kill(pid, signal.SIGTERM)
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, signal.SIGKILL)
            shutil.rmtree(cfg, ignore_errors=True)
            text = logp.read_text(errors='replace')
            bad = set()
            for l in text.splitlines():
                if 'ERROR' in l or 'is not a type' in l or 'TypeError' in l or 'ReferenceError' in l or 'not installed' in l:
                    bad.add(re.sub(r'\x1b\[[0-9;]*m', '', l).split('qml]', 1)[-1].strip()[:200])
            return {'alive': alive, 'loaded': 'Configuration Loaded' in text, 'errors': sorted(bad)}

        reg_old = regression(OLD, 'installed')
        reg_new = regression(NEW, 'candidate')
        check('[installed] full MiOS shell (Config.qml) loads (regression baseline)', reg_old['loaded'], reg_old)
        check('[candidate] full MiOS shell (Config.qml) loads and stays up', reg_new['loaded'] and reg_new['alive'], reg_new)
        new_errors = [e for e in reg_new['errors'] if e not in reg_old['errors']]
        check('[candidate] no QML diagnostic the installed Quickshell does not also print', not new_errors, new_errors[:8])

        old = run(OLD, 'installed')
        check('[installed] NEGATIVE CONTROL: churn freezes the Qt property bindings (update-group leak)', old['frozen'] is True, old)
        unp = None
        if UNPATCHED and os.access(UNPATCHED, os.X_OK):
            unp = run(UNPATCHED, 'unpatched')
            check('[unpatched] NEGATIVE CONTROL: bindings do not freeze (0.3.x carries the update-group fix)', unp['frozen'] is False, unp)
            check('[unpatched] NEGATIVE CONTROL: the stall drops the event socket', unp['socket_dropped'], unp)
            check('[unpatched] NEGATIVE CONTROL: without the reconnect the deaf shell keeps the HUD over a fullscreen window',
                  unp['hud_hid_on_fullscreen'] is False and not unp['reconnected'], unp['detail'])
        new = run(NEW, 'candidate')
        check('[candidate] shell survived the churn', new['alive'], new)
        check('[candidate] bindings still follow their inputs after the same churn', new['frozen'] is False, new)
        check('[candidate] the stall dropped the event socket and the shell reconnected', new['socket_dropped'] and new['reconnected'], new)
        check('[candidate] a real fullscreen window auto-hides the HUD rows (reserved 0)', new['hud_hid_on_fullscreen'] is True, new['detail'])
        check('[candidate] a maximized window stays inside the HUD reserved rows', new['maximize_inside'] is True, new['maximize_detail'])
        warn = [l for l in (OUT / 'shell-candidate.log').read_text(errors='replace').splitlines()
                if ('ERROR' in l or 'is not a type' in l or 'module "' in l and 'not installed' in l)]
        check('[candidate] MiOS shell QML loads without QML errors', not warn, warn[:5])
        (OUT / 'result.json').write_text(json.dumps({'installed': old, 'unpatched': unp, 'candidate': new}, indent=2))
    except Exception as e:  # report, then clean up
        check('driver', False, repr(e))
    finally:
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
