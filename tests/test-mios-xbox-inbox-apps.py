#!/usr/bin/env python3
# AI-hint: Two-sided tests for the MiOS-Xbox inbox-app integration (round-1 debug boot of build-20261023T224037Z,
#   xbox-m1/boot-round-1.md): the edition-filtered UUP list carries no app package, so the image had 3 provisioned apps,
#   no Microsoft Store, no App Installer (winget) for the Hydrate stage and a GamingHomeApp naming an absent Xbox app.
#   windows.xbox.source.apps now selects the update's own IPA_<group>_* apps + every framework from the full list
#   (resolve_latest), fetch places them in the UUP set under UUP Dump's names and renders [Store_Apps] CustomList=1 +
#   CustomAppsList.txt for the converter, render_spec refuses a home app / Hydrate path the image cannot satisfy, and
#   the image gate (conform) requires every family under Program Files\WindowsApps. Each positive control has a negative
#   control (the round-1 inputs). No network, no VM.
# AI-related: usr/libexec/mios/win/xbox/resolve_latest.py, usr/libexec/mios/win/xbox/fetch.py,
#   usr/libexec/mios/win/xbox/render_spec.py, usr/libexec/mios/win/xbox/spec_items.py, usr/libexec/mios/win/xbox/conform.py,
#   usr/share/mios/mios.toml ([windows.xbox.source].apps / app_frameworks, [windows.xbox.build.converter].store_apps)
"""Run: python3 -I tests/test-mios-xbox-inbox-apps.py -v   (temp dirs only; no network, no VM)"""

from __future__ import annotations

import base64
import copy
import hashlib
import importlib.util
import json
import os
import shutil
import socket
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("mios_xbox_provisioning_tests_apps", os.path.join(HERE, "test-mios-xbox-provisioning.py"))
P = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(P)   # shares the fixture SSOT layers (vendor + fixture identity) and the module imports
R, F, RL, S, C = P.R, P.F, P.RL, P.S, P.C

UUID = "9775f6a3-8aa9-43c2-a875-e0fed7c1e81c"
API = "https://api.example.invalid"
FW_RE = P.Fixture.get()[0]["windows"]["xbox"]["source"]["app_frameworks"]
# Upstream ConvertConfig.ini (abbodi1406/BatUtil uup-converter-wimlib at 4c02e02d, the commit build-20261023T224037Z
# resolved): its [convert-UUP] and [Store_Apps] keys, the two sections fetch.py edits.
_UP_KEYS = ("AutoStart AddUpdates Cleanup ResetBase NetFx3 StartVirtual wim2esd wim2swm SkipISO SkipWinRE LCUwinre "
            "LCUmsuExpand UpdtBootFiles ForceDism RefESD SkipLCUmsu SkipEdge AutoExit DisableUpdatingUpgrade AddDrivers").split()
UPSTREAM_INI = ("[convert-UUP]\r\n" + "".join(f"{k:<13}=0\r\n" for k in _UP_KEYS) + "Drv_Source   =\\Drivers\r\n\r\n"
                "[Store_Apps]\r\nSkipApps     =0\r\nAppsLevel    =0\r\nStubAppsFull =1\r\nCustomList   =0\r\n\r\n"
                "[create_virtual_editions]\r\nvUseDism     =1\r\n")
# The 3 packages the round-1 image provisioned (Program Files\WindowsApps folder names).
ROUND1_WINDOWSAPPS = [
    "/Program Files/WindowsApps/Microsoft.MicrosoftEdge.Stable_156.0.4314.42_neutral__8wekyb3d8bbwe",
    "/Program Files/WindowsApps/Microsoft.OneDriveSync_25087.506.1.0_neutral__8wekyb3d8bbwe",
    "/Program Files/WindowsApps/Microsoft.OutlookForWindows_1.2026.1013.100_x64__8wekyb3d8bbwe/AppxManifest.xml",
]


def _uupfile(payload: bytes) -> dict:
    sha1 = hashlib.sha1(payload).digest()
    return {"sha1": sha1.hex(), "sha256": hashlib.sha256(payload).hexdigest(), "size": str(len(payload)),
            "url": "https://tlu.dl.delivery.mp.microsoft.com/filestreamingservice/files/x?P1=1&P4=SIGNED",
            "debug": f"<FileLocation><FileDigest>{base64.b64encode(sha1).decode()}</FileDigest></FileLocation>"}


