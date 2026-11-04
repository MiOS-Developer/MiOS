// AI-hint: Full-viewport pet room, responsive framed menus and animated care reactions; schema-1 game state stays independent of presentation.
// AI-related: sprite.rs, render.rs, input.rs, config.rs, specs/tamagotchi-tui
use crate::{
    capabilities::Renderer,
    characters::{self, Character},
    config::{Config, HudMode, ViewMode},
    dialogs::{Choice, DialogEvent, DialogRequest, NativeDialogs},
    game::{Care, Direction, Game, Rules},
    input::{Action, ControllerMonitor, ControllerPrompts, action},
    render::{Camera, CameraView, Colors, HabitatView, RoomWidget},
    save::Store,
    sprite::{Appearance, Expression, Sprite, SpriteStyle},
};
use crossterm::event::{
    self, DisableMouseCapture, EnableMouseCapture, Event, KeyEvent, KeyEventKind, MouseButton,
    MouseEventKind,
};
use ratatui::{
    DefaultTerminal, Frame,
    layout::{Constraint, Layout, Rect},
    style::{Modifier, Style},
    text::{Line, Span},
    widgets::{Block, Borders, Clear, Paragraph, Wrap},
};
use std::{
    cell::{Cell, RefCell},
    io,
    time::{Duration, Instant},
};

