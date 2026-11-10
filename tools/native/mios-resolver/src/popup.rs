// AI-hint: Shared MiOS popup bounds inherited from existing launch sizes and font cells; centered consumers retain their own scrolling and input ownership.
// AI-related: usr/share/mios/mios.toml [terminal.popups], miosd launcher, mios-gen tmux-os, mios-tamagotchi

use toml::Value;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SurfaceKind {
    Window,
    Menu,
    Dialog,
    Tooltip,
}

#[derive(Clone, Debug)]
pub struct PopupPolicy {
    pub enabled: bool,
    cells: [[u16; 2]; 4],
    cell_width: f64,
    cell_height: f64,
    aspect: f64,
}

fn at<'a>(root: &'a Value, key: &str) -> Result<&'a Value, String> {
    key.split('.')
        .try_fold(root, |value, part| value.get(part))
        .ok_or_else(|| format!("popup size source {key:?} is missing"))
}
fn positive(value: &Value) -> Result<u16, String> {
    value
        .as_integer()
        .and_then(|n| u16::try_from(n).ok())
        .filter(|n| (1..=4096).contains(n))
        .ok_or_else(|| "popup dimensions must be positive cell counts <=4096".into())
}
impl PopupPolicy {
    /// Generators need only the switch; runtime consumers resolve dimension sources.
    pub fn enabled_from_value(root: &Value) -> Result<bool, String> {
        let Some(config) = root.get("terminal").and_then(|v| v.get("popups")) else {
            return Ok(false);
        };
        config
            .get("enabled")
            .and_then(Value::as_bool)
            .ok_or_else(|| "terminal.popups.enabled must be boolean".into())
    }
    pub fn from_value(root: &Value) -> Result<Self, String> {
        let Some(config) = root.get("terminal").and_then(|v| v.get("popups")) else {
            return Ok(Self {
                enabled: false,
                cells: [[0; 2]; 4],
                cell_width: 1.,
                cell_height: 1.,
                aspect: 1.,
            });
        };
        let enabled = Self::enabled_from_value(root)?;
        let source = |name: &str| -> Result<&Value, String> {
            let key = config
                .get(name)
                .and_then(Value::as_str)
                .ok_or_else(|| format!("terminal.popups.{name} must name an existing SSOT key"))?;
            at(root, key)
        };
        let window = source("window_size_source")?
            .as_array()
            .filter(|v| v.len() == 2)
            .ok_or("popup window source must be a cell pair")?;
        let cells = [
            [positive(&window[0])?, positive(&window[1])?],
            [
                positive(source("menu_width_source")?)?,
                positive(source("menu_height_source")?)?,
            ],
            [
                positive(source("dialog_width_source")?)?,
                positive(source("dialog_height_source")?)?,
            ],
            [
                positive(source("tooltip_width_source")?)?,
                positive(source("tooltip_height_source")?)?,
            ],
        ];
        let number = |name: &str| -> Result<f64, String> {
            let v = source(name)?;
            v.as_float()
                .or_else(|| v.as_integer().map(|v| v as f64))
                .filter(|v| v.is_finite() && *v > 0. && *v <= 4096.)
                .ok_or_else(|| format!("invalid popup metric {name}"))
        };
        let aspect = number("aspect_source")?;
        if !(1.0..=4.0).contains(&aspect) {
            return Err("popup aspect must be landscape,1..4".into());
        }
        Ok(Self {
            enabled,
            cells,
            cell_width: number("font_width_source")?,
            cell_height: number("font_height_source")?,
            aspect,
        })
    }
    pub fn size(&self, kind: SurfaceKind, width: u16, height: u16) -> (u16, u16) {
        if !self.enabled {
            return (width, height);
        }
        if width == 0 || height == 0 {
            return (0, 0);
        }
        let cap = self.cells[kind as usize];
        // A watch-sized viewport is already smaller than a compact popup.
        // Let its controls use the whole available area instead of double framing.
        if width < cap[0] / 2 && height < cap[1] / 2 {
            return (width, height);
        }
        let w = cap[0].min(width.saturating_sub(2).max(1));
        let landscape = (f64::from(w) * self.cell_width / (self.cell_height * self.aspect))
            .floor()
            .max(1.) as u16;
        // Tiny terminals retain borders, one item and a back control before aspect.
        let available = if height >= 6 { height - 2 } else { height };
        let h = cap[1].min(available).min(landscape.max(4.min(height)));
        (w, h)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn vendor() -> Value {
        include_str!("../../../../usr/share/mios/mios.toml")
            .parse()
            .unwrap()
    }
    #[test]
    fn existing_sizes_bound_each_surface_and_font_cells_keep_landscape() {
        let mut doc = vendor();
        let switch: Value = "[terminal.popups]\nenabled = true\n".parse().unwrap();
        assert!(PopupPolicy::enabled_from_value(&switch).unwrap());
        assert!(
            PopupPolicy::from_value(&switch).is_err(),
            "geometry still requires its source references"
        );
        let p = PopupPolicy::from_value(&doc).unwrap();
        assert_eq!(p.size(SurfaceKind::Window, 240, 67), (94, 33));
        assert_eq!(p.size(SurfaceKind::Menu, 240, 67), (60, 18));
        assert_eq!(p.size(SurfaceKind::Dialog, 240, 67), (60, 18));
        assert_eq!(p.size(SurfaceKind::Tooltip, 240, 67), (60, 6));
        doc["theme"]["font"]["cell_h_px"] = Value::Integer(40);
        let p = PopupPolicy::from_value(&doc).unwrap();
        assert!(p.size(SurfaceKind::Window, 240, 67).1 < 33);
    }
    #[test]
    fn tiny_portrait_offset_and_missing_source_do_not_escape_bounds() {
        let mut doc = vendor();
        let p = PopupPolicy::from_value(&doc).unwrap();
        for (w, h) in [(20, 40), (29, 90)] {
            let (pw, ph) = p.size(SurfaceKind::Menu, w, h);
            assert!(ph <= 18 && ph < h && pw <= w);
            assert!(f64::from(pw) * p.cell_width >= f64::from(ph) * p.cell_height * p.aspect);
        }
        for w in 0..45 {
            for h in 0..75 {
                let (a, b) = p.size(SurfaceKind::Dialog, w, h);
                assert!(a <= w && b <= h);
                if w > 0 && h > 0 {
                    assert!(a > 0 && b > 0);
                }
            }
        }
        doc["terminal"]["popups"]["dialog_width_source"] = Value::String("missing.setting".into());
        assert!(PopupPolicy::from_value(&doc).is_err());
        assert!(
            !PopupPolicy::from_value(&Value::Table(Default::default()))
                .unwrap()
                .enabled
        );
    }
}
