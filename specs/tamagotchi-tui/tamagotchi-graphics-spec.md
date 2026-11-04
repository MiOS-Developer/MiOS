# Tamagotchi TUI - Graphics & Rendering Specification

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



> The implemented rendering/input foundation is documented in [the native preview guide](../../usr/share/doc/mios/tamagotchi-tui.md). Remaining examples describe future design, not available APIs.

## Overview

This document specifies the **graphics and rendering architecture** for the MiOS Tamagotchi TUI application, targeting:
- **Terminal block graphics** at low resolutions
- **ASCII artwork** at higher resolutions  
- **16-bit style graphics** (256-color palette)
- **Native TTY0/Console/Linux VT0** support
- **Headless MiOS installations**

---

## 1. Graphics Targets & Capabilities

### 1.1 Terminal Capability Matrix

| Mode | Terminal Type | Colors | Character Set | Resolution | Notes |
|------|---------------|--------|----------------|------------|-------|
| **Basic** | VT100, VT220 | 16 | ASCII | 80x24 | Minimal support |
| **Extended** | xterm, Linux VT | 256 | ASCII + Basic Unicode | 80x24+ | Standard target |
| **Modern** | Alacritty, Kitty, WezTerm | 24-bit | Full Unicode | Any | Full capabilities |
| **Console** | Linux TTY0/VT0 | 16/256 | ASCII + Box Drawing | 80x25 | Native VT console |
| **Headless** | SSH, Serial | 16/256 | ASCII/Unicode | 80x24 | No mouse, limited input |

### 1.2 Graphics Quality Tiers

**Tier 0: ASCII-Only (Minimal)**
- Characters: Standard ASCII (0x20-0x7E)
- Colors: 16 ANSI colors
- Resolution: 80x24 minimum
- Use: Fallback for all terminals

**Tier 1: Extended ASCII (Basic)**
- Characters: ASCII + Box Drawing (▀▄█▌▐░▒▓)
- Colors: 16 ANSI colors
- Resolution: 80x25
- Use: VT100/VT220, basic terminals

**Tier 2: 256-Color (Standard)**
- Characters: Full ASCII + Unicode blocks + Braille
- Colors: 256-color palette
- Resolution: 80x24 to 160x90
- Use: xterm, Linux console, most modern terminals

**Tier 3: True Color (Premium)**
- Characters: Full Unicode (emoji, braille, blocks)
- Colors: 24-bit true color
- Resolution: Any size
- Use: Alacritty, Kitty, WezTerm, iTerm2

---

## 2. Character Sets for Graphics

### 2.1 Block Characters (Low Resolution)

**Full Blocks:**
- `░` U+2591 - Light shade (25%)
- `▒` U+2592 - Medium shade (50%)
- `▓` U+2593 - Dark shade (75%)
- `█` U+2588 - Full block (100%)

**Half Blocks:**
- `▀` U+2580 - Upper half
- `▄` U+2584 - Lower half
- `▌` U+258C - Left half
- `▐` U+2590 - Right half

**Quadrant Characters:**
- `▗` U+2597 - Upper left
- `▖` U+2596 - Upper right
- `▝` U+259D - Lower left
- `▘` U+2598 - Lower right
- `▚` U+259A - Diagonal cross
- `▞` U+259E - Reverse diagonal
- `▟` U+259F - Left half black
- `▙` U+259B - Right half black

### 2.2 Braille Patterns (High Resolution)

Braille characters (U+2800 to U+28FF) can represent **2x4 pixel patterns**:

```
Braille Dot Positions:
 1 4
 2 5
 3 6
 7 8

Example patterns:
⠿ = All 8 dots (U+28FF)
⡇ = Dots 1-4 (top row) (U+2847)
⠋ = Dots 1,4,5,8 (corners) (U+280B)
⢿ = Dots 1,2,3,7,8 (left side) (U+28BF)
```

**2x3 Patterns (6 dots):**
- Use dots 1,2,4,5 for 2x2 patterns
- Use dots 1,3,4,6 for diagonal patterns

### 2.3 Box Drawing Characters

