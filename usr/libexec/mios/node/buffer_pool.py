#!/usr/bin/env python3
# AI-hint: Zero-copy network buffer pooling for mios-node frames (task_337 / task_2570).
# AI-related: usr/libexec/mios/node/wire.py, tests/test-node.py
"""
MiOS Zero-Copy Network Buffer Pool.
Provides bucketed allocations (Small=256B, Medium=4KB, Large=64KB, Huge=1MB),
RAII context manager auto-recycling, bounded memory capacity, and zero-copy memoryviews.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
import os
import sys
import threading
from typing import Dict, List, Optional, Tuple

_NODE_DIR = os.path.dirname(os.path.abspath(__file__))
if _NODE_DIR not in sys.path:
    sys.path.insert(0, _NODE_DIR)

class BucketTier(IntEnum):
    SMALL = 256
    MEDIUM = 4096
    LARGE = 65536
    HUGE = 1048576

    @classmethod
    def from_size(cls, size: int) -> BucketTier:
        if size <= cls.SMALL:
            return cls.SMALL
        elif size <= cls.MEDIUM:
            return cls.MEDIUM
        elif size <= cls.LARGE:
            return cls.LARGE
        return cls.HUGE

    @property
    def max_pool_capacity(self) -> int:
        if self == BucketTier.SMALL:
            return 256
        elif self == BucketTier.MEDIUM:
            return 64
        elif self == BucketTier.LARGE:
            return 32
        return 8

@dataclass
class PoolStats:
    allocations: int = 0
    recycles: int = 0
    pool_hits: int = 0
    pool_misses: int = 0
    active_leased: int = 0

class PooledBuffer:
    """RAII-guarded buffer wrapping a bytearray with zero-copy memoryview support."""

    def __init__(
        self,
        raw_buffer: bytearray,
        tier: BucketTier,
        pool: Optional[BufferPool] = None,
    ) -> None:
        self._raw: Optional[bytearray] = raw_buffer
        self._tier = tier
        self._pool = pool
        self._released = False

    @property
    def tier(self) -> BucketTier:
        return self._tier

    @property
    def capacity(self) -> int:
        return len(self._raw) if self._raw is not None else 0

    def len(self) -> int:
        return len(self._raw) if self._raw is not None else 0

    def write(self, data: bytes) -> None:
        if self._released or self._raw is None:
            raise RuntimeError("Buffer is already released")
        self._raw.clear()
        self._raw.extend(data)

    def extend(self, data: bytes) -> None:
        if self._released or self._raw is None:
            raise RuntimeError("Buffer is already released")
        self._raw.extend(data)

    def clear(self) -> None:
        if self._released or self._raw is None:
            raise RuntimeError("Buffer is already released")
        self._raw.clear()

    def as_bytes(self) -> bytes:
        if self._released or self._raw is None:
            raise RuntimeError("Buffer is already released")
        return bytes(self._raw)

    def as_memoryview(self) -> memoryview:
        if self._released or self._raw is None:
            raise RuntimeError("Buffer is already released")
        return memoryview(bytes(self._raw))

    def slice(self, start: int, end: int) -> memoryview:
        if self._released or self._raw is None:
            raise RuntimeError("Buffer is already released")
        return memoryview(bytes(self._raw))[start:end]

    def split_prefix(self, at: int) -> bytes:
        if self._released or self._raw is None:
            raise RuntimeError("Buffer is already released")
        if at > len(self._raw):
            raise IndexError(f"Prefix split {at} exceeds buffer length {len(self._raw)}")
        prefix = bytes(self._raw[:at])
        del self._raw[:at]
        return prefix

    def release(self) -> None:
        if not self._released:
            self._released = True
            if self._pool is not None and self._raw is not None:
                self._pool._recycle(self._tier, self._raw)
                self._raw = None

    def __enter__(self) -> PooledBuffer:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.release()

    def __del__(self) -> None:
        self.release()

class BufferPool:
    """Thread-safe bucketed buffer pool with bounded memory retention."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buckets: Dict[BucketTier, List[bytearray]] = {
            BucketTier.SMALL: [],
            BucketTier.MEDIUM: [],
            BucketTier.LARGE: [],
            BucketTier.HUGE: [],
        }
        self._stats = PoolStats()

    def acquire(self, size_hint: int) -> PooledBuffer:
        tier = BucketTier.from_size(size_hint)
        return self.acquire_exact(tier)

    def acquire_exact(self, tier: BucketTier) -> PooledBuffer:
        with self._lock:
            self._stats.allocations += 1
            self._stats.active_leased += 1
            bucket = self._buckets[tier]
            if bucket:
                self._stats.pool_hits += 1
                raw = bucket.pop()
                raw.clear()
            else:
                self._stats.pool_misses += 1
                raw = bytearray()

        return PooledBuffer(raw_buffer=raw, tier=tier, pool=self)

    def _recycle(self, tier: BucketTier, raw: bytearray) -> None:
        raw.clear()
        with self._lock:
            if self._stats.active_leased > 0:
                self._stats.active_leased -= 1

            bucket = self._buckets[tier]
            if len(bucket) < tier.max_pool_capacity:
                bucket.append(raw)
                self._stats.recycles += 1

    def get_stats(self) -> PoolStats:
        with self._lock:
            return PoolStats(
                allocations=self._stats.allocations,
                recycles=self._stats.recycles,
                pool_hits=self._stats.pool_hits,
                pool_misses=self._stats.pool_misses,
                active_leased=self._stats.active_leased,
            )

    def bucket_depths(self) -> Tuple[int, int, int, int]:
        with self._lock:
            return (
                len(self._buckets[BucketTier.SMALL]),
                len(self._buckets[BucketTier.MEDIUM]),
                len(self._buckets[BucketTier.LARGE]),
                len(self._buckets[BucketTier.HUGE]),
            )
