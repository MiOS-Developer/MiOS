# AI-hint: Stdlib assert-script for mios_planner. No network: the planner LLM call in decompose_intent is exercised only on the early short-prompt-skip / dis...
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md
"""Offline assert-script for mios_planner. Run: python test_mios_planner.py"""

import asyncio
import contextvars
import json
import sys
import httpx

import mios_planner

def _ids(nodes):
    return [n.get("id") for n in nodes]

chain = [
    {"id": "n3", "tool": "c", "deps": ["n2"]},
    {"id": "n1", "tool": "a", "deps": []},
    {"id": "n2", "tool": "b", "deps": ["n1"]},
]
order = _ids(mios_planner._topological_order(chain))
assert order.index("n1") < order.index("n2") < order.index("n3"), order
assert set(order) == {"n1", "n2", "n3"}, order

diamond = [
    {"id": "n1", "deps": []},
    {"id": "n2", "deps": ["n1"]},
    {"id": "n3", "deps": ["n1"]},
    {"id": "n4", "deps": ["n2", "n3"]},
]
do = _ids(mios_planner._topological_order(diamond))
assert do[0] == "n1", do
assert do.index("n4") == 3, do
assert do.index("n2") < do.index("n4") and do.index("n3") < do.index("n4"), do

cycle = [
    {"id": "n1", "deps": ["n2"]},
    {"id": "n2", "deps": ["n1"]},
]
co = _ids(mios_planner._topological_order(cycle))
assert set(co) == {"n1", "n2"}, co

dangling = [{"id": "n1", "deps": ["ghost"]}]
assert _ids(mios_planner._topological_order(dangling)) == ["n1"]

lv = mios_planner._dag_levels(diamond)
assert _ids(lv[0]) == ["n1"], lv
assert {n["id"] for n in lv[1]} == {"n2", "n3"}, lv   # concurrent middle level
assert _ids(lv[2]) == ["n4"], lv
flat = [n["id"] for level in lv for n in level]
assert sorted(flat) == ["n1", "n2", "n3", "n4"], flat

fanout = [{"id": "a", "deps": []}, {"id": "b", "deps": []},
          {"id": "c", "deps": ["a"]}]
flv = mios_planner._dag_levels(fanout)
assert {n["id"] for n in flv[0]} == {"a", "b"}, flv
assert _ids(flv[1]) == ["c"], flv

clv = mios_planner._dag_levels(cycle)
cflat = sorted(n["id"] for level in clv for n in level)
assert cflat == ["n1", "n2"], cflat
assert all(len(level) == 1 for level in clv), clv   # forced single-node rounds

VERB = "<<VERB_CATALOG_SENTINEL>>"
RECIPE = "<<RECIPE_CATALOG_SENTINEL>>"
AGENT = "<<AGENT_CATALOG_SENTINEL>>"

_registry = {"hermes": {"lane": "x"}, "opencode": {"lane": "y"}}
_routed = contextvars.ContextVar("routed_domain", default=None)  # no domain routed

def _is_action_domain_stub(domain):
    return False

def _build_dispatch_cmd_stub(tool, args):
    return None if tool == "bogus_verb" else ["mios-launch", tool]

mios_planner.configure(
    verb_catalog_rendered=VERB,
    recipe_catalog_rendered=RECIPE,
    agent_catalog_rendered=AGENT,
    routed_domain_var=_routed,
    is_action_domain=_is_action_domain_stub,
    build_dispatch_cmd=_build_dispatch_cmd_stub,
    agent_registry=_registry,
)

ps = mios_planner._PLANNER_SYSTEM
assert isinstance(ps, str) and ps, "planner system not built"
assert VERB in ps and RECIPE in ps and AGENT in ps, "catalogs not embedded"
assert "You are the MiOS planner (Agentic-OS DAG decomposition layer)." in ps
assert "REASON -> PLAN -> DELEGATE meta-rule:" in ps
assert '{"action":"decompose",' in ps
assert "Cap your DAG at 8 nodes." in ps, "node cap not rendered"

