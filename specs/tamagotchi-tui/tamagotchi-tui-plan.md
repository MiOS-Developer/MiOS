# MiOS Rust-Native Tamagotchi TUI App - Comprehensive Plan

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



> A playable local care/memory/save sample now exists as `mios-tamagotchi`. See [the native preview guide](../../usr/share/doc/mios/tamagotchi-tui.md). The complete device feature set and live deployment validation remain tracked work; this plan is not a completion claim.

## Executive Summary

This document outlines the complete plan to create a Rust-native Terminal User Interface (TUI) application for Linux that replicates and extends all functionalities of the best Japanese Tamagotchi versions, with full integration into MiOS's Inputd platform for controls and keybindings.

**Target:** A fully functional virtual pet that captures the essence of Tamagotchi Plus Color, Tamagotchi Connection, Tamagotchi On, Tamagotchi Smart, and Tamagotchi Uni, while leveraging MiOS's sophisticated input routing, session management, and configuration system.

---

## 1. Tamagotchi Feature Research & Analysis

### 1.1 Best Tamagotchi Versions Analyzed

**Tier 1: Foundation (1996-1998)**
- Original Tamagotchi (1996) - Basic care: feed, discipline, play games
- Tamagotchi Angel (1997) - Angel theme, comes back after death
- Mesutchi & Osutchi (1997) - First marriage/connection system (physical ports)
- Tamagotchi Ocean (1998) - Underwater theme

**Tier 2: Modern Revival (2004-2008)**
- Tamagotchi Connection / Plus (2004) - IR connectivity, friendship system, marriage, Gotchi Points
- Tamagotchi Plus Color (2008) - First color screen, locations (Tamagotchi Planet), Dating Place, GuruGuru Park, Game Center, TamaDepa Market

**Tier 3: Advanced Features (2013-2024)**
- Tamagotchi P's (2013) - Full-color, shops, accessories, wallpaper, garden
- Tamagotchi On / Meets (2018-2019) - Marriage, can stay forever, customization (4 accessories), online connectivity, DLC
- Tamagotchi Smart (2022) - Touch screen, small device, camera functionality (Pix variant)
- Tamagotchi Uni (2024) - Marriage, camera, online connectivity, group events
- Tamagotchi Pix / Pix Party (2021-2023) - Camera, photo capture, social connectivity, 17 mini-games

### 1.2 Core Feature Categories

**A. Life Cycle System**
- Egg -> Baby -> Child -> Teen -> Adult -> Senior/Death
- 2-3 days per stage, 7-10 days total lifespan

**B. Care Mechanics**
- Hunger System: Meals 3x daily (breakfast, lunch, dinner), Snacks
- Happiness System: Play games, Discipline for bad behavior
- Health System: Poop cleaning, Bathing, Illness from neglect
- Discipline System: Scold/Praise affects personality

**C. Personality & Characters**
- Personality Types: Perfect care -> Mametchi, Good care -> various, Poor care -> bad characters
- Plus Color: 40+ characters across all growth stages
- On/Meets/Uni: 50+ characters with DLC

**D. Mini-Games**
- Original: Character game, Number game
- Connection/Plus: Pop the Balloon, Speed Eating
- Plus Color: Game Center with multiple games (100G reward each)
- Pix: 17 mini-games

**E. Social Features**
- Connectivity: IR (Infrared) in Connection/Plus, NFC, Bluetooth in later models
- Friendship System: 6 levels (Acquaintance -> Buddy -> Friend -> Good Friend -> Best Friend -> Partner)
- Marriage: Introduced in Mesutchi/Osutchi (1997), available in all modern versions
  - Requirements: Both adults, opposite gender, high friendship
  - Matchmaker appears at 10:30 AM, 3:00 PM, 7:00 PM
  - Female produces 2 babies, keeps 1, sends 1 to male

**F. Locations & Exploration**
- Plus Color - Tamagotchi Planet: Game Center, TamaDepa, TamaTama Market, Dating Place, GuruGuru Park, Parents' House

