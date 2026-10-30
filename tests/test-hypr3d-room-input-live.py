#!/usr/bin/env python3
# AI-hint: LIVE opt-in (MIOS_LIVE_NESTED=1): Hypr3D room verbs (cancel/sprint/recenter, via mios-inputd dispatch) and PiP face-the-viewer (2 m room / 4 m Theatre, eased follow, eased return) in an owned nested PiP session; the installed plugin (MIOS_HYPR3D_OLD) is the negative control.
# AI-related: usr/share/mios/hyprland/patches/hypr3d-mios-room-input.patch, usr/share/mios/mios.toml [desktop.hyprland.hypr3d.config.windows] [keybindings.global.controller.room], tools/native/mios-inputd
# AI-functions: run_pass, main
# Runs in an owned nested MiOS PiP session (mios-gen nested-session --layout pip). The camera is moved by real key
# events (W/A/S/D held as send_key_state events to the nested parent window); every measurement is the PiP plane
# normal against the direction from the PiP centre to the camera position (hl.plugin.hypr3d.export_layout()).
#   MIOS_VENDOR_TOML  candidate SSOT whose [desktop.hyprland.hypr3d].path is the candidate plugin
#   MIOS_HYPR3D_OLD   a pre-P1 hypr3d.so for the negative-control pass (skipped when unset)
#   MIOS_INPUTD_BIN   candidate mios-inputd for the `dispatch hypr:hypr3d:*` end-to-end checks
# The primary compositor only receives send_key_state for the nested window. The nested Hyprland is SIGTERMed at the end.
import json, math, os, re, signal, subprocess, sys, tempfile, time
from pathlib import Path

SSOT = os.environ.get('MIOS_VENDOR_TOML', str(Path(__file__).resolve().parents[1] / 'usr/share/mios/mios.toml'))
OLD = os.environ.get('MIOS_HYPR3D_OLD', '')
INPUTD = os.environ.get('MIOS_INPUTD_BIN', '/usr/libexec/mios/mios-inputd')
res = []


def check(n, ok, d=''):
    res.append(bool(ok)); print(f"{'PASS' if ok else 'FAIL'} {n} {d}", flush=True)


def sh(a, env=None, timeout=30):
    return subprocess.run(a, capture_output=True, text=True, env=env, timeout=timeout)


def normal(w):
    return (math.sin(w['yaw']) * math.cos(w['pitch']), -math.sin(w['pitch']), math.cos(w['yaw']) * math.cos(w['pitch']))


def off_axis(w, cam):
    """Degrees between the PiP plane normal and the direction from its centre to the camera, and the distance."""
    n = normal(w)
    d = (cam['x'] - w['x'], cam['y'] - w['y'], cam['z'] - w['z'])
    L = math.sqrt(sum(c * c for c in d))
    dot = sum(a * b for a, b in zip(n, d)) / L
    return math.degrees(math.acos(max(-1.0, min(1.0, dot)))), L


