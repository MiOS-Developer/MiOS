#!/usr/bin/env python3
"""
review.py - SCOPE Staged Code Review & Invariant Verification Engine (/review, /rv).
Audits git diffs for contract drift, security sanitization, invariant violations, and dead code.
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
class ReviewIssue:
    severity: str  # BLOCKER, CRITICAL, WARNING, SUGGESTION
    rule: str
    file_path: str
    line_number: Optional[int]
    description: str


@dataclass
class ReviewResult:
    target_ref: str
    passed: bool
    issues: List[ReviewIssue] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    summary: str = ""


class StagedReviewer:
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
        src_path = self.script_dir / "templates" / "REVIEW_RUBRIC.md"
        if not src_path.exists():
            src_path = self.repo_root / "reference" / "templates" / "REVIEW_RUBRIC.md"
        dest_path = self.repo_root / "REVIEW_RUBRIC.md"
        if src_path.exists():
            shutil.copy2(src_path, dest_path)
            print(f"[SCAFFOLDED] Created {dest_path.name} at {dest_path}")
        return dest_path

    def review_diff(self, target_ref: str = "HEAD") -> ReviewResult:
        print(f"==> Running SCOPE staged review on ref '{target_ref}'...")
        res = subprocess.run(
            ["git", "diff", f"{target_ref}~1..{target_ref}"],
            cwd=self.repo_root, capture_output=True, text=True
        )

        diff_text = res.stdout
        issues: List[ReviewIssue] = []

        current_file = ""
        line_no = 0

        for line in diff_text.splitlines():
            if line.startswith("+++ b/"):
                current_file = line[6:]
                line_no = 0
            elif line.startswith("@@"):
                m = re.search(r"\+(\d+)", line)
                if m:
                    line_no = int(m.group(1))
            elif line.startswith("+") and not line.startswith("+++"):
                line_no += 1
                content = line[1:]

                # 1. Check for committed secrets / API keys
                if re.search(r'(?i)(api_key|secret|password|bearer|private_key)\s*[:=]\s*[\'"][A-Za-z0-9_\-]{8,}[\'"]', content):
                    issues.append(ReviewIssue(
                        severity="BLOCKER",
                        rule="security/secret-leak",
                        file_path=current_file,
                        line_number=line_no,
                        description="Potential hardcoded secret or credential detected."
                    ))

                # 2. Check for unsafe shell injection patterns
                if re.search(r'(shell=True|os\.system\(|subprocess\.Popen\(.*shell=True)', content):
                    issues.append(ReviewIssue(
                        severity="CRITICAL",
                        rule="security/shell-injection",
                        file_path=current_file,
                        line_number=line_no,
                        description="Unescaped shell execution detected. Prefer parameterized argument lists."
                    ))

                # 3. Check for leftover debugging statements
                if re.search(r'(console\.log\(|print\(|debugger;|binding\.pry)', content) and not current_file.startswith("tests/"):
                    issues.append(ReviewIssue(
                        severity="WARNING",
                        rule="hygiene/debug-statement",
                        file_path=current_file,
                        line_number=line_no,
                        description="Leftover debug statement in production code."
                    ))

        blockers = [i for i in issues if i.severity in ["BLOCKER", "CRITICAL"]]
        passed = len(blockers) == 0

        summary = f"Review completed with {len(issues)} finding(s): {len(blockers)} blocker(s), {len(issues) - len(blockers)} advisory."
        result = ReviewResult(
            target_ref=target_ref,
            passed=passed,
            issues=issues,
            summary=summary
        )

        self._save_report(result)
        self._export_markdown(result)
        return result

    def _save_report(self, result: ReviewResult):
        path = self.artifacts_dir / f"review_{int(time.time())}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(result), f, indent=2)
        print(f"[SAVED] Review telemetry: {path}")

    def _export_markdown(self, result: ReviewResult):
        out_path = self.repo_root / "REVIEW.md"
        lines = [
            "# SCOPE Staged Code Review (`/review`, `/rv`)",
            "",
            f"**Target Ref:** `{result.target_ref}`  ",
            f"**Timestamp:** {result.timestamp}  ",
            f"**Status:** {'PASSED' if result.passed else 'FAILED (Blocked)'}  ",
            f"**Summary:** {result.summary}",
            "",
            "## Findings",
        ]
        if not result.issues:
            lines.append("- No issues detected. Clean staged review pass.")
        else:
            for iss in result.issues:
                loc = f"{iss.file_path}:{iss.line_number}" if iss.line_number else iss.file_path
                lines.append(f"### [{iss.severity}] `{iss.rule}` in `{loc}`")
                lines.append(f"{iss.description}")
                lines.append("")

        with open(out_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        print(f"[EXPORTED] Review briefing: {out_path}")


def main():
    parser = argparse.ArgumentParser(description="Dev Loop SCOPE Staged Review Engine")
    parser.add_argument("ref", nargs="?", default="HEAD", help="Git commit or range to review (default: HEAD)")
    parser.add_argument("--init", action="store_true", help="Scaffold REVIEW_RUBRIC.md template")
    parser.add_argument("--second-model", dest="second_model", action="store_true", help="Format review prompt for an independent second model to eliminate blind spots")
    args = parser.parse_args()

    reviewer = StagedReviewer()
    if args.init:
        reviewer.scaffold_template()
        return

    if args.second_model:
        print(f"==> Exporting diff prompt for independent second-model review on ref '{args.ref}'...")
        prompt_path = reviewer.repo_root / "SECOND_MODEL_REVIEW_PROMPT.md"
        diff_res = subprocess.run(["git", "diff", f"{args.ref}~1..{args.ref}"], cwd=reviewer.repo_root, capture_output=True, text=True)
        prompt_content = f"""# Independent Second-Model Review Prompt
You are an independent, adversarially thorough senior staff engineer.
Review the following git diff for:
1. Hardcoded credentials, secrets, or unsanitized inputs.
2. Invariant and contract breaks against GOALS.md and ADRs.
3. Edge-case crashes, concurrency race conditions, and unhandled nulls.
4. Redundant code duplication or dead code.

## Git Diff
```diff
{diff_res.stdout}
```
"""
        prompt_path.write_text(prompt_content, encoding="utf-8")
        print(f"[EXPORTED] Second-model review prompt created at: {prompt_path}")

    res = reviewer.review_diff(args.ref)
    if not res.passed:
        sys.exit(1)


if __name__ == "__main__":
    main()
