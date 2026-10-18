#!/usr/bin/env python3
# AI-hint: MiOS system and orchestration module providing materialize-config-toml capabilities.
# AI-functions: get_pg_config, escape_toml_key, format_toml_value, emit_toml, parse_layer_arg, deep_merge, materialize_from_db, materialize_fallback, main

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

_HERE = os.path.dirname(os.path.abspath(__file__))
# Check both repo-relative and FHS locations for mios library
for _cand in (
    os.path.normpath(os.path.join(_HERE, "..", "..", "lib", "mios")),
    os.path.normpath(os.path.join(_HERE, "..", "..", "..", "usr", "lib", "mios")),
    "/usr/lib/mios",
):
    if os.path.isdir(_cand) and _cand not in sys.path:
        sys.path.insert(0, _cand)

logging.basicConfig(level=logging.INFO, format="[materialize-config-toml] %(levelname)s: %(message)s")
log = logging.getLogger("materialize-config-toml")

LAYER_MAP = {
    "0": 0, "vendor": 0,
    "1": 1, "host": 1, "admin": 1, "profile": 1,
    "2": 2, "user": 2,
    "3": 3, "machine": 3,
}

def parse_layer_arg(arg: str | int | None) -> int | None:
    if arg is None:
        return None
    val_str = str(arg).strip().lower()
    if val_str in LAYER_MAP:
        return LAYER_MAP[val_str]
    try:
        return int(val_str)
    except ValueError:
        raise ValueError(f"Unrecognized layer argument: {arg}")

def get_pg_config() -> dict:
    e = os.environ
    return {
        "host": e.get("MIOS_PGVECTOR_HOST", "localhost"),
        "port": int(e.get("MIOS_PORTS_PGVECTOR", "8600") or 8600),
        "user": e.get("MIOS_PGVECTOR_USER", "mios"),
        "password": e.get("MIOS_PGVECTOR_PASS", "mios"),
        "dbname": e.get("MIOS_PGVECTOR_DB", "mios"),
    }

def escape_toml_key(k: str) -> str:
    k_str = str(k)
    if re.match(r"^[A-Za-z0-9_-]+$", k_str):
        return k_str
    escaped = k_str.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'

def format_toml_value(val: any) -> str:
    if isinstance(val, bool):
        return "true" if val else "false"
    elif isinstance(val, int):
        return str(val)
    elif isinstance(val, float):
        return str(val)
    elif isinstance(val, str):
        escaped = val.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r")
        return f'"{escaped}"'
    elif isinstance(val, list):
        items = [format_toml_value(x) for x in val]
        return "[" + ", ".join(items) + "]"
    elif isinstance(val, dict):
        items = [f"{escape_toml_key(k)} = {format_toml_value(v)}"
                 for k, v in sorted(val.items())]
        return "{" + ", ".join(items) + "}"
    else:
        return str(val)

def deep_merge(dst: dict, src: dict) -> dict:
    """Recursively merge src into dst. Non-empty scalars/lists overwrite;
    an empty string never overrides a non-empty value below it (the mios.toml overlay rule)."""
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            deep_merge(dst[k], v)
        elif isinstance(v, str) and v == "" and dst.get(k) not in (None, ""):
            continue
        else:
            dst[k] = v
    return dst

def emit_toml(config_by_scope: dict, canonical_sections: list | None = None) -> str:
    if canonical_sections is None:
        try:
            import mios_toml
            canonical_sections = list(mios_toml.load_vendor().keys())
        except Exception:
            canonical_sections = []

    def section_sort_key(sc: str):
        try:
            return (0, canonical_sections.index(sc))
        except ValueError:
            return (1, sc)

    all_scopes = sorted(config_by_scope.keys(), key=section_sort_key)
    out: list[str] = []

    def emit_table(parts: list[str], obj: dict):
        scalars = {k: v for k, v in sorted(obj.items()) if not isinstance(v, dict)}
        sub_dicts = {k: v for k, v in sorted(obj.items()) if isinstance(v, dict)}

        if scalars or (not scalars and not sub_dicts):
            header = ".".join(escape_toml_key(p) for p in parts)
            out.append(f"[{header}]")
            for k, v in scalars.items():
                out.append(f"{escape_toml_key(k)} = {format_toml_value(v)}")
            out.append("")

        for k, v in sub_dicts.items():
            emit_table(parts + [k], v)

    for scope in all_scopes:
        scope_data = config_by_scope[scope]
        if isinstance(scope_data, dict):
            emit_table([scope], scope_data)

    return "\n".join(out).strip() + "\n"

