# AI-hint: Embedding TOOL/APP semantic-search core extracted verbatim from server.py (refactor R10 toolsearch wave).
# AI-doc: usr/share/doc/mios/manual/routing.md

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import time
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse

log = logging.getLogger("mios-agent-pipe")

_get_client = None
_VERB_CATALOG: dict = {}
_MCP_CLIENT_TOOLS: dict = {}
_MCP_CLIENT_LOCK = None
_loads_lenient = None
_embed_one = None

_EMBED_COOLDOWN_SECS = float(
    os.environ.get("MIOS_EMBED_COOLDOWN_SECS", "60") or 60)
_embed_cooldown_until = 0.0

def _embed_in_cooldown() -> bool:
    """True while we are backing off after an embed-backend connection failure."""
    return time.time() < _embed_cooldown_until

def _embed_note_failure() -> None:
    """Arm the cooldown window after an embed call came back empty (backend likely
    down). Subsequent loop iterations short-circuit until the window lapses."""
    global _embed_cooldown_until
    _embed_cooldown_until = time.time() + _EMBED_COOLDOWN_SECS

def configure(*, get_client=None, verb_catalog=None, mcp_client_tools=None,
              mcp_client_lock=None, loads_lenient=None, embed_one=None) -> None:
    """Inject server.py's runtime deps (HTTP client, verb catalog, MCP registry +
    lock, the per-vector embedder, the lenient JSON loader). Mutable
    catalogs/registries are injected BY REFERENCE so server-side mutation stays
    visible. The cosine metric + verb embed-text/fingerprint helpers are native
    here, not injected."""
    global _get_client, _VERB_CATALOG, _MCP_CLIENT_TOOLS, _MCP_CLIENT_LOCK
    global _loads_lenient, _embed_one
    if get_client is not None:
        _get_client = get_client
    if verb_catalog is not None:
        _VERB_CATALOG = verb_catalog
    if mcp_client_tools is not None:
        _MCP_CLIENT_TOOLS = mcp_client_tools
    if mcp_client_lock is not None:
        _MCP_CLIENT_LOCK = mcp_client_lock
    if loads_lenient is not None:
        _loads_lenient = loads_lenient
    if embed_one is not None:
        _embed_one = embed_one

def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    import math
    return dot / (math.sqrt(na) * math.sqrt(nb))

def _verb_embed_text(vname: str, vcfg: dict) -> str:
    """P1 TDWA: the text embedded for verb retrieval -- the MODEL-FACING name (the P1
    model_name alias if any, else the key) + the description + the synthetic example
    queries. Cosine selection then rides realistic user phrasings, not just the terse
    desc (ScaleMCP/TDWA: the accuracy ceiling lives in the description, not the model)."""
    facing = str(vcfg.get("model_name", "") or "").strip() or vname
    base = f"{facing}: {vcfg.get('desc','')}".strip()
    ex = [str(e).strip() for e in (vcfg.get("examples") or []) if str(e).strip()]
    if ex:
        base += "\nExample requests: " + " | ".join(ex)
    return base

def _verb_embed_fingerprint() -> str:
    """Hash over every embeddable verb's (key, embed-text). Any rename / desc edit /
    example change flips it -> the persisted cache is rebuilt instead of serving stale
    vectors (the old gap-fill loader only added NEW verbs; it never noticed a changed
    description, so a re-described verb kept its old embedding forever)."""
    h = hashlib.sha256()
    for vname in sorted(_VERB_CATALOG):
        vcfg = _VERB_CATALOG[vname]
        if vcfg.get("tier") == "rare":
            continue
        h.update(vname.encode("utf-8")); h.update(b"\x00")
        h.update(_verb_embed_text(vname, vcfg).encode("utf-8")); h.update(b"\x00")
    return h.hexdigest()

_VERB_EMBEDDINGS: dict[str, list[float]] = {}
_VERB_EMBEDDINGS_LOCK = asyncio.Lock()
_MCP_EMBEDDINGS: dict[str, list[float]] = {}

def _tool_embedding(name: str):
    """Retrieval vector for a tool name: a native verb (by resolved key) OR an external MCP
    tool (by its mcp.<id>.<tool> name). None if neither is embedded (-> priority fallback)."""
    v = _VERB_EMBEDDINGS.get(name)
    return v if v is not None else _MCP_EMBEDDINGS.get(name)