**G. Economy System**
- Gotchi Points (G): Earned from mini-games, spent on items
- Shop System: Buy food, snacks, toys, accessories, transit passes

**H. Customization**
- On/Meets/Uni: 4 accessory slots, wallpaper, home decorations
- Plus Color: Garden, tree growing
- Pix: Camera, photo capture

---

## 2. Target Feature Set

### 2.1 Must-Have Features (P0 - Core Experience)

| Feature | Source Version | Notes |
|---------|----------------|-------|
| Life Cycle (Egg->Baby->Child->Teen->Adult) | All | Core mechanic |
| Hunger System (Meals 3x/day) | All | Timed meals |
| Happiness System (Games, Attention) | All | Happy hearts |
| Health System (Poop, Bath) | Most | Cleaning required |
| Discipline System | All | Scold/Praise |
| Mini-Games (3+) | All | Earn points |
| Character System (20+) | All | Multiple personalities |
| Death & Reset | All | Game over state |
| Pause Function | Connection | Essential for TUI |

### 2.2 Should-Have Features (P1 - Enhanced Experience)

| Feature | Source Version | Notes |
|---------|----------------|-------|
| Gotchi Points Economy | Connection+ | Currency system |
| Shop System | Connection+ | Buy items |
| IR/Connection Simulation | Connection+ | Local multi-device |
| Friendship System | Connection+ | 6 levels |
| Marriage System | Mesutchi+ | Core social feature |
| Matchmaker (Otokitchi) | Plus Color | Automated marriage |
| Multiple Locations | Plus Color | Tamagotchi Planet |
| Transit Passes | Plus Color | Unlock areas |
| Accessories & Customization | P's/On | 4+ slots |
| Wallpaper/Decorations | P's/On | Home customization |

### 2.3 Nice-to-Have Features (P2 - Premium Experience)

| Feature | Source Version | Notes |
|---------|----------------|-------|
| Color TUI (256 colors) | Plus Color | Enhanced visuals |
| Camera Simulation | Pix | Screenshot feature |
| Online Connectivity | Smart/Uni | Multiplayer |
| DLC System | On/Meets | Downloadable content |
| Touch Screen Support | Smart | Alternative input |
| Seasonal Events | Uni | Holidays, special days |
| Achievements | Plaza | Completion tracking |

### 2.4 MiOS-Specific Features

| Feature | Priority | Notes |
|---------|----------|-------|
| Inputd Integration | P0 | Native controller support |
| MiOS Keybindings | P0 | Consistent with MiOS UX |
| Tmux Integration | P0 | Works in MiOS terminal |
| Session Management | P0 | Save/load across MiOS sessions |
| Configuration via mios.toml | P0 | SSOT integration |
| MCP Tools | P1 | Agent integration |
| Native Rust | P0 | Performance, safety |

---

## 3. Technical Architecture

### 3.1 Technology Stack

- **Language:** Rust 2024 Edition
- **TUI Framework:** ratatui 0.28+ (formerly tui-rs)
- **Terminal:** crossterm 0.28+
- **Async:** tokio 1.0+
- **Serialization:** serde + toml/rmp-serde
- **State Management:** Custom
- **Time:** chrono + tokio cron scheduler
- **Logging:** tracing + tracing-subscriber
- **Configuration:** mios-resolver (MiOS SSOT)
- **Input:** mios-inputd client library

### 3.2 Crate Structure

