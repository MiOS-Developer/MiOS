#!/usr/bin/env python3
# AI-hint: Unit tests for the MiOS-Xbox latest-resolver (xb-06, XB-2) with mocked HTTP only: positive controls
#   (manifest lists every resolved version + its upstream digest, nothing but build-manifest.json is written, the
#   SSOT is untouched, verify accepts a matching file) and negative controls (resolver failure aborts and removes a
#   stale manifest, tampered/malformed published digests abort, a tampered download fails verify, a pinned or
#   missing SSOT value aborts, the wrong Authenticode signer aborts; the Looking Glass IDD must match the client commit;
#   the UUP converter is anchored to the git tree of its newest commit). Real sockets are blocked during the tests.
# AI-related: usr/libexec/mios/win/xbox/resolve_latest.py, usr/lib/mios/mios_toml.py,
#   /home/mios/mios-desktop-work-1/staged-ssot/xbox-spec.toml, /home/mios/mios-desktop-work-1/staged-ssot/xbox-research.toml
"""Run: python3 -I tests/test-mios-xbox-resolve-latest.py -v"""
from __future__ import annotations

import base64
import copy
import hashlib
import importlib.util
import io
import json
import os
import socket
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_MOD = os.path.join(_ROOT, "usr", "libexec", "mios", "win", "xbox", "resolve_latest.py")
_LIB = os.path.join(_ROOT, "usr", "lib", "mios")
_VENDOR = os.environ.get("MIOS_VENDOR_TOML") or os.path.join(_ROOT, "usr", "share", "mios", "mios.toml")
_STAGED = os.environ.get("MIOS_XBOX_STAGED_DIR", "/home/mios/mios-desktop-work-1/staged-ssot")
# Fragments not yet merged into the vendor SSOT; once merged they are skipped automatically (absent files).
_FRAGMENTS = [os.path.join(_STAGED, f) for f in ("xbox-spec.toml", "xbox-research.toml")]

sys.dont_write_bytecode = True  # never leave __pycache__ next to the shipped module
spec = importlib.util.spec_from_file_location("resolve_latest", _MOD)
rl = importlib.util.module_from_spec(spec)
sys.modules["resolve_latest"] = rl
spec.loader.exec_module(rl)

sys.path.insert(0, _LIB)
import mios_toml  # noqa: E402  (read-only use: load_merged with explicit layers)
sys.path.pop(0)

COMMIT = "cdba29f2f6bcae3ded26369fc231a067cbca3752"
UUID = "2fab9ee5-66d1-4532-8592-f48a223edf69"
API = "https://api.uupdump.net"
VIRTIO = "https://fedorapeople.org/groups/virt/virtio-win/direct-downloads"
REL = "http://fedorapeople.org/groups/virt/virtio-win/direct-downloads/archive-virtio/virtio-win-0.1.302-1/"
LG = "https://looking-glass.io"
FW = "https://go.microsoft.com/fwlink/?linkid=2124701"
WV2 = "https://msedge.sf.dl.delivery.mp.microsoft.com/filestreamingservice/files/35871e19/MicrosoftEdgeWebView2RuntimeInstallerX64.exe"

ESD = b"fixture esd payload"
MSI = b"fixture winfsp msi"
LG_SHA = hashlib.sha256(b"fixture lg host zip").hexdigest()
LG_IDD_SHA = hashlib.sha256(b"fixture lg idd zip").hexdigest()
GH = "https://api.github.com"
CONV_REPO = "abbodi1406/BatUtil"
CONV_SHA = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"
CONV_FILES = {"convert-UUP.cmd": b"@echo off\r\n", "ConvertConfig.ini": b"[convert-UUP]\r\nAutoStart    =0\r\n", "bin/cdimage.exe": b"MZ"}


def _git_blob(b: bytes) -> str:
    return hashlib.sha1(f"blob {len(b)}\0".encode() + b).hexdigest()


