# AI-hint: Council/swarm fan-out SELECTION (refactor R3 wave; de-hardcoded per operator "the scoring IS a hardcode in and of itself").
# AI-doc: usr/share/doc/mios/manual/routing.md

from __future__ import annotations

import json
import logging
import re
from typing import Optional

import httpx

from mios_config import _MICRO_MODEL, _MICRO_ENDPOINT
from mios_jsonsalvage import loads_lenient as _loads_lenient

log = logging.getLogger("mios-agent-pipe")

_AGENT_REGISTRY: dict = {}
_DISPATCH_CFG: dict = {}
_depth_exhausted = None
_dispatch_depth = None
_lane_sem_key = None
_dedup_pool_by_target = None
_over_global_ceiling = None
_agent_lane = None
_agent_skill_tags = None
MAX_DISPATCH_DEPTH = 2
COUNCIL_MAX_DEFAULT = 4
ADMIT_ENABLE = False
ROUTE_ON_CARD_SKILLS = False
_db_create = None
_db_post = None
_db_fire = None

def configure(*, agent_registry=None, dispatch_cfg=None, depth_exhausted=None,
              dispatch_depth=None, lane_sem_key=None, dedup_pool_by_target=None,
              over_global_ceiling=None, agent_lane=None, agent_skill_tags=None,
              max_dispatch_depth=None, council_max_default=None,
              admit_enable=None, route_on_card_skills=None,
              db_create=None, db_post=None, db_fire=None) -> None:
    global _AGENT_REGISTRY, _DISPATCH_CFG, _depth_exhausted, _dispatch_depth
    global _lane_sem_key, _dedup_pool_by_target, _over_global_ceiling
    global _agent_lane, _agent_skill_tags, MAX_DISPATCH_DEPTH
    global COUNCIL_MAX_DEFAULT, ADMIT_ENABLE, ROUTE_ON_CARD_SKILLS
    global _db_create, _db_post, _db_fire
    if agent_registry is not None:
        _AGENT_REGISTRY = agent_registry
    if dispatch_cfg is not None:
        _DISPATCH_CFG = dispatch_cfg
    if depth_exhausted is not None:
        _depth_exhausted = depth_exhausted
    if dispatch_depth is not None:
        _dispatch_depth = dispatch_depth
    if lane_sem_key is not None:
        _lane_sem_key = lane_sem_key
    if dedup_pool_by_target is not None:
        _dedup_pool_by_target = dedup_pool_by_target
    if over_global_ceiling is not None:
        _over_global_ceiling = over_global_ceiling
    if agent_lane is not None:
        _agent_lane = agent_lane
    if agent_skill_tags is not None:
        _agent_skill_tags = agent_skill_tags
    if max_dispatch_depth is not None:
        MAX_DISPATCH_DEPTH = max_dispatch_depth
    if council_max_default is not None:
        COUNCIL_MAX_DEFAULT = council_max_default
    if admit_enable is not None:
        ADMIT_ENABLE = admit_enable
    if route_on_card_skills is not None:
        ROUTE_ON_CARD_SKILLS = route_on_card_skills
    if db_create is not None:
        _db_create = db_create
    if db_post is not None:
        _db_post = db_post
    if db_fire is not None:
        _db_fire = db_fire

def _opted_out(c: dict) -> bool:
    """Explicit fan-out opt-out. The telemetry daemon-agent sets this: it ignores
    the prompt and always returns a system digest, so it would flood synthesis."""
    return c.get("fanout") is False or \
        str(c.get("fanout", "")).lower() in {"false", "no", "0"}

def _eligible_candidates(primary_name: str, live_agents: Optional[set],
                         include_research: bool) -> list:
    out = []
    for name, cfg in _AGENT_REGISTRY.items():
        if name == primary_name or _opted_out(cfg):
            continue
        if live_agents is not None and name not in live_agents:   # OUTAGE prune
            continue
        if not include_research and cfg.get("research_only"):
            continue
        out.append((name, cfg))
    return out

