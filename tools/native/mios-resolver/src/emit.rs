// AI-hint: Canonical export-map builder -- combines walked canonical keys with legacy aliases and applies WALK_MOSTLY_DEAD suppression -- and every emitter over it: shell, PowerShell, JSON, install.env, build and repos.
// AI-related: tools/native/mios-ssot-walk, usr/lib/mios/mios_toml.py, usr/libexec/mios/57-mios-sys-build.sh, usr/share/mios/mios.toml, /etc/mios/install.env, usr/share/containers/systemd, automation/lib/globals.ps1, automation/06-enable-external-repos.sh, usr/share/mios/mios.toml [repos.external], usr/lib/mios/userenv.sh, tools/lib/userenv.sh
use mios_ssot_walk::{is_emit_keep_var, is_excluded_section, is_mostly_dead_section};
use std::collections::BTreeMap;
use toml::Value;

use crate::aliases::get_aliases;
use crate::walk::{process_val, walk};

pub fn build_exports_map(merged: &Value, stack_offset: i64) -> BTreeMap<String, String> {
    build_exports(merged, stack_offset, false, None)
}

/// Emitted name -> the SSOT key path whose value it carries, traced with the
/// emitters' own precedence; a name whose value is computed has no entry.
pub fn export_sources(merged: &Value, stack_offset: i64) -> BTreeMap<String, String> {
    let mut sources = BTreeMap::new();
    let exports = build_exports(merged, stack_offset, false, Some(&mut sources));
    // A derived port carries the category value it was copied from verbatim.
    let origins = crate::ports::port_origins(merged);
    for source in sources.values_mut() {
        if let Some(origin) = origins.get(source.as_str()) {
            *source = origin.clone();
        }
    }
    // A value that is exactly one `${MIOS_X}` reference carries MIOS_X's value
    // whenever MIOS_X is set, so it is MIOS_X's declaration, not a second one.
    // An unset reference falls back to its default and stays its own.
    let traced = sources.clone();
    for (name, source) in sources.iter_mut() {
        let mut current = name.clone();
        for _ in 0..crate::expand::MAX_DEPTH {
            let Some(target) = exports
                .get(&current)
                .and_then(|v| crate::expand::sole_reference(v))
                .filter(|t| exports.get(*t).is_some_and(|v| !v.is_empty()))
            else {
                break;
            };
            current = target.to_string();
        }
        if &current != name {
            if let Some(origin) = traced.get(&current) {
                *source = origin.clone();
            }
        }
    }
    sources
}

/// Globals use the same naming/value engine. Only their historical projection
/// scope differs: comments, env overrides and the dead Guacamole aliases.
pub fn build_globals_map(merged: &Value, stack_offset: i64) -> BTreeMap<String, String> {
    let mut exports = build_exports(merged, stack_offset, true, None);
    exports.remove("MIOS_PORT_GUACAMOLE");
    exports.remove("MIOS_GUACAMOLE_PORT");
    exports
}

