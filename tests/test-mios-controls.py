#!/usr/bin/env python3
# AI-hint: Real-binary positive/negative verification of mios-controls: draft validation (gate collision analysis over the re-rendered compositor session), user-tier-only atomic apply, restore-defaults, import/export confinement, credential non-disclosure, and the TUI in a private tmux server.
# AI-related: tools/native/mios-controls/src/main.rs, src/mios-rs/mios-gate/src/keybinds.rs, usr/share/mios/mios.toml [keybindings.global]
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import time
import tomllib
import unittest

ROOT = Path(os.environ.get('MIOS_TEST_ROOT', '/'))
SECRET = 'mios-controls-test-secret-value'


def binary():
    explicit = os.environ.get('MIOS_CONTROLS')
    here = Path(__file__).resolve().parents[1]
    candidates = [Path(explicit)] if explicit else [
        Path('/var/lib/mios-build/tmp/mios-controls-app/target/x86_64-unknown-linux-musl/release/mios-controls'),
        here / 'tools/native/target/x86_64-unknown-linux-musl/release/mios-controls',
        Path('/usr/bin/mios-controls'),
    ]
    for p in candidates:
        if p.is_file() and os.access(p, os.X_OK):
            return p
    raise AssertionError('mios-controls binary not found; set MIOS_CONTROLS')


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='mios-controls-test-')
        self.user = Path(self.tmp.name) / 'cfg/mios/mios.toml'
        self.env = dict(os.environ, MIOS_USER_TOML=str(self.user), MIOS_USER_TOML_D=str(Path(self.tmp.name) / 'none.d'))
        self.bin = binary()

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args):
        return subprocess.run([str(self.bin), '--root', str(ROOT), *args], capture_output=True, text=True, timeout=120, env=self.env)

    def write_user(self, text):
        self.user.parent.mkdir(parents=True, exist_ok=True)
        self.user.write_text(text)
        os.chmod(self.user, 0o600)


