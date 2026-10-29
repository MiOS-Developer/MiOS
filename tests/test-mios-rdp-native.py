#!/usr/bin/python3 -I
# AI-hint: Tests for the native RDP server path (rdp-native-design.md 13): the display service of usr/libexec/mios/mios-rdp-rfb-bridge (--display-service JSON-lines protocol against a stateful fake Hyprland: attach creates the headless output with the [desktop.scaling] scale, layout follows DISP resizes/rotations, device_rule maps uinput devices to the remote output only, pace/refresh never touch HDMI-A-1, detach hands the cursor back and lingers/removes the output; negatives: bad device names, layout before attach, mirror refuses resizes), mios-rdp's backend selection (enabled xrdp|native truth table: [remote.rdp].server, missing native binary, crash-loop fallback marker, runtime override; legacy `enabled` unchanged) and native.json (priv TLS/auth, [::]:[ports].rdp, stats path, sub-tables), the SSOT == code defaults rule for [remote.rdp.native|redirect|naming] (mios-rdp-server --print-defaults), and opt-in live end-to-end checks with a private server on 127.0.0.1:18301 + FreeRDP in Xvfb + HEADLESS-MIOS-SCALETEST (MIOS_RDP_NATIVE_LIVE=1; harness in tests/fixtures/mios-rdp/): G1 AVC420 negotiated and acked / no-EGFX client gets bitmaps and no H.264, G6 colour bars dE2000, A1/A3/A4/A6 auth and TLS, D1 resize. MIOS_RDP_BRIDGE / MIOS_RDP / MIOS_RDP_SERVER_BIN point the suite at other copies.
# AI-related: usr/libexec/mios/mios-rdp, usr/libexec/mios/mios-rdp-rfb-bridge, src/mios-rdp-server/, usr/share/mios/mios.toml [remote.rdp] [remote.rdp.native] [remote.rdp.redirect] [remote.rdp.naming], tests/fixtures/mios-rdp/
import asyncio
import importlib.machinery
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
import unittest

sys.dont_write_bytecode = True
_ROOT = os.environ.get("MIOS_RDP_TEST_ROOT") or os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
_BRIDGE = os.environ.get("MIOS_RDP_BRIDGE") or os.path.join(_ROOT, "usr", "libexec", "mios", "mios-rdp-rfb-bridge")
_RDP = os.environ.get("MIOS_RDP") or os.path.join(_ROOT, "usr", "libexec", "mios", "mios-rdp")
_SERVER = os.environ.get("MIOS_RDP_SERVER_BIN") or os.path.join(_ROOT, "usr", "libexec", "mios", "rdp-native", "mios-rdp-server")
_SSOT = os.path.join(_ROOT, "usr", "share", "mios", "mios.toml")
_FIX = os.path.join(_ROOT, "tests", "fixtures", "mios-rdp")


def _load(path, name):
    loader = importlib.machinery.SourceFileLoader(name, path)
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


B = _load(_BRIDGE, "mios_rdp_bridge_native_test")
VOUT = "HEADLESS-MIOS-RDP"


