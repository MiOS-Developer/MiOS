#!/usr/bin/env python3
# AI-hint: Test-and-fixture hygiene gates in one module: leaked fixtures, temp fixture cleanup, negative-test registration, Rust test coverage, schema consumers, tracked-file readability and module length. The subcommand selects the gate.
# AI-doc: usr/share/doc/mios/manual/tools.md
"""Test-and-fixture hygiene gates. One module, one subcommand per gate."""
from __future__ import annotations

import sys


import os
import subprocess
import sys

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

# Assembled, never written whole: a literal here would make this file its own
# first violation, which is how four probes in this repository have been found
# to trip the very check that scans for them.
lf_MARKER = "neg" + "test"
# A test that hides a file renames it aside; that suffix is a leak too, and
# a hidden file reads as a deletion rather than as an artefact.
lf_BACKUP_SUFFIXES = (".bak", ".negbak", ".orig", ".rej", ".softtest.bak",
                   ".neg-hidden", ".neg-bak", ".negtmp")

# The harness is allowed to name its own fixtures; that is where they belong.
lf_ALLOWED_PATHS = frozenset({
    "tests/drift-gate-negatives.sh",
    "tools/check-testhygiene.py",
    "automation/98-drift-checks.sh",
    "usr/share/mios/reference/manual-corpus.tsv",
    "automation/manifest.json",
    "tools/manifest.json",
    "specs/manifest.json",
    "root-manifest.json",
})


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mios_tracked import tracked, GitUnavailable  # noqa: E402

def lf_main() -> int:
    root = os.environ.get("MIOS_DRIFT_ROOT") or os.getcwd()
    viol = []

    try:
        paths = tracked(root)
    except GitUnavailable as exc:
        print("check-leaked-fixtures: %s" % exc, file=sys.stderr)
        return 1

    for path in paths:
        if path.endswith(lf_BACKUP_SUFFIXES):
            viol.append(f"{path}: a backup file is tracked; a negative test left it behind")
        if path in lf_ALLOWED_PATHS:
            continue
        full = os.path.join(root, path)
        try:
            with open(full, encoding="utf-8", errors="ignore") as fh:
                for n, line in enumerate(fh, 1):
                    if lf_MARKER in line:
                        viol.append(f"{path}:{n}: carries an injected test fixture: "
                                    f"{line.strip()[:90]}")
        except (OSError, ValueError):
            continue

    try:
        with open(os.path.join(root, "usr/share/mios/mios.toml"), "rb") as fh:
            ceiling = ((tomllib.load(fh).get("tests") or {}).get("max_leaked_fixtures"))
    except OSError:
        ceiling = None

    if ceiling is None:
        print("mios.toml has no [tests].max_leaked_fixtures -- an absent ceiling is a"
              " broken ratchet, not an open one")
        return 1
    if len(viol) > int(ceiling):
        print("\n".join(viol[:20]))
        if len(viol) > 20:
            print(f"... and {len(viol) - 20} more")
        print(f"leaked fixtures {len(viol)} > ceiling {ceiling}")
        return 1
    print(f"[check-leaked-fixtures] {len(viol)}/{ceiling} leaked fixture(s) in the"
          f" tracked tree", file=sys.stderr)
    return 0


import os
import subprocess
import sys

tfc_MARKERS = ("rmtree", "TemporaryDirectory", "addCleanup", "_mkdtemp_cleaned",
           "_cleanup_fixtures")
tfc_MAKER = "mkdtemp"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mios_tracked import tracked, GitUnavailable  # noqa: E402

def tfc_main() -> int:
    root = os.environ.get("MIOS_DRIFT_ROOT") or os.getcwd()
    try:
        paths = tracked(root, "tools/test_*.py", "tests/*.py",
                        "usr/lib/mios/agent-pipe/test_*.py",
                        "usr/libexec/mios/test_*.py")
    except GitUnavailable as exc:
        print("check-temp-fixture-cleanup: %s" % exc, file=sys.stderr)
        return 1
    viol = []
    for rel in sorted(paths):
        full = os.path.join(root, rel)
        try:
            with open(full, encoding="utf-8", errors="ignore") as fh:
                s = fh.read()
        except OSError:
            continue
        if tfc_MAKER not in s or rel.endswith("check-testhygiene.py"):
            continue
        if not any(m in s for m in tfc_MARKERS):
            viol.append("%s makes a temporary directory and never removes it -- "
                        "one survives every run" % rel)
    print("\n".join(viol))
    if viol:
        return 1
    print("[check-temp-fixture-cleanup] every temp-dir fixture is removed",
          file=sys.stderr)
    return 0