For borders and UI elements:
- `─` U+2500 - Horizontal line
- `│` U+2502 - Vertical line
- `┌` U+250C - Top-left corner
- `┐` U+2510 - Top-right corner
- `└` U+2514 - Bottom-left corner
- `┘` U+2518 - Bottom-right corner
- `├` U+251C - Left T-junction
- `┤` U+2524 - Right T-junction
- `┬` U+252C - Top T-junction
- `┴` U+2534 - Bottom T-junction
- `┼` U+253C - Cross

---

## 3. Sprite System

### 3.1 Sprite Definitions

Sprites are defined for each graphics tier:

```rust
#[derive(Clone, Debug)]
pub struct Sprite {
    pub id: String,
    pub tiers: HashMap<GraphicsTier, SpriteVariant>,
}

#[derive(Clone, Debug)]
pub struct SpriteVariant {
    pub width: usize,
    pub height: usize,
    pub frames: Vec<Frame>,
    pub frame_duration: Duration,
    pub color: Option<Color>,
}

#[derive(Clone, Debug)]
pub struct Frame {
    pub data: Vec<String>,      // Lines of characters
    pub colors: Option<Vec<Vec<Color>>>,  // Per-character colors
}
```

### 3.2 Tamagotchi Sprites by Tier

**Tier 0 (ASCII-Only):**
```
Egg:      "O"
Baby:     "(^_^)"
Child:    "(•_•)"
Teen:     "(>_<)"
Adult:    "(≧∇≦)"
Happy:    "^_^"
Sad:      ">_<"
Sick:     "X_X"
Angry:    ">:("
Sleep:    "(-_-) zzz"
Poop:     "@"
Food:     "☺"
Heart:    "<3"
```

**Tier 1 (Extended ASCII):**
```
Egg:      "◉"
Baby:     "☺"
Adult:    "♫"
Happy:    "◡ ◡"
Sad:      "◠ ◠"
Heart:    "♥"
Star:     "★"
Diamond:  "◆"

2x2 Tamagotchi:
 ┌─┐
 │☺│
 └─┘
```

**Tier 2 (256-Color with Braille):**
```
4x4 Braille-based Tamagotchi:
⠐⠠⠐⠐⠠⠐⠐⠠
⠠⡠⠐⠐⡠⠠⠐
⠐⠐⡰⡰⠐⠐⡰⠠
⠠⡰⠐⠐⡰⠠⠠⠐

Each braille char = 2x4 pixels
Total: 8x8 pixel equivalent
```

**Tier 3 (True Color):**
```
16x16 pixel art using block characters:
████████████████
██░░░░░░░░░░██
██░████░░████░██
██░█░█░░█░█░██
██░████░░████░██
██░░░░░░░░░░██
████████████████

With 24-bit colors for each pixel
```

### 3.3 Sprite Registry

```rust
pub struct SpriteRegistry {
    sprites: HashMap<String, Sprite>,
}

impl SpriteRegistry {
    pub fn new() -> Self {
        let mut registry = Self {
            sprites: HashMap::new(),
        };
        
        // Register Tamagotchi species sprites
        registry.register_species_sprites();
        
        // Register UI sprites
        registry.register_ui_sprites();
        
        // Register item sprites
        registry.register_item_sprites();
        
        // Register animation sprites
        registry.register_animation_sprites();
        
        registry
    }
    
    pub fn get(&self, id: &str, tier: GraphicsTier) -> Option<&SpriteVariant> {
        self.sprites.get(id).and_then(|s| s.tiers.get(&tier))
    }
}
```

---

## 4. Color Systems

### 4.1 16-Color ANSI Palette

```rust
pub enum AnsiColor {
    Black = 0,
    Red = 1,
    Green = 2,
    Yellow = 3,
    Blue = 4,
    Magenta = 5,
    Cyan = 6,
    White = 7,
    BrightBlack = 8,
    BrightRed = 9,
    BrightGreen = 10,
    BrightYellow = 11,
    BrightBlue = 12,
    BrightMagenta = 13,
    BrightCyan = 14,
    BrightWhite = 15,
}
```

**Tamagotchi 16-Color Scheme:**
```
Background:        Black (0)
Text:             White (7)
Primary:          Cyan (6)
Secondary:        Magenta (5)
Success:          Green (2)
Warning:          Yellow (3)
Danger:           Red (1)

Tamagotchi:       BrightWhite (15)
Mametchi:         BrightRed (9)
Kuchipatchi:     BrightGreen (10)
Gozarutchi:      BrightBlue (12)

Borders:          Cyan (6)
Progress Bars:   Green (2)
Buttons:          Magenta (5)
```