const LABELS: [&str; 8] = [
    "Feed", "Memory", "Clean", "Rest", "Medicine", "Pause", "Save", "New pet",
];
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Effect {
    Continue,
    Save,
    Quit,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Menu {
    Home,
    Care,
    Games,
    Status,
    Graphics,
    Help,
    Gallery,
    Talk,
    Roster,
    Habitat,
    Training,
}

#[derive(Clone, Copy)]
enum NativeKind {
    Talk,
    Thought,
    Context,
}
pub struct Ui {
    pub game: Game,
    pub selected: usize,
    pub confirm_reset: bool,
    reset_refused: bool,
    pub notice: String,
    pub menu: Option<Menu>,
    pub menu_selected: usize,
    gallery_stage: usize,
    pub view_mode: ViewMode,
    pub hud_mode: HudMode,
    camera_view: CameraView,
    camera_from_view: CameraView,
    zoom_percent: u16,
    zoom_target: u16,
    zoom_origin: u16,
    zoom_step: u16,
    camera_transition_frames: usize,
    camera_started: usize,
    camera_blend: u16,
    menu_scroll: usize,
    pub sprite_style: SpriteStyle,
    pub renderer: Option<Renderer>,
    pub controller: Option<ControllerPrompts>,
    frame: usize,
    reaction: Option<Expression>,
    reaction_until: usize,
    reaction_started: usize,
    reaction_frames: usize,
    viewport: Cell<(u16, u16)>,
    position: (u16, u16),
    destination: (u16, u16),
    yaw: u16,
    target_yaw: u16,
    visual_rng: u64,
    wander_ticks: u64,
    wander_speed: u16,
    mobile_stage: bool,
    catalogue: Vec<Character>,
    habitat_rules: Option<crate::habitat::HabitatRules>,
    dialog_variant: u64,
    dialog_page: usize,
    dialog_rows: Cell<usize>,
    dialog_line_count: Cell<usize>,
    native_request: Option<NativeKind>,
    native_modal: bool,
    thought_ticks: u64,
    adopt_character: Option<u8>,
    pet_area: Cell<Rect>,
    dialog_area: Cell<Rect>,
    care_targets: RefCell<Vec<(Rect, usize)>>,
}
impl Ui {
    pub fn new(game: Game) -> Self {
        let visual_rng = game.visual_seed().wrapping_add(1).max(1);
        Self {
            game,
            selected: 0,
            confirm_reset: false,
            reset_refused: false,
            notice: "Welcome home. Raise a healthy, happy adult.".into(),
            menu: None,
            menu_selected: 0,
            gallery_stage: 3,
            view_mode: ViewMode::Pixel,
            hud_mode: HudMode::Auto,
            camera_view: CameraView::Pet,
            camera_from_view: CameraView::Pet,
            zoom_percent: 100,
            zoom_target: 100,
            zoom_origin: 100,
            zoom_step: 25,
            camera_transition_frames: 8,
            camera_started: 0,
            camera_blend: 100,
            menu_scroll: 0,
            sprite_style: SpriteStyle::EightBit,
            renderer: None,
            controller: None,
            frame: 0,
            reaction: None,
            reaction_until: 0,
            reaction_started: 0,
            reaction_frames: 0,
            viewport: Cell::new((u16::MAX, u16::MAX)),
            position: (500, 700),
            destination: (500, 700),
            yaw: 0,
            target_yaw: 0,
            visual_rng,
            wander_ticks: 0,
            wander_speed: 1,
            mobile_stage: false,
            catalogue: Vec::new(),
            habitat_rules: None,
            dialog_variant: 0,
            dialog_page: 0,
            dialog_rows: Cell::new(1),
            dialog_line_count: Cell::new(1),
            native_request: None,
            native_modal: false,
            thought_ticks: 0,
            adopt_character: None,
            pet_area: Cell::new(Rect::default()),
            dialog_area: Cell::new(Rect::default()),
            care_targets: RefCell::new(Vec::new()),
        }
    }
    pub fn configured(game: Game, config: &Config) -> Self {
        let mut ui = Self::new(game);
        ui.catalogue = config.characters.clone();
        for character in &mut ui.catalogue {
            if let Ok((body, accent)) = config
                .game
                .training
                .palette(character.shape, &character.archetype)
            {
                character.body = body;
                character.accent = accent;
            }
        }
        ui.habitat_rules = Some(config.habitat.clone());
        ui.view_mode = config.view_mode;
        ui.hud_mode = config.hud_mode;
        ui.sprite_style = config.sprite_style;
        ui.camera_view = config.camera_view;
        ui.camera_from_view = config.camera_view;
        ui.zoom_percent = config.zoom_percent;
        ui.zoom_target = config.zoom_percent;
        ui.zoom_origin = config.zoom_percent;
        ui.zoom_step = config.zoom_step_percent;
        ui.camera_transition_frames = config.zoom_transition_frames;
        ui.wander_speed = config.wander_speed;
        ui.mobile_stage = !matches!(
            ui.game.stage(&config.game),
            crate::game::Stage::Egg | crate::game::Stage::Dead
        );
        ui.reaction_frames = config.reaction_frames;
        ui
    }
    pub fn animate(&mut self, frame: usize) {
        let elapsed = frame.saturating_sub(self.frame).min(24);
        if self.can_move() {
            for _ in 0..elapsed {
                let approach = |at: u16, target: u16| {
                    if at < target {
                        at.saturating_add(self.wander_speed).min(target)
                    } else {
                        at.saturating_sub(self.wander_speed).max(target)
                    }
                };
                self.position.0 = approach(self.position.0, self.destination.0);
                self.position.1 = approach(self.position.1, self.destination.1);
                let turn = (i32::from(self.target_yaw) - i32::from(self.yaw) + 540) % 360 - 180;
                self.yaw = (i32::from(self.yaw) + turn.clamp(-15, 15)).rem_euclid(360) as u16;
            }
        }
        let elapsed_camera = frame.saturating_sub(self.camera_started);
        let t = elapsed_camera
            .saturating_mul(100)
            .checked_div(self.camera_transition_frames.max(1))
            .unwrap_or(100)
            .min(100) as i32;
        let eased = t * t * (300 - 2 * t) / 10000;
        self.camera_blend = eased as u16;
        self.zoom_percent = (i32::from(self.zoom_origin)
            + (i32::from(self.zoom_target) - i32::from(self.zoom_origin)) * eased / 100)
            .clamp(50, 400) as u16;
        self.frame = frame;
    }
    fn camera(&self) -> Camera {
        let (w, h) = self.viewport.get();
        let effective = |view| {
            if (w < 28 || h < 10) && view == CameraView::Pet {
                CameraView::Face
            } else {
                view
            }
        };
        Camera {
            view: effective(self.camera_view),
            zoom_percent: self.zoom_percent,
            from_view: effective(self.camera_from_view),
            transition_percent: self.camera_blend,
        }
    }
    fn change_camera(&mut self, view: CameraView, zoom: u16) {
        self.camera_from_view = self.camera_view;
        self.camera_view = view;
        self.zoom_origin = self.zoom_percent;
        self.zoom_target = zoom.clamp(50, 400);
        self.camera_started = self.frame;
        self.camera_blend = 0;
        self.notice = format!(
            "{:?} view / {}% zoom. + - zoom, 0 reset, Z view.",
            view, self.zoom_target
        );
    }
    fn zoom_by(&mut self, inward: bool) {
        let zoom = if inward {
            self.zoom_target.saturating_add(self.zoom_step).min(400)
        } else {
            self.zoom_target.saturating_sub(self.zoom_step).max(50)
        };
        self.change_camera(self.camera_view, zoom);
    }
    fn cycle_camera(&mut self) {
        let next = match self.camera_view {
            CameraView::Room => CameraView::Pet,
            CameraView::Pet => CameraView::Face,
            CameraView::Face => CameraView::Room,
        };
        self.change_camera(next, self.zoom_target);
    }
    fn camera_yaw(&self, yaw: u16) -> u16 {
        // The camera orbits toward the face; pet simulation orientation is untouched.
        let camera = self.camera();
        let from = if camera.from_view == CameraView::Face {
            0
        } else {
            i32::from(yaw)
        };
        let to = if camera.view == CameraView::Face {
            0
        } else {
            i32::from(yaw)
        };
        let turn = (to - from + 540) % 360 - 180;
        (from + turn * i32::from(self.camera_blend) / 100).rem_euclid(360) as u16
    }
    fn can_move(&self) -> bool {
        self.mobile_stage
            && !self.game.paused
            && !self.game.sleeping
            && !self.game.ill
            && self.game.health > 0
            && self.game.challenge.is_none()
            && !self.overlay_open()
            && !(self.frame < self.reaction_until
                && matches!(
                    self.reaction,
                    Some(
                        Expression::Eating
                            | Expression::Cleaning
                            | Expression::Healing
                            | Expression::Sleeping
                    )
                ))
    }
    fn walking(&self) -> bool {
        self.can_move() && self.position != self.destination
    }
    fn next_visual_random(&mut self) -> u64 {
        self.visual_rng ^= self.visual_rng << 13;
        self.visual_rng ^= self.visual_rng >> 7;
        self.visual_rng ^= self.visual_rng << 17;
        self.visual_rng
    }
    fn simulate_behavior(&mut self, config: &Config) {
        use crate::game::Stage;
        self.mobile_stage = !matches!(self.game.stage(&config.game), Stage::Egg | Stage::Dead);
        if !self.game.paused && !self.overlay_open() {
            self.thought_ticks = self.thought_ticks.saturating_add(1);
            if config.floating_thoughts
                && self
                    .thought_ticks
                    .is_multiple_of(config.thought_interval_ticks)
            {
                self.native_request = Some(NativeKind::Thought);
            }
        }
        if !self.can_move() || self.game.energy < config.game.illness_threshold {
            return;
        }
        self.wander_ticks = self.wander_ticks.saturating_add(1);
        if !self
            .wander_ticks
            .is_multiple_of(config.wander_interval_ticks)
        {
            return;
        }
        if self.next_visual_random() % 100 < u64::from(config.wander_chance) {
            self.destination = (
                100 + (self.next_visual_random() % 801) as u16,
                300 + (self.next_visual_random() % 651) as u16,
            );
            let dx = f64::from(self.destination.0) - f64::from(self.position.0);
            let dz = f64::from(self.destination.1) - f64::from(self.position.1);
            self.target_yaw = dx.atan2(dz).to_degrees().rem_euclid(360.0) as u16;
        } else {
            self.target_yaw = ((self.next_visual_random() % 8) * 45) as u16;
        }
    }
    pub fn pet_sprite(&self, rules: &Rules, frame: usize) -> Sprite {
        let expression = self.expression(rules, frame);
        let pose = if self.reaction.is_some() && frame < self.reaction_until {
            frame.saturating_sub(self.reaction_started)
        } else {
            frame
        };
        match self.view_mode {
            ViewMode::Pixel => {
                Sprite::animated(self.game.stage(rules), expression, self.sprite_style, pose)
                    .recolored(self.appearance().body, self.appearance().accent)
            }
            ViewMode::Voxel => Sprite::voxel_with_appearance(
                self.game.stage(rules),
                expression,
                self.sprite_style,
                pose,
                self.camera_yaw(self.yaw),
                self.walking(),
                self.appearance(),
            ),
        }
    }
    fn current_character(&self) -> Option<&Character> {
        self.catalogue.iter().find(|c| c.id == self.game.character)
    }
    pub fn name(&self) -> &str {
        if self.game.progress.name.is_empty() {
            self.current_character()
                .map_or("MiOS-Mon", |c| c.name.as_str())
        } else {
            self.game.progress.name.as_str()
        }
    }
    fn appearance(&self) -> Appearance {
        self.current_character()
            .map_or_else(Appearance::default, |c| Appearance {
                shape: c.shape,
                body: c.body,
                accent: c.accent,
            })
    }
    fn words(&self, config: &Config, thought: bool) -> String {
        self.current_character().map_or_else(
            || self.notice.clone(),
            |c| {
                if thought {
                    characters::thought(
                        c,
                        &self.game,
                        &config.game,
                        self.dialog_variant
                            .wrapping_add(self.visual_rng)
                            .wrapping_add(self.game.age_ticks / config.thought_interval_ticks),
                    )
                } else {
                    characters::speech(c, &self.game, &config.game, self.dialog_variant)
                }
            },
        )
    }
    fn open_talk(&mut self) {
        self.open(Menu::Talk);
        self.dialog_page = 0;
        self.native_request = Some(NativeKind::Talk);
    }
    fn dialog_request(&self, config: &Config, kind: NativeKind) -> DialogRequest {
        DialogRequest {
            title: format!(
                "{} {}",
                self.name(),
                if matches!(kind, NativeKind::Thought) {
                    "thinks"
                } else {
                    "says"
                }
            ),
            text: self.words(
                config,
                matches!(kind, NativeKind::Thought | NativeKind::Context),
            ),
            choices: if matches!(kind, NativeKind::Context) {
                vec![
                    Choice::Listen,
                    Choice::Feed,
                    Choice::Play,
                    Choice::Clean,
                    Choice::Rest,
                    Choice::Medicine,
                    Choice::Status,
                    Choice::Graphics,
                    Choice::Roster,
                    Choice::Pause,
                    Choice::Save,
                    Choice::Quit,
                    Choice::Back,
                    Choice::Habitat,
                    Choice::Training,
                ]
            } else {
                vec![
                    Choice::Listen,
                    Choice::Feed,
                    Choice::Play,
                    Choice::Rest,
                    Choice::Back,
                ]
            },
            accept_hint: self
                .controller
                .as_ref()
                .map_or_else(|| "Enter".into(), |p| p.accept.clone()),
            back_hint: self
                .controller
                .as_ref()
                .map_or_else(|| "Escape".into(), |p| p.back.clone()),
            ascii: self.renderer == Some(Renderer::Ascii),
        }
    }
    fn apply_dialog_choice(&mut self, choice: Choice, rules: &Rules) -> Effect {
        self.menu = None;
        match choice {
            Choice::Listen => {
                self.dialog_variant = self.dialog_variant.wrapping_add(1);
                self.open_talk();
            }
            Choice::Feed => self.care(Care::Feed, rules),
            Choice::Play => self.care(Care::Play, rules),
            Choice::Clean => self.care(Care::Clean, rules),
            Choice::Rest => self.care(Care::Rest, rules),
            Choice::Medicine => self.care(Care::Medicine, rules),
            Choice::Status => self.open(Menu::Status),
            Choice::Graphics => self.open(Menu::Graphics),
            Choice::Roster => self.open(Menu::Roster),
            Choice::Habitat => self.open(Menu::Habitat),
            Choice::Training => self.open(Menu::Training),
            Choice::Pause => self.game.paused = !self.game.paused,
            Choice::Save => return Effect::Save,
            Choice::Quit => return Effect::Quit,
            Choice::Back => {}
        }
        Effect::Continue
    }
    pub fn overlay_open(&self) -> bool {
        self.menu.is_some() || self.confirm_reset || self.native_modal
    }
    fn react(&mut self, expression: Expression) {
        self.reaction = Some(expression);
        self.reaction_started = self.frame;
        self.reaction_until = self.frame.saturating_add(self.reaction_frames);
    }
    pub fn expression(&self, rules: &Rules, frame: usize) -> Expression {
        if self.game.health == 0 {
            return Expression::Dead;
        }
        if self.game.ill {
            return Expression::Sick;
        }
        if let Some(expression) = self.reaction.filter(|_| frame < self.reaction_until) {
            return expression;
        }
        if self.game.sleeping {
            Expression::Sleeping
        } else if self.game.fullness < rules.healthy_threshold {
            Expression::Hungry
        } else if self.game.happiness < rules.healthy_threshold {
            Expression::Sad
        } else if self.game.challenge.is_some() {
            Expression::Playful
        } else if self.game.thriving(rules) {
            Expression::Happy
        } else {
            Expression::Idle
        }
    }
    fn open(&mut self, menu: Menu) {
        self.menu = Some(menu);
        self.menu_selected = 0;
        self.menu_scroll = 0;
    }
    fn care(&mut self, care: Care, rules: &Rules) {
        let before = self.game.clone();
        self.notice = self.game.care(care, rules).into();
        if before.paused
            || before.health == 0
            || before.stage(rules) == crate::game::Stage::Egg
            || before.challenge.is_some()
        {
            return;
        }
        let expression = match care {
            Care::Feed if self.game.fullness > before.fullness => Expression::Eating,
            Care::Clean => Expression::Cleaning,
            Care::Rest if self.game.sleeping => Expression::Sleeping,
            Care::Rest => Expression::Excited,
            Care::Medicine
                if self.game.health > before.health || (before.ill && !self.game.ill) =>
            {
                Expression::Healing
            }
            Care::Play if self.game.challenge.is_some() => Expression::Playful,
            _ => Expression::Surprised,
        };
        self.react(expression);
    }
    pub fn handle(&mut self, key: KeyEvent, rules: &Rules) -> Effect {
        let Some(a) = action(key) else {
            return Effect::Continue;
        };
        if a == Action::Quit {
            return Effect::Quit;
        }
        let (width, height) = self.viewport.get();
        if (width < 10 || height < 4)
            && !matches!(
                a,
                Action::Save
                    | Action::Pause
                    | Action::ZoomIn
                    | Action::ZoomOut
                    | Action::ZoomReset
                    | Action::Camera
            )
        {
            self.confirm_reset = false;
            self.notice = "Enlarge the viewport to confirm a new pet.".into();
            return Effect::Continue;
        }
        if self.reset_refused {
            self.reset_refused = false;
            if matches!(a, Action::Accept | Action::Confirm) {
                return Effect::Continue;
            }
        }
        if self.confirm_reset && (width < 18 || height < 6) {
            self.confirm_reset = false;
            self.adopt_character = None;
            self.reset_refused = true;
            self.notice = "Kept pet; confirmation no longer fits.".into();
            return Effect::Continue;
        }
        if self.confirm_reset {
            match a {
                Action::Accept | Action::Confirm => {
                    let seed = self.next_visual_random();
                    self.game = Game::new(rules, seed);
                    if let Some(character) = self.adopt_character.take() {
                        self.game.character = character;
                    }
                    self.mobile_stage = false;
                    self.position = (500, 700);
                    self.destination = self.position;
                    self.yaw = 0;
                    self.target_yaw = 0;
                    self.visual_rng = self.game.visual_seed().wrapping_add(1).max(1);
                    self.wander_ticks = 0;
                    self.confirm_reset = false;
                    self.notice = "A new egg! The next save replaces the previous pet.".into();
                    self.react(Expression::Surprised);
                }
                Action::Cancel => {
                    self.confirm_reset = false;
                    self.adopt_character = None;
                    self.notice = "Kept your current pet.".into();
                }
                _ => {}
            }
            return Effect::Continue;
        }
        match a {
            Action::ZoomIn => {
                self.zoom_by(true);
                return Effect::Continue;
            }
            Action::ZoomOut => {
                self.zoom_by(false);
                return Effect::Continue;
            }
            Action::ZoomReset => {
                self.change_camera(self.camera_view, 100);
                return Effect::Continue;
            }
            Action::Camera => {
                self.cycle_camera();
                return Effect::Continue;
            }
            Action::Menu => {
                if self.menu.is_some() {
                    self.menu = None;
                } else {
                    self.open(Menu::Home);
                }
                return Effect::Continue;
            }
            Action::Talk => {
                self.open_talk();
                return Effect::Continue;
            }
            Action::Roster => {
                self.open(Menu::Roster);
                return Effect::Continue;
            }
            Action::Habitat => {
                self.open(Menu::Habitat);
                return Effect::Continue;
            }
            Action::Training => {
                self.open(Menu::Training);
                return Effect::Continue;
            }
            Action::Status => {
                self.open(Menu::Status);
                return Effect::Continue;
            }
            Action::Graphics => {
                self.open(Menu::Graphics);
                return Effect::Continue;
            }
            Action::Help => {
                self.open(Menu::Help);
                return Effect::Continue;
            }
            _ => {}
        }
        if self.menu.is_some() {
            return self.menu_input(a, rules);
        }
        if a == Action::Cancel {
            if self.game.challenge.take().is_some() {
                self.notice = "Memory game cancelled.".into();
                return Effect::Continue;
            }
            if self.controller.is_some() {
                self.open(Menu::Home);
                return Effect::Continue;
            }
            return Effect::Quit;
        }
        if a == Action::Pause {
            self.game.paused = !self.game.paused;
            self.notice = if self.game.paused {
                "Clock paused."
            } else {
                "Clock resumed."
            }
            .into();
            return Effect::Continue;
        }
        if a == Action::Save {
            return Effect::Save;
        }
        if a == Action::Reset {
            self.ask_reset();
            return Effect::Continue;
        }
        if self.game.challenge.is_some() {
            if key.kind == KeyEventKind::Press {
                if let Action::Direction(d) = a {
                    let wins = self.game.wins;
                    self.notice = self.game.guess(d, rules).into();
                    if self.game.wins > wins {
                        self.react(Expression::Celebrate);
                    } else if self.game.challenge.is_none() {
                        self.react(Expression::Sad);
                    }
                }
            }
            return Effect::Continue;
        }
        match a {
            Action::Direction(Direction::Up | Direction::Left) | Action::Previous => {
                self.selected = (self.selected + LABELS.len() - 1) % LABELS.len()
            }
            Action::Direction(Direction::Down | Direction::Right) | Action::Next => {
                self.selected = (self.selected + 1) % LABELS.len()
            }
            Action::Care(care) => self.care(care, rules),
            Action::Accept => match self.selected {
                0..=4 => self.care(
                    [
                        Care::Feed,
                        Care::Play,
                        Care::Clean,
                        Care::Rest,
                        Care::Medicine,
                    ][self.selected],
                    rules,
                ),
                5 => {
                    self.game.paused = !self.game.paused;
                    self.notice = "Clock toggled. P also pauses or resumes.".into();
                }
                6 => return Effect::Save,
                _ => self.ask_reset(),
            },
            _ => {}
        }
        Effect::Continue
    }
    fn menu_input(&mut self, action: Action, rules: &Rules) -> Effect {
        let Some(menu) = self.menu else {
            return Effect::Continue;
        };
        if menu == Menu::Roster {
            let count = self.catalogue.len().max(1);
            match action {
                Action::Cancel => self.menu = None,
                Action::Previous | Action::Direction(Direction::Up | Direction::Left) => {
                    self.menu_selected = (self.menu_selected + count - 1) % count
                }
                Action::Next | Action::Direction(Direction::Down | Direction::Right) => {
                    self.menu_selected = (self.menu_selected + 1) % count
                }
                Action::Accept => {
                    self.adopt_character = self.catalogue.get(self.menu_selected).map(|c| c.id);
                    self.menu = None;
                    self.ask_reset();
                }
                Action::Save => return Effect::Save,
                _ => {}
            }
            return Effect::Continue;
        }
        if menu == Menu::Gallery {
            match action {
                Action::Save => {
                    self.menu = None;
                    return Effect::Save;
                }
                Action::Pause => {
                    self.game.paused = !self.game.paused;
                }
                Action::Reset => {
                    self.menu = None;
                    self.ask_reset();
                }
                Action::Cancel => self.menu = None,
                Action::Direction(Direction::Left) | Action::Previous => {
                    self.menu_selected = (self.menu_selected + 15) % 16
                }
                Action::Direction(Direction::Right) | Action::Next => {
                    self.menu_selected = (self.menu_selected + 1) % 16
                }
                Action::Direction(Direction::Up) => {
                    self.gallery_stage = (self.gallery_stage + 4) % 5
                }
                Action::Direction(Direction::Down) => {
                    self.gallery_stage = (self.gallery_stage + 1) % 5
                }
                Action::Accept => {
                    self.sprite_style = match self.sprite_style {
                        SpriteStyle::EightBit => SpriteStyle::SixteenBit,
                        SpriteStyle::SixteenBit => SpriteStyle::EightBit,
                    }
                }
                _ => {}
            }
            return Effect::Continue;
        }
        if matches!(menu, Menu::Status | Menu::Help) {
            match action {
                Action::Direction(Direction::Down | Direction::Right) | Action::Next => {
                    self.menu_scroll = self.menu_scroll.saturating_add(1).min(512);
                    return Effect::Continue;
                }
                Action::Direction(Direction::Up | Direction::Left) | Action::Previous => {
                    self.menu_scroll = self.menu_scroll.saturating_sub(1);
                    return Effect::Continue;
                }
                _ => {}
            }
        }
        let count = match menu {
            Menu::Home => 12,
            Menu::Care => 5,
            Menu::Games => 2,
            Menu::Graphics => 19,
            Menu::Training => 6,
            Menu::Status | Menu::Help => 1,
            Menu::Gallery => 16,
            Menu::Talk => 7,
            Menu::Roster => self.catalogue.len().max(1),
            Menu::Habitat => self.habitat_rules.as_ref().map_or(2, |r| r.items.len() + 2),
        };
        match action {
            Action::Cancel => {
                self.menu = None;
            }
            Action::Previous | Action::Direction(Direction::Up | Direction::Left) => {
                self.menu_selected = (self.menu_selected + count - 1) % count
            }
            Action::Next | Action::Direction(Direction::Down | Direction::Right) => {
                self.menu_selected = (self.menu_selected + 1) % count
            }
            Action::Save => {
                self.menu = None;
                return Effect::Save;
            }
            Action::Pause => {
                self.game.paused = !self.game.paused;
            }
            Action::Care(care) => {
                self.menu = None;
                self.care(care, rules);
            }
            Action::Reset => {
                self.menu = None;
                self.ask_reset();
            }
            Action::Accept => match menu {
                Menu::Home => match self.menu_selected {
                    0 => self.open(Menu::Care),
                    1 => self.open(Menu::Games),
                    2 => self.open_talk(),
                    3 => self.open(Menu::Roster),
                    4 => self.open(Menu::Status),
                    5 => self.open(Menu::Graphics),
                    6 => self.open(Menu::Gallery),
                    7 => self.open(Menu::Help),
                    8 => {
                        self.menu = None;
                        return Effect::Save;
                    }
                    9 => return Effect::Quit,
                    10 => self.open(Menu::Habitat),
                    _ => self.open(Menu::Training),
                },
                Menu::Care => {
                    self.menu = None;
                    if self.menu_selected < 4 {
                        self.care(
                            [Care::Feed, Care::Clean, Care::Rest, Care::Medicine]
                                [self.menu_selected],
                            rules,
                        );
                    }
                }
                Menu::Games => {
                    self.menu = None;
                    if self.menu_selected == 0 {
                        self.care(Care::Play, rules);
                    }
                }
                Menu::Graphics => match self.menu_selected {
                    0 => self.view_mode = ViewMode::Voxel,
                    1 => self.view_mode = ViewMode::Pixel,
                    2 => self.sprite_style = SpriteStyle::EightBit,
                    3 => self.sprite_style = SpriteStyle::SixteenBit,
                    4 => self.renderer = Some(Renderer::HalfBlock),
                    5 => self.renderer = Some(Renderer::Ascii),
                    6 => self.renderer = Some(Renderer::Quadrant),
                    7 => self.renderer = Some(Renderer::Braille),
                    8 => self.open(Menu::Gallery),
                    9 => self.hud_mode = HudMode::Auto,
                    10 => self.hud_mode = HudMode::Minimal,
                    11 => self.hud_mode = HudMode::Full,
                    12 => self.change_camera(CameraView::Room, self.zoom_target),
                    13 => self.change_camera(CameraView::Pet, self.zoom_target),
                    14 => self.change_camera(CameraView::Face, self.zoom_target),
                    15 => self.zoom_by(true),
                    16 => self.zoom_by(false),
                    17 => self.change_camera(self.camera_view, 100),
                    _ => self.menu = None,
                },
                Menu::Training => {
                    if self.menu_selected >= 5 {
                        self.menu = None;
                    } else {
                        match self.game.train(self.menu_selected, rules) {
                            Ok(message) => {
                                self.notice = message;
                                self.react(Expression::Celebrate);
                            }
                            Err(message) => {
                                self.notice = message;
                                self.react(Expression::Surprised);
                            }
                        }
                    }
                }
                Menu::Status | Menu::Help => self.menu = None,
                Menu::Talk => {
                    if self.menu_selected == 0 {
                        let rows = self.dialog_rows.get().max(1);
                        if self.dialog_page + rows < self.dialog_line_count.get() {
                            self.dialog_page += rows;
                        } else {
                            self.dialog_page = 0;
                            self.dialog_variant = self.dialog_variant.wrapping_add(1);
                        }
                    } else {
                        self.menu = None;
                        if self.menu_selected < 6 {
                            self.care(
                                [
                                    Care::Feed,
                                    Care::Play,
                                    Care::Clean,
                                    Care::Rest,
                                    Care::Medicine,
                                ][self.menu_selected - 1],
                                rules,
                            );
                        }
                    }
                }
                Menu::Habitat => {
                    if let Some(habitat) = &self.habitat_rules {
                        let outcome = if let Some(item) = habitat.items.get(self.menu_selected) {
                            if self.game.habitat.owns(
                                item.id,
                                self.game.wins,
                                self.game.age_ticks,
                                habitat,
                            ) {
                                self.game.toggle_item(item.id, habitat)
                            } else {
                                self.game
                                    .buy_item(item.id, habitat)
                                    .and_then(|_| self.game.toggle_item(item.id, habitat))
                            }
                        } else if self.menu_selected == habitat.items.len() {
                            let next =
                                (usize::from(self.game.habitat.theme) + 1) % habitat.themes.len();
                            self.game.set_theme(next as u8, habitat)
                        } else {
                            self.menu = None;
                            return Effect::Continue;
                        };
                        self.notice = outcome.unwrap_or_else(|error| error);
                    } else {
                        self.notice = "Habitat catalogue unavailable.".into();
                    }
                }
                Menu::Gallery | Menu::Roster => {}
            },
            _ => {}
        }
        Effect::Continue
    }
    fn ask_reset(&mut self) {
        let (w, h) = self.viewport.get();
        if w < 18 || h < 6 {
            self.confirm_reset = false;
            self.reset_refused = true;
            self.notice = "Enlarge to confirm a new pet.".into();
            return;
        }
        self.confirm_reset = true;
        self.notice = "Replace this pet? Enter/Y confirms; Escape keeps it.".into();
    }
    pub fn summary(&self, rules: &Rules) -> String {
        format!(
            "{} - {:?} | age {} ticks | coins {} | memory wins {}\nFull {}  Happy {}  Clean {}  Energy {}  Health {}\n{}{}{}",
            self.name(),
            self.game.stage(rules),
            self.game.age_ticks,
            self.game.coins,
            self.game.wins,
            self.game.fullness,
            self.game.happiness,
            self.game.hygiene,
            self.game.energy,
            self.game.health,
            if self.game.paused { "PAUSED " } else { "" },
            if self.game.sleeping { "RESTING " } else { "" },
            if self.game.ill {
                "ILL - clean, then give medicine"
            } else {
                ""
            }
        )
    }
    pub fn draw(
        &self,
        screen: &mut Frame,
        config: &Config,
        renderer: Renderer,
        colors: &Colors,
        frame: usize,
        persistent: bool,
    ) {
        let renderer = self.renderer.unwrap_or(renderer);
        let ascii = renderer == Renderer::Ascii;
        let area = screen.area();
        self.pet_area.set(Rect::default());
        self.dialog_area.set(Rect::default());
        self.care_targets.borrow_mut().clear();
        let face = self.expression(&config.game, frame).kaomoji(frame);
        let stage = format!("{:?}", self.game.stage(&config.game));
        let clock = if self.game.paused {
            "P"
        } else if self.game.sleeping {
            "R"
        } else {
            "L"
        };
        let heading = if area.width < 16 {
            format!(
                "{} {}{clock}",
                self.name().chars().take(5).collect::<String>(),
                &stage[..1]
            )
        } else if area.width < 28 {
            let name = self
                .name()
                .chars()
                .take(usize::from(area.width.saturating_sub(11)))
                .collect::<String>();
            format!("{name} {}{clock} {face}", &stage[..1])
        } else {
            format!(
                "PET ROOM {} {} {stage} {clock} {face} {:?} {}%",
                self.name(),
                self.current_character()
                    .map_or("pet", |c| c.species.as_str()),
                self.camera().view,
                self.zoom_percent
            )
        };
        let compact = self.hud_mode == HudMode::Minimal
            || area.width < config.hud_minimal_width
            || area.height < config.hud_minimal_height;
        let footer = if compact {
            crate::hud::needs(&self.game, area.width.saturating_sub(2), frame / 14)
        } else {
            "MIOS / PET ROOM".into()
        };
        let outer = panel(&heading, colors, ascii)
            .title_bottom(Line::from(footer).style(Style::default().fg(colors.ink(7))));
        let inner = outer.inner(area);
        self.viewport.set((inner.width, inner.height));
        screen.render_widget(outer, area);
        if inner.width < 10 || inner.height < 4 {
            screen.render_widget(
                Paragraph::new(format!(
                    "Mio / {:?}\n{}\n{}",
                    self.game.stage(&config.game),
                    if self.game.paused { "PAUSED" } else { "LIVE" },
                    self.controller
                        .as_ref()
                        .and_then(|p| p.quit.as_ref())
                        .map_or_else(
                            || "Tiny viewport: Q quits safely".to_owned(),
                            |q| format!("{q}: quit")
                        )
                ))
                .style(Style::default().fg(colors.ink(2))),
                inner,
            );
            return;
        }
        if self.hud_mode == HudMode::Minimal
            || inner.width < 34
            || inner.height < 12
            || (self.hud_mode == HudMode::Auto
                && (area.width < config.hud_minimal_width
                    || area.height < config.hud_minimal_height))
        {
            self.draw_minimal(screen, config, renderer, colors, frame, inner);
            if let Some(menu) = self.menu.filter(|_| !self.native_modal) {
                self.draw_menu(screen, menu, config, colors, renderer);
            }
            self.draw_reset(screen, colors, ascii);
            return;
        }
        let [header, content, dialogue, dock] = Layout::vertical([
            Constraint::Length(3),
            Constraint::Min(1),
            Constraint::Length(config.inline_dialog_rows),
            Constraint::Length(5),
        ])
        .areas(inner);
        let style = match self.sprite_style {
            SpriteStyle::EightBit => "8-BIT",
            SpriteStyle::SixteenBit => "16-BIT",
        };
        let clock = if self.game.paused {
            "PAUSED"
        } else if self.overlay_open() {
            "MENU"
        } else {
            "LIVE"
        };
        let status = if self.game.ill {
            "NEEDS MEDICINE"
        } else if self.game.thriving(&config.game) {
            "THRIVING ADULT"
        } else if self.game.sleeping {
            "SLEEPING"
        } else {
            "WELCOME HOME"
        };
        screen.render_widget(
            Paragraph::new(vec![
                Line::from(vec![
                    Span::styled(
                        format!(" {} ", self.name()),
                        Style::default()
                            .fg(colors.ink(0))
                            .bg(colors.ink(3))
                            .add_modifier(Modifier::BOLD),
                    ),
                    Span::raw(format!(
                        "  {style} {} / {clock} / {status}",
                        if self.view_mode == ViewMode::Voxel {
                            "VOXEL 3D"
                        } else {
                            "PIXEL"
                        }
                    )),
                    Span::styled(
                        format!("  {} coins  ", self.game.coins),
                        Style::default().fg(colors.ink(7)),
                    ),
                ]),
                Line::from(format!(
                    " {:?}  |  Age {} ticks  |  {} memory wins  |  {}",
                    self.game.stage(&config.game),
                    self.game.age_ticks,
                    self.game.wins,
                    if persistent {
                        "LOCAL SAVE"
                    } else {
                        "SAMPLE / NO SAVE"
                    }
                )),
                Line::from(format!(
                    " Full {}  Happy {}  Clean {}  Energy {}  Health {}",
                    self.game.fullness,
                    self.game.happiness,
                    self.game.hygiene,
                    self.game.energy,
                    self.game.health
                )),
            ])
            .style(Style::default().fg(colors.ink(2))),
            header,
        );
        let proportions = crate::hud::split(
            content,
            config.hud_golden_ratio,
            config.hud_landscape_columns,
            config.hud_portrait_ratio_percent,
        );
        let (room, side) = if proportions.landscape {
            (proportions.scene, Some(proportions.info))
        } else {
            (content, None)
        };
        let expression = self.expression(&config.game, frame);
        let room_title = format!(
            " {:?} {}% / {:?} {} ",
            self.camera_view,
            self.zoom_percent,
            expression,
            expression.kaomoji(frame)
        );
        let border = panel(&room_title, colors, ascii);
        let room_inner = border.inner(room);
        screen.render_widget(border, room);
        self.pet_area.set(room_inner);
        let sprite = self.pet_sprite(&config.game, frame);
        screen.render_widget(
            RoomWidget {
                camera: self.camera(),
                sprite: &sprite,
                renderer,
                scale: config.scale,
                colors,
                expression,
                frame,
                position: self.position,
                habitat: Some(HabitatView {
                    state: &self.game.habitat,
                    rules: &config.habitat,
                    wins: self.game.wins,
                    age: self.game.age_ticks,
                }),
            },
            room_inner,
        );
        if let Some(side) = side {
            self.draw_status(screen, side, colors, ascii);
        }
        let dock_title = self.controller.as_ref().map_or_else(
            || " QUICK CARE ".to_owned(),
            |pad| format!(" {} / CONTROLLER ", pad.family),
        );
        let border = panel(&dock_title, colors, ascii);
        let actions = border.inner(dock);
        screen.render_widget(border, dock);
        let mut lines = Vec::new();
        let shortcuts = ["F", "G", "C", "R", "M", "P", "S", "N"];
        for row in 0..2 {
            let mut spans = Vec::new();
            let mut offset = 0u16;
            for i in row * 4..row * 4 + 4 {
                let label = if i == 3 && self.game.sleeping {
                    "Wake"
                } else {
                    LABELS[i]
                };
                let style = if self.selected == i {
                    Style::default()
                        .fg(colors.ink(0))
                        .bg(colors.ink(7))
                        .add_modifier(Modifier::BOLD)
                } else {
                    Style::default().fg(colors.ink(2)).bg(colors.ink(6))
                };
                let caption = if let Some(pad) = &self.controller {
                    if self.selected == i {
                        format!("[{}] {label}", pad.accept)
                    } else {
                        label.into()
                    }
                } else {
                    format!("[{}] {label}", shortcuts[i])
                };
                let printed = format!(" {caption:<14} ");
                let width = printed.len().min(usize::from(u16::MAX)) as u16;
                let rect = Rect::new(
                    actions.x.saturating_add(offset),
                    actions.y.saturating_add(row as u16),
                    width,
                    1,
                )
                .intersection(actions);
                self.care_targets.borrow_mut().push((rect, i));
                offset = offset.saturating_add(width + 1);
                spans.push(Span::styled(printed, style));
                spans.push(Span::raw(" "));
            }
            lines.push(Line::from(spans));
        }
        lines.push(
            Line::from(if let Some(pad) = &self.controller {
                format!(
                    "{} Select [{}] Act  [{}] Menu  [{}] Next",
                    pad.navigate, pad.accept, pad.back, pad.next
                )
            } else {
                " O Menu H Habitat | +/- zoom 0 reset Z view | Arrows/Tab + Enter | Q Quit".into()
            })
            .style(Style::default().fg(colors.ink(13))),
        );
        screen.render_widget(Paragraph::new(lines), actions);
        if let Some(challenge) = &self.game.challenge {
            let text = if challenge.reveal > 0 {
                format!(
                    "MEMORY / WATCH ({})\n{}\nWait for the pattern to disappear.",
                    challenge.reveal,
                    challenge
                        .pattern
                        .iter()
                        .map(|d| d.label())
                        .collect::<Vec<_>>()
                        .join("  ")
                )
            } else {
                format!(
                    "MEMORY / REPEAT\n{} of 3 arrows correct\n{}",
                    challenge.entered,
                    self.controller.as_ref().map_or_else(
                        || "Arrows repeat. Escape cancels. P pauses.".to_owned(),
                        |pad| format!("{} repeat. [{}] Cancel.", pad.navigate, pad.back)
                    )
                )
            };
            self.bubble(screen, room, &text, colors, ascii);
        }
        self.draw_dialogue(screen, config, colors, renderer, dialogue, false);
        if let Some(menu) = self.menu.filter(|_| !self.native_modal) {
            self.draw_menu(screen, menu, config, colors, renderer);
        }
        self.draw_reset(screen, colors, ascii);
    }
    fn draw_minimal(
        &self,
        screen: &mut Frame,
        config: &Config,
        renderer: Renderer,
        colors: &Colors,
        frame: usize,
        area: Rect,
    ) {
        let talking = self.menu == Some(Menu::Talk) && !self.native_modal;
        let layout = crate::hud::split(
            area,
            config.hud_golden_ratio,
            config.hud_landscape_columns,
            config.hud_portrait_ratio_percent,
        );
        let scene = layout.scene;
        let info = layout.info;
        self.pet_area.set(scene);
        let sprite = self.pet_sprite(&config.game, frame);
        let expression = self.expression(&config.game, frame);
        screen.render_widget(
            RoomWidget {
                camera: self.camera(),
                sprite: &sprite,
                renderer,
                scale: 0,
                colors,
                expression,
                frame,
                position: self.position,
                habitat: Some(HabitatView {
                    state: &self.game.habitat,
                    rules: &config.habitat,
                    wins: self.game.wins,
                    age: self.game.age_ticks,
                }),
            },
            scene,
        );
        if talking {
            let body = if info.height >= 3 {
                info
            } else {
                Rect::new(
                    area.x,
                    area.bottom().saturating_sub(3),
                    area.width,
                    3.min(area.height),
                )
            };
            self.draw_dialogue(screen, config, colors, renderer, body, true);
        } else if layout.landscape {
            let mut lines = vec![
                Line::from(format!(
                    "{} / {:?}",
                    self.name(),
                    self.game.stage(&config.game)
                ))
                .style(Style::default().fg(colors.ink(7))),
                Line::from(format!(
                    "{:?} {} / {:?} {}%",
                    expression,
                    expression.kaomoji(frame),
                    self.camera().view,
                    self.zoom_percent
                )),
                Line::from(format!(
                    "Age {} | {} coins | {} wins",
                    self.game.age_ticks, self.game.coins, self.game.wins
                )),
            ];
            for (label, value, index) in [
                ("Food", self.game.fullness, 8),
                ("Happy", self.game.happiness, 12),
                ("Clean", self.game.hygiene, 9),
                ("Energy", self.game.energy, 7),
                ("Health", self.game.health, 15),
            ] {
                let width = usize::from(info.width.saturating_sub(15)).min(24);
                let filled = width * usize::from(value) / 100;
                lines.push(
                    Line::from(format!(
                        "{label:6} {value:3} [{}{}]",
                        "#".repeat(filled),
                        ".".repeat(width.saturating_sub(filled))
                    ))
                    .style(Style::default().fg(colors.ink(index))),
                );
            }
            if self.game.progress.hatched && info.height >= 19 {
                for (index, stat) in self.game.progress.stats.iter().enumerate() {
                    lines.push(Line::from(format!(
                        "{} {} / Lv{} / {} XP",
                        crate::progression::TrainingRules::label(index),
                        stat.value,
                        stat.level,
                        stat.xp
                    )));
                }
            }
            if let Some(theme) = config.habitat.theme(self.game.habitat.theme) {
                lines.push(Line::from(theme.name.clone()));
            }
            lines.push(Line::from(format!(
                "{} items placed | {}",
                self.game.habitat.placed.count_ones(),
                if self.game.paused {
                    "PAUSED"
                } else if self.game.sleeping {
                    "RESTING"
                } else {
                    "LIVE"
                }
            )));
            if let Some(pad) = &self.controller {
                lines.push(Line::from(format!("{} / Inputd focused", pad.family)));
                lines.push(Line::from(pad.release.clone()));
            }
            let footer_rows = if self.controller.is_some() { 3 } else { 2 };
            let available = usize::from(info.height).saturating_sub(lines.len() + footer_rows);
            lines.extend(
                wrap_dialogue(&self.words(config, true), usize::from(info.width))
                    .into_iter()
                    .take(available)
                    .map(Line::from),
            );
            let footer = Rect::new(
                info.x,
                info.bottom().saturating_sub(footer_rows as u16),
                info.width,
                (footer_rows as u16).min(info.height),
            );
            let upper = Rect::new(
                info.x,
                info.y,
                info.width,
                info.height.saturating_sub(footer.height),
            );
            screen.render_widget(
                Paragraph::new(lines).style(Style::default().fg(colors.ink(2))),
                upper,
            );
            let hints = if let Some(pad) = &self.controller {
                format!(
                    "{} {}\n{} Menu\n{} Next / V Graphics",
                    tiny_button(&pad.accept),
                    LABELS[self.selected],
                    tiny_button(&pad.back),
                    tiny_button(&pad.next)
                )
            } else {
                format!(
                    "[Enter] {} O Menu\n+/- zoom 0 reset Z view",
                    LABELS[self.selected]
                )
            };
            screen.render_widget(
                Paragraph::new(hints).style(Style::default().fg(colors.ink(13))),
                footer,
            );
            self.care_targets.borrow_mut().push((
                Rect::new(footer.x, footer.y, footer.width, 1),
                self.selected,
            ));
        } else {
            let control_rows = if self.controller.is_some() { 2 } else { 1 }.min(info.height);
            let controls = Rect::new(
                info.x,
                info.bottom().saturating_sub(control_rows),
                info.width,
                control_rows,
            );
            let metadata_rows = info.height.saturating_sub(control_rows).min(1);
            let metadata = Rect::new(info.x, info.y, info.width, metadata_rows);
            let thought = Rect::new(
                info.x,
                info.y.saturating_add(metadata_rows),
                info.width,
                info.height.saturating_sub(control_rows + metadata_rows),
            );
            screen.render_widget(
                Paragraph::new(self.compact_metadata(info.width, frame))
                    .style(Style::default().fg(colors.ink(7))),
                metadata,
            );
            if thought.height > 0 {
                self.draw_dialogue(screen, config, colors, renderer, thought, true);
            }
            let hint = if let Some(pad) = &self.controller {
                format!(
                    "{}:{}\n{}:Menu",
                    tiny_button(&pad.accept),
                    ["Feed", "Play", "Wash", "Rest", "Med", "Paus", "Save", "New"][self.selected],
                    tiny_button(&pad.back)
                )
            } else if controls.width < 18 {
                format!(
                    "Ent {} O",
                    ["Feed", "Play", "Wash", "Rest", "Med", "Paus", "Save", "New"][self.selected]
                )
            } else {
                format!(
                    "[Enter] {} O",
                    ["Feed", "Play", "Wash", "Rest", "Med", "Paus", "Save", "New"][self.selected]
                )
            };
            screen.render_widget(
                Paragraph::new(hint).style(Style::default().fg(colors.ink(13))),
                controls,
            );
            self.care_targets.borrow_mut().push((
                Rect::new(
                    controls.x,
                    controls.y,
                    controls.width,
                    1.min(controls.height),
                ),
                self.selected,
            ));
        }
        if let Some(challenge) = &self.game.challenge {
            let text = if challenge.reveal > 0 {
                format!(
                    "WATCH {}\n{}",
                    challenge.reveal,
                    challenge
                        .pattern
                        .iter()
                        .map(|d| match d {
                            Direction::Up => "U",
                            Direction::Down => "D",
                            Direction::Left => "L",
                            Direction::Right => "R",
                        })
                        .collect::<Vec<_>>()
                        .join(" ")
                )
            } else {
                format!(
                    "REPEAT {}/3\n{}",
                    challenge.entered,
                    self.controller.as_ref().map_or_else(
                        || "Arrows; Esc Back".to_owned(),
                        |p| format!("{}; {} Back", p.navigate, p.back)
                    )
                )
            };
            self.bubble(screen, area, &text, colors, renderer == Renderer::Ascii);
        }
    }
    fn draw_dialogue(
        &self,
        screen: &mut Frame,
        config: &Config,
        colors: &Colors,
        renderer: Renderer,
        area: Rect,
        compact: bool,
    ) {
        if area.width == 0 || area.height == 0 {
            return;
        }
        self.dialog_area.set(area);
        let talking = self.menu == Some(Menu::Talk) && !self.native_modal;
        let text = self.words(config, !talking);
        let title = format!(
            " {} {} ",
            self.name(),
            if talking { "says" } else { "thinks" }
        );
        let body = if compact {
            area
        } else {
            let block = panel(&title, colors, renderer == Renderer::Ascii);
            let inner = block.inner(area);
            screen.render_widget(block, area);
            inner
        };
        let reserved = if talking { 2 } else { 0 };
        let rows = usize::from(body.height.saturating_sub(reserved)).max(1);
        let words = wrap_dialogue(&text, usize::from(body.width));
        self.dialog_rows.set(rows);
        self.dialog_line_count.set(words.len());
        let start = if talking {
            self.dialog_page.min(words.len().saturating_sub(1))
        } else {
            0
        };
        let mut lines: Vec<Line> = words
            .into_iter()
            .skip(start)
            .take(rows)
            .map(Line::from)
            .collect();
        if talking {
            let label = [
                "Read more",
                "Feed",
                "Memory",
                "Clean",
                "Rest",
                "Medicine",
                "Back",
            ][self.menu_selected.min(6)];
            let (accept, back) = self.controller.as_ref().map_or_else(
                || ("Enter".into(), "Esc".into()),
                |pad| (tiny_button(&pad.accept), tiny_button(&pad.back)),
            );
            lines.push(
                Line::from(if body.width < 18 {
                    format!(
                        "{accept}: {}",
                        if self.menu_selected == 0 {
                            "More"
                        } else {
                            label
                        }
                    )
                } else {
                    format!("[{accept}] {label}")
                })
                .style(Style::default().fg(colors.ink(7))),
            );
            lines.push(
                Line::from(if body.width < 18 {
                    format!("{back}:Back")
                } else {
                    format!("[{back}] Back / Tab")
                })
                .style(Style::default().fg(colors.ink(13))),
            );
        } else if !compact && lines.len() < usize::from(body.height) {
            lines.push(Line::from(self.notice.clone()).style(Style::default().fg(colors.ink(13))));
        }
        screen.render_widget(
            Paragraph::new(lines).style(Style::default().fg(colors.ink(2)).bg(colors.background)),
            body,
        );
    }
    fn save_to(&self, store: &Store) -> Result<(), String> {
        if let Some(rules) = &self.habitat_rules {
            store.save_with_rules(&self.game, rules)
        } else {
            store.save(&self.game)
        }
    }

    fn compact_metadata(&self, width: u16, frame: usize) -> String {
        let short = format!(
            "{}t${}w{}",
            crate::hud::ticks(self.game.age_ticks),
            crate::hud::counter(self.game.coins),
            crate::hud::counter(self.game.wins)
        );
        let mut pages = if short.len() <= usize::from(width) {
            vec![short]
        } else {
            vec![
                format!("t{}", crate::hud::ticks(self.game.age_ticks)),
                format!("${}", crate::hud::counter(self.game.coins)),
                format!("w{}", crate::hud::counter(self.game.wins)),
            ]
        };
        if self.game.progress.hatched {
            for (index, stat) in self.game.progress.stats.iter().enumerate() {
                pages.push(format!(
                    "{}L{}v{}",
                    ["Sw", "Fl", "Ru", "Pw", "St"][index],
                    stat.level,
                    stat.value
                ));
            }
        }
        pages[frame / 28 % pages.len()].clone()
    }

    fn draw_training(
        &self,
        screen: &mut Frame,
        config: &Config,
        colors: &Colors,
        renderer: Renderer,
    ) {
        let area = if screen.area().width < 40 || screen.area().height < 14 {
            screen.area()
        } else {
            center(screen.area(), 66, 20)
        };
        screen.render_widget(Clear, area);
        let selected = self.menu_selected.min(5);
        let title = if area.width < 20 {
            format!("TRAIN {}/6", selected + 1)
        } else {
            format!(" TRAINING / {} / Lv/value ", self.name())
        };
        let block = panel(&title, colors, renderer == Renderer::Ascii);
        let inner = block.inner(area);
        screen.render_widget(block, area);
        let micro = inner.height <= 5 || inner.width < 18;
        let header_rows = if micro { 0 } else { 2 };
        let detail_rows = if micro { 1 } else { 3 };
        let [header, list, detail, controls] = Layout::vertical([
            Constraint::Length(header_rows),
            Constraint::Min(1),
            Constraint::Length(detail_rows),
            Constraint::Length(2),
        ])
        .areas(inner);
        screen.render_widget(
            Paragraph::new(format!(
                "Food -{} / Energy -{} / Happy +{}\n{} coins per level / innate grades E..A",
                config.game.training.fullness_cost,
                config.game.training.energy_cost,
                config.game.training.happiness_gain,
                config.game.training.level_coin_reward
            ))
            .style(Style::default().fg(colors.ink(7))),
            header,
        );
        let rows = usize::from(list.height);
        let start = selected.saturating_sub(rows.saturating_sub(1));
        let lines = (start..6)
            .take(rows)
            .map(|index| {
                let text = if index == 5 {
                    "Back".to_string()
                } else {
                    let stat = self.game.progress.stats[index];
                    if micro {
                        format!(
                            "{}{}/{}",
                            ["Swim", "Fly", "Run", "Pwr", "Stam"][index],
                            stat.level,
                            stat.value
                        )
                    } else {
                        format!(
                            "{}: level {} / value {} / aptitude {}",
                            crate::progression::TrainingRules::label(index),
                            stat.level,
                            stat.value,
                            stat.grade()
                        )
                    }
                };
                let text = if micro {
                    text
                } else {
                    format!("{} {text}", if index == selected { ">" } else { " " })
                };
                Line::from(text).style(if index == selected {
                    Style::default()
                        .fg(colors.ink(0))
                        .bg(colors.ink(7))
                        .add_modifier(Modifier::BOLD)
                } else {
                    Style::default().fg(colors.ink(2))
                })
            })
            .collect::<Vec<_>>();
        screen.render_widget(Paragraph::new(lines), list);
        let r = &config.game.training;
        let reason = if !self.game.progress.hatched {
            Some("Egg: wait")
        } else if self.game.health == 0 {
            Some("Pet died")
        } else if self.game.paused {
            Some("Paused: P")
        } else if self.game.sleeping {
            Some("Rest: R")
        } else if self.game.ill {
            Some("Ill: M")
        } else if self.game.challenge.is_some() {
            Some("Memory: G")
        } else if self.game.energy < r.energy_cost {
            Some("Rest: R")
        } else if self.game.fullness < r.fullness_cost {
            Some("Feed: F")
        } else if self
            .game
            .progress
            .last_train_tick
            .is_some_and(|last| self.game.age_ticks.saturating_sub(last) < r.cooldown_ticks)
        {
            Some("Wait: Esc")
        } else if selected < 5 && self.game.progress.stats[selected].level >= r.max_level {
            Some("Level cap")
        } else {
            None
        };
        let text = if selected >= 5 {
            "Keep pet".to_owned()
        } else if micro {
            if let Some(reason) = reason {
                reason.to_owned()
            } else {
                let stat = self.game.progress.stats[selected];
                format!("{}/{}XP{}", stat.xp, r.xp_needed(stat.level), stat.grade())
            }
        } else {
            let stat = self.game.progress.stats[selected];
            format!(
                "XP {} / {} | aptitude {}\n{}\n{}",
                stat.xp,
                r.xp_needed(stat.level),
                stat.grade(),
                reason.unwrap_or("Ready: training spends food and energy."),
                self.notice
            )
        };
        screen.render_widget(
            Paragraph::new(text).style(Style::default().fg(colors.ink(7))),
            detail,
        );
        let hints = self.controller.as_ref().map_or_else(
            || {
                if micro {
                    "Ent train\nEsc Back".to_owned()
                } else {
                    "Enter: train selected skill / Tab: next\nEscape: return / L: training"
                        .to_owned()
                }
            },
            |pad| {
                format!(
                    "{} {}\n{} Back",
                    tiny_button(&pad.accept),
                    if micro {
                        "train"
                    } else {
                        "train / D-pad select"
                    },
                    tiny_button(&pad.back)
                )
            },
        );
        screen.render_widget(
            Paragraph::new(hints).style(Style::default().fg(colors.ink(13))),
            controls,
        );
    }

    fn draw_habitat_menu(
        &self,
        screen: &mut Frame,
        config: &Config,
        colors: &Colors,
        renderer: Renderer,
    ) {
        let area = if screen.area().width < 40 || screen.area().height < 14 {
            screen.area()
        } else {
            center(screen.area(), 66, 26)
        };
        screen.render_widget(Clear, area);
        let block = panel(" HABITAT / ITEMS ", colors, renderer == Renderer::Ascii);
        let inner = block.inner(area);
        screen.render_widget(block, area);
        let micro = inner.width < 18 || inner.height <= 5;
        let [header, notice, list, controls] = Layout::vertical([
            Constraint::Length(if micro { 1 } else { 2 }),
            Constraint::Length(if micro { 0 } else { 1 }),
            Constraint::Min(1),
            Constraint::Length(2),
        ])
        .areas(inner);
        let rules = &config.habitat;
        let theme = rules
            .theme(self.game.habitat.theme)
            .map_or("Unknown", |t| t.name.as_str());
        screen.render_widget(
            Paragraph::new(if micro {
                if self.frame / 14 % 2 == 0 {
                    format!(
                        "{}c {}p",
                        crate::hud::counter(self.game.coins),
                        self.game.habitat.placed.count_ones()
                    )
                } else {
                    frame_label(&self.notice, inner.width, self.frame / 28)
                }
            } else {
                format!(
                    "{theme}\n{}c | {} placed",
                    self.game.coins,
                    self.game.habitat.placed.count_ones()
                )
            })
            .style(Style::default().fg(colors.ink(7))),
            header,
        );
        screen.render_widget(
            Paragraph::new(self.notice.clone()).style(Style::default().fg(colors.ink(13))),
            notice,
        );
        let mut entries = Vec::new();
        for item in &rules.items {
            let placed = self.game.habitat.placed & (1u64 << item.id) != 0;
            let owns = self
                .game
                .habitat
                .owns(item.id, self.game.wins, self.game.age_ticks, rules);
            entries.push(if placed {
                format!("HIDE {}", item.name)
            } else if owns {
                format!("PLACE {}", item.name)
            } else if item.earned() {
                format!(
                    "EARN {}w {}t {}",
                    item.reward_wins, item.reward_age, item.name
                )
            } else {
                format!("{}c BUY {}", item.price, item.name)
            });
        }
        entries.push("0c NEXT THEME".into());
        entries.push("Back".into());
        let rows = usize::from(list.height).max(1);
        let start = self.menu_selected.saturating_sub(rows - 1);
        let lines: Vec<Line> = entries
            .iter()
            .enumerate()
            .skip(start)
            .take(rows)
            .map(|(index, text)| {
                let selected = index == self.menu_selected;
                let text = if micro {
                    frame_label(text, list.width.saturating_sub(2), self.frame / 14)
                } else {
                    text.clone()
                };
                Line::from(format!("{} {text}", if selected { ">" } else { " " })).style(
                    if selected {
                        Style::default()
                            .fg(colors.ink(0))
                            .bg(colors.ink(7))
                            .add_modifier(Modifier::BOLD)
                    } else {
                        Style::default().fg(colors.ink(2))
                    },
                )
            })
            .collect();
        screen.render_widget(Paragraph::new(lines), list);
        let hints = self.controller.as_ref().map_or_else(
            || {
                if micro {
                    "Ent use\nEsc Back".into()
                } else {
                    "Enter: buy/place\nEsc: Back / Tab".into()
                }
            },
            |p| {
                if micro {
                    format!(
                        "{} use\n{} Back",
                        tiny_button(&p.accept),
                        tiny_button(&p.back)
                    )
                } else {
                    format!(
                        "{}: buy/place\n{}: Back / {}",
                        tiny_button(&p.accept),
                        tiny_button(&p.back),
                        tiny_button(&p.navigate)
                    )
                }
            },
        );
        screen.render_widget(
            Paragraph::new(hints).style(Style::default().fg(colors.ink(13))),
            controls,
        );
    }

    fn draw_roster(
        &self,
        screen: &mut Frame,
        config: &Config,
        colors: &Colors,
        renderer: Renderer,
    ) {
        let Some(character) = self
            .catalogue
            .get(self.menu_selected % self.catalogue.len().max(1))
        else {
            return;
        };
        let ascii = renderer == Renderer::Ascii;
        let area = screen.area();
        screen.render_widget(Clear, area);
        let micro = area.width < 18 || area.height <= 7;
        let title = if micro {
            format!(
                "{}/{} {}",
                self.menu_selected + 1,
                self.catalogue.len(),
                character.name
            )
        } else {
            format!(
                " ROSTER {}/{} / PREVIEW ",
                self.menu_selected + 1,
                self.catalogue.len()
            )
        };
        let block = panel(&title, colors, ascii).title_bottom(Line::from(frame_label(
            &format!("{} / {}", character.species, character.archetype),
            area.width.saturating_sub(2),
            self.frame / 14,
        )));
        let inner = block.inner(area);
        screen.render_widget(block, area);
        let compact = inner.width < 50 || inner.height < 16;
        let [header, info, room, controls] = Layout::vertical([
            Constraint::Length(if micro { 0 } else { 1 }),
            Constraint::Length(if micro {
                0
            } else if compact {
                1
            } else {
                3
            }),
            Constraint::Min(1),
            Constraint::Length(2),
        ])
        .areas(inner);
        screen.render_widget(
            Paragraph::new(format!("{} / {}", character.name, character.species))
                .style(Style::default().fg(colors.ink(7))),
            header,
        );
        let detail = if compact {
            format!("{} / {}", character.archetype, character.hobby)
        } else {
            format!(
                "{} / {}\n{}\nYour current pet is unchanged.",
                character.archetype,
                character.hobby,
                characters::thought(character, &self.game, &config.game, self.dialog_variant)
            )
        };
        screen.render_widget(
            Paragraph::new(detail)
                .wrap(Wrap { trim: true })
                .style(Style::default().fg(colors.ink(2))),
            info,
        );
        let expression = Expression::Happy;
        let appearance = Appearance {
            shape: character.shape,
            body: character.body,
            accent: character.accent,
        };
        let sprite = match self.view_mode {
            ViewMode::Pixel => Sprite::animated(
                crate::game::Stage::Adult,
                expression,
                self.sprite_style,
                self.frame,
            )
            .recolored(character.body, character.accent),
            ViewMode::Voxel => Sprite::voxel_with_appearance(
                crate::game::Stage::Adult,
                expression,
                self.sprite_style,
                self.frame,
                self.camera_yaw(((self.frame * 3) % 360) as u16),
                false,
                appearance,
            ),
        };
        screen.render_widget(
            RoomWidget {
                camera: self.camera(),
                sprite: &sprite,
                renderer,
                scale: 0,
                colors,
                expression,
                frame: self.frame,
                position: (500, 700),
                habitat: Some(HabitatView {
                    state: &self.game.habitat,
                    rules: &config.habitat,
                    wins: self.game.wins,
                    age: self.game.age_ticks,
                }),
            },
            room,
        );
        let hints = self.controller.as_ref().map_or_else(
            || {
                if controls.width < 18 {
                    "Ent new?\nEsc keep".into()
                } else if controls.width < 28 {
                    "Arrows Enter:new?\nEsc: keep pet".into()
                } else {
                    "Arrows: animal  Enter: adopt?\nEsc: keep current pet".into()
                }
            },
            |p| {
                if controls.width < 18 {
                    format!(
                        "{} new?\n{} keep",
                        tiny_button(&p.accept),
                        tiny_button(&p.back)
                    )
                } else if controls.width < 28 {
                    format!(
                        "{} {}:new?\n{}:keep",
                        p.navigate,
                        tiny_button(&p.accept),
                        tiny_button(&p.back)
                    )
                } else {
                    format!(
                        "{}: animal [{}] adopt?\n[{}] keep current",
                        p.navigate,
                        tiny_button(&p.accept),
                        tiny_button(&p.back)
                    )
                }
            },
        );
        screen.render_widget(
            Paragraph::new(hints).style(Style::default().fg(colors.ink(13))),
            controls,
        );
    }
    fn draw_reset(&self, screen: &mut Frame, colors: &Colors, ascii: bool) {
        let area = screen.area();
        if self.confirm_reset {
            let modal = center(area, 62, 7);
            screen.render_widget(Clear, modal);
            let question = self
                .adopt_character
                .and_then(|id| self.catalogue.iter().find(|character| character.id == id))
                .map_or_else(
                    || "Replace pet?".to_owned(),
                    |c| format!("Adopt {}?", c.name),
                );
            let prompt = if area.width < 40 || area.height < 14 {
                if let Some(pad) = &self.controller {
                    format!(
                        "{question}\n{}: New\n{}: Keep",
                        compact_button(&pad.accept),
                        compact_button(&pad.back)
                    )
                } else {
                    format!("{question}\nEnter/Y: New\nEsc: Keep")
                }
            } else if let Some(pad) = &self.controller {
                format!(
                    "Replace this pet with a new egg?\nYour current save changes only after confirmation.\n\n[{}] New pet    [{}] Keep current pet",
                    pad.accept, pad.back
                )
            } else {
                "Replace this pet with a new egg?\nYour current save changes only after confirmation.\n\nEnter / Y: new pet    Escape: keep current pet".into()
            };
            screen.render_widget(
                Paragraph::new(prompt)
                    .block(panel(" NEW PET? ", colors, ascii))
                    .style(Style::default().fg(colors.ink(2)).bg(colors.background))
                    .wrap(Wrap { trim: true }),
                modal,
            );
        }
    }
    fn draw_status(&self, screen: &mut Frame, area: Rect, colors: &Colors, ascii: bool) {
        let block = panel(" STATUS ", colors, ascii);
        let body = block.inner(area);
        screen.render_widget(block, area);
        let mut lines = Vec::new();
        // Stage is already displayed in the header; this panel focuses on actual needs.
        lines.push(
            Line::from(format!(
                "{} coins  /  {} wins",
                self.game.coins, self.game.wins
            ))
            .style(Style::default().fg(colors.ink(7))),
        );
        lines.push(Line::from(""));
        let width = usize::from(body.width.saturating_sub(3)).min(22);
        for (name, value, color) in [
            ("FULLNESS", self.game.fullness, 8),
            ("HAPPINESS", self.game.happiness, 5),
            ("HYGIENE", self.game.hygiene, 9),
            ("ENERGY", self.game.energy, 7),
            ("HEALTH", self.game.health, 11),
        ] {
            lines.push(
                Line::from(format!("{name:<12} {value:>3}%"))
                    .style(Style::default().fg(colors.ink(color))),
            );
            let filled = width * usize::from(value) / 100;
            let bar = format!(
                "{}{}",
                if ascii { "#" } else { "━" }.repeat(filled),
                if ascii { "." } else { "─" }.repeat(width - filled)
            );
            lines.push(Line::from(bar).style(Style::default().fg(colors.ink(color))));
        }
        lines.push(Line::from(""));
        lines.push(Line::from(if self.game.ill {
            "Medicine needed"
        } else if self.game.sleeping {
            "Resting peacefully"
        } else {
            "A little care goes far."
        }));
        screen.render_widget(
            Paragraph::new(lines).style(Style::default().fg(colors.ink(2))),
            body,
        );
    }
    fn bubble(&self, screen: &mut Frame, room: Rect, text: &str, colors: &Colors, ascii: bool) {
        let width = room.width.saturating_sub(4).min(45);
        let area = Rect::new(
            room.x + (room.width.saturating_sub(width)) / 2,
            room.y + 1,
            width,
            5.min(room.height.saturating_sub(2)),
        );
        screen.render_widget(Clear, area);
        screen.render_widget(
            Paragraph::new(text)
                .block(panel(" MEMORY ", colors, ascii))
                .style(Style::default().fg(colors.ink(7)).bg(colors.background)),
            area,
        );
    }
    fn draw_menu(
        &self,
        screen: &mut Frame,
        menu: Menu,
        config: &Config,
        colors: &Colors,
        renderer: Renderer,
    ) {
        let ascii = renderer == Renderer::Ascii;
        if menu == Menu::Talk {
            return;
        }
        if menu == Menu::Training {
            self.draw_training(screen, config, colors, renderer);
            return;
        }
        if menu == Menu::Habitat {
            self.draw_habitat_menu(screen, config, colors, renderer);
            return;
        }
        if menu == Menu::Roster {
            self.draw_roster(screen, config, colors, renderer);
            return;
        }
        if menu == Menu::Gallery {
            let expressions = [
                Expression::Idle,
                Expression::Happy,
                Expression::Sad,
                Expression::Angry,
                Expression::Surprised,
                Expression::Sleeping,
                Expression::Sick,
                Expression::Hungry,
                Expression::Playful,
                Expression::Shy,
                Expression::Excited,
                Expression::Eating,
                Expression::Cleaning,
                Expression::Healing,
                Expression::Celebrate,
                Expression::Dead,
            ];
            let stages = [
                crate::game::Stage::Egg,
                crate::game::Stage::Baby,
                crate::game::Stage::Child,
                crate::game::Stage::Adult,
                crate::game::Stage::Dead,
            ];
            let expression = expressions[self.menu_selected % expressions.len()];
            let stage = stages[self.gallery_stage % stages.len()];
            let area = screen.area();
            screen.render_widget(Clear, area);
            let micro = area.width < 18 || area.height <= 7;
            let title = if micro {
                frame_label(
                    &format!("{stage:?} {expression:?}"),
                    area.width.saturating_sub(2),
                    self.frame / 14,
                )
            } else {
                " SPRITE STUDIO / PREVIEW ".into()
            };
            let block = panel(&title, colors, ascii).title_bottom(Line::from(if micro {
                "<> mood ^v".to_string()
            } else {
                "PREVIEW / pet unchanged".to_string()
            }));
            let inner = block.inner(area);
            screen.render_widget(block, area);
            let [label, room, controls] = Layout::vertical([
                Constraint::Length(if micro { 0 } else { 2 }),
                Constraint::Min(1),
                Constraint::Length(2),
            ])
            .areas(inner);
            screen.render_widget(
                Paragraph::new(format!(
                    "{:?} / {:?} / {:?}\nPreview only. Your pet is unchanged.",
                    stage, expression, self.sprite_style
                ))
                .style(Style::default().fg(colors.ink(7))),
                label,
            );
            let sprite = match self.view_mode {
                ViewMode::Pixel => {
                    Sprite::animated(stage, expression, self.sprite_style, self.frame)
                        .recolored(self.appearance().body, self.appearance().accent)
                }
                ViewMode::Voxel => Sprite::voxel_with_appearance(
                    stage,
                    expression,
                    self.sprite_style,
                    self.frame,
                    self.camera_yaw(((self.frame * 3) % 360) as u16),
                    false,
                    self.appearance(),
                ),
            };
            screen.render_widget(
                RoomWidget {
                    camera: self.camera(),
                    sprite: &sprite,
                    renderer,
                    scale: 0,
                    colors,
                    expression,
                    frame: self.frame,
                    position: (500, 700),
                    habitat: Some(HabitatView {
                        state: &self.game.habitat,
                        rules: &config.habitat,
                        wins: self.game.wins,
                        age: self.game.age_ticks,
                    }),
                },
                room,
            );
            let hint = if micro {
                self.controller.as_ref().map_or_else(
                    || "Ent 8/16\nEsc Back".into(),
                    |pad| {
                        format!(
                            "{} 8/16\n{} Back",
                            tiny_button(&pad.accept),
                            tiny_button(&pad.back)
                        )
                    },
                )
            } else if let Some(pad) = &self.controller {
                format!(
                    "{}: reaction / life stage\n[{}] 8-bit / 16-bit   [{}] Return",
                    pad.navigate, pad.accept, pad.back
                )
            } else {
                "Left/Right: reaction   Up/Down: life stage\nEnter/Space: 8-bit / 16-bit   Esc: return".into()
            };
            screen.render_widget(
                Paragraph::new(hint).style(Style::default().fg(colors.ink(13))),
                controls,
            );
            return;
        }
        let (title, entries) = match menu {
            Menu::Home => (
                " MAIN MENU ",
                vec![
                    "Care",
                    "Games",
                    "Talk",
                    "Roster",
                    "Status",
                    "Graphics",
                    "Sprite studio",
                    "Help",
                    "Save",
                    "Quit",
                    "Habitat / items",
                    "Training / stats",
                ],
            ),
            Menu::Care => (
                " CARE ",
                vec![
                    "Feed / snack",
                    "Clean / bath",
                    "Rest / wake",
                    "Medicine",
                    "Back",
                ],
            ),
            Menu::Games => (" GAMES ", vec!["Three-arrow memory", "Back"]),
            Menu::Status => (" MIO / STATUS ", vec!["Back"]),
            Menu::Graphics => (
                " GRAPHICS ",
                vec![
                    "Voxel 3D / autonomous pet",
                    "Classic pixel sprite view",
                    "8-bit classic sprites",
                    "16-bit detailed sprites",
                    "Half-block pixels",
                    "ASCII characters",
                    "Quadrant pixels",
                    "Braille pixels",
                    "Sprite studio / reactions",
                    "HUD / Automatic",
                    "HUD / Minimal",
                    "HUD / Full",
                    "Camera / whole room",
                    "Camera / pet body",
                    "Camera / face close-up",
                    "Zoom in (+)",
                    "Zoom out (-)",
                    "Reset zoom (0)",
                    "Back",
                ],
            ),
            Menu::Help => (" HOW TO PLAY ", vec!["Back"]),
            Menu::Gallery | Menu::Talk | Menu::Roster | Menu::Habitat | Menu::Training => {
                unreachable!("special menus draw their own view")
            }
        };
        let extra = if matches!(menu, Menu::Status | Menu::Help) {
            if self.controller.is_some() && menu == Menu::Help {
                16
            } else {
                12
            }
        } else {
            4
        };
        let modal = if screen.area().width < 32 || screen.area().height < 12 {
            screen.area()
        } else {
            center(screen.area(), 64, (entries.len() + extra) as u16)
        };
        screen.render_widget(Clear, modal);
        let menu_title = if menu == Menu::Graphics {
            format!(" GRAPHICS / {:?} {}% ", self.camera_view, self.zoom_target)
        } else {
            title.to_owned()
        };
        let block = panel(&menu_title, colors, ascii);
        let body = block.inner(modal);
        screen.render_widget(block, modal);
        let mut lines = Vec::new();
        if menu == Menu::Status {
            lines.extend(
                self.summary_for_menu()
                    .lines()
                    .map(|s| Line::from(s.to_owned())),
            );
            lines.push(Line::from("Offline time is frozen. Saves stay local."));
            lines.push(Line::from(""));
        } else if menu == Menu::Help {
            if let Some(pad) = &self.controller {
                lines.push(Line::from(format!("{} / focused controller", pad.family)));
                lines.push(Line::from(format!(
                    "{} Select [{}] Choose  [{}] Back",
                    pad.navigate, pad.accept, pad.back
                )));
                lines.push(Line::from(format!(
                    "[{}] Next action. Back in the room opens Menu.",
                    pad.next
                )));
                lines.push(Line::from(pad.release.clone()));
            }
            for line in [
                "Feed, clean, rest and play to raise a happy adult.",
                "Watch three arrows, then repeat after they disappear.",
                "F Feed  G Memory  C Clean  R Rest  M Medicine",
                "P Pause  S Save  N New pet (asks first)",
                "L Training: Swim / Flight / Run / Power / Stamina.",
                "Hatch rolls name, stats and aptitude. Care funds training.",
                "Arrows / Tab select; Enter / Space activates.",
                "O / F1 Menu  I Status  V Graphics  ? Help",
                "Escape closes a menu; Q saves and quits.",
                "Menus freeze care time; animation keeps moving.",
                "Compact HUD: *=100. Tiny frames page all needs.",
                "Inputd arrows/Enter/Tab/Back operate menus and care.",
                "+ / - or mouse wheel: zoom. 0: reset. Z: camera.",
                "Room / Pet / Face cameras never steer your pet.",
                "Compact controller H- means Hold; labels match mapped buttons.",
                "Graphics choices affect this session only.",
                "",
            ] {
                lines.push(Line::from(line));
            }
        }
        if matches!(menu, Menu::Status | Menu::Help) {
            lines = lines
                .into_iter()
                .flat_map(|line| {
                    let text = line
                        .spans
                        .iter()
                        .map(|span| span.content.as_ref())
                        .collect::<String>();
                    if text.is_empty() {
                        vec![Line::from("")]
                    } else {
                        wrap_dialogue(&text, usize::from(body.width.max(1)))
                            .into_iter()
                            .map(Line::from)
                            .collect()
                    }
                })
                .collect();
        }
        for (i, entry) in entries.iter().enumerate() {
            let selected = i == self.menu_selected;
            lines.push(
                Line::from(format!(
                    " {} {} ",
                    if selected { ">" } else { " " },
                    entry
                        .chars()
                        .take(usize::from(body.width.saturating_sub(3)))
                        .collect::<String>()
                ))
                .style(if selected {
                    Style::default()
                        .fg(colors.ink(0))
                        .bg(colors.ink(7))
                        .add_modifier(Modifier::BOLD)
                } else {
                    Style::default().fg(colors.ink(2))
                }),
            );
        }
        lines.push(Line::from(""));
        let footer: Vec<Line> = if body.width < 32 {
            if let Some(pad) = &self.controller {
                vec![
                    Line::from(format!(
                        "{}: OK",
                        if body.width < 18 {
                            tiny_button(&pad.accept)
                        } else {
                            compact_button(&pad.accept)
                        }
                    )),
                    Line::from(format!(
                        "{}: Back",
                        if body.width < 18 {
                            tiny_button(&pad.back)
                        } else {
                            compact_button(&pad.back)
                        }
                    )),
                ]
            } else {
                vec![Line::from("Enter: OK"), Line::from("Esc: Back")]
            }
        } else {
            vec![Line::from(if let Some(pad) = &self.controller {
                format!(
                    "{} [{}] Choose [{}] Back [{}] Next",
                    pad.navigate, pad.accept, pad.back, pad.next
                )
            } else {
                "Arrows / Tab: select   Enter: choose   Esc: back".into()
            })]
        };
        let footer: Vec<_> = footer
            .into_iter()
            .map(|l| l.style(Style::default().fg(colors.ink(13))))
            .collect();
        lines.extend(footer.clone());
        let height = usize::from(body.height);
        if lines.len() > height && height > footer.len() {
            if matches!(menu, Menu::Status | Menu::Help) {
                let end = lines.len().saturating_sub(footer.len() + 2);
                let visible = height.saturating_sub(footer.len() + 1);
                let start = self.menu_scroll.min(end.saturating_sub(visible));
                let mut page = lines[start..(start + visible).min(end)].to_vec();
                page.push(Line::from(if body.width < 18 {
                    "> Back"
                } else {
                    "> Back / scroll"
                }));
                page.extend(footer);
                lines = page;
            } else {
                let visible = height.saturating_sub(footer.len());
                let start = self
                    .menu_selected
                    .saturating_sub(visible.saturating_sub(1))
                    .min(entries.len().saturating_sub(visible));
                let mut page = lines[start..(start + visible).min(entries.len())].to_vec();
                page.extend(footer);
                lines = page;
            }
        }
        screen.render_widget(
            Paragraph::new(lines)
                .style(Style::default().bg(colors.background))
                .wrap(Wrap { trim: true }),
            body,
        );
    }
    fn summary_for_menu(&self) -> String {
        let base = format!(
            "{}\nAge {} game ticks / {} coins / {} wins\n\nFullness {}%     Happiness {}%\nHygiene  {}%     Energy    {}%\nHealth   {}%\n\n{}{}",
            self.name(),
            self.game.age_ticks,
            self.game.coins,
            self.game.wins,
            self.game.fullness,
            self.game.happiness,
            self.game.hygiene,
            self.game.energy,
            self.game.health,
            if self.game.paused {
                "Clock paused. "
            } else {
                ""
            },
            if self.game.ill {
                "Needs medicine."
            } else {
                "Healthy."
            }
        );
        if !self.game.progress.hatched {
            return format!("{base}\nStats roll when the egg hatches.");
        }
        let stats = self
            .game
            .progress
            .stats
            .iter()
            .enumerate()
            .map(|(index, stat)| {
                format!(
                    "{}: value {} / level {} / XP {} / aptitude {}",
                    crate::progression::TrainingRules::label(index),
                    stat.value,
                    stat.level,
                    stat.xp,
                    stat.grade()
                )
            })
            .collect::<Vec<_>>()
            .join("\n");
        format!("{base}\n{stats}\nL: Training / care costs apply")
    }
}
fn frame_label(value: &str, width: u16, page: usize) -> String {
    let pages = wrap_dialogue(value, usize::from(width));
    pages
        .get(page % pages.len().max(1))
        .cloned()
        .unwrap_or_default()
}
fn tiny_button(value: &str) -> String {
    compact_button(value)
        .replace("Hold ", "H-")
        .replace("Cross", "X")
        .replace("Circle", "O")
        .replace("South", "S")
        .replace("East", "E")
        .replace("West", "W")
        .replace("North", "N")
}
fn wrap_dialogue(text: &str, width: usize) -> Vec<String> {
    if width == 0 {
        return vec![String::new()];
    }
    let mut out = Vec::new();
    let mut line = String::new();
    for word in text.split_whitespace() {
        if !line.is_empty() && line.len() + 1 + word.len() > width {
            out.push(std::mem::take(&mut line));
        }
        for chunk in word.as_bytes().chunks(width) {
            let part = String::from_utf8_lossy(chunk);
            if !line.is_empty() && line.len() + 1 + part.len() > width {
                out.push(std::mem::take(&mut line));
            }
            if !line.is_empty() {
                line.push(' ');
            }
            line.push_str(&part);
        }
    }
    if !line.is_empty() {
        out.push(line);
    }
    if out.is_empty() {
        out.push(String::new());
    }
    out
}
fn compact_button(value: &str) -> String {
    value
        .strip_prefix("Tap ")
        .unwrap_or(value)
        .replace("D-pad Up", "DU")
        .replace("D-pad Down", "DD")
        .replace("D-pad Left", "DL")
        .replace("D-pad Right", "DR")
        .replace("Square", "[]")
        .replace("Triangle", "^")
}
fn center(area: Rect, width: u16, height: u16) -> Rect {
    let width = width.min(area.width.saturating_sub(2));
    let height = height.min(area.height.saturating_sub(2));
    Rect::new(
        area.x + (area.width - width) / 2,
        area.y + (area.height - height) / 2,
        width,
        height,
    )
}
fn panel<'a>(title: &'a str, colors: &Colors, ascii: bool) -> Block<'a> {
    let mut block = Block::default()
        .title(title)
        .borders(Borders::ALL)
        .border_style(Style::default().fg(colors.ink(3)))
        .style(Style::default().fg(colors.ink(2)).bg(colors.background));
    if ascii {
        block = block.border_set(ratatui::symbols::border::Set {
            top_left: "+",
            top_right: "+",
            bottom_left: "+",
            bottom_right: "+",
            vertical_left: "|",
            vertical_right: "|",
            horizontal_top: "-",
            horizontal_bottom: "-",
        });
    }
    block
}
struct MouseCapture;
impl MouseCapture {
    fn enable() -> io::Result<Self> {
        crossterm::execute!(io::stdout(), EnableMouseCapture)?;
        Ok(Self)
    }
}
impl Drop for MouseCapture {
    fn drop(&mut self) {
        let _ = crossterm::execute!(io::stdout(), DisableMouseCapture);
    }
}
fn apply_effect(
    effect: Effect,
    ui: &mut Ui,
    store: Option<&Store>,
    last_save: &mut Instant,
) -> io::Result<bool> {
    match effect {
        Effect::Quit => {
            if let Some(s) = store {
                ui.save_to(s).map_err(io::Error::other)?;
            }
            return Ok(true);
        }
        Effect::Save => {
            if let Some(s) = store {
                ui.save_to(s).map_err(io::Error::other)?;
                ui.notice = "Saved locally.".into();
                *last_save = Instant::now();
            } else {
                ui.notice = "Sample mode: no save file is written.".into();
            }
        }
        Effect::Continue => {}
    }
    Ok(false)
}
pub fn run(
    terminal: &mut DefaultTerminal,
    config: &Config,
    renderer: Renderer,
    colors: &Colors,
    store: Option<&Store>,
    game: Game,
) -> io::Result<()> {
    let mut ui = Ui::configured(game, config);
    let mut controller = ControllerMonitor::new(config);
    let mut native = NativeDialogs::new(config);
    let _mouse = MouseCapture::enable()?;
    let mut last_tick = Instant::now();
    let mut last_save = Instant::now();
    let animation = Instant::now();
    loop {
        while let Some(reply) = native.poll() {
            match reply {
                DialogEvent::Opened => ui.native_modal = true,
                DialogEvent::Selected(choice) => {
                    ui.native_modal = false;
                    let effect = ui.apply_dialog_choice(choice, &config.game);
                    if apply_effect(effect, &mut ui, store, &mut last_save)? {
                        return Ok(());
                    }
                }
                DialogEvent::Closed => {
                    ui.native_modal = false;
                    ui.menu = None;
                }
                DialogEvent::Inline => ui.native_modal = false,
            }
            last_tick = Instant::now();
        }
        if let Some(kind) = ui.native_request.take() {
            let context = matches!(kind, NativeKind::Context);
            let mut request = ui.dialog_request(config, kind);
            request.ascii = ui.renderer.unwrap_or(renderer) == Renderer::Ascii;
            if native.request(request, context) {
                ui.native_modal = true;
                last_tick = Instant::now();
            }
        }
        let frame =
            (animation.elapsed().as_millis() / u128::from(config.animation_frame_ms)) as usize;
        ui.animate(frame);
        ui.controller = controller.poll();
        terminal
            .draw(|screen| ui.draw(screen, config, renderer, colors, frame, store.is_some()))?;
        if event::poll(Duration::from_millis(
            config.tick_ms.min(config.animation_frame_ms),
        ))? {
            let incoming = event::read()?;
            let was_paused = ui.game.paused;
            let was_overlay = ui.overlay_open();
            let old_age = ui.game.age_ticks;
            let effect = match incoming {
                Event::Key(key) => ui.handle(key, &config.game),
                Event::Mouse(mouse) if mouse.kind == MouseEventKind::ScrollUp => {
                    ui.zoom_by(true);
                    Effect::Continue
                }
                Event::Mouse(mouse) if mouse.kind == MouseEventKind::ScrollDown => {
                    ui.zoom_by(false);
                    Effect::Continue
                }
                Event::Mouse(mouse) if mouse.kind == MouseEventKind::Down(MouseButton::Right) => {
                    ui.open(Menu::Home);
                    ui.native_request = Some(NativeKind::Context);
                    Effect::Continue
                }
                Event::Mouse(mouse) if mouse.kind == MouseEventKind::Down(MouseButton::Left) => {
                    let point = (mouse.column, mouse.row).into();
                    let selected = ui
                        .care_targets
                        .borrow()
                        .iter()
                        .find(|(r, _)| r.contains(point))
                        .map(|(_, i)| *i);
                    if let Some(index) = selected {
                        ui.selected = index;
                        ui.handle(
                            KeyEvent::new(
                                crossterm::event::KeyCode::Enter,
                                crossterm::event::KeyModifiers::NONE,
                            ),
                            &config.game,
                        )
                    } else if ui.dialog_area.get().contains(point) && ui.menu == Some(Menu::Talk) {
                        ui.handle(
                            KeyEvent::new(
                                crossterm::event::KeyCode::Enter,
                                crossterm::event::KeyModifiers::NONE,
                            ),
                            &config.game,
                        )
                    } else if ui.pet_area.get().contains(point) {
                        ui.open_talk();
                        Effect::Continue
                    } else {
                        Effect::Continue
                    }
                }
                _ => Effect::Continue,
            };
            if ui.game.paused != was_paused
                || ui.overlay_open() != was_overlay
                || ui.game.age_ticks < old_age
            {
                last_tick = Instant::now();
            }
            if apply_effect(effect, &mut ui, store, &mut last_save)? {
                return Ok(());
            }
        }
        if ui.overlay_open() {
            last_tick = Instant::now();
        } else if last_tick.elapsed() >= Duration::from_secs(config.game.tick_seconds) {
            let stage = ui.game.stage(&config.game);
            ui.game.tick(&config.game);
            ui.simulate_behavior(config);
            if ui.game.stage(&config.game) != stage {
                ui.react(Expression::Surprised);
            }
            last_tick = Instant::now();
        }
        if last_save.elapsed() >= Duration::from_secs(config.autosave_seconds) {
            if let Some(s) = store {
                ui.save_to(s).map_err(io::Error::other)?;
            }
            last_save = Instant::now();
        }
    }
}

