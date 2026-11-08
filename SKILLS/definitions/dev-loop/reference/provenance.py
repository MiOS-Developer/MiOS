#!/usr/bin/env python3
"""
provenance.py - Value Provenance & Decision-Owed Gating Engine for Dev Loop.
Enforces that every displayed, computed, or transmitted value has a declared,
verifiable source before code implementation begins.
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
from typing import Dict, List, Optional


VALID_SOURCES = {"DB_COLUMN", "EXTERNAL_API", "USER_INPUT", "COMPUTED", "CONSTANT", "ENVIRONMENT"}


@dataclass
class ProvenanceField:
    name: str
    target_surface: str
    source_type: str
    source_origin: str
    formula_or_notes: str = ""
    is_decided: bool = True


@dataclass
class ProvenanceReport:
    feature_name: str
    spec_path: str
    total_fields: int
    decided_fields: int
    owed_decisions: int
    fields: List[ProvenanceField] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    status: str = "PASSED"


class ProvenanceEngine:
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

    def parse_markdown_table(self, file_path: Path) -> List[ProvenanceField]:
        if not file_path.exists():
            return []

        content = file_path.read_text(encoding="utf-8")
        fields: List[ProvenanceField] = []
        in_table = False
        headers = []

        for line in content.splitlines():
            line_str = line.strip()
            if not line_str.startswith("|"):
                in_table = False
                continue

            cols = [c.strip() for c in line_str.split("|")[1:-1]]
            if not in_table:
                header_line = [c.lower() for c in cols]
                if any("field" in h or "property" in h for h in header_line) and any("source" in h for h in header_line):
                    in_table = True
                    headers = header_line
                continue

            if in_table:
                if all(re.match(r"^:?-+:?$", c) for c in cols):
                    continue

                row = dict(zip(headers, cols))
                field_name = row.get("field", "") or row.get("property", "") or row.get("name", "")
                if not field_name:
                    continue

                target = row.get("target", "") or row.get("surface", "") or "API_RESPONSE"
                src_val = row.get("source", "") or row.get("origin", "")
                notes = row.get("formula", "") or row.get("notes", "") or row.get("rule", "")

                src_type = "UNDECIDED"
                src_origin = src_val

                for s in VALID_SOURCES:
                    if s in src_val.upper():
                        src_type = s
                        break

                is_decided = src_type != "UNDECIDED" and not any(
                    token in src_val.lower() for token in ["tbd", "todo", "unknown", "undecided", "owed", "guess"]
                )

                fields.append(ProvenanceField(
                    name=field_name,
                    target_surface=target,
                    source_type=src_type if is_decided else "UNDECIDED",
                    source_origin=src_origin,
                    formula_or_notes=notes,
                    is_decided=is_decided
                ))

        return fields

    def audit_provenance(self, spec_path: str, feature_name: str = "Feature") -> ProvenanceReport:
        p = Path(spec_path)
        if not p.is_absolute():
            p = self.repo_root / p

        fields = self.parse_markdown_table(p)
        owed = [f for f in fields if not f.is_decided]
        status = "PASSED" if len(owed) == 0 else "DECISION_OWED"

        report = ProvenanceReport(
            feature_name=feature_name,
            spec_path=str(p.relative_to(self.repo_root)) if p.is_relative_to(self.repo_root) else str(p),
            total_fields=len(fields),
            decided_fields=len(fields) - len(owed),
            owed_decisions=len(owed),
            fields=fields,
            status=status
        )

        self._save_report(report)
        self._export_markdown(report)
        return report

    def _save_report(self, report: ProvenanceReport):
        path = self.artifacts_dir / f"provenance_{int(time.time())}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(report), f, indent=2)
        print(f"[SAVED] Provenance telemetry: {path}")

    def _export_markdown(self, report: ProvenanceReport):
        out_path = self.repo_root / "DECISIONS_OWED.md"
        lines = [
            f"# Value Provenance & Decision-Owed Audit: {report.feature_name}",
            "",
            f"**Specification File:** `{report.spec_path}`  ",
            f"**Timestamp:** {report.timestamp}  ",
            f"**Status:** `[{report.status}]`  ",
            f"**Fields Audited:** {report.total_fields} | **Decided:** {report.decided_fields} | **Decisions Owed:** {report.owed_decisions}",
            "",
            "## Audit Summary",
        ]

        if report.status == "PASSED":
            lines.append("All exposed values and attributes have verified, declared sources. Ready for implementation.")
        else:
            lines.append("Execution is **GATED**. The following fields have no declared architectural source:")
            lines.append("")
            lines.append("| Field Name | Surface | Current Source Claim | Required Action |")
            lines.append("| :--- | :--- | :--- | :--- |")
            for f in report.fields:
                if not f.is_decided:
                    lines.append(f"| `{f.name}` | `{f.target_surface}` | `{f.source_origin}` | Define source in ADR/GOALS.md |")
            lines.append("")
            lines.append("**Rule:** An AI agent MUST NOT invent data models, database columns, or mock APIs on the fly. Resolve these decisions in an ADR before proceeding.")

        lines.append("")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(chr(10).join(lines) + chr(10))
        print(f"[EXPORTED] Provenance briefing: {out_path}")


def main():
    parser = argparse.ArgumentParser(description="Dev Loop Value Provenance & Decision-Owed Engine")
    parser.add_argument("spec", nargs="?", default="GOALS.md", help="Path to specification file (default: GOALS.md)")
    parser.add_argument("--feature", default="Feature", help="Feature name")
    parser.add_argument("--strict", action="store_true", help="Exit with non-zero code if decisions are owed")

    args = parser.parse_args()
    engine = ProvenanceEngine()
    report = engine.audit_provenance(args.spec, feature_name=args.feature)

    if args.strict and report.status != "PASSED":
        print(f"\n[GATED] {report.owed_decisions} decision(s) owed. Halting build.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
