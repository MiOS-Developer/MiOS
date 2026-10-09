#!/usr/bin/env python3
# AI-hint: Task-plane drift gates in one module: the retired TASKS.md table-vs-section parity, AGY task schema, and task id/dependency resolution, over the frozen lists tasks.jsonl keeps (ADR-0028).
# AI-doc: usr/share/doc/mios/manual/tools.md
# AI-functions: main, status_parity_main, schema_main, agy_main
"""Task-plane drift gates. One module, one subcommand per gate.

The frozen lists name every task by its typed id, task_<number> (ADR-0028: migrate-ids rewrote the old ids).
"""

import json
import os
import re
import sys


TASKS = "TASKS.md"
AGY_TASKS = "AGY-TASKS.md"
PLACEHOLDER = "?"
# A typed task id; the number is the task's creation-order number in tasks.jsonl.
TASK = r"task_([1-9][0-9]*)"


def store_path(root: str):
    try:
        with open(os.path.join(root, "usr/share/mios/mios.toml"), "rb") as fh:
            return os.path.join(root, tomllib.load(fh)["tasks"]["store"]["path"])
    except (FileNotFoundError, KeyError):
        return None


def list_text(root: str, name: str):
    """A retired task list rebuilt from its frozen slices in tasks.jsonl (ADR-0028), else the file itself."""
    parts = []
    store = store_path(root)
    if store and os.path.isfile(store):
        with open(store, encoding="utf-8") as fh:
            parts = sorted((s["offset"], s["text"]) for rec in map(json.loads, fh)
                           for s in (rec.get("provenance") or {}).get("sources") or [] if s["file"] == "MiOS:" + name)
    if parts:
        return "".join(t for _, t in parts)
    path = os.path.join(root, name)
    return open(path, encoding="utf-8", errors="replace").read() if os.path.isfile(path) else None


def task_numbers(root: str):
    """Every task number tasks.jsonl holds, or None for a fixture root without one."""
    store = store_path(root)
    if not store or not os.path.isfile(store):
        return None
    with open(store, encoding="utf-8") as fh:
        return {r["number"] for r in map(json.loads, fh) if isinstance(r.get("number"), int)}


KNOWN = {
    "done", "done-by-code", "completed", "retired",
    "planned", "planned/unverified", "in-progress", "pending",
    "partial", "open", "blocked", "built-gated-off",
}

_SECTION_RE = re.compile(r"^## (" + TASK + r")\s*(?:--|:)\s*(.*?)(?=^## |\Z)", re.M | re.S)
_STATUS_RE = re.compile(r"^\*\*Status:\*\*\s*(.+?)\s*(?:\||$)", re.M)
_ROW_RE = re.compile(r"^\|\s*(" + TASK + r")\s*\|\s*P\d\s*\|\s*([^|]+?)\s*\|")
# A heading of the frozen AGY-TASKS.md: '## task_12 -- title' (a range banner is one task too).
_HEAD_RE = re.compile(r"^#{2,3} " + TASK + r"(?=\s)", re.M)


def status_parity_head_token(status: str) -> str:
    """The comparable head of a free-prose status: everything before the first
    ` -- ` continuation or ` (` qualifier."""
    return re.split(r"\s+--\s+|\s*\(", status, maxsplit=1)[0].strip().rstrip(".,;:").lower()


def status_parity_detail_statuses(text: str) -> dict:
    out = {}
    for m in _SECTION_RE.finditer(text):
        sm = _STATUS_RE.search(m.group(0))
        if sm:
            out[m.group(1)] = sm.group(1).strip()
    return out


