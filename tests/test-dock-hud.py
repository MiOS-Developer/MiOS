#!/usr/bin/env python3
# AI-hint: Contract tests for the dock HUD (no compositor needed): the expanded dock grows upward at the bar's width with
#          SSOT height/animation ([desktop.dock]); DockConfig's fallbacks equal the vendor SSOT; every [desktop.dock] key is
#          in the configurator; window previews capture only mapped windows on a live monitor and are flat (no borders,
#          margins or frames); app labels wrap/shrink and have a tooltip; the remote-device item lives in the topbar, not the
#          dock; the keyboard toggle state is user-tier. Each contract also runs against the pre-change tree from git
#          (NEG_REF, default main's merge-base) and must FAIL there: the negative control.
# AI-related: usr/share/mios/quickshell/Sidebar.qml, DockExpanded.qml, DockPreview.qml, DockConfig.qml, DockPopup.qml,
#             PanelWindow.qml, usr/share/mios/mios.toml [desktop.dock], usr/share/mios/configurator/mios.html
import os, re, subprocess, sys, tomllib, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QS = "usr/share/mios/quickshell"
NEG_REF = os.environ.get("NEG_REF") or subprocess.run(["git", "-C", ROOT, "merge-base", "HEAD", "main"], capture_output=True, text=True).stdout.strip()