class _FakeResp:
    def __init__(self, payload):
        self.status_code = 200
        self._payload = payload

    def json(self):
        return self._payload

class _FakeClient:
    _next_content = None  # set per-test

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, headers=None):
        body = {"choices": [{"message": {"content": _FakeClient._next_content}}]}
        return _FakeResp(body)

_orig_httpx_async_client = getattr(httpx, "AsyncClient", None)
mios_planner.httpx.AsyncClient = _FakeClient  # monkeypatch the model call

def tearDownModule():
    if _orig_httpx_async_client is not None:
        httpx.AsyncClient = _orig_httpx_async_client
        if hasattr(mios_planner, "httpx") and hasattr(mios_planner.httpx, "AsyncClient"):
            mios_planner.httpx.AsyncClient = _orig_httpx_async_client

GOOD = (
    "```json\n"
    '{"action":"decompose","summary":"find then launch",'
    '"nodes":['
    '{"id":"n1","tool":"directory_lookup","args":{"query":"x"},"deps":[]},'
    '{"id":"n2","agent":"hermes","prompt":"pick the best","deps":["n1"]}'
    "]}\n"
    "```"
)
LONG_TEXT = ("please find my report file and then open it in the editor "
             "after locating the most recent revision on disk")

async def _run(text, content):
    _FakeClient._next_content = content
    return await mios_planner.decompose_intent(text)

parsed = asyncio.run(_run(LONG_TEXT, GOOD))
assert parsed is not None, "good DAG was rejected"
assert parsed["action"] == "decompose", parsed
ids = [n["id"] for n in parsed["nodes"]]
assert ids == ["n1", "n2"], ids
assert parsed["nodes"][1]["agent"] == "hermes", parsed

BAD_AGENT = (
    '{"action":"decompose","summary":"",'
    '"nodes":[{"id":"n1","tool":"directory_lookup","args":{},"deps":[]},'
    '{"id":"n2","agent":"ghost_agent","prompt":"x","deps":["n1"]}]}'
)
assert asyncio.run(_run(LONG_TEXT, BAD_AGENT)) is None, "unknown agent not rejected"

BAD_VERB = (
    '{"action":"decompose","summary":"",'
    '"nodes":[{"id":"n1","tool":"directory_lookup","args":{},"deps":[]},'
    '{"id":"n2","tool":"bogus_verb","args":{},"deps":["n1"]}]}'
)
assert asyncio.run(_run(LONG_TEXT, BAD_VERB)) is None, "unknown verb not rejected"

ONE_NODE = ('{"action":"decompose","summary":"",'
            '"nodes":[{"id":"n1","tool":"directory_lookup","args":{},"deps":[]}]}')
assert asyncio.run(_run(LONG_TEXT, ONE_NODE)) is None, "single-node DAG not rejected"

N = mios_planner.PLANNER_MAX_NODES + 3
many = ",".join(
    '{"id":"m%d","tool":"directory_lookup","args":{},"deps":[]}' % i
    for i in range(N))
MANY = '{"action":"decompose","summary":"","nodes":[' + many + "]}"
capped = asyncio.run(_run(LONG_TEXT, MANY))
assert capped is not None, "valid many-node DAG rejected"
assert len(capped["nodes"]) == mios_planner.PLANNER_MAX_NODES, len(capped["nodes"])

assert asyncio.run(_run("open steam", GOOD)) is None, "short prompt not skipped"

assert mios_planner.PLANNER_SHORT_PROMPT_CHARS == 60, mios_planner.PLANNER_SHORT_PROMPT_CHARS
assert mios_planner.PLANNER_SHORT_PROMPT_WORDS == 10, mios_planner.PLANNER_SHORT_PROMPT_WORDS

mios_planner.configure(short_prompt_chars=4)
assert mios_planner.PLANNER_SHORT_PROMPT_CHARS == 4
assert asyncio.run(_run("open steam", GOOD)) is not None, "char cutoff not read from SSOT"

mios_planner.configure(short_prompt_chars=60, short_prompt_words=1)
assert asyncio.run(_run("open steam", GOOD)) is not None, "word cutoff not read from SSOT"