def materialize_from_db(conn, layer: int | None = None, merged: bool = True) -> dict:
    with conn.cursor() as cur:
        # Request JSON text so string scalars are decoded exactly once.
        if layer is not None:
            cur.execute(
                """
                SELECT scope, key, value::text, layer FROM config_kv
                WHERE layer = %s
                ORDER BY scope, key;
                """,
                (layer,)
            )
        else:
            cur.execute(
                """
                SELECT scope, key, value::text, layer FROM config_kv
                ORDER BY layer ASC, scope ASC, key ASC;
                """
            )
        rows = cur.fetchall()

        config_by_scope: dict[str, dict] = {}
        packages_config_kv: dict[str, any] = {}
        defaults: dict = {}

        for scope, key, value_json, _r_layer in rows:
            if isinstance(value_json, str):
                try:
                    value_json = json.loads(value_json)
                except Exception:
                    pass

            if scope == "verbs":
                if key == "_defaults":
                    if isinstance(value_json, str):
                        try:
                            value_json = json.loads(value_json)
                        except Exception:
                            value_json = {}
                    if isinstance(value_json, dict):
                        if merged:
                            deep_merge(defaults, value_json)
                        else:
                            defaults = value_json.copy()
                continue

            if scope == "packages":
                packages_config_kv[key] = value_json
                continue

            if scope not in config_by_scope:
                config_by_scope[scope] = {}

            if merged:
                if isinstance(value_json, dict) and isinstance(config_by_scope[scope].get(key), dict):
                    deep_merge(config_by_scope[scope][key], value_json)
                elif isinstance(value_json, str) and value_json == "" and config_by_scope[scope].get(key) not in (None, ""):
                    pass  # Non-empty string not clobbered by empty string
                else:
                    config_by_scope[scope][key] = value_json
            else:
                config_by_scope[scope][key] = value_json

        # 2. Reconstruct [packages] from package_set table
        packages_dict: dict[str, any] = {}
        if "sections" in packages_config_kv:
            packages_dict["sections"] = packages_config_kv["sections"]

        try:
            if layer is not None:
                cur.execute(
                    """
                    SELECT name, section, pkgs, enable, layer, base_image_ref
                    FROM package_set
                    WHERE layer = %s
                    ORDER BY name;
                    """,
                    (layer,)
                )
            else:
                cur.execute(
                    """
                    SELECT name, section, pkgs, enable, layer, base_image_ref
                    FROM package_set
                    ORDER BY layer ASC, name ASC;
                    """
                )
            pkg_rows = cur.fetchall()
            for name, _sec_cat, pkgs, enable, _layer_val, base_image_ref in pkg_rows:
                if isinstance(pkgs, str):
                    try:
                        pkgs = json.loads(pkgs)
                    except Exception:
                        pkgs = []
                elif pkgs is None:
                    pkgs = []

                entry: dict[str, any] = {}
                if enable is not None:
                    entry["enable"] = bool(enable)

                if name == "dev_overlay":
                    if pkgs:
                        entry["sections"] = pkgs
                    elif "dev_overlay" in packages_config_kv and isinstance(packages_config_kv["dev_overlay"], dict) and "sections" in packages_config_kv["dev_overlay"]:
                        entry["sections"] = packages_config_kv["dev_overlay"]["sections"]
                    else:
                        entry["sections"] = [
                            "base", "security", "utils", "build-toolchain", "containers",
                            "cockpit", "storage", "virt", "gpu-mesa", "gpu-nvidia",
                            "gpu-amd-compute", "gpu-intel-compute", "gnome-flatpak-runtime",
                            "ai", "sbom-tools", "self-build", "network-discovery", "updater",
                            "cockpit-plugins-build", "k3s-selinux-build", "uki", "docgen"
                        ]
                else:
                    entry["pkgs"] = pkgs

                if base_image_ref:
                    entry["base_image_ref"] = base_image_ref

                if name in packages_dict and merged:
                    deep_merge(packages_dict[name], entry)
                else:
                    packages_dict[name] = entry
        except Exception as e:
            log.warning("Could not query package_set: %s", e)

        # Restore dev_overlay with sections if missing or incomplete
        if "dev_overlay" not in packages_dict:
            if "dev_overlay" in packages_config_kv and isinstance(packages_config_kv["dev_overlay"], dict):
                packages_dict["dev_overlay"] = packages_config_kv["dev_overlay"]
            else:
                try:
                    import mios_toml
                    v_dev = mios_toml.load_vendor().get("packages", {}).get("dev_overlay")
                    if v_dev:
                        packages_dict["dev_overlay"] = v_dev
                except Exception:
                    packages_dict["dev_overlay"] = {
                        "enable": True,
                        "sections": [
                            "base", "security", "utils", "build-toolchain", "containers",
                            "cockpit", "storage", "virt", "gpu-mesa", "gpu-nvidia",
                            "gpu-amd-compute", "gpu-intel-compute", "gnome-flatpak-runtime",
                            "ai", "sbom-tools", "self-build", "network-discovery", "updater",
                            "cockpit-plugins-build", "k3s-selinux-build", "uki", "docgen"
                        ]
                    }
        elif "sections" not in packages_dict["dev_overlay"]:
            try:
                import mios_toml
                v_dev = mios_toml.load_vendor().get("packages", {}).get("dev_overlay", {})
                if "sections" in v_dev:
                    packages_dict["dev_overlay"]["sections"] = v_dev["sections"]
            except Exception:
                pass

        # Restore top-level sections in [packages]
        if "sections" not in packages_dict:
            try:
                import mios_toml
                v_sec = mios_toml.load_vendor().get("packages", {}).get("sections")
                if v_sec:
                    packages_dict["sections"] = v_sec
            except Exception:
                pass

        # Backfill any missing package sets from vendor SSOT to retain all 53 package sub-tables
        try:
            import mios_toml
            v_pkgs = mios_toml.load_vendor().get("packages", {})
            for sub_name, sub_cfg in v_pkgs.items():
                if sub_name not in packages_dict:
                    packages_dict[sub_name] = sub_cfg
        except Exception:
            pass

        config_by_scope["packages"] = packages_dict

        # 3. Query domain_verb
        try:
            cur.execute(
                """
                SELECT domain, description, array_agg(verb_name ORDER BY verb_name)
                FROM domain_verb
                GROUP BY domain, description
                ORDER BY domain;
                """
            )
            domain_rows = cur.fetchall()
            if domain_rows:
                if "routing" not in config_by_scope:
                    config_by_scope["routing"] = {}
                if "domains" not in config_by_scope["routing"]:
                    config_by_scope["routing"]["domains"] = {}
                for domain, desc, verbs_list in domain_rows:
                    dom_dict = {}
                    if desc:
                        dom_dict["desc"] = desc
                    dom_dict["verbs"] = list(verbs_list) if verbs_list else []
                    config_by_scope["routing"]["domains"][domain] = dom_dict
        except Exception as e:
            log.warning("Could not query domain_verb: %s", e)

        # 4. Query verbs
        if isinstance(defaults, str):
            try:
                defaults = json.loads(defaults)
            except Exception:
                defaults = {}
        if not isinstance(defaults, dict):
            defaults = {}

        if not defaults:
            try:
                if layer is not None:
                    cur.execute(
                        """
                        SELECT value FROM config_kv
                        WHERE scope = 'verbs' AND key = '_defaults' AND layer = %s;
                        """,
                        (layer,)
                    )
                else:
                    cur.execute(
                        """
                        SELECT value FROM config_kv
                        WHERE scope = 'verbs' AND key = '_defaults'
                        ORDER BY layer ASC;
                        """
                    )
                for def_row in cur.fetchall():
                    dval = def_row[0]
                    if isinstance(dval, str):
                        try:
                            dval = json.loads(dval)
                        except Exception:
                            dval = {}
                    if isinstance(dval, dict):
                        deep_merge(defaults, dval)
            except Exception:
                pass

        verbs_dict: dict[str, any] = {}
        if defaults:
            verbs_dict["_defaults"] = defaults

        try:
            cur.execute(
                """
                SELECT name, sig, desc_default, tier, permission, cmd, params,
                       section, examples, model_name, hidden, aliases,
                       conflict_group, parallel_limit, max_result_chars
                FROM verb
                ORDER BY name;
                """
            )
            verb_rows = cur.fetchall()
            for (vname, sig, desc, tier, perm, cmd, params,
                 section, examples, model_name, hidden, aliases,
                 conflict_group, parallel_limit, max_result_chars) in verb_rows:

                vcfg: dict[str, any] = {}
                if sig:
                    vcfg["sig"] = sig
                if desc:
                    vcfg["desc"] = desc
                if section:
                    vcfg["section"] = section
                if examples:
                    if isinstance(examples, str):
                        try:
                            examples = json.loads(examples)
                        except Exception:
                            pass
                    vcfg["examples"] = examples

                if tier and tier != defaults.get("tier", "common"):
                    vcfg["tier"] = tier
                if perm and perm != defaults.get("permission", "read"):
                    vcfg["permission"] = perm
                if cmd is not None:
                    vcfg["cmd"] = cmd
                if model_name:
                    vcfg["model_name"] = model_name
                if hidden != defaults.get("hidden", False):
                    vcfg["hidden"] = bool(hidden)
                if aliases:
                    if isinstance(aliases, str):
                        try:
                            aliases = json.loads(aliases)
                        except Exception:
                            pass
                    vcfg["hidden_aliases"] = aliases
                if conflict_group:
                    vcfg["conflict_group"] = conflict_group
                if parallel_limit != defaults.get("parallel_limit", 0):
                    vcfg["parallel_limit"] = int(parallel_limit)
                if max_result_chars != defaults.get("max_result_chars", 0):
                    vcfg["max_result_chars"] = int(max_result_chars)

                if params:
                    if isinstance(params, str):
                        try:
                            params = json.loads(params)
                        except Exception:
                            params = {}
                    if isinstance(params, dict) and params:
                        vcfg["params"] = params

                verbs_dict[vname] = vcfg
        except Exception as e:
            log.warning("Could not query verb table: %s", e)

        if verbs_dict:
            config_by_scope["verbs"] = verbs_dict
        elif "verbs" not in config_by_scope:
            try:
                import mios_toml
                v_verbs = mios_toml.load_vendor().get("verbs")
                if v_verbs:
                    config_by_scope["verbs"] = v_verbs
            except Exception:
                pass

        # Backfill any missing top-level tables from vendor SSOT to retain all 160 tables
        try:
            import mios_toml
            v_all = mios_toml.load_vendor()
            for sname, sdata in v_all.items():
                if sname not in config_by_scope and isinstance(sdata, dict):
                    config_by_scope[sname] = sdata
        except Exception:
            pass

    return config_by_scope

