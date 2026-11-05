#!/usr/bin/env python3
"""
audit.py - Brownfield Repository Inspection & Hierarchical Context Generator (/audit).
Operates on legacy and modern codebases across any programming language.
Discovers toolchains, dependencies, and conventions, seeding lean, hierarchical AGENTS.md files
while strictly preserving manual human developer edits.
"""

import argparse
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set


USER_CONTEXT_START = "<!-- USER CONTEXT START -->"
USER_CONTEXT_END = "<!-- USER CONTEXT END -->"


@dataclass
class DetectedToolchain:
    primary_language: str
    languages: List[str] = field(default_factory=list)
    package_managers: List[str] = field(default_factory=list)
    frameworks: List[str] = field(default_factory=list)
    test_runners: List[str] = field(default_factory=list)
    linters: List[str] = field(default_factory=list)
    entrypoints: List[str] = field(default_factory=list)
    workspaces: List[str] = field(default_factory=list)


@dataclass
class AuditReport:
    repo_root: str
    is_monorepo: bool
    toolchain: DetectedToolchain
    files_audited: int
    context_files_generated: List[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


class RepositoryAuditor:
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

    def detect_toolchain(self) -> DetectedToolchain:
        tc = DetectedToolchain(primary_language="Unknown")
        langs: Set[str] = set()
        pms: Set[str] = set()
        fws: Set[str] = set()
        tests: Set[str] = set()
        lints: Set[str] = set()
        entries: List[str] = []
        workspaces: List[str] = []

        if (self.repo_root / "package.json").exists():
            langs.add("JavaScript/TypeScript")
            pms.add("npm")
            if (self.repo_root / "pnpm-lock.yaml").exists(): pms.add("pnpm")
            if (self.repo_root / "yarn.lock").exists(): pms.add("yarn")
            if (self.repo_root / "bun.lockb").exists() or (self.repo_root / "bun.lock").exists(): pms.add("bun")

            try:
                pkg = json.loads((self.repo_root / "package.json").read_text(encoding="utf-8"))
                deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
                if "next" in deps: fws.add("Next.js")
                if "react" in deps: fws.add("React")
                if "vue" in deps: fws.add("Vue")
                if "express" in deps: fws.add("Express")
                if "nestjs" in deps or "@nestjs/core" in deps: fws.add("NestJS")
                if "jest" in deps: tests.add("jest")
                if "vitest" in deps: tests.add("vitest")
                if "eslint" in deps: lints.add("eslint")
                if "prettier" in deps: lints.add("prettier")
                if "workspaces" in pkg:
                    tc.workspaces = pkg["workspaces"] if isinstance(pkg["workspaces"], list) else []
            except Exception:
                pass

        if (self.repo_root / "pyproject.toml").exists() or (self.repo_root / "requirements.txt").exists() or (self.repo_root / "Pipfile").exists():
            langs.add("Python")
            if (self.repo_root / "poetry.lock").exists(): pms.add("poetry")
            elif (self.repo_root / "Pipfile.lock").exists(): pms.add("pipenv")
            elif (self.repo_root / "uv.lock").exists(): pms.add("uv")
            else: pms.add("pip")

            if (self.repo_root / "pyproject.toml").exists():
                text = (self.repo_root / "pyproject.toml").read_text(encoding="utf-8", errors="ignore")
                if "fastapi" in text: fws.add("FastAPI")
                if "django" in text: fws.add("Django")
                if "flask" in text: fws.add("Flask")
                if "pytest" in text: tests.add("pytest")
                if "ruff" in text: lints.add("ruff")
                if "black" in text: lints.add("black")

        if (self.repo_root / "Cargo.toml").exists():
            langs.add("Rust")
            pms.add("cargo")
            tests.add("cargo test")
            lints.add("clippy")

        if (self.repo_root / "go.mod").exists():
            langs.add("Go")
            pms.add("go modules")
            tests.add("go test")

        if (self.repo_root / "composer.json").exists():
            langs.add("PHP")
            pms.add("composer")
            tests.add("phpunit")

        if "Python" in langs: tc.primary_language = "Python"
        elif "JavaScript/TypeScript" in langs: tc.primary_language = "TypeScript"
        elif "Rust" in langs: tc.primary_language = "Rust"
        elif "Go" in langs: tc.primary_language = "Go"
        elif "PHP" in langs: tc.primary_language = "PHP"

        tc.languages = sorted(list(langs))
        tc.package_managers = sorted(list(pms))
        tc.frameworks = sorted(list(fws))
        tc.test_runners = sorted(list(tests))
        tc.linters = sorted(list(lints))
        tc.entrypoints = entries
        return tc

    def generate_agents_md(self, toolchain: DetectedToolchain) -> Path:
        agents_path = self.repo_root / "AGENTS.md"
        user_notes = ""

        if agents_path.exists():
            existing = agents_path.read_text(encoding="utf-8")
            match = re.search(f"{re.escape(USER_CONTEXT_START)}(.*?){re.escape(USER_CONTEXT_END)}", existing, re.DOTALL)
            if match:
                user_notes = match.group(1).strip()

        commands_summary = []
        if "pytest" in toolchain.test_runners: commands_summary.append("- Test: `pytest`")
        elif "vitest" in toolchain.test_runners: commands_summary.append("- Test: `pnpm test` (or `npm test`)")
        elif "cargo test" in toolchain.test_runners: commands_summary.append("- Test: `cargo test`")
        elif "go test" in toolchain.test_runners: commands_summary.append("- Test: `go test ./...`")
        else: commands_summary.append("- Test: Run project verification suite")

        if "ruff" in toolchain.linters: commands_summary.append("- Lint: `ruff check .`")
        elif "eslint" in toolchain.linters: commands_summary.append("- Lint: `npm run lint`")

        lines = [
            "# AGENTS.md - Repository Operational Context",
            "",
            "This file defines non-negotiable conventions, commands, and boundaries for AI agents.",
            "",
            "## 1. Project Overview & Architecture",
            f"- **Primary Language:** {toolchain.primary_language}",
            f"- **Languages Detected:** {', '.join(toolchain.languages) if toolchain.languages else 'None'}",
            f"- **Package Manager:** {', '.join(toolchain.package_managers) if toolchain.package_managers else 'Standard'}",
            f"- **Frameworks:** {', '.join(toolchain.frameworks) if toolchain.frameworks else 'None'}",
            f"- **Workspaces:** {'Monorepo (' + ', '.join(toolchain.workspaces) + ')' if toolchain.workspaces else 'Single root repository'}",
            "",
            "## 2. Standard Development Commands",
            *commands_summary,
            "",
            "## 3. Engineering Conventions & Invariants",
            "- **Value Provenance:** Every displayed or returned field MUST declare an architectural source (`reference/provenance.py`).",
            "- **Worktree Isolation:** Concurrent features run in dedicated git worktrees.",
            "- **Two-Sided Verification:** Both target functionality and non-regression checks must pass.",
            "- **Stopping Conditions:** Honor `GOALS.md` definitions before marking tasks complete.",
            "",
            f"{USER_CONTEXT_START}",
            "### User-Specified Rules & Team Context (Preserved across audits)",
            user_notes if user_notes else "- Add custom team rules, architecture guidelines, or invariants here.",
            f"{USER_CONTEXT_END}",
            ""
        ]

        agents_path.write_text("\n".join(lines), encoding="utf-8")
        print(f"[AUDIT] Generated/updated: {agents_path}")
        return agents_path

    def run_audit(self) -> AuditReport:
        print(f"==> Auditing repository at '{self.repo_root}'...")
        tc = self.detect_toolchain()
        is_mono = len(tc.workspaces) > 0
        agents_file = self.generate_agents_md(tc)

        report = AuditReport(
            repo_root=str(self.repo_root),
            is_monorepo=is_mono,
            toolchain=tc,
            files_audited=sum(len(f) for _, _, f in os.walk(self.repo_root) if not any(x in _ for x in [".git", "node_modules", "venv", ".venv"])),
            context_files_generated=[str(agents_file.relative_to(self.repo_root))]
        )

        out_json = self.artifacts_dir / f"audit_{int(time.time())}.json"
        with open(out_json, "w", encoding="utf-8") as f:
            json.dump(asdict(report), f, indent=2)

        brief_path = self.repo_root / "AUDIT_REPORT.md"
        brief_lines = [
            "# Dev Loop Repository Audit Report (`/audit`)",
            "",
            f"**Repository Root:** `{report.repo_root}`  ",
            f"**Primary Stack:** {tc.primary_language} ({', '.join(tc.frameworks)})  ",
            f"**Monorepo:** {'Yes' if is_mono else 'No'}  ",
            f"**Timestamp:** {report.timestamp}  ",
            "",
            "## Detected Toolchain",
            f"- **Languages:** {', '.join(tc.languages)}",
            f"- **Package Managers:** {', '.join(tc.package_managers)}",
            f"- **Test Runners:** {', '.join(tc.test_runners)}",
            f"- **Linters:** {', '.join(tc.linters)}",
            "",
            "## Generated Artifacts",
            f"- Seeded context: `{report.context_files_generated}`",
            ""
        ]
        with open(brief_path, "w", encoding="utf-8") as f:
            f.write("\n".join(brief_lines) + "\n")

        print(f"[SAVED] Audit telemetry: {out_json}")
        print(f"[EXPORTED] Audit briefing: {brief_path}")
        return report


def main():
    parser = argparse.ArgumentParser(description="Dev Loop Brownfield Repository Auditor")
    args = parser.parse_args()
    auditor = RepositoryAuditor()
    auditor.run_audit()


if __name__ == "__main__":
    main()
