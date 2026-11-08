#!/usr/bin/env python3
# AI-hint: Automated unit test suite for declarative nftables inter-container firewall rule generator (task_423).
# AI-doc: usr/share/doc/mios/manual/ch28-dynamic-network-and-firewall-management.md
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_FW_BIN = os.path.join(_ROOT, "usr", "libexec", "mios", "mios-firewall-isolate")
_SERVICE_FILE = os.path.join(_ROOT, "usr", "lib", "systemd", "system", "mios-firewall.service")
_TOML_PATH = os.path.join(_ROOT, "usr", "share", "mios", "mios.toml")
_BAKE = os.path.join(_ROOT, "automation", "45-firewall.sh")
_LIVE_INIT = "/usr/libexec/mios-firewall-init"
_REQUIRE_TOOLS = os.environ.get("MIOS_DRIFT_REQUIRE_TOOLS") == "1"
_MATRIX_RULE = 'destination = "searxng", port_key = "searxng"'
_SWALLOW = "--apply --dry-run 2>/dev/null || true"


class TestNftablesFirewallIsolate(unittest.TestCase):
    """Validates declarative nftables inter-container firewall rule generator."""

    def test_binary_executable(self):
        self.assertTrue(os.path.isfile(_FW_BIN), f"Missing {_FW_BIN}")
        self.assertTrue(os.access(_FW_BIN, os.X_OK), f"Not executable: {_FW_BIN}")

    def test_service_unit_exists(self):
        self.assertTrue(os.path.isfile(_SERVICE_FILE), f"Missing {_SERVICE_FILE}")
        with open(_SERVICE_FILE, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("mios-firewall-isolate", content)
        self.assertIn("WantedBy=multi-user.target", content)

    def test_ruleset_generation_and_policy(self):
        # A port_key rule resolves through the layered SSOT: pin every layer to
        # this tree's vendor file and empty host/user layers, so no live /etc
        # layer is read.
        with tempfile.TemporaryDirectory(prefix="mios-fw-isolate-") as tmp:
            empty, frags = os.path.join(tmp, "empty.toml"), os.path.join(tmp, "mios.d")
            open(empty, "w", encoding="utf-8").close()
            os.mkdir(frags)
            env = {
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8",
                "HOME": tmp, "XDG_CONFIG_HOME": tmp,
                "MIOS_VENDOR_TOML": _TOML_PATH, "MIOS_VENDOR_TOML_D": frags,
                "MIOS_HOST_TOML": empty, "MIOS_HOST_TOML_D": frags,
                "MIOS_USER_TOML": empty, "MIOS_USER_TOML_D": frags,
                "MIOS_RESOLVER_NATIVE": "0", "MIOS_DB_AUTHORITATIVE": "0",
            }
            res = subprocess.run(
                [_FW_BIN, "--toml", _TOML_PATH, "--check", "--json"],
                capture_output=True,
                text=True,
                check=True,
                env=env,
            )
        data = json.loads(res.stdout)
        self.assertEqual(data.get("status"), "valid")
        self.assertEqual(data.get("default_policy"), "drop")
        self.assertGreaterEqual(data.get("rule_count", 0), 5)

        ruleset = data.get("ruleset", "")
        self.assertIn("table inet mios_container_isolate", ruleset)
        self.assertIn("policy drop;", ruleset)
        self.assertIn("ct state established,related accept", ruleset)
        self.assertIn("[mios-fw-drop]", ruleset)
        self.assertIn("tcp dport 8700 accept", ruleset)  # agent_pipe
        self.assertIn("tcp dport 8720 accept", ruleset)  # hermes
        self.assertIn("tcp dport 5432 accept", ruleset)  # pgvector


def _fingerprint(path: str):
    """sha256 and mtime of a live file, or None while it is absent."""
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest(), os.stat(path).st_mtime_ns
    except FileNotFoundError:
        return None


class TestBakeStepExit(unittest.TestCase):
    """automation/45-firewall.sh, run whole in a disposable bwrap sandbox: the
    live root read-only, /usr/libexec (where it writes mios-firewall-init and
    finds the isolate) and /etc/mios private tmpfs, no network, and
    firewall-cmd/systemctl/nft recording stubs first on PATH. The isolate is
    the shipped one over a fixture SSOT; the result is the script's own exit."""

    def setUp(self):
        self.bwrap = shutil.which("bwrap")
        if not self.bwrap:
            if _REQUIRE_TOOLS:
                self.fail("bwrap is required (MIOS_DRIFT_REQUIRE_TOOLS=1) to run 45-firewall.sh off the live root")
            raise unittest.SkipTest("bwrap is not installed")
        self.tmp = tempfile.mkdtemp(prefix="mios-fw-bake-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        live = _fingerprint(_LIVE_INIT)
        self.addCleanup(lambda: self.assertEqual(live, _fingerprint(_LIVE_INIT), _LIVE_INIT))
        with open(_TOML_PATH, encoding="utf-8") as fh:
            self.vendor = fh.read()
        self.assertEqual(1, self.vendor.count(_MATRIX_RULE))
        with open(_BAKE, encoding="utf-8") as fh:
            self.script = fh.read()

    def bake(self, vendor: str, isolate: bool = True, script: str | None = None):
        fx = tempfile.mkdtemp(dir=self.tmp)
        for d in ("bin", "home", "mios.d"):
            os.mkdir(os.path.join(fx, d))
        for name, text in (("vendor.toml", vendor), ("empty.toml", "")):
            with open(os.path.join(fx, name), "w", encoding="utf-8") as fh:
                fh.write(text)
        for stub in ("firewall-cmd", "systemctl", "nft"):
            self._exe(os.path.join(fx, "bin", stub), f'#!/bin/sh\necho "{stub} $*" >> /tmp/fx/calls\nexit 0\n')
        cmd = [self.bwrap, "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp",
               "--tmpfs", "/usr/libexec", "--unshare-all", "--die-with-parent", "--new-session"]
        for hidden in ("/etc/mios", "/usr/share/mios"):
            if os.path.isdir(hidden):
                cmd += ["--tmpfs", hidden]
        cmd += ["--bind", fx, "/tmp/fx"]
        if isolate:
            # The shipped isolate at its image path; --toml stands in for the
            # image's vendor file, the env below for its layers.
            self._exe(os.path.join(fx, "isolate"),
                      f'#!/bin/sh\nexec "{_FW_BIN}" --toml /tmp/fx/vendor.toml "$@"\n')
            cmd += ["--ro-bind", os.path.join(fx, "isolate"), "/usr/libexec/mios/mios-firewall-isolate"]
        if script is not None:
            with open(os.path.join(fx, "45-firewall.sh"), "w", encoding="utf-8") as fh:
                fh.write(script)
            cmd += ["--ro-bind", os.path.join(fx, "45-firewall.sh"), _BAKE]
        cmd += ["--chdir", _ROOT, "--", "bash", "-c",
                'bash "$1"; rc=$?; [ -s /usr/libexec/mios-firewall-init ] && echo FIXTURE-INIT-WRITTEN; exit "$rc"',
                "bake", _BAKE]
        env = {"PATH": "/tmp/fx/bin:/usr/bin:/bin", "LANG": "C.UTF-8", "HOME": "/tmp/fx/home",
               "XDG_CONFIG_HOME": "/tmp/fx/home", "MIOS_VENDOR_TOML": "/tmp/fx/vendor.toml",
               "MIOS_VENDOR_TOML_D": "/tmp/fx/mios.d", "MIOS_HOST_TOML": "/tmp/fx/empty.toml",
               "MIOS_HOST_TOML_D": "/tmp/fx/mios.d", "MIOS_USER_TOML": "/tmp/fx/empty.toml",
               "MIOS_USER_TOML_D": "/tmp/fx/mios.d", "MIOS_RESOLVER_NATIVE": "0",
               "MIOS_DB_AUTHORITATIVE": "0", "MIOS_MIGRATION_USE_RUST_RESOLVER_SHELL": "0"}
        r = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=300)
        calls = os.path.join(fx, "calls")
        self.assertFalse(os.path.exists(calls), open(calls).read() if os.path.exists(calls) else "")
        self.assertIn("FIXTURE-INIT-WRITTEN", r.stdout, r.stdout + r.stderr)
        return r

    @staticmethod
    def _exe(path: str, text: str):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(path, 0o755)

    def unresolved(self):
        """Fixture SSOTs whose declared matrix port_key does not resolve."""
        undeclared = self.vendor.replace(_MATRIX_RULE, 'destination = "searxng", port_key = "fixture_undeclared"')
        members = 'members = ["searxng", "crawl4ai", "firecrawl"]'
        self.assertEqual(1, self.vendor.count(members))
        lines = self.vendor.replace(members, 'members = ["", "crawl4ai", "firecrawl"]').splitlines(keepends=True)
        flat = [i for i, ln in enumerate(lines) if ln.startswith("searxng ") and "=" in ln and ln.split("=")[1].split()[0].isdigit()]
        self.assertEqual(1, len(flat))
        del lines[flat[0]]
        return (("fixture_undeclared", undeclared), ("searxng", "".join(lines)))

    def test_without_an_isolate_the_step_passes(self):
        r = self.bake(self.vendor, isolate=False)
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)
        self.assertNotIn("mios-firewall-isolate", r.stdout + r.stderr)

    def test_a_valid_matrix_passes(self):
        r = self.bake(self.vendor)
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)
        self.assertIn("Apply Status: simulated", r.stdout)

    def test_an_unresolved_port_key_fails_the_bake(self):
        for key, vendor in self.unresolved():
            with self.subTest(port_key=key):
                r = self.bake(vendor)
                self.assertEqual(78, r.returncode, r.stdout + r.stderr)
                self.assertIn(f"rule hermes->searxng: port_key {key!r}", r.stderr)

    def test_the_fixture_tells_a_swallowed_exit_apart(self):
        # Control: the swallowing form this step used to have passes the same
        # broken matrix, and hides the key.
        self.assertNotIn(_SWALLOW, self.script)
        planted = self.script.replace("--apply --dry-run || exit $?", _SWALLOW)
        self.assertEqual(1, planted.count(_SWALLOW))
        for key, vendor in self.unresolved():
            with self.subTest(port_key=key):
                r = self.bake(vendor, script=planted)
                self.assertEqual(0, r.returncode, r.stdout + r.stderr)
                self.assertNotIn(f"port_key {key!r}", r.stderr)


def main() -> int:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite([loader.loadTestsFromTestCase(TestNftablesFirewallIsolate),
                                loader.loadTestsFromTestCase(TestBakeStepExit)])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
