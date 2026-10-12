#!/usr/bin/env python3
# AI-hint: MiOS system and orchestration module providing seed-db-config capabilities.
# AI-related: /usr/share/mios/mios.toml, automation/, mios-find, mios-bootstrap, https://github.com/mios-dev/mios-bootstrap/blob/main/field/autounattend/mios-debloat.json, https://github.com/mios-dev/mios-bootstrap/blob/main/field/autounattend/mios-xbox-features.txt
# AI-functions: get_seeded_sections, get_pg_config, main

import os
import json
import logging
import sys

logging.basicConfig(level=logging.INFO, format="[seed-db-config] %(levelname)s: %(message)s")
log = logging.getLogger("seed-db-config")

_CANONICAL_SECTIONS = {
    'migration', 'artifacts', 'security', 'firewall', 'ssot_consumers', 'ssot_tables', 'unit_projection', 'units', 'identity', 'user', 'accounts', 'mios', 'agent_passport', 'repos', 'ai_tag', 'selfimprove', 'portal', 'locale', 'auth', 'network', 'management_mesh', 'power', 'metal', 'meta', 'bootc_install', 'psi', 'install_phases', 'messages', 'search', 'hermes', 'gpu', 'sandbox', 'laws', 'uki', 'fs_watcher', 'pkg', 'ttyd', 'agents', 'gateway', 'blades', 'nodes', 'lanes', 'cost', 'audit', 'worker_tools', 'slo', 'sched', 'scheduler', 'admission', 'reflect', 'drift_monitor', 'schema', 'refactor', 'shell_session', 'powershell', 'consensus', 'drift', 'dispatch', 'council', 'web_research', 'knowledge', 'hitl', 'memory', 'run_template', 'aci', 'dci', 'verity', 'code_mode', 'refine', 'planner', 'polish', 'passport', 'daemon', 'budget', 'os_control', 'computer_use', 'observability', 'gossip', 'a2a', 'mcp', 'routing', 'browser', 'owui', 'recipes', 'skills', 'reliability', 'frontier', 'agent_pipe', 'pgvector', 'lsfs', 'offline', 'llamacpp', 'enhanced_session', 'flatpak', 'appearance', 'graphics', 'ai', 'desktop', 'image', 'deployment', 'hwcaps', 'browser_ai', 'virt', 'ports', 'urls', 'generator', 'versions', 'services', 'pods', 'adguard', 'paths', 'mios-find', 'compliance', 'build', 'deploy', 'vm', 'cockpit', 'greenboot', 'colors', 'bootstrap', 'wsl2', 'terminal', 'theme', 'branding', 'btop', 'dashboard', 'apps', 'windows', 'preflight', 'quadlets', 'env', 'finetune', 'storage', 'converge', 'networks', 'images', 'containers', 'editions', 'blade', 'field', 'dotfiles', 'shell', 'ssh', 'gitconfig', 'templates', 'kargs', 'testing', 'pipeline', 'resolver', 'tasks', 'docs', 'legibility', 'ci', 'tests', 'variants', 'profiles', 'rust', 'database', 'logging', 'orchestration',
    'agent_cli', 'keybindings', 'headscale', 'tailscale', 'aliases', 'workspace', 'generation'
}