```
tamagotchi-tui/
├── Cargo.toml                    # Workspace root
├── tamagotchi-core/              # Core game logic (no UI)
│   ├── Cargo.toml
│   └── src/
│       ├── lib.rs
│       ├── tamagotchi/
│       │   ├── mod.rs
│       │   ├── entity.rs       # Tamagotchi entity
│       │   ├── lifecycle.rs    # Life stages
│       │   ├── needs.rs        # Hunger, happiness, health
│       │   ├── personality.rs
│       │   ├── genetics.rs     # For marriage system
│       │   └── species.rs      # Character types
│       ├── world/
│       │   ├── mod.rs
│       │   ├── time.rs         # Game time system
│       │   ├── location.rs     # Tamagotchi Planet
│       │   ├── shop.rs         # Economy
│       │   └── inventory.rs    # Items
│       ├── games/
│       │   ├── mod.rs
│       │   ├── registry.rs     # Game registry
│       │   ├── pop_balloon.rs
│       │   ├── speed_eating.rs
│       │   └── memory.rs
│       ├── social/
│       │   ├── mod.rs
│       │   ├── friendship.rs
│       │   ├── marriage.rs
│       │   └── connection.rs   # Device-to-device
│       └── save/
│           ├── mod.rs
│           ├── serializer.rs
│           └── version.rs
├── tamagotchi-tui/               # TUI application
│   ├── Cargo.toml
│   └── src/
│       ├── main.rs
│       ├── app.rs               # Main app state
│       ├── ui/
│       │   ├── mod.rs
│       │   ├── components/
│       │   │   ├── tamagotchi.rs
│       │   │   ├── status.rs
│       │   │   ├── menu.rs
│       │   │   └── game.rs
│       │   ├── layouts/
│       │   │   ├── main.rs
│       │   │   └── care.rs
│       │   └── theme.rs
│       ├── input/
│       │   ├── mod.rs
│       │   ├── handler.rs
│       │   ├── bindings.rs
│       │   └── mios.rs         # Inputd integration
│       └── config/
│           ├── mod.rs
│           └── mios.rs         # MiOS SSOT integration
└── tamagotchi-inputd/             # Inputd plugin/bridge
    ├── Cargo.toml
    └── src/
        └── lib.rs
```

### 3.3 Core Architecture Components

#### Game Loop (tokio-based)
- Input handling: Immediate
- Game tick: 1Hz (configurable)
- Render tick: 60Hz or as needed
- Uses tokio::select! for concurrent event handling

#### Entity System
- Tamagotchi struct with all state
- Life stages with durations
- Needs (hunger, happiness, health, discipline)
- Genetics for marriage system
- Social relationships

#### Time System
- Real-time vs game-time
- Pause functionality
- Time scaling (0.5x, 1x, 2x, etc.)
- Meal time tracking (8AM, 1PM, 6PM)

#### Need System
- Each need: value (0.0-1.0), decay rate, thresholds
- Automatic decay over time
- Manual filling through actions
- Critical/warning states

#### Life Stage System
- Enum with all stages
- Duration per stage
- Transition logic
- Marriage eligibility

---

## 4. MiOS Inputd Integration

### 4.1 Inputd Architecture Overview

From MiOS codebase analysis, Inputd provides:
- Global input policy validation
- Controller routing (evdev/uinput bridge)
- Session management
- Navigation profiles
- Room input ownership
- Console VT bridge
- Virtual pad (vpad) for agents

Key components:
- config.rs - GlobalConfig, Binding, ControllerConfig
- router.rs - Event routing, PadState, Output
- nav.rs - Navigation profiles
- sessions.rs - Session routing
- linux.rs - Linux-specific evdev handling
- vpad.rs - Virtual gamepad

### 4.2 Integration Strategy

**Tier 1: Basic Keybinding Support**
- Register as MiOS application with standard keybindings
- Use [keybindings.tmux] for terminal-based controls
- Support MiOS global chords (Ctrl+Alt+Shift + key)

**Tier 2: Controller Support**
- Register as game context in Inputd
- Map gamepad buttons to Tamagotchi actions
- Support navigation profiles for different screens

**Tier 3: Full Inputd Client**
- Implement as native Inputd client
- Use virtual pad for controller input
- Participate in room input ownership

### 4.3 Implementation

#### Current terminal integration

The native preview consumes ordinary Crossterm key events through the existing
Inputd terminal navigation projection. The current SSOT already maps
`BTN_DPAD_*` to arrow keys, `BTN_SOUTH` to Enter, `BTN_EAST` to Escape, and
`BTN_WEST` to Tab/Shift+Tab. The TUI handles these keys in `input::action` and
ignores release events; repeats navigate without toggling pause repeatedly.
Existing `controller.sessions` routing targets an exact tmux pane when needed.

