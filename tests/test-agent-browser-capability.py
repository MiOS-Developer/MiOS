#!/usr/bin/env python3
# AI-hint: Asserts the agent browser stays off a Blade unless it has the agent-browser capability: only the desktop archetype grants it, the worker browser requires it in [blade.requires] (its generated drop-in conditions on the marker), the primary stays seat_side with `mios-hermes-browser allowed` as its ExecCondition in the SSOT and the rendered unit, and mios-cdp-fetch reports a disabled host at once instead of polling a port nothing will open.
# AI-related: usr/share/mios/mios.toml, usr/share/mios/dropins/blade-agent-browser.conf, usr/libexec/mios/mios-hermes-browser, usr/libexec/mios/mios-cdp-fetch
# AI-doc: usr/share/doc/mios/manual/tests.md
import importlib.machinery
import importlib.util
import io
import sys
import tomllib
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
CAP = "agent-browser"


def ssot() -> dict:
    with open(ROOT / "usr/share/mios/mios.toml", "rb") as f:
        return tomllib.load(f)


class AgentBrowserCapability(unittest.TestCase):
    def test_only_the_desktop_archetype_grants_it(self):
        arche = ssot()["blade"]["archetypes"]
        self.assertIn(CAP, arche["desktop"])
        others = sorted(n for n, caps in arche.items() if n != "desktop" and CAP in caps)
        self.assertEqual(others, [], "a non-desktop archetype grants the agent browser")

    def test_the_worker_browser_requires_it_and_the_primary_asks_the_launcher(self):
        data = ssot()
        blade = data["blade"]
        self.assertIn(CAP, blade["requires"]["mios-hermes-browser-worker"])
        # The primary is the seat's browser: a capability a seat lacks cannot gate it.
        self.assertIn("mios-hermes-browser", blade["seat_side"])
        self.assertNotIn("mios-hermes-browser", blade["requires"])
        gate = "/usr/libexec/mios/mios-hermes-browser allowed"
        svc = data["units"]["mios-hermes-browser.service"]["Service"]
        self.assertEqual(svc.get("ExecCondition"), gate)
        unit = (ROOT / "usr/lib/systemd/system/mios-hermes-browser.service").read_text()
        self.assertIn(f"\nExecCondition={gate}\n", unit)

    def test_generated_dropin_conditions_on_the_marker(self):
        text = (ROOT / "usr/share/mios/dropins/blade-agent-browser.conf").read_text()
        self.assertIn(f"ConditionPathExists=/etc/mios/blade.d/{CAP}\n", text)

    def test_cdp_fetch_stops_at_once_on_a_disabled_host(self):
        sys.modules.setdefault("websockets", types.ModuleType("websockets"))
        path = str(ROOT / "usr/libexec/mios/mios-cdp-fetch")
        loader = importlib.machinery.SourceFileLoader("mios_cdp_fetch", path)
        spec = importlib.util.spec_from_loader("mios_cdp_fetch", loader)
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
        refused = mock.Mock(returncode=3, stderr="agent browser disabled on this host\n")
        with (
            mock.patch.object(mod, "_http", side_effect=OSError("no CDP")),
            mock.patch.object(mod.subprocess, "run", return_value=refused),
            mock.patch.object(mod.time, "sleep") as sleep,
            mock.patch.object(mod.sys, "stderr", new_callable=io.StringIO) as err,
        ):
            self.assertFalse(mod._ensure())
        sleep.assert_not_called()
        self.assertIn("agent browser disabled", err.getvalue())


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False, verbosity=2).result.wasSuccessful() else 1)