import os
import re
import sys
import tomllib

nr_HARNESS = "tests/drift-gate-negatives.sh"
nr_GATE = "automation/98-drift-checks.sh"
nr_TOML = "usr/share/mios/mios.toml"

def nr_main() -> int:
    root = os.environ.get("MIOS_DRIFT_ROOT") or os.getcwd()
    try:
        s = open(os.path.join(root, nr_HARNESS), encoding="utf-8", errors="replace").read()
    except OSError as exc:
        print("%s unreadable: %s" % (nr_HARNESS, exc))
        return 1
    s_harness = s
    defined = set(re.findall(r"^(test_[a-z0-9_]+)\(\)", s, re.M))
    invoked = set(re.findall(r"^\s*_run_test\s+(test_[a-z0-9_]+)\s*$", s, re.M))
    invoked |= set(re.findall(r"^\s*(test_[a-z0-9_]+)\s*$", s, re.M))
    orphans = sorted(defined - invoked)
    if orphans:
        print("negative test(s) defined but never invoked -- coverage that is not:")
        for o in orphans[:15]:
            print("  " + o)
        if len(orphans) > 15:
            print("  ... and %d more" % (len(orphans) - 15))
        return 1
    # The index has always described this gate as "every drift check has a
    # corresponding negative test registered". It only ever detected orphans,
    # so that half went unenforced. Ratchet it: shrink-only, seeded at the
    # measured gap.
    try:
        with open(os.path.join(root, nr_GATE), encoding="utf-8", errors="replace") as fh:
            gate = fh.read()
        with open(os.path.join(root, nr_TOML), "rb") as fh:
            ceiling = tomllib.load(fh)["tests"]["max_checks_without_negative"]
    except (OSError, KeyError) as exc:
        print("cannot read the gate or [tests].max_checks_without_negative: %s" % exc)
        return 1

    body = re.search(r"^main\(\) \{(.*?)^\}", gate, re.S | re.M)
    if not body:
        print("could not locate main() in %s -- the dispatch list is the subject" % nr_GATE)
        return 1
    dispatched = re.findall(r"^\s+(check_[a-z0-9_]+)\s*$", body.group(1), re.M)
    if len(dispatched) < 50:
        print("only %d dispatched checks parsed from main() -- the subject list is wrong"
              % len(dispatched))
        return 1
    uncovered = sorted(set(dispatched) - set(re.findall(r'(?:_neg_gate|\.sh"|"\$[A-Za-z_]\w*")[\s\\]+"?(check_[a-z0-9_]+)\b', s_harness)))
    if len(uncovered) > int(ceiling):
        print("drift checks with no negative test: %d > ceiling %d "
              "(write one, then lower [tests].max_checks_without_negative)"
              % (len(uncovered), ceiling))
        for u in uncovered[:15]:
            print("  " + u)
        return 1

    print("[check-negatives-registered] %d negative test(s), all invoked; "
          "%d/%d dispatched check(s) have none"
          % (len(defined), len(uncovered), ceiling), file=sys.stderr)
    return 0


"""A crate with no tests passes `cargo test` every time.

The workspace run prints `test result: ok. 0 passed` for each such crate, which
reads exactly like a crate whose tests all passed. Ten crates and roughly 5,600
lines are in that state, miosd alone being 3,550 of them, so the suite's green
says far less than it appears to.

The ceiling is shrink-only: a crate may be registered as untested with a reason,
and the count may only fall.
"""
import os
import re
import sys

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

rtc_WORKSPACES = ("src/mios-rs", "tools/native")
rtc_TEST_MARKERS = ("#[test]", "#[tokio::test]", "#[rstest]")
rtc_PRIMITIVE_WORDS = {"true", "false", "Ok", "Err", "Some", "None", "self", "Self"}

