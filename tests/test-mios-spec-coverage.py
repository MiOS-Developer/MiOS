#!/usr/bin/env python3
# AI-hint: Two-sided tests for mios-spec-01, the spec-coverage gate: the vendor SSOT passes; an unread applicable key, an unmapped section, a stale or reasonless declaration, a bad map, a failing pipeline and a parity drift each fail by name.
# AI-related: usr/libexec/mios/win/xbox/spec_coverage.py, usr/libexec/mios/win/xbox/build.py, usr/share/mios/mios.toml, tests/fixtures/mios-xbox/fixture-identity.toml
"""Run: python3 -I tests/test-mios-spec-coverage.py -v   (temp dirs only; no network, no VM)"""

from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
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
PRIVATE_VALUE = "fixture-" + "private-parity-value"
PRIVATE_PARITY = ('[identity]\ndefault_password = ' + json.dumps(PRIVATE_VALUE)
                  + '\n[spec.parity]\nequal = [["identity.default_password", "identity.username"]]\n')


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

    def test_empty_gated_list_cannot_disable_coverage(self):
        _, bad = self.gate('[spec.coverage]\ngated = []\n')
        self.assertViolation(bad, "[spec.coverage].gated must be a non-empty list")

    def test_a_required_image_cannot_be_replaced_by_another_valid_image(self):
        m, cov = self.cov()
        cov["gated"] = ["mios"]
        def read_all(*args):
            return set(SC.leaves(m, ""))
        with mock.patch.dict(SC.TRACERS, {"mios": read_all}):
            _, bad = SC.check(m, LAYERS, tracers={"mios": read_all}, required_image=IMAGE)
        self.assertEqual(bad, [f"{IMAGE}: this pipeline requires its image in [spec.coverage].gated"])

    def test_an_empty_applicability_map_cannot_pass(self):
        m, cov = self.cov()
        cov["roots"] = {}
        cov["pending"] = {}
        cov["not_applicable"] = {}
        _, bad = SC.check(m, LAYERS)
        self.assertViolation(bad, f"{IMAGE}: no applicable coverage units")


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

    def test_parity_pairs_have_exactly_two_nonempty_string_paths(self):
        for row in (["theme.cursor_linux.size", "theme.cursor_linux.size", "theme.no_such"],
                    ["theme.cursor_linux.size", 3], ["", "theme.cursor_linux.size"]):
            with self.subTest(row=row):
                m = copy.deepcopy(self.merged)
                m["spec"]["parity"]["equal"] = [row]
                self.assertViolation(SC.parity(m), "exactly two non-empty string key paths required")

    def test_parity_diagnostics_report_paths_without_merged_values(self):
        m = copy.deepcopy(self.merged)
        m["identity"]["default_password"] = PRIVATE_VALUE
        m["spec"]["parity"]["equal"] = [["identity.default_password", "identity.username"]]
        bad = SC.parity(m)
        self.assertViolation(bad, "parity: identity.default_password differs from identity.username")
        self.assertNotIn(PRIVATE_VALUE, json.dumps(bad))


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

    def test_a_missing_configured_tracer_does_not_skip_or_use_the_default(self):
        _, bad = SC.check(self.merged, LAYERS, tracers={})
        self.assertViolation(bad, f"{IMAGE}: configured pipeline tracer is unavailable")


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

    def test_private_parity_values_never_enter_plan_json_or_gate_output(self):
        with overlay_layers(PRIVATE_PARITY) as layers:
            plan_json = json.dumps(self.plan(layers))
            self.assertIn("identity.default_password differs from identity.username", plan_json)
            self.assertNotIn(PRIVATE_VALUE, plan_json)
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                self.assertEqual(SC.main(["--check"] + [a for p in layers for a in ("--layer", p)]), 1)
            self.assertIn("identity.default_password differs from identity.username", err.getvalue())
            self.assertNotIn(PRIVATE_VALUE, out.getvalue() + err.getvalue())