#[cfg(test)]
mod graphics_tests {
    use super::*;
    use crate::capabilities::Depth;
    use crossterm::event::{KeyCode, KeyModifiers};
    use ratatui::{Terminal, backend::TestBackend, style::Color};

    fn key(code: KeyCode) -> KeyEvent {
        KeyEvent::new(code, KeyModifiers::NONE)
    }
    fn pet() -> (Config, Ui) {
        let config = Config::sample().unwrap();
        let mut game = Game::new(&config.game, 7);
        for _ in 0..config.game.hatch_ticks {
            game.tick(&config.game);
        }
        let ui = Ui::configured(game, &config);
        (config, ui)
    }
    #[test]
    fn menus_navigate_back_and_do_not_activate_on_release_or_repeat() {
        let (config, mut ui) = pet();
        let saved = ui.game.clone();
        ui.handle(key(KeyCode::Char('o')), &config.game);
        assert_eq!(ui.menu, Some(Menu::Home));
        ui.handle(key(KeyCode::Down), &config.game);
        assert_eq!(ui.menu_selected, 1);
        let mut accept = key(KeyCode::Enter);
        accept.kind = KeyEventKind::Release;
        ui.handle(accept, &config.game);
        assert_eq!(ui.menu, Some(Menu::Home));
        accept.kind = KeyEventKind::Repeat;
        ui.handle(accept, &config.game);
        assert_eq!(ui.menu, Some(Menu::Home));
        ui.handle(key(KeyCode::Enter), &config.game);
        assert_eq!(ui.menu, Some(Menu::Games));
        ui.handle(key(KeyCode::Esc), &config.game);
        assert_eq!(ui.menu, None);
        assert_eq!(ui.game, saved);
    }
    #[test]
    fn visual_choices_and_reactions_are_separate_from_saved_state() {
        let (config, mut ui) = pet();
        let saved = ui.game.clone();
        ui.handle(key(KeyCode::Char('v')), &config.game);
        ui.menu_selected = 2;
        ui.handle(key(KeyCode::Enter), &config.game);
        assert_eq!(ui.sprite_style, SpriteStyle::EightBit);
        assert_eq!(ui.game, saved);
        ui.handle(key(KeyCode::Down), &config.game);
        ui.handle(key(KeyCode::Enter), &config.game);
        assert_eq!(ui.sprite_style, SpriteStyle::SixteenBit);
        ui.handle(key(KeyCode::Esc), &config.game);
        ui.animate(10);
        ui.handle(key(KeyCode::Char('f')), &config.game);
        assert_eq!(ui.expression(&config.game, 11), Expression::Eating);
        assert_ne!(
            ui.expression(&config.game, 10 + config.reaction_frames + 1),
            Expression::Eating
        );
        ui.game.paused = true;
        let frozen = ui.game.clone();
        ui.handle(key(KeyCode::Char('c')), &config.game);
        assert_eq!(ui.game, frozen);
    }
    #[test]
    fn graphical_frame_fits_documented_sizes_and_tiny_quit_survives() {
        let (mut config, mut ui) = pet();
        config.hud_mode = HudMode::Full;
        ui.hud_mode = HudMode::Full;
        let colors = Colors::from_config(&config, Depth::Indexed).unwrap();
        for (width, height) in [
            (80, 24),
            (66, 20),
            (120, 40),
            (160, 60),
            (130, 28),
            (34, 12),
            (12, 3),
            (1, 1),
        ] {
            let mut terminal = Terminal::new(TestBackend::new(width, height)).unwrap();
            terminal
                .draw(|f| ui.draw(f, &config, Renderer::HalfBlock, &colors, 4, false))
                .unwrap();
            let buffer = terminal.backend().buffer();
            assert_eq!(buffer.area.width, width);
            if width >= 66 {
                let text: String = buffer.content.iter().map(|c| c.symbol()).collect();
                assert!(!text.contains("Terminal too small"));
                assert!(text.contains("PET ROOM"));
                assert!(text.contains("QUICK CARE"));
                let colored = buffer
                    .content
                    .iter()
                    .filter(|c| c.bg != Color::Reset)
                    .count();
                assert!(colored > usize::from(width) * usize::from(height) / 2);
            }
            assert_eq!(
                ui.handle(key(KeyCode::Char('q')), &config.game),
                Effect::Quit
            );
        }
    }
    #[test]
    fn ascii_graphics_and_menus_stay_ascii_when_requested() {
        let (config, mut ui) = pet();
        let colors = Colors::from_config(&config, Depth::Mono).unwrap();
        for menu in [
            None,
            Some(Menu::Home),
            Some(Menu::Care),
            Some(Menu::Graphics),
            Some(Menu::Help),
            Some(Menu::Status),
        ] {
            ui.menu = menu;
            let mut terminal = Terminal::new(TestBackend::new(80, 24)).unwrap();
            terminal
                .draw(|f| ui.draw(f, &config, Renderer::Ascii, &colors, 5, false))
                .unwrap();
            assert!(
                terminal
                    .backend()
                    .buffer()
                    .content
                    .iter()
                    .all(|c| c.symbol().is_ascii())
            );
        }
    }
    #[test]
    fn visible_save_quit_and_sprite_studio_match_their_menu_actions() {
        let (config, mut ui) = pet();
        ui.open(Menu::Home);
        ui.menu_selected = 6;
        assert_eq!(
            ui.handle(key(KeyCode::Enter), &config.game),
            Effect::Continue
        );
        assert_eq!(ui.menu, Some(Menu::Gallery));
        let saved = ui.game.clone();
        for code in [
            KeyCode::Right,
            KeyCode::Down,
            KeyCode::Enter,
            KeyCode::Char('f'),
        ] {
            ui.handle(key(code), &config.game);
        }
        assert_eq!(ui.game, saved);
        ui.open(Menu::Home);
        ui.menu_selected = 8;
        assert_eq!(ui.handle(key(KeyCode::Enter), &config.game), Effect::Save);
        ui.open(Menu::Home);
        ui.menu_selected = 9;
        let colors = Colors::from_config(&config, Depth::Indexed).unwrap();
        let mut terminal = Terminal::new(TestBackend::new(80, 24)).unwrap();
        terminal
            .draw(|f| ui.draw(f, &config, Renderer::HalfBlock, &colors, 0, false))
            .unwrap();
        let text: String = terminal
            .backend()
            .buffer()
            .content
            .iter()
            .map(|c| c.symbol())
            .collect();
        assert!(text.contains("Sprite studio"));
        assert!(text.contains("> Quit"));
        assert_eq!(ui.handle(key(KeyCode::Enter), &config.game), Effect::Quit);
    }
    #[test]
    fn blocked_care_has_no_success_reaction_and_tiny_reset_is_safe() {
        let (config, _) = pet();
        let mut ui = Ui::configured(Game::new(&config.game, 1), &config);
        ui.care(Care::Clean, &config.game);
        assert!(ui.reaction.is_none());
        ui.game.age_ticks = config.game.hatch_ticks;
        ui.care(Care::Play, &config.game);
        assert!(ui.game.challenge.is_some());
        ui.reaction = None;
        ui.care(Care::Clean, &config.game);
        assert!(ui.reaction.is_none());
        ui.game.challenge = None;
        let before = ui.game.clone();
        let colors = Colors::from_config(&config, Depth::Mono).unwrap();
        for (width, height) in [(12, 3), (19, 7), (1, 1)] {
            let mut terminal = Terminal::new(TestBackend::new(width, height)).unwrap();
            terminal
                .draw(|f| ui.draw(f, &config, Renderer::Ascii, &colors, 0, false))
                .unwrap();
            ui.handle(key(KeyCode::Char('n')), &config.game);
            ui.handle(key(KeyCode::Enter), &config.game);
            assert!(!ui.confirm_reset);
            assert_eq!(ui.game, before);
        }
        ui.open(Menu::Gallery);
        assert_eq!(
            ui.handle(key(KeyCode::Char('s')), &config.game),
            Effect::Save
        );
        ui.open(Menu::Gallery);
        let paused = ui.game.paused;
        ui.handle(key(KeyCode::Char('p')), &config.game);
        assert_ne!(ui.game.paused, paused);
    }
    #[test]
    fn focused_controller_hints_follow_each_ui_context_and_keyboard_fallback() {
        let (config, mut ui) = pet();
        ui.hud_mode = HudMode::Full;
        ui.controller = Some(ControllerPrompts {
            family: "DualSense".into(),
            navigate: "D-pad".into(),
            accept: "Cross".into(),
            back: "Circle".into(),
            next: "Square".into(),
            release: "Hold L3+R3".into(),
            quit: Some("Hold Circle".into()),
        });
        let colors = Colors::from_config(&config, Depth::Indexed).unwrap();
        let mut terminal = Terminal::new(TestBackend::new(100, 30)).unwrap();
        let render = |ui: &Ui, terminal: &mut Terminal<TestBackend>| {
            terminal
                .draw(|f| ui.draw(f, &config, Renderer::HalfBlock, &colors, 0, false))
                .unwrap();
            terminal
                .backend()
                .buffer()
                .content
                .iter()
                .map(|c| c.symbol())
                .collect::<String>()
        };
        let text = render(&ui, &mut terminal);
        assert!(text.contains("DualSense / CONTROLLER"));
        assert!(text.contains("[Cross] Feed"));
        assert!(text.contains("[Circle] Menu"));
        assert!(!text.contains("[F] Feed"));
        assert_eq!(ui.handle(key(KeyCode::Esc), &config.game), Effect::Continue);
        assert_eq!(ui.menu, Some(Menu::Home));
        let text = render(&ui, &mut terminal);
        assert!(text.contains("[Cross] Choose"));
        assert!(text.contains("[Circle] Back"));
        ui.controller.as_mut().unwrap().navigate = "Shoulders".into();
        ui.open(Menu::Habitat);
        let text = render(&ui, &mut terminal);
        assert!(text.contains("X: buy/place"));
        assert!(text.contains("O: Back / Shoulders"));
        assert!(!text.contains("D-pad"));
        ui.menu = None;
        ui.ask_reset();
        let text = render(&ui, &mut terminal);
        assert!(text.contains("[Cross] New pet"));
        assert!(text.contains("[Circle] Keep"));
        ui.handle(key(KeyCode::Esc), &config.game);
        ui.controller = None;
        let text = render(&ui, &mut terminal);
        assert!(text.contains("[F] Feed"));
        assert!(text.contains("O Menu"));
        assert_eq!(ui.handle(key(KeyCode::Esc), &config.game), Effect::Quit);
    }
    #[test]
    fn autonomous_rng_wandering_never_follows_menu_controls_or_changes_saved_game() {
        let (mut config, mut ui) = pet();
        config.wander_chance = 100;
        config.wander_interval_ticks = 1;
        let saved = ui.game.clone();
        let initial = ui.position;
        for code in [
            KeyCode::Left,
            KeyCode::Right,
            KeyCode::Up,
            KeyCode::Down,
            KeyCode::Tab,
        ] {
            ui.handle(key(code), &config.game);
        }
        assert_eq!(ui.position, initial);
        assert_eq!(ui.destination, initial);
        ui.simulate_behavior(&config);
        assert_ne!(ui.destination, initial);
        ui.animate(8);
        assert_ne!(ui.position, initial);
        assert_eq!(ui.game, saved);
        let frozen = ui.position;
        ui.game.paused = true;
        ui.animate(20);
        assert_eq!(ui.position, frozen);
        ui.game.paused = false;
        ui.open(Menu::Home);
        ui.animate(30);
        assert_eq!(ui.position, frozen);
        ui.menu = None;
        ui.game.sleeping = true;
        ui.animate(40);
        assert_eq!(ui.position, frozen);
        let mut egg = Ui::configured(Game::new(&config.game, 1), &config);
        egg.simulate_behavior(&config);
        egg.animate(50);
        assert_eq!(egg.position, (500, 700));
    }

