# AI-hint: Anti-fabrication POLISH/VERITY cluster extracted verbatim from server.py (refactor R6 wave).
# AI-doc: usr/share/doc/mios/manual/lifecycle.md

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from typing import Optional

import httpx

from mios_jsonsalvage import loads_lenient as _loads_lenient
from mios_grounding import _env_grounding
from mios_config import (  # SSOT mios.toml reader + model-call scalars (sibling, pure)
    _toml_section, ROUTER_MODEL, PLANNER_ENDPOINT, PLANNER_TIMEOUT_S)

log = logging.getLogger("mios-agent-pipe")

REFINE_TIMEOUT_S = int(os.environ.get("MIOS_REFINE_TIMEOUT_SECONDS", "30"))
REFINE_ENDPOINT = ""
REFINE_MODEL = ""
_WEB_ENRICH_VERBS: set = set()
WEB_RESEARCH_SEARCH_TIMEOUT = float(
    os.environ.get("MIOS_WEB_RESEARCH_SEARCH_TIMEOUT_S", "30"))
POLISH_ENABLED = False
_POLISH_SYSTEM = ""
POLISH_ENDPOINT = ""
POLISH_MODEL = ""
POLISH_MAX_TOKENS = 800
POLISH_TIMEOUT_S = 15
ASK_CLARIFY_JUDGE_ENABLE = False

_ABBR_DEFAULT = ("approx.", "Approx.", "e.g.", "i.e.", "vs.", "etc.", "U.S.",
                 "U.K.", "a.m.", "p.m.", "No.", "Inc.", "Co.", "Ltd.", "St.", "Mt.")

def _ssot_abbreviations() -> tuple:
    """Sentence-split abbreviation exceptions from mios.toml [verity], or the
    Latin default when the key/file is absent. Script-neutral splitter + this
    SSOT exception list = no baked word screen frozen in code."""
    try:
        _v = _toml_section("verity").get("sentence_abbreviations")
        if isinstance(_v, (list, tuple)) and _v:
            return tuple(str(x) for x in _v if str(x).strip())
    except Exception:  # noqa: BLE001 -- best-effort; fall to the Latin default
        pass
    return _ABBR_DEFAULT

_ABBR = _ssot_abbreviations()

_polish_post = None
_recent_tool_history = None
_format_tool_history = None
_recent_satisfaction_verdicts = None
_format_satisfaction_block = None
_store_knowledge = None
_write_skill_md_fire = None
_proposal_var = None