class TestBuildAdmission(Base):
    """Exercise the real non-plan CLI; machine admission and every mutation are tripwired."""

    @contextlib.contextmanager
    def admission(self, layers):
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(B, "build_layers", return_value=layers))
            stack.enter_context(mock.patch.object(B.os, "geteuid", return_value=0, create=True))
            storage = stack.enter_context(mock.patch.object(
                B, "require_storage_capacity", side_effect=RuntimeError("admission-reached")))
            effects = [stack.enter_context(mock.patch.object(owner, name, side_effect=AssertionError(name)))
                       for owner, name in ((B.os, "execv"), (B.os, "execvp"), (B, "host_preflight"),
                                           (B.Build, "new"), (B, "run_stage"), (B, "run_service"), (B, "run_gate"))]
            yield storage, effects

    def test_valid_coverage_reaches_admission_without_starting_work(self):
        with self.admission(LAYERS) as (storage, effects):
            with self.assertRaisesRegex(RuntimeError, "admission-reached"):
                B.main(["stage"])
            storage.assert_called_once()
            for effect in effects:
                effect.assert_not_called()

    def test_every_nonplan_command_rejects_unread_keys_before_any_machine_admission(self):
        with overlay_layers(PROBE) as layers:
            for command in ("capture", "stage", "service", "gate", "run"):
                with self.subTest(command=command), self.admission(layers) as (storage, effects):
                    err = io.StringIO()
                    with contextlib.redirect_stderr(err):
                        self.assertEqual(B.main([command]), 2)
                    self.assertIn("[preflight] spec coverage:", err.getvalue())
                    self.assertIn("colors.spec_probe_unread", err.getvalue())
                    storage.assert_not_called()
                    for effect in effects:
                        effect.assert_not_called()

    def test_the_nonplan_build_cannot_turn_off_coverage(self):
        with overlay_layers('[spec.coverage]\ngated = []\n') as layers:
            with self.admission(layers) as (storage, effects):
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    self.assertEqual(B.main(["run"]), 2)
                self.assertIn("this pipeline requires its image", err.getvalue())
                storage.assert_not_called()
                for effect in effects:
                    effect.assert_not_called()

    def test_private_parity_values_never_enter_build_errors(self):
        with overlay_layers(PRIVATE_PARITY) as layers:
            with self.admission(layers) as (storage, effects):
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    self.assertEqual(B.main(["run"]), 2)
                self.assertIn("identity.default_password differs from identity.username", err.getvalue())
                self.assertNotIn(PRIVATE_VALUE, err.getvalue())
                storage.assert_not_called()
                for effect in effects:
                    effect.assert_not_called()


