// AI-hint: One canonical variable-name convention and compatibility inventory for every SSOT consumer.
// AI-related: usr/lib/mios/mios_toml.py, usr/share/mios/referenced_names.txt
use serde::Serialize;
use std::collections::{BTreeMap, BTreeSet};
use toml::Value;

/// The capability key determines the name; punctuation never introduces a
/// second namespace or a reader-specific abbreviation.
pub fn canonical_name(path: &str) -> String {
    let upper = path.to_uppercase();
    let body: String = upper
        .chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || c == '_' {
                c
            } else {
                '_'
            }
        })
        .collect();
    if body.starts_with("MIOS_") {
        body
    } else {
        format!("MIOS_{body}")
    }
}

#[derive(Debug, Serialize)]
pub struct NameEntry {
    pub key: String,
    pub canonical: String,
    pub emitted: bool,
    pub aliases: Vec<NameAlias>,
}

#[derive(Debug, Serialize)]
pub struct NameAlias {
    pub name: String,
    pub semantics: &'static str,
}

#[derive(Debug, Serialize)]
pub struct NameRegistry {
    pub convention: &'static str,
    pub entries: Vec<NameEntry>,
    pub ambiguous_aliases: BTreeMap<String, Vec<String>>,
    /// Emitted name -> the SSOT key path whose value it carries (see
    /// `emit::export_sources`). Two names with one source are one declaration
    /// under two spellings; a name absent here is its own declaration.
    pub sources: BTreeMap<String, String>,
}

impl NameRegistry {
    /// Only interchangeable inputs with one owner may cross the compatibility boundary.
    pub fn input_aliases(&self) -> BTreeMap<String, Vec<String>> {
        self.entries
            .iter()
            .filter(|entry| entry.emitted)
            .map(|entry| {
                let aliases: Vec<_> = entry
                    .aliases
                    .iter()
                    .filter(|alias| {
                        alias.semantics == "same-value"
                            && !self.ambiguous_aliases.contains_key(&alias.name)
                    })
                    .map(|alias| alias.name.clone())
                    .collect();
                (entry.canonical.clone(), aliases)
            })
            .collect()
    }
}

/// Metadata only: never serialize values, credentials, or process environment.
/// Distinct keys that collapse lexically are an error before any output writes.
pub fn registry(merged: &Value) -> Result<NameRegistry, String> {
    let offset = crate::stack_offset_of(merged);
    let exports = crate::emit::build_exports_map(merged, offset);
    let mut canonical_owners = BTreeMap::new();
    let mut alias_owners: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    let mut alias_names = BTreeSet::new();
    let mut entries = Vec::new();
    for (key, _) in crate::walk::walk(merged) {
        let canonical = canonical_name(&key);
        if let Some(previous) = canonical_owners.insert(canonical.clone(), key.clone()) {
            return Err(format!(
                "canonical variable collision {canonical}: {previous} and {key}"
            ));
        }
        let aliases = get_aliases(&key)
            .into_iter()
            .filter(|name| name != &canonical)
            .map(|name| {
                alias_names.insert(name.clone());
                alias_owners
                    .entry(name.clone())
                    .or_default()
                    .insert(key.clone());
                let semantics = if key.starts_with("image.sidecars.") && name.ends_with("_VERSION")
                {
                    "image-tag"
                } else {
                    "same-value"
                };
                NameAlias { name, semantics }
            })
            .collect();
        entries.push(NameEntry {
            emitted: exports.contains_key(&canonical),
            key,
            canonical,
            aliases,
        });
    }
    for (name, key) in canonical_owners {
        alias_owners.entry(name).or_default().insert(key);
    }
    let ambiguous_aliases = alias_owners
        .into_iter()
        .filter(|(name, owners)| alias_names.contains(name) && owners.len() > 1)
        .map(|(name, owners)| (name, owners.into_iter().collect()))
        .collect();
    Ok(NameRegistry {
        convention: "section.key -> MIOS_SECTION_KEY",
        entries,
        ambiguous_aliases,
        sources: crate::emit::export_sources(merged, offset),
    })
}