    #[test]
    fn minimal_hud_keeps_every_menu_choice_visible_and_reset_prompt_readable() {
        let (config, mut ui) = pet();
        ui.hud_mode = HudMode::Minimal;
        let colors = Colors::from_config(&config, Depth::Mono).unwrap();
        for (width, height) in [(20, 8), (28, 10), (40, 12), (60, 16)] {
            let mut terminal = Terminal::new(TestBackend::new(width, height)).unwrap();
            ui.menu = None;
            terminal
                .draw(|f| ui.draw(f, &config, Renderer::Ascii, &colors, 0, false))
                .unwrap();
            let text: String = terminal
                .backend()
                .buffer()
                .content
                .iter()
                .map(|c| c.symbol())
                .collect();
            assert!(text.contains("[Enter]"));
            assert!(!text.contains("Terminal too small"));
            for index in 0..10 {
                ui.open(Menu::Home);
                ui.menu_selected = index;
                terminal
                    .draw(|f| ui.draw(f, &config, Renderer::Ascii, &colors, 0, false))
                    .unwrap();
                let text: String = terminal
                    .backend()
                    .buffer()
                    .content
                    .iter()
                    .map(|c| c.symbol())
                    .collect();
                let label = [
                    "Care",
                    "Games",
                    "Talk",
                    "Roster",
                    "Status",
                    "Graphics",
                    "Sprite studio",
                    "Help",
                    "Save",
                    "Quit",
                ][index];
                assert!(text.contains(label), "{width}x{height} {label}: {text}");
                assert!(text.contains('>'));
            }
            ui.menu = None;
            ui.handle(key(KeyCode::Char('n')), &config.game);
            terminal
                .draw(|f| ui.draw(f, &config, Renderer::Ascii, &colors, 0, false))
                .unwrap();
            let text: String = terminal
                .backend()
                .buffer()
                .content
                .iter()
                .map(|c| c.symbol())
                .collect();
            assert!(text.contains("Enter/Y: New") || text.contains("Enter / Y: new pet"));
            assert!(text.contains("Esc: Keep") || text.contains("Escape: keep current pet"));
            let saved = ui.game.clone();
            ui.handle(key(KeyCode::Esc), &config.game);
            assert_eq!(ui.game, saved);
        }
    }
    #[test]
    fn compact_controller_prompts_fit_real_tap_and_remapped_hold_labels() {
        let (config, mut ui) = pet();
        ui.hud_mode = HudMode::Minimal;
        let colors = Colors::from_config(&config, Depth::Mono).unwrap();
        for (accept, back) in [
            ("Tap South", "Tap East"),
            ("Tap Cross", "Tap Circle"),
            ("Hold D-pad Left", "Hold D-pad Right"),
        ] {
            ui.controller = Some(ControllerPrompts {
                family: "Controller".into(),
                navigate: "D-pad".into(),
                accept: accept.into(),
                back: back.into(),
                next: "Tap West".into(),
                release: "Hold L3+R3".into(),
                quit: Some("Hold East".into()),
            });
            for (width, height) in [(20, 8), (28, 10)] {
                let mut terminal = Terminal::new(TestBackend::new(width, height)).unwrap();
                ui.open(Menu::Home);
                ui.menu_selected = 9;
                terminal
                    .draw(|f| ui.draw(f, &config, Renderer::Ascii, &colors, 0, false))
                    .unwrap();
                let text: String = terminal
                    .backend()
                    .buffer()
                    .content
                    .iter()
                    .map(|c| c.symbol())
                    .collect();
                assert!(text.contains("Quit"));
                assert!(text.contains(&format!("{}: OK", compact_button(accept))));
                assert!(text.contains(&format!("{}: Back", compact_button(back))));
            }
        }
    }
    #[test]
    fn roster_is_read_only_until_adoption_is_confirmed_and_resets_presentation() {
        let (config, mut ui) = pet();
        let saved = ui.game.clone();
        let colors = Colors::from_config(&config, Depth::Indexed).unwrap();
        let mut terminal = Terminal::new(TestBackend::new(40, 12)).unwrap();
        for index in 0..16 {
            ui.open(Menu::Roster);
            ui.menu_selected = index;
            terminal
                .draw(|f| ui.draw(f, &config, Renderer::HalfBlock, &colors, index, false))
                .unwrap();
            let text: String = terminal
                .backend()
                .buffer()
                .content
                .iter()
                .map(|c| c.symbol())
                .collect();
            assert!(text.contains(&config.characters[index].name));
            assert_eq!(ui.game, saved);
        }
        ui.destination = (900, 900);
        ui.mobile_stage = true;
        ui.open(Menu::Roster);
        ui.menu_selected = 15;
        ui.handle(key(KeyCode::Enter), &config.game);
        assert!(ui.confirm_reset);
        assert_eq!(ui.game, saved);
        ui.handle(key(KeyCode::Esc), &config.game);
        assert_eq!(ui.game, saved);
        ui.open(Menu::Roster);
        ui.menu_selected = 15;
        ui.handle(key(KeyCode::Enter), &config.game);
        ui.handle(key(KeyCode::Enter), &config.game);
        assert_eq!(ui.game.character, 15);
        assert_eq!(ui.game.age_ticks, 0);
        let position = ui.position;
        ui.animate(100);
        assert_eq!(ui.position, position);
        assert_eq!(ui.destination, position);
        assert!(!ui.mobile_stage);
    }
    #[test]
    fn inline_dialogue_pages_without_mutating_pet_and_compact_memory_shows_all_tokens() {
        let (config, mut ui) = pet();
        ui.hud_mode = HudMode::Minimal;
        ui.open(Menu::Talk);
        ui.native_request = None;
        let saved = ui.game.clone();
        let colors = Colors::from_config(&config, Depth::Mono).unwrap();
        let mut terminal = Terminal::new(TestBackend::new(20, 8)).unwrap();
        terminal
            .draw(|f| ui.draw(f, &config, Renderer::Ascii, &colors, 0, false))
            .unwrap();
        ui.handle(key(KeyCode::Enter), &config.game);
        assert_eq!(ui.game, saved);
        ui.menu = None;
        ui.game.care(Care::Play, &config.game);
        let challenge = ui.game.challenge.as_mut().unwrap();
        challenge.pattern = [Direction::Right; 3];
        challenge.reveal = 2;
        terminal
            .draw(|f| ui.draw(f, &config, Renderer::Ascii, &colors, 0, false))
            .unwrap();
        let text: String = terminal
            .backend()
            .buffer()
            .content
            .iter()
            .map(|c| c.symbol())
            .collect();
        assert!(text.contains("R R R"), "{text}");
    }
    #[test]
    fn pending_reset_is_cancelled_on_micro_resize_and_cannot_replace_pet() {
        let (config, mut ui) = pet();
        let saved = ui.game.clone();
        ui.viewport.set((18, 6));
        ui.ask_reset();
        assert!(ui.confirm_reset);
        ui.viewport.set((10, 4));
        ui.handle(key(KeyCode::Enter), &config.game);
        assert!(!ui.confirm_reset);
        assert!(ui.adopt_character.is_none());
        assert_eq!(ui.game, saved);
        ui.handle(key(KeyCode::Char('n')), &config.game);
        ui.handle(key(KeyCode::Enter), &config.game);
        assert_eq!(ui.game, saved);
    }
    #[test]
    fn micro_menu_keeps_selected_option_and_both_navigation_prompts() {
        let (config, mut ui) = pet();
        let colors = Colors::from_config(&config, Depth::Mono).unwrap();
        let mut terminal = Terminal::new(TestBackend::new(12, 6)).unwrap();
        ui.open(Menu::Home);
        ui.menu_selected = 9;
        terminal
            .draw(|f| ui.draw(f, &config, Renderer::Ascii, &colors, 0, false))
            .unwrap();
        let text: String = terminal
            .backend()
            .buffer()
            .content
            .iter()
            .map(|c| c.symbol())
            .collect();
        assert!(text.contains("Quit"), "{text}");
        assert!(text.contains("Enter: OK"), "{text}");
        assert!(text.contains("Esc: Back"), "{text}");
    }
    #[test]
    fn camera_controls_interpolate_without_steering_or_mutating_pet() {
        let (config, mut ui) = pet();
        let saved = ui.game.clone();
        let position = ui.position;
        let yaw = ui.yaw;
        ui.animate(10);
        ui.handle(key(KeyCode::Char('+')), &config.game);
        assert_eq!(ui.zoom_target, 125);
        assert_eq!(ui.zoom_percent, 100);
        ui.animate(14);
        assert!(ui.zoom_percent > 100 && ui.zoom_percent < 125);
        ui.animate(18);
        assert_eq!(ui.zoom_percent, 125);
        ui.handle(key(KeyCode::Char('0')), &config.game);
        ui.animate(26);
        assert_eq!(ui.zoom_percent, 100);
        ui.handle(key(KeyCode::Char('z')), &config.game);
        assert_eq!(ui.camera_view, CameraView::Face);
        assert_eq!(ui.camera_from_view, CameraView::Pet);
        ui.animate(30);
        assert!(ui.camera_blend > 0 && ui.camera_blend < 100);
        ui.animate(34);
        assert_eq!(ui.camera_blend, 100);
        for _ in 0..50 {
            ui.handle(key(KeyCode::Char('+')), &config.game);
        }
        assert_eq!(ui.zoom_target, 400);
        for _ in 0..100 {
            ui.handle(key(KeyCode::Char('-')), &config.game);
        }
        assert_eq!(ui.zoom_target, 50);
        let zoom = ui.zoom_target;
        let mut released = key(KeyCode::Char('+'));
        released.kind = KeyEventKind::Release;
        ui.handle(released, &config.game);
        released.kind = KeyEventKind::Repeat;
        ui.handle(released, &config.game);
        assert_eq!(ui.zoom_target, zoom);
        assert_eq!(ui.game, saved);
        assert_eq!(ui.position, position);
        assert_eq!(ui.yaw, yaw);
    }
    #[test]
    fn focused_controller_can_choose_camera_and_zoom_in_graphics_menu() {
        let (config, mut ui) = pet();
        let saved = ui.game.clone();
        ui.open(Menu::Graphics);
        ui.menu_selected = 14;
        ui.handle(key(KeyCode::Enter), &config.game);
        assert_eq!(ui.camera_view, CameraView::Face);
        ui.menu_selected = 15;
        ui.handle(key(KeyCode::Enter), &config.game);
        assert_eq!(ui.zoom_target, 125);
        ui.menu_selected = 16;
        ui.handle(key(KeyCode::Enter), &config.game);
        assert_eq!(ui.zoom_target, 100);
        ui.menu_selected = 18;
        ui.handle(key(KeyCode::Enter), &config.game);
        assert!(ui.menu.is_none());
        assert_eq!(ui.game, saved);
    }

