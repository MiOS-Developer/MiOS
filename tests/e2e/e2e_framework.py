#!/usr/bin/env python3
"""MiOS E2E Test Framework.

Provides opaque-box testing primitives, hermetic execution fixtures,
two-sided verification controls, and structured reporting (TAP & JSON).
"""

from __future__ import annotations

import os
import sys
import time
import shutil
import tempfile
import traceback
import subprocess
from typing import Any, Callable, Dict, List, Optional, Tuple


class TestAssertionError(AssertionError):
    """Raised when an E2E test assertion fails."""
    pass


class TestResult:
    """Represents the execution result of an individual test case."""

    def __init__(self, name: str, suite: str, tier: int):
        self.name = name
        self.suite = suite
        self.tier = tier
        self.status = "PENDING"  # PASS, FAIL, SKIP, ERROR
        self.duration_s = 0.0
        self.error_message: Optional[str] = None
        self.traceback: Optional[str] = None
        self.details: Dict[str, Any] = {}

    def pass_test(self, duration_s: float):
        self.status = "PASS"
        self.duration_s = duration_s

    def fail_test(self, duration_s: float, message: str, tb: str):
        self.status = "FAIL"
        self.duration_s = duration_s
        self.error_message = message
        self.traceback = tb

    def error_test(self, duration_s: float, message: str, tb: str):
        self.status = "ERROR"
        self.duration_s = duration_s
        self.error_message = message
        self.traceback = tb

    def skip_test(self, reason: str):
        self.status = "SKIP"
        self.error_message = reason

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "suite": self.suite,
            "tier": self.tier,
            "status": self.status,
            "duration_s": round(self.duration_s, 4),
            "error_message": self.error_message,
            "details": self.details,
        }


class IsolatedEnvironment:
    """Context manager for hermetic filesystem isolation.
    
    Creates a temporary directory for tests to generate/mutate files
    without touching system paths or active desktop state.
    """

    def __init__(self, prefix: str = "mios_e2e_"):
        self.prefix = prefix
        self.root: Optional[str] = None

    def __enter__(self) -> IsolatedEnvironment:
        self.root = tempfile.mkdtemp(prefix=self.prefix)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.root and os.path.exists(self.root):
            try:
                shutil.rmtree(self.root, ignore_errors=True)
            except Exception:
                pass

    def path(self, *parts: str) -> str:
        if not self.root:
            raise RuntimeError("Environment not initialized")
        full_path = os.path.join(self.root, *parts)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        return full_path

    def write_file(self, content: str, *parts: str, mode: int = 0o644) -> str:
        target = self.path(*parts)
        with open(target, "w", encoding="utf-8") as f:
            f.write(content)
        os.chmod(target, mode)
        return target

    def read_file(self, *parts: str) -> str:
        target = self.path(*parts)
        with open(target, "r", encoding="utf-8") as f:
            return f.read()


