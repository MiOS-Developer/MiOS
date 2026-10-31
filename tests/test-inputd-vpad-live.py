#!/usr/bin/env python3
# AI-hint: LIVE opt-in (MIOS_LIVE_NESTED=1): the agent virtual controller drives Hypr3D smoothly inside an owned nested PiP session: `vpad create` (fixture-named), `mios-gen nested-session --layout pip --seat <s> --device <pad node>` binds it, `vpad assign` runs the session's `bridge --nested`, and eased stick ramps walk and turn the session's room camera with a gradual speed profile; a raw fixture-pad step and the unassigned pad are the contrast and negative controls.
# AI-related: tools/native/mios-inputd/src/vpad.rs, tools/native/mios-inputd/src/nested.rs, tools/native/mios-gen/src/nested_session.rs, usr/share/mios/mios.toml [keybindings.global.controller.agent_pad] [desktop.nested]
# AI-functions: Session, speeds, profile, main
# The only primary-compositor calls are reads (pgrep). Every pad is fixture-named (no live bridge opens one) and is held
# by an exclusive grab of this test or of the session's bridge whenever it moves, so no other reader on the host sees it.
#   MIOS_INPUTD_BIN   candidate mios-inputd (default: installed)
#   MIOS_VENDOR_TOML  candidate SSOT for the nested session (default: this tree's)
import fcntl, json, math, os, re, signal, socket, subprocess, sys, tempfile, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SSOT = os.environ.get('MIOS_VENDOR_TOML', str(ROOT / 'usr/share/mios/mios.toml'))
INPUTD = os.environ.get('MIOS_INPUTD_BIN', '/usr/libexec/mios/mios-inputd')
EVIOCGRAB = 0x40044590
res = []


def check(n, ok, d=''):
    res.append(bool(ok)); print(f"{'PASS' if ok else 'FAIL'} {n} {d}", flush=True)


def sh(a, env=None, timeout=30):
    return subprocess.run(a, capture_output=True, text=True, env=env, timeout=timeout)


def grab(path):
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    fcntl.ioctl(fd, EVIOCGRAB, 1)
    return fd


class Session:
    def __init__(self, d, s):
        self.d, self.s = Path(d), s
        self.sock = self.d / 'hypr' / s['instance']['instance'] / '.socket.sock'

    def ipc(self, cmd):
        c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        c.settimeout(2)
        c.connect(str(self.sock))
        c.sendall(cmd.encode())
        out = b''
        while True:
            b = c.recv(65536)
            if not b:
                break
            out += b
        c.close()
        return out.decode(errors='replace')

    def layout(self):
        t = self.ipc('/eval error(hl.plugin.hypr3d.export_layout(),0)')
        try:
            return json.loads(t[t.index('{'):t.rindex('}') + 1])
        except ValueError:
            return None

    def active(self):
        return 'true' in self.ipc('/eval error(tostring(hl.plugin.hypr3d and hl.plugin.hypr3d.active()),0)')


def record(sess, seconds):
    """(t, camera) samples straight from the session compositor's socket."""
    out, t0 = [], time.monotonic()
    while time.monotonic() - t0 < seconds:
        lay = sess.layout()
        if lay:
            out.append((time.monotonic() - t0, lay['camera']))
        time.sleep(0.015)
    return out


def speeds(samples, key):
    """Speed per sample: planar m/s for 'walk', rad/s of yaw for 'turn'."""
    v = []
    for (t0, a), (t1, b) in zip(samples, samples[1:]):
        dt = max(t1 - t0, 1e-3)
        d = math.hypot(b['x'] - a['x'], b['z'] - a['z']) if key == 'walk' else abs(b['yaw'] - a['yaw'])
        v.append((t1, d / dt))
    return v


def smooth(v, n=3):
    return [(v[i][0], sum(x for _, x in v[max(0, i - n + 1):i + 1]) / len(v[max(0, i - n + 1):i + 1])) for i in range(len(v))]


def profile(v):
    """Rise time (5% -> 80% of the peak), fall time (80% -> 5%) and the largest sample-to-sample jump (fraction of
    the peak) of a speed series. The rise time is the discriminating measure (a raw step against the eased pad, in
    the same room); the jump is reported only (at ~20 ms sampling it is sampling noise for both)."""
    if not v:
        return dict(peak=0)
    peak = max(x for _, x in v)
    if peak <= 0:
        return dict(peak=0)
    first = lambda f, frac: next((t for t, x in v if f(x, frac * peak)), None)
    t5 = first(lambda x, y: x >= y, 0.05)
    t80 = first(lambda x, y: x >= y, 0.8)
    after = [(t, x) for t, x in v if t80 is not None and t >= t80]
    last80 = max((t for t, x in after if x >= 0.8 * peak), default=None)
    t_stop = next((t for t, x in after if last80 is not None and t > last80 and x < 0.05 * peak), None)
    jump = max((abs(b - a) for (_, a), (_, b) in zip(v, v[1:])), default=0) / peak
    return dict(peak=round(peak, 3), rise=None if t5 is None or t80 is None else round(t80 - t5, 3),
                fall=None if last80 is None or t_stop is None else round(t_stop - last80, 3), jump=round(jump, 3))


