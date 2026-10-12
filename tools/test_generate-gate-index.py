#!/usr/bin/env python3
# AI-hint: Sibling test for tools/generate-gate-index.py; proves a row never carries a description belonging to another check.
# AI-related: tools/native/mios-gen/src/indexes.rs, automation/98-drift-checks.sh
"""Each case is a row the index must NOT emit.

An index row is the only published description of a gate, so a row describing
the wrong check is worse than a terse one: it is read as the check's contract.
"""
import os
import shutil
import subprocess
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)

GATE = """\
# --- described neighbour ---
check_described() {
    echo "[98-drift-checks] described neighbour"
}

check_one_liner() { _run_py_check check_one_liner tools/fake-tool.py; }

check_sub_command() { _run_py_check check_sub_command "tools/fake-multi.py a-subcommand"; }

check_flagged() { _run_py_check check_flagged "tools/fake-tool.py --check"; }

check_elided() { _run_py_check check_elided tools/fake-elided.py; }

# --- a later multi-line check with its own echo ---
check_later() {
    echo "[98-drift-checks] a later multi-line check with its own echo"
}
main() {
    check_described
    check_one_liner
    check_sub_command
    check_flagged
    check_elided
    check_later
}
"""

class TestDescription(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.root = cls.tmp.name
        os.makedirs(os.path.join(cls.root, "tools"))
        for name, hint in (
                ("fake-tool.py", "Drift gate for a fake thing. And more prose"),
                ("fake-multi.py", "Module that runs many unrelated checks"),
                ("fake-elided.py", "A hint the tagger cut off mid-sent...")):
            with open(os.path.join(cls.root, "tools", name), "w") as fh:
                fh.write("#!/usr/bin/env python3\n# AI-hint: %s\n" % hint)
        os.makedirs(os.path.join(cls.root, "automation"))
        with open(os.path.join(cls.root, "automation", "98-drift-checks.sh"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(GATE)
        binary = os.environ.get("MIOS_GEN_BIN") or shutil.which("mios-gen")
        if not binary:
            raise AssertionError("mios-gen is required to verify the production gate index")
        subprocess.run([binary, "gate-index", "--root", cls.root], check=True, capture_output=True, text=True)
        with open(os.path.join(cls.root, "usr/share/mios/reference/drift-gate-index.tsv"), encoding="utf-8") as fh:
            cls.descriptions = {row[1]: row[2] for line in fh if not line.startswith("#")
                                for row in [line.rstrip("\n").split("\t")]}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _desc(self, name):
        return self.descriptions[name]

    def test_a_comment_above_the_definition_wins(self):
        self.assertEqual("described neighbour", self._desc("check_described"))

    def test_a_one_liner_does_not_inherit_the_next_functions_echo(self):
        """The defect: `(.*?)\\n\\}` ran past a one-liner into check_later."""
        got = self._desc("check_one_liner")
        self.assertNotIn("later multi-line", got)
        self.assertNotIn("described neighbour", got)
        self.assertEqual("Drift gate for a fake thing", got)

    def test_a_sub_command_does_not_borrow_the_modules_hint(self):
        self.assertEqual("sub command", self._desc("check_sub_command"))

    def test_a_flag_still_describes_the_tool(self):
        self.assertEqual("Drift gate for a fake thing", self._desc("check_flagged"))

    def test_an_elided_hint_is_not_republished(self):
        self.assertEqual("elided", self._desc("check_elided"))

    def test_the_shipped_index_has_no_two_checks_sharing_a_description(self):
        seen = {}
        path = os.path.join(_ROOT, "usr/share/mios/reference/drift-gate-index.tsv")
        with open(path, encoding="utf-8") as fh:
            rows = [l.rstrip("\n").split("\t") for l in fh
                    if l.strip() and not l.startswith("#")]
        self.assertGreater(len(rows), 100, "the index did not load")
        for row in rows:
            self.assertEqual(3, len(row), row)
            seen.setdefault(row[2], []).append(row[1])
        shared = {d: n for d, n in seen.items() if len(n) > 1}
        self.assertEqual({}, shared, "checks sharing one description")

if __name__ == "__main__":
    unittest.main(verbosity=1)
