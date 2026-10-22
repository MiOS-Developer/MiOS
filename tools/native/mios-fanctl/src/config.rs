// AI-hint: Layered config for mios-fanctl: built-in defaults < vendor /usr/share/mios/fan-control/mios-fanctl.toml < admin /etc/mios/fan-control/mios-fanctl.toml < $MIOS_FANCTL_CONFIG.
// AI-related: /usr/share/mios/fan-control/mios-fanctl.toml, /usr/share/mios/mios.toml [hardware.fan_control]
// AI-functions: load, load_from, merge, effective_floor

use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

pub const VENDOR_PATH: &str = "/usr/share/mios/fan-control/mios-fanctl.toml";
pub const ADMIN_PATH: &str = "/etc/mios/fan-control/mios-fanctl.toml";
/// Hard floor for any override this client sends, whatever the config says.
pub const HARD_FLOOR_PCT: u8 = 30;

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct ClientCfg {
    pub socket: String,
    pub refresh_ms: u64,
    pub timeout_ms: u64,
    pub min_override_pct: u8,
    pub default_profile: String,
}

impl Default for ClientCfg {
    fn default() -> Self {
        Self {
            socket: "/run/ventulus/ventulus.sock".into(),
            refresh_ms: 1000,
            timeout_ms: 5000,
            min_override_pct: HARD_FLOOR_PCT,
            default_profile: "mios-default".into(),
        }
    }
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct Config {
    pub client: ClientCfg,
    /// Stable ventulusd header id -> role token (pump, cpu_fan, radiator_fan,
    /// chassis_fan, no_fan, unknown). Applied by `mios-fanctl apply-roles`.
    pub roles: BTreeMap<String, String>,
    #[serde(skip)]
    pub sources: Vec<String>,
}

impl Config {
    pub fn effective_floor(&self) -> u8 {
        self.client.min_override_pct.clamp(HARD_FLOOR_PCT, 100)
    }
}

/// Deep-merge `over` into `base` (tables merge per key, everything else replaces).
pub fn merge(base: &mut toml::Value, over: toml::Value) {
    match (base, over) {
        (toml::Value::Table(b), toml::Value::Table(o)) => {
            for (k, v) in o {
                match b.get_mut(&k) {
                    Some(slot) => merge(slot, v),
                    None => {
                        b.insert(k, v);
                    }
                }
            }
        }
        (b, o) => *b = o,
    }
}

pub fn load_from(paths: &[String]) -> Result<Config, String> {
    let mut acc = toml::Value::Table(toml::map::Map::new());
    let mut sources = Vec::new();
    for p in paths {
        let Ok(text) = std::fs::read_to_string(p) else { continue };
        let v: toml::Value = toml::from_str(&text).map_err(|e| format!("{p}: {e}"))?;
        merge(&mut acc, v);
        sources.push(p.clone());
    }
    let mut cfg: Config = acc.try_into().map_err(|e| format!("config: {e}"))?;
    cfg.sources = sources;
    Ok(cfg)
}

pub fn load() -> Result<Config, String> {
    let mut paths = vec![VENDOR_PATH.to_string(), ADMIN_PATH.to_string()];
    if let Ok(p) = std::env::var("MIOS_FANCTL_CONFIG") {
        paths.push(p);
    }
    load_from(&paths)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn defaults_without_files() {
        let c = load_from(&["/nonexistent/a.toml".into()]).unwrap();
        assert_eq!(c.client.socket, "/run/ventulus/ventulus.sock");
        assert!(c.roles.is_empty());
        assert!(c.sources.is_empty());
    }

    #[test]
    fn admin_layer_overrides_per_key() {
        let d = std::env::temp_dir().join(format!("mios-fanctl-test-{}", std::process::id()));
        std::fs::create_dir_all(&d).unwrap();
        let a = d.join("vendor.toml");
        let b = d.join("admin.toml");
        std::fs::write(&a, "[client]\nrefresh_ms = 500\n[roles]\n\"h:pwm6\" = \"pump\"\n").unwrap();
        std::fs::write(&b, "[client]\nsocket = \"/tmp/x.sock\"\n[roles]\n\"h:pwm7\" = \"pump\"\n").unwrap();
        let c = load_from(&[a.display().to_string(), b.display().to_string()]).unwrap();
        assert_eq!(c.client.refresh_ms, 500);
        assert_eq!(c.client.socket, "/tmp/x.sock");
        assert_eq!(c.roles.len(), 2);
        std::fs::remove_dir_all(&d).ok();
    }

    #[test]
    fn floor_never_below_hard_floor() {
        let mut c = Config::default();
        c.client.min_override_pct = 5;
        assert_eq!(c.effective_floor(), HARD_FLOOR_PCT);
        c.client.min_override_pct = 45;
        assert_eq!(c.effective_floor(), 45);
    }

    #[test]
    fn unknown_key_rejected() {
        let d = std::env::temp_dir().join(format!("mios-fanctl-bad-{}", std::process::id()));
        std::fs::write(&d, "[client]\nsokcet = \"x\"\n").unwrap();
        assert!(load_from(&[d.display().to_string()]).is_err());
        std::fs::remove_file(&d).ok();
    }
}