mios_planner.configure(short_prompt_words=10)
assert asyncio.run(_run("open steam", GOOD)) is None, "short prompt not skipped after restore"

SYN_CATALOG = {
    "qq_write_a": {"permission": "write"},
    "qq_probe_a": {"permission": "read"},
    "zz_read_b":  {"permission": "read"},
}
SYN_DOMAINS = {
    "doma": {"verbs": ["qq_write_a", "qq_probe_a"]},   # action: has a write verb
    "domb": {"verbs": ["zz_read_b"]},                  # research: no write verb
}

def _is_action_domain_real(domain):
    verbs = (SYN_DOMAINS.get(domain) or {}).get("verbs") or []
    return any((SYN_CATALOG.get(v) or {}).get("permission") == "write" for v in verbs)

mios_planner.configure(
    verb_catalog=SYN_CATALOG,
    routing_domains=SYN_DOMAINS,
    is_action_domain=_is_action_domain_real,
    verb_catalog_rendered=VERB,
)

assert mios_planner._action_domain_verbs() == {"qq_write_a", "qq_probe_a"}, \
    mios_planner._action_domain_verbs()

sys_a = mios_planner._planner_system_for("doma")
assert "qq_write_a" in sys_a and "qq_probe_a" in sys_a, sys_a
assert VERB not in sys_a, "full catalog block not narrowed for action domain"

sys_b = mios_planner._planner_system_for("domb")
assert "zz_read_b" in sys_b and "qq_write_a" not in sys_b, sys_b

assert mios_planner._planner_system_for(None) == mios_planner._PLANNER_SYSTEM
assert mios_planner._planner_system_for("ghost_domain") == mios_planner._PLANNER_SYSTEM

print("test_mios_planner: ALL PASS")
tearDownModule()


# ==============================================================================
# Consolidated from test_mios_replay.py (task_1006)
# ==============================================================================
# AI-hint: Standalone assert-script unit test for the task_205 run-template REPLAY path -- the pure matcher (mios_pipe.routing.replay), the...
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md

"""Unit tests for intent-keyed run-template replay (task_205)."""

import asyncio
import contextvars
import os
import sys

from mios_pipe.routing import replay as R

_fails_replay = 0

def _check_replay(name, cond, detail=""):
    global _fails_replay
    if not cond:
        _fails_replay += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

A = "search the web for the latest linux kernel CVEs and summarise the top three"

def t_keying():
    _check_replay("key: word ORDER does not change the key",
          R.intent_key(A) == R.intent_key(
              "summarise the top three latest linux kernel CVEs and search the web"))
    _check_replay("key: punctuation and case do not change the key",
          R.intent_key(A) == R.intent_key(
              "SEARCH the WEB for the LATEST, linux kernel CVEs -- and summarise the top three!"))
    _check_replay("key: a different request gets a different key",
          R.intent_key(A) != R.intent_key("what is the weather in Paris tomorrow"))
    _check_replay("key: an empty turn keys to nothing", R.intent_key("") == "")
    _check_replay("key: an all-stopword turn keys to nothing", R.intent_key("what is the of and") == "")
    _check_replay("tokens: stopwords are dropped", "the" not in R.normalize_tokens(A))
    _check_replay("tokens: sorted + unique",
          R.normalize_tokens("beta alpha beta") == ("alpha", "beta"))

def t_similarity():
    _check_replay("sim: identical sets score 1.0",
          R.similarity(("a", "b"), ("b", "a")) == 1.0)
    _check_replay("sim: disjoint sets score 0.0", R.similarity(("a",), ("b",)) == 0.0)
    _check_replay("sim: two EMPTY sets score 0.0, not a perfect 1.0",
          R.similarity((), ()) == 0.0)
    _check_replay("sim: one empty set scores 0.0", R.similarity(("a",), ()) == 0.0)
    _check_replay("sim: half overlap scores 1/3 (Jaccard, not overlap-coefficient)",
          abs(R.similarity(("a", "b"), ("b", "c")) - (1 / 3)) < 1e-9)

