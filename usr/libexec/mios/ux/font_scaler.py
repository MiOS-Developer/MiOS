#!/usr/bin/env python3
# AI-hint: Dynamic font size scaler for High-DPI displays calculating font metrics and fontconfig XML rules.
# AI-related: tests/test-ux.py, usr/share/mios/mios.toml
# AI-functions: FontScalerEngine, DisplayMetrics, ScaledFontConfig, main
"""
MiOS High-DPI Dynamic Font Size Scaler (task_406).

Calculates and applies optimal typography scaling for High-DPI (4K/Retina) vs
standard (1080p) displays across terminal and desktop surfaces.
Avoids fractional Wayland compositor scaling that causes blurry XWayland rendering;
instead projects crisp integer/point typography metrics into fontconfig and desktop settings.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_LIB_PATH = os.path.normpath(os.path.join(_HERE, "..", "..", "..", "lib", "mios"))
if os.path.isdir(_LIB_PATH) and _LIB_PATH not in sys.path:
    sys.path.insert(0, _LIB_PATH)

try:
    import mios_toml
except ImportError:
    mios_toml = None

DEFAULT_BASE_DPI = 96.0
DEFAULT_BASE_TERMINAL_FONT_PT = 11.0
DEFAULT_BASE_DESKTOP_FONT_PT = 10.0
DEFAULT_BASE_CODE_FONT_PT = 12.0
DEFAULT_BASE_CURSOR_SIZE = 24
DEFAULT_FONT_FAMILY = "JetBrains Mono"
DEFAULT_FONTCONFIG_PATH = "/usr/share/mios/themes/fonts.conf"

@dataclass
class DisplayMetrics:
    """Hardware display resolution and pixel density metrics."""
    width: int = 1920
    height: int = 1080
    dpi: float = 96.0
    scale_factor: float = 1.0
    detected_compositor: str = "wayland-generic"

@dataclass
class ScaledFontConfig:
    """Derived typography and UI dimensions across desktop surfaces."""
    dpi: float
    scale_factor: float
    terminal_font_pt: float
    desktop_font_pt: float
    code_font_pt: float
    cursor_size_px: int
    font_family: str
    text_scaling_factor: float

class FontScalerEngine:
    """Calculates display DPI metrics and generates fontconfig XML & desktop settings."""

    def __init__(
        self,
        base_dpi: float = DEFAULT_BASE_DPI,
        font_family: str = DEFAULT_FONT_FAMILY,
        out_fontconfig: Optional[str] = None,
        mock: bool = False,
        dry_run: bool = False,
        verbose: bool = False,
    ) -> None:
        self.base_dpi = base_dpi
        self.font_family = font_family
        self.out_fontconfig = out_fontconfig or DEFAULT_FONTCONFIG_PATH
        self.mock = mock
        self.dry_run = dry_run
        self.verbose = verbose

    def detect_metrics(
        self,
        override_dpi: Optional[float] = None,
        override_scale: Optional[float] = None,
    ) -> DisplayMetrics:
        """Detect or simulate display resolution and DPI."""
        if self.mock:
            dpi = override_dpi if override_dpi is not None else 192.0  # 4K HiDPI mock
            scale = override_scale if override_scale is not None else (dpi / self.base_dpi)
            return DisplayMetrics(
                width=3840,
                height=2160,
                dpi=dpi,
                scale_factor=scale,
                detected_compositor="mock-wayland",
            )

        if override_dpi is not None:
            scale = override_scale if override_scale is not None else (override_dpi / self.base_dpi)
            return DisplayMetrics(
                width=1920,
                height=1080,
                dpi=override_dpi,
                scale_factor=scale,
                detected_compositor="manual-override",
            )

        # Probe hyprctl if available
        if shutil.which("hyprctl"):
            try:
                res = subprocess.run(["hyprctl", "monitors", "-j"], capture_output=True, text=True, timeout=2)
                if res.returncode == 0:
                    monitors = json.loads(res.stdout)
                    if monitors and isinstance(monitors, list):
                        m = monitors[0]
                        w = int(m.get("width", 1920))
                        h = int(m.get("height", 1080))
                        sc = float(m.get("scale", 1.0))
                        derived_dpi = self.base_dpi * (h / 1080.0)
                        return DisplayMetrics(width=w, height=h, dpi=derived_dpi, scale_factor=sc, detected_compositor="hyprland")
            except Exception:
                pass

        # Probe swaymsg if available
        if shutil.which("swaymsg"):
            try:
                res = subprocess.run(["swaymsg", "-t", "get_outputs"], capture_output=True, text=True, timeout=2)
                if res.returncode == 0:
                    outputs = json.loads(res.stdout)
                    if outputs and isinstance(outputs, list):
                        o = outputs[0]
                        rect = o.get("rect", {})
                        w = int(rect.get("width", 1920))
                        h = int(rect.get("height", 1080))
                        sc = float(o.get("scale", 1.0))
                        derived_dpi = self.base_dpi * (h / 1080.0)
                        return DisplayMetrics(width=w, height=h, dpi=derived_dpi, scale_factor=sc, detected_compositor="sway")
            except Exception:
                pass

        # Standard 1080p fallback
        return DisplayMetrics(
            width=1920,
            height=1080,
            dpi=self.base_dpi,
            scale_factor=1.0,
            detected_compositor="fallback",
        )

    def calculate_font_config(
        self,
        metrics: DisplayMetrics,
        font_family: Optional[str] = None,
    ) -> ScaledFontConfig:
        """Calculate scaled typography points and pixel sizes based on metrics."""
        fam = font_family or self.font_family
        scale = max(0.75, min(4.0, metrics.dpi / self.base_dpi))

        term_pt = round(DEFAULT_BASE_TERMINAL_FONT_PT * scale, 1)
        desktop_pt = round(DEFAULT_BASE_DESKTOP_FONT_PT * scale, 1)
        code_pt = round(DEFAULT_BASE_CODE_FONT_PT * scale, 1)

        # Cursor sizes are standardized integers (24, 32, 48, 64)
        if scale >= 2.5:
            cursor_size = 64
        elif scale >= 1.75:
            cursor_size = 48
        elif scale >= 1.25:
            cursor_size = 32
        else:
            cursor_size = 24

        text_scale = round(scale, 2)

        return ScaledFontConfig(
            dpi=round(metrics.dpi, 1),
            scale_factor=round(scale, 2),
            terminal_font_pt=term_pt,
            desktop_font_pt=desktop_pt,
            code_font_pt=code_pt,
            cursor_size_px=cursor_size,
            font_family=fam,
            text_scaling_factor=text_scale,
        )

    def render_fontconfig_xml(self, config: ScaledFontConfig) -> str:
        """Render standard fontconfig 99-mios-dpi.conf XML."""
        return f"""<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">