def configure(*, refine_timeout_s=None, refine_endpoint=None, refine_model=None,
              web_enrich_verbs=None, web_research_search_timeout=None,
              polish_enabled=None, polish_system=None, polish_endpoint=None,
              polish_model=None, polish_max_tokens=None, polish_timeout_s=None,
              ask_clarify_judge_enable=None, polish_post=None,
              recent_tool_history=None, format_tool_history=None,
              recent_satisfaction_verdicts=None, format_satisfaction_block=None,
              store_knowledge=None, write_skill_md_fire=None,
              proposal_var=None,
              abbreviations=None) -> None:
    """Inject the server.py constants + runtime helpers the verity/polish
    cluster reads and calls back into. Called once at import time, after every
    injected symbol is defined."""
    global REFINE_TIMEOUT_S, REFINE_ENDPOINT, REFINE_MODEL, _WEB_ENRICH_VERBS
    global WEB_RESEARCH_SEARCH_TIMEOUT, POLISH_ENABLED, _POLISH_SYSTEM
    global POLISH_ENDPOINT, POLISH_MODEL, POLISH_MAX_TOKENS, POLISH_TIMEOUT_S
    global ASK_CLARIFY_JUDGE_ENABLE
    global _polish_post, _recent_tool_history, _format_tool_history
    global _recent_satisfaction_verdicts, _format_satisfaction_block
    global _store_knowledge, _write_skill_md_fire
    global _proposal_var, _ABBR
    if refine_timeout_s is not None:
        REFINE_TIMEOUT_S = refine_timeout_s
    if refine_endpoint is not None:
        REFINE_ENDPOINT = refine_endpoint
    if refine_model is not None:
        REFINE_MODEL = refine_model
    if web_enrich_verbs is not None:
        _WEB_ENRICH_VERBS = web_enrich_verbs
    if web_research_search_timeout is not None:
        WEB_RESEARCH_SEARCH_TIMEOUT = web_research_search_timeout
    if polish_enabled is not None:
        POLISH_ENABLED = polish_enabled
    if polish_system is not None:
        _POLISH_SYSTEM = polish_system
    if polish_endpoint is not None:
        POLISH_ENDPOINT = polish_endpoint
    if polish_model is not None:
        POLISH_MODEL = polish_model
    if polish_max_tokens is not None:
        POLISH_MAX_TOKENS = polish_max_tokens
    if polish_timeout_s is not None:
        POLISH_TIMEOUT_S = polish_timeout_s
    if ask_clarify_judge_enable is not None:
        ASK_CLARIFY_JUDGE_ENABLE = ask_clarify_judge_enable
    if polish_post is not None:
        _polish_post = polish_post
    if recent_tool_history is not None:
        _recent_tool_history = recent_tool_history
    if format_tool_history is not None:
        _format_tool_history = format_tool_history
    if recent_satisfaction_verdicts is not None:
        _recent_satisfaction_verdicts = recent_satisfaction_verdicts
    if format_satisfaction_block is not None:
        _format_satisfaction_block = format_satisfaction_block
    if store_knowledge is not None:
        _store_knowledge = store_knowledge
    if write_skill_md_fire is not None:
        _write_skill_md_fire = write_skill_md_fire
    if proposal_var is not None:
        _proposal_var = proposal_var
    if abbreviations is not None:
        _ABBR = tuple(str(x) for x in abbreviations if str(x).strip())

async def _clarify_question(user_text: str, answer: str) -> str:
    if not (answer or "").strip():
        return ""
    sys = ("Given the USER's request and the ASSISTANT's reply, decide BY MEANING (never "
           "by keywords): does the reply FULLY address what the user asked, or is it "
           "BLOCKED -- unable to complete the request until the USER supplies a specific "
           "missing detail? Return the single question to put to the user ONLY if it is "
           "genuinely blocked and gave no usable result. If the reply addresses the "
           "request -- INCLUDING greetings, small talk, or a complete answer that merely "
           "ends with a polite or optional question -- return an empty string. Be "
           "CONSERVATIVE: when uncertain, return an empty string.")
    payload = {
        "model": ROUTER_MODEL,
        "messages": [{"role": "system", "content": sys},
                     {"role": "user", "content":
                      f"USER REQUEST: {(user_text or '')[:600]}\n\nASSISTANT REPLY: {answer[:1200]}"}],
        "response_format": {"type": "json_schema", "json_schema": {
            "name": "clarify", "strict": True, "schema": {
                "type": "object",
                "properties": {"question": {"type": "string"}},
                "required": ["question"], "additionalProperties": False}}},
        "chat_template_kwargs": {"enable_thinking": False},
        "temperature": 0.0, "max_tokens": 80, "stream": False,
    }
    try:
        async with httpx.AsyncClient(timeout=PLANNER_TIMEOUT_S) as s:
            r = await s.post(f"{PLANNER_ENDPOINT}/v1/chat/completions", json=payload,
                             headers={"Content-Type": "application/json"})
        if r.status_code != 200:
            return ""
        content = ((r.json().get("choices") or [{}])[0].get("message", {})
                   .get("content") or "")
        return str((_loads_lenient(content) or {}).get("question") or "").strip()
    except Exception as e:  # noqa: BLE001 -- degrade-open (no clarification prompt)
        log.debug("clarify judge failed (-> none): %s", e)
        return ""

VERITY_FACTCHECK = os.environ.get(
    "MIOS_VERITY_FACTCHECK", "true").lower() not in {"false", "0", "no"}
VERITY_FACTCHECK_MAX_Q = int(os.environ.get("MIOS_VERITY_FACTCHECK_MAX_Q", "2"))