    #[test]
    fn habitat_menu_buys_places_and_hides_once_without_steering_or_repeated_charges() {
        let (config, mut ui) = pet();
        ui.game.paused = true;
        let before = ui.game.clone();
        let position = ui.position;
        let item = &config.habitat.items[0];
        assert!(!item.earned());
        ui.handle(key(KeyCode::Char('h')), &config.game);
        assert_eq!(ui.menu, Some(Menu::Habitat));
        ui.handle(key(KeyCode::Enter), &config.game);
        assert_eq!(ui.game.coins, before.coins - item.price);
        assert_ne!(ui.game.habitat.placed & (1u64 << item.id), 0);
        let after_buy = ui.game.clone();
        let mut repeat = key(KeyCode::Enter);
        repeat.kind = KeyEventKind::Repeat;
        ui.handle(repeat, &config.game);
        assert_eq!(ui.game, after_buy);
        ui.handle(key(KeyCode::Enter), &config.game);
        assert_eq!(ui.game.habitat.placed & (1u64 << item.id), 0);
        assert_eq!(ui.game.coins, after_buy.coins);
        assert_eq!(ui.position, position);
        let colors = Colors::from_config(&config, Depth::Mono).unwrap();
        let mut terminal = Terminal::new(TestBackend::new(20, 8)).unwrap();
        terminal
            .draw(|f| ui.draw(f, &config, Renderer::Ascii, &colors, 0, false))
            .unwrap();
        let text: String = terminal
            .backend()
            .buffer()
            .content
            .iter()
            .map(|c| c.symbol())
            .collect();
        assert!(text.contains("buy/place"), "{text}");
        assert!(text.contains("Back / Tab"), "{text}");
    }