class Validation(Base):
    def test_clean_map_checks_clean(self):
        r = self.run_cli('check')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_free_chord_validates_and_dry_run_writes_nothing(self):
        r = self.run_cli('set', 'terminal', '--chord', 'SUPER+F8')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('dry run', r.stdout)
        self.assertFalse(self.user.exists(), 'a dry run must not create the user tier')

    def test_colliding_chord_is_rejected_by_table_and_rendered_session(self):
        r = self.run_cli('set', 'terminal', '--chord', 'SUPER+c', '--apply')
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn('NEW FINDING: collision chord SUPER+c', r.stdout)
        self.assertIn('[hyprland.lua]', r.stdout, 'the re-rendered compositor session must see the moved bind')
        self.assertFalse(self.user.exists(), 'a rejected draft is never written, even with --apply')

    def test_realized_row_on_a_foreign_compositor_chord_is_rejected(self):
        # D1 (generators verify): a realized_by row moved onto SUPER+J (the compositor's togglesplit) used to pass
        # with 0 findings and a dead row. Now render-hyprland refuses it, so the draft is rejected and never written.
        # The pre-fix generator (MIOS_GEN_OLD) is the negative control: it accepts the same draft.
        for row in ('session-scale-up', 'screenshot-region'):
            r = self.run_cli('set', row, '--chord', 'SUPER+J', '--passthrough', 'vm-passthrough', '--apply')
            self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn(f'{row}: chord SUPER+j is already bound by the compositor', r.stdout + r.stderr)
            self.assertFalse(self.user.exists(), 'a rejected draft is never written, even with --apply')
        old_gen, old_bin = os.environ.get('MIOS_GEN_OLD'), os.environ.get('MIOS_CONTROLS_OLD')
        draft = ['set', 'session-scale-up', '--chord', 'SUPER+J', '--passthrough', 'vm-passthrough']
        if old_gen:
            # Second line alone: the gate analysis (compiled into this editor) flags the row even when the generator
            # still drops it silently.
            r = subprocess.run([str(self.bin), '--root', str(ROOT), *draft], capture_output=True, text=True, timeout=120, env=dict(self.env, MIOS_GEN=old_gen))
            self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn('session-scale-up: realized_by hyprland on SUPER+j but that chord is desktop.hyprland.bindings:Toggle split direction', r.stdout)
        if old_gen and old_bin:
            r = subprocess.run([old_bin, '--root', str(ROOT), *draft], capture_output=True, text=True, timeout=120, env=dict(self.env, MIOS_GEN=old_gen))
            self.assertEqual(r.returncode, 0, 'negative control: the pre-fix editor + generator accept the dead row\n' + r.stdout + r.stderr)
            self.assertIn('0 finding(s)', r.stdout)

    def test_exec_row_on_an_unrelated_literal_chord_is_rejected(self):
        # V1 (generators verify, round 2): an exec:<prog> row on a chord whose literal merely MENTIONS the program
        # (CTRL+ALT+SHIFT+m runs `alacritty -e ... mios-terminal --action system`) was taken as realized: no dispatch
        # handle, 0 findings, and --apply wrote the dead row. The literals come from [keybindings.actions] and the
        # terminal/launcher keys, not the compositor tables. Positive control: the same row on a free chord.
        probes = [('exec:alacritty', 'CTRL+ALT+SHIFT+m'), ('exec:mios-terminal', 'CTRL+ALT+SHIFT+a'),
                  ('exec:quickshell', 'SUPER+R'), ('exec:env', 'SUPER+Q')]
        for action, chord in probes:
            r = self.run_cli('set', 'session-scale-up', '--action', action, '--chord', chord, '--passthrough', 'vm-passthrough', '--apply')
            self.assertNotEqual(r.returncode, 0, f'{action} on {chord}\n' + r.stdout + r.stderr)
            self.assertIn('session-scale-up: chord', r.stdout + r.stderr)
            self.assertIn(f'(not {action})', r.stdout + r.stderr)
            self.assertFalse(self.user.exists(), 'a rejected draft is never written, even with --apply')
        r = self.run_cli('set', 'session-scale-up', '--action', 'exec:alacritty', '--chord', 'CTRL+ALT+SHIFT+F9', '--passthrough', 'vm-passthrough')
        self.assertEqual(r.returncode, 0, 'positive control: a free chord validates\n' + r.stdout + r.stderr)
        old_gen, old_bin = os.environ.get('MIOS_GEN_OLD'), os.environ.get('MIOS_CONTROLS_OLD')
        draft = ['set', 'session-scale-up', '--action', 'exec:alacritty', '--chord', 'CTRL+ALT+SHIFT+m', '--passthrough', 'vm-passthrough']
        if old_gen:
            # The gate (compiled into this editor) flags the row on its own when the generator still drops it.
            r = subprocess.run([str(self.bin), '--root', str(ROOT), *draft], capture_output=True, text=True, timeout=120, env=dict(self.env, MIOS_GEN=old_gen))
            self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn('session-scale-up: realized_by hyprland on CTRL+ALT+SHIFT+m but hyprland.lua binds that chord to', r.stdout)
        if old_gen and old_bin:
            r = subprocess.run([old_bin, '--root', str(ROOT), *draft], capture_output=True, text=True, timeout=120, env=dict(self.env, MIOS_GEN=old_gen))
            self.assertEqual(r.returncode, 0, 'negative control: the pre-fix editor + generator accept the dead row\n' + r.stdout + r.stderr)
            self.assertIn('0 finding(s)', r.stdout)

    def test_reserved_terminal_chord_is_rejected(self):
        r = self.run_cli('add', 'steal-ctrl-c', '--chord', 'CTRL+c', '--action', 'exec:true', '--contexts', 'terminal')
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn('reserved terminal chord', r.stdout)

    def test_system_layer_rejects_guide_and_unknown_buttons(self):
        r = self.run_cli('pad', 'BTN_MODE', 'hypr:togglefloating')
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn('modifier', r.stdout)
        r = self.run_cli('pad', 'BTN_BOGUS', 'hypr:togglefloating')
        self.assertEqual(r.returncode, 1, r.stdout)
        r = self.run_cli('pad', 'BTN_WEST', 'exec:foot')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class Apply(Base):
    def test_apply_restore_round_trip_keeps_foreign_tables_and_never_prints_them(self):
        original = f'# operator notes\n[identity]\nuser_password = "{SECRET}"\n'
        self.write_user(original)
        r = self.run_cli('set', 'terminal', '--chord', 'SUPER+F8', '--apply')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn(SECRET, r.stdout + r.stderr)
        text = self.user.read_text()
        self.assertTrue(text.startswith(original), 'other tables keep their exact bytes')
        self.assertEqual(stat.S_IMODE(self.user.stat().st_mode), 0o600)
        data = tomllib.loads(text)
        chords = {b['id']: b['chord'] for b in data['keybindings']['global']['bindings']}
        self.assertEqual(chords['terminal'], 'SUPER+F8')
        backup = self.user.with_name('mios.toml.controls-backup')
        self.assertEqual(backup.read_text(), original)
        self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)
        listed = json.loads(self.run_cli('list', '--json').stdout)
        self.assertEqual({b['id']: b['chord'] for b in listed['bindings']}['terminal'], 'SUPER+F8')
        self.assertNotIn(SECRET, json.dumps(listed))
        r = self.run_cli('restore-defaults', '--apply')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.user.read_text().rstrip(), original.rstrip(), 'restore removes every override and stub header')

    def test_unchanged_draft_is_not_written(self):
        self.write_user('[identity]\nx = 1\n')
        before = self.user.stat().st_mtime_ns
        r = self.run_cli('restore-defaults', '--apply')
        self.assertIn('no change to apply', r.stdout)
        self.assertEqual(self.user.stat().st_mtime_ns, before)