def run_pass(label, plugin, candidate):
    text = Path(SSOT).read_text()
    text, k = re.subn(r'(?m)^(path = )"[^"]*hypr3d[^"]*\.so"', lambda m: f'{m.group(1)}"{plugin}"', text)
    assert k == 1, 'hypr3d path line not found'
    tmp = tempfile.NamedTemporaryFile('w', suffix='.toml', prefix=f'mios-room-{label}-', delete=False)
    tmp.write(text); tmp.close()
    env0 = dict(os.environ, MIOS_VENDOR_TOML=tmp.name)
    env0.pop('MIOS_TOML', None)
    L = subprocess.Popen(['/usr/bin/mios-gen', 'nested-session', '--layout', 'pip'], env=env0, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    d = Path(os.environ['XDG_RUNTIME_DIR']) / f'mios-nested-desktop-{L.pid}'
    npid = None
    try:
        for _ in range(300):
            if (d / 'session.json').is_file():
                break
            time.sleep(0.2)
        s = json.loads((d / 'session.json').read_text())
        npid = s['instance']['pid']
        pw = s['parent_window']
        env = dict(env0, XDG_RUNTIME_DIR=str(d), HYPRLAND_INSTANCE_SIGNATURE=s['instance']['instance'], WAYLAND_DISPLAY=s['instance']['wl_socket'])
        env.pop('DISPLAY', None)
        ctl = lambda *a: sh(['hyprctl', *a], env=env)
        ev = lambda expr: ctl('eval', expr)

        def layout():
            r = ev('error(hl.plugin.hypr3d.export_layout(),0)')
            t = r.stdout + r.stderr
            try:
                return json.loads(t[t.index('{'):t.rindex('}') + 1])
            except ValueError:
                return None

        def sk(key, state):
            # the ONLY primary-compositor call: a key event for the nested window
            sh(['hyprctl', 'eval', f"hl.dispatch(hl.dsp.send_key_state({{mods='',key='{key}',state='{state}',window='address:{pw}'}}))"])

        def hold(key, seconds):
            sk(key, 'down'); time.sleep(seconds); sk(key, 'up')

        # A PiP the room manages (the tag mios-pip-gravity sets), in the nested session only.
        for _ in range(100):
            if ctl('-j', 'monitors').stdout.strip().startswith('['):
                break
            time.sleep(0.2)
        ev("hl.dispatch(hl.dsp.exec_cmd('alacritty --title Picture-in-Picture --class mios-room-pip -e sleep 900'))")
        win = None
        for _ in range(100):
            win = next((c for c in json.loads(ctl('-j', 'clients').stdout or '[]') if c.get('class') == 'mios-room-pip'), None)
            if win:
                break
            time.sleep(0.2)
        check(f'[{label}] PiP window mapped', win)
        addr = win['address']
        ev(f"hl.dispatch(hl.dsp.window.tag({{window='address:{addr}',tag='+mios-pip-managed'}}))")
        ev(f"hl.dispatch(hl.dsp.focus({{window='address:{addr}'}}))")
        time.sleep(0.5)

        def toggle():
            return sh(['/usr/bin/mios-gen', 'hyprland-3d-toggle'], env=env, timeout=60)

        def settle(pred, seconds=6.0):
            lay = None
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                lay = layout()
                if lay and pred(lay):
                    return lay
                time.sleep(0.1)
            return lay

        r = toggle()
        lay = settle(lambda l: l['owns_input'] and l['view_morph'] == 0 and l['windows'])
        check(f'[{label}] room active with the plugin under test', lay and lay['owns_input'], (r.stdout + r.stderr).strip()[-200:])
        pip = lambda l: next((w for w in l['windows'] if int(w['id'], 16) == int(addr, 16)), None)
        check(f'[{label}] PiP in the room', pip(lay), [w['id'] for w in lay['windows']])
        check(f'[{label}] the loaded plugin is the one under test', ('room_input' in lay) == candidate, sorted(lay))
        # Deterministic start: camera at its spawn point looking -Z, the PiP 3 m ahead at eye height, turned 40 degrees
        # (yaw -0.7 rad) away from the camera. Outside 2 m, so a facing turn must not happen yet.
        cam = lay['camera']
        w0 = pip(lay)
        base_yaw = -0.7  # normal toward -X: the walk (forward, then right) moves away from it
        toggle()
        active = lambda: 'true' in (lambda r: r.stdout + r.stderr)(ev('error(tostring(hl.plugin.hypr3d.active()),0)'))
        end = time.monotonic() + 8.0
        while active() and time.monotonic() < end:
            time.sleep(0.1)
        check(f'[{label}] room closed for the checkpoint', not active())
        win_t = (f"{{id='{w0['id']}',x={cam['x']},y={cam['y']},z={cam['z'] - 3.0},yaw={base_yaw},pitch=0,roll=0,"
                 f"scale={w0['scale']},left={w0['left']},top={w0['top']},width={w0['width']},height={w0['height']}}}")
        end = time.monotonic() + 10.0
        while True:  # the exit transition still holds the room: retry until it has closed
            r = ev(f"hl.plugin.hypr3d.restore_layout({{version=1,camera={{x={cam['x']},y={cam['y']},z={cam['z']},yaw=0,pitch=0,mode=0}},windows={{{win_t}}}}})")
            if 'close the room' not in r.stdout + r.stderr or time.monotonic() > end:
                break
            time.sleep(0.2)
        check(f'[{label}] room checkpoint placed the PiP', r.returncode == 0 and 'error' not in r.stdout.lower(), (r.stdout + r.stderr).strip()[:200])
        toggle()
        lay = settle(lambda l: l['owns_input'] and l['view_morph'] == 0 and pip(l) and abs(pip(l)['yaw'] - base_yaw) < 0.02, 8.0)
        a, dist = off_axis(pip(lay), lay['camera'])
        check(f'[{label}] start: PiP at {dist:.2f} m, outside 2 m, not facing', dist > 2.2 and a > 30, f'off-axis {a:.1f} deg yaw {pip(lay)["yaw"]:.3f}')

        # Walk toward the PiP (W) and to its right (D) until it is within 1.6 m.
        for _ in range(40):
            lay = layout()
            a, dist = off_axis(pip(lay), lay['camera'])
            if dist < 1.6:
                break
            hold('w', 0.12)
        hold('d', 0.25)
        series = []
        t0 = time.monotonic()
        while time.monotonic() - t0 < 1.5:
            lay = layout()
            series.append(off_axis(pip(lay), lay['camera'])[0])
        lay = layout()
        a, dist = off_axis(pip(lay), lay['camera'])
        inside = dict(off=round(a, 2), dist=round(dist, 2), yaw=round(pip(lay)['yaw'], 3), facing=pip(lay).get('facing'))
        if candidate:
            check(f'[{label}] inside 2 m the PiP normal faces the camera position', a < 3.0 and dist < 2.0, inside)
            # Eased, not snapped: right after the strafe the PiP lags the moved camera, then the gap closes gradually
            # (a snapping turn would read ~0 deg in every sample).
            peak = max(series)
            mid = [x for x in series if 1.0 < x < peak - 1.0]
            check(f'[{label}] the turn is eased (it lags, then closes gradually; no snap)', peak > 4.0 and len(mid) >= 3, [round(x, 1) for x in series[:14]])
            check(f'[{label}] export reports facing', pip(lay).get('facing') is True)
        else:
            check(f'[{label}] NEGATIVE: the pre-P1 plugin leaves the PiP turned away inside 2 m', a > 10.0 and dist < 2.0, inside)

        # Follow: strafe left while inside the distance; the normal tracks the moving camera.
        hold('a', 0.5)
        time.sleep(1.2)
        lay = layout()
        a, dist = off_axis(pip(lay), lay['camera'])
        if candidate:
            check(f'[{label}] follows the camera as it moves', a < 3.0 and dist < 2.1, dict(off=round(a, 2), dist=round(dist, 2)))

        # Leave: walk back (S) past 2 m + the hysteresis band; the PiP eases back to the pose it was placed with.
        for _ in range(60):
            lay = layout()
            a, dist = off_axis(pip(lay), lay['camera'])
            if dist > 2.5:
                break
            hold('s', 0.12)
        time.sleep(1.5)
        lay = layout()
        w = pip(lay)
        a, dist = off_axis(w, lay['camera'])
        check(f'[{label}] outside the distance the PiP returns to its placed pose', abs(w['yaw'] - base_yaw) < 0.02 and abs(w['pitch']) < 0.02 and dist > 2.1,
              dict(yaw=round(w['yaw'], 3), off=round(a, 1), dist=round(dist, 2)))

        if candidate:
            # Room verbs end to end: mios-inputd dispatch -> hyprctl eval -> hl.plugin.hypr3d.<verb>() in THIS compositor.
            disp = lambda action: sh([INPUTD, 'dispatch', action], env=env)
            s0 = layout()['room_input']['sprint']
            r = disp('hypr:hypr3d:sprint')
            s1 = settle(lambda l: l['room_input']['sprint'] != s0, 2.0)['room_input']['sprint']
            check(f'[{label}] inputd dispatch hypr:hypr3d:sprint toggles the sprint latch', s1 != s0, (r.stdout + r.stderr).strip()[:160])
            disp('hypr:hypr3d:sprint')
            check(f'[{label}] and again turns it off', settle(lambda l: l['room_input']['sprint'] == s0, 2.0)['room_input']['sprint'] == s0)
            # recenter: look away (the controller's look path), then ease back onto the focused PiP
            # (looking away clears aim focus: recenter must still find the window the view last rested on)
            ev(f"hl.dispatch(hl.dsp.focus({{window='address:{addr}'}}))")
            time.sleep(0.3)
            ev('hl.plugin.hypr3d.camera(900, 0)')
            time.sleep(0.4)
            ev('hl.plugin.hypr3d.camera(0, 0)')
            time.sleep(0.3)
            focused = json.loads(ctl('-j', 'activewindow').stdout or '{}').get('address')
            lay = layout()
            c, w = lay['camera'], pip(lay)

            def aim(c, w):
                f = (math.sin(c['yaw']) * math.cos(c['pitch']), math.sin(c['pitch']), -math.cos(c['yaw']) * math.cos(c['pitch']))
                v = (w['x'] - c['x'], w['y'] - c['y'], w['z'] - c['z']); n = math.sqrt(sum(x * x for x in v))
                return math.degrees(math.acos(max(-1, min(1, sum(p * q for p, q in zip(f, v)) / n))))
            # the window the view rests on decides the target: aim focus may have moved to another room window
            target = lambda l: min(l['windows'], key=lambda w: aim(l['camera'], w))
            if aim(c, target(lay)) < 10:
                # the look-away landed on a window: rest the view on the PiP explicitly, then look away from it
                ev(f"hl.dispatch(hl.dsp.focus({{window='address:{addr}'}}))")
            before = aim(c, w)
            r = disp('hypr:hypr3d:recenter')
            said = ((r.stdout + r.stderr).strip()[:120], layout()['room_input']['recentering'])
            samples = []
            t0 = time.monotonic()
            while time.monotonic() - t0 < 2.0:
                l2 = layout(); samples.append(aim(l2['camera'], target(l2)))
            after = samples[-1]
            check(f'[{label}] inputd dispatch hypr:hypr3d:recenter eases the camera onto a room window', before > 20 and after < 2.0 and any(2.0 < x < before - 2.0 for x in samples),
                  dict(before=round(before, 1), after=round(after, 2), path=[round(x, 1) for x in samples[:8]], focused_at_dispatch=focused == addr, said=said))
            # cancel: drops an active grab; with nothing to cancel it changes nothing
            ev('hl.plugin.hypr3d.grab("on")')
            g = settle(lambda l: l['room_input']['drag'] or l['room_input']['gesture'] != 0, 2.0)['room_input']
            r = ev('error(hl.plugin.hypr3d.cancel(),0)')
            g2 = settle(lambda l: not l['room_input']['drag'] and l['room_input']['gesture'] == 0, 2.0)['room_input']
            check(f'[{label}] cancel drops an active grab', (g['drag'] or g['gesture'] != 0) and not g2['drag'] and g2['gesture'] == 0 and 'grab' in r.stdout + r.stderr,
                  dict(during=g, after=g2, said=(r.stdout + r.stderr).strip()[:60]))
            r = ev('error(hl.plugin.hypr3d.cancel(),0)')
            check(f'[{label}] NEGATIVE: cancel with nothing active cancels nothing', 'none' in r.stdout + r.stderr, (r.stdout + r.stderr).strip()[:60])
            ev('hl.plugin.hypr3d.mode("cursor")')
            r = disp('hypr:hypr3d:cancel')
            check(f'[{label}] inputd dispatch hypr:hypr3d:cancel leaves the room cursor', settle(lambda l: not l['room_input']['cursor'], 2.0)['room_input']['cursor'] is False)

            # Theatre (native maximize of the PiP): 4 m. Walk to ~3 m and sideways: the Theatre PiP faces the camera at
            # a distance where a regular PiP (2 m) does not.
            ev('hl.plugin.hypr3d.mode("camera")')
            ev(f"hl.dispatch(hl.dsp.focus({{window='address:{addr}'}}))")
            ev(f"hl.dispatch(hl.dsp.window.fullscreen({{window='address:{addr}',mode='maximized',action='set'}}))")
            lay = settle(lambda l: l['pip_theatre'] != '0x0' and l['view_morph'] == 0, 6.0)
            time.sleep(1.0)
            check(f'[{label}] PiP Theatre entered', lay and lay['pip_theatre'] != '0x0', lay and lay['pip_theatre'])
            for _ in range(40):
                lay = layout()
                a, dist = off_axis(pip(lay), lay['camera'])
                if dist < 3.2:
                    break
                hold('w', 0.12)
            hold('d', 0.3)
            time.sleep(1.5)
            lay = layout()
            a, dist = off_axis(pip(lay), lay['camera'])
            check(f'[{label}] Theatre: faces the camera within 4 m (here {dist:.2f} m, beyond the regular 2 m)', a < 3.0 and 2.2 < dist < 4.0, dict(off=round(a, 2), dist=round(dist, 2)))
            ev(f"hl.dispatch(hl.dsp.window.fullscreen({{window='address:{addr}',mode='maximized',action='unset'}}))")
        else:
            r = ev('error(tostring(hl.plugin.hypr3d.cancel),0)')
            check(f'[{label}] NEGATIVE: the pre-P1 plugin has no cancel verb', 'nil' in r.stdout + r.stderr, (r.stdout + r.stderr).strip()[:80])
            r = sh([INPUTD, 'dispatch', 'hypr:hypr3d:recenter'], env=env)
            check(f'[{label}] NEGATIVE: recenter does not exist in the pre-P1 plugin', r.returncode != 0 or 'nil' in (r.stdout + r.stderr).lower() or 'error' in (r.stdout + r.stderr).lower(), (r.stdout + r.stderr).strip()[:120])
    finally:
        if npid:
            try:
                os.kill(npid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        try:
            L.wait(timeout=30)
        except subprocess.TimeoutExpired:
            L.kill()
        os.unlink(tmp.name)
        check(f'[{label}] nested cleaned up', not d.exists())


def main():
    if os.environ.get('MIOS_LIVE_NESTED') != '1' or not os.environ.get('HYPRLAND_INSTANCE_SIGNATURE'):
        print('skip: live nested-session test (set MIOS_LIVE_NESTED=1 inside a MiOS desktop session)')
        return 0
    primary = sh(['pgrep', '-x', 'Hyprland', '-o']).stdout.strip()
    portal0 = open('/proc/self/mountinfo').read().count('fuse.portal')
    import tomllib
    plugin = tomllib.loads(Path(SSOT).read_text())['desktop']['hyprland']['hypr3d']['path']
    run_pass('candidate', plugin, True)
    if OLD:
        run_pass('installed', OLD, False)
    portal1 = open('/proc/self/mountinfo').read().count('fuse.portal')
    # (other workflows' nested sessions run concurrently: the portal count is reported, the primary pid is the check)
    check('primary compositor unchanged', sh(['pgrep', '-x', 'Hyprland', '-o']).stdout.strip() == primary, f'portal mounts {portal0} -> {portal1}')
    print(f'{sum(res)}/{len(res)} passed')
    return 0 if all(res) else 1


if __name__ == '__main__':
    sys.exit(main())
