#!/usr/bin/env python3
# AI-hint: LIVE opt-in test (MIOS_LIVE_NESTED=1) of global-bindings step S0 in an owned nested MiOS PiP session: SUPER+CTRL+F11 / SUPER+CTRL+Escape escapes bound and acting (in and out of fullscreen), SUPER+F11 unchanged; real key events, primary untouched.
# AI-related: usr/share/mios/mios.toml [keybindings.global], docs/design/mios-global-bindings-final.md
# Live S0 check in an owned nested MiOS PiP session running the S0 SSOT: the new bypass chords are bound and act
# (SUPER+CTRL+F11 toggles fullscreen, including OUT of a fullscreen window), SUPER+F11 still works, SUPER+CTRL+Escape is
# bound; keys are real key events into the nested compositor (modifiers held). Primary untouched.
import json, os, signal, subprocess, sys, time
from pathlib import Path

SSOT = os.environ.get('MIOS_VENDOR_TOML', str(Path(__file__).resolve().parents[1] / 'usr/share/mios/mios.toml'))
res = []


def check(n, ok, d=''):
    res.append(bool(ok)); print(f"{'PASS' if ok else 'FAIL'} {n} {d}", flush=True)


def sh(a, env=None):
    return subprocess.run(a, capture_output=True, text=True, env=env, timeout=30)


if os.environ.get('MIOS_LIVE_NESTED') != '1' or not os.environ.get('HYPRLAND_INSTANCE_SIGNATURE'):
    print('skip: live nested-session test (set MIOS_LIVE_NESTED=1 inside a MiOS desktop session)')
    sys.exit(0)
env0 = dict(os.environ, MIOS_VENDOR_TOML=SSOT)
env0.pop('MIOS_TOML', None)
portal0 = open('/proc/self/mountinfo').read().count('fuse.portal')
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
    binds = json.loads(ctl('-j', 'binds').stdout)
    has = lambda key, mm: any(str(b.get('key', '')).lower() == key.lower() and b.get('modmask') == mm for b in binds)
    check('SUPER+CTRL+F11 bound', has('F11', 68))
    check('SUPER+CTRL+Escape bound', has('Escape', 68))
    check('SUPER+F11 still bound', has('F11', 64))
    print('F11/Escape binds:', [ (b.get('key'), b.get('modmask'), b.get('dispatcher'), b.get('submap')) for b in binds if str(b.get('key','')).lower() in ('f11','escape')], flush=True)
    ctl('eval', "hl.dispatch(hl.dsp.exec_cmd('alacritty --class mios-s0-test -e sleep 300'))")
    w = None
    for _ in range(100):
        w = next((c for c in json.loads(ctl('-j', 'clients').stdout or '[]') if c.get('class') == 'mios-s0-test'), None)
        if w:
            break
        time.sleep(0.2)
    check('test window mapped', w)
    ctl('eval', f"hl.dispatch(hl.dsp.focus({{window='address:{w['address']}'}}))")

    def sk(m, k, st):
        sh(['hyprctl', 'eval', f"hl.dispatch(hl.dsp.send_key_state({{mods='{m}',key='{k}',state='{st}',window='address:{pw}'}}))"])

    def chord(mods, key):
        held = [('', 'Super_L', 'SUPER')] + ([('SUPER', 'Control_L', 'SUPER CTRL')] if 'CTRL' in mods else [])
        for m, k, _ in held:
            sk(m, k, 'down')
        sk(mods, key, 'down'); sk(mods, key, 'up')
        for m, k, _ in reversed(held):
            sk(m.replace(' CTRL', '') if k == 'Control_L' else '', k, 'up')
        time.sleep(0.6)

    fs = lambda: next((c for c in json.loads(ctl('-j', 'clients').stdout or '[]') if c.get('class') == 'mios-s0-test'), {}).get('fullscreen')
    f0 = fs()
    chord('SUPER CTRL', 'F11'); f1 = fs()
    check('SUPER+CTRL+F11 enters fullscreen', f1 and f1 != f0, f'{f0} -> {f1}')
    chord('SUPER CTRL', 'F11'); f2 = fs()
    check('SUPER+CTRL+F11 leaves fullscreen (the escape works from inside fullscreen)', f2 == f0, f'{f1} -> {f2}')
    chord('SUPER', 'F11'); f3 = fs()
    check('SUPER+F11 still toggles fullscreen', f3 and f3 != f0, f'-> {f3}')
    chord('SUPER', 'F11')
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
    check('nested cleaned up', not d.exists())
    check('primary unchanged', sh(['pgrep', '-x', 'Hyprland', '-o']).stdout.strip() == os.environ.get('PRIMARY_PID', sh(['pgrep', '-x', 'Hyprland', '-o']).stdout.strip()) and open('/proc/self/mountinfo').read().count('fuse.portal') == portal0)
print(f'{sum(res)}/{len(res)} passed')
sys.exit(0 if all(res) else 1)