### 4.2 256-Color Palette (16-bit Style)

**16-bit Style Color Definitions:**
```rust
// 256-color index palette for 16-bit aesthetic
pub const PALETTE_16BIT: [u8; 16] = [
    0,      // Black
    196,    // Red
    46,     // Green
    226,    // Yellow
    21,     // Blue
    201,    // Magenta
    51,     // Cyan
    255,    // White
    238,    // Bright Black
    160,    // Bright Red
    40,     // Bright Green
    220,    // Bright Yellow
    39,     // Bright Blue
    200,    // Bright Magenta
    45,     // Bright Cyan
    254,    // Bright White
];
```

**Tamagotchi Species Colors (256-color):**
```
Mametchi:         224 (Pink)
Kuchipatchi:     120 (Light Green)
Gozarutchi:      153 (Light Blue)
Hoshitchi:       228 (Yellow)
Kuribotchi:      130 (Purple)
Ginjirotchi:     214 (Orange)
Otokitchi:        255 (White)
Ojitchi:         245 (Gray)
```

### 4.3 True Color (24-bit) Palette

**16-bit Style RGB Values:**
```rust
pub const COLOR_16BIT: ColorPalette = ColorPalette {
    // Backgrounds
    bg_dark:      Color::Rgb(10, 10, 20),
    bg_medium:    Color::Rgb(20, 20, 40),
    bg_light:     Color::Rgb(40, 40, 60),
    
    // Tamagotchi colors
    tama_white:   Color::Rgb(240, 240, 255),
    tama_pink:    Color::Rgb(255, 180, 200),  // Mametchi
    tama_green:   Color::Rgb(180, 255, 180),  // Kuchipatchi
    tama_blue:    Color::Rgb(180, 200, 255),  // Gozarutchi
    tama_yellow:  Color::Rgb(255, 255, 180),
    tama_purple:  Color::Rgb(200, 180, 255),
    tama_orange:  Color::Rgb(255, 200, 150),
    tama_red:     Color::Rgb(255, 150, 150),
    
    // UI colors
    ui_primary:   Color::Rgb(100, 150, 255),
    ui_secondary: Color::Rgb(200, 100, 255),
    ui_success:   Color::Rgb(100, 255, 150),
    ui_warning:   Color::Rgb(255, 255, 100),
    ui_danger:    Color::Rgb(255, 100, 100),
    
    // Need colors
    need_hunger:   Color::Rgb(255, 200, 100),
    need_happiness: Color::Rgb(255, 150, 200),
    need_health:    Color::Rgb(100, 255, 100),
    need_discipline: Color::Rgb(100, 200, 255),
    
    // Status colors
    status_happy:   Color::Rgb(255, 255, 100),
    status_sad:     Color::Rgb(100, 150, 255),
    status_sick:    Color::Rgb(255, 100, 100),
    status_angry:   Color::Rgb(255, 100, 50),
    status_sleep:   Color::Rgb(150, 150, 200),
};
```

---

## 5. Rendering Backends

### 5.1 Terminal Capability Detection

The implemented policy lives in `tools/native/mios-tamagotchi/src/capabilities.rs`.
It checks both input and output TTYs, selects color independently of glyphs, and
uses `supports_color::on(Stream::Stdout)` rather than nonexistent Crossterm
color queries. Unknown or Linux-console fonts default to ASCII; explicit glyph
overrides require operator verification. `NO_COLOR` disables colors. Redirected
streams and `TERM=dumb` produce one plain ASCII snapshot without raw mode.

For future enhanced-key handling, the supported query is
`crossterm::terminal::supports_keyboard_enhancement()`, before starting the event
reader. No portable query certifies every glyph or mouse protocol.


### 5.2 Adaptive Renderer

