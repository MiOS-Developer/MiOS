#!/usr/bin/env python3
# AI-hint: Agent CLI interaction projection and the mios agent launcher. Every `mios agent NAME` wrapper (agy, claude,
# codex, gemini, aider, copilot, opencode, zcode -> mios-mcp-server --launch-agent) first runs
# `mios-gen render-agent-interaction --root / --home ~ --runtime`. From 2026-10-18 that verb was declared (SSOT
# generation.sync and the launcher) but never implemented, so every wrapper crashed with "unrecognized subcommand".
#   1. contract: every mios-gen verb that [generation.sync.steps] or mios-mcp-server calls exists in the binary under
#      test (MIOS_GEN, default /usr/bin/mios-gen). Negative control: MIOS_GEN_OLD (the 50ae5690 build) lacks one.
#   2. behaviour: the verb merges [agent_cli.interaction.clients.*].values into a home's client files (fresh files
#      0600, unrelated keys kept), is idempotent, --check reports and writes nothing (rc 1), a malformed client file
#      is left untouched with a note on stderr (rc 0: a broken client file must not block launching another agent),
#      and without --home it only validates the table.
#   3. the launcher names a non-executable agent binary (mode 0700 after a root self-update) instead of "not installed".
# AI-related: tools/native/mios-gen/src/agent_interaction.rs, usr/libexec/mios/mios-mcp-server,
# usr/share/mios/mios.toml [agent_cli.interaction] [generation.sync]
"""Agent interaction projection and launcher contract tests."""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import tempfile
import tomllib
import unittest

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SSOT = os.path.join(ROOT, "usr", "share", "mios", "mios.toml")
MCP = os.path.join(ROOT, "usr", "libexec", "mios", "mios-mcp-server")
GEN = os.environ.get("MIOS_GEN", "/usr/bin/mios-gen")
GEN_OLD = os.environ.get("MIOS_GEN_OLD", "")


def ssot():
    with open(SSOT, "rb") as f:
        return tomllib.load(f)


def called_verbs():
    verbs = set()
    for step in ssot()["generation"]["sync"]["steps"]:
        for call in step.get("calls", []):
            if call.get("tool") == "mios-gen" and call.get("args"):
                verbs.add(call["args"][0])
    with open(MCP, encoding="utf-8") as f:
        verbs.update(re.findall(r'"/usr/libexec/mios/mios-gen",\s*"([a-z0-9-]+)"', f.read()))
    return sorted(verbs)


def missing(binary):
    return [v for v in called_verbs() if subprocess.run([binary, v, "--help"], capture_output=True).returncode]


def gen(*args, home=None):
    cmd = [GEN, "render-agent-interaction", "--root", ROOT, *args]
    if home:
        cmd += ["--home", home]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30)


@unittest.skipUnless(os.access(GEN, os.X_OK), f"{GEN} not available")
class Contract(unittest.TestCase):
    def test_every_called_verb_exists(self):
        verbs = called_verbs()
        self.assertIn("render-agent-interaction", verbs)
        self.assertEqual(missing(GEN), [])

    @unittest.skipUnless(GEN_OLD, "MIOS_GEN_OLD (a build without the verb) not provided")
    def test_negative_old_build_lacks_the_verb(self):
        self.assertIn("render-agent-interaction", missing(GEN_OLD))


@unittest.skipUnless(os.access(GEN, os.X_OK), f"{GEN} not available")
class Behaviour(unittest.TestCase):
    def setUp(self):
        self.clients = ssot()["agent_cli"]["interaction"]["clients"]
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)

    def path(self, name):
        return os.path.join(self.home.name, self.clients[name]["path"])

    def test_fresh_home_gets_every_client_private(self):
        r = gen(home=self.home.name)
        self.assertEqual(r.returncode, 0, r.stderr)
        for name, c in self.clients.items():
            p = self.path(name)
            self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600, p)
            with open(p, "rb") as f:
                data = json.load(f) if c["format"] == "json" else tomllib.load(f)
            for k, v in c["values"].items():
                self.assertEqual(data[k], v if not isinstance(v, dict) else {**data[k], **v}, f"{name}.{k}")

    def test_unrelated_keys_kept_and_idempotent(self):
        p = self.path("gemini")
        os.makedirs(os.path.dirname(p))
        with open(p, "w") as f:
            json.dump({"theme": "MiOS", "ui": {"showLineNumbers": True}}, f)
        self.assertEqual(gen(home=self.home.name).returncode, 0)
        with open(p) as f:
            data = json.load(f)
        self.assertEqual(data["theme"], "MiOS")
        self.assertTrue(data["ui"]["showLineNumbers"])
        before = {n: os.stat(self.path(n)).st_mtime_ns for n in self.clients}
        r = gen("--check", home=self.home.name)
        self.assertEqual(r.returncode, 0, "second projection has nothing to change")
        self.assertEqual(gen(home=self.home.name).returncode, 0)
        self.assertEqual(before, {n: os.stat(self.path(n)).st_mtime_ns for n in self.clients}, "no rewrite")

    def test_check_writes_nothing_and_fails_on_drift(self):
        r = gen("--check", home=self.home.name)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(os.listdir(self.home.name), [])

    def test_negative_malformed_client_file_untouched(self):
        p = self.path("codex")
        os.makedirs(os.path.dirname(p))
        with open(p, "w") as f:
            f.write("[tui\nbroken")
        r = gen(home=self.home.name)
        self.assertEqual(r.returncode, 0, "a broken client file must not block an agent launch")
        self.assertIn("left untouched", r.stderr)
        with open(p) as f:
            self.assertEqual(f.read(), "[tui\nbroken")

    def test_validate_only_without_home(self):
        r = gen()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("table valid", r.stdout)
        self.assertEqual(os.listdir(self.home.name), [])


class Launcher(unittest.TestCase):
    def test_non_executable_binary_is_named(self):
        with open(MCP, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("is not executable by this user", src)
        self.assertIn("render-agent-interaction", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
