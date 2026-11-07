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
        # Resolve beside this module so a checkout cannot import an older
        # installed DB/TOML implementation merely because the host has MiOS.
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
        # Resolve beside this module so a checkout cannot import an older
        # installed DB/TOML implementation merely because the host has MiOS.
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

def _toml_inline(v) -> str:
    """v in TOML inline syntax; arrays nest and may hold inline tables."""
    import datetime
    if isinstance(v, bool):
        return str(v).lower()
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'
    if isinstance(v, (datetime.datetime, datetime.date, datetime.time)):
        return v.isoformat()
    if isinstance(v, list):
        return "[" + ", ".join(_toml_inline(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{quote_key(k)} = {_toml_inline(x)}" for k, x in v.items()) + " }"
    raise TypeError(f"Unsupported list item type: {type(v)}")

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
            lines.append(f"{k_str} = {_toml_inline(v)}")
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
    except Exception:  # noqa: BLE001 -- no ceiling resolved: refuse
        log.warning("Portal configuration safety cap unavailable")
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
    except Exception:
        log.warning("Configuration TOML parse failed")
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
                    f"[ports].{k} must be an integer 1-65535.")
            elif not (1 <= v <= 65535):
                errors.append(
                    f"[ports].{k} is out of the valid 1-65535 range.")

    return (len(errors) == 0, errors)

# What miosd's GET /portal/config serves in place of every secret value.
REDACTED = "<redacted>"

UNRESOLVED_REFUSAL = ("Refusing the save: the current config did not resolve, so it cannot be"
                      " diffed or its <redacted> markers restored; nothing was written")

def portal_post_toml(content_type: str, body: bytes):
    """A POST /portal/config body's TOML: raw, or miosd's lossless JSON envelope
    {"toml": "<text>"}; None for a malformed envelope."""
    if content_type.split(";")[0].strip().lower() != "application/json":
        return body.decode("utf-8")
    import json
    try:
        envelope = json.loads(body.decode("utf-8"))
    except ValueError as e:
        log.warning("Invalid JSON posted to /portal/config: %s", e)
        return None
    toml_text = envelope.get("toml") if isinstance(envelope, dict) else None
    return toml_text if isinstance(toml_text, str) else None

class TiersUnresolved(RuntimeError):
    """The tiers a save diffs against, or restores its REDACTED markers from,
    did not resolve: write_user_config refuses and writes nothing."""

def _ascii_eq(a: str, b: str) -> bool:
    return a.encode("utf-8").lower() == b.encode("utf-8").lower()

def _redact_matches(value, secret_keys) -> None:
    if isinstance(value, dict):
        for k, v in value.items():
            if k == "keys" and isinstance(v, list):
                continue
            if any(_ascii_eq(sk, k) or (sk.startswith("MIOS_") and _ascii_eq(sk[5:], k))
                   for sk in secret_keys):
                value[k] = REDACTED
            else:
                _redact_matches(v, secret_keys)
    elif isinstance(value, list):
        for item in value:
            _redact_matches(item, secret_keys)

def redact_secrets(merged: dict) -> dict:
    """A copy of merged with each secret REDACTED exactly as miosd's GET does."""
    import copy
    out = copy.deepcopy(merged)
    for table, key in (("portal", "password"), ("portal", "secret"), ("ai", "api_key")):
        if isinstance(out.get(table), dict) and key in out[table]:
            out[table][key] = REDACTED
    security = out.get("security")
    secret_keys = security.get("secret_keys") if isinstance(security, dict) else None
    keys = secret_keys.get("keys") if isinstance(secret_keys, dict) else None
    _redact_matches(out, [k for k in keys if isinstance(k, str)] if isinstance(keys, list) else [])
    return out

def has_redacted(value) -> bool:
    """True when value holds a REDACTED marker anywhere."""
    if isinstance(value, dict):
        return any(has_redacted(v) for v in value.values())
    if isinstance(value, list):
        return any(has_redacted(v) for v in value)
    return isinstance(value, str) and value == REDACTED

REDACTED_ARRAY_REFUSAL = (
    "Refusing the save: a redacted array was edited or cannot be matched to its "
    "destination layer. Reload or supply explicit values; nothing was written.")

class AmbiguousRedactedArray(ValueError):
    """An opaque array edit cannot identify the secrets it would restore."""

