#!/usr/bin/env python3
# AI-hint: Unit tests for MiOS VPN Kill-Switch and fwmark split-tunnel manager.
# AI-doc: usr/share/doc/mios/manual/tests.md
import unittest
import sys
import os
import re
import shutil
import subprocess
import tempfile
import types
from unittest import mock

try:
    import tomllib
except ImportError:  # py < 3.11
    import tomli as tomllib  # type: ignore

REPO = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SCRIPT = os.path.join(REPO, "usr", "libexec", "mios", "net", "vpn_killswitch.py")
UNIT = os.path.join(REPO, "usr", "lib", "systemd", "system", "mios-vpn-killswitch.service")
VENDOR = os.path.join(REPO, "usr", "share", "mios", "mios.toml")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "usr", "libexec", "mios", "net"))
import vpn_killswitch  # noqa: E402
from vpn_killswitch import VPNKillSwitchManager, DEFAULT_VPN_TABLE, DEFAULT_FWMARK  # noqa: E402
import mios_toml  # noqa: E402 -- vpn_killswitch put usr/lib/mios on sys.path

POSIX = os.name == "posix" and hasattr(os, "getuid")

class TestVPNKillSwitchManager(unittest.TestCase):
    def setUp(self):
        self.mgr = VPNKillSwitchManager(
            vpn_interface="wg0",
            local_cidrs=["10.0.0.0/8", "192.168.1.0/24", "::1/128"],
            dns_server="127.0.0.1",
            dns_port=53,
            fwmark="0x100",
            dry_run=True,
        )

    def test_render_rules_table_and_sets(self):
        rules = self.mgr.render_nftables_rules(vpn_peer_endpoints=["198.51.100.1:51820"])
        self.assertIn(f"table {DEFAULT_VPN_TABLE}", rules)
        self.assertIn("set local_ipv4", rules)
        self.assertIn("10.0.0.0/8, 192.168.1.0/24", rules)
        self.assertIn("set local_ipv6", rules)
        self.assertIn("::1/128", rules)

    def test_render_rules_split_tunnel_fwmark(self):
        rules = self.mgr.render_nftables_rules()
        self.assertIn("chain output_mangle", rules)
        self.assertIn("meta mark set 0x100 accept", rules)
        self.assertIn("chain output_filter", rules)
        self.assertIn("policy drop;", rules)
        self.assertIn("meta mark 0x100 accept", rules)

    def test_render_rules_dns_and_peer_whitelist(self):
        rules = self.mgr.render_nftables_rules(vpn_peer_endpoints=["203.0.113.50:51820"])
        self.assertIn("ip daddr 127.0.0.1 udp dport 53 accept", rules)
        self.assertIn("ip daddr 127.0.0.1 tcp dport 53 accept", rules)
        self.assertIn("ip daddr 203.0.113.50 udp dport 51820 accept", rules)

    def test_dry_run_apply_and_flush(self):
        rules = self.mgr.render_nftables_rules()
        res_apply = self.mgr.apply_rules(rules)
        self.assertEqual(res_apply["status"], "dry_run")
        self.assertFalse(res_apply["applied"])

        res_flush = self.mgr.flush_rules()
        self.assertEqual(res_flush["status"], "dry_run")
        self.assertFalse(res_flush["flushed"])

    def test_render_without_interface_accepts_no_tunnel(self):
        rules = VPNKillSwitchManager(dry_run=True).render_nftables_rules()
        self.assertNotIn("oifname \"None\"", rules)
        self.assertEqual(rules.count("oifname"), 1)  # loopback only

