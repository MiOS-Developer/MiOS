# AI-hint: Hermetic tests for the current SSOT Zen layout: MiOS top bar above,
#   compact glass tab popout LEFT, pages centre, AI chat
#   sidebar RIGHT auto-hiding. Checks the SSOT [browser.zen].prefs, their projection into user.js by
#   mios-theme-render --web, and that user CSS / user.js lines outside the MiOS blocks survive.
# AI-related: usr/share/mios/mios.toml [browser.zen], usr/libexec/mios/mios-theme-render (apply_web),
#   usr/share/mios/theme/gecko-userChrome.css
# AI-functions: zen_layout_problems, TestZenLayoutPolicy, TestZenProjection, main
#
# MIOS_SSOT_TOML overrides the SSOT under test (default: <root>/usr/share/mios/mios.toml).

import importlib.machinery
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("MIOS_ROOT") or os.path.normpath(os.path.join(HERE, ".."))
RENDER = os.path.join(ROOT, "usr", "libexec", "mios", "mios-theme-render")
SSOT = os.environ.get("MIOS_SSOT_TOML") or os.path.join(ROOT, "usr", "share", "mios", "mios.toml")
CHROME = os.path.join(ROOT, "usr", "share", "mios", "theme", "gecko-userChrome.css")

# Current [browser.zen] contract: compact tabs left; AI chat overlays the right edge.
LAYOUT = {
    "zen.view.compact.enable-at-startup": "true",   # compact glass tab popout, as documented in SSOT
    "zen.tabs.vertical.right-side": "false",        # tabs/apps on the LEFT
    "sidebar.position_start": "false",              # AI chat sidebar on the RIGHT
    "browser.ml.chat.sidebar": "true",              # the chatbot opens in that sidebar
    "sidebar.visibility": '"hide-on-close"',        # closing chat hides the sidebar: pages fill
    "mios.zen.ai-sidebar.autohide": "true",         # MiOS userChrome: chat overlays + auto-hides
    "toolkit.legacyUserProfileCustomizations.stylesheets": "true",
    "browser.tabs.allow_transparent_browser": "true",  # glass theme kept
    "zen.widget.linux.transparency": "true",
}


