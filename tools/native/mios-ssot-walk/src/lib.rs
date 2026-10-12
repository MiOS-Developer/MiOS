// AI-hint: Single-sourced mios.toml SSOT-walk logic for resolver-twin and names-registry generators.

pub const EXCLUDED_SECTIONS: &[&str] = &[
    "containers",
    "verbs",
    "recipes",
    "packages",
    "dotfiles",
    "btop",
    "theme",
    "install_phases",
    "messages",
    // Read from the TOML directly by the suite registry reader; projecting
    // them emitted two dozen constants no consumer reads.
    "ci",
    "tests",
    // Rendered to unit files by mios-unit-gen, which parses mios.toml
    // itself and never reads globals; projecting them emitted 739
    // constants no consumer reads.
    "units",
    "generation",
];

pub const WALK_MOSTLY_DEAD: &[&str] =
    &["ai", "image", "bootstrap", "profile", "sandbox", "security"];

pub const WALK_EMIT_KEEP: &[&str] = &[
    "MIOS_AI_BAKE_MODELS",
    "MIOS_AI_DIR",
    "MIOS_AI_EMBED_MODEL",
    "MIOS_AI_ENDPOINT",
    "MIOS_AI_JOURNAL",
    "MIOS_AI_MCP_DIR",
    "MIOS_AI_MEMORY_DIR",
    "MIOS_AI_MODEL",
    "MIOS_AI_AGENT_MODEL",
    "MIOS_AI_MODELS_DIR",
    "MIOS_AI_RAM_FLOOR_GB",
    "MIOS_AI_SCRATCH_DIR",
    "MIOS_IMAGE_NAME",
    "MIOS_IMAGE_REF",
    "MIOS_IMAGE_TAG",
    "MIOS_BOOTSTRAP_MODE",
    "MIOS_SANDBOX_ENABLE",
    "MIOS_SECURITY_ALLOWLIST_HOSTS",
    "MIOS_SECURITY_PROBE_VERIFY_TLS",
    "MIOS_SECURITY_PROVENANCE_TAINT",
    "MIOS_SECURITY_SIGSTORE_COSIGN_VERSION",
    "MIOS_SECURITY_SIGSTORE_COSIGN_RELEASE_URL",
    "MIOS_HEADLESS",
    "MIOS_MONITOR_RUNNING",
    "MIOS_NO_COLOR",
    "MIOS_NO_MONITOR",
];

pub fn is_excluded_section(section: &str) -> bool {
    EXCLUDED_SECTIONS.contains(&section)
}

pub fn is_mostly_dead_section(section: &str) -> bool {
    WALK_MOSTLY_DEAD.contains(&section)
}

pub fn is_emit_keep_var(var_name: &str) -> bool {
    WALK_EMIT_KEEP.contains(&var_name)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashSet;
    use std::fs;

    #[test]
    fn test_constants_match_python_ssot() {
        let root = std::env::var("CARGO_MANIFEST_DIR")
            .map(|p| std::path::PathBuf::from(p).join("../../../usr/lib/mios/mios_toml.py"))
            .unwrap();
        if !root.exists() {
            return;
        }
        let content = fs::read_to_string(&root).expect("read mios_toml.py");

        let extract_set = |name: &str| -> HashSet<String> {
            let marker = format!("{} = ", name);
            let idx = content
                .find(&marker)
                .unwrap_or_else(|| panic!("find {}", name));
            let sub = &content[idx + marker.len()..];
            let end_idx = sub
                .find('}')
                .unwrap_or_else(|| panic!("end brace {}", name));
            let block = &sub[..end_idx];
            block
                .split(',')
                .map(|s| {
                    s.trim().trim_matches(|c| {
                        c == '{' || c == '"' || c == '\'' || c == '\n' || c == ' '
                    })
                })
                .filter(|s| !s.is_empty())
                .map(String::from)
                .collect()
        };

        let py_excluded = extract_set("EXCLUDED_SECTIONS");
        let rs_excluded: HashSet<String> =
            EXCLUDED_SECTIONS.iter().map(|s| s.to_string()).collect();
        assert_eq!(py_excluded, rs_excluded, "EXCLUDED_SECTIONS mismatch");

        let py_dead = extract_set("WALK_MOSTLY_DEAD");
        let rs_dead: HashSet<String> = WALK_MOSTLY_DEAD.iter().map(|s| s.to_string()).collect();
        assert_eq!(py_dead, rs_dead, "WALK_MOSTLY_DEAD mismatch");

        let py_keep = extract_set("WALK_EMIT_KEEP");
        let rs_keep: HashSet<String> = WALK_EMIT_KEEP.iter().map(|s| s.to_string()).collect();
        assert_eq!(py_keep, rs_keep, "WALK_EMIT_KEEP mismatch");
    }
}
