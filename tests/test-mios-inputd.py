#!/usr/bin/env python3
# AI-hint: Functional native input policy and legacy controller parity tests; no real device grabs.
# AI-related: tools/native/mios-inputd, usr/libexec/mios/mios-gamepad-hypr3d
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[1]
BIN = Path(os.environ.get('MIOS_INPUTD_BIN', '/usr/libexec/mios/mios-inputd'))


class InputPolicy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not BIN.is_file():
            raise AssertionError(f'required native artifact missing: {BIN}')
        cls.temp = tempfile.TemporaryDirectory(prefix='mios-input-policy-')
        cls.root = Path(cls.temp.name)
        target = cls.root / 'usr/share/mios/mios.toml'
        target.parent.mkdir(parents=True)
        # Test the published policy with the real legacy configuration, never a
        # hardcoded substitute pretending to be production parity.
        source = Path(os.environ.get('MIOS_INPUTD_SSOT', ROOT / 'usr/share/mios/mios.toml'))
        cls.source = source.read_text()
        cls.data = tomllib.loads(cls.source)
        assert cls.data['keybindings']['global']['controller']['enabled']
        target.write_text(cls.source)
        cls.env = dict(os.environ)
        for key in list(cls.env):
            if key.startswith('MIOS_') and ('TOML' in key or key == 'MIOS_TOML_ROOT'):
                cls.env.pop(key)
        cls.env['HOME'] = str(cls.root / 'home')
        cls.env['XDG_CONFIG_HOME'] = str(cls.root / 'home/.config')

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def call(self, *args, data=None, valid=True):
        result = subprocess.run([str(BIN), '--root', str(self.root), *args], input=None if data is None else json.dumps(data), text=True, capture_output=True, env=self.env, timeout=5)
        self.assertEqual(result.returncode == 0, valid, result.stdout + result.stderr)
        return json.loads(result.stdout) if valid else result

    def replay(self, context, events, axes=None, tick=0):
        return self.call('replay', data=dict(context=context, events=events, axes=axes or {}, tick_ms=tick))

    @staticmethod
    def key(code, value=1):
        return dict(kind=1, code=code, value=value)

    def test_published_map_loads(self):
        self.assertTrue(self.call('check')['valid'])
        value = self.call('map')
        self.assertGreater(len(value['global']['controller']['system_layer']), 0)
        self.assertFalse(value['global']['controller']['exclusive_grab'])

    def test_game_keeps_ordinary_shoulders(self):
        result = self.replay('game', [self.key(0x136), self.key(0x136, 0)])
        self.assertFalse(result['output']['actions'])
        self.assertEqual([e['value'] for e in result['output']['app']], [1, 0])

    def test_guide_system_layer_survives_vm_context(self):
        result = self.replay('vm-passthrough', [self.key(0x13c), self.key(0x136), self.key(0x136, 0), self.key(0x13c, 0)])
        self.assertEqual(result['output']['actions'], ['hypr:workspace e-1'])
        self.assertFalse([e for e in result['output']['app'] if e['kind'] == 1])
        self.assertFalse(result['executed'])

    def test_start_toggles_2d_cursor(self):
        result = self.replay('desktop-2d', [self.key(0x13b)])
        self.assertTrue(result['cursor'])
        self.assertEqual(result['output']['actions'], ['input:cursor on'])

    def test_2d_pointer_movement(self):
        axes = {str(c): dict(min=-32768, max=32767, value=0) for c in (0, 1, 3, 4)}
        result = self.replay('desktop-2d', [self.key(0x13b), dict(kind=3, code=3, value=32767)], axes, 10)
        self.assertTrue(any(e['kind'] == 2 and e['code'] == 0 and e['value'] > 0 for e in result['output']['pointer']))

    def test_terminal_cursor_mode_is_explicitly_available(self):
        result=self.replay('terminal',[self.key(0x13b)])
        self.assertTrue(result['cursor'])

    def test_host_layer_changes_policy_and_invalid_edits_fail(self):
        host=self.root/'etc/mios/mios.toml'
        host.parent.mkdir(parents=True,exist_ok=True)
        try:
            host.write_text('[keybindings.global.controller]\npointer_hz=125\n')
            self.assertEqual(self.call('map')['global']['controller']['pointer_hz'],125)
            host.write_text('[keybindings.global.controller]\nstick_deadzone=2.0\n')
            self.call('check',valid=False)
        finally:
            host.unlink(missing_ok=True)

    def test_unknown_context_fails(self):
        self.call('replay', data=dict(context='invented', events=[]), valid=False)

    def test_invalid_button_and_partial_key_chord_fail(self):
        self.call('normalize', 'BTN_NOT_REAL', valid=False)
        self.call('normalize', 'SUPER+CTRL', valid=False)

    def test_legacy_3d_button_sequence_parity(self):
        legacy = ROOT / 'usr/libexec/mios/mios-gamepad-hypr3d'
        loader = importlib.machinery.SourceFileLoader('mios_input_legacy', str(legacy))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        module = importlib.util.module_from_spec(spec)
        sys.modules[loader.name] = module
        loader.exec_module(module)
        cfg = module.ControllerConfig.from_dict(self.data)
        daemon = module.GamepadHypr3dDaemon(config=cfg, dry_run=True)
        daemon.hypr3d_active = True
        events = []
        for name in (cfg.view_mode_button, cfg.cursor_mode_button, cfg.fullscreen_button, cfg.shaderglass_toggle_button, cfg.shaderglass_cycle_button, cfg.jump_button, cfg.crouch_button):
            code = module.KEY_NAMES[name]
            events += [self.key(code), self.key(code, 0)]
        for i, event in enumerate(events):
            daemon.process_event(event['kind'], event['code'], event['value'], 1.0 + i * .01)
        result = self.replay('desktop-3d', events)
        expected = [daemon.select_seq, daemon.start_seq, daemon.south_seq, daemon.fly_seq, daemon.west_seq, daemon.north_seq, daemon.rb_seq, daemon.lb_seq]
        self.assertEqual(result['pad']['sequences'], expected)

    def test_native_virtual_fixture(self):
        if os.environ.get('MIOS_INPUTD_UINPUT_TEST') != '1':
            self.skipTest('explicit private virtual-device tier not requested')
        value = self.call('fixture')
        for key in ('grab_owner_received', 'ordinary_input_replayed', 'virtual_app_grab_preserves_host_input', 'duplicate_owner_rejected', 'replay_loop_rejected', 'typing_keyboard_rejected', 'keyboard_aux_joystick_rejected'):
            self.assertTrue(value[key], key)
        self.assertEqual(value['release_after_signals'], [15, 9])


if __name__ == '__main__':
    unittest.main()
