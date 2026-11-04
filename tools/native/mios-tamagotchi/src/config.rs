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
    pub dialog_mode: crate::dialogs::DialogMode,
    pub dialog_timeout_ms: u64,
    pub dialog_query_timeout_ms: u64,
    pub dialog_max_bytes: usize,
    pub popup_min_width: u16,
    pub popup_min_height: u16,
    pub floating_thoughts: bool,
    pub thought_interval_ticks: u64,
    pub inline_dialog_rows: u16,
    pub characters: Vec<crate::characters::Character>,
    pub renderer: Renderer,
    pub view_mode: ViewMode,
    pub camera_view: crate::render::CameraView,
    pub zoom_percent: u16,
    pub zoom_step_percent: u16,
    pub zoom_transition_frames: usize,
    pub hud_mode: HudMode,
    #[serde(skip)]
    pub hud_golden_ratio: f64,
    #[serde(skip)]
    pub hud_landscape_columns: u16,
    #[serde(skip)]
    pub hud_portrait_ratio_percent: u16,
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
    pub habitat: crate::habitat::HabitatRules,
}

impl Config {
    pub fn load(root: Option<&Path>) -> Result<Self, String> {
        let value = mios_resolver::resolve_merged(root, false).map_err(|e| e.to_string())?;
        Self::from_value(&value)
    }

    pub fn from_value(value: &toml::Value) -> Result<Self, String> {
        if !value.is_table() {
            return Err("MiOS configuration must be a TOML table".into());
        }
        // The compiled vendor SSOT is the lowest compatibility tier. An older
        // installed vendor or sparse user profile can omit newly added fields;
        // explicit layered choices still win through the shared merge contract.
        let vendor = include_str!("../../../../usr/share/mios/mios.toml")
            .parse::<toml::Value>()
            .map_err(|e| e.to_string())?;
        let mut table = vendor
            .get("tamagotchi")
            .cloned()
            .ok_or("embedded vendor SSOT lacks [tamagotchi]")?;
        if let Some(layered) = value.get("tamagotchi") {
            mios_resolver::merge::deep_merge(&mut table, layered.clone());
        }
        let mut config: Self = table.try_into().map_err(|e| format!("[tamagotchi]: {e}"))?;
        let workspace = |document: &toml::Value, key: &str| {
            document
                .get("mcp")
                .and_then(|v| v.get("tmux"))
                .and_then(|v| v.get("workspace"))
                .and_then(|v| v.get(key))
                .cloned()
        };
        let resolved = |key: &str| {
            workspace(value, key)
                .or_else(|| workspace(&vendor, key))
                .ok_or_else(|| format!("missing native workspace layout setting {key}"))
        };
        let ratio = resolved("golden_ratio")?;
        config.hud_golden_ratio = ratio
            .as_float()
            .or_else(|| ratio.as_integer().map(|v| v as f64))
            .ok_or("native workspace golden_ratio must be numeric")?;
        config.hud_landscape_columns = resolved("resource_min_columns")?
            .as_integer()
            .and_then(|v| u16::try_from(v).ok())
            .ok_or("invalid native workspace resource_min_columns")?;
        config.hud_portrait_ratio_percent = resolved("portrait_ratio_percent")?
            .as_integer()
            .and_then(|v| u16::try_from(v).ok())
            .ok_or("invalid native workspace portrait_ratio_percent")?;
        config.validate()?;
        Ok(config)
    }

    pub fn sample() -> Result<Self, String> {
        Self::from_value(&toml::Value::Table(toml::Table::new()))
    }

    pub fn validate(&self) -> Result<(), String> {
        self.game.validate()?;
        self.habitat.validate()?;
        if !(1000..=600_000).contains(&self.dialog_timeout_ms)
            || !(50..=2000).contains(&self.dialog_query_timeout_ms)
            || !(4096..=262_144).contains(&self.dialog_max_bytes)
            || !(30..=200).contains(&self.popup_min_width)
            || !(10..=80).contains(&self.popup_min_height)
            || !(5..=3600).contains(&self.thought_interval_ticks)
            || !(4..=12).contains(&self.inline_dialog_rows)
        {
            return Err("tamagotchi: invalid native/inline dialogue limits".into());
        }
        crate::characters::validate_catalogue(&self.characters)?;
        if !self.hud_golden_ratio.is_finite()
            || !(1.1..=4.0).contains(&self.hud_golden_ratio)
            || !(20..=240).contains(&self.hud_landscape_columns)
            || !(100..=400).contains(&self.hud_portrait_ratio_percent)
        {
            return Err("tamagotchi: invalid inherited native workspace HUD proportions".into());
        }
        if !(20..=200).contains(&self.hud_minimal_width)
            || !(8..=80).contains(&self.hud_minimal_height)
        {
            return Err("tamagotchi: invalid automatic minimal HUD threshold".into());
        }
        if !(50..=400).contains(&self.zoom_percent)
            || !(5..=100).contains(&self.zoom_step_percent)
            || !(1..=60).contains(&self.zoom_transition_frames)
        {
            return Err(
                "tamagotchi: zoom must be50..=400 percent, step5..=100 and transition1..=60 frames"
                    .into(),
            );
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
