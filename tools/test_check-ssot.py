#!/usr/bin/env python3
# AI-hint: Sibling unit tests for tools/check-ssot.py -- one suite per subcommand. Class names are prefixed because five TestCase names collide across the merged sources.
"""Sibling tests for the consolidated SSOT-plane gates."""
import sys
import unittest


import os
import shutil
import subprocess
import sys
import tempfile

tmti__HERE = os.path.dirname(os.path.abspath(__file__))
tmti__fails = 0

def tmti_check(name, cond, detail=""):
    global tmti__fails
    if cond:
        print(f"ok   - {name}")
    else:
        tmti__fails += 1
        print(f"FAIL - {name}" + (f" -- {detail}" if detail else ""))

def tmti_run_tool(root):
    p = subprocess.run(
        [sys.executable, os.path.join(tmti__HERE, "check-ssot.py"), "toml-integrity"],
        env={**os.environ, "MIOS_DRIFT_ROOT": root},
        capture_output=True,
        text=True,
    )
    return p.returncode, p.stdout + p.stderr

def tmti_main():
    root = tempfile.mkdtemp(prefix="mios-toml-test-")
    try:
        target_dir = os.path.join(root, "usr/share/mios")
        os.makedirs(target_dir, exist_ok=True)
        toml_path = os.path.join(target_dir, "mios.toml")

        # Copy real mios.toml to temp repo
        real_toml = os.path.join(tmti__HERE, "../usr/share/mios/mios.toml")
        shutil.copy(real_toml, toml_path)

        rc, out = tmti_run_tool(root)
        tmti_check("valid mios.toml passes", rc == 0, f"rc={rc} out={out}")

        # Test truncation failure
        with open(toml_path, "w", encoding="utf-8") as f:
            f.write("[versions]\nmios_version = \"0.3.0\"\n")

        rc, out = tmti_run_tool(root)
        tmti_check("truncated mios.toml fails", rc != 0, f"rc={rc} out={out}")

    finally:
        shutil.rmtree(root, ignore_errors=True)

    if tmti__fails > 0:
        return 1


"""Tests for the SSOT<->consumer key-contract gate."""

import os
import shutil
import tempfile
import unittest
from importlib.machinery import SourceFileLoader

tsck__HERE = os.path.dirname(os.path.abspath(__file__))
tsck__ROOT = os.path.dirname(tsck__HERE)
tsck_mod = SourceFileLoader(
    "check_ssot_consumer_keys",
    os.path.join(tsck__HERE, "check-ssot.py")).load_module()

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

def tsck_tree(files: dict) -> str:
    """A throwaway root holding usr/<path> = <body> for each entry."""
    root = tempfile.mkdtemp()
    for rel, body in files.items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(body)
    return root

def tsck_data(unresolved=(), max_unresolved=None, ssot=None, table=True):
    d = {"security": {"api_require_auth": False}, "offline": {"memory_provider": "x"}}
    if ssot:
        d.update(ssot)
    if table:
        t = {"unresolved": list(unresolved)}
        if max_unresolved is not None:
            t["max_unresolved"] = max_unresolved
        d["ssot_consumers"] = t
    return d

def tsck_only(viols, needle):
    return [v for v in viols if needle in v]

class tsck_TestConsumerReads(unittest.TestCase):
    def setUp(self):
        self.roots = []

    def tearDown(self):
        for r in self.roots:
            shutil.rmtree(r, ignore_errors=True)

    def make(self, files):
        r = tsck_tree(files)
        self.roots.append(r)
        return r

    def test_both_call_spellings_are_matched(self):
        root = self.make({"usr/a.py":
                          '_toml_section("security").get("api_require_auth", False)\n'
                          'x = (_toml_section("offline") or {}).get("memory_provider")\n'})
        self.assertEqual(
            set(tsck_mod.sck_consumer_reads(root)),
            {("security", "api_require_auth"), ("offline", "memory_provider")})

    def test_tests_are_not_scanned(self):
        # A test may legitimately read a key it stubs itself.
        root = self.make({"usr/test_a.py": '_toml_section("nope").get("nope")\n'})
        self.assertEqual(tsck_mod.sck_consumer_reads(root), {})

    def test_pycache_is_not_scanned(self):
        root = self.make({"usr/__pycache__/a.py": '_toml_section("nope").get("nope")\n'})
        self.assertEqual(tsck_mod.sck_consumer_reads(root), {})

    def test_a_resolving_read_is_silent(self):
        root = self.make({"usr/a.py": '_toml_section("security").get("api_require_auth")\n'})
        self.assertEqual(tsck_mod.sck_violations(tsck_data((), 0), root), [])

    def test_a_misplaced_key_names_both_paths(self):
        root = self.make({"usr/a.py": '_toml_section("pgvector").get("memory_provider")\n'})
        v = tsck_mod.sck_violations(tsck_data((), 0, {"pgvector": {}}), root)
        hit = tsck_only(v, "pgvector.memory_provider")
        self.assertTrue(hit, v)
        self.assertIn("offline.memory_provider", hit[0])

    def test_an_undeclared_key_says_so(self):
        root = self.make({"usr/a.py": '_toml_section("ai").get("permission_tiers")\n'})
        v = tsck_mod.sck_violations(tsck_data((), 0, {"ai": {}}), root)
        self.assertTrue(tsck_only(v, "declared NOWHERE"), v)

    def test_registering_it_silences_it(self):
        root = self.make({"usr/a.py": '_toml_section("ai").get("permission_tiers")\n'})
        self.assertEqual(
            tsck_mod.sck_violations(tsck_data(("ai.permission_tiers",), 1, {"ai": {}}), root), [])

    def test_an_entry_that_resolves_again_must_leave(self):
        root = self.make({"usr/a.py": '_toml_section("security").get("api_require_auth")\n'})
        v = tsck_mod.sck_violations(tsck_data(("security.api_require_auth",), 1), root)
        self.assertTrue(tsck_only(v, "resolves now"), v)

    def test_an_entry_nothing_reads_must_leave(self):
        root = self.make({"usr/a.py": '_toml_section("security").get("api_require_auth")\n'})
        v = tsck_mod.sck_violations(tsck_data(("ghost.key",), 1), root)
        self.assertTrue(tsck_only(v, "no shipped consumer reads"), v)

    def test_unsorted_register(self):
        root = self.make({"usr/a.py":
                          '_toml_section("ai").get("b")\n_toml_section("ai").get("a")\n'})
        v = tsck_mod.sck_violations(tsck_data(("ai.b", "ai.a"), 2, {"ai": {}}), root)
        self.assertTrue(tsck_only(v, "not sorted"), v)

    def test_duplicate_register_entry(self):
        root = self.make({"usr/a.py": '_toml_section("ai").get("a")\n'})
        v = tsck_mod.sck_violations(tsck_data(("ai.a", "ai.a"), 2, {"ai": {}}), root)
        self.assertTrue(tsck_only(v, "twice"), v)

    def test_ceiling_absent_over_and_left_high(self):
        root = self.make({"usr/a.py":
                          '_toml_section("ai").get("a")\n_toml_section("ai").get("b")\n'})
        both = ("ai.a", "ai.b")
        self.assertTrue(tsck_only(tsck_mod.sck_violations(tsck_data(both, None, {"ai": {}}), root),
                             "max_unresolved is unset"))
        self.assertTrue(tsck_only(tsck_mod.sck_violations(tsck_data(both, 1, {"ai": {}}), root),
                             "over the ratchet ceiling"))
        self.assertTrue(tsck_only(tsck_mod.sck_violations(tsck_data(both, 9, {"ai": {}}), root),
                             "lower it to 2"))

    def test_absent_table(self):
        root = self.make({"usr/a.py": '_toml_section("ai").get("a")\n'})
        v = tsck_mod.sck_violations(tsck_data(table=False), root)
        self.assertTrue(tsck_only(v, "[ssot_consumers] is absent"), v)

    def test_no_reads_at_all_fails_rather_than_passing_vacuously(self):
        root = self.make({"usr/a.py": "print('nothing to see')\n"})
        v = tsck_mod.sck_violations(tsck_data((), 0), root)
        self.assertTrue(tsck_only(v, "vacuously"), v)

