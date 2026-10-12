# AI-hint: Stdlib assert-script for mios_web_research. No network. Drives
# AI-related: ./mios_web_research.py
import asyncio
import contextvars
import json
import os
import re

_here = os.path.dirname(os.path.abspath(__file__))
_toml = "/usr/share/mios/mios.toml"
if not os.path.isfile(_toml):
    _toml = os.path.abspath(os.path.join(_here, "..", "..", "..", "..",
                                         "usr", "share", "mios", "mios.toml"))
if "MIOS_TOML" not in os.environ and os.path.isfile(_toml):
    os.environ["MIOS_TOML"] = _toml

os.environ.setdefault(
    "MIOS_SEARCH_ANCHOR_STOPWORDS",
    "the,and,for,with,from,that,this,are,was,were,has,have,you,your,our",
)

import mios_web_research as W

_routed = contextvars.ContextVar("routed", default=None)
_cenv = contextvars.ContextVar("cenv", default={})
_sources_var = contextvars.ContextVar("sources", default=None)
_conv_key = contextvars.ContextVar("conv_key", default="")
_src_turn = contextvars.ContextVar("src_turn", default=None)

def _url_has_path(u: str) -> bool:
    try:
        tail = u.split("://", 1)[-1]
        path = tail[tail.find("/"):] if "/" in tail else ""
        path = path.split("?", 1)[0].split("#", 1)[0]
        return any(s for s in path.split("/") if s)
    except Exception:
        return False

def _configure(**over):
    kw = dict(
        is_action_domain=lambda d: False,
        current_date_str=lambda: "2026-06-25",
        current_year=lambda: "2026",
        anchor_tokens=lambda t: set(),          # empty -> anchor filter skipped
        shares_anchor=lambda t, a: True,         # degrade-open
        url_has_path=_url_has_path,
        clean_web_text=lambda s: s,              # identity (isolate _strip_nav_chrome)
        routed_domain_var=_routed,
        client_env_var=_cenv,
        sources_var=_sources_var,
        conv_key_var=_conv_key,
        src_turn_var=_src_turn,
        sources_registry={},
        sources_registry_cap=64,
        max_sources=8,
        web_enrich_verbs={"web_search", "web_extract", "crawl"},
        location_sensitive_phrases=(),
        judge_model="judge",
        judge_endpoint="http://127.0.0.1:1/v1",
        web_research_enabled=True,
        web_research_passes=1,
        web_research_results=8,
        web_research_fanout=1,
        web_research_fetch_n=1,
        web_research_fetch_chars=3000,
        web_research_block_chars=1200,
        web_research_search_timeout=5.0,
        web_research_fetch_timeout=5.0,
        web_research_crawl_fallback=False,
        web_research_min_chars=50,
        web_research_crawl_timeout=5.0,
        web_research_crawl_max=6,
        web_research_use_news_category=False,
        web_research_time_range="",
        web_research_max_attempts=2,
    )
    kw.update(over)
    W.configure(**kw)

class _FakeProc:
    def __init__(self, out): self._out = out
    async def communicate(self): return (self._out, b"")
    def kill(self): pass

def _install_exec(router, counter):
    async def _fake_exec(*args, **kw):
        tool = args[0]
        counter[tool] = counter.get(tool, 0) + 1
        payload = router(tool, list(args))
        return _FakeProc(json.dumps(payload).encode("utf-8"))
    asyncio.create_subprocess_exec = _fake_exec

class _FakeResp:
    status_code = 200
    def __init__(self, payload): self._p = payload
    def json(self): return self._p

class _FakeClient:
    def __init__(self, *a, **k): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def post(self, url, **k):
        verdict = _JUDGE_QUEUE.pop(0) if _JUDGE_QUEUE else {"answerable": True}
        return _FakeResp({"choices": [{"message": {"content": json.dumps(verdict)}}]})

class _FakeHttpx:
    AsyncClient = _FakeClient

_JUDGE_QUEUE: list = []
_REAL_EXEC = asyncio.create_subprocess_exec
_REAL_HTTPX = W.httpx

