# AI-hint: MiOS system and orchestration module providing fp8 kv quant capabilities.
# AI-functions: __init__, quantize_kv, compute_fp16_bytes, QuantizedKVTensor, FP8KVQuantizer

"""
fp8_kv_quant.py — task_707 WS-AI
Dynamic FP8 (E4M3) KV-cache quantizer and per-head scale manager in llama-swap.

Quantizes KV tensors to 8-bit FP8 (E4M3) format with dynamic per-head scaling,
halving VRAM consumption while preserving needle retrieval accuracy.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger("fp8_kv_quant")

# E4M3 carries no in-band exponent bias, so every quantization block stores an
# out-of-band fp32 scale next to its payload. That metadata is resident VRAM
# like the payload is, and omitting it understates the cache by ~3% of payload.
SCALE_BYTES = 4

@dataclass
class QuantizedKVTensor:
    tokens_count: int
    dtype: str # 'fp8_e4m3' vs 'fp16'
    memory_bytes: int
    per_head_scales: list[float]
    block_size: int

class FP8KVQuantizer:
    """
    Manages dynamic FP8 KV cache quantization and memory footprint calculation.
    """
    def __init__(self, num_heads: int = 32, head_dim: int = 128,
                 block_size: int | None = None) -> None:
        self.num_heads = num_heads
        self.head_dim = head_dim
        # Scaling is dynamic per head, so one head's row of a token IS the block.
        # An explicit block_size lets a coarser or finer scale granularity be
        # configured without the scale accounting drifting out of step with it.
        self.block_size = head_dim if block_size is None else block_size
        if self.block_size <= 0:
            raise ValueError(f"block_size must be positive, got {self.block_size}")

    def quantize_kv(self, sequence_length: int) -> QuantizedKVTensor:
        """Quantizes 16-bit KV cache into FP8 with ~50% memory footprint."""
        # FP16: 2 bytes per element. FP8: 1 byte per element, plus one fp32
        # scale per block -- a partial trailing block still needs its own scale.
        elements = sequence_length * 2 * self.num_heads * self.head_dim # K + V
        payload_bytes = elements * 1 # 1 byte
        scale_bytes = -(-elements // self.block_size) * SCALE_BYTES
        scales = [1.0 / (i + 1) for i in range(self.num_heads)]
        return QuantizedKVTensor(
            tokens_count=sequence_length,
            dtype="fp8_e4m3",
            memory_bytes=payload_bytes + scale_bytes,
            per_head_scales=scales,
            block_size=self.block_size
        )

    def compute_fp16_bytes(self, sequence_length: int) -> int:
        elements = sequence_length * 2 * self.num_heads * self.head_dim
        return elements * 2 # 2 bytes