/// Accept old environment inputs at the process boundary while consumers move
/// to canonical names. A canonical input wins; disagreeing synonyms fail with
/// names only. Never put these process-local values into generated source.
pub fn overlay_inputs(
    merged: &mut Value,
    environment: impl Fn(&str) -> Option<String>,
) -> Result<(), String> {
    let registry = registry(merged)?;
    let mut overrides = BTreeMap::new();
    for (canonical, aliases) in registry.input_aliases() {
        let canonical_input = environment(&canonical).filter(|v| !v.is_empty());
        let value = if canonical_input.is_some() {
            canonical_input
        } else {
            let mut selected: Option<(String, String)> = None;
            for alias in &aliases {
                if let Some(value) = environment(alias).filter(|v| !v.is_empty()) {
                    if let Some((name, previous)) = &selected {
                        if previous != &value {
                            return Err(format!(
                                "conflicting legacy inputs {name} and {alias}; set {canonical}"
                            ));
                        }
                    } else {
                        selected = Some((alias.clone(), value));
                    }
                }
            }
            selected.map(|(_, value)| value)
        };
        if let Some(value) = value {
            overrides.insert(canonical, Value::String(value.clone()));
            for alias in aliases {
                overrides.insert(alias, Value::String(value.clone()));
            }
        }
    }
    if overrides.is_empty() {
        return Ok(());
    }
    let table = merged.as_table_mut().ok_or("SSOT root must be a table")?;
    let env = table
        .entry("env")
        .or_insert_with(|| Value::Table(Default::default()))
        .as_table_mut()
        .ok_or("SSOT env must be a table")?;
    env.extend(overrides);
    Ok(())
}

