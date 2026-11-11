// AI-hint: Pure SSOT-configured voxel lighting and visible ASCII tonal shading.
// AI-related: config.rs, render.rs, sprite.rs, usr/share/mios/mios.toml [mons.voxel_lighting]
// AI-functions: validate, brightness, shade_rgb, glyph

#[derive(Clone, Debug, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct VoxelLighting {
    pub enabled: bool,
    pub ambient_percent: u16,
    pub diffuse_percent: u16,
    pub occlusion_percent: u16,
    pub outline_percent: u16,
    pub light_direction: [i16; 3],
    pub ascii_ramp: String,
}

impl VoxelLighting {
    pub fn validate(&self) -> Result<(), String> {
        if !(20..=100).contains(&self.ambient_percent)
            || self.diffuse_percent > 100
            || u32::from(self.ambient_percent) + u32::from(self.diffuse_percent) > 180
            || self.occlusion_percent > 100
            || self.outline_percent > 100
        {
            return Err("mons.voxel_lighting: ambient must be20..100, diffuse/occlusion/outline0..100 and ambient+diffuse<=180".into());
        }
        if self
            .light_direction
            .iter()
            .any(|axis| !(-100..=100).contains(axis))
            || self.light_direction == [0, 0, 0]
        {
            return Err("mons.voxel_lighting.light_direction: require a nonzero vector with each axis in -100..100".into());
        }
        if !(2..=16).contains(&self.ascii_ramp.len())
            || !self
                .ascii_ramp
                .bytes()
                .all(|byte| (b' '..=b'~').contains(&byte))
        {
            return Err(
                "mons.voxel_lighting.ascii_ramp: require2..16 printable ASCII characters".into(),
            );
        }
        let mut visible = self.ascii_ramp.bytes().filter(u8::is_ascii_graphic);
        let first = visible.next();
        if !visible.any(|byte| Some(byte) != first) {
            return Err(
                "mons.voxel_lighting.ascii_ramp: require at least two distinct nonspace characters"
                    .into(),
            );
        }
        Ok(())
    }

    /// Percent light for an occupied surface. Normals and light vectors are
    /// normalized independently, so geometry scale cannot change the lighting.
    /// Occlusion100 means full contact; edge applies the separate outline term.
    pub fn brightness(&self, normal: [f32; 3], occlusion: u8, edge: bool) -> u16 {
        if !self.enabled {
            return 100;
        }
        let ambient = self.ambient_percent.clamp(20, 100);
        let Some(normal) = normalized(normal.map(f64::from)) else {
            return ambient;
        };
        let Some(light) = normalized(self.light_direction.map(f64::from)) else {
            return ambient;
        };
        let dot = normal
            .into_iter()
            .zip(light)
            .map(|(n, l)| n * l)
            .sum::<f64>()
            .clamp(0.0, 1.0);
        let base =
            (f64::from(ambient) + f64::from(self.diffuse_percent.min(100)) * dot).clamp(0.0, 180.0);
        let contact = 1.0
            - f64::from(self.occlusion_percent.min(100)) * f64::from(occlusion.min(100)) / 10_000.0;
        let outline = if edge {
            1.0 - f64::from(self.outline_percent.min(100)) / 100.0
        } else {
            1.0
        };
        (base * contact * outline).round().clamp(0.0, 180.0) as u16
    }

    /// Apply one scalar to the original RGB channels; palette indices and
    /// species colors remain the caller's unchanged source of color.
    pub fn shade_rgb(&self, rgb: [u8; 3], brightness: u16) -> [u8; 3] {
        let brightness = u32::from(brightness.min(180));
        rgb.map(|channel| ((u32::from(channel) * brightness + 50) / 100).min(255) as u8)
    }

    /// Actual configured ramp order, dim to bright. Spaces never erase an
    /// occupied surface. A malformed unvalidated ramp yields a visible '?'.
    /// Disabled rendering delegates to the legacy glyph path in the renderer.
    pub fn glyph(&self, brightness: u16) -> char {
        let visible = || self.ascii_ramp.bytes().filter(u8::is_ascii_graphic);
        let count = visible().count();
        let index = usize::from(brightness.min(180)).saturating_mul(count.saturating_sub(1)) / 180;
        visible().nth(index).map(char::from).unwrap_or('?')
    }
}