async def _mcp_embed_new_tools() -> None:
    """Embed every registered MCP tool not yet in _MCP_EMBEDDINGS (best-effort, off the
    hot path -- called at the end of a server probe). Degrade-open: an embed outage just
    leaves the tool on its name-keyword priority fallback, never breaks the surface."""
    try:
        async with (_MCP_CLIENT_LOCK or contextlib.nullcontext()):
            items = [(k, dict(v)) for k, v in _MCP_CLIENT_TOOLS.items()
                     if k not in _MCP_EMBEDDINGS]
        for k, info in items:
            tool = str(info.get("tool") or "")
            ns = str(info.get("namespace") or "")
            facing = tool if (ns and tool.startswith(ns)) else (ns + tool)
            txt = f"{facing}: {info.get('description') or ''}".strip()
            ex = [str(e).strip() for e in (info.get("examples") or []) if str(e).strip()]
            if ex:
                txt += "\nExample requests: " + " | ".join(ex)
            vec = await _embed_one(txt, prefix="search_document: ")
            if vec:
                _MCP_EMBEDDINGS[k] = vec
    except Exception:  # noqa: BLE001
        pass

async def _ensure_verb_embeddings() -> None:
    """Compute embeddings for tier=core+common verbs. Persisted to
    the postgres database table 'verb' (PG native) with a fallback to
    /var/lib/mios/agent-env/verb-embeddings.json when PG is unavailable.
    Hidden by lock."""
    async with _VERB_EMBEDDINGS_LOCK:
        if _VERB_EMBEDDINGS:
            return
        fp = _verb_embed_fingerprint()

        pg_success = False
        try:
            from mios_pg import execute as pg_execute
            rows = await pg_execute(
                "SELECT name, emb, emb_model, emb_version FROM verb WHERE hidden = false AND (tier = 'core' OR tier = 'common')",
                fetch=True
            )
            if isinstance(rows, list):
                pg_success = True
                db_embs = {}
                for r in rows:
                    name = r.get("name")
                    emb = r.get("emb")
                    ver = r.get("emb_version")
                    if name and isinstance(emb, list) and emb and ver == fp:
                        db_embs[name] = [float(x) for x in emb]

                for vname, vcfg in _VERB_CATALOG.items():
                    if vcfg.get("tier") == "rare":
                        continue
                    if vname in db_embs:
                        _VERB_EMBEDDINGS[vname] = db_embs[vname]

                rebuilt = False
                for vname, vcfg in _VERB_CATALOG.items():
                    if vcfg.get("tier") == "rare":
                        continue
                    if vname in _VERB_EMBEDDINGS:
                        continue
                    if _embed_in_cooldown():
                        break
                    vec = await _embed_one(_verb_embed_text(vname, vcfg), prefix="search_document: ")
                    if not vec:
                        _embed_note_failure()
                        break
                    _VERB_EMBEDDINGS[vname] = vec
                    rebuilt = True
                    try:
                        await pg_execute(
                            "UPDATE verb SET emb = %(emb)s::vector, emb_model = %(model)s, emb_version = %(ver)s WHERE name = %(name)s",
                            {"emb": vec, "model": os.environ.get("MIOS_AI_EMBED_MODEL") or "nomic-embed-text", "ver": fp, "name": vname}
                        )
                    except Exception as e_up:
                        log.debug("Failed to update database verb embedding for %s: %s", vname, e_up)
                log.info("verb database embeddings ready: %d entries (rebuilt=%s)",
                         len(_VERB_EMBEDDINGS), rebuilt)
        except Exception as e_pg:
            log.debug("Database verb embeddings lookup failed (falling back to JSON): %s", e_pg)
            pg_success = False

        if not pg_success:
            cached = _load_persisted_embeddings(_VERB_EMBED_PERSIST)
            if cached and cached.get("__fingerprint__") == fp:
                for vname, vcfg in _VERB_CATALOG.items():
                    if vcfg.get("tier") == "rare":
                        continue
                    vec = cached.get(vname)
                    if isinstance(vec, list) and vec:
                        _VERB_EMBEDDINGS[vname] = [float(x) for x in vec]
            rebuilt = False
            for vname, vcfg in _VERB_CATALOG.items():
                if vcfg.get("tier") == "rare":
                    continue
                if vname in _VERB_EMBEDDINGS:
                    continue
                if _embed_in_cooldown():
                    break
                vec = await _embed_one(_verb_embed_text(vname, vcfg))
                if not vec:
                    _embed_note_failure()
                    break
                _VERB_EMBEDDINGS[vname] = vec
                rebuilt = True
            if rebuilt:
                _save_persisted_embeddings(
                    _VERB_EMBED_PERSIST, {"__fingerprint__": fp, **_VERB_EMBEDDINGS})
            log.info("verb JSON embeddings ready: %d entries (rebuilt=%s)",
                     len(_VERB_EMBEDDINGS), rebuilt)