pub fn get_aliases(dotted_path: &str) -> Vec<String> {
    let mut aliases = Vec::new();

    if let Some(rest) = dotted_path.strip_prefix("converge.") {
        // Boundary compatibility for already installed units; canonical output
        // always uses the complete capability section name.
        aliases.push(format!("MIOS_CONV_{}", py_key(rest)));
    } else if let Some(rest) = dotted_path.strip_prefix("ai.vllm.") {
        let suffix = rest.to_uppercase().replace(['.', '-', '/'], "_");
        if suffix == "V1_ENGINE" {
            aliases.push("MIOS_VLLM_USE_V1".into());
        } else {
            aliases.push(format!("MIOS_VLLM_{}", suffix));
        }
    } else if let Some(rest) = dotted_path.strip_prefix("ai.sglang.") {
        let suffix = rest.to_uppercase().replace(['.', '-', '/'], "_");
        if suffix == "UNIFIED_RADIX_TREE" {
            aliases.push("MIOS_SGLANG_ENABLE_UNIFIED_RADIX_TREE".into());
        } else if suffix == "HIERARCHICAL_CACHE" {
            aliases.push("MIOS_SGLANG_ENABLE_HIERARCHICAL_CACHE".into());
        } else {
            aliases.push(format!("MIOS_SGLANG_{}", suffix));
        }
    } else if dotted_path == "identity.username" {
        aliases.push("MIOS_USER".into());
        aliases.push("MIOS_DEFAULT_USER".into());
    } else if dotted_path == "identity.uid" {
        aliases.push("MIOS_UID".into());
        aliases.push("MIOS_USER_UID".into());
    } else if dotted_path == "identity.fullname" {
        aliases.push("MIOS_USER_FULLNAME".into());
    } else if dotted_path == "identity.hostname" {
        aliases.push("MIOS_HOSTNAME".into());
        aliases.push("MIOS_DEFAULT_HOST".into());
    } else if dotted_path == "identity.shell" {
        aliases.push("MIOS_USER_SHELL".into());
        aliases.push("MIOS_DEFAULT_SHELL".into());
    } else if dotted_path == "identity.groups" {
        aliases.push("MIOS_USER_GROUPS".into());
        aliases.push("MIOS_DEFAULT_GROUPS".into());
    } else if dotted_path == "identity.default_password" {
        aliases.push("MIOS_DEFAULT_PASSWORD".into());
    } else if dotted_path == "accounts.db_backed" {
        aliases.push("MIOS_ACCOUNTS_DB_BACKED".into());
    } else if dotted_path == "locale.timezone" {
        aliases.push("MIOS_TIMEZONE".into());
        aliases.push("MIOS_DEFAULT_TIMEZONE".into());
    } else if dotted_path == "locale.keyboard_layout" {
        aliases.push("MIOS_KEYBOARD".into());
        aliases.push("MIOS_DEFAULT_KEYBOARD".into());
    } else if dotted_path == "locale.language" {
        aliases.push("MIOS_LOCALE".into());
        aliases.push("MIOS_DEFAULT_LOCALE".into());
    } else if dotted_path == "auth.ssh_key_action" {
        aliases.push("MIOS_SSH_KEY_ACTION".into());
    } else if dotted_path == "auth.password_policy" {
        aliases.push("MIOS_PASSWORD_POLICY".into());
    } else if dotted_path == "network.firewalld_default_zone" {
        aliases.push("MIOS_FIREWALLD_ZONE".into());
    } else if let Some(rest) = dotted_path.strip_prefix("portal.") {
        let suffix = rest.to_uppercase().replace(['.', '-', '/'], "_");
        if suffix == "PUBLIC_HOST" {
            aliases.push("MIOS_PUBLIC_HOST".into());
        } else {
            aliases.push(format!("MIOS_PORTAL_{}", suffix));
        }
    } else if let Some(rest) = dotted_path.strip_prefix("a2a.") {
        let name = rest.to_uppercase().replace(['.', '-', '/'], "_");
        if name == "DISCOVER_PORT" {
            aliases.push("MIOS_A2A_DISCOVER_PORT".into());
        } else if name == "PUBLIC_DOMAIN" {
            aliases.push("MIOS_PUBLIC_DOMAIN".into());
        } else {
            aliases.push(format!("MIOS_A2A_{}", name));
        }
    } else if dotted_path == "agents.hermes.endpoint" {
        aliases.push("MIOS_HERMES_WORKER_ENDPOINT".into());
    } else if dotted_path.starts_with("ai.")
        && !dotted_path.starts_with("ai.vllm.")
        && !dotted_path.starts_with("ai.sglang.")
    {
        let suffix = dotted_path["ai.".len()..]
            .to_uppercase()
            .replace(['.', '-', '/'], "_");
        if suffix == "API_KEY" || suffix == "KEY" {
            aliases.push("MIOS_AI_KEY".into());
        } else if suffix == "EMBED_MODEL" {
            aliases.push("MIOS_VERB_EMBED_MODEL".into());
        } else if suffix == "STACK_MODEL" {
            aliases.push("MIOS_STACK_MODEL".into());
        } else if suffix == "CHAT_VISION_MODEL" {
            aliases.push("MIOS_AGENT_PIPE_VISION_MODEL".into());
        } else if suffix == "AGENT_VENV" {
            aliases.push("MIOS_HERMES_VENV".into());
        } else if suffix == "AGENT_INSTALL_DIR" {
            aliases.push("MIOS_HERMES_DIR".into());
        } else if suffix == "MICRO_MODEL" {
            aliases.push("MIOS_MICRO_MODEL".into());
        } else if suffix == "MICRO_ENDPOINT" {
            aliases.push("MIOS_MICRO_ENDPOINT".into());
        } else if suffix == "OPENCODE_GATEWAY_WORKDIR" {
            aliases.push("MIOS_OPENCODE_WORKDIR".into());
        } else if suffix == "OPENCODE_GATEWAY_TIMEOUT_S" {
            aliases.push("MIOS_OPENCODE_TIMEOUT_S".into());
        } else if suffix.starts_with("OPENCODE_") {
            aliases.push(format!("MIOS_{}", suffix));
        } else if suffix == "ENDPOINT" || suffix == "MODEL" {
            aliases.push(format!("MIOS_AI_{}", suffix));
            aliases.push(format!("MIOS_{}", suffix));
        } else if matches!(
            suffix.as_str(),
            "SYSTEM_PROMPT_FILE"
                | "TOKENIZER_BACKEND"
                | "TOKENIZER_ENCODING"
                | "TOKENIZER_CACHE_DIR"
                | "TOKENIZER_PATH"
                | "HERMES_AGENT_REPO"
                | "HERMES_AGENT_REF"
                | "HERMES_BACKEND_URL"
                | "MCP_REGISTRY"
        ) {
            aliases.push(format!("MIOS_{}", suffix));
        }
    } else if let Some(rest) = dotted_path.strip_prefix("build.") {
        let name = rest.to_uppercase().replace(['.', '-', '/'], "_");
        // AI_RAM_FLOOR_GB is deliberately absent: [ai].ram_floor_gb owns that
        // name already, with a different value (task_964).
        if matches!(name.as_str(), "LOCAL_TAG" | "RECHUNK_MAX_LAYERS") {
            aliases.push(format!("MIOS_{}", name));
        } else {
            aliases.push(format!("MIOS_BUILD_{}", name));
        }
    } else if let Some(rest) = dotted_path.strip_prefix("code_mode.") {
        let name = rest.to_uppercase();
        aliases.push(format!("MIOS_CODEMODE_{}", name));
    } else if let Some(rest) = dotted_path.strip_prefix("colors.") {
        let name = rest.to_uppercase();
        if name.starts_with("ANSI_") {
            aliases.push(format!("MIOS_{}", name));
        } else {
            aliases.push(format!("MIOS_COLOR_{}", name));
        }
    } else if let Some(rest) = dotted_path.strip_prefix("frontier.") {
        let name = rest.to_uppercase();
        if name == "STREAM_TO_REASONING" {
            aliases.push("MIOS_A2O_STREAM_REASONING".into());
        } else {
            aliases.push(format!("MIOS_A2O_{}", name));
        }
    } else if let Some(rest) = dotted_path.strip_prefix("paths.") {
        let name = rest.to_uppercase();
        if name == "MIOS_TOML" {
            aliases.push("MIOS_TOML".into());
        } else if name == "WSL_FIRSTBOOT_DONE" {
            aliases.push("MIOS_WSLBOOT_DONE".into());
        } else if name == "LAUNCHER_SOCKET" || name == "MIOS_LAUNCHER_SOCKET" {
            aliases.push("MIOS_LAUNCHER_SOCKET".into());
        } else {
            aliases.push(format!("MIOS_{}", name));
        }
    } else if dotted_path.starts_with("pgvector.") || dotted_path.starts_with("pg.") {
        let prefix_len = if dotted_path.starts_with("pgvector.") {
            "pgvector.".len()
        } else {
            "pg.".len()
        };
        let name = dotted_path[prefix_len..].to_uppercase();
        if name == "DB_BACKEND" {
            aliases.push("MIOS_DB_BACKEND".into());
        } else if name == "RLS_ENABLE" {
            aliases.push("MIOS_DB_RLS_ENABLE".into());
        } else {
            aliases.push(format!("MIOS_PG_{}", name));
            aliases.push(format!("MIOS_PGVECTOR_{}", name));
        }
    } else if dotted_path.starts_with("routing.") && !dotted_path.starts_with("routing.domains.") {
        let name = dotted_path["routing.".len()..]
            .to_uppercase()
            .replace(['.', '-', '/'], "_");
        aliases.push(format!("MIOS_{}", name));
    } else if dotted_path == "polish.timeout_seconds" {
        aliases.push("MIOS_POLISH_TIMEOUT_S".into());
    } else if dotted_path == "refine.timeout_seconds" {
        aliases.push("MIOS_REFINE_TIMEOUT_S".into());
    } else if dotted_path == "security.fapolicyd_observe.enable" {
        aliases.push("MIOS_FAPOLICYD_OBSERVE_ENABLE".into());
    } else if dotted_path == "uki.verity_uki_build" {
        aliases.push("MIOS_UKI_VERITY_BUILD".into());
    } else if let Some(rest) = dotted_path.strip_prefix("verity.") {
        let name = rest.to_uppercase();
        aliases.push(format!("MIOS_{}", name));
    } else if dotted_path == "user.hostname" {
        aliases.push("MIOS_HOSTNAME".into());
    } else if dotted_path == "user.name" {
        aliases.push("MIOS_USER_FULLNAME".into());
    } else if dotted_path == "flatpaks.install" {
        aliases.push("MIOS_FLATPAKS".into());
    } else if let Some(rest) = dotted_path.strip_prefix("llamacpp.") {
        let name = rest.to_uppercase();
        if name == "CPU_NODE_THREADS" {
            aliases.push("MIOS_CPU_NODE_THREADS".into());
        } else {
            aliases.push(format!("MIOS_LLAMACPP_{}", name));
        }
    } else if dotted_path == "meta.mios_version" {
        aliases.push("MIOS_VERSION".into());
    } else if let Some(rest) = dotted_path.strip_prefix("network.quadlet.") {
        let name = rest.to_uppercase();
        if name == "CORE_GATEWAY" {
            aliases.push("MIOS_CORE_NET_GATEWAY".into());
        } else if name == "CORE_SUBNET" {
            aliases.push("MIOS_CORE_NET_SUBNET".into());
        } else if name == "NETWORK" {
            aliases.push("MIOS_QUADLET_NETWORK".into());
        } else if name == "SUBNET" {
            aliases.push("MIOS_QUADLET_SUBNET".into());
        }
    } else if dotted_path == "fs_watcher.watch_dirs" {
        aliases.push("MIOS_FS_WATCHER_DIRS".into());
    } else if dotted_path.starts_with("ports.categories.") {
        // Allocation schema, not a port
    } else if let Some(rest) = dotted_path.strip_prefix("ports.") {
        let name = rest.to_uppercase().replace(['.', '-', '/'], "_");
        let canon = if name == "GUACAMOLE_WEB" {
            "GUACAMOLE"
        } else {
            &name
        };
        aliases.push(format!("MIOS_PORT_{}", canon));
        aliases.push(format!("MIOS_{}_PORT", canon));
    } else if let Some(rest) = dotted_path.strip_prefix("image.sidecars.") {
        let name = rest.to_uppercase().replace(['.', '-', '/'], "_");
        if name.ends_with("_VERSION") {
            let base = &name[..name.len() - "_VERSION".len()];
            aliases.push(format!("MIOS_{}_VERSION", base));
        } else {
            aliases.push(format!("MIOS_{}_IMAGE", name));
            aliases.push(format!("MIOS_{}_VERSION", name));
        }
    } else if let Some(rest) = dotted_path.strip_prefix("services.") {
        let parts: Vec<&str> = rest.split('.').collect();
        if parts.len() >= 2 {
            let service = parts[0].to_uppercase().replace('-', "_");
            let key = parts[1..].join("_").to_uppercase().replace('-', "_");
            if service == "WEBTOOLS" {
                if matches!(key.as_str(), "USER" | "UID" | "GID") {
                    aliases.push(format!("MIOS_WEBTOOLS_{}", key));
                } else if key == "CDP_URL" {
                    aliases.push("MIOS_CRAWL_CDP_URL".into());
                } else if key == "CAMOUFOX" {
                    aliases.push("MIOS_CRAWL_CAMOUFOX".into());
                } else if key == "MIN_CHARS" {
                    aliases.push("MIOS_CRAWL_MIN_CHARS".into());
                } else if key.starts_with("FIRECRAWL_") || key.starts_with("CRAWL4AI_") {
                    aliases.push(format!("MIOS_{}", key));
                }
            } else {
                aliases.push(format!("MIOS_{}_{}", service, key));
            }
        }
    } else if let Some(rest) = dotted_path.strip_prefix("storage.cephfs.") {
        let key = rest.to_uppercase().replace(['.', '-', '/'], "_");
        if key == "XDG_CACHE_HOME_OVERRIDE" {
            aliases.push("MIOS_XDG_CACHE_LOCAL_PATH".into());
        } else {
            aliases.push(format!("MIOS_CEPHFS_{}", key));
        }
    } else if let Some(rest) = dotted_path.strip_prefix("wsl2.") {
        let key = rest.to_uppercase().replace(['.', '-', '/'], "_");
        if key == "DESKTOP_COMPAT_GDK_BACKEND" {
            aliases.push("MIOS_WSLG_GDK_BACKEND".into());
        } else if key == "DESKTOP_COMPAT_MOZ_WAYLAND" {
            aliases.push("MIOS_WSLG_MOZ_WAYLAND".into());
        } else if key == "DESKTOP_COMPAT_QT_PLATFORM" {
            aliases.push("MIOS_WSLG_QT_PLATFORM".into());
        } else if key == "DEV_VM_QUADLET_NETWORK_MODE" {
            aliases.push("MIOS_QUADLET_DEV_NETWORK_MODE".into());
        } else {
            aliases.push(format!("MIOS_WSL2_{}", key));
        }
    } else if dotted_path.starts_with("converge.") {
        // No legacy alias
    } else if dotted_path.starts_with("image.") && !dotted_path.starts_with("image.sidecars.") {
        let key = dotted_path["image.".len()..]
            .to_uppercase()
            .replace(['.', '-', '/'], "_");
        if key == "BRANCH" {
            aliases.push("MIOS_BRANCH".into());
        } else if key == "BASE" {
            aliases.push("MIOS_BASE_IMAGE".into());
        } else if key == "BIB" {
            aliases.push("MIOS_BIB_IMAGE".into());
        } else if key == "LOCAL_TAG" {
            aliases.push("MIOS_LOCAL_TAG".into());
        } else if matches!(key.as_str(), "REF" | "NAME" | "TAG") {
            aliases.push(format!("MIOS_IMAGE_{}", key));
        }
    } else if let Some(rest) = dotted_path.strip_prefix("versions.") {
        // Mirrors mios_toml.py: the legacy singular beside the plural. Absent
        // here, Quadlets floating `${MIOS_VERSION_*}` resolved to nothing in
        // every Rust consumer (task_994).
        let key = rest.to_uppercase().replace(['.', '-'], "_");
        aliases.push(format!("MIOS_VERSION_{}", key));
    } else if let Some(rest) = dotted_path.strip_prefix("desktop.") {
        let key = rest.to_uppercase().replace(['.', '-', '/'], "_");
        if key == "COLOR_SCHEME" {
            aliases.push("MIOS_COLOR_SCHEME".into());
        } else if key == "FLATPAKS" {
            aliases.push("MIOS_FLATPAKS".into());
        } else if key == "SESSION" {
            aliases.push(format!("MIOS_DESKTOP_{}", key));
        }
    } else if let Some(rest) = dotted_path.strip_prefix("bootstrap.dev_vm.") {
        let key = rest.to_uppercase().replace(['.', '-', '/'], "_");
        if key == "MACHINE_NAME" {
            aliases.push("MIOS_BUILDER_DISTRO".into());
        } else if key == "WSL_DISTRO" {
            aliases.push("MIOS_WSL_DISTRO".into());
        } else if key == "DISK_SIZE_GB" {
            aliases.push("MIOS_DEV_VM_DISK_GB".into());
        } else if key == "GPU_PASSTHROUGH" {
            aliases.push("MIOS_DEV_VM_GPU".into());
        } else if key == "HOST_RESERVE_CPU_PCT" {
            aliases.push("MIOS_DEV_VM_CPU_RESERVE_PCT".into());
        } else if key == "HOST_RESERVE_CPU_MIN" {
            aliases.push("MIOS_DEV_VM_CPU_RESERVE_MIN".into());
        } else if key == "HOST_RESERVE_MEMORY_PCT" {
            aliases.push("MIOS_DEV_VM_MEMORY_RESERVE_PCT".into());
        } else if key == "HOST_RESERVE_MEMORY_GB" {
            aliases.push("MIOS_DEV_VM_MEMORY_RESERVE_GB".into());
        } else if key == "HOST_RESERVE_DISK_GB" {
            aliases.push("MIOS_DEV_VM_DISK_RESERVE_GB".into());
        } else if matches!(key.as_str(), "BASE_IMAGE" | "CPUS" | "MEMORY_MB") {
            aliases.push(format!("MIOS_DEV_VM_{}", key));
        }
    } else if let Some(rest) = dotted_path.strip_prefix("bootstrap.host_storage.") {
        let key = rest.to_uppercase().replace(['.', '-', '/'], "_");
        if key == "SHRINK_MB" {
            aliases.push("MIOS_DATA_DISK_MB".into());
        } else if key == "DRIVE_LETTER" {
            aliases.push("MIOS_DATA_DISK_LETTER".into());
        }
    } else if dotted_path.starts_with("bootstrap.")
        && !dotted_path.starts_with("bootstrap.dev_vm.")
        && !dotted_path.starts_with("bootstrap.host_storage.")
    {
        let key = dotted_path["bootstrap.".len()..]
            .to_uppercase()
            .replace(['.', '-', '/'], "_");
        if key == "MIOS_REPO" {
            aliases.push("MIOS_REPO_URL".into());
        } else if key == "BOOTSTRAP_REPO" {
            aliases.push("MIOS_BOOTSTRAP_REPO_URL".into());
        } else if key == "MODE" {
            aliases.push(format!("MIOS_BOOTSTRAP_{}", key));
        }
    } else if let Some(rest) = dotted_path.strip_prefix("reliability.") {
        let key = rest.to_uppercase().replace(['.', '-', '/'], "_");
        aliases.push(format!("MIOS_RELIABILITY_{}", key));
    } else if let Some(rest) = dotted_path.strip_prefix("routing.") {
        let key = rest.to_uppercase().replace(['.', '-', '/'], "_");
        aliases.push(format!("MIOS_ROUTING_{}", key));
        if key.starts_with("LAUNCH_") {
            aliases.push(format!("MIOS_{}", key));
        }
    } else if dotted_path.starts_with("mios-find.") || dotted_path.starts_with("mios_find.") {
        let prefix_len = if dotted_path.starts_with("mios-find.") {
            "mios-find.".len()
        } else {
            "mios_find.".len()
        };
        let key = dotted_path[prefix_len..]
            .to_uppercase()
            .replace(['.', '-', '/'], "_");
        aliases.push(format!("MIOS_FIND_{}", key));
    } else if let Some(rest) = dotted_path.strip_prefix("code_server.") {
        aliases.push(format!("MIOS_CODE_SERVER_{}", py_key(rest)));
    } else if let Some(rest) = dotted_path
        .strip_prefix("postgres.")
        .or_else(|| dotted_path.strip_prefix("db."))
    {
        let key = py_key(rest);
        for family in ["DB", "POSTGRES", "PG", "PGVECTOR"] {
            aliases.push(format!("MIOS_{}_{}", family, key));
        }
    } else if let Some(rest) = dotted_path.strip_prefix("mios.") {
        aliases.push(format!("MIOS_{}", py_key(rest)));
    } else if let Some(rest) = dotted_path
        .strip_prefix("offline.backup_")
        .or_else(|| dotted_path.strip_prefix("offline.backup."))
    {
        let key = py_key(rest.trim_start_matches(['_', '.']));
        aliases.push(format!("MIOS_PG_BACKUP_{}", key));
        aliases.push(format!("MIOS_OFFLINE_BACKUP_{}", key));
    } else if let Some(rest) = dotted_path.strip_prefix("units.") {
        // Unit names carry '-' and '.', neither legal in a shell identifier.
        let tail: String = rest
            .to_uppercase()
            .chars()
            .map(|c| if c.is_ascii_alphanumeric() { c } else { '_' })
            .collect();
        aliases.push(format!("MIOS_UNIT_{}", tail));
    } else if let Some(rest) = dotted_path.strip_prefix("urls.") {
        // [urls].forge -> MIOS_FORGE_URL; non_addressable lists port KEYS, not a URL.
        let name = rest.to_uppercase();
        if name == "LOCAL_FORGE_REPO" {
            aliases.push("MIOS_LOCAL_FORGE_REPO".into());
        } else if name != "NON_ADDRESSABLE" {
            aliases.push(format!("MIOS_{}_URL", name));
        }
    }

    aliases
}

