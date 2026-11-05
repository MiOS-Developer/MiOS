#!/usr/bin/env python3
"""
triage.py - Automated Phantom-Failure Triage & Flakiness Isolation Engine (/triage, /tr).
Distinguishes deterministic code bugs, flaky tests, and phantom environment failures.
Generates minimal standalone reproduction scripts.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class TriageClassification:
    failure_type: str  # DETERMINISTIC_BUG, FLAKY_TEST, PHANTOM_ENVIRONMENT, INDEX_LOCK, RESOURCE_STARVATION
    confidence: float
    root_cause_summary: str
    reproduction_steps: List[str] = field(default_factory=list)
    remediation_recommendation: str = ""
    repro_script_path: Optional[str] = None


@dataclass
class TriageReport:
    command_executed: str
    exit_code: int
    classification: Optional[TriageClassification] = None
    stdout_sample: str = ""
    stderr_sample: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


class FailureTriager:
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
        src_path = self.script_dir / "templates" / "TRIAGE_INCIDENT.md"
        if not src_path.exists():
            src_path = self.repo_root / "reference" / "templates" / "TRIAGE_INCIDENT.md"
        dest_path = self.repo_root / "INCIDENT_TRIAGE.md"
        if src_path.exists():
            shutil.copy2(src_path, dest_path)
            print(f"[SCAFFOLDED] Created {dest_path.name} at {dest_path}")
        return dest_path

    def triage_command(self, test_cmd: str, iterations: int = 3) -> TriageReport:
        print(f"==> Triaging command across {iterations} iteration(s): '{test_cmd}'...")
        results = []
        for i in range(iterations):
            p = subprocess.run(test_cmd, shell=True, cwd=self.repo_root, capture_output=True, text=True)
            results.append((p.returncode, p.stdout, p.stderr))
            if p.returncode == 0 and iterations > 1:
                time.sleep(0.5)

        first_rc, first_out, first_err = results[0]
        report = TriageReport(
            command_executed=test_cmd,
            exit_code=first_rc,
            stdout_sample=first_out[-1000:] if first_out else "",
            stderr_sample=first_err[-1000:] if first_err else ""
        )

        return_codes = [r[0] for r in results]
        stderr_combined = " ".join([r[2] for r in results])
        stdout_combined = " ".join([r[1] for r in results])
        full_output = (stderr_combined + " " + stdout_combined).lower()

        if any("index.lock" in full_output for _ in [1]):
            clf = TriageClassification(
                failure_type="INDEX_LOCK",
                confidence=0.98,
                root_cause_summary="Git index lock contention (.git/index.lock present).",
                remediation_recommendation="Remove stale .git/index.lock and retry with exponential backoff."
            )
        elif any(term in full_output for term in ["enospc", "out of memory", "oom", "resource temporarily unavailable"]):
            clf = TriageClassification(
                failure_type="RESOURCE_STARVATION",
                confidence=0.95,
                root_cause_summary="System resource exhaustion (memory, disk space, or process limits).",
                remediation_recommendation="Prune temporary worktrees, free RAM/disk, and reduce worker concurrency."
            )
        elif 0 in return_codes and any(rc != 0 for rc in return_codes):
            clf = TriageClassification(
                failure_type="FLAKY_TEST",
                confidence=0.90,
                root_cause_summary=f"Non-deterministic test outcome across {iterations} runs (Exit codes: {return_codes}).",
                remediation_recommendation="Isolate race condition, async timeout, or unmocked external dependency."
            )
        elif all(rc != 0 for rc in return_codes):
            repro_path = self._generate_repro_script(test_cmd)
            clf = TriageClassification(
                failure_type="DETERMINISTIC_BUG",
                confidence=0.92,
                root_cause_summary="Persistent deterministic failure reproducible across all test runs.",
                remediation_recommendation="Inspect failure stack trace, verify assertion invariants, and patch logic.",
                repro_script_path=str(repro_path.relative_to(self.repo_root)) if repro_path else None
            )
        else:
            clf = TriageClassification(
                failure_type="CLEAN_PASS",
                confidence=1.0,
                root_cause_summary="Command completed with exit code 0 on all executions.",
                remediation_recommendation="No action needed."
            )

        report.classification = clf
        self._save_report(report)
        self._export_markdown(report)
        return report

    def _generate_repro_script(self, test_cmd: str) -> Path:
        repro_path = self.repo_root / "repro.sh"
        content = f"""#!/usr/bin/env bash
# Standalone reproduction script generated by Dev Loop /triage
set -euo pipefail
echo "==> Reproducing failure for: {test_cmd}"
{test_cmd}
"""
        repro_path.write_text(content, encoding="utf-8")
        repro_path.chmod(0o755)
        return repro_path

    def _save_report(self, report: TriageReport):
        path = self.artifacts_dir / f"triage_{int(time.time())}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(report), f, indent=2)
        print(f"[SAVED] Triage telemetry: {path}")

    def _export_markdown(self, report: TriageReport):
        out_path = self.repo_root / "TRIAGE.md"
        clf = report.classification
        lines = [
            "# Dev Loop Triage Report (`/triage`, `/tr`)",
            "",
            f"**Command:** `{report.command_executed}`  ",
            f"**Timestamp:** {report.timestamp}  ",
            f"**Exit Code:** `{report.exit_code}`  ",
            f"**Classification:** `{clf.failure_type}` (Confidence: {clf.confidence * 100:.0f}%)",
            "",
            "## Root Cause Analysis",
            f"{clf.root_cause_summary}",
            "",
            "## Recommended Remediation",
            f"{clf.remediation_recommendation}",
        ]
        if clf.repro_script_path:
            lines.extend([
                "",
                "## Minimal Standalone Reproduction",
                f"- Repro script created at: `{clf.repro_script_path}`",
                f"- Run independently via: `./{clf.repro_script_path}`"
            ])
        lines.append("")

        with open(out_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        print(f"[EXPORTED] Triage briefing: {out_path}")


def main():
    parser = argparse.ArgumentParser(description="Dev Loop Phantom-Failure Triage Engine")
    parser.add_argument("command", nargs="?", help="The test or verification command that failed")
    parser.add_argument("--runs", type=int, default=3, help="Number of repetitions to evaluate non-determinism")
    parser.add_argument("--init", action="store_true", help="Scaffold INCIDENT_TRIAGE.md template")

    args = parser.parse_args()
    triager = FailureTriager()

    if args.init:
        triager.scaffold_template()
        return

    if not args.command:
        parser.print_help()
        sys.exit(1)

    triager.triage_command(args.command, iterations=args.runs)


if __name__ == "__main__":
    main()
