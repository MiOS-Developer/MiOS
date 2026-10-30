#!/usr/bin/env python3
# AI-hint: Real-binary controls for the Hypr3D room table (spec P1, C-14a/b): mios-inputd replay, resolve-action and check over the SSOT [keybindings.global.controller.room]; the candidate passes, the installed (pre-P1) binary is the negative control.
# AI-related: tools/native/mios-inputd/src/router.rs, tools/native/mios-inputd/src/config.rs, usr/share/mios/mios.toml [keybindings.global.controller.room], docs/design/mios-global-bindings-tests.md C-14a/b
# AI-functions: RoomTable
# MIOS_INPUTD_BIN = the candidate (default: the installed binary); MIOS_INPUTD_OLD = a pre-P1 binary for the negative
# controls (skipped when unset). Runs against a private copy of the SSOT; no device, no compositor.
import json
import os
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BIN = Path(os.environ.get('MIOS_INPUTD_BIN', '/usr/libexec/mios/mios-inputd'))
OLD = os.environ.get('MIOS_INPUTD_OLD', '')
SSOT = Path(os.environ.get('MIOS_INPUTD_SSOT', ROOT / 'usr/share/mios/mios.toml'))
EAST, NORTH, SELECT, GUIDE, L3, R3, SOUTH = 0x131, 0x133, 0x13a, 0x13c, 0x13d, 0x13e, 0x130


def key(code, value=1):
    return dict(kind=1, code=code, value=value)


