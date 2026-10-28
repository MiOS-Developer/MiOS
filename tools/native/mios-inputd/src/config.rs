// AI-hint: Layered global input SSOT schema and canonical chord normalization.
// AI-related: usr/share/mios/mios.toml [keybindings.global], tools/native/mios-resolver
use regex::Regex;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;

pub const CONTEXTS: [&str; 6] = [
    "desktop-2d",
    "desktop-3d",
    "terminal",
    "vm-passthrough",
    "game",
    "controller",
];
pub const VIRTUAL_PREFIX: &str = "mios-vctl";

pub use mios_resolver::keybindings::{axis_code, button_code, normalize_chord};

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Binding {
    pub id: String,
    pub tier: u8,
    pub chord: String,
    pub action: String,
    #[serde(default)]
    pub bypass: Option<String>,
    #[serde(default)]
    pub contexts: Vec<String>,
    #[serde(default)]
    pub passthrough: Vec<String>,
    #[serde(default = "host_scope")]
    pub scope: String,
    #[serde(default)]
    pub note: String,
}
fn host_scope() -> String {
    "host".into()
}
impl Binding {
    pub fn active_in(&self, context: &str) -> bool {
        self.scope == "host"
            && (self.contexts.is_empty() || self.contexts.iter().any(|c| c == context))
            && !self.passthrough.iter().any(|c| c == context)
    }
}
#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct ControllerConfig {
    pub enabled: bool,
    pub exclusive_grab: bool,
    pub guide_button: String,
    pub mode_override_chord: Vec<String>,
    pub pointer_hz: u32,
    pub stick_deadzone: f64,
    pub pointer_max_speed_px: f64,
    pub scroll_sensitivity: f64,
    #[serde(default)]
    pub system_layer: BTreeMap<String, String>,
    pub cursor_mode_button: String,
    pub cursor_mode_contexts: Vec<String>,
    pub legacy_pad_state_path: std::path::PathBuf,
    pub debounce_ms: u64,
    pub scan_ms: u64,
    pub recovery_argv: Vec<String>,
}
#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct GlobalConfig {
    pub enabled: bool,
    pub mod_key: String,
    pub bypass_mod: String,
    pub chord_sep: String,
    pub contexts: BTreeMap<String, Vec<String>>,
    #[serde(default)]
    pub reserved_terminal: Vec<String>,
    #[serde(default)]
    pub bindings: Vec<Binding>,
    pub controller: ControllerConfig,
    pub vm: crate::vm::VmConfig,
    #[serde(skip)]
    pub resolved_actions: BTreeMap<String, Vec<String>>,
}
impl GlobalConfig {
    pub fn load(root: Option<&Path>) -> Result<(Self, LegacyConfig), String> {
        Self::from_value(&mios_resolver::resolve_merged(root, false).map_err(|e| e.to_string())?)
    }
    pub fn from_value(v: &toml::Value) -> Result<(Self, LegacyConfig), String> {
        let section = v
            .get("keybindings")
            .and_then(|v| v.get("global"))
            .ok_or("missing [keybindings.global]; publish the schema before enabling inputd")?;
        let mut cfg: Self = section
            .clone()
            .try_into()
            .map_err(|e: toml::de::Error| e.to_string())?;
        cfg.resolve_actions(v)?;
        cfg.validate()?;
        let legacy: LegacyConfig = v
            .get("desktop")
            .and_then(|v| v.get("hyprland"))
            .and_then(|v| v.get("hypr3d"))
            .and_then(|v| v.get("controller"))
            .ok_or("missing native Hypr3D controller map")?
            .clone()
            .try_into()
            .map_err(|e: toml::de::Error| e.to_string())?;
        legacy.validate()?;
        Ok((cfg, legacy))
    }
    fn resolve_actions(&mut self, v: &toml::Value) -> Result<(), String> {
        // Reuse existing desktop launch definitions. A terminal entry must open
        // an emulator, not attach tmux to this daemon's null stdin.
        if let Some(actions) = v
            .get("keybindings")
            .and_then(|v| v.get("actions"))
            .and_then(toml::Value::as_array)
        {
            for action in actions {
                let id = action.get("id").and_then(toml::Value::as_str).unwrap_or("");
                let mapped = match id {
                    "terminal" => Some("exec:mios-terminal"),
                    "system" => Some("quickshell:monitor-toggle"),
                    _ => None,
                };
                if let (Some(mapped), Some(command)) = (
                    mapped,
                    action.get("desktop_command").and_then(toml::Value::as_str),
                ) {
                    self.resolved_actions
                        .insert(mapped.into(), command_argv(command)?);
                }
            }
        }
        if let Some(lock) = v
            .get("desktop")
            .and_then(|v| v.get("topbar"))
            .and_then(|v| v.get("commands"))
            .and_then(|v| v.get("lock"))
            .and_then(toml::Value::as_str)
        {
            self.resolved_actions
                .insert("quickshell:lock".into(), command_argv(lock)?);
        }
        if let Some(ipc) = v
            .get("desktop")
            .and_then(|v| v.get("screenshot"))
            .and_then(|v| v.get("shell_ipc"))
            .and_then(toml::Value::as_array)
        {
            let base = ipc
                .iter()
                .map(|v| {
                    v.as_str()
                        .map(str::to_owned)
                        .ok_or("shell_ipc requires string argv")
                })
                .collect::<Result<Vec<_>, _>>()?;
            if base.is_empty() {
                return Err("empty shell IPC argv".into());
            }
            for (action, target) in [
                ("quickshell:drawer-toggle", "drawer"),
                ("quickshell:launcher", "drawer"),
                ("quickshell:search-toggle", "search"),
                ("quickshell:notifications-toggle", "notifications"),
                ("quickshell:keymap-toggle", "keymap"),
            ] {
                let mut argv = base.clone();
                argv.extend([target.into(), "toggle".into()]);
                self.resolved_actions.insert(action.into(), argv);
            }
        }
        Ok(())
    }
    pub fn classify(&self, class: &str, room: bool) -> String {
        // VM/game classifications outrank room/terminal: application ownership
        // must survive opening the 3D room around a captured VM/game window.
        for context in ["vm-passthrough", "game"] {
            if self.matches(context, class) {
                return context.into();
            }
        }
        if room {
            return "desktop-3d".into();
        }
        if self.matches("terminal", class) {
            "terminal".into()
        } else {
            "desktop-2d".into()
        }
    }
    fn matches(&self, context: &str, class: &str) -> bool {
        self.contexts.get(context).is_some_and(|patterns| {
            patterns
                .iter()
                .any(|p| Regex::new(&format!("^(?:{p})$")).is_ok_and(|r| r.is_match(class)))
        })
    }
    pub fn validate(&self) -> Result<(), String> {
        if self.chord_sep.is_empty() {
            return Err("empty chord_sep".into());
        }
        {
            // Validate shape, not one fixed operator modifier.
            normalize_chord(&format!("{}+a", self.mod_key), &self.chord_sep)?;
            normalize_chord(&format!("{}+a", self.bypass_mod), &self.chord_sep)?;
        }
        for (context, patterns) in &self.contexts {
            if !CONTEXTS.contains(&context.as_str()) {
                return Err(format!("unknown context {context}"));
            }
            for p in patterns {
                Regex::new(&format!("^(?:{p})$")).map_err(|e| e.to_string())?;
            }
        }
        self.vm.validate()?;
        let c = &self.controller;
        if c.recovery_argv.is_empty() || !Path::new(&c.recovery_argv[0]).is_absolute() || c.recovery_argv.iter().any(|a|a.contains('\0')) {
            return Err("controller recovery requires an absolute executable argv".into());
        }
        if !(1..=1000).contains(&c.pointer_hz)
            || !c.stick_deadzone.is_finite()
            || !(0.0..1.0).contains(&c.stick_deadzone)
            || !c.pointer_max_speed_px.is_finite()
            || c.pointer_max_speed_px <= 0.0
            || !c.scroll_sensitivity.is_finite()
            || c.scroll_sensitivity < 0.0
        {
            return Err("invalid controller rate/deadzone/sensitivity".into());
        }
        button_code(&c.guide_button)?;
        button_code(&c.cursor_mode_button)?;
        if c.scan_ms == 0
            || c.scan_ms > 60_000
            || c.debounce_ms == 0
            || !c.legacy_pad_state_path.is_absolute()
            || c.cursor_mode_contexts
                .iter()
                .any(|ctx| !CONTEXTS.contains(&ctx.as_str()))
        {
            return Err("invalid controller mode/timing/path".into());
        }
        if c.mode_override_chord.is_empty() {
            return Err("empty mode override chord".into());
        }
        for b in &c.mode_override_chord {
            button_code(b)?;
        }
        for (button, action) in &c.system_layer {
            button_code(button)?;
            validate_action(action)?;
        }
        let mut ids = BTreeSet::new();
        for b in &self.bindings {
            if b.id.is_empty()
                || !b
                    .id
                    .bytes()
                    .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == b'-')
                || !ids.insert(&b.id)
            {
                return Err(format!("invalid/duplicate binding id {}", b.id));
            }
            if !(1..=4).contains(&b.tier) || !["host", "app"].contains(&b.scope.as_str()) {
                return Err(format!("invalid tier/scope in {}", b.id));
            }
            for ctx in b.contexts.iter().chain(&b.passthrough) {
                if !CONTEXTS.contains(&ctx.as_str()) {
                    return Err(format!("unknown context {ctx}"));
                }
            }
            normalize_chord(&b.chord, &self.chord_sep)?;
            validate_action(&b.action)?;
            if let Some(bypass) = &b.bypass {
                let n = normalize_chord(bypass, &self.chord_sep)?;
                if !n.starts_with(&format!("{}+", self.bypass_mod)) {
                    return Err(format!("bypass must use bypass_mod: {}", b.id));
                }
            }
            if b.active_in("vm-passthrough")
                && !b.chord.starts_with(&format!("{}+", self.bypass_mod))
                && !b.chord.starts_with("BTN_")
            {
                return Err(format!("VM host binding lacks escape modifier: {}", b.id));
            }
            let n = normalize_chord(&b.chord, &self.chord_sep)?;
            if b.active_in("terminal")
                && self.reserved_terminal.iter().any(|r| {
                    normalize_chord(r, &self.chord_sep)
                        .is_ok_and(|r| n == r || n.starts_with(&format!("{r}{}", self.chord_sep)))
                })
            {
                return Err(format!("terminal owns {}", b.chord));
            }
            if b.active_in("terminal")
                && [
                    "CTRL+c", "CTRL+z", "CTRL+d", "CTRL+\\", "CTRL+a", "CTRL+e", "CTRL+k",
                    "CTRL+u", "CTRL+w", "CTRL+r", "CTRL+b", "CTRL+s",
                ]
                .iter()
                .any(|p| n == *p || n.starts_with(&format!("{p}{}", self.chord_sep)))
            {
                return Err(format!("terminal owns {}", b.chord));
            }
        }
        for (i, a) in self.bindings.iter().enumerate() {
            for b in &self.bindings[i + 1..] {
                if a.scope != b.scope || a.scope != "host" {
                    continue;
                }
                let effective = CONTEXTS
                    .iter()
                    .any(|ctx| a.active_in(ctx) && b.active_in(ctx));
                if !effective {
                    continue;
                }
                for ca in std::iter::once(&a.chord).chain(a.bypass.iter()) {
                    for cb in std::iter::once(&b.chord).chain(b.bypass.iter()) {
                        let ca = normalize_chord(ca, &self.chord_sep)?;
                        let cb = normalize_chord(cb, &self.chord_sep)?;
                        if ca == cb
                            || ca.starts_with(&format!("{cb}{}", self.chord_sep))
                            || cb.starts_with(&format!("{ca}{}", self.chord_sep))
                        {
                            return Err(format!("collision: {} and {}", a.id, b.id));
                        }
                    }
                }
            }
        }
        Ok(())
    }
}
pub fn validate_action(action: &str) -> Result<(), String> {
    let (namespace, verb) = action
        .split_once(':')
        .ok_or_else(|| format!("action needs namespace: {action}"))?;
    if !["hypr", "exec", "quickshell", "mios", "tmux", "readline"].contains(&namespace)
        || verb.is_empty()
        || action.chars().any(|c| c.is_control())
    {
        return Err(format!("invalid action {action}"));
    }
    Ok(())
}
#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct LegacyConfig {
    pub enabled: bool,
    pub toggle_chord: Vec<String>,
    pub debounce_ms: u64,
    pub deadzone: f64,
    pub sensitivity: f64,
    pub move_sensitivity: f64,
    pub camera_sensitivity: f64,
    pub invert_y: bool,
    pub dpad_enabled: bool,
    pub poll_ms: u64,
    pub fallback_axes: Vec<String>,
    pub grab_button: String,
    pub grab_axis: String,
    pub resize_button: String,
    pub resize_axis: String,
    pub trigger_threshold: f64,
    pub view_mode_button: String,
    pub cursor_mode_button: String,
    pub jump_button: String,
    pub double_press_ms: u64,
    pub crouch_button: String,
    pub fullscreen_button: String,
    pub shaderglass_toggle_button: String,
    pub shaderglass_cycle_button: String,
}
impl LegacyConfig {
    fn validate(&self) -> Result<(), String> {
        if self.toggle_chord.len() < 2
            || self.fallback_axes.len() != 2
            || self.debounce_ms == 0
            || self.double_press_ms == 0
            || self.poll_ms == 0
            || !self.deadzone.is_finite()
            || !(0.0..=1.0).contains(&self.deadzone)
            || !self.trigger_threshold.is_finite()
            || !(0.0..=1.0).contains(&self.trigger_threshold)
        {
            return Err("invalid legacy controller ranges".into());
        }
        for b in self.toggle_chord.iter().map(String::as_str).chain([
            self.grab_button.as_str(),
            &self.resize_button,
            &self.view_mode_button,
            &self.cursor_mode_button,
            &self.jump_button,
            &self.crouch_button,
            &self.fullscreen_button,
            &self.shaderglass_toggle_button,
            &self.shaderglass_cycle_button,
        ]) {
            button_code(b)?;
        }
        for a in self
            .fallback_axes
            .iter()
            .map(String::as_str)
            .chain([self.grab_axis.as_str(), &self.resize_axis])
        {
            axis_code(a)?;
        }
        for v in [
            self.sensitivity,
            self.move_sensitivity,
            self.camera_sensitivity,
        ] {
            if !v.is_finite() || v < 0.0 {
                return Err("invalid legacy sensitivity".into());
            }
        }
        Ok(())
    }
}

