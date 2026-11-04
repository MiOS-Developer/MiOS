// AI-hint: Validate layered [tamagotchi] SSOT before entering raw mode; no private configuration defaults.
// AI-related: usr/share/mios/mios.toml [tamagotchi], capabilities.rs
use std::path::Path;

use serde::Deserialize;

use crate::capabilities::{ColorMode, Renderer};

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Config {
    pub renderer: Renderer,
    pub color: ColorMode,
    pub scale: u16,
    pub tick_ms: u64,
    pub palette: Vec<String>,
    pub background: String,
}

impl Config {
    pub fn load(root: Option<&Path>) -> Result<Self, String> {
        let value = mios_resolver::resolve_merged(root, false).map_err(|e| e.to_string())?;
        Self::from_value(&value)
    }

    pub fn from_value(value: &toml::Value) -> Result<Self, String> {
        let config: Self = value
            .get("tamagotchi")
            .ok_or("missing [tamagotchi] configuration")?
            .clone()
            .try_into()
            .map_err(|e| format!("[tamagotchi]: {e}"))?;
        config.validate()?;
        Ok(config)
    }

    pub fn validate(&self) -> Result<(), String> {
        if ![0, 1, 2, 4, 8, 16].contains(&self.scale) {
            return Err("tamagotchi.scale must be 0 (auto), 1, 2, 4, 8 or 16".into());
        }
        if !(16..=60_000).contains(&self.tick_ms) {
            return Err("tamagotchi.tick_ms must be in 16..=60000".into());
        }
        if self.palette.len() != 4 {
            return Err("tamagotchi.palette must contain exactly four #RRGGBB colors".into());
        }
        for color in self.palette.iter().chain(std::iter::once(&self.background)) {
            parse_rgb(color)?;
        }
        Ok(())
    }
}

pub fn parse_rgb(text: &str) -> Result<[u8; 3], String> {
    let digits = text
        .strip_prefix('#')
        .ok_or_else(|| format!("invalid #RRGGBB color: {text:?}"))?;
    if digits.len() != 6 || !digits.bytes().all(|c| c.is_ascii_hexdigit()) {
        return Err(format!("invalid #RRGGBB color: {text:?}"));
    }
    let value = u32::from_str_radix(digits, 16).map_err(|e| e.to_string())?;
    Ok([(value >> 16) as u8, (value >> 8) as u8, value as u8])
}
