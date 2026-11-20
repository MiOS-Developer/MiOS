// AI-hint: Fan-controller selection for ventulusd: matches the machine's hwmon chip names (/sys/class/hwmon/*/name) against [[controllers]] in mios-fanctl.toml and either names the profile to drive, keeps the fans on firmware control, or reports that no supported controller exists. Run once per start as the unit's ExecCondition, so a missing controller is one log line and a skipped unit, never a per-tick error.
// AI-related: /usr/share/mios/fan-control/mios-fanctl.toml, /usr/lib/systemd/system/ventulusd.service.d/10-mios.conf
// AI-functions: hwmon_names, select, run, write_env

use crate::config::{Config, Controller};
use std::path::Path;

pub const HWMON_ROOT: &str = "/sys/class/hwmon";
/// Read by ventulusd.service (EnvironmentFile=-) ahead of the operator's
/// /etc/mios/fan-control.env, so an explicit operator profile still wins.
pub const ENV_OUT: &str = "/run/mios/fan-control/selected.env";

#[derive(Debug, PartialEq)]
pub enum Selection {
    /// Drive the fans with this profile.
    Drive { hwmon: String, profile: String },
    /// A known controller whose fans MiOS deliberately leaves on firmware.
    Firmware { hwmon: String, note: String },
    /// Nothing in [[controllers]] is present on this machine.
    Unsupported { found: Vec<String> },
}

/// Chip names of every hwmon device under `root`, sorted and de-duplicated.
pub fn hwmon_names(root: &Path) -> Vec<String> {
    let mut names: Vec<String> = std::fs::read_dir(root)
        .into_iter()
        .flatten()
        .flatten()
        .filter_map(|e| std::fs::read_to_string(e.path().join("name")).ok())
        .map(|n| n.trim().to_string())
        .filter(|n| !n.is_empty())
        .collect();
    names.sort();
    names.dedup();
    names
}

pub fn validate(controllers: &[Controller]) -> Result<(), String> {
    for c in controllers {
        if c.hwmon.trim().is_empty() {
            return Err("[[controllers]] entry without hwmon".into());
        }
        if c.profile.is_empty() == !c.firmware {
            return Err(format!(
                "[[controllers]] {}: set exactly one of profile or firmware = true",
                c.hwmon
            ));
        }
    }
    Ok(())
}

/// The first configured controller present on the machine wins.
pub fn select(cfg: &Config, names: &[String]) -> Result<Selection, String> {
    validate(&cfg.controllers)?;
    for c in &cfg.controllers {
        if names.iter().any(|n| n == &c.hwmon) {
            return Ok(if c.firmware {
                Selection::Firmware {
                    hwmon: c.hwmon.clone(),
                    note: c.note.clone(),
                }
            } else {
                Selection::Drive {
                    hwmon: c.hwmon.clone(),
                    profile: c.profile.clone(),
                }
            });
        }
    }
    Ok(Selection::Unsupported {
        found: names.to_vec(),
    })
}

fn write_env(out: &Path, profile: &str) -> Result<(), String> {
    if let Some(dir) = out.parent() {
        std::fs::create_dir_all(dir).map_err(|e| format!("{}: {e}", dir.display()))?;
    }
    let tmp = out.with_extension("env.tmp");
    std::fs::write(&tmp, format!("OPENFAN_PROFILE={profile}\n"))
        .map_err(|e| format!("{}: {e}", tmp.display()))?;
    std::fs::rename(&tmp, out).map_err(|e| format!("{}: {e}", out.display()))
}

