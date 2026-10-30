// AI-hint: Unified systemd and deployment projection library: units, blade capabilities, kernel cmdline, Cockpit, and FreeIPA settings from SSOT.
//! MiOS Systemd Unit Generator & Golden Master Deviance Oracle.

use serde::Deserialize;
use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::Path;
use thiserror::Error;

pub mod tmux_os;
use tmux_os::render_tmux_keys;

#[derive(Error, Debug)]
pub enum UnitGenError {
    #[error("IO error: {0}")]
    Io(#[from] std::io::Error),
    #[error("TOML parse error: {0}")]
    Toml(#[from] toml::de::Error),
    #[error("Golden master verification error: {0}")]
    GoldenMaster(String),
}

pub const BLADE_KARG: &str = "usr/lib/bootc/kargs.d/05-mios-blade.toml";
pub const UKI_CMDLINE: &str = "usr/lib/kernel/cmdline";

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CpuMitigationsPolicy {
    pub required: Vec<String>,
    pub forbidden: Vec<String>,
}

impl CpuMitigationsPolicy {
    pub fn from_ssot_str(ssot: &str) -> Result<Self, UnitGenError> {
        let doc: toml::Value = toml::from_str(ssot)?;
        let kargs_tbl = doc
            .get("kargs")
            .ok_or_else(|| UnitGenError::GoldenMaster("mios.toml missing [kargs] table".into()))?;
        let cpu_tbl = kargs_tbl.get("cpu_mitigations").ok_or_else(|| {
            UnitGenError::GoldenMaster("mios.toml missing [kargs.cpu_mitigations] table".into())
        })?;
        let required = cpu_tbl
            .get("required")
            .and_then(|v| v.as_array())
            .ok_or_else(|| {
                UnitGenError::GoldenMaster(
                    "[kargs.cpu_mitigations].required must be a string array".into(),
                )
            })?
            .iter()
            .map(|v| {
                v.as_str().map(str::to_owned).ok_or_else(|| {
                    UnitGenError::GoldenMaster(
                        "[kargs.cpu_mitigations].required elements must be strings".into(),
                    )
                })
            })
            .collect::<Result<Vec<_>, _>>()?;
        let forbidden = cpu_tbl
            .get("forbidden")
            .and_then(|v| v.as_array())
            .ok_or_else(|| {
                UnitGenError::GoldenMaster(
                    "[kargs.cpu_mitigations].forbidden must be a string array".into(),
                )
            })?
            .iter()
            .map(|v| {
                v.as_str().map(str::to_owned).ok_or_else(|| {
                    UnitGenError::GoldenMaster(
                        "[kargs.cpu_mitigations].forbidden elements must be strings".into(),
                    )
                })
            })
            .collect::<Result<Vec<_>, _>>()?;
        Ok(Self {
            required,
            forbidden,
        })
    }
}

pub fn load_cpu_mitigations_policy(
    root: &Path,
    toml_override: Option<&Path>,
) -> Result<CpuMitigationsPolicy, UnitGenError> {
    if let Some(p) = toml_override {
        let s = fs::read_to_string(p)?;
        return CpuMitigationsPolicy::from_ssot_str(&s);
    }
    let local = root.join(SSOT);
    if local.is_file() {
        let s = fs::read_to_string(&local)?;
        return CpuMitigationsPolicy::from_ssot_str(&s);
    }
    if let Ok(m) = std::env::var("MIOS_ROOT") {
        let p = Path::new(&m).join(SSOT);
        if p.is_file() {
            let s = fs::read_to_string(&p)?;
            return CpuMitigationsPolicy::from_ssot_str(&s);
        }
    }
    let cur = Path::new(SSOT);
    if cur.is_file() {
        let s = fs::read_to_string(cur)?;
        return CpuMitigationsPolicy::from_ssot_str(&s);
    }
    if let Ok(manifest_dir) = std::env::var("CARGO_MANIFEST_DIR") {
        let p = Path::new(&manifest_dir).join("../../..").join(SSOT);
        if p.is_file() {
            let s = fs::read_to_string(&p)?;
            return CpuMitigationsPolicy::from_ssot_str(&s);
        }
    }
    if let Ok(exe) = std::env::current_exe() {
        for ancestor in exe.ancestors() {
            let cand = ancestor.join(SSOT);
            if cand.is_file() {
                let s = fs::read_to_string(&cand)?;
                return CpuMitigationsPolicy::from_ssot_str(&s);
            }
        }
    }
    Err(UnitGenError::GoldenMaster(
        "cannot locate usr/share/mios/mios.toml SSOT".into(),
    ))
}

pub const COCKPIT_CONF: &str = "etc/cockpit/cockpit.conf";
pub const IPA_ENROLL_ENV: &str = "etc/mios/ipa-enroll.env";
pub const BOOTC_INSTALL_CONF: &str = "usr/lib/bootc/install/00-mios.toml";
pub const REPART_ROOT_CONF: &str = "usr/lib/repart.d/50-root.conf";
const SSOT: &str = "usr/share/mios/mios.toml";
const DROPINS: &str = "usr/share/mios/dropins";

#[derive(Clone, Copy, Debug)]
pub enum DeploymentKind {
    BladeDropins,
    BladeKarg,
    UkiCmdline,
    Cockpit,
    IpaEnroll,
    BootcInstall,
    Keybindings,
}

/// Shared mobile keyboard contract. Reject collisions before writing any file;
/// the same table drives SSH/tmux, compositor, desktop, and editor surfaces.
pub fn render_keybindings(ssot: &str) -> Result<BTreeMap<String, String>, UnitGenError> {
    render_keybindings_for(ssot, "linux")
}

/// `platform` is `linux` (the image, the runtime projection) or `windows` (the native host tmux render, which
/// drops every row and menu item whose `platforms` omits it and every Linux-only MiOS-Tmux helper).
pub fn render_keybindings_for(
    ssot: &str,
    platform: &str,
) -> Result<BTreeMap<String, String>, UnitGenError> {
    if !matches!(platform, "linux" | "windows") {
        return Err(UnitGenError::GoldenMaster(format!(
            "keybindings platform must be linux or windows, not {platform:?}"
        )));
    }
    let doc: toml::Value = toml::from_str(ssot)?;
    let config = config_value(&doc, "root", "keybindings")?;
    let string = |table: &toml::Value, key: &str| -> Result<String, UnitGenError> {
        config_value(table, "keybindings", key)?
            .as_str()
            .filter(|v| !v.is_empty() && !v.contains(['\n', '\r', '\0']))
            .map(str::to_owned)
            .ok_or_else(|| {
                UnitGenError::GoldenMaster(format!("[keybindings].{key}: invalid string"))
            })
    };
    let number = |key: &str| -> Result<i64, UnitGenError> {
        config_value(config, "keybindings", key)?
            .as_integer()
            .filter(|v| *v > 0)
            .ok_or_else(|| {
                UnitGenError::GoldenMaster(format!(
                    "[keybindings].{key}: positive integer required"
                ))
            })
    };
    let enabled = config_bool(config, "keybindings", "enabled")?;
    let prefix = string(config, "tmux_prefix")?;
    if !prefix.starts_with("C-") || prefix.len() != 3 || !prefix.as_bytes()[2].is_ascii_lowercase()
    {
        return Err(UnitGenError::GoldenMaster(
            "[keybindings].tmux_prefix must be C- plus one lowercase ASCII letter".into(),
        ));
    }
    let editor_prefix = string(config, "vscode_prefix")?;
    if editor_prefix != format!("ctrl+{}", &prefix[2..]) {
        return Err(UnitGenError::GoldenMaster(
            "[keybindings].vscode_prefix must match tmux_prefix".into(),
        ));
    }
    let modifier = string(config, "desktop_modifier")?;
    let accelerator = string(config, "desktop_accelerator")?;
    let header =
        "# AI-hint: Generated from mios.toml [keybindings] by mios-unit-gen keybindings.\n";
    let mut tmux = header.to_owned();
    let mut hypr = header.to_owned();
    let mut sway = header.to_owned();
    let mut desktop = header.to_owned();
    let mut editor = Vec::new();
    let mut mobile = Vec::new();
    let mut keys = BTreeSet::new();
    if enabled {
        tmux += &format!("unbind-key -a -T prefix\nset -g prefix {prefix}\nset -g prefix2 None\nbind-key {prefix} send-prefix\nset -s escape-time {}\nset -g repeat-time {}\nset -g history-limit {}\nset -g mouse {}\n", number("escape_time_ms")?, number("repeat_time_ms")?, number("history_limit")?, if config_bool(config, "keybindings", "mouse")? { "on" } else { "off" });
        let actions = config_value(config, "keybindings", "actions")?
            .as_array()
            .filter(|v| !v.is_empty())
            .ok_or_else(|| {
                UnitGenError::GoldenMaster("[keybindings].actions must not be empty".into())
            })?;
        let mut paths = Vec::new();
        let mut tmux_actions = Vec::new();
        for action in actions {
            let key = string(action, "key")?;
            let id = string(action, "id")?;
            if key.len() != 1
                || !key.as_bytes()[0].is_ascii_lowercase()
                || !keys.insert(key.clone())
            {
                return Err(UnitGenError::GoldenMaster(format!(
                    "[keybindings].actions duplicate or non-mobile key: {key}"
                )));
            }
            if !id.bytes().all(|v| v.is_ascii_lowercase() || v == b'-') {
                return Err(UnitGenError::GoldenMaster(format!(
                    "[keybindings].actions invalid id: {id}"
                )));
            }
            let command = string(action, "command")?;
            let desktop_command = string(action, "desktop_command")?;
            let label = string(action, "label")?;
            if [command.as_str(), desktop_command.as_str(), label.as_str()]
                .iter()
                .any(|v| v.contains('\''))
            {
                return Err(UnitGenError::GoldenMaster(format!(
                    "[keybindings].actions {id}: GVariant quote is unsupported"
                )));
            }
            tmux_actions.push((key.clone(), string(action, "tmux_command")?, label.clone()));
            hypr += &format!("bind = {modifier}, {key}, exec, {desktop_command}\n");
            let sway_modifier = modifier
                .split_whitespace()
                .map(|m| match m {
                    "CTRL" => "Ctrl",
                    "ALT" => "Mod1",
                    "SHIFT" => "Shift",
                    "SUPER" => "Mod4",
                    other => other,
                })
                .collect::<Vec<_>>()
                .join("+");
            sway += &format!("bindsym {sway_modifier}+{key} exec {desktop_command}\n");
            let path = format!(
                "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/mios-{id}/"
            );
            paths.push(format!("'{path}'"));
            desktop += &format!("\n[org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/mios-{id}]\nname='{label}'\ncommand='{desktop_command}'\nbinding='{accelerator}{key}'\n");
            let mut row = serde_json::json!({"key": format!("{editor_prefix} {key}"), "command": string(action, "vscode_command")?, "when": "!terminalFocus"});
            if let Some(shell) = action.get("vscode_shell").and_then(toml::Value::as_str) {
                row["args"] = serde_json::json!({"commands": ["workbench.action.terminal.new", {"command": "workbench.action.terminal.sendSequence", "args": {"text": format!("{shell}\r")}}]});
            }
            editor.push(row);
            mobile.push(
                serde_json::json!({"name":label,"prefix":prefix,"key":key,"command":command}),
            );
        }
        desktop += &format!(
            "\n[org/gnome/settings-daemon/plugins/media-keys]\ncustom-keybindings=[{}]\n",
            paths.join(", ")
        );
        tmux += &render_tmux_keys(&doc, config, platform, &mut keys, &tmux_actions)?;
    }
    let passthrough = config_value(config, "keybindings", "vscode_passthrough_commands")?
        .as_array()
        .ok_or_else(|| {
            UnitGenError::GoldenMaster("vscode_passthrough_commands must be an array".into())
        })?
        .iter()
        .map(|v| {
            v.as_str()
                .map(|v| format!("-{v}"))
                .ok_or_else(|| UnitGenError::GoldenMaster("invalid passthrough command".into()))
        })
        .collect::<Result<Vec<_>, _>>()?;
    let settings = serde_json::json!({"terminal.integrated.allowChords": config_bool(config,"keybindings","vscode_allow_chords")?, "terminal.integrated.allowMnemonics": config_bool(config,"keybindings","vscode_allow_mnemonics")?, "terminal.integrated.commandsToSkipShell": passthrough});
    let encode = |value: &serde_json::Value| {
        // Cargo can unify mios-task's preserve_order feature into a CI build.
        // Canonicalize nested maps so standalone/runtime and workspace builds
        // project the same bytes regardless of that unrelated feature.
        let mut canonical = value.clone();
        canonical.sort_all_objects();
        serde_json::to_string_pretty(&canonical).expect("JSON value serializes") + "\n"
    };
    Ok(BTreeMap::from([
        ("usr/share/mios/tmux/mios-keys.tmux.conf".into(), tmux),
        ("etc/tmux.conf".into(), format!("{header}source-file /usr/share/mios/tmux/mios-theme.tmux.conf\nsource-file /usr/share/mios/tmux/mios-keys.tmux.conf\n")),
        // The retired hand-written Blink layer: every mobile key now lives in [keybindings.tmux]; legacy
        // `source-file` references keep working by loading the one global profile.
        ("usr/share/mios/tmux/blink-mobile-keys.tmux.conf".into(), format!("{header}# Retired Blink mobile layer: the global MiOS-Tmux profile carries every mobile key ([keybindings.tmux]).\nsource-file /etc/tmux.conf\n")),
        ("usr/share/mios/hyprland/mios-keys.conf".into(), hypr),
        ("usr/share/mios/sway/mios-keys.conf".into(), sway),
        ("etc/dconf/db/local.d/10-mios-keybindings".into(), desktop),
        ("usr/share/mios/keybindings/vscode-keybindings.json".into(), encode(&serde_json::Value::Array(editor.clone()))),
        ("usr/share/mios/keybindings/vscode-settings.json".into(), encode(&settings)),
        ("usr/share/mios/keybindings/mobile-shortcuts.json".into(), encode(&serde_json::Value::Array(mobile))),
        ("etc/skel/.config/Code/User/keybindings.json".into(), encode(&serde_json::Value::Array(editor.clone()))),
        ("etc/skel/.local/share/code-server/User/keybindings.json".into(), encode(&serde_json::Value::Array(editor))),
    ]))
}

fn config_value<'a>(
    table: &'a toml::Value,
    section: &str,
    key: &str,
) -> Result<&'a toml::Value, UnitGenError> {
    table.get(key).ok_or_else(|| {
        UnitGenError::GoldenMaster(format!("[{section}].{key} is missing from SSOT"))
    })
}

