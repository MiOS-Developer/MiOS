#!/usr/bin/env python3
# AI-hint: Unit tests for usr/libexec/mios/mios-rdp-rfb-bridge: client RFB framing (unknown message type closes the connection), PointerEvent forwarding + signed clamping, the single-seat local-cursor policy (no warp per click/hover; one hand-back at detach only when the remote moved it), that hyprctl never runs on the event loop, and the access/firewall side: mios-rdp admits only allowed_users (default the operator), the upgrade scrub removes stale 3389/3390/5900-5999 openings from every zone while keeping the tailnet zone's mios-rdp service (fake firewall-cmd), mios-grd-setup opens no ports and is preset-disabled. Pure in-process / fake binaries: no Hyprland, no real firewalld, no cursor moves; the remote display's placement below the centre of the primary output (group-centred, re-centred after every resize, primary never touched, old right/far placement gone, parity with `mios-gen display-scale --attach-below`), the [desktop.scaling] policy call with legacy fallback and the wayvnc>=0.10 gate for scaled outputs, ServerStream resync after a wayvnc death mid-message, and end to end (fake xrdp <-> bridge <-> fake wayvnc + stateful fake Hyprland) that resizes/rotations/scale changes keep the xrdp connection and a wayvnc crash mid-update is recovered transparently (negative control: wayvnc_restarts = 0 ends the session); the pointer drawn into the frames on a scalable remote output (--render-cursor, cursor pseudo-encodings dropped from SetEncodings; render_cursor = false keeps the RFB cursor), a Hyprland config reload re-placing the phone output via the socket2 configreloaded event (negative: no event = stays displaced), no primary local output = 0,0 never a negative x, adaptive refresh pacing (Pacer: a client taking fewer than half the frames paces the virtual output down to >= 2x its rate, floor min_hz, one keeping up gets target_hz back at once; idle/noise/dwell/resize never change it; end to end a slow fake xrdp is paced 90 -> 30 without losing frames and back to 90 when it speeds up, negative control refresh_pacing = "fixed"), and an opt-in live HEADLESS-MIOS-SCALETEST reapply check (MIOS_RDP_LIVE_SCALETEST=1). MIOS_RDP_BRIDGE / MIOS_RDP / MIOS_RDP_TEST_ROOT point the suite at other copies.
# AI-related: /usr/libexec/mios/mios-rdp-rfb-bridge, /usr/libexec/mios/mios-rdp, /usr/libexec/mios-grd-setup, /usr/lib/systemd/system-preset/90-mios.preset, automation/45-firewall.sh, /usr/share/mios/mios.toml [remote.rdp]
from __future__ import annotations

import asyncio
import importlib.machinery
import importlib.util
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import tempfile
import sys
import time
import unittest

sys.dont_write_bytecode = True
_ROOT = os.environ.get("MIOS_RDP_TEST_ROOT") or os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
_BRIDGE = os.environ.get("MIOS_RDP_BRIDGE") or os.path.join(_ROOT, "usr", "libexec", "mios", "mios-rdp-rfb-bridge")


def _load(path: str, name: str):
    loader = importlib.machinery.SourceFileLoader(name, path)
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


B = _load(_BRIDGE, "mios_rdp_bridge")
VOUT = "HEADLESS-MIOS-RDP"
MONS = [
    {"name": "HDMI-A-1", "x": 0, "y": 0, "width": 1920, "height": 1080, "scale": 1.0, "transform": 0,
     "refreshRate": 144.0},
    {"name": VOUT, "x": 1920, "y": 0, "width": 1100, "height": 2080, "scale": 2.5, "transform": 0,
     "refreshRate": 60.0},
]


def ptr(mask: int, x: int, y: int) -> bytes:
    return struct.pack(">BBhh", 5, mask, x, y)


def key(down: int, sym: int) -> bytes:
    return struct.pack(">BBxxI", 4, down, sym)


def fbreq(x: int, y: int, w: int, h: int) -> bytes:
    return struct.pack(">BBHHHH", 3, 1, x, y, w, h)


def client_stream(on_pointer=None, fb=(1100, 2080)):
    cs = B.ClientStream(0, lambda *a: None, intercept=True, server_fb=lambda: fb, on_pointer=on_pointer)
    assert cs.feed(b"\x01") == b"\x01"  # ClientInit
    return cs


class FakeHypr:
    """Records calls; an optional blocking delay emulates a slow hyprctl."""

    def __init__(self, cursor=(1320, 495), delay=0.0):
        self.cursor = cursor
        self.delay = delay
        self.warps: list[tuple] = []
        self.calls = 0

    def _slow(self):
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)  # blocking on purpose

    def cursorpos(self):
        self._slow()
        return self.cursor

    def monitors(self):
        self._slow()
        return [dict(m) for m in MONS]

    def monitor(self, name):
        return next((m for m in self.monitors() if m["name"] == name), None)

    def warp_local(self, x, y, mon):
        self._slow()
        self.warps.append((x, y, mon))
        return True

    def eval(self, lua):
        self._slow()
        return True

    def ctl(self, *a):
        self._slow()
        return ""

    def resolve(self):
        return True


def bridge(hypr: FakeHypr):
    b = B.Bridge({"runtime_dir": "/nonexistent", "mode": "virtual", "virtual_output": VOUT,
                  "linger_seconds": 0, "keep_virtual_output": True})
    b.hypr = hypr
    return b


class TestClientFraming(unittest.TestCase):
    def test_positive_known_messages_forwarded_verbatim(self):
        cs = client_stream()
        data = key(1, 0x61) + key(0, 0x61) + fbreq(0, 0, 100, 100) + ptr(0, 10, 20)
        self.assertEqual(cs.feed(data), data)

    def test_positive_split_messages_reassembled(self):
        cs = client_stream()
        data = key(1, 0x61) + ptr(1, 5, 6)
        out = b"".join(cs.feed(data[i:i + 3]) for i in range(0, len(data), 3))
        self.assertEqual(out, data)

    def test_negative_unknown_type_closes_instead_of_resyncing(self):
        cs = client_stream()
        bogus = bytes([0x7F, 4, 0, 0, 0, 0, 0, 0x61])  # would resync as a KeyEvent after one byte
        with self.assertRaises(B.RFBFramingError):
            cs.feed(key(1, 0x61) + bogus + key(0, 0x61))
        with self.assertRaises(B.RFBFramingError):  # never falls back to raw passthrough
            cs.feed(key(0, 0x61))

    def test_negative_pipe_closes_upstream_on_unknown_type(self):
        class R:
            def __init__(self, chunks):
                self.chunks = list(chunks)

            async def read(self, n):
                return self.chunks.pop(0) if self.chunks else b""

        class W:
            def __init__(self):
                self.data, self.closed = bytearray(), False

            def write(self, d):
                assert not self.closed
                self.data += d

            async def drain(self):
                pass

            def close(self):
                self.closed = True

        cs = client_stream()
        w = W()
        good = key(1, 0x61)
        asyncio.run(B._pipe(R([good, bytes([0xEE]) + key(0, 0x61), key(1, 0x62)]), w, cs.feed))
        self.assertTrue(w.closed)
        self.assertEqual(bytes(w.data), good)  # nothing after the unknown byte reached wayvnc


class TestPointer(unittest.TestCase):
    def test_positive_hover_and_release_are_forwarded(self):
        seen = []
        cs = client_stream(on_pointer=seen.append)
        data = ptr(0, 10, 10) + ptr(1, 10, 10) + ptr(0, 11, 11) + ptr(0, 50, 60)
        self.assertEqual(cs.feed(data), data)
        self.assertEqual(seen, [0, 1, 0, 0])

    def test_negative_signed_and_oversized_coordinates_clamped(self):
        cs = client_stream(fb=(1100, 2080))
        out = cs.feed(ptr(0, -5, 3000) + ptr(0, 5000, -1))
        self.assertEqual(struct.unpack(">BBHH", out[:6]), (5, 0, 0, 2079))
        self.assertEqual(struct.unpack(">BBHH", out[6:12]), (5, 0, 1099, 0))


class TestCursorPolicy(unittest.TestCase):
    saved = (900, 500, "HDMI-A-1")

    def test_positive_restore_when_remote_left_cursor_on_remote_display(self):
        self.assertTrue(B.should_restore_local(True, self.saved, (2100, 300), MONS, VOUT))

    def test_negative_no_restore_cases(self):
        self.assertFalse(B.should_restore_local(False, self.saved, (2100, 300), MONS, VOUT))  # no remote input
        self.assertFalse(B.should_restore_local(True, None, (2100, 300), MONS, VOUT))  # nothing saved
        self.assertFalse(B.should_restore_local(True, self.saved, (400, 300), MONS, VOUT))  # operator took it back
        self.assertFalse(B.should_restore_local(True, (1, 1, "DP-9"), (2100, 300), MONS, VOUT))  # monitor gone

    def test_monitor_at_uses_logical_size(self):
        self.assertEqual(B.monitor_at(MONS, (1919, 1079)), "HDMI-A-1")
        self.assertEqual(B.monitor_at(MONS, (1920 + 439, 831)), VOUT)  # 1100x2080 @2.5 = 440x832
        self.assertIsNone(B.monitor_at(MONS, (1920 + 440, 10)))
        self.assertIsNone(B.monitor_at(MONS, None))

    def test_parse_cursorpos(self):
        self.assertEqual(B.parse_cursorpos("1320, 495"), (1320, 495))
        self.assertIsNone(B.parse_cursorpos("error: no instance"))
        self.assertIsNone(B.parse_cursorpos(""))

    def _session(self, hypr, events, cursor_at_detach):
        async def run():
            b = bridge(hypr)
            b.current = asyncio.current_task()
            await b._save_local()
            cs = client_stream(on_pointer=b._on_pointer)
            out = cs.feed(events)
            await asyncio.sleep(0.05)
            warps_during = list(hypr.warps)
            hypr.cursor = cursor_at_detach
            b.current = None
            b.detached()
            await b.detach_task
            return out, warps_during, list(hypr.warps), b
        return asyncio.run(run())

    def test_positive_one_handback_at_detach_never_per_event(self):
        hypr = FakeHypr(cursor=(900, 500))
        events = b"".join(ptr(m, 100, 100) for m in (1, 0, 0, 1, 0, 0, 0))
        out, during, after, b = self._session(hypr, events, cursor_at_detach=(2100, 300))
        self.assertEqual(out, events)
        self.assertEqual(during, [])  # no warp on any release or hover
        self.assertEqual(after, [(900, 500, "HDMI-A-1")])  # exactly one, at detach
        self.assertIsNone(b.saved_local)

    def test_negative_no_handback_when_operator_already_took_cursor_back(self):
        hypr = FakeHypr(cursor=(900, 500))
        _, during, after, _ = self._session(hypr, ptr(1, 1, 1) + ptr(0, 1, 1), cursor_at_detach=(10, 10))
        self.assertEqual((during, after), ([], []))

    def test_negative_no_handback_without_remote_pointer_input(self):
        hypr = FakeHypr(cursor=(900, 500))
        _, during, after, _ = self._session(hypr, key(1, 0x61) + key(0, 0x61), cursor_at_detach=(2100, 300))
        self.assertEqual((during, after), ([], []))


