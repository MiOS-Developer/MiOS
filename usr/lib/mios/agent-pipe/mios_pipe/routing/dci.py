# AI-hint: Deliberative Collective Intelligence (DCI) subsystem extracted verbatim from server.py (refactor R6 wave).
# AI-doc: usr/share/doc/mios/manual/routing.md

from __future__ import annotations

from mios_lanes_resolver import _pick_inference_backend, _inference_headers

import asyncio
import json
import logging
import os
import re
from typing import Optional

import httpx

from mios_jsonsalvage import loads_lenient as _loads_lenient
from mios_config import _STACK_MODEL, _LIGHT_BASE, _toml_section

log = logging.getLogger("mios-agent-pipe")

_db_post = None
_db_create = None
_db_fire = None
_apply_outbound_auth = None

def configure(*, db_post=None, db_create=None, db_fire=None,
              apply_outbound_auth=None) -> None:
    """Inject the server.py runtime helpers the DCI flow/critic call back into."""
    global _db_post, _db_create, _db_fire, _apply_outbound_auth
    if db_post is not None:
        _db_post = db_post
    if db_create is not None:
        _db_create = db_create
    if db_fire is not None:
        _db_fire = db_fire
    if apply_outbound_auth is not None:
        _apply_outbound_auth = apply_outbound_auth

DCI_ENABLED = os.environ.get("MIOS_AGENT_PIPE_DCI_ENABLED",
                              "true").lower() not in {"false", "0", "no"}
DCI_MODEL = os.environ.get("MIOS_AGENT_PIPE_DCI_MODEL", _STACK_MODEL)  # = _STACK_MODEL (granite4.1:8b on :8500; gemma4:12b retired -> 404)
DCI_ENDPOINT = os.environ.get(
    "MIOS_AGENT_PIPE_DCI_ENDPOINT", _LIGHT_BASE,  # mios-llm-light (WS-0B: one owned port key)
).rstrip("/")
DCI_TIMEOUT_S = int(os.environ.get("MIOS_AGENT_PIPE_DCI_TIMEOUT_S", "20"))
DCI_MAX_TOKENS = int(os.environ.get("MIOS_AGENT_PIPE_DCI_MAX_TOKENS", "400"))

_DCI_ACTS: dict[str, dict] = {
    "frame":         {"family": "orienting",   "intent": "establish the problem definition"},
    "clarify":       {"family": "orienting",   "intent": "request or supply clarification"},
    "reframe":       {"family": "orienting",   "intent": "restate the problem with a shifted lens"},
    "propose":       {"family": "generative",  "intent": "offer a candidate solution / hypothesis"},
    "extend":        {"family": "generative",  "intent": "build on an existing proposal"},
    "spawn":         {"family": "generative",  "intent": "open a new line of inquiry"},
    "ask":           {"family": "critical",    "intent": "request evidence / probe an assumption"},
    "challenge":     {"family": "critical",    "intent": "contest a claim with a counter-argument"},
    "bridge":        {"family": "integrative", "intent": "connect two distinct ideas"},
    "synthesize":    {"family": "integrative", "intent": "merge disparate views into a coherent whole"},
    "recall":        {"family": "integrative", "intent": "surface prior context / decisions"},
    "ground":        {"family": "epistemic",   "intent": "anchor a claim to verifiable evidence"},
    "update":        {"family": "epistemic",   "intent": "revise a prior belief in light of new info"},
    "recommend":     {"family": "decisional",  "intent": "advance a specific action / decision"},
}

_DCI_ACT_NAMES = sorted(_DCI_ACTS.keys())

_DCI_ACT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "act":        {"type": "string", "enum": _DCI_ACT_NAMES,
                       "description": "Which of the 14 DCI epistemic acts you are emitting."},
        "content":    {"type": "string",
                       "description": "Free-form payload, 1-3 sentences. Mirror the chat language."},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0,
                       "description": "0.0 = highly uncertain; 1.0 = certain."},
        "targets":    {"type": "array", "items": {"type": "string"},
                       "description": "Optional list of prior act-ids this act addresses (Phase B.2 tension tracking)."},
    },
    "required": ["act", "content", "confidence"],
}

