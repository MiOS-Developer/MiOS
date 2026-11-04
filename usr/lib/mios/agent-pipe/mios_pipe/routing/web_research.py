# AI-hint: WEB-RESEARCH enrichment subsystem extracted verbatim from server.py.
# AI-doc: usr/share/doc/mios/manual/routing.md

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Optional

import httpx

from mios_config import _toml_section
from mios_jsonsalvage import loads_lenient as _loads_lenient

log = logging.getLogger("mios-agent-pipe")

_is_action_domain = None
_current_date_str = None
_current_year = None
_routed_domain_var = None
_client_env_var = None
_sources_var = None
_conv_key_var = None
_src_turn_var = None
_SOURCES_REGISTRY = None
_SOURCES_REGISTRY_CAP = 64
MAX_SOURCES = 8
_WEB_ENRICH_VERBS = frozenset()
_LOCATION_SENSITIVE_PHRASES = ()
_JUDGE_MODEL = None
_JUDGE_ENDPOINT = None
WEB_RESEARCH_ENABLED = True
WEB_RESEARCH_PASSES = 4
WEB_RESEARCH_RESULTS = 8
WEB_RESEARCH_FANOUT = 3
WEB_RESEARCH_FETCH_N = 5
WEB_RESEARCH_FETCH_CHARS = 3000
WEB_RESEARCH_BLOCK_CHARS = 1200
WEB_RESEARCH_SEARCH_TIMEOUT = 30.0
WEB_RESEARCH_FETCH_TIMEOUT = 12.0
WEB_RESEARCH_CRAWL_FALLBACK = True
WEB_RESEARCH_MIN_CHARS = 300
WEB_RESEARCH_CRAWL_TIMEOUT = 45.0
WEB_RESEARCH_CRAWL_MAX = 6
WEB_RESEARCH_USE_NEWS_CATEGORY = False
WEB_RESEARCH_TIME_RANGE = ""
WEB_RESEARCH_RECENCY_RANGE = "month"
WEB_RESEARCH_MAX_ATTEMPTS = 5

def configure(*, is_action_domain=None, current_date_str=None, current_year=None,
              anchor_tokens=None, shares_anchor=None, url_has_path=None,
              clean_web_text=None, routed_domain_var=None,
              client_env_var=None, sources_var=None, conv_key_var=None,
              src_turn_var=None, sources_registry=None, sources_registry_cap=None,
              max_sources=None, web_enrich_verbs=None,
              location_sensitive_phrases=None, judge_model=None,
              judge_endpoint=None, web_research_enabled=None, web_research_passes=None,
              web_research_results=None, web_research_fanout=None,
              web_research_fetch_n=None, web_research_fetch_chars=None,
              web_research_block_chars=None, web_research_search_timeout=None,
              web_research_fetch_timeout=None, web_research_crawl_fallback=None,
              web_research_min_chars=None, web_research_crawl_timeout=None,
              web_research_crawl_max=None, web_research_use_news_category=None,
              web_research_time_range=None, web_research_recency_range=None,
              web_research_max_attempts=None) -> None:
    """Inject server.py runtime helpers, request contextvars and config consts.

    Constants are mapped to the EXACT original server-side global names; injected
    via ``is not None`` guards so a falsey-but-real value (0, 0.0, False, "")
    still overrides the placeholder."""
    global _is_action_domain, _current_date_str, _current_year, _anchor_tokens
    global _shares_anchor, _url_has_path, _clean_web_text
    global _routed_domain_var, _client_env_var, _WEB_ENRICH_VERBS
    global _sources_var, _conv_key_var, _src_turn_var, _SOURCES_REGISTRY
    global _SOURCES_REGISTRY_CAP, MAX_SOURCES
    global _LOCATION_SENSITIVE_PHRASES, _JUDGE_MODEL, _JUDGE_ENDPOINT
    global WEB_RESEARCH_ENABLED, WEB_RESEARCH_PASSES, WEB_RESEARCH_RESULTS
    global WEB_RESEARCH_FANOUT, WEB_RESEARCH_FETCH_N, WEB_RESEARCH_FETCH_CHARS
    global WEB_RESEARCH_BLOCK_CHARS, WEB_RESEARCH_SEARCH_TIMEOUT
    global WEB_RESEARCH_FETCH_TIMEOUT, WEB_RESEARCH_CRAWL_FALLBACK
    global WEB_RESEARCH_MIN_CHARS, WEB_RESEARCH_CRAWL_TIMEOUT, WEB_RESEARCH_CRAWL_MAX
    global WEB_RESEARCH_USE_NEWS_CATEGORY, WEB_RESEARCH_TIME_RANGE
    global WEB_RESEARCH_RECENCY_RANGE, WEB_RESEARCH_MAX_ATTEMPTS
    if is_action_domain is not None: _is_action_domain = is_action_domain
    if current_date_str is not None: _current_date_str = current_date_str
    if current_year is not None: _current_year = current_year
    if anchor_tokens is not None: _anchor_tokens = anchor_tokens
    if shares_anchor is not None: _shares_anchor = shares_anchor
    if url_has_path is not None: _url_has_path = url_has_path
    if clean_web_text is not None: _clean_web_text = clean_web_text
    if routed_domain_var is not None: _routed_domain_var = routed_domain_var
    if client_env_var is not None: _client_env_var = client_env_var
    if sources_var is not None: _sources_var = sources_var
    if conv_key_var is not None: _conv_key_var = conv_key_var
    if src_turn_var is not None: _src_turn_var = src_turn_var
    if sources_registry is not None: _SOURCES_REGISTRY = sources_registry
    if sources_registry_cap is not None: _SOURCES_REGISTRY_CAP = sources_registry_cap
    if max_sources is not None: MAX_SOURCES = max_sources
    if web_enrich_verbs is not None: _WEB_ENRICH_VERBS = web_enrich_verbs
    if location_sensitive_phrases is not None: _LOCATION_SENSITIVE_PHRASES = location_sensitive_phrases
    if judge_model is not None: _JUDGE_MODEL = judge_model
    if judge_endpoint is not None: _JUDGE_ENDPOINT = judge_endpoint
    if web_research_enabled is not None: WEB_RESEARCH_ENABLED = web_research_enabled
    if web_research_passes is not None: WEB_RESEARCH_PASSES = web_research_passes
    if web_research_results is not None: WEB_RESEARCH_RESULTS = web_research_results
    if web_research_fanout is not None: WEB_RESEARCH_FANOUT = web_research_fanout
    if web_research_fetch_n is not None: WEB_RESEARCH_FETCH_N = web_research_fetch_n
    if web_research_fetch_chars is not None: WEB_RESEARCH_FETCH_CHARS = web_research_fetch_chars
    if web_research_block_chars is not None: WEB_RESEARCH_BLOCK_CHARS = web_research_block_chars
    if web_research_search_timeout is not None: WEB_RESEARCH_SEARCH_TIMEOUT = web_research_search_timeout
    if web_research_fetch_timeout is not None: WEB_RESEARCH_FETCH_TIMEOUT = web_research_fetch_timeout
    if web_research_crawl_fallback is not None: WEB_RESEARCH_CRAWL_FALLBACK = web_research_crawl_fallback
    if web_research_min_chars is not None: WEB_RESEARCH_MIN_CHARS = web_research_min_chars
    if web_research_crawl_timeout is not None: WEB_RESEARCH_CRAWL_TIMEOUT = web_research_crawl_timeout
    if web_research_crawl_max is not None: WEB_RESEARCH_CRAWL_MAX = web_research_crawl_max
    if web_research_use_news_category is not None: WEB_RESEARCH_USE_NEWS_CATEGORY = web_research_use_news_category
    if web_research_time_range is not None: WEB_RESEARCH_TIME_RANGE = web_research_time_range
    if web_research_recency_range is not None: WEB_RESEARCH_RECENCY_RANGE = web_research_recency_range
    if web_research_max_attempts is not None: WEB_RESEARCH_MAX_ATTEMPTS = web_research_max_attempts

