#!/usr/bin/env python3
# AI-hint: Two-sided controls for mios-tailscale-sync -- SSOT [tailscale] is actually applied with `tailscale set`, and the exit node is the first online peer from exit_nodes that offers one, else cleared.
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


def run_sync(script: str, toml: str, peers: dict, state: str = "Running") -> list[str]:
    """Run `script` under a fake tailscale; return the argv lines `set` recorded."""
    with tempfile.TemporaryDirectory() as tmp:
        bindir = os.path.join(tmp, "bin")
        os.mkdir(bindir)
        fake = os.path.join(bindir, "tailscale")
        with open(fake, "w") as f:
            f.write(_FAKE)
        os.chmod(fake, 0o755)
        status = os.path.join(tmp, "status.json")
        with open(status, "w") as f:
            json.dump({"BackendState": state, "Peer": peers}, f)
        vendor = os.path.join(tmp, "vendor.toml")
        with open(vendor, "w") as f:
            f.write(toml)
        log = os.path.join(tmp, "set.log")
        driver = (
            "import sys\n"
            f"sys.argv = [{script!r}]\n"
            "import importlib.machinery, importlib.util\n"
            f"l = importlib.machinery.SourceFileLoader('s', {script!r})\n"
            "m = importlib.util.module_from_spec(importlib.util.spec_from_loader('s', l))\n"
            "l.exec_module(m)\n"
            f"m.VENDOR_TOML = {vendor!r}\n"
            f"m.HOST_TOML = {os.path.join(tmp, 'absent.toml')!r}\n"
            "sys.exit(m.main())\n")
        env = {k: v for k, v in os.environ.items() if not k.startswith("MIOS_")}
        # The fake shadows any real tailscale; /usr/bin:/bin only serve its cat.
        env.update(PATH=bindir + ":/usr/bin:/bin", FAKE_STATUS=status, FAKE_LOG=log)
        subprocess.run([sys.executable, "-I", "-c", driver], env=env,
                       capture_output=True, text=True, timeout=30)
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
