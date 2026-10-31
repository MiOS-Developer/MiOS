#!/usr/bin/env python3
# AI-hint: LIVE opt-in test (MIOS_LIVE_NESTED=1) of mios-inputd step I1 in an owned nested MiOS PiP session: a private uinput pad (xpad and DualSense layouts) drives the candidate bridge; capture/release (North tap, North hold, held L3+R3) are identical for every window kind, half-capture keeps RB/Guide/North with the app, release unsets only a bridge fullscreen, Super+C works again after release, the room stick has one sink; old binary as negative control.
# AI-related: tools/native/mios-inputd, docs/design/mios-global-bindings-tests.md (I1), tests/test-inputd-ownership.py, tests/test-global-bindings-s0-live.py
# AI-functions: check, Nested, Pad, Bridge
# Environment: MIOS_LIVE_NESTED=1 (else skip), MIOS_INPUTD_BIN = candidate (default installed), MIOS_INPUTD_OLD_BIN = pre-I1
# binary for the negative controls (default /usr/libexec/mios/mios-inputd when it lacks --live-context),
# MIOS_VENDOR_TOML = the SSOT under test (default: this tree's). The primary compositor only receives send_key_state
# events addressed to the nested parent window. No physical device is opened: the pad is a private uinput fixture.
import json
import math
import os
from pathlib import Path
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import threading
import time

TREE = Path(__file__).resolve().parents[1]
SSOT = Path(os.environ.get('MIOS_VENDOR_TOML', str(TREE / 'usr/share/mios/mios.toml')))
BIN = os.environ.get('MIOS_INPUTD_BIN', '/usr/libexec/mios/mios-inputd')
OLD = os.environ.get('MIOS_INPUTD_OLD_BIN', '/usr/libexec/mios/mios-inputd')
NORTH, RB, SELECT, START, GUIDE, L3, R3 = 0x133, 0x137, 0x13a, 0x13b, 0x13c, 0x13d, 0x13e
CAP, REL, FS = 'mios-input-captured', 'mios-input-released', 'mios-input-fullscreen'
res = []


def check(name, ok, detail=''):
    res.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'} {name} {detail}", flush=True)


def sh(argv, env=None, timeout=30, data=None):
    return subprocess.run(argv, capture_output=True, text=True, env=env, timeout=timeout, input=data)


def wait(pred, seconds=5.0, step=0.05):
    end = time.monotonic() + seconds
    while True:
        v = pred()
        if v or time.monotonic() > end:
            return v
        time.sleep(step)


if os.environ.get('MIOS_LIVE_NESTED') != '1' or not os.environ.get('HYPRLAND_INSTANCE_SIGNATURE'):
    print('skip: live nested-session test (set MIOS_LIVE_NESTED=1 inside a MiOS desktop session)')
    sys.exit(0)

tmp = Path(tempfile.mkdtemp(prefix='mios-inputd-i1-live-'))
root = tmp / 'root'
(root / 'usr/share/mios').mkdir(parents=True)
shutil.copy(SSOT, root / 'usr/share/mios/mios.toml')
home = tmp / 'home'
(home / '.config').mkdir(parents=True)
env0 = dict(os.environ, MIOS_VENDOR_TOML=str(SSOT))
env0.pop('MIOS_TOML', None)
primary = sh(['pgrep', '-x', 'Hyprland', '-o']).stdout.strip()
launcher_log = open(tmp / 'launcher.log', 'w')
launcher = subprocess.Popen(['/usr/bin/mios-gen', 'nested-session', '--layout', 'pip'], env=env0,
                            stdout=launcher_log, stderr=subprocess.STDOUT)
sdir = Path(os.environ['XDG_RUNTIME_DIR']) / f'mios-nested-desktop-{launcher.pid}'
npid = None
procs = []


