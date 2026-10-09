#!/usr/bin/env python3
# AI-hint: Bubblewrap namespace and filesystem sandboxing engine for Model Context Protocol (MCP) servers (task_321 / task_2554).
# AI-related: usr/lib/mios/agent-pipe/server.py, usr/lib/mios/agent-pipe/mios_pipe/federation/mcp.py, tests/test-mcp.py
"""
MiOS MCP Bubblewrap Sandboxing Engine.

Provides hermetic process isolation, mount namespace segregation, network policy
enforcement, and strict write path authorization for external Model Context Protocol
(MCP) tool and resource servers.
"""

from __future__ import annotations

import argparse
import json
import os
import posixpath
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

# Root paths strictly disallowed for writable mounting in sandboxed MCP environments
DISALLOWED_WRITE_ROOTS: Tuple[str, ...] = (
    "/etc",
    "/usr",
    "/boot",
    "/sys",
    "/root",
    "/bin",
    "/sbin",
    "/lib",
    "/lib64",
    "/dev",
    "/proc",
)

def normalize_posix_path(path: str) -> str:
    """Normalize a path to a canonical POSIX path format."""
    p = path.strip().replace("\\", "/")
    # Remove drive letters if present on Windows paths for testing
    if len(p) >= 2 and p[1] == ":":
        p = p[2:]
    norm = posixpath.normpath(p)
    if not norm.startswith("/"):
        norm = "/" + norm
    return norm

class McpSandbox:
    """
    Constructs and manages bubblewrap (bwrap) isolation parameters for MCP servers.
    """

    def __init__(
        self,
        server_name: str,
        allow_net: bool = False,
        custom_ro_binds: Optional[List[Union[str, Tuple[str, str]]]] = None,
        custom_rw_binds: Optional[List[Union[str, Tuple[str, str]]]] = None,
        workspace_dir: Optional[str] = None,
        bwrap_binary: str = "bwrap",
    ) -> None:
        self.server_name = server_name.strip()
        if not self.server_name:
            raise ValueError("server_name cannot be empty")

        self.allow_net = bool(allow_net)
        self.bwrap_binary = bwrap_binary
        self._ro_binds: List[Tuple[str, str]] = []
        self._rw_binds: List[Tuple[str, str]] = []
        self.workspace_dir: Optional[str] = None

        if custom_ro_binds:
            for item in custom_ro_binds:
                if isinstance(item, tuple) and len(item) == 2:
                    self.add_ro_bind(item[0], item[1])
                elif isinstance(item, str):
                    self.add_ro_bind(item)
                else:
                    raise TypeError(f"Invalid ro_bind format: {item!r}")

        if custom_rw_binds:
            for item in custom_rw_binds:
                if isinstance(item, tuple) and len(item) == 2:
                    self.add_rw_bind(item[0], item[1])
                elif isinstance(item, str):
                    self.add_rw_bind(item)
                else:
                    raise TypeError(f"Invalid rw_bind format: {item!r}")

        if workspace_dir is not None:
            self.set_workspace_dir(workspace_dir)

    def validate_rw_path(self, path: str) -> str:
        """
        Validate that a path is authorized for writable mounting.
        Raises ValueError if attempting to mount protected host system paths as writable.
        """
        if not path or not isinstance(path, str):
            raise ValueError("Path must be a non-empty string")

        norm = normalize_posix_path(path)

        for root in DISALLOWED_WRITE_ROOTS:
            if norm == root or norm.startswith(root + "/"):
                raise ValueError(
                    f"Disallowed writable bind path '{path}': target falls within protected system root '{root}'"
                )

        return norm

    def add_custom_bind(
        self,
        src: str,
        dest: Optional[str] = None,
        writable: bool = False,
    ) -> None:
        """Add a custom read-only or writable bind mount."""
        if writable:
            self.add_rw_bind(src, dest)
        else:
            self.add_ro_bind(src, dest)

    def add_ro_bind(self, src: str, dest: Optional[str] = None) -> None:
        """Add a read-only bind mount."""
        if not src:
            raise ValueError("Source path cannot be empty")
        target_dest = dest if dest is not None else src
        self._ro_binds.append((src, target_dest))

    def add_rw_bind(self, src: str, dest: Optional[str] = None) -> None:
        """Add a writable bind mount after validating both source and destination."""
        if not src:
            raise ValueError("Source path cannot be empty")
        target_dest = dest if dest is not None else src

        self.validate_rw_path(src)
        self.validate_rw_path(target_dest)

        self._rw_binds.append((src, target_dest))

    def set_workspace_dir(self, workspace_dir: str) -> None:
        """Configure the isolated workspace directory."""
        if not workspace_dir:
            raise ValueError("workspace_dir cannot be empty")
        self.validate_rw_path(workspace_dir)
        self.workspace_dir = workspace_dir

    @property
    def ro_binds(self) -> List[Tuple[str, str]]:
        """Return a copy of the read-only bind mounts."""
        return list(self._ro_binds)

    @property
    def rw_binds(self) -> List[Tuple[str, str]]:
        """Return a copy of the writable bind mounts."""
        return list(self._rw_binds)

    def build_command(self, inner_cmd: Sequence[str]) -> List[str]:
        """
        Construct the complete bubblewrap command line arguments to execute inner_cmd
        inside the isolated sandbox container.
        """
        if not inner_cmd:
            raise ValueError("inner_cmd cannot be empty")

        cmd: List[str] = [
            self.bwrap_binary,
            "--die-with-parent",
            "--new-session",
            "--unshare-all",
        ]

        # Network isolation policy
        if self.allow_net:
            cmd.append("--share-net")
        else:
            cmd.append("--unshare-net")

        # Core system read-only mounts
        cmd.extend([
            "--ro-bind", "/usr", "/usr",
            "--ro-bind", "/etc", "/etc",
            "--ro-bind", "/lib64", "/lib64",
        ])

        # Essential virtual and temporary filesystems
        cmd.extend([
            "--dev", "/dev",
            "--proc", "/proc",
            "--tmpfs", "/tmp",
        ])

        # User-configured custom read-only bind mounts
        for src, dst in self._ro_binds:
            cmd.extend(["--ro-bind", src, dst])

        # User-configured custom writable bind mounts
        for src, dst in self._rw_binds:
            # Re-validate at build time
            self.validate_rw_path(src)
            self.validate_rw_path(dst)
            cmd.extend(["--bind", src, dst])

        # Workspace directory isolation and working directory setup
        if self.workspace_dir:
            self.validate_rw_path(self.workspace_dir)
            cmd.extend([
                "--bind", self.workspace_dir, self.workspace_dir,
                "--chdir", self.workspace_dir,
            ])

        # Inner command to execute
        cmd.extend(inner_cmd)

        return cmd

    def to_dict(self) -> Dict[str, Any]:
        """Export sandbox configuration as a serializable dictionary."""
        return {
            "server_name": self.server_name,
            "allow_net": self.allow_net,
            "bwrap_binary": self.bwrap_binary,
            "ro_binds": self._ro_binds,
            "rw_binds": self._rw_binds,
            "workspace_dir": self.workspace_dir,
        }

    def execute(
        self,
        inner_cmd: Sequence[str],
        env: Optional[Dict[str, str]] = None,
        **subprocess_kwargs: Any,
    ) -> subprocess.CompletedProcess:
        """
        Execute the inner command inside the bubblewrap sandbox.
        """
        cmd = self.build_command(inner_cmd)
        run_env = os.environ.copy()
        if env:
            run_env.update(env)
        return subprocess.run(cmd, env=run_env, **subprocess_kwargs)