def rtc_extract_assertions(body: str) -> list[tuple[str, str]]:
    assertions = []
    pattern = re.compile(r'\b(assert(?:_eq|_ne|_matches)?)\s*!\s*\(', re.MULTILINE)
    for match in pattern.finditer(body):
        macro_name = match.group(1)
        start = match.end()
        depth = 1
        i = start
        in_str = False
        str_char = None
        escape = False
        while i < len(body) and depth > 0:
            ch = body[i]
            if escape:
                escape = False
            elif ch == '\\' and in_str:
                escape = True
            elif in_str:
                if ch == str_char:
                    in_str = False
            elif ch in ('"', "'"):
                in_str = True
                str_char = ch
            elif ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
            i += 1
        if depth == 0:
            args_str = body[start:i-1]
            assertions.append((macro_name, args_str))
    return assertions

def rtc_is_meaningful_assertion(args_str: str) -> bool:
    no_strings = re.sub(r'"([^"\\]|\\.)*"', '""', args_str)
    no_strings = re.sub(r"'([^'\\]|\\.)*'", "''", no_strings)
    no_comments = re.sub(r'//.*', '', no_strings)
    tokens = re.findall(r'\b[A-Za-z_][A-Za-z0-9_]*\b', no_comments)
    non_primitive = [t for t in tokens if t not in rtc_PRIMITIVE_WORDS]
    return len(non_primitive) > 0

def rtc_crate_tests(root: str, ws: str, crate: str) -> int:
    has_test_func = False
    meaningful_asserts = 0
    for sub in ("src", "tests", "benches"):
        base = os.path.join(root, ws, crate, sub)
        for dirpath, _dirs, files in os.walk(base):
            for f in files:
                if not f.endswith(".rs"):
                    continue
                try:
                    body = open(os.path.join(dirpath, f), encoding="utf-8",
                                errors="replace").read()
                except OSError:
                    continue
                if any(m in body for m in rtc_TEST_MARKERS):
                    has_test_func = True
                for _m_name, args in rtc_extract_assertions(body):
                    if rtc_is_meaningful_assertion(args):
                        meaningful_asserts += 1
    if has_test_func and meaningful_asserts > 0:
        return meaningful_asserts
    return 0

def rtc_crate_lines(root: str, ws: str, crate: str) -> int:
    n = 0
    base = os.path.join(root, ws, crate, "src")
    for dirpath, _dirs, files in os.walk(base):
        for f in files:
            if f.endswith(".rs"):
                try:
                    with open(os.path.join(dirpath, f), encoding="utf-8",
                              errors="replace") as fh:
                        n += sum(1 for _ in fh)
                except OSError:
                    pass
    return n

def rtc_main() -> int:
    root = os.environ.get("MIOS_DRIFT_ROOT") or os.environ.get("MIOS_ROOT") or os.getcwd()
    with open(os.path.join(root, "usr/share/mios/mios.toml"), "rb") as fh:
        rust = (tomllib.load(fh).get("rust") or {})

    registered = rust.get("untested_crates") or {}
    ceiling = rust.get("max_untested_crates")
    viol, untested, seen = [], [], 0

    for ws in rtc_WORKSPACES:
        wsdir = os.path.join(root, ws)
        if not os.path.isdir(wsdir):
            viol.append("workspace %s is missing -- the gate has nothing to inspect" % ws)
            continue
        for crate in sorted(os.listdir(wsdir)):
            if not os.path.isfile(os.path.join(wsdir, crate, "Cargo.toml")):
                continue
            seen += 1
            if rtc_crate_tests(root, ws, crate) == 0:
                untested.append("%s/%s" % (ws, crate))

    if not seen:
        print("no crate was inspected -- an empty scan reports the same green as a"
              " clean one")
        return 1

    for name in untested:
        if name not in registered:
            viol.append("%s ships %d source line(s) and not one test; cargo test"
                        " reports ok for it regardless"
                        % (name, rtc_crate_lines(root, *name.split("/", 1))))
        elif not str(registered[name]).strip():
            viol.append("%s is registered as untested with no reason" % name)

    for name in sorted(registered):
        if name not in untested:
            viol.append("%s is registered as untested but now has tests -- remove"
                        " the entry and lower the ceiling" % name)

    if ceiling is None:
        viol.append("[rust] has no max_untested_crates -- an absent ceiling is a"
                    " broken ratchet, not an open one")
    elif len(untested) > int(ceiling):
        viol.append("untested crates %d > ceiling %d" % (len(untested), ceiling))

    print("\n".join(viol))
    if viol:
        return 1
    print("[check-rust-test-coverage] %d crate(s); %d untested and registered"
          " (ceiling %s)" % (seen, len(untested), ceiling), file=sys.stderr)
    return 0


