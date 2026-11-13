#!/usr/bin/env python3
# AI-hint: Two-sided tests for mios-spec-01, the spec-coverage gate: the vendor SSOT passes; an unread applicable key, an unmapped section, a stale or reasonless declaration, a bad map, a failing pipeline and a parity drift each fail by name.
# AI-related: usr/libexec/mios/win/xbox/spec_coverage.py, usr/libexec/mios/win/xbox/build.py, usr/share/mios/mios.toml, tests/fixtures/mios-xbox/fixture-identity.toml
"""Run: python3 -I tests/test-mios-spec-coverage.py -v   (temp dirs only; no network, no VM)"""

from __future__ import annotations

import contextlib
import copy
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(ROOT, "usr", "libexec", "mios", "win", "xbox"))
import spec_coverage as SC  # noqa: E402
import build as B  # noqa: E402

VENDOR = os.path.join(ROOT, "usr", "share", "mios", "mios.toml")
FIXTURE_IDENTITY = os.path.join(HERE, "fixtures", "mios-xbox", "fixture-identity.toml")
LAYERS = [VENDOR, FIXTURE_IDENTITY]
IMAGE = "mios-xbox"
PROBE = '[colors]\nspec_probe_unread = "#010203"\n'


def load(layers):
    return SC.R.mios_toml.load_merged(layers)


@contextlib.contextmanager
def overlay_layers(text: str):
    with tempfile.TemporaryDirectory(prefix="mios-spec-coverage-test.") as d:
        layers = list(LAYERS)
        if text:
            path = os.path.join(d, "overlay.toml")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
            layers.append(path)
        yield layers


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.merged = load(LAYERS)
        cls.reads = SC.trace_xbox(cls.merged, LAYERS)
        cls.baseline, cls.baseline_bad = SC.check(cls.merged, LAYERS)

    def gate(self, text: str = ""):
        with overlay_layers(text) as layers:
            return SC.check(load(layers), layers)

    def assertViolation(self, bad, *fragments):
        self.assertTrue([v for v in bad if all(f in v for f in fragments)], f"no violation naming {fragments}; got {bad}")

    def leaf_of(self, section: str) -> str:
        node = SC._get(self.merged, section)
        return next(p for p in SC.leaves(node, section))


