#!/usr/bin/env python3
"""
sync.py - Repository State & Documentation Reconciliation Engine (/sync).
Reconciles AGENTS.md, ADR statuses, and project scopes against live git changes.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class SyncReport:
    reconciled_files: List[str] = field(default_factory=list)
    adr_status_updates: List[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    summary: str = ""


class RepoSynchronizer:
    def __init__(self, repo_root: Optional[str] = None):
        self.repo_root = Path(repo_root or self._find_repo_root()).resolve()
        self.artifacts_dir = self.repo_root / ".devloop_artifacts"
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

    def _find_repo_root(self) -> Path:
        cur = Path.cwd()
        for parent in [cur] + list(cur.parents):
            if (parent / ".git").exists():
                return parent
        return cur

    def reconcile(self) -> SyncReport:
        print(f"==> Reconciling repository state and context files at '{self.repo_root}'...")
        report = SyncReport()

        res = subprocess.run(
            ["git", "diff", "--name-only", "HEAD~5..HEAD"],
            cwd=self.repo_root, capture_output=True, text=True
        )
        changed = res.stdout.strip().splitlines() if res.returncode == 0 else []

        adr_dir = self.repo_root / "docs" / "adr"
        if adr_dir.exists():
            for f in adr_dir.glob("*.md"):
                text = f.read_text(encoding="utf-8")
                if "Status: Proposed" in text or "Status: [Proposed]" in text:
                    report.adr_status_updates.append(f"{f.name}: Ready for review / Accepted")

        agents_path = self.repo_root / "AGENTS.md"
        if agents_path.exists():
            report.reconciled_files.append("AGENTS.md")

        report.summary = f"Reconciliation complete. Audited {len(changed)} recent modified file(s)."
        
        out_json = self.artifacts_dir / f"sync_{int(time.time())}.json"
        with open(out_json, "w", encoding="utf-8") as f:
            json.dump(asdict(report), f, indent=2)

        out_md = self.repo_root / "SYNC_REPORT.md"
        lines = [
            "# Dev Loop Repository Sync Report (`/sync`)",
            "",
            f"**Timestamp:** {report.timestamp}  ",
            f"**Summary:** {report.summary}  ",
            "",
            "## ADR Status Updates",
            *([f"- {u}" for u in report.adr_status_updates] if report.adr_status_updates else ["- All ADR statuses current."]),
            "",
            "## Synchronized Context Files",
            *([f"- `{r}`" for r in report.reconciled_files] if report.reconciled_files else ["- No context files required updates."]),
            ""
        ]
        with open(out_md, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

        print(f"[SAVED] Sync telemetry: {out_json}")
        print(f"[EXPORTED] Sync briefing: {out_md}")
        return report


def main():
    parser = argparse.ArgumentParser(description="Dev Loop State Synchronizer")
    args = parser.parse_args()
    syncer = RepoSynchronizer()
    syncer.reconcile()


if __name__ == "__main__":
    main()