LONG = ("This is a real fetched article body with plenty of substantive prose "
        "describing the actual developments the user asked about, well over the "
        "minimum character threshold so it counts as real content. " * 3)

def _router_simple(tool, args):
    if tool == "mios-web-search":
        return {"results": [{"url": "https://example.com/article-one-2026",
                             "title": "Story One", "content": "snippet"}]}
    if tool == "mios-web-extract":
        return {"content": LONG}
    return {"success": False}

def test_judge_gate_loop_vs_stop():
    global _JUDGE_QUEUE
    W.httpx = _FakeHttpx
    try:
        _configure()
        _JUDGE_QUEUE = [{"answerable": True}]
        counter = {}
        _install_exec(_router_simple, counter)
        refined = {"web": True}
        out = asyncio.run(W._web_research_enrich("recent xyz developments", refined))
        assert out.startswith("LIVE WEB RESEARCH"), out[:60]
        assert counter.get("mios-web-search") == 1, counter
        _configure()
        _JUDGE_QUEUE = [{"answerable": False, "better_query": "sharper angle q",
                         "scrape_url": ""},
                        {"answerable": True}]
        counter = {}
        _install_exec(_router_simple, counter)
        out2 = asyncio.run(W._web_research_enrich("recent xyz developments", {"web": True}))
        assert counter.get("mios-web-search") == 2, counter
        assert out2.startswith("LIVE WEB RESEARCH"), out2[:60]
    finally:
        asyncio.create_subprocess_exec = _REAL_EXEC
        W.httpx = _REAL_HTTPX
    print("test_judge_gate_loop_vs_stop OK")

def test_step_recording_shape():
    global _JUDGE_QUEUE
    W.httpx = _FakeHttpx
    try:
        _configure(web_research_max_attempts=1)   # no judge call needed
        _JUDGE_QUEUE = []
        counter = {}
        _install_exec(_router_simple, counter)
        captured: list = []
        refined = {"web": True}
        out = asyncio.run(W._web_research_enrich(
            "recent xyz developments", refined, emit=captured.append))
        assert out.startswith("LIVE WEB RESEARCH"), out[:60]
        assert "real fetched article body" in out
        steps = refined.get("_web_steps")
        assert isinstance(steps, list) and steps, steps
        assert all(isinstance(s, dict) and "emoji" in s and "label" in s
                   and "detail" in s for s in steps), steps
        assert any(s["emoji"] == "\U0001F50E" for s in steps), "no search step"
        assert any(s["emoji"] == "\U0001F4D6" for s in steps), "no read step"
        stats = refined.get("_web_stats")
        assert stats and stats["real"] == 1 and stats["sources"] >= 1, stats
        assert refined.get("_web_sources"), "no _web_sources block"
        assert captured == steps, (captured, steps)
    finally:
        asyncio.create_subprocess_exec = _REAL_EXEC
        W.httpx = _REAL_HTTPX
    print("test_step_recording_shape OK")

NAV = ("[Home](https://news.example.com/) [About](https://news.example.com/about) "
       "[Contact](https://news.example.com/contact)")
PROSE = ("Real prose paragraph reporting the substantive facts the user asked "
         "about, with enough words to be retained as body content not chrome. " * 3)
INDEX_MD = NAV + "\n\n" + PROSE
STORY_URL = "https://news.example.com/story-about-the-big-thing-2026-01-02"

def _router_drill(tool, args):
    url = args[-1]
    if tool == "mios-web-search":
        return {"results": [{"url": "https://news.example.com/",
                             "title": "News Home", "content": "x"}]}
    if tool == "mios-web-extract":
        if url == STORY_URL:
            return {"content": "STORY BODY " + LONG}
        return {"content": "thin"}
    if tool == "mios-crawl":
        if url == "https://news.example.com/":
            return {"success": True, "markdown": INDEX_MD,
                    "links": [{"href": STORY_URL}]}
        return {"success": True, "markdown": "STORY BODY " + LONG, "links": []}
    if tool == "mios-firecrawl":
        return {"success": False}
    return {"success": False}

