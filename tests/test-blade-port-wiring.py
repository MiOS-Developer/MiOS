#!/usr/bin/env python3
# AI-hint: Asserts the cluster/remote daemons bind the SSOT [ports] value instead of their upstream defaults: k3s API (Quadlet and native unit) on [ports].k3s_api, guacd on [ports].guacd with mios-guacamole pointed at it, matchbox on [ports].pxe_hub_api. Each case is checked against the rendered unit the image ships.
# AI-related: usr/share/mios/mios.toml, usr/share/containers/systemd/mios-k3s.container, usr/lib/systemd/system/k3s.service, usr/share/containers/systemd/mios-guacd.container, usr/share/containers/systemd/mios-guacamole.container, usr/share/containers/systemd/mios-pxe-hub.container
# AI-doc: usr/share/doc/mios/manual/tests.md
import re
import sys
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QUADLETS = ROOT / "usr/share/containers/systemd"
UNITS = ROOT / "usr/lib/systemd/system"


def directive(text: str, key: str) -> list[str]:
    return [m.group(1).strip() for m in re.finditer(rf"^{key}=(.*)$", text, re.M)]


class BladePortWiring(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(ROOT / "usr/share/mios/mios.toml", "rb") as f:
            cls.ports = tomllib.load(f)["ports"]

    def quadlet_exec(self, name: str) -> str:
        execs = directive((QUADLETS / name).read_text(encoding="utf-8"), "Exec")
        self.assertEqual(len(execs), 1, f"{name}: expected one Exec=")
        return execs[0]

    def test_k3s_quadlet_listens_on_ssot_port(self):
        e = self.quadlet_exec("mios-k3s.container")
        self.assertIn(f"--https-listen-port={self.ports['k3s_api']}", e.split())

    def test_k3s_native_unit_listens_on_ssot_port(self):
        text = (UNITS / "k3s.service").read_text(encoding="utf-8")
        start = directive(text, "ExecStart")
        self.assertEqual(len(start), 1)
        self.assertIn("--https-listen-port=${MIOS_PORTS_K3S_API}", start[0].split())
        # The variable must come from the resolver's bare env file, not stay unset.
        self.assertIn("-/etc/mios/install.env", directive(text, "EnvironmentFile"))

    def test_guacd_and_its_client_use_ssot_port(self):
        argv = self.quadlet_exec("mios-guacd.container").split()
        self.assertEqual(argv[argv.index("-l") + 1], str(self.ports["guacd"]))
        env = directive(
            (QUADLETS / "mios-guacamole.container").read_text(encoding="utf-8"), "Environment"
        )
        self.assertIn(f"GUACD_PORT={self.ports['guacd']}", env)

    def test_matchbox_uses_ssot_port(self):
        argv = self.quadlet_exec("mios-pxe-hub.container").split()
        address = argv[argv.index("-address") + 1]
        self.assertEqual(address.rsplit(":", 1)[1], str(self.ports["pxe_hub_api"]))


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False, verbosity=2).result.wasSuccessful() else 1)
