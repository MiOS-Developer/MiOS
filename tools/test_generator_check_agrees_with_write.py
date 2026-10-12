#!/usr/bin/env python3
# AI-hint: Asserts a generator's --check mode compares what its write mode produces; the pairs are read from the gate's own projection-evidence emitter, not listed here.
# AI-related: tools/native/mios-gen/src/image.rs, tools/native/mios-gen/src/indexes.rs, automation/98-drift-checks.sh
"""Every gate-diffed generator's --check must agree with its write mode.

The defect this exists to catch: tools/generate-bib-configs.py --check
compared only the VALUE it projects, via a tolerant regex, while write mode
ALSO normalised surrounding whitespace. config/artifacts/iso.toml carried
aligned padding, so --check printed PASS on a file the generator rewrote on
sight. The drift gate calls --check, so it reported in-sync while the
committed artifact did not match its own generator.

A check that does not compare what the writer produces cannot detect the
drift the writer creates. This test asserts the invariant directly: run each
generator for real, and if it changed a tracked file, --check must have
refused to call the tree clean.

The generator -> target pairs are read from the gate itself rather than
listed here, so a generator added to the gate is covered without editing
this file.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_GATE = os.path.join(_ROOT, "automation", "98-drift-checks.sh")

def _projection_pairs(text=None):
    """(generator, [targets]) as declared by the gate's evidence emitter."""
    if text is None:
        with open(_GATE, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    pairs = []
    native_verb = None
    for line in text.splitlines():
        if re.match(r'^check_[a-z0-9_]+\(\)\s*\{', line):
            native_verb = None
        call = re.search(r'"\$(?:bin|gen)"\s+([a-z][a-z-]+)\s+--root', line)
        if call:
            native_verb = call.group(1)
        if "_emit_projection_evidence " not in line:
            continue
        args = re.findall(r'"([^"]+)"', line)
        if len(args) < 2:
            continue
        if native_verb is None or args[0] != native_verb:
            raise AssertionError("native projection evidence has no preceding generator invocation")
        pairs.append((("mios-gen", native_verb), args[1:]))
    return pairs


def _command(generator, root):
    if generator[0] == "mios-gen":
        binary = os.environ.get("MIOS_GEN_BIN") or shutil.which("mios-gen")
        if not binary:
            raise AssertionError("mios-gen is required for projection parity verification")
        return [binary, generator[1], "--root", root]
    return [generator[0], os.path.join(root, generator[1])]


def _snapshot(root):
    """Copy only tracked working bytes; generators never write the caller's tree."""
    census = subprocess.run(["git", "-C", _ROOT, "ls-files", "-z"], check=True, capture_output=True).stdout
    for name in census.decode("utf-8").split("\0"):
        if not name:
            continue
        source = os.path.join(_ROOT, name)
        target = os.path.join(root, name)
        if not os.path.isfile(source) and not os.path.islink(source):
            raise AssertionError(f"tracked projection input is missing: {name}")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if os.path.islink(source):
            os.symlink(os.readlink(source), target)
        else:
            shutil.copy2(source, target)
    subprocess.run(["git", "init", "-q", root], check=True, capture_output=True)
    subprocess.run(["git", "-C", root, "add", "."], check=True, capture_output=True)

class GeneratorCheckAgreesWithWrite(unittest.TestCase):
    def test_the_gate_declares_projection_pairs(self):
        # A zero-length list would make every other test here vacuous.
        self.assertTrue(_projection_pairs(),
                        "no _emit_projection_evidence pairs found in the gate; "
                        "this suite would silently test nothing")

    def test_check_mode_refuses_a_tree_write_mode_would_change(self):
        with tempfile.TemporaryDirectory() as root:
            _snapshot(root)
            env = dict(os.environ, MIOS_DRIFT_ROOT=root)
            for gen, targets in _projection_pairs():
                before = {}
                for rel in targets:
                    with open(os.path.join(root, rel), "rb") as fh:
                        before[rel] = fh.read()
                command = _command(gen, root)
                # --check first, on the untouched tree: after a write it could only ever agree.
                chk = subprocess.run(command + ["--check"],
                                     cwd=root, env=env,
                                     capture_output=True, text=True)
                for rel, original in before.items():
                    with open(os.path.join(root, rel), "rb") as fh:
                        self.assertEqual(fh.read(), original, "--check changed the fixture")
                write = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True)
                self.assertEqual(write.returncode, 0, write.stderr)
                changed = []
                for rel, original in before.items():
                    p = os.path.join(root, rel)
                    with open(p, "rb") as fh:
                        now = fh.read()
                    if now != original:
                        changed.append(rel)

                if changed:
                    self.assertNotEqual(
                        0, chk.returncode,
                        "%s --check reported the tree in sync, but running it "
                        "rewrote %s. check mode must compare what write mode "
                        "produces." % (" ".join(gen), ", ".join(changed)))
                checked = subprocess.run(command + ["--check"], cwd=root, env=env, capture_output=True, text=True)
                self.assertEqual(checked.returncode, 0, checked.stderr)
                # Prove every selected native command actually detects drift.
                drifted = os.path.join(root, targets[0])
                with open(drifted, "rb") as fh:
                    clean = fh.read()
                if gen == ("mios-gen", "bib-configs"):
                    # This projector preserves comments and owns only minsize.
                    planted, count = re.subn(rb'minsize\s*=\s*"[^"]+"', b'minsize = "1 GiB"', clean, count=1)
                    self.assertEqual(count, 1, "fixture has no projected minsize")
                    self.assertNotEqual(planted, clean)
                else:
                    planted = clean + b"\n# planted projection drift\n"
                with open(drifted, "wb") as fh:
                    fh.write(planted)
                refused = subprocess.run(command + ["--check"], cwd=root, env=env, capture_output=True, text=True)
                self.assertNotEqual(refused.returncode, 0, "planted drift was accepted: " + " ".join(gen))
                repaired = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True)
                self.assertEqual(repaired.returncode, 0, repaired.stderr)
                restored = subprocess.run(command + ["--check"], cwd=root, env=env, capture_output=True, text=True)
                self.assertEqual(restored.returncode, 0, restored.stderr)

    def test_native_evidence_without_a_command_fails_instead_of_skipping(self):
        with self.assertRaisesRegex(AssertionError, "no preceding generator"):
            _projection_pairs('check_bad() {\n_emit_projection_evidence "gate-index" "output"\n}')

    def test_generated_artifacts_are_lf_on_every_host(self):
        # Python text mode translates newlines to the host separator, so a
        # generator without an explicit newline="\n" emits CRLF on Windows and
        # LF on Linux. The gate diffs generated against committed, which made
        # these checks fire on who ran them rather than on real drift.
        cr = chr(13).encode()
        for gen, targets in _projection_pairs():
            for rel in targets:
                p = os.path.join(_ROOT, rel)
                if not os.path.isfile(p):
                    continue
                with open(p, "rb") as fh:
                    body = fh.read()
                self.assertNotIn(
                    cr, body,
                    "%s (written by %s) contains CR; pin the write with "
                    'newline="\n"' % (rel, gen))

if __name__ == "__main__":
    unittest.main(verbosity=2)
