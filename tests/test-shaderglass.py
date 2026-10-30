#!/usr/bin/env python3
# AI-hint: ShaderGlass overlay pane ([desktop.shaderglass]: miosd shaderglass, mios-gen shaderglass_lua, mios-share-picker) and the Hyprglass frame-rate knob, in four tiers:
#   1. SSOT/source (always): the section is unpinned and its window patterns are anchored and scoped; the pane uses the
#      desktop's one glass preset; the drawer entry, keymap and package wiring follow `enabled`; the keybinds are free;
#      the glue scripts parse; the release tree is git-ignored; the WineCap patch and builder agree with miosd.
#   2. projection (MIOS_GEN_BIN, default /usr/bin/mios-gen once it projects ShaderGlass): rules and binds are present
#      only while enabled and parse as Lua; live_resample_fps = 0 reaches hg.config for windows and layers.
#   3. miosd (MIOS_SHADERGLASS_MIOSD, default /usr/bin/miosd once it has `shaderglass`), hermetic temp roots, never the
#      compositor: mocked latest-release resolution, digest-verified install of a synthetic release (tamper, licence and
#      stage negatives), the picker's one-shot answer and stock fallback, xdph.conf, argument handling.
#   4. upstream (network; MIOS_TEST_OFFLINE=1 skips): the WineCap patch applies to ShaderGlass HEAD and its newest
#      release tag; hyprglass HEAD and the commit pinned for [desktop.hyprland.hyprglass].hyprland_version register
#      windows/layers live_resample_fps with 0 = uncapped and map hg.config's nested tables onto those option paths.
# AI-related: usr/share/mios/mios.toml [desktop.shaderglass] [desktop.hyprland.xdph] [desktop.hyprland.hyprglass.config],
# src/mios-rs/miosd/src/shaderglass.rs, src/mios-rs/miosd/src/shaderglass/, tools/native/mios-gen/src/render.rs (shaderglass_lua),
# usr/share/mios/shaderglass/, usr/bin/mios-shaderglass, usr/libexec/mios/mios-share-picker, usr/libexec/mios/mios-sync-theme
"""ShaderGlass overlay pane and Hyprglass live re-sample rate tests."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import tomllib
import unittest
import zipfile

ROOT = os.environ.get("MIOS_TEST_ROOT") or os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SSOT = os.environ.get("MIOS_VENDOR_TOML") or os.path.join(ROOT, "usr", "share", "mios", "mios.toml")
OFFLINE = os.environ.get("MIOS_TEST_OFFLINE") == "1"


def read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def read_bytes(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def ssot() -> dict:
    with open(SSOT, "rb") as f:
        return tomllib.load(f)


def combo(key: str) -> str:
    return "+".join(sorted(p.strip().upper() for p in key.split("+") if p.strip()))


def run(cmd: list[str], env: dict | None = None, cwd: str | None = None, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=cwd, timeout=timeout)


def with_enabled(text: str, on: bool) -> str:
    """The SSOT text with [desktop.shaderglass].enabled set (the key line is unique in its section)."""
    start = text.index("[desktop.shaderglass]\n")
    end = text.index("\n[", start + 1)
    head, body, tail = text[:start], text[start:end], text[end:]
    body2 = re.sub(r"(?m)^enabled = (true|false)", f"enabled = {'true' if on else 'false'}", body, count=1)
    assert body2 != body or f"enabled = {'true' if on else 'false'}" in body
    return head + body2 + tail


def with_fps(text: str) -> str:
    """The SSOT text with [desktop.framerate].enabled = false and live_resample_fps = 0 for layers and windows."""
    g = tomllib.loads(text)["desktop"]["hyprland"]["hyprglass"]["config"]
    if g.get("layers", {}).get("live_resample_fps") == 0 and g.get("windows", {}).get("live_resample_fps") == 0:
        return text
    if "[desktop.framerate]\n" in text:
        fstart = text.index("[desktop.framerate]\n")
        fend = text.index("\n[", fstart + 1)
        fhead, fbody, ftail = text[:fstart], text[fstart:fend], text[fend:]
        fbody = re.sub(r"(?m)^enabled = (true|false)", "enabled = false", fbody, count=1)
        text = fhead + fbody + ftail
    old = "[desktop.hyprland.hyprglass.config.layers]\nenabled = true\n"
    assert text.count(old) == 1, "fps anchor"
    return text.replace(
        old,
        "[desktop.hyprland.hyprglass.config.windows]\nlive_resample_fps = 0\n" + old + "live_resample_fps = 0\n",
        1,
    )


# ---------------------------------------------------------------- 1. SSOT and source


class SsotAndSource(unittest.TestCase):
    def setUp(self) -> None:
        self.d = ssot()
        self.sg = self.d["desktop"]["shaderglass"]

    def test_section_is_unpinned(self) -> None:
        def keys(v, path=""):
            if isinstance(v, dict):
                for k, x in v.items():
                    yield f"{path}.{k}" if path else k
                    yield from keys(x, f"{path}.{k}" if path else k)
        bad = [k for k in keys(self.sg) if re.search(r"(^|\.)(version|tag|revision|commit|sha256|digest)$", k)]
        self.assertEqual(bad, [], "a pinned upstream key in [desktop.shaderglass]")
        dumped = json.dumps(self.sg)
        self.assertIsNone(re.search(r"\bv?\d+\.\d+\.\d+\b", dumped), "a version literal in [desktop.shaderglass]")
        self.assertIn("{fedora_release}", self.sg["winecap"]["builder"]["base_image"], "the builder follows the host's Fedora release")
        self.assertIn(self.sg["channel"], ("newest", "stable"))
        self.assertIsInstance(self.sg["enabled"], bool)

    def test_window_patterns_are_anchored_and_scoped(self) -> None:
        w = self.sg["window"]
        for k in ("class", "title", "tools_title"):
            self.assertTrue(w[k].startswith("^") and w[k].endswith("$"), k)
        cls, title, tools = (re.compile(w[k]) for k in ("class", "title", "tools_title"))
        self.assertTrue(cls.match("shaderglass.exe"))
        for other in ("kitty", "xshaderglass.exe", "shaderglass.exe2", "zen"):
            self.assertIsNone(cls.match(other), other)
        self.assertTrue(title.match("ShaderGlass") and title.match("ShaderGlass (kitty, crt-geom, 1px, Free, ~1:1, 60fps)"))
        self.assertIsNone(title.match("Shader Parameters"))
        self.assertTrue(tools.match("Choose Shader") and tools.match("Shader Parameters: crt-geom"))
        self.assertIsNone(tools.match("ShaderGlass"))
        self.assertNotEqual(w["pane_tag"], w["passthrough_tag"])
        self.assertEqual(w["layer"], "pinned")
        for word in ("fullscreen", "maximize", "activate", "activatefocus"):
            self.assertIn(word, w["suppress_event"].split())
        self.assertFalse(w["exclude_from_captures"], "OBS and screenshots keep the shaded pane by default")

    def test_only_the_retitled_main_window_is_a_pane(self) -> None:
        w = self.sg["window"]
        main, title, tools = (re.compile(w[k]) for k in ("main_title", "title", "tools_title"))
        self.assertTrue(w["main_title"].startswith("^") and w["main_title"].endswith("$"))
        for t in ("ShaderGlass (no input selected)", "ShaderGlass (stopped)", "ShaderGlass (kitty, crt-geom, 1px, free, ~1:1, 60fps)"):
            self.assertTrue(main.match(t) and title.match(t), t)
        # Negative controls: message boxes and the input dialog carry the bare app title; tools have their own.
        for t in ("ShaderGlass", "Choose Shader", "Shader Parameters: crt-geom"):
            self.assertIsNone(main.match(t), t)
        self.assertTrue(title.match("ShaderGlass"), "the static rules still match the bare title at map time")
        self.assertIn(self.sg["wine"]["gpu"], ("compositor", "any"))

    def test_hyprglass_glass_resamples_every_output_frame(self) -> None:
        g = self.d["desktop"]["hyprland"]["hyprglass"]["config"]
        fr = self.d["desktop"].get("framerate", {})
        if fr.get("enabled"):
            for group in ("windows", "layers"):
                self.assertNotIn("live_resample_fps", g.get(group, {}),
                                 f"{group}: superseded by [desktop.framerate] glass rate G")
        else:
            for group in ("windows", "layers"):
                self.assertEqual(g.get(group, {}).get("live_resample_fps"), 0,
                                 f"[desktop.hyprland.hyprglass.config.{group}].live_resample_fps must be 0 (one re-sample per output frame)")
        self.assertTrue(g["layers"]["live_resample"])
        self.assertNotEqual(g.get("windows", {}).get("live_resample"), False)

    def test_one_glass_material(self) -> None:
        g = self.d["desktop"]["hyprland"]["hyprglass"]
        self.assertEqual(self.sg["window"]["glass_preset"], g["config"]["default_preset"])
        self.assertIn(self.sg["window"]["glass_preset"], g["presets"])

    def test_capture_policy_never_offers_a_screen(self) -> None:
        c = self.sg["capture"]
        self.assertEqual(c["source_types"], "window", "a monitor source would put the pane in its own input")
        self.assertIn(c["picker"], ("mios", "stock"))
        self.assertGreaterEqual(c["request_ttl_ms"], 1000)
        self.assertLessEqual(c["request_ttl_ms"], 60000, "a one-shot request must not outlive one launch")

    def test_launcher_and_keymap_follow_enabled(self) -> None:
        ln = self.d["desktop"]["launchers"]["mios-shaderglass"]
        self.assertEqual(ln.get("no_display", False), not self.sg["enabled"], "drawer entry and projection switch together")
        self.assertTrue(ln["exec_cmd"].startswith(self.sg["binary"] + " "))
        self.assertNotIn("startup_wm_class", ln, "the pane must never be taken for the launcher's window")
        entry = os.path.join(ROOT, "usr", "share", "applications", "mios-shaderglass.desktop")
        self.assertTrue(os.path.isfile(entry))
        gen = os.environ.get("MIOS_GEN_BIN") or "/usr/bin/mios-gen"
        if not os.access(gen, os.X_OK):
            self.skipTest("no mios-gen to render the drawer entry")
        with tempfile.TemporaryDirectory() as t:
            os.makedirs(os.path.join(t, "usr", "share", "applications"))
            r = run([gen, "render-desktop", "--root", t], env={**os.environ, "MIOS_TOML": SSOT}, cwd=t)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(read(os.path.join(t, "usr", "share", "applications", "mios-shaderglass.desktop")), read(entry),
                             "usr/share/applications/mios-shaderglass.desktop drifted from [desktop.launchers.mios-shaderglass]")

    def test_keymap_lists_the_bindings_only_while_enabled(self) -> None:
        tool = os.path.join(ROOT, "usr", "libexec", "mios", "mios-sync-theme")
        text = read(SSOT)
        for on in (False, True):
            with tempfile.TemporaryDirectory() as t:
                vendor = os.path.join(t, "mios.toml")
                with open(vendor, "w", encoding="utf-8") as f:
                    f.write(with_enabled(text, on))
                env = {**os.environ, "MIOS_VENDOR_TOML": vendor, "MIOS_HOST_TOML": os.path.join(t, "none"),
                       "MIOS_USER_TOML": os.path.join(t, "none"), "MIOS_THEME_OUT_DIR": os.path.join(t, "out")}
                r = run(["bash", tool, "--bridge-only"], env=env)
                self.assertEqual(r.returncode, 0, r.stderr)
                with open(os.path.join(t, "out", "theme.json"), encoding="utf-8") as f:
                    keymap = json.load(f)["keymap"]
                mine = [k for k in keymap if k["label"].startswith("ShaderGlass")]
                if on:
                    self.assertEqual(sorted(combo(k["keys"]) for k in mine), sorted(combo(b["key"]) for b in self.sg["bindings"]))
                else:
                    self.assertEqual(mine, [], "a disabled overlay must not advertise its keys")

    def test_bindings_are_free_and_plain(self) -> None:
        h = self.d["desktop"]["hyprland"]
        taken = {combo(b["key"]) for b in h["bindings"]}
        taken |= {combo(b["key"]) for b in self.d["desktop"].get("screenshot", {}).get("bindings", [])}
        taken.add(combo(h["hypr3d"]["toggle_key"]))
        for b in self.sg["bindings"]:
            self.assertNotIn(combo(b["key"]), taken, b["key"])
            self.assertRegex(b["args"], r"^[a-z][a-z-]*$")
            self.assertTrue(b["label"].startswith("ShaderGlass"))

    def test_packages_are_wired_and_exist_upstream_names(self) -> None:
        pk = self.d["packages"]["shaderglass"]
        self.assertTrue({"wine-core", "wine-dxvk"} <= set(pk["pkgs"]))
        self.assertNotIn("wine-staging", pk["pkgs"], "Fedora 44 ships no wine-staging")
        step = read(os.path.join(ROOT, "automation", "65-bake-hyprland.sh"))
        self.assertIn('install_packages "shaderglass"', step)

    def test_glue_scripts_parse_and_fall_back(self) -> None:
        picker = self.d["desktop"]["hyprland"]["xdph"]["custom_picker_binary"]
        glue = [os.path.join(ROOT, picker.lstrip("/")), os.path.join(ROOT, self.sg["binary"].lstrip("/"))]
        for g in glue:
            self.assertTrue(os.access(g, os.X_OK), g)
            self.assertEqual(run(["bash", "-n", g]).returncode, 0, g)
            if shutil.which("shellcheck"):
                r = run(["shellcheck", g])
                self.assertEqual(r.returncode, 0, r.stdout)
        p = read(glue[0])
        probe, stock = p.index("shaderglass pick --probe"), p.index('exec hyprland-share-picker "$@"')
        self.assertLess(probe, stock, "the stock picker is the fallback, after the probe")
        self.assertIn('exec "$MIOSD" shaderglass pick "$@"', p)

    def test_release_tree_is_never_tracked(self) -> None:
        if not os.path.isdir(os.path.join(ROOT, ".git")):
            self.skipTest("not a git work tree")
        inst = self.sg["install_dir"].lstrip("/")
        for path in (f"{inst}/ShaderGlass.exe", f"{inst}/WineCap.dll", f"{inst}/INSTALLED.json", f"{inst}.prev/LICENSE"):
            self.assertEqual(run(["git", "-C", ROOT, "check-ignore", "-q", "--no-index", path]).returncode, 0, f"{path} is not ignored")
        for path in (self.sg["binary"].lstrip("/"), "usr/libexec/mios/mios-share-picker", "usr/share/applications/mios-shaderglass.desktop"):
            self.assertEqual(run(["git", "-C", ROOT, "check-ignore", "-q", "--no-index", path]).returncode, 1, f"{path} is ignored")

    def test_winecap_patch_and_builder_agree_with_miosd(self) -> None:
        wc = self.sg["winecap"]
        self.assertEqual(wc["source"], "build", "restore tokens need the patched WineCap")
        for p in wc["patches"]:
            text = read(os.path.join(ROOT, p))
            self.assertTrue(text.startswith("diff --git a/WineCap/"), p)
            for needle in ("MIOS_WINECAP_TOKEN_FILE", "MIOS_WINECAP_SOURCE_TYPES", "MIOS_WINECAP_CURSOR",
                           '"persist_mode", g_variant_new_uint32(2)', '"restore_token"'):
                self.assertIn(needle, text, needle)
        cf = read(os.path.join(self.sg["winecap"]["builder"]["containerfile_dir"].replace("/usr/share", os.path.join(ROOT, "usr", "share"), 1), "Containerfile"))
        rel = read(os.path.join(ROOT, "src", "mios-rs", "miosd", "src", "shaderglass", "release.rs"))
        wine = read(os.path.join(ROOT, "src", "mios-rs", "miosd", "src", "shaderglass", "wine.rs"))
        for arg in ("MIOS_WINECAP_BUILD_BASE", "MIOS_WINECAP_BUILD_PACKAGES"):
            self.assertIn(f"ARG {arg}", cf)
            self.assertIn(f"{arg}=", rel)
        for env in ("MIOS_WINECAP_TOKEN_FILE", "MIOS_WINECAP_SOURCE_TYPES", "MIOS_WINECAP_CURSOR"):
            self.assertIn(env, wine, f"miosd never sets {env}")
        self.assertIn('make -C /src all', cf)
        # WineCap/Makefile: winegcc (wine-devel, which drives g++ even for C), libpipewire-0.3, gio-unix-2.0, make.
        need = {"wine-devel", "pipewire-devel", "glib2-devel", "gcc", "gcc-c++", "make", "pkgconf-pkg-config"}
        self.assertLessEqual(need, set(self.sg["winecap"]["builder"]["packages"]))


# ---------------------------------------------------------------- 2. projection


def gen_with_shaderglass() -> str | None:
    gen = os.environ.get("MIOS_GEN_BIN") or "/usr/bin/mios-gen"
    if not os.access(gen, os.X_OK):
        return None
    with tempfile.TemporaryDirectory() as t:
        out = render(gen, t, with_enabled(read(SSOT), True))
    return gen if out and 'mios_section("shaderglass"' in out else None


def render(gen: str, t: str, text: str) -> str | None:
    for d in ("usr/share/mios/hyprland", "etc/skel/.config/hypr", "usr/share/wayland-sessions"):
        os.makedirs(os.path.join(t, d), exist_ok=True)
    with open(os.path.join(t, "usr/share/mios/mios.toml"), "w", encoding="utf-8") as f:
        f.write(text)
    r = run([gen, "render-hyprland", "--root", t], cwd=t)
    if r.returncode != 0:
        raise AssertionError(r.stderr)
    return read(os.path.join(t, "usr/share/mios/hyprland/hyprland.lua"))


class Projection(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.gen = gen_with_shaderglass()

    def setUp(self) -> None:
        if not self.gen:
            self.skipTest("mios-gen without shaderglass_lua (set MIOS_GEN_BIN to the staged build)")

    def test_rules_and_binds_only_while_enabled(self) -> None:
        text = read(SSOT)
        with tempfile.TemporaryDirectory() as t:
            off = render(self.gen, t, with_enabled(text, False))
        self.assertNotIn("shaderglass", off, "disabled: nothing projected")
        with tempfile.TemporaryDirectory() as t:
            on = render(self.gen, t, with_enabled(text, True))
            if shutil.which("luajit"):
                r = run(["luajit", "-bl", os.path.join(t, "usr/share/mios/hyprland/hyprland.lua")])
                self.assertEqual(r.returncode, 0, r.stderr)
        section = on[on.index('mios_section("shaderglass", function()'):]
        section = section[: section.index("\nend)\n")]
        pane = next(l for l in section.splitlines() if '["name"] = "mios-shaderglass"' in l)
        for kv in ('["float"] = true', '["pin"] = true', '["no_initial_focus"] = true', '["focus_on_activate"] = false',
                   '["no_anim"] = true', '["tag"] = "+mios-shaderglass"', '["class"] = "^(shaderglass\\\\.exe)$"'):
            self.assertIn(kv, pane, kv)
        self.assertNotIn("no_focus", pane)
        self.assertIn('["match"] = {["tag"] = "mios-shaderglass-passthrough"}, ["no_focus"] = true', section)
        self.assertIn('["tag"] = "+hyprglass_preset_mios"', section)
        for b in ssot()["desktop"]["shaderglass"]["bindings"]:
            self.assertIn(f'hl.bind("{b["key"]}", hl.dsp.exec_cmd("/usr/bin/mios-shaderglass {b["args"]}"))', section)
        # Negative control: the rules are gone again when disabled, everything else is identical.
        self.assertEqual(off, on.replace(section + "\nend)\n", ""), "enabling must add exactly the shaderglass section")

    def test_live_resample_fps_reaches_hg_config(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            out = render(self.gen, t, with_fps(read(SSOT)))
        cfg = next(l for l in out.splitlines() if l.startswith(" hg.config({") and "default_preset" in l)
        self.assertIn('["windows"] = {["live_resample_fps"] = 0}', cfg)
        layers = re.search(r'\["layers"\] = \{[^}]*\}', cfg).group(0)
        self.assertIn('["live_resample_fps"] = 0', layers)
        bad = with_fps(read(SSOT)).replace("[desktop.hyprland.hyprglass.config.windows]\nlive_resample_fps = 0",
                                           "[desktop.hyprland.hyprglass.config.windows]\nlive_resample_fps = -1", 1)
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaises(AssertionError) as e:
                render(self.gen, t, bad)
        self.assertIn("live_resample_fps", str(e.exception))


# ---------------------------------------------------------------- 3. miosd


def miosd_with_shaderglass() -> str | None:
    b = os.environ.get("MIOS_SHADERGLASS_MIOSD") or "/usr/bin/miosd"
    if not os.access(b, os.X_OK):
        return None
    return b if run([b, "shaderglass", "--help"]).returncode == 0 else None


def fake_winecap() -> bytes:
    elf = bytearray(128)
    elf[:4] = b"\x7fELF"
    elf[4], elf[5], elf[16], elf[18] = 2, 1, 3, 62
    return bytes(elf) + b"\0libpipewire-0.3.so.0\0__wine_spec_nt_header\0"


def release(tag: str, pre: bool, published: str, asset: str, digest: str | None, repo: str = "mausimus/ShaderGlass") -> dict:
    return {"tag_name": tag, "prerelease": pre, "draft": False, "published_at": published,
            "html_url": f"https://github.com/{repo}/releases/tag/{tag}",
            "assets": [{"name": asset, "size": 1, "digest": f"sha256:{digest}" if digest else None,
                        "browser_download_url": f"https://github.com/{repo}/releases/download/{tag}/{asset}"}]}


class Miosd(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bin = miosd_with_shaderglass()

    def setUp(self) -> None:
        if not self.bin:
            self.skipTest("miosd without `shaderglass` (set MIOS_SHADERGLASS_MIOSD to the staged build)")
        self.tmp = tempfile.TemporaryDirectory()
        t = self.t = self.tmp.name
        os.makedirs(os.path.join(t, "root/usr/share/mios"))
        text = read(SSOT).replace('stock_picker = "hyprland-share-picker"', 'stock_picker = "/usr/bin/echo"', 1)
        self.vendor = os.path.join(t, "root/usr/share/mios/mios.toml")
        with open(self.vendor, "w", encoding="utf-8") as f:
            f.write(text)
        sg = ssot()["desktop"]["shaderglass"]
        for p in sg["winecap"]["patches"]:
            os.makedirs(os.path.dirname(os.path.join(t, "root", p)), exist_ok=True)
            shutil.copy(os.path.join(ROOT, p), os.path.join(t, "root", p))
        for d in ("home", "run", "cfg", "data", "cache", "state", "work"):
            os.makedirs(os.path.join(t, d))
        os.chmod(os.path.join(t, "run"), 0o700)
        self.env = {k: v for k, v in os.environ.items() if k not in ("HYPRLAND_INSTANCE_SIGNATURE", "WAYLAND_DISPLAY", "DISPLAY", "MIOS_TOML")}
        self.env.update({"MIOS_ROOT": os.path.join(t, "root"), "MIOS_VENDOR_TOML": self.vendor,
                         "MIOS_HOST_TOML": os.path.join(t, "none"), "MIOS_USER_TOML": os.path.join(t, "none"),
                         "HOME": os.path.join(t, "home"), "XDG_RUNTIME_DIR": os.path.join(t, "run"),
                         "XDG_CONFIG_HOME": os.path.join(t, "cfg"), "XDG_DATA_HOME": os.path.join(t, "data"),
                         "XDG_CACHE_HOME": os.path.join(t, "cache"), "XDG_STATE_HOME": os.path.join(t, "state"),
                         "MIOS_SHADERGLASS_WORK": os.path.join(t, "work")})

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def sg(self, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
        return run([self.bin, "shaderglass", *args], env=env or self.env, cwd=self.t)

    def write_json(self, name: str, v) -> str:
        p = os.path.join(self.t, name)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(v, f)
        return p

    def test_resolve_follows_the_channel_against_a_mocked_api(self) -> None:
        rels = [release("v1.3.0", False, "2026-03-19T03:14:06Z", "ShaderGlass-1.3.0-win-x64.zip", "d6" * 32),
                release("v1.3.1", True, "2026-10-18T07:37:59Z", "ShaderGlass-1.3.1-beta-win-x64.zip", "16" * 32),
                {"tag_name": "v9.0.0", "draft": True, "prerelease": False, "published_at": "2027-01-01T00:00:00Z", "assets": []}]
        f = self.write_json("releases.json", rels)
        r = self.sg("resolve", "--releases-json", f)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["tag"], "v1.3.1", "newest includes pre-releases, never drafts")
        r = self.sg("resolve", "--releases-json", f, "--channel", "stable")
        self.assertEqual(json.loads(r.stdout)["tag"], "v1.3.0")
        rels.append(release("v1.4.0", True, "2026-11-01T00:00:00Z", "ShaderGlass-1.4.0-win-x64.zip", None))
        r = self.sg("resolve", "--releases-json", self.write_json("r2.json", rels))
        self.assertNotEqual(r.returncode, 0, "a newest release without a digest must not fall back to an older one")
        self.assertIn("no sha256 digest", r.stderr)
        self.assertNotEqual(self.sg("resolve", "--releases-json", f, "--channel", "nightly").returncode, 0)

    def make_release(self, license_text: str = "GNU GENERAL PUBLIC LICENSE\n Version 3, 29 June 2007\n") -> tuple[str, str]:
        z = os.path.join(self.t, "ShaderGlass-9.9.9-win-x64.zip")
        with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("ShaderGlass.exe", b"MZ" + os.urandom(4096))
            zf.writestr("LICENSE", license_text)
            zf.writestr("README.txt", "readme")
            zf.writestr("MANUAL.pdf", b"%PDF-1.4")
            zf.writestr("ignored-extra.txt", "never installed")
        digest = hashlib.sha256(read_bytes(z)).hexdigest()
        rels = self.write_json("rels.json", [release("v9.9.9", False, "2030-01-01T00:00:00Z", os.path.basename(z), digest)])
        return z, rels

    def test_install_is_digest_verified_recorded_and_atomic(self) -> None:
        z, rels = self.make_release()
        wc = os.path.join(self.t, "WineCap.dll")
        with open(wc, "wb") as f:
            f.write(fake_winecap())
        dest = os.path.join(self.t, "inst", "shaderglass")
        os.makedirs(os.path.dirname(dest))
        args = ["install", "--releases-json", rels, "--archive", z, "--winecap", wc, "--dest", dest]
        r = self.sg(*args)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("resolved mausimus/ShaderGlass v9.9.9", r.stderr, "the resolved release is logged")
        for name in ("ShaderGlass.exe", "LICENSE", "README.txt", "MANUAL.pdf", "WineCap.dll", "INSTALLED.json"):
            st = os.stat(os.path.join(dest, name))
            self.assertEqual(st.st_mode & 0o777, 0o644, name)
        self.assertFalse(os.path.exists(os.path.join(dest, "ignored-extra.txt")), "only the SSOT's install_files")
        self.assertEqual(os.stat(dest).st_mode & 0o777, 0o755)
        rec = json.loads(read(os.path.join(dest, "INSTALLED.json")))
        self.assertEqual(rec["release"]["tag"], "v9.9.9")
        self.assertEqual(rec["release"]["asset"]["sha256"], hashlib.sha256(read_bytes(z)).hexdigest())
        self.assertEqual(rec["files"]["WineCap.dll"], hashlib.sha256(fake_winecap()).hexdigest())
        self.assertEqual(rec["winecap"]["source"], "build")
        self.assertEqual([p["path"] for p in rec["winecap"]["patches"]], ssot()["desktop"]["shaderglass"]["winecap"]["patches"])
        # Same release again: nothing to do; --force reinstalls and keeps the previous tree.
        r = self.sg(*args)
        self.assertIn("already installed", r.stdout)
        r = self.sg(*args, "--force")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.isdir(dest + ".prev"))
        # The stage is re-verified by the root step: a file changed after staging is refused.
        stages = sorted(os.path.join(self.t, "work", "stage", s, "tree") for s in os.listdir(os.path.join(self.t, "work", "stage")))
        with open(os.path.join(stages[-1], "WineCap.dll"), "ab") as f:
            f.write(b"tampered")
        r = self.sg("install", "--from-stage", stages[-1], "--dest", dest)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("INTEGRITY", r.stderr)
        self.assertEqual(read_bytes(os.path.join(dest, "WineCap.dll")), fake_winecap(), "installed tree untouched")

    def test_install_refuses_a_tampered_archive_a_changed_licence_and_a_non_winelib_winecap(self) -> None:
        z, rels = self.make_release()
        wc = os.path.join(self.t, "WineCap.dll")
        with open(wc, "wb") as f:
            f.write(fake_winecap())
        dest = os.path.join(self.t, "inst", "shaderglass")
        os.makedirs(os.path.dirname(dest))
        with open(z, "ab") as f:
            f.write(b"\0")
        r = self.sg("install", "--releases-json", rels, "--archive", z, "--winecap", wc, "--dest", dest)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("INTEGRITY", r.stderr)
        self.assertFalse(os.path.exists(dest))
        z, rels = self.make_release("MIT License\n")
        r = self.sg("install", "--releases-json", rels, "--archive", z, "--winecap", wc, "--dest", dest)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("LICENSE no longer contains", r.stderr)
        z, rels = self.make_release()
        with open(wc, "wb") as f:
            f.write(b"MZ" + b"\0" * 200)
        r = self.sg("install", "--releases-json", rels, "--archive", z, "--winecap", wc, "--dest", dest)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("ELF", r.stderr)
        self.assertFalse(os.path.exists(dest))

    def test_picker_answers_its_one_shot_request_then_hands_over_to_the_stock_picker(self) -> None:
        picks = os.path.join(self.t, "run", "mios", "shaderglass", "picks")
        os.makedirs(picks)
        target = 0x55D0C1A2B3C0
        listing = f"4242[HC>]kitty[HT>]~/src[HE>]{target}[HA>]99[HC>]zen[HT>]docs[HE>]{target + 1}[HA>]"
        env = {**self.env, "XDPH_WINDOW_SHARING_LIST": listing}

        def request(name: str, expires: int, pid: int) -> None:
            with open(os.path.join(picks, name), "w", encoding="utf-8") as f:
                json.dump({"target": hex(target), "expires_ms": expires, "supervisor_pid": pid}, f)

        now = int(time.time() * 1000)
        request("00000000000000000002-1.json", now + 60000, os.getpid())
        r = self.sg("pick", "--allow-token", env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "[SELECTION]r/window:4242")
        self.assertEqual(os.listdir(picks), [], "one shot")
        # No request, an expired one, or one whose writer is gone: the stock picker gets the same arguments.
        for name, exp, pid in (("x", 0, 0), ("00000000000000000003-1.json", now - 1, os.getpid()), ("00000000000000000004-1.json", now + 60000, 2 ** 22 + 7)):
            if name != "x":
                request(name, exp, pid)
            r = self.sg("pick", "--allow-token", env=env)
            self.assertEqual(r.stdout.strip(), "--allow-token", f"{name}: expected the stock picker (echo) to run")
        self.assertEqual(self.sg("pick", "--probe").stdout.strip(), "ok")

    def test_portal_config_projection_and_check(self) -> None:
        r = self.sg("portal-config", "--write")
        self.assertEqual(r.returncode, 0, r.stderr)
        path = os.path.join(self.t, "cfg", "hypr", "xdph.conf")
        text = read(path)
        x = ssot()["desktop"]["hyprland"]["xdph"]
        self.assertIn(f"custom_picker_binary = {x['custom_picker_binary']}", text)
        self.assertIn(f"allow_token_by_default = {str(x['allow_token_by_default']).lower()}", text)
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o644)
        self.assertEqual(self.sg("portal-config", "--check").returncode, 0)
        with open(path, "a", encoding="utf-8") as f:
            f.write("# hand edit\n")
        self.assertNotEqual(self.sg("portal-config", "--check").returncode, 0, "a hand-edited projection is drift")

    def test_argument_handling_and_refusals_before_any_compositor_contact(self) -> None:
        for bad in (["frobnicate"], ["attach", "--window"], ["install", "--from-stage", "/x", "--archive", "/y"]):
            self.assertEqual(self.sg(*bad).returncode, 2, bad)
        # Disabled in the SSOT (the vendor default): attach refuses before connecting to anything.
        off = with_enabled(read(self.vendor), False)
        with open(self.vendor, "w", encoding="utf-8") as f:
            f.write(off)
        r = self.sg("attach")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("disabled", r.stderr)
        # Enabled but not installed: still refused before any compositor contact.
        with open(self.vendor, "w", encoding="utf-8") as f:
            f.write(with_enabled(off, True).replace('install_dir = "/usr/lib/mios/shaderglass"',
                                                     f'install_dir = "{self.t}/none/shaderglass"', 1))
        r = self.sg("attach", "--window", "0x1")
        self.assertIn("not installed", r.stderr)
        # No supervisor: detach is a no-op, status says so.
        r = self.sg("detach")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "no ShaderGlass pane is running"))
        st = json.loads(self.sg("status").stdout)
        self.assertIsNone(st["supervisor"])
        # This test runs with a private XDG_RUNTIME_DIR, i.e. as a non-primary session: its own supervisor unit
        # instance, so it never stops or replaces the operator's app-mios-org.mios.ShaderGlass@overlay.service.
        self.assertRegex(st["unit"], r"^app-mios-org\.mios\.ShaderGlass@overlay-[0-9a-f]{8}\.service$")


# ---------------------------------------------------------------- 4. upstream


def clone(url: str, dest: str, ref: str | None = None) -> bool:
    args = ["git", "-c", "advice.detachedHead=false", "clone", "--quiet", "--depth", "1"]
    if ref:
        args += ["--branch", ref]
    return run(args + [url, dest], timeout=300).returncode == 0


def fps_registered(src: str) -> list[str]:
    """Why hyprglass at `src` would not honour live_resample_fps = 0 for windows and layers (empty = it does)."""
    errors = []
    hpp = read(os.path.join(src, "src", "PluginConfig.hpp"))
    cpp = read(os.path.join(src, "src", "PluginConfig.cpp"))
    for group, key in (("windows", "WINDOWS_LIVE_RESAMPLE_FPS"), ("layers", "LAYERS_LIVE_RESAMPLE_FPS")):
        if not re.search(rf'{key}\s*=\s*"plugin:hyprglass:{group}:live_resample_fps"', hpp):
            errors.append(f"no option plugin:hyprglass:{group}:live_resample_fps")
        if not re.search(rf"addConfigValue<Config::Values::Int>\(handle, ConfigKeys::{key},", cpp):
            errors.append(f"{key} not registered as an int")
    if '"plugin:hyprglass:" + dotted' not in cpp or "std::replace(optionPath.begin(), optionPath.end(), '.', ':')" not in cpp:
        errors.append("hg.config no longer maps nested tables to plugin:hyprglass:<group>:<key>")
    for f, field in (("GlassDecoration.cpp", "windowsLiveResampleFps"), ("GlassLayerSurface.cpp", "layersLiveResampleFps")):
        body = read(os.path.join(src, "src", f))
        # v0.10.0: `if (fps > 0 && now - last < 1/fps) return;` -- v0.11.0+: `if (fps > 0) { ... defer ... }`.
        if field not in body or not re.search(r"\bfps\s*>\s*0\b", body):
            errors.append(f"{f}: the throttle no longer treats 0 as uncapped")
    return errors


@unittest.skipIf(OFFLINE, "MIOS_TEST_OFFLINE=1")
class Upstream(unittest.TestCase):
    def test_winecap_patch_applies_to_shaderglass_head_and_newest_release(self) -> None:
        sg = ssot()["desktop"]["shaderglass"]
        repo = sg["source_repository"]
        with tempfile.TemporaryDirectory() as t:
            if not clone(repo, os.path.join(t, "head")):
                self.skipTest(f"{repo} unreachable")
            tags = run(["git", "ls-remote", "--tags", "--refs", repo], timeout=120).stdout.split()
            versions = sorted({x.rsplit("/", 1)[1] for x in tags if x.startswith("refs/tags/")},
                              key=lambda v: [int(n) for n in re.findall(r"\d+", v)])
            self.assertTrue(versions, "no release tags")
            newest = versions[-1]
            self.assertTrue(clone(repo, os.path.join(t, "rel"), newest), newest)
            for tree in ("head", "rel"):
                for p in sg["winecap"]["patches"]:
                    r = run(["git", "-C", os.path.join(t, tree), "apply", "--check", os.path.join(ROOT, p)])
                    self.assertEqual(r.returncode, 0, f"{p} on {tree} ({newest}): {r.stderr}")
            # Negative control: the same check fails on a patch whose context no longer exists.
            bad = os.path.join(t, "bad.patch")
            with open(bad, "w", encoding="utf-8") as f:
                f.write(read(os.path.join(ROOT, sg["winecap"]["patches"][0])).replace(" #include <gio/gunixfdlist.h>", " #include <gio/gunixfdlist2.h>", 1))
            self.assertNotEqual(run(["git", "-C", os.path.join(t, "head"), "apply", "--check", bad]).returncode, 0)

    def test_hyprglass_honours_live_resample_fps_zero(self) -> None:
        g = ssot()["desktop"]["hyprland"]["hyprglass"]
        with tempfile.TemporaryDirectory() as t:
            head = os.path.join(t, "head")
            if not clone(g["source_repository"], head):
                self.skipTest("hyprglass unreachable")
            self.assertEqual(fps_registered(head), [], "hyprglass HEAD")
            # The commit the build takes for the held Hyprland release (hyprpm.toml commit_pins of HEAD).
            ls = run(["git", "ls-remote", g["hyprland_repository"], f"refs/tags/v{g['hyprland_version']}^{{}}",
                      f"refs/tags/v{g['hyprland_version']}"], timeout=120).stdout.splitlines()
            hl = next((l.split()[0] for l in ls if l.endswith("^{}")), ls[0].split()[0] if ls else None)
            pins = tomllib.loads(read(os.path.join(head, "hyprpm.toml")))["repository"].get("commit_pins", [])
            pinned = next((p for h, p in pins if h == hl), None)
            if pinned:
                r = run(["git", "-C", head, "fetch", "--quiet", "--depth", "1", "origin", pinned], timeout=300)
                self.assertEqual(r.returncode, 0, r.stderr)
                pin = os.path.join(t, "pin")
                run(["git", "-C", head, "worktree", "add", "--quiet", "--detach", pin, pinned])
                self.assertEqual(fps_registered(pin), [], f"hyprglass {pinned} (pinned for Hyprland {g['hyprland_version']})")
            # Negative control: a renamed option is caught.
            mut = os.path.join(t, "mut")
            shutil.copytree(head, mut, ignore=shutil.ignore_patterns(".git"))
            hpp = os.path.join(mut, "src", "PluginConfig.hpp")
            with open(hpp, "w", encoding="utf-8") as f:
                f.write(read(os.path.join(head, "src", "PluginConfig.hpp")).replace("windows:live_resample_fps", "windows:resample_rate"))
            self.assertTrue(fps_registered(mut))



@unittest.skipUnless(os.environ.get("MIOS_SHADERGLASS_LIVE_RENDER") == "1",
                     "live desktop rendering is opt-in (MIOS_SHADERGLASS_LIVE_RENDER=1)")
class LiveRendering(unittest.TestCase):
    """Check real rendered coverage, not just equal compositor rectangles."""

    def test_four_corners_remain_visible_after_wide_and_portrait_resizes(self):
        import sys
        import uuid
        from pathlib import Path
        from PIL import Image

        binary = os.environ.get("MIOS_SHADERGLASS_MIOSD") or "/usr/bin/miosd"
        evidence = Path(os.environ.get("MIOS_SHADERGLASS_RENDER_EVIDENCE") or
                        ("/tmp/mios-render-probe-" + uuid.uuid4().hex[:8]))
        evidence.mkdir(mode=0o700)
        evidence.chmod(0o700)
        name = "mios-render-probe-" + uuid.uuid4().hex[:8]
        target = None
        child = None
        log = (evidence / "mios-window.log").open("w")

        def checked(args):
            result = run(args, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout

        def clients():
            return json.loads(checked(["hyprctl", "-j", "clients"]))

        def wait_for(predicate, timeout=12):
            until = time.monotonic() + timeout
            while time.monotonic() < until:
                value = predicate()
                if value:
                    return value
                time.sleep(0.1)
            self.fail("render probe did not reach its expected state")

        def corner_counts(path):
            counts = dict(red=0, blue=0, green=0, yellow=0)
            with Image.open(path) as image:
                for r, g, b in image.convert("RGB").get_flattened_data():
                    counts["red"] += r > 50 and r > 3*g and r > 3*b
                    counts["blue"] += b > 70 and b > 2*r and g > 2*r and b > g
                    counts["green"] += g > 50 and g > 3*r and g > 3*b
                    counts["yellow"] += r > 70 and g > 70 and b < min(r, g)/2 and 0.5 < r/g < 2
            return counts

        chart = """import os,signal