```rust
pub struct Renderer {
    capabilities: TerminalCapabilities,
    tier: GraphicsTier,
    sprite_registry: SpriteRegistry,
    animation_system: AnimationSystem,
    sprite_cache: SpriteCache,
}

impl Renderer {
    pub fn new() -> Self {
        let capabilities = TerminalCapabilities::detect();
        let tier = capabilities.graphics_tier();
        
        Self {
            capabilities,
            tier,
            sprite_registry: SpriteRegistry::new(),
            animation_system: AnimationSystem::new(),
            sprite_cache: SpriteCache::new(),
        }
    }
    
    pub fn render(&mut self, game: &TamagotchiGame) -> Result<(), Box<dyn Error>> {
        self.animation_system.update(Duration::from_millis(16));
        
        match self.tier {
            GraphicsTier::Tier0 => self.render_tier0(game),
            GraphicsTier::Tier1 => self.render_tier1(game),
            GraphicsTier::Tier2 => self.render_tier2(game),
            GraphicsTier::Tier3 => self.render_tier3(game),
        }
    }
}
```

### 5.3 Ratatui Backend (Primary)

For modern terminals with ratatui support:

```rust
pub struct RatatuiRenderer {
    terminal: Terminal<CrosstermBackend<Stdout>>,
    capabilities: TerminalCapabilities,
    sprite_registry: SpriteRegistry,
}

impl RatatuiRenderer {
    pub fn new() -> Result<Self, Box<dyn Error>> {
        let stdout = io::stdout();
        let backend = CrosstermBackend::new(stdout);
        let mut terminal = Terminal::new(backend)?;
        
        crossterm::terminal::enable_raw_mode()?;
        crossterm::execute!(stdout, EnterAlternateScreen, EnableMouseCapture)?;
        
        Ok(Self {
            terminal,
            capabilities: TerminalCapabilities::detect(),
            sprite_registry: SpriteRegistry::new(),
        })
    }
    
    pub fn render(&mut self, game: &TamagotchiGame) -> Result<(), Box<dyn Error>> {
        self.terminal.draw(|f| {
            match self.capabilities.graphics_tier() {
                GraphicsTier::Tier0 => self.render_ascii(f, game),
                GraphicsTier::Tier1 => self.render_extended(f, game),
                GraphicsTier::Tier2 => self.render_256color(f, game),
                GraphicsTier::Tier3 => self.render_truecolor(f, game),
            }
        })?;
        
        Ok(())
    }
}
```

### 5.4 Console Direct Backend

For Linux console (TTY0/VT0) with direct rendering:

```rust
pub struct ConsoleRenderer {
    capabilities: TerminalCapabilities,
    sprite_registry: SpriteRegistry,
}

impl ConsoleRenderer {
    pub fn new() -> Self {
        Self {
            capabilities: TerminalCapabilities::detect(),
            sprite_registry: SpriteRegistry::new(),
        }
    }
    
    pub fn render(&mut self, game: &TamagotchiGame) -> Result<(), Box<dyn Error>> {
        crossterm::execute!(io::stdout(), Clear(ClearType::All))?;
        crossterm::execute!(io::stdout(), MoveTo(0, 0))?;
        
        let output = match self.capabilities.graphics_tier() {
            GraphicsTier::Tier0 => self.create_ascii_output(game),
            GraphicsTier::Tier1 => self.create_extended_output(game),
            GraphicsTier::Tier2 => self.create_256color_output(game),
            GraphicsTier::Tier3 => self.create_truecolor_output(game),
        };
        
        crossterm::execute!(io::stdout(), Print(output))?;
        io::stdout().flush()?;
        
        Ok(())
    }
}
```

### 5.5 Headless Text Backend

For headless mode (SSH, CI, etc.):

```rust
pub struct HeadlessRenderer;

impl HeadlessRenderer {
    pub fn render(&self, game: &TamagotchiGame) -> String {
        let mut output = String::new();
        
        output.push_str(&format!("=== Tamagotchi: {} ===\n", game.tamagotchi.name));
        output.push_str(&format!("Age: {} days, {} hours\n",
            game.tamagotchi.age.num_days(),
            game.tamagotchi.age.num_hours() % 24));
        output.push_str(&format!("Stage: {:?}\n", game.tamagotchi.stage));
        output.push_str(&format!("Mood: {:?}\n", game.tamagotchi.mood));
        output.push_str("\n");
        
        output.push_str("Needs:\n");
        output.push_str(&format!("  Hunger: {:.0}%\n", game.tamagotchi.hunger * 100.0));
        output.push_str(&format!("  Happiness: {:.0}%\n", game.tamagotchi.happiness * 100.0));
        output.push_str(&format!("  Health: {:.0}%\n", game.tamagotchi.health * 100.0));
        output.push_str(&format!("  Discipline: {:.0}%\n", game.tamagotchi.discipline * 100.0));
        output.push_str("\n");
        
        output.push_str("Actions: [F] Feed, [P] Play, [C] Clean, [D] Discipline, [S] Save, [Q] Quit\n");
        
        output
    }
}
```

