#!/usr/bin/env python3
# AI-hint: MiOS-Cat tri-launcher hardening and repo vs mutable data path staging.
# AI-related: tests/test-cat-launcher.py, usr/share/doc/mios/manual/ch02-installation-and-deployment.md
"""
MiOS-Cat Tri-Launcher & Staging Engine.
Enforces read-only repo bind mounts and isolated mutable data staging for dev, staging, and prod modes.
"""

from __future__ import annotations

import os
import sys
from typing import Dict, List, Optional

class CatLauncher:
    """Manages tri-launcher execution profiles (dev, staging, prod) and filesystem staging."""

    VALID_MODES = {"dev", "staging", "prod"}

    def __init__(
        self,
        mode: str = "dev",
        repo_root: str = "/repo",
        data_root: str = "/data",
    ) -> None:
        if mode not in self.VALID_MODES:
            raise ValueError(f"Invalid mode: {mode}. Must be one of {self.VALID_MODES}")
        self.mode = mode
        self.repo_root = repo_root
        self.data_root = data_root

    def get_mount_configuration(self) -> List[Dict[str, str]]:
        """Returns podman/container bind mount flags ensuring repo is read-only."""
        mounts = [
            {"source": self.repo_root, "target": "/repo", "options": "ro"},
            {"source": os.path.join(self.data_root, self.mode), "target": "/data", "options": "rw"},
        ]
        if self.mode == "staging":
            mounts.append({"source": "tmpfs", "target": "/tmp", "options": "rw,noexec,nosuid"})
        return mounts

    def resolve_staging_paths(self) -> Dict[str, str]:
        """Resolves workspace and scratch paths for the current execution mode."""
        return {
            "mode": self.mode,
            "repo_dir": self.repo_root,
            "data_dir": os.path.join(self.data_root, self.mode),
            "state_dir": os.path.join(self.data_root, self.mode, "state"),
        }