def full_list(apps, extra=None) -> dict:
    """An update's full UUP list: per app its IPA group's bundle + stub + resource pack, the frameworks (one inside a
    selected group, one in another app's group, one in the frameworks group), an unrelated app and an edition file."""
    out = {"MetadataESD_professional_en-us.esd": _uupfile(b"esd"),
           "IPA_Paint_Microsoft.Paint_8wekyb3d8bbwe.msixbundle": _uupfile(b"paint (not selected)"),
           "IPA_Paint_Microsoft.WindowsAppRuntime.x64.1.8.appx": _uupfile(b"war 1.8 framework"),
           "IPA_frameworks_Microsoft.UI.Xaml.x64.2.7.appx": _uupfile(b"xaml 2.7 framework")}
    for pfn in apps:
        g = "G" + pfn.split("_")[0].replace(".", "")
        out[f"IPA_{g}_{pfn}.msixbundle"] = _uupfile(f"bundle {pfn}".encode())
        out[f"IPA_{g}_AppxMetadata_Stub_{g}_x64_stub.msix"] = _uupfile(f"stub {pfn}".encode())
        out[f"IPA_{g}_App_1.0.0.0_language-de.msix"] = _uupfile(f"lang {pfn}".encode())
    first = "G" + apps[0].split("_")[0].replace(".", "")
    out[f"IPA_{first}_Microsoft.VCLibs.x64.14.00.appx"] = _uupfile(b"vclibs framework in a selected group")
    out.update(extra or {})
    return out


def cfg(apps) -> dict:
    return {"app_packages": list(apps), "app_frameworks": FW_RE}


class FakeHttp:
    def __init__(self, files):
        self.files = files
        self.calls = []

    def request(self, url, method="GET", follow=True, headers=None, limit=None):
        self.calls.append(url)
        return RL.Response(200, {}, json.dumps({"response": {"files": self.files}}).encode())


class FakeDl:
    """Downloader stand-in: serves the payload bytes registered per URL."""
    tries = 1

    def __init__(self, payloads):
        self.payloads = payloads
        self.got = []

    @staticmethod
    def sleep(_s):
        return None

    def get(self, url, dest, size=None):
        self.got.append(url)
        with open(dest, "wb") as fh:
            fh.write(self.payloads[url])


def _no_network(*a, **k):
    raise AssertionError("network access in an offline test")


