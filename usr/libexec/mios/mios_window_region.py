#!/usr/bin/env python3
# AI-hint: Pure geometry helper for named window-snap regions.
# AI-doc: usr/share/doc/mios/manual/mios.md
"""Named window-snap region geometry (pure, no side effects).

The rectangle math is intentionally free of hardcoded pixel constants: half /
quarter fills are computed from the LIVE work-area width and height, and the
right/bottom halves take the exact remainder so a pair of halves tiles the work
area with no gap or overlap on odd dimensions.
"""
from __future__ import annotations

import json
import sys

_ALIASES = {
    "left": "left-half",
    "right": "right-half",
    "top": "top-half",
    "bottom": "bottom-half",
    "max": "maximize",
    "full": "maximize",
    "maximize-pos": "maximize",
    "fullscreen": "maximize",
}

def normalize_region(region: str) -> str:
    """Canonicalize a region name: lowercase, trim, underscores->hyphens, alias."""
    key = (region or "").strip().lower().replace("_", "-")
    return _ALIASES.get(key, key)

def region_rect(width: int, height: int, region: str):
    """Return (x, y, w, h) for ``region`` within a work area ``width`` x ``height``.

    Coordinates are RELATIVE to the work-area origin (0, 0); the caller adds the
    monitor's work-area (x, y) offset. Returns ``None`` for an unknown region.
    """
    w = int(width)
    h = int(height)
    half_w = w // 2
    half_h = h // 2
    rem_w = w - half_w
    rem_h = h - half_h

    region = normalize_region(region)
    table = {
        "maximize": (0, 0, w, h),
        "left-half": (0, 0, half_w, h),
        "right-half": (half_w, 0, rem_w, h),
        "top-half": (0, 0, w, half_h),
        "bottom-half": (0, half_h, w, rem_h),
        "top-left": (0, 0, half_w, half_h),
        "top-right": (half_w, 0, rem_w, half_h),
        "bottom-left": (0, half_h, half_w, rem_h),
        "bottom-right": (half_w, half_h, rem_w, rem_h),
    }
    return table.get(region)

def rect_from_layout(layout: dict, region: str, monitor: int = 0):
    """Compute the ABSOLUTE (x, y, w, h) for ``region`` from a screen-layout dict.

    ``layout`` matches the OS-control executor's /screen-layout contract:
    ``{"screens": [{"work": {"x", "y", "width", "height"}}, ...]}``. The chosen
    monitor's work-area origin is added to the relative rectangle. Returns None
    on an unknown region or an out-of-range / malformed monitor entry.
    """
    try:
        screens = layout["screens"]
        work = screens[monitor if 0 <= monitor < len(screens) else 0]["work"]
        wx, wy = int(work["x"]), int(work["y"])
        ww, wh = int(work["width"]), int(work["height"])
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    rel = region_rect(ww, wh, region)
    if rel is None:
        return None
    x, y, w, h = rel
    return (wx + x, wy + y, w, h)

def main(argv=None) -> int:
    """CLI: ``mios_window_region.py <region> [monitor]`` reads a screen-layout
    JSON on stdin and prints ``x y w h`` (absolute). Exit 2 = unknown region /
    unresolvable geometry; exit 3 = unparseable layout on stdin."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        sys.stderr.write("usage: mios_window_region.py <region> [monitor] < screen-layout.json\n")
        return 64
    region = argv[0]
    monitor = int(argv[1]) if len(argv) > 1 and argv[1].lstrip("-").isdigit() else 0
    try:
        layout = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError):
        return 3
    rect = rect_from_layout(layout, region, monitor)
    if rect is None:
        return 2
    sys.stdout.write("{} {} {} {}\n".format(*rect))
    return 0

if __name__ == "__main__":
    sys.exit(main())