def _council_fallback(primary_name: str, candidates: list, want: int) -> list:
    primary_lane = _lane_sem_key(_AGENT_REGISTRY.get(primary_name) or {})
    pool = sorted(candidates, key=lambda nc: (
        0 if _lane_sem_key(nc[1]) != primary_lane else 1, nc[0]))
    keep = set(_dedup_pool_by_target([n for n, _c in pool]))
    pool = [(n, c) for (n, c) in pool if n in keep]
    return pool if want <= 0 else pool[:want]

def _published_skill_lines(cfg: dict) -> list:
    out: list = []
    skills = cfg.get("card_skills")
    if not isinstance(skills, (list, tuple)):
        return out
    for s in skills:
        if not isinstance(s, dict):
            continue
        bits = []
        nm = str(s.get("name") or s.get("id") or "").strip()
        if nm:
            bits.append(nm)
        desc = str(s.get("description") or "").strip()
        if desc:
            bits.append(desc)
        tags = s.get("tags")
        if isinstance(tags, (list, tuple)):
            tg = ", ".join(str(t).strip() for t in tags if str(t).strip())
            if tg:
                bits.append(tg)
        if bits:
            out.append(": ".join(bits))
    return out

def _agent_card(name: str, cfg: dict) -> str:
    role = str(cfg.get("role") or cfg.get("job") or "").strip()
    strengths = cfg.get("strengths")
    if isinstance(strengths, (list, tuple)):
        strengths = ", ".join(str(s) for s in strengths)
    tags = ""
    try:
        tags = ", ".join(_agent_skill_tags(cfg) or [])
    except Exception:  # noqa: BLE001 -- card is best-effort
        pass
    parts = [p for p in (role, str(strengths or "").strip(), tags) if p]
    if ROUTE_ON_CARD_SKILLS:
        parts.extend(_published_skill_lines(cfg))
    return f"{name} -- {' | '.join(parts)}" if parts else name

def _emit_route_event(primary_name: str, secondaries: list) -> None:
    """Best-effort: record the fan-out routing DECISION in the event table when
    card-skills routing is active (FED-G7 / task_51). No-op when the feature is off or
    the DB writers are not injected (standalone import / unit tests) -- so the
    default byte-identical path writes nothing new."""
    if not ROUTE_ON_CARD_SKILLS or _db_create is None:
        return
    try:
        summary = (primary_name + " + " + ", ".join(secondaries)) if secondaries \
            else (primary_name + " (solo)")
        _db_fire(_db_post(_db_create("event", {
            "source": "mios-agent-pipe",
            "kind": "fanout_route",
            "severity": "info",
            "summary": summary[:120],
            "payload": {"primary": primary_name, "secondaries": secondaries,
                        "route_on_card_skills": True},
        }, now_fields=("ts",))))
    except Exception as e:  # noqa: BLE001 -- telemetry is best-effort; never block routing
        log.debug("fanout route-event emit failed: %s", e)