def vpad(*a, env):
    r = sh([INPUTD, '--root', str(ROOT), 'vpad', *a], env=env, timeout=40)
    return r.returncode, (json.loads(r.stdout) if r.stdout.strip().startswith(('{', '[')) else None), r.stderr.strip()


def main():
    if os.environ.get('MIOS_LIVE_NESTED') != '1' or not os.environ.get('HYPRLAND_INSTANCE_SIGNATURE'):
        print('skip: live nested-session test (set MIOS_LIVE_NESTED=1 inside a MiOS desktop session)')
        return 0
    primary = sh(['pgrep', '-x', 'Hyprland', '-o']).stdout.strip()
    seat = f'vpl{os.getpid() % 100000}'
    host = {k: v for k, v in os.environ.items() if not (k.startswith('MIOS_') and 'TOML' in k)}
    env0 = dict(host, MIOS_VENDOR_TOML=SSOT)
    held, procs, launcher, npid = [], [], None, None
    try:
        code, out, err = vpad('create', '--pad', seat, '--fixture', env=host)
        check('vpad create (fixture-named agent pad)', code == 0 and out and out['ok'], err[-200:])
        node = out['created']['path']
        held.append(grab(node))  # nothing on the host sees the pad until the session's bridge owns it
        fx = subprocess.Popen([INPUTD, 'fixture-pad', '--layout', 'dualsense'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        procs.append(fx)
        fnode = json.loads(fx.stdout.readline())['path']
        held.append(grab(fnode))

        def step(*events, hold=0.0):
            fx.stdin.write(json.dumps({'events': [list(e) for e in events]}) + '\n'); fx.stdin.flush(); fx.stdout.readline()
            time.sleep(hold)

        launcher = subprocess.Popen(['/usr/bin/mios-gen', 'nested-session', '--layout', 'pip', '--seat', seat, '--device', node, '--device', fnode],
                                    env=env0, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        d = Path(os.environ['XDG_RUNTIME_DIR']) / f'mios-nested-desktop-{launcher.pid}'
        for _ in range(300):
            if (d / 'session.json').is_file() or launcher.poll() is not None:
                break
            time.sleep(0.2)
        if not (d / 'session.json').is_file():
            check('nested PiP session started', False, (launcher.stderr.read() if launcher.poll() is not None else '')[-400:])
            return 1
        s = json.loads((d / 'session.json').read_text())
        npid = s['instance']['pid']
        sess = Session(d, s)
        check('PiP session with the pad bound into its /dev/input', s['layout'] == 'pip' and {x['path'] for x in s['devices']} == {node, fnode}, s['devices'])
        check('labelled seat: a private room packet', s['pad_state'] and str(s['pad_state']).startswith(str(d)), s['pad_state'])
        nenv = dict(env0, XDG_RUNTIME_DIR=str(d), HYPRLAND_INSTANCE_SIGNATURE=s['instance']['instance'], WAYLAND_DISPLAY=s['instance']['wl_socket'])
        nenv.pop('DISPLAY', None)
        for _ in range(100):
            if sess.ipc('j/monitors').strip().startswith('['):
                break
            time.sleep(0.2)
        r = sh(['/usr/bin/mios-gen', 'hyprland-3d-toggle'], env=nenv, timeout=60)
        end = time.monotonic() + 10
        lay = None
        while time.monotonic() < end:
            lay = sess.layout()
            if lay and lay.get('owns_input') and lay.get('view_morph', 0) == 0:
                break
            time.sleep(0.1)
        check('the session room is open and owns input', lay and lay.get('owns_input'), (r.stdout + r.stderr).strip()[-200:])

        # NEGATIVE: unassigned, the pad drives nothing in the session (no path besides its bridge).
        vpad('stick', '--pad', seat, '--stick', 'left', '--y', '-1', '--ms', '300', '--hold-ms', '500', env=host)
        neg = record(sess, 1.5)
        moved = math.hypot(neg[-1][1]['x'] - neg[0][1]['x'], neg[-1][1]['z'] - neg[0][1]['z']) if len(neg) > 1 else 0
        check('NEGATIVE: an unassigned pad does not move the session camera', moved < 0.02, f'{moved:.3f} m')
        vpad('release', '--pad', seat, '--ms', '80', '--wait', env=host)

        for fd in held:
            os.close(fd)
        held.clear()
        code, out, err = vpad('assign', '--pad', seat, env=host)
        check('vpad assign starts the session bridge', code == 0 and out and out.get('assigned') == seat, err[-300:])
        blog = Path(os.environ['XDG_RUNTIME_DIR']) / f'mios-inputd/vpad/{seat}-bridge.log'
        end = time.monotonic() + 10
        while time.monotonic() < end and blog.read_text().count('"mode":"bridge"') < 2:
            time.sleep(0.1)
        text = blog.read_text()
        check('the bridge follows the session and owns both assigned pads', text.count('"mode":"bridge"') == 2 and '"nested":"follow"' in text, text[-400:])
        check('the bridge holds the pad (no other host reader sees it)', all(_cant_grab(p) for p in (node, fnode)))
        time.sleep(1.0)

        # Walk: an eased ramp in, a hold, an eased ramp out.
        c0 = sess.layout()['camera']
        vpad('stick', '--pad', seat, '--stick', 'left', '--y', '-1', '--ms', '1000', '--hold-ms', '600', env=host)
        walk = record(sess, 3.2)
        c1 = walk[-1][1]
        dist = math.hypot(c1['x'] - c0['x'], c1['z'] - c0['z'])
        fwd = (math.sin(c0['yaw']), -math.cos(c0['yaw']))
        along = ((c1['x'] - c0['x']) * fwd[0] + (c1['z'] - c0['z']) * fwd[1]) / max(dist, 1e-6)
        pw = profile(smooth(speeds(walk, 'walk')))
        check('the eased left stick walks the session camera forward', dist > 0.5 and along > 0.9, f'{dist:.2f} m, along {along:.2f}')
        check('walk speed rises gradually (no teleport)', pw.get('rise') is not None and pw['rise'] >= 0.25, pw)
        check('and eases to a stop', pw.get('fall') is not None and pw['fall'] >= 0.25, pw)

        # Turn: the right stick turns the camera with the same profile.
        y0 = sess.layout()['camera']['yaw']
        vpad('stick', '--pad', seat, '--stick', 'right', '--x', '0.6', '--ms', '900', '--hold-ms', '400', env=host)
        turn = record(sess, 2.8)
        pt = profile(smooth(speeds(turn, 'turn')))
        dyaw = abs(turn[-1][1]['yaw'] - y0)
        check('the eased right stick turns the session camera smoothly', dyaw > 0.2 and pt.get('rise') is not None and pt['rise'] >= 0.2, dict(dyaw=round(dyaw, 3), **pt))

        # CONTRAST: a raw step from the fixture pad (the same bridge and room).
        time.sleep(0.5)
        step((3, 1, -32767))
        raw = record(sess, 1.2)
        step((3, 1, 0))
        time.sleep(0.8)
        pr = profile(smooth(speeds(raw, 'walk')))
        check('CONTRAST: a raw stick step reaches walking speed much faster than the eased pad',
              pr.get('rise') is not None and pw.get('rise') is not None and pr['rise'] + 0.15 < pw['rise'], dict(step=pr, eased=pw))
        step((3, 3, round(0.6 * 32767)))
        rawt = record(sess, 1.0)
        step((3, 3, 0))
        time.sleep(0.6)
        prt = profile(smooth(speeds(rawt, 'turn')))
        check('CONTRAST: a raw right-stick step reaches turning speed faster than the eased pad',
              prt.get('rise') is not None and pt.get('rise') is not None and prt['rise'] + 0.1 < pt['rise'], dict(step=prt, eased=pt))
        print(json.dumps(dict(walk=pw, turn=pt, step_walk=pr, step_turn=prt)), flush=True)

        sh(['/usr/bin/mios-gen', 'hyprland-3d-toggle'], env=nenv, timeout=60)
    finally:
        for fd in held:
            try:
                os.close(fd)
            except OSError:
                pass
        vpad('destroy', '--pad', seat, env=host)
        for log in (Path(os.environ['XDG_RUNTIME_DIR']) / 'mios-inputd/vpad').glob(f'{seat}*.log'):
            if os.environ.get('MIOS_VPAD_KEEP_LOGS') != '1':
                log.unlink(missing_ok=True)
        for p in procs:
            try:
                p.stdin.close(); p.wait(5)
            except Exception:
                p.kill()
        if npid:
            try:
                os.kill(npid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        if launcher:
            try:
                launcher.wait(20)
            except subprocess.TimeoutExpired:
                launcher.terminate(); launcher.wait(10)
    time.sleep(1.0)
    left = Path('/proc/bus/input/devices').read_text()
    check('no pad of this test is left', f'mios-input-fixture-vpad-{seat}' not in left)
    reg = Path(os.environ['XDG_RUNTIME_DIR']) / 'mios/nested-sessions' / f'{seat}.json'
    check('the session left the registry', not reg.exists())
    check('primary compositor unchanged', sh(['pgrep', '-x', 'Hyprland', '-o']).stdout.strip() == primary)
    print(f'{sum(res)}/{len(res)} passed')
    return 0 if all(res) else 1


def _cant_grab(path):
    try:
        fd = grab(path)
    except OSError:
        return True
    os.close(fd)
    return False


if __name__ == '__main__':
    sys.exit(main())
