#!/usr/bin/env python3
# AI-hint: Real-binary positive/negative verification of global keybind collisions and compositor projection drift.
# AI-related: src/mios-rs/mios-gate/src/keybinds.rs, usr/share/mios/keybinds/chord-vectors.json, usr/share/doc/mios/keybinds.md
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import tempfile
import tomllib
import unittest

ROOT = Path(os.environ.get('MIOS_TEST_ROOT', Path(__file__).resolve().parents[1]))
SSOT = ROOT / 'usr/share/mios/mios.toml'
LUA = ROOT / 'usr/share/mios/hyprland/hyprland.lua'
VECTORS = ROOT / 'usr/share/mios/keybinds/chord-vectors.json'


def gate():
    explicit = os.environ.get('MIOS_GATE')
    candidates = [Path(explicit)] if explicit else [ROOT / 'usr/libexec/mios/mios-gate', ROOT / 'usr/bin/mios-gate', ROOT / 'src/mios-rs/target/release/mios-gate', ROOT / 'src/mios-rs/target/debug/mios-gate']
    for path in candidates:
        if path.is_file() and os.access(path, os.X_OK):
            result = subprocess.run([str(path), 'no-such-check'], capture_output=True, text=True, timeout=5)
            if 'keybind-collisions' in result.stdout + result.stderr:
                return path
    raise AssertionError('required mios-gate with keybind-collisions is missing; set MIOS_GATE to the built binary')


def run_gate(binary, root):
    return subprocess.run([str(binary), 'keybind-collisions', '--root', str(root)], capture_output=True, text=True, timeout=5)


class Contract(unittest.TestCase):
    def test_vectors_have_positive_and_negative_controls(self):
        cases = json.loads(VECTORS.read_text())['cases']
        self.assertGreaterEqual(len(cases), 12)
        self.assertTrue(any('expect' not in c for c in cases))
        self.assertTrue(any(c['input'].startswith('BTN_') and 'expect' in c for c in cases))
        for case in cases:
            self.assertIsInstance(case['input'], str)
            if 'expect' in case:
                self.assertTrue(case['expect'])
                self.assertTrue(all(isinstance(step, str) and step for step in case['expect']))

    def test_ssot_declares_global_contract(self):
        data = tomllib.loads(SSOT.read_text())['keybindings']['global']
        self.assertTrue(data['bypass_mod'].startswith('SUPER'))
        self.assertTrue({'hypr', 'exec', 'quickshell', 'mios', 'tmux', 'readline'} <= set(data['action_namespaces']))
        self.assertGreaterEqual(len(data['reserved_terminal']), 5)
        self.assertTrue({1, 2, 3} <= {b['tier'] for b in data['bindings']})
        ids = [b['id'] for b in data['bindings']]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertIn('BTN_TL', data['controller']['system_layer'])
        self.assertFalse(data['controller']['exclusive_grab'], 'vendor capture remains off until migration')