fn config_bool(table: &toml::Value, section: &str, key: &str) -> Result<bool, UnitGenError> {
    config_value(table, section, key)?
        .as_bool()
        .ok_or_else(|| UnitGenError::GoldenMaster(format!("[{section}].{key} must be a boolean")))
}

pub fn render_cockpit(ssot: &str) -> Result<String, UnitGenError> {
    let doc: toml::Value = toml::from_str(ssot)?;
    let config = config_value(&doc, "root", "cockpit")?;
    let allow = config_bool(config, "cockpit", "allow_unencrypted")?;
    let login = config_bool(config, "cockpit", "login_to")?;
    let idle = config_value(config, "cockpit", "idle_timeout")?
        .as_integer()
        .filter(|v| *v >= 0)
        .ok_or_else(|| {
            UnitGenError::GoldenMaster(
                "[cockpit].idle_timeout must be a nonnegative integer".into(),
            )
        })?;
    Ok(format!("# AI-hint: Cockpit web console settings projected from mios.toml [cockpit]; regenerate with mios-unit-gen cockpit, never edit.\n# Cockpit configuration file\n[WebService]\nAllowUnencrypted = {allow}\nLoginTo = {login}\n\n[Session]\nIdleTimeout = {idle}\n"))
}

fn install_fs(install: &toml::Value) -> Result<&str, UnitGenError> {
    let fs = config_value(install, "bootc_install", "root_fs_type")?
        .as_str()
        .ok_or_else(|| {
            UnitGenError::GoldenMaster("[bootc_install].root_fs_type must be a string".into())
        })?;
    if !matches!(fs, "xfs" | "ext4" | "btrfs") {
        return Err(UnitGenError::GoldenMaster(format!(
            "[bootc_install].root_fs_type = {fs:?} is not one of bootc's xfs | ext4 | btrfs"
        )));
    }
    Ok(fs)
}

