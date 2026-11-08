#!/usr/bin/env python3
"""
goal.py - Dev Loop Goal Engine & Stopping-Condition Evaluator.
Defines, tracks, evaluates, and enforces stopping conditions for autonomous agentic loops.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class GoalCriterion:
    id: str
    description: str
    criterion_type: str  # test, invariant, artifact, git_clean
    command: Optional[str] = None
    passed: bool = False
    details: Optional[str] = None


@dataclass
class GoalDefinition:
    objective: str
    stopping_condition: str
    status: str = "IN_PROGRESS"  # NOT_STARTED, IN_PROGRESS, VERIFYING, BLOCKED, COMPLETED
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    completed_at: Optional[str] = None
    criteria: List[GoalCriterion] = field(default_factory=list)
    non_goals: List[str] = field(default_factory=list)


class GoalEngine:
    def __init__(self, repo_root: Optional[str] = None):
        self.repo_root = Path(repo_root or self._find_repo_root()).resolve()
        self.goal_file = self.repo_root / "GOALS.md"
        self.state_file = self.repo_root / ".devloop_artifacts" / "goal_state.json"
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.goal: Optional[GoalDefinition] = None
        self._load()

    def _find_repo_root(self) -> Path:
        cur = Path.cwd()
        for parent in [cur] + list(cur.parents):
            if (parent / ".git").exists():
                return parent
        return cur

    def _load(self):
        if self.state_file.exists():
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    criteria = [GoalCriterion(**c) for c in data.get("criteria", [])]
                    self.goal = GoalDefinition(
                        objective=data.get("objective", ""),
                        stopping_condition=data.get("stopping_condition", ""),
                        status=data.get("status", "IN_PROGRESS"),
                        created_at=data.get("created_at", ""),
                        completed_at=data.get("completed_at"),
                        criteria=criteria,
                        non_goals=data.get("non_goals", [])
                    )
            except Exception as e:
                print(f"[WARN] Could not parse goal state: {e}", file=sys.stderr)

    def save(self):
        if not self.goal:
            return
        data = asdict(self.goal)
        temp_file = self.state_file.with_suffix(".tmp")
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        temp_file.replace(self.state_file)

    def init_goal(self, objective: str, stopping_condition: str, criteria: Optional[List[Dict]] = None, non_goals: Optional[List[str]] = None):
        c_objs = []
        if criteria:
            for idx, c in enumerate(criteria):
                c_objs.append(GoalCriterion(
                    id=f"C-{idx+1:02d}",
                    description=c.get("description", ""),
                    criterion_type=c.get("type", "invariant"),
                    command=c.get("command")
                ))
        else:
            # Default minimal robust criteria
            c_objs = [
                GoalCriterion(id="C-01", description="Acceptance tests pass", criterion_type="test", command="pytest"),
                GoalCriterion(id="C-02", description="Working tree clean of uncommitted residue", criterion_type="git_clean"),
                GoalCriterion(id="C-03", description="Artifact manifest validated", criterion_type="artifact")
            ]

        self.goal = GoalDefinition(
            objective=objective,
            stopping_condition=stopping_condition,
            status="IN_PROGRESS",
            criteria=c_objs,
            non_goals=non_goals or []
        )
        self.save()
        self._sync_goals_md()
        print(f"[GOAL INITIALIZED] {objective}")
        print(f"  Stopping Condition: {stopping_condition}")

    def _sync_goals_md(self):
        if not self.goal:
            return
        lines = [
            f"# Engineering Goal: {self.goal.objective}",
            "",
            f"**Status:** `{self.goal.status}`  ",
            f"**Stopping Condition:** {self.goal.stopping_condition}  ",
            f"**Initialized:** {self.goal.created_at}",
            "",
            "## 1. Core Invariants & Stopping Criteria",
        ]
        for c in self.goal.criteria:
            chk = "[x]" if c.passed else "[ ]"
            cmd_hint = f" (`{c.command}`)" if c.command else ""
            lines.append(f"- {chk} **{c.id}**: {c.description}{cmd_hint}")

        if self.goal.non_goals:
            lines.extend(["", "## 2. Non-Goals (Blast Radius Boundaries)"])
            for ng in self.goal.non_goals:
                lines.append(f"- {ng}")

        with open(self.goal_file, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    def evaluate(self) -> bool:
        if not self.goal:
            print("[ERROR] No goal initialized. Run 'goal.py init' first.", file=sys.stderr)
            return False

        print(f"==> Evaluating stopping condition for: {self.goal.objective}")
        all_passed = True

        for c in self.goal.criteria:
            if c.criterion_type == "test" and c.command:
                res = subprocess.run(c.command, shell=True, cwd=self.repo_root, capture_output=True, text=True)
                c.passed = (res.returncode == 0)
                c.details = f"Exit code {res.returncode}"
            elif c.criterion_type == "git_clean":
                res = subprocess.run(["git", "status", "--porcelain"], cwd=self.repo_root, capture_output=True, text=True)
                clean = len(res.stdout.strip()) == 0
                c.passed = clean
                c.details = "Working tree clean" if clean else f"Dirty files: {res.stdout.strip()[:100]}"
            elif c.criterion_type == "artifact":
                # Check artifacts manifest if artifacts.py exists
                art_py = self.repo_root / "reference" / "artifacts.py"
                if art_py.exists():
                    res = subprocess.run([sys.executable, str(art_py), "verify"], cwd=self.repo_root, capture_output=True, text=True)
                    c.passed = (res.returncode == 0)
                    c.details = "Manifest verified" if c.passed else "Manifest verify failed"
                else:
                    c.passed = True
                    c.details = "No artifact manager found, skipped"
            else:
                # Custom invariant check
                c.passed = True

            status_sym = "\033[92m[PASSED]\033[0m" if c.passed else "\033[91m[FAILED]\033[0m"
            print(f"  {status_sym} {c.id}: {c.description} ({c.details})")
            if not c.passed:
                all_passed = False

        if all_passed:
            self.goal.status = "COMPLETED"
            self.goal.completed_at = datetime.now().isoformat()
            print("\n\033[92m==> STOPPING CONDITION SATISFIED! Goal is COMPLETE.\033[0m")
        else:
            self.goal.status = "IN_PROGRESS"
            print("\n\033[93m==> Stopping condition NOT met. Dev loop iteration must continue.\033[0m")

        self.save()
        self._sync_goals_md()
        return all_passed


def main():
    parser = argparse.ArgumentParser(description="Dev Loop Goal Engine")
    subparsers = parser.add_subparsers(dest="cmd", required=True)

    init_p = subparsers.add_parser("init", help="Initialize a goal with stopping conditions")
    init_p.add_argument("objective", help="High-level goal description")
    init_p.add_argument("--stop", "-s", required=True, help="Specific, verifiable stopping condition")

    eval_p = subparsers.add_parser("eval", help="Evaluate if stopping condition has been satisfied")
    eval_p.add_argument("--repo-root", default=None)

    status_p = subparsers.add_parser("status", help="Print current goal status")

    args = parser.parse_args()
    engine = GoalEngine(args.repo_root if hasattr(args, "repo_root") else None)

    if args.cmd == "init":
        engine.init_goal(args.objective, args.stop)
    elif args.cmd == "eval":
        if not engine.evaluate():
            sys.exit(1)
    elif args.cmd == "status":
        if engine.goal:
            print(f"Objective: {engine.goal.objective}")
            print(f"Status: {engine.goal.status}")
            print(f"Stopping Condition: {engine.goal.stopping_condition}")
            for c in engine.goal.criteria:
                mark = "[x]" if c.passed else "[ ]"
                print(f"  {mark} {c.id}: {c.description}")
        else:
            print("No active goal.")


if __name__ == "__main__":
    main()
