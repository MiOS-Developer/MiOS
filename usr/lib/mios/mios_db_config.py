# AI-hint: Peer of mios_toml.py resolving configuration settings from PostgreSQL config tables (WS-VECTOR V1 / task_222).
from __future__ import annotations

import os
import json
import logging
import mios_toml
from typing import Any

log = logging.getLogger("mios-db-config")

_DIVERGENT_KEYS = set()

def get_divergences() -> int:
    return len(_DIVERGENT_KEYS)

def reset_divergences() -> None:
    _DIVERGENT_KEYS.clear()

def record_divergence(key: str | set[str] | list[str]) -> None:
    if isinstance(key, (set, list)):
        _DIVERGENT_KEYS.update(key)
    else:
        _DIVERGENT_KEYS.add(key)

def get_pg_config() -> dict:
    e = os.environ
    try:
        port = int(e.get("MIOS_PORTS_PGVECTOR") or 8600)
    except (TypeError, ValueError):
        port = 8600
    return {
        "host": e.get("MIOS_PGVECTOR_HOST", "localhost"),
        "port": port,
        "user": e.get("MIOS_PGVECTOR_USER", "mios"),
        "password": e.get("MIOS_PGVECTOR_PASS", "mios"),
        "dbname": e.get("MIOS_PGVECTOR_DB", "mios"),
    }

_IS_DB_AUTHORITATIVE_CACHE = None
_LOAD_DB_CONFIG_CACHE = None

def clear_cache():
    global _IS_DB_AUTHORITATIVE_CACHE, _LOAD_DB_CONFIG_CACHE
    _IS_DB_AUTHORITATIVE_CACHE = None
    _LOAD_DB_CONFIG_CACHE = None

def is_db_authoritative() -> bool:
    global _IS_DB_AUTHORITATIVE_CACHE
    if _IS_DB_AUTHORITATIVE_CACHE is not None:
        return _IS_DB_AUTHORITATIVE_CACHE
    val = os.environ.get("MIOS_DB_AUTHORITATIVE")
    if val is not None:
        res = val.lower() in ("true", "1", "yes")
    else:
        file_data = mios_toml.load_merged(layers=mios_toml.layer_paths())
        res = bool(mios_toml.get("ai", "db_authoritative", default=False, data=file_data))
    _IS_DB_AUTHORITATIVE_CACHE = res
    return res

