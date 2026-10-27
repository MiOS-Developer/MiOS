#!/usr/bin/env python3
# AI-hint: Two-sided tests for the MiOS-Xbox policy / trust / gate fixes (fixer B, R3 items 3, 7 and 8). Item 3: every
#   RDP redirection policy names its Microsoft TerminalServer.admx policy; camera = fDisableCameraRedir
#   (TS_CAMERA_REDIRECTION), location = fDisableLocationRedir (TS_LOCATION_REDIRECTION), fDisableCam = audio/video
#   playback; a misnamed or unmapped value fails the render and the gate (Microsoft's table + the image's own ADMX).
#   Item 7: virtio-win members and the unsigned qemu-ga MSI are anchored by the sha256 the virtio-win RPM header records
#   (anchored by upstream's CHECKSUM), never by an Authenticode claim; NotSigned is never accepted. Item 8: the gate
#   catches an Administrators AutoLogon account, DISM /Add-Driver / Add-WindowsDriver / AMD / Intel display INF
#   installs, and runs Read-MiOSGpuSpec on the shipped guest spec (gpu.post_setup). Every positive control has a
#   negative control that plants the defect. No network, no VM, fixture credentials only.
# AI-related: usr/libexec/mios/win/xbox/spec_items.py, usr/libexec/mios/win/xbox/conform.py, usr/libexec/mios/win/xbox/render_spec.py,
#   usr/libexec/mios/win/xbox/resolve_latest.py, usr/libexec/mios/win/xbox/fetch.py, usr/libexec/mios/win/xbox/answer.py,
#   usr/share/mios/windows/build/New-MiOSXboxImage.ps1, usr/share/mios/mios.toml, tests/_mios_xbox_fixtures.py
"""Run: python3 -I tests/test-mios-xbox-policy-gates.py -v   (temp dirs only; no network, no VM).
Optional: MIOS_XBOX_TS_ADMX=<a real Windows\\PolicyDefinitions\\TerminalServer.admx> compares Microsoft's table in
spec_items.TS_POLICIES with that file."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
XBOX = os.path.join(ROOT, "usr", "libexec", "mios", "win", "xbox")
sys.path.insert(0, XBOX)
sys.path.insert(0, HERE)
import render_spec as R  # noqa: E402
import answer as A  # noqa: E402
import conform as C  # noqa: E402
import fetch as F  # noqa: E402
import resolve_latest as RL  # noqa: E402
import spec_items as S  # noqa: E402
import _mios_xbox_fixtures as FX  # noqa: E402

VENDOR = os.path.join(ROOT, "usr", "share", "mios", "mios.toml")
FIXTURE_IDENTITY = os.path.join(HERE, "fixtures", "mios-xbox", "fixture-identity.toml")
LAYERS = [VENDOR, FIXTURE_IDENTITY]
IMAGE_PS = os.path.join(ROOT, "usr", "share", "mios", "windows", "build", "New-MiOSXboxImage.ps1")
TS = S.TS_POLICY_KEY
# the old acceptance of an unsigned "Red Hat" artefact (worker servicing + setup stage, R3 item 7)
NOTSIGNED_BYPASS_RE = re.compile(r"Status\s+-eq\s+'NotSigned'\s+-and\s+\$\w+\s+-eq\s+'Red Hat'", re.I)
# Microsoft's TerminalServer.admx (build 29683, read from the last good image): policy -> (valueName, enabled, disabled)
ADMX_29683 = {
    "TS_CLIENT_AUDIO": ("fDisableCam", 0, 1), "TS_CLIENT_AUDIO_CAPTURE": ("fDisableAudioCapture", 0, 1),
    "TS_CAMERA_REDIRECTION": ("fDisableCameraRedir", 1, 0), "TS_CLIENT_DRIVE_M": ("fDisableCdm", 1, 0),
    "TS_CLIENT_COM": ("fDisableCcm", 1, 0), "TS_CLIENT_CLIPBOARD": ("fDisableClip", 1, 0),
    "TS_CLIENT_LPT": ("fDisableLPT", 1, 0), "TS_CLIENT_PNP": ("fDisablePNPRedir", 1, 0),
    "TS_CLIENT_PRINTER": ("fDisableCpm", 1, 0), "TS_LOCATION_REDIRECTION": ("fDisableLocationRedir", 1, 0),
    "TS_SMART_CARD": ("fEnableSmartCard", 0, 1), "TS_TIME_ZONE": ("fEnableTimeZoneRedirection", 1, 0),
    "TS_UIA": ("EnableUiaRedirection", 1, 0), "TS_WEBAUTHN": ("fDisableWebAuthn", 1, 0),
    "TS_DISABLE_REMOTE_DESKTOP_WALLPAPER": ("fNoRemoteDesktopWallpaper", 1, 0),
}

_HOME = tempfile.mkdtemp(prefix="mios-xbox-fixb-home.")   # no ~/.ssh keys: the fixture identity's SSOT key is the only one
R._operator_home = lambda: _HOME


def _ids(findings):
    return {f.id for f in findings}


def _rd(p: str) -> str:
    with open(p, encoding="utf-8") as fh:
        return fh.read()


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


class Fixture:
    merged = None
    doc = None

    @classmethod
    def get(cls):
        if cls.doc is None:
            cls.merged = R.mios_toml.load_merged(LAYERS)
            cls.doc = R.render(copy.deepcopy(cls.merged), LAYERS)
        return copy.deepcopy(cls.merged), copy.deepcopy(cls.doc)


class LiveBase(unittest.TestCase):
    """The live vendor SSOT (+ fixture identity) rendered by render_spec, its items, and a conforming FX bundle."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="mios-xbox-fixb.")
        cls.merged, cls.doc = Fixture.get()
        cls.root = FX.fixture_root(cls.tmp, cls.doc)
        cls.items = S.items_from_spec(cls.doc, cls.root)
        cls.policy = S.spec_policy(cls.doc)
        cls.fam = sorted(next(i for i in cls.items if i["kind"] == "build_family")["families"])[0]
        cls.good = FX.render_bundle(os.path.join(cls.tmp, "good"), cls.doc, cls.items, cls.root, cls.fam)
        cls.n = 0

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def item(self, iid, items=None):
        return next(i for i in (items or self.items) if i["id"] == iid)

    def copy_good(self):
        type(self).n += 1
        d = os.path.join(self.tmp, f"case{self.n}")
        shutil.copytree(self.good, d)
        return d

    def append(self, bundle, line, rel="payload/Windows/Setup/Scripts/SetupComplete.cmd"):
        with open(os.path.join(bundle, rel), "a", encoding="utf-8") as fh:
            fh.write(line + "\r\n")

    def rendered(self, bundle, items=None):
        return C.evaluate(items or self.items, C.collect_rendered([bundle]), "rendered", self.policy)

    def image(self, name, items=None, **kw):
        """An offline image whose hives AND setup scripts carry `items` (default: the spec's), gated against the spec."""
        bundle = self.good if items is None else FX.render_bundle(os.path.join(self.tmp, name + ".bundle"), self.doc, items,
                                                                    self.root, self.fam)
        wim = FX.build_image(os.path.join(self.tmp, name), bundle, items or self.items, self.fam, **kw)
        wd = tempfile.mkdtemp(dir=self.tmp)
        obs = C.collect_image(wim, 1, self.items, wd, os.path.join(self.good, "autounattend.xml"))
        return C.evaluate(self.items, obs, "image", self.policy)


