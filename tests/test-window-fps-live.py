#!/usr/bin/env python3
# AI-hint: LIVE opt-in (MIOS_LIVE_NESTED=1): the status island reads "<host> FPS/<window> FPS" in an owned nested PiP
#          session -- mios-pacing's focused-window meter measured against clients of known rate: GStreamer waylandsink
#          at 24 and 15 FPS (a video subsurface), a terminal redrawn at 20 FPS by a deadline-paced printer, vkcube (FIFO:
#          the output's rate), an idle terminal (0), nothing focused (hidden) -- in 2D and in the Hypr3D room; the island
#          text comes from the nested shell's topbar IPC and grim screenshots are kept. Negative controls: the installed
#          plugin (no meter: the window part is hidden) and [desktop.topbar] window_fps = false.
# AI-related: usr/share/mios/hyprland/plugins/mios-pacing/main.cpp, usr/share/mios/hyprland/plugins/mios-pacing/pacing.hpp,
# AI-related: usr/share/mios/quickshell/PanelWindow.qml, usr/share/mios/quickshell/Nested.qml, usr/share/mios/quickshell/Config.qml,
# AI-related: usr/share/mios/mios.toml [desktop.topbar], tests/test-window-fps.py
# AI-functions: run_pass, main
# Environment:
#   MIOS_LIVE_NESTED=1   run at all (an owned nested MiOS session: /usr/bin/mios-gen nested-session --layout pip)
#   MIOS_PACING_SO       candidate mios-pacing.so (default: the installed one, which then fails the positive pass)
#   MIOS_PACING_OLD      plugin for the negative pass (default /usr/lib64/mios/mios-pacing.so; "" skips it)
#   MIOS_VENDOR_TOML     SSOT the test copies (default: this tree's); MIOS_TOML is ignored
#   MIOS_WINDOW_FPS_OUT  directory for the screenshots (default: a temp dir)
# Known Hypr3D limit (not the meter's): a waylandsink video subsurface stops presenting when the room opens (with the
# installed plugin too), so the room is measured with root-surface clients (the paced terminal and vkcube).
# The primary compositor is never addressed; one nested session at a time, its Hyprland SIGTERMed at the end.
import json, os, re, shutil, signal, subprocess, sys, tempfile, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SSOT = os.environ.get('MIOS_VENDOR_TOML', str(ROOT / 'usr/share/mios/mios.toml'))
SHELL = str(ROOT / 'usr/share/mios/quickshell/Nested.qml')
CAND = os.environ.get('MIOS_PACING_SO', '/usr/lib64/mios/mios-pacing.so')
OLD = os.environ.get('MIOS_PACING_OLD', '/usr/lib64/mios/mios-pacing.so')
OUT = Path(os.environ.get('MIOS_WINDOW_FPS_OUT') or tempfile.mkdtemp(prefix='mios-window-fps-'))
# Redraws one terminal line at absolute deadlines 1/rate apart (no drift); bounded to two minutes.
PRINTER = ('import sys,time\nr=float(sys.argv[1]);t=time.monotonic()\nfor i in range(int(r*120)):\n'
           ' sys.stdout.write("\\r%06d"%i);sys.stdout.flush();t+=1/r;time.sleep(max(0,t-time.monotonic()))\n')
res = []


def check(n, ok, d=''):
    res.append(bool(ok)); print(f"{'PASS' if ok else 'FAIL'} {n} {d}", flush=True)


def sh(a, env=None, timeout=30):
    return subprocess.run(a, capture_output=True, text=True, env=env, timeout=timeout)


def ssot_copy(label, plugin, window_fps):
    text = Path(SSOT).read_text()
    text, k = re.subn(r'(?m)^(path = )"[^"]*mios-pacing[^"]*\.so"', lambda m: f'{m.group(1)}"{plugin}"', text)
    assert k == 1, 'mios-pacing path line not found'
    text, k = re.subn(r'(?m)^(shell_entry = )"[^"]*"', lambda m: f'{m.group(1)}"{SHELL}"', text)
    assert k == 1, 'desktop.nested shell_entry line not found'
    text, k = re.subn(r'(?m)^window_fps = (true|false)', f'window_fps = {"true" if window_fps else "false"}', text)
    assert k == 1, 'desktop.topbar window_fps line not found'
    tmp = tempfile.NamedTemporaryFile('w', suffix='.toml', prefix=f'mios-window-fps-{label}-', delete=False)
    tmp.write(text); tmp.close()
    return tmp.name