def test_nav_strip_and_two_hop_drill():
    global _JUDGE_QUEUE
    W.httpx = _FakeHttpx
    _orig_port = W._is_port_open
    try:
        _configure(web_research_max_attempts=1, web_research_crawl_fallback=True,
                   web_research_fetch_n=2, web_research_passes=1)
        W._is_port_open = lambda *a, **k: True   # crawl + firecrawl engines "up"
        _JUDGE_QUEUE = []
        counter = {}
        _install_exec(_router_drill, counter)
        refined = {"web": True}
        out = asyncio.run(W._web_research_enrich("the big thing", refined))
        steps = refined.get("_web_steps") or []
        assert any(s.get("label") == "reading the story" for s in steps), steps
        assert "STORY BODY" in out, out[:120]
        assert "/about" not in out and "/contact" not in out, "nav chrome leaked"
    finally:
        asyncio.create_subprocess_exec = _REAL_EXEC
        W.httpx = _REAL_HTTPX
        W._is_port_open = _orig_port
    print("test_nav_strip_and_two_hop_drill OK")

def _capturing_router(sink):
    def _router(tool, args):
        if tool == "mios-web-search":
            sink.append(list(args))
            return {"results": [{"url": "https://example.com/article-one-x",
                                 "title": "Story One", "content": "snippet"}]}
        if tool == "mios-web-extract":
            return {"content": LONG}
        return {"success": False}
    return _router

def test_recency_flag_drives_time_range():
    global _JUDGE_QUEUE
    W.httpx = _FakeHttpx
    try:
        _configure(web_research_max_attempts=1, web_research_recency_range="month")
        _JUDGE_QUEUE = []
        sink_a: list = []
        _install_exec(_capturing_router(sink_a), {})
        out = asyncio.run(W._web_research_enrich("xyz developments", {"news": True}))
        assert out.startswith("LIVE WEB RESEARCH"), out[:60]
        assert sink_a, "no search fired"
        a = sink_a[0]
        assert "--time-range" in a and a[a.index("--time-range") + 1] == "month", a
        assert a[-1].endswith("2026"), a[-1]   # current year appended (time-sensitive)

        _configure(web_research_max_attempts=1, web_research_recency_range="month")
        sink_a2: list = []
        _install_exec(_capturing_router(sink_a2), {})
        asyncio.run(W._web_research_enrich(
            "xyz developments", {"web": True, "needs_recency": True}))
        a2 = sink_a2[0]
        assert "--time-range" in a2 and a2[a2.index("--time-range") + 1] == "month", a2

        _configure(web_research_max_attempts=1, web_research_recency_range="week")
        sink_a3: list = []
        _install_exec(_capturing_router(sink_a3), {})
        asyncio.run(W._web_research_enrich("xyz developments", {"news": True}))
        a3 = sink_a3[0]
        assert "--time-range" in a3 and a3[a3.index("--time-range") + 1] == "week", a3

        _configure(web_research_max_attempts=1, web_research_recency_range="month")
        sink_b: list = []
        _install_exec(_capturing_router(sink_b), {})
        asyncio.run(W._web_research_enrich("what happened today", {"web": True}))
        b = sink_b[0]
        assert "--time-range" not in b, b      # no lexical recency from a query word
        assert "2026" not in b[-1], b[-1]      # no year appended without the model flag
    finally:
        asyncio.create_subprocess_exec = _REAL_EXEC
        W.httpx = _REAL_HTTPX
    print("test_recency_flag_drives_time_range OK")

