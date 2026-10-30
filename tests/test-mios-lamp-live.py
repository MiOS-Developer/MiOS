#!/usr/bin/env python3
# AI-hint: LIVE opt-in test (MIOS_LIVE_NESTED=1): the magic lamp and the wobble of a candidate mios-wobbly.so inside an OWNED nested MiOS PiP session (the plugin is loaded only there, through a private copy of the vendor SSOT): genie minimize into the dock and restore, exact dwindle split-tree restore (restore_tree on vs off), per-window origin output with a second output, hot-unplug re-homing of the origin output, and wobble colour banding. An optional second binary (MIOS_LAMP_PLUGIN_OLD) is the negative control.
# AI-related: usr/share/mios/hyprland/plugins/mios-wobbly/lamp.cpp, usr/share/mios/hyprland/plugins/mios-wobbly/main.cpp, usr/share/mios/quickshell/DockLamp.qml, tools/native/mios-gen/src/nested_session.rs, tests/test-mios-lamp.py
# AI-functions: run_session, main
#
# Usage (inside a MiOS desktop session; nothing is dispatched to the primary compositor -- the launcher alone opens and
# adopts the PiP viewport window):
#   MIOS_LIVE_NESTED=1 MIOS_LAMP_PLUGIN=/path/cand.so [MIOS_LAMP_PLUGIN_OLD=/path/old.so] \
#   [MIOS_NESTED_LAUNCHER=/path/mios-gen] [MIOS_LIVE_OUT=dir] python3 tests/test-mios-lamp-live.py
# Test fixture overrides in the private SSOT copy (never in the vendor SSOT): the plugin path, the nested shell entry
# (this tree's Nested.qml, so the candidate DockLamp.qml runs) and [desktop.mobile].enabled = false (the small PiP
# viewport would otherwise be a phone-style monocle output and nothing would tile).
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(os.environ.get("MIOS_LIVE_OUT") or tempfile.mkdtemp(prefix="mios-lamp-live-"))
LAUNCHER = os.environ.get("MIOS_NESTED_LAUNCHER", "/usr/bin/mios-gen")
SSOT = Path(os.environ.get("MIOS_VENDOR_TOML") or ROOT / "usr/share/mios/mios.toml")
FILL = "#!/bin/bash\nprintf '\\033[?25l'\nif [ \"$1\" = ramp ]; then\n" \
    "  for r in 1 2 3 4 5 6; do for i in $(seq 0 63); do v=$((i*4)); printf '\\033[48;2;%d;%d;%dm ' $v $v $v; done; printf '\\033[0m\\n'; done\n" \
    "  for r in $(seq 1 8); do printf '\\033[48;2;128;128;128m%20s\\033[48;2;220;120;30m%20s\\033[48;2;230;230;230m%20s\\033[0m\\n' '' '' ''; done\n" \
    "fi\nexec sleep 3600\n"
COLORS = {"A": (230, 30, 30), "B": (30, 200, 30), "C": (30, 30, 230), "D": (230, 200, 30), "E": (30, 200, 200), "W": (106, 90, 205)}
HEADLESS = "HEADLESS-LAMP"
results = []


def check(label, name, ok, detail=""):
    results.append((f"[{label}] {name}", bool(ok), str(detail)))
    print(f"{'PASS' if ok else 'FAIL'} [{label}] {name} {detail}", flush=True)


def note(label, name, detail):
    print(f"INFO [{label}] {name} {detail}", flush=True)


def private_ssot(plugin: str, label: str) -> Path:
    src = SSOT.read_text(encoding="utf-8")
    subs = [
        ('path = "/usr/lib64/mios/mios-wobbly.so"', f'path = "{plugin}"'),
        ('shell_entry = "/usr/share/mios/quickshell/Nested.qml"', f'shell_entry = "{ROOT}/usr/share/mios/quickshell/Nested.qml"'),
        ("[desktop.mobile]\nenabled              = true", "[desktop.mobile]\nenabled              = false"),
    ]
    for a, b in subs:
        if src.count(a) != 1:
            raise RuntimeError(f"SSOT fixture anchor not found exactly once: {a!r}")
        src = src.replace(a, b)
    p = OUT / f"vendor-{label}.toml"
    p.write_text(src, encoding="utf-8")
    p.chmod(0o600)
    return p


