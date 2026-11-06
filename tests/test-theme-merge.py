# AI-hint: Hermetic tests for the dotfiles renderer: every merge kind, Law 13 drop-ins, and edge-to-edge terminals (edge_insets parity table, edge surfaces, edge-literal/scope policy, GTK @import, configurator).
# AI-related: usr/libexec/mios/mios-theme-render, usr/libexec/mios/mios-dotfiles-render, usr/lib/mios/mios_toml.py, usr/share/mios/theme/fixtures/edge/padding-cases.tsv, usr/share/mios/configurator/mios.html
# AI-functions: TestThemeMerge, TestEdgeInsets, TestEdgeRender, TestEdgePolicy, TestGtkImport, TestConfigurator, main

import importlib.machinery
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

class TestThemeMerge(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = self.temp_dir.name

        self.dirs = [
            "usr/share/mios",
            "usr/share/mios/theme/templates",
            "usr/share/mios/theme/fixtures",
            "usr/libexec/mios",
            "etc/mios"
        ]
        for d in self.dirs:
            os.makedirs(os.path.join(self.root, d), exist_ok=True)

        self.vendor_d = os.path.join(self.root, "usr/lib/mios/mios.d")
        self.host_d = os.path.join(self.root, "etc/mios/mios.d")
        self.dotfiles_render = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "usr", "libexec", "mios", "mios-dotfiles-render")
        )

        self.render_script = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "usr", "libexec", "mios", "mios-theme-render")
        )

        self.write_vendor_toml({
            "meta": {"mios_version": "0.3.0"},
            "colors": {"accent": "#123456"}
        })

    def tearDown(self):
        self.temp_dir.cleanup()

    def get_env(self, user_toml=None):
        env = os.environ.copy()
        # Pin EVERY layer and fragment dir into the temp root, so an inherited
        # MIOS_TOML_ROOT / MIOS_*_TOML_D (tools/sync-generated.sh exports them)
        # can never leak the real tree's drop-ins into a hermetic test.
        env.pop("MIOS_TOML", None)
        env["MIOS_THEME_ROOT"] = self.root
        env["MIOS_TOML_ROOT"] = self.root
        env["MIOS_VENDOR_TOML"] = os.path.join(self.root, "usr/share/mios/mios.toml")
        env["MIOS_VENDOR_TOML_D"] = self.vendor_d
        env["MIOS_HOST_TOML"] = os.path.join(self.root, "etc/mios/mios-host.toml")
        env["MIOS_HOST_TOML_D"] = self.host_d
        env["MIOS_USER_TOML_D"] = os.path.join(self.root, "etc/mios/user-none.d")
        if user_toml:
            env["MIOS_USER_TOML"] = user_toml
        else:
            env["MIOS_USER_TOML"] = os.path.join(self.root, "etc/mios/mios-user-none.toml")
        repo_lib = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "usr", "lib", "mios"))
        env["PYTHONPATH"] = repo_lib + os.pathsep + env.get("PYTHONPATH", "")
        return env

    # The [theme] keys every dotfiles render reads (mios-dotfiles-render _resolved_for: the bg_alpha token from
    # opacity / acrylic). The vendor SSOT always carries them; a fixture SSOT without them is not one the renderer
    # can be handed, so each fixture gets them unless the test brings its own [theme].
    RENDER_THEME = {"opacity": 100, "acrylic": False}

    def write_vendor_toml(self, data):
        if "theme" not in data:
            data = {"theme": dict(self.RENDER_THEME), **data}
        path = os.path.join(self.root, "usr/share/mios/mios.toml")
        with open(path, "w", encoding="utf-8") as f:
            for section, keys in data.items():
                if section == "dotfiles" and "registry" in keys:
                    for name, cfg in keys["registry"].items():
                        f.write(f"[dotfiles.registry.{name}]\n")
                        for k, v in cfg.items():
                            if isinstance(v, dict):
                                f.write(f"[dotfiles.registry.{name}.{k}]\n")
                                for sk, sv in v.items():
                                    f.write(f'{sk} = "{sv}"\n')
                            elif isinstance(v, str):
                                f.write(f'{k} = "{v}"\n')
                            else:
                                f.write(f'{k} = {str(v).lower() if isinstance(v, bool) else v}\n')
                        f.write("\n")
                else:
                    f.write(f"[{section}]\n")
                    for k, v in keys.items():
                        if isinstance(v, str):
                            f.write(f'{k} = "{v}"\n')
                        else:
                            f.write(f'{k} = {str(v).lower() if isinstance(v, bool) else v}\n')
                    f.write("\n")

    def run_cmd(self, args, user_toml=None):
        cmd = [sys.executable, self.render_script] + args
        return subprocess.run(cmd, env=self.get_env(user_toml), capture_output=True, text=True)

    def test_unknown_kind_aborts(self):
        self.write_vendor_toml({
            "dotfiles": {
                "registry": {
                    "bad-kind": {
                        "template": "usr/share/mios/theme/templates/bad.tmpl",
                        "target": "usr/share/mios/theme/fixtures/bad.expected",
                        "kind": "unknown-kind-xyz"
                    }
                }
            }
        })
        res = self.run_cmd(["check", "bad-kind"])
        self.assertEqual(res.returncode, 3)
        self.assertIn("unknown kind", res.stderr)

    def test_missing_fixture_aborts(self):
        self.write_vendor_toml({
            "dotfiles": {
                "registry": {
                    "missing-fx": {
                        "template": "usr/share/mios/theme/templates/missing.tmpl",
                        "target": "usr/share/mios/theme/fixtures/missing.expected",
                        "kind": "json-merge"
                    }
                }
            }
        })
        res = self.run_cmd(["check", "missing-fx"])
        self.assertEqual(res.returncode, 3)
        self.assertIn("MUST declare fixture.base + fixture.expected", res.stderr)

    def test_json_merge_semantics(self):
        self.write_vendor_toml({
            "colors": {"accent": "#112233"},
            "dotfiles": {
                "registry": {
                    "json-surface": {
                        "template": "usr/share/mios/theme/templates/jsonsurface.json.tmpl",
                        "target": "usr/share/mios/theme/fixtures/jsonsurface.expected.json",
                        "kind": "json-merge",
                        "fixture": {
                            "base": "usr/share/mios/theme/fixtures/jsonsurface.base.json",
                            "expected": "usr/share/mios/theme/fixtures/jsonsurface.expected.json"
                        }
                    }
                }
            }
        })

        tmpl_path = os.path.join(self.root, "usr/share/mios/theme/templates/jsonsurface.json.tmpl")
        with open(tmpl_path, "w", encoding="utf-8") as f:
            f.write('{"mykey": "@MIOS:accent@"}\n')

        base_path = os.path.join(self.root, "usr/share/mios/theme/fixtures/jsonsurface.base.json")
        base_content = {
            "foreign_key": "preserved_val",
            "url": "https://foreign-url.com//some//path",
            "nested": {
                "foreign_sub": 42
            }
        }
        with open(base_path, "w", encoding="utf-8") as f:
            json.dump(base_content, f)

        res = self.run_cmd(["render", "json-surface"])
        self.assertEqual(res.returncode, 0)

        expected_path = os.path.join(self.root, "usr/share/mios/theme/fixtures/jsonsurface.expected.json")
        with open(expected_path, "r", encoding="utf-8") as f:
            expected = json.load(f)

        self.assertEqual(expected.get("mykey"), "#112233")
        self.assertEqual(expected.get("foreign_key"), "preserved_val")
        self.assertEqual(expected.get("url"), "https://foreign-url.com//some//path")
        self.assertEqual(expected["nested"].get("foreign_sub"), 42)

        with open(base_path, "w", encoding="utf-8") as f:
            f.write("{invalid-json-structure\n")

        res_bad = self.run_cmd(["render", "json-surface"])
        self.assertEqual(res_bad.returncode, 2)
        self.assertIn("REFUSED: json-merge base did not parse", res_bad.stderr)

    def test_ini_merge_semantics(self):
        self.write_vendor_toml({
            "colors": {"accent": "#223344"},
            "dotfiles": {
                "registry": {
                    "ini-surface": {
                        "template": "usr/share/mios/theme/templates/inisurface.tmpl",
                        "target": "usr/share/mios/theme/fixtures/inisurface.expected",
                        "kind": "ini-merge",
                        "section": "colors",
                        "policy": "seed-or-enforce",
                        "fixture": {
                            "base": "usr/share/mios/theme/fixtures/inisurface.base",
                            "expected": "usr/share/mios/theme/fixtures/inisurface.expected"
                        }
                    }
                }
            }
        })

        tmpl_path = os.path.join(self.root, "usr/share/mios/theme/templates/inisurface.tmpl")
        with open(tmpl_path, "w", encoding="utf-8") as f:
            f.write("[colors]\nmykey = @MIOS:colors_accent@\n")

        base_path = os.path.join(self.root, "usr/share/mios/theme/fixtures/inisurface.base")
        base_lines = [
            "[credential]",
            "helper = cache",
            "[user]",
            "signingkey = ABCDEF",
            "[remote \"origin\"]",
            "url = git@github.com:user/repo.git",
            "[colors]",
            "mykey = #existing_val"
        ]
        with open(base_path, "w", encoding="utf-8") as f:
            f.write("\n".join(base_lines) + "\n")

        res = self.run_cmd(["render", "ini-surface"])
        self.assertEqual(res.returncode, 0)

        expected_path = os.path.join(self.root, "usr/share/mios/theme/fixtures/inisurface.expected")
        with open(expected_path, "r", encoding="utf-8") as f:
            expected_content = f.read()

        self.assertIn("signingkey = ABCDEF", expected_content)
        self.assertIn("helper = cache", expected_content)
        self.assertIn("url = git@github.com:user/repo.git", expected_content)
        self.assertIn("mykey = #existing_val", expected_content)

        user_toml_path = os.path.join(self.root, "etc/mios/mios-user-overlay.toml")
        with open(user_toml_path, "w", encoding="utf-8") as f:
            f.write("[colors]\naccent = \"#998877\"\n")

        res_overlay = self.run_cmd(["render", "ini-surface"], user_toml=user_toml_path)
        self.assertEqual(res_overlay.returncode, 0)

        with open(expected_path, "r", encoding="utf-8") as f:
            expected_content_overlay = f.read()

        self.assertIn("signingkey = ABCDEF", expected_content_overlay)
        self.assertIn("mykey = #998877", expected_content_overlay)

    def test_tampered_expected_fails_check(self):
        self.write_vendor_toml({
            "colors": {"accent": "#111111"},
            "dotfiles": {
                "registry": {
                    "check-surface": {
                        "template": "usr/share/mios/theme/templates/check.tmpl",
                        "target": "usr/share/mios/theme/fixtures/check.expected",
                        "kind": "json-merge",
                        "fixture": {
                            "base": "usr/share/mios/theme/fixtures/check.base",
                            "expected": "usr/share/mios/theme/fixtures/check.expected"
                        }
                    }
                }
            }
        })

        tmpl_path = os.path.join(self.root, "usr/share/mios/theme/templates/check.tmpl")
        with open(tmpl_path, "w", encoding="utf-8") as f:
            f.write('{"val": "@MIOS:accent@"}\n')

        base_path = os.path.join(self.root, "usr/share/mios/theme/fixtures/check.base")
        with open(base_path, "w", encoding="utf-8") as f:
            f.write('{\n  "other": 1\n}\n')

        res_render = self.run_cmd(["render", "check-surface"])
        self.assertEqual(res_render.returncode, 0)

        res_pass = self.run_cmd(["check", "check-surface"])
        self.assertEqual(res_pass.returncode, 0)

        expected_path = os.path.join(self.root, "usr/share/mios/theme/fixtures/check.expected")
        with open(expected_path, "w", encoding="utf-8") as f:
            f.write('{\n  "other": 1,\n  "val": "#tampered_val"\n}\n')

        res_fail = self.run_cmd(["check", "check-surface"])
        self.assertEqual(res_fail.returncode, 1)
        self.assertIn("drifted from SSOT projection", res_fail.stderr)

    # -- Law 13: drop-in fragments reach the projection, tier-major ---------

    def _fragment_surface(self):
        """A json-merge surface whose one token is [colors].accent; returns the
        path of its rendered expected fixture."""
        self.write_vendor_toml({
            "colors": {"accent": "#101010"},
            "dotfiles": {
                "registry": {
                    "frag-surface": {
                        "template": "usr/share/mios/theme/templates/frag.json.tmpl",
                        "target": "usr/share/mios/theme/fixtures/frag.expected.json",
                        "kind": "json-merge",
                        "fixture": {
                            "base": "usr/share/mios/theme/fixtures/frag.base.json",
                            "expected": "usr/share/mios/theme/fixtures/frag.expected.json"
                        }
                    }
                }
            }
        })
        with open(os.path.join(self.root, "usr/share/mios/theme/templates/frag.json.tmpl"),
                  "w", encoding="utf-8") as f:
            f.write('{"accent": "@MIOS:accent@"}\n')
        with open(os.path.join(self.root, "usr/share/mios/theme/fixtures/frag.base.json"),
                  "w", encoding="utf-8") as f:
            f.write('{"other": 1}\n')
        return os.path.join(self.root, "usr/share/mios/theme/fixtures/frag.expected.json")

    def _write_fragment(self, dirpath, name, accent):
        os.makedirs(dirpath, exist_ok=True)
        with open(os.path.join(dirpath, name), "w", encoding="utf-8") as f:
            f.write(f'[colors]\naccent = "{accent}"\n')

    def _render_accent(self, expected_path):
        res = subprocess.run([sys.executable, self.dotfiles_render, "render", "frag-surface"],
                             env=self.get_env(), capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        with open(expected_path, "r", encoding="utf-8") as f:
            return json.load(f).get("accent")

    def test_vendor_fragment_reaches_projection(self):
        expected = self._fragment_surface()
        self.assertEqual(self._render_accent(expected), "#101010")
        self._write_fragment(self.vendor_d, "50-accent.toml", "#AB12CD")
        self.assertEqual(self._render_accent(expected), "#AB12CD",
                         "a usr/lib/mios/mios.d vendor fragment did not reach the render")

    def test_host_fragment_outranks_vendor_fragment(self):
        expected = self._fragment_surface()
        # The vendor fragment sorts LATER by basename; tier still wins (tier-major).
        self._write_fragment(self.vendor_d, "99-accent.toml", "#AB12CD")
        self._write_fragment(self.host_d, "00-accent.toml", "#34EF56")
        self.assertEqual(self._render_accent(expected), "#34EF56",
                         "a vendor fragment outranked a host fragment (flat sort, not tier-major)")

    def test_vendor_tier_anchored_to_render_root(self):
        # No MIOS_TOML_ROOT / MIOS_VENDOR_TOML(_D): the renderer itself must pin the vendor
        # tier (monolith + usr/lib/mios/mios.d) to MIOS_THEME_ROOT, never to the FHS paths.
        expected = self._fragment_surface()
        self._write_fragment(self.vendor_d, "50-accent.toml", "#AB12CD")
        env = self.get_env()
        for k in ("MIOS_TOML_ROOT", "MIOS_VENDOR_TOML", "MIOS_VENDOR_TOML_D"):
            env.pop(k)
        res = subprocess.run([sys.executable, self.dotfiles_render, "render", "frag-surface"],
                             env=env, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        with open(expected, "r", encoding="utf-8") as f:
            self.assertEqual(json.load(f).get("accent"), "#AB12CD",
                             "the vendor tier left MIOS_THEME_ROOT (MIOS_TOML_ROOT setdefault missing)")


# -- Edge-to-edge terminals ------------------------------------------------
REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(REPO, "usr/lib/mios"))
import mios_toml  # noqa: E402

RENDER = os.path.join(REPO, "usr/libexec/mios/mios-dotfiles-render")
CASES = os.path.join(REPO, "usr/share/mios/theme/fixtures/edge/padding-cases.tsv")
EDGE_SURFACES = ["code-server-terminal", "gtk4-edge", "windows-terminal", "fastfetch"]


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


class _TempRoot:
    """A render root holding the real vendor mios.toml and theme tree, isolated from host/user tiers."""

    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        shutil.copytree(os.path.join(REPO, "usr/share/mios/theme"), os.path.join(self.root, "usr/share/mios/theme"))
        shutil.copy2(os.path.join(REPO, "usr/share/mios/mios.toml"), os.path.join(self.root, "usr/share/mios/mios.toml"))
        self.user_toml = os.path.join(self.root, "user.toml")

    def env(self, user_text=None):
        env = dict(os.environ)
        env.pop("MIOS_TOML", None)
        env.update({
            "MIOS_THEME_ROOT": self.root, "MIOS_TOML_ROOT": self.root,
            "MIOS_VENDOR_TOML": os.path.join(self.root, "usr/share/mios/mios.toml"),
            "MIOS_VENDOR_TOML_D": os.path.join(self.root, "vendor.d"),
            "MIOS_HOST_TOML": os.path.join(self.root, "host-absent.toml"),
            "MIOS_HOST_TOML_D": os.path.join(self.root, "host-absent.d"),
            "MIOS_USER_TOML": self.user_toml if user_text is not None else os.path.join(self.root, "user-absent.toml"),
            "MIOS_USER_TOML_D": os.path.join(self.root, "user-absent.d"),
            "PYTHONPATH": os.path.join(REPO, "usr/lib/mios") + os.pathsep + os.environ.get("PYTHONPATH", ""),
        })
        if user_text is not None:
            _write(self.user_toml, user_text)
        return env

    def run(self, *args, user_text=None):
        return subprocess.run([sys.executable, RENDER, *args], env=self.env(user_text),
                              capture_output=True, text=True)

    def text(self, rel):
        return _read(os.path.join(self.root, rel))

    def close(self):
        self._tmp.cleanup()


class TestEdgeInsets(unittest.TestCase):
    def test_parity_table(self):
        rows = 0
        for line in _read(CASES).splitlines():
            if not line or line.startswith("#"):
                continue
            cols = line.split("\t")
            given = "" if cols[0] == "<empty>" else cols[0].replace("<LF>", "\n")
            try:
                ins = mios_toml.edge_insets({"theme": {"padding": given, "scrollbar_state": "hidden"}})
                got = [str(ins[k]) for k in ("left", "top", "right", "bottom")]
            except mios_toml.EdgeInsetsError as e:
                self.assertEqual(str(e), f"[theme].padding: '{given}' is not a non-negative integer WT padding")
                got = ["ERR"]
            self.assertEqual(got, cols[1:], f"padding {given!r}")
            rows += 1
        self.assertGreaterEqual(rows, 10, "the parity table lost its rows")

    def test_non_string_padding_is_named(self):
        for bad in (True, None, 1.5):
            with self.assertRaisesRegex(mios_toml.EdgeInsetsError, r"\[theme\]\.padding: '%s'" % ("" if bad is None else bad)):
                mios_toml.edge_insets({"theme": {"padding": bad, "scrollbar_state": "hidden"}})

    def test_scrollbar_state(self):
        base = {"padding": "0"}
        self.assertTrue(mios_toml.edge_insets({"theme": dict(base, scrollbar_state="hidden")})["scrollbar_hidden"])
        self.assertFalse(mios_toml.edge_insets({"theme": dict(base, scrollbar_state="always")})["scrollbar_hidden"])
        with self.assertRaisesRegex(mios_toml.EdgeInsetsError, r"\[theme\]\.scrollbar_state: 'off'"):
            mios_toml.edge_insets({"theme": dict(base, scrollbar_state="off")})


class TestEdgeRender(unittest.TestCase):
    def setUp(self):
        self.t = _TempRoot()

    def tearDown(self):
        self.t.close()

    def test_vendor_tier_is_edge_to_edge(self):
        css = _read(os.path.join(REPO, "usr/share/mios/themes/code-server-terminal.css"))
        self.assertIn("padding-left: 0px !important;", css)
        self.assertIn("right: 0px !important;", css)
        self.assertIn("display: none !important;", css)
        self.assertNotRegex(css, r"\.part\.panel\s*\{", "the .part.panel margin/border override is back")
        self.assertNotIn(".monaco-scrollable-element > .scrollbar", css, "the scrollbar rule reaches every workbench scrollbar")
        self.assertIn("vte-terminal.padded { padding: 0px 0px 0px 0px; margin: 0; }",
                      _read(os.path.join(REPO, "etc/skel/.config/gtk-4.0/mios-edge.css")))
        self.assertIn('"padding": { "top": 1, "right": 0, "left": 0 }',
                      _read(os.path.join(REPO, "usr/share/mios/fastfetch/config.jsonc")))
        wt = _read(os.path.join(REPO, "usr/share/mios/theme/fixtures/windows-terminal-settings.expected.json"))
        self.assertIn('"padding": "0"', wt)
        self.assertIn('"scrollbarState": "hidden"', wt)

    def test_input_variation(self):
        user = '[theme]\npadding = "3, 1, 3, 1"\nscrollbar_state = "visible"\n[theme.fastfetch]\nlogo_padding_left = 4\n'
        res = self.t.run("render", *EDGE_SURFACES, user_text=user)
        self.assertEqual(res.returncode, 0, res.stderr)
        css = self.t.text("usr/share/mios/themes/code-server-terminal.css")
        self.assertIn("padding-left: 3px !important;", css)
        self.assertIn("padding-top: 1px !important;", css)
        self.assertIn("margin-left: calc(-1 * 3px) !important;", css)
        self.assertIn("right: 3px !important;", css)
        self.assertIn("display: block !important;", css)
        self.assertIn("vte-terminal.padded { padding: 1px 3px 1px 3px; margin: 0; }",
                      self.t.text("etc/skel/.config/gtk-4.0/mios-edge.css"))
        wt = self.t.text("usr/share/mios/theme/fixtures/windows-terminal-settings.expected.json")
        self.assertIn('"padding": "3, 1, 3, 1"', wt)
        self.assertIn('"scrollbarState": "visible"', wt)
        self.assertIn('"padding": { "top": 1, "right": 0, "left": 4 }',
                      self.t.text("usr/share/mios/fastfetch/config.jsonc"))

    def test_bad_padding_fails_loud(self):
        res = self.t.run("render", "gtk4-edge", user_text='[theme]\npadding = "1.5"\n')
        self.assertEqual(res.returncode, 2)
        self.assertIn("[theme].padding: '1.5' is not a non-negative integer WT padding", res.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.t.root, "etc/skel/.config/gtk-4.0/mios-edge.css")))

    def test_committed_drift_is_named(self):
        self.assertEqual(self.t.run("render", *EDGE_SURFACES).returncode, 0)
        css = os.path.join(self.t.root, "usr/share/mios/themes/code-server-terminal.css")
        _write(css, _read(css).replace("padding-left: 0px !important;", "padding-left: 20px !important;", 1))
        res = self.t.run("check", "code-server-terminal")
        self.assertEqual(res.returncode, 1)
        self.assertIn("code-server-terminal: usr/share/mios/themes/code-server-terminal.css drifted from SSOT projection", res.stderr)
        self.assertIn("committed='padding-left: 20px !important;' projected='padding-left: 0px !important;'", res.stderr)


