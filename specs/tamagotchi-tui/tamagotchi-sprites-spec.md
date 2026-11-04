# Tamagotchi TUI - 8-Bit & Pseudo-16-Bit Sprite Specification

## Implemented graphics scope (task_3711)

The native sample now has a full-viewport room, framed care/game/status/graphics/help
menus, needs meters, an elapsed-time animation clock, separate original 8-bit and
16-bit-style stage sprites, care/mood reactions and a read-only full-screen Sprite
Studio. The implementation contract and current keys are documented in
[the sample guide](../../usr/share/doc/mios/tamagotchi-tui.md).
Saved game schema 1, the existing care rules and read-only redirected output remain
the compatibility floor. The larger catalogues, additional characters/locations,
social/economic systems and illustrative APIs below remain roadmap material.
Do not infer those future counts or APIs from the implemented graphics increment.



> The implemented sprite/widget foundation is in `tools/native/mios-tamagotchi`. See [the native preview guide](../../usr/share/doc/mios/tamagotchi-tui.md); later features remain planned.

## Document Information
- **Version:** 1.0
- **Created:** 2024-11-03
- **Author:** MiOS Development Team
- **Status:** Draft
- **Related Documents:**
  - [tamagotchi-tui-plan.md](./tamagotchi-tui-plan.md)
  - [tamagotchi-graphics-spec.md](./tamagotchi-graphics-spec.md)

---

