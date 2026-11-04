// AI-hint: Validate layered [tamagotchi] SSOT before entering raw mode; no private configuration defaults.
// AI-related: usr/share/mios/mios.toml [tamagotchi], capabilities.rs
use std::path::Path;

use serde::Deserialize;

use crate::{
    capabilities::{ColorMode, Renderer},
    game::Rules,
};

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Eq, clap::ValueEnum)]
#[serde(rename_all = "kebab-case")]
pub enum ViewMode {
    Pixel,
    Voxel,
}

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Eq, clap::ValueEnum)]
#[serde(rename_all = "kebab-case")]
pub enum HudMode {
    Auto,
    Full,
    Minimal,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Config {
    pub renderer: Renderer,
    pub view_mode: ViewMode,
    pub hud_mode: HudMode,
    pub hud_minimal_width: u16,
    pub hud_minimal_height: u16,
    pub wander_chance: u8,
    pub wander_interval_ticks: u64,
    pub wander_speed: u16,
    pub sprite_style: crate::sprite::SpriteStyle,
    pub animation_frame_ms: u64,
    pub reaction_frames: usize,
    pub controller_prompts: bool,
    pub controller_poll_ms: u64,
    pub controller_timeout_ms: u64,
    pub controller_stale_ms: u64,
    pub controller_max_bytes: usize,
    pub color: ColorMode,
    pub scale: u16,
    pub tick_ms: u64,
    pub palette: Vec<String>,
    pub background: String,
    pub state_directory: String,
    pub autosave_seconds: u64,
    pub game: Rules,
}

impl Config {
    pub fn load(root: Option<&Path>) -> Result<Self, String> {
        let value = mios_resolver::resolve_merged(root, false).map_err(|e| e.to_string())?;
        Self::from_value(&value)
    }

    pub fn from_value(value: &toml::Value) -> Result<Self, String> {
        let table = value
            .get("tamagotchi")
            .ok_or("missing [tamagotchi] configuration; try --sample")?;
        table
            .get("game")
            .ok_or("missing [tamagotchi.game]; try --sample or update the MiOS configuration")?;
        let config: Self = table
            .clone()
            .try_into()
            .map_err(|e| format!("[tamagotchi]: {e}"))?;
        config.validate()?;
        Ok(config)
    }

    pub fn sample() -> Result<Self, String> {
        Self::from_value(
            &include_str!("../../../../usr/share/mios/mios.toml")
                .parse::<toml::Value>()
                .map_err(|e| e.to_string())?,
        )
    }

    pub fn validate(&self) -> Result<(), String> {
        self.game.validate()?;
        if !(20..=200).contains(&self.hud_minimal_width)
            || !(8..=80).contains(&self.hud_minimal_height)
        {
            return Err("tamagotchi: invalid automatic minimal HUD threshold".into());
        }
        if self.wander_chance > 100
            || !(1..=60).contains(&self.wander_interval_ticks)
            || !(1..=100).contains(&self.wander_speed)
        {
            return Err("tamagotchi: invalid autonomous wandering settings".into());
        }
        if !(1..=3600).contains(&self.autosave_seconds)
            || self.state_directory.is_empty()
            || !std::path::Path::new(&self.state_directory)
                .components()
                .all(|c| matches!(c, std::path::Component::Normal(_)))
        {
            return Err("tamagotchi: autosave_seconds must be 1..=3600 and state_directory must be a relative path without traversal".into());
        }
        if ![0, 1, 2, 4, 8, 16].contains(&self.scale) {
            return Err("tamagotchi.scale must be 0 (auto), 1, 2, 4, 8 or 16".into());
        }
        if !(16..=60_000).contains(&self.tick_ms) {
            return Err("tamagotchi.tick_ms must be in 16..=60000".into());
        }
        if ![4, 16].contains(&self.palette.len()) {
            return Err("tamagotchi.palette must contain four or sixteen #RRGGBB colors".into());
        }
        if !(40..=2000).contains(&self.animation_frame_ms)
            || !(1..=120).contains(&self.reaction_frames)
        {
            return Err(
                "tamagotchi: animation_frame_ms must be 40..=2000 and reaction_frames 1..=120"
                    .into(),
            );
        }
        if !(250..=10_000).contains(&self.controller_poll_ms)
            || !(50..=1000).contains(&self.controller_timeout_ms)
            || self.controller_stale_ms < self.controller_poll_ms
            || self.controller_stale_ms > 30_000
            || !(4096..=1_048_576).contains(&self.controller_max_bytes)
        {
            return Err("tamagotchi: invalid controller probe timing or size limits".into());
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
