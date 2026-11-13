#!/usr/bin/env python3
# AI-hint: Two-sided controls for mios-tailscale-sync -- SSOT [tailscale] is actually applied with `tailscale set`, the exit node is the first online peer from exit_nodes that offers one, else cleared, and a health gate drops an exit node worse than the direct uplink.
# AI-related: usr/libexec/mios/mios-tailscale-sync, usr/share/mios/mios.toml, mios-tailscale-sync.timer
# AI-functions: load_sync, run_sync, check_enrolls, TestTailscaleSync
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

from contextlib import contextmanager
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
# The script before enrollment existed: it only printed "run: tailscale up".
_PRE_ENROLL = "46c4fe9"

_FAKE = """#!/bin/sh
case " $* " in *" serve status "*) cat "$FAKE_SERVE" 2>/dev/null || echo '{}'; exit "${FAKE_SERVE_RC:-0}";; esac
case " $* " in *" status "*) cat "$FAKE_STATUS"; exit 0;; esac
for a in "$@"; do
  case "$a" in --auth-key=file:*) cat "${a#--auth-key=file:}" > "$FAKE_DIR/seen-key";; esac
done
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
_FAKE_SYSTEMCTL = """#!/bin/sh
printf 'systemctl %s\\n' "$*" >> "$FAKE_LOG"
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


@contextmanager
def private_script(source: str):
    """Keep mutations off the source mount and resolve helpers from the candidate source tree."""
    with tempfile.TemporaryDirectory(prefix="mios-tailscale-script-") as root:
        script_dir = os.path.join(root, "usr", "libexec", "mios")
        os.makedirs(script_dir)
        os.makedirs(os.path.join(root, "usr", "lib"))
        os.symlink(os.path.join(_ROOT, "usr", "lib", "mios"),
                   os.path.join(root, "usr", "lib", "mios"))
        script = os.path.join(script_dir, "mios-tailscale-sync")
        with open(script, "w") as f:
            f.write(source)
        yield script


def load_sync(path: str):
    loader = importlib.machinery.SourceFileLoader("mios_tailscale_sync_ut", path)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def run_sync(script: str, toml: str, peers: dict, state: str = "Running",
             host_dropin: str = "", pings: dict | None = None,
             cooling: dict | None = None, out: dict | None = None,
             active_exit: str = "", args: tuple[str, ...] = (),
             extra_env: dict | None = None, operator: str = "",
             module_patch: dict | None = None, module_code: str = "") -> list[str]:
    """Run `script` under a fake tailscale; return the argv lines `set` recorded.
    `host_dropin` becomes a host-tier mios.d fragment over the vendor layer;
    `pings` maps "direct" or an exit-node IP to (loss %, avg RTT ms or None);
    `cooling` seeds the health state file and `out["state"]` receives it;
    `active_exit` is the exit node the fake reports as already in use;
    `args` are the script's arguments, `extra_env` is added to its environment
    and `out["seen_key"]` receives what an --auth-key=file: held at call time.
    `operator` is the operator's (user) tier text; `module_patch` sets module
    constants before main() runs and `module_code` (a PLANT) runs against it as `m`."""
    with tempfile.TemporaryDirectory() as tmp:
        bindir = os.path.join(tmp, "bin")
        os.mkdir(bindir)
        for name, body in (("tailscale", _FAKE), ("ping", _FAKE_PING), ("ip", _FAKE_IP),
                           ("systemctl", _FAKE_SYSTEMCTL)):
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
            snapshot = {"BackendState": state, "Peer": peers,
                        "Self": {"DNSName": "blade.tail0000.ts.net."}}
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
        if operator:
            with open(os.path.join(tmp, "absent-user.toml"), "w") as f:
                f.write(operator)
        patches = "".join(f"m.{k} = {v!r}\n" for k, v in (module_patch or {}).items())
        driver = (
            "import sys\n"
            f"sys.argv = [{script!r}, *{list(args)!r}]\n"
            "import importlib.machinery, importlib.util\n"
            f"l = importlib.machinery.SourceFileLoader('s', {script!r})\n"
            "m = importlib.util.module_from_spec(importlib.util.spec_from_loader('s', l))\n"
            "l.exec_module(m)\n"
            # Only the pre-fix script reads these; the fix resolves the env layers.
            f"m.VENDOR_TOML = {vendor!r}\n"
            f"m.HOST_TOML = {os.path.join(tmp, 'absent.toml')!r}\n"
            + patches + module_code + "\n"
            "sys.exit(m.main())\n")
        env = {k: v for k, v in os.environ.items() if not k.startswith("MIOS_")}
        # The fake shadows any real tailscale; /usr/bin:/bin only serve its cat.
        env.update(PATH=bindir + ":/usr/bin:/bin", FAKE_STATUS=status, FAKE_LOG=log, FAKE_DIR=tmp,
                   MIOS_VENDOR_TOML=vendor, MIOS_VENDOR_TOML_D=os.path.join(tmp, "vendor.d"),
                   MIOS_HOST_TOML=os.path.join(tmp, "absent.toml"), MIOS_HOST_TOML_D=host_d,
                   MIOS_USER_TOML=os.path.join(tmp, "absent-user.toml"),
                   MIOS_USER_TOML_D=os.path.join(tmp, "user.d"))
        env.pop("TS_AUTHKEY", None)
        env.update(extra_env or {})
        subprocess.run([sys.executable, "-I", "-c", driver], env=env,
                       capture_output=True, text=True, timeout=60)
        seen = os.path.join(tmp, "seen-key")
        if out is not None and os.path.exists(seen):
            with open(seen) as f:
                out["seen_key"] = f.read()
        if out is not None and os.path.exists(state_path):
            with open(state_path) as f:
                out["state"] = json.load(f)
        if not os.path.exists(log):
            return []
        with open(log) as f:
            return f.read().splitlines()


