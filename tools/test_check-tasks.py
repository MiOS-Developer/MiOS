#!/usr/bin/env python3
# AI-hint: Sibling unit tests for tools/check-tasks.py -- one suite per subcommand (status-parity, schema, agy), each with its own failure counter.
"""Sibling tests for the consolidated task-plane gates."""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile


sp__HERE = os.path.dirname(os.path.abspath(__file__))
sp__spec = importlib.util.spec_from_file_location(
    "check_tasks", os.path.join(sp__HERE, "check-tasks.py"))
sp_M = importlib.util.module_from_spec(sp__spec)
sp__spec.loader.exec_module(sp_M)

sp__fails = 0

def sp_check(name, cond, detail=""):
    global sp__fails
    if cond:
        print(f"ok   - {name}")
    else:
        sp__fails += 1
        print(f"FAIL - {name}" + (f" -- {detail}" if detail else ""))

def sp_mkrepo(rows, sections):
    """rows: [(tid, pri, status)] summary table. sections: [(tid, sep, status)]."""
    root = tempfile.mkdtemp(prefix="tasksparity-")
    body = ["| ID | Pri | Status | Domain | Title |",
            "|----|-----|--------|--------|-------|"]
    for tid, pri, status in rows:
        body.append(f"| {tid} | {pri} | {status} | Domain | Title |")
    body.append("")
    for tid, sep, status in sections:
        body.append(f"## {tid} {sep} Title  (WS-X | P1 | S)")
        body.append("**Goal:** goal.")
        body.append(f"**Status:** {status} | **Domain:** Domain")
        body.append("")
    open(os.path.join(root, sp_M.TASKS), "w", encoding="utf-8").write("\n".join(body) + "\n")
    return root