No new global Tamagotchi context or nested `room.tamagotchi` table is registered.
Inputd navigation profiles use kernel button identifiers and namespaced actions;
the room table accepts action strings or tap/hold arrays, not per-app tables.
The existing `vpad` is an injectable virtual controller, not a physical-input
subscription service. A future native event subscriber requires a separately
specified and implemented protocol; `connect_to_inputd()` is not an available API.

#### Controller Mapping

```rust
#[derive(Clone, Debug)]
pub struct TamagotchiControllerMap {
    pub up: TamagotchiAction,
    pub down: TamagotchiAction,
    pub left: TamagotchiAction,
    pub right: TamagotchiAction,
    pub north: TamagotchiAction,  // Y/△
    pub south: TamagotchiAction,  // A/×
    pub east: TamagotchiAction,   // B/○
    pub west: TamagotchiAction,   // X/□
    pub left_trigger: TamagotchiAction,
    pub right_trigger: TamagotchiAction,
    pub start: TamagotchiAction,
    pub select: TamagotchiAction,
    pub guide: TamagotchiAction,
}

#[derive(Clone, Debug, PartialEq)]
pub enum TamagotchiAction {
    Feed,
    PlayGame,
    Discipline,
    Clean,
    Status,
    Menu,
    Pause,
    NextTama,
    PrevTama,
    Accept,
    Cancel,
    Jump,
    Left,
    Right,
    Up,
    Down,
}

// Default mapping
impl Default for TamagotchiControllerMap {
    fn default() -> Self {
        Self {
            up: TamagotchiAction::Status,
            down: TamagotchiAction::Feed,
            left: TamagotchiAction::PrevTama,
            right: TamagotchiAction::NextTama,
            north: TamagotchiAction::PlayGame,
            south: TamagotchiAction::Accept,
            east: TamagotchiAction::Discipline,
            west: TamagotchiAction::Clean,
            left_trigger: TamagotchiAction::Pause,
            right_trigger: TamagotchiAction::Menu,
            start: TamagotchiAction::Pause,
            select: TamagotchiAction::Status,
            guide: TamagotchiAction::Menu,
        }
    }
}
```

#### Input Context System

```rust
#[derive(Clone, Debug, PartialEq)]
pub enum InputContext {
    Care,           // Main care screen
    Menu,           // Menu navigation
    MiniGame(String), // Mini-game playing
    Shop,           // Shop browsing
    Inventory,      // Inventory management
    Location(String), // Location exploration
    Connection,     // Multi-device connection
    Marriage,       // Marriage ceremony
    Paused,         // Paused state
}

impl InputContext {
    pub fn handle_input(&self, input: &InputEvent) -> Vec<Action> {
        match self {
            InputContext::Care => self.handle_care_input(input),
            InputContext::Menu => self.handle_menu_input(input),
            InputContext::MiniGame(game) => self.handle_game_input(game, input),
            // ...
        }
    }
}
```

---

## 5. Keybinding Design

### 5.1 Keyboard Keybindings

#### Global MiOS Bindings
- Open Tamagotchi: Ctrl+Alt+Shift+T
- Tamagotchi Menu: Ctrl+B, then T (from MiOS menu)

#### Tamagotchi-Specific Bindings

**Navigation:**
- h/j/k/l - Move cursor (vi keys)
- Arrow Keys - Move cursor
- Tab - Next element
- Shift+Tab - Previous element
- Enter - Select/Confirm
- Esc - Cancel/Back

**Care Actions:**
- f - Feed (meal or snack)
- p - Play game
- d - Discipline
- c - Clean (poop)
- b - Bathe
- m - Check status/Mood

**Game Actions:**
- g - Open games menu
- s - Open shop
- l - Open locations menu
- i - Open inventory
- n - Next Tamagotchi (multi-device)

**System Actions:**
- Space - Pause/Resume
- + - Speed up time
- - - Slow down time
- 0 - Reset time scale
- Ctrl+S - Save game
- Ctrl+L - Load game
- Ctrl+Q - Quit (with confirmation)