fn install_gb(install: &toml::Value, key: &str) -> Result<i64, UnitGenError> {
    config_value(install, "bootc_install", key)?
        .as_integer()
        .filter(|v| *v > 0)
        .ok_or_else(|| {
            UnitGenError::GoldenMaster(format!("[bootc_install].{key} must be a positive integer"))
        })
}

/// bootc's install configuration ([install] in a usr/lib/bootc/install/*.toml
/// drop-in), from mios.toml [bootc_install]. bootc parses that table with
/// deny_unknown_fields, so only keys bootc documents are emitted.
pub fn render_bootc_install(ssot: &str) -> Result<String, UnitGenError> {
    let doc: toml::Value = toml::from_str(ssot)?;
    let fs = install_fs(config_value(&doc, "root", "bootc_install")?)?;
    Ok(format!("# AI-hint: bootc install configuration projected from mios.toml [bootc_install]; regenerate with mios-unit-gen bootc-install, never edit.\n[install]\nroot-fs-type = \"{fs}\"\n"))
}

/// The root partition for systemd-repart, from the same [bootc_install] table, so
/// the filesystem bootc formats and the one repart declares cannot disagree.
pub fn render_repart_root(ssot: &str) -> Result<String, UnitGenError> {
    let doc: toml::Value = toml::from_str(ssot)?;
    let install = config_value(&doc, "root", "bootc_install")?;
    let fs = install_fs(install)?;
    let min = install_gb(install, "root_min_gb")?;
    let pad = install_gb(install, "root_padding_gb")?;
    Ok(format!("# AI-hint: Root partition for systemd-repart projected from mios.toml [bootc_install]; regenerate with mios-unit-gen bootc-install, never edit.\n[Partition]\nType=root\n# Grows to fill the disk: no SizeMaxBytes.\nSizeMinBytes={min}G\nPaddingMinBytes={pad}G\nFormat={fs}\n"))
}

/// The enrollment consumer sources this file in Bash. Escape double-quote
/// syntax rather than letting a TOML value become a shell expansion.
pub fn render_ipa_enroll(ssot: &str) -> Result<String, UnitGenError> {
    let doc: toml::Value = toml::from_str(ssot)?;
    let identity = config_value(&doc, "root", "identity")?;
    let config = config_value(identity, "identity", "ipa")?;
    let enabled = config_bool(config, "identity.ipa", "enabled")?;
    let mut out = format!("# AI-hint: FreeIPA zero-touch enrollment settings projected from mios.toml [identity.ipa]; regenerate with mios-unit-gen ipa-enroll, never edit.\n# FreeIPA Zero-Touch Enrollment Config\nMIOS_IPA_ENABLED=\"{enabled}\"\n");
    for (key, variable) in [
        ("realm", "MIOS_IPA_REALM"),
        ("server", "MIOS_IPA_SERVER"),
        ("domain", "MIOS_IPA_DOMAIN"),
        ("enroll_principal", "MIOS_IPA_ENROLL_PRINCIPAL"),
        ("otp_file", "MIOS_IPA_OTP_FILE"),
        ("otp_key", "MIOS_IPA_OTP_KEY"),
    ] {
        let value = config_value(config, "identity.ipa", key)?
            .as_str()
            .ok_or_else(|| {
                UnitGenError::GoldenMaster(format!("[identity.ipa].{key} must be a string"))
            })?;
        if value.chars().any(char::is_control) {
            return Err(UnitGenError::GoldenMaster(format!(
                "[identity.ipa].{key} must not contain control characters"
            )));
        }
        if key == "otp_key"
            && (value.is_empty()
                || !value.bytes().enumerate().all(|(i, c)| {
                    c.is_ascii_alphabetic() || c == b'_' || (i > 0 && c.is_ascii_digit())
                }))
        {
            return Err(UnitGenError::GoldenMaster(
                "[identity.ipa].otp_key must be a shell variable name".into(),
            ));
        }
        out.push_str(variable);
        out.push_str("=\"");
        for c in value.chars() {
            if matches!(c, '\\' | '"' | '$' | '`') {
                out.push('\\');
            }
            out.push(c);
        }
        out.push_str("\"\n");
    }
    Ok(out)
}