def draw(*unused):
 cols,rows=os.get_terminal_size()
 esc=chr(27);s=esc+'[0m'+esc+'[2J'+esc+'[H'
 for row,col,color in ((3,3,(255,25,25)),(3,max(3,cols-12),(15,120,255)),(max(3,rows-5),3,(25,255,25)),(max(3,rows-5),max(3,cols-12),(255,255,25))):
  for dy in (0,1,2):
   s+=esc+f'[{row+dy};{col}H'+esc+'[48;2;%d;%d;%dm'%color+' '*10+esc+'[0m'
 os.write(1,s.encode())
signal.signal(signal.SIGWINCH,draw)
draw()
while True:signal.pause()
"""
        results = []
        try:
            child = subprocess.Popen(
                ["alacritty", "--class", name, "--title", "MiOS render probe",
                 "-o", "window.opacity=1.0", "-e", sys.executable, "-c", chart],
                stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            window = wait_for(lambda: next((c for c in clients() if c.get("class") == name), None))
            target = window["address"]
            window_ref = "address:" + target
            checked(["hyprctl", "eval",
                     f"hl.dispatch(hl.dsp.window.float({{action='enable',window='{window_ref}'}}))"])
            checked(["hyprctl", "eval",
                     f"hl.dispatch(hl.dsp.window.resize({{x=1100,y=700,window='{window_ref}'}}));"
                     f"hl.dispatch(hl.dsp.window.move({{x=400,y=180,window='{window_ref}'}}))"])
            time.sleep(0.8)
            checked([binary, "shaderglass", "attach", "--window", target])
            time.sleep(5)  # Allow capture, swapchain, and shader to become visible.
            # This private target is the only window the test ever detaches/closes.
            for label, width, height, x, y in (("wide", 1100, 700, 400, 180),
                                               ("portrait", 700, 950, 80, 60)):
                checked(["hyprctl", "eval",
                         f"hl.dispatch(hl.dsp.window.resize({{x={width},y={height},window='{window_ref}'}}));"
                         f"hl.dispatch(hl.dsp.window.move({{x={x},y={y},window='{window_ref}'}}))"])
                time.sleep(3)  # Do not sample the unshaded frame while resizing.
                image = evidence / ("mios-" + label + ".png")
                counts = {}
                rendered = False
                until = time.monotonic() + 12
                while time.monotonic() < until:
                    current = next((c for c in clients() if c.get("address") == target), None)
                    self.assertIsNotNone(current, "the owned probe window disappeared")
                    at, size = current["at"], current["size"]
                    checked(["grim", "-g", f"{at[0]},{at[1]} {size[0]}x{size[1]}", str(image)])
                    counts = corner_counts(image)
                    if all(value >= 50 for value in counts.values()):
                        # Require the shader pane to have mapped, so the unshaded
                        # source cannot make this check pass during startup.
                        panes = (json.loads(checked([binary, "shaderglass", "status"])).get("supervisor") or {}).get("panes", [])
                        if any(p.get("target") == target and p.get("window") for p in panes):
                            rendered = True
                            break
                    time.sleep(0.2)
                results.append(dict(layout=label, counts=counts, image=str(image)))
                (evidence / "mios-render-result.json").write_text(json.dumps(results, indent=2))
                self.assertTrue(rendered and all(value >= 50 for value in counts.values()),
                                f"{label}: missing captured corners: {counts}; evidence={evidence}")
            # Setup temporarily restores input; returning to overlay must fit again.
            for entering in (True, False):
                reply = json.loads(checked([binary, "shaderglass", "setup", "--window", target]))
                self.assertEqual(reply.get("setup"), entering)
                time.sleep(2)
                panes = (json.loads(checked([binary, "shaderglass", "status"])).get("supervisor") or {}).get("panes", [])
                pane = next(p for p in panes if p.get("target") == target)
                self.assertEqual(pane["state"], "setup" if entering else "overlay")
                overlay = next(c for c in clients() if c.get("address") == pane["window"])
                passthrough = any(tag.rstrip("*") == "mios-shaderglass-passthrough"
                                  for tag in overlay.get("tags", []))
                self.assertEqual(passthrough, not entering)
            current = next(c for c in clients() if c.get("address") == target)
            at, size = current["at"], current["size"]
            image = evidence / "mios-resumed.png"
            checked(["grim", "-g", f"{at[0]},{at[1]} {size[0]}x{size[1]}", str(image)])
            self.assertTrue(all(value >= 50 for value in corner_counts(image).values()),
                            f"resumed overlay lost a captured corner; evidence={evidence}")
            # Closing the owned source must close its overlay without a global stop.
            overlay_address = pane["window"]
            child.terminate()
            child.wait(timeout=10)
            wait_for(lambda: not any(c.get("address") == overlay_address for c in clients()))
        finally:
            if target:
                run([binary, "shaderglass", "detach", "--window", target], timeout=30)
            if child is not None:
                if child.poll() is None:
                    child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
            log.close()



@unittest.skipUnless(os.environ.get("MIOS_SHADERGLASS_LIVE_RENDER") == "1",
                     "live desktop capture is opt-in (MIOS_SHADERGLASS_LIVE_RENDER=1)")
class LiveCaptureIsolation(unittest.TestCase):
    """A restored capture must not redirect the next window's picker."""

    def test_restored_pane_does_not_steal_the_next_target(self):
        import sys
        import uuid
        from pathlib import Path
        from PIL import Image, ImageStat

        binary = os.environ.get("MIOS_SHADERGLASS_MIOSD") or "/usr/bin/miosd"
        evidence = Path(os.environ.get("MIOS_SHADERGLASS_RENDER_EVIDENCE") or
                        ("/tmp/mios-capture-isolation-" + uuid.uuid4().hex[:8]))
        evidence.mkdir(mode=0o700)
        evidence.chmod(0o700)
        suffix = uuid.uuid4().hex[:8]
        targets, children, records = [], [], []
        log = (evidence / "mios-window.log").open("w")

        def checked(args):
            result = run(args, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout

        def clients():
            return json.loads(checked(["hyprctl", "-j", "clients"]))

        def panes():
            return (json.loads(checked([binary, "shaderglass", "status"])).get("supervisor") or {}).get("panes", [])

        def wait_for(predicate, timeout=20):
            until = time.monotonic() + timeout
            while time.monotonic() < until:
                result = predicate()
                if result:
                    return result
                time.sleep(0.1)
            self.fail("capture isolation did not reach its expected state")

        def mapped(target):
            return next((p for p in panes() if p.get("target") == target and
                         p.get("window") and p.get("state") == "overlay"), None)

        chart = """import os,signal
def draw(*unused):
 os.write(1,(chr(27)+'[48;2;'+os.environ['MIOS_PROBE_COLOR']+'m'+chr(27)+'[2J'+chr(27)+'[H').encode())
signal.signal(signal.SIGWINCH,draw)
draw()
while True:signal.pause()
"""
        try:
            self.assertFalse(panes(), "capture isolation requires an idle overlay supervisor")
            for index, color in enumerate(("255;15;15", "15;255;15")):
                name = "mios-capture-probe-" + suffix + "-" + str(index)
                child = subprocess.Popen(
                    ["alacritty", "--class", name, "--title", "MiOS capture isolation",
                     "-o", "window.opacity=1.0", "-e", sys.executable, "-c", chart],
                    env={**os.environ, "MIOS_PROBE_COLOR": color},
                    stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                children.append(child)
                window = wait_for(lambda: next((c for c in clients() if c.get("class") == name), None))
                target = window["address"]
                targets.append(target)
                ref = "address:" + target
                checked(["hyprctl", "eval",
                         f"hl.dispatch(hl.dsp.window.float({{action='enable',window='{ref}'}}));"
                         f"hl.dispatch(hl.dsp.window.resize({{x=800,y=650,window='{ref}'}}));"
                         f"hl.dispatch(hl.dsp.window.move({{x={80+index*960},y=160,window='{ref}'}}))"])
            time.sleep(1)
            # Issue a restore token for this exact live source before reattaching it.
            checked([binary, "shaderglass", "attach", "--window", targets[0]])
            wait_for(lambda: mapped(targets[0]))
            time.sleep(5)
            checked([binary, "shaderglass", "detach", "--window", targets[0]])
            wait_for(lambda: not any(p.get("target") == targets[0] for p in panes()))
            checked([binary, "shaderglass", "attach", "--window", targets[0]])
            wait_for(lambda: mapped(targets[0]))
            time.sleep(3)
            picks = Path(os.environ["XDG_RUNTIME_DIR"]) / "mios/shaderglass/picks"
            pending_after_restore = len(list(picks.glob("*.json")))
            (evidence / "mios-request-state.json").write_text(json.dumps(
                dict(pending_after_restore=pending_after_restore), indent=2))
            checked([binary, "shaderglass", "attach", "--window", targets[1]])
            wait_for(lambda: mapped(targets[1]))
            time.sleep(5)
            for index, target in enumerate(targets):
                current = next(c for c in clients() if c.get("address") == target)
                at, size = current["at"], current["size"]
                image = evidence / ("mios-target-" + str(index) + ".png")
                checked(["grim", "-g", f"{at[0]},{at[1]} {size[0]}x{size[1]}", str(image)])
                with Image.open(image) as pixels:
                    width, height = pixels.size
                    center = pixels.convert("RGB").crop((width//4, height//4, 3*width//4, 3*height//4))
                    rgb = ImageStat.Stat(center).mean
                records.append(dict(target=target, expected=("red", "green")[index],
                                    mean_rgb=rgb, image=str(image)))
                (evidence / "mios-isolation-result.json").write_text(json.dumps(records, indent=2))
                primary, other = (rgb[0], rgb[1]) if index == 0 else (rgb[1], rgb[0])
                self.assertGreater(primary, max(30, 3*other),
                                   f"target {index} captured another window: {rgb}; evidence={evidence}")
            self.assertEqual(pending_after_restore, 0, "restored capture left a request for another picker")
            self.assertEqual(list(picks.glob("*.json")), [], "completed captures left unconsumed requests")
        finally:
            for target in targets:
                run([binary, "shaderglass", "detach", "--window", target], timeout=30)
            for child in children:
                if child.poll() is None:
                    child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
            log.close()


if __name__ == "__main__":
    unittest.main()
