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
    pub voxel_lighting: crate::lighting::VoxelLighting,
    pub camera_view: crate::render::CameraView,
    pub world: crate::world::WorldRules,
    pub camera: CameraRules,
    pub genetics: crate::genetics::GeneticsRules,
    pub zoom_percent: u16,
    pub zoom_step_percent: u16,
    pub zoom_transition_frames: usize,
    pub hud_mode: HudMode,
    pub presentation: crate::hud::DetailRules,
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
        self.voxel_lighting.validate()?;
        self.presentation.validate()?;
        self.world.validate()?;
        self.camera.validate()?;
        self.genetics.validate()?;
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
        for character in &self.characters {
            self.game
                .training
                .palette(character.shape, &character.archetype)
                .map_err(|e| format!("tamagotchi.characters[{}]: {e}", character.id))?;
        }
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

/// Interactive camera tunables, `[tamagotchi.camera]`. Presentation only: the
/// camera is never saved and never steers the Mon. No private defaults: the
/// vendor SSOT supplies every key; `INERT` is the config-less constructor's rig.
#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct CameraRules {
    pub pan_step_permille: u16,
    pub pan_max_permille: u16,
    pub pan_margin_permille: u16,
    pub tilt_step_degrees: u8,
    pub tilt_min_degrees: i16,
    pub tilt_max_degrees: i16,
    pub orbit_fine_step_degrees: u16,
    pub max_lead_steps: u8,
    pub preview_orbit_pauses_turntable: bool,
}

impl CameraRules {
    /// Every step and range is zero, so camera-cluster input changes nothing.
    pub const INERT: Self = Self {
        pan_step_permille: 0,
        pan_max_permille: 0,
        pan_margin_permille: 0,
        tilt_step_degrees: 0,
        tilt_min_degrees: 0,
        tilt_max_degrees: 0,
        orbit_fine_step_degrees: 0,
        max_lead_steps: 0,
        preview_orbit_pauses_turntable: false,
    };

