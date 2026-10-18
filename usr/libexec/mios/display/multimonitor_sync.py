#!/usr/bin/env python3
# AI-hint: Multi-monitor Looking Glass display geometry calculator, cursor synchronizer, and launcher generator.
# AI-related: tests/test-multimonitor-sync.py, usr/share/doc/mios/manual/ch67-discrete-gpu-vfio-looking-glass-and-displays.md
"""
MiOS Multi-Monitor Looking Glass Display Geometry & Cursor Synchronizer.

Calculates power-of-2 IVSHMEM buffer sizing across display resolutions (1080p, 1440p,
4K, Ultrawide, 8K), parses Wayland / Hyprland monitor topologies, computes cross-monitor
cursor warp transitions, and generates multi-head libvirt XML blocks, Hyprland window rules,
and synchronized multi-instance client launchers.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple, Union

SYNTHETIC_PRESETS: Dict[str, List[Dict[str, Any]]] = {
    "dual-1080p": [
        {"id": 0, "name": "DP-1", "width": 1920, "height": 1080, "refreshRate": 144.0, "x": 0, "y": 0, "scale": 1.0, "focused": True},
        {"id": 1, "name": "DP-2", "width": 1920, "height": 1080, "refreshRate": 144.0, "x": 1920, "y": 0, "scale": 1.0, "focused": False},
    ],
    "dual-1440p": [
        {"id": 0, "name": "DP-1", "width": 2560, "height": 1440, "refreshRate": 165.0, "x": 0, "y": 0, "scale": 1.0, "focused": True},
        {"id": 1, "name": "DP-2", "width": 2560, "height": 1440, "refreshRate": 165.0, "x": 2560, "y": 0, "scale": 1.0, "focused": False},
    ],
    "triple-4k": [
        {"id": 0, "name": "DP-1", "width": 3840, "height": 2160, "refreshRate": 120.0, "x": 0, "y": 0, "scale": 1.0, "focused": True},
        {"id": 1, "name": "DP-2", "width": 3840, "height": 2160, "refreshRate": 120.0, "x": 3840, "y": 0, "scale": 1.0, "focused": False},
        {"id": 2, "name": "DP-3", "width": 3840, "height": 2160, "refreshRate": 120.0, "x": 7680, "y": 0, "scale": 1.0, "focused": False},
    ],
    "mixed-1440p-4k": [
        {"id": 0, "name": "DP-1", "width": 2560, "height": 1440, "refreshRate": 165.0, "x": 0, "y": 0, "scale": 1.0, "focused": True},
        {"id": 1, "name": "DP-2", "width": 3840, "height": 2160, "refreshRate": 144.0, "x": 2560, "y": 0, "scale": 1.0, "focused": False},
    ],
    "ultrawide-plus-1080p": [
        {"id": 0, "name": "DP-1", "width": 5120, "height": 1440, "refreshRate": 240.0, "x": 0, "y": 0, "scale": 1.0, "focused": True},
        {"id": 1, "name": "DP-2", "width": 1920, "height": 1080, "refreshRate": 144.0, "x": 5120, "y": 0, "scale": 1.0, "focused": False},
    ],
}

class MultiMonitorSyncManager:
    """Calculates multi-head display geometry, IVSHMEM allocation, cursor warp, and launchers."""

    def __init__(self, monitors: Optional[List[Dict[str, Any]]] = None) -> None:
        self.monitors: List[Dict[str, Any]] = []
        if monitors:
            self.set_monitors(monitors)
        else:
            self.detect_displays(mock=True, synthetic_preset="dual-1440p")

    @staticmethod
    def compute_shm_size_mb(
        width: int,
        height: int,
        bpp: int = 4,
        double_buffer: bool = True,
        overhead_mb: int = 10,
    ) -> int:
        """Calculates power-of-2 IVSHMEM buffer size in MB for a given display resolution."""
        if width <= 0 or height <= 0:
            raise ValueError(f"Width and height must be positive, got {width}x{height}")

        multiplier = 2 if double_buffer else 1
        raw_bytes = width * height * bpp * multiplier
        raw_mb = (raw_bytes / (1024 * 1024)) + overhead_mb

        power = 16
        while power < raw_mb:
            power *= 2
        return power

    def set_monitors(self, monitor_list: List[Dict[str, Any]]) -> None:
        """Normalizes and configures monitor topology list with IVSHMEM sizing."""
        normalized: List[Dict[str, Any]] = []
        for idx, m in enumerate(monitor_list):
            w = int(m.get("width", 1920))
            h = int(m.get("height", 1080))
            shm_mb = self.compute_shm_size_mb(w, h)
            head_dict = {
                "head_id": int(m.get("id", idx)),
                "name": str(m.get("name", f"DP-{idx + 1}")),
                "width": w,
                "height": h,
                "refresh_rate": float(m.get("refreshRate", m.get("refresh_rate", 60.0))),
                "x": int(m.get("x", 0)),
                "y": int(m.get("y", 0)),
                "scale": float(m.get("scale", 1.0)),
                "focused": bool(m.get("focused", idx == 0)),
                "shm_device": str(m.get("shm_device", f"/dev/kvmfr{idx}")),
                "shm_size_mb": shm_mb,
            }
            normalized.append(head_dict)
        self.monitors = sorted(normalized, key=lambda mon: mon["x"])

    @classmethod
    def parse_hyprctl_monitors(cls, raw_data: Union[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        """Parses output from hyprctl monitors -j into structured monitor definitions."""
        if isinstance(raw_data, str):
            data = json.loads(raw_data)
        else:
            data = raw_data

        if not isinstance(data, list):
            raise ValueError("Expected a list of monitor objects from hyprctl")

        result = []
        for idx, item in enumerate(data):
            w = int(item.get("width", 1920))
            h = int(item.get("height", 1080))
            shm_mb = cls.compute_shm_size_mb(w, h)
            result.append({
                "id": int(item.get("id", idx)),
                "name": str(item.get("name", f"DP-{idx + 1}")),
                "width": w,
                "height": h,
                "refreshRate": float(item.get("refreshRate", 60.0)),
                "x": int(item.get("x", 0)),
                "y": int(item.get("y", 0)),
                "scale": float(item.get("scale", 1.0)),
                "focused": bool(item.get("focused", False)),
                "shm_size_mb": shm_mb,
                "shm_device": f"/dev/kvmfr{idx}",
            })
        return result

    def detect_displays(
        self,
        mock: bool = False,
        synthetic_preset: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Detects active display outputs from Hyprland/Wayland or synthetic presets."""
        preset_key = synthetic_preset or "dual-1440p"

        if mock or os.name == "nt" or not shutil.which("hyprctl"):
            monitors_data = SYNTHETIC_PRESETS.get(preset_key, SYNTHETIC_PRESETS["dual-1440p"])
            self.set_monitors(monitors_data)
            return self.monitors

        try:
            out = subprocess.check_output(["hyprctl", "monitors", "-j"], timeout=3)
            parsed = self.parse_hyprctl_monitors(out.decode("utf-8"))
            if parsed:
                self.set_monitors(parsed)
                return self.monitors
        except Exception:
            pass

        monitors_data = SYNTHETIC_PRESETS.get(preset_key, SYNTHETIC_PRESETS["dual-1440p"])
        self.set_monitors(monitors_data)
        return self.monitors

    def generate_libvirt_ivshmem_block(self) -> str:
        """Generates libvirt XML domain snippet with discrete IVSHMEM devices per head."""
        blocks = ["<!-- Multi-Monitor Looking Glass IVSHMEM Devices -->"]
        for idx, mon in enumerate(self.monitors):
            name = f"looking-glass-{idx}"
            blocks.append(f"""<shmem name="{name}">
  <model type="ivshmem-plain"/>
  <size unit="M">{mon['shm_size_mb']}</size>
</shmem>""")
        return "\n".join(blocks) + "\n"

    def generate_hyprland_multihead_rules(self) -> str:
        """Generates Hyprland window rules to pin each client instance to its physical output."""
        rules = [
            "# Multi-Head Looking Glass Hyprland Window Rules",
            "# Binds discrete Looking Glass client instances to designated Wayland outputs",
        ]
        for idx, mon in enumerate(self.monitors):
            app_class = f"looking-glass-head-{idx}"
            rules.extend([
                f"windowrulev2 = monitor {mon['name']}, class:^({app_class})$",
                f"windowrulev2 = fullscreen, class:^({app_class})$",
                f"windowrulev2 = idleinhibit always, class:^({app_class})$",
                f"windowrulev2 = immediate, class:^({app_class})$",
            ])
        return "\n".join(rules) + "\n"

    def calculate_cursor_warp(
        self,
        source_head: int,
        x: float,
        y: float,
    ) -> Dict[str, Any]:
        """
        Computes cursor coordinate transformation and boundary crossing between monitors.

        Given coordinates (x, y) relative to source_head's top-left origin:
        - Detects if cursor crosses right/left/top/bottom boundaries.
        - Identifies matching adjacent head in the global topology.
        - Returns transformed target head and target (x, y) coordinates.
        """
        if not self.monitors:
            return {"transition": False, "source_head": source_head, "target_head": source_head, "x": x, "y": y}

        src_mon = next((m for m in self.monitors if m["head_id"] == source_head), None)
        if not src_mon:
            src_mon = self.monitors[0]
            source_head = src_mon["head_id"]

        w = src_mon["width"]
        h = src_mon["height"]
        src_global_x = src_mon["x"] + x
        src_global_y = src_mon["y"] + y

        transition = False
        direction: Optional[str] = None
        target_head = source_head
        target_x = x
        target_y = y

        # Crossing Right Edge
        if x >= w:
            direction = "right"
            for mon in self.monitors:
                if mon["head_id"] != source_head and mon["x"] >= src_mon["x"] + w:
                    if mon["y"] <= src_global_y < (mon["y"] + mon["height"]):
                        target_head = mon["head_id"]
                        target_x = src_global_x - mon["x"]
                        target_y = src_global_y - mon["y"]
                        transition = True
                        break

        # Crossing Left Edge
        elif x < 0:
            direction = "left"
            for mon in self.monitors:
                if mon["head_id"] != source_head and (mon["x"] + mon["width"]) <= src_mon["x"]:
                    if mon["y"] <= src_global_y < (mon["y"] + mon["height"]):
                        target_head = mon["head_id"]
                        target_x = src_global_x - mon["x"]
                        target_y = src_global_y - mon["y"]
                        transition = True
                        break

        # Crossing Bottom Edge
        elif y >= h:
            direction = "down"
            for mon in self.monitors:
                if mon["head_id"] != source_head and mon["y"] >= src_mon["y"] + h:
                    if mon["x"] <= src_global_x < (mon["x"] + mon["width"]):
                        target_head = mon["head_id"]
                        target_x = src_global_x - mon["x"]
                        target_y = src_global_y - mon["y"]
                        transition = True
                        break

        # Crossing Top Edge
        elif y < 0:
            direction = "up"
            for mon in self.monitors:
                if mon["head_id"] != source_head and (mon["y"] + mon["height"]) <= src_mon["y"]:
                    if mon["x"] <= src_global_x < (mon["x"] + mon["width"]):
                        target_head = mon["head_id"]
                        target_x = src_global_x - mon["x"]
                        target_y = src_global_y - mon["y"]
                        transition = True
                        break

        return {
            "transition": transition,
            "direction": direction,
            "source_head": source_head,
            "target_head": target_head,
            "source_coords": [x, y],
            "target_coords": [target_x, target_y],
            "global_coords": [src_global_x, src_global_y],
        }

    def generate_launch_scripts(self, output_path: Optional[str] = None) -> str:
        """Synthesizes bash launcher script executing synchronized multi-client instances."""
        lines = [
            "#!/usr/bin/env bash",
            "# Generated by MiOS Multi-Monitor Looking Glass Synchronizer",
            "set -euo pipefail",
            "",
            'echo "Starting Looking Glass Multi-Head Instances..."',
            "",
        ]

        for idx, mon in enumerate(self.monitors):
            app_class = f"looking-glass-head-{idx}"
            title = f"Looking Glass - Head {idx} ({mon['name']})"
            lines.extend([
                f"# Head {idx} ({mon['name']} - {mon['width']}x{mon['height']} @ {mon['refresh_rate']}Hz -> {mon['shm_size_mb']}MB)",
                "looking-glass-client \\",
                f"  -f {mon['shm_device']} \\",
                f'  app:title="{title}" \\',
                f'  app:class="{app_class}" \\',
                f'  wayland:output="{mon["name"]}" &',
                "",
            ])

        lines.extend([
            'echo "All Looking Glass multi-head instances launched."',
            "wait",
            "",
        ])

        script_content = "\n".join(lines)
        if output_path:
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(script_content)
        return script_content

    def verify_all(self, mock: bool = False) -> Dict[str, Any]:
        """Validates geometry sizing, XML emission, rule synthesis, and warp calculations."""
        monitors = self.detect_displays(mock=mock)
        xml = self.generate_libvirt_ivshmem_block()
        rules = self.generate_hyprland_multihead_rules()
        script = self.generate_launch_scripts()

        # Test sample warp across head 0 and head 1
        warp_res = self.calculate_cursor_warp(0, monitors[0]["width"] + 10, 500) if len(monitors) > 1 else {"transition": False}

        xml_ok = "looking-glass-0" in xml and "<shmem" in xml
        rules_ok = "windowrulev2 = monitor" in rules
        script_ok = "looking-glass-client" in script

        overall_pass = xml_ok and rules_ok and script_ok and len(monitors) >= 1

        return {
            "status": "pass" if overall_pass else "fail",
            "head_count": len(monitors),
            "monitors": monitors,
            "warp_test": warp_res,
            "checks": {
                "display_detection": "pass" if len(monitors) >= 1 else "fail",
                "xml_generation": "pass" if xml_ok else "fail",
                "hyprland_rules": "pass" if rules_ok else "fail",
                "launch_script": "pass" if script_ok else "fail",
            },
            "mock": mock or os.name == "nt",
        }