_DCI_CRITIC_SYSTEM = (
    "You are a DCI Challenger agent (Deliberative Collective\n"
    "Intelligence, arxiv 2603.11781). Examine the operator's prompt\n"
    "and the agent's tool_result envelope. Emit ONE typed epistemic\n"
    "act as structured JSON. No free-form prose.\n"
    "\n"
    "Available acts (pick ONE):\n"
    + "\n".join(f"  - {a}: {info['intent']} (family: {info['family']})"
                for a, info in sorted(_DCI_ACTS.items())) +
    "\n\n"
    "Output schema (JSON ONLY):\n"
    '  {"act":"<one of the 14>",\n'
    '   "content":"<1-3 sentences in the chat language>",\n'
    '   "confidence":<0.0-1.0>,\n'
    '   "targets":[<optional act-ids you address>]}\n'
    "\n"
    "Heuristic for picking an act (Challenger persona):\n"
    "- If the agent's result looks WRONG / unjustified -> challenge\n"
    "  with a specific counter-argument.\n"
    "- If a step seems UNJUSTIFIED -> ask for evidence.\n"
    "- If the result is well-grounded -> ground (acknowledge +\n"
    "  cite the evidence).\n"
    "- If the result OBSOLETES a prior decision -> update.\n"
    "- If unsure -> ask (low confidence is fine; emit it as a\n"
    "  number).\n"
    "\n"
    "Write any text in ENGLISH by default (another language only if the\n"
    "user's own message is clearly in it). Output JSON ONLY -- no preamble,\n"
    "no markdown."
)

DCI_FLOW_ENABLED = str(
    os.environ.get("MIOS_AGENT_PIPE_DCI_FLOW_ENABLED")
    or _toml_section("dci").get("flow_enabled", "false")
).strip().lower() not in {"false", "0", "no"}
DCI_FLOW_R_MAX = int(os.environ.get("MIOS_AGENT_PIPE_DCI_FLOW_R_MAX", "3"))
DCI_FLOW_TIMEOUT_S = int(os.environ.get(
    "MIOS_AGENT_PIPE_DCI_FLOW_TIMEOUT_S", "20"))

_PERSONA_ALLOWED_ACTS: dict[str, set] = {
    "framer":     {"frame", "clarify", "reframe"},
    "explorer":   {"propose", "extend", "spawn"},
    "challenger": {"ask", "challenge"},
    "integrator": {"bridge", "synthesize", "recall",
                   "ground", "update", "recommend"},
}

_DCI_DISSENT_ACTS = frozenset(_PERSONA_ALLOWED_ACTS["challenger"])

def _persona_prompt(role: str, role_desc: str, allowed_acts: set) -> str:
    """Build a hard-constraint persona prompt: MUST emit one of the
    listed acts, with each act's intent inline so the model picks
    the right one for its cognitive role."""
    allowed_lines = "\n".join(
        f"  - {a}: {_DCI_ACTS[a]['intent']}"
        for a in sorted(allowed_acts)
    )
    return (
        f"You are the DCI {role} persona (arxiv 2603.11781).\n"
        f"Your job: {role_desc}\n"
        "\n"
        "You MUST emit EXACTLY ONE act from this list. Any other\n"
        "act will be REJECTED and your contribution to this round\n"
        "will be lost:\n"
        f"{allowed_lines}\n"
        "\n"
        "Write the content in ENGLISH by default (another language only if\n"
        "the operator's own message is clearly in it). Output JSON ONLY shaped:\n"
        '  {"act":"<name>","content":"<1-3 sentences>",'
        '"confidence":<0-1>,"targets":[]}\n'
        "No preamble, no markdown, no commentary."
    )

_DCI_FRAMER_SYSTEM = _persona_prompt(
    "Framer",
    "establish the problem scope + clarify ambiguity. Read the "
    "operator's prompt + the envelope and decide what we're really "
    "deciding about.",
    _PERSONA_ALLOWED_ACTS["framer"],
)