def get_seeded_sections(data: dict) -> list:
    """Returns top-level SSOT section keys that seed-db-config seeds into config_kv."""
    return [k for k in data.keys() if k in _CANONICAL_SECTIONS]

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
    except ImportError:
        log.error("DEGRADED: psycopg not installed; database seeding skipped (dependency unavailable)")
        return 2

    toml_path = os.environ.get("MIOS_TOML", "/usr/share/mios/mios.toml")
    if not os.path.isfile(toml_path):
        log.error("mios.toml not found at %s", toml_path)
        return 1

    try:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib
        with open(toml_path, "rb") as fh:
            data = tomllib.load(fh)
    except Exception as e:
        log.error("Failed to parse mios.toml: %s", e)
        return 1

    cfg = get_pg_config()
    conn_str = (f"postgresql://{cfg['user']}:{cfg['password']}"
                f"@{cfg['host']}:{cfg['port']}/{cfg['dbname']}")

    try:
        with psycopg.connect(conn_str, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                log.info("Connected to database. Starting seeding...")

                sys_sections = ["ports", "ai", "routing", "pgvector", "a2a", "mcp", "observability", "sandbox", "security", "agent_passport", "agent_pipe"]
                for sec in sys_sections:
                    sec_data = data.get(sec) or {}
                    if not isinstance(sec_data, dict):
                        continue
                    for k, val in sec_data.items():
                        if sec == "routing" and k in ("domains", "nohc_allowlist"):
                            continue
                        db_key = f"{sec}.{k}"
                        cur.execute(
                            """
                            INSERT INTO system_config (key, value, description)
                            VALUES (%s, %s, %s)
                            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;
                            """,
                            (db_key, json.dumps(val), f"Default configuration for {db_key}")
                        )
                log.info("System configuration seeded.")

                verbs = data.get("verbs") or {}
                defaults = verbs.get("_defaults") or {}
                cur.execute(
                    """
                    INSERT INTO config_kv (scope, key, value, layer, description)
                    VALUES ('verbs', '_defaults', %s, 0, 'Verbs defaults')
                    ON CONFLICT (scope, key, layer) DO UPDATE SET value = EXCLUDED.value;
                    """,
                    (json.dumps(defaults),)
                )

                kv_sections = get_seeded_sections(data)
                for sec in kv_sections:
                    sec_data = data.get(sec) or {}
                    if not isinstance(sec_data, dict):
                        continue
                    for k, val in sec_data.items():
                        if sec == "routing" and k == "domains":
                            continue
                        cur.execute(
                            """
                            INSERT INTO config_kv (scope, key, value, layer, description)
                            VALUES (%s, %s, %s, 0, %s)
                            ON CONFLICT (scope, key, layer) DO UPDATE SET value = EXCLUDED.value;
                            """,
                            (sec, k, json.dumps(val), f"Vendor default for {sec}.{k}")
                        )
                log.info("config_kv seeded.")

                verbs = data.get("verbs") or {}
                defaults = verbs.get("_defaults") or {}
                if isinstance(verbs, dict):
                    for vname, vcfg in verbs.items():
                        if vname == "_defaults" or not isinstance(vcfg, dict):
                            continue
                        merged = defaults.copy()
                        merged.update(vcfg)

                        sig = str(merged.get("sig", ""))
                        desc = str(merged.get("desc", ""))
                        tier = str(merged.get("tier", "common"))
                        perm = str(merged.get("permission", "read"))
                        cmd = merged.get("cmd")
                        if cmd is not None:
                            cmd = str(cmd)
                        params = merged.get("params") or {}

                        section = merged.get("section")
                        examples = merged.get("examples")
                        model_name = merged.get("model_name")
                        hidden = bool(merged.get("hidden", False))
                        aliases = merged.get("hidden_aliases") or merged.get("aliases")
                        conflict_group = merged.get("conflict_group")
                        parallel_limit = int(merged.get("parallel_limit", 0))
                        max_result_chars = int(merged.get("max_result_chars", 0))

                        examples_json = json.dumps(examples) if examples is not None else None
                        aliases_json = json.dumps(aliases) if aliases is not None else None

                        cur.execute(
                            """
                            INSERT INTO verb (name, sig, desc_default, tier, permission, cmd, params,
                                              section, examples, model_name, hidden, aliases,
                                              conflict_group, parallel_limit, max_result_chars)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (name) DO UPDATE SET
                                sig = EXCLUDED.sig,
                                desc_default = EXCLUDED.desc_default,
                                tier = EXCLUDED.tier,
                                permission = EXCLUDED.permission,
                                cmd = EXCLUDED.cmd,
                                params = EXCLUDED.params,
                                section = EXCLUDED.section,
                                examples = EXCLUDED.examples,
                                model_name = EXCLUDED.model_name,
                                hidden = EXCLUDED.hidden,
                                aliases = EXCLUDED.aliases,
                                conflict_group = EXCLUDED.conflict_group,
                                parallel_limit = EXCLUDED.parallel_limit,
                                max_result_chars = EXCLUDED.max_result_chars;
                            """,
                            (
                                vname, sig, desc, tier, perm, cmd, json.dumps(params),
                                section, examples_json, model_name, hidden, aliases_json,
                                conflict_group, parallel_limit, max_result_chars
                            )
                        )
                log.info("Verbs seeded.")

                routing = data.get("routing") or {}
                domains = routing.get("domains") or {}
                if isinstance(domains, dict):
                    cur.execute("TRUNCATE TABLE domain_verb;")
                    for dom, dom_cfg in domains.items():
                        if not isinstance(dom_cfg, dict):
                            continue
                        vlist = dom_cfg.get("verbs") or []
                        desc = dom_cfg.get("desc") or ""
                        for vname in vlist:
                            cur.execute("SELECT 1 FROM verb WHERE name = %s;", (vname,))
                            if cur.fetchone():
                                cur.execute(
                                    """
                                    INSERT INTO domain_verb (domain, verb_name, description)
                                    VALUES (%s, %s, %s)
                                    ON CONFLICT (domain, verb_name) DO UPDATE SET description = EXCLUDED.description;
                                    """,
                                    (dom, vname, desc)
                                )
                log.info("Domain verb mappings seeded.")

                packages = data.get("packages") or {}
                if isinstance(packages, dict):
                    for sec_name, sec_cfg in packages.items():
                        if not isinstance(sec_cfg, dict) or "pkgs" not in sec_cfg:
                            continue
                        pkgs = sec_cfg.get("pkgs") or []
                        enable = bool(sec_cfg.get("enable", True))
                        layer = int(sec_cfg.get("layer", 0))
                        base_image_ref = sec_cfg.get("base_image_ref", "")
                        section = sec_cfg.get("section", "Misc")

                        cur.execute(
                            """
                            INSERT INTO package_set (name, section, pkgs, enable, layer, base_image_ref)
                            VALUES (%s, %s, %s, %s, %s, %s)
                            ON CONFLICT (name) DO UPDATE SET
                                section = EXCLUDED.section,
                                pkgs = EXCLUDED.pkgs,
                                enable = EXCLUDED.enable,
                                layer = EXCLUDED.layer,
                                base_image_ref = EXCLUDED.base_image_ref;
                            """,
                            (sec_name, section, json.dumps(pkgs), enable, layer, base_image_ref)
                        )
                log.info("package_set seeded.")

                import re
                repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
                automation_dir = os.path.join(repo_root, "automation")
                if not os.path.isdir(automation_dir):
                    automation_dir = "/ctx/automation"
                if not os.path.isdir(automation_dir):
                    automation_dir = "/usr/share/mios/automation"

                log.info("Scanning automation directory: %s", automation_dir)
                if os.path.isdir(automation_dir):
                    scripts = []
                    for f in os.listdir(automation_dir):
                        if re.match(r"^\d{2}-.*\.sh$", f):
                            scripts.append(f)
                    scripts.sort()

                    prev_script = None
                    for idx, s in enumerate(scripts):
                        ordinal = int(s.split("-", 1)[0])
                        deps = [prev_script] if prev_script else []

                        cur.execute(
                            """
                            INSERT INTO build_phase (ordinal, script, stage, deps)
                            VALUES (%s, %s, 'container', %s)
                            ON CONFLICT (script) DO UPDATE SET
                                ordinal = EXCLUDED.ordinal,
                                stage = EXCLUDED.stage,
                                deps = EXCLUDED.deps;
                            """,
                            (ordinal, s, json.dumps(deps))
                        )
                        prev_script = s

                    fb_dir = os.path.join(automation_dir, "firstboot")
                    if os.path.isdir(fb_dir):
                        for f in os.listdir(fb_dir):
                            if f.endswith(".sh"):
                                script_path = f"firstboot/{f}"
                                cur.execute(
                                    """
                                    INSERT INTO build_phase (ordinal, script, stage, deps)
                                    VALUES (NULL, %s, 'firstboot', '[]'::jsonb)
                                    ON CONFLICT (script) DO UPDATE SET
                                        ordinal = EXCLUDED.ordinal,
                                        stage = EXCLUDED.stage,
                                        deps = EXCLUDED.deps;
                                    """,
                                    (script_path,)
                                )
                log.info("build_phase seeded.")

                bootstrap_dir = os.path.abspath(os.path.join(repo_root, "..", "mios-bootstrap", "field", "autounattend"))
                debloat_json_path = os.path.join(bootstrap_dir, "mios-debloat.json")
                features_txt_path = os.path.join(bootstrap_dir, "mios-xbox-features.txt")

                policies_to_seed = {}
                features_to_seed = []

                if os.path.isfile(debloat_json_path):
                    try:
                        with open(debloat_json_path, "r", encoding="utf-8") as f:
                            policies_to_seed = json.load(f)
                    except Exception as e:
                        log.warning("Failed to parse mios-debloat.json: %s", e)

                if os.path.isfile(features_txt_path):
                    try:
                        with open(features_txt_path, "r", encoding="utf-8") as f:
                            features_to_seed = [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]
                    except Exception as e:
                        log.warning("Failed to parse mios-xbox-features.txt: %s", e)

                for pol_name, pol_rules in policies_to_seed.items():
                    if pol_name == "_comment" or not isinstance(pol_rules, list):
                        continue
                    pol_type = "service" if pol_name == "system_services" else "registry"
                    cur.execute(
                        """
                        INSERT INTO debloat_policy (name, policy_type, rules)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (name) DO UPDATE SET policy_type = EXCLUDED.policy_type, rules = EXCLUDED.rules;
                        """,
                        (pol_name, pol_type, json.dumps(pol_rules))
                    )

                cur.execute(
                    """
                    INSERT INTO debloat_profile (name, description)
                    VALUES ('default', 'Default debloat profile')
                    ON CONFLICT (name) DO UPDATE SET description = EXCLUDED.description;
                    """
                )

                cur.execute(
                    """
                    INSERT INTO preset (name, description, features, debloat_profile_name)
                    VALUES ('default', 'Default preset', %s, 'default')
                    ON CONFLICT (name) DO UPDATE SET description = EXCLUDED.description, features = EXCLUDED.features, debloat_profile_name = EXCLUDED.debloat_profile_name;
                    """,
                    (json.dumps(features_to_seed),)
                )
                log.info("Debloat and Xbox features/profiles seeded.")

                cur.execute(
                    """
                    UPDATE account
                    SET home_dir = COALESCE(home_dir, '/home/' || name),
                        shell = COALESCE(shell, '/bin/bash')
                    WHERE home_dir IS NULL OR shell IS NULL;
                    """
                )
                log.info("Account defaults backfilled.")

                conn.commit()
                log.info("Seeding completed successfully.")
    except Exception as e:
        log.error("DEGRADED: database seeding failed (pgvector unavailable): %s", e)
        return 1

    return 0

if __name__ == "__main__":
    sys.exit(main())