class tsck_TestRealTree(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(tsck__ROOT, "usr/share/mios/mios.toml"), "rb") as fh:
            self.real = tomllib.load(fh)

    def test_the_shipped_register_is_clean(self):
        self.assertEqual(tsck_mod.sck_violations(self.real, tsck__ROOT), [])

    def test_the_ceiling_equals_the_register(self):
        self.assertEqual(tsck_mod.sck_max_unresolved(self.real),
                         len(tsck_mod.sck_register(self.real)))

    def test_the_nine_security_controls_resolve(self):
        # T-325: these sat under an unclosed [security.nohc_allowlist] header, so
        # every one of them silently took its compiled default.
        for key in ("api_require_auth", "api_caller_keys_path", "principal_bind_mode",
                    "rule_of_two_mode", "quarantine_mode", "firewall_high_privilege_verbs",
                    "taint_verbs", "text_view_taint_prefixes", "internal_tld_suffixes",
                    "allowlist_hosts", "provenance_taint"):
            self.assertIn(key, self.real["security"], key)

    def test_the_allowlist_header_holds_only_its_own_lists(self):
        self.assertEqual(set(self.real["security"]["nohc_allowlist"]),
                         {"exempt_files", "exempt_patterns"})

    def test_the_register_does_not_cover_every_read(self):
        # If it did, the gate would assert nothing.
        self.assertLess(len(tsck_mod.sck_register(self.real)),
                        len(tsck_mod.sck_consumer_reads(tsck__ROOT)))


"""Tests for the [units] projection debt-register gate."""

import os
import unittest
from importlib.machinery import SourceFileLoader

tup__HERE = os.path.dirname(os.path.abspath(__file__))
tup__ROOT = os.path.dirname(tup__HERE)
tup_mod = SourceFileLoader(
    "check_unit_projection", os.path.join(tup__HERE, "check-ssot.py")).load_module()

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

# Two real units, so `shipped()` finds them without a fixture tree.
tup_A = "mios-agent-pipe.service"
tup_B = "mios-daemon.service"

def tup_data(drift, max_drift=None, units=(tup_A, tup_B), aliases=None, table=True):
    u = {name: {"Unit": {"Description": "x"}} for name in units}
    for k, v in (aliases or {}).items():
        u[k] = v
    d = {"units": u}
    if table:
        proj = {"drift": list(drift)}
        if max_drift is not None:
            proj["max_drift"] = max_drift
        d["unit_projection"] = proj
    return d

def tup_only(viols, needle):
    return [v for v in viols if needle in v]

class tup_TestHygiene(unittest.TestCase):
    def test_a_clean_register_is_silent(self):
        self.assertEqual(tup_mod.up_hygiene(tup_data([tup_A], 1), tup__ROOT), [])

    def test_an_empty_register_is_the_goal_not_an_error(self):
        self.assertEqual(tup_mod.up_hygiene(tup_data([], 0), tup__ROOT), [])

    def test_entry_not_projected_by_units(self):
        v = tup_mod.up_hygiene(tup_data(["mios-nope.service"], 1), tup__ROOT)
        self.assertTrue(tup_only(v, "which [units.*] does"), v)

    def test_entry_the_tree_does_not_ship(self):
        # Projected, so it passes the first check -- but there is no such file.
        v = tup_mod.up_hygiene(tup_data(["ghost.service"], 1, units=(tup_A, "ghost.service")), tup__ROOT)
        self.assertTrue(tup_only(v, "which the tree does"), v)

    def test_duplicate_entry(self):
        v = tup_mod.up_hygiene(tup_data([tup_A, tup_A], 2), tup__ROOT)
        self.assertTrue(tup_only(v, "lists a unit twice"), v)

    def test_unsorted_register(self):
        v = tup_mod.up_hygiene(tup_data([tup_B, tup_A], 2), tup__ROOT)
        self.assertTrue(tup_only(v, "not sorted"), v)

    def test_absent_table(self):
        v = tup_mod.up_hygiene(tup_data([], table=False), tup__ROOT)
        self.assertTrue(tup_only(v, "[unit_projection] is absent"), v)

    def test_absent_drift_key(self):
        d = tup_data([], 0)
        del d["unit_projection"]["drift"]
        v = tup_mod.up_hygiene(d, tup__ROOT)
        self.assertTrue(tup_only(v, "declares no `drift` key"), v)

    def test_absent_ceiling(self):
        v = tup_mod.up_hygiene(tup_data([tup_A]), tup__ROOT)
        self.assertTrue(tup_only(v, "max_drift is unset"), v)

    def test_register_over_the_ceiling(self):
        v = tup_mod.up_hygiene(tup_data([tup_A, tup_B], 1), tup__ROOT)
        self.assertTrue(tup_only(v, "over the ratchet ceiling"), v)

    def test_ceiling_left_high_after_the_debt_shrank(self):
        # The ground gained must be HELD. A ceiling that stays above the real
        # count is room for the next unit to drift into unnoticed.
        v = tup_mod.up_hygiene(tup_data([tup_A], 9), tup__ROOT)
        self.assertTrue(tup_only(v, "lower the ceiling"), v)

    def test_empty_units_table_fails_rather_than_passing_vacuously(self):
        v = tup_mod.up_hygiene({"units": {}, "unit_projection": {"drift": [], "max_drift": 0}},
                        tup__ROOT)
        self.assertTrue(tup_only(v, "vacuously"), v)

class tup_TestAliasHalf(unittest.TestCase):
    """[units] carries both `[units."x.service".Unit]` projections and bare
    `name = "unit.service"` aliases. Counting the aliases as projected units
    overstated the projection by 16 and would let one be 'registered'."""

    def test_string_values_are_not_projected_units(self):
        d = tup_data([], 0, aliases={"agent_pipe": tup_A})
        self.assertNotIn("agent_pipe", tup_mod.up_declared_units(d))
        self.assertIn("agent_pipe", tup_mod.up_unit_aliases(d))

    def test_an_alias_cannot_be_registered_as_drift(self):
        v = tup_mod.up_hygiene(tup_data(["agent_pipe"], 1, aliases={"agent_pipe": tup_A}), tup__ROOT)
        self.assertTrue(tup_only(v, "which [units.*] does"), v)

class tup_TestRealTree(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(tup__ROOT, "usr/share/mios/mios.toml"), "rb") as fh:
            self.real = tomllib.load(fh)

    def test_the_shipped_register_is_clean(self):
        self.assertEqual(tup_mod.up_hygiene(self.real, tup__ROOT), [])

    def test_the_ceiling_equals_the_register(self):
        self.assertEqual(tup_mod.up_max_drift(self.real), len(tup_mod.up_register(self.real)))

    def test_every_registered_unit_is_projected_and_shipped(self):
        units, on_disk = tup_mod.up_declared_units(self.real), tup_mod.up_shipped(tup__ROOT)
        for name in tup_mod.up_register(self.real):
            self.assertIn(name, units, name)
            self.assertIn(name, on_disk, name)

    def test_the_register_does_not_cover_the_whole_projection(self):
        # If every projected unit were registered the gate would assert nothing.
        self.assertLess(len(tup_mod.up_register(self.real)),
                        len(tup_mod.up_declared_units(self.real)))


"""Tests for the port-literal gate."""

import os
import tempfile
import unittest
from importlib.machinery import SourceFileLoader

tpf__HERE = os.path.dirname(os.path.abspath(__file__))
tpf__ROOT = os.path.dirname(tpf__HERE)
tpf_mod = SourceFileLoader(
    "check_port_fallbacks", os.path.join(tpf__HERE, "check-ssot.py")).load_module()

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

tpf_DATA = {"ports": {"agent_pipe": 8700, "llm_light": 8500, "pgvector": 8600,
                  "arbiter": 8760}}

