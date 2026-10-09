# AI-hint: Pure config-constant + SSOT-reader layer extracted from server.py (refactor WS R1).
# AI-doc: usr/share/doc/mios/manual/kernel.md

from __future__ import annotations

import logging
import os

log = logging.getLogger("mios-agent-pipe")

def _toml_section(section: str) -> dict:
    """Layered <section> table from mios.toml (vendor <- /etc <- ~/.config)
    or from PostgreSQL config tables (behind the db_authoritative sentinel)."""
    try:
        import sys
        lib_path = "/usr/lib/mios"
        if not os.path.exists(lib_path):
            lib_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        if lib_path not in sys.path:
            sys.path.insert(0, lib_path)
        import mios_db_config
        out = mios_db_config.section(None, section) or {}
    except Exception as e:
        log.warning("Failed to load overlay config section %s: %s", section, e)
        out = {}

    def _xpand(v):
        if isinstance(v, str):
            return os.path.expandvars(v) if "$" in v else v
        if isinstance(v, dict):
            return {k: _xpand(x) for k, x in v.items()}
        if isinstance(v, list):
            return [_xpand(x) for x in v]
        return v
    return _xpand(out)

def _cfg_num(table: dict, env: str, key: str, default, cast=int):
    """Resolve a numeric tunable: env override -> table[key] -> literal default.
    Preserves a legit 0 (unlike a bare `or` chain)."""
    v = os.environ.get(env)
    if v not in (None, ""):
        try:
            return cast(v)
        except (ValueError, TypeError):
            pass
    v = table.get(key)
    if v is not None:
        try:
            return cast(v)
        except (ValueError, TypeError):
            pass
    return default

def _dispatch_toml() -> dict:
    """Layered [dispatch] table from mios.toml (vendor <- /etc <- ~/.config)
    or from PostgreSQL config tables (behind the db_authoritative sentinel)."""
    try:
        import sys
        lib_path = "/usr/lib/mios"
        if not os.path.exists(lib_path):
            lib_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        if lib_path not in sys.path:
            sys.path.insert(0, lib_path)
        import mios_db_config
        return mios_db_config.section(None, "dispatch") or {}
    except Exception as e:
        log.warning("Failed to load overlay config for dispatch: %s", e)
        return {}

_DISPATCH_TOML = _dispatch_toml()

def _dispatch_num(env: str, key: str, default, cast=int):
    """env override -> mios.toml [dispatch].<key> -> default; keeps a legitimate 0."""
    return _cfg_num(_DISPATCH_TOML, env, key, default, cast)

PORT = int(os.environ.get("MIOS_PORTS_AGENT_PIPE", "8700"))
# MIOS_PORTS_MCP to the same value) then the [ports].mcp SSOT table. NO literal
MCP_SERVER_PORT = _cfg_num(_toml_section("ports"), "MIOS_PORTS_MCP", "mcp", None)
_LIGHT_BASE = "http://localhost:" + (os.environ.get("MIOS_PORTS_LLM_LIGHT") or "8500")
BACKEND = (os.environ.get("MIOS_AGENT_PIPE_BACKEND")
           or (_LIGHT_BASE + "/v1"
               if (os.environ.get("MIOS_AGENT_PIPE_BACKEND_LIGHT") or "").strip().lower()
                  in {"1", "true", "yes", "on"}
               else f"http://localhost:{os.environ.get('MIOS_PORTS_HERMES', '8720')}/v1")).rstrip("/")
_BACKEND_IS_LIGHT = (
    (os.environ.get("MIOS_AGENT_PIPE_BACKEND_LIGHT") or "").strip().lower()
    in {"1", "true", "yes", "on"}
    and not (os.environ.get("MIOS_AGENT_PIPE_BACKEND") or "").strip())
BACKEND_MODEL = (os.environ.get("MIOS_AGENT_PIPE_BACKEND_MODEL")
                 or os.environ.get("MIOS_AI_MODEL")   # WS-0B: ONE owned key = [ai].model
                 or "hermes-agent")