**Multi-Device:**
- Ctrl+C - Connect to another device
- Ctrl+M - Marriage (when adult)
- Ctrl+G - Gift exchange

**Mini-Game Controls:**
- WASD - Movement
- Space - Action/Jump
- Shift - Run/Sprint

### 5.2 Controller Keybindings

**Gamepad Layout:**
- D-Pad: Navigate menus
- Left Stick: Navigate (alternative)
- Right Stick: Not used (or camera in 3D mode)
- Y/△ (North): Play Game / Accept
- A/× (South): Feed / Select
- B/○ (East): Discipline
- X/□ (West): Clean / Cancel
- Start: Status
- Select: Inventory
- L Trigger: Pause
- R Trigger: Menu
- Guide: Menu

**Controller Mapping in mios.toml:**

```toml
[keybindings.global.controller.room.tamagotchi]
north = ["tamagotchi:play_game", "accept"]
south = ["tamagotchi:feed", "select"]
east = ["tamagotchi:discipline", "confirm"]
west = ["tamagotchi:clean", "cancel"]
start = ["tamagotchi:status"]
select = ["tamagotchi:inventory"]
left_trigger = ["tamagotchi:pause"]
right_trigger = ["tamagotchi:menu"]
left_stick = ["navigate"]
dpad = ["navigate"]

[keybindings.global.controller.nav.tamagotchi]
up = "previous_item"
down = "next_item"
left = "previous_page"
right = "next_page"
```

---

## 6. Implementation Roadmap

### 6.1 Phase 0: Foundation (Week 1-2)

**Goal:** Set up project structure, basic infrastructure

**Tasks:**
- Create workspace Cargo.toml with all crates
- Set up tamagotchi-core crate with basic types
- Set up tamagotchi-tui crate with ratatui
- Implement basic game loop
- Create core entity system (Tamagotchi struct)
- Implement basic life cycle (Egg -> Baby -> ...)
- Set up time system with pause functionality
- Create basic need system (hunger, happiness)
- Set up logging with tracing
- Create basic error handling

**Deliverables:**
- Compiling project structure
- Basic Tamagotchi that ages through stages
- Simple text-based display
- Time system that can be paused

### 6.2 Phase 1: Core Gameplay (Week 3-4)

**Goal:** Implement basic Tamagotchi care mechanics

**Tasks:**
- Implement hunger system with meal times
- Implement happiness system with games
- Implement health system (poop, cleaning)
- Implement discipline system
- Create basic mini-games (2-3):
  - Pop the Balloon
  - Speed Eating
  - Simple reaction game
- Implement character/personality system
- Create basic UI components:
  - Status display
  - Care menu
  - Game selection
- Implement save/load system
- Add basic keyboard controls

**Deliverables:**
- Playable Tamagotchi with care mechanics
- 2-3 mini-games
- Basic UI with status display
- Save/load functionality
- Keyboard controls

### 6.3 Phase 2: Enhanced Features (Week 5-6)

**Goal:** Add social and economic systems

**Tasks:**
- Implement Gotchi Points economy
- Create shop system with items
- Add inventory system
- Implement friendship system
- Implement marriage system
- Add matchmaker (Otokitchi) logic
- Create multi-Tamagotchi support
- Implement location system (Tamagotchi Planet)
- Add transit passes
- Create more mini-games (5+ total)
- Add character customization (accessories)
- Implement wallpaper/decorations

**Deliverables:**
- Complete economy with shop
- Social features (friendship, marriage)
- Multiple locations
- 5+ mini-games
- Customization options

### 6.4 Phase 3: MiOS Integration (Week 7-8)

**Goal:** Full MiOS Inputd integration

**Tasks:**
- Create tamagotchi-inputd crate
- Implement Inputd client library
- Register Tamagotchi with MiOS keybindings
- Add controller support
- Implement navigation profiles
- Add tmux integration
- Create MiOS SSOT configuration
- Implement MCP server for agents
- Add session management
- Test with MiOS terminal