def spawn(kind, rate, env, log=None):
    """Known-rate clients, all bounded."""
    out = subprocess.DEVNULL
    if kind == 'video':  # one buffer per video frame on waylandsink's video subsurface; fpsdisplaysink reports the
        # client's own rendered rate ("current: N") so the meter is checked against the client, not only the nominal rate
        argv = ['gst-launch-1.0', '-v', 'videotestsrc', 'is-live=true', 'pattern=ball', f'num-buffers={rate * 120}', '!',
                f'video/x-raw,framerate={rate}/1,width=320,height=240', '!', 'fpsdisplaysink', 'video-sink=waylandsink',
                'text-overlay=false', 'sync=true']
        out = open(log, 'w')
    elif kind == 'terminal':  # the toplevel itself redraws `rate` times a second
        argv = ['alacritty', '-o', 'cursor.style.blinking="Never"', '--class', 'mios-window-fps-paced', '-e', 'python3', '-I', '-c', PRINTER, str(rate)]
    elif kind == 'vkcube':  # FIFO presentation: every output frame
        argv = ['vkcube', '--wsi', 'wayland', '--c', '12000']
    else:  # idle: a terminal that draws nothing
        argv = ['alacritty', '-o', 'cursor.style.blinking="Never"', '--class', 'mios-window-fps-idle', '-e', 'sleep', '120']
    return subprocess.Popen(argv, env=env, stdout=out, stderr=subprocess.STDOUT if log else subprocess.DEVNULL)


def client_fps(log):
    """The video client's own last measured rate (fpsdisplaysink), or None."""
    try:
        m = re.findall(r'rendered: (\d+), dropped: (\d+), current: ([0-9.]+)', Path(log).read_text())
    except OSError:
        return None
    return float(m[-1][2]) if m else None