## Table of Contents
1. [Overview](#1-overview)
2. [8-Bit Sprite System](#2-8-bit-sprite-system)
3. [Full-Fledged ASCII Character Rendering](#3-full-fledged-ascii-character-rendering)
4. [Pseudo-16-Bit Sprite System](#4-pseudo-16-bit-sprite-system)
5. [Sprite Scaling & Sizing](#5-sprite-scaling--sizing)
6. [Tamagotchi-Specific Sprites](#6-tamagotchi-specific-sprites)
7. [Sprite Registry & Caching](#7-sprite-registry--caching)
8. [Rendering Pipeline](#8-rendering-pipeline)
9. [Performance Considerations](#9-performance-considerations)
10. [Configuration](#10-configuration)

---

## 1. Overview

### 1.1 Purpose
This document specifies the 8-bit and pseudo-16-bit sprite systems for the Tamagotchi TUI application, enabling:
- Classic 8-bit pixel art aesthetics at low resolutions
- Full ASCII/Unicode character rendering for all terminal types
- Pseudo-16-bit color depth using multi-character pixel techniques
- Scalable rendering across different terminal sizes and capabilities

### 1.2 Scope
- 8-bit sprite format and encoding (NES/Game Boy inspired)
- ASCII character mapping for pixel data
- Pseudo-16-bit techniques using braille, blocks, and layering
- Scaling system (S1-S5) for different terminal resolutions
- Tamagotchi-specific sprite definitions
- Integration with the adaptive rendering backend

### 1.3 Design Philosophy
- **Nostalgia:** Capture the authentic Tamagotchi aesthetic
- **Flexibility:** Support all terminal types from VT100 to modern terminals
- **Performance:** Efficient rendering with minimal CPU usage
- **Compatibility:** Work with MiOS Inputd, TTY0/VT0, headless mode
- **Scalability:** Render at any size while maintaining visual fidelity

---

## 2. 8-Bit Sprite System

### 2.1 Sprite Format

#### 2.1.1 Mono-Chrome 8x8 Sprites (1-bit per pixel)
```
Format: 8 bytes per sprite (1 byte per row)
Each bit represents a pixel (0 = transparent/background, 1 = foreground)
```

```rust
#[derive(Clone, Debug)]
pub struct Sprite8x8Mono {
    pub data: [u8; 8],      // 8 rows of 8 pixels
    pub fg_color: u8,       // Palette index for foreground (0-15 for 16-color)
    pub bg_color: u8,       // Palette index for background (0-15 for 16-color)
}
```

#### 2.1.2 8-Bit Color Sprites (2-bit per pixel, 4 colors per sprite)
```
Format: 16 bytes per sprite (2 bytes per row)
Each 2-bit nibble represents a pixel color index (0-3)
```

```rust
#[derive(Clone, Debug)]
pub struct Sprite8x8Color {
    pub data: [u8; 16],     // 16 bytes = 8 rows * 2 bytes/row = 128 bits = 64 pixels * 2 bits
    pub palette: [u8; 4],   // 4 color indices into global palette
}
```

#### 2.1.3 8-Bit Extended Sprites (Variable Size)
```
Format: Width x Height with 1 or 2 bits per pixel
Supports: 8x8, 8x16, 16x8, 16x16, 32x32
```

```rust
#[derive(Clone, Debug)]
pub enum Sprite8Bit {
    Mono8x8(Sprite8x8Mono),
    Color8x8(Sprite8x8Color),
    Mono16x16 { data: [u8; 32], fg: u8, bg: u8 },  // 32 bytes = 16 rows * 2 bytes
    Color16x16 { data: [u8; 64], palette: [u8; 4] }, // 64 bytes = 16 rows * 4 bytes
    Mono32x32 { data: Vec<u8>, fg: u8, bg: u8, width: u8, height: u8 },
    Color32x32 { data: Vec<u8>, palette: [u8; 4], width: u8, height: u8 },
}
```

### 2.2 Color Palettes

#### 2.2.1 NES-Style Palette (16 Colors)
```rust
pub const NES_PALETTE: [u8; 16] = [
    0x00, 0x00, 0x00,  // 0: Black / Transparent
    0x00, 0x00, 0xAA,  // 1: Blue
    0x00, 0xAA, 0x00,  // 2: Green
    0x00, 0xAA, 0xAA,  // 3: Cyan
    0xAA, 0x00, 0x00,  // 4: Red
    0xAA, 0x00, 0xAA,  // 5: Magenta
    0xAA, 0x55, 0x00,  // 6: Brown
    0xAA, 0xAA, 0xAA,  // 7: Light Gray
    0x55, 0x55, 0x55,  // 8: Dark Gray
    0x55, 0x55, 0xFF,  // 9: Light Blue
    0x55, 0xFF, 0x55,  // 10: Light Green
    0x55, 0xFF, 0xFF,  // 11: Light Cyan
    0xFF, 0x55, 0x55,  // 12: Light Red
    0xFF, 0x55, 0xFF,  // 13: Light Magenta
    0xFF, 0xFF, 0x55,  // 14: Yellow
    0xFF, 0xFF, 0xFF,  // 15: White
];
```

#### 2.2.2 Game Boy-Style Palette (4 Colors per Sprite)
```rust
// Each sprite has its own 4-color palette
// Colors are indices into the global 256-color or 16-color palette
pub struct SpritePalette {
    pub colors: [u8; 4],  // Color indices (0-15 for 16-color, 0-255 for 256-color)
}
```

### 2.3 Sprite Encoding & Storage

#### 2.3.1 Binary Format (For Embedded Sprites)
```
Header: 4 bytes
- Magic: 0x54, 0x41, 0x4D, 0x41 ("TAMA")
- Version: 1 byte (0x01 for 8-bit)
- Type: 1 byte (0x00 = Mono 8x8, 0x01 = Color 8x8, 0x02 = Mono 16x16, etc.)
- Reserved: 2 bytes

Data: Variable based on type
- Mono 8x8: 8 bytes + 2 bytes (fg, bg)
- Color 8x8: 16 bytes + 4 bytes (palette)
```

#### 2.3.2 Rust Struct Format (For Runtime)
```rust
#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct Sprite8BitDefinition {
    pub id: String,
    pub name: String,
    pub width: u8,
    pub height: u8,
    pub bpp: u8,  // 1 or 2
    pub data: Vec<u8>,
    pub palette: Option<Vec<u8>>,  // Some for color sprites
    pub fg_color: Option<u8>,      // For mono sprites
    pub bg_color: Option<u8>,      // For mono sprites
}
```

### 2.4 Sprite Rendering (8-Bit to Terminal)

#### 2.4.1 Mono 8x8 to Block Characters
Pixels use row-major order `[top_left, top_right, bottom_left, bottom_right]`,
with weights 1, 2, 4 and 8. The native `render::quadrant` implementation indexes
`ratatui::symbols::pixel::QUADRANTS`, including the existing half/full block
characters. Do not map masks by Unicode codepoint arithmetic.


#### 2.4.2 Mono 8x8 to ASCII Art
```rust
/// Simple ASCII representation for 8x8 sprite
fn sprite8x8_to_ascii(sprite: &Sprite8x8Mono) -> String {
    let mut result = String::with_capacity(64);
    for row in 0..8 {
        for col in 0..8 {
            let pixel = (sprite.data[row] >> (7 - col)) & 1;
            result.push(if pixel == 1 { sprite.fg_char() } else { sprite.bg_char() });
        }
        result.push('\n');
    }
    result
}
```

#### 2.4.3 Color 8x8 Rendering
For terminals with 256+ color support, each pixel can be rendered as a colored block or character.

---

## 3. Full-Fledged ASCII Character Rendering

### 3.1 Character Sets

#### 3.1.1 Standard ASCII (32-126)
```
Basic characters for simple rendering:
- Space (32) for empty pixels
- @, #, %, *, +, =, -, | for various densities
- O, o, . for circular shapes
- /, \, |, -, + for lines
```

#### 3.1.2 Extended ASCII (128-255)
```
Additional characters for better rendering:
- █, ▓, ▒, ░ for shading
- ▀, ▄, ▐, ░ for half-blocks
- ┌, ┐, └, ┘ for corners
- ─, │, ┼, ┴, ├, ┤ for lines
```

#### 3.1.3 Unicode Block Elements (U+2580-U+259F)
```
Full block: █ (U+2588)
Upper half: ▀ (U+2580)
Lower half: ▄ (U+2584)
Left half: ▌ (U+258C)
Right half: ▐ (U+2590)
Light shade: ░ (U+2591)
Medium shade: ▒ (U+2592)
Dark shade: ▓ (U+2593)
```

#### 3.1.4 Unicode Braille Patterns (U+2800-U+28FF)
```
Each braille character is 2x4 pixels:
- U+2800: ⠀ (no dots)
- U+2801: ⠁ (dot 1)
- U+2803: ⠃ (dots 1,2)
- ...
- U+28FF: ⣿ (all dots)

This allows 2x4 pixel resolution per character cell.
```

### 3.2 ASCII Art Mapping Strategies

#### 3.2.1 Density-Based Mapping
```rust
/// Map pixel density to ASCII character
fn density_to_ascii(density: f32) -> char {
    match density {
        0.0 => ' ',
        d if d < 0.1 => '.',
        d if d < 0.25 => ':',
        d if d < 0.4 => '!',
        d if d < 0.5 => '*',
        d if d < 0.6 => 'o',
        d if d < 0.75 => 'O',
        d if d < 0.9 => '#',
        _ => '@',
    }
}
```

#### 3.2.2 2x2 Block to Single Character
```rust
/// Map 2x2 pixel block to single ASCII character
fn block2x2_to_char(block: [[bool; 2]; 2]) -> char {
    let count = block.iter().flatten().filter(|&&p| p).count();
    match count {
        0 => ' ',
        1 => if block[0][0] { '▗' } else if block[0][1] { '▖' } 
             else if block[1][0] { '▘' } else { '▄' },
        2 => if block[0][0] && block[0][1] { '▀' }
             else if block[1][0] && block[1][1] { '▄' }
             else if block[0][0] && block[1][0] { '▌' }
             else if block[0][1] && block[1][1] { '▐' }
             else { '▒' },
        3 => '▓',
        4 => '█',
        _ => '▓',
    }
}
```

#### 3.2.3 Character-Based Dithering
```rust
/// Bayer ordered dithering for ASCII art
fn dither_ascii(image: &[Vec<bool>], threshold_map: &[Vec<f32>]) -> Vec<String> {
    let mut result = Vec::new();
    for (y, row) in image.iter().enumerate() {
        let mut line = String::new();
        for (x, &pixel) in row.iter().enumerate() {
            let threshold = threshold_map[y % 4][x % 4];
            let intensity = if pixel { 1.0 } else { 0.0 };
            let dithered = intensity >= threshold;
            line.push(if dithered { '*' } else { ' ' });
        }
        result.push(line);
    }
    result
}
```

### 3.3 ASCII Art Styles

#### 3.3.1 Simple ASCII (Tier 0)
```
Uses only standard ASCII characters (32-126)
Best for: VT100, basic terminals, headless mode
Characters: .:-=+*#%@
```

#### 3.3.2 Extended ASCII (Tier 1)
```
Uses extended ASCII (128-255)
Best for: Most modern terminals
Characters: █▓▒░▀▄▐▌
```

#### 3.3.3 Unicode Blocks (Tier 2)
```
Uses Unicode block elements
Best for: UTF-8 terminals
Characters: Full range of U+2580-U+259F
```

#### 3.3.4 Unicode Braille (Tier 3)
```
Uses Unicode braille patterns
Best for: High-resolution UTF-8 terminals
Characters: Full range of U+2800-U+28FF
```

### 3.4 ASCII Art Generation from Sprites

#### 3.4.1 Sprite to ASCII Conversion
```rust
pub trait ToAscii {
    fn to_ascii(&self, style: AsciiStyle) -> Vec<String>;
}

impl ToAscii for Sprite8x8Mono {
    fn to_ascii(&self, style: AsciiStyle) -> Vec<String> {
        match style {
            AsciiStyle::Simple => self.to_simple_ascii(),
            AsciiStyle::Extended => self.to_extended_ascii(),
            AsciiStyle::Unicode => self.to_unicode_ascii(),
            AsciiStyle::Braille => self.to_braille_ascii(),
        }
    }
}
```

---

## 4. Pseudo-16-Bit Sprite System

### 4.1 Overview
Pseudo-16-bit rendering achieves higher color depth and resolution by:
1. Using multi-character pixels (2x2, 3x3, etc.)
2. Layering characters with different colors
3. Using braille patterns for 2x4 pixel resolution
4. Combining foreground and background colors

### 4.2 Techniques

#### 4.2.1 Braille-Based Pseudo-16-Bit
```
Each braille character = 2x4 pixels = 8 pixels
Each pixel can be one of 2 colors (foreground/background)
With 256-color terminal: 256 * 256 = 65,536 color combinations per braille char
This approximates 16-bit color (65,536 colors)
```

```rust
/// Braille-based pseudo-16-bit pixel
/// Each braille char represents 2x4 pixels
/// Each pixel can be fg or bg color
pub struct BraillePixel {
    pub dots: u8,  // 8 bits for 8 possible dots
    pub fg_color: u8,
    pub bg_color: u8,
}

impl BraillePixel {
    pub fn to_char(&self) -> char {
        // Map dots to braille Unicode (U+2800 + dots pattern)
        char::from_u32(0x2800 + self.dots as u32).unwrap_or(' ')
    }
}
```

#### 4.2.2 Multi-Character Pixels
```
Use 2x2 block of characters to represent one pixel:
- Each character has its own foreground color
- Background color is shared
- Achieves 4 sub-pixels per logical pixel
```

```rust
/// 2x2 multi-character pixel
pub struct MultiCharPixel {
    pub chars: [[char; 2]; 2],
    pub colors: [[u8; 2]; 2],  // Color for each character
    pub bg_color: u8,
}
```

#### 4.2.3 Color Layering
```
Overlay multiple characters with different colors:
- First layer: Background color
- Second layer: Foreground characters with their colors
- Third layer: Additional highlight characters
```

#### 4.2.4 Dithering for Color Depth
```rust
/// Floyd-Steinberg dithering for pseudo-16-bit
pub fn dither_pseudo_16bit(
    image: &ImageBuffer<Rgba<u8>, Vec<u8>>,
    palette: &[Rgb<u8>; 256],
) -> Vec<Vec<BraillePixel>> {
    // Convert image to dithered braille pixels
    // using Floyd-Steinberg error diffusion
    unimplemented!()
}
```

### 4.3 Pseudo-16-Bit Sprite Format

```rust
#[derive(Clone, Debug)]
pub enum Pseudo16BitSprite {
    /// Braille-based: 2x4 pixels per character
    Braille {
        width: u16,           // In pixels
        height: u16,          // In pixels
        data: Vec<BraillePixel>, // One per 2x4 pixel block
    },
    /// Multi-character: 2x2 characters per pixel
    MultiChar {
        width: u16,
        height: u16,
        data: Vec<MultiCharPixel>,
    },
    /// Layered: Multiple layers of characters
    Layered {
        width: u16,
        height: u16,
        layers: Vec<Vec<Vec<(char, u8)>>>, // (char, color_index) per layer
    },
}
```

### 4.4 Rendering Pseudo-16-Bit Sprites

#### 4.4.1 Braille Rendering
```rust
impl Pseudo16BitSprite {
    pub fn render_braille(&self, terminal: &mut Terminal) -> Result<(), Box<dyn Error>> {
        if let Pseudo16BitSprite::Braille { width, height, data } = self {
            let chars_per_row = (width + 1) / 2; // 2 pixels per braille char width
            let rows = (height + 3) / 4; // 4 pixels per braille char height
            
            for row in 0..rows {
                for col in 0..chars_per_row {
                    let idx = row * chars_per_row + col;
                    if idx < data.len() {
                        let pixel = &data[idx];
                        terminal.set_fg(pixel.fg_color)?;
                        terminal.set_bg(pixel.bg_color)?;
                        terminal.print(pixel.to_char())?;
                    }
                }
                terminal.println()?;
            }
        }
        Ok(())
    }
}
```

#### 4.4.2 Multi-Character Rendering
```rust
/// Render multi-character pixel to terminal
fn render_multi_char_pixel(
    terminal: &mut Terminal,
    pixel: &MultiCharPixel,
) -> Result<(), Box<dyn Error>> {
    for row in 0..2 {
        for col in 0..2 {
            terminal.set_fg(pixel.colors[row][col])?;
            terminal.set_bg(pixel.bg_color)?;
            terminal.print(pixel.chars[row][col])?;
        }
        terminal.println()?;
    }
    Ok(())
}
```

---

## 5. Sprite Scaling & Sizing

### 5.1 Scale Levels

| Scale | Name | Character Size | Effective Resolution | Use Case |
|-------|------|----------------|----------------------|----------|
| S1 | Tiny | 1x1 | Original | Headless, very small terminals |
| S2 | Small | 2x2 | 2x original | Small terminals (80x24) |
| S3 | Medium | 4x4 | 4x original | Medium terminals (80x40) |
| S4 | Large | 8x8 | 8x original | Large terminals (120x60) |
| S5 | Huge | 16x16 | 16x original | Very large terminals (160x90) |

### 5.2 Scale Detection

The native `sprite::fit_scale` selects the largest fitting scale from
1, 2, 4, 8 and 16 using BOTH drawable dimensions after UI chrome is removed:

```text
ceil(sprite_width * scale / pixels_per_cell_x) <= viewport_columns
ceil(sprite_height * scale / pixels_per_cell_y) <= viewport_rows
```

Packing is 1x1 for ASCII, 1x2 for half-blocks, 2x2 for quadrants and 2x4 for
Braille. Intermediate products use `u32`. A requested fixed scale that does not
fit returns no fit. The scale/use-case table above is illustrative: for the old
full-cell 8x8 algorithm, S4 needs 64 rows and S5 needs 128 rows, so its 120x60 and
160x90 examples cannot use those advertised scales. Area alone is insufficient:
320x25 selects S4 in the retired area algorithm, producing 64 rows in 25.


### 5.3 Scaling Algorithms

#### 5.3.1 Nearest Neighbor
```rust
pub fn scale_nearest_neighbor(
    sprite: &Sprite8x8Mono,
    scale: SpriteScale,
) -> Vec<String> {
    let scale_factor = match scale {
        SpriteScale::S1 => 1,
        SpriteScale::S2 => 2,
        SpriteScale::S3 => 4,
        SpriteScale::S4 => 8,
        SpriteScale::S5 => 16,
    };
    
    let mut result = Vec::new();
    for y in 0..8 * scale_factor {
        let mut line = String::new();
        let src_y = y / scale_factor;
        for x in 0..8 * scale_factor {
            let src_x = x / scale_factor;
            let pixel = (sprite.data[src_y] >> (7 - src_x)) & 1;
            line.push(if pixel == 1 { '█' } else { ' ' });
        }
        result.push(line);
    }
    result
}
```

#### 5.3.2 Bilinear Interpolation
```rust
pub fn scale_bilinear(
    sprite: &Sprite8x8Mono,
    target_width: usize,
    target_height: usize,
) -> Vec<String> {
    let mut result = Vec::with_capacity(target_height);
    
    for y in 0..target_height {
        let src_y = (y as f32 / target_height as f32) * 8.0;
        let y_floor = src_y.floor() as usize;
        let y_frac = src_y - y_floor as f32;
        
        let mut line = String::with_capacity(target_width);
        for x in 0..target_width {
            let src_x = (x as f32 / target_width as f32) * 8.0;
            let x_floor = src_x.floor() as usize;
            let x_frac = src_x - x_floor as f32;
            
            // Get 4 surrounding pixels
            let p00 = ((sprite.data[y_floor] >> (7 - x_floor)) & 1) as f32;
            let p01 = if x_floor + 1 < 8 { ((sprite.data[y_floor] >> (7 - (x_floor + 1))) & 1) as f32 } else { p00 };
            let p10 = if y_floor + 1 < 8 { ((sprite.data[y_floor + 1] >> (7 - x_floor)) & 1) as f32 } else { p00 };
            let p11 = if y_floor + 1 < 8 && x_floor + 1 < 8 {
                ((sprite.data[y_floor + 1] >> (7 - (x_floor + 1))) & 1) as f32
            } else { p00 };
            
            // Bilinear interpolation
            let top = p00 * (1.0 - x_frac) + p01 * x_frac;
            let bottom = p10 * (1.0 - x_frac) + p11 * x_frac;
            let value = top * (1.0 - y_frac) + bottom * y_frac;
            
            line.push(if value > 0.5 { '█' } else { ' ' });
        }
        result.push(line);
    }
    result
}
```

### 5.4 Adaptive Scaling

```rust
pub fn render_scaled(
    sprite: &dyn Sprite,
    terminal: &mut Terminal,
    available_width: u16,
    available_height: u16,
) -> Result<(), Box<dyn Error>> {
    // Calculate maximum scale that fits
    let sprite_width = sprite.width();
    let sprite_height = sprite.height();
    
    let max_scale_w = available_width / sprite_width;
    let max_scale_h = available_height / sprite_height;
    let scale = max_scale_w.min(max_scale_h);
    
    // Render at appropriate scale
    match scale {
        s if s >= 16 => sprite.render_at_scale(terminal, SpriteScale::S5),
        s if s >= 8 => sprite.render_at_scale(terminal, SpriteScale::S4),
        s if s >= 4 => sprite.render_at_scale(terminal, SpriteScale::S3),
        s if s >= 2 => sprite.render_at_scale(terminal, SpriteScale::S2),
        _ => sprite.render_at_scale(terminal, SpriteScale::S1),
    }
}
```

---

## 6. Tamagotchi-Specific Sprites

### 6.1 Sprite Categories

| Category | Count | Description |
|----------|-------|-------------|
| Characters | 50+ | All Tamagotchi species and variants |
| Faces | 100+ | Different expressions and emotions |
| Items | 30+ | Food, toys, accessories |
| UI | 20+ | Icons, buttons, frames |
| Animations | 50+ | Walking, eating, playing, etc. |
| Backgrounds | 10+ | Room backgrounds, outdoor scenes |
| Effects | 15+ | Particles, status indicators |

### 6.2 Character Sprites

#### 6.2.1 Species List
```
Generation 1 (Original):
- Mametchi, Kuchipatchi, Tamatchi, etc.

Generation 2 (Connection):
- V1: Ichigotchi, Kuchipatchi, Mametchi, etc.
- V2: Gozarutchi, Kuchipatchi, Mametchi, etc.

Generation 3 (Plus):
- Tamagotchi Plus: Mametchi, Kuchipatchi, Gozarutchi, etc.
- Tamagotchi Plus Color: All previous + new color variants

Generation 4 (P's):
- Tamagotchi P's: New characters with animations

Generation 5 (On/Meets):
- Tamagotchi On: Modern characters with more detail
- Tamagotchi Meets: Similar to On with regional differences

Generation 6 (Smart):
- Tamagotchi Smart: High-resolution characters

Generation 7 (Uni):
- Tamagotchi Uni: Customizable characters

Generation 8 (Pix):
- Tamagotchi Pix: Camera-based characters
```

#### 6.2.2 Life Stages
```
All Tamagotchi go through these stages:
1. Baby (0-3 days)
2. Child (3-7 days)
3. Teen (7-10 days)
4. Adult (10+ days)
5. Senior (Old age)
```

#### 6.2.3 Sprite Definitions

```rust
// Example: Mametchi adult sprite
pub const MAMETCHI_ADULT: Sprite8x8Color = Sprite8x8Color {
    data: [
        // Row 0: Head top
        0b00111100, 0b00000000,
        // Row 1: Head middle
        0b01111110, 0b00000000,
        // Row 2: Eyes
        0b01000010, 0b00000000,
        // Row 3: Nose
        0b01011010, 0b00000000,
        // Row 4: Mouth
        0b01000010, 0b00000000,
        // Row 5: Body
        0b01111110, 0b00000000,
        // Row 6: Arms
        0b00110110, 0b00000000,
        // Row 7: Legs
        0b00010100, 0b00000000,
    ],
    palette: [0x0F, 0x00, 0x04, 0x0C], // White, Transparent, Red, Light Blue
};
```

### 6.3 Face Sprites (Expressions)

```rust
pub enum FaceExpression {
    Happy,
    Sad,
    Angry,
    Surprised,
    Sleepy,
    Sick,
    Hungry,
    Playful,
    Shy,
    Excited,
}

// Each character has multiple face sprites for different expressions
pub struct CharacterFaces {
    pub happy: Sprite8x8Color,
    pub sad: Sprite8x8Color,
    pub angry: Sprite8x8Color,
    pub surprised: Sprite8x8Color,
    pub sleepy: Sprite8x8Color,
    pub sick: Sprite8x8Color,
    pub hungry: Sprite8x8Color,
    pub playful: Sprite8x8Color,
}
```

### 6.4 Item Sprites

```rust
pub enum ItemType {
    Food(FoodType),
    Toy(ToyType),
    Medicine,
    Discipline,
    Attention,
    Special,
}

pub struct ItemSprite {
    pub id: String,
    pub name: String,
    pub item_type: ItemType,
    pub sprite: Sprite8x8Color,
    pub icon: char,  // Single character representation for UI
}
```

### 6.5 UI Sprites

```rust
pub enum UISprite {
    // Icons
    HeartEmpty,
    HeartFull,
    HeartHalf,
    HungerIcon,
    HappinessIcon,
    HealthIcon,
    DisciplineIcon,
    
    // Buttons
    ButtonA,
    ButtonB,
    ButtonUp,
    ButtonDown,
    ButtonLeft,
    ButtonRight,
    
    // Frames
    FrameTopLeft,
    FrameTopRight,
    FrameBottomLeft,
    FrameBottomRight,
    FrameHorizontal,
    FrameVertical,
    
    // Status indicators
    StatusHappy,
    StatusSad,
    StatusAngry,
    StatusSick,
    StatusHungry,
    
    // Progress bars
    ProgressEmpty,
    Progress25,
    Progress50,
    Progress75,
    Progress100,
}
```

### 6.6 Animation Sprites

```rust
pub struct Animation {
    pub id: String,
    pub name: String,
    pub frames: Vec<Sprite8x8Color>,
    pub frame_delays: Vec<u64>,  // Milliseconds per frame
    pub loop_count: Option<usize>, // None = infinite loop
}

// Example animations:
// - Walking (4 frames)
// - Eating (6 frames)
// - Playing (8 frames)
// - Sleeping (2 frames: eyes open, eyes closed)
// - Jumping (5 frames)
// - Dancing (10 frames)
// - Crying (3 frames)
// - Laughing (4 frames)
```

### 6.7 Background Sprites

```rust
pub enum BackgroundType {
    RoomDay,
    RoomNight,
    OutsideDay,
    OutsideNight,
    Park,
    Beach,
    School,
    Restaurant,
    Shop,
    SpecialLocation,
}

pub struct Background {
    pub id: String,
    pub name: String,
    pub background_type: BackgroundType,
    pub tiles: Vec<Sprite8x8Color>,  // Tiles for tiled background
    pub width: u16,
    pub height: u16,
}
```

### 6.8 Effect Sprites

```rust
pub enum EffectType {
    // Particles
    Sparkle,
    Heart,
    Star,
    Note,
    
    // Status effects
    Poof,      // Appears when character evolves
    Sweat,     // When character is hot
    Shiver,    // When character is cold
    Tears,     // When character is sad
    
    // Notifications
    Exclamation,
    Question,
    PlusOne,
    LevelUp,
}

pub struct Effect {
    pub id: String,
    pub effect_type: EffectType,
    pub sprite: Sprite8x8Color,
    pub animation: Option<Animation>,
    pub lifetime: u64,  // Milliseconds
}
```

---

## 7. Sprite Registry & Caching

### 7.1 Sprite Registry

```rust
pub struct SpriteRegistry {
    sprites_8bit: HashMap<String, Sprite8Bit>,
    sprites_pseudo16: HashMap<String, Pseudo16BitSprite>,
    ascii_sprites: HashMap<String, Vec<String>>,  // Pre-rendered ASCII
    braille_sprites: HashMap<String, Vec<Vec<char>>>,  // Pre-rendered braille
    
    // Category indexes
    characters: HashMap<String, Vec<String>>,  // species -> sprite IDs
    items: HashMap<String, Vec<String>>,       // item type -> sprite IDs
    ui: HashMap<String, Vec<String>>,          // ui element -> sprite IDs
    animations: HashMap<String, Vec<String>>,  // animation -> frame IDs
    backgrounds: HashMap<String, Vec<String>>, // background -> tile IDs
    effects: HashMap<String, Vec<String>>,     // effect -> sprite IDs
}

impl SpriteRegistry {
    pub fn new() -> Self {
        Self {
            sprites_8bit: HashMap::new(),
            sprites_pseudo16: HashMap::new(),
            ascii_sprites: HashMap::new(),
            braille_sprites: HashMap::new(),
            characters: HashMap::new(),
            items: HashMap::new(),
            ui: HashMap::new(),
            animations: HashMap::new(),
            backgrounds: HashMap::new(),
            effects: HashMap::new(),
        }
    }
    
    pub fn register_8bit(&mut self, id: &str, sprite: Sprite8Bit) {
        self.sprites_8bit.insert(id.to_string(), sprite);
    }
    
    pub fn register_pseudo16(&mut self, id: &str, sprite: Pseudo16BitSprite) {
        self.sprites_pseudo16.insert(id.to_string(), sprite);
    }
    
    pub fn get(&self, id: &str) -> Option<&Sprite8Bit> {
        self.sprites_8bit.get(id)
    }
    
    pub fn get_ascii(&mut self, id: &str, style: AsciiStyle) -> Vec<String> {
        if let Some(ascii) = self.ascii_sprites.get(id) {
            return ascii.clone();
        }
        
        if let Some(sprite) = self.sprites_8bit.get(id) {
            let ascii = sprite.to_ascii(style);
            self.ascii_sprites.insert(id.to_string(), ascii.clone());
            ascii
        } else {
            vec!["?.".to_string()]
        }
    }
}
```

### 7.2 Sprite Loading

```rust
pub enum SpriteSource {
    Embedded,      // Compiled into binary
    FileSystem,    // Loaded from files
    Network,       // Downloaded from server
    Generated,     // Procedurally generated
}

pub struct SpriteLoader {
    registry: SpriteRegistry,
    sources: Vec<SpriteSource>,
}

impl SpriteLoader {
    pub fn load_all(&mut self) -> Result<(), Box<dyn Error>> {
        self.load_embedded_sprites()?;
        self.load_filesystem_sprites()?;
        self.load_custom_sprites()?;
        Ok(())
    }
    
    fn load_embedded_sprites(&mut self) -> Result<(), Box<dyn Error>> {
        // Load all built-in sprites
        self.registry.register_8bit("mametchi_adult", MAMETCHI_ADULT);
        // ... more sprites
        Ok(())
    }
    
    fn load_filesystem_sprites(&mut self) -> Result<(), Box<dyn Error>> {
        // Load from ~/.config/tamagotchi/sprites/
        // Load from /usr/share/tamagotchi/sprites/
        unimplemented!()
    }
    
    fn load_custom_sprites(&mut self) -> Result<(), Box<dyn Error>> {
        // Load user-created sprites
        unimplemented!()
    }
}
```

### 7.3 Sprite Caching

```rust
pub struct SpriteCache {
    // Cache for rendered sprites at different scales
    rendered: HashMap<(String, SpriteScale, AsciiStyle), Vec<String>>,
    
    // Cache for pseudo-16-bit rendered sprites
    rendered_pseudo16: HashMap<(String, SpriteScale), Vec<String>>,
    
    // LRU cache for recently used sprites
    lru: LruCache<String, ()>,
    
    // Statistics
    hits: AtomicUsize,
    misses: AtomicUsize,
}

impl SpriteCache {
    pub fn new(max_size: usize) -> Self {
        Self {
            rendered: HashMap::new(),
            rendered_pseudo16: HashMap::new(),
            lru: LruCache::new(max_size),
            hits: AtomicUsize::new(0),
            misses: AtomicUsize::new(0),
        }
    }
    
    pub fn get_or_render<F>(&mut self, key: (String, SpriteScale, AsciiStyle), render_fn: F) -> Vec<String>
    where
        F: FnOnce() -> Vec<String>,
    {
        if let Some(rendered) = self.rendered.get(&key) {
            self.hits.fetch_add(1, Ordering::Relaxed);
            rendered.clone()
        } else {
            self.misses.fetch_add(1, Ordering::Relaxed);
            let rendered = render_fn();
            self.rendered.insert(key, rendered.clone());
            self.lru.put(key.0.clone(), ());
            rendered
        }
    }
    
    pub fn stats(&self) -> (usize, usize) {
        (self.hits.load(Ordering::Relaxed), self.misses.load(Ordering::Relaxed))
    }
}
```

---

## 8. Rendering Pipeline

### 8.1 Pipeline Overview

```
Input: Sprite + Terminal Capabilities + Scale
    |
    v
[Sprite Selection] --> Choose appropriate sprite variant
    |
    v
[Capability Detection] --> Detect terminal features (colors, Unicode, size)
    |
    v
[Scale Selection] --> Determine optimal scale
    |
    v
[Format Selection] --> Choose rendering format (ASCII, Extended, Unicode, Braille)
    |
    v
[Rendering] --> Convert sprite to terminal characters
    |
    v
[Color Mapping] --> Map sprite colors to terminal colors
    |
    v
[Caching] --> Cache rendered result
    |
    v
Output: Vec<String> (lines of characters with color info)
```

### 8.2 Rendering Context

```rust
pub struct RenderContext<'a> {
    pub terminal: &'a mut Terminal,
    pub capabilities: TerminalCapabilities,
    pub scale: SpriteScale,
    pub ascii_style: AsciiStyle,
    pub color_mode: ColorMode,
    pub palette: &'a [Rgb<u8>; 256],
}

pub enum TerminalCapabilities {
    Basic,       // 16 colors, no Unicode
    Extended,    // 16 colors, extended ASCII
    Color256,    // 256 colors, Unicode blocks
    TrueColor,   // 24-bit color, full Unicode
}

pub enum AsciiStyle {
    Simple,
    Extended,
    Unicode,
    Braille,
}

pub enum ColorMode {
    Mono,
    Color16,
    Color256,
    TrueColor,
}
```

### 8.3 Sprite Renderer

```rust
pub struct SpriteRenderer {
    registry: SpriteRegistry,
    cache: SpriteCache,
}

impl SpriteRenderer {
    pub fn new(registry: SpriteRegistry) -> Self {
        Self {
            registry,
            cache: SpriteCache::new(1000),
        }
    }
    
    pub fn render(
        &mut self,
        sprite_id: &str,
        context: &RenderContext,
    ) -> Result<Vec<(String, Option<u8>, Option<u8>)>, Box<dyn Error>> {
        // Get sprite from registry
        let sprite = self.registry.get(sprite_id)
            .ok_or_else(|| format!("Sprite not found: {}", sprite_id))?;
        
        // Check cache
        let key = (sprite_id.to_string(), context.scale, context.ascii_style);
        
        self.cache.get_or_render(key, || {
            // Render based on capabilities
            match context.capabilities {
                TerminalCapabilities::TrueColor => {
                    self.render_true_color(sprite, context)
                }
                TerminalCapabilities::Color256 => {
                    self.render_256_color(sprite, context)
                }
                TerminalCapabilities::Extended => {
                    self.render_extended(sprite, context)
                }
                TerminalCapabilities::Basic => {
                    self.render_basic(sprite, context)
                }
            }
        })
    }
    
    fn render_true_color(
        &self,
        sprite: &Sprite8Bit,
        context: &RenderContext,
    ) -> Vec<(String, Option<u8>, Option<u8>)> {
        // Render with true color support
        // Each character can have its own foreground and background color
        unimplemented!()
    }
    
    fn render_256_color(
        &self,
        sprite: &Sprite8Bit,
        context: &RenderContext,
    ) -> Vec<(String, Option<u8>, Option<u8>)> {
        // Render with 256-color support
        // Use color indices from palette
        unimplemented!()
    }
    
    fn render_extended(
        &self,
        sprite: &Sprite8Bit,
        context: &RenderContext,
    ) -> Vec<(String, Option<u8>, Option<u8>)> {
        // Render with extended ASCII (16 colors)
        // Use extended ASCII characters
        unimplemented!()
    }
    
    fn render_basic(
        &self,
        sprite: &Sprite8Bit,
        context: &RenderContext,
    ) -> Vec<(String, Option<u8>, Option<u8>)> {
        // Render with basic ASCII (16 colors)
        // Use standard ASCII characters only
        unimplemented!()
    }
}
```

### 8.4 Batch Rendering

```rust
pub struct BatchRenderer {
    renderer: SpriteRenderer,
}

impl BatchRenderer {
    pub fn render_scene(
        &mut self,
        scene: &Scene,
        context: &RenderContext,
    ) -> Result<Vec<String>, Box<dyn Error>> {
        // Scene contains multiple sprites with positions
        // Render all sprites in correct order (background to foreground)
        
        // Sort sprites by z-index
        let mut sprites: Vec<_> = scene.sprites.iter().collect();
        sprites.sort_by(|a, b| a.z_index.cmp(&b.z_index));
        
        // Create buffer
        let mut buffer = vec![vec![(' ', None, None); context.terminal_width as usize]; 
                             context.terminal_height as usize];
        
        // Render each sprite
        for sprite_ref in sprites {
            let rendered = self.renderer.render(&sprite_ref.sprite_id, context)?;
            // Composite rendered sprite into buffer at correct position
            self.composite(&mut buffer, &rendered, sprite_ref.x, sprite_ref.y)?;
        }
        
        // Convert buffer to lines
        Ok(buffer.iter().map(|row| {
            row.iter().map(|(c, fg, bg)| {
                format_char(*c, *fg, *bg)
            }).collect()
        }).collect())
    }
    
    fn composite(
        &self,
        buffer: &mut Vec<Vec<(char, Option<u8>, Option<u8>)>>,
        sprite: &[(String, Option<u8>, Option<u8>)],
        x: u16,
        y: u16,
    ) -> Result<(), Box<dyn Error>> {
        // Composite sprite into buffer at position (x, y)
        // Handle transparency and z-order
        unimplemented!()
    }
}
```

---

## 9. Performance Considerations

### 9.1 Optimization Strategies

#### 9.1.1 Pre-Rendering
```
- Pre-render all static sprites at all scales
- Cache rendered results in memory
- Only re-render when terminal capabilities change
```

#### 9.1.2 Dirty Rendering
```
- Track which sprites have changed
- Only re-render changed sprites
- Only redraw changed areas of the screen
```

#### 9.1.3 Sprite Atlases
```
- Combine multiple small sprites into larger atlases
- Reduce number of individual render calls
- Improve cache locality
```

#### 9.1.4 Lazy Loading
```
- Load sprites on demand
- Unload unused sprites after timeout
- Keep frequently used sprites in memory
```

### 9.2 Benchmarks

```
Target performance:
- Sprite rendering: < 1ms per sprite
- Full scene render: < 16ms (60 FPS)
- Memory usage: < 10MB for all sprites
- Startup time: < 500ms
```

### 9.3 Profiling

```rust
pub struct RenderProfiler {
    sprite_render_times: HashMap<String, Vec<u64>>,
    frame_times: Vec<u64>,
    sprite_counts: HashMap<String, usize>,
}

impl RenderProfiler {
    pub fn record_sprite_render(&mut self, sprite_id: &str, time_ns: u64) {
        self.sprite_render_times
            .entry(sprite_id.to_string())
            .or_default()
            .push(time_ns);
    }
    
    pub fn record_frame(&mut self, time_ns: u64) {
        self.frame_times.push(time_ns);
    }
    
    pub fn report(&self) -> String {
        let mut report = String::new();
        
        // Average frame time
        let avg_frame = self.frame_times.iter().sum::<u64>() / self.frame_times.len() as u64;
        report.push_str(&format!("Average frame time: {}ns ({:.2}ms)\n", avg_frame, avg_frame as f64 / 1_000_000.0));
        
        // Slowest sprites
        let mut slowest: Vec<_> = self.sprite_render_times.iter()
            .map(|(id, times)| (id, times.iter().sum::<u64>() / times.len() as u64))
            .collect();
        slowest.sort_by(|a, b| b.1.cmp(&a.1));
        
        report.push_str("\nSlowest sprites:\n");
        for (id, time) in slowest.iter().take(10) {
            report.push_str(&format!("  {}: {}ns\n", id, time));
        }
        
        report
    }
}
```

---

## 10. Configuration

### 10.1 User Configuration

```toml
# ~/.config/tamagotchi/sprites.toml

[rendering]
# Preferred ASCII style
ascii_style = "unicode"  # simple, extended, unicode, braille

# Preferred color mode
color_mode = "truecolor"  # mono, color16, color256, truecolor

# Sprite scaling
scale = "auto"  # auto, s1, s2, s3, s4, s5

# Enable pseudo-16-bit rendering
pseudo_16bit = true

# Braille rendering settings
braille_enabled = true
braille_density = "high"  # low, medium, high

[cache]
# Maximum cached sprites
max_size = 1000

# Cache pre-rendered sprites
pre_render = true

[sprites]
# Custom sprite paths
paths = [
    "~/.config/tamagotchi/sprites",
    "/usr/share/tamagotchi/sprites",
]

# Load embedded sprites
embedded = true

# Sprite quality level
quality = "high"  # low, medium, high
```

### 10.2 MiOS Integration

```
# /usr/share/mios/mios.toml additions

[inputd.bindings.tamagotchi]
# Sprite rendering controls
"ctrl+alt+shift+s" = "tamagotchi:toggle_sprite_style"
"ctrl+alt+shift+c" = "tamagotchi:toggle_color_mode"
"ctrl+alt+shift+r" = "tamagotchi:reload_sprites"
"ctrl+alt+shift+p" = "tamagotchi:toggle_pseudo_16bit"

# Sprite scale controls
"ctrl+alt+shift+1" = "tamagotchi:set_scale_s1"
"ctrl+alt+shift+2" = "tamagotchi:set_scale_s2"
"ctrl+alt+shift+3" = "tamagotchi:set_scale_s3"
"ctrl+alt+shift+4" = "tamagotchi:set_scale_s4"
"ctrl+alt+shift+5" = "tamagotchi:set_scale_s5"
```

### 10.3 Environment Variables

```bash
# Terminal type override
TAMAGOTCHI_TERMINAL=vt100

# Color mode override
TAMAGOTCHI_COLOR=truecolor

# Sprite scale override
TAMAGOTCHI_SCALE=s3

# ASCII style override
TAMAGOTCHI_ASCII=unicode

# Disable pseudo-16-bit
TAMAGOTCHI_PSEUDO_16BIT=0

# Sprite path
TAMAGOTCHI_SPRITES_PATH=/custom/path
```

---

## Appendix A: Sprite Format Examples

### A.1 8x8 Mono Sprite (Mametchi Head)
```
Binary: 00111100 01111110 01000010 01011010 01000010 01111110 00110110 00010100
Hex:    0x3C 0x7E 0x42 0x6A 0x42 0x7E 0x36 0x14

Visual:
  ██████  
 ███████ 
 █      █
 █  ██  █
 █      █
 ███████ 
  ██  ██ 
   █  █  
```

### A.2 8x8 Color Sprite (Kuchipatchi)
```
Palette: [Transparent, White, Black, Red]
Data (2bpp):
Row 0: 00 01 01 01 01 00 00 00
Row 1: 00 11 11 11 11 10 00 00
Row 2: 00 10 00 00 00 10 00 00
Row 3: 00 10 11 00 11 10 00 00
Row 4: 00 10 00 00 00 10 00 00
Row 5: 00 11 11 11 11 10 00 00
Row 6: 00 01 10 11 01 10 00 00
Row 7: 00 00 10 10 00 00 00 00
```

---

## Appendix B: Character Mapping Reference

### B.1 Block Characters (U+2580-U+259F)
```
U+2580: ▀ Lower one eighth block
U+2581: ▁ Lower one seventh block
U+2582: ▂ Lower one sixth block
U+2583: ▃ Lower one fifth block
U+2584: ▄ Lower one quarter block
U+2585: ▅ Lower three eighths block
U+2586: ▆ Lower two thirds block
U+2587: ▇ Lower three quarters block
U+2588: █ Full block
U+2589: ▉ Left seven eighths block
U+258A: ▊ Left three quarters block
U+258B: ▋ Left five eighths block
U+258C: ▌ Left half block
U+258D: ▍ Left three eighths block
U+258E: ▎ Left one quarter block
U+258F: ▏ Left one eighth block
U+2590: ▐ Right one eighth block
... (and more)
```

### B.2 Braille Patterns (U+2800-U+28FF)
```
U+2800: ⠀ No dots
U+2801: ⠁ Dot 1
U+2802: ⠂ Dot 4
U+2803: ⠃ Dots 1,4
U+2804: ⠄ Dot 2
U+2805: ⠅ Dots 1,2
U+2806: ⠆ Dots 2,4
U+2807: ⠇ Dots 1,2,4
U+2808: ⠈ Dot 3
... (all combinations up to U+28FF)

Dot positions:
  1 4
  2 5
  3 6
  7 8
```

---

## Appendix C: Performance Testing

### C.1 Test Scenarios
1. **Static Scene:** 10 sprites, no animation
2. **Animated Scene:** 10 sprites, all animated
3. **Complex Scene:** 50 sprites, mixed static and animated
4. **Headless Mode:** Text-only rendering
5. **Low-End Terminal:** VT100 emulation
6. **High-End Terminal:** True color, full Unicode

### C.2 Expected Results
| Scenario | FPS | Memory | CPU |
|----------|-----|--------|-----|
| Static | 60+ | < 5MB | < 5% |
| Animated | 60+ | < 10MB | < 10% |
| Complex | 30+ | < 20MB | < 20% |
| Headless | 60+ | < 2MB | < 2% |
| VT100 | 60+ | < 3MB | < 5% |
| TrueColor | 60+ | < 15MB | < 15% |

---

## Appendix D: References

### D.1 Tamagotchi Resources
- [Tamagotchi Wiki](https://tamagotchi.fandom.com)
- [Tamagotchi Database](https://www.tamagotchi.com)
- [Tamagotchi Sprites Archive](https://github.com/tamagotchi-sprites)

### D.2 Technical Resources
- [Unicode Block Elements](https://www.unicode.org/charts/PDF/U2580.pdf)
- [Unicode Braille Patterns](https://www.unicode.org/charts/PDF/U2800.pdf)
- [ANSI Escape Codes](https://en.wikipedia.org/wiki/ANSI_escape_code)
- [256-Color Terminal Guide](https://github.com/shiena/ansicolor)

### D.3 Rust Resources
- [ratatui Documentation](https://docs.rs/ratatui)
- [crossterm Documentation](https://docs.rs/crossterm)
- [image crate](https://docs.rs/image)

---

## Document History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2024-11-03 | MiOS Team | Initial version |

---

*End of Document*

### Voxel and compact-view implementation scope (tasks 3713–3715)

The playable implementation adds CPU voxel geometry with depth-tested yaw,
autonomous stage/needs/seeded-RNG wandering and facial reactions, plus selectable
Auto/Full/Minimal terminal HUDs. Controllers navigate menus/HUD only, with
connected/focused native Inputd prompts. Existing pixel-art views and read-only
ASCII headless output remain available. Compact menus scroll; the smallest
interactive layout is 20x8. These concrete features do not certify the broader
future character catalogue, additional economy/social games, physical controller
coverage, smartwatch browser integration or performance targets in this design.
