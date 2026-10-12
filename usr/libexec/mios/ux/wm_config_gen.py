#!/usr/bin/env python3
# AI-hint: Hyprland and Sway tiling window manager configuration generator from SSOT with hot-reload support
# AI-related: tests/test-ux.py, usr/share/mios/mios.toml, usr/lib/mios/mios_toml.py, automation/65-bake-hyprland.sh, usr/share/mios/hyprland/hyprland.conf
# AI-functions: WmConfigGenEngine, generate_hyprland_conf, generate_sway_config, trigger_wm_reload, edge_geometry, check_fixture, write_fixture, main
"""
MiOS Window Manager (Hyprland & Sway) Configuration Generator.

Projects keybindings, gaps, border widths, animations, and SSOT palette tokens
into native compositor configuration files:
- Hyprland: `usr/share/mios/hyprland/hyprland.conf`
- Sway: `usr/share/mios/sway/config`
- Live reload trigger via `hyprctl reload` or `swaymsg reload`.
- Synchronizes behavior, workspaces, and shortcuts across both compositors.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from typing import Any, Dict, Optional

_TREE = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", ".."))
if os.path.join(_TREE, "usr", "lib", "mios") not in sys.path:
    sys.path.insert(0, os.path.join(_TREE, "usr", "lib", "mios"))
import mios_toml  # noqa: E402 -- required: every geometry value resolves through it

# attribute -> [theme.edge] key; no pixel literal lives in this generator
EDGE_KEYS = {"gaps_inner": "wm_gaps_inner_px", "gaps_outer": "wm_gaps_outer_px", "border_size": "wm_border_px"}
# the root-relative path the image installs -> its renderer (automation/65-bake-hyprland.sh renders both at bake)
GOLDENS = {"usr/share/mios/hyprland/hyprland.conf": "generate_hyprland_conf", "usr/share/mios/sway/config": "generate_sway_config"}

def edge_geometry(data: Dict[str, Any]) -> Dict[str, int]:
    """gaps_inner / gaps_outer / border_size from [theme.edge].wm_*; a missing or non-integer key raises naming it."""
    edge = mios_toml.section(data, "theme.edge")
    out = {}
    for attr, key in EDGE_KEYS.items():
        val = edge.get(key)
        if isinstance(val, bool) or not isinstance(val, int) or val < 0:
            raise ValueError(f"[theme.edge].{key}: {val!r} is not a non-negative integer")
        out[attr] = val
    return out

class WmConfigGenEngine:
    """Generates Hyprland and Sway compositor configurations with SSOT colors and geometry."""

    def __init__(
        self,
        gaps_inner: Optional[int] = None,
        gaps_outer: Optional[int] = None,
        border_size: Optional[int] = None,
        terminal: str = "alacritty",
        launcher: str = "rofi -show drun",
        mock: bool = False,
        dry_run: bool = False,
        data: Optional[Dict[str, Any]] = None,
    ):
        data = mios_toml.load_merged(mios_toml.layer_paths()) if data is None else data
        given = {"gaps_inner": gaps_inner, "gaps_outer": gaps_outer, "border_size": border_size}
        geometry = edge_geometry(data) if None in given.values() else {}
        self.gaps_inner = geometry["gaps_inner"] if gaps_inner is None else gaps_inner
        self.gaps_outer = geometry["gaps_outer"] if gaps_outer is None else gaps_outer
        self.border_size = geometry["border_size"] if border_size is None else border_size
        self.terminal = terminal
        self.launcher = launcher
        self.mock = mock
        self.dry_run = dry_run
        self.palette = mios_toml.colors(data)
        self.data = data

    def generate_hyprland_conf(self) -> str:
        """The hyprland.conf the image ships: geometry from [theme.edge].wm_*, colours from [colors]."""
        hexes = {k: v.lstrip("#") for k, v in self.palette.items()}
        return f"""# AI-hint: Hyprland config the image ships, rendered by wm_config_gen.py; gaps and border from mios.toml [theme.edge].wm_*
monitor=,preferred,auto,1

input {{
    kb_layout = us
    follow_mouse = 1
    sensitivity = 0 # -1.0 - 1.0, 0 means no modification
    touchpad {{
        natural_scroll = true
    }}
}}

