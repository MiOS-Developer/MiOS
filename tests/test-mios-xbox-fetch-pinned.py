#!/usr/bin/env python3
# AI-hint: Two-sided tests for fetch.py's downloads: fetch_pinned's sha256 pin, cache and refusals, and the one file-name rule of fetch_pinned/fetch_file/fetch_virtio/fetch_fod (never "", "." or "..", so never the work dir or its parent). No network.
# AI-related: usr/libexec/mios/win/xbox/fetch.py, usr/share/mios/mios.toml, tests/test-mios-xbox-identity.py
"""Run: python3 -I tests/test-mios-xbox-fetch-pinned.py -v   (temp dirs only; no network)"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
XBOX = os.path.join(HERE, "..", "usr", "libexec", "mios", "win", "xbox")
sys.path.insert(0, XBOX)
import fetch as F  # noqa: E402

GOOD = b"the pinned cursor build"
PIN = hashlib.sha256(GOOD).hexdigest()
URL = "https://example.invalid/releases/download/v1/Cursor-Windows.zip"
NAME = "Cursor-Windows.zip"


class FakeDownloader:
    """Writes the chosen bytes to dest and records every call; never touches the network."""

    tries = 1

    def __init__(self, data: bytes):
        self.data = data
        self.calls = []

    @staticmethod
    def sleep(_s: float) -> None:
        return None

    def get(self, url: str, dest: str, size=None) -> None:
        self.calls.append((url, dest))
        with open(dest, "wb") as fh:
            fh.write(self.data)


class TestFetchPinned(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.mkdtemp(prefix="mios-fetch-pinned.")

    def tearDown(self):
        shutil.rmtree(self.work, ignore_errors=True)

    def files(self):
        return sorted(os.listdir(self.work))

    def cache(self, data: bytes) -> str:
        p = os.path.join(self.work, NAME)
        with open(p, "wb") as fh:
            fh.write(data)
        return p

    # --- positive controls -----------------------------------------------------------------------------------

    def test_positive_matching_bytes_return_the_path(self):
        dl = FakeDownloader(GOOD)
        path = F.fetch_pinned(URL, PIN, self.work, dl, "identity.cursor")
        self.assertEqual(path, os.path.join(self.work, NAME))
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(), GOOD)
        self.assertEqual(dl.calls, [(URL, path)])
        self.assertEqual(self.files(), [NAME])

    def test_positive_a_matching_cached_file_is_reused_without_a_download(self):
        cached = self.cache(GOOD)
        dl = FakeDownloader(b"never written")
        self.assertEqual(F.fetch_pinned(URL, PIN, self.work, dl, "identity.cursor"), cached)
        self.assertEqual(dl.calls, [])
        with open(cached, "rb") as fh:
            self.assertEqual(fh.read(), GOOD)

    def test_positive_a_stale_cached_file_is_replaced(self):
        self.cache(b"an older build")
        dl = FakeDownloader(GOOD)
        path = F.fetch_pinned(URL, PIN, self.work, dl, "identity.cursor")
        self.assertEqual(len(dl.calls), 1)
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(), GOOD)
        self.assertEqual(self.files(), [NAME])

    # --- negative controls: each refused with FetchError, nothing left behind ----------------------------------

    def assertRefused(self, url: str, pin: str, dl: FakeDownloader, why: str, downloads: int):
        with self.assertRaisesRegex(F.FetchError, why):
            F.fetch_pinned(url, pin, self.work, dl, "identity.cursor")
        self.assertEqual(len(dl.calls), downloads)
        self.assertEqual(self.files(), [], "a refused fetch must leave no file")

    def test_negative_plain_http_url(self):
        self.assertRefused("http://example.invalid/Cursor-Windows.zip", PIN, FakeDownloader(GOOD), "not an https URL", 0)

    def test_negative_pin_not_a_lower_case_sha256(self):
        for pin in (PIN.upper(), PIN[:-1], "", "g" * 64, PIN + "0"):
            with self.subTest(pin=pin[:12]):
                self.assertRefused(URL, pin, FakeDownloader(GOOD), "not a lower-case sha256", 0)

    def test_negative_unsafe_file_name(self):
        # ".." and "." would name the work directory's parent / itself; "" a directory URL; %-escapes are not decoded
        for url in ("https://example.invalid/releases/", "https://example.invalid/a%2F..%2Fx.zip",
                    "https://example.invalid/a%20b.zip", "https://example.invalid/..", "https://example.invalid/x/.",
                    "https://example.invalid/.hidden.zip"):
            with self.subTest(url=url):
                self.assertRefused(url, PIN, FakeDownloader(GOOD), "unsafe file name", 0)

    def test_negative_downloaded_bytes_differ_from_the_pin(self):
        self.assertRefused(URL, PIN, FakeDownloader(b"a tampered build"), "differs from the SSOT pin", 1)

    def test_negative_stale_cache_and_wrong_download(self):
        self.cache(b"an older build")
        self.assertRefused(URL, PIN, FakeDownloader(b"a tampered build"), "differs from the SSOT pin", 1)


class FakeUupApi:
    """UUP Dump's get.php answer for fetch_fod: the live file list (name -> sha256 + url)."""

    def __init__(self, files: dict):
        self.files = files

    def request(self, url, method="GET", follow=True, headers=None, limit=None):
        return F.RL.Response(200, {}, __import__("json").dumps({"response": {"files": self.files}}).encode())