class TestVendor(Base):
    def test_vendor_ssot_passes_with_every_unit_accounted_for(self):
        self.assertEqual(self.baseline_bad, [])
        r = self.baseline["images"][IMAGE]
        self.assertEqual(r["units"], r["covered"] + r["not_applicable"] + r["pending"])
        self.assertGreater(r["covered"], 0)
        self.assertEqual(r["pending_keys"], sorted(self.merged["spec"]["coverage"]["pending"][IMAGE]))
        self.assertIn("mios", self.baseline["not_gated"])

    def test_cli_exit_status_names_the_violation(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(SC.main(["--check", "--layer", VENDOR, "--layer", FIXTURE_IDENTITY]), 0)
        self.assertIn(f"[spec-coverage] {IMAGE}:", out.getvalue())
        self.assertIn("UNRUN", out.getvalue())
        with overlay_layers(PROBE) as layers:
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                self.assertEqual(SC.main(["--check"] + [a for p in layers for a in ("--layer", p)]), 1)
        self.assertIn("VIOLATION mios-xbox: colors.spec_probe_unread", err.getvalue())


class TestCoverage(Base):
    def test_an_unread_applicable_key_fails(self):
        _, bad = self.gate(PROBE)
        self.assertViolation(bad, "colors.spec_probe_unread applies to mios-xbox but its pipeline never reads it")

    def test_a_declared_key_is_counted_not_failed(self):
        for kind in SC.DECLARATIONS:
            with self.subTest(kind=kind):
                report, bad = self.gate(PROBE + f'[spec.coverage.{kind}.mios-xbox]\n'
                                                '"colors.spec_probe_unread" = "a probe key no image renders, for the gate test"\n')
                self.assertEqual(bad, [])
                self.assertEqual(report["images"][IMAGE][kind], self.baseline["images"][IMAGE][kind] + 1)

    def test_an_unmapped_subtable_fails(self):
        _, bad = self.gate('[theme.spec_probe]\nvalue = 1\n')
        self.assertViolation(bad, "spec section [theme.spec_probe] has no [spec.coverage.sections] entry")

    def test_section_granularity_flags_a_new_subsection_and_a_new_root_scalar(self):
        _, bad = self.gate('[windows.xbox.spec_probe]\nvalue = 1\n')
        self.assertViolation(bad, "windows.xbox.spec_probe applies to mios-xbox")
        _, bad = self.gate('[windows.xbox]\nspec_probe_scalar = 1\n')
        self.assertViolation(bad, "windows.xbox.spec_probe_scalar applies to mios-xbox")

    def test_the_section_map_decides_applicability(self):
        _, bad = self.gate('[theme.edge]\nspec_probe = 1\n')   # theme.edge maps to Linux images only
        self.assertEqual(bad, [])
        _, bad = self.gate('[theme.terminal]\nspec_probe = 1\n')   # theme.terminal maps to mios-xbox too
        self.assertViolation(bad, "theme.terminal.spec_probe applies to mios-xbox")

    def test_a_declaration_the_pipeline_contradicts_is_stale(self):
        read = next(p for p in sorted(self.reads) if p.startswith("colors.") and not isinstance(SC._get(self.merged, p), dict))
        _, bad = self.gate(f'[spec.coverage.pending.mios-xbox]\n"{read}" = "planted stale declaration for the gate test"\n')
        self.assertViolation(bad, f'"{read}" is stale')

    def test_a_declaration_outside_the_image_fails(self):
        key = self.leaf_of("theme.edge")
        _, bad = self.gate(f'[spec.coverage.not_applicable.mios-xbox]\n"{key}" = "planted declaration outside the image"\n')
        self.assertViolation(bad, f'"{key}" declares a key that does not apply to mios-xbox')

    def test_a_declaration_needs_a_reason_and_a_real_key(self):
        _, bad = self.gate(PROBE + '[spec.coverage.pending.mios-xbox]\n"colors.spec_probe_unread" = "n/a"\n')
        self.assertViolation(bad, '"colors.spec_probe_unread": needs a reason')
        _, bad = self.gate('[spec.coverage.pending.mios-xbox]\n"colors.no_such_probe_key" = "a reason that is long enough"\n')
        self.assertViolation(bad, '"colors.no_such_probe_key": no such SSOT key')


class TestMap(Base):
    def cov(self):
        m = copy.deepcopy(self.merged)
        return m, m["spec"]["coverage"]

    def plan_bad(self, m):
        return SC.plan(m)[1]

    def test_an_image_table_is_required(self):
        m, cov = self.cov()
        cov["images_ref"] = "no.such.table"
        self.assertEqual(SC.plan(m), ({}, ["[spec.coverage].images_ref names no image table"]))

    def test_sections_name_known_images(self):
        m, cov = self.cov()
        cov["sections"]["colors"] = {"images": ["mios-nope"]}
        self.assertViolation(self.plan_bad(m), "[spec.coverage.sections].colors: unknown image(s) ['mios-nope']")
        cov["sections"]["colors"] = {"images": "some"}
        self.assertViolation(self.plan_bad(m), '[spec.coverage.sections].colors: images must be "all" or a non-empty list')

    def test_roots_gated_images_and_declarations_are_validated(self):
        m, cov = self.cov()
        cov["roots"] = dict(cov["roots"], colors="leaf")
        cov["gated"] = [IMAGE, "mios"]
        cov["pending"] = dict(cov["pending"], **{"mios-nope": {"colors.bg": "a reason that is long enough"}})
        bad = self.plan_bad(m)
        self.assertViolation(bad, "[spec.coverage.roots].colors: granularity must be one of")
        self.assertViolation(bad, "[spec.coverage].gated: no pipeline tracer for mios")
        self.assertViolation(bad, "[spec.coverage.pending].mios-nope: not an image")


class TestParity(Base):
    def test_vendor_parity_holds(self):
        self.assertEqual(SC.parity(self.merged), [])

    def test_a_parity_drift_fails(self):
        m = copy.deepcopy(self.merged)
        m["theme"]["cursor_linux"]["size"] = m["theme"]["cursor_windows"]["base_size"] + 8
        self.assertViolation(SC.parity(m), "parity: theme.cursor_windows.base_size", "differs from theme.cursor_linux.size")

    def test_malformed_parity_entries_fail(self):
        m = copy.deepcopy(self.merged)
        m["spec"]["parity"]["equal"] = [["theme.cursor_windows.base_size"], ["theme.cursor_windows.base_size", "theme.no_such"]]
        bad = SC.parity(m)
        self.assertViolation(bad, "is not a pair of SSOT keys")
        self.assertViolation(bad, "theme.no_such is not an SSOT key")


class TestTracer(Base):
    def test_reads_lookups_and_iteration_are_recorded(self):
        t = SC._Tracer()
        d = SC.Traced({"a": {"b": 1, "c": 2}, "z": 0}, "", t)
        d["a"].get("b")
        self.assertTrue("z" in d)
        self.assertEqual(t.read, {"a", "a.b", "z"})
        list(d["a"].items())
        self.assertIn("a.c", t.read)
        t.on = False
        d.get("q")
        self.assertNotIn("q", t.read)

    def test_the_trace_restores_patches_and_never_sees_the_operator_home(self):
        walk, home = SC.R._walk_refs, SC.R._operator_home
        seen = []
        real = SC.S.items_from_spec

        def spy(doc, tree):
            seen.append(os.listdir(SC.R._operator_home()))
            return real(doc, tree)

        before = copy.deepcopy(self.merged)
        with mock.patch.object(SC.S, "items_from_spec", spy):
            SC.trace_xbox(self.merged, LAYERS)
        self.assertEqual(seen, [[]])
        self.assertIs(SC.R._walk_refs, walk)
        self.assertIs(SC.R._operator_home, home)
        self.assertEqual(self.merged, before)

    def test_a_pipeline_that_does_not_render_is_a_violation(self):
        def broken(merged, layers, tree):
            raise SC.R.SpecError("colors.bg", "planted render failure")

        _, bad = SC.check(self.merged, LAYERS, tracers={IMAGE: broken})
        self.assertViolation(bad, "mios-xbox: its pipeline does not render from this SSOT (colors.bg: planted render failure)")


class TestBuildPlan(Base):
    def plan(self, layers):
        with mock.patch.object(B, "storage_capacity", return_value={"blockers": []}), \
                mock.patch.object(B.W, "mem_available_gib", return_value=1024), \
                mock.patch.object(B.W, "watchdog_inactive", return_value=True):
            return B.plan(layers, lambda *args, **kwargs: (0, "shut off\n"))

    def test_plan_reports_coverage_and_turns_violations_into_problems(self):
        plan = self.plan(LAYERS)
        self.assertEqual(plan["spec_coverage"]["images"][IMAGE]["covered"], self.baseline["images"][IMAGE]["covered"])
        self.assertFalse([p for p in plan["problems"] if p.startswith("spec coverage:")])
        with overlay_layers(PROBE) as layers:
            plan = self.plan(layers)
        self.assertTrue([p for p in plan["problems"] if p.startswith("spec coverage: mios-xbox: colors.spec_probe_unread")])


if __name__ == "__main__":
    unittest.main()