class TestBuildSnapshot(Base):
    @contextlib.contextmanager
    def state(self):
        with tempfile.TemporaryDirectory(prefix="mios-spec-snapshot-test.") as tmp:
            root = Path(tmp)
            layer = root / "operator.toml"
            text = ('[windows.xbox.build]\nwork_root = ' + json.dumps(str(root / "work"))
                    + '\nartifact_root = ' + json.dumps(str(root / "artifacts"))
                    + '\n[windows.xbox.probe]\npython = ' + json.dumps(sys.executable) + '\n')
            layer.write_text(text, encoding="utf-8")
            yield root, layer, text, LAYERS + [str(layer)]

    def test_a_save_after_preflight_cannot_change_render_or_resolver_inputs(self):
        with self.state() as (root, layer, text, layers):
            edition = SC._get(load(layers), "windows.xbox.source.edition")
            observed = []

            def late_save(*args):
                layer.write_text(text + PROBE + '[windows.xbox.source]\nedition = "unadmitted-edition"\n', encoding="utf-8")

            def resolve(cfg, *args, **kwargs):
                observed.append(cfg["uup_edition"])
                return {"artifacts": {}}

            with mock.patch.object(B, "build_layers", return_value=layers), \
                    mock.patch.object(B.os, "geteuid", return_value=0, create=True), \
                    mock.patch.dict(os.environ, {B.SCOPED_ENV: "1"}), \
                    mock.patch.object(B, "require_storage_capacity"), \
                    mock.patch.object(B, "host_preflight", side_effect=late_save), \
                    mock.patch.object(B.RL, "resolve_all", side_effect=resolve), \
                    mock.patch.object(B, "stage_fetch", side_effect=B.BuildError("fixture stop before download")), \
                    mock.patch.object(B.F, "fetch_all", side_effect=AssertionError("download")) as download, \
                    mock.patch.object(B, "run_service", side_effect=AssertionError("worker")) as worker, \
                    mock.patch.object(B.os, "execv", side_effect=AssertionError("exec")), \
                    mock.patch.object(B.os, "execvp", side_effect=AssertionError("scope")), \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(B.main(["run"]), 2)
            self.assertIn("fixture stop before download", err.getvalue())
            self.assertIn("spec_probe_unread", layer.read_text())
            saved = list((root / "artifacts").glob("build-*/xbox-spec.json"))
            self.assertEqual(len(saved), 1)
            self.assertEqual(json.loads(saved[0].read_text())["source"]["edition"], edition)
            self.assertEqual(observed, [edition])
            download.assert_not_called()
            worker.assert_not_called()

    def test_direct_new_freezes_admission_and_direct_render_rejects_unadmitted_state(self):
        with self.state() as (root, layer, text, layers):
            b = B.Build.new(str(root / "artifacts"), layers)
            layer.write_text(text + PROBE, encoding="utf-8")
            B.stage_render(b)   # uses the earlier admitted snapshot
            self.assertTrue(os.path.isfile(b.spec_path))
            other = B.Build(str(root / "unadmitted"), layers)
            with mock.patch.object(B.render_spec, "_write_atomic") as write:
                with self.assertRaisesRegex(B.BuildError, "colors.spec_probe_unread"):
                    B.stage_render(other)
                write.assert_not_called()

    def test_clean_resume_keeps_recorded_storage_and_reuses_validated_config(self):
        with self.state() as (root, layer, text, layers):
            b = B.Build.new(str(root / "artifacts"), layers)
            B.stage_render(b)
            layer.write_text(text.replace(json.dumps(str(root / "work")),
                                          json.dumps(str(root / "changed-work"))), encoding="utf-8")
            resumed = B.Build(b.root, layers)
            with mock.patch.object(B.RL, "resolve_all", return_value={"artifacts": {}}) as resolve:
                B.stage_resolve(resumed)
            self.assertEqual(resumed.spec()["spec"]["build"]["work_root"], str(root / "work"))
            resolve.assert_called_once()

    def test_stale_or_unread_saved_state_stops_before_resolver_and_preserves_cache(self):
        with self.state() as (root, layer, text, layers):
            b = B.Build.new(str(root / "artifacts"), layers)
            B.stage_render(b)
            original = {p: Path(p).read_bytes() for p in (b.spec_path, b.guest_spec_path)}
            for change, message in ((PROBE, "colors.spec_probe_unread"),
                                    ('[windows.xbox.source]\nedition = "changed-edition"\n', "preserve this build state")):
                layer.write_text(text + change, encoding="utf-8")
                with self.subTest(change=change), mock.patch.object(B.RL, "resolve_all") as resolve:
                    with self.assertRaisesRegex(B.BuildError, message):
                        B.stage_resolve(B.Build(b.root, layers))
                    resolve.assert_not_called()
                for path, data in original.items():
                    with open(path, "rb") as fh:
                        self.assertEqual(fh.read(), data)
                self.assertFalse(os.path.exists(b.manifest_path))

    def test_missing_saved_spec_requires_new_state_without_resolver_effects(self):
        with self.state() as (root, layer, text, layers):
            with mock.patch.object(B.RL, "resolve_all") as resolve:
                with self.assertRaisesRegex(B.BuildError, "start a new build or re-render"):
                    B.stage_resolve(B.Build(str(root / "missing"), layers))
                resolve.assert_not_called()


if __name__ == "__main__":
    unittest.main()
