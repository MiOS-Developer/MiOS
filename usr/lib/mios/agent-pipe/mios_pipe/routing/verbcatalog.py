# AI-hint: VERB/RECIPE CATALOG loader + 3-projection SSOT source, extracted verbatim from server.py (refactor R2 leaf wave).
# AI-doc: usr/share/doc/mios/manual/routing.md

from __future__ import annotations

import logging
import os
import re

from mios_grounding import _capability_grounding

log = logging.getLogger("mios-agent-pipe")

_VERB_CATALOG: dict = {}
_MODEL_NAME_TO_VERB: dict = {}
CATALOG_FAIL_MODE = "warn"

_INJECTED = frozenset((
    "_VERB_CATALOG", "_MODEL_NAME_TO_VERB", "CATALOG_FAIL_MODE",
))

def configure(**deps) -> None:
    """Inject the server-owned catalog globals + config under their EXACT original
    names (one-way boundary). Called from ``server.py``: once early to inject
    ``CATALOG_FAIL_MODE`` before the first catalog build, then again after
    ``_VERB_CATALOG`` / ``_MODEL_NAME_TO_VERB`` are built. Partial injection is fine
    -- only the keys present in ``deps`` (and in ``_INJECTED``) are set."""
    g = globals()
    for _k, _v in deps.items():
        if _k in _INJECTED:
            g[_k] = _v

def _normalize_verb_catalog_entry(entry: dict) -> dict:
    if not isinstance(entry, dict):
        return entry
    entry["permission"] = str(entry.get("permission", "read")).strip().lower()
    entry["tier"] = str(entry.get("tier", "common")).strip().lower()
    entry["hidden"] = bool(entry.get("hidden", False))
    params = entry.get("params") or {}
    if isinstance(params, dict):
        norm_params = {}
        for k, v in params.items():
            if isinstance(v, dict):
                p = v.copy()
                if "type" in p and isinstance(p["type"], str):
                    p["type"] = p["type"].strip().lower()
                norm_params[k] = p
            else:
                norm_params[k] = v
        entry["params"] = norm_params
    return entry

