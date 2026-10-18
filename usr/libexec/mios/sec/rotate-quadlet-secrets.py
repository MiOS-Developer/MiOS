#!/usr/bin/env python3
# AI-hint: Quadlet secrets enforcement, fail-closed private atomic initialization preserving existing credentials, and explicit token rotation.
# AI-related: tests/test-sec.py, usr/share/doc/mios/manual/sec.md
"""
MiOS Quadlet Secrets Permission Hardening and Rotation Engine.
Enforces 0600 permissions on container environment files and rotates secret keys securely.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import stat
import sys
import tempfile
from typing import Dict, List, Optional, Tuple

class QuadletSecretsHardener:
    """Audits and rotates container environment secret files."""

    def __init__(self, secrets_dir: str = "/etc/mios/secrets") -> None:
        self.secrets_dir = secrets_dir

    def audit_and_harden_permissions(self, dir_path: Optional[str] = None) -> List[str]:
        target_dir = dir_path or self.secrets_dir
        fixed = []
        if not os.path.exists(target_dir):
            return fixed

        for root, _, files in os.walk(target_dir):
            for fname in files:
                if fname.endswith(".env") or fname.endswith(".secret"):
                    full_path = os.path.join(root, fname)
                    st = os.stat(full_path)
                    curr_mode = stat.S_IMODE(st.st_mode)
                    if curr_mode != 0o600:
                        os.chmod(full_path, 0o600)
                        fixed.append(full_path)
        return fixed

    def generate_rotated_secret(self, key_name: str, length_bytes: int = 32) -> Tuple[str, str]:
        """Generates new cryptographically secure token and formatted .env line."""
        token_hex = secrets.token_hex(length_bytes)
        env_line = f"{key_name}={token_hex}\n"
        return token_hex, env_line

    def init_secrets_env(self, secrets_file: str = "/etc/mios/secrets.env") -> Dict[str, str]:
        """Non-destructively initializes secrets.env ensuring existing credentials are preserved."""
        default_keys = [
            "POSTGRES_PASSWORD",
            "MIOS_IDENTITY_DEFAULT_PASSWORD",
            "HA_PASSWORD",
            "POSTGRESQL_PASSWORD",
            "K3S_TOKEN",
            "RAG_OPENAI_API_KEY",
            "WEBUI_SECRET_KEY",
        ]

        existing_secrets: Dict[str, str] = {}
        if os.path.islink(secrets_file):
            raise OSError("refusing a symbolic-link secrets file")
        try:
            read_fd = os.open(secrets_file, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except FileNotFoundError:
            exists = False
        else:
            exists = True
            with os.fdopen(read_fd, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        existing_secrets[k.strip()] = v.strip()

        updated = False
        for k in default_keys:
            if k not in existing_secrets or not existing_secrets[k]:
                token = secrets.token_hex(32)
                existing_secrets[k] = token
                updated = True

        parent_dir = os.path.dirname(secrets_file)
        if parent_dir and not os.path.exists(parent_dir):
            os.makedirs(parent_dir, exist_ok=True)

        if updated or not exists:
            # mkstemp creates a private inode. Apply the required mode before
            # writing any credential, then publish only a complete, flushed file.
            fd, staged = tempfile.mkstemp(dir=parent_dir or ".", prefix=".mios-secrets-", suffix=".tmp")
            try:
                os.chmod(staged, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                    fd = -1
                    f.write("# MiOS Container Secrets Environment File (0600)\n")
                    f.write("# Managed by mios-secret-init / rotate-quadlet-secrets.py\n")
                    for k, v in existing_secrets.items():
                        f.write(f"{k}={v}\n")
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(staged, secrets_file)
            finally:
                if fd >= 0:
                    os.close(fd)
                if os.path.exists(staged):
                    os.unlink(staged)
        else:
            # An unreadable file or a mode/publication failure must never become
            # a successful initialization receipt or regenerate operator keys.
            os.chmod(secrets_file, 0o600)

        return existing_secrets

def init_secrets_env(secrets_file: str = "/etc/mios/secrets.env") -> Dict[str, str]:
    """Module-level helper for non-destructive secrets initialization."""
    hardener = QuadletSecretsHardener()
    return hardener.init_secrets_env(secrets_file=secrets_file)

def main() -> int:
    parser = argparse.ArgumentParser(description="MiOS Quadlet Secrets Permission Hardening and Rotation Engine.")
    parser.add_argument("--init", action="store_true", help="Initialize /etc/mios/secrets.env non-destructively.")
    parser.add_argument("--audit", action="store_true", help="Audit and harden permissions in secrets directory.")
    parser.add_argument("--rotate", type=str, default=None, help="Generate rotated credential for specified key.")
    parser.add_argument("--secrets-file", type=str, default="/etc/mios/secrets.env", help="Target secrets.env file path.")
    parser.add_argument("--secrets-dir", type=str, default="/etc/mios/secrets", help="Target secrets directory.")
    parser.add_argument("--json", action="store_true", help="Output results in JSON format.")
    args = parser.parse_args()

    hardener = QuadletSecretsHardener(secrets_dir=args.secrets_dir)

    if args.init:
        secrets_map = hardener.init_secrets_env(secrets_file=args.secrets_file)
        if args.json:
            # A count, like the text receipt: the initializer's result never reaches output.
            sys.stdout.write(json.dumps({"status": "ok", "secrets_file": args.secrets_file, "key_count": len(secrets_map)}, indent=2) + "\n")
        else:
            sys.stdout.write(f"[secrets-init] Initialized {args.secrets_file} with {len(secrets_map)} keys (mode 0600)\n")
        return 0

    if args.audit:
        fixed = hardener.audit_and_harden_permissions(args.secrets_dir)
        if args.json:
            sys.stdout.write(json.dumps({"status": "ok", "fixed_count": len(fixed), "fixed_files": fixed}, indent=2) + "\n")
        else:
            sys.stdout.write(f"[secrets-audit] Hardened {len(fixed)} file(s) to mode 0600\n")
        return 0

    if args.rotate:
        token, line = hardener.generate_rotated_secret(args.rotate)
        if args.json:
            sys.stdout.write(json.dumps({"key": args.rotate, "token": token, "line": line.strip()}, indent=2) + "\n")
        else:
            sys.stdout.write(line)
        return 0

    parser.print_help()
    return 0

if __name__ == "__main__":
    sys.exit(main())
