#!/usr/bin/env python3
# AI-hint: Two-sided verification test suite for MiOS Decentralized RDE Platform Engine (DevPod + OpenTofu + MIT IDE).
# AI-doc: usr/share/doc/mios/manual/rde.md
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_SSOT_PATH = os.path.join(_ROOT, "usr", "share", "mios", "mios.toml")
_DEVCONTAINER_PATH = os.path.join(_ROOT, ".devcontainer", "devcontainer.json")
_USR_BIN_MIOS = os.path.join(_ROOT, "usr", "bin", "mios")
_MIOS_RS_DIR = os.path.join(_ROOT, "src", "mios-rs")


def _find_miosd_binary() -> str:
    """Finds or builds the miosd executable for testing. MIOS_MIOSD_BIN names a
    candidate build; when it is set, that binary is the one under test."""
    override = os.environ.get("MIOS_MIOSD_BIN")
    if override:
        if not os.access(override, os.X_OK):
            raise AssertionError(f"MIOS_MIOSD_BIN={override} is not an executable miosd")
        return override
    candidates = [
        os.path.join(_MIOS_RS_DIR, "target", "debug", "miosd.exe"),
        os.path.join(_MIOS_RS_DIR, "target", "debug", "miosd"),
        os.path.join(_ROOT, "usr", "libexec", "mios", "miosd"),
        shutil.which("miosd") or "",
    ]
    for c in candidates:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    # If not found, try building with cargo
    try:
        subprocess.run(["cargo", "build", "-p", "miosd"], cwd=_MIOS_RS_DIR, check=True, capture_output=True)
    except Exception:
        pass
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return ""


def _ssot_tree() -> dict:
    with open(_SSOT_PATH, "rb") as fh:
        return tomllib.load(fh)


def _ssot_ports() -> dict:
    return _ssot_tree()["ports"]


def _agent_overlay_text(port) -> str:
    """A tier that moves [ports].devpod_agent to `port` (TOML value text). [ports]
    is re-derived from [ports.categories] after the merge, so an overlay moves a
    category member through its category."""
    ssot = _ssot_tree()
    category = next((name for name, cat in ssot["ports"].get("categories", {}).items()
                     if "devpod_agent" in (cat.get("members") or [])), None)
    return (f"[ports.categories.{category}]\npinned = {{ devpod_agent = {port} }}\n"
            if category else f"[ports]\ndevpod_agent = {port}\n")


def _flat_overlay_text(value) -> str:
    """A tier that writes the flat [ports].devpod_agent; derivation writes over it."""
    return f"[ports]\ndevpod_agent = {value}\n"


# A devpod_agent value of each non-integer TOML type, as written and as named.
_NOT_INTEGERS = ('"abc"', '"20000"', "20000.0", "1.5", "true", "[20000]", "{ port = 20000 }",
                 "1979-05-27")


def _spare_ports(taken, count=2) -> list:
    """`count` distinct valid TCP ports outside `taken`, to move the agent to."""
    return [p for p in range(20000, 65536, 1111) if p not in taken][:count]


def _devcontainer_forwards() -> dict:
    """forwardPorts of every devcontainer.json under .devcontainer/, by path."""
    found = {}
    for dirpath, _dirs, files in os.walk(os.path.join(_ROOT, ".devcontainer")):
        if "devcontainer.json" in files:
            path = os.path.join(dirpath, "devcontainer.json")
            with open(path, "r", encoding="utf-8") as fh:
                found[os.path.relpath(path, _ROOT)] = json.load(fh).get("forwardPorts", [])
    return found