def parse_bind_arg(arg: str) -> Tuple[str, str]:
    """Parse a bind mount argument in the form 'src' or 'src:dest'."""
    if ":" in arg:
        parts = arg.split(":", 1)
        return parts[0], parts[1]
    return arg, arg

def build_arg_parser() -> argparse.ArgumentParser:
    """Create the command line argument parser for the MCP sandbox runner."""
    parser = argparse.ArgumentParser(
        description="MiOS MCP Bubblewrap Sandbox Engine",
        prog="sandbox.py",
    )
    parser.add_argument(
        "--server-name",
        type=str,
        default="mcp-server",
        help="Identifier name for the MCP server instance",
    )
    parser.add_argument(
        "--allow-net",
        action="store_true",
        default=False,
        help="Allow network access inside sandbox (default: isolated/unshared)",
    )
    parser.add_argument(
        "--workspace-dir",
        type=str,
        default=None,
        help="Workspace directory to bind mount as writable and chdir into",
    )
    parser.add_argument(
        "--ro-bind",
        action="append",
        default=[],
        metavar="PATH",
        help="Add a custom read-only bind mount (format: 'path' or 'src:dest')",
    )
    parser.add_argument(
        "--rw-bind",
        action="append",
        default=[],
        metavar="PATH",
        help="Add a custom writable bind mount (format: 'path' or 'src:dest')",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print constructed command arguments in JSON format and exit without running",
    )
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="Command and arguments to execute inside the sandbox (e.g. -- python3 server.py)",
    )
    return parser

def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entrypoint for MCP sandbox execution."""
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    raw_cmd = list(args.command)
    if raw_cmd and raw_cmd[0] == "--":
        raw_cmd = raw_cmd[1:]

    try:
        sb = McpSandbox(
            server_name=args.server_name,
            allow_net=args.allow_net,
            workspace_dir=args.workspace_dir,
        )

        for ro in args.ro_bind:
            src, dst = parse_bind_arg(ro)
            sb.add_ro_bind(src, dst)

        for rw in args.rw_bind:
            src, dst = parse_bind_arg(rw)
            sb.add_rw_bind(src, dst)

        if not raw_cmd:
            if args.dry_run:
                print(json.dumps(sb.to_dict(), indent=2))
                return 0
            parser.print_help(sys.stderr)
            return 2

        bwrap_cmd = sb.build_command(raw_cmd)

        if args.dry_run:
            print(json.dumps(bwrap_cmd, indent=2))
            return 0

        # Execute using execvp on POSIX platforms, or subprocess
        if hasattr(os, "execvp") and sys.platform != "win32":
            os.execvp(bwrap_cmd[0], bwrap_cmd)
        else:
            res = subprocess.run(bwrap_cmd)
            return res.returncode

    except ValueError as err:
        sys.stderr.write(f"Error: {err}\n")
        return 1
    except Exception as err:
        sys.stderr.write(f"Unexpected error: {err}\n")
        return 1

    return 0

if __name__ == "__main__":
    sys.exit(main())
