#!/usr/bin/env python3
# AI-hint: LIVE opt-in test (MIOS_LIVE_NESTED=1) of nested-session launch targets and device binding inside an OWNED nested parent: the stock launcher opens the parent as a PiP on the primary; every other session is launched INTO that parent, so no primary output, mode or window is touched. Targets: output (fullscreen on an attached output), remote (the remote display output; a headless output is attempted first), hypr3d (a room window), seat identity/registry, and a private uinput joystick bound into one session only.
# AI-related: tools/native/mios-gen/src/nested_session.rs, tools/native/mios-gen/src/nested_devices.rs, usr/share/mios/mios.toml [desktop.nested], tests/test-nested-multiuse.py, tests/test-nested-session-scaling.py
# AI-functions: Session, Live
# Env: MIOS_GEN (candidate launcher), MIOS_NESTED_H3D_SO (plugin loaded ONLY into the owned parent; default the installed
# /usr/lib64/mios/hypr3d.so), MIOS_LIVE_OUT (evidence directory). Positive and negative control for every behaviour.
import hashlib, importlib.util, json, math, os, re, select, signal, struct, subprocess, sys, tempfile, time, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('nested_multiuse', HERE / 'test-nested-multiuse.py')
nm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nm)

LIVE = os.environ.get('MIOS_LIVE_NESTED') == '1' and os.environ.get('HYPRLAND_INSTANCE_SIGNATURE') and os.environ.get('WAYLAND_DISPLAY')
GEN = os.environ.get('MIOS_GEN', '/usr/bin/mios-gen')
H3D_SO = os.environ.get('MIOS_NESTED_H3D_SO', '/usr/lib64/mios/hypr3d.so')
OUT = Path(os.environ.get('MIOS_LIVE_OUT') or tempfile.mkdtemp(prefix='mios-nested-multiuse-live-'))
STATUS = Path('/tmp/hypr3d-status.txt')


def sh(args, env=None, timeout=30):
    return subprocess.run(args, capture_output=True, text=True, env=env, timeout=timeout)


def sha(p):
    try:
        return hashlib.sha256(Path(p).read_bytes()).hexdigest()
    except OSError:
        return 'absent'


def primary_state():
    mons = json.loads(sh(['hyprctl', '-j', 'monitors', 'all']).stdout)
    return {'pid': sh(['pgrep', '-x', 'Hyprland', '-o']).stdout.strip(),
            'monitors': sorted((m['name'], m['width'], m['height'], m['scale'], m['x'], m['y'], m['transform']) for m in mons)}


def ino(p):
    st = os.stat(p)
    return [st.st_ino, st.st_dev]


def fit_scale(w, h, W, H, lo, hi):
    s = max(lo, min(hi, min(w / W, h / H)))
    k = int(s * 120 + 0.5)
    for d in range(241):
        for c in (k - d, k + d):
            if math.ceil(lo * 120) <= c <= math.floor(hi * 120) and (w * 120) % c == 0 and (h * 120) % c == 0:
                return c / 120
    return 1.0


