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

class TestConfiguratorSecurity(unittest.TestCase):
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