_DCI_EXPLORER_SYSTEM = _persona_prompt(
    "Explorer",
    "expand the option space. What alternative paths or framings has "
    "the Framer missed? What is the second-best option here?",
    _PERSONA_ALLOWED_ACTS["explorer"],
)

_DCI_CHALLENGER_SYSTEM = _persona_prompt(
    "Challenger",
    "interrogate the proposals + the envelope. What evidence is "
    "thin? What assumption looks shaky? Pick the most consequential "
    "weak point and contest it -- or ask for evidence if it's "
    "ambiguous.",
    _PERSONA_ALLOWED_ACTS["challenger"],
)

_DCI_INTEGRATOR_SYSTEM = _persona_prompt(
    "Integrator",
    "synthesize the Framer / Explorer / Challenger contributions "
    "into a coherent next step. When their views diverge, EXPLICITLY "
    "name the tension in your `content` -- do NOT paper over "
    "disagreement. On the final round emit `recommend` to close.",
    _PERSONA_ALLOWED_ACTS["integrator"],
)

_DCI_PERSONAS = [
    ("framer",     _DCI_FRAMER_SYSTEM),
    ("explorer",   _DCI_EXPLORER_SYSTEM),
    ("challenger", _DCI_CHALLENGER_SYSTEM),
    ("integrator", _DCI_INTEGRATOR_SYSTEM),
]

async def _dci_call_persona(
    persona_name: str,
    system_prompt: str,
    user_text: str,
    workspace: dict,
) -> Optional[dict]:
    """One persona round: gives the persona the workspace state +
    asks for ONE typed act. Returns the parsed act dict (with
    persona name appended) or None on any error."""
    workspace_summary = json.dumps(workspace, indent=2, default=str)[:3000]
    user_msg = (
        f"OPERATOR PROMPT:\n{user_text[:1500]}\n\n"
        f"CURRENT WORKSPACE STATE:\n{workspace_summary}\n\n"
        f"You are the {persona_name}. Emit ONE typed epistemic act now:"
    )
    payload = {
        "model": DCI_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_msg},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.0,
        "max_tokens": DCI_MAX_TOKENS,
        "stream": False,
    }
    endpoint, payload["model"] = await _pick_inference_backend(
        DCI_ENDPOINT, DCI_MODEL, endpoint_env=("MIOS_AGENT_PIPE_DCI_ENDPOINT",),
        model_env=("MIOS_AGENT_PIPE_DCI_MODEL",))
    try:
        async with httpx.AsyncClient(timeout=DCI_FLOW_TIMEOUT_S) as s:
            r = await s.post(
                f"{endpoint}/chat/completions",
                json=payload,
                headers=_inference_headers(endpoint),
            )
            if r.status_code != 200:
                return None
            body = r.json()
    except (httpx.HTTPError, asyncio.TimeoutError):
        return None
    except Exception as e:
        log.warning("dci flow %s error: %s", persona_name, e)
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
        parsed = _loads_lenient(content)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    act = parsed.get("act")
    if act not in _DCI_ACTS:
        return None
    allowed = _PERSONA_ALLOWED_ACTS.get(persona_name, set(_DCI_ACTS.keys()))
    if act not in allowed:
        log.info("dci %s emitted %s (not in family); rejecting",
                 persona_name, act)
        return None
    try:
        parsed["confidence"] = max(0.0, min(1.0, float(parsed.get("confidence", 0.5))))
    except (TypeError, ValueError):
        parsed["confidence"] = 0.5
    parsed["persona"] = persona_name
    parsed["family"] = _DCI_ACTS[act]["family"]
    return parsed