class Session:
    """One `mios-gen nested-session` launch in `parent` (None = the primary) and its descriptor."""
    n = 0
    all = []

    def __init__(self, ssot, args, parent=None, wait=True):
        Session.n += 1
        Session.all.append(self)
        self.log = OUT / f'launcher-{Session.n}-{re.sub(r"[^A-Za-z0-9_.-]+", "_", "-".join(a.strip("-") for a in args))[:60]}.log'
        env = {k: v for k, v in os.environ.items() if k not in ('MIOS_TOML', 'DISPLAY', 'TMUX', 'TMUX_PANE')}
        env['MIOS_VENDOR_TOML'] = str(ssot)
        if parent:
            env.update(XDG_RUNTIME_DIR=str(parent.dir), HYPRLAND_INSTANCE_SIGNATURE=parent.sig, WAYLAND_DISPLAY=parent.wl)
        self.parent_env, self.parent = env, parent
        self.logf = open(self.log, 'w')
        self.proc = subprocess.Popen([GEN, 'nested-session', *args], env=env, stdout=self.logf, stderr=subprocess.STDOUT)
        # where the parent finds the session (the runtime itself, or a link to it for a session inside a session)
        self.link = self.dir = Path(env['XDG_RUNTIME_DIR']) / f'mios-nested-desktop-{self.proc.pid}'
        self.d = None
        if wait:
            self.wait_ready()

    def wait_ready(self, timeout=60):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.proc.poll() is not None:
                raise AssertionError(f'launcher exited {self.proc.returncode}: {self.log.read_text()[-1500:]}')
            if (self.dir / 'session.json').is_file():
                self.d = json.loads((self.dir / 'session.json').read_text())
                self.dir = Path(self.d['runtime'])  # the real runtime: its socket paths fit sun_path
                inst = self.d['instance']
                self.sig, self.wl, self.pid = inst['instance'], inst['wl_socket'], inst['pid']
                self.env = {k: v for k, v in os.environ.items() if k not in ('MIOS_TOML', 'DISPLAY')}
                self.env.update(XDG_RUNTIME_DIR=str(self.dir), HYPRLAND_INSTANCE_SIGNATURE=self.sig, WAYLAND_DISPLAY=self.wl)
                return self
            time.sleep(0.2)
        raise AssertionError('no session.json')

    def finish(self, timeout=30):
        """The launcher's exit status and log for a launch expected to refuse."""
        try:
            self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            raise
        finally:
            self.logf.close()
        return self.proc.returncode, self.log.read_text()

    def hc(self, *a):
        return sh(['hyprctl', *a], env=self.env)

    def j(self, what):
        return json.loads(self.hc('-j', *what.split()).stdout)

    def ev(self, lua):
        return self.hc('eval', lua).stdout.strip()

    def run_inside(self, argv, out, timeout=20):
        """Run argv inside this session's namespace (as a child of its compositor) and return its JSON output file."""
        out.unlink(missing_ok=True)
        r = self.ev('hl.exec_cmd(%s)' % json.dumps(' '.join(nm_shell(a) for a in argv)))
        assert r == 'ok', r
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if out.is_file() and out.stat().st_size:
                try:
                    return json.loads(out.read_text())
                except ValueError:
                    pass
            time.sleep(0.2)
        raise AssertionError(f'no output from inside the session: {argv}')

    def stop(self):
        if getattr(self, 'pid', None):
            try:
                os.kill(self.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        try:
            self.proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        self.logf.close()


def nm_shell(s):
    return "'" + s.replace("'", "'\\''") + "'"


PROBE = r'''
import json, os, select, struct, sys, time
out, want, other = sys.argv[1], sys.argv[2], sys.argv[3]
r = {"listing": sorted(os.listdir("/dev/input")), "env_seat": os.environ.get("MIOS_" "NESTED_SEAT"), "events": [], "other": None,
     "tmp": list(os.stat("/tmp")[1:3]), "status": list(os.stat("/tmp/hypr3d-status.txt")[1:3]) if os.path.exists("/tmp/hypr3d-status.txt") else None}
try:
    os.close(os.open(other, os.O_RDONLY)); r["other"] = "opened"
except OSError as e:
    r["other"] = e.errno
if want != "-":
    fd = os.open(want, os.O_RDONLY | os.O_NONBLOCK)
    end = time.monotonic() + 15
    while time.monotonic() < end and len(r["events"]) < 2:
        if select.select([fd], [], [], 0.5)[0]:
            data = os.read(fd, 24 * 64)
            for i in range(0, len(data) - 23, 24):
                _, _, t, c, v = struct.unpack("llHHi", data[i:i + 24])
                if t == 1:
                    r["events"].append([c, v])
    os.close(fd)
open(out + ".tmp", "w").write(json.dumps(r)); os.rename(out + ".tmp", out)
'''


@unittest.skipUnless(LIVE, 'set MIOS_LIVE_NESTED=1 inside a MiOS desktop session')
class Live(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        OUT.mkdir(parents=True, exist_ok=True)
        os.chmod(OUT, 0o700)
        cls.before = primary_state()
        cls.status_before = (sha(STATUS), STATUS.stat().st_size if STATUS.exists() else -1)
        cls.ssot = nm.scratch_ssot(OUT / 'vendor-test.toml', {'desktop.nested.targets.remote': {'outputs': ['WAYLAND-2'], 'wait_ms': 8000}})
        cls.ssot_closed = nm.scratch_ssot(OUT / 'vendor-test-room-closed.toml', {'desktop.nested.targets.hypr3d': {'open_room': False}})
        cls.data = nm.tomllib.loads(cls.ssot.read_text())
        (OUT / 'probe.py').write_text(PROBE)
        # the owned nested parent: the stock PiP launch on the primary
        # a labelled seat: a private /tmp, so the room loaded into it later can never write the host's plugin files
        cls.p1 = Session(cls.ssot, ['--layout', 'pip', '--seat', 'lab'])
        time.sleep(3)

    @classmethod
    def tearDownClass(cls):
        cls.p1.stop()
        time.sleep(2)  # past the launcher's bounded runtime-removal grace
        left = [str(p) for s in Session.all for p in (s.dir, s.link) if p.exists() or p.is_symlink()]
        after = primary_state()
        (OUT / 'primary.json').write_text(json.dumps({'before': cls.before, 'after': after, 'left_behind': left}, indent=1))
        assert not left, f'runtime directories left behind: {left}'
        assert after == cls.before, f'primary changed: {cls.before} -> {after}'

    def p1_clients(self):
        return self.p1.j('clients')

    def assert_nothing_started(self, s):
        self.assertFalse(s.dir.exists(), 'a refused launch leaves no runtime directory')
        reg = self.p1.dir / 'mios/nested-sessions'
        self.assertEqual([p.name for p in reg.glob('*.json')] if reg.exists() else [], [], 'and no registry entry')
        end = time.monotonic() + 5
        while time.monotonic() < end and [c for c in self.p1_clients() if c['class'] == 'aquamarine']:
            time.sleep(0.2)
        self.assertFalse([c for c in self.p1_clients() if c['class'] == 'aquamarine'], 'and no window in the parent')

    def test_0_parent_is_an_isolated_pip(self):
        d = self.p1.d
        self.assertEqual((d['target'], d['seat'], d['devices'], d['physical_controllers_assigned']), ('pip', 'lab', [], False))
        self.assertEqual((d['tmp'], d['pad_state']), (str(self.p1.dir / 'tmp'), str(self.p1.dir / 'tmp/mios-hypr3d-pad.bin')), 'a labelled seat has a private /tmp')
        mine = [c for c in json.loads(sh(['hyprctl', '-j', 'clients']).stdout) if c['address'] == d['parent_window']]
        self.assertTrue(mine and mine[0]['pinned'], 'the default target is a pinned PiP on the primary')
        r = self.p1.run_inside(['python3', '-I', str(OUT / 'probe.py'), str(OUT / 'p1-probe.json'), '-', '/dev/input/event0'], OUT / 'p1-probe.json')
        self.assertEqual(r['listing'], [], 'no device assigned: /dev/input is empty inside the session')
        self.assertEqual(r['other'], 2, 'host nodes do not exist inside (ENOENT)')
        self.assertEqual(r['tmp'], ino(self.p1.dir / 'tmp'), "the session's /tmp is its private directory")
        self.assertNotEqual(r['tmp'], ino('/tmp'), "not the host's /tmp")
        self.assertEqual(list((self.p1.dir / 'private').iterdir()), [], 'no per-file masks are needed under a private /tmp')

    def test_1_output_target(self):
        neg = Session(self.ssot, ['--target', 'output', '--output', 'NOPE-9'], parent=self.p1, wait=False)
        rc, log = neg.finish()
        self.assertEqual(rc, 1, log)
        self.assertIn('is not attached', log)
        self.assert_nothing_started(neg)
        mon = [m for m in self.p1.j('monitors') if m['name'] == 'WAYLAND-1'][0]
        s = Session(self.ssot, ['--target', 'output', '--output', 'WAYLAND-1', '--seat', 'out1', '--user', 'Second User'], parent=self.p1)
        try:
            time.sleep(2)
            w = [c for c in self.p1_clients() if c['address'] == s.d['parent_window']][0]
            (OUT / 'output-window.json').write_text(json.dumps(w, indent=1))
            self.assertGreater(w['fullscreen'], 0, 'fullscreen on the chosen output')
            self.assertEqual(w['monitor'], mon['id'])
            self.assertFalse(w['pinned'], 'not a PiP')
            self.assertEqual((s.d['target'], s.d['output'], s.d['seat'], s.d['user'], s.d['scale_mode']), ('output', 'WAYLAND-1', 'out1', 'Second User', 'native'))
            self.assertTrue(s.d['tmux_socket'].endswith('/mios-nested-out1'), 'per-seat tmux namespace')
            m2 = s.j('monitors')[0]
            self.assertEqual(m2['scale'], 1.0, 'native: output pixels 1:1')
            lw, lh = round(mon['width'] / mon['scale']), round(mon['height'] / mon['scale'])
            self.assertLessEqual(abs(m2['width'] - lw) + abs(m2['height'] - lh), 4, f'the session covers the whole output {m2["width"]}x{m2["height"]} vs {lw}x{lh}')
            reg = json.loads((self.p1.dir / 'mios/nested-sessions/out1.json').read_text())
            self.assertEqual((reg['state'], reg['user'], reg['target'], reg['output'], reg['compositor_pid']), ('running', 'Second User', 'output', 'WAYLAND-1', s.pid))
            dup = Session(self.ssot, ['--target', 'window', '--seat', 'out1'], parent=self.p1, wait=False)
            rc, log = dup.finish()
            self.assertEqual(rc, 1)
            self.assertIn('already in use', log, 'a seat label is unique among live sessions')
            self.assertFalse(dup.dir.exists())
        finally:
            s.stop()
        self.assertFalse((self.p1.dir / 'mios/nested-sessions/out1.json').exists(), 'the registry entry leaves with the session')

    def test_2_remote_target(self):
        neg = Session(self.ssot, ['--target', 'remote'], parent=self.p1, wait=False)
        rc, log = neg.finish()
        self.assertEqual(rc, 1, log)
        self.assertIn('no remote display output is attached', log)
        self.assert_nothing_started(neg)
        # the RDP bridge's own kind of output first: a headless output in the owned parent
        self.p1.hc('output', 'create', 'headless', 'HEADLESS-MIOS-RDP')
        time.sleep(2)
        headless = [m for m in self.p1.j('monitors all') if m['name'] == 'HEADLESS-MIOS-RDP']
        evidence = {'headless': headless[0] if headless else None}
        try:
            if headless and headless[0]['width'] == 0:
                # platform limitation recorded in the handoff: a Wayland-backend nested compositor on this host cannot
                # allocate headless buffers (GBM). The launcher must skip an unusable 0x0 output (negative control).
                neg2 = Session(self.ssot, ['--target', 'remote'], parent=self.p1, wait=False)
                rc, log = neg2.finish()
                self.assertEqual(rc, 1, log)
                self.assertIn('no remote display output is attached', log, 'a 0x0 remote output is never used')
            usable_headless = headless and headless[0]['width'] > 0
            # the launch waits (targets.remote.wait_ms) for the remote display to attach, as a client connecting late
            s = Session(self.ssot, ['--target', 'remote', '--seat', 'rem1'], parent=self.p1, wait=False)
            time.sleep(1)
            self.assertIsNone(s.proc.poll(), 'the launcher waits for the remote display')
            self.assertFalse(s.link.exists(), 'and starts nothing before it attaches')
            if not usable_headless:
                # an output that cannot allocate stalls the parent's output configuration: drop it, then attach the
                # parent's second Wayland output (a window of the owned parent, never an output of the primary)
                self.p1.hc('output', 'remove', 'HEADLESS-MIOS-RDP')
                self.p1.hc('output', 'create', 'wayland', 'WAYLAND-2')
            s.wait_ready()
            try:
                time.sleep(2.5)
                want = 'HEADLESS-MIOS-RDP' if usable_headless else 'WAYLAND-2'
                self.assertEqual((s.d['target'], s.d['remote']['output'], s.d['output']), ('remote', want, want))
                mon = [m for m in self.p1.j('monitors') if m['name'] == want][0]
                w = [c for c in self.p1_clients() if c['address'] == s.d['parent_window']][0]
                self.assertEqual((w['monitor'], w['fullscreen'] > 0), (mon['id'], True))
                own = s.j('monitors')[0]
                self.assertEqual(s.d['serve'], {'mode': 'mirror', 'runtime': s.d['runtime'], 'hypr_instance': s.sig, 'wayland_display': s.wl, 'output': own['name']},
                                 'the descriptor carries what a remote server needs to share the session itself')
                self.assertEqual(own['scale'], 1.0, 'remote: native scale, the remote display pixels 1:1')
                shot = OUT / 'remote-output.png'
                sh(['grim', '-o', want, str(shot)], env=self.p1.env)
                from PIL import Image
                img = Image.open(shot).convert('RGB')
                colours = len(set(img.resize((64, 64)).tobytes()[i:i + 3] for i in range(0, 64 * 64 * 3, 3)))
                evidence.update(remote_output=mon, window=w, shot=str(shot), size=img.size, colours=colours)
                self.assertAlmostEqual(img.size[0] / img.size[1], mon['width'] / mon['height'], places=2, msg='the remote output carries frames (screencopy, as the remote servers capture)')
                self.assertGreater(colours, 8, 'the session desktop, not an empty output')
            finally:
                s.stop()
        finally:
            for name in ('WAYLAND-2', 'HEADLESS-MIOS-RDP'):
                self.p1.hc('output', 'remove', name)
            (OUT / 'remote.json').write_text(json.dumps(evidence, indent=1, default=str))

    def test_3_device_binding(self):
        a = nm.Uinput(f'MiOS Nested Live Pad A {os.getpid()}')
        b = nm.Uinput(f'MiOS Nested Live Pad B {os.getpid()}')
        try:
            s = Session(self.ssot, ['--target', 'window', '--seat', 'dev1', '--device', f'name:{a.name}'], parent=self.p1)
            try:
                self.assertTrue(s.link.is_symlink() and s.link.resolve() == s.dir, 'a session inside a session is linked from its parent runtime')
                self.assertFalse(str(s.dir).startswith(str(self.p1.dir) + '/'), 'but lives outside it')
                sock = s.dir / 'hypr' / s.sig / '.socket2.sock'
                self.assertTrue(sock.exists() and len(str(sock)) <= 107, f'its event socket exists within sun_path: {sock}')
                dev = s.d['devices']
                self.assertEqual([(d['path'], d['kind'], d['virtual']) for d in dev], [(a.node, 'controller', True)])
                self.assertTrue(s.d['physical_controllers_assigned'])
                nodes = {Path(a.node).name, *(Path(x).name for x in dev[0]['siblings'])}
                reg = json.loads((self.p1.dir / 'mios/nested-sessions/dev1.json').read_text())
                self.assertEqual([d['path'] for d in reg['devices']], [a.node], 'the registry names the session\'s nodes for the input bridge')
                # exclusivity and seat uniqueness (negative controls) while dev1 holds pad A
                for args, needle in ((['--seat', 'dev2', '--device', a.node], 'exclusive refuses'), (['--seat', 'dev1', '--device', b.node], 'already in use')):
                    neg = Session(self.ssot, ['--target', 'window', *args], parent=self.p1, wait=False)
                    rc, log = neg.finish()
                    self.assertEqual(rc, 1, log)
                    self.assertIn(needle, log)
                    self.assertFalse(neg.dir.exists())
                out = OUT / 'dev1-probe.json'
                out.unlink(missing_ok=True)
                self.assertEqual(s.ev('hl.exec_cmd(%s)' % json.dumps(' '.join(nm_shell(x) for x in ['python3', '-I', str(OUT / 'probe.py'), str(out), a.node, b.node]))), 'ok')
                end = time.monotonic() + 20
                while time.monotonic() < end and not out.is_file():
                    a.press()
                    b.press()
                    time.sleep(0.3)
                r = json.loads(out.read_text())
                (OUT / 'dev1-probe-result.json').write_text(json.dumps(r, indent=1))
                self.assertEqual(set(r['listing']), nodes, 'exactly the assigned nodes inside the session')
                self.assertIn([nm.BTN_TRIGGER, 1], r['events'], 'events from the assigned device reach the session')
                self.assertEqual(r['other'], 2, 'an unassigned device does not exist inside the session')
                self.assertEqual(r['env_seat'], 'dev1', 'the session knows its seat')
            finally:
                s.stop()
            self.assertFalse((self.p1.dir / 'mios/nested-sessions/dev1.json').exists())
            self.assertFalse(s.link.exists() or s.link.is_symlink() or s.dir.exists(), 'runtime and link leave with the session')
            # once released, the device is assignable again
            s2 = Session(self.ssot, ['--target', 'window', '--seat', 'dev2', '--device', a.node], parent=self.p1)
            s2.stop()
        finally:
            a.close()
            b.close()

    def test_4_hypr3d_room_target(self):
        neg = Session(self.ssot_closed, ['--target', 'hypr3d'], parent=self.p1, wait=False)
        rc, log = neg.finish()
        self.assertEqual(rc, 1, log)
        self.assertIn('room is not open', log)
        self.assert_nothing_started(neg)
        private = self.p1.dir / 'tmp/hypr3d-status.txt'
        size0 = private.stat().st_size if private.exists() else 0
        out = self.p1.hc('plugin', 'load', H3D_SO).stdout
        self.assertNotIn('error', out.lower(), out)
        try:
            s = Session(self.ssot, ['--target', 'hypr3d', '--seat', 'room1'], parent=self.p1)
            try:
                time.sleep(3)
                self.assertEqual(self.p1.ev("assert(hl.plugin.hypr3d.active(), 'room closed')"), 'ok', 'the room opened through the real toggle')
                lay = OUT / 'room-layout.json'
                self.assertEqual(self.p1.ev("local f=io.open(%s,'w') f:write(hl.plugin.hypr3d.export_layout()) f:close()" % json.dumps(str(lay))), 'ok')
                layout = json.loads(lay.read_text())
                mine = [e for e in layout['windows'] if e['id'] == s.d['parent_window']]
                self.assertTrue(mine, f'the session is a room window: {layout["windows"]}')
                self.assertTrue(s.d['room_window'])
                self.assertGreater(mine[0]['scale'], 0)
                w = [c for c in self.p1_clients() if c['address'] == s.d['parent_window']][0]
                self.assertFalse(w['pinned'])
                n = self.data['desktop']['nested']
                m2 = s.j('monitors')[0]
                want = fit_scale(m2['width'], m2['height'], n['width'], n['height'], n['scale_min'], n['scale_max'])
                self.assertAlmostEqual(m2['scale'], want, places=3, msg='the session keeps its logical desktop (fit) at the room window size')
                (OUT / 'room.json').write_text(json.dumps({'entity': mine[0], 'window': w, 'session_monitor': m2, 'fit': want}, indent=1))
            finally:
                s.stop()
        finally:
            self.p1.ev('if hl.plugin.hypr3d and hl.plugin.hypr3d.active() then hl.plugin.hypr3d.close() end')
            time.sleep(1.5)
            self.p1.hc('plugin', 'unload', H3D_SO)
        self.assertGreater(private.stat().st_size, size0, 'the room in the session wrote its private status file')
        r = self.p1.run_inside(['python3', '-I', str(OUT / 'probe.py'), str(OUT / 'p1-probe-room.json'), '-', '/dev/input/event0'], OUT / 'p1-probe-room.json')
        self.assertEqual(r['status'], ino(private), "the room's /tmp/hypr3d-status.txt is the session's private file")
        if STATUS.exists():
            self.assertNotEqual(r['status'], ino(STATUS), "never the host's (other rooms on the host may write that one)")

    def test_5_unlabelled_session_keeps_the_shared_tmp_with_masks(self):
        s = Session(self.ssot, ['--target', 'window'], parent=self.p1)
        try:
            self.assertEqual((s.d['seat'], s.d['tmp'], s.d['pad_state']), ('', 'shared', None))
            r = s.run_inside(['python3', '-I', str(OUT / 'probe.py'), str(OUT / 'shared-probe.json'), '-', '/dev/input/event0'], OUT / 'shared-probe.json')
            self.assertEqual(r['tmp'], ino('/tmp'), "the unlabelled development session shares the host's /tmp")
            masks = sorted((s.dir / 'private').iterdir())
            self.assertEqual(len(masks), len(self.data['desktop']['nested']['private_files']), f'one private copy per plugin state file: {masks}')
            status = [m for m in masks if m.name.endswith('hypr3d-status.txt')][0]
            self.assertEqual(r['status'], ino(status), 'its plugin status file is masked by its private copy')
        finally:
            s.stop()


if __name__ == '__main__':
    if not LIVE:
        print('skip: live nested-session test (set MIOS_LIVE_NESTED=1 inside a MiOS desktop session)')
    unittest.main(verbosity=2)
