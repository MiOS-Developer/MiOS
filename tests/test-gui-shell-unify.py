#!/usr/bin/env python3
# AI-hint: Two-sided contract test suite verifying the comprehensive fix for all 6 GUI/Shell defects:
#          - Defect 0: Global Keybinds Active (dock owns typing while open; generated drawer escapes bypass inhibition).
#          - Defect 1: Full Liquid Glass Surfaces (Search's body is the one shared GlassSurface with its defaults -- no
#            per-surface radius, fill, outline or hard-coded colour/opacity, none set later by a state, transition,
#            animation or Binding, no tint layer or second GlassSurface of its own, no alias exposing its glass, no
#            window tint behind it and no local GlassSurface component, with planted negative controls; the
#            centralized specular rim; compositor blur).
#          - Defect 2: Phosh OSK Virtual Keyboard (embedded in 220px slot, Phosh header, direct targetInput typing + sendshortcut IPC, D-Bus unfold).
#          - Defect 3: Maximize Workarea Bounds [0, 44, 0, 72] & TopBar Deconfliction (exclusiveZone 36px, fullscreen >= 2, tmux-os unoccluded).
#          - Defect 4: Rounded Corner UI Highlights (Glass.radius / innerRadius / pillRadius, zero square highlights).
#          - Defect 5: Dock App Launch Origin Animation (accurate icon coordinates, non-zero fallback, smooth zoom-out).
#          - GNOME & Phosh App Launching (Calls DBusActivatable bypass, XDG_CURRENT_DESKTOP GNOME:Phosh, drawer dismiss).
#          Two-sided verification: Every contract runs against working tree (must PASS) and git base commit (must FAIL).
# AI-related: usr/share/mios/quickshell/VirtualKeyboard.qml, usr/share/mios/quickshell/DockExpanded.qml,
#             usr/share/mios/quickshell/Sidebar.qml, usr/share/mios/quickshell/Search.qml,
#             usr/share/mios/quickshell/Keymap.qml, usr/share/mios/quickshell/AppMenu.qml,
#             usr/share/mios/quickshell/PanelWindow.qml, usr/share/mios/quickshell/Glass.qml,
#             usr/share/mios/quickshell/DockPreview.qml, usr/share/mios/quickshell/AppLaunch.qml,
#             usr/libexec/mios/mios-desktop-launch, usr/share/mios/hypr/hyprland-effects.conf
import os
import json
import shutil
import shlex
import tomllib
import re
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QS = "usr/share/mios/quickshell"
BASE_REV = "72bd86de9f98fa7abef0ddf4dbeaec947450ccd4"
NEG_REF = os.environ.get("NEG_REF") or BASE_REV


def read(rel: str, ref: str = None) -> str:
    if ref is None:
        p = os.path.join(ROOT, rel)
        if not os.path.exists(p):
            return ""
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    r = subprocess.run(["git", "-C", ROOT, "show", f"{ref}:{rel}"], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


# ---- Defect 0: Global Keybinds & Layer Keyboard Focus -----------------------------------------------------------------
def contract_defect0_layer_keyboard_focus(ref: str = None) -> list[str]:
    problems = []
    # Search.qml: keyboard focus must NOT be unconditionally Exclusive when requestedOpen is true
    s = read(f"{QS}/Search.qml", ref)
    if not s:
        problems.append("Search.qml missing")
    else:
        if re.search(r"WlrLayershell\.keyboardFocus:\s*requestedOpen\s*\?\s*WlrKeyboardFocus\.Exclusive\s*:\s*WlrKeyboardFocus\.None", s):
            problems.append("Search.qml grabs unconditional Exclusive focus on requestedOpen")
        if "activeFocus" not in s:
            problems.append("Search.qml keyboardFocus not gated on activeFocus")
        # Focus grab must use active: property
        if re.search(r"HyprlandFocusGrab\s*\{[^}]*visible:", s):
            problems.append("Search.qml HyprlandFocusGrab uses non-existent property 'visible'")

    # The expanded drawer owns typing even with its non-focus-stealing OSK; collapsing releases keyboard focus.
    sb = read(f"{QS}/Sidebar.qml", ref)
    focus = re.search(r"WlrLayershell\.keyboardFocus:\s*([^\n]+)", sb)
    if not focus:
        problems.append("Sidebar.qml keyboard focus binding missing")
    else:
        script = """
const focus = new Function("expanded", "root", "WlrKeyboardFocus", "return (" + EXPR + ")");
const modes = {Exclusive: 1, None: 0, OnDemand: 2};
for (const expanded of [false, true]) for (const oskWanted of [false, true]) {
    if (focus(expanded, {oskWanted}, modes) !== (expanded ? modes.Exclusive : modes.None))
        throw Error("dock keyboard ownership must follow expansion independently of OSK");
}
""".replace("EXPR", json.dumps(focus.group(1)))
        try:
            result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=5)
            if result.returncode:
                problems.append("Dock keyboard ownership does not follow expansion independently of OSK")
        except (OSError, subprocess.TimeoutExpired):
            problems.append("Dock keyboard ownership verification could not run")

    # Keymap.qml: must use OnDemand focus and HyprlandFocusGrab
    km = read(f"{QS}/Keymap.qml", ref)
    if not km:
        problems.append("Keymap.qml missing")
    else:
        if re.search(r"WlrLayershell\.keyboardFocus:\s*open\s*\?\s*WlrKeyboardFocus\.Exclusive", km):
            problems.append("Keymap.qml grabs Exclusive focus instead of OnDemand")
        if "HyprlandFocusGrab" not in km:
            problems.append("Keymap.qml missing HyprlandFocusGrab for outside-click dismissal")
        if re.search(r"HyprlandFocusGrab\s*\{[^}]*visible:", km):
            problems.append("Keymap.qml HyprlandFocusGrab uses non-existent property 'visible'")

    # AppMenu.qml: must use OnDemand focus
    am = read(f"{QS}/AppMenu.qml", ref)
    if not am:
        problems.append("AppMenu.qml missing")
    else:
        if re.search(r"WlrLayershell\.keyboardFocus:\s*openMenu\s*>=\s*0\s*\?\s*WlrKeyboardFocus\.Exclusive", am):
            problems.append("AppMenu.qml grabs Exclusive focus instead of OnDemand")

    problems.extend(drawer_global_escape_contract(ref))
    return problems


