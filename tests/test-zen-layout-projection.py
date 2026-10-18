# AI-hint: Hermetic tests for the operator's Zen layout (design-2026-10-16 sketch 4): MiOS top bar above,
#   Zen tab sidebar LEFT (in-window, foot-bar toggle collapses it to an icon strip), pages centre, AI chat
#   sidebar RIGHT auto-hiding. Checks the SSOT [browser.zen].prefs, their projection into user.js by
#   mios-theme-render --web, and that user CSS / user.js lines outside the MiOS blocks survive.
# AI-related: usr/share/mios/mios.toml [browser.zen], usr/libexec/mios/mios-theme-render (apply_web),
#   usr/share/mios/theme/gecko-userChrome.css
# AI-functions: zen_layout_problems, TestZenLayoutPolicy, TestZenProjection, main
#
# MIOS_SSOT_TOML overrides the SSOT under test (default: <root>/usr/share/mios/mios.toml).

import importlib.machinery
import importlib.util
import os
import re
import sys
import tempfile
import tomllib
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("MIOS_ROOT") or os.path.normpath(os.path.join(HERE, ".."))
RENDER = os.path.join(ROOT, "usr", "libexec", "mios", "mios-theme-render")
SSOT = os.environ.get("MIOS_SSOT_TOML") or os.path.join(ROOT, "usr", "share", "mios", "mios.toml")
CHROME = os.path.join(ROOT, "usr", "share", "mios", "theme", "gecko-userChrome.css")

# Sketch 4, as Zen/Firefox prefs (Zen 1.23: sidebar.revamp is locked false, so the AI chat uses the
# legacy #sidebar-box, docked to the end by sidebar.position_start=false).
LAYOUT = {
    "zen.view.compact.enable-at-startup": "false",  # left tab sidebar sits in the window, not a hover popout
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
    """Sketch-4 deviations in a {pref: literal} map (empty list = layout honoured)."""
    return [f"{k}={prefs_js.get(k)!r} (want {v})" for k, v in LAYOUT.items() if prefs_js.get(k) != v]


class TestZenLayoutPolicy(unittest.TestCase):
    def test_ssot_block_projects_sketch_layout(self):  # positive
        R = load_render()
        block = R._user_js_block(list(load_ssot()["browser"]["zen"]["prefs"]))
        self.assertEqual(zen_layout_problems(user_js_prefs(block)), [])

    def test_floating_left_sidebar_is_detected(self):  # negative control
        R = load_render()
        prefs = [p for p in load_ssot()["browser"]["zen"]["prefs"] if not p.startswith("zen.view.compact.enable-at-startup|")]
        prefs.append("zen.view.compact.enable-at-startup|bool|true")
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
        self.R.mios_toml.load_merged = lambda *a, **k: merged
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
