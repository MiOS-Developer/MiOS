#!/usr/bin/env python3
# AI-hint: Asserts mios_comments.load_ai_tag loads usr/libexec/mios/mios-ai-tag on an interpreter without the removed importlib load_module() API (Python 3.15+), which the image build's docs index stage depends on.
# AI-related: usr/lib/mios/mios_comments.py, usr/libexec/mios/mios-ai-tag, usr/libexec/mios/mios-manual
# AI-doc: usr/share/doc/mios/manual/tests.md
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Runs in a child so stripping the API cannot leak into this process.
PROBE = r"""
import importlib.machinery, sys
for cls in importlib.machinery.SourceFileLoader.__mro__:
    if "load_module" in vars(cls):
        try:
            delattr(cls, "load_module")
        except (AttributeError, TypeError):
            pass
assert not hasattr(importlib.machinery.SourceFileLoader, "load_module"), "could not simulate 3.15"
sys.path.insert(0, sys.argv[1] + "/usr/lib/mios")
import mios_comments
mod = mios_comments.load_ai_tag(sys.argv[1])
print("loaded" if mod is not None and hasattr(mod, "main") else "none")
"""


class AiTagLoader(unittest.TestCase):
    def test_loads_without_load_module(self):
        out = subprocess.run([sys.executable, "-c", PROBE, str(ROOT)],
                             capture_output=True, text=True, check=True)
        self.assertEqual(out.stdout.strip(), "loaded", out.stderr)

    def test_loads_on_this_interpreter(self):
        sys.path.insert(0, str(ROOT / "usr/lib/mios"))
        import mios_comments
        self.assertIsNotNone(mios_comments.load_ai_tag(str(ROOT)))


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False, verbosity=2).result.wasSuccessful() else 1)