/// Render only the capability files this projection owns; service drop-ins
/// maintained elsewhere under the same directory remain untouched.
pub fn render_blade_dropins(ssot: &str) -> Result<BTreeMap<String, String>, UnitGenError> {
    let doc: toml::Value = toml::from_str(ssot)?;
    let mut requires = BTreeMap::<String, Vec<String>>::new();
    if let Some(table) = doc.get("blade").and_then(|b| b.get("requires")) {
        let table = table
            .as_table()
            .ok_or_else(|| UnitGenError::GoldenMaster("[blade.requires] must be a table".into()))?;
        for (service, value) in table {
            let values = match value {
                toml::Value::String(s) => vec![s.clone()],
                toml::Value::Array(values) => values
                    .iter()
                    .map(|v| {
                        v.as_str().map(str::to_owned).ok_or_else(|| {
                            UnitGenError::GoldenMaster(format!(
                                "blade.requires.{service}: capabilities must be strings"
                            ))
                        })
                    })
                    .collect::<Result<Vec<_>, _>>()?,
                _ => {
                    return Err(UnitGenError::GoldenMaster(format!(
                        "blade.requires.{service}: expected a string or string array"
                    )))
                }
            };
            let caps: Vec<String> = values
                .into_iter()
                .map(|s| s.trim().to_owned())
                .filter(|s| !s.is_empty())
                .collect();
            for cap in &caps {
                if !cap
                    .bytes()
                    .all(|c| c.is_ascii_alphanumeric() || matches!(c, b'-' | b'_'))
                {
                    return Err(UnitGenError::GoldenMaster(format!(
                        "blade.requires.{service}: unsafe capability {cap:?}"
                    )));
                }
            }
            requires.insert(service.clone(), caps);
        }
    }
    let unique: BTreeSet<&String> = requires.values().flatten().collect();
    let mut out = BTreeMap::new();
    for cap in unique {
        out.insert(format!("{DROPINS}/blade-{cap}.conf"), format!(
            "# AI-hint: GENERATED systemd capability drop-in for MiOS (WS-BLADE). DO NOT EDIT -- regenerate via mios-unit-gen blade-dropins.\n[Unit]\nConditionPathExists=/etc/mios/blade.d/{cap}\n"
        ));
    }
    let mut selectors = String::from(
        "# AI-hint: GENERATED k3s nodeSelectors/tolerations from mios.toml [blade.requires] SSOT (task_2236). DO NOT EDIT.\n# Rendered by mios-unit-gen blade-dropins.\nservices:\n"
    );
    let mut pcs = String::from(
        "# AI-hint: GENERATED Pacemaker location constraint rules from mios.toml [blade.requires] SSOT (task_2236). DO NOT EDIT.\n# Rendered by mios-unit-gen blade-dropins.\n"
    );
    for (service, caps) in &requires {
        selectors.push_str(&format!("  {service}:\n    nodeSelector:\n"));
        for cap in caps {
            selectors.push_str(&format!("      mios.capability/{cap}: \"true\"\n"));
        }
        selectors.push_str("    tolerations:\n");
        for cap in caps {
            selectors.push_str(&format!("      - key: \"mios.capability/{cap}\"\n        operator: \"Exists\"\n        effect: \"NoSchedule\"\n"));
        }
        if !caps.is_empty() {
            let conditions = caps
                .iter()
                .map(|c| format!("mios-cap-{c} eq true"))
                .collect::<Vec<_>>()
                .join(" and ");
            pcs.push_str(&format!(
                "pcs constraint location {service} rule score=100 {conditions}\n"
            ));
        }
    }
    out.insert(format!("{DROPINS}/k3s-node-selectors.yaml"), selectors);
    out.insert(format!("{DROPINS}/pcs-location-rules.pcs"), pcs);
    Ok(out)
}

pub fn render_blade_karg(ssot: &str) -> Result<String, UnitGenError> {
    let doc: toml::Value = toml::from_str(ssot)?;
    let blade = doc.get("blade");
    let kind = blade
        .and_then(|b| b.get("type"))
        .and_then(toml::Value::as_str)
        .unwrap_or("")
        .trim();
    if kind.is_empty() {
        return Err(UnitGenError::GoldenMaster(
            "[blade].type is empty -- refusing to emit an empty blade karg".into(),
        ));
    }
    if !blade
        .and_then(|b| b.get("archetypes"))
        .and_then(toml::Value::as_table)
        .is_some_and(|a| a.contains_key(kind))
    {
        return Err(UnitGenError::GoldenMaster(format!(
            "[blade].type = {kind:?} names no archetype in [blade.archetypes]"
        )));
    }
    let token = toml::Value::String(format!("mios.blade={kind}"));
    Ok(format!(
        "# AI-hint: GENERATED from mios.toml [blade].type. DO NOT EDIT; regenerate via mios-unit-gen blade-karg. Installer, Butane kernel_arguments and `mios blade set` override it on the cmdline, where role-apply reads the LAST mios.blade= token.\n# AI-related: usr/share/mios/mios.toml, usr/libexec/mios/role-apply, tools/native/mios-unit-gen/src/lib.rs\n# bootc kargs.d: bare `kargs = [...]` only. NO [kargs] table header.\n\nkargs = [\n    {token}\n]\n"
    ))
}

/// Preserve drop-in filename ordering and each file's token ordering. A bad
/// drop-in aborts the projection before the existing command line is written.
pub fn render_uki_cmdline(root: &Path) -> Result<String, UnitGenError> {
    render_uki_cmdline_with_toml(root, None)
}