**Deliverables:**
- Full MiOS Inputd integration
- Controller support
- MiOS keybinding compatibility
- MCP agent support

### 6.5 Phase 4: Polish & Advanced Features (Week 9-10)

**Goal:** Polish and add nice-to-have features

**Tasks:**
- Add color TUI support (256 colors)
- Implement camera/screenshot feature
- Add online connectivity (local network)
- Create DLC/content system
- Add touch screen support (experimental)
- Implement seasonal events
- Add achievements system
- Create comprehensive documentation
- Write unit tests
- Write integration tests
- Performance optimization
- Accessibility features

**Deliverables:**
- Polished TUI with colors
- Advanced features
- Comprehensive tests
- Full documentation

### 6.6 Phase 5: Deployment (Week 11)

**Goal:** Package and deploy

**Tasks:**
- Create RPM package for Fedora
- Create Debian package
- Create Flatpak package
- Add to MiOS image build
- Create installation script
- Write user documentation
- Create man pages
- Set up CI/CD pipeline
- Final testing
- Release v1.0.0

**Deliverables:**
- Installable packages
- MiOS integration
- User documentation
- CI/CD pipeline

### 6.7 Milestone Summary

| Milestone | Week | Features | Status |
|-----------|------|----------|--------|
| M0: Foundation | 1-2 | Project setup, core types, basic loop | Pending |
| M1: Core Gameplay | 3-4 | Care mechanics, mini-games, basic UI | Pending |
| M2: Enhanced Features | 5-6 | Economy, social, locations, customization | Pending |
| M3: MiOS Integration | 7-8 | Inputd, controllers, keybindings, MCP | Pending |
| M4: Polish | 9-10 | Colors, advanced features, tests, docs | Pending |
| M5: Deployment | 11 | Packaging, installation, release | Pending |

---

## 7. Dependencies

### 7.1 Rust Crates

**Workspace Cargo.toml:**
```toml
[workspace]
members = [
    "crates/tamagotchi-core",
    "crates/tamagotchi-tui",
    "crates/tamagotchi-inputd",
    "crates/tamagotchi-mcp",
]
resolver = "2"

[workspace.dependencies]
serde = { version = "1.0", features = ["derive"] }
serde_json = "1.0"
toml = "0.8"
uuid = { version = "1.0", features = ["v4", "serde"] }
chrono = { version = "0.4", features = ["serde"] }
thiserror = "1.0"
anyhow = "1.0"
tracing = "0.1"
tracing-subscriber = { version = "0.3", features = ["env-filter"] }
tokio = { version = "1.0", features = ["full"] }
ratatui = "0.28"
crossterm = "0.28"
mios-resolver = { path = "../MiOS/tools/native/mios-resolver" }
mios-inputd = { path = "../MiOS/tools/native/mios-inputd" }
clap = { version = "4.0", features = ["derive"] }
```

### 7.2 System Dependencies

- Linux kernel 5.4+
- glibc 2.32+
- libudev (for controller detection)
- libevdev (optional, for advanced controller features)

### 7.3 MiOS Dependencies

- mios-resolver - Configuration resolution from SSOT
- mios-inputd - Input handling and controller support
- mios-service-core - Service management (optional)

---

## 8. Testing Strategy

### 8.1 Unit Tests

Test coverage targets:
- tamagotchi-core: 90%+
- tamagotchi-tui: 80%+
- tamagotchi-inputd: 85%+
- tamagotchi-mcp: 80%+

### 8.2 Test Categories

1. **Core Logic Tests**
   - Life cycle transitions
   - Need decay and filling
   - Personality development
   - Genetics and marriage
   - Time system

2. **UI Tests**
   - Component rendering
   - Layout calculations
   - Input handling
   - Theme application

3. **Input Tests**
   - Keyboard bindings
   - Controller mappings
   - Inputd integration
   - Context switching

4. **Integration Tests**
   - Save/load roundtrip
   - Multi-device connection
   - Shop transactions
   - Mini-game scoring