def _load_verb_catalog() -> dict:
    """Parse mios.toml [verbs.*] sections into the canonical verb
    catalog. Each entry: {section, sig, desc, tier, permission, params:
    {<arg>: {type, desc, aliases, enum, default}}}. SSOT for the planner
    prompt + the arg-synonym dispatcher + (future) MCP tools/list."""
    global _DB_UNREACHABLE
    cat: dict = {}
    try:
        import mios_toml
        merged = mios_toml.load_merged()
        verbs = mios_toml.section(merged, "verbs")
        defaults = verbs.get("_defaults") or {}
        if isinstance(verbs, dict):
            for vname, vcfg in verbs.items():
                if vname == "_defaults":
                    continue
                if not isinstance(vcfg, dict):
                    continue
                merged_vcfg = defaults.copy()
                merged_vcfg.update(vcfg)
                vcfg = merged_vcfg
                if "section" not in vcfg:
                    continue
                cat[vname] = _normalize_verb_catalog_entry({
                    "section":    str(vcfg.get("section", "Misc")),
                    "sig":        str(vcfg.get("sig", "")),
                    "desc":       str(vcfg.get("desc", "")),
                    "tier":       str(vcfg.get("tier", "common")),
                    "permission": str(vcfg.get("permission", "read")),
                    "params":     vcfg.get("params") or {},
                    "cmd":        str(vcfg.get("cmd", "") or ""),
                    "max_result_chars": int(vcfg.get("max_result_chars", 0) or 0),
                    "model_name": str(vcfg.get("model_name", "") or "").strip(),
                    "examples": [str(x).strip() for x in (vcfg.get("examples") or [])
                                 if str(x).strip()],
                    "hidden": bool(vcfg.get("hidden", False)),
                    "hidden_aliases": [str(x).strip()
                                       for x in (vcfg.get("hidden_aliases") or [])
                                       if str(x).strip()],
                    "parallel_limit": int(vcfg.get("parallel_limit", 0) or 0),
                    "conflict_group": str(vcfg.get("conflict_group", "") or "").strip(),
                })
    except Exception as e:
        log.warning("verb catalog load failed: %s", e)
        if CATALOG_FAIL_MODE == "fail":   # WS-A1 fail-loud (opt-in)
            raise

    db_auth = False
    try:
        import sys
        if "/usr/lib/mios" not in sys.path:
            sys.path.insert(0, "/usr/lib/mios")
        import mios_db_config
        db_auth = mios_db_config.is_db_authoritative()
    except Exception:
        pass

    import copy
    pure_toml_cat = copy.deepcopy(cat)

    if db_auth and not _DB_UNREACHABLE:
        try:
            import psycopg
            from psycopg.rows import dict_row
            from mios_pipe.memory.pg import pg_config
            cfg = pg_config()
            conn_str = (f"postgresql://{cfg['user']}:{cfg['password']}"
                        f"@{cfg['host']}:{cfg['port']}/{cfg['dbname']}")

            locale = os.environ.get("MIOS_LOCALE_LANGUAGE") or os.environ.get("LANG", "en")
            lang = locale.split("_")[0].split(".")[0].lower()

            with psycopg.connect(conn_str, connect_timeout=2) as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute("SELECT * FROM verb;")
                    rows = cur.fetchall()
                    for r in rows:
                        vname = r["name"]
                        if not r["is_active"]:
                            cat.pop(vname, None)
                            continue

                        sig = r["sig"]
                        desc = r["desc_default"]
                        tier = r["tier"]
                        permission = r["permission"]
                        cmd = r["cmd"] or ""
                        params = r["params"] or {}

                        i18n = r["i18n"] or {}
                        if lang in i18n:
                            lang_data = i18n[lang]
                            if "desc" in lang_data:
                                desc = lang_data["desc"]
                            if "params" in lang_data:
                                lang_params = lang_data["params"]
                                for arg, arg_cfg in params.items():
                                    if isinstance(arg_cfg, dict) and arg in lang_params:
                                        arg_cfg["desc"] = lang_params[arg]

                        if vname in cat:
                            cat[vname].update({
                                "sig": sig,
                                "desc": desc,
                                "tier": tier,
                                "permission": permission,
                                "cmd": cmd,
                                "params": params
                            })
                        else:
                            cat[vname] = {
                                "section": "Misc",
                                "sig": sig,
                                "desc": desc,
                                "tier": tier,
                                "permission": permission,
                                "params": params,
                                "cmd": cmd,
                                "max_result_chars": 0,
                                "model_name": "",
                                "examples": [],
                                "hidden": False,
                                "hidden_aliases": [],
                                "parallel_limit": 0,
                                "conflict_group": "",
                            }
        except Exception as db_err:
            _DB_UNREACHABLE = True
            log.debug("Database verb catalog overlay failed (using TOML baseline): %s", db_err)

    try:
        if db_auth:
            if not _DB_UNREACHABLE:
                db_cat = _load_verb_catalog_from_db()
                if db_cat:
                    return db_cat
            return cat
        else:
            import threading
            def _bg_shadow():
                try:
                    db_cat = _load_verb_catalog_from_db()
                    if db_cat:
                        divs = _compare_catalogs(pure_toml_cat, db_cat)
                        if divs:
                            import mios_db_config
                            mios_db_config.record_divergence(divs)
                            log.warning("Verb catalog divergence detected between TOML/Overlay and DB on keys: %s", divs)
                except Exception as bg_err:
                    log.debug("background shadow-compare failed: %s", bg_err)
            threading.Thread(target=_bg_shadow, daemon=True).start()
    except Exception as sentinel_err:
        log.debug("sentinel check or shadow-compare failed: %s", sentinel_err)

    return cat

_DB_UNREACHABLE = False