async def run_dci_flow(
    user_text: str,
    envelope: dict,
    *,
    session_id: Optional[str] = None,
    r_max: Optional[int] = None,
) -> dict:
    if r_max is None:
        r_max = DCI_FLOW_R_MAX
    workspace: dict = {
        "user_prompt":  user_text[:600],
        "envelope":     {
            "tool":    (envelope.get("tool_call") or {}).get("function", {}).get("name"),
            "args":    (envelope.get("tool_call") or {}).get("function", {}).get("arguments"),
            "success": (envelope.get("tool_result") or {}).get("success"),
            "output":  ((envelope.get("tool_result") or {}).get("output") or "")[:500],
        },
        "frames":       [],    # Framer acts
        "proposals":    [],    # Explorer acts
        "challenges":   [],    # Challenger acts
        "syntheses":    [],    # Integrator non-final acts
    }
    rounds: list = []
    decision: Optional[dict] = None
    for r_idx in range(1, r_max + 1):
        round_acts = []
        for persona_name, system_prompt in _DCI_PERSONAS:
            act = await _dci_call_persona(
                persona_name, system_prompt, user_text, workspace,
            )
            if not act:
                continue
            round_acts.append(act)
            family = act.get("family", "")
            if family == "orienting":
                workspace["frames"].append(act)
            elif family == "generative":
                workspace["proposals"].append(act)
            elif family == "critical":
                workspace["challenges"].append(act)
            elif family in ("integrative", "epistemic"):
                workspace["syntheses"].append(act)
            elif family == "decisional":
                decision = act
            severity = "warn" if act["act"] in _DCI_DISSENT_ACTS and act["confidence"] >= DCI_FLOW_TRIGGER_CONF else "info"
            _db_fire(_db_post(_db_create("event", {
                "source": "mios-agent-pipe",
                "kind": "dci_act",
                "severity": severity,
                "summary": f"r{r_idx}/{persona_name}/{act['act']} ({act['confidence']:.2f})",
                "act_type": act["act"],
                "payload": {
                    "round": r_idx,
                    "persona": persona_name,
                    "act": act["act"],
                    "family": act["family"],
                    "confidence": act["confidence"],
                    "content": (act.get("content") or "")[:500],
                    "targets": act.get("targets") or [],
                    "session": session_id,
                },
            }, now_fields=("ts",))))
        rounds.append(round_acts)
        if decision is not None:
            break
    if decision is None:
        forced = await _dci_call_persona(
            "integrator",
            _DCI_INTEGRATOR_SYSTEM + (
                "\n\nIMPORTANT: This is the FINAL round. You MUST "
                "emit `recommend` as your act -- not `synthesize`, "
                "not `bridge`. The workspace has reached R_max; "
                "the deliberation MUST close with a decision."
            ),
            user_text, workspace,
        )
        if forced and forced.get("act") == "recommend":
            decision = forced
    converged = decision is not None
    dissents = [
        a for a in workspace["challenges"]
        if a.get("confidence", 0.0) >= DCI_FLOW_TRIGGER_CONF
    ]
    for d in dissents:
        await _db_post(_db_create("event", {
            "source": "mios-agent-pipe",
            "kind": "dissent",
            "severity": "warn",
            "summary": f"unresolved {d['act']} ({d['confidence']:.2f})",
            "act_type": d["act"],
            "payload": {
                "persona": d.get("persona"),
                "content": (d.get("content") or "")[:500],
                "session": session_id,
            },
        }, now_fields=("ts",)))
    if decision is None and workspace["syntheses"]:
        decision = dict(workspace["syntheses"][-1])
        decision["fallback"] = True
    return {
        "decision": decision,
        "rounds": rounds,
        "dissents": dissents,
        "converged": converged,
        "rounds_used": len(rounds),
        "workspace": {
            "frames":     len(workspace["frames"]),
            "proposals":  len(workspace["proposals"]),
            "challenges": len(workspace["challenges"]),
            "syntheses":  len(workspace["syntheses"]),
        },
    }

DCI_FLOW_TRIGGER_CONF = float(os.environ.get(
    "MIOS_AGENT_PIPE_DCI_FLOW_TRIGGER_CONF", "0.7"))