class E2ETestCase:
    """Base class for all MiOS E2E test suites."""

    tier: int = 1
    suite_name: str = "BaseSuite"

    def __init__(self):
        self.results: List[TestResult] = []

    # --- Assertion Primitives ---

    def assert_true(self, condition: bool, msg: str = "Expected condition to be True"):
        if not condition:
            raise TestAssertionError(msg)

    def assert_false(self, condition: bool, msg: str = "Expected condition to be False"):
        if condition:
            raise TestAssertionError(msg)

    def assert_equal(self, actual: Any, expected: Any, msg: Optional[str] = None):
        if actual != expected:
            default_msg = f"Expected '{expected}' (type {type(expected).__name__}), got '{actual}' (type {type(actual).__name__})"
            raise TestAssertionError(msg or default_msg)

    def assert_not_equal(self, actual: Any, expected: Any, msg: Optional[str] = None):
        if actual == expected:
            default_msg = f"Expected actual to differ from '{expected}', but both matched"
            raise TestAssertionError(msg or default_msg)

    def assert_in(self, member: Any, container: Any, msg: Optional[str] = None):
        if member not in container:
            default_msg = f"Expected '{member}' to be present in container"
            raise TestAssertionError(msg or default_msg)

    def assert_not_in(self, member: Any, container: Any, msg: Optional[str] = None):
        if member in container:
            default_msg = f"Expected '{member}' to be absent from container"
            raise TestAssertionError(msg or default_msg)

    def assert_greater(self, val1: Any, val2: Any, msg: Optional[str] = None):
        if not (val1 > val2):
            default_msg = f"Expected {val1} > {val2}"
            raise TestAssertionError(msg or default_msg)

    def assert_greater_equal(self, val1: Any, val2: Any, msg: Optional[str] = None):
        if not (val1 >= val2):
            default_msg = f"Expected {val1} >= {val2}"
            raise TestAssertionError(msg or default_msg)

    def assert_less(self, val1: Any, val2: Any, msg: Optional[str] = None):
        if not (val1 < val2):
            default_msg = f"Expected {val1} < {val2}"
            raise TestAssertionError(msg or default_msg)

    def assert_regex(self, text: str, pattern: str, msg: Optional[str] = None):
        import re
        if not re.search(pattern, text):
            default_msg = f"Pattern '{pattern}' did not match text:\n{text[:200]}..."
            raise TestAssertionError(msg or default_msg)

    def assert_two_sided(
        self,
        positive_fn: Callable[[], bool],
        negative_fn: Callable[[], bool],
        description: str,
    ):
        """Two-Sided Verification: runs positive control (must succeed)
        and negative control (must detect breakage / fail gracefully).
        """
        # 1. Positive Control
        try:
            pos_ok = positive_fn()
        except Exception as e:
            raise TestAssertionError(f"[{description}] Positive control raised unexpected exception: {e}")
        if not pos_ok:
            raise TestAssertionError(f"[{description}] Positive control failed to assert success")

        # 2. Negative Control
        try:
            neg_ok = negative_fn()
        except Exception as e:
            raise TestAssertionError(f"[{description}] Negative control raised unexpected exception: {e}")
        if not neg_ok:
            raise TestAssertionError(f"[{description}] Negative control failed to detect invalid state")

    # --- Subprocess Execution Helpers ---

    def run_cmd(
        self,
        cmd: List[str] | str,
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        timeout_s: float = 30.0,
        shell: bool = False,
    ) -> Tuple[int, str, str]:
        """Runs a subprocess hermetically, capturing exit code, stdout, and stderr."""
        run_env = os.environ.copy()
        if env:
            run_env.update(env)

        try:
            proc = subprocess.Popen(
                cmd,
                cwd=cwd,
                env=run_env,
                shell=shell,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            stdout, stderr = proc.communicate(timeout=timeout_s)
            return proc.returncode, stdout, stderr
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
            return -1, stdout, f"TIMEOUT after {timeout_s}s: {stderr}"
        except Exception as e:
            return -2, "", str(e)


class E2ERunner:
    """Discovers and executes E2E test suites with TAP & JSON reporting."""

    def __init__(self, filter_tier: Optional[int] = None, filter_pattern: Optional[str] = None):
        self.filter_tier = filter_tier
        self.filter_pattern = filter_pattern
        self.all_results: List[TestResult] = []

    def run_suite(self, test_case_cls: type[E2ETestCase]) -> List[TestResult]:
        suite = test_case_cls()
        test_methods = [
            m for m in dir(suite)
            if m.startswith("test_") and callable(getattr(suite, m))
        ]
        test_methods.sort()

        for method_name in test_methods:
            if self.filter_pattern and self.filter_pattern not in method_name:
                continue

            result = TestResult(
                name=method_name,
                suite=suite.suite_name,
                tier=suite.tier,
            )

            start_t = time.perf_counter()
            try:
                test_fn = getattr(suite, method_name)
                test_fn()
                duration = time.perf_counter() - start_t
                result.pass_test(duration)
            except TestAssertionError as e:
                duration = time.perf_counter() - start_t
                result.fail_test(duration, str(e), traceback.format_exc())
            except Exception as e:
                duration = time.perf_counter() - start_t
                result.error_test(duration, str(e), traceback.format_exc())

            self.all_results.append(result)

        return self.all_results

    def generate_tap(self) -> str:
        lines = [f"1..{len(self.all_results)}"]
        for idx, res in enumerate(self.all_results, 1):
            status = "ok" if res.status == "PASS" else "not ok"
            if res.status == "SKIP":
                status = "ok"
                directive = f" # SKIP {res.error_message or ''}"
            else:
                directive = ""

            lines.append(f"{status} {idx} - [{res.suite}] {res.name} (tier {res.tier}, {res.duration_s:.3f}s){directive}")
            if res.status in ("FAIL", "ERROR"):
                lines.append(f"  ---")
                lines.append(f"  message: {res.error_message}")
                if res.traceback:
                    tb_indented = "\n".join(f"    {line}" for line in res.traceback.strip().splitlines())
                    lines.append(f"  traceback:\n{tb_indented}")
                lines.append(f"  ...")
        return "\n".join(lines)

    def summary(self) -> Dict[str, Any]:
        total = len(self.all_results)
        passed = sum(1 for r in self.all_results if r.status == "PASS")
        failed = sum(1 for r in self.all_results if r.status == "FAIL")
        errored = sum(1 for r in self.all_results if r.status == "ERROR")
        skipped = sum(1 for r in self.all_results if r.status == "SKIP")
        duration = sum(r.duration_s for r in self.all_results)

        by_tier = {}
        for r in self.all_results:
            t = r.tier
            if t not in by_tier:
                by_tier[t] = {"total": 0, "passed": 0, "failed": 0, "errored": 0, "skipped": 0}
            by_tier[t]["total"] += 1
            if r.status == "PASS":
                by_tier[t]["passed"] += 1
            elif r.status == "FAIL":
                by_tier[t]["failed"] += 1
            elif r.status == "ERROR":
                by_tier[t]["errored"] += 1
            elif r.status == "SKIP":
                by_tier[t]["skipped"] += 1

        return {
            "total": total,
            "passed": passed,
            "failed": failed,
            "errored": errored,
            "skipped": skipped,
            "duration_s": round(duration, 4),
            "by_tier": by_tier,
            "success": (failed == 0 and errored == 0),
        }


def run_standalone(test_case_cls: type[E2ETestCase]) -> int:
    """Convenience entry point for running an individual E2E test suite standalone."""
    runner = E2ERunner()
    runner.run_suite(test_case_cls)
    print(runner.generate_tap())
    return 0 if runner.summary()["success"] else 1

