#!/usr/bin/env python3
# AI-hint: Island notifications (operator 2026-10-17: "notifications come in from the top center under the island/topbar/pill
# -- it's a glass blob that grows to the notifications"). Notifications.qml draws every notification -- apps over
# org.freedesktop.Notifications (Config.qml NotificationServer) and the shell's own toasts -- as ONE glass blob per output
# that starts as the status island (ShellGeometry.drawnIslands, published by PanelWindow.qml) and grows on the island menus'
# morph (Emerge.qml) into the card / stack below it, melting back on dismiss / expiry.
#   1. SSOT (always): [desktop.notifications] is well-formed (position "island", timeout, max_visible, width, morph ...);
#      mutated copies fail (negative controls).
#   2. static (always, MIOS_QS_DIR or the repo QML): the surface spans the output's top row (never anchored top-right), its
#      layer namespace is glass-covered (not in [desktop.hyprland.hyprglass.config.layers] exclude_namespaces), it grows
#      out of the drawn island with Emerge on the island's Glass.fill, its input mask is the blob, the island publishes its
#      drawn rectangle and a notification-only reveal hook; no literal colour or radius. Mutated copies fail.
#   3. nested (MIOS_NOTIFY_NEST_SIG + MIOS_NOTIFY_NEST_WL: a NESTED test compositor -- only HEADLESS-*/WAYLAND-* outputs, the
#      lane harness notify-island/nest/start.sh -- with hyprglass loaded and outputs HEADLESS-DESK 1920x1080@1 and
#      HEADLESS-PHONE 1100x2080@2.5; never the live desktop): the test starts its OWN private dbus-daemon (a config with no
#      service directories: nothing is ever activated, no portals) and its own Quickshell from a staged copy of the QML,
#      sends notifications over that bus (gdbus / notify-send), drives a virtual pointer (zwlr_virtual_pointer_v1, built
#      from the embedded source into a temp dir) and grabs frames of the nested outputs with grim. Positive: the morph's
#      first frame is the island's rectangle and radius (+-2 px), the card ends centred under the island one MeltPopup gap
#      below it, a second notification grows the same blob (one surface, a stack), dismiss / expiry melts it back into the
#      island, the blob is glass (the backdrop is frosted, not flat; edge rim), on a mobile output (1100x2080 @ 2.5, island
#      off-centre between window tabs) it grows from the island's actual x with 44 px touch targets, over a fullscreen
#      window the island reveals for it and hides afterwards, critical stays, actions and the default action fire
#      ActionInvoked. Negative: nothing at the top right, nothing on other outputs, no input outside the blob, no island on
#      the output -> it still emerges from the top centre, an excluded (glass-less) namespace measures flat, malformed
#      notifications (no body, huge image, bogus image-data, 0 timeout, markup, 20k body) leave the shell alive without QML
#      errors. The portal FUSE mount count is checked before and after (a private bus never starts portals).
# AI-related: usr/share/mios/quickshell/Notifications.qml, usr/share/mios/quickshell/NotifModel.qml,
# usr/share/mios/quickshell/Config.qml, usr/share/mios/quickshell/PanelWindow.qml, usr/share/mios/quickshell/EdgeReveal.qml,
# usr/share/mios/quickshell/ShellGeometry.qml, usr/share/mios/quickshell/Emerge.qml, usr/share/mios/quickshell/Glass.qml,
# usr/share/mios/mios.toml [desktop.notifications] [desktop.hyprland.hyprglass], tests/test-island-sysmon.py
"""Island notification blob tests."""

from __future__ import annotations

import copy
import io
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import tomllib
import unittest

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SSOT = os.environ.get("MIOS_SSOT") or os.path.join(ROOT, "usr", "share", "mios", "mios.toml")
QS_DIR = os.environ.get("MIOS_QS_DIR") or os.path.join(ROOT, "usr", "share", "mios", "quickshell")
NEST_SIG = os.environ.get("MIOS_NOTIFY_NEST_SIG", "")
NEST_WL = os.environ.get("MIOS_NOTIFY_NEST_WL", "")
DESK = os.environ.get("MIOS_NOTIFY_NEST_DESK", "HEADLESS-DESK")
PHONE = os.environ.get("MIOS_NOTIFY_NEST_PHONE", "HEADLESS-PHONE")
SHOTS = os.environ.get("MIOS_NOTIFY_SHOTS", "")   # optional: keep evidence frames here


def ssot() -> dict:
    with open(SSOT, "rb") as f:
        return tomllib.load(f)


def read(name: str, qs_dir: str = QS_DIR) -> str:
    with open(os.path.join(qs_dir, name), encoding="utf-8") as f:
        return f.read()


# ---- SSOT --------------------------------------------------------------------------------------------------------
def notifications_errors(d: dict) -> list[str]:
    t = d.get("desktop", {}).get("notifications")
    if not isinstance(t, dict):
        return ["[desktop.notifications] missing"]
    e = []
    if t.get("position") != "island":
        e.append("position must be \"island\" (the only placement: out of the status island, top centre)")
    for k, lo in (("timeout_ms", 1000), ("max_visible", 1)):
        if not isinstance(t.get(k), int) or isinstance(t.get(k), bool) or t[k] < lo:
            e.append(f"{k} must be an integer >= {lo}")
    for k in ("width_px", "morph_ms", "image_max_height_px", "actions_max"):
        if not isinstance(t.get(k), int) or isinstance(t.get(k), bool) or t[k] < 0:
            e.append(f"{k} must be a non-negative integer")
    if not isinstance(t.get("body_max_lines"), int) or t["body_max_lines"] < 1:
        e.append("body_max_lines must be an integer >= 1")
    if not isinstance(t.get("do_not_disturb"), bool):
        e.append("do_not_disturb must be a boolean")
    if t.get("no_anim") is not True:
        e.append("no_anim must be true (the blob's layer box moves; an animated box stretches the blob)")
    extra = set(t) - {"position", "timeout_ms", "max_visible", "width_px", "morph_ms", "image_max_height_px",
                      "body_max_lines", "actions_max", "do_not_disturb", "no_anim"}
    if extra:
        e.append(f"unknown keys {sorted(extra)}")
    return e


class Ssot(unittest.TestCase):
    def test_positive_table(self):
        self.assertEqual(notifications_errors(ssot()), [])

    def test_negative_bad_values(self):
        for key, val, frag in (("position", "top-right", "position"), ("max_visible", 0, "max_visible"),
                               ("timeout_ms", -1, "timeout_ms"), ("width_px", -5, "width_px"), ("morph_ms", "fast", "morph_ms"),
                               ("do_not_disturb", "no", "do_not_disturb"), ("body_max_lines", 0, "body_max_lines"),
                               ("no_anim", False, "no_anim"),
                               ("anchor", "right", "unknown")):
            d = copy.deepcopy(ssot())
            d["desktop"]["notifications"][key] = val
            self.assertTrue(any(frag in x for x in notifications_errors(d)), (key, notifications_errors(d)))
        d = copy.deepcopy(ssot())
        del d["desktop"]["notifications"]
        self.assertTrue(notifications_errors(d))


# ---- static ------------------------------------------------------------------------------------------------------
LITERAL = re.compile(r"#[0-9a-fA-F]{6,8}\b|\b(?:color|fill|fromFill|outline|[a-zA-Z]*[rR]adius)\s*:\s*(?:-?\d|\"(?!transparent\"))")


def glass_namespace_ok(namespace: str, d: dict) -> list[str]:
    layers = d["desktop"]["hyprland"]["hyprglass"]
    cfg = layers.get("config", {}).get("layers", {})
    e = []
    if cfg.get("enabled") is not True:
        e.append("hyprglass layer glass is off")
    if namespace in [x.strip() for x in str(cfg.get("exclude_namespaces", "")).split(",") if x.strip()]:
        e.append(f"{namespace} is in the hyprglass exclude list")
    white = layers.get("layers", [])
    if white and namespace not in white:
        e.append(f"{namespace} is not in the hyprglass layer whitelist")
    return e


