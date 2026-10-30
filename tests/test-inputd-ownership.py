#!/usr/bin/env python3
# AI-hint: Real-binary two-sided tests for mios-inputd step I1: capture/release identical for every window kind, half-capture keeps every button with the app, release/fullscreen tags (Lua, run against a mock compositor), serve exits/idles instead of exec'ing a recovery owner, unit start limit.
# AI-related: tools/native/mios-inputd, docs/design/mios-global-bindings-tests.md (I1), usr/lib/systemd/user/mios-inputd.service
# AI-functions: Ownership, Serve
# Environment: MIOS_INPUTD_BIN = the binary under test (default: installed). MIOS_INPUTD_OLD_BIN + MIOS_INPUTD_OLD_SSOT =
# the pre-I1 binary and SSOT for the negative controls (skipped when absent). No physical device is ever opened:
# serve runs inside bubblewrap with an empty /dev/input, a private /tmp and no compositor signature.
import fcntl
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
BIN = Path(os.environ.get('MIOS_INPUTD_BIN', '/usr/libexec/mios/mios-inputd'))
OLD = Path(os.environ.get('MIOS_INPUTD_OLD_BIN', '/nonexistent'))
OLD_SSOT = Path(os.environ.get('MIOS_INPUTD_OLD_SSOT', '/nonexistent'))
LUA = Path('/usr/bin/lua')
NORTH, RB, LB, SELECT, START, GUIDE, EAST, L3, R3 = 0x133, 0x137, 0x136, 0x13a, 0x13b, 0x13c, 0x131, 0x13d, 0x13e


def key(code, value=1):
    return dict(kind=1, code=code, value=value)


def tap(code):
    return [key(code), key(code, 0)]


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not BIN.is_file():
            raise AssertionError(f'binary under test missing: {BIN}')
        cls.temp = tempfile.TemporaryDirectory(prefix='mios-inputd-i1-')
        cls.root = Path(cls.temp.name) / 'root'
        (cls.root / 'usr/share/mios').mkdir(parents=True)
        shutil.copy(ROOT / 'usr/share/mios/mios.toml', cls.root / 'usr/share/mios/mios.toml')
        cls.old_root = None
        if OLD.is_file() and OLD_SSOT.is_file():
            cls.old_root = Path(cls.temp.name) / 'old-root'
            (cls.old_root / 'usr/share/mios').mkdir(parents=True)
            shutil.copy(OLD_SSOT, cls.old_root / 'usr/share/mios/mios.toml')
        cls.env = {k: v for k, v in os.environ.items() if not (k.startswith('MIOS_') and ('TOML' in k or k == 'MIOS_TOML_ROOT'))}
        cls.env.pop('HYPRLAND_INSTANCE_SIGNATURE', None)
        cls.env['HOME'] = str(Path(cls.temp.name) / 'home')
        cls.env['XDG_CONFIG_HOME'] = str(Path(cls.temp.name) / 'home/.config')

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def run_bin(self, *args, binary=None, root=None, data=None, timeout=10):
        return subprocess.run([str(binary or BIN), '--root', str(root or self.root), *args], input=data, text=True,
                              capture_output=True, env=self.env, timeout=timeout)

    def replay(self, context, events, binary=None, root=None):
        r = self.run_bin('replay', binary=binary, root=root, data=json.dumps(dict(context=context, events=events)))
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        return out['output']['actions'], [(e['code'], e['value']) for e in out['output']['app'] if e['kind'] == 1]

    def need_old(self):
        if self.old_root is None:
            self.skipTest('negative control needs MIOS_INPUTD_OLD_BIN and MIOS_INPUTD_OLD_SSOT (the pre-I1 pair)')

    def host_layer(self, text, root=None):
        path = (root or self.root) / 'etc/mios/mios.toml'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        self.addCleanup(path.unlink, missing_ok=True)