# ---------------------------------------------------------------------------
# item 3: RDP redirection policy value names (F5)
# ---------------------------------------------------------------------------

class TestItem3RdpPolicies(LiveBase):
    WANT = {   # SSOT key -> (policy, value name, value): what the operator's Windows App redirections need
        "allow_audio_playback": ("TS_CLIENT_AUDIO", "fDisableCam", 0),
        "allow_microphone": ("TS_CLIENT_AUDIO_CAPTURE", "fDisableAudioCapture", 0),
        "allow_camera": ("TS_CAMERA_REDIRECTION", "fDisableCameraRedir", 0),
        "allow_drives": ("TS_CLIENT_DRIVE_M", "fDisableCdm", 0),
        "allow_com_ports": ("TS_CLIENT_COM", "fDisableCcm", 0),
        "allow_clipboard": ("TS_CLIENT_CLIPBOARD", "fDisableClip", 0),
        "allow_location": ("TS_LOCATION_REDIRECTION", "fDisableLocationRedir", 0),
        "allow_time_zone": ("TS_TIME_ZONE", "fEnableTimeZoneRedirection", 1),
    }

    def test_microsoft_table_matches_the_image_admx(self):
        self.assertEqual(S.TS_POLICIES, ADMX_29683)
        real = os.environ.get("MIOS_XBOX_TS_ADMX")
        if real:
            got = C.parse_admx(_rd(real))
            for pol, (value, en, dis) in S.TS_POLICIES.items():
                self.assertEqual((got[pol]["value"], got[pol]["enabled"], got[pol]["disabled"]), (value, en, dis), pol)
                self.assertEqual(got[pol]["key"].lower(), "software\\" + TS.lower(), pol)

    def test_positive_live_render_writes_the_documented_names(self):
        reg = {r["owner"]: r for r in self.doc["registry"]}
        for key, (pol, name, val) in self.WANT.items():
            r = reg[f"settings.rdp.{key}"]
            self.assertEqual((r["hive"], r["key"], r["name"], r["type"], r["data"], r.get("policy")),
                             ("SOFTWARE", TS, name, "REG_DWORD", val, pol), key)
        names = {r["name"] for r in self.doc["registry"] if S.is_ts_policy_key(r["hive"], r["key"])}
        self.assertNotIn("fDisableLocation", names)
        self.assertTrue(all(r.get("policy") for r in self.doc["registry"] if S.is_ts_policy_key(r["hive"], r["key"])))
        # the camera is no longer the playback policy
        self.assertNotEqual(reg["settings.rdp.allow_camera"]["name"], reg["settings.rdp.allow_audio_playback"]["name"])
        # the gate's items carry the policy name
        self.assertEqual(self.item("reg.settings.rdp.allow_location")["policy"], "TS_LOCATION_REDIRECTION")

    def _render_fails(self, mutate, key_part, msg_part):
        merged = copy.deepcopy(self.merged)
        mutate(merged["windows"]["xbox"])
        with self.assertRaises(R.SpecError) as cm:
            R.render(merged, LAYERS)
        self.assertIn(key_part, cm.exception.key)
        self.assertIn(msg_part, str(cm.exception))

    def test_negative_render_refuses_misnamed_or_unmapped_policies(self):
        rm = lambda x, k: x["registry_map"][f"settings.rdp.{k}"]  # noqa: E731
        # the R3 location name (no TerminalServer.admx policy writes it)
        self._render_fails(lambda x: rm(x, "allow_location").__setitem__("name", "fDisableLocation"),
                           "registry_map.settings.rdp.allow_location", "writes fDisableLocationRedir, not fDisableLocation")
        # the R3 camera mapping: fDisableCam is the audio/video PLAYBACK policy
        self._render_fails(lambda x: rm(x, "allow_camera").__setitem__("name", "fDisableCam"),
                           "registry_map.settings.rdp.allow_camera", "writes fDisableCameraRedir, not fDisableCam")
        # a Terminal Services value that names no policy
        self._render_fails(lambda x: rm(x, "allow_clipboard").pop("policy"),
                           "registry_map.settings.rdp.allow_clipboard", "without naming its TerminalServer.admx policy")
        self._render_fails(lambda x: rm(x, "allow_drives").__setitem__("policy", "TS_NO_SUCH_POLICY"),
                           "registry_map.settings.rdp.allow_drives", "is not a TerminalServer.admx policy")
        # a value the policy does not define
        self._render_fails(lambda x: x["settings"]["rdp"].__setitem__("allow_camera", 2),
                           "registry_map.settings.rdp.allow_camera", "the policy's values are 1 (Enabled) and 0 (Disabled)")
        # a stale / renamed key in a host or user layer (the old allow_cam) is not silently ignored
        self._render_fails(lambda x: x["settings"]["rdp"].__setitem__("allow_cam", 0),
                           "settings.rdp.allow_cam", "no registry_map entry")

    def test_positive_rendered_and_image_gate_green_for_rdp(self):
        _, f = self.rendered(self.good)
        self.assertFalse({i for i in _ids(f) if "rdp" in i or "ts-policy" in i or "policy" in i}, sorted(_ids(f)))
        results, f = self.image("img-rdp-good")
        self.assertFalse({i for i in _ids(f) if "rdp" in i or "ts-policy" in i or "policy" in i}, [str(x) for x in f])
        passed = {r["id"] for r in results if r["status"] == "pass"}
        for key in self.WANT:
            self.assertIn(f"reg.settings.rdp.{key}", passed)

    def test_negative_image_with_the_r3_names(self):
        # what build-20261018T045609Z carries: fDisableCam as "camera", fDisableLocation, no fDisableCameraRedir
        r3 = copy.deepcopy(self.items)
        r3 = [i for i in r3 if i["id"] != "reg.settings.rdp.allow_camera"]
        self.item("reg.settings.rdp.allow_location", r3)["name"] = "fDisableLocation"
        _, f = self.image("img-rdp-r3", items=r3)
        ids = _ids(f)
        self.assertIn("missing:reg.settings.rdp.allow_camera", ids)
        self.assertIn("missing:reg.settings.rdp.allow_location", ids)
        self.assertIn("deny.unknown-ts-policy-value", ids)

    def test_negative_hand_edited_spec_item_and_old_admx(self):
        bad = copy.deepcopy(self.items)
        self.item("reg.settings.rdp.allow_location", bad)["name"] = "fDisableLocation"   # policy kept: TS_LOCATION_REDIRECTION
        bundle = FX.render_bundle(os.path.join(self.tmp, "img-rdp-edit.bundle"), self.doc, bad, self.root, self.fam)
        wim = FX.build_image(os.path.join(self.tmp, "img-rdp-edit"), bundle, bad, self.fam)
        obs = C.collect_image(wim, 1, bad, tempfile.mkdtemp(dir=self.tmp), os.path.join(self.good, "autounattend.xml"))
        _, f = C.evaluate(bad, obs, "image", self.policy)
        srcs = {x.source for x in f if x.id == "off-spec:reg.settings.rdp.allow_location.policy"}
        self.assertEqual(srcs, {"xbox-spec", C._TS_ADMX})   # Microsoft's table AND the image's own ADMX
        # an image whose ADMX predates the location policy
        old = {k: v for k, v in S.TS_POLICIES.items() if k != "TS_LOCATION_REDIRECTION"}
        _, f = self.image("img-rdp-oldadmx", admx=FX.terminalserver_admx(old))
        self.assertIn("off-spec:reg.settings.rdp.allow_location.policy", _ids(f))
        # no ADMX at all: the names cannot be verified -> fail closed
        _, f = self.image("img-rdp-noadmx", drop=["Windows/PolicyDefinitions/TerminalServer.admx"])
        self.assertIn("missing:policy-definitions", _ids(f))

    def test_negative_rendered_script_writes_an_unknown_ts_value(self):
        b = self.copy_good()
        self.append(b, f'reg add "HKLM\\SOFTWARE\\{TS}" /v fDisableLocation /t REG_DWORD /d 0 /f')
        _, f = self.rendered(b)
        self.assertIn("deny.unknown-ts-policy-value", _ids(f))