def _url_has_path(u: str) -> bool:
    """True when a URL points DEEPER than a site front page (has a real path) --
    a STRUCTURAL article-vs-homepage signal for ranking news results (no topic /
    English content). 'https://x.com/' and 'https://x.com' -> False; a story URL
    '.../world/region/a-story-slug/...' -> True. Import-free (called on the hot path)."""
    try:
        after = u.split("://", 1)[-1]
        path = after.split("/", 1)[1] if "/" in after else ""
        path = path.split("?", 1)[0].split("#", 1)[0]
        return bool(path.strip("/"))
    except Exception:  # noqa: BLE001
        return True

_MD_IMG_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_EMPTY_LINK_RE = re.compile(r"\[\s*\]\([^)]*\)")
_NAV_BULLET_RE = re.compile(r"(?m)^\s*[\*\-+]\s*\[[^\]]*\]\([^)]*\)\s*$")
_INLINE_LINK_RE = re.compile(r"\[([^\]]+)\]\((?:https?|ftp|mailto|javascript)[^)]*\)")
_DATA_URI_RE = re.compile(r"\(data:[^)]*\)")
_EMPTY_BULLET_RE = re.compile(r"(?m)^\s*[\*\-+]\s*[)\]\s]*$")
_MULTI_BLANK_RE = re.compile(r"\n{3,}")

def _clean_web_text(s: str) -> str:
    """Strip structural site-chrome from one fetched page's text so the block
    char budget holds real article content. Language-agnostic (markdown/URL
    structure only). Best-effort: any failure returns the input unchanged."""
    if not s:
        return s
    try:
        s = _MD_IMG_RE.sub("", s)
        s = _EMPTY_LINK_RE.sub("", s)
        s = _NAV_BULLET_RE.sub("", s)
        s = _INLINE_LINK_RE.sub(r"\1", s)
        s = _DATA_URI_RE.sub("", s)
        s = _EMPTY_BULLET_RE.sub("", s)
        s = _MULTI_BLANK_RE.sub("\n\n", s)
        return s.strip()
    except Exception:  # noqa: BLE001 -- never let cleanup break grounding
        return s

def _load_anchor_stopwords() -> frozenset:
    """Resolve the anchor stopword screen from SSOT: MIOS_SEARCH_ANCHOR_STOPWORDS, the
    CSV the resolver emits for [search].anchor_stopwords (Law 9: one canonical name, so
    the override is reachable) -> that table -> empty (degrade-open). Lowercased."""
    _csv = os.environ.get("MIOS_SEARCH_ANCHOR_STOPWORDS")
    if _csv not in (None, ""):
        return frozenset(t.strip().lower() for t in _csv.split(",") if t.strip())
    _v = _toml_section("search").get("anchor_stopwords")
    if isinstance(_v, list):
        return frozenset(str(x).strip().lower() for x in _v if str(x).strip())
    return frozenset()

_ANCHOR_STOPWORDS = _load_anchor_stopwords()
_CJK = ("぀-ヿ㄀-ㄯ㐀-䶿一-鿿"
        "豈-﫿가-힯")