class Ownership(Base):
    HALF = [key(GUIDE), key(SELECT), key(SELECT, 0), key(GUIDE, 0)]

    def test_c01_half_capture_keeps_rb_guide_and_north_with_the_app(self):
        actions, app = self.replay('app-capture', self.HALF + tap(RB))
        self.assertEqual(actions, ['input:cursor on'])
        self.assertEqual(app, [(RB, 1), (RB, 0)])
        actions, app = self.replay('app-capture', self.HALF + [key(GUIDE), key(EAST), key(EAST, 0), key(GUIDE, 0)] + tap(NORTH) + tap(START))
        self.assertEqual(actions, ['input:cursor on', 'input:cursor off'], 'Guide+East is app input; Start returns to full capture')
        self.assertEqual(app, [(GUIDE, 1), (EAST, 1), (EAST, 0), (GUIDE, 0), (NORTH, 1), (NORTH, 0)])

    def test_c01_negative_old_binary_fires_shaderglass_in_half_capture(self):
        self.need_old()
        actions, app = self.replay('app-capture', self.HALF + tap(RB), binary=OLD, root=self.old_root)
        self.assertIn('mios:shaderglass toggle', actions, 'the pre-I1 binary must reproduce the live RB bug')
        self.assertNotIn((RB, 1), app)

    def test_c01_uncaptured_keeps_host_actions(self):
        for context in ('desktop-2d', 'terminal', 'game', 'vm-passthrough'):
            with self.subTest(context=context):
                actions, _ = self.replay(context, self.HALF + tap(RB))
                self.assertEqual(actions, ['input:cursor on', 'mios:shaderglass toggle'])

    def test_capture_and_release_are_identical_for_every_window_kind(self):
        transcripts = {}
        for context in ('desktop-2d', 'terminal', 'game', 'vm-passthrough'):
            transcripts[context] = self.replay(context, tap(NORTH) + tap(RB))
        for context, t in transcripts.items():
            self.assertEqual(t, (['input:window on'], [(RB, 1), (RB, 0)]), f'{context}: North captures, then the pending capture forwards RB to the app')
        self.assertEqual(self.replay('app-capture', tap(NORTH) + tap(RB)), ([], [(NORTH, 1), (NORTH, 0), (RB, 1), (RB, 0)]))

    def test_capture_negative_old_binary_decided_capture_by_class(self):
        self.need_old()
        game = self.replay('game', tap(NORTH) + tap(RB), binary=OLD, root=self.old_root)
        self.assertEqual(game, ([], [(NORTH, 1), (NORTH, 0), (RB, 1), (RB, 0)]), 'pre-I1: the game class owned the pad without a capture')
        self.assertNotEqual(game, self.replay('game', tap(NORTH) + tap(RB)))

    def test_retired_recovery_is_rejected_and_old_binary_execs_it(self):
        self.host_layer('[keybindings.global.controller]\nrecovery_argv = ["/usr/bin/echo", "RECOVERY-EXEC"]\n')
        r = self.run_bin('check')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('recovery_argv is retired', r.stderr)
        if self.old_root is None:
            return
        self.host_layer('[keybindings.global.controller]\nenabled = false\nrecovery_argv = ["/usr/bin/echo", "RECOVERY-EXEC"]\n', root=self.old_root)
        r = self.run_bin('serve', binary=OLD, root=self.old_root)
        self.assertIn('RECOVERY-EXEC', r.stdout, 'negative control: the pre-I1 serve execs a second pad owner when disabled')

    def test_tags_must_be_distinct_and_safe(self):
        # (An empty string never overrides a lower tier, so "" cannot be planted from a host layer.)
        for text in ('release_tag = "mios-input-captured"', 'capture_fullscreen_tag = "bad tag"', 'release_tag = "x;y"'):
            with self.subTest(text=text):
                self.host_layer(f'[keybindings.global.controller]\n{text}\n')
                self.assertNotEqual(self.run_bin('check').returncode, 0)

    # ---- the ownership Lua, evaluated by a real Lua interpreter against a mock compositor ----
    MOCK = r'''
D={}
local W=@@WINDOW@@
hl={get_active_window=function() return W end,get_windows=function() return {W} end,
 dispatch=function(x) table.insert(D,x) end,
 dsp={window={tag=function(t) return {kind='tag',v=t.tag} end,fullscreen=function(t) return {kind='fullscreen',v=t.action} end}}}
MIOS_INPUT_POLICY=@@POLICY@@
local ok,msg=pcall(function()
@@BODY@@
end)
print('RESULT '..tostring(msg))
for _,d in ipairs(D) do print('DISPATCH '..d.kind..' '..tostring(d.v)) end
'''

    def lua(self, body, window, policy='nil'):
        if not LUA.is_file():
            self.fail('native Lua interpreter required for ownership-transaction controls')
        script = self.MOCK.replace('@@WINDOW@@', window).replace('@@POLICY@@', policy).replace('@@BODY@@', body)
        r = subprocess.run([str(LUA), '-'], input=script, text=True, capture_output=True, timeout=5)
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.splitlines()
        return lines[0], [l[9:] for l in lines[1:]]

    def window_lua(self, mode, binary=None, root=None):
        r = self.run_bin('resolve-action', f'input:window {mode}', binary=binary, root=root)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)['argv'][2]

    def context_lua(self):
        r = self.run_bin('context', '--lua')
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)['lua']

    W_BRIDGE_FS = "{address='0xabc',class='steam_app_570',fullscreen=2,tags={'mios-input-captured','mios-input-fullscreen'}}"
    W_USER_FS = "{address='0xabc',class='org.any.Flatpak',fullscreen=2,tags={'mios-input-captured'}}"
    W_WINDOWED = "{address='0xabc',class='looking-glass-client',fullscreen=0,tags={'mios-input-released'}}"

    def test_c02_release_tags_and_unsets_only_a_bridge_fullscreen(self):
        result, d = self.lua(self.window_lua('off'), self.W_BRIDGE_FS)
        self.assertEqual(result, 'RESULT MIOS_INPUT_WINDOW_OK:0xabc')
        self.assertEqual(d, ['tag -mios-input-captured', 'tag +mios-input-released', 'tag -mios-input-fullscreen', 'fullscreen unset'])
        result, d = self.lua(self.window_lua('off'), self.W_USER_FS)
        self.assertEqual(d, ['tag -mios-input-captured', 'tag +mios-input-released'], 'a Super+F11 fullscreen is never undone by a release')

    def test_c02_capture_clears_release_and_marks_only_its_own_fullscreen(self):
        _, d = self.lua(self.window_lua('fullscreen'), self.W_WINDOWED)
        self.assertEqual(d, ['tag -mios-input-released', 'tag +mios-input-captured', 'tag +mios-input-fullscreen', 'fullscreen set'])
        _, d = self.lua(self.window_lua('fullscreen'), self.W_USER_FS)
        self.assertEqual(d, [], 'already fullscreen and captured: no tag the release would later misread as ours')
        _, d = self.lua(self.window_lua('on'), self.W_WINDOWED)
        self.assertEqual(d, ['tag -mios-input-released', 'tag +mios-input-captured'])

    def test_c02_negative_old_release_left_the_window_fullscreen_and_captured(self):
        self.need_old()
        _, d = self.lua(self.window_lua('off', binary=OLD, root=self.old_root), self.W_BRIDGE_FS)
        self.assertNotIn('fullscreen unset', d)
        self.assertNotIn('tag +mios-input-released', d)

    def test_context_predicate_honours_release_for_every_window_kind(self):
        body = self.context_lua()
        for cls in ('steam_app_570', 'looking-glass-client', 'org.any.Flatpak', 'Alacritty'):
            with self.subTest(cls=cls):
                fs = f"{{address='0xabc',class='{cls}',pid=4,fullscreen=2,tags={{}}}}"
                rel = f"{{address='0xabc',class='{cls}',pid=4,fullscreen=2,tags={{'mios-input-released'}}}}"
                one = json.loads(self.lua(body, fs)[0].split('MIOS_INPUT_CONTEXT:', 1)[1])
                two = json.loads(self.lua(body, rel)[0].split('MIOS_INPUT_CONTEXT:', 1)[1])
                self.assertTrue(one['captured'] and not one['released'])
                self.assertFalse(two['captured'], 'release_tag cancels fullscreen==2')
                self.assertTrue(two['released'])
        # The generated predicate (G1) wins when the session exposes it.
        out = json.loads(self.lua(body, "{address='0xabc',class='x',pid=4,fullscreen=0,tags={}}",
                                  policy='{captured=function(w) return true end}')[0].split('MIOS_INPUT_CONTEXT:', 1)[1])
        self.assertTrue(out['captured'])

    def test_c02_transaction_refreshes_the_keyboard_policy(self):
        # Host Super chords follow the new ownership at once (tag-only capture/release fires no fullscreen event).
        refresh = "{refresh=function() table.insert(D,{kind='refresh',v='keyboard'}) end}"
        for mode, window in (('on', self.W_WINDOWED), ('off', self.W_USER_FS)):
            with self.subTest(mode=mode):
                result, d = self.lua(self.window_lua(mode), window, policy=refresh)
                self.assertTrue(result.startswith('RESULT MIOS_INPUT_WINDOW_OK:'), result)
                self.assertEqual(d[-1], 'refresh keyboard', d)
        lua = self.window_lua('on').replace('local EXPECT=""', 'local EXPECT="0xdef"')
        self.assertNotIn('refresh keyboard', self.lua(lua, self.W_WINDOWED, policy=refresh)[1], 'a refused transaction changes nothing')

    def test_c02_focus_race_changes_nothing(self):
        lua = self.window_lua('on').replace('local EXPECT=""', 'local EXPECT="0xdef"')
        result, d = self.lua(lua, self.W_WINDOWED)
        self.assertIn('MIOS_INPUT_WINDOW_FAIL', result)
        self.assertEqual(d, [])