general {{
    gaps_in = {self.gaps_inner}
    gaps_out = {self.gaps_outer}
    border_size = {self.border_size}
    col.active_border = rgba({hexes['cursor']}ee) rgba({hexes['accent']}ee) rgba({hexes['success']}ee) 60deg
    col.inactive_border = rgba({hexes['bg']}aa)
    layout = dwindle
    allow_tearing = false
}}

render {{
    direct_scanout = 1
}}

env = GTK_THEME,Adwaita:dark
env = QT_QPA_PLATFORM,wayland;xcb
env = QT_QPA_PLATFORMTHEME,qt6ct
env = XDG_CURRENT_DESKTOP,Hyprland
env = XDG_SESSION_TYPE,wayland
env = XDG_SESSION_DESKTOP,Hyprland

decoration {{
    rounding = 12
    active_opacity = 1.0
    inactive_opacity = 0.93
    fullscreen_opacity = 1.0
    blur {{
        enabled = true
        size = 10
        passes = 4
        new_optimizations = true
        xray = true
        ignore_opacity = true
        noise = 0.010
        contrast = 1.08
        brightness = 0.88
        vibrancy = 0.25
        vibrancy_darkness = 0.08
        popups = true           # frost drop-down menus / context popups too
        popups_ignorealpha = 0.2
    }}
    drop_shadow = true
    shadow_range = 22
    shadow_render_power = 4
    shadow_offset = 0 4
    col.shadow = rgba(0A0A0A99)
    col.shadow_inactive = rgba(0A0A0A44)
    dim_inactive = true
    dim_strength = 0.05
}}

animations {{
    enabled = true
    bezier = liquid,    0.25, 1.30, 0.35, 1.00
    bezier = smoothOut, 0.36, 0.00, 0.66, -0.56
    bezier = smoothIn,  0.25, 1.00, 0.50, 1.00
    bezier = myBezier,  0.05, 0.90, 0.10, 1.05
    animation = windows,     1, 5, myBezier, popin 60%
    animation = windowsIn,   1, 5, myBezier, popin 60%
    animation = windowsOut,  1, 4, smoothOut, popin 80%
    animation = windowsMove,  1, 4, liquid
    animation = border,      1, 10, default
    animation = borderangle, 1, 8, default
    animation = fade,        1, 5, smoothIn
    animation = fadeIn,      1, 5, smoothIn
    animation = fadeOut,     1, 5, smoothOut
    animation = workspaces,  1, 5, liquid, slide
    animation = layers,      1, 4, myBezier, slide
    animation = layersIn,    1, 4, myBezier, slide
    animation = layersOut,   1, 4, smoothOut, slide
}}

layerrule = blur, quickshell
layerrule = ignorealpha 0.2, quickshell
layerrule = blur, rofi
layerrule = ignorealpha 0.5, rofi
layerrule = blur, notifications
layerrule = ignorealpha 0.3, notifications

windowrulev2 = suppressevent maximize, class:.*
windowrulev2 = float, class:^(mios-webshell)$
windowrulev2 = size 1200 800, class:^(mios-webshell)$

windowrulev2 = float, class:^(cockpit)$
windowrulev2 = size 1400 900, class:^(cockpit)$

exec-once = quickshell --config /usr/share/mios/quickshell/Config.qml

exec-once = systemctl --user import-environment WAYLAND_DISPLAY XDG_CURRENT_DESKTOP
exec-once = dbus-update-activation-environment --systemd WAYLAND_DISPLAY XDG_CURRENT_DESKTOP
exec-once = systemctl --user start graphical-session.target

$mainMod = SUPER

bind = $mainMod, Q, killactive,
bind = $mainMod SHIFT, M, exit,
bind = $mainMod, E, exec, mios-webshell
bind = $mainMod, V, togglefloating,
bind = $mainMod, R, exec, rofi -show drun
bind = $mainMod, P, pseudo, # dwindle
bind = $mainMod, J, togglesplit, # dwindle
bind = $mainMod, C, exec, xdg-open http://localhost:9090 # Cockpit -- real, see mios-cockpit-link.container

bind = $mainMod, left, movefocus, l
bind = $mainMod, right, movefocus, r
bind = $mainMod, up, movefocus, u
bind = $mainMod, down, movefocus, d

bind = $mainMod, 1, workspace, 1
bind = $mainMod, 2, workspace, 2
bind = $mainMod, 3, workspace, 3
bind = $mainMod, 4, workspace, 4
bind = $mainMod, 5, workspace, 5

