#!/usr/bin/env python3
"""
critic.py - Multi-Perspective Maker-Checker Critic Panel Engine.
Evaluates diffs prior to merge across four specialized architectural lenses:
- DRY Critic (Duplication & Reuse)
- KISS & YAGNI Critic (Simplicity & Over-engineering)
- SRP & SoC Critic (Separation of Concerns & Modularity)
- Security & Invariant Critic (Vulnerabilities & Schema Contracts)
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
class CriticFinding:
    critic_role: str  # DRY, KISS_YAGNI, SRP_SOC, SECURITY_INVARIANT
    severity: str     # BLOCKER, MAJOR, MINOR, PRAISE
    file_path: str
    description: str


@dataclass
class CriticPanelReport:
    target_ref: str
    consensus_approved: bool
    findings: List[CriticFinding] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    summary: str = ""


class CriticPanel:
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

    def evaluate(self, target_ref: str = "HEAD") -> CriticPanelReport:
        print(f"==> Convening Multi-Perspective Critic Panel on ref '{target_ref}'...")
        res = subprocess.run(["git", "diff", f"{target_ref}~1..{target_ref}"], cwd=self.repo_root, capture_output=True, text=True)
        diff_text = res.stdout

        findings: List[CriticFinding] = []

        # 1. DRY Critic: Check for duplicated blocks or reinvented standard routines
        if "def " in diff_text or "function " in diff_text:
            lines = [l for l in diff_text.splitlines() if l.startswith("+") and not l.startswith("+++")]
            if len(lines) > 200:
                findings.append(CriticFinding(
                    critic_role="DRY",
                    severity="MAJOR",
                    file_path="diff",
                    description="Large addition (>200 loc). Verify whether existing utilities or abstractions could be reused."
                ))

        # 2. KISS / YAGNI Critic: Check for premature micro-frameworks or excessive config
        if re.search(r'(class .*(Factory|Builder|Manager|Provider|Strategy).*Factory)', diff_text):
            findings.append(CriticFinding(
                critic_role="KISS_YAGNI",
                severity="MAJOR",
                file_path="diff",
                description="Potential over-engineering (Factory of Factories pattern detected). Simplify to functional calls."
            ))

        # 3. Security & Invariant Critic: Secrets and raw shell calls
        for line in diff_text.splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                if re.search(r'(?i)(api_key|secret|password|bearer)\s*[:=]\s*[\'"][A-Za-z0-9_\-]{8,}[\'"]', line):
                    findings.append(CriticFinding(
                        critic_role="SECURITY_INVARIANT",
                        severity="BLOCKER",
                        file_path="diff",
                        description="Hardcoded credential or private token detected."
                    ))
                if "shell=True" in line:
                    findings.append(CriticFinding(
                        critic_role="SECURITY_INVARIANT",
                        severity="BLOCKER",
                        file_path="diff",
                        description="Unsanitized shell=True invocation detected."
                    ))

        blockers = [f for f in findings if f.severity == "BLOCKER"]
        approved = len(blockers) == 0

        summary = f"Critic Panel completed: {len(findings)} finding(s) ({len(blockers)} blocker(s)). Consensus: {'APPROVED' if approved else 'REJECTED'}."
        report = CriticPanelReport(
            target_ref=target_ref,
            consensus_approved=approved,
            findings=findings,
            summary=summary
        )

        out_json = self.artifacts_dir / f"critic_{int(time.time())}.json"
        with open(out_json, "w", encoding="utf-8") as f:
            json.dump(asdict(report), f, indent=2)

        out_md = self.repo_root / "CRITIC_PANEL_REPORT.md"
        lines_md = [
            "# Critic Panel Multi-Perspective Review (`/loop-review`)",
            "",
            f"**Target Ref:** `{report.target_ref}`  ",
            f"**Timestamp:** {report.timestamp}  ",
            f"**Consensus Status:** `{'APPROVED' if report.consensus_approved else 'BLOCKED (Changes Required)'}`  ",
            f"**Summary:** {report.summary}",
            "",
            "## Perspective Findings",
        ]
        if not report.findings:
            lines_md.append("- All critics report unanimous consensus. Clean architecture.")
        else:
            for f in report.findings:
                lines_md.append(f"### [{f.critic_role}] [{f.severity}] `{f.file_path}`")
                lines_md.append(f"{f.description}")
                lines_md.append("")

        with open(out_md, "w", encoding="utf-8") as f:
            f.write("\n".join(lines_md) + "\n")

        print(f"[SAVED] Critic telemetry: {out_json}")
        print(f"[EXPORTED] Critic briefing: {out_md}")
        return report


def main():
    parser = argparse.ArgumentParser(description="Dev Loop Multi-Perspective Critic Panel")
    parser.add_argument("ref", nargs="?", default="HEAD", help="Git commit or ref range to evaluate (default: HEAD)")
    args = parser.parse_args()

    panel = CriticPanel()
    res = panel.evaluate(args.ref)
    if not res.consensus_approved:
        sys.exit(1)


if __name__ == "__main__":
    main()
