#!/usr/bin/env python3
# AI-hint: PagedAttention virtual block memory manager and asynchronous KV defragmenter (task_581, task_582).
# AI-related: usr/libexec/mios/ai/paged_attn.py, usr/libexec/mios/ai/paged_attention.py, tests/test-ai.py
"""PagedAttention virtual block memory manager proxy module for MiOS."""

from __future__ import annotations

from paged_attn import (
    DEFAULT_BLOCK_SIZE,
    PhysicalBlock,
    SessionTable,
    PagedAttentionBlockManager,
    main,
)

__all__ = [
    "DEFAULT_BLOCK_SIZE",
    "PhysicalBlock",
    "SessionTable",
    "PagedAttentionBlockManager",
    "main",
]

if __name__ == "__main__":
    main()