class Nested:
    def __init__(self, s):
        self.pw = s['parent_window']
        self.env = dict(env0, XDG_RUNTIME_DIR=str(sdir), HYPRLAND_INSTANCE_SIGNATURE=s['instance']['instance'],
                        WAYLAND_DISPLAY=s['instance']['wl_socket'], HOME=str(home), XDG_CONFIG_HOME=str(home / '.config'))
        self.env.pop('DISPLAY', None)
        # Tools that resolve the SSOT read only the copied tree (MIOS_* overrides would beat --root).
        self.tool_env = {k: v for k, v in self.env.items() if k not in ('MIOS_VENDOR_TOML', 'MIOS_TOML')}

    def ctl(self, *a):
        return sh(['hyprctl', *a], env=self.env)

    def eval(self, lua):
        return self.ctl('eval', lua)

    def clients(self):
        try:
            return json.loads(self.ctl('-j', 'clients').stdout or '[]')
        except json.JSONDecodeError:
            return []

    def window(self, cls):
        return next((c for c in self.clients() if c.get('class') == cls), None)

    def tags(self, cls):
        return sorted(t.rstrip('*') for t in (self.window(cls) or {}).get('tags', []) if t.rstrip('*') in (CAP, REL, FS))

    def fullscreen(self, cls):
        return (self.window(cls) or {}).get('fullscreen')

    def focus(self, addr):
        self.eval(f"hl.dispatch(hl.dsp.focus({{window='address:{addr}'}}))")

    def context(self, binary=BIN, rootdir=None):
        r = sh([binary, '--root', str(rootdir or root), 'context'], env=self.tool_env)
        try:
            return json.loads(r.stdout)
        except json.JSONDecodeError:
            return {'error': r.stderr.strip()[:200]}

    def key(self, mods, key, state):
        # Bind-triggering keys reach the nested compositor as real key events into its parent window on the primary.
        sh(['hyprctl', 'eval', f"hl.dispatch(hl.dsp.send_key_state({{mods='{mods}',key='{key}',state='{state}',window='address:{self.pw}'}}))"])

    def super_chord(self, key):
        self.key('', 'Super_L', 'down')
        self.key('SUPER', key, 'down')
        self.key('SUPER', key, 'up')
        self.key('', 'Super_L', 'up')
        time.sleep(0.6)


class Pad:
    """The private uinput pad (mios-input-fixture-<layout>) the candidate binary creates; JSON lines drive it."""

    def __init__(self, layout):
        self.p = subprocess.Popen([BIN, 'fixture-pad', '--layout', layout], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True)
        procs.append(self.p)
        self.info = json.loads(self.p.stdout.readline())
        self.path = self.info['path']

    def send(self, events):
        self.p.stdin.write(json.dumps({'events': events}) + '\n')
        self.p.stdin.flush()
        return json.loads(self.p.stdout.readline())

    def tap(self, code):
        self.send([[1, code, 1]])
        time.sleep(0.05)
        self.send([[1, code, 0]])
        time.sleep(0.25)

    def hold(self, codes, ms=800):
        for c in codes:
            self.send([[1, c, 1]])
        time.sleep(ms / 1000)
        for c in codes:
            self.send([[1, c, 0]])
        time.sleep(0.3)

    def stick(self, x):
        self.send([[3, 0, x]])

    def close(self):
        self.p.stdin.close()
        self.p.wait(timeout=5)


