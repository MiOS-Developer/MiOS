#!/usr/bin/env python3
"""
research.py - Dev Loop Upstream Research & Web Discovery Engine (/research, /rs, /websearch, /s).
Automates upstream repository analysis, tag/SHA diffing, breaking change audits, and research briefings.
Includes template scaffolding for research spikes, upstream audits, and technology evaluations.
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
class UpstreamFinding:
    category: str  # breaking_change, deprecation, feature, security, invariant
    title: str
    description: str
    affected_files: List[str] = field(default_factory=list)
    action_required: bool = True


@dataclass
class ResearchReport:
    topic: str
    query: str
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    findings: List[UpstreamFinding] = field(default_factory=list)
    summary: str = ""
    sources: List[str] = field(default_factory=list)


class UpstreamResearcher:
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

    def scaffold_template(self, template_type: str, dest_name: Optional[str] = None) -> Path:
        mapping = {
            "spike": ("SPIKE.md", "SPIKE.md"),
            "upstream": ("UPSTREAM_AUDIT.md", "UPSTREAM_AUDIT.md"),
            "eval": ("TECH_EVAL.md", "TECH_EVAL.md"),
        }
        if template_type.lower() not in mapping:
            raise ValueError(f"Unknown template type: {template_type}. Choose from: {list(mapping.keys())}")

        src_name, default_dest = mapping[template_type.lower()]
        target_name = dest_name or default_dest
        src_path = self.script_dir / "templates" / src_name
        dest_path = self.repo_root / target_name

        if not src_path.exists():
            src_path = self.repo_root / "reference" / "templates" / src_name

        if not src_path.exists():
            print(f"[ERROR] Template source not found: {src_path}", file=sys.stderr)
            sys.exit(1)

        shutil.copy2(src_path, dest_path)
        print(f"[SCAFFOLDED] Created {dest_path.name} at {dest_path}")
        return dest_path

    def search_docs(self, query: str, engine: str = "web") -> ResearchReport:
        print(f"==> Searching upstream knowledge base for: '{query}' (Engine: {engine})...")
        report = ResearchReport(topic="websearch", query=query)
        
        doc_matches = []
        for doc_dir in ["docs", "doc", "reference", "templates"]:
            target_dir = self.repo_root / doc_dir
            if target_dir.exists():
                for root, _, files in os.walk(target_dir):
                    for f in files:
                        if f.endswith((".md", ".txt", ".toml", ".json")):
                            p = Path(root) / f
                            try:
                                text = p.read_text(encoding="utf-8", errors="ignore")
                                if query.lower() in text.lower():
                                    doc_matches.append(str(p.relative_to(self.repo_root)))
                            except Exception:
                                pass

        report.sources = doc_matches
        if doc_matches:
            report.findings.append(UpstreamFinding(
                category="documentation",
                title=f"Local documentation matches for '{query}'",
                description=f"Found {len(doc_matches)} matching local docs.",
                affected_files=doc_matches[:5],
                action_required=False
            ))
            report.summary = f"Found {len(doc_matches)} repository reference(s) matching query."
        else:
            report.summary = f"No local reference found. External web search suggested for '{query}'."

        self._save_report(report, f"search_{int(time.time())}.json")
        self._export_markdown(report)
        return report

    def diff_upstream(self, base_ref: str, upstream_ref: str) -> ResearchReport:
        print(f"==> Analyzing upstream diff between '{base_ref}' and '{upstream_ref}'...")
        report = ResearchReport(topic="upstream_diff", query=f"{base_ref}..{upstream_ref}")

        res = subprocess.run(
            ["git", "diff", "--name-status", f"{base_ref}..{upstream_ref}"],
            cwd=self.repo_root, capture_output=True, text=True
        )

        if res.returncode != 0:
            print(f"[ERROR] git diff failed: {res.stderr}", file=sys.stderr)
            report.summary = f"Diff failed: {res.stderr.strip()}"
            return report

        changed_files = res.stdout.strip().splitlines()
        report.sources = [f"git diff {base_ref}..{upstream_ref}"]

        breaking = []
        modified = []
        for line in changed_files:
            parts = line.split("\t")
            if len(parts) >= 2:
                status, path = parts[0], parts[1]
                if status.startswith("D"):
                    breaking.append(path)
                else:
                    modified.append(path)

        if breaking:
            report.findings.append(UpstreamFinding(
                category="breaking_change",
                title=f"Deleted upstream files ({len(breaking)})",
                description="Upstream files removed; callers may encounter missing references.",
                affected_files=breaking,
                action_required=True
            ))

        if modified:
            report.findings.append(UpstreamFinding(
                category="modification",
                title=f"Modified upstream files ({len(modified)})",
                description="Upstream implementations updated. Review contracts.",
                affected_files=modified[:10],
                action_required=False
            ))

        report.summary = f"Upstream diff analysis complete: {len(breaking)} deletions, {len(modified)} modifications."
        self._save_report(report, f"upstream_diff_{int(time.time())}.json")
        self._export_markdown(report)
        return report

    def _save_report(self, report: ResearchReport, filename: str):
        path = self.artifacts_dir / filename
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(report), f, indent=2)
        print(f"[SAVED] Telemetry report: {path}")

    def _export_markdown(self, report: ResearchReport):
        brief_path = self.repo_root / "UPSTREAM_BRIEF.md"
        lines = [
            f"# Upstream Research Brief: {report.topic}",
            "",
            f"**Query/Target:** `{report.query}`  ",
            f"**Timestamp:** {report.timestamp}  ",
            f"**Summary:** {report.summary}",
            "",
            "## Findings",
        ]
        if not report.findings:
            lines.append("- No critical findings recorded.")
        for find in report.findings:
            lines.append(f"### [{find.category.upper()}] {find.title}")
            lines.append(f"{find.description}")
            if find.affected_files:
                lines.append("**Affected Files:**")
                for aff in find.affected_files:
                    lines.append(f"- `{aff}`")
            lines.append("")

        with open(brief_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        print(f"[EXPORTED] Research brief: {brief_path}")


def main():
    parser = argparse.ArgumentParser(description="Dev Loop Upstream Research & Discovery Engine")
    subparsers = parser.add_subparsers(dest="cmd", required=True)

    search_p = subparsers.add_parser("search", aliases=["s"], help="Search documentation or error codes")
    search_p.add_argument("query", help="Search query")

    diff_p = subparsers.add_parser("diff", aliases=["upstream", "rs"], help="Diff against upstream git ref/tag")
    diff_p.add_argument("base", help="Base commit or branch (e.g., HEAD~5, v1.0.0)")
    diff_p.add_argument("upstream", default="HEAD", nargs="?", help="Target upstream commit or branch")

    templ_p = subparsers.add_parser("template", aliases=["scaffold"], help="Scaffold a research document template")
    templ_p.add_argument("type", choices=["spike", "upstream", "eval"], help="Template type to scaffold")
    templ_p.add_argument("--dest", help="Custom destination filename")

    args = parser.parse_args()
    researcher = UpstreamResearcher()

    if args.cmd in ["search", "s"]:
        researcher.search_docs(args.query)
    elif args.cmd in ["diff", "upstream", "rs"]:
        researcher.diff_upstream(args.base, args.upstream)
    elif args.cmd in ["template", "scaffold"]:
        researcher.scaffold_template(args.type, args.dest)


if __name__ == "__main__":
    main()