pub fn render_uki_cmdline_with_toml(
    root: &Path,
    toml_override: Option<&Path>,
) -> Result<String, UnitGenError> {
    let policy = load_cpu_mitigations_policy(root, toml_override)?;
    let directory = root.join("usr/lib/bootc/kargs.d");
    let mut files = Vec::new();
    for entry in fs::read_dir(&directory)? {
        let path = entry?.path();
        if path.extension().is_some_and(|e| e == "toml") {
            files.push(path);
        }
    }
    files.sort();
    let mut tokens = Vec::new();
    let mut last_values: BTreeMap<String, String> = BTreeMap::new();
    for file in files {
        let doc: toml::Value = fs::read_to_string(&file)?
            .parse()
            .map_err(|e| UnitGenError::GoldenMaster(format!("{}: {e}", file.display())))?;
        if let Some(value) = doc.get("kargs") {
            let args = value.as_array().ok_or_else(|| {
                UnitGenError::GoldenMaster(format!(
                    "{}: kargs must be a string array",
                    file.display()
                ))
            })?;
            for arg in args {
                let token = arg.as_str().ok_or_else(|| {
                    UnitGenError::GoldenMaster(format!(
                        "{}: kargs must contain strings",
                        file.display()
                    ))
                })?;
                if policy.forbidden.iter().any(|f| f == token) {
                    return Err(UnitGenError::GoldenMaster(format!(
                        "{}: contradictory CPU mitigation argument forbidden: {token}",
                        file.display()
                    )));
                }
                tokens.push(token.to_owned());
                if let Some((k, v)) = token.split_once('=') {
                    last_values.insert(k.to_string(), v.to_string());
                }
            }
        }
    }
    for req in &policy.required {
        let (req_key, req_val) = req.split_once('=').ok_or_else(|| {
            UnitGenError::GoldenMaster(format!("required CPU mitigation must be key=value: {req}"))
        })?;
        match last_values.get(req_key) {
            None => {
                return Err(UnitGenError::GoldenMaster(format!(
                    "missing required CPU mitigation: {req}"
                )));
            }
            Some(actual_val) if actual_val != req_val => {
                return Err(UnitGenError::GoldenMaster(format!(
                    "contradictory CPU mitigation argument forbidden: {req_key}={actual_val}"
                )));
            }
            _ => {}
        }
    }
    Ok(tokens.join(" ").trim().to_owned() + "\n")
}

/// Both the CLI and the daemon use this comparison, so neither can certify a
/// different destination or a check that never renders its subject.
pub fn project_deployment(
    root: &Path,
    kind: DeploymentKind,
    check: bool,
    toml_override: Option<&Path>,
) -> Result<usize, UnitGenError> {
    let files = match kind {
        DeploymentKind::Keybindings => render_keybindings(&fs::read_to_string(
            toml_override.unwrap_or(&root.join(SSOT)),
        )?)?,
        DeploymentKind::BladeDropins => render_blade_dropins(&fs::read_to_string(
            toml_override.unwrap_or(&root.join(SSOT)),
        )?)?,
        DeploymentKind::BladeKarg => BTreeMap::from([(
            BLADE_KARG.to_owned(),
            render_blade_karg(&fs::read_to_string(
                toml_override.unwrap_or(&root.join(SSOT)),
            )?)?,
        )]),
        DeploymentKind::UkiCmdline => BTreeMap::from([(
            UKI_CMDLINE.to_owned(),
            render_uki_cmdline_with_toml(root, toml_override)?,
        )]),
        DeploymentKind::Cockpit => BTreeMap::from([(
            COCKPIT_CONF.to_owned(),
            render_cockpit(&fs::read_to_string(
                toml_override.unwrap_or(&root.join(SSOT)),
            )?)?,
        )]),
        DeploymentKind::IpaEnroll => BTreeMap::from([(
            IPA_ENROLL_ENV.to_owned(),
            render_ipa_enroll(&fs::read_to_string(
                toml_override.unwrap_or(&root.join(SSOT)),
            )?)?,
        )]),
        DeploymentKind::BootcInstall => {
            let ssot = fs::read_to_string(toml_override.unwrap_or(&root.join(SSOT)))?;
            BTreeMap::from([
                (BOOTC_INSTALL_CONF.to_owned(), render_bootc_install(&ssot)?),
                (REPART_ROOT_CONF.to_owned(), render_repart_root(&ssot)?),
            ])
        }
    };
    let mut drift = Vec::new();
    for (relative, body) in &files {
        let path = root.join(relative);
        if check {
            match fs::read_to_string(&path) {
                Ok(have) => {
                    if matches!(kind, DeploymentKind::UkiCmdline) {
                        let policy = load_cpu_mitigations_policy(root, toml_override)?;
                        let tokens: Vec<&str> = have.split_whitespace().collect();
                        let mut disk_kargs: BTreeMap<&str, &str> = BTreeMap::new();
                        for token in &tokens {
                            if policy.forbidden.iter().any(|f| f == token) {
                                drift.push(format!(
                                    "{relative}: contradictory CPU mitigation argument forbidden: {token}"
                                ));
                            }
                            if let Some((k, v)) = token.split_once('=') {
                                disk_kargs.insert(k, v);
                            }
                        }
                        for req in &policy.required {
                            if let Some((k, v)) = req.split_once('=') {
                                match disk_kargs.get(k) {
                                    None => drift.push(format!(
                                        "{relative}: missing required CPU mitigation: {req}"
                                    )),
                                    Some(actual) if *actual != v => drift.push(format!(
                                        "{relative}: contradictory CPU mitigation argument forbidden: {k}={actual}"
                                    )),
                                    _ => {}
                                }
                            }
                        }
                    }
                    if have != *body {
                        drift.push(format!("{relative}: drifted from SSOT"));
                    }
                }
                Err(e) => drift.push(format!("{relative}: cannot read projection: {e}")),
            }
        } else {
            if let Some(parent) = path.parent() {
                fs::create_dir_all(parent)?;
            }
            fs::write(path, body)?;
        }
    }
    if !drift.is_empty() {
        return Err(UnitGenError::GoldenMaster(drift.join("\n")));
    }
    Ok(files.len())
}

#[derive(Deserialize, Debug, Clone)]
pub struct SsotRoot {
    pub security: Option<SecurityTable>,
    pub units: Option<BTreeMap<String, toml::Value>>,
}

#[derive(Deserialize, Debug, Clone)]
pub struct SecurityTable {
    pub privileged_units: Option<PrivilegedUnitsRoster>,
}

#[derive(Deserialize, Debug, Clone)]
pub struct PrivilegedUnitsRoster {
    pub unconfined: Option<Vec<String>>,
}

/// systemd expands `${VAR}` only in Exec*= command lines. Anywhere else
/// (Environment=, ListenStream=, ...) a `${MIOS_PORTS_<NAME>}` placeholder
/// reaches the daemon verbatim, so the projection resolves it from SSOT
/// [ports] (+ stack_id * 10000) and the shipped unit carries the number. A
/// name [ports] does not declare is left as written.
fn resolve_port_placeholders(key: &str, value: &str, ports: Option<&toml::Value>) -> String {
    let Some(ports) = ports.filter(|_| !key.starts_with("Exec")) else {
        return value.to_owned();
    };
    let offset = ports
        .get("stack_id")
        .and_then(|v| {
            v.as_integer()
                .or_else(|| v.as_str().and_then(|s| s.parse().ok()))
        })
        .unwrap_or(0)
        * 10000;
    let mut out = String::with_capacity(value.len());
    let mut rest = value;
    while let Some(start) = rest.find("${MIOS_PORTS_") {
        let Some(len) = rest[start..].find('}') else {
            break;
        };
        let placeholder = &rest[start..start + len + 1];
        let name = placeholder["${MIOS_PORTS_".len()..placeholder.len() - 1]
            .split(":-")
            .next()
            .unwrap_or_default()
            .to_ascii_lowercase();
        out.push_str(&rest[..start]);
        match ports.get(&name).and_then(toml::Value::as_integer) {
            Some(port) => out.push_str(&(port + offset).to_string()),
            None => out.push_str(placeholder),
        }
        rest = &rest[start + len + 1..];
    }
    out.push_str(rest);
    out
}

