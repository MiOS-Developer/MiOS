// AI-hint: Validated indexed sprite and overflow-safe viewport fitting in terminal cells.
// AI-related: render.rs, specs/tamagotchi-tui/tamagotchi-sprites-spec.md
use crate::capabilities::Renderer;

#[derive(Clone, Debug)]
pub struct Sprite {
    width: u16,
    height: u16,
    pixels: Vec<Option<u8>>,
}

impl Sprite {
    pub fn width(&self) -> u16 {
        self.width
    }
    pub fn height(&self) -> u16 {
        self.height
    }
    pub fn new(width: u16, height: u16, pixels: Vec<Option<u8>>) -> Result<Self, String> {
        if width == 0 || height == 0 || width > 256 || height > 256 {
            return Err("sprite dimensions must be in 1..=256".into());
        }
        if pixels.len() != usize::from(width) * usize::from(height) {
            return Err("sprite pixel count does not match its dimensions".into());
        }
        if pixels.iter().flatten().any(|i| *i >= 4) {
            return Err("sprite palette indices must be in 0..4".into());
        }
        Ok(Self {
            width,
            height,
            pixels,
        })
    }

    pub fn pixel(&self, x: u32, y: u32, scale: u16) -> Option<u8> {
        if scale == 0 {
            return None;
        }
        let (x, y) = (x / u32::from(scale), y / u32::from(scale));
        if x >= u32::from(self.width) || y >= u32::from(self.height) {
            return None;
        }
        self.pixels[(y * u32::from(self.width) + x) as usize]
    }

    pub fn pet(stage: crate::game::Stage, frame: usize) -> Self {
        use crate::game::Stage;
        let rows = match stage {
            Stage::Egg => return Self::egg(),
            Stage::Adult => return Self::preview(frame),
            Stage::Baby => [
                "........", "........", "...00...", "..0110..", "..0220..", "..0110..", "...00...",
                "........",
            ],
            Stage::Child => [
                "........", "..0..0..", ".011110.", ".021120.", ".011110.", "..0110..", "..0..0..",
                "........",
            ],
            Stage::Dead => [
                "........", "..0000..", ".022220.", ".020020.", ".022220.", "..0000..", "..0..0..",
                "........",
            ],
        };
        let pixels = rows
            .iter()
            .flat_map(|r| r.bytes())
            .map(|c| (c != b'.').then(|| c - b'0'))
            .collect();
        Self {
            width: 8,
            height: 8,
            pixels,
        }
    }

    pub fn egg() -> Self {
        let rows = [
            "........", "...00...", "..0330..", ".031130.", ".011110.", ".011110.", "..0000..",
            "........",
        ];
        let pixels = rows
            .iter()
            .flat_map(|r| r.bytes())
            .map(|c| (c != b'.').then(|| c - b'0'))
            .collect();
        Self {
            width: 8,
            height: 8,
            pixels,
        }
    }

    /// Original local-pet art, with two animation frames.
    pub fn preview(frame: usize) -> Self {
        let rows = [
            "..0000..", ".011110.", "01133110", "01211210", "01111110", ".011110.", "..0000..",
            ".00..00.",
        ];
        let mut pixels: Vec<_> = rows
            .iter()
            .flat_map(|row| row.bytes())
            .map(|c| (c != b'.').then(|| c - b'0'))
            .collect();
        if frame % 2 == 1 {
            pixels[3 * 8 + 2] = Some(0);
            pixels[3 * 8 + 5] = Some(0);
        }
        Self {
            width: 8,
            height: 8,
            pixels,
        }
    }
}

pub fn cell_size(width: u16, height: u16, scale: u16, renderer: Renderer) -> (u32, u32) {
    let (dx, dy) = renderer.packing();
    (
        (u32::from(width) * u32::from(scale)).div_ceil(u32::from(dx)),
        (u32::from(height) * u32::from(scale)).div_ceil(u32::from(dy)),
    )
}

/// `columns` and `rows` are the actual drawable viewport, with UI chrome already removed.
/// Zero means automatic scale; a requested fixed scale is never silently changed.
pub fn fit_scale(
    width: u16,
    height: u16,
    columns: u16,
    rows: u16,
    renderer: Renderer,
    requested: u16,
) -> Option<u16> {
    if width == 0 || height == 0 {
        return None;
    }
    [16, 8, 4, 2, 1].into_iter().find(|&scale| {
        let (w, h) = cell_size(width, height, scale, renderer);
        (requested == 0 || requested == scale) && w <= u32::from(columns) && h <= u32::from(rows)
    })
}
