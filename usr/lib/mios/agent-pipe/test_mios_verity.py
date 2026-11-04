#!/usr/bin/env python3
# AI-hint: Standalone assert-script unit test for mios_verity (refactor R6 extraction). Pure stdlib, no server.py/DB/network/pytest.
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md
"""Unit tests for mios_verity (refactor R6)."""

import asyncio
import contextvars
import sys
import types

import mios_verity as v

_fails = 0

def check(name, cond, detail=""):
    global _fails
    if not cond:
        _fails += 1
    line = f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail else "")
    try:
        print(line)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "ascii"
        print(line.encode(enc, "replace").decode(enc))

def t_strip_figures():
    answer = "Deals as low as $184 are available. Prices drop to $999 today."
    haystack = "We found fares around $184 on the route."
    out = v._strip_ungrounded_figures(answer, haystack)
    check("strip: keeps grounded $184", "$184" in out, out)
    check("strip: drops ungrounded $999", "$999" not in out, out)

def t_strip_failsafe():
    answer = "It costs $500 and is 12% off."
    check("strip: empty-haystack untouched",
          v._strip_ungrounded_figures(answer, "no numbers here") == answer)
    check("strip: empty answer untouched", v._strip_ungrounded_figures("", "x $5") == "")

def t_strip_unicode_sentence():
    answer = "東京の価格は $184 です。大阪の価格は $999 です。"
    haystack = "東京 fares around $184 were found."
    out = v._strip_ungrounded_figures(answer, haystack)
    check("strip(cjk): keeps grounded 東京/$184", "$184" in out and "東京" in out, out)
    check("strip(cjk): drops ungrounded 大阪/$999", "$999" not in out and "大阪" not in out, out)

def t_abbr_from_ssot():
    answer = "Items etc. $999 listed. Final price $184 today."
    haystack = "Final price was $184."
    v.configure(abbreviations=["etc."])
    out_protected = v._strip_ungrounded_figures(answer, haystack)
    check("abbr(ssot): protected 'etc.' -> sentence dropped",
          "Items etc" not in out_protected and "$184" in out_protected, out_protected)
    v.configure(abbreviations=["zzz."])
    out_split = v._strip_ungrounded_figures(answer, haystack)
    check("abbr(ssot): unprotected 'etc.' -> sentence kept",
          "Items etc" in out_split and "$999" not in out_split, out_split)
    v.configure(abbreviations=list(v._ABBR_DEFAULT))

_SENT = "The capital of France is Paris."

class _FakeResp:
    status_code = 200
    text = ""

    def json(self):
        return {"choices": [{"message": {"content": _SENT}}]}

class _FakeClient:
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, **k):
        return _FakeResp()

async def _ath(*a, **k):
    return []

async def _averd(*a, **k):
    return []

def _wire_stubs():
    pv = contextvars.ContextVar("proposal", default=None)
    v.configure(
        polish_enabled=True,
        polish_system="SYS",
        polish_endpoint="http://127.0.0.1:0",
        polish_model="m",
        polish_max_tokens=100,
        polish_timeout_s=5,
        ask_clarify_judge_enable=False,
        polish_post=lambda ep, m, msgs, mt, temperature=0.0: ("http://127.0.0.1:0/v1/chat/completions", {}),
        recent_tool_history=_ath,
        format_tool_history=lambda rows: "",
        recent_satisfaction_verdicts=_averd,
        format_satisfaction_block=lambda rows: "",
        store_knowledge=lambda **k: None,
        write_skill_md_fire=lambda **k: None,
        proposal_var=pv,
    )
    v.httpx = types.SimpleNamespace(AsyncClient=_FakeClient, HTTPError=Exception)
    v._env_grounding = lambda: "ENV"

def t_polish_empty():
    out = asyncio.run(v.polish_response("", {"intended_outcome": "x"}))
    check("polish: empty raw -> None", out is None)

def t_polish_passthrough():
    _wire_stubs()
    out = asyncio.run(v.polish_response(
        "Paris is the capital.",
        {"intended_outcome": "answer the question"},
        session_id=None,
        original_user_text="What is the capital of France?"))
    check("polish: passes canned model output through", out == _SENT, repr(out))

_CLARIFY_TOKEN = "Zyxqq-wuvil-3?"

class _FakeClarifyClient:
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, **k):
        import json as _j
        return types.SimpleNamespace(
            status_code=200,
            json=lambda: {"choices": [{"message": {
                "content": _j.dumps({"question": _CLARIFY_TOKEN})}}]},
        )

def t_clarify_empty():
    check("clarify: empty answer -> ''", asyncio.run(v._clarify_question("zzv-qmx", "")) == "")

def t_clarify_extracts_question():
    saved = v.httpx
    v.httpx = types.SimpleNamespace(AsyncClient=_FakeClarifyClient, HTTPError=Exception)
    try:
        out = asyncio.run(v._clarify_question("wqx-plok", "vmz-trun?"))
    finally:
        v.httpx = saved
    check("clarify: extracts model 'question' field", out == _CLARIFY_TOKEN, repr(out))


def t_news_polish_preserves_checked_evidence():
    import importlib
    from unittest import mock
    target = importlib.import_module("mios_pipe.lifecycle.verity")
    checked = "Reports published on 2026-11-03:\n> A source excerpt about an earlier event."
    with mock.patch.object(target.httpx, "AsyncClient",
                           side_effect=AssertionError("polish must not invent news synthesis")):
        answer = asyncio.run(target.polish_response(
            "A fabricated current event from model memory.",
            {"news": True, "_news_answer": checked}, original_user_text="current reports"))
    check("news polish preserves checked article receipt without model synthesis", answer == checked)

def t_news_retrieval_failure_drops_stale_receipt():
    import importlib
    from unittest import mock
    web = importlib.import_module("mios_pipe.routing.web_research")
    with mock.patch.object(web, "_web_research_enrich", mock.AsyncMock(side_effect=RuntimeError("fixture offline"))):
        response = asyncio.run(v._respond_news_evidence(
            {"news": True, "_news_answer": "UNVERIFIED OLD ANSWER", "_news_evidence": [{"url": "forged"}]},
            streaming=False, chat_id="news", model="public", query="fixture",
            time_range="day"))
    import json
    data = json.loads(response.body)
    answer = data["choices"][0]["message"]["content"]
    check("failed news retrieval yields explicit evidence gap and no stale sources",
          "couldn't verify" in answer and "UNVERIFIED" not in answer
          and data["mios_sources"] == [] and data["mios_news_evidence"] == [])


def main():
    t_strip_figures()
    t_strip_failsafe()
    t_strip_unicode_sentence()
    t_abbr_from_ssot()
    t_polish_empty()
    t_polish_passthrough()
    t_clarify_empty()
    t_clarify_extracts_question()
    t_news_polish_preserves_checked_evidence()
    t_news_retrieval_failure_drops_stale_receipt()
    print(f"\n{'ok' if _fails == 0 else str(_fails) + ' FAILED'}")
    return 1 if _fails else 0

if __name__ == "__main__":
    sys.exit(main())