_BASE = """[security.operator_tier]
values = ["identity.default_password", "portal.password", "ttyd.auth_pass"]
toggles = ["tailscale.serve.enabled", "tailscale.serve.routes.*.enabled"]
[tailscale]
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


_LOADER = "mios_toml.operator_view()[0]"
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


# The cloud overlay's launch parameters, as [deployment.cloud.overlay.tailscale] sets them.
_CLOUD = _BASE + """hostname = "mios-cloud"
advertise_exit_node = true
shields_up = true
"""
_KEY = "tskey-auth-UNITTEST0000-notarealkey"


def check_enrolls(script: str) -> str | None:
    """None when a logged-out node with a key file is enrolled through
    `up --auth-key=file:` carrying the SSOT launch parameters."""
    with tempfile.TemporaryDirectory() as d:
        key = os.path.join(d, "authkey")
        with open(key, "w") as f:
            f.write(_KEY)
        out: dict = {}
        calls = run_sync(script, _CLOUD + f'auth_key_file = "{key}"\n', {},
                         state="NeedsLogin", out=out)
    ups = [c for c in calls if c.startswith("up ")]
    if not ups:
        return f"logged-out node never enrolled: {calls!r}"
    want = ("--reset", f"--auth-key=file:{key}", "--hostname=mios-cloud",
            "--advertise-exit-node=true", "--shields-up=true", "--accept-dns=")
    missing = [w for w in want if w not in ups[0]]
    if missing:
        return f"enrollment lacks {missing}: {ups[0]!r}"
    if out.get("seen_key") != _KEY:
        return f"key file not readable at enrollment: {out.get('seen_key')!r}"
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

    def test_enrolls_from_key_file_with_launch_params(self) -> None:
        self.assertIsNone(check_enrolls(_SCRIPT))

    def test_enrolls_from_env_key_without_exposing_it(self) -> None:
        out: dict = {}
        calls = run_sync(_SCRIPT, _CLOUD, {}, state="NeedsLogin", out=out,
                         extra_env={"MIOS_TAILSCALE_AUTHKEY": _KEY})
        self.assertEqual(len(calls), 1, calls)
        self.assertTrue(calls[0].startswith("up --reset --auth-key=file:"), calls)
        self.assertNotIn(_KEY, calls[0])  # never on the command line
        self.assertEqual(out.get("seen_key"), _KEY)
        tmp_key = calls[0].split("--auth-key=file:", 1)[1].split()[0]
        self.assertFalse(os.path.exists(tmp_key), f"temporary key copy left at {tmp_key}")

    def test_socket_argument_addresses_tailscaled(self) -> None:
        calls = run_sync(_SCRIPT, _CLOUD, {}, args=("--socket=/run/ts/x.sock",))
        self.assertEqual(len(calls), 1, calls)
        self.assertTrue(calls[0].startswith("--socket=/run/ts/x.sock set "), calls)
        self.assertIn("--advertise-exit-node=true", calls[0])
        self.assertIn("--shields-up=true", calls[0])

    def test_unset_exit_node_and_shields_are_left_alone(self) -> None:
        calls = run_sync(_SCRIPT, _BASE, {})
        self.assertEqual(len(calls), 1, calls)
        for flag in ("--advertise-exit-node", "--shields-up", "--hostname"):
            self.assertNotIn(flag, calls[0])

    def test_exit_node_offer_suppresses_exit_node_use(self) -> None:
        calls = run_sync(_SCRIPT, _PREFS + "advertise_exit_node = true\n",
                         {"b": _peer("kabu", "100.89.218.102", True, True)})
        self.assertEqual(len(calls), 1, calls)
        self.assertNotIn("--exit-node=", calls[0])

    def test_negative_pre_enrollment_script_never_enrolls(self) -> None:
        old = subprocess.run(["git", "-C", _ROOT, "show",
                              f"{_PRE_ENROLL}:usr/libexec/mios/mios-tailscale-sync"],
                             capture_output=True, text=True)
        if old.returncode != 0:
            self.skipTest(f"pre-enrollment revision {_PRE_ENROLL} unavailable")
        with private_script(old.stdout) as script:
            defect = check_enrolls(script)
        self.assertIsNotNone(defect)
        self.assertIn("never enrolled", defect)

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
        with private_script(source.replace(_LOADER, _MONOLITHS_ONLY)) as script:
            defect = check_host_dropin(script)
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
        with private_script(source.replace(_STANDBY, "if False:")) as script:
            defect = check_standby(script)
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
        with private_script(source.replace(_KEEP, "is_worse = False")) as script:
            defect = check_health_gate(script)
        self.assertIsNotNone(defect)
        self.assertIn("worse exit node kept", defect)

    def test_negative_pre_fix_script_never_applies(self) -> None:
        old = subprocess.run(["git", "-C", _ROOT, "show",
                              f"{_PRE_FIX}:usr/libexec/mios/mios-tailscale-sync"],
                             capture_output=True, text=True)
        if old.returncode != 0:
            self.skipTest(f"pre-fix revision {_PRE_FIX} unavailable")
        with private_script(old.stdout) as script:
            defect = check_applies(script)
        self.assertIsNotNone(defect)
        self.assertIn("never applied", defect)


# ---------------------------------------------------------------- [tailscale.serve] routes
import http.server  # noqa: E402
import threading  # noqa: E402


class _Upstream(http.server.BaseHTTPRequestHandler):
    """A loopback upstream that answers anonymous GETs with `code` (`paths` overrides it
    per path: the Portal's "/" redirects to /login while /v1/* may answer without one)."""
    code = 401
    paths: dict = {}

    def do_GET(self):  # noqa: N802
        code = self.paths.get(self.path, self.code)
        self.send_response(code)
        if code in (302, 303):
            self.send_header("Location", "/login")
        self.end_headers()

    def log_message(self, *a):
        pass


def _serve_toml(d: str, routes: str, up: int) -> str:
    return _BASE + f"""[ports]
agent_pipe = {up}
agent_share = {up}
forge_http = 8400
forge_ssh = 8410
ttyd_bash = {up}
tailnet_https = 443
[paths]
var_tailscale_sync_dir = "{d}"
[network]
loopback = "127.0.0.1"
[units]
forge = "mios-forge.service"
[portal]
public_host = ""
[tailscale.serve]
enabled = true
""" + routes


_R_PORTAL = '[tailscale.serve.routes.portal]\nenabled = true\nproto = "https"\nfront_door = true\npath = "/"\nupstream = "agent_pipe"\nrequire_auth = true\n'
_R_SHARE = '[tailscale.serve.routes.agent_share]\nenabled = true\nproto = "https"\nfront_door = true\npath = "/share/"\nupstream = "agent_share"\n'
_R_FORGE = '[tailscale.serve.routes.forge_http]\nenabled = true\nproto = "https"\npath = "/"\nupstream = "forge_http"\n'
_R_SSH = '[tailscale.serve.routes.forge_ssh]\nenabled = true\nproto = "tcp"\nupstream = "forge_ssh"\n'
_SELF = "blade.tail0000.ts.net"


class TestTailscaleIpv6(unittest.TestCase):
    """IPv6 URL and listener behavior without opening a network socket."""

    def test_loopback_route_authorities_and_probes(self):
        mod = load_sync(_SCRIPT)
        data = {"network": {"loopback": "::1"}, "ports": {"agent_pipe": 8700, "forge_ssh": 8410, "ttyd_bash": 7681},
                "tailscale": {"serve": {"enabled": True, "routes": {
                    "portal": {"enabled": True, "proto": "https", "upstream": "agent_pipe", "require_auth": True,
                               "auth_probe_paths": ["/", "ws:/events"]},
                    "forge_ssh": {"enabled": True, "proto": "tcp", "upstream": "forge_ssh"}}}}}
        routes, errors = mod.render_routes(data)
        self.assertEqual(errors, [])
        by = {row["name"]: row for row in routes}
        self.assertEqual(by["portal"]["target"], "http://[::1]:8700")
        self.assertEqual(by["portal"]["probes"], ["http://[::1]:8700/", "ws://[::1]:8700/events"])
        self.assertEqual(by["forge_ssh"]["target"], "tcp://[::1]:8410")
        self.assertEqual(mod.legacy_ttyd_handler(data), ("https:7681:/", "http://[::1]:7681"))
        data["network"]["loopback"] = "127.0.0.1"
        routes, errors = mod.render_routes(data)
        self.assertEqual(errors, [])
        self.assertEqual(next(row for row in routes if row["name"] == "portal")["target"], "http://127.0.0.1:8700")

    def test_probe_parses_ipv6_and_refuses_malformed_urls(self):
        import http.client
        from unittest import mock
        mod = load_sync(_SCRIPT)
        with mock.patch("http.client.HTTPConnection") as connect:
            conn = connect.return_value
            conn.getresponse.return_value.status = 401
            conn.getresponse.return_value.getheader.return_value = ""
            for url, path in (("http://[::1]:8700/status", "/status"), ("ws://[::1]:8700/events", "/events")):
                connect.reset_mock()
                self.assertEqual(mod._probe(url), "login")
                connect.assert_called_once_with("::1", 8700, timeout=6)
                self.assertEqual(conn.request.call_args.args[:2], ("GET", path))
                conn.close.assert_called_once_with()
            for url in ("http://::1:8700/", "http://[::1]:bad/", "http://[::1", "ws://[::1]:0/", "file:///etc/shadow"):
                connect.reset_mock()
                self.assertEqual(mod._probe(url), "invalid", url)
                connect.assert_not_called()
                self.assertEqual(mod._auth_refusal({"probes": [url]}), "an auth probe URL is invalid")
            conn.request.side_effect = http.client.InvalidURL("fixture invalid request path")
            connect.reset_mock()
            self.assertEqual(mod._probe("http://[::1]:8700/"), "invalid")
            conn.close.assert_called_once_with()
            conn.request.side_effect = None
            conn.getresponse.side_effect = http.client.BadStatusLine("fixture bad upstream response")
            connect.reset_mock()
            self.assertEqual(mod._probe("http://[::1]:8700/"), "down")
            conn.close.assert_called_once_with()

    def test_share_selects_the_bind_address_family(self):
        import socket
        from unittest import mock
        mod = load_sync(_SCRIPT)
        seen = []

        class FakeServer:
            address_family = socket.AF_INET

            def __init__(self, address, handler):
                seen.append((self.address_family, address))

            def serve_forever(self):
                raise KeyboardInterrupt

            def server_close(self):
                pass

        with tempfile.TemporaryDirectory() as root, mock.patch("os.geteuid", return_value=1000), \
                mock.patch("http.server.ThreadingHTTPServer", FakeServer):
            self.assertEqual(mod.share_serve(root, "::1", 8790), 0)
            self.assertEqual(mod.share_serve(root, "127.0.0.1", 8790), 0)
        self.assertEqual(seen, [(socket.AF_INET6, ("::1", 8790)), (socket.AF_INET, ("127.0.0.1", 8790))])


class TestTailscaleServe(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="mios-ts-serve-")
        self.quadlet = os.path.join(self.d, "quadlet")
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Upstream)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.up = self.srv.server_address[1]

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        import shutil
        shutil.rmtree(self.d, ignore_errors=True)

    def _dropin(self) -> str:
        return os.path.join(self.quadlet, "mios-forge.container.d", "60-mios-tailnet.conf")

    def _run(self, routes: str, live: dict | None = None, owned: dict | None = None, args=(),
             operator: str = "", raw: str | None = None, env: dict | None = None, code: str = "",
             toml: str | None = None):
        live_path = os.path.join(self.d, "serve.json")
        with open(live_path, "w") as f:
            f.write(raw if raw is not None else json.dumps(live or {}))
        if owned is not None:
            with open(os.path.join(self.d, "serve-owned.json"), "w") as f:
                json.dump(owned, f)
        calls = run_sync(_SCRIPT, toml or _serve_toml(self.d, routes, self.up), {}, args=args,
                         extra_env={"FAKE_SERVE": live_path, **(env or {})}, operator=operator,
                         module_patch={"QUADLET_RUNTIME_DIR": self.quadlet}, module_code=code)
        return [c for c in calls if c.startswith("serve") or c.startswith("systemctl")]

    def _state(self) -> dict:
        try:
            with open(os.path.join(self.d, "serve-owned.json")) as f:
                return json.load(f)
        except OSError:
            return {}

    def _owned(self) -> dict:
        """The owned ROUTES (markers such as the one-time ttyd-expose migration left out)."""
        return {k: v for k, v in self._state().items() if not k.startswith("migrated:")}

    def test_plan_is_pure_and_owns_only_what_it_added_or_adopted(self):
        mod = load_sync(_SCRIPT)
        want = [{"name": "a", "proto": "https", "port": 443, "path": "/", "target": "http://127.0.0.1:1"},
                {"name": "b", "proto": "tcp", "port": 8410, "path": "", "target": "tcp://127.0.0.1:8410"},
                {"name": "c", "proto": "https", "port": 8900, "path": "/", "target": "http://127.0.0.1:8900"},
                {"name": "d", "proto": "tcp", "port": 8800, "path": "", "target": "tcp://127.0.0.1:8800"}]
        live = {"https:443:/": "http://127.0.0.1:1", "tcp:8410:": "tcp://127.0.0.1:9999",
                "https:8400:/": "http://127.0.0.1:8400", "https:7000:/": "http://127.0.0.1:7000",
                "https:8800:/": "http://127.0.0.1:8800"}
        owned = {"https:8400:/": "http://127.0.0.1:8400", "https:7000:/": "http://127.0.0.1:7001"}
        plan = {(a, r["name"]) for a, r in mod.plan_serve(want, live, owned)}
        # d: a tcp route on a port a hand-made https route holds is a conflict, never an add.
        self.assertEqual(plan, {("adopt", "a"), ("conflict", "b"), ("add", "c"), ("conflict", "d"),
                                ("remove", "https:8400:/"), ("forget", "https:7000:/")})

    def test_positive_every_route_is_a_loopback_proxy(self):
        calls = self._run(_R_PORTAL + _R_SHARE)
        self.assertIn(f"serve --bg --https=443 --set-path=/ http://127.0.0.1:{self.up}", calls)
        self.assertIn(f"serve --bg --https=443 --set-path=/share/ http://127.0.0.1:{self.up}", calls)
        self.assertFalse([c for c in calls if "reset" in c or "funnel" in c], calls)
        for c in calls:
            self.assertRegex(c, r" (http|tcp)://127\.0\.0\.1:\d+$", f"a route that is not a loopback proxy: {c}")
        self.assertEqual(sorted(self._owned()), ["https:443:/", "https:443:/share/"])

    def test_negative_unauthenticated_upstream_is_refused(self):
        _Upstream.code = 200
        try:
            calls = self._run(_R_PORTAL)
        finally:
            _Upstream.code = 401
        self.assertEqual(calls, [], "an upstream that answers without a login was exposed")

    def test_negative_directory_handler_is_never_rendered(self):
        mod = load_sync(_SCRIPT)
        data = {"tailscale": {"serve": {"enabled": True, "front_door": "h", "routes": {
            "s": {"enabled": True, "proto": "https", "front_door": True, "path": "/share/",
                  "dir": "credstore_dir"}}}},
            "ports": {"h": 443}, "network": {"loopback": "127.0.0.1"}, "paths": {"credstore_dir": "/etc"}}
        routes, errors = mod.render_routes(data)
        self.assertEqual(routes, [], "a directory path handler was rendered")
        self.assertTrue(errors)
        data["network"]["loopback"] = "10.0.0.5"
        data["tailscale"]["serve"]["routes"]["s"].update(upstream="h")
        self.assertEqual(mod.render_routes(data)[0], [], "a non-loopback upstream was rendered")

    def test_negative_operator_tier_cannot_retarget_a_route(self):
        # Only the enabled toggles follow the operator tier; its upstream, front door,
        # ledger dir, public_host and loopback are ignored (all vendor/host).
        hostile = ('[tailscale.serve]\nenabled = true\nfront_door = "forge_http"\n'
                   '[tailscale.serve.routes.portal]\nenabled = true\nupstream = "forge_ssh"\nproto = "tcp"\n'
                   f'[paths]\nvar_tailscale_sync_dir = "{self.d}/evil"\n'
                   '[network]\nloopback = "10.9.9.9"\n[portal]\npublic_host = "x\\n[Service]"\n')
        calls = self._run(_R_PORTAL.replace("enabled = true", "enabled = false"), operator=hostile)
        self.assertEqual(calls, [f"serve --bg --https=443 --set-path=/ http://127.0.0.1:{self.up}"],
                         "the operator tier steered more than the enabled toggle")
        self.assertFalse(os.path.exists(os.path.join(self.d, "evil")))

    def test_hand_made_route_is_adopted_or_left_never_reset(self):
        live = {"TCP": {"443": {"HTTPS": True}},
                "Web": {f"{_SELF}:443": {"Handlers": {"/": {"Proxy": f"http://127.0.0.1:{self.up}"},
                                                      "/share/": {"Path": "/somewhere/else"}}}}}
        calls = self._run(_R_PORTAL + _R_SHARE, live=live)
        self.assertEqual(calls, [], "an identical route was re-added or a different one replaced")
        self.assertEqual(sorted(self._owned()), ["https:443:/"])

    def test_stale_handler_of_another_node_name_is_not_ours(self):
        # A renamed node's leftover handler on the same port, in either key order, is
        # neither a conflict while the route is on nor forgotten when it is turned off.
        target = f"http://127.0.0.1:{self.up}"
        for stale in ("aa-old.tail0000.ts.net", "zz-old.tail0000.ts.net"):
            web = {f"{stale}:443": {"Handlers": {"/": {"Proxy": "https+insecure://127.0.0.1:9090"}}},
                   f"{_SELF}:443": {"Handlers": {"/": {"Proxy": target}}}}
            # tailscale's JSON is a Go map: keys come out SORTED, so zz-old follows this node.
            live = {"TCP": {"443": {"HTTPS": True}}, "Web": dict(sorted(web.items()))}
            self.assertEqual(self._run(_R_PORTAL, live=live, owned={"https:443:/": target}), [], stale)
            calls = self._run("", live=live, owned={"https:443:/": target})
            self.assertEqual(calls, ["serve --https=443 --set-path=/ off"], stale)
            self.assertEqual(self._owned(), {})

    def test_disabled_owned_route_is_removed(self):
        owned = {"tcp:8410:": "tcp://127.0.0.1:8410"}
        live = {"TCP": {"8410": {"TCPForward": "127.0.0.1:8410"}}}
        calls = self._run('[tailscale.serve.routes.x]\nenabled = false\n', live=live, owned=owned)
        self.assertEqual(calls, ["serve --tcp=8410 off"])
        self.assertEqual(self._owned(), {})

    def test_tcp_and_https_cannot_share_a_listener(self):
        mod = load_sync(_SCRIPT)
        data = {"tailscale": {"serve": {"enabled": True, "routes": {
            "a": {"enabled": True, "proto": "https", "upstream": "x"},
            "b": {"enabled": True, "proto": "tcp", "upstream": "x"}}}},
            "ports": {"x": 8410}, "network": {"loopback": "127.0.0.1"}}
        routes, errors = mod.render_routes(data)
        self.assertEqual(routes[:1], [r for r in routes if r["name"] == "a"])
        self.assertTrue(any("collides" in e for e in errors), errors)

    def test_forge_route_renders_the_generated_dropin(self):
        calls = self._run(_R_FORGE + _R_SSH)
        with open(self._dropin()) as f:
            dropin = f.read()
        self.assertTrue(dropin.startswith("# GENERATED by mios-tailscale-sync"))
        self.assertIn(f"FORGEJO__server__ROOT_URL=https://{_SELF}:8400/", dropin)
        self.assertIn("FORGEJO__server__HTTP_ADDR=127.0.0.1", dropin)
        self.assertIn("FORGEJO__server__SSH_LISTEN_HOST=127.0.0.1", dropin)
        self.assertIn("serve --bg --tcp=8410 tcp://127.0.0.1:8410", calls)
        self.assertIn("systemctl --no-block try-restart mios-forge.service", calls)
        self._run("")                 # the route is disabled again: drop-in retired
        self.assertFalse(os.path.exists(self._dropin()))

    def test_forge_dropin_follows_only_a_route_this_sync_serves(self):
        # A hand-made handler holds the forge listener: no drop-in, no restart.
        live = {"TCP": {"8400": {"HTTPS": True}}, "Web": {f"{_SELF}:8400": {"Handlers": {"/": {"Text": "hand-made"}}}}}
        calls = self._run(_R_FORGE, live=live)
        self.assertEqual([c for c in calls if c.startswith("serve")], [], "the hand-made handler was replaced")
        self.assertFalse(os.path.exists(self._dropin()), "a drop-in names a tailnet URL the sync did not serve")
        self.assertNotIn("systemctl --no-block try-restart mios-forge.service", calls)
        self._run(_R_FORGE)                                   # control: the same route, served
        self.assertTrue(os.path.exists(self._dropin()))
        self._run(_R_FORGE, live=live)                        # a conflict later retires it again
        self.assertFalse(os.path.exists(self._dropin()))

    def test_negative_public_host_cannot_inject_into_the_dropin(self):
        mod = load_sync(_SCRIPT)
        for bad in ("evil.example\n[Service]\nExecStartPre=/bin/sh -c id", "a b", "x" * 254, "-lead.example", ""):
            self.assertEqual(mod.dns_name(bad), "", repr(bad))
        with self.assertRaises(ValueError):
            mod.forge_dropin({"network": {"loopback": "127.0.0.1"}}, "evil\n[Service]", False, 8400)
        toml = _R_FORGE.replace("[tailscale.serve.routes.forge_http]",
                                '[tailscale.serve.routes.forge_http]')
        source = _serve_toml(self.d, toml, self.up).replace('public_host = ""',
                                                            'public_host = "evil.example\\n[Service]"')
        calls = run_sync(_SCRIPT, source, {}, module_patch={"QUADLET_RUNTIME_DIR": self.quadlet},
                         extra_env={"FAKE_SERVE": os.path.join(self.d, "absent.json")})
        self.assertFalse(os.path.exists(self._dropin()), "an invalid public_host was written into a unit")
        self.assertNotIn("systemctl daemon-reload", calls)

    def test_failed_serve_status_plans_nothing(self):
        # A hand-made Text handler holds 443 /, and a route the operator just disabled is owned
        # and live: a status that fails must neither overwrite the one nor forget the other.
        target = f"http://127.0.0.1:{self.up}"
        owned = {"https:443:/share/": target, "migrated:ttyd-expose": "done"}
        live = {"TCP": {"443": {"HTTPS": True}},
                "Web": {f"{_SELF}:443": {"Handlers": {"/": {"Text": "hand-made"}, "/share/": {"Proxy": target}}}}}
        for label, kw in (("rc=1", {"live": live, "env": {"FAKE_SERVE_RC": "1"}}),
                          ("garbled", {"raw": '{"TCP": {'}), ("not an object", {"raw": "[1, 2]"}),
                          ("empty output", {"raw": ""})):
            calls = self._run(_R_PORTAL, owned=owned, **kw)
            self.assertEqual(calls, [], f"{label}: planned from a failed serve status")
            self.assertEqual(self._state(), owned, f"{label}: the owned record changed")
        # control: the same live status, read, is a conflict for / and turns /share/ off
        calls = self._run(_R_PORTAL, owned=owned, live=live)
        self.assertEqual(calls, ["serve --https=443 --set-path=/share/ off"])
        # Negative control: the round-3 reader (a failed status reads as {}) overwrites and forgets.
        calls = self._run(_R_PORTAL, owned=owned, live=live, env={"FAKE_SERVE_RC": "1"},
                          code="m._serve_status = lambda ts_bin: {}")
        self.assertEqual(calls, [f"serve --bg --https=443 --set-path=/ {target}"])
        self.assertNotIn("https:443:/share/", self._owned(), "this control no longer shows the forget")

    def test_serve_status_reader_refuses_what_it_cannot_trust(self):
        mod = load_sync(_SCRIPT)
        import unittest.mock as um
        for out, rc, want in (('{"TCP": {}}', 0, {"TCP": {}}), ("null\n", 0, {}), ("{}", 0, {}),
                              ("{}", 1, None), ("", 0, None), ("[]", 0, None), ("{", 0, None)):
            res = subprocess.CompletedProcess([], rc, stdout=out, stderr="")
            with um.patch.object(mod.subprocess, "run", return_value=res):
                self.assertEqual(mod._serve_status("tailscale"), want, (out, rc))
        with um.patch.object(mod.subprocess, "run", side_effect=subprocess.TimeoutExpired("x", 10)):
            self.assertIsNone(mod._serve_status("tailscale"), "a timeout was read as an empty config")

    def test_plain_http_listener_is_a_conflict_never_adopted(self):
        target = f"http://127.0.0.1:{self.up}"
        live = {"TCP": {"443": {"HTTP": True}}, "Web": {f"{_SELF}:443": {"Handlers": {"/": {"Proxy": target}}}}}
        self.assertEqual(self._run(_R_PORTAL, live=live), [], "an http listener was overwritten")
        self.assertEqual(self._owned(), {}, "an http listener was adopted as an https route")
        mod = load_sync(_SCRIPT)
        live_keys, _foreign = mod.live_routes(live, _SELF)
        self.assertEqual(live_keys, {"http:443:/": target})
        want = [{"name": "portal", "proto": "https", "port": 443, "path": "/", "target": target}]
        self.assertEqual([a for a, _r in mod.plan_serve(want, live_keys, {})], ["conflict"])
        # Negative control: the round-3 reader keyed every Web handler https -- an adoption.
        old = {f"https:{hp.rpartition(':')[2]}:{p}": h["Proxy"] for hp, w in live["Web"].items()
               for p, h in w["Handlers"].items()}
        self.assertEqual([a for a, _r in mod.plan_serve(want, old, {})], ["adopt"])

    def test_portal_route_needs_every_auth_probe_to_ask_for_a_login(self):
        probes = 'auth_probe_paths = ["/", "/v1/chat/completions", "/a2a"]\n'
        route = _R_PORTAL + probes
        target = f"http://127.0.0.1:{self.up}"
        _Upstream.paths = {"/": 302, "/v1/chat/completions": 405, "/a2a": 405}   # api_require_auth off
        try:
            self.assertEqual(self._run(route), [], "the whole agent-pipe was published with an open /v1")
            # Negative control: the round-3 single probe of "/" publishes it.
            self.assertEqual(self._run(_R_PORTAL), [f"serve --bg --https=443 --set-path=/ {target}"])
            live = {"TCP": {"443": {"HTTPS": True}}, "Web": {f"{_SELF}:443": {"Handlers": {"/": {"Proxy": target}}}}}
            calls = self._run(route, live=live, owned={"https:443:/": target})
            self.assertEqual(calls, ["serve --https=443 --set-path=/ off"], "an owned route left open stayed published")
            _Upstream.paths = {"/": 302, "/v1/chat/completions": 401, "/a2a": 401}  # api_require_auth on
            self.assertEqual(self._run(route), [f"serve --bg --https=443 --set-path=/ {target}"])
        finally:
            _Upstream.paths = {}
        self.srv.shutdown()                                   # an upstream that is down: not published,
        self.srv.server_close()                               # and an owned route is kept
        self.assertEqual(self._run(route, owned={}), [])
        self.assertEqual(self._run(route, live=live, owned={"https:443:/": target}), [])
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Upstream)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def test_route_guarded_by_the_vendor_default_login_is_refused(self):
        target = f"http://127.0.0.1:{self.up}"
        ident = '[identity]\nusername = "fixop"\ndefault_password = "fixture-vendor-pw"\n'
        for route, operator, want in (
                (_R_PORTAL + 'credential = "portal"\n', "", []),
                (_R_PORTAL + 'credential = "portal"\n', '[identity]\ndefault_password = "fixture-op-pw"\n',
                 [f"serve --bg --https=443 --set-path=/ {target}"]),
                # ttyd never takes the operator credential: only its own login lifts the refusal
                (_R_PORTAL + 'credential = "ttyd"\n', '[identity]\ndefault_password = "fixture-op-pw"\n', []),
                (_R_PORTAL + 'credential = "ttyd"\n', '[ttyd]\nauth_pass = "fixture-ttyd-own"\n',
                 [f"serve --bg --https=443 --set-path=/ {target}"]),
                # Negative control: the same route without `credential` publishes the vendor default.
                (_R_PORTAL, "", [f"serve --bg --https=443 --set-path=/ {target}"])):
            toml = ident + _serve_toml(self.d, route, self.up)
            self.assertEqual(self._run("", toml=toml, operator=operator), want, (route[-24:], operator))
            os.unlink(os.path.join(self.d, "serve-owned.json"))

    def test_retired_ttyd_expose_handler_follows_the_ttyd_route_once(self):
        target = f"http://127.0.0.1:{self.up}"
        live = {"TCP": {str(self.up): {"HTTPS": True}},
                "Web": {f"{_SELF}:{self.up}": {"Handlers": {"/": {"Proxy": target}}}}}
        off = _serve_toml(self.d, "", self.up).replace("[tailscale.serve]\nenabled = true", "[tailscale.serve]\nenabled = false")
        calls = self._run("", live=live, toml=off)
        self.assertEqual(calls, [f"serve --https={self.up} --set-path=/ off"], "the retired handler stayed published")
        self.assertEqual(self._state(), {"migrated:ttyd-expose": "done"})
        self.assertEqual(self._run("", live=live, toml=off), [], "the migration ran twice")
        os.unlink(os.path.join(self.d, "serve-owned.json"))
        route = '[tailscale.serve.routes.ttyd_bash]\nenabled = true\nproto = "https"\npath = "/"\nupstream = "ttyd_bash"\nrequire_auth = true\n'
        self.assertEqual(self._run(route, live=live), [], "an enabled ttyd route re-added the handler")
        self.assertEqual(self._owned(), {f"https:{self.up}:/": target}, "the handler was not adopted")
        os.unlink(os.path.join(self.d, "serve-owned.json"))
        # Negative control: without the migration the handler is never touched.
        calls = self._run("", live=live, toml=off, code='m.legacy_ttyd_handler = lambda data: ("", "")')
        self.assertEqual(calls, [])

    def test_retired_tailnet_expose_true_keeps_the_handler_unowned(self):
        target = f"http://127.0.0.1:{self.up}"
        live = {"TCP": {str(self.up): {"HTTPS": True}},
                "Web": {f"{_SELF}:{self.up}": {"Handlers": {"/": {"Proxy": target}}}}}
        off = _serve_toml(self.d, "", self.up).replace("[tailscale.serve]\nenabled = true", "[tailscale.serve]\nenabled = false")
        kept = off + "[ttyd]\ntailnet_expose = true\n"            # a host tier that still sets the retired key
        self.assertEqual(self._run("", live=live, toml=kept), [], "the operator's phone terminal was turned off")
        self.assertEqual(self._state(), {"migrated:ttyd-expose": "done"}, "the kept handler was taken as owned")
        self.assertEqual(self._run("", live=live, toml=kept), [], "a later run removed it")

    def test_check_reports_without_changing(self):
        calls = self._run(_R_PORTAL, args=("--check",))
        self.assertEqual(calls, [])
        self.assertEqual(self._owned(), {})

    def test_negative_broken_operator_tier_applies_nothing(self):
        calls = self._run(_R_PORTAL, operator="[tailscale.serve\nenabled = true\n")
        self.assertEqual(calls, [], "a layer that does not parse still applied routes")

    def test_broken_operator_tier_stops_only_the_routes_never_the_mesh(self):
        # A typo in mios.html must not stop exit-node failover: the mesh takes nothing from
        # the operator tier, so it still syncs on vendor + host while no route changes.
        peers = {"a": _peer("pixel-7-pro", "100.122.33.108", True, True)}
        toml = _serve_toml(self.d, _R_PORTAL, self.up).replace("[tailscale]\n", '[tailscale]\nexit_nodes = ["pixel-7-pro"]\n', 1)
        calls = run_sync(_SCRIPT, toml, peers, operator="[tailscale.serve\nenabled = true\n",
                         extra_env={"FAKE_SERVE": os.path.join(self.d, "absent.json")},
                         module_patch={"QUADLET_RUNTIME_DIR": self.quadlet})
        self.assertEqual([c for c in calls if c.startswith("serve")], [], "routes applied from a broken tier")
        self.assertEqual([c for c in calls if c.startswith("set ")],
                         ["set --accept-routes=true --accept-dns=true --advertise-routes= "
                          "--exit-node=100.122.33.108 --exit-node-allow-lan-access=true"],
                         "a broken operator tier stopped the mesh sync")
        # A broken VENDOR layer still stops everything.
        calls = run_sync(_SCRIPT, "[tailscale\n" + toml, peers,
                         extra_env={"FAKE_SERVE": os.path.join(self.d, "absent.json")})
        self.assertEqual(calls, [], "a vendor layer that does not parse still applied something")