/// Render units from SSOT TOML string. Returns map of relative path -> rendered content.
/// Render unprivileged session units through the same canonical systemd renderer.
pub fn render_user_units(ssot_toml: &str) -> Result<BTreeMap<String, String>, UnitGenError> {
    let mut doc: toml::Value = toml::from_str(ssot_toml)?;
    let users = doc
        .get("user_units")
        .cloned()
        .unwrap_or(toml::Value::Table(Default::default()));
    doc.as_table_mut()
        .ok_or_else(|| UnitGenError::GoldenMaster("SSOT must be a table".into()))?
        .insert("units".into(), users);
    render_units(&toml::to_string(&doc).map_err(|e| UnitGenError::GoldenMaster(e.to_string()))?)
}

pub fn render_units(ssot_toml: &str) -> Result<BTreeMap<String, String>, UnitGenError> {
    let root: SsotRoot = toml::from_str(ssot_toml)?;
    let doc: toml::Value = toml::from_str(ssot_toml)?;
    let ports = doc.get("ports");
    let mut rendered = BTreeMap::new();

    let unconfined_units: Vec<String> = root
        .security
        .as_ref()
        .and_then(|s| s.privileged_units.as_ref())
        .and_then(|p| p.unconfined.clone())
        .unwrap_or_default();

    if let Some(units) = root.units {
        for (filename, val) in units {
            if let Some(sections) = val.as_table() {
                let mut out = String::new();
                if let Some(comment_val) = sections.get("comment").and_then(|v| v.as_str()) {
                    out.push_str(comment_val);
                    if !comment_val.ends_with('\n') {
                        out.push('\n');
                    }
                }
                fn section_rank(sec: &str) -> (usize, &str) {
                    match sec.to_lowercase().as_str() {
                        "unit" => (0, sec),
                        "service" | "path" | "timer" | "socket" | "mount" => (1, sec),
                        "install" => (2, sec),
                        _ => (3, sec),
                    }
                }
                let mut sec_keys: Vec<&String> = sections.keys().collect();
                sec_keys.sort_by_key(|k| section_rank(k));
                for sec_name in sec_keys {
                    if sec_name == "comment" {
                        continue;
                    }
                    let sec_kv = &sections[sec_name];
                    if let Some(table) = sec_kv.as_table() {
                        // Blank line before each header, above the comment
                        // block: the tree separates sections 208:16 and glues
                        // comment-to-header 94:23.
                        if !out.is_empty() && !out.ends_with("\n\n") {
                            out.push('\n');
                        }
                        // A section's `comment` is the block ABOVE its header;
                        // the SSOT nests it, so a top-level-only hoist emitted a
                        // bogus `comment=` key.
                        if let Some(c) = table.get("comment").and_then(|v| v.as_str()) {
                            out.push_str(c);
                            if !c.ends_with('\n') {
                                out.push('\n');
                            }
                        }
                        out.push_str(&format!("[{}]\n", sec_name));
                        let is_service = sec_name == "Service";
                        let is_unconfined = unconfined_units.contains(&filename);

                        let mut existing_keys = BTreeMap::new();
                        for (k, v) in table {
                            if k == "comment" {
                                continue;
                            }
                            existing_keys.insert(k.as_str(), v);
                            match v {
                                toml::Value::String(s) => {
                                    let s = resolve_port_placeholders(k, s, ports);
                                    out.push_str(&format!("{}={}\n", k, s));
                                }
                                toml::Value::Array(arr) => {
                                    for item in arr {
                                        if let Some(s) = item.as_str() {
                                            let s = resolve_port_placeholders(k, s, ports);
                                            out.push_str(&format!("{}={}\n", k, s));
                                        }
                                    }
                                }
                                toml::Value::Boolean(b) => {
                                    // systemd's own spelling; `true`/`false`
                                    // parse but are not what the tree ships.
                                    out.push_str(&format!(
                                        "{}={}\n",
                                        k,
                                        if *b { "yes" } else { "no" }
                                    ));
                                }
                                toml::Value::Integer(i) => {
                                    out.push_str(&format!("{}={}\n", k, i));
                                }
                                _ => {}
                            }
                        }

                        // No hardening baseline is injected: a generator that
                        // adds undeclared directives is not a projection.
                        // See tasks.jsonl task_267.
                        let _ = (is_service, is_unconfined, &existing_keys);
                    }
                }
                if !out.is_empty() {
                    rendered.insert(filename, out);
                }
            }
        }
    }

    Ok(rendered)
}

/// Compare unit bodies on content, not on byte-exactness: line endings differ
/// between a Windows checkout and the Linux build, and a trailing newline is
/// not drift. Anything else is.
pub fn normalize(s: &str) -> String {
    let mut out: Vec<&str> = s.lines().map(|l| l.trim_end()).collect();
    while out.last().is_some_and(|l| l.is_empty()) {
        out.pop();
    }
    out.join("\n")
}

/// What `[units.*]` projects onto `usr/lib/systemd/system`, in four buckets.
#[derive(Debug, Default, Clone)]
pub struct Projection {
    /// Declared in `[units.*]` and rendering byte-equal (after `normalize`).
    pub faithful: Vec<String>,
    /// Declared in `[units.*]` but rendering something else than the file.
    pub drifted: Vec<String>,
    /// Declared in `[units.*]` with no file on disk at all.
    pub missing: Vec<String>,
    /// Shipped units the SSOT does not describe -- outside the projection.
    pub undeclared: Vec<String>,
}

const UNIT_SUFFIXES: [&str; 7] = [
    ".service", ".target", ".timer", ".path", ".socket", ".mount", ".slice",
];

/// The shrink-only drift register: `[unit_projection].drift` in the SSOT.
///
/// It exists because `[units.*]` describes 68 of the tree's units and most of
/// those descriptions are stale. The register makes that debt COUNTED rather
/// than absent, so the projection can be gated today and drained unit by unit.
pub fn drift_register(ssot_toml: &str) -> Result<Vec<String>, UnitGenError> {
    let root: toml::Value = toml::from_str(ssot_toml)?;
    Ok(root
        .get("unit_projection")
        .and_then(|t| t.get("drift"))
        .and_then(|d| d.as_array())
        .map(|a| {
            a.iter()
                .filter_map(|v| v.as_str())
                .map(|s| s.to_string())
                .collect()
        })
        .unwrap_or_default())
}