def _same_config_value(left, right) -> bool:
    """TOML equality without Python's bool/int or int/float coercions."""
    if False:
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            _same_config_value(value, right[key]) for key, value in left.items())
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _same_config_value(a, b) for a, b in zip(left, right))
    return left == right

def validate_redacted_arrays(posted, redacted_prefix) -> None:
    """Opaque arrays must be an unchanged echo of the destination prefix.

    Matching indexes cannot identify a secret after an insertion, reorder or
    edit. Compare the complete redacted array before the old restoration walk,
    including outer arrays with nested markers. Explicit marker-free arrays
    remain ordinary edits. This check neither mutates nor reveals either input.
    """
    if isinstance(posted, list) and has_redacted(posted):
        if not isinstance(redacted_prefix, list) or not _same_config_value(posted, redacted_prefix):
            raise AmbiguousRedactedArray(REDACTED_ARRAY_REFUSAL)
    elif isinstance(posted, dict):
        for key, value in posted.items():
            prefix = redacted_prefix.get(key) if isinstance(redacted_prefix, dict) else None
            validate_redacted_arrays(value, prefix)

def restore_redacted(posted, live):
    """Each REDACTED marker takes back the live value at its path, or is dropped
    where live has none. The input is not mutated."""
    if isinstance(posted, dict):
        out = {}
        for k, v in posted.items():
            has_live = isinstance(live, dict) and k in live
            if isinstance(v, str) and v == REDACTED:
                if has_live:
                    out[k] = live[k]
                continue
            out[k] = restore_redacted(v, live[k] if has_live else None)
        return out
    if isinstance(posted, list):
        out = []
        for i, item in enumerate(posted):
            has_live = isinstance(live, list) and i < len(live)
            if isinstance(item, str) and item == REDACTED:
                if has_live:
                    out.append(live[i])
                continue
            out.append(restore_redacted(item, live[i] if has_live else None))
        return out
    return posted

def _unparsed_layers(paths) -> list:
    """The layer files in paths that exist but do not parse as TOML."""
    try:
        import tomllib as _toml
    except ImportError:
        import tomli as _toml  # type: ignore
    bad = []
    for p in paths:
        if p and os.path.isfile(p):
            try:
                with open(p, "rb") as fh:
                    _toml.load(fh)
            except Exception:  # noqa: BLE001 -- any unreadable layer is unresolved
                bad.append(p)
    return bad

def write_user_config(cfg: dict, dest_path: str = None, live_config: dict = None) -> None:
    """Atomically write what cfg changes over vendor..host.d to the user tier.

    cfg's REDACTED markers restore from vendor..host.d plus that user tier, so a
    secret stays in its own layer. TiersUnresolved, writing nothing: a tier does
    not parse, or live_config (the handler's merged load) is None."""
    import sys
    if "/usr/lib/mios" not in sys.path:
        sys.path.insert(0, "/usr/lib/mios")
    base_cfg = {}
    try:
        import mios_toml
        vendor, vendor_d, host, host_d, user, user_d = mios_toml._tier_dirs()
        paths = ([vendor] + mios_toml._frags(vendor_d)
                 + [host] + mios_toml._frags(host_d))
        if _unparsed_layers(paths):
            raise TiersUnresolved("the lower tiers did not parse; nothing was written")
        for p in paths:
            mios_toml.deep_merge(base_cfg, mios_toml._load_one(p))
        # The posted config carries [ports] derived from [ports.categories]
        # (GET serves load_merged); derive the base the same way so a derived
        # value is not frozen into the user tier. miosd's save does the same.
        mios_toml.derive_ports(base_cfg)
    except Exception:  # noqa: BLE001 -- an empty base would copy every tier upward
        raise TiersUnresolved("the tiers below the user tier did not resolve; nothing was written") from None
    if dest_path is None:
        dest_path = user
    if has_redacted(cfg):
        unparsed = _unparsed_layers(paths + [user, dest_path] + mios_toml._frags(user_d))
        if live_config is None or unparsed:
            raise TiersUnresolved("the save carries REDACTED markers but the live config"
                                  " did not resolve; nothing was written")
        own = {}
        for p in paths + [dest_path]:
            mios_toml.deep_merge(own, mios_toml._load_one(p))
        validate_redacted_arrays(cfg, redact_secrets(own))
        cfg = restore_redacted(cfg, own)

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