---

## 6. Layout Examples

### 6.1 Low Resolution (80x24) - Tier 0/1

```
┌─────────────────────────────────────────────────────────────┐
│  TAMAGOTCHI v1.0.0                    [PAUSED]    [2025-01-XX] │
├─────────────────────────────────────────────────────────────┤
│                                                                  │
│   ┌─────────────┐    ┌─────────────────────────────────┐   │
│   │             │    │  Name: Mametchi                  │   │
│   │    (^_^)    │    │  Age: 3 days                    │   │
│   │             │    │  Stage: Adult                   │   │
│   │   ██████    │    │  Hunger: ████░░░░ 40%          │   │
│   │   █    █    │    │  Happiness: ████████ 80%        │   │
│   │   ██████    │    │  Health: ██████░░ 60%          │   │
│   └─────────────┘    │  Poop: @                         │   │
│                      │  Gotchi Points: 1500             │   │
│   [F] Feed    [P] Play    [C] Clean    [M] Menu            │   │
│                                                                  │
└─────────────────────────────────────────────────────────────┘
```

**Character Palette:**
- Empty: ` ` (space)
- Light: `░` (U+2591)
- Medium: `▒` (U+2592)
- Dark: `▓` (U+2593)
- Full: `█` (U+2588)

### 6.2 Medium Resolution (120x40) - Tier 2

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           TAMAGOTCHI v1.0.0                              │
│                         [PAUSED]    [2025-01-XX 14:30]                   │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│   ┌───────────────────────┐    ┌─────────────────────────────────────┐ │
│   │                       │    │                                      │ │
│   │    .--""""""-.       │    │   Name: Mametchi                     │ │
│   │   /   .--.    \      │    │   Species: Original                  │ │
│   │  |   |  o\    |     │    │   Age: 3 days, 5 hours              │ │
│   │   \   \   __  /      │    │   Stage: Adult                      │ │
│   │    \  .-"  /       │    │   Gender: Male                       │ │
│   │     '.___.'        │    │                                      │ │
│   │   /       \        │    │   ┌─────────────────────────────┐   │ │
│   │  /         \       │    │   │ Needs:                          │   │ │
│   │ |           |       │    │   │ Hunger:  ████████░░░░░░ 40%    │   │ │
│   │  \         /        │    │   │ Happiness: ████████████░░ 80%  │   │ │
│   │   \_______/         │    │   │ Health: █████████░░░░ 60%      │   │ │
│   │                       │    │   │ Discipline: ██████░░░░░░ 30% │   │ │
│   └───────────────────────┘    │   └─────────────────────────────┘   │ │
│                                    │                                      │ │
│   [1] Feed    [2] Play    [3] Clean    [4] Discipline    [5] Status   │ │
│   [6] Shop    [7] Games   [8] Connect   [9] Save    [0] Menu        │ │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 6.3 High Resolution (160x60) - Tier 3

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                                                     TAMAGOTCHI v1.0.0            │
│                                                  [PAUSED] [2025-01-XX 14:30:45] │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                   │
│   ┌─────────────────────────────┐    ┌─────────────────────────────────────────┐ │
│   │                             │    │                                          │ │
│   │      .--""""""-.           │    │   ┌─────────────────────────────────┐   │ │
│   │     /   .--.    \          │    │   │  Mametchi                          │   │ │
│   │    |   |  o\    |         │    │   │  ┌─────────────────────────────┐ │   │ │
│   │     \   \   __  /          │    │   │  │  Level: 5                       │ │   │ │
│   │      \  .-"  /           │    │   │  │  Age: 3 days, 5h, 30m          │ │   │ │
│   │       '.___.'            │    │   │  │  Generation: 1                 │ │   │ │
│   │      /       \            │    │   │  └─────────────────────────────┘ │   │ │
│   │     /         \           │    │   │                                     │   │ │
│   │    |           |           │    │   │  ┌─────────────────────────────┐ │   │ │
│   │     \         /            │    │   │  │  Needs                         │ │   │ │
│   │      \_______/             │    │   │  │  Hunger:    ████████░░░░ 40%  │ │   │ │
│   │                             │    │   │  │  Happiness: ████████████░░ 80% │ │   │ │
│   │   [HAPPY]  [HEALTHY]       │    │   │  │  Health:    █████████░░░░ 60% │ │   │ │
│   │                             │    │   │  │  Discipline:██████░░░░░░ 30% │ │   │ │
│   └─────────────────────────┘    │    │  └─────────────────────────────┘ │   │ │
│                                    │    │                                     │   │ │
│   ┌─────────────────────────────┐ │    │  ┌─────────────────────────────┐ │   │ │
│   │  Stats                      │ │    │  │  Quick Actions               │ │   │ │
│   │  Gotchi Points: 1500        │ │    │  │  [F] Feed                    │ │   │ │
│   │  Weight: Normal             │ │    │  │  [P] Play Game               │ │   │ │
│   │  Care Mistakes: 2           │ │    │  │  [C] Clean                   │ │   │ │
│   │  Friendship: Best Friends   │ │    │  │  [D] Discipline              │ │   │ │
│   └─────────────────────────────┘ │    │  │  [M] Menu                    │ │   │ │
│                                    │    │  └─────────────────────────────┘ │   │ │
│   [1] Status  [2] Shop  [3] Games  [4] Connect  [5] Save  [6] Quit         │
│                                                                                   │
└─────────────────────────────────────────────────────────────────────────────────┘
```

With **24-bit colors** for each element:
- Tamagotchi: Pink (Mametchi)
- Background: Dark blue
- Text: White
- Borders: Cyan
- Progress bars: Green (health), Orange (hunger), Pink (happiness), Blue (discipline)

---

## 7. Console & TTY0/VT0 Support

### 7.1 Linux Console Characteristics

- **Resolution:** 80x25 (default), configurable via `fbset` or `stty`
- **Colors:** 16 ANSI colors (VGA text mode)
- **Character Set:** VGA font (256 glyphs including box drawing)
- **Unicode:** Limited (depends on loaded font)
- **Mouse:** No native support (requires gpm)
- **Input:** Raw keyboard input

### 7.2 Console Detection

```rust
pub fn is_linux_console() -> bool {
    let term = std::env::var("TERM").unwrap_or_default();
    term == "linux" || term.contains("console")
}