class ImportExport(Base):
    def test_export_is_private_and_never_overwrites(self):
        out = Path(self.tmp.name) / 'map.toml'
        r = self.run_cli('export', str(out))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(stat.S_IMODE(out.stat().st_mode), 0o600)
        self.assertEqual(set(tomllib.loads(out.read_text())), {'keybindings'})
        r = self.run_cli('export', str(out))
        self.assertNotEqual(r.returncode, 0, 'export must refuse an existing file')

    def test_import_confined_to_the_binding_map(self):
        bad = Path(self.tmp.name) / 'bad.toml'
        bad.write_text('[identity]\nuser_password = "x"\n')
        r = self.run_cli('import', str(bad), '--apply')
        self.assertEqual(r.returncode, 2)
        self.assertIn('outside [keybindings]', r.stderr)
        bad.write_text('[keybindings.global]\nbypass_mod = "ALT"\n')
        self.assertEqual(self.run_cli('import', str(bad)).returncode, 2)
        good = Path(self.tmp.name) / 'good.toml'
        good.write_text('[keybindings.global.controller.system_layer]\nBTN_WEST = "exec:foot"\n')
        r = self.run_cli('import', str(good), '--apply')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(tomllib.loads(self.user.read_text())['keybindings']['global']['controller']['system_layer'], {'BTN_WEST': 'exec:foot'})


class ControllerAudit(Base):
    """GT (C-34 / C-23 / C-16g): list shows the controller tables, check runs the gate's controller audit over the
    resolved user projection, and an explicit --root reads that root's vendor tier."""

    NAV = ('[keybindings.global.controller.nav]\nenabled = true\n[keybindings.global.controller.nav.contexts]\n'
           'desktop-2d = "desktop"\n[keybindings.global.controller.nav.desktop]\n'
           'BTN_SOUTH = "{south}"\nBTN_EAST = "none"\nBTN_WEST = "none"\nBTN_DPAD_UP = "none"\nBTN_DPAD_DOWN = "none"\n'
           'BTN_DPAD_LEFT = "none"\nBTN_DPAD_RIGHT = "none"\nLS_UP = "none"\nLS_DOWN = "none"\nLS_LEFT = "none"\n'
           'LS_RIGHT = "none"\nRS_UP = "none"\nRS_DOWN = "none"\nRS_LEFT = "none"\nRS_RIGHT = "none"\nBTN_TL2 = "none"\n'
           'BTN_TR2 = "none"\nBTN_SELECT = "none"\nBTN_MODE = "none"\nBTN_THUMBL = "none"\nBTN_THUMBR = "none"\n')

    def test_list_json_carries_the_controller_tables(self):
        listed = json.loads(self.run_cli('list', '--json').stdout)
        for k in ['nav', 'room', 'hypr3d', 'toggle_chord', 'mode_override_chord']:
            self.assertIn(k, listed)
        vendor = tomllib.loads((ROOT / 'usr/share/mios/mios.toml').read_text())
        self.assertEqual(listed['toggle_chord'], vendor['desktop']['hyprland']['hypr3d']['controller']['toggle_chord'])
        self.write_user(self.NAV.format(south='hypr:movefocus down'))
        listed = json.loads(self.run_cli('list', '--json').stdout)
        self.assertEqual(listed['nav']['desktop']['BTN_SOUTH'], 'hypr:movefocus down', 'list shows the resolved user projection')

    def test_check_audits_the_user_nav_map(self):
        # (key:/pointer: join action_namespaces with step I2; the vendor namespaces are used here)
        self.write_user(self.NAV.format(south='hypr:movefocus down'))
        r = self.run_cli('check')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.write_user(self.NAV.format(south='bogus'))
        r = self.run_cli('check')
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn('BTN_SOUTH: action "bogus" must be <namespace>:<verb>', r.stdout)
        self.write_user(self.NAV.format(south='hypr:movefocus down').replace('BTN_EAST = "none"\n', ''))
        r = self.run_cli('check')
        self.assertIn('desktop-2d (desktop): BTN_EAST unmapped', r.stdout, 'every nav input maps to an action or "none"')

    def test_console_rows_take_console_namespaces_only(self):
        row = '[[keybindings.global.bindings]]\nid = "vt-x"\ntier = 1\nchord = "SUPER+CTRL+F3"\naction = "{a}"\ncontexts = ["console"]\n'
        r = self.run_cli('import', str(self._file(row.format(a='hypr:killactive'))))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn('cannot run on the console', r.stdout)
        r = self.run_cli('import', str(self._file(row.format(a='tmux:menu'))))
        self.assertNotIn('cannot run on the console', r.stdout)

    def test_explicit_root_reads_its_own_vendor_tier(self):
        scratch = Path(self.tmp.name) / 'root'
        (scratch / 'usr/share/mios').mkdir(parents=True)
        text = (ROOT / 'usr/share/mios/mios.toml').read_text()
        moved = text.replace('toggle_chord = ["BTN_THUMBL", "BTN_THUMBR"]', 'toggle_chord = ["BTN_TL", "BTN_TR"]', 1)
        self.assertNotEqual(moved, text)
        (scratch / 'usr/share/mios/mios.toml').write_text(moved)
        env = dict(self.env, MIOS_TOML=str(ROOT / 'usr/share/mios/mios.toml'), MIOS_VENDOR_TOML=str(ROOT / 'usr/share/mios/mios.toml'))
        r = subprocess.run([str(self.bin), '--root', str(scratch), 'list', '--json'], capture_output=True, text=True, timeout=60, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)['toggle_chord'], ['BTN_TL', 'BTN_TR'], 'the session MIOS_TOML must not redirect --root')

    def _file(self, text):
        p = Path(self.tmp.name) / f'import-{len(text)}-{abs(hash(text))}.toml'
        p.write_text(text)
        return p


