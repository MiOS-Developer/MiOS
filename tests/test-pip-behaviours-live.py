#!/usr/bin/env python3
# AI-hint: LIVE opt-in test (MIOS_LIVE_NESTED=1) of PiP behaviours in an owned nested MiOS PiP session with stand-in clients: automatic Spotify/Discord/browser PiP adoption, collision packing on both rails, minimum/launch/maximum size magnets, native stay-on-top (pinned layer, lost-pin repair, no z-order churn) and the fullscreen/maximize/Theatre matrix. Runs the OLD daemon+SSOT first (negative controls), then the candidate.
# AI-related: tools/native/mios-pip-gravity/src/{main.rs,gravity.rs,matrix.rs,window.rs}, usr/share/mios/mios.toml [desktop.pip_gravity], usr/share/doc/mios/desktop-pip.md, tests/test-pip-gravity-drawer-live.py
# AI-functions: Nest, run_label, main
#
# Usage (inside a MiOS desktop session; the primary compositor is only read, never dispatched to):
#   MIOS_LIVE_NESTED=1 MIOS_PIP_GRAVITY=<candidate> [MIOS_PIP_GRAVITY_OLD=<old binary> MIOS_PIP_SSOT_OLD=<old mios.toml>] \
#     [MIOS_NESTED_RUNTIME=<existing owned nested runtime dir>] [MIOS_PIP_HYPR3D_SO=/usr/lib64/mios/hypr3d.so] \
#     [MIOS_PIP_PHASES=rules,detection,sizes,matrix,pipfull,theatre] [MIOS_PIP_SQUEEZE_N=10] \
#     [MIOS_PIP_GRAVITY_TRACE=1 [MIOS_PIP_GRAVITY_PREFIX=<pre-fix candidate>]] \
#     python3 tests/test-pip-behaviours-live.py
# Without MIOS_NESTED_RUNTIME the test launches its own `mios-gen nested-session --layout pip` and stops it at the end.
import json, os, signal, subprocess, sys, tempfile, time, tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = Path(os.environ.get('MIOS_LIVE_OUT') or tempfile.mkdtemp(prefix='mios-pip-behaviours-'))
NEW = os.environ.get('MIOS_PIP_GRAVITY', '/usr/libexec/mios/mios-pip-gravity')
OLD = os.environ.get('MIOS_PIP_GRAVITY_OLD', '')
SSOT = os.environ.get('MIOS_VENDOR_TOML', str(REPO / 'usr/share/mios/mios.toml'))
SSOT_OLD = os.environ.get('MIOS_PIP_SSOT_OLD', '')
H3D = os.environ.get('MIOS_PIP_HYPR3D_SO', '')
PREFIX = os.environ.get('MIOS_PIP_GRAVITY_PREFIX', '')  # a pre-fix candidate binary: the room-exit negative control
TRACE = os.environ.get('MIOS_PIP_GRAVITY_TRACE') == '1'
PHASES = set((os.environ.get('MIOS_PIP_PHASES') or 'rules,detection,sizes,matrix,pipfull,theatre').split(','))
results = []


def check(name, ok, detail=''):
    results.append((name, bool(ok), str(detail)))
    print(f"{'PASS' if ok else 'FAIL'} {name} {detail}", flush=True)


def sh(a, env=None, timeout=30):
    return subprocess.run(a, capture_output=True, text=True, env=env, timeout=timeout)


def primary_state():
    pid = sh(['pgrep', '-x', 'Hyprland', '-o']).stdout.strip()
    return pid, open('/proc/self/mountinfo').read().count('fuse.portal')


