# AI-hint: Agent/node REGISTRY builders extracted verbatim from server.py (refactor R3/mios_agentreg wave).
# AI-doc: usr/share/doc/mios/manual/routing.md

from __future__ import annotations

import os

import mios_hopbudget   # WS-4 effort-width width cap (pure module; never imports server)

from mios_config import (  # noqa: E402
    _toml_section,
    BACKEND,
    BACKEND_MODEL,
    _AGENT_AUTH_BY_HOSTPORT,
)

_is_remote_endpoint = None
_opt_int_mb = None
log = None
CATALOG_FAIL_MODE = "warn"
NODES_RESEARCH_ONLY = False

_AGENT_REGISTRY: dict = {}
_agent_binding = None
_endpoint_key = None
_ROLE_SYSTEM_DIR = None
EFFORT_DEFAULT = None
SWARM_MAX_WIDTH = None

def configure(*, is_remote_endpoint=None, opt_int_mb=None, logger=None,
              catalog_fail_mode=None, nodes_research_only=None,
              agent_registry=None, agent_binding=None, endpoint_key=None,
              role_system_dir=None, effort_default=None, swarm_max_width=None) -> None:
    global _is_remote_endpoint, _opt_int_mb, log
    global CATALOG_FAIL_MODE, NODES_RESEARCH_ONLY
    global _AGENT_REGISTRY, _agent_binding, _endpoint_key
    global _ROLE_SYSTEM_DIR, EFFORT_DEFAULT, SWARM_MAX_WIDTH
    if is_remote_endpoint is not None:
        _is_remote_endpoint = is_remote_endpoint
    if opt_int_mb is not None:
        _opt_int_mb = opt_int_mb
    if logger is not None:
        log = logger
    if catalog_fail_mode is not None:
        CATALOG_FAIL_MODE = catalog_fail_mode
    if nodes_research_only is not None:
        NODES_RESEARCH_ONLY = nodes_research_only
    if agent_registry is not None:
        _AGENT_REGISTRY = agent_registry
    if agent_binding is not None:
        _agent_binding = agent_binding
    if endpoint_key is not None:
        _endpoint_key = endpoint_key
    if role_system_dir is not None:
        _ROLE_SYSTEM_DIR = role_system_dir
    if effort_default is not None:
        EFFORT_DEFAULT = effort_default
    if swarm_max_width is not None:
        SWARM_MAX_WIDTH = swarm_max_width

def _build_agent_engines(raw_cfg: dict, entry: dict) -> dict:
    engines: dict = {}
    home = (str(entry.get("lane") or "").strip() or "gpu")
    if entry.get("endpoint"):
        engines[home] = {"endpoint": entry["endpoint"],
                         "model": entry.get("model", "")}
    if entry.get("cpu_endpoint"):
        engines["cpu"] = {"endpoint": entry["cpu_endpoint"],
                          "model": entry.get("cpu_model") or entry.get("model", "")}
    for _tbl in ("engines", "nodes"):
        raw = raw_cfg.get(_tbl)
        if isinstance(raw, dict):
            for label, b in raw.items():
                if isinstance(b, dict) and b.get("endpoint"):
                    engines[str(label).lower().strip()] = {
                        "endpoint": str(b["endpoint"]).rstrip("/"),
                        "model": str(b.get("model") or entry.get("model", "")),
                    }
    return engines

