#!/usr/bin/env python3
# AI-hint: Regression test for the operator's flatpak document portal dying (exit 21) during agent live tests: a second xdg-document-portal started with the operator's XDG_RUNTIME_DIR lazily unmounts $XDG_RUNTIME_DIR/doc at startup (its stale-mount cleanup) and kills the primary instance. Proves the mechanism in scratch runtime dirs on private buses, guards every test/tool that starts an activating private bus to pass its own environment, and checks the restart drop-in.
# AI-related: usr/lib/systemd/user/xdg-document-portal.service.d/10-mios-restart.conf, tests/test-shell-appmenu-live.py, tests/test-quickshell-candidate-live.py
# Never touches the operator's runtime dir, session bus or portal: every portal here runs on its own private bus with
# a scratch XDG_RUNTIME_DIR, and every mount it makes is lazily unmounted in tearDown.
# MIOS_PORTAL_PRE_FIX=<rev> names the commit whose tests/test-shell-appmenu-live.py is the static guard's negative control.
import configparser, os, re, shutil, subprocess, tempfile, time, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORTAL = '/usr/libexec/xdg-document-portal'
DROPIN = ROOT / 'usr/lib/systemd/user/xdg-document-portal.service.d/10-mios-restart.conf'
PRE_FIX = os.environ.get('MIOS_PORTAL_PRE_FIX', '62614f8c1')  # app-menu test still starts its bus with the host env
SCAN = ['tests', 'tools', 'usr/libexec/mios', 'src/mios-rs']


def mounted(path):
    with open('/proc/self/mountinfo') as f:
        return any(line.split()[4] == path for line in f)