pub fn is_tty() -> bool {
    std::io::stdin().is_terminal()
}
```

### 7.3 Console-Optimized Rendering

```rust
impl Renderer {
    pub fn render_for_console(&mut self, game: &TamagotchiGame) -> Result<(), Box<dyn Error>> {
        // Use VGA-compatible characters only
        // Avoid Unicode that might not be in the console font
        // Use 16 colors only
        
        let output = self.create_console_output(game);
        
        crossterm::execute!(
            io::stdout(),
            Clear(ClearType::All),
            MoveTo(0, 0),
            Print(output),
        )?;
        
        Ok(())
    }
    
    fn create_console_output(&self, game: &TamagotchiGame) -> String {
        // Use only VGA-compatible characters and 16 colors
        // ...
    }
}
```

---

## 8. Headless Mode Support

### 8.1 Headless Detection

```rust
pub fn is_headless() -> bool {
    let is_tty = std::io::stdin().is_terminal();
    let term = std::env::var("TERM").unwrap_or_default();
    
    !is_tty || 
    term == "dumb" || 
    term.is_empty() ||
    std::env::var("HEADLESS").is_ok() ||
    std::env::var("CI").is_ok() ||
    std::env::var("GITHUB_ACTIONS").is_ok()
}
```

### 8.2 Headless Rendering

```rust
impl HeadlessRenderer {
    pub fn render(&self, game: &TamagotchiGame) -> String {
        // Pure text output, no graphics
        self.format_text_output(game)
    }
    
