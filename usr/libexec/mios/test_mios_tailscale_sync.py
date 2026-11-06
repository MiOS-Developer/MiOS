#!/usr/bin/env python3
# AI-hint: Two-sided controls for mios-tailscale-sync -- SSOT [tailscale] is actually applied with `tailscale set`, the exit node is the first online peer from exit_nodes that offers one, else cleared, and a health gate drops an exit node worse than the direct uplink.
# AI-related: usr/libexec/mios/mios-tailscale-sync, usr/share/mios/mios.toml, mios-tailscale-sync.timer
# AI-functions: load_sync, run_sync, TestTailscaleSync
"""WHEN mios-tailscale-sync runs THE SYSTEM SHALL apply [tailscale] to the live
node, and route through the first exit_nodes peer that is online and offers
itself as an exit node -- clearing the exit node when none does.

Every check runs the real script against a fake `tailscale` on an otherwise
empty PATH: `status --json` serves a fixture tailnet, `set` records its argv.
Ambient MIOS_* is scrubbed and the SSOT layers are scratch files.

Negative control: the pre-fix script (HEAD of the file before this change
built a `tailscale up` command and never ran it) fails the same apply check.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, "..", "..", ".."))
_SCRIPT = os.path.join(_ROOT, "usr", "libexec", "mios", "mios-tailscale-sync")
_PRE_FIX = "150b5b461"

_FAKE = """#!/bin/sh
if [ "$1" = status ]; then cat "$FAKE_STATUS"; exit 0; fi
printf '%s\\n' "$*" >> "$FAKE_LOG"
"""
# A direct probe (-I <uplink>) answers from ping-direct; any other probe goes
# through the exit node the last `tailscale set` applied (ping-<ip>).
_FAKE_PING = """#!/bin/sh
case " $* " in *" -I uplink0 "*) cat "$FAKE_DIR/ping-direct"; exit 0;; esac
ip=$(tail -n1 "$FAKE_LOG" | sed -n 's/.*--exit-node=\\([0-9.]*\\).*/\\1/p')
cat "$FAKE_DIR/ping-$ip" 2>/dev/null
"""
_FAKE_IP = """#!/bin/sh
cat "$FAKE_DIR/routes.json"
"""
_ROUTES = [{"dst": "default", "dev": "tether0", "metric": 900},
           {"dst": "default", "dev": "uplink0", "metric": 600}]


def _ping_summary(loss: float, rtt: float | None) -> str:
    out = f"--- probe ping statistics ---\n10 packets transmitted, {10 - int(loss / 10)} received, {loss:g}% packet loss, time 1800ms\n"
    return out + (f"rtt min/avg/max/mdev = 1.0/{rtt:g}/{rtt * 2:g}/1.0 ms\n" if rtt is not None else "")


def _peer(name: str, ip: str, online: bool, exit_opt: bool) -> dict:
    return {"HostName": name, "DNSName": f"{name}.tail0000.ts.net.",
            "TailscaleIPs": [ip, "fd7a:115c:a1e0::1"], "Online": online,
            "ExitNodeOption": exit_opt}


def load_sync(path: str):
    loader = importlib.machinery.SourceFileLoader("mios_tailscale_sync_ut", path)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def run_sync(script: str, toml: str, peers: dict, state: str = "Running",
             host_dropin: str = "", pings: dict | None = None,
             cooling: dict | None = None, out: dict | None = None,
             active_exit: str = "") -> list[str]:
    """Run `script` under a fake tailscale; return the argv lines `set` recorded.
    `host_dropin` becomes a host-tier mios.d fragment over the vendor layer;
    `pings` maps "direct" or an exit-node IP to (loss %, avg RTT ms or None);
    `cooling` seeds the health state file and `out["state"]` receives it;
    `active_exit` is the exit node the fake reports as already in use."""
    with tempfile.TemporaryDirectory() as tmp:
        bindir = os.path.join(tmp, "bin")
        os.mkdir(bindir)
        for name, body in (("tailscale", _FAKE), ("ping", _FAKE_PING), ("ip", _FAKE_IP)):
            with open(os.path.join(bindir, name), "w") as f:
                f.write(body)
            os.chmod(os.path.join(bindir, name), 0o755)
        with open(os.path.join(tmp, "routes.json"), "w") as f:
            json.dump(_ROUTES, f)
        for key, (loss, rtt) in (pings or {}).items():
            with open(os.path.join(tmp, f"ping-{key}"), "w") as f:
                f.write(_ping_summary(loss, rtt))
        state_path = os.path.join(tmp, "run", "exit-health.json")
        if cooling is not None:
            os.makedirs(os.path.dirname(state_path))
            with open(state_path, "w") as f:
                json.dump(cooling, f)
        toml = toml.replace("@STATE@", state_path)
        status = os.path.join(tmp, "status.json")
        with open(status, "w") as f:
            snapshot = {"BackendState": state, "Peer": peers}
            if active_exit:
                snapshot["ExitNodeStatus"] = {"Online": True, "TailscaleIPs": [f"{active_exit}/32"]}
            json.dump(snapshot, f)
        vendor = os.path.join(tmp, "vendor.toml")
        with open(vendor, "w") as f:
            f.write(toml)
        host_d = os.path.join(tmp, "host.d")
        os.mkdir(host_d)
        if host_dropin:
            with open(os.path.join(host_d, "60-tailscale.toml"), "w") as f:
                f.write(host_dropin)
        log = os.path.join(tmp, "set.log")
        driver = (
            "import sys\n"
            f"sys.argv = [{script!r}]\n"
            "import importlib.machinery, importlib.util\n"
            f"l = importlib.machinery.SourceFileLoader('s', {script!r})\n"
            "m = importlib.util.module_from_spec(importlib.util.spec_from_loader('s', l))\n"
            "l.exec_module(m)\n"
            # Only the pre-fix script reads these; the fix resolves the env layers.
            f"m.VENDOR_TOML = {vendor!r}\n"
            f"m.HOST_TOML = {os.path.join(tmp, 'absent.toml')!r}\n"
            "sys.exit(m.main())\n")
        env = {k: v for k, v in os.environ.items() if not k.startswith("MIOS_")}
        # The fake shadows any real tailscale; /usr/bin:/bin only serve its cat.
        env.update(PATH=bindir + ":/usr/bin:/bin", FAKE_STATUS=status, FAKE_LOG=log, FAKE_DIR=tmp,
                   MIOS_VENDOR_TOML=vendor, MIOS_VENDOR_TOML_D=os.path.join(tmp, "vendor.d"),
                   MIOS_HOST_TOML=os.path.join(tmp, "absent.toml"), MIOS_HOST_TOML_D=host_d,
                   MIOS_USER_TOML=os.path.join(tmp, "absent-user.toml"),
                   MIOS_USER_TOML_D=os.path.join(tmp, "user.d"))
        subprocess.run([sys.executable, "-I", "-c", driver], env=env,
                       capture_output=True, text=True, timeout=60)
        if out is not None and os.path.exists(state_path):
            with open(state_path) as f:
                out["state"] = json.load(f)
        if not os.path.exists(log):
            return []
        with open(log) as f:
            return f.read().splitlines()


_BASE = """[tailscale]
enabled = true
accept_routes = true
accept_dns = true
advertise_routes = ""
"""
_PREFS = _BASE + 'exit_nodes = ["pixel-7-pro", "kabu"]\nexit_node_allow_lan_access = true\n'
_HEALTH = _PREFS + """[tailscale.exit_node_health]
enabled = true
probe = "192.0.2.1"
count = 10
interval_s = 0.2
max_extra_loss_pct = 10
max_extra_rtt_ms = 150
cooldown_s = 1800
state = "@STATE@"
"""
_PIXEL, _KABU = "100.122.33.108", "100.89.218.102"
_BOTH = {"a": _peer("pixel-7-pro", _PIXEL, True, True), "b": _peer("kabu", _KABU, True, True)}
_KEEP = "is_worse = exit_is_worse(direct, via_exit, health)"
_STANDBY = 'if direct[0] <= health["max_extra_loss_pct"]:'


def check_applies(script: str) -> str | None:
    """None when `script` applies SSOT with `tailscale set`; else the defect."""
    peers = {"a": _peer("pixel-7-pro", "100.122.33.108", True, True)}
    calls = run_sync(script, _PREFS, peers)
    if not calls:
        return "no tailscale invocation besides status: SSOT is never applied"
    want = "set --accept-routes=true --accept-dns=true --advertise-routes= " \
           "--exit-node=100.122.33.108 --exit-node-allow-lan-access=true"
    if calls != [want]:
        return f"applied {calls!r}, expected [{want!r}]"
    return None


_LOADER = "mios_toml.load_merged(layers=mios_toml.layer_paths())"
_MONOLITHS_ONLY = 'mios_toml.load_merged(layers=[os.environ["MIOS_VENDOR_TOML"], os.environ["MIOS_HOST_TOML"]])'


def check_host_dropin(script: str) -> str | None:
    """None when a host mios.d fragment's exit_nodes reach `tailscale set`."""
    peers = {"a": _peer("kabu", "100.89.218.102", True, True)}
    calls = run_sync(script, _BASE, peers, host_dropin='[tailscale]\nexit_nodes = ["kabu"]\n')
    if len(calls) != 1 or "--exit-node=100.89.218.102" not in calls[0]:
        return f"host mios.d drop-in ignored: applied {calls!r}"
    return None