def test_source_registry_cluster():
    _configure()   # wires _sources_var/_conv_key/_src_turn/_SOURCES_REGISTRY + caps

    def _run():
        _conv_key.set("chat-xyz")
        _sources_var.set([])
        W._src_turn_init()
        assert W._src_turn_key() == "chat-xyz"
        W._src_record([
            {"title": "Story One", "url": "https://example.com/article-one"},
            {"title": "Home", "url": "https://example.com/"},          # bare homepage
            {"title": "junk", "url": "not-a-url"},                      # dropped
            {"title": "Story One dup", "url": "https://example.com/article-one"},  # dup url
        ])
        refs = W._src_collected()
        urls = [u for _t, u in refs]
        assert urls == ["https://example.com/article-one"], urls

        md = W._sources_markdown(refs)
        assert "**Sources:**" in md and "https://example.com/article-one" in md
        meta = W._sources_metadata(refs)
        assert meta == [{"n": 1, "title": "Story One",
                         "url": "https://example.com/article-one"}], meta
        text = "see https://example.com/article-one for details"
        ann = W._sources_annotations(refs, text)
        assert ann[0]["type"] == "url_citation"
        assert ann[0]["start_index"] == text.find("https://example.com/article-one")

        kept = W._filter_relevant_sources(refs, "a Story about One")
        assert kept == refs, kept
        kept_none = W._filter_relevant_sources(refs, "completely unrelated zzzz")
        assert kept_none == refs, "degrade-open: never strip citations to empty"

    import contextvars as _cv
    _cv.copy_context().run(_run)
    print("test_source_registry_cluster OK")

def test_src_record_from_text_harvest():
    _configure()

    def _run():
        _conv_key.set("chat-harvest")
        _sources_var.set([])
        W._src_turn_init()
        W._src_record_from_text(
            "answer body\n\n**Sources:**\n1. Title A — https://site.test/a\n"
            "2. Title B — https://site.test/b\n")
        urls = [u for _t, u in W._src_collected()]
        assert "https://site.test/a" in urls and "https://site.test/b" in urls, urls

        _sources_var.set([])
        W._SOURCES_REGISTRY[W._src_turn_key()] = []
        W._harvest_sub_sources({"mios_sources": [
            {"title": "Struct", "url": "https://site.test/structured"}]}, "ignored")
        urls2 = [u for _t, u in W._src_collected()]
        assert "https://site.test/structured" in urls2, urls2

    import contextvars as _cv
    _cv.copy_context().run(_run)
    print("test_src_record_from_text_harvest OK")

def test_web_text_anchor_helpers_native():

    assert W._url_has_path("https://x.com/") is False
    assert W._url_has_path("https://x.com") is False
    assert W._url_has_path("https://x.com/aaa/bbb/ccc/ddd") is True

    raw = ("* [Navlbl](https://s.test/)\n"
           "Prose Zzqqx with an [Anchortxt](https://s.test/x) retained.\n"
           "![Imgalt](https://s.test/img.png)\n\n\n\nTailz.")
    cleaned = W._clean_web_text(raw)
    assert "https://s.test/x" not in cleaned          # inline URL flattened away
    assert "Anchortxt" in cleaned                      # anchor text retained
    assert "](https://s.test/)" not in cleaned         # nav bullet line dropped
    assert "![Imgalt]" not in cleaned                  # image stripped
    assert "\n\n\n" not in cleaned                      # blank runs collapsed
    assert W._clean_web_text("") == ""

    toks = W._anchor_tokens("Zorptel ab Quibblers")
    assert "zorptel" in toks                            # alpha-initial, len>=3, lowercased
    assert "quibbler" in toks                           # 'Quibblers' (len>=5, -s) folded
    assert "ab" not in toks                             # len<3 dropped
    _sw = next((w for w in W._ANCHOR_STOPWORDS if len(w) >= 3 and w.isalpha()), None)
    assert _sw is not None
    assert _sw not in W._anchor_tokens(f"Zorptel {_sw} Quibblers")

    anchor = W._anchor_tokens("Zorptel Quibblers Frobnak")
    assert len(anchor) >= 2
    assert W._shares_anchor("brand Zorptel item", anchor) is True
    assert W._shares_anchor("aaaa bbbb cccc", anchor) is False
    assert W._shares_anchor("whatever", {"single"}) is True
    print("test_web_text_anchor_helpers_native OK")