    fn format_text_output(&self, game: &TamagotchiGame) -> String {
        let mut output = String::new();
        
        output.push_str("=== Tamagotchi ===\n\n");
        output.push_str(&format!("Name: {}\n", game.tamagotchi.name));
        output.push_str(&format!("Species: {:?}\n", game.tamagotchi.species));
        output.push_str(&format!("Age: {} days, {} hours\n",
            game.tamagotchi.age.num_days(),
            game.tamagotchi.age.num_hours() % 24));
        output.push_str(&format!("Stage: {:?}\n", game.tamagotchi.stage));
        output.push_str(&format!("Mood: {:?}\n\n", game.tamagotchi.mood));
        
        output.push_str("Needs:\n");
        output.push_str(&format!("  Hunger: {:.0}%\n", game.tamagotchi.hunger * 100.0));
        output.push_str(&format!("  Happiness: {:.0}%\n", game.tamagotchi.happiness * 100.0));
        output.push_str(&format!("  Health: {:.0}%\n", game.tamagotchi.health * 100.0));
        output.push_str(&format!("  Discipline: {:.0}%\n\n", game.tamagotchi.discipline * 100.0));
        
        output.push_str("Stats:\n");
        output.push_str(&format!("  Gotchi Points: {}\n", game.tamagotchi.gotchi_points));
        output.push_str(&format!("  Care Mistakes: {}\n", game.tamagotchi.care_mistakes));
        output.push_str(&format!("  Generation: {}\n\n", game.tamagotchi.generation));
        
        output.push_str("Actions:\n");
        output.push_str("  [F] Feed\n");
        output.push_str("  [P] Play Game\n");
        output.push_str("  [C] Clean\n");
        output.push_str("  [D] Discipline\n");
        output.push_str("  [M] Menu\n");
        output.push_str("  [S] Save\n");
        output.push_str("  [Q] Quit\n");
        
        output
    }
}
```

---

## 9. Performance Optimization

### 9.1 Frame Buffering

```rust
pub struct FrameBuffer {
    width: u16,
    height: u16,
    cells: Vec<Vec<Cell>>,
    dirty: bool,
}

#[derive(Clone, Debug)]
pub struct Cell {
    pub character: char,
    pub foreground: Color,
    pub background: Color,
}

impl FrameBuffer {
    pub fn new(width: u16, height: u16) -> Self {
        Self {
            width,
            height,
            cells: vec![vec![Cell::default(); width as usize]; height as usize],
            dirty: true,
        }
    }
    
    pub fn set(&mut self, x: u16, y: u16, character: char, fg: Color, bg: Color) {
        if x < self.width && y < self.height {
            self.cells[y as usize][x as usize] = Cell { character, foreground: fg, background: bg };
            self.dirty = true;
        }
    }
    
    pub fn clear(&mut self) {
        for row in &mut self.cells {
            for cell in row {
                *cell = Cell::default();
            }
        }
        self.dirty = true;
    }
}
```

### 9.2 Sprite Caching

```rust
pub struct SpriteCache {
    cache: HashMap<String, CachedSprite>,
}

#[derive(Clone)]
pub struct CachedSprite {
    pub lines: Vec<String>,
    pub colors: Vec<Vec<Color>>,
}

impl SpriteCache {
    pub fn new() -> Self {
        Self { cache: HashMap::new() }
    }
    
    pub fn get_or_create(
        &mut self,
        sprite: &Sprite,
        tier: GraphicsTier,
        color: Option<Color>
    ) -> &CachedSprite {
        let key = format!("{}:{:?}:{:?}", sprite.id, tier, color);
        
        if !self.cache.contains_key(&key) {
            let cached = self.render_sprite(sprite, tier, color);
            self.cache.insert(key, cached);
        }
        
        self.cache.get(&key).unwrap()
    }
    
    fn render_sprite(&self, sprite: &Sprite, tier: GraphicsTier, color: Option<Color>) -> CachedSprite {
        let variant = sprite.tiers.get(&tier).unwrap();
        let frame = &variant.frames[0];
        
        let mut lines = Vec::new();
        let mut colors = Vec::new();
        
        for line in &frame.data {
            lines.push(line.clone());
            
            if let Some(ref frame_colors) = frame.colors {
                colors.push(frame_colors.clone());
            } else if let Some(c) = color {
                colors.push(vec![c; line.len()]);
            } else {
                colors.push(vec![Color::Reset; line.len()]);
            }
        }
        
        CachedSprite { lines, colors }
    }
}
```

### 9.3 Dirty Rectangle Tracking

```rust
pub struct DirtyTracker {
    rects: Vec<Rect>,
}

