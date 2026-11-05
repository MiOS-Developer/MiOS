#!/usr/bin/env python3
"""
artifacts.py - Dev Loop Artifact Lifecycle and Manifest Manager.
Tracks, validates, hashes, and audits all generated artifacts across engineering iterations.
"""

import argparse
import hashlib
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class ArtifactEntry:
    path: str
    artifact_type: str  # code, test, doc, config, schema, telemetry
    sha256: str
    size_bytes: int
    created_at: str
    metadata: Dict[str, str] = field(default_factory=dict)


class ArtifactManifestManager:
    def __init__(self, repo_root: Optional[str] = None):
        self.repo_root = Path(repo_root or self._find_repo_root()).resolve()
        self.artifacts_dir = self.repo_root / ".devloop_artifacts"
        self.manifest_path = self.artifacts_dir / "manifest.json"
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.entries: Dict[str, ArtifactEntry] = {}
        self._load_manifest()

    def _find_repo_root(self) -> Path:
        cur = Path.cwd()
        for parent in [cur] + list(cur.parents):
            if (parent / ".git").exists():
                return parent
        return cur

    def _compute_sha256(self, file_path: Path) -> str:
        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
        return h.hexdigest()

    def _load_manifest(self):
        if self.manifest_path.exists():
            try:
                with open(self.manifest_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for item in data.get("artifacts", []):
                        entry = ArtifactEntry(**item)
                        self.entries[entry.path] = entry
            except Exception as e:
                print(f"[WARN] Failed to load manifest: {e}", file=sys.stderr)

    def save_manifest(self):
        data = {
            "version": "2.0.0",
            "last_updated": datetime.now().isoformat(),
            "total_artifacts": len(self.entries),
            "artifacts": [asdict(e) for e in self.entries.values()]
        }
        temp_file = self.manifest_path.with_suffix(".tmp")
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        temp_file.replace(self.manifest_path)

    def register(self, file_rel_path: str, artifact_type: str = "code", metadata: Optional[Dict] = None) -> ArtifactEntry:
        target = (self.repo_root / file_rel_path).resolve()
        if not target.exists() or not target.is_file():
            raise FileNotFoundError(f"Cannot register missing artifact: {file_rel_path}")

        size = target.stat().st_size
        if size == 0:
            raise ValueError(f"Refusing to register 0-byte truncated artifact: {file_rel_path}")

        sha = self._compute_sha256(target)
        rel_str = str(target.relative_to(self.repo_root)).replace("\\", "/")

        entry = ArtifactEntry(
            path=rel_str,
            artifact_type=artifact_type,
            sha256=sha,
            size_bytes=size,
            created_at=datetime.now().isoformat(),
            metadata=metadata or {}
        )
        self.entries[rel_str] = entry
        self.save_manifest()
        print(f"[REGISTERED] {rel_str} ({artifact_type}, {size} bytes, sha256: {sha[:12]}...)")
        return entry

    def verify(self) -> bool:
        print(f"==> Verifying integrity of {len(self.entries)} registered artifacts...")
        all_ok = True
        for path_str, entry in self.entries.items():
            target = self.repo_root / path_str
            if not target.exists():
                print(f"  [MISSING] {path_str}", file=sys.stderr)
                all_ok = False
                continue

            current_size = target.stat().st_size
            if current_size == 0:
                print(f"  [TRUNCATED] {path_str} is 0 bytes!", file=sys.stderr)
                all_ok = False
                continue

            current_sha = self._compute_sha256(target)
            if current_sha != entry.sha256:
                print(f"  [CORRUPT] {path_str} SHA-256 mismatch (Expected: {entry.sha256[:12]}, Found: {current_sha[:12]})", file=sys.stderr)
                all_ok = False
                continue

            print(f"  [OK] {path_str}")

        return all_ok


def main():
    parser = argparse.ArgumentParser(description="Dev Loop Artifact Lifecycle Manager")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    reg_p = subparsers.add_parser("register", help="Register an artifact in the manifest")
    reg_p.add_argument("path", help="Path to artifact relative to repository root")
    reg_p.add_argument("--type", default="code", choices=["code", "test", "doc", "config", "schema", "telemetry"])

    ver_p = subparsers.add_parser("verify", help="Verify all registered artifacts against manifest")

    args = parser.parse_args()
    mgr = ArtifactManifestManager()

    if args.subcommand == "register":
        mgr.register(args.path, args.type)
    elif args.subcommand == "verify":
        if not mgr.verify():
            sys.exit(1)


if __name__ == "__main__":
    main()