_BACKEND_HOSTPORT = BACKEND.split("://")[-1].split("/")[0]
# MIOS_AGENT_PIPE_BACKEND is repointed at a keyless local lane (mios-llm-light on
_HERMES_ENDPOINT = (os.environ.get("MIOS_HERMES_ENDPOINT")
                    or _toml_section("hermes").get("endpoint")
                    or f"http://localhost:{os.environ.get('MIOS_PORTS_HERMES', '8720')}/v1").rstrip("/")
_HERMES_WORKER_ENDPOINT = (os.environ.get("MIOS_AGENTS_HERMES_ENDPOINT")
                           or _toml_section("agents").get("hermes", {}).get("endpoint")
                           or f"http://localhost:{os.environ.get('MIOS_PORTS_HERMES', '8720')}/v1").rstrip("/")
_AUTH_HOSTPORTS = {
    _BACKEND_HOSTPORT,
    _HERMES_ENDPOINT.split("://")[-1].split("/")[0],
    _HERMES_WORKER_ENDPOINT.split("://")[-1].split("/")[0],
}

_AGENT_AUTH_BY_HOSTPORT: dict = {}

CLIENT_TOOLS_PASSTHROUGH = os.environ.get(
    "MIOS_AGENT_PIPE_CLIENT_TOOLS_PASSTHROUGH", "true"
).strip().lower() in {"1", "true", "yes", "on"}
_TOOL_BACKEND = os.environ.get(
    "MIOS_AGENT_PIPE_TOOL_BACKEND", _LIGHT_BASE + "/v1").rstrip("/")
_TOOL_BACKEND_MODEL = os.environ.get(
    "MIOS_AGENT_PIPE_TOOL_BACKEND_MODEL", "granite4.1:8b")
_TOOL_BACKEND_HEAVY = (os.environ.get("MIOS_AGENT_PIPE_TOOL_BACKEND_HEAVY")
                       or _toml_section("nodes").get("local-heavy", {}).get("endpoint")
                       or f"http://localhost:{os.environ.get('MIOS_PORTS_LLM_HEAVY', '8520')}/v1").rstrip("/")
_TOOL_BACKEND_HEAVY_MODEL = os.environ.get(
    "MIOS_AGENT_PIPE_TOOL_BACKEND_HEAVY_MODEL", "mios-heavy")
_HEAVY_PROBE_TTL = float(os.environ.get("MIOS_AGENT_PIPE_HEAVY_PROBE_TTL", "30"))

_INGRESS_KEY = os.environ.get("MIOS_AGENT_PIPE_INGRESS_KEY", "").strip()

# MIOS_STACK_MODEL; embeddings + vision keep their own. Every reasoning stage
_STACK_MODEL = (os.environ.get("MIOS_STACK_MODEL")        # explicit per-deploy override
                or os.environ.get("MIOS_AI_MODEL")        # WS-0B: ONE owned key = [ai].model (install.env)
                or "granite4.1:8b")  # served brain on :8500 (gemma4:12b retired -> 404;)
_MICRO_MODEL = (os.environ.get("MIOS_MICRO_MODEL")
                or _toml_section("ai").get("micro_model")
                or _STACK_MODEL)
_MICRO_ENDPOINT = (os.environ.get("MIOS_MICRO_ENDPOINT")
                   or _toml_section("ai").get("micro_endpoint")
                   or _LIGHT_BASE + "/v1").rstrip("/")
_MICRO_BASE = (_MICRO_ENDPOINT[:-3].rstrip("/")
               if _MICRO_ENDPOINT.endswith("/v1") else _MICRO_ENDPOINT)

_LIGHT_LANE = os.environ.get("MIOS_LLM_CPU_ENDPOINT",
                             _LIGHT_BASE).rstrip("/")  # mios-llm-light (WS-0B: one owned port key)