"""Gate: no schema table is dead (no reader, no writer, not registered)."""

import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mios_tracked import GitUnavailable  # noqa: E402

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover -- py<3.11
    import tomli as tomllib  # type: ignore

sc_SCHEMA = "usr/share/mios/postgres/schema-init.sql"
# Doc, generated and CONFIG surfaces MENTION a table without consuming it. A
# .toml in particular declares policy about a table ([security.redact].tables,
# this gate's own register) -- naming it there is not reading or writing it, and
# counting it would let the register satisfy itself.
sc_NON_CONSUMER_SUFFIXES = (".md", ".txt", ".tsv", ".json", ".snap", ".toml", ".negbak", ".bak")
sc_NON_CONSUMER_DIRS = ("/docs/", "usr/share/doc/", "usr/share/mios/reference/", "tasks.jsonl")
# A file GENERATED from mios.toml re-emits whatever the SSOT says -- including
# this gate's own register -- so a table name appearing there is an echo, not a
# consumer. Detected by the marker the renderers stamp, so a new projection is
# excluded automatically.
sc_GENERATED_MARKER = "GENERATED IN FULL from usr/share/mios/mios.toml"

def sc_is_tracked(root: str, rel: str) -> bool:
    """Whether git's INDEX carries rel, which survives the worktree copy going
    away. Raises GitUnavailable when git cannot answer at all."""
    try:
        r = subprocess.run(["git", "-C", root, "ls-files", "--", rel],
                           capture_output=True, text=True)
    except OSError as exc:
        raise GitUnavailable("git could not be run in %s: %s" % (root, exc))
    if r.returncode != 0:
        raise GitUnavailable(
            "git ls-files failed in %s (exit %d): %s"
            % (root, r.returncode, (r.stderr or "").strip() or "no message"))
    return bool(r.stdout.strip())

def sc_declared_tables(root: str) -> list:
    path = os.path.join(root, sc_SCHEMA)
    if not os.path.isfile(path):
        return []
    sql = open(path, encoding="utf-8", errors="replace").read()
    seen, out = set(), []
    for m in re.finditer(r'CREATE TABLE(?:\s+IF NOT EXISTS)?\s+([A-Za-z0-9_."]+)\s*\(',
                         sql, re.I):
        name = m.group(1).strip('"')
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out

def sc_has_consumer(root: str, table: str) -> bool:
    """True when some non-doc file outside the schema itself names the table.

    git grep exits 1 for "no match" and >1 for "could not search"; only the
    first is a verdict.
    """
    short = table.split(".")[-1]
    try:
        r = subprocess.run(["git", "-C", root, "grep", "-l", "--", short],
                           capture_output=True, text=True)
    except OSError as exc:
        raise GitUnavailable("git could not be run in %s: %s" % (root, exc))
    if r.returncode > 1:
        raise GitUnavailable(
            "git grep failed in %s (exit %d): %s"
            % (root, r.returncode, (r.stderr or "").strip() or "no message"))
    for f in r.stdout.split():
        if f == sc_SCHEMA:
            continue
        if f.endswith(sc_NON_CONSUMER_SUFFIXES):
            continue
        if any(d in f for d in sc_NON_CONSUMER_DIRS):
            continue
        try:
            with open(os.path.join(root, f), encoding="utf-8", errors="replace") as fh:
                if sc_GENERATED_MARKER in fh.read(4096):
                    continue
        except OSError:
            pass
        return True
    return False