# ---------------------------------------------------------------- the read-only agent share
import pwd  # noqa: E402
import urllib.error  # noqa: E402
import urllib.request  # noqa: E402


def _free_port() -> int:
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestAgentShare(unittest.TestCase):
    """--share-serve as an unprivileged uid: world-readable regular files only, no link of
    any kind followed, sandboxed content types. Each refusal is the live exploit shape:
    an entry an agent plants in the group-writable share."""

    @classmethod
    def setUpClass(cls):
        cls.base = tempfile.mkdtemp(prefix="mios-share-")
        os.chmod(cls.base, 0o755)
        root = cls.root = os.path.join(cls.base, "share")
        os.mkdir(root, 0o755)
        os.chmod(root, 0o755)
        outside = os.path.join(cls.base, "outside")
        os.mkdir(outside, 0o755)
        os.chmod(outside, 0o755)

        def put(path, text, mode):
            with open(path, "w") as f:
                f.write(text)
            os.chmod(path, mode)
        put(os.path.join(root, "ok.txt"), "fixture-public\n", 0o644)
        put(os.path.join(root, "secret.txt"), "fixture-root-only\n", 0o600)
        put(os.path.join(root, "evil.html"), "<script>alert(1)</script>\n", 0o644)
        put(os.path.join(outside, "public-out.txt"), "fixture-outside\n", 0o644)
        put(os.path.join(outside, "root-only.txt"), "fixture-outside-secret\n", 0o600)
        os.symlink(os.path.join(outside, "root-only.txt"), os.path.join(root, "link-secret"))
        os.symlink(os.path.join(outside, "public-out.txt"), os.path.join(root, "link-public"))
        os.symlink(outside, os.path.join(root, "linkdir"))
        os.link(os.path.join(outside, "public-out.txt"), os.path.join(root, "hard.txt"))
        sub = os.path.join(root, "sub")
        os.mkdir(sub, 0o755)
        os.chmod(sub, 0o755)
        put(os.path.join(sub, "nested.txt"), "fixture-nested\n", 0o644)
        cls.big = "".join(f"{i:07d}\n" for i in range(40000))          # 320 kB: several chunks
        put(os.path.join(sub, "big.txt"), cls.big, 0o644)
        cls.port = _free_port()
        env = {k: v for k, v in os.environ.items() if not k.startswith("MIOS_")}
        user = None
        if os.geteuid() == 0:
            user = pwd.getpwnam("nobody").pw_uid
            can_read = subprocess.run([sys.executable, "-c", "import sys; open(sys.argv[1]).close(); "
                                       "open(sys.argv[2]).close()", _SCRIPT, os.path.join(root, "ok.txt")],
                                      user=user, group=user, extra_groups=[], capture_output=True)
            if can_read.returncode != 0:
                import shutil
                shutil.rmtree(cls.base, ignore_errors=True)
                raise unittest.SkipTest("this tree or TMPDIR is not readable by uid nobody "
                                        "(the share server never runs as root)")
        cls.proc = subprocess.Popen(
            [sys.executable, _SCRIPT, "--share-serve", "--root", root, "--bind", "127.0.0.1",
             "--port", str(cls.port), "--prefix", "/share/"],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            user=user, group=user, extra_groups=[] if user is not None else None)
        import time
        for _ in range(100):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{cls.port}/share/ok.txt", timeout=1).read()
                break
            except OSError:
                if cls.proc.poll() is not None:
                    raise AssertionError(f"the share server exited {cls.proc.returncode}: "
                                         f"{cls.proc.stdout.read().decode(errors='replace')[-400:]}") from None
                time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(timeout=10)
        import shutil
        shutil.rmtree(cls.base, ignore_errors=True)

    def _get(self, path):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=5) as r:
                return r.status, r.read().decode(), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, "", dict(e.headers)

    def test_positive_world_readable_files_and_listing(self):
        code, body, headers = self._get("/share/ok.txt")
        self.assertEqual((code, body), (200, "fixture-public\n"))
        self.assertIn("sandbox", headers.get("Content-Security-Policy", ""))
        self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(self._get("/share/sub/nested.txt")[:2], (200, "fixture-nested\n"))
        self.assertEqual(self._get("/ok.txt")[0], 200)              # prefix stripped or not
        code, listing, _h = self._get("/share/")
        self.assertEqual(code, 200)
        self.assertEqual(sorted(listing.split()), ["evil.html", "ok.txt", "sub/"],
                         "the listing names a link, a root-only or a hard-linked file")
        code, body, headers = self._get("/share/sub/big.txt")
        self.assertEqual((code, headers.get("Content-Length"), body), (200, str(len(self.big)), self.big),
                         "a file larger than one chunk was not served byte for byte")

    def test_shipped_unit_route_and_tmpfiles_keep_the_share_behind_a_proxy(self):
        """The share is reached only as: tailnet route -> loopback proxy -> a DynamicUser
        process. Negative: no route carries a directory, and the unit names no User=/root."""
        import tomllib
        with open(os.path.join(_ROOT, "usr/share/mios/mios.toml"), "rb") as f:
            ssot = tomllib.load(f)
        with open(os.path.join(_ROOT, "usr/lib/systemd/system/mios-agent-share.service")) as f:
            unit = f.read()
        route = ssot["tailscale"]["serve"]["routes"]["agent_share"]
        self.assertEqual((route["proto"], route["upstream"], route["enabled"]), ("https", "agent_share", False))
        for name, r in ssot["tailscale"]["serve"]["routes"].items():
            self.assertNotIn("dir", r, f"route {name} is a directory handler (root tailscaled follows links)")
        self.assertIn("\nDynamicUser=yes\n", unit)
        self.assertNotRegex(unit, r"(?m)^User=", "the share server was pinned to an account")
        self.assertIn(f"Environment=MIOS_PORTS_AGENT_SHARE={ssot['ports']['agent_share']}\n", unit)
        self.assertRegex(unit, r"(?m)^ExecStart=/usr/libexec/mios/mios-tailscale-sync --share-serve "
                               r"--bind \$\{MIOS_NETWORK_LOOPBACK\} --port \$\{MIOS_PORTS_AGENT_SHARE\}$")
        share = ssot["paths"]["srv_agent_share_dir"]
        # Law 7/8: the unit restates neither the share dir nor the route path; the server
        # reads both from the vendor + host tiers.
        for copy in (share, route["path"]):
            self.assertNotIn(copy, unit.split("\n[Service]", 1)[1], f"the unit restates {copy}")
        mod = load_sync(_SCRIPT)
        env = {"MIOS_VENDOR_TOML": os.path.join(_ROOT, "usr/share/mios/mios.toml"),
               "MIOS_VENDOR_TOML_D": os.path.join(self.base, "none.d"),
               "MIOS_HOST_TOML": os.path.join(self.base, "none.toml"), "MIOS_HOST_TOML_D": os.path.join(self.base, "none.d")}
        import unittest.mock as um
        with um.patch.dict(os.environ, env):
            self.assertEqual(mod._share_defaults(), (share, route["path"]))
            host = os.path.join(self.base, "host.toml")
            with open(host, "w") as f:
                f.write('[paths]\nsrv_agent_share_dir = "/srv/elsewhere"\n[tailscale.serve.routes.agent_share]\npath = "/s/"\n')
            with um.patch.dict(os.environ, {"MIOS_HOST_TOML": host}):
                self.assertEqual(mod._share_defaults(), ("/srv/elsewhere", "/s/"), "a host override is not followed")
        with open(os.path.join(_ROOT, "usr/lib/tmpfiles.d/50-mios.conf")) as f:
            tmpfiles = [ln.split() for ln in f if ln.startswith("d ")]
        self.assertIn(["d", share, "2775", "1000", "mios-ai", "-"], tmpfiles, "Law 2: the share is not declared")
        self.assertIn(["d", ssot["paths"]["run_credential_inbox_dir"], "1730", "root", "mios-ai", "-"], tmpfiles,
                      "the Portal's credential inbox is not declared (or is listable by its writers)")

    def test_negative_no_link_or_private_file_is_served(self):
        for path in ("/share/link-secret", "/share/link-public", "/share/linkdir/public-out.txt",
                     "/share/secret.txt", "/share/hard.txt", "/share/../outside/public-out.txt",
                     "/share/%2e%2e/outside/public-out.txt", "/share/sub/../../outside/public-out.txt"):
            code, body, _h = self._get(path)
            self.assertEqual(code, 404, f"{path} was served")
            self.assertNotIn("fixture-outside", body)

    def test_negative_html_is_never_rendered(self):
        code, body, headers = self._get("/share/evil.html")
        self.assertEqual(code, 200)
        self.assertEqual(headers.get("Content-Type"), "application/octet-stream")
        self.assertIn("sandbox", headers.get("Content-Security-Policy", ""))

    def test_negative_never_serves_as_root(self):
        if os.geteuid() != 0:
            self.skipTest("needs root to prove the refusal")
        r = subprocess.run([sys.executable, _SCRIPT, "--share-serve", "--root", self.root,
                            "--bind", "127.0.0.1", "--port", str(_free_port())],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 2)
        self.assertIn("refusing to serve as root", r.stdout + r.stderr)
        r = subprocess.run([sys.executable, _SCRIPT, "--share-serve", "--root", self.root,
                            "--bind", "0.0.0.0", "--port", str(_free_port())],
                           capture_output=True, text=True, timeout=30, user=pwd.getpwnam("nobody").pw_uid)
        self.assertEqual(r.returncode, 2, "a non-loopback bind was accepted")