class FakeHypr:
    """Stateful Hyprland stand-in: hl.monitor rules, headless create/remove, hl.device rules, cursor."""

    RULE = re.compile(r'hl\.monitor\(\{output = "([^"]+)", mode = "(\d+)x(\d+)(?:@([\d.]+))?", '
                      r'position = "(-?\d+)x(-?\d+)", scale = ([\d.]+), transform = (\d+)\}\)')
    DEV = re.compile(r'hl\.device\(\{name = "([^"]+)", output = "([^"]+)"\}\)')

    def __init__(self):
        self.mons = [{"name": "HDMI-A-1", "x": 0, "y": 0, "width": 1920, "height": 1080, "scale": 1.0,
                      "transform": 0, "refreshRate": 144.0, "disabled": False}]
        self.rules = {}
        self.devices = {}
        self.evals = []
        self.env = dict(os.environ)
        self.cursor = (900, 500)
        self.warps = []
        self.socket2_path = "/nonexistent/.socket2.sock"

    def resolve(self):
        return True

    def socket2(self):
        return self.socket2_path

    def monitors(self):
        return [dict(m) for m in self.mons]

    def monitor(self, name):
        return next((dict(m) for m in self.mons if m["name"] == name), None)

    def cursorpos(self):
        return self.cursor

    def warp_local(self, x, y, mon):
        self.warps.append((x, y, mon))
        self.cursor = (x, y)
        return True

    def _apply(self, name):
        r = self.rules.get(name)
        m = next((m for m in self.mons if m["name"] == name), None)
        if r and m:
            m.update(r)

    def eval(self, lua):
        self.evals.append(lua)
        mm = self.RULE.fullmatch(lua)
        if mm:
            n = mm.group(1)
            self.rules[n] = {"width": int(mm.group(2)), "height": int(mm.group(3)), "x": int(mm.group(5)),
                             "y": int(mm.group(6)), "scale": float(mm.group(7)), "transform": int(mm.group(8)),
                             "refreshRate": float(mm.group(4) or 60.0)}
            self._apply(n)
        dm = self.DEV.fullmatch(lua)
        if dm:
            self.devices[dm.group(1)] = dm.group(2)
        return True

    def ctl(self, *a):
        if a[:3] == ("output", "create", "headless"):
            self.mons.append({"name": a[3], "x": 0, "y": 0, "width": 1920, "height": 1080, "scale": 1.0,
                              "transform": 0, "refreshRate": 60.0, "disabled": False})
            self._apply(a[3])
            return "ok"
        if a[:2] == ("output", "remove"):
            self.mons = [m for m in self.mons if m["name"] != a[2]]
            return "ok"
        return ""


FAKE_POLICY = r"""
import json, sys
a = sys.argv[1:]
w, h = int(a[a.index("--width") + 1]), int(a[a.index("--height") + 1])
s = 2.5 if sorted((w, h)) == [1100, 2080] else 1.0
print(json.dumps({"version": 1, "scale": s, "source": "fallback-handheld"}))
"""


def _exe(d, name, body):
    p = os.path.join(d, name)
    with open(p, "w") as f:
        f.write(f"#!{sys.executable} -I\n" + body)
    os.chmod(p, 0o755)
    return p