def conv_tree(files=None, truncated=False) -> dict:
    files = CONV_FILES if files is None else files
    tree = [{"path": f"uup-converter-wimlib/{p}", "type": "blob", "sha": _git_blob(b), "size": len(b)} for p, b in files.items()]
    tree += [{"path": "uup-converter-wimlib", "type": "tree", "sha": "f" * 40},
             {"path": "other-tool/readme.md", "type": "blob", "sha": _git_blob(b"x"), "size": 1}]
    return {"sha": CONV_SHA, "truncated": truncated, "tree": tree}


def _uupfile(payload: bytes, size: int | None = None, digest_override: str | None = None) -> dict:
    sha1 = hashlib.sha1(payload).digest()
    fd = digest_override or base64.b64encode(sha1).decode()
    return {"sha1": sha1.hex(), "sha256": hashlib.sha256(payload).hexdigest(), "size": str(size or len(payload)),
            "url": "http://tlu.dl.delivery.mp.microsoft.com/filestreamingservice/files/x?P1=1&P2=404&P3=2&P4=SIGNED",
            "uuid": "1c2bb969-b49b-4b5e-89f1-9c347fdd5468",
            "debug": f"<FileLocation><FileDigest>{fd}</FileDigest><Url>http://x</Url></FileLocation>"}


def routes() -> dict:
    """(method, url) -> (status, headers, body) for one consistent upstream snapshot."""
    j = lambda o: json.dumps(o).encode()  # noqa: E731
    return {
        ("GET", f"{API}/fetchupd.php?arch=amd64&ring=Dev"): (200, {}, j({"response": {"updateArray": [
            {"updateId": UUID, "updateTitle": "Windows 11 Insider Preview 29683.1000 (rs_prerelease)",
             "foundBuild": "29683.1000", "arch": "amd64"},
            {"updateId": "00000000-0000-0000-0000-000000000000", "updateTitle": "Windows Server Insider 29683.1000",
             "foundBuild": "29683.1000", "arch": "amd64"}]}})),
        ("GET", f"{API}/listlangs.php?id={UUID}"): (200, {}, j({"response": {"langList": ["de-de", "en-us"]}})),
        ("GET", f"{API}/listeditions.php?id={UUID}&lang=en-us"): (200, {}, j({"response": {
            "editionList": ["CORE", "PROFESSIONAL"]}})),
        ("GET", f"{API}/get.php?id={UUID}&lang=en-us&edition=professional"): (200, {}, j({"response": {
            "build": "29683.1000", "files": {"MetadataESD_professional_en-us.esd": _uupfile(ESD),
                                             f"{UUID}.AggregatedMetadata.cab": _uupfile(b"cab")}}})),
        ("HEAD", f"{VIRTIO}/latest-virtio/virtio-win.iso"): (301, {"Location": REL + "virtio-win.iso"}, b""),
        ("GET", REL.replace("http://", "https://") + "CHECKSUM"): (200, {}, (
            "66f65c16ab3e8dfe12c2855a0d1ac303  virtio-win-0.1.302-1.noarch.rpm\n"
            "09c1acd2ff72263c16a6afbf7a5f2e69  virtio-win-0.1.302-1.src.rpm\n").encode()),
        ("HEAD", f"{VIRTIO}/latest-qemu-ga/qemu-ga-x86_64.msi"): (301, {"Location":
            "http://fedorapeople.org/groups/virt/virtio-win/direct-downloads/archive-qemu-ga/qemu-ga-win-110.2.3-2.el10/qemu-ga-x86_64.msi"}, b""),
        ("HEAD", f"{VIRTIO}/archive-qemu-ga/qemu-ga-win-110.2.3-2.el10/qemu-ga-x86_64.msi"): (200, {"Content-Length": "11909632"}, b""),
        ("GET", f"{LG}/downloads"): (200, {}, (
            '<a href="/artifact/B7/host">B7</a><a href="/artifact/B7-826-236efcb1/host">x</a>'
            '<a href="/artifact/B7-829-cdba29f2/host">x</a><a href="/artifact/B7-829-cdba29f2/idd">x</a>').encode()),
        ("HEAD", f"{LG}/artifact/B7-829-cdba29f2/host"): (302, {"Location":
            f"/artifact/file/host/{LG_SHA}/29a03249/download.bin?name=looking-glass-host-B7-829.zip"}, b""),
        ("HEAD", f"{LG}/artifact/B7-829-cdba29f2/idd"): (302, {"Location":
            f"/artifact/file/idd/{LG_IDD_SHA}/29a03250/download.bin?name=looking-glass-idd-B7-829.zip"}, b""),
        ("GET", f"{GH}/repos/{CONV_REPO}"): (200, {}, j({"default_branch": "master"})),
        ("GET", f"{GH}/repos/{CONV_REPO}/commits/master"): (200, {}, j({"sha": CONV_SHA})),
        ("GET", f"{GH}/repos/{CONV_REPO}/git/trees/{CONV_SHA}?recursive=1"): (200, {}, j(conv_tree())),
        ("GET", "https://api.github.com/repos/winfsp/winfsp/releases/latest"): (200, {}, j({
            "tag_name": "v2.1", "draft": False, "prerelease": False, "html_url": "https://github.com/winfsp/winfsp/releases/v2.1",
            "assets": [{"name": "winfsp-2.1.25156.msi", "size": len(MSI), "digest": "sha256:" + hashlib.sha256(MSI).hexdigest(),
                        "browser_download_url": "https://github.com/winfsp/winfsp/releases/download/v2.1/winfsp-2.1.25156.msi"},
                       {"name": "winfsp-tests-2.1.25156.zip", "size": 1, "digest": "sha256:" + "0" * 64,
                        "browser_download_url": "https://example.invalid/t.zip"}]})),
        ("HEAD", FW): (302, {"Location": WV2}, b""),
        ("HEAD", WV2): (200, {"Content-Length": "214156024", "Last-Modified": "Fri, 16 Oct 2026 18:39:02 GMT",
                               "ETag": '"CIIs"'}, b""),
        ("GET", "https://edgeupdates.microsoft.com/api/products?view=enterprise"): (200, {}, j([
            {"Product": "Beta", "Releases": [{"Platform": "Windows", "Architecture": "x64", "ProductVersion": "156.0.1.1"}]},
            {"Product": "Stable", "Releases": [
                {"Platform": "Windows", "Architecture": "x64", "ProductVersion": "155.0.4283.62"},
                {"Platform": "Windows", "Architecture": "x64", "ProductVersion": "154.0.4200.10"},
                {"Platform": "MacOS", "Architecture": "universal", "ProductVersion": "155.0.4283.99"}]}])),
    }


