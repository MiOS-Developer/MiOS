#!/usr/bin/env python3
# AI-hint: Characterization and safety tests for mios-os-recipe tokenized argv execution without shell injection.
# AI-related: usr/libexec/mios/mios-os-recipe, automation/98-drift-checks.sh
# AI-functions: test_no_shell_true_in_recipe, test_build_argv_metacharacters_inert, test_execution_no_command_injection, main

import importlib.machinery
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_RECIPE_BIN = os.path.join(_HERE, "mios-os-recipe")

loader = importlib.machinery.SourceFileLoader("mios_os_recipe", _RECIPE_BIN)
spec = importlib.util.spec_from_loader("mios_os_recipe", loader)
recipe_mod = importlib.util.module_from_spec(spec)
loader.exec_module(recipe_mod)


class TestRecipeSafety(unittest.TestCase):
    def test_no_shell_true_in_recipe_source(self):
        with open(_RECIPE_BIN, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn("shell=True", src)

    def test_build_argv_metacharacters_inert(self):
        malicious_input = "; rm -rf /tmp/fake_target; echo pwned"
        params = {"msg": malicious_input}
        declared = ["msg"]

        # 1. Non-pipeline template
        template = "echo -n {msg}"
        argv, display = recipe_mod._build_argv(
            template, params, declared, windows=False
        )
        self.assertEqual(argv[0], "echo")
        self.assertEqual(argv[1], "-n")
        self.assertEqual(argv[2], malicious_input)
        self.assertEqual(len(argv), 3)

        # 2. Pipeline template
        pipeline_template = "echo {msg} | tr 'a-z' 'A-Z'"
        argv_pipe, display_pipe = recipe_mod._build_argv(
            pipeline_template, params, declared, windows=False
        )
        self.assertEqual(argv_pipe[0], "/bin/bash")
        self.assertEqual(argv_pipe[1], "-c")
        self.assertIn('"$1"', argv_pipe[2])
        self.assertEqual(argv_pipe[3], "mios-os-recipe")
        self.assertEqual(argv_pipe[4], malicious_input)

    def test_execution_no_command_injection(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            marker_file = os.path.join(tmpdir, "injected.txt")
            payload = f"safe_text; touch {marker_file}"

            # Direct test of _build_argv execution
            argv, _ = recipe_mod._build_argv(
                "echo -n {data}", {"data": payload}, ["data"], windows=False
            )
            r = subprocess.run(argv, shell=False, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0)
            self.assertEqual(r.stdout, payload)
            self.assertFalse(
                os.path.exists(marker_file), "Command injection succeeded!"
            )


def main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(TestRecipeSafety)
    runner = unittest.TextTestRunner(verbosity=2)
    res = runner.run(suite)
    return 0 if res.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
