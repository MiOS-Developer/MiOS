#!/usr/bin/env python3
# AI-hint: Desktop frame-rate policy ([desktop.framerate]; operator 2026-10-23: performance 90, balanced / power-saver /
# battery 60, gaming 120, unlocked = no cap; glass 1/3 of the desktop rate, 1/2 for gaming and unlocked; wallpaper 1/2,
# full rate when unlocked), in four tiers, each with positive and negative controls:
#   1. SSOT/source (always): the tier table is the operator's (a Python mirror of mios_resolver::framerate derives D, G
#      and W per tier: wallpaper = D/2 except unlocked = D); the fixed Hyprglass rates are gone (superseded); the Hypr3D
#      frame-rate patch is listed after edge-quality and adds its keys; the mios-pacing plugin (frame cap + wallpaper
#      frame-callback hook), its gate host test (`make test`), the user unit and the target's Wants; the wallpaper host
#      steps once per presented frame while paced, with a self-check (negative controls); it reads the live rate (Host.qml ctl,
#      MeshGradient u_rate, the rate-independent SIM shader; the pre-change Host.qml is rejected); MIOS_FR_STAGE (the
#      staged tree) adds the compiled shader's u_rate uniform.
#   2. projection (MIOS_FR_GEN = a mios-gen with the framerate projection): `render-hyprland --runtime` in scratch roots
#      (tuned-ppd base profile + sysfs battery per login tier) runs under a Lua stub: the pacing cap and the Hyprglass
#      rate per tier; nothing when the policy is disabled; a fixed live_resample_fps or Hypr3D rate key is refused.
#   3. upstream (network; MIOS_TEST_OFFLINE=1 skips; MIOS_FR_UPSTREAM = a checkout instead of a clone): every SSOT Hypr3D
#      patch applies to upstream HEAD in order, the frame-rate patch last; it does not apply without the earlier ones.
#   4. nested (MIOS_FR_NEST = framerate/nest with start.sh running, the staged daemon and wallpaper started by run.sh;
#      never the live session): fake power-profile / UPower / GameMode signals on private buses move the daemon's tier;
#      the pacing plugin holds the compositor at the tier's rate (never above it; at it whenever frames were deferred --
#      the nest's shared iGPU stalls clients for seconds, which is not the cap), the Hyprglass options and the state
#      file follow; the wallpaper presents at W (its frame callbacks per second, held by the plugin) and steps at W,
#      half the desktop rate except unlocked; negative control: with the throttle off the wallpaper asks for about the
#      desktop rate (Qt commits each step twice), and the daemon repairs it; a config reload is repaired;
#      MIOS_FR_HYPR3D_SO adds the Hypr3D key parse (positive and negative).
# AI-related: usr/share/mios/mios.toml [desktop.framerate] [desktop.hyprland.mios-pacing], tools/native/mios-resolver/src/framerate.rs,
# tools/native/mios-gen/src/render.rs (framerate_lua), src/mios-rs/miosd/src/framerate.rs, usr/share/mios/hyprland/plugins/mios-pacing,
# usr/share/mios/hyprland/patches/hypr3d-mios-frame-rate.patch, usr/share/mios/wallpaper/{Host,MeshGradient}.qml, tools/native/mios-wallpaperd
"""Desktop frame-rate policy tests."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import tomllib
import unittest
import zlib

ROOT = os.environ.get("MIOS_TEST_ROOT") or os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SSOT = os.environ.get("MIOS_VENDOR_TOML") or os.path.join(ROOT, "usr", "share", "mios", "mios.toml")
OFFLINE = os.environ.get("MIOS_TEST_OFFLINE") == "1"
GEN = os.environ.get("MIOS_FR_GEN", "")
STAGE = os.environ.get("MIOS_FR_STAGE", "")
NEST = os.environ.get("MIOS_FR_NEST", "")
H3D_SO = os.environ.get("MIOS_FR_HYPR3D_SO", "")
UPSTREAM = os.environ.get("MIOS_FR_UPSTREAM", "")
# negative controls: the builds installed on the host before this change (they must NOT cap)
OLD_GEN = os.environ.get("MIOS_FR_OLD_GEN", "/usr/bin/mios-gen")
OLD_MIOSD = os.environ.get("MIOS_FR_OLD_MIOSD", "/usr/bin/miosd")
OLD_H3D_SO = os.environ.get("MIOS_FR_OLD_HYPR3D_SO", "/usr/lib64/mios/hypr3d.so")
OLD_WALLPAPERD = os.environ.get("MIOS_FR_OLD_WALLPAPERD", "/usr/bin/mios-wallpaperd")
EVIDENCE_PATH = os.environ.get("MIOS_FR_EVIDENCE", "")
EVIDENCE: dict = {}


def evidence(key: str, value) -> None:
    """Measurements for the report (MIOS_FR_EVIDENCE, JSON, merged across runs)."""
    EVIDENCE[key] = value


def tearDownModule() -> None:
    if not (EVIDENCE_PATH and EVIDENCE):
        return
    try:
        with open(EVIDENCE_PATH) as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    data.update(EVIDENCE)
    d = os.path.dirname(EVIDENCE_PATH)
    if d and not os.path.isdir(d):
        os.makedirs(d)
        os.chmod(d, 0o755)
    tmp = EVIDENCE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=1, sort_keys=True)
    os.chmod(tmp, 0o644)
    os.replace(tmp, EVIDENCE_PATH)
FR_PATCH = "usr/share/mios/hyprland/patches/hypr3d-mios-frame-rate.patch"
PACING = os.path.join(ROOT, "usr", "share", "mios", "hyprland", "plugins", "mios-pacing")
WALL = os.path.join(ROOT, "usr", "share", "mios", "wallpaper")


def read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def ssot() -> dict:
    with open(SSOT, "rb") as f:
        return tomllib.load(f)


def run(cmd: list[str], env: dict | None = None, cwd: str | None = None, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=cwd, timeout=timeout)


# ---- Python mirror of mios_resolver::framerate (tier precedence + rates) ------------------------------------------------
OPERATOR = {  # tier: (desktop_hz, glass_divisor, wallpaper_divisor) -- DESIGN.md section 0
    "unlocked": (0, 2, 1), "gaming": (120, 2, 2), "battery": (60, 3, 2),
    "performance": (90, 3, 2), "balanced": (60, 3, 2), "power-saver": (60, 3, 2),
}


def tier_for(fr: dict, profile: str | None, battery: bool, gaming: bool) -> str:
    if fr["unlocked"]:
        return "unlocked"
    if gaming:
        return "gaming"
    if battery:
        return "battery"
    known = ("power-saver", "balanced", "performance")
    return fr["profiles"][profile] if profile in known else fr["profiles"]["unknown"]


def with_key(text: str, section: str, key: str, value: str) -> str:
    """`text` with `key = value` in [section] (replacing the key's line there, comments kept)."""
    start = text.index(f"[{section}]\n")
    end = text.index("\n[", start + 1)
    body, n = re.subn(rf"(?m)^{re.escape(key)} = [^#\n]*?(\s*(#.*)?)$", f"{key} = {value}\\1", text[start:end], count=1)
    assert n == 1, (section, key)
    return text[:start] + body + text[end:]


def rates(fr: dict, tier: str, max_hz: int | None) -> tuple[int, int, int, int]:
    t = fr["tiers"][tier]
    d = t["desktop_hz"]
    desk = (max_hz or 0) if d == 0 else (min(d, max_hz) if max_hz else d)
    return desk, d, desk // t["glass_divisor"], desk // t["wallpaper_divisor"]  # D, cap, G, W


def table_errors(fr: dict) -> list[str]:
    errs = []
    for name, (d, g, w) in OPERATOR.items():
        t = fr.get("tiers", {}).get(name)
        if not t:
            errs.append(f"tier {name} missing")
        elif (t.get("desktop_hz"), t.get("glass_divisor"), t.get("wallpaper_divisor")) != (d, g, w):
            errs.append(f"tier {name} = {t} (operator: {d} Hz, glass /{g}, wallpaper /{w})")
    return errs


def host_contract_errors(host: str, mesh: str, frag: str) -> list[str]:
    """Why the wallpaper host would NOT follow the live rate (empty = it does)."""
    e = []
    if not re.search(r'readonly property string ctl: query\["ctl"\]', host):
        e.append("Host.qml reads no ctl (state file) from the URL")
    if "wallpaper_fps" not in host or "XMLHttpRequest" not in host:
        e.append("Host.qml does not read wallpaper_fps from the state file")
    if not re.search(r"^\s*property real fps:", host, re.M):
        e.append("Host.qml fps is not writable (readonly: no live rate change)")
    if not re.search(r'readonly property real refFps: num\("ref", num\("fps"', host):
        e.append("Host.qml keeps no reference rate")
    if "return host.refFps" not in host:
        e.append("Host.qml has no reference-rate fallback when the state file is gone")
    if "refFps: host.refFps" not in host:
        e.append("Host.qml does not pass the reference rate to MeshGradient")
    if not re.search(r"property real u_rate: ocean\.refFps / ocean\.fps", mesh):
        e.append("MeshGradient gives the SIM no u_rate")
    if not re.search(r"float u_rate;", frag) or "atRef()" not in frag or "pow(1.0 - 0.020, u_rate)" not in frag:
        e.append("the SIM shader does not compound its per-step constants by u_rate")
    return e


def paced_contract_errors(host: str) -> list[str]:
    """Why the wallpaper host would NOT present at W under compositor pacing (empty = it does): one step per presented
    frame, taken before the frame's sync, only while the state says the compositor paces it, with a fallback."""
    e = []
    if "wallpaper_paced === true" not in host:
        e.append("Host.qml does not read wallpaper_paced from the state file")
    if not re.search(r"onAfterAnimating: \{[^}]*host\.paced[^}]*ocean\.step\(host\.simTime\(\)\)[^}]*win\.update\(\)", host, re.S):
        e.append("Host.qml takes no step per frame (afterAnimating) while paced")
    if "running: host.fixedSteps === 0 && !host.paced" not in host:
        e.append("the own timer keeps stepping while paced (two commits per step)")
    if "host.refusePacing(rate)" not in host or "rate > host.fps * 1.5" not in host:
        e.append("Host.qml has no self-check against an unpaced compositor (steps at the full desktop rate)")
    if "pacedRetry" not in host:
        e.append("a refused pacing is never retried")
    return e


def qsb_has_uniform(path: str, name: str) -> bool:
    """Qt .qsb: a qCompress()ed QShader stream (4-byte big-endian size + zlib); the reflection names every uniform."""
    with open(path, "rb") as f:
        data = f.read()
    try:
        raw = zlib.decompress(data[4:])
    except zlib.error:
        raw = data
    return name.encode() in raw


# ---------------------------------------------------------------- 1. SSOT and source


class SsotAndSource(unittest.TestCase):
    def setUp(self) -> None:
        self.d = ssot()
        self.fr = self.d["desktop"].get("framerate")
        self.assertIsNotNone(self.fr, "[desktop.framerate] is missing (the staged framerate SSOT edit)")

    def test_tier_table_is_the_operators(self) -> None:
        self.assertEqual(table_errors(self.fr), [])
        self.assertTrue(self.fr["enabled"])
        self.assertFalse(self.fr["unlocked"], "unlocked is an explicit opt-in, never the vendor default")
        self.assertEqual(self.fr["profiles"], {"power-saver": "power-saver", "balanced": "balanced",
                                               "performance": "performance", "unknown": "balanced"})
        bad = json.loads(json.dumps(self.fr))
        bad["tiers"]["performance"]["desktop_hz"] = 100
        self.assertTrue(table_errors(bad), "negative control: a changed rate is flagged")

    def test_display_mode_changes_revert_unless_confirmed(self) -> None:
        """Any automatic display-mode change is restored after mode_confirm_s (15 s) unless the operator confirms it,
        and stopping the daemon restores every mode it changed (the nested tier runs it; this is the source contract)."""
        self.assertEqual(self.fr["mode_confirm_s"], 15)
        src = read(os.path.join(ROOT, "src", "mios-rs", "miosd", "src", "framerate.rs"))
        missing = [n for n in ("plan::needs_confirm(", "fn expire_pending", "self.confirm.declined.insert", "fn restore_modes",
                               "srv.restore_modes();", "ConfirmMode", "SignalKind::user_defined1()") if n not in src]
        self.assertEqual(missing, [], "miosd framerate lacks the confirm-or-revert path")
        plan = read(os.path.join(ROOT, "src", "mios-rs", "miosd", "src", "framerate", "plan.rs"))
        self.assertIn('env.declined.contains(&m.identity) => Mech::Uncapped("mode switch not confirmed")', plan)
        # negative control: a daemon that only verifies (the run-2 build) and never asks is flagged
        old = src.replace("plan::needs_confirm(", "plan::verified(").replace("srv.restore_modes();", "")
        self.assertTrue([n for n in ("plan::needs_confirm(", "srv.restore_modes();") if n not in old])

    def test_precedence_and_rates_per_tier(self) -> None:
        fr = self.fr
        expect = {  # (profile, battery, gaming) -> (tier, D, G, W) on a 144 Hz output
            ("power-saver", False, False): ("power-saver", 60, 20, 30),
            ("balanced", False, False): ("balanced", 60, 20, 30),
            ("performance", False, False): ("performance", 90, 30, 45),
            ("performance", True, False): ("battery", 60, 20, 30),
            ("power-saver", True, True): ("gaming", 120, 60, 60),
            (None, False, False): ("balanced", 60, 20, 30),
        }
        for (p, b, g), (tier, d, gl, w) in expect.items():
            t = tier_for(fr, p, b, g)
            r = rates(fr, t, 144)
            self.assertEqual((t, r[0], r[2], r[3]), (tier, d, gl, w), (p, b, g))
        u = dict(fr, unlocked=True)
        self.assertEqual(tier_for(u, "power-saver", True, True), "unlocked")
        self.assertEqual(rates(u, "unlocked", 144), (144, 0, 72, 144))

    def test_wallpaper_is_half_the_desktop_rate_except_unlocked(self) -> None:
        for max_hz in (60, 75, 100, 120, 144, 165, 240):
            for tier in ("power-saver", "balanced", "performance", "battery", "gaming"):
                d, _, _, w = rates(self.fr, tier, max_hz)
                self.assertEqual(w, d // 2, (tier, max_hz))
                self.assertNotEqual(w, d, f"negative control: {tier} never runs the wallpaper at the desktop rate")
            d, cap, g, w = rates(self.fr, "unlocked", max_hz)
            self.assertEqual((w, d, cap, g), (max_hz, max_hz, 0, max_hz // 2), max_hz)

    def test_fixed_hyprglass_rates_are_superseded(self) -> None:
        g = self.d["desktop"]["hyprland"]["hyprglass"]["config"]
        for group in ("layers", "windows"):
            self.assertNotIn("live_resample_fps", g.get(group, {}), f"{group}: the glass rate is [desktop.framerate]'s G")
        self.assertTrue(g["layers"]["live_resample"], "content-driven re-sampling stays on")

    def test_hypr3d_frame_rate_patch_is_last_and_adds_its_keys(self) -> None:
        patches = self.d["desktop"]["hyprland"]["hypr3d"]["patches"]
        self.assertIn(FR_PATCH, patches)
        fr_idx = patches.index(FR_PATCH)
        self.assertEqual(patches[fr_idx - 1], "usr/share/mios/hyprland/patches/hypr3d-mios-edge-quality.patch")
        text = read(os.path.join(ROOT, FR_PATCH))
        added = "\n".join(l[1:] for l in text.splitlines() if l.startswith("+") and not l.startswith("+++"))
        for key in ('"max_fps"', '"glass_fps"', '"glass_cache_mib"', '"live_fps"'):
            self.assertRegex(added, r"SET_NUM\(idx, " + re.escape(key), key)
        for need in ("glassFrameDue", "storeGlassSlot", "latchGlassEnvironment", "framePumpTimeout", "domeDue", "pacing: max_fps="):
            self.assertIn(need, added, need)
        h3d = self.d["desktop"]["hyprland"]["hypr3d"]["config"]
        self.assertNotIn("max_fps", h3d.get("render", {}))
        self.assertNotIn("live_fps", h3d.get("world", {}))

    def test_pacing_plugin_entry_sources_and_gate(self) -> None:
        p = self.d["desktop"]["hyprland"][self.fr["pacing_plugin"]]
        self.assertTrue(p["enabled"])
        self.assertEqual(p["source_path"], "usr/share/mios/hyprland/plugins/mios-pacing")
        self.assertEqual(p["build_system"], "make")
        self.assertTrue(p["config"]["require_vrr"], "a cap on a fixed-refresh output would judder")
        self.assertEqual(p["config"]["wallpaper_namespaces"], "mios-wallpaper", "the wallpaper's layer namespace (Host.qml scope)")
        host = read(os.path.join(WALL, "Host.qml"))
        self.assertIn(f'LayerShell.Window.scope: "{p["config"]["wallpaper_namespaces"]}"', host)
        for k in ("max_fps", "exclude_prefixes", "wallpaper_fps"):
            self.assertNotIn(k, p["config"], f"{k} is the running tier's, never a static SSOT value")
        main = read(os.path.join(PACING, "main.cpp"))
        self.assertIn("CMonitorFrameScheduler::onFrame", main)
        self.assertIn('"CWLSurfaceResource::frame("', main, "the wallpaper's frame callbacks are held in the compositor")
        for opt in ("max_fps", "exclude_prefixes", "slack_us", "require_vrr", "enabled", "wallpaper_fps", "wallpaper_namespaces"):
            self.assertIn(f'"plugin:mios_pacing:{opt}"', main)
        if shutil.which("g++") and shutil.which("make"):
            with tempfile.TemporaryDirectory() as t:
                r = run(["make", "-C", PACING, "test", f"TEST={t}/gate"])
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertIn("all tests passed", r.stdout)

    def test_daemon_unit_and_target(self) -> None:
        u = self.d["user_units"]["mios-framerate.service"]
        self.assertEqual(u["Service"]["ExecStart"], "/usr/bin/miosd framerate serve")
        self.assertEqual(u["Unit"]["PartOf"], "mios-desktop.target")
        self.assertIn("mios-framerate.service", self.d["user_units"]["mios-desktop.target"]["Unit"]["Wants"].split())

    def test_remote_outputs_keep_their_own_pacing(self) -> None:
        prefixes = self.fr.get("exclude_prefixes", self.d["desktop"]["displays"]["ignore_prefixes"])
        rdp = self.d["remote"]["rdp"].get("virtual_output", "HEADLESS-MIOS-RDP")
        self.assertTrue(any(rdp.startswith(p) for p in prefixes), f"{rdp} must be excluded from the cap and modesets")

    def test_wallpaper_host_follows_the_live_rate(self) -> None:
        host, mesh = read(os.path.join(WALL, "Host.qml")), read(os.path.join(WALL, "MeshGradient.qml"))
        frag = read(os.path.join(WALL, "shaders", "ocean-sim.frag"))
        self.assertEqual(host_contract_errors(host, mesh, frag), [])
        self.assertEqual(paced_contract_errors(host), [])
        # negative controls: a host that keeps its timer while paced, or never checks, is flagged
        self.assertTrue(paced_contract_errors(host.replace("running: host.fixedSteps === 0 && !host.paced", "running: host.fixedSteps === 0")))
        self.assertTrue(paced_contract_errors(host.replace("host.refusePacing(rate)", "console.info(rate)")))
        self.assertEqual(self.d["theme"]["wallpaper"]["framerate"], 30, "the look's reference rate is unchanged")
        # negative control: the host before the change (committed tree) steps at a fixed rate
        old = run(["git", "-C", ROOT, "show", "HEAD:usr/share/mios/wallpaper/Host.qml"])
        oldmesh = run(["git", "-C", ROOT, "show", "HEAD:usr/share/mios/wallpaper/MeshGradient.qml"])
        oldfrag = run(["git", "-C", ROOT, "show", "HEAD:usr/share/mios/wallpaper/shaders/ocean-sim.frag"])
        if old.returncode == 0 and oldmesh.returncode == 0 and oldfrag.returncode == 0:
            self.assertTrue(host_contract_errors(old.stdout, oldmesh.stdout, oldfrag.stdout))
        wd = read(os.path.join(ROOT, "tools", "native", "mios-wallpaperd", "src", "main.rs"))
        self.assertIn('.env("QML_XHR_ALLOW_FILE_READ", "1")', wd)
        self.assertIn("fn rate_query", wd)

    @unittest.skipUnless(STAGE, "MIOS_FR_STAGE (the staged tree) not set")
    def test_staged_sim_shader_has_the_rate_uniform(self) -> None:
        new = os.path.join(STAGE, "usr", "share", "mios", "wallpaper", "shaders", "ocean-sim.frag.qsb")
        self.assertTrue(qsb_has_uniform(new, "u_rate"), new)
        old = os.path.join(WALL, "shaders", "ocean-sim.frag.qsb")
        if not qsb_has_uniform(old, "u_rate"):  # negative control while the old shader is still installed
            self.assertTrue(qsb_has_uniform(old, "u_dt"), "the decoder reads the installed shader")


# ---------------------------------------------------------------- 2. projection

LUA_STUB = r"""
local function auto()
 return setmetatable({}, { __index = function(t, k) local v = auto() rawset(t, k, v) return v end, __call = function() return auto() end })
end
MIOS_SESSION = {}
hl = auto()
local out = {}
local function walk(t, prefix)
 for k, v in pairs(t) do
  local key = prefix .. ":" .. k
  if type(v) == "table" then walk(v, key) else out[#out + 1] = key .. "=" .. tostring(v) end
 end
end
local monitors = { { name = "DP-3", x = 0, y = 0, width = 1920, height = 1080, refresh_rate = 143.998, scale = 1, transform = 0,
  available_modes = { { width = 1920, height = 1080, refresh_rate = 143.998 }, { width = 1920, height = 1080, refresh_rate = 60 } } } }
hl.get_monitors = function() return monitors end
hl.get_monitor = function(n) return monitors[1] end
hl.plugin.load = function(p) out[#out + 1] = "load " .. p end
hl.config = function(t) walk(t, "") end
hl.plugin.hyprglass = setmetatable({ config = function(t) walk(t, ":plugin:hyprglass") end }, { __index = function() return function() end end })
dofile(os.getenv("CHUNK"))
for _, l in ipairs(out) do print(l) end
local r = MIOS_SESSION.framerate or {}
print("rate " .. tostring(r.tier) .. " " .. tostring(r.desktop_hz) .. " " .. tostring(r.cap_hz) .. " " .. tostring(r.glass_fps) .. " " .. tostring(r.wallpaper_fps))
for _, e in ipairs(MIOS_SESSION.errors or {}) do print("error " .. e) end
"""


def lua() -> str:
    for p in ("/usr/bin/luajit", "/usr/bin/lua", "/usr/sbin/lua"):
        if os.path.exists(p):
            return p
    raise unittest.SkipTest("no Lua interpreter")


@unittest.skipUnless(GEN, "MIOS_FR_GEN (a mios-gen with the framerate projection) not set")
class Projection(unittest.TestCase):
    def render(self, text: str, profile: str | None, battery: bool) -> tuple[int, str, str]:
        with tempfile.TemporaryDirectory() as t:
            os.makedirs(f"{t}/usr/share/mios")
            os.makedirs(f"{t}/etc/tuned")
            with open(f"{t}/usr/share/mios/mios.toml", "w") as f:
                f.write(text)
            if profile:
                with open(f"{t}/etc/tuned/ppd_base_profile", "w") as f:
                    f.write(profile)
            for name, kind, extra in (("BAT0", "Battery", {"status": "Discharging" if battery else "Full"}), ("AC", "Mains", {"online": "0" if battery else "1"})):
                d = f"{t}/sys/class/power_supply/{name}"
                os.makedirs(d)
                with open(f"{d}/type", "w") as f:
                    f.write(kind + "\n")
                for k, v in extra.items():
                    with open(f"{d}/{k}", "w") as f:
                        f.write(v + "\n")
            env = {k: v for k, v in os.environ.items() if not k.startswith(("MIOS_TOML", "MIOS_VENDOR", "MIOS_HOST", "MIOS_USER", "XDG_RUNTIME_DIR"))}
            # --runtime layers the vendor file it is pointed at (else the INSTALLED one), host and user tiers: point
            # all three into the scratch root; the login inputs come from the root's etc/tuned and sys/class.
            env.update(MIOS_VENDOR_TOML=f"{t}/usr/share/mios/mios.toml", MIOS_USER_TOML=f"{t}/none.toml",
                       MIOS_HOST_TOML=f"{t}/none-host.toml", XDG_RUNTIME_DIR=f"{t}/rt", HOME=t)
            r = run([GEN, "render-hyprland", "--root", t, "--runtime"], env=env)
            if r.returncode != 0:
                return r.returncode, "", r.stderr + r.stdout
            chunk = f"{t}/chunk.lua"
            with open(chunk, "w") as f:
                f.write(r.stdout)
            stub = f"{t}/stub.lua"
            with open(stub, "w") as f:
                f.write(LUA_STUB)
            s = run([lua(), stub], env=dict(env, CHUNK=chunk))
            self.assertEqual(s.returncode, 0, s.stdout + s.stderr)
            return 0, r.stdout, s.stdout

    def test_each_login_tier_projects_its_cap_and_glass_rate(self) -> None:
        text = read(SSOT)
        path = ssot()["desktop"]["hyprland"]["mios-pacing"]["path"]
        for profile, battery, tier, d, cap, g, w in (
            ("power-saver", False, "power-saver", 60, 60, 20, 30),
            ("balanced", False, "balanced", 60, 60, 20, 30),
            ("performance", False, "performance", 90, 90, 30, 45),
            ("performance", True, "battery", 60, 60, 20, 30),
            (None, False, "balanced", 60, 60, 20, 30),
        ):
            rc, chunk, out = self.render(text, profile, battery)
            self.assertEqual(rc, 0, out)
            self.assertNotIn("error ", out)
            self.assertIn(f"rate {tier} {d} {cap} {g} {w}", out, (profile, battery))
            self.assertIn(f"load {path}", out)
            self.assertIn(f":plugin:mios_pacing:max_fps={cap}", out)
            self.assertIn(":plugin:mios_pacing:wallpaper_fps=0", out, "login: no state file, the wallpaper keeps its timer")
            self.assertIn(":plugin:mios_pacing:wallpaper_namespaces=mios-wallpaper", out, "the static plugin options reach it")
            self.assertIn(f":plugin:hyprglass:layers:live_resample_fps={g}", out)
            self.assertIn(f":plugin:hyprglass:windows:live_resample_fps={g}", out)
        # unlocked (an opt-in in any tier): no cap, G and W from the output's maximum
        rc, _, out = self.render(with_key(text, "desktop.framerate", "unlocked", "true"), "power-saver", True)
        self.assertEqual(rc, 0, out)
        self.assertIn("rate unlocked 144 0 72 144", out)
        self.assertIn(":plugin:mios_pacing:max_fps=0", out)

    def test_disabled_policy_projects_nothing(self) -> None:
        text = read(SSOT)
        off = with_key(text, "desktop.framerate", "enabled", "false")
        self.assertNotEqual(off, text)
        rc, chunk, out = self.render(off, "performance", False)
        self.assertEqual(rc, 0, out)
        self.assertNotIn('mios_section("framerate"', chunk)
        self.assertNotIn("mios_pacing", out)
        self.assertNotIn("live_resample_fps", out, "hyprglass keeps its own default")

    @unittest.skipUnless(os.access(OLD_GEN, os.X_OK), "no installed mios-gen")
    def test_installed_generator_projects_no_cap(self) -> None:
        """Negative control: the mios-gen installed before this change, on the policy SSOT, sets no frame cap and no
        policy glass rate (nothing on the host caps today)."""
        global GEN
        new, GEN = GEN, OLD_GEN
        try:
            rc, chunk, out = self.render(read(SSOT), "power-saver", False)
        finally:
            GEN = new
        self.assertNotIn(":plugin:mios_pacing:max_fps=", out, out[-800:])
        self.assertNotIn("rate power-saver", out)
        self.assertNotIn(":plugin:hyprglass:layers:live_resample_fps=20", out)
        evidence("projection.installed_mios_gen", {"rc": rc, "sets_cap": ":plugin:mios_pacing:max_fps=" in out})

    def test_fixed_rates_are_refused_while_the_policy_is_on(self) -> None:
        text = read(SSOT)
        fixed = text.replace("[desktop.hyprland.hyprglass.config.layers]\n", "[desktop.hyprland.hyprglass.config.layers]\nlive_resample_fps = 30\n", 1)
        rc, _, err = self.render(fixed, "balanced", False)
        self.assertNotEqual(rc, 0)
        self.assertIn("superseded by [desktop.framerate]", err)
        h3d = text.replace("[desktop.hyprland.hypr3d.config.render]\n", "[desktop.hyprland.hypr3d.config.render]\nmax_fps = 90\n", 1)
        rc, _, err = self.render(h3d, "balanced", False)
        self.assertNotEqual(rc, 0)
        self.assertIn("render.max_fps is superseded", err)


# ---------------------------------------------------------------- 3. upstream


@unittest.skipIf(OFFLINE and not UPSTREAM, "offline")
class Upstream(unittest.TestCase):
    def test_hypr3d_patches_apply_in_order_on_head(self) -> None:
        patches = ssot()["desktop"]["hyprland"]["hypr3d"]["patches"]
        url = ssot()["desktop"]["hyprland"]["hypr3d"]["source_repository"]
        with tempfile.TemporaryDirectory() as t:
            src = f"{t}/h3d"
            r = run(["git", "clone", "-q", "--depth=1", UPSTREAM or url, src], timeout=300)
            self.assertEqual(r.returncode, 0, r.stderr)
            head = run(["git", "-C", src, "rev-parse", "HEAD"]).stdout.strip()
            # negative control: the frame-rate patch alone does not apply to pristine upstream
            alone = run(["git", "-C", src, "apply", "--check", os.path.join(ROOT, FR_PATCH)])
            self.assertNotEqual(alone.returncode, 0, "the frame-rate patch must build on the earlier MiOS patches")
            for p in patches:
                a = run(["git", "-C", src, "apply", os.path.join(ROOT, p)])
                self.assertEqual(a.returncode, 0, f"{p} on {head}: {a.stderr}")
            main = read(f"{src}/src/main.cpp")
            for key in ('"max_fps"', '"glass_fps"', '"glass_cache_mib"', '"live_fps"'):
                self.assertIn(f"SET_NUM(idx, {key}", main)
            print(f"\n[upstream] Hypr3D HEAD {head}: {len(patches)} MiOS patches applied")


# ---------------------------------------------------------------- 4. nested


@unittest.skipUnless(NEST, "MIOS_FR_NEST (a running framerate/nest session with the daemon and wallpaper) not set")
class _Nest(unittest.TestCase):
    """Helpers for the nested tiers (no tests here)."""
    X = "/run/user/1000/mfr"

    def h(self, *args: str) -> str:
        return run([f"{NEST}/h.sh", *args]).stdout

    def state(self) -> dict:
        with open(f"{self.X}/mios/framerate/state.json") as f:
            return json.load(f)

    def set_prop(self, bus: str, dest: str, path: str, iface: str, prop: str, value: str) -> None:
        r = run(["gdbus", "call", "--address", f"unix:path={self.X}/{bus}", "-d", dest, "-o", path, "-m",
                 "org.freedesktop.DBus.Properties.Set", iface, prop, value])
        self.assertEqual(r.returncode, 0, r.stderr)

    def profile(self, p: str) -> None:
        self.set_prop("sysbus", "net.hadess.PowerProfiles", "/net/hadess/PowerProfiles", "net.hadess.PowerProfiles", "ActiveProfile", f"<'{p}'>")

    def battery(self, on: bool) -> None:
        self.set_prop("sysbus", "org.freedesktop.UPower", "/org/freedesktop/UPower", "org.freedesktop.UPower", "OnBattery", "<true>" if on else "<false>")

    def games(self, n: int) -> None:
        self.set_prop("bus", "com.feralinteractive.GameMode", "/com/feralinteractive/GameMode", "com.feralinteractive.GameMode", "ClientCount", f"<{n}>")

    def wait_tier(self, tier: str, timeout: float = 8.0) -> dict:
        end = time.time() + timeout
        while time.time() < end:
            try:
                s = self.state()
                if s["tier"] == tier:
                    return s
            except (OSError, ValueError):
                pass
            time.sleep(0.2)
        self.fail(f"tier {tier} not reached: {self.state()}")

    def pacing(self) -> dict:
        return json.loads(self.h("mios-pacing", "status"))

    def rendered(self) -> float:
        """The compositor's frames per second, median of three 1 s meter readings after the tier settled."""
        time.sleep(2.0)
        vals = []
        for _ in range(3):
            vals.append(self.pacing()["outputs"][0]["rendered_fps"])
            time.sleep(0.8)
        return sorted(vals)[1]

    def sample(self, seconds: float = 6.0, step: float = 0.5) -> list[dict]:
        """Pacing-plugin readings every `step` s: the output's rendered fps (1 s meter), its deferred frames per second
        since the previous reading, and the wallpaper surfaces' callbacks per second (1 s meter)."""
        out = []
        prev_t, prev = time.monotonic(), self.pacing()
        end = prev_t + seconds
        while time.monotonic() < end:
            time.sleep(step)
            t, cur = time.monotonic(), self.pacing()
            o, po = cur["outputs"][0], prev["outputs"][0]
            wall = [w["callbacks_fps"] for w in cur.get("wallpapers", []) if w["output"] == o["name"]]
            out.append({"fps": o["rendered_fps"], "deferred": (o["deferred"] - po["deferred"]) / (t - prev_t),
                        "wallpaper": wall[0] if wall else None})
            prev_t, prev = t, cur
        return out

    def assert_cap_holds(self, tier: str, d: int, samples: list[dict]) -> None:
        """The cap holds D: never above it, and at it whenever the clients outpace it (frames were deferred in that
        reading). A fast producer asks at the output's maximum, so the cap defers about (max - D) frames a second; a
        reading with at least half of that is cap-limited. The nest renders on a shared 2-CU iGPU: client stalls there
        dip the rate below D for seconds with few deferrals -- the producers, not the cap; those readings are not
        counted for the 'at D' part (measured at 60 on 144: 81 deferred/s steady, 4-17/s in a stall)."""
        over = [x["fps"] for x in samples if x["fps"] > d * 1.03 + 1]
        self.assertFalse(over, f"{tier}: rendered above the cap {d}: {over}")
        headroom = max(self.state().get("max_hz", 144) - d, 0)
        limited = sorted(x["fps"] for x in samples if x["deferred"] >= max(5.0, headroom * 0.5))
        self.assertTrue(limited, f"{tier}: the cap never limited (clients idle?): {samples}")
        fps = limited[len(limited) // 2]
        self.assertAlmostEqual(fps, d, delta=d * 0.05, msg=f"{tier}: compositor {fps} fps while capped, want {d}: {samples}")

    def assert_wallpaper_at(self, tier: str, w: int, samples: list[dict]) -> None:
        """The wallpaper presents at W: its frame callbacks (= its commits: Qt swaps once per callback) per second."""
        rates = sorted(x["wallpaper"] for x in samples if x["wallpaper"] is not None)
        self.assertTrue(rates, f"{tier}: no wallpaper surface in the pacing status: {samples}")
        mid = rates[len(rates) // 2]
        self.assertAlmostEqual(mid, w, delta=w * 0.10 + 1, msg=f"{tier}: wallpaper {mid} callbacks/s, want {w}: {rates}")

    def option(self, name: str) -> int:
        return json.loads(self.h("-j", "getoption", name))["int"]

    # ---- daemon, CLI and nest files
    def daemon_pid(self) -> int | None:
        out = run(["pgrep", "-f", "stage/usr/bin/miosd framerate serve"]).stdout.split()
        return int(out[0]) if out else None

    def cli_env(self) -> dict:
        """The staged miosd CLI against the nest: its scratch SSOT root, user tier, runtime, state home and buses."""
        with open(f"{NEST}/run/nsig") as f:
            sig = f.read().strip()
        return dict(os.environ, MIOS_ROOT=f"{NEST}/root", MIOS_VENDOR_TOML=f"{NEST}/root/usr/share/mios/mios.toml",
                    MIOS_TOML=f"{NEST}/root/usr/share/mios/mios.toml", MIOS_HOST_TOML=f"{NEST}/root/etc/mios/mios.toml",
                    MIOS_USER_TOML=f"{NEST}/run/user/mios.toml", MIOS_USER_TOML_D=f"{NEST}/run/user/mios.d",
                    XDG_RUNTIME_DIR=self.X, XDG_STATE_HOME=f"{NEST}/run/state", HYPRLAND_INSTANCE_SIGNATURE=sig,
                    DBUS_SESSION_BUS_ADDRESS=f"unix:path={self.X}/bus", DBUS_SYSTEM_BUS_ADDRESS=f"unix:path={self.X}/sysbus")

    def miosd(self, *args: str, binary: str = "") -> subprocess.CompletedProcess:
        return run([binary or os.path.join(NEST, "..", "stage", "usr", "bin", "miosd"), "framerate", *args], env=self.cli_env())

    def fragment(self, name: str, text: str | None) -> None:
        """A user-tier mios.d fragment for the nest daemon (None removes it)."""
        path = f"{NEST}/run/user/mios.d/{name}"
        if text is None:
            if os.path.exists(path):
                os.remove(path)
            return
        with open(path, "w") as f:
            f.write(text)
        os.chmod(path, 0o644)

    def hup(self) -> None:
        pid = self.daemon_pid()
        self.assertIsNotNone(pid, "the nest daemon runs")
        os.kill(pid, 1)

    def ctl(self, prop: str, value: str) -> None:
        self.set_prop("sysbus", "org.mios.test", "/org/mios/test", "org.mios.test.FakeCtl", prop, value)

    def desk(self) -> dict:
        return [m for m in json.loads(self.h("-j", "monitors")) if m["name"] == "HEADLESS-DESK"][0]

    def probe_fps(self, seconds: float = 4.0) -> float:
        """The fps-probe client's presented frames per second (median of its 1 s log lines over `seconds`)."""
        log = f"{NEST}/run/c-probe.log"
        before = len(re.findall(r"fr-probe fps (\d+)", read(log)))
        time.sleep(seconds)
        vals = [int(v) for v in re.findall(r"fr-probe fps (\d+)", read(log))[before:]]
        self.assertTrue(vals, "the fps probe logs (run.sh probe qml fps-probe.qml)")
        return sorted(vals)[len(vals) // 2]

    # ---- Hypr3D (the nest's private /tmp holds its status file)
    STATUS = os.path.join(NEST, "run", "tmp", "hypr3d-status.txt") if NEST else ""

    def h3d_blocks(self) -> list[dict]:
        try:
            with open(self.STATUS, "rb") as f:
                text = f.read().decode("utf-8", "replace")
        except OSError:
            return []
        blocks, cur = [], None
        for line in text.splitlines():
            m = re.match(r"--- frame (\d+) t=([\d.e+-]+)s", line)
            if m:
                cur = {"t": float(m[2])}
                blocks.append(cur)
            elif cur is not None and line.startswith("render: "):
                f = re.search(r"fps=([\d.e+-]+)", line)
                cur["fps"] = float(f[1]) if f else 0.0
            elif cur is not None and line.startswith("pacing: "):
                for k, v in re.findall(r"([\w/]+)=([\d.e+-]+)", line):
                    cur[k] = float(v)
            elif cur is not None and line.startswith("active="):
                cur["active"] = line.startswith("active=1")
        return [b for b in blocks if "fps" in b]

    def room(self, seconds: float = 6.0) -> dict:
        """One measurement of the open room from Hypr3D's status file and the compositor's meter:
        room_fps: room frames per second (glassDue + glassKept: one per drawn room frame), compositor_fps: frames the
        output presented (mios-pacing meter, median), glass_due_per_s: frames whose refraction backdrops and reflected
        dome refreshed, dome_per_s: live dome re-captures per second (time-weighted), status_fps: the status line's own
        `fps=` -- an EMA of 1/dt, which overstates a paced (uneven) cadence, recorded but not asserted."""
        t0 = (self.h3d_blocks() or [{"t": 0.0}])[-1]["t"]
        samples = self.sample(seconds)
        blocks = [b for b in self.h3d_blocks() if b["t"] > t0 and b.get("active", True) and "glassDue" in b]
        self.assertGreater(len(blocks), 4, "Hypr3D writes its status (with the frame-rate pacing line) while the room is open")
        first, last = blocks[0], blocks[-1]
        dt = last["t"] - first["t"]
        frames = (last["glassDue"] + last["glassKept"]) - (first["glassDue"] + first["glassKept"])
        dome = sum(b["domeCapture/s"] * (b["t"] - a["t"]) for a, b in zip(blocks, blocks[1:])) / dt
        fps = sorted(b["fps"] for b in blocks)
        comp = sorted(x["fps"] for x in samples)
        return {"room_fps": round(frames / dt, 1), "compositor_fps": comp[len(comp) // 2], "compositor_max": comp[-1],
                "glass_due_per_s": round((last["glassDue"] - first["glassDue"]) / dt, 1), "dome_per_s": round(dome, 1),
                "status_fps": round(fps[len(fps) // 2], 1), "rates": (last["max_fps"], last["glass_fps"], last["live_fps"])}

    def reset_status(self) -> None:
        """Hypr3D's status file keeps older plugin instances' blocks (their clocks restart at 0): start it empty."""
        if self.STATUS and os.path.exists(self.STATUS):
            open(self.STATUS, "w").close()

    def open_room(self) -> None:
        self.reset_status()
        light = ("{scale = 1, msaa = 0, fxaa = false, sharpen = 0, texture_supersample = false, anisotropy = 1}")
        self.assertEqual(self.h("eval", 'local p = assert(hl.plugin.hypr3d); p.config({world = {monitor = "HEADLESS-DESK", grid = false}, '
                                        f'render = {light}}}); if not p.active() then p.open() end').strip(), "ok")
        time.sleep(4.0)

    def close_room(self) -> None:
        self.h("eval", "if hl.plugin.hypr3d and hl.plugin.hypr3d.active() then hl.plugin.hypr3d.close() end")
        time.sleep(2.0)

    def load_h3d(self, so: str) -> None:
        listed = self.h("plugin", "list")
        if "hypr3d" in listed:
            self.close_room()
            for cand in (H3D_SO, OLD_H3D_SO):
                if cand:
                    self.h("plugin", "unload", cand)
            time.sleep(1.0)
            self.assertNotIn("Plugin hypr3d", self.h("plugin", "list"), "the previous hypr3d build unloaded")
        self.assertEqual(self.h("plugin", "load", so).strip(), "ok")
        self.reset_status()
        time.sleep(0.5)

    def check(self, tier: str, d: int, cap: int, g: int, w: int) -> None:
        s = self.wait_tier(tier)
        self.assertEqual((s["desktop_hz"], s["cap_hz"], s["glass_fps"], s["wallpaper_fps"]), (d, cap, g, w), s)
        self.assertEqual(self.option("plugin:mios_pacing:max_fps"), cap)
        self.assertEqual(self.option("plugin:hyprglass:layers:live_resample_fps"), g)
        self.assertEqual(self.option("plugin:hyprglass:windows:live_resample_fps"), g)
        self.assertTrue(s["wallpaper_paced"], f"{tier}: the compositor paces the wallpaper: {s}")
        self.assertEqual(self.option("plugin:mios_pacing:wallpaper_fps"), w)
        time.sleep(2.0)  # the rate settles; Host.qml reads the state once a second
        samples = self.sample()
        self.assert_cap_holds(tier, d, samples)
        self.assert_wallpaper_at(tier, w, samples)
        logs = read(f"{NEST}/run/c-wallpaper.log")
        steps = re.findall(r"step rate \S+ -> (\d+)", logs)
        want = w if w != 30 else None  # 30 = the reference rate: no change logged when it is already there
        if want and steps:
            self.assertEqual(int(steps[-1]), want, f"{tier}: wallpaper steps {steps[-1]}/s, want {want}")



class Nested(_Nest):
    def setUp(self) -> None:
        # every test starts from the same inputs (a failed test must not leave the next one on battery)
        self.games(0)
        self.battery(False)
        self.profile("power-saver")
        self.wait_tier("power-saver", timeout=10.0)

    def test_signals_move_the_tier_and_every_consumer_follows(self) -> None:
        self.assertIn("anim", run(["systemctl", "--user", "list-units", "--plain", "--no-legend", "mios-fr-c-anim*"]).stdout,
                      "start the animated client (run.sh anim qml anim.qml) so the compositor renders every frame")
        self.games(0)
        self.battery(False)
        self.profile("balanced")
        self.check("balanced", 60, 60, 20, 30)
        self.profile("performance")
        self.check("performance", 90, 90, 30, 45)
        self.games(1)
        self.check("gaming", 120, 120, 60, 60)
        self.games(0)
        time.sleep(1.0)
        self.assertEqual(self.state()["tier"], "gaming", "the 3 s exit hold keeps gaming")
        self.check("performance", 90, 90, 30, 45)
        self.battery(True)
        self.check("battery", 60, 60, 20, 30)
        self.battery(False)
        self.profile("power-saver")
        self.check("power-saver", 60, 60, 20, 30)

    def test_wallpaper_presents_at_w_only_while_the_compositor_paces_it(self) -> None:
        s = self.wait_tier("power-saver")
        self.assertTrue(s["wallpaper_paced"], s)
        time.sleep(2.0)
        self.assert_wallpaper_at("power-saver", 30, self.sample(4.0))
        logs = read(f"{NEST}/run/c-wallpaper.log")
        self.assertIn("compositor pacing on", logs)
        # negative control: the throttle off (as before this change) -> the wallpaper asks for frames at about the
        # desktop rate D = 60: Qt commits every timer step twice (or Host.qml steps per frame until its self-check).
        # The daemon is paused meanwhile (SIGSTOP), else its self-check may repair the throttle inside the window.
        pid = self.daemon_pid()
        os.kill(pid, 19)
        try:
            self.assertEqual(self.h("eval", "hl.config({plugin = {mios_pacing = {wallpaper_fps = 0}}})").strip(), "ok")
            time.sleep(1.5)
            unpaced = sorted(x["wallpaper"] for x in self.sample(3.0) if x["wallpaper"] is not None)
            evidence("nested.wallpaper.unthrottled_negative_control", unpaced)
            self.assertGreater(unpaced[len(unpaced) // 2], 30 * 1.6, f"unthrottled wallpaper callbacks/s {unpaced}")
        finally:
            os.kill(pid, 18)
            # the daemon's self-check (nest: 10 s) re-applies W; Host.qml retries pacing 20 s after a refusal
            end = time.time() + 15
            while time.time() < end and self.option("plugin:mios_pacing:wallpaper_fps") != 30:
                time.sleep(0.5)
        self.assertEqual(self.option("plugin:mios_pacing:wallpaper_fps"), 30, "the daemon repaired the throttle")
        end = time.time() + 35
        while True:
            time.sleep(2.0)
            rates = sorted(x["wallpaper"] for x in self.sample(3.0) if x["wallpaper"] is not None)
            if rates and abs(rates[len(rates) // 2] - 30) <= 4 or time.time() > end:
                break
        self.assert_wallpaper_at("power-saver (repaired)", 30, self.sample(3.0))

    def test_config_reload_is_repaired(self) -> None:
        self.profile("performance")
        self.wait_tier("performance")
        self.h("reload")
        time.sleep(2.0)
        self.assertEqual(self.option("plugin:mios_pacing:max_fps"), 90, "the daemon re-applies after configreloaded")
        self.profile("power-saver")
        self.wait_tier("power-saver")

    def test_unlocked_lifts_the_cap_and_runs_the_wallpaper_at_full_rate(self) -> None:
        env = dict(os.environ, MIOS_ROOT=f"{NEST}/root", MIOS_VENDOR_TOML=f"{NEST}/root/usr/share/mios/mios.toml",
                   MIOS_TOML=f"{NEST}/root/usr/share/mios/mios.toml", MIOS_HOST_TOML=f"{NEST}/root/etc/mios/mios.toml",
                   MIOS_USER_TOML=f"{NEST}/run/user/mios.toml", MIOS_USER_TOML_D=f"{NEST}/run/user/mios.d")
        miosd = os.path.join(NEST, "..", "stage", "usr", "bin", "miosd")
        pid = run(["pgrep", "-f", "stage/usr/bin/miosd framerate serve"]).stdout.split()[0]
        try:
            r = run([miosd, "framerate", "set-unlocked", "on", "--no-reload"], env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            os.kill(int(pid), 1)
            s = self.wait_tier("unlocked")
            self.assertEqual((s["cap_hz"], s["desktop_hz"], s["glass_fps"], s["wallpaper_fps"]), (0, 144, 72, 144))
            self.assertEqual(self.option("plugin:mios_pacing:max_fps"), 0)
            self.assertEqual(self.option("plugin:mios_pacing:wallpaper_fps"), 144, "unlocked: the wallpaper at the full rate")
            self.assertGreater(self.rendered(), 100, "no cap: above every tier cap but gaming's, up to the output's 144 Hz")
            self.assertEqual(self.pacing()["outputs"][0]["uncapped"], "no cap")
            time.sleep(1.5)
            steps = re.findall(r"step rate \S+ -> (\d+)", read(f"{NEST}/run/c-wallpaper.log"))
            self.assertEqual(steps[-1], "144", "unlocked: the wallpaper at the full desktop rate")
        finally:
            run([miosd, "framerate", "set-unlocked", "off", "--no-reload"], env=env)
            os.kill(int(pid), 1)
        self.assertNotEqual(self.wait_tier("power-saver")["tier"], "unlocked")

    def test_tuned_profile_without_tuned_ppd(self) -> None:
        """A host with TuneD but no tuned-ppd: the daemon reads com.redhat.tuned active_profile, maps it back through
        etc/tuned/ppd.conf (mios-host-performance -> performance) and follows profile_changed."""
        self.ctl("Ppd", "<false>")
        try:
            self.ctl("TunedProfile", "<'mios-host-performance'>")
            self.check("performance", 90, 90, 30, 45)
            self.ctl("TunedProfile", "<'mios-host'>")
            self.assertEqual(self.wait_tier("balanced")["inputs"]["profile"], "balanced")
            self.ctl("TunedProfile", "<'mios-host-powersave'>")
            self.check("power-saver", 60, 60, 20, 30)
            logs = read(f"{NEST}/run/c-daemon.log")
            self.assertIn("via com.redhat.tuned (mios-host-performance)", logs)
            self.assertIn("via com.redhat.tuned (mios-host-powersave)", logs)
        finally:
            self.ctl("Ppd", "<true>")
            self.ctl("TunedProfile", "<'mios-host-powersave'>")
        self.profile("power-saver")
        self.wait_tier("power-saver")

    def test_hyprglass_rates_change_at_runtime(self) -> None:
        """Hyprglass reads live_resample_fps through its config pointer on every re-sample decision
        (GlassDecoration/GlassLayerSurface markBackgroundDirty), so a runtime change applies at once: the daemon's
        value is what the plugin holds after every tier change, and a foreign value is repaired."""
        seen = {}
        for setter, tier, g in ((lambda: self.profile("performance"), "performance", 30), (lambda: self.games(1), "gaming", 60),
                                (lambda: self.games(0), "performance", 30), (lambda: self.profile("power-saver"), "power-saver", 20)):
            setter()
            if tier == "performance" and seen.get("gaming"):
                time.sleep(3.5)  # the gaming exit hold
            self.wait_tier(tier)
            time.sleep(0.5)
            got = (self.option("plugin:hyprglass:layers:live_resample_fps"), self.option("plugin:hyprglass:windows:live_resample_fps"))
            self.assertEqual(got, (g, g), tier)
            seen[tier] = got
        self.assertEqual(self.h("eval", "hl.plugin.hyprglass.config({layers = {live_resample_fps = 7}})").strip(), "ok")
        self.assertEqual(self.option("plugin:hyprglass:layers:live_resample_fps"), 7, "the plugin takes a runtime value")
        end = time.time() + 15
        while time.time() < end and self.option("plugin:hyprglass:layers:live_resample_fps") != 20:
            time.sleep(0.5)
        self.assertEqual(self.option("plugin:hyprglass:layers:live_resample_fps"), 20, "the self-check repaired G")
        evidence("nested.hyprglass_runtime", seen)

    @unittest.skipUnless(H3D_SO, "MIOS_FR_HYPR3D_SO (the staged hypr3d.so) not set")
    def test_hypr3d_parses_its_rate_keys(self) -> None:
        listed = self.h("plugin", "list")
        if "hypr3d" not in listed:
            self.assertEqual(self.h("plugin", "load", H3D_SO).strip(), "ok")
        ok = self.h("eval", "hl.plugin.hypr3d.config({render = {max_fps = 90, glass_fps = 30, glass_cache_mib = 128}, world = {live_fps = 45}})")
        self.assertEqual(ok.strip(), "ok")
        for bad, key in (("render = {max_fps = 'x'}", "render.max_fps"), ("render = {glass_fps = {}}", "render.glass_fps"),
                         ("world = {live_fps = 'y'}", "world.live_fps")):
            out = self.h("eval", f"hl.plugin.hypr3d.config({{{bad}}})")
            self.assertIn(f"{key} must be a number", out)



@unittest.skipUnless(H3D_SO, "MIOS_FR_HYPR3D_SO (the staged hypr3d.so) not set")
class NestedHypr3D(_Nest):
    """The 3D room (staged hypr3d.so, opened in the nest over an animated client and the live wallpaper) follows the
    daemon's tier: the room and the compositor at D, glass refraction/reflection refreshes at G, the live dome at W;
    unlocked lifts the cap. Runs in the small nest (run-tests.sh: HEADLESS-DESK 640x360@144, a small client): at
    1920x1080 the nest's 2-CU iGPU draws the glass room at 17-37 fps, below every tier (measured, explore/)."""

    @classmethod
    def setUpClass(cls) -> None:
        t = cls()
        if "hypr3d" not in t.h("plugin", "list"):
            t.load_h3d(H3D_SO)
        t.games(0)
        t.battery(False)
        t.profile("power-saver")
        t.hup()  # a plugin loaded since the last apply: the daemon sends the room its rates now (else at its self-check)
        t.wait_tier("power-saver")
        t.open_room()

    @classmethod
    def tearDownClass(cls) -> None:
        cls().close_room()

    def wait_rates(self, want: tuple, timeout: float = 15.0) -> None:
        end = time.time() + timeout
        last = None
        while time.time() < end:
            b = [x for x in self.h3d_blocks() if "max_fps" in x]
            last = (b[-1]["max_fps"], b[-1]["glass_fps"], b[-1]["live_fps"]) if b else None
            if last == tuple(float(v) for v in want):
                return
            time.sleep(0.5)
        self.fail(f"Hypr3D rates {last}, want {want}")

    def assert_room(self, tier: str, r: dict, d: int, g: int, w: int) -> None:
        self.assertAlmostEqual(r["room_fps"], d, delta=d * 0.05 + 1, msg=f"{tier}: room frames/s, want D={d}: {r}")
        self.assertLessEqual(r["compositor_max"], d * 1.03 + 1, f"{tier}: compositor above D={d}: {r}")
        self.assertAlmostEqual(r["glass_due_per_s"], g, delta=g * 0.08 + 1, msg=f"{tier}: glass refreshes/s, want G={g}: {r}")
        self.assertAlmostEqual(r["dome_per_s"], w, delta=w * 0.10 + 1, msg=f"{tier}: dome captures/s, want W={w}: {r}")

    def test_room_follows_every_tier(self) -> None:
        results = {}
        for setter, tier, d, g, w in ((lambda: self.profile("power-saver"), "power-saver", 60, 20, 30),
                                      (lambda: self.profile("performance"), "performance", 90, 30, 45),
                                      (lambda: self.games(1), "gaming", 120, 60, 60),
                                      (lambda: (self.games(0), self.battery(True)), "battery", 60, 20, 30)):
            setter()
            self.wait_tier(tier, timeout=12.0)
            self.wait_rates((d, g, w))
            time.sleep(2.0)
            r = self.room()
            results[tier] = r
            self.assert_room(tier, r, d, g, w)
        self.battery(False)
        self.profile("power-saver")
        self.wait_tier("power-saver")
        # unlocked: no cap -- the room at the output's 144 Hz, glass at 72, the dome at the full rate
        env = self.cli_env()
        self.assertEqual(self.miosd("set-unlocked", "on", "--no-reload").returncode, 0)
        self.hup()
        try:
            s = self.wait_tier("unlocked")
            self.assertEqual((s["cap_hz"], s["desktop_hz"], s["glass_fps"], s["wallpaper_fps"]), (0, 144, 72, 144))
            self.wait_rates((0, 72, 144))
            time.sleep(2.0)
            r = self.room()
            results["unlocked"] = r
            self.assertGreater(r["room_fps"], 120 * 1.1, f"unlocked: the room runs above every tier cap: {r}")
            self.assertAlmostEqual(r["glass_due_per_s"], 72, delta=72 * 0.08 + 1, msg=f"unlocked glass: {r}")
            self.assertAlmostEqual(r["dome_per_s"], r["room_fps"], delta=r["room_fps"] * 0.1 + 1, msg=f"unlocked dome = every frame: {r}")
        finally:
            run([os.path.join(NEST, "..", "stage", "usr", "bin", "miosd"), "framerate", "set-unlocked", "off", "--no-reload"], env=env)
            self.hup()
        self.wait_tier("power-saver")
        evidence("nested.hypr3d_tiers", results)


@unittest.skipUnless(H3D_SO, "MIOS_FR_HYPR3D_SO (the staged hypr3d.so) not set")
class NestedHypr3DIsolated(_Nest):
    """The room's OWN rate keys, with nothing else in the nest asking for frames (the daemon, the animated client and
    the wallpaper stopped; mios-pacing as a meter only): render.max_fps holds the room's frame pump at D, glass_fps
    its refraction/reflection refreshes at G. Negative control: the installed hypr3d.so (before this change) takes
    the same keys silently and pumps at its fixed 8 ms (about 125 frames/s)."""

    @classmethod
    def setUpClass(cls) -> None:
        for u in ("daemon", "anim", "wallpaper", "probe"):
            run(["systemctl", "--user", "stop", f"mios-fr-c-{u}.scope"])
        # one static pane (glass in the room) that commits once and then asks for nothing
        run([f"{NEST}/run.sh", "static", "/usr/lib64/qt6/bin/qml", f"{NEST}/static.qml"])
        time.sleep(3.0)

    def setUp(self) -> None:
        self.assertIsNone(self.daemon_pid(), "the daemon is stopped")
        self.assertEqual(self.h("eval", "hl.config({plugin = {mios_pacing = {max_fps = 0, wallpaper_fps = 0}}})").strip(), "ok")

    def pump(self, d: int, g: int) -> dict:
        self.assertEqual(self.h("eval", f"hl.plugin.hypr3d.config({{render = {{max_fps = {d}, glass_fps = {g}}}}})").strip(), "ok")
        time.sleep(2.5)
        t0 = (self.h3d_blocks() or [{"t": 0.0}])[-1]["t"]
        samples = self.sample(5.0)
        comp = sorted(x["fps"] for x in samples)
        out = {"compositor_fps": comp[len(comp) // 2]}
        blocks = [b for b in self.h3d_blocks() if b["t"] > t0]
        if blocks and "glassDue" in blocks[-1]:
            dt = blocks[-1]["t"] - blocks[0]["t"]
            out["room_fps"] = round(((blocks[-1]["glassDue"] + blocks[-1]["glassKept"]) - (blocks[0]["glassDue"] + blocks[0]["glassKept"])) / dt, 1)
            out["glass_due_per_s"] = round((blocks[-1]["glassDue"] - blocks[0]["glassDue"]) / dt, 1)
        out["pacing_line"] = any("max_fps" in b for b in blocks)
        return out

    def test_a_staged_room_holds_its_own_rates(self) -> None:
        self.load_h3d(H3D_SO)
        self.open_room()
        res = {}
        try:
            for d, g in ((60, 20), (90, 30), (120, 60), (0, 0)):
                r = res[f"{d}/{g}"] = self.pump(d, g)
                if d:
                    self.assertAlmostEqual(r["room_fps"], d, delta=d * 0.05 + 1, msg=f"room pump {d}: {r}")
                    self.assertAlmostEqual(r["compositor_fps"], d, delta=d * 0.05 + 1, msg=f"nothing else damages: {r}")
                    self.assertAlmostEqual(r["glass_due_per_s"], g, delta=g * 0.08 + 1, msg=f"glass {g}: {r}")
            u = res["0/0"]
            self.assertAlmostEqual(u["room_fps"], 125, delta=12, msg=f"max_fps 0: the upstream 8 ms pump: {u}")
            self.assertAlmostEqual(u["glass_due_per_s"], u["room_fps"], delta=2, msg=f"glass_fps 0: every frame: {u}")
        finally:
            self.close_room()
        evidence("nested.hypr3d_own_pump", res)

    @unittest.skipUnless(os.path.exists(OLD_H3D_SO), "no installed hypr3d.so")
    def test_b_installed_hypr3d_does_not_cap(self) -> None:
        self.load_h3d(OLD_H3D_SO)
        self.open_room()
        try:
            r = self.pump(60, 20)  # the installed build ignores keys it does not know: no error, no effect
            self.assertFalse(r["pacing_line"], "the installed build has no frame-rate pacing status")
            self.assertGreater(r["compositor_fps"], 60 * 1.5, f"installed hypr3d with max_fps = 60: {r} (no cap)")
            evidence("nested.negative.installed_hypr3d", r)
        finally:
            self.close_room()
            self.h("plugin", "unload", OLD_H3D_SO)


class NestedModes(_Nest):
    """The advertised-mode path (mechanism = "mode" in the nest's user tier; MIOS_FRAMERATE_TEST_MODES gives the
    virtual HEADLESS-DESK the modes 60/100/144): a new mode is restored after mode_confirm_s (15 s) unless confirmed,
    and the panel then gets no more automatic modesets; a confirmed mode stays and needs no answer again; stopping the
    daemon restores the mode the output had before the policy. The daemon stops at the end (NestedIsolated runs
    without it)."""

    FRAG = "60-fr-modes.toml"

    def refresh(self) -> float:
        return float(self.desk()["refreshRate"])

    def wait_refresh(self, hz: float, timeout: float) -> float:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if abs(self.refresh() - hz) < 0.5:
                return time.monotonic()
            time.sleep(0.25)
        self.fail(f"HEADLESS-DESK at {self.refresh()} Hz, want {hz} within {timeout} s")

    def pending(self) -> dict | None:
        try:
            with open(f"{self.X}/mios/framerate/revert.json") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def wait_pending(self, timeout: float = 4.0) -> dict:
        """revert.json carries a deadline once the switch is verified and waits for its answer."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            p = self.pending()
            if p and p.get("deadline_unix"):
                return p
            time.sleep(0.1)
        self.fail(f"no modeset waits for confirmation: {self.pending()}")

    def confirm_file(self) -> dict:
        try:
            with open(f"{NEST}/run/state/mios/framerate/mode-confirm.json") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def test_1_an_unconfirmed_mode_is_restored_after_15_s(self) -> None:
        self.assertIn("MIOS_FRAMERATE_TEST_MODES", read(f"/proc/{self.daemon_pid()}/environ").replace("\0", "\n"))
        self.games(0)
        self.battery(False)
        self.profile("power-saver")
        self.wait_tier("power-saver")
        self.assertEqual(self.miosd("confirm-mode", "--reset").returncode, 0)
        self.assertAlmostEqual(self.refresh(), 144, delta=0.5)
        self.fragment(self.FRAG, '[desktop.framerate]\nmechanism = "mode"\n')
        self.hup()
        t_switch = self.wait_refresh(60, 12.0)
        p = self.wait_pending()
        self.assertTrue(p["mode"].startswith("1920x1080@60"), p)
        st = self.miosd("status")
        self.assertIn("waiting for confirmation: HEADLESS-DESK 1920x1080@60", st.stdout)
        t_back = self.wait_refresh(144, 22.0)
        waited = t_back - t_switch
        self.assertGreaterEqual(waited, 14.0, f"restored after {waited:.1f} s")
        self.assertLessEqual(waited, 17.5, f"restored after {waited:.1f} s")
        self.assertIsNone(self.pending())
        self.assertTrue(self.confirm_file().get("declined"), self.confirm_file())
        self.assertIn("not confirmed within the deadline", read(f"{NEST}/run/c-daemon.log"))
        time.sleep(8.0)
        self.assertAlmostEqual(self.refresh(), 144, delta=0.5, msg="a declined panel gets no further automatic modeset")
        self.assertEqual(self.state()["outputs"]["HEADLESS-DESK"]["mechanism"], "uncapped")
        self.assertIn("no automatic modesets (not confirmed)", self.miosd("status").stdout)
        evidence("nested.modes.unconfirmed", {"switched_to": p["mode"], "restored_after_s": round(waited, 2), "declined": self.confirm_file().get("declined")})

    def test_2_a_confirmed_mode_stays_and_stopping_restores_the_baseline(self) -> None:
        self.assertEqual(self.miosd("confirm-mode", "--reset").returncode, 0)  # forgets the decline, SIGHUP
        self.wait_refresh(60, 12.0)
        self.wait_pending()
        r = self.miosd("confirm-mode")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("keeping 1920x1080@60", r.stdout)
        time.sleep(17.0)
        self.assertAlmostEqual(self.refresh(), 60, delta=0.5, msg="a confirmed mode stays past the deadline")
        self.assertIsNone(self.pending())
        conf = self.confirm_file()
        self.assertTrue(any(m.startswith("1920x1080@60") for ms in conf.get("confirmed", {}).values() for m in ms), conf)
        # performance (D 90): the nearest advertised mode is 100 -- new on this panel: confirm again
        self.profile("performance")
        self.wait_refresh(100, 15.0)
        self.wait_pending()  # 100 Hz waits for its answer
        self.assertEqual(self.miosd("confirm-mode").returncode, 0)
        # back to power-saver: 60 was confirmed before -- no question this time
        self.profile("power-saver")
        self.wait_refresh(60, 15.0)
        time.sleep(1.0)
        self.assertIsNone(self.pending(), "a confirmed mode needs no answer again")
        # negative control: confirm-mode with nothing pending fails
        self.assertNotEqual(self.miosd("confirm-mode").returncode, 0)
        # stop: every mode the daemon changed comes back, the state file goes, the wallpaper throttle is lifted
        run(["systemctl", "--user", "stop", "mios-fr-c-daemon.scope"])
        t_back = self.wait_refresh(144, 6.0)
        self.assertFalse(os.path.exists(f"{self.X}/mios/framerate/state.json"))
        self.assertEqual(self.option("plugin:mios_pacing:wallpaper_fps"), 0)
        self.assertIn("restoring the mode it had before the frame-rate policy", read(f"{NEST}/run/c-daemon.log"))
        self.fragment(self.FRAG, None)
        time.sleep(3.0)
        self.assertIn("compositor pacing off", read(f"{NEST}/run/c-wallpaper.log"), "Host.qml back on its own timer")
        evidence("nested.modes.confirmed", {"confirmed": conf.get("confirmed"), "stop_restored_baseline": True})


class NestedIsolated(_Nest):
    """Without the daemon (NestedModes stopped it): negative controls with the builds installed before this change --
    they do not cap."""

    def setUp(self) -> None:
        self.assertIsNone(self.daemon_pid(), "the daemon is stopped (NestedModes)")

    def meter_only(self) -> None:
        """mios-pacing loaded as a meter: no cap, no wallpaper hold."""
        self.assertEqual(self.h("eval", "hl.config({plugin = {mios_pacing = {max_fps = 0, wallpaper_fps = 0}}})").strip(), "ok")

    @unittest.skipUnless(os.access(OLD_WALLPAPERD, os.X_OK), "no installed mios-wallpaperd")
    def test_c_installed_wallpaper_and_compositor_do_not_cap(self) -> None:
        """The installed mios-wallpaperd with the pre-change Host.qml presents at about 60 (two commits per 30 Hz step)
        whatever the tier (W would be 30); without mios-pacing the compositor runs at the output's 144 Hz."""
        self.meter_only()
        old = f"{NEST}/wallpaper-old/Host.qml"
        self.assertTrue(os.path.exists(old), "start.sh copies the pre-change wallpaper host to wallpaper-old/")
        run(["systemctl", "--user", "stop", "mios-fr-c-wallpaper.scope"])
        time.sleep(1.0)
        self.fragment("61-fr-old-wallpaper.toml", f'[theme.wallpaper]\nlinux_host = "{old}"\n')
        try:
            r = run([f"{NEST}/run.sh", "wallpaper-old", OLD_WALLPAPERD, "host"])
            self.assertEqual(r.returncode, 0, r.stderr)
            time.sleep(8.0)  # spawn + the 3.5 s intro
            rates = sorted(x["wallpaper"] for x in self.sample(4.0) if x["wallpaper"] is not None)
            self.assertTrue(rates, "the installed wallpaper maps its layer")
            mid = rates[len(rates) // 2]
            self.assertGreater(mid, 30 * 1.6, f"installed wallpaper: {mid} presents/s (W would be 30): no 1/2 rate")
            self.assertNotIn("compositor pacing", read(f"{NEST}/run/c-wallpaper-old.log"))
            # and no compositor cap at all without the plugin
            so = os.path.join(NEST, "..", "stage", "usr", "lib64", "mios", "mios-pacing.so")
            capped = self.probe_fps(3.0)
            self.assertEqual(self.h("plugin", "unload", so).strip(), "ok")
            time.sleep(1.0)
            free = self.probe_fps(4.0)
            self.assertGreater(free, 100, f"no mios-pacing: the probe client at {free} fps (power-saver would be 60)")
            evidence("nested.negative.installed_wallpaper_and_no_plugin", {"wallpaper_presents_per_s": mid,
                     "probe_fps_meter_only": capped, "probe_fps_without_plugin": free})
        finally:
            run(["systemctl", "--user", "stop", "mios-fr-c-wallpaper-old.scope"])
            self.fragment("61-fr-old-wallpaper.toml", None)

    @unittest.skipUnless(os.access(OLD_MIOSD, os.X_OK), "no installed miosd")
    def test_d_installed_miosd_has_no_policy(self) -> None:
        r = run([OLD_MIOSD, "framerate", "status"], env=self.cli_env())
        self.assertNotEqual(r.returncode, 0, "the installed miosd has no framerate module")
        self.assertRegex(r.stderr + r.stdout, r"unrecognized subcommand|invalid subcommand|error")
        evidence("nested.negative.installed_miosd", {"rc": r.returncode, "stderr": (r.stderr or r.stdout).strip()[:120]})


if __name__ == "__main__":
    unittest.main(verbosity=2)
