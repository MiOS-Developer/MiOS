#!/usr/bin/env python3
# AI-hint: Execute the configurator's real JavaScript serializers, path banner and status card against hostile strings; validate emitted TOML with the standard parser.
# AI-related: usr/share/mios/configurator/mios.html
import json
import re
import subprocess
import time
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
for (const name of ['normalizeMonsSections', 'parseToml', 'parseTomlValue', 'emitToml', 'emitTomlValue']) {
  const match = html.match(new RegExp('^function '+name+'\\([^\\n]*\\n[\\s\\S]*?^}', 'm'));
  if (!match) throw new Error('Missing function '+name);
  vm.runInContext(match[0], ctx);
}
const toml = vm.runInContext('emitToml(sections)', ctx);
ctx.toml = toml;
const reparsed = vm.runInContext('parseToml(toml)', ctx);
process.stdout.write(JSON.stringify({toml, reparsed}));
'''


MONS_IMPORT_NODE = r'''
const fs = require('fs'), vm = require('vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const html = fs.readFileSync(input.path, 'utf8');
const ctx = {text: input.text};
vm.createContext(ctx);
for (const name of ['normalizeMonsSections','parseToml','parseTomlValue','emitToml','emitTomlValue']) {
 const match=html.match(new RegExp('^function '+name+'\\([^\\n]*\\n[\\s\\S]*?^}', 'm'));
 if(!match)throw new Error('Missing function '+name);
 vm.runInContext(match[0],ctx);
}
const parsed=vm.runInContext('parseToml(text)',ctx);
ctx.parsed=parsed;
const toml=vm.runInContext('emitToml(parsed)',ctx);
ctx.toml=toml;
process.stdout.write(JSON.stringify({parsed,toml,reparsed:vm.runInContext('parseToml(toml)',ctx)}));
'''


MON_FORM_NODE = r'''
const fs = require('fs'), vm = require('vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const html = fs.readFileSync(input.path, 'utf8');
const ctx = {DATA: input.initial, controls: input.controls, storeGpuResourceTables:()=>{},
 document:{getElementById:()=>({value:'[]'}),querySelectorAll:()=>input.controls}};
vm.createContext(ctx);
for (const name of ['normalizeMonsSections','splitDataKey','gatherFromForm','emitToml','emitTomlValue']) {
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

# The Portal's one status renderer, as served at /portal/status.js (mios.html mounts it).
STATUS_SOURCE = re.search(r'_PORTAL_STATUS_JS = r"""(.*?)"""', (ROOT / "usr/lib/mios/agent-pipe/mios_pipe/routing/portal_assets.py").read_text(encoding="utf-8"), re.S).group(1)
# As portal_status.status_js serves it: the first-poll retry cap is [portal.status].refresh_s.
STATUS_REFRESH_S = tomllib.loads((ROOT / "usr/share/mios/mios.toml").read_text())["portal"]["status"]["refresh_s"]
STATUS_JS = STATUS_SOURCE.replace("var RETRY_MAX_S=0;", f"var RETRY_MAX_S={STATUS_REFRESH_S};", 1)

STATUS_NODE = r'''
const fs = require('fs'), vm = require('vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const html = fs.readFileSync(input.path, 'utf8');
const events = {fetch: [], timers: [], innerHTML: [], scripts: []};
function node(tag) {
  const n = {tag, children: [], className: '', style: {}, _t: '', appendChild(c) { this.children.push(c); return c; }};
  Object.defineProperty(n, 'textContent', {get() { return this._t + this.children.map(c => c.textContent).join(''); },
    set(v) { this._t = String(v); this.children = []; }});
  Object.defineProperty(n, 'innerHTML', {get() { return ''; }, set(v) { events.innerHTML.push(String(v)); }});
  return n;
}
const ids = {'grp-status': node('section'), 'status-grid': node('div'), 'status-note': node('span'), 'v-meta': node('span')};
ids['grp-status'].style.display = 'none';
const head = node('head');
// A <script> appended to <head> is "loaded": the Portal's real /portal/status.js runs in this
// same context (or fails to load, like the miosd config server's 404), then onload fires.
head.appendChild = script => { events.scripts.push(script.src);
  if (input.scriptLoads === false) { if (script.onerror) script.onerror(); return script; }
  vm.runInContext(input.statusJs, ctx); if (script.onload) script.onload(); return script; };
const ctx = {MIOS_EMBEDDED: input.embedded !== false, DATA: input.data, Date, Math, String, JSON,
  document: {hidden: false, head, getElementById: id => ids[id] || null, createElement: node, addEventListener() {}},
  setTimeout: (fn, ms) => { events.timers.push(ms); return 1; }, clearTimeout() {},
  fetch: async (url, options) => {
    events.fetch.push({url, options});
    if (input.networkError) throw new Error('transport interrupted');
    const status = input.status || 200;
    return {ok: status >= 200 && status < 300, status,
      json: async () => { if (input.badJson) throw new SyntaxError('bad response JSON'); return input.body; }};
  }};
ctx.window = ctx;
vm.createContext(ctx);
for (const name of ['renderVersionBanner', 'mountStatus', 'normalizeMonsSections', 'emitToml', 'emitTomlValue']) {
  const match = html.match(new RegExp('^function ' + name + '\\([^\\n]*\\n[\\s\\S]*?^}', 'm'));
  if (!match) throw new Error('Missing function ' + name);
  vm.runInContext(match[0], ctx);
}
const snapshot = () => vm.runInContext('JSON.stringify(DATA) + "\\u0000" + emitToml(DATA)', ctx);
(async () => {
  const before = snapshot();
  vm.runInContext('mountStatus()', ctx);
  for (let i = 0; i < 6; i++) await new Promise(r => setImmediate(r));
  const after = snapshot();
  vm.runInContext('renderVersionBanner()', ctx);
  const grid = ids['status-grid'];
  process.stdout.write(JSON.stringify({events, shown: ids['grp-status'].style.display, text: grid.textContent,
    note: ids['status-note'].textContent, opacity: grid.style.opacity || '', banner: ids['v-meta'].textContent,
    unchanged: before === after, cardClass: grid.children.length ? grid.children[0].className : null}));
})().catch(error => { console.error(error); process.exitCode = 1; });
'''

STATUS_FIXTURE = {
    "object": "mios.portal.status", "schema_version": "1", "ts": 1, "refresh_s": 15, "stale_after_s": 120,
    "node": "blade-a",
    "nodes": [{"name": "blade-a", "local": True, "state": "ok", "mios_version": "0.3.0", "image_ref": None,
               "image_branch": "main", "profile": "full", "os_version": None, "git_head": "0" * 40,
               "git_branch": "main", "git_dirty": 0, "bootc_state": "not-bootc", "booted_image": None,
               "booted_digest": None, "staged_image": None, "rollback_image": None, "spec_age_s": 3}],
    "agents": {"state": "ok", "age_s": 1, "total": 1, "online": 1, "pending": 0, "sources": [],
               "rows": [{"agent_id": "a", "kind": "claude", "label": "<img src=x onerror=1>", "online": True,
                         "pending": 0, "parent_agent_id": None, "node": None, "session_name": None,
                         "source": "local", "subagent": False}]},
    "goal": {"path": ".devloop/goal_state.json", "objective": "goal", "stopping_condition": "stop",
             "status": "IN_PROGRESS", "created_at": None, "completed_at": None, "evaluated": "recorded",
             "criteria": [{"id": "C-01", "criterion_type": "test", "description": "d", "passed": None,
                           "details": None}], "non_goals": []},
    "goals": [{"path": ".devloop/GOALS.md", "title": "T", "conditions": [{"id": "SC-1", "condition": "c"}]}],
    "tasks": {"state": "ok", "total": 1, "by_status": {"pending": 1, "in_progress": 0, "completed": 0,
              "incomplete": 0, "cancelled": 0, "other": 0}, "overrides": 0, "invalid_lines": 0,
              "in_progress": [], "ready": []},
    "services": [{"name": "agent-pipe", "unit": "mios-agent-pipe.service", "active": "active"}],
    "sources": [{"name": "agents", "state": "ok", "detail": "snapshot fresh", "age_s": 1}]}


class _DataKeys(HTMLParser):
    """data-key attributes inside the #grp-status section."""
    def __init__(self):
        super().__init__()
        self.depth, self.keys = 0, []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if self.depth or a.get("id") == "grp-status":
            if tag == "section":
                self.depth += 1
            if "data-key" in a:
                self.keys.append(a["data-key"])

    def handle_endtag(self, tag):
        if self.depth and tag == "section":
            self.depth -= 1


class TestConfiguratorSecurity(unittest.TestCase):

    def test_mons_import_normalizes_flat_subtables_and_array_tables_before_form_hydration(self):
        html = ROOT / "usr/share/mios/configurator/mios.html"
        text = """[tamagotchi]
zoom_percent=125
unknown='kept'
[tamagotchi.camera]
pan_step_permille=50
follow_half_life_ms=500
[[tamagotchi.characters]]
id=17
name='Previous'
[mons]
zoom_percent=150
[mons.camera]
pan_step_permille=75
[[mons.characters]]
id=18
name='Canonical'
[operator]
filename='my-tamagotchi-save.json'
"""
        run = subprocess.run(["node", "-e", MONS_IMPORT_NODE],
            input=json.dumps({"path": str(html), "text": text}), capture_output=True, text=True, check=True, timeout=15)
        output = json.loads(run.stdout)
        self.assertEqual(output["parsed"], output["reparsed"])
        self.assertFalse(any(key == "tamagotchi" or key.startswith("tamagotchi.") for key in output["parsed"]))
        self.assertEqual(output["parsed"]["mons"]["zoom_percent"], 150)
        self.assertEqual(output["parsed"]["mons"]["unknown"], "kept")
        self.assertEqual(output["parsed"]["mons"]["characters"], [{"id": 18, "name": "Canonical"}])
        self.assertEqual(output["parsed"]["mons.camera"], {"pan_step_permille": 75, "follow_half_life_ms": 500})
        self.assertEqual(tomllib.loads(output["toml"])["operator"]["filename"], "my-tamagotchi-save.json")

    def test_mons_import_preserves_empty_inheritance_and_only_converts_typed_references(self):
        html = ROOT / "usr/share/mios/configurator/mios.html"
        text = """[tamagotchi]
zoom_percent=125
palette=['one','two']
[mons]
zoom_percent=''
palette=[]
[terminal.popups]
menu_width_source='tamagotchi.popup_min_width'
operator_source='tamagotchi.keep_this_text'
[rust.categories.tamagotchi]
binary='mios-tamagotchi'
replaces=['/custom/mios-tamagotchi']
[rust.categories.roles]
apps=['apps','tamagotchi']
[generation.walk]
excluded_sections=['theme','tamagotchi']
[build.native.categories.apps]
binaries=['mios-launch','mios-tamagotchi']
[operator.launcher]
argv=['/custom/mios-tamagotchi']
[keybindings.global.controller.nav.apps.mon]
exe=['/usr/bin/mios-tamagotchi','/custom/mios-tamagotchi']
user_exe=['/home/operator/.local/bin/mios-tamagotchi']
"""
        run = subprocess.run(["node", "-e", MONS_IMPORT_NODE],
            input=json.dumps({"path": str(html), "text": text}), capture_output=True, text=True, check=True, timeout=15)
        output = json.loads(run.stdout)
        parsed = output["parsed"]
        self.assertEqual(parsed["mons"], {"zoom_percent":125, "palette":[]})
        self.assertEqual(parsed["terminal.popups"]["menu_width_source"], "mons.popup_min_width")
        self.assertEqual(parsed["terminal.popups"]["operator_source"], "tamagotchi.keep_this_text")
        self.assertEqual(parsed["rust.categories.mons"]["binary"], "mios-mons")
        self.assertEqual(parsed["rust.categories.mons"]["replaces"], ["/custom/mios-tamagotchi"])
        self.assertEqual(parsed["rust.categories.roles"]["apps"], ["apps","mons"])
        self.assertEqual(parsed["generation.walk"]["excluded_sections"], ["theme","mons"])
        self.assertEqual(parsed["build.native.categories.apps"]["binaries"], ["mios-launch","mios-mons"])
        self.assertEqual(parsed["operator.launcher"]["argv"], ["/custom/mios-tamagotchi"])
        self.assertEqual(parsed["keybindings.global.controller.nav.apps.mon"]["exe"], ["/usr/bin/mios-mons", "/custom/mios-tamagotchi"])
        self.assertEqual(parsed["keybindings.global.controller.nav.apps.mon"]["user_exe"], ["/home/operator/.local/bin/mios-tamagotchi"])
        self.assertEqual(output["parsed"], output["reparsed"])

    def test_focus_gesture_controls_roundtrip_and_reject_invalid_settings(self):
        html = ROOT / "usr/share/mios/configurator/mios.html"
        section = "keybindings.global.controller.nav.focus_gesture"
        class Controls(HTMLParser):
            def __init__(self):
                super().__init__()
                self.by_key = {}
                self.attrs = {}
            def handle_starttag(self, tag, attrs):
                a = dict(attrs)
                key = a.get("data-key", "")
                if tag == "input" and key.startswith(section + "."):
                    name = key.rsplit(".", 1)[1]
                    if name in self.by_key:
                        raise AssertionError("Duplicate focus gesture control: " + name)
                    self.attrs[name] = a
                    self.by_key[name] = {"tagName": "INPUT", "type": a.get("type", ""), "value": "",
                        "dataset": {"key": key, "inherit": a.get("data-inherit"), "type": a.get("data-type")}}
        parser = Controls()
        parser.feed(html.read_text())
        vendor = tomllib.loads((ROOT / "usr/share/mios/mios.toml").read_text())
        vendor = vendor["keybindings"]["global"]["controller"]["nav"]["focus_gesture"]
        self.assertEqual(set(parser.by_key), set(vendor), "expose exactly the native table's seven fields")
        self.assertEqual(len(parser.by_key), 7)
        for key, attrs in parser.attrs.items():
            self.assertEqual(attrs["data-type"], "float" if key.endswith("_pct") else "int", key)
            self.assertEqual(attrs["data-inherit"], "true", key)
            self.assertNotIn("value", attrs, "HTML must not duplicate vendor defaults")
        def gather(initial=None):
            return subprocess.run(["node", "-e", MON_FORM_NODE],
                input=json.dumps({"path": str(html), "controls": list(parser.by_key.values()), "initial": initial or {}}),
                capture_output=True, text=True, timeout=15)
        self.assertEqual(json.loads(gather().stdout)["data"], {})
        settings = {"neutral_pct": "0.2", "neutral_ms": "100", "rise_pct": "0.95", "rise_ms": "90",
                    "direction_deg": "15", "return_ms": "200", "cooldown_ms": "0"}
        for key, value in settings.items():
            parser.by_key[key]["value"] = value
        result = gather()
        self.assertEqual(result.returncode, 0, result.stderr)
        table = tomllib.loads(json.loads(result.stdout)["toml"])["keybindings"]["global"]["controller"]["nav"]["focus_gesture"]
        self.assertEqual(table, {key: float(value) if key.endswith("_pct") else int(value) for key, value in settings.items()})
        for key, values in {
            "neutral_pct": ["0", "0.51", "NaN", "Infinity", "0.2oops"],
            "rise_pct": ["0", "1.01", "NaN", "Infinity", "0.95oops"],
            "neutral_ms": ["0", "2001", "1.5", "80ms", "1e2"],
            "rise_ms": ["0", "2001", "1.5", "120ms"],
            "direction_deg": ["0", "46", "1.5", "20deg"],
            "return_ms": ["0", "2001", "1.5", "250ms"],
            "cooldown_ms": ["-1", "5001", "1.5", "400ms"],
        }.items():
            for value in values:
                with self.subTest(key=key, value=value):
                    parser.by_key[key]["value"] = value
                    result = gather()
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("Focus gesture setting outside supported range", result.stderr)
            parser.by_key[key]["value"] = settings[key]
        for value in ("0.1", "0.2"):
            parser.by_key["rise_pct"]["value"] = value
            result = gather()
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("peak must exceed neutral", result.stderr)
        for control in parser.by_key.values():
            control["value"] = ""
        retained = {section: {"neutral_pct": 0.3, "other": "preserved"}}
        result = gather(retained)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["data"], {section: {"other": "preserved"}})

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
        for key in ["mons.camera_view", "mons.zoom_percent",
                    "mons.game.training.name_starts", "mons.game.training.species_colors",
                    "mons.game.training.personality_colors.cozy",
                    "mons.camera.pan_step_permille",
                    "mons.camera.preview_orbit_pauses_turntable"]:
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
        self.assertNotIn("mons", tomllib.loads(output["toml"]))
        changes = {"mons.camera_view": "face", "mons.zoom_percent": "175",
            "mons.game.training.name_starts": "Yu, Ko",
            "mons.game.training.species_colors": "4,8,14,15,14,11,7,1",
            "mons.game.training.personality_colors.cozy": "3",
            "mons.camera.pan_step_permille": "50"}
        for key, value in changes.items():
            by_key[key]["value"] = value
        output = gather({})
        parsed = tomllib.loads(output["toml"])["mons"]
        self.assertEqual(parsed["camera_view"], "face")
        self.assertEqual(parsed["zoom_percent"], 175)
        self.assertEqual(parsed["game"]["training"]["name_starts"], ["Yu", "Ko"])
        self.assertEqual(parsed["game"]["training"]["species_colors"], [4,8,14,15,14,11,7,1])
        self.assertEqual(parsed["game"]["training"]["personality_colors"]["cozy"], 3)
        self.assertEqual(parsed["camera"]["pan_step_permille"], 50)
        self.assertNotIn("preview_orbit_pauses_turntable", parsed["camera"])
        # The camera flag is a typed, inheritable bool select. (Bool emission
        # is the client's job; gatherFromForm owns that coercion.)
        flag = by_key["mons.camera.preview_orbit_pauses_turntable"]
        self.assertEqual((flag["tagName"], flag["dataset"].get("type")), ("SELECT", "bool"))
        for value, expected in [("true", True), ("false", False)]:
            flag["value"] = value
            typed = tomllib.loads(gather({})["toml"])["mons"]["camera"]
            self.assertIs(typed["preview_orbit_pauses_turntable"], expected)
        flag["value"] = "not-a-bool"
        with self.assertRaises(subprocess.CalledProcessError) as invalid:
            gather({})
        self.assertIn("Boolean setting requires true or false", invalid.exception.stderr)
        flag["value"] = ""
        for key in ["pan_step_permille", "pan_max_permille", "pan_margin_permille",
                    "tilt_step_degrees", "tilt_min_degrees", "tilt_max_degrees",
                    "orbit_fine_step_degrees", "max_lead_steps"]:
            self.assertEqual(by_key["mons.camera." + key]["dataset"].get("type"), "int", key)
        # Choosing Inherit for an existing key removes that override only.
        for control in parser.controls:
            control["value"] = ""
        output = gather({"mons": {"camera_view": "room", "unrelated": "kept"}})
        self.assertEqual(output["data"]["mons"], {"unrelated": "kept"})

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
            "apps.mon.exe": "/usr/bin/mios-mons",
            "apps.mon.user_exe": "/home/fixture/.local/bin/mios-mons-sample",
            "apps.mon.contexts.terminal": "mon", "apps.mon.contexts.console": "mon_console",
            "mon_tmux.LS_UP": "key:ALT+8", "mon_console_plain.RS_LEFT": "none",
            "apps.mon.repeat.LS_UP": "220, 80"}
        for key, value in changes.items():
            by_key[prefix + key]["value"] = value
        emitted = tomllib.loads(gather({})["toml"])["keybindings"]["global"]["controller"]["nav"]
        self.assertEqual(emitted["app_poll_ms"], 300)
        self.assertEqual(emitted["app_query_timeout_ms"], 100)
        self.assertEqual(emitted["apps"]["mon"]["exe"], ["/usr/bin/mios-mons"])
        self.assertEqual(emitted["apps"]["mon"]["user_exe"],
                         ["/home/fixture/.local/bin/mios-mons-sample"])
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

    def test_controller_timing_bounds_and_repeat_pairs_use_the_real_form(self):
        html = ROOT / "usr/share/mios/configurator/mios.html"
        prefix = "keybindings.global.controller.nav."
        class Controls(HTMLParser):
            def __init__(self):
                super().__init__()
                self.by_key = {}
                self.bounds = {}
            def handle_starttag(self, tag, attrs):
                a = dict(attrs)
                key = a.get("data-key", "")
                if tag == "input" and key.startswith(prefix) and (
                        key in (prefix + "app_poll_ms", prefix + "app_query_timeout_ms")
                        or key.startswith(prefix + "apps.mon.repeat.")):
                    self.bounds[key] = (a.get("min"), a.get("max"))
                    self.by_key[key] = {"tagName": "INPUT", "type": a.get("type", ""),
                        "value": "", "dataset": {"key": key, "inherit": "true",
                            **({"type": a["data-type"]} if "data-type" in a else {}),
                            **({"array": a["data-array"]} if "data-array" in a else {})}}
        parser = Controls()
        parser.feed(html.read_text())
        poll, query = prefix + "app_poll_ms", prefix + "app_query_timeout_ms"
        self.assertEqual(parser.bounds[poll], ("50", "2000"))
        self.assertEqual(parser.bounds[query], ("20", "1000"))
        self.assertEqual(len(parser.by_key), 12)
        def gather():
            return subprocess.run(["node", "-e", MON_FORM_NODE],
                input=json.dumps({"path": str(html), "controls": list(parser.by_key.values()), "initial": {}}),
                capture_output=True, text=True, timeout=15)
        def reject(key, value, error):
            with self.subTest(key=key, value=value):
                old = parser.by_key[key]["value"]
                parser.by_key[key]["value"] = value
                try:
                    result = gather()
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(error, result.stderr)
                finally:
                    parser.by_key[key]["value"] = old
        for key, values in [(poll, ["49", "2001", "50.5", "50ms", "-1", "NaN"]),
                            (query, ["19", "1001", "20.5", "20ms", "-1", "Infinity"])]:
            for value in values:
                reject(key, value, "Controller timing outside supported range")
        for key in parser.by_key:
            if ".repeat." not in key:
                continue
            for value in ["49,16", "5001,16", "50,15", "50,5001", "50.5,16", "50ms,16",
                          "50", "50,16,20", "50,,16", "50,-16"]:
                reject(key, value, "Controller repeat requires delay")
            for value in ["50,16", "5000,5000"]:
                parser.by_key[key]["value"] = value
                result = gather()
                self.assertEqual(result.returncode, 0, result.stderr)
                emitted = tomllib.loads(json.loads(result.stdout)["toml"])
                self.assertEqual(emitted["keybindings"]["global"]["controller"]["nav"]["apps"]["mon"]["repeat"][key.rsplit(".", 1)[1]],
                                 [int(v) for v in value.split(",")])
            parser.by_key[key]["value"] = ""
        for p, q in [("50", "20"), ("2000", "1000")]:
            parser.by_key[poll]["value"], parser.by_key[query]["value"] = p, q
            result = gather()
            self.assertEqual(result.returncode, 0, result.stderr)
        parser.by_key[poll]["value"] = "50"
        reject(query, "51", "query timeout must not exceed poll interval")
        for control in parser.by_key.values():
            control["value"] = ""
        self.assertEqual(json.loads(gather().stdout)["data"], {})

    def test_smooth_camera_settings_use_real_inheritable_typed_controls(self):
        html = ROOT / "usr/share/mios/configurator/mios.html"
        prefix = "mons.camera."
        limits = {
            "follow_half_life_ms": (90, 20, 1000), "motion_frame_ms": (33, 16, 140),
            "orbit_rate_deg_s": (180, 10, 720), "tilt_rate_deg_s": (90, 5, 360),
            "pan_rate_permille_s": (600, 50, 2000), "zoom_rate_percent_s": (300, 25, 800),
            "reset_glide_ms": (350, 0, 2000), "rest_epsilon_permille": (2, 1, 50),
        }
        class Controls(HTMLParser):
            def __init__(self):
                super().__init__()
                self.by_key = {}
                self.bounds = {}
            def handle_starttag(self, tag, attrs):
                a = dict(attrs)
                key = a.get("data-key", "")
                if tag == "input" and (key in {prefix + k for k in limits} or key == "mons.animation_frame_ms"):
                    self.by_key[key] = {"tagName": "INPUT", "type": a.get("type", ""),
                        "value": "", "dataset": {"key": key, "inherit": a.get("data-inherit"),
                        "type": a.get("data-type")}}
                    self.bounds[key] = (a.get("min"), a.get("max"))
        parser = Controls()
        parser.feed(html.read_text())
        self.assertEqual(len(parser.by_key), 9)
        def gather(initial=None):
            return subprocess.run(["node", "-e", MON_FORM_NODE],
                input=json.dumps({"path": str(html), "controls": list(parser.by_key.values()),
                                  "initial": initial or {}}),
                capture_output=True, text=True, timeout=15)
        for key, (default, lo, hi) in limits.items():
            control = parser.by_key[prefix + key]
            self.assertEqual(control["dataset"]["inherit"], "true")
            self.assertEqual(control["dataset"]["type"], "int")
            self.assertEqual(parser.bounds[prefix + key], (str(lo), str(hi)))
            control["value"] = str(default)
        result = gather({"identity": {"hostname": "kept"}})
        self.assertEqual(result.returncode, 0, result.stderr)
        parsed = tomllib.loads(json.loads(result.stdout)["toml"])
        self.assertEqual(parsed["mons"]["camera"], {k: v[0] for k, v in limits.items()})
        self.assertEqual(parsed["identity"]["hostname"], "kept")
        for key, (_, lo, hi) in limits.items():
            control = parser.by_key[prefix + key]
            saved = control["value"]
            for invalid in [str(lo - 1), str(hi + 1), str(lo) + ".5", str(lo) + "ms"]:
                with self.subTest(key=key, value=invalid):
                    try:
                        control["value"] = invalid
                        result = gather()
                        self.assertNotEqual(result.returncode, 0)
                        self.assertIn("Camera setting outside supported range", result.stderr)
                    finally:
                        control["value"] = saved
        try:
            parser.by_key[prefix + "motion_frame_ms"]["value"] = "100"
            parser.by_key["mons.animation_frame_ms"]["value"] = "40"
            result = gather()
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("motion interval must not exceed", result.stderr)
        finally:
            parser.by_key[prefix + "motion_frame_ms"]["value"] = "33"
            parser.by_key["mons.animation_frame_ms"]["value"] = ""
        try:
            parser.by_key[prefix + "follow_half_life_ms"]["value"] = "200"
            parser.by_key[prefix + "reset_glide_ms"]["value"] = "100"
            result = gather()
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("half-time must not exceed", result.stderr)
            parser.by_key[prefix + "reset_glide_ms"]["value"] = "0"
            result = gather()
            self.assertEqual(result.returncode, 0, result.stderr)
        finally:
            for control in parser.by_key.values():
                control["value"] = ""
        result = gather({"mons.camera": {"follow_half_life_ms": 90, "unrelated": "kept"}})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["data"]["mons.camera"], {"unrelated": "kept"})

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


    def _status_case(self, **case):
        payload = {"path": str(ROOT / "usr/share/mios/configurator/mios.html"), "body": STATUS_FIXTURE,
                   "statusJs": STATUS_JS,
                   "data": {"meta": {"schema_version": "1.1.0", "mios_version": "9.9.9"},
                            "custom": {"kept": "value"}}, **case}
        result = subprocess.run(["node", "-e", STATUS_NODE], input=json.dumps(payload), capture_output=True,
                                text=True, check=True, timeout=15)
        return json.loads(result.stdout)

    def test_status_card_mounts_the_portal_renderer_and_renders_text_only(self):
        output = self._status_case(body=dict(STATUS_FIXTURE, ts=int(time.time())))
        events = output["events"]
        self.assertEqual(events["scripts"], ["/portal/status.js"], "the one renderer is the Portal's status.js")
        self.assertEqual([(e["url"], e["options"]) for e in events["fetch"]],
                         [("/portal/status", {"credentials": "same-origin", "cache": "no-store"})])
        self.assertEqual(output["shown"], "")
        self.assertEqual(output["cardClass"].split()[:2], ["card", "status-card"], "mounted with this page's classes")
        self.assertIn("<img src=x onerror=1>", output["text"], "a hostile label is written as text")
        self.assertIn("C-01not evaluated", output["text"], "a never-evaluated criterion is not a failure")
        self.assertEqual(events["innerHTML"], [], "innerHTML never receives fixture data")
        self.assertEqual(events["timers"], [STATUS_FIXTURE["refresh_s"] * 1000], "cadence comes from the payload")
        self.assertNotIn("stale since", output["note"])
        self.assertTrue(output["unchanged"], "DATA and emitToml(DATA) are untouched by the status card")

    def test_status_card_marks_an_old_payload_stale(self):
        output = self._status_case()  # STATUS_FIXTURE.ts is 1: far older than stale_after_s
        self.assertIn("stale since", output["note"])
        self.assertEqual(output["opacity"], "0.5")

    def test_status_card_stays_hidden_on_any_other_answer(self):
        self.assertEqual(STATUS_SOURCE.count("var RETRY_MAX_S=0;"), 1, "the served retry cap has one placeholder")
        cases = [{"embedded": False}, {"scriptLoads": False}, {"status": 404}, {"status": 401},
                 {"networkError": True}, {"badJson": True}, {"body": {"object": "something.else"}}]
        for case in cases:
            with self.subTest(case):
                output = self._status_case(**case)
                self.assertEqual(output["shown"], "none")
                quiet = case.get("embedded") is False or case.get("scriptLoads") is False
                # A failed first poll schedules one bounded retry (1 s, doubling to refresh_s); file:// none.
                self.assertEqual(output["events"]["timers"], [] if quiet else [1000])
                self.assertTrue(output["unchanged"])
                self.assertEqual(len(output["events"]["fetch"]), 0 if quiet else 1)
                self.assertEqual(output["events"]["scripts"], [] if case.get("embedded") is False
                                 else ["/portal/status.js"], "file:// loads nothing")

    def test_status_section_carries_no_config_keys_and_banner_reads_meta(self):
        html = (ROOT / "usr/share/mios/configurator/mios.html").read_text()
        parser = _DataKeys()
        parser.feed(html)
        self.assertEqual(parser.keys, [])
        planted = _DataKeys()  # negative control: the detector sees a key planted in the section
        planted.feed(html.replace('<div class="card-grid" id="status-grid">',
                                  '<div class="card-grid" id="status-grid"><input data-key="portal.status.planted">', 1))
        self.assertEqual(planted.keys, ["portal.status.planted"])
        self.assertIn('id="v-meta"></span>', html, "the banner is no longer a literal")
        refresh = html[html.index("function refreshFromData() {"):]
        self.assertIn("renderVersionBanner();", refresh[:refresh.index("\n}\n")])
        self.assertIn("9.9.9", self._status_case(embedded=False)["banner"])
        for key in ("refresh_s", "cache_s", "stale_after_s", "task_rows", "agent_rows", "goal_state", "goal_docs"):
            self.assertIn(f'data-key="portal.status.{key}"', html)
        for key in ("snapshot_dir", "repo_root", "probe_timeout_s", "request_timeout_s"):  # host constants
            self.assertNotIn(f'data-key="portal.status.{key}"', html)
        self.assertNotIn("function renderStatus(", html, "one renderer: mios.html has no copy of its own")


    def test_status_card_unserved_script_retries_nothing(self):
        """Negative control: the retry above comes from the served cap; the unserved source stops."""
        output = self._status_case(networkError=True, statusJs=STATUS_SOURCE)
        self.assertEqual(output["events"]["timers"], [])

    def test_portal_status_cadence_relations_are_validated(self):
        """gatherFromForm refuses the [portal.status] values portal_spec._cfg would fail closed on."""
        html = ROOT / "usr/share/mios/configurator/mios.html"
        vendor = tomllib.loads((ROOT / "usr/share/mios/mios.toml").read_text())
        status = vendor["portal"]["status"]

        class Controls(HTMLParser):
            def __init__(self):
                super().__init__()
                self.by_key = {}

            def handle_starttag(self, tag, attrs):
                a = dict(attrs)
                key = a.get("data-key", "")
                if tag == "input" and key.startswith("portal.status."):
                    self.by_key[key] = {"tagName": "INPUT", "type": a.get("type", ""), "value": "",
                                        "dataset": {"key": key, "inherit": a.get("data-inherit"),
                                                    "type": a.get("data-type")}}
        parser = Controls()
        parser.feed(html.read_text())
        initial = {"mcp.agents.observation": dict(vendor["mcp"]["agents"]["observation"]),
                   'units."mios-podman-ps.timer".Timer': dict(vendor["units"]["mios-podman-ps.timer"]["Timer"]),
                   'units."mios-podman-ps.service".Service': {"TimeoutStopSec": vendor["units"]["mios-podman-ps.service"]["Service"]["TimeoutStopSec"]},
                   "portal.status": {"probe_timeout_s": status["probe_timeout_s"], "request_timeout_s": status["request_timeout_s"]}}

        def gather(values, data=None):
            for key, control in parser.by_key.items():
                control["value"] = str(values.get(key.rsplit(".", 1)[1], ""))
            return subprocess.run(["node", "-e", MON_FORM_NODE],
                                  input=json.dumps({"path": str(html), "controls": list(parser.by_key.values()),
                                                    "initial": data or json.loads(json.dumps(initial))}),
                                  capture_output=True, text=True, timeout=15)
        vendor_values = {k: status[k] for k in ("refresh_s", "cache_s", "stale_after_s", "task_rows", "agent_rows")}
        result = gather(vendor_values)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(tomllib.loads(json.loads(result.stdout)["toml"])["portal"]["status"]["agent_rows"],
                         status["agent_rows"])
        refused = [({"cache_s": status["refresh_s"]}, "cache must be shorter than the poll interval"),
                   ({"refresh_s": 3 * status["request_timeout_s"]}, "request probes"),
                   ({"stale_after_s": vendor["mcp"]["agents"]["observation"]["refresh_s"]}, "agent observation interval"),
                   ({"stale_after_s": 15, "cache_s": 0}, "spec snapshot interval")]
        for change, message in refused:
            with self.subTest(change=change):
                result = gather({**vendor_values, **change})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
        data = json.loads(json.dumps(initial))
        data["portal.status"]["probe_timeout_s"] = 30
        result = gather(vendor_values, data)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("writer probes", result.stderr)


if __name__ == '__main__':
    unittest.main()