async def _model_select(corpus: str, candidates: list, want: int) -> Optional[list]:
    mode = str(_DISPATCH_CFG.get("fanout_select_mode", "model")).strip().lower()
    if mode != "model" or not candidates or not corpus.strip():
        return None
    names = [n for n, _c in candidates]
    cards = "\n".join(f"- {_agent_card(n, c)}" for n, c in candidates)
    sys_prompt = (
        "You choose which specialist helper agents (if any) should run CONCURRENTLY "
        "with the primary agent to improve THIS task's answer. You are given the task "
        "plan and the available agents with their declared capabilities. Pick ONLY "
        "agents whose declared capabilities are genuinely relevant to the task; pick "
        "none if no specialist adds value. Do not invent names. Respond with ONLY a "
        "JSON array of the chosen agent names (a subset of the listed names), e.g. "
        '["name-a","name-b"], or [] for none.')
    user = f"TASK PLAN:\n{corpus.strip()[:2000]}\n\nAVAILABLE AGENTS:\n{cards}\n\nChosen (JSON array):"
    timeout = float(_DISPATCH_CFG.get("fanout_select_timeout_s", 8) or 8)
    base = _MICRO_ENDPOINT.rstrip("/")
    url = base + ("" if base.endswith("/chat/completions") else "/chat/completions")
    body = {
        "model": _MICRO_MODEL,
        "messages": [{"role": "system", "content": sys_prompt},
                     {"role": "user", "content": user}],
        "temperature": 0,
        "max_tokens": 120,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(url, json=body)
        if r.status_code != 200:
            return None
        content = (((r.json().get("choices") or [{}])[0].get("message") or {})
                   .get("content") or "")
        picked = _loads_lenient(content)
        if isinstance(picked, dict):   # tolerate {"agents": [...]}
            picked = picked.get("agents") or picked.get("names") or []
        if not isinstance(picked, list):
            m = re.search(r"\[.*\]", content, re.DOTALL)
            if m:
                try:
                    picked = json.loads(m.group(0))
                except (ValueError, TypeError):
                    picked = None
        if not isinstance(picked, list):
            return None
        allow = set(names)
        chosen = []
        for x in picked:
            xn = str(x).strip()
            if xn in allow and xn not in chosen:
                chosen.append(xn)
        return chosen[:want] if want > 0 else chosen
    except Exception as e:  # noqa: BLE001 -- relevance is best-effort; degrade open
        log.debug("fanout model-select failed (-> council fallback): %s", e)
        return None

async def _pick_fanout_agents(primary_name: str,
                              refined: Optional[dict],
                              *, force_council: bool = False,
                              live_agents: Optional[set] = None,
                              include_research: bool = False) -> list:

    if _depth_exhausted():
        log.info("fanout: dispatch depth %d >= %d -> single-agent (degrade-closed)",
                 _dispatch_depth(), MAX_DISPATCH_DEPTH)
        return []

    candidates = _eligible_candidates(primary_name, live_agents, include_research)

    if force_council:
        primary_lane = _lane_sem_key(_AGENT_REGISTRY.get(primary_name) or {})
        return sorted(candidates, key=lambda nc: (
            0 if _lane_sem_key(nc[1]) != primary_lane else 1, nc[0]))

    if not _DISPATCH_CFG.get("enable") or _DISPATCH_CFG.get("fanout_max", 1) <= 1:
        return []
    want = _DISPATCH_CFG["fanout_max"] - 1

    if _DISPATCH_CFG.get("mode") == "council":
        sel = _council_fallback(primary_name, candidates, COUNCIL_MAX_DEFAULT)
        if ADMIT_ENABLE and COUNCIL_MAX_DEFAULT != 1 and _over_global_ceiling():
            _cmax = max(1, (COUNCIL_MAX_DEFAULT if COUNCIL_MAX_DEFAULT > 0
                            else len(sel)) // 2)
            sel = sel[:_cmax]
        return sel

    corpus = ""
    if isinstance(refined, dict):
        corpus = " ".join(str(refined.get(k, "")) for k in
                          ("intended_outcome", "refined_text", "target_agent"))
        for k in ("hint_tools", "hint_skills"):
            v = refined.get(k)
            if isinstance(v, list):
                corpus += " " + " ".join(str(x) for x in v)
    if not candidates or not corpus.strip():
        return []

    chosen_names = await _model_select(corpus, candidates, want)
    if chosen_names is None:
        sel = _council_fallback(primary_name, candidates, want)
        _emit_route_event(primary_name, [n for n, _c in sel])
        return sel
    by_name = dict(candidates)
    sel = [(n, by_name[n]) for n in chosen_names if n in by_name]
    _emit_route_event(primary_name, [n for n, _c in sel])
    return sel