# ------------------------------------------------------------------ the portal's probe list
import ast  # noqa: E402
import re  # noqa: E402
import tomllib  # noqa: E402

_AGENT_PIPE = os.path.join(_ROOT, "usr", "lib", "mios", "agent-pipe")
_ROUTE_METHODS = {"get", "post", "put", "delete", "patch", "head", "options", "api_route", "websocket"}
# Published without a login on purpose; every other ungated agent-pipe route is a probe.
_PUBLIC_ROUTES = {
    "/login": "the login page", "/portal/login": "the login POST",
    "/portal/logout": "drops the cookie and redirects to /login",
    "/sw.js": "the login page's service worker",
    "/portal/icon.svg": "PWA asset", "/portal/icon-192.png": "PWA asset",
    "/portal/icon-512.png": "PWA asset", "/portal/manifest.webmanifest": "PWA asset",
    "/portal/xterm.js": "vendored terminal asset", "/portal/xterm.css": "vendored terminal asset",
    "/portal/addon-fit.js": "vendored terminal asset",
    "/.well-known/agntcy-manifest.json": "discovery manifest, the class of the agent cards",
    "/passport/public-key": "a public key", "/passport/verify": "stateless passport check for any agent",
}


def agent_pipe_routes(root: str = _AGENT_PIPE) -> set:
    """{(method, path)} of every route decorator/registration in agent-pipe's non-test source."""
    out = set()
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in ("tests", "__pycache__", ".venv")]
        for f in fn:
            if not f.endswith(".py") or f.startswith("test_"):
                continue
            with open(os.path.join(dp, f), encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr in _ROUTE_METHODS | {"add_api_route", "add_websocket_route"}
                        and node.args and isinstance(node.args[0], ast.Constant)
                        and isinstance(node.args[0].value, str) and node.args[0].value.startswith("/")):
                    method = "websocket" if "websocket" in node.func.attr else node.func.attr
                    out.add((method, node.args[0].value))
    return out


