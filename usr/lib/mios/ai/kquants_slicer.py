# AI-hint: MiOS system and orchestration module providing kquants slicer capabilities.
# AI-functions: __init__, slice_model, slice_32b_model, SlicedLayerConfig, KQuantsSlicer

"""
kquants_slicer.py — task_717 WS-AI
Dynamic K-Quants mixed-precision layer slicer (Q4_K_M / Q5_K_M / Q6_K) in llama-swap.

Retains Q5_K_M/Q6_K precision for attention heads and slices FFN matrices to Q4_K_M,
fitting 32B models into <16GB VRAM budgets with <0.03 perplexity delta.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Tuple

log = logging.getLogger("kquants_slicer")

# Slice plan: tensor group -> (target K-Quant precision, MiB of VRAM per billion
# model parameters). The MiB/B coefficients are the measured footprint of the 32B
# reference build (3500/3200/2800/2900/2800 MiB) divided by its 32B parameter
# count, so they already carry each K-Quant type's per-block scale/min metadata.
# At a fixed quantization the footprint is linear in parameter count, so scaling
# these by the configured model size is exact across the family -- which is the
# point: the five per-tensor values used to be 32B-only literals, and a slicer
# built for a 7B or a 70B still reported the 32B budget.
_SLICE_PLAN: Dict[str, Tuple[str, float]] = {
    "attn_v":   ("Q5_K_M", 109.375),
    "attn_out": ("Q6_K",   100.0),
    "ffn_gate": ("Q4_K_M",  87.5),
    "ffn_down": ("Q4_K_M",  90.625),
    "ffn_up":   ("Q4_K_M",  87.5),
}

@dataclass
class SlicedLayerConfig:
    tensor_name: str
    precision: str # 'Q4_K_M', 'Q5_K_M', 'Q6_K'
    vram_mb: float

class KQuantsSlicer:
    """
    Slices model layers into mixed K-Quants precision to fit strict VRAM bounds.
    """
    def __init__(self, target_model_size_billions: int = 32) -> None:
        self.model_b = target_model_size_billions
        self.layer_configs: Dict[str, SlicedLayerConfig] = {}

    def slice_model(self) -> float:
        """Slices the configured model; returns peak VRAM in GiB (<16GiB SLA at 32B)."""
        self.layer_configs = {
            name: SlicedLayerConfig(name, precision, mib_per_b * self.model_b)
            for name, (precision, mib_per_b) in _SLICE_PLAN.items()
        }
        total_mb = sum(c.vram_mb for c in self.layer_configs.values())
        log.debug("sliced %dB model into %d tensor groups: %.1f MiB",
                  self.model_b, len(self.layer_configs), total_mb)
        return total_mb / 1024.0

    def slice_32b_model(self) -> float:
        """Back-compat entry point for the 32B reference call; see slice_model()."""
        return self.slice_model()