def load_render():
    loader = importlib.machinery.SourceFileLoader("mios_theme_render", RENDER)
    spec = importlib.util.spec_from_loader("mios_theme_render", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def load_ssot(path=SSOT):
    with open(path, "rb") as fh:
        return tomllib.load(fh)


def user_js_prefs(text):
    return dict(re.findall(r'user_pref\("([^"]+)",\s*(.*?)\);', text))


def zen_layout_problems(prefs_js):
    """Current layout deviations in a {pref: literal} map."""
    return [f"{k}={prefs_js.get(k)!r} (want {v})" for k, v in LAYOUT.items() if prefs_js.get(k) != v]


def render_web_sources(root, host_toml=None):
    """Render only the two registered CSS surfaces from real templates and SSOT."""
    doc = load_ssot()
    targets = {}
    for name in ("web", "gecko-chrome"):
        surface = doc["dotfiles"]["registry"][name]
        template = os.path.join(root, surface["template"])
        os.makedirs(os.path.dirname(template), exist_ok=True)
        shutil.copyfile(os.path.join(ROOT, surface["template"]), template)
        targets[name] = os.path.join(root, surface["target"])
    env = {k: v for k, v in os.environ.items() if not k.startswith("MIOS_")}
    env.update(MIOS_THEME_ROOT=root, MIOS_TOML_ROOT=ROOT, MIOS_VENDOR_TOML=SSOT,
               MIOS_VENDOR_TOML_D=os.path.join(ROOT, "usr/lib/mios/mios.d"),
               MIOS_HOST_TOML=host_toml or os.path.join(root, "absent-host.toml"),
               MIOS_USER_TOML=os.path.join(root, "absent-user.toml"),
               MIOS_HOST_TOML_D=os.path.join(root, "absent-host.d"),
               MIOS_USER_TOML_D=os.path.join(root, "absent-user.d"),
               PYTHONPATH=os.path.join(ROOT, "usr/lib/mios"))
    subprocess.run([sys.executable, os.path.join(ROOT, "usr/libexec/mios/mios-dotfiles-render"),
                    "render", "web", "gecko-chrome"], env=env, check=True,
                   capture_output=True, text=True, timeout=30)
    for path in targets.values():
        if not os.path.isfile(path) or os.path.getsize(path) == 0:
            raise AssertionError(f"CSS projection missing: {path}")
    return targets


class TestZenLayoutPolicy(unittest.TestCase):
    def test_ssot_block_projects_current_layout(self):  # positive
        R = load_render()
        block = R._user_js_block(list(load_ssot()["browser"]["zen"]["prefs"]))
        self.assertEqual(zen_layout_problems(user_js_prefs(block)), [])

    def test_noncompact_left_sidebar_is_detected(self):  # negative control
        R = load_render()
        prefs = [p for p in load_ssot()["browser"]["zen"]["prefs"] if not p.startswith("zen.view.compact.enable-at-startup|")]
        prefs.append("zen.view.compact.enable-at-startup|bool|false")
        problems = zen_layout_problems(user_js_prefs(R._user_js_block(prefs)))
        self.assertTrue(any("compact.enable-at-startup" in p for p in problems), problems)

    def test_chat_on_left_is_detected(self):  # negative control
        R = load_render()
        prefs = [p.replace("sidebar.position_start|bool|false", "sidebar.position_start|bool|true")
                 for p in load_ssot()["browser"]["zen"]["prefs"]]
        self.assertTrue(zen_layout_problems(user_js_prefs(R._user_js_block(prefs))))

    def test_sidebar_toggle_state_is_not_pinned(self):
        # The foot-bar toggle (zen-expand-sidebar-button) owns expanded vs icon strip; a user.js pin
        # would undo the operator's choice on every start.
        names = {p.split("|", 1)[0] for p in load_ssot()["browser"]["zen"]["prefs"]}
        self.assertNotIn("zen.view.sidebar-expanded", names)

    def test_chrome_css_autohides_right_chat_sidebar(self):
        with open(CHROME, encoding="utf-8") as fh:
            css = fh.read()
        self.assertIn('@media -moz-pref("mios.zen.ai-sidebar.autohide")', css)
        self.assertRegex(css, r"#sidebar-box[^{]*\{[^}]*inset-inline-end:\s*0")
        self.assertRegex(css, r"#sidebar-box[^{]*:is\(:hover, :focus-within\)")
        self.assertIn("--mios-chrome-fill", css)  # glass fill


class TestZenProjection(unittest.TestCase):
    """apply_web into a throwaway profile; the operator's profiles and Zen are never touched."""

    USER_CSS = "/* operator rule */\n#urlbar { font-size: 15px !important; }\n"
    USER_JS = 'user_pref("browser.startup.page", 3);\n'

    def setUp(self):
        environment = mock.patch.dict(os.environ, {k: v for k, v in os.environ.items()
            if not k.startswith("MIOS_")}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        self.R = load_render()
        self.tmp = tempfile.TemporaryDirectory()
        root = os.path.join(self.tmp.name, "zen")
        self.profile = os.path.join(root, "abcd.Default (release)")
        os.makedirs(os.path.join(self.profile, "chrome"))
        with open(os.path.join(root, "profiles.ini"), "w") as fh:
            fh.write("[Profile0]\nName=t\nIsRelative=1\nPath=abcd.Default (release)\n")
        with open(os.path.join(self.profile, "chrome", "userChrome.css"), "w") as fh:
            fh.write(self.USER_CSS)
        with open(os.path.join(self.profile, "user.js"), "w") as fh:
            fh.write(self.USER_JS)
        merged = load_ssot()
        merged["browser"]["zen"]["profile_roots"] = [root]
        merged.setdefault("browser_ai", {})["enable"] = False
        self.merged = merged
        self.asset_root = os.path.join(self.tmp.name, "projection")
        self.sources = render_web_sources(self.asset_root)
        self.R._ROOT = self.asset_root
        patch = mock.patch.object(self.R.mios_toml, "load_merged", return_value=merged)
        patch.start()
        self.addCleanup(patch.stop)
        self.R._zen_global_menu = lambda *a, **k: []  # no flatpak override from a test

    def tearDown(self):
        self.tmp.cleanup()

    def read(self, *parts):
        with open(os.path.join(self.profile, *parts), encoding="utf-8") as fh:
            return fh.read()

    def test_projection_contains_layout_prefs(self):  # positive
        written = self.R.apply_web()
        self.assertIn(os.path.join(self.profile, "user.js"), written)
        self.assertEqual(zen_layout_problems(user_js_prefs(self.read("user.js"))), [])
        self.assertIn('mios.zen.ai-sidebar.autohide', self.read("chrome", "userChrome.css"))

    def test_generated_css_matches_ssot_and_profile(self):
        self.R.apply_web()
        for surface, profile_name in (("web", "userContent.css"), ("gecko-chrome", "userChrome.css")):
            with open(self.sources[surface], encoding="utf-8") as f:
                rendered = f.read()
            target = load_ssot()["dotfiles"]["registry"][surface]["target"]
            with open(os.path.join(ROOT, target), encoding="utf-8") as f:
                self.assertEqual(rendered, f.read(), f"{surface} artifact drift")
            self.assertNotIn("@MIOS:", rendered)
            self.assertIn(rendered.rstrip(), self.read("chrome", profile_name))
        chrome = self.read("chrome", "userChrome.css")
        zen = self.merged["browser"]["zen"]
        self.assertIn(f"height: calc(100% * {zen['compact_height_ratio']})", chrome)
        self.assertIn(f"width: {zen['ai_sidebar_width_px']}px", chrome)
        self.assertIn(f"translateX(calc(100% - {zen['ai_sidebar_peek_px']}px))", chrome)
        colors = self.R.mios_toml.colors(self.merged)
        self.assertIn(f"accent-color: {colors['accent']}", self.read("chrome", "userContent.css"))

    def test_generated_css_honors_changed_layer(self):
        overlay = os.path.join(self.tmp.name, "host.toml")
        with open(overlay, "w") as f:
            f.write("[browser.zen]\nai_sidebar_peek_px = 9\n")
        render_web_sources(self.asset_root, overlay)
        self.R.apply_web()
        chrome = self.read("chrome", "userChrome.css")
        self.assertIn("translateX(calc(100% - 9px))", chrome)
        self.assertNotIn("translateX(calc(100% - 6px))", chrome)

    def test_missing_generated_css_fails_without_installed_fallback(self):
        os.unlink(self.sources["web"])
        with self.assertRaises(FileNotFoundError):
            self.R.apply_web()

    def test_user_css_and_user_js_outside_blocks_untouched(self):  # negative: no collateral edits
        self.R.apply_web()
        css = self.read("chrome", "userChrome.css")
        self.assertTrue(css.startswith(self.USER_CSS.rstrip("\n")), css[:200])
        self.assertEqual(css.count(self.R._CSS_BEGIN), 1)
        js = self.read("user.js")
        self.assertTrue(js.startswith(self.USER_JS), js[:200])
        self.R.apply_web()  # idempotent: second pass changes nothing
        self.assertEqual(self.read("chrome", "userChrome.css"), css)
        self.assertEqual(self.read("user.js"), js)



    def enable_smart_window(self):
        self.merged["browser_ai"].update(enable=True, smart_window=True)
        self.merged["ai"].update(endpoint="http://localhost:${MIOS_PORTS_AGENT_PIPE}/v1",
                                 agent_model="MiOS AI", api_key="offline-fixture-private")
        self.merged["ports"]["agent_pipe"] = 18640

    def test_native_smart_window_uses_local_openai_contract(self):
        self.enable_smart_window()
        self.R.apply_web()
        text = self.read("user.js")
        prefs = user_js_prefs(text)
        self.assertEqual(json.loads(prefs["browser.smartwindow.customEndpoint"]),
                         "http://localhost:18640/v1")
        self.assertEqual(json.loads(prefs["browser.smartwindow.model"]), "MiOS AI")
        self.assertEqual(json.loads(prefs["browser.smartwindow.apiKey"]), "offline-fixture-private")
        self.assertEqual(json.loads(prefs["browser.smartwindow.firstrun.modelChoice"]), "0")
        self.assertEqual(json.loads(prefs["browser.smartwindow.endpoint"]), "")
        self.assertEqual(json.loads(prefs["browser.ml.chat.provider"]),
                         "http://localhost:" + str(self.merged["ports"]["open_webui"]))
        self.assertNotIn("browser.smartwindow.tos.consentTime", prefs)
        self.assertNotIn("browser.smartwindow.firstrun.hasCompleted", prefs)
        self.assertTrue(text.startswith(self.USER_JS))
        path = os.path.join(self.profile, "user.js")
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        self.R.apply_web()
        self.assertEqual(self.read("user.js"), text)

    def test_native_smart_window_process_inputs_and_private_first_write(self):
        self.enable_smart_window()
        os.unlink(os.path.join(self.profile, "user.js"))
        with mock.patch.dict(os.environ, {"MIOS_AI_ENDPOINT": "http://[::1]:19001/v1/",
                "MIOS_AI_GATEWAY_MODEL": "custom-local-agent", "MIOS_AI_KEY": 'fixture-"quoted"|key'}):
            self.R.apply_web()
        prefs = user_js_prefs(self.read("user.js"))
        self.assertEqual(json.loads(prefs["browser.smartwindow.customEndpoint"]), "http://[::1]:19001/v1")
        self.assertEqual(json.loads(prefs["browser.smartwindow.model"]), "custom-local-agent")
        self.assertEqual(json.loads(prefs["browser.smartwindow.apiKey"]), 'fixture-"quoted"|key')
        self.assertEqual(os.stat(os.path.join(self.profile, "user.js")).st_mode & 0o777, 0o600)

    def test_smart_window_invalid_or_cloud_endpoint_leaves_profile_untouched(self):
        self.enable_smart_window()
        for endpoint in ("https://example.invalid/v1", "http://127.0.0.1:0/v1",
                "http://user@localhost/v1", "http://localhost/v1?key=fixture",
                "http://localhost/chat/completions", "http://localhost:${MIOS_UNKNOWN}/v1",
                "http://localhost/v1\n"):
            with self.subTest(endpoint=endpoint):
                self.merged["ai"]["endpoint"] = endpoint
                with self.assertRaises(ValueError):
                    self.R.apply_web()
                self.assertEqual(self.read("user.js"), self.USER_JS)
                self.assertEqual(self.read("chrome", "userChrome.css"), self.USER_CSS)

    def test_smart_window_dry_run_does_not_write_credentials(self):
        self.enable_smart_window()
        paths = self.R.apply_web(dry_run=True)
        self.assertNotIn("offline-fixture-private", repr(paths))
        self.assertEqual(self.read("user.js"), self.USER_JS)
        self.assertEqual(self.read("chrome", "userChrome.css"), self.USER_CSS)

    def test_smart_window_disabled_leaves_sidebar_available(self):
        self.enable_smart_window()
        self.merged["browser_ai"]["smart_window"] = False
        self.merged["ai"]["endpoint"] = "https://example.invalid/v1"
        self.R.apply_web()
        prefs = user_js_prefs(self.read("user.js"))
        self.assertNotIn("browser.smartwindow.customEndpoint", prefs)
        self.assertNotIn("browser.smartwindow.apiKey", prefs)
        self.assertIn("browser.ml.chat.provider", prefs)

if __name__ == "__main__":
    unittest.main(verbosity=2)