class TestKillSwitchShippedSSOT(unittest.TestCase):
    """The vendor tier, the unit and the script as shipped."""

    @classmethod
    def setUpClass(cls):
        with open(VENDOR, "rb") as fh:
            cls.vendor = tomllib.load(fh)
        with open(UNIT, encoding="utf-8") as fh:
            cls.unit = fh.read()
        with open(SCRIPT, encoding="utf-8") as fh:
            cls.script = fh.read()

    def test_vendor_default_is_false(self):
        vpn = self.vendor["network"]["vpn"]
        self.assertIs(vpn["killswitch"], False)
        self.assertRegex(vpn["interface"], r"^[A-Za-z0-9_.-]{1,15}$")

    def test_operator_tier_may_flip_only_the_toggle(self):
        policy = self.vendor["security"]["operator_tier"]
        self.assertIn("network.vpn.killswitch", policy["toggles"])
        self.assertNotIn("network.vpn.interface", policy["toggles"] + policy["values"])

    def test_unit_gates_on_the_ssot_condition(self):
        directives = [line.strip() for line in self.unit.splitlines() if line.strip() and not line.lstrip().startswith("#")]
        self.assertIn("ExecCondition=/usr/libexec/mios/net/vpn_killswitch.py --condition", directives)
        starts = [d for d in directives if d.startswith("ExecStart=")]
        self.assertEqual(starts, ["ExecStart=/usr/libexec/mios/net/vpn_killswitch.py"])

    def test_no_interface_literal_in_unit_or_script(self):
        self.assertNotRegex(self.unit, r"\bwg0\b")
        self.assertNotRegex(self.script, r"\bwg0\b")

