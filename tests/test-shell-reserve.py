#!/usr/bin/env python3
# AI-hint: Reserved rows of the shell (operator 2026-10-17 22:45: "stuck in full screen-tiling"; a tiled window ran under the
# status island and the dock because Hyprland's reserved area for the output was [0, 0, 0, 0]). The status island
# (PanelWindow.qml) and the dock (Sidebar.qml) reserve their rows with layer-shell exclusive zones; Hyprland drops the WHOLE
# reserved area of an output when the zones do not fit it (CReservedArea from an empty / invalid usable box), and
# Quickshell only re-sends layer state that differs from what it believes it committed, so a lost zone is never re-sent by
# itself. EdgeReveal.qml's guard compares the compositor's reserved area with what each surface expects
# (reserveExpectedNow()) and re-asserts lost zones (nudge, then recreate, then back off and report).
#   1. static (always, MIOS_QS_DIR or the repo QML): the island's exclusionMode is a constant Normal (no Ignore/Normal
#      ternary racing Quickshell's "writing exclusiveZone sets Normal"), its zone and the dock's carry the guard's nudge,
#      both register with EdgeReveal and report their expected reservation, EdgeReveal runs the guard (events + heartbeat,
#      the three stages, the "reserve-<output>" IPC). Mutated copies fail.
#   2. bindings (always): [desktop.hyprland].bindings carries "SUPER + F11" (window.fullscreen, fullscreen, toggle,
#      "Toggle fullscreen") and "SUPER + SHIFT + F11" (maximized, "Toggle maximized"); a scratch `mios-gen render-hyprland
#      --root <tmp>` projects exactly those hl.bind lines (luajit-parseable) and refuses an unsafe dispatcher.
#   3. nested (MIOS_RESERVE_NEST_SIG + MIOS_RESERVE_NEST_XDG + MIOS_RESERVE_NEST_WL: a NESTED test compositor started from
#      the current projection with a private XDG_RUNTIME_DIR and a HEADLESS-DESK output -- the lane harness
#      reserve-fix/nest/start.sh; never the live desktop): the test starts its OWN private dbus-daemon (no service dirs:
#      nothing is ever activated, no portals) and its own Quickshell from a staged copy of the QML (plus a fault-injection
#      copy and a foreign oversized-zone shell), opens a tiled window and checks after every trigger that the output
#      reserves [0, top, 0, bottom] (top = top_height + 2 x edge_margin, bottom = dock_height + 2 x edge_margin from the
#      theme bridge) and the tile ends below the island and above the dock (positive): a hot reload, a reload that rebuilds
#      the surfaces, a headless output added / resized / removed the way the RDP bridge does it, a dock minimize + restore
#      through the lamp, a Hyprland config reload, maximize on/off, fullscreen exit with the island revealed by a
#      notification, and lost zones (fault injection: both surfaces' exclusionMode forced to Ignore -> [0,0,0,0]) restored by
#      the guard. Negative: under a real fullscreen window it is [0,0,0,0] and the guard does nothing; after leaving
#      fullscreen it comes back; another client's oversized zone empties the area, the guard tries twice, backs off (no
#      loop) and recovers once that client is gone. Root cause reproduced last: Quickshell < 1bd5b083 leaks a Qt
#      property update group (HyprlandToplevel::updateFromObject returns inside it when j/clients names a workspace the
#      shell does not track); computed bindings then freeze, a reload's surfaces send a zone of 0 -> [0,0,0,0]; the guard
#      detects it (binding probe), does not rebuild surfaces in a loop, reports "frozen", and a new shell process recovers
#      (skipped when the race cannot be provoked). The portal FUSE mount count is checked before and after.
# AI-related: usr/share/mios/quickshell/EdgeReveal.qml, usr/share/mios/quickshell/PanelWindow.qml,
# usr/share/mios/quickshell/Sidebar.qml, usr/share/mios/quickshell/Config.qml, usr/share/mios/quickshell/Theme.qml,
# tests/test-notify-island.py
"""Shell reserved-rows (exclusive zone) tests."""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
import unittest

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
QS_DIR = os.environ.get("MIOS_QS_DIR") or os.path.join(ROOT, "usr", "share", "mios", "quickshell")
THEME = os.environ.get("MIOS_THEME_BRIDGE") or "/etc/mios/theme/theme.json"
NEST_SIG = os.environ.get("MIOS_RESERVE_NEST_SIG", "")
NEST_XDG = os.environ.get("MIOS_RESERVE_NEST_XDG", "")
NEST_WL = os.environ.get("MIOS_RESERVE_NEST_WL", "")
DESK = os.environ.get("MIOS_RESERVE_NEST_DESK", "HEADLESS-DESK")


def read(name: str, qs_dir: str = QS_DIR) -> str:
    with open(os.path.join(qs_dir, name), encoding="utf-8") as f:
        return f.read()