def check_health_gate(script: str) -> str | None:
    """None when an exit node worse than the direct uplink is cooled down and
    the next preference is tried."""
    out: dict = {}
    calls = run_sync(script, _HEALTH, _BOTH, out=out, active_exit=_PIXEL,
                     pings={"direct": (0, 25), _PIXEL: (33, 500), _KABU: (0, 60)})
    exits = [c.split("--exit-node=")[1].split()[0] for c in calls if "--exit-node=" in c]
    if exits != [_PIXEL, _KABU]:
        return f"worse exit node kept: applied exit nodes {exits!r}"
    if _PIXEL not in out.get("state", {}):
        return f"no cooldown recorded: {out.get('state')!r}"
    return None


def check_standby(script: str) -> str | None:
    """None when a healthy direct uplink leaves the exit nodes untried."""
    calls = run_sync(script, _HEALTH, _BOTH, pings={"direct": (0, 25), _PIXEL: (0, 60)})
    if any(f"--exit-node={ip}" in c for c in calls for ip in (_PIXEL, _KABU)):
        return f"exit node trialled on a healthy uplink: {calls!r}"
    if len(calls) != 1 or "--exit-node= " not in calls[0] + " ":
        return f"standby did not apply the cleared exit node: {calls!r}"
    return None


class TestTailscaleSync(unittest.TestCase):
    def test_applies_ssot_with_first_preference(self) -> None:
        self.assertIsNone(check_applies(_SCRIPT))

    def test_skips_peer_that_does_not_offer_exit_node(self) -> None:
        peers = {"a": _peer("pixel-7-pro", "100.122.33.108", True, False),
                 "b": _peer("kabu", "100.89.218.102", True, True)}
        calls = run_sync(_SCRIPT, _PREFS, peers)
        self.assertEqual(len(calls), 1, calls)
        self.assertIn("--exit-node=100.89.218.102", calls[0])

    def test_clears_exit_node_when_none_usable(self) -> None:
        peers = {"a": _peer("pixel-7-pro", "100.122.33.108", False, True),
                 "b": _peer("kabu", "100.89.218.102", True, False)}
        calls = run_sync(_SCRIPT, _PREFS, peers)
        self.assertEqual(len(calls), 1, calls)
        self.assertIn("--exit-node= ", calls[0] + " ")

    def test_no_preferences_leaves_exit_node_alone(self) -> None:
        calls = run_sync(_SCRIPT, _BASE, {"a": _peer("kabu", "100.89.218.102", True, True)})
        self.assertEqual(len(calls), 1, calls)
        self.assertNotIn("--exit-node", calls[0])

    def test_logged_out_node_is_not_reconfigured(self) -> None:
        calls = run_sync(_SCRIPT, _PREFS, {}, state="NeedsLogin")
        self.assertEqual(calls, [])

    def test_select_matches_hostname_dnsname_or_ip(self) -> None:
        mod = load_sync(_SCRIPT)
        status = {"Peer": {"a": _peer("Pixel-7-Pro", "100.122.33.108", True, True)}}
        for want in ("pixel-7-pro", "PIXEL-7-PRO", "100.122.33.108"):
            self.assertEqual(mod.select_exit_node(status, [want])[0], "100.122.33.108", want)
        self.assertEqual(mod.select_exit_node(status, ["absent"]), ("", ""))

    def test_host_dropin_sets_exit_nodes(self) -> None:
        self.assertIsNone(check_host_dropin(_SCRIPT))

    def test_negative_monolith_only_loader_misses_dropin(self) -> None:
        with open(_SCRIPT) as f:
            source = f.read()
        self.assertEqual(source.count(_LOADER), 1)
        with tempfile.NamedTemporaryFile("w", suffix="-mios-tailscale-sync",
                                         dir=os.path.dirname(_SCRIPT), delete=False) as f:
            f.write(source.replace(_LOADER, _MONOLITHS_ONLY))
        try:
            defect = check_host_dropin(f.name)
        finally:
            os.unlink(f.name)
        self.assertIsNotNone(defect)
        self.assertIn("drop-in ignored", defect)

    def test_health_gate_keeps_a_healthy_exit_node(self) -> None:
        calls = run_sync(_SCRIPT, _HEALTH, _BOTH, active_exit=_PIXEL,
                         pings={"direct": (0, 25), _PIXEL: (0, 70)})
        self.assertEqual(len(calls), 1, calls)
        self.assertIn(f"--exit-node={_PIXEL}", calls[0])

    def test_health_gate_fails_over_from_a_worse_exit_node(self) -> None:
        self.assertIsNone(check_health_gate(_SCRIPT))

    def test_health_gate_clears_when_every_exit_node_is_worse(self) -> None:
        calls = run_sync(_SCRIPT, _HEALTH, _BOTH, active_exit=_PIXEL,
                         pings={"direct": (0, 25), _PIXEL: (33, 500), _KABU: (100, None)})
        self.assertEqual(len(calls), 3, calls)
        self.assertIn("--exit-node= ", calls[2] + " ")

    def test_health_gate_honours_cooldown(self) -> None:
        calls = run_sync(_SCRIPT, _HEALTH, _BOTH, cooling={_PIXEL: 4102444800},
                         pings={"direct": (55, 25), _KABU: (0, 60)})
        self.assertEqual(len(calls), 1, calls)
        self.assertIn(f"--exit-node={_KABU}", calls[0])

    def test_healthy_uplink_keeps_exit_nodes_on_standby(self) -> None:
        self.assertIsNone(check_standby(_SCRIPT))

    def test_degraded_uplink_fails_over_to_a_less_lossy_exit_node(self) -> None:
        calls = run_sync(_SCRIPT, _HEALTH, _BOTH, pings={"direct": (55, 25), _PIXEL: (35, 600)})
        self.assertEqual(len(calls), 1, calls)
        self.assertIn(f"--exit-node={_PIXEL}", calls[0])

    def test_negative_no_standby_trials_exit_nodes_on_a_healthy_uplink(self) -> None:
        with open(_SCRIPT) as f:
            source = f.read()
        self.assertEqual(source.count(_STANDBY), 1)
        with tempfile.NamedTemporaryFile("w", suffix="-mios-tailscale-sync",
                                         dir=os.path.dirname(_SCRIPT), delete=False) as f:
            f.write(source.replace(_STANDBY, "if False:"))
        try:
            defect = check_standby(f.name)
        finally:
            os.unlink(f.name)
        self.assertIsNotNone(defect)
        self.assertIn("healthy uplink", defect)

    def test_exit_is_worse_rule(self) -> None:
        mod = load_sync(_SCRIPT)
        h = {"max_extra_loss_pct": 10, "max_extra_rtt_ms": 150}
        inf = float("inf")
        for direct, via, worse in [((0, 25), (33, 500), True),     # live: clean uplink
                                   ((55, 25), (35, 600), False),   # lossy uplink: less loss wins
                                   ((0, 25), (5, 120), False),     # similar loss, +95 ms
                                   ((0, 25), (0, 200), True),      # similar loss, +175 ms
                                   ((100, inf), (40, 300), False),  # dead uplink
                                   ((0, 25), (100, inf), True)]:   # dead exit node
            self.assertEqual(mod.exit_is_worse(direct, via, h), worse, (direct, via))

    def test_health_gate_off_without_complete_ssot(self) -> None:
        partial = _HEALTH.replace('max_extra_rtt_ms = 150\n', '')
        calls = run_sync(_SCRIPT, partial, _BOTH, pings={"direct": (0, 25), _PIXEL: (33, 500)})
        self.assertEqual(len(calls), 1, calls)
        self.assertIn(f"--exit-node={_PIXEL}", calls[0])

    def test_negative_ungated_failover_keeps_a_worse_exit_node(self) -> None:
        with open(_SCRIPT) as f:
            source = f.read()
        self.assertEqual(source.count(_KEEP), 1)
        with tempfile.NamedTemporaryFile("w", suffix="-mios-tailscale-sync",
                                         dir=os.path.dirname(_SCRIPT), delete=False) as f:
            f.write(source.replace(_KEEP, "is_worse = False"))
        try:
            defect = check_health_gate(f.name)
        finally:
            os.unlink(f.name)
        self.assertIsNotNone(defect)
        self.assertIn("worse exit node kept", defect)

    def test_negative_pre_fix_script_never_applies(self) -> None:
        old = subprocess.run(["git", "-C", _ROOT, "show",
                              f"{_PRE_FIX}:usr/libexec/mios/mios-tailscale-sync"],
                             capture_output=True, text=True)
        if old.returncode != 0:
            self.skipTest(f"pre-fix revision {_PRE_FIX} unavailable")
        with tempfile.NamedTemporaryFile("w", suffix="-mios-tailscale-sync",
                                         dir=os.path.dirname(_SCRIPT), delete=False) as f:
            f.write(old.stdout)
        try:
            defect = check_applies(f.name)
        finally:
            os.unlink(f.name)
        self.assertIsNotNone(defect)
        self.assertIn("never applied", defect)


if __name__ == "__main__":
    unittest.main()
