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


MON_FORM_NODE = r'''
const fs = require('fs'), vm = require('vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const html = fs.readFileSync(input.path, 'utf8');
const ctx = {DATA: input.initial, controls: input.controls, storeGpuResourceTables:()=>{},
 document:{getElementById:()=>({value:'[]'}),querySelectorAll:()=>input.controls}};
vm.createContext(ctx);
for (const name of ['splitDataKey','gatherFromForm','emitToml','emitTomlValue']) {
 const match=html.match(new RegExp('^function '+name+'\\([^\\n]*\\n[\\s\\S]*?^}', 'm'));
 if(!match)throw new Error('Missing function '+name);
 vm.runInContext(match[0],ctx);
}
vm.runInContext('gatherFromForm()',ctx);
const toml=vm.runInContext('emitToml(DATA)',ctx);
process.stdout.write(JSON.stringify({data:ctx.DATA,toml}));
'''


SAVE_NODE = r'''
const fs = require('fs'), vm = require('vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const html = fs.readFileSync(input.path, 'utf8');
const events = {fetch: [], writes: [], errors: [], statuses: [], downloads: 0, pickers: 0, hidden: 0};
const ctx = {
  MIOS_EMBEDDED: input.embedded !== false,
  FILE_HANDLE: input.handle ? {name:'bound.toml'} : null,
  HAS_FSA: !!input.fsa, LOADED_TOML: 'original', toml: input.toml, Blob,
  hideSavePreview: () => events.hidden++,
  renderSaveErrors: errors => events.errors.push(errors),
  showStatus: (text, level) => events.statuses.push({text, level}),
  writeFileHandle: async (handle, text) => events.writes.push({name:handle.name, text}),
  window: {showSaveFilePicker: async () => { events.pickers++; return {name:'chosen.toml'}; }},
  URL: {createObjectURL: () => 'blob:fixture', revokeObjectURL: () => {}},
  setTimeout: fn => fn(),
  document: {createElement: () => ({click: () => events.downloads++})},
  fetch: async (url, options) => {
    events.fetch.push({url, options});
    if (input.networkError) throw new Error('transport interrupted');
    const status = input.status || 200;
    return {ok: status >= 200 && status < 300, status,
      json: async () => {
        if (input.badJson) throw new Error('bad response JSON');
        return {errors: ['Synthetic validation refusal']};
      }};
  }
};
vm.createContext(ctx);
const match = html.match(/^async function performSave\([^\n]*\n[\s\S]*?^}/m);
if (!match) throw new Error('Missing performSave');
vm.runInContext(match[0], ctx);
(async () => {
  await vm.runInContext('performSave(toml)', ctx);
  process.stdout.write(JSON.stringify({events, loaded:ctx.LOADED_TOML}));
})().catch(error => { console.error(error); process.exitCode = 1; });
'''

