#!/usr/bin/env python3
# AI-hint: Automated unit test suite for Git LFS Sparse Fetcher & Shared Cache (task_659, task_660).
# AI-related: usr/libexec/mios/git/lfs_pull.py, tests/test-lfs-cache.py
"""Automated unit test suite for MiOS Git LFS Sparse Cache Manager."""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "usr", "libexec", "mios", "git"))

from lfs_pull import LFSSparseCacheManager

class TestLFSCache(unittest.TestCase):
    def setUp(self):
        self.tmp_cache = tempfile.mkdtemp(prefix="mios-lfs-test-")
        self.mgr = LFSSparseCacheManager(cache_root=self.tmp_cache, dry_run=True)

    def tearDown(self):
        shutil.rmtree(self.tmp_cache, ignore_errors=True)

    def test_sparse_pull_and_cache_deduplication(self):
        """Test initial pull caches blob and subsequent pull hits local cache."""
        payload = b"QUANTIZED_GGUF_BLOB_CONTENT_DATA"
        # First pull: not cached
        r1 = self.mgr.fetch_sparse_blob("model-q4.gguf", payload)
        self.assertFalse(r1.was_cached)
        self.assertEqual(r1.size_bytes, len(payload))

        # Second pull: instant cache hit
        r2 = self.mgr.fetch_sparse_blob("model-q4.gguf", payload)
        self.assertTrue(r2.was_cached)
        self.assertEqual(r1.sha256_hash, r2.sha256_hash)

if __name__ == "__main__":
    unittest.main()