def _load_verb_catalog_from_db() -> dict:
    global _DB_UNREACHABLE
    if _DB_UNREACHABLE:
        return {}
    import json
    cat: dict = {}
    try:
        import psycopg
        from psycopg.rows import dict_row
        from mios_pipe.memory.pg import pg_config
        cfg = pg_config()
        conn_str = (f"postgresql://{cfg['user']}:{cfg['password']}"
                    f"@{cfg['host']}:{cfg['port']}/{cfg['dbname']}")

        locale = os.environ.get("MIOS_LOCALE_LANGUAGE") or os.environ.get("LANG", "en")
        lang = locale.split("_")[0].split(".")[0].lower()

        defaults = {}
        with psycopg.connect(conn_str, connect_timeout=2) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT value FROM config_kv
                    WHERE scope = 'verbs' AND key = '_defaults' AND layer = 0;
                    """
                )
                def_row = cur.fetchone()
                if def_row:
                    defaults = def_row[0]
                    if isinstance(defaults, str):
                        try:
                            defaults = json.loads(defaults)
                        except Exception:
                            defaults = {}

            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("SELECT * FROM verb ORDER BY name;")
                rows = cur.fetchall()
                for r in rows:
                    vname = r["name"]
                    if not r["is_active"]:
                        continue
                    if not r.get("section"):
                        continue

                    vcfg = defaults.copy()
                    sig = r.get("sig")
                    desc = r.get("desc_default")
                    tier = r.get("tier")
                    permission = r.get("permission")
                    cmd = r.get("cmd")
                    params = r.get("params")
                    section = r.get("section")
                    examples = r.get("examples")
                    model_name = r.get("model_name")
                    hidden = r.get("hidden")
                    aliases = r.get("aliases")
                    conflict_group = r.get("conflict_group")
                    parallel_limit = r.get("parallel_limit")
                    max_result_chars = r.get("max_result_chars")

                    i18n = r.get("i18n") or {}
                    if lang in i18n:
                        lang_data = i18n[lang]
                        if "desc" in lang_data:
                            desc = lang_data["desc"]
                        if "params" in lang_data:
                            lang_params = lang_data["params"]
                            for arg, arg_cfg in (params or {}).items():
                                if isinstance(arg_cfg, dict) and arg in lang_params:
                                    arg_cfg["desc"] = lang_params[arg]

                    vcfg.update({
                        "section":    str(section or "Misc"),
                        "sig":        str(sig or ""),
                        "desc":       str(desc or ""),
                        "tier":       str(tier or "common"),
                        "permission": str(permission or "read"),
                        "params":     params or {},
                        "cmd":        str(cmd or ""),
                        "max_result_chars": int(max_result_chars or 0),
                        "model_name": str(model_name or "").strip(),
                        "examples": [str(x).strip() for x in (examples or [])
                                     if str(x).strip()],
                        "hidden": bool(hidden),
                        "hidden_aliases": [str(x).strip()
                                           for x in (aliases or [])
                                           if str(x).strip()],
                        "parallel_limit": int(parallel_limit or 0),
                        "conflict_group": str(conflict_group or "").strip(),
                    })
                    cat[vname] = _normalize_verb_catalog_entry(vcfg)
    except Exception as e:
        _DB_UNREACHABLE = True
        log.debug("Failed to load verb catalog from DB: %s", e)
        return {}
    return cat

def _normalize_verb_catalog_entry(vcfg: dict) -> dict:
    import copy
    v = copy.deepcopy(vcfg)
    for key in ("sig", "desc", "cmd", "section", "model_name", "conflict_group"):
        val = v.get(key)
        if val == "" or val is None:
            v[key] = None
        else:
            v[key] = str(val).strip()
    for key in ("examples", "hidden_aliases", "aliases"):
        val = v.get(key)
        if not val:
            v[key] = None
        else:
            seen = set()
            cleaned = []
            for x in val:
                s = str(x).strip()
                if s and s not in seen:
                    seen.add(s)
                    cleaned.append(s)
            v[key] = sorted(cleaned) if cleaned else None
    if "aliases" in v:
        val = v.pop("aliases")
        if val:
            if v.get("hidden_aliases"):
                combined = set(v["hidden_aliases"]) | set(val)
                v["hidden_aliases"] = sorted(list(combined))
            else:
                v["hidden_aliases"] = val
    def to_bool(val):
        if isinstance(val, bool):
            return val
        if isinstance(val, (int, float)):
            return bool(val)
        if isinstance(val, str):
            return val.lower() in ("true", "1", "yes", "on")
        return bool(val)
    v["hidden"] = to_bool(v.get("hidden", False))
    if "sensitive" in v:
        v["sensitive"] = to_bool(v["sensitive"])
    v["parallel_limit"] = int(v.get("parallel_limit") or 0)
    v["max_result_chars"] = int(v.get("max_result_chars") or 0)
    params = v.get("params")
    if isinstance(params, dict) and params:
        normalized_params = {}
        for p_name, p_cfg in sorted(params.items()):
            if not isinstance(p_cfg, dict):
                normalized_params[p_name] = p_cfg
                continue
            p = copy.deepcopy(p_cfg)
            if "required" in p:
                p["required"] = to_bool(p["required"])
            for k in ("type", "desc"):
                if k in p:
                    p[k] = str(p[k]).strip() if p[k] is not None else None
            for k in ("aliases", "enum"):
                if k in p:
                    pval = p[k]
                    if not pval:
                        p[k] = None
                    else:
                        pseen = set()
                        pcleaned = []
                        for px in pval:
                            ps = str(px).strip()
                            if ps and ps not in pseen:
                                pseen.add(ps)
                                pcleaned.append(ps)
                        p[k] = sorted(pcleaned) if pcleaned else None
            if "default" in p:
                p_type = str(p.get("type") or "").lower()
                p_def = p["default"]
                if p_type == "boolean":
                    p["default"] = to_bool(p_def)
                elif p_type in ("integer", "number"):
                    try:
                        p["default"] = int(p_def)
                    except (ValueError, TypeError):
                        try:
                            p["default"] = float(p_def)
                        except (ValueError, TypeError):
                            p["default"] = p_def
                else:
                    if isinstance(p_def, str) and p_def.lower() in ("true", "false"):
                        p["default"] = (p_def.lower() == "true")
            normalized_params[p_name] = p
        v["params"] = normalized_params
    else:
        v["params"] = None
    return v

def _compare_catalogs(toml_cat: dict, db_cat: dict) -> set[str]:
    divs = set()
    for vname in set(toml_cat.keys()) | set(db_cat.keys()):
        if vname not in toml_cat:
            log.warning("Verb '%s' exists in DB but not in TOML", vname)
            divs.add(f"verbs.{vname}")
        elif vname not in db_cat:
            log.warning("Verb '%s' exists in TOML but not in DB", vname)
            divs.add(f"verbs.{vname}")
        else:
            toml_v = _normalize_verb_catalog_entry(toml_cat[vname])
            db_v = _normalize_verb_catalog_entry(db_cat[vname])
            if toml_v != db_v:
                log.warning("Verb '%s' differences: TOML=%s, DB=%s", vname, toml_v, db_v)
                divs.add(f"verbs.{vname}")
    return divs

def _verb_arg_synonyms_from_catalog(cat: dict) -> dict:
    """Project verb catalog's per-arg `aliases` lists into the legacy
    {verb: {arg: [alias,...]}} shape `_arg_with_synonyms` consumes.
    Single SSOT (catalog) -- no separate [verbs.<name>.synonyms] block."""
    syn: dict = {}
    for vname, vcfg in cat.items():
        params = vcfg.get("params") or {}
        if not isinstance(params, dict):
            continue
        for argname, argcfg in params.items():
            if not isinstance(argcfg, dict):
                continue
            aliases = argcfg.get("aliases") or []
            if aliases:
                syn.setdefault(vname, {})[str(argname)] = [str(a) for a in aliases]
    return syn

def _render_verb_catalog(cat: dict, include_rare: bool = True) -> str:
    """Render the verb catalog as the prose block the planner consumes.
    Sections grouped + ordered by first-seen order. Verbs tagged
    tier='rare' are HIDDEN by default -- they remain dispatchable for
    in-flight chains but don't burn planner tokens. Set include_rare=
    True for a full audit."""
    sections: dict[str, list[str]] = {}
    order: list[str] = []
    for vname, vcfg in cat.items():
        if not include_rare and vcfg.get("tier") == "rare":
            continue
        sec = vcfg.get("section", "Misc")
        if sec not in sections:
            sections[sec] = []
            order.append(sec)
        sig = vcfg.get("sig", "")
        desc = vcfg.get("desc", "")
        line = f"  {vname}({sig})".ljust(48) + f"-- {desc}"
        sections[sec].append(line)
    parts: list[str] = []
    for sec in order:
        parts.append(f"  -- {sec} --")
        parts.extend(sections[sec])
        parts.append("")
    return "\n".join(parts).rstrip()

def _identity_answer() -> str:
    cap = _capability_grounding(_VERB_CATALOG)
    if not cap:
        return ""
    body = cap.split(":\n", 1)[1].strip() if ":\n" in cap else ""
    intro = ("I'm **MiOS-Agent**, the local AI assistant built into MiOS. I'm not "
             "just a chatbot: I run on a local model and can act on this system "
             "through a live tool surface, and I answer from real tool results, the "
             "web, and facts you've asked me to remember -- not guesswork.")
    if not body:
        return intro
    return (intro + "\n\nHere's my actual capability surface on this system, by "
            "area:\n\n" + body + "\n\nJust ask in plain English and I'll pick the "
            "right tools to do it.")

def _load_verb_arg_synonyms() -> dict:
    """Compat shim -- existing callers still hit this name."""
    return _verb_arg_synonyms_from_catalog(_VERB_CATALOG)

def _build_model_name_map(cat: dict) -> dict:
    rev: dict = {}
    keys = set(cat.keys())
    collisions: list[str] = []
    for vname, vcfg in cat.items():
        mn = str((vcfg or {}).get("model_name", "") or "").strip()
        if not mn or mn == vname:
            continue
        if mn in keys:
            collisions.append(f"alias {mn!r} (verb {vname!r}) == a real verb key")
            log.error("ALIAS-COLLISION: model_name %r (verb %r) collides with a real "
                      "verb key -- alias ignored", mn, vname)
            continue
        if mn in rev:
            collisions.append(f"alias {mn!r} claimed by both {rev[mn]!r} and {vname!r}")
            log.error("ALIAS-COLLISION: model_name %r duplicated (%r vs %r) -- keeping "
                      "first; %r is DROPPED from the model-facing surface", mn, rev[mn],
                      vname, vname)
            continue
        rev[mn] = vname
    for vname, vcfg in cat.items():
        for al in ((vcfg or {}).get("hidden_aliases") or []):
            al = str(al).strip()
            if not al or al == vname:
                continue
            if al in keys:
                collisions.append(f"hidden_alias {al!r} (verb {vname!r}) == a real verb key")
                log.error("ALIAS-COLLISION: hidden_alias %r (verb %r) == a real verb "
                          "key -- ignored", al, vname)
                continue
            if al in rev and rev[al] != vname:
                collisions.append(
                    f"hidden_alias {al!r} claimed by both {rev[al]!r} and {vname!r}")
                log.error("ALIAS-COLLISION: hidden_alias %r duplicated (%r vs %r) -- "
                          "keeping first", al, rev[al], vname)
                continue
            rev[al] = vname
    if collisions:
        log.error("ALIAS-COLLISION: %d model_name collision(s) -- %s",
                  len(collisions), "; ".join(collisions))
        if str(os.environ.get("MIOS_STRICT_VERB_ALIASES", "")).strip().lower() \
                in {"1", "true", "yes"}:
            raise RuntimeError(
                f"verb model_name alias collisions ({len(collisions)}): "
                + "; ".join(collisions))
    return rev

def _resolve_verb_key(name: str) -> str:
    """Map a model-facing tool name (possibly a P1 model_name alias) back to its
    canonical verb key. Identity for names that are already keys or unknown. Cheap +
    idempotent -- safe to call on an already-resolved key."""
    if not name:
        return name
    if name in _VERB_CATALOG:
        return name
    return _MODEL_NAME_TO_VERB.get(name, name)

def _load_recipe_catalog() -> dict:
    out: dict = {}
    try:
        import mios_toml
        merged = mios_toml.load_merged()
        recs = mios_toml.section(merged, "recipes")
        for name, cfg in recs.items():
            if isinstance(cfg, dict):
                out[name] = {
                    "description": str(cfg.get("description", "")),
                    "args": list(cfg.get("args") or []),
                    "permission": str(cfg.get("permission", "read")),
                }
    except Exception as e:
        if log is not None:
            log.warning("recipe catalog load failed: %s", e)
        if CATALOG_FAIL_MODE == "fail":
            raise
    return out

def _render_recipe_catalog(rec: dict) -> str:
    if not rec:
        return ""
    lines = ["  -- OS recipes (run via os_recipe(name=..., params={...})) --"]
    for name, cfg in rec.items():
        args = ",".join(cfg.get("args") or [])
        perm = cfg.get("permission", "read")
        tag = "" if perm == "read" else f" [{perm}]"
        lines.append(f"  {name}({args})".ljust(34)
                     + f"-- {cfg.get('description', '')}{tag}")
    return "\n".join(lines)

def _recipe_to_openai_tool(name: str, cfg: dict) -> dict:
    args_raw = cfg.get("args") or []
    props: dict = {}
    if isinstance(args_raw, dict):
        for argname, argcfg in args_raw.items():
            if isinstance(argcfg, dict):
                spec = {
                    "type": [argcfg.get("type", "string"), "null"],
                    "description": argcfg.get("desc") or argcfg.get("description") or f"value for {argname}"
                }
                if "enum" in argcfg:
                    spec["enum"] = list(argcfg["enum"]) + [None]
                props[argname] = spec
            else:
                props[argname] = {
                    "type": ["string", "null"],
                    "description": f"value for {argname}",
                }
    else:
        for argname in args_raw:
            if not isinstance(argname, str) or not argname:
                continue
            props[argname] = {
                "type": ["string", "null"],
                "description": f"value for {argname}",
            }
    if "os" not in props:
        props["os"] = {
            "type": ["string", "null"],
            "description": "target OS selector (optional; defaults to host)",
        }
    return {
        "type": "function",
        "function": {
            "name": f"mios_recipe__{re.sub(r'[^A-Za-z0-9_]', '_', name)}",
            "description": cfg.get("description", "") or f"MiOS OS recipe {name}",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": props,
                "required": list(props.keys()),
                "additionalProperties": False,
            },
        },
        "x-mios-recipe": name,
        "x-mios-permission": cfg.get("permission", "read"),
    }

def _verb_to_openai_tool(vname: str, vcfg: dict) -> dict:
    props: dict = {}
    required: list[str] = []
    for argname, argcfg in (vcfg.get("params") or {}).items():
        if not isinstance(argcfg, dict):
            continue
        _t = argcfg.get("type", "string")
        spec: dict = {"description": argcfg.get("desc", "")}
        if argcfg.get("enum"):
            spec["enum"] = list(argcfg["enum"])
        if "default" in argcfg:
            spec["type"] = [_t, "null"]
            if "enum" in spec:
                spec["enum"] = spec["enum"] + [None]
        else:
            spec["type"] = _t
        required.append(argname)
        props[argname] = spec
    _facing = str(vcfg.get("model_name", "") or "").strip() or vname
    return {
        "type": "function",
        "function": {
            "name": _facing,
            "description": vcfg.get("desc", ""),
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": props,
                "required": required,
                "additionalProperties": False,
            },
        },
        "x-mios-verb": vname,
        "x-mios-permission": vcfg.get("permission", "read"),
        "x-mios-section": vcfg.get("section", ""),
    }