def run_pass(label, plugin, meter, window_fps=True, full=False):
    vendor = ssot_copy(label, plugin, window_fps)
    env0 = dict(os.environ, MIOS_VENDOR_TOML=vendor)
    env0.pop('MIOS_TOML', None)
    L = subprocess.Popen(['/usr/bin/mios-gen', 'nested-session', '--layout', 'pip'], env=env0, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    d = Path(os.environ['XDG_RUNTIME_DIR']) / f'mios-nested-desktop-{L.pid}'
    npid, procs = None, []
    try:
        for _ in range(300):
            if (d / 'session.json').is_file():
                break
            time.sleep(0.2)
        s = json.loads((d / 'session.json').read_text())
        npid = s['instance']['pid']
        env = dict(env0, XDG_RUNTIME_DIR=str(d), HYPRLAND_INSTANCE_SIGNATURE=s['instance']['instance'], WAYLAND_DISPLAY=s['instance']['wl_socket'])
        env.pop('DISPLAY', None)
        ctl = lambda *a: sh(['hyprctl', *a], env=env)
        ev = lambda expr: ctl('eval', expr)
        for _ in range(100):
            if ctl('-j', 'monitors').stdout.strip().startswith('['):
                break
            time.sleep(0.2)
        mon = json.loads(ctl('-j', 'monitors').stdout)[0]['name']

        def status():
            try:
                return json.loads(ctl('mios-pacing', 'status').stdout)
            except ValueError:
                return {}

        def island():
            r = sh(['quickshell', 'ipc', '--path', SHELL, '--any-display', 'call', 'topbar', 'state'], env=env)
            try:
                return json.loads(r.stdout)
            except ValueError:
                return {}

        def wait_island(pred, seconds=8.0):
            st = {}
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                st = island()
                if st and pred(st):
                    return st
                time.sleep(0.25)
            return st

        def start(kind, rate=0, log=None):
            p = spawn(kind, rate, env, log); procs.append(p)
            for _ in range(300):
                c = next((c for c in json.loads(ctl('-j', 'clients').stdout or '[]') if c.get('pid') == p.pid), None)
                if c:
                    return p, c
                time.sleep(0.1)
            return p, None

        def room(on):
            act = lambda: 'true' in (lambda r: r.stdout + r.stderr)(ev('error(tostring(hl.plugin.hypr3d.active()),0)'))
            if act() != on:
                sh(['/usr/bin/mios-gen', 'hyprland-3d-toggle'], env=env, timeout=60)
            end = time.monotonic() + 10
            while act() != on and time.monotonic() < end:
                time.sleep(0.2)
            return act() == on

        def focus(c):
            for _ in range(3):
                ev(f"hl.dispatch(hl.dsp.focus({{window='address:{c['address']}'}}))")
                for _ in range(15):
                    a = json.loads(ctl('-j', 'activewindow').stdout or '{}').get('address')
                    if a and int(a, 16) == int(c['address'], 16):
                        return True
                    time.sleep(0.2)
            return False

        def measure(c, rate, tag):
            """Focus `c`; the meter follows it and reads `rate` (None: the output's own rate, a FIFO client)."""
            focus(c)
            time.sleep(2.5)
            stj = status()
            w = stj.get('window')
            got = w.get('fps') if w else None
            want = rate
            if want is None and meter:
                # A FIFO client presents once per output frame: never above the output's rate, and equal to it while the
                # output's rate holds (a nested output's rate swings with the parent's pacing: up to 4 samples).
                above = []
                for _ in range(4):
                    outs = stj.get('outputs') or [{}]
                    want = outs[0].get('rendered_fps')
                    w = stj.get('window')
                    got = w.get('fps') if w else None
                    if got is not None and want:
                        above.append(got > want * 1.1 + 1.5)
                        if abs(got - want) <= max(1.5, want * 0.1):
                            break
                    time.sleep(0.5)
                    stj = status()
                check(f'[{label}] {tag}: never above the output rate', above and not any(above), above)
            elif want is None:
                want = (stj.get('outputs') or [{}])[0].get('rendered_fps')
            if meter:
                check(f'[{label}] {tag}: meter follows the focused window', w and int(w['address'], 16) == int(c['address'], 16) and w.get('output') == mon,
                      json.dumps(w))
                tol = max(1.5, (want or 0) * (0.1 if rate is None or rate == 20 else 0.08))
                check(f'[{label}] {tag}: window FPS {got} = {want if rate else f"output {want}"}', got is not None and want is not None and abs(got - want) <= tol,
                      json.dumps(w))
            else:
                check(f'[{label}] {tag}: no window meter in this plugin', 'window' not in stj, json.dumps(w))
            st = wait_island(lambda st: (st.get('window_fps', -1) >= 0) == (meter and window_fps) and st.get('fps_text'))
            host, wf, text = st.get('fps'), st.get('window_fps'), st.get('fps_text', '')
            if meter and window_fps:
                exp = (f"{host} FPS" if host is not None and host >= 0 else "FPS —") + f"/{wf} FPS"
                ref = want if rate is not None else host  # a FIFO client follows the output rate the island shows
                check(f'[{label}] {tag}: island reads "{text}"', text == exp and ref is not None and abs(wf - ref) <= max(2, ref * 0.12), json.dumps(st)[:240])
            else:
                check(f'[{label}] {tag}: island shows the host rate alone "{text}"', text and '/' not in text and wf == -1, json.dumps(st)[:240])
            return st

        st = wait_island(lambda st: st.get('fps', -1) >= 0)
        check(f'[{label}] nested island shows the session\'s own output FPS', st.get('fps', -1) >= 0, json.dumps(st)[:160])
        time.sleep(3.0)  # the session's output and tiling settle (a resize while waylandsink maps can stall it)
        # 2D: two video rates the client paces itself (the meter is the client's rate, not the output's).
        for rate in ((24, 15) if full else (24,)):
            log = str(OUT / f'{label}-video-{rate}.log')
            p, c = start('video', rate, log)
            check(f'[{label}] {rate} FPS video client mapped', c)
            if c:
                measure(c, rate, f'2D video {rate} FPS')
                w, own = status().get('window'), client_fps(log)
                if meter:
                    check(f'[{label}] 2D video {rate} FPS: meter agrees with the client\'s own rate {own}',
                          w and own is not None and abs(w['fps'] - own) <= max(1.5, own * 0.08), json.dumps(w))
                if rate == 24:
                    sh(['grim', str(OUT / f'{label}-2d-video-24.png')], env=env)
            p.terminate(); p.wait(timeout=10)
        if not full:
            return
        p, term = start('terminal', 20)
        check(f'[{label}] paced terminal mapped', term)
        p2, cube = start('vkcube')
        check(f'[{label}] vkcube mapped', cube)
        if term:
            measure(term, 20, '2D terminal 20 FPS')
        if cube:
            measure(cube, None, '2D vkcube (FIFO)')
        p.terminate(); p.wait(timeout=10)
        # Hypr3D: the room renders the windows as textures; their own commits are still what is counted. The room
        # focuses the window it aims at, so each client is measured while it is the room's focused window: vkcube
        # (aimed when the room opens), then a paced terminal opened inside the room (it spawns in front, focused).
        check(f'[{label}] Hypr3D room active', room(True))
        time.sleep(1.0)
        if cube:
            measure(cube, None, 'Hypr3D vkcube (FIFO)')
        p2.terminate(); p2.wait(timeout=10)
        p, term = start('terminal', 20)
        check(f'[{label}] paced terminal mapped inside the room', term)
        if term:
            measure(term, 20, 'Hypr3D terminal 20 FPS')
            sh(['grim', str(OUT / f'{label}-3d-terminal-20.png')], env=env)
        check(f'[{label}] back in 2D', room(False))
        time.sleep(2.0)  # the exit transition settles focus
        p.terminate(); p.wait(timeout=10)
        # An idle window presents nothing: 0 FPS, still known.
        p, c = start('idle')
        if c:
            focus(c)
            time.sleep(3.0)
            w = status().get('window') or {}
            check(f'[{label}] idle window reads 0 FPS', int(w.get('address', '0x0'), 16) == int(c['address'], 16) and w.get('fps', -1) < 1.0, json.dumps(w))
            st = wait_island(lambda st: st.get('window_fps') == 0)
            check(f'[{label}] island reads "{st.get("fps_text")}" for the idle window', str(st.get('fps_text', '')).endswith('/0 FPS'), json.dumps(st)[:160])
        p.terminate(); p.wait(timeout=10)
        # Nothing focused: the window part is hidden (unknown, not 0).
        ev("hl.dispatch(hl.dsp.focus({workspace=9}))")
        time.sleep(1.5)
        w = status().get('window', 'missing')
        check(f'[{label}] no focused window: the meter reports null', w is None, json.dumps(w))
        st = wait_island(lambda st: st.get('window_fps') == -1)
        check(f'[{label}] no focused window: island shows the host rate alone "{st.get("fps_text")}"',
              st.get('window_fps') == -1 and '/' not in str(st.get('fps_text', '/')), json.dumps(st)[:160])
    finally:
        for p in procs:
            if p.poll() is None:
                p.kill()
        if npid:
            try:
                os.kill(npid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        try:
            L.wait(timeout=20)
        except subprocess.TimeoutExpired:
            L.kill()
        os.unlink(vendor)


def main():
    if os.environ.get('MIOS_LIVE_NESTED') != '1':
        print('SKIP: set MIOS_LIVE_NESTED=1 (owned nested MiOS PiP session)')
        return 0
    for tool in ('gst-launch-1.0', 'grim', 'alacritty', 'quickshell', 'vkcube'):
        if not shutil.which(tool):
            print(f'SKIP: {tool} missing'); return 0
    before = json.loads(sh(['hyprctl', '-j', 'instances']).stdout or '[]')
    run_pass('candidate', CAND, meter=True, full=True)
    run_pass('ssot-off', CAND, meter=True, window_fps=False)
    if OLD:
        run_pass('installed', OLD, meter=False)
    after = json.loads(sh(['hyprctl', '-j', 'instances']).stdout or '[]')
    primary = [i.get('pid') for i in before if i.get('instance') == os.environ.get('HYPRLAND_INSTANCE_SIGNATURE')]
    check('primary compositor untouched (same pid)', primary and primary == [i.get('pid') for i in after if i.get('instance') == os.environ.get('HYPRLAND_INSTANCE_SIGNATURE')], primary)
    print(f'screenshots: {OUT}')
    print(f'{sum(res)}/{len(res)} passed')
    return 0 if all(res) else 1


if __name__ == '__main__':
    sys.exit(main())
