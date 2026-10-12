# AI-hint: Pure-stdlib BLADE/topology model for the agent-pipe (V4 + V5 multi-blade AI-related: ./mios_config.py, ./mios_agentreg.py, ./server.py, ....
# AI-doc: usr/share/doc/mios/manual/scheduler.md

from __future__ import annotations

import os
import socket
from typing import Callable, Optional

from mios_config import _toml_section

def _as_int(v, default: int = 0) -> int:
    """Coerce an optional capacity value to int, falling back to ``default`` when it
    is unset/blank/non-numeric (mirrors server._opt_int_mb's tolerant coercion)."""
    try:
        if v is None or str(v).strip() == "":
            return int(default)
        return int(float(v))
    except (TypeError, ValueError):
        try:
            return int(default)
        except (TypeError, ValueError):
            return 0

def local_blade_name() -> str:
    try:
        h = str(os.environ.get("MIOS_IDENTITY_HOSTNAME") or "").strip()
        if not h:
            h = str((_toml_section("identity") or {}).get("hostname") or "").strip()
        if not h:
            h = str(socket.gethostname() or "").strip()
        return h or str(socket.gethostname() or "").strip()
    except Exception:  # noqa: BLE001 -- degrade-open: a hostname probe must never raise
        try:
            return str(socket.gethostname() or "").strip()
        except Exception:  # noqa: BLE001
            return ""

def load_blade_pool(local_blade: str, local_vram_budget_mb,
                    local_load_ceil: Optional[float] = None) -> dict:
    _local_vram = _as_int(local_vram_budget_mb)
    _local_ceil = None
    if local_load_ceil is not None:
        try:
            _local_ceil = float(local_load_ceil)
        except (TypeError, ValueError):
            _local_ceil = None
    pool: dict = {local_blade: {"vram_budget_mb": _local_vram, "load_ceil": _local_ceil}}
    try:
        blades = _toml_section("blades")
    except Exception:  # noqa: BLE001 -- degrade-open: absent/broken section -> local only
        blades = {}
    if isinstance(blades, dict):
        for name, cfg in blades.items():
            if not isinstance(cfg, dict):
                continue
            entry = dict(pool.get(name) or {})
            vb = cfg.get("vram_budget_mb")
            if vb is not None and str(vb).strip() != "":
                entry["vram_budget_mb"] = _as_int(vb, entry.get("vram_budget_mb", _local_vram))
            elif "vram_budget_mb" not in entry:
                entry["vram_budget_mb"] = _local_vram
            lc = cfg.get("load_ceil")
            if lc is not None and str(lc).strip() != "":
                try:
                    entry["load_ceil"] = float(lc)
                except (TypeError, ValueError):
                    entry.setdefault("load_ceil", None)
            else:
                entry.setdefault("load_ceil", None)
            pool[name] = entry
    return pool

def endpoint_blade_map(registry: dict, endpoint_key: Callable[[str], str],
                       local_blade: str) -> dict:
    out: dict = {}
    if not isinstance(registry, dict):
        return out
    for _name, cfg in registry.items():
        if not isinstance(cfg, dict):
            continue
        ep = str(cfg.get("endpoint") or "").strip()
        if not ep:
            continue
        try:
            key = endpoint_key(ep)
        except Exception:  # noqa: BLE001 -- a bad endpoint string just isn't mapped
            continue
        if not key:
            continue
        out[key] = str(cfg.get("blade") or "").strip() or local_blade
    return out

def blade_for_endpoint(ep_blade_map: dict, endpoint_key: Callable[[str], str],
                       ep: str, local_blade: str) -> str:
    """The blade an endpoint lives on; degrade-open to the LOCAL blade when unknown."""
    try:
        return str(ep_blade_map.get(endpoint_key(ep)) or local_blade)
    except Exception:  # noqa: BLE001 -- degrade-open: unknown endpoint -> local blade
        return local_blade

def blade_vram_budget(blade_pool: dict, blade_name: str, local_vram_budget_mb) -> int:
    """The VRAM budget (MB) for a blade, degrading OPEN to the LOCAL scalar when the
    blade (or its capacity) is unknown -- so an endpoint whose blade can't be resolved
    is admitted against today's local budget, never wedged or locked out."""
    try:
        cap = (blade_pool.get(blade_name) or {}).get("vram_budget_mb")
        if cap:
            return int(cap)
    except Exception:  # noqa: BLE001 -- degrade-open
        pass
    return _as_int(local_vram_budget_mb)
