#!/usr/bin/env python3
# AI-hint: Sibling unit tests for tools/check-testhygiene.py -- one suite per subcommand; the unittest suites run under one discovery pass, the two script-style suites return their own verdict.
"""Sibling tests for the consolidated test-and-fixture hygiene gates."""
from __future__ import annotations

import sys
import unittest


"""The scan found five real leaks on its first run; these cases keep it able to.

Each test plants the shape in a throwaway git repository and asserts the checker
goes red, because a leak detector that cannot detect is worse than none: it
stops anyone looking.
"""
import importlib.util
import os
import subprocess
import tempfile
import unittest

tlf__HERE = os.path.dirname(os.path.abspath(__file__))
tlf__ROOT = os.path.dirname(tlf__HERE)
tlf__MARKER = "neg" + "test"

def tlf__load():
    spec = importlib.util.spec_from_file_location(
        "check_leaked_fixtures", os.path.join(tlf__HERE, "check-testhygiene.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

tlf_MOD = tlf__load()

tlf__MADE = []

def tlf__repo(files):
    """A throwaway git repo tracking `files` (name -> content), with a ceiling of 0.

    Registered for removal: mkdtemp leaves the directory behind, and a git repo
    left in the temp directory shows up as a checkout in an editor's source
    control view. Five of them did.
    """
    d = tempfile.mkdtemp(prefix="mios-leakfix-")
    tlf__MADE.append(d)
    subprocess.run(["git", "init", "-q", d], check=False,
                   capture_output=True)
    os.makedirs(os.path.join(d, "usr", "share", "mios"), exist_ok=True)
    with open(os.path.join(d, "usr/share/mios/mios.toml"), "w",
              encoding="utf-8") as fh:
        fh.write("[tests]\nmax_leaked_fixtures = 0\n")
    for name, body in files.items():
        full = os.path.join(d, name)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(body)
    subprocess.run(["git", "-C", d, "add", "-A"], check=False,
                   capture_output=True)
    return d

def tlf__run(root):
    old = os.environ.get("MIOS_DRIFT_ROOT")
    os.environ["MIOS_DRIFT_ROOT"] = root
    try:
        return tlf_MOD.lf_main()
    finally:
        if old is None:
            os.environ.pop("MIOS_DRIFT_ROOT", None)
        else:
            os.environ["MIOS_DRIFT_ROOT"] = old

class tlf_TestLeakedFixtures(unittest.TestCase):
    def test_a_clean_tree_passes(self):
        self.assertEqual(0, tlf__run(tlf__repo({"a.sh": "echo hello\n"})))

    def test_an_injected_marker_fails(self):
        body = "CREATE TABLE mios_%s_orphan (id int);\n" % tlf__MARKER
        self.assertNotEqual(0, tlf__run(tlf__repo({"schema.sql": body})))

    def test_a_tracked_backup_file_fails(self):
        self.assertNotEqual(0, tlf__run(tlf__repo({"thing.ps1.negbak": "x\n"})))

    def test_a_hidden_file_fails(self):
        """A test that hides a file renames it aside; one had reached HEAD."""
        self.assertNotEqual(0, tlf__run(tlf__repo({"ch01.md.neg-hidden": "x\n"})))

    def test_an_absent_ceiling_fails(self):
        d = tlf__repo({"a.sh": "true\n"})
        os.remove(os.path.join(d, "usr/share/mios/mios.toml"))
        self.assertNotEqual(0, tlf__run(d))

    def test_the_shipped_tree_is_clean(self):
        self.assertEqual(0, tlf__run(tlf__ROOT))

def tlf_tearDownModule():
    """Remove every fixture repo, whatever the outcome of the tests.

    git marks its objects read-only, and on Windows a read-only file refuses
    deletion, so a plain rmtree leaves the repository behind -- five of them
    turned up in an editor's source control view. rmtree's error hook is not a
    portable fix either: the onerror parameter was removed in 3.14. Making
    everything writable first needs no hook at all.
    """
    import shutil
    import stat

    for d in tlf__MADE:
        for base, dirs, files in os.walk(d):
            for name in dirs + files:
                try:
                    os.chmod(os.path.join(base, name), stat.S_IWRITE | stat.S_IREAD)
                except OSError:
                    pass
        shutil.rmtree(d, ignore_errors=True)
    tlf__MADE.clear()


import importlib.util
import os
import unittest

ttfc__HERE = os.path.dirname(os.path.abspath(__file__))
ttfc__ROOT = os.path.dirname(ttfc__HERE)

def ttfc__load():
    spec = importlib.util.spec_from_file_location(
        "check_temp_fixture_cleanup",
        os.path.join(ttfc__HERE, "check-testhygiene.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

ttfc_MOD = ttfc__load()

class ttfc_TestCleanupGate(unittest.TestCase):
    def test_the_markers_cover_the_common_idioms(self):
        for m in ("rmtree", "TemporaryDirectory", "addCleanup"):
            self.assertIn(m, ttfc_MOD.tfc_MARKERS)

    def test_the_shipped_tree_is_clean(self):
        os.environ["MIOS_DRIFT_ROOT"] = ttfc__ROOT
        self.assertEqual(0, ttfc_MOD.tfc_main())

    def test_every_test_that_makes_a_temp_dir_declares_a_cleanup(self):
        """The gate's own claim, restated where a reader can see it fail."""
        import subprocess
        out = subprocess.run(["git", "-C", ttfc__ROOT, "ls-files",
                              "tools/test_*.py", "tests/*.py",
                              "usr/lib/mios/agent-pipe/test_*.py"],
                             capture_output=True, text=True, check=False).stdout
        for rel in (p.strip() for p in out.splitlines() if p.strip()):
            full = os.path.join(ttfc__ROOT, rel)
            try:
                with open(full, encoding="utf-8", errors="ignore") as fh:
                    s = fh.read()
            except OSError:
                continue
            if ttfc_MOD.tfc_MAKER in s and not rel.endswith("check-testhygiene.py"):
                self.assertTrue(any(m in s for m in ttfc_MOD.tfc_MARKERS), rel)


import importlib.util
import os
import re
import unittest

tnr__HERE = os.path.dirname(os.path.abspath(__file__))
tnr__ROOT = os.path.dirname(tnr__HERE)

def tnr__load():
    spec = importlib.util.spec_from_file_location(
        "check_negatives_registered",
        os.path.join(tnr__HERE, "check-testhygiene.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

tnr_MOD = tnr__load()

class tnr_TestNegativesRegistered(unittest.TestCase):
    def test_the_shipped_harness_invokes_everything_it_defines(self):
        os.environ["MIOS_DRIFT_ROOT"] = tnr__ROOT
        self.assertEqual(0, tnr_MOD.nr_main())

    def test_the_harness_defines_a_substantial_number(self):
        """A harness that defines nothing would pass vacuously."""
        s = open(os.path.join(tnr__ROOT, tnr_MOD.nr_HARNESS), encoding="utf-8").read()
        self.assertGreater(len(set(re.findall(r"^(test_[a-z0-9_]+)\(\)", s, re.M))), 100)

    def test_an_unregistered_test_is_detected(self):
        """The regex pair is the whole gate; assert it separates the two sets."""
        s = "test_alpha() {\n:\n}\ntest_beta() {\n:\n}\n    _run_test test_alpha\n"
        defined = set(re.findall(r"^(test_[a-z0-9_]+)\(\)", s, re.M))
        invoked = set(re.findall(r"^\s*_run_test\s+(test_[a-z0-9_]+)\s*$", s, re.M))
        self.assertEqual({"test_beta"}, defined - invoked)


import os
import unittest
from importlib.machinery import SourceFileLoader

trtc__HERE = os.path.dirname(os.path.abspath(__file__))
trtc_mod = SourceFileLoader(
    "check_rust_test_coverage", os.path.join(trtc__HERE, "check-testhygiene.py")).load_module()

class trtc_TestCheckRustTestCoverage(unittest.TestCase):
    def test_import_and_main_callable(self):
        self.assertTrue(hasattr(trtc_mod, "main"))
        self.assertTrue(callable(trtc_mod.main))


import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile

tsc__HERE = os.path.dirname(os.path.abspath(__file__))
tsc__spec = importlib.util.spec_from_file_location(
    "check_schema_consumers", os.path.join(tsc__HERE, "check-testhygiene.py"))
tsc_M = importlib.util.module_from_spec(tsc__spec)
tsc__spec.loader.exec_module(tsc_M)

tsc__fails = 0

def tsc_check(name, cond, detail=""):
    global tsc__fails
    if cond:
        print(f"ok   - {name}")
    else:
        tsc__fails += 1
        print(f"FAIL - {name}" + (f" -- {detail}" if detail else ""))

tsc__MADE = []

def tsc__cleanup_fixtures():
    """Remove the fixture repos this module made.

    mkdtemp leaves its directory behind, so every run added one per fixture;
    forty had accumulated in the temp directory, where an editor lists any of
    them that contain a repository as a checkout.
    """
    import shutil
    import stat

    for d in tsc__MADE:
        for base, dirs, files in os.walk(d):
            for name in dirs + files:
                try:
                    os.chmod(os.path.join(base, name), stat.S_IWRITE | stat.S_IREAD)
                except OSError:
                    pass
        shutil.rmtree(d, ignore_errors=True)
    tsc__MADE.clear()

def tsc_mkrepo(tables, consumers=None, register=(), doc_mentions=(), toml_mentions=()):
    """tables: names to CREATE. consumers: {table: relpath} code files that
    reference it. register: [(table, reason)]. Returns the repo root."""
    root = tempfile.mkdtemp(prefix="schemacons-")
    tsc__MADE.append(root)
    os.makedirs(os.path.join(root, "usr/share/mios/postgres"), exist_ok=True)
    os.makedirs(os.path.join(root, "usr/lib/mios"), exist_ok=True)
    os.makedirs(os.path.join(root, "usr/share/doc/mios"), exist_ok=True)

    sql = "".join(f"CREATE TABLE IF NOT EXISTS {t} (id bigint);\n" for t in tables)
    open(os.path.join(root, tsc_M.sc_SCHEMA), "w").write(sql)

    rows = "\n".join('    { table = "%s", reason = "%s" },' % (t, r) for t, r in register)
    open(os.path.join(root, "usr/share/mios/mios.toml"), "w").write(
        "[schema]\nunconsumed = [\n%s\n]\n" % rows
        + "".join('# policy mentions %s\n' % t for t in toml_mentions))

    for table, rel in (consumers or {}).items():
        full = os.path.join(root, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        open(full, "w").write(f'SQL = "SELECT * FROM {table}"\n')

    for t in doc_mentions:
        open(os.path.join(root, "usr/share/doc/mios/notes.md"), "a").write(
            f"the {t} table is planned\n")

    subprocess.run(["git", "-C", root, "init", "-q"], check=True)
    subprocess.run(["git", "-C", root, "add", "-A"], check=True,
                   capture_output=True)
    return root

def tsc_run(root, git=None):
    """git: a directory to prepend to PATH, used to stand a refusing git in
    front of the real one."""
    env = dict(os.environ, MIOS_DRIFT_ROOT=root)
    if git:
        env["PATH"] = git + os.pathsep + env.get("PATH", "")
    r = subprocess.run([sys.executable, os.path.join(tsc__HERE, "check-testhygiene.py"), "schema-consumers"],
                       capture_output=True, text=True, env=env)
    return r.returncode, r.stdout + r.stderr

def tsc_mkshim():
    """A git that refuses, the way one does over a foreign-owned checkout."""
    d = tempfile.mkdtemp(prefix="gitshim-")
    tsc__MADE.append(d)
    p = os.path.join(d, "git")
    open(p, "w").write('#!/bin/sh\n'
                       'echo "fatal: detected dubious ownership in repository" >&2\n'
                       'exit 128\n')
    os.chmod(p, 0o755)
    return d

def tsc_t_real_consumer_passes():
    r = tsc_mkrepo(["knowledge"], consumers={"knowledge": "usr/lib/mios/reader.py"})
    try:
        rc, out = tsc_run(r)
        tsc_check("a table with a code consumer passes", rc == 0, out)
    finally:
        shutil.rmtree(r, ignore_errors=True)

def tsc_t_dead_table_fails():
    r = tsc_mkrepo(["ghost"])
    try:
        rc, out = tsc_run(r)
        tsc_check("a table with no consumer fails", rc == 1, out)
        tsc_check("the message says what to do", "wire it, drop it, or record it" in out, out)
    finally:
        shutil.rmtree(r, ignore_errors=True)

def tsc_t_doc_mention_is_not_a_consumer():
    r = tsc_mkrepo(["ghost"], doc_mentions=["ghost"])
    try:
        rc, out = tsc_run(r)
        tsc_check("a doc mention does NOT count as a consumer", rc == 1, out)
    finally:
        shutil.rmtree(r, ignore_errors=True)

def tsc_t_toml_mention_is_not_a_consumer():
    # The register itself names the table; if .toml counted, the register
    # would satisfy the gate on its own and the whole check would be vacuous.
    r = tsc_mkrepo(["ghost"], toml_mentions=["ghost"])
    try:
        rc, out = tsc_run(r)
        tsc_check("a .toml mention does NOT count as a consumer", rc == 1, out)
    finally:
        shutil.rmtree(r, ignore_errors=True)

def tsc_t_generated_projection_is_not_a_consumer():
    """A file generated FROM mios.toml re-emits the register itself, so counting
    it would make every registered table look consumed -- which is exactly what
    happened once automation/lib/globals.sh was regenerated."""
    r = tsc_mkrepo(["ghost"], register=[("ghost", "planned")])
    try:
        gen = os.path.join(r, "automation/lib/globals.sh")
        os.makedirs(os.path.dirname(gen), exist_ok=True)
        with open(gen, "w") as fh:
            fh.write("# AI-hint: GENERATED IN FULL from usr/share/mios/mios.toml\n"
                     "MIOS_SCHEMA_UNCONSUMED_0_TABLE='ghost'\n")
        subprocess.run(["git", "-C", r, "add", "-A"], check=True, capture_output=True)
        rc, out = tsc_run(r)
        tsc_check("a GENERATED projection does NOT count as a consumer", rc == 0, out)
        tsc_check("the table stays registered rather than looking wired",
              "registered-unconsumed=1" in out, out)
    finally:
        shutil.rmtree(r, ignore_errors=True)

def tsc_t_registered_dead_table_passes():
    r = tsc_mkrepo(["ghost"], register=[("ghost", "planned")])
    try:
        rc, out = tsc_run(r)
        tsc_check("a REGISTERED dead table passes", rc == 0, out)
        tsc_check("the count is reported", "registered-unconsumed=1" in out, out)
    finally:
        shutil.rmtree(r, ignore_errors=True)

def tsc_t_registered_table_that_gained_a_consumer_fails():
    r = tsc_mkrepo(["ghost"], consumers={"ghost": "usr/lib/mios/reader.py"},
               register=[("ghost", "planned")])
    try:
        rc, out = tsc_run(r)
        tsc_check("a registered table that GAINED a consumer fails (register shrinks)",
              rc == 1 and "now HAS a consumer" in out, out)
    finally:
        shutil.rmtree(r, ignore_errors=True)

def tsc_t_stale_register_entry_fails():
    r = tsc_mkrepo(["knowledge"], consumers={"knowledge": "usr/lib/mios/reader.py"},
               register=[("gone", "planned")])
    try:
        rc, out = tsc_run(r)
        tsc_check("a register entry for a dropped table fails",
              rc == 1 and "no longer declares" in out, out)
    finally:
        shutil.rmtree(r, ignore_errors=True)

def tsc_t_deleted_tracked_schema_fails():
    """The subject of the gate, deleted. It stays in the index, so this is a
    dropped deliverable and not the partial checkout the skip was written for."""
    r = tsc_mkrepo(["knowledge"], consumers={"knowledge": "usr/lib/mios/reader.py"})
    try:
        os.remove(os.path.join(r, tsc_M.sc_SCHEMA))
        rc, out = tsc_run(r)
        tsc_check("deleting the TRACKED schema fails rather than passing",
              rc == 1, out)
        tsc_check("the message names the missing subject",
              "declares no CREATE TABLE" in out, out)
    finally:
        shutil.rmtree(r, ignore_errors=True)

def tsc_t_untracked_missing_schema_still_skips():
    """A checkout that never had the file is the state the skip exists for."""
    r = tsc_mkrepo(["knowledge"], consumers={"knowledge": "usr/lib/mios/reader.py"})
    try:
        os.remove(os.path.join(r, tsc_M.sc_SCHEMA))
        subprocess.run(["git", "-C", r, "rm", "-q", "--cached", tsc_M.sc_SCHEMA],
                       check=True, capture_output=True)
        rc, out = tsc_run(r)
        tsc_check("an UNtracked missing schema still skips", rc == 0, out)
        tsc_check("the skip says why", "partial checkout" in out, out)
    finally:
        shutil.rmtree(r, ignore_errors=True)

def tsc_t_refusing_git_is_not_a_verdict_on_the_tables():
    """git grep exits >1 when it cannot search. Reading that as "no match" made
    every live table look dead, and the remedy the message named would have
    registered the whole schema as unconsumed."""
    r = tsc_mkrepo(["knowledge"], consumers={"knowledge": "usr/lib/mios/reader.py"})
    try:
        rc, out = tsc_run(r, git=tsc_mkshim())
        tsc_check("a refusing git fails the gate", rc == 1, out)
        tsc_check("it blames git, not the tables",
              "cannot" in out and "no reader and no writer" not in out, out)
    finally:
        shutil.rmtree(r, ignore_errors=True)

def tsc_main():
    tsc_t_real_consumer_passes()
    tsc_t_deleted_tracked_schema_fails()
    tsc_t_untracked_missing_schema_still_skips()
    tsc_t_refusing_git_is_not_a_verdict_on_the_tables()
    tsc_t_dead_table_fails()
    tsc_t_doc_mention_is_not_a_consumer()
    tsc_t_toml_mention_is_not_a_consumer()
    tsc_t_generated_projection_is_not_a_consumer()
    tsc_t_registered_dead_table_passes()
    tsc_t_registered_table_that_gained_a_consumer_fails()
    tsc_t_stale_register_entry_fails()
    print(f"\n{tsc__fails} FAILED" if tsc__fails else "\nok")
    return 1 if tsc__fails else 0


"""A version of this check that could not FAIL would restore the blindness
it was written to remove, so the fixture removes a tracked file.
"""

import importlib.util
import os
import subprocess
import sys
import tempfile

ttr_HERE = os.path.dirname(os.path.abspath(__file__))
ttr_ROOT = os.path.abspath(os.path.join(ttr_HERE, ".."))

ttr__spec = importlib.util.spec_from_file_location(
    "ctr", os.path.join(ttr_HERE, "check-testhygiene.py"))
ttr_ctr = importlib.util.module_from_spec(ttr__spec)
ttr__spec.loader.exec_module(ttr_ctr)

ttr_FAILED: list = []
ttr_PASSED = 0


def ttr_check(name, got, want):
    global ttr_PASSED
    if got == want:
        ttr_PASSED += 1
    else:
        ttr_FAILED.append(f"{name}: got {got!r}, want {want!r}")


def ttr__git(root, *a):
    return subprocess.run(["git", "-C", root, *a],
                          capture_output=True, text=True, check=False)


def ttr_test_clean_repo_has_nothing_to_report():
    with tempfile.TemporaryDirectory() as tmp:
        ttr__git(tmp, "init", "-q")
        p = os.path.join(tmp, "a.txt")
        open(p, "w").write("hello\n")
        ttr__git(tmp, "add", "a.txt")
        missing, unreadable = ttr_ctr.tr_scan(tmp)
        ttr_check("clean-missing", missing, [])
        ttr_check("clean-unreadable", unreadable, [])


def ttr_test_removed_tracked_file_is_named():
    """The defect this check exists for: a file in the index, gone from disk."""
    with tempfile.TemporaryDirectory() as tmp:
        ttr__git(tmp, "init", "-q")
        for n in ("a.txt", "b.txt"):
            open(os.path.join(tmp, n), "w").write("x\n")
        ttr__git(tmp, "add", "a.txt", "b.txt")
        os.remove(os.path.join(tmp, "b.txt"))
        missing, unreadable = ttr_ctr.tr_scan(tmp)
        ttr_check("removed-is-reported", missing, ["b.txt"])
        ttr_check("survivor-not-reported", "a.txt" in missing, False)


def ttr_test_unlistable_repo_raises():
    """A dead git must not read as an empty, therefore clean, tree."""
    with tempfile.TemporaryDirectory() as tmp:
        raised = False
        try:
            ttr_ctr.tr_scan(tmp)          # not a git repo -- ls-files exits 128
        except ttr_ctr.GitUnavailable:
            raised = True
        ttr_check("dead-git-raises", raised, True)


def ttr_main() -> int:
    ttr_test_clean_repo_has_nothing_to_report()
    ttr_test_removed_tracked_file_is_named()
    ttr_test_unlistable_repo_raises()
    print(f"[test_check-tracked-readable] {ttr_PASSED} passed, {len(ttr_FAILED)} failed")
    for f in ttr_FAILED:
        print(f"  FAIL {f}")
    return 1 if ttr_FAILED else 0


"""Unit tests for the agent-pipe module-size ratchet (check 149)."""

import importlib.util
import os
import shutil
import sys
import tempfile

tml__HERE = os.path.dirname(os.path.abspath(__file__))
tml__spec = importlib.util.spec_from_file_location(
    "check_module_length", os.path.join(tml__HERE, "check-testhygiene.py"))
tml_M = importlib.util.module_from_spec(tml__spec)
tml__spec.loader.exec_module(tml_M)

tml__fails = 0

def tml_check(name, cond):
    global tml__fails
    if cond:
        print(f"ok   - {name}")
    else:
        tml__fails += 1
        print(f"FAIL - {name}")

def tml_mkroot(files, oversize=(), max_lines=800):
    """files: {relpath under mios_pipe: line_count}. Returns the root path."""
    root = tempfile.mkdtemp(prefix="modlen-")
    os.makedirs(os.path.join(root, "usr/share/mios"), exist_ok=True)
    rows = "\n".join(
        '    { path = "%s", lines = %d },' % (p, n) for p, n in oversize)
    with open(os.path.join(root, "usr/share/mios/mios.toml"), "w") as fh:
        fh.write("[refactor]\nmax_lines = %d\nsubmodule_roots = [\"mios_pipe/\"]\n"
                 "oversize = [\n%s\n]\n" % (max_lines, rows))
    for rel, n in files.items():
        full = os.path.join(root, tml_M.ml_PKG, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as fh:
            fh.write("\n".join(str(i) for i in range(n)) + "\n")
    return root

def tml_run(root):
    bad, checked = tml_M.ml_scan(root)
    return bad, checked

def tml_t_small_file_passes():
    root = tml_mkroot({"mios_pipe/routing/small.py": 100})
    bad, checked = tml_run(root)
    tml_check("small file passes", bad == [])
    tml_check("small file was actually checked", checked == 1)
    shutil.rmtree(root)

def tml_t_new_oversize_fails():
    root = tml_mkroot({"mios_pipe/routing/big.py": 801})
    bad, _ = tml_run(root)
    tml_check("new file over the limit fails", len(bad) == 1)
    tml_check("message says split, not grandfather",
          bad and "do NOT add it to [refactor].oversize" in bad[0])
    shutil.rmtree(root)

def tml_t_nested_is_seen():
    # The bash predecessor used find -maxdepth 1 and could not see this.
    root = tml_mkroot({"mios_pipe/routing/deep/deeper/big.py": 900})
    bad, checked = tml_run(root)
    tml_check("a file two directories deep is scanned", checked == 1)
    tml_check("a nested file over the limit fails", len(bad) == 1)
    shutil.rmtree(root)

def tml_t_init_and_nonpy_skipped():
    root = tml_mkroot({"mios_pipe/__init__.py": 900,
                   "mios_pipe/routing/__init__.py": 900,
                   "mios_pipe/routing/notes.txt": 900})
    bad, checked = tml_run(root)
    tml_check("__init__.py and non-.py files are skipped", checked == 0)
    tml_check("skipped files raise nothing", bad == [])
    shutil.rmtree(root)

def tml_t_root_level_module_is_main():
    """Operator ruling: the ceiling governs sub-modules only. The agent-pipe
    ROOT (server.py, the mios_*.py components) holds main modules, where
    features fold in, so a long root module is not a violation -- while the
    same length inside a sub-module package still is."""
    root = tml_mkroot({"root_big.py": 900, "mios_pipe/routing/big.py": 900})
    bad, checked = tml_run(root)
    tml_check("a ROOT-level main module carries no ceiling",
          not any("root_big.py" in b for b in bad))
    tml_check("the same length in a sub-module still fails",
          checked == 1 and any("big.py" in b for b in bad))
    shutil.rmtree(root)

def tml_t_submodule_roots_required():
    root = tempfile.mkdtemp(prefix="modlen-")
    os.makedirs(os.path.join(root, "usr/share/mios"), exist_ok=True)
    with open(os.path.join(root, "usr/share/mios/mios.toml"), "w") as fh:
        fh.write("[refactor]\nmax_lines = 800\noversize = []\n")
    try:
        tml_run(root)
        failed = False
    except SystemExit:
        failed = True
    tml_check("a register without submodule_roots fails closed", failed)
    shutil.rmtree(root)

def tml_t_shim_is_skipped():
    """A lazy re-export shim is ~28 lines of boilerplate, not a module."""
    root = tml_mkroot({"shim_mod.py": 5})
    full = os.path.join(root, tml_M.ml_PKG, "shim_mod.py")
    with open(full, "w") as fh:
        fh.write("# AI-hint: Re-export shim for mios_pipe.routing.thing\n")
        fh.write("\n".join(str(i) for i in range(900)) + "\n")
    bad, checked = tml_run(root)
    tml_check("a re-export shim is excluded from sizing", bad == [])
    shutil.rmtree(root)

def tml_t_grandfathered_at_recorded_passes():
    root = tml_mkroot({"mios_pipe/routing/legacy.py": 1200},
                  oversize=[("mios_pipe/routing/legacy.py", 1200)])
    bad, _ = tml_run(root)
    tml_check("grandfathered file at its recorded length passes", bad == [])
    shutil.rmtree(root)

def tml_t_grandfathered_growth_fails():
    root = tml_mkroot({"mios_pipe/routing/legacy.py": 1201},
                  oversize=[("mios_pipe/routing/legacy.py", 1200)])
    bad, _ = tml_run(root)
    tml_check("a grandfathered file that GREW fails", len(bad) == 1)
    tml_check("message names the ratchet direction",
          bad and "ratchets DOWN" in bad[0])
    shutil.rmtree(root)

def tml_t_grandfathered_shrink_fails():
    root = tml_mkroot({"mios_pipe/routing/legacy.py": 900},
                  oversize=[("mios_pipe/routing/legacy.py", 1200)])
    bad, _ = tml_run(root)
    tml_check("a grandfathered file that SHRANK fails (lock the win in)",
          len(bad) == 1 and "lower its" in bad[0])
    shutil.rmtree(root)

def tml_t_stale_register_entry_fails():
    root = tml_mkroot({"mios_pipe/routing/small.py": 10},
                  oversize=[("mios_pipe/routing/gone.py", 1200)])
    bad, _ = tml_run(root)
    tml_check("a register entry for a deleted file fails",
          len(bad) == 1 and "no longer exists" in bad[0])
    shutil.rmtree(root)

def tml_t_absent_tree_is_noop():
    root = tempfile.mkdtemp(prefix="modlen-")
    os.makedirs(os.path.join(root, "usr/share/mios"), exist_ok=True)
    with open(os.path.join(root, "usr/share/mios/mios.toml"), "w") as fh:
        fh.write("[refactor]\nmax_lines = 800\nsubmodule_roots = [\"mios_pipe/\"]\noversize = []\n")
    bad, checked = tml_run(root)
    tml_check("absent package tree is a clean no-op", bad == [] and checked == 0)
    shutil.rmtree(root)

def tml_main():
    tml_t_small_file_passes()
    tml_t_new_oversize_fails()
    tml_t_nested_is_seen()
    tml_t_init_and_nonpy_skipped()
    tml_t_root_level_module_is_main()
    tml_t_submodule_roots_required()
    tml_t_shim_is_skipped()
    tml_t_grandfathered_at_recorded_passes()
    tml_t_grandfathered_growth_fails()
    tml_t_grandfathered_shrink_fails()
    tml_t_stale_register_entry_fails()
    tml_t_absent_tree_is_noop()
    print(f"\n{tml__fails} FAILED" if tml__fails else "\nok")
    return 1 if tml__fails else 0

def main():
    rc = 0 if unittest.main(argv=[sys.argv[0]], exit=False).result.wasSuccessful() else 1
    for fn in (ttr_main, tml_main):
        rc |= (fn() or 0)
    return rc


if __name__ == "__main__":
    sys.exit(main())