def read(rel, ref=None):
    if ref is None:
        p = os.path.join(ROOT, rel)
        if not os.path.exists(p):
            return ""
        with open(p) as f:
            return f.read()
    r = subprocess.run(["git", "-C", ROOT, "show", f"{ref}:{rel}"], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def ssot(ref=None):
    t = read("usr/share/mios/mios.toml", ref)
    return tomllib.loads(t) if t else {}


# ---- contracts: each returns a list of problems ("" list = holds) ----------------------------------------------------
def grows_upward_at_bar_width(ref=None):
    s = read(f"{QS}/Sidebar.qml", ref)
    p = []
    if "drawerWidth - tiles.implicitWidth" in s or "drawerWidth" in s:
        p.append("dock width still morphs to the drawer width")
    if "DockConfig.expandedHeight(" not in s:
        p.append("expanded height not from [desktop.dock]")
    if "DockConfig.expandMs" not in s:
        p.append("expand animation not from [desktop.dock]")
    if not re.search(r"anchors\.bottom:\s*true", s):
        p.append("dock not anchored to the bottom edge (it must grow upward)")
    return p


def config_fallbacks_match_ssot(ref=None):
    d = ssot(ref).get("desktop", {}).get("dock")
    q = read(f"{QS}/DockConfig.qml", ref)
    if not d or not q:
        return ["no [desktop.dock] or DockConfig.qml"]
    p = []
    def fb(table, key):
        m = re.search(r'(?:num|bool|str)\(%s,\s*"%s",\s*("?[^,)"]*"?)\)' % (table, key), q)
        return m.group(1).strip('"') if m else None
    for key in ["expand_height_fraction", "expand_min_height_px", "expand_max_height_px", "expand_min_width_px", "expand_ms",
                "label_lines", "label_min_font_px", "tooltip_delay_ms"]:
        if str(fb("table", key)) != str(d[key]).lower():
            p.append(f"{key}: fallback {fb('table', key)} != SSOT {d[key]}")
    for sub, keys in {"previews": ["enabled", "include", "height_px", "live", "max"], "osk": ["enabled", "show", "height_px"],
                      "targets": ["enabled", "poll_ms"]}.items():
        for key in keys:
            if str(fb(sub, key)) != str(d[sub][key]).lower():
                p.append(f"{sub}.{key}: fallback {fb(sub, key)} != SSOT {d[sub][key]}")
    return p


def configurator_has_every_scalar(ref=None):
    d = ssot(ref).get("desktop", {}).get("dock")
    h = read("usr/share/mios/configurator/mios.html", ref)
    if not d:
        return ["no [desktop.dock]"]
    p = []
    def walk(t, prefix):
        for k, v in t.items():
            if isinstance(v, dict):
                walk(v, f"{prefix}.{k}")
            elif not isinstance(v, list) and k not in ("command", "layer", "namespace"):
                if f'data-key="{prefix}.{k}"' not in h:
                    p.append(f"{prefix}.{k} missing from the configurator")
    walk(d, "desktop.dock")
    return p


def previews_capture_safe_and_flat(ref=None):
    pv = read(f"{QS}/DockPreview.qml", ref)
    pop = read(f"{QS}/DockPopup.qml", ref)
    p = []
    if not pv:
        return ["no DockPreview.qml"]
    if not re.search(r"info\.monitor\s*>=\s*0", pv) or "info.mapped !== false" not in pv:
        p.append("preview may capture a window without a live monitor or unmapped")
    if "captureSource: preview.capturable ?" not in pv:
        p.append("capture source not gated on capturability")
    code = "\n".join(l for l in pv.splitlines() if not l.strip().startswith("//"))
    if "border." in code or "anchors.margins" in code:
        p.append("preview has a border or margins")
    card = pop[pop.find("id: winCard"):pop.find("id: cardMouse")] if "id: winCard" in pop else ""
    if "border.width" in card or re.search(r"anchors\.margins:\s*6", card):
        p.append("popup preview card still has a border/inset")
    if "DockPreview {" not in card:
        p.append("popup does not use the capture-safe DockPreview")
    raw = re.findall(r"ScreencopyView\s*\{[^}]*\}", pop)
    if any("phonePreview" not in r for r in raw):
        p.append("popup has an unguarded window ScreencopyView")
    return p


def labels_fit_and_tooltip(ref=None):
    ex = read(f"{QS}/DockExpanded.qml", ref)
    if not ex:
        return ["no DockExpanded.qml"]
    p = []
    for needle in ["maximumLineCount: DockConfig.labelLines", "fontSizeMode: Text.Fit", "minimumPixelSize: DockConfig.labelMinFont",
                   "wrapMode: Text.WrapAtWordBoundaryOrAnywhere", "tip.show(tile)", "tile.focused"]:
        if needle not in ex:
            p.append(f"label/tooltip contract missing: {needle}")
    return p


def remote_item_in_topbar_not_dock(ref=None):
    sb = read(f"{QS}/Sidebar.qml", ref)
    pw = read(f"{QS}/PanelWindow.qml", ref)
    p = []
    if "id: phoneTile" in sb:
        p.append("the remote-device tile is still in the dock")
    if 'shown("remote")' not in pw:
        p.append("no inbound remote item in the topbar")
    return p


def osk_toggle_user_tier(ref=None):
    sb = read(f"{QS}/Sidebar.qml", ref)
    p = []
    if "mios/dock-state.json" not in sb or "XDG_STATE_HOME" not in sb:
        p.append("keyboard toggle not persisted in the user state file")
    if '"dock", "osk"' not in sb:
        p.append("keyboard not launched through mios-gen dock osk")
    if "dock.expanded" not in sb[sb.find("id: oskProc"):sb.find("id: oskProc") + 600]:
        p.append("keyboard not tied to the expanded dock")
    return p


CONTRACTS = [grows_upward_at_bar_width, config_fallbacks_match_ssot, configurator_has_every_scalar, previews_capture_safe_and_flat,
             labels_fit_and_tooltip, remote_item_in_topbar_not_dock, osk_toggle_user_tier]


class Positive(unittest.TestCase):
    pass


class Negative(unittest.TestCase):
    pass


for c in CONTRACTS:
    setattr(Positive, f"test_{c.__name__}", lambda self, c=c: self.assertEqual(c(), [], c.__name__))
    setattr(Negative, f"test_{c.__name__}_fails_before", lambda self, c=c: self.assertNotEqual(c(NEG_REF), [], f"{c.__name__} already held at {NEG_REF}"))


if __name__ == "__main__":
    if not NEG_REF:
        sys.exit("NEG_REF unresolved")
    unittest.main(verbosity=2)