fn command_argv(command: &str) -> Result<Vec<String>, String> {
    let args = shlex::split(command).ok_or("malformed desktop command quoting")?;
    if args.is_empty() || args.iter().any(|s| s.contains('\0')) {
        return Err("empty/invalid desktop command argv".into());
    }
    if args
        .iter()
        .any(|s| ["|", "||", "&&", ";", ">", "<", "&"].contains(&s.as_str()))
    {
        return Err("input action needs a direct executable argv, not shell operators".into());
    }
    Ok(args)
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    pub fn fixture() -> (GlobalConfig, LegacyConfig) {
        let mut v: toml::Value = include_str!("../../../../usr/share/mios/mios.toml")
            .parse()
            .unwrap();
        let section: toml::Value = r#"
[keybindings.global]
enabled=true
mod_key="SUPER"
bypass_mod="SUPER+CTRL"
chord_sep=" "
[keybindings.global.contexts]
terminal=["Alacritty"]
vm-passthrough=["looking-glass-client"]
game=["steam_app_.*"]
[keybindings.global.controller]
enabled=true
exclusive_grab=false
guide_button="BTN_MODE"
mode_override_chord=["BTN_MODE","BTN_SELECT"]
pointer_hz=250
stick_deadzone=0.12
pointer_max_speed_px=18.0
scroll_sensitivity=45.0
cursor_mode_button="BTN_START"
cursor_mode_contexts=["desktop-2d","desktop-3d"]
legacy_pad_state_path="/tmp/mios-hypr3d-pad.bin"
debounce_ms=250
scan_ms=2000
recovery_argv=["/usr/libexec/mios/mios-gamepad-hypr3d","--daemon"]
[keybindings.global.controller.system_layer]
BTN_TL="hypr:workspace e-1"
BTN_TR="hypr:workspace e+1"
"#
        .parse()
        .unwrap();
        let vm = v["keybindings"]["global"]["vm"].clone();
        v["keybindings"]
            .as_table_mut()
            .unwrap()
            .insert("global".into(), section["keybindings"]["global"].clone());
        v["keybindings"]["global"].as_table_mut().unwrap().insert("vm".into(), vm);
        GlobalConfig::from_value(&v).unwrap()
    }
    #[test]
    fn canonical_modifier_order() {
        assert_eq!(
            normalize_chord("shift+ctrl+Super+b", " ").unwrap(),
            "SUPER+CTRL+SHIFT+b"
        );
    }
    #[test]
    fn modifier_alias() {
        assert_eq!(
            normalize_chord("SUPER_CTRL+Space", " ").unwrap(),
            "SUPER+CTRL+space"
        );
    }
    #[test]
    fn multiple_steps() {
        assert_eq!(
            normalize_chord("ctrl+x ctrl+Return", " ").unwrap(),
            "CTRL+x CTRL+return"
        );
    }
    #[test]
    fn button_names_keep_underscores() {
        assert_eq!(
            normalize_chord("BTN_TL+BTN_MODE", " ").unwrap(),
            "BTN_MODE+BTN_TL"
        );
    }
    #[test]
    fn button_aliases_are_identical() {
        assert_eq!(
            normalize_chord("BTN_A", " ").unwrap(),
            normalize_chord("BTN_SOUTH", " ").unwrap()
        );
    }
    #[test]
    fn malformed_chords_fail() {
        for c in ["", "CTRL", "CTRL+CTRL+a", "SUPER++b", "a+b", "BTN_BAD"] {
            assert!(normalize_chord(c, " ").is_err(), "{c}");
        }
    }
    #[test]
    fn context_priority_and_anchoring() {
        let (c, _) = fixture();
        assert_eq!(c.classify("steam_app_123", true), "game");
        assert_eq!(c.classify("looking-glass-client", true), "vm-passthrough");
        assert_eq!(c.classify("Alacritty", false), "terminal");
        assert_eq!(c.classify("not-Alacritty", false), "desktop-2d");
    }
    fn binding(id: &str, chord: &str) -> Binding {
        Binding {
            id: id.into(),
            tier: 1,
            chord: chord.into(),
            action: "mios:launcher".into(),
            bypass: None,
            contexts: vec!["desktop-2d".into()],
            passthrough: vec![],
            scope: "host".into(),
            note: String::new(),
        }
    }
    #[test]
    fn collisions_fail() {
        let (mut c, _) = fixture();
        c.bindings = vec![
            binding("one", "CTRL+SUPER+b"),
            binding("two", "SUPER+CTRL+b"),
        ];
        assert!(c.validate().unwrap_err().contains("collision"));
    }
    #[test]
    fn prefix_collision_fails() {
        let (mut c, _) = fixture();
        c.bindings = vec![binding("one", "CTRL+x"), binding("two", "CTRL+x CTRL+b")];
        assert!(c.validate().is_err());
    }
    #[test]
    fn distinct_contexts_do_not_collide() {
        let (mut c, _) = fixture();
        let a = binding("one", "SUPER+b");
        let mut b = binding("two", "SUPER+b");
        b.contexts = vec!["desktop-3d".into()];
        c.bindings = vec![a, b];
        c.validate().unwrap();
    }
    #[test]
    fn terminal_defaults_owned_by_app() {
        let (mut c, _) = fixture();
        let mut b = binding("bad", "CTRL+b");
        b.contexts = vec!["terminal".into()];
        c.bindings = vec![b];
        assert!(c.validate().unwrap_err().contains("terminal owns"));
    }
    #[test]
    fn invalid_regex_and_nan_rejected() {
        let (mut c, _) = fixture();
        c.contexts.insert("terminal".into(), vec!["[".into()]);
        assert!(c.validate().is_err());
        let (mut c, _) = fixture();
        c.controller.stick_deadzone = f64::NAN;
        assert!(c.validate().is_err());
    }
}