_ANCHOR_TOKEN_RE = re.compile(rf"[{_CJK}]|[^\W_{_CJK}]+")

def _anchor_tokens(text: str) -> set:
    """Content tokens of `text` for topical-overlap anchoring: unicode-aware word tokens
    (CJK per-ideograph, other scripts whole), lowercased, bare numbers + the SSOT stopword
    screen removed, sub-3 ASCII fragments dropped (never length-gate non-Latin), Latin
    trailing-s plural fold."""
    out: set = set()
    for t in _ANCHOR_TOKEN_RE.findall(text or ""):
        t = t.lower()
        if t.isdigit():
            continue                            # a bare number / year is too weak an anchor
        if t.isascii() and len(t) < 3:
            continue                            # ASCII noise floor; never gate non-Latin
        if t in _ANCHOR_STOPWORDS:
            continue
        if len(t) >= 5 and t.endswith("s"):     # Latin plural fold (trailing -s)
            t = t[:-1]
        out.add(t)
    return out

def _shares_anchor(text: str, anchor: set) -> bool:
    """True when `text` shares >=1 content token with `anchor`. Degrades OPEN
    when the anchor is too thin (<2 tokens) to judge -- never over-filter."""
    if len(anchor) < 2:
        return True
    return bool(_anchor_tokens(text) & anchor)

_LINK_RANK_DEFAULTS = {
    "link_rank_mode": "heuristic",  # "heuristic" (structural, default) | "embed" (opt-in; degrade-open)
    "seg_base": 1,        # score per path segment (path depth = article-ness signal)
    "slug_weight": 2,     # bonus when the last segment is a long hyphenated headline slug
    "slug_min_len": 12,   # min last-segment length to count as a headline slug
    "digit_weight": 1,    # bonus when the path carries a digit (a date / article id)
    "anchor_weight": 2,   # bonus when the anchor text is long (a real headline)
    "anchor_min_len": 25, # min anchor-text length to count as a real headline
    "min_score": 2,       # drop shallow generic / utility links scoring below this
    "top_n": 6,           # keep at most this many ranked article links
}

def _link_rank_cfg() -> dict:
    """Resolve the article-link scorer's mode + weights/thresholds from SSOT
    (mios.toml [web_research]) layered over the degrade-open defaults. Each key
    falls back INDEPENDENTLY, so a partial or malformed [web_research] table still
    yields the byte-identical structural ranking for every key it omits. Never
    raises (degrade-open): any read/parse error returns the full defaults."""
    cfg = dict(_LINK_RANK_DEFAULTS)
    try:
        sect = _toml_section("web_research")
        if isinstance(sect, dict):
            for k, default in _LINK_RANK_DEFAULTS.items():
                v = sect.get(k)
                if v is None:
                    continue
                try:
                    if isinstance(default, str):
                        if isinstance(v, str) and v:
                            cfg[k] = v
                    elif isinstance(v, bool):
                        continue              # a stray bool never poisons an int weight
                    else:
                        cfg[k] = int(v)
                except (TypeError, ValueError):
                    continue                  # one bad key keeps its default, never raises
    except Exception:  # noqa: BLE001 -- degrade-open: any error -> full structural defaults
        return dict(_LINK_RANK_DEFAULTS)
    return cfg

def _rank_links_by_structure(cands: list, src_url: str, anchor: set,
                             cfg: Optional[dict] = None) -> list:
    if cfg is None:
        cfg = _link_rank_cfg()
    scored: list = []
    _seen_l: set = set()
    for atext, u in cands:
        if not u.startswith(("http://", "https://")) or u == src_url:
            continue
        if u in _seen_l or not _url_has_path(u):
            continue
        if atext.startswith("!") or "](" in atext:
            continue
        if len(anchor) >= 2 and not _shares_anchor(u + " " + atext, anchor):
            continue
        _tail = u.split("://", 1)[-1]
        _path = _tail[_tail.find("/"):] if "/" in _tail else ""
        _path = _path.split("?", 1)[0].split("#", 1)[0]
        _segs = [s for s in _path.split("/") if s]
        _last = (_segs[-1] if _segs else "").replace("_", "-")
        _score = cfg["seg_base"] * len(_segs)
        if "-" in _last and len(_last) >= cfg["slug_min_len"]:
            _score += cfg["slug_weight"]            # long hyphenated headline slug
        if any(c.isdigit() for c in _path):         # str.isdigit() is unicode-aware
            _score += cfg["digit_weight"]           # date / article id
        if len(atext) >= cfg["anchor_min_len"]:
            _score += cfg["anchor_weight"]          # long anchor = real headline
        if _score < cfg["min_score"]:               # shallow generic / utility link
            continue
        _seen_l.add(u)
        scored.append((_score, u))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [u for _s, u in scored[:cfg["top_n"]]]

def _rank_links_embed(cands: list, src_url: str, anchor: set, cfg: dict):
    return None

def _rank_links(cands: list, src_url: str, anchor: set,
                cfg: Optional[dict] = None) -> list:
    """Rank candidate article links per the SSOT link_rank_mode. Default 'heuristic'
    = the structural ranker. A non-default mode is tried first and DEGRADES OPEN to
    the structural ranker on a None result or ANY error (operator binding: a mode flip
    never breaks the drill)."""
    if cfg is None:
        cfg = _link_rank_cfg()
    mode = cfg.get("link_rank_mode") or "heuristic"
    if mode != "heuristic":
        try:
            ranked = _rank_links_embed(cands, src_url, anchor, cfg)
            if ranked is not None:
                return ranked
        except Exception:  # noqa: BLE001 -- degrade-open: non-default mode error -> structural
            log.debug("link-rank %r mode failed; degrading to structural", mode,
                      exc_info=True)
    return _rank_links_by_structure(cands, src_url, anchor, cfg)