class TestEventLoopNeverBlocks(unittest.TestCase):
    """A slow hyprctl (blocking 0.6 s per call) must not stall the loop that forwards frames."""

    DELAY = 0.6

    @staticmethod
    async def _max_stall(coro) -> float:
        stall = 0.0
        done = False

        async def ticker():
            nonlocal stall
            last = time.monotonic()
            while not done:
                await asyncio.sleep(0.01)
                now = time.monotonic()
                stall = max(stall, now - last)
                last = now

        t = asyncio.ensure_future(ticker())
        await asyncio.sleep(0.02)
        await coro
        done = True
        await t
        return stall

    def test_positive_bridge_paths_run_hyprctl_off_loop(self):
        hypr = FakeHypr(cursor=(900, 500), delay=self.DELAY)
        feed_s = []

        async def work():
            b = bridge(hypr)
            b.current = asyncio.current_task()
            await b._save_local()
            t0 = time.monotonic()
            cs = client_stream(on_pointer=b._on_pointer)
            cs.feed(b"".join(ptr(m, 5, 5) for m in (1, 0, 0, 0)))
            feed_s.append(time.monotonic() - t0)
            hypr.cursor = (2100, 300)
            b.current = None
            b.detached()
            await b.detach_task
        stall = asyncio.run(self._max_stall(work()))
        self.assertLess(feed_s[0], 0.05)
        self.assertGreaterEqual(hypr.calls, 5)  # save (2) + restore (2) + warp (1) all happened
        self.assertEqual(len(hypr.warps), 1)
        self.assertLess(stall, 0.25, f"event loop stalled {stall:.2f}s")

    def test_negative_control_detector_sees_blocking_call(self):
        async def blocking():
            FakeHypr(delay=self.DELAY).cursorpos()  # the pre-fix pattern: hyprctl on the loop
        stall = asyncio.run(self._max_stall(blocking()))
        self.assertGreater(stall, self.DELAY * 0.8)


# ---------------------------------------------------------------- access + firewall (mios-rdp)

_RDP = os.environ.get("MIOS_RDP") or os.path.join(_ROOT, "usr", "libexec", "mios", "mios-rdp")


def _read(path: str) -> str:
    with open(path) as f:
        return f.read()

FAKE_FW = r"""#!/usr/bin/env python3
import json, os, sys
P = os.environ["FAKE_FW_STATE"]
st = json.load(open(P))
a = [x for x in sys.argv[1:] if x != "--permanent"]
st["log"].append(a)
def kv(pfx):
    for x in a:
        if x.startswith(pfx + "="):
            return x.split("=", 1)[1]
    return None
def save(rc=0, out=None):
    with open(P + ".new", "w") as f:
        json.dump(st, f)
    os.replace(P + ".new", P)
    if out is not None:
        print(out)
    sys.exit(rc)
if a == ["--state"]: save()
if a == ["--reload"]: save()
if a == ["--get-services"]: save(out=" ".join(st["services"]))
if a == ["--get-zones"]: save(out=" ".join(st["zones"]))
if a == ["--get-default-zone"]: save(out=st["default"])
if kv("--get-zone-of-interface"):
    z = st["iface"].get(kv("--get-zone-of-interface"))
    save(0 if z else 2, z or "no zone")
if kv("--new-service"):
    st["services"].setdefault(kv("--new-service"), []); save()
svc = kv("--service")
if svc:
    ports = st["services"][svc]
    if "--get-ports" in a: save(out=" ".join(ports))
    if kv("--add-port"): ports.append(kv("--add-port"))
    if kv("--remove-port") and kv("--remove-port") in ports: ports.remove(kv("--remove-port"))
    save()
z = kv("--zone")
if z:
    zz = st["zones"][z]
    if "--list-rich-rules" in a: save(out="\n".join(zz["rich"]))
    if kv("--query-port"): save(0 if kv("--query-port") in zz["ports"] else 1)
    for key, fld in (("--remove-service", "services"), ("--remove-port", "ports"), ("--remove-rich-rule", "rich")):
        v = kv(key)
        if v is not None:
            if v not in zz[fld]: save(1)
            zz[fld].remove(v); save()
    for key, fld in (("--add-service", "services"), ("--add-rich-rule", "rich")):
        v = kv(key)
        if v is not None:
            if v not in zz[fld]: zz[fld].append(v)
            save()
save(2)
"""

FAKE_TOML = r"""#!/usr/bin/env python3
import os, sys
if sys.argv[1:2] == ["--section"]:
    print(os.environ["FAKE_RDP_JSON"])
else:
    print(sys.argv[-1])
"""

FAKE_GETENT = r"""#!/usr/bin/env python3
import sys
DB = {"mios": "mios:x:1000:1000::/home/mios:/bin/bash",
      "alice": "alice:x:1001:1001::/home/alice:/bin/bash",
      "root": "root:x:0:0::/root:/bin/bash",
      "toor": "toor:x:0:0::/root:/bin/bash"}
if len(sys.argv) == 3 and sys.argv[1] == "passwd" and sys.argv[2] in DB:
    print(DB[sys.argv[2]]); sys.exit(0)
sys.exit(2)
"""


def _bin(d: str, name: str, body: str) -> None:
    p = os.path.join(d, name)
    with open(p, "w") as f:
        f.write(body)
    os.chmod(p, 0o755)