bind = $mainMod SHIFT, 1, movetoworkspace, 1
bind = $mainMod SHIFT, 2, movetoworkspace, 2
bind = $mainMod SHIFT, 3, movetoworkspace, 3
bind = $mainMod SHIFT, 4, movetoworkspace, 4
bind = $mainMod SHIFT, 5, movetoworkspace, 5

# Global MiOS terminal, system and AI shortcuts share the SSH action letters.
source = /usr/share/mios/hyprland/mios-keys.conf
"""

    def generate_sway_config(self) -> str:
        """Generate Sway tiling window manager configuration (sway/config)."""
        p = self.palette
        bg = p["bg"]
        fg = p["fg"]
        accent = p["accent"]
        cursor = p["cursor"]
        muted = p["muted"]
        error = p["error"]

        return f"""# AI-hint: Sway config rendered by wm_config_gen.py; gaps and border from mios.toml [theme.edge].wm_*
# =====================================================================
# MiOS Sway Configuration
# Generated from mios.toml SSOT
# =====================================================================

# Mod Key
set $mod Mod4

# Font Configuration
font pango:{self.data['theme']['font']['family']} {self.data['theme']['font']['size']}

# Gaps & Borders
default_border pixel {self.border_size}
default_floating_border pixel {self.border_size}
gaps inner {self.gaps_inner}
gaps outer {self.gaps_outer}

# Color Classes: <class> <border> <background> <text> <indicator> <child_border>
client.focused          {cursor} {accent} {fg} {cursor} {cursor}
client.focused_inactive {muted} {bg} {fg} {muted} {muted}
client.unfocused        {muted} {bg} {muted} {bg} {bg}
client.urgent           {error} {error} {fg} {error} {error}

# Keybindings
include /usr/share/mios/sway/mios-keys.conf
bindsym $mod+Return exec {self.terminal}
bindsym $mod+q kill
bindsym $mod+space exec {self.launcher}
bindsym $mod+v floating toggle
bindsym $mod+f fullscreen toggle
bindsym $mod+l exec mios-lock
bindsym $mod+Shift+e exec swaynag -t warning -m 'Exit Sway?' -b 'Yes' 'swaymsg exit'

# Workspace Switching
bindsym $mod+1 workspace number 1
bindsym $mod+2 workspace number 2
bindsym $mod+3 workspace number 3
bindsym $mod+4 workspace number 4
bindsym $mod+5 workspace number 5

# Window Movement to Workspaces
bindsym $mod+Shift+1 move container to workspace number 1
bindsym $mod+Shift+2 move container to workspace number 2
bindsym $mod+Shift+3 move container to workspace number 3
bindsym $mod+Shift+4 move container to workspace number 4
bindsym $mod+Shift+5 move container to workspace number 5
"""

    def trigger_reload(self, wm: str) -> Dict[str, Any]:
        """Trigger live reload of active window manager compositor."""
        if self.mock or self.dry_run:
            return {"reloaded": True, "wm": wm, "command": f"{wm} reload (mock)"}

        cmd = []
        if wm == "hyprland":
            if shutil.which("hyprctl"):
                cmd = ["hyprctl", "reload"]
        elif wm == "sway":
            if shutil.which("swaymsg"):
                cmd = ["swaymsg", "reload"]

        if not cmd:
            return {"reloaded": False, "wm": wm, "message": f"{wm} binary not in PATH"}

        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
            return {
                "reloaded": res.returncode == 0,
                "wm": wm,
                "stdout": res.stdout.strip(),
                "stderr": res.stderr.strip(),
            }
        except Exception as e:
            return {"reloaded": False, "wm": wm, "error": str(e)}

    def write_output(self, path: str, content: str) -> None:
        """Write configuration to disk if not in mock or dry-run mode."""
        if not self.mock and not self.dry_run:
            mios_toml.write_atomic(path, content)

    def run(
        self,
        wm: str = "all",
        out_dir: Optional[str] = None,
        reload_active: bool = False,
    ) -> Dict[str, Any]:
        """Execute window manager configuration generation."""
        hyprland_src = self.generate_hyprland_conf()
        sway_src = self.generate_sway_config()
        written_files = []

        if out_dir:
            if wm in ("hyprland", "all"):
                h_path = os.path.join(out_dir, "hyprland", "hyprland.conf")
                self.write_output(h_path, hyprland_src)
                written_files.append(h_path)
            if wm in ("sway", "all"):
                s_path = os.path.join(out_dir, "sway", "config")
                self.write_output(s_path, sway_src)
                written_files.append(s_path)

        reload_results = {}
        if reload_active:
            if wm in ("hyprland", "all"):
                reload_results["hyprland"] = self.trigger_reload("hyprland")
            if wm in ("sway", "all"):
                reload_results["sway"] = self.trigger_reload("sway")

        return {
            "status": "success",
            "wm": wm,
            "gaps_inner": self.gaps_inner,
            "gaps_outer": self.gaps_outer,
            "border_size": self.border_size,
            "hyprland_lines": len(hyprland_src.splitlines()),
            "sway_lines": len(sway_src.splitlines()),
            "written_files": written_files,
            "reload": reload_results if reload_active else None,
            "dry_run": self.dry_run,
            "mock": self.mock,
        }

def fixture_renders() -> Dict[str, str]:
    """Each installed golden's root-relative path -> its render from the vendor tier."""
    engine = WmConfigGenEngine(mock=True, data=mios_toml.vendor_tree(_TREE))
    return {rel: getattr(engine, fn)() for rel, fn in GOLDENS.items()}

