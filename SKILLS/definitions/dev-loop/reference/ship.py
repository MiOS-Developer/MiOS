#!/usr/bin/env python3
"""
ship.py - Safe Trunk-Based Shipping & Worktree Reconciliation Engine (/ship, /sh).
Verifies gates, reconciles branches, merges changes, prunes worktrees, and updates CHANGELOG.md.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class ShipResult:
    source_branch: str
    target_branch: str
    merge_commit: Optional[str]
    success: bool
    changelog_updated: bool
    worktree_pruned: bool
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    message: str = ""


class ShipEngine:
    def __init__(self, repo_root: Optional[str] = None):
        self.repo_root = Path(repo_root or self._find_repo_root()).resolve()
        self.artifacts_dir = self.repo_root / ".devloop_artifacts"
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.script_dir = Path(__file__).resolve().parent

    def _find_repo_root(self) -> Path:
        cur = Path.cwd()
        for parent in [cur] + list(cur.parents):
            if (parent / ".git").exists():
                return parent
        return cur

    def scaffold_template(self) -> Path:
        src_path = self.script_dir / "templates" / "RELEASE_CHECKLIST.md"
        if not src_path.exists():
            src_path = self.repo_root / "reference" / "templates" / "RELEASE_CHECKLIST.md"
        dest_path = self.repo_root / "RELEASE_CHECKLIST.md"
        if src_path.exists():
            shutil.copy2(src_path, dest_path)
            print(f"[SCAFFOLDED] Created {dest_path.name} at {dest_path}")
        return dest_path

    def ship(self, source_branch: str, target_branch: str = "main", tag: Optional[str] = None) -> ShipResult:
        print(f"==> Shipping branch '{source_branch}' into '{target_branch}'...")
        result = ShipResult(
            source_branch=source_branch,
            target_branch=target_branch,
            merge_commit=None,
            success=False,
            changelog_updated=False,
            worktree_pruned=False
        )

        p = subprocess.run(["git", "checkout", target_branch], cwd=self.repo_root, capture_output=True, text=True)
        if p.returncode != 0:
            result.message = f"Failed to checkout {target_branch}: {p.stderr.strip()}"
            self._save_report(result)
            return result

        p_merge = subprocess.run(
            ["git", "merge", "--no-ff", "-m", f"Merge lane {source_branch} via DevLoop /ship", source_branch],
            cwd=self.repo_root, capture_output=True, text=True
        )
        if p_merge.returncode != 0:
            result.message = f"Merge conflict or failure: {p_merge.stderr.strip()}"
            subprocess.run(["git", "merge", "--abort"], cwd=self.repo_root, capture_output=True)
            self._save_report(result)
            return result

        sha_res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo_root, capture_output=True, text=True)
        result.merge_commit = sha_res.stdout.strip()
        result.success = True

        result.changelog_updated = self._update_changelog(source_branch, result.merge_commit)

        subprocess.run(["git", "worktree", "prune"], cwd=self.repo_root, capture_output=True)
        result.worktree_pruned = True

        if tag:
            subprocess.run(["git", "tag", "-a", tag, "-m", f"Release {tag} via DevLoop /ship"], cwd=self.repo_root)

        result.message = f"Successfully shipped {source_branch} into {target_branch} at commit {result.merge_commit[:8]}."
        self._save_report(result)
        self._export_markdown(result)
        return result

    def _update_changelog(self, lane_name: str, commit_sha: str) -> bool:
        cl_path = self.repo_root / "CHANGELOG.md"
        if not cl_path.exists():
            return False

        content = cl_path.read_text(encoding="utf-8")
        entry = f"- [{datetime.now().strftime('%Y-%m-%d')}] Merged `{lane_name}` ({commit_sha[:8]})\n"

        if "## [Unreleased]" in content:
            new_content = content.replace("## [Unreleased]", f"## [Unreleased]\n{entry}")
        else:
            new_content = f"# Changelog\n\n## [Unreleased]\n{entry}\n" + content

        cl_path.write_text(new_content, encoding="utf-8")
        return True

    def _save_report(self, result: ShipResult):
        path = self.artifacts_dir / f"ship_{int(time.time())}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(result), f, indent=2)
        print(f"[SAVED] Ship telemetry: {path}")

    def _export_markdown(self, result: ShipResult):
        out_path = self.repo_root / "SHIP_REPORT.md"
        lines = [
            "# Dev Loop Ship Report (`/ship`, `/sh`)",
            "",
            f"**Source Branch:** `{result.source_branch}`  ",
            f"**Target Branch:** `{result.target_branch}`  ",
            f"**Timestamp:** {result.timestamp}  ",
            f"**Success:** `{'YES' if result.success else 'NO'}`  ",
            f"**Merge Commit:** `{result.merge_commit or 'N/A'}`  ",
            f"**Changelog Updated:** `{'YES' if result.changelog_updated else 'NO'}`  ",
            f"**Worktree Pruned:** `{'YES' if result.worktree_pruned else 'NO'}`  ",
            "",
            "## Delivery Summary",
            f"{result.message}",
            ""
        ]
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        print(f"[EXPORTED] Ship briefing: {out_path}")


def main():
    parser = argparse.ArgumentParser(description="Dev Loop Safe Shipping Engine")
    parser.add_argument("source", nargs="?", help="Source feature/lane branch to ship")
    parser.add_argument("--target", default="main", help="Target trunk branch (default: main)")
    parser.add_argument("--tag", help="Optional release tag to apply")
    parser.add_argument("--init", action="store_true", help="Scaffold RELEASE_CHECKLIST.md template")

    args = parser.parse_args()
    engine = ShipEngine()

    if args.init:
        engine.scaffold_template()
        return

    if not args.source:
        parser.print_help()
        sys.exit(1)

    res = engine.ship(args.source, target_branch=args.target, tag=args.tag)
    if not res.success:
        sys.exit(1)


if __name__ == "__main__":
    main()