class TestRdeEngine(unittest.TestCase):
    """Two-sided test suite for MiOS RDE Orchestration Engine, Copyleft Taint Gating, and SSOT Conformance."""

    @classmethod
    def setUpClass(cls):
        cls.miosd_bin = _find_miosd_binary()
        if not cls.miosd_bin:
            raise unittest.SkipTest("miosd binary not found and cargo build unavailable")

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="mios_rde_test_")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _rde(self, *args, host_toml=None, user_toml=None):
        """miosd rde over this tree's vendor SSOT and nothing else: no host or
        user tier unless the test supplies one, and no inherited MIOS_* pointer."""
        none = os.path.join(self.temp_dir, "none")
        env = {k: v for k, v in os.environ.items() if not k.startswith("MIOS_")}
        env.update(MIOS_HOST_TOML=host_toml or os.path.join(none, "host.toml"),
                   MIOS_HOST_TOML_D=os.path.join(none, "host.d"),
                   MIOS_USER_TOML=user_toml or os.path.join(none, "user.toml"),
                   MIOS_USER_TOML_D=os.path.join(none, "user.d"),
                   XDG_CONFIG_HOME=os.path.join(none, "xdg"))
        return subprocess.run([self.miosd_bin, "rde", "--root", _ROOT, *args],
                              capture_output=True, text=True, env=env)

    def _rde_json(self, *args, host_toml=None, user_toml=None):
        res = self._rde(*args, "--json", host_toml=host_toml, user_toml=user_toml)
        self.assertEqual(res.returncode, 0, f"rde {' '.join(args)} failed: {res.stderr}")
        return json.loads(res.stdout)

    def _workspace(self):
        dc_dir = os.path.join(self.temp_dir, ".devcontainer")
        os.makedirs(dc_dir, exist_ok=True)
        with open(os.path.join(dc_dir, "devcontainer.json"), "w", encoding="utf-8") as f:
            json.dump({"name": "Test Workspace"}, f)
        return self.temp_dir

    def _assert_forwarded(self, agent, host_toml=None, user_toml=None):
        """rde's forward contract for agent port `agent`: every surface that names
        the agent port -- status JSON and text, the up plan JSON and its text --
        names `agent`, the port is forwarded exactly once, the devpod command
        opens every forwarded port, and the forwards are every devcontainer
        projection's forwardPorts plus the agent transport, each once."""
        tiers = dict(host_toml=host_toml, user_toml=user_toml)
        status = self._rde_json("status", **tiers)
        self.assertEqual(status.get("devpod_agent_port"), agent)
        self.assertEqual(status["forward_ports"].count(agent), 1, status["forward_ports"])
        self.assertEqual(len(set(status["forward_ports"])), len(status["forward_ports"]),
                         f"a port is forwarded twice: {status['forward_ports']}")
        plan = self._rde_json("up", self._workspace(), "--dry-run", **tiers)
        self.assertEqual(plan.get("devpod_agent_port"), agent)
        self.assertEqual(plan.get("forward_ports"), status["forward_ports"])
        opened = [plan["command"][i + 1] for i, a in enumerate(plan["command"]) if a == "--open"]
        self.assertEqual(opened, [f"localhost:{p}" for p in plan["forward_ports"]])
        # The human-readable surfaces print the same resolved port, never a literal.
        text = self._rde("status", **tiers)
        self.assertEqual(text.returncode, 0, text.stderr)
        self.assertIn(f"Agent Port:   {agent} (forwarded)", text.stdout)
        text = self._rde("up", self._workspace(), "--dry-run", **tiers)
        self.assertEqual(text.returncode, 0, text.stderr)
        self.assertIn(f"Forwarded Ports:  {plan['forward_ports']} (devpod_agent = {agent})",
                      text.stdout)
        # Devcontainer parity: whatever the agent port is, every unrelated port a
        # devcontainer forwards is still forwarded, in order, and nothing else is.
        devcontainers = _devcontainer_forwards()
        self.assertTrue(devcontainers, "no devcontainer.json to compare with proves nothing")
        for path, forwards in devcontainers.items():
            self.assertEqual([p for p in plan["forward_ports"] if p != agent],
                             [p for p in forwards if p != agent],
                             f"rde forwards {plan['forward_ports']} for agent {agent}; {path} forwards {forwards}")
        return status

    def _tier(self, name, port):
        """Write a tier file that moves the agent port to `port`; returns its path."""
        return self._write(name, _agent_overlay_text(port))

    def _write(self, name, text):
        path = os.path.join(self.temp_dir, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def _assert_refused(self, shown, host_toml=None, user_toml=None):
        """Every rde verb exits 1 with the error naming `shown`, and prints no
        plan or status."""
        ws = self._workspace()
        for args in (("status", "--json"), ("status",), ("up", ws, "--dry-run", "--json"),
                     ("up", ws, "--dry-run"), ("down", ws, "--dry-run", "--json"),
                     ("audit", "--json")):
            with self.subTest(value=shown, verb=" ".join(args)):
                res = self._rde(*args, host_toml=host_toml, user_toml=user_toml)
                self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
                self.assertIn(f"[ports].devpod_agent = {shown} is not a TCP port", res.stderr)
                self.assertEqual(res.stdout, "", "a refused port must not print a plan or status")

    # -------------------------------------------------------------------------
    # POSITIVE CONTROLS
    # -------------------------------------------------------------------------

    def test_ssot_rde_configuration(self):
        """Positive Control: Verify SSOT defaults and ports in mios.toml."""
        self.assertTrue(os.path.isfile(_SSOT_PATH), f"SSOT missing at {_SSOT_PATH}")
        with open(_SSOT_PATH, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("code_server", content)
        self.assertIn("8900", content)

        # Verify miosd status loads SSOT-derived defaults
        cmd = [self.miosd_bin, "rde", "--root", _ROOT, "status", "--json"]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"rde status failed: {res.stderr}")
        status = json.loads(res.stdout)
        self.assertEqual(status.get("orchestrator"), "devpod")
        self.assertEqual(status.get("engine"), "podman")
        self.assertEqual(status.get("ide"), "code-server")
        self.assertEqual(status.get("devpod_agent_port"), _ssot_ports()["devpod_agent"])

    def test_rde_forward_port_contract(self):
        """Positive Control: miosd rde forwards and reports the SSOT [ports].devpod_agent."""
        ports = _ssot_ports()
        status = self._assert_forwarded(ports["devpod_agent"])
        self.assertIn(ports["code_server"], status["forward_ports"])

    def test_devcontainer_forwards_the_code_server_port(self):
        """Positive Control: .devcontainer/devcontainer.json forwards the SSOT
        [ports].code_server (the code-server workbench), resolved, not a literal."""
        with open(_DEVCONTAINER_PATH, "r", encoding="utf-8") as f:
            forward_ports = json.load(f).get("forwardPorts", [])
        code_server = _ssot_ports()["code_server"]
        self.assertIn(code_server, forward_ports,
                      f"[ports].code_server = {code_server} (code-server) must be in forwardPorts")

    def test_rde_forward_ports_follow_an_ssot_overlay(self):
        """Negative Control: a host overlay that moves [ports].devpod_agent moves
        every surface rde reports it on (status JSON and text, the up plan JSON and
        its text). The moved port REPLACES the vendor port in the vendor port's
        slot of forward_ports -- it is not appended -- so the vendor port is no
        longer forwarded and the moved one is forwarded exactly once."""
        vendor = _ssot_ports()["devpod_agent"]
        vendor_forwarded = self._rde_json("status")["forward_ports"]
        moved = vendor + 10000 if vendor + 10000 <= 65535 else vendor - 1000
        host = self._tier("host.toml", moved)
        status = self._assert_forwarded(moved, host_toml=host)
        self.assertNotIn(vendor, status["forward_ports"], "the vendor agent port must not stay forwarded")
        self.assertEqual(status["forward_ports"],
                         [moved if p == vendor else p for p in vendor_forwarded],
                         "the moved agent port takes the vendor port's slot")
        plan = self._rde_json("up", self._workspace(), "--dry-run", host_toml=host)
        self.assertEqual(plan["devpod_agent_port"], moved)
        self.assertIn(f"localhost:{moved}", plan["command"])
        self.assertNotIn(f"localhost:{vendor}", plan["command"])
        text = self._rde("up", self._workspace(), "--dry-run", host_toml=host)
        self.assertEqual(text.returncode, 0, text.stderr)
        self.assertIn(f"(devpod_agent = {moved})", text.stdout)
        self.assertNotIn(f"(devpod_agent = {vendor})", text.stdout)

    # The agent-port contract (root ruling F2): the SSOT overlay REPLACES the
    # agent-transport role -- the unselected default is not kept -- a collision
    # is forwarded once, the last tier wins, an integer outside 1..65535 fails
    # closed, and every unrelated forward survives each case.

    def test_dropping_the_agent_overlay_restores_the_vendor_port(self):
        """Negative Control: an overlay moves the agent port; without it the vendor
        port is forwarded in its own slot, the moved port is not, and the forwards
        equal the overlay-free run."""
        vendor = _ssot_ports()["devpod_agent"]
        baseline = self._rde_json("status")["forward_ports"]
        (moved,) = _spare_ports(baseline, 1)
        host = self._tier("host.toml", moved)
        status = self._assert_forwarded(moved, host_toml=host)
        self.assertNotIn(vendor, status["forward_ports"], "the unselected vendor port is not kept")
        os.remove(host)
        status = self._assert_forwarded(vendor, host_toml=host)
        self.assertEqual(status["forward_ports"], baseline)
        self.assertNotIn(moved, status["forward_ports"])

    def test_an_agent_overlay_onto_another_forward_is_forwarded_once(self):
        """Negative Control: an overlay that puts the agent port on a port rde
        already forwards lists that port once; the vendor port is dropped and every
        other forward stays."""
        vendor = _ssot_ports()["devpod_agent"]
        baseline = self._rde_json("status")["forward_ports"]
        others = [p for p in baseline if p != vendor]
        self.assertTrue(others, "no other forward to collide with proves nothing")
        for other in others:
            with self.subTest(other=other):
                status = self._assert_forwarded(other, host_toml=self._tier("host.toml", other))
                self.assertNotIn(vendor, status["forward_ports"])
                self.assertEqual(sorted(status["forward_ports"]), sorted(others))

    def test_host_then_user_agent_overlays_last_tier_wins_once(self):
        """Negative Control: with a host and a user overlay the user tier's port is
        the agent port, forwarded once in the vendor slot; neither the host's nor
        the vendor's port stays forwarded, and the larger port does not win."""
        vendor = _ssot_ports()["devpod_agent"]
        baseline = self._rde_json("status")["forward_ports"]
        low, high = _spare_ports(baseline)
        for host_port, user_port in ((low, high), (high, low)):
            with self.subTest(host=host_port, user=user_port):
                status = self._assert_forwarded(user_port,
                                                host_toml=self._tier("host.toml", host_port),
                                                user_toml=self._tier("user.toml", user_port))
                self.assertNotIn(host_port, status["forward_ports"])
                self.assertEqual(status["forward_ports"],
                                 [user_port if p == vendor else p for p in baseline])
        # The tiers merge before rde applies the port once, so a host overlay onto
        # another forward that the user tier moves on leaves that forward in place.
        other = next(p for p in baseline if p != vendor)
        status = self._assert_forwarded(high, host_toml=self._tier("host.toml", other),
                                        user_toml=self._tier("user.toml", high))
        self.assertEqual(status["forward_ports"], [high if p == vendor else p for p in baseline])

    def test_an_agent_overlay_outside_the_tcp_range_fails_closed(self):
        """Negative Control: 0, 65536, -1 and 65536 + vendor (which `as u16` wraps
        onto the vendor port) are refused by every rde verb with an error naming
        the key and value, and nothing is planned or reported."""
        vendor = _ssot_ports()["devpod_agent"]
        ws = self._workspace()
        for bad in (0, 65536, -1, 65536 + vendor):
            host = self._tier("host.toml", bad)
            for args in (("status", "--json"), ("status",), ("up", ws, "--dry-run", "--json"),
                         ("up", ws, "--dry-run"), ("down", ws, "--dry-run", "--json"),
                         ("audit", "--json")):
                with self.subTest(bad=bad, verb=" ".join(args)):
                    res = self._rde(*args, host_toml=host)
                    self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
                    self.assertIn(f"[ports].devpod_agent = {bad} is not a TCP port", res.stderr)
                    self.assertEqual(res.stdout, "", "a refused port must not print a plan or status")

    def test_an_agent_overlay_on_the_tcp_range_boundaries_is_accepted(self):
        """Positive Control: ports 1 and 65535 are valid agent ports, forwarded
        once in the vendor slot on every surface."""
        vendor = _ssot_ports()["devpod_agent"]
        baseline = self._rde_json("status")["forward_ports"]
        for port in (1, 65535):
            with self.subTest(port=port):
                status = self._assert_forwarded(port, host_toml=self._tier("host.toml", port))
                self.assertEqual(status["forward_ports"],
                                 [port if p == vendor else p for p in baseline])

    def test_a_non_integer_agent_port_fails_closed(self):
        """Negative Control: a string, numeric string, float, integral float, bool,
        array, table or date as the agent port, pinned or flat, stops every rde
        verb with the error naming the value as written."""
        for value in _NOT_INTEGERS:
            self._assert_refused(value, host_toml=self._tier("host.toml", value))
            self._assert_refused(value, host_toml=self._write("host.toml", _flat_overlay_text(value)))

    def test_a_mistyped_user_pin_over_a_host_pin_fails_closed(self):
        """Negative Control: the host pins a valid port and the user pins "x"; the
        merged pin is "x", so every verb fails and neither port is forwarded."""
        baseline = self._rde_json("status")["forward_ports"]
        (port,) = _spare_ports(baseline, 1)
        self._assert_refused('"x"', host_toml=self._tier("host.toml", port),
                             user_toml=self._tier("user.toml", '"x"'))
        # A mistyped flat value fails though a valid user pin would win.
        self._assert_refused('"x"', host_toml=self._write("host.toml", _flat_overlay_text('"x"')),
                             user_toml=self._tier("user.toml", port))

    def test_a_valid_user_pin_over_a_mistyped_host_pin_is_accepted(self):
        """Positive Control: a valid user pin replaces a mistyped host pin in the
        merge, so the user port is forwarded once in the vendor slot."""
        vendor = _ssot_ports()["devpod_agent"]
        baseline = self._rde_json("status")["forward_ports"]
        (port,) = _spare_ports(baseline, 1)
        status = self._assert_forwarded(port, host_toml=self._tier("host.toml", '"x"'),
                                        user_toml=self._tier("user.toml", port))
        self.assertEqual(status["forward_ports"], [port if p == vendor else p for p in baseline])

    def test_a_flat_agent_port_outside_the_tcp_range_fails_closed(self):
        """Negative Control: a flat [ports].devpod_agent of 0, 65536 or -1 stops
        every verb, although [ports.categories] derives the port over it."""
        for bad in (0, 65536, -1):
            self._assert_refused(str(bad), host_toml=self._write("host.toml", _flat_overlay_text(bad)))

    def test_an_empty_agent_pin_sets_nothing(self):
        """Positive Control: an empty string never overrides a lower tier, so an
        empty user pin leaves the vendor port alone and a host pin in place."""
        vendor = _ssot_ports()["devpod_agent"]
        baseline = self._rde_json("status")["forward_ports"]
        status = self._assert_forwarded(vendor, user_toml=self._tier("user.toml", '""'))
        self.assertEqual(status["forward_ports"], baseline)
        (port,) = _spare_ports(baseline, 1)
        self._assert_forwarded(port, host_toml=self._tier("host.toml", port),
                               user_toml=self._tier("user.toml", '""'))

    def test_rde_audit_zero_taint(self):
        """Positive Control: Verify automated license audit confirms 0 copyleft taint."""
        cmd = [self.miosd_bin, "rde", "--root", _ROOT, "audit", "--json"]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"rde audit failed: {res.stderr}")

        report = json.loads(res.stdout)
        self.assertTrue(report.get("compliant"), "Audit report must be compliant")
        self.assertEqual(report.get("copyleft_violations"), 0, "Violations must be 0")

        # Verify orchestrator is MPL-2.0 DevPod
        orch = report.get("orchestrator", {})
        self.assertEqual(orch.get("name"), "devpod")
        self.assertEqual(orch.get("license"), "MPL-2.0")
        self.assertFalse(orch.get("copyleft_taint"))

        # Verify infrastructure is MPL-2.0 OpenTofu
        infra = report.get("infrastructure", {})
        self.assertEqual(infra.get("name"), "OpenTofu")
        self.assertEqual(infra.get("license"), "MPL-2.0")
        self.assertFalse(infra.get("copyleft_taint"))

        # Verify IDE is MIT code-server
        ide = report.get("ide", {})
        self.assertEqual(ide.get("name"), "code-server")
        self.assertEqual(ide.get("license"), "MIT")
        self.assertFalse(ide.get("copyleft_taint"))

    def test_rde_up_dry_run_plan(self):
        """Positive Control: Verify miosd rde up generates valid execution plan and arguments."""
        # Create a sample workspace with a mock devcontainer
        dc_dir = os.path.join(self.temp_dir, ".devcontainer")
        os.makedirs(dc_dir, exist_ok=True)
        with open(os.path.join(dc_dir, "devcontainer.json"), "w", encoding="utf-8") as f:
            json.dump({"name": "Test Workspace"}, f)

        cmd = [
            self.miosd_bin,
            "rde",
            "--root",
            _ROOT,
            "up",
            self.temp_dir,
            "--provider",
            "docker",
            "--ide",
            "code-server",
            "--dry-run",
            "--json",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"rde up failed: {res.stderr}")

        plan = json.loads(res.stdout)
        self.assertEqual(plan.get("orchestrator"), "devpod")
        self.assertEqual(plan.get("engine"), "podman")
        self.assertEqual(plan.get("provider"), "docker")
        self.assertEqual(plan.get("ide"), "code-server")

        command = plan.get("command", [])
        self.assertTrue(len(command) >= 3)
        self.assertEqual(command[0], "devpod")
        self.assertEqual(command[1], "up")
        self.assertIn("--provider", command)
        self.assertIn("docker", command)
        self.assertIn("--ide", command)
        self.assertIn("code-server", command)

        # Check port forwarding in devpod command
        ports = plan.get("forward_ports", [])
        self.assertIn(_ssot_ports()["devpod_agent"], ports)
        self.assertIn(_ssot_ports()["code_server"], ports)

        env = plan.get("env", {})
        self.assertEqual(env.get("MIOS_RDE_ORCHESTRATOR"), "devpod")
        self.assertEqual(env.get("MIOS_RDE_IDE"), "code-server")

    def test_rde_down_dry_run(self):
        """Positive Control: Verify miosd rde down generates valid stop/delete command."""
        cmd = [self.miosd_bin, "rde", "--root", _ROOT, "down", self.temp_dir, "--dry-run", "--json"]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"rde down failed: {res.stderr}")

        data = json.loads(res.stdout)
        self.assertEqual(data.get("action"), "stop")
        command = data.get("command", [])
        self.assertEqual(command[0], "devpod")
        self.assertEqual(command[1], "stop")

    def test_rde_down_destroy_dry_run(self):
        """Positive Control: Verify miosd rde down --destroy generates delete command."""
        cmd = [self.miosd_bin, "rde", "--root", _ROOT, "down", self.temp_dir, "--destroy", "--dry-run", "--json"]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"rde down --destroy failed: {res.stderr}")

        data = json.loads(res.stdout)
        self.assertEqual(data.get("action"), "delete")
        self.assertTrue(data.get("destroy"))
        command = data.get("command", [])
        self.assertEqual(command[0], "devpod")
        self.assertEqual(command[1], "delete")

    def test_tofu_plan_dry_run(self):
        """Positive Control: Verify OpenTofu wrapper accepts valid tofu arguments under dry-run."""
        cmd = [self.miosd_bin, "rde", "--root", _ROOT, "tofu", "plan", "-var", "env=dev", "--dry-run"]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"rde tofu failed: {res.stderr}")
        self.assertIn("tofu plan", res.stdout)
        self.assertIn("--dry-run", res.stdout)

    def test_cli_verb_dispatch_registration(self):
        """Positive Control: Verify usr/bin/mios registers 'rde' in KNOWN_VERBS."""
        self.assertTrue(os.path.isfile(_USR_BIN_MIOS))
        with open(_USR_BIN_MIOS, "r", encoding="utf-8") as f:
            script_text = f.read()

        self.assertIn('"rde"', script_text)
        self.assertIn('["/usr/libexec/mios/miosd", "rde"]', script_text)
        self.assertIn("mios rde", script_text)

    # -------------------------------------------------------------------------
    # NEGATIVE CONTROLS (Gating & Taint Prevention)
    # -------------------------------------------------------------------------

    def test_coder_agpl_taint_rejection(self):
        """Negative Control: Verify provider 'coder' is rejected with AGPL taint error."""
        cmd = [self.miosd_bin, "rde", "--root", _ROOT, "up", self.temp_dir, "--provider", "coder"]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertNotEqual(res.returncode, 0, "Provider 'coder' must be rejected")
        combined_err = res.stdout + res.stderr
        self.assertTrue(
            "AGPL" in combined_err or "copyleft" in combined_err,
            f"Expected AGPL copyleft rejection error, got: {combined_err}",
        )

    def test_coder_ide_agpl_taint_rejection(self):
        """Negative Control: Verify IDE 'coder' is rejected with AGPL taint error."""
        cmd = [self.miosd_bin, "rde", "--root", _ROOT, "up", self.temp_dir, "--ide", "coder"]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertNotEqual(res.returncode, 0, "IDE 'coder' must be rejected")
        combined_err = res.stdout + res.stderr
        self.assertTrue(
            "AGPL" in combined_err or "copyleft" in combined_err,
            f"Expected AGPL copyleft rejection error, got: {combined_err}",
        )

    def test_terraform_bsl_taint_rejection(self):
        """Negative Control: Verify direct 'terraform' invocations are rejected under BSL 1.1 taint policy."""
        cmd = [self.miosd_bin, "rde", "--root", _ROOT, "tofu", "terraform", "apply"]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertNotEqual(res.returncode, 0, "Direct 'terraform' must be rejected")
        combined_err = res.stdout + res.stderr
        self.assertTrue(
            "BSL" in combined_err or "terraform" in combined_err,
            f"Expected BSL 1.1 rejection error, got: {combined_err}",
        )

    def test_invalid_workspace_rejection(self):
        """Negative Control: Verify non-existent workspace path is rejected with diagnostic."""
        non_existent = os.path.join(self.temp_dir, "non_existent_subdir_404")
        cmd = [self.miosd_bin, "rde", "--root", _ROOT, "up", non_existent]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertNotEqual(res.returncode, 0, "Non-existent workspace must fail")
        combined_err = res.stdout + res.stderr
        self.assertTrue(
            "does not exist" in combined_err or "Invalid workspace" in combined_err,
            f"Expected workspace existence error, got: {combined_err}",
        )


def main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(TestRdeEngine)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