class TestEdgePolicy(unittest.TestCase):
    def setUp(self):
        self.t = _TempRoot()

    def tearDown(self):
        self.t.close()

    def _plant(self, rel, old, new):
        p = os.path.join(self.t.root, rel)
        text = _read(p)
        self.assertIn(old, text)
        _write(p, text.replace(old, new, 1))

    def test_clean_templates_pass(self):
        self.assertEqual(self.t.run("render", *EDGE_SURFACES).returncode, 0)
        res = self.t.run("check", *EDGE_SURFACES)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertNotIn("edge-literal", res.stderr)

    def test_literal_inset_is_named(self):
        tmpl = "usr/share/mios/theme/templates/code-server-terminal.css.tmpl"
        self._plant(tmpl, "padding-left: @MIOS:edge.left_px@px !important;", "padding-left: 20px !important;")
        self._plant("usr/share/mios/theme/templates/gtk4-edge.css.tmpl", "margin: 0;", "margin: 1px;")
        self._plant("usr/share/mios/theme/templates/windows-terminal-settings.json.tmpl",
                    '"scrollbarState": "@MIOS:edge.scrollbar_state@"', '"scrollbarState": "visible"')
        self._plant("usr/share/mios/theme/templates/fastfetch-config.jsonc.tmpl",
                    '"left": @MIOS:theme.fastfetch.logo_padding_left@', '"left": 2')
        res = self.t.run("check", *EDGE_SURFACES)
        self.assertEqual(res.returncode, 1)
        for want in ("edge-literal code-server-terminal: xterm_padding_left: 20px !important",
                     "edge-literal gtk4-edge: vte_margin: 1px",
                     "edge-literal windows-terminal: scrollbarState: visible",
                     "edge-literal fastfetch: logo_padding_left: 2"):
            self.assertIn(want, res.stderr)

    def test_terminal_chrome_literal_and_scope_are_named(self):
        tmpl = "usr/share/mios/theme/templates/code-server-terminal.css.tmpl"
        self._plant(tmpl, ".terminal-split-pane-wrapper .split-view-view {\n    padding: @MIOS:theme.edge.code_server_chrome_px@px",
                    ".terminal-split-pane-wrapper .split-view-view {\n    padding: 20px")
        self._plant(tmpl, ".xterm .xterm-scrollable-element > .scrollbar {",
                    ".xterm .xterm-scrollable-element > .scrollbar,\n.monaco-scrollable-element > .scrollbar {")
        with open(os.path.join(self.t.root, tmpl), "a", encoding="utf-8") as fh:
            fh.write("\n.minimap { left: @MIOS:edge.left_px@px; }\n")
        res = self.t.run("check", "code-server-terminal")
        self.assertEqual(res.returncode, 1)
        for want in ("edge-literal code-server-terminal: terminal_chrome: padding: 20px",
                     "edge-scope code-server-terminal: terminal_chrome: .monaco-scrollable-element > .scrollbar",
                     "edge-scope code-server-terminal: terminal_chrome: .minimap"):
            self.assertIn(want, res.stderr)

    def test_removed_rule_is_named(self):
        self._plant("usr/share/mios/theme/templates/windows-terminal-settings.json.tmpl",
                    ',\n      "padding": "@MIOS:edge.padding@"', "")
        res = self.t.run("check", "windows-terminal")
        self.assertEqual(res.returncode, 1)
        self.assertIn("edge-literal windows-terminal: padding: <absent>", res.stderr)

    def test_enforce_off_skips_policy(self):
        self._plant("usr/share/mios/theme/templates/gtk4-edge.css.tmpl", "margin: 0;", "margin: 1px;")
        user = '[theme.edge]\nenforce = false\n'
        self.assertEqual(self.t.run("render", "gtk4-edge", user_text=user).returncode, 0)
        res = self.t.run("check", "gtk4-edge", user_text=user)
        self.assertEqual(res.returncode, 0, res.stderr)

    def test_malformed_edge_props_exit_3(self):
        _write(os.path.join(self.t.root, "vendor.d", "50-bad.toml"),
               "[dotfiles.registry.gtk4-edge.edge_props]\nvte_padding = 'vte-terminal'\n")
        res = self.t.run("check", "gtk4-edge")
        self.assertEqual(res.returncode, 3)
        self.assertIn("[dotfiles.registry.gtk4-edge] edge = true edge_props.vte_padding", res.stderr)


