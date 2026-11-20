#!/usr/bin/env python3
# AI-hint: Asserts the mios-agents image runs code-server: its final ENTRYPOINT is code-server in exec form with an empty CMD, and mios-agents.service passes only code-server arguments after the image name, so podman never execs a flag (exit 127).
# AI-related: usr/share/mios/agents/Containerfile, usr/lib/systemd/system/mios-agents.service
# AI-doc: usr/share/doc/mios/manual/tests.md
import json
import re
import shlex
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CF = ROOT / "usr/share/mios/agents/Containerfile"
UNIT = ROOT / "usr/lib/systemd/system/mios-agents.service"
IMAGE = "localhost/mios-agents:latest"


def final_stage(text: str) -> list[str]:
    lines = [l for l in text.splitlines() if l.strip() and not l.lstrip().startswith("#")]
    last_from = max(i for i, l in enumerate(lines) if l.upper().startswith("FROM "))
    return lines[last_from:]


def instruction(lines: list[str], name: str):
    found = [l.split(None, 1)[1] if len(l.split(None, 1)) > 1 else "" for l in lines
             if l.split(None, 1)[0].upper() == name]
    return found[-1] if found else None


def exec_start(text: str) -> list[str]:
    joined = re.sub(r"\\\n", " ", text)
    m = re.search(r"^ExecStart=(.*)$", joined, re.M)
    return shlex.split(m.group(1))


class AgentsEntrypoint(unittest.TestCase):
    def test_final_stage_entrypoint_is_code_server(self):
        stage = final_stage(CF.read_text(encoding="utf-8"))
        ep = instruction(stage, "ENTRYPOINT")
        self.assertIsNotNone(ep, "final stage has no ENTRYPOINT: podman would exec the first flag")
        self.assertEqual(json.loads(ep), ["/usr/bin/code-server"])
        self.assertEqual(json.loads(instruction(stage, "CMD") or "null"), [],
                         "the base image's CMD must be cleared")

    def test_unit_passes_only_editor_arguments(self):
        argv = exec_start(UNIT.read_text(encoding="utf-8"))
        args = argv[argv.index(IMAGE) + 1:]
        self.assertTrue(args, "no code-server arguments after the image")
        self.assertTrue(args[0].startswith("--"), f"first argument {args[0]!r} would replace the entrypoint's role")
        self.assertIn("--bind-addr", args)


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False, verbosity=2).result.wasSuccessful() else 1)