class Base(unittest.TestCase):
    def setUp(self):
        self._sock = socket.socket
        socket.socket = _no_network  # type: ignore[assignment]
        self.tmp = tempfile.mkdtemp(prefix="mios-xbox-apps.")
        self.merged, self.doc = P.Fixture.get()
        self.apps = list(self.doc["spec"]["source"]["apps"])

    def tearDown(self):
        socket.socket = self._sock  # type: ignore[assignment]
        shutil.rmtree(self.tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# 1. resolve_latest: the SSOT apps + every framework from the SAME update's full list
# ---------------------------------------------------------------------------

class TestResolveApps(Base):
    def test_positive_every_group_file_and_every_framework(self):
        allf = full_list(self.apps)
        rows = RL.resolve_apps(cfg(self.apps), allf, UUID)
        by = {r["name"]: r for r in rows}
        self.assertEqual(sorted({r["pfn"] for r in rows if r["role"] == "app"}), sorted(self.apps))
        for pfn in self.apps:   # bundle + stub + resource pack of each app's group
            g = "G" + pfn.split("_")[0].replace(".", "")
            for n in (f"IPA_{g}_{pfn}.msixbundle", f"IPA_{g}_AppxMetadata_Stub_{g}_x64_stub.msix", f"IPA_{g}_App_1.0.0.0_language-de.msix"):
                self.assertEqual((by[n]["role"], by[n]["pfn"], by[n]["group"]), ("app", pfn, g), n)
        fws = sorted(n for n, r in by.items() if r["role"] == "framework")
        self.assertEqual(len(fws), 3, fws)   # in a selected group, in an unselected app's group, in the frameworks group
        self.assertNotIn("pfn", by["IPA_Paint_Microsoft.WindowsAppRuntime.x64.1.8.appx"])
        self.assertNotIn("IPA_Paint_Microsoft.Paint_8wekyb3d8bbwe.msixbundle", by)   # an unselected app stays out
        self.assertNotIn("MetadataESD_professional_en-us.esd", by)
        for r in rows:
            self.assertEqual(r["sha256"], allf[r["name"]]["sha256"])
        # verify_file accepts a downloaded app file against the manifest's app_files (and only those)
        man = {"artifacts": {"windows_uup": {"integrity": {"kind": "sha256-per-file"}, "files": [], "app_files": rows}}}
        name = f"IPA_G{self.apps[0].split('_')[0].replace('.', '')}_{self.apps[0]}.msixbundle"
        p = os.path.join(self.tmp, name)
        with open(p, "wb") as fh:
            fh.write(f"bundle {self.apps[0]}".encode())
        self.assertEqual(RL.verify_file(man, "windows_uup", p), "sha256")
        with open(p, "wb") as fh:
            fh.write(b"tampered")
        with self.assertRaises(RL.ResolveError):
            RL.verify_file(man, "windows_uup", p)

    def test_negative_missing_ambiguous_or_no_framework_aborts(self):
        allf = full_list(self.apps[1:])   # the round-1 situation for one app: the update does not offer it
        with self.assertRaisesRegex(RL.ResolveError, "offers no inbox app " + self.apps[0]):
            RL.resolve_apps(cfg(self.apps), allf, UUID)
        twice = full_list(self.apps, {f"IPA_Other_{self.apps[0]}.appx": _uupfile(b"second copy")})
        with self.assertRaisesRegex(RL.ResolveError, "more than one inbox app"):
            RL.resolve_apps(cfg(self.apps), twice, UUID)
        nofw = {n: v for n, v in full_list(self.apps).items() if "VCLibs" not in n and "AppRuntime" not in n and "Xaml" not in n}
        with self.assertRaisesRegex(RL.ResolveError, "no framework package"):
            RL.resolve_apps(cfg(self.apps), nofw, UUID)
        bad = full_list(self.apps, {"IPA_frameworks_Microsoft.VCLibs.x64.14.00 evil$.appx": _uupfile(b"x")})
        with self.assertRaisesRegex(RL.ResolveError, "unsafe inbox app file name"):
            RL.resolve_apps(cfg(self.apps), bad, UUID)

    def test_load_config_reads_and_checks_the_ssot_apps(self):
        c = RL.load_config(self.merged)
        self.assertEqual(c["app_packages"], self.apps)
        self.assertEqual(c["app_frameworks"], FW_RE)
        for bad in (["Microsoft.WindowsStore"], ["Microsoft.WindowsStore_8wekyb3d8bbwe"] * 2, "Microsoft.WindowsStore_8wekyb3d8bbwe"):
            m = copy.deepcopy(self.merged)
            m["windows"]["xbox"]["source"]["apps"] = bad
            with self.assertRaisesRegex(RL.ResolveError, "source.apps"):
                RL.load_config(m)
        m = copy.deepcopy(self.merged)
        m["windows"]["xbox"]["source"]["app_frameworks"] = "(["
        with self.assertRaisesRegex(RL.ResolveError, "app_frameworks"):
            RL.load_config(m)
        # negative control: the round-1 SSOT (no source.apps) resolved no app at all
        m = copy.deepcopy(self.merged)
        del m["windows"]["xbox"]["source"]["apps"]
        self.assertEqual(RL.load_config(m)["app_packages"], [])

    def test_resolve_uup_records_app_files_with_one_full_list_call(self):
        c = RL.load_config(self.merged)
        allf = {**full_list(self.apps), "cabs_OpenSSH-Server-Package-amd64.cab": _uupfile(b"sshd fod"),
                "FoDMetadata_Client.cab": _uupfile(b"fod meta")}
        http = FakeHttp(allf)
        rows_fod = RL.resolve_fod(c, http, API, UUID, "amd64", sleep=lambda s: None, full=allf)
        self.assertEqual(http.calls, [], "the shared full list is not fetched again")
        self.assertTrue(rows_fod)
        rows = RL.resolve_apps(c, allf, UUID)
        digest = hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        self.assertRegex(digest, r"^[0-9a-f]{64}$")


# ---------------------------------------------------------------------------
# 2. fetch: apps into the UUP set; [Store_Apps] + CustomAppsList.txt for the converter
# ---------------------------------------------------------------------------

class TestFetchAndConverter(Base):
    def _manifest(self, rows):
        return {"artifacts": {"windows_uup": {"uuid": UUID, "integrity": {"kind": "sha256-per-file"}, "files": [], "app_files": rows}}}

    def test_positive_apps_land_in_the_uup_set_verified(self):
        allf = full_list(self.apps)
        rows = RL.resolve_apps(cfg(self.apps), allf, UUID)
        live = {n: {**v, "url": f"https://cdn.example.invalid/{n}"} for n, v in allf.items()}
        payloads = {}
        for n in allf:
            g = n.split("_")[1] if n.startswith("IPA_") else ""
            pfn = next((p for p in self.apps if n.endswith(f"_{p}.msixbundle")), None)
            body = {"IPA_Paint_Microsoft.WindowsAppRuntime.x64.1.8.appx": b"war 1.8 framework",
                    "IPA_frameworks_Microsoft.UI.Xaml.x64.2.7.appx": b"xaml 2.7 framework"}.get(n)
            if body is None and pfn:
                body = f"bundle {pfn}".encode()
            if body is None and "Stub" in n:
                body = f"stub {next(p for p in self.apps if 'G' + p.split('_')[0].replace('.', '') == g)}".encode()
            if body is None and "language-de" in n:
                body = f"lang {next(p for p in self.apps if 'G' + p.split('_')[0].replace('.', '') == g)}".encode()
            if body is None and "VCLibs" in n:
                body = b"vclibs framework in a selected group"
            if body is not None:
                payloads[live[n]["url"]] = body
        uup = os.path.join(self.tmp, "uup")
        dl = FakeDl(payloads)
        names = F.fetch_apps(self._manifest(rows), self.doc, uup, dl, FakeHttp(live))
        self.assertEqual(sorted(names), sorted(r["name"] for r in rows))
        self.assertEqual(sorted(os.listdir(uup)), sorted(r["name"] for r in rows))   # UUP Dump's IPA_ names, flat
        # an already verified file is kept (an interrupted fetch resumes)
        dl.got.clear()
        F.fetch_apps(self._manifest(rows), self.doc, uup, dl, FakeHttp(live))
        self.assertEqual(dl.got, [])

    def test_negative_tampered_live_list_or_stale_manifest_refused(self):
        allf = full_list(self.apps)
        rows = RL.resolve_apps(cfg(self.apps), allf, UUID)
        live = {n: {**v, "url": f"https://cdn.example.invalid/{n}"} for n, v in allf.items()}
        first = rows[0]["name"]
        live[first] = {**live[first], "sha256": "0" * 64}
        with self.assertRaisesRegex(F.FetchError, "live sha256 differs"):
            F.fetch_apps(self._manifest(rows), self.doc, os.path.join(self.tmp, "u1"), FakeDl({}), FakeHttp(live))
        # negative control: the round-1 manifest resolved no app; the spec now wants them -> re-resolve, never a silent skip
        with self.assertRaisesRegex(F.FetchError, "differ from windows.xbox.source.apps"):
            F.fetch_apps(self._manifest([]), self.doc, os.path.join(self.tmp, "u2"), FakeDl({}), FakeHttp(live))
        doc = copy.deepcopy(self.doc)
        doc["spec"]["source"]["apps"] = []
        self.assertEqual(F.fetch_apps(self._manifest([]), doc, os.path.join(self.tmp, "u3"), FakeDl({}), FakeHttp(live)), [])

    def test_converter_store_apps_and_custom_list(self):
        store, text = F.converter_store_apps(self.doc)
        self.assertEqual(store, {"StubAppsFull": 1, "SkipApps": 0, "CustomList": 1})
        lines = text.split("\r\n")
        self.assertTrue(lines[0].startswith("###"))
        self.assertEqual([ln for ln in lines[1:] if ln], self.apps)
        self.assertTrue(text.endswith("\r\n"))
        ini = F.render_converter_config(UPSTREAM_INI, {"AutoStart": 1, "SkipEdge": 0}, {"Store_Apps": store})
        sec = ini.split("[Store_Apps]")[1].split("[create_virtual_editions]")[0]
        self.assertIn("CustomList   =1", sec)
        self.assertIn("SkipApps     =0", sec)
        self.assertIn("AutoStart    =1", ini.split("[Store_Apps]")[0])
        self.assertIn("SkipEdge     =0", ini.split("[Store_Apps]")[0])
        # negative control: the round-1 render touched [convert-UUP] only -> CustomList stayed 0 (and no apps were staged)
        old = F.render_converter_config(UPSTREAM_INI, {"AutoStart": 1})
        self.assertIn("CustomList   =0", old.split("[Store_Apps]")[1])
        # negatives: an SSOT override of a derived key, an unknown [Store_Apps] key
        doc = copy.deepcopy(self.doc)
        doc["spec"]["build"]["converter"]["store_apps"]["CustomList"] = 0
        with self.assertRaisesRegex(F.FetchError, "derived from windows.xbox.source.apps"):
            F.converter_store_apps(doc)
        with self.assertRaisesRegex(F.FetchError, r"store_apps: upstream ConvertConfig.ini has no \[Store_Apps\] key\(s\) NoSuchKey"):
            F.render_converter_config(UPSTREAM_INI, {}, {"Store_Apps": {"NoSuchKey": 1}})
        doc["spec"]["source"]["apps"] = []
        del doc["spec"]["build"]["converter"]["store_apps"]["CustomList"]
        self.assertEqual(F.converter_store_apps(doc), ({"StubAppsFull": 1, "SkipApps": 1, "CustomList": 0}, None))

    def test_fetch_converter_writes_only_our_list(self):
        conv = os.path.join(self.tmp, "converter")
        os.makedirs(conv)
        for n, body in (("ConvertConfig.ini", UPSTREAM_INI), ("convert-UUP.cmd", "@echo off\r\n"),
                        ("CustomAppsList.txt", "Microsoft.WindowsStore_8wekyb3d8bbwe\r\n# Microsoft.Paint_8wekyb3d8bbwe\r\n"),
                        ("CustomAppsList2.txt", "Microsoft.Paint_8wekyb3d8bbwe\r\n")):
            with open(os.path.join(conv, n), "w", newline="") as fh:
                fh.write(body)
        saved = (F._safe_extract, F.RL.verify_tree)
        man = {"artifacts": {"uup_converter": {"integrity": {"kind": "git-tree"}, "fetch": "https://example.invalid/t.tgz",
                                               "subdir": "uup-converter-wimlib"}}}
        try:
            F._safe_extract = lambda *a, **k: conv
            F.RL.verify_tree = lambda *a, **k: None
            out = F.fetch_converter(man, self.doc, self.tmp, FakeDl({"https://example.invalid/t.tgz": b""}))
        finally:
            F._safe_extract, F.RL.verify_tree = saved
        self.assertEqual(out, conv)
        self.assertFalse(os.path.exists(os.path.join(conv, "CustomAppsList2.txt")), "the converter would prefer it")
        with open(os.path.join(conv, "CustomAppsList.txt"), newline="") as fh:
            listed = [ln for ln in fh.read().split("\r\n") if ln and not ln.startswith("#")]
        self.assertEqual(listed, self.apps)
        with open(os.path.join(conv, "ConvertConfig.ini"), newline="") as fh:
            self.assertIn("CustomList   =1", fh.read().split("[Store_Apps]")[1])


# ---------------------------------------------------------------------------
# 3. render_spec: what other SSOT values rely on must be integrated
# ---------------------------------------------------------------------------

class TestRenderChecks(Base):
    def render(self, m):
        return R.render(m, P.LAYERS)

    def test_positive_vendor_ssot_renders_with_apps(self):
        doc = self.render(copy.deepcopy(self.merged))
        self.assertEqual(doc["spec"]["source"]["apps"], self.apps)
        home = doc["spec"]["xbox"]["home_app"].split("!")[0]
        self.assertIn(home, self.apps)
        for r in R.HYDRATE_REQUIRES:
            self.assertIn(r, self.apps)

    def test_negative_home_app_hydrate_and_derived_keys(self):
        m = copy.deepcopy(self.merged)
        m["windows"]["xbox"]["source"]["apps"].remove("Microsoft.GamingApp_8wekyb3d8bbwe")
        with self.assertRaisesRegex(R.SpecError, "xbox.home_app"):
            self.render(m)
        m = copy.deepcopy(self.merged)
        m["windows"]["xbox"]["source"]["apps"] = ["Microsoft.GamingApp_8wekyb3d8bbwe"]   # no Store, no winget
        with self.assertRaisesRegex(R.SpecError, "store_hydrate.packages"):
            self.render(m)
        m = copy.deepcopy(self.merged)
        m["windows"]["xbox"]["source"]["apps"].append("NotAFamily")
        with self.assertRaisesRegex(R.SpecError, r"source.apps\[\d+\]"):
            self.render(m)
        m = copy.deepcopy(self.merged)
        m["windows"]["xbox"]["build"]["converter"]["store_apps"]["SkipApps"] = 0
        with self.assertRaisesRegex(R.SpecError, "store_apps.SkipApps"):
            self.render(m)
        # negative control: the round-1 SSOT (no source.apps) no longer renders
        m = copy.deepcopy(self.merged)
        del m["windows"]["xbox"]["source"]["apps"]
        with self.assertRaisesRegex(R.SpecError, "source.apps"):
            self.render(m)
        # an empty list is a valid choice only without a gaming home and without Store-hydrated packages
        m = copy.deepcopy(self.merged)
        m["windows"]["xbox"]["source"]["apps"] = []
        m["windows"]["xbox"]["xbox"]["home_app"] = ""
        m["windows"]["xbox"]["store_hydrate"]["packages"] = []
        self.assertEqual(self.render(m)["spec"]["source"]["apps"], [])


# ---------------------------------------------------------------------------
# 4. image gate + probe items: every integrated family is provisioned in install.wim
# ---------------------------------------------------------------------------

def _windowsapps_listing(apps):
    out = []
    for pfn in apps:
        name, pub = pfn.rsplit("_", 1)
        out += [f"/Program Files/WindowsApps/{name}_1.0.0.0_neutral_~_{pub}",
                f"/Program Files/WindowsApps/{name}_1.0.0.0_x64__{pub}",
                f"/Program Files/WindowsApps/{name}_1.0.0.0_x64__{pub}/AppxManifest.xml",
                f"/Program Files/WindowsApps/{name}_1.0.0.0_neutral_split.scale-100_{pub}"]
    return out + ["/Program Files/WindowsApps/Deleted", "/Program Files/WindowsApps/MovedPackages"]


class TestImageGate(Base):
    def items(self):
        return [it for it in S.items_from_rendered(self.doc, P.ROOT) if it["kind"] == "appx"]

    def test_items_carry_every_family(self):
        inbox = [it for it in self.items() if it.get("inbox")]
        self.assertEqual(sorted(it["pfn"] for it in inbox), sorted(self.apps))
        self.assertIn({"id": "source.apps.Microsoft.Xbox.TCUI_8wekyb3d8bbwe", "kind": "appx", "name": "Microsoft.Xbox.TCUI",
                       "pfn": "Microsoft.Xbox.TCUI_8wekyb3d8bbwe", "present": True, "inbox": True}, inbox)
        self.assertTrue(any(it["id"] == "store_hydrate.Microsoft.GamingServices" for it in self.items()))

    def test_windowsapps_families_parser(self):
        fams = C.windowsapps_families(_windowsapps_listing(self.apps))
        self.assertEqual(fams, {a.lower() for a in self.apps})
        self.assertEqual(C.windowsapps_families(ROUND1_WINDOWSAPPS),
                         {"microsoft.microsoftedge.stable_8wekyb3d8bbwe", "microsoft.onedrivesync_8wekyb3d8bbwe",
                          "microsoft.outlookforwindows_8wekyb3d8bbwe"})
        self.assertEqual(C.windowsapps_families(["/Program Files/WindowsApps/Deleted", "Microsoft.Bad_x64"]), set())

    def _eval(self, provisioned, mode="image"):
        obs = C.Observations()
        obs.provisioned_apps = provisioned
        results, findings = C.evaluate(self.items(), obs, mode)
        return {r["id"]: r["status"] for r in results}, {f.id for f in findings}

    def test_positive_and_round1_negative(self):
        status, findings = self._eval(C.windowsapps_families(_windowsapps_listing(self.apps)))
        for pfn in self.apps:
            self.assertEqual(status[f"source.apps.{pfn}"], "pass", pfn)
        self.assertEqual(status["store_hydrate.Microsoft.GamingServices"], "skipped")   # the probe's job
        # negative control: the round-1 image (Edge, OneDrive, Outlook only) fails every family
        status, findings = self._eval(C.windowsapps_families(ROUND1_WINDOWSAPPS))
        for pfn in self.apps:
            self.assertEqual(status[f"source.apps.{pfn}"], "fail", pfn)
            self.assertIn(f"missing:source.apps.{pfn}", findings)
        # one family missing is named; a listing that failed is a finding too, never a pass
        some = C.windowsapps_families(_windowsapps_listing(self.apps[1:]))
        status, findings = self._eval(some)
        self.assertEqual(status[f"source.apps.{self.apps[0]}"], "fail")
        self.assertEqual(status[f"source.apps.{self.apps[1]}"], "pass")
        status, _ = self._eval(None)
        self.assertEqual(status[f"source.apps.{self.apps[0]}"], "fail")
        # the rendered gate cannot see install.wim: skipped there, decided by the image gate and the probe
        status, _ = self._eval(None, mode="rendered")
        self.assertEqual(status[f"source.apps.{self.apps[0]}"], "skipped")


if __name__ == "__main__":
    unittest.main()