class Gate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.binary = gate()  # A missing required artifact is a failure, never a skip.

    def tmp_root(self, extra='', lua=None):
        temp = tempfile.TemporaryDirectory(prefix='mios-keybind-test-')
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        target = root / 'usr/share/mios/hyprland'
        target.mkdir(parents=True)
        (root / 'usr/share/mios/mios.toml').write_text(SSOT.read_text() + '\n' + extra)
        (target / 'hyprland.lua').write_text(LUA.read_text() if lua is None else lua)
        return root

    def bad(self, extra, needle):
        result = run_gate(self.binary, self.tmp_root(extra))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(needle, result.stdout + result.stderr)

    def test_live_ssot_and_generated_lua_are_clean(self):
        result = run_gate(self.binary, ROOT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('0 finding(s)', result.stdout)
        count=re.search(r'(\d+) declared binding\(s\)',result.stdout)
        self.assertIsNotNone(count)
        self.assertGreater(int(count.group(1)),0)

    def test_literal_concatenation_is_evaluated(self):
        # A controlled concatenated chord exercises the real parser without
        # assuming the generator's spelling/case for the production launcher.
        extra='[[keybindings.global.bindings]]\nid="concat-fixture"\ntier=1\nchord="SUPER+F7"\naction="exec:x"\nrealized_by="hyprland"\npassthrough=["vm-passthrough"]\n'
        text=LUA.read_text()+'\nhl.bind("SUPER" .. " + " .. "F7", hl.dsp.window.close())\n'
        result = run_gate(self.binary, self.tmp_root(extra,lua=text))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_duplicate_of_compositor_chord_fails(self):
        self.bad('[[keybindings.global.bindings]]\nid="dup"\ntier=1\nchord="super+C"\naction="hypr:killactive"\npassthrough=["vm-passthrough"]\n', 'collision')

    def test_reserved_terminal_chord_fails(self):
        self.bad('[[keybindings.global.bindings]]\nid="steal"\ntier=3\nchord="CTRL+c"\naction="exec:x"\ncontexts=["terminal"]\n', 'reserved terminal chord')

    def test_tmux_root_key_on_a_reserved_chord_fails_but_the_prefix_is_exempt(self):
        # MiOS-Tmux tier 3: a root-table key (the agent accept chord) taken from every terminal must not be a
        # reserved terminal chord; the prefix is the tier-3 owner's own chord and stays legal.
        text = SSOT.read_text()
        self.assertIn('accept_chord = "S-Enter"', text)
        root = self.tmp_root()
        (root / 'usr/share/mios/mios.toml').write_text(text.replace('accept_chord = "S-Enter"', 'accept_chord = "C-a"', 1))
        result = run_gate(self.binary, root)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('tmux root key C-a shadows reserved terminal chord', result.stdout + result.stderr)
        prefix = re.search(r'^tmux_prefix\s*=\s*"([^"]+)"', text, re.M).group(1)
        (root / 'usr/share/mios/mios.toml').write_text(text.replace('accept_chord = "S-Enter"', f'accept_chord = "{prefix}"', 1))
        result = run_gate(self.binary, root)
        self.assertNotIn('tmux root key', result.stdout + result.stderr)

    def test_host_binding_leaking_to_vm_fails(self):
        self.bad('[[keybindings.global.bindings]]\nid="leak"\ntier=1\nchord="SUPER+F9"\naction="exec:x"\n', 'vm-passthrough')

    def test_invalid_bypass_fails(self):
        self.bad('[[keybindings.global.bindings]]\nid="badbypass"\ntier=1\nchord="SUPER+F8"\nbypass="SUPER+SHIFT+F8"\naction="exec:x"\npassthrough=["vm-passthrough"]\n', 'bypass_mod')

    def test_declared_realized_binding_without_projection_fails(self):
        self.bad('[[keybindings.global.bindings]]\nid="ghost"\ntier=1\nchord="SUPER+F7"\naction="exec:x"\nrealized_by="hyprland"\npassthrough=["vm-passthrough"]\n', 'realized_by hyprland but no compositor binding')

    def test_removed_real_lua_line_fails_even_with_ssot_declaration(self):
        lines = [line for line in LUA.read_text().splitlines() if not ('hl.bind(' in line and '"space"' in line.lower())]
        result = run_gate(self.binary, self.tmp_root(lua='\n'.join(lines)))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('app-drawer: realized_by hyprland but no compositor binding', result.stdout + result.stderr)

    def test_duplicate_literal_lua_binding_fails(self):
        text = LUA.read_text() + '\nhl.bind("SUPER + F11", hl.dsp.window.fullscreen({mode="fullscreen",action="toggle"}))\n'
        result = run_gate(self.binary, self.tmp_root(lua=text))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('collision', result.stdout + result.stderr)

    def test_missing_projection_fails(self):
        root = self.tmp_root()
        (root / 'usr/share/mios/hyprland/hyprland.lua').unlink()
        result = run_gate(self.binary, root)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('required compositor projection', result.stdout + result.stderr)

    def test_missing_ssot_fails(self):
        root = self.tmp_root()
        (root / 'usr/share/mios/mios.toml').unlink()
        result = run_gate(self.binary, root)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('missing', result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