# TLS verification on reachability probes; insecure is opt-in. Probes degrade
# open, so an unverifiable peer reads as down, never as a crash.
PROBE_VERIFY_TLS = os.environ.get(
    "MIOS_SECURITY_PROBE_VERIFY_TLS", "true").lower() not in {"false", "0", "no"}
ROUTER_ENABLED = os.environ.get("MIOS_AGENT_PIPE_ROUTER_ENABLED",
                                "true").lower() not in {"false", "0", "no"}
ROUTER_MODEL = os.environ.get("MIOS_AGENT_PIPE_ROUTER_MODEL", _MICRO_MODEL)
ROUTER_ENDPOINT = os.environ.get(
    "MIOS_AGENT_PIPE_ROUTER_ENDPOINT", _LIGHT_LANE
).rstrip("/")
ROUTER_TIMEOUT_S = int(os.environ.get("MIOS_AGENT_PIPE_ROUTER_TIMEOUT_S", "30"))
ROUTER_MAX_TOKENS = int(os.environ.get("MIOS_AGENT_PIPE_ROUTER_MAX_TOKENS", "200"))

PLANNER_ENABLED = os.environ.get(
    "MIOS_AGENT_PIPE_PLANNER_ENABLED", "true",
).lower() not in {"false", "0", "no"}
PLANNER_MODEL = os.environ.get(
    "MIOS_AGENT_PIPE_PLANNER_MODEL", _STACK_MODEL,   # gemma4:12b entire-stack
)
PLANNER_ENDPOINT = os.environ.get(
    "MIOS_AGENT_PIPE_PLANNER_ENDPOINT", _LIGHT_BASE,
).rstrip("/")
PLANNER_TIMEOUT_S = int(os.environ.get(
    "MIOS_AGENT_PIPE_PLANNER_TIMEOUT_S", "30"))
PLANNER_MAX_TOKENS = int(os.environ.get(
    "MIOS_AGENT_PIPE_PLANNER_MAX_TOKENS", "1536"))
PLANNER_MAX_NODES = int(os.environ.get(
    "MIOS_AGENT_PIPE_PLANNER_MAX_NODES", "8"))
PLANNER_REFLEXION_CAP = int(os.environ.get(
    "MIOS_AGENT_PIPE_PLANNER_REFLEXION_CAP", "2"))

_ROUTER_SYSTEM = (
    "You are the MiOS router (Agentic-OS layer 1). Classify the "
    "user prompt into ONE of three actions and emit JSON ONLY.\n"
    "\n"
    "Actions:\n"
    '  "dispatch": the prompt maps to ONE MiOS verb call. Emit\n'
    '              {"action":"dispatch","tool":"<name>",'
    '"args":{...},"reason":"<short>"}\n'
    '  "chat":     conversational (greeting/thanks/question with\n'
    '              no system effect). Emit\n'
    '              {"action":"chat","reply":"<your reply>"}\n'
    '  "agent":    multi-step / research / unclear / needs\n'
    '              several tools. Emit\n'
    '              {"action":"agent","reason":"<short>"}\n'
    "\n"
    "Rules:\n"
    "- `dispatch` only when ONE verb solves it.\n"
    "- A WRITE verb is the right pick whenever the user asks for a\n"
    "  system effect (open/close/focus/move). NEVER pick a READ verb\n"
    "  when the user clearly wants an effect.\n"
    "- Position defaults to \"default\" (golden+16:10 centered); set\n"
    "  explicitly only when the user named a side.\n"
    "- `chat` for greetings, thanks, one-sentence clarification.\n"
    "  REASON -> PLAN -> DELEGATE meta-rule: an 'open / find / install /\n"
    "  launch / use X' intent NEVER routes to `chat`. The downstream\n"
    "  agent has to fan out across discovery surfaces before deciding\n"
    "  if X exists -- pick `agent` so it can do that. Refusing as 'not\n"
    "  installed' without running any probe first is a defect.\n"
    "- `agent` for N>1 tools, web research, install, file editing,\n"
    "  general knowledge questions, conversational follow-through,\n"
    "  ANY 'open / find / install / launch / use' intent.\n"
    "  MiOS-Agent is both an Agentic-OS AND a generalized AI agent.\n"
    "- Write `reply` fields in ENGLISH by default; use another language\n"
    "  only if the user's own message is clearly written in it.\n"
    "- Output JSON ONLY -- no preamble, no markdown, no commentary."
)