/// ExecCondition contract: 0 starts the daemon; 1 skips the unit cleanly
/// (one journal line, no restart loop); 255 fails it (broken config).
pub fn run(cfg: &Config, hwmon_root: &Path, env_out: &Path) -> i32 {
    let names = hwmon_names(hwmon_root);
    let selection = match select(cfg, &names) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("mios-fanctl: {e}");
            return 255;
        }
    };
    if !matches!(selection, Selection::Drive { .. }) {
        let _ = std::fs::remove_file(env_out);
    }
    match selection {
        Selection::Drive { hwmon, profile } => match write_env(env_out, &profile) {
            Ok(()) => {
                println!("fan controller {hwmon}: driving with profile {profile}");
                0
            }
            Err(e) => {
                eprintln!("mios-fanctl: cannot record the selected profile: {e}");
                255
            }
        },
        Selection::Firmware { hwmon, note } => {
            eprintln!(
                "fan controller {hwmon}: left on firmware control{}",
                if note.is_empty() {
                    String::new()
                } else {
                    format!(" ({note})")
                }
            );
            1
        }
        Selection::Unsupported { found } => {
            eprintln!(
                "no supported fan controller among hwmon [{}]; fans stay on firmware control (add a [[controllers]] entry to mios-fanctl.toml)",
                found.join(", ")
            );
            1
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn cfg() -> Config {
        crate::config::load_from(&[concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../../usr/share/mios/fan-control/mios-fanctl.toml"
        )
        .to_string()])
        .unwrap()
    }

    fn hwmon(names: &[&str]) -> tempfile_dir::Dir {
        let d = tempfile_dir::Dir::new("hwmon");
        for (i, n) in names.iter().enumerate() {
            let h = d.0.join(format!("hwmon{i}"));
            std::fs::create_dir_all(&h).unwrap();
            std::fs::write(h.join("name"), format!("{n}\n")).unwrap();
        }
        d
    }

    /// Minimal self-cleaning temp dir; the crate carries no tempfile dependency.
    mod tempfile_dir {
        pub struct Dir(pub std::path::PathBuf);
        impl Dir {
            pub fn new(tag: &str) -> Self {
                use std::sync::atomic::{AtomicUsize, Ordering};
                static N: AtomicUsize = AtomicUsize::new(0);
                let p = std::env::temp_dir().join(format!(
                    "mios-fanctl-{tag}-{}-{}",
                    std::process::id(),
                    N.fetch_add(1, Ordering::SeqCst)
                ));
                std::fs::create_dir_all(&p).unwrap();
                Dir(p)
            }
        }
        impl Drop for Dir {
            fn drop(&mut self) {
                let _ = std::fs::remove_dir_all(&self.0);
            }
        }
    }

    #[test]
    fn vendor_config_drives_the_nct6799_board() {
        let h = hwmon(&["acpitz", "k10temp", "nct6799", "nvme"]);
        let out = h.0.join("run/selected.env");
        assert_eq!(run(&cfg(), &h.0, &out), 0);
        assert_eq!(
            std::fs::read_to_string(&out).unwrap(),
            "OPENFAN_PROFILE=mios-default\n"
        );
    }

    #[test]
    fn vendor_config_knows_the_ps5_fan_and_leaves_it_on_firmware() {
        let names = hwmon_names(&hwmon(&["k10temp", "ps5_fan"]).0);
        match select(&cfg(), &names).unwrap() {
            Selection::Firmware { hwmon, note } => {
                assert_eq!(hwmon, "ps5_fan");
                assert!(!note.is_empty());
            }
            other => panic!("ps5_fan must be a known controller, got {other:?}"),
        }
        let h = hwmon(&["ps5_fan"]);
        let out = h.0.join("selected.env");
        std::fs::write(&out, "OPENFAN_PROFILE=stale\n").unwrap();
        assert_eq!(run(&cfg(), &h.0, &out), 1);
        assert!(!out.exists(), "a stale selection must not survive");
    }

    #[test]
    fn missing_controller_skips_without_writing() {
        let h = hwmon(&["acpitz", "amdgpu"]);
        let out = h.0.join("selected.env");
        assert_eq!(
            select(&cfg(), &hwmon_names(&h.0)).unwrap(),
            Selection::Unsupported {
                found: vec!["acpitz".into(), "amdgpu".into()]
            }
        );
        assert_eq!(run(&cfg(), &h.0, &out), 1);
        assert!(!out.exists());
        // No hwmon class at all (a guest) is the same clean skip.
        assert_eq!(run(&cfg(), &h.0.join("absent"), &out), 1);
    }

    #[test]
    fn first_listed_controller_wins_and_broken_entries_fail() {
        let mut c = cfg();
        let both = vec!["nct6799".to_string(), "ps5_fan".to_string()];
        assert!(matches!(
            select(&c, &both).unwrap(),
            Selection::Drive { ref hwmon, .. } if hwmon == "nct6799"
        ));
        c.controllers.push(Controller {
            hwmon: "it8689".into(),
            profile: "p".into(),
            firmware: true,
            note: String::new(),
        });
        assert!(select(&c, &both).is_err());
        let h = hwmon(&["it8689"]);
        assert_eq!(run(&c, &h.0, &h.0.join("e")), 255);
    }

    #[test]
    fn hwmon_names_are_trimmed_and_unique() {
        let h = hwmon(&["nct6799", "nct6799", "k10temp"]);
        assert_eq!(hwmon_names(&h.0), vec!["k10temp", "nct6799"]);
    }
}