5. **MiOS Integration Tests**
   - SSOT configuration loading
   - Inputd client communication
   - Tmux compatibility
   - MCP server functionality

---

## 9. Deployment

### 9.1 Build Process

```bash
# Build all crates
cargo build --workspace --release

# Build individual crates
cargo build -p tamagotchi-core --release
cargo build -p tamagotchi-tui --release

# Run tests
cargo test --workspace

# Generate documentation
cargo doc --workspace --no-deps --open
```

### 9.2 Installation Methods

**MiOS Image Integration:**
```bash
# In MiOS Containerfile
COPY --from=builder /target/release/tamagotchi-tui /usr/local/bin/tamagotchi-tui
COPY --from=builder /target/release/tamagotchi-mcp /usr/local/bin/tamagotchi-mcp
```

**RPM Package:**
- Create spec file for Fedora
- Build with cargo and rpmbuild

**Flatpak Package:**
- Create manifest for Flatpak
- Build with flatpak-builder

### 9.3 Configuration Files

**MiOS SSOT Integration:**
```toml
# In usr/share/mios/mios.toml
[tamagotchi]
enabled = true
max_instances = 4
save_directory = "/var/lib/mios/tamagotchi"
auto_save_interval_minutes = 5

# Tamagotchi-specific keybindings
[[keybindings.actions]]
id = "tamagotchi"
label = "Tamagotchi TUI"
key = "t"
command = "/usr/local/bin/tamagotchi-tui"
```

**Systemd Service (MCP Server):**
```ini
# /usr/lib/systemd/system/tamagotchi-mcp.service
[Unit]
Description=Tamagotchi MCP Server
After=network.target mios-inputd.service
Requires=mios-inputd.service

[Service]
Type=simple
ExecStart=/usr/local/bin/tamagotchi-mcp
Restart=on-failure
RestartSec=5
Environment=RUST_LOG=info

[Install]
WantedBy=multi-user.target
```

### 9.4 File Locations

| Purpose | Path | Notes |
|---------|------|-------|
| Binary | /usr/local/bin/tamagotchi-tui | Main executable |
| Binary | /usr/local/bin/tamagotchi-mcp | MCP server |
| Config | /usr/share/mios/mios.toml | MiOS SSOT |
| Config | ~/.config/tamagotchi/config.toml | User config |
| Saves | /var/lib/mios/tamagotchi/ | Persistent saves |
| Logs | /var/log/mios/tamagotchi.log | Application logs |
| Cache | ~/.cache/tamagotchi/ | Temporary files |

---

## 10. License

Apache License 2.0 - Consistent with MiOS licensing policy.

---

## Document Information

- **Author:** Vibe Code (MiOS Agent)
- **Created:** 2025-01-XX
- **Version:** 1.0.0-draft
- **Status:** Draft / In Review
- **Target Audience:** MiOS Developers, Tamagotchi Enthusiasts, Rust Programmers

---

*This plan is a living document and will be updated as development progresses.*


---

## 11. Graphics & Rendering

**See separate documents:**
- [`tamagotchi-graphics-spec.md`](tamagotchi-graphics-spec.md) - Main graphics and rendering architecture
- [`tamagotchi-sprites-spec.md`](tamagotchi-sprites-spec.md) - 8-bit and pseudo-16-bit sprite system

This comprehensive graphics specification covers:

### Graphics Targets
- **Terminal Capability Matrix** - Support for VT100, xterm, Linux VT, Alacritty, Kitty, WezTerm, Console (TTY0/VT0), and Headless modes
- **Graphics Quality Tiers** - 4 tiers from ASCII-only to True Color

### Character Sets
- **Block Characters** - Full, half, quadrant blocks for low-resolution rendering
- **Braille Patterns** - 2x4 and 2x3 pixel patterns for high-resolution sprites
- **Box Drawing Characters** - For borders and UI elements

### Sprite System
- **Tier-Specific Sprites** - Each Tamagotchi species has sprites for all 4 graphics tiers
- **Sprite Registry** - Central repository for all game sprites
- **Sprite Examples** - ASCII, Extended ASCII, 256-color, and True Color versions