def _tpl(intent, nodes=1):
    return {"intent": intent, "intent_key": R.intent_key(intent),
            "dag": {"nodes": [{"id": i + 1, "tool": "web_search"} for i in range(nodes)]}}

def t_match():
    T = [_tpl(A, nodes=2)]
    tpl, score, why = R.match_template(A, T, 0.85)
    _check_replay("match: an exact intent key wins outright", tpl is not None and score == 1.0, why)

    tpl, score, why = R.match_template("what is the price of a bicycle in Amsterdam", T, 0.85)
    _check_replay("match: an unrelated turn does NOT match", tpl is None and score == 0.0, why)

    tpl, score, why = R.match_template("search the web for linux kernel CVEs", T, 0.85)
    _check_replay("match: a merely PARTIAL overlap falls back rather than replaying",
          tpl is None and 0.0 < score < 0.85, why)
    tpl, score, why = R.match_template("search the web for linux kernel CVEs", T, 0.5)
    _check_replay("match: the same turn DOES match once the threshold is lowered",
          tpl is not None and score >= 0.5, why)

    _check_replay("match: an empty turn matches nothing",
          R.match_template("", T, 0.85)[0] is None)
    _check_replay("match: an empty template list matches nothing",
          R.match_template(A, [], 0.85)[0] is None)
    _check_replay("match: a row with NO dag never consumes the match",
          R.match_template(A, [{"intent": A, "intent_key": R.intent_key(A), "dag": {}}],
                           0.85)[0] is None)
    _check_replay("match: a row with an intent but no stored key still matches by intent",
          R.match_template(A, [{"intent": A, "dag": {"nodes": [{"id": 1}]}}], 0.85)[1] == 1.0)
    _check_replay("match: a non-numeric threshold falls back to the default, not a crash",
          R.match_template("search the web for linux kernel CVEs", T, "bogus")[0] is None)
    _check_replay("match: the score is returned even on a miss, so the decision is auditable",
          R.match_template("search the web for linux kernel CVEs", T, 0.85)[1] > 0)

def t_capture_roundtrip():
    """A captured template must be matchable by the turn that produced it --
    the whole feature is dead if the capture stores no usable intent key."""
    from mios_pipe.routing import run_template as RT
    rows = []
    RT.configure(run_template_enable=True, pg_primary=True,
                 pg_mirror=lambda t, r: rows.append(r),
                 db_create=lambda *a, **k: "x", db_post=lambda s: s,
                 db_fire=lambda x: None)
    RT._capture_run_template(
        {"summary": "s", "intent": A, "nodes": [{"id": 1, "tool": "web_search"}]}, "sess1")
    _check_replay("capture: a row is written", len(rows) == 1)
    _check_replay("capture: it carries a NON-EMPTY intent key",
          bool(rows and rows[0].get("intent_key")), str(rows[:1]))
    _check_replay("capture: the captured row matches its own turn",
          bool(rows) and R.match_template(A, [dict(rows[0])], 0.85)[1] == 1.0)
    rows.clear()
    RT._capture_run_template({"summary": "s", "nodes": []}, "sess1")
    _check_replay("capture: an empty DAG is not stored", rows == [])

