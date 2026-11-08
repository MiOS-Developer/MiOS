#!/usr/bin/env python3
"""
mcp.py - Role-Based Capability Profiler for Model Context Protocol (MCP).
Prevents token bloat by dynamically loading only the MCP servers required for
the agent's immediate operational phase.
"""

import argparse
import json
import os
import sys
from pathlib import Path


class MCPProfileManager:
    def __init__(self, repo_root: str = None):
        self.repo_root = Path(repo_root or Path.cwd()).resolve()
        self.profiles_file = Path(__file__).resolve().parent / "mcp_profiles.json"

    def load_profiles(self):
        if self.profiles_file.exists():
            return json.loads(self.profiles_file.read_text(encoding="utf-8"))
        return {}

    def get_role_config(self, role: str):
        profiles = self.load_profiles()
        if role not in profiles:
            raise ValueError(f"Unknown role '{role}'. Available: {list(profiles.keys())}")
        return profiles[role]


def main():
    parser = argparse.ArgumentParser(description="Dev Loop MCP Role-Based Profiler")
    subparsers = parser.add_subparsers(dest="cmd", required=True)

    subparsers.add_parser("list", help="List all capability roles")
    get_p = subparsers.add_parser("get", help="Get MCP server configuration for a role")
    get_p.add_argument("role", help="Role name (explorer, architect, builder, critic, ui_verifier)")

    args = parser.parse_args()
    mgr = MCPProfileManager()

    if args.cmd == "list":
        profiles = mgr.load_profiles()
        for k, v in profiles.items():
            print(f"- **{k}**: {v.get('description', '')} ({', '.join(v.get('servers', []))})")
    elif args.cmd == "get":
        cfg = mgr.get_role_config(args.role)
        print(json.dumps(cfg, indent=2))


if __name__ == "__main__":
    main()