class Tui(Base):
    """The TUI in a private tmux server (never the operator's): renders, navigates and quits."""

    def setUp(self):
        super().setUp()
        self.sock = f'mios-controls-test-{os.getpid()}'
        self.tmux_env = dict(self.env, TMUX_TMPDIR=self.tmp.name, TERM='xterm-256color')
        self.tmux_env.pop('TMUX', None)

    def tmux(self, *a):
        return subprocess.run(['tmux', '-L', self.sock, '-f', '/dev/null', *a], capture_output=True, text=True, env=self.tmux_env, timeout=10)

    def screen(self, want, timeout=10.0):
        end = time.monotonic() + timeout
        text = ''
        while time.monotonic() < end:
            text = self.tmux('capture-pane', '-p', '-t', 'c').stdout
            if want in text:
                return text
            time.sleep(0.1)
        self.fail(f'{want!r} not on screen:\n{text}')

    def tearDown(self):
        self.tmux('kill-server')
        super().tearDown()

    def test_renders_navigates_and_quits(self):
        cmd = f'{self.bin} --root {ROOT}; echo EXITED:$?; sleep 30'
        r = self.tmux('new-session', '-d', '-s', 'c', '-x', '170', '-y', '45', cmd)
        self.assertEqual(r.returncode, 0, r.stderr)
        s = self.screen('MiOS Controls')
        for part in ['Peripherals', 'Keyboard', 'Bindings', 'app-drawer', '? help']:
            self.assertIn(part, s)
        self.screen('valid:', timeout=60)
        self.tmux('send-keys', '-t', 'c', ']')
        self.screen('context [desktop-2d]')
        self.tmux('send-keys', '-t', 'c', 'Tab')
        self.tmux('send-keys', '-t', 'c', 'Down')
        self.tmux('send-keys', '-t', 'c', 'Enter')
        self.screen('Controller')
        self.tmux('send-keys', '-t', 'c', '?')
        self.screen('TAKING CONTROL')
        self.tmux('send-keys', '-t', 'c', 'x')
        self.tmux('send-keys', '-t', 'c', 'q')
        self.screen('EXITED:0')
        self.assertFalse(self.user.exists(), 'browsing writes nothing')


if __name__ == '__main__':
    unittest.main()