@unittest.skipUnless(POSIX, "the operator-tier read uses POSIX dir fds")
class TestKillSwitchCondition(unittest.TestCase):
    """ExecCondition decisions against temporary vendor / host / operator tiers."""

    @classmethod
    def setUpClass(cls):
        with open(VENDOR, "rb") as fh:
            cls.policy = tomllib.load(fh)["security"]["operator_tier"]

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mios-ks-test-")
        self.vendor = os.path.join(self.tmp, "vendor", "mios.toml")
        self.host = os.path.join(self.tmp, "etc", "mios.toml")
        self.home = os.path.join(self.tmp, "home", "op")
        self.operator = os.path.join(self.home, ".config", "mios", "mios.toml")
        for path in (self.vendor, self.host, self.operator):
            os.makedirs(os.path.dirname(path), exist_ok=True)
        env = {
            "MIOS_VENDOR_TOML": self.vendor,
            "MIOS_VENDOR_TOML_D": os.path.join(self.tmp, "vendor", "mios.d"),
            "MIOS_HOST_TOML": self.host,
            "MIOS_HOST_TOML_D": os.path.join(self.tmp, "etc", "mios.d"),
        }
        self.env = mock.patch.dict(os.environ, env)
        self.env.start()
        for name in ("MIOS_USER_TOML", "MIOS_USER_TOML_D", "MIOS_TOML_ROOT", "MIOS_TOML"):
            os.environ.pop(name, None)
        self.getpw = lambda name: types.SimpleNamespace(pw_dir=self.home, pw_uid=os.getuid())

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_vendor(self, vpn_block='[network.vpn]\nkillswitch = false\ninterface = "wgtest0"\n'):
        toggles = ", ".join(f'"{t}"' for t in self.policy["toggles"])
        values = ", ".join(f'"{v}"' for v in self.policy["values"])
        with open(self.vendor, "w", encoding="utf-8") as fh:
            fh.write('[identity]\nusername = "op"\n\n[security.operator_tier]\n'
                     f"values = [{values}]\ntoggles = [{toggles}]\n\n[network]\nallow_ssh = true\n\n" + vpn_block)

    def write(self, path, text):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def decide(self):
        """The decision root makes: the operator's tier read as a system caller."""
        return vpn_killswitch.condition_exit(
            view=lambda: mios_toml.operator_view(getpw=self.getpw, euid=0, uid_min=1000),
            base=mios_toml.base_view)

    def test_vendor_false_skips(self):
        self.write_vendor()
        self.assertEqual(self.decide(), 1)

    def test_operator_tier_true_arms(self):
        self.write_vendor()
        self.write(self.operator, "[network.vpn]\nkillswitch = true\n")
        self.assertEqual(self.decide(), 0)

    def test_tighten_only(self):
        self.write_vendor()
        self.write(self.host, "[network.vpn]\nkillswitch = true\n")
        self.assertEqual(self.decide(), 0)                      # host true
        self.write(self.operator, "[network.vpn]\nkillswitch = false\n")
        self.assertEqual(self.decide(), 0)                      # host true + operator false still arms
        self.write(self.host, "[network.vpn]\nkillswitch = false\n")
        self.write(self.operator, "[network.vpn]\nkillswitch = true\n")
        self.assertEqual(self.decide(), 0)                      # host false + operator true arms
        self.write(self.operator, "[network.vpn]\nkillswitch = false\n")
        self.assertEqual(self.decide(), 1)                      # both false

    def test_host_true_survives_a_broken_operator_tier(self):
        self.write_vendor()
        self.write(self.host, "[network.vpn]\nkillswitch = true\n")
        for broken in ("[network.vpn\nkillswitch = false\n", '[network.vpn]\nkillswitch = "false"\n'):
            self.write(self.operator, broken)
            self.assertEqual(self.decide(), 0, broken)

    def test_undeclared_table_skips(self):
        self.write_vendor(vpn_block="")
        self.assertEqual(self.decide(), 1)
        # The operator tier cannot create a table the vendor/host tiers do not declare.
        self.write(self.operator, "[network.vpn]\nkillswitch = true\n")
        self.assertEqual(self.decide(), 1)

    def test_string_true_is_an_error_in_every_tier(self):
        self.write_vendor('[network.vpn]\nkillswitch = "true"\ninterface = "wgtest0"\n')
        self.assertEqual(self.decide(), 255)
        self.write_vendor()
        self.write(self.host, '[network.vpn]\nkillswitch = "true"\n')
        self.assertEqual(self.decide(), 255)
        os.remove(self.host)
        self.write(self.operator, '[network.vpn]\nkillswitch = "true"\n')
        self.assertEqual(self.decide(), 255)

    def test_unreadable_tier_is_an_error(self):
        self.write_vendor()
        self.write(self.host, "[network.vpn\nkillswitch = true\n")
        self.assertEqual(self.decide(), 255)

    def test_invalid_interface_is_an_error_when_armed(self):
        self.write_vendor('[network.vpn]\nkillswitch = true\ninterface = "wg0; drop"\n')
        self.assertEqual(self.decide(), 255)

    def test_condition_never_touches_the_firewall(self):
        self.write_vendor()
        self.write(self.operator, "[network.vpn]\nkillswitch = true\n")
        with mock.patch.object(vpn_killswitch.subprocess, "run", side_effect=AssertionError("nft called")) as run:
            self.assertEqual(self.decide(), 0)
            self.write(self.host, "[network.vpn]\nkillswitch = true\n")
            self.assertEqual(self.decide(), 0)
        run.assert_not_called()

    def run_script(self, *args, user_toml=None):
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        if user_toml:
            env["MIOS_USER_TOML"] = user_toml
        return subprocess.run([sys.executable, SCRIPT, *args], env=env, capture_output=True, text=True)

    def test_cli_exit_codes(self):
        self.write_vendor()
        self.assertEqual(self.run_script("--condition", user_toml=self.operator).returncode, 1)
        self.write(self.operator, "[network.vpn]\nkillswitch = true\n")
        self.assertEqual(self.run_script("--condition", user_toml=self.operator).returncode, 0)
        self.write(self.operator, '[network.vpn]\nkillswitch = "true"\n')
        self.assertEqual(self.run_script("--condition", user_toml=self.operator).returncode, 255)

    def test_interface_comes_from_the_ssot(self):
        self.write_vendor()
        res = self.run_script("--render-only")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn('oifname "wgtest0" accept', res.stdout)
        self.write_vendor(vpn_block="")
        res = self.run_script("--render-only")
        self.assertEqual(res.returncode, 1)
        self.assertNotIn("table inet", res.stdout)

if __name__ == "__main__":
    unittest.main()
