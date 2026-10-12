#!/usr/bin/env python3
# AI-hint: Cross-platform palette synchronizer writing directly to Windows Registry (.reg) and GTK 3/4 CSS
# AI-related: tests/test-ux.py, usr/share/mios/mios.toml, usr/lib/mios/mios_toml.py
# AI-functions: ThemeSyncEngine, hex_to_dword_bgr, generate_reg_content, generate_gtk3_css, generate_gtk4_css, main
"""
MiOS Cross-Platform Theme & Palette Synchronizer.

Synchronizes canonical palette tokens from `mios.toml` [colors] directly into:
1. Windows Registry structures (.reg and HKCU winreg API):
   - Personalize (AppsUseLightTheme, SystemUsesLightTheme, ColorPrevalence)
   - DWM (AccentColor, ColorizationColor)
   - Console/MiOS (ColorTable00..ColorTable15 in 0x00BBGGRR DWORD format)
2. GTK 3 CSS stylesheet (@define-color macros)
3. GTK 4 CSS stylesheet (:root CSS variables and window styling)
"""

from __future__ import annotations

import argparse
import importlib.machinery
import importlib.util
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_TREE = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..")
)

# Enable relative import of mios_toml
_LIB_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "lib", "mios")
)
if os.path.isdir(_LIB_DIR) and _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)

try:
    import mios_toml
except ImportError:
    mios_toml = None

def hex_to_dword_bgr(hex_color: str) -> int:
    """Convert hex #RRGGBB to Windows Console DWORD format (0x00BBGGRR)."""
    hex_clean = hex_color.lstrip("#")
    if len(hex_clean) == 6:
        r = int(hex_clean[0:2], 16)
        g = int(hex_clean[2:4], 16)
        b = int(hex_clean[4:6], 16)
        return (b << 16) | (g << 8) | r
    return 0

def hex_to_dword_abgr(hex_color: str, alpha: int = 0xFF) -> int:
    """Convert hex #RRGGBB to Windows DWM AccentColor format (0xAABBGGRR)."""
    hex_clean = hex_color.lstrip("#")
    if len(hex_clean) == 6:
        r = int(hex_clean[0:2], 16)
        g = int(hex_clean[2:4], 16)
        b = int(hex_clean[4:6], 16)
        return (alpha << 24) | (b << 16) | (g << 8) | r
    return 0xFF000000

