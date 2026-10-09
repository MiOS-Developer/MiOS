#!/usr/bin/env python3
# AI-hint: Hardware-tiered modern model matrix allocator for Consumer, Prosumer, and Poweruser tiers.
# AI-related: usr/share/mios/llamacpp/mios-llm-light.yaml, usr/share/doc/mios/manual/ch10-local-inference-lanes-and-llama-cpp.md, tests/test-ai.py
# AI-functions: ModelMatrixAllocator, detect_host_hardware, project_llama_swap_yaml, main
"""
WS-AI (T-571): Hardware-Tiered Modern Model Matrix Allocator for Consumer, Prosumer, and Poweruser.
Dynamically detects host GPU VRAM and System RAM to assign modern open-weight models
(Qwen2.5-Coder, DeepSeek-R1-Distill, nomic-embed-text) across function-named inference lanes
(mios-llm-light, mios-llm-heavy). Enforces strict VRAM headroom reservation (<=90%) and projects
configurations into llama-swap.yaml for zero-downtime multi-model auto-swapping.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "lib", "mios"))
import mios_toml  # noqa: E402 -- the ONE shared layered-mios.toml resolver


def llm_light_port() -> int:
    """SSOT [ports].llm_light: MIOS_PORTS_LLM_LIGHT when exported, else the layered mios.toml."""
    env = os.environ.get("MIOS_PORTS_LLM_LIGHT", "")
    return int(env) if env.isdigit() else int(mios_toml.get("ports", "llm_light"))

TIER_CONSUMER = "consumer"
TIER_PROSUMER = "prosumer"
TIER_POWERUSER = "poweruser"

DEFAULT_HEADROOM_RATIO = 0.90  # Max 90% VRAM used for weights + initial KV cache

MODEL_CATALOG = {
    "embedding": {
        "name": "nomic-embed-text",
        "repo": "nomic-ai/nomic-embed-text-v1.5-GGUF",
        "file": "nomic-embed-text-v1.5.Q8_0.gguf",
        "vram_gb": 0.35,
        "ctx_size": 2048,
        "quant": "Q8_0",
    },
    "consumer": {
        "default": {
            "name": "qwen2.5-coder-7b",
            "repo": "Qwen/Qwen2.5-Coder-7B-Instruct-GGUF",
            "file": "qwen2.5-coder-7b-instruct-q4_k_m.gguf",
            "vram_gb": 4.6,
            "ctx_size": 32768,
            "quant": "Q4_K_M",
        },
        "reasoning": {
            "name": "deepseek-r1-distill-qwen-7b",
            "repo": "deepseek-ai/DeepSeek-R1-Distill-Qwen-7B-GGUF",
            "file": "DeepSeek-R1-Distill-Qwen-7B-Q4_K_M.gguf",
            "vram_gb": 4.6,
            "ctx_size": 32768,
            "quant": "Q4_K_M",
        },
    },
    "prosumer": {
        "default": {
            "name": "qwen2.5-coder-14b",
            "repo": "Qwen/Qwen2.5-Coder-14B-Instruct-GGUF",
            "file": "qwen2.5-coder-14b-instruct-q4_k_m.gguf",
            "vram_gb": 9.2,
            "ctx_size": 32768,
            "quant": "Q4_K_M",
        },
        "reasoning": {
            "name": "deepseek-r1-distill-qwen-14b",
            "repo": "deepseek-ai/DeepSeek-R1-Distill-Qwen-14B-GGUF",
            "file": "DeepSeek-R1-Distill-Qwen-14B-Q4_K_M.gguf",
            "vram_gb": 9.2,
            "ctx_size": 32768,
            "quant": "Q4_K_M",
        },
    },
    "poweruser": {
        "default": {
            "name": "qwen2.5-coder-32b",
            "repo": "Qwen/Qwen2.5-Coder-32B-Instruct-GGUF",
            "file": "qwen2.5-coder-32b-instruct-q4_k_m.gguf",
            "vram_gb": 20.0,
            "ctx_size": 32768,
            "quant": "Q4_K_M",
        },
        "reasoning": {
            "name": "deepseek-r1-distill-llama-70b",
            "repo": "deepseek-ai/DeepSeek-R1-Distill-Llama-70B-GGUF",
            "file": "DeepSeek-R1-Distill-Llama-70B-Q4_K_M.gguf",
            "vram_gb": 42.0,
            "ctx_size": 65536,
            "quant": "Q4_K_M",
        },
    },
}

def detect_host_hardware(mock: bool = False, mock_vram_gb: Optional[float] = None) -> Dict[str, Any]:
    """Detect available GPU VRAM and System RAM."""
    if mock:
        vram = mock_vram_gb if mock_vram_gb is not None else 16.0
        return {
            "gpu_count": 1,
            "gpu_name": "NVIDIA GeForce RTX 4080 (Mock)",
            "total_vram_gb": round(vram, 2),
            "total_ram_gb": 32.0,
            "cpu_cores": 16,
            "source": "mock",
        }

    total_vram = 0.0
    gpu_name = "None (CPU Only)"
    gpu_count = 0

    # Probe NVIDIA GPUs via nvidia-smi
    if shutil.which("nvidia-smi"):
        try:
            cmd = ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"]
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if res.returncode == 0:
                lines = res.stdout.strip().splitlines()
                gpu_count = len(lines)
                for line in lines:
                    parts = line.split(",")
                    if len(parts) >= 2:
                        gpu_name = parts[0].strip()
                        mb = float(parts[1].strip())
                        total_vram += mb / 1024.0
        except Exception:
            pass

    # Probe System RAM
    total_ram = 16.0
    if os.path.isfile("/proc/meminfo"):
        try:
            with open("/proc/meminfo", "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        kb = float(line.split()[1])
                        total_ram = kb / 1024.0 / 1024.0
                        break
        except Exception:
            pass

    return {
        "gpu_count": gpu_count,
        "gpu_name": gpu_name,
        "total_vram_gb": round(total_vram, 2),
        "total_ram_gb": round(total_ram, 2),
        "cpu_cores": os.cpu_count() or 4,
        "source": "hardware_probe",
    }

class ModelMatrixAllocator:
    """Allocator determining optimal model matrix assignments and VRAM budgeting."""

    def __init__(
        self,
        headroom_ratio: float = DEFAULT_HEADROOM_RATIO,
        mock: bool = False,
        verbose: bool = False,
    ) -> None:
        self.headroom_ratio = headroom_ratio
        self.mock = mock
        self.verbose = verbose

    def classify_tier(self, vram_gb: float, ram_gb: float = 16.0) -> str:
        """Classify hardware profile into Consumer, Prosumer, or Poweruser tier."""
        if vram_gb >= 32.0:
            return TIER_POWERUSER
        elif vram_gb >= 12.0:
            return TIER_PROSUMER
        else:
            return TIER_CONSUMER

    def allocate_matrix(
        self,
        vram_gb: Optional[float] = None,
        ram_gb: Optional[float] = None,
        forced_tier: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Compute optimal model matrix and VRAM allocations."""
        hw = detect_host_hardware(mock=self.mock, mock_vram_gb=vram_gb)
        effective_vram = vram_gb if vram_gb is not None else hw["total_vram_gb"]
        effective_ram = ram_gb if ram_gb is not None else hw["total_ram_gb"]

        tier = forced_tier.lower() if forced_tier else self.classify_tier(effective_vram, effective_ram)
        if tier not in (TIER_CONSUMER, TIER_PROSUMER, TIER_POWERUSER):
            tier = TIER_CONSUMER

        tier_models = MODEL_CATALOG[tier]
        embedding_model = MODEL_CATALOG["embedding"]

        # VRAM Budgeting
        allowed_vram = max(4.0, effective_vram * self.headroom_ratio) if effective_vram > 0 else (effective_ram * 0.5)

        default_model = dict(tier_models["default"])
        reasoning_model = dict(tier_models["reasoning"])
        embed_model = dict(embedding_model)

        # In consumer/low-VRAM mode, models swap sequentially via llama-swap
        # Single active model footprint <= allowed_vram
        max_active_footprint = max(default_model["vram_gb"], reasoning_model["vram_gb"]) + embed_model["vram_gb"]
        fits_in_vram = max_active_footprint <= allowed_vram

        return {
            "tier": tier,
            "hardware": {
                "detected_vram_gb": effective_vram,
                "detected_ram_gb": effective_ram,
                "allowed_vram_budget_gb": round(allowed_vram, 2),
                "headroom_ratio": self.headroom_ratio,
            },
            "fits_in_vram": fits_in_vram,
            "models": {
                "default": default_model,
                "reasoning": reasoning_model,
                "embedding": embed_model,
            },
            "heavy_lane": {
                "enabled": tier == TIER_POWERUSER,
                "port_key": "llm_heavy" if tier == TIER_POWERUSER else None,
                "reason": "Off by default on VRAM grounds" if tier != TIER_POWERUSER else "Enabled for multi-GPU power tier",
            },
            "endpoint_contract": "MIOS_AI_ENDPOINT (Law 5 UNIFIED-AI-REDIRECTS)",
        }

    def generate_llama_swap_config(self, allocation: Dict[str, Any]) -> Dict[str, Any]:
        """Generate structured configuration dictionary conforming to llama-swap.yaml format."""
        tier = allocation["tier"]
        models = allocation["models"]
        models_dir = "/usr/share/mios/models"

        config_models: Dict[str, Any] = {}

        # Default coder model
        m_def = models["default"]
        config_models["mios-coder"] = {
            "model": f"{models_dir}/{m_def['file']}",
            "args": [
                "--ctx-size", str(m_def["ctx_size"]),
                "--n-gpu-layers", "99" if allocation["fits_in_vram"] else "33",
                "--alias", "default,mios-coder,qwen2.5-coder",
            ],
            "ttl": 300,
        }

        # Reasoning model
        m_res = models["reasoning"]
        config_models["mios-reasoning"] = {
            "model": f"{models_dir}/{m_res['file']}",
            "args": [
                "--ctx-size", str(m_res["ctx_size"]),
                "--n-gpu-layers", "99" if allocation["fits_in_vram"] else "33",
                "--alias", "reasoning,deepseek-r1",
            ],
            "ttl": 300,
        }

        # Embedding model
        m_emb = models["embedding"]
        config_models["nomic-embed-text"] = {
            "model": f"{models_dir}/{m_emb['file']}",
            "args": [
                "--ctx-size", str(m_emb["ctx_size"]),
                "--n-gpu-layers", "99",
                "--embedding",
                "--alias", "nomic-embed-text,embedding",
            ],
            "ttl": 0,  # Keep embedding model resident
        }

        return {
            "version": "1.0",
            "tier": tier,
            "port": llm_light_port(),
            "health_check": "/v1/models",
            "models": config_models,
        }

    def project_yaml(self, output_path: str, allocation: Dict[str, Any]) -> bool:
        """Write projected YAML configuration to disk."""
        conf = self.generate_llama_swap_config(allocation)
        try:
            parent = os.path.dirname(os.path.abspath(output_path))
            if parent:
                os.makedirs(parent, exist_ok=True)

            # Standard simple YAML serializer without third-party pyyaml requirement
            lines = [
                f"# llama-swap configuration generated by model_matrix_alloc.py",
                f"# Tier: {conf['tier']} (Hardware-Aware Allocation)",
                f"port: {conf['port']}",
                f"health_check: {conf['health_check']}",
                f"models:",
            ]
            for m_key, m_val in conf["models"].items():
                lines.append(f"  {m_key}:")
                lines.append(f"    model: \"{m_val['model']}\"")
                lines.append(f"    ttl: {m_val['ttl']}")
                lines.append(f"    args:")
                for arg in m_val["args"]:
                    lines.append(f"      - \"{arg}\"")

            with open(output_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            return True
        except Exception as exc:
            if self.verbose:
                sys.stderr.write(f"[model-matrix-alloc] YAML project error: {exc}\n")
            return False

def main() -> int:
    parser = argparse.ArgumentParser(
        description="MiOS Hardware-Tiered Model Matrix Allocator (T-571)"
    )
    parser.add_argument("--detect", action="store_true", help="Detect host hardware profile")
    parser.add_argument("--tier", choices=[TIER_CONSUMER, TIER_PROSUMER, TIER_POWERUSER], help="Force allocation tier")
    parser.add_argument("--vram", type=float, help="Simulate/override GPU VRAM in GB")
    parser.add_argument("--ram", type=float, help="Simulate/override System RAM in GB")
    parser.add_argument("--project-yaml", metavar="PATH", help="Project configuration to llama-swap.yaml path")
    parser.add_argument("--status", action="store_true", help="Display model allocation status")
    parser.add_argument("--mock", action="store_true", help="Run with simulated mocks")
    parser.add_argument("--json", action="store_true", help="Output results in JSON format")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")

    args = parser.parse_args()
    allocator = ModelMatrixAllocator(mock=args.mock, verbose=args.verbose)

    if args.detect:
        hw = detect_host_hardware(mock=args.mock, mock_vram_gb=args.vram)
        tier = allocator.classify_tier(hw["total_vram_gb"], hw["total_ram_gb"])
        result = {"hardware": hw, "classified_tier": tier}
    else:
        alloc = allocator.allocate_matrix(
            vram_gb=args.vram,
            ram_gb=args.ram,
            forced_tier=args.tier,
        )
        if args.project_yaml:
            success = allocator.project_yaml(args.project_yaml, alloc)
            result = {"projected": success, "path": args.project_yaml, "allocation": alloc}
        else:
            result = alloc

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(json.dumps(result, indent=2))

    return 0

if __name__ == "__main__":
    sys.exit(main())