def drawer_global_escape_contract(ref=None):
    """Execute generated policy and bypass realization on stub handles, never the live compositor."""
    source = read("usr/share/mios/hyprland/hyprland.lua", ref)
    canonical = lambda key: re.sub(r"\s+", "", key).lower()
    try:
        data = tomllib.loads(read("usr/share/mios/mios.toml", ref))
        global_config = data["keybindings"]["global"]
        drawer = next(row for row in global_config["bindings"] if row.get("id") == "app-drawer")
        chords = [drawer["chord"], drawer["bypass"]]
        command = global_config["action_argv"][drawer["action"]]
        start = source.index("-- MiOS global policy:")
        stop = source.index('mios_section("virtual-keyboard"', start)
        clone_start = source.index("local extra={}", stop)
        clone_end = source.index("for _,v in ipairs(extra)", clone_start)
        clone_end = source.index("\n", clone_end)
    except (KeyError, ValueError, StopIteration, tomllib.TOMLDecodeError):
        return ["Generated drawer global escape policy or SSOT binding missing"]
    realized = None
    escapes = {}
    escape_lua = []
    decoder = json.JSONDecoder()
    for line in source.splitlines():
        line = line.strip()
        try:
            if line.startswith("hl.bind("):
                key, end = decoder.raw_decode(line[len("hl.bind("):])
                tail = line[len("hl.bind(") + end:].lstrip(", ")
                if not tail.startswith("hl.dsp.exec_cmd("):
                    continue
                text, _ = decoder.raw_decode(tail[len("hl.dsp.exec_cmd("):])
                if canonical(key) == canonical(chords[0]) and shlex.split(text) == command:
                    realized = key
            elif line.startswith("escapes["):
                key, end = decoder.raw_decode(line[len("escapes["):])
                value, _ = decoder.raw_decode(line[len("escapes[") + end:].lstrip("]= "))
                escapes[canonical(key)] = canonical(value)
                escape_lua.append(line)
        except ValueError:
            continue
    if not realized or escapes.get(canonical(chords[0])) != canonical(chords[1]):
        return ["Generated drawer chord and bypass must both realize the SSOT drawer command"]
    lua = shutil.which("luajit") or shutil.which("lua")
    if not lua:
        return ["Lua interpreter unavailable for generated drawer escape verification"]
    script = """
local recorded={}
local function key_id(key) return key:gsub('%s',''):lower() end
local hl={bind=function(key,dispatcher,flags)
 recorded[key_id(key)]={flags=flags or {},dispatcher=dispatcher}; return {}
end}
""" + source[start:stop] + """
local supplied={release=true}
hl.bind(MAINKEY,{},supplied)
local canonical=mios_input_canonical
local escapes={}
MAPS
CLONE
for _,key in ipairs(CHORDS) do
 local bound=recorded[key_id(key)]
 assert(bound and bound.flags.dont_inhibit and bound.flags.submap_universal,'drawer escape is inhibited: '..key)
end
assert(recorded[key_id(MAINKEY)].flags.release==true,'caller binding flags were lost')
assert(recorded[key_id(MAINKEY)].dispatcher==recorded[key_id(BYPASS)].dispatcher,'bypass changed drawer action')
assert(supplied.dont_inhibit==nil and supplied.submap_universal==nil,'caller flags were mutated')
hl.bind('ALT+F2',{})
assert(not recorded['alt+f2'].flags.dont_inhibit and not recorded['alt+f2'].flags.submap_universal,'ordinary bind became an escape')
"""
    script = (script.replace("MAINKEY", json.dumps(realized)).replace("BYPASS", json.dumps(chords[1]))
              .replace("CHORDS", "{" + ",".join(json.dumps(chord) for chord in chords) + "}")
              .replace("MAPS", "\n".join(escape_lua)).replace("CLONE", source[clone_start:clone_end]))
    try:
        result = subprocess.run([lua, "-"], input=script, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return ["Generated drawer escape verification could not run"]
    return [] if result.returncode == 0 else ["Generated drawer escape flags do not preserve uninhibited universal access"]


# ---- Defect 1: Full Liquid Glass Surfaces & Compositor Blur ----------------------------------------------------------
# The operator's unified-glass rule: ONE QML glass component (GlassSurface) for every surface, no per-surface variants.
# GlassSurface owns a body's shape and colour -- radius Glass.corner(min(w, h)), fill Glass.fill, no outline (the rim
# is hyprglass' fresnel). A body that sets any of these itself (or an Item opacity over them) is a per-surface variant.
GLASS_OWNED = ("radius", "power", "hairline", "borderColor", "border", "specular", "specularColor", "rimColor",
               "opacity")
# A colour or opacity written into the QML instead of read from the design tokens.
HARD_CODED = re.compile(r"#[0-9a-fA-F]{3,8}\b|\bQt\.(?:rgba|rgb|hsla|hsva|hsl|hsv|lighter|darker|tint|alpha)\s*\("
                        r"|\"[A-Za-z]+\"|(?<![\w.])\d*\.?\d+(?![\w.])")
# Objects that set another object's properties later: a state's PropertyChanges, a transition's PropertyAction or
# animation, a Binding. Aimed at the body (`target: box`, Qt 6's `box.radius: ...`, or `X on radius` inside it) they
# are a per-surface variant by another route.
SETTER = re.compile(r"(?:\w+\.)?(?:PropertyChanges|PropertyAction|Binding|\w*Animation)")
# Types that paint a layer of their own (a tint, a sheet, an image), as opposed to laying out or showing content.
PAINTERS = ("Rectangle", "Canvas", "Image", "BorderImage", "AnimatedImage", "ShaderEffect", "MultiEffect", "Shape",
            "RectangularShadow")
TEXT = ("Text", "TextInput", "TextEdit", "TextField", "TextArea", "Label")
COLOUR = re.compile(r"(?i)colou?r$")


def qml_code(text: str) -> str:
    """`text` with comments blanked and string contents replaced by `x` (same length), for structural scanning."""
    out, i, n = list(text), 0, len(text)
    while i < n:
        if text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            out[i:j] = " " * (j - i)
            i = j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out[i:j] = ["\n" if c == "\n" else " " for c in text[i:j]]
            i = j
        elif text[i] in "\"'`":
            j = i + 1
            while j < n and text[j] != text[i]:
                j += 2 if text[j] == "\\" else 1
            out[i + 1:min(j, n)] = "x" * (min(j, n) - i - 1)
            i = j + 1
        else:
            i += 1
    return "".join(out)


def qml_statements(text: str, code: str, opener: int) -> list[tuple[str, str, int]]:
    """The object at `opener` ('{')'s own statements as (name, expression, child opener or -1): its own property
    assignments and child objects, never those of a nested object or JS block. A child object's name is its type.
    A grouped property block (`anchors { fill: parent }`) is flattened into its dotted statements (`anchors.fill`)."""
    stmts, cur, depth = [], [], 0
    for i in range(opener + 1, len(code)):
        c = code[i]
        if c == "{":
            if depth == 0:
                cur.append(i)
            depth += 1
        elif c == "}":
            if depth == 0:
                break
            depth -= 1
        elif depth == 0:
            if c in "\n;":
                stmts.append(cur)
                cur = []
            else:
                cur.append(i)
    stmts.append(cur)
    out = []
    for idx in stmts:
        # the source text minus comments (blanked in `code`), string contents kept
        src = "".join(text[i] for i in idx if not (code[i] == " " and text[i] != " ")).strip()
        child = idx[-1] if idx and code[idx[-1]] == "{" else -1
        if child >= 0:
            m = re.match(r"(?:[\w.]+\s*:\s*)?([A-Z][\w.]*)\s*\{$", src)
            group = re.match(r"([a-z][\w.]*)\s*\{$", src)
            if m:
                out.append((m.group(1), "", child))
            elif group:
                out += [(n if op >= 0 else f"{group.group(1)}.{n}", e, op)
                        for n, e, op in qml_statements(text, code, child)]
            continue
        m = re.match(r"(?:(?:readonly|required|default)\s+)*(?:property\s+[\w<>.]+\s+)?([A-Za-z_][\w.]*)\s*:\s*(.*)$", src)
        if m:
            out.append((m.group(1), m.group(2).strip(), -1))
    return out


def qml_objects(code: str) -> list[tuple[str, str, int, int]]:
    """Every object in `code` (from qml_code) as (type, its `on` property or "", opener, enclosing object's opener or
    -1), wherever it sits: in a `states:` list, a transition, a StateGroup, a child object or a one-line form."""
    starts = {m.end() - 1: (m.group(1), m.group(2) or "")
              for m in re.finditer(r"(?<![\w.])([A-Z][\w.]*)(?:\s+on\s+([\w.]+))?\s*\{", code)}
    out, stack = [], []
    for i, c in enumerate(code):
        if c == "{":
            if i in starts:
                out.append((*starts[i], i, next((o for o in reversed(stack) if o in starts), -1)))
            stack.append(i)
        elif c == "}" and stack:
            stack.pop()
    return out


def glass_setter_props(s: str, code: str, opener: int, ident: str) -> list[tuple[str, str]]:
    """(setter type, property) for each property of the object at `opener` (id `ident`) that a PropertyChanges,
    PropertyAction, Binding or animation anywhere in the file sets: `target: <id>` with the property named, Qt 6's
    `<id>.radius: ...`, or `X on radius` inside the object."""
    out = []
    aimed_at = re.compile(r"\b%s\b" % re.escape(ident)) if ident else None
    for kind, on, op, parent in qml_objects(code):
        if not SETTER.fullmatch(kind):
            continue
        props = [on] if on and parent == opener else []
        own = [(n, e) for n, e, child in qml_statements(s, code, op) if child < 0]
        aimed = bool(aimed_at) and not on and any(n in ("target", "targets") and aimed_at.search(e) for n, e in own)
        for n, e in own:
            if ident and n.startswith(ident + "."):  # Qt 6: PropertyChanges { box.radius: ... }
                props.append(n[len(ident) + 1:])
            elif aimed and n in ("property", "properties"):
                props += re.findall(r"[\w.]+", e)
            elif aimed and kind.endswith("PropertyChanges") and n not in ("target", "explicit", "restoreEntryValues"):
                props.append(n)
        out += [(kind, p) for p in props]
    return out


def glass_layer_errors(s: str, code: str, opener: int, direct: bool, body: int = -1) -> list[str]:
    """Children of `opener` that paint a layer of their own on the glass: anything but a GlassSurface that paints (a
    painter type, or a colour or Gradient on anything but text) and is shaped (any radius) or fills its parent, also
    behind a non-painting child that fills it (a wrapper Item, the content Column). A GlassSurface other than `body`
    that fills its parent is a second glass stacked on the one surface (a row or chip highlight is sized to itself).
    Such a layer's hard-coded colour is a defect too, as is any hard-coded colour on `opener`'s own children when
    `direct`."""
    problems = []
    for kind, _, op in qml_statements(s, code, opener):
        if op < 0 or op == body:
            continue
        stmts = qml_statements(s, code, op)
        own = {n: e for n, e, child in stmts if child < 0}
        base = kind.split(".")[-1]
        colours = [(n, e) for n, e in own.items() if COLOUR.search(n)]
        paints = base in PAINTERS or base not in TEXT and bool(
            colours or any(child >= 0 and k.split(".")[-1] == "Gradient" for k, _, child in stmts))
        shaped = any(re.search(r"(?i)radius$", n) for n in own)
        fills = "anchors.fill" in own or bool(re.search(r"\.width\b", own.get("width", ""))
                                               and re.search(r"\.height\b", own.get("height", "")))
        layer = base != "GlassSurface" and paints and (shaped or fills)
        stacked = base == "GlassSurface" and fills
        if layer:
            problems.append(f"Search.qml paints a {kind} of its own on the shared glass")
        if stacked:
            problems.append(f"Search.qml stacks another {kind} on the shared glass")
        if layer or stacked or direct:
            problems += [f"Search.qml hard-codes a colour on the shared glass: {kind} {n}: {e}"
                         for n, e in colours if HARD_CODED.search(e)]
        if base != "GlassSurface" and not paints and fills:
            problems += glass_layer_errors(s, code, op, False)
    return problems


def search_glass_errors(s: str) -> list[str]:
    """Search's body is the one shared GlassSurface with its defaults: the GlassSurface filling the window, no
    radius/outline/opacity of its own, its fill the shared Glass.fill, nothing hard-coded, nothing assigned later (by
    script, Binding, state, transition or animation), no tint layer of its own on it or beside it in the window, and
    GlassSurface itself the shared component, not a local one."""
    code = qml_code(s)
    root = re.search(r"\b[A-Z][\w.]*\s*\{", code)
    if not root:
        return ["Search.qml has no root object"]
    top = qml_statements(s, code, root.end() - 1)
    body = next(((kind, op) for kind, _, op in top
                 if op >= 0 and ("anchors.fill", "parent", -1) in qml_statements(s, code, op)), None)
    if not body:
        return ["Search.qml has no window-filling glass body"]
    kind, opener = body
    problems = [f"Search.qml shadows the shared {m.group(1)} with a local component"
                for m in re.finditer(r"\bcomponent\s+(GlassSurface|Glass)\s*:", code)]
    if kind != "GlassSurface":
        problems.append(f"Search.qml body is a {kind}, not the shared GlassSurface")
    own = qml_statements(s, code, opener)
    ident = next((expr for name, expr, _ in own if name == "id"), "")
    for name, expr, _ in own:
        prop = name.split(".")[0]
        if prop in GLASS_OWNED:
            problems.append(f"Search.qml overrides the shared glass {name}: {expr}")
        if prop == "color" and expr != "Glass.fill":
            problems.append(f"Search.qml replaces the shared Glass.fill with {expr}")
        if prop in GLASS_OWNED + ("color",) and HARD_CODED.search(expr):
            problems.append(f"Search.qml hard-codes a glass colour/opacity: {name}: {expr}")
    # Glass.fill is GlassSurface's default, but the body states it: test-shell-hud-contract reads a surface's colour
    # from its first 700 characters, where an unstated fill leaves the Column's TextInput colour to be read instead.
    if kind == "GlassSurface" and not any(name == "color" and child < 0 for name, _, child in own):
        problems.append("Search.qml body leaves its Glass.fill unstated "
                        "(test-shell-hud-contract would read the TextInput's colour as the body's)")
    if ident and re.search(r"\b%s\.(?:%s|color)\b(?:\.\w+)?\s*=(?!=)" % (re.escape(ident), "|".join(GLASS_OWNED)), code):
        problems.append(f"Search.qml reassigns the shared glass of {ident} at runtime")
    if ident and re.search(r"\bBinding\s*\{[^{}]*\btarget:\s*%s\b" % re.escape(ident), code):
        problems.append(f"Search.qml rebinds the shared glass of {ident}")
    problems += [f"Search.qml overrides the shared glass of {ident or 'its body'} in a {setter}: {prop}"
                 for setter, prop in glass_setter_props(s, code, opener, ident)
                 if prop.split(".")[0] in GLASS_OWNED + ("color",)]
    # an alias hands the body's glass to whoever instantiates Search (`Search { glassRadius: 14 }` in shell.qml)
    if ident:
        problems += [f"Search.qml exposes the shared glass of {ident} for override: property alias {m.group(1)}: "
                     f"{ident}.{m.group(2)}"
                     for m in re.finditer(r"\bproperty\s+alias\s+(\w+)\s*:\s*%s\.((?:%s|color)\b[\w.]*)"
                                          % (re.escape(ident), "|".join(GLASS_OWNED)), code)]
    problems += glass_layer_errors(s, code, opener, True)
    problems += glass_layer_errors(s, code, root.end() - 1, False, opener)
    # the window itself stays transparent: a colour there tints every pixel behind the glass, set in place, at
    # runtime or later by a state, transition or animation
    window = next((expr for name, expr, child in top if name == "id" and child < 0), "")
    problems += [f"Search.qml tints its window behind the shared glass: color: {expr}"
                 for name, expr, child in top if name == "color" and child < 0 and expr != '"transparent"']
    if window and re.search(r"\b%s\.color\b(?:\.\w+)?\s*=(?!=)" % re.escape(window), code):
        problems.append("Search.qml tints its window behind the shared glass at runtime")
    problems += [f"Search.qml tints its window behind the shared glass in a {setter}: {prop}"
                 for setter, prop in glass_setter_props(s, code, root.end() - 1, window)
                 if prop.split(".")[0] == "color"]
    return problems


def contract_defect1_glass_styling_and_compositor_blur(ref: str = None) -> list[str]:
    problems = []
    # Search.qml: the one shared GlassSurface with its defaults (no per-surface radius, fill, outline or literal)
    s = read(f"{QS}/Search.qml", ref)
    if not s:
        problems.append("Search.qml missing")
    else:
        problems += search_glass_errors(s)

    # GlassSurface.qml: must have centralized concentric specular highlight rim
    gs = read(f"{QS}/GlassSurface.qml", ref)
    if not gs:
        problems.append("GlassSurface.qml missing")
    else:
        if "innerSpecular" not in gs and "specular" not in gs.lower():
            problems.append("GlassSurface.qml missing centralized concentric specular highlight rim")
        # the defaults Search's body takes are the design language's shared fill and corner rule
        if not re.search(r"^\s*property\s+color\s+color:\s*Glass\.fill\s*$", gs, re.M):
            problems.append("GlassSurface.qml default fill is not the shared Glass.fill")
        if not re.search(r"^\s*property\s+real\s+radius:\s*Glass\.corner\(Math\.min\(width,\s*height\)\)\s*$", gs, re.M):
            problems.append("GlassSurface.qml default radius is not the shared Glass.corner rule")

    # Hyprland effects: must define blur and ignorealpha for quickshell and mios-* layer surfaces
    fx = read("usr/share/mios/hypr/hyprland-effects.conf", ref)
    if not fx:
        problems.append("hyprland-effects.conf missing")
    else:
        if "layerrule = blur, quickshell" not in fx:
            problems.append("hyprland-effects.conf missing blur rule for quickshell")
        if "mios-" not in fx and "^(mios-.*)$" not in fx:
            problems.append("hyprland-effects.conf missing blur rule for mios-* layer surfaces (e.g. mios-search, mios-dock)")
        if "xray 0" not in fx:
            problems.append("hyprland-effects.conf missing xray 0 rule for glass opacity clarity")

    return problems


# ---- Defect 2: Phosh OSK Virtual Keyboard Keys & Typing -------------------------------------------------------------
def contract_defect2_virtual_keyboard_osk(ref: str = None) -> list[str]:
    problems = []
    vk = read(f"{QS}/VirtualKeyboard.qml", ref)
    if not vk:
        problems.append("VirtualKeyboard.qml does not exist")
    else:
        # Check for Phosh-styled terminal/navigation header keys
        for key in ["Esc", "Tab", "Ctrl", "Alt", "Super", "←", "↑", "↓", "→", "|", "/", "-"]:
            if f'text: "{key}"' not in vk and f'"{key}"' not in vk:
                problems.append(f"VirtualKeyboard.qml missing header key '{key}'")
        # Check for dispatch logic: direct targetInput text insertion + sendshortcut fallback
        if "dispatchChar" not in vk or "sendHyprShortcut" not in vk:
            problems.append("VirtualKeyboard.qml missing input dispatching functions")
        if "targetInput.text" not in vk:
            problems.append("VirtualKeyboard.qml does not insert text directly into targetInput")
        if "sendshortcut" not in vk:
            problems.append("VirtualKeyboard.qml does not dispatch sendshortcut via Hyprland IPC")

    # DockExpanded.qml: must embed VirtualKeyboard inside the 220px slot
    de = read(f"{QS}/DockExpanded.qml", ref)
    if not de:
        problems.append("DockExpanded.qml missing")
    else:
        if "VirtualKeyboard" not in de:
            problems.append("DockExpanded.qml does not embed VirtualKeyboard")
        if "oskShown" not in de:
            problems.append("DockExpanded.qml does not bind VirtualKeyboard visibility to oskShown")

    # Sidebar.qml: must pass safe numeric arguments to mios-gen dock osk and issue D-Bus SetVisible
    sb = read(f"{QS}/Sidebar.qml", ref)
    if not sb:
        problems.append("Sidebar.qml missing")
    else:
        if 'command: [DockConfig.backend, "dock", "osk"' in sb:
            if '"--bottom"' not in sb or '"--width"' not in sb or '"--height"' not in sb:
                problems.append("Sidebar.qml passes invalid or missing arguments to mios-gen dock osk")
        if "sm.puri.OSK0" not in sb:
            problems.append("Sidebar.qml does not interface with sm.puri.OSK0 D-Bus service for stevia unfolding")

    return problems


# ---- Defect 3: Maximize Workarea Bounds & HUD Pill Deconfliction -----------------------------------------------------
def contract_defect3_maximize_workarea_and_deconfliction(ref: str = None) -> list[str]:
    problems = []
    pw = read(f"{QS}/PanelWindow.qml", ref)
    if not pw:
        problems.append("PanelWindow.qml missing")
    else:
        # exclusiveZone must incorporate Glass.gap so top reservation is 44px
        if "zoneFor(overFullscreen, rowHeight, Glass.gap, ui)" not in pw:
            problems.append("PanelWindow.qml exclusiveZone does not pass Glass.gap into zoneFor")

    # EdgeReveal.qml: must check true fullscreen (fullscreen >= 2), NEVER maximized (fullscreen == 1)
    er = read(f"{QS}/EdgeReveal.qml", ref)
    if not er:
        problems.append("EdgeReveal.qml missing")
    else:
        if "o.fullscreen >= 2" not in er:
            problems.append("EdgeReveal.qml treats maximized windows as fullscreen (breaks topbar exclusiveZone under maximized windows)")

    # AppMenu.qml: pills must be clipped before the status island to prevent overlap
    am = read(f"{QS}/AppMenu.qml", ref)
    if not am:
        problems.append("AppMenu.qml missing")
    else:
        if "island.x - rowLeft - Glass.gap" not in am and "island.x - " not in am:
            problems.append("AppMenu.qml does not deconflict width with status island (pills can overlap)")

    return problems


# ---- Defect 4: Rounded Corner UI Highlights (Zero Square Highlights) ------------------------------------------------
def contract_defect4_rounded_ui_highlights(ref: str = None) -> list[str]:
    problems = []
    # Glass.qml: must expose canonical radius, innerRadius, and pillRadius
    g = read(f"{QS}/Glass.qml", ref)
    if not g:
        problems.append("Glass.qml missing")
    else:
        if "readonly property real radius:" not in g:
            problems.append("Glass.qml missing convenience property 'radius'")
        if "readonly property real innerRadius:" not in g:
            problems.append("Glass.qml missing convenience property 'innerRadius'")
        if "readonly property real pillRadius:" not in g:
            problems.append("Glass.qml missing convenience property 'pillRadius'")

    # DockPreview.qml: bottom title scrim must use GlassSurface, not a plain sharp Rectangle
    dp = read(f"{QS}/DockPreview.qml", ref)
    if not dp:
        problems.append("DockPreview.qml missing")
    else:
        if re.search(r"Rectangle\s*\{[^}]*anchors\.bottom:\s*parent\.bottom[^}]*color:\s*theme\.withAlpha", dp):
            problems.append("DockPreview.qml uses a plain sharp Rectangle scrim at the card bottom")
        if "radius: Glass.cornerFor(height)" not in dp and "radius: Glass." not in dp:
            problems.append("DockPreview.qml title scrim missing rounded corner radius")

    # Sidebar.qml: app and minimized tiles must have rounded corners
    sb = read(f"{QS}/Sidebar.qml", ref)
    if not sb:
        problems.append("Sidebar.qml missing")
    else:
        if "radius: Glass.cornerFor(height)" not in sb and "Glass.corner(" not in sb:
            problems.append("Sidebar.qml tiles missing rounded corner radius")

    return problems


# ---- Defect 5: Dock App Launch Origin Animation ---------------------------------------------------------------------
def contract_defect5_launch_origin_animation(ref: str = None) -> list[str]:
    problems = []
    sb = read(f"{QS}/Sidebar.qml", ref)
    if not sb:
        problems.append("Sidebar.qml missing")
    else:
        if "return [0, 0, 48, 48]" in sb:
            problems.append("Sidebar.qml:rectForEntry falls back to top-left [0, 0, 48, 48] (causes top-left launch animation)")
        if "NEVER top-left" not in sb and "root.monX + Math.round" not in sb:
            problems.append("Sidebar.qml:rectForEntry missing bottom-center dock fallback coordinates")

    al = read(f"{QS}/AppLaunch.qml", ref)
    if not al:
        problems.append("AppLaunch.qml missing")
    else:
        if "[936, 1016, 48, 48]" not in al and "DockLamp.recordLaunch" not in al:
            problems.append("AppLaunch.qml missing safe dock fallback coordinates for DockLamp.recordLaunch")

    return problems


# ---- GNOME & Phosh App Launching & DBusActivatable Bypass ------------------------------------------------------------
NATIVE_DISPATCH = "tools/native/mios-gen/src/app_preflight.rs"


def rust_fn_body(src: str, name: str) -> str:
    """The brace-balanced body of `fn name(...) ... { ... }` in Rust source, or ""."""
    m = re.search(r"\bfn %s\s*\(" % re.escape(name), src)
    if not m:
        return ""
    start = src.find("{", m.end())
    depth = 0
    for i in range(start, len(src)) if start >= 0 else ():
        depth += {"{": 1, "}": -1}.get(src[i], 0)
        if depth == 0:
            return src[start:i + 1]
    return ""


def native_dispatch_bypasses_dbus_activation(ref: str = None) -> bool:
    """`mios-gen desktop-dispatch` (dispatch_exec) runs an entry's own Exec even when it is DBusActivatable=true; D-Bus
    activation is reached only for an entry with no Exec (or GIO's one-instance-per-resource case), never because the
    entry is DBusActivatable -- the Calls bypass the shell launcher carried before it delegated to the native dispatcher."""
    body = rust_fn_body(read(NATIVE_DISPATCH, ref), "dispatch_exec")
    guard = re.search(r'if\s+!fields\.get\("Exec"\)\.is_some_and\(\|s\|\s*!s\.is_empty\(\)\)[^{]*\{\s*return\s+dispatch_activation\(',
                      body)
    return bool(guard) and "DBusActivatable" not in body and body.count("dispatch_activation(") == 1 \
        and "Command::new(&argv[0])" in body


def contract_gnome_phosh_app_launching(ref: str = None) -> list[str]:
    problems = []
    launch_sh = read("usr/libexec/mios/mios-desktop-launch", ref)
    if not launch_sh:
        problems.append("mios-desktop-launch missing")
    else:
        if "XDG_CURRENT_DESKTOP" not in launch_sh or "GNOME:Phosh" not in launch_sh:
            problems.append("mios-desktop-launch does not set XDG_CURRENT_DESKTOP containing GNOME:Phosh")
        # The bypass lives in the launcher itself, or in the native dispatcher the launcher execs.
        delegates = re.search(r"^exec mios-gen desktop-dispatch\b", launch_sh, re.M)
        if "DBusActivatable" not in launch_sh and not (delegates and native_dispatch_bypasses_dbus_activation(ref)):
            problems.append("mios-desktop-launch does not bypass DBusActivatable=true headless daemon invocation (Calls fails to open GUI window)")

    sb = read(f"{QS}/Sidebar.qml", ref)
    if not sb:
        problems.append("Sidebar.qml missing")
    else:
        if "shellState.drawerOpen = false" not in sb:
            problems.append("Sidebar.qml does not close drawer on app launch")

    return problems


class TestGuiShellUnify(unittest.TestCase):
    """Automated test suite asserting genuine implementation and two-sided verification for GUI Shell unification."""

    @classmethod
    def setUpClass(cls):
        result = subprocess.run(["git", "-C", ROOT, "cat-file", "-e", NEG_REF + "^{commit}"], capture_output=True)
        if result.returncode:
            raise RuntimeError("Negative GUI reference must resolve to a commit: " + NEG_REF)

    def test_regression_osk_cannot_disable_expanded_drawer_typing(self):
        original = read
        sidebar = original(f"{QS}/Sidebar.qml")
        mutated = re.sub(r"(WlrLayershell\.keyboardFocus:)\s*[^\n]+",
                         r"\1 (expanded && !root.oskWanted) ? WlrKeyboardFocus.OnDemand : WlrKeyboardFocus.None", sidebar, count=1)
        self.assertNotEqual(sidebar, mutated)
        def fixture(path, ref=None):
            return mutated if ref is None and path == f"{QS}/Sidebar.qml" else original(path, ref)
        with patch.dict(globals(), {"read": fixture}):
            self.assertIn("Dock keyboard ownership does not follow expansion independently of OSK",
                          contract_defect0_layer_keyboard_focus())

    def test_regression_primary_drawer_chord_must_remain_a_global_escape(self):
        original = read
        path = "usr/share/mios/hyprland/hyprland.lua"
        source = original(path)
        mutated, count = re.subn(r"local mios_input_always = [^\n]+", "local mios_input_always = {}", source, count=1)
        self.assertEqual(count, 1)
        with patch.dict(globals(), {"read": lambda rel, ref=None: mutated if rel == path and ref is None else original(rel, ref)}):
            self.assertIn("Generated drawer escape flags do not preserve uninhibited universal access",
                          drawer_global_escape_contract())

    def test_regression_bypass_drawer_chord_must_remain_a_global_escape(self):
        original = read
        path = "usr/share/mios/hyprland/hyprland.lua"
        source = original(path)
        begin = source.index("local extra={}")
        before, clone = source[:begin], source[begin:]
        mutated = before + clone.replace("dont_inhibit=true,submap_universal=true", "dont_inhibit=false,submap_universal=true", 1)
        self.assertNotEqual(source, mutated)
        with patch.dict(globals(), {"read": lambda rel, ref=None: mutated if rel == path and ref is None else original(rel, ref)}):
            self.assertIn("Generated drawer escape flags do not preserve uninhibited universal access",
                          drawer_global_escape_contract())

    def test_regression_native_dispatch_must_not_activate_dbus_entries_that_have_an_exec(self):
        original = read
        source = original(NATIVE_DISPATCH)
        guard = '    if !fields.get("Exec").is_some_and(|s| !s.is_empty())\n        || multiple_single_resources'
        self.assertEqual(source.count(guard), 1)
        mutated = source.replace(guard, '    if !fields.get("Exec").is_some_and(|s| !s.is_empty())\n'
                                 '        || fields.get("DBusActivatable").is_some_and(|s| s == "true")\n'
                                 '        || multiple_single_resources', 1)
        with patch.dict(globals(), {"read": lambda rel, ref=None: mutated if rel == NATIVE_DISPATCH and ref is None else original(rel, ref)}):
            self.assertIn("mios-desktop-launch does not bypass DBusActivatable=true headless daemon invocation (Calls fails to open GUI window)",
                          contract_gnome_phosh_app_launching())

    def test_regression_launcher_must_keep_the_bypassing_dispatcher(self):
        original = read
        path = "usr/libexec/mios/mios-desktop-launch"
        source = original(path)
        mutated, count = re.subn(r"^exec mios-gen desktop-dispatch .*$", 'exec gtk-launch "$@"', source, count=1, flags=re.M)
        self.assertEqual(count, 1)
        with patch.dict(globals(), {"read": lambda rel, ref=None: mutated if rel == path and ref is None else original(rel, ref)}):
            self.assertIn("mios-desktop-launch does not bypass DBusActivatable=true headless daemon invocation (Calls fails to open GUI window)",
                          contract_gnome_phosh_app_launching())

    def test_regression_search_glass_must_stay_the_shared_surface(self):
        """Planted Search glass defects (each a per-surface variant of the one glass) are each caught by name."""
        original = read
        path = f"{QS}/Search.qml"
        source = original(path)
        # the real file first, so a defect in it (an unstated Glass.fill included) is reported by name, not as a
        # missing anchor below
        self.assertEqual(search_glass_errors(source), [])
        head = "    GlassSurface {\n        id: box\n        anchors.fill: parent\n"
        # stated although it is GlassSurface's default: test-shell-hud-contract reads the body's colour from its first
        # 700 characters, where an unstated fill leaves the Column's TextInput colour to be read instead
        fill = "        color: Glass.fill\n"
        body_end = "        }\n    }\n    IpcHandler {"  # the Column's and the body's closing braces
        ipc = "    IpcHandler {"
        window = '    color: "transparent"\n'
        for anchor in (head, fill, body_end, ipc, window):
            self.assertEqual(source.count(anchor), 1, f"Search.qml no longer has the planted controls' anchor {anchor!r}")
        wide = "searchWindow.theme.withAlpha(Glass.fillHex, %s)"
        hard = "Search.qml hard-codes a glass colour/opacity: "
        later = "Search.qml overrides the shared glass of box in a "
        layer = "Search.qml paints a Rectangle of its own on the shared glass"
        tint = "Search.qml hard-codes a colour on the shared glass: Rectangle color: "
        token = "searchWindow.theme.withAlpha(searchWindow.theme.bg, searchWindow.theme.panelOpacity)"
        mutations = (  # (label, anchor, replacement, the problem(s) it must raise)
            ("per-surface radius literal", head, head + "        radius: 14\n", hard + "radius: 14"),
            ("Glass.radius override", head, head + "        radius: Glass.radius\n",
             "Search.qml overrides the shared glass radius: Glass.radius"),
            ("Rectangle in place of GlassSurface", head, head.replace("GlassSurface", "Rectangle"),
             "Search.qml body is a Rectangle, not the shared GlassSurface"),
            ("hard-coded fill opacity", fill, "        color: %s\n" % (wide % "0.72"),
             hard + "color: " + wide % "0.72"),
            ("hard-coded colour", fill, '        color: "#cc101014"\n', hard + 'color: "#cc101014"'),
            ("hard-coded item opacity", head, head + "        opacity: 0.85\n", hard + "opacity: 0.85"),
            ("panel-opacity fill (f8ca510ad)", fill,
             "        color: %s\n" % (wide % "searchWindow.theme.panelOpacity"),
             "Search.qml replaces the shared Glass.fill with " + wide % "searchWindow.theme.panelOpacity"),
            ("hand-drawn outline (f8ca510ad)", fill,
             fill + "        hairline: true\n        borderColor: searchWindow.theme.outlineColor\n",
             "Search.qml overrides the shared glass borderColor: searchWindow.theme.outlineColor"),
            ("one-line object override", head, "    GlassSurface { id: box; anchors.fill: parent; radius: Glass.radius\n",
             "Search.qml overrides the shared glass radius: Glass.radius"),
            ("runtime reassignment", head, head + "        Component.onCompleted: box.radius = Glass.radius\n",
             "Search.qml reassigns the shared glass of box at runtime"),
            ("no window-filling body", head, head.replace("anchors.fill: parent", "anchors.centerIn: parent"),
             "Search.qml has no window-filling glass body"),
            ("grouped outline", fill, fill + "        border { width: 1; color: searchWindow.theme.outlineColor }\n",
             "Search.qml overrides the shared glass border.color: searchWindow.theme.outlineColor"),
            ("pure-defaults body (fill unstated)", fill, "",
             "Search.qml body leaves its Glass.fill unstated "
             "(test-shell-hud-contract would read the TextInput's colour as the body's)"),
            # set later by a state, a transition or an animation instead of in the body
            ("one-line state in the body", fill,
             fill + '        states: State { name: "open"; when: searchWindow.query.length > 0; '
                    'PropertyChanges { target: box; radius: 14 } }\n', later + "PropertyChanges: radius"),
            ("StateGroup at the root", ipc,
             "    StateGroup {\n        states: State {\n            when: searchWindow.query.length > 0\n"
             "            PropertyChanges {\n                target: box\n                radius: Glass.radius\n"
             "                color: %s\n            }\n        }\n    }\n" % (wide % "searchWindow.theme.panelOpacity") + ipc,
             (later + "PropertyChanges: radius", later + "PropertyChanges: color")),
            ("Qt 6 PropertyChanges in a states list", fill,
             fill + "        states: [State { when: true; PropertyChanges { box.radius: Glass.radius } }]\n",
             later + "PropertyChanges: radius"),
            ("transition PropertyAction", ipc,
             '    transitions: Transition { PropertyAction { target: box; property: "color"; value: Glass.fillHex } }\n'
             + ipc, later + "PropertyAction: color"),
            ("colour animation", ipc,
             '    ColorAnimation { running: true; target: box; properties: "color"; to: "#cc101014" }\n' + ipc,
             later + "ColorAnimation: color"),
            ("animation on the body's radius", fill, fill + "        NumberAnimation on radius { to: Glass.radius }\n",
             later + "NumberAnimation: radius"),
            # a tint layer of its own on the glass
            ("one-line tint child", body_end,
             body_end.replace("    }\n    IpcHandler", '        Rectangle { anchors.fill: parent; radius: 14; color: "#33000000" }\n'
                                                     "    }\n    IpcHandler"), (layer, tint + '"#33000000"')),
            ("square tint after the Column", body_end,
             body_end.replace("    }\n    IpcHandler", '        Rectangle {\n            anchors.fill: parent\n            z: -1\n'
                                                     '            color: "#33000000"\n        }\n    }\n    IpcHandler'),
             (layer, tint + '"#33000000"')),
            ("token tint child", fill,
             fill + "        Rectangle {\n            anchors.fill: parent\n            radius: box.radius\n"
                    "            color: %s\n        }\n" % token, layer),
            ("tint behind a filling Item", body_end,
             body_end.replace("    }\n    IpcHandler", "        Item {\n            anchors.fill: parent\n            Rectangle {\n"
                                                     "                anchors { fill: parent }\n                color: %s\n"
                                                     "            }\n        }\n    }\n    IpcHandler" % token), layer),
            ("hard-coded tint on a nested GlassSurface", fill,
             fill + '        GlassSurface { anchors.fill: parent; color: "#33000000" }\n',
             'Search.qml hard-codes a colour on the shared glass: GlassSurface color: "#33000000"'),
            ("hard-coded chip on the body", fill,
             fill + '        GlassSurface { width: searchWindow.theme.appIcon; height: width; color: "#33000000" }\n',
             'Search.qml hard-codes a colour on the shared glass: GlassSurface color: "#33000000"'),
            ("tint beside the body in the window", ipc, "    Rectangle { anchors.fill: parent; color: %s }\n" % token + ipc,
             layer),
            # a second glass on the one surface: a filling GlassSurface in the body or beside it, token-coloured
            ("filling GlassSurface tint in the body", fill,
             fill + "        GlassSurface {\n            anchors.fill: parent\n"
                    "            color: %s\n        }\n" % token,
             "Search.qml stacks another GlassSurface on the shared glass"),
            ("second GlassSurface beside the body in the window", ipc,
             "    GlassSurface { width: parent.width; height: parent.height; color: %s }\n" % token + ipc,
             "Search.qml stacks another GlassSurface on the shared glass"),
            ("alias handing the body's radius to the caller", window,
             window + "    property alias glassRadius: box.radius\n",
             "Search.qml exposes the shared glass of box for override: property alias glassRadius: box.radius"),
            ("window tint behind the glass", window, "    color: Qt.rgba(0, 0, 0, 0.3)\n",
             "Search.qml tints its window behind the shared glass: color: Qt.rgba(0, 0, 0, 0.3)"),
            ("window tint by a state", ipc,
             "    states: State { when: searchWindow.query.length > 0; "
             "PropertyChanges { target: searchWindow; color: %s } }\n" % token + ipc,
             "Search.qml tints its window behind the shared glass in a PropertyChanges: color"),
            ("window tint at runtime", window, window + "    Component.onCompleted: searchWindow.color = %s\n" % token,
             "Search.qml tints its window behind the shared glass at runtime"),
            ("local GlassSurface component", head,
             '    component GlassSurface: Rectangle { radius: 14; color: "#cc101014" }\n' + head,
             "Search.qml shadows the shared GlassSurface with a local component"),
        )
        for label, anchor, replacement, problems in mutations:
            with self.subTest(label):
                mutated = source.replace(anchor, replacement, 1)
                self.assertNotEqual(mutated, source)
                fixture = {"read": lambda rel, ref=None, m=mutated: m if rel == path and ref is None else original(rel, ref)}
                with patch.dict(globals(), fixture):
                    found = contract_defect1_glass_styling_and_compositor_blur()
                for problem in (problems,) if isinstance(problems, str) else problems:
                    self.assertIn(problem, found)
        # not overrides: a commented-out one, a state on another object's colour or on a property the glass does not
        # own, the body's anchors in grouped form, a chip highlight sized to itself, an alias of the input's text, a
        # state on the window's width; and a child row's own concentric radius is not the body's
        for label, anchor, replacement in (
                ("commented-out override", head, head + "        // radius: 14\n"),
                ("state on another object's colour", fill,
                 fill + "        states: State { when: true; PropertyChanges { target: input; "
                        "color: searchWindow.theme.accent } }\n"),
                ("state on a property the glass does not own", fill,
                 fill + "        states: State { when: true; PropertyChanges { target: box; clip: false } }\n"),
                ("grouped anchors", head, head.replace("anchors.fill: parent", "anchors { fill: parent }")),
                ("chip highlight sized to itself", fill,
                 fill + "        GlassSurface { width: searchWindow.theme.appIcon; height: width; color: "
                        "searchWindow.theme.withAlpha(searchWindow.theme.accent, "
                        "searchWindow.theme.selectionOpacity) }\n"),
                ("alias of the input's text", window, window + "    property alias queryText: input.text\n"),
                ("state on the window's width", ipc,
                 "    states: State { when: true; PropertyChanges { target: searchWindow; "
                 "implicitWidth: searchWindow.theme.searchWidth } }\n" + ipc)):
            with self.subTest(label):
                mutated = source.replace(anchor, replacement, 1)
                self.assertNotEqual(mutated, source)
                self.assertEqual(search_glass_errors(mutated), [])
        self.assertIn("radius: Glass.inner(box.radius, Glass.panelPad)", source)

    def test_regression_glass_surface_defaults_are_the_shared_rule(self):
        """The defaults Search relies on: a GlassSurface whose default fill or radius drifts is caught."""
        original = read
        path = f"{QS}/GlassSurface.qml"
        source = original(path)
        for old, new, problem in (
                ("property color color: Glass.fill", "property color color: Glass.theme.bg",
                 "GlassSurface.qml default fill is not the shared Glass.fill"),
                ("property real radius: Glass.corner(Math.min(width, height))", "property real radius: 12",
                 "GlassSurface.qml default radius is not the shared Glass.corner rule")):
            with self.subTest(problem):
                self.assertEqual(source.count(old), 1)
                mutated = source.replace(old, new, 1)
                fixture = {"read": lambda rel, ref=None, m=mutated: m if rel == path and ref is None else original(rel, ref)}
                with patch.dict(globals(), fixture):
                    self.assertIn(problem, contract_defect1_glass_styling_and_compositor_blur())

    def test_positive_defect0_layer_keyboard_focus(self):
        problems = contract_defect0_layer_keyboard_focus(None)
        self.assertEqual(problems, [], f"Defect 0 contract failed on working tree: {problems}")

    def test_negative_defect0_layer_keyboard_focus(self):
        problems = contract_defect0_layer_keyboard_focus(NEG_REF)
        self.assertTrue(len(problems) > 0, "Defect 0 negative control failed: base revision should have defects")

    def test_positive_defect1_glass_styling_and_compositor_blur(self):
        problems = contract_defect1_glass_styling_and_compositor_blur(None)
        self.assertEqual(problems, [], f"Defect 1 contract failed on working tree: {problems}")

    def test_negative_defect1_glass_styling_and_compositor_blur(self):
        problems = contract_defect1_glass_styling_and_compositor_blur(NEG_REF)
        self.assertTrue(len(problems) > 0, "Defect 1 negative control failed: base revision should have defects")

    def test_positive_defect2_virtual_keyboard_osk(self):
        problems = contract_defect2_virtual_keyboard_osk(None)
        self.assertEqual(problems, [], f"Defect 2 contract failed on working tree: {problems}")

    def test_negative_defect2_virtual_keyboard_osk(self):
        problems = contract_defect2_virtual_keyboard_osk(NEG_REF)
        self.assertTrue(len(problems) > 0, "Defect 2 negative control failed: base revision should have defects")

    def test_positive_defect3_maximize_workarea_and_deconfliction(self):
        problems = contract_defect3_maximize_workarea_and_deconfliction(None)
        self.assertEqual(problems, [], f"Defect 3 contract failed on working tree: {problems}")

    def test_negative_defect3_maximize_workarea_and_deconfliction(self):
        problems = contract_defect3_maximize_workarea_and_deconfliction(NEG_REF)
        self.assertTrue(len(problems) > 0, "Defect 3 negative control failed: base revision should have defects")

    def test_positive_defect4_rounded_ui_highlights(self):
        problems = contract_defect4_rounded_ui_highlights(None)
        self.assertEqual(problems, [], f"Defect 4 contract failed on working tree: {problems}")

    def test_negative_defect4_rounded_ui_highlights(self):
        problems = contract_defect4_rounded_ui_highlights(NEG_REF)
        self.assertTrue(len(problems) > 0, "Defect 4 negative control failed: base revision should have defects")

    def test_positive_defect5_launch_origin_animation(self):
        problems = contract_defect5_launch_origin_animation(None)
        self.assertEqual(problems, [], f"Defect 5 contract failed on working tree: {problems}")

    def test_negative_defect5_launch_origin_animation(self):
        problems = contract_defect5_launch_origin_animation(NEG_REF)
        self.assertTrue(len(problems) > 0, "Defect 5 negative control failed: base revision should have defects")

    def test_positive_gnome_phosh_app_launching(self):
        problems = contract_gnome_phosh_app_launching(None)
        self.assertEqual(problems, [], f"GNOME/Phosh app launching contract failed on working tree: {problems}")

    def test_negative_gnome_phosh_app_launching(self):
        problems = contract_gnome_phosh_app_launching(NEG_REF)
        self.assertTrue(len(problems) > 0, "GNOME/Phosh negative control failed: base revision should have defects")



class TestBrowserDispatchIntegrity(unittest.TestCase):
    """Real launcher scripts against local argv recorders, never a browser/session."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.bin = os.path.join(self.tmp.name, "bin")
        os.makedirs(self.bin)
        self.log = os.path.join(self.tmp.name, "argv.json")
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("MIOS_")}
        self.env.update(PATH=self.bin + ":" + os.environ["PATH"],
                        XDG_DATA_HOME=os.path.join(self.tmp.name, "user"),
                        XDG_DATA_DIRS=os.path.join(self.tmp.name, "vendor"),
                        MIOS_OSCONTROL_EXECUTOR="http://offline.invalid",
                        MIOS_LAUNCH_CONFIRM_SECS="0",
                        FIXTURE_ARGV_LOG=self.log)
        self.make("flatpak", "#!/bin/sh\n[ \"$1\" = list ] && exit 0\nexit 1\n")
        self.make("curl", "#!/bin/sh\nexit 1\n")
        self.make("pgrep", "#!/bin/sh\nexit 0\n")
        self.make("sleep", "#!/bin/sh\nexit 0\n")

    def make(self, name, content):
        path = os.path.join(self.bin, name)
        with open(path, "w") as stream:
            stream.write(content)
        os.chmod(path, 0o755)
        return path

    def test_desktop_exec_full_argv_and_child_failure_propagate(self):
        native = os.environ.get("MIOS_BROWSER_TEST_BIN") or shutil.which("mios-browser")
        self.assertTrue(native, "Build and install the native catalog before browser dispatch tests")
        native = os.path.realpath(native)
        self.assertTrue(os.access(native, os.X_OK), "Native browser artifact is not executable: " + native)
        with open(native, "rb") as artifact:
            self.assertEqual(artifact.read(4), b"\x7fELF", "Browser dispatch must exercise the native artifact")
        os.symlink(native, os.path.join(self.bin, "mios-browser"))
        recorder = self.make("record-argv", "#!" + sys.executable + "\n"
            "import os,sys,json\n"
            "with open(os.environ['FIXTURE_ARGV_LOG'],'w') as f: json.dump(sys.argv[1:],f)\n"
            "raise SystemExit(17)\n")
        appdir = os.path.join(self.env["XDG_DATA_DIRS"], "applications")
        os.makedirs(appdir)
        desktop = os.path.join(appdir, "fixture-browser.desktop")
        with open(desktop, "w") as stream:
            stream.write('[Desktop Entry]\nName=Fixture Browser\nExec="' + recorder
                + '" --branch=stable --command=launch-script.sh --file-forwarding org.example.Browser @@u %u @@\n')
        url = 'https://example.invalid/$(false)?a=one&b=two words'
        result = subprocess.run(["bash", os.path.join(ROOT, "usr/libexec/mios/mios-gui"),
            "fixture-browser", "--new-tab", url], env=self.env, text=True,
            capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 17, result.stderr)
        with open(self.log) as stream:
            self.assertEqual(json.load(stream), ["--branch=stable", "--command=launch-script.sh",
                "--file-forwarding", "org.example.Browser", "--new-tab", "@@u", url, "@@"])
        userdir = os.path.join(self.env["XDG_DATA_HOME"], "applications")
        os.makedirs(userdir)
        with open(os.path.join(userdir, "fixture-browser.desktop"), "w") as stream:
            stream.write("[Desktop Entry]\nHidden=true\nExec=true\n")
        os.unlink(self.log)
        result = subprocess.run(["bash", os.path.join(ROOT, "usr/libexec/mios/mios-gui"),
            "fixture-browser", "--new-tab", url], env=self.env, text=True,
            capture_output=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(os.path.exists(self.log))

    def test_running_browser_or_successful_dispatch_is_not_url_confirmation(self):
        self.make("mios-browser", "#!/bin/sh\nprintf 'Flags: --new-tab\\n'\n")
        self.make("mios-gui", "#!/bin/sh\nexit \"$FIXTURE_LAUNCH_RC\"\n")
        self.make("mios-window-active", "#!/bin/sh\nprintf '{\"summary\":\"presented\"}\\n'\n")
        script = os.path.join(ROOT, "usr/libexec/mios/mios-open-url")
        for code in (0, 3, 7):
            with self.subTest(code=code):
                self.env["FIXTURE_LAUNCH_RC"] = str(code)
                url = 'https://example.invalid/a?quoted="yes"&x=two words'
                result = subprocess.run(["bash", script, url, "fixture-browser"],
                    env=self.env, text=True, capture_output=True, timeout=10)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                if code in (0, 3):
                    data = json.loads(result.stdout)
                    self.assertFalse(data["success"])
                    self.assertFalse(data["navigation_verified"])
                    self.assertEqual(data["url"], url)
                    self.assertEqual(data["error"]["code"], "navigation_unverified")
                self.assertNotIn('"success": true', result.stdout)
                self.assertNotIn("already-running", result.stdout)

    def test_cdp_receipt_requires_created_target_url(self):
        import asyncio
        from unittest.mock import patch
        text = read("usr/libexec/mios/mios-open-url")
        marker = 'if /usr/lib/mios/agents/.venv/bin/python3 - "$_cdp_port" "$URL"'
        start = text.index("import sys\n", text.index(marker))
        end = text.index("\nPYEOF", start)
        namespace = {"__name__": "offline_cdp_fixture"}
        exec(compile(text[start:end], "<offline-cdp>", "exec"), namespace)
        url = "https://example.invalid/observed"
        class Response:
            def __init__(self, data): self.data = data
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return json.dumps(self.data).encode()
        for data, expected in (({"id": "target", "url": url}, True),
                ({"id": "target", "url": "about:blank"}, False),
                ({"url": url}, False), ({}, False)):
            with self.subTest(data=data):
                with patch("urllib.request.urlopen", return_value=Response(data)):
                    self.assertEqual(asyncio.run(namespace["cdp_create_tab"](1, url)), expected)

    def test_recursive_default_handler_is_rejected_before_launch(self):
        self.make("mios-gui", "#!/bin/sh\ntouch \"$FIXTURE_ARGV_LOG\"\nexit 0\n")
        script = os.path.join(ROOT, "usr/libexec/mios/mios-open-url")
        alias = os.path.join(self.bin, "browser-handler")
        os.symlink(script, alias)
        result = subprocess.run(["bash", script, "https://example.invalid/", alias],
            env=self.env, text=True, capture_output=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("recursive browser handler", result.stderr)
        self.assertFalse(os.path.exists(self.log))


if __name__ == "__main__":
    unittest.main()
