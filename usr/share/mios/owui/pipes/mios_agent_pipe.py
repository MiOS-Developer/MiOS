# AI-hint: The primary OWUI entry point for the MiOS-Agent, managing the proxy-to-hermes stream, real-time event tailing from mios-hermes-tail, and wrapping ...
# AI-doc: usr/share/doc/mios/manual/owui.md
"""
title: MiOS AI
author: MiOS
version: 1.2.0
description: |
  Consolidated MiOS systems agent. The canonical user-facing entry
  point in OWUI. Owns three concerns end-to-end so they always fire
  regardless of OWUI's routing decisions (the global Filter chain
  can be bypassed when a Pipe takes over):

    1. PROXY -> prefilter -> hermes ([ports] prefilter/hermes).
       Streams SSE
       chunks back to the operator as fast as they arrive. Unbounded
       sock_read so long CPU-bound tool turns don't TransferEncoding-
       Error the response.

    2. LIVE EMITS. Polls /var/lib/mios/hermes-tail/latest.json (the
       root-tail bridge populated by mios-hermes-tail.service) on a
       background task while the stream is open, and pushes each
       new event through __event_emitter__ so the operator SEES
       "calling terminal: mios-find beamng" / "max retries hit" in
       real time -- not after the chat completes.

    3. NARRATION COLLAPSE. Buffers content until a line boundary,
       routes each whole line through _is_narration_line() and wraps
       narration in <think>...</think> so OWUI renders it inside its
       native collapsible Thinking widget. Final-answer lines pass
       through verbatim. Operator directive "ALL THESE
       MIOS-HERMES AGENTS THINKING PRINTS COULD BE EMMITED AND/OR
       COLLAPSABLE AS THINKING IN OWUI".

  BACKEND_URL resolves from MIOS_AI_ENDPOINT (Law 5), so it tracks
  [ports].agent_pipe. The container-internal address
  (host.containers.internal) was a leftover from the container-era
  Quadlet -- OWUI runs as a host process now, so it never resolved.
"""

from pydantic import BaseModel, Field
import json
import os
import re
import shlex
import asyncio
import time
from typing import Any, AsyncGenerator, Awaitable, Callable, Optional

import aiohttp

NARRATION_LEADERS = [
    r"^let me\b", r"^let.s\b", r"^i.ll\b", r"^i.m going to\b",
    r"^i.m about to\b", r"^i need to\b", r"^i.ll need to\b",
    r"^first,?\s*i\b", r"^next,?\s*i\b", r"^now,?\s*i\b",
    r"^i.ve (loaded|updated|checked|verified|noted)\b",
    r"^i.?ll try (a different|another) approach\b",
    r"^i.?ll take a (simpler|different) approach\b",
    r"^i.?ll approach this (differently|another way)\b",
    r"^based on the available tools\b",
    r"^i need to analyze\b", r"^let me analyze\b",
    r"^i should\b", r"^i will now\b",
    r"^(thinking|reasoning):\s",
]
NARRATION_RES = [re.compile(p, re.IGNORECASE) for p in NARRATION_LEADERS]