### Color Systems
- **16-Color ANSI Palette** - For basic terminals
- **256-Color Palette** - 16-bit style color definitions
- **True Color (24-bit)** - Full RGB with 16-bit aesthetic
- **Tamagotchi Species Colors** - Unique colors for each character type

### Rendering Backends
- **Terminal Capability Detection** - Automatic detection of terminal features
- **Adaptive Renderer** - Automatically selects best rendering mode
- **Ratatui Backend** - Primary backend for modern terminals
- **Console Direct Backend** - Optimized for Linux TTY0/VT0
- **Headless Text Backend** - Pure text output for SSH/CI

### Layout Examples
- **Low Resolution (80x24)** - Tier 0/1 with block characters
- **Medium Resolution (120x40)** - Tier 2 with ASCII art
- **High Resolution (160x60)** - Tier 3 with full graphics

### Console & TTY0/VT0 Support
- **Linux Console Detection** - Detects when running on Linux console
- **Console-Optimized Rendering** - Uses VGA-compatible characters only
- **gpm Mouse Support** - Optional mouse support via gpm

### Headless Mode Support
- **Headless Detection** - Detects SSH, CI, and other headless environments
- **Text-Only Output** - Pure text representation without graphics
- **Simplified Input** - Line-based input for headless mode

### Performance Optimization
- **Frame Buffering** - Reduces terminal redraws
- **Sprite Caching** - Caches rendered sprites for performance
- **Dirty Rectangle Tracking** - Only redraws changed areas

### Configuration
- **User Configuration** - `~/.config/tamagotchi/config.toml`
- **MiOS SSOT Integration** - `/usr/share/mios/mios.toml`

---

## Document Index

| Document | Purpose |
|----------|---------|
| `tamagotchi-tui-plan.md` | Main development plan (this document) |
| `tamagotchi-graphics-spec.md` | Graphics & rendering specification |
| `tamagotchi-sprites-spec.md` | 8-bit and pseudo-16-bit sprite specification |

---

## Quick Reference

### Graphics Tier Summary

| Tier | Name | Colors | Characters | Resolution | Target |
|------|------|--------|------------|------------|--------|
| 0 | ASCII-Only | 16 | ASCII | 80x24+ | All terminals (fallback) |
| 1 | Extended ASCII | 16 | ASCII + Box Drawing | 80x25 | VT100/VT220, basic |
| 2 | 256-Color | 256 | ASCII + Unicode + Braille | 80x24 to 160x90 | xterm, Linux console |
| 3 | True Color | 24-bit | Full Unicode | Any | Alacritty, Kitty, WezTerm |

### Console Support

- **TTY0/VT0:** Full support with 16 colors and VGA characters
- **Linux VT:** Full support with 256 colors and box drawing
- **Headless:** Text-only mode with no graphics

### 16-Bit Style Graphics

The Tamagotchi TUI uses a **16-bit console aesthetic** with:
- Limited color palettes (16, 256, or 24-bit)
- Pixel art sprites using block/braille characters
- Bright, saturated colors reminiscent of 16-bit consoles
- Clean, retro visual style

---

*This plan is a living document and will be updated as development progresses.*

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


## Current mature-body and habitat extension

The implemented child/adult art uses8x16/16x32 pixel canvases and24x48/32x64
solid voxel canvases, retaining sixteen original animal identities. Distinct
head/torso/limbs, autonomous gait and front-facing ASCII kaomoji replace the
floating-head silhouette; tiny HUDs retain a readable face when art is reduced.

Persistent seeded theme/layout, purchased/earned decoration state and validated
catalogues extend saves to schema3, while schemas1/2 migrate without changing
their original simulation fields. Rooms use an independent cosmetic seed;
coins change only through explicit validated purchases. The Habitat HUD and
native context-menu entry support buying/placing/hiding items and selecting a
theme. Native dialogue retains focused popup and inline-below-pet fallbacks.
See usr/share/doc/mios/tamagotchi-tui.md for the current installation contract.