def check_fixture(root: str) -> int:
    """0 when every golden under root matches the generator, else 1 printing each drift."""
    return mios_toml.golden_gate("wm_config_gen", root, fixture_renders())

def write_fixture(root: str) -> int:
    """Regenerate every golden under root from the vendor tier."""
    return mios_toml.golden_gate("wm_config_gen", root, fixture_renders(), write=True)

def main() -> int:
    parser = argparse.ArgumentParser(
        description="MiOS Hyprland & Sway Window Manager Configuration Generator"
    )
    parser.add_argument("--wm", default="all", choices=["hyprland", "sway", "all"],
                        help="Target window manager")
    parser.add_argument("--out-dir", help="Output root directory for WM configs")
    parser.add_argument("--gaps-in", type=int, default=None, help="Inner gaps in pixels (default [theme.edge].wm_gaps_inner_px)")
    parser.add_argument("--gaps-out", type=int, default=None, help="Outer gaps in pixels (default [theme.edge].wm_gaps_outer_px)")
    parser.add_argument("--border-size", type=int, default=None, help="Border width in pixels (default [theme.edge].wm_border_px)")
    fixture = parser.add_mutually_exclusive_group()
    fixture.add_argument("--check-fixture", metavar="ROOT", help="Diff ROOT/usr/share/mios/{hyprland/hyprland.conf,sway/config} against the vendor-tier render")
    fixture.add_argument("--write-fixture", metavar="ROOT", help="Regenerate those files under ROOT from the vendor tier")
    parser.add_argument("--reload", action="store_true", help="Trigger hot-reload of active compositor")
    parser.add_argument("--dry-run", action="store_true", help="Simulate execution without writing files")
    parser.add_argument("--mock", action="store_true", help="Deterministic mock execution for CI")
    parser.add_argument("--json", action="store_true", help="Format output as JSON dictionary")

    args = parser.parse_args()
    if args.check_fixture or args.write_fixture:
        if (args.gaps_in, args.gaps_out, args.border_size) != (None, None, None):
            parser.error("--check-fixture/--write-fixture render the vendor tier; drop --gaps-in/--gaps-out/--border-size")
        try:
            return check_fixture(args.check_fixture) if args.check_fixture else write_fixture(args.write_fixture)
        except (ValueError, OSError) as exc:
            print(f"[wm_config_gen] ERROR: {exc}", file=sys.stderr)
            return 1

    try:
        engine = WmConfigGenEngine(
            gaps_inner=args.gaps_in,
            gaps_outer=args.gaps_out,
            border_size=args.border_size,
            mock=args.mock,
            dry_run=args.dry_run,
        )
        res = engine.run(
            wm=args.wm,
            out_dir=args.out_dir,
            reload_active=args.reload,
        )

        if args.json:
            print(json.dumps(res, indent=2))
        else:
            print(f"[wm_config_gen] SUCCESS: Generated configs for target '{args.wm}'")
            print(f"  Hyprland ({res['hyprland_lines']} lines) | Sway ({res['sway_lines']} lines)")
            if res["written_files"]:
                print(f"  Written files: {', '.join(res['written_files'])}")
        return 0
    except Exception as e:
        err = {"status": "error", "error": str(e)}
        if args.json:
            print(json.dumps(err, indent=2))
        else:
            print(f"[wm_config_gen] ERROR: {e}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())