def server_auth_scope(server: str = os.path.join(_AGENT_PIPE, "server.py")) -> tuple:
    """(_AUTH_GATED_PREFIXES, _AUTH_OPEN_PATHS) as server.py hands them to inbound_auth_mw."""
    with open(server, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    got = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id in ("_AUTH_GATED_PREFIXES", "_AUTH_OPEN_PATHS"):
            v = node.value
            if isinstance(v, ast.Call) and v.args:            # frozenset({...})
                v = v.args[0]
            got[node.targets[0].id] = set(ast.literal_eval(v))
    return got["_AUTH_GATED_PREFIXES"], got["_AUTH_OPEN_PATHS"]


def uncovered_routes(routes: set, probes: list, gated: set, open_paths: set) -> list:
    """The agent-pipe routes a front door on that upstream would publish unchecked."""
    missing = []
    for method, path in sorted(routes):
        if path in open_paths or path in _PUBLIC_ROUTES:
            continue
        if method == "websocket":   # HTTP middleware never sees a websocket: probe each one
            if "ws:" + re.sub(r"\{[^}]+\}", "0", path) not in probes:
                missing.append(f"websocket {path}")
            continue
        scope = next((g for g in sorted(gated) if path.startswith(g)), None)
        if scope:   # inbound_auth_mw gates the prefix: one probe under it proves the gate
            if not any(q.startswith(scope) and q not in open_paths for q in probes):
                missing.append(f"{path} (no probe under {scope})")
        elif re.sub(r"\{[^}]+\}", "0", path) not in probes:
            missing.append(path)
    return missing


class TestPortalProbeCoverage(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(_ROOT, "usr", "share", "mios", "mios.toml"), "rb") as fh:
            route = tomllib.load(fh)["tailscale"]["serve"]["routes"]["portal"]
        self.probes = route["auth_probe_paths"]
        self.routes = agent_pipe_routes()
        self.gated, self.open = server_auth_scope()

    def test_the_scan_sees_the_real_route_table(self):
        # Guard against a scanner that finds nothing and passes vacuously.
        for want in (("post", "/skills/run"), ("post", "/dci/deliberate"), ("get", "/kg/lookup"),
                     ("get", "/"), ("post", "/v1/chat/completions"), ("websocket", "/portal/term/{port}")):
            self.assertIn(want, self.routes)
        self.assertGreater(len(self.routes), 60)
        self.assertEqual(self.gated, {"/v1/", "/a2a"})

    def test_vendor_portal_probes_cover_every_agent_pipe_route(self):
        self.assertEqual(uncovered_routes(self.routes, self.probes, self.gated, self.open), [])

    def test_the_round_four_probe_list_misses_the_ungated_routes(self):
        # Negative control: v2's three probes let /skills/run, /dci/*, /kg/lookup through.
        missing = uncovered_routes(self.routes, ["/", "/v1/chat/completions", "/a2a"], self.gated, self.open)
        for path in ("/skills/run", "/dci/deliberate", "/kg/lookup", "/iostest", "/v1"):
            self.assertIn(path, missing)

    def test_a_new_route_is_never_missed(self):
        extra = self.routes | {("get", "/newcap/run"), ("websocket", "/newws")}
        self.assertEqual(uncovered_routes(extra, self.probes, self.gated, self.open),
                         ["/newcap/run", "websocket /newws"])

    def test_public_exemptions_still_name_real_routes(self):
        paths = {p for _m, p in self.routes}
        self.assertEqual(set(_PUBLIC_ROUTES) - paths, set(), "an exemption names a route that no longer exists")

    def test_a_websocket_is_never_covered_by_the_http_gate(self):
        # HTTP middleware never sees a websocket: only a ws: probe covers /v1/events/ws.
        no_ws = [p for p in self.probes if not p.startswith("ws:")]
        self.assertIn("websocket /v1/events/ws", uncovered_routes(self.routes, no_ws, self.gated, self.open))


if __name__ == "__main__":
    unittest.main()
