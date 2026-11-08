#!/usr/bin/env python3
# AI-hint: Unit suite for the systemd-resolved drop-in policy: host DNS stays on the discovered links, no DNS=/FallbackDNS= entry ever targets resolved's own mDNS port, and the bake (30-dns-config.sh) ships the tracked drop-ins unchanged.
# AI-doc: usr/share/doc/mios/manual/ch28-dynamic-network-and-firewall-management.md
# AI-related: automation/30-dns-config.sh, usr/lib/systemd/resolved.conf.d/10-adguard.conf, etc/systemd/resolved.conf.d/10-adguard.conf
from __future__ import annotations

import configparser
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_REL_USR = os.path.join("usr", "lib", "systemd", "resolved.conf.d", "10-adguard.conf")
_REL_ETC = os.path.join("etc", "systemd", "resolved.conf.d", "10-adguard.conf")
_USR_CONF = os.path.join(_ROOT, _REL_USR)
_ETC_CONF = os.path.join(_ROOT, _REL_ETC)
_SCRIPT = os.path.join(_ROOT, "automation", "30-dns-config.sh")

# RFC 6762. The drop-in sets MulticastDNS=yes, so resolved itself listens here: a unicast
# DNS= aimed at it loops every query into the local mDNS responder (8e9c3a65f).
MDNS_PORT = 5353
_SERVER_LINE = re.compile(r"^\s*(DNS|FallbackDNS)\s*=(.*)$")


def dns_entry_port(entry: str) -> int:
    """The port of one resolved.conf server entry: ADDR[:PORT][%IFACE][#NAME], an IPv6 port as [ADDR]:PORT."""
    e = entry.split("#", 1)[0].split("%", 1)[0]
    if e.startswith("["):
        _host, sep, port = e.rpartition("]:")
        return int(port) if sep else 53
    if e.count(":") == 1:
        return int(e.rsplit(":", 1)[1])
    return 53


def server_entries(text: str) -> list[tuple[str, str]]:
    """Every (directive, entry) a DNS= or FallbackDNS= line names, repeated lines included."""
    found = []
    for line in text.splitlines():
        m = _SERVER_LINE.match(line)
        if m:
            found.extend((m.group(1), e) for e in m.group(2).split())
    return found