def sc_main() -> int:
    root = os.environ.get("MIOS_DRIFT_ROOT") or os.environ.get("MIOS_ROOT") or "."
    with open(os.path.join(root, "usr/share/mios/mios.toml"), "rb") as fh:
        cfg = tomllib.load(fh)
    reg = (cfg.get("schema") or {}).get("unconsumed") or []
    registered = {}
    for row in reg:
        if isinstance(row, dict) and row.get("table"):
            registered[str(row["table"])] = str(row.get("reason") or "")

    tables = sc_declared_tables(root)
    if not tables:
        # Absent-though-TRACKED is a dropped deliverable, not a partial checkout.
        try:
            dropped = sc_is_tracked(root, sc_SCHEMA)
        except GitUnavailable as exc:
            print("cannot tell whether %s is tracked: %s" % (sc_SCHEMA, exc))
            return 1
        if dropped:
            print("%s is tracked but declares no CREATE TABLE -- the gate's whole "
                  "subject is missing, which is not a pass" % sc_SCHEMA)
            return 1
        print("schema-init.sql declares no tables (partial checkout)")
        return 0

    bad, live_registered = [], set()
    for t in tables:
        try:
            consumed = sc_has_consumer(root, t)
        except GitUnavailable as exc:
            print("cannot search the tree for table consumers: %s" % exc)
            return 1
        if t in registered:
            if consumed:
                bad.append(f"{t} is in [schema].unconsumed but now HAS a consumer "
                           f"-- remove its entry; the register only shrinks")
            else:
                live_registered.add(t)
        elif not consumed:
            bad.append(f"{t} has no reader and no writer anywhere -- wire it, drop "
                       f"it, or record it in [schema].unconsumed with a reason")
    for t in sorted(set(registered) - set(tables)):
        bad.append(f"[schema].unconsumed names {t}, which schema-init.sql no "
                   f"longer declares -- drop the entry")

    if bad:
        for line in bad:
            print(line)
        return 1
    print(f"every schema table has a consumer "
          f"(tables={len(tables)} registered-unconsumed={len(live_registered)})")
    return 0


"""Makes 49 silent per-file drops observable from one place.

Those gates share this corpus, so a pass means their `except OSError:
continue` handlers are unreachable. See 20cd4fdf.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mios_tracked import tracked, GitUnavailable  # noqa: E402


def tr_scan(root: str):
    """Returns (missing, unreadable) for the tracked tree under root."""
    missing, unreadable = [], []
    for rel in tracked(root):
        full = os.path.join(root, rel)
        if not os.path.exists(full):
            missing.append(rel)
            continue
        try:
            with open(full, "rb") as fh:
                fh.read(1)
        except OSError as exc:
            unreadable.append((rel, str(exc)))
    return missing, unreadable


def tr_main() -> int:
    root = os.environ.get("MIOS_DRIFT_ROOT") or os.getcwd()
    try:
        missing, unreadable = tr_scan(root)
    except GitUnavailable as exc:
        print("check-tracked-readable: %s" % exc, file=sys.stderr)
        return 1

    for rel in missing[:20]:
        print("    tracked but absent from the worktree: %s" % rel, file=sys.stderr)
    for rel, why in unreadable[:20]:
        print("    tracked but unreadable: %s (%s)" % (rel, why), file=sys.stderr)
    total = len(missing) + len(unreadable)
    if total:
        if total > 20:
            print("    ... and %d more" % (total - 20), file=sys.stderr)
        print("%d tracked file(s) cannot be read, so every corpus-scanning gate "
              "silently drops them and still reports clean" % total, file=sys.stderr)
        return 1

    print("[check-tracked-readable] every tracked file is present and readable")
    return 0


"""Shrink-only module-size ratchet for the agent-pipe extraction (check 149)."""

import os
import sys

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover -- py<3.11
    import tomli as tomllib  # type: ignore

ml_PKG = os.path.join("usr", "lib", "mios", "agent-pipe")
# Operator ruling: the ceiling governs SUB-MODULES only -- the packages named by
# [refactor].submodule_roots. The agent-pipe root (server.py, the mios_*.py
# components) holds main modules, where features fold in. Shims are excluded --
# they are ~28 lines of lazy re-export.

def ml_load_policy(root: str) -> tuple:
    """Return (max_lines, {path: recorded_lines}, submodule_roots) from [refactor]."""
    with open(os.path.join(root, "usr/share/mios/mios.toml"), "rb") as fh:
        data = tomllib.load(fh)
    sec = data.get("refactor") or {}
    max_lines = int(sec.get("max_lines") or 800)
    recorded = {}
    for row in sec.get("oversize") or []:
        if isinstance(row, dict) and row.get("path"):
            recorded[str(row["path"])] = int(row.get("lines") or 0)
    roots = [str(r) for r in sec.get("submodule_roots") or []]
    if not roots or any(not r.endswith("/") for r in roots):
        raise SystemExit("[refactor].submodule_roots must list package prefixes ending in '/'")
    return max_lines, recorded, roots

def ml__is_shim(path: str) -> bool:
    """A lazy re-export shim (~28 lines) is not a module worth sizing."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return "Re-export shim for" in fh.read(400)
    except OSError:
        return False

