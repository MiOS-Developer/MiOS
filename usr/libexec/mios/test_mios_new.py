#!/usr/bin/env python3
# AI-hint: Unit-proves the mios-new scaffolder allocates ordinals from the directory rather than from a frozen literal (Law 16; ADR-0021).
# AI-related: /usr/libexec/mios/mios-new, /usr/share/mios/mios.toml, src/mios-rs/miosd/src/main.rs, usr/share/doc/mios/adr/0021-rust-static-binary-consolidation.md
"""Ordinal allocation in ``mios-new``.

Both halves: an ordinal moves when the listing says so; an execution-order
ordinal does not.
"""
import importlib.machinery
import importlib.util
import os
import shutil
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "../../.."))
_MIOS_NEW = os.path.join(_HERE, "mios-new")


def _load():
    loader = importlib.machinery.SourceFileLoader("mios_new", _MIOS_NEW)
    spec = importlib.util.spec_from_loader("mios_new", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


MOD = _load()


class TestNextOrdinal(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="ordinal-")
        self.addCleanup(shutil.rmtree, self.d, True)

    def _touch(self, *names):
        for n in names:
            open(os.path.join(self.d, n), "w").close()

    def test_an_empty_directory_yields_the_first_ordinal(self):
        self.assertEqual(1, MOD.next_ordinal(self.d, 4))

    def test_a_missing_directory_yields_the_first_ordinal(self):
        """Scaffolding into a tree that has none of this type yet is legitimate."""
        self.assertEqual(1, MOD.next_ordinal(os.path.join(self.d, "absent"), 4))

    def test_the_ordinal_follows_the_directory(self):
        self._touch("0001-a.md", "0002-b.md", "0003-c.md")
        self.assertEqual(4, MOD.next_ordinal(self.d, 4))

    def test_a_gap_is_filled_before_the_tail_grows(self):
        self._touch("0001-a.md", "0003-c.md")
        self.assertEqual(2, MOD.next_ordinal(self.d, 4))

    def test_unnumbered_entries_are_ignored(self):
        self._touch("README.md", "0001-a.md")
        self.assertEqual(2, MOD.next_ordinal(self.d, 4))

    def test_the_width_is_honoured(self):
        """A 2-digit scan must not read the first two digits of a 4-digit name."""
        self._touch("0001-a.md")
        self.assertEqual(1, MOD.next_ordinal(self.d, 2))


class TestDestPath(unittest.TestCase):
    """Against the shipped tree, so the SSOT wiring is what is under test."""

    def _dest(self, type_name, name):
        return os.path.relpath(MOD.get_dest_path(name, type_name, _ROOT), _ROOT)

    def test_an_adr_gets_the_next_free_number_not_a_literal(self):
        dest = self._dest("adr", "some-new-decision")
        head = os.path.basename(dest)[:5]
        self.assertRegex(head, r"^\d{4}-")
        # The whole defect was that this stayed at the frozen seed forever.
        self.assertNotEqual("0001-", head, "the seed was emitted instead of a free ordinal")
        self.assertFalse(os.path.exists(os.path.join(_ROOT, dest)),
                         "%s already exists -- the ordinal collided" % dest)

    def test_an_adr_the_caller_numbered_is_left_alone(self):
        self.assertEqual("usr/share/doc/mios/adr/0099-explicit.md",
                         self._dest("adr", "0099-explicit"))

    def test_an_automation_step_does_not_get_a_sequential_ordinal(self):
        """NN- encodes execution order; allocating max+1 would misplace the step
        and, at two digits, run past 99 into the postcheck's own number."""
        self.assertEqual("automation/99-myfeature.sh",
                         self._dest("automation-step", "myfeature"))

    def test_an_automation_step_the_author_placed_is_left_alone(self):
        self.assertEqual("automation/45-myfeature.sh",
                         self._dest("automation-step", "45-myfeature"))

    def test_a_non_ordinal_prefix_is_still_a_literal(self):
        self.assertEqual("usr/libexec/mios/mios-widget.py",
                         self._dest("python-tool", "widget"))


class TestTwinAgreement(unittest.TestCase):
    """The Rust scaffolder in miosd is the other half of this contract."""

    _MIOSD = os.path.join(_ROOT, "src/mios-rs/miosd/src/main.rs")

    def test_neither_twin_still_compares_the_prefix_to_a_literal(self):
        for path in (_MIOS_NEW, self._MIOSD):
            with open(path, encoding="utf-8", errors="replace") as fh:
                src = fh.read()
            for magic in ('"0012-"', '"99-"'):
                # assertTrue, not assertNotIn: a failing assertNotIn prints the
                # whole 6k-line haystack, burying the one line that matters.
                self.assertTrue(
                    "== %s" % magic not in src,
                    "%s still special-cases %s by value" % (path, magic))

    def test_the_rust_twin_has_the_ordinal_allocator(self):
        with open(self._MIOSD, encoding="utf-8", errors="replace") as fh:
            src = fh.read()
        self.assertIn("fn next_ordinal(", src)
        self.assertIn("name_ordinal_next", src)


if __name__ == "__main__":
    unittest.main(verbosity=1)