#[cfg(test)]
mod shared_conformance {
    use super::*;
    #[test]
    fn gate_and_daemon_share_chord_vectors() {
        let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../..");
        let text = std::fs::read_to_string(root.join("usr/share/mios/keybinds/chord-vectors.json"))
            .expect("shared chord vectors must ship with the source");
        let v: serde_json::Value = serde_json::from_str(&text).unwrap();
        let cases = v["cases"].as_array().unwrap();
        assert!(cases.len() >= 12);
        for case in cases {
            let chord = case["input"].as_str().unwrap();
            let actual = normalize_chord(chord, " ");
            if let Some(expected) = case.get("expect").and_then(|v| v.as_array()) {
                let expected = expected
                    .iter()
                    .map(|v| v.as_str().unwrap())
                    .collect::<Vec<_>>()
                    .join(" ");
                assert_eq!(actual.unwrap(), expected, "{chord}");
            } else {
                assert!(actual.is_err(), "must reject: {chord}");
            }
        }
    }
}

#[cfg(test)]
mod action_tests {
    use super::*;
    #[test]
    fn quotes_are_argv_not_shell() {
        assert_eq!(
            command_argv("terminal --title 'Two words' -e /bin/true").unwrap(),
            vec!["terminal", "--title", "Two words", "-e", "/bin/true"]
        );
    }
    #[test]
    fn malformed_and_shell_pipelines_are_rejected() {
        for c in ["", "tool 'unterminated", "tool | other", "tool && other"] {
            assert!(command_argv(c).is_err(), "{c}");
        }
    }
    #[test]
    fn desktop_terminal_and_monitor_are_resolved_from_existing_registry() {
        let (c, _) = super::tests::fixture();
        assert!(c.resolved_actions["exec:mios-terminal"]
            .iter()
            .any(|s| s == "-e"));
        assert!(c.resolved_actions["quickshell:monitor-toggle"]
            .iter()
            .any(|s| s == "system"));
        assert_eq!(c.resolved_actions["quickshell:lock"], vec!["hyprlock"]);
    }
}