def static_errors(notif: str, panel: str, edge: str, config: str, geometry: str, model: str = "") -> list[str]:
    e = []
    if "WlrLayershell.namespace: NotifModel.layerNamespace" not in notif:
        e.append("the surfaces do not take NotifModel's layer namespace (the one the no_anim rule names)")
    anchors = re.search(r"anchors\s*\{([^}]*)\}", notif)
    a = anchors.group(1) if anchors else ""
    if not (re.search(r"\btop:\s*true", a) and re.search(r"\bleft:\s*true", a)) or re.search(r"\bright:\s*true", a):
        e.append("the surface must be placed from the output's top-left corner (anchors top + left), never anchored right")
    for box in ("morphBox", "restBox"):
        if f"margins.left: root.{box}.x" not in notif or f"implicitWidth: root.{box}.width" not in notif:
            e.append(f"a surface box ({box}) is not placed / sized from its box")
    if "morphBox = boxOf([islandRect, finalRect])" not in notif:
        e.append("the morph surface's box does not hold the island while the blob grows out of it")
    if "restBox = boxOf([finalRect])" not in notif:
        e.append("the rest surface's box is not exactly the stack (edge light on the card)")
    if "ShellGeometry.drawnIslands" not in notif:
        e.append("the blob does not start from the drawn island (ShellGeometry.drawnIslands)")
    if not re.search(r"Emerge\s*\{", notif) or "fromRect: root.islandRect" not in notif:
        e.append("the blob is not the Emerge morph out of the island rectangle")
    if not re.search(r"fill:\s*Glass\.fill", notif) or not re.search(r"fromFill:\s*Glass\.fill", notif) \
            or not re.search(r"color:\s*Glass\.fill", notif):
        e.append("the blob is not the island's material (Glass.fill)")
    if not re.search(r"mask: RoundedRegion \{\s*rx: blob\.glass\.x", notif) or "rw: blob.shown ? blob.glass.width : 0" not in notif \
            or "mask: RoundedRegion { rx: restGlass.x" not in notif or "shape: QS.RegionShape.Ellipse" not in notif:
        e.append("the input mask is not the blob alone (its rounded shape)")
    if "exclusionMode: QS.ExclusionMode.Ignore" not in notif:
        e.append("the surface must ignore exclusive zones (the island's coordinates are output-local)")
    for line in notif.splitlines():
        code = line.split("//", 1)[0]
        if LITERAL.search(code):
            e.append(f"literal colour/radius: {line.strip()}")
    if "ShellGeometry.setDrawnIsland" not in panel or "reveal.islandShown" not in panel:
        e.append("PanelWindow does not publish the drawn island / honour the notification reveal")
    if not re.search(r"property bool notifyPopup", edge) or "islandShown" not in edge:
        e.append("EdgeReveal has no notification reveal hook")
    if "drawnIslands" not in geometry:
        e.append("ShellGeometry has no drawn-island map")
    if "NotificationServer" not in config or not re.search(r"MiOS\.Notifications \{[^}]*reveal: edgeReveal", config):
        e.append("Config.qml does not route notifications to the per-output blob with its reveal")
    if model and not ("hl.layer_rule(" in model and "no_anim = true" in model and 'layerNamespace: "mios-notifications"' in model
                      and "configreloaded" in model):
        e.append("NotifModel does not ask Hyprland for the no_anim layer rule (at start and after a config reload)")
    return e


class Static(unittest.TestCase):
    def files(self):
        return (read("Notifications.qml"), read("PanelWindow.qml"), read("EdgeReveal.qml"), read("Config.qml"),
                read("ShellGeometry.qml"), read("NotifModel.qml"))

    def test_positive_source(self):
        self.assertEqual(static_errors(*self.files()), [])
        ns = re.search(r'layerNamespace:\s*"([^"]+)"', self.files()[5]).group(1)
        self.assertEqual(ns, "mios-notifications")
        self.assertEqual(glass_namespace_ok(ns, ssot()), [])

    def test_negative_mutations(self):
        notif, panel, edge, config, geo, model = self.files()
        for bad in (notif.replace("anchors { top: true; left: true }", "anchors { top: true; right: true }"),
                    notif.replace("margins.left: root.restBox.x", "margins.left: 0"),
                    notif.replace("restBox = boxOf([finalRect])", "restBox = boxOf([islandRect, finalRect])"),
                    notif.replace("fill: Glass.fill", 'fill: "#282262"', 1),
                    notif.replace("rw: blob.shown ? blob.glass.width : 0", "rw: root.outputWidth"),
                    notif.replace("toRadius: root.cardRadius", "toRadius: 12"),
                    notif.replace("ShellGeometry.drawnIslands", "ShellGeometry.islands")):
            self.assertTrue(static_errors(bad, panel, edge, config, geo, model), "a mutation passed")
        self.assertTrue(static_errors(notif, panel.replace("reveal.islandShown", "reveal.topShown"), edge, config, geo, model))
        self.assertTrue(static_errors(notif, panel, edge, config, geo, model.replace("no_anim = true", "no_anim = false")))
        d = copy.deepcopy(ssot())
        cfg = d["desktop"]["hyprland"]["hyprglass"]["config"]["layers"]
        cfg["exclude_namespaces"] = cfg["exclude_namespaces"] + ",mios-notifications"
        self.assertTrue(glass_namespace_ok("mios-notifications", d))