def _load_agent_registry() -> dict[str, dict]:
    registry: dict[str, dict] = {}
    try:
        raw_agents = _toml_section("agents") or {}
        agents = {k: v.copy() for k, v in raw_agents.items() if isinstance(v, dict)}
        _agent_defaults = (agents.pop("_defaults", {})
                           if isinstance(agents.get("_defaults"), dict) else {})
        _AGENT_AUTH_BY_HOSTPORT.clear()  # WS-FED/G2: rebuilt each load
        for name, cfg in agents.items():
            if name.startswith("_") or not isinstance(cfg, dict):
                continue
            cfg = {**_agent_defaults, **cfg}
            _ep_x = os.path.expandvars(str(cfg.get("endpoint", ""))).rstrip("/")
            _kind = str(cfg.get("kind", "")).strip().lower()
            _hg_default = (
                _kind in ("remote-http", "cli", "mobile", "edge", "node", "a2a")
                or not bool(cfg.get("enabled", True))
                or _is_remote_endpoint(_ep_x)
            )
            registry[name] = {
                "endpoint": os.path.expandvars(str(cfg.get("endpoint", ""))).rstrip("/"),
                "model":    str(cfg.get("model", name)),
                "role":     str(cfg.get("role", "general")),
                "default":  bool(cfg.get("default", False)),
                "strengths": list(cfg.get("strengths") or []),
                "lane":     str(cfg.get("lane", "")).lower().strip(),
                "denied_verbs":  list(cfg.get("denied_verbs") or []),
                "allowed_verbs": list(cfg.get("allowed_verbs") or []),
                "max_permission": str(cfg.get("max_permission", "")).strip().lower(),
                "fanout":   bool(cfg.get("fanout", True)),
                "cpu_endpoint": str(cfg.get("cpu_endpoint", "")).rstrip("/"),
                "cpu_model":    str(cfg.get("cpu_model", "")),
                "health_gate":  bool(cfg.get("health_gate", _hg_default)),
                "failover_agents": [str(s) for s in (cfg.get("failover_agents")
                                                    or []) if str(s).strip()],
                "research_only": bool(cfg.get("research_only", False)),
                "kind":      (_kind or ("remote-http" if _is_remote_endpoint(_ep_x)
                                        else "local-http")),
                "enabled":   bool(cfg.get("enabled", True)),
                "transport": str(cfg.get("transport",
                                         "cli" if _kind == "cli" else "http")).strip().lower(),
                "timeout_s": int(cfg.get("timeout_s", 0) or 0),
                "auth":  cfg.get("auth") if isinstance(cfg.get("auth"), dict) else {},
                "trust": cfg.get("trust") if isinstance(cfg.get("trust"), dict) else {},
            }
            registry[name]["engines"] = _build_agent_engines(cfg, registry[name])
            _auth_t = str((registry[name].get("auth") or {}).get("header_template") or "").strip()
            _ep0 = registry[name].get("endpoint") or ""
            if _auth_t and _ep0:
                _hp0 = _ep0.split("://")[-1].split("/")[0]
                _rendered = os.path.expandvars(_auth_t)
                if _hp0 and "${" not in _rendered and ":" in _rendered:
                    _AGENT_AUTH_BY_HOSTPORT[_hp0] = _rendered
    except Exception as e:
        log.warning("agent registry load failed: %s; using fallback", e)
        if CATALOG_FAIL_MODE == "fail":   # WS-A1 fail-loud (opt-in)
            raise
    if not registry:
        registry["hermes"] = {
            "endpoint": BACKEND, "model": BACKEND_MODEL,
            "role": "general", "default": True, "strengths": [],
        }
    return registry

def _load_node_pool(registry: dict[str, dict]) -> int:
    try:
        nodes = _toml_section("nodes")
    except Exception as e:  # noqa: BLE001 -- degrade-open
        log.warning("node pool load failed: %s; no nodes injected", e)
        return 0
    if not isinstance(nodes, dict) or not nodes:
        return 0
    n = 0
    for name, cfg in nodes.items():
        if not isinstance(cfg, dict):
            continue
        ep = str(cfg.get("endpoint", "")).rstrip("/")
        if not ep:
            continue  # inert / privacy-empty node (e.g. vendor local-igpu)
        lane = str(cfg.get("lane", "")).lower().strip() or "gpu"
        _is_local = ("localhost" in ep) or ("127.0.0.1" in ep)
        health_gate = bool(cfg.get("health_gate", not _is_local))
        entry: dict = {
            "endpoint": ep,
            "model":    str(cfg.get("model") or "mios-agent"),
            "role":     str(cfg.get("role", "research")),
            "job":      str(cfg.get("job",
                          "Concurrent research worker -- one MiOS-Agent brain "
                          "dispatched on this node to research a facet in "
                          "parallel with the rest of the pool.")),
            "default":  False,
            "strengths": list(cfg.get("strengths")
                              or ["research", "web_search", "summarize"]),
            "lane":     lane,
            "blade":     str(cfg.get("blade", "")).strip(),
            "sub_lane":  str(cfg.get("sub_lane", "")).lower().strip(),
            "vram_mb":   _opt_int_mb(cfg.get("vram_mb")),
            "ram_mb":    _opt_int_mb(cfg.get("ram_mb")),
            "tool_capable": bool(cfg.get("tool_capable", True)),
            "fanout":   bool(cfg.get("fanout", True)),
            "cpu_endpoint": str(cfg.get("cpu_endpoint", "")).rstrip("/"),
            "cpu_model":    str(cfg.get("cpu_model", "")),
            "api":          str(cfg.get("api", "")).strip().lower(),
            "health_gate":  health_gate,
            "failover_agents": [str(s) for s in (cfg.get("failover_agents")
                                                or []) if str(s).strip()],
            "research_only": bool(cfg.get("research_only", NODES_RESEARCH_ONLY)),
        }
        entry["engines"] = _build_agent_engines(cfg, entry)
        registry[f"node:{name}"] = entry
        n += 1
    if n:
        log.info("node pool: injected %d research-worker node(s) "
                 "(ONE canonical MiOS Modelfile dispatched per node)", n)
    return n