class Mechanism(unittest.TestCase):
    """Two portals on two private buses; the first one's fate depends only on the second one's XDG_RUNTIME_DIR."""

    def setUp(self):
        for tool in (PORTAL, shutil.which('dbus-daemon'), shutil.which('fusermount3')):
            if not tool or not os.access(tool, os.X_OK):
                self.skipTest('needs xdg-document-portal, dbus-daemon and fusermount3')
        # Bus sockets need a short path (sun_path is 108 bytes).
        self.base = tempfile.mkdtemp(prefix='mios-pt-', dir=f'/tmp/claude-{os.getuid()}' if os.path.isdir(f'/tmp/claude-{os.getuid()}') else None)
        self.procs, self.buses, self.mounts = [], [], set()

    def tearDown(self):
        for p in reversed(self.procs):
            if p.poll() is None:
                p.terminate()
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                p.kill()
        for pid in self.buses:
            try:
                os.kill(pid, 15)
            except ProcessLookupError:
                pass
        time.sleep(0.3)
        for m in self.mounts:
            if mounted(m):
                subprocess.run(['fusermount3', '-u', '-z', m], capture_output=True)
        for m in self.mounts:
            self.assertFalse(mounted(m), f'{m} left mounted')
        shutil.rmtree(self.base, ignore_errors=True)

    def runtime(self, name):
        p = os.path.join(self.base, name)
        os.makedirs(p, mode=0o700)
        self.mounts.add(p + '/doc')
        return p

    def portal_on_private_bus(self, runtime, tag):
        home = os.path.join(self.base, 'home-' + tag)
        os.makedirs(home)
        env = {k: v for k, v in os.environ.items() if k not in ('DBUS_SESSION_BUS_ADDRESS', 'DISPLAY', 'WAYLAND_DISPLAY')}
        env.update(XDG_RUNTIME_DIR=runtime, HOME=home, XDG_DATA_HOME=os.path.join(home, 'data'))
        r = subprocess.run(['dbus-daemon', '--session', '--fork', '--print-address=1', '--print-pid=1',
                            f'--address=unix:path={self.base}/bus-{tag}'], capture_output=True, text=True, env=env, timeout=10)
        self.assertEqual(r.returncode, 0, r.stderr)
        addr, pid = r.stdout.split()[:2]
        self.buses.append(int(pid))
        env['DBUS_SESSION_BUS_ADDRESS'] = addr
        p = subprocess.Popen([PORTAL], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.procs.append(p)
        return p

    def first_portal(self, runtime):
        a = self.portal_on_private_bus(runtime, 'a')
        for _ in range(50):
            if mounted(runtime + '/doc'):
                break
            time.sleep(0.1)
        self.assertTrue(mounted(runtime + '/doc'), 'first portal never mounted its doc directory')
        return a

    def test_second_portal_on_the_same_runtime_dir_kills_the_first(self):
        rt = self.runtime('shared')
        a = self.first_portal(rt)
        self.portal_on_private_bus(rt, 'b')
        for _ in range(30):
            if a.poll() is not None:
                break
            time.sleep(0.1)
        self.assertEqual(a.poll(), 21, 'the primary portal must exit 21 ("Fuse filesystem unmounted")')

    def test_second_portal_on_its_own_runtime_dir_leaves_the_first_alone(self):
        rt = self.runtime('mine')
        a = self.first_portal(rt)
        self.portal_on_private_bus(self.runtime('theirs'), 'b')
        time.sleep(2)
        self.assertIsNone(a.poll(), 'a portal with its own runtime dir must not touch another doc mount')
        self.assertTrue(mounted(rt + '/doc'))


# A bus that activates the standard session services: dbus-daemon --session (session.conf) or dbus-run-session.
ACTIVATING = re.compile(r"""(['"])dbus-daemon\1\s*,\s*(['"])--session\2|(['"])dbus-run-session\3""")


def unsafe_bus_launches(text):
    """Call statements that start an activating private bus without passing an explicit env=."""
    lines, bad = text.splitlines(), []
    for i, line in enumerate(lines):
        if not ACTIVATING.search(line):
            continue
        stmt, depth = '', 0
        for j in range(i, min(i + 8, len(lines))):
            stmt += lines[j] + '\n'
            depth += lines[j].count('(') + lines[j].count('[') - lines[j].count(')') - lines[j].count(']')
            if depth <= 0:
                break
        if not re.search(r'\benv\s*=', stmt):
            bad.append((i + 1, line.strip()))
    return bad


class BusLaunchGuard(unittest.TestCase):
    """Every activating private bus a test or tool starts carries an explicit environment (its own runtime dir)."""

    def test_tree_has_no_activating_bus_on_the_inherited_environment(self):
        findings = []
        for top in SCAN:
            for p in sorted((ROOT / top).rglob('*.py')) if (ROOT / top).is_dir() else []:
                if p.name == Path(__file__).name:
                    continue
                try:
                    text = p.read_text(errors='replace')
                except OSError:
                    continue
                findings += [f'{p.relative_to(ROOT)}:{n}: {s}' for n, s in unsafe_bus_launches(text)]
        self.assertEqual(findings, [], 'activating private buses started with the inherited (operator) environment')

    def test_guard_flags_the_pre_fix_app_menu_test(self):
        r = subprocess.run(['git', '-C', str(ROOT), 'show', f'{PRE_FIX}:tests/test-shell-appmenu-live.py'],
                           capture_output=True, text=True)
        if r.returncode != 0:
            self.skipTest(f'{PRE_FIX} not reachable from this checkout')
        self.assertTrue(unsafe_bus_launches(r.stdout), 'negative control: the pre-fix bus launch must be flagged')

    def test_guard_accepts_an_explicit_env_across_lines(self):
        ok = "p = subprocess.Popen(['dbus-run-session', '--', 'bwrap',\n    '--bind', '/', '/'],\n    env=renv)\n"
        bad = "r = sh(['dbus-daemon', '--session', '--fork',\n        '--address=unix:path=x'])\n"
        self.assertEqual(unsafe_bus_launches(ok), [])
        self.assertEqual(len(unsafe_bus_launches(bad)), 1)


class RestartDropin(unittest.TestCase):
    def test_dropin_restarts_on_failure_only(self):
        c = configparser.ConfigParser(strict=True, interpolation=None)
        c.optionxform = str
        c.read_string(DROPIN.read_text())
        self.assertEqual(c.sections(), ['Service'])
        self.assertEqual(c['Service'].get('Restart'), 'on-failure')
        self.assertGreaterEqual(float(c['Service'].get('RestartSec', '0')), 1)

    def test_installed_manager_uses_it(self):
        if os.environ.get('MIOS_LIVE') != '1':
            self.skipTest('set MIOS_LIVE=1 to read the running user manager (read-only)')
        installed = Path('/usr/lib/systemd/user/xdg-document-portal.service.d/10-mios-restart.conf')
        self.assertTrue(installed.is_file() and installed.read_bytes() == DROPIN.read_bytes(), 'drop-in not installed')
        r = subprocess.run(['systemctl', '--user', 'show', 'xdg-document-portal.service', '-p', 'Restart', '-p', 'DropInPaths'],
                           capture_output=True, text=True)
        self.assertIn('Restart=on-failure', r.stdout)
        self.assertIn(str(installed), r.stdout)


if __name__ == '__main__':
    unittest.main(verbosity=2)