def _is_port_open(port: int, host: str = "127.0.0.1") -> bool:
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.1)
            s.connect((host, port))
            return True
    except Exception:
        return False

async def _web_research_enrich(query: str, refined: Optional[dict],
                               emit=None, quick: bool = False) -> str:
    if not WEB_RESEARCH_ENABLED or not query or not query.strip():
        return ""
    if (refined or {}).get("intent") == "chat" and not (refined or {}).get("news"):
        return ""
    if (refined or {}).get("local_state"):
        return ""
    _webverbs = _WEB_ENRICH_VERBS | {"open_url"}
    hints = [str(t).lower().strip() for t in ((refined or {}).get("hint_tools") or [])]
    _web_flagged = bool((refined or {}).get("news") or (refined or {}).get("web")
                        or (refined or {}).get("deep")
                        or (refined or {}).get("deep_research")
                        or _routed_domain_var.get(None) == "web")
    if _is_action_domain(_routed_domain_var.get(None)):
        return ""
    if not _web_flagged and not any(h in _webverbs for h in hints):
        return ""

    documents = {}  # fetched publication metadata; search dates never certify a story
    _cenv = {}

    async def _search(q: str, news: bool = False, time_range: str = "") -> list:
        args = ["mios-web-search", "-n", str(WEB_RESEARCH_RESULTS),
                "--fanout", str(WEB_RESEARCH_FANOUT)]
        if news:
            args.append("--news")   # SearXNG news category -> real dated stories
        if time_range:
            args += ["--time-range", time_range]
        args.append(q[:400])
        try:
            p = await asyncio.create_subprocess_exec(
                *args, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL)
            o, _ = await asyncio.wait_for(
                p.communicate(), timeout=WEB_RESEARCH_SEARCH_TIMEOUT)
            d = _loads_lenient((o or b"{}").decode("utf-8", "replace") or "{}")
            return [r for r in (d.get("results") or []) if r.get("url")]
        except Exception as e:  # noqa: BLE001 -- best-effort
            try: p.kill()
            except: pass
            log.debug("web-research search failed: %s", e)
            return []

    async def _extract(url: str) -> str:
        try:
            p = await asyncio.create_subprocess_exec(
                "mios-web-extract", "-n", str(WEB_RESEARCH_FETCH_CHARS), url,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            o, _ = await asyncio.wait_for(
                p.communicate(), timeout=WEB_RESEARCH_FETCH_TIMEOUT)
            d = _loads_lenient((o or b"{}").decode("utf-8", "replace") or "{}")
            documents[url] = d if isinstance(d, dict) else {}
            return (d.get("content") or "").strip()
        except Exception:  # noqa: BLE001
            try: p.kill()
            except: pass
            return ""

    async def _crawl(url: str) -> tuple:
        if not _is_port_open(11235):
            return "", []
        try:
            p = await asyncio.create_subprocess_exec(
                "mios-crawl", "--json", "--max-chars", str(WEB_RESEARCH_FETCH_CHARS),
                url, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL)
            o, _ = await asyncio.wait_for(
                p.communicate(), timeout=WEB_RESEARCH_CRAWL_TIMEOUT)
            d = _loads_lenient((o or b"{}").decode("utf-8", "replace") or "{}")
            if d.get("success"):
                return (d.get("markdown") or "").strip(), (d.get("links") or [])
            return "", []
        except Exception:  # noqa: BLE001
            try: p.kill()
            except: pass
            return "", []

    async def _firecrawl(url: str) -> tuple:
        if not _is_port_open(3002):
            return "", []
        try:
            p = await asyncio.create_subprocess_exec(
                "mios-firecrawl", "--max-chars", str(WEB_RESEARCH_FETCH_CHARS),
                url, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL)
            o, _ = await asyncio.wait_for(
                p.communicate(), timeout=WEB_RESEARCH_CRAWL_TIMEOUT)
            d = _loads_lenient((o or b"{}").decode("utf-8", "replace") or "{}")
            if d.get("success"):
                return (d.get("markdown") or "").strip(), (d.get("links") or [])
            return "", []
        except Exception:  # noqa: BLE001
            try: p.kill()
            except: pass
            return "", []

    async def _judge_satisfied(user_q: str, gathered: str) -> tuple:
        if not gathered.strip():
            return False, "", ""
        sys_p = (
            "You decide whether the GATHERED web content actually DELIVERS what "
            "the USER QUERY asks for -- not merely whether it is on-topic. Reply "
            "JSON ONLY: {\"answerable\": true|false, "
            "\"better_query\": \"<sharper web-search query if NOT answerable, else "
            "empty>\", \"scrape_url\": \"<a concrete authoritative http(s) URL "
            "whose page most likely HAS the answer, to fetch directly -- e.g. a "
            "major outlet's lite/text news index for a current-events ask -- else "
            "empty>\"}. STRICT: answerable=true ONLY when the content contains the "
            "SPECIFIC deliverable the user asked for -- e.g. if they asked to "
            "'compile reviews', you need actual REVIEW verdicts/scores from named "
            "outlets, NOT pre-launch hype or 'the game exists' pages; if they "
            "asked 'what's new', you need actual DATED headlines/stories, NOT news "
            "homepages or nav text. answerable=false when the content is off-topic, "
            "junk (dictionary/brand/homepage), pre-launch filler, or missing the "
            "specific facts -- then KEEP LOOPING: give a sharper better_query (a "
            "DIFFERENT angle than before) + a concrete scrape_url. Better to loop "
            "again than to declare a thin/wrong page answerable. better_query + "
            "scrape_url must be concrete and AVOID vague words a search engine "
            "mis-matches to a brand/product. Prefer lightweight text/lite endpoints. "
            "STRICT RECENCY: If the USER QUERY asks for 'today', 'recent', 'latest', "
            "'now', or a specific year/date, the GATHERED content MUST contain facts "
            "or stories from that exact timeframe. If the content is stale or from "
            "a past year/timeframe relative to the CURRENT DATE (e.g. last year's "
            "results when the query asks for the current year/today), set answerable=false and you "
            "MUST suggest a sharper query with the correct year/date or a concrete "
            "authoritative news URL to scrape. Don't accept year-old listicles or posts as 'today'.")
        _msgs = [{"role": "system", "content": sys_p},
                 {"role": "user",
                  "content": f"CURRENT DATE: {_current_date_str()}\nUSER QUERY: {user_q}\n\nGATHERED:\n{gathered}"}]
        _url = (f"{_JUDGE_ENDPOINT}/chat/completions" if _JUDGE_ENDPOINT.endswith("/v1")
                else f"{_JUDGE_ENDPOINT}/v1/chat/completions")
        try:
            async with httpx.AsyncClient(timeout=20.0) as c:
                r = await c.post(_url, json={
                    "model": _JUDGE_MODEL, "messages": _msgs, "stream": False,
                    "temperature": 0.0, "max_tokens": 200,
                    "response_format": {"type": "json_object"}},
                    headers={"Content-Type": "application/json"})
                if r.status_code != 200:
                    return True, "", ""        # degrade open
                msg = (((r.json().get("choices") or [{}])[0]
                        .get("message") or {}).get("content")) or "{}"
            obj = _loads_lenient(msg)
            return (bool(obj.get("answerable")),
                    str(obj.get("better_query") or ""),
                    str(obj.get("scrape_url") or ""))
        except Exception:  # noqa: BLE001 -- never block the answer on the judge
            log.warning("Judge satisfied check encountered unexpected error", exc_info=True)
            return True, "", ""

    search_q = query.strip() if (query and query.strip()) else str((refined or {}).get("refined_text") or "").strip()
    try:
        _cenv = _client_env_var.get() if isinstance(_client_env_var.get(), dict) else {}
        _q_loc = str(_cenv.get("location") or "").strip()
        _ql = search_q.lower()
        _loc_sensitive = bool((refined or {}).get("needs_location")) or any(
            _ph in _ql for _ph in _LOCATION_SENSITIVE_PHRASES)
        if (_q_loc and _loc_sensitive
                and _q_loc.split(",")[0].strip().lower() not in search_q.lower()):
            search_q = f"{search_q} {_q_loc}".strip()
            log.info("web-research: location-scoped the query to %r", _q_loc)
    except Exception:  # noqa: BLE001
        pass
    _time_sensitive = bool((refined or {}).get("news") or (refined or {}).get("needs_recency"))
    if (_time_sensitive and not re.search(r"\b(?:19|20)\d{2}\b", search_q)):
        search_q = f"{search_q} {_current_year()}".strip()
    _use_news = WEB_RESEARCH_USE_NEWS_CATEGORY and bool((refined or {}).get("news"))
    _time_range = WEB_RESEARCH_TIME_RANGE or str((refined or {}).get("_news_time_range") or "")
    if not _time_range and _time_sensitive:
        _time_range = WEB_RESEARCH_RECENCY_RANGE
    _steps: list = []

    def _rec(_s: dict) -> None:
        _steps.append(_s)
        if emit:
            try:
                emit(_s)
            except Exception:  # noqa: BLE001
                pass

    content: dict = {}          # url -> REAL article text (>= MIN_CHARS)
    snippets: dict = {}         # url -> fallback snippet (thin/blocked page)
    touched: list = []          # results considered, in drill order
    seen: set = set()           # urls already fetched (dedup ACROSS attempts)
    crawl_budget = WEB_RESEARCH_CRAWL_MAX
    link_budget = WEB_RESEARCH_CRAWL_MAX   # 2-hop article-link drill budget
    _max_att = 1 if quick else WEB_RESEARCH_MAX_ATTEMPTS
    want = max(1, WEB_RESEARCH_FETCH_N)
    n_crawled = 0               # pages whose richest text came from a deep engine
    passes = 0
    search_q_now = search_q
    _anchor = _anchor_tokens(f"{query} {(refined or {}).get('refined_text') or ''}")

    async def _fetch_all(url: str) -> tuple:
        nonlocal crawl_budget
        jobs = [("read", _extract(url))]
        if WEB_RESEARCH_CRAWL_FALLBACK and crawl_budget > 0:
            crawl_budget -= 1
            jobs += [("deep-crawl", _crawl(url)), ("firecrawl", _firecrawl(url))]
        outs = await asyncio.gather(*[j for _, j in jobs])
        cand: dict = {}
        links: list = []
        for (label, _), out in zip(jobs, outs):
            if isinstance(out, tuple):
                _txt, _lks = out
                cand[label] = _txt or ""
                links.extend(_lks or [])
            else:
                cand[label] = out or ""
        links.extend(documents.get(url, {}).get("links") or [])
        fc = cand.get("firecrawl", "")
        if len(fc) >= WEB_RESEARCH_MIN_CHARS:
            return fc, "firecrawl", links
        best, eng = "", "read"
        for label, text in cand.items():
            if len(text) > len(best):
                best, eng = text, label
        return best, eng, links

    _md_link_re = re.compile(r"(?<!\!)\[([^\]]{0,200})\]\((https?://[^)\s]+)\)")

    def _rank_article_links(links: list, text: str, anchor: set,
                            src_url: str) -> list:
        cands: list = []   # (anchor_text, url)
        for it in (links or []):
            u = it if isinstance(it, str) else (
                (it.get("href") or it.get("url") or "") if isinstance(it, dict) else "")
            if u:
                cands.append(("", u.strip()))
        for m in _md_link_re.finditer(text or ""):
            cands.append((m.group(1).strip(), m.group(2).strip()))
        return _rank_links(cands, src_url, anchor)

    _md_any_re = re.compile(r"!?\[[^\]]*\]\([^)\s]*\)")

    def _strip_nav_chrome(md: str) -> str:
        if not md:
            return md
        out: list = []
        for ln in md.splitlines():
            s = ln.strip()
            if not s:
                out.append(ln)
                continue
            spans = _md_any_re.findall(s)
            link_chars = sum(len(x) for x in spans)
            prose = _md_any_re.sub(" ", s)
            prose_words = len(re.findall(r"[A-Za-z]{2,}", prose))
            if len(spans) >= 2 and link_chars >= 0.6 * len(s) and prose_words <= 6:
                continue   # link-dominated menu/nav/footer line -> drop
            out.append(ln)
        _txt = "\n".join(out)
        return re.sub(r"\n{3,}", "\n\n", _txt).strip()

    if (refined or {}).get("news"):
        from mios_verity import _news_research_evidence
        return await _news_research_evidence(
            search_q, refined, search=_search, fetch=_fetch_all, documents=documents,
            rank_links=_rank_article_links, today=_current_date_str(), time_range=_time_range,
            news_category=_use_news, timezone=str(_cenv.get("timezone") or ""),
            fetch_n=want, passes=WEB_RESEARCH_PASSES, crawl_max=link_budget,
            min_chars=WEB_RESEARCH_MIN_CHARS, block_chars=WEB_RESEARCH_BLOCK_CHARS,
            record=_src_record, emit=emit)

    _named_urls = [u.rstrip('.,);]}>"\'') for u in _SRC_URL_RE.findall(
        f"{query or ''} {(refined or {}).get('refined_text') or ''}")]
    for _nu in _named_urls[:3]:
        if not _nu.startswith("http") or _nu in seen:
            continue
        seen.add(_nu)
        try:
            _ntxt = await _extract(_nu)
        except Exception:  # noqa: BLE001
            _ntxt = ""
        if _ntxt and len(_ntxt) >= WEB_RESEARCH_MIN_CHARS:
            content[_nu] = _strip_nav_chrome(_ntxt)
            touched.append({"url": _nu, "title": "named source", "content": ""})
            _src_record([{"url": _nu, "title": "named source"}])
            log.info("web-research: direct-extracted named url %s (%dB) -> skip search",
                     _nu, len(_ntxt))
        elif _ntxt:
            snippets[_nu] = _ntxt
            _src_record([{"url": _nu, "title": "named source"}])
    if content:   # a named url grounded the turn -> skip web_search (no junk-anchor)
        _max_att = 0

    for attempt in range(1, _max_att + 1):
        _rec({"emoji": "🔎", "label": "searching the web",
              "detail": (("news · " if _use_news else
                          (_time_range + " · " if _time_range else ""))
                         + search_q_now)[:72]})
        results = await _search(search_q_now, news=_use_news, time_range=_time_range)
        if _anchor and results:
            _rel = [r for r in results if _shares_anchor(
                f"{r.get('title', '')} {r.get('url', '')} {r.get('content', '')}",
                _anchor)]
            if len(_rel) < len(results):
                log.info("web-research: anchor-filtered %d/%d off-topic results",
                         len(results) - len(_rel), len(results))
            results = _rel
        ordered: list = []
        for r in (results or []):
            u = r.get("url", "")
            if u and u not in seen:
                ordered.append(r)
        _pathful = [r for r in ordered
                    if r.get("publishedDate") or _url_has_path(r.get("url", ""))]
        if _pathful:
            _homes = [r for r in ordered if r not in _pathful]
            ordered = _pathful + _homes[:2]
            if _homes:
                log.info("web-research: %d content URLs + %d index seed(s) for the "
                         "2-hop article drill", len(_pathful), min(len(_homes), 2))
        idx = 0
        for _p in range(1, WEB_RESEARCH_PASSES + 1):
            passes += 1
            batch = ordered[idx:idx + want]
            if not batch:
                break
            idx += len(batch)
            touched.extend(batch)
            for r in batch:
                seen.add(r.get("url", ""))
            fetched = await asyncio.gather(*[_fetch_all(r["url"]) for r in batch])
            _hop2: list = []   # article links harvested from index pages this batch
            for r, (best, eng, _links) in zip(batch, fetched):
                if eng != "read" and best:
                    n_crawled += 1
                if len(best) >= WEB_RESEARCH_MIN_CHARS:
                    content[r["url"]] = _strip_nav_chrome(best)   # clean article body
                    _src_record([r])   # REAL article fetched -> a citable source
                elif best:
                    snippets[r["url"]] = best          # thin/blocked -> snippet
                    _src_record([r])   # thin but a real, fetched source
                _ttl = (str(r.get("title", "")).strip() or r.get("url", ""))[:60]
                _rec({"emoji": "📖",
                      "label": ("reading the page deeply"
                                if eng in ("deep-crawl", "firecrawl")
                                else "reading the page"),
                      "detail": _ttl})
                _indexish = (not _url_has_path(r.get("url", ""))
                             or len(best) < WEB_RESEARCH_MIN_CHARS)
                if link_budget > 0 and _indexish and (_links or best):
                    _hop2.extend(_rank_article_links(
                        _links, best, _anchor, r.get("url", "")))
            _hop2 = [u for u in dict.fromkeys(_hop2)
                     if u not in seen][:max(0, min(link_budget, want))]
            if _hop2:
                link_budget -= len(_hop2)
                for u in _hop2:
                    seen.add(u)
                _drilled = await asyncio.gather(*[_fetch_all(u) for u in _hop2])
                for u, (best, eng, _l) in zip(_hop2, _drilled):
                    if eng != "read" and best:
                        n_crawled += 1
                    if len(best) >= WEB_RESEARCH_MIN_CHARS:
                        content[u] = _strip_nav_chrome(best)   # clean article body
                    elif best:
                        snippets[u] = best
                    touched.append({"url": u, "title": "story", "content": ""})
                    _rec({"emoji": "📖", "label": "reading the story",
                          "detail": u[:60]})
            if len(content) >= want:
                break
        if attempt >= _max_att:
            break
        _gathered = "\n\n".join(
            ((content.get(r.get("url", "")) or snippets.get(r.get("url", ""))
              or str(r.get("content", "")))[:600]) for r in touched)[:5000]
        ok, better_q, scrape_url = await _judge_satisfied(query, _gathered)
        if ok:
            break
        su = scrape_url.strip()
        _scraped = False
        if su.startswith(("http://", "https://")) and su not in seen:
            seen.add(su)
            _rec({"emoji": "📖", "label": "reading the best source",
                  "detail": su[:60]})
            _md, _ = await _firecrawl(su)
            if len(_md) >= WEB_RESEARCH_MIN_CHARS:
                content[su] = _strip_nav_chrome(_md)
                touched.append({"url": su, "title": "news source (judge-picked)",
                                "content": ""})
                _src_record([{"url": su, "title": "news source"}])  # citable source
                n_crawled += 1
                _scraped = True
        _bq = better_q.strip() if better_q else ""
        _usable_bq = bool(_bq and _bq != search_q_now and _shares_anchor(_bq, _anchor))
        if _usable_bq:
            search_q_now = _bq
        elif _bq:
            log.info("web-research: dropped off-anchor re-search %r", better_q)
        if not _usable_bq and not _scraped:
            log.info("web-research: no usable sharper query -> stop drilling "
                     "(gathered %d real / %d snippet)", len(content), len(snippets))
            break
        _rec({"emoji": "🔁", "label": f"retry {attempt + 1}",
              "detail": ("not answered -> " + search_q_now)[:60]})
    def _rank(r: dict) -> tuple:
        u = r.get("url", "")
        picked = 0 if "judge-picked" in str(r.get("title", "")) else 1
        real = 0 if u in content else (1 if u in snippets else 2)
        return (picked, real)
    _ranked = sorted({r.get("url", ""): r for r in touched}.values(), key=_rank)
    blocks: list = []
    for i, r in enumerate(_ranked, start=1):
        u = r.get("url", "")
        title = str(r.get("title", "")).strip()
        body = (content.get(u) or snippets.get(u)
                or str(r.get("content", "")).strip())
        body = _clean_web_text(body)  # strip nav/image/share chrome before the cap
        if body:
            blocks.append(f"[{i}] {title} ({u})\n{body[:WEB_RESEARCH_BLOCK_CHARS]}")
    if not blocks:
        return ""
    log.info("web-research: %d results, %d real / %d snippet, %d deep-crawled "
             "over %d pass(es) for %.60r",
             len(touched), len(content), len(snippets), n_crawled, passes, query)
    if isinstance(refined, dict):
        refined["_web_stats"] = {"sources": len(blocks), "real": len(content),
                                 "crawled": n_crawled, "passes": passes}
        refined["_web_steps"] = _steps
        refined["_web_sources"] = "\n\n".join(blocks)
    return ("LIVE WEB RESEARCH. The following is retrieved source text, not proof that every "
            "result is relevant or current. Answer only from evidence that supports the user's "
            "question and cite its numbered source. Index titles, snippets, loading pages and "
            "a successful fetch do not establish a factual story. Publication/update dates do "
            "not establish event dates. State gaps or uncertainty plainly; never fill missing "
            "current facts with model memory or relabel older reports as today's events.\n\n"
            + "\n\n".join(blocks))

def _src_turn_key() -> str:
    """Stable per-turn key shared by the primary + every council/DAG secondary.
    Prefers the explicit turn-id (propagated to sub-requests) over the per-request
    conv key, so a re-entrant secondary records into the parent turn's bucket."""
    try:
        _t = _src_turn_var.get()
        if _t:
            return str(_t)
    except Exception:  # noqa: BLE001
        pass
    try:
        return str(_conv_key_var.get() or "")
    except Exception:  # noqa: BLE001
        return ""

def _src_turn_init() -> None:
    """Open a fresh registry bucket for THIS turn (call once at chat_completions
    entry, after _conv_key_var is set). Trims the registry to its cap."""
    _k = _src_turn_key()
    if not _k:
        return
    _SOURCES_REGISTRY[_k] = []
    if len(_SOURCES_REGISTRY) > _SOURCES_REGISTRY_CAP:
        try:  # drop oldest insertion-ordered entries (dict preserves order)
            for _old in list(_SOURCES_REGISTRY.keys())[:-_SOURCES_REGISTRY_CAP]:
                _SOURCES_REGISTRY.pop(_old, None)
        except Exception:  # noqa: BLE001
            pass

def _src_record(items) -> None:
    """Record real (title,url) pairs from a web_search/extract result list into BOTH
    the turn-scoped contextvar bucket AND the module-level registry (keyed by the
    turn key) so the parent finalize sees sources collected by child agents too.
    Degrade-open: odd shape / no turn key -> safe no-op."""
    if not items:
        return
    _bucket = _sources_var.get()
    _reg = _SOURCES_REGISTRY.get(_src_turn_key()) if _src_turn_key() else None
    if _bucket is None and _reg is None:
        return
    try:
        _added = 0
        for _it in items:
            if not isinstance(_it, dict):
                continue
            _u = str(_it.get("url") or _it.get("link") or "").strip()
            _u = _u.rstrip("\\").rstrip(".,;:)]}>\"'").rstrip("\\")
            _t = str(_it.get("title") or _it.get("name") or "").strip()
            if not _u.startswith("http"):
                continue
            if _bucket is not None:
                _bucket.append((_t, _u))
            if _reg is not None:
                _reg.append((_t, _u))
            _added += 1
        if _added:
            log.debug("src: recorded %d into turn %s", _added, _src_turn_key()[:40])
    except Exception:  # noqa: BLE001 -- never break tool execution
        pass

def _src_collected() -> list:
    """Deduped (by url, order-preserved) sources from the contextvar bucket AND the
    turn registry (cross-agent), capped to MAX_SOURCES."""
    _merged: list = []
    _b = _sources_var.get()
    if _b:
        _merged.extend(_b)
    _r = _SOURCES_REGISTRY.get(_src_turn_key()) if _src_turn_key() else None
    if _r:
        _merged.extend(_r)
    if not _merged:
        return []
    _pathful = [(_t, _u) for (_t, _u) in _merged if _url_has_path(_u)]
    _use = _pathful if _pathful else _merged
    _seen: set = set()
    _out: list = []
    for _t, _u in _use:
        if _u in _seen:
            continue
        _seen.add(_u)
        _out.append((_t, _u))
        if len(_out) >= MAX_SOURCES:
            break
    return _out

def _sources_markdown(refs: list) -> str:
    """A deterministic '**Sources:**' markdown list of the REAL urls (numbered)."""
    if not refs:
        return ""
    return "\n\n**Sources:**\n" + "\n".join(
        f"{i + 1}. {(_t or _u)[:90]} — {_u}" for i, (_t, _u) in enumerate(refs))

def _sources_metadata(refs: list) -> list:
    """Structured citation metadata (operator 'A2A or metadata'): real {n,title,url}
    objects attached to the response so clients render citations from REAL sources."""
    return [{"n": i + 1, "title": _t, "url": _u} for i, (_t, _u) in enumerate(refs)]

def _sources_annotations(refs: list, text: str) -> list:
    out: list = []
    _txt = text or ""
    for _ref in (refs or []):
        try:
            _t, _u = _ref
        except (ValueError, TypeError):
            continue
        if not _u:
            continue
        _i = _txt.find(_u)
        out.append({
            "type": "url_citation",
            "url": _u,
            "title": (_t or _u),
            "start_index": (_i if _i >= 0 else 0),
            "end_index": (_i + len(_u) if _i >= 0 else 0),
        })
    return out

def _filter_relevant_sources(refs: list, *texts: str) -> list:
    if not refs:
        return refs
    _blob = " ".join(t for t in texts if t).lower()
    if not _blob:
        return refs
    _kept: list = []
    for _ref in refs:
        try:
            _t, _u = _ref
        except (ValueError, TypeError):
            continue
        _dom = re.sub(r"^https?://(www\.)?", "", str(_u or "")).split("/")[0].lower()
        _stem = _dom.split(".")[0] if _dom else ""
        _words = set(re.findall(r"[a-z0-9]{4,}", str(_t or "").lower()))
        if (_stem and len(_stem) >= 4 and _stem in _blob) or any(_w in _blob for _w in _words):
            _kept.append((_t, _u))
    return _kept if _kept else refs

_SRC_LINE_RE = re.compile(
    r"^\s*\d+\.\s+(.*?)\s+[—\-]+\s+(https?://\S+?)\s*$", re.MULTILINE)
_SRC_URL_RE = re.compile(r"https?://[^\s\)\]\}<>\"']+")

def _src_record_from_text(text: str) -> None:
    """Harvest sources from an answer's appended Sources block; fall back to bare
    URLs only if no numbered block is present. Records into the current turn bucket."""
    if not text or "http" not in text:
        return
    _items = [{"title": _m.group(1).strip(), "url": _m.group(2).strip()}
              for _m in _SRC_LINE_RE.finditer(text)]
    if not _items:
        _items = [{"title": "", "url": _u} for _u in _SRC_URL_RE.findall(text)]
    if _items:
        _src_record(_items)

def _harvest_sub_sources(rj, content: str) -> None:
    """Pull a dispatched sub-agent's REAL sources into the parent turn: prefer the
    structured mios_sources JSON (survives when the leaf passes custom fields
    through), else parse the answer's appended Sources block / bare URLs."""
    try:
        _ms = rj.get("mios_sources") if isinstance(rj, dict) else None
    except Exception:  # noqa: BLE001
        _ms = None
    if _ms:
        _src_record(_ms)
        return
    _src_record_from_text(content or "")
