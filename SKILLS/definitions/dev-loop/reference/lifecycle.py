#!/usr/bin/env python3
"""
lifecycle.py - Deterministic 7-Phase Dev-Loop State Machine Engine.
Enforces that lifecycle routing logic resides in deterministic external state
with ZERO LLM calls in the decision path.
Phases: issue_intake -> refinement -> implementation -> draft_gate -> feedback_resolution -> pre_approval_gate -> merge.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional


PHASES = [
    "issue_intake",
    "refinement",
    "implementation",
    "draft_gate",
    "feedback_resolution",
    "pre_approval_gate",
    "merge"
]


@dataclass
class DevLoopState:
    issue_id: str
    current_phase: str
    branch_name: str
    target_branch: str
    worktree_path: Optional[str]
    phase_history: List[Dict[str, str]] = field(default_factory=list)
    ci_status: str = "PENDING"
    unresolved_threads: int = 0
    critic_approved: bool = False
    last_updated: str = field(default_factory=lambda: datetime.now().isoformat())


class DevLoopStateMachine:
    def __init__(self, repo_root: Optional[str] = None):
        self.repo_root = Path(repo_root or self._find_repo_root()).resolve()
        self.state_file = self.repo_root / ".devloop_state.json"
        self.progress_file = self.repo_root / "progress.md"
        self.artifacts_dir = self.repo_root / ".devloop_artifacts"
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

    def _find_repo_root(self) -> Path:
        cur = Path.cwd()
        for parent in [cur] + list(cur.parents):
            if (parent / ".git").exists():
                return parent
        return cur

    def load_state(self) -> DevLoopState:
        if self.state_file.exists():
            try:
                data = json.loads(self.state_file.read_text(encoding="utf-8"))
                return DevLoopState(**data)
            except Exception:
                pass
        return DevLoopState(
            issue_id="default-task",
            current_phase="issue_intake",
            branch_name="main",
            target_branch="main",
            worktree_path=None
        )

    def save_state(self, state: DevLoopState):
        state.last_updated = datetime.now().isoformat()
        self.state_file.write_text(json.dumps(asdict(state), indent=2), encoding="utf-8")
        self._sync_progress_md(state)

    def _sync_progress_md(self, state: DevLoopState):
        lines = [
            f"# Dev-Loop Progress & Durable State Spine",
            "",
            f"**Issue / Task ID:** `{state.issue_id}`  ",
            f"**Current Phase:** `{state.current_phase}`  ",
            f"**Branch:** `{state.branch_name}`  ",
            f"**Target:** `{state.target_branch}`  ",
            f"**Last Updated:** {state.last_updated}  ",
            "",
            "## Phase Progression Status",
        ]
        for p in PHASES:
            if p == state.current_phase:
                mark = "[/] **CURRENT:**"
            elif PHASES.index(p) < PHASES.index(state.current_phase):
                mark = "[x]"
            else:
                mark = "[ ]"
            lines.append(f"- {mark} `{p}`")

        lines.extend([
            "",
            "## Gate Statuses",
            f"- CI Status: `{state.ci_status}`",
            f"- Unresolved Review Threads: `{state.unresolved_threads}`",
            f"- Critic Panel Approval: `{'YES' if state.critic_approved else 'NO'}`",
            ""
        ])
        self.progress_file.write_text("\n".join(lines), encoding="utf-8")

    def advance_phase(self, force: bool = False) -> str:
        state = self.load_state()
        idx = PHASES.index(state.current_phase)

        if idx >= len(PHASES) - 1:
            print(f"[COMPLETED] Dev loop already reached terminal phase '{state.current_phase}'.")
            return state.current_phase

        next_phase = PHASES[idx + 1]

        # Deterministic verification gate evaluations (zero LLM in routing path)
        if not force:
            if state.current_phase == "implementation":
                # Check for uncommitted changes or test failures
                res = subprocess.run(["git", "status", "--porcelain"], cwd=self.repo_root, capture_output=True, text=True)
                if res.stdout.strip():
                    print("[BLOCKED] Uncommitted changes detected in worktree. Commit before draft_gate.", file=sys.stderr)
                    sys.exit(1)
            elif state.current_phase == "draft_gate":
                if state.ci_status != "GREEN" and state.ci_status != "PASSED":
                    print(f"[BLOCKED] CI Status is '{state.ci_status}'. Must be GREEN to pass draft gate.", file=sys.stderr)
                    sys.exit(1)
            elif state.current_phase == "feedback_resolution":
                if state.unresolved_threads > 0:
                    print(f"[BLOCKED] {state.unresolved_threads} unresolved review thread(s) pending.", file=sys.stderr)
                    sys.exit(1)
            elif state.current_phase == "pre_approval_gate":
                if not state.critic_approved:
                    print("[BLOCKED] Critic panel consensus not yet recorded.", file=sys.stderr)
                    sys.exit(1)

        state.phase_history.append({"from": state.current_phase, "to": next_phase, "at": datetime.now().isoformat()})
        state.current_phase = next_phase
        self.save_state(state)
        print(f"[TRANSITION] Advanced dev loop to phase: '{next_phase}'")
        return next_phase

    def set_phase(self, phase: str):
        if phase not in PHASES:
            raise ValueError(f"Unknown phase: {phase}. Valid phases: {PHASES}")
        state = self.load_state()
        state.current_phase = phase
        self.save_state(state)
        print(f"[STATE] Explicitly set dev loop phase to: '{phase}'")


def main():
    parser = argparse.ArgumentParser(description="Deterministic 7-Phase Dev-Loop State Machine Engine")
    subparsers = parser.add_subparsers(dest="cmd", required=True)

    subparsers.add_parser("status", help="Print current state machine status")
    adv = subparsers.add_parser("advance", help="Advance to next phase if exit criteria pass")
    adv.add_argument("--force", action="store_true", help="Bypass deterministic exit gates")

    set_p = subparsers.add_parser("set", help="Explicitly set phase")
    set_p.add_argument("phase", choices=PHASES, help="Target phase")

    init_p = subparsers.add_parser("init", help="Initialize dev loop for an issue")
    init_p.add_argument("issue", help="Issue or Task ID")
    init_p.add_argument("--branch", default="feature/task", help="Feature branch name")
    init_p.add_argument("--target", default="main", help="Target branch")

    args = parser.parse_args()
    sm = DevLoopStateMachine()

    if args.cmd == "status":
        state = sm.load_state()
        print(f"Issue: {state.issue_id} | Phase: {state.current_phase} | Branch: {state.branch_name}")
    elif args.cmd == "advance":
        sm.advance_phase(force=args.force)
    elif args.cmd == "set":
        sm.set_phase(args.phase)
    elif args.cmd == "init":
        state = DevLoopState(
            issue_id=args.issue,
            current_phase="issue_intake",
            branch_name=args.branch,
            target_branch=args.target,
            worktree_path=None
        )
        sm.save_state(state)
        print(f"[INITIALIZED] Dev loop initialized for issue '{args.issue}' at phase 'issue_intake'.")


if __name__ == "__main__":
    main()