def ml__count(path: str) -> int:
    with open(path, "rb") as fh:
        return sum(1 for _ in fh)

def ml_scan(root: str) -> tuple:
    """Return (violations, checked). A violation is a human-readable string."""
    max_lines, recorded, roots = ml_load_policy(root)
    base = os.path.join(root, ml_PKG)
    if not os.path.isdir(base):
        return [], 0
    seen = set()
    bad = []
    checked = 0
    scanned = set()
    walked = []
    for sub in roots:
        top = os.path.normpath(os.path.join(base, sub))
        if not os.path.isdir(top):
            continue
        for dirpath, _dirs, files in os.walk(top):
            walked.append((dirpath, sorted(files)))
    for dirpath, files in walked:
        for fn in files:
            if not fn.endswith(".py") or fn == "__init__.py":
                continue
            full = os.path.join(dirpath, fn)
            if not os.path.isfile(full):
                continue
            rel = os.path.relpath(full, base).replace(os.sep, "/")
            if rel in scanned:
                continue
            scanned.add(rel)
            if ml__is_shim(full):
                continue
            checked += 1
            n = ml__count(full)
            if rel in recorded:
                seen.add(rel)
                if n > recorded[rel]:
                    bad.append(
                        f"{rel} grew to {n} lines, above its recorded "
                        f"{recorded[rel]} -- the oversize register only ratchets DOWN")
                elif n < recorded[rel]:
                    bad.append(
                        f"{rel} is now {n} lines (recorded {recorded[rel]}) -- "
                        f"lower its [refactor].oversize entry to lock the win in")
            elif n > max_lines:
                bad.append(
                    f"{rel} is {n} lines, above the {max_lines}-line limit -- "
                    f"split it; do NOT add it to [refactor].oversize")
    for rel in sorted(set(recorded) - seen):
        bad.append(
            f"[refactor].oversize names a file that no longer exists: {rel}")
    return bad, checked

def ml_main() -> int:
    root = os.environ.get("MIOS_DRIFT_ROOT") or os.environ.get("MIOS_ROOT") or "."
    bad, checked = ml_scan(root)
    if bad:
        for line in bad:
            print(line)
        return 1
    max_lines, recorded, _roots = ml_load_policy(root)
    print(f"agent-pipe modules within the size ratchet "
          f"(checked={checked} limit={max_lines} grandfathered={len(recorded)})")
    return 0

_GATES = {"leaked-fixtures": lf_main, "temp-fixture-cleanup": tfc_main, "negatives-registered": nr_main, "rust-test-coverage": rtc_main, "schema-consumers": sc_main, "tracked-readable": tr_main, "module-length": ml_main}


def main() -> int:
    # An unknown or missing subcommand must FAIL, never report a clean gate.
    if len(sys.argv) < 2 or sys.argv[1] not in _GATES:
        sys.stderr.write("usage: check-testhygiene.py {%s}\n" % "|".join(sorted(_GATES)))
        return 2
    return _GATES[sys.argv.pop(1)]()


if __name__ == "__main__":
    sys.exit(main())