class FakeHttp:
    def __init__(self, table: dict):
        self.table = table
        self.calls: list = []

    def request(self, url: str, method: str = "GET", follow: bool = True):
        self.calls.append((method, url))
        if (method, url) not in self.table:
            raise AssertionError(f"unmocked upstream call {method} {url}")
        entry = self.table[(method, url)]
        if isinstance(entry, list):  # a sequence of responses for successive calls (retry tests)
            entry = entry.pop(0) if len(entry) > 1 else entry[0]
        st, hd, body = entry
        if isinstance(st, Exception):
            raise st
        return rl.Response(st, hd, body)


def _write(path: str, data: bytes) -> None:
    with open(path, "wb") as fh:
        fh.write(data)


def _readb(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def _read(path: str) -> str:
    return _readb(path).decode()


def _load(path: str):
    return json.loads(_read(path))


def _no_network(*a, **k):
    raise AssertionError("real network access attempted in a mocked test")


class ResolveLatestTest(unittest.TestCase):
    def setUp(self):
        self._sock = socket.socket
        socket.socket = _no_network  # type: ignore[assignment]
        self.tmp = tempfile.TemporaryDirectory(prefix="mios-xb06-")
        self.dir = self.tmp.name
        self.record = os.path.join(self.dir, "lg-build.json")
        with open(self.record, "w") as fh:
            json.dump({"ref": "latest", "commit": COMMIT, "version": "B7-829-gcdba29f2f6"}, fh)
        # user-tier layer: point the resolver at the fixture client build record
        self.user = os.path.join(self.dir, "user.toml")
        with open(self.user, "w") as fh:
            fh.write(f'[windows.xbox.resolve]\nlg_client_build_record = "{self.record}"\n')
        self.layers = [_VENDOR] + [f for f in _FRAGMENTS if os.path.isfile(f)] + [self.user]
        self.ssot_hashes = {p: hashlib.sha256(_readb(p)).hexdigest() for p in self.layers[:-1]}
        self.data = mios_toml.load_merged(self.layers)
        self.out = os.path.join(self.dir, "build", "build-manifest.json")

    def tearDown(self):
        socket.socket = self._sock  # type: ignore[assignment]
        self.tmp.cleanup()

    def run_main(self, argv, http=None, data=None):
        so, se = io.StringIO(), io.StringIO()
        with redirect_stdout(so), redirect_stderr(se):
            rc = rl.main(argv, http=http, data=data, sleep=lambda s: None)
        return rc, so.getvalue(), se.getvalue()

    def resolve(self, table=None, data=None, extra=()):
        return self.run_main(["resolve", "--out", self.out, *extra], http=FakeHttp(table or routes()),
                             data=self.data if data is None else data)

    # ---------------- positive controls ----------------

    def test_ssot_layers_feed_the_config(self):
        cfg = rl.load_config(self.data)
        self.assertEqual((cfg["uup_ring"], cfg["uup_arch"], cfg["uup_edition"], cfg["uup_lang"]),
                         ("Dev", "amd64", "professional", "en-us"))
        self.assertEqual(cfg["lg_client_build_record"], self.record)  # user layer wins over the vendor value

    def test_manifest_lists_resolved_versions_and_digests(self):
        rc, out, err = self.resolve()
        self.assertEqual(rc, 0, err)
        m = _load(self.out)
        a = m["artifacts"]
        self.assertEqual(set(a), {"windows_uup", "virtio_win", "qemu_ga", "looking_glass_host", "looking_glass_idd",
                                  "winfsp", "webview2", "uup_converter"})
        self.assertEqual((a["looking_glass_idd"]["version"], a["looking_glass_idd"]["integrity"]["value"]),
                         ("B7-829-cdba29f2", LG_IDD_SHA))   # the IDD of the SAME client commit as the host app
        c = a["uup_converter"]
        self.assertEqual((c["commit"], c["integrity"]["kind"]), (CONV_SHA, "git-tree"))
        self.assertEqual({f["path"] for f in c["files"]}, set(CONV_FILES))   # only the converter subdir, blob ids kept
        self.assertEqual({f["path"]: f["git_sha1"] for f in c["files"]}["convert-UUP.cmd"], _git_blob(CONV_FILES["convert-UUP.cmd"]))
        self.assertEqual(a["windows_uup"]["version"], "29683.1000")
        self.assertEqual(a["windows_uup"]["uuid"], UUID)
        self.assertEqual({f["name"]: f["sha256"] for f in a["windows_uup"]["files"]}["MetadataESD_professional_en-us.esd"],
                         hashlib.sha256(ESD).hexdigest())
        self.assertEqual((a["virtio_win"]["version"], a["virtio_win"]["integrity"]["value"]),
                         ("0.1.302-1", "66f65c16ab3e8dfe12c2855a0d1ac303"))
        self.assertEqual(a["qemu_ga"]["version"], "110.2.3-2.el10")
        self.assertEqual((a["qemu_ga"]["integrity"]["kind"], a["qemu_ga"]["integrity"]["size"]), ("authenticode", 11909632))
        self.assertEqual((a["looking_glass_host"]["version"], a["looking_glass_host"]["integrity"]["value"]),
                         ("B7-829-cdba29f2", LG_SHA))
        self.assertEqual((a["winfsp"]["version"], a["winfsp"]["integrity"]["value"]),
                         ("2.1.25156", hashlib.sha256(MSI).hexdigest()))
        self.assertEqual(a["webview2"]["version"], "155.0.4283.62")  # newest Stable Windows x64, not Beta/Mac
        self.assertEqual(m["schema"], rl.SCHEMA)
        # writes only build-manifest.json, nothing back to the SSOT, no short-lived signed URLs recorded
        self.assertEqual(os.listdir(os.path.dirname(self.out)), ["build-manifest.json"])
        for p, h in self.ssot_hashes.items():
            self.assertEqual(hashlib.sha256(_readb(p)).hexdigest(), h, p)
        self.assertNotIn("P4=", _read(self.out))
        self.assertEqual(stat.S_IMODE(os.stat(self.out).st_mode), 0o644)

    def test_verify_accepts_matching_downloads(self):
        self.assertEqual(self.resolve()[0], 0)
        f = os.path.join(self.dir, "winfsp-2.1.25156.msi")
        _write(f, MSI)
        rc, out, err = self.run_main(["verify", "--manifest", self.out, "--artifact", "winfsp", "--file", f])
        self.assertEqual(rc, 0, err)
        e = os.path.join(self.dir, "MetadataESD_professional_en-us.esd")
        _write(e, ESD)
        self.assertEqual(self.run_main(["verify", "--manifest", self.out, "--artifact", "windows_uup", "--file", e])[0], 0)

    def test_verify_authenticode_signer(self):
        self.assertEqual(self.resolve()[0], 0)
        m = _load(self.out)
        f = os.path.join(self.dir, "qemu-ga-x86_64.msi")
        with open(f, "wb") as fh:
            fh.truncate(m["artifacts"]["qemu_ga"]["integrity"]["size"])
        tool = os.path.join(self.dir, "osslsigncode")
        with open(tool, "w") as fh:
            fh.write("#!/bin/sh\necho \"Subject: /C=US/O=$FAKE_SIGNER/CN=$FAKE_SIGNER\"\necho 'Signature verification: ok'\n")
        os.chmod(tool, 0o755)
        os.environ["MIOS_OSSLSIGNCODE"] = tool
        try:
            os.environ["FAKE_SIGNER"] = "Red Hat, Inc."
            self.assertEqual(rl.verify_file(m, "qemu_ga", f), "authenticode")
            os.environ["FAKE_SIGNER"] = "Evil Corp"  # negative: signed by someone else
            with self.assertRaisesRegex(rl.ResolveError, "not by"):
                rl.verify_file(m, "qemu_ga", f)
        finally:
            for k in ("MIOS_OSSLSIGNCODE", "FAKE_SIGNER"):
                os.environ.pop(k, None)

    # ---------------- negative controls ----------------

    def test_resolver_failure_aborts_and_removes_stale_manifest(self):
        os.makedirs(os.path.dirname(self.out))
        with open(self.out, "w") as fh:
            json.dump({"artifacts": {"windows_uup": {"version": "26100.1"}}}, fh)  # an old run's manifest
        t = routes()
        t[("GET", f"{API}/fetchupd.php?arch=amd64&ring=Dev")] = (503, {}, b"")
        rc, out, err = self.resolve(t, extra=["--replace"])
        self.assertEqual(rc, 2)
        self.assertIn("ABORT", err)
        self.assertFalse(os.path.exists(self.out), "a failed resolve must not leave a manifest to fall back to")

    def test_transport_error_aborts_without_manifest(self):
        t = routes()
        t[("GET", "https://api.github.com/repos/winfsp/winfsp/releases/latest")] = (OSError("unreachable"), {}, b"")
        rc, _, err = self.resolve(t)
        self.assertEqual(rc, 2)
        self.assertIn("[winfsp]", err)
        self.assertFalse(os.path.exists(self.out))

    def test_tampered_uup_digest_aborts(self):
        t = routes()
        k = ("GET", f"{API}/get.php?id={UUID}&lang=en-us&edition=professional")
        body = json.loads(t[k][2])
        body["response"]["files"]["MetadataESD_professional_en-us.esd"] = _uupfile(
            ESD, digest_override=base64.b64encode(hashlib.sha1(b"other").digest()).decode())
        t[k] = (200, {}, json.dumps(body).encode())
        rc, _, err = self.resolve(t)
        self.assertEqual(rc, 2)
        self.assertIn("tampered", err)
        self.assertFalse(os.path.exists(self.out))

    def test_malformed_published_digests_abort(self):
        cases = {
            ("HEAD", f"{LG}/artifact/B7-829-cdba29f2/host"): (302, {"Location": "/artifact/file/host/zz12/x/download.bin"}, b""),
            ("GET", REL.replace("http://", "https://") + "CHECKSUM"): (200, {}, b"xyz  virtio-win-0.1.302-1.noarch.rpm\n"),
        }
        for key, val in cases.items():
            t = routes()
            t[key] = val
            rc, _, err = self.resolve(t)
            self.assertEqual(rc, 2, key)
            self.assertFalse(os.path.exists(self.out), key)
        t = routes()
        k = ("GET", "https://api.github.com/repos/winfsp/winfsp/releases/latest")
        rel = json.loads(t[k][2])
        del rel["assets"][0]["digest"]
        t[k] = (200, {}, json.dumps(rel).encode())
        rc, _, err = self.resolve(t)
        self.assertEqual(rc, 2)
        self.assertIn("no sha256", err)

    def test_tampered_download_fails_verify(self):
        self.assertEqual(self.resolve()[0], 0)
        f = os.path.join(self.dir, "winfsp-2.1.25156.msi")
        _write(f, MSI + b"x")
        rc, _, err = self.run_main(["verify", "--manifest", self.out, "--artifact", "winfsp", "--file", f])
        self.assertEqual(rc, 2)
        self.assertIn("sha256", err)
        e = os.path.join(self.dir, "MetadataESD_professional_en-us.esd")
        _write(e, b"Y" * len(ESD))
        self.assertEqual(self.run_main(["verify", "--manifest", self.out, "--artifact", "windows_uup", "--file", e])[0], 2)

    def test_lg_host_must_match_client_commit(self):
        with open(self.record, "w") as fh:
            json.dump({"commit": "0123456789abcdef0123456789abcdef01234567", "version": "B7-900-g0123456789"}, fh)
        rc, _, err = self.resolve()
        self.assertEqual(rc, 2)
        self.assertIn("no Looking Glass host build for client commit", err)

    def test_lg_idd_must_match_client_commit(self):
        t = routes()
        k = ("GET", f"{LG}/downloads")
        t[k] = (200, {}, t[k][2].replace(b'<a href="/artifact/B7-829-cdba29f2/idd">x</a>', b'<a href="/artifact/B7-826-236efcb1/idd">x</a>'))
        rc, _, err = self.resolve(t)
        self.assertEqual(rc, 2)
        self.assertIn("no Looking Glass idd build for client commit", err)
        self.assertFalse(os.path.exists(self.out))

    def test_converter_tree_must_anchor_every_file(self):
        for name, body in (("truncated", conv_tree(truncated=True)),
                           ("no entry", conv_tree({"ConvertConfig.ini": CONV_FILES["ConvertConfig.ini"]})),
                           ("malformed blob", {"tree": [{"path": "uup-converter-wimlib/convert-UUP.cmd", "type": "blob", "sha": "zz", "size": 1}]})):
            t = routes()
            t[("GET", f"{GH}/repos/{CONV_REPO}/git/trees/{CONV_SHA}?recursive=1")] = (200, {}, json.dumps(body).encode())
            rc, _, err = self.resolve(t)
            self.assertEqual(rc, 2, name)
            self.assertIn("[uup_converter]", err, name)
            self.assertFalse(os.path.exists(self.out), name)

    def test_converter_channel_pin_aborts(self):
        d = copy.deepcopy(self.data)
        d["windows"]["xbox"]["build"]["converter"]["channel"] = "master@a1b2c3d"
        http = FakeHttp(routes())
        rc, _, err = self.run_main(["resolve", "--out", self.out], http=http, data=d)
        self.assertEqual(rc, 2)
        self.assertIn("build.converter.channel", err)
        self.assertEqual(http.calls, [])

    def test_pins_and_missing_keys_in_ssot_abort(self):
        mutations = {
            "pinned UUP build": lambda d: d["windows"]["xbox"]["source"].__setitem__("build", "29683.1000"),
            "stable-virtio": lambda d: d["windows"]["xbox"]["drivers"]["virtio_win"].__setitem__(
                "upstream", f"{VIRTIO}/stable-virtio/virtio-win.iso"),
            "pinned qemu-ga dir": lambda d: d["windows"]["xbox"]["guest_tools"]["qemu_ga"].__setitem__(
                "upstream", f"{VIRTIO}/archive-qemu-ga/qemu-ga-win-110.2.3-2.el10/"),
            "version key": lambda d: d["windows"]["xbox"]["resolve"].__setitem__("winfsp_version", "2.1"),
            "missing edition": lambda d: d["windows"]["xbox"]["source"].pop("edition"),
            "missing endpoint": lambda d: d["windows"]["xbox"]["resolve"].pop("uupdump_api"),
            "unknown channel": lambda d: d["windows"]["xbox"]["source"].__setitem__("channel", "nightly"),
        }
        for name, mut in mutations.items():
            d = copy.deepcopy(self.data)
            mut(d)
            http = FakeHttp(routes())
            rc, _, err = self.run_main(["resolve", "--out", self.out], http=http, data=d)
            self.assertEqual(rc, 2, name)
            self.assertEqual(http.calls, [], f"{name}: no upstream call before the SSOT is validated")
            self.assertFalse(os.path.exists(self.out), name)

    def test_output_guards(self):
        rc, _, err = self.run_main(["resolve", "--out", "/usr/share/mios/build-manifest.json"],
                                   http=FakeHttp(routes()), data=self.data)
        self.assertEqual(rc, 2)
        self.assertIn("SSOT tree", err)
        rc, _, err = self.run_main(["resolve", "--out", os.path.join(self.dir, "mios.toml")],
                                   http=FakeHttp(routes()), data=self.data)
        self.assertEqual(rc, 2)
        self.assertEqual(self.resolve()[0], 0)
        rc, _, err = self.resolve()  # existing manifest without --replace
        self.assertEqual(rc, 2)
        self.assertIn("exists", err)

    def test_rate_limit_is_retried_then_resolves(self):
        t = routes()
        k = ("GET", f"{API}/fetchupd.php?arch=amd64&ring=Dev")
        t[k] = [(429, {}, b""), t[k]]
        http = FakeHttp(t)
        rc, _, err = self.run_main(["resolve", "--out", self.out], http=http, data=self.data)
        self.assertEqual(rc, 0, err)
        self.assertEqual(http.calls.count(("GET", k[1])), 2)

    def test_error_status_aborts_even_with_a_plausible_body(self):
        t = routes()
        k = ("GET", "https://api.github.com/repos/winfsp/winfsp/releases/latest")
        t[k] = (404, {}, t[k][2])  # a cached/stale body behind an error status is not a resolution
        rc, _, err = self.resolve(t)
        self.assertEqual(rc, 2)
        self.assertIn("HTTP 404", err)
        self.assertFalse(os.path.exists(self.out))

    def test_webview2_must_come_from_microsoft(self):
        t = routes()
        t[("HEAD", FW)] = (302, {"Location": "https://evil.example.com/MicrosoftEdgeWebView2RuntimeInstallerX64.exe"}, b"")
        rc, _, err = self.resolve(t)
        self.assertEqual(rc, 2)
        self.assertIn("off Microsoft", err)


if __name__ == "__main__":
    unittest.main()