def t_planner_replay():
    """The Done-When, counted at the HTTP layer: a repeat spends ZERO planning
    calls; a fuzzy variant spends one; the default flag spends one."""
    try:
        import httpx
    except ModuleNotFoundError:                      # pragma: no cover
        print("[SKIP] planner replay: httpx absent")
        return
    import mios_planner as P

    calls = []

    class _Counting:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, **kw):
            calls.append(url)
            return httpx.Response(200, json={"choices": [{"message": {"content":
                '{"action":"decompose","summary":"fresh","nodes":['
                '{"id":1,"tool":"web_search","args":{}},'
                '{"id":2,"tool":"web_search","args":{},"deps":[1]}]}'}}]},
                request=httpx.Request("POST", url))

    real_httpx = P.httpx
    P.httpx = type("H", (), {"AsyncClient": _Counting, "Timeout": httpx.Timeout})
    prev_build = P._build_dispatch_cmd
    P.configure(replay_templates=lambda limit=50: _rows(),
                routed_domain_var=contextvars.ContextVar("d", default=None),
                is_action_domain=lambda d: False,
                build_dispatch_cmd=lambda tool, args: "echo ok")
    enabled, chars, words = P.PLANNER_ENABLED, P.PLANNER_SHORT_PROMPT_CHARS, P.PLANNER_SHORT_PROMPT_WORDS
    P.PLANNER_ENABLED, P.PLANNER_SHORT_PROMPT_CHARS, P.PLANNER_SHORT_PROMPT_WORDS = True, 0, 0

    async def _rows(limit=50):
        return [_tpl(A, nodes=2)]

    try:
        os.environ["MIOS_RUN_TEMPLATE_REPLAY"] = "true"
        calls.clear()
        dag = asyncio.run(P.decompose_intent(A))
        _check_replay("planner: a repeated intent spends ZERO planning calls", len(calls) == 0, str(calls))
        _check_replay("planner: it returns the STORED dag, marked replayed",
              bool(dag) and dag.get("replayed") is True and len(dag.get("nodes") or []) == 2)

        calls.clear()
        asyncio.run(P.decompose_intent(
            "SEARCH the WEB for the LATEST, linux kernel CVEs -- summarise the top three!"))
        _check_replay("planner: a rephrasing of the same intent also spends zero calls",
              len(calls) == 0, str(calls))

        calls.clear()
        fuzzy_text = "what is the current price of a second-hand bicycle in Amsterdam right now"
        dag = asyncio.run(P.decompose_intent(fuzzy_text))
        _check_replay("planner: a fuzzy variant FALLS BACK to planning", len(calls) == 1, str(calls))
        _check_replay("planner: the fuzzy variant is not marked replayed",
              not (dag or {}).get("replayed"))
        # The link between planning and capture: without this stamp every stored
        # template is unreplayable, and the whole feature is silently dead.
        _check_replay("planner: a freshly planned DAG carries the TURN's intent",
              (dag or {}).get("intent") == fuzzy_text, str((dag or {}).get("intent")))
        from mios_pipe.routing import run_template as _RT
        _check_replay("planner: that intent yields a usable capture key",
              bool(_RT._replay.intent_key((dag or {}).get("intent") or "")))

        calls.clear()
        asyncio.run(P.decompose_intent("search the web for linux kernel CVEs"))
        _check_replay("planner: a merely partial overlap also falls back", len(calls) == 1, str(calls))

        os.environ["MIOS_RUN_TEMPLATE_REPLAY"] = "false"
        calls.clear()
        dag = asyncio.run(P.decompose_intent(A))
        _check_replay("planner: at the DEFAULT flag the replay path is inert",
              len(calls) == 1 and not (dag or {}).get("replayed"), str(calls))
    finally:
        os.environ.pop("MIOS_RUN_TEMPLATE_REPLAY", None)
        P.httpx = real_httpx
        P.PLANNER_ENABLED, P.PLANNER_SHORT_PROMPT_CHARS, P.PLANNER_SHORT_PROMPT_WORDS = enabled, chars, words
        P.configure(build_dispatch_cmd=prev_build)

def _main_replay():
    t_keying()
    t_similarity()
    t_match()
    t_capture_roundtrip()
    t_planner_replay()
    print(f"\n{'ok' if _fails_replay == 0 else str(_fails_replay) + ' FAILED'}")
    return 1 if _fails_replay else 0


def _run_extra_replay():
    import os
    _saved_env = dict(os.environ)
    try:
        return _main_replay()
    except SystemExit as _e:
        return _e.code if _e.code is not None else 0
    finally:
        os.environ.clear()
        os.environ.update(_saved_env)



def _run_all_folded_planner_suites():
    rc = _run_extra_replay()
    if rc not in (None, 0):
        import sys
        sys.exit(f"Folded test suite failed: exit code {rc}")

_run_all_folded_planner_suites()