fn shipped_units(unit_dir: &Path) -> Vec<String> {
    let mut out = Vec::new();
    if let Ok(entries) = fs::read_dir(unit_dir) {
        for entry in entries.flatten() {
            let path = entry.path();
            if !path.is_file() {
                continue;
            }
            if let Some(name) = path.file_name().and_then(|n| n.to_str()) {
                if UNIT_SUFFIXES.iter().any(|sfx| name.ends_with(sfx)) {
                    out.push(name.to_string());
                }
            }
        }
    }
    out.sort();
    out
}

/// Render `[units.*]` from the SSOT at `root` and compare it to the shipped tree.
pub fn project(root: &Path) -> Result<Projection, UnitGenError> {
    project_scope(root, false)
}
pub fn project_user(root: &Path) -> Result<Projection, UnitGenError> {
    project_scope(root, true)
}
fn project_scope(root: &Path, user: bool) -> Result<Projection, UnitGenError> {
    let ssot = fs::read_to_string(root.join("usr/share/mios/mios.toml"))?;
    let rendered = if user {
        render_user_units(&ssot)?
    } else {
        render_units(&ssot)?
    };
    let unit_dir = root.join(if user {
        "usr/lib/systemd/user"
    } else {
        "usr/lib/systemd/system"
    });

    let mut p = Projection::default();
    for (filename, body) in &rendered {
        match fs::read_to_string(unit_dir.join(filename)) {
            Err(_) => p.missing.push(filename.clone()),
            Ok(actual) => {
                if normalize(&actual) == normalize(body) {
                    p.faithful.push(filename.clone());
                } else {
                    p.drifted.push(filename.clone());
                }
            }
        }
    }
    for name in shipped_units(&unit_dir) {
        if !rendered.contains_key(&name) {
            p.undeclared.push(name);
        }
    }
    Ok(p)
}

#[cfg(test)]
mod tests {
    #[test]
    fn mobile_keybindings_project_all_surfaces() {
        let ssot = include_str!("../../../../usr/share/mios/mios.toml");
        let profiles = super::render_keybindings(ssot).unwrap();
        assert_eq!(profiles.len(), 11);
        assert!(profiles["usr/share/mios/tmux/mios-keys.tmux.conf"]
            .contains(" Tab send-keys BTab\n"));
        assert!(profiles["usr/share/mios/hyprland/mios-keys.conf"]
            .contains("bind = CTRL ALT SHIFT, a, exec,"));
        let editor: serde_json::Value =
            serde_json::from_str(&profiles["usr/share/mios/keybindings/vscode-keybindings.json"])
                .unwrap();
        // One editor row per SSOT action: the count is the SSOT's, not a literal.
        let doc: toml::Value = toml::from_str(ssot).unwrap();
        let actions = doc["keybindings"]["actions"].as_array().unwrap().len();
        assert!(actions > 0);
        assert_eq!(editor.as_array().unwrap().len(), actions);
        assert!(editor
            .as_array()
            .unwrap()
            .iter()
            .all(|row| row["when"] == "!terminalFocus"));
    }

    #[test]
    fn mobile_keybindings_mouse_menu_respects_application_tracking() {
        let ssot = include_str!("../../../../usr/share/mios/mios.toml");
        let profiles = super::render_keybindings(ssot).unwrap();
        let tmux = &profiles["usr/share/mios/tmux/mios-keys.tmux.conf"];
        // Right-click passes through when the application tracks the mouse (stock tmux 3.7c rule; copy and
        // view mode keep the menu) and otherwise opens the generated pane menu alias.
        assert!(tmux.contains("MouseDown3Pane select-pane -t = \\; if-shell -F '#{||:#{mouse_any_flag},"));
        assert!(tmux.contains("'send-keys -M' \"if-shell -F '#{e|<:#{client_height},"));
        assert!(tmux.contains("mios-menu-pane=display-menu"));
        assert!(tmux.contains("\"mios-menu-root=display-menu -T MiOS -x "));
        // The master toggle must disable menu interception as well as mouse capture.
        let mut doc: toml::Value = toml::from_str(ssot).unwrap();
        doc["keybindings"]["mouse"] = toml::Value::Boolean(false);
        let disabled = super::render_keybindings(&toml::to_string(&doc).unwrap()).unwrap();
        let disabled_tmux = &disabled["usr/share/mios/tmux/mios-keys.tmux.conf"];
        assert!(!disabled_tmux.contains("set -g mouse on"));
        assert!(!disabled_tmux.contains("-T root MouseDown3Pane select-pane"));
        assert!(!disabled_tmux.contains("-T root MouseDown3Status \"if-shell"));
    }

    fn tmux_conf(doc: &toml::Value) -> Result<String, String> {
        super::render_keybindings(&toml::to_string(doc).unwrap())
            .map(|p| p["usr/share/mios/tmux/mios-keys.tmux.conf"].clone())
            .map_err(|e| e.to_string())
    }

    /// U1 C-18: mouse_terms gives the Linux VT the tmux mouse feature; without it (or with mouse off) nothing changes.
    #[test]
    fn mouse_terms_add_the_mouse_terminal_feature() {
        let doc: toml::Value = toml::from_str(include_str!("../../../../usr/share/mios/mios.toml")).unwrap();
        assert!(tmux_conf(&doc).unwrap().contains("set -s terminal-features[130] 'linux:mouse'\n"));
        let mut none = doc.clone();
        none["keybindings"]["tmux"].as_table_mut().unwrap().remove("mouse_terms");
        assert!(!tmux_conf(&none).unwrap().contains(":mouse'"));
        let mut off = doc.clone();
        off["keybindings"]["mouse"] = toml::Value::Boolean(false);
        let off_conf = tmux_conf(&off).unwrap();
        assert!(!off_conf.contains(":mouse'"), "the mouse master toggle wins");
        assert!(off_conf.contains("set -su terminal-features[130]\n"), "the owned slots are still cleared on reload");
        let mut bad = doc.clone();
        bad["keybindings"]["tmux"]["mouse_terms"] = toml::Value::Array(vec![toml::Value::String("linux\" ; run-shell x".into())]);
        assert!(tmux_conf(&bad).unwrap_err().contains("invalid terminal pattern"));
        let mut overlap = doc.clone();
        overlap["keybindings"]["tmux"]["mouse_features_index"] = toml::Value::Integer(125);
        assert!(tmux_conf(&overlap).unwrap_err().contains("overlaps the extended-keys"), "never clobber the extkeys slots");
    }

    #[test]
    fn mobile_keybindings_reject_duplicate() {
        let mut doc: toml::Value =
            toml::from_str(include_str!("../../../../usr/share/mios/mios.toml")).unwrap();
        doc["keybindings"]["actions"][1]["key"] = toml::Value::String("t".into());
        let error = super::render_keybindings(&toml::to_string(&doc).unwrap())
            .unwrap_err()
            .to_string();
        assert!(error.contains("duplicate or non-mobile key: t"), "{error}");
    }