class RoomTable(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='mios-room-input-')
        cls.root = Path(cls.temp.name)
        target = cls.root / 'usr/share/mios/mios.toml'
        target.parent.mkdir(parents=True)
        cls.text = SSOT.read_text()
        target.write_text(cls.text)
        cls.env = {k: v for k, v in os.environ.items() if not (k.startswith('MIOS_') and 'TOML' in k)}
        cls.env['HOME'] = str(cls.root / 'home')
        cls.env['XDG_CONFIG_HOME'] = str(cls.root / 'home/.config')
        cls.room = tomllib.loads(cls.text)['keybindings']['global']['controller']['room']

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def run_bin(self, binary, *args, data=None, root=None):
        return subprocess.run([str(binary), '--root', str(root or self.root), *args], text=True, capture_output=True,
                              input=None if data is None else json.dumps(data), env=self.env, timeout=10)

    def replay(self, context, events, tick=0, binary=BIN, axes=None):
        r = self.run_bin(binary, 'replay', data=dict(context=context, events=events, axes=axes or {}, tick_ms=tick))
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_ssot_declares_the_p1_room_table(self):
        self.assertEqual(self.room['BTN_EAST'], 'hypr:hypr3d:cancel')
        self.assertEqual(self.room['BTN_THUMBL'], 'hypr:hypr3d:sprint')
        self.assertEqual(self.room['BTN_THUMBR'], 'hypr:hypr3d:recenter')
        self.assertEqual(self.room['BTN_SELECT'], ['hypr:hypr3d:view', 'quickshell:keymap-toggle'])
        self.assertEqual(self.room['BTN_MODE'], 'quickshell:drawer-toggle')
        r = self.run_bin(BIN, 'check')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)['unresolved_actions'], [])

    def test_east_is_cancel_in_the_room_only(self):
        o = self.replay('desktop-3d', [key(EAST), key(EAST, 0)])['output']
        self.assertEqual(o['actions'], ['hypr:hypr3d:cancel'])
        self.assertEqual(o['app'], [], 'the room keeps East from the app')
        for ctx in ('desktop-2d', 'terminal'):
            o = self.replay(ctx, [key(EAST), key(EAST, 0)])['output']
            self.assertFalse(any('hypr3d' in a for a in o['actions']), f'{ctx}: East is not the room cancel: {o}')

    def test_every_captured_window_kind_owns_east(self):
        # O-1/O-4: capture is the same for every window kind: no class list decides who receives the button.
        for ctx in ('app-capture', 'game', 'vm-passthrough'):
            o = self.replay(ctx, [key(EAST), key(EAST, 0)])['output']
            self.assertFalse(any('hypr3d' in a for a in o['actions']), f'{ctx}: {o}')

    def test_l3_sprint_r3_recenter_on_a_clean_release(self):
        self.assertEqual(self.replay('desktop-3d', [key(L3), key(L3, 0)])['output']['actions'], ['hypr:hypr3d:sprint'])
        self.assertEqual(self.replay('desktop-3d', [key(R3), key(R3, 0)])['output']['actions'], ['hypr:hypr3d:recenter'])
        # L3 + R3 together (10 ms apart, inside the chord debounce) is the room toggle and never also a single
        o = self.replay('desktop-3d', [key(L3), key(R3), key(L3, 0), key(R3, 0)])['output']['actions']
        self.assertEqual(o, ['mios:hyprland-3d-toggle'])
        # R3 is ignored while the right stick is deflected (R-23)
        axes = {str(c): dict(min=-32768, max=32767, value=0) for c in (0, 1, 3, 4)}
        o = self.replay('desktop-3d', [dict(kind=3, code=3, value=30000), key(R3), key(R3, 0)], axes=axes)['output']['actions']
        self.assertNotIn('hypr:hypr3d:recenter', o)
        # NEGATIVE: the left stick deflected does not suppress L3 (sprint while walking)
        o = self.replay('desktop-3d', [dict(kind=3, code=1, value=-30000), key(L3), key(L3, 0)], axes=axes)['output']['actions']
        self.assertIn('hypr:hypr3d:sprint', o)

    def test_select_tap_is_the_view_slot_and_hold_is_the_keymap(self):
        tap = self.replay('desktop-3d', [key(SELECT), key(SELECT, 0)])
        self.assertEqual(tap['output']['actions'], [], 'the view tap spawns no process')
        self.assertEqual(tap['pad']['sequences'][0], 1, 'tap = packet view slot 0')
        hold = self.replay('desktop-3d', [key(SELECT)], tick=600)
        self.assertEqual(hold['output']['actions'], ['quickshell:keymap-toggle'])
        self.assertEqual(hold['pad']['sequences'][0], 0, 'a hold does not also cycle the view')

    def test_guide_tap_opens_the_drawer_and_guide_layer_does_not(self):
        self.assertEqual(self.replay('desktop-3d', [key(GUIDE), key(GUIDE, 0)])['output']['actions'], ['quickshell:drawer-toggle'])
        o = self.replay('desktop-3d', [key(GUIDE), key(SOUTH), key(SOUTH, 0), key(GUIDE, 0)])['output']['actions']
        self.assertNotIn('quickshell:drawer-toggle', o, 'Guide + South is the system layer, not a tap')

    def test_room_verbs_resolve_to_the_plugin_lua_api(self):
        for action, lua in (('hypr:hypr3d:cancel', 'hl.plugin.hypr3d.cancel()'), ('hypr:hypr3d:sprint', 'hl.plugin.hypr3d.sprint()'),
                            ('hypr:hypr3d:recenter', 'hl.plugin.hypr3d.recenter()')):
            r = self.run_bin(BIN, 'resolve-action', action)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn(lua, json.loads(r.stdout)['argv'])
        r = self.run_bin(BIN, 'resolve-action', 'hypr:hypr3d:unknown')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('unsupported room action', r.stderr)

    def test_invalid_room_tables_are_rejected(self):
        for bad, why in (('BTN_EAST = "hypr:hypr3d:unknown"', 'unsupported room action'),
                         ('BTN_NORTH = "hypr:hypr3d:cancel"', 'ownership contract'),
                         ('BTN_MODE = ["quickshell:drawer-toggle", "quickshell:keymap-toggle"]', 'Guide is a tap')):
            with tempfile.TemporaryDirectory() as d:
                t = Path(d) / 'usr/share/mios/mios.toml'
                t.parent.mkdir(parents=True)
                head, sep, tail = self.text.partition('[keybindings.global.controller.room]\n')
                self.assertTrue(sep)
                body, rest = tail.split('\n\n', 1)
                lines = [l for l in body.splitlines() if l.split('=')[0].strip() != bad.split('=')[0].strip()]
                t.write_text(head + sep + '\n'.join(lines + [bad]) + '\n\n' + rest)
                r = self.run_bin(BIN, 'check', root=d)
                self.assertNotEqual(r.returncode, 0, f'accepted {bad}')
                self.assertIn(why, r.stderr)

    @unittest.skipUnless(OLD, 'MIOS_INPUTD_OLD (a pre-P1 binary) not set')
    def test_negative_control_the_pre_p1_binary_has_no_room_table(self):
        o = self.replay('desktop-3d', [key(EAST), key(EAST, 0)], binary=OLD)['output']
        self.assertNotIn('hypr:hypr3d:cancel', o['actions'], 'the old binary must not already implement P1')
        o = self.replay('desktop-3d', [key(GUIDE), key(GUIDE, 0)], binary=OLD)['output']
        self.assertNotIn('quickshell:drawer-toggle', o['actions'])
        r = self.run_bin(OLD, 'resolve-action', 'hypr:hypr3d:cancel')
        self.assertNotEqual(r.returncode, 0, 'the old binary has no cancel verb')


if __name__ == '__main__':
    unittest.main()
