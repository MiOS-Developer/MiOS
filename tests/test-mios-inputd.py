#!/usr/bin/env python3
# AI-hint: Functional native input policy and legacy controller parity tests; no real device grabs.
# AI-related: tools/native/mios-inputd, usr/libexec/mios/mios-gamepad-hypr3d
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import socket
import threading
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
        self.assertIsInstance(value['global']['controller']['exclusive_grab'], bool)
        self.assertNotIn('recovery_argv', value['global']['controller'], 'I1 retired the recovery owner')

    def test_captured_window_keeps_ordinary_shoulders(self):
        result = self.replay('app-capture', [self.key(0x136), self.key(0x136, 0)])
        self.assertFalse(result['output']['actions'])
        self.assertEqual([e['value'] for e in result['output']['app'] if e['kind'] == 1], [1, 0])
        # An uncaptured game is an ordinary window: no class captures the controller (I1, operator O-1/O-4).
        result = self.replay('game', [self.key(0x136), self.key(0x136, 0)])
        self.assertEqual(result['output']['actions'], ['mios:shaderglass cycle'])

    def test_captured_apps_do_not_fire_system_guide_combinations(self):
        for context in ('app-capture',):
            with self.subTest(context=context):
                result=self.replay(context,[self.key(0x13c),self.key(0x136),self.key(0x136,0),self.key(0x13c,0)])
                self.assertFalse(result['output']['actions'])
                self.assertEqual([e['code'] for e in result['output']['app'] if e['kind']==1],[0x13c,0x136,0x136,0x13c])

    def test_capture_escape_is_explicit_and_does_not_replay_the_chord(self):
        result=self.replay('app-capture',[self.key(0x13c),self.key(0x13a)])
        self.assertEqual(result['output']['actions'],['input:cursor on'])
        self.assertTrue(result['cursor'])
        self.assertFalse([e for e in result['output']['app'] if e['kind']==1])

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
        # North now transfers ownership on tap and has a separate hold action;
        # the remaining room controls retain their legacy sequence contract.
        for name in (cfg.view_mode_button, cfg.cursor_mode_button, cfg.shaderglass_toggle_button, cfg.shaderglass_cycle_button, cfg.jump_button, cfg.crouch_button):
            code = module.KEY_NAMES[name]
            events += [self.key(code), self.key(code, 0)]
        for i, event in enumerate(events):
            daemon.process_event(event['kind'], event['code'], event['value'], 1.0 + i * .01)
        result = self.replay('desktop-3d', events)
        expected = [daemon.select_seq, daemon.start_seq, daemon.south_seq, daemon.fly_seq, daemon.west_seq, daemon.north_seq, daemon.rb_seq, daemon.lb_seq]
        self.assertEqual(result['pad']['sequences'], expected)

    def test_action_registry_overrides_and_invalid_argv_fail(self):
        host=self.root/'etc/mios/mios.toml'
        host.parent.mkdir(parents=True,exist_ok=True)
        try:
            host.write_text('[keybindings.global.action_argv]\n"quickshell:lock"=["/usr/bin/true","operator-lock"]\n')
            self.assertEqual(self.call('resolve-action','quickshell:lock')['argv'], ['/usr/bin/true','operator-lock'])
            host.write_text('[keybindings.global.action_argv]\n"quickshell:lock"=[]\n')
            self.call('check',valid=False)
        finally:
            host.unlink(missing_ok=True)

    def test_vm_escape_checks_focus_in_the_native_transaction(self):
        if not Path('/usr/bin/lua').is_file():
            self.fail('native Lua interpreter required for focus-guard controls')
        for changed in (False,True):
            with self.subTest(focus_changed=changed):
                runtime=self.root/('runtime-changed' if changed else 'runtime-same')
                sig='mios-owned-vm-fixture'
                folder=runtime/'hypr'/sig;folder.mkdir(parents=True)
                server=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
                server.bind(str(folder/'.socket.sock'));server.listen(2);server.settimeout(3)
                records=[];errors=[]
                def serve():
                    try:
                        for _ in range(2):
                            conn,_=server.accept()
                            with conn:
                                request=conn.recv(65536).decode();records.append(request)
                                if request=='j/activewindow':
                                    conn.sendall(json.dumps(dict(address='0xabc',pid=42,**{'class':'looking-glass-client'})).encode())
                                else:
                                    self.assertTrue(request.startswith('/eval '))
                                    address='0xdef' if changed else '0xabc'
                                    script=("hl={get_active_window=function() return {address="+json.dumps(address)+",pid=42,class='looking-glass-client'} end,dsp={send_shortcut=function(t) assert(t.key=='Scroll_Lock' and t.window=='address:0xabc'); return t end},dispatch=function(t) io.write('DISPATCHED\\n') end}\n"+request[6:])
                                    output=subprocess.run(['/usr/bin/lua','-'],input=script,text=True,capture_output=True,timeout=2)
                                    conn.sendall((output.stdout+output.stderr).encode())
                    except Exception as error:
                        errors.append(str(error))
                    finally:
                        server.close()
                thread=threading.Thread(target=serve);thread.start()
                before=dict(self.env)
                try:
                    self.env.update(XDG_RUNTIME_DIR=str(runtime),HYPRLAND_INSTANCE_SIGNATURE=sig)
                    result=self.call('vm-action','vm-capture-toggle',valid=not changed)
                    if changed:
                        self.assertIn('VM focus changed; no input sent',result.stderr)
                        self.assertNotIn('DISPATCHED',result.stderr)
                    else:
                        self.assertTrue(result['native_command_sent'])
                        self.assertEqual(result['client_capture_state'],'unknown')
                finally:
                    self.env=before;thread.join(4)
                self.assertFalse(thread.is_alive())
                self.assertFalse(errors,errors)
                self.assertEqual(len(records),2)

    def test_held_toggle_does_not_retrigger_on_unrelated_buttons(self):
        # L3+R3 remains held while another button is pressed/released later.
        events=[self.key(0x13d),self.key(0x13e)]
        events += [self.key(0x131),self.key(0x131,0)] * 30
        result=self.replay('desktop-2d',events)
        self.assertEqual(result['output']['actions'].count('mios:hyprland-3d-toggle'),1)

    def test_native_virtual_fixture(self):
        if os.environ.get('MIOS_INPUTD_UINPUT_TEST') != '1':
            self.skipTest('explicit private virtual-device tier not requested')
        value = self.call('fixture')
        for key in ('isolated_from_live_bridge', 'grab_owner_received', 'ordinary_input_replayed', 'virtual_app_grab_preserves_host_input', 'duplicate_owner_rejected', 'replay_loop_rejected', 'typing_keyboard_rejected', 'keyboard_aux_joystick_rejected'):
            self.assertTrue(value[key], key)
        self.assertEqual(value['release_after_signals'], [15, 9])


if __name__ == '__main__':
    unittest.main()