/// mios_toml.py's key transform for these families: upper-case, '.' and '-' to '_'.
fn py_key(rest: &str) -> String {
    rest.to_uppercase().replace(['.', '-'], "_")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn canonical_names_follow_capabilities_without_reader_abbreviations() {
        for (key, expected) in [
            ("identity.username", "MIOS_IDENTITY_USERNAME"),
            ("ports.agent_pipe", "MIOS_PORTS_AGENT_PIPE"),
            ("converge.timeout_s", "MIOS_CONVERGE_TIMEOUT_S"),
            ("units.worker@.service", "MIOS_UNITS_WORKER__SERVICE"),
        ] {
            assert_eq!(canonical_name(key), expected);
        }
    }

    #[test]
    fn registry_rejects_lexical_collisions_and_does_not_expose_values() {
        let good: Value = toml::from_str("[identity]\nusername='secret-canary'\n").unwrap();
        let output = serde_json::to_string(&registry(&good).unwrap()).unwrap();
        assert!(output.contains("MIOS_IDENTITY_USERNAME"));
        assert!(!output.contains("secret-canary"));
        let bad: Value = toml::from_str("[a]\nx_y=1\n[a.x]\ny=2\n").unwrap();
        let error = registry(&bad).unwrap_err();
        assert!(error.contains("MIOS_A_X_Y") && error.contains("a.x_y") && error.contains("a.x.y"));
    }

    #[test]
    fn ambiguous_aliases_and_tag_semantics_are_not_silently_folded() {
        let input: Value = toml::from_str("[identity]\nfullname='one'\n[user]\nname='two'\n[image.sidecars]\nk3s='docker.io/rancher/k3s:v1'\n").unwrap();
        let registry = registry(&input).unwrap();
        assert_eq!(
            registry.ambiguous_aliases["MIOS_USER_FULLNAME"],
            ["identity.fullname", "user.name"]
        );
        let image = registry
            .entries
            .iter()
            .find(|e| e.key == "image.sidecars.k3s")
            .unwrap();
        assert!(image
            .aliases
            .iter()
            .any(|a| a.name == "MIOS_K3S_VERSION" && a.semantics == "image-tag"));
    }

    #[test]
    fn old_inputs_survive_and_canonical_inputs_win_without_secret_diagnostics() {
        let original: Value =
            toml::from_str("[ports]\nagent_pipe=8700\n[identity]\nusername='mios'\n").unwrap();
        let mut input = original.clone();
        overlay_inputs(&mut input, |key| {
            (key == "MIOS_PORT_AGENT_PIPE").then(|| "9100".into())
        })
        .unwrap();
        let exports = crate::emit::build_exports_map(&input, 0);
        assert_eq!(exports["MIOS_PORTS_AGENT_PIPE"], "9100");
        assert_eq!(exports["MIOS_AGENT_PIPE_PORT"], "9100");
        let env = BTreeMap::from([
            ("MIOS_USER", "secret-one"),
            ("MIOS_DEFAULT_USER", "secret-two"),
        ]);
        let mut conflict = original.clone();
        let error = overlay_inputs(&mut conflict, |key| env.get(key).map(|v| (*v).to_string()))
            .unwrap_err();
        assert!(error.contains("MIOS_USER") && error.contains("MIOS_DEFAULT_USER"));
        assert!(!error.contains("secret-one") && !error.contains("secret-two"));
        assert_eq!(
            conflict, original,
            "failed validation must not partially overlay input"
        );
        overlay_inputs(&mut conflict, |key| {
            if key == "MIOS_IDENTITY_USERNAME" {
                Some("canonical".into())
            } else {
                env.get(key).map(|v| (*v).to_string())
            }
        })
        .unwrap();
        assert_eq!(
            crate::emit::build_exports_map(&conflict, 0)["MIOS_IDENTITY_USERNAME"],
            "canonical"
        );
    }

    #[test]
    fn test_identity_aliases() {
        assert_eq!(
            get_aliases("identity.username"),
            vec!["MIOS_USER", "MIOS_DEFAULT_USER"]
        );
        assert_eq!(
            get_aliases("identity.hostname"),
            vec!["MIOS_HOSTNAME", "MIOS_DEFAULT_HOST"]
        );
    }

    #[test]
    fn test_ports_dual_alias() {
        assert_eq!(
            get_aliases("ports.vllm"),
            vec!["MIOS_PORT_VLLM", "MIOS_VLLM_PORT"]
        );
        assert_eq!(
            get_aliases("ports.guacamole_web"),
            vec!["MIOS_PORT_GUACAMOLE", "MIOS_GUACAMOLE_PORT"]
        );
        assert!(get_aliases("ports.categories.agent.base").is_empty());
        assert!(get_aliases("ports.categories.edge.members").is_empty());
    }

    #[test]
    fn test_families_ported_from_mios_toml_py() {
        assert_eq!(
            get_aliases("urls.code_server"),
            vec!["MIOS_CODE_SERVER_URL"]
        );
        assert_eq!(
            get_aliases("urls.local_forge_repo"),
            vec!["MIOS_LOCAL_FORGE_REPO"]
        );
        assert!(get_aliases("urls.non_addressable").is_empty());
        assert_eq!(
            get_aliases("units.var-lib-nfs.mount"),
            vec!["MIOS_UNIT_VAR_LIB_NFS_MOUNT"]
        );
        assert_eq!(
            get_aliases("offline.backup_keep"),
            vec!["MIOS_PG_BACKUP_KEEP", "MIOS_OFFLINE_BACKUP_KEEP"]
        );
        assert_eq!(
            get_aliases("db.host"),
            vec![
                "MIOS_DB_HOST",
                "MIOS_POSTGRES_HOST",
                "MIOS_PG_HOST",
                "MIOS_PGVECTOR_HOST"
            ]
        );
        assert_eq!(
            get_aliases("code_server.bind"),
            vec!["MIOS_CODE_SERVER_BIND"]
        );
        assert_eq!(get_aliases("mios.repo-root"), vec!["MIOS_REPO_ROOT"]);
    }

    #[test]
    fn test_image_sidecars_aliases() {
        assert_eq!(
            get_aliases("image.sidecars.vllm"),
            vec!["MIOS_VLLM_IMAGE", "MIOS_VLLM_VERSION"]
        );
        assert_eq!(
            get_aliases("image.sidecars.k3s_version"),
            vec!["MIOS_K3S_VERSION"]
        );
    }
}