<!-- MiOS High-DPI Typography Configuration - Generated by font_scaler.py -->
<fontconfig>
  <!-- Force exact DPI mapping for crisp rendering without XWayland fractional blur -->
  <match target="font">
    <edit name="dpi" mode="assign">
      <double>{config.dpi}</double>
    </edit>
    <edit name="antialias" mode="assign">
      <bool>true</bool>
    </edit>
    <edit name="hinting" mode="assign">
      <bool>true</bool>
    </edit>
    <edit name="hintstyle" mode="assign">
      <const>hintslight</const>
    </edit>
    <edit name="rgba" mode="assign">
      <const>rgb</const>
    </edit>
  </match>

  <!-- Default monospace binding -->
  <alias>
    <family>monospace</family>
    <prefer>
      <family>{config.font_family}</family>
      <family>Noto Sans Mono</family>
      <family>DejaVu Sans Mono</family>
    </prefer>
  </alias>
</fontconfig>
"""

    def apply(
        self,
        override_dpi: Optional[float] = None,
        override_scale: Optional[float] = None,
        out_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Compute scaling and write fontconfig snippet."""
        dest = out_path or self.out_fontconfig
        metrics = self.detect_metrics(override_dpi=override_dpi, override_scale=override_scale)
        config = self.calculate_font_config(metrics)
        xml_content = self.render_fontconfig_xml(config)

        if not self.mock and not self.dry_run:
            parent = os.path.dirname(dest)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(dest, "w", encoding="utf-8") as f:
                f.write(xml_content)

        return {
            "status": "success",
            "action": "scale_fonts",
            "target_path": dest,
            "metrics": asdict(metrics),
            "scaled_config": asdict(config),
            "xml_preview": xml_content,
            "dry_run": self.dry_run,
            "mock": self.mock,
        }

def main() -> int:
    parser = argparse.ArgumentParser(
        description="MiOS High-DPI Dynamic Font Size Scaler (task_406)"
    )
    parser.add_argument("--auto", action="store_true", help="Auto-detect monitor DPI from Wayland compositor")
    parser.add_argument("--dpi", type=float, help="Explicit target display DPI (e.g. 96.0, 144.0, 192.0)")
    parser.add_argument("--scale", type=float, help="Explicit display scaling factor (e.g. 1.0, 1.5, 2.0)")
    parser.add_argument("--font-name", default=DEFAULT_FONT_FAMILY, help="Primary typography font family")
    parser.add_argument("--out-fontconfig", help="Destination path for fontconfig XML rules")
    parser.add_argument("--mock", action="store_true", help="Deterministic in-memory mock mode")
    parser.add_argument("--dry-run", action="store_true", help="Simulate scaling without writing files")
    parser.add_argument("--json", action="store_true", help="Emit output as JSON")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")

    args = parser.parse_args()

    engine = FontScalerEngine(
        font_family=args.font_name,
        out_fontconfig=args.out_fontconfig,
        mock=args.mock,
        dry_run=args.dry_run,
        verbose=args.verbose,
    )

    try:
        result = engine.apply(
            override_dpi=args.dpi,
            override_scale=args.scale,
            out_path=args.out_fontconfig,
        )

        if args.json:
            print(json.dumps(result, indent=2))
        else:
            cfg = result["scaled_config"]
            print(f"[font_scaler] Display DPI: {cfg['dpi']} | Scale Factor: {cfg['scale_factor']}x")
            print(f"  Terminal Font: {cfg['terminal_font_pt']} pt ({cfg['font_family']})")
            print(f"  Desktop Font:  {cfg['desktop_font_pt']} pt")
            print(f"  Code Font:     {cfg['code_font_pt']} pt")
            print(f"  Cursor Size:   {cfg['cursor_size_px']} px")
            print(f"  Output:        {result['target_path']}")
        return 0
    except Exception as e:
        err = {"status": "error", "error": str(e)}
        if args.json:
            print(json.dumps(err, indent=2))
        else:
            print(f"[font_scaler] ERROR: {e}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())