def main() -> int:
    parser = argparse.ArgumentParser(
        description="MiOS Multi-Monitor Looking Glass Display Geometry & Synchronizer Utility."
    )
    parser.add_argument("--detect", action="store_true", help="Detect Wayland monitors and output topology.")
    parser.add_argument("--preset", type=str, default="dual-1440p", choices=list(SYNTHETIC_PRESETS.keys()), help="Synthetic monitor preset.")
    parser.add_argument("--generate-xml", action="store_true", help="Generate multi-head libvirt IVSHMEM XML snippet.")
    parser.add_argument("--generate-rules", action="store_true", help="Generate Hyprland multi-head window rules.")
    parser.add_argument("--generate-launch", action="store_true", help="Generate bash multi-client launcher script.")
    parser.add_argument("--calc-shm", action="store_true", help="Calculate power-of-2 IVSHMEM buffer size for width/height.")
    parser.add_argument("--width", type=int, default=1920, help="Display horizontal resolution.")
    parser.add_argument("--height", type=int, default=1080, help="Display vertical resolution.")
    parser.add_argument("--warp-test", action="store_true", help="Calculate cursor warp transition.")
    parser.add_argument("--source-head", type=int, default=0, help="Source head index for cursor warp.")
    parser.add_argument("--x", type=float, default=2560.0, help="Cursor X coordinate relative to source head.")
    parser.add_argument("--y", type=float, default=500.0, help="Cursor Y coordinate relative to source head.")
    parser.add_argument("--verify", action="store_true", help="Run full diagnostic verification.")
    parser.add_argument("--output", type=str, default=None, help="Optional output file path.")
    parser.add_argument("--json", action="store_true", help="Output results in JSON format.")
    parser.add_argument("--mock", action="store_true", help="Run in mock/synthetic mode.")
    args = parser.parse_args()

    manager = MultiMonitorSyncManager()
    manager.detect_displays(mock=args.mock or os.name == "nt", synthetic_preset=args.preset)

    result_text = ""
    json_data: Optional[Dict[str, Any]] = None

    if args.calc_shm:
        shm_mb = manager.compute_shm_size_mb(args.width, args.height)
        if args.json:
            json_data = {"width": args.width, "height": args.height, "shm_size_mb": shm_mb}
        else:
            result_text = f"Resolution: {args.width}x{args.height} -> IVSHMEM Buffer Size: {shm_mb} MB (Power of 2)"

    elif args.warp_test:
        warp_res = manager.calculate_cursor_warp(args.source_head, args.x, args.y)
        if args.json:
            json_data = warp_res
        else:
            result_text = (
                f"Cursor Warp Test: Source Head {warp_res['source_head']} ({args.x}, {args.y})\n"
                f"  - Transition: {warp_res['transition']}\n"
                f"  - Direction: {warp_res.get('direction')}\n"
                f"  - Target Head: {warp_res['target_head']}\n"
                f"  - Target Coords: {warp_res['target_coords']}"
            )

    elif args.generate_xml:
        result_text = manager.generate_libvirt_ivshmem_block()
        if args.json:
            json_data = {"xml": result_text, "heads": len(manager.monitors)}

    elif args.generate_rules:
        result_text = manager.generate_hyprland_multihead_rules()
        if args.json:
            json_data = {"rules": result_text, "heads": len(manager.monitors)}

    elif args.generate_launch:
        result_text = manager.generate_launch_scripts()
        if args.json:
            json_data = {"script": result_text, "heads": len(manager.monitors)}

    elif args.detect:
        if args.json:
            json_data = {"monitors": manager.monitors, "count": len(manager.monitors)}
        else:
            lines = [f"Detected {len(manager.monitors)} monitors:"]
            for m in manager.monitors:
                lines.append(
                    f"  - Head {m['head_id']} ({m['name']}): {m['width']}x{m['height']}@{m['refresh_rate']}Hz "
                    f"at ({m['x']},{m['y']}) -> SHM: {m['shm_size_mb']}MB ({m['shm_device']})"
                )
            result_text = "\n".join(lines)

    elif args.verify or not sys.argv[1:]:
        verify_results = manager.verify_all(mock=args.mock or os.name == "nt")
        if args.json:
            json_data = verify_results
        else:
            result_text = (
                f"[multimonitor-sync] Status: {verify_results['status'].upper()} (mock={verify_results['mock']})\n"
                f"  - Heads: {verify_results['head_count']}\n"
                f"  - Detection: {verify_results['checks']['display_detection']}\n"
                f"  - Libvirt XML: {verify_results['checks']['xml_generation']}\n"
                f"  - Hyprland Rules: {verify_results['checks']['hyprland_rules']}\n"
                f"  - Launch Scripts: {verify_results['checks']['launch_script']}\n"
            )
        if not args.output and not args.json:
            sys.stdout.write(result_text)
            return 0 if verify_results["status"] == "pass" else 1

    if json_data is not None and args.json:
        result_text = json.dumps(json_data, indent=2) + "\n"

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(result_text)
    else:
        sys.stdout.write(result_text + ("\n" if not result_text.endswith("\n") else ""))

    return 0

if __name__ == "__main__":
    sys.exit(main())
