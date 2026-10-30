#!/usr/bin/env python3
# AI-hint: Regression and integrity gate test for tasks.jsonl title/status contradiction rejection (ADR-0028).
# AI-related: tasks.jsonl, TASKS.md, tools/check-tasks.py, tools/native/mios-task/
# AI-doc: usr/share/doc/mios/manual/tests.md
"""Regression suite for tracker integrity: verifies no title/status contradictions exist.

Positive control: validates root tasks.jsonl has 0 contradictions.
Negative controls: plants contradiction test cases into temporary fixtures and asserts non-zero exit.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = (
    os.environ.get("MIOS_DRIFT_ROOT")
    or os.environ.get("MIOS_ROOT")
    or os.path.normpath(os.path.join(_HERE, ".."))
)
_CHECK_TASKS = os.path.join(_ROOT, "tools/check-tasks.py")


def run_gate(root_dir: str) -> tuple[int, str]:
    """Run `tools/check-tasks.py integrity` targeting `root_dir`."""
    env = {**os.environ, "MIOS_DRIFT_ROOT": root_dir}
    p = subprocess.run(
        [sys.executable, _CHECK_TASKS, "integrity"],
        env=env,
        capture_output=True,
        text=True,
    )
    return p.returncode, p.stdout + p.stderr


def test_positive_control() -> None:
    """Verify live repository root tasks.jsonl passes with 0 contradictions."""
    tasks_path = os.path.join(_ROOT, "tasks.jsonl")
    assert os.path.isfile(tasks_path), f"Missing tasks.jsonl under {_ROOT}"

    rc, out = run_gate(_ROOT)
    assert rc == 0, f"Positive control failed (rc={rc}):\n{out}"
    assert "integrity check passed" in out, f"Unexpected output in positive control:\n{out}"
    assert "0 contradictions" in out, f"Expected 0 contradictions in output:\n{out}"
    print(f"[test-tasks-integrity] Positive control PASSED: {out.strip()}")


def test_negative_controls() -> None:
    """Verify planted contradiction cases fail with non-zero exit codes."""
    tmpdir = tempfile.mkdtemp(prefix="test-tasks-integrity-")
    try:
        clean_records = [
            {
                "id": "task_1",
                "number": 1,
                "status": "completed",
                "title": "Legitimate finished task",
                "verification_evidence": "provenance note",
            },
            {
                "id": "task_2",
                "number": 2,
                "status": "pending",
                "title": "Clean pending backlog item",
            },
            # Edge-case non-contradiction 1: uppercase ABANDONED (contains substring 'DONE' but not word '\bDONE\b')
            {
                "id": "task_3",
                "number": 3,
                "status": "pending",
                "title": "Clean pending task describing an ABANDONED legacy subsystem",
            },
            # Edge-case non-contradiction 2: lowercase 'done-by-code' description
            {
                "id": "task_4",
                "number": 4,
                "status": "pending",
                "title": "Give every task marked done-by-code a command that demonstrates it",
            },
            # Edge-case non-contradiction 3: completed status with DONE marker is valid
            {
                "id": "task_5",
                "number": 5,
                "status": "completed",
                "title": "Task completed with marker [DONE]",
                "verification_evidence": "verified in test",
            },
        ]

        def write_fixture(extra_records: list[dict]) -> str:
            path = os.path.join(tmpdir, "tasks.jsonl")
            with open(path, "w", encoding="utf-8") as f:
                for r in clean_records + extra_records:
                    f.write(json.dumps(r) + "\n")
            return tmpdir

        # Baseline check on clean fixture
        write_fixture([])
        rc, out = run_gate(tmpdir)
        assert rc == 0, f"Clean fixture baseline failed:\n{out}"

        # Negative Control 1: status="pending" with "[DONE]"
        write_fixture([
            {
                "id": "task_bad_1",
                "number": 101,
                "status": "pending",
                "title": "Task claiming completion in title [DONE]",
            }
        ])
        rc, out = run_gate(tmpdir)
        assert rc != 0, "Negative Control 1 FAILED: accepted status='pending' with '[DONE]'"
        assert "task_101" in out and "title/status contradiction" in out, f"Unexpected error output:\n{out}"
        print("[test-tasks-integrity] Negative Control 1 (status: pending, title: '[DONE]') successfully rejected.")

        # Negative Control 2: status="pending" with "✅ DONE"
        write_fixture([
            {
                "id": "task_bad_2",
                "number": 102,
                "status": "pending",
                "title": "Task with markdown badge **[P1] ✅ DONE**",
            }
        ])
        rc, out = run_gate(tmpdir)
        assert rc != 0, "Negative Control 2 FAILED: accepted status='pending' with '✅ DONE'"
        assert "task_102" in out and "title/status contradiction" in out, f"Unexpected error output:\n{out}"
        print("[test-tasks-integrity] Negative Control 2 (status: pending, title: '✅ DONE') successfully rejected.")

        # Negative Control 3: status="pending" with "DONE this session"
        write_fixture([
            {
                "id": "task_bad_3",
                "number": 103,
                "status": "pending",
                "title": "Task annotated with **✅ DONE this session",
            }
        ])
        rc, out = run_gate(tmpdir)
        assert rc != 0, "Negative Control 3 FAILED: accepted status='pending' with 'DONE this session'"
        assert "task_103" in out and "title/status contradiction" in out, f"Unexpected error output:\n{out}"
        print("[test-tasks-integrity] Negative Control 3 (status: pending, title: 'DONE this session') successfully rejected.")

        # Negative Control 4: status="in_progress" with "DONE"
        write_fixture([
            {
                "id": "task_bad_4",
                "number": 104,
                "status": "in_progress",
                "title": "In-progress task prematurely claiming DONE",
            }
        ])
        rc, out = run_gate(tmpdir)
        assert rc != 0, "Negative Control 4 FAILED: accepted status='in_progress' with 'DONE'"
        assert "task_104" in out and "title/status contradiction" in out, f"Unexpected error output:\n{out}"
        print("[test-tasks-integrity] Negative Control 4 (status: in_progress, title: 'DONE') successfully rejected.")

        # Negative Control 5: status="incomplete" with "DONE"
        write_fixture([
            {
                "id": "task_bad_5",
                "number": 105,
                "status": "incomplete",
                "title": "Incomplete task claiming DONE",
            }
        ])
        rc, out = run_gate(tmpdir)
        assert rc != 0, "Negative Control 5 FAILED: accepted status='incomplete' with 'DONE'"
        assert "task_105" in out and "title/status contradiction" in out, f"Unexpected error output:\n{out}"
        print("[test-tasks-integrity] Negative Control 5 (status: incomplete, title: 'DONE') successfully rejected.")

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def main() -> int:
    print("[test-tasks-integrity] Starting Tracker Integrity Verification Suite...")
    test_positive_control()
    test_negative_controls()
    print("[test-tasks-integrity] ALL POSITIVE AND NEGATIVE CONTROLS PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
