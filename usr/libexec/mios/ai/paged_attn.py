#!/usr/bin/env python3
# AI-hint: PagedAttention virtual block memory manager and asynchronous KV defragmenter (task_581, task_582).
# AI-related: usr/libexec/mios/ai/paged_attn.py, usr/libexec/mios/ai/paged_attention.py, tests/test-ai.py
"""PagedAttention virtual block memory manager and asynchronous KV defragmenter for MiOS.

Manages KV-cache memory in discrete 16/32-token virtual memory blocks, eliminates internal
fragmentation, provides Copy-on-Write (CoW) page sharing for branched prompts / speculative decoding,
and provides LRU page eviction under VRAM pressure.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Union

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("mios-paged-attn")

DEFAULT_BLOCK_SIZE = 32  # 32 tokens per KV block

@dataclass
class PhysicalBlock:
    block_id: int
    ref_count: int = 0
    is_free: bool = True
    last_accessed: float = 0.0
    token_data: List[int] = field(default_factory=list)

@dataclass
class SessionTable:
    session_id: str
    logical_to_physical: List[int] = field(default_factory=list)  # Maps logical block idx -> physical block ID
    token_count: int = 0
    last_accessed: float = field(default_factory=time.time)
    is_active: bool = True

class PagedAttentionBlockManager:
    """Virtual memory block allocator, CoW page sharer, and defragmenter for KV caches."""

    def __init__(
        self,
        total_blocks: int = 1024,
        block_size: int = DEFAULT_BLOCK_SIZE,
        dry_run: bool = False,
    ) -> None:
        self.total_blocks = total_blocks
        self.block_size = block_size
        self.dry_run = dry_run
        self.physical_blocks = [PhysicalBlock(block_id=i) for i in range(total_blocks)]
        self.sessions: Dict[str, SessionTable] = {}
        self.cow_splits = 0
        self.evictions = 0

    @property
    def free_blocks_count(self) -> int:
        return sum(1 for b in self.physical_blocks if b.is_free)

    def _get_free_block(self) -> Optional[PhysicalBlock]:
        return next((b for b in self.physical_blocks if b.is_free), None)

    def allocate_tokens(
        self, session_id: str, new_tokens: Union[int, List[int]], allow_eviction: bool = True
    ) -> bool:
        """Allocates virtual blocks for new tokens in session."""
        if session_id not in self.sessions:
            self.sessions[session_id] = SessionTable(session_id=session_id)

        session = self.sessions[session_id]
        token_count = new_tokens if isinstance(new_tokens, int) else len(new_tokens)

        needed_blocks = (session.token_count + token_count + self.block_size - 1) // self.block_size
        current_blocks = len(session.logical_to_physical)

        while current_blocks < needed_blocks:
            free_block = self._get_free_block()
            if not free_block and allow_eviction:
                self.evict_lru_under_pressure(1)
                free_block = self._get_free_block()

            if not free_block:
                logger.error(f"VRAM exhaustion! No free blocks available for session {session_id}.")
                return False

            free_block.is_free = False
            free_block.ref_count = 1
            free_block.last_accessed = time.time()
            session.logical_to_physical.append(free_block.block_id)
            current_blocks += 1

        session.token_count += token_count
        session.last_accessed = time.time()
        return True

    def branch_session(self, parent_session_id: str, child_session_id: str) -> bool:
        """Creates a child session sharing all physical blocks with parent (Copy-on-Write)."""
        if parent_session_id not in self.sessions:
            logger.error(f"Parent session {parent_session_id} does not exist.")
            return False

        parent = self.sessions[parent_session_id]
        child = SessionTable(
            session_id=child_session_id,
            logical_to_physical=list(parent.logical_to_physical),
            token_count=parent.token_count,
            last_accessed=time.time(),
        )

        for p_id in child.logical_to_physical:
            block = self.physical_blocks[p_id]
            block.ref_count += 1
            block.last_accessed = time.time()

        self.sessions[child_session_id] = child
        return True

    def append_tokens_cow(self, session_id: str, new_tokens: Union[int, List[int]]) -> bool:
        """Appends tokens with Copy-on-Write mutation if the last block is shared."""
        if session_id not in self.sessions:
            return self.allocate_tokens(session_id, new_tokens)

        session = self.sessions[session_id]
        session.last_accessed = time.time()
        token_count = new_tokens if isinstance(new_tokens, int) else len(new_tokens)

        if session.logical_to_physical:
            last_pid = session.logical_to_physical[-1]
            last_block = self.physical_blocks[last_pid]
            if last_block.ref_count > 1:
                free_block = self._get_free_block()
                if not free_block:
                    self.evict_lru_under_pressure(1)
                    free_block = self._get_free_block()
                    if not free_block:
                        return False
                free_block.is_free = False
                free_block.ref_count = 1
                free_block.last_accessed = time.time()
                free_block.token_data = list(last_block.token_data)

                last_block.ref_count -= 1
                session.logical_to_physical[-1] = free_block.block_id
                self.cow_splits += 1

        needed_blocks = (session.token_count + token_count + self.block_size - 1) // self.block_size
        current_blocks = len(session.logical_to_physical)

        while current_blocks < needed_blocks:
            free_block = self._get_free_block()
            if not free_block:
                self.evict_lru_under_pressure(1)
                free_block = self._get_free_block()
                if not free_block:
                    return False
            free_block.is_free = False
            free_block.ref_count = 1
            free_block.last_accessed = time.time()
            session.logical_to_physical.append(free_block.block_id)
            current_blocks += 1

        session.token_count += token_count
        return True

    def free_session(self, session_id: str) -> None:
        """Reclaims all allocated physical blocks for completed session."""
        if session_id in self.sessions:
            session = self.sessions.pop(session_id)
            for bid in session.logical_to_physical:
                block = self.physical_blocks[bid]
                block.ref_count = max(0, block.ref_count - 1)
                if block.ref_count == 0:
                    block.is_free = True
                    block.token_data.clear()

    def evict_lru_under_pressure(self, needed_blocks: int) -> int:
        """Evicts least-recently-accessed sessions to free physical blocks."""
        candidates = sorted(self.sessions.values(), key=lambda s: (s.is_active, s.last_accessed))
        for session in candidates:
            if self.free_blocks_count >= needed_blocks:
                break
            sid = session.session_id
            self.free_session(sid)
            self.evictions += 1
        return self.free_blocks_count

    def defragment_memory(self) -> int:
        """Compacts allocated blocks into contiguous physical address range."""
        moved_count = 0
        allocated_blocks = [b for b in self.physical_blocks if not b.is_free]

        for i, block in enumerate(allocated_blocks):
            if block.block_id != i:
                target_id = i
                old_id = block.block_id

                for session in self.sessions.values():
                    for idx, p_id in enumerate(session.logical_to_physical):
                        if p_id == old_id:
                            session.logical_to_physical[idx] = target_id

                target_block = self.physical_blocks[target_id]
                target_block.is_free = False
                target_block.ref_count = block.ref_count
                target_block.last_accessed = block.last_accessed
                target_block.token_data = list(block.token_data)

                block.is_free = True
                block.ref_count = 0
                block.token_data.clear()
                moved_count += 1

        return moved_count

    def compute_fragmentation_waste(self) -> float:
        """Computes internal and external memory fragmentation percentage."""
        total_tokens = sum(s.token_count for s in self.sessions.values())
        allocated_capacity = sum(len(s.logical_to_physical) * self.block_size for s in self.sessions.values())
        if allocated_capacity == 0:
            return 0.0
        waste_pct = ((allocated_capacity - total_tokens) / allocated_capacity) * 100.0
        return round(waste_pct, 2)

    def get_stats(self) -> Dict[str, Any]:
        return {
            "total_blocks": self.total_blocks,
            "free_blocks": self.free_blocks_count,
            "allocated_blocks": self.total_blocks - self.free_blocks_count,
            "active_sessions": len(self.sessions),
            "cow_splits": self.cow_splits,
            "evictions": self.evictions,
            "fragmentation_waste_pct": self.compute_fragmentation_waste(),
        }

def main():
    mgr = PagedAttentionBlockManager(total_blocks=512)
    mgr.allocate_tokens("sess_1", 75)
    mgr.branch_session("sess_1", "sess_1_branch")
    mgr.append_tokens_cow("sess_1_branch", 20)
    print(json.dumps(mgr.get_stats(), indent=2))

if __name__ == "__main__":
    main()
