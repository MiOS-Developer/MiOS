# AI-hint: ROUTING layer extracted verbatim from server.py (refactor R2/mios_routing wave).
# AI-doc: usr/share/doc/mios/manual/routing.md

from __future__ import annotations

import os
import re
from typing import Optional

log = None
_COMPOUND_ACTION_ALT = ""
_COMPOUND_CONNECTIVE_ALT = ""
_FASTPATH_VERBS = frozenset()
_LAUNCH_TRIGGERS = frozenset()
_LAUNCH_FILLERS = []
_LAUNCH_LEAD_WORDS = frozenset()
_LAUNCH_TRAIL_WORDS = frozenset()

def configure(*, logger=None, compound_action_alt=None, compound_connective_alt=None,
              fastpath_verbs=None,
              launch_triggers=None, launch_fillers=None, launch_lead_words=None,
              launch_trail_words=None) -> None:
    """Inject the server.py logger + the _VERB_CATALOG-derived fast-path verb
    sets / launch phrase frozensets the routing layer reads."""
    global log, _COMPOUND_ACTION_ALT, _COMPOUND_CONNECTIVE_ALT, _FASTPATH_VERBS, _LAUNCH_TRIGGERS
    global _LAUNCH_FILLERS, _LAUNCH_LEAD_WORDS, _LAUNCH_TRAIL_WORDS
    if logger is not None:
        log = logger
    if compound_action_alt is not None:
        _COMPOUND_ACTION_ALT = compound_action_alt
    if compound_connective_alt is not None:
        _COMPOUND_CONNECTIVE_ALT = compound_connective_alt
    if fastpath_verbs is not None:
        _FASTPATH_VERBS = fastpath_verbs
    if launch_triggers is not None:
        _LAUNCH_TRIGGERS = launch_triggers
    if launch_fillers is not None:
        _LAUNCH_FILLERS = launch_fillers
    if launch_lead_words is not None:
        _LAUNCH_LEAD_WORDS = launch_lead_words
    if launch_trail_words is not None:
        _LAUNCH_TRAIL_WORDS = launch_trail_words

def _load_routing_domains() -> tuple[dict, bool]:
    try:
        import mios_db_config
        rt = mios_db_config.section(None, "routing")
        enable = str(rt.get("router_enable", "false")).lower() in {"true", "1", "yes", "on"}
        domains: dict = {}
        for dom, cfg in (rt.get("domains") or {}).items():
            if isinstance(cfg, dict):
                domains[str(dom)] = {"desc": str(cfg.get("desc", "")),
                                     "verbs": [str(v) for v in (cfg.get("verbs") or [])]}
        try:
            import psycopg
            from psycopg.rows import dict_row
            from mios_pipe.memory.pg import pg_config
            pcfg = pg_config()
            conn_str = (f"postgresql://{pcfg['user']}:{pcfg['password']}"
                        f"@{pcfg['host']}:{pcfg['port']}/{pcfg['dbname']}")
            with psycopg.connect(conn_str, connect_timeout=2) as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute("SELECT domain, verb_name FROM domain_verb;")
                    db_domains = {}
                    for r in cur.fetchall():
                        db_domains.setdefault(r["domain"], []).append(r["verb_name"])
                    for dom, verbs in db_domains.items():
                        if dom in domains:
                            domains[dom]["verbs"] = verbs
                        else:
                            domains[dom] = {"desc": f"Database domain {dom}", "verbs": verbs}
        except Exception as db_err:
            if log is not None:
                log.debug("Database routing domains overlay failed (using TOML baseline): %s", db_err)

        return domains, enable
    except Exception as e:
        if log is not None:
            log.warning("routing domains load failed: %s", e)
        else:
            import sys
            print(f"routing domains load failed: {e}", file=sys.stderr)
        return {}, False