# ---------------------------------------------------------------------------
# item 7: virtio-win / qemu-ga trust anchors
# ---------------------------------------------------------------------------

VIO = "https://fedorapeople.org/groups/virt/virtio-win/direct-downloads"
REL = "https://fedorapeople.org/groups/virt/virtio-win/direct-downloads/archive-virtio/virtio-win-0.1.302-1/"
QGA = f"{VIO}/archive-qemu-ga/qemu-ga-win-110.2.3-2.el10/qemu-ga-x86_64.msi"
MEMBERS = {
    "usr/share/virtio-win/virtio-win-0.1.302.iso": b"fixture iso",
    "usr/share/virtio-win/installer/virtio-win-guest-tools.exe": b"fixture burn bundle",
    "usr/share/virtio-win/installer/virtio-win-gt-x64.msi": b"fixture gt msi",
    "usr/share/virtio-win/guest-agent/qemu-ga-x86_64.msi": b"fixture qemu-ga msi",
}


class FakeHttp:
    def __init__(self, table):
        self.table = table

    def request(self, url, method="GET", follow=True, headers=None, limit=None):
        if (method, url) not in self.table:
            raise AssertionError(f"unmocked {method} {url}")
        st, hd, body = self.table[(method, url)]
        return RL.Response(st, hd, body[:limit] if limit else body)


