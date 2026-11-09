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
        exported = tomllib.loads(out.read_text())
        # the binding map is [keybindings] plus the compositor key tables the editor may move rows in
        self.assertEqual(set(exported), {'keybindings', 'desktop'})
        self.assertLessEqual(set(exported['desktop']), {'hyprland', 'screenshot', 'shaderglass'})
        for table in exported['desktop'].values():
            self.assertEqual(set(table), {'bindings'}, 'only the bindings arrays of the desktop tables are exported')
        r = self.run_cli('export', str(out))
        self.assertNotEqual(r.returncode, 0, 'export must refuse an existing file')

    def test_import_confined_to_the_binding_map(self):
        bad = Path(self.tmp.name) / 'bad.toml'
        bad.write_text('[identity]\nuser_password = "x"\n')
        r = self.run_cli('import', str(bad), '--apply')
        self.assertEqual(r.returncode, 2)
        self.assertIn('import may carry only the editable binding map', r.stderr)
        self.assertIn('not identity', r.stderr)
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
        # Plant the gap in a user-named profile: the vendor tier's full nav.desktop would fill a removed
        # BTN_EAST back in through the layered merge, but nothing below the user tier defines "mine".
        mine = (self.NAV.format(south='hypr:movefocus down').replace('BTN_EAST = "none"\n', '')
                .replace('desktop-2d = "desktop"', 'desktop-2d = "mine"').replace('controller.nav.desktop]', 'controller.nav.mine]'))
        self.write_user(mine)
        r = self.run_cli('check')
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn('desktop-2d (mine): BTN_EAST unmapped', r.stdout, 'every nav input maps to an action or "none"')

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
        # a host-tier fragment points the pad thread at no real controller: a capture grabs the pads it reads, and
        # the operator's controllers are not this test's to grab
        hostd = Path(self.tmp.name) / 'host.d'
        hostd.mkdir()
        for frag in sorted(Path('/etc/mios/mios.d').glob('*.toml')):
            try:
                (hostd / frag.name).write_text(frag.read_text())
            except OSError:
                pass
        (hostd / '99-pads.toml').write_text('[desktop.controls]\npad_names = ["mios-input-fixture-no-such-pad"]\n')
        self.tmux_env = dict(self.env, TMUX_TMPDIR=self.tmp.name, TERM='xterm-256color', MIOS_HOST_TOML_D=str(hostd))
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
        s = self.screen('valid:', timeout=60)
        for part in ['MiOS Controls', '1 Keyboard', '2 Mouse', '3 Controller', '4 Bindings', '5 Devices', 'Esc', 'Space', '? help']:
            self.assertIn(part, s)
        self.tmux('send-keys', '-t', 'c', ']')
        self.screen('context [desktop-2d]')
        self.tmux('send-keys', '-t', 'c', '3')
        self.screen('Controller: ')
        self.tmux('send-keys', '-t', 'c', '?')
        self.screen('TAKING CONTROL')
        self.tmux('send-keys', '-t', 'c', 'x')
        self.tmux('send-keys', '-t', 'c', 'q')
        self.screen('EXITED:0')
        self.assertFalse(self.user.exists(), 'browsing writes nothing')

    def test_select_key_capture_rebind_undo_and_apply(self):
        """Keyboard view: move to a key, list its uses, capture-to-bind, rebind a Guide button, undo and apply."""
        cmd = f'{self.bin} --root {ROOT}; echo EXITED:$?; sleep 30'
        self.assertEqual(self.tmux('new-session', '-d', '-s', 'c', '-x', '170', '-y', '45', cmd).returncode, 0)
        self.screen('valid:', timeout=60)
        # capture-to-bind with a terminal key: c, then press x -> the drawing selects key x and lists its uses
        self.tmux('send-keys', '-t', 'c', 'c')
        self.screen('CAPTURE')
        self.tmux('send-keys', '-t', 'c', 'x')
        self.screen('key x (')
        # search jumps to the Guide-layer use on the controller drawing; rebind it by moving the cursor (a swap),
        # then undo, redo and undo again
        self.tmux('send-keys', '-t', 'c', '/')
        self.tmux('send-keys', '-t', 'c', '-l', 'launch-browser')
        self.screen('Search "launch-browser": 1')
        self.tmux('send-keys', '-t', 'c', 'Enter')
        self.tmux('send-keys', '-t', 'c', 'Enter')
        self.screen('Guide + BTN_NORTH on the Controller drawing')
        self.tmux('send-keys', '-t', 'c', 'r')
        self.screen('REBIND Guide + BTN_NORTH')
        self.tmux('send-keys', '-t', 'c', 'Down')
        self.tmux('send-keys', '-t', 'c', 'Enter')
        self.screen('Guide + BTN_NORTH <-> Guide + BTN_SOUTH (swapped)', timeout=60)
        self.screen('DRAFT (1 edit(s))')
        self.tmux('send-keys', '-t', 'c', 'u')
        self.screen('| saved |')
        self.tmux('send-keys', '-t', 'c', 'U')
        self.screen('DRAFT (1 edit(s))')
        self.tmux('send-keys', '-t', 'c', 'u')
        self.screen('| saved |')
        self.tmux('send-keys', '-t', 'c', 'q')
        self.screen('EXITED:0')
        self.assertFalse(self.user.exists(), 'nothing applied')


