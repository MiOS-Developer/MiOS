#!/usr/bin/env python3
# AI-hint: Two-sided tests for fetch.fetch_pinned (xbox-bm-39), the build-time download of a release asset its upstream publishes no digest for (the Windows cursor build), pinned by sha256 in the SSOT: matching bytes return the path and a matching cached file is reused without a download; a stale cached file is replaced; a plain-http URL, a pin that is not a lower-case sha256, an unsafe file name and downloaded bytes that differ from the pin are refused with FetchError, and no file is left behind. A fake Downloader writes chosen bytes and records its calls. No network.
# AI-related: usr/libexec/mios/win/xbox/fetch.py, usr/share/mios/mios.toml [theme.cursor_windows], tests/test-mios-xbox-identity.py
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

    def __init__(self, data: bytes):
        self.data = data
        self.calls = []

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


if __name__ == "__main__":
    unittest.main()