def test_link_rank_scorer_ssot():
    d = W._LINK_RANK_DEFAULTS
    src = "https://syn.invalid/"                       # the index page itself (skipped)

    _slug = ("a" * d["slug_min_len"]) + "-z"           # has '-', len >= slug_min_len
    _slug_digit = ("a" * d["slug_min_len"]) + "-7"     # same, but carries a digit
    _long_anchor = "h" * d["anchor_min_len"]           # len >= anchor_min_len
    rich = "https://syn.invalid/sec/" + _slug_digit
    medium = "https://syn.invalid/" + _slug
    weak = "https://syn.invalid/ab"
    cands = [(_long_anchor, rich), ("", medium), ("", weak)]

    rich_score = (d["seg_base"] * 2 + d["slug_weight"]
                  + d["digit_weight"] + d["anchor_weight"])
    medium_score = d["seg_base"] * 1 + d["slug_weight"]
    weak_score = d["seg_base"] * 1
    assert weak_score < d["min_score"]                 # weak below the drop cutoff
    assert medium_score >= d["min_score"] and rich_score > medium_score

    ranked = W._rank_links_by_structure(cands, src, set())
    assert ranked == [rich, medium], ranked

    over_top = {**d, "top_n": 1}                        # keep only the top result
    assert W._rank_links_by_structure(cands, src, set(), cfg=over_top) == [rich], "top_n"
    over_min = {**d, "min_score": rich_score}           # raise cutoff -> medium drops
    assert W._rank_links_by_structure(cands, src, set(), cfg=over_min) == [rich], "min_score"
    over_slug = {**d, "slug_weight": 0}                 # zero the slug bonus -> medium falls
    assert medium not in W._rank_links_by_structure(cands, src, set(), cfg=over_slug), "slug_weight"

    _orig = W._toml_section
    try:
        W._toml_section = lambda name: ({"top_n": 1, "slug_weight": 9}
                                        if name == "web_research" else {})
        cfg = W._link_rank_cfg()
        assert cfg["top_n"] == 1 and cfg["slug_weight"] == 9, cfg
        assert cfg["anchor_min_len"] == d["anchor_min_len"], cfg   # omitted -> default
        assert cfg["link_rank_mode"] == d["link_rank_mode"], cfg

        def _boom(name):
            raise RuntimeError("malformed config")
        W._toml_section = _boom
        assert W._link_rank_cfg() == d, "raise -> defaults"
        W._toml_section = lambda name: ["not", "a", "table"]   # non-dict section
        assert W._link_rank_cfg() == d, "non-dict -> defaults"
        W._toml_section = lambda name: {"slug_weight": "NaN", "top_n": 2}  # garbage value
        cfg_bad = W._link_rank_cfg()
        assert cfg_bad["slug_weight"] == d["slug_weight"], cfg_bad   # bad key keeps default
        assert cfg_bad["top_n"] == 2, cfg_bad                        # good key still applies
    finally:
        W._toml_section = _orig

    over_embed = {**d, "link_rank_mode": "embed"}
    assert (W._rank_links(cands, src, set(), cfg=over_embed)
            == W._rank_links_by_structure(cands, src, set(), cfg=d)), "embed degrade-open"
    print("test_link_rank_scorer_ssot OK")



# ==============================================================================
# Consolidated from test_mios_applet_webresearch.py (task_1006)
# ==============================================================================
# AI-hint: Isolation tests for the web-research SSE applet (mios_pipe.routing.applet_webresearch).
# AI-related: mios_pipe.routing
# AI-functions: check, _events, main

"""Isolation tests for the web-research SSE applet (mios_pipe.routing.applet_webresearch)."""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mios_pipe.routing import applet_webresearch as wr  # noqa: E402

_fails_applet_webresearch = 0

def _check_applet_webresearch(cond, msg):
    global _fails_applet_webresearch
    print(("[PASS] " if cond else "[FAIL] ") + msg)
    if not cond:
        _fails_applet_webresearch += 1

async def _collect(query, dispatch, **kw):
    frames = []
    async for f in wr.stream_webresearch(query, dispatch, **kw):
        frames.append(f)
    return frames

def _events(frames):
    return [f.split("\n", 1)[0].replace("event: ", "") for f in frames]

