// AI-hint: Shared MiOS popup bounds inherited from existing launch sizes and font cells; centered consumers retain their own scrolling and input ownership.
// AI-related: usr/share/mios/mios.toml [terminal.popups], miosd launcher, mios-gen tmux-os, mios-mons

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

/// A rectangle in terminal cells, including any border allowance selected by
/// the consumer. Geometry has no process, client, or input ownership authority.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct CellRect {
    pub x: u32,
    pub y: u32,
    pub width: u32,
    pub height: u32,
}

#[derive(Clone, Copy, Debug)]
pub struct SpiralPolicy {
    share: f64,
    start: u8,
    clockwise: bool,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct SpiralSplit {
    pub cell: CellRect,
    pub remainder: CellRect,
    pub direction: u8,
}

impl SpiralPolicy {
    /// Reuse the existing workspace SSOT for both tiled and floating cells.
    pub fn from_workspace(workspace: &Value) -> Result<Self, String> {
        let ratio = workspace.get("golden_ratio").map_or_else(
            || Ok((1.0 + 5.0_f64.sqrt()) / 2.0),
            |v| {
                v.as_float()
                    .filter(|n| n.is_finite() && *n > 1.0 && *n < 2.0)
                    .ok_or_else(|| "invalid workspace golden_ratio".to_owned())
            },
        )?;
        let layout = workspace.get("layout");
        if layout.is_some_and(|v| !v.is_table()) {
            return Err("workspace layout must be a table".into());
        }
        let field = |name: &str| layout.and_then(|v| v.get(name));
        let share = field("spiral_share").map_or(Ok(1.0 / ratio), |v| {
            v.as_float()
                .filter(|n| n.is_finite() && (0.1..=0.9).contains(n))
                .ok_or_else(|| "invalid workspace spiral_share".to_owned())
        })?;
        let start = match field("spiral_start")
            .and_then(Value::as_str)
            .unwrap_or("down")
        {
            "right" => 0,
            "down" => 1,
            "left" => 2,
            "up" => 3,
            _ => return Err("invalid workspace spiral_start".into()),
        };
        let clockwise = match field("spiral_turn")
            .and_then(Value::as_str)
            .unwrap_or("clockwise")
        {
            "clockwise" => true,
            "counterclockwise" => false,
            _ => return Err("invalid workspace spiral_turn".into()),
        };
        for name in ["spiral_start", "spiral_turn"] {
            if field(name).is_some_and(|v| !v.is_str()) {
                return Err(format!("invalid workspace {name}"));
            }
        }
        Ok(Self {
            share,
            start,
            clockwise,
        })
    }

    pub fn start(&self) -> u8 {
        self.start
    }

    pub fn turn(&self, direction: u8) -> u8 {
        (direction % 4 + if self.clockwise { 1 } else { 3 }) % 4
    }

    /// A failed preferred direction may turn once, matching the established
    /// native workspace planner. Never enlarge a fitted cell over its neighbor.
    pub fn split(
        &self,
        area: CellRect,
        direction: u8,
        minimum: (u32, u32),
        gap: u32,
    ) -> Result<SpiralSplit, String> {
        if minimum.0 == 0
            || minimum.1 == 0
            || area.width < minimum.0
            || area.height < minimum.1
            || area.x.checked_add(area.width).is_none()
            || area.y.checked_add(area.height).is_none()
        {
            return Err("spiral has no usable terminal area".into());
        }
        for direction in [direction % 4, self.turn(direction % 4)] {
            let horizontal = direction.is_multiple_of(2);
            let size = if horizontal { area.width } else { area.height };
            let floor = if horizontal { minimum.0 } else { minimum.1 };
            let Some(available) = size.checked_sub(gap) else {
                continue;
            };
            let first = (f64::from(available) * self.share).round() as u32;
            let rest = available.saturating_sub(first);
            if first < floor || rest < floor {
                continue;
            }
            let (cell, remainder) = match direction {
                0 => (
                    CellRect {
                        width: first,
                        ..area
                    },
                    CellRect {
                        x: area.x + first + gap,
                        width: rest,
                        ..area
                    },
                ),
                1 => (
                    CellRect {
                        height: first,
                        ..area
                    },
                    CellRect {
                        y: area.y + first + gap,
                        height: rest,
                        ..area
                    },
                ),
                2 => (
                    CellRect {
                        x: area.x + rest + gap,
                        width: first,
                        ..area
                    },
                    CellRect {
                        width: rest,
                        ..area
                    },
                ),
                _ => (
                    CellRect {
                        y: area.y + rest + gap,
                        height: first,
                        ..area
                    },
                    CellRect {
                        height: rest,
                        ..area
                    },
                ),
            };
            return Ok(SpiralSplit {
                cell,
                remainder,
                direction,
            });
        }
        Err("spiral cannot fit the requested terminal cells".into())
    }