# ---- nested ------------------------------------------------------------------------------------------------------
VPTR_XML = """<?xml version="1.0" encoding="UTF-8"?>
<protocol name="wlr_virtual_pointer_unstable_v1">
  <interface name="zwlr_virtual_pointer_v1" version="2">
    <request name="motion"><arg name="time" type="uint"/><arg name="dx" type="fixed"/><arg name="dy" type="fixed"/></request>
    <request name="motion_absolute"><arg name="time" type="uint"/><arg name="x" type="uint"/><arg name="y" type="uint"/><arg name="x_extent" type="uint"/><arg name="y_extent" type="uint"/></request>
    <request name="button"><arg name="time" type="uint"/><arg name="button" type="uint"/><arg name="state" type="uint"/></request>
    <request name="axis"><arg name="time" type="uint"/><arg name="axis" type="uint"/><arg name="value" type="fixed"/></request>
    <request name="frame"/>
    <request name="axis_source"><arg name="axis_source" type="uint"/></request>
    <request name="axis_stop"><arg name="time" type="uint"/><arg name="axis" type="uint"/></request>
    <request name="axis_discrete"><arg name="time" type="uint"/><arg name="axis" type="uint"/><arg name="value" type="fixed"/><arg name="discrete" type="int"/></request>
    <request name="destroy" type="destructor"/>
  </interface>
  <interface name="zwlr_virtual_pointer_manager_v1" version="2">
    <request name="create_virtual_pointer"><arg name="seat" type="object" interface="wl_seat" allow-null="true"/><arg name="id" type="new_id" interface="zwlr_virtual_pointer_v1"/></request>
    <request name="destroy" type="destructor"/>
    <request name="create_virtual_pointer_with_output" since="2"><arg name="seat" type="object" interface="wl_seat" allow-null="true"/><arg name="output" type="object" interface="wl_output" allow-null="true"/><arg name="id" type="new_id" interface="zwlr_virtual_pointer_v1"/></request>
  </interface>
</protocol>
"""
VPTR_C = r"""
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <time.h>
#include <wayland-client.h>
#include "vp-client-protocol.h"
struct out { struct wl_output *o; char name[64]; };
static struct zwlr_virtual_pointer_manager_v1 *mgr; static struct wl_seat *seat; static struct out outs[16]; static int nouts;
static void og(void *d, struct wl_output *o, int32_t x, int32_t y, int32_t a, int32_t b, int32_t s, const char *m, const char *n, int32_t t) {}
static void om(void *d, struct wl_output *o, uint32_t f, int32_t w, int32_t h, int32_t r) {}
static void od(void *d, struct wl_output *o) {}
static void os_(void *d, struct wl_output *o, int32_t s) {}
static void on(void *d, struct wl_output *o, const char *n) { snprintf(((struct out *)d)->name, 64, "%s", n); }
static void oe(void *d, struct wl_output *o, const char *n) {}
static const struct wl_output_listener ol = { og, om, od, os_, on, oe };
static void rg(void *d, struct wl_registry *r, uint32_t id, const char *i, uint32_t v) {
  if (!strcmp(i, zwlr_virtual_pointer_manager_v1_interface.name)) mgr = wl_registry_bind(r, id, &zwlr_virtual_pointer_manager_v1_interface, v < 2 ? v : 2);
  else if (!strcmp(i, wl_seat_interface.name) && !seat) seat = wl_registry_bind(r, id, &wl_seat_interface, 1);
  else if (!strcmp(i, wl_output_interface.name) && nouts < 16 && v >= 4) { struct out *u = &outs[nouts++]; u->name[0] = 0;
    u->o = wl_registry_bind(r, id, &wl_output_interface, 4); wl_output_add_listener(u->o, &ol, u); } }
static void rr(void *d, struct wl_registry *r, uint32_t id) {}
static const struct wl_registry_listener rl = { rg, rr };
static uint32_t now(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec * 1000 + t.tv_nsec / 1000000; }
/* vptr <output> <logical w> <logical h> <x> <y> [move|click] */
int main(int c, char **v) {
  if (c < 6) return 2;
  struct wl_display *dpy = wl_display_connect(NULL); if (!dpy) return 1;
  struct wl_registry *reg = wl_display_get_registry(dpy); wl_registry_add_listener(reg, &rl, NULL);
  wl_display_roundtrip(dpy); wl_display_roundtrip(dpy);
  if (!mgr) return 3;
  struct out *u = NULL; for (int i = 0; i < nouts; i++) if (!strcmp(outs[i].name, v[1])) u = &outs[i];
  if (!u) return 4;
  struct zwlr_virtual_pointer_v1 *p = zwlr_virtual_pointer_manager_v1_create_virtual_pointer_with_output(mgr, seat, u->o);
  zwlr_virtual_pointer_v1_motion_absolute(p, now(), atoi(v[4]), atoi(v[5]), atoi(v[2]), atoi(v[3]));
  zwlr_virtual_pointer_v1_frame(p); wl_display_roundtrip(dpy);
  if (c > 6 && !strcmp(v[6], "click")) {
    usleep(60000);
    zwlr_virtual_pointer_v1_button(p, now(), 0x110, 1); zwlr_virtual_pointer_v1_frame(p); wl_display_roundtrip(dpy);
    usleep(40000);
    zwlr_virtual_pointer_v1_button(p, now(), 0x110, 0); zwlr_virtual_pointer_v1_frame(p); wl_display_roundtrip(dpy);
  }
  usleep(30000); zwlr_virtual_pointer_v1_destroy(p); wl_display_roundtrip(dpy); wl_display_disconnect(dpy); return 0; }
"""
DBUS_CONF = """<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <type>session</type>
  <listen>unix:path={sock}</listen>
  <auth>EXTERNAL</auth>
  <policy context="default"><allow send_destination="*" eavesdrop="true"/><allow eavesdrop="true"/><allow own="*"/></policy>
</busconfig>
"""
# Log lines that are not this feature's: Quickshell's own validation of malformed notifications, the shared IPC targets
# every output registers, the property-cache notes and the app-menu binding loop of another lane.
LOG_IGNORE = re.compile(r"quickshell\.service\.notifications|propertyCache|Handler was registered but will not be used|"
                        r"QML AppMenu at|qs:@/qs/AppMenu\.qml|^\s*$")
LOG_ERROR = re.compile(r"TypeError|ReferenceError|SyntaxError|Error:|Cannot read|is not a function|is not defined|"
                       r"Unable to assign|Cannot assign|failed to load|is not a type|Notifications|NotifModel|Binding loop")


def portal_mounts() -> int:
    with open("/proc/self/mountinfo") as f:
        return sum(1 for line in f if "fuse.portal" in line)


def hypr(*args: str) -> str:
    return subprocess.run(["hyprctl", "-i", NEST_SIG, *args], capture_output=True, text=True, timeout=15).stdout


def hypr_json(*args: str):
    return json.loads(hypr("-j", *args) or "null")


def lum(img):
    return img.convert("L")


def since(trace: list, event: str) -> list:
    """The trace from the last change of that kind (opening / melting / changing / rest) on."""
    idx = max((i for i, e in enumerate(trace) if e.get("event") == event), default=None)
    return trace[idx:] if idx is not None else []