class Serve(Base):
    def sandbox(self, *, uinput=True, lock_held=False):
        """bubblewrap: read-only host, private /tmp and runtime dir, an EMPTY /dev/input, optional /dev/uinput."""
        if not shutil.which('bwrap'):
            self.fail('bubblewrap required to run serve without any physical device')
        rt = Path(self.temp.name) / f'rt-{time.monotonic_ns()}'
        rt.mkdir(mode=0o700)
        self.host_layer(f'[keybindings.global.controller]\nlegacy_pad_state_path = "{rt}/pad.bin"\n')
        if lock_held:
            lock = open(rt / 'mios-inputd.lock', 'w')
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.addCleanup(lock.close)
        argv = ['bwrap', '--ro-bind', '/', '/', '--dev', '/dev', '--dir', '/dev/input', '--tmpfs', '/tmp', '--proc', '/proc',
                '--bind', self.temp.name, self.temp.name, '--die-with-parent']
        if uinput:
            argv[argv.index('--dir'):argv.index('--dir')] = ['--dev-bind', '/dev/uinput', '/dev/uinput']
        env = dict(self.env, XDG_RUNTIME_DIR=str(rt))
        return argv + [str(BIN), '--root', str(self.root), 'serve'], env

    def start(self, argv, env):
        return subprocess.Popen(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    @staticmethod
    def term(p):
        # bubblewrap does not forward signals: SIGTERM the serve process it runs (its exit status is bwrap's).
        children = subprocess.run(['pgrep', '-P', str(p.pid)], capture_output=True, text=True).stdout.split()
        for pid in children or [str(p.pid)]:
            os.kill(int(pid), signal.SIGTERM)

    def test_disabled_and_ungrabbed_exit_zero(self):
        for text in ('enabled = false', 'exclusive_grab = false'):
            with self.subTest(text=text):
                self.host_layer(f'[keybindings.global.controller]\n{text}\n')
                r = self.run_bin('serve', timeout=5)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn('"serve":"disabled"', r.stderr)

    def test_no_device_access_idles_then_sigterm_exits_zero(self):
        argv, env = self.sandbox(uinput=False)
        p = self.start(argv, env)
        try:
            time.sleep(2.5)
            self.assertIsNone(p.poll(), 'serve must idle, not exit into a restart loop')
            self.term(p)
            out, err = p.communicate(timeout=5)
        finally:
            if p.poll() is None:
                p.kill()
        self.assertEqual(p.returncode, 0, err)
        self.assertIn('"serve":"idle"', err)
        self.assertIn('/dev/uinput', err)
        self.assertNotIn('"mode":"bridge"', err)

    def test_bridge_error_exits_nonzero(self):
        argv, env = self.sandbox(uinput=True, lock_held=True)
        p = self.start(argv, env)
        try:
            out, err = p.communicate(timeout=10)
        finally:
            if p.poll() is None:
                p.kill()
        if '"serve":"idle"' in err:
            self.skipTest(f'this login has no seat; the error path needs one: {err.strip()[:200]}')
        self.assertEqual(p.returncode, 1, err)
        self.assertIn('already owns', err)

    def test_seat_and_access_reach_the_bridge_without_devices(self):
        argv, env = self.sandbox(uinput=True)
        p = self.start(argv, env)
        try:
            time.sleep(2.5)
            alive = p.poll() is None
            self.term(p)
            out, err = p.communicate(timeout=5)
        finally:
            if p.poll() is None:
                p.kill()
        if '"serve":"idle"' in err:
            self.skipTest('this login has no seat')
        self.assertTrue(alive, err)
        self.assertEqual(p.returncode, 0, err)
        self.assertNotIn('"mode":"bridge"', err, 'the empty /dev/input must not yield a device')

    def test_unit_has_a_start_limit_and_no_recovery(self):
        unit = (ROOT / 'usr/lib/systemd/user/mios-inputd.service').read_text()
        self.assertIn('StartLimitIntervalSec=60', unit)
        self.assertIn('StartLimitBurst=5', unit)
        self.assertIn('Restart=on-failure', unit)
        self.assertNotIn('recovery', unit.lower())
        ssot = (ROOT / 'usr/share/mios/mios.toml').read_text()
        self.assertNotIn('recovery_argv', ssot)


@unittest.skipUnless(os.environ.get('MIOS_INPUTD_UNIT_TEST') == '1', 'transient user-unit start-limit test (set MIOS_INPUTD_UNIT_TEST=1)')
class StartLimit(Base):
    """C-05 with systemd itself: a private transient user unit (never mios-inputd.service) runs serve in the bubblewrap
    sandbox (empty /dev/input) with the shipped unit's restart and start-limit properties."""
    sandbox = Serve.sandbox

    def unit_props(self, limits=True):
        unit = (ROOT / 'usr/lib/systemd/user/mios-inputd.service').read_text().splitlines()
        keep = ('Restart=', 'RestartSec=') + (('StartLimitIntervalSec=', 'StartLimitBurst=') if limits else ())
        return [p for line in unit if line.startswith(keep) for p in ('-p', line)]

    def transient(self, name, argv, env, limits=True):
        unit = f'mios-inputd-i1-test-{name}-{os.getpid()}.service'
        self.addCleanup(lambda: subprocess.run(['systemctl', '--user', 'stop', unit], capture_output=True))
        self.addCleanup(lambda: subprocess.run(['systemctl', '--user', 'reset-failed', unit], capture_output=True))
        envs = [f'--setenv={k}={env[k]}' for k in ('XDG_RUNTIME_DIR', 'HOME', 'XDG_CONFIG_HOME')]
        r = subprocess.run(['systemd-run', '--user', '--quiet', f'--unit={unit}', *self.unit_props(limits), *envs, *argv],
                           capture_output=True, text=True, timeout=10)
        self.assertEqual(r.returncode, 0, r.stderr)
        return unit

    @staticmethod
    def show(unit):
        out = subprocess.run(['systemctl', '--user', 'show', unit, '-p', 'ActiveState', '-p', 'Result', '-p', 'NRestarts'],
                             capture_output=True, text=True).stdout
        return dict(l.split('=', 1) for l in out.splitlines() if '=' in l)

    def test_bridge_error_loop_stops_at_the_start_limit(self):
        argv, env = self.sandbox(uinput=True, lock_held=True)
        unit = self.transient('limit', argv, env)
        end = time.monotonic() + 40
        while time.monotonic() < end and self.show(unit).get('ActiveState') != 'failed':
            time.sleep(0.5)
        st = self.show(unit)
        if st.get('Result') == 'success':
            self.skipTest('this login has no seat; serve idled instead of erroring')
        # systemd 259 keeps Result=exit-code; the refused restart is the start limit (journal: "repeated too quickly").
        self.assertEqual(st.get('ActiveState'), 'failed', st)
        self.assertIn(st.get('Result'), ('exit-code', 'start-limit-hit'), st)
        journal = subprocess.run(['journalctl', '--user', '-u', unit, '--no-pager', '-o', 'cat'], capture_output=True, text=True).stdout
        self.assertIn('already owns', journal, 'each start failed on the bridge error itself')
        self.assertIn(int(st.get('NRestarts', '0')), (4, 5), f'StartLimitBurst=5 starts, then the unit stays failed: {st}')
        time.sleep(4)
        self.assertEqual(self.show(unit), st, 'no further restart after the start limit')

    def test_negative_default_limits_keep_restart_looping(self):
        # Without the explicit limit, RestartSec=3 outpaces systemd's default 5-in-10s window: the loop never stops.
        argv, env = self.sandbox(uinput=True, lock_held=True)
        unit = self.transient('nolimit', argv, env, limits=False)
        time.sleep(21)
        st = self.show(unit)
        if st.get('Result') == 'success':
            self.skipTest('this login has no seat')
        self.assertGreaterEqual(int(st.get('NRestarts', '0')), 5, st)
        self.assertNotEqual(st.get('ActiveState'), 'failed', f'without the explicit limit systemd keeps restarting: {st}')

    def test_disabled_exits_zero_without_restart(self):
        argv, env = self.sandbox(uinput=True)
        self.host_layer(f'[keybindings.global.controller]\nenabled = false\nlegacy_pad_state_path = "{env["XDG_RUNTIME_DIR"]}/pad.bin"\n')
        unit = self.transient('disabled', argv, env)
        end = time.monotonic() + 10
        while time.monotonic() < end and self.show(unit).get('ActiveState') not in ('inactive', 'failed'):
            time.sleep(0.2)
        time.sleep(4)
        st = self.show(unit)
        self.assertEqual(st.get('ActiveState'), 'inactive', st)
        self.assertEqual(st.get('NRestarts'), '0', st)


if __name__ == '__main__':
    unittest.main()
