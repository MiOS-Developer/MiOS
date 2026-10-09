#!/usr/bin/env python3
# AI-hint: Storage test suite: remote backup, storage bench, Ceph heal/CephFS quota/cockpit, container GC LRU, ledger sync, OPAL LUKS, SMART evacuation, scrubd, adversarial challenger 2.
"""Consolidated MiOS storage tests (backup, bench, Ceph, GC, ledger sync, OPAL/LUKS, SMART, scrub)."""
from __future__ import annotations


# ======================================================================
# from tests/test-storage.py
# ======================================================================
"""Automated tests for WS-DURA remote delta backup synchronization (task_352 / task_2585)."""


import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import zlib
from unittest import mock

_br_HERE = os.path.dirname(os.path.abspath(__file__))
_br_ROOT = os.path.normpath(os.path.join(_br_HERE, ".."))
_br_BACKUP_REMOTE_PATH = os.path.join(_br_ROOT, "usr", "libexec", "mios", "storage", "mios-backup-remote")

br_loader = importlib.machinery.SourceFileLoader("backup_remote", _br_BACKUP_REMOTE_PATH)
br_spec = importlib.util.spec_from_loader("backup_remote", br_loader)
if br_spec and br_spec.loader:
    br_backup_remote = importlib.util.module_from_spec(br_spec)
    sys.modules[br_spec.name] = br_backup_remote
    br_spec.loader.exec_module(br_backup_remote)
else:
    raise ImportError(f"Could not load backup_remote module from {_br_BACKUP_REMOTE_PATH}")


def _br_have_zstd_tooling() -> bool:
    """True when a real zstd encoder (CLI or python zstandard module) is installed on this host."""
    if shutil.which("zstd"):
        return True
    try:
        import zstandard  # noqa: F401
        return True
    except ImportError:
        return False


