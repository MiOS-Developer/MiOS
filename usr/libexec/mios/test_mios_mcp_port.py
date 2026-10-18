#!/usr/bin/env python3
# AI-hint: Two-sided controls for task_3428 -- mios-mcp.service gets its MCP port from the resolver-rendered install.env, never a unit literal (Law 7).
# AI-related: usr/lib/systemd/system/mios-mcp.service, usr/libexec/mios/mcp-server-runner, usr/lib/mios/mios_toml.py, usr/share/mios/mios.toml
# AI-functions: render_exports, run_preamble, unit_port_literals, TestMcpPort
"""task_3428: WHEN mios-mcp.service starts THE SYSTEM SHALL have its MCP port
defined -- by the resolver-rendered /etc/mios/install.env, not a literal.

* the exports the resolver renders from the vendor mios.toml carry
  MIOS_PORTS_MCP / MIOS_PORTS_MCP equal to [ports].mcp;
* mcp-server-runner's preamble, run under exactly that environment (ambient
  MIOS_* scrubbed), resolves MIOS_PORTS_MCP to that value;
* neither the unit nor its [units."mios-mcp.service"] SSOT mirror assigns a
  MIOS_* port literal.

Negative controls: the same environment without the port names must stop
the runner with its named error, and a planted Environment=MIOS_PORTS_MCP=<n>
must be named by the literal check.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tomllib
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, "..", "..", ".."))
_RUNNER = os.path.join(_ROOT, "usr", "libexec", "mios", "mcp-server-runner")
_UNIT = os.path.join(_ROOT, "usr", "lib", "systemd", "system", "mios-mcp.service")
_TOML = os.path.join(_ROOT, "usr", "share", "mios", "mios.toml")
_PORT_NAMES = ("MIOS_PORTS_MCP", "MIOS_PORTS_MCP", "MIOS_PORTS_MCP")
_LITERAL = re.compile(r"""^\s*Environment\s*=\s*"?(MIOS_\w*PORT\w*)=(\d+)""", re.MULTILINE)

sys.path.insert(0, os.path.join(_ROOT, "usr", "lib", "mios"))
import mios_toml  # noqa: E402


def load_vendor() -> dict:
    with open(_TOML, "rb") as f:
        return tomllib.load(f)


def render_exports(data: dict) -> dict[str, str]:
    """What the resolver writes to install.env for this vendor layer."""
    return mios_toml.emit_exports(data)


def run_preamble(env: dict[str, str]) -> subprocess.CompletedProcess:
    """Run mcp-server-runner up to its export line, then print the port."""
    with open(_RUNNER, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()
    end = next(i for i, line in enumerate(lines) if line.startswith("export MIOS_AI_ENDPOINT"))
    preamble = "\n".join(lines[: end + 1])
    clean = {k: v for k, v in os.environ.items() if not k.startswith("MIOS_")}
    clean.update(env)
    script = preamble.replace('"$(dirname "${BASH_SOURCE[0]}")', '"' + os.path.dirname(_RUNNER))
    return subprocess.run(["bash", "-c", script + '\necho "PORT=$MIOS_PORTS_MCP"'],
                          env=clean, capture_output=True, text=True, timeout=30)


def unit_port_literals(unit_text: str, unit_table: dict) -> list[str]:
    """Every MIOS_* port name a unit (or its SSOT mirror) assigns a number."""
    found = [f"{name}={val}" for name, val in _LITERAL.findall(unit_text)]
    env = unit_table.get("Environment", [])
    for entry in [env] if isinstance(env, str) else env:
        m = re.match(r'"?(MIOS_\w*PORT\w*)=(\d+)', entry)
        if m:
            found.append(f"[units] {m.group(1)}={m.group(2)}")
    return found


class TestMcpPort(unittest.TestCase):
    def setUp(self) -> None:
        self.data = load_vendor()
        self.exports = render_exports(self.data)
        ports = self.data["ports"]
        self.want = str(int(ports["mcp"]) + int(ports.get("stack_id", 0)) * 10000)

    def test_install_env_supplies_the_port(self) -> None:
        for name in ("MIOS_PORTS_MCP", "MIOS_PORTS_MCP"):
            self.assertEqual(self.exports.get(name), self.want, name)

    def test_runner_resolves_from_install_env_alone(self) -> None:
        r = run_preamble(self.exports)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"PORT={self.want}", r.stdout)

    def test_unit_carries_no_port_literal(self) -> None:
        with open(_UNIT, "r", encoding="utf-8") as f:
            text = f.read()
        self.assertIn("EnvironmentFile=-/etc/mios/install.env", text)
        table = self.data["units"]["mios-mcp.service"]["Service"]
        self.assertEqual(unit_port_literals(text, table), [])

    def test_negative_runner_names_missing_port(self) -> None:
        env = {k: v for k, v in self.exports.items() if k not in _PORT_NAMES}
        r = run_preamble(env)
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("MIOS_PORTS_MCP is unset", r.stderr)
        self.assertNotIn("PORT=", r.stdout)

    def test_negative_planted_literal_is_named(self) -> None:
        with open(_UNIT, "r", encoding="utf-8") as f:
            text = f.read()
        planted = text.replace("EnvironmentFile=-/etc/mios/install.env\n",
                               "EnvironmentFile=-/etc/mios/install.env\nEnvironment=MIOS_PORTS_MCP=8770\n", 1)
        self.assertNotEqual(planted, text, "plant did not apply")
        table = dict(self.data["units"]["mios-mcp.service"]["Service"], Environment="MIOS_PORTS_MCP=8770")
        self.assertEqual(unit_port_literals(planted, table),
                         ["MIOS_PORTS_MCP=8770", "[units] MIOS_PORTS_MCP=8770"])


if __name__ == "__main__":
    unittest.main()