REFINE_ENABLED = os.environ.get(
    "MIOS_REFINE_ENABLE", "true",
).lower() not in {"false", "0", "no"}
REFINE_MODEL = os.environ.get("MIOS_REFINE_MODEL", _STACK_MODEL)
REFINE_ENDPOINT = os.environ.get(
    "MIOS_REFINE_ENDPOINT", _LIGHT_BASE,  # mios-llm-light (WS-0B: one owned port key)
).rstrip("/")
REFINE_TIMEOUT_S = int(os.environ.get("MIOS_REFINE_TIMEOUT_SECONDS", "30"))
REFINE_ATTEMPTS = int(os.environ.get("MIOS_REFINE_ATTEMPTS", "2"))
REFINE_MAX_TOKENS = int(os.environ.get("MIOS_REFINE_MAX_TOKENS", "700"))
REFINE_BYPASS_CHARS = int(os.environ.get("MIOS_REFINE_BYPASS_CHARS", "24"))
REFINE_KEEP_ALIVE = os.environ.get("MIOS_REFINE_KEEP_ALIVE", "30m")

_REFLECT_TOML = _toml_section("reflect") or {}
JUDGE_EXAMPLES = os.environ.get(
    "MIOS_REFLECT_JUDGE_EXAMPLES",
    _REFLECT_TOML.get("judge_examples", "a punt, refusal, 'I cannot', or 'where to look'")
)

POLISH_ENABLED = os.environ.get(
    "MIOS_POLISH_ENABLE", "true",
).lower() not in {"false", "0", "no"}
POLISH_MODEL = os.environ.get("MIOS_POLISH_MODEL", _STACK_MODEL)
POLISH_ENDPOINT = os.environ.get(
    "MIOS_POLISH_ENDPOINT", _LIGHT_BASE,  # mios-llm-light (WS-0B: one owned port key)
).rstrip("/")
POLISH_TIMEOUT_S = int(os.environ.get("MIOS_POLISH_TIMEOUT_SECONDS", "15"))
POLISH_MAX_TOKENS = int(os.environ.get("MIOS_POLISH_MAX_TOKENS", "800"))

_COUNCIL_TOML = _toml_section("agent_pipe.council") or _toml_section("council") or {}

def _cfg_bool(table: dict, env: str, key: str, default: bool) -> bool:
    """Resolve a boolean tunable: env override -> table[key] -> literal default.
    Truthy set matches the rest of the config layer (1/true/yes/on)."""
    v = os.environ.get(env)
    if v not in (None, ""):
        return v.strip().lower() in {"1", "true", "yes", "on"}
    v = table.get(key)
    if v is not None:
        return str(v).strip().lower() in {"1", "true", "yes", "on"}
    return default

COUNCIL_DIVERSITY_GATE = _cfg_bool(
    _COUNCIL_TOML, "MIOS_COUNCIL_DIVERSITY_GATE", "diversity_gate", False)
COUNCIL_DIVERSITY_THRESHOLD = _cfg_num(
    _COUNCIL_TOML, "MIOS_COUNCIL_DIVERSITY_THRESHOLD", "diversity_threshold",
    0.92, cast=float)
COUNCIL_AGGREGATOR_BYPASS = _cfg_bool(
    _COUNCIL_TOML, "MIOS_COUNCIL_AGGREGATOR_BYPASS", "aggregator_bypass", False)