class DeviceViews(Base):
    """The drawings and the read-only verbs: layout, uses, search, conflicts, devices, profile. The pre-fix editor
    (MIOS_CONTROLS_OLD, e.g. the installed one) has none of these verbs: the negative control."""

    VIEWS = ('keyboard', 'mouse', 'controller', 'bindings', 'devices')
    BOX = set(chr(c) for c in range(0x2500, 0x2580))

    def layout(self, view, *extra, term='xterm-256color', w=160, h=45):
        env = dict(self.env, TERM=term)
        return subprocess.run([str(self.bin), '--root', str(ROOT), 'layout', view, '--width', str(w), '--height', str(h), *extra],
                              capture_output=True, text=True, timeout=60, env=env)

    def test_every_view_draws_at_desktop_pip_and_console_sizes(self):
        for view in self.VIEWS:
            for w, h in ((160, 45), (100, 30), (80, 25), (60, 20)):
                r = self.layout(view, w=w, h=h)
                self.assertEqual(r.returncode, 0, f'{view} {w}x{h}: {r.stderr}')
                lines = r.stdout.rstrip('\n').split('\n')
                self.assertTrue(all(len(l) <= w for l in lines), f'{view} {w}x{h} overflows')
                self.assertIn('MiOS Controls', r.stdout)

    def test_keyboard_drawing_has_every_row_and_marks_the_selection(self):
        r = self.layout('keyboard', '--select', 'key:c', '--layer', 'SUPER')
        self.assertEqual(r.returncode, 0, r.stderr)
        for cap in ('Esc', 'F12', 'Bksp', 'Tab', 'Caps', 'Enter', 'Shift', 'Ctrl', 'Super', 'Space', 'PgUp'):
            self.assertIn(cap, r.stdout)
        self.assertIn('key c (', r.stdout, 'the selected key lists its uses')
        self.assertIn('hypr:killactive', r.stdout)

    def test_mouse_drawing_has_buttons_wheel_and_side_buttons(self):
        r = self.layout('mouse', w=120, h=40)
        self.assertEqual(r.returncode, 0, r.stderr)
        for part in ('Left', 'Right', 'Mid', 'Side', 'Extra', 'Fwd', 'Back'):
            self.assertIn(part, r.stdout)

    def test_controller_families_have_their_own_glyphs_and_layout(self):
        xbox = self.layout('controller', '--family', 'xbox', '--glyphs', 'unicode').stdout
        ps = self.layout('controller', '--family', 'playstation', '--glyphs', 'unicode').stdout
        gen = self.layout('controller', '--family', 'generic', '--glyphs', 'unicode').stdout
        for part in ('LB', 'RB', 'LT', 'RT', 'View', 'Menu', 'Xbox', 'LS', 'RS'):
            self.assertIn(part, xbox)
        for part in ('\u2715', '\u25cb', '\u25a1', '\u25b3', 'L1', 'R2', 'Options', 'PS'):
            self.assertIn(part, ps)
        self.assertIn('Guide', gen)
        self.assertNotEqual(xbox, ps)
        vt = self.layout('controller', '--family', 'playstation', term='linux').stdout
        self.assertNotIn('\u2715', vt, 'the VT font has no PlayStation shapes')
        self.assertIn('Tri', vt)

    def test_vt_console_output_is_ascii_plus_single_box_lines(self):
        for view in self.VIEWS:
            r = self.layout(view, term='linux', w=100, h=40)
            self.assertEqual(r.returncode, 0, r.stderr)
            odd = {c for c in r.stdout if ord(c) > 127} - self.BOX
            self.assertEqual(odd, set(), f'{view} on TERM=linux draws glyphs the VT font lacks')
            self.assertFalse({'\u256d', '\u2550', '\u2551'} & set(r.stdout), 'no rounded or double lines on the VT')
        r = self.layout('keyboard', '--glyphs', 'ascii')
        self.assertEqual({c for c in r.stdout if ord(c) > 127}, set(), 'glyphs = ascii is pure ASCII')

    def test_uses_lists_every_layer_for_an_input(self):
        r = self.run_cli('uses', 'pad:BTN_EAST', '--json')
        self.assertEqual(r.returncode, 0, r.stderr)
        uses = json.loads(r.stdout)
        self.assertEqual({u['source'] for u in uses}, {'guide', 'fixed', 'room'}, uses)
        self.assertIn('hypr:hypr3d:cancel', [u['action'] for u in uses if u['source'] == 'room'])
        r = self.run_cli('uses', 'key:x')
        self.assertIn('tmux-os', r.stdout, 'the tmux-os prefix layer is one of the uses')
        r = self.run_cli('uses', 'key:return')
        self.assertIn('SUPER+Return', r.stdout)
        r = self.run_cli('uses', 'bogus:thing')
        self.assertNotEqual(r.returncode, 0, 'an unknown input is an error, not an empty list')

    def test_search_finds_actions_and_chords(self):
        r = self.run_cli('search', 'screenshot', '--json')
        self.assertEqual(r.returncode, 0, r.stderr)
        found = json.loads(r.stdout)
        self.assertTrue(any('screenshot' in (u['action'] + u['label'] + u['note']).lower() or 'shot' in u['action'] for u in found))
        r = self.run_cli('search', 'no-such-action-anywhere', '--json')
        self.assertEqual(json.loads(r.stdout), [])

    def test_conflicts_are_found_and_highlighted(self):
        r = self.run_cli('conflicts')
        self.assertEqual(r.returncode, 0, 'the shipped map has no conflicts\n' + r.stdout)
        self.write_user('[desktop.screenshot]\nbindings = [ { key = "SUPER + C", args = "region", label = "Shot" } ]\n')
        r = self.run_cli('conflicts', '--json')
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        hit = [u for u in json.loads(r.stdout) if u['conflict']]
        self.assertTrue(any(u['input'] == 'key:c' for u in hit), hit)
        r = self.layout('keyboard', '--layer', 'SUPER', '--select', 'key:c')
        self.assertIn('!desktop', r.stdout.replace('\u2502', ''), 'conflicting uses are flagged in the panel')

    def test_devices_and_profile_are_read_only(self):
        r = self.run_cli('devices', '--json')
        self.assertEqual(r.returncode, 0, r.stderr)
        devs = json.loads(r.stdout)
        if not devs:
            self.skipTest('no input devices visible')
        r = self.run_cli('profile', '0')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotEqual(self.run_cli('profile', 'no-such-device-name').returncode, 0)
        self.assertFalse(self.user.exists())

    def test_pre_fix_editor_lacks_the_device_views(self):
        old = os.environ.get('MIOS_CONTROLS_OLD')
        if not old:
            self.skipTest('set MIOS_CONTROLS_OLD to the pre-fix binary')
        for args in (['layout', 'keyboard'], ['uses', 'key:c'], ['conflicts'], ['rebind', 'guide:BTN_NORTH', '--to', 'pad:BTN_WEST']):
            r = subprocess.run([old, '--root', str(ROOT), *args], capture_output=True, text=True, timeout=60, env=self.env)
            self.assertNotEqual(r.returncode, 0, f'negative control: the pre-fix editor has {args[0]}')