def mdns_targets(text: str) -> list[str]:
    return [f"{d}={e}" for d, e in server_entries(text) if dns_entry_port(e) == MDNS_PORT]


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class TestAdguardDNS(unittest.TestCase):
    """The tracked no-unicast-DNS-to-mDNS policy, in the tree and in what the bake installs."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="test-adguard-dns-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _bake(self, ctx: str, dest: str) -> subprocess.CompletedProcess:
        """Run the phase against ctx into dest, every SSOT layer but the tree's vendor file absent."""
        none = os.path.join(self.tmp, "none")
        env = {
            "HOME": os.path.join(self.tmp, "home"), "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "LANG": "C.UTF-8", "CTX": ctx,
            "MIOS_VENDOR_TOML": os.path.join(_ROOT, "usr", "share", "mios", "mios.toml"),
            "MIOS_HOST_TOML": os.path.join(none, "h.toml"), "MIOS_HOST_TOML_D": os.path.join(none, "h.d"),
            "MIOS_USER_TOML": os.path.join(none, "u.toml"), "MIOS_USER_TOML_D": os.path.join(none, "u.d"),
            "XDG_CONFIG_HOME": os.path.join(none, "x"),
        }
        return subprocess.run(["bash", _SCRIPT, dest], env=env, capture_output=True, text=True, timeout=120)

    def _ctx(self, name: str, plant: dict[str, str] | None = None) -> str:
        """A build context holding copies of the tracked drop-ins, with `plant` lines appended per path."""
        ctx = os.path.join(self.tmp, name)
        for rel in (_REL_USR, _REL_ETC):
            os.makedirs(os.path.dirname(os.path.join(ctx, rel)), exist_ok=True)
            shutil.copyfile(os.path.join(_ROOT, rel), os.path.join(ctx, rel))
            if plant and rel in plant:
                with open(os.path.join(ctx, rel), "a", encoding="utf-8") as fh:
                    fh.write(plant[rel] + "\n")
        return ctx

    def test_files_exist(self):
        self.assertTrue(os.path.isfile(_USR_CONF), f"Missing {_USR_CONF}")
        self.assertTrue(os.path.isfile(_ETC_CONF), f"Missing {_ETC_CONF}")
        self.assertTrue(os.path.isfile(_SCRIPT), f"Missing {_SCRIPT}")
        self.assertTrue(os.access(_SCRIPT, os.X_OK), f"Not executable: {_SCRIPT}")

    def test_resolved_config_directives(self):
        for path in (_USR_CONF, _ETC_CONF):
            parser = configparser.ConfigParser(strict=False)
            parser.read(path)
            self.assertIn("Resolve", parser.sections(), f"Missing [Resolve] section in {path}")
            self.assertEqual(parser["Resolve"].get("MulticastDNS"), "yes",
                             f"{path}: resolved owns the mDNS port only with MulticastDNS=yes")
            self.assertEqual(mdns_targets(_read(path)), [],
                             f"{path} sends unicast DNS into the local mDNS listener on :{MDNS_PORT}")
        self.assertEqual(_read(_USR_CONF), _read(_ETC_CONF), "the /usr and /etc drop-ins must carry one policy")

    def test_entry_port_parser(self):
        for entry in ("127.0.0.1:5353", "[::1]:5353", "127.0.0.1:5353%lo#mdns.local", "[fe80::1]:5353%eth0#x"):
            self.assertEqual(dns_entry_port(entry), MDNS_PORT, entry)
        for entry in ("127.0.0.1", "::1", "2001:db8::5353", "[2001:db8::5353]:53", "1.1.1.1#cloudflare-dns.com"):
            self.assertEqual(dns_entry_port(entry), 53, entry)
        self.assertEqual(mdns_targets("[Resolve]\nDNS=1.1.1.1\nDNS=127.0.0.1:5353 9.9.9.9\nFallbackDNS=[::1]:5353\n"),
                         ["DNS=127.0.0.1:5353", "FallbackDNS=[::1]:5353"])

    def test_bake_installs_the_tracked_dropins(self):
        dest = os.path.join(self.tmp, "root")
        res = self._bake(_ROOT, dest)
        self.assertEqual(res.returncode, 0, f"30-dns-config.sh failed: {res.stdout}{res.stderr}")
        for rel in (_REL_USR, _REL_ETC):
            out = os.path.join(dest, rel)
            self.assertTrue(os.path.isfile(out), f"the bake did not install {rel}")
            self.assertEqual(stat.S_IMODE(os.stat(out).st_mode), 0o644, rel)
            self.assertEqual(_read(out), _read(os.path.join(_ROOT, rel)),
                             f"the bake rewrote {rel} instead of shipping the tracked file")
            self.assertEqual(mdns_targets(_read(out)), [], f"the bake re-added unicast DNS to :{MDNS_PORT} in {rel}")

    def test_bake_refuses_an_mdns_upstream(self):
        res = self._bake(self._ctx("ctx-clean"), os.path.join(self.tmp, "root-clean"))
        self.assertEqual(res.returncode, 0, f"an unplanted context must bake: {res.stdout}{res.stderr}")
        precise = self._ctx("ctx-v6", {_REL_USR: "DNS=[2001:db8::5353]:53 2001:db8::5353"})
        res = self._bake(precise, os.path.join(self.tmp, "root-v6"))
        self.assertEqual(res.returncode, 0, f"an IPv6 group 5353 is not the mDNS port: {res.stdout}{res.stderr}")
        plants = (
            {_REL_USR: "DNS=127.0.0.1:5353"},
            {_REL_USR: "DNS=1.1.1.1 [::1]:5353"},
            {_REL_ETC: "FallbackDNS=127.0.0.1:5353%lo#mdns.local"},
        )
        for i, plant in enumerate(plants):
            with self.subTest(plant=plant):
                dest = os.path.join(self.tmp, f"root-bad{i}")
                res = self._bake(self._ctx(f"ctx-bad{i}", plant), dest)
                self.assertNotEqual(res.returncode, 0, f"the bake shipped {plant}")
                self.assertIn("mDNS listener", res.stdout + res.stderr)
                (rel,) = plant
                self.assertFalse(os.path.exists(os.path.join(dest, rel)), f"the bake installed {rel} before refusing")

    def test_script_syntax_and_execution(self):
        res = subprocess.run(["bash", "-n", _SCRIPT], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"Syntax error in {_SCRIPT}: {res.stderr}")


def main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(TestAdguardDNS)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