class Nested:
    def __init__(self, label: str, plugin: str):
        self.label = label
        self.plugin = plugin
        self.procs = []
        env = {k: v for k, v in os.environ.items() if k != "MIOS_TOML"}
        env["MIOS_VENDOR_TOML"] = str(private_ssot(plugin, label))
        self.launcher = subprocess.Popen([LAUNCHER, "nested-session", "--layout", "pip"], env=env,
                                         stdout=open(OUT / f"launcher-{label}.log", "w"), stderr=subprocess.STDOUT)
        self.dir = Path(os.environ["XDG_RUNTIME_DIR"]) / f"mios-nested-desktop-{self.launcher.pid}"
        self.env = None
        self.pid = None
        for _ in range(300):
            if (self.dir / "session.json").is_file():
                s = json.loads((self.dir / "session.json").read_text())
                self.pid = s["instance"]["pid"]
                self.env = dict(os.environ, XDG_RUNTIME_DIR=str(self.dir), HYPRLAND_INSTANCE_SIGNATURE=s["instance"]["instance"],
                                WAYLAND_DISPLAY=s["instance"]["wl_socket"])
                for k in ("DISPLAY", "MIOS_TOML", "TMUX", "TMUX_PANE"):
                    self.env.pop(k, None)
                break
            time.sleep(0.2)
        if not self.env:
            raise RuntimeError("nested session did not start")
        if self.env["HYPRLAND_INSTANCE_SIGNATURE"] == os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
            raise RuntimeError("refusing: nested signature equals the primary's")

    # ---- compositor (nested only: XDG_RUNTIME_DIR is the session's own runtime dir) ----------------------------
    def ctl(self, *a, timeout=15):
        return subprocess.run(["hyprctl", *a], env=self.env, capture_output=True, text=True, timeout=timeout).stdout

    def j(self, *a):
        for _ in range(5):
            try:
                return json.loads(self.ctl("-j", *a))
            except json.JSONDecodeError:
                time.sleep(0.1)
        return None

    def dispatch(self, lua):
        return self.ctl("dispatch", lua).strip()

    def alive(self):
        try:
            os.kill(self.pid, 0)
        except ProcessLookupError:
            return False
        return isinstance(self.j("monitors"), list)

    def clients(self):
        return self.j("clients") or []

    def client(self, cls):
        return next((c for c in self.clients() if c["class"] == cls), None)

    def box(self, cls):
        c = self.client(cls)
        return (tuple(c["at"]) + tuple(c["size"])) if c else None

    def lamp(self):
        try:
            return json.loads(self.ctl("mios-lamp", "status"))
        except json.JSONDecodeError:
            return None

    def restart_shell(self):
        """Replace the session's own Quickshell (same argv, env and input sandbox as the launcher): every QML cache is
        lost, so whatever the shell knows afterwards came from the compositor and the plugin's file."""
        own = None
        for pid in filter(str.isdigit, os.listdir("/proc")):
            try:
                argv = open(f"/proc/{pid}/cmdline", "rb").read().split(b"\0")[:-1]
                if not argv or not argv[0].endswith(b"quickshell"):
                    continue
                env = dict(kv.split(b"=", 1) for kv in open(f"/proc/{pid}/environ", "rb").read().split(b"\0") if b"=" in kv)
            except OSError:
                continue
            if env.get(b"XDG_RUNTIME_DIR") == str(self.dir).encode():
                own = (int(pid), [a.decode() for a in argv], {k.decode(): v.decode() for k, v in env.items()})
                break
        if not own:
            return False
        pid, argv, env = own
        os.kill(pid, signal.SIGTERM)
        wait_for(lambda: not os.path.exists(f"/proc/{pid}"), 5)
        self.procs.append(subprocess.Popen(["bwrap", "--die-with-parent", "--bind", "/", "/", "--dev-bind", "/dev", "/dev", "--ro-bind", "/dev/null",
                                            "/tmp/mios-hypr3d-pad.bin", "--ro-bind", str(self.dir / "input"), "/dev/input", "--", *argv],
                                           env=env, stdout=open(OUT / "shell-restart.log", "a"), stderr=subprocess.STDOUT))
        return True

    def shell_state(self):
        r = subprocess.run(["quickshell", "ipc", "--path", str(ROOT / "usr/share/mios/quickshell/Nested.qml"), "--any-display", "call", "docklamp", "state"],
                           env=self.env, capture_output=True, text=True, timeout=15)
        try:
            return json.loads(r.stdout)
        except ValueError:
            return {}

    def runtime_json(self, name):
        try:
            return json.loads((self.dir / name).read_text())
        except (OSError, ValueError):
            return None

    def monitor(self, name):
        return next((m for m in self.j("monitors") or [] if m["name"] == name), None)

    def grab(self, output="WAYLAND-1"):
        m = self.monitor(output)
        raw = subprocess.run(["grim", "-t", "ppm", "-s", str(m["scale"]), "-o", output, "-"], env=self.env, capture_output=True, timeout=10).stdout
        return Image.open(io.BytesIO(raw)).convert("RGB"), m["scale"]

    # ---- test windows ----------------------------------------------------------------------------------------
    def open(self, key, mode="solid", wait=10.0):
        cls = f"lampt.{key}"
        cfg = OUT / f"alac-{key}.toml"
        cfg.write_text('[colors.primary]\nbackground = "#%02x%02x%02x"\nforeground = "#ffffff"\n[window]\nopacity = 1.0\n'
                       'padding = { x = 0, y = 0 }\n[font]\nsize = 10.0\n' % COLORS[key])
        fill = OUT / "fill.sh"
        fill.write_text(FILL)
        fill.chmod(0o700)
        self.procs.append(subprocess.Popen(["alacritty", "--config-file", str(cfg), "--class", cls, "-T", cls, "-e", str(fill), mode],
                                           env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True))
        end = time.time() + wait
        while time.time() < end:
            c = self.client(cls)
            if c:
                addr = c["address"]
                # opaque (the SSOT's 0.85 would mix the background into the colour masks)
                self.ctl("eval", f"hl.dispatch(hl.dsp.window.set_prop({{prop='opacity', value='1 override 1 override', window='address:{addr}'}}))")
                time.sleep(0.8)
                return addr
            time.sleep(0.1)
        raise RuntimeError(f"{cls} did not map")

    def stop(self):
        for p in self.procs:
            if p.poll() is None:
                p.terminate()
        for p in self.procs:
            try:
                p.wait(3)
            except subprocess.TimeoutExpired:
                p.kill()
        if self.pid:
            try:
                os.kill(self.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        try:
            self.launcher.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self.launcher.kill()


# ---- pixels ----------------------------------------------------------------------------------------------------
def color_mask(im, rgb, tol=40):
    bands = []
    for band, v in zip(im.split(), rgb):
        bands.append(band.point(lambda x, v=v: 255 if abs(x - v) <= tol else 0))
    m = ImageChops.multiply(ImageChops.multiply(bands[0], bands[1]), bands[2])
    return m


def mask_stats(m):
    area = m.histogram()[255]
    bb = m.getbbox()
    return area, bb


def levels(im, box, minc=12):
    """Luminance levels that cover >= minc pixels: the grey ramp's 64 steps survive resampling as populated levels;
    posterisation (an 8-bit work buffer at a low layout alpha, then a gain) leaves only a dozen."""
    return sum(1 for c in im.crop(box).convert("L").histogram() if c >= minc)


def posterise(im, alpha=0.05):
    """What the R2 wobble did to window content: draw at layout alpha 0.05 into an 8-bit buffer, then x1/alpha."""
    return im.point(lambda v: min(255, int(round(v * alpha)) * int(round(1 / alpha))))


def sample(n, seconds, action, out_tag, cls_rgb, output="WAYLAND-1"):
    """Run action, then sample lamp status and frames for `seconds`."""
    action()
    t0 = time.monotonic()
    st, frames = [], []
    while time.monotonic() - t0 < seconds:
        s = n.lamp() or {}
        st.append((round((time.monotonic() - t0) * 1000), s.get("animating", [])))
        im, _ = n.grab(output)
        frames.append(mask_stats(color_mask(im, cls_rgb)))
        if len(frames) in (2, 4, 6):
            im.save(OUT / f"{out_tag}-f{len(frames)}.png")
    return st, frames


def wait_for(cond, timeout=8.0, step=0.1):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = cond()
        if v:
            return v
        time.sleep(step)
    return cond()


# ---- scenarios -------------------------------------------------------------------------------------------------
def scenario_lamp_and_tree(n, L, negative):
    """Workspace 3: A | (B / C) with custom split ratios; genie-minimize B into the dock, restore it (tree on), then the
    same with restore_tree = false (negative control: dwindle re-inserts B with an even split)."""
    n.dispatch('hl.dsp.focus({workspace = "3"})')
    n.ctl("eval", "hl.config({dwindle = {force_split = 2}})")  # new tiles split right/below whatever the cursor
    time.sleep(0.5)
    a = n.open("A")
    b = n.open("B")
    n.dispatch(f'hl.dsp.focus({{window = "address:{a}"}})')
    n.dispatch('hl.dsp.layout("splitratio 0.6 exact")')
    n.dispatch(f'hl.dsp.focus({{window = "address:{b}"}})')
    c = n.open("C")
    n.dispatch(f'hl.dsp.focus({{window = "address:{c}"}})')
    n.dispatch('hl.dsp.layout("splitratio 1.4 exact")')
    time.sleep(0.8)
    before = {k: n.box(f"lampt.{k}") for k in "ABC"}
    note(L, "tiles before", before)
    check(L, "three tiles with custom split ratios (A narrower than half, B wider than C)",
          all(before.values()) and before["A"][2] < before["B"][2] + before["C"][2] - 50 and before["B"][2] > before["C"][2] + 20, before)

    def run(tag, tree):
        n.ctl("eval", f"hl.config({{plugin = {{mios_lamp = {{restore_tree = {'true' if tree else 'false'}}}}}}})")
        dock = (n.runtime_json("mios-dock-icons.json") or {}).get("outputs", {}).get("WAYLAND-1", {})
        st, fr = sample(n, 1.1, lambda: n.dispatch(f'hl.plugin.mios_lamp.minimize({{window = "address:{b}"}})'), f"{L}-{tag}-min", COLORS["B"])
        anim = [a for _, al in st for a in al if a.get("address") == b and a.get("kind") == "minimize"]
        ws = (n.client("lampt.B") or {}).get("workspace", {}).get("name")
        areas = [f[0] for f in fr]
        a0 = areas[0] if areas else 0
        mid = [x for x in areas if 0.05 * a0 < x < 0.9 * a0]
        bot = [f[1][3] for f in fr if f[1] and 0.05 * a0 < f[0] < 0.9 * a0]
        if negative:
            note(L, f"{tag}: minimize animation samples", f"{len(anim)} animating, ws={ws}")
        else:
            check(L, f"{tag}: genie minimize ran (plugin status: kind=minimize, t rising to 1, target in the dock)",
                  anim and anim[-1]["t"] >= 0.9 and anim[0]["t"] <= 0.5 and dock and anim[0]["target"][1] >= dock.get("dock_rect", [0, 1e9])[1] - 2,
                  f"{len(anim)} samples, t {anim[0]['t'] if anim else None}->{anim[-1]['t'] if anim else None}, target {anim[0]['target'] if anim else None}, dock {dock.get('dock_rect')}")
            check(L, f"{tag}: frames show the window funnelling down into the dock (partial areas, bottom edge at the dock), then gone",
                  len(mid) >= 2 and areas[-1] < 0.05 * a0 and bot and max(bot) >= fr[0][1][3] if fr and fr[0][1] else False,
                  f"areas {areas[:12]} bottoms {bot[:8]} rest bottom {fr[0][1][3] if fr and fr[0][1] else None}")
        check(L, f"{tag}: B parked on special:minimized", ws == "special:minimized", ws)
        mid_boxes = {k: n.box(f"lampt.{k}") for k in "AC"}
        st, fr = sample(n, 1.0, lambda: n.dispatch(f'hl.plugin.mios_lamp.restore({{window = "address:{b}"}})'), f"{L}-{tag}-res", COLORS["B"])
        ranim = [a for _, al in st for a in al if a.get("address") == b and a.get("kind") == "restore"]
        time.sleep(0.6)
        after = {k: n.box(f"lampt.{k}") for k in "ABC"}
        if not negative:
            check(L, f"{tag}: restore animated out of the dock (kind=restore, t falling)", ranim and ranim[0]["t"] >= 0.5, f"{len(ranim)} samples")
        diff = max(abs(p - q) for k in "ABC" for p, q in zip(before[k], after[k] or (0, 0, 0, 0)))
        return mid_boxes, after, diff

    mid, after, diff = run("tree-on", True)
    note(L, "tree-on boxes", f"while minimized {mid} after {after}")
    if negative:
        note(L, "tree-on max box error (old binary)", diff)
    else:
        check(L, "restore_tree on: every tile back at its exact pre-minimize box (structure AND ratios)", diff <= 2, f"max error {diff}px {after}")
    # negative control: same binary, restore_tree off -> slot heuristics, the custom 1.4 ratio is not reproduced
    mid, after2, diff2 = run("tree-off", False)
    check(L, "NEGATIVE CONTROL restore_tree off: the recorded split ratio is lost (some tile off by > 8px)", diff2 > 8, f"max error {diff2}px {after2}")


def scenario_two_outputs(n, L, negative):
    """A second output (headless), a window minimized from each output, then the origin output is unplugged."""
    r = n.ctl("output", "create", "headless", HEADLESS).strip()
    n.ctl("eval", f'hl.monitor({{output = "{HEADLESS}", mode = "1920x1080", position = "4000x0", scale = 1}})')
    mon = wait_for(lambda: n.monitor(HEADLESS), 5)
    check(L, "second output created in the nested compositor", mon, f"{r} {mon and (mon['width'], mon['height'])}")
    if mon and (mon["width"] == 0 or mon["height"] == 0):
        note(L, "second output has no mode", "the nested Wayland backend's GBM allocator cannot allocate headless buffers here; logical checks only")
    dock = wait_for(lambda: HEADLESS in ((n.runtime_json("mios-dock-icons.json") or {}).get("outputs") or {}), 6)
    check(L, "nested shell publishes a dock for the second output", dock)
    hws = (mon or {}).get("activeWorkspace", {}).get("name", "2")
    d = n.open("D")
    n.dispatch(f'hl.dsp.window.move({{workspace = "{hws}", window = "address:{d}", follow = false}})')
    time.sleep(0.6)
    check(L, "D lives on the second output", (n.client("lampt.D") or {}).get("monitor") == (mon or {}).get("id"), n.client("lampt.D"))
    n.dispatch('hl.dsp.focus({workspace = "4"})')
    e = n.open("E")
    for w in (d, e):
        n.dispatch(f'hl.plugin.mios_lamp.minimize({{window = "address:{w}"}})')
        wait_for(lambda w=w: next((c for c in n.clients() if c["address"] == w and c["workspace"]["name"] == "special:minimized"), None), 3)
    time.sleep(0.8)
    cd, ce = n.client("lampt.D"), n.client("lampt.E")
    note(L, "Hyprland's own monitor for the two minimized windows", (cd or {}).get("monitor"), )
    pub = n.runtime_json("mios-lamp-minimized.json")
    by = {m["address"]: m["monitor"] for m in (pub or {}).get("minimized", [])}
    if negative:
        check(L, "NEGATIVE CONTROL old binary: no per-window origin file", pub is None, pub)
    else:
        check(L, "both share special:minimized's single monitor in Hyprland", cd and ce and cd["monitor"] == ce["monitor"], (cd or {}).get("monitor"))
        check(L, "plugin file keeps each window's own origin (D: second output, E: WAYLAND-1)", by.get(d) == HEADLESS and by.get(e) == "WAYLAND-1", by)
        outs = wait_for(lambda: (n.runtime_json("mios-dock-icons.json") or {}).get("outputs"), 3) or {}

        def tile_on(out, addr):
            return any(str(t.get("address", "")).lower() == addr.lower() for t in (outs.get(out) or {}).get("minimized", []))
        outs = wait_for(lambda: (lambda o: o if tile_on(HEADLESS, d) and tile_on("WAYLAND-1", e) else None)((n.runtime_json("mios-dock-icons.json") or {}).get("outputs") or {}), 4) or outs
        sh = wait_for(lambda: (lambda s: s if s.get("plugin_origin", {}).get(d) == HEADLESS else None)(n.shell_state()), 4) or n.shell_state()
        smw = {m.get("address"): m.get("monitorName") for m in sh.get("minimized_windows", [])}
        check(L, "the shell (DockLamp, inotify on the plugin file) puts D on the second output and E on WAYLAND-1", smw.get(d) == HEADLESS and smw.get(e) == "WAYLAND-1", smw)
        check(L, "dock shows D's minimized tile on the second output and E's on WAYLAND-1", tile_on(HEADLESS, d) and tile_on("WAYLAND-1", e),
              {k: [t.get("address") for t in v.get("minimized", [])] for k, v in outs.items()})
    # the shell restarts while D and E stay minimized: its own origin caches are gone, only the plugin's file knows
    check(L, "nested shell restarted (QML caches dropped)", n.restart_shell())
    sh = wait_for(lambda: (lambda s: s if len(s.get("minimized_windows", [])) >= 2 else None)(n.shell_state()), 20) or {}
    time.sleep(1.0)
    sh = n.shell_state() or sh
    smw = {m.get("address"): m.get("monitorName") for m in sh.get("minimized_windows", [])}
    if negative:
        check(L, "NEGATIVE CONTROL old binary: after a shell restart D's origin output is lost", smw.get(d) != HEADLESS, smw)
    else:
        check(L, "after a shell restart the shell still puts D on the second output and E on WAYLAND-1 (plugin file)",
              smw.get(d) == HEADLESS and smw.get(e) == "WAYLAND-1", smw)
    # hot-unplug the origin output of D
    n.ctl("output", "remove", HEADLESS)
    time.sleep(1.2)
    check(L, "nested compositor survives unplugging the origin output", n.alive())
    pub = n.runtime_json("mios-lamp-minimized.json")  # read BEFORE any `hyprctl mios-lamp status` (that prunes + re-homes)
    by = {m["address"]: m["monitor"] for m in (pub or {}).get("minimized", [])}
    if not negative:
        check(L, "plugin re-homed D to the surviving output on unplug (no status poll needed)", by.get(d) == "WAYLAND-1", by)
        outs = wait_for(lambda: (lambda o: o if HEADLESS not in o else None)((n.runtime_json("mios-dock-icons.json") or {}).get("outputs") or {}), 4)
        check(L, "dock dropped the unplugged output", outs is not None and HEADLESS not in outs, list((outs or {}).keys()))
    st = n.lamp() or {}
    smon = {m["address"]: m["monitor"] for m in st.get("minimized", [])}
    if negative:
        check(L, "NEGATIVE CONTROL old binary: D still claims the dead output", smon.get(d) == HEADLESS, smon)
    else:
        check(L, "status agrees: D's origin is WAYLAND-1", smon.get(d) == "WAYLAND-1", smon)
    st, _ = sample(n, 0.8, lambda: n.dispatch(f'hl.plugin.mios_lamp.restore({{window = "address:{d}"}})'), f"{L}-unplug-res", COLORS["D"])
    time.sleep(0.6)
    cd = n.client("lampt.D") or {}
    m0 = n.monitor("WAYLAND-1") or {}
    ok = cd.get("monitor") == m0.get("id") and not cd.get("workspace", {}).get("name", "special").startswith("special")
    if negative:
        note(L, "restore after unplug (old binary)", cd.get("workspace"))
    else:
        check(L, "D restores onto the surviving output, visible", ok, (cd.get("workspace"), cd.get("monitor"), cd.get("at"), cd.get("size")))
    # E is closed while still minimized: the published list drops it at once (no status poll)
    n.dispatch(f'hl.dsp.window.close({{window = "address:{e}"}})')
    gone = wait_for(lambda: not any(c["address"] == e for c in n.clients()), 4)
    pub = n.runtime_json("mios-lamp-minimized.json")
    if not negative:
        check(L, "a window closed while minimized leaves the published list", gone and pub is not None and all(m["address"] != e for m in pub.get("minimized", [])), pub)


def ghost_px(im, box, bg):
    """Pixels that are the workspace background lightened by a faint glass tint (no channel darker, channel sum +2..+20):
    the rigid hyprglass pane (and its bevel) showing where the bent content no longer covers the real window box."""
    n = 0
    for r, g, b in im.crop(box).getdata():
        d0, d1, d2 = r - bg[0], g - bg[1], b - bg[2]
        if d0 >= 0 and d1 >= 0 and d2 >= 0 and 2 <= d0 + d1 + d2 <= 20:
            n += 1
    return n


def scenario_wobble(n, L, negative):
    """A floating grey-ramp window moved by the compositor wobbles. Default pane_fade = 1: the deformed content keeps its
    colour levels and the full-strength rigid glass pane shows beside it; pane_fade = 0.4: the pane is hidden and the
    compensated content still keeps its levels (no 8-bit banding)."""
    n.dispatch('hl.dsp.focus({workspace = "5"})')
    w = n.open("W", "ramp")
    n.dispatch(f'hl.dsp.window.float({{action = "toggle", window = "address:{w}"}})')
    n.dispatch(f'hl.dsp.window.resize({{x = 760, y = 420, window = "address:{w}"}})')
    n.dispatch(f'hl.dsp.window.move({{x = 200, y = 200, window = "address:{w}"}})')
    time.sleep(1.2)
    im0, scale = n.grab()
    A, B = (200, 200), (560, 330)
    x, y, bw, bh = n.box("lampt.W")
    union = tuple(int(round(v * scale)) for v in (A[0] - 20, A[1] - 20, B[0] + bw + 20, B[1] + bh + 20))
    union = (max(0, union[0]), max(0, union[1]), min(im0.width, union[2]), min(im0.height, union[3]))

    def fill(im):
        a, bb = mask_stats(color_mask(im.crop(union), (220, 120, 30), 30))
        return a / max(1, (bb[2] - bb[0]) * (bb[3] - bb[1])) if bb else 1.0

    def run(tag, to):
        rest, _ = n.grab()
        rest.save(OUT / f"{L}-wob-{tag}-rest.png")
        bg = rest.getpixel((union[2] - 4, union[1] + 4))  # top-right of the region: workspace background in both runs
        lv0, g0 = levels(rest, union), ghost_px(rest, union, bg)
        n.dispatch(f'hl.dsp.window.move({{x = {to[0]}, y = {to[1]}, window = "address:{w}"}})')
        frames = []
        t0 = time.monotonic()
        while time.monotonic() - t0 < 1.4:
            frames.append(n.grab()[0])
        time.sleep(1.0)
        fills = [round(fill(im), 3) for im in frames]
        wob = [i for i, f in enumerate(fills) if f < 0.98]  # rest: exactly 1.0
        if wob:
            frames[min(wob, key=lambda i: fills[i])].save(OUT / f"{L}-wob-{tag}-deformed.png")
        lv = [levels(frames[i], union) for i in wob]
        pv = [levels(posterise(frames[i]), union) for i in wob]
        gh = max((ghost_px(frames[i], union, bg) for i in wob), default=0)
        res = {"rest_fill": round(fill(rest), 3), "fills": fills[:16], "wob": len(wob), "levels_rest": lv0, "levels_min": min(lv) if lv else None,
               "ratio": (min(lv) / lv0) if lv and lv0 else 0, "posterised_ratio": (min(pv) / lv0) if pv and lv0 else 1, "ghost_rest": g0, "ghost_max": gh,
               "settled_ghost": ghost_px(n.grab()[0], union, bg)}
        note(L, f"wobble {tag}", res)
        return res

    r = run("pane1", B)
    check(L, "at rest the orange patch is a rectangle", r["rest_fill"] >= 0.95, r["rest_fill"])
    if negative:
        check(L, "NEGATIVE CONTROL old binary: a compositor-driven move does not wobble (drag-only)", r["wob"] == 0, r["fills"])
        return
    check(L, "the moved floating window wobbles (orange patch no longer a rectangle)", r["wob"] >= 2, r["fills"])
    check(L, "pane_fade 1 (default): no banding, the deformed window keeps >= 80 % of its rest luminance levels", r["ratio"] >= 0.8, f"ratio {r['ratio']:.2f}")
    check(L, "NEGATIVE CONTROL detector: the same frames posterised like the R2 0.05 fade drop below 60 %", r["posterised_ratio"] < 0.6, f"ratio {r['posterised_ratio']:.2f}")
    check(L, "pane_fade 1 (default, documented trade-off): the full-strength rigid glass pane shows beside the bent content",
          r["ghost_max"] - r["ghost_rest"] > 1000, f"ghost {r['ghost_rest']} -> {r['ghost_max']}")
    n.ctl("eval", "hl.config({plugin = {mios_wobbly = {pane_fade = 0.4}}})")
    r4 = run("pane04", A)
    check(L, "pane_fade 0.4: the rigid pane is hidden while the shape bends (ghost < 1/4 of the pane_fade 1 ghost)",
          r4["wob"] >= 2 and (r4["ghost_max"] - r4["ghost_rest"]) * 4 < (r["ghost_max"] - r["ghost_rest"]), f"ghost {r4['ghost_rest']} -> {r4['ghost_max']}")
    check(L, "pane_fade 0.4: the compensated content keeps >= 80 % of its rest luminance levels (no banding)", r4["ratio"] >= 0.8, f"ratio {r4['ratio']:.2f}")
    check(L, "pane_fade 0.4: after settling the window is back at full glass (no ghost, alpha 1)", r4["settled_ghost"] <= r4["ghost_rest"] + 50,
          f"settled {r4['settled_ghost']} rest {r4['ghost_rest']}")
    n.ctl("eval", "hl.config({plugin = {mios_wobbly = {pane_fade = 1}}})")


def run_session(label, plugin, negative):
    portal0 = open("/proc/self/mountinfo").read().count("fuse.portal")
    n = None
    try:
        n = Nested(label, plugin)
        check(label, "owned nested PiP session", True, n.dir.name)
        maps = open(f"/proc/{n.pid}/maps").read()
        check(label, "the binary under test is loaded in the NESTED compositor only", plugin in maps and plugin not in open("/proc/%s/maps" % os.environ.get("PRIMARY_PID", subprocess.run(["pgrep", "-x", "-o", "Hyprland"], capture_output=True, text=True).stdout.strip() or "1")).read(), plugin)
        wait_for(lambda: (n.runtime_json("mios-dock-icons.json") or {}).get("outputs", {}).get("WAYLAND-1"), 30)
        check(label, "nested shell dock published", (n.runtime_json("mios-dock-icons.json") or {}).get("outputs", {}).get("WAYLAND-1"))
        if not negative:
            check(label, "plugin published its (empty) minimized-origin file at load", (n.runtime_json("mios-lamp-minimized.json") or {}).get("minimized") == [])
        for sc in (scenario_lamp_and_tree, scenario_two_outputs, scenario_wobble):
            try:
                sc(n, label, negative)
            except Exception as e:  # one scenario failing must not hide the others
                check(label, f"{sc.__name__} driver", False, repr(e))
            if not n.alive():
                check(label, "nested compositor alive", False, sc.__name__)
                break
    except Exception as e:
        check(label, "driver", False, repr(e))
    finally:
        if n:
            n.stop()
            check(label, "nested runtime cleaned up", wait_for(lambda: not n.dir.exists(), 15))
        check(label, "no portal mount added", open("/proc/self/mountinfo").read().count("fuse.portal") == portal0)


def main():
    cand = os.environ.get("MIOS_LAMP_PLUGIN")
    if not cand or not os.path.isfile(cand):
        print("skip: MIOS_LAMP_PLUGIN (candidate mios-wobbly.so) missing")
        return 0
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"output: {OUT}", flush=True)
    run_session("candidate", os.path.abspath(cand), False)
    old = os.environ.get("MIOS_LAMP_PLUGIN_OLD")
    if old and os.path.isfile(old):
        run_session("old", os.path.abspath(old), True)
    ok = sum(1 for r in results if r[1])
    print(f"{ok}/{len(results)} passed", flush=True)
    (OUT / "results.json").write_text(json.dumps([{"check": a, "ok": b, "detail": c} for a, b, c in results], indent=1))
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    if os.environ.get("MIOS_LIVE_NESTED") != "1" or not os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        print("skip: live nested-session test (set MIOS_LIVE_NESTED=1 inside a MiOS desktop session)")
        sys.exit(0)
    sys.exit(main())