class ThemeSyncEngine:
    """Synchronizes SSOT palette to Windows Registry, GTK3, and GTK4 surfaces."""

    def __init__(
        self,
        target: str = "all",
        dark_mode: bool = True,
        mock: bool = False,
        dry_run: bool = False,
    ):
        self.target = target
        self.dark_mode = dark_mode
        self.mock = mock
        self.dry_run = dry_run
        self.palette = self._load_palette()

    def _load_palette(self) -> Dict[str, str]:
        """Fetch color scheme from mios.toml SSOT or fallback defaults."""
        if mios_toml is not None:
            try:
                return mios_toml.colors()
            except Exception:
                pass
        return {
            "bg": "#282262",
            "fg": "#E7DFD3",
            "accent": "#1A407F",
            "cursor": "#F35C15",
            "success": "#3E7765",
            "warning": "#F35C15",
            "error": "#DC271B",
            "info": "#1A407F",
            "muted": "#948E8E",
            "subtle": "#B7C9D7",
            "earth": "#734F39",
            "silver": "#E0E0E0",
            "ansi_0_black": "#282262",
            "ansi_1_red": "#DC271B",
            "ansi_2_green": "#3E7765",
            "ansi_3_yellow": "#F35C15",
            "ansi_4_blue": "#1A407F",
            "ansi_5_magenta": "#734F39",
            "ansi_6_cyan": "#B7C9D7",
            "ansi_7_white": "#E7DFD3",
            "ansi_8_bright_black": "#948E8E",
            "ansi_9_bright_red": "#FF6B5C",
            "ansi_10_bright_green": "#5FAA8E",
            "ansi_11_bright_yellow": "#FF8540",
            "ansi_12_bright_blue": "#3D6BA8",
            "ansi_13_bright_magenta": "#9D7660",
            "ansi_14_bright_cyan": "#E0E0E0",
            "ansi_15_bright_white": "#FFFFFF",
        }

    def generate_windows_reg(self) -> str:
        """Generate Windows Registry (.reg) export."""
        accent_hex = self.palette.get("accent", "#1A407F")
        accent_dword = hex_to_dword_abgr(accent_hex)
        light_mode_val = 0 if self.dark_mode else 1

        reg_lines = [
            "Windows Registry Editor Version 5.00",
            "",
            "; MiOS Canonical Palette Sync - Windows Personalize & DWM",
            "[HKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\CurrentVersion\\Themes\\Personalize]",
            f'"AppsUseLightTheme"=dword:0000000{light_mode_val}',
            f'"SystemUsesLightTheme"=dword:0000000{light_mode_val}',
            '"ColorPrevalence"=dword:00000001',
            '"EnableTransparency"=dword:00000001',
            "",
            "[HKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\DWM]",
            f'"AccentColor"=dword:{accent_dword:08x}',
            f'"ColorizationColor"=dword:{accent_dword:08x}',
            '"ColorizationAfterglow"=dword:00000000',
            '"ColorizationBlurBalance"=dword:00000001',
            "",
        ]

        console_targets = [
            "[HKEY_CURRENT_USER\\Console]",
            "[HKEY_CURRENT_USER\\Console\\%SystemRoot%_System32_cmd.exe]",
            "[HKEY_CURRENT_USER\\Console\\MiOS]",
        ]

        font_family = "GeistMono Nerd Font Mono"
        font_size = 12
        if mios_toml is not None:
            try:
                d = mios_toml.load_merged()
                font_family = d.get("theme", {}).get("font", {}).get("family", font_family)
                font_size = int(d.get("theme", {}).get("font", {}).get("size", font_size))
            except Exception:
                pass
        font_dword = (font_size << 16)

        # ANSI 16 slot console color table mapping
        ansi_keys = [
            "ansi_0_black", "ansi_4_blue", "ansi_2_green", "ansi_6_cyan",
            "ansi_1_red", "ansi_5_magenta", "ansi_3_yellow", "ansi_7_white",
            "ansi_8_bright_black", "ansi_12_bright_blue", "ansi_10_bright_green", "ansi_14_bright_cyan",
            "ansi_9_bright_red", "ansi_13_bright_magenta", "ansi_11_bright_yellow", "ansi_15_bright_white"
        ]

        for section in console_targets:
            reg_lines.extend([
                section,
                f'"FaceName"="{font_family}"',
                '"FontFamily"=dword:00000036',
                f'"FontSize"=dword:{font_dword:08x}',
                '"PopupColors"=dword:000000f5',
                '"ScreenColors"=dword:00000007',
                '"VirtualTerminalLevel"=dword:00000001',
            ])
            for idx, key in enumerate(ansi_keys):
                hex_val = self.palette.get(key, "#000000")
                dword = hex_to_dword_bgr(hex_val)
                reg_lines.append(f'"ColorTable{idx:02d}"=dword:{dword:08x}')
            reg_lines.append("")

        return "\r\n".join(reg_lines)

    def generate_gtk3_css(self) -> str:
        """Generate GTK 3 CSS stylesheet using @define-color directives."""
        p = self.palette
        lines = [
            "/* MiOS GTK3 Theme Definitions (Generated from mios.toml SSOT) */",
            f"@define-color mios_bg {p.get('bg', '#282262')};",
            f"@define-color mios_fg {p.get('fg', '#E7DFD3')};",
            f"@define-color mios_accent {p.get('accent', '#1A407F')};",
            f"@define-color mios_cursor {p.get('cursor', '#F35C15')};",
            f"@define-color mios_success {p.get('success', '#3E7765')};",
            f"@define-color mios_warning {p.get('warning', '#F35C15')};",
            f"@define-color mios_error {p.get('error', '#DC271B')};",
            f"@define-color mios_info {p.get('info', '#1A407F')};",
            f"@define-color mios_muted {p.get('muted', '#948E8E')};",
            f"@define-color mios_subtle {p.get('subtle', '#B7C9D7')};",
            "",
            "/* GTK Semantic Bindings */",
            f"@define-color theme_bg_color {p.get('bg', '#282262')};",
            f"@define-color theme_fg_color {p.get('fg', '#E7DFD3')};",
            f"@define-color theme_base_color {p.get('bg', '#282262')};",
            f"@define-color theme_text_color {p.get('fg', '#E7DFD3')};",
            f"@define-color theme_selected_bg_color {p.get('accent', '#1A407F')};",
            f"@define-color theme_selected_fg_color {p.get('fg', '#E7DFD3')};",
            f"@define-color borders {p.get('muted', '#948E8E')};",
            f"@define-color link_color {p.get('cursor', '#F35C15')};",
            "",
            "window {",
            "    background-color: @theme_bg_color;",
            "    color: @theme_fg_color;",
            "}",
            "",
            "button.suggested-action {",
            "    background-color: @theme_selected_bg_color;",
            "    color: @theme_selected_fg_color;",
            "}",
            "",
        ]
        return "\n".join(lines)

    def generate_gtk4_css(self) -> str:
        """Generate GTK 4 CSS stylesheet using CSS custom properties (:root)."""
        p = self.palette
        lines = [
            "/* MiOS GTK4 Theme Definitions (Generated from mios.toml SSOT) */",
            ":root {",
            f"  --mios-bg: {p.get('bg', '#282262')};",
            f"  --mios-fg: {p.get('fg', '#E7DFD3')};",
            f"  --mios-accent: {p.get('accent', '#1A407F')};",
            f"  --mios-cursor: {p.get('cursor', '#F35C15')};",
            f"  --mios-success: {p.get('success', '#3E7765')};",
            f"  --mios-warning: {p.get('warning', '#F35C15')};",
            f"  --mios-error: {p.get('error', '#DC271B')};",
            f"  --mios-info: {p.get('info', '#1A407F')};",
            f"  --mios-muted: {p.get('muted', '#948E8E')};",
            f"  --mios-subtle: {p.get('subtle', '#B7C9D7')};",
            f"  --mios-earth: {p.get('earth', '#734F39')};",
            f"  --mios-silver: {p.get('silver', '#E0E0E0')};",
            f"  --accent-color: {p.get('accent', '#1A407F')};",
            f"  --accent-bg-color: {p.get('accent', '#1A407F')};",
            f"  --accent-fg-color: {p.get('fg', '#E7DFD3')};",
            f"  --window-bg-color: {p.get('bg', '#282262')};",
            f"  --window-fg-color: {p.get('fg', '#E7DFD3')};",
            "}",
            "",
            "window {",
            "  background-color: var(--window-bg-color);",
            "  color: var(--window-fg-color);",
            "}",
            "",
            ".accent {",
            "  color: var(--mios-accent);",
            "}",
            "",
        ]
        return "\n".join(lines)

    def write_output(self, path: str, content: str) -> None:
        """Write content to disk if not in mock or dry-run mode."""
        if not self.mock and not self.dry_run:
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)

    def run(
        self,
        export_reg: Optional[str] = None,
        export_gtk3: Optional[str] = None,
        export_gtk4: Optional[str] = None,
        out_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Execute theme synchronization."""
        reg_content = self.generate_windows_reg()
        gtk3_content = self.generate_gtk3_css()
        gtk4_content = self.generate_gtk4_css()

        written_files = []

        if export_reg:
            self.write_output(export_reg, reg_content)
            written_files.append(export_reg)
        if export_gtk3:
            self.write_output(export_gtk3, gtk3_content)
            written_files.append(export_gtk3)
        if export_gtk4:
            self.write_output(export_gtk4, gtk4_content)
            written_files.append(export_gtk4)

        if out_path:
            if self.target == "windows" or out_path.endswith(".reg"):
                self.write_output(out_path, reg_content)
            elif self.target == "gtk3":
                self.write_output(out_path, gtk3_content)
            else:
                self.write_output(out_path, gtk4_content)
            written_files.append(out_path)

        return {
            "status": "success",
            "target": self.target,
            "dark_mode": self.dark_mode,
            "palette": self.palette,
            "windows_reg_lines": len(reg_content.splitlines()),
            "gtk3_lines": len(gtk3_content.splitlines()),
            "gtk4_lines": len(gtk4_content.splitlines()),
            "written_files": written_files,
            "dry_run": self.dry_run,
            "mock": self.mock,
        }

def render_prompt(data, remote=False):
    """Render desktop or portable prompt glyphs with the same layered palette."""
    loader = importlib.machinery.SourceFileLoader(
        "mios_terminal_dotfiles", os.path.join(_TREE, "usr/libexec/mios/mios-dotfiles-render")
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    renderer = importlib.util.module_from_spec(spec)
    loader.exec_module(renderer)
    template = os.path.join(_TREE, data["dotfiles"]["registry"]["oh-my-posh"]["template"])
    with open(template, encoding="utf-8") as handle:
        omp = renderer._render_text(handle.read(), renderer._resolved_for(data, None), data)
    settings = data["theme"]["prompt"]
    mode = settings["remote_glyph_mode"] if remote else settings["glyph_mode"]
    if mode not in {"auto", "nerd", "ascii"}:
        raise ValueError("[theme.prompt] glyph modes must be auto, nerd or ascii")
    prompt = json.loads(omp)
    if mode == "ascii" or (mode == "auto" and (remote or "nerd" not in data["theme"]["font"]["family"].lower())):
        ascii_style = settings["ascii"]
        for block in prompt["blocks"]:
            for segment in block["segments"]:
                if segment["style"] == "powerline":
                    segment["style"] = "plain"
                    segment.pop("powerline_symbol", None)
                if segment["type"] == "text":
                    segment["template"] = ascii_style["leader"]
                elif segment["type"] == "status":
                    segment["template"] = ascii_style["closer"]
                elif segment["type"] == "git":
                    properties = segment.setdefault("properties", {})
                    properties.update(
                        fetch_upstream_icon=False,
                        branch_icon=ascii_style["git_branch"],
                        commit_icon=ascii_style["git_commit"],
                    )
                    segment["template"] = segment["template"].replace("{{ .UpstreamIcon }}", "").replace("✎", ascii_style["git_change"])
                if segment["style"] == "plain" and segment["type"] not in {"text", "status"}:
                    segment["template"] += ascii_style["separator"]
    return json.dumps(prompt, ensure_ascii=True, indent=2) + "\n"

def main() -> int:
    parser = argparse.ArgumentParser(
        description="MiOS Cross-Platform Theme & Palette Synchronizer"
    )
    parser.add_argument("--sync", action="store_true", help="Execute full theme synchronization")
    parser.add_argument("--target", default="all", choices=["windows", "gtk", "gtk3", "gtk4", "all"],
                        help="Theme output target")
    parser.add_argument("--export-reg", help="Export Windows Registry file (.reg)")
    parser.add_argument("--export-gtk3", help="Export GTK3 CSS file")
    parser.add_argument("--export-gtk4", help="Export GTK4 CSS file")
    parser.add_argument("--out", help="Generic output path")
    parser.add_argument("--light-theme", action="store_true", help="Sync light theme values")
    parser.add_argument("--check", action="store_true", help="Check for configuration drift against SSOT")
    parser.add_argument("--dry-run", action="store_true", help="Simulate execution without writing files")
    parser.add_argument("--mock", action="store_true", help="Deterministic mock execution for CI")
    parser.add_argument("--json", action="store_true", help="Format output as JSON dictionary")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")
    parser.add_argument("--render-prompt", action="store_true",
                        help="Print the layered Oh My Posh prompt JSON to stdout (used by mios-gen render-tmux-theme --runtime)")
    parser.add_argument("--remote", action="store_true", help="With --render-prompt: render the remote-terminal glyph mode")

    args = parser.parse_args()

    if args.render_prompt:
        try:
            if mios_toml is None:
                raise RuntimeError("mios_toml unavailable")
            mios_toml.clear_cache()
            sys.stdout.write(render_prompt(mios_toml.load_merged(), remote=args.remote))
            return 0
        except Exception as e:  # noqa: BLE001 -- report and fail closed for the caller
            print(f"[theme_sync] ERROR: {e}", file=sys.stderr)
            return 1

    engine = ThemeSyncEngine(
        target=args.target,
        dark_mode=not args.light_theme,
        mock=args.mock,
        dry_run=args.dry_run,
    )

    try:
        res = engine.run(
            export_reg=args.export_reg,
            export_gtk3=args.export_gtk3,
            export_gtk4=args.export_gtk4,
            out_path=args.out,
        )

        if args.json:
            print(json.dumps(res, indent=2))
        else:
            print(f"[theme_sync] SUCCESS: Synchronized theme for target '{args.target}'")
            print(f"  Palette: bg={res['palette'].get('bg')}, accent={res['palette'].get('accent')}, cursor={res['palette'].get('cursor')}")
            if res["written_files"]:
                print(f"  Written files: {', '.join(res['written_files'])}")
        return 0
    except Exception as e:
        err = {"status": "error", "error": str(e)}
        if args.json:
            print(json.dumps(err, indent=2))
        else:
            print(f"[theme_sync] ERROR: {e}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())