def tpf_tree(tmp, files, register=None):
    for rel, body in files.items():
        p = os.path.join(tmp, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(body)
    d = {"ports": dict(tpf_DATA["ports"])}
    if register is not None:
        d["ports"]["stale_fallbacks"] = list(register)
    return d

class tpf_TestIdioms(unittest.TestCase):
    def _one(self, body, rel="usr/libexec/mios/probe"):
        with tempfile.TemporaryDirectory() as tmp:
            d = tpf_tree(tmp, {rel: body})
            return tpf_mod.pf_findings(d, tmp)

    def test_an_unconditional_environment_pin_is_found(self):
        # The shape that made agent-pipe bind a retired port.
        f = self._one("Environment=MIOS_PORTS_AGENT_PIPE=8640\n",
                      "usr/lib/systemd/system/x.service")
        self.assertIn("usr/lib/systemd/system/x.service:AGENT_PIPE", f)

    def test_a_shell_fallback_is_found(self):
        # 8450 is DELIBERATELY wrong -- [ports].llm_light is 8500. A fixture
        # carrying the CORRECT value produces no finding, so the assertion
        # would pass over nothing.
        self.assertTrue(self._one('P="${MIOS_PORTS_LLM_LIGHT:-8450}"\n'))

    def test_a_python_get_default_is_found(self):
        self.assertTrue(self._one('p = os.environ.get("MIOS_PORTS_LLM_LIGHT", "8450")\n'))

    def test_the_second_literal_of_a_double_fallback_is_found(self):
        # get(K, "correct") or WRONG -- the `or` is what runs when the var is
        # empty, and the first sweep of this gate missed it entirely.
        f = self._one('p = int(e.get("MIOS_PORTS_PGVECTOR", "8600") or 8432)\n')
        self.assertIn("usr/libexec/mios/probe:PGVECTOR", f)

    def test_a_bare_or_fallback_is_found(self):
        self.assertTrue(self._one('p = os.environ.get("MIOS_PORTS_LLM_LIGHT") or "8450"\n'))

    def test_the_powershell_table_shape_is_found(self):
        self.assertTrue(self._one("_MiosPort 'MIOS_PORTS_LLM_LIGHT' 8450\n"))

    def test_the_alias_spelling_is_found(self):
        self.assertTrue(self._one('p = os.environ.get("MIOS_PORTS_ARBITER", "8650")\n'))

    def test_an_agreeing_literal_is_not_a_finding(self):
        self.assertEqual(self._one('p = os.environ.get("MIOS_PORTS_LLM_LIGHT", "8500")\n'), {})

    def test_a_templated_reference_is_not_a_finding(self):
        self.assertEqual(self._one('P="${MIOS_PORTS_LLM_LIGHT}"\n'), {})

    def test_a_comment_is_never_a_finding(self):
        self.assertEqual(self._one('# MIOS_PORTS_LLM_LIGHT used to be 8450\n'), {})

    def test_a_name_with_no_ports_key_is_ignored(self):
        self.assertEqual(self._one('p = os.environ.get("MIOS_PG_PORT", "5432")\n'), {})

class tpf_TestRegister(unittest.TestCase):
    def test_a_registered_finding_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = tpf_tree(tmp, {"usr/libexec/mios/probe": 'x = "${MIOS_PORTS_LLM_LIGHT:-8450}"\n'},
                     register=["usr/libexec/mios/probe:LLM_LIGHT"])
            self.assertEqual(tpf_mod.pf_classify(d, tmp), [])

    def test_an_unregistered_finding_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = tpf_tree(tmp, {"usr/libexec/mios/probe": 'x = "${MIOS_PORTS_LLM_LIGHT:-8450}"\n'},
                     register=[])
            self.assertTrue(tpf_mod.pf_classify(d, tmp))

    def test_the_register_only_shrinks(self):
        # An entry that no longer reproduces must be REMOVED, not left to rot.
        with tempfile.TemporaryDirectory() as tmp:
            d = tpf_tree(tmp, {"usr/libexec/mios/probe": "clean\n"},
                     register=["usr/libexec/mios/probe:LLM_LIGHT"])
            out = tpf_mod.pf_classify(d, tmp)
            self.assertTrue(any("only shrinks" in v for v in out))

    def test_a_duplicated_register_entry_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = tpf_tree(tmp, {"usr/libexec/mios/probe": 'x = "${MIOS_PORTS_LLM_LIGHT:-8450}"\n'},
                     register=["usr/libexec/mios/probe:LLM_LIGHT"] * 2)
            self.assertTrue(any("twice" in v for v in tpf_mod.pf_classify(d, tmp)))

class tpf_TestRealTree(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(tpf__ROOT, tpf_mod.pf_TOML), "rb") as fh:
            self.real = tomllib.load(fh)

    def test_the_shipped_tree_is_clean(self):
        self.assertEqual(tpf_mod.pf_classify(self.real, tpf__ROOT), [])

    def test_the_gate_actually_scans_something(self):
        # A gate that walks an empty set reports success over nothing.
        self.assertGreater(sum(1 for _ in tpf_mod.pf_scan_paths(tpf__ROOT)), 200)

    def test_the_register_is_drained_and_stays_drained(self):
        self.assertEqual(tpf_mod.pf_register(self.real), [])


"""Tests for the allocated-but-unbound port gate."""

import os
import unittest
from importlib.machinery import SourceFileLoader

tpb__HERE = os.path.dirname(os.path.abspath(__file__))
tpb__ROOT = os.path.dirname(tpb__HERE)
tpb_mod = SourceFileLoader(
    "check_ports_bound", os.path.join(tpb__HERE, "check-ssot.py")).load_module()

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

def tpb_data(ports=None, unbound=None):
    d = {"ports": dict(ports or {})}
    if unbound is not None:
        d["ports"]["unbound"] = list(unbound)
    return d

class tpb_TestPortKeys(unittest.TestCase):
    def test_stack_id_is_not_a_port(self):
        self.assertEqual(tpb_mod.pb_port_keys(tpb_data({"a": 1, "stack_id": 0})), {"a"})

    def test_the_register_itself_is_not_a_port(self):
        self.assertEqual(tpb_mod.pb_port_keys(tpb_data({"a": 1}, ["a"])), {"a"})

class tpb_TestClassify(unittest.TestCase):
    def test_referenced_port_is_clean(self):
        self.assertEqual(tpb_mod.pb_classify(tpb_data({"a": 1}), {"a"}), [])

    def test_registered_and_unreferenced_is_clean(self):
        self.assertEqual(tpb_mod.pb_classify(tpb_data({"a": 1}, ["a"]), set()), [])

    def test_unreferenced_and_unregistered_fails(self):
        v = tpb_mod.pb_classify(tpb_data({"a": 1}), set())
        self.assertEqual(len(v), 1)
        self.assertIn("guards a number nothing binds", v[0])

    def test_register_only_shrinks(self):
        # Wired since it was registered -> the entry must be removed.
        v = tpb_mod.pb_classify(tpb_data({"a": 1}, ["a"]), {"a"})
        self.assertIn("only shrinks", v[0])

    def test_register_naming_a_missing_port_fails(self):
        v = tpb_mod.pb_classify(tpb_data({"a": 1}, ["ghost"]), {"a"})
        self.assertTrue(any("not a [ports] key" in x for x in v))

    def test_duplicate_register_entry_fails(self):
        v = tpb_mod.pb_classify(tpb_data({"a": 1, "b": 2}, ["b", "b"]), {"a"})
        self.assertTrue(any("twice" in x for x in v))

    def test_empty_port_table_fails_rather_than_passing_vacuously(self):
        self.assertIn("vacuously", tpb_mod.pb_classify(tpb_data({}, []), set())[0])

    def test_whitespace_entries_are_ignored(self):
        self.assertEqual(tpb_mod.pb_classify(tpb_data({"a": 1, "b": 2}, [" b ", ""]), {"a"}), [])

class tpb_TestSkipSurfaces(unittest.TestCase):
    def test_ssot_and_docs_cannot_prove_a_binding(self):
        # A port mentioned only where ports are DESCRIBED is still unbound.
        for p in ("usr/share/mios/mios.toml", "usr/share/doc/mios/x.md",
                  "automation/lib/globals.sh", "tasks.jsonl", "ADR.md"):
            self.assertTrue(p.startswith(tpb_mod.pb_SKIP_PREFIXES), p)

    def test_a_quadlet_is_not_skipped(self):
        for p in ("usr/share/containers/systemd/mios-guacd.container",
                  "usr/lib/systemd/system/mios-agent-pipe.service",
                  "usr/lib/mios/agent-pipe/server.py"):
            self.assertFalse(p.startswith(tpb_mod.pb_SKIP_PREFIXES), p)

class tpb_TestShippedTree(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(tpb__ROOT, tpb_mod.pb_TOML), "rb") as fh:
            self.real = tomllib.load(fh)

    def test_the_real_ssot_accounts_for_every_port(self):
        ref = tpb_mod.pb_referenced_ports(tpb__ROOT, tpb_mod.pb_port_keys(self.real))
        self.assertEqual(tpb_mod.pb_classify(self.real, ref), [])

    def test_every_register_entry_is_a_real_port(self):
        self.assertTrue(set(tpb_mod.pb_register(self.real)) <= tpb_mod.pb_port_keys(self.real))

    def test_the_ports_that_were_wired_are_really_referenced(self):
        # The four T-318 drains: if any regresses, this fails before the gate does.
        ref = tpb_mod.pb_referenced_ports(tpb__ROOT, tpb_mod.pb_port_keys(self.real))
        for k in ("guacd", "redis", "pxe_hub_api", "forge_ssh"):
            self.assertIn(k, ref, k)


import importlib.util
import os
import unittest

tvr__HERE = os.path.dirname(os.path.abspath(__file__))
tvr__ROOT = os.path.dirname(tvr__HERE)

def tvr__load():
    spec = importlib.util.spec_from_file_location(
        "check_variant_registry", os.path.join(tvr__HERE, "check-ssot.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

tvr_MOD = tvr__load()

def tvr__ssot():
    import tomllib
    with open(os.path.join(tvr__ROOT, "usr/share/mios/mios.toml"), "rb") as fh:
        return tomllib.load(fh)

class tvr_TestVariantRegistry(unittest.TestCase):
    def setUp(self):
        self.v = tvr__ssot()["variants"]
        self.entries = self.v["entries"]

    def test_the_shipped_registry_passes(self):
        os.environ["MIOS_DRIFT_ROOT"] = tvr__ROOT
        self.assertEqual(0, tvr_MOD.vr_main())

    def test_every_variant_carries_every_required_field(self):
        for key, spec in self.entries.items():
            for field in tvr_MOD.vr_REQUIRED:
                self.assertIn(field, spec, "%s lacks %s" % (key, field))

    def test_status_is_from_the_measured_vocabulary(self):
        for key, spec in self.entries.items():
            self.assertIn(spec["status"], tvr_MOD.vr_STATUSES, key)

    def test_title_and_key_are_one_name_in_two_registers(self):
        base = self.v["naming"]["base"]
        for key, spec in self.entries.items():
            if key == base:
                self.assertEqual(self.v["naming"]["prefix"], spec["title"])
            else:
                self.assertEqual(key, spec["title"].lower(), key)

    def test_no_variant_is_named_for_its_size(self):
        """Mini described the image; Metal describes the job. See the suffix rule."""
        for key, spec in self.entries.items():
            for banned in ("mini", "small", "tiny", "lite", "big"):
                self.assertNotIn(banned, key.split("-")[-1].lower(),
                                 "%s names a size, not a job" % key)

    def test_every_edition_is_claimed_by_a_variant(self):
        claimed = {s.get("edition") for s in self.entries.values() if s.get("edition")}
        for ed in tvr__ssot()["editions"]:
            self.assertIn(ed, claimed, "[editions.%s] ships in no variant" % ed)

    def test_the_design_ceiling_equals_the_measurement(self):
        design = [k for k, s in self.entries.items() if s["status"] == "design"]
        self.assertEqual(len(design), self.v["max_design_variants"],
                         "the ceiling must sit at the measurement, not above it")

    def test_each_variant_has_a_page_in_the_manual(self):
        p = os.path.join(tvr__ROOT, "usr/share/man/man7/mios-variants.7")
        self.assertTrue(os.path.isfile(p), "mios-variants(7) is not rendered")
        body = open(p, encoding="utf-8").read()
        for spec in self.entries.values():
            self.assertIn(spec["title"].replace("-", chr(92) + "-"), body)


import importlib.util
import os
import re
import unittest

tdf__HERE = os.path.dirname(os.path.abspath(__file__))
tdf__ROOT = os.path.dirname(tdf__HERE)

def tdf__load():
    spec = importlib.util.spec_from_file_location(
        "check_deploy_formats", os.path.join(tdf__HERE, "check-ssot.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

tdf_MOD = tdf__load()

def tdf__ssot():
    import tomllib
    with open(os.path.join(tdf__ROOT, "usr/share/mios/mios.toml"), "rb") as fh:
        return tomllib.load(fh)

class tdf_TestDeployFormats(unittest.TestCase):
    def setUp(self):
        self.formats = {k: v for k, v in tdf__ssot()["deploy"]["formats"].items()
                        if isinstance(v, dict)}

    def test_the_shipped_matrix_passes(self):
        os.environ["MIOS_DRIFT_ROOT"] = tdf__ROOT
        self.assertEqual(0, tdf_MOD.df_main())

    def test_wsl_is_a_supported_format(self):
        """MiOS ships enabled WSL units, so WSL must be a declared format."""
        self.assertIn("wsl2", self.formats)
        self.assertIn("wslg", self.formats["wsl2"]["gui"].lower())

    def test_every_format_target_exists_in_the_justfile(self):
        just = open(os.path.join(tdf__ROOT, "Justfile"), encoding="utf-8").read()
        targets = set(re.findall(r"^([a-z0-9][a-z0-9_-]*):", just, re.M))
        for name, spec in self.formats.items():
            self.assertIn(spec["target"], targets, name)

    def test_every_recipe_file_is_claimed(self):
        claimed = {os.path.basename(s["recipe"]) for s in self.formats.values()
                   if s.get("recipe")}
        claimed.add(os.path.basename(tdf__ssot()["deploy"]["formats"]["shared_recipe"]))
        for fn in os.listdir(os.path.join(tdf__ROOT, "config/artifacts")):
            if fn.endswith(".toml"):
                self.assertIn(fn, claimed, "%s is claimed by no format" % fn)

    def test_every_variant_ships_declared_formats_only(self):
        for vname, vspec in tdf__ssot()["variants"]["entries"].items():
            for art in vspec.get("artifacts", []):
                self.assertIn(art, self.formats, "%s ships %s" % (vname, art))

    def test_the_media_span_metal_vm_removable_and_wsl(self):
        media = " ".join(s["medium"] for s in self.formats.values()).lower()
        for expected in ("disk", "virtual machine", "usb", "wsl"):
            self.assertIn(expected, media)


"""Tests for the blade role-SSOT gate."""

import os
import shutil
import tempfile
import unittest
from importlib.machinery import SourceFileLoader

trs__HERE = os.path.dirname(os.path.abspath(__file__))
trs__ROOT = os.path.dirname(trs__HERE)
trs__NOROOT = os.path.join(trs__HERE, "no-such-root")
trs_mod = SourceFileLoader(
    "check_role_ssot", os.path.join(trs__HERE, "check-ssot.py")).load_module()

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

def trs_data(btype="hybrid", archetypes=None, alias=None, fallback="headless"):
    if archetypes is None:
        archetypes = {"hybrid": ["x"], "endpoint": []}
    # `or` would swallow an intentionally EMPTY table -- the exact case one of
    # these tests exists to exercise.
    blade = {"type": btype, "fallback": fallback, "archetypes": dict(archetypes)}
    if alias is not None:
        blade["role_aliases"] = dict(alias)
    return {"blade": blade}

def trs_tree(tmp, units):
    """A fake root: {unit-filename: body}."""
    d = os.path.join(tmp, trs_mod.rs_UNIT_DIR)
    os.makedirs(d, exist_ok=True)
    for name, body in units.items():
        with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
            fh.write(body)
    return tmp

def trs_target(name, conflicts=()):
    return ("[Unit]\nDescription=x\nRequires=multi-user.target\n"
            "Conflicts=%s\nAllowIsolate=yes\n\n[Install]\n"
            "WantedBy=multi-user.target\n" % " ".join(conflicts))

class trs_TestType(unittest.TestCase):
    def test_a_legal_type_is_clean(self):
        self.assertEqual(trs_mod.rs_check_type(trs_data()), [])

    def test_an_empty_type_fails(self):
        self.assertTrue(trs_mod.rs_check_type(trs_data(btype="")))

    def test_a_type_naming_no_archetype_fails(self):
        # The exact shape [profile].role shipped in: "developer" was never one.
        out = trs_mod.rs_check_type(trs_data(btype="developer"))
        self.assertTrue(out)
        self.assertIn("developer", out[0])

    def test_an_empty_archetype_table_fails_rather_than_passing_vacuously(self):
        self.assertTrue(trs_mod.rs_check_type(trs_data(archetypes={})))

class trs_TestTargets(unittest.TestCase):
    def test_a_missing_target_fails(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            trs_tree(tmp, {"mios-hybrid.target": trs_target("hybrid")})
            out = trs_mod.rs_check_targets(trs_data(), tmp)
            self.assertTrue(any("mios-endpoint.target" in v for v in out))

    def test_an_archetype_name_that_is_not_a_unit_stem_fails(self):
        out = trs_mod.rs_check_targets(trs_data(archetypes={"Not Legal": []}), trs__NOROOT)
        self.assertTrue(any("legal unit-name stem" in v for v in out))

class trs_TestCapabilitiesConsumed(unittest.TestCase):
    def test_a_capability_nothing_requires_fails(self):
        d = trs_data(archetypes={"hybrid": ["x", "decorative"], "endpoint": []})
        d["blade"]["requires"] = {"a": ["x"]}
        out = trs_mod.rs_check_capabilities_consumed(d)
        self.assertTrue(any("decorative" in v for v in out))

    def test_a_fully_consumed_table_is_clean(self):
        d = trs_data(archetypes={"hybrid": ["x"], "endpoint": []})
        d["blade"]["requires"] = {"a": ["x"]}
        self.assertEqual(trs_mod.rs_check_capabilities_consumed(d), [])

    def test_the_seat_granting_nothing_is_not_a_violation(self):
        d = trs_data(archetypes={"endpoint": []})
        d["blade"]["requires"] = {}
        self.assertEqual(trs_mod.rs_check_capabilities_consumed(d), [])

class trs_TestAliases(unittest.TestCase):
    def test_an_alias_onto_an_archetype_is_clean(self):
        self.assertEqual(trs_mod.rs_check_aliases(trs_data(alias={"k3s": "hybrid"})), [])

    def test_an_alias_onto_nothing_fails(self):
        self.assertTrue(trs_mod.rs_check_aliases(trs_data(alias={"k3s": "nope"})))

    def test_an_alias_shadowing_an_archetype_fails(self):
        self.assertTrue(trs_mod.rs_check_aliases(trs_data(alias={"hybrid": "endpoint"})))

class trs_TestConflicts(unittest.TestCase):
    def test_a_complete_graph_is_clean(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            trs_tree(tmp, {
                "mios-hybrid.target": trs_target("hybrid", ["mios-endpoint.target"]),
                "mios-endpoint.target": trs_target("endpoint", ["mios-hybrid.target"]),
            })
            self.assertEqual(trs_mod.rs_check_conflicts(trs_data(), tmp), [])

    def test_a_role_conflicting_with_nothing_fails(self):
        # This is exactly what mios-hybrid.target -- the DEFAULT -- shipped as.
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            trs_tree(tmp, {
                "mios-hybrid.target": trs_target("hybrid"),
                "mios-endpoint.target": trs_target("endpoint", ["mios-hybrid.target"]),
            })
            out = trs_mod.rs_check_conflicts(trs_data(), tmp)
            self.assertTrue(any("mios-hybrid.target does not conflict" in v
                                for v in out))

    def test_a_conflict_with_a_non_role_target_fails(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            trs_tree(tmp, {
                "mios-hybrid.target": trs_target(
                    "hybrid", ["mios-endpoint.target", "mios-k3s-worker.target"]),
                "mios-endpoint.target": trs_target("endpoint", ["mios-hybrid.target"]),
            })
            out = trs_mod.rs_check_conflicts(trs_data(), tmp)
            self.assertTrue(any("not a role target" in v for v in out))

class trs_TestUnitAliases(unittest.TestCase):
    def test_a_suffix_matching_alias_is_clean(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            trs_tree(tmp, {"mios-hybrid.target":
                       "[Install]\nAlias=mios-default.target\n"})
            self.assertEqual(trs_mod.rs_check_aliases_in_units(tmp), [])

    def test_the_shipped_default_target_alias_fails(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            trs_tree(tmp, {"mios-hybrid.target":
                       "[Install]\nAlias=default.target.mios-hybrid\n"})
            out = trs_mod.rs_check_aliases_in_units(tmp)
            self.assertTrue(out)
            self.assertIn("same suffix", out[0])

class trs_TestProfileRetired(unittest.TestCase):
    def test_no_profile_section_is_clean(self):
        self.assertEqual(trs_mod.rs_check_profile_retired(trs_data(), trs__NOROOT), [])

    def test_a_resurrected_illegal_role_fails(self):
        d = trs_data()
        d["profile"] = {"role": "developer"}
        self.assertTrue(trs_mod.rs_check_profile_retired(d, trs__NOROOT))

    def test_the_capital_R_spelling_is_caught_too(self):
        # user-setup.sh emitted `Role`, which no reader spells that way.
        d = trs_data()
        d["profile"] = {"Role": "developer"}
        self.assertTrue(trs_mod.rs_check_profile_retired(d, trs__NOROOT))

    def test_a_legal_role_alias_is_permitted(self):
        d = trs_data()
        d["profile"] = {"role": "hybrid"}
        self.assertEqual(trs_mod.rs_check_profile_retired(d, trs__NOROOT), [])

    def test_features_may_not_come_back(self):
        d = trs_data()
        d["profile"] = {"features": ["ai"]}
        self.assertTrue(trs_mod.rs_check_profile_retired(d, trs__NOROOT))

    def test_a_keep_list_naming_the_retired_vars_fails(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, trs_mod.rs_KEEP_LISTS[0])
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write('WALK_EMIT_KEEP = {"MIOS_PROFILE_ROLE"}\n')
            out = trs_mod.rs_check_profile_retired(trs_data(), tmp)
            self.assertTrue(any("MIOS_PROFILE_ROLE" in v for v in out))

class trs_TestNoHardcodedRoles(unittest.TestCase):
    def test_a_literal_archetype_in_blade_code_fails(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, trs_mod.rs_BLADE_CODE[0])
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write('case "$ROLE" in\n  endpoint) TARGET=x ;;\nesac\n')
            out = trs_mod.rs_check_no_hardcoded_roles(trs_data(), tmp)
            self.assertTrue(any("endpoint" in v for v in out))

    def test_a_heredoc_body_is_not_shell_control_flow(self):
        # The embedded python that READS [blade.archetypes] necessarily names
        # TOML keys, and `endpoint` is both an archetype and an ordinary config
        # key -- flagging it would punish the SSOT read this rule requires.
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, trs_mod.rs_BLADE_CODE[0])
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("_q() {\n    python3 - <<'PY'\n"
                         "print((d.get('ai') or {}).get('endpoint'))\n"
                         "PY\n}\n")
            self.assertEqual(trs_mod.rs_check_no_hardcoded_roles(trs_data(), tmp), [])

    def test_a_case_arm_AFTER_a_heredoc_is_still_caught(self):
        # ...and closing the heredoc must resume checking, or the exclusion
        # becomes a way to hide anything.
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, trs_mod.rs_BLADE_CODE[0])
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("_q() {\n    python3 - <<'PY'\nprint('x')\nPY\n}\n"
                         'case "$ROLE" in\n  endpoint) T=x ;;\nesac\n')
            out = trs_mod.rs_check_no_hardcoded_roles(trs_data(), tmp)
            self.assertTrue(any("endpoint" in v for v in out), out)

    def test_a_mention_in_a_comment_is_not_a_hardcode(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, trs_mod.rs_BLADE_CODE[0])
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("# an endpoint blade is a seat\ntrue\n")
            self.assertEqual(trs_mod.rs_check_no_hardcoded_roles(trs_data(), tmp), [])

class trs_TestRealTree(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(trs__ROOT, trs_mod.rs_TOML), "rb") as fh:
            self.real = tomllib.load(fh)

    def test_the_shipped_tree_passes_every_rule(self):
        self.assertEqual(trs_mod.rs_collect(self.real, trs__ROOT), [])

    def test_the_seat_is_declared_and_grants_nothing(self):
        arche = trs_mod.rs_archetypes(self.real)
        seats = [n for n, caps in arche.items() if not caps]
        self.assertEqual(seats, ["endpoint"])

    def test_the_fallback_is_itself_an_archetype(self):
        blade = self.real["blade"]
        self.assertIn(blade["fallback"], trs_mod.rs_archetypes(self.real))

    def test_every_role_target_conflicts_with_every_other(self):
        targets = trs_mod.rs_role_targets(self.real)
        self.assertGreater(len(targets), 1)
        for unit in targets:
            body = trs_mod.rs_unit_body(trs__ROOT, unit)
            have = set()
            for line in body.splitlines():
                if line.startswith("Conflicts="):
                    have |= set(line.split("=", 1)[1].split())
            self.assertEqual(have, set(targets) - {unit}, unit)

class trs_TestKeyAccessIsNotAnArchetype(unittest.TestCase):
    """`endpoint` is both an archetype and an ordinary TOML key. A token after
    `.` is a key access; a bare one, or one after `-`, is a hardcoded role."""

    def _scan(self, body):
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, True)
        path = os.path.join(root, "usr/lib/mios/blade.sh")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(body)
        trs_data = {"blade": {"archetypes": {"endpoint": [], "hybrid": ["service-plane"]}}}
        return trs_mod.rs_check_no_hardcoded_roles(trs_data, root)

    def test_a_toml_key_access_is_not_flagged(self):
        self.assertEqual(self._scan('printf "no [ai].endpoint resolved"\n'), [])

    def test_a_bare_role_literal_is_still_flagged(self):
        self.assertTrue(self._scan('case "$r" in endpoint) : ;; esac\n'))

    def test_a_hyphenated_unit_literal_is_still_flagged(self):
        # `-` is NOT excluded: mios-endpoint.target restates the archetype.
        self.assertTrue(self._scan('systemctl start mios-endpoint.target\n'))


"""Tests for the fan-out pool gate."""

import os
import unittest
from importlib.machinery import SourceFileLoader

tnp__HERE = os.path.dirname(os.path.abspath(__file__))
tnp__ROOT = os.path.dirname(tnp__HERE)
tnp_mod = SourceFileLoader(
    "check_node_pool", os.path.join(tnp__HERE, "check-ssot.py")).load_module()

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

tnp_VOCAB = "gpu:8,cpu:7,accelerator:6,igpu:3,mobile:2,_default:5"

def tnp_data(nodes, blades=None, vocab=tnp_VOCAB):
    d = {"dispatch": {"lane_priority": vocab}, "nodes": dict(nodes)}
    if blades is not None:
        d["blades"] = dict(blades)
    return d

tnp_GPU = {"endpoint": "http://localhost:${MIOS_PORTS_LLM_HEAVY}/v1",
       "model": "mios-heavy", "lane": "gpu"}

class tnp_TestAliases(unittest.TestCase):
    def test_an_exact_duplicate_fails(self):
        # Four of six shipped nodes were this.
        out = tnp_mod.np_aliases(tnp_data({"a": dict(tnp_GPU), "b": dict(tnp_GPU)}))
        self.assertTrue(out)
        self.assertIn("duplicates", out[0])

    def test_a_different_model_on_one_endpoint_is_not_an_alias(self):
        b = dict(tnp_GPU); b["model"] = "mios-agent-cpu"
        self.assertEqual(tnp_mod.np_aliases(tnp_data({"a": dict(tnp_GPU), "b": b})), [])

    def test_an_inert_placeholder_is_never_an_alias(self):
        inert = {"endpoint": "", "model": "mios-igpu", "lane": "igpu"}
        self.assertEqual(
            tnp_mod.np_aliases(tnp_data({"a": dict(inert), "b": dict(inert)})), [])

class tnp_TestLanes(unittest.TestCase):
    def test_one_endpoint_declared_as_two_lanes_fails(self):
        b = dict(tnp_GPU); b["lane"] = "cpu"; b["model"] = "other"
        out = tnp_mod.np_lane_conflicts(tnp_data({"a": dict(tnp_GPU), "b": b}))
        self.assertTrue(out)
        self.assertIn("one endpoint", out[0])

    def test_a_lane_dispatch_does_not_budget_fails(self):
        n = dict(tnp_GPU); n["lane"] = "quantum"
        out = tnp_mod.np_illegal_lanes(tnp_data({"a": n}))
        self.assertTrue(out)
        self.assertIn("quantum", out[0])

    def test_an_empty_vocabulary_fails_rather_than_passing_vacuously(self):
        self.assertTrue(tnp_mod.np_illegal_lanes(tnp_data({"a": dict(tnp_GPU)}, vocab="")))

    def test_the_real_vocabulary_is_read_from_dispatch(self):
        self.assertEqual(tnp_mod.np_lane_vocabulary(tnp_data({})),
                         {"gpu", "cpu", "accelerator", "igpu", "mobile"})

class tnp_TestBlades(unittest.TestCase):
    def test_omitting_blade_is_legal(self):
        # No blade == the LOCAL blade, whose name comes from [identity].hostname.
        self.assertEqual(tnp_mod.np_orphan_blades(tnp_data({"a": dict(tnp_GPU)})), [])

    def test_naming_a_blade_that_does_not_exist_fails(self):
        n = dict(tnp_GPU); n["blade"] = "blade-99"
        self.assertTrue(tnp_mod.np_orphan_blades(tnp_data({"a": n}, blades={})))

    def test_naming_a_declared_blade_is_clean(self):
        n = dict(tnp_GPU); n["blade"] = "blade-01"
        self.assertEqual(
            tnp_mod.np_orphan_blades(tnp_data({"a": n}, blades={"blade-01": {}})), [])

class tnp_TestOffloadability(unittest.TestCase):
    def test_a_baked_local_port_fails(self):
        n = {"endpoint": "http://localhost:8520/v1", "model": "m", "lane": "gpu"}
        out = tnp_mod.np_unmovable_endpoints(tnp_data({"a": n}))
        self.assertTrue(out)
        self.assertIn("8520", out[0])

    def test_a_templated_local_port_is_clean(self):
        self.assertEqual(tnp_mod.np_unmovable_endpoints(tnp_data({"a": dict(tnp_GPU)})), [])

    def test_a_remote_host_is_clean(self):
        n = {"endpoint": "http://blade-01.mesh:8520/v1", "model": "m", "lane": "gpu"}
        self.assertEqual(tnp_mod.np_unmovable_endpoints(tnp_data({"a": n})), [])

class tnp_TestRealTree(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(tnp__ROOT, tnp_mod.np_TOML), "rb") as fh:
            self.real = tomllib.load(fh)

    def test_the_shipped_pool_is_clean(self):
        self.assertEqual(tnp_mod.np_classify(self.real), [])

    def test_an_empty_pool_fails_rather_than_passing_vacuously(self):
        self.assertTrue(tnp_mod.np_classify({"dispatch": {"lane_priority": tnp_VOCAB},
                                      "nodes": {}}))

    def test_the_pool_actually_has_a_cpu_lane(self):
        # It did not: local-cpu pointed at the GPU endpoint with lane="gpu".
        lanes = {str(c.get("lane") or "") for c in tnp_mod.np_nodes(self.real).values()}
        self.assertIn("cpu", lanes)

    def test_every_reachable_endpoint_is_distinct(self):
        eps = [c["endpoint"] for c in tnp_mod.np_nodes(self.real).values()
               if c.get("endpoint")]
        self.assertEqual(len(eps), len(set(eps)))


"""Tests for the blade activation-coverage gate."""

import os
import unittest
from importlib.machinery import SourceFileLoader

tbc__HERE = os.path.dirname(os.path.abspath(__file__))
tbc__ROOT = os.path.dirname(tbc__HERE)
# A root with no usr/lib/systemd/system: synthetic cases must see only their
# own declared containers, never the real tree's 18 long-running units.
tbc__NOROOT = os.path.join(tbc__HERE, "no-such-root")
tbc_mod = SourceFileLoader(
    "check_blade_coverage", os.path.join(tbc__HERE, "check-ssot.py")).load_module()

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

def tbc_data(conts=(), archetypes=None, req=None, ungated=None, fallbacks=None):
    blade = {"archetypes": dict(archetypes or {"hybrid": ["gpu-serving"]})}
    if req is not None:
        blade["requires"] = dict(req)
    if ungated is not None:
        blade["ungated"] = list(ungated)
    # ADR-0017 D2: a gpu-serving unit must name a CPU lane to degrade to. The
    # fixtures below opt in explicitly so that rule is exercised by its own
    # test rather than firing as a side effect of every other one.
    if fallbacks is not None:
        blade["cpu_fallbacks"] = dict(fallbacks)
    return {"containers": {c: {} for c in conts}, "blade": blade}

class tbc_TestReaders(unittest.TestCase):
    def test_a_bare_string_capability_is_read_as_a_list(self):
        d = tbc_data(["a"], req={"a": "gpu-serving"})
        self.assertEqual(tbc_mod.bc_requires(d), {"a": ["gpu-serving"]})

    def test_archetype_caps_unions_every_archetype(self):
        d = tbc_data(archetypes={"hybrid": ["x", "y"], "compute": ["y"], "seat": []})
        self.assertEqual(tbc_mod.bc_archetype_caps(d), {"x", "y"})

class tbc_TestClassify(unittest.TestCase):
    def test_fully_classified_is_clean(self):
        d = tbc_data(["a", "b"], req={"a": ["gpu-serving"]}, ungated=["b"],
                 fallbacks={"a": ["cpu"]})
        self.assertEqual(tbc_mod.bc_classify(d, tbc__NOROOT), [])

    def test_unclassified_container_fails(self):
        d = tbc_data(["a", "b"], req={"a": ["gpu-serving"]}, ungated=[],
                 fallbacks={"a": ["cpu"]})
        self.assertEqual(len(tbc_mod.bc_classify(d, tbc__NOROOT)), 1)
        self.assertIn("'b'", tbc_mod.bc_classify(d, tbc__NOROOT)[0])

    def test_gpu_unit_without_a_cpu_fallback_fails(self):
        """ADR-0017 D2: GPU-gated work degrades to a CPU lane, it does not vanish."""
        d = tbc_data(["a"], req={"a": ["gpu-serving"]}, ungated=[], fallbacks={})
        self.assertTrue(any("cpu_fallbacks" in v for v in tbc_mod.bc_classify(d, tbc__NOROOT)))
        ok = tbc_data(["a"], req={"a": ["gpu-serving"]}, ungated=[], fallbacks={"a": ["cpu"]})
        self.assertFalse(any("cpu_fallbacks" in v for v in tbc_mod.bc_classify(ok, tbc__NOROOT)))

    def test_classified_both_ways_fails(self):
        d = tbc_data(["a"], req={"a": ["gpu-serving"]}, ungated=["a"])
        self.assertTrue(any("classified more than once" in v for v in tbc_mod.bc_classify(d, tbc__NOROOT)))

    def test_requires_naming_a_missing_container_fails(self):
        d = tbc_data(["a"], req={"ghost": ["gpu-serving"]}, ungated=["a"])
        self.assertTrue(any("not a declared container" in v for v in tbc_mod.bc_classify(d, tbc__NOROOT)))

    def test_register_naming_a_missing_container_fails(self):
        d = tbc_data(["a"], req={"a": ["gpu-serving"]}, ungated=["ghost"])
        self.assertTrue(any("not a declared container" in v for v in tbc_mod.bc_classify(d, tbc__NOROOT)))

    def test_empty_capability_list_fails(self):
        d = tbc_data(["a"], req={"a": []}, ungated=[])
        self.assertTrue(any("gates nothing" in v for v in tbc_mod.bc_classify(d, tbc__NOROOT)))

    def test_capability_no_archetype_grants_fails(self):
        d = tbc_data(["a"], archetypes={"hybrid": ["gpu-serving"]},
                 req={"a": ["storage-serving"]}, ungated=[])
        self.assertTrue(any("granted by NO" in v for v in tbc_mod.bc_classify(d, tbc__NOROOT)))

    def test_duplicate_register_entry_fails(self):
        d = tbc_data(["a", "b"], req={"a": ["gpu-serving"]}, ungated=["b", "b"])
        self.assertTrue(any("twice" in v for v in tbc_mod.bc_classify(d, tbc__NOROOT)))

    def test_empty_container_table_fails_rather_than_passing_vacuously(self):
        self.assertIn("vacuously", tbc_mod.bc_classify(tbc_data([], req={}, ungated=[]), tbc__NOROOT)[0])

class tbc_TestShippedTree(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(tbc__ROOT, tbc_mod.bc_TOML), "rb") as fh:
            self.real = tomllib.load(fh)

    def test_the_real_ssot_classifies_every_container(self):
        self.assertEqual(tbc_mod.bc_classify(self.real, tbc__ROOT), [])

    def test_the_gpu_lanes_are_capability_gated(self):
        req = tbc_mod.bc_requires(self.real)
        for svc in ("mios-llm-heavy", "mios-llm-worker@"):
            self.assertIn("gpu-serving", req.get(svc, []), svc)

    def test_the_seat_archetype_grants_nothing(self):
        # An endpoint (a seat) must expand to NO capabilities, or it is not a seat.
        self.assertEqual(self.real["blade"]["archetypes"]["endpoint"], [])

    def test_the_register_is_drained_and_stays_drained(self):
        # This assertion started as "not empty yet" -- a guard that fired the
        # moment T-319 drained the register. Revisited as designed: empty is now
        # the goal state, so the claim is the stronger one.
        self.assertEqual(tbc_mod.bc_register(self.real), [])

    def test_every_container_is_capability_gated(self):
        req = tbc_mod.bc_requires(self.real)
        for c in sorted(tbc_mod.bc_containers(self.real)):
            self.assertTrue(req.get(c), "%s is gated by nothing" % c)

    def test_the_long_running_units_are_in_scope(self):
        # This gate once counted CONTAINERS only and reported "23 of 23" over a
        # set that excluded 18 long-running units. Guard the wider scope.
        units = tbc_mod.bc_long_running_units(tbc__ROOT)
        self.assertGreater(len(units), 10)
        self.assertIn("mios-agent-pipe", units)
        self.assertNotIn("mios-firstboot", units)   # oneshots need no blade gate

    def test_seat_side_units_are_not_also_gated(self):
        req, seat = tbc_mod.bc_requires(self.real), set(tbc_mod.bc_seat_side(self.real))
        self.assertFalse(seat & set(req))

    def test_the_front_door_is_seat_side(self):
        # A seat with no agent-pipe has no way to reach its blade.
        self.assertIn("mios-agent-pipe", tbc_mod.bc_seat_side(self.real))

    def test_no_unit_activates_a_gated_unit_without_its_capability(self):
        # Derived, not hand-classified: this found 11 units that would start on a
        # blade where their dependency is condition-skipped and fail forever --
        # a seat running pgvector backups against a database it does not have.
        self.assertEqual(tbc_mod.bc_dependency_violations(self.real, tbc__ROOT), [])

    def test_after_alone_does_not_propagate_a_gate(self):
        # After= is ordering only; it activates nothing, so it must not force a
        # capability onto a unit that merely sequences behind a gated one.
        pulls = tbc_mod.bc_unit_pulls(tbc__ROOT)
        self.assertNotIn("mios-llm-heavy", pulls.get("mios-gpu-nvidia", set()))

    def test_the_soft_ok_exemption_names_only_real_units(self):
        self.assertTrue(set(tbc_mod.bc_soft_ok(self.real))
                        <= tbc_mod.bc_known_units(self.real, tbc__ROOT))

    def test_oneshots_may_be_gated_but_are_not_required_to_be(self):
        must = tbc_mod.bc_all_units(self.real, tbc__ROOT)
        known = tbc_mod.bc_known_units(self.real, tbc__ROOT)
        self.assertTrue(must < known)                       # strictly wider
        self.assertIn("mios-pgvector-backup", set(tbc_mod.bc_requires(self.real)))
        self.assertNotIn("mios-pgvector-backup", must)      # a oneshot

    def test_every_long_running_unit_has_exactly_one_classification(self):
        req = set(tbc_mod.bc_requires(self.real))
        seat = set(tbc_mod.bc_seat_side(self.real))
        reg = set(tbc_mod.bc_register(self.real))
        for u in sorted(tbc_mod.bc_long_running_units(tbc__ROOT)):
            hits = [g for g, s in (("requires", req), ("seat_side", seat),
                                   ("ungated", reg)) if u in s]
            self.assertEqual(len(hits), 1, "%s -> %s" % (u, hits))

class tbc_TestSeatDeadWeight(unittest.TestCase):
    """The AI plane couples over ADDRESSES, which the dependency walk cannot see."""

    def _tree(self, tmp, units, seat, req, urls=None, endpoint=""):
        d = os.path.join(tmp, "usr/lib/systemd/system")
        os.makedirs(d, exist_ok=True)
        for name, body in units.items():
            with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
                fh.write(body)
        return {"ports": {"worker_cdp": 9223, "front": 8700},
                "urls": dict(urls or {}),
                "ai": {"endpoint": endpoint},
                "blade": {"archetypes": {"hybrid": ["service-plane"], "endpoint": []},
                          "seat_side": list(seat), "requires": dict(req)}}

    def test_a_seat_side_binder_whose_only_client_is_gated_fails(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            d = self._tree(tmp, {
                "browser-w.service": "Environment=MIOS_PORT_WORKER_CDP=9223\n",
                "worker.service": "Environment=URL=http://localhost:9223\n",
            }, seat=["browser-w"], req={"worker": ["service-plane"]})
            out = tbc_mod.bc_seat_dead_weight(d, tmp)
            self.assertTrue(any("browser-w" in v for v in out), out)

    def test_an_ungated_client_clears_it(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            d = self._tree(tmp, {
                "browser-w.service": "Environment=MIOS_PORT_WORKER_CDP=9223\n",
                "tool.service": "Environment=URL=http://localhost:9223\n",
            }, seat=["browser-w"], req={})
            self.assertEqual(tbc_mod.bc_seat_dead_weight(d, tmp), [])

    def test_a_person_facing_port_is_exempt(self):
        # The front door's client is every human and CLI, not another unit.
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            d = self._tree(tmp, {
                "front.service": "Environment=MIOS_PORT_FRONT=8700\n",
                "owui.service": "Environment=URL=http://localhost:8700\n",
            }, seat=["front"], req={"owui": ["service-plane"]},
                endpoint="http://localhost:${MIOS_PORT_FRONT}/v1")
            self.assertEqual(tbc_mod.bc_seat_dead_weight(d, tmp), [])

    def test_a_urls_entry_also_makes_a_port_person_facing(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            d = self._tree(tmp, {
                "front.service": "Environment=MIOS_PORT_FRONT=8700\n",
                "owui.service": "Environment=URL=http://localhost:8700\n",
            }, seat=["front"], req={"owui": ["service-plane"]},
                urls={"front": "http://localhost:${MIOS_PORT_FRONT}/"})
            self.assertEqual(tbc_mod.bc_seat_dead_weight(d, tmp), [])

    def test_the_real_tree_has_no_dead_weight_on_a_seat(self):
        with open(os.path.join(tbc__ROOT, "usr/share/mios/mios.toml"), "rb") as fh:
            real = tomllib.load(fh)
        self.assertEqual(tbc_mod.bc_seat_dead_weight(real, tbc__ROOT), [])


import os
import shutil
import tempfile
import unittest
from importlib.machinery import SourceFileLoader

tfs__HERE = os.path.dirname(os.path.abspath(__file__))
tfs__ROOT = os.path.dirname(tfs__HERE)
tfs_mod = SourceFileLoader(
    "check_fleet_safety", os.path.join(tfs__HERE, "check-ssot.py")).load_module()

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

tfs_K3S_SERVER = "[Container]\nExec=k3s server --disable=traefik\n"
tfs_K3S_JOIN = "[Container]\nEnvironment=K3S_URL=https://blade-01:6443\nExec=k3s agent\n"

def tfs_data(accepted=(), max_accepted=None, max_nodes=6, grantors=2,
         hazards_table=True, requires=("controller",)):
    arche = {"headless": ["service-plane"]}
    for i in range(grantors):
        arche["ctl%d" % i] = list(requires) + ["service-plane"]
    d = {
        "blades": {"min_nodes": 1, "typical_nodes": 3},
        "blade": {"archetypes": arche,
                  "requires": {"mios-k3s": list(requires)}},
    }
    if max_nodes is not None:
        d["blades"]["max_nodes"] = max_nodes
    if hazards_table:
        h = {"accepted": list(accepted)}
        if max_accepted is not None:
            h["max_accepted"] = max_accepted
        d["blades"]["hazards"] = h
    return d

def tfs_tree(k3s_body=tfs_K3S_SERVER, ha_body=""):
    root = tempfile.mkdtemp()
    qd = os.path.join(root, "usr/share/containers/systemd")
    os.makedirs(qd)
    with open(os.path.join(qd, "mios-k3s.container"), "w", newline="\n") as fh:
        fh.write(k3s_body)
    ud = os.path.join(root, "usr/lib/systemd/system")
    os.makedirs(ud)
    if ha_body:
        with open(os.path.join(ud, "mios-ha-bootstrap.service"), "w", newline="\n") as fh:
            fh.write(ha_body)
    return root

def tfs_only(viols, needle):
    return [v for v in viols if needle in v]

class tfs_TestK3sDetector(unittest.TestCase):
    def setUp(self):
        self.roots = []

    def tearDown(self):
        for r in self.roots:
            shutil.rmtree(r, ignore_errors=True)

    def make(self, **kw):
        r = tfs_tree(**kw)
        self.roots.append(r)
        return r

    def test_two_grantors_and_no_join_path_is_a_hazard(self):
        self.assertIn("k3s-multi-server", tfs_mod.fs_detect(tfs_data(), self.make()))

    def test_one_grantor_is_not_a_hazard(self):
        # A single archetype standing up one control plane is the correct shape.
        self.assertNotIn("k3s-multi-server",
                         tfs_mod.fs_detect(tfs_data(grantors=1), self.make()))

    def test_a_join_path_clears_it(self):
        # K3S_URL means the peers join rather than each initialising.
        self.assertNotIn("k3s-multi-server",
                         tfs_mod.fs_detect(tfs_data(), self.make(k3s_body=tfs_K3S_JOIN)))

    def test_a_commented_out_server_is_not_a_hazard(self):
        r = self.make(k3s_body="[Container]\n# Exec=k3s server\nExec=/bin/true\n")
        self.assertNotIn("k3s-multi-server", tfs_mod.fs_detect(tfs_data(), r))

    def test_the_detail_names_the_grantors(self):
        detail = tfs_mod.fs_detect(tfs_data(grantors=3), self.make())["k3s-multi-server"]
        self.assertIn("ctl0", detail)
        self.assertIn("K3S_URL", detail)

class tfs_TestPacemakerDetector(unittest.TestCase):
    def setUp(self):
        self.roots = []

    def tearDown(self):
        for r in self.roots:
            shutil.rmtree(r, ignore_errors=True)

    def test_fencing_disabled_is_a_hazard(self):
        r = tfs_tree(ha_body="ExecStart=pcs property set stonith-enabled=false\n")
        self.roots.append(r)
        found = tfs_mod.fs_detect(tfs_data(), r)
        self.assertIn("pacemaker-unfenced", found)
        self.assertIn("mios-ha-bootstrap.service:1", found["pacemaker-unfenced"])

    def test_a_comment_about_fencing_is_not_a_hazard(self):
        r = tfs_tree(ha_body="# we used to set stonith-enabled=false here\nExecStart=/bin/true\n")
        self.roots.append(r)
        self.assertNotIn("pacemaker-unfenced", tfs_mod.fs_detect(tfs_data(), r))

    def test_no_pacemaker_config_is_not_a_hazard(self):
        r = tfs_tree()
        self.roots.append(r)
        self.assertNotIn("pacemaker-unfenced", tfs_mod.fs_detect(tfs_data(), r))

class tfs_TestRegister(unittest.TestCase):
    def setUp(self):
        self.root = tfs_tree()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_an_accepted_hazard_is_silent(self):
        self.assertEqual(
            tfs_mod.fs_violations(tfs_data(("k3s-multi-server",), 1), self.root), [])

    def test_an_unaccepted_hazard_fails(self):
        v = tfs_mod.fs_violations(tfs_data((), 0), self.root)
        self.assertTrue(tfs_only(v, "k3s-multi-server"), v)

    def test_standalone_disarms_the_hazards(self):
        # max_nodes = 1 is a real deployment, not a loophole: the hazards
        # genuinely do not bite, and raising max_nodes re-arms them.
        self.assertEqual(tfs_mod.fs_violations(tfs_data((), 0, max_nodes=1), self.root), [])
        self.assertTrue(tfs_mod.fs_violations(tfs_data((), 0, max_nodes=2), self.root))

    def test_max_nodes_must_be_declared(self):
        v = tfs_mod.fs_violations(tfs_data((), 0, max_nodes=None), self.root)
        self.assertTrue(tfs_only(v, "max_nodes is unset"), v)

    def test_absent_hazards_table(self):
        v = tfs_mod.fs_violations(tfs_data(hazards_table=False), self.root)
        self.assertTrue(tfs_only(v, "[blades.hazards] is absent"), v)

    def test_an_entry_that_no_longer_reproduces_must_leave(self):
        v = tfs_mod.fs_violations(tfs_data(("k3s-multi-server", "pacemaker-unfenced"), 2),
                           self.root)
        self.assertTrue(tfs_only(v, "no longer reproduces"), v)

    def test_an_unknown_hazard_id_can_never_retire(self):
        v = tfs_mod.fs_violations(tfs_data(("ghost-hazard", "k3s-multi-server"), 2), self.root)
        self.assertTrue(tfs_only(v, "no detector produces"), v)

    def test_unsorted_and_duplicated(self):
        self.assertTrue(tfs_only(tfs_mod.fs_violations(
            tfs_data(("pacemaker-unfenced", "k3s-multi-server"), 2), self.root),
            "not sorted"))
        self.assertTrue(tfs_only(tfs_mod.fs_violations(
            tfs_data(("k3s-multi-server", "k3s-multi-server"), 2), self.root),
            "twice"))

    def test_ceiling_absent_over_and_left_high(self):
        self.assertTrue(tfs_only(tfs_mod.fs_violations(tfs_data(("k3s-multi-server",)), self.root),
                             "max_accepted is unset"))
        self.assertTrue(tfs_only(tfs_mod.fs_violations(tfs_data(("k3s-multi-server",), 0), self.root),
                             "over the ratchet ceiling"))
        self.assertTrue(tfs_only(tfs_mod.fs_violations(tfs_data(("k3s-multi-server",), 9), self.root),
                             "lower it to 1"))

class tfs_TestRealTree(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(tfs__ROOT, "usr/share/mios/mios.toml"), "rb") as fh:
            self.real = tomllib.load(fh)

    def test_the_shipped_register_is_clean(self):
        self.assertEqual(tfs_mod.fs_violations(self.real, tfs__ROOT), [])

    def test_the_operators_fleet_shape_is_declared(self):
        shape = tfs_mod.fs_fleet_shape(self.real)
        self.assertEqual(shape["max_nodes"], 6)
        self.assertEqual(shape["typical_nodes"], 3)
        self.assertEqual(shape["min_nodes"], 1)

    def test_both_hazards_really_reproduce_in_the_tree(self):
        # If they stopped, the register entries must go -- this is what makes
        # the register shrink-only rather than decorative.
        self.assertEqual(set(tfs_mod.fs_detect(self.real, tfs__ROOT)),
                         {"k3s-multi-server", "pacemaker-unfenced"})

    def test_the_ceiling_equals_the_register(self):
        self.assertEqual(tfs_mod.fs_max_accepted(self.real),
                         len(tfs_mod.fs_register(self.real)))

def main():
    # unittest discovers every TestCase in this module, so one call runs all of
    # them; prefixing is what keeps five same-named suites from shadowing.
    rc = 0 if unittest.main(argv=[sys.argv[0]], exit=False).result.wasSuccessful() else 1
    return rc | (tmti_main() or 0)


if __name__ == "__main__":
    sys.exit(main())