def sp_run(root):
    p = subprocess.run([sys.executable, os.path.join(sp__HERE, "check-tasks.py"), "status-parity"],
                       env={**os.environ, "MIOS_DRIFT_ROOT": root},
                       capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr

def sp_main():
    roots = []

    r = sp_mkrepo([("task_1", "P1", "done"), ("task_2", "P2", "planned")],
               [("task_1", "--", "done"), ("task_2", ":", "planned")])
    roots.append(r)
    rc, out = sp_run(r)
    sp_check("agreeing surfaces pass (both heading styles)", rc == 0, out)
    sp_check("the pass line reports the open count", "open=1" in out, out)

    r = sp_mkrepo([("task_1", "P1", "done")], [("task_1", "--", "planned")])
    roots.append(r)
    rc, out = sp_run(r)
    sp_check("a disagreeing cell fails", rc == 1 and "task_1" in out, out)

    r = sp_mkrepo([("task_1", "P1", "?")], [("task_1", "--", "in-progress")])
    roots.append(r)
    rc, out = sp_run(r)
    sp_check("'?' fails when a section can answer it", rc == 1 and "'?'" in out, out)

    r = sp_mkrepo([("task_1", "P1", "done")],
               [("task_1", "--", "done -- a long explanation with -- dashes in it")])
    roots.append(r)
    rc, out = sp_run(r)
    sp_check("free prose after ' -- ' is ignored", rc == 0, out)

    r = sp_mkrepo([("task_1", "P1", "in-progress")],
               [("task_1", "--", "in-progress (built-gated)")])
    roots.append(r)
    rc, out = sp_run(r)
    sp_check("a ' (qualifier)' is ignored", rc == 0, out)

    r = sp_mkrepo([("task_1", "P1", "finished")], [("task_1", "--", "finished")])
    roots.append(r)
    rc, out = sp_run(r)
    sp_check("an unknown status word fails even when both agree",
          rc == 1 and "unknown status" in out, out)

    r = sp_mkrepo([("task_1", "P1", "done")],
               [("task_1", "--", "done"), ("task_2", "--", "done")])
    roots.append(r)
    rc, out = sp_run(r)
    sp_check("a section with no summary row fails",
          rc == 1 and "task_2" in out and "no row in the summary table" in out, out)

    r = sp_mkrepo([], [])
    roots.append(r)
    rc, out = sp_run(r)
    sp_check("an unparseable summary table fails rather than passing vacuously",
          rc == 1 and "no parseable rows" in out, out)

    r = sp_mkrepo([("task_1", "P1", "?")], [])
    roots.append(r)
    rc, out = sp_run(r)
    sp_check("'?' with no section to resolve it still fails", rc == 1, out)

    r = sp_mkrepo([("task_1", "P1", "done")], [])
    roots.append(r)
    rc, out = sp_run(r)
    sp_check("a row with no section is allowed when it carries a real status",
          rc == 0, out)

    r = sp_mkrepo([("task_1", "P1", "done")], [("task_1", "--", "done -- see task_404")])
    roots.append(r)
    rc, out = sp_run(r)
    sp_check("a task the list names but nothing holds fails",
          rc == 1 and "references task_404, which names no task" in out, out)

    sp_check("head_token strips the continuation", sp_M.status_parity_head_token("done -- x") == "done")
    sp_check("head_token strips the qualifier", sp_M.status_parity_head_token("planned (decision)") == "planned")
    sp_check("head_token lowercases", sp_M.status_parity_head_token("Done") == "done")

    for r in roots:
        shutil.rmtree(r, ignore_errors=True)
    print(f"\n{'FAIL' if sp__fails else 'PASS'}: {sp__fails} failure(s)")
    return 1 if sp__fails else 0


sch__HERE = os.path.dirname(os.path.abspath(__file__))
sch__fails = 0

def sch_check(name, cond, detail=""):
    global sch__fails
    if cond:
        print(f"ok   - {name}")
    else:
        sch__fails += 1
        print(f"FAIL - {name}" + (f" -- {detail}" if detail else ""))

def sch_run_tool(root):
    p = subprocess.run(
        [sys.executable, os.path.join(sch__HERE, "check-tasks.py"), "schema"],
        env={**os.environ, "MIOS_DRIFT_ROOT": root},
        capture_output=True,
        text=True,
    )
    return p.returncode, p.stdout + p.stderr

def sch_plant(store, text):
    """Append `text` to the last slice of the frozen AGY-TASKS.md in a copy of tasks.jsonl."""
    import json
    lines = open(store, encoding="utf-8").read().split("\n")[:-1]
    last = None
    for i, line in enumerate(lines):
        for j, sl in enumerate((json.loads(line).get("provenance") or {}).get("sources") or []):
            if sl["file"] == "MiOS:AGY-TASKS.md" and (last is None or sl["offset"] > last[2]):
                last = (i, j, sl["offset"])
    rec = json.loads(lines[last[0]])
    rec["provenance"]["sources"][last[1]]["text"] += text
    lines[last[0]] = json.dumps(rec, ensure_ascii=False)
    open(store, "w", encoding="utf-8").write("\n".join(lines) + "\n")

def sch_main():
    root = tempfile.mkdtemp(prefix="task-schema-test-")
    try:
        os.makedirs(os.path.join(root, "usr/share/mios"), exist_ok=True)
        shutil.copy(
            os.path.join(sch__HERE, "../usr/share/mios/mios.toml"),
            os.path.join(root, "usr/share/mios/mios.toml"),
        )
        # The retired AGY-TASKS.md lives on as frozen slices in tasks.jsonl (ADR-0028).
        shutil.copy(os.path.join(sch__HERE, "../tasks.jsonl"), os.path.join(root, "tasks.jsonl"))

        rc, out = sch_run_tool(root)
        sch_check("valid AGY-TASKS.md passes task schema check", rc == 0, f"rc={rc} out={out}")

        # A task past the schema floor without Verify / Do NOT, naming a task that does not exist.
        sch_plant(os.path.join(root, "tasks.jsonl"), """
## task_999999 -- Test task  (WS-TEST | P0 | S)
**Goal:** test
**What+How:** test
**Where:** test
**Done When:** test
**Why:** test
**Dep:** task_9999999
""")
        rc, out = sch_run_tool(root)
        sch_check("missing required field (Verify / Do NOT) fails",
                  rc != 0 and "task_999999: missing **Verify:**" in out, f"rc={rc} out={out}")
        sch_check("a Dep naming no task fails", "names task_9999999, which does not exist" in out, out)

    finally:
        shutil.rmtree(root, ignore_errors=True)

    if sch__fails > 0:
        return 1


agy__HERE = os.path.dirname(os.path.abspath(__file__))
agy__fails = 0

def agy_check(name, cond, detail=""):
    global agy__fails
    if cond:
        print(f"ok   - {name}")
    else:
        agy__fails += 1
        print(f"FAIL - {name}" + (f" -- {detail}" if detail else ""))

def agy_run_tool(root):
    p = subprocess.run(
        [sys.executable, os.path.join(agy__HERE, "check-tasks.py"), "agy"],
        env={**os.environ, "MIOS_DRIFT_ROOT": root},
        capture_output=True,
        text=True,
    )
    return p.returncode, p.stdout + p.stderr

def agy_main():
    root = tempfile.mkdtemp(prefix="agy-tasks-test-")
    try:
        content_clean = """
## task_1 -- First task
**Dep:** none

## task_2 -- Second task
**Dep:** task_1
"""
        with open(os.path.join(root, "AGY-TASKS.md"), "w", encoding="utf-8") as f:
            f.write(content_clean)

        rc, out = agy_run_tool(root)
        agy_check("clean AGY tasks passes", rc == 0, f"rc={rc} out={out}")

        content_dup = content_clean + "\n## task_1 -- Duplicate task\n"
        with open(os.path.join(root, "AGY-TASKS.md"), "w", encoding="utf-8") as f:
            f.write(content_dup)

        rc, out = agy_run_tool(root)
        agy_check("duplicate AGY task ID fails", rc != 0 and "task_1 is defined multiple times" in out,
                  f"rc={rc} out={out}")

        content_dangling = content_clean + "\n## task_3 -- Task\n**Dep:** task_99999\n"
        with open(os.path.join(root, "AGY-TASKS.md"), "w", encoding="utf-8") as f:
            f.write(content_dangling)

        rc, out = agy_run_tool(root)
        agy_check("dangling dependency reference fails",
                  rc != 0 and "dangling dependency reference task_99999" in out, f"rc={rc} out={out}")

    finally:
        shutil.rmtree(root, ignore_errors=True)

    if agy__fails > 0:
        return 1

int__fails = 0


def int_check(name, cond, detail=""):
    global int__fails
    if cond:
        print(f"ok   - {name}")
    else:
        int__fails += 1
        print(f"FAIL - {name}" + (f" -- {detail}" if detail else ""))


def int_run_tool(root):
    p = subprocess.run(
        [sys.executable, os.path.join(sp__HERE, "check-tasks.py"), "integrity"],
        env={**os.environ, "MIOS_DRIFT_ROOT": root},
        capture_output=True,
        text=True,
    )
    return p.returncode, p.stdout + p.stderr


def int_main():
    root = tempfile.mkdtemp(prefix="int-tasks-test-")
    try:
        # Positive control: clean records pass
        tasks_clean = [
            {"id": "task_1", "number": 1, "status": "completed", "title": "Done task", "verification_evidence": "verified"},
            {"id": "task_2", "number": 2, "status": "pending", "title": "Future task"},
        ]
        with open(os.path.join(root, "tasks.jsonl"), "w", encoding="utf-8") as f:
            for r in tasks_clean:
                f.write(json.dumps(r) + "\n")

        rc, out = int_run_tool(root)
        int_check("clean tasks.jsonl passes integrity check", rc == 0, f"rc={rc} out={out}")

        # Negative control 1: pending with [DONE]
        tasks_bad1 = tasks_clean + [
            {"id": "task_3", "number": 3, "status": "pending", "title": "Fake complete [DONE]"}
        ]
        with open(os.path.join(root, "tasks.jsonl"), "w", encoding="utf-8") as f:
            for r in tasks_bad1:
                f.write(json.dumps(r) + "\n")
        rc, out = int_run_tool(root)
        int_check("pending task with [DONE] fails integrity check", rc != 0 and "title/status contradiction" in out, f"rc={rc} out={out}")

        # Negative control 2: in_progress with ✅ DONE
        tasks_bad2 = tasks_clean + [
            {"id": "task_4", "number": 4, "status": "in_progress", "title": "Premature ✅ DONE"}
        ]
        with open(os.path.join(root, "tasks.jsonl"), "w", encoding="utf-8") as f:
            for r in tasks_bad2:
                f.write(json.dumps(r) + "\n")
        rc, out = int_run_tool(root)
        int_check("in_progress task with ✅ DONE fails integrity check", rc != 0 and "title/status contradiction" in out, f"rc={rc} out={out}")

        # Negative control 3: pending with DONE this session
        tasks_bad3 = tasks_clean + [
            {"id": "task_5", "number": 5, "status": "pending", "title": "Unfulfilled DONE this session"}
        ]
        with open(os.path.join(root, "tasks.jsonl"), "w", encoding="utf-8") as f:
            for r in tasks_bad3:
                f.write(json.dumps(r) + "\n")
        rc, out = int_run_tool(root)
        int_check("pending task with DONE this session fails integrity check", rc != 0 and "title/status contradiction" in out, f"rc={rc} out={out}")
    finally:
        shutil.rmtree(root, ignore_errors=True)

    if int__fails > 0:
        return 1
    return 0


def main():
    # Every suite runs even when an earlier one fails; the old scripts called
    # sys.exit, which in one file would hide the suites after the first failure.
    rc = 0
    for fn in (sp_main, sch_main, agy_main, int_main):
        rc |= (fn() or 0)
    return rc


if __name__ == "__main__":
    sys.exit(main())