COUNCIL_AGGREGATOR_BYPASS_THRESHOLD = _cfg_num(
    _COUNCIL_TOML, "MIOS_COUNCIL_AGGREGATOR_BYPASS_THRESHOLD",
    "aggregator_bypass_threshold", 0.95, cast=float)

_DRIFT_MONITOR_TOML = _toml_section("drift_monitor") or {}
DRIFT_MONITOR_ENABLED = _cfg_bool(
    _DRIFT_MONITOR_TOML, "MIOS_DRIFT_MONITOR_ENABLE", "enable", False)
DRIFT_MONITOR_THRESHOLD = _cfg_num(
    _DRIFT_MONITOR_TOML, "MIOS_DRIFT_MONITOR_THRESHOLD", "threshold", 0.2, cast=float)
DRIFT_MONITOR_WINDOW = int(_cfg_num(
    _DRIFT_MONITOR_TOML, "MIOS_DRIFT_MONITOR_WINDOW", "window", 200, cast=float))
DRIFT_MONITOR_MIN_SAMPLES = int(_cfg_num(
    _DRIFT_MONITOR_TOML, "MIOS_DRIFT_MONITOR_MIN_SAMPLES", "min_samples", 30, cast=float))
DRIFT_MONITOR_AXES = [str(a) for a in (_DRIFT_MONITOR_TOML.get("axes") or [])]

_CONSENSUS_TOML = _toml_section("consensus") or {}
CONSENSUS_ENABLED = _cfg_bool(
    _CONSENSUS_TOML, "MIOS_CONSENSUS_ENABLE", "enable", False)
CONSENSUS_THRESHOLD = _cfg_num(
    _CONSENSUS_TOML, "MIOS_CONSENSUS_THRESHOLD", "threshold", 0.5, cast=float)
CONSENSUS_MIN_LANES = int(_cfg_num(
    _CONSENSUS_TOML, "MIOS_CONSENSUS_MIN_LANES", "min_lanes", 2, cast=float))
CONSENSUS_TIMEOUT_S = _cfg_num(
    _CONSENSUS_TOML, "MIOS_CONSENSUS_TIMEOUT_S", "timeout_s", 20.0, cast=float)
CONSENSUS_WEIGHT_FLOOR = _cfg_num(
    _CONSENSUS_TOML, "MIOS_CONSENSUS_WEIGHT_FLOOR", "weight_floor", 0.1, cast=float)
CONSENSUS_RRF_K = int(_cfg_num(
    _CONSENSUS_TOML, "MIOS_CONSENSUS_RRF_K", "rrf_k", 60, cast=float))
# Each lane is {name, endpoint, model, weight}; endpoint/model empty means
# "the refine lane", so a panel can be declared without repeating its address.
CONSENSUS_LANES = [d for d in (_CONSENSUS_TOML.get("lanes") or []) if isinstance(d, dict)]

_PGVECTOR_TOML = _toml_section("pgvector") or {}
# Postgres is the agent plane's SOLE datastore since the WS-A3 cutover, so
# "enabled and backed by postgres" IS "primary". These were referenced from
# server.py without ever being defined -- see manual ch54.
_PG_ENABLED = _cfg_bool(_PGVECTOR_TOML, "MIOS_PGVECTOR_ENABLE", "enable", True)
_PG_PRIMARY = _PG_ENABLED and str(
    os.environ.get("MIOS_PG_BACKEND")
    or _PGVECTOR_TOML.get("db_backend", "postgres")).strip().lower() == "postgres"

_MEMORY_TOML = _toml_section("memory") or {}
MEMORY_CONSOLIDATE_ENABLED = _cfg_bool(
    _MEMORY_TOML, "MIOS_MEMORY_CONSOLIDATE", "consolidate", True)
MEMORY_CONSOLIDATE_INTERVAL_S = int(_cfg_num(
    _MEMORY_TOML, "MIOS_MEMORY_CONSOLIDATE_INTERVAL_S",
    "consolidate_interval_s", 3600, cast=float))