def load_db_config() -> dict:
    global _LOAD_DB_CONFIG_CACHE
    if _LOAD_DB_CONFIG_CACHE is not None:
        return _LOAD_DB_CONFIG_CACHE
    try:
        import psycopg
    except ImportError:
        return {}

    cfg = get_pg_config()
    conn_str = f"postgresql://{cfg['user']}:{cfg['password']}@{cfg['host']}:{cfg['port']}/{cfg['dbname']}"

    data = {}
    try:
        with psycopg.connect(conn_str, connect_timeout=2) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT scope, key, value, layer FROM config_kv
                    ORDER BY layer ASC, scope ASC, key ASC
                    """
                )
                rows = cur.fetchall()
                for scope, key, value, layer in rows:
                    if scope not in data:
                        data[scope] = {}

                    if isinstance(value, dict) and isinstance(data[scope].get(key), dict):
                        mios_toml.deep_merge(data[scope][key], value)
                    elif isinstance(value, str) and value == "" and data[scope].get(key) not in (None, ""):
                        continue
                    else:
                        data[scope][key] = value

                cur.execute(
                    """
                    SELECT domain, description, array_agg(verb_name ORDER BY verb_name)
                    FROM domain_verb
                    GROUP BY domain, description
                    ORDER BY domain
                    """
                )
                domain_rows = cur.fetchall()
                if domain_rows:
                    if "routing" not in data:
                        data["routing"] = {}
                    if "domains" not in data["routing"]:
                        data["routing"]["domains"] = {}

                    for domain, desc, verbs_list in domain_rows:
                        data["routing"]["domains"][domain] = {
                            "desc": desc or "",
                            "verbs": list(verbs_list)
                        }

                defaults = {}
                if "verbs" in data and "_defaults" in data["verbs"]:
                    defaults = data["verbs"]["_defaults"]
                else:
                    cur.execute(
                        """
                        SELECT value FROM config_kv
                        WHERE scope = 'verbs' AND key = '_defaults' AND layer = 0
                        """
                    )
                    def_row = cur.fetchone()
                    if def_row:
                        defaults = def_row[0]
                        if "verbs" not in data:
                            data["verbs"] = {}
                        data["verbs"]["_defaults"] = defaults

                cur.execute(
                    """
                    SELECT name, sig, desc_default, tier, permission, cmd, params,
                           section, examples, model_name, hidden, aliases,
                           conflict_group, parallel_limit, max_result_chars
                    FROM verb
                    ORDER BY name
                    """
                )
                verb_rows = cur.fetchall()
                if verb_rows:
                    if "verbs" not in data:
                        data["verbs"] = {}
                    for (vname, sig, desc, tier, perm, cmd, params,
                         section, examples, model_name, hidden, aliases,
                         conflict_group, parallel_limit, max_result_chars) in verb_rows:

                        vcfg = defaults.copy()
                        vcfg["sig"] = sig or ""
                        vcfg["desc"] = desc or ""
                        if section:
                            vcfg["section"] = section
                        if examples:
                            vcfg["examples"] = examples
                        vcfg["tier"] = tier
                        vcfg["permission"] = perm
                        if cmd is not None:
                            vcfg["cmd"] = cmd
                        if model_name:
                            vcfg["model_name"] = model_name
                        vcfg["hidden"] = bool(hidden)
                        if aliases:
                            vcfg["aliases"] = aliases
                        if conflict_group:
                            vcfg["conflict_group"] = conflict_group
                        vcfg["parallel_limit"] = parallel_limit
                        vcfg["max_result_chars"] = max_result_chars
                        if params:
                            vcfg["params"] = params

                        data["verbs"][vname] = vcfg
    except Exception as e:
        log.debug("Failed to load configuration from database: %s", e)
        return {}
    _LOAD_DB_CONFIG_CACHE = data
    return data

def _normalize_for_compare(val):
    if isinstance(val, dict):
        return {k: _normalize_for_compare(v) for k, v in val.items()}
    elif isinstance(val, list):
        if all(isinstance(x, (str, int, float, bool, type(None))) for x in val):
            try:
                return sorted(val, key=lambda x: str(x))
            except Exception:
                pass
        return [_normalize_for_compare(x) for x in val]
    return val

def _is_equal(a, b) -> bool:
    try:
        norm_a = _normalize_for_compare(a)
        norm_b = _normalize_for_compare(b)
        return json.dumps(norm_a, sort_keys=True) == json.dumps(norm_b, sort_keys=True)
    except Exception:
        return a == b

def _find_divergent_keys(a: dict, b: dict, prefix="") -> set[str]:
    divs = set()
    for k in b:
        if k == "verbs":
            continue
        p = f"{prefix}.{k}" if prefix else k
        if k not in a:
            divs.add(p)
            continue
        val_a = a[k]
        val_b = b[k]
        if isinstance(val_a, dict) and isinstance(val_b, dict):
            divs.update(_find_divergent_keys(val_a, val_b, p))
        elif not _is_equal(val_a, val_b):
            divs.add(p)
    return divs

def load_merged(layers=None) -> dict:
    if layers is not None:
        return mios_toml.load_merged(layers)

    if is_db_authoritative():
        db_cfg = load_db_config()
        if db_cfg:
            return db_cfg
        return mios_toml.load_merged()
    else:
        toml_val = mios_toml.load_merged()
        db_val = load_db_config()
        if db_val:
            divs = _find_divergent_keys(toml_val, db_val)
            if divs:
                global _DIVERGENT_KEYS
                _DIVERGENT_KEYS.update(divs)
                filtered_toml = {k: toml_val[k] for k in db_val if k in toml_val and k != "verbs"}
                filtered_db = {k: db_val[k] for k in db_val if k != "verbs"}
                log.warning("Config divergence in load_merged on keys %s: TOML=%s, DB=%s", divs, filtered_toml, filtered_db)
        return toml_val

def load_vendor() -> dict:
    return mios_toml.load_vendor()

def section(data, name) -> dict:
    if data is not None:
        return mios_toml.section(data, name)

    if is_db_authoritative():
        db_cfg = load_db_config()
        merged = db_cfg if db_cfg else mios_toml.load_merged()
        return mios_toml.section(merged, name)
    else:
        toml_val = mios_toml.section(mios_toml.load_merged(), name)
        db_val = mios_toml.section(load_db_config(), name)
        if db_val and not _is_equal(toml_val, db_val):
            divs = _find_divergent_keys(toml_val, db_val, prefix=name)
            if divs:
                record_divergence(divs)
            else:
                record_divergence(name)
            log.warning("Config divergence in section '%s': TOML=%s, DB=%s", name, toml_val, db_val)
        return toml_val

def get(sect, key, default=None, data=None) -> Any:
    if data is not None:
        return mios_toml.section(data, sect).get(key, default)

    if is_db_authoritative():
        db_cfg = load_db_config()
        if db_cfg:
            sect_dict = mios_toml.section(db_cfg, sect)
            if key in sect_dict:
                return sect_dict[key]
        return mios_toml.get(sect, key, default)
    else:
        toml_val = mios_toml.get(sect, key, default)
        db_cfg = load_db_config()
        if db_cfg:
            sect_dict = mios_toml.section(db_cfg, sect)
            if key in sect_dict:
                db_val = sect_dict[key]
                if not _is_equal(toml_val, db_val):
                    record_divergence(f"{sect}.{key}")
                    log.warning("Config divergence in key '%s.%s': TOML=%s, DB=%s", sect, key, toml_val, db_val)
        return toml_val

def colors(data=None) -> dict:
    if data is not None:
        return mios_toml.colors(data)

    if is_db_authoritative():
        db_cfg = load_db_config()
        merged = db_cfg if db_cfg else mios_toml.load_merged()
        return mios_toml.colors(merged)
    else:
        toml_val = mios_toml.colors()
        db_cfg = load_db_config()
        if db_cfg and "colors" in db_cfg:
            db_val = mios_toml.colors(db_cfg)
            if not _is_equal(toml_val, db_val):
                divs = _find_divergent_keys(toml_val, db_val, prefix="colors")
                if divs:
                    record_divergence(divs)
                else:
                    record_divergence("colors")
                log.warning("Config divergence in colors: TOML=%s, DB=%s", toml_val, db_val)
        return toml_val
