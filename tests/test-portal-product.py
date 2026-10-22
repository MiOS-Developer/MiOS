#!/usr/bin/env python3
# AI-hint: Hermetic positive and negative controls for the MiOS-Portal SSOT spec (mp-01..03): the [desktop.portal.gecko] table renders every src/mios-portal product file through [dotfiles.registry.portal-*]; the portal-product gate rejects a pinned version, a literal colour or port, an unregistered file, a missing Gecko linkage exception and a phase header that disagrees with [build.phases]; the stager fills @BUILD sentinels only in the build copy.
# AI-related: usr/share/mios/mios.toml, usr/libexec/mios/mios-dotfiles-render, usr/libexec/mios/mios-portal-tree, tools/drift-checks.py, automation/67-bake-portal.sh
# AI-functions: Root, TestRender, TestGate, TestStage
"""Every case runs in a throwaway copy of the files it needs; the repository is only read."""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("MIOS_ROOT") or os.path.normpath(os.path.join(HERE, ".."))
TREE = "src/mios-portal"
NEEDED = [
    "usr/share/mios/mios.toml",
    "usr/lib/mios/mios_toml.py",
    "usr/libexec/mios/mios-dotfiles-render",
    "usr/libexec/mios/mios-portal-tree",
    "usr/share/mios/portal/icon-512.png",
    "tools/drift-checks.py",
    "automation/67-bake-portal.sh",
    "usr/bin/mios-portal",
]
SURFACES = ["portal-surfer", "portal-mozconfig-common", "portal-mozconfig-linux", "portal-lane-artifact",
            "portal-lane-full", "portal-branding-prefs", "portal-chrome", "portal-policies", "portal-prefs",
            "portal-autoconfig"]