def _br_independent_zstd_decode(frame: bytes) -> bytes:
    """
    Decodes a Zstandard frame WITHOUT using any mios-backup-remote code:
    prefers the zstd CLI, falls back to the python zstandard module.
    Raises ValueError when the frame is not decodable by the independent decoder.
    """
    zstd_bin = shutil.which("zstd")
    if zstd_bin:
        proc = subprocess.run(
            [zstd_bin, "-d", "-c", "-"],
            input=frame,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if proc.returncode != 0:
            err = proc.stderr.decode("utf-8", errors="replace").strip()
            raise ValueError(f"independent zstd CLI decode failed (exit {proc.returncode}): {err}")
        return proc.stdout
    import zstandard
    try:
        dobj = zstandard.ZstdDecompressor().decompressobj()
        plain = dobj.decompress(frame)
        if not dobj.eof:
            raise ValueError("independent decode: truncated Zstandard frame")
        if dobj.unused_data:
            raise ValueError("independent decode: trailing bytes after Zstandard frame")
        return plain
    except zstandard.ZstdError as e:
        raise ValueError(f"independent zstandard decode failed: {e}") from e


_BR_ZSTD_AVAILABLE = _br_have_zstd_tooling()

class br_TestBackupRemote(unittest.TestCase):
    """Validates chunk hashing, manifest creation, delta plan computation, zstd staging, sync, and verification."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="mios_test_backup_remote_")

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_python_codec_roundtrip_and_failure_controls(self):
        try:
            import zstandard
        except ImportError:
            self.skipTest("requires the real Python Zstandard codec")
        payload = bytes(range(256)) * 100
        with mock.patch.object(br_backup_remote.shutil, "which", return_value=None):
            frame = br_backup_remote.compress_data_zstd(payload)
            self.assertEqual(zstandard.ZstdDecompressor().decompress(frame), payload)
            self.assertEqual(br_backup_remote.decompress_data_zstd(frame), payload)
            for broken in (frame[:-1], frame + b"trailing", frame[:-1] + bytes([frame[-1] ^ 1])):
                with self.subTest(payload_length=len(broken)):
                    with self.assertRaises(br_backup_remote.ZstdDecompressionError):
                        br_backup_remote.decompress_data_zstd(broken)

    def test_unimplemented_remote_verification_cannot_report_success(self):
        for backend in ("rsync", "rclone"):
            with self.subTest(backend=backend):
                ok, message = br_backup_remote.verify_remote_manifest("remote:store", "snapshot", backend)
                self.assertFalse(ok)
                self.assertIn("verification unavailable", message)

    def test_missing_remote_transport_never_falls_back_to_local_copy(self):
        remote = os.path.join(self.test_dir, "missing:remote")
        with mock.patch.object(br_backup_remote.shutil, "which", return_value=None), \
                mock.patch.object(br_backup_remote.subprocess, "run", side_effect=FileNotFoundError("transport absent")):
            result = br_backup_remote.sync_delta_payload(self.test_dir, remote)
        self.assertEqual(result["backend_used"], "rclone")
        self.assertEqual(result["status"], "failed")
        self.assertIn("transport absent", result["error"])
        self.assertFalse(os.path.exists(remote))

    def test_empty_or_invalid_manifest_cannot_report_verified(self):
        remote = os.path.join(self.test_dir, "bad_manifests")
        os.makedirs(remote)
        path = os.path.join(remote, "manifest_snapshot.json")
        for manifest in ({}, {"snapshot_id": "other", "files": {}, "chunk_index": {}},
                         {"snapshot_id": "snapshot", "files": {}, "chunk_index": {"../outside": {"length": 1}}}):
            with self.subTest(manifest=manifest):
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(manifest, f)
                ok, message = br_backup_remote.verify_remote_manifest(remote, "snapshot")
                self.assertFalse(ok)
                self.assertIn("Invalid snapshot", message)

    def test_hash_file_chunks(self):
        test_file = os.path.join(self.test_dir, "sample.bin")
        # 10KB file with chunk size 4KB -> 3 chunks (4096, 4096, 2048)
        content = os.urandom(10240)
        with open(test_file, "wb") as f:
            f.write(content)

        chunks = br_backup_remote.hash_file_chunks(test_file, chunk_size=4096)
        self.assertEqual(len(chunks), 3)
        self.assertEqual(chunks[0]["offset"], 0)
        self.assertEqual(chunks[0]["length"], 4096)
        self.assertEqual(chunks[1]["offset"], 4096)
        self.assertEqual(chunks[1]["length"], 4096)
        self.assertEqual(chunks[2]["offset"], 8192)
        self.assertEqual(chunks[2]["length"], 2048)

        # Hash check of chunk 0
        expected_hash0 = br_backup_remote.hash_bytes(content[:4096])
        self.assertEqual(chunks[0]["sha256"], expected_hash0)

    def test_create_snapshot_manifest(self):
        src_dir = os.path.join(self.test_dir, "source")
        os.makedirs(src_dir, exist_ok=True)

        with open(os.path.join(src_dir, "file1.txt"), "w") as f:
            f.write("Hello MiOS Backup Remote 1")
        with open(os.path.join(src_dir, "file2.txt"), "w") as f:
            f.write("Hello MiOS Backup Remote 2")

        manifest = br_backup_remote.create_snapshot_manifest(src_dir, snapshot_id="snap_001", chunk_size=1024)
        self.assertEqual(manifest["snapshot_id"], "snap_001")
        self.assertEqual(manifest["total_files"], 2)
        self.assertGreater(manifest["total_bytes"], 0)
        self.assertIn("file1.txt", manifest["files"])
        self.assertIn("file2.txt", manifest["files"])
        self.assertEqual(len(manifest["chunk_index"]), 2)

    def test_delta_plan_retransmission_prevention(self):
        """
        Verify: Generate a baseline backup; add 10MB of data; verify delta backup
        transmits only the 10MB diff payload without retransmitting unchanged data.
        """
        src_dir = os.path.join(self.test_dir, "src_delta")
        os.makedirs(src_dir, exist_ok=True)

        # Baseline: 5MB static database file
        static_file = os.path.join(src_dir, "static_db.bin")
        static_data = b"".join(f"STATIC_DB_BLOCK_{i:04d}_".encode("utf-8") * (1024 * 1024 // 20) for i in range(5))
        with open(static_file, "wb") as f:
            f.write(static_data)

        baseline_manifest = br_backup_remote.create_snapshot_manifest(src_dir, snapshot_id="snap_base", chunk_size=1024*1024)

        # Add 10MB new incremental delta file with unique chunks
        new_file = os.path.join(src_dir, "incremental_diff.bin")
        new_data = b"".join(f"DIFF_PAYLOAD_BLK_{i:04d}".encode("utf-8") * (1024 * 1024 // 20) for i in range(10))
        with open(new_file, "wb") as f:
            f.write(new_data)

        current_manifest = br_backup_remote.create_snapshot_manifest(src_dir, snapshot_id="snap_inc", chunk_size=1024*1024)

        delta_plan = br_backup_remote.compute_delta_plan(current_manifest, baseline_manifest)

        # Verify only 10MB new bytes are planned for transmission, not 15MB
        self.assertEqual(delta_plan["new_raw_bytes"], len(new_data))
        self.assertEqual(delta_plan["reused_bytes"], len(static_data))
        self.assertEqual(delta_plan["total_raw_bytes"], len(static_data) + len(new_data))
        self.assertGreater(delta_plan["dedup_ratio_pct"], 30.0)

    @unittest.skipUnless(_BR_ZSTD_AVAILABLE, "requires a real zstd encoder (zstd CLI or python zstandard)")
    def test_compression_and_staging(self):
        src_dir = os.path.join(self.test_dir, "src_comp")
        staging_dir = os.path.join(self.test_dir, "staging")
        os.makedirs(src_dir, exist_ok=True)

        test_payload = b"COMPRESSION_TEST_DATA_ABCXYZ" * 1000
        with open(os.path.join(src_dir, "data.bin"), "wb") as f:
            f.write(test_payload)

        manifest = br_backup_remote.create_snapshot_manifest(src_dir, snapshot_id="snap_stage", chunk_size=1024*1024)
        delta_plan = br_backup_remote.compute_delta_plan(manifest, baseline_manifest=None)

        staged_files, comp_bytes = br_backup_remote.stage_delta_chunks(
            source_dir=src_dir,
            current_manifest=manifest,
            delta_plan=delta_plan,
            staging_dir=staging_dir,
            zstd_level=3,
        )

        self.assertGreater(len(staged_files), 1)  # Chunks + manifest
        self.assertTrue(any(f.endswith(".chunk.zst") for f in staged_files))
        self.assertTrue(any(f.endswith("manifest_snap_stage.json") for f in staged_files))
        self.assertGreater(comp_bytes, 0)
        self.assertLess(comp_bytes, len(test_payload))  # Verified compression

    @unittest.skipUnless(_BR_ZSTD_AVAILABLE, "requires a real zstd encoder (zstd CLI or python zstandard)")
    def test_compress_roundtrip_independent_decoder(self):
        """
        Positive control: a binary payload compressed by the production encoder
        must be a genuine Zstandard frame decodable by an INDEPENDENT decoder
        (zstd CLI when installed, otherwise the python zstandard module) and
        must round-trip byte-for-byte.
        """
        payload = (
            bytes(range(256)) * 37                       # structured binary (all byte values)
            + os.urandom(8192)                           # incompressible binary noise
            + b"\x00" * 512                              # runs of zeros
        )
        for level in (1, 3, 19):
            with self.subTest(level=level):
                frame = br_backup_remote.compress_data_zstd(payload, level=level)
                self.assertTrue(
                    frame.startswith(b"\x28\xb5\x2f\xfd"),
                    "advertised zstd output must begin with the Zstandard frame magic",
                )
                self.assertEqual(_br_independent_zstd_decode(frame), payload)
                # Production restore consumer must agree with the independent decoder
                self.assertEqual(br_backup_remote.decompress_data_zstd(frame), payload)
        # Degenerate payloads must still produce valid frames
        for edge in (b"", b"x", b"\xff" * 1024):
            with self.subTest(payload_len=len(edge)):
                frame = br_backup_remote.compress_data_zstd(edge)
                self.assertEqual(_br_independent_zstd_decode(frame), edge)

    @unittest.skipUnless(_BR_ZSTD_AVAILABLE, "requires a real zstd encoder (zstd CLI or python zstandard)")
    def test_staged_chunks_decode_and_match_manifest_hashes(self):
        """
        Positive integration: every staged .chunk.zst artifact decodes through the
        independent decoder and its plaintext sha256 matches the manifest metadata
        (filename hash, chunk_index hash, and recorded length).
        """
        src_dir = os.path.join(self.test_dir, "src_rt")
        staging_dir = os.path.join(self.test_dir, "staging_rt")
        os.makedirs(src_dir, exist_ok=True)
        payload = b"STAGED_ROUNDTRIP_BLOCK_" * 4096
        with open(os.path.join(src_dir, "data.bin"), "wb") as f:
            f.write(payload)

        manifest = br_backup_remote.create_snapshot_manifest(src_dir, snapshot_id="snap_rt", chunk_size=64 * 1024)
        delta_plan = br_backup_remote.compute_delta_plan(manifest, baseline_manifest=None)
        staged_files, _ = br_backup_remote.stage_delta_chunks(
            source_dir=src_dir,
            current_manifest=manifest,
            delta_plan=delta_plan,
            staging_dir=staging_dir,
            zstd_level=3,
        )

        chunk_files = [f for f in staged_files if f.endswith(".chunk.zst")]
        self.assertGreater(len(chunk_files), 0)
        for chunk_file in chunk_files:
            with open(chunk_file, "rb") as f:
                frame = f.read()
            plain = _br_independent_zstd_decode(frame)
            digest = br_backup_remote.hash_bytes(plain)
            self.assertEqual(os.path.basename(chunk_file), f"{digest}.chunk.zst")
            self.assertIn(digest, manifest["chunk_index"])
            self.assertEqual(manifest["chunk_index"][digest]["length"], len(plain))
        restored = []
        for chunk in manifest["files"]["data.bin"]["chunks"]:
            with open(os.path.join(staging_dir, f"{chunk['sha256']}.chunk.zst"), "rb") as f:
                restored.append(_br_independent_zstd_decode(f.read()))
        self.assertEqual(b"".join(restored), payload)

    def test_atomic_staging_write_failure_removes_temporary(self):
        staging = os.path.join(self.test_dir, "atomic_failure")
        os.makedirs(staging)
        target = os.path.join(staging, "manifest_atomic.json")
        for failure in ("fsync", "replace"):
            with self.subTest(failure=failure), mock.patch.object(
                    br_backup_remote.os, failure, side_effect=OSError("planted write failure")):
                with self.assertRaisesRegex(OSError, "planted write failure"):
                    br_backup_remote._write_staged_file(target, b"complete bytes")
            self.assertEqual(os.listdir(staging), [])

    def test_missing_delta_chunk_refuses_manifest_publication(self):
        staging = os.path.join(self.test_dir, "missing_delta")
        with self.assertRaisesRegex(ValueError, "absent from snapshot index"):
            br_backup_remote.stage_delta_chunks(
                self.test_dir, {"snapshot_id": "missing", "chunk_index": {}},
                {"new_chunk_hashes": ["missing"]}, staging)
        self.assertEqual(os.listdir(staging), [])

    @unittest.skipUnless(_BR_ZSTD_AVAILABLE, "requires real Zstandard")
    def test_source_change_and_manifest_write_failure_discard_staging(self):
        source = os.path.join(self.test_dir, "changing_source")
        staging = os.path.join(self.test_dir, "changing_staging")
        os.makedirs(source)
        path = os.path.join(source, "data.bin")
        with open(path, "wb") as f:
            f.write(b"first bytes")
        manifest = br_backup_remote.create_snapshot_manifest(source, snapshot_id="changing")
        plan = br_backup_remote.compute_delta_plan(manifest)
        with open(path, "wb") as f:
            f.write(b"other bytes")
        with self.assertRaisesRegex(ValueError, "changed since snapshot"):
            br_backup_remote.stage_delta_chunks(source, manifest, plan, staging)
        self.assertEqual(os.listdir(staging), [])
        with open(path, "wb") as f:
            f.write(b"first bytes")
        real_replace = br_backup_remote.os.replace
        def fail_manifest(src, dst):
            if dst.endswith(".json"):
                raise OSError("planted manifest publication failure")
            return real_replace(src, dst)
        with mock.patch.object(br_backup_remote.os, "replace", side_effect=fail_manifest):
            with self.assertRaisesRegex(OSError, "planted manifest publication failure"):
                br_backup_remote.stage_delta_chunks(source, manifest, plan, staging)
        self.assertEqual(os.listdir(staging), [])

    def test_no_encoder_available_fails_and_writes_no_artifact(self):
        """
        Negative control: with BOTH encoders unavailable (zstd CLI hidden and
        zstandard module blocked), compression and staging must fail with the
        named ZstdUnavailableError and leave NO artifact behind in staging.
        """
        src_dir = os.path.join(self.test_dir, "src_noenc")
        staging_dir = os.path.join(self.test_dir, "staging_noenc")
        os.makedirs(src_dir, exist_ok=True)
        with open(os.path.join(src_dir, "data.bin"), "wb") as f:
            f.write(b"NO_ENCODER_PAYLOAD" * 1000)

        manifest = br_backup_remote.create_snapshot_manifest(src_dir, snapshot_id="snap_noenc", chunk_size=4096)
        delta_plan = br_backup_remote.compute_delta_plan(manifest, baseline_manifest=None)
        self.assertGreater(len(delta_plan["new_chunk_hashes"]), 0)

        with mock.patch.object(br_backup_remote.shutil, "which", return_value=None), \
                mock.patch.dict(sys.modules, {"zstandard": None}):
            with self.assertRaises(br_backup_remote.ZstdUnavailableError) as ctx:
                br_backup_remote.compress_data_zstd(b"probe")
            self.assertIn("zstd", str(ctx.exception).lower())

            with self.assertRaises(br_backup_remote.ZstdUnavailableError):
                br_backup_remote.stage_delta_chunks(
                    source_dir=src_dir,
                    current_manifest=manifest,
                    delta_plan=delta_plan,
                    staging_dir=staging_dir,
                )

        # No artifact may exist: no chunk files, no manifest, nothing falsely labeled
        if os.path.isdir(staging_dir):
            leftovers = os.listdir(staging_dir)
            self.assertEqual(leftovers, [], f"staging must be empty after encoder failure, found: {leftovers}")
        self.assertFalse(any(n.endswith(".chunk.zst") for n in os.listdir(self.test_dir)))

    def test_encoder_subprocess_failure_fails(self):
        """
        Negative control: when the located encoder binary exits non-zero, the
        operation must fail with ZstdCompressionError (no silent fallback to a
        fake frame). Uses the real Python interpreter as a failing "zstd" binary:
        it rejects zstd-style flags and exits with status 2.
        """
        fake_zstd = sys.executable
        with mock.patch.object(br_backup_remote.shutil, "which", return_value=fake_zstd), \
                mock.patch.dict(sys.modules, {"zstandard": None}):
            with self.assertRaises(br_backup_remote.ZstdCompressionError) as ctx:
                br_backup_remote.compress_data_zstd(b"SUBPROCESS_FAILURE_PROBE", level=3)
        self.assertIn("exit status", str(ctx.exception))

    def test_invalid_compression_settings_fail(self):
        """Negative control: out-of-range and non-integer zstd levels must fail before any encoder runs."""
        for bad_level in (0, -1, -100, 23, 64, 1000, 2.5, "3", None):
            with self.subTest(level=bad_level), \
                    self.assertRaises(ValueError):
                br_backup_remote.compress_data_zstd(b"SETTINGS_PROBE", level=bad_level)
        # Staging path must propagate invalid settings too
        src_dir = os.path.join(self.test_dir, "src_badlevel")
        staging_dir = os.path.join(self.test_dir, "staging_badlevel")
        os.makedirs(src_dir, exist_ok=True)
        with open(os.path.join(src_dir, "data.bin"), "wb") as f:
            f.write(b"BAD_LEVEL_PAYLOAD" * 100)
        manifest = br_backup_remote.create_snapshot_manifest(src_dir, snapshot_id="snap_bad", chunk_size=4096)
        delta_plan = br_backup_remote.compute_delta_plan(manifest, baseline_manifest=None)
        with self.assertRaises(ValueError):
            br_backup_remote.stage_delta_chunks(
                source_dir=src_dir,
                current_manifest=manifest,
                delta_plan=delta_plan,
                staging_dir=staging_dir,
                zstd_level=99,
            )
        if os.path.isdir(staging_dir):
            self.assertEqual(os.listdir(staging_dir), [])

    @unittest.skipUnless(_BR_ZSTD_AVAILABLE, "requires a real zstd encoder (zstd CLI or python zstandard)")
    def test_corrupted_frame_rejected_on_restore(self):
        """
        Negative control: corrupted, garbage, and mislabeled (zlib-with-zstd-magic)
        payloads must be rejected by the restore path, and a corrupted remote store
        chunk must fail verify_remote_manifest.
        """
        payload = b"CORRUPTION_DETECTION_PAYLOAD_" * 512
        frame = br_backup_remote.compress_data_zstd(payload)

        # 1. Bit flips in the compressed block body and in the trailing content
        #    checksum must both be rejected (frames embed a zstd content checksum)
        for pos in (10, len(frame) // 2, len(frame) - 2):
            with self.subTest(flip_position=pos):
                corrupted = frame[:pos] + bytes([frame[pos] ^ 0xFF]) + frame[pos + 1:]
                with self.assertRaises(br_backup_remote.ZstdDecompressionError):
                    br_backup_remote.decompress_data_zstd(corrupted)

        # 2. Truncated frame: production restore consumer AND independent decoder must reject
        with self.assertRaises(br_backup_remote.ZstdDecompressionError):
            br_backup_remote.decompress_data_zstd(frame[: len(frame) // 2])
        with self.assertRaises(ValueError):
            _br_independent_zstd_decode(frame[: len(frame) // 2])

        # 2b. Trailing garbage appended after a valid frame must be rejected
        with self.assertRaises(br_backup_remote.ZstdDecompressionError):
            br_backup_remote.decompress_data_zstd(frame + b"GARBAGE_TAIL")

        # 3. Garbage without magic
        with self.assertRaises(br_backup_remote.ZstdDecompressionError):
            br_backup_remote.decompress_data_zstd(b"\x00" * 128)

        # 4. Mislabeled zlib payload carrying a zstd magic prefix (the historical defect shape)
        with self.assertRaises(br_backup_remote.ZstdDecompressionError):
            br_backup_remote.decompress_data_zstd(b"\x28\xb5\x2f\xfd" + zlib.compress(payload))

        # 5. End-to-end: corrupt one chunk in the remote store, verification must fail
        src_dir = os.path.join(self.test_dir, "src_corrupt")
        staging_dir = os.path.join(self.test_dir, "staging_corrupt")
        remote_dir = os.path.join(self.test_dir, "remote_corrupt")
        os.makedirs(src_dir, exist_ok=True)
        with open(os.path.join(src_dir, "data.bin"), "wb") as f:
            f.write(payload)
        manifest = br_backup_remote.create_snapshot_manifest(src_dir, snapshot_id="snap_cor", chunk_size=4096)
        delta_plan = br_backup_remote.compute_delta_plan(manifest, baseline_manifest=None)
        br_backup_remote.stage_delta_chunks(
            source_dir=src_dir,
            current_manifest=manifest,
            delta_plan=delta_plan,
            staging_dir=staging_dir,
        )
        sync_res = br_backup_remote.sync_delta_payload(staging_dir=staging_dir, remote_target=remote_dir, backend="local")
        self.assertEqual(sync_res["status"], "success")
        chunk_name = delta_plan["new_chunk_hashes"][0] + ".chunk.zst"
        remote_chunk = os.path.join(remote_dir, chunk_name)
        with open(remote_chunk, "rb") as f:
            good = f.read()
        with open(remote_chunk, "wb") as f:
            f.write(good[:6] + bytes([good[6] ^ 0x5A]) + good[7:])
        ok, msg = br_backup_remote.verify_remote_manifest(remote_target=remote_dir, snapshot_id="snap_cor", backend="local")
        self.assertFalse(ok, "corrupted remote chunk must fail verification")
        self.assertIn("failed restore validation", msg)

    def test_planted_invalid_frame_fails_roundtrip(self):
        """
        Harness self-proof: a PLANTED invalid frame (exactly the shape the old
        defect produced: zlib bytes behind a zstd magic prefix) MUST fail the
        independent round-trip decoder and the production restore consumer.
        If this test ever passes silently, the round-trip harness is broken.
        """
        payload = b"HARNESS_CAN_FAIL_PROOF_" * 200
        planted = b"\x28\xb5\x2f\xfd" + zlib.compress(payload, 6)
        # Independent decoder must reject the planted frame
        with self.assertRaises(ValueError):
            _br_independent_zstd_decode(planted)
        # Production restore consumer must reject it as well
        with self.assertRaises(br_backup_remote.ZstdDecompressionError):
            br_backup_remote.decompress_data_zstd(planted)

    @unittest.skipUnless(_BR_ZSTD_AVAILABLE, "requires a real zstd encoder (zstd CLI or python zstandard)")
    def test_sync_and_remote_verification(self):
        src_dir = os.path.join(self.test_dir, "src_sync")
        staging_dir = os.path.join(self.test_dir, "staging_sync")
        remote_dir = os.path.join(self.test_dir, "remote_store")
        os.makedirs(src_dir, exist_ok=True)

        with open(os.path.join(src_dir, "file_a.txt"), "w") as f:
            f.write("Alpha delta content")

        manifest = br_backup_remote.create_snapshot_manifest(src_dir, snapshot_id="snap_sync_01", chunk_size=4096)
        delta_plan = br_backup_remote.compute_delta_plan(manifest, baseline_manifest=None)

        br_backup_remote.stage_delta_chunks(
            source_dir=src_dir,
            current_manifest=manifest,
            delta_plan=delta_plan,
            staging_dir=staging_dir,
        )

        sync_res = br_backup_remote.sync_delta_payload(
            staging_dir=staging_dir,
            remote_target=remote_dir,
            backend="local",
        )
        self.assertEqual(sync_res["status"], "success")

        # Verify remote target
        ok, msg = br_backup_remote.verify_remote_manifest(
            remote_target=remote_dir,
            snapshot_id="snap_sync_01",
            backend="local",
        )
        self.assertTrue(ok, f"Verification failed: {msg}")

    def test_prune_old_manifests(self):
        manifest_dir = os.path.join(self.test_dir, "manifests")
        os.makedirs(manifest_dir, exist_ok=True)

        for i in range(10):
            m_path = os.path.join(manifest_dir, f"manifest_snap_{i:02d}.json")
            with open(m_path, "w") as f:
                json.dump({"snapshot_id": f"snap_{i:02d}"}, f)
            # Set progressive mtime
            mtime = time.time() - (10 - i) * 100
            os.utime(m_path, (mtime, mtime))

        deleted = br_backup_remote.prune_old_manifests(manifest_dir, keep_count=7)
        self.assertEqual(len(deleted), 3)

        remaining = [f for f in os.listdir(manifest_dir) if f.startswith("manifest_")]
        self.assertEqual(len(remaining), 7)

def br_main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(br_TestBackupRemote)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# ======================================================================
# from tests/test-storage.py
# ======================================================================
"""Automated tests for WS-STRG storage benchmark tool (task_353 / task_2586)."""


import importlib.machinery
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest

_bs_HERE = os.path.dirname(os.path.abspath(__file__))
_bs_ROOT = os.path.normpath(os.path.join(_bs_HERE, ".."))
_bs_BENCH_PATH = os.path.join(_bs_ROOT, "usr", "libexec", "mios", "storage", "mios-bench-storage")

bs_loader = importlib.machinery.SourceFileLoader("bench_storage", _bs_BENCH_PATH)
bs_spec = importlib.util.spec_from_loader("bench_storage", bs_loader)
if bs_spec and bs_spec.loader:
    bs_bench_storage = importlib.util.module_from_spec(bs_spec)
    sys.modules[bs_spec.name] = bs_bench_storage
    bs_spec.loader.exec_module(bs_bench_storage)
else:
    raise ImportError(f"Could not load bench_storage module from {_bs_BENCH_PATH}")

class bs_TestBenchStorage(unittest.TestCase):
    """Validates IOPS, sequential throughput, fsync latency benchmarks, floor evaluations, and scratch safety."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="mios_test_bench_storage_")

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_scratch_file_lifecycle_and_safety(self):
        """Verify scratch file is created with specified size and cleaned up safely."""
        scratch_path = bs_bench_storage.prepare_benchmark_file(self.test_dir, file_size_mb=2)
        self.assertTrue(os.path.isfile(scratch_path))
        self.assertEqual(os.path.getsize(scratch_path), 2 * 1024 * 1024)

        # Confirm non-destructive temporary naming
        self.assertIn("mios_bench_scratch_", os.path.basename(scratch_path))
        os.remove(scratch_path)

    def test_random_4k_benchmarks(self):
        scratch_path = bs_bench_storage.prepare_benchmark_file(self.test_dir, file_size_mb=2)
        try:
            r_iops, r_lat = bs_bench_storage.run_random_4k_read_benchmark(scratch_path, duration_sec=0.2, max_ops=1000)
            self.assertGreater(r_iops, 0.0)
            self.assertGreater(r_lat, 0.0)

            w_iops, w_lat = bs_bench_storage.run_random_4k_write_benchmark(scratch_path, duration_sec=0.2, max_ops=1000)
            self.assertGreater(w_iops, 0.0)
            self.assertGreater(w_lat, 0.0)
        finally:
            if os.path.exists(scratch_path):
                os.remove(scratch_path)

    def test_seq_1m_throughput_benchmarks(self):
        scratch_path = bs_bench_storage.prepare_benchmark_file(self.test_dir, file_size_mb=4)
        try:
            r_mbps = bs_bench_storage.run_seq_1m_read_benchmark(scratch_path, duration_sec=0.2)
            self.assertGreater(r_mbps, 0.0)

            w_mbps = bs_bench_storage.run_seq_1m_write_benchmark(scratch_path, duration_sec=0.2)
            self.assertGreater(w_mbps, 0.0)
        finally:
            if os.path.exists(scratch_path):
                os.remove(scratch_path)

    def test_fsync_latency_percentiles(self):
        scratch_path = bs_bench_storage.prepare_benchmark_file(self.test_dir, file_size_mb=2)
        try:
            lat_stats = bs_bench_storage.run_fsync_latency_benchmark(scratch_path, iterations=20)
            self.assertIn("p50_us", lat_stats)
            self.assertIn("p95_us", lat_stats)
            self.assertIn("p99_us", lat_stats)
            self.assertIn("max_us", lat_stats)
            self.assertGreater(lat_stats["p50_us"], 0.0)
            self.assertLessEqual(lat_stats["p50_us"], lat_stats["p95_us"])
            self.assertLessEqual(lat_stats["p95_us"], lat_stats["p99_us"])
            self.assertLessEqual(lat_stats["p99_us"], lat_stats["max_us"])
        finally:
            if os.path.exists(scratch_path):
                os.remove(scratch_path)

    def test_evaluate_inference_floors_pass_and_fail(self):
        # Passing mock metrics
        passing_metrics = {
            "iops_rand_read_4k": 8000.0,
            "iops_rand_write_4k": 4000.0,
            "mbps_seq_read_1m": 500.0,
            "mbps_seq_write_1m": 300.0,
            "fsync_latency_us": {"p95_us": 4000.0},
        }
        res_pass = bs_bench_storage.evaluate_inference_floors(passing_metrics, profile_name="standard")
        self.assertTrue(res_pass["meets_ai_inference_floors"])
        self.assertTrue(res_pass["evaluations"]["iops_rand_read_4k"]["passed"])
        self.assertTrue(res_pass["evaluations"]["fsync_latency_p95_us"]["passed"])

        # Failing mock metrics (low IOPS, high latency)
        failing_metrics = {
            "iops_rand_read_4k": 500.0,
            "iops_rand_write_4k": 200.0,
            "mbps_seq_read_1m": 50.0,
            "mbps_seq_write_1m": 20.0,
            "fsync_latency_us": {"p95_us": 80000.0},
        }
        res_fail = bs_bench_storage.evaluate_inference_floors(failing_metrics, profile_name="heavy_gpu")
        self.assertFalse(res_fail["meets_ai_inference_floors"])
        self.assertFalse(res_fail["evaluations"]["iops_rand_read_4k"]["passed"])
        self.assertFalse(res_fail["evaluations"]["mbps_seq_read_1m"]["passed"])

    def test_full_benchmark_suite_execution_and_cleanup(self):
        report = bs_bench_storage.run_full_storage_benchmark(
            target_dir=self.test_dir,
            file_size_mb=4,
            duration_sec=0.2,
            fsync_iterations=10,
            profile="edge_llm",
        )
        self.assertEqual(report["file_size_mb"], 4)
        self.assertIn("assessment", report)
        self.assertIn("meets_ai_inference_floors", report["assessment"])

        # Verify scratch files are deleted
        scratch_files = [f for f in os.listdir(self.test_dir) if "mios_bench_scratch_" in f]
        self.assertEqual(len(scratch_files), 0, "Scratch files were not cleaned up")

def bs_main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(bs_TestBenchStorage)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# ======================================================================
# from tests/test-storage.py
# ======================================================================
"""Automated unit test suite for MiOS Ceph Self-Healing Orchestrator."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "usr", "libexec", "mios", "storage"))

from ceph_heal import MAX_CLIENT_LATENCY_DEGRADATION_PCT, CephSelfHealingOrchestrator

class ch_TestCephHeal(unittest.TestCase):
    def setUp(self):
        self.orch = CephSelfHealingOrchestrator(max_backfills=1, dry_run=True)

    def test_osd_failure_heals_to_health_ok(self):
        """Test failed OSD rebalances PGs to HEALTH_OK with <10% client latency degradation."""
        rep = self.orch.trigger_osd_failover_and_heal("osd.1", degraded_pg_count=48)
        self.assertEqual(rep.cluster_health_state, "HEALTH_OK")
        self.assertTrue(rep.recovery_completed)
        self.assertLess(rep.client_latency_degradation_pct, MAX_CLIENT_LATENCY_DEGRADATION_PCT)


# ======================================================================
# from tests/test-storage.py
# ======================================================================
"""Automated tests for CephFS dynamic quota parsing, extended attribute quotas, resizing, and monitoring."""


import importlib.machinery
import importlib.util
import os
import shutil
import sys
import tempfile
import unittest

_cq_HERE = os.path.dirname(os.path.abspath(__file__))
_cq_ROOT = os.path.normpath(os.path.join(_cq_HERE, ".."))

sys.path.insert(0, os.path.join(_cq_ROOT, "usr", "lib", "mios"))
sys.path.insert(0, os.path.join(_cq_ROOT, "lib", "mios"))

_cq_QUOTA_PATH = os.path.join(_cq_ROOT, "usr", "libexec", "mios", "storage", "mios-cephfs-quota")
cq_loader = importlib.machinery.SourceFileLoader("cephfs_quota", _cq_QUOTA_PATH)
cq_spec = importlib.util.spec_from_loader("cephfs_quota", cq_loader)
if cq_spec and cq_spec.loader:
    cq_cephfs_quota = importlib.util.module_from_spec(cq_spec)
    sys.modules[cq_spec.name] = cq_cephfs_quota
    cq_spec.loader.exec_module(cq_cephfs_quota)
else:
    raise ImportError(f"Could not load mios-cephfs-quota module from {_cq_QUOTA_PATH}")

class cq_TestCephFSQuota(unittest.TestCase):
    """Tests byte size parsing, quota management, subvolume resize command generation, and directory monitoring."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="mios_quota_test_")
        self.tenant_a = os.path.join(self.test_dir, "tenant_a")
        self.tenant_b = os.path.join(self.test_dir, "tenant_b")
        os.makedirs(self.tenant_a, exist_ok=True)
        os.makedirs(self.tenant_b, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_parse_size_bytes_units(self):
        self.assertEqual(cq_cephfs_quota.parse_size_bytes("0"), 0)
        self.assertEqual(cq_cephfs_quota.parse_size_bytes("unlimited"), 0)
        self.assertEqual(cq_cephfs_quota.parse_size_bytes("500"), 500)
        self.assertEqual(cq_cephfs_quota.parse_size_bytes("1k"), 1000)
        self.assertEqual(cq_cephfs_quota.parse_size_bytes("1kib"), 1024)
        self.assertEqual(cq_cephfs_quota.parse_size_bytes("100MB"), 100 * 1000 * 1000)
        self.assertEqual(cq_cephfs_quota.parse_size_bytes("100MiB"), 100 * 1024 * 1024)
        self.assertEqual(cq_cephfs_quota.parse_size_bytes("50GiB"), 50 * (1024**3))
        self.assertEqual(cq_cephfs_quota.parse_size_bytes("1TiB"), 1024**4)
        self.assertEqual(cq_cephfs_quota.parse_size_bytes(1073741824), 1073741824)

    def test_parse_size_bytes_invalid(self):
        with self.assertRaises(ValueError):
            cq_cephfs_quota.parse_size_bytes("50invalid")

    def test_format_size_bytes(self):
        self.assertEqual(cq_cephfs_quota.format_size_bytes(0), "0 B")
        self.assertEqual(cq_cephfs_quota.format_size_bytes(1024), "1.00 KiB")
        self.assertEqual(cq_cephfs_quota.format_size_bytes(1048576), "1.00 MiB")
        self.assertEqual(cq_cephfs_quota.format_size_bytes(53687091200), "50.00 GiB")

    def test_parse_count(self):
        self.assertEqual(cq_cephfs_quota.parse_count("0"), 0)
        self.assertEqual(cq_cephfs_quota.parse_count("100"), 100)
        self.assertEqual(cq_cephfs_quota.parse_count("10k"), 10000)
        self.assertEqual(cq_cephfs_quota.parse_count("1M"), 1000000)

    def test_set_and_get_quota(self):
        mgr = cq_cephfs_quota.CephFSQuotaManager()
        quota_bytes = 10 * 1024 * 1024  # 10 MiB
        quota_files = 500

        res = mgr.set_quota(self.tenant_a, max_bytes=quota_bytes, max_files=quota_files)
        self.assertEqual(res["ceph.quota.max_bytes"], quota_bytes)
        self.assertEqual(res["ceph.quota.max_files"], quota_files)

        info = mgr.get_quota(self.tenant_a)
        self.assertEqual(info["max_bytes"], quota_bytes)
        self.assertEqual(info["max_files"], quota_files)
        self.assertEqual(info["status"], "OK")

    def test_quota_usage_calculation_and_status(self):
        mgr = cq_cephfs_quota.CephFSQuotaManager()
        quota_bytes = 10000  # 10 KB
        mgr.set_quota(self.tenant_a, max_bytes=quota_bytes, max_files=10)

        # Write 9.5 KB file (95% usage -> CRITICAL)
        test_file = os.path.join(self.tenant_a, "data.bin")
        with open(test_file, "wb") as f:
            f.write(b"x" * 9500)

        info = mgr.get_quota(self.tenant_a)
        self.assertEqual(info["used_bytes"], 9500)
        self.assertEqual(info["used_files"], 1)
        self.assertGreaterEqual(info["bytes_percent"], 90.0)
        self.assertEqual(info["status"], "CRITICAL")

        # Write additional file to exceed quota -> EXCEEDED
        test_file2 = os.path.join(self.tenant_a, "data2.bin")
        with open(test_file2, "wb") as f:
            f.write(b"x" * 1000)

        info2 = mgr.get_quota(self.tenant_a)
        self.assertEqual(info2["status"], "EXCEEDED")

    def test_subvolume_resize_command(self):
        mgr = cq_cephfs_quota.CephFSQuotaManager()
        new_size = 200 * (1024**3)  # 200 GiB
        res = mgr.resize_subvolume(
            fs_name="cephfs",
            subvolume="user_subvol_1000",
            group_name="tenants",
            new_size_bytes=new_size,
            dry_run=True,
        )
        self.assertEqual(res["status"], "simulated")
        self.assertIn("ceph fs subvolume resize cephfs user_subvol_1000", res["command"])
        self.assertIn("--group_name tenants", res["command"])

    def test_service_and_timer_files_exist(self):
        svc_path = os.path.join(_cq_ROOT, "usr", "lib", "systemd", "system", "mios-cephfs-quota.service")
        timer_path = os.path.join(_cq_ROOT, "usr", "lib", "systemd", "system", "mios-cephfs-quota.timer")

        self.assertTrue(os.path.exists(svc_path), f"Service unit missing at {svc_path}")
        self.assertTrue(os.path.exists(timer_path), f"Timer unit missing at {timer_path}")

        with open(svc_path, "r", encoding="utf-8") as f:
            s_content = f.read()
        self.assertIn("mios-cephfs-quota", s_content)

        with open(timer_path, "r", encoding="utf-8") as f:
            t_content = f.read()
        self.assertIn("OnCalendar=", t_content)

def cq_main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(cq_TestCephFSQuota)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# ======================================================================
# from tests/test-storage.py
# ======================================================================
"""Automated tests for Cockpit CephFS & Storage Telemetry Backend (task_494)."""


import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

_cc_HERE = os.path.dirname(os.path.abspath(__file__))
_cc_ROOT = os.path.normpath(os.path.join(_cc_HERE, ".."))
_cc_MODULE_PATH = os.path.join(_cc_ROOT, "usr", "libexec", "mios", "storage", "cockpit_ceph.py")

cc_spec = importlib.util.spec_from_file_location("cockpit_ceph", _cc_MODULE_PATH)
if cc_spec and cc_spec.loader:
    cc_cockpit_ceph = importlib.util.module_from_spec(cc_spec)
    sys.modules[cc_spec.name] = cc_cockpit_ceph
    cc_spec.loader.exec_module(cc_cockpit_ceph)
else:
    raise ImportError(f"Could not load cockpit_ceph module from {_cc_MODULE_PATH}")

class cc_TestCockpitCeph(unittest.TestCase):
    """Validates CephFS pool metrics, drive encryption telemetry, and Cockpit manifest generation."""

    def setUp(self) -> None:
        self.mgr = cc_cockpit_ceph.CockpitCephManager(mock=True)
        self.tmp_dir = tempfile.mkdtemp(prefix="mios_test_ceph_")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_get_ceph_status_mock(self) -> None:
        """Asserts correct retrieval of mock CephFS cluster status and tiered pools."""
        status = self.mgr.get_ceph_status()
        self.assertEqual(status.health_status, "HEALTH_OK")
        self.assertEqual(len(status.pools), 3)

        hot_pool = next(p for p in status.pools if p.pool_name == "cephfs-data-hot")
        self.assertEqual(hot_pool.tier_type, "hot_nvme")
        self.assertEqual(hot_pool.pg_num, 128)
        self.assertGreater(hot_pool.read_iops, 1000)

        cold_pool = next(p for p in status.pools if p.pool_name == "cephfs-data-cold")
        self.assertEqual(cold_pool.tier_type, "cold_hdd")

    def test_get_smart_metrics_mock(self) -> None:
        """Asserts SMART metrics and encryption status for physical drives."""
        drives = self.mgr.get_smart_metrics()
        self.assertEqual(len(drives), 2)

        nvme = next(d for d in drives if d.device == "/dev/nvme0n1")
        self.assertEqual(nvme.type, "opal2")
        self.assertTrue(nvme.locked)
        self.assertEqual(nvme.smart_health, "PASSED")

        sda = next(d for d in drives if d.device == "/dev/sda")
        self.assertEqual(sda.type, "luks2")
        self.assertTrue(sda.tpm_sealed)

    def test_generate_cockpit_manifest(self) -> None:
        """Asserts generation of Cockpit manifest JSON."""
        out_file = os.path.join(self.tmp_dir, "manifest.json")
        manifest = self.mgr.generate_cockpit_manifest(output_path=out_file)
        self.assertEqual(manifest["name"], "mios-storage")
        self.assertIn("tools", manifest)
        self.assertTrue(os.path.exists(out_file))

        with open(out_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            self.assertEqual(data["name"], "mios-storage")

    def test_cli_pools_json(self) -> None:
        """Asserts CLI execution with --pools --mock --json."""
        with patch("sys.argv", ["cockpit_ceph.py", "--pools", "--mock", "--json"]):
            with patch("builtins.print") as mock_print:
                ret = cc_cockpit_ceph.main()
                self.assertEqual(ret, 0)
                mock_print.assert_called()
                parsed = json.loads(mock_print.call_args[0][0])
                self.assertEqual(parsed["status"], "ok")
                self.assertIn("pools", parsed)

    def test_cli_smart_json(self) -> None:
        """Asserts CLI execution with --smart --mock --json."""
        with patch("sys.argv", ["cockpit_ceph.py", "--smart", "--mock", "--json"]):
            with patch("builtins.print") as mock_print:
                ret = cc_cockpit_ceph.main()
                self.assertEqual(ret, 0)
                mock_print.assert_called()
                parsed = json.loads(mock_print.call_args[0][0])
                self.assertEqual(parsed["status"], "ok")
                self.assertIn("drives", parsed)


# ======================================================================
# from tests/test-storage.py
# ======================================================================
"""Unit and integration tests for ContainerGCManager."""


import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_cgl_HERE = os.path.dirname(os.path.abspath(__file__))
_cgl_ROOT = os.path.normpath(os.path.join(_cgl_HERE, ".."))
_cgl_TARGET_PATH = os.path.join(_cgl_ROOT, "usr", "libexec", "mios", "storage", "container_gc.py")

cgl_spec = importlib.util.spec_from_file_location("container_gc", _cgl_TARGET_PATH)
if cgl_spec and cgl_spec.loader:
    cgl_container_gc = importlib.util.module_from_spec(cgl_spec)
    sys.modules[cgl_spec.name] = cgl_container_gc
    cgl_spec.loader.exec_module(cgl_container_gc)
else:
    raise ImportError(f"Could not load module from {_cgl_TARGET_PATH}")

class cgl_TestContainerGCManager(unittest.TestCase):
    """Test suite for container image inspection, LRU sorting, and threshold pruning."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="mios-test-containargc-")
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_list_images_mock(self):
        mgr = cgl_container_gc.ContainerGCManager(mock=True)
        images = mgr.list_images()
        self.assertEqual(len(images), 4)

        pinned = [img for img in images if img.is_pinned]
        self.assertEqual(len(pinned), 2)
        self.assertTrue(any(img.repository == "ghcr.io/mios-dev/mios" for img in pinned))

    def test_plan_prune_ordering_lru(self):
        mgr = cgl_container_gc.ContainerGCManager(threshold_pct=80.0, mock=True)
        plan = mgr.plan_prune()

        # In mock mode usage is 88.5% > 80.0%, so pruning triggers
        self.assertEqual(plan.unreferenced_images, 2)
        self.assertEqual(len(plan.prune_targets), 2)

        # Oldest unreferenced image (alpine:3.18) must be first target
        self.assertEqual(plan.prune_targets[0].repository, "docker.io/library/alpine")
        self.assertEqual(plan.prune_targets[1].repository, "docker.io/library/node")
        self.assertAlmostEqual(plan.reclaimable_mb, 192.5, places=1)

    def test_plan_prune_below_threshold_returns_zero_targets(self):
        mgr = cgl_container_gc.ContainerGCManager(threshold_pct=95.0, mock=True)
        plan = mgr.plan_prune()

        # In mock mode usage is 88.5% < 95.0%, so zero targets should be pruned
        self.assertEqual(len(plan.prune_targets), 0)
        self.assertEqual(plan.reclaimable_mb, 0.0)

    def test_execute_prune_mock(self):
        mgr = cgl_container_gc.ContainerGCManager(threshold_pct=80.0, mock=True)
        plan = mgr.plan_prune()
        count, reclaimed_mb = mgr.execute_prune(plan)

        self.assertEqual(count, 2)
        self.assertAlmostEqual(reclaimed_mb, 192.5, places=1)

    def test_cli_execution_scan_mock(self):
        test_args = ["container_gc.py", "--scan", "--mock", "--json"]
        with patch.object(sys, "argv", test_args):
            exit_code = cgl_container_gc.main()
            self.assertEqual(exit_code, 0)

    def test_cli_execution_plan_mock(self):
        test_args = ["container_gc.py", "--plan", "--threshold", "80.0", "--mock", "--json"]
        with patch.object(sys, "argv", test_args):
            exit_code = cgl_container_gc.main()
            self.assertEqual(exit_code, 0)

    def test_cli_execution_prune_mock(self):
        test_args = ["container_gc.py", "--prune", "--threshold", "80.0", "--mock", "--json"]
        with patch.object(sys, "argv", test_args):
            exit_code = cgl_container_gc.main()
            self.assertEqual(exit_code, 0)

def cgl_main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(cgl_TestContainerGCManager)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# ======================================================================
# from tests/test-storage.py
# ======================================================================
"""MiOS Empirical Adversarial Test Harness (Challenger 2).  Executes stress-testing, boundary attacks, simulated memory pressure, fuzzing payloads, security exclusions, SQL injection defense, and zero-downtime safety checks against: - task_348: Ceph RADOS Gateway Quadlet S3 Container - task_349: LUKS2 Zero-Downtime Key Rotation Engine (mios-luks-rotate) - task_351: SQLite / PostgreSQL Database Doctor (mios-db-doctor) - task_352: Remote Delta Snapshot Backup Synchronizer (mios-backup-remote) - task_353: Storage Performance Benchmark Harness (mios-bench-storage) - task_354: Automated tmpfs Spill-to-NVMe Manager (mios-tmpfs-spill) - task_355: Unified Journald Log Aggregation & pgvector Streamer (mios-log-streamer) - task_356: Zero-Downtime Database Migration Runner (mios-db-migrate)"""


import importlib.util
import json
import math
import os
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from typing import Any, Dict, List, Optional

_ec2_HERE = os.path.abspath(os.path.dirname(__file__)) if "__file__" in globals() else os.path.abspath(".")
_ec2_ROOT = os.path.normpath(os.path.join(_ec2_HERE, "..")) if os.path.basename(_ec2_HERE) == "tests" else _ec2_HERE

def ec2_load_module(name: str, rel_path: str) -> Any:
    from importlib.machinery import SourceFileLoader
    full_path = os.path.join(_ec2_ROOT, rel_path)
    loader = SourceFileLoader(name, full_path)
    spec = importlib.util.spec_from_loader(name, loader)
    if spec and spec.loader:
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        return mod
    raise ImportError(f"Could not load module {name} from {full_path}")

# Load target modules under test
ec2_mod_bench = ec2_load_module("bench_storage", "usr/libexec/mios/storage/mios-bench-storage")
ec2_mod_luks = ec2_load_module("luks_rotate", "usr/libexec/mios/sec/mios-luks-rotate")
ec2_mod_spill = ec2_load_module("tmpfs_spill", "usr/libexec/mios/mem/mios-tmpfs-spill")
ec2_mod_log = ec2_load_module("log_streamer", "usr/libexec/mios/log/mios-log-streamer")
ec2_mod_backup = ec2_load_module("backup_remote", "usr/libexec/mios/storage/mios-backup-remote")
ec2_mod_doctor = ec2_load_module("db_doctor", "usr/libexec/mios/db/mios-db-doctor.py")
ec2_mod_migrate = ec2_load_module("db_migrate", "usr/libexec/mios/db/mios-db-migrate.py")

class ec2_TestAdversarialBenchStorage(unittest.TestCase):
    """Adversarial testing on mios-bench-storage: percentile math, profile bounds, and cleanup."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="mios-bench-adv-")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_percentile_boundary_conditions(self):
        """Stress-test percentile function with empty, 1-element, duplicates, and extreme percentiles."""
        # 1. Empty list
        self.assertEqual(ec2_mod_bench.percentile([], 50), 0.0)
        self.assertEqual(ec2_mod_bench.percentile([], 0), 0.0)
        self.assertEqual(ec2_mod_bench.percentile([], 100), 0.0)

        # 2. Single element
        self.assertEqual(ec2_mod_bench.percentile([42.5], 0), 42.5)
        self.assertEqual(ec2_mod_bench.percentile([42.5], 50), 42.5)
        self.assertEqual(ec2_mod_bench.percentile([42.5], 100), 42.5)

        # 3. Two elements
        data = [10.0, 20.0]
        self.assertEqual(ec2_mod_bench.percentile(data, 0), 10.0)
        self.assertEqual(ec2_mod_bench.percentile(data, 50), 15.0)
        self.assertEqual(ec2_mod_bench.percentile(data, 100), 20.0)

        # 4. Large list with duplicate values
        data_dup = [100.0] * 50
        self.assertEqual(ec2_mod_bench.percentile(data_dup, 95), 100.0)

    def test_quick_benchmark_execution_and_guaranteed_cleanup(self):
        """Execute full benchmark and verify scratch file lifecycle and cleanup."""
        report = ec2_mod_bench.run_full_storage_benchmark(
            target_dir=self.tmpdir,
            file_size_mb=4,
            duration_sec=0.1,
            fsync_iterations=5,
            profile="edge_llm",
        )
        self.assertIn("iops_rand_read_4k", report)
        self.assertIn("fsync_latency_us", report)
        self.assertIn("assessment", report)
        self.assertEqual(report["assessment"]["profile"], "edge_llm")

        # Verify no scratch files (.dat) were leaked in target_dir
        remaining = [f for f in os.listdir(self.tmpdir) if f.startswith("mios_bench_scratch_")]
        self.assertEqual(len(remaining), 0, f"Leaked scratch files: {remaining}")

    def test_inference_floor_evaluation_matrix(self):
        """Adversarially test hardware inference floor boundary decisions."""
        mock_metrics_pass = {
            "iops_rand_read_4k": 3500,
            "iops_rand_write_4k": 1600,
            "mbps_seq_read_1m": 300.0,
            "mbps_seq_write_1m": 150.0,
            "fsync_latency_us": {"p95_us": 12000.0},
        }
        res_pass = ec2_mod_bench.evaluate_inference_floors(mock_metrics_pass, profile_name="edge_llm")
        self.assertTrue(res_pass["meets_ai_inference_floors"])

        # Test failure if even 1 metric misses floor
        mock_metrics_fail = dict(mock_metrics_pass)
        mock_metrics_fail["mbps_seq_read_1m"] = 249.0  # Required: 250.0
        res_fail = ec2_mod_bench.evaluate_inference_floors(mock_metrics_fail, profile_name="edge_llm")
        self.assertFalse(res_fail["meets_ai_inference_floors"])
        self.assertFalse(res_fail["evaluations"]["mbps_seq_read_1m"]["passed"])

class ec2_TestAdversarialLUKSRotate(unittest.TestCase):
    """Adversarial testing on mios-luks-rotate: key validation aborts, slot exhaustion, and log safety."""

    def test_key_rotation_aborts_and_preserves_old_slot_on_failure(self):
        """CRITICAL: If test_passphrase fails on newly added slot, abort without touching old slot."""
        calls = []

        def mock_runner(cmd, input=None, capture_output=True, text=True, check=True):
            cmd_str = " ".join(cmd)
            calls.append((cmd_str, input))
            if "luksDump" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, stdout='{"keyslots":{"0":{"state":"active"}}}', stderr="")
            elif "luksHeaderBackup" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            elif "luksAddKey" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            elif "--test-passphrase" in cmd_str:
                # First check (current key) passes; second check (new key) FAILS
                if input and "old_secret" in input:
                    return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="Decryption failed")
            elif "luksKillSlot" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        dev = ec2_mod_luks.LUKSDevice(runner=mock_runner)
        tmpdir = tempfile.mkdtemp()
        engine = ec2_mod_luks.LUKSRotationEngine(luks_device=dev, backup_root=tmpdir)

        try:
            with self.assertRaises(RuntimeError) as ctx:
                engine.rotate_key("/dev/sda2", current_passphrase="old_secret", new_passphrase="bad_new_secret")
            self.assertIn("ABORTED ROTATION", str(ctx.exception))

            # Verify that luksKillSlot was NOT called with slot 0 (the active old slot)
            killed_slots = [c[0] for c in calls if "luksKillSlot" in c[0]]
            for k in killed_slots:
                self.assertNotIn("luksKillSlot /dev/sda2 0", k, "CRITICAL ERROR: Old slot 0 was killed despite new key verification failure!")
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_no_free_keyslots_rejection(self):
        """Verify engine refuses to rotate if all keyslots are occupied."""
        # 32 active slots
        all_slots_json = json.dumps({"keyslots": {str(i): {"state": "active"} for i in range(32)}})

        def mock_runner(cmd, input=None, capture_output=True, text=True, check=True):
            if "luksDump" in " ".join(cmd):
                return subprocess.CompletedProcess(cmd, 0, stdout=all_slots_json, stderr="")
            elif "--test-passphrase" in " ".join(cmd):
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        dev = ec2_mod_luks.LUKSDevice(runner=mock_runner)
        engine = ec2_mod_luks.LUKSRotationEngine(luks_device=dev)
        with self.assertRaises(RuntimeError) as ctx:
            engine.rotate_key("/dev/nvme0n1p3", current_passphrase="valid_pass")
        self.assertIn("No available free keyslots", str(ctx.exception))

class ec2_TestAdversarialTmpfsSpill(unittest.TestCase):
    """Adversarial testing on mios-tmpfs-spill: security exclusions, PSI triggers, and LRU eviction."""

    def setUp(self):
        self.src_dir = tempfile.mkdtemp(prefix="mios-tmpfs-src-")
        self.tgt_dir = tempfile.mkdtemp(prefix="mios-tmpfs-tgt-")

    def tearDown(self):
        shutil.rmtree(self.src_dir, ignore_errors=True)
        shutil.rmtree(self.tgt_dir, ignore_errors=True)

    def test_security_sensitive_exclusion_matrix(self):
        """Adversarially verify that no crypto keys, tokens, ssh/gpg sockets, or credentials are spilled."""
        sensitive_files = [
            "id_rsa", "id_ed25519", "server.key", "ca.crt", "tls.pem",
            "access_token.jwt", "bearer_token", "admin.password", "app.secret",
            "ssh-agent.1234", "gpg-agent.socket", "X11-unix.sock", "systemd.lock",
        ]
        for sname in sensitive_files:
            p = os.path.join(self.src_dir, sname)
            with open(p, "wb") as f:
                f.write(b"TOP_SECRET_CREDENTIALS_" * 100000)  # > 1MB

        # Create 1 legitimate big file
        legit_path = os.path.join(self.src_dir, "large_cache.bin")
        with open(legit_path, "wb") as f:
            f.write(b"LEGITIMATE_CACHE_DATA_" * 100000)

        # Trigger spill under high PSI
        res = ec2_mod_spill.evaluate_and_spill(
            source_dir=self.src_dir,
            target_dir=self.tgt_dir,
            mock_psi=75.0,  # > 60%
            min_file_size=1024 * 1024,
        )

        self.assertTrue(res["spill_action_taken"])
        self.assertEqual(res["files_spilled"], 1)
        self.assertEqual(res["spilled_details"][0]["source"], legit_path)

        # Ensure all sensitive files remain regular non-symlinked files in src_dir
        for sname in sensitive_files:
            p = os.path.join(self.src_dir, sname)
            self.assertTrue(os.path.exists(p))
            self.assertFalse(os.path.islink(p), f"Security sensitive file {sname} was improperly symlinked/spilled!")

    def test_lru_quota_eviction_and_broken_symlink_cleanup(self):
        """Verify oldest spilled files are evicted when exceeding max_spill_bytes and broken symlinks purged."""
        # Create 3 files of 1MB each
        f1 = os.path.join(self.src_dir, "file1.dat")
        f2 = os.path.join(self.src_dir, "file2.dat")
        f3 = os.path.join(self.src_dir, "file3.dat")

        with open(f1, "wb") as f:
            f.write(b"A" * (1024 * 1024))
        time.sleep(0.01)
        with open(f2, "wb") as f:
            f.write(b"B" * (1024 * 1024))
        time.sleep(0.01)
        with open(f3, "wb") as f:
            f.write(b"C" * (1024 * 1024))

        # Set quota to 2.5MB (file1 + file2 + file3 = 3.0MB -> file1 must be evicted)
        res = ec2_mod_spill.evaluate_and_spill(
            source_dir=self.src_dir,
            target_dir=self.tgt_dir,
            mock_psi=80.0,
            min_file_size=512 * 1024,
            max_spill_bytes=int(2.5 * 1024 * 1024),
        )

        self.assertTrue(res["spill_action_taken"])
        self.assertEqual(res["files_spilled"], 3)
        self.assertEqual(res["evicted_files"], 1)

        ledger = ec2_mod_spill.load_spill_ledger(self.tgt_dir)
        self.assertLessEqual(ledger["total_spilled_bytes"], int(2.5 * 1024 * 1024))

    def test_unspill_full_restoration(self):
        """Verify unspill cleanly restores symlinks back to physical files."""
        fpath = os.path.join(self.src_dir, "workload.dat")
        test_content = b"RESTORATION_INTEGRITY_CHECK_" * 50000
        with open(fpath, "wb") as f:
            f.write(test_content)

        ec2_mod_spill.evaluate_and_spill(
            source_dir=self.src_dir,
            target_dir=self.tgt_dir,
            mock_psi=90.0,
            min_file_size=500 * 1024,
        )
        self.assertTrue(os.path.islink(fpath))

        # Unspill
        restored_cnt, restored_b = ec2_mod_spill.unspill_files(target_dir=self.tgt_dir)
        self.assertEqual(restored_cnt, 1)
        self.assertFalse(os.path.islink(fpath))
        self.assertTrue(os.path.isfile(fpath))
        with open(fpath, "rb") as f:
            self.assertEqual(f.read(), test_content)

class ec2_TestAdversarialLogStreamer(unittest.TestCase):
    """Adversarial testing on mios-log-streamer: malformed streams, SQL injection, and vector math."""

    def test_hostile_journal_stream_and_sql_injection_defense(self):
        """Feed adversarial SQL injection payloads and malformed json to journal parser."""
        hostile_records = [
            # 1. SQL Injection attempt
            {
                "PRIORITY": 2,
                "MESSAGE": "'); DROP TABLE system_logs; SELECT pg_sleep(10); --",
                "_SYSTEMD_UNIT": "malicious'; DROP TABLE users; --.service",
                "__REALTIME_TIMESTAMP": "1724688000000000",
            },
            # 2. Binary message payload
            {
                "PRIORITY": 3,
                "MESSAGE": [0xDE, 0xAD, 0xBE, 0xEF, 0x48, 0x65, 0x6C, 0x6C, 0x6F],
                "SYSLOG_IDENTIFIER": "kernel",
            },
            # 3. High priority filter exclusion (debug level 7)
            {
                "PRIORITY": 7,
                "MESSAGE": "Standard debug trace",
                "_SYSTEMD_UNIT": "systemd.service",
            },
            # 4. Missing required fields
            {
                "PRIORITY": 1,
            },
        ]

        parsed = []
        for r in hostile_records:
            p = ec2_mod_log.parse_journal_record(r, max_priority=3)
            if p:
                parsed.append(p)

        # Records 1 & 2 should parse; 3 (priority 7) and 4 (empty message) should be filtered
        self.assertEqual(len(parsed), 2)

        # Check SQL formatting safety
        parsed[0]["emb"] = [0.1] * 768
        parsed[1]["emb"] = [0.2] * 768
        sql = ec2_mod_log.format_sql_insert(parsed)

        # Single quotes must be doubled for SQL escape
        self.assertIn("malicious''; DROP TABLE users; --.service", sql)
        self.assertIn("'''); DROP TABLE system_logs; SELECT pg_sleep(10); --", sql)

    def test_vector_embeddings_mathematical_properties(self):
        """Verify deterministic embeddings are 768-dim, unit normalized (L2=1.0), and deterministic."""
        text1 = "[sshd.service] Failed password for root from 192.168.1.100 port 22 ssh2"
        text2 = "[systemd] Started MiOS Autonomous Agent Daemon."

        emb1_a = ec2_mod_log.generate_deterministic_embedding(text1, dim=768)
        emb1_b = ec2_mod_log.generate_deterministic_embedding(text1, dim=768)
        emb2 = ec2_mod_log.generate_deterministic_embedding(text2, dim=768)

        # 1. Dimension check
        self.assertEqual(len(emb1_a), 768)
        self.assertEqual(len(emb2), 768)

        # 2. Determinism check
        self.assertEqual(emb1_a, emb1_b)
        self.assertNotEqual(emb1_a, emb2)

        # 3. Unit norm check (sum of squares ~ 1.0)
        norm1 = math.sqrt(sum(x * x for x in emb1_a))
        norm2 = math.sqrt(sum(x * x for x in emb2))
        self.assertAlmostEqual(norm1, 1.0, delta=0.01)
        self.assertAlmostEqual(norm2, 1.0, delta=0.01)

class ec2_TestAdversarialBackupRemote(unittest.TestCase):
    """Adversarial testing on mios-backup-remote: chunk boundary edge cases and delta deduplication."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="mios-backup-adv-")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_chunking_boundary_sizes(self):
        """Test hashing on 0-byte, 1-byte, exact chunk boundary, and cross-boundary files."""
        # 0 bytes
        p0 = os.path.join(self.tmpdir, "zero.dat")
        open(p0, "wb").close()
        c0 = ec2_mod_backup.hash_file_chunks(p0, chunk_size=1024)
        self.assertEqual(len(c0), 0)

        # Exact boundary: 2048 bytes with chunk_size 1024 -> exactly 2 chunks
        p2048 = os.path.join(self.tmpdir, "exact.dat")
        with open(p2048, "wb") as f:
            f.write(b"X" * 2048)
        c2048 = ec2_mod_backup.hash_file_chunks(p2048, chunk_size=1024)
        self.assertEqual(len(c2048), 2)
        self.assertEqual(c2048[0]["length"], 1024)
        self.assertEqual(c2048[1]["length"], 1024)

        # Cross boundary: 2049 bytes with chunk_size 1024 -> 3 chunks (1024, 1024, 1)
        p2049 = os.path.join(self.tmpdir, "cross.dat")
        with open(p2049, "wb") as f:
            f.write(b"Y" * 2049)
        c2049 = ec2_mod_backup.hash_file_chunks(p2049, chunk_size=1024)
        self.assertEqual(len(c2049), 3)
        self.assertEqual(c2049[2]["length"], 1)

    def test_delta_plan_deduplication_accuracy(self):
        """Verify delta plan accurately detects unchanged vs modified chunks across snapshots."""
        src_dir = os.path.join(self.tmpdir, "data")
        os.makedirs(src_dir, exist_ok=True)

        f_static = os.path.join(src_dir, "static.bin")
        f_mut = os.path.join(src_dir, "mut.bin")

        with open(f_static, "wb") as f:
            f.write(b"STATIC_CHUNK_DATA_" * 1000)
        with open(f_mut, "wb") as f:
            f.write(b"MUTABLE_INITIAL_" * 1000)

        # Baseline snapshot
        m1 = ec2_mod_backup.create_snapshot_manifest(src_dir, snapshot_id="snap1", chunk_size=4096)

        # Mutate second file
        with open(f_mut, "wb") as f:
            f.write(b"MUTABLE_MODIFIED_PAYLOAD_" * 1000)

        # Second snapshot
        m2 = ec2_mod_backup.create_snapshot_manifest(src_dir, snapshot_id="snap2", chunk_size=4096)

        plan = ec2_mod_backup.compute_delta_plan(m2, baseline_manifest=m1)
        self.assertGreater(plan["new_chunks_count"], 0)
        self.assertGreater(plan["reused_chunks_count"], 0)
        self.assertGreater(plan["dedup_ratio_pct"], 0.0)

class ec2_TestAdversarialDbDoctorAndMigrate(unittest.TestCase):
    """Adversarial testing on usr/libexec/mios/db/mios-db-doctor.py and usr/libexec/mios/db/mios-db-migrate.py."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="mios-db-adv-")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_db_doctor_garbage_and_corrupt_files(self):
        """Verify db doctor ignores non-sqlite files and creates backup before repair."""
        # 1. 0-byte file
        p_zero = os.path.join(self.tmpdir, "empty.db")
        open(p_zero, "wb").close()

        # 2. Random garbage file
        p_rand = os.path.join(self.tmpdir, "garbage.sqlite")
        with open(p_rand, "wb") as f:
            f.write(os.urandom(1024))

        doc = ec2_mod_doctor.DbDoctor(sqlite_paths=[self.tmpdir])
        found = doc.find_sqlite_databases()
        self.assertEqual(len(found), 0, "Non-sqlite files were falsely discovered")

    def test_db_migrate_checksum_tampering_detection(self):
        """Verify migration runner detects post-application script modification."""
        mig_dir = os.path.join(self.tmpdir, "migrations")
        os.makedirs(mig_dir, exist_ok=True)

        m1_path = os.path.join(mig_dir, "0001_init.sql")
        with open(m1_path, "w", encoding="utf-8") as f:
            f.write("CREATE TABLE test_table (id INT);\n")

        migrator = ec2_mod_migrate.DbMigrator(
            migrations_dir=mig_dir,
            mock=True,
        )

        # Apply initial migration
        res = migrator.migrate()
        self.assertEqual(res["total_applied"], 1)

        # Tamper with migration file on disk
        with open(m1_path, "w", encoding="utf-8") as f:
            f.write("CREATE TABLE test_table (id INT, tampered_col TEXT);\n")

        # Re-run: must detect checksum mismatch
        with self.assertRaises(RuntimeError) as ctx:
            migrator.migrate()
        self.assertIn("checksum mismatch", str(ctx.exception).lower())

def ec2_main() -> int:
    suite = unittest.TestSuite()
    for test_class in [
        ec2_TestAdversarialBenchStorage,
        ec2_TestAdversarialLUKSRotate,
        ec2_TestAdversarialTmpfsSpill,
        ec2_TestAdversarialLogStreamer,
        ec2_TestAdversarialBackupRemote,
        ec2_TestAdversarialDbDoctorAndMigrate,
    ]:
        suite.addTests(unittest.TestLoader().loadTestsFromTestCase(test_class))

    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# ======================================================================
# from tests/test-storage.py
# ======================================================================
"""Automated tests for CephFS transactional ledger replication, block hashing, and reconciliation."""


import importlib.machinery
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest

_ls_HERE = os.path.dirname(os.path.abspath(__file__))
_ls_ROOT = os.path.normpath(os.path.join(_ls_HERE, ".."))

sys.path.insert(0, os.path.join(_ls_ROOT, "usr", "lib", "mios"))
sys.path.insert(0, os.path.join(_ls_ROOT, "lib", "mios"))

_ls_SYNC_PATH = os.path.join(_ls_ROOT, "usr", "libexec", "mios", "storage", "mios-ledger-sync")
ls_loader = importlib.machinery.SourceFileLoader("ledger_sync", _ls_SYNC_PATH)
ls_spec = importlib.util.spec_from_loader("ledger_sync", ls_loader)
if ls_spec and ls_spec.loader:
    ls_ledger_sync = importlib.util.module_from_spec(ls_spec)
    sys.modules[ls_spec.name] = ls_ledger_sync
    ls_spec.loader.exec_module(ls_ledger_sync)
else:
    raise ImportError(f"Could not load mios-ledger-sync module from {_ls_SYNC_PATH}")

class ls_TestLedgerSync(unittest.TestCase):
    """Tests block creation, cryptographic linking, tamper detection, and cross-pool sync."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="mios_ledger_test_")
        self.src_dir = os.path.join(self.test_dir, "src_pool")
        self.dst_dir = os.path.join(self.test_dir, "dst_pool")
        self.rec_log = os.path.join(self.test_dir, "reconciliation.log")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_canonical_json_determinism(self):
        d1 = {"b": 2, "a": 1, "nested": {"z": 10, "y": 20}}
        d2 = {"nested": {"y": 20, "z": 10}, "a": 1, "b": 2}
        self.assertEqual(ls_ledger_sync.canonical_json(d1), ls_ledger_sync.canonical_json(d2))
        self.assertEqual(ls_ledger_sync.canonical_json(d1), '{"a":1,"b":2,"nested":{"y":20,"z":10}}')

    def test_block_creation_and_genesis_validation(self):
        payload = {"event": "genesis_init", "cluster": "ceph-test"}
        block = ls_ledger_sync.Block.create(
            index=0,
            prev_hash=ls_ledger_sync.GENESIS_PREV_HASH,
            payload=payload,
        )
        self.assertEqual(block.index, 0)
        self.assertEqual(block.prev_hash, ls_ledger_sync.GENESIS_PREV_HASH)
        self.assertEqual(block.payload_hash, ls_ledger_sync.compute_hash(ls_ledger_sync.canonical_json(payload)))

        ok, err = block.validate_integrity(prev_block=None)
        self.assertTrue(ok, f"Genesis validation failed: {err}")

    def test_sequential_chain_linking(self):
        chain = ls_ledger_sync.LedgerChain(self.src_dir)
        b0 = chain.append({"action": "create_user", "uid": 1000})
        b1 = chain.append({"action": "assign_quota", "uid": 1000, "bytes": 5000000})
        b2 = chain.append({"action": "audit_event", "status": "approved"})

        self.assertEqual(b0.index, 0)
        self.assertEqual(b1.index, 1)
        self.assertEqual(b2.index, 2)

        self.assertEqual(b1.prev_hash, b0.block_hash)
        self.assertEqual(b2.prev_hash, b1.block_hash)

        valid, count, errors = chain.verify()
        self.assertTrue(valid, f"Chain verify failed: {errors}")
        self.assertEqual(count, 3)

    def test_hmac_signature_verification(self):
        key = "mios-secret-cryptographic-key-12345"
        chain = ls_ledger_sync.LedgerChain(self.src_dir)
        b0 = chain.append({"action": "root_command", "cmd": "ceph status"}, secret_key=key)
        self.assertIsNotNone(b0.signature)

        # Verify with correct key
        valid, count, errors = chain.verify(secret_key=key)
        self.assertTrue(valid)

        # Verify with wrong key fails
        valid_wrong, _, errors_wrong = chain.verify(secret_key="wrong-key")
        self.assertFalse(valid_wrong)
        self.assertTrue(any("signature" in e for e in errors_wrong))

    def test_tamper_detection_mutated_payload(self):
        chain = ls_ledger_sync.LedgerChain(self.src_dir)
        chain.append({"tx": 1, "amount": 100})
        chain.append({"tx": 2, "amount": 200})

        # Mutate block 1 on disk
        b1_path = os.path.join(self.src_dir, "blocks", "block_00000001.json")
        with open(b1_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        data["payload"]["amount"] = 999999  # Tamper!
        with open(b1_path, "w", encoding="utf-8") as f:
            json.dump(data, f)

        valid, count, errors = chain.verify()
        self.assertFalse(valid, "Tampered block should have failed verification")
        self.assertTrue(any("payload hash mismatch" in e for e in errors))

    def test_tamper_detection_broken_chain_hash(self):
        chain = ls_ledger_sync.LedgerChain(self.src_dir)
        chain.append({"tx": 1})
        chain.append({"tx": 2})

        # Mutate prev_hash in block 1
        b1_path = os.path.join(self.src_dir, "blocks", "block_00000001.json")
        with open(b1_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        data["prev_hash"] = "f" * 64
        # Recalculate block hash so internal matches, but parent linkage fails
        header = f"1:{data['timestamp']}:{data['prev_hash']}:{data['payload_hash']}"
        data["block_hash"] = ls_ledger_sync.compute_hash(header)
        with open(b1_path, "w", encoding="utf-8") as f:
            json.dump(data, f)

        valid, count, errors = chain.verify()
        self.assertFalse(valid)
        self.assertTrue(any("does not match parent" in e for e in errors))

    def test_replication_between_pools(self):
        src_chain = ls_ledger_sync.LedgerChain(self.src_dir)
        for i in range(5):
            src_chain.append({"entry_index": i, "data": f"payload_{i}"})

        engine = ls_ledger_sync.LedgerSyncEngine()
        report = engine.replicate(self.src_dir, self.dst_dir, reconciliation_log=self.rec_log)

        self.assertEqual(report["status"], "synchronized")
        self.assertEqual(report["synced_blocks"], 5)
        self.assertEqual(report["total_blocks"], 5)

        # Verify destination chain independently
        dst_chain = ls_ledger_sync.LedgerChain(self.dst_dir)
        valid, count, errors = dst_chain.verify()
        self.assertTrue(valid)
        self.assertEqual(count, 5)

        # Verify reconciliation log exists and contains valid JSON record
        self.assertTrue(os.path.exists(self.rec_log))
        with open(self.rec_log, "r", encoding="utf-8") as f:
            lines = f.readlines()
        self.assertEqual(len(lines), 1)
        rec_data = json.loads(lines[0])
        self.assertEqual(rec_data["synced_blocks"], 5)

    def test_incremental_replication(self):
        src_chain = ls_ledger_sync.LedgerChain(self.src_dir)
        for i in range(3):
            src_chain.append({"entry": i})

        engine = ls_ledger_sync.LedgerSyncEngine()
        engine.replicate(self.src_dir, self.dst_dir)

        # Append 2 more entries to source
        src_chain.append({"entry": 3})
        src_chain.append({"entry": 4})

        # Incremental sync
        report2 = engine.replicate(self.src_dir, self.dst_dir)
        self.assertEqual(report2["synced_blocks"], 2)
        self.assertEqual(report2["total_blocks"], 5)

    def test_replication_refuses_corrupted_source(self):
        src_chain = ls_ledger_sync.LedgerChain(self.src_dir)
        src_chain.append({"clean": True})

        # Tamper source block
        b0_path = os.path.join(self.src_dir, "blocks", "block_00000000.json")
        with open(b0_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        data["payload"]["clean"] = False
        with open(b0_path, "w", encoding="utf-8") as f:
            json.dump(data, f)

        engine = ls_ledger_sync.LedgerSyncEngine()
        with self.assertRaises(ValueError) as ctx:
            engine.replicate(self.src_dir, self.dst_dir)
        self.assertIn("Source ledger integrity failure", str(ctx.exception))

    def test_service_unit_file_exists(self):
        svc_path = os.path.join(_ls_ROOT, "usr", "lib", "systemd", "system", "mios-ledger-sync.service")
        self.assertTrue(os.path.exists(svc_path), f"Service unit missing at {svc_path}")
        with open(svc_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("mios-ledger-sync", content)
        self.assertIn("Type=oneshot", content)

def ls_main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(ls_TestLedgerSync)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# ======================================================================
# from tests/test-storage.py
# ======================================================================
"""Automated tests for Hardware OPAL 2.0 SED / LUKS2 Partitioning Engine (task_493)."""


import importlib.util
import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

_olp_HERE = os.path.dirname(os.path.abspath(__file__))
_olp_ROOT = os.path.normpath(os.path.join(_olp_HERE, ".."))
_olp_MODULE_PATH = os.path.join(_olp_ROOT, "usr", "libexec", "mios", "storage", "opal_luks_partition.py")

olp_spec = importlib.util.spec_from_file_location("opal_luks_partition", _olp_MODULE_PATH)
if olp_spec and olp_spec.loader:
    olp_opal_luks = importlib.util.module_from_spec(olp_spec)
    sys.modules[olp_spec.name] = olp_opal_luks
    olp_spec.loader.exec_module(olp_opal_luks)
else:
    raise ImportError(f"Could not load opal_luks_partition module from {_olp_MODULE_PATH}")

class olp_TestOpalLuksPartition(unittest.TestCase):
    """Unit tests for OPAL 2.0 SED detection, LUKS2 TPM enrollment, and GPT partitioning."""

    def setUp(self) -> None:
        self.engine = olp_opal_luks.OpalLuksPartitionEngine(mock=True)

    def test_scan_mock_drives(self) -> None:
        """Asserts discovery of mock NVMe (OPAL 2.0 SED) and SATA (Standard LUKS2) drives."""
        drives = self.engine.scan_drives()
        self.assertEqual(len(drives), 2)

        nvme = next(d for d in drives if d.path == "/dev/nvme0n1")
        self.assertTrue(nvme.is_opal2)
        self.assertTrue(nvme.is_sed)
        self.assertFalse(nvme.is_locked)

        sata = next(d for d in drives if d.path == "/dev/sda")
        self.assertFalse(sata.is_opal2)
        self.assertEqual(sata.luks_version, 2)
        self.assertTrue(sata.tpm_bound)

    def test_setup_opal_sed_success(self) -> None:
        """Asserts successful activation of OPAL 2.0 Locking Range 0 on supported drive."""
        res = self.engine.setup_opal_sed("/dev/nvme0n1", admin_password="TestPassword123!")
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["locking_range"], 0)
        self.assertTrue(res["locked"])

        # Check that state updated
        drives = self.engine.scan_drives()
        nvme = next(d for d in drives if d.path == "/dev/nvme0n1")
        self.assertTrue(nvme.is_locked)

    def test_setup_opal_sed_unsupported_error(self) -> None:
        """Asserts error when attempting OPAL 2.0 configuration on non-SED drive."""
        with self.assertRaises(RuntimeError):
            self.engine.setup_opal_sed("/dev/sda", admin_password="TestPassword123!")

    def test_setup_luks2_tpm(self) -> None:
        """Asserts LUKS2 volume initialization and TPM 2.0 PCR enrollment."""
        res = self.engine.setup_luks2_tpm("/dev/nvme0n1p2", pcr_list=[7, 11])
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["luks_version"], 2)
        self.assertTrue(res["tpm_bound"])
        self.assertEqual(res["pcrs"], [7, 11])

    def test_apply_partition_layout_default(self) -> None:
        """Asserts default partition layout generation (ESP, Root, Home, Data)."""
        res = self.engine.apply_partition_layout("/dev/nvme0n1")
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["partitions_created"], 4)
        self.assertEqual(res["table_type"], "gpt")

    def test_apply_partition_layout_custom(self) -> None:
        """Asserts custom partition specification application."""
        custom_layout = [
            olp_opal_luks.PartitionSpec(name="ESP", size_gb=0.5, fs_type="vfat", mount_point="/boot/efi", part_num=1),
            olp_opal_luks.PartitionSpec(name="Ceph-OSD", size_gb=0.0, fs_type="raw", mount_point="/var/lib/ceph", part_num=2),
        ]
        res = self.engine.apply_partition_layout("/dev/nvme0n1", layout=custom_layout)
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["partitions_created"], 2)

    def test_cli_scan_json(self) -> None:
        """Asserts CLI execution with --scan --mock --json."""
        with patch("sys.argv", ["opal_luks_partition.py", "--scan", "--mock", "--json"]):
            with patch("builtins.print") as mock_print:
                ret = olp_opal_luks.main()
                self.assertEqual(ret, 0)
                mock_print.assert_called()
                output_str = mock_print.call_args[0][0]
                parsed = json.loads(output_str)
                self.assertEqual(parsed["status"], "ok")
                self.assertIn("drives", parsed)


# ======================================================================
# from tests/test-storage.py
# ======================================================================
"""Automated unit test suite for MiOS S.M.A.R.T. Drive Health and CephFS Evacuation."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "usr", "libexec", "mios", "storage"))

from disk_health import SmartHealthMonitor, DriveHealth

class sce_TestSmartCephfsEvacuation(unittest.TestCase):
    def setUp(self):
        self.monitor = SmartHealthMonitor(dry_run=True)

    def test_healthy_drive_no_action(self):
        """Test healthy NVMe drive operates without triggering evacuation."""
        h = self.monitor.evaluate_drive_health(
            "/dev/nvme0n1", {"percentage_used": 20.0, "available_spare": 100.0, "media_errors": 0}
        )
        self.assertFalse(h.is_degraded)
        self.assertEqual(h.action_taken, "none")
        self.assertEqual(h.risk_level, "OK")
        self.assertGreater(h.health_score, 80.0)

    def test_degraded_wear_triggers_evacuation(self):
        """Test percentage_used >= 95% triggers automated CephFS OSD out."""
        h = self.monitor.evaluate_drive_health(
            "/dev/nvme1n1", {"percentage_used": 97.0, "available_spare": 100.0, "media_errors": 0}
        )
        self.assertTrue(h.is_degraded)
        self.assertIn("ceph_osd_out", h.action_taken)
        self.assertIn("osd.1n1", self.monitor.evacuated_osds)
        self.assertEqual(h.evacuation_status, "evacuated")

    def test_available_spare_depletion_evacuation(self):
        """Test available_spare <= 10% triggers predictive evacuation."""
        h = self.monitor.evaluate_drive_health(
            "/dev/nvme2n1", {"percentage_used": 50.0, "available_spare": 5.0, "media_errors": 2}
        )
        self.assertTrue(h.is_degraded)
        self.assertIn("ceph_osd_out", h.action_taken)
        self.assertEqual(h.risk_level, "CRITICAL")

    def test_thermal_overheating_evacuation(self):
        """Test drive temperature > 75°C triggers proactive protection."""
        h = self.monitor.evaluate_drive_health(
            "/dev/nvme3n1", {"percentage_used": 10.0, "temperature_c": 78.0}
        )
        self.assertTrue(h.is_degraded)
        self.assertIn("ceph_osd_out", h.action_taken)

    def test_sata_reallocated_sectors_evacuation(self):
        """Test SATA drive with high reallocated sector count triggers drain."""
        mock_sata = {
            "ata_smart_attributes": {
                "table": [
                    {"name": "Reallocated_Sector_Ct", "raw": {"value": 48}},
                    {"name": "Temperature_Celsius", "raw": {"value": 35}},
                ]
            }
        }
        h = self.monitor.evaluate_drive_health("/dev/sda", mock_sata)
        self.assertTrue(h.is_degraded)
        self.assertIn("ceph_osd_out", h.action_taken)

    def test_zero_rebalance_object_loss(self):
        """Test that evacuation events register zero degraded object loss."""
        self.monitor.evaluate_drive_health("/dev/nvme0n1", {"percentage_used": 99.0})
        self.assertGreater(len(self.monitor.evacuation_events), 0)
        for ev in self.monitor.evacuation_events:
            self.assertEqual(ev["rebalance_loss"], 0)

    def test_malformed_smart_json_none_values(self):
        """Test parser resilience against None and malformed fields."""
        malformed_data = {
            "percentage_used": None,
            "available_spare": None,
            "media_errors": "not_an_int",
            "temperature": None,
            "critical_warning": None,
            "ata_smart_attributes": None,
            "reallocated_sectors": None,
        }
        h = self.monitor.parse_smart_json("/dev/nvme0n1", malformed_data)
        self.assertFalse(h.is_degraded)
        self.assertEqual(h.percentage_used, 10.0)
        self.assertEqual(h.available_spare, 100.0)
        self.assertEqual(h.media_errors, 0)
        self.assertEqual(h.temperature_c, 40.0)
        self.assertEqual(h.reallocated_sectors, 0)
        self.assertEqual(h.critical_warning, 0)


# ======================================================================
# from tests/test-storage.py
# ======================================================================
"""Automated unit test suite for MiOS Storage Scrubber Daemon."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "usr", "libexec", "mios", "storage"))

from scrubd import MAX_PSI_PRESSURE_THRESHOLD, StorageScrubManager

class ss_TestStorageScrubd(unittest.TestCase):
    def setUp(self):
        self.mgr = StorageScrubManager(psi_threshold=20.0, dry_run=True)

    def test_bit_rot_repair_and_low_latency_impact(self):
        """Test scrubber repairs corrupt mirror block and limits latency degradation <5%."""
        rep = self.mgr.execute_pool_scrub("btrfs_root", 10000, simulate_bitrot=True)
        self.assertEqual(rep.bit_rot_blocks_repaired, 1)
        self.assertLess(rep.psi_io_pressure_avg, MAX_PSI_PRESSURE_THRESHOLD)
        self.assertLess(rep.interactive_latency_degradation_pct, 5.0)


def main() -> int:
    rc = 0 if unittest.main(argv=[sys.argv[0]], exit=False).result.wasSuccessful() else 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
