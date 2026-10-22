#!/usr/bin/env python3
# AI-hint: MiOS system and orchestration module providing materialize-build-ctx capabilities.
# AI-functions: get_pg_config, main

import os
import json
import logging
import sys

logging.basicConfig(level=logging.INFO, format="[materialize-build-ctx] %(levelname)s: %(message)s")
log = logging.getLogger("materialize-build-ctx")

def get_pg_config():
    e = os.environ
    return {
        "host": e.get("MIOS_PGVECTOR_HOST", "localhost"),
        "port": int(e.get("MIOS_PORTS_PGVECTOR", "8600") or 8600),
        "user": e.get("MIOS_PGVECTOR_USER", "mios"),
        "password": e.get("MIOS_PGVECTOR_PASS", "mios"),
        "dbname": e.get("MIOS_PGVECTOR_DB", "mios"),
    }

def main():
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError:
        log.error("psycopg not installed. Skipping materialization.")
        return 2

    ctx_dir = os.environ.get("MIOS_BUILD_CTX", "/ctx")
    try:
        os.makedirs(ctx_dir, exist_ok=True)
    except Exception as e:
        log.error("Failed to create context directory %s: %s", ctx_dir, e)
        return 1

    cfg = get_pg_config()
    conn_str = (f"postgresql://{cfg['user']}:{cfg['password']}"
                f"@{cfg['host']}:{cfg['port']}/{cfg['dbname']}")

    try:
        with psycopg.connect(conn_str, connect_timeout=5, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                log.info("Connected to database. Starting materialization of build context to %s...", ctx_dir)

                cur.execute("SELECT name, section, pkgs, enable, layer, base_image_ref FROM package_set ORDER BY name;")
                package_sets = cur.fetchall()
                package_sets_out = []
                for p in package_sets:
                    p_dict = dict(p)
                    if isinstance(p_dict.get("pkgs"), str):
                        p_dict["pkgs"] = json.loads(p_dict["pkgs"])
                    package_sets_out.append(p_dict)
                with open(os.path.join(ctx_dir, "package_sets.json"), "w", encoding="utf-8") as fh:
                    json.dump(package_sets_out, fh, indent=2)
                log.info("Materialized package_sets.json (%d sets)", len(package_sets_out))

                cur.execute("SELECT ordinal, script, stage, deps FROM build_phase ORDER BY stage, ordinal NULLS LAST, script;")
                phases = cur.fetchall()
                phases_out = []
                for p in phases:
                    p_dict = dict(p)
                    if isinstance(p_dict.get("deps"), str):
                        p_dict["deps"] = json.loads(p_dict["deps"])
                    phases_out.append(p_dict)
                with open(os.path.join(ctx_dir, "build_phases.json"), "w", encoding="utf-8") as fh:
                    json.dump(phases_out, fh, indent=2)
                log.info("Materialized build_phases.json (%d phases)", len(phases_out))

                toml_path = os.environ.get("TOML_PATH") or os.environ.get("MIOS_TOML")
                if not toml_path:
                    for candidate in ["/etc/mios/mios.toml", "/usr/share/mios/mios.toml"]:
                        if os.path.isfile(candidate):
                            toml_path = candidate
                            break
                if toml_path:
                    toml_dir = os.path.dirname(os.path.abspath(toml_path))
                    if toml_dir and toml_dir != "/ctx" and os.path.abspath(toml_dir) != os.path.abspath(ctx_dir):
                        try:
                            os.makedirs(toml_dir, exist_ok=True)
                            with open(os.path.join(toml_dir, "build_phases.json"), "w", encoding="utf-8") as fh:
                                json.dump(phases_out, fh, indent=2)
                            log.info("Also materialized build_phases.json to %s", toml_dir)
                        except Exception as ex:
                            log.warning("Could not write build_phases.json to %s: %s", toml_dir, ex)

                cur.execute("SELECT name, policy_type, rules FROM debloat_policy ORDER BY name;")
                policies = cur.fetchall()
                policies_out = []
                for p in policies:
                    p_dict = dict(p)
                    if isinstance(p_dict.get("rules"), str):
                        p_dict["rules"] = json.loads(p_dict["rules"])
                    policies_out.append(p_dict)

                cur.execute("SELECT name, description FROM debloat_profile ORDER BY name;")
                profiles = [dict(r) for r in cur.fetchall()]

                cur.execute("SELECT name, description, features, debloat_profile_name FROM preset ORDER BY name;")
                presets = cur.fetchall()
                presets_out = []
                for p in presets:
                    p_dict = dict(p)
                    if isinstance(p_dict.get("features"), str):
                        p_dict["features"] = json.loads(p_dict["features"])
                    presets_out.append(p_dict)

                xbox_ctx = {
                    "policies": policies_out,
                    "profiles": profiles,
                    "presets": presets_out
                }
                with open(os.path.join(ctx_dir, "debloat_profiles.json"), "w", encoding="utf-8") as fh:
                    json.dump(xbox_ctx, fh, indent=2)
                log.info("Materialized debloat_profiles.json")

    except Exception as e:
        log.error("Database materialization failed: %s", e)
        return 1

    return 0

if __name__ == "__main__":
    sys.exit(main())
