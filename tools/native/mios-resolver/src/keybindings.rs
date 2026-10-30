// AI-hint: Shared global chord and Linux controller-code parser for gate and runtime.
// AI-related: usr/share/mios/keybinds/chord-vectors.json, tools/native/mios-inputd, src/mios-rs/mios-gate
use std::collections::BTreeSet;

/// Chord parser checked against the gate's shared conformance vectors. Underscores in
/// evdev names are significant; only modifier aliases use underscore separators.
pub fn normalize_chord(chord: &str, separator: &str) -> Result<String, String> {
    if separator.is_empty() {
        return Err("chord_sep must not be empty".into());
    }
    let compact=chord.split_whitespace().collect::<Vec<_>>().join(" ");
    let compact=compact.replace(" +", "+").replace("+ ", "+");
    let steps: Vec<_> = compact.split(separator).filter(|s| !s.is_empty()).collect();
    if steps.is_empty() {
        return Err("empty chord".into());
    }
    steps
        .into_iter()
        .map(normalize_step)
        .collect::<Result<Vec<_>, _>>()
        .map(|s| s.join(separator))
}
fn normalize_step(step: &str) -> Result<String, String> {
    let step = step
        .replace("SUPER_CTRL", "SUPER+CTRL")
        .replace("SUPER_SHIFT", "SUPER+SHIFT")
        .replace("CTRL_ALT", "CTRL+ALT")
        .replace("CTRL_SHIFT", "CTRL+SHIFT");
    let tokens: Vec<_> = step.split('+').collect();
    if tokens.iter().all(|t| t.starts_with("BTN_")) {
        let mut buttons = BTreeSet::new();
        for t in tokens {
            button_code(t)?;
            let t = match t {
                "BTN_A" | "BTN_GAMEPAD" => "BTN_SOUTH",
                "BTN_B" => "BTN_EAST",
                "BTN_X" => "BTN_NORTH",
                "BTN_Y" => "BTN_WEST",
                _ => t,
            };
            if !buttons.insert(t) {
                return Err(format!("duplicate button {t}"));
            }
        }
        return Ok(buttons.into_iter().collect::<Vec<_>>().join("+"));
    }
    let mut mods = BTreeSet::new();
    let mut key = None;
    for token in tokens {
        let token = token.trim();
        let upper = token.to_ascii_uppercase();
        let modifier = match upper.as_str() {
            "SUPER" | "META" | "WIN" | "MOD4" => Some("SUPER"),
            "CTRL" | "CONTROL" => Some("CTRL"),
            "RIGHTCTRL" | "RCTRL" => Some("RIGHTCTRL"),
            "LEFTCTRL" | "LCTRL" => Some("LEFTCTRL"),
            "ALT" | "MOD1" => Some("ALT"),
            "SHIFT" => Some("SHIFT"),
            _ => None,
        };
        if let Some(m) = modifier {
            if !mods.insert(m) {
                return Err(format!("duplicate modifier {m}"));
            }
        } else {
            if token.is_empty() || key.is_some() {
                return Err(format!("invalid chord step {step}"));
            }
            let canonical = match upper.as_str() {
                "ENTER" | "RETURN" => "Return".into(),
                "SPACE" => "Space".into(),
                "ESC" | "ESCAPE" => "Escape".into(),
                "TAB" => "Tab".into(),
                "BACKSPACE" => "BackSpace".into(),
                "DELETE" => "Delete".into(),
                "INSERT" => "Insert".into(),
                "LEFT" => "Left".into(),
                "RIGHT" => "Right".into(),
                "UP" => "Up".into(),
                "DOWN" => "Down".into(),
                "HOME" => "Home".into(),
                "END" => "End".into(),
                "PAGEUP" => "Prior".into(),
                "PAGEDOWN" => "Next".into(),
                _ if token.len() == 1 => token.to_ascii_lowercase(),
                _ if upper.starts_with('F')
                    && upper[1..]
                        .parse::<u8>()
                        .is_ok_and(|n| (1..=35).contains(&n)) =>
                {
                    upper
                }
                _ if token
                    .chars()
                    .all(|c| c.is_ascii_alphanumeric() || c == '_' || c == ':') =>
                {
                    token.into()
                }
                _ => return Err(format!("invalid key {token}")),
            };
            key = Some(canonical.to_ascii_lowercase());
        }
    }
    let key = key.ok_or_else(|| format!("chord has no key: {step}"))?;
    let mut out: Vec<_> = ["SUPER", "CTRL", "ALT", "SHIFT", "LEFTCTRL", "RIGHTCTRL"]
        .into_iter()
        .filter(|m| mods.contains(m))
        .map(str::to_owned)
        .collect();
    out.push(key);
    Ok(out.join("+"))
}