class Nest:
    def __init__(self):
        self.launcher = None
        self.dir = None
        self.env = None
        self.pid = None
        self.owned = False

    def start(self):
        rt = os.environ.get('MIOS_NESTED_RUNTIME')
        if rt:
            self.dir = Path(rt)
        else:
            env = dict(os.environ, MIOS_VENDOR_TOML=SSOT)
            env.pop('MIOS_TOML', None)
            self.launcher = subprocess.Popen(['/usr/bin/mios-gen', 'nested-session', '--layout', 'pip'], env=env, stdout=open(OUT / 'launcher.log', 'w'), stderr=subprocess.STDOUT)
            self.dir = Path(os.environ['XDG_RUNTIME_DIR']) / f'mios-nested-desktop-{self.launcher.pid}'
            self.owned = True
        for _ in range(300):
            if (self.dir / 'session.json').is_file():
                s = json.loads((self.dir / 'session.json').read_text())
                self.pid = s['instance']['pid']
                self.env = dict(os.environ, XDG_RUNTIME_DIR=str(self.dir), HYPRLAND_INSTANCE_SIGNATURE=s['instance']['instance'], WAYLAND_DISPLAY=s['instance']['wl_socket'])
                for k in ('DISPLAY', 'MIOS_TOML', 'MIOS_VENDOR_TOML'):
                    self.env.pop(k, None)
                return True
            time.sleep(0.2)
        return False

    def stop(self):
        if not self.owned:
            return
        if self.pid:
            try:
                os.kill(self.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        try:
            self.launcher.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self.launcher.kill()

    def ctl(self, *a):
        return sh(['hyprctl', *a], env=self.env)

    def ev(self, lua):
        r = self.ctl('eval', lua)
        return r.stdout.strip()

    def clients(self):
        try:
            return json.loads(self.ctl('-j', 'clients').stdout or '[]')
        except ValueError:
            return []

    def client(self, addr):
        return next((c for c in self.clients() if c['address'] == addr), None)

    def monitor(self):
        m = json.loads(self.ctl('-j', 'monitors').stdout)[0]
        sc = m['scale']
        W, H = m['width'] / sc, m['height'] / sc
        r = m['reserved']
        return {'name': m['name'], 'output': (m['x'], m['y'], round(W), round(H)), 'area': (m['x'] + r[0], m['y'] + r[1], W - r[0] - r[2], H - r[1] - r[3])}

    def huds(self):
        out = []
        try:
            for mon in json.loads(self.ctl('-j', 'layers').stdout).values():
                for lvl in mon['levels'].values():
                    for l in lvl:
                        if l['namespace'] in ('mios-topbar', 'mios-dock', 'mios-appmenu') and l['h'] < 300:
                            out.append((l['x'], l['y'], l['w'], l['h']))
        except (ValueError, KeyError):
            pass
        return out

    def dsp(self, addr, call):
        return self.ev(f"hl.dispatch(hl.dsp.window.{call.replace('TARGET', repr('address:' + addr))})")

    def pixel(self, x, y, name):
        p = OUT / f'{name}.png'
        sh(['grim', str(p)], env=self.env)
        from PIL import Image
        im = Image.open(p).convert('RGB')
        s = im.size[0] / self.monitor()['output'][2]
        return im.getpixel((int(x * s), int(y * s)))


def colour(px):
    r, g, b = px
    return 'red' if r > 150 and b < 110 and g < 110 else 'blue' if b > 150 and r < 110 and g < 110 else f'other{px}'


def rect(c):
    return (c['at'][0], c['at'][1], c['size'][0], c['size'][1])


def overlap(a, b, slack=1):
    return a[0] < b[0] + b[2] - slack and b[0] < a[0] + a[2] - slack and a[1] < b[1] + b[3] - slack and b[1] < a[1] + a[3] - slack


def managed(c):
    return any(t.rstrip('*') == 'mios-pip-managed' for t in c.get('tags', []))


# Processes of stand-ins already launched: a stand-in's own extra windows (the Spotify stand-in's mini-player maps after
# its main window) must never be taken for a later stand-in with the same class and title.
SPAWNED_PIDS = set()


def standin(nest, name, cls, title, colour_='', inner='exec sleep 900', dynamic=False, floating=False):
    """Launch an Alacritty stand-in with a given class/initial title (and solid colour); return its address once mapped.
    dynamic: the client may retitle itself later. Alacritty disables dynamic titles for an explicit --title, so the
    initial title then comes from the stand-in's own config file."""
    col = {'red': 'ff0000', 'blue': '0000ff'}.get(colour_)
    cfg = OUT / (f'ala-{name}.toml' if dynamic else f'ala-{colour_ or "plain"}.toml')
    cfg.write_text('[window]\nopacity = 1.0\ndecorations = "None"\ndynamic_title = %s\n' % ('true' if dynamic else 'false') + (f'title = "{title}"\n' if dynamic else '') + (f'[colors.primary]\nbackground = "#{col}"\nforeground = "#{col}"\n' if col else ''))
    script = OUT / f'standin-{name}.sh'
    title_arg = '' if dynamic else f" --title '{title}'"
    script.write_text(f"#!/bin/sh\nexec alacritty --config-file '{cfg}' --class '{cls}'{title_arg} -e sh -c '{inner}'\n")
    script.chmod(0o700)
    before = {c['address'] for c in nest.clients()}
    nest.ev(f"hl.dispatch(hl.dsp.exec_cmd('sh {script}'))")
    for _ in range(150):
        new = [c for c in nest.clients() if c['address'] not in before and c['class'] == cls and c['initialTitle'] == title and c['pid'] not in SPAWNED_PIDS]
        if new:
            addr = new[0]['address']
            SPAWNED_PIDS.add(new[0]['pid'])
            if colour_:
                nest.dsp(addr, "tag({window=TARGET,tag='+hyprglass_disabled'})")
            if floating:
                nest.dsp(addr, "float({window=TARGET,action='enable'})")
            return addr, time.monotonic()
        time.sleep(0.02)
    raise RuntimeError(f'stand-in {name} never mapped')


def close_all(nest):
    for c in nest.clients():
        if c['class'] != 'mios-nested-terminal':
            nest.dsp(c['address'], 'close({window=TARGET})')
    for _ in range(100):
        if all(c['class'] == 'mios-nested-terminal' for c in nest.clients()):
            return
        time.sleep(0.1)


def geoms(nest, addrs):
    by = {c['address']: c for c in nest.clients()}
    return {a: (rect(by[a]) if a in by else None) for a in addrs}


def settle(nest, addrs, still=1.5, timeout=15.0):
    last, since, end = None, time.monotonic(), time.monotonic() + timeout
    while time.monotonic() < end:
        g = geoms(nest, addrs)
        if g != last:
            last, since = g, time.monotonic()
        elif time.monotonic() - since >= still:
            return g
        time.sleep(0.05)
    return last


def sample(nest, addrs, seconds):
    seen, end = [], time.monotonic() + seconds
    while time.monotonic() < end:
        seen.append(geoms(nest, addrs))
        time.sleep(0.05)
    return seen


def moved(samples, ref, skip=()):
    return sorted({a for s in samples for a, g in s.items() if a not in skip and g != ref[a]})


class Daemon:
    def __init__(self, nest, binary, ssot, label):
        env = dict(nest.env, MIOS_VENDOR_TOML=ssot)
        self.p = subprocess.Popen([binary], env=env, stdout=open(OUT / f'daemon-{label}.log', 'a'), stderr=subprocess.STDOUT)

    def stop(self):
        self.p.send_signal(signal.SIGTERM)
        try:
            self.p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.p.kill()


def margin_px(ssot):
    d = tomllib.load(open(ssot, 'rb'))
    v = d['desktop']['pip_gravity']['rail_margin']
    if isinstance(v, str):
        for k in v.split('.'):
            d = d[k]
        v = d
    return float(v)


def detection(nest, label, cand):
    close_all(nest)
    spawn = {
        'discord-popout': ('discord', 'Discord Popout', "sleep 1.5; printf \"\\033]2;Voice Connected\\007\"; exec sleep 900", True),
        'discord-main': ('discord', 'Friends - Discord', 'exec sleep 900', False),
        'vesktop-popout': ('vesktop', 'Discord Popout', 'exec sleep 900', False),
        'spotify-main': ('spotify', 'Spotify Premium', 'alacritty msg create-window --class chromium-browser --title \"Any track • Any artist\" -e sleep 900; exec sleep 900', False),
        'chromium-other': ('chromium-browser', 'Any track • Any artist', 'exec sleep 900', False),
        'chromium-pip': ('chromium', 'Picture in picture', 'exec sleep 900', False),
        'firefox-pip': ('firefox', 'Picture-in-Picture', 'exec sleep 900', False),
    }
    addr, t0 = {}, {}
    for name, (cls, title, inner, dyn) in spawn.items():
        addr[name], t0[name] = standin(nest, name, cls, title, inner=inner, dynamic=dyn)
    # the Spotify mini-player stand-in: the second window of the Spotify stand-in's process
    for _ in range(150):
        mini = [c for c in nest.clients() if c['class'] == 'chromium-browser' and c['pid'] == nest.client(addr['spotify-main'])['pid']]
        if mini:
            addr['spotify-mini'], t0['spotify-mini'] = mini[0]['address'], time.monotonic()
            break
        time.sleep(0.02)
    # Native shader rules (rendered from this SSOT into the nested compositor): PiP and Discord popouts open without the
    # glass-dissolve shader. The float/pin window rules are checked without any daemon in the `rules` phase.
    by = {c['address']: c for c in nest.clients()}
    cp, other = by.get(addr['chromium-pip']), by.get(addr['chromium-other'])
    noshader = lambda c: any(t.startswith('shader_open:') and 'none-open' in t for t in c.get('tags', []))
    check(f'[{label}] PiP and Discord popouts open without the dissolve shader; an ordinary Chromium window keeps it', cp and noshader(cp) and noshader(by[addr['discord-popout']]) and other and not noshader(other) and not other['pinned'], other and other['tags'])
    adopted = {}
    end = time.monotonic() + 4.0
    while time.monotonic() < end:
        by = {c['address']: c for c in nest.clients()}
        for name, a in addr.items():
            if name not in adopted and a in by and managed(by[a]) and by[a]['pinned'] and by[a]['floating']:
                adopted[name] = round((time.monotonic() - t0[name]) * 1000)
        time.sleep(0.01)
    want = {'discord-popout', 'spotify-mini', 'firefox-pip'} | ({'vesktop-popout', 'chromium-pip'} if cand else set())
    never = {'discord-main', 'spotify-main', 'chromium-other'}
    got = set(adopted)
    check(f'[{label}] adopted exactly the PiP stand-ins', got == want, f'adopted={adopted} want={sorted(want)}')
    check(f'[{label}] never adopted the Discord/Spotify main windows or an unrelated Chromium window', not (got & never), sorted(got & never))
    if not cand:
        check(f'[{label}] NEGATIVE CONTROL: Vesktop popout / Chromium "Picture in picture" are missed by the old rules', not ({'vesktop-popout', 'chromium-pip'} & got), sorted(got))
    c = nest.client(addr['discord-popout'])
    check(f'[{label}] Discord popout stays PiP after its title changes', c and c['title'] == 'Voice Connected' and managed(c), c and c['title'])
    return addr, adopted


def packing(nest, label, addrs):
    g = settle(nest, addrs)
    mon, huds = nest.monitor(), nest.huds()
    ax, ay, aw, ah = mon['area']
    rs = [r for r in g.values() if r]
    bad = [(a, b) for i, a in enumerate(rs) for b in rs[:i] if overlap(a, b)]
    check(f'[{label}] {len(rs)} PiPs packed without collisions', not bad and len(rs) == len(addrs), f'{bad} {g}')
    check(f'[{label}] every PiP inside the HUD work area and clear of HUD bars', all(r[0] >= ax - 1 and r[1] >= ay - 1 and r[0] + r[2] <= ax + aw + 1 and r[1] + r[3] <= ay + ah + 1 for r in rs) and not any(overlap(r, h) for r in rs for h in huds), f'area={mon["area"]} huds={huds}')
    m = margin_px(SSOT)
    check(f'[{label}] every PiP on a flank rail', all(abs(r[0] - (ax + m)) <= 1 or abs(r[0] + r[2] - (ax + aw - m)) <= 1 for r in rs), rs)
    return g


def sizes(nest, label, cand):
    close_all(nest)
    a, _ = standin(nest, 'size', 'pipt-size', 'Picture-in-Picture')
    g = settle(nest, [a])[a]
    launch = g[2]
    mon = nest.monitor()
    m = margin_px(SSOT)
    out = {'launch': g}

    def to(w, h):
        nest.dsp(a, f'resize({{window=TARGET,x={int(w)},y={int(h)},exact=true}})')
        time.sleep(0.2)
        return settle(nest, [a])[a]

    asp = g[2] / g[3]
    out['tiny'] = to(60, 60 / asp)
    out['near_launch'] = to(launch + 14, (launch + 14) / asp)
    out['huge'] = to(mon['area'][2] - 4, (mon['area'][2] - 4) / asp)
    hmax = out['huge']
    out['near_max'] = to(hmax[2] - 15, (hmax[2] - 15) / asp)
    out['near_min'] = to(175, 175 / asp)
    print(f'[{label}] sizes {out} area={mon["area"]}', flush=True)
    check(f'[{label}] near the launch (flank) width snaps exactly to it', abs(out['near_launch'][2] - launch) <= 1, out['near_launch'])
    check(f'[{label}] the maximum keeps the aspect inside the HUD work area', hmax[3] <= mon['area'][3] - 2 * m + 1 and hmax[2] <= mon['area'][2] - 2 * m + 1, hmax)
    if cand:
        check(f'[{label}] a resize below the minimum springs back to min_width_px', abs(out['tiny'][2] - 160) <= 1, out['tiny'])
        # The aspect-true maximum follows the current (integer-rounded) aspect, so allow the daemon's own settle
        # threshold (SETTLE_PX = 3) on the free axis; the binding axis must match exactly.
        check(f'[{label}] within size_snap_px of the maximum snaps exactly to it', abs(out['near_max'][2] - hmax[2]) <= 3 and abs(out['near_max'][3] - hmax[3]) <= 1, f"{out['near_max']} max={hmax}")
        check(f'[{label}] within size_snap_px of the minimum snaps exactly to it', abs(out['near_min'][2] - 160) <= 1, out['near_min'])
    else:
        check(f'[{label}] NEGATIVE CONTROL: old daemon leaves a 60 px PiP (no pixel minimum)', out['tiny'][2] < 100, out['tiny'])
        check(f'[{label}] NEGATIVE CONTROL: old daemon has no maximum magnet', abs(out['near_max'][2] - hmax[2]) > 5, out['near_max'])
    return out


def crowd(nest, n=6):
    close_all(nest)
    addrs = [standin(nest, f'm{i}', f'pipt-m{i}', 'Picture-in-Picture', colour_='red')[0] for i in range(n)]
    return addrs, settle(nest, addrs, still=2.0, timeout=20)


def matrix(nest, label, cand):
    addrs, g0 = crowd(nest)
    mon0 = nest.monitor()
    cover, _ = standin(nest, 'cover', 'pipt-cover', 'cover', colour_='blue', floating=True)
    time.sleep(0.5)
    g0 = settle(nest, addrs)
    p0 = g0[addrs[0]]
    cx, cy = p0[0] + p0[2] / 2, p0[1] + p0[3] / 2

    for mode in ('fullscreen', 'maximized'):
        nest.dsp(cover, f"fullscreen({{window=TARGET,mode='{mode}',action='set'}})")
        s = sample(nest, addrs, 3.0)
        px = colour(nest.pixel(cx, cy, f'{label}-other-{mode}'))
        nest.dsp(cover, f"fullscreen({{window=TARGET,mode='{mode}',action='unset'}})")
        s2 = sample(nest, addrs, 3.0)
        mv, mv2 = moved(s, g0), moved(s2, g0)
        check(f'[{label}] PiP stays visible above another {mode} window (native pinned layer)', px == 'red', px)
        if cand or mode == 'maximized':
            check(f'[{label}] no PiP moves while another window is {mode} or after it returns', not mv and not mv2, f'{mv} {mv2}')
        else:
            check(f'[{label}] NEGATIVE CONTROL: old daemon moves PiPs when another window goes {mode} and back', mv or mv2, f'{mv} {mv2}')
        g0 = settle(nest, addrs)

    # lost pin: the native stay-on-top layer is repaired
    nest.dsp(cover, f"move({{window=TARGET,x={int(p0[0] + 10)},y={int(p0[1] + 10)}}})")
    nest.dsp(cover, f"resize({{window=TARGET,x={int(p0[2])},y={int(p0[3])},exact=true}})")
    nest.dsp(cover, "alter_zorder({window=TARGET,mode='top'})")
    nest.dsp(addrs[0], "pin({window=TARGET,action='disable'})")
    time.sleep(1.5)
    c = nest.client(addrs[0])
    px = colour(nest.pixel(cx + 20, cy + 20, f'{label}-lost-pin'))
    if cand:
        check(f'[{label}] an unpinned PiP is re-pinned and drawn above the window covering it', c['pinned'] and px == 'red', f'pinned={c["pinned"]} pixel={px}')
    else:
        check(f'[{label}] NEGATIVE CONTROL: old daemon leaves the PiP unpinned (covered)', not c['pinned'], f'pinned={c["pinned"]} pixel={px}')
    nest.dsp(addrs[0], "pin({window=TARGET,action='enable'})")
    # no z-order churn: an unpinned floating window far from every PiP does not re-stack them
    nest.dsp(cover, "move({window=TARGET,x=600,y=400})")
    nest.dsp(cover, "resize({window=TARGET,x=80,y=60,exact=true})")
    time.sleep(0.3)
    order0 = [c['address'] for c in nest.clients()]
    nest.dsp(cover, "alter_zorder({window=TARGET,mode='top'})")
    time.sleep(1.0)
    order1 = [c['address'] for c in nest.clients()]
    churn = order1.index(cover) != len(order1) - 1
    if cand:
        check(f'[{label}] an unpinned window above PiPs causes no PiP re-raise (pinned layer already covers it)', not churn, order1)
    else:
        check(f'[{label}] NEGATIVE CONTROL: old daemon re-raises PiPs for any floating window', churn, order1)
    mon1 = nest.monitor()
    check(f'[{label}] nested output unchanged during the matrix (results comparable)', mon0['output'] == mon1['output'], f'{mon0} {mon1}')
    nest.dsp(cover, 'close({window=TARGET})')
    return addrs


# Enough PiPs to squeeze both rails below their natural size: dropping a maximized/fullscreen PiP from the plan (the old
# daemon) then lets its neighbours grow into the freed room, which is what the reserved slot prevents.
SQUEEZE_N = int(os.environ.get('MIOS_PIP_SQUEEZE_N', '10'))


def matrix_pip(nest, label, cand):
    addrs, g0 = crowd(nest, SQUEEZE_N)
    g0 = settle(nest, addrs)
    seen = []
    # The subject sits on the squeezed rail (smallest PiP): only there does dropping its slot free room for neighbours.
    k = min(addrs, key=lambda a: g0[a][2])
    for mode in ('maximized', 'fullscreen'):
        nest.dsp(k, f"fullscreen({{window=TARGET,mode='{mode}',action='set'}})")
        s = sample(nest, addrs, 3.0)
        held = {x[k] for x in s}
        others = moved(s, g0, skip=(k,))
        c = nest.client(k)
        area = nest.monitor()['area']
        check(f'[{label}] PiP {mode}: gravity leaves it alone', len(held) == 1 and c['fullscreen'] != 0, held)
        if mode == 'maximized':
            r = rect(c)
            check(f'[{label}] PiP maximize stays inside the HUD work area', r[1] >= area[1] - 1 and r[1] + r[3] <= area[1] + area[3] + 1, f'{r} area={area}')
        nest.dsp(k, f"fullscreen({{window=TARGET,mode='{mode}',action='unset'}})")
        g1 = settle(nest, addrs)
        c = nest.client(k)
        if cand:
            check(f'[{label}] PiP {mode}: neighbours keep their slots', not others, others)
            check(f'[{label}] PiP {mode} restore: exact slot, pinned, every PiP where it was', g1 == g0 and c['pinned'], f'{g0} -> {g1}')
        else:
            seen.append(bool(others or g1 != g0))
            print(f'OBSERVED [{label}] {mode} PiP: neighbours moved={others} layout restored={g1 == g0}', flush=True)
        g0 = g1

    if not cand:
        # Recorded, not asserted: the old daemon planned from current (already squeezed) sizes, so dropping a slot gives
        # its neighbours nothing to grow back to and only layout-dependent position effects remain (reproduced in 1 of
        # 4 runs). The reliable controls are matrix::tests (dropping the slot) and the Theatre phase below.
        print(f'OBSERVED [{label}] squeezed rails, maximized/fullscreen PiP moved neighbours: {seen}', flush=True)
    return addrs


def room(nest, open_):
    if open_:
        name = nest.monitor()['name']
        nest.ev('local p = assert(hl.plugin.hypr3d); p.config({world = {monitor = "%s", grid = false}, render = {scale = 1, msaa = 0, fxaa = false, sharpen = 0, texture_supersample = false, anisotropy = 1}}); if not p.active() then p.open() end' % name)
    else:
        nest.ev('if hl.plugin.hypr3d and hl.plugin.hypr3d.active() then hl.plugin.hypr3d.close() end')
    time.sleep(3.0 if open_ else 2.0)
    f = Path(nest.env['XDG_RUNTIME_DIR']) / 'pip-room.txt'
    nest.ev("local f=io.open(os.getenv('XDG_RUNTIME_DIR')..'/pip-room.txt','w');f:write(tostring(hl.plugin.hypr3d.active()));f:close()")
    return f.read_text().strip() == 'true'


def theatre(nest, label, cand):
    if 'hypr3d' not in nest.ctl('plugin', 'list').stdout:
        r = nest.ctl('plugin', 'load', H3D).stdout.strip()
        if r != 'ok':
            check(f'[{label}] Hypr3D loaded in the nested session', False, r)
            return
    # Room 1: the room owns placement (flat gravity never pulls a moved PiP back) and adoption continues.
    addrs, g0 = crowd(nest)
    try:
        active = room(nest, True)
        check(f'[{label}] Hypr3D room active in the nested session', active)
        if not active:
            return
        k = addrs[1]
        r = g0[k]
        nest.dsp(k, f'move({{window=TARGET,x={int(r[0] + 150)},y={int(r[1] + 40)}}})')
        time.sleep(0.3)
        dragged = rect(nest.client(k))
        s = sample(nest, [k], 2.0)
        check(f'[{label}] room active: flat gravity does not pull a moved PiP back', all(x[k] == dragged for x in s), f'{r} -> {dragged} -> {s[-1][k]}')
        pop, t = standin(nest, 'room-pop', 'discord', 'Discord Popout')
        ok = False
        for _ in range(100):
            c = nest.client(pop)
            if c and managed(c):
                ok = True
                break
            time.sleep(0.03)
        check(f'[{label}] room active: a Discord popout is still adopted', ok, round((time.monotonic() - t) * 1000))
    finally:
        room(nest, False)
    # Room 2 on squeezed rails: Theatre (a PiP maximized inside the room), leave the room, restore it.
    addrs, g0 = crowd(nest, SQUEEZE_N)
    g0 = settle(nest, addrs)
    log = OUT / f'daemon-{label}.log'
    mark = log.stat().st_size if log.exists() else 0
    th = min(addrs, key=lambda a: g0[a][2])  # on the squeezed rail
    print(f'TRACE [{label}] theatre PiP {th} slot {g0[th]}', flush=True)
    try:
        if not room(nest, True):
            check(f'[{label}] Hypr3D room reopened for Theatre', False)
            return
        nest.dsp(th, "fullscreen({window=TARGET,mode='maximized',action='set'})")
        time.sleep(2.0)
    finally:
        room(nest, False)
    s = sample(nest, addrs, 2.5)
    print(f'TRACE [{label}] after room, before unmaximize: {s[0]} ... {s[-1]}', flush=True)
    others = moved(s, g0, skip=(th,))
    nest.dsp(th, "fullscreen({window=TARGET,mode='maximized',action='unset'})")
    g1 = settle(nest, addrs)
    # Deterministic room-exit control (the geometric checks above can be masked by the launch magnet): with
    # MIOS_PIP_GRAVITY_TRACE=1 the daemon logs every natural-size update; none may come from the room's close.
    drift = [l for l in (log.read_bytes()[mark:].decode(errors='replace').splitlines() if TRACE and log.exists() else []) if 'natural size' in l]
    if label == 'prefix':
        # Recorded, not asserted: Hypr3D animates the windows back on close only in some runs (after the earlier
        # phases; not in a fresh Theatre-only session), so the pre-fix drift is not reproducible on demand. The
        # deterministic negative control is track_tests (the release on the first flat tick fails them).
        print(f'OBSERVED [{label}] NEGATIVE CONTROL: pre-fix room-exit natural-size updates={len(drift)} neighbours moved={others} layout restored={g1 == g0}', flush=True)
        return
    if cand and TRACE:
        check(f'[{label}] leaving the room and the Theatre restore never change a natural size', not drift, drift[:3])
    if cand:
        check(f'[{label}] after Theatre the neighbours kept their slots', not others, others)
        check(f'[{label}] Theatre PiP restores to its slot and the layout is unchanged', g1 == g0 and nest.client(th)['pinned'], f'{g0} -> {g1}')
    else:
        # Recorded, not asserted: the old daemon dropped every track in the room and waited settle_ms afterwards, so it
        # usually restored exactly (verifier 2/2, this lane 1/1). The room-exit negative control is the pre-fix
        # candidate (MIOS_PIP_GRAVITY_PREFIX, trace-based) and track_tests.
        print(f'OBSERVED [{label}] Theatre on squeezed rails: neighbours moved={others} layout restored={g1 == g0}', flush=True)


def rules(nest):
    """Native window rules alone (NO daemon): Firefox- and Chromium-family PiP windows float and pin at map; an ordinary
    Chromium window does not. A mobile-classified output tiles every new window (mios-mobile-<output>), overriding
    float rules by design, so there the check is skipped with the reason (run with a mobile-off SSOT copy)."""
    close_all(nest)
    try:
        mob = json.loads((nest.dir / 'mios-mobile-outputs.json').read_text())['outputs']
    except (OSError, ValueError, KeyError):
        mob = {}
    mobile = any(v.get('mobile') for v in mob.values())
    got = {}
    for name, cls, title in [('rule-cp', 'chromium', 'Picture in picture'), ('rule-fp', 'firefox', 'Picture-in-Picture'), ('rule-other', 'chromium-browser', 'New Tab - Chromium')]:
        a, _ = standin(nest, name, cls, title)
        time.sleep(0.3)
        c = nest.client(a)
        got[name] = (c['floating'], c['pinned'])
    if mobile:
        print(f'SKIP rules: nested output classified mobile ({mob}); the mobile layout tiles every new window. {got}', flush=True)
        return
    check('[rules] mios-chromium-pip floats and pins Chromium "Picture in picture" at map (no daemon)', got['rule-cp'] == (True, True), got)
    check('[rules] mios-browser-pip floats and pins Firefox "Picture-in-Picture" at map (no daemon)', got['rule-fp'] == (True, True), got)
    check('[rules] an ordinary Chromium window is neither floated nor pinned by the PiP rules', got['rule-other'] == (False, False), got)
    close_all(nest)


def phase(nest, name, fn, *a):
    """Run one phase; when the nested output changed meanwhile (the PRIMARY compositor re-laid this session's PiP
    window, e.g. another lane opened a session), its geometry checks are not comparable: drop them and rerun."""
    for attempt in range(4):
        mark, o0 = len(results), nest.monitor()['output']
        r = fn(*a)
        o1 = nest.monitor()['output']
        if o0 == o1:
            return r
        del results[mark:]
        print(f'RETRY {name}: nested output changed {o0} -> {o1} (primary re-laid the session window)', flush=True)
        close_all(nest)
        time.sleep(2.0)
    check(f'{name}: nested output stable for one whole phase', False, 'environment kept resizing the session window')
    return r


def run_label(nest, label, binary, ssot, cand):
    d = Daemon(nest, binary, ssot, label)
    try:
        time.sleep(0.5)

        def detect_and_pack():
            addr, adopted = detection(nest, label, cand)
            packing(nest, label, list(dict.fromkeys(addr[n] for n in adopted)))
        if 'detection' in PHASES:
            phase(nest, f'[{label}] detection+packing', detect_and_pack)
        if 'sizes' in PHASES:
            phase(nest, f'[{label}] sizes', sizes, nest, label, cand)
        if 'matrix' in PHASES:
            phase(nest, f'[{label}] matrix', matrix, nest, label, cand)
        if 'pipfull' in PHASES:
            phase(nest, f'[{label}] PiP maximize/fullscreen', matrix_pip, nest, label, cand)
        if H3D and 'theatre' in PHASES:
            phase(nest, f'[{label}] theatre', theatre, nest, label, cand)
    except Exception as e:
        check(f'[{label}] driver', False, repr(e))
    finally:
        d.stop()
        close_all(nest)


def main():
    p0 = primary_state()
    nest = Nest()
    try:
        check('nested PiP session', nest.start(), nest.dir)
        if 'rules' in PHASES:
            time.sleep(2.0)
            rules(nest)
        if OLD:
            old_ssot = SSOT_OLD or str(OUT / 'old-mios.toml')
            if not SSOT_OLD:
                (OUT / 'old-mios.toml').write_text(sh(['git', '-C', str(REPO), 'show', 'main:usr/share/mios/mios.toml']).stdout)
            run_label(nest, 'old', OLD, old_ssot, False)
        if PREFIX:
            run_label(nest, 'prefix', PREFIX, SSOT, True)
        run_label(nest, 'candidate', NEW, SSOT, True)
    finally:
        nest.stop()
        if nest.owned:
            check('nested runtime cleaned up', not nest.dir.exists())
        check('primary compositor and portal mounts unchanged', primary_state() == p0, f'{p0} -> {primary_state()}')
    n = sum(1 for r in results if r[1])
    print(f'{n}/{len(results)} passed; artifacts in {OUT}')
    return 0 if n == len(results) else 1


if __name__ == '__main__':
    if os.environ.get('MIOS_LIVE_NESTED') != '1' or not os.environ.get('HYPRLAND_INSTANCE_SIGNATURE'):
        print('skip: live nested-session test (set MIOS_LIVE_NESTED=1 inside a MiOS desktop session)')
        sys.exit(0)
    sys.exit(main())