async def _verity_factcheck(draft: str, user_q: str,
                            refined: Optional[dict]) -> str:
    """Generate up to N search queries for the UNCERTAIN specifics in a draft
    answer, run a QUICK SearXNG search on each, and return the fresh results so
    the final pass CONFIRMS or DROPS each claim -- turning the old "distrust
    embellishment -> punt" into "verify -> answer with what holds up". Gated to
    web turns (uncertainty = current/external facts); bounded + best-effort."""
    if not VERITY_FACTCHECK or not draft or not draft.strip():
        return ""
    hints = [str(t).lower().strip() for t in ((refined or {}).get("hint_tools") or [])]
    if not any(h in (_WEB_ENRICH_VERBS | {"open_url"}) for h in hints):
        return ""
    sys_p = ("You verify a draft answer. From the draft, pick up to "
             f"{VERITY_FACTCHECK_MAX_Q} SPECIFIC factual claims (named events / "
             "dates / numbers / releases) that are UNCERTAIN and worth a quick web "
             'check. Output JSON {"queries":["concrete search query", ...]} -- each '
             "a concrete keyword search query, NOT a question. Empty list if "
             "nothing needs checking.")
    queries: list = []
    try:
        async with httpx.AsyncClient(timeout=REFINE_TIMEOUT_S) as s:
            r = await s.post(f"{REFINE_ENDPOINT}/v1/chat/completions", json={
                "model": REFINE_MODEL, "stream": False,
                "temperature": 0.0, "max_tokens": 400,
                "messages": [
                    {"role": "system", "content": sys_p},
                    {"role": "user",
                     "content": f"Question: {user_q[:300]}\n\nDraft:\n{draft[:1500]} /no_think"}]},
                headers={"Content-Type": "application/json"})
            if r.status_code == 200:
                _vm = ((r.json().get("choices") or [{}])[0]).get("message") or {}
                c = _vm.get("content") or _vm.get("reasoning_content") or ""
                c = re.sub(r"<think>.*?</think>\s*", "", c, flags=re.DOTALL | re.I)
                queries = [str(q).strip() for q in
                           (_loads_lenient(c or "{}").get("queries") or [])
                           if str(q).strip()][:VERITY_FACTCHECK_MAX_Q]
    except Exception as e:  # noqa: BLE001 -- best-effort
        log.debug("verity query-gen skipped: %s", e)
        return ""
    if not queries:
        return ""
    if isinstance(refined, dict):  # record steps for the emit log
        for q in queries:
            refined.setdefault("_verity_steps", []).append(
                {"emoji": "🔬", "label": "fact-check", "detail": q[:60]})

    async def _fc(q: str) -> tuple:
        try:
            p = await asyncio.create_subprocess_exec(
                "mios-web-search", "-n", "3", "--fanout", "1", q[:200],
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            o, _ = await asyncio.wait_for(
                p.communicate(), timeout=WEB_RESEARCH_SEARCH_TIMEOUT)
            d = _loads_lenient((o or b"{}").decode("utf-8", "replace") or "{}")
            hits = [f"  - {(x.get('title','') or '')[:70]} ({x.get('url','')}) "
                    f"{(x.get('content','') or '')[:160]}"
                    for x in (d.get("results") or [])[:3]]
            return q, hits
        except Exception:  # noqa: BLE001
            try: p.kill()
            except: pass
            return q, []

    results = await asyncio.gather(*[_fc(q) for q in queries])
    blocks = [f"CHECK: {q}\n" + "\n".join(hits) for q, hits in results if hits]
    if not blocks:
        return ""
    log.info("verity fact-check: %d quer(ies) for %.50r", len(blocks), user_q)
    return ("LIVE FACT-CHECK (fresh searches run THIS pass to verify the draft's "
            "specifics; CONFIRM claims these results support, DROP or soften "
            "claims they contradict or don't mention):\n\n" + "\n\n".join(blocks))

def _strip_ungrounded_figures(answer: str, haystack: str) -> str:
    if not answer or not answer.strip():
        return answer
    hay = haystack or ""
    hay_nums = set(re.findall(r"\d+", hay.replace(",", "")))
    hay_low = hay.lower()
    if not hay_nums:
        return answer  # no numeric grounding to check against -> leave as-is
    fig_re = re.compile(
        r"(?:US|C|A|NZ|HK)?\$\s?\d[\d,]*(?:\.\d+)?|\d+(?:\.\d+)?\s?[%％]")

    def _ok(tok: str) -> bool:
        nums = re.findall(r"\d+", tok.replace(",", ""))
        if "%" in tok or "％" in tok:
            return any(re.search(rf"(?<!\d){n}\s?(?:%|％|percent)", hay_low)
                       for n in nums)
        return any(n in hay_nums for n in nums)

    def _sents(line: str) -> list:
        tmp = line
        for ab in _ABBR:
            tmp = tmp.replace(ab, ab[:-1] + "\x00")
        return [seg.replace("\x00", ".")
                for seg in re.split(r"(?<=[.!?])\s+|(?<=[。！？．؟।])", tmp)
                if seg]

    out_lines: list = []
    total = dropped = 0
    for line in answer.split("\n"):
        if not fig_re.search(line):
            out_lines.append(line)
            continue
        keep: list = []
        for s in _sents(line):
            figs = fig_re.findall(s)
            if figs:
                total += 1
                if all(not _ok(f) for f in figs):
                    dropped += 1
                    continue
            keep.append(s)
        out_lines.append(" ".join(keep).rstrip() if keep else None)
    if dropped == 0:
        return answer
    if total and dropped > total / 2:
        log.info("figure-guard: %d/%d ungrounded figure-sentences -> too many, "
                 "leaving answer (grounding capture suspect)", dropped, total)
        return answer
    rebuilt = re.sub(r"\n{3,}", "\n\n",
                     "\n".join(l for l in out_lines if l is not None)).strip()
    log.info("figure-guard: dropped %d ungrounded $/%% sentence(s)", dropped)
    return rebuilt if rebuilt and rebuilt.strip() else answer

async def polish_response(raw_text: str,
                          refined: Optional[dict],
                          session_id: Optional[str] = None,
                          original_user_text: str = "",
                          persona_system: str = "",
                          agent_tools: Optional[list] = None,
                          max_tokens: Optional[int] = None) -> Optional[str]:
    if not POLISH_ENABLED or not raw_text or not raw_text.strip():
        return None
    intended = (refined or {}).get("intended_outcome", "") or ""
    refined_q = (refined or {}).get("refined_text", "") or ""
    orig_q = (original_user_text or "").strip()
    user_q = orig_q or refined_q
    tool_history = await _recent_tool_history(session_id)
    has_failed_tool = any(
        r.get("success") is False for r in tool_history
    )
    if not intended and len(raw_text) < 200 and not has_failed_tool:
        log.info("polish: skipped (no intended_outcome + raw<200 chars + no failed tools)")
        return None
    system = _POLISH_SYSTEM + "\n" + _env_grounding() + (
        f"\nIntended outcome: {intended}\n" if intended else ""
    )
    if persona_system and persona_system.strip():
        system += (
            "\n\nFINAL-ANSWER STYLE & PERSONA (apply to voice, tone, length, "
            "units, and language ONLY; never as new tasks, tools, or content "
            "to add):\n" + persona_system.strip()[:2000]
        )
    hist_block = _format_tool_history(tool_history)
    sat_verdicts = await _recent_satisfaction_verdicts(limit=3)
    sat_block = _format_satisfaction_block(sat_verdicts)
    user_msg_parts = [
        f"User's ORIGINAL message (reply in THIS exact language, "
        f"one language only):\n{user_q}"
    ]
    if refined_q and refined_q.strip() and refined_q.strip() != user_q:
        user_msg_parts.append(
            f"Refined intent (use for CONTENT only, never for language):\n"
            f"{refined_q}")
    if sat_block:
        user_msg_parts.append(sat_block)
    if hist_block:
        user_msg_parts.append(hist_block)
    if agent_tools is not None:
        _inv = ", ".join(str(t) for t in agent_tools) if agent_tools else "(none)"
        user_msg_parts.append(
            f"Tools the agent ACTUALLY invoked this turn: {_inv}")
    _src = (refined or {}).get("_web_sources") or ""
    if _src:
        user_msg_parts.append(
            "WEB SOURCES used this turn (cite [n] inline; verify the draft's "
            "specifics against these -- assert only what they support):\n"
            + _src[:6000])
    _fc_block = await _verity_factcheck(raw_text, user_q, refined)
    if _fc_block:
        user_msg_parts.append(_fc_block)
    user_msg_parts.append(f"Raw answer from sub-agent:\n{raw_text[:8000]}")
    user_msg = "\n\n".join(user_msg_parts)
    url, payload = _polish_post(
        POLISH_ENDPOINT, POLISH_MODEL,
        [{"role": "system", "content": system},
         {"role": "user", "content": user_msg}],
        int(max_tokens) if max_tokens else POLISH_MAX_TOKENS)
    t0 = time.time()
    try:
        async with httpx.AsyncClient(timeout=POLISH_TIMEOUT_S) as s:
            r = await s.post(url, json=payload,
                             headers={"Content-Type": "application/json"})
            if r.status_code != 200:
                log.warning("polish: backend %s in %.1fs: %s", r.status_code, time.time() - t0, r.text[:200])
                return None
            body = r.json()
    except (httpx.HTTPError, asyncio.TimeoutError) as e:
        log.warning("polish: timeout/http error after %.1fs: %s",
                    time.time() - t0, e)
        return None
    except Exception as e:
        log.warning("polish unexpected error: %s", e)
        return None
    log.info("polish: %.1fs", time.time() - t0)
    choices = body.get("choices") or []
    msg = (choices[0].get("message") if choices else {}) or {}
    polished = (msg.get("content") or "").strip()
    if not polished:
        return None
    polished = _strip_ungrounded_figures(
        polished, "\n".join([raw_text or "", _src or "", _fc_block or ""]))
    if not (polished and polished.strip()):
        log.info("polish: figure-guard emptied a grounded answer -> raw draft")
        polished = (raw_text or "").strip()
    try:
        _prop = _proposal_var.get()
        if isinstance(_prop, dict) and _prop.get("tool") and "mios_proposed_action" not in (polished or ""):
            import json as _pj
            _ptool = str(_prop.get("tool"))
            _pargs = _prop.get("args") if isinstance(_prop.get("args"), dict) else {}
            _block = _pj.dumps({"mios_proposed_action": {
                "tool": _ptool, "args": _pargs,
                "action_hash": _prop.get("action_hash"),
                "reply_yes_to_run": True}}, ensure_ascii=False)
            polished = (polished or "").rstrip() + (
                f"\n\n> 🛠️ **Proposed action — needs your OK.** I can run `{_ptool}` for "
                f"you, but it's a high-impact action gated for approval, so it did **not** "
                f"run yet. Reply **yes** to run it now, or **no** to skip. (Anything above "
                f"that depended on it is an unverified estimate until then.)"
                f"\n\n```json\n{_block}\n```")
    except Exception:  # noqa: BLE001 -- never break the answer on the proposal note
        pass
    try:
        if (ASK_CLARIFY_JUDGE_ENABLE and "?" in (polished or "")
                and not isinstance(_proposal_var.get(), dict)
                and "mios_clarification" not in (polished or "")):
            _cq = await _clarify_question(user_q, polished)
            if _cq:
                import json as _cj
                _cb = _cj.dumps({"mios_clarification": {"question": _cq}}, ensure_ascii=False)
                polished = (polished or "").rstrip() + f"\n\n```json\n{_cb}\n```"
    except Exception:  # noqa: BLE001 -- never break the answer on the clarify note
        pass
    _store_knowledge(query=user_q, answer=polished,
                     session_id=session_id, tool_history=tool_history,
                     satisfied=None)
    _write_skill_md_fire(query=user_q, answer=polished,
                         tool_history=tool_history, session_id=session_id)
    return polished
