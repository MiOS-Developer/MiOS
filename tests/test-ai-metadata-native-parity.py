#!/usr/bin/env python3
# AI-hint: Differential gate for the ai.metadata sync stage: `mios-gen ai-metadata` must export byte-identically to usr/libexec/mios/mios-ai-metadata.py over the real tree and over planted edge cases (CRLF, lone CR, BOM, non-ASCII and astral text, U+2028 and U+001C separators, the 60-line and 8192-character cuts, invalid UTF-8, dotfile extensions, symlinks, skipped directories); a deliberately mutated Python leg must make it go red.
# AI-related: tools/native/mios-gen/src/ai_metadata.rs, usr/libexec/mios/mios-ai-metadata.py, usr/lib/mios/mios_comments.py, usr/share/mios/ai/v1/metadata.json, tests/golden/README.md (ai-metadata)
# AI-functions: native_gen, python_leg, native_leg, plant_edge_cases, TestAiMetadataNativeParity
#
# MIOS_GEN_BIN=<path> selects the binary under test (default: the tree's own
# tools/native/target build, else the installed one).
import importlib.util, os, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "usr/libexec/mios/mios-ai-metadata.py"


def native_gen():
    override = os.environ.get("MIOS_GEN_BIN")
    if override:
        return override if os.access(override, os.X_OK) else None
    for cand in (ROOT / "tools/native/target/release/mios-gen", ROOT / "tools/native/target/debug/mios-gen",
                 Path("/usr/libexec/mios/mios-gen"), Path("/usr/bin/mios-gen")):
        if os.access(cand, os.X_OK):
            return str(cand)
    return None


def load_python_leg():
    spec = importlib.util.spec_from_file_location("mios_ai_metadata_leg", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def python_leg(mod, root):
    return mod.render_catalog_json(mod.build_metadata_catalog(str(root)))


def native_leg(gen, root, out):
    res = subprocess.run([gen, "ai-metadata", "--root", str(root), "--export", str(out)],
                         capture_output=True, text=True, timeout=300, env={"PATH": "/usr/bin:/bin"})
    if res.returncode != 0:
        raise AssertionError("mios-gen ai-metadata failed (%d): %s" % (res.returncode, res.stderr[-1500:]))
    return Path(out).read_text(encoding="utf-8")


def plant_edge_cases(root):
    """A standalone fixture tree (no git index) holding every reading rule."""
    w = lambda rel, data: (root / rel).parent.mkdir(parents=True, exist_ok=True) or (root / rel).write_bytes(data)
    w("crlf.sh", b"#!/bin/sh\r\n# AI-hint: crlf header \r\n# AI-related: a,b\r\n")
    w("lonecr.py", b"# AI-hint: lone cr\r# AI-functions: f, g ,, h\r")
    w("bom.md", "﻿<!-- AI-hint: bom first -->\n".encode())
    w("unicode.rs", "// AI-hint: café \U0001F600 \u007f tab\there\n// AI-doc: döc.md\n".encode())
    w("sep.ini", "; AI-hint: before ; AI-related: after sep\n\x1c; AI-doc: fs-sep \x1c\n".encode())
    w("late.sh", b"\n" * 59 + b"# AI-hint: on line 60\n# AI-related: line 61\n")
    w("cut.cfg", b"x" * 8180 + b"\n# AI-hint: cut by the 8192-character limit\n")
    w("bad-utf8.sql", b"-- AI-hint: invalid \xff\xfe bytes \xe2\x82 here\n")
    w(".hidden.md", b"<!-- AI-hint: dotfile ext -->\n")
    w("UPPER.QML", b"// ai-hint: case folded tag\n")
    w("arrow.html", b"<!-- AI-hint: trailing arrow   -->   \n<!-- AI-related: x -->\n")
    w("target/skipped.rs", b"// AI-hint: in a skipped dir\n")
    w(".dotdir/skipped.rs", b"// AI-hint: in a dot dir\n")
    w("first-wins.sh", b"# AI-hint: first\n# AI-hint: second\n# AI-related: \n# AI-related: r2\n")
    (root / "link.sh").symlink_to("crlf.sh")
    (root / "dangling.sh").symlink_to("nowhere.sh")
    (root / "dirlink").symlink_to("target")


class TestAiMetadataNativeParity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gen = native_gen()
        if not cls.gen:
            raise unittest.SkipTest("cannot run: no mios-gen (build tools/native or set MIOS_GEN_BIN)")
        probe = subprocess.run([cls.gen, "ai-metadata", "--help"], capture_output=True)
        if probe.returncode != 0:
            raise unittest.SkipTest("cannot run: %s has no ai-metadata verb" % cls.gen)
        cls.mod = load_python_leg()
        cls.tmp = Path(tempfile.mkdtemp(prefix="mios-aimeta-"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_real_tree_is_byte_identical(self):
        native = native_leg(self.gen, ROOT, self.tmp / "tree.json")
        python = python_leg(self.mod, ROOT)
        self.assertEqual(native.encode(), python.encode(), "native and Python catalogs of the tree differ")

    def test_edge_cases_are_byte_identical(self):
        fx = self.tmp / "edge"
        fx.mkdir()
        plant_edge_cases(fx)
        out = self.tmp / "edge.json"   # outside the fixture: it must not count itself
        native = native_leg(self.gen, fx, out)
        python = python_leg(self.mod, fx)
        self.assertEqual(native, python)
        # The fixture really exercises the cuts it claims to.
        self.assertIn('"path": "late.sh"', native)
        self.assertNotIn("line 61", native)
        self.assertNotIn("8192-character", native)
        self.assertNotIn("skipped dir", native)

    def test_a_divergent_leg_goes_red(self):
        """Negative control: the comparison catches a one-line rule change."""
        fx = self.tmp / "neg"
        fx.mkdir()
        plant_edge_cases(fx)
        native = native_leg(self.gen, fx, self.tmp / "neg.json")
        mutated = load_python_leg()
        orig = mutated.extract_ai_header_metadata

        def sixty_one_lines(content, rel):
            # Reads one line further than the rule: late.sh gains "line 61".
            return orig(content.split("\n", 1)[1] if rel == "late.sh" else content, rel)

        mutated.extract_ai_header_metadata = sixty_one_lines
        self.assertNotEqual(native, python_leg(mutated, fx))


if __name__ == "__main__":
    unittest.main()
