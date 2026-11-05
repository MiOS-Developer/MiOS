#!/usr/bin/env python3
"""
devloop_worker.py - Universal Cross-Platform Multi-Harness Worker & Orchestrator.
Supports Windows, Linux, and macOS.
"""

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple


@dataclass
class LaneResult:
    worker_id: str
    domain: str
    harness: str
    worktree: str
    command: str
    test_cmd: Optional[str]
    start_time: float = 0.0
    end_time: float = 0.0
    duration: float = 0.0
    exit_code: int = -1
    test_exit_code: Optional[int] = None
    status: str = "PENDING"
    error_message: Optional[str] = None
    log_file: Optional[str] = None


class GitWorktreeError(Exception):
    pass


class DevLoopOrchestrator:
    def __init__(self, config_path: str, verbose: bool = True):
        self.config_path = Path(config_path).resolve()
        self.verbose = verbose
        if not self.config_path.exists():
            raise FileNotFoundError(f"Config file not found: {self.config_path}")

        with open(self.config_path, "r", encoding="utf-8") as f:
            self.config = json.load(f)

        self._validate_config()
        self.root_dir = self._find_git_root()
        self.objective = self.config.get("objective", "Unnamed Objective")
        self.base_branch = self.config.get("base_branch", "main")
        self.worktree_dir = self.config.get("worktree_dir", ".worktrees")
        self.lanes_config = self.config.get("lanes", [])
        self.timeout = self.config.get("timeout_seconds", 3600)

        self.logs_dir = self.root_dir / ".devloop_logs"
        self.reports_dir = self.root_dir / ".devloop_reports"
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)

        self._ensure_gitignore()

    def _log(self, msg: str, level: str = "INFO"):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        prefix = f"[{ts}] [{level.upper()}]"
        if level == "ERROR":
            print(f"\033[91m{prefix} {msg}\033[0m", file=sys.stderr)
        elif level == "WARN":
            print(f"\033[93m{prefix} {msg}\033[0m")
        elif level == "SUCCESS":
            print(f"\033[92m{prefix} {msg}\033[0m")
        else:
            print(f"\033[96m{prefix} {msg}\033[0m")

    def _find_git_root(self) -> Path:
        res = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            cwd=self.config_path.parent
        )
        if res.returncode != 0:
            raise GitWorktreeError("Not currently inside a git repository.")
        return Path(res.stdout.strip()).resolve()

    def _validate_config(self):
        if "objective" not in self.config or not self.config["objective"].strip():
            raise ValueError("Config missing non-empty 'objective'.")
        if "lanes" not in self.config or not isinstance(self.config["lanes"], list) or len(self.config["lanes"]) == 0:
            raise ValueError("Config must have non-empty 'lanes' list.")

        seen_workers = set()
        for idx, lane in enumerate(self.config["lanes"]):
            worker_id = lane.get("worker_id")
            if not worker_id or not re.match(r"^[a-zA-Z0-9_-]+$", worker_id):
                raise ValueError(f"Lane #{idx} invalid or missing worker_id: '{worker_id}'")
            if worker_id in seen_workers:
                raise ValueError(f"Duplicate worker_id detected: '{worker_id}'")
            seen_workers.add(worker_id)

            wt = lane.get("worktree", "")
            if not wt or ".." in wt or wt.startswith(("/", "\\")):
                raise ValueError(f"Lane '{worker_id}' has invalid worktree path: '{wt}'")

    def _ensure_gitignore(self):
        gi_path = self.root_dir / ".gitignore"
        pattern = f"{self.worktree_dir}/"
        if gi_path.exists():
            content = gi_path.read_text(encoding="utf-8")
            if pattern not in content and self.worktree_dir not in content:
                self._log(f"Adding '{pattern}' to .gitignore", "WARN")
                with open(gi_path, "a", encoding="utf-8") as f:
                    f.write(f"\n{pattern}\n")
        else:
            self._log(f"Creating .gitignore with '{pattern}'", "INFO")
            gi_path.write_text(f"{pattern}\n", encoding="utf-8")

    def _run_git(self, args: List[str], cwd: Optional[Path] = None, check: bool = True, retries: int = 3) -> subprocess.CompletedProcess:
        work_dir = cwd or self.root_dir
        for attempt in range(1, retries + 1):
            res = subprocess.run(["git"] + args, cwd=work_dir, capture_output=True, text=True)
            if res.returncode == 0:
                return res
            # Handle index.lock contention
            if "index.lock" in res.stderr and attempt < retries:
                self._log(f"Git index.lock detected. Retrying in {attempt * 0.5}s...", "WARN")
                time.sleep(attempt * 0.5)
                continue
            if check:
                raise GitWorktreeError(f"Git command failed: git {' '.join(args)}\nStderr: {res.stderr}\nStdout: {res.stdout}")
            return res
        return res

    def provision_worktrees(self):
        self._log(f"Provisioning worktrees for objective: {self.objective}", "INFO")
        for lane in self.lanes_config:
            worker_id = lane["worker_id"]
            wt_path = self.root_dir / lane["worktree"]
            branch = worker_id

            if wt_path.exists():
                self._log(f"Worktree at {wt_path} already exists. Reusing.", "INFO")
                continue

            wt_path.parent.mkdir(parents=True, exist_ok=True)
            branch_check = self._run_git(["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], check=False)
            if branch_check.returncode == 0:
                self._log(f"Branch '{branch}' exists. Attaching worktree...", "INFO")
                self._run_git(["worktree", "add", str(wt_path), branch])
            else:
                self._log(f"Creating worktree and branch '{branch}' off '{self.base_branch}'...", "INFO")
                self._run_git(["worktree", "add", str(wt_path), "-b", branch, self.base_branch])

        self._log("All worktrees provisioned successfully.", "SUCCESS")

    def _build_command(self, lane: Dict) -> str:
        harness = lane.get("harness", "custom").lower()
        cmd = lane["command"]
        wt_path = str(self.root_dir / lane["worktree"])

        # Try unified adapters module first
        try:
            sys.path.insert(0, str(Path(__file__).parent.resolve()))
            from adapters import get_adapter
            adapter = get_adapter(harness)
            return adapter.format_task_command(wt_path, cmd)
        except Exception:
            if harness == "claude":
                return f"claude -w {shlex.quote(wt_path)} -p {shlex.quote(cmd)}"
            elif harness == "cloudcode":
                return f"cloudcode cli -w {shlex.quote(wt_path)} --exec {shlex.quote(cmd)}"
            elif harness == "gemini":
                return f"gemini code -w {shlex.quote(wt_path)} -p {shlex.quote(cmd)}"
            elif harness == "openai":
                return f"openai-agent-cli -w {shlex.quote(wt_path)} -p {shlex.quote(cmd)}"
            elif harness == "codex":
                return f"codex exec -C {shlex.quote(wt_path)} --prompt {shlex.quote(cmd)}"
            elif harness == "copilot":
                return f"gh copilot run -w {shlex.quote(wt_path)} -p {shlex.quote(cmd)}"
            elif harness == "antigravity":
                return f"antigravity run -w {shlex.quote(wt_path)} --task {shlex.quote(cmd)}"
            elif harness == "opencode":
                return f"opencode run -d {shlex.quote(wt_path)} -p {shlex.quote(cmd)}"
            elif harness == "cursor":
                return f"cursor-cli -w {shlex.quote(wt_path)} -p {shlex.quote(cmd)}"
            else:
                return cmd

    def run_lane(self, lane: Dict) -> LaneResult:
        worker_id = lane["worker_id"]
        domain = lane.get("domain", "general")
        harness = lane.get("harness", "custom")
        worktree = lane["worktree"]
        cmd = lane["command"]
        test_cmd = lane.get("test_cmd")
        wt_full_path = self.root_dir / worktree

        log_path = self.logs_dir / f"{worker_id}.log"
        result = LaneResult(
            worker_id=worker_id,
            domain=domain,
            harness=harness,
            worktree=worktree,
            command=cmd,
            test_cmd=test_cmd,
            log_file=str(log_path)
        )

        full_cmd = self._build_command(lane)
        self._log(f"Starting lane '{worker_id}' ({domain}). Logs: {log_path}", "INFO")

        result.start_time = time.time()
        env = os.environ.copy()
        if "env" in lane and isinstance(lane["env"], dict):
            env.update({str(k): str(v) for k, v in lane["env"].items()})

        try:
            with open(log_path, "w", encoding="utf-8") as log_file:
                log_file.write(f"=== DevLoop Lane: {worker_id} [{domain}] ===\n")
                log_file.write(f"Objective: {self.objective}\n")
                log_file.write(f"Started at: {datetime.now().isoformat()}\n")
                log_file.write(f"Harness Command: {full_cmd}\n")
                log_file.write("=" * 60 + "\n\n")
                log_file.flush()

                proc = subprocess.run(
                    full_cmd,
                    shell=True,
                    cwd=wt_full_path,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    timeout=self.timeout,
                    env=env
                )
                result.exit_code = proc.returncode

                if result.exit_code == 0 and test_cmd:
                    log_file.write(f"\n\n=== Running Test Gate: {test_cmd} ===\n")
                    log_file.flush()
                    test_proc = subprocess.run(
                        test_cmd,
                        shell=True,
                        cwd=wt_full_path,
                        stdout=log_file,
                        stderr=subprocess.STDOUT,
                        timeout=self.timeout,
                        env=env
                    )
                    result.test_exit_code = test_proc.returncode
                    if test_proc.returncode != 0:
                        result.status = "TEST_FAILED"
                        result.error_message = f"Test gate failed with exit code {test_proc.returncode}"
                    else:
                        result.status = "PASSED"
                elif result.exit_code == 0:
                    result.status = "PASSED"
                else:
                    result.status = "COMMAND_FAILED"
                    result.error_message = f"Command failed with exit code {result.exit_code}"

        except subprocess.TimeoutExpired:
            result.status = "TIMEOUT"
            result.error_message = f"Lane timed out after {self.timeout} seconds"
            self._log(f"Lane '{worker_id}' TIMED OUT", "ERROR")
        except Exception as e:
            result.status = "ERROR"
            result.error_message = str(e)
            self._log(f"Lane '{worker_id}' ERROR: {e}", "ERROR")
        finally:
            result.end_time = time.time()
            result.duration = round(result.end_time - result.start_time, 2)

        if result.status == "PASSED":
            self._log(f"Lane '{worker_id}' completed successfully in {result.duration}s", "SUCCESS")
        else:
            self._log(f"Lane '{worker_id}' finished with status {result.status}: {result.error_message}", "ERROR")

        return result

    def run_all(self, parallel: bool = True) -> List[LaneResult]:
        self.provision_worktrees()
        results: List[LaneResult] = []

        if parallel:
            from concurrent.futures import ThreadPoolExecutor
            self._log(f"Executing {len(self.lanes_config)} lanes in parallel...", "INFO")
            with ThreadPoolExecutor(max_workers=len(self.lanes_config)) as executor:
                futures = [executor.submit(self.run_lane, lane) for lane in self.lanes_config]
                for f in futures:
                    results.append(f.result())
        else:
            self._log(f"Executing {len(self.lanes_config)} lanes sequentially...", "INFO")
            for lane in self.lanes_config:
                results.append(self.run_lane(lane))

        self._generate_report(results)
        return results

    def reconcile_and_clean(self) -> bool:
        self._log(f"Reconciling lanes into base branch '{self.base_branch}'...", "INFO")

        # Verify base branch status
        self._run_git(["checkout", self.base_branch])
        base_status = self._run_git(["status", "--porcelain"])
        if base_status.stdout.strip():
            raise GitWorktreeError(f"Base branch '{self.base_branch}' has uncommitted changes. Stash or commit first.")

        all_clean = True
        merged_lanes = []
        failed_lanes = []

        for lane in self.lanes_config:
            worker_id = lane["worker_id"]
            wt_path = self.root_dir / lane["worktree"]
            branch = worker_id
            test_cmd = lane.get("test_cmd")

            self._log(f"Inspecting lane '{worker_id}'...", "INFO")
            if not wt_path.exists():
                self._log(f"Worktree '{wt_path}' not found. Skipping.", "WARN")
                continue

            # Check uncommitted files in worktree
            status_check = self._run_git(["status", "--porcelain"], cwd=wt_path)
            if status_check.stdout.strip():
                self._log(f"Lane '{worker_id}' has uncommitted changes in worktree! Cannot reconcile.", "ERROR")
                failed_lanes.append((worker_id, "Uncommitted changes in worktree"))
                all_clean = False
                continue

            # Run pre-merge test gate
            if test_cmd:
                self._log(f"Running pre-merge test gate in worktree: {test_cmd}", "INFO")
                test_proc = subprocess.run(test_cmd, shell=True, cwd=wt_path, capture_output=True, text=True)
                if test_proc.returncode != 0:
                    self._log(f"Pre-merge test gate FAILED for '{worker_id}'. Stderr:\n{test_proc.stderr}", "ERROR")
                    failed_lanes.append((worker_id, f"Test gate failed (exit code {test_proc.returncode})"))
                    all_clean = False
                    continue
                self._log(f"Pre-merge test gate PASSED for '{worker_id}'.", "SUCCESS")

            # Perform atomic merge
            self._log(f"Merging branch '{branch}' into '{self.base_branch}'...", "INFO")
            self._run_git(["checkout", self.base_branch])
            merge_proc = self._run_git(
                ["merge", "--no-ff", branch, "-m", f"Merge lane {worker_id} for: {self.objective}"],
                check=False
            )

            if merge_proc.returncode != 0:
                self._log(f"Merge CONFLICT on branch '{branch}'. Aborting merge.", "ERROR")
                self._run_git(["merge", "--abort"], check=False)
                failed_lanes.append((worker_id, "Merge conflict"))
                all_clean = False
                continue

            # Clean worktree and branch
            self._log(f"Removing worktree and deleting merged branch '{branch}'...", "SUCCESS")
            self._run_git(["worktree", "remove", str(wt_path)])
            self._run_git(["branch", "-d", branch])
            merged_lanes.append(worker_id)

        self._log("=" * 60, "INFO")
        self._log("Reconciliation Summary:", "INFO")
        self._log(f"  Successfully merged ({len(merged_lanes)}): {', '.join(merged_lanes) or 'None'}", "SUCCESS")
        if failed_lanes:
            self._log(f"  Failed merges ({len(failed_lanes)}):", "ERROR")
            for w_id, reason in failed_lanes:
                self._log(f"    - {w_id}: {reason}", "ERROR")
            return False

        self._log("All lanes cleanly merged and reclaimed!", "SUCCESS")
        return True

    def _generate_report(self, results: List[LaneResult]):
        report_data = {
            "objective": self.objective,
            "base_branch": self.base_branch,
            "timestamp": datetime.now().isoformat(),
            "total_lanes": len(results),
            "passed_lanes": sum(1 for r in results if r.status == "PASSED"),
            "failed_lanes": sum(1 for r in results if r.status != "PASSED"),
            "lanes": [
                {
                    "worker_id": r.worker_id,
                    "domain": r.domain,
                    "harness": r.harness,
                    "status": r.status,
                    "duration_seconds": r.duration,
                    "exit_code": r.exit_code,
                    "test_exit_code": r.test_exit_code,
                    "error_message": r.error_message,
                    "log_file": r.log_file
                }
                for r in results
            ]
        }
        report_file = self.reports_dir / f"report_{int(time.time())}.json"
        with open(report_file, "w", encoding="utf-8") as f:
            json.dump(report_data, f, indent=2)
        self._log(f"Telemetry report generated at: {report_file}", "INFO")


def main():
    parser = argparse.ArgumentParser(description="Universal DevLoop Worker & Worktree Orchestrator")
    parser.add_argument("--config", "-c", required=True, help="Path to lanes configuration JSON file")
    parser.add_argument("--run", action="store_true", help="Provision worktrees and execute all worker lanes")
    parser.add_argument("--reconcile", action="store_true", help="Verify pre-merge gates, atomically merge, and cleanup worktrees")
    parser.add_argument("--sequential", action="store_true", help="Run lanes sequentially instead of concurrently")

    args = parser.parse_args()
    orchestrator = DevLoopOrchestrator(args.config)

    if args.run:
        results = orchestrator.run_all(parallel=not args.sequential)
        if any(r.status != "PASSED" for r in results):
            sys.exit(1)

    if args.reconcile:
        success = orchestrator.reconcile_and_clean()
        if not success:
            sys.exit(1)


if __name__ == "__main__":
    main()