def write(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def code(src: str) -> str:
    """The source without // comments (so a comment never satisfies or breaks a contract)."""
    return "\n".join(line.split("//", 1)[0] for line in src.splitlines())


# ---- static ------------------------------------------------------------------------------------------------------
def static_errors(panel: str, sidebar: str, edge: str) -> list[str]:
    e = []
    p, s, g = code(panel), code(sidebar), code(edge)
    # island
    if not re.search(r"^\s*exclusionMode:\s*QS\.ExclusionMode\.Normal\s*$", p, re.M):
        e.append("PanelWindow: exclusionMode must be the constant Normal (an Ignore/Normal binding races the exclusiveZone write)")
    if re.search(r"exclusionMode:\s*[^\n]*\?", p):
        e.append("PanelWindow: exclusionMode is a conditional binding")
    if not re.search(r"^\s*exclusiveZone:\s*zoneFor\(overFullscreen,[^\n]*\)\s*\+\s*\(overFullscreen \? 0 : reserveNudge\)", p, re.M):
        e.append("PanelWindow: the zone does not carry the guard's nudge (0 under a fullscreen window)")
    for fn in ("reserveExpectedNow", "reserveDiag", "nudgeReserve", "recreateReserve"):
        if not re.search(r"function %s\(" % fn, p):
            e.append(f"PanelWindow: no {fn}()")
    if not re.search(r"reveal\.topSurface\s*=\s*pill", p):
        e.append("PanelWindow: does not register with EdgeReveal (reveal.topSurface)")
    if not re.search(r"^\s*visible:[^\n]*&&\s*!reserveRebuild", p, re.M):
        e.append("PanelWindow: visible does not honour the guard's rebuild")
    # dock
    if not re.search(r"exclusiveZone:\s*zoneFor\(root\.autohide \|\| !visible \|\| root\.fullscreen,", s):
        e.append("Sidebar: the dock zone is not 0 under autohide / hidden / fullscreen")
    if "reserveNudge > 0 && !(root.autohide || !visible || root.fullscreen) ? reserveNudge : 0" not in s:
        e.append("Sidebar: the dock zone does not carry the guard's nudge")
    for fn in ("reserveExpectedNow", "reserveDiag", "nudgeReserve", "recreateReserve"):
        if not re.search(r"function %s\(" % fn, s):
            e.append(f"Sidebar: no {fn}()")
    if not re.search(r"root\.reveal\.bottomSurface\s*=\s*dock", s):
        e.append("Sidebar: the dock does not register with EdgeReveal (reveal.bottomSurface)")
    if not re.search(r"visible:\s*!reserveRebuild\s*&&", s):
        e.append("Sidebar: visible does not honour the guard's rebuild")
    if re.search(r"exclusionMode:", s):
        e.append("Sidebar: the dock sets an exclusionMode (its zone write sets Normal)")
    # guard
    for prop in ("topSurface", "bottomSurface"):
        if not re.search(r"property var %s: null" % prop, g):
            e.append(f"EdgeReveal: no {prop}")
    if not re.search(r"o\s*\?\s*o\.reserved", g):
        e.append("EdgeReveal: does not read the compositor's reserved area (monitor.lastIpcObject.reserved)")
    for ev in ("openlayer", "closelayer", "monitoraddedv2", "monitorremovedv2", "configreloaded", "fullscreen"):
        if '"%s"' % ev not in g:
            e.append(f"EdgeReveal: the guard does not re-check on {ev}")
    if not re.search(r"interval:\s*5000\s*\n\s*repeat:\s*true", g):
        e.append("EdgeReveal: no heartbeat re-check")
    if "reserveShortCount >= 2" not in g:
        e.append("EdgeReveal: acts on a single short observation (transitions must settle)")
    for stage in ('"nudge"', '"recreate"', '"backoff"'):
        if stage not in g:
            e.append(f"EdgeReveal: no {stage} stage")
    if "!snap.reserved" not in g or "!fullscreen && e.top > 0" not in g:
        e.append("EdgeReveal: the guard does not stand down under a fullscreen window / without data")
    if not re.search(r'target:\s*"reserve-"\s*\+', g) or "function state()" not in g or "function reassert()" not in g:
        e.append('EdgeReveal: no "reserve-<output>" IPC (state / reassert)')
    if "Math.pow(2, reserveFailures)" not in g:
        e.append("EdgeReveal: the back-off does not grow (a foreign zone would be retried every minute forever)")
    if "function probeQtBindings()" not in g or "reserveStage === 1 && !probeQtBindings()" not in g:
        e.append("EdgeReveal: no frozen-binding probe gating the surface rebuild (Quickshell update-group leak)")
    if "exec hyprctl notify" not in g or "reserveFrozenNotified" not in g or "-mmin -60" not in g:
        e.append("EdgeReveal: frozen bindings are not reported through Hyprland's own notification")
    return e


class Static(unittest.TestCase):
    def files(self):
        return read("PanelWindow.qml"), read("Sidebar.qml"), read("EdgeReveal.qml")

    def test_positive_source(self):
        self.assertEqual(static_errors(*self.files()), [])

    def test_negative_mutations(self):
        panel, side, edge = self.files()
        for bad in (
            panel.replace("exclusionMode: QS.ExclusionMode.Normal",
                          "exclusionMode: overFullscreen ? QS.ExclusionMode.Ignore : QS.ExclusionMode.Normal", 1),
            panel.replace("+ (overFullscreen ? 0 : reserveNudge)", "", 1),
            panel.replace("reveal.topSurface = pill", "reveal.topSurface = null"),
            panel.replace("&& !reserveRebuild", "", 1),
            panel.replace("function reserveExpectedNow(", "function reserveExpectedLater(", 1),
        ):
            self.assertTrue(static_errors(bad, side, edge), "a PanelWindow mutation passed")
        for bad in (
            side.replace("+ (reserveNudge > 0 && !(root.autohide || !visible || root.fullscreen) ? reserveNudge : 0)", "", 1),
            side.replace("root.reveal.bottomSurface = dock", "root.reveal.bottomSurface = null", 1),
            side.replace("visible: !reserveRebuild &&", "visible:", 1),
            side.replace("exclusiveZone: zoneFor(root.autohide || !visible || root.fullscreen,", "exclusiveZone: zoneFor(false,", 1),
        ):
            self.assertTrue(static_errors(panel, bad, edge), "a Sidebar mutation passed")
        for bad in (
            edge.replace('"closelayer"', '"closewindow"'),
            edge.replace("reserveShortCount >= 2", "reserveShortCount >= 1"),
            edge.replace('reserveLast = "recreate"', 'reserveLast = "again"'),
            edge.replace("Math.pow(2, reserveFailures)", "1"),
            edge.replace('target: "reserve-" +', 'target: "reserve" +'),
            edge.replace("o ? o.reserved", "o ? o.size"),
            edge.replace("reserveStage === 1 && !probeQtBindings()", "reserveStage === 1"),
            edge.replace("exec hyprctl notify", "exec true notify"),
            edge.replace("-mmin -60", "-mmin -0"),
        ):
            self.assertTrue(static_errors(panel, side, bad), "an EdgeReveal mutation passed")


# ---- bindings: Super+F11 fullscreen, Super+Shift+F11 maximized -----------------------------------------------------
SSOT = os.environ.get("MIOS_SSOT") or os.path.join(ROOT, "usr", "share", "mios", "mios.toml")
F11_BINDS = {
    "SUPER + F11": ({"mode": "fullscreen", "action": "toggle"}, "Toggle fullscreen"),
    "SUPER + SHIFT + F11": ({"mode": "maximized", "action": "toggle"}, "Toggle maximized"),
}
F11_LUA = ('hl.bind("SUPER + F11", hl.dsp.window.fullscreen({["mode"] = "fullscreen", ["action"] = "toggle"}))',
           'hl.bind("SUPER + SHIFT + F11", hl.dsp.window.fullscreen({["mode"] = "maximized", ["action"] = "toggle"}))')


def binding_errors(d: dict) -> list[str]:
    e = []
    binds = d.get("desktop", {}).get("hyprland", {}).get("bindings", [])
    for key, (args, label) in F11_BINDS.items():
        hits = [b for b in binds if b.get("key") == key]
        if len(hits) != 1:
            e.append(f"{key}: {len(hits)} bindings (want 1)")
            continue
        b = hits[0]
        if b.get("dispatcher") != "window.fullscreen" or b.get("args") != args:
            e.append(f"{key}: must be window.fullscreen {args}, is {b.get('dispatcher')} {b.get('args')}")
        if b.get("label") != label:
            e.append(f"{key}: label {b.get('label')!r} (keymap style: {label!r})")
    labels = [b.get("label") for b in binds if b.get("label")]
    if any(not re.match(r"^[A-Z][a-z]", l) for l in labels):
        e.append("labels must read like the keymap ('Toggle fullscreen')")
    return e


def render_static(root: str) -> subprocess.CompletedProcess:
    """`mios-gen render-hyprland --root ROOT` writes the projection under ROOT only (never the live tree)."""
    os.makedirs(os.path.join(root, "usr", "share", "mios", "hyprland"), exist_ok=True)
    os.makedirs(os.path.join(root, "etc", "skel", ".config", "hypr"), exist_ok=True)
    return subprocess.run(["mios-gen", "render-hyprland", "--root", root], capture_output=True, text=True, timeout=120, cwd=root)


class Bindings(unittest.TestCase):
    def ssot(self):
        import tomllib
        with open(SSOT, "rb") as f:
            return tomllib.load(f)

    def test_positive_ssot(self):
        self.assertEqual(binding_errors(self.ssot()), [])

    def test_negative_ssot(self):
        import copy
        for key, field, val in (("SUPER + F11", "dispatcher", "window.float"), ("SUPER + F11", "args", {"mode": "maximized", "action": "toggle"}),
                                ("SUPER + SHIFT + F11", "label", "toggle max"), ("SUPER + F11", "key", "SUPER + F12")):
            d = copy.deepcopy(self.ssot())
            next(b for b in d["desktop"]["hyprland"]["bindings"] if b["key"] == key)[field] = val
            self.assertTrue(binding_errors(d), (key, field, val))

    @unittest.skipUnless(shutil.which("mios-gen"), "mios-gen not installed")
    def test_render_projects_the_dispatcher(self):
        with tempfile.TemporaryDirectory(prefix="mios-reserve-render-") as root:
            os.makedirs(os.path.join(root, "usr", "share", "mios"))
            shutil.copy2(SSOT, os.path.join(root, "usr", "share", "mios", "mios.toml"))
            r = render_static(root)
            self.assertEqual(r.returncode, 0, r.stderr)
            lua = read("hyprland.lua", os.path.join(root, "usr", "share", "mios", "hyprland"))
            for line in F11_LUA:
                self.assertEqual(lua.count(line), 1, line)
            if shutil.which("luajit"):
                c = subprocess.run(["luajit", "-bl", os.path.join(root, "usr", "share", "mios", "hyprland", "hyprland.lua")],
                                   capture_output=True, text=True, timeout=60)
                self.assertEqual(c.returncode, 0, c.stderr)
            # negative: an unsafe dispatcher identifier is refused
            p = os.path.join(root, "usr", "share", "mios", "mios.toml")
            write(p, read("mios.toml", os.path.dirname(p)).replace('dispatcher = "window.fullscreen", args = { mode = "fullscreen"',
                                                                  'dispatcher = "window.fullscreen);bad(", args = { mode = "fullscreen"', 1))
            r = render_static(root)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("dispatcher", r.stderr)


# ---- nested ------------------------------------------------------------------------------------------------------
DBUS_CONF = """<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <type>session</type>
  <listen>unix:path={sock}</listen>
  <auth>EXTERNAL</auth>
  <policy context="default"><allow send_destination="*" eavesdrop="true"/><allow eavesdrop="true"/><allow own="*"/></policy>
</busconfig>
"""
# Test-only injections (the staged fault copy, never the shipped QML): force lost zones, and provoke the Quickshell
# update-group leak -- hammer Hyprland.refreshToplevels() on a busy GUI thread so a j/clients response can name a
# workspace whose create / destroy event the shell has not read yet.
FAULT_IPC = """    Timer { id: rsvHammer; interval: 4; repeat: true; running: false; onTriggered: Hyprland.refreshToplevels() }
    Timer { interval: 25; repeat: true; running: rsvHammer.running; onTriggered: { const t = Date.now(); while (Date.now() - t < 18) {} } }
    IpcHandler {
        target: "reserve-fault-" + (edge.screen ? edge.screen.name : "")
        function ignoreTop(): void { if (edge.topSurface) edge.topSurface.exclusionMode = QS.ExclusionMode.Ignore }
        function ignoreBottom(): void { if (edge.bottomSurface) edge.bottomSurface.exclusionMode = QS.ExclusionMode.Ignore }
        function hammer(on: bool): void { rsvHammer.running = on }
    }
"""
# A second client with an oversized bottom zone: Hyprland then drops the output's whole reserved area.
FOREIGN_QML = """import QtQuick
import Quickshell as QS
import Quickshell.Wayland
QS.ShellRoot {
    QS.PanelWindow {
        screen: QS.Quickshell.screens.find(s => s.name === "%s") || null
        WlrLayershell.namespace: "rsvtest-foreign"
        anchors.bottom: true
        implicitWidth: 40
        implicitHeight: 4
        exclusiveZone: 4000
        color: "transparent"
    }
}
"""
LOG_ERROR = re.compile(r"TypeError|ReferenceError|SyntaxError|Cannot read|is not a function|is not defined|Unable to assign|"
                       r"Cannot assign|failed to load|is not a type|Property value set multiple times")


def portal_mounts() -> int:
    with open("/proc/self/mountinfo") as f:
        return sum(1 for line in f if "fuse.portal" in line)


def nest_env(**extra) -> dict:
    e = {k: v for k, v in os.environ.items() if k not in ("QT_QPA_PLATFORMTHEME", "DBUS_SESSION_BUS_ADDRESS")}
    e.update(XDG_RUNTIME_DIR=NEST_XDG, HYPRLAND_INSTANCE_SIGNATURE=NEST_SIG)
    e.update(extra)
    return e


def hypr(*args: str) -> str:
    return subprocess.run(["hyprctl", "-i", NEST_SIG, *args], capture_output=True, text=True, timeout=15, env=nest_env()).stdout


def hypr_json(*args: str):
    return json.loads(hypr("-j", *args) or "null")


def expected_rows() -> tuple[int, int]:
    with open(THEME) as f:
        sh = json.load(f)["shell"]
    m = int(sh["edge_margin_px"])
    return int(sh["top_height_px"]) + 2 * m, int(sh["dock_height_px"]) + 2 * m


@unittest.skipUnless(NEST_SIG and NEST_XDG and NEST_WL,
                     "nested compositor (MIOS_RESERVE_NEST_SIG + MIOS_RESERVE_NEST_XDG + MIOS_RESERVE_NEST_WL; never the live desktop)")
class Nested(unittest.TestCase):
    tmp = None
    shell = None
    bus_proc = None
    windows: list = []

    @classmethod
    def setUpClass(cls):
        if os.path.realpath(NEST_XDG) == os.path.realpath(f"/run/user/{os.getuid()}"):
            raise unittest.SkipTest("refusing: MIOS_RESERVE_NEST_XDG is the live session's runtime dir")
        mons = hypr_json("monitors") or []
        names = {m["name"]: m for m in mons}
        bad = [n for n in names if not re.match(r"^(HEADLESS-|WAYLAND-)", n)]
        if bad:
            raise unittest.SkipTest(f"refusing: {NEST_SIG} drives real outputs {bad} (the live desktop?)")
        if DESK not in names:
            raise unittest.SkipTest(f"nested output {DESK} missing")
        if any(l for m in (hypr_json("layers") or {}).values() for lv in m["levels"].values() for l in lv
               if l["namespace"].startswith("mios-")):
            raise unittest.SkipTest("another MiOS shell runs on the nested compositor: stop it first")
        cls.top, cls.bottom = expected_rows()
        cls.portals_before = portal_mounts()
        cls.tmp = tempfile.mkdtemp(prefix="mios-reserve-")
        sock = os.path.join(cls.tmp, "bus")
        with open(os.path.join(cls.tmp, "bus.conf"), "w") as f:
            f.write(DBUS_CONF.format(sock=sock))
        cls.bus_proc = subprocess.Popen(["dbus-daemon", "--nofork", "--nopidfile", "--config-file=" + os.path.join(cls.tmp, "bus.conf")],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            if os.path.exists(sock):
                break
            time.sleep(0.1)
        cls.bus = "unix:path=" + sock
        act = subprocess.run(["gdbus", "call", "--session", "-d", "org.freedesktop.DBus", "-o", "/org/freedesktop/DBus",
                              "-m", "org.freedesktop.DBus.ListActivatableNames"], env=cls.env(), capture_output=True, text=True)
        assert act.stdout.strip() == "(['org.freedesktop.DBus'],)", act.stdout
        # staged QML, a fault-injection copy, a foreign oversized-zone shell
        cls.qs = os.path.join(cls.tmp, "qs")
        shutil.copytree(QS_DIR, cls.qs, ignore=shutil.ignore_patterns("*.bak*", "*.before*", ".*"))
        cls.qs_fault = os.path.join(cls.tmp, "qs-fault")
        shutil.copytree(cls.qs, cls.qs_fault)
        p = os.path.join(cls.qs_fault, "EdgeReveal.qml")
        src = read("EdgeReveal.qml", cls.qs_fault)
        i = src.rindex("}")
        write(p, src[:i] + FAULT_IPC + src[i:])
        cls.qs_foreign = os.path.join(cls.tmp, "qs-foreign")
        os.mkdir(cls.qs_foreign)
        write(os.path.join(cls.qs_foreign, "shell.qml"), FOREIGN_QML % DESK)
        write(os.path.join(cls.tmp, "appmenu.json"), "{}")
        cls.focus(DESK)
        cls.start_shell(cls.qs)
        cls.open_window("rsvtest.tile1")

    @classmethod
    def tearDownClass(cls):
        cls.stop_shell()
        for p in cls.windows:
            try:
                os.killpg(p.pid, signal.SIGTERM)
                p.wait(5)
            except Exception:
                pass
        hypr("eval", "for _, r in ipairs(rawget(_G, 'rsvtest_rules') or {}) do r:set_enabled(false) end")
        for m in hypr_json("monitors") or []:
            if m["name"] == "HEADLESS-RSV-RDP":
                hypr("output", "remove", "HEADLESS-RSV-RDP")
        if cls.bus_proc:
            cls.bus_proc.terminate()
            try:
                cls.bus_proc.wait(5)
            except subprocess.TimeoutExpired:
                cls.bus_proc.kill()
        if cls.tmp:
            shutil.rmtree(cls.tmp, ignore_errors=True)
        after = portal_mounts()
        assert after == cls.portals_before, f"portal mounts changed: {cls.portals_before} -> {after}"

    # ---- harness -------------------------------------------------------------------------------------------------
    @classmethod
    def env(cls, **extra) -> dict:
        return nest_env(DBUS_SESSION_BUS_ADDRESS=cls.bus, WAYLAND_DISPLAY=NEST_WL, NO_AT_BRIDGE="1", GTK_USE_PORTAL="0",
                        QT_NO_XDG_DESKTOP_PORTAL="1", **extra)

    @classmethod
    def start_shell(cls, qs_dir, attr="shell", config="Config.qml"):
        if attr == "shell":
            cls.stop_shell()
            cls.qs_path = qs_dir
        log = os.path.join(cls.tmp, "%s-%d.log" % (attr, int(time.time() * 1000)))
        logf = open(log, "w")  # handed to the child; closed below
        p = subprocess.Popen(
            ["systemd-run", "--user", "--scope", "-q", "-p", "MemoryMax=1G", "-p", "MemorySwapMax=0",
             "--unit=mios-reserve-test-%s-%d-%d" % (attr, os.getpid(), int(time.time() * 1000)),
             "env", *["%s=%s" % kv for kv in cls.env(MIOS_APPMENU_JSON=os.path.join(cls.tmp, "appmenu.json")).items()
                      if kv[0] in ("XDG_RUNTIME_DIR", "HYPRLAND_INSTANCE_SIGNATURE", "DBUS_SESSION_BUS_ADDRESS", "WAYLAND_DISPLAY",
                                   "NO_AT_BRIDGE", "GTK_USE_PORTAL", "QT_NO_XDG_DESKTOP_PORTAL", "MIOS_APPMENU_JSON")],
             "quickshell", "--path", os.path.join(qs_dir, config)],
            stdout=logf, stderr=subprocess.STDOUT, start_new_session=True)
        logf.close()
        setattr(cls, attr, p)
        setattr(cls, attr + "_log", log)
        if attr != "shell":
            return p
        for _ in range(150):
            st = cls.state()
            if st and st.get("reserved") and st.get("top") and st.get("bottom"):
                break
            time.sleep(0.2)
        else:
            with open(log, errors="replace") as f:
                raise AssertionError("test shell did not come up: " + f.read()[-3000:])
        time.sleep(1.5)
        return p

    @classmethod
    def kill(cls, p):
        if p and p.poll() is None:
            try:
                os.killpg(p.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, signal.SIGKILL)

    @classmethod
    def stop_shell(cls):
        cls.kill(cls.shell)
        cls.shell = None
        time.sleep(0.5)

    @classmethod
    def ipc(cls, target, fn, *args) -> str:
        r = subprocess.run(["quickshell", "ipc", "--path", os.path.join(cls.qs_path, "Config.qml"), "call", target, fn, *args],
                           capture_output=True, text=True, timeout=15, env=cls.env())
        return r.stdout.strip()

    @classmethod
    def state(cls):
        try:
            return json.loads(cls.ipc("reserve-" + DESK, "state") or "null")
        except (json.JSONDecodeError, subprocess.TimeoutExpired):
            return None

    @classmethod
    def focus(cls, output):
        hypr("dispatch", 'hl.dsp.focus({monitor = "%s"})' % output)
        time.sleep(0.3)

    @classmethod
    def open_window(cls, cls_name):
        hypr("eval", "_G.rsvtest_rules = rawget(_G, 'rsvtest_rules') or {}; table.insert(_G.rsvtest_rules, "
                     "hl.window_rule({name = 'rsvtest-tile', match = {class = '^(rsvtest\\\\.tile.*)$'}, tile = true}))")
        p = subprocess.Popen(["alacritty", "--class", cls_name, "-T", cls_name, "-e", "sh", "-c", "exec sleep 3600"],
                             env=cls.env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        cls.windows.append(p)
        for _ in range(60):
            if any(c["class"] == cls_name for c in hypr_json("clients") or []):
                break
            time.sleep(0.1)
        time.sleep(1.0)
        return p

    def client(self, cls_name="rsvtest.tile1"):
        return next(c for c in hypr_json("clients") if c["class"] == cls_name)

    def mon(self):
        return next(m for m in hypr_json("monitors") if m["name"] == DESK)

    def reserved(self):
        return self.mon()["reserved"]

    def wait_reserved(self, want, timeout=8.0):
        end = time.time() + timeout
        r = None
        while time.time() < end:
            r = self.reserved()
            if r == want:
                return r
            time.sleep(0.2)
        return r

    def assert_rows(self, what, timeout=8.0):
        want = [0, self.top, 0, self.bottom]
        r = self.wait_reserved(want, timeout)
        self.assertEqual(r, want, f"{what}: reserved {r}, state {self.state()}")
        # the tile ends below the island and above the dock
        end = time.time() + 3
        while time.time() < end:
            m, c = self.mon(), self.client()
            if c["workspace"]["name"] == m["activeWorkspace"]["name"] and c["at"][1] == m["y"] + self.top \
                    and c["at"][1] + c["size"][1] == m["y"] + m["height"] - self.bottom:
                break
            time.sleep(0.2)
        m, c = self.mon(), self.client()
        self.assertEqual(c["fullscreen"], 0, what)
        self.assertEqual(c["at"][1], m["y"] + self.top, f"{what}: tile top {c}")
        self.assertEqual(c["at"][1] + c["size"][1], m["y"] + m["height"] - self.bottom, f"{what}: tile bottom {c}")

    def fullscreen(self, mode="fullscreen"):
        hypr("dispatch", 'hl.dsp.window.fullscreen({mode = "%s", action = "toggle", window = "address:%s"})'
             % (mode, self.client()["address"]))

    @staticmethod
    def read_text(path):
        with open(path, errors="replace") as f:
            return f.read()

    def log_errors(self, log=None):
        return [l for l in self.read_text(log or self.shell_log).splitlines() if LOG_ERROR.search(l)]

    # ---- tests (run in name order: each leaves the desk as it found it) ------------------------------------------------
    def test_01_baseline(self):
        self.assert_rows("baseline")
        st = self.state()
        self.assertEqual(st["expected"], {"top": self.top, "bottom": self.bottom})
        self.assertEqual(st["stage"], 0)

    def test_02_hot_reload_keeps_rows(self):
        for name in ("PanelWindow.qml", "Sidebar.qml", "EdgeReveal.qml"):
            p = os.path.join(self.qs, name)
            src = self.read_text(p)
            write(p, src + "\n")   # same tree: the windows are reused
            time.sleep(2.5)
            self.assert_rows(f"after a hot reload of {name}")

    def test_03_rebuilding_reload_keeps_rows(self):
        p = os.path.join(self.qs, "Config.qml")
        src = self.read_text(p)
        write(p, src.replace("    MiOS.Keymap {}\n", "    MiOS.Keymap {}\n    QtObject { objectName: \"rsvtest\" }\n", 1))
        time.sleep(3)
        self.assert_rows("after a reload with a changed object tree")
        write(p, src)
        time.sleep(3)
        self.assert_rows("after reverting it")

    def test_04_headless_output_like_rdp(self):
        desk = self.mon()
        x = desk["x"] + (desk["width"] - 440) // 2
        y = desk["y"] + desk["height"]
        hypr("eval", 'hl.workspace_rule({workspace = "name:rsvremote", monitor = "HEADLESS-RSV-RDP", default = true})')
        hypr("eval", 'hl.monitor({output = "HEADLESS-RSV-RDP", mode = "1280x720@60", position = "%dx%d", scale = 1, transform = 0})'
             % (desk["x"] + 320, y))
        hypr("output", "create", "headless", "HEADLESS-RSV-RDP")
        time.sleep(1.5)
        hypr("eval", 'hl.monitor({output = "HEADLESS-RSV-RDP", mode = "1100x2080@60", position = "%dx%d", scale = 2.5, transform = 0})' % (x, y))
        time.sleep(2)
        self.focus("HEADLESS-RSV-RDP")
        self.focus(DESK)
        self.assert_rows("with the headless output attached")
        hypr("output", "remove", "HEADLESS-RSV-RDP")
        time.sleep(2)
        self.assert_rows("after the headless output went away")

    def test_05_lamp_minimize_restore(self):
        addr = self.client()["address"]
        self.ipc("docklamp", "minimize", addr)
        time.sleep(1.5)
        self.assertTrue(self.client()["workspace"]["name"].startswith("special:"), self.client())
        self.assertEqual(self.wait_reserved([0, self.top, 0, self.bottom]), [0, self.top, 0, self.bottom], "while minimized")
        self.ipc("docklamp", "restore", addr)
        time.sleep(2)
        self.assert_rows("after the lamp restore")

    def test_06_config_reload(self):
        hypr("reload")
        time.sleep(3)
        self.assert_rows("after a Hyprland config reload")

    def test_07_maximize_keeps_rows(self):
        self.fullscreen("maximized")
        time.sleep(1.5)
        self.assertEqual(self.wait_reserved([0, self.top, 0, self.bottom]), [0, self.top, 0, self.bottom], "maximized")
        self.fullscreen("maximized")
        time.sleep(1.5)
        self.assert_rows("after maximize off")

    def test_08_fullscreen_reserves_nothing_then_back(self):
        self.fullscreen()
        time.sleep(1.5)
        r = self.wait_reserved([0, 0, 0, 0])
        self.assertEqual(r, [0, 0, 0, 0], "under a fullscreen window nothing is reserved")
        st = self.state()
        self.assertTrue(st["fullscreen"])
        self.assertEqual(st["expected"], {"top": 0, "bottom": 0})
        time.sleep(6)   # a heartbeat later the guard still stands down
        self.assertEqual(self.state()["stage"], 0)
        self.assertEqual(self.reserved(), [0, 0, 0, 0])
        self.fullscreen()
        time.sleep(1.5)
        self.assert_rows("after leaving fullscreen")

    def test_09_fullscreen_exit_with_the_island_revealed(self):
        self.fullscreen()
        time.sleep(1.5)
        self.ipc("notifications", "post", "Reserve test", "the island stays mapped through the fullscreen exit", "info")
        time.sleep(0.8)
        self.fullscreen()
        time.sleep(2)
        self.assert_rows("after a fullscreen exit while the island was revealed")

    def test_10_lost_zones_restored_by_the_guard(self):
        self.start_shell(self.qs_fault)
        self.assert_rows("fault shell baseline")
        self.ipc("reserve-fault-" + DESK, "ignoreTop")
        self.ipc("reserve-fault-" + DESK, "ignoreBottom")
        r = self.wait_reserved([0, 0, 0, 0], 3)
        self.assertEqual(r, [0, 0, 0, 0], "the fault empties the reserved area (the operator's symptom)")
        self.assert_rows("the guard re-asserted the lost zones", timeout=15)
        st = self.state()
        self.assertGreaterEqual(st["reasserts"], 1, st)
        log, end = "", time.time() + 10
        while time.time() < end and "restored after nudge" not in log:
            time.sleep(0.5)
            log = self.read_text(self.shell_log)
        self.assertIn("[reserve] %s: compositor reserved [0,0,0,0]" % DESK, log)
        self.assertIn("restored after nudge", log)
        self.start_shell(self.qs)
        self.assert_rows("back on the staged shell")

    def test_11_foreign_oversized_zone_backs_off(self):
        foreign = self.start_shell(self.qs_foreign, attr="foreign", config="shell.qml")
        try:
            r = self.wait_reserved([0, 0, 0, 0], 6)
            self.assertEqual(r, [0, 0, 0, 0], "another client's oversized zone empties the output's reserved area")
            end = time.time() + 25
            while time.time() < end and self.state()["stage"] < 3:
                time.sleep(0.5)
            st = self.state()
            self.assertEqual(st["stage"], 3, f"the guard did not back off: {st}")
            n = st["reasserts"]
            time.sleep(8)
            self.assertEqual(self.state()["reasserts"], n, "the guard keeps re-asserting while backed off (a loop)")
        finally:
            self.kill(foreign)
        self.assert_rows("after the foreign client left", timeout=10)
        end = time.time() + 8
        while time.time() < end and self.state()["stage"] != 0:
            time.sleep(0.5)
        self.assertEqual(self.state()["stage"], 0, "the guard stays backed off although the rows are back")

    def test_12_no_qml_errors(self):
        self.assertEqual(self.log_errors(), [])

    def test_13_frozen_bindings_detected_not_looped(self):
        """The operator's root cause, reproduced: Quickshell < 1bd5b083 leaks a Qt property update group; afterwards the
        surfaces of a reload send a zone of 0 forever. The guard must detect it (probe), skip the useless surface rebuild,
        back off as "frozen" and tell the operator; a new shell process recovers. Runs last: it poisons the shell process."""
        self.start_shell(self.qs_fault)
        self.assert_rows("fault shell baseline")
        self.assertFalse(json.loads(self.ipc("reserve-" + DESK, "probe"))["qt_bindings_frozen"])
        self.open_window("rsvtest.tile9")
        addr = self.client("rsvtest.tile9")["address"]
        frozen = False
        for attempt in range(3):
            self.ipc("reserve-fault-" + DESK, "hammer", "true")
            for i in range(200):
                hypr("dispatch", 'hl.dsp.window.move({workspace = "name:rsvleak%d_%d", window = "address:%s", follow = false})'
                     % (attempt, i, addr))
            hypr("dispatch", 'hl.dsp.window.move({workspace = "%s", window = "address:%s", follow = false})'
                 % (self.mon()["activeWorkspace"]["name"], addr))
            self.ipc("reserve-fault-" + DESK, "hammer", "false")
            time.sleep(0.5)
            if json.loads(self.ipc("reserve-" + DESK, "probe"))["qt_bindings_frozen"]:
                frozen = True
                break
        if not frozen:
            self.skipTest("the Quickshell update-group leak did not trigger (Quickshell >= 1bd5b083, or the race missed)")
        # a reload after the leak: the new surfaces send 0 -> the operator's [0, 0, 0, 0]
        p = os.path.join(self.qs_fault, "PanelWindow.qml")
        write(p, self.read_text(p) + "\n")
        r = self.wait_reserved([0, 0, 0, 0], 8)
        self.assertEqual(r, [0, 0, 0, 0], "after the leak a reload leaves the output without reserved rows")
        end = time.time() + 25
        st = self.state()
        while time.time() < end and not (st and st["stage"] == 3):
            time.sleep(0.5)
            st = self.state()
        self.assertEqual(st["stage"], 3, st)
        self.assertTrue(st["qt_bindings_frozen"], st)
        self.assertEqual(st["last"], "frozen", st)
        log = self.read_text(self.shell_log)
        self.assertIn("Qt property bindings are frozen", log)
        tail = log[log.rindex("Qt property bindings are frozen"):]
        self.assertNotIn("; recreate;", tail, "the guard rebuilt surfaces although bindings are frozen")
        # the remedy: a new shell process
        self.start_shell(self.qs)
        self.assert_rows("a new shell process reserves the rows again")
        self.assertFalse(json.loads(self.ipc("reserve-" + DESK, "probe"))["qt_bindings_frozen"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