    #[test]
    fn mobile_keybindings_negative_projection_names_planted_file() {
        let root = tempfile::tempdir().unwrap();
        let ssot = root.path().join("usr/share/mios/mios.toml");
        std::fs::create_dir_all(ssot.parent().unwrap()).unwrap();
        std::fs::write(&ssot, include_str!("../../../../usr/share/mios/mios.toml")).unwrap();
        assert_eq!(
            super::project_deployment(root.path(), super::DeploymentKind::Keybindings, false, None)
                .unwrap(),
            11
        );
        super::project_deployment(root.path(), super::DeploymentKind::Keybindings, true, None)
            .unwrap();
        std::fs::write(
            root.path().join("usr/share/mios/tmux/mios-keys.tmux.conf"),
            "DEVLOOP-PLANTED-KEYBIND",
        )
        .unwrap();
        let error =
            super::project_deployment(root.path(), super::DeploymentKind::Keybindings, true, None)
                .unwrap_err()
                .to_string();
        assert!(
            error.contains("usr/share/mios/tmux/mios-keys.tmux.conf: drifted from SSOT"),
            "{error}"
        );
    }
    use super::*;

    /// The renderer is a PROJECTION: only what `[units.*]` declares comes
    /// out. It used to inject a hardening baseline. See tasks.jsonl task_267.
    #[test]
    fn test_render_invents_no_directives() {
        let toml_str = r#"
[units."sample.service".Service]
Type = "simple"
ExecStart = "/usr/bin/sample"
"#;
        let rendered = render_units(toml_str).unwrap();
        let sample = rendered.get("sample.service").unwrap();
        assert_eq!(
            sample,
            "[Service]\nType=simple\nExecStart=/usr/bin/sample\n"
        );
        for invented in [
            "NoNewPrivileges",
            "ProtectSystem",
            "ProtectHome",
            "PrivateTmp",
            "SystemCallFilter",
        ] {
            assert!(
                !sample.contains(invented),
                "renderer invented {invented}, which the SSOT never declared"
            );
        }
    }

    /// Being on the unconfined roster must not change the output either: with
    /// no injection there is nothing left for the roster to suppress.
    #[test]
    fn test_unconfined_roster_changes_nothing() {
        let body = r#"
[units."sample.service".Service]
Type = "simple"
ExecStart = "/usr/bin/sample"
"#;
        let with_roster =
            format!("[security.privileged_units]\nunconfined = [\"sample.service\"]\n{body}");
        assert_eq!(
            render_units(body).unwrap().get("sample.service"),
            render_units(&with_roster).unwrap().get("sample.service")
        );
    }

    /// A section's `comment` is the block ABOVE its header -- for the first
    /// section that is the file's AI-hint. Hoisting only a top-level `comment`
    /// emitted a literal `comment=` line, which is not a systemd key at all.
    #[test]
    fn test_section_comment_is_hoisted_above_the_header() {
        let toml_str = r##"
[units."sample.service".Unit]
comment = "# AI-hint: sample\n"
Description = "sample"

[units."sample.service".Service]
ExecStart = "/usr/bin/sample"
"##;
        let out = render_units(toml_str).unwrap();
        let sample = out.get("sample.service").unwrap();
        assert_eq!(
            sample,
            "# AI-hint: sample\n[Unit]\nDescription=sample\n\n[Service]\nExecStart=/usr/bin/sample\n"
        );
        assert!(
            !sample.contains("comment="),
            "comment leaked as a directive"
        );
    }

    /// systemd's own spelling. `true`/`false` parse, but they are not what the
    /// tree ships, so a bool rendered that way is drift on every boolean key.
    #[test]
    fn test_booleans_render_as_yes_no() {
        let toml_str = r#"
[units."sample.service".Service]
RemainAfterExit = true
PrivateTmp = false
"#;
        let out = render_units(toml_str).unwrap();
        assert_eq!(
            out.get("sample.service").unwrap(),
            "[Service]\nRemainAfterExit=yes\nPrivateTmp=no\n"
        );
    }

    /// systemd repeats a key rather than joining values, so an array is N lines.
    #[test]
    fn test_arrays_repeat_the_key() {
        let toml_str = r#"
[units."sample.service".Service]
ExecStartPre = ["/usr/bin/a", "/usr/bin/b"]
"#;
        let out = render_units(toml_str).unwrap();
        assert_eq!(
            out.get("sample.service").unwrap(),
            "[Service]\nExecStartPre=/usr/bin/a\nExecStartPre=/usr/bin/b\n"
        );
    }

    /// systemd leaves `${VAR}` unexpanded outside Exec*= lines, so those values
    /// must ship resolved; Exec lines keep the placeholder systemd expands, and
    /// a name [ports] does not declare is not invented.
    #[test]
    fn test_port_placeholders_resolve_only_where_systemd_cannot() {
        let toml_str = r#"
[ports]
stack_id = 1
node = 8650
[units."sample.socket".Socket]
ListenStream = "0.0.0.0:${MIOS_PORTS_NODE}"
[units."sample.service".Service]
ExecStart = "/usr/bin/x --port ${MIOS_PORTS_NODE}"
Environment = ["A=${MIOS_PORTS_NODE:-8650}/v1", "B=${MIOS_PORTS_ABSENT:-9}", "C=${MIOS_OTHER}"]
"#;
        let out = render_units(toml_str).unwrap();
        assert_eq!(
            out.get("sample.socket").unwrap(),
            "[Socket]\nListenStream=0.0.0.0:18650\n"
        );
        assert_eq!(
            out.get("sample.service").unwrap(),
            "[Service]\nExecStart=/usr/bin/x --port ${MIOS_PORTS_NODE}\n\
             Environment=A=18650/v1\nEnvironment=B=${MIOS_PORTS_ABSENT:-9}\nEnvironment=C=${MIOS_OTHER}\n"
        );
    }

    /// `normalize` must forgive line endings and a trailing newline, and NOTHING
    /// else -- an interior blank line is real drift.
    #[test]
    fn test_normalize_forgives_only_eol_and_trailing_blanks() {
        assert_eq!(normalize("[Unit]\r\nX=1\r\n"), normalize("[Unit]\nX=1"));
        assert_eq!(normalize("[Unit]\nX=1\n\n\n"), normalize("[Unit]\nX=1"));
        assert_ne!(normalize("[Unit]\n\nX=1"), normalize("[Unit]\nX=1"));
    }

    #[test]
    fn test_drift_register_reads_the_ssot_table() {
        let toml_str = r#"
[unit_projection]
drift = ["a.service", "b.timer"]
"#;
        assert_eq!(
            drift_register(toml_str).unwrap(),
            vec!["a.service".to_string(), "b.timer".to_string()]
        );
        assert!(drift_register("").unwrap().is_empty());
    }
}