class TestGtkImport(unittest.TestCase):
    def test_import_line(self):
        path = os.path.join(REPO, "usr/libexec/mios/mios-theme-render")
        loader = importlib.machinery.SourceFileLoader("mios_theme_render", path)
        mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("mios_theme_render", loader))
        loader.exec_module(mod)
        colors = mios_toml.colors({})
        self.assertEqual(mod.render_gtk_css(colors).splitlines()[1], '@import url("mios-edge.css");')
        self.assertNotIn("@import", mod.render_gtk_css(colors, edge_import=False))
        # The skel layering apply_gtk writes: gtk.css imports the generated palette (user rules below it stay the
        # user's), and the palette imports the edge stylesheet first, so the edge rules still lead the cascade.
        def first_rule(rel):
            lines = _read(os.path.join(REPO, rel)).splitlines()
            return next(ln for ln in lines if ln.strip() and not ln.startswith("/*"))
        self.assertEqual(first_rule("etc/skel/.config/gtk-4.0/gtk.css"), '@import url("mios-colors.css");')
        self.assertEqual(first_rule("etc/skel/.config/gtk-4.0/mios-colors.css"), '@import url("mios-edge.css");')


class TestConfigurator(unittest.TestCase):
    def test_every_edge_scalar_has_an_input(self):
        html = _read(os.path.join(REPO, "usr/share/mios/configurator/mios.html"))
        data = mios_toml.load_merged([os.path.join(REPO, "usr/share/mios/mios.toml")])
        keys = [f"theme.edge.{k}" for k, v in mios_toml.section(data, "theme.edge").items()
                if not isinstance(v, (dict, list))]
        keys += [f"theme.fastfetch.{k}" for k in mios_toml.section(data, "theme.fastfetch") if k.startswith("logo_padding_")]
        self.assertGreaterEqual(len(keys), 11)
        self.assertEqual([k for k in keys if not re.search(r'data-key="%s"' % re.escape(k), html)], [])


if __name__ == "__main__":
    unittest.main()