async def _main_async_applet_webresearch():
    async def d_ok(tool, args):
        _check_applet_webresearch(tool == "web_search", "dispatch invoked with web_search verb")
        _check_applet_webresearch(args.get("query") == "langs", "query forwarded to dispatch args")
        return {"results": [
            {"title": "Rust", "url": "https://rust-lang.org", "snippet": "systems lang"},
            {"title": "Zig", "url": "https://ziglang.org"},
        ]}
    fr = await _collect("langs", d_ok, limit=5)
    ev = _events(fr)
    _check_applet_webresearch(ev[0] == "status", f"first frame is status ({ev[:1]})")
    _check_applet_webresearch(ev.count("result") == 2, f"two result frames ({ev.count('result')})")
    _check_applet_webresearch(ev[-1] == "done", "last frame is done")
    body = "".join(fr)
    _check_applet_webresearch("https://rust-lang.org" in body and ">Rust<" in body, "result html carries url+title")
    _check_applet_webresearch(all(f.endswith("\n\n") for f in fr), "every SSE frame terminates with a blank line")

    called = {"n": 0}
    async def d_never(t, a):
        called["n"] += 1
        return {}
    fr = await _collect("   ", d_never)
    _check_applet_webresearch(_events(fr) == ["error", "done"], "empty query -> error,done")
    _check_applet_webresearch(called["n"] == 0, "dispatch NOT called on empty query")

    async def d_boom(t, a):
        raise RuntimeError("boom-net")
    fr = await _collect("x", d_boom)
    _check_applet_webresearch(_events(fr)[-1] == "done" and "error" in _events(fr), "dispatch error -> error,done")
    _check_applet_webresearch("boom-net" in "".join(fr), "error frame carries the failure message")

    async def d_xss(t, a):
        return {"results": [{"title": "<script>alert(1)</script>", "url": "https://x/"}]}
    body = "".join(await _collect("x", d_xss))
    _check_applet_webresearch("<script>alert" not in body and "&lt;script&gt;" in body, "title HTML-escaped (no raw <script>)")

    _check_applet_webresearch(len(wr._extract_results({"data": [{"a": 1}]})) == 1, "extract: dict.data list")
    _check_applet_webresearch(len(wr._extract_results([{"a": 1}, {"b": 2}])) == 2, "extract: bare list")
    _check_applet_webresearch(len(wr._extract_results({"stdout": json.dumps({"hits": [{"x": 1}]})})) == 1, "extract: json-in-stdout")
    _check_applet_webresearch(wr._extract_results("garbage") == [], "extract: unknown -> []")

def _main_applet_webresearch():
    asyncio.run(_main_async_applet_webresearch())
    try:
        r = wr.build_router()
        paths = {getattr(rt, "path", None) for rt in r.routes}
        _check_applet_webresearch("/portal/app/webresearch" in paths, "router exposes the applet page route")
        _check_applet_webresearch("/portal/app/webresearch/stream" in paths, "router exposes the SSE stream route")
    except Exception as e:  # noqa: BLE001
        _check_applet_webresearch(False, f"build_router() failed: {e}")
    print("ALL PASS" if _fails_applet_webresearch == 0 else f"{_fails_applet_webresearch} FAILED")
    return 1 if _fails_applet_webresearch else 0


def _run_extra_applet_webresearch():
    import os
    _saved_env = dict(os.environ)
    try:
        return _main_applet_webresearch()
    except SystemExit as _e:
        return _e.code if _e.code is not None else 0
    finally:
        os.environ.clear()
        os.environ.update(_saved_env)



def _run_all_folded_web_research_suites():
    rc = _run_extra_applet_webresearch()
    if rc not in (None, 0):
        import sys
        sys.exit(f"Folded test suite failed: exit code {rc}")

if __name__ == "__main__":
    _run_all_folded_web_research_suites()
    test_web_text_anchor_helpers_native()
    test_link_rank_scorer_ssot()
    test_judge_gate_loop_vs_stop()
    test_step_recording_shape()
    test_nav_strip_and_two_hop_drill()
    test_recency_flag_drives_time_range()
    test_source_registry_cluster()
    test_src_record_from_text_harvest()
    print("ALL mios_web_research tests passed")