class Rebind(Base):
    """rebind moves one use to another input through the same draft -> validate -> apply pipeline."""

    def test_guide_layer_buttons_swap(self):
        r = self.run_cli('rebind', 'guide:BTN_NORTH', '--to', 'pad:BTN_WEST')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('swapped', r.stdout)
        self.assertIn('0 finding(s)', r.stdout)
        self.assertFalse(self.user.exists(), 'dry run')
        r = self.run_cli('rebind', 'guide:BTN_NORTH', '--to', 'pad:BTN_WEST', '--apply')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        layer = tomllib.loads(self.user.read_text())['keybindings']['global']['controller']['system_layer']
        self.assertEqual(layer['BTN_WEST'], 'mios:launch-browser')
        self.assertEqual(stat.S_IMODE(self.user.stat().st_mode), 0o600)

    def test_room_button_moves(self):
        r = self.run_cli('rebind', 'room:BTN_EAST', '--to', 'pad:BTN_TR')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('BTN_TR = "hypr:hypr3d:cancel"', r.stdout)

    def test_global_row_moves_with_its_compositor_row_and_a_collision_is_refused(self):
        r = self.run_cli('rebind', 'global:screenshot-region', '--to', 'SUPER+F9')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        r = self.run_cli('rebind', 'global:screenshot-region', '--to', 'SUPER+c', '--apply')
        self.assertEqual(r.returncode, 1, 'negative control: a colliding rebind is refused\n' + r.stdout)
        self.assertIn('NEW FINDING: collision chord SUPER+c', r.stdout)
        self.assertFalse(self.user.exists(), 'a refused draft is never written')

    def test_unmovable_and_invalid_targets_are_refused(self):
        r = self.run_cli('rebind', 'global:terminal', '--to', 'SUPER+F9')
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn('cannot move', r.stderr)
        r = self.run_cli('rebind', 'tmux:x', '--to', 'key:F7')
        self.assertEqual(r.returncode, 2)
        self.assertIn('tmux-os cannot bind F7', r.stderr)
        r = self.run_cli('rebind', 'tmux:x', '--to', 'key:k', '--apply')
        self.assertEqual(r.returncode, 1, 'a tmux key that does not project is a finding\n' + r.stdout)
        self.assertFalse(self.user.exists())

    def test_apply_refuses_foreign_tables_and_keeps_them_byte_identical(self):
        original = f'[identity]\nuser_password = "{SECRET}"\n'
        self.write_user(original)
        r = self.run_cli('rebind', 'room:BTN_EAST', '--to', 'pad:BTN_TR', '--apply')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn(SECRET, r.stdout + r.stderr)
        self.assertTrue(self.user.read_text().startswith(original))
        self.assertEqual(self.user.with_name('mios.toml.controls-backup').read_text(), original)

if __name__ == '__main__':
    unittest.main()