def virtio_routes(header: bytes, qga_size: int) -> dict:
    return {("HEAD", f"{VIO}/latest-virtio/virtio-win.iso"): (301, {"Location": REL.replace("https", "http") + "virtio-win.iso"}, b""),
            ("GET", REL + "CHECKSUM"): (200, {}, b"0123456789abcdef0123456789abcdef  virtio-win-0.1.302-1.noarch.rpm\n"),
            ("GET", REL + "virtio-win-0.1.302-1.noarch.rpm"): (206, {}, header),
            ("HEAD", f"{VIO}/latest-qemu-ga/qemu-ga-x86_64.msi"): (301, {"Location": QGA.replace("https", "http")}, b""),
            ("HEAD", QGA): (200, {"Content-Length": str(qga_size)}, b"")}


CFG = {"virtio_iso": f"{VIO}/latest-virtio/virtio-win.iso", "qemu_ga_dir": f"{VIO}/latest-qemu-ga/",
       "qemu_ga_msi": "qemu-ga-x86_64.msi", "virtio_gt_msi": "virtio-win-gt-x64.msi", "virtio_bundle": "virtio-win-guest-tools.exe"}


def _rpmbuild(tmp: str, files: dict, name: str = "virtio-win") -> str:
    """A real noarch RPM (rpmbuild) carrying `files` under / -- what rpm2cpio + cpio and the header parser read."""
    top = os.path.join(tmp, "rpmtop")
    src = os.path.join(tmp, "rpmsrc")
    for rel, data in files.items():
        p = os.path.join(src, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as fh:
            fh.write(data)
    spec = os.path.join(tmp, "v.spec")
    with open(spec, "w") as fh:
        fh.write(f"Name: {name}\nVersion: 0.1.302\nRelease: 1\nSummary: fixture\nLicense: MIT\nBuildArch: noarch\n"
                 "%description\nfixture\n%install\nmkdir -p %{buildroot}\ncp -a " + src + "/. %{buildroot}/\n%files\n"
                 + "".join(f"/{rel}\n" for rel in files))
    r = subprocess.run(["rpmbuild", "-bb", "--quiet", "--define", f"_topdir {top}", "--define", "__os_install_post %{nil}",
                        "--define", "_build_id_links none", "--define", "debug_package %{nil}", spec],
                       capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        raise AssertionError(r.stderr[-1500:])
    out = [os.path.join(d, f) for d, _, fs in os.walk(os.path.join(top, "RPMS")) for f in fs if f.endswith(".rpm")]
    assert len(out) == 1, out
    return out[0]


class TestItem7TrustAnchors(unittest.TestCase):
    def setUp(self):
        self._sock = socket.socket
        socket.socket = lambda *a, **k: (_ for _ in ()).throw(AssertionError("network in an offline test"))  # type: ignore
        self.tmp = tempfile.mkdtemp(prefix="mios-xbox-fixb-7.")

    def tearDown(self):
        socket.socket = self._sock  # type: ignore
        shutil.rmtree(self.tmp, ignore_errors=True)

    def header(self, **kw):
        return FX.rpm_header_bytes("virtio-win", "0.1.302", "1", dict(MEMBERS), **kw)

    def test_positive_qemu_ga_and_members_pinned_from_the_rpm_header(self):
        http = FakeHttp(virtio_routes(self.header(), len(MEMBERS["usr/share/virtio-win/guest-agent/qemu-ga-x86_64.msi"])))
        v = RL.resolve_virtio(CFG, http, sleep=lambda s: None)
        q = RL.resolve_qemu_ga(CFG, http, sleep=lambda s: None, virtio=v)
        self.assertEqual(q["integrity"], {**q["integrity"], "kind": "sha256", "value": _sha(MEMBERS[v["members"]["qemu_ga"]]),
                                          "signed": False})
        self.assertNotIn("authenticode", json.dumps(q).lower())
        for role, path in v["members"].items():
            self.assertEqual(v["member_digests"][path], {"sha256": _sha(MEMBERS[path]), "size": len(MEMBERS[path])}, role)
        self.assertIn("NOT Authenticode-signed", v["integrity"]["chain"])

    def test_negative_resolver_aborts_on_unanchored_or_tampered_metadata(self):
        qlen = len(MEMBERS["usr/share/virtio-win/guest-agent/qemu-ga-x86_64.msi"])
        # a latest-qemu-ga MSI the latest virtio-win release does not carry: no published digest -> abort
        http = FakeHttp(virtio_routes(self.header(), qlen + 1))
        with self.assertRaisesRegex(RL.ResolveError, "cannot be anchored"):
            RL.resolve_qemu_ga(CFG, http, sleep=lambda s: None)
        for kw, msg in (({"tamper_header": True}, "does not match the sha256 in its signature header"),
                        ({"algo": 1}, "not sha256")):
            with self.assertRaisesRegex(RL.ResolveError, msg):
                RL.resolve_virtio(CFG, FakeHttp(virtio_routes(self.header(**kw), qlen)), sleep=lambda s: None)
        two = dict(MEMBERS, **{"usr/share/virtio-win/guest-agent/old/qemu-ga-x86_64.msi": b"x"})
        with self.assertRaisesRegex(RL.ResolveError, "2 regular files named 'qemu-ga-x86_64.msi'"):
            RL.resolve_virtio(CFG, FakeHttp(virtio_routes(FX.rpm_header_bytes("virtio-win", "0.1.302", "1", two), qlen)),
                              sleep=lambda s: None)
        # the SSOT carries no Authenticode signer for qemu-ga any more; the resolver does not require one
        self.assertNotIn("qemu_ga_signer", RL.REQUIRED_RESOLVE_KEYS)
        merged, _ = Fixture.get()
        self.assertNotIn("qemu_ga_signer", merged["windows"]["xbox"]["resolve"])

    @unittest.skipUnless(shutil.which("rpmbuild") and shutil.which("rpm2cpio") and shutil.which("cpio"), "rpmbuild/rpm2cpio/cpio missing")
    def test_fetch_extracts_members_only_when_they_match_the_header_pins(self):
        rpm = _rpmbuild(self.tmp, MEMBERS)
        with open(rpm, "rb") as fh:
            head = fh.read(1 << 20)
        info = RL.rpm_header(head[:RL.rpm_header_extent(head)])
        roles = {"iso": "usr/share/virtio-win/virtio-win-0.1.302.iso",
                 "guest_tools": "usr/share/virtio-win/installer/virtio-win-guest-tools.exe",
                 "gt_msi": "usr/share/virtio-win/installer/virtio-win-gt-x64.msi",
                 "qemu_ga": "usr/share/virtio-win/guest-agent/qemu-ga-x86_64.msi"}
        man = {"artifacts": {"virtio_win": {"integrity": {"kind": "md5", "value": "0" * 32}, "members": roles,
                                            "rpm_header_sha256": info["header_sha256"],
                                            "member_digests": {p: {"sha256": _sha(MEMBERS[p]), "size": len(MEMBERS[p])} for p in roles.values()}}}}
        got = F.extract_rpm_members(man, rpm, os.path.join(self.tmp, "ex"))   # positive
        for role in F.VIRTIO_ROLES:
            with open(got[role], "rb") as fh:
                self.assertEqual(fh.read(), MEMBERS[roles[role]], role)
        # negative 1: a pin that is not what the RPM header records
        bad = copy.deepcopy(man)
        bad["artifacts"]["virtio_win"]["member_digests"][roles["gt_msi"]]["sha256"] = "f" * 64
        with self.assertRaisesRegex(F.FetchError, "RPM header digest differs"):
            F.extract_rpm_members(bad, rpm, os.path.join(self.tmp, "ex1"))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "ex1")))
        # negative 2: another RPM (payload differs) than the one resolved
        other = _rpmbuild(os.path.join(self.tmp, "o"), dict(MEMBERS, **{roles["gt_msi"]: b"tampered gt msi"}))
        with self.assertRaisesRegex(F.FetchError, "not the resolved one"):
            F.extract_rpm_members(man, other, os.path.join(self.tmp, "ex2"))
        # negative 3: an old manifest without per-member pins (the md5-only chain) is refused
        old = copy.deepcopy(man)
        del old["artifacts"]["virtio_win"]["member_digests"]
        with self.assertRaisesRegex(F.FetchError, "no sha256 per virtio-win member"):
            F.extract_rpm_members(old, rpm, os.path.join(self.tmp, "ex3"))

    def test_inputs_rows_claim_no_signer_for_unsigned_artifacts(self):
        x = {"build": {"download_tries": 1}, "resolve": {"webview2_signer": "Microsoft Corporation", "lg_signer": "HostFission"},
             "guest_tools": {"qemu_ga": {"package": "qemu-ga-x86_64.msi"}}}
        paths = {}
        for n in ("gt.msi", "spice.msi", "qga.msi", "winfsp.msi", "wv2.exe", "lgh.zip", "lgi.zip"):
            paths[n] = os.path.join(self.tmp, n)
            with open(paths[n], "wb") as fh:
                fh.write(n.encode())
        arts = {k: {"version": "v", "source": "https://example.invalid/"} for k in
                ("virtio_win", "qemu_ga", "winfsp", "webview2", "looking_glass_host", "looking_glass_idd")}
        files = {"qemu_ga": "qga.msi", "winfsp": "winfsp.msi", "webview2": "wv2.exe", "looking_glass_host": "lgh.zip",
                 "looking_glass_idd": "lgi.zip"}
        saved = (F.fetch_virtio, F.fetch_file, F.fetch_converter, F.fetch_uup, F.fetch_fod, F.fetch_apps)
        try:
            F.fetch_virtio = lambda *a, **k: {"gt_msi": paths["gt.msi"], "spice_msi": paths["spice.msi"], "drivers": self.tmp}
            F.fetch_file = lambda m, art, *a, **k: paths[files[art]]
            F.fetch_converter = F.fetch_uup = F.fetch_fod = lambda *a, **k: self.tmp
            F.fetch_apps = lambda *a, **k: []
            rows = F.fetch_all({"artifacts": arts}, {"spec": x}, os.path.join(self.tmp, "w"))["installers"]
        finally:
            F.fetch_virtio, F.fetch_file, F.fetch_converter, F.fetch_uup, F.fetch_fod, F.fetch_apps = saved
        for role in ("virtio_guest_tools", "spice_vdagent", "qemu_ga"):
            self.assertEqual(rows[role]["signer"], "", role)
            self.assertIn("sha256", rows[role]["anchor"], role)
        self.assertEqual(rows["webview2"]["signer"], "Microsoft Corporation")   # a publisher that signs keeps its check

    def test_rendered_stage_never_accepts_notsigned(self):
        _, doc = Fixture.get()
        inputs = FX_inputs(self.tmp, doc)
        setup = A.render_setup_script(doc, inputs, ROOT)
        step = lambda n: re.search(r"^Invoke-MiOSStep '" + re.escape(n) + r"' \{\n(.*?)^\}", setup, re.S | re.M).group(1)  # noqa: E731
        for name in ("virtio_win_guest_tools", "qemu_ga", "spice_vdagent"):
            self.assertIn("Assert-MiOSSha256 $f", step(name))
            self.assertNotIn("Assert-MiOSSigned", step(name))      # unsigned upstream: sha256 is the anchor
        self.assertIn("Assert-MiOSSigned $f 'Microsoft Corporation'", step("webview2"))
        self.assertNotRegex(A._PS_LIB, r"NotSigned")
        self.assertIsNone(NOTSIGNED_BYPASS_RE.search(_rd(IMAGE_PS)))
        # negative controls: the old bypass is recognised, and a signed installer without a signer is refused
        self.assertIsNotNone(NOTSIGNED_BYPASS_RE.search("if ($s.Status -eq 'NotSigned' -and $Signer -eq 'Red Hat') { return }"))
        head = subprocess.run(["git", "-C", ROOT, "show", "HEAD:usr/share/mios/windows/build/New-MiOSXboxImage.ps1"],
                              capture_output=True, text=True, timeout=60)
        if head.returncode == 0:
            self.assertIsNotNone(NOTSIGNED_BYPASS_RE.search(head.stdout), "the R3 worker bypass should be detectable")
        noisy = copy.deepcopy(inputs)
        noisy["installers"]["webview2"]["signer"] = ""
        with self.assertRaisesRegex(A.PayloadError, "no Authenticode signer"):
            A.render_setup_script(doc, noisy, ROOT)

    @unittest.skipUnless(shutil.which("pwsh"), "pwsh not installed")
    def test_assert_signed_throws_on_notsigned_even_for_red_hat(self):
        lib = os.path.join(self.tmp, "lib.ps1")
        with open(lib, "w") as fh:
            fh.write(A._PS_LIB)
        def run(status, subject, signer):
            ps = (f"function Get-AuthenticodeSignature {{ param([string]$LiteralPath) [pscustomobject]@{{ Status = '{status}'; "
                  f"SignerCertificate = $(if ('{subject}') {{ [pscustomobject]@{{ Subject = '{subject}' }} }} else {{ $null }}) }} }}; "
                  f"$MiOSLogDir = '{self.tmp}'; . '{lib}'; "
                  f"try {{ Assert-MiOSSigned '{lib}' '{signer}'; 'OK' }} catch {{ 'THROWN: ' + $_.Exception.Message }}")
            r = subprocess.run(["pwsh", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True, text=True, timeout=120)
            return (r.stdout + r.stderr).strip().splitlines()[-1]
        self.assertTrue(run("NotSigned", "", "Red Hat").startswith("THROWN"))          # the R3 bypass is gone
        self.assertTrue(run("Valid", "CN=Evil, O=Evil", "Red Hat").startswith("THROWN"))
        self.assertTrue(run("Valid", "CN=Red Hat, O=Red Hat, Inc.", "").startswith("THROWN"))
        self.assertEqual(run("Valid", "CN=Microsoft Corporation, O=Microsoft Corporation", "Microsoft Corporation"), "OK")


def FX_inputs(tmp: str, doc) -> dict:
    """Verified-input stand-ins shaped like fetch.py's rows (signer "" for the sha256-anchored virtio-win artefacts)."""
    dl = os.path.join(tmp, "dl")
    os.makedirs(dl, exist_ok=True)
    signers = {"webview2": "Microsoft Corporation", "looking_glass_host": "HostFission", "looking_glass_idd": "HostFission"}
    names = {"virtio_guest_tools": "virtio-win-gt-x64.msi", "spice_vdagent": "spice-vdagent-x64.msi",
             "qemu_ga": "qemu-ga-x86_64.msi", "winfsp": "winfsp-9.9.99999.msi",
             "webview2": "MicrosoftEdgeWebView2RuntimeInstallerX64.exe", "looking_glass_host": "looking-glass-host.zip",
             "looking_glass_idd": "looking-glass-idd.zip"}
    inputs = {"installers": {}}
    for role in A.INSTALLER_ROLES:
        p = os.path.join(dl, names[role])
        with open(p, "wb") as fh:
            fh.write(role.encode())
        inputs["installers"][role] = {"path": p, "sha256": _sha(role.encode()), "signer": signers.get(role, ""),
                                      "version": "fixture", "url": f"https://example.invalid/{role}"}
    return inputs


# ---------------------------------------------------------------------------
# item 8: gate gaps
# ---------------------------------------------------------------------------

class TestItem8AutologonAdmin(LiveBase):
    def test_positive_standard_autologon_account(self):
        d = self.item("users.desktop")
        self.assertTrue(d["autologon"])
        self.assertNotIn("administrators", {g.lower() for g in d["groups"]})
        _, f = self.rendered(self.good)
        self.assertFalse({i for i in _ids(f) if i.startswith(("users.autologon-admin", "off-spec:users.group"))})
        b = self.copy_good()   # the separate elevation admin may be an Administrator
        self.append(b, f"Add-LocalGroupMember -SID 'S-1-5-32-544' -Member '{self.item('users.admin')['name']}'",
                    rel="payload/Windows/Setup/Scripts/x.ps1")
        _, f = self.rendered(b)
        self.assertNotIn("users.autologon-admin", _ids(f))

    def test_negative_autologon_account_in_administrators(self):
        # D2b: the answer file keeps "user" as AutoLogon but its <Group> becomes Administrators
        doc = copy.deepcopy(self.doc)
        doc["users"]["desktop"]["groups"] = ["Administrators"] + list(doc["users"]["desktop"]["groups"])
        b = self.copy_good()
        with open(os.path.join(b, "autounattend.xml"), "w", encoding="utf-8") as fh:
            fh.write(FX.answer_file(doc, ""))
        _, f = self.rendered(b)
        self.assertIn("users.autologon-admin", _ids(f))
        self.assertIn("off-spec:users.group:users.desktop", _ids(f))
        user = self.item("users.desktop")["name"]
        for line in (f"Add-LocalGroupMember -SID 'S-1-5-32-544' -Member '{user}'",
                     f"Add-LocalGroupMember -Group Administrators -Member {user}",
                     f"net localgroup Administrators {user} /add"):
            b = self.copy_good()
            self.append(b, line, rel="payload/Windows/Setup/Scripts/x.ps1")
            _, f = self.rendered(b)
            self.assertIn("users.autologon-admin", _ids(f), line)


class TestItem8DriverScripts(LiveBase):
    PLANTS = {   # R3 D4c-D4f and a vendor driver directory
        "dism /online /add-driver /driver:C:\\drv\\nvidia\\nv_dispi.inf": "deny.gpu-driver-script",
        "Add-WindowsDriver -Online -Driver 'C:\\drv\\nv_dispi.inf'": "deny.gpu-driver-script",
        'pnputil /add-driver "C:\\amd\\u0401095.inf"': "deny.gpu-driver-script",
        "pnputil /add-driver C:\\intel\\iigd_dch.inf": "deny.gpu-driver-script",
        "dism /image:C:\\mnt /add-driver /driver:C:\\Drivers\\NVIDIA /recurse": "deny.gpu-driver-script",
        "devcon install C:\\x\\amdkmdag.inf PCI\\VEN_1002": "deny.gpu-driver-script",
        'pnputil.exe /add-driver "C:\\nvidia\\nv_dispi.inf" /install': "deny.pnputil-nvidia-inf",
    }

    def test_positive_virtio_and_variable_driver_installs_pass(self):
        b = self.copy_good()
        for line in ("pnputil.exe /add-driver C:\\drivers\\viostor.inf /install",
                     "Add-WindowsDriver -Path $mount -Driver $DriversDir -Recurse | Out-Null",
                     "$o = & (Join-Path $env:SystemRoot 'System32\\pnputil.exe') /add-driver (Join-Path $Drv '*.inf') /subdirs /install"):
            self.append(b, line, rel="payload/Windows/Setup/Scripts/x.ps1")
        _, f = self.rendered(b)
        self.assertFalse({i for i in _ids(f) if i in ("deny.gpu-driver-script", "deny.pnputil-nvidia-inf")})
        for p in [os.path.join(d, n) for d, _, ns in os.walk(os.path.join(ROOT, "usr", "share", "mios", "windows")) for n in ns
                  if n.lower().endswith((".ps1", ".cmd"))]:   # nothing the repo ships trips the rule
            self.assertFalse([fid for fid, _ in S.text_deny_hits(_rd(p)) if fid in ("deny.gpu-driver-script", "deny.pnputil-nvidia-inf")], p)

    def test_negative_each_gpu_driver_install_is_denied(self):
        for line, fid in self.PLANTS.items():
            b = self.copy_good()
            self.append(b, line)
            _, f = self.rendered(b)
            self.assertIn(fid, _ids(f), line)
            self.assertEqual(1, sum(1 for x in f if x.id in ("deny.gpu-driver-script", "deny.pnputil-nvidia-inf")), line)


@unittest.skipUnless(shutil.which("pwsh"), "pwsh not installed")
class TestItem8GpuPostSetup(LiveBase):
    def guest(self, bundle):
        return os.path.join(bundle, "payload", self.item("gpu.post_setup")["spec_path"])

    def test_positive_read_spec_runs_on_the_shipped_guest_spec(self):
        it = self.item("gpu.post_setup")
        self.assertEqual((it["spec_path"], it["mode"], it["script"]),
                         ("ProgramData/MiOS/xbox-spec.json", "detect", self.doc["spec"]["drivers"]["gpu_step"]))
        results, f = self.rendered(self.good)
        self.assertEqual("pass", next(r["status"] for r in results if r["id"] == "gpu.post_setup"), [str(x) for x in f])
        results, f = self.image("img-gpu-good")
        self.assertEqual("pass", next(r["status"] for r in results if r["id"] == "gpu.post_setup"), [str(x) for x in f])

    def _plant(self, mutate):
        b = self.copy_good()
        with open(self.guest(b)) as fh:
            g = json.load(fh)
        mutate(g)
        with open(self.guest(b), "w") as fh:
            json.dump(g, fh)
        return b

    def test_negative_shipped_spec_the_step_cannot_read(self):
        # the M1/M2 failure: "SSOT table windows.xbox.gpu missing" on the shipped spec
        _, f = self.rendered(self._plant(lambda g: g["spec"].pop("gpu")))
        msg = [x.detail for x in f if x.id == "off-spec:gpu.post_setup"]
        self.assertTrue(msg and "windows.xbox.gpu missing" in msg[0], [str(x) for x in f])
        _, f = self.rendered(self._plant(lambda g: g["spec"]["drivers"].__setitem__("gpu", "nvidia-package")))
        self.assertIn("off-spec:gpu.post_setup", _ids(f))
        _, f = self.rendered(self._plant(lambda g: g["spec"]["gpu"].__setitem__("record_path", "C:\\elsewhere.json")))
        self.assertIn("off-spec:gpu.post_setup", _ids(f))
        b = self.copy_good()
        os.unlink(self.guest(b))
        _, f = self.rendered(b)
        self.assertIn("missing:gpu.post_setup", _ids(f))
        _, f = self.image("img-gpu-nospec", drop=[self.item("gpu.post_setup")["spec_path"]])
        self.assertIn("missing:gpu.post_setup", _ids(f))


if __name__ == "__main__":
    unittest.main(verbosity=2)