class TestFetchNameGuards(unittest.TestCase):
    """Every name joined onto the work directory follows fetch_pinned's rule: a letter or digit first."""
    CANON = "Microsoft-OpenSSH-Server-Package~31bf3856ad364e35~amd64~~.cab"

    def setUp(self):
        self.work = tempfile.mkdtemp(prefix="mios-fetch-names.")

    def tearDown(self):
        shutil.rmtree(self.work, ignore_errors=True)

    def files(self):
        return sorted(os.listdir(self.work))

    def manifest(self, artifact: str, url: str) -> dict:
        return {"artifacts": {artifact: {"fetch": url, "integrity": {"kind": "sha256", "value": PIN}}}}

    def assertRefused(self, fn, why: str = "unsafe"):
        dl = FakeDownloader(GOOD)
        with self.assertRaisesRegex(F.FetchError, why):
            fn(dl)
        self.assertEqual((dl.calls, self.files()), ([], []), "a refused name must download and leave nothing")

    # --- fetch_file: the manifest URL's name, or an explicit one ------------------------------------------------

    def test_positive_fetch_file_good_names(self):
        p = F.fetch_file(self.manifest("tool", "https://example.invalid/d/Tool-1.zip"), "tool", self.work, FakeDownloader(GOOD))
        self.assertEqual(p, os.path.join(self.work, "Tool-1.zip"))
        p = F.fetch_file(self.manifest("tool", "https://example.invalid/d/x"), "tool", self.work, FakeDownloader(GOOD), "tool.msi")
        self.assertEqual(p, os.path.join(self.work, "tool.msi"))

    def test_negative_fetch_file_dot_names(self):
        for url, name in (("https://example.invalid/d/..", None), ("https://example.invalid/d/", None),
                          ("https://example.invalid/d/x.zip", "."), ("https://example.invalid/d/x.zip", ".."),
                          ("https://example.invalid/d/x.zip", ""), ("https://example.invalid/d/.hidden", None)):
            with self.subTest(url=url, name=name):
                self.assertRefused(lambda dl: F.fetch_file(self.manifest("tool", url), "tool", self.work, dl, name))

    # --- fetch_virtio: the RPM URL's name ------------------------------------------------------------------------

    def virtio(self, url: str, dl):
        spec = {"spec": {"drivers": {"virtio_win": {}}, "guest_tools": {}}}
        return F.fetch_virtio(self.manifest("virtio_win", url), spec, self.work, dl)

    def test_positive_fetch_virtio_good_name_passes_the_guard(self):
        dl = FakeDownloader(GOOD)
        with self.assertRaises(F.FetchError) as cm:   # the fixture bytes are no RPM: it fails later, past the guard
            self.virtio("https://example.invalid/d/virtio-win-0.1.302-1.noarch.rpm", dl)
        self.assertNotIn("unsafe", str(cm.exception))
        self.assertEqual(len(dl.calls), 1)

    def test_negative_fetch_virtio_dot_names(self):
        for url in ("https://example.invalid/d/..", "https://example.invalid/d/.", "https://example.invalid/d/"):
            with self.subTest(url=url):
                self.assertRefused(lambda dl: self.virtio(url, dl))

    # --- fetch_fod: the FOD file name and its canonical repository name -----------------------------------------

    def fod(self, name: str, canonical: str, dl):
        files = [{"name": name, "canonical": canonical, "sha256": PIN, "size": len(GOOD)}]
        manifest = {"artifacts": {"windows_uup": {"uuid": "u", "integrity": {"kind": "sha256-per-file"}, "fod_files": files}}}
        spec = {"spec": {"features": {"capabilities_add": ["OpenSSH.Server"]}, "resolve": {"uupdump_api": "https://api.invalid"}}}
        api = FakeUupApi({name: {"sha256": PIN, "url": "https://example.invalid/fod.cab"}})
        return F.fetch_fod(manifest, spec, self.work, dl, api)

    def test_positive_fetch_fod_stages_the_canonical_name(self):
        dl = FakeDownloader(GOOD)
        self.fod("cabs_OpenSSH-Server-Package-amd64.cab", self.CANON, dl)
        self.assertEqual(self.files(), [self.CANON])
        self.assertEqual(len(dl.calls), 1)

    def test_negative_fetch_fod_dot_names(self):
        for name, canon in (("cabs_x.cab", ".."), ("cabs_x.cab", "."), (".", self.CANON), ("..", self.CANON),
                            ("cabs_x.cab", "~x.cab")):
            with self.subTest(name=name, canonical=canon):
                self.assertRefused(lambda dl: self.fod(name, canon, dl), "unsafe FOD file name")


if __name__ == "__main__":
    unittest.main()