MEMORY_CONSOLIDATE_MAX_GROUPS = int(_cfg_num(
    _MEMORY_TOML, "MIOS_MEMORY_CONSOLIDATE_MAX_GROUPS",
    "consolidate_max_groups", 200, cast=float))
KV_SLOT_PERSIST = (
    os.environ.get("MIOS_KV_SLOT_PERSIST", "").strip().lower() in {"1", "true", "yes"}
    or str(_MEMORY_TOML.get("kv_slot_persist", "true")).strip().lower() in {"1", "true", "yes", "on"}
)

def quote_key(k: str) -> str:
    import re
    if re.match(r"^[A-Za-z0-9_-]+$", k):
        return k
    escaped = k.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'

def to_toml(d: dict, prefix: list = None) -> str:
    import datetime
    if prefix is None:
        prefix = []

    lines = []

    for k, v in sorted(d.items()):
        if isinstance(v, dict):
            continue
        if isinstance(v, list) and len(v) > 0 and isinstance(v[0], dict):
            continue

        k_str = quote_key(k)
        if isinstance(v, bool):
            lines.append(f"{k_str} = {str(v).lower()}")
        elif isinstance(v, (int, float)):
            lines.append(f"{k_str} = {v}")
        elif isinstance(v, str):
            escaped = v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
            lines.append(f'{k_str} = "{escaped}"')
        elif isinstance(v, (datetime.datetime, datetime.date, datetime.time)):
            lines.append(f"{k_str} = {v.isoformat()}")
        elif isinstance(v, list):
            list_str = []
            for item in v:
                if isinstance(item, str):
                    escaped_item = item.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
                    list_str.append(f'"{escaped_item}"')
                elif isinstance(item, bool):
                    list_str.append(str(item).lower())
                elif isinstance(item, (int, float)):
                    list_str.append(str(item))
                elif isinstance(item, (datetime.datetime, datetime.date, datetime.time)):
                    list_str.append(item.isoformat())
                else:
                    raise TypeError(f"Unsupported list item type: {type(item)}")
            lines.append(f"{k_str} = [{', '.join(list_str)}]")
        elif v is None:
            continue
        else:
            raise TypeError(f"Unsupported TOML type: {type(v)}")

    for k, v in sorted(d.items()):
        if isinstance(v, dict):
            new_prefix = prefix + [k]
            sect_name = ".".join(quote_key(p) for p in new_prefix)
            lines.append(f"\n[{sect_name}]")
            lines.append(to_toml(v, new_prefix))

    for k, v in sorted(d.items()):
        if isinstance(v, list) and len(v) > 0 and isinstance(v[0], dict):
            new_prefix = prefix + [k]
            sect_name = ".".join(quote_key(p) for p in new_prefix)
            for item in v:
                lines.append(f"\n[[{sect_name}]]")
                lines.append(to_toml(item, new_prefix))

    return "\n".join(lines)

def _validate_max_bytes() -> int:
    """[portal].config_max_body_bytes from the layered mios.toml -- the same
    ceiling miosd's config server enforces. Unresolvable raises (fail closed)."""
    import sys
    here = os.path.dirname(os.path.abspath(__file__))
    for d in ("/usr/lib/mios", os.path.normpath(os.path.join(here, "..", "..", ".."))):
        if os.path.isfile(os.path.join(d, "mios_toml.py")) and d not in sys.path:
            sys.path.insert(0, d)
    import mios_toml
    return int(mios_toml.get("portal", "config_max_body_bytes"))

_VALIDATE_CRITICAL_SECTIONS = ("identity", "ports")