    #[test]
    fn minimal_hud_retains_ascii_face_and_inline_dialogue_below_a_tiny_pet() {
        let (config, mut ui) = pet();
        ui.hud_mode = HudMode::Minimal;
        ui.game.paused = true;
        let saved = ui.game.clone();
        let face = ui.expression(&config.game, 0).kaomoji(0);
        let colors = Colors::from_config(&config, Depth::Mono).unwrap();
        let mut terminal = Terminal::new(TestBackend::new(20, 8)).unwrap();
        terminal
            .draw(|f| ui.draw(f, &config, Renderer::Ascii, &colors, 0, false))
            .unwrap();
        let text: String = terminal
            .backend()
            .buffer()
            .content
            .iter()
            .map(|c| c.symbol())
            .collect();
        assert!(text.contains(face), "{text}");
        ui.open(Menu::Talk);
        ui.native_request = None;
        terminal
            .draw(|f| ui.draw(f, &config, Renderer::Ascii, &colors, 0, false))
            .unwrap();
        let text: String = terminal
            .backend()
            .buffer()
            .content
            .iter()
            .map(|c| c.symbol())
            .collect();
        assert!(text.contains("Read more"), "{text}");
        assert!(text.contains("Back / Tab"), "{text}");
        assert_eq!(ui.game, saved);
    }