fn normalized(vector: [f64; 3]) -> Option<[f64; 3]> {
    if !vector.iter().all(|component| component.is_finite()) {
        return None;
    }
    let length = vector
        .iter()
        .map(|component| component * component)
        .sum::<f64>()
        .sqrt();
    if !length.is_finite() || length == 0.0 {
        return None;
    }
    Some(vector.map(|component| component / length))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn lighting() -> VoxelLighting {
        VoxelLighting {
            enabled: true,
            ambient_percent: 55,
            diffuse_percent: 65,
            occlusion_percent: 30,
            outline_percent: 20,
            light_direction: [-45, 65, 45],
            ascii_ramp: ".:-=+*#%@".into(),
        }
    }

    #[test]
    fn front_back_and_contact_have_directional_contrast_without_scale_dependence() {
        let mut light = lighting();
        light.ambient_percent = 40;
        light.diffuse_percent = 80;
        light.occlusion_percent = 40;
        light.outline_percent = 25;
        light.light_direction = [0, 0, 100];
        assert!(light.validate().is_ok());
        assert_eq!(light.brightness([0.0, 0.0, 1.0], 0, false), 120);
        assert_eq!(light.brightness([0.0, 0.0, 8.0], 0, false), 120);
        assert_eq!(light.brightness([0.0, 0.0, -1.0], 0, false), 40);
        assert_eq!(light.brightness([1.0, 0.0, 0.0], 0, false), 40);
        assert_eq!(light.brightness([0.0, 0.0, 1.0], 50, false), 96);
        assert_eq!(light.brightness([0.0, 0.0, 1.0], 50, true), 72);
        assert!(
            light.brightness([0.0, 0.0, 1.0], 0, false)
                > light.brightness([0.0, 0.0, -1.0], 0, false)
        );
    }

    #[test]
    fn disabled_is_exact_passthrough_and_bad_normals_never_poison_the_frame() {
        let mut light = lighting();
        for normal in [
            [0.0; 3],
            [f32::NAN, 0.0, 1.0],
            [0.0, f32::INFINITY, 1.0],
            [0.0, 1.0, f32::NEG_INFINITY],
        ] {
            assert_eq!(light.brightness(normal, 255, true), light.ambient_percent);
        }
        assert!(light.brightness([f32::MAX; 3], 0, false) <= 180);
        light.light_direction = [0; 3];
        assert_eq!(
            light.brightness([0.0, 0.0, 1.0], 0, false),
            light.ambient_percent
        );
        light.enabled = false;
        for normal in [[0.0; 3], [1.0, 2.0, 3.0], [f32::NAN; 3]] {
            let brightness = light.brightness(normal, 255, true);
            assert_eq!(brightness, 100);
            assert_eq!(light.shade_rgb([23, 101, 245], brightness), [23, 101, 245]);
        }
    }

    #[test]
    fn rgb_scaling_retains_family_channel_ratios_and_saturates_without_wrapping() {
        let light = lighting();
        assert_eq!(light.shade_rgb([80, 40, 20], 75), [60, 30, 15]);
        assert_eq!(light.shade_rgb([80, 40, 20], 180), [144, 72, 36]);
        assert_eq!(light.shade_rgb([255, 200, 100], u16::MAX), [255, 255, 180]);
        assert_eq!(light.shade_rgb([80, 40, 20], 0), [0, 0, 0]);
        for brightness in 0..=180 {
            let shaded = light.shade_rgb([80, 40, 20], brightness);
            assert!(shaded[0] >= shaded[1] && shaded[1] >= shaded[2]);
        }
    }

    #[test]
    fn configured_ramp_is_monotone_and_never_blanks_an_occupied_cell() {
        let mut light = lighting();
        light.ascii_ramp = " .oO# ".into();
        assert!(light.validate().is_ok());
        assert_eq!(light.glyph(0), '.');
        assert_eq!(light.glyph(180), '#');
        assert_eq!(light.glyph(u16::MAX), '#');
        let order = ".oO#";
        let mut previous = 0;
        for brightness in 0..=180 {
            let glyph = light.glyph(brightness);
            assert!(glyph.is_ascii_graphic());
            let position = order.find(glyph).unwrap();
            assert!(position >= previous);
            previous = position;
        }
        light.ascii_ramp = "   ".into();
        assert!(light.validate().is_err());
        assert_eq!(light.glyph(0), '?');
    }

    #[test]
    fn malformed_lighting_and_terminal_escape_ramps_are_rejected() {
        let valid = lighting();
        let reject = |light: VoxelLighting| assert!(light.validate().is_err(), "{light:?}");
        for ambient in [0, 19, 101, u16::MAX] {
            let mut light = valid.clone();
            light.ambient_percent = ambient;
            reject(light);
        }
        for field in 0..3 {
            let mut light = valid.clone();
            match field {
                0 => light.diffuse_percent = 101,
                1 => light.occlusion_percent = 101,
                _ => light.outline_percent = 101,
            }
            reject(light);
        }
        let mut light = valid.clone();
        light.ambient_percent = 100;
        light.diffuse_percent = 81;
        reject(light);
        for direction in [[0, 0, 0], [101, 0, 0], [0, -101, 0], [0, 0, i16::MAX]] {
            let mut light = valid.clone();
            light.light_direction = direction;
            reject(light);
        }
        for ramp in [
            "",
            ".",
            ".....",
            " . ",
            "\n.#",
            "\u{1b}[m#",
            ".é",
            ".:-=+*#%@0123456789",
        ] {
            let mut light = valid.clone();
            light.ascii_ramp = ramp.into();
            reject(light);
        }
        assert!(valid.validate().is_ok());
    }

    #[test]
    fn deserialization_requires_every_field_and_rejects_unknown_keys() {
        let text = r#"
enabled = true
ambient_percent = 55
diffuse_percent = 65
occlusion_percent = 30
outline_percent = 20
light_direction = [-45, 65, 45]
ascii_ramp = ".:-=+*#%@"
"#;
        let parsed: VoxelLighting = toml::from_str(text).unwrap();
        assert!(parsed.validate().is_ok());
        assert!(toml::from_str::<VoxelLighting>(&format!("{text}\nunknown = 1")).is_err());
        assert!(
            toml::from_str::<VoxelLighting>(&text.replace("ambient_percent = 55\n", "")).is_err()
        );
    }
}