@unittest.skipUnless(NEST_SIG and NEST_WL, "nested compositor (MIOS_NOTIFY_NEST_SIG + MIOS_NOTIFY_NEST_WL; never the live desktop)")
class Nested(unittest.TestCase):
    tmp = None
    shell = None
    bus_proc = None
    fixture = None

    # ---- harness -------------------------------------------------------------------------------------------------
    @classmethod
    def setUpClass(cls):
        from PIL import Image  # noqa: F401  (pixel checks need Pillow)
        mons = hypr_json("monitors")
        names = {m["name"]: m for m in mons}
        bad = [n for n in names if not re.match(r"^(HEADLESS-|WAYLAND-)", n)]
        if bad:
            raise unittest.SkipTest(f"refusing: {NEST_SIG} drives real outputs {bad} (the live desktop?)")
        if DESK not in names or PHONE not in names:
            raise unittest.SkipTest(f"nested outputs {DESK} / {PHONE} missing")
        cls.mon = names
        if any(l for m in hypr_json("layers").values() for lv in m["levels"].values() for l in lv
               if l["namespace"].startswith("mios-")):
            raise unittest.SkipTest("another MiOS shell runs on the nested compositor: stop it first")
        cls.portals_before = portal_mounts()
        cls.tmp = tempfile.mkdtemp(prefix="mios-notify-island-")
        # private bus: no service directories -> nothing is ever activated (no portals, no daemons)
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
        assert act.stdout.strip() in ("(['org.freedesktop.DBus'],)",), act.stdout
        # staged QML (and a copy without the status island for the fallback, one with an excluded namespace)
        cls.qs = os.path.join(cls.tmp, "qs")
        shutil.copytree(QS_DIR, cls.qs, ignore=shutil.ignore_patterns("*.bak*", "*.before*"))
        cls.qs_noisland = os.path.join(cls.tmp, "qs-noisland")
        shutil.copytree(cls.qs, cls.qs_noisland)
        p = os.path.join(cls.qs_noisland, "Config.qml")
        src = open(p).read()
        assert "MiOS.PanelWindow { screen: modelData" in src
        open(p, "w").write("\n".join(l for l in src.splitlines() if "MiOS.PanelWindow { screen: modelData" not in l) + "\n")
        excluded = [x for x in ssot()["desktop"]["hyprland"]["hyprglass"]["config"]["layers"]["exclude_namespaces"].split(",") if x]
        cls.qs_flat = os.path.join(cls.tmp, "qs-flat")
        shutil.copytree(cls.qs, cls.qs_flat)
        p = os.path.join(cls.qs_flat, "NotifModel.qml")
        src = open(p).read()
        assert 'layerNamespace: "mios-notifications"' in src
        with open(p, "w") as f:
            f.write(src.replace('layerNamespace: "mios-notifications"', "layerNamespace: " + json.dumps(excluded[-1])))
        # resolver fixture: [desktop.notifications] from a JSON file, everything else from the real layered SSOT
        cls.fixture = os.path.join(cls.tmp, "notifications.json")
        cls.resolver = os.path.join(cls.tmp, "toml-get")
        with open(cls.resolver, "w") as f:
            f.write("#!/bin/sh\nif [ \"$1\" = --section ] && [ \"$2\" = desktop.notifications ]; then cat %s; exit 0; fi\n"
                    "exec /usr/bin/mios-toml-get \"$@\"\n" % cls.fixture)
        os.chmod(cls.resolver, 0o755)
        cls.set_fixture(morph_ms=0)
        # mobile verdict fixture: the phone output is mobile (scale applied by the compositor)
        ph, dk = names[PHONE], names[DESK]
        cls.phone_w, cls.phone_h = round(ph["width"] / ph["scale"]), round(ph["height"] / ph["scale"])
        cls.desk_w, cls.desk_h = dk["width"], dk["height"]
        mob = {"version": 1, "hyprbars_tabs": True, "outputs": {
            n: {"mobile": n == PHONE, "origin": "remote", "logical_width": round(m["width"] / m["scale"]),
                "logical_height": round(m["height"] / m["scale"]), "scale": m["scale"], "applied_scale": m["scale"]}
            for n, m in names.items()},
            "config": {"enabled": True, "gaps_out_px": 6, "hyprbars_tab": "mobile", "app_menu": True,
                       "island_motion": "fadeIn", "island_hold": ["windowsOut", "fadeOut"], "window_controls": []}}
        cls.mobile_json = os.path.join(cls.tmp, "mobile.json")
        with open(cls.mobile_json, "w") as f:
            json.dump(mob, f)
        with open(os.path.join(cls.tmp, "appmenu.json"), "w") as f:
            f.write("{}")
        # virtual pointer (nested only)
        cls.vptr = None
        if shutil.which("gcc") and shutil.which("wayland-scanner") and shutil.which("pkg-config"):
            d = os.path.join(cls.tmp, "vptr")
            os.mkdir(d)
            open(os.path.join(d, "vp.xml"), "w").write(VPTR_XML)
            open(os.path.join(d, "vptr.c"), "w").write(VPTR_C)
            ok = subprocess.run(["wayland-scanner", "client-header", "vp.xml", "vp-client-protocol.h"], cwd=d).returncode == 0
            ok = ok and subprocess.run(["wayland-scanner", "private-code", "vp.xml", "vp-protocol.c"], cwd=d).returncode == 0
            flags = subprocess.run(["pkg-config", "--cflags", "--libs", "wayland-client"], capture_output=True, text=True).stdout.split()
            ok = ok and subprocess.run(["gcc", "-O1", "-o", "vptr", "vptr.c", "vp-protocol.c", *flags], cwd=d).returncode == 0
            cls.vptr = os.path.join(d, "vptr") if ok else None
        # this test's windows: rules on the NESTED compositor only (monitor-relative placement)
        for rule in ('{name = "ntfisl-bg", match = {class = "^(ntfisl\\\\.bg)$"}, float = true, size = "1800 1000", move = "60 50"}',
                     '{name = "ntfisl-pfloat", match = {class = "^(ntfisl\\\\.pfloat)$"}, float = true, size = "200 300", move = "40 60"}'):
            hypr("eval", "_G.ntfisl_rules = rawget(_G, 'ntfisl_rules') or {}; table.insert(_G.ntfisl_rules, hl.window_rule(%s))" % rule)
        cls.windows = []
        cls.focus(DESK)
        cls.open_window("ntfisl.bg")
        cls.start_shell(cls.qs)

    @classmethod
    def tearDownClass(cls):
        cls.stop_shell()
        for p in cls.windows:
            try:
                p.terminate(); p.wait(5)
            except Exception:
                pass
        hypr("eval", "for _, r in ipairs(rawget(_G, 'ntfisl_rules') or {}) do r:set_enabled(false) end")
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

    @classmethod
    def env(cls, **extra) -> dict:
        e = {k: v for k, v in os.environ.items() if k not in ("QT_QPA_PLATFORMTHEME", "DBUS_SESSION_BUS_ADDRESS")}
        e.update(DBUS_SESSION_BUS_ADDRESS=cls.bus, HYPRLAND_INSTANCE_SIGNATURE=NEST_SIG, WAYLAND_DISPLAY=NEST_WL,
                 NO_AT_BRIDGE="1", GTK_USE_PORTAL="0")
        e.update(extra)
        return e

    @classmethod
    def set_fixture(cls, **kw):
        base = {"position": "island", "timeout_ms": 5000, "max_visible": 4, "width_px": 0, "morph_ms": 0,
                "image_max_height_px": 120, "body_max_lines": 6, "actions_max": 6, "do_not_disturb": False}
        base.update(kw)
        with open(cls.fixture, "w") as f:
            json.dump(base, f)
        if cls.shell:
            cls.ipc("notifications", "reload")
            for _ in range(50):
                st = cls.state()
                if st and st["config"]["morph_ms"] == (base["morph_ms"] or st["config"]["morph_ms"]) and \
                        st["config"]["max_visible"] == base["max_visible"]:
                    break
                time.sleep(0.1)

    @classmethod
    def start_shell(cls, qs_dir):
        cls.stop_shell()
        cls.qs_path = qs_dir
        cls.log = os.path.join(cls.tmp, "qs-%d.log" % int(time.time() * 1000))
        cls.shell = subprocess.Popen(
            ["systemd-run", "--user", "--scope", "-q", "-p", "MemoryMax=1G", "-p", "MemorySwapMax=0",
             "--unit=mios-notify-island-test-%d-%d" % (os.getpid(), int(time.time() * 1000)), "quickshell", "--path", os.path.join(qs_dir, "Config.qml")],
            env=cls.env(MIOS_MOBILE_OUTPUTS_JSON=cls.mobile_json, MIOS_APPMENU_JSON=os.path.join(cls.tmp, "appmenu.json"),
                        MIOS_TOML_GET=cls.resolver),
            stdout=open(cls.log, "w"), stderr=subprocess.STDOUT, start_new_session=True)
        for _ in range(150):
            st = cls.state()
            if st and st.get("loaded") and len(st["views"]) >= 2:
                break
            time.sleep(0.2)
        else:
            with open(cls.log, errors="replace") as f:
                raise AssertionError("test shell did not come up: " + f.read()[-3000:])
        time.sleep(1.5)   # let the island settle (its width follows its contents)

    @classmethod
    def stop_shell(cls):
        if cls.shell and cls.shell.poll() is None:
            try:
                os.killpg(cls.shell.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                cls.shell.wait(5)
            except subprocess.TimeoutExpired:
                os.killpg(cls.shell.pid, signal.SIGKILL)
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
            return json.loads(cls.ipc("notifications", "state") or "null")
        except (json.JSONDecodeError, subprocess.TimeoutExpired):
            return None

    @classmethod
    def view(cls, output):
        st = cls.state()
        return next(v for v in st["views"] if v["output"] == output)

    @classmethod
    def focus(cls, output):
        hypr("dispatch", 'hl.dsp.focus({monitor = "%s"})' % output)
        time.sleep(0.3)

    @classmethod
    def open_window(cls, cls_name):
        script = "i=0; while [ $i -lt 90 ]; do printf '%02d #### ==== notify island glass backdrop ~/mios $ ls -la //// |||| oooo XXXX ++++ .... #### MMMM wwww\\n' $i; i=$((i+1)); done; exec sleep 3600"
        p = subprocess.Popen(["alacritty", "--class", cls_name, "-T", cls_name, "-e", "sh", "-c", script],
                             env=cls.env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        cls.windows.append(p)
        for _ in range(50):
            if any(c["class"] == cls_name for c in hypr_json("clients")):
                break
            time.sleep(0.1)
        time.sleep(1.0)
        return p

    @classmethod
    def close_window(cls, cls_name):
        for c in hypr_json("clients"):
            if c["class"] == cls_name:
                hypr("dispatch", 'hl.dsp.window.close({window = "address:%s"})' % c["address"])
        time.sleep(1.0)

    def notify(self, summary, body="", actions=(), hints="{}", timeout=-1, replaces=0, app="MiOS test") -> int:
        acts = "[" + ",".join(json.dumps(a) for a in actions) + "]"
        r = subprocess.run(["gdbus", "call", "--session", "-d", "org.freedesktop.Notifications", "-o", "/org/freedesktop/Notifications",
                            "-m", "org.freedesktop.Notifications.Notify", app, str(replaces), "", summary, body, acts, hints, str(timeout)],
                           env=self.env(), capture_output=True, text=True, timeout=15)
        m = re.search(r"uint32 (\d+)", r.stdout)
        self.assertTrue(m, r.stderr)
        return int(m.group(1))

    def close_notification(self, nid):
        subprocess.run(["gdbus", "call", "--session", "-d", "org.freedesktop.Notifications", "-o", "/org/freedesktop/Notifications",
                        "-m", "org.freedesktop.Notifications.CloseNotification", str(nid)], env=self.env(), capture_output=True)

    def monitor(self):
        """Signals of org.freedesktop.Notifications on the private bus (ActionInvoked / NotificationClosed)."""
        out = os.path.join(self.tmp, "mon-%d.txt" % int(time.time() * 1000))
        p = subprocess.Popen(["dbus-monitor", "--session", "type='signal',interface='org.freedesktop.Notifications'"],
                             env=self.env(), stdout=open(out, "w"), stderr=subprocess.DEVNULL)
        time.sleep(0.4)
        return p, out

    def signals(self, mon):
        p, out = mon
        time.sleep(0.3)
        p.terminate(); p.wait(5)
        txt = open(out).read()
        res = []
        for m in re.finditer(r"member=(ActionInvoked|NotificationClosed)\n\s+uint32 (\d+)\n\s+(?:string \"([^\"]*)\"|uint32 (\d+))", txt):
            res.append((m.group(1), int(m.group(2)), m.group(3) if m.group(3) is not None else int(m.group(4))))
        return res

    def pointer(self, output, x, y, click=False):
        if not self.vptr:
            self.skipTest("no compiler for the virtual pointer")
        w, h = (self.phone_w, self.phone_h) if output == PHONE else (self.desk_w, self.desk_h)
        # enter from the bare bottom-left corner first: a surface that appeared under a still pointer gets no enter
        subprocess.run([self.vptr, output, str(w), str(h), "3", str(h - 3), "move"], env=self.env(), timeout=10)
        time.sleep(0.2)
        r = subprocess.run([self.vptr, output, str(w), str(h), str(int(x)), str(int(y)), "click" if click else "move"],
                           env=self.env(), timeout=10)
        self.assertEqual(r.returncode, 0)
        time.sleep(0.3)

    def grab(self, output, region=None):
        from PIL import Image
        m = self.mon[output]
        if region is None:
            g = "%d,%d %dx%d" % (m["x"], m["y"], round(m["width"] / m["scale"]), round(m["height"] / m["scale"]))
        else:
            x, y, w, h = region
            g = "%d,%d %dx%d" % (m["x"] + x, m["y"] + y, w, h)
        out = subprocess.run(["grim", "-s", "1", "-t", "ppm", "-g", g, "-"], env=self.env(), capture_output=True, timeout=10).stdout
        im = Image.open(io.BytesIO(out)).convert("RGB")
        return im

    def frames(self, output, seconds, region, action=None):
        """Grab frames of a region (logical, output-local) while action() runs; [(wall ms, image)]."""
        res = []
        stop = time.time() + seconds
        t = threading.Thread(target=action) if action else None
        if t:
            t.start()
        while time.time() < stop:
            ts = time.time() * 1000
            res.append((ts, self.grab(output, region)))
        if t:
            t.join()
        return res

    def keep(self, name, im):
        if SHOTS:
            os.makedirs(SHOTS, exist_ok=True)
            im.save(os.path.join(SHOTS, name))

    @staticmethod
    def diff_mask(a, b, thr=24):
        from PIL import ImageChops
        d = ImageChops.difference(a, b)
        r, g, bb = d.split()
        return ImageChops.lighter(ImageChops.lighter(r, g), bb).point(lambda v: 255 if v > thr else 0)

    def wait_for(self, output, pred, timeout=5.0, step=0.1):
        end = time.time() + timeout
        v = None
        while time.time() < end:
            v = self.view(output)
            if pred(v):
                return v
            time.sleep(step)
        self.fail(f"timed out waiting on {output}: {json.dumps(v)[:1500] if v else v}")

    def settle_idle(self, output):
        self.ipc("notifications", "dismissAll")
        return self.wait_for(output, lambda v: not v["active"] and not v["visible"], timeout=6)

    def log_errors(self):
        out = []
        for line in open(self.log, errors="replace").read().splitlines():
            line = re.sub(r"\x1b\[[0-9;]*m", "", line)
            if LOG_IGNORE.search(line):
                continue
            if LOG_ERROR.search(line):
                out.append(line)
        return out

    def notification_layers(self, output):
        levels = hypr_json("layers").get(output, {}).get("levels", {})
        return [l for lv in levels.values() for l in lv if l["namespace"] == "mios-notifications"]

    def topbar_mapped(self, output):
        return any(l["namespace"] in ("mios-topbar", "mios-topbar-remote") for lv in hypr_json("layers").get(output, {}).get("levels", {}).values() for l in lv)

    def park(self, output):
        """The pointer off every notification (hovering the blob pauses expiry)."""
        if self.vptr:
            w, h = (self.phone_w, self.phone_h) if output == PHONE else (self.desk_w, self.desk_h)
            subprocess.run([self.vptr, output, str(w), str(h), "3", str(h - 3), "move"], env=self.env(), timeout=10)
            time.sleep(0.2)

    def no_anim_rule(self):
        r = subprocess.run(["hyprctl", "-i", NEST_SIG, "eval", 'error("rule=" .. tostring(rawget(_G, "mios_notifications_no_anim")))'],
                           capture_output=True, text=True, timeout=10)
        return "rule=HL.LayerRule" in (r.stdout + r.stderr)

    # ---- tests -----------------------------------------------------------------------------------------------------
    def test_01_morph_starts_as_island_and_ends_under_it(self):
        self.focus(DESK)
        self.park(DESK)
        self.settle_idle(DESK)
        self.assertTrue(self.no_anim_rule(), "the shell did not install the no_anim layer rule")
        self.set_fixture(morph_ms=1200, timeout_ms=3000)
        try:
            region = (0, 0, self.desk_w, 300)
            base = self.grab(DESK, region)
            self.keep("01-base.png", base)
            frames = self.frames(DESK, 2.4, region, lambda: self.notify("MiOS test morph", "Grows out of the island.", timeout=2500))
            v = self.view(DESK)
            isl, trace = v["island"], since(v["trace"], "opening")
            # first frame of the morph: the island's own rectangle and radius
            first = trace[0]
            self.assertLess(first["p"], 0.01, trace[:3])
            for a, b in (("x", "x"), ("y", "y"), ("w", "width"), ("h", "height")):
                self.assertAlmostEqual(first[a], isl[b], delta=2, msg=(first, isl))
            self.assertAlmostEqual(first["r"], isl["radius"], delta=0.5)
            # the card: centred under the island, one MeltPopup gap (half an edge margin) below it, global rounding
            to = v["to"]
            self.assertAlmostEqual(to["x"] + to["width"] / 2, isl["x"] + isl["width"] / 2, delta=1)
            self.assertAlmostEqual(to["y"], isl["y"] + isl["height"] + v["gap"], delta=1)
            self.assertEqual(v["gap"], ssot()["theme"]["shell"]["edge_margin_px"] / 2)
            self.assertAlmostEqual(v["to_radius"], ssot()["desktop"]["hyprland"]["config"]["decoration"]["rounding"], delta=0.5)
            # the surface box: the whole path while the blob moves (the island included), exactly the card at rest
            b0 = first["box"]
            self.assertTrue(b0[0] <= isl["x"] + 1 and b0[1] <= isl["y"] + 1 and b0[0] + b0[2] >= isl["x"] + isl["width"] - 1, (b0, isl))
            lay = self.notification_layers(DESK)
            self.assertEqual(len(lay), 1)
            for a, b in (("x", "x"), ("w", "width"), ("h", "height")):
                self.assertAlmostEqual(lay[0][a], to[b], delta=1.5, msg=(lay, to))
            self.assertAlmostEqual(lay[0]["y"] - self.mon[DESK]["y"], to["y"], delta=1.5, msg=(lay, to))
            # pixels: an early frame shows the blob AS the island (the island frosted, nothing outside it) ...
            ix, iy, iw, ih = isl["x"], isl["y"], isl["width"], isl["height"]
            early = None
            for ts, im in frames:
                bb = self.diff_mask(base, im).getbbox()
                if not bb:
                    continue
                cover = sum(1 for p in self.diff_mask(base, im).crop((ix, iy, ix + iw, iy + ih)).tobytes() if p) / (iw * ih)
                if cover > 0.12:
                    early = (ts, bb, cover, im)
                    break
            self.assertIsNotNone(early, "the blob never covered the island")
            self.keep("01-first-blob-frame.png", early[3])
            bb = early[1]
            # (6 px: the compositor's glass edge light and refraction reach a few px past the alpha edge)
            self.assertTrue(bb[0] >= ix - 6 and bb[2] <= ix + iw + 6 and bb[1] >= iy - 6 and bb[3] <= iy + ih + 6,
                            f"first blob frame {bb} is not the island {isl}")
            # ... it grows downward, and the settled card is the diff below the island
            mids = [self.diff_mask(base, im).crop((0, iy + ih + 2, self.desk_w, 300)).getbbox() for _, im in frames]
            bottoms = [b[3] for b in mids if b]
            self.assertTrue(bottoms and max(bottoms) >= to["y"] + to["height"] - 6 - (iy + ih + 2), (bottoms, to))
            rest_at = next(e["t"] for e in trace if e.get("event") == "rest")
            settled = next(im for ts, im in frames if ts > rest_at + 100)
            self.keep("01-settled.png", settled)
            card = self.diff_mask(base, settled).crop((0, iy + ih + 2, self.desk_w, 300)).getbbox()
            self.assertIsNotNone(card)
            # (pixels: within the rounded corners' reach -- the exact box is the compositor's layer geometry above)
            self.assertAlmostEqual(card[0], to["x"], delta=12)
            self.assertAlmostEqual(card[2], to["x"] + to["width"], delta=12)
            # negative: nothing at the top right, ever
            for _, im in frames:
                self.assertIsNone(self.diff_mask(base, im).crop((self.desk_w - 420, 0, self.desk_w, 300)).getbbox(), "something at the top right")
            # negative: nothing on the other outputs
            for o in (PHONE,):
                ov = self.view(o)
                self.assertEqual((ov["cards"], ov["progress"], ov["visible"]), ([], 0, False))
            # melt: expiry runs the morph backwards into the island
            v = self.wait_for(DESK, lambda v: not v["active"], timeout=8)
            back = [e for e in since(v["trace"], "melting") if not e["open"] and "event" not in e]
            self.assertTrue(back and back[-1]["p"] == 0, back[-3:])
            ps = [e["p"] for e in back]
            self.assertEqual(ps, sorted(ps, reverse=True), "the melt is not monotonic")
            last = back[-2] if len(back) > 1 else back[-1]
            self.assertLess(last["y"] + last["h"], isl["y"] + isl["height"] + 0.25 * (to["y"] + to["height"] - isl["y"] - isl["height"]) + 2)
            self.assertFalse(v["visible"], "the surface stayed mapped after the melt")
            after = self.grab(DESK, region)
            self.assertIsNone(self.diff_mask(base, after).crop((0, iy + ih + 2, self.desk_w, 300)).getbbox(), "the blob left something behind")
        finally:
            self.set_fixture(morph_ms=0)

    def test_02_second_notification_grows_the_same_blob(self):
        self.focus(DESK)
        self.park(DESK)
        self.settle_idle(DESK)
        self.notify("MiOS test stack one", "first", timeout=0)
        v1 = self.wait_for(DESK, lambda v: v["phase"] == "rest" and len(v["cards"]) == 1)
        self.notify("MiOS test stack two", "second", timeout=0)
        v2 = self.wait_for(DESK, lambda v: v["phase"] == "rest" and len(v["cards"]) == 2)
        self.assertEqual(len(self.notification_layers(DESK)), 1, "more than one notification surface")
        self.assertEqual(v2["to"]["y"], v1["to"]["y"])
        c1, c2 = v2["cards"]
        self.assertAlmostEqual(v2["glass"]["height"], c1["height"] + c2["height"], delta=1)
        self.assertAlmostEqual(c2["hits"]["card"]["y"], c1["hits"]["card"]["y"] + c1["height"], delta=1)
        self.assertGreater(v2["glass"]["height"], v1["glass"]["height"])
        self.keep("02-stack.png", self.grab(DESK, (0, 0, self.desk_w, 300)))
        # max_visible: the oldest expires
        self.set_fixture(max_visible=2)
        try:
            self.notify("MiOS test stack three", "third", timeout=0)
            v3 = self.wait_for(DESK, lambda v: v["phase"] == "rest" and [c["title"] for c in v["cards"]] == ["MiOS test stack two", "MiOS test stack three"], timeout=4)
            self.assertEqual(v3["live"], 2)
        finally:
            self.set_fixture()
        self.settle_idle(DESK)

    def test_03_close_collapses_one_and_the_last_melts(self):
        self.focus(DESK)
        self.settle_idle(DESK)
        a = self.notify("MiOS test close A", "stays", timeout=0)
        b = self.notify("MiOS test close B", "closed by its cross", timeout=0)
        v = self.wait_for(DESK, lambda v: v["phase"] == "rest" and len(v["cards"]) == 2)
        mon = self.monitor()
        close = v["cards"][1]["hits"]["close"]
        self.pointer(DESK, close["x"] + close["width"] / 2, close["y"] + close["height"] / 2, click=True)
        v = self.wait_for(DESK, lambda v: v["phase"] == "rest" and [c["title"] for c in v["cards"]] == ["MiOS test close A"], timeout=4)
        self.assertEqual(v["progress"], 1, "the blob melted although a card is left")
        self.close_notification(a)   # the app closes the last one: the whole blob melts back
        v = self.wait_for(DESK, lambda v: not v["active"], timeout=4)
        sig = self.signals(mon)
        self.assertIn(("NotificationClosed", b, 2), sig)
        self.assertIn(("NotificationClosed", a, 3), sig)
        self.assertEqual(since(v["trace"], "melting")[-2]["p"], 0)

    def test_04_actions_and_default_action_fire(self):
        self.focus(DESK)
        self.settle_idle(DESK)
        mon = self.monitor()
        n1 = self.notify("MiOS test actions", "Screenshot saved", actions=["default", "Open", "open", "Open", "edit", "Edit", "copy", "Copy"], timeout=0)
        v = self.wait_for(DESK, lambda v: v["phase"] == "rest" and v["cards"] and len(v["cards"][0]["hits"]["actions"]) == 3)
        btn = next(a for a in v["cards"][0]["hits"]["actions"] if a["id"] == "edit")
        self.pointer(DESK, btn["x"] + btn["width"] / 2, btn["y"] + btn["height"] / 2, click=True)
        self.wait_for(DESK, lambda v: not v["active"], timeout=4)
        n2 = self.notify("MiOS test default", "click the card", actions=["default", "Open"], timeout=0)
        v = self.wait_for(DESK, lambda v: v["phase"] == "rest" and v["cards"])
        card = v["cards"][0]["hits"]["card"]
        self.pointer(DESK, card["x"] + 30, card["y"] + card["height"] - 12, click=True)
        self.wait_for(DESK, lambda v: not v["active"], timeout=4)
        sig = self.signals(mon)
        self.assertIn(("ActionInvoked", n1, "edit"), sig)
        self.assertIn(("ActionInvoked", n2, "default"), sig)
        # replaces_id: the same card, updated in place
        n3 = self.notify("MiOS test replace", "before", timeout=0)
        self.wait_for(DESK, lambda v: v["phase"] == "rest" and v["cards"])
        self.assertEqual(self.notify("MiOS test replace (updated)", "after", timeout=0, replaces=n3), n3)
        v = self.wait_for(DESK, lambda v: [c["title"] for c in v["cards"]] == ["MiOS test replace (updated)"], timeout=3)
        self.assertEqual(v["cards"][0]["body"], "after")
        self.settle_idle(DESK)

    def test_05_critical_stays_until_dismissed(self):
        self.focus(DESK)
        self.park(DESK)
        self.settle_idle(DESK)
        self.notify("MiOS test critical", "stays", hints="{'urgency': <byte 2>}", timeout=400)
        self.notify("MiOS test normal", "goes", timeout=600)
        time.sleep(2.5)
        v = self.view(DESK)
        self.assertEqual([c["title"] for c in v["cards"]], ["MiOS test critical"])
        self.assertTrue(v["cards"][0]["critical"])
        # do not disturb: only critical ones show
        self.ipc("notifications", "dnd", "true")
        try:
            self.notify("MiOS test dnd", "hidden", timeout=0)
            time.sleep(0.8)
            self.assertEqual(len(self.view(DESK)["cards"]), 1)
        finally:
            self.ipc("notifications", "dnd", "false")
        self.settle_idle(DESK)

    def measure_glass(self, qs_dir):
        from PIL import ImageFilter, ImageStat
        if qs_dir != self.qs_path:
            self.start_shell(qs_dir)
        self.focus(DESK)
        self.park(DESK)
        self.settle_idle(DESK)
        region = (0, 0, self.desk_w, 300)
        # frost: over the busy backdrop window the blob blurs what is behind it
        base = self.grab(DESK, region)
        self.notify("MiOS test glass", "", timeout=0)
        v = self.wait_for(DESK, lambda v: v["phase"] == "rest" and v["cards"])
        time.sleep(0.4)
        im = self.grab(DESK, region)
        g = v["glass"]
        lap = lambda i: i.convert("L").filter(ImageFilter.Kernel((3, 3), [0, -1, 0, -1, 4, -1, 0, -1, 0], 1, 128)).point(lambda p: abs(p - 128))
        inner = (int(g["x"] + 150), int(g["y"] + 8), int(g["x"] + g["width"] - 44), int(g["y"] + g["height"] - 8))
        hf_base = ImageStat.Stat(lap(base).crop(inner)).mean[0]
        hf_glass = ImageStat.Stat(lap(im).crop(inner)).mean[0]
        std = ImageStat.Stat(im.convert("L").crop(inner)).stddev[0]
        self.keep("06-frost-%s.png" % os.path.basename(qs_dir), im)
        self.settle_idle(DESK)
        # edge light: over a uniform backdrop (no window) the card's top edge carries the preset's specular band
        self.close_window("ntfisl.bg")
        try:
            self.notify("MiOS test edge", "a\nb\nc\nd", timeout=0)
            v = self.wait_for(DESK, lambda v: v["phase"] == "rest" and v["cards"])
            time.sleep(0.4)
            L = self.grab(DESK, region).convert("L")
            g = v["glass"]
            x0, x1 = int(g["x"] + g["width"] / 2), int(g["x"] + g["width"] - g["radius"] - 20)
            top = max(ImageStat.Stat(L.crop((x0, int(g["y"]) + d, x1, int(g["y"]) + d + 1))).mean[0] for d in range(1, 7))
            mid = ImageStat.Stat(L.crop((x0, int(g["y"] + g["height"] / 2) - 2, x1, int(g["y"] + g["height"] / 2) + 2))).mean[0]
            self.keep("06-edge-%s.png" % os.path.basename(qs_dir), L)
            self.settle_idle(DESK)
        finally:
            self.focus(DESK)
            self.open_window("ntfisl.bg")
        return hf_base, hf_glass, std, top - mid

    def test_06_glass_present_not_flat(self):
        hf_base, hf_glass, std, edge = self.measure_glass(self.qs)
        self.assertGreater(hf_base, 10, "the backdrop under the card is not busy enough to tell glass from flat")
        self.assertLess(hf_glass / hf_base, 0.3, "the backdrop is not frosted under the blob (no glass)")
        self.assertGreater(std, 2.0, "the blob is a flat colour")
        self.assertGreater(edge, 5.0, "no specular edge light on the card's top edge")
        # negative control: the same blob on a namespace hyprglass excludes measures flat, without edge light
        try:
            hf_base2, hf_flat, _, edge_flat = self.measure_glass(self.qs_flat)
            self.assertGreater(hf_flat / hf_base2, 0.5, "the glass check does not tell a glass-less layer apart")
            self.assertLess(edge_flat, 3.0, "the edge check does not tell a glass-less layer apart")
        finally:
            self.start_shell(self.qs)

    def test_07_no_input_outside_the_blob(self):
        self.focus(DESK)
        self.park(DESK)
        self.settle_idle(DESK)
        self.notify("MiOS test input", "only the blob takes input", timeout=0)
        v = self.wait_for(DESK, lambda v: v["phase"] == "rest" and v["cards"])
        layer = self.notification_layers(DESK)
        self.assertEqual(len(layer), 1)
        to = v["to"]
        # at rest the surface is the card's box: its corners outside the rounded glass are not the blob ...
        self.assertAlmostEqual(layer[0]["y"] - self.mon[DESK]["y"] + layer[0]["h"], to["y"] + to["height"], delta=1.5)
        self.focus(PHONE)
        # (the bottom-left corner: the backdrop window lies under it)
        self.pointer(DESK, to["x"] + 2, to["y"] + to["height"] - 2)
        self.assertEqual(hypr_json("activewindow").get("class"), "ntfisl.bg", "a corner of the surface outside the blob took the pointer")
        self.assertFalse(self.view(DESK)["hovered"])
        # ... and the blob is: it hovers the card, the window below loses the pointer
        self.focus(PHONE)
        self.pointer(DESK, to["x"] + to["width"] / 2, to["y"] + to["height"] / 2)
        self.assertTrue(self.view(DESK)["hovered"])
        self.assertNotEqual(hypr_json("activewindow").get("class"), "ntfisl.bg")
        self.park(DESK)
        self.settle_idle(DESK)

    def test_08_mobile_grows_from_the_off_centre_island(self):
        self.focus(PHONE)
        self.open_window("ntfisl.phone")
        self.open_window("ntfisl.pfloat")
        try:
            self.focus(PHONE)
            mob = None
            for _ in range(40):
                mob = json.loads(self.ipc("mobile", "state") or "{}")
                if mob.get("island", {}).get("animating") is False and mob["island"]["px"]["width"] > 0:
                    break
                time.sleep(0.2)
            px = mob["island"]["px"]
            W = self.phone_w
            self.assertGreater(abs(px["x"] + px["width"] / 2 - W / 2), 20, f"island not off-centre: {px}")
            self.settle_idle(PHONE)
            region = (0, 0, W, 360)
            base = self.grab(PHONE, region)
            self.notify("MiOS test phone", "Grows from the mobile island.", actions=["open", "Open", "edit", "Edit"], timeout=0)
            v = self.wait_for(PHONE, lambda v: v["phase"] == "rest" and v["cards"])
            first = since(v["trace"], "opening")[0]
            for a, b in (("x", "x"), ("y", "y"), ("w", "width"), ("h", "height")):
                self.assertAlmostEqual(first[a], px[b], delta=2, msg=(first, px))
            margin = 8
            to = v["to"]
            want = max(margin, min(px["x"] + px["width"] / 2 - to["width"] / 2, W - margin - to["width"]))
            self.assertAlmostEqual(to["x"], want, delta=1)
            self.assertLessEqual(to["x"] + to["width"], W - margin + 0.5)
            self.assertGreaterEqual(to["x"], margin - 0.5)
            touch = ssot()["desktop"]["hyprland"]["hyprbars"]["mobile"]["touch_target"]
            for h in v["cards"][0]["hits"]["actions"] + [v["cards"][0]["hits"]["close"]]:
                self.assertGreaterEqual(min(h["width"], h["height"]), touch - 0.5, h)
            im = self.grab(PHONE, region)
            self.keep("08-phone.png", im)
            # the compositor's view: the rest surface is exactly the card, where the IPC says
            lay = self.notification_layers(PHONE)
            self.assertEqual(len(lay), 1, lay)
            self.assertAlmostEqual(lay[0]["x"] - self.mon[PHONE]["x"], to["x"], delta=1.5)
            self.assertAlmostEqual(lay[0]["y"] - self.mon[PHONE]["y"], to["y"], delta=1.5)
            self.assertAlmostEqual(lay[0]["w"], to["width"], delta=1.5)
            # pixels: the card's rectangle changed (the blob is there)
            inner = (int(to["x"]) + 6, int(to["y"]) + 6, int(to["x"] + to["width"]) - 6, int(min(360, to["y"] + to["height"])) - 6)
            cover = sum(1 for p in self.diff_mask(base, im).crop(inner).tobytes() if p) / max(1, (inner[2] - inner[0]) * (inner[3] - inner[1]))
            self.assertGreater(cover, 0.15, "no blob where the card should be")
            self.assertEqual(self.view(DESK)["cards"], [])
            self.settle_idle(PHONE)
        finally:
            self.close_window("ntfisl.pfloat")
            self.close_window("ntfisl.phone")

    def test_09_fullscreen_reveals_the_island_for_the_blob(self):
        self.focus(DESK)
        self.park(DESK)
        self.settle_idle(DESK)
        self.open_window("ntfisl.fs")
        try:
            addr = next(c["address"] for c in hypr_json("clients") if c["class"] == "ntfisl.fs")
            hypr("dispatch", 'hl.dsp.window.fullscreen({mode = "fullscreen", action = "set", window = "address:%s"})' % addr)
            time.sleep(1.5)
            if next(c for c in hypr_json("clients") if c["class"] == "ntfisl.fs")["fullscreen"] < 2:
                hypr("dispatch", 'hl.dsp.window.fullscreen({mode = "fullscreen", action = "toggle", window = "address:%s"})' % addr)
                time.sleep(1.5)
            v = self.view(DESK)
            self.assertTrue(v["reveal"]["top_active"])
            self.assertFalse(v["island"]["shown"])
            self.assertFalse(self.topbar_mapped(DESK))
            region = (0, 0, self.desk_w, 300)
            base = self.grab(DESK, region)
            frames = self.frames(DESK, 1.5, region, lambda: self.notify("MiOS test fullscreen", "over a fullscreen window", timeout=1500))
            v = self.view(DESK)
            self.assertTrue(v["island"]["shown"] and v["reveal"]["island_shown"])
            self.assertTrue(self.topbar_mapped(DESK))
            isl = v["island"]
            self.assertAlmostEqual(since(v["trace"], "opening")[0]["y"], isl["y"], delta=2)
            below = [self.diff_mask(base, im).crop((0, isl["y"] + isl["height"] + 2, self.desk_w, 300)).getbbox() for _, im in frames]
            self.assertTrue(any(b and b[3] > 30 for b in below), "the blob never emerged below the island")
            self.keep("09-fullscreen.png", frames[-1][1])
            # after the melt and the reveal's hide delay the island hides again
            self.wait_for(DESK, lambda v: not v["active"], timeout=6)
            hide = ssot()["desktop"]["topbar"]["fullscreen_hide_delay_ms"] / 1000
            time.sleep(hide + 1.2)
            v = self.view(DESK)
            self.assertFalse(v["island"]["shown"])
            self.assertFalse(v["reveal"]["island_shown"])
            self.assertFalse(self.topbar_mapped(DESK))
        finally:
            self.close_window("ntfisl.fs")

    def test_10_no_island_emerges_from_the_top_centre(self):
        self.start_shell(self.qs_noisland)
        try:
            self.focus(DESK)
            region = (0, 0, self.desk_w, 300)
            base = self.grab(DESK, region)
            self.notify("MiOS test no island", "from the top centre", timeout=0)
            v = self.wait_for(DESK, lambda v: v["phase"] == "rest" and v["cards"])
            self.assertTrue(v["fallback"])
            f, to = v["from"], v["to"]
            self.assertAlmostEqual(f["x"] + f["width"] / 2, self.desk_w / 2, delta=1)
            self.assertAlmostEqual(to["x"] + to["width"] / 2, self.desk_w / 2, delta=1)
            self.assertAlmostEqual(f["y"], 8, delta=1)
            im = self.grab(DESK, region)
            self.keep("10-no-island.png", im)
            bb = self.diff_mask(base, im).getbbox()
            self.assertIsNotNone(bb)
            self.assertAlmostEqual((bb[0] + bb[2]) / 2, self.desk_w / 2, delta=12)
            self.assertIsNone(self.diff_mask(base, im).crop((self.desk_w - 420, 0, self.desk_w, 300)).getbbox())
            self.settle_idle(DESK)
        finally:
            self.start_shell(self.qs)

    def test_11_malformed_notifications_do_not_break_the_shell(self):
        from PIL import Image
        self.focus(DESK)
        self.settle_idle(DESK)
        huge = os.path.join(self.tmp, "huge.png")
        Image.new("RGB", (8000, 6000), (200, 40, 40)).save(huge)
        self.notify("", "", timeout=0)                                                     # no summary, no body
        self.notify("MiOS test huge image", "", hints="{'image-path': <'file://%s'>}" % huge, timeout=0)
        self.notify("MiOS test bogus image-data", "x", hints="{'image-data': <(100, 100, 1, true, 8, 4, [byte 1, 2, 3])>}", timeout=0)
        self.notify("MiOS test <b>markup</b>", "<b>b</b> <img src='file:///etc/passwd'/> <a href='x'>l</a>", actions=["a"], timeout=0)
        self.notify("MiOS test long", "B" * 20000, actions=["", "", "x"], timeout=0)
        time.sleep(1.5)
        self.assertIsNone(self.shell.poll(), "the shell died")
        v = self.view(DESK)
        self.assertEqual(len(v["cards"]), 4, [c["title"] for c in v["cards"]])   # max_visible 4: the oldest expired
        img = next(c for c in v["cards"] if c["title"] == "MiOS test huge image")
        self.assertTrue(img["image"])
        self.assertLessEqual(img["height"], 120 + 120)
        mark = next(c for c in v["cards"] if c["title"].startswith("MiOS test <b>"))
        self.assertIn("<b>b</b>", mark["body"])   # plain text, never markup
        self.assertLess(v["glass"]["height"], self.desk_h)
        self.settle_idle(DESK)
        self.assertEqual(self.log_errors(), [])

    def test_12_shell_toast_uses_the_same_blob(self):
        self.focus(DESK)
        self.park(DESK)
        self.settle_idle(DESK)
        self.ipc("shot", "toast", "MiOS test shot toast", "Saved")
        v = self.wait_for(DESK, lambda v: v["phase"] == "rest" and v["cards"])
        self.assertEqual(v["cards"][0]["title"], "MiOS test shot toast")
        self.assertAlmostEqual(since(v["trace"], "opening")[0]["y"], v["island"]["y"], delta=2)
        self.settle_idle(DESK)
        self.assertEqual(self.log_errors(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