class Root:
    """A minimal copy of the repo: SSOT, renderer, stager, gate, portal templates and product tree."""

    def __init__(self):
        self.path = tempfile.mkdtemp(prefix="mios-portal-test-")
        for rel in NEEDED:
            dst = os.path.join(self.path, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(os.path.join(REPO, rel), dst)
        tdir = "usr/share/mios/theme/templates"
        os.makedirs(os.path.join(self.path, tdir))
        for fn in os.listdir(os.path.join(REPO, tdir)):
            shutil.copy2(os.path.join(REPO, tdir, fn), os.path.join(self.path, tdir, fn))
        shutil.copytree(os.path.join(REPO, TREE), os.path.join(self.path, TREE))

    def close(self):
        shutil.rmtree(self.path, ignore_errors=True)

    def p(self, rel):
        return os.path.join(self.path, rel)

    def read(self, rel):
        with open(self.p(rel), encoding="utf-8") as fh:
            return fh.read()

    def write(self, rel, text):
        os.makedirs(os.path.dirname(self.p(rel)), exist_ok=True)
        with open(self.p(rel), "w", encoding="utf-8") as fh:
            fh.write(text)

    def edit(self, rel, old, new):
        text = self.read(rel)
        assert text.count(old) == 1, (rel, old, text.count(old))
        self.write(rel, text.replace(old, new))

    def env(self):
        env = {k: v for k, v in os.environ.items() if k not in ("MIOS_TOML", "MIOS_VENDOR_TOML", "MIOS_VENDOR_TOML_D")}
        env.update(MIOS_THEME_ROOT=self.path, MIOS_TOML_ROOT=self.path, MIOS_DRIFT_ROOT=self.path,
                   MIOS_VENDOR_TOML=self.p("usr/share/mios/mios.toml"),
                   MIOS_HOST_TOML="/nonexistent.toml", MIOS_USER_TOML="/nonexistent.toml")
        return env

    def run(self, *argv):
        return subprocess.run([sys.executable, "-I", *argv], capture_output=True, text=True, env=self.env(),
                              cwd=self.path, timeout=120)

    def render(self, *names):
        return self.run(self.p("usr/libexec/mios/mios-dotfiles-render"), "render", *(names or SURFACES))

    def check(self):
        return self.run(self.p("usr/libexec/mios/mios-dotfiles-render"), "check", *SURFACES)

    def gate(self):
        return self.run(self.p("tools/drift-checks.py"), "portal-product")

    def ssot(self):
        with open(self.p("usr/share/mios/mios.toml"), "rb") as fh:
            return tomllib.load(fh)


class Case(unittest.TestCase):
    def setUp(self):
        self.root = Root()
        self.addCleanup(self.root.close)

    def assertOk(self, proc):
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def assertFails(self, proc, needle):
        self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn(needle, proc.stdout + proc.stderr)


class TestRender(Case):
    def test_positive_committed_tree_is_the_ssot_projection(self):
        self.assertOk(self.root.check())

    def test_positive_render_fills_identity_urls_and_layout_from_the_ssot(self):
        self.assertOk(self.root.render())
        d = self.root.ssot()
        g, port = d["desktop"]["portal"]["gecko"], d["ports"]["agent_pipe"]
        surfer = json.loads(self.root.read(f"{TREE}/surfer.json"))
        self.assertEqual((surfer["name"], surfer["appId"], surfer["binaryName"]), (g["name"], g["app_id"], g["binary"]))
        self.assertEqual(surfer["version"]["version"], "@BUILD:firefox_version@")
        self.assertEqual(list(surfer["brands"]), [g["brand"]])
        self.assertEqual(surfer["brands"][g["brand"]]["backgroundColor"].upper(), d["colors"]["bg"].upper())
        pol = json.loads(self.root.read(f"{TREE}/install/distribution/policies.json"))["policies"]
        self.assertIs(pol["DisableAppUpdate"], True)
        self.assertEqual(pol["Homepage"]["URL"], f"http://localhost:{port}/")
        self.assertEqual(pol["Preferences"]["browser.ml.chat.provider"]["Value"], f"http://127.0.0.1:{port}/v1")
        for off in ("browser.smartwindow.enabled", "browser.ml.enable", "browser.ml.linkPreview.enabled"):
            self.assertIs(pol["Preferences"][off]["Value"], False, off)
        self.assertNotRegex(json.dumps(pol), r"mozilla\.(org|com|net)|firefox\.com")
        # App-level defaults (read after omni.ja's firefox.js), never the GRE defaults/pref/ where
        # firefox.js overrides every sidebar.* / uidensity key (the horizontal-tabs regression).
        self.assertFalse(os.path.exists(os.path.join(self.root.path, TREE, "install/defaults/pref/mios-portal.js")))
        prefs = self.root.read(f"{TREE}/install/browser/defaults/preferences/mios-portal.js")
        self.assertIn('pref("sidebar.verticalTabs", true);', prefs)
        self.assertIn('pref("sidebar.position_start", true);', prefs)
        self.assertIn('pref("mios.portal.chat.end", true);', prefs)
        moz = self.root.read(f"{TREE}/configs/common/mozconfig")
        self.assertNotIn("MOZ_APP_PROFILE", moz)
        self.assertIn("ac_add_options --with-distribution-id=${appId}", moz)
        self.assertIn("--profile", self.root.read("usr/bin/mios-portal"))
        self.assertNotIn("--enable-lto", self.root.read(f"{TREE}/configs/lanes/full.mozconfig"))
        self.assertIn("--enable-artifact-builds", self.root.read(f"{TREE}/configs/lanes/artifact.mozconfig"))

    def test_negative_flipping_the_chat_side_changes_the_render_and_fails_the_drift_gate(self):
        self.root.edit("usr/share/mios/mios.toml", "chat_at_end          = true", "chat_at_end          = false")
        self.assertFails(self.root.check(), "portal-prefs")
        self.assertOk(self.root.render("portal-prefs"))
        self.assertIn('pref("mios.portal.chat.end", false);', self.root.read(f"{TREE}/install/browser/defaults/preferences/mios-portal.js"))

    def test_negative_a_palette_change_reaches_the_chrome_css(self):
        bg = self.root.ssot()["colors"]["bg"]
        text = self.root.read("usr/share/mios/mios.toml")
        new = "#123456" if bg.upper() != "#123456" else "#654321"
        self.root.write("usr/share/mios/mios.toml", re.sub(r'(?m)^(bg\s*=\s*")%s(")' % re.escape(bg), r"\g<1>%s\2" % new, text, count=1))
        self.assertFails(self.root.check(), "portal-chrome")
        self.assertOk(self.root.render("portal-chrome"))
        self.assertIn(new, self.root.read(f"{TREE}/src/browser/themes/custom/shared/shared.inc.css"))

    def test_negative_a_missing_template_variable_fails_the_render(self):
        self.root.edit("usr/share/mios/theme/templates/portal-prefs.js.tmpl",
                       'pref("browser.startup.page", 1);', 'pref("x", "@MIOS:desktop.portal.gecko.no_such_key@");')
        self.assertFails(self.root.render("portal-prefs"), "no_such_key")

    def test_negative_an_unresolvable_reference_fails_the_render(self):
        self.root.edit("usr/share/mios/mios.toml", 'home_url     = "http://localhost:${MIOS_PORTS_AGENT_PIPE}/"',
                       'home_url     = "http://localhost:${MIOS_PORTS_NO_SUCH_PORT}/"')
        self.assertFails(self.root.render("portal-prefs"), "unresolved reference")


class TestGate(Case):
    def test_positive_gate_passes_on_the_shipped_tree(self):
        self.assertOk(self.root.gate())

    def test_negative_a_pinned_version_in_the_spec_fails(self):
        self.root.edit("usr/share/mios/mios.toml", 'short_name   = "Portal"', 'short_name   = "Portal"\nfirefox      = "158.0"')
        self.assertFails(self.root.gate(), "pins a version")

    def test_negative_a_pinned_version_in_a_template_fails(self):
        self.root.edit("usr/share/mios/theme/templates/portal-surfer.json.tmpl",
                       '"version": "@BUILD:firefox_version@"', '"version": "158.0"')
        self.assertFails(self.root.gate(), "literal version")

    def test_negative_a_literal_colour_fails(self):
        self.root.edit("usr/share/mios/theme/templates/portal-shared.inc.css.tmpl",
                       "#navigator-toolbox, #sidebar-container, sidebar-main, #sidebar-box, #sidebar-header {",
                       "#navigator-toolbox, #sidebar-container, sidebar-main, #sidebar-box, #sidebar-header {\n  outline-color: #ff8800;")
        self.assertFails(self.root.gate(), "literal colour")

    def test_negative_a_literal_port_fails(self):
        self.root.edit("usr/share/mios/theme/templates/portal-autoconfig.cfg.tmpl",
                       '"@MIOS:desktop.portal.gecko.newtab_url@"', '"http://localhost:8700/"')
        self.assertFails(self.root.gate(), "literal port")
        self.root.edit("usr/share/mios/mios.toml", 'chat_url     = "http://127.0.0.1:${MIOS_PORTS_AGENT_PIPE}/v1"',
                       'chat_url     = "http://127.0.0.1:8700/v1"')
        self.assertFails(self.root.gate(), "port literal")

    def test_negative_a_missing_gecko_exception_fails(self):
        text = self.root.read("usr/share/mios/mios.toml")
        start = text.index("[[build.native.linux.exceptions]]")
        end = text.index("\n]\n", start) + 3
        self.root.write("usr/share/mios/mios.toml", text[:start] + text[end:])
        self.assertFails(self.root.gate(), "no [[build.native.linux.exceptions]] is scoped")

    def test_negative_an_exception_that_drops_libxul_fails(self):
        self.root.edit("usr/share/mios/mios.toml", '"libxul.so", ', "")
        self.assertFails(self.root.gate(), "does not name 'libxul.so'")

    def test_negative_an_unregistered_product_file_fails(self):
        self.root.write(f"{TREE}/src/browser/base/sneaky.cpp", "int main() {}\n")
        self.assertFails(self.root.gate(), "not a product-tree file type")
        os.unlink(self.root.p(f"{TREE}/src/browser/base/sneaky.cpp"))
        self.root.write(f"{TREE}/src/browser/base/content/handwritten.js", "/* no header */\n")
        self.assertFails(self.root.gate(), "neither rendered")

    def test_negative_a_mozilla_cloud_host_in_product_tree_fails(self):
        for bad_host, needle in (("https://ml.mozilla.org/v1", "Mozilla cloud/telemetry host"),
                                 ("wss://smartwindow.fastly-mlpa.net/ws", "Mozilla AI host"),
                                 ("fastly-mlpa.net", "Mozilla AI host"),
                                 ("https://mozilla.ai/chat", "Mozilla cloud/telemetry host"),
                                 ("model-hub.mozilla.ai", "Mozilla AI host")):
            with self.subTest(bad_host=bad_host):
                self.root.edit(f"{TREE}/install/distribution/policies.json",
                               '"browser.smartwindow.endpoint": { "Value": "", "Status": "locked" }',
                               f'"browser.smartwindow.endpoint": {{ "Value": "{bad_host}", "Status": "locked" }}')
                self.assertFails(self.root.gate(), needle)
                self.root.edit(f"{TREE}/install/distribution/policies.json",
                               f'"browser.smartwindow.endpoint": {{ "Value": "{bad_host}", "Status": "locked" }}',
                               '"browser.smartwindow.endpoint": { "Value": "", "Status": "locked" }')

    def test_negative_a_phase_header_that_disagrees_with_the_ssot_fails(self):
        self.root.edit("automation/67-bake-portal.sh", "# MIOS_APPLY_CLASS=universal", "# MIOS_APPLY_CLASS=bake-only")
        self.assertFails(self.root.gate(), "MIOS_APPLY_CLASS=bake-only")

    def test_negative_the_old_literal_fallbacks_fail(self):
        for planted, needle in ((': "${MIOS_COLORS_BG:=#282262}"', "colour fallback"),
                                ('export MIOS_SURFER_NAME="${MIOS_SURFER_NAME:-MiOS Webshell}"', "MIOS_SURFER_"),
                                ('FF_VER="${FF_VER:-153.0}"', "version fallback"),
                                ("npx surfer download", "in-image Gecko fetch")):
            with self.subTest(planted=planted):
                body = self.root.read("automation/67-bake-portal.sh")
                self.root.write("automation/67-bake-portal.sh", body + planted + "\n")
                self.assertFails(self.root.gate(), needle)
                self.root.write("automation/67-bake-portal.sh", body)
        self.assertOk(self.root.gate())


class TestStage(Case):
    def setUp(self):
        super().setUp()
        try:
            import PIL  # noqa: F401
        except ImportError:
            self.skipTest("python3-pillow is not installed")
        self.versions = self.root.p("versions.txt")
        with open(self.versions, "w") as fh:
            fh.write("firefox_version=158.0\nfirefox_revision=" + "a" * 40 + "\nsurfer_version=1.14.9\n")

    def test_positive_stage_fills_versions_only_in_the_build_copy(self):
        out = self.root.p("stage")
        self.assertOk(self.root.run(self.root.p("usr/libexec/mios/mios-portal-tree"), "stage", "--out", out,
                                    "--versions", self.versions))
        surfer = json.load(open(os.path.join(out, "surfer.json")))
        self.assertEqual(surfer["version"]["version"], "158.0")
        self.assertIn("@BUILD:firefox_version@", self.root.read(f"{TREE}/surfer.json"))
        lane = self.root.ssot()["desktop"]["portal"]["builder"]["lane"]
        self.assertEqual(open(os.path.join(out, "mozconfig")).read(),
                         self.root.read(f"{TREE}/configs/lanes/{lane}.mozconfig"))
        brand = os.path.join(out, "configs/branding", self.root.ssot()["desktop"]["portal"]["gecko"]["brand"])
        for f in ("logo.png", "logo-mac.png", "firefox.ico", "firefox64.ico", "logo16.png", "logo512.png"):
            self.assertTrue(os.path.isfile(os.path.join(brand, f)), f)
        self.assertTrue(os.path.isdir(os.path.join(brand, "content")))
        manifest = json.load(open(os.path.join(out, "mios-portal-stage.json")))
        self.assertEqual(manifest["resolved"], {"firefox_version": "158.0"})

    def test_negative_a_missing_resolved_version_fails(self):
        with open(self.versions, "w") as fh:
            fh.write("surfer_version=1.14.9\n")
        self.assertFails(self.root.run(self.root.p("usr/libexec/mios/mios-portal-tree"), "stage", "--out",
                                       self.root.p("stage"), "--versions", self.versions), "@BUILD:firefox_version@")

    def test_negative_a_stale_committed_tree_fails(self):
        self.root.edit("usr/share/mios/mios.toml", 'short_name   = "Portal"', 'short_name   = "Portal X"')
        self.assertFails(self.root.run(self.root.p("usr/libexec/mios/mios-portal-tree"), "stage", "--out",
                                       self.root.p("stage"), "--versions", self.versions), "drifted")

    def test_check_import_needs_every_copy_patch_in_engine(self):
        proj = self.root.p("proj")
        shutil.copytree(self.root.p(TREE), proj)
        engine = os.path.join(proj, "engine")
        tool = self.root.p("usr/libexec/mios/mios-portal-tree")
        os.makedirs(engine)
        self.assertFails(self.root.run(tool, "check-import", "--project", proj), "did not place")
        for d, _ds, fs in os.walk(os.path.join(proj, "src")):
            for f in fs:
                if not f.endswith(".patch"):
                    rel = os.path.relpath(os.path.join(d, f), os.path.join(proj, "src"))
                    os.makedirs(os.path.dirname(os.path.join(engine, rel)), exist_ok=True)
                    os.symlink(os.path.join(d, f), os.path.join(engine, rel))
        self.assertOk(self.root.run(tool, "check-import", "--project", proj))


if __name__ == "__main__":
    unittest.main(verbosity=2)