    #[test]
    fn tiny_roster_exposes_adoption_and_unordered_catalogue_confirms_selected_identity() {
        let (config, mut ui) = pet();
        let saved = ui.game.clone();
        ui.catalogue.reverse();
        ui.open(Menu::Roster);
        let colors = Colors::from_config(&config, Depth::Mono).unwrap();
        let mut terminal = Terminal::new(TestBackend::new(20, 8)).unwrap();
        terminal
            .draw(|f| ui.draw(f, &config, Renderer::Ascii, &colors, 0, false))
            .unwrap();
        let text: String = terminal
            .backend()
            .buffer()
            .content
            .iter()
            .map(|c| c.symbol())
            .collect();
        assert!(text.contains("Enter:new?"), "{text}");
        assert!(text.contains("Esc: keep pet"), "{text}");
        ui.handle(key(KeyCode::Enter), &config.game);
        terminal
            .draw(|f| ui.draw(f, &config, Renderer::Ascii, &colors, 0, false))
            .unwrap();
        let text: String = terminal
            .backend()
            .buffer()
            .content
            .iter()
            .map(|c| c.symbol())
            .collect();
        assert!(text.contains("Adopt Iwa?"), "{text}");
        assert!(!text.contains("Adopt Mio?"), "{text}");
        ui.handle(key(KeyCode::Esc), &config.game);
        assert_eq!(ui.game, saved);
    }
    #[test]
    fn native_dialogue_hides_duplicate_inline_menu_without_hiding_the_pet_view() {
        let (config, mut ui) = pet();
        ui.hud_mode = HudMode::Full;
        ui.open(Menu::Home);
        ui.native_modal = true;
        let colors = Colors::from_config(&config, Depth::Indexed).unwrap();
        let mut terminal = Terminal::new(TestBackend::new(100, 36)).unwrap();
        terminal
            .draw(|f| ui.draw(f, &config, Renderer::HalfBlock, &colors, 0, false))
            .unwrap();
        let text: String = terminal
            .backend()
            .buffer()
            .content
            .iter()
            .map(|c| c.symbol())
            .collect();
        assert!(!text.contains("MAIN MENU"));
        assert!(text.contains("ROOM"));
        assert!(text.contains("thinks"));
        ui.open(Menu::Talk);
        terminal
            .draw(|f| ui.draw(f, &config, Renderer::HalfBlock, &colors, 0, false))
            .unwrap();
        let text: String = terminal
            .backend()
            .buffer()
            .content
            .iter()
            .map(|c| c.symbol())
            .collect();
        assert!(text.contains("thinks"));
        assert!(!text.contains(" says "));
        assert!(!text.contains("Back / Tab"));
        ui.menu = Some(Menu::Home);
        ui.native_modal = false;
        terminal
            .draw(|f| ui.draw(f, &config, Renderer::HalfBlock, &colors, 0, false))
            .unwrap();
        let text: String = terminal
            .backend()
            .buffer()
            .content
            .iter()
            .map(|c| c.symbol())
            .collect();
        assert!(text.contains("MAIN MENU"));
    }
}

