#!/usr/bin/env python3
# AI-hint: Automated unit test suite for USBGuard declarative authorization and udisks2 read-only mount policies (task_742).
# AI-doc: usr/share/doc/mios/manual/ch17-defense-in-depth-hardening.md
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_RULES_FILE = os.path.join(_ROOT, "etc", "usbguard", "rules.conf")
_UDISKS_FILE = os.path.join(_ROOT, "etc", "udisks2", "mount_options.conf")
_USBGUARD_BIN = os.path.join(_ROOT, "usr", "libexec", "mios", "mios-usbguard")

# usbguard-rules.conf(5) attributes. The daemon's rule parser raises
# "<attribute> attribute already defined" on a repeat, and one bad rule stops
# the whole rule set from loading. `name` compares strings with ==, so a `*`
# there matches only a device literally named with an asterisk.
_ATTRS = ("id", "hash", "parent-hash", "name", "serial", "via-port",
          "with-interface", "with-connect-type", "label", "if")


def _unloadable_rules(text: str) -> list[str]:
    """Rules the USBGuard daemon would refuse or could never match."""
    bad = []
    for n, line in enumerate(text.splitlines(), 1):
        rule = line.split("#", 1)[0].strip()
        if not rule:
            continue
        for value in re.findall(r'\bname\s+"([^"]*)"', rule):
            if "*" in value:
                bad.append(f"line {n}: name \"{value}\" is matched literally, not as a glob")
        bare = re.sub(r"\{[^}]*\}", "{}", re.sub(r'"[^"]*"', '""', rule)).split()
        if bare[0] not in ("allow", "block", "reject"):
            bad.append(f"line {n}: target {bare[0]!r} is not allow, block or reject")
        for attr in _ATTRS:
            if bare.count(attr) > 1:
                bad.append(f"line {n}: {attr} attribute already defined")
    return bad


class TestUSBGuardSec(unittest.TestCase):
    """Validates USBGuard rules, udisks2 mount options, and BadUSB evaluation logic."""

    def test_rules_file(self):
        self.assertTrue(os.path.isfile(_RULES_FILE), f"Missing {_RULES_FILE}")
        with open(_RULES_FILE, "r") as f:
            content = f.read()
        self.assertIn("09:*:*", content, "Must allow USB hubs")
        self.assertEqual(_unloadable_rules(content), [])

    def test_unloadable_rule_is_named(self):
        # The line this test used to REQUIRE: the daemon refuses it, so the
        # whole rule set never loads.
        bad = "block with-interface equals { 03:*:* } with-interface equals { 08:*:* }\n"
        found = _unloadable_rules(bad)
        self.assertEqual(len(found), 1)
        self.assertIn("with-interface", found[0])
        self.assertEqual(len(_unloadable_rules('allow name "Probe*"\n')), 1)

    def test_udisks_mount_options(self):
        self.assertTrue(os.path.isfile(_UDISKS_FILE), f"Missing {_UDISKS_FILE}")
        with open(_UDISKS_FILE, "r") as f:
            content = f.read()
        self.assertIn("defaults=ro,nosuid,nodev,noexec", content)

    def test_usbguard_cli_badusb_detection(self):
        self.assertTrue(os.path.isfile(_USBGUARD_BIN), f"Missing {_USBGUARD_BIN}")
        self.assertTrue(os.access(_USBGUARD_BIN, os.X_OK), f"Not executable {_USBGUARD_BIN}")

        # Test compound BadUSB (HID + Mass Storage)
        res = subprocess.run(
            [
                sys.executable,
                _USBGUARD_BIN,
                "--evaluate",
                "--vendor", "046d",
                "--product", "c52b",
                "--interfaces", "03,08",
                "--json",
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 1)
        data = json.loads(res.stdout)
        self.assertEqual(data.get("verdict"), "block")
        self.assertTrue(data.get("is_badusb"))

        # Test authorized token
        res_token = subprocess.run(
            [
                sys.executable,
                _USBGUARD_BIN,
                "--evaluate",
                "--vendor", "1050",
                "--product", "0407",
                "--interfaces", "03,0b",
                "--json",
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res_token.returncode, 0)
        data_token = json.loads(res_token.stdout)
        self.assertEqual(data_token.get("verdict"), "allow")
        self.assertFalse(data_token.get("is_badusb"))


def main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(TestUSBGuardSec)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