def _is_narration_line(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    for pat in NARRATION_RES:
        if pat.search(s):
            return True
    return False

HERMES_TAIL_PATH = "/var/lib/mios/hermes-tail/latest.json"
TAIL_POLL_INTERVAL_S = 0.4

_TAIL_ICONS = {
    "max_retries":    "❌",
    "invalid_tool":   "⚠️",
    "retry":          "↻",
    "delegate_spawn": "🚀",
    "synthesis":      "🔀",
    "subagent_done":  "✓",
    "tool_call":      "🛠️ ",
}

import base64 as _base64
import urllib.parse as _urlparse  # noqa: F401  (reserved for record-id quoting)

# Empty by DEFAULT. These rows (session / tool_call / event) are written by
# agent-pipe, which this pipe proxies through -- the copies below survived the
# extraction and addressed the DECOMMISSIONED datastore, so they were not
# "un-mirrored writes", they were writes to nothing. Set MIOS_DB_URL to
# re-enable them against a live legacy endpoint.
_DB_URL  = os.environ.get("MIOS_DB_URL", "")
_DB_USER = os.environ.get("MIOS_DB_USER", "root")
_DB_PASS = os.environ.get("MIOS_DB_PASS", "root")
_DB_NS   = os.environ.get("MIOS_DB_NS",   "mios")
_DB_DB   = os.environ.get("MIOS_DB_DB",   "mios")
_DB_AUTH = "Basic " + _base64.b64encode(f"{_DB_USER}:{_DB_PASS}".encode()).decode()
_DB_DOWN_UNTIL: float = 0.0

async def _db_post(sql: str, *, timeout: float = 3.0) -> Optional[list]:
    """Best-effort retired database write/query. Returns the parsed list of
    per-statement results, or None on any error. A 30s backoff after
    each failure prevents per-turn retry storms when the DB is down."""
    global _DB_DOWN_UNTIL
    if not _DB_URL:
        return None
    if not sql or not sql.strip():
        return None
    if time.time() < _DB_DOWN_UNTIL:
        return None
    body = (f"USE NS {_DB_NS} DB {_DB_DB}; " + sql).encode()
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as s:
            async with s.post(
                f"{_DB_URL}/sql",
                data=body,
                headers={
                    "Authorization": _DB_AUTH,
                    "Accept": "application/json",
                },
            ) as r:
                if r.status != 200:
                    _DB_DOWN_UNTIL = time.time() + 30
                    return None
                return await r.json()
    except Exception:
        _DB_DOWN_UNTIL = time.time() + 30
        return None

def _db_create(table: str, fields: dict, *,
               now_fields: tuple = (),
               extra: str = "") -> str:
    """Build a `CREATE <table> SET ... [<extra>];` statement.

    The retired database rejected plain ISO-Z strings for fields with TYPE
    datetime ("Expected datetime but found '...'"). The canonical
    pattern is `field = time::now()` literal. now_fields lists the
    keys to assign via time::now(); all OTHER keys go through
    json.dumps which yields valid legacy-dialect literals for strings, numbers,
    bools, arrays, and nested objects (JSON-object syntax IS
    the legacy object syntax).

    extra is appended verbatim after the SET list (e.g. "RETURN id")."""
    parts = [f"{k} = time::now()" for k in now_fields]
    for k, v in fields.items():
        if k in now_fields or v is None:
            continue
        parts.append(f"{k} = {json.dumps(v, default=str)}")
    sql = f"CREATE {table} SET " + ", ".join(parts)
    if extra:
        sql += " " + extra
    return sql + ";"

def _db_fire(coro: Awaitable) -> None:
    """Schedule a DB coroutine without blocking the caller. Silently
    no-ops outside an active event loop (callers may invoke from sync
    helpers; in those cases the write is simply dropped)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(coro)

_ENV_SENTINELS = frozenset({"", "unknown", "none", "null", "n/a", "undefined"})

def _env_ok(v) -> bool:
    """True when v is a REAL env value (non-empty, not an absent-value sentinel)."""
    s = str(v if v is not None else "").strip()
    return bool(s) and s.lower() not in _ENV_SENTINELS

class Pipe:
    class Valves(BaseModel):
        BACKEND_URL: str = Field(
            default_factory=lambda: os.environ.get(
                "MIOS_AI_ENDPOINT", "http://127.0.0.1:8700/v1"),
            description="OpenAI-compat backend = the standalone MiOS Agent Pipe service (NOT hermes directly). Resolved from MIOS_AI_ENDPOINT, the one front door every agent and tool targets (Law 5), so the port follows [ports].agent_pipe instead of being frozen here. The router/dispatch/writes chain is extracted out of this OWUI pipe class into a gateway-agnostic FastAPI service so Hermes Discord + future Slack/Telegram/MCP gateways get the same tool-understanding parity as OWUI; agent-pipe forwards to hermes-agent itself. The former default named a RETIRED port on host.containers.internal -- an address this file's own docstring already recorded as never resolving once OWUI became a host process -- so the canonical OWUI entry point could not reach agent-pipe at all.",
        )
        BACKEND_MODEL: str = Field(
            default="hermes-agent",
            description="Model name to pass upstream. Direct dispatch to hermes uses its native model id `hermes-agent`.",
        )
        BACKEND_KEY: str = Field(
            default_factory=lambda: os.environ.get("API_SERVER_KEY") or os.environ.get("OPENAI_API_KEY") or "",
            description="Bearer key for the backend. Defaults to API_SERVER_KEY / OPENAI_API_KEY from the OWUI container env.",
        )

        REFINE_ENABLED: bool = Field(
            default=False,
            description="In-pipe CPU refinement pass. MiOS-Agent (agent-pipe, [ports].agent_pipe) handles refine centrally as of Phase D.5a -- having this ENABLED here causes a DOUBLE refine (OWUI pipe rewrites the prompt, then agent-pipe rewrites it again). Default flipped to False; agent-pipe's iGPU-lane qwen3:1.7b refine is faster than the dGPU qwen2.5-coder:7b path this valve used. Set to true ONLY when running OWUI without agent-pipe in front.",
        )
        REFINE_MODEL: str = Field(
            default="qwen2.5-coder:7b",
            description="Small NON-THINKING CPU model used for refinement. qwen3.x family models all emit to message.thinking with empty message.content even with /nothink directive (modelfile-level thinking-mode override). qwen2.5-coder:7b is the available non-thinking model that produces content directly. ~4.7 GB on CPU; cold load 30-90s on WSL2 disk, warm calls 3-10s. Loaded once with keep_alive=-1.",
        )
        REFINE_ENDPOINT: str = Field(
            default_factory=lambda: (
                os.environ.get("MIOS_REFINE_ENDPOINT")
                or "http://127.0.0.1:" + os.environ.get("MIOS_PORTS_LLM_LIGHT", "8500")),
            description="Refine-call endpoint -- mios-llm-light (llama.cpp behind llama-swap), resolved from MIOS_REFINE_ENDPOINT or [ports].llm_light. Hits /api/chat (NOT /v1, which drops the options field). The former default froze :8450, which under the current port scheme is [ports].k3s_api -- a refine call would have gone to the Kubernetes API server -- on a host.containers.internal address that stopped resolving when OWUI became a host process.",
        )
        REFINE_TIMEOUT_S: int = Field(
            default=180,
            description="Hard cap on the refine call. Should comfortably exceed warm-call latency on the host's CPU. On timeout the pipe falls through to original prompt (NOT 503; pipe is OWUI-facing and 503 would be a bad UX). 60s was insufficient with the expanded refiner system prompt (flagged as too short); 180s gives a 3x safety margin for warm calls + room for cold first calls + occasional CPU contention.",
        )
        REFINE_MAX_TOKENS: int = Field(
            default=220,
            description="Cap refine output. Smaller = faster turn. 220 tokens fits INTENT + 2-3 step PLAN comfortably; dropped from 300 to keep refine latency under the new 180s ceiling on a busy CPU.",
        )
        REFINE_SKIP_SHORT: bool = Field(
            default=True,
            description="Skip refinement on greetings/acks (hi/hello/thanks/bye) -- no value added, just adds latency.",
        )

        DISPLAY_NAME: str = Field(
            default="",
            description="Suffix appended to the FUNCTION row name in OWUI's model dropdown. Leave empty so the dropdown shows just the function title 'MiOS AI'.",
        )
        EMIT_STATUS: bool = Field(
            default=True,
            description="Emit status events (🧠 refine, 🧠 → hermes, 🛠️ tool, 🎨 polish, ✅). Symbol+term form, locale-neutral.",
        )
        EMIT_HERMES_TAIL: bool = Field(
            default=False,
            description="Live-poll /var/lib/mios/hermes-tail/latest.json and emit each new event during the stream. DEFAULT FALSE: agent-pipe is the canonical orchestrator now and forwards Hermes's own mios_status events via SSE (with humanistic labels). Setting true ALSO emits literal 'tool: terminal' / 'tool: kanban_block' labels from the hermes-tail file, which clutters the strip + duplicates events. Set true ONLY when running OWUI without agent-pipe in front.",
        )
        COLLAPSE_NARRATION: bool = Field(
            default=False,
            description="Buffer per-line, wrap meta-speak in <think>...</think> tags. DEFAULT FALSE: OWUI renders <think> as literal visible text (not a collapsible block); the actual collapsible the operator wants is <details type='reasoning'> which agent-pipe now emits centrally. Leaving this true ADDS visible <think>...</think> to the content. Set true ONLY when running OWUI without agent-pipe in front.",
        )
        TIMEOUT_S: int = Field(
            default=0,
            description="Total HTTP timeout in seconds (0 = unbounded, recommended for CPU-bound tool turns).",
        )

        POLISH_ENABLED: bool = Field(
            default=False,
            description="In-pipe polish pass. agent-pipe (Phase D.5b) handles polish centrally and wraps the raw sub-agent output in a <details type='reasoning'> dropdown ITSELF. Having this ENABLED here causes a DOUBLE polish + double-wrap. Default flipped to False; the OWUI pipe trusts agent-pipe's centralised pass. Set to true ONLY when running OWUI without agent-pipe in front.",
        )
        POLISH_MODEL: str = Field(
            default="qwen2.5-coder:7b",
            description="CPU model used for the output polish pass. Same model+keep_alive as the input refiner so cold-load happens once.",
        )
        POLISH_TIMEOUT_S: int = Field(
            default=180,
            description="Hard cap on the polish call. Falls back to raw hermes output on timeout (better than empty answer).",
        )
        POLISH_MAX_TOKENS: int = Field(
            default=600,
            description="Cap polished output. 600 tokens fits a multi-paragraph + table answer; bigger answers can pass through raw.",
        )
        POLISH_SKIP_SHORT_CHARS: int = Field(
            default=240,
            description="If the raw agent output is shorter than this and contains no narration markers, pass through unpolished -- no value in spinning up the CPU model for a one-liner result.",
        )
        CRITIC_ENABLED: bool = Field(
            default=False,
            description="In-pipe critic pass over the polished draft. agent-pipe's Phase B.1 / B.2 DCI critic + Phase D.5b polish stack handle review centrally. The in-pipe critic was paired with the in-pipe polish (both default-off now); running it standalone over a thin-passthrough body adds latency without complementary review. Set to true ONLY when running OWUI without agent-pipe in front + in-pipe POLISH_ENABLED is also true.",
        )
        CRITIC_MODEL: str = Field(
            default="qwen3:1.7b",
            description="Small model for the critic pass. Per the iGPU-only-micro-LLMs policy -- micro-LLMs land on the AMD/Intel iGPU CDI lane when present, leaving the dGPU free for big-model work. qwen3:1.7b ~1.4 GB.",
        )
        CRITIC_TIMEOUT_S: int = Field(
            default=45,
            description="Cap the critic call. Small model + structured input + JSON-only output = sub-10s typical; 45s is the safety ceiling.",
        )
        CRITIC_MAX_TOKENS: int = Field(
            default=300,
            description="Cap critic output. JSON verdict + issue list fits comfortably.",
        )
        CRITIC_MAX_ITERATIONS: int = Field(
            default=1,
            description="Max revise cycles. 0 = run critic but never revise (audit-only). 1 = one revision pass (compose, critique, revise, done). Bounded reflexion -- never infinite loop.",
        )
        AGENT_THINKING_LABEL: str = Field(
            default="🧠 MiOS-Hermes",
            description="The <summary> rendered above the collapsed reasoning block. Per-agent label so the operator can tell which agent (hermes / opencode / etc.) produced the thinking. Kept short + symbol-led so it reads the same across operator locales (a global sweep removed hardcoded English).",
        )
        ROUTER_ENABLED: bool = Field(default=True, description="layer-1 router (micro-LLM)")
        ROUTER_MODEL: str    = Field(default="qwen3:1.7b", description="always-warm micro-LLM (keep_alive=-1) for fast refinements; repointed from qwen3:0.6b-cpu to the 4-model-set micro base qwen3:1.7b")
        ROUTER_TIMEOUT_S: int = Field(default=12, description="s")
        ROUTER_MAX_TOKENS: int = Field(default=200, description="tokens")

    class UserValves(BaseModel):
        """PER-USER MiOS AI persona -- fully CUSTOMIZABLE IN OWUI (chat
        Controls > Valves, or per-user model settings). +
        'make the MiOS AI system prompt a user-defined persona with
        user-defined fields' + 'Persona should be customizable in OWUI'. Each
        field is a USER ENTRY; its `description` is the OPERATOR HINT shown in
        the OWUI Valves form, with examples of preferred values. These ride
        ALONGSIDE OWUI's environment variables ({{USER_NAME}}, locale, date)
        and the vendor system prompt -- they don't replace them. Every field is
        OPTIONAL; empty ones drop out, so there is NO hardcoded persona prose --
        only the user's own values are ever emitted, as a thin OpenAI-standard
        system block (see _compose_persona)."""
        enabled: bool = Field(
            default=True,
            description="Master switch for your persona. OFF = plain MiOS AI with environment grounding only (no persona block added).")
        persona_name: str = Field(
            default="",
            description="What the assistant calls ITSELF. Empty = 'MiOS AI'. Examples: Atlas, Jeeves, Friday.")
        address_as: str = Field(
            default="",
            description="What the assistant should call YOU. Empty = it uses no name. Put your own preferred first name, nickname, or title here.")
        tone: str = Field(
            default="",
            description="Voice / tone to adopt. Examples: concise, warm, formal, playful, dry, encouraging. Empty = neutral.")
        verbosity: str = Field(
            default="",
            description="Preferred answer length. One of: brief | balanced | detailed. Empty = the model decides per question.")
        formatting: str = Field(
            default="",
            description="Output style. Examples: markdown, plain text, bullet points, code-first, tables-ok. Empty = adapt to the content.")
        language: str = Field(
            default="",
            description="Preferred response language. Examples: English, Espanol, Francais, Nihongo. Empty = match the language you write in.")
        units: str = Field(
            default="auto",
            description="Units in answers: auto (follow your locale) | metric | imperial.")
        expertise: str = Field(
            default="",
            description="Explain at this level. One of: beginner | intermediate | expert. Empty = adapt to the question.")
        boundaries: str = Field(
            default="",
            description="Standing do's & don'ts. Examples: 'no emojis; always cite sources; never guess prices; ask before very long answers'. Empty = none.")
        custom_instructions: str = Field(
            default="",
            description="Any other standing instructions / preferences (plain text). Your words, used verbatim.")

    _TOOL_HINTS_PATH = "/usr/share/mios/owui/tool-hints.yaml"

    def __init__(self):
        self.valves = self.Valves()
        self.name = "MiOS AI"
        self._refine_system_rendered = self._render_refine_system()

    @staticmethod
    def _compose_persona(__user__: Optional[dict]) -> str:
        """Build a PER-USER persona system block from the operator's
        UserValves. Emits ONLY the operator's own field values (no hardcoded
        persona text); returns '' when nothing is set or the persona is
        disabled, so the vendor system prompt + OWUI env vars stand alone.
        Tolerates OWUI handing UserValves as a pydantic model OR a dict."""
        if not isinstance(__user__, dict):
            return ""
        v = __user__.get("valves")
        if v is None:
            return ""

        def _g(name, default=""):
            if isinstance(v, dict):
                return v.get(name, default)
            return getattr(v, name, default)

        if not _g("enabled", True):
            return ""
        name = str(_g("persona_name") or "").strip()
        address_as = str(_g("address_as") or "").strip()
        tone = str(_g("tone") or "").strip()
        verbosity = str(_g("verbosity") or "").strip()
        formatting = str(_g("formatting") or "").strip()
        language = str(_g("language") or "").strip()
        units = str(_g("units") or "auto").strip()
        expertise = str(_g("expertise") or "").strip()
        boundaries = str(_g("boundaries") or "").strip()
        custom = str(_g("custom_instructions") or "").strip()
        lines = []
        if name:
            lines.append(f"You are {name}.")
        if address_as:
            lines.append(f"Address the user as {address_as}.")
        if tone:
            lines.append(f"Tone: {tone}.")
        if verbosity:
            lines.append(f"Answer length: {verbosity}.")
        if formatting:
            lines.append(f"Formatting: {formatting}.")
        if language:
            lines.append(f"Always respond in {language}.")
        if units and units.lower() != "auto":
            lines.append(f"Units: {units}.")
        if expertise:
            lines.append(f"Explain at a {expertise} level.")
        if boundaries:
            lines.append(f"Boundaries: {boundaries}.")
        if custom:
            lines.append(custom)
        if not lines:
            return ""
        return "## OPERATOR PERSONA (user-set in OWUI)\n" + "\n".join(lines)

    @staticmethod
    def _collect_env_vars(__user__: Optional[dict],
                          __metadata__: Optional[dict],
                          body: Optional[dict] = None) -> dict:
        """Resolve THIS request's OWUI environment into a {{TOKEN}}: value map.

        Single source of truth shared by _resolve_env_vars (system-prompt
        substitution) and pipe() (structural forward to agent-pipe as
        metadata.variables). Channels, in priority order: the frontend-captured
        browser variables (metadata.variables AND body.variables -- locale /
        timezone / live geolocation); the OWUI-backend-substituted location
        recovered from the system-prompt TEXT (the channel that actually carries
        the iPhone app's live geo -- see step 1.5); the host clock for any
        CURRENT_* the browser omitted; then __user__ (name / email / persisted
        profile location). Absent-value sentinels are dropped (case-insensitive,
        shared with server _ENV_SENTINELS) so a missing fact never overrides a
        real value. 'OWUI provides entire environment details
        ... USE them' + 'iPhone OWUI shares location but MiOS said it
        had none -- OWUI exposes DOZENS of env details, MiOS AI uses them'."""
        import datetime as _dt
        sub: dict = {}
        if isinstance(__metadata__, dict):
            for _k, _v in (__metadata__.get("variables") or {}).items():
                if _env_ok(_v):
                    sub[str(_k)] = str(_v).strip()
        if isinstance(body, dict):
            for _k, _v in (body.get("variables") or {}).items():
                if _env_ok(_v):
                    sub.setdefault(str(_k), str(_v).strip())
        if "{{USER_LOCATION}}" not in sub and isinstance(body, dict):
            try:
                for _m in (body.get("messages") or []):
                    if not (isinstance(_m, dict) and _m.get("role") == "system"):
                        continue
                    _sys = _m.get("content")
                    if not isinstance(_sys, str) or not _sys:
                        continue
                    _mt = re.search(
                        r"location\s*(?:is|[:=])\s*(.+?)(?:\n|\.\s|\.$|$)",
                        _sys, re.I)
                    if _mt:
                        _rec = _mt.group(1).strip().rstrip(".").strip()
                        if ("{{" not in _rec and re.search(r"[0-9A-Za-z]", _rec)
                                and _env_ok(_rec)):
                            sub.setdefault("{{USER_LOCATION}}", _rec)
                            break
            except Exception:
                pass  # recovery is best-effort; never break the turn
        _now = _dt.datetime.now().astimezone()
        sub.setdefault("{{CURRENT_DATE}}", _now.strftime("%Y-%m-%d"))
        sub.setdefault("{{CURRENT_TIME}}", _now.strftime("%I:%M:%S %p"))
        sub.setdefault("{{CURRENT_DATETIME}}",
                       _now.strftime("%Y-%m-%d %I:%M:%S %p"))
        sub.setdefault("{{CURRENT_WEEKDAY}}", _now.strftime("%A"))
        _tz = _now.tzname() or ""
        if _tz:
            sub.setdefault("{{CURRENT_TIMEZONE}}", _tz)
        if isinstance(__user__, dict):
            _nm = str(__user__.get("name") or "").strip()
            if _env_ok(_nm):
                sub.setdefault("{{USER_NAME}}", _nm)
            _em = str(__user__.get("email") or "").strip()
            if _env_ok(_em):
                sub.setdefault("{{USER_EMAIL}}", _em)
            _info = __user__.get("info")
            if isinstance(_info, dict):
                _loc = str(_info.get("location") or "").strip()
                if _env_ok(_loc):
                    sub.setdefault("{{USER_LOCATION}}", _loc)
        return sub

    @staticmethod
    def _resolve_env_vars(text: str,
                          __user__: Optional[dict],
                          __metadata__: Optional[dict]) -> str:
        """Resolve any OWUI {{...}} template token that reached the pipe
        UNRESOLVED, then STRIP whatever is still unresolved so no literal
        {{VAR}} ever survives into the model's system prompt.

        OWUI substitutes most system-prompt vars server-side before the
        pipe runs (functions.apply_system_prompt_to_body). But
        {{USER_LANGUAGE}} and {{CURRENT_TIMEZONE}} are FRONTEND-only
        variables (verified in OWUI utils/task.py: prompt_template has no
        case for either) -- they resolve ONLY when the browser sent them
        in form_data['variables'], else they leak. Direct-API callers get
        no substitution at all. So we backfill from metadata.variables
        (authoritative for locale/timezone the browser captured), the host
        clock, and __user__, then strip the remainder..

        Note: this does NOT touch the reply-language -- that mirrors the
        operator's own input per the template. These values feed
        formatting/units + grounding only."""
        if not text or "{{" not in text:
            return text
        sub = Pipe._collect_env_vars(__user__, __metadata__)
        for _k, _v in sub.items():
            if _k in text:
                text = text.replace(_k, _v)
        text = re.sub(r"\{\{[^}]+\}\}", "", text)
        return text

    def _render_refine_system(self) -> str:
        """Load tool-hints.yaml, render the canonical-verbs section as
        a markdown table, and substitute into _REFINE_SYSTEM. Falls
        back to the un-substituted prompt on any error (YAML missing,
        parse error, etc.) so the refine pass still runs."""
        try:
            import yaml as _yaml
            with open(self._TOOL_HINTS_PATH, "r", encoding="utf-8") as f:
                manifest = _yaml.safe_load(f) or {}
        except Exception:
            return self._REFINE_SYSTEM.replace(
                "{tool_table}",
                "(tool-hints.yaml not loaded -- agent must rely on PATH discovery)",
            )
        verbs = manifest.get("canonical_verbs") or []
        if not verbs:
            return self._REFINE_SYSTEM.replace(
                "{tool_table}", "(no canonical verbs registered)",
            )
        rows = ["| Verb | Intent | Example |", "|------|--------|---------|"]
        for v in verbs:
            name = v.get("name", "?")
            intent = (v.get("intent", "") or "").replace("|", "\\|")
            example = (v.get("example", "") or "").replace("|", "\\|")
            rows.append(f"| `{name}` | {intent} | `{example}` |")
        patterns = manifest.get("intent_patterns") or []
        extras = []
        if patterns:
            extras.append("")
            extras.append("Intent patterns (for asks the verb table alone misses):")
            for p in patterns:
                m = p.get("match", "?")
                c = p.get("first_call", "")
                n = p.get("note", "")
                extras.append(f"- `{m}` → `{c}`  ({n})" if n else
                              f"- `{m}` → `{c}`")
        table = "\n".join(rows + extras)
        return self._REFINE_SYSTEM.replace("{tool_table}", table)

    def pipes(self):
        return [{"id": "mios-agent", "name": self.valves.DISPLAY_NAME}]

    async def _emit(
        self,
        emitter: Optional[Callable[..., Awaitable[None]]],
        description: str,
        done: bool = False,
    ) -> None:
        if not (self.valves.EMIT_STATUS and emitter):
            return
        try:
            await emitter({
                "type": "status",
                "data": {"description": description, "done": done},
            })
        except Exception:
            pass

    async def _tail_watcher(
        self,
        emitter: Optional[Callable[..., Awaitable[None]]],
        stop: asyncio.Event,
    ) -> None:
        """Background task: poll hermes-tail JSON, emit new events as
        they arrive. Tracks last-seen event ts per-task so we never
        re-emit.

        Operator-flagged the trace showed `⚙️ hermes:
        tool: terminal` events BEFORE the pipe's `📡 prompt` marker,
        making it look like Hermes was running first. Root cause was
        last_ts=0.0 -- the first tick on every new chat turn replayed
        EVERY historical event in the rolling latest.json buffer
        (which carries the last several minutes of hermes activity).

        Fix: initialise last_ts to NOW so the watcher only emits
        events that land AFTER this chat turn dispatched. The
        historical buffer is preserved for satisfaction_loop /
        post-hoc audit, just not replayed in the user-facing
        status pill.
        """
        if not (self.valves.EMIT_HERMES_TAIL and emitter):
            return
        last_ts = time.time()
        while not stop.is_set():
            try:
                st = os.stat(HERMES_TAIL_PATH)
                if st.st_mtime > 0:
                    with open(HERMES_TAIL_PATH) as f:
                        payload = json.load(f)
                    for ev in payload.get("events", []):
                        ev_ts = ev.get("ts", 0)
                        if ev_ts > last_ts:
                            last_ts = ev_ts
                            kind = ev.get("kind", "")
                            detail = ev.get("detail", "")
                            icon = _TAIL_ICONS.get(kind, "·")
                            await self._emit(emitter, f"{icon} {detail}")
            except (OSError, json.JSONDecodeError):
                pass
            try:
                await asyncio.wait_for(stop.wait(), timeout=TAIL_POLL_INTERVAL_S)
            except asyncio.TimeoutError:
                continue

    _CONVERSATIONAL_RE = re.compile(
        r"^\s*((?:hi|hello|hey|yo|howdy)(?:\s+(?:there|y[’']?all|everyone|all|guys|friend|friends|bot))?|"
        r"sup|gm|gn|ok|okay|kk|alright|"
        r"thanks|thx|ty|thank you|cool|nice|great|got it|"
        r"sounds good|sgtm|sure|yes|no|yep|nope|yeah|nah|"
        r"bye|cya|goodbye|later|peace|seeya|"
        r"good (morning|afternoon|evening|night)|"
        r"what[’']?s (up|new|good|happening|going on)|"
        r"how['’]?s (it going|things|things today|things going|life|stuff)|"
        r"how (are|have|you been|are things|are you|are ya|"
        r"you doing|you been|is it going|is everything)|"
        r"what do you (want|wanna|got|need)|"
        r"hola|holi|holaaa+|buenas|buenos d[ií]as|buenas (tardes|noches)|"
        r"gracias|de nada|adi[óo]s|chao|chau|hasta luego|qu[eé] tal|"
        r"c[óo]mo (est[áa]s|va|andas)|todo bien|vale|s[íi]|"
        r"ol[áa]|bom dia|boa (tarde|noite)|obrigad[oa]|tudo bem|tchau|"
        r"salut|bonjour|bonsoir|bonne nuit|merci|merci beaucoup|"
        r"de rien|au revoir|[àa] bient[oô]t|[çc]a va|comment [çc]a va|d[’']accord|oui|non|"
        r"ciao|salve|buongiorno|buonasera|buonanotte|"
        r"grazie|prego|arrivederci|come stai|come va|s[íi]|"
        r"hallo|hi+|moin|servus|gr[üu][sß] (dich|gott)|guten (morgen|tag|abend)|"
        r"danke|bitte|tsch[üu]ss|auf wiedersehen|wie geht.?s|"
        r"hej|hej hej|hejs[åa]|tack|farv[ée]l|"
        r"hallo|hoi|dag|dankjewel|doei|"
        r"czesc|cze[śs][ćc]|dzi[ęe]kuj[ęe]|do widzenia|"
        r"ahoj|d[ěe]kuji|d[ěe]k|nashledanou|"
        r"ohayou?|konnichiwa|konbanwa|sayounara|arigatou?|"
        r"annyeong(haseyo)?|kamsahamnida|"
        r"ni hao|xie ?xie|zai ?jian|"
        r"namaste|namaskar|shukriya|dhanyavaad|"
        r"shalom|toda|"
        r"mahalo|aloha)[!?.,\s]*$"
        r"|^\s*(?:привет|здравствуй(те)?|спасибо|пока|до свидания)[!?.,\s]*$"
        r"|^\s*(?:你好|您好|嗨|哈罗|谢谢|再见)[!?.,\s]*$"
        r"|^\s*(?:こんにちは|こんばんは|おはよう(ございます)?|ありがとう(ございます)?|さようなら|またね)[!?.,\s]*$"
        r"|^\s*(?:안녕(하세요)?|반갑습니다|감사합니다|고마워(요)?|잘 ?가)[!?.,\s]*$"
        r"|^\s*(?:مرحبا|أهلا|السلام عليكم|شكرا|وداعا)[!?.,\s]*$"
        r"|^\s*(?:שלום|תודה|להתראות)[!?.,\s]*$",
        re.IGNORECASE,
    )

    _REFINE_SYSTEM = (
        "You are a prompt refinement layer for a multi-agent system.\n"
        "Rewrite the user's raw request into a structured handoff the\n"
        "downstream orchestrator agent will execute.\n"
        "\n"
        "## THINK FIRST, then emit the structured handoff\n"
        "Before writing the schema below, reason step-by-step in a\n"
        "single THINKING block: (a) restate the user's intent in one\n"
        "phrase, (b) scan the canonical-verbs table for the row that\n"
        "matches, (c) decide if the intent is single-step or multi-\n"
        "step, (d) decide if delegate fan-out helps. Keep THINKING\n"
        "under 80 tokens. Write in ENGLISH by default.\n"
        "\n"
        "## OUTPUT SCHEMA (emit EXACTLY this, nothing else)\n"
        "THINKING: <free-form planning, <=80 tokens>\n"
        "INTENT: <one sentence: what the user wants>\n"
        "TOOLS: <comma-separated downstream tool names>\n"
        "DELEGATE: <YES if parallel fan-out is sensible, else NO>\n"
        "PLAN:\n"
        "  1. <tool>: <exact command / arguments>\n"
        "  2. ...\n"
        "\n"
        "## CANONICAL VERBS (PREFER these over generic shells; pick the row matching the user's intent)\n"
        "{tool_table}\n"
        "\n"
        "## DOWNSTREAM TOOLS (available to the orchestrator)\n"
        "terminal (any shell command, including the verbs above),\n"
        "delegate_task (spawn parallel sub-agents), web_search,\n"
        "web_extract (SEARCH-ONLY backend -- never expect URL content\n"
        "back; for URL content use `terminal: curl ...`),\n"
        "discord_send_message, cronjob_*, kanban_*, memory_*, read_file,\n"
        "write_file, skill_view, skill_manage.\n"
        "\n"
        "browser_* (browser_navigate, browser_console, browser_snapshot,\n"
        "browser_click, browser_type) drives a HEADLESS CDP session the\n"
        "user CANNOT see -- only useful for scraping or inspection. For\n"
        "any user-visible browser action prefer the canonical verbs above\n"
        "(URL open / map / image). Before ANY browser_* call, the agent\n"
        "must run the canonical 'bring CDP up' verb first.\n"
        "\n"
        "## HARD RULES\n"
        "- Trivial intent (matches ONE canonical-verb row) → ONE PLAN\n"
        "  line. No padding, no skill loads, no web_extract noise.\n"
        "- EXECUTE a launcher's resolved target verbatim. Never\n"
        "  substitute a different launcher (e.g. if the launcher\n"
        "  returned a vendor URI, do NOT switch to a different\n"
        "  storefront).\n"
        "- '<query> near <PLACE>' / 'around <PLACE>' / 'by <PLACE>':\n"
        "  resolve the PLACE'S ADDRESS first (canonical 'map' verb),\n"
        "  then web_search anchored on that address. Never return\n"
        "  generic same-city results.\n"
        "- 'open <url>', 'go to <url>', 'show me a map of', 'directions\n"
        "  to', 'show a picture of', 'open <gui-app>' all map to specific\n"
        "  canonical verbs above. NEVER claim the environment 'cannot\n"
        "  display' or 'cannot open a browser' -- if a canonical verb\n"
        "  exists for the intent, USE IT.\n"
        "- 'close <X>' (app/game/window): use the canonical close\n"
        "  verb (graceful WM_CLOSE). NEVER pkill / Stop-Process /\n"
        "  systemctl stop on infrastructure services.\n"
        "- Output ONLY the labeled INTENT/TOOLS/DELEGATE/PLAN block.\n"
        "  No preamble, no markdown headers (no ##), no commentary,\n"
        "  no closing remarks.\n"
        "- Stay under 300 tokens total.\n"
    )

    _LEGACY_REFINE_DELETED = True

    _POLISH_SYSTEM = (
        "You are MiOS-Agent's FINAL-ANSWER polisher. The downstream\n"
        "agent (MiOS-Hermes today; same role for OpenCode / MCP /\n"
        "delegated subagents tomorrow) has just finished a task. Its\n"
        "RAW OUTPUT (narration, tool calls, intermediate text, final\n"
        "result) is provided below.\n"
        "\n"
        "Produce ONE clean operator-facing answer in markdown. The\n"
        "operator will not see the raw output, it's collapsed in a\n"
        "<details type=\"reasoning\"> block above your answer. They\n"
        "see only what you emit.\n"
        "\n"
        "## LOCALE\n"
        "Respond in ENGLISH by default. Switch to another language ONLY\n"
        "when the ORIGINAL OPERATOR ASK below is itself clearly written in\n"
        "that language -- then reply in that ONE language only, mirroring\n"
        "the operator's diction. Never drift to a language the operator did\n"
        "not use. Tool output (paths, IDs, command names, JSON keys) stays\n"
        "in its native form.\n"
        "\n"
        "## RULES (hard)\n"
        "- RAW OUTPUT is ground truth. NEVER invent paths, IDs,\n"
        "  numbers, statuses, app names, registry coords, ports, sizes,\n"
        "  timestamps, package names. If a field isn't in RAW OUTPUT,\n"
        "  don't write it.\n"
        "- NEVER report an action as 'successful' / 'completed' / 'opened' /\n"
        "  'launched' / 'posted' / 'sent' unless RAW OUTPUT contains the\n"
        "  matching tool_result with success:true. As an example,\n"
        "  polish once claimed 'Open YouTube: Successfully opened\n"
        "  YouTube' + 'Web Search: Proceeded with the web search' when\n"
        "  only one (failing) web_extract call had actually run. Reporting\n"
        "  steps that did NOT execute is a defect. If a planned step did\n"
        "  not run or did not succeed, SAY SO -- 'Step 2 (web_search) did\n"
        "  not run' or 'Step 1 (mios-open-url) returned exit 1: <err>'.\n"
        "- NEVER emit <details> in your output. The pipe wraps agent\n"
        "  thinking in its own <details type=\"reasoning\"> block above\n"
        "  your answer. Adding another one stacks them and the operator\n"
        "  sees two expand-arrows. Plain markdown only.\n"
        "- Strip narration. Phrases like \"Let me\", \"I'll\", \"First\n"
        "  I...\", \"Now I'll\", \"Let me check\" are FORBIDDEN in your\n"
        "  output. The operator wants the result, not the reasoning.\n"
        "- Surface CONCRETE results: file paths, command exit codes,\n"
        "  app statuses, IDs, sizes, URLs -- straight from RAW OUTPUT.\n"
        "- If the agent FAILED, say what failed in ONE sentence and\n"
        "  surface the verbatim error in a code block.\n"
        "- NO \"would you like me to...\" trailing questions unless RAW\n"
        "  OUTPUT explicitly proposed exactly that.\n"
        "- NO \"if you'd like\" / \"feel free to\" / \"let me know if you\n"
        "  need\" boilerplate. End when the answer is done.\n"
        "- NO inventing operator-state: 'I appreciate the kind words',\n"
        "  'thanks for the patience', 'just to keep things clear I'm\n"
        "  actually running model X' -- the operator did NOT say kind\n"
        "  words, did NOT ask about your model, did NOT thank you.\n"
        "  Polish the RESULT, not a parasocial chat. If RAW OUTPUT\n"
        "  contains this kind of preamble, DELETE it -- don't pass\n"
        "  it through.\n"
        "- NO offering alternatives the agent didn't actually try.\n"
        "  'I can generate a text description', 'I can find images\n"
        "  online for you' -- if the action wasn't run, don't pitch\n"
        "  it as a follow-up. The operator wants the thing they\n"
        "  asked for, OR a clear single-sentence reason it didn't\n"
        "  happen + the fix to retry.\n"
        "- No preamble like \"Here's the result:\" -- get to the answer.\n"
        "- Use markdown tables / lists ONLY when they make the answer\n"
        "  clearer. A 1-line answer is one line.\n"
        "- Emit markdown SOURCE directly, NOT wrapped in ```markdown\n"
        "  ... ``` fences. OWUI renders bare markdown as proper markup\n"
        "  (headings, bold, lists, tables). Wrapping the WHOLE answer\n"
        "  in a code fence makes OWUI display the raw markdown source\n"
        "  inside a code block instead of rendering it. ONLY use code\n"
        "  fences for actual code / command snippets / shell output --\n"
        "  never for the framing of the answer itself.\n"
        "- If RAW OUTPUT is mostly empty / mostly tool calls with no\n"
        "  text result, summarize what tools ran + their outcomes in\n"
        "  one short paragraph. Never claim something worked that the\n"
        "  raw output doesn't confirm.\n"
        "\n"
        "## KNOWN AGENT ERRORS in RAW OUTPUT -- recognize + rewrite cleanly\n"
        "\n"
        "IMPORTANT: only apply the rewrites below when the raw output\n"
        "does NOT also contain a confirmed-success signal -- specifically\n"
        "a tool_result with `success: true`, a `pid: <N>` in broker\n"
        "output, a `presented_to_operator: true` from mios-window-active,\n"
        "or a `wrote <path>` line from a file/CDI helper. If the agent\n"
        "errored during exploration but ultimately succeeded, polish\n"
        "the SUCCESS not the error -- for example, polish once\n"
        "rewrote a successful Notepad launch (pid 499978) into\n"
        "'Agent attempted PowerShell in bash by mistake' because an\n"
        "earlier exploratory bash call in the same turn errored. The\n"
        "operator saw a misleading failure message for what was\n"
        "actually a working launch (the broker had a separate\n"
        "invisible-window bug fixed in mios-windows; the polish\n"
        "false-positive compounded it).\n"
        "\n"
        "If RAW OUTPUT contains any of these signatures AND no\n"
        "confirmed-success signal, DO NOT echo the raw error verbatim --\n"
        "the operator already saw it once. Instead emit a single line\n"
        "explaining what the agent did wrong + what the operator\n"
        "should try next:\n"
        "\n"
        "  * `/var/lib/mios/hermes.<Word>` or `/var/lib/mios/hermes.<Prop>`\n"
        "    in any line that mentions 'not recognized' / 'cmdlet' / 'cannot\n"
        "    parse' -> The agent ran PowerShell syntax directly in `terminal:`\n"
        "    (bash). `$_` got mis-parsed as bash's last-arg variable.\n"
        "    Polished line:\n"
        "      'Agent attempted PowerShell in bash by mistake. Retry: the\n"
        "       request will be wrapped via `terminal: mios-windows ps`.'\n"
        "\n"
        "  * `screencapture.exe`, `Invoke-Screenshot`, `GDI+ error`, or\n"
        "    `cannot capture` errors -> The agent guessed at non-existent\n"
        "    screenshot tools instead of `mios-screenshot`. Polished line:\n"
        "      'Agent reached for an unavailable screenshot tool. Retry:\n"
        "       the canonical verb is `mios-screenshot [--open]`.'\n"
        "\n"
        "  * `Get-StartApps not found` / `pwsh not found` in bash output ->\n"
        "    Same PowerShell-in-bash pattern. Tell operator to retry; the\n"
        "    next attempt should use `mios-windows ps` or `mios-find`.\n"
        "\n"
        "  * `I don't have <tool> available` / `<tool> is not in my\n"
        "    toolset` / `appears to be vendor-specific` / `not in\n"
        "    this environment` claims about anything starting with\n"
        "    `mios-` (a SHELL helper on PATH) or the native gateway\n"
        "    tools (discord_send_message, web_search, kanban_*,\n"
        "    delegate_task, memory_save, etc.) -> The agent\n"
        "    hallucinated the tool's absence. The fix is one shell\n"
        "    call. Polished line:\n"
        "      'Agent hallucinated that `<tool>` was unavailable. For\n"
        "       any `mios-*` verb, run `terminal: <tool> <args>` --\n"
        "       it lives on $PATH. Native gateway tools live in the\n"
        "       MiOS api_server toolset. Retry the request.'\n"
        "\n"
        "  * `no display server` / `no active X server` / `no X server\n"
        "    or Wayland` / `terminal restrictions prevent running\n"
        "    graphical apps` / `display infrastructure issue` / `pure\n"
        "    terminal service` / `browser can't launch in this\n"
        "    environment` -> ALL LIES. MiOS WSL2 has WSLg (DISPLAY=:0,\n"
        "    WAYLAND_DISPLAY=wayland-0, /mnt/wslg/ mounted). The agent\n"
        "    forgot to use the canonical shim. Polished line:\n"
        "      'Agent skipped the canonical WSLg launcher. Retry with\n"
        "       `terminal: mios-gui-launch <app>` for Linux GUI apps\n"
        "       or `terminal: mios-open-url <url>` for browser opens\n"
        "       -- both work in this MiOS environment.'\n"
        "\n"
        "## ORIGINAL OPERATOR ASK\n"
        "{user_prompt}\n"
        "\n"
        "## RAW AGENT OUTPUT (ground truth)\n"
        "{raw_output}\n"
        "\n"
        "## POLISHED ANSWER\n"
    )

    _NARRATION_MARKERS = re.compile(
        r"\b(let me|i.?ll|i'?ll|first,?\s*i|now,?\s*i|let.?s|i need to|i.?m going to|i.?ve|i.?m about to)\b",
        re.IGNORECASE,
    )
    _STRUCTURED_MD_RE = re.compile(
        r"^\s*(?:#{1,6}\s+\S|\|[\s\-:|]+\|)",
        re.M,
    )
    _KNOWN_AGENT_ERROR_RE = re.compile(
        r"(?:not recognized|cmdlet|cannot parse|screencapture\.exe|"
        r"Invoke-Screenshot|GDI\+|Get-StartApps not found|pwsh not found|"
        r"vendor-specific|I don.t have|not in (?:my toolset|this environment)|"
        r"no (?:active )?(?:X server|display server|X server or Wayland)|"
        r"terminal restrictions prevent|display infrastructure issue|"
        r"pure terminal service|browser can.t launch in this environment|"
        r"can.t open a map for you in this WSL environment)",
        re.IGNORECASE,
    )

    HERMES_SESSIONS_DIR = "/var/lib/mios/hermes/sessions"

    def _load_session_tool_history(self, after_ts: float,
                                    max_age_s: float = 600
                                    ) -> Optional[list[dict]]:
        """Find the newest Hermes session JSON whose mtime is later
        than `after_ts` (the moment the pipe dispatched to hermes)
        and within `max_age_s` of now. Return the OpenAI-format
        message list (user/assistant/tool) for the compose layer.
        Returns None when no session found or unreadable -- compose
        falls back to the legacy text-blob path.

        Day-0 / bootc note: HERMES_SESSIONS_DIR lives under
        /var/lib/mios/hermes (created by mios-hermes-firstboot at
        first boot; bootc-bootable). No /etc writes required."""
        import glob, os, json as _json
        try:
            sessions = sorted(
                glob.glob(f"{self.HERMES_SESSIONS_DIR}/session_*.json"),
                key=os.path.getmtime, reverse=True,
            )
        except OSError:
            return None
        now = time.time()
        for path in sessions[:5]:
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            if mtime < after_ts:
                break
            if (now - mtime) > max_age_s:
                continue
            try:
                d = _json.loads(open(path, "r", encoding="utf-8").read())
            except (OSError, _json.JSONDecodeError):
                continue
            msgs = d.get("messages") or []
            if not msgs:
                continue
            last_user_idx = -1
            for i in range(len(msgs) - 1, -1, -1):
                m = msgs[i]
                if isinstance(m, dict) and m.get("role") == "user":
                    last_user_idx = i; break
            slice_msgs = msgs[last_user_idx:] if last_user_idx >= 0 else msgs
            return slice_msgs
        return None

    def _render_tool_history_for_compose(self, msgs: list[dict]) -> str:
        """Render the OpenAI-format message list as a compact JSON
        the compose model can reason over. Pairs each assistant
        tool_call with its matching tool_result message and adds a
        derived `success` field (parsed from the tool result content
        when the upstream tool returned JSON with `success` -- the
        MiOS verb convention)."""
        import json as _json
        results: dict[str, dict] = {}
        for m in msgs:
            if not isinstance(m, dict) or m.get("role") != "tool":
                continue
            tcid = m.get("tool_call_id") or ""
            content = m.get("content") or ""
            success: Optional[bool] = None
            try:
                parsed = _json.loads(content) if isinstance(content, str) else None
                if isinstance(parsed, dict) and "success" in parsed:
                    success = bool(parsed["success"])
            except (_json.JSONDecodeError, ValueError):
                pass
            preview = content if isinstance(content, str) else str(content)
            results[tcid] = {
                "tool_call_id": tcid,
                "content_preview": preview[:1200],
                "success": success,
            }
        events: list[dict] = []
        for m in msgs:
            if not isinstance(m, dict):
                continue
            role = m.get("role")
            if role == "user":
                events.append({
                    "role": "user",
                    "content": (m.get("content") or "")[:1200],
                })
            elif role == "assistant":
                tc = m.get("tool_calls") or []
                if tc:
                    for t in tc:
                        fn = (t.get("function") or {}) if isinstance(t, dict) else {}
                        name = fn.get("name", "?")
                        args = fn.get("arguments", "")
                        tcid = t.get("id", "") if isinstance(t, dict) else ""
                        r = results.get(tcid, {})
                        events.append({
                            "role": "assistant.tool_call",
                            "tool_call_id": tcid,
                            "tool": name,
                            "arguments": args[:600] if isinstance(args, str)
                                         else _json.dumps(args)[:600],
                            "success": r.get("success"),
                            "result_preview": r.get("content_preview", ""),
                        })
                text = m.get("content")
                if text:
                    events.append({
                        "role": "assistant.text",
                        "content": str(text)[:1200],
                    })
        return _json.dumps({"history": events}, indent=2)

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
        "MiOS verbs available for dispatch. Each verb is tagged WRITE\n"
        "(causes a visible system effect) or READ (returns info only):\n"
        '  [WRITE] open_app(name, position="default", args?, monitor=0)\n'
        '          -- LAUNCH an app/program. Use for "open X" / "launch X" /\n'
        '             "start X" / "run X". position enum:\n'
        '             default / as-is / center / left / right / top /\n'
        '             bottom / top-left / top-right / bottom-left /\n'
        '             bottom-right / maximize\n'
        '  [WRITE] launch_app(name)                -- simpler launch, no position arg\n'
        '  [WRITE] focus_window(title, position="default")\n'
        '          -- bring an OPEN window to front + apply default golden+\n'
        '             16:10-centered geometry (same position enum as open_app).\n'
        '             pass position="as-is" to focus WITHOUT resizing.\n'
        '  [WRITE] move_window(title, position, monitor=0)\n'
        '  [WRITE] close_window(title, mode="graceful")   -- mode: graceful|force\n'
        '  [WRITE] open_url(url, browser?)         -- open a URL in a browser\n'
        '  [READ ] list_windows()                  -- list currently OPEN windows\n'
        '  [READ ] screen_layout()                 -- monitor geometry\n'
        '  [READ ] mios_find(name)                 -- resolve name -> path, no launch\n'
        '  [READ ] mios_apps(filter?)              -- INVENTORY of installed apps, no launch\n'
        '  [READ ] everything_search(query, limit=10, ext?)\n'
        '          -- Windows-side filesystem search via Voidtools Everything CLI.\n'
        '             Use for finding files / .exe / paths on the operator s\n'
        '             Windows drives (C:, D:, M:, etc.).\n'
        '  [READ ] fs_search(query, limit=20, ext?, path?, type?)\n'
        '          -- Linux-side filesystem search inside the MiOS-DEV/host\n'
        '             environment (plocate -> locate -> find fallback).\n'
        '             Use for finding files in /usr /etc /var/lib /opt /home\n'
        '             /root /var/log /usr/share/mios. type: "f" files only,\n'
        '             "d" directories only, omit for both.\n'
        '  [READ ] system_status()\n'
        '  [READ ] service_status(name)\n'
        '          -- systemctl is-active + status snapshot for a Linux\n'
        '             service (hermes-agent, mios-daemon, mios-open-webui, ...).\n'
        '  [WRITE] service_restart(name)\n'
        '          -- systemctl restart <name>. Use for live patches.\n'
        '  [READ ] process_list(filter?, sort=\"rss\", limit=20)\n'
        '          -- ps snapshot sorted by rss (default) or cpu.\n'
        '             filter = case-insensitive substring on command name.\n'
        '  [READ ] container_status(name?)\n'
        '          -- podman ps -a snapshot. name = optional substring filter.\n'
        '  [WRITE] container_restart(name)\n'
        '          -- podman restart <name> (or substring of a container name).\n'
        "\n"
        "Verb-pick priority (most common cases first):\n"
        '  "open X" / "launch X" / "start X" / "run X"  -> open_app(name=X)\n'
        '  "close X"                                    -> close_window(title=X)\n'
        '  "focus X" / "bring X to front" / "switch to X" -> focus_window(title=X)\n'
        '  "move X to <pos>"                            -> move_window(title=X, position=<pos>)\n'
        '  "what apps are installed" / "list apps"      -> mios_apps()\n'
        '  "what windows are open"                      -> list_windows()\n'
        '  "go to <url>" / "visit <url>"                -> open_url(url=<url>)\n'
        "\n"
        "Rules:\n"
        "- `dispatch` only when ONE verb solves it.\n"
        "- A WRITE verb is the right pick whenever the user asks for a\n"
        "  system effect (open/close/focus/move). NEVER pick a READ verb\n"
        "  when the user clearly wants an effect.\n"
        "- Position defaults to \"default\" (golden+16:10 centered); set\n"
        "  explicitly only when the user named a side.\n"
        "- `chat` for greetings, thanks, one-sentence clarification.\n"
        "- `agent` for N>1 tools, web research, install, file editing,\n"
        "  general knowledge questions, conversational follow-through.\n"
        "  MiOS-Agent is both an Agentic-OS AND a generalized AI agent.\n"
        "- Write `reply` fields in ENGLISH by default; use another language\n"
        "  only if the user's own message is clearly written in it.\n"
        "- Output JSON ONLY -- no preamble, no markdown, no commentary."
    )

    async def _classify_intent(
        self,
        user_text: str,
        emitter: Optional[Callable[..., Awaitable[None]]],
    ) -> Optional[dict]:
        """Layer-1 router call. Returns the parsed verdict dict, or
        None to fall through to the legacy refine+hermes path."""
        if not self.valves.ROUTER_ENABLED or not user_text.strip():
            return None
        await self._emit(emitter, "🧭 route")
        payload = {
            "model": self.valves.ROUTER_MODEL,
            "messages": [
                {"role": "system", "content": self._ROUTER_SYSTEM},
                {"role": "user",   "content": user_text[:2000]},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.0,
            "max_tokens": int(self.valves.ROUTER_MAX_TOKENS),
            "stream": False,
        }
        url = self.valves.REFINE_ENDPOINT.rstrip("/") + "/v1/chat/completions"
        try:
            timeout = aiohttp.ClientTimeout(total=int(self.valves.ROUTER_TIMEOUT_S))
            async with aiohttp.ClientSession(timeout=timeout) as s:
                async with s.post(url,
                                  data=json.dumps(payload).encode(),
                                  headers={"Content-Type": "application/json"}) as r:
                    if r.status != 200:
                        return None
                    body = await r.json()
        except (asyncio.TimeoutError, aiohttp.ClientError):
            await self._emit(emitter, "🧭 ⚠ router timeout → agent")
            return None
        except Exception:
            return None
        choices = body.get("choices") or []
        if not choices:
            return None
        content = ((choices[0].get("message") or {}).get("content") or "").strip()
        if not content:
            return None
        content = re.sub(r"^\s*```(?:json)?\s*\n?", "", content)
        content = re.sub(r"\n?```\s*$", "", content)
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            return None
        if not isinstance(parsed, dict) or "action" not in parsed:
            return None
        _row = {
            "source": "mios-agent-pipe",
            "kind": "classify",
            "severity": "info",
            "summary": str(parsed.get("action", "?"))[:120],
            "payload": parsed,
        }
        _db_fire(_db_post(_db_create("event", _row, now_fields=("ts",))))
        return parsed

    async def _dispatch_mios_verb(
        self,
        tool: str,
        args: dict,
        emitter: Optional[Callable[..., Awaitable[None]]],
    ) -> str:
        """Execute one MiOS verb via the launcher broker (the same
        broker mios_verbs.Tools uses). Returns the broker output as
        a single string. Fail-open: returns the error JSON on any
        failure so compose can surface it."""
        import socket as _socket
        env_prefix = ""
        if tool == "open_app":
            name = str(args.get("name", "")).strip()
            position = str(args.get("position", "default")).lower()
            extra_args = args.get("args") or []
            if position and position != "as-is":
                env_prefix = f"MIOS_LAUNCH_POSITION={shlex.quote(position)} "
            if extra_args:
                ea = " ".join(shlex.quote(str(a)) for a in extra_args)
                cmd = f"{env_prefix}mios-windows launch {shlex.quote(name)} {ea}"
            else:
                cmd = f"{env_prefix}mios-launch {shlex.quote(name)}"
        elif tool == "launch_app":
            cmd = f"mios-launch {shlex.quote(str(args.get('name', '')))}"
        elif tool == "focus_window":
            title = shlex.quote(str(args.get("title", "")))
            pos = str(args.get("position", "default")).lower()
            if pos in ("as-is",):
                cmd = f"mios-window focus {title}"
            else:
                cmd = (
                    f"mios-window focus {title} && "
                    f"MIOS_LAUNCH_POSITION={shlex.quote(pos)} "
                    f"mios-window {shlex.quote(pos)} {title}"
                )
        elif tool == "move_window":
            title = shlex.quote(str(args.get("title", "")))
            pos   = shlex.quote(str(args.get("position", "center")))
            cmd = f"mios-window {pos} {title}"
        elif tool == "close_window":
            title = shlex.quote(str(args.get("title", "")))
            mode  = "kill" if str(args.get("mode", "graceful")) == "force" else "close"
            cmd = f"mios-window {mode} {title}"
        elif tool == "list_windows":
            cmd = "mios-pc-control window-list"
        elif tool == "screen_layout":
            cmd = "mios-pc-control screen-layout"
        elif tool == "open_url":
            url = shlex.quote(str(args.get("url", "")))
            browser = args.get("browser") or ""
            cmd = f"mios-open-url {url}" + (
                f" {shlex.quote(str(browser))}" if browser else "")
        elif tool == "mios_find":
            cmd = f"mios-find {shlex.quote(str(args.get('name', '')))}"
        elif tool == "mios_apps":
            f = args.get("filter") or ""
            cmd = "mios-apps" + (f" --filter {shlex.quote(str(f))}" if f else "")
        elif tool == "everything_search":
            q = shlex.quote(str(args.get("query", "")))
            n = int(args.get("limit", 10))
            ext = args.get("ext") or ""
            cmd = f"mios-everything -n {n} {q}"
            if ext: cmd += f" -ext {shlex.quote(str(ext))}"
        elif tool == "fs_search":
            q = shlex.quote(str(args.get("query", "")))
            n = int(args.get("limit", 20))
            ext = args.get("ext") or ""
            path = args.get("path") or ""
            type_filter = args.get("type") or ""
            cmd = f"mios-locate -n {n} {q}"
            if ext:  cmd += f" -ext {shlex.quote(str(ext))}"
            if path: cmd += f" -path {shlex.quote(str(path))}"
            if type_filter in ("f", "d"):
                cmd += f" -type {type_filter}"
        elif tool == "system_status":
            cmd = "mios-system-status"
        elif tool == "service_status":
            name = shlex.quote(str(args.get("name", "")))
            cmd = (
                f"echo \"=== is-active ===\"; systemctl is-active {name}; "
                f"echo; echo \"=== status ===\"; "
                f"systemctl --no-pager status {name} | head -20"
            )
        elif tool == "service_restart":
            name = shlex.quote(str(args.get("name", "")))
            cmd = (
                f"systemctl restart {name} && "
                f"echo \"restarted; is-active=$(systemctl is-active {name})\""
            )
        elif tool == "process_list":
            limit = int(args.get("limit", 20))
            sort = str(args.get("sort", "rss")).lower()
            sort_arg = "--sort=-pcpu" if sort == "cpu" else "--sort=-rss"
            filt = str(args.get("filter", "")).strip()
            base = (
                f"ps -eo pid,user,rss,pcpu,comm,args {sort_arg} --no-headers"
            )
            if filt:
                base += f" | grep -i -- {shlex.quote(filt)}"
            cmd = f"{base} | head -{limit}"
        elif tool == "container_status":
            filt = str(args.get("name", "")).strip()
            base = "podman ps -a --format '{{.Names}}\\t{{.Status}}\\t{{.Image}}'"
            if filt:
                base += f" | grep -i -- {shlex.quote(filt)}"
            cmd = base
        elif tool == "container_restart":
            name = shlex.quote(str(args.get("name", "")))
            cmd = (
                f"podman restart {name} && "
                f"podman ps --filter name={name} "
                f"--format '{{.Names}}\\t{{.Status}}'"
            )
        else:
            return json.dumps({"success": False,
                               "stderr": f"router emitted unknown tool {tool!r}"})
        await self._emit(emitter, f"🛠️  {tool}")
        sock_path = os.environ.get("MIOS_LAUNCHER_SOCK",
                                    "/run/mios-launcher/launcher.sock")
        _t0 = time.time()
        _result_payload: Optional[dict] = None
        if not os.path.exists(sock_path):
            _result_payload = {"success": False,
                               "stderr": f"broker socket missing at {sock_path}"}
        else:
            try:
                s = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
                s.settimeout(15.0)
                s.connect(sock_path)
                s.sendall(("CAPTURE_JSON: " + cmd + "\n").encode())
                chunks: list[bytes] = []
                try:
                    while True:
                        buf = s.recv(65536)
                        if not buf: break
                        chunks.append(buf)
                except _socket.timeout:
                    pass
                finally:
                    s.close()
                raw = b"".join(chunks).decode("utf-8", errors="replace").strip()
                try:
                    j = json.loads(raw) if raw else {}
                except json.JSONDecodeError:
                    j = {}
                if not j:
                    _result_payload = {"success": False,
                                       "tool": tool, "args": args,
                                       "output": "", "stderr": raw or "broker: empty response"}
                else:
                    exit_code = int(j.get("exit_code", -1))
                    _result_payload = {
                        "success": exit_code == 0,
                        "tool": tool, "args": args,
                        "output": (j.get("stdout") or "")[:6000],
                        "stderr": (j.get("stderr") or "")[:2000],
                        "exit_code": exit_code,
                    }
            except OSError as e:
                _result_payload = {"success": False, "stderr": f"broker: {e}",
                                   "output": "", "tool": tool, "args": args}
        _latency_ms = int((time.time() - _t0) * 1000)
        _success = bool(_result_payload.get("success"))
        _out_preview = (_result_payload.get("output")
                        or _result_payload.get("stderr") or "")
        _row = {
            "tool": tool,
            "args": args if isinstance(args, dict) else {},
            "result_preview": _out_preview[:500],
            "success": _success,
            "latency_ms": _latency_ms,
        }
        _sid = getattr(self, "_session_id", None)
        if _sid:
            _db_fire(_db_post(
                _db_create("tool_call", _row, now_fields=("ts",)).rstrip(";")
                + f", session = {_sid};"
            ))
        else:
            _db_fire(_db_post(
                _db_create("tool_call", _row, now_fields=("ts",))
            ))
        return json.dumps(_result_payload)

    def _looks_conversational(self, text: str) -> bool:
        if not text:
            return True
        if len(text.strip()) > 80:
            return False
        return bool(self._CONVERSATIONAL_RE.match(text.strip()))

    def _polish_can_skip(self, raw: str) -> bool:
        """Skip polish when raw is already a clean operator-facing answer.

        Skip if EITHER:
          a) Short + no narration markers (the original heuristic), OR
          b) Looks like structured markdown (heading or table block) +
             no narration markers + no known agent-error patterns.

        Case (b) was added after the MiOS System Dashboard
        chat: hermes emitted clean markdown tables (~3300 chars), polish
        ran on it, the CPU model wrapped the whole thing in ```markdown
        and hit POLISH_MAX_TOKENS mid-table -- producing a truncated
        code-fenced answer. The raw was already correct; polish made it
        worse. Now we trust raw markdown when it looks structured."""
        if not raw:
            return True
        if self._NARRATION_MARKERS.search(raw):
            return False
        if self._KNOWN_AGENT_ERROR_RE.search(raw):
            return False
        if len(raw) <= int(self.valves.POLISH_SKIP_SHORT_CHARS):
            return True
        if self._STRUCTURED_MD_RE.search(raw):
            return True
        return False

    async def _polish_via_cpu(
        self,
        user_text: str,
        raw_output: str,
        emitter: Optional[Callable[..., Awaitable[None]]],
        dispatch_ts: Optional[float] = None,
    ) -> str:
        """Output polish/compose: prefers STRUCTURED tool history from
        the Hermes session JSON over the text-blob raw_output (phase-1
        native multi-agent path; see /usr/share/mios/docs/multi-agent-
        architecture.md). Falls back to raw on any failure."""
        if not self.valves.POLISH_ENABLED:
            return raw_output
        if not raw_output or not raw_output.strip():
            return raw_output
        if self._polish_can_skip(raw_output):
            await self._emit(emitter, "✓ clean → skip polish")
            cleaned = self._strip_outer_md_fence(raw_output)
            cleaned = self._strip_reasoning_leaks(cleaned)
            return cleaned or raw_output

        tool_history_json: Optional[str] = None
        if dispatch_ts is not None:
            try:
                msgs = self._load_session_tool_history(dispatch_ts)
                if msgs:
                    tool_history_json = self._render_tool_history_for_compose(msgs)
            except Exception:
                tool_history_json = None
        await self._emit(emitter, "🎨 polish")

        sys_content = self._POLISH_SYSTEM.format(
            user_prompt=user_text[:2000],
            raw_output=raw_output[:12000],
        )
        if tool_history_json:
            sys_content += (
                "\n\n## STRUCTURED TOOL HISTORY (OpenAI-format; authoritative for success/fail)\n"
                "Below is the tool_call + tool_result message history from\n"
                "the Hermes session for this turn. EACH event includes the\n"
                "tool name, arguments, and (when the tool returned JSON\n"
                "with a `success` field per the MiOS verb convention) a\n"
                "boolean `success`. ALSO reflected here: kanban_* tool\n"
                "calls (task state), memory_save/memory_search (durable\n"
                "context), skill_view/skill_manage (agent self-iteration),\n"
                "and any knowledge_search hits (OWUI RAG corpus).\n"
                "\n"
                "PREFER this structured history over the raw text above\n"
                "when reasoning about which steps ran and what they\n"
                "produced. Cite events by tool name + success state in\n"
                "your answer. Steps NOT present in this history did NOT\n"
                "run -- say so explicitly rather than fabricating.\n"
                "\n"
                f"{tool_history_json[:6000]}\n"
            )
        body = {
            "model": self.valves.POLISH_MODEL,
            "messages": [
                {"role": "system", "content": sys_content},
                {"role": "user", "content": "Emit the polished answer now."},
            ],
            "options": {
                "num_gpu": 0,
                "num_predict": int(self.valves.POLISH_MAX_TOKENS),
                "temperature": 0.0,
            },
            "keep_alive": -1,
            "stream": False,
        }
        try:
            timeout = aiohttp.ClientTimeout(total=int(self.valves.POLISH_TIMEOUT_S))
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    self.valves.REFINE_ENDPOINT.rstrip("/") + "/api/chat",
                    data=json.dumps(body).encode(),
                    headers={"Content-Type": "application/json"},
                ) as resp:
                    if resp.status != 200:
                        await self._emit(emitter, f"⚠️ polish HTTP {resp.status} → raw")
                        return raw_output
                    data = await resp.json()
        except asyncio.TimeoutError:
            await self._emit(emitter, "⏱️ polish timeout → raw")
            return raw_output
        except Exception as e:
            await self._emit(emitter, f"⚠️ polish {type(e).__name__} → raw")
            return raw_output

        msg = (data.get("message") or {})
        polished = (msg.get("content") or msg.get("thinking") or "").strip()
        if not polished:
            await self._emit(emitter, "⚠️ polish=∅ → raw")
            return raw_output

        if len(polished) < min(40, len(raw_output) // 10):
            await self._emit(emitter, "⚠️ polish too short → raw")
            return raw_output

        polished = self._strip_outer_md_fence(polished)
        polished = self._strip_reasoning_leaks(polished)

        if (self.valves.CRITIC_ENABLED and tool_history_json
                and int(self.valves.CRITIC_MAX_ITERATIONS) > 0):
            for attempt in range(int(self.valves.CRITIC_MAX_ITERATIONS)):
                verdict = await self._critic_via_cpu(
                    user_text, polished, tool_history_json, emitter,
                )
                if verdict.get("verdict") == "approve":
                    await self._emit(emitter, "🧑‍⚖️ ✓ critic approve")
                    break
                issues = verdict.get("issues") or []
                if not issues:
                    break
                await self._emit(emitter,
                    f"🧑‍⚖️ ✎ critic: {len(issues)} issue(s) → revise")
                polished = await self._recompose_with_critic_feedback(
                    user_text, raw_output, tool_history_json, polished,
                    issues, emitter,
                ) or polished
                polished = self._strip_outer_md_fence(polished)
                polished = self._strip_reasoning_leaks(polished)
        return polished

    _CRITIC_SYSTEM = (
        "You are a Critic Agent. The Compose Agent drafted the answer\n"
        "below. Your job: check the draft against the structured tool\n"
        "history (authoritative ground truth). Spot:\n"
        "  1. Claims of success on tools whose tool_result had success=false\n"
        "  2. Claims of completion for steps NOT present in the history\n"
        "  3. Fabricated specifics (paths/ids/numbers not in any result)\n"
        "  4. Wrong tool attribution (saying tool X was used when Y ran)\n"
        "  5. Missing critical info that IS in the history\n"
        "\n"
        "Output JSON ONLY:\n"
        "  {\"verdict\": \"approve\" | \"revise\",\n"
        "   \"issues\": [\"<one-line issue>\", ...]}\n"
        "\n"
        "If draft accurately reflects the history, verdict=\"approve\",\n"
        "issues=[]. Otherwise verdict=\"revise\", issues=[ specific\n"
        "actionable items the Compose Agent should fix ].\n"
        "Be terse. NO prose preamble.\n"
    )

    async def _critic_via_cpu(
        self,
        user_text: str,
        draft: str,
        tool_history_json: str,
        emitter: Optional[Callable[..., Awaitable[None]]],
    ) -> dict:
        """Critic pass over compose draft. Returns the parsed JSON
        verdict; returns {} on any error (fail-open: skip revision,
        ship the draft)."""
        await self._emit(emitter, "🧑‍⚖️ critic")
        user_msg = (
            f"## OPERATOR ASK\n{user_text[:1500]}\n\n"
            f"## STRUCTURED TOOL HISTORY (authoritative)\n"
            f"{tool_history_json[:6000]}\n\n"
            f"## DRAFT ANSWER (from Compose Agent)\n"
            f"{draft[:4000]}\n\n"
            "## VERDICT (JSON only):"
        )
        body = {
            "model": self.valves.CRITIC_MODEL,
            "messages": [
                {"role": "system", "content": self._CRITIC_SYSTEM},
                {"role": "user",   "content": user_msg},
            ],
            "options": {
                "num_gpu": 0,
                "num_predict": int(self.valves.CRITIC_MAX_TOKENS),
                "temperature": 0.0,
            },
            "format": "json",
            "keep_alive": -1,
            "stream": False,
        }
        try:
            timeout = aiohttp.ClientTimeout(total=int(self.valves.CRITIC_TIMEOUT_S))
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    self.valves.REFINE_ENDPOINT.rstrip("/") + "/api/chat",
                    data=json.dumps(body).encode(),
                    headers={"Content-Type": "application/json"},
                ) as resp:
                    if resp.status != 200:
                        return {}
                    data = await resp.json()
        except (asyncio.TimeoutError, aiohttp.ClientError):
            await self._emit(emitter, "🧑‍⚖️ ⚠ critic err → ship draft")
            return {}
        except Exception:
            return {}
        msg = (data.get("message") or {})
        content = (msg.get("content") or "").strip()
        if not content:
            return {}
        content = re.sub(r"^\s*```(?:json)?\s*\n?", "", content)
        content = re.sub(r"\n?```\s*$", "", content)
        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict):
                _row = {
                    "source": "mios-agent-pipe",
                    "kind": "critic_verdict",
                    "severity": "warn" if parsed.get("issues") else "info",
                    "summary": str(parsed.get("verdict", "?"))[:120],
                    "payload": parsed,
                }
                _db_fire(_db_post(_db_create("event", _row, now_fields=("ts",))))
                return parsed
        except json.JSONDecodeError:
            pass
        return {}

    async def _recompose_with_critic_feedback(
        self,
        user_text: str,
        raw_output: str,
        tool_history_json: str,
        prev_draft: str,
        issues: list,
        emitter: Optional[Callable[..., Awaitable[None]]],
    ) -> str:
        """Re-run compose with the critic's specific issue list fed
        back in. The compose model sees:
          * Original system prompt + structured history
          * The previous draft
          * The critic's list of issues to fix
        Returns the revised answer; empty string on any failure (the
        caller keeps the prev_draft in that case)."""
        sys_content = self._POLISH_SYSTEM.format(
            user_prompt=user_text[:2000],
            raw_output=raw_output[:12000],
        )
        sys_content += (
            "\n\n## STRUCTURED TOOL HISTORY (authoritative)\n"
            f"{tool_history_json[:6000]}\n"
            "\n## CRITIC FEEDBACK on your previous draft\n"
            "Your previous draft had these issues -- FIX each one in\n"
            "the revised answer. Use the structured history above to\n"
            "ground every claim. Output the revised final answer only;\n"
            "no preamble, no 'here is the revised version'.\n\n"
        )
        for i, issue in enumerate(issues, 1):
            sys_content += f"  {i}. {str(issue)[:300]}\n"
        sys_content += f"\n## PREVIOUS DRAFT\n{prev_draft[:6000]}\n"
        body = {
            "model": self.valves.POLISH_MODEL,
            "messages": [
                {"role": "system", "content": sys_content},
                {"role": "user", "content": "Emit the revised final answer."},
            ],
            "options": {
                "num_gpu": 0,
                "num_predict": int(self.valves.POLISH_MAX_TOKENS),
                "temperature": 0.0,
            },
            "keep_alive": -1,
            "stream": False,
        }
        try:
            timeout = aiohttp.ClientTimeout(total=int(self.valves.POLISH_TIMEOUT_S))
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    self.valves.REFINE_ENDPOINT.rstrip("/") + "/api/chat",
                    data=json.dumps(body).encode(),
                    headers={"Content-Type": "application/json"},
                ) as resp:
                    if resp.status != 200:
                        return ""
                    data = await resp.json()
        except (asyncio.TimeoutError, aiohttp.ClientError):
            return ""
        except Exception:
            return ""
        msg = (data.get("message") or {})
        return (msg.get("content") or msg.get("thinking") or "").strip()

    _OUTER_FENCE_RE = re.compile(
        r"^\s*```(?:md|markdown|MD|MARKDOWN)?\s*\n(.*?)(?:\n```\s*)?$",
        re.S,
    )
    _THINK_TAG_RE = re.compile(
        r"<\s*(?:think|reasoning|thinking|cot)\s*>.*?<\s*/\s*(?:think|reasoning|thinking|cot)\s*>",
        re.S | re.I,
    )
    _LEADING_THOUGHT_RE = re.compile(
        r"^\s*(?:thought|thinking|reasoning)\s*\n+", re.I,
    )
    _DETAILS_BLOCK_RE = re.compile(
        r"<\s*details[^>]*>.*?<\s*/\s*details\s*>",
        re.S | re.I,
    )

    def _strip_outer_md_fence(self, text: str) -> str:
        """If the entire response is wrapped in a ```markdown ... ```
        (or bare ```) fence, unwrap so OWUI renders the inner markdown
        as proper markup instead of as a code block."""
        m = self._OUTER_FENCE_RE.match(text)
        if not m:
            return text
        inner = m.group(1).strip()
        if inner.startswith("```"):
            return text
        return inner

    def _strip_reasoning_leaks(self, text: str) -> str:
        """Remove <think>/<reasoning>/<details type="reasoning"> tags
        the polish model occasionally emits despite the system prompt
        rule against narration. Operator-flagged (think)
        + (details). The pipe wraps the AGENT thinking in
        its own <details> block above the polished answer; the polish
        model must NEVER emit its own."""
        text = self._DETAILS_BLOCK_RE.sub("", text)
        text = self._THINK_TAG_RE.sub("", text)
        text = self._LEADING_THOUGHT_RE.sub("", text)
        return text.strip()

    async def _refine_via_cpu(
        self,
        user_text: str,
        emitter: Optional[Callable[..., Awaitable[None]]],
    ) -> str:
        """Call the small CPU refiner. Returns refined text
        on success, or the ORIGINAL on failure / timeout / empty
        (best-effort -- the pipe is OWUI-facing so we never 503 here;
        worst case the unrefined prompt goes through)."""
        if not self.valves.REFINE_ENABLED or not user_text:
            return user_text
        if self.valves.REFINE_SKIP_SHORT and self._looks_conversational(user_text):
            await self._emit(emitter, "💬 → skip refine")
            return user_text

        model = self.valves.REFINE_MODEL
        await self._emit(emitter, "🧠 refine")
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": self._refine_system_rendered},
                {"role": "user", "content": user_text},
            ],
            "options": {
                "num_gpu": 0,           # CPU per operator architecture
                "num_thread": 8,
                "num_predict": int(self.valves.REFINE_MAX_TOKENS),
                "temperature": 0.0,
            },
            "stream": False,
            "keep_alive": -1,           # always-on
        }
        url = self.valves.REFINE_ENDPOINT.rstrip("/") + "/api/chat"
        try:
            timeout = aiohttp.ClientTimeout(total=int(self.valves.REFINE_TIMEOUT_S))
            async with aiohttp.ClientSession(timeout=timeout) as s:
                async with s.post(url, data=json.dumps(payload).encode(),
                                  headers={"Content-Type": "application/json"}) as r:
                    if r.status != 200:
                        await self._emit(emitter,
                            f"⚠️ refine endpoint {r.status} → original")
                        return user_text
                    body = await r.json()
            msg = body.get("message") or {}
            refined = (msg.get("content") or "").strip()
            if not refined:
                refined = (msg.get("thinking") or msg.get("reasoning") or "").strip()
            if not refined:
                await self._emit(emitter, "⚠️ refine=∅ → original")
                return user_text
            await self._emit(emitter,
                f"✓ refine {len(user_text)}c → {len(refined)}c")
            return refined
        except asyncio.TimeoutError:
            await self._emit(emitter,
                f"⏱️ refine timeout {self.valves.REFINE_TIMEOUT_S}s → original")
            return user_text
        except Exception as e:
            await self._emit(emitter,
                f"⚠️ refine {type(e).__name__} → original")
            return user_text

    async def _raw_passthrough(
        self,
        body: dict,
        emitter: Optional[Callable[..., Awaitable[None]]],
    ) -> AsyncGenerator[str, None]:
        """Stream the request straight through WITHOUT refinement,
        thinking-wrap, or polish. Used for OWUI's internal task-
        generation calls (title/tags/follow-up/autocomplete/etc.) that
        expect raw JSON or short labels back.

        Operator architecture "using hermes immediately
        and not using the MiOS-Agent CPU model(s) ... MiOS-Agent is
        the agents driving the operations and retrying the sub-agents
        (MiOS-Hermes, MiOS-OpenCode, etc-etc)". Routes task-gen to
        the CPU model directly (via mios-llm-light /v1/chat/completions, which is
        OpenAI-compat), not to Hermes. Hermes is the heavy
        orchestrator -- spinning it up for trivial title/tag
        generation wastes 30-90s of CPU + delegate-spawn overhead per
        call and was the source of the "hermes immediately" behavior
        the operator flagged."""
        body = dict(body)
        body["model"] = self.valves.REFINE_MODEL
        body["stream"] = True
        body.pop("tools", None)
        body.pop("tool_choice", None)
        body.setdefault("max_tokens", 220)

        headers = {"Content-Type": "application/json"}
        url = self.valves.REFINE_ENDPOINT.rstrip("/") + "/v1/chat/completions"
        timeout = aiohttp.ClientTimeout(total=90, sock_connect=15, sock_read=None)

        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, headers=headers,
                                        data=json.dumps(body).encode()) as resp:
                    if resp.status != 200:
                        err = (await resp.text())[:200]
                        await self._emit(emitter,
                            f"❌ task-gen backend {resp.status}", done=True)
                        yield ""    # OWUI tolerates empty for task-gen
                        return
                    async for raw_line in resp.content:
                        line = raw_line.decode("utf-8", errors="ignore").strip()
                        if not line or not line.startswith("data:"):
                            continue
                        payload = line[5:].strip()
                        if payload == "[DONE]":
                            break
                        try:
                            chunk = json.loads(payload)
                        except json.JSONDecodeError:
                            continue
                        delta = ((chunk.get("choices") or [{}])[0]
                                 .get("delta") or {})
                        piece = delta.get("content") or ""
                        if piece:
                            yield piece
            await self._emit(emitter, "✅ task-gen", done=True)
        except asyncio.TimeoutError:
            await self._emit(emitter, "⏱️ task-gen timeout", done=True)
            yield ""
        except Exception as e:
            await self._emit(emitter,
                f"❌ task-gen {type(e).__name__}: {e}", done=True)
            yield ""

    def _process_buffer(self, buffer: str) -> tuple[str, str]:
        """Pop COMPLETED lines from buffer, transform, return (yieldable, leftover).
        Narration lines get <think>...</think> wrapping; final-answer lines pass through.
        Adjacent narration coalesces into a single <think> block."""
        if not self.valves.COLLAPSE_NARRATION:
            return buffer, ""
        if "\n" not in buffer:
            return "", buffer
        head, _, tail = buffer.rpartition("\n")
        out_parts: list[str] = []
        narration_run: list[str] = []

        def _flush_narration():
            if narration_run:
                joined = "\n".join(narration_run).rstrip()
                out_parts.append(f"<think>{joined}</think>\n")
                narration_run.clear()

        for line in head.splitlines():
            if _is_narration_line(line):
                narration_run.append(line)
            else:
                _flush_narration()
                out_parts.append(line + "\n")
        _flush_narration()
        return "".join(out_parts), tail

    async def _confirm_and_followup(self, raw_text, body, url, headers,
                                    __event_call__, __event_emitter__):
        """GLOBAL ASK-USER native prompt ("OWUI... handle asking with
        prompts" + "for questions and clarifications too, not just coderunning"): if the
        agent emitted a mios_proposed_action (run an action) or a mios_clarification (it
        needs a detail) and OWUI gave us __event_call__, render a NATIVE dialog -- a
        CONFIRMATION for the action (run it on OK) or an INPUT prompt for the question
        (continue with the typed answer). Degrade-open -> yields nothing (the streamed
        text + the text round-trip still work for clients without __event_call__)."""
        if not __event_call__:
            return

        def _extract(marker):
            try:
                i = raw_text.find('{"' + marker + '"')
                if i >= 0:
                    obj, _ = json.JSONDecoder().raw_decode(raw_text[i:])
                    return (obj or {}).get(marker)
            except Exception:
                return None
            return None

        async def _fu_stream(user_msg):
            """Re-request the SAME chat (stable chat_id in body.metadata) with `user_msg`
            appended, and stream the result content. The server's ask-to-run / continued
            turn does the rest."""
            _fu = dict(body)
            _fu["messages"] = list(body.get("messages") or []) + [
                {"role": "assistant", "content": str(raw_text)[:4000]},
                {"role": "user", "content": user_msg}]
            _fu["stream"] = True
            async with aiohttp.ClientSession() as _s:
                async with _s.post(url, headers=headers,
                                   data=json.dumps(_fu).encode()) as _r:
                    if _r.status != 200:
                        yield f"_⚠️ follow-up failed ({_r.status})_"
                        return
                    async for _ln in _r.content:
                        _l = _ln.decode("utf-8", "ignore").strip()
                        if not _l.startswith("data:"):
                            continue
                        _p = _l[5:].strip()
                        if _p == "[DONE]":
                            break
                        try:
                            _ch = json.loads(_p)
                        except Exception:
                            continue
                        _t = ((_ch.get("choices") or [{}])[0].get("delta") or {}).get("content") or ""
                        if _t:
                            yield _t

        pa = _extract("mios_proposed_action")
        if isinstance(pa, dict) and pa.get("tool"):
            try:
                ok = await __event_call__({
                    "type": "confirmation",
                    "data": {"title": "Run this action?",
                             "message": f"MiOS proposes running `{pa.get('tool')}`. "
                                        f"Approve to run it now, or cancel to skip."}})
            except Exception:
                return
            if ok is not True:                 # Cancel / timeout-dict / False
                yield "\n\n> ⏭️ _Action skipped — not approved._"
                try:
                    await self._emit(__event_emitter__, "⏭️ skipped", done=True)
                except Exception:
                    pass
                return
            yield "\n\n"
            try:
                async for _c in _fu_stream("yes"):
                    yield _c
                await self._emit(__event_emitter__, "✅ ran", done=True)
            except Exception as _e:
                yield f"\n\n_⚠️ approval follow-up error: {type(_e).__name__}_"
            return

        cq = _extract("mios_clarification")
        if isinstance(cq, dict) and str(cq.get("question") or "").strip():
            try:
                ans = await __event_call__({
                    "type": "input",
                    "data": {"title": "MiOS needs a detail",
                             "message": str(cq.get("question")).strip(),
                             "placeholder": "Type your answer…"}})
            except Exception:
                return
            if not isinstance(ans, str) or not ans.strip():
                yield "\n\n> ⏭️ _No answer provided._"
                try:
                    await self._emit(__event_emitter__, "⏭️ skipped", done=True)
                except Exception:
                    pass
                return
            yield "\n\n"
            try:
                async for _c in _fu_stream(ans.strip()):
                    yield _c
                await self._emit(__event_emitter__, "✅", done=True)
            except Exception as _e:
                yield f"\n\n_⚠️ clarification follow-up error: {type(_e).__name__}_"
            return

    async def pipe(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __event_emitter__: Optional[Callable[..., Awaitable[None]]] = None,
        __event_call__: Optional[Callable[..., Awaitable[Any]]] = None,
        __metadata__: Optional[dict] = None,
        __task__: Optional[str] = None,
        __tools__: Optional[list] = None,
        __files__: Optional[list] = None,
    ) -> AsyncGenerator[str, None]:
        task_kind = (__task__ or "").strip().lower()
        if not task_kind and isinstance(__metadata__, dict):
            task_kind = str(__metadata__.get("task") or "").strip().lower()
        is_task_gen = task_kind in {
            "title_generation", "tags_generation", "follow_up_generation",
            "autocomplete_generation", "query_generation", "image_prompt_generation",
            "moa_response_generation", "function_calling",
        }

        if is_task_gen:
            await self._emit(__event_emitter__,
                f"⚙️ task-gen ({task_kind})")
            async for chunk in self._raw_passthrough(body, __event_emitter__):
                yield chunk
            return

        try:
            _msgs0 = body.get("messages")
            if isinstance(_msgs0, list):
                for _m in _msgs0:
                    if (isinstance(_m, dict) and _m.get("role") == "system"
                            and isinstance(_m.get("content"), str)):
                        _m["content"] = self._resolve_env_vars(
                            _m["content"], __user__, __metadata__)
        except Exception:
            pass  # env resolution is best-effort; never break the turn

        try:
            _persona = self._compose_persona(__user__)
            if _persona:
                _msgs = body.get("messages")
                if isinstance(_msgs, list):
                    _i = 0
                    while (_i < len(_msgs) and isinstance(_msgs[_i], dict)
                           and _msgs[_i].get("role") == "system"):
                        _i += 1
                    _msgs.insert(_i, {"role": "system", "content": _persona})
                    body["messages"] = _msgs
        except Exception:
            pass  # persona is best-effort; never break the turn

        _chat_id = ""
        if isinstance(__metadata__, dict):
            _chat_id = str(__metadata__.get("chat_id") or "")
        self._session_id = None
        _sess_row = {
            "platform": "owui",
            "owui_chat_id": _chat_id or None,
            "model": self.valves.BACKEND_MODEL,
        }
        try:
            _resp = await _db_post(_db_create(
                "session", _sess_row,
                now_fields=("started_at",),
                extra="RETURN id",
            ))
            if isinstance(_resp, list) and _resp:
                last = _resp[-1] or {}
                rows = last.get("result") or []
                if isinstance(rows, list) and rows:
                    rid = rows[0].get("id")
                    if rid:
                        self._session_id = str(rid)
        except Exception:
            self._session_id = None

        body = dict(body)
        body["model"] = self.valves.BACKEND_MODEL
        body["stream"] = True
        _md = dict(body.get("metadata")) if isinstance(body.get("metadata"), dict) else {}
        if _chat_id:
            _md["chat_id"] = str(_chat_id)[:512]
        if isinstance(__metadata__, dict):
            for _mk in ("message_id", "session_id"):
                _mv = __metadata__.get(_mk)
                if _mv:
                    _md[_mk] = str(_mv)[:512]
        try:
            _env_vars = self._collect_env_vars(__user__, __metadata__, body)
            if _env_vars:
                _md["variables"] = {str(_k)[:64]: str(_v)[:512]
                                    for _k, _v in _env_vars.items()}
        except Exception:
            pass  # env forwarding is best-effort; never break the turn
        try:
            _md.setdefault("variables", {})["SURFACE"] = "owui"
        except Exception:
            pass
        if _md:
            body["metadata"] = _md

        messages = body.get("messages") or []
        _last_user_text = ""
        for i in range(len(messages) - 1, -1, -1):
            if isinstance(messages[i], dict) and messages[i].get("role") == "user":
                raw = messages[i].get("content") or ""
                if isinstance(raw, list):
                    for part in raw:
                        if isinstance(part, dict) and part.get("type") == "text":
                            raw = part.get("text", "")
                            break
                if isinstance(raw, str):
                    _last_user_text = raw
                break

        last_user_idx = -1
        for i in range(len(messages) - 1, -1, -1):
            if isinstance(messages[i], dict) and messages[i].get("role") == "user":
                last_user_idx = i
                break
        if last_user_idx >= 0:
            raw = messages[last_user_idx].get("content") or ""
            if isinstance(raw, list):
                for part in raw:
                    if isinstance(part, dict) and part.get("type") == "text":
                        raw = part.get("text", "")
                        break
                else:
                    raw = ""
            if isinstance(raw, str) and raw.strip():
                refined = await self._refine_via_cpu(raw, __event_emitter__)
                if refined and refined != raw:
                    messages[last_user_idx]["content"] = refined
                    body["messages"] = messages

        headers = {"Content-Type": "application/json"}
        if self.valves.BACKEND_KEY:
            headers["Authorization"] = f"Bearer {self.valves.BACKEND_KEY}"

        try:
            _owui_cid = str((__metadata__ or {}).get("chat_id") or "") if isinstance(__metadata__, dict) else ""
            if _owui_cid:
                _bm = body.get("metadata")
                if not isinstance(_bm, dict):
                    _bm = {}
                    body["metadata"] = _bm
                _bm.setdefault("chat_id", _owui_cid)
        except Exception:
            pass

        _dispatch_ts = time.time()

        total = None if self.valves.TIMEOUT_S <= 0 else self.valves.TIMEOUT_S
        timeout = aiohttp.ClientTimeout(total=total, sock_connect=15, sock_read=None)
        url = self.valves.BACKEND_URL.rstrip("/") + "/chat/completions"

        stop_tail = asyncio.Event()
        tail_task = asyncio.create_task(self._tail_watcher(__event_emitter__, stop_tail))

        raw_buffer = ""
        reasoning_buffer = ""
        any_text = False
        _details_opened = False

        def _open_details_chunk() -> str:
            return (
                f"<details type=\"reasoning\" done=\"true\" "
                f"data-mios-agent=\"hermes\">\n"
                f"<summary>{self.valves.AGENT_THINKING_LABEL}</summary>\n\n"
            )

        _tail_seen = _dispatch_ts
        _reasoning_open = False
        _answer_started = False
        _think_open = False

        def _think_close() -> str:
            """Close the live <think> block exactly once; '' if not open."""
            nonlocal _think_open
            if _think_open:
                _think_open = False
                return "\n</think>\n\n"
            return ""

        def _poll_tail_lines() -> str:
            nonlocal _tail_seen
            picked: list = []
            try:
                with open(HERMES_TAIL_PATH) as _tf:
                    _tp = json.load(_tf)
            except (OSError, json.JSONDecodeError):
                return ""
            for _ev in _tp.get("events", []):
                _ets = _ev.get("ts", 0)
                if _ets > _tail_seen:
                    _tail_seen = _ets
                    _detail = str(_ev.get("detail", "")).strip()
                    if _detail:
                        _icon = _TAIL_ICONS.get(_ev.get("kind", ""), "·")
                        picked.append(f"{_icon} {_detail}")
            return "\n".join(picked)

        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, headers=headers,
                                        data=json.dumps(body).encode()) as resp:
                    if resp.status != 200:
                        err = (await resp.text())[:300]
                        await self._emit(__event_emitter__,
                                         f"❌ backend {resp.status}: {err}",
                                         done=True)
                        yield f"backend error {resp.status}: {err}"
                        return

                    async for raw_line in resp.content:
                        line = raw_line.decode("utf-8", errors="ignore").strip()
                        if not line or not line.startswith("data:"):
                            continue
                        payload_str = line[5:].strip()
                        if payload_str == "[DONE]":
                            break
                        try:
                            chunk = json.loads(payload_str)
                        except json.JSONDecodeError:
                            continue
                        _mios_status = chunk.get("mios_status")
                        if isinstance(_mios_status, dict):
                            emoji = str(_mios_status.get("emoji", ""))
                            label = str(_mios_status.get("label", ""))
                            done  = bool(_mios_status.get("done", False))
                            description = (
                                f"{emoji} {label}".strip() if (emoji or label)
                                else ""
                            )
                            if description:
                                await self._emit(__event_emitter__,
                                                 description, done=done)
                        _sources = chunk.get("mios_sources")
                        if isinstance(_sources, list) and _sources:
                            documents = []
                            metadata = []
                            for src in _sources:
                                title = src.get("title", "") or src.get("url", "")
                                url = src.get("url", "")
                                documents.append(title)
                                metadata.append({
                                    "source": url,
                                    "name": title,
                                    "url": url
                                })
                            if __event_emitter__:
                                await __event_emitter__({
                                    "type": "source",
                                    "data": {
                                        "document": documents,
                                        "metadata": metadata
                                    }
                                })
                        choices = chunk.get("choices") or []
                        if not choices:
                            continue
                        delta = (choices[0].get("delta") or {})
                        _rc = delta.get("reasoning_content") or delta.get("reasoning") or ""
                        if _rc:
                            if not _think_open:
                                _think_open = True
                                yield "<think>\n"
                            yield _rc
                            reasoning_buffer += _rc
                            any_text = True
                            continue
                        text_piece = delta.get("content") or ""
                        if not text_piece:
                            continue
                        if not _answer_started:
                            _answer_started = True
                        _close = _think_close()
                        if _close:
                            yield _close
                        any_text = True
                        raw_buffer += text_piece
                        yield text_piece

            _close = _think_close()
            if _close:
                yield _close

            raw_text = raw_buffer.strip()
            if not raw_text and reasoning_buffer.strip():
                raw_text = reasoning_buffer.strip()

            if not any_text or not raw_text:
                yield "_⚠️ ∅_"
                await self._emit(__event_emitter__, "✅", done=True)
                return

            if not self.valves.POLISH_ENABLED:
                async for _c in self._confirm_and_followup(
                        raw_text, body, url, headers, __event_call__, __event_emitter__):
                    yield _c
                await self._emit(__event_emitter__, "✅", done=True)
                return

            user_text_for_polish = ""
            try:
                last_user = next(
                    (m.get("content") or "") for m in reversed(messages)
                    if isinstance(m, dict) and m.get("role") == "user"
                )
                user_text_for_polish = last_user if isinstance(last_user, str) else ""
            except StopIteration:
                pass

            polished = await self._polish_via_cpu(
                user_text_for_polish, raw_text, __event_emitter__,
                dispatch_ts=_dispatch_ts,
            )
            yield polished

            async for _c in self._confirm_and_followup(
                    raw_text, body, url, headers, __event_call__, __event_emitter__):
                yield _c

            await self._emit(__event_emitter__, "✅", done=True)
        except asyncio.TimeoutError:
            _close = _think_close()
            if _close:
                yield _close
            await self._emit(__event_emitter__,
                             f"⏱️ {self.valves.TIMEOUT_S}s",
                             done=True)
            yield f"\n\n_⏱️ {self.valves.TIMEOUT_S}s_"
        except Exception as e:
            _close = _think_close()
            if _close:
                yield _close
            await self._emit(__event_emitter__,
                             f"❌ {type(e).__name__}: {e}",
                             done=True)
            yield f"\n\n_❌ {type(e).__name__}: {e}_"
        finally:
            stop_tail.set()
            try:
                await asyncio.wait_for(tail_task, timeout=1.0)
            except (asyncio.TimeoutError, Exception):
                pass
