# SPDX-License-Identifier: Apache-2.0
# AI-hint: Dynamic plugin loader and subcommand discovery for MiOS CLI (task_458).
# AI-doc: usr/share/doc/mios/manual/mios.md
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class PluginInfo:
    name: str
    path: str
    description: str
    is_python: bool


class PluginLoader:
    """Discovers and executes dynamic subcommands from plugin directories."""

    def __init__(self, search_paths: Optional[List[str]] = None) -> None:
        if search_paths is not None:
            self.search_paths = search_paths
        else:
            root = os.environ.get("MIOS_DRIFT_ROOT") or os.environ.get("MIOS_ROOT")
            if not root:
                p = pathlib.Path(__file__).resolve()
                for parent in p.parents:
                    if (parent / "usr" / "libexec" / "mios").is_dir():
                        root = str(parent)
                        break
            paths = []
            if root:
                paths.append(os.path.join(root, "usr", "libexec", "mios", "plugins"))
                paths.append(os.path.join(root, "var", "lib", "mios", "plugins"))
            paths.extend(["/usr/libexec/mios/plugins", "/var/lib/mios/plugins"])
            self.search_paths = [p for p in paths if p]

    def discover_plugins(self) -> Dict[str, PluginInfo]:
        plugins: Dict[str, PluginInfo] = {}
        for search_dir in self.search_paths:
            p_dir = pathlib.Path(search_dir)
            if not p_dir.is_dir():
                continue
            for entry in sorted(p_dir.iterdir()):
                if entry.is_file() and not entry.name.startswith("."):
                    name = entry.name
                    if name.endswith(".py"):
                        sub_name = name[:-3]
                        is_py = True
                    else:
                        sub_name = name
                        is_py = False

                    if sub_name.startswith("mios-"):
                        sub_name = sub_name[5:]

                    # Ignore if already discovered (higher priority directory wins)
                    if sub_name in plugins:
                        continue

                    # Determine description
                    desc = self._extract_description(entry, is_py)
                    plugins[sub_name] = PluginInfo(
                        name=sub_name,
                        path=str(entry.resolve()),
                        description=desc,
                        is_python=is_py,
                    )
        return plugins

    def _extract_description(self, path: pathlib.Path, is_python: bool) -> str:
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                for _ in range(25):
                    line = f.readline()
                    if not line:
                        break
                    line_s = line.strip()
                    if line_s.startswith("# AI-hint:"):
                        return line_s[10:].strip()
                    if line_s.startswith("# Description:"):
                        return line_s[14:].strip()
                    if is_python and (line_s.startswith('"""') or line_s.startswith("'''")):
                        desc = line_s.strip("\"'")
                        if desc:
                            return desc
        except Exception:
            pass
        return "external plugin subcommand"

    def execute_plugin(self, name: str, args: List[str]) -> int:
        plugins = self.discover_plugins()
        if name not in plugins:
            sys.stderr.write(f"mios: unknown plugin '{name}'\n")
            return 127

        target = plugins[name]
        cmd = [sys.executable, target.path] if target.is_python or not os.access(target.path, os.X_OK) else [target.path]
        try:
            res = subprocess.run(cmd + args)
            return res.returncode
        except Exception as e:
            sys.stderr.write(f"mios plugin '{name}' error: {e}\n")
            return 1


def get_available_plugins() -> Dict[str, PluginInfo]:
    return PluginLoader().discover_plugins()


def run_plugin(name: str, args: List[str]) -> int:
    return PluginLoader().execute_plugin(name, args)
