# AI-hint: MiOS system and orchestration module providing streaming llm capabilities.
# AI-functions: current_allocated_tokens, rolling_capacity, __post_init__, __init__, append_token, StreamingKVCache, StreamingLLMManager

"""
streaming_llm.py — task_699 WS-AI
StreamingLLM attention sink pinner and rolling KV eviction manager in llama-swap.

Pins initial attention sink tokens (positions 0..3) permanently while maintaining
a rolling FIFO circular buffer for positions >= 4, bounding memory consumption
and enabling perpetual infinite generation with zero OOM crashes.
"""
from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Deque

log = logging.getLogger("streaming_llm")

@dataclass
class StreamingKVCache:
    sink_size: int = 4
    window_size: int = 32768
    sink_tokens: list[int] = field(default_factory=list)
    rolling_tokens: Deque[int] = field(default_factory=deque)
    total_tokens_seen: int = 0

    @property
    def rolling_capacity(self) -> int:
        """Rolling slots remaining once the pinned sinks have taken their share."""
        return max(0, self.window_size - self.sink_size)

    def __post_init__(self) -> None:
        # Eviction is per-token on an infinite generation, so it has to be O(1).
        # A list evicts the head in O(n) -- at a 32k window that is a 32k-element
        # memmove for every single token. A bounded deque pops the head in O(1),
        # and its maxlen makes the window a property of the structure rather than
        # of the one call site that remembers to trim.
        self.rolling_tokens = deque(self.rolling_tokens, maxlen=self.rolling_capacity)

    @property
    def current_allocated_tokens(self) -> int:
        return len(self.sink_tokens) + len(self.rolling_tokens)

class StreamingLLMManager:
    """
    Manages StreamingLLM KV attention sink pinning and rolling window eviction.
    """
    def __init__(self, sink_size: int = 4, window_size: int = 32768) -> None:
        self.sink_size = sink_size
        self.window_size = window_size
        self.cache = StreamingKVCache(sink_size=sink_size, window_size=window_size)

    def append_token(self, token_id: int) -> dict:
        """Appends a token, preserving sinks and evicting oldest rolling tokens if full."""
        c = self.cache
        c.total_tokens_seen += 1

        if len(c.sink_tokens) < c.sink_size:
            c.sink_tokens.append(token_id)
            evicted = False
        else:
            evicted = len(c.rolling_tokens) >= c.rolling_capacity
            if evicted and c.rolling_tokens:
                # Evict oldest token from rolling window (FIFO)
                c.rolling_tokens.popleft()
            c.rolling_tokens.append(token_id)

        return {
            "total_seen": c.total_tokens_seen,
            "allocated": c.current_allocated_tokens,
            "evicted": evicted
        }