def _agent_lane(cfg: dict) -> str:
    lane = str(cfg.get("lane", "")).lower().strip()
    if lane in ("cpu", "gpu", "igpu", "accelerator", "mobile"):
        return lane
    ep = str(cfg.get("endpoint", ""))
    mdl = str(cfg.get("model", "")).lower()
    _light_port = os.environ.get("MIOS_PORTS_LLM_LIGHT", "8500")
    _cpu_port = os.environ.get("MIOS_PORTS_CPU_NODE", "8510")
    _daemon_port = os.environ.get("MIOS_PORTS_DAEMON_AGENT", "8740")
    if (":" + _light_port) in ep or "igpu" in mdl:        # iGPU / light lane
        return "igpu"
    if (":" + _daemon_port) in ep or (":" + _cpu_port) in ep or "cpu" in mdl:
        return "cpu"
    return "gpu"

def _render_agent_catalog(registry: dict) -> str:
    if not registry:
        return ""
    lines = [
        "  -- sub-agents (delegate a sub-task via an `agent` node) --",
        "  every agent wields ALL MiOS tools/recipes/skills; pick by the JOB it",
        "  is best at + its compute lane (spread work across lanes), NOT by tools:",
    ]
    for name, cfg in sorted(registry.items()):
        lane = _agent_lane(cfg)
        job = str(cfg.get("job") or "").strip()
        if not job:
            role = str(cfg.get("role", "general"))
            strengths = ", ".join(str(s) for s in (cfg.get("strengths") or []))
            job = role + (f" ({strengths})" if strengths else "")
        lines.append(f"  {name}".ljust(24) + f"[{lane} lane] -- {job}")
    return "\n".join(lines)

def _role_system(aname: str) -> str:
    if not aname:
        return ""
    try:
        with open(os.path.join(_ROLE_SYSTEM_DIR, f"{aname}.md"),
                  "r", encoding="utf-8") as _f:
            return _f.read().strip()
    except (OSError, UnicodeDecodeError):
        return ""

def _dedup_pool_by_target(pool: list) -> list:
    def _rank(a: str) -> int:
        if str(a).startswith("node:"):
            return 0
        return 2 if (_AGENT_REGISTRY.get(a) or {}).get("research_only") else 1
    seen: set = set()
    keep: set = set()
    for a in sorted(pool, key=lambda x: (_rank(x), str(x))):
        c = _AGENT_REGISTRY.get(a) or {}
        try:
            _ep, _mdl = _agent_binding(c, None)
        except Exception:  # noqa: BLE001
            _ep, _mdl = str(c.get("endpoint", "")), str(c.get("model", ""))
        _batching = str(c.get("api", "")).strip().lower() in {"openai", "sglang", "vllm"}
        if _ep and _batching:
            key = ("@name:" + str(a), "")
        elif _ep:
            key = (_endpoint_key(_ep), str(_mdl or ""))
        else:
            key = ("@" + str(a), "")
        if key in seen:
            continue
        seen.add(key)
        keep.add(a)
    out = [a for a in pool if a in keep]   # restore natural order (primary first)
    _eff_w = (mios_hopbudget.effort_width(EFFORT_DEFAULT, base=2, cap=SWARM_MAX_WIDTH)
              if SWARM_MAX_WIDTH > 0 else 0)
    if _eff_w > 0 and len(out) > _eff_w:
        out = out[:_eff_w]
    return out