class _FakeEnv(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mios-rdp-test-")
        self.bin = os.path.join(self.tmp, "bin")
        os.mkdir(self.bin)
        _bin(self.bin, "firewall-cmd", FAKE_FW)
        _bin(self.bin, "mios-toml-get", FAKE_TOML)
        _bin(self.bin, "getent", FAKE_GETENT)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def env(self, **extra):
        e = {"PATH": self.bin + ":/usr/bin:/bin", "MIOS_TOML_GET": os.path.join(self.bin, "mios-toml-get"),
             "MIOS_PORTS_RDP": "8300", "HOME": self.tmp, "LANG": "C"}
        e.update(extra)
        return e


class TestFirewallScrub(_FakeEnv):
    RICH_PUBLIC = 'rule family="ipv4" source address="100.64.0.0/10" service name="mios-rdp" accept'

    def _run(self, zones, cfg=None):
        state = {"default": "public", "iface": {"tailscale0": "FedoraServer"},
                 "services": {"ssh": [], "mios-rdp": ["3389/tcp"]}, "zones": zones, "log": []}
        sp = os.path.join(self.tmp, "fw.json")
        with open(sp, "w") as f:
            json.dump(state, f)
        cfg = cfg or {"enabled": True}
        r = subprocess.run(["bash", _RDP, "firewall"], capture_output=True, text=True, timeout=60,
                           env=self.env(FAKE_FW_STATE=sp, FAKE_RDP_JSON=json.dumps(cfg)))
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(_read(sp)), r.stderr

    @staticmethod
    def _z(ports=(), services=(), rich=()):
        return {"ports": list(ports), "services": list(services), "rich": list(rich)}

    def test_positive_tailnet_zone_keeps_only_mios_rdp_service(self):
        st, err = self._run({"public": self._z(services=["ssh"]), "FedoraServer": self._z(services=["ssh"])})
        self.assertEqual(st["services"]["mios-rdp"], ["8300/tcp"])
        self.assertIn("mios-rdp", st["zones"]["FedoraServer"]["services"])
        self.assertNotIn("mios-rdp", st["zones"]["public"]["services"])
        self.assertEqual(st["zones"]["public"]["rich"], [])
        self.assertIn("open on: zone FedoraServer", err)

    def test_negative_stale_openings_scrubbed_from_every_zone(self):
        stale = ["3389/tcp", "3390/tcp", "5900-5999/tcp"]
        st, err = self._run({
            "public": self._z(ports=stale + ["22/tcp", "8080/tcp", "8300/tcp"], services=["ssh", "mios-rdp"],
                              rich=[self.RICH_PUBLIC]),
            "FedoraServer": self._z(ports=["3389/tcp", "5900-5999/tcp"], services=["mios-rdp"]),
            "trusted": self._z(ports=["3390/tcp"]),
        })
        for z, zz in st["zones"].items():
            for p in stale + ["8300/tcp"]:
                self.assertNotIn(p, zz["ports"], f"{p} still open in {z}")
        self.assertEqual(st["zones"]["public"]["ports"], ["22/tcp", "8080/tcp"])  # unrelated openings kept
        self.assertEqual(st["zones"]["public"]["rich"], [])
        self.assertEqual(st["zones"]["public"]["services"], ["ssh"])
        self.assertEqual(st["zones"]["FedoraServer"]["services"], ["mios-rdp"])
        self.assertIn("removed stale 3389/tcp from zone public", err)

    def test_negative_disabled_closes_everything(self):
        st, err = self._run({"public": self._z(), "FedoraServer": self._z(services=["mios-rdp"])},
                            cfg={"enabled": False})
        self.assertNotIn("mios-rdp", st["zones"]["FedoraServer"]["services"])
        self.assertIn("closed everywhere", err)


class TestAllowedUsers(_FakeEnv):
    def _admitted(self, users: str) -> tuple[list[str], str]:
        script = ("set -euo pipefail\nlog() { printf '%s\\n' \"$*\" >&2; }\n"
                  + re.search(r"^admitted_users\(\) \{.*?^\}\n", _read(_RDP), re.S | re.M).group(0)
                  + 'OP_USER=mios\nRDP_ALLOWED_USERS="$1"\nadmitted_users\n')
        r = subprocess.run(["bash", "-c", script, "t", users], capture_output=True, text=True,
                           timeout=30, env=self.env())
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.split(), r.stderr

    def test_positive_default_is_operator_only(self):
        self.assertEqual(self._admitted("")[0], ["mios"])

    def test_positive_listed_user_admitted(self):
        self.assertEqual(self._admitted("alice mios")[0], ["alice", "mios"])

    def test_negative_root_unknown_and_malformed_rejected(self):
        got, err = self._admitted("root toor ghost 'x;rm' Alice")
        self.assertEqual(got, ["mios"])  # nothing valid -> falls back to the operator, never empty/open
        self.assertIn("root is never admitted", err)
        self.assertIn("'ghost' is not an account", err)

    def test_render_logs_admitted_and_sesman_group_check(self):
        src = _read(_RDP)
        self.assertIn("AlwaysGroupCheck=true", src)
        self.assertIn("TerminalServerUsers=${ALLOWED_GROUP}", src)
        self.assertIn("admitted: ${ADMITTED}", src)


class TestStaticSurfaces(unittest.TestCase):
    @staticmethod
    def _code(path: str) -> list[str]:
        return [l for l in _read(path).splitlines() if l.strip() and not l.lstrip().startswith("#")]

    def test_grd_setup_opens_no_ports(self):
        code = self._code(os.path.join(_ROOT, "usr", "libexec", "mios-grd-setup"))
        self.assertTrue(any("set-tls-key" in l for l in code))  # positive: still does its TLS job
        self.assertFalse([l for l in code if "firewall-cmd" in l])  # negative: no 3389/8080 opening
        self.assertTrue(any("remote.rdp enabled" in l for l in code))

    def test_grd_setup_preset_disabled(self):
        lines = self._code(os.path.join(_ROOT, "usr", "lib", "systemd", "system-preset", "90-mios.preset"))
        self.assertIn("disable mios-grd-setup.service", lines)
        self.assertNotIn("enable mios-grd-setup.service", lines)
        tgt = _read(os.path.join(_ROOT, "usr", "lib", "systemd", "system", "mios-firstboot.target"))
        self.assertNotIn("mios-grd-setup", re.search(r"^Wants=.*$", tgt, re.M).group(0))

    def test_45_firewall_never_opens_rdp_or_vnc(self):
        code = self._code(os.path.join(_ROOT, "automation", "45-firewall.sh"))
        bad = [l for l in code if "firewall-cmd" in l and re.search(r"RDP|3389|3390|59\d\d", l)]
        self.assertEqual(bad, [])
        self.assertTrue(any("--add-port=${MIOS_PORTS_SSH}/tcp" in l for l in code))

    def test_ssot_defaults(self):
        import tomllib
        with open(os.path.join(_ROOT, "usr", "share", "mios", "mios.toml"), "rb") as f:
            r = tomllib.load(f)["remote"]["rdp"]
        self.assertEqual(r["allowed_users"], [])
        self.assertEqual(r["allowed_group"], "")
        self.assertEqual(sorted(r["stale_ports"]), ["3389/tcp", "3390/tcp", "5900-5999/tcp"])
        self.assertEqual(r["virtual_attach"], "below-center")  # remote displays attach under the primary
        self.assertNotIn("virtual_position", r)  # old right-of-layout / far-away placement removed
        self.assertNotIn("far_gap_px", r)
        self.assertIs(r["output_scale"], True)
        self.assertEqual(r["wayvnc_binary"], "auto")
        # Operator 2026-10-16: target 90 Hz, never capped at 30; dmabuf capture (no CPU readback on
        # Hyprland's loop); delivered-FPS counter on.
        self.assertEqual(r["target_hz"], 90)
        self.assertNotIn("max_fps", r)
        self.assertIs(r["capture_dmabuf"], True)
        self.assertEqual(r["render_cursor"], "auto")
        self.assertGreater(r["stats_seconds"], 0)
        # 2026-10-17: the refresh follows what the client takes (90 when it keeps up), floor 30. Until the
        # staged [remote.rdp] fragment is merged the keys are absent and mios-rdp's defaults (the same
        # values, TestWayvncArgv.test_pacing_keys_reach_the_bridge) apply; once present they must match.
        self.assertEqual(r.get("refresh_pacing", "adaptive"), "adaptive")
        self.assertEqual(r.get("min_hz", 30), 30)
        self.assertLessEqual(r.get("min_hz", 30), r["target_hz"])



# ---------------------------------------------------------------- placement, scale policy, wayvnc restart

class StatefulHypr:
    """A tiny Hyprland: hl.monitor rules + headless create/remove, with real layout state.
    Writes the virtual output's pixel mode to size_file (read by the fake wayvnc)."""

    RULE = re.compile(r'hl\.monitor\(\{output = "([^"]+)", mode = "(\d+)x(\d+)(?:@([\d.]+))?", '
                      r'position = "(-?\d+)x(-?\d+)", scale = ([\d.]+), transform = (\d+)\}\)')

    def __init__(self, size_file=None, primary=("HDMI-A-1", 0, 0, 1920, 1080)):
        name, x, y, w, h = primary
        self.mons = [{"name": name, "x": x, "y": y, "width": w, "height": h, "scale": 1.0,
                      "transform": 0, "refreshRate": 144.0, "disabled": False}]
        self.rules: dict[str, dict] = {}
        self.evals: list[str] = []
        self.size_file = size_file
        self.env = dict(os.environ)
        self.cursor = (900, 500)
        self.warps: list = []

    def resolve(self):
        return True

    socket2_path = "/nonexistent/.socket2.sock"

    def socket2(self):
        return self.socket2_path

    def config_reload(self, name):
        """What a Hyprland config reload did to the bridge's output: its runtime rule is gone and the
        projection's catch-all ("preferred, auto, 1") puts it right of the layout at 1920x1080."""
        self.rules.pop(name, None)
        m = next(m for m in self.mons if m["name"] == name)
        m.update(width=1920, height=1080, scale=1.0, x=1920, y=0, transform=0, refreshRate=60.0)
        if self.size_file:
            with open(self.size_file + ".tmp", "w") as f:
                f.write("1920x1080")
            os.replace(self.size_file + ".tmp", self.size_file)

    def monitors(self):
        return [dict(m) for m in self.mons]

    def monitor(self, name):
        return next((dict(m) for m in self.mons if m["name"] == name), None)

    def cursorpos(self):
        return self.cursor

    def warp_local(self, x, y, mon):
        self.warps.append((x, y, mon))
        return True

    def _apply(self, name):
        r = self.rules.get(name)
        m = next((m for m in self.mons if m["name"] == name), None)
        if r is None or m is None:
            return
        m.update(r)
        if self.size_file and name.startswith("HEADLESS-"):
            with open(self.size_file + ".tmp", "w") as f:
                f.write(f"{m['width']}x{m['height']}")
            os.replace(self.size_file + ".tmp", self.size_file)

    def eval(self, lua):
        self.evals.append(lua)
        mm = self.RULE.fullmatch(lua)
        if mm:
            name = mm.group(1)
            self.rules[name] = {"width": int(mm.group(2)), "height": int(mm.group(3)), "x": int(mm.group(5)),
                                "y": int(mm.group(6)), "scale": float(mm.group(7)), "transform": int(mm.group(8)),
                                "refreshRate": float(mm.group(4) or 60.0)}  # headless default 60 Hz
            self._apply(name)
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


def _exe(d: str, name: str, body: str) -> str:
    p = os.path.join(d, name)
    with open(p, "w") as f:
        f.write(f"#!{sys.executable} -I\n" + body)
    os.chmod(p, 0o755)
    return p


# Policy stand-in for `mios-gen display-scale`: phone modes -> 2.5, anything else -> 1.
FAKE_POLICY = r"""
import json, sys
a = sys.argv[1:]
w, h = int(a[a.index("--width") + 1]), int(a[a.index("--height") + 1])
s = 2.5 if sorted((w, h)) == [1100, 2080] else 1.0
print(json.dumps({"version": 1, "scale": s, "logical_width": round(w / s), "logical_height": round(h / s),
                  "mobile": s > 1, "source": "dpi-hint" if "--dpi" in a else "fallback-handheld"}))
"""

# RFB 3.8 server standing in for wayvnc: ServerInit from the size file, a 4x4 raw rect per update
# request (plus an ExtendedDesktopSize rect whenever the size file changed). If <dir>/crash exists
# when an update is requested, it sends HALF of a 2-rect update and dies (like 0.9.1's fatal
# cursor-capture exit in the middle of a frame).
FAKE_WAYVNC = r"""
import os, socket, struct, sys, time
sock = sys.argv[-1]
d = os.environ["FAKE_WAYVNC_DIR"]
n = len(os.listdir(d + "/starts")) + 1
open(f"{d}/starts/{n}", "w").close()
with open(f"{d}/argv-{n}", "w") as f:
    f.write("\n".join(sys.argv[1:]))
def size():
    w, h = open(d + "/size").read().split("x")
    return int(w), int(h)
srv = socket.socket(socket.AF_UNIX)
srv.bind(sock)
srv.listen(1)
c, _ = srv.accept()
def rd(k):
    b = b""
    while len(b) < k:
        x = c.recv(k - len(b))
        if not x:
            sys.exit(0)
        b += x
    return b
c.sendall(b"RFB 003.008\n"); rd(12)
c.sendall(b"\x01\x01"); rd(1); c.sendall(struct.pack(">I", 0)); rd(1)
cur = size()
pf = struct.pack(">BBBBHHHBBB3x", 32, 24, 0, 1, 255, 255, 255, 16, 8, 0)
c.sendall(struct.pack(">HH", *cur) + pf + struct.pack(">I", 4) + b"fake")
FIX = {0: 20, 3: 10, 4: 8, 5: 6}
while True:
    t = rd(1)[0]
    if t in FIX:
        body = rd(FIX[t] - 1)
    elif t == 2:
        hdr = rd(3); k = struct.unpack(">H", hdr[1:3])[0]; encs = rd(4 * k)
        with open(f"{d}/enc-{n}", "wb") as f:
            f.write(encs)
        continue
    elif t == 6:
        hdr = rd(7); rd(struct.unpack(">I", hdr[3:7])[0]); continue
    else:
        sys.exit(9)
    if t != 3:
        continue
    time.sleep(0.01)
    if os.path.exists(d + "/crash"):
        os.unlink(d + "/crash")
        c.sendall(struct.pack(">BxH", 0, 2) + struct.pack(">HHHHi", 0, 0, 4, 4, 0) + bytes(20))
        os._exit(3)
    rects = []
    new = size()
    if new != cur:
        cur = new
        rects.append(struct.pack(">HHHHi", 0, 0, cur[0], cur[1], -308) + struct.pack(">B3x", 1)
                     + struct.pack(">IHHHHI", 0, 0, 0, cur[0], cur[1], 0))
    rects.append(struct.pack(">HHHHi", 0, 0, 4, 4, 0) + bytes(64))
    c.sendall(struct.pack(">BxH", 0, len(rects)) + b"".join(rects))
"""


class XrdpClient:
    """The xrdp side (RFB 3.3, libvnc's message set), parsing every server message strictly: a
    rect outside the framebuffer or an unknown encoding is an error (xrdp drops the session)."""

    def __init__(self, r, w):
        self.r, self.w = r, w
        self.fb = (0, 0)
        self.frames = 0
        self.eds: list[tuple] = []
        self.error = ""
        self.closed = False
        self.delay = 0.0  # seconds between an update and the next request (a client slower than the display)

    async def handshake(self):
        assert await self.r.readexactly(12) == b"RFB 003.003\n"
        self.w.write(b"RFB 003.003\n")
        assert struct.unpack(">I", await self.r.readexactly(4))[0] == 1
        self.w.write(b"\x01")
        w, h = struct.unpack(">HH", await self.r.readexactly(4))
        await self.r.readexactly(16)
        await self.r.readexactly(struct.unpack(">I", await self.r.readexactly(4))[0])
        self.fb = (w, h)
        pf = struct.pack(">BBBBHHHBBB3x", 32, 24, 0, 1, 255, 255, 255, 16, 8, 0)
        encs = [0, 1, -239, -223, -308]
        self.w.write(struct.pack(">B3x", 0) + pf
                     + struct.pack(">BxH", 2, len(encs)) + b"".join(struct.pack(">i", e) for e in encs)
                     + fbreq(0, 0, w, h))
        await self.w.drain()

    def set_size(self, w, h):
        self.w.write(struct.pack(">BxHHBx", 251, w, h, 1) + struct.pack(">IHHHHI", 1, 0, 0, w, h, 0))

    async def run(self):
        try:
            while True:
                t = (await self.r.readexactly(1))[0]
                if t != 0:
                    self.error = f"server message type {t}"
                    return
                await self.r.readexactly(1)
                n = struct.unpack(">H", await self.r.readexactly(2))[0]
                for _ in range(n):
                    x, y, w, h, enc = struct.unpack(">HHHHi", await self.r.readexactly(12))
                    if enc == -308:
                        ns = (await self.r.readexactly(4))[0]
                        await self.r.readexactly(16 * ns)
                        self.fb = (w, h)
                        self.eds.append((x, y, w, h))
                        continue
                    if enc == -223:
                        self.fb = (w, h)
                        continue
                    if x + w > self.fb[0] or y + h > self.fb[1] or not w or not h:
                        self.error = f"rect {x},{y} {w}x{h} outside {self.fb}"
                        return
                    if enc == 0:
                        await self.r.readexactly(w * h * 4)
                    elif enc == 1:
                        await self.r.readexactly(4)
                    elif enc == -239:
                        await self.r.readexactly(w * h * 4 + ((w + 7) // 8) * h)
                    else:
                        self.error = f"encoding {enc}"
                        return
                self.frames += 1
                if self.delay:
                    await asyncio.sleep(self.delay)
                self.w.write(fbreq(0, 0, *self.fb))
                await self.w.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            self.closed = True


class _Live(unittest.TestCase):
    """Bridge._session end to end: fake xrdp <-> bridge <-> fake wayvnc, StatefulHypr."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mios-rdp-bridge-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        os.mkdir(os.path.join(self.tmp, "starts"))
        self.size = os.path.join(self.tmp, "size")
        self.hypr = StatefulHypr(self.size)
        self.policy = _exe(self.tmp, "mios-gen", FAKE_POLICY)
        self.wayvnc = _exe(self.tmp, "wayvnc", FAKE_WAYVNC)
        self.logs: list[str] = []

    def cfg(self, **kw):
        c = {"runtime_dir": self.tmp, "mode": "virtual", "virtual_output": VOUT, "linger_seconds": 0,
             "keep_virtual_output": True, "vnc_sock": os.path.join(self.tmp, "w.sock"),
             "wayvnc_argv": [self.wayvnc], "display_scale_cmd": self.policy, "scale_dpi_hint": False,
             "output_scale": True, "virtual_attach": "below-center", "primary_output": "HDMI-A-1",
             "virtual_initial_mode": "1280x720", "virtual_workspace": ""}
        c.update(kw)
        return c

    def starts(self):
        return len(os.listdir(os.path.join(self.tmp, "starts")))

    def run_session(self, cfg, script):
        self.hypr.env["FAKE_WAYVNC_DIR"] = self.tmp
        orig_log, orig_hint = B.log, B.xrdp_dpi_hint
        B.xrdp_dpi_hint = lambda since: None  # never read the real xrdp journal
        self.addCleanup(setattr, B, "xrdp_dpi_hint", orig_hint)
        B.log = lambda m: self.logs.append(m)
        self.addCleanup(setattr, B, "log", orig_log)

        async def main():
            b = B.Bridge(cfg)
            b.hypr = self.hypr
            b.wayvnc_ok = True
            s1, s2 = socket.socketpair()
            br, bw = await asyncio.open_unix_connection(sock=s1)
            cr, cw = await asyncio.open_unix_connection(sock=s2)
            sess = asyncio.ensure_future(b.attach(br, bw))
            cl = XrdpClient(cr, cw)
            await asyncio.wait_for(cl.handshake(), 10)
            reader = asyncio.ensure_future(cl.run())
            try:
                await asyncio.wait_for(script(b, cl), 30)
            finally:
                cw.close()
                await asyncio.wait_for(sess, 15)
                reader.cancel()
            return b, cl

        return asyncio.run(main())

    @staticmethod
    async def until(cond, timeout=8.0):
        t0 = time.monotonic()
        while not cond():
            if time.monotonic() - t0 > timeout:
                raise AssertionError("condition not reached")
            await asyncio.sleep(0.02)

    def vout(self):
        return self.hypr.monitor(VOUT)


class TestPlacement(unittest.TestCase):
    def _bridge(self, hypr, **kw):
        cfg = {"runtime_dir": "/nonexistent", "mode": "virtual", "virtual_output": VOUT,
               "primary_output": "HDMI-A-1"}
        cfg.update(kw)
        b = B.Bridge(cfg)
        b.hypr = hypr
        return b

    def test_positive_below_center_for_every_scale(self):
        hypr = StatefulHypr()
        b = self._bridge(hypr)
        for w, h, s in ((1100, 2080, 2.5), (2080, 1100, 2.5), (1080, 2400, 3.0), (1280, 720, 1.0), (2560, 1600, 2.0)):
            lw = round(w / s)
            self.assertEqual(b._layout(hypr.monitors(), (w, h, s))[VOUT], (0 + (1920 - lw) // 2, 0 + 1080))
        self.assertEqual(B.attach_below((0, 0, 1920, 1080), [440]), [(740, 1080)])

    def test_positive_primary_by_name_not_position(self):
        hypr = StatefulHypr(primary=("DP-2", 1920, 0, 2560, 1440))
        hypr.mons.insert(0, {"name": "HDMI-A-1", "x": 0, "y": 0, "width": 1920, "height": 1080, "scale": 1.0,
                             "transform": 0, "refreshRate": 60.0, "disabled": False})
        b = self._bridge(hypr, primary_output="DP-2")
        self.assertEqual(b._layout(hypr.monitors(), (1100, 2080, 2.5))[VOUT], (1920 + (2560 - 440) // 2, 1440))
        b = self._bridge(hypr, primary_output="")  # no SSOT primary: the local output at 0,0
        self.assertEqual(b._layout(hypr.monitors(), (1100, 2080, 2.5))[VOUT], (740, 1080))

    def test_positive_group_centred_side_by_side(self):
        hypr = StatefulHypr()
        hypr.mons.append({"name": VOUT + "-2", "x": 5000, "y": 0, "width": 1280, "height": 720, "scale": 1.0,
                          "transform": 0, "refreshRate": 60.0, "disabled": False})
        b = self._bridge(hypr)
        pos = b._layout(hypr.monitors(), (1100, 2080, 2.5))
        total = 440 + 1280
        self.assertEqual(pos[VOUT], ((1920 - total) // 2, 1080))
        self.assertEqual(pos[VOUT + "-2"], ((1920 - total) // 2 + 440, 1080))
        b.position = (0, 0)
        b._place((1100, 2080, 2.5))  # moves the sibling, never a local output
        self.assertEqual(hypr.monitor(VOUT + "-2")["x"], (1920 - total) // 2 + 440)
        self.assertFalse([e for e in hypr.evals if "HDMI-A-1" in e])

    def test_negative_old_right_and_far_placement_gone(self):
        hypr = StatefulHypr()
        for attach in ("below-center", "right", "far"):
            b = self._bridge(hypr, virtual_attach=attach, virtual_position="right", far_gap_px=10000)
            x, y = b._layout(hypr.monitors(), (1100, 2080, 2.5))[VOUT]
            self.assertNotEqual((x, y), (1920, 0))  # flush right of the layout
            self.assertLess(x, 10000)  # far away
            self.assertEqual(y, 1080)
            self.assertLess(x + 440, 1920 + 1)  # under the primary, sharing its bottom edge
        b = self._bridge(hypr, virtual_attach="300x2000")  # explicit override still possible
        self.assertEqual(b._layout(hypr.monitors(), (1100, 2080, 2.5))[VOUT], (300, 2000))

    @unittest.skipUnless(os.access("/usr/bin/mios-gen", os.X_OK), "mios-gen not installed")
    def test_positive_matches_mios_gen_display_scale(self):
        out = subprocess.run(["/usr/bin/mios-gen", "display-scale", "--width", "1100", "--height", "2080",
                              "--dpi", "568", "--remote", "--attach-below", "0,0,1920,1080", "--format", "json"],
                             capture_output=True, text=True, timeout=20)
        self.assertEqual(out.returncode, 0, out.stderr)
        j = json.loads(out.stdout)
        self.assertEqual([(j["position"]["x"], j["position"]["y"])],
                         B.attach_below((0, 0, 1920, 1080), [j["logical_width"]]))
        s, info = B.policy_scale({"display_scale_cmd": "/usr/bin/mios-gen"}, 1100, 2080, 568.0, "HEADLESS-MIOS-SCALETEST")
        self.assertEqual(s, j["scale"])
        self.assertNotEqual(info.get("source"), "legacy")


class TestScalePolicy(unittest.TestCase):
    def test_policy_cli_used_and_legacy_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            pol = _exe(d, "mios-gen", FAKE_POLICY)
            s, info = B.policy_scale({"display_scale_cmd": pol}, 1100, 2080, 568.0, VOUT)
            self.assertEqual((s, info["source"]), (2.5, "dpi-hint"))  # positive
            bad = _exe(d, "bad", "import sys\nprint('nope', file=sys.stderr)\nsys.exit(2)\n")
            s, info = B.policy_scale({"display_scale_cmd": bad, "virtual_scale": "1.5"}, 1100, 2080, None, VOUT)
            self.assertEqual(info["source"], "legacy")  # negative: CLI failure never breaks the attach
            self.assertEqual(s, B.valid_scale(1100, 2080, 1.5))

    def test_wayvnc_version_gate(self):
        with tempfile.TemporaryDirectory() as d:
            new = _exe(d, "new", "print('wayvnc: v0.11-dev-b286ab9 (HEAD)')\nprint('neatvnc: v1.1-dev')\n")
            old = _exe(d, "old", "print('wayvnc: 0.9.1')\n")
            self.assertEqual(B.wayvnc_version(new), (0, 11, 0))
            self.assertTrue(B.scaled_capture_ok(B.wayvnc_version(new)))
            self.assertEqual(B.wayvnc_version(old), (0, 9, 1))
            self.assertFalse(B.scaled_capture_ok(B.wayvnc_version(old)))
            self.assertFalse(B.scaled_capture_ok(B.wayvnc_version(os.path.join(d, "missing"))))

    def test_display_hint_written_atomically_and_removed(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "mios-display-hints.json")
            with open(p, "w") as f:
                json.dump({"outputs": {"DP-1": {"dpi": 110}}}, f)
            B.write_display_hint(d, VOUT, 568.4)
            self.assertEqual(json.load(open(p)), {"outputs": {"DP-1": {"dpi": 110}, VOUT: {"dpi": 568}}})
            self.assertEqual(os.stat(p).st_mode & 0o777, 0o600)
            B.write_display_hint(d, VOUT, None)
            self.assertEqual(json.load(open(p)), {"outputs": {"DP-1": {"dpi": 110}}})
            self.assertEqual([f for f in os.listdir(d) if f.endswith(".tmp")], [])


def _server_stream(fb=(100, 50)):
    ss = B.ServerStream()
    init = struct.pack(">HH", *fb) + struct.pack(">BBBBHHHBBB3x", 32, 24, 0, 1, 255, 255, 255, 16, 8, 0) \
        + struct.pack(">I", 1) + b"x"
    out = ss.feed(init)
    return ss, out


class TestServerResync(unittest.TestCase):
    def _parse_ok(self, data: bytes, fb=(100, 50)) -> int:
        """Strict parse of complete FramebufferUpdates (after ServerInit + initial EDS); returns frames."""
        i, frames = 0, 0
        while i < len(data):
            self.assertEqual(data[i], 0)
            n = struct.unpack(">H", data[i + 2:i + 4])[0]
            i += 4
            for _ in range(n):
                x, y, w, h, enc = struct.unpack(">HHHHi", data[i:i + 12])
                i += 12
                if enc == -308:
                    i += 4 + 16 * data[i]
                    continue
                self.assertLessEqual(x + w, fb[0])
                self.assertLessEqual(y + h, fb[1])
                self.assertEqual(enc, 0)
                i += w * h * 4
            frames += 1
        self.assertEqual(i, len(data))
        return frames

    def test_positive_mid_payload_completed_with_filler(self):
        ss, init = _server_stream()
        upd = struct.pack(">BxH", 0, 3) + struct.pack(">HHHHi", 0, 0, 4, 4, 0) + bytes(64)
        part = upd[:4 + 12 + 30]  # header + first rect header + 30 of 64 payload bytes
        out = ss.feed(part)
        fill = ss.resync()
        self.assertEqual(len(out) + len(fill), 4 + 3 * 12 + 64 + 2 * 4)  # rest of rect 1, two 1x1 rects
        self.assertTrue(ss.boundary)
        nxt = ss.feed(struct.pack(">BxH", 0, 1) + struct.pack(">HHHHi", 0, 0, 2, 2, 0) + bytes(16))
        eds_len = len(B.eds_update(0, 0, 1, 1))
        self.assertEqual(self._parse_ok(init[-eds_len:] + out + fill + nxt), 3)

    def test_positive_partial_header_never_forwarded(self):
        ss, _ = _server_stream()
        self.assertEqual(ss.feed(b"\x00\x00"), b"")  # 2 of 4 header bytes: nothing reached xrdp
        self.assertEqual(ss.resync(), b"")
        ss.feed(struct.pack(">BxH", 0, 2) + struct.pack(">HHHHi", 0, 0, 4, 4, 0)[:5])
        fill = ss.resync()  # header went out, rect 1 header did not: two filler rects owed
        self.assertEqual(len(fill), 2 * (12 + 4))

    def test_positive_queued_injection_flushed_at_resync(self):
        ss, _ = _server_stream()
        ss.feed(struct.pack(">BxH", 0, 1) + struct.pack(">HHHHi", 0, 0, 4, 4, 0) + bytes(10))
        ss.writer = None
        ss.request(80, 40)  # not at a boundary: queued
        fill = ss.resync()
        self.assertTrue(fill.endswith(B.eds_update(1, 0, 80, 40)))

    def test_negative_unframed_stream_cannot_resume(self):
        ss, _ = _server_stream()
        ss.raw = True
        with self.assertRaises(B.RFBFramingError):
            ss.resync()
        with self.assertRaises(B.RFBFramingError):
            B.ServerStream().resync()  # before ServerInit

    def test_logical_serverinit_shown_as_real_size(self):
        ss = B.ServerStream(expect=(1100, 2080))
        init = struct.pack(">HH", 440, 832) + bytes(16) + struct.pack(">I", 0)
        out = ss.feed(init)
        self.assertEqual(struct.unpack(">HH", out[:4]), (1100, 2080))
        self.assertEqual(ss.target, (1100, 2080))
        # wayvnc's stale logical EDS is rewritten to the real size; the real one clears the mask
        eds = B.eds_update(0, 0, 440, 832)
        self.assertEqual(struct.unpack(">HH", ss.feed(eds)[4 + 4:4 + 8]), (1100, 2080))
        ss.feed(B.eds_update(0, 0, 1100, 2080))
        self.assertIsNone(ss.target)


class TestResizeKeepsConnection(_Live):
    def test_positive_resize_rotation_scale_keep_xrdp_connected(self):
        async def script(b, cl):
            await self.until(lambda: cl.frames >= 3)
            self.assertEqual(self.vout()["y"], 1080)  # created below the primary already
            cl.set_size(1100, 2080)
            await self.until(lambda: cl.fb == (1100, 2080) and self.vout()["scale"] == 2.5)
            await self.until(lambda: (self.vout()["x"], self.vout()["y"]) == (740, 1080))
            f = cl.frames
            await self.until(lambda: cl.frames >= f + 3)
            cl.set_size(2080, 1100)  # rotation
            await self.until(lambda: cl.fb == (2080, 1100) and self.vout()["width"] == 2080)
            await self.until(lambda: (self.vout()["x"], self.vout()["y"]) == ((1920 - 832) // 2, 1080))
            f = cl.frames
            await self.until(lambda: cl.frames >= f + 3)
            self.assertFalse(cl.closed)
            self.assertEqual(cl.error, "")

        b, cl = self.run_session(self.cfg(), script)
        self.assertEqual(cl.error, "")
        self.assertEqual(self.starts(), 1)  # no wayvnc restart needed for a resize
        self.assertIn((1, 0, 1100, 2080), cl.eds)  # SetDesktopSize answered (reason 1, status 0)
        self.assertIn((1, 0, 2080, 1100), cl.eds)
        self.assertEqual(self.hypr.monitor("HDMI-A-1")["x"], 0)
        self.assertFalse([e for e in self.hypr.evals if "HDMI-A-1" in e])  # primary never touched

    def test_negative_old_wayvnc_keeps_scale_one(self):
        async def script(b, cl):
            b.wayvnc_ok = False
            b.wayvnc_ver = (0, 9, 1)
            await self.until(lambda: cl.frames >= 2)
            cl.set_size(1100, 2080)
            await self.until(lambda: cl.fb == (1100, 2080) and self.vout()["width"] == 1100)
            await self.until(lambda: any("now 1100x2080" in m for m in self.logs))

        self.run_session(self.cfg(), script)
        self.assertEqual(self.vout()["scale"], 1.0)
        self.assertEqual((self.vout()["x"], self.vout()["y"]), ((1920 - 1100) // 2, 1080))
        self.assertTrue(any("exits on a scaled output" in m for m in self.logs))


class TestWayvncCrashRecovered(_Live):
    def test_negative_capture_failure_recovered_not_fatal(self):
        async def script(b, cl):
            cl.set_size(1100, 2080)
            await self.until(lambda: cl.fb == (1100, 2080) and self.vout()["scale"] == 2.5)
            await self.until(lambda: cl.frames >= 3)
            open(os.path.join(self.tmp, "crash"), "w").close()  # wayvnc dies mid-update
            await self.until(lambda: self.starts() == 2)
            f = cl.frames
            await self.until(lambda: cl.frames >= f + 5)  # frames flow again from the new wayvnc
            self.assertFalse(cl.closed)

        b, cl = self.run_session(self.cfg(render_cursor=False), script)
        self.assertEqual(cl.error, "")  # xrdp never saw a broken or out-of-bounds message
        self.assertEqual(self.starts(), 2)
        with open(os.path.join(self.tmp, "enc-2"), "rb") as f:  # xrdp's SetEncodings replayed
            self.assertEqual(struct.unpack(">5i", f.read()), (0, 1, -239, -223, -308))
        self.assertTrue(any("wayvnc restarted" in m for m in self.logs))
        self.assertEqual(cl.fb, (1100, 2080))

    def test_negative_control_without_restarts_session_ends(self):
        async def script(b, cl):
            await self.until(lambda: cl.frames >= 3)
            open(os.path.join(self.tmp, "crash"), "w").close()
            await self.until(lambda: cl.closed)

        b, cl = self.run_session(self.cfg(wayvnc_restarts=0), script)
        self.assertTrue(cl.closed)
        self.assertEqual(self.starts(), 1)
        self.assertTrue(any("ending the session" in m for m in self.logs))



# ---------------------------------------------------------------- pointer on a scaled output

class TestRenderCursor(unittest.TestCase):
    def test_policy(self):
        v = {"mode": "virtual", "output_scale": True}
        self.assertTrue(B.overlay_cursor(v, ["/w"], True))  # positive: scalable remote output
        self.assertFalse(B.overlay_cursor(v, ["/w"], False))  # negative: wayvnc 0.9 keeps scale 1
        self.assertFalse(B.overlay_cursor({**v, "output_scale": False}, ["/w"], True))
        self.assertFalse(B.overlay_cursor({**v, "mode": "mirror"}, ["/w"], True))
        self.assertFalse(B.overlay_cursor({**v, "render_cursor": False}, ["/w"], True))
        self.assertFalse(B.overlay_cursor({**v, "render_cursor": "off"}, ["/w"], True))
        self.assertTrue(B.overlay_cursor({**v, "mode": "mirror", "render_cursor": True}, ["/w"], False))
        self.assertTrue(B.overlay_cursor({**v, "output_scale": False}, ["/w", "--render-cursor"], False))

    def test_cursor_encodings_dropped_only_when_overlaid(self):
        encs = (0, 1, -239, -240, -223, -308)
        msg = struct.pack(">BxH", 2, len(encs)) + b"".join(struct.pack(">i", e) for e in encs)
        cs = B.ClientStream(0, lambda *a: None, intercept=True, drop_encodings=B.CURSOR_ENCODINGS)
        out = cs.feed(b"\x01" + msg + key(1, 65))
        self.assertEqual(out[1:5], struct.pack(">BxH", 2, 4))
        self.assertEqual(struct.unpack(">4i", out[5:21]), (0, 1, -223, -308))
        self.assertEqual(out[21:], key(1, 65))  # framing intact after the shortened message
        self.assertEqual(cs.setup[2], out[1:21])  # the filtered list is what a restarted wayvnc gets
        plain = client_stream()  # negative control: no overlay, SetEncodings forwarded verbatim
        self.assertEqual(plain.feed(msg), msg)


class TestRenderCursorLive(_Live):
    def _script(self):
        async def script(b, cl):
            await self.until(lambda: cl.frames >= 3 and os.path.exists(os.path.join(self.tmp, "enc-1")))
        return script

    def test_positive_scaled_virtual_output_draws_the_pointer(self):
        self.run_session(self.cfg(), self._script())
        with open(os.path.join(self.tmp, "argv-1")) as f:
            self.assertIn("--render-cursor", f.read().split("\n"))
        with open(os.path.join(self.tmp, "enc-1"), "rb") as f:
            raw = f.read()
        self.assertEqual(struct.unpack(f">{len(raw) // 4}i", raw), (0, 1, -223, -308))
        self.assertTrue(any("pointer drawn into the frames" in m for m in self.logs))

    def test_negative_render_cursor_false_keeps_rfb_cursor(self):
        self.run_session(self.cfg(render_cursor=False), self._script())
        with open(os.path.join(self.tmp, "argv-1")) as f:
            self.assertNotIn("--render-cursor", f.read().split("\n"))
        with open(os.path.join(self.tmp, "enc-1"), "rb") as f:
            raw = f.read()
        self.assertEqual(struct.unpack(f">{len(raw) // 4}i", raw), (0, 1, -239, -223, -308))


# ---------------------------------------------------------------- config reload, no primary

class TestReapply(unittest.TestCase):
    def _bridge(self, hypr, **kw):
        cfg = {"runtime_dir": "/nonexistent", "mode": "virtual", "virtual_output": VOUT,
               "primary_output": "HDMI-A-1", "virtual_workspace": "remote"}
        cfg.update(kw)
        b = B.Bridge(cfg)
        b.hypr = hypr
        return b

    def _phone(self, hypr, b):
        self.assertTrue(b.ensure_virtual())
        b.applied = (1100, 2080, 2.5)
        b.reapply("setup")
        self.assertEqual({k: hypr.monitor(VOUT)[k] for k in ("width", "height", "scale", "x", "y")},
                         {"width": 1100, "height": 2080, "scale": 2.5, "x": 740, "y": 1080})

    def test_event_mapping(self):
        b = self._bridge(StatefulHypr())
        self.assertEqual(b._event_action("configreloaded", ""), "reload")
        self.assertEqual(b._event_action("monitorremovedv2", f"3,{VOUT},Headless"), "gone")
        self.assertEqual(b._event_action("monitorremoved", VOUT), "gone")
        self.assertEqual(b._event_action("monitoraddedv2", f"4,{VOUT}-2,Headless"), "place")
        self.assertEqual(b._event_action("monitorremoved", f"{VOUT}-2"), "place")
        # negative controls: local outputs and look-alike names never move the remote display
        self.assertIsNone(b._event_action("monitoraddedv2", "5,DP-3,Dell"))
        self.assertIsNone(b._event_action("monitoradded", "HEADLESS-MIOS-SCALETEST"))
        self.assertIsNone(b._event_action("focusedmon", f"{VOUT},3"))

    def test_positive_reload_displacement_is_undone(self):
        hypr = StatefulHypr()
        b = self._bridge(hypr)
        self._phone(hypr, b)
        hypr.config_reload(VOUT)
        n = len(hypr.evals)
        self.assertTrue(b.reapply("Hyprland config reloaded", restate=True))
        m = hypr.monitor(VOUT)
        self.assertEqual((m["width"], m["height"], m["scale"], m["x"], m["y"]), (1100, 2080, 2.5, 740, 1080))
        self.assertTrue(any('hl.workspace_rule({workspace = "name:remote"' in e for e in hypr.evals[n:]))
        self.assertFalse([e for e in hypr.evals if "HDMI-A-1" in e])  # primary never touched
        self.assertEqual(hypr.monitor("HDMI-A-1")["x"], 0)

    def test_negative_reapply_is_idempotent_and_needs_a_known_mode(self):
        hypr = StatefulHypr()
        b = self._bridge(hypr)
        self._phone(hypr, b)
        n = len(hypr.evals)
        self.assertFalse(b.reapply("no change"))  # in place already: no rule re-applied
        self.assertEqual(hypr.evals[n:], [])
        hypr.config_reload(VOUT)
        b.applied = None  # nothing the bridge applied: it never invents a mode
        self.assertFalse(b.reapply("reload"))
        self.assertEqual(hypr.monitor(VOUT)["x"], 1920)

    def test_no_primary_never_negative_x(self):
        hypr = StatefulHypr()
        hypr.mons = []  # no local output at all
        logs = []
        orig = B.log
        B.log = logs.append
        self.addCleanup(setattr, B, "log", orig)
        b = self._bridge(hypr)
        pos = b._layout(hypr.monitors(), (1100, 2080, 2.5))[VOUT]
        self.assertEqual(pos, (0, 0))
        self.assertTrue(any("no enabled local output" in m for m in logs))
        hypr.mons.append({"name": "HDMI-A-1", "x": 0, "y": 0, "width": 1920, "height": 1080, "scale": 1.0,
                          "transform": 0, "refreshRate": 144.0, "disabled": True})
        self.assertEqual(b._layout(hypr.monitors(), (1100, 2080, 2.5))[VOUT], (0, 0))  # disabled = absent
        # fallback to the first enabled local output when the SSOT primary is missing
        hypr.mons[0]["disabled"] = False
        hypr.mons[0]["x"] = 300
        b = self._bridge(hypr, primary_output="DP-9")
        self.assertEqual(b._layout(hypr.monitors(), (1100, 2080, 2.5))[VOUT], (300 + 740, 1080))
        self.assertTrue(any("DP-9 is not an enabled local output" in m for m in logs))
        # positive control: the named primary is used
        b = self._bridge(hypr)
        self.assertEqual(b._layout(hypr.monitors(), (1100, 2080, 2.5))[VOUT], (300 + 740, 1080))


class _Socket2:
    """A Hyprland event socket (socket2) that sends lines on demand."""

    def __init__(self, path):
        import threading
        self.srv = socket.socket(socket.AF_UNIX)
        self.srv.bind(path)
        self.srv.listen(4)
        self.conns: list = []
        self.th = threading.Thread(target=self._accept, daemon=True)
        self.th.start()

    def _accept(self):
        while True:
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            self.conns.append(c)

    def send(self, line: str):
        for c in list(self.conns):
            try:
                c.sendall((line + "\n").encode())
            except OSError:
                pass

    def close(self):
        for c in self.conns:
            c.close()
        self.srv.close()


class TestReloadLive(_Live):
    def _run(self, send_event: bool):
        path = os.path.join(self.tmp, "s2.sock")
        s2 = _Socket2(path)
        self.addCleanup(s2.close)
        self.hypr.socket2_path = path
        seen = {}

        async def script(b, cl):
            cl.set_size(1100, 2080)
            await self.until(lambda: cl.fb == (1100, 2080) and self.vout()["scale"] == 2.5)
            await self.until(lambda: (self.vout()["x"], self.vout()["y"]) == (740, 1080))
            await self.until(lambda: len(s2.conns) >= 1)
            self.hypr.config_reload(VOUT)
            if send_event:
                s2.send("configreloaded>>")
            await asyncio.sleep(1.5)
            v = self.vout()
            seen["after"] = (v["width"], v["height"], v["scale"], v["x"], v["y"])
            f = cl.frames
            await self.until(lambda: cl.frames >= f + 3)
            seen["fb"] = cl.fb

        b, cl = self.run_session(self.cfg(reapply_delay_seconds=0.05), script)
        return seen, cl

    def test_positive_reload_re_places_the_phone_and_keeps_xrdp(self):
        seen, cl = self._run(True)
        self.assertEqual(seen["after"], (1100, 2080, 2.5, 740, 1080))
        self.assertEqual(seen["fb"], (1100, 2080))
        self.assertEqual(cl.error, "")
        self.assertFalse(cl.closed and cl.frames < 3)
        self.assertTrue(any("Hyprland config reloaded" in m and "re-applied 1100x2080" in m for m in self.logs))
        self.assertFalse([e for e in self.hypr.evals if "HDMI-A-1" in e])

    def test_negative_without_the_event_the_phone_stays_displaced(self):
        seen, cl = self._run(False)
        self.assertEqual(seen["after"], (1920, 1080, 1.0, 1920, 0))


@unittest.skipUnless(os.environ.get("MIOS_RDP_LIVE_SCALETEST") == "1",
                     "live Hyprland check: MIOS_RDP_LIVE_SCALETEST=1 (creates HEADLESS-MIOS-SCALETEST far away)")
class TestReapplyLiveHyprland(unittest.TestCase):
    """Against the running Hyprland: a throwaway HEADLESS-MIOS-SCALETEST far from the layout,
    never the operator's outputs or windows; always removed. CAUTION (observed 2026-10-16, three runs):
    after creating/removing the headless output the seat cursor sat near the primary's centre
    (Hyprland 0.56 appears to warp on headless add/remove), so run it only with the operator's consent."""

    NAME = "HEADLESS-MIOS-SCALETEST"

    def test_reapply_restores_a_displaced_scaled_output(self):
        rt = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        b = B.Bridge({"runtime_dir": rt, "mode": "virtual", "virtual_output": self.NAME,
                      "virtual_attach": "20000x20000", "virtual_workspace": "", "virtual_initial_mode": "1280x720"})
        self.assertTrue(b.hypr.resolve())
        keep = lambda: sorted((m["name"], m["width"], m["height"], m["scale"], m["x"], m["y"], m["transform"])
                              for m in b.hypr.monitors() if m["name"] != self.NAME)
        before = keep()
        self.assertIsNone(b.hypr.monitor(self.NAME), "a stale SCALETEST output exists")
        try:
            self.assertTrue(b.ensure_virtual())
            b.applied = (1100, 2080, 2.5)
            self.assertTrue(b.reapply("setup"))
            for _ in range(30):
                m = b.hypr.monitor(self.NAME)
                if (m["width"], m["height"], m["x"], m["y"]) == (1100, 2080, 20000, 20000):
                    break
                time.sleep(0.1)
            self.assertEqual((m["width"], m["height"], round(m["scale"], 3), m["x"], m["y"]),
                             (1100, 2080, 2.5, 20000, 20000))
            # displace it the way a reload does (position + mode + scale)
            b.hypr.eval(f'hl.monitor({{output = "{self.NAME}", mode = "1280x720", position = "21000x20000", '
                        'scale = 1.000000, transform = 0})')
            time.sleep(0.5)
            m = b.hypr.monitor(self.NAME)
            self.assertEqual((m["width"], m["x"]), (1280, 21000))  # control: the displacement happened
            self.assertTrue(b.reapply("test reload"))
            time.sleep(0.5)
            m = b.hypr.monitor(self.NAME)
            self.assertEqual((m["width"], m["height"], round(m["scale"], 3), m["x"], m["y"]),
                             (1100, 2080, 2.5, 20000, 20000))
            self.assertAlmostEqual(float(m["refreshRate"]), b.hz, delta=0.5)  # WxH@target_hz kept
            self.assertFalse(b.reapply("again"))  # negative: in place, nothing re-applied
        finally:
            b.hypr.ctl("output", "remove", self.NAME)
            for _ in range(30):
                if not b.hypr.monitor(self.NAME):
                    break
                time.sleep(0.1)
        self.assertIsNone(b.hypr.monitor(self.NAME))
        self.assertEqual(keep(), before)  # HDMI-A-1 and the phone's output untouched


# ---------------------------------------------------------------- 90 Hz target, dmabuf capture, stats

class TestTargetHz(unittest.TestCase):
    def test_positive_target_hz_and_legacy_max_fps(self):
        self.assertEqual(B.target_hz({}), 90)
        self.assertEqual(B.target_hz({"target_hz": 75}), 75)
        self.assertEqual(B.target_hz({"max_fps": 45}), 45)  # legacy key only when target_hz is absent
        self.assertEqual(B.target_hz({"target_hz": 90, "max_fps": 30}), 90)
        self.assertEqual(B.with_refresh("1100x2080", 90), "1100x2080@90")
        self.assertEqual(B.with_refresh("1280x720@60", 90), "1280x720@60")  # explicit refresh kept

    def test_negative_invalid_target_hz_falls_back_to_90(self):
        logs = []
        orig = B.log
        B.log = logs.append
        self.addCleanup(setattr, B, "log", orig)
        for bad in (0, -5, 1000, "fast", None):
            self.assertEqual(B.target_hz({"target_hz": bad}), 90, bad)
        self.assertEqual(len(logs), 5)

    def _bridge(self, hypr, **kw):
        cfg = {"runtime_dir": "/nonexistent", "mode": "virtual", "virtual_output": VOUT,
               "primary_output": "HDMI-A-1", "virtual_workspace": "remote"}
        cfg.update(kw)
        b = B.Bridge(cfg)
        b.hypr = hypr
        return b

    def test_positive_virtual_output_runs_at_target_hz_and_reload_restores_it(self):
        hypr = StatefulHypr()
        b = self._bridge(hypr, target_hz=90)
        self.assertTrue(b.ensure_virtual())
        b.applied = (1100, 2080, 2.5)
        b.reapply("setup")
        self.assertEqual(hypr.monitor(VOUT)["refreshRate"], 90.0)
        self.assertTrue(any('mode = "1100x2080@90"' in e for e in hypr.evals), hypr.evals)
        self.assertTrue(all("@90" in e for e in hypr.evals if "hl.monitor(" in e), hypr.evals)
        # a reload drops the rule (headless default 60 Hz): re-applied at 90 even if size/scale/position match
        hypr.mons[-1]["refreshRate"] = 60.0
        self.assertTrue(b.reapply("Hyprland config reloaded"))
        self.assertEqual(hypr.monitor(VOUT)["refreshRate"], 90.0)
        self.assertFalse(b.reapply("again"))  # negative: at 90 already, nothing re-applied

    def test_negative_control_legacy_30_is_not_the_default(self):
        hypr = StatefulHypr()
        b = self._bridge(hypr)  # no target_hz in the config at all
        self.assertTrue(b.ensure_virtual())
        self.assertEqual(b.hz, 90)
        self.assertFalse([e for e in hypr.evals if "@30" in e or "@60" in e])


class TestDeliveredStats(unittest.TestCase):
    def test_positive_frames_requests_and_rates_counted(self):
        ss, _ = _server_stream((4, 2))
        cs = client_stream(fb=(4, 2))
        for _ in range(3):
            cs.feed(fbreq(0, 0, 4, 2))
        upd = b"\x00\x00\x00\x01" + struct.pack(">HHHHi", 0, 0, 4, 2, 0) + b"\x00" * 32
        st = B.Stats(100.0)
        out = ss.feed(upd + upd)
        st.bytes += len(out)
        self.assertEqual((ss.updates, cs.requests), (2, 3))
        rate = st.sample(102.0, ss.updates, cs.requests)
        self.assertEqual(rate["fps"], 1.0)
        self.assertEqual(rate["requests_per_s"], 1.5)
        self.assertEqual(st.peak_fps, 1.0)
        # negative: no new frames in the next window -> 0 fps (rates are per window, not cumulative)
        self.assertEqual(st.sample(104.0, ss.updates, cs.requests)["fps"], 0.0)
        self.assertEqual(st.peak_fps, 1.0)

    def test_negative_other_server_messages_are_not_frames(self):
        ss, _ = _server_stream((4, 2))
        ss.feed(b"\x02" + b"\x03\x00\x00\x00" + b"\x00\x00\x00\x00")  # Bell + ServerCutText ""
        self.assertEqual(ss.updates, 0)

    def test_stats_file_written_atomically(self):
        d = tempfile.mkdtemp(prefix="mios-rdp-stats-")
        self.addCleanup(shutil.rmtree, d, True)
        B.write_stats(d, {"fps": 88.5, "target_hz": 90})
        p = os.path.join(d, "mios-rdp", "stats.json")
        with open(p) as f:
            self.assertEqual(json.load(f)["fps"], 88.5)
        self.assertFalse(os.path.exists(p + ".tmp"))
        B.write_stats("/proc/nonexistent-dir", {"fps": 1})  # negative: an unwritable dir never raises


class TestPacer(unittest.TestCase):
    """Refresh pacing decisions (pure): the remote display renders at most ~2x what the client takes."""

    @staticmethod
    def feed(p, rates, t0=0.0, step=2.0):
        out = []
        for i, r in enumerate(rates):
            out.append(p.update(r, t0 + i * step))
        return out

    def test_positive_phone_over_the_link_paces_down_and_stays(self):
        p = B.Pacer(90, 30)
        self.assertEqual(p.levels, [30, 45, 60, 75, 90])
        # measured 2026-10-17: a phone takes ~11 frames/s of 1100x2080 bitmaps at 90 Hz
        got = self.feed(p, [11, 11, 11, 11, 11, 11, 11])
        self.assertEqual(got[:3], [None, None, 30])  # three slow windows, then >= 2 x 11 -> 30
        self.assertEqual(p.hz, 30)
        self.assertEqual([g for g in got[3:] if g is not None], [])  # stable at the floor

    def test_positive_client_that_keeps_up_gets_target_back_at_once(self):
        p = B.Pacer(90, 30)
        self.feed(p, [11, 11, 11])
        self.assertEqual(p.hz, 30)
        self.assertEqual(p.update(24, 100.0), 90)  # 24 >= 0.75 x 30: one window, straight to target_hz
        self.assertEqual(p.hz, 90)

    def test_positive_content_limited_lan_client_settles_at_60(self):
        p = B.Pacer(90, 30)
        got = self.feed(p, [30] * 8)  # 30 Hz wallpaper, a client that could take more
        self.assertIn(60, got)
        self.assertEqual(p.hz, 60)  # never below 2 x the delivered rate
        self.assertEqual(p.update(46, 100.0), 90)  # cursor motion: the client takes more -> 90

    def test_negative_idle_screen_noise_and_dwell_never_change(self):
        p = B.Pacer(90, 30)
        self.assertEqual(self.feed(p, [0, 0.5, 2, 0, 1, 0, 0, 0]), [None] * 8)  # nothing rendered: keep 90
        p = B.Pacer(90, 30)
        self.assertEqual(self.feed(p, [11, 11, 80, 11, 11, 60, 11]), [None] * 7)  # never 3 slow windows in a row
        p = B.Pacer(90, 30)
        self.feed(p, [11, 11, 11])
        self.assertEqual(p.hz, 30)
        self.assertIsNone(p.update(80, 4.0 + 1.0))  # inside DWELL of the last change
        self.assertEqual(p.update(80, 4.0 + B.Pacer.DWELL), 90)

    def test_negative_bounds(self):
        p = B.Pacer(90, 30)
        self.feed(p, [3, 3, 3, 3, 3, 3])
        self.assertEqual(p.hz, 30)  # never below min_hz
        p = B.Pacer(90, 30)
        self.assertIsNone(p.update(500, 0.0))  # never above target_hz
        self.assertEqual(B.Pacer(60, 120).levels, [60])  # a floor above the target collapses to the target
        self.assertEqual(B.Pacer(144, 30).levels, [30, 45, 60, 75, 90, 120, 144])

    def test_config_parsing(self):
        logs = []
        orig = B.log
        B.log = logs.append
        self.addCleanup(setattr, B, "log", orig)
        self.assertEqual(B.refresh_pacing({}), ("adaptive", 30))
        self.assertEqual(B.refresh_pacing({"refresh_pacing": "Fixed", "min_hz": 45}), ("fixed", 45))
        self.assertEqual(B.refresh_pacing({"refresh_pacing": "turbo", "min_hz": "x"}), ("adaptive", 30))
        self.assertEqual(B.refresh_pacing({"min_hz": 0}), ("adaptive", 30))
        self.assertEqual(len(logs), 3)


class TestPacingBridge(unittest.TestCase):
    """Bridge side: a pacer decision becomes hl.monitor on the virtual output only (same mode/scale/position)."""

    def _bridge(self, hypr, **kw):
        cfg = {"runtime_dir": "/nonexistent", "mode": "virtual", "virtual_output": VOUT,
               "primary_output": "HDMI-A-1", "virtual_workspace": "remote", "target_hz": 90}
        cfg.update(kw)
        b = B.Bridge(cfg)
        b.hypr = hypr
        b.pacer = B.Pacer(b.hz, b.min_hz) if b.pacing == "adaptive" else None
        logs = []
        orig = B.log
        B.log = logs.append
        self.addCleanup(setattr, B, "log", orig)
        return b, logs

    def _attached(self, hypr, **kw):
        b, logs = self._bridge(hypr, **kw)
        self.assertTrue(b.ensure_virtual())
        b.applied = (1100, 2080, 2.5)
        b.reapply("setup")
        b.sstream = B.ServerStream()
        return b, logs

    def test_positive_slow_client_paces_the_output_and_reload_keeps_it(self):
        hypr = StatefulHypr()
        b, logs = self._attached(hypr)
        before = [dict(m) for m in hypr.mons if m["name"] == "HDMI-A-1"]
        for i in range(3):
            asyncio.run(b._pace(11.0, 10.0 + 2 * i))
        m = hypr.monitor(VOUT)
        self.assertEqual((m["refreshRate"], m["width"], m["height"], m["scale"]), (30.0, 1100, 2080, 2.5))
        self.assertEqual(b.out_hz, 30)
        self.assertTrue(any("pacing: the client takes 11 frames/s" in l and "90 -> 30 Hz" in l for l in logs), logs)
        self.assertEqual([dict(m) for m in hypr.mons if m["name"] == "HDMI-A-1"], before)  # local output untouched
        hypr.mons[-1]["refreshRate"] = 60.0  # a config reload drops the rule: re-applied at the PACED refresh
        self.assertTrue(b.reapply("Hyprland config reloaded"))
        self.assertEqual(hypr.monitor(VOUT)["refreshRate"], 30.0)
        asyncio.run(b._pace(25.0, 30.0))  # the client keeps up -> target_hz again
        self.assertEqual(hypr.monitor(VOUT)["refreshRate"], 90.0)

    def test_negative_fixed_pacing_and_resize_in_flight_keep_target(self):
        hypr = StatefulHypr()
        b, _ = self._attached(hypr, refresh_pacing="fixed")
        self.assertIsNone(b.pacer)
        for i in range(6):
            asyncio.run(b._pace(11.0, 10.0 + 2 * i))
        self.assertEqual(hypr.monitor(VOUT)["refreshRate"], 90.0)
        hypr2 = StatefulHypr()
        b2, _ = self._attached(hypr2)
        b2.sstream.target = (1100, 2080)  # a client resize owns the mode until wayvnc reports it
        for i in range(6):
            asyncio.run(b2._pace(11.0, 10.0 + 2 * i))
        self.assertEqual(hypr2.monitor(VOUT)["refreshRate"], 90.0)
        self.assertEqual(b2.pacer.low, 0)  # windows during a resize are not even counted

    def test_new_client_starts_at_target(self):
        hypr = StatefulHypr()
        b, logs = self._attached(hypr)
        self.assertTrue(b._set_refresh(30, "pacing"))
        self.assertEqual(hypr.monitor(VOUT)["refreshRate"], 30.0)
        self.assertTrue(b._set_refresh(b.hz, "new client"))  # what _session does for every attach
        self.assertEqual(hypr.monitor(VOUT)["refreshRate"], 90.0)
        self.assertFalse(b._set_refresh(90, "again"))  # negative: already there, no rule


class TestPacingLive(_Live):
    """End to end (fake xrdp <-> bridge <-> fake wayvnc): a client slower than the display paces the virtual
    output down without losing frames, a client that speeds up gets target_hz back; "fixed" never paces."""

    def setUp(self):
        super().setUp()
        for obj, name, val in ((B, "STATS_WINDOW", 0.4), (B.Pacer, "DWELL", 0.5)):
            self.addCleanup(setattr, obj, name, getattr(obj, name))
            setattr(obj, name, val)

    def _run(self, **kw):
        seen = {}

        async def script(b, cl):
            cl.delay = 0.09  # ~10 frames/s: the phone over its RDP link
            await self.until(lambda: self.vout() is not None and self.vout()["refreshRate"] == 90.0)
            t0, f0 = time.monotonic(), cl.frames
            if kw.get("refresh_pacing") == "fixed":
                await asyncio.sleep(3.0)
            else:
                await self.until(lambda: self.vout()["refreshRate"] == 30.0, timeout=8)
            seen["slow_fps"] = (cl.frames - f0) / (time.monotonic() - t0)
            seen["slow_hz"] = self.vout()["refreshRate"]
            t1, f1 = time.monotonic(), cl.frames
            await asyncio.sleep(1.2)
            seen["paced_fps"] = (cl.frames - f1) / (time.monotonic() - t1)
            cl.delay = 0.0  # the link got faster
            if kw.get("refresh_pacing") != "fixed":
                await self.until(lambda: self.vout()["refreshRate"] == 90.0, timeout=8)
            seen["fast_hz"] = self.vout()["refreshRate"]
            with open(os.path.join(self.tmp, "mios-rdp", "stats.json")) as f:
                seen["stats"] = json.load(f)

        b, cl = self.run_session(self.cfg(target_hz=90, **kw), script)
        self.assertEqual(cl.error, "")
        return seen

    def test_positive_slow_client_paced_down_then_back_up(self):
        s = self._run()
        self.assertEqual((s["slow_hz"], s["fast_hz"]), (30.0, 90.0))
        self.assertGreater(s["paced_fps"], 0.7 * s["slow_fps"])  # pacing never lowered what the client got
        self.assertEqual((s["stats"]["pacing"], s["stats"]["target_hz"]), ("adaptive", 90))
        self.assertIn(s["stats"]["output_hz"], (30, 90))
        self.assertTrue(any("pacing: the client takes" in l for l in self.logs), self.logs)
        self.assertTrue(any("pacing: the client keeps up" in l for l in self.logs), self.logs)

    def test_negative_control_fixed_keeps_target_hz(self):
        s = self._run(refresh_pacing="fixed")
        self.assertEqual((s["slow_hz"], s["fast_hz"]), (90.0, 90.0))
        self.assertEqual(s["stats"]["pacing"], "fixed")
        self.assertFalse([l for l in self.logs if "pacing:" in l])


class TestWayvncArgv(_FakeEnv):
    """mios-rdp bridge-config: wayvnc -f follows [remote.rdp].target_hz, -g follows capture_dmabuf."""

    def argv(self, **rdp):
        out = os.path.join(self.tmp, "vnc")
        os.mkdir(out)
        p = subprocess.run(["bash", _RDP, "bridge-config", out], env=self.env(FAKE_RDP_JSON=json.dumps(rdp)),
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 0, p.stderr)
        with open(os.path.join(out, "bridge.json")) as f:
            cfg = json.load(f)
        shutil.rmtree(out)
        return cfg["wayvnc_argv"], cfg, p.stderr

    @staticmethod
    def fps(argv):
        return int(argv[argv.index("-f") + 1])

    def test_positive_defaults_90_hz_and_dmabuf(self):
        argv, cfg, _ = self.argv()
        self.assertEqual(self.fps(argv), 90)
        self.assertIn("-g", argv)
        self.assertEqual(cfg["target_hz"], 90)
        argv, cfg, _ = self.argv(target_hz=120)
        self.assertEqual((self.fps(argv), cfg["target_hz"]), (120, 120))
        argv, _, _ = self.argv(max_fps=45)  # legacy key only
        self.assertEqual(self.fps(argv), 45)

    def test_negative_dmabuf_off_and_invalid_rate(self):
        argv, _, _ = self.argv(capture_dmabuf=False)
        self.assertNotIn("-g", argv)
        argv, _, err = self.argv(target_hz="fast")
        self.assertEqual(self.fps(argv), 90)
        self.assertIn("invalid [remote.rdp].target_hz", err)
        argv, _, _ = self.argv(target_hz=90, max_fps=30)  # target_hz wins over the legacy cap
        self.assertEqual(self.fps(argv), 90)
        self.assertNotIn("30", argv[argv.index("-f") + 1])

    def test_pacing_keys_reach_the_bridge(self):
        _, cfg, _ = self.argv()
        self.assertEqual((cfg["refresh_pacing"], cfg["min_hz"]), ("adaptive", 30))
        argv, cfg, _ = self.argv(refresh_pacing="fixed", min_hz=45)
        self.assertEqual((cfg["refresh_pacing"], cfg["min_hz"]), ("fixed", 45))
        self.assertEqual(self.fps(argv), 90)  # wayvnc's capture ceiling stays target_hz either way


class TestSessionInstance(unittest.TestCase):
    """A nested Hyprland (test compositor running as a window, started with WAYLAND_DISPLAY) must never
    capture the remote display, even when it is newer than the operator's session."""

    def setUp(self):
        self.rt = tempfile.mkdtemp(prefix="mios-rdp-inst-")
        self.addCleanup(shutil.rmtree, self.rt, True)
        self.procs = []

    def tearDown(self):
        for p in self.procs:
            p.kill()
            p.wait()

    def inst(self, sig, wl, nested, age):
        env = {"PATH": "/usr/bin:/bin"}
        if nested:
            env["WAYLAND_DISPLAY"] = "wayland-1"
        p = subprocess.Popen(["sleep", "60"], env=env)
        self.procs.append(p)
        d = os.path.join(self.rt, "hypr", sig)
        os.makedirs(d)
        lock = os.path.join(d, "hyprland.lock")
        with open(lock, "w") as f:
            f.write(f"{p.pid}\n{wl}\n")
        os.utime(lock, (time.time() - age, time.time() - age))
        sk = socket.socket(socket.AF_UNIX)  # mios-rdp checks for a real socket (-S)
        sk.bind(os.path.join(self.rt, wl))
        self.addCleanup(sk.close)
        return p.pid

    def live_instance(self):
        fn = subprocess.run(["sed", "-n", "/^_live_instance()/,/^}/p", _RDP], capture_output=True, text=True).stdout
        return subprocess.run(["bash", "-c", fn + '\n_live_instance "$1"', "x", self.rt],
                              capture_output=True, text=True, timeout=10).stdout.strip()

    def test_positive_session_wins_over_a_newer_nested_instance(self):
        self.inst("session", "wayland-1", nested=False, age=3600)
        self.inst("nested", "wayland-2", nested=True, age=1)
        h = B.Hypr(self.rt)
        self.assertTrue(h.resolve())
        self.assertEqual((h.sig, h.env["WAYLAND_DISPLAY"]), ("session", "wayland-1"))
        self.assertEqual(self.live_instance(), "session wayland-1")

    def test_negative_control_newest_session_still_wins_and_nested_alone_is_refused(self):
        self.inst("old", "wayland-1", nested=False, age=3600)
        self.inst("new", "wayland-2", nested=False, age=1)
        h = B.Hypr(self.rt)
        self.assertTrue(h.resolve())
        self.assertEqual(h.sig, "new")  # without a nested one the newest session is used, as before
        self.assertEqual(self.live_instance(), "new wayland-2")
        shutil.rmtree(os.path.join(self.rt, "hypr"))
        self.inst("nested", "wayland-3", nested=True, age=1)
        self.assertFalse(B.Hypr(self.rt).resolve())
        self.assertEqual(self.live_instance(), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