_APP_EMBEDDINGS: dict[str, dict] = {}   # key -> {vec, record}
_APP_INV_MTIME: float = 0.0
_APP_INV_LOCK = asyncio.Lock()
_APP_INV_CACHE_FILE = os.environ.get(
    "MIOS_APP_INV_CACHE",
    "/var/lib/mios/agent-env/apps-inventory.ndjson",
)
_APP_EMBED_PERSIST = os.environ.get(
    "MIOS_APP_EMBED_PERSIST",
    "/var/lib/mios/agent-env/apps-embeddings.json",
)
_VERB_EMBED_PERSIST = os.environ.get(
    "MIOS_VERB_EMBED_PERSIST",
    "/var/lib/mios/agent-env/verb-embeddings.json",
)

def _load_persisted_embeddings(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}

def _save_persisted_embeddings(path: str, data: dict) -> None:
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except OSError as e:
        log.warning("embedding persist failed: %s -> %s", path, e)

async def _refresh_app_inventory(force: bool = False) -> None:
    """Re-run `mios-apps --json` if the cache is stale (>5min) or
    missing, parse the NDJSON, embed any new records. Existing
    records reuse persisted embeddings. Persisted to disk so a
    restart doesn't trigger a 4-5s blocking embed flood."""
    global _APP_INV_MTIME
    async with _APP_INV_LOCK:
        if not _APP_EMBEDDINGS:
            cached = _load_persisted_embeddings(_APP_EMBED_PERSIST)
            if isinstance(cached, dict):
                for k, v in cached.items():
                    if (isinstance(v, dict) and isinstance(v.get("vec"), list)
                            and isinstance(v.get("record"), dict)):
                        _APP_EMBEDDINGS[k] = {
                            "vec": [float(x) for x in v["vec"]],
                            "record": v["record"],
                        }
        try:
            st = os.stat(_APP_INV_CACHE_FILE)
            age = time.time() - st.st_mtime
            need_refresh = force or age > 300
        except OSError:
            need_refresh = True
        if need_refresh:
            try:
                os.makedirs(os.path.dirname(_APP_INV_CACHE_FILE), exist_ok=True)
                proc = await asyncio.create_subprocess_exec(
                    "/usr/libexec/mios/mios-apps", "--json",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
                with open(_APP_INV_CACHE_FILE, "wb") as f:
                    f.write(stdout)
            except Exception as e:
                try: proc.kill()
                except: pass
                log.warning("mios-apps inventory refresh failed: %s", e)
                return
        try:
            with open(_APP_INV_CACHE_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except OSError:
            return
        seen_keys: set[str] = set()
        added = 0
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                rec = _loads_lenient(line)
            except json.JSONDecodeError:
                continue
            key = f"{rec.get('category','')}::{rec.get('name','')}::{rec.get('launch','')}"
            seen_keys.add(key)
            if key in _APP_EMBEDDINGS:
                continue
            if _embed_in_cooldown():
                continue
            blob = f"{rec.get('name','')}: {rec.get('description','')}".strip()
            vec = await _embed_one(blob, prefix="search_document: ")
            if not vec:
                _embed_note_failure()
                continue
            _APP_EMBEDDINGS[key] = {"vec": vec, "record": rec}
            added += 1
        stale = [k for k in _APP_EMBEDDINGS if k not in seen_keys]
        for k in stale:
            _APP_EMBEDDINGS.pop(k, None)
        if added or stale:
            _save_persisted_embeddings(_APP_EMBED_PERSIST, _APP_EMBEDDINGS)
            log.info("app inventory: +%d new / -%d stale = %d total",
                     added, len(stale), len(_APP_EMBEDDINGS))
        try:
            _APP_INV_MTIME = os.stat(_APP_INV_CACHE_FILE).st_mtime
        except OSError:
            pass

async def tool_search_logic(query: str = "", limit: int = 5, namespace: str = "",
                            tier: str = "", detail_level: str = "full") -> JSONResponse:
    """Logic for GET /v1/tool-search (server.py keeps the thin @app route)."""
    if not query.strip():
        return JSONResponse({"hits": [], "error": "empty query"})
    dl = (detail_level or "full").strip().lower()
    ns_f = (namespace or "").strip()
    tier_f = (tier or "").strip().lower()

    def _meta(name):  # -> (namespace, tier, sig, desc)
        if name.startswith("mcp."):
            mi = _MCP_CLIENT_TOOLS.get(name) or {}
            return (str(mi.get("namespace") or ""), str(mi.get("tier") or "rare"),
                    "", str(mi.get("description") or ""))
        v = _VERB_CATALOG.get(name) or {}
        return ("", str(v.get("tier") or ""), str(v.get("sig") or ""),
                str(v.get("desc") or ""))

    def _passes(name):
        ns, tr, _s, _d = _meta(name)
        return (not (ns_f and ns != ns_f)) and (not (tier_f and tr != tier_f))

    def _shape(name, score):
        ns, tr, sig, desc = _meta(name)
        sc = round(float(score), 4)
        if dl == "names":
            return {"name": name, "score": sc}
        if dl == "brief":
            return {"name": name, "desc": desc, "tier": tr, "score": sc}
        return {"name": name, "sig": sig, "desc": desc, "tier": tr,
                "namespace": ns, "score": sc}

    await _ensure_verb_embeddings()
    qvec = await _embed_one(query, prefix="search_query: ")
    hits: list[dict] = []
    cap = max(1, min(20, int(limit or 5)))

    pg_success = False
    scored = []
    if qvec:
        try:
            import mios_pg as _mios_pg
            rows = await _mios_pg.execute(
                "SELECT name, 1 - (emb <=> %(qvec)s::vector) AS score FROM verb "
                "WHERE hidden = false AND emb IS NOT NULL",
                {"qvec": qvec},
                fetch=True
            )
            if rows is not None:
                db_names = {r["name"] for r in rows if r.get("name")}
                scored = [(float(r["score"]), r["name"]) for r in rows if r.get("name")]
                for vname, vec in _VERB_EMBEDDINGS.items():
                    if vname not in db_names:
                        scored.append((_cosine(qvec, vec), vname))
                scored += [(_cosine(qvec, vec), k) for k, vec in _MCP_EMBEDDINGS.items()]
                scored.sort(reverse=True)
                pg_success = True
        except Exception as e_pg:
            log.debug("Database native vector search failed: %s", e_pg)

    if not pg_success:
        if qvec and (_VERB_EMBEDDINGS or _MCP_EMBEDDINGS):
            scored = [(_cosine(qvec, vec), n) for n, vec in _VERB_EMBEDDINGS.items()]
            scored += [(_cosine(qvec, vec), k) for k, vec in _MCP_EMBEDDINGS.items()]
            scored.sort(reverse=True)

    if scored:
        for score, name in scored:
            if not _passes(name):
                continue
            hits.append(_shape(name, score))
            if len(hits) >= cap:
                break
    else:
        q = query.lower()
        for vname, vcfg in _VERB_CATALOG.items():
            if vcfg.get("tier") == "rare" and tier_f != "rare":
                continue
            if not _passes(vname):
                continue
            if q in f"{vname} {vcfg.get('desc','')}".lower():
                hits.append(_shape(vname, 1.0))
            if len(hits) >= cap:
                break
    return JSONResponse({"hits": hits, "query": query, "embedded": bool(qvec),
                         "namespace": ns_f, "tier": tier_f, "detail_level": dl})

async def app_search_logic(query: str = "", limit: int = 5) -> JSONResponse:
    """Logic for GET /v1/app-search (server.py keeps the thin @app route)."""
    if not query.strip():
        return JSONResponse({"hits": [], "error": "empty query"})
    await _refresh_app_inventory()
    qvec = await _embed_one(query, prefix="search_query: ")
    hits: list[dict] = []
    if qvec and _APP_EMBEDDINGS:
        scored = [
            (_cosine(qvec, entry["vec"]), entry["record"])
            for entry in _APP_EMBEDDINGS.values()
        ]
        scored.sort(reverse=True, key=lambda x: x[0])
        for score, rec in scored[: max(1, min(20, int(limit or 5)))]:
            hits.append({**rec, "score": round(float(score), 4)})
    else:
        q = query.lower()
        for entry in _APP_EMBEDDINGS.values():
            rec = entry["record"]
            blob = f"{rec.get('name','')} {rec.get('description','')}".lower()
            if q in blob:
                hits.append({**rec, "score": 1.0})
            if len(hits) >= int(limit or 5):
                break
    return JSONResponse({
        "hits": hits, "query": query,
        "embedded": bool(qvec),
        "inventory_size": len(_APP_EMBEDDINGS),
    })

toolsearch_router = APIRouter()

@toolsearch_router.get("/v1/tool-search")
async def tool_search(query: str = "", limit: int = 5, namespace: str = "",
                      tier: str = "", detail_level: str = "full") -> JSONResponse:
    return await tool_search_logic(
        query=query, limit=limit, namespace=namespace, tier=tier,
        detail_level=detail_level)

@toolsearch_router.get("/v1/app-search")
async def app_search(query: str = "", limit: int = 5) -> JSONResponse:
    """Semantic search over the installed-app inventory. Returns top-k
    {category, name, description, launch, score}."""
    return await app_search_logic(query=query, limit=limit)