def status_parity_table_rows(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        m = _ROW_RE.match(line)
        if m:
            out[m.group(1)] = m.group(3).strip()
    return out


def heading_numbers(text) -> set:
    return {int(m.group(1)) for m in _HEAD_RE.finditer(text or "")}


def status_parity_main() -> int:
    """Gate: TASKS.md summary table agrees with each section, and every task it names exists."""
    root = os.environ.get("MIOS_DRIFT_ROOT", os.environ.get("MIOS_TOML_ROOT", "."))
    text = list_text(root, TASKS)
    if text is None:
        print(f"{TASKS} not found under {root}, on disk or in tasks.jsonl")
        return 1
    detail = status_parity_detail_statuses(text)
    rows = status_parity_table_rows(text)
    if not rows:
        print(f"{TASKS} summary table has no parseable rows")
        return 1

    problems = []
    for tid in sorted(rows):
        cell = rows[tid]
        if tid not in detail:
            if cell == PLACEHOLDER:
                problems.append(f"{tid}: status is '?' and the task has no section to resolve it")
            elif cell not in KNOWN:
                problems.append(f"{tid}: unknown status '{cell}' in the summary table")
            continue
        want = status_parity_head_token(detail[tid])
        if cell == PLACEHOLDER:
            problems.append(
                f"{tid}: summary table says '?' while the task section says '{want}'")
        elif cell != want:
            problems.append(
                f"{tid}: summary table says '{cell}', the task section says '{want}'")
        if want not in KNOWN:
            problems.append(f"{tid}: unknown status '{want}' in the task section")

    for tid in sorted(set(detail) - set(rows)):
        problems.append(f"{tid}: has a task section but no row in the summary table")

    # Every task TASKS.md names exists: in tasks.jsonl, or (a fixture root) as a section, row or AGY heading.
    known = task_numbers(root)
    if known is None:
        known = {int(t[5:]) for t in set(rows) | set(detail)} | heading_numbers(list_text(root, AGY_TASKS))
    refs = [int(n) for n in re.findall(r"(?<![A-Za-z0-9_])" + TASK + r"(?![0-9])", text)]
    for ref in sorted(set(refs)):
        if ref not in known:
            problems.append(f"VIOLATION: TASKS.md references task_{ref}, which names no task")

    if problems:
        for p in problems:
            print(p)
        return 1

    closed = {"done", "done-by-code", "completed", "retired"}
    open_n = sum(1 for s in rows.values() if s not in closed)
    print(f"TASKS.md summary table matches every task section and every task it names exists "
          f"(tasks={len(rows)} sections={len(detail)} open={open_n} references={len(refs)})")
    return 0


try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

DONE_MARKER = "[DONE]"

REQUIRED = ("Goal", "What+How", "Where", "Verify", "Do NOT", "Done When", "Why", "Dep")


def typed_number(value, key: str) -> int:
    m = re.fullmatch(TASK, str(value))
    if not m:
        raise ValueError(f"[tasks].{key} = {value!r} is not a typed task id (task_<number>)")
    return int(m.group(1))


def schema_main() -> int:
    """Gate: every AGY task from the schema floor on carries the full schema; a missing Verify is a task anyone can call done."""
    root = os.environ.get("MIOS_DRIFT_ROOT") or os.getcwd()
    text = list_text(root, AGY_TASKS)
    if text is None:
        print(f"{AGY_TASKS} unreadable: not on disk and not in tasks.jsonl")
        return 1

    try:
        with open(os.path.join(root, "usr/share/mios/mios.toml"), "rb") as fh:
            tasks_tbl = (tomllib.load(fh).get("tasks") or {})
    except OSError:
        tasks_tbl = {}
    if tasks_tbl.get("schema_from") is None:
        print("mios.toml has no [tasks].schema_from -- without it the schema"
              " floor is a constant nobody can lower on purpose")
        return 1
    try:
        schema_from = typed_number(tasks_tbl["schema_from"], "schema_from")
        # Tasks created after the floor that the list files before it, so numbered below it.
        also = {typed_number(x, "schema_also") for x in tasks_tbl.get("schema_also") or []}
    except ValueError as e:
        print(e)
        return 1

    def demanded(tid: int, first_line: str) -> bool:
        return (tid >= schema_from or tid in also) and DONE_MARKER not in first_line

    blocks = re.split(r"(?=^#{2,3} task_[1-9][0-9]*\s)", text, flags=re.M)
    ids, viol = [], []
    for b in blocks:
        m = _HEAD_RE.match(b)
        if not m:
            continue
        tid = int(m.group(1))
        ids.append(tid)
        if not demanded(tid, b.split(chr(10))[0]):
            continue
        for field in REQUIRED:
            if f"**{field}:**" not in b:
                viol.append(f"task_{tid}: missing **{field}:**")

    known = task_numbers(root)
    if known is None:
        known = set(ids)
    for b in blocks:
        m = _HEAD_RE.match(b)
        if not m or not demanded(int(m.group(1)), b.split(chr(10))[0]):
            continue
        dep = re.search(r"^\*\*Dep:\*\*\s*(.+)$", b, re.M)
        if not dep:
            continue
        for ref in re.findall(TASK, dep.group(1)):
            if int(ref) not in known:
                viol.append(f"task_{m.group(1)}: **Dep:** names task_{ref}, which does not exist")

    dupes = sorted({i for i in ids if ids.count(i) > 1})
    ceil = tasks_tbl.get("max_duplicate_ids")
    if ceil is None:
        viol.append("mios.toml has no [tasks].max_duplicate_ids -- absent is a broken"
                    " ceiling, not an open one")
    elif len(dupes) > int(ceil):
        viol.append(f"duplicate task ids {len(dupes)} > ceiling {ceil}: "
                    f"{['task_%d' % d for d in dupes[:8]]}")

    print("\n".join(viol))
    if not viol:
        n = sum(1 for i in ids if i >= schema_from or i in also)
        print(f"[check-task-schema] {n} task(s) carry the full schema; "
              f"{len(dupes)}/{ceil} duplicate ids", file=sys.stderr)
    return 1 if viol else 0


def agy_extract_dep_ids(dep_str: str) -> list:
    """Every task a Dep line names; a span 'task_a..task_b' names its two ends."""
    return [int(n) for n in re.findall(TASK, dep_str)]


def agy_main() -> int:
    """Gate: AGY-TASKS.md headings are unique and every Dep names a task that exists."""
    root = os.environ.get("MIOS_DRIFT_ROOT", os.environ.get("MIOS_TOML_ROOT", "."))
    content = list_text(root, AGY_TASKS)
    if content is None:
        print(f"VIOLATION: {AGY_TASKS} not found under {root}, on disk or in tasks.jsonl")
        return 1

    header_pattern = re.compile(r"^#+\s*" + TASK + r"(?:\s+.*)?$")
    task_occurrences = {}
    for line_idx, line in enumerate(content.splitlines(), 1):
        m = header_pattern.match(line)
        if m:
            task_occurrences.setdefault(int(m.group(1)), []).append(line_idx)

    problems = []
    for tid, lines in task_occurrences.items():
        if len(lines) > 1:
            locs = ", ".join(f"line {n}" for n in lines)
            problems.append(f"VIOLATION: task_{tid} is defined multiple times ({locs})")

    known = task_numbers(root)
    if known is None:
        known = set(task_occurrences)
    dep_pattern = re.compile(r"\*\*Dep:\*\*\s*(.*)", re.IGNORECASE)
    refs = 0
    for line_idx, line in enumerate(content.splitlines(), 1):
        m = dep_pattern.search(line)
        if not m:
            continue
        dep_str = m.group(1).strip()
        if dep_str.lower() in ("none", "n/a", ""):
            continue
        for ref_id in agy_extract_dep_ids(dep_str):
            refs += 1
            if ref_id not in known:
                problems.append(
                    f"VIOLATION: line {line_idx} has dangling dependency reference task_{ref_id}"
                )

    if problems:
        for p in problems:
            print(p)
        return 1

    print(f"AGY task id parity check passed (headings={len(task_occurrences)}, dep_references={refs})")
    return 0


_GATES = {"status-parity": status_parity_main, "schema": schema_main, "agy": agy_main}


def main() -> int:
    # An unknown or missing subcommand must FAIL, never report a clean gate.
    if len(sys.argv) < 2 or sys.argv[1] not in _GATES:
        sys.stderr.write("usage: check-tasks.py {%s}\n" % "|".join(sorted(_GATES)))
        return 2
    return _GATES[sys.argv.pop(1)]()


if __name__ == "__main__":
    sys.exit(main())
