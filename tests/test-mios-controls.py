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
