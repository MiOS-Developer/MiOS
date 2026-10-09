// AI-hint: High-performance binary CLI dispatcher routing 33 known verbs with <5ms latency.
// AI-related: usr/bin/mios, usr/libexec/mios/

use std::path::{Path, PathBuf};
use std::process::Command;

pub static KNOWN_VERBS: &[(&str, &[&str])] = &[
    ("build", &["/usr/libexec/mios/mios-build-driver"]),
    ("dash", &["/usr/libexec/mios/mios-dashboard", "--dash", "--once"]),
    ("mini", &["/usr/libexec/mios/mios-dashboard", "--mini", "--once"]),
    ("mon", &["/usr/libexec/mios/mios-dashboard", "--monitor"]),
    (
        "monitor",
        &["/usr/libexec/mios/mios-dashboard", "--monitor"],
    ),
    ("config", &["/usr/libexec/mios/mios-configurator-launch"]),
    (
        "code",
        &["xdg-open", "http://localhost:${MIOS_PORT_CODE_SERVER}/"],
    ),
    (
        "ai",
        &["xdg-open", "http://localhost:${MIOS_PORT_OPEN_WEBUI}/"],
    ),
    ("xbox", &["/usr/libexec/mios/xbox-repair.sh"]),
    ("virt", &["/usr/libexec/mios/virt-apply.sh"]),
    ("vfio", &["/usr/libexec/mios/vfio-config.sh"]),
    ("vfio-check", &["/usr/libexec/mios/vfio-check.sh"]),
    ("vfio-toggle", &["/usr/libexec/mios/vfio-toggle.sh"]),
    ("tune", &["/usr/libexec/mios/tune-performance.sh"]),
    ("summary", &["/usr/libexec/mios/system-summary.sh"]),
    ("profile", &["/usr/libexec/mios/system-profile.sh"]),
    ("assess", &["/usr/libexec/mios/capability-audit.sh"]),
    ("theme", &["/usr/libexec/mios/mios-sync-theme"]),
    ("dotfiles", &["/usr/libexec/mios/mios-dotfiles"]),
    ("new", &["/usr/libexec/mios/mios-new"]),
    ("iommu", &["/usr/libexec/mios/hardware-iommu.sh"]),
    (
        "iommu-groups",
        &["/usr/libexec/mios/hardware-iommu-groups.sh"],
    ),
    ("env", &["/usr/libexec/mios/system-env.sh"]),
    ("sync-env", &["/usr/libexec/mios/system-sync-env.sh"]),
    ("blade", &["/usr/libexec/mios/mios-blade"]),
    ("flatpaks", &["/usr/libexec/mios/flatpaks-manage.sh"]),
    ("user", &["/usr/libexec/mios/user-setup.sh"]),
    ("flight", &["/usr/libexec/mios/flight-control.sh"]),
    ("models", &["/usr/libexec/mios/mios-models"]),
    ("update", &["/usr/bin/mios-update"]),
    ("check", &["/usr/libexec/mios/miosd", "check"]),
    ("status", &["/usr/libexec/mios/mios-system-status"]),
    ("logs", &["journalctl", "-u", "miosd.service", "-f"]),
    ("backup", &["/usr/libexec/mios/mios-backup"]),
    ("secret", &["/usr/libexec/mios/miosd", "secret"]),
];

pub struct CliDispatcher;

impl CliDispatcher {
    pub fn find_verb(verb: &str) -> Option<&'static [&'static str]> {
        for (v, cmd) in KNOWN_VERBS {
            if *v == verb {
                return Some(cmd);
            }
        }
        None
    }

    pub fn resolve_target(target: &str) -> PathBuf {
        let path = Path::new(target);
        if path.is_file() {
            return path.to_path_buf();
        }

        // Try resolving relative to MIOS_ROOT
        if let Ok(root) = std::env::var("MIOS_ROOT").or_else(|_| std::env::var("MIOS_DRIFT_ROOT")) {
            let candidate = Path::new(&root).join(target.trim_start_matches('/'));
            if candidate.is_file() {
                return candidate;
            }
        }

        // Return original
        path.to_path_buf()
    }

    pub fn execute_verb(cmd_spec: &[&str], extra_args: &[String]) -> i32 {
        if cmd_spec.is_empty() {
            return 1;
        }
        let target_raw = cmd_spec[0];
        let resolved = Self::resolve_target(target_raw);
        // ${MIOS_*} in a verb's arguments resolve from the SSOT at dispatch time.
        let spec_args: Vec<String> = match cmd_spec[1..]
            .iter()
            .map(|a| mios_resolver::runtime::expand_refs(a))
            .collect()
        {
            Ok(args) => args,
            Err(e) => {
                eprintln!("mios: cannot resolve '{}': {}", target_raw, e);
                return 1;
            }
        };

        let mut cmd = Command::new(&resolved);
        for arg in &spec_args {
            cmd.arg(arg);
        }
        for arg in extra_args {
            cmd.arg(arg);
        }

        match cmd.status() {
            Ok(status) => status.code().unwrap_or(1),
            Err(e) => {
                // If direct execution failed and target wasn't found, try running as PATH command
                if e.kind() == std::io::ErrorKind::NotFound {
                    let mut fallback = Command::new(target_raw);
                    for arg in &spec_args {
                        fallback.arg(arg);
                    }
                    for arg in extra_args {
                        fallback.arg(arg);
                    }
                    if let Ok(st) = fallback.status() {
                        return st.code().unwrap_or(1);
                    }
                }
                eprintln!("mios: failed to execute '{}': {}", target_raw, e);
                127
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_known_verbs_table_count() {
        assert!(
            KNOWN_VERBS.len() >= 33,
            "Expected at least 33 known verbs, got {}",
            KNOWN_VERBS.len()
        );
        assert!(CliDispatcher::find_verb("build").is_some());
        assert!(CliDispatcher::find_verb("dash").is_some());
        assert!(CliDispatcher::find_verb("check").is_some());
        assert!(CliDispatcher::find_verb("status").is_some());
        assert!(CliDispatcher::find_verb("logs").is_some());
    }

    #[test]
    fn test_resolve_target() {
        let p = CliDispatcher::resolve_target("/usr/libexec/mios/nonexistent-xyz");
        assert_eq!(p, PathBuf::from("/usr/libexec/mios/nonexistent-xyz"));
    }
}