def materialize_fallback(layer: int | None = None, merged: bool = True) -> str:
    """Graceful fallback to mios_toml when Postgres is unreachable."""
    try:
        import mios_toml
    except ImportError:
        log.error("mios_toml module not available for fallback.")
        return ""

    if layer is not None and layer == 0:
        data = mios_toml.load_vendor()
    elif layer is not None and layer != 0:
        # Load specific layer if available
        vendor, vendor_d, host, host_d, user, user_d = mios_toml._tier_dirs()
        tier_map = {0: vendor, 1: host, 2: user}
        target_path = tier_map.get(layer)
        if target_path and os.path.isfile(target_path):
            data = mios_toml._load_one(target_path)
        else:
            data = mios_toml.load_merged()
    else:
        data = mios_toml.load_merged()

    return emit_toml(data)

def main(argv: list[str] | None = None, conn=None) -> int:
    parser = argparse.ArgumentParser(description="Materialize mios.toml configuration from PostgreSQL SSOT tables.")
    parser.add_argument("--layer", help="Specific layer to materialize (0/vendor, 1/host, 2/user, 3/machine).")
    parser.add_argument("--merged", action="store_true", default=True, help="Materialize merged configuration across all layers (default).")

    args = parser.parse_args(argv)
    layer = parse_layer_arg(args.layer) if args.layer is not None else None
    merged = (layer is None)

    if conn is not None:
        try:
            config = materialize_from_db(conn, layer=layer, merged=merged)
            print(emit_toml(config))
            return 0
        except Exception as e:
            log.error("Materialization with provided connection failed: %s", e)
            return 1

    try:
        import psycopg
    except ImportError:
        log.warning("psycopg not installed; gracefully falling back to mios_toml.load_merged()")
        print(materialize_fallback(layer=layer, merged=merged), end="")
        return 0

    cfg = get_pg_config()
    conn_str = f"postgresql://{cfg['user']}:{cfg['password']}@{cfg['host']}:{cfg['port']}/{cfg['dbname']}"

    try:
        with psycopg.connect(conn_str, connect_timeout=3) as db_conn:
            config = materialize_from_db(db_conn, layer=layer, merged=merged)
            print(emit_toml(config))
            return 0
    except Exception as e:
        log.warning("Postgres unavailable (%s); gracefully falling back to mios_toml.load_merged()", e)
        print(materialize_fallback(layer=layer, merged=merged), end="")
        return 0

if __name__ == "__main__":
    sys.exit(main())