/// A real controller button: an evdev key code a device can report. Every controller role (chords, the legacy
/// Hypr3D buttons, Guide/cursor, the room table, quirks, the agent pad) takes only these. The stick pseudo-keys
/// (LS_*/RS_*) are refused here: they are accepted by [`system_layer_code`] only.
pub fn button_code(name: &str) -> Result<u16, String> {
    let code = system_layer_code(name)?;
    if is_stick_pseudo(code) {
        return Err(format!("{name} is a stick direction, accepted only as a [controller.system_layer] key, not a button"));
    }
    Ok(code)
}
/// A Guide system-layer key: a real button ([`button_code`]) or, since I4 (C-15b), a stick direction pseudo-key
/// (LS_*/RS_*: one action per flick). Only the system layer resolves names through this.
pub fn system_layer_code(name: &str) -> Result<u16, String> {
    match name {
        "BTN_SOUTH" | "BTN_A" | "BTN_GAMEPAD" => Ok(0x130),
        "BTN_EAST" | "BTN_B" => Ok(0x131),
        "BTN_NORTH" | "BTN_X" => Ok(0x133),
        "BTN_WEST" | "BTN_Y" => Ok(0x134),
        "BTN_TL" => Ok(0x136),
        "BTN_TR" => Ok(0x137),
        "BTN_TL2" => Ok(0x138),
        "BTN_TR2" => Ok(0x139),
        "BTN_SELECT" => Ok(0x13a),
        "BTN_START" => Ok(0x13b),
        "BTN_MODE" => Ok(0x13c),
        "BTN_THUMBL" => Ok(0x13d),
        "BTN_THUMBR" => Ok(0x13e),
        "BTN_DPAD_UP" => Ok(0x220),
        "BTN_DPAD_DOWN" => Ok(0x221),
        "BTN_DPAD_LEFT" => Ok(0x222),
        "BTN_DPAD_RIGHT" => Ok(0x223),
        // I4 (C-15b): stick directions as pseudo-keys for the Guide system layer (one action per flick). They lie past
        // KEY_MAX (0x2ff), so no evdev event can ever carry one; nav parses LS_*/RS_* as stick inputs before this.
        "LS_UP" => Ok(STICK_PSEUDO),
        "LS_DOWN" => Ok(STICK_PSEUDO + 1),
        "LS_LEFT" => Ok(STICK_PSEUDO + 2),
        "LS_RIGHT" => Ok(STICK_PSEUDO + 3),
        "RS_UP" => Ok(STICK_PSEUDO + 4),
        "RS_DOWN" => Ok(STICK_PSEUDO + 5),
        "RS_LEFT" => Ok(STICK_PSEUDO + 6),
        "RS_RIGHT" => Ok(STICK_PSEUDO + 7),
        _ => Err(format!("unknown controller button {name}")),
    }
}
/// First stick pseudo-key code (LS_UP); LS_* then RS_* in UP, DOWN, LEFT, RIGHT order. Never a real evdev code.
pub const STICK_PSEUDO: u16 = 0x300;
/// A stick pseudo-key (LS_*/RS_*), not a button a device can press.
pub fn is_stick_pseudo(code: u16) -> bool {
    (STICK_PSEUDO..STICK_PSEUDO + 8).contains(&code)
}
pub fn axis_code(name: &str) -> Result<u16, String> {
    match name {
        "ABS_X" => Ok(0),
        "ABS_Y" => Ok(1),
        "ABS_Z" => Ok(2),
        "ABS_RX" => Ok(3),
        "ABS_RY" => Ok(4),
        "ABS_RZ" => Ok(5),
        "ABS_HAT0X" => Ok(16),
        "ABS_HAT0Y" => Ok(17),
        _ => Err(format!("unknown controller axis {name}")),
    }
}