    /// Only real panes are supplied by callers. Failure leaves all existing
    /// window geometry untouched so the caller can retain compact popups.
    pub fn partition(
        &self,
        area: CellRect,
        count: usize,
        minimum: (u32, u32),
        gap: u32,
    ) -> Result<Vec<CellRect>, String> {
        if count == 0 {
            return Ok(Vec::new());
        }
        if area.width < minimum.0
            || area.height < minimum.1
            || minimum.0 == 0
            || minimum.1 == 0
            || area.x.checked_add(area.width).is_none()
            || area.y.checked_add(area.height).is_none()
        {
            return Err("spiral has no usable terminal area".into());
        }
        let mut cells = Vec::new();
        let mut remaining = area;
        let mut direction = self.start;
        for index in 0..count {
            if index + 1 == count {
                cells.push(remaining);
                break;
            }
            let step = self.split(remaining, direction, minimum, gap)?;
            cells.push(step.cell);
            remaining = step.remainder;
            direction = self.turn(step.direction);
        }
        Ok(cells)
    }
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
    fn golden_blueprint_partitions_the_fibonacci_rectangle_without_overlap() {
        let policy = SpiralPolicy::from_workspace(&vendor()["mcp"]["tmux"]["workspace"]).unwrap();
        let bounds = CellRect {
            x: 7,
            y: 11,
            width: 55,
            height: 89,
        };
        let cells = policy.partition(bounds, 10, (1, 1), 0).unwrap();
        let sizes: Vec<_> = cells.iter().map(|cell| (cell.width, cell.height)).collect();
        assert_eq!(
            sizes,
            [
                (55, 55),
                (34, 34),
                (21, 21),
                (13, 13),
                (8, 8),
                (5, 5),
                (3, 3),
                (2, 2),
                (1, 1),
                (1, 1)
            ]
        );
        assert_eq!(
            cells.iter().map(|c| c.width * c.height).sum::<u32>(),
            55 * 89
        );
        for (index, cell) in cells.iter().enumerate() {
            assert!(cell.x >= bounds.x && cell.y >= bounds.y);
            assert!(cell.x + cell.width <= bounds.x + bounds.width);
            assert!(cell.y + cell.height <= bounds.y + bounds.height);
            for other in &cells[index + 1..] {
                assert!(
                    cell.x + cell.width <= other.x
                        || other.x + other.width <= cell.x
                        || cell.y + cell.height <= other.y
                        || other.y + other.height <= cell.y
                );
            }
        }
    }

    #[test]
    fn terminal_minimums_refuse_unusable_spirals_without_enlarging_cells() {
        let policy = SpiralPolicy::from_workspace(&vendor()["mcp"]["tmux"]["workspace"]).unwrap();
        let bounds = CellRect {
            x: 0,
            y: 0,
            width: 120,
            height: 48,
        };
        let cells = policy.partition(bounds, 3, (30, 8), 1).unwrap();
        assert!(cells.iter().all(|c| c.width >= 30 && c.height >= 8));
        assert!(policy.partition(bounds, 10, (30, 8), 1).is_err());
        assert!(policy
            .partition(
                CellRect {
                    x: u32::MAX,
                    ..bounds
                },
                1,
                (1, 1),
                0
            )
            .is_err());
        assert!(policy.partition(bounds, 0, (30, 8), 1).unwrap().is_empty());
        let mut custom = vendor()["mcp"]["tmux"]["workspace"].clone();
        custom["layout"]["spiral_share"] = Value::Float(0.7);
        let changed = SpiralPolicy::from_workspace(&custom)
            .unwrap()
            .partition(bounds, 2, (30, 8), 1)
            .unwrap();
        assert_eq!(changed[0].height, 33);
        assert_ne!(cells[0].height, changed[0].height);
        custom["layout"]["spiral_turn"] = Value::String("sideways".into());
        assert!(SpiralPolicy::from_workspace(&custom).is_err());
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