class Bridge:
    """The candidate bridge on the fixture tier, following the nested compositor (--live-context)."""

    def __init__(self, nested, pad, pad_path):
        argv = [BIN, '--root', str(root), 'bridge', '--fixture-only', '--device', pad.path, '--live-context', '--pad-path', str(pad_path)]
        self.p = subprocess.Popen(argv, env=nested.tool_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        procs.append(self.p)
        self.actions, self.ownership, self.log = [], [], []
        threading.Thread(target=self._read, args=(self.p.stdout, True), daemon=True).start()
        threading.Thread(target=self._read, args=(self.p.stderr, False), daemon=True).start()

    def _read(self, stream, out):
        for line in stream:
            self.log.append(line.rstrip())
            try:
                v = json.loads(line)
            except json.JSONDecodeError:
                continue
            if out and 'action' in v:
                self.actions.append(v['action'])
            if not out and 'ownership' in v:
                self.ownership.append((v['ownership'], v.get('ok')))

    def mark(self):
        return len(self.actions), len(self.ownership)

    def since(self, m):
        return self.actions[m[0]:], self.ownership[m[1]:]

    def stop(self):
        self.p.send_signal(signal.SIGTERM)
        try:
            self.p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.p.kill()


def vpad_reader(source_path):
    """Open the bridge's virtual pad (mios-vctl-<source event>) read-only, never grabbed; returns an fd or None."""
    want = f"mios-vctl-{Path(source_path).name}"
    for _ in range(100):
        for name in Path('/sys/class/input').glob('event*/device/name'):
            if name.read_text().strip() == want:
                try:
                    return os.open(f"/dev/input/{name.parent.parent.name}", os.O_RDONLY | os.O_NONBLOCK)
                except PermissionError:
                    pass  # udev has not applied the uaccess ACL yet
        time.sleep(0.05)
    return None


def drain(fd):
    out = []
    while fd is not None:
        try:
            b = os.read(fd, 24 * 64)
        except BlockingIOError:
            break
        for i in range(0, len(b) - 23, 24):
            _, _, kind, code, value = struct.unpack('qqHHi', b[i:i + 24])
            out.append((kind, code, value))
    return out


def packet_lx(path):
    try:
        b = Path(path).read_bytes()
    except OSError:
        return None
    return struct.unpack('<f', b[16:20])[0] if len(b) == 68 else None


try:
    def session():
        try:
            v = json.loads((sdir / 'session.json').read_text())
        except (OSError, json.JSONDecodeError):
            return None
        return v if v.get('instance', {}).get('pid') and v.get('parent_window') else None
    s = wait(session, 240, 0.2)
    if not s:
        raise SystemExit('nested session did not publish session.json: ' + (tmp / 'launcher.log').read_text()[-600:])
    npid = s['instance']['pid']
    n = Nested(s)
    check('nested session up', n.ctl('-j', 'version').returncode == 0)

    kinds = [('org.gnome.Nautilus', 'desktop-2d', 'native'), ('app.zen_browser.zen', 'desktop-2d', 'Flatpak'),
             ('steam_app_570', 'game', 'game'), ('looking-glass-client', 'vm-passthrough', 'VM'),
             ('xfreerdp', 'vm-passthrough', 'remote'), ('mios-nested-desktop', 'desktop-2d', 'nested'),
             ('mios-pip-player', 'desktop-2d', 'PiP'), ('Alacritty', 'terminal', 'terminal')]
    for cls, _, _ in kinds:
        n.eval(f"hl.dispatch(hl.dsp.exec_cmd('alacritty --class {cls} -e sleep 600'))")
    mapped = wait(lambda: all(n.window(c) for c, _, _ in kinds), 30)
    check('every window kind mapped', mapped, [c for c, _, _ in kinds if not n.window(c)])

    # Negative control first (before any room toggle or bridge): the pre-I1 binary's release (input:window off)
    # leaves a North-hold fullscreen captured.
    old_help = sh([OLD, 'bridge', '--help']).stdout
    if '--live-context' in old_help:
        check('negative control binary is pre-I1', False, f'{OLD} already has --live-context; set MIOS_INPUTD_OLD_BIN')
    else:
        cls = 'looking-glass-client'
        w = n.window(cls)
        active = lambda: json.loads(n.ctl('-j', 'activewindow').stdout or '{}').get('address')
        focused = wait(lambda: (n.focus(w['address']), active() == w['address'])[1], 10, 0.3)
        check('negative-control window focused', focused, f"active={active()} want={w['address']}")
        n.eval("hl.dispatch(hl.dsp.window.tag({window=hl.get_active_window(),tag='-mios-input-released'}))")
        wait(lambda: REL not in n.tags(cls), 3)
        lua = lambda b, mode: json.loads(sh([b, '--root', '/', 'resolve-action', f'input:window {mode}'], env=n.tool_env).stdout)['argv'][2]
        n.eval(lua(OLD, 'fullscreen')); wait(lambda: n.fullscreen(cls) == 2, 3)
        lost = active() != w['address']
        # A fullscreen transition can leave the compositor briefly without an active window; wait for the focus
        # (never re-dispatch it, which restarts the transition) and retry the old release only on that refusal.
        wait(lambda: active() == w['address'], 5)
        old_off = ''
        for _ in range(5):
            old_off = n.eval(lua(OLD, 'off')).stdout.strip()
            if old_off != 'error: no focused window':
                break
            time.sleep(0.4)
        old_ctx = wait(lambda: (lambda c: c if c.get('address') == w['address'] else None)(n.context(OLD, '/')), 5) or n.context(OLD, '/')
        check('NEGATIVE: the old release leaves the window fullscreen and still captured',
              old_off == 'ok' and n.fullscreen(cls) == 2 and REL not in n.tags(cls) and old_ctx.get('context') == 'app-capture',
              f'old-off={old_off!r} fs={n.fullscreen(cls)} tags={n.tags(cls)} old={old_ctx} new={n.context()} active={active()} focus-lost-after-fullscreen={lost}')
        n.eval("hl.dispatch(hl.dsp.window.fullscreen({mode='fullscreen',action='unset'}))")
        n.eval(f"hl.dispatch(hl.dsp.window.tag({{window='address:{w['address']}',tag='-{CAP}'}}))")
        wait(lambda: n.fullscreen(cls) == 0 and not n.tags(cls), 3)
        old = sh([OLD, '--root', '/', 'replay'], env=n.tool_env, data=json.dumps({'context': 'game', 'events': [{'kind': 1, 'code': RB, 'value': 1}, {'kind': 1, 'code': RB, 'value': 0}]}))
        new = sh([BIN, '--root', str(root), 'replay'], env=n.tool_env, data=json.dumps({'context': 'game', 'events': [{'kind': 1, 'code': RB, 'value': 1}, {'kind': 1, 'code': RB, 'value': 0}]}))
        oa, na = json.loads(old.stdout)['output']['actions'], json.loads(new.stdout)['output']['actions']
        check('NEGATIVE: the old binary hands an uncaptured game class the pad; the candidate does not', oa == [] and na == ['mios:shaderglass toggle'], f'old={oa} new={na}')

    for layout in ('xpad', 'dualsense'):
        pad = Pad(layout)
        check(f'{layout}: private fixture pad', pad.info.get('name') == f'mios-input-fixture-{layout}', pad.info)
        pad_path = tmp / f'pad-{layout}.bin'
        br = Bridge(n, pad, pad_path)
        ok = wait(lambda: any('"mode":"bridge"' in l for l in br.log), 10)
        check(f'{layout}: candidate bridge owns only the fixture pad', ok, br.log[-3:])
        vfd = vpad_reader(pad.path)
        transcripts = {}
        for cls, expect, kind in (kinds if layout == 'xpad' else kinds[:3]):
            w = n.window(cls)
            n.focus(w['address'])
            ctx = wait(lambda: (lambda c: c if c.get('address') == w['address'] else None)(n.context()), 5) or {}
            t = [f"class-context={ctx.get('context') == expect}", f"owner={ctx.get('controller_owner')}"]
            time.sleep(0.3)
            m = br.mark(); pad.tap(RB); t.append(f"uncaptured RB={br.since(m)[0]}")
            m = br.mark(); pad.tap(NORTH)
            wait(lambda: CAP in n.tags(cls), 3)
            t.append(f"north tap={br.since(m)} tags={n.tags(cls)} fs={n.fullscreen(cls)}")
            wait(lambda: n.context().get('controller_owner') == 'app', 3)
            t.append(f"owner={n.context().get('controller_owner')}")
            drain(vfd)
            m = br.mark(); pad.tap(RB)
            got = [e for e in drain(vfd) if e[0] == 1 and e[1] == RB]
            t.append(f"captured RB actions={br.since(m)[0]} app-got-RB={(RB, 1) in [(c, v) for _, c, v in got]}")
            # Half-capture (Guide+Select): RB and North stay with the app; Start returns to full capture.
            m = br.mark()
            pad.send([[1, GUIDE, 1]]); pad.send([[1, SELECT, 1]]); pad.send([[1, SELECT, 0]]); pad.send([[1, GUIDE, 0]]); time.sleep(0.2)
            pad.tap(RB); pad.tap(NORTH); pad.tap(START)
            t.append(f"half-capture={br.since(m)}")
            m = br.mark(); pad.hold([L3, R3])
            wait(lambda: REL in n.tags(cls), 3)
            t.append(f"release={br.since(m)} tags={n.tags(cls)} fs={n.fullscreen(cls)}")
            wait(lambda: n.context().get('controller_owner') == 'mios', 3)
            t.append(f"owner={n.context().get('controller_owner')} released={n.context().get('released')}")
            m = br.mark(); pad.hold([NORTH])
            wait(lambda: n.fullscreen(cls) == 2, 3)
            t.append(f"north hold={br.since(m)} tags={n.tags(cls)} fs={n.fullscreen(cls)}")
            m = br.mark(); pad.hold([L3, R3])
            wait(lambda: n.fullscreen(cls) == 0, 3)
            t.append(f"release={br.since(m)} tags={n.tags(cls)} fs={n.fullscreen(cls)}")
            transcripts[kind] = t
            print(f"  {layout}/{kind}: {t}", flush=True)
        first = transcripts['native']
        check(f'{layout}: native window: North tap captures, held L3+R3 releases (tags, owner)',
              first[:2] == ['class-context=True', 'owner=mios'] and "tags=['mios-input-captured'] fs=0" in first[3] and 'owner=app' == first[4]
              and "tags=['mios-input-released'] fs=0" in first[7] and first[8] == 'owner=mios released=True', first)
        check(f'{layout}: uncaptured RB is the host ShaderGlass toggle', first[2] == "uncaptured RB=['mios:shaderglass toggle']", first[2])
        check(f'{layout}: captured RB reaches the app (virtual pad), not ShaderGlass', first[5] == 'captured RB actions=[] app-got-RB=True', first[5])
        check(f'{layout}: half-capture keeps RB/North with the app', first[6] == "half-capture=(['input:cursor on', 'input:cursor off'], [])", first[6])
        check(f'{layout}: North hold = fullscreen + capture, marked as the bridge fullscreen',
              "tags=['mios-input-captured', 'mios-input-fullscreen'] fs=2" in first[9], first[9])
        check(f'{layout}: release unsets the bridge fullscreen', "tags=['mios-input-released'] fs=0" in first[10], first[10])
        for kind, t in transcripts.items():
            check(f'{layout}: {kind} captures and releases exactly like a native window', t == first,
                  '' if t == first else [(a, b) for a, b in zip(first, t) if a != b])
        # Super+C works again after the release (the window was fullscreen-captured a moment ago).
        w = n.window('mios-pip-player')
        if layout == 'xpad' and w:
            n.focus(w['address']); time.sleep(0.3)
            n.super_chord('c')
            check('after release Super+C reaches the host again (window closed)', wait(lambda: not n.window('mios-pip-player'), 3))
        if layout == 'dualsense':
            # Negative: a user fullscreen (not set by the bridge) is never undone by a release.
            cls = 'steam_app_570'
            w = n.window(cls); n.focus(w['address']); time.sleep(0.3)
            n.eval("hl.dispatch(hl.dsp.window.fullscreen({mode='fullscreen',action='set'}))")
            wait(lambda: n.fullscreen(cls) == 2, 3)
            pad.tap(NORTH); wait(lambda: CAP in n.tags(cls), 3)
            pad.hold([L3, R3]); wait(lambda: REL in n.tags(cls), 3)
            check('a user fullscreen survives the release (no bridge fullscreen tag)', n.fullscreen(cls) == 2 and n.tags(cls) == [REL],
                  f"fs={n.fullscreen(cls)} tags={n.tags(cls)}")
            check('released fullscreen window no longer owns the controller', n.context().get('controller_owner') == 'mios', n.context())
            n.eval("hl.dispatch(hl.dsp.window.fullscreen({mode='fullscreen',action='unset'}))")
            # Room: one sink per axis. Uncaptured, the left stick drives the camera packet and never the app.
            r = sh(['/usr/bin/mios-gen', 'hyprland-3d-toggle'], env=n.env, timeout=60)
            room = n.eval("if not (hl.plugin.hypr3d and hl.plugin.hypr3d.active()) then error('room inactive',0) end").stdout.strip() == 'ok'
            check('nested room entered', room, r.stderr.strip()[-200:])
            if room:
                wait(lambda: n.context().get('context') == 'desktop-3d', 5)
                time.sleep(0.5); drain(vfd)
                pad.stick(30000); time.sleep(0.3)
                lx, app_x = packet_lx(pad_path), [e for e in drain(vfd) if e[:2] == (3, 0)]
                check('room: left stick moves the camera packet only', lx and lx > 0.5 and not app_x, f'lx={lx} app={app_x[:3]}')
                pad.stick(0); time.sleep(0.2)
                m = br.mark(); pad.tap(NORTH)
                wait(lambda: n.context().get('controller_owner') == 'app', 3)
                check('room: North tap captures the aimed window and stays in 3D', br.since(m)[1] == [('on', True)] and room
                      and n.eval("if not hl.plugin.hypr3d.active() then error('left',0) end").stdout.strip() == 'ok', br.since(m))
                drain(vfd); pad.stick(30000); time.sleep(0.3)
                lx, app_x = packet_lx(pad_path), [e for e in drain(vfd) if e[:2] == (3, 0)]
                check('room capture: left stick reaches the app only (camera packet neutral)', lx == 0.0 and app_x, f'lx={lx} app={app_x[:3]}')
                pad.stick(0); time.sleep(0.2)
                m = br.mark(); pad.hold([L3, R3])
                wait(lambda: n.context().get('context') == 'desktop-3d', 3)
                drain(vfd); pad.stick(30000); time.sleep(0.3)
                lx, app_x = packet_lx(pad_path), [e for e in drain(vfd) if e[:2] == (3, 0)]
                check('room release: the stick drives the camera again, not the app', br.since(m)[1] == [('off', True)] and lx and lx > 0.5 and not app_x,
                      f'{br.since(m)} lx={lx} app={app_x[:3]}')
                pad.stick(0)
                sh(['/usr/bin/mios-gen', 'hyprland-3d-toggle'], env=n.env, timeout=60)
                check('nested room left', wait(lambda: n.eval("if hl.plugin.hypr3d.active() then error('room',0) end").stdout.strip() == 'ok', 10, 0.2))
        if vfd is not None:
            os.close(vfd)
        br.stop()
        check(f'{layout}: bridge exits cleanly on SIGTERM', br.p.returncode == 0, br.log[-3:])
        pad.close()

finally:
    for p in procs:
        if p.poll() is None:
            p.kill()
            p.wait()
    if npid:
        try:
            os.kill(npid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        launcher.wait(timeout=30)
    except subprocess.TimeoutExpired:
        launcher.kill()
    shutil.rmtree(tmp, ignore_errors=True)
    check('nested cleaned up', not sdir.exists())
    check('primary compositor unchanged', sh(['pgrep', '-x', 'Hyprland', '-o']).stdout.strip() == primary)
    check('no fixture pad left behind', not any(n.read_text().startswith('mios-input-fixture-') for n in Path('/sys/class/input').glob('event*/device/name')))
print(f'{sum(res)}/{len(res)} passed')
sys.exit(0 if res and all(res) else 1)