async def critic_then_maybe_flow(
    user_text: str,
    envelope: dict,
    *,
    session_id: Optional[str] = None,
) -> None:
    if not (DCI_ENABLED or DCI_FLOW_ENABLED):
        return
    act = await dci_critic_pass(user_text, envelope, session_id=session_id)
    if not act:
        return
    if (DCI_FLOW_ENABLED
            and act.get("act") in _DCI_DISSENT_ACTS
            and act.get("confidence", 0.0) >= DCI_FLOW_TRIGGER_CONF):
        result = await run_dci_flow(
            user_text, envelope,
            session_id=session_id, r_max=2,
        )
        if result.get("dissents") and session_id:
            taint_row = {
                "tool": "dci_dissent",
                "args": {
                    "dissent_count": len(result["dissents"]),
                    "trigger_act": act["act"],
                    "trigger_conf": act["confidence"],
                },
                "result_preview": (
                    f"DCI flow surfaced {len(result['dissents'])} "
                    f"unresolved dissent(s) -- session tainted"
                ),
                "success": False,
                "latency_ms": 0,
                "tainted": True,
                "taint_reason": (
                    f"dci_dissent:{len(result['dissents'])}_"
                    f"unresolved_after_r{result.get('rounds_used',0)}"
                ),
            }
            await _db_post(
                _db_create("tool_call", taint_row, now_fields=("ts",)).rstrip(";")
                + f", session = {session_id};"
            )

async def dci_critic_pass(
    user_text: str,
    envelope: dict,
    *,
    session_id: Optional[str] = None,
) -> Optional[dict]:
    if not DCI_ENABLED or not user_text:
        return None
    compact = {
        "tool":       (envelope.get("tool_call") or {}).get("function", {}).get("name"),
        "args":       (envelope.get("tool_call") or {}).get("function", {}).get("arguments"),
        "success":    (envelope.get("tool_result") or {}).get("success"),
        "output":    ((envelope.get("tool_result") or {}).get("output") or "")[:600],
        "stderr":    ((envelope.get("tool_result") or {}).get("stderr") or "")[:200],
        "exit_code":  (envelope.get("tool_result") or {}).get("exit_code"),
    }
    user_msg = (
        f"OPERATOR PROMPT:\n{user_text[:1500]}\n\n"
        f"AGENT ENVELOPE:\n{json.dumps(compact, indent=2, default=str)}\n\n"
        "Emit ONE typed epistemic act now:"
    )
    payload = {
        "model": DCI_MODEL,
        "messages": [
            {"role": "system", "content": _DCI_CRITIC_SYSTEM},
            {"role": "user",   "content": user_msg},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.0,
        "max_tokens": DCI_MAX_TOKENS,
        "stream": False,
    }
    endpoint, payload["model"] = await _pick_inference_backend(
        DCI_ENDPOINT, DCI_MODEL, endpoint_env=("MIOS_AGENT_PIPE_DCI_ENDPOINT",),
        model_env=("MIOS_AGENT_PIPE_DCI_MODEL",))
    try:
        async with httpx.AsyncClient(timeout=DCI_TIMEOUT_S) as s:
            _dci_hdrs = {"Content-Type": "application/json"}
            _apply_outbound_auth(_dci_hdrs, endpoint)
            r = await s.post(
                f"{endpoint}/chat/completions",
                json=payload,
                headers=_dci_hdrs,
            )
            if r.status_code != 200:
                return None
            body = r.json()
    except (httpx.HTTPError, asyncio.TimeoutError):
        return None
    except Exception as e:
        log.warning("dci_critic unexpected error: %s", e)
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
        parsed = _loads_lenient(content)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    act = parsed.get("act")
    if act not in _DCI_ACTS:
        return None
    try:
        parsed["confidence"] = max(0.0, min(1.0, float(parsed.get("confidence", 0.5))))
    except (TypeError, ValueError):
        parsed["confidence"] = 0.5
    family = _DCI_ACTS[act]["family"]
    severity = "warn" if act in _DCI_DISSENT_ACTS and parsed["confidence"] >= DCI_FLOW_TRIGGER_CONF else "info"
    row = {
        "source":  "mios-agent-pipe",
        "kind":    "dci_act",
        "severity": severity,
        "summary": f"{family}/{act} ({parsed['confidence']:.2f})",
        "act_type": act,
        "payload": {
            "act":         act,
            "family":      family,
            "confidence":  parsed.get("confidence"),
            "content":     (parsed.get("content") or "")[:600],
            "targets":     parsed.get("targets") or [],
            "session":     session_id,
        },
    }
    _db_fire(_db_post(_db_create("event", row, now_fields=("ts",))))
    return parsed