class _DS(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mios-rdp-ds-")
        self.cfg = {"mode": "virtual", "virtual_output": VOUT, "virtual_attach": "below-center",
                    "virtual_workspace": "remote", "virtual_initial_mode": "1280x720", "linger_seconds": 0,
                    "target_hz": 90, "min_hz": 30, "refresh_pacing": "adaptive", "output_scale": True,
                    "virtual_scale": "auto", "runtime_dir": self.tmp, "hints_dir": self.tmp,
                    "primary_output": "HDMI-A-1", "reapply_delay_seconds": 0.01,
                    "display_scale_cmd": _exe(self.tmp, "mios-gen", FAKE_POLICY)}
        self.ds = B.DisplayService(dict(self.cfg))
        self.hypr = FakeHypr()
        self.ds.hypr = self.hypr
        self.loop = asyncio.new_event_loop()
        self.addCleanup(self.loop.close)

    def call(self, op, **kw):
        kw["op"] = op
        return self.loop.run_until_complete(self.ds.handle(kw))

    def hdmi(self):
        return self.hypr.monitor("HDMI-A-1")


class TestDisplayService(_DS):
    def test_positive_attach_creates_scaled_output_below_primary(self):
        before = self.hdmi()
        r = self.call("attach", width=1100, height=2080)
        self.assertTrue(r["ok"], r)
        self.assertEqual((r["output"], r["width"], r["height"], r["scale"]), (VOUT, 1100, 2080, 2.5))
        self.assertEqual((r["x"], r["y"]), (960 - 220, 1080))  # centred under HDMI-A-1, logical 440 wide
        self.assertEqual(r["hz"], 90)
        self.assertEqual(self.hdmi(), before)

    def test_positive_layout_follows_rotation(self):
        self.call("attach", width=1100, height=2080)
        r = self.call("layout", width=2080, height=1100)
        self.assertTrue(r["ok"])
        self.assertEqual((r["width"], r["height"], r["scale"]), (2080, 1100, 2.5))  # landscape phone keeps the scale
        self.assertEqual((r["x"], r["y"]), (960 - 416, 1080))  # re-centred: logical 832 wide
        self.assertEqual(self.hdmi()["x"], 0)

    def test_layout_rounds_to_even(self):
        self.call("attach", width=1100, height=2080)
        r = self.call("layout", width=1101, height=2079)
        self.assertEqual((r["width"], r["height"]), (1100, 2078))

    def test_negative_layout_before_attach(self):
        r = self.call("layout", width=800, height=600)
        self.assertFalse(r["ok"])
        self.assertEqual(self.hypr.monitor(VOUT), None)

    def test_positive_device_rule_maps_to_remote_output_only(self):
        self.call("attach", width=1100, height=2080)
        r = self.call("device_rule", name="mios-remote-touch")
        self.assertEqual(r, {"ok": True, "applied": True})
        self.assertEqual(self.hypr.devices, {"mios-remote-touch": VOUT})

    def test_negative_device_rule_rejects_injection(self):
        r = self.call("device_rule", name='x", output = "HDMI-A-1')
        self.assertFalse(r["ok"])
        self.assertEqual(self.hypr.devices, {})

    def test_pace_lowers_only_the_remote_refresh(self):
        self.call("attach", width=1100, height=2080)
        hz = [self.call("pace", fps=11.0)["hz"] for _ in range(4)]
        self.assertEqual(hz[-1], 30)
        self.assertEqual(self.hypr.monitor(VOUT)["refreshRate"], 30.0)
        self.assertEqual(self.hdmi()["refreshRate"], 144.0)
        self.ds.pacer.changed = float("-inf")
        self.assertEqual(self.call("pace", fps=88.0)["hz"], 90)

    def test_refresh_is_clamped_to_min_and_target(self):
        self.call("attach", width=1100, height=2080)
        self.assertEqual(self.call("refresh", hz=5)["hz"], 30)
        self.assertEqual(self.call("refresh", hz=500)["hz"], 90)

    def test_detach_hands_cursor_back_and_removes_output(self):
        self.call("attach", width=1100, height=2080)
        self.hypr.cursor = (900, 1500)  # the remote client left it on the phone output
        self.call("detach")
        self.loop.run_until_complete(asyncio.sleep(0.2))
        self.assertEqual(self.hypr.warps, [(900, 500, "HDMI-A-1")])
        self.assertIsNone(self.hypr.monitor(VOUT))

    def test_negative_detach_without_remote_cursor_does_not_warp(self):
        self.call("attach", width=1100, height=2080)
        self.call("detach")  # cursor still on HDMI-A-1
        self.loop.run_until_complete(asyncio.sleep(0.2))
        self.assertEqual(self.hypr.warps, [])

    def test_mirror_refuses_resize(self):
        self.ds.mode = "mirror"
        r = self.call("attach", width=800, height=600)
        self.assertEqual((r["output"], r["width"], r["height"]), ("HDMI-A-1", 1920, 1080))
        r = self.call("layout", width=640, height=480)
        self.assertEqual((r["width"], r["height"]), (1920, 1080))
        self.assertFalse(any("hl.monitor" in e for e in self.hypr.evals))

    def test_unknown_op(self):
        self.assertFalse(self.call("format_disk")["ok"])


class TestDisplayServiceProcess(unittest.TestCase):
    """The real `--display-service` process: hello over stdin/stdout, quit on EOF (no Hyprland needed)."""

    def test_hello_and_eof(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = os.path.join(d, "c.json")
            with open(cfg, "w") as f:
                json.dump({"mode": "virtual", "virtual_output": VOUT, "runtime_dir": d}, f)
            p = subprocess.run([sys.executable, "-I", _BRIDGE, "--display-service", "--config", cfg],
                               input='{"id": 7, "op": "hello"}\n', capture_output=True, text=True, timeout=20)
            line = json.loads(p.stdout.splitlines()[0])
            self.assertEqual((line["id"], line["ok"], line["output"]), (7, True, VOUT))
            self.assertEqual(p.returncode, 0)


FAKE_TOML = r"""
import json, os, sys
a = sys.argv[1:]
if a[:1] == ["--section"]:
    print(os.environ.get("FAKE_SECTION_" + a[1].replace(".", "_"), "{}"))
else:
    print(a[2] if len(a) > 2 else "")
"""


class TestBackendSelection(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mios-rdp-be-")
        self.toml = _exe(self.tmp, "mios-toml-get", FAKE_TOML)
        # Backend controls must not masquerade as real listener switches in the host journal.
        _exe(self.tmp, "logger", "pass\n")
        self.native = os.path.join(self.tmp, "native")
        self.state = os.path.join(self.tmp, "state")
        os.makedirs(self.native)
        os.makedirs(self.state)

    def run_rdp(self, server, *args, binary=True, marker=False, override=None, systemctl=None):
        b = os.path.join(self.native, "mios-rdp-server")
        if binary:
            _exe(self.native, "mios-rdp-server", "print('x')\n")
        elif os.path.exists(b):
            os.unlink(b)
        m = os.path.join(self.state, "fallback")
        if marker:
            with open(m, "w") as f:
                f.write("crash loop\n")
        elif os.path.exists(m):
            os.unlink(m)
        o = os.path.join(self.state, "server-override")
        if override:
            with open(o, "w") as f:
                f.write(override + "\n")
        elif os.path.exists(o):
            os.unlink(o)
        rdp = {"enabled": True, "operator_uid": os.getuid()}
        if server:
            rdp["server"] = server
        env = dict(os.environ, MIOS_TOML_GET=self.toml, MIOS_RDP_NATIVE_DIR=self.native, MIOS_RDP_STATE_DIR=self.state,
                   FAKE_SECTION_remote_rdp=json.dumps(rdp), MIOS_PORTS_RDP="8300")
        if systemctl is not None:
            _exe(self.tmp, "systemctl", systemctl)
            env["PATH"] = self.tmp + os.pathsep + env.get("PATH", "")
        return subprocess.run(["bash", _RDP, *args], env=env, capture_output=True, text=True, timeout=30)

    @unittest.skipUnless(os.getuid() == 0, "backend switching requires root")
    def test_native_switch_preserves_listener_when_helper_cannot_start(self):
        log = os.path.join(self.tmp, "systemctl.log")
        stub = f"import sys\nwith open({log!r}, 'a') as f: f.write(' '.join(sys.argv[1:]) + '\\n')\nsys.exit(1)\n"
        r = self.run_rdp("xrdp", "switch", "native", systemctl=stub)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("current server preserved", r.stderr)
        with open(log) as f:
            self.assertEqual(f.read().strip(), "start mios-rdp-priv.socket")
        self.assertFalse(os.path.exists(os.path.join(self.state, "server-override")))

    @unittest.skipUnless(os.getuid() == 0, "backend switching requires root")
    def test_native_start_failure_restores_compatibility_listener(self):
        log = os.path.join(self.tmp, "systemctl.log")
        stub = f"import sys\na = sys.argv[1:]\nwith open({log!r}, 'a') as f: f.write(' '.join(a) + '\\n')\nsys.exit(1 if 'start' in a and 'mios-rdp-native.service' in a else 0)\n"
        r = self.run_rdp("xrdp", "switch", "native", systemctl=stub)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("restored the xrdp listener", r.stderr)
        with open(os.path.join(self.state, "server-override")) as f:
            self.assertEqual(f.read().strip(), "xrdp")
        with open(log) as f:
            self.assertIn("start xrdp-sesman.service xrdp.service", f.read())

    def _user_bus_fixture(self, log, error):
        # No PAM/login or real systemd calls. The runuser stub records the
        # requested identity/environment and executes only our fake systemctl.
        _exe(self.tmp, "runuser", (
            "import json, os, sys\n"
            f"log = {log!r}\n"
            "a = sys.argv[1:]\n"
            "with open(log + '.runuser', 'a') as f: f.write(json.dumps(a) + '\\n')\n"
            "assert a[0] == '-u' and a[2] == '--'\n"
            "os.execvpe(a[3], a[3:], os.environ)\n"
        ))
        _exe(self.tmp, "ss", (
            "import json\n"
            f"log = {log!r}\n"
            "try:\n"
            "    calls = [json.loads(line) for line in open(log)]\n"
            "except FileNotFoundError:\n"
            "    calls = []\n"
            "if ['--user', 'start', 'mios-rdp-native.service'] in calls:\n"
            "    print('LISTEN 0 128 *:8300 *:* users:((\"mios-rdp-server\",pid=4242,fd=15))')\n"
        ))
        return (
            "import json, sys\n"
            f"log = {log!r}\n"
            "a = sys.argv[1:]\n"
            "with open(log, 'a') as f: f.write(json.dumps(a) + '\\n')\n"
            "if any(x == '--machine' or x.startswith('--machine=') for x in a):\n"
            f"    sys.stderr.write({error!r} + '\\n')\n"
            "    sys.exit(5)\n"
            "if a[:1] == ['--user'] and 'show' in a:\n"
            "    print(4242)\n"
            "sys.exit(0)\n"
        )

    @unittest.skipUnless(os.getuid() == 0, "backend switching requires root")
    def test_switch_user_bus_fallback_on_transport_error(self):
        for i, error in enumerate(("Remote peer disconnected", "Transport endpoint is not connected", "Connection reset by peer")):
            with self.subTest(error=error):
                log = os.path.join(self.tmp, f"um-transport-{i}.log")
                r = self.run_rdp("xrdp", "switch", "native", systemctl=self._user_bus_fixture(log, error))
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn("RDP server is now native", r.stderr)
                with open(log) as f:
                    calls = [json.loads(line) for line in f]
                self.assertTrue(any(any(x.startswith('--machine=') for x in a) for a in calls))
                self.assertIn(['--user', 'start', 'mios-rdp-native.service'], calls)
                with open(log + '.runuser') as f:
                    routed = [json.loads(line) for line in f]
                self.assertTrue(routed)
                for a in routed:
                    self.assertEqual(a[0], '-u')
                    self.assertIn(f'XDG_RUNTIME_DIR=/run/user/{os.getuid()}', a)
                    self.assertIn(f'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{os.getuid()}/bus', a)
                with open(os.path.join(self.state, 'server-override')) as f:
                    self.assertEqual(f.read().strip(), 'native')

    @unittest.skipUnless(os.getuid() == 0, "backend switching requires root")
    def test_switch_user_bus_fallback_not_used_for_real_unit_errors(self):
        for i, error in enumerate(("Unit mios-rdp-native.service not found", "Access denied", "Job for mios-rdp-native.service failed")):
            with self.subTest(error=error):
                log = os.path.join(self.tmp, f"um-unit-{i}.log")
                r = self.run_rdp("xrdp", "switch", "native", systemctl=self._user_bus_fixture(log, error))
                self.assertNotEqual(r.returncode, 0)
                self.assertIn(error, r.stderr)
                self.assertIn('restored the xrdp listener', r.stderr)
                with open(log) as f:
                    calls = [json.loads(line) for line in f]
                self.assertFalse(any(a[:1] == ['--user'] and not any(x.startswith('--machine=') for x in a) for a in calls))
                self.assertFalse(os.path.exists(log + '.runuser'), 'a genuine unit failure must not invoke fallback')
                with open(os.path.join(self.state, 'server-override')) as f:
                    self.assertEqual(f.read().strip(), 'xrdp')

    def test_default_is_xrdp(self):
        self.assertEqual(self.run_rdp(None, "backend").stdout.strip(), "xrdp")
        self.assertEqual(self.run_rdp(None, "enabled", "xrdp").returncode, 0)
        self.assertEqual(self.run_rdp(None, "enabled", "native").returncode, 1)

    def test_native_selected(self):
        self.assertEqual(self.run_rdp("native", "enabled", "native").returncode, 0)
        self.assertEqual(self.run_rdp("native", "enabled", "xrdp").returncode, 1)

    def test_negative_native_without_binary_falls_back(self):
        self.assertEqual(self.run_rdp("native", "backend", binary=False).stdout.strip(), "xrdp")
        self.assertEqual(self.run_rdp("native", "enabled", "xrdp", binary=False).returncode, 0)
        self.assertEqual(self.run_rdp("native", "enabled", "native", binary=False).returncode, 1)

    def test_negative_crash_loop_marker_falls_back(self):
        self.assertEqual(self.run_rdp("native", "backend", marker=True).stdout.strip(), "xrdp")
        self.assertEqual(self.run_rdp("native", "enabled", "xrdp", marker=True).returncode, 0)

    def test_runtime_override_both_ways(self):
        self.assertEqual(self.run_rdp("xrdp", "backend", override="native").stdout.strip(), "native")
        self.assertEqual(self.run_rdp("native", "backend", override="xrdp").stdout.strip(), "xrdp")
        self.assertEqual(self.run_rdp("xrdp", "backend", override="bogus").stdout.strip(), "xrdp")

    def test_legacy_enabled_unchanged(self):
        self.assertEqual(self.run_rdp("native", "enabled").returncode, 0)
        self.assertEqual(self.run_rdp("xrdp", "enabled").returncode, 0)

    def test_invalid_server_value_is_xrdp(self):
        r = self.run_rdp("wayvnc", "backend")
        self.assertEqual(r.stdout.strip(), "xrdp")
        self.assertIn("invalid [remote.rdp].server", r.stderr)

    def test_native_json(self):
        out = os.path.join(self.tmp, "nj")
        os.makedirs(out)
        r = self.run_rdp("native", "native-config", out)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(os.path.join(out, "native.json")) as f:
            d = json.load(f)
        self.assertEqual((d["listen"], d["tls_source"], d["auth"], d["priv_socket"]),
                         ("[::]:8300", "priv", "priv", "/run/mios-rdp-priv.sock"))
        self.assertEqual(d.get("bind_device"), "tailscale0")
        self.assertEqual(d["stats_path"], out + "/stats.json")
        self.assertIn("--display-service", d["display_service"])
        self.assertEqual(os.stat(os.path.join(out, "native.json")).st_mode & 0o777, 0o600)
        for sub in ("native", "redirect", "naming"):
            self.assertIsInstance(d[sub], dict)


class TestSsotDefaults(unittest.TestCase):
    @unittest.skipUnless(os.access(_SERVER, os.X_OK), "mios-rdp-server not built")
    def test_capture_probe_rejects_conflicting_pixel_layouts(self):
        r = subprocess.run([_SERVER, "--bench-capture", "must-not-capture", "0", "--bitmap", "--encode"],
                           capture_output=True, text=True, timeout=5)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("mutually exclusive", r.stderr)
        self.assertNotIn("capture session created", r.stderr)

    """Code defaults == SSOT values ([remote.rdp] server/native_binary in mios-rdp; the sub-tables in mios-rdp-server)."""

    @classmethod
    def setUpClass(cls):
        with open(_SSOT, "rb") as f:
            cls.ssot = tomllib.load(f)
        cls.rdp = cls.ssot["remote"]["rdp"]

    def test_server_keys(self):
        self.assertEqual(self.rdp.get("server", "xrdp"), "xrdp")
        self.assertEqual(self.rdp.get("native_binary", "auto"), "auto")
        with open(_RDP) as f:
            src = f.read()
        self.assertIn('"server": "xrdp", "native_binary": "auto"', src)

    @unittest.skipUnless(os.access(_SERVER, os.X_OK), "mios-rdp-server not built")
    def test_subtables_equal_code_defaults(self):
        d = json.loads(subprocess.run([_SERVER, "--print-defaults"], capture_output=True, text=True, timeout=30).stdout)
        for sub in ("native", "redirect", "naming"):
            ssot_sub = self.rdp.get(sub, {})
            self.assertEqual(set(ssot_sub.keys()), set(d[sub].keys()),
                             f"[remote.rdp.{sub}] key mismatch: SSOT={sorted(ssot_sub)} vs code={sorted(d[sub])}")
            for k, v in ssot_sub.items():
                self.assertIn(k, d[sub], f"[remote.rdp.{sub}].{k} has no code default")
                self.assertEqual(d[sub][k], v, f"[remote.rdp.{sub}].{k}: SSOT {v!r} != code {d[sub][k]!r}")

    def test_encoder_is_openh264_only(self):
        n = self.rdp.get("native", {})
        self.assertEqual(n.get("encoder", "openh264"), "openh264")
        self.assertFalse(n.get("udp_transport", False))

    def test_pam_configuration(self):
        for path in ("/usr/lib/pam.d/mios-rdp", "/etc/pam.d/mios-rdp"):
            self.assertTrue(os.path.exists(path), f"missing PAM config {path}")
            st = os.stat(path)
            self.assertEqual(oct(st.st_mode & 0o777), "0o644", f"bad mode on {path}")
            with open(path) as f:
                content = f.read()
            self.assertIn("auth       include      password-auth", content)
            self.assertIn("account    required     pam_succeed_if.so user ingroup mios-rdp", content)
            self.assertIn("account    required     pam_succeed_if.so uid >= 1000 quiet", content)

    @unittest.skipUnless(os.access(_SERVER, os.X_OK), "mios-rdp-server not built")
    def test_self_test_openh264_and_egl_and_invariants(self):
        r = subprocess.run([_SERVER, "--self-test"], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        d = json.loads(r.stdout)
        self.assertTrue(d.get("openh264", {}).get("ok"), f"openh264 self-test failed: {d}")
        self.assertTrue(d.get("egl", {}).get("ok"), f"egl self-test failed: {d}")
        self.assertTrue(d.get("invariants", {}).get("ok"), f"pipeline/redirect invariants failed: {d}")

    def test_build_json_matches_binaries(self):
        import hashlib
        rdp_dir = os.path.dirname(_SERVER)
        build_json = os.path.join(rdp_dir, "build.json")
        self.assertTrue(os.path.exists(build_json), f"missing {build_json}")
        with open(build_json) as f:
            meta = json.load(f)
        self.assertEqual(meta.get("self_test"), {"openh264": True, "egl": True})
        self.assertIn("nscodec", meta.get("features", []))
        for bname in ("mios-rdp-server", "mios-rdp-priv"):
            bpath = os.path.join(rdp_dir, bname)
            self.assertTrue(os.path.exists(bpath), f"missing {bpath}")
            h = hashlib.sha256()
            with open(bpath, "rb") as bf:
                while chunk := bf.read(65536):
                    h.update(chunk)
            self.assertEqual(meta["binaries"][bname], h.hexdigest(), f"SHA-256 mismatch for {bname}")

    def test_systemd_units_and_ssot_invariants(self):
        with open("/usr/lib/systemd/user/mios-rdp-native.service") as f:
            native_u = f.read()
        self.assertIn("RestartMode=direct", native_u)
        self.assertIn("StartLimitBurst=5", native_u)
        self.assertIn("OnFailure=mios-rdp-native-failed.service", native_u)
        self.assertIn("ExecCondition=/usr/libexec/mios/mios-rdp enabled native", native_u)
        with open("/usr/lib/systemd/user/mios-rdp-vnc.service") as f:
            vnc_u = f.read()
        self.assertIn("ExecCondition=/usr/libexec/mios/mios-rdp enabled xrdp", vnc_u)
        with open("/usr/lib/systemd/system/mios-rdp-priv@.service") as f:
            priv_u = f.read()
        self.assertIn("CAP_SYS_PTRACE", priv_u)
        self.assertIn("~process_vm_readv process_vm_writev", priv_u)
        priv_ssot = self.ssot["units"]["mios-rdp-priv@.service"]["Service"]
        self.assertIn("CAP_SYS_PTRACE", priv_ssot["CapabilityBoundingSet"])
        self.assertIn("~process_vm_readv process_vm_writev", priv_ssot["SystemCallFilter"])
        pkgs = self.ssot["packages"]["rdp-server-build"]["pkgs"]
        self.assertTrue(len(pkgs) >= 5, pkgs)

    def test_restart_mode_direct_waits_for_burst_limit(self):
        import time
        unit = "mios-test-rdp-rmd-burst.service"
        subprocess.run(["systemctl", "--user", "stop", unit], stderr=subprocess.DEVNULL)
        subprocess.run(["systemctl", "--user", "reset-failed", unit], stderr=subprocess.DEVNULL)
        with tempfile.TemporaryDirectory(prefix="mios-rdp-rmd-") as d:
            restarts_log = os.path.join(d, "restarts.log")
            cmd = [
                "systemd-run", "--user", f"--unit={unit}",
                "-p", "Type=exec", "-p", "Restart=on-failure", "-p", "RestartSec=150ms",
                "-p", "RestartMode=direct", "-p", "StartLimitIntervalSec=120", "-p", "StartLimitBurst=5",
                "/bin/sh", "-c", f"echo x >> {restarts_log}; exit 1"
            ]
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode != 0:
                self.skipTest(f"systemd-run --user unavailable: {r.stderr}")
            try:
                state, nrest = "", 0
                for _ in range(80):
                    st = subprocess.run(
                        ["systemctl", "--user", "show", unit, "-p", "ActiveState", "-p", "NRestarts", "--value"],
                        capture_output=True, text=True
                    ).stdout.splitlines()
                    if len(st) == 2:
                        state, nrest = st[0], int(st[1])
                        if state == "failed":
                            break
                    time.sleep(0.05)
                with open(restarts_log) as f:
                    runs = len(f.readlines())
                self.assertEqual(state, "failed")
                self.assertEqual(runs, 5, f"expected 5 runs before entering failed state, got {runs}")
                self.assertGreaterEqual(nrest, 4)
            finally:
                subprocess.run(["systemctl", "--user", "stop", unit], stderr=subprocess.DEVNULL)
                subprocess.run(["systemctl", "--user", "reset-failed", unit], stderr=subprocess.DEVNULL)

    @unittest.skipUnless(os.path.exists("/run/mios-rdp-priv.sock"), "mios-rdp-priv.sock not active")
    def test_tls_key_two_sided_control(self):
        import socket
        if os.getuid() != self.rdp.get("operator_uid", 1000):
            self.skipTest("TLS-KEY controls must run as the configured desktop operator")
        # Negative control: non-mios-rdp-server process requesting TLS-KEY must be denied with 0 fds.
        s = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        try:
            s.connect("/run/mios-rdp-priv.sock")
            s.sendall(b"TLS-KEY")
            msg, anc, _, _ = s.recvmsg(256, socket.CMSG_SPACE(16))
        finally:
            s.close()
        self.assertEqual(msg, b"DENY unauthorized caller exe")
        self.assertEqual(anc, [])
        # Positive control: genuine /usr/libexec/mios/rdp-native/mios-rdp-server receives TLS-KEY fds.
        with tempfile.TemporaryDirectory() as d:
            cfg = os.path.join(d, "native.json")
            with open(cfg, "w") as f:
                json.dump({"listen": "127.0.0.1:0", "bind_device": None, "tls_source": "priv",
                           "auth": "priv", "priv_socket": "/run/mios-rdp-priv.sock",
                           "display_service": ["/bin/false"], "stats_path": os.path.join(d, "stats.json")}, f)
            p = subprocess.run([_SERVER, "--config", cfg], capture_output=True, text=True, timeout=15)
            combined = p.stdout + p.stderr
            self.assertIn("TLS certificate loaded sha256=", combined, combined)
            self.assertNotIn("priv sentinel refused TLS-KEY", combined, combined)


@unittest.skipUnless(os.environ.get("MIOS_RDP_NATIVE_LIVE") == "1", "live native-server checks are opt-in (MIOS_RDP_NATIVE_LIVE=1)")
class TestLive(unittest.TestCase):
    """Private server on 127.0.0.1:18301 (test-auth build: MIOS_RDP_BIN) + FreeRDP in Xvfb + SCALETEST."""

    def run_fixture(self, name, *args, timeout=400):
        with tempfile.TemporaryDirectory(prefix="mios-rdp-live-") as live_dir:
            env = dict(os.environ, MIOS_RDP_LIVE_DIR=live_dir)
            p = subprocess.run([sys.executable, "-I", os.path.join(_FIX, name), *args], capture_output=True, text=True,
                               env=env, timeout=timeout)
            lines = [l for l in p.stdout.splitlines() if l.startswith("{")]
            return json.loads("".join(p.stdout[p.stdout.index("{"):].splitlines())) if lines else {"error": p.stderr[-2000:]}

    def test_g6_colour(self):
        r = self.run_fixture("g6.py", "g6-test", "bt709-full")
        self.assertTrue(r["guards_ok"])
        self.assertLessEqual(r["dE_max"], 3.0)
        n = self.run_fixture("g6.py", "g6-neg", "bt601-limited")
        self.assertGreater(n["dE_max"], 2 * r["dE_max"])

    def test_security(self):
        r = self.run_fixture("sec.py")
        self.assertTrue(r["guards_ok"])
        self.assertEqual(r["A3_tls"]["tls_version"], "TLSv1.3")
        self.assertTrue(r["A3_tls"]["cert_matches"])
        self.assertIn("tls_error", r["A3_tls10_refused"])
        self.assertTrue(r["A1"]["wrong_password_refused"] and r["A1"]["backoff_refused"] and r["A1"]["empty_refused"])
        self.assertTrue(r["A1"]["correct_after_backoff_attached"])
        self.assertTrue(r["A6_stalled_peer"]["no_capture_before_auth"])
        self.assertTrue(r["A4_source_refused"]["logged"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
