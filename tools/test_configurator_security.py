#!/usr/bin/env python3
# AI-hint: Execute the configurator's real JavaScript serializers and path banner against hostile strings; validate emitted TOML with the standard parser.
# AI-related: usr/share/mios/configurator/mios.html
import json
import subprocess
import tomllib
import unittest
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NODE = r'''
const fs = require('fs'), vm = require('vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const html = fs.readFileSync(input.path, 'utf8');
const banner = {innerHTML:'', style:{}};
const ctx = {URL, navigator:{platform:'Win32'}, document:{getElementById:()=>banner},
  window:{location:{href:'https://localhost/?suggested_path='+encodeURIComponent(input.pathAttack)}}};
vm.createContext(ctx);
vm.runInContext('const HAS_FSA = true;', ctx);
for (const name of ['emitTomlValue','escapeHtml','detectPlatform','suggestedPaths','querySuggestedPath','renderPathBanner']) {
  const match = html.match(new RegExp('^function '+name+'\\([^\\n]*\\n[\\s\\S]*?^}', 'm'));
  if (!match) throw new Error('Missing function '+name);
  vm.runInContext(match[0], ctx);
}
ctx.samples = input.samples;
const emitted = vm.runInContext('samples.map(emitTomlValue)', ctx);
vm.runInContext('renderPathBanner()', ctx);
process.stdout.write(JSON.stringify({emitted,banner:banner.innerHTML}));
'''

ROUNDTRIP_NODE = r'''
const fs = require('fs'), vm = require('vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const html = fs.readFileSync(input.path, 'utf8');
const ctx = {sections: input.sections};
vm.createContext(ctx);
for (const name of ['parseToml', 'parseTomlValue', 'emitToml', 'emitTomlValue']) {
  const match = html.match(new RegExp('^function '+name+'\\([^\\n]*\\n[\\s\\S]*?^}', 'm'));
  if (!match) throw new Error('Missing function '+name);
  vm.runInContext(match[0], ctx);
}
const toml = vm.runInContext('emitToml(sections)', ctx);
ctx.toml = toml;
const reparsed = vm.runInContext('parseToml(toml)', ctx);
process.stdout.write(JSON.stringify({toml, reparsed}));
'''

class TestConfiguratorSecurity(unittest.TestCase):
    def test_nvidia_runtime_and_signed_repo_roundtrip(self):
        sections = {
            "repos.external.nvidia-container-toolkit": {
                "enabled": True, "name": "NVIDIA stable",
                "baseurl": "https://nvidia.github.io/libnvidia-container/stable/rpm/$basearch",
                "gpgkey": "https://nvidia.github.io/libnvidia-container/gpgkey",
                "repo_gpgcheck": True,
            },
            "packages.gpu-nvidia-runtime": {
                "enable": True,
                "pkgs": ["libnvidia-container1", "libnvidia-container-tools",
                         "nvidia-container-toolkit-base", "nvidia-container-toolkit"],
            },
        }
        html = ROOT / "usr/share/mios/configurator/mios.html"
        result = subprocess.run(["node", "-e", ROUNDTRIP_NODE],
                                input=json.dumps({"path": str(html), "sections": sections}),
                                capture_output=True, text=True, check=True, timeout=15)
        output = json.loads(result.stdout)
        self.assertEqual(output["reparsed"], sections)
        standard = tomllib.loads(output["toml"])
        self.assertEqual(standard["repos"]["external"]["nvidia-container-toolkit"],
                         sections["repos.external.nvidia-container-toolkit"])
        self.assertEqual(standard["packages"]["gpu-nvidia-runtime"],
                         sections["packages.gpu-nvidia-runtime"])
        markup = html.read_text()
        for key in sections["repos.external.nvidia-container-toolkit"]:
            self.assertIn('data-key="repos.external.nvidia-container-toolkit.' + key + '"', markup)
        self.assertIn('data-key="packages.gpu-nvidia-runtime.pkgs"', markup)

    def test_session_arrays_roundtrip_without_capturing_controller_flags(self):
        routes = json.loads(r'''[
          {"name":"player-one","controllers":["/dev/input/by-id/controller-one-event-joystick"],
           "socket":"/run/user/1000/tmux-1000/mios-human","session":"work","pane":"%1"},
          {"name":"player-two","controllers":["/dev/input/event2","/dev/input/by-id/controller-two-event-joystick"],
           "socket":"/run/user/1000/tmux-1000/mios-human","session":"work","pane":"%2","ssh_host":"operator@blade-two"}
        ]''')
        # The session array deliberately precedes scalar and ordinary-array
        # fields, matching the SSOT insertion order that exposed this bug.
        # Cover both the known-section loop and trailing-section preservation.
        for section, key in [('keybindings.global.controller', 'sessions'),
                             ('autounattend', 'accounts')]:
            with self.subTest(section=section):
                values = {key: routes, 'exclusive_grab': True, 'capture': False,
                          'context_stale_ms': 2500, 'labels': ['first', 'second'],
                          'disabled_devices': []}
                result = subprocess.run(['node', '-e', ROUNDTRIP_NODE], input=json.dumps({
                    'path': str(ROOT / 'usr/share/mios/configurator/mios.html'),
                    'sections': {section: values}}), capture_output=True, text=True,
                    check=True, timeout=15)
                output = json.loads(result.stdout)
                standard = tomllib.loads(output['toml'])
                for part in section.split('.'):
                    standard = standard[part]
                self.assertEqual(standard, values)
                self.assertEqual(output['reparsed'][section], values)
                self.assertNotIn('ssh_host', standard[key][0])
                self.assertEqual(standard[key][1]['ssh_host'], 'operator@blade-two')

    def test_toml_strings_and_arrays_preserve_literal_paths_and_controls(self):
        samples = ['C:\\new\\tools\\MiOS', 'quote " then \\ slash', 'line\n\t\r\b\f\x00',
                   ['C:\\new', 'a", injected = "value', 'tab\tand\\slash']]
        attack = '<img src=x onerror="window.pwned=1">&"\''
        result = subprocess.run(['node', '-e', NODE], input=json.dumps({
            'path': str(ROOT / 'usr/share/mios/configurator/mios.html'),
            'samples': samples, 'pathAttack': attack}), capture_output=True, text=True,
            check=True, timeout=15)
        output = json.loads(result.stdout)
        for original, emitted in zip(samples, output['emitted'], strict=True):
            self.assertEqual(tomllib.loads('value = ' + emitted)['value'], original)
        tags = []
        class Tags(HTMLParser):
            def handle_starttag(self, tag, attrs):
                tags.append(tag)
        Tags().feed(output['banner'])
        self.assertNotIn('img', tags)
        self.assertIn('&lt;img', output['banner'])
        self.assertNotIn(attack, output['banner'])

if __name__ == '__main__':
    unittest.main()