class TestConfiguratorSecurity(unittest.TestCase):

    def test_mon_controls_preserve_vendor_inheritance_and_typed_nested_overrides(self):
        html = ROOT / "usr/share/mios/configurator/mios.html"
        class Controls(HTMLParser):
            def __init__(self):
                super().__init__()
                self.controls = []
            def handle_starttag(self, tag, attrs):
                a = dict(attrs)
                key = a.get("data-key", "")
                if tag in ("input", "select") and a.get("data-inherit") == "true":
                    self.controls.append({"tagName": tag.upper(), "type": a.get("type", ""),
                        "value": "", "dataset": {"key": key, "inherit": "true",
                            **({"type": a["data-type"]} if "data-type" in a else {}),
                            **({"array": a["data-array"]} if "data-array" in a else {})}})
        parser = Controls()
        parser.feed(html.read_text())
        by_key = {control["dataset"]["key"]: control for control in parser.controls}
        self.assertEqual(len(by_key), len(parser.controls), "no duplicate inheritance controls")
        for key in ["tamagotchi.camera_view", "tamagotchi.zoom_percent",
                    "tamagotchi.game.training.name_starts", "tamagotchi.game.training.species_colors",
                    "tamagotchi.game.training.personality_colors.cozy",
                    "tamagotchi.camera.pan_step_permille",
                    "tamagotchi.camera.preview_orbit_pauses_turntable"]:
            self.assertIn(key, by_key)
        def gather(initial):
            result = subprocess.run(["node", "-e", MON_FORM_NODE],
                input=json.dumps({"path": str(html), "controls": parser.controls, "initial": initial}),
                capture_output=True, text=True, check=True, timeout=15)
            return json.loads(result.stdout)
        # Empty fields inherit the canonical runtime tier, without an HTML copy
        # of vendor numeric values or an invalid empty-string override.
        output = gather({"identity": {"hostname": "mios-test"}})
        self.assertEqual(output["data"], {"identity": {"hostname": "mios-test"}})
        self.assertNotIn("tamagotchi", tomllib.loads(output["toml"]))
        changes = {"tamagotchi.camera_view": "face", "tamagotchi.zoom_percent": "175",
            "tamagotchi.game.training.name_starts": "Yu, Ko",
            "tamagotchi.game.training.species_colors": "4,8,14,15,14,11,7,1",
            "tamagotchi.game.training.personality_colors.cozy": "3",
            "tamagotchi.camera.pan_step_permille": "50"}
        for key, value in changes.items():
            by_key[key]["value"] = value
        output = gather({})
        parsed = tomllib.loads(output["toml"])["tamagotchi"]
        self.assertEqual(parsed["camera_view"], "face")
        self.assertEqual(parsed["zoom_percent"], 175)
        self.assertEqual(parsed["game"]["training"]["name_starts"], ["Yu", "Ko"])
        self.assertEqual(parsed["game"]["training"]["species_colors"], [4,8,14,15,14,11,7,1])
        self.assertEqual(parsed["game"]["training"]["personality_colors"]["cozy"], 3)
        self.assertEqual(parsed["camera"]["pan_step_permille"], 50)
        self.assertNotIn("preview_orbit_pauses_turntable", parsed["camera"])
        # The camera flag is a typed, inheritable bool select. (Bool emission
        # is the client's job; gatherFromForm owns that coercion.)
        flag = by_key["tamagotchi.camera.preview_orbit_pauses_turntable"]
        self.assertEqual((flag["tagName"], flag["dataset"].get("type")), ("SELECT", "bool"))
        for value, expected in [("true", True), ("false", False)]:
            flag["value"] = value
            typed = tomllib.loads(gather({})["toml"])["tamagotchi"]["camera"]
            self.assertIs(typed["preview_orbit_pauses_turntable"], expected)
        flag["value"] = "not-a-bool"
        with self.assertRaises(subprocess.CalledProcessError) as invalid:
            gather({})
        self.assertIn("Boolean setting requires true or false", invalid.exception.stderr)
        flag["value"] = ""
        for key in ["pan_step_permille", "pan_max_permille", "pan_margin_permille",
                    "tilt_step_degrees", "tilt_min_degrees", "tilt_max_degrees",
                    "orbit_fine_step_degrees", "max_lead_steps"]:
            self.assertEqual(by_key["tamagotchi.camera." + key]["dataset"].get("type"), "int", key)
        # Choosing Inherit for an existing key removes that override only.
        for control in parser.controls:
            control["value"] = ""
        output = gather({"tamagotchi": {"camera_view": "room", "unrelated": "kept"}})
        self.assertEqual(output["data"]["tamagotchi"], {"unrelated": "kept"})

    def test_focused_mon_policy_controls_roundtrip_real_form(self):
        html = ROOT / "usr/share/mios/configurator/mios.html"
        prefix = "keybindings.global.controller.nav."
        class Controls(HTMLParser):
            def __init__(self):
                super().__init__()
                self.controls = []
            def handle_starttag(self, tag, attrs):
                a = dict(attrs)
                if tag in ("input", "select") and a.get("data-key", "").startswith(prefix) and a.get("data-inherit") == "true":
                    self.controls.append({"tagName": tag.upper(), "type": a.get("type", ""),
                        "value": "", "dataset": {"key": a["data-key"], "inherit": "true",
                        **({"type": a["data-type"]} if "data-type" in a else {}),
                        **({"array": a["data-array"]} if "data-array" in a else {})}})
        parser = Controls()
        parser.feed(html.read_text())
        by_key = {c["dataset"]["key"]: c for c in parser.controls}
        self.assertEqual(len(by_key), len(parser.controls))
        inputs = ["LS_LEFT", "LS_RIGHT", "LS_UP", "LS_DOWN", "RS_LEFT", "RS_RIGHT",
                  "RS_UP", "RS_DOWN", "BTN_TL2", "BTN_TR2", "BTN_THUMBL", "BTN_THUMBR"]
        for profile in ("mon_tmux", "mon_plain", "mon_console_tmux", "mon_console_plain"):
            for button in inputs:
                self.assertIn(prefix + profile + "." + button, by_key)
        def gather(initial):
            run = subprocess.run(["node", "-e", MON_FORM_NODE],
                input=json.dumps({"path": str(html), "controls": parser.controls, "initial": initial}),
                capture_output=True, text=True, check=True, timeout=15)
            return json.loads(run.stdout)
        unchanged = {"identity": {"hostname": "kept"}}
        self.assertEqual(gather(unchanged)["data"], unchanged)
        changes = {"app_poll_ms": "300", "app_query_timeout_ms": "100",
            "apps.mon.exe": "/usr/bin/mios-tamagotchi",
            "apps.mon.user_exe": "/home/fixture/.local/bin/mios-tamagotchi-sample",
            "apps.mon.contexts.terminal": "mon", "apps.mon.contexts.console": "mon_console",
            "mon_tmux.LS_UP": "key:ALT+8", "mon_console_plain.RS_LEFT": "none",
            "apps.mon.repeat.LS_UP": "220, 80"}
        for key, value in changes.items():
            by_key[prefix + key]["value"] = value
        emitted = tomllib.loads(gather({})["toml"])["keybindings"]["global"]["controller"]["nav"]
        self.assertEqual(emitted["app_poll_ms"], 300)
        self.assertEqual(emitted["app_query_timeout_ms"], 100)
        self.assertEqual(emitted["apps"]["mon"]["exe"], ["/usr/bin/mios-tamagotchi"])
        self.assertEqual(emitted["apps"]["mon"]["user_exe"],
                         ["/home/fixture/.local/bin/mios-tamagotchi-sample"])
        self.assertEqual(emitted["apps"]["mon"]["repeat"]["LS_UP"], [220, 80])
        self.assertEqual(emitted["apps"]["mon"]["contexts"], {"terminal": "mon", "console": "mon_console"})
        self.assertEqual(emitted["mon_tmux"]["LS_UP"], "key:ALT+8")
        self.assertEqual(emitted["mon_console_plain"]["RS_LEFT"], "none")
        self.assertNotIn("mon_plain", emitted)
        for control in parser.controls:
            control["value"] = ""
        old = {"keybindings.global.controller.nav.mon_tmux": {"LS_UP": "key:ALT+2", "unrelated": "kept"}}
        self.assertEqual(gather(old)["data"]["keybindings.global.controller.nav.mon_tmux"], {"unrelated": "kept"})

    def test_controller_json_arrays_use_the_real_form_and_keep_binding_validation(self):
        html = ROOT / "usr/share/mios/configurator/mios.html"
        class Controls(HTMLParser):
            def __init__(self):
                super().__init__()
                self.by_key = {}
            def handle_starttag(self, tag, attrs):
                a = dict(attrs)
                if tag in ("textarea", "input") and a.get("data-type") == "json-array":
                    self.by_key[a["data-key"]] = {"tagName": tag.upper(), "type": a.get("type", ""),
                        "value": "", "dataset": {"key": a["data-key"], "type": "json-array"}}
        parser = Controls()
        parser.feed(html.read_text())
        session = {"name": "player-two", "controllers": ["/dev/input/event14"],
                   "socket": "/run/user/1000/tmux-1000/mios-human", "session": "play",
                   "pane": "%3", "ssh_host": "fixture@blade-two"}
        binding = {"id": "fixture", "chord": "SUPER+F11", "action": "exec:true"}
        values = {
            "keybindings.global.bindings": [binding],
            "keybindings.global.always_active_actions": [],
            "keybindings.global.controller.sessions": [session],
            "keybindings.global.controller.capture_navigation_contexts": ["terminal"],
            "keybindings.global.controller.nav.tmux_sockets": ["mios-human", "mios-nested-fixture"],
            "keybindings.global.controller.console.pointer_names": ["mios-vctl-pointer"],
        }
        for key, value in values.items():
            self.assertIn(key, parser.by_key)
            parser.by_key[key]["value"] = json.dumps(value)
        controls = [parser.by_key[key] for key in values]
        def gather():
            initial = {"keybindings.global.controller": {"exclusive_grab": False,
                "capture_fullscreen": False}, "identity": {"hostname": "kept"}}
            return subprocess.run(["node", "-e", MON_FORM_NODE],
                input=json.dumps({"path": str(html), "controls": controls, "initial": initial}),
                capture_output=True, text=True, timeout=15)
        result = gather()
        self.assertEqual(result.returncode, 0, result.stderr)
        parsed = tomllib.loads(json.loads(result.stdout)["toml"])
        self.assertEqual(parsed["identity"]["hostname"], "kept")
        global_keys = parsed["keybindings"]["global"]
        controller = global_keys["controller"]
        self.assertEqual(global_keys["bindings"], [binding])
        self.assertEqual(global_keys["always_active_actions"], [])
        self.assertEqual(controller["sessions"], [session])
        self.assertEqual(controller["nav"]["tmux_sockets"], values["keybindings.global.controller.nav.tmux_sockets"])
        self.assertEqual(controller["capture_navigation_contexts"], ["terminal"])
        self.assertEqual(controller["console"]["pointer_names"], ["mios-vctl-pointer"])
        self.assertIs(controller["exclusive_grab"], False)
        self.assertIs(controller["capture_fullscreen"], False)
        for key in values:
            with self.subTest(non_array=key):
                control = parser.by_key[key]
                saved = control["value"]
                for invalid in ("{}", "null", '"text"', "42"):
                    control["value"] = invalid
                    rejected = gather()
                    self.assertNotEqual(rejected.returncode, 0)
                    self.assertIn("Setting requires a JSON array: " + key, rejected.stderr)
                control["value"] = saved
        control = parser.by_key["keybindings.global.bindings"]
        for invalid in ([{}], ["not a binding"], [{"id": "fixture", "chord": "SUPER+F11"}]):
            with self.subTest(invalid_binding=invalid):
                control["value"] = json.dumps(invalid)
                rejected = gather()
                self.assertNotEqual(rejected.returncode, 0)
                self.assertIn("Global bindings require an array of binding objects", rejected.stderr)

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


    def _save_case(self, **case):
        payload = {"path": str(ROOT / "usr/share/mios/configurator/mios.html"),
                   "toml": '# keep comment\n[custom."unknown.key"]\nvalue = "quoted \\"text\\""\n',
                   **case}
        result = subprocess.run(["node", "-e", SAVE_NODE],
                                input=json.dumps(payload), capture_output=True,
                                text=True, check=True, timeout=15)
        return payload, json.loads(result.stdout)

    def test_portal_save_uses_a_lossless_envelope_and_finishes_only_after_success(self):
        payload, output = self._save_case(handle=True, fsa=True)
        events = output["events"]
        self.assertEqual(len(events["fetch"]), 1)
        request = events["fetch"][0]
        self.assertEqual(request["url"], "/portal/config")
        self.assertEqual(request["options"]["credentials"], "same-origin")
        self.assertEqual(request["options"]["headers"]["Content-Type"], "application/json")
        self.assertEqual(json.loads(request["options"]["body"]), {"toml": payload["toml"]})
        self.assertEqual(output["loaded"], payload["toml"])
        self.assertEqual(events["hidden"], 1)
        self.assertEqual(events["writes"], [])
        self.assertEqual(events["pickers"], 0)
        self.assertEqual(events["downloads"], 0)
        self.assertEqual(events["errors"], [])

    def test_portal_refusals_and_unknown_transport_never_fall_through_to_local_save(self):
        cases = [{"status": status} for status in [400, 401, 403, 409, 422, 500, 502, 503]]
        cases += [{"status": 500, "badJson": True}, {"networkError": True}]
        for case in cases:
            with self.subTest(case):
                _, output = self._save_case(handle=True, fsa=True, **case)
                events = output["events"]
                self.assertEqual(events["writes"], [], "a refused Portal save must not overwrite a bound file")
                self.assertEqual(events["pickers"], 0)
                self.assertEqual(events["downloads"], 0)
                self.assertEqual(events["hidden"], 0, "preview stays available for correction")
                self.assertEqual(output["loaded"], "original")
                self.assertTrue(events["errors"])
                self.assertEqual(events["statuses"][-1]["level"], "err")

    def test_standalone_offline_file_picker_and_download_paths_still_save(self):
        for mode in [{"handle": True}, {"fsa": True}, {}]:
            with self.subTest(mode):
                payload, output = self._save_case(embedded=False, **mode)
                events = output["events"]
                self.assertEqual(events["fetch"], [])
                self.assertEqual(output["loaded"], payload["toml"])
                self.assertEqual(events["hidden"], 1)
                if mode:
                    self.assertEqual(len(events["writes"]), 1)
                    self.assertEqual(events["writes"][0]["text"], payload["toml"])
                    self.assertEqual(events["pickers"], int(bool(mode.get("fsa"))))
                    self.assertEqual(events["downloads"], 0)
                else:
                    self.assertEqual(events["downloads"], 1)
                    self.assertEqual(events["writes"], [])


if __name__ == '__main__':
    unittest.main()