    pub fn validate(&self) -> Result<(), String> {
        let rule = |ok: bool, message: &str| {
            if ok {
                Ok(())
            } else {
                Err(format!("tamagotchi.camera: {message}"))
            }
        };
        rule(
            (5..=200).contains(&self.pan_step_permille),
            "pan_step_permille must be 5..=200",
        )?;
        rule(
            self.pan_max_permille <= 400,
            "pan_max_permille must be 0..=400",
        )?;
        rule(
            self.pan_margin_permille <= 250,
            "pan_margin_permille must be 0..=250",
        )?;
        rule(
            self.pan_max_permille + self.pan_margin_permille <= 500,
            "pan_max_permille + pan_margin_permille must be <= 500",
        )?;
        rule(
            (1..=30).contains(&self.tilt_step_degrees),
            "tilt_step_degrees must be 1..=30",
        )?;
        rule(
            (-17..=0).contains(&self.tilt_min_degrees),
            "tilt_min_degrees must be -17..=0",
        )?;
        rule(
            (0..=55).contains(&self.tilt_max_degrees),
            "tilt_max_degrees must be 0..=55",
        )?;
        rule(
            (1..=90).contains(&self.orbit_fine_step_degrees),
            "orbit_fine_step_degrees must be 1..=90",
        )?;
        rule(
            (1..=10).contains(&self.max_lead_steps),
            "max_lead_steps must be 1..=10",
        )
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

#[cfg(test)]
mod training_palette_validation_tests {
    use super::Config;
    #[test]
    fn a_missing_actual_personality_palette_is_rejected_before_raw_mode() {
        let mut config = Config::sample().unwrap();
        assert!(config.validate().is_ok());
        let archetype = config.characters[0].archetype.clone();
        config.game.training.personality_colors.remove(&archetype);
        assert!(config
            .validate()
            .unwrap_err()
            .contains("tamagotchi.game.training"));
    }
}

#[cfg(test)]
mod voxel_lighting_validation_tests {
    use super::Config;

    #[test]
    fn sparse_overlay_inherits_the_vendor_light_without_overriding_other_choices() {
        let vendor = Config::sample().unwrap();
        let overlay = "[tamagotchi]\nzoom_percent = 125\n"
            .parse::<toml::Value>()
            .unwrap();
        let config = Config::from_value(&overlay).unwrap();
        assert_eq!(config.zoom_percent, 125);
        assert_eq!(
            config.voxel_lighting.ambient_percent,
            vendor.voxel_lighting.ambient_percent
        );
        assert_eq!(
            config.voxel_lighting.light_direction,
            vendor.voxel_lighting.light_direction
        );
        assert_eq!(
            config.voxel_lighting.ascii_ramp,
            vendor.voxel_lighting.ascii_ramp
        );
        let changed = "[tamagotchi.voxel_lighting]\nenabled = false\nambient_percent = 40\n"
            .parse::<toml::Value>()
            .unwrap();
        let config = Config::from_value(&changed).unwrap();
        assert!(!config.voxel_lighting.enabled);
        assert_eq!(config.voxel_lighting.ambient_percent, 40);
        assert_eq!(
            config.voxel_lighting.diffuse_percent,
            vendor.voxel_lighting.diffuse_percent
        );
    }

    #[test]
    fn malformed_layered_lighting_fails_before_entering_the_terminal() {
        for setting in [
            "ambient_percent = 19",
            "light_direction = [0, 0, 0]",
            "ascii_ramp = '...'",
            "unknown = 1",
        ] {
            let overlay = format!("[tamagotchi.voxel_lighting]\n{setting}\n")
                .parse::<toml::Value>()
                .unwrap();
            assert!(Config::from_value(&overlay).is_err(), "{setting}");
        }
    }
}

#[cfg(test)]
mod presentation_validation_tests {
    use super::Config;
    #[test]
    fn layered_detail_rules_are_required_in_vendor_and_validate_user_overrides() {
        let vendor = Config::sample().unwrap();
        let sparse = "[tamagotchi]\nzoom_percent = 125"
            .parse::<toml::Value>()
            .unwrap();
        let resolved = Config::from_value(&sparse).unwrap();
        assert_eq!(
            resolved.presentation.min_width,
            vendor.presentation.min_width
        );
        assert_eq!(
            resolved.presentation.min_height,
            vendor.presentation.min_height
        );
        assert_eq!(resolved.zoom_percent, 125);
        for setting in [
            "min_width = 0",
            "min_height = 0",
            "hysteresis_percent = 101",
            "unknown = 1",
        ] {
            let value = format!("[tamagotchi.presentation]\n{setting}\n")
                .parse::<toml::Value>()
                .unwrap();
            assert!(Config::from_value(&value).is_err(), "{setting}");
        }
    }
}

#[cfg(test)]
mod camera_rules_validation_tests {
    use super::{CameraRules, Config};

    #[test]
    fn camera_rules_inherit_vendor_and_validate_layered_overrides() {
        let vendor = Config::sample().unwrap().camera;
        assert_ne!(vendor, CameraRules::INERT);
        let sparse = "[tamagotchi.camera]\npan_step_permille = 50\n"
            .parse::<toml::Value>()
            .unwrap();
        let resolved = Config::from_value(&sparse).unwrap().camera;
        assert_eq!(resolved.pan_step_permille, 50);
        // The other eight keys inherit the vendor tier unchanged.
        assert_eq!(
            CameraRules {
                pan_step_permille: vendor.pan_step_permille,
                ..resolved
            },
            vendor
        );
        for (setting, rule) in [
            ("pan_step_permille = 4", "pan_step_permille"),
            ("pan_step_permille = 201", "pan_step_permille"),
            ("pan_max_permille = 401", "pan_max_permille must"),
            ("pan_margin_permille = 251", "pan_margin_permille must"),
            (
                "pan_max_permille = 300\npan_margin_permille = 201",
                "pan_max_permille + pan_margin_permille",
            ),
            ("tilt_min_degrees = -18", "tilt_min_degrees"),
            ("tilt_min_degrees = 1", "tilt_min_degrees"),
            ("tilt_max_degrees = 56", "tilt_max_degrees"),
            ("tilt_max_degrees = -1", "tilt_max_degrees"),
            ("tilt_step_degrees = 0", "tilt_step_degrees"),
            ("orbit_fine_step_degrees = 91", "orbit_fine_step_degrees"),
            ("max_lead_steps = 0", "max_lead_steps"),
            ("unknown = 1", "unknown"),
        ] {
            let overlay = format!("[tamagotchi.camera]\n{setting}\n")
                .parse::<toml::Value>()
                .unwrap();
            let error = Config::from_value(&overlay).unwrap_err();
            assert!(error.contains(rule), "{setting}: {error}");
        }
    }
}