#[derive(Clone, Copy, Debug)]
pub struct Rect {
    pub x: u16,
    pub y: u16,
    pub width: u16,
    pub height: u16,
}

impl DirtyTracker {
    pub fn new() -> Self {
        Self { rects: Vec::new() }
    }
    
    pub fn mark(&mut self, rect: Rect) {
        self.rects.push(rect);
    }
    
    pub fn get_and_clear(&mut self) -> Vec<Rect> {
        let rects = self.rects.clone();
        self.rects.clear();
        rects
    }
    
    pub fn merge(&self) -> Vec<Rect> {
        // Merge overlapping rectangles
        // Implementation omitted for brevity
        self.rects.clone()
    }
}
```

---

## 10. Configuration

### 10.1 User Configuration

```toml
# ~/.config/tamagotchi/config.toml
[graphics]
# Force a specific tier (auto, 0, 1, 2, 3)
force_tier = "auto"

# Enable/disable animations
animations_enabled = true
animation_speed = "normal"  # slow, normal, fast

# Sprite style
sprite_style = "classic"  # classic, modern, minimal, ascii

# Color scheme
color_scheme = "16bit"  # 16bit, 256, truecolor, monochrome

# Console-specific
console_optimized = true
use_box_drawing = true
use_braille = true

# Performance
max_fps = 60
vsync = true
frame_buffering = true
sprite_caching = true

# Headless
headless_text_only = true
```

### 10.2 MiOS SSOT Integration

```toml
# /usr/share/mios/mios.toml
[tamagotchi.graphics]
# Default graphics settings
default_tier = "auto"
min_tier = 0
max_tier = 3

# Color schemes
color_schemes = ["16bit", "256", "truecolor", "monochrome"]

# Console support
console_optimized = true
console_use_gpm = true
console_font = "vga"

# Headless support
headless_enabled = true
headless_text_only = true
headless_no_colors = false
```

---

## 11. Summary

This graphics specification provides:

1. **Multi-tier rendering** for different terminal capabilities
2. **ASCII artwork** at various resolutions
3. **Terminal block graphics** using Unicode box drawing and block characters
4. **16-bit style graphics** with 256-color palette
5. **Native Linux console (TTY0/VT0) support**
6. **Headless mode** for SSH and CI environments
7. **Adaptive rendering** that automatically detects terminal capabilities
8. **Sprite system** with tier-specific definitions
9. **Animation system** for smooth transitions
10. **Performance optimizations** (frame buffering, sprite caching, dirty tracking)

The Tamagotchi TUI will provide a **visually appealing experience** across all terminal types, from basic VT100 emulators to modern true-color terminals, while maintaining full functionality in headless environments.

---

*This document is part of the Tamagotchi TUI Comprehensive Plan.*

### Voxel and compact-view implementation scope (tasks 3713–3715)

The playable implementation adds CPU voxel geometry with depth-tested yaw,
autonomous stage/needs/seeded-RNG wandering and facial reactions, plus selectable
Auto/Full/Minimal terminal HUDs. Controllers navigate menus/HUD only, with
connected/focused native Inputd prompts. Existing pixel-art views and read-only
ASCII headless output remain available. Compact menus scroll; the smallest
interactive layout is 20x8. These concrete features do not certify the broader
future character catalogue, additional economy/social games, physical controller
coverage, smartwatch browser integration or performance targets in this design.

### Original animal catalogue and dialogue transport (tasks 3716–3717)

The implementation now includes sixteen original Japanese-inspired animal
MiOS-Mons, stable schema-2 identity with read-only schema-1 migration, eight
personality archetypes, individual hobbies, state-aware voices and thoughts.
Native tmux popups and right-click context menus target only a verified owning
client; inline below-view dialogue and compact paging remain available in
restricted/remote transports. These characters and dialogue are original; the
research note distinguishes primary Nintendo design statements, community
documentation and our implementation choices. Physical mobile-browser and
controller acceptance remains a per-session runtime receipt.