def _load_routing_phrases(key: str) -> list:
    """Load a deterministic-launch SSOT phrase list from mios.toml [routing].<key>,
    lowercased + de-duplicated, longest-first (so multi-word phrases strip before
    their substrings). NO hardcoded English in code -- the lists are SSOT data.
    FAIL-SAFE: any error -> []."""
    try:
        import mios_db_config
        rt = mios_db_config.section(None, "routing")
        return sorted(
            {str(p).lower().strip() for p in (rt.get(key) or []) if str(p).strip()},
            key=len, reverse=True)
    except Exception as e:
        if log is not None:
            log.warning("routing phrases load failed (%s): %s", key, e)
        else:
            import sys
            print(f"routing phrases load failed ({key}): {e}", file=sys.stderr)
        return []

def _load_launch_fillers() -> list:
    """Trailing courtesy/location phrases (SSOT mios.toml [routing].launch_filler_
    phrases) stripped from a deterministic launch target so 'open notepad for me'
    -> open_app(name='notepad') and 'open spotify on my desktop' -> name='spotify'
 (e2e: filler bled into the app name, and 'on my desktop'
    forced the launch into the LLM path which mis-classified it as discovery)."""
    return _load_routing_phrases("launch_filler_phrases")

def _deterministic_action_route(user_text: str) -> Optional[dict]:
    t = (user_text or "").strip()
    if not t or "?" in t:
        return None
    if _COMPOUND_ACTION_ALT and "pc_type" in _FASTPATH_VERBS:
        _Q = "\"'‘’“”"
        _av = r"(?:" + _COMPOUND_ACTION_ALT + r")"
        _typ = None
        _mq = re.match(
            r"^\s*(?:\w+\s+){0,2}?" + _av + r"\b[\s:.\-]*["
            + _Q + r"](.+?)[" + _Q + r"]", t, re.IGNORECASE)
        if _mq:
            _typ = _mq.group(1).strip()
        else:
            _mh = re.match(
                r"^\s*" + _av + r"\b[\s:.\-]+(.+?)\s+\b(?:into|in)\b\s+\S",
                t, re.IGNORECASE)
            if _mh:
                _typ = _mh.group(1).strip().strip(_Q).strip()
        if _typ:
            return {"intent": "dispatch", "tool": "pc_type",
                    "args": {"text": _typ}, "_deterministic": True}
    if not t or len(t) > 80:
        return None
    words = t.split()
    if len(words) < 2:
        return None
    head = words[0].lower().strip(".,:;!\"'")
    if head not in _LAUNCH_TRIGGERS or "open_app" not in _FASTPATH_VERBS:
        return None
    rest = " ".join(words[1:]).strip()
    rest = rest.rstrip(" .,!;:")
    _low = rest.lower()
    _changed = True
    while _changed and rest:
        _changed = False
        for _f in _LAUNCH_FILLERS:
            if _f and _low.endswith(_f):
                rest = rest[:len(rest) - len(_f)].rstrip(" ,.")
                _low = rest.lower()
                _changed = True
                break
    _rw = rest.split()
    while _rw and _rw[0].lower() in _LAUNCH_LEAD_WORDS:
        _rw.pop(0)
    while _rw and _rw[-1].lower() in _LAUNCH_TRAIL_WORDS:
        _rw.pop()
    rest = " ".join(_rw).strip()
    _low = rest.lower()
    if not rest or len(rest.split()) > 3:
        return None
    # A remaining location is an explicit target request, not part of an app
    # name. Let the schema-aware route resolve its runtime session argument.
    if re.search(r"\s+(?:on|in)\s+\S", rest, re.IGNORECASE):
        return None
    if "://" in rest or (_COMPOUND_CONNECTIVE_ALT
                         and re.search(r"\b(?:" + _COMPOUND_CONNECTIVE_ALT + r")\b", _low)):
        return None
    return {"intent": "dispatch", "tool": "open_app",
            "args": {"name": rest}, "_deterministic": True}