fn build_exports(
    merged: &Value,
    stack_offset: i64,
    globals: bool,
    mut sources: Option<&mut BTreeMap<String, String>>,
) -> BTreeMap<String, String> {
    let mut exports = BTreeMap::new();
    let mut legacy = BTreeMap::new();
    // An alias two declarations write has no single source: which one lands
    // follows table iteration order, which the toml crate's preserve_order
    // feature flips between builds of this same code. None keeps it
    // untraceable everywhere rather than traced differently per build.
    let mut legacy_source: BTreeMap<String, Option<String>> = BTreeMap::new();
    let all_pairs = walk(merged);

    // Any character that is not [A-Za-z0-9_] becomes `_`, matching
    // render-globals.py's _UNSAFE_NAME_RE. Replacing only `.`, `-` and `/` left
    // keys like ..._MIOS_LLM_WORKER@ that the Python resolver renders as
    // ..._MIOS_LLM_WORKER_, and neither shell nor PowerShell accepts the first.
    fn sanitize(name: &str) -> String {
        name.chars()
            .map(|c| {
                if c.is_ascii_alphanumeric() || c == '_' {
                    c
                } else {
                    '_'
                }
            })
            .collect()
    }

    for (path, val) in all_pairs {
        if globals && path.rsplit('.').next() == Some("comment") {
            continue;
        }
        let val_processed = process_val(&path, &val, stack_offset);
        if val_processed.is_empty() {
            continue;
        }

        // Projected by their own renderers, not exported as variables.
        if is_excluded_section(path.split('.').next().unwrap_or(&path)) {
            continue;
        }

        let canonical = crate::names::canonical_name(&path);

        let sec_name = path.split('.').next().unwrap_or(&path);
        if is_mostly_dead_section(sec_name) && !is_emit_keep_var(&canonical) {
            // Suppressed canonical key
        } else {
            if let Some(sources) = sources.as_deref_mut() {
                sources.insert(canonical.clone(), path.clone());
            }
            exports.insert(canonical, val_processed.clone());
        }

        // An alias carries its canonical key's value, EXCEPT that
        // image.sidecars.*_VERSION carries only the tag.
        //
        // This split was removed once, on the grounds that it made
        // MIOS_ADGUARD_VERSION "latest" where globals.sh carried the full ref.
        // That traded a cosmetic disagreement on 22 unconsumed keys for a wrong
        // value on the one that IS consumed: automation/36-ceph-k3s.sh does
        // K3S_TAG="${MIOS_K3S_VERSION:-}" and then ${K3S_TAG/-k3s/+k3s}.
        // common.sh sources userenv.sh BEFORE globals.sh and globals.sh guards
        // its assignments, so the value comes from userenv.sh -- whose preferred
        // tier is this binary. Without the split a bake gets
        // docker.io/rancher/k3s:v1.36.3+k3s1 as a release tag (task_3363).
        for leg in get_aliases(&path) {
            let v = if leg.ends_with("_VERSION") && path.starts_with("image.sidecars.") {
                match val_processed.rsplit_once(':') {
                    Some((_, tag)) => tag.to_string(),
                    None => "latest".to_string(),
                }
            } else {
                val_processed.clone()
            };
            legacy_source
                .entry(leg.clone())
                .and_modify(|seen| {
                    if seen.as_deref() != Some(path.as_str()) {
                        *seen = None;
                    }
                })
                .or_insert_with(|| Some(path.clone()));
            legacy.insert(leg, v);
        }
    }
    // Canonical keys retain their own typed value even when another key once
    // used the same spelling as a compatibility alias (database vs OS account).
    for (name, value) in legacy {
        if let std::collections::btree_map::Entry::Vacant(slot) = exports.entry(name) {
            if let Some(sources) = sources.as_deref_mut() {
                if let Some(Some(path)) = legacy_source.get(slot.key()) {
                    sources.insert(slot.key().clone(), path.clone());
                }
            }
            slot.insert(value);
        }
    }

    // MIOS_COLOR_<name> for every palette entry ([colors] over the palette
    // table), never overriding a walked key -- mios_toml.emit_exports' setdefault.
    for (name, value) in crate::palette::resolve(merged) {
        let upper = name.to_uppercase();
        let key = if upper.starts_with("MIOS_COLOR_") {
            upper
        } else {
            format!("MIOS_COLOR_{}", upper)
        };
        exports.entry(sanitize(&key)).or_insert(value);
    }

    // [env] verbatim, last, so it wins -- mios_toml.emit_exports' env_tbl
    // merge (empty values skipped). Every emitter and runtime::get see it.
    if let Some(env_table) = merged
        .get("env")
        .and_then(|v| v.as_table())
        .filter(|_| !globals)
    {
        for (k, v) in env_table {
            let val = process_val(&format!("env.{k}"), v, stack_offset);
            if !val.is_empty() {
                if let Some(sources) = sources.as_deref_mut() {
                    sources.insert(sanitize(k), format!("env.{k}"));
                }
                exports.insert(sanitize(k), val);
            }
        }
    }

    exports
}