#[cfg(test)]
mod progression_ui_tests {
    use super::*;
    use crate::capabilities::Depth;
    use crossterm::event::{KeyCode, KeyModifiers};
    use ratatui::{Terminal, backend::TestBackend};
    fn key(code: KeyCode) -> KeyEvent {
        KeyEvent::new(code, KeyModifiers::NONE)
    }
    fn pet() -> (Config, Ui) {
        let config = Config::sample().unwrap();
        let mut game = Game::new(&config.game, 741);
        for _ in 0..config.game.hatch_ticks {
            game.tick(&config.game);
        }
        (config.clone(), Ui::configured(game, &config))
    }
    fn draw(ui: &Ui, config: &Config, width: u16, height: u16, frame: usize) -> String {
        let colors = Colors::from_config(config, Depth::Indexed).unwrap();
        let mut terminal = Terminal::new(TestBackend::new(width, height)).unwrap();
        terminal
            .draw(|f| ui.draw(f, config, Renderer::Ascii, &colors, frame, false))
            .unwrap();
        terminal
            .backend()
            .buffer()
            .content
            .iter()
            .map(|c| c.symbol())
            .collect()
    }
    #[test]
    fn training_is_navigable_at_watch_size_and_uses_real_game_costs_and_cooldown() {
        let (config, mut ui) = pet();
        let before = ui.game.clone();
        draw(&ui, &config, 12, 6, 0);
        assert_eq!(
            ui.handle(key(KeyCode::Char('l')), &config.game),
            Effect::Continue
        );
        assert_eq!(ui.menu, Some(Menu::Training));
        let text = draw(&ui, &config, 12, 6, 0);
        assert!(
            text.contains("Ent train") && text.contains("Esc Back"),
            "{text}"
        );
        assert_eq!(ui.game, before);
        ui.handle(key(KeyCode::Enter), &config.game);
        assert_eq!(ui.game.progress.total_training, 1);
        assert_eq!(
            ui.game.fullness,
            before.fullness - config.game.training.fullness_cost
        );
        assert_eq!(
            ui.game.energy,
            before.energy - config.game.training.energy_cost
        );
        assert!(ui.game.progress.stats[0].xp > 0 || ui.game.progress.stats[0].level > 0);
        assert_eq!(ui.game.age_ticks, before.age_ticks);
        let trained = ui.game.clone();
        ui.handle(key(KeyCode::Enter), &config.game);
        assert_eq!(
            ui.game, trained,
            "same-tick training refusal must preserve all state/RNG"
        );
        ui.handle(key(KeyCode::Esc), &config.game);
        assert_eq!(ui.menu, None);
    }
    #[test]
    fn generated_name_palette_and_stat_pages_are_stable_read_only_presentation() {
        let (config, ui) = pet();
        assert_eq!(ui.name(), ui.game.progress.name);
        assert!(!ui.name().is_empty());
        for character in &ui.catalogue {
            assert_eq!(
                (character.body, character.accent),
                config
                    .game
                    .training
                    .palette(character.shape, &character.archetype)
                    .unwrap()
            );
        }
        let before = ui.game.clone();
        let pages = (0..6)
            .map(|page| ui.compact_metadata(10, page * 28))
            .collect::<Vec<_>>();
        assert!(pages.iter().all(|page| page.len() <= 10));
        for label in ["SwL", "FlL", "RuL", "PwL", "StL"] {
            assert!(pages.iter().any(|page| page.contains(label)), "{pages:?}");
        }
        let status = ui.summary_for_menu();
        for label in ["Swim", "Flight", "Run", "Power", "Stamina"] {
            assert!(status.contains(label));
        }
        assert_eq!(ui.game, before);
        let mut legacy = ui.game.clone();
        legacy.progress = crate::progression::Progress::legacy(legacy.visual_seed());
        legacy.prepare_progress(&config.game).unwrap();
        let legacy_ui = Ui::configured(legacy, &config);
        assert_eq!(
            legacy_ui.name(),
            legacy_ui.current_character().unwrap().name
        );
    }
    #[test]
    fn watch_roster_studio_and_habitat_keep_scene_choices_and_explicit_back() {
        let (config, mut ui) = pet();
        let before = ui.game.clone();
        for menu in [Menu::Roster, Menu::Gallery, Menu::Habitat] {
            ui.open(menu);
            let text = draw(&ui, &config, 12, 6, 0);
            assert!(
                text.contains("Ent") && text.contains("Esc"),
                "{menu:?}: {text}"
            );
            if menu == Menu::Habitat {
                assert!(text.contains(">"), "{text}");
            } else {
                assert!(
                    text.contains("(^_^)") || text.contains("(o_o)"),
                    "{menu:?}: face scene missing: {text}"
                );
            }
            ui.handle(key(KeyCode::Esc), &config.game);
            assert_eq!(ui.menu, None);
        }
        assert_eq!(ui.game, before);
    }
    #[test]
    fn held_mapped_controller_buttons_remain_legible_with_back_on_its_own_row() {
        let (config, mut ui) = pet();
        ui.controller = Some(ControllerPrompts {
            family: "Test".into(),
            navigate: "Hold D-pad Right / Tap Triangle".into(),
            accept: "Hold D-pad Right".into(),
            back: "Hold Triangle".into(),
            next: "Tap Square".into(),
            release: "Hold L3+R3".into(),
            quit: None,
        });
        ui.hud_mode = HudMode::Minimal;
        let text = draw(&ui, &config, 12, 6, 0);
        assert!(
            text.contains("H-DR:Feed") && text.contains("H-^:Menu"),
            "{text}"
        );
        assert!(
            !text.contains("D-pad Right /"),
            "the long controller map belongs in paginated Help"
        );
        let before = ui.game.clone();
        ui.open(Menu::Home);
        let text = draw(&ui, &config, 12, 6, 0);
        assert!(
            text.contains("H-DR: OK") && text.contains("H-^: Back"),
            "{text}"
        );
        assert_eq!(ui.game, before);
    }
}
