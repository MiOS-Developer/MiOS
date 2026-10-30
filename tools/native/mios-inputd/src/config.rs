// AI-hint: Layered global input SSOT schema and canonical chord normalization.
// AI-related: usr/share/mios/mios.toml [keybindings.global], tools/native/mios-resolver
use regex::Regex;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;

pub const CONTEXTS: [&str; 8] = [
    "desktop-2d",
    "desktop-3d",
    "terminal",
    "vm-passthrough",
    "game",
    "controller",
    "app-capture",
    // I3 (C-16): no compositor, or this user's active seat session is a tty. Not a window class.
    "console",
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
    /// R-09: a row without `contexts` is active in every context except `console` (no compositor runs there).
    pub fn active_in(&self, context: &str) -> bool {
        self.scope == "host"
            && ((self.contexts.is_empty() && context != "console") || self.contexts.iter().any(|c| c == context))
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
    pub captured_system_layer: bool,
    pub capture_fullscreen: bool,
    pub capture_tag: String,
    /// Set by a held toggle_chord release; cancels fullscreen==2, capture_tag and the room input_owner for that
    /// window (every window kind alike) until the next North capture removes it.
    pub release_tag: String,
    /// Marks a fullscreen the bridge itself set (North hold); a release unsets only that fullscreen.
    pub capture_fullscreen_tag: String,
    /// Retired (I1): serve never execs a fallback owner. Present only so a planted key is rejected, not ignored.
    #[serde(default, skip_serializing)]
    pub recovery_argv: Option<toml::Value>,
    pub context_poll_ms: u64,
    pub context_stale_ms: u64,
    #[serde(default = "capture_hold_ms")]
    pub capture_hold_ms: u64,
    /// C-32: notify on every capture and release ("Controller -> <app>. Hold L3+R3 to release.").
    #[serde(default)]
    pub capture_osd: bool,
    /// The notifier argv; one argument carries `{message}`. Runs off the forwarding loop after the compositor
    /// confirmed the ownership transaction.
    #[serde(default)]
    pub capture_osd_argv: Vec<String>,
    /// The raw [keybindings.global.controller.nav] table (serialized so a policy edit reloads every router).
    #[serde(default, rename = "nav")]
    pub nav_table: Option<toml::Value>,
    /// The resolved nav projection (step I2): profiles with `inherits` applied.
    #[serde(skip)]
    pub nav: crate::nav::NavConfig,
    /// `[keybindings.global.controller.room]` (spec P1, C-14b): the room's additions to the Hypr3D map. A value is an
    /// action fired on press (on the toggle_chord buttons and Guide: on a clean release/tap) or `[tap, hold]`
    /// (tap on release before capture_hold_ms, hold once at it). `"none"` unbinds.
    #[serde(default)]
    pub room: BTreeMap<String, RoomAction>,
    /// I3: the Linux console (VT) side: the pty bridge's pointer source (gpm or evdev) for tmux-os mouse reports.
    #[serde(default)]
    pub console: ConsoleConfig,
    /// I4 (O-7): `[keybindings.global.controller.quirks.<name>]` per-driver code fixups. MiOS maps by evdev codes; a
    /// quirk names a kernel driver (sysfs) or USB ids and remaps the codes that driver reports into the positional
    /// Linux gamepad layout (BTN_NORTH = top face button, BTN_DPAD_* = the d-pad).
    #[serde(default)]
    pub quirks: BTreeMap<String, Quirk>,
    /// I4: the agent virtual controller (`mios-inputd vpad`): a per-session uinput pad driven with eased analog curves.
    #[serde(default)]
    pub agent_pad: AgentPadConfig,
}
/// One per-driver quirk (O-7). `drivers` are kernel driver names (`/sys/class/input/eventN/device/device/driver`),
/// `ids` are `vvvv:pppp` USB ids (hex); either matches. `remap` maps a reported code to the positional code: keys are
/// button names or hex codes ("0x2c0" = BTN_TRIGGER_HAPPY1), values are button names. The map must be one-to-one so
/// the captured app still receives the driver's own codes (the virtual pad is a clone of the device).
#[derive(Clone, Debug, Default, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Quirk {
    /// A higher tier turns a vendor quirk off with `enabled = false` (tables merge per key, so an empty remap could not).
    #[serde(default = "yes")]
    pub enabled: bool,
    #[serde(default)]
    pub drivers: Vec<String>,
    #[serde(default)]
    pub ids: Vec<String>,
    #[serde(default)]
    pub remap: BTreeMap<String, String>,
    #[serde(default)]
    pub note: String,
}
fn yes() -> bool {
    true
}
/// A remap key: a button name or a hex evdev key code (0x100..=0x2ff).
pub fn quirk_code(name: &str) -> Result<u16, String> {
    if let Some(hex) = name.strip_prefix("0x") {
        let c = u16::from_str_radix(hex, 16).map_err(|_| format!("quirk code {name}: hex evdev key code"))?;
        return if (0x100..=0x2ff).contains(&c) { Ok(c) } else { Err(format!("quirk code {name}: outside the evdev button range 0x100..0x2ff")) };
    }
    let c = button_code(name)?;
    if mios_resolver::keybindings::is_stick_pseudo(c) {
        return Err(format!("quirk code {name}: a stick direction is not a reported code"));
    }
    Ok(c)
}
impl Quirk {
    /// The reported -> positional code map.
    pub fn codes(&self) -> Result<BTreeMap<u16, u16>, String> {
        let mut out = BTreeMap::new();
        let mut targets = BTreeSet::new();
        for (from, to) in &self.remap {
            let (f, t) = (quirk_code(from)?, quirk_code(to)?);
            if !targets.insert(t) {
                return Err(format!("quirk remap: {to} is the target of two codes (the map must be one-to-one)"));
            }
            out.insert(f, t);
        }
        Ok(out)
    }
    pub fn matches(&self, driver: Option<&str>, vendor: u16, product: u16) -> bool {
        let id = format!("{vendor:04x}:{product:04x}");
        self.enabled && (driver.is_some_and(|d| self.drivers.iter().any(|x| x == d)) || self.ids.iter().any(|x| x.eq_ignore_ascii_case(&id)))
    }
}
/// The remap for one device: every matching quirk's map (later names win on a shared code), as (forward, inverse).
pub fn quirk_maps(quirks: &BTreeMap<String, Quirk>, driver: Option<&str>, vendor: u16, product: u16) -> (Vec<String>, BTreeMap<u16, u16>) {
    let mut names = vec![];
    let mut map = BTreeMap::new();
    for (name, q) in quirks {
        if q.matches(driver, vendor, product) {
            if let Ok(m) = q.codes() {
                names.push(name.clone());
                map.extend(m);
            }
        }
    }
    (names, map)
}
/// [keybindings.global.controller.agent_pad] (I4): the agent virtual controller. A pad is a uinput gamepad held by one
/// `mios-inputd vpad hold` process; every stick/trigger change is an eased ramp sampled at `rate_hz`, never a jump.
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(default, deny_unknown_fields)]
pub struct AgentPadConfig {
    pub enabled: bool,
    /// evdev name of an unseated pad (it drives the session it is created in); `{id}` is the pad id. A seat's pad is
    /// named by [desktop.nested.devices].virtual_pad_name, so `mios-gen nested-session --device virtual-pad` binds it.
    pub name: String,
    /// `vvvv:pppp` USB ids the pad reports (BUS_VIRTUAL).
    pub usb_id: String,
    /// Motion sample rate.
    pub rate_hz: u32,
    /// Default curve: linear, in, out or in-out (cubic smoothstep: zero velocity at both ends).
    pub ease: String,
    /// Default ramp length; `min_ms` is the floor every ramp is stretched to, so nothing ever teleports.
    pub ramp_ms: u64,
    pub min_ms: u64,
    pub max_ms: u64,
    /// A button tap's press length.
    pub tap_ms: u64,
    /// The holder exits (destroying the pad) after this long without a command while every axis is at rest.
    pub idle_exit_s: u64,
    /// Under $XDG_RUNTIME_DIR: one control socket and state file per pad.
    pub socket_dir: String,
}
impl Default for AgentPadConfig {
    fn default() -> Self {
        Self {
            enabled: true,
            name: "MiOS Agent Pad {id}".into(),
            usb_id: "1209:4d50".into(),
            rate_hz: 250,
            ease: "in-out".into(),
            ramp_ms: 400,
            min_ms: 80,
            max_ms: 10_000,
            tap_ms: 80,
            idle_exit_s: 900,
            socket_dir: "mios-inputd/vpad".into(),
        }
    }
}
pub const EASES: [&str; 4] = ["linear", "in", "out", "in-out"];
impl AgentPadConfig {
    pub fn usb(&self) -> Result<(u16, u16), String> {
        let (v, p) = self.usb_id.split_once(':').ok_or("agent_pad.usb_id: vvvv:pppp")?;
        let h = |s: &str| u16::from_str_radix(s, 16).map_err(|_| "agent_pad.usb_id: hex vvvv:pppp".to_string());
        Ok((h(v)?, h(p)?))
    }
    pub fn validate(&self) -> Result<(), String> {
        self.usb()?;
        if !self.name.contains("{id}") || self.name.len() > 60 || self.name.chars().any(char::is_control) || self.name.starts_with(VIRTUAL_PREFIX) {
            return Err("agent_pad.name: a printable evdev name with {id} (not in the mios-vctl namespace)".into());
        }
        if !(10..=1000).contains(&self.rate_hz) || !EASES.contains(&self.ease.as_str()) {
            return Err("agent_pad: rate_hz 10..=1000 and ease linear|in|out|in-out".into());
        }
        if self.min_ms == 0 || self.min_ms > self.ramp_ms || self.ramp_ms > self.max_ms || self.max_ms > 60_000 || !(10..=2000).contains(&self.tap_ms) {
            return Err("agent_pad: 0 < min_ms <= ramp_ms <= max_ms <= 60000 and tap_ms 10..=2000".into());
        }
        if !(10..=86_400).contains(&self.idle_exit_s) {
            return Err("agent_pad.idle_exit_s: 10..=86400".into());
        }
        let p = Path::new(&self.socket_dir);
        if self.socket_dir.is_empty() || p.is_absolute() || !p.components().all(|c| matches!(c, std::path::Component::Normal(_))) {
            return Err("agent_pad.socket_dir: a relative path under XDG_RUNTIME_DIR without '..'".into());
        }
        Ok(())
    }
}
/// [keybindings.global.controller.console] (I3, C-18). The console bridge wraps a VT tmux client in a pty and turns
/// pointer events into mouse reports (SGR 1006, or the legacy encoding) only while the client has mouse mode on.
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(default)]
pub struct ConsoleConfig {
    /// mios-terminal wraps a local VT tmux client in `mios-inputd console-bridge`.
    pub bridge: bool,
    /// Pointer source: "auto" (gpm when its socket exists, else evdev), "gpm", "evdev" or "off".
    pub mouse: String,
    /// The gpm client socket (the daemon draws the console pointer; MiOS reads its events while mouse mode is on).
    pub gpm_socket: std::path::PathBuf,
    /// evdev source: pointer devices by name, read without a grab (the controller's cursor-mode virtual pointer).
    pub pointer_names: Vec<String>,
    /// evdev source: pointer pixels per character cell (x, y).
    pub cell_px: [u32; 2],
    /// tmux:keymap: the popup argv; `{file}` is the rendered controller map (`mios-inputd map --text`).
    pub keymap_argv: Vec<String>,
}
impl Default for ConsoleConfig {
    fn default() -> Self {
        Self {
            bridge: true,
            mouse: "auto".into(),
            gpm_socket: "/dev/gpmctl".into(),
            pointer_names: vec!["mios-vctl-pointer".into()],
            cell_px: [8, 16],
            keymap_argv: vec!["/usr/bin/less".into(), "-R".into(), "{file}".into()],
        }
    }
}
impl ConsoleConfig {
    pub fn validate(&self) -> Result<(), String> {
        if !["auto", "gpm", "evdev", "off"].contains(&self.mouse.as_str()) {
            return Err("controller.console.mouse: auto, gpm, evdev or off".into());
        }
        if !self.gpm_socket.is_absolute() || self.cell_px.iter().any(|v| !(1..=256).contains(v)) {
            return Err("controller.console: gpm_socket is absolute and cell_px is 1..=256".into());
        }
        if self.pointer_names.iter().any(|n| n.is_empty() || n.len() >= 80) {
            return Err("controller.console.pointer_names: evdev names (1..79 bytes)".into());
        }
        if self.keymap_argv.first().is_none_or(|p| !p.starts_with('/')) || !self.keymap_argv.iter().any(|a| a.contains("{file}"))
            || self.keymap_argv.iter().any(|a| a.contains('\0')) {
            return Err("controller.console.keymap_argv: an absolute program and one argument carrying {file}".into());
        }
        Ok(())
    }
}
fn capture_hold_ms() -> u64 { 500 }
fn default_namespaces() -> Vec<String> {
    ["hypr", "exec", "quickshell", "mios", "tmux", "readline"].into_iter().map(str::to_owned).collect()
}
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(untagged)]
pub enum RoomAction {
    Press(String),
    TapHold(Vec<String>),
}
impl RoomAction {
    /// (tap-or-press, hold)
    pub fn parts(&self) -> (&str, Option<&str>) {
        match self {
            Self::Press(a) => (a, None),
            Self::TapHold(v) => (v.first().map_or("", String::as_str), v.get(1).map(String::as_str)),
        }
    }
}
/// A room action: `"none"`, or a valid action whose `hypr:` form the room dispatcher whitelists (runtime.rs).
pub fn validate_room_action(action: &str) -> Result<(), String> {
    if action == "none" {
        return Ok(());
    }
    validate_action(action)?;
    if let Some(verb) = action.strip_prefix("hypr:hypr3d:") {
        let name = verb.split_whitespace().next().unwrap_or("");
        if !ROOM_VERBS.contains(&name) {
            return Err(format!("unsupported room action {name}"));
        }
    }
    Ok(())
}
/// The Hypr3D verbs the compositor dispatcher may call (hl.plugin.hypr3d.<verb>); one list for validation and runtime.
pub const ROOM_VERBS: [&str; 13] = [
    "mode", "view", "crt", "grab", "resize", "fly", "posture", "click", "fullscreen", "jump", "cancel", "sprint", "recenter",
];
#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct GlobalConfig {
    pub enabled: bool,
    pub mod_key: String,
    pub bypass_mod: String,
    pub chord_sep: String,
    /// Every action is `<namespace>:<verb>` with a namespace from this SSOT list (key: and pointer: since I2).
    #[serde(default = "default_namespaces")]
    pub action_namespaces: Vec<String>,
    pub contexts: BTreeMap<String, Vec<String>>,
    #[serde(default)]
    pub reserved_terminal: Vec<String>,
    #[serde(default)]
    pub bindings: Vec<Binding>,
    pub controller: ControllerConfig,
    pub vm: crate::vm::VmConfig,
    #[serde(default)]
    pub action_argv: BTreeMap<String, Vec<String>>,
    #[serde(default)]
    pub planned_actions: Vec<String>,
    #[serde(default)]
    pub native_actions: BTreeMap<String, String>,
    #[serde(skip)]
    pub resolved_actions: BTreeMap<String, Vec<String>>,
    /// [keybindings].tmux_prefix: the prefix `tmux:key` sends with `send-keys -K`.
    #[serde(default)]
    pub tmux_prefix: String,
    /// The tmux prefix-table keys the SSOT binds ([keybindings.actions].key and [keybindings.tmux].bindings[].key).
    #[serde(default)]
    pub tmux_prefix_keys: BTreeSet<String>,
    /// [keybindings.tmux].menu_key (U1): prefix + this key opens the generated MiOS menu; `tmux:menu` sends it.
    #[serde(default)]
    pub tmux_menu_key: Option<String>,
    /// I4: [desktop.nested].registry_dir (under XDG_RUNTIME_DIR): live nested sessions and the input nodes assigned to
    /// them. The host bridge never opens an assigned node; `bridge --nested <seat>` opens only those.
    #[serde(default)]
    pub nested_registry: String,
    /// I4: [desktop.nested.devices].virtual_pad_name: a seat's agent pad (`{seat}`); the host bridge never owns one.
    #[serde(default)]
    pub seat_pad_name: String,
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
        cfg.resolve_tmux(v);
        let nested = v.get("desktop").and_then(|d| d.get("nested"));
        cfg.nested_registry = nested.and_then(|n| n.get("registry_dir")).and_then(toml::Value::as_str).unwrap_or("mios/nested-sessions").to_owned();
        cfg.seat_pad_name = nested.and_then(|n| n.get("devices")).and_then(|d| d.get("virtual_pad_name")).and_then(toml::Value::as_str)
            .unwrap_or("MiOS Nested Pad {seat}").to_owned();
        cfg.validate()?;
        cfg.controller.nav = crate::nav::NavConfig::from_value(cfg.controller.nav_table.as_ref(), &crate::nav::Ctx {
            namespaces: &cfg.action_namespaces,
            tmux_prefix_keys: &cfg.tmux_prefix_keys,
            contexts: &CONTEXTS,
            deadzone: cfg.controller.stick_deadzone,
        })?;
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
        // The ownership contract is not a room binding: North captures, the toggle_chord (as a chord) toggles/releases.
        for button in cfg.controller.room.keys() {
            if button_code(button)? == button_code(&legacy.fullscreen_button)? {
                return Err(format!("room {button}: the capture button ({}) belongs to the ownership contract", legacy.fullscreen_button));
            }
            if let RoomAction::TapHold(_) = cfg.controller.room[button] {
                if legacy.toggle_chord.iter().any(|b| button_code(b).ok() == button_code(button).ok()) {
                    return Err(format!("room {button}: a toggle_chord button fires one action on a clean release, not [tap, hold]"));
                }
            }
        }
        Ok((cfg, legacy))
    }
    fn resolve_tmux(&mut self, v: &toml::Value) {
        let kb = v.get("keybindings");
        self.tmux_prefix = kb.and_then(|k| k.get("tmux_prefix")).and_then(toml::Value::as_str).unwrap_or("C-b").to_owned();
        let keys = |arr: Option<&toml::Value>| -> Vec<String> {
            arr.and_then(toml::Value::as_array).into_iter().flatten()
                .filter_map(|a| a.get("key").and_then(toml::Value::as_str).map(str::to_owned)).collect()
        };
        self.tmux_menu_key = kb.and_then(|k| k.get("tmux")).and_then(|t| t.get("menu_key")).and_then(toml::Value::as_str).map(str::to_owned);
        // Every prefix-table key the SSOT binds: actions, tmux bindings, the menu/launcher keys, the float keys and the
        // agent accept key (mios-unit-gen projects exactly these into the prefix table).
        let tmux = kb.and_then(|k| k.get("tmux"));
        let named = |t: Option<&toml::Value>, names: &[&str]| -> Vec<String> {
            names.iter().filter_map(|n| t.and_then(|t| t.get(*n)).and_then(toml::Value::as_str).map(str::to_owned)).collect()
        };
        let float_keys: Vec<String> = tmux.and_then(|t| t.get("float")).and_then(toml::Value::as_table).into_iter().flatten()
            .filter(|(k, _)| k.ends_with("_key")).filter_map(|(_, v)| v.as_str().map(str::to_owned)).collect();
        self.tmux_prefix_keys = keys(kb.and_then(|k| k.get("actions"))).into_iter()
            .chain(keys(tmux.and_then(|t| t.get("bindings"))))
            .chain(named(tmux, &["menu_key", "launcher_key"]))
            .chain(float_keys)
            .chain(named(kb.and_then(|k| k.get("agent")), &["accept_key"]))
            .collect();
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
                    // C-19 (I4): the system monitor is a MiOS action, not a shell IPC target.
                    "system" => Some("mios:system-monitor"),
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
        for (action, argv) in &self.action_argv {
            validate_action_in(action, &self.action_namespaces)?;
            if argv.is_empty() || argv.iter().any(|a| a.is_empty() || a.contains('\0')) {
                return Err(format!("invalid action argv: {action}"));
            }
            if self.planned_actions.contains(action) { return Err(format!("action both implemented and planned: {action}")); }
            self.resolved_actions.insert(action.clone(),argv.clone());
        }
        for (action,backend) in &self.native_actions {
            if action != "mios:vm-command-modifier" || backend != "hyprland" || self.action_argv.contains_key(action) || self.planned_actions.contains(action) {
                return Err(format!("unsupported native action realizer: {action}"));
            }
        }
        Ok(())
    }
    pub fn classify(&self, class: &str, room: bool) -> String {
        // The room owns unselected windows. Explicit native capture is
        // evaluated first by classify_window, never guessed from app class.
        if room { return "desktop-3d".into(); }
        for context in ["vm-passthrough", "game"] {
            if self.matches(context,class) { return context.into(); }
        }
        if self.matches("terminal", class) {
            "terminal".into()
        } else {
            "desktop-2d".into()
        }
    }
    pub fn classify_window(&self,class:Option<&str>,room:bool,captured:bool) -> String {
        if captured { return "app-capture".into(); }
        class.map_or_else(|| if room {"desktop-3d".into()} else {"desktop-2d".into()},|c|self.classify(c,room))
    }
    fn matches(&self, context: &str, class: &str) -> bool {
        self.contexts.get(context).is_some_and(|patterns| {
            patterns
                .iter()
                .any(|p| Regex::new(&format!("^(?:{p})$")).is_ok_and(|r| r.is_match(class)))
        })
    }
    pub fn validate(&self) -> Result<(), String> {
        if !(100..=3000).contains(&self.controller.capture_hold_ms) {
            return Err("capture_hold_ms must be 100..=3000".into());
        }
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
        let tag_ok=|t:&str| !t.is_empty() && t.len()<=64 && t.bytes().all(|b|b.is_ascii_alphanumeric() || b==b'-' || b==b'_');
        if c.context_poll_ms==0 || c.context_poll_ms>1000 || c.context_stale_ms<c.context_poll_ms || c.context_stale_ms>10000
            || !tag_ok(&c.capture_tag) || !tag_ok(&c.release_tag) || !tag_ok(&c.capture_fullscreen_tag) {
            return Err("invalid capture/context timing policy".into());
        }
        if c.capture_tag==c.release_tag || c.capture_tag==c.capture_fullscreen_tag || c.release_tag==c.capture_fullscreen_tag {
            return Err("capture_tag, release_tag and capture_fullscreen_tag must be distinct".into());
        }
        if self.action_namespaces.is_empty() || self.action_namespaces.iter().any(|n| n.is_empty() || n == "input" || !n.bytes().all(|b| b.is_ascii_lowercase() || b.is_ascii_digit())) {
            return Err("action_namespaces: lowercase names; input: is reserved for the bridge's own ownership verbs".into());
        }
        if c.capture_osd && (c.capture_osd_argv.is_empty() || !c.capture_osd_argv[0].starts_with('/')
            || c.capture_osd_argv.iter().any(|a| a.contains('\0')) || !c.capture_osd_argv.iter().any(|a| a.contains("{message}"))) {
            return Err("capture_osd_argv: an absolute program and one argument carrying {message}".into());
        }
        c.console.validate()?;
        c.agent_pad.validate()?;
        for (name, q) in &c.quirks {
            q.codes().map_err(|e| format!("controller.quirks.{name}: {e}"))?;
            if q.drivers.is_empty() && q.ids.is_empty() {
                return Err(format!("controller.quirks.{name}: names no driver and no USB id"));
            }
            for id in &q.ids {
                let ok = id.split_once(':').is_some_and(|(a, b)| a.len() == 4 && b.len() == 4 && u16::from_str_radix(a, 16).is_ok() && u16::from_str_radix(b, 16).is_ok());
                if !ok { return Err(format!("controller.quirks.{name}: id {id:?} must be vvvv:pppp (hex)")); }
            }
        }
        let nested = Path::new(&self.nested_registry);
        if nested.is_absolute() || !nested.components().all(|c| matches!(c, std::path::Component::Normal(_))) || self.nested_registry.is_empty() {
            return Err("desktop.nested.registry_dir: a relative path without '..'".into());
        }
        if !self.seat_pad_name.contains("{seat}") || self.seat_pad_name.len() > 60 || self.seat_pad_name.chars().any(char::is_control) {
            return Err("desktop.nested.devices.virtual_pad_name: a printable evdev name with {seat}".into());
        }
        if c.recovery_argv.is_some() {
            return Err("controller recovery_argv is retired: serve exits or idles instead of starting a second pad owner".into());
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
        // Stick pseudo-keys (LS_*/RS_*, I4) name stick flicks of the system layer only; no chord or role takes them.
        let real = |b: &str| -> Result<u16, String> {
            let code = button_code(b)?;
            if mios_resolver::keybindings::is_stick_pseudo(code) { return Err(format!("{b} is a stick direction, not a button")); }
            Ok(code)
        };
        real(&c.guide_button)?;
        real(&c.cursor_mode_button)?;
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
            real(b)?;
        }
        for (button, action) in &c.system_layer {
            // A button (Guide + button) or, since I4, a stick direction (Guide + one flick: LS_UP, RS_LEFT, ...).
            if button_code(button)? == real(&c.guide_button)? {
                return Err(format!("system_layer {button}: the guide button is the layer's modifier"));
            }
            validate_action_in(action, &self.action_namespaces)?;
        }
        for (button, value) in &c.room {
            let code = real(button).map_err(|e| format!("room {button}: {e}"))?;
            let (tap, hold) = value.parts();
            if let RoomAction::TapHold(v) = value {
                if v.len() != 2 {
                    return Err(format!("room {button}: [tap, hold] needs exactly two actions"));
                }
            }
            validate_room_action(tap).map_err(|e| format!("room {button}: {e}"))?;
            if let Some(hold) = hold {
                validate_room_action(hold).map_err(|e| format!("room {button}: {e}"))?;
                if code == button_code(&c.guide_button)? {
                    return Err(format!("room {button}: Guide is a tap (its hold is the system layer)"));
                }
            }
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
            validate_action_in(&b.action, &self.action_namespaces)?;
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
    validate_action_in(action, &default_namespaces())
}
/// An action is `<namespace>:<verb>` with a namespace from [keybindings.global].action_namespaces.
pub fn validate_action_in(action: &str, namespaces: &[String]) -> Result<(), String> {
    let (namespace, verb) = action
        .split_once(':')
        .ok_or_else(|| format!("action needs namespace: {action}"))?;
    if !namespaces.iter().any(|n| n == namespace)
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
    pub deadzone: f64,
    pub sensitivity: f64,
    pub move_sensitivity: f64,
    pub camera_sensitivity: f64,
    pub invert_y: bool,
    pub dpad_enabled: bool,
    pub fallback_axes: Vec<String>,
    pub grab_button: String,
    pub grab_axis: String,
    pub resize_button: String,
    pub resize_axis: String,
    pub trigger_threshold: f64,
    pub view_mode_button: String,
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
            || self.double_press_ms == 0
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
cursor_mode_contexts=["desktop-2d","desktop-3d","terminal"]
legacy_pad_state_path="/tmp/mios-hypr3d-pad.bin"
debounce_ms=250
scan_ms=2000
captured_system_layer=false
capture_fullscreen=true
capture_tag="mios-input-captured"
release_tag="mios-input-released"
capture_fullscreen_tag="mios-input-fullscreen"
context_poll_ms=50
context_stale_ms=1000
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
    /// The fixture plus the vendor nav tables, key:/pointer: namespaces and the vendor tmux prefix keys (step I2).
    pub fn fixture_nav() -> (GlobalConfig, LegacyConfig) {
        fixture_nav_with(|_| {})
    }
    pub fn fixture_nav_with(edit: impl FnOnce(&mut toml::Value)) -> (GlobalConfig, LegacyConfig) {
        try_fixture_nav_with(edit).unwrap()
    }
    pub fn try_fixture_nav_with(edit: impl FnOnce(&mut toml::Value)) -> Result<(GlobalConfig, LegacyConfig), String> {
        let vendor: toml::Value = include_str!("../../../../usr/share/mios/mios.toml").parse().unwrap();
        let (cfg, _) = fixture();
        let mut v = vendor.clone();
        let mut global = toml::Value::try_from(&cfg).unwrap();
        let g = global.as_table_mut().unwrap();
        g.remove("tmux_prefix"); g.remove("tmux_prefix_keys"); g.remove("tmux_menu_key");
        g.insert("action_namespaces".into(), vendor["keybindings"]["global"]["action_namespaces"].clone());
        let c = g.get_mut("controller").unwrap().as_table_mut().unwrap();
        c.insert("nav".into(), vendor["keybindings"]["global"]["controller"]["nav"].clone());
        c.insert("cursor_mode_contexts".into(), vendor["keybindings"]["global"]["controller"]["cursor_mode_contexts"].clone());
        v["keybindings"].as_table_mut().unwrap().insert("global".into(), global);
        edit(&mut v);
        GlobalConfig::from_value(&v)
    }
    fn vendor_with_room(extra: &str) -> Result<(GlobalConfig, LegacyConfig), String> {
        let mut v: toml::Value = include_str!("../../../../usr/share/mios/mios.toml").parse().unwrap();
        let room: toml::Value = extra.parse().unwrap();
        v["keybindings"]["global"]["controller"]
            .as_table_mut()
            .unwrap()
            .insert("room".into(), room);
        GlobalConfig::from_value(&v)
    }
    fn vendor_with(edit: impl FnOnce(&mut toml::Value)) -> Result<(GlobalConfig, LegacyConfig), String> {
        let mut v: toml::Value = include_str!("../../../../usr/share/mios/mios.toml").parse().unwrap();
        edit(&mut v);
        GlobalConfig::from_value(&v)
    }
    fn controller(v: &mut toml::Value) -> &mut toml::value::Table {
        v["keybindings"]["global"]["controller"].as_table_mut().unwrap()
    }
    #[test]
    fn vendor_quirks_map_xpad_to_the_positional_layout() {
        let (g, _) = vendor_with(|_| {}).unwrap();
        let (names, map) = quirk_maps(&g.controller.quirks, Some("xpad"), 0x1234, 0x5678);
        assert_eq!(names, vec!["xpad"]);
        assert_eq!(map.get(&0x134), Some(&0x133), "physical Y (top) reports BTN_WEST -> BTN_NORTH");
        assert_eq!(map.get(&0x133), Some(&0x134));
        assert_eq!(map.get(&0x2c0), Some(&0x222), "dpad_to_buttons left");
        assert_eq!(map.get(&0x2c3), Some(&0x221), "dpad_to_buttons down");
        assert_eq!(quirk_maps(&g.controller.quirks, None, 0x045e, 0x028e).0, vec!["xpad"], "matched by USB id (uinput fixture)");
        assert!(quirk_maps(&g.controller.quirks, Some("playstation"), 0x054c, 0x0ce6).1.is_empty(), "DualSense is positional: no fixup");
        assert!(quirk_maps(&g.controller.quirks, None, 0x1209, 0x4d50).1.is_empty(), "the agent pad is positional");
        let (g, _) = vendor_with(|v| { controller(v)["quirks"]["xpad"].as_table_mut().unwrap().insert("enabled".into(), false.into()); }).unwrap();
        assert!(quirk_maps(&g.controller.quirks, Some("xpad"), 0x045e, 0x028e).1.is_empty(), "enabled = false turns the vendor quirk off");
    }
    #[test]
    fn quirks_stick_pseudo_keys_and_the_agent_pad_are_validated() {
        type Edit = Box<dyn FnOnce(&mut toml::Value)>;
        let set = |path: &'static [&'static str], val: toml::Value| -> Edit {
            Box::new(move |v: &mut toml::Value| {
                let mut t = controller(v);
                for k in &path[..path.len() - 1] {
                    t = t.entry(k.to_string()).or_insert_with(|| toml::Value::Table(Default::default())).as_table_mut().unwrap();
                }
                t.insert(path[path.len() - 1].into(), val);
            })
        };
        let remap = |pairs: &[(&str, &str)]| toml::Value::Table(pairs.iter().map(|(a, b)| (a.to_string(), toml::Value::from(*b))).collect());
        let cases: Vec<(Edit, &str)> = vec![
            (set(&["quirks", "xpad", "remap"], remap(&[("BTN_NORTH", "BTN_WEST"), ("BTN_SOUTH", "BTN_WEST")])), "one-to-one"),
            (set(&["quirks", "xpad", "remap"], remap(&[("0x400", "BTN_WEST")])), "outside the evdev button range"),
            (set(&["quirks", "xpad", "remap"], remap(&[("LS_UP", "BTN_WEST")])), "stick direction"),
            (set(&["quirks", "xpad", "ids"], toml::Value::Array(vec!["45e:28e".into()])), "vvvv:pppp"),
            (set(&["quirks", "bare", "remap"], remap(&[("BTN_NORTH", "BTN_WEST")])), "names no driver"),
            (set(&["room", "LS_UP"], "hypr:hypr3d:cancel".into()), "stick direction, not a button"),
            (set(&["guide_button"], "LS_UP".into()), "stick direction"),
            (set(&["system_layer", "BTN_MODE"], "hypr:killactive".into()), "the layer's modifier"),
            (set(&["system_layer", "LS_NOWHERE"], "hypr:killactive".into()), "unknown controller button"),
            (set(&["agent_pad", "rate_hz"], 0.into()), "rate_hz"),
            (set(&["agent_pad", "ease"], "bounce".into()), "ease"),
            (set(&["agent_pad", "name"], "MiOS Agent Pad".into()), "{id}"),
            (set(&["agent_pad", "name"], "mios-vctl-agent-{id}".into()), "mios-vctl"),
            (set(&["agent_pad", "min_ms"], 0.into()), "min_ms"),
            (set(&["agent_pad", "socket_dir"], "../escape".into()), "socket_dir"),
            (set(&["agent_pad", "usb_id"], "zz:1".into()), "usb_id"),
            (set(&["agent_pad", "warp"], true.into()), "unknown field"),
        ];
        for (i, (edit, why)) in cases.into_iter().enumerate() {
            let e = vendor_with(edit).err().unwrap_or_else(|| panic!("case {i} accepted ({why})"));
            assert!(e.contains(why), "case {i}: {e}");
        }
        assert!(vendor_with(set(&["system_layer", "RS_UP"], "hypr:movewindow up".into())).is_ok());
        assert!(vendor_with(set(&["quirks", "x", "drivers"], toml::Value::Array(vec!["hid-nintendo".into()]))).is_ok(), "a quirk without remap is inert, not an error");
    }
    #[test]
    fn stick_pseudo_keys_are_never_real_codes() {
        for n in ["LS_UP", "LS_DOWN", "LS_LEFT", "LS_RIGHT", "RS_UP", "RS_DOWN", "RS_LEFT", "RS_RIGHT"] {
            let c = button_code(n).unwrap();
            assert!(c > 0x2ff && mios_resolver::keybindings::is_stick_pseudo(c), "{n}");
        }
        assert!(normalize_chord("BTN_SOUTH+LS_UP", " ").is_err(), "a chord takes buttons only");
    }
    #[test]
    fn vendor_room_table_is_declared_and_valid() {
        let v: toml::Value = include_str!("../../../../usr/share/mios/mios.toml").parse().unwrap();
        let (g, _) = GlobalConfig::from_value(&v).unwrap();
        let room = &g.controller.room;
        assert_eq!(room.get("BTN_EAST"), Some(&RoomAction::Press("hypr:hypr3d:cancel".into())));
        assert_eq!(room.get("BTN_THUMBL"), Some(&RoomAction::Press("hypr:hypr3d:sprint".into())));
        assert_eq!(room.get("BTN_THUMBR"), Some(&RoomAction::Press("hypr:hypr3d:recenter".into())));
        assert_eq!(room.get("BTN_SELECT"), Some(&RoomAction::TapHold(vec!["hypr:hypr3d:view".into(), "quickshell:keymap-toggle".into()])));
        assert_eq!(room.get("BTN_MODE"), Some(&RoomAction::Press("quickshell:drawer-toggle".into())));
    }
    #[test]
    fn room_table_rejects_what_the_dispatcher_cannot_run_and_the_contract_buttons() {
        assert!(vendor_with_room("BTN_EAST = \"none\"").is_ok(), "none is the unbind word");
        for (bad, why) in [
            ("BTN_EAST = \"hypr:hypr3d:unknown\"", "unsupported room action unknown"),
            ("BTN_EAST = \"\"", "action needs namespace"),
            ("BTN_EAST = \"bogus\"", "action needs namespace"),
            ("BTN_NOPE = \"hypr:hypr3d:cancel\"", ""),
            ("BTN_EAST = [\"hypr:hypr3d:cancel\"]", "exactly two"),
            ("BTN_MODE = [\"quickshell:drawer-toggle\", \"quickshell:keymap-toggle\"]", "Guide is a tap"),
            ("BTN_THUMBL = [\"hypr:hypr3d:sprint\", \"hypr:hypr3d:recenter\"]", "clean release"),
            ("BTN_NORTH = \"hypr:hypr3d:cancel\"", "ownership contract"),
        ] {
            let e = vendor_with_room(bad).err().unwrap_or_else(|| panic!("accepted {bad}"));
            assert!(e.contains(why), "{bad}: {e}");
        }
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
        assert_eq!(c.classify("steam_app_123", true), "desktop-3d");
        assert_eq!(c.classify_window(Some("steam_app_123"),true,true),"app-capture");
        assert_eq!(c.classify("looking-glass-client", true), "desktop-3d");
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
        assert!(c.resolved_actions["mios:system-monitor"]
            .iter()
            .any(|s| s == "system"));
        assert!(!c.resolved_actions.contains_key("quickshell:monitor-toggle"), "C-19: the old alias is gone");
        assert_eq!(c.resolved_actions["quickshell:lock"], vec!["hyprlock"]);
    }
}