/// Resolve `${MIOS_*}` that one emitted value makes to another.
///
/// Called by the emitters whose consumer CANNOT expand -- `emit_json` (the
/// resolved-environment view) and `emit_install_env` (systemd
/// `EnvironmentFile=` and podman `--env-file`). It is deliberately NOT called
/// by `build_exports_map`, because `emit_shell` and `emit_ps` render into bash
/// and PowerShell, which expand at source time: keeping the reference live
/// there is what makes an operator's pre-exported `MIOS_PORTS_AGENT_PIPE`
/// propagate into `MIOS_AI_ENDPOINT`. Baking in the shared builder would take
/// that property away from both generated globals files.
///
/// systemd `EnvironmentFile=` and podman `--env-file` have no such expansion,
/// so an emitted `MIOS_AI_ENDPOINT=http://localhost:${MIOS_PORTS_AGENT_PIPE}/v1`
/// means two different things depending on who reads it. It is also why
/// `system-sync-env.sh` DROPPED that variable rather than emitting it: its
/// filter rejects any value containing `$` (task_997).
///
/// Expansion reads a snapshot, so the result does not depend on map order, and
/// a name that resolves to nothing is left verbatim rather than blanked -- the
/// caller can then report it instead of shipping an empty string.
pub fn resolve_cross_references(exports: &mut BTreeMap<String, String>) {
    let snapshot = exports.clone();
    for value in exports.values_mut() {
        if value.contains("${") {
            *value = crate::expand::expand(value, &snapshot).text;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The tag-split has been removed once already, on cosmetic grounds, and
    /// that broke the one consumer: automation/36-ceph-k3s.sh computes
    /// K3S_TAG="${MIOS_K3S_VERSION:-}" and then ${K3S_TAG/-k3s/+k3s}. With the
    /// full ref it produces docker.io/rancher/k3s:v1.36.3+k3s1 as a release tag.
    #[test]
    fn sidecar_version_alias_carries_only_the_tag() {
        let val: Value = toml::from_str(
            r#"
[image.sidecars]
k3s = "docker.io/rancher/k3s:v1.36.3-k3s1"
"#,
        )
        .unwrap();
        let e = build_exports_map(&val, 0);
        assert_eq!(
            e.get("MIOS_K3S_VERSION").map(String::as_str),
            Some("v1.36.3-k3s1"),
            "the _VERSION alias must be the tag, not the image ref"
        );
        assert_eq!(
            e.get("MIOS_K3S_IMAGE").map(String::as_str),
            Some("docker.io/rancher/k3s:v1.36.3-k3s1"),
            "the _IMAGE alias keeps the full ref, so the two names stay distinct"
        );
    }

    /// An untagged ref has no tag to split; "latest" is what it resolves to.
    #[test]
    fn sidecar_version_without_a_tag_is_latest() {
        let val: Value = toml::from_str(
            r#"
[image.sidecars]
thing = "docker.io/library/thing"
"#,
        )
        .unwrap();
        let e = build_exports_map(&val, 0);
        assert_eq!(
            e.get("MIOS_THING_VERSION").map(String::as_str),
            Some("latest")
        );
    }

    #[test]
    fn sources_trace_every_spelling_to_its_declaration() {
        let val: Value = toml::from_str(
            r#"
[converge]
memory_dir = "/var/lib/x"

[ports]
radosgw = 8470
pgvector_internal = 1
agent_pipe = 1
llm_heavy = 1

[ports.categories.data]
base = 8600
stride = 10
members = ["pgvector"]
pinned = { pgvector_internal = 5432 }

[ports.categories.agent]
base = 8700
stride = 10
members = ["agent_pipe", "llm_heavy"]

[image.sidecars]
sglang = "docker.io/lmsysorg/sglang:latest"

[images.heavy.Image]
Image = "${MIOS_SGLANG_IMAGE:-docker.io/lmsysorg/sglang:latest}"

[dispatch]
hint = "http://${MIOS_PORTS_LLM_HEAVY}/v1"

[pgvector]
user = "database"

[services.pgvector]
user = "os-account"

[env]
MIOS_OVERRIDDEN = "x"

[other]
overridden = "y"
"#,
        )
        .unwrap();
        let mut merged = val;
        crate::ports::derive_ports(&mut merged);
        let s = export_sources(&merged, 0);
        // One declaration, three spellings.
        assert_eq!(s["MIOS_CONVERGE_MEMORY_DIR"], "converge.memory_dir");
        assert_eq!(s["MIOS_CONV_MEMORY_DIR"], "converge.memory_dir");
        assert_eq!(s["MIOS_PORTS_RADOSGW"], "ports.radosgw");
        assert_eq!(s["MIOS_RADOSGW_PORT"], "ports.radosgw");
        // A pinned or offset-0 port IS its category key.
        assert_eq!(
            s["MIOS_PORT_PGVECTOR_INTERNAL"],
            "ports.categories.data.pinned.pgvector_internal"
        );
        assert_eq!(s["MIOS_PORTS_AGENT_PIPE"], "ports.categories.agent.base");
        // A computed member keeps its own declaration.
        assert_eq!(s["MIOS_PORTS_LLM_HEAVY"], "ports.llm_heavy");
        // A whole-value reference is the referenced declaration...
        assert_eq!(s["MIOS_IMAGES_HEAVY_IMAGE_IMAGE"], "image.sidecars.sglang");
        // ...a composition is its own.
        assert_eq!(s["MIOS_DISPATCH_HINT"], "dispatch.hint");
        // A canonical key keeps its value against another key's alias.
        assert_eq!(s["MIOS_PGVECTOR_USER"], "pgvector.user");
        assert_eq!(s["MIOS_SERVICES_PGVECTOR_USER"], "services.pgvector.user");
        // [env] wins, and says so.
        assert_eq!(s["MIOS_OVERRIDDEN"], "env.MIOS_OVERRIDDEN");
        // Two declarations writing one alias: no single source, in any build.
        let two: Value =
            toml::from_str("[urls]\nbootstrap_repo = \"u\"\n[bootstrap]\nbootstrap_repo = \"u\"\n")
                .unwrap();
        let s = export_sources(&two, 0);
        assert!(!s.contains_key("MIOS_BOOTSTRAP_REPO_URL"), "{s:?}");
        assert_eq!(s["MIOS_URLS_BOOTSTRAP_REPO"], "urls.bootstrap_repo");
    }

    #[test]
    fn test_mostly_dead_suppression() {
        let val: Value = toml::from_str(
            r#"
[ai]
endpoint = "http://localhost:8000"
random_setting = "test"
"#,
        )
        .unwrap();

        let exports = build_exports_map(&val, 0);
        assert!(exports.contains_key("MIOS_AI_ENDPOINT")); // In WALK_EMIT_KEEP
        assert!(!exports.contains_key("MIOS_AI_RANDOM_SETTING")); // Suppressed!
    }
}

#[cfg(test)]
mod canonical_export_tests {
    #[test]
    fn signing_build_inputs_survive_security_suppression() {
        let data: toml::Value = "[security.sigstore]\ncosign_version='fixture-pin'\ncosign_release_url='https://example.invalid/releases'\nprivate_setting='must-not-export'\n".parse().unwrap();
        for exports in [
            super::build_exports_map(&data, 0),
            super::build_globals_map(&data, 0),
        ] {
            assert_eq!(
                exports["MIOS_SECURITY_SIGSTORE_COSIGN_VERSION"],
                "fixture-pin"
            );
            assert_eq!(
                exports["MIOS_SECURITY_SIGSTORE_COSIGN_RELEASE_URL"],
                "https://example.invalid/releases"
            );
            assert!(!exports.contains_key("MIOS_SECURITY_SIGSTORE_PRIVATE_SETTING"));
        }
    }

    #[test]
    fn service_account_alias_cannot_replace_database_username() {
        let data: toml::Value =
            "[pgvector]\nuser='database'\n[services.pgvector]\nuser='os-account'\n"
                .parse()
                .unwrap();
        for exports in [
            super::build_exports_map(&data, 0),
            super::build_globals_map(&data, 0),
        ] {
            assert_eq!(exports["MIOS_PGVECTOR_USER"], "database");
            assert_eq!(exports["MIOS_PG_USER"], "database");
            assert_eq!(exports["MIOS_SERVICES_PGVECTOR_USER"], "os-account");
        }
    }
}

pub mod emit_build {
    use std::collections::BTreeSet;
    use toml::Value;

    pub fn emit_build_shell(merged: &Value, stack_offset: i64) -> Result<String, String> {
        let mut packages = Vec::new();
        let mut seen = BTreeSet::new();
        for section in ["mcp", "agent_cli"] {
            let values = merged
                .get("packages")
                .and_then(|v| v.get(section))
                .and_then(|v| v.get("pkgs"))
                .and_then(Value::as_array)
                .ok_or_else(|| format!("SSOT packages.{section}.pkgs must be an array"))?;
            for value in values {
                let package = value
                    .as_str()
                    .filter(|s| !s.is_empty() && !s.chars().any(char::is_whitespace))
                    .ok_or_else(|| {
                        format!("SSOT packages.{section}.pkgs contains an invalid package")
                    })?;
                if seen.insert(package) {
                    packages.push(package);
                }
            }
        }
        if packages.is_empty() {
            return Err("SSOT service-base packages are empty".into());
        }
        let reference = merged
            .get("build")
            .and_then(|v| v.get("bake_refs"))
            .and_then(|v| v.get("searxng"))
            .and_then(Value::as_str)
            .filter(|s| !s.is_empty())
            .ok_or("SSOT build.bake_refs.searxng is empty")?;
        let mut exports = crate::emit::build_exports_map(merged, stack_offset);
        crate::emit::resolve_cross_references(&mut exports);
        let mut output = format!(
            "export MIOS_MCP_PACKAGES={}\nexport SEARXNG_REF={}\n",
            crate::emit_shell::shlex_quote(&packages.join(" ")),
            crate::emit_shell::shlex_quote(reference)
        );
        for key in [
            "MIOS_SERVICES_PIPER_BASE",
            "MIOS_SERVICES_PIPER_VERSION",
            "MIOS_SERVICES_PIPER_VOICE",
            "MIOS_SERVICES_PIPER_UID",
            "MIOS_SERVICES_PIPER_GID",
        ] {
            let value = exports
                .get(key)
                .filter(|v| !v.is_empty())
                .ok_or_else(|| format!("SSOT {key} is empty"))?;
            output.push_str(&format!(
                "export {key}={}\n",
                crate::emit_shell::shlex_quote(value)
            ));
        }
        Ok(output)
    }

    #[cfg(test)]
    mod tests {
        use super::*;
        fn fixture() -> Value {
            "[packages.mcp]\npkgs=['python3','git']\n[packages.agent_cli]\npkgs=['git','tmux']\n[build.bake_refs]\nsearxng='custom'\n[env]\nMIOS_SERVICES_PIPER_BASE='base'\nMIOS_SERVICES_PIPER_VERSION='v1'\nMIOS_SERVICES_PIPER_VOICE='voice'\nMIOS_SERVICES_PIPER_UID='1000'\nMIOS_SERVICES_PIPER_GID='1000'".parse().unwrap()
        }
        #[test]
        fn selections_are_deduplicated_and_shell_quoted() {
            let mut data = fixture();
            data["build"]["bake_refs"]["searxng"] =
                Value::String("branch'; touch /tmp/control".into());
            let text = emit_build_shell(&data, 0).unwrap();
            assert!(text.contains("MIOS_MCP_PACKAGES='python3 git tmux'"));
            assert!(text.contains("SEARXNG_REF='branch'\"'\"'; touch /tmp/control'"));
        }
        #[test]
        fn missing_and_malformed_inputs_do_not_emit_partial_exports() {
            let mut data = fixture();
            data["packages"]["mcp"]["pkgs"] =
                Value::Array(vec![Value::String("two packages".into())]);
            assert!(emit_build_shell(&data, 0).is_err());
            let mut data = fixture();
            data["env"]["MIOS_SERVICES_PIPER_VOICE"] = Value::String(String::new());
            assert!(emit_build_shell(&data, 0).is_err());
        }
    }
}

pub mod emit_install_env {
    use std::path::Path;
    use toml::Value;

    use crate::emit::{build_exports_map, resolve_cross_references};

    pub fn emit_install_env(
        merged: &Value,
        stack_offset: i64,
        _ref_names_path: Option<&Path>,
    ) -> String {
        let mut exports = build_exports_map(merged, stack_offset);

        // After the [env] merge, so an [env] value can both reference an
        // exported key and be referenced by one. Unresolved values still carry
        // `$` and are dropped by the bare-safe filter below (Law 10) -- which is
        // exactly how MIOS_AI_ENDPOINT went missing before task_997.
        resolve_cross_references(&mut exports);

        let mut lines = Vec::new();

        for (k, v) in &exports {
            if k.contains("SECRET") || k.contains("TOKEN") || k.contains("PASSWORD") {
                continue;
            }
            if v.contains(' ') || v.contains('\n') || v.contains('$') {
                continue;
            }
            lines.push(format!("{}={}", k, v));
        }

        lines.sort();
        lines.join("\n") + "\n"
    }

    #[cfg(test)]
    mod tests {
        use super::*;

        #[test]
        fn test_emit_install_env() {
            let val: Value = toml::from_str(
                r#"
[identity]
role = "mini"
"#,
            )
            .unwrap();
            let env_str = emit_install_env(&val, 0, None);
            assert!(env_str.contains("MIOS_IDENTITY_ROLE=mini"));
        }
    }
}

pub mod emit_json {
    use serde_json::{json, Value as JsonValue};
    use toml::Value;

    use crate::emit::{build_exports_map, resolve_cross_references};

    pub fn emit_json(merged: &Value, stack_offset: i64) -> String {
        // Nothing downstream of this JSON re-expands, so the cross-references
        // are resolved here rather than shipped as text a consumer must parse.
        let mut exports = build_exports_map(merged, stack_offset);
        resolve_cross_references(&mut exports);
        let merged_json: JsonValue = serde_json::to_value(merged).unwrap_or(json!({}));

        let res = json!({
            "merged": merged_json,
            "exports": exports,
        });

        serde_json::to_string_pretty(&res).unwrap_or_else(|_| "{}".to_string()) + "\n"
    }

    #[cfg(test)]
    mod tests {
        use super::*;

        #[test]
        fn test_emit_json() {
            let val: Value = toml::from_str(
                r#"
[identity]
role = "mini"
"#,
            )
            .unwrap();
            let j_str = emit_json(&val, 0);
            let parsed: JsonValue = serde_json::from_str(&j_str).unwrap();
            assert!(parsed.get("merged").is_some());
            assert!(parsed.get("exports").is_some());
        }
    }
}

pub mod emit_ps {
    use toml::Value;

    use crate::emit::{build_exports_map, resolve_cross_references};
    use crate::palette::resolve as resolve_palette;

    pub fn emit_powershell(merged: &Value, stack_offset: i64) -> String {
        // Emitted as '...' below, and a PowerShell single-quoted string does not
        // interpolate either, so the same literal-export defect applies here.
        let mut exports = build_exports_map(merged, stack_offset);
        resolve_cross_references(&mut exports);
        let palette = resolve_palette(merged);

        let mut lines = Vec::new();
        lines.push(
            "# Generated by mios-resolver --emit=powershell - DO NOT EDIT DIRECTLY".to_string(),
        );
        lines.push("# SSOT values derived from mios.toml layers".to_string());
        lines.push("".to_string());

        for (k, v) in &exports {
            let escaped = v.replace('\'', "''");
            lines.push(format!(
                "$script:{} = if ($env:{}) {{ $env:{} }} else {{ '{}' }}",
                k, k, k, escaped
            ));
        }

        for (k, v) in &palette {
            let key_name = format!("MIOS_COLOR_{}", k.to_uppercase());
            if !exports.contains_key(&key_name) {
                let escaped = v.replace('\'', "''");
                lines.push(format!(
                    "$script:{} = if ($env:{}) {{ $env:{} }} else {{ '{}' }}",
                    key_name, key_name, key_name, escaped
                ));
            }
        }

        lines.join("\n") + "\n"
    }

    #[cfg(test)]
    mod tests {
        use super::*;

        /// A PowerShell single-quoted string does not interpolate, so an
        /// unresolved ${MIOS_*} would be assigned as literal text.
        #[test]
        fn test_cross_reference_is_resolved_not_quoted_literal() {
            let val: Value = toml::from_str(
                r#"
[ports]
agent_pipe = 8700

[ai]
endpoint = "http://localhost:${MIOS_PORTS_AGENT_PIPE}/v1"
"#,
            )
            .unwrap();
            let out = emit_powershell(&val, 0);
            assert!(
                out.contains("else { 'http://localhost:8700/v1' }"),
                "expected the resolved literal, got:\n{out}"
            );
            assert!(!out.contains("${MIOS_PORTS_AGENT_PIPE}"));
        }

        #[test]
        fn test_emit_powershell() {
            let val: Value = toml::from_str(
                r#"
[ports]
vllm = 8000
"#,
            )
            .unwrap();
            let ps = emit_powershell(&val, 0);
            assert!(ps.contains("$script:MIOS_PORTS_VLLM = if ($env:MIOS_PORTS_VLLM) { $env:MIOS_PORTS_VLLM } else { '8000' }"));
        }
    }
}

pub mod emit_repos {
    use toml::Value;

    pub fn emit_repos(merged: &Value) -> Result<String, String> {
        let repos = merged
            .get("repos")
            .and_then(|v| v.get("external"))
            .and_then(Value::as_table)
            .ok_or("SSOT repos.external must be a table")?;
        let mut result =
            String::from("# Generated by native mios-resolver from layered mios.toml.\n");
        for (id, data) in repos {
            if id.is_empty()
                || !id
                    .bytes()
                    .all(|c| c.is_ascii_alphanumeric() || c == b'-' || c == b'_')
            {
                return Err("Invalid external repository identifier".into());
            }
            let enabled = data
                .get("enabled")
                .and_then(Value::as_bool)
                .ok_or_else(|| format!("repos.external.{id}.enabled must be a boolean"))?;
            if !enabled {
                continue;
            }
            let text = |key: &str| -> Result<&str, String> {
                data.get(key)
                    .and_then(Value::as_str)
                    .filter(|s| !s.is_empty() && !s.chars().any(char::is_control))
                    .ok_or_else(|| format!("Invalid repos.external.{id}.{key}"))
            };
            let name = text("name")?;
            let baseurl = text("baseurl")?;
            let gpgkey = text("gpgkey")?;
            if !baseurl.starts_with("https://")
                || !gpgkey.starts_with("https://")
                || baseurl.contains(char::is_whitespace)
                || gpgkey.contains(char::is_whitespace)
            {
                return Err(format!(
                    "repos.external.{id} requires HTTPS baseurl and gpgkey"
                ));
            }
            result.push_str(&format!("\n[mios-{id}]\nname={name}\nbaseurl={baseurl}\ngpgkey={gpgkey}\nenabled=1\ngpgcheck=1\nskip_if_unavailable=False\n"));
        }
        Ok(result)
    }

    #[cfg(test)]
    mod tests {
        use super::*;
        #[test]
        fn projects_operator_url_and_rejects_injected_ini_sections() {
            let mut data: Value = "[repos.external.hyprland]\nenabled=true\nname='Hyprland'\nbaseurl='https://example.test/fedora-$releasever-$basearch/'\ngpgkey='https://example.test/pubkey.gpg'".parse().unwrap();
            let output = emit_repos(&data).unwrap();
            assert!(output.contains("baseurl=https://example.test/fedora-$releasever-$basearch/"));
            assert!(output.contains("gpgcheck=1\nskip_if_unavailable=False"));
            data["repos"]["external"]["hyprland"]["name"] =
                Value::String("good\n[evil]\ngpgcheck=0".into());
            assert!(emit_repos(&data).is_err());
        }
        #[test]
        fn disabled_repo_is_omitted_and_insecure_enabled_repo_rejected() {
            let mut data: Value = "[repos.external.custom]\nenabled=false".parse().unwrap();
            assert!(!emit_repos(&data).unwrap().contains("[mios-custom]"));
            data["repos"]["external"]["custom"]["enabled"] = Value::Boolean(true);
            assert!(emit_repos(&data).is_err());
        }
    }
}

pub mod emit_shell {
    use std::fs;
    use std::path::Path;
    use toml::Value;

    use crate::emit::{build_exports_map, resolve_cross_references};

    pub fn shlex_quote(s: &str) -> String {
        if s.is_empty() {
            return "''".to_string();
        }
        if s.chars().all(|c| {
            matches!(c,
            'a'..='z' | 'A'..='Z' | '0'..='9' | '-' | '_' | '.' | ':' | '/' | '@')
        }) {
            return s.to_string();
        }
        format!("'{}'", s.replace('\'', "'\"'\"'"))
    }

    pub fn emit_shell(merged: &Value, stack_offset: i64, ref_names_path: Option<&Path>) -> String {
        let mut exports = build_exports_map(merged, stack_offset);

        if !exports.contains_key("MIOS_PG_BIND_ADDR") {
            let is_loopback = exports
                .get("MIOS_PGVECTOR_LISTEN_LOOPBACK")
                .map(|s| s == "true" || s == "1")
                .unwrap_or(true);
            let loopback_addr = exports
                .get("MIOS_NETWORK_LOOPBACK")
                .map(|s| s.as_str())
                .unwrap_or("127.0.0.1");
            let bind_all_addr = exports
                .get("MIOS_NETWORK_BIND_ALL")
                .map(|s| s.as_str())
                .unwrap_or("0.0.0.0");
            exports.insert(
                "MIOS_PG_BIND_ADDR".to_string(),
                if is_loopback { loopback_addr } else { bind_all_addr }.to_string(),
            );
        }

        // shlex_quote single-quotes anything containing `$`, and bash does not
        // expand inside single quotes -- so a live ${MIOS_*} reference here is
        // exported as literal text, never as its value. Nor is there anything to
        // expand against: these lines are sorted alphabetically, not
        // topologically, so a referent may be defined after its referrer. Unlike
        // automation/lib/globals.sh, which splices and topologically sorts, this
        // binding also exports unconditionally, so it never offered the
        // "pre-exported value wins" property that a live reference would serve.
        // Resolving here is what makes userenv.sh's native tier agree with its
        // Python fallback.
        resolve_cross_references(&mut exports);

        let mut lines = Vec::new();
        for (k, v) in &exports {
            lines.push(format!("export {}={}", k, shlex_quote(v)));
        }

        // Referenced names passthrough
        if let Some(ref_path) = ref_names_path {
            if ref_path.exists() {
                if let Ok(content) = fs::read_to_string(ref_path) {
                    for line in content.lines() {
                        let name = line.trim();
                        if !name.is_empty() && !exports.contains_key(name) {
                            lines.push(format!("export {}=\"${{{}:-}}\"", name, name));
                        }
                    }
                }
            }
        }

        lines.sort();
        lines.join("\n") + "\n"
    }

    #[cfg(test)]
    mod tests {
        use super::*;

        #[test]
        fn test_shlex_quote() {
            assert_eq!(shlex_quote("simple"), "simple");
            assert_eq!(shlex_quote("with space"), "'with space'");
            assert_eq!(shlex_quote("don't"), "'don'\"'\"'t'");
            assert_eq!(shlex_quote(""), "''");
        }

        /// shlex_quote single-quotes any value containing `$`, and bash does not
        /// expand inside single quotes. userenv.sh evals this output in its primary
        /// tier, so a value that still carried `${MIOS_PORTS_AGENT_PIPE}` here was
        /// exported to consumers verbatim, as that literal text.
        #[test]
        fn test_cross_reference_is_resolved_not_quoted_literal() {
            let val: Value = toml::from_str(
                r#"
[ports]
agent_pipe = 8700

[ai]
endpoint = "http://localhost:${MIOS_PORTS_AGENT_PIPE}/v1"
"#,
            )
            .unwrap();
            let out = emit_shell(&val, 0, None);
            let line = out
                .lines()
                .find(|l| l.starts_with("export MIOS_AI_ENDPOINT="))
                .expect("MIOS_AI_ENDPOINT is emitted");
            assert_eq!(line, "export MIOS_AI_ENDPOINT=http://localhost:8700/v1");
            assert!(
                !out.contains("${MIOS_PORTS_AGENT_PIPE}"),
                "a single-quoted ${{...}} is exported as literal text, not expanded"
            );
        }
    }
}