def validate_config(toml_text: str, live_config: dict = None):
    errors: list = []

    try:
        size = len(toml_text.encode("utf-8"))
    except Exception:
        size = len(toml_text or "")
    try:
        max_bytes = _validate_max_bytes()
    except Exception as e:  # noqa: BLE001 -- no ceiling resolved: refuse
        log.warning("Portal configuration safety cap unavailable: %s", e)
        return (False, ["[portal].config_max_body_bytes did not resolve"])
    if size > max_bytes:
        return (False, [f"Config too large: {size} bytes exceeds the "
                        f"{max_bytes}-byte safety cap ([portal].config_max_body_bytes)."])

    try:
        import tomllib as _toml
    except ImportError:
        import tomli as _toml  # type: ignore
    try:
        parsed = _toml.loads(toml_text)
    except Exception as e:
        log.warning("Configuration TOML parse failed: %s", e)
        return (False, ["Invalid TOML syntax"])

    live = live_config if isinstance(live_config, dict) else {}
    for sec in _VALIDATE_CRITICAL_SECTIONS:
        live_sec = live.get(sec)
        if isinstance(live_sec, dict) and live_sec:
            new_sec = parsed.get(sec)
            if not isinstance(new_sec, dict) or not new_sec:
                errors.append(
                    f"Refusing to drop critical [{sec}] section -- it is "
                    f"present in the live config and losing it bricks the deploy.")

    identity = parsed.get("identity")
    if isinstance(identity, dict) and "mios_user" in identity:
        mu = identity.get("mios_user")
        if not isinstance(mu, str) or not mu.strip():
            errors.append("[identity].mios_user must be a non-empty string.")

    ports = parsed.get("ports")
    if isinstance(ports, dict):
        for k, v in ports.items():
            if k == "stack_id":
                continue
            if isinstance(v, (dict, list)):
                continue  # nested table -- not a port scalar; leave it alone
            if isinstance(v, bool) or not isinstance(v, int):
                errors.append(
                    f"[ports].{k} must be an integer 1-65535 (got {v!r}).")
            elif not (1 <= v <= 65535):
                errors.append(
                    f"[ports].{k} = {v} is out of the valid 1-65535 range.")

    return (len(errors) == 0, errors)

def write_user_config(cfg: dict, dest_path: str = None) -> None:
    """Atomically write the dictionary to the user-layer config file."""
    base_cfg = {}
    try:
        import sys
        if "/usr/lib/mios" not in sys.path:
            sys.path.insert(0, "/usr/lib/mios")
        import mios_toml
        vendor, vendor_d, host, host_d, user, user_d = mios_toml._tier_dirs()
        paths = ([vendor] + mios_toml._frags(vendor_d)
                 + [host] + mios_toml._frags(host_d))
        for p in paths:
            mios_toml.deep_merge(base_cfg, mios_toml._load_one(p))
        # The posted config carries [ports] derived from [ports.categories]
        # (GET serves load_merged); derive the base the same way so a derived
        # value is not frozen into the user tier. miosd's save does the same.
        mios_toml.derive_ports(base_cfg)
    except Exception:
        pass

    def _dict_diff(new_dict: dict, base_dict: dict) -> dict:
        diff = {}
        for k, v in new_dict.items():
            if k not in base_dict:
                diff[k] = v
            elif isinstance(v, dict) and isinstance(base_dict[k], dict):
                sub_diff = _dict_diff(v, base_dict[k])
                if sub_diff:
                    diff[k] = sub_diff
            elif v != base_dict[k]:
                diff[k] = v
        return diff

    delta_cfg = _dict_diff(cfg, base_cfg)

    if dest_path is None:
        try:
            import mios_toml
            dest_path = mios_toml.USER
        except Exception:
            dest_path = os.path.expanduser("~/.config/mios/mios.toml")

    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    temp_path = dest_path + ".tmp"
    toml_str = to_toml(delta_cfg)
    with open(temp_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(toml_str)
    os.replace(temp_path, dest_path)

    try:
        import mios_toml
        mios_toml.clear_cache()
    except Exception:
        pass
    try:
        import mios_db_config
        mios_db_config.clear_cache()
    except Exception:
        pass
