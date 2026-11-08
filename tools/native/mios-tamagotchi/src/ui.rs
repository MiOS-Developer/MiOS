// AI-hint: Full-viewport pet room, responsive framed menus and animated care reactions; schema-1 game state stays independent of presentation.
// AI-related: sprite.rs, render.rs, input.rs, config.rs, specs/tamagotchi-tui
use crate::{
    capabilities::Renderer,
    characters::{self, Character},
    config::{Config, HudMode, ViewMode},
    dialogs::{Choice, DialogEvent, DialogRequest, NativeDialogs},
    game::{Care, Direction, Game, Rules, Stage},
    genetics::MonCode,
    input::{action, Action, ControllerMonitor, ControllerPrompts},
    render::{Camera, CameraView, Colors, HabitatView, RoomWidget},
    save::Store,
    seed_entry::{Edit as SeedEdit, SeedEntry},
    sprite::{Appearance, Expression, Sprite, SpriteStyle},
};
use crossterm::event::{
    self, DisableBracketedPaste, DisableMouseCapture, EnableBracketedPaste, EnableMouseCapture,
    Event, KeyEvent, KeyEventKind, MouseButton, MouseEventKind,
};
use ratatui::{
    backend::Backend,
    layout::{Constraint, Layout, Rect, Size},
    style::{Modifier, Style},
    text::{Line, Span},
    widgets::{Block, Borders, Clear, Paragraph, Wrap},
    DefaultTerminal, Frame, Terminal,
};
use std::{
    cell::{Cell, RefCell},
    io,
    time::{Duration, Instant},
};

const PREVIEW_STAGES: [Stage; 5] = [
    Stage::Egg,
    Stage::Baby,
    Stage::Child,
    Stage::Adult,
    Stage::Dead,
];
const SHORTCUTS: [&str; 8] = ["F", "G", "C", "R", "M", "P", "S", "N"];
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
    Identity,
    Seed,
}

#[derive(Clone, Copy)]
enum NativeKind {
    Talk,
    Thought,
    Context,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum SceneSubject {
    Live(u64),
    Roster,
    Studio,
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
    face_orbit_reference: u16,
    zoom_percent: u16,
    zoom_target: u16,
    zoom_origin: u16,
    zoom_step: u16,
    camera_transition_frames: usize,
    camera_started: usize,
    camera_blend: u16,
    orbit_degrees: u16,
    orbit_target: u16,
    orbit_origin: u16,
    orbit_default: u16,
    orbit_step: u16,
    orbit_started: usize,
    world_enabled: bool,
    world_elevation: u16,
    genetic_config: Option<Config>,
    seed_entry: SeedEntry,
    seed_pending: Option<MonCode>,
    seed_import_allowed: bool,
    seed_message: String,
    seed_cursor_targets: RefCell<Vec<(Rect, usize)>>,
    seed_edit_targets: RefCell<Vec<(Rect, SeedEdit)>>,
    menu_scroll: usize,
    menu_scroll_limit: Cell<usize>,
    menu_visible_rows: Cell<usize>,
    menu_total_lines: Cell<usize>,
    menu_history: Vec<Menu>,
    menu_number_start: Cell<usize>,
    menu_number_count: Cell<usize>,
    menu_footer: Cell<Rect>,
    /// Footer rect of the last `draw_information`; read by its regression test.
    #[cfg(test)]
    info_footer: Cell<Rect>,
    pub sprite_style: SpriteStyle,
    presentation: Option<crate::hud::DetailRules>,
    effective_style: Cell<SpriteStyle>,
    detail_ready: Cell<bool>,
    pub renderer: Option<Renderer>,
    pub controller: Option<ControllerPrompts>,
    controller_status: String,
    controller_camera: Option<String>,
    controller_menu_navigation: Option<String>,
    frame: usize,
    reaction: Option<Expression>,
    reaction_until: usize,
    reaction_started: usize,
    reaction_frames: usize,
    viewport: Cell<(u16, u16)>,
    camera_viewport: Cell<(u16, u16)>,
    camera_stage: Cell<Option<Stage>>,
    camera_subject: Cell<Option<SceneSubject>>,
    camera_subject_cut: Cell<bool>,
    pet_generation: u64,
    /// Source Room/Pet/Face mix in per-mille, target view and start frame.
    camera_resize: Cell<Option<([u16; 3], CameraView, usize)>>,
    /// The face detail tier and blend the scene subject drew last.
    face_detail: Cell<crate::render::DetailMemo>,
    /// Mix a manual blend starts from; None keeps the one-hot `camera_from_view`.
    camera_from_weights: Option<[u16; 3]>,
    camera_yaw_origin: Option<u16>,
    camera_pitch_origin: Option<f32>,
    camera_rules: crate::config::CameraRules,
    camera_pan: Cell<(i16, i16)>,
    camera_pan_from: Cell<(i16, i16)>,
    camera_pan_target: Cell<(i16, i16)>,
    camera_axes_started: Cell<usize>,
    camera_tilt: i16,
    camera_tilt_from: i16,
    camera_tilt_target: i16,
    camera_pan_current: Cell<(f64, f64)>,
    camera_tilt_deg: f64,
    camera_orbit_deg: f64,
    camera_zoom_pct: f64,
    camera_reset_in_flight: bool,
    camera_reset_elapsed_ms: f64,
    camera_stepped_realtime: bool,
    preview_angle: Cell<Option<u16>>,
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
    menu_targets: RefCell<Vec<(Rect, usize)>>,
    choice_geometry: RefCell<Option<(Menu, crate::hud::MenuGeometry)>>,
    control_targets: RefCell<Vec<(Rect, Action)>>,
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
            face_orbit_reference: 0,
            zoom_percent: 100,
            zoom_target: 100,
            zoom_origin: 100,
            zoom_step: 25,
            camera_transition_frames: 8,
            camera_started: 0,
            camera_blend: 100,
            orbit_degrees: 0,
            orbit_target: 0,
            orbit_origin: 0,
            orbit_default: 0,
            orbit_step: 45,
            orbit_started: 0,
            world_enabled: false,
            world_elevation: 30,
            genetic_config: None,
            seed_entry: SeedEntry::default(),
            seed_pending: None,
            seed_import_allowed: true,
            seed_message: String::new(),
            seed_cursor_targets: RefCell::new(Vec::new()),
            seed_edit_targets: RefCell::new(Vec::new()),
            menu_scroll: 0,
            menu_scroll_limit: Cell::new(512),
            menu_visible_rows: Cell::new(1),
            menu_total_lines: Cell::new(1),
            menu_history: Vec::new(),
            menu_number_start: Cell::new(0),
            menu_number_count: Cell::new(0),
            menu_footer: Cell::new(Rect::default()),
            #[cfg(test)]
            info_footer: Cell::new(Rect::default()),
            sprite_style: SpriteStyle::EightBit,
            presentation: None,
            effective_style: Cell::new(SpriteStyle::EightBit),
            detail_ready: Cell::new(false),
            renderer: None,
            controller: None,
            controller_camera: None,
            controller_menu_navigation: None,
            controller_status: "Inputd: checking local focus and assigned pad.".into(),
            frame: 0,
            reaction: None,
            reaction_until: 0,
            reaction_started: 0,
            reaction_frames: 0,
            viewport: Cell::new((u16::MAX, u16::MAX)),
            camera_viewport: Cell::new((u16::MAX, u16::MAX)),
            camera_stage: Cell::new(None),
            camera_subject: Cell::new(None),
            camera_subject_cut: Cell::new(false),
            pet_generation: 0,
            camera_resize: Cell::new(None),
            face_detail: Cell::new(crate::render::DetailMemo::default()),
            camera_from_weights: None,
            camera_yaw_origin: None,
            camera_pitch_origin: None,
            camera_rules: crate::config::CameraRules::INERT,
            camera_pan: Cell::new((0, 0)),
            camera_pan_from: Cell::new((0, 0)),
            camera_pan_target: Cell::new((0, 0)),
            camera_axes_started: Cell::new(0),
            camera_tilt: 0,
            camera_tilt_from: 0,
            camera_tilt_target: 0,
            camera_pan_current: Cell::new((0.0, 0.0)),
            camera_tilt_deg: 0.0,
            camera_orbit_deg: 0.0,
            camera_zoom_pct: 100.0,
            camera_reset_in_flight: false,
            camera_reset_elapsed_ms: 0.0,
            camera_stepped_realtime: false,
            preview_angle: Cell::new(None),
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
            menu_targets: RefCell::new(Vec::new()),
            choice_geometry: RefCell::new(None),
            control_targets: RefCell::new(Vec::new()),
        }
    }
    pub fn configured(game: Game, config: &Config) -> Self {
        let mut ui = Self::new(game);
        ui.genetic_config = Some(config.clone());
        ui.habitat_rules = Some(config.habitat.clone());
        ui.view_mode = config.view_mode;
        ui.hud_mode = config.hud_mode;
        ui.sprite_style = config.sprite_style;
        ui.presentation = Some(config.presentation.clone());
        ui.effective_style.set(config.sprite_style);
        ui.camera_rules = config.camera;
        ui.camera_view = config.camera_view;
        ui.camera_from_view = config.camera_view;
        ui.zoom_percent = config.zoom_percent;
        ui.zoom_target = config.zoom_percent;
        ui.zoom_origin = config.zoom_percent;
        ui.zoom_step = config.zoom_step_percent;
        ui.camera_transition_frames = config.zoom_transition_frames;
        ui.orbit_default = config.world.default_yaw_degrees % 360;
        ui.orbit_degrees = ui.orbit_default;
        ui.orbit_origin = ui.orbit_default;
        ui.orbit_target = ui.orbit_default;
        ui.face_orbit_reference = ui.orbit_default;
        ui.orbit_step = config.world.orbit_step_degrees;
        ui.camera_pan_current = Cell::new((0.0, 0.0));
        ui.camera_tilt_deg = 0.0;
        ui.camera_orbit_deg = f64::from(ui.orbit_default);
        ui.camera_zoom_pct = f64::from(ui.zoom_percent);
        ui.camera_reset_in_flight = false;
        ui.camera_reset_elapsed_ms = 0.0;
        ui.camera_stepped_realtime = false;
        ui.world_enabled = config.world.enabled;
        ui.world_elevation = config.world.elevation_degrees;
        ui.wander_speed = config.wander_speed;
        ui.mobile_stage = !matches!(
            ui.game.stage(&config.game),
            crate::game::Stage::Egg | crate::game::Stage::Dead
        );
        ui.reaction_frames = config.reaction_frames;
        ui.refresh_catalogue();
        ui
    }
    fn refresh_catalogue(&mut self) {
        let Some(config) = self.genetic_config.as_ref() else {
            return;
        };
        self.catalogue = config.characters.clone();
        for character in &mut self.catalogue {
            if let Ok((body, accent)) = config
                .game
                .training
                .palette(character.shape, &character.archetype)
            {
                character.body = body;
                character.accent = accent;
            }
        }
        if let Some(profile) = crate::genetics::Profile::for_game(&self.game, config) {
            if let Some(base) = self
                .catalogue
                .iter_mut()
                .find(|c| c.id == self.game.character)
            {
                *base = profile.character(base, config);
            }
        }
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
        // Realtime motion owns the displayed axes once the interactive loop starts.
        // Frame-only snapshots retain their deterministic legacy interpolation.
        if !self.camera_stepped_realtime {
            self.zoom_percent = (i32::from(self.zoom_origin)
                + (i32::from(self.zoom_target) - i32::from(self.zoom_origin)) * eased / 100)
                .clamp(50, 400) as u16;
            let t = frame
                .saturating_sub(self.orbit_started)
                .saturating_mul(100)
                .checked_div(self.camera_transition_frames.max(1))
                .unwrap_or(100)
                .min(100) as i32;
            let eased = t * t * (300 - 2 * t) / 10000;
            let turn =
                (i32::from(self.orbit_target) - i32::from(self.orbit_origin) + 540) % 360 - 180;
            self.orbit_degrees =
                (i32::from(self.orbit_origin) + turn * eased / 100).rem_euclid(360) as u16;
            let t = frame
                .saturating_sub(self.camera_axes_started.get())
                .saturating_mul(100)
                .checked_div(self.camera_transition_frames.max(1))
                .unwrap_or(100)
                .min(100) as i32;
            let eased = t * t * (300 - 2 * t) / 10000;
            let lerp = |from: i16, to: i16| {
                (i32::from(from) + (i32::from(to) - i32::from(from)) * eased / 100) as i16
            };
            let from = self.camera_pan_from.get();
            let to = self.camera_pan_target.get();
            self.camera_pan
                .set((lerp(from.0, to.0), lerp(from.1, to.1)));
            self.camera_tilt = lerp(self.camera_tilt_from, self.camera_tilt_target);
            self.camera_orbit_deg = f64::from(self.orbit_degrees);
            self.camera_tilt_deg = f64::from(self.camera_tilt);
            self.camera_zoom_pct = f64::from(self.zoom_percent);
            let pan = self.camera_pan.get();
            self.camera_pan_current
                .set((f64::from(pan.0), f64::from(pan.1)));
        }
        self.frame = frame;
    }
    pub fn effective_style(&self) -> SpriteStyle {
        if self.detail_ready.get() {
            self.effective_style.get()
        } else {
            self.sprite_style
        }
    }
    fn automatic_detail(&self) -> bool {
        self.presentation
            .as_ref()
            .is_some_and(|rules| rules.automatic)
    }
    fn detail_label(&self) -> String {
        format!(
            "{}{}-bit",
            if self.automatic_detail() {
                "AUTO"
            } else {
                "MANUAL"
            },
            if self.effective_style() == SpriteStyle::EightBit {
                8
            } else {
                16
            }
        )
    }
    fn detail_tag(&self) -> String {
        format!(
            "{}{}",
            if self.automatic_detail() { "A" } else { "M" },
            if self.effective_style() == SpriteStyle::EightBit {
                8
            } else {
                16
            }
        )
    }
    fn room_heading(&self, width: u16, rules: &Rules) -> String {
        let budget = usize::from(width.saturating_sub(2));
        if budget == 0 {
            return String::new();
        }
        let stage = format!("{:?}", self.game.stage(rules));
        let clock = if self.game.paused {
            "P"
        } else if self.game.sleeping {
            "R"
        } else {
            "L"
        };
        let mode = self.detail_tag();
        let compact = budget < 16;
        let stage = if compact { &stage[..1] } else { stage.as_str() };
        let illness = if self.game.ill {
            if compact {
                "I"
            } else {
                " ILL"
            }
        } else {
            ""
        };
        let tail = format!("{stage} {clock}{illness}");
        let mode_budget = if compact { mode.len() + 1 } else { 0 };
        let name_budget =
            budget.saturating_sub(Line::from(tail.as_str()).width() + 1 + mode_budget);
        if name_budget == 0 {
            return frame_label(&format!("{stage}{clock}{illness} {mode}"), budget as u16, 0);
        }
        let name = frame_label(self.name(), name_budget as u16, 0);
        let mut heading = format!("{name} {tail}");
        let species = self
            .current_character()
            .map(|c| c.species.as_str())
            .unwrap_or("pet");
        for extra in [
            mode,
            format!("{:?} {}%", self.camera().view, self.zoom_percent),
            format!("[/] {}deg", self.orbit_degrees),
            species.to_owned(),
        ] {
            let candidate = format!("{heading} {extra}");
            if Line::from(candidate.as_str()).width() <= budget {
                heading = candidate;
            }
        }
        heading
    }
    fn select_detail(&self, scene: Rect, renderer: Renderer) {
        let style = self
            .presentation
            .as_ref()
            .map_or(self.sprite_style, |rules| {
                rules.choose(
                    self.effective_style(),
                    self.sprite_style,
                    scene,
                    renderer,
                    self.zoom_percent,
                )
            });
        self.effective_style.set(style);
        self.detail_ready.set(true);
    }
    fn manual_style(&mut self, style: SpriteStyle) {
        self.sprite_style = style;
        if let Some(rules) = &mut self.presentation {
            rules.automatic = false;
        }
        self.effective_style.set(style);
        self.detail_ready.set(true);
    }
    fn toggle_detail(&mut self) {
        if let Some(rules) = &mut self.presentation {
            rules.automatic = !rules.automatic;
            if !rules.automatic {
                self.effective_style.set(self.sprite_style);
            }
        }
        self.notice = if self
            .presentation
            .as_ref()
            .is_some_and(|rules| rules.automatic)
        {
            "Automatic detail follows viewport and zoom. D selects manual.".into()
        } else {
            format!("{} / D toggles automatic detail.", self.detail_label())
        };
    }
    fn live_stage(&self) -> Option<Stage> {
        self.genetic_config
            .as_ref()
            .map(|config| self.game.stage(&config.game))
    }
    fn view_for_stage(
        view: CameraView,
        dimensions: (u16, u16),
        stage: Option<Stage>,
    ) -> CameraView {
        if (dimensions.0 < 28 || dimensions.1 < 10)
            && matches!(view, CameraView::Room | CameraView::Pet)
        {
            if stage == Some(Stage::Egg) {
                CameraView::Pet
            } else {
                CameraView::Face
            }
        } else {
            view
        }
    }
    fn effective_view(&self, view: CameraView, dimensions: (u16, u16)) -> CameraView {
        Self::view_for_stage(
            view,
            dimensions,
            self.camera_stage.get().or_else(|| self.live_stage()),
        )
    }
    fn set_scene(&self, area: Rect) {
        self.set_scene_for_stage(
            area,
            self.live_stage(),
            SceneSubject::Live(self.pet_generation),
        );
    }
    fn set_scene_for_stage(&self, area: Rect, stage: Option<Stage>, subject: SceneSubject) {
        // Capture exactly what the previous frame showed before the viewport
        // and stage change: a re-registration starts from the drawn mix.
        let current = self.camera_mix();
        let old_target = self.camera().view;
        self.pet_area.set(area);
        let dimensions = (area.width, area.height);
        let previous = self.camera_viewport.replace(dimensions);
        self.camera_stage.set(stage);
        if self.camera_subject.replace(Some(subject)) != Some(subject) {
            // Previews and replacement pets are different subjects. Their first
            // frame must not inherit a hatch, resize or manual-view blend.
            self.camera_resize.set(None);
            self.face_detail.set(crate::render::DetailMemo::default());
            self.camera_pan.set((0, 0));
            self.camera_pan_current.set((0.0, 0.0));
            self.camera_pan_from.set((0, 0));
            self.camera_pan_target.set((0, 0));
            self.preview_angle.set(None);
            self.camera_subject_cut.set(true);
            return;
        }
        if previous != (u16::MAX, u16::MAX) {
            let target = Self::view_for_stage(self.camera_view, dimensions, stage);
            // An unchanged target keeps the blend already in flight untouched.
            if target != old_target {
                self.camera_resize.set(Some((current, target, self.frame)));
            }
        }
    }
    /// The Room/Pet/Face mix `camera()` draws now, in per-mille summing to 1000.
    fn camera_mix(&self) -> [u16; 3] {
        let camera = self.camera();
        let source = camera
            .from_weights
            .unwrap_or_else(|| camera.from_view.one_hot());
        camera.view.blend_from(source, camera.transition_percent)
    }
    fn camera_resize_in_flight(&self) -> Option<CameraView> {
        let (_, to, started) = self.camera_resize.get()?;
        (self.camera_progress(started) < 100).then_some(to)
    }
    fn camera_progress(&self, started: usize) -> u32 {
        self.frame
            .saturating_sub(started)
            .saturating_mul(100)
            .checked_div(self.camera_transition_frames.max(1))
            .unwrap_or(100)
            .min(100) as u32
    }
    fn camera(&self) -> Camera {
        let (w, h) = self.camera_viewport.get();
        let effective = |view| self.effective_view(view, (w, h));
        if let Some((source, to, started)) = self.camera_resize.get() {
            let t = self.camera_progress(started);
            if t < 100 {
                return Camera {
                    from_view: CameraView::dominant(source),
                    from_weights: Some(source),
                    view: to,
                    zoom_percent: self.zoom_percent,
                    orbit_degrees: self.orbit_degrees,
                    transition_percent: (t * t * (300 - 2 * t) / 10000) as u16,
                    pan_permille: self.camera_pan.get(),
                    elevation_radians: Some(self.camera_pitch()),
                };
            }
        }
        let view = effective(self.camera_view);
        let cut = self.camera_subject_cut.get();
        let (from_view, from_weights) = match self.camera_from_weights {
            _ if cut => (view, None),
            Some(weights) => (CameraView::dominant(weights), Some(weights)),
            None => (effective(self.camera_from_view), None),
        };
        Camera {
            view,
            zoom_percent: self.zoom_percent,
            orbit_degrees: self.orbit_degrees,
            from_view,
            from_weights,
            transition_percent: if cut { 100 } else { self.camera_blend },
            pan_permille: self.camera_pan.get(),
            elevation_radians: Some(self.camera_pitch()),
        }
    }
    fn change_camera(&mut self, view: CameraView, zoom: u16) {
        self.camera_reset_in_flight = false;
        // A new manual blend starts from the mix drawn now (task3755a).
        let mix = self.camera_mix();
        self.camera_yaw_origin = Some(self.camera_yaw(self.subject_yaw()));
        self.camera_pitch_origin = Some(self.camera_pitch());
        if view != self.camera_view {
            self.camera_pan_from.set(self.camera_pan.get());
            self.camera_pan_target.set((0, 0));
            self.camera_axes_started.set(self.frame);
            self.camera_tilt_from = self.camera_tilt;
        }
        self.camera_from_weights = Some(match self.camera_resize_in_flight() {
            // Same view during a resize: the resize keeps carrying the view
            // blend, so the manual blend must not replay its source afterwards.
            Some(target) if view == self.camera_view => target.one_hot(),
            _ => mix,
        });
        self.camera_subject_cut.set(false);
        if view != self.camera_view {
            self.camera_resize.set(None);
        }
        if view == CameraView::Face && self.camera_view != CameraView::Face {
            // Face begins at the front, then explicit orbit is relative to
            // this entry. Zooming the same view must not recenter it.
            self.face_orbit_reference = self.orbit_degrees;
        }
        self.camera_from_view = self.camera_view;
        self.camera_view = view;
        self.zoom_origin = self.zoom_percent;
        self.zoom_target = zoom.clamp(50, 400);
        self.camera_started = self.frame;
        self.camera_blend = 0;
        self.notice = format!(
            "{:?} view / {}% zoom. +/- zoom, 0 zoom only, Z view, [/] orbit.",
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
    fn change_orbit(&mut self, target: u16) {
        self.camera_reset_in_flight = false;
        self.orbit_origin = self.orbit_degrees;
        self.orbit_target = target % 360;
        self.orbit_started = self.frame;
        self.notice = format!(
            "Orbit {} deg. [ left / ] right; Graphics resets orbit. 0 resets zoom only.",
            self.orbit_target
        );
    }
    fn orbit_by(&mut self, clockwise: bool) {
        if self.camera_rules.preview_orbit_pauses_turntable
            && matches!(self.menu, Some(Menu::Roster | Menu::Gallery))
            && self.preview_angle.get().is_none()
        {
            self.preview_angle.set(Some(self.preview_yaw()));
        }
        let delta = i32::from(self.orbit_step % 360);
        self.change_orbit(
            (i32::from(self.orbit_target) + if clockwise { delta } else { -delta }).rem_euclid(360)
                as u16,
        );
    }
    fn camera_yaw(&self, yaw: u16) -> u16 {
        // World and sprite use the same x'=cos(yaw)*x+sin(yaw)*z transform.
        // Only explicitly selected Face framing looks toward the front. Tiny
        // automatic head cropping retains the real world orientation/occlusion.
        let world_yaw = (u32::from(yaw) + u32::from(self.orbit_degrees)) % 360;
        let face_yaw =
            (i32::from(self.orbit_degrees) - i32::from(self.face_orbit_reference)).rem_euclid(360);
        let from = self.camera_yaw_origin.map(i32::from).unwrap_or_else(|| {
            if self.camera_from_view == CameraView::Face {
                face_yaw
            } else {
                world_yaw as i32
            }
        });
        let to = if self.camera_view == CameraView::Face {
            face_yaw
        } else {
            world_yaw as i32
        };
        if self.camera_subject_cut.get() {
            return to as u16;
        }
        let turn = (to - from + 540) % 360 - 180;
        (from + turn * i32::from(self.camera_blend) / 100).rem_euclid(360) as u16
    }
    fn subject_yaw(&self) -> u16 {
        match self.camera_subject.get() {
            Some(SceneSubject::Roster | SceneSubject::Studio) => self.preview_yaw(),
            _ => self.yaw,
        }
    }
    fn preview_yaw(&self) -> u16 {
        self.preview_angle
            .get()
            .unwrap_or(((self.frame * 3) % 360) as u16)
    }
    fn camera_pitch(&self) -> f32 {
        let base = |view| {
            if view == CameraView::Room && self.world_enabled {
                f32::from(self.world_elevation).to_radians()
            } else {
                0.32
            }
        };
        let to = base(self.camera_view) + f32::from(self.camera_tilt).to_radians();
        if self.camera_subject_cut.get() {
            return to;
        }
        let from = self
            .camera_pitch_origin
            .unwrap_or(base(self.camera_from_view));
        from + (to - from) * f32::from(self.camera_blend) / 100.0
    }
    fn menu_navigation_scope(&self) -> bool {
        !self.native_modal
            && !self.confirm_reset
            && !self.camera_scope()
            && self.menu.is_some_and(|menu| menu != Menu::Seed)
    }

    fn controller_navigation(&self, pad: &ControllerPrompts) -> String {
        if self.menu_navigation_scope() && self.menu.is_some_and(choice_menu) {
            if let Some(menu) = &self.controller_menu_navigation {
                return format!("{} + {menu}", pad.navigate);
            }
        }
        pad.navigate.clone()
    }

    fn controller_description(&self) -> String {
        if self.controller.is_none() {
            return self.controller_status.clone();
        }
        let hint = if self.camera_scope() {
            self.controller_camera.as_deref()
        } else if self.menu_navigation_scope() && self.menu.is_some_and(choice_menu) {
            self.controller_menu_navigation.as_deref()
        } else {
            None
        };
        hint.map_or_else(
            || self.controller_status.clone(),
            |hint| format!("{} {hint}", self.controller_status),
        )
    }

    fn camera_scope(&self) -> bool {
        !self.confirm_reset
            && !self.native_modal
            && self.game.challenge.is_none()
            && matches!(self.menu, None | Some(Menu::Roster | Menu::Gallery))
    }
    fn camera_command(&mut self, command: crate::input::CameraCommand) {
        use crate::input::CameraCommand::*;
        if self.camera_rules.max_lead_steps == 0 {
            return;
        }
        if self.camera_rules.preview_orbit_pauses_turntable
            && matches!(self.menu, Some(Menu::Roster | Menu::Gallery))
            && self.preview_angle.get().is_none()
        {
            self.preview_angle.set(Some(self.preview_yaw()));
        }
        let rules = self.camera_rules;
        self.camera_reset_in_flight = false;
        let lead = i32::from(rules.max_lead_steps);
        self.camera_pan_from.set(self.camera_pan.get());
        self.camera_tilt_from = self.camera_tilt;
        self.camera_axes_started.set(self.frame);
        match command {
            Pan(x, y) => {
                let current = self.camera_pan.get();
                let target = self.camera_pan_target.get();
                let step = i32::from(rules.pan_step_permille);
                let bound = i32::from(rules.pan_max_permille)
                    .min(500 - i32::from(rules.pan_margin_permille));
                let axis = |at: i16, to: i16, direction: i16| {
                    (i32::from(to) + i32::from(direction) * step)
                        .clamp(i32::from(at) - step * lead, i32::from(at) + step * lead)
                        .clamp(-bound, bound) as i16
                };
                self.camera_pan_target
                    .set((axis(current.0, target.0, x), axis(current.1, target.1, y)));
            }
            Tilt(direction) => {
                let step = i32::from(rules.tilt_step_degrees);
                self.camera_tilt_target = (i32::from(self.camera_tilt_target)
                    + i32::from(direction) * step)
                    .clamp(
                        i32::from(self.camera_tilt) - step * lead,
                        i32::from(self.camera_tilt) + step * lead,
                    )
                    .clamp(
                        i32::from(rules.tilt_min_degrees),
                        i32::from(rules.tilt_max_degrees),
                    ) as i16;
                if self.camera_blend == 100 {
                    self.camera_pitch_origin = None;
                }
            }
            Orbit(direction) => {
                let step = i32::from(rules.orbit_fine_step_degrees);
                let desired =
                    (i32::from(self.orbit_target) + i32::from(direction) * step).rem_euclid(360);
                let delta = (desired - i32::from(self.orbit_degrees) + 540) % 360 - 180;
                let limit = (step * lead).min(179);
                self.change_orbit(
                    (i32::from(self.orbit_degrees) + delta.clamp(-limit, limit)).rem_euclid(360)
                        as u16,
                );
            }
            Zoom(inward) => self.zoom_by(inward),
            Cycle => self.cycle_camera(),
            Reset => {
                let drawn_yaw = self.camera_yaw(self.subject_yaw());
                let drawn_pitch = self.camera_pitch();
                self.face_orbit_reference = self.orbit_default;
                self.camera_pan_target.set((0, 0));
                self.camera_tilt_target = 0;
                self.camera_pitch_origin = None;
                self.preview_angle.set(None);
                self.change_orbit(self.orbit_default);
                self.change_camera(self.camera_view, 100);
                self.camera_yaw_origin = Some(drawn_yaw);
                self.camera_pitch_origin = Some(drawn_pitch);
                if rules.reset_glide_ms == 0 {
                    self.snap_camera_targets();
                } else {
                    self.camera_reset_in_flight = true;
                    self.camera_reset_elapsed_ms = 0.0;
                }
            }
        }
        self.notice = "Camera: Shift+arrows or 4/6/8/2 pan; 7/9 orbit; 1/3 tilt; 5 reset. Graphics has touch controls.".into();
    }

    fn camera_axis_epsilon(&self, step: u16) -> f64 {
        f64::from(step) * f64::from(self.camera_rules.rest_epsilon_permille) / 1000.0
    }

    fn camera_axes_unsettled(&self) -> bool {
        let rules = self.camera_rules;
        let orbit_diff =
            ((f64::from(self.orbit_target) - self.camera_orbit_deg + 540.0) % 360.0) - 180.0;
        let pan = self.camera_pan_current.get();
        let target_pan = self.camera_pan_target.get();
        orbit_diff.abs() > self.camera_axis_epsilon(rules.orbit_fine_step_degrees)
            || (f64::from(self.camera_tilt_target) - self.camera_tilt_deg).abs()
                > self.camera_axis_epsilon(u16::from(rules.tilt_step_degrees))
            || (f64::from(target_pan.0) - pan.0).abs()
                > self.camera_axis_epsilon(rules.pan_step_permille)
            || (f64::from(target_pan.1) - pan.1).abs()
                > self.camera_axis_epsilon(rules.pan_step_permille)
            || (f64::from(self.zoom_target) - self.camera_zoom_pct).abs()
                > self.camera_axis_epsilon(self.zoom_step)
    }

    pub fn camera_unsettled(&self) -> bool {
        self.camera_reset_in_flight || self.camera_axes_unsettled()
    }

    fn camera_poll_interval_ms(&self, config: &Config) -> u64 {
        let frame_ms = if self.camera_unsettled() {
            u64::from(config.camera.motion_frame_ms)
        } else {
            config.animation_frame_ms
        };
        config.tick_ms.min(frame_ms)
    }

    fn snap_camera_targets(&mut self) {
        self.camera_orbit_deg = f64::from(self.orbit_target);
        self.orbit_degrees = self.orbit_target;
        self.orbit_origin = self.orbit_target;
        self.camera_tilt_deg = f64::from(self.camera_tilt_target);
        self.camera_tilt = self.camera_tilt_target;
        self.camera_tilt_from = self.camera_tilt_target;
        let pan = self.camera_pan_target.get();
        self.camera_pan_current
            .set((f64::from(pan.0), f64::from(pan.1)));
        self.camera_pan.set(pan);
        self.camera_pan_from.set(pan);
        self.camera_zoom_pct = f64::from(self.zoom_target);
        self.zoom_percent = self.zoom_target;
        self.zoom_origin = self.zoom_target;
        self.camera_reset_in_flight = false;
        self.camera_reset_elapsed_ms = 0.0;
        // A completed reset must not keep a second frame-clock yaw/pitch blend.
        self.camera_yaw_origin = None;
        self.camera_pitch_origin = None;
        self.camera_from_view = self.camera_view;
        self.camera_from_weights = None;
        self.camera_resize.set(None);
    }

    pub fn step_camera_realtime(&mut self, dt_ms: f64) {
        self.camera_stepped_realtime = true;
        let dt = dt_ms.clamp(0.0, 250.0);
        if !dt.is_finite() || dt == 0.0 {
            return;
        }
        let previous_orbit = self.orbit_degrees;
        let previous_tilt = self.camera_tilt;
        let rules = self.camera_rules;
        let half_life = f64::from(rules.follow_half_life_ms);
        let alpha = if half_life > 0.0 {
            1.0 - 0.5f64.powf(dt / half_life)
        } else {
            1.0
        };
        let approach = |current: f64, difference: f64, rate: u16, epsilon: f64| {
            let max_step = f64::from(rate) * dt / 1000.0;
            // Settling may snap only inside both the stop threshold and this
            // frame's rate budget. Zero time can never move a camera axis.
            let step = if difference.abs() <= epsilon {
                difference
            } else {
                difference * alpha
            };
            current + step.clamp(-max_step, max_step)
        };
        let orbit_diff =
            ((f64::from(self.orbit_target) - self.camera_orbit_deg + 540.0) % 360.0) - 180.0;
        self.camera_orbit_deg = approach(
            self.camera_orbit_deg,
            orbit_diff,
            rules.orbit_rate_deg_s,
            self.camera_axis_epsilon(rules.orbit_fine_step_degrees),
        )
        .rem_euclid(360.0);
        self.orbit_degrees = self.camera_orbit_deg.round() as u16 % 360;
        self.camera_tilt_deg = approach(
            self.camera_tilt_deg,
            f64::from(self.camera_tilt_target) - self.camera_tilt_deg,
            rules.tilt_rate_deg_s,
            self.camera_axis_epsilon(u16::from(rules.tilt_step_degrees)),
        );
        self.camera_tilt = self.camera_tilt_deg.round() as i16;
        let pan = self.camera_pan_current.get();
        let target_pan = self.camera_pan_target.get();
        let pan_epsilon = self.camera_axis_epsilon(rules.pan_step_permille);
        let next_pan = (
            approach(
                pan.0,
                f64::from(target_pan.0) - pan.0,
                rules.pan_rate_permille_s,
                pan_epsilon,
            ),
            approach(
                pan.1,
                f64::from(target_pan.1) - pan.1,
                rules.pan_rate_permille_s,
                pan_epsilon,
            ),
        );
        self.camera_pan_current.set(next_pan);
        self.camera_pan
            .set((next_pan.0.round() as i16, next_pan.1.round() as i16));
        self.camera_zoom_pct = approach(
            self.camera_zoom_pct,
            f64::from(self.zoom_target) - self.camera_zoom_pct,
            rules.zoom_rate_percent_s,
            self.camera_axis_epsilon(self.zoom_step),
        );
        self.zoom_percent = self.camera_zoom_pct.round() as u16;

        // The Room/Pet/Face blend retains its pet-frame clock, but its captured
        // endpoints must both follow realtime orbit/tilt. Otherwise a zoom or
        // view blend pins the drawn yaw/pitch between pet animation frames.
        let orbit_step =
            (i32::from(self.orbit_degrees) - i32::from(previous_orbit) + 540) % 360 - 180;
        if let Some(origin) = &mut self.camera_yaw_origin {
            *origin = (i32::from(*origin) + orbit_step).rem_euclid(360) as u16;
        }
        if let Some(origin) = &mut self.camera_pitch_origin {
            *origin += f32::from(self.camera_tilt - previous_tilt).to_radians();
        }

        if self.camera_reset_in_flight {
            self.camera_reset_elapsed_ms += dt;
            // reset_glide_ms bounds the accumulated camera transition time.
            // At the deadline reset cuts to its target, even with slow rates;
            // other input cancels that deadline before choosing a new target.
            if !self.camera_axes_unsettled()
                || self.camera_reset_elapsed_ms >= f64::from(rules.reset_glide_ms)
            {
                self.snap_camera_targets();
            }
        }
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
        self.mobile_stage = !matches!(self.game.stage(&config.game), Stage::Egg | Stage::Dead);
        if !self.game.paused && !self.overlay_open() && self.game.challenge.is_none() {
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
            ViewMode::Pixel => Sprite::animated(
                self.game.stage(rules),
                expression,
                self.effective_style(),
                pose,
            )
            .recolored(self.appearance().body, self.appearance().accent),
            ViewMode::Voxel if self.world_enabled && self.camera_view == CameraView::Room => {
                Sprite::voxel_with_pose(
                    self.game.stage(rules),
                    expression,
                    self.effective_style(),
                    pose,
                    (self.camera_yaw(self.yaw), self.camera_pitch()),
                    self.walking(),
                    self.appearance(),
                )
            }
            ViewMode::Voxel => Sprite::voxel_with_pose(
                self.game.stage(rules),
                expression,
                self.effective_style(),
                pose,
                (self.camera_yaw(self.yaw), self.camera_pitch()),
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
            text: {
                let words = self.words(
                    config,
                    matches!(kind, NativeKind::Thought | NativeKind::Context),
                );
                if matches!(kind, NativeKind::Context) {
                    format!(
                        "{words}\nCamera: Shift+arrows or 4/6/8/2 pan; 7/9 fine orbit; 1/3 or {{ }} tilt; 5 reset. Wheel zooms, Shift+wheel tilts. Graphics has touch controls."
                    )
                } else {
                    words
                }
            },
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
        self.choice_geometry.borrow_mut().take();
        if let Some(parent) = self.menu {
            if parent != menu && self.menu_history.len() < 16 {
                self.menu_history.push(parent);
            }
        } else {
            self.menu_history.clear();
        }
        self.menu = Some(menu);
        self.menu_selected = 0;
        self.menu_scroll = 0;
        self.menu_scroll_limit.set(512);
        self.menu_number_start.set(0);
        self.menu_number_count.set(0);
    }
    fn back(&mut self) {
        self.choice_geometry.borrow_mut().take();
        self.menu = self.menu_history.pop();
        self.menu_selected = 0;
        self.menu_scroll = 0;
        self.menu_scroll_limit.set(512);
        self.menu_number_start.set(0);
        self.menu_number_count.set(0);
    }
    fn action_hint(&self) -> String {
        if self.selected <= 4 {
            if self.game.paused {
                return "P Resume / care paused".into();
            }
            if self.game.health == 0 {
                return "N New pet / pet died".into();
            }
            if self.game.challenge.is_some() {
                return "Arrows: memory / Esc cancels".into();
            }
            if !self.game.progress.hatched {
                return "Egg: care after hatch".into();
            }
        }
        format!("[{}] {}", SHORTCUTS[self.selected], LABELS[self.selected])
    }
    fn menu_len(&self, menu: Menu) -> usize {
        match menu {
            Menu::Home => 14,
            Menu::Care => 5,
            Menu::Games => 2,
            Menu::Graphics => 30,
            Menu::Training => 6,
            Menu::Status | Menu::Help | Menu::Identity | Menu::Seed => 1,
            Menu::Gallery => 16,
            Menu::Talk => 7,
            Menu::Roster => self.catalogue.len().max(1),
            Menu::Habitat => self.habitat_rules.as_ref().map_or(2, |r| r.items.len() + 2),
        }
    }

    fn menu_panel(&self, title: &str, colors: &Colors, ascii: bool) -> Block<'static> {
        panel("", colors, ascii).title(Line::from(self.menu_caption(title, self.viewport.get().0)))
    }

    fn menu_caption(&self, title: &str, width: u16) -> String {
        let menu = self.menu.unwrap_or(Menu::Home);
        let width = usize::from(width);
        let mode = self.detail_tag();
        if width < 30 {
            format!("{mode} {}", menu_title(menu))
        } else if menu == Menu::Home {
            format!("{} [{mode}]", menu_title(menu))
        } else {
            let parent = self.menu_history.last().copied().unwrap_or(Menu::Home);
            let parent = if parent == Menu::Home {
                "Home"
            } else {
                menu_title(parent)
            };
            let raw = title.replace(&self.detail_label(), "");
            let clean = raw
                .split_whitespace()
                .filter(|word| *word != mode)
                .collect::<Vec<_>>()
                .join(" ");
            let clean = clean.trim_matches(|c: char| c == '/' || c.is_whitespace());
            let title =
                if matches!(menu, Menu::Talk | Menu::Roster | Menu::Gallery) && !clean.is_empty() {
                    clean
                } else {
                    menu_title(menu)
                };
            let breadcrumb = format!("{parent} > {title} [{mode}]");
            if Line::from(breadcrumb.as_str()).width() <= width {
                breadcrumb
            } else {
                format!("{} [{mode}]", menu_title(menu))
            }
        }
    }

    fn menu_description(&self, menu: Menu) -> Option<&'static str> {
        let index = self.menu_selected;
        match menu {
            Menu::Home if index == 8 && self.seed_import_allowed => {
                Some("This development pet has no save path. Share its birth code.")
            }
            Menu::Home if index == 9 && !self.seed_import_allowed => {
                Some("Save current progress and leave the game.")
            }
            Menu::Home if index == 13 && !self.seed_import_allowed => {
                Some("Birth-code import is disabled while a saved pet is open.")
            }
            Menu::Home => [
                "Feed, clean, rest or treat the Mon.",
                "Play a memory round to earn coins and happiness.",
                "Read thoughts and choose a conversation prompt.",
                "Inspect other Mons. Adoption requires confirmation.",
                "Review needs, health, growth and trained stats.",
                "Choose the renderer, detail, HUD and camera.",
                "Preview expressions and life stages without changing your pet.",
                "Read controls, Inputd status and terminal help.",
                "Write progress to the explicit local save path.",
                "Leave the game. Progress needs an explicit save path.",
                "Decorate the room with earned or purchased items.",
                "Train the hatched Mon's stats using care and coins.",
                "Read the birth identity, traits and shareable code.",
                "Enter a birth code. Keep the current pet until confirmed.",
            ]
            .get(index)
            .copied(),
            Menu::Care if index == 2 && self.game.sleeping => {
                Some("Wake the Mon and resume its normal activity.")
            }
            Menu::Care => [
                "Feed the Mon to restore fullness.",
                "Wash the Mon to restore hygiene.",
                "Rest restores energy while the clock runs.",
                "Treat illness. Review the Mon's health in Status.",
                "Return to the previous menu.",
            ]
            .get(index)
            .copied(),
            Menu::Games => [
                "Remember three arrows, then repeat them to earn rewards.",
                "Return to the previous menu.",
            ]
            .get(index)
            .copied(),
            Menu::Graphics => [
                "Render the autonomous Mon and its voxel environment.",
                "Use a classic pixel sprite without changing the pet.",
                "Choose compact 8-bit detail manually.",
                "Choose detailed 16-bit sprites manually.",
                "Pack two vertical pixels into each terminal cell.",
                "Use plain ASCII shading for restricted terminals.",
                "Pack a two-by-two pixel grid into each cell.",
                "Use Braille dot packing where the terminal supports it.",
                "Inspect expressions and stages in the sprite studio.",
                "Let the HUD adapt to the viewport's cell budget.",
                "Keep a compact scene and essential status controls.",
                "Show the expanded status and care dashboard.",
                "Frame the isometric room; tiny views focus the Mon.",
                "Frame the Mon's body and species features.",
                "Frame the head. Orbit to inspect its tracked face.",
                "Move the camera closer without changing pet stats.",
                "Move the camera farther away without changing pet stats.",
                "Restore 100 percent zoom; preserve the chosen orbit.",
                "Return to the previous menu.",
                "Toggle detail that follows viewport size and zoom.",
                "Orbit left around the Mon. This does not steer it.",
                "Orbit right around the Mon. This does not steer it.",
                "Restore the configured isometric azimuth.",
            ]
            .get(index)
            .copied(),
            _ => None,
        }
    }

    fn suspend_native_targets(&self) {
        if self.native_modal {
            self.care_targets.borrow_mut().clear();
            self.menu_targets.borrow_mut().clear();
            self.control_targets.borrow_mut().clear();
            self.seed_cursor_targets.borrow_mut().clear();
            self.seed_edit_targets.borrow_mut().clear();
        }
    }

    fn draw_menu_position(&self, screen: &mut Frame, colors: &Colors) {
        let Some(menu) = self.menu else { return };
        if self.native_modal || self.confirm_reset || screen.area().width < 12 {
            return;
        }
        let text = if matches!(menu, Menu::Help | Menu::Status | Menu::Identity) {
            let rows = self.menu_visible_rows.get().max(1);
            let total = self.menu_total_lines.get().max(1);
            let start = self.menu_scroll.min(self.menu_scroll_limit.get());
            format!("L{}-{}/{}", start + 1, (start + rows).min(total), total)
        } else if menu == Menu::Seed {
            format!(
                "Code {}/{} / Esc back",
                self.seed_entry.cursor(),
                self.seed_entry.text().len()
            )
        } else {
            let total = self.menu_len(menu);
            let selected = self.menu_selected.min(total.saturating_sub(1));
            let start = self.menu_number_start.get();
            let count = self.menu_number_count.get();
            if count > 0 && !matches!(menu, Menu::Roster | Menu::Gallery) {
                format!(
                    "{}/{} [{}-{}]",
                    selected + 1,
                    total,
                    start + 1,
                    start + count
                )
            } else {
                format!("{}/{}", selected + 1, total)
            }
        };
        let area = screen.area();
        let width = area.width.saturating_sub(2);
        screen.render_widget(
            Paragraph::new(text).style(Style::default().fg(colors.ink(7)).bg(colors.background)),
            Rect::new(area.x + 1, area.bottom().saturating_sub(1), width, 1),
        );
    }

    fn draw_talk_menu(
        &self,
        screen: &mut Frame,
        config: &Config,
        colors: &Colors,
        renderer: Renderer,
    ) {
        let area = screen.area();
        screen.render_widget(Clear, area);
        let block = self.menu_panel(
            &format!("TALK / {} says", self.name()),
            colors,
            renderer == Renderer::Ascii,
        );
        let body = block.inner(area);
        screen.render_widget(block, area);
        let [words_area, list, controls] = Layout::vertical([
            Constraint::Length(if body.height >= 12 { 4 } else { 1 }),
            Constraint::Min(1),
            Constraint::Length(2),
        ])
        .areas(body);
        self.dialog_area.set(words_area);
        let words = wrap_dialogue(&self.words(config, false), usize::from(body.width.max(1)));
        let rows = usize::from(words_area.height).max(1);
        let start = self.dialog_page.min(words.len().saturating_sub(rows));
        self.dialog_rows.set(rows);
        self.dialog_line_count.set(words.len());
        screen.render_widget(
            Paragraph::new(
                words
                    .into_iter()
                    .skip(start)
                    .take(rows)
                    .map(Line::from)
                    .collect::<Vec<_>>(),
            )
            .style(Style::default().fg(colors.ink(7))),
            words_area,
        );
        let entries = [
            "Read more",
            "Feed",
            "Memory",
            "Clean",
            "Rest",
            "Medicine",
            "Back",
        ];
        let window = crate::hud::menu_window(self.menu_selected, entries.len(), list.height);
        self.menu_number_start.set(window.start);
        self.menu_number_count.set(window.count);
        for (row, index) in (window.start..window.start + window.count).enumerate() {
            let target = Rect::new(list.x, list.y + row as u16, list.width, 1);
            let selected = index == self.menu_selected;
            let style = if selected {
                selected_style(colors)
            } else {
                Style::default().fg(colors.ink(2)).bg(colors.background)
            };
            screen.render_widget(
                Paragraph::new(format!(
                    "{}{} {}",
                    if selected { ">" } else { " " },
                    row + 1,
                    entries[index]
                ))
                .style(style),
                target,
            );
            self.menu_targets.borrow_mut().push((target, index));
        }
        self.menu_controls(controls);
        let hint = if body.width < 17 {
            format!(
                "Ent:{}\nEsc:Back",
                ["More", "Feed", "Play", "Wash", "Rest", "Med", "Back"][self.menu_selected.min(6)]
            )
        } else {
            "Enter: Choose\nEsc: Back".into()
        };
        screen.render_widget(
            Paragraph::new(hint).style(Style::default().fg(colors.ink(7))),
            controls,
        );
    }

    fn menu_controls(&self, area: Rect) {
        self.menu_footer.set(area);
        if area.height == 0 {
            return;
        }
        self.control_targets
            .borrow_mut()
            .push((Rect::new(area.x, area.y, area.width, 1), Action::Accept));
        if area.height > 1 {
            self.control_targets.borrow_mut().push((
                Rect::new(area.x, area.bottom() - 1, area.width, 1),
                Action::Cancel,
            ));
        }
    }
    fn draw_menu_navigation(&self, screen: &mut Frame, colors: &Colors) {
        let Some(menu) = self.menu else { return };
        let area = self.menu_footer.get().intersection(screen.area());
        if self.confirm_reset
            || self.native_modal
            || menu == Menu::Seed
            || area.width < 17
            || area.height < 2
        {
            return;
        }
        // Replace the old whole-row targets with exactly the painted buttons.
        self.control_targets.borrow_mut().clear();
        screen.render_widget(Clear, area);
        let style = Style::default().fg(colors.ink(7)).bg(colors.background);
        for (offset, label, action) in [(0, "[<]", Action::Previous), (3, "[>]", Action::Next)] {
            let rect = Rect::new(area.x + offset, area.y, 3, 1);
            screen.render_widget(Paragraph::new(label).style(style), rect);
            self.control_targets.borrow_mut().push((rect, action));
        }
        let verb = match menu {
            Menu::Roster => "new?",
            Menu::Training => "train",
            Menu::Habitat => {
                if area.width >= 32 {
                    "buy/place"
                } else {
                    "use"
                }
            }
            Menu::Gallery => "8/16",
            Menu::Talk => ["More", "feed", "memory", "clean", "rest", "med", "back"]
                [self.menu_selected.min(6)],
            Menu::Identity | Menu::Status | Menu::Help => "back",
            _ => {
                if area.width >= 32 {
                    "Choose"
                } else {
                    "OK"
                }
            }
        };
        let (accept, back) = self.controller.as_ref().map_or_else(
            || {
                (
                    if area.width >= 18 { "Enter" } else { "Ent" }.to_owned(),
                    "Esc".to_owned(),
                )
            },
            |pad| {
                let accept = compact_button(&pad.accept);
                let accept = if accept.len() + verb.len() + 2 <= usize::from(area.width - 7) {
                    accept
                } else {
                    tiny_button(&pad.accept)
                };
                let back = compact_button(&pad.back);
                let available =
                    area.width
                        .saturating_sub(if menu == Menu::Gallery { 7 } else { 0 });
                let back = if back.len() + 6 <= usize::from(available) {
                    back
                } else {
                    tiny_button(&pad.back)
                };
                (accept, back)
            },
        );
        let accept_rect = Rect::new(area.x + 7, area.y, area.width - 7, 1);
        let accept_caption =
            if let Some(pad) = self.controller.as_ref().filter(|_| area.width >= 32) {
                if menu == Menu::Habitat {
                    format!("{}: buy/place", tiny_button(&pad.accept))
                } else {
                    format!("[{accept}] {verb}")
                }
            } else {
                match menu {
                    Menu::Training | Menu::Habitat | Menu::Gallery => format!(
                        "{} {verb}",
                        if self.controller.is_none() {
                            "Ent"
                        } else {
                            &accept
                        }
                    ),
                    Menu::Roster => format!("{accept}:new?"),
                    _ => format!("{accept}: {verb}"),
                }
            };
        let caption = if accept_caption.chars().count() <= usize::from(accept_rect.width) {
            accept_caption
        } else {
            // Exact remaps remain in Controls/Help. Use a truthful keyboard
            // fallback when the complete mapped chord cannot fit.
            format!("Ent {verb}")
        };
        let visible = caption.len().min(usize::from(accept_rect.width)) as u16;
        screen.render_widget(Paragraph::new(caption).style(style), accept_rect);
        if visible > 0 {
            self.control_targets
                .borrow_mut()
                .push((accept_rect, Action::Accept));
        }
        let mut back_x = area.x;
        if menu == Menu::Gallery && area.width >= 17 {
            for (offset, label, action) in [
                (0, "[^]", Action::Direction(Direction::Up)),
                (3, "[v]", Action::Direction(Direction::Down)),
            ] {
                let rect = Rect::new(area.x + offset, area.bottom() - 1, 3, 1);
                screen.render_widget(Paragraph::new(label).style(style), rect);
                self.control_targets.borrow_mut().push((rect, action));
            }
            back_x += 7;
        }
        let back_rect = Rect::new(back_x, area.bottom() - 1, area.right() - back_x, 1);
        let back_caption = if let Some(pad) = self.controller.as_ref().filter(|_| area.width >= 32)
        {
            if menu == Menu::Habitat {
                format!(
                    "{}: Back / {}",
                    tiny_button(&pad.back),
                    self.controller_navigation(pad)
                )
            } else {
                format!("[{back}] Back / {}", self.controller_navigation(pad))
            }
        } else {
            match menu {
                Menu::Roster => format!("{back}: keep pet"),
                Menu::Talk => format!("{back}: Back / Tab"),
                Menu::Training | Menu::Habitat | Menu::Gallery => format!("{back} Back"),
                _ => format!("{back}: Back"),
            }
        };
        let caption = if back_caption.chars().count() <= usize::from(back_rect.width) {
            back_caption
        } else if self.controller.is_some() {
            format!("{back}: Back")
        } else {
            "Esc Back".into()
        };
        let visible = caption.len().min(usize::from(back_rect.width)) as u16;
        screen.render_widget(Paragraph::new(caption).style(style), back_rect);
        if visible > 0 {
            self.control_targets
                .borrow_mut()
                .push((back_rect, Action::Cancel));
        }
    }

    fn click(&mut self, column: u16, row: u16, rules: &Rules) -> Effect {
        if self.native_modal {
            return Effect::Continue;
        }
        let point = (column, row).into();
        if self.menu == Some(Menu::Seed) && !self.confirm_reset && self.seed_import_allowed {
            let edit = self
                .seed_edit_targets
                .borrow()
                .iter()
                .find(|(area, _)| area.contains(point))
                .map(|(_, edit)| *edit);
            if let Some(edit) = edit {
                self.seed_message.clear();
                self.seed_entry.edit(edit);
                return Effect::Continue;
            }
            let cursor = self
                .seed_cursor_targets
                .borrow()
                .iter()
                .find(|(area, _)| area.contains(point))
                .map(|(_, index)| *index);
            if let Some(cursor) = cursor {
                for _ in 0..=self.seed_entry.text().len() {
                    let previous = self.seed_entry.cursor();
                    if previous == cursor {
                        break;
                    }
                    self.seed_entry.edit(if previous < cursor {
                        SeedEdit::Right
                    } else {
                        SeedEdit::Left
                    });
                    if self.seed_entry.cursor() == previous {
                        break;
                    }
                }
                return Effect::Continue;
            }
        }

        let control = self
            .control_targets
            .borrow()
            .iter()
            .rev()
            .find(|(area, _)| area.contains(point))
            .map(|(_, action)| *action);
        if let Some(action) = control {
            let code = match action {
                Action::Accept => crossterm::event::KeyCode::Enter,
                Action::Previous => crossterm::event::KeyCode::BackTab,
                Action::Next => crossterm::event::KeyCode::Tab,
                Action::Direction(Direction::Up) => crossterm::event::KeyCode::Up,
                Action::Direction(Direction::Down) => crossterm::event::KeyCode::Down,
                Action::Confirm => crossterm::event::KeyCode::Char('y'),
                Action::Cancel => crossterm::event::KeyCode::Esc,
                Action::Menu => crossterm::event::KeyCode::Char('o'),
                Action::Help => crossterm::event::KeyCode::Char('?'),
                Action::Pause => crossterm::event::KeyCode::Char('p'),
                Action::Quit => crossterm::event::KeyCode::Char('q'),
                _ => return Effect::Continue,
            };
            let effect = self.handle(
                KeyEvent::new(code, crossterm::event::KeyModifiers::NONE),
                rules,
            );
            if matches!(
                action,
                Action::Accept
                    | Action::Confirm
                    | Action::Cancel
                    | Action::Menu
                    | Action::Help
                    | Action::Quit
            ) {
                // A second event before repaint cannot reuse an old modal
                // accept target as ordinary-room care.
                self.control_targets.borrow_mut().clear();
                self.menu_targets.borrow_mut().clear();
                self.care_targets.borrow_mut().clear();
                self.seed_cursor_targets.borrow_mut().clear();
                self.seed_edit_targets.borrow_mut().clear();
            }
            return effect;
        }
        if self.confirm_reset || self.native_modal {
            return Effect::Continue;
        }
        if self.menu.is_some() {
            let index = self
                .menu_targets
                .borrow()
                .iter()
                .find(|(area, _)| area.contains(point))
                .map(|(_, index)| *index);
            if let Some(index) = index {
                self.menu_selected = index;
                let effect = self.handle(
                    KeyEvent::new(
                        crossterm::event::KeyCode::Enter,
                        crossterm::event::KeyModifiers::NONE,
                    ),
                    rules,
                );
                self.control_targets.borrow_mut().clear();
                self.menu_targets.borrow_mut().clear();
                self.care_targets.borrow_mut().clear();
                return effect;
            }
            return Effect::Continue;
        }
        let index = self
            .care_targets
            .borrow()
            .iter()
            .find(|(area, _)| area.contains(point))
            .map(|(_, index)| *index);
        if let Some(index) = index {
            self.selected = index;
            self.handle(
                KeyEvent::new(
                    crossterm::event::KeyCode::Enter,
                    crossterm::event::KeyModifiers::NONE,
                ),
                rules,
            )
        } else {
            if self.pet_area.get().contains(point) {
                self.open_talk();
            }
            Effect::Continue
        }
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
    fn open_seed(&mut self) {
        self.seed_entry = SeedEntry::default();
        self.seed_pending = None;
        self.seed_message.clear();
        self.open(Menu::Seed);
    }
    fn paste_seed(&mut self, text: &str) {
        if self.menu == Some(Menu::Seed) && !self.confirm_reset && self.seed_import_allowed {
            self.seed_message.clear();
            self.seed_entry.paste(text);
        }
    }
    fn handle_seed_key(&mut self, key: KeyEvent) -> Effect {
        use crossterm::event::{KeyCode, KeyModifiers};
        if key.kind != KeyEventKind::Press {
            return Effect::Continue;
        }
        if key.modifiers.contains(KeyModifiers::CONTROL) {
            if key.code == KeyCode::Char('c') {
                return Effect::Quit;
            }
            if key.code == KeyCode::Char('u') && self.seed_import_allowed {
                self.seed_message.clear();
                self.seed_entry.edit(SeedEdit::Clear);
            }
            return Effect::Continue;
        }
        if key.modifiers.intersects(
            KeyModifiers::ALT | KeyModifiers::SUPER | KeyModifiers::HYPER | KeyModifiers::META,
        ) {
            return Effect::Continue;
        }
        if matches!(key.code, KeyCode::Char('q' | 'Q'))
            && (self.viewport.get().0 < 10 || self.viewport.get().1 < 4)
        {
            return Effect::Quit;
        }
        if key.code == KeyCode::Esc {
            self.seed_pending = None;
            self.back();
            return Effect::Continue;
        }
        if !self.seed_import_allowed {
            self.seed_message = "Saved pet protected; start --sample without --save.".into();
            return Effect::Continue;
        }
        if self.reset_refused {
            self.reset_refused = false;
            if key.code == KeyCode::Enter {
                return Effect::Continue;
            }
        }
        if key.code == KeyCode::Enter {
            let (w, h) = self.viewport.get();
            if w < 18 || h < 6 {
                self.seed_message = "Enlarge to20x8 to confirm.".into();
            } else if let Some(code) = self.seed_entry.validate() {
                self.seed_pending = Some(code);
                self.confirm_reset = true;
                self.adopt_character = None;
                self.seed_message.clear();
            }
            return Effect::Continue;
        }
        let edit = match key.code {
            KeyCode::Char(c) => Some(SeedEdit::Character(c)),
            KeyCode::Left => Some(SeedEdit::Left),
            KeyCode::Right => Some(SeedEdit::Right),
            KeyCode::Up => Some(SeedEdit::Up),
            KeyCode::Down => Some(SeedEdit::Down),
            KeyCode::Backspace => Some(SeedEdit::Backspace),
            KeyCode::Delete => Some(SeedEdit::Delete),
            KeyCode::Home => Some(SeedEdit::Home),
            KeyCode::End => Some(SeedEdit::End),
            _ => None,
        };
        if let Some(edit) = edit {
            self.seed_message.clear();
            self.seed_entry.edit(edit);
        }
        Effect::Continue
    }

    pub fn handle(&mut self, key: KeyEvent, rules: &Rules) -> Effect {
        // Inputd's cardinal stick aliases follow painted choice menus. Preview
        // cameras keep their pan controls; text entry, confirmation and native
        // dialogs retain their own input ownership. A release/repeat never acts.
        if key.kind == KeyEventKind::Press
            && key.modifiers == crossterm::event::KeyModifiers::ALT
            && !self.native_modal
            && !self.confirm_reset
            && !self.camera_scope()
            && self.menu.is_some_and(|menu| menu != Menu::Seed)
        {
            let direction = match key.code {
                crossterm::event::KeyCode::Char('8') => Some(Direction::Up),
                crossterm::event::KeyCode::Char('2') => Some(Direction::Down),
                crossterm::event::KeyCode::Char('4') if self.menu.is_some_and(choice_menu) => {
                    Some(Direction::Left)
                }
                crossterm::event::KeyCode::Char('6') if self.menu.is_some_and(choice_menu) => {
                    Some(Direction::Right)
                }
                _ => None,
            };
            if let Some(direction) = direction {
                return self.menu_input(Action::Direction(direction), rules);
            }
        }
        let mut camera_key = key;
        camera_key.kind = KeyEventKind::Press;
        // Plain camera chords retain their ordinary meaning outside camera
        // views: Shift+arrows still navigate menus, edit seeds and answer memory.
        // Strict Alt aliases remain isolated by the guard below.
        if let Some(command) = crate::input::camera_action(camera_key, self.menu.is_none())
            .filter(|_| self.camera_scope())
        {
            if key.kind == KeyEventKind::Press {
                self.camera_command(command);
            }
            return Effect::Continue;
        }
        if key.modifiers.contains(crossterm::event::KeyModifiers::ALT) {
            return Effect::Continue;
        }
        if self.native_modal {
            // The external dialog owns menu/care input until its choice or
            // inline fallback arrives. Preserve the universal quit shortcut.
            return if action(key) == Some(Action::Quit) {
                Effect::Quit
            } else {
                Effect::Continue
            };
        }
        if self.menu == Some(Menu::Seed) && !self.confirm_reset {
            return self.handle_seed_key(key);
        }

        if let Some(menu) = self.menu.filter(|_| {
            !self.confirm_reset
                && !self.native_modal
                && key.kind != KeyEventKind::Release
                && !key.modifiers.intersects(
                    crossterm::event::KeyModifiers::CONTROL
                        | crossterm::event::KeyModifiers::ALT
                        | crossterm::event::KeyModifiers::SUPER
                        | crossterm::event::KeyModifiers::HYPER
                        | crossterm::event::KeyModifiers::META,
                )
        }) {
            use crossterm::event::KeyCode;
            let step = self.menu_number_count.get().max(1);
            if matches!(menu, Menu::Status | Menu::Help | Menu::Identity) {
                let rows = self.menu_visible_rows.get().max(1);
                let next = match key.code {
                    KeyCode::Home => Some(0),
                    KeyCode::End => Some(self.menu_scroll_limit.get()),
                    KeyCode::PageUp => Some(
                        self.menu_scroll
                            .min(self.menu_scroll_limit.get())
                            .saturating_sub(rows),
                    ),
                    KeyCode::PageDown => Some(
                        self.menu_scroll
                            .min(self.menu_scroll_limit.get())
                            .saturating_add(rows)
                            .min(self.menu_scroll_limit.get()),
                    ),
                    _ => None,
                };
                if let Some(next) = next {
                    self.menu_scroll = next;
                    return Effect::Continue;
                }
            } else {
                let last = self.menu_len(menu).saturating_sub(1);
                let next = match key.code {
                    KeyCode::Home => Some(0),
                    KeyCode::End => Some(last),
                    KeyCode::PageUp => Some(self.menu_selected.saturating_sub(step)),
                    KeyCode::PageDown => Some(self.menu_selected.saturating_add(step).min(last)),
                    _ => None,
                };
                if let Some(next) = next {
                    self.menu_selected = next;
                    return Effect::Continue;
                }
            }
        }

        // Orbit is UI-local so controller arrows retain care/menu/memory semantics.
        // Match input::action's modifier and press-only shortcut policy.
        if key.kind == KeyEventKind::Press
            && !key.modifiers.intersects(
                crossterm::event::KeyModifiers::CONTROL
                    | crossterm::event::KeyModifiers::ALT
                    | crossterm::event::KeyModifiers::SUPER
                    | crossterm::event::KeyModifiers::HYPER
                    | crossterm::event::KeyModifiers::META,
            )
            && !self.confirm_reset
        {
            match key.code {
                crossterm::event::KeyCode::Char('k' | 'K') => {
                    self.open_seed();
                    return Effect::Continue;
                }
                crossterm::event::KeyCode::Char('[') => {
                    self.orbit_by(false);
                    return Effect::Continue;
                }
                crossterm::event::KeyCode::Char(']') => {
                    self.orbit_by(true);
                    return Effect::Continue;
                }
                _ => {}
            }
        }
        let Some(a) = action(key) else {
            return Effect::Continue;
        };
        if a == Action::Quit {
            return Effect::Quit;
        }
        let (width, height) = self.viewport.get();
        if (width < 10 || height < 4) && a == Action::Cancel {
            self.confirm_reset = false;
            self.seed_pending = None;
            self.adopt_character = None;
            self.back();
            return Effect::Continue;
        }
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
            self.seed_pending = None;
            self.adopt_character = None;
            self.reset_refused = true;
            self.notice = "Enlarge the viewport to confirm a new pet.".into();
            return Effect::Continue;
        }
        if self.reset_refused {
            self.reset_refused = false;
            if matches!(a, Action::Accept | Action::Confirm) {
                return Effect::Continue;
            }
        }
        let (confirm_width, confirm_height) = (18, 6);
        if self.confirm_reset && (width < confirm_width || height < confirm_height) {
            self.confirm_reset = false;
            self.adopt_character = None;
            self.seed_pending = None;
            self.reset_refused = true;
            self.notice = "Kept pet; confirmation no longer fits.".into();
            return Effect::Continue;
        }
        if self.confirm_reset {
            match a {
                Action::Accept | Action::Confirm => {
                    let selected = self.adopt_character.take();
                    let imported = self.seed_pending.take();
                    let seed = if let Some(code) = imported {
                        code.seed()
                    } else {
                        let random_seed = self.next_visual_random();
                        selected.map_or(random_seed, |id| selected_birth_seed(random_seed, id))
                    };
                    self.game = Game::new(rules, seed);
                    self.pet_generation = self.pet_generation.wrapping_add(1);
                    if imported.is_some() {
                        self.game.paused = true;
                    }
                    if let Some(character) = selected {
                        self.game.character = character;
                        if character >= 16 {
                            self.game.genesis_seed = None;
                        }
                    }
                    self.mobile_stage = false;
                    self.position = (500, 700);
                    self.destination = self.position;
                    self.yaw = 0;
                    self.target_yaw = 0;
                    self.visual_rng = self.game.visual_seed().wrapping_add(1).max(1);
                    self.wander_ticks = 0;
                    self.refresh_catalogue();
                    self.confirm_reset = false;
                    if let Some(code) = imported {
                        self.menu = None;
                        self.menu_history.clear();
                        self.notice = format!(
                            "New paused egg: {}. P resumes; I shares the code.",
                            code.compact()
                        );
                    } else {
                        self.notice = "A new egg! The next save replaces the previous pet.".into();
                    }
                    self.react(Expression::Surprised);
                }
                Action::Cancel => {
                    self.confirm_reset = false;
                    self.adopt_character = None;
                    self.seed_pending = None;
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
                self.open(Menu::Identity);
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
            Action::Detail => {
                self.toggle_detail();
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
            self.open(Menu::Home);
            return Effect::Continue;
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
    fn navigate_choice_menu(&mut self, menu: Menu, direction: Direction) {
        let count = self.menu_len(menu);
        let geometry = self.choice_geometry.borrow();
        if let Some((owner, geometry)) = geometry
            .as_ref()
            .filter(|(owner, geometry)| *owner == menu && geometry.navigation.len() == count)
        {
            debug_assert_eq!(*owner, menu);
            self.menu_selected =
                spatial_menu_neighbor(&geometry.navigation, self.menu_selected, direction);
        } else {
            // Before the first current-menu paint, only the known sequential
            // column is safe. Never use the previous menu's layout.
            let column: Vec<_> = (0..count).map(|index| (index, 0, index as i32)).collect();
            self.menu_selected = spatial_menu_neighbor(&column, self.menu_selected, direction);
        }
    }

    fn menu_input(&mut self, action: Action, rules: &Rules) -> Effect {
        let Some(menu) = self.menu else {
            return Effect::Continue;
        };
        if let Action::Number(number) = action {
            if number < self.menu_number_count.get() {
                self.menu_selected = self.menu_number_start.get() + number;
                return self.menu_input(Action::Accept, rules);
            }
            return Effect::Continue;
        }
        if menu == Menu::Seed {
            if self.confirm_reset {
                return Effect::Continue;
            }
            let code = match action {
                Action::Accept => crossterm::event::KeyCode::Enter,
                Action::Cancel => crossterm::event::KeyCode::Esc,
                Action::Next | Action::Direction(Direction::Down) => {
                    crossterm::event::KeyCode::Down
                }
                Action::Previous | Action::Direction(Direction::Up) => {
                    crossterm::event::KeyCode::Up
                }
                Action::Direction(Direction::Left) => crossterm::event::KeyCode::Left,
                Action::Direction(Direction::Right) => crossterm::event::KeyCode::Right,
                _ => return Effect::Continue,
            };
            return self.handle_seed_key(KeyEvent::new(code, crossterm::event::KeyModifiers::NONE));
        }
        if menu == Menu::Roster {
            let count = self.catalogue.len().max(1);
            match action {
                Action::Cancel => self.back(),
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
        if menu == Menu::Identity {
            // This view never spends coins, resets, trains, pauses or steers.
            match action {
                Action::Direction(Direction::Down | Direction::Right) | Action::Next => {
                    self.menu_scroll = self
                        .menu_scroll
                        .min(self.menu_scroll_limit.get())
                        .saturating_add(1)
                        .min(self.menu_scroll_limit.get());
                }
                Action::Direction(Direction::Up | Direction::Left) | Action::Previous => {
                    self.menu_scroll = self
                        .menu_scroll
                        .min(self.menu_scroll_limit.get())
                        .saturating_sub(1);
                }
                Action::Cancel | Action::Accept => self.back(),
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
                Action::Cancel => self.back(),
                Action::Direction(Direction::Left) | Action::Previous => {
                    self.menu_selected = (self.menu_selected + 15) % 16
                }
                Action::Direction(Direction::Right) | Action::Next => {
                    self.menu_selected = (self.menu_selected + 1) % 16
                }
                Action::Direction(Direction::Up) => {
                    self.gallery_stage =
                        (self.gallery_stage + PREVIEW_STAGES.len() - 1) % PREVIEW_STAGES.len()
                }
                Action::Direction(Direction::Down) => {
                    self.gallery_stage = (self.gallery_stage + 1) % PREVIEW_STAGES.len()
                }
                Action::Accept => {
                    self.manual_style(match self.effective_style() {
                        SpriteStyle::EightBit => SpriteStyle::SixteenBit,
                        SpriteStyle::SixteenBit => SpriteStyle::EightBit,
                    });
                }
                _ => {}
            }
            return Effect::Continue;
        }
        if matches!(menu, Menu::Status | Menu::Help) {
            match action {
                Action::Direction(Direction::Down | Direction::Right) | Action::Next => {
                    self.menu_scroll = self
                        .menu_scroll
                        .min(self.menu_scroll_limit.get())
                        .saturating_add(1)
                        .min(self.menu_scroll_limit.get());
                    return Effect::Continue;
                }
                Action::Direction(Direction::Up | Direction::Left) | Action::Previous => {
                    self.menu_scroll = self
                        .menu_scroll
                        .min(self.menu_scroll_limit.get())
                        .saturating_sub(1);
                    return Effect::Continue;
                }
                _ => {}
            }
        }
        let count = self.menu_len(menu);
        match action {
            Action::Cancel => self.back(),
            Action::Direction(direction) if choice_menu(menu) => {
                self.navigate_choice_menu(menu, direction)
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
                    11 => self.open(Menu::Training),
                    12 => self.open(Menu::Identity),
                    _ => self.open_seed(),
                },
                Menu::Care => {
                    if self.menu_selected < 4 {
                        self.menu = None;
                        self.care(
                            [Care::Feed, Care::Clean, Care::Rest, Care::Medicine]
                                [self.menu_selected],
                            rules,
                        );
                    } else {
                        self.back();
                    }
                }
                Menu::Games => {
                    if self.menu_selected == 0 {
                        self.menu = None;
                        self.care(Care::Play, rules);
                    } else {
                        self.back();
                    }
                }
                Menu::Graphics => match self.menu_selected {
                    0 => self.view_mode = ViewMode::Voxel,
                    1 => self.view_mode = ViewMode::Pixel,
                    2 => self.manual_style(SpriteStyle::EightBit),
                    3 => self.manual_style(SpriteStyle::SixteenBit),
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
                    19 => self.toggle_detail(),
                    20 => self.orbit_by(false),
                    21 => self.orbit_by(true),
                    22 => self.change_orbit(self.orbit_default),
                    23 => self.camera_command(crate::input::CameraCommand::Pan(-1, 0)),
                    24 => self.camera_command(crate::input::CameraCommand::Pan(1, 0)),
                    25 => self.camera_command(crate::input::CameraCommand::Pan(0, -1)),
                    26 => self.camera_command(crate::input::CameraCommand::Pan(0, 1)),
                    27 => self.camera_command(crate::input::CameraCommand::Tilt(1)),
                    28 => self.camera_command(crate::input::CameraCommand::Tilt(-1)),
                    29 => self.camera_command(crate::input::CameraCommand::Reset),
                    _ => self.back(),
                },
                Menu::Training => {
                    if self.menu_selected >= 5 {
                        self.back();
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
                Menu::Status | Menu::Help | Menu::Identity | Menu::Seed => self.back(),
                Menu::Talk => {
                    if self.menu_selected == 0 {
                        let rows = self.dialog_rows.get().max(1);
                        if self.dialog_page + rows < self.dialog_line_count.get() {
                            self.dialog_page += rows;
                        } else {
                            self.dialog_page = 0;
                            self.dialog_variant = self.dialog_variant.wrapping_add(1);
                        }
                    } else if self.menu_selected == 6 {
                        self.back();
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
                            self.back();
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
        self.seed_pending = None;
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
        let viewport = Rect::new(
            area.x.saturating_add(1),
            area.y.saturating_add(1),
            area.width.saturating_sub(2),
            area.height.saturating_sub(2),
        );
        let detail_scene = crate::hud::split(
            viewport,
            config.hud_golden_ratio,
            config.hud_landscape_columns,
            config.hud_portrait_ratio_percent,
        )
        .scene;
        self.select_detail(detail_scene, renderer);
        self.pet_area.set(Rect::default());
        self.dialog_area.set(Rect::default());
        self.seed_edit_targets.borrow_mut().clear();
        self.seed_cursor_targets.borrow_mut().clear();
        self.care_targets.borrow_mut().clear();
        self.menu_targets.borrow_mut().clear();
        self.choice_geometry.borrow_mut().take();
        self.control_targets.borrow_mut().clear();
        self.menu_number_count.set(0);
        self.menu_footer.set(Rect::default());
        #[cfg(test)]
        self.info_footer.set(Rect::default());
        let heading = self.room_heading(area.width, &config.game);
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
            if self.menu == Some(Menu::Seed) {
                let hint = if inner.height < 2 {
                    "^C".into()
                } else {
                    self.controller.as_ref().map_or_else(
                        || "Esc back\n^C quit".into(),
                        |p| format!("{} back\n^C quit", tiny_button(&p.back)),
                    )
                };
                screen.render_widget(
                    Paragraph::new(hint).style(Style::default().fg(colors.ink(7))),
                    inner,
                );
                return;
            }
            screen.render_widget(
                Paragraph::new(format!(
                    "{} / {:?}\n{}\n{}",
                    self.name(),
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
        if let Some(menu) = self
            .menu
            .filter(|menu| !self.native_modal && !choice_menu(*menu))
        {
            self.draw_menu(screen, menu, config, colors, renderer);
            self.draw_menu_navigation(screen, colors);
            self.draw_menu_position(screen, colors);
            self.draw_reset(screen, colors, ascii);
            return;
        }
        let [header, content, dialogue, dock] =
            crate::hud::full_layout(inner, config.inline_dialog_rows);
        if self.hud_mode == HudMode::Minimal
            || inner.width < 34
            || inner.height < 12
            || (self.hud_mode == HudMode::Auto
                && (area.width < config.hud_minimal_width
                    || area.height < config.hud_minimal_height
                    || crate::hud::scene_starved(content, inner, config.hud_golden_ratio)))
        {
            self.draw_minimal(screen, config, renderer, colors, frame, inner);
            self.redraw_heading(screen, config, colors, ascii);
            if let Some(menu) = self.menu.filter(|_| !self.native_modal) {
                self.draw_menu(screen, menu, config, colors, renderer);
            }
            self.draw_menu_position(screen, colors);
            self.draw_reset(screen, colors, ascii);
            self.draw_menu_navigation(screen, colors);
            self.suspend_native_targets();
            return;
        }

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
                        format!(" {clock} "),
                        Style::default()
                            .fg(colors.ink(0))
                            .bg(colors.ink(3))
                            .add_modifier(Modifier::BOLD),
                    ),
                    Span::raw(format!(
                        "  {} / {status}",
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
            " {:?} {}% [/] {}deg / {:?} {} ",
            self.camera_view,
            self.zoom_percent,
            self.orbit_degrees,
            expression,
            expression.kaomoji(frame)
        );
        let border = panel(&room_title, colors, ascii);
        let room_inner = border.inner(room);
        screen.render_widget(border, room);
        self.set_scene(room_inner);
        self.redraw_heading(screen, config, colors, ascii);
        let sprite = self.pet_sprite(&config.game, frame);
        screen.render_widget(
            RoomWidget {
                world: Some(&config.world),
                detail: self.effective_style(),
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
                face_detail: Some((
                    &self.face_detail,
                    crate::render::DetailClock {
                        frame: self.frame,
                        frames: self.camera_transition_frames,
                        zoom: [self.zoom_percent, self.zoom_target],
                    },
                )),
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
        let shortcuts = SHORTCUTS;
        for row in 0..2 {
            let mut spans = Vec::new();
            let mut offset = 0u16;
            for i in row * 4..row * 4 + 4 {
                let label = if i == 5 && self.game.paused {
                    "Resume"
                } else if i == 3 && self.game.sleeping {
                    "Wake"
                } else {
                    LABELS[i]
                };
                let style = if self.selected == i {
                    selected_style(colors)
                } else {
                    Style::default().fg(colors.ink(7)).bg(colors.ink(0))
                };
                let caption = format!("[{}] {label}", shortcuts[i]);
                let width = if i % 4 == 3 {
                    actions.width.saturating_sub(offset)
                } else {
                    actions.width / 4
                };
                let caption = caption.chars().take(usize::from(width)).collect::<String>();
                let printed = format!("{caption:width$}", width = usize::from(width));
                let rect = Rect::new(
                    actions.x.saturating_add(offset),
                    actions.y.saturating_add(row as u16),
                    width,
                    1,
                )
                .intersection(actions);
                self.care_targets.borrow_mut().push((rect, i));
                offset = offset.saturating_add(width);
                spans.push(Span::styled(printed, style));
            }
            lines.push(Line::from(spans));
        }
        let navigation = if let Some(pad) = &self.controller {
            format!(
                "{} [{}] Act [{}] Menu / ? Help / Q Quit",
                self.controller_navigation(pad),
                pad.accept,
                pad.back
            )
        } else {
            "O Menu Esc Back ? Help | Arrows/Tab Enter | Q Quit".into()
        };
        lines.push(Line::from(navigation.clone()).style(Style::default().fg(colors.ink(7))));
        lines.push(
            Line::from(if self.game.paused {
                "P Resume / care paused / ? Help".to_owned()
            } else {
                self.controller_description()
            })
            .style(Style::default().fg(colors.ink(7))),
        );
        screen.render_widget(Paragraph::new(lines), actions);
        if actions.height > 2 {
            let menu_caption = self
                .controller
                .as_ref()
                .map_or_else(|| "O Menu".to_owned(), |pad| format!("[{}] Menu", pad.back));
            for (caption, action) in [
                (menu_caption.as_str(), Action::Menu),
                ("Esc Back", Action::Cancel),
                ("? Help", Action::Help),
                ("Q Quit", Action::Quit),
            ] {
                if let Some(offset) = navigation.find(caption) {
                    let offset = (offset as u16).min(actions.width);
                    self.control_targets.borrow_mut().push((
                        Rect::new(
                            actions.x + offset,
                            actions.y + 2,
                            (caption.len() as u16).min(actions.width.saturating_sub(offset)),
                            1,
                        ),
                        action,
                    ));
                }
            }
        }
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
                        |pad| format!(
                            "{} repeat. [{}] Cancel.",
                            self.controller_navigation(pad),
                            pad.back
                        )
                    )
                )
            };
            self.bubble(screen, room, &text, colors, ascii);
        }
        self.draw_dialogue(screen, config, colors, renderer, dialogue, false);
        if let Some(menu) = self.menu.filter(|_| !self.native_modal) {
            self.draw_menu(screen, menu, config, colors, renderer);
        }
        self.draw_menu_position(screen, colors);
        self.draw_reset(screen, colors, ascii);
        self.draw_menu_navigation(screen, colors);
        self.suspend_native_targets();
    }
    /// The frame heading names the view registered by this frame's scene, not
    /// the previous size's (task3755b). Only the top border row is repainted.
    fn redraw_heading(&self, screen: &mut Frame, config: &Config, colors: &Colors, ascii: bool) {
        let area = screen.area();
        let heading = self.room_heading(area.width, &config.game);
        screen.render_widget(
            panel(&heading, colors, ascii).borders(Borders::TOP | Borders::LEFT | Borders::RIGHT),
            Rect::new(area.x, area.y, area.width, 1.min(area.height)),
        );
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
        let mut layout = crate::hud::split(
            area,
            config.hud_golden_ratio,
            config.hud_landscape_columns,
            config.hud_portrait_ratio_percent,
        );
        if talking && !layout.landscape && layout.info.height < 3 {
            layout.scene.height = area.height.saturating_sub(3);
            layout.info.y = layout.scene.bottom();
            layout.info.height = area.height.saturating_sub(layout.scene.height);
        }
        let scene = layout.scene;
        let info = layout.info;
        self.set_scene(scene);
        let sprite = self.pet_sprite(&config.game, frame);
        let expression = self.expression(&config.game, frame);
        screen.render_widget(
            RoomWidget {
                world: Some(&config.world),
                detail: self.effective_style(),
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
                face_detail: Some((
                    &self.face_detail,
                    crate::render::DetailClock {
                        frame: self.frame,
                        frames: self.camera_transition_frames,
                        zoom: [self.zoom_percent, self.zoom_target],
                    },
                )),
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
        } else if layout.landscape || (info.height >= 8 && info.width >= 18) {
            self.draw_information(screen, config, colors, info, frame, layout.landscape);
        } else {
            let control_rows = 2.min(info.height);
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
            let hint = if self.game.paused && self.selected <= 4 {
                if controls.width < 18 {
                    "P Resume\nO Menu ?".into()
                } else {
                    "P Resume / care paused\nO Menu / ? Help".into()
                }
            } else if let Some(pad) = &self.controller {
                let short =
                    ["Feed", "Play", "Wash", "Rest", "Med", "Paus", "Save", "New"][self.selected];
                let mapped = format!("{}:{short}", tiny_button(&pad.accept));
                let first = if mapped.len() <= usize::from(controls.width) {
                    mapped
                } else {
                    format!("{} {short} Ent", SHORTCUTS[self.selected])
                };
                format!("{first}\n{}:Menu ?", tiny_button(&pad.back))
            } else if controls.width < 18 {
                format!(
                    "{} {} Ent\nO Menu ?",
                    SHORTCUTS[self.selected],
                    ["Feed", "Play", "Wash", "Rest", "Med", "Paus", "Save", "New"][self.selected]
                )
            } else {
                format!(
                    "[{}] {} / [Enter]\nO Menu / ? Help",
                    SHORTCUTS[self.selected], LABELS[self.selected]
                )
            };
            screen.render_widget(
                Paragraph::new(hint.clone()).style(Style::default().fg(colors.ink(7))),
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
            if self.game.paused && self.selected <= 4 {
                self.control_targets.borrow_mut().push((
                    Rect::new(controls.x, controls.y, controls.width, 1),
                    Action::Pause,
                ));
            }
            if controls.height > 1 {
                let menu_width = hint
                    .lines()
                    .nth(1)
                    .and_then(|line| line.find('?'))
                    .map_or(controls.width, |offset| offset as u16)
                    .min(controls.width);
                self.control_targets.borrow_mut().extend([
                    (
                        Rect::new(controls.x, controls.y + 1, menu_width, 1),
                        Action::Menu,
                    ),
                    (
                        Rect::new(
                            controls.x + menu_width,
                            controls.y + 1,
                            controls.width - menu_width,
                            1,
                        ),
                        Action::Help,
                    ),
                ]);
            }
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

    fn draw_information(
        &self,
        screen: &mut Frame,
        config: &Config,
        colors: &Colors,
        info: Rect,
        frame: usize,
        landscape: bool,
    ) {
        let narrow_controls = (18..28).contains(&info.width) && info.height >= 14;
        let expanded_controls = (info.width >= 28 && info.height >= 8) || narrow_controls;
        let footer_rows: u16 = if narrow_controls {
            12
        } else if expanded_controls {
            6
        } else {
            3
        };
        let footer_rows = footer_rows.min(info.height);
        let upper = Rect::new(
            info.x,
            info.y,
            info.width,
            info.height.saturating_sub(footer_rows),
        );
        let footer = Rect::new(info.x, upper.bottom(), info.width, footer_rows);
        let budget = usize::from(upper.height);
        let mut lines = Vec::new();
        if landscape && budget >= 10 {
            lines.push(
                Line::from(format!(
                    "{} / {:?}",
                    self.name(),
                    self.game.stage(&config.game)
                ))
                .style(Style::default().fg(colors.ink(7))),
            );
        }
        if landscape && budget >= 18 {
            let expression = self.expression(&config.game, frame);
            lines.push(Line::from(format!(
                "{:?} {} / {:?} {}% / orbit {}deg",
                expression,
                expression.kaomoji(frame),
                self.camera().view,
                self.zoom_percent,
                self.orbit_degrees
            )));
        }
        lines.push(Line::from(if info.width < 28 {
            format!(
                "t{} ${} W{}",
                crate::hud::ticks(self.game.age_ticks),
                crate::hud::counter(self.game.coins),
                crate::hud::counter(self.game.wins)
            )
        } else {
            format!(
                "Age {} | ${} | W{}",
                crate::hud::ticks(self.game.age_ticks),
                crate::hud::counter(self.game.coins),
                crate::hud::counter(self.game.wins)
            )
        }));
        if budget >= 15 {
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
        } else {
            lines.push(Line::from(crate::hud::needs(
                &self.game,
                info.width,
                frame / 14,
            )));
        }
        if self.game.progress.hatched {
            let indices: Vec<usize> = if budget.saturating_sub(lines.len()) >= 6 {
                (0..5).collect()
            } else {
                vec![frame / 28 % 5]
            };
            for index in indices {
                let stat = self.game.progress.stats[index];
                let label = crate::progression::TrainingRules::label(index);
                let text = format!(
                    "{label} {} L{} x{} {}",
                    stat.value,
                    stat.level,
                    crate::hud::counter(stat.xp),
                    stat.grade()
                );
                lines.push(
                    Line::from(frame_label(&text, info.width, frame / 28))
                        .style(Style::default().fg(colors.ink(7))),
                );
            }
        } else if budget > lines.len() + 1 {
            lines.push(Line::from("Hatch: stats pending"));
        }
        if budget.saturating_sub(lines.len()) >= 3 {
            if let Some(theme) = config.habitat.theme(self.game.habitat.theme) {
                lines.push(Line::from(theme.name.clone()));
            }
            lines.push(Line::from(format!(
                "{} placed / {}",
                self.game.habitat.placed.count_ones(),
                if self.game.paused {
                    "PAUSED"
                } else if self.game.sleeping {
                    "RESTING"
                } else {
                    "LIVE"
                }
            )));
        }
        if budget.saturating_sub(lines.len()) >= 2 {
            lines.extend(
                wrap_dialogue(&self.controller_description(), usize::from(info.width))
                    .into_iter()
                    .take(2)
                    .map(Line::from),
            );
        }
        lines.extend(
            wrap_dialogue(&self.words(config, true), usize::from(info.width))
                .into_iter()
                .take(budget.saturating_sub(lines.len()))
                .map(Line::from),
        );
        screen.render_widget(Clear, info);
        screen.render_widget(
            Paragraph::new(lines).style(Style::default().fg(colors.ink(2)).bg(colors.background)),
            upper,
        );
        let selected = self.action_hint();
        let care_rows = [
            ["F Feed", "G Memory", "C Clean", "R Rest"],
            [
                "M Medicine",
                if self.game.paused {
                    "P Resume"
                } else {
                    "P Pause"
                },
                "S Save",
                "N New",
            ],
        ];
        let resume = self.game.paused && self.selected <= 4;
        let selected_segment = if resume {
            "P Resume".to_owned()
        } else {
            self.controller.as_ref().map_or_else(
                || format!("[Enter] {selected}"),
                |pad| format!("{} {selected}", compact_button(&pad.accept)),
            )
        };
        let hint_lines: Vec<String> = if narrow_controls {
            let mut lines = care_rows
                .iter()
                .flat_map(|row| row.chunks(2).map(|pair| pair.join("  ")))
                .collect::<Vec<_>>();
            lines.push(if resume {
                "P Resume / care paused".into()
            } else {
                selected_segment.clone()
            });
            lines.push(self.controller.as_ref().map_or_else(
                || "Arrows/Tab select".into(),
                |pad| {
                    frame_label(
                        &format!(
                            "{} / {} Next",
                            self.controller_navigation(pad),
                            compact_button(&pad.next)
                        ),
                        info.width,
                        frame / 28,
                    )
                },
            ));
            lines.push(self.controller.as_ref().map_or_else(
                || "O Menu / Esc Back".into(),
                |pad| format!("{} / O Menu", compact_button(&pad.back)),
            ));
            lines.push("? Help / Q Quit".into());
            lines.push("+/- 0 Z [/] / I Code H L V T B".into());
            let status = wrap_dialogue(&self.controller_description(), usize::from(info.width));
            lines.extend(status.into_iter().take(3));
            lines
        } else if expanded_controls {
            let navigation = self.controller.as_ref().map_or_else(
                || {
                    if resume {
                        "P Resume / care paused / Q Quit".into()
                    } else {
                        format!("Arrows/Tab / {selected_segment} / Q Quit")
                    }
                },
                |pad| {
                    format!(
                        "{} / {selected_segment} / Q Quit",
                        self.controller_navigation(pad)
                    )
                },
            );
            let menu = self.controller.as_ref().map_or_else(
                || "O Menu / Esc Back / ? Help".to_string(),
                |pad| format!("{} / O Menu / ? Help", compact_button(&pad.back)),
            );
            vec![
                care_rows[0].join("  "),
                care_rows[1].join("  "),
                navigation,
                menu,
                "+/- 0 Z [/] orbit / I Code T Talk B Roster H Items L Train V Graphics".into(),
                frame_label(&self.controller_description(), info.width, frame / 28),
            ]
        } else if let Some(pad) = &self.controller {
            vec![
                format!("{} {selected}", tiny_button(&pad.accept)),
                format!("{} Menu / ? Help", tiny_button(&pad.back)),
                format!("{} Next / +/- Z", tiny_button(&pad.next)),
            ]
        } else {
            vec![
                format!("{selected} Ent"),
                "O Menu / ? Help".into(),
                "+/- 0 Z / L".into(),
            ]
        };
        // The footer is Clear-ed with the rest of `info`; paint the configured
        // background so no terminal default (light or dark) shows through.
        screen.render_widget(
            Paragraph::new(hint_lines.join("\n"))
                .style(Style::default().fg(colors.ink(7)).bg(colors.background)),
            footer,
        );
        #[cfg(test)]
        self.info_footer.set(footer);
        let action_row = if narrow_controls {
            4
        } else if expanded_controls {
            2
        } else {
            0
        };
        let action_line = hint_lines.get(action_row).map_or("", String::as_str);
        let segment = if expanded_controls {
            selected_segment.as_str()
        } else if resume {
            "P Resume"
        } else {
            action_line
        };
        if let Some(offset) = action_line.find(segment) {
            let offset = offset.min(usize::from(footer.width)) as u16;
            let width = (segment.len() as u16).min(footer.width.saturating_sub(offset));
            let area = Rect::new(footer.x + offset, footer.y + action_row as u16, width, 1);
            if resume {
                self.control_targets
                    .borrow_mut()
                    .push((area, Action::Pause));
            } else {
                self.care_targets.borrow_mut().push((area, self.selected));
            }
        }
        if expanded_controls {
            let columns = if narrow_controls { 2 } else { 4 };
            for (index, caption) in care_rows.iter().flatten().copied().enumerate() {
                let row = index / columns;
                if let Some(offset) = hint_lines[row].find(caption) {
                    let offset = (offset as u16).min(footer.width);
                    self.care_targets.borrow_mut().push((
                        Rect::new(
                            footer.x + offset,
                            footer.y + row as u16,
                            (caption.len() as u16).min(footer.width.saturating_sub(offset)),
                            1,
                        ),
                        index,
                    ));
                }
            }
        }
        let back_caption = self
            .controller
            .as_ref()
            .map(|pad| format!("{} / O Menu", compact_button(&pad.back)));
        for (row, line) in hint_lines
            .iter()
            .enumerate()
            .take(usize::from(footer.height))
        {
            for (caption, action) in [
                ("O Menu", Action::Menu),
                ("Esc Back", Action::Cancel),
                ("? Help", Action::Help),
                ("Q Quit", Action::Quit),
            ]
            .into_iter()
            .chain(
                back_caption
                    .as_deref()
                    .map(|caption| (caption, Action::Menu)),
            ) {
                if let Some(offset) = line.find(caption) {
                    let offset = (offset as u16).min(footer.width);
                    self.control_targets.borrow_mut().push((
                        Rect::new(
                            footer.x + offset,
                            footer.y + row as u16,
                            (caption.len() as u16).min(footer.width.saturating_sub(offset)),
                            1,
                        ),
                        action,
                    ));
                }
            }
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
        screen.render_widget(Clear, area);
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
            lines.resize(rows, Line::from(""));
            self.menu_number_start.set(self.menu_selected.min(6));
            self.menu_number_count.set(1);
            let controls = Rect::new(
                body.x,
                body.bottom().saturating_sub(2),
                body.width,
                2.min(body.height),
            );
            self.menu_controls(controls);
            self.menu_targets.borrow_mut().push((
                Rect::new(body.x, body.y, body.width, body.height.saturating_sub(2)),
                0,
            ));
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
                || {
                    (
                        if body.width < 18 {
                            "Ent".into()
                        } else {
                            "Enter".into()
                        },
                        "Esc".into(),
                    )
                },
                |pad| (tiny_button(&pad.accept), tiny_button(&pad.back)),
            );
            lines.push(
                Line::from(if body.width < 18 {
                    format!(
                        "1 {}:{}",
                        ["Ent", "F", "G", "C", "R", "M", "Esc"][self.menu_selected.min(6)],
                        ["More", "Feed", "Play", "Wash", "Rest", "Med", "Back"]
                            [self.menu_selected.min(6)]
                    )
                } else {
                    format!(
                        "1 {} {label} / {accept}",
                        ["", "[F]", "[G]", "[C]", "[R]", "[M]", "Esc"][self.menu_selected.min(6)]
                    )
                })
                .style(Style::default().fg(colors.ink(7))),
            );
            lines.push(
                Line::from(if body.width < 18 {
                    format!("{back}:Back")
                } else {
                    format!("[{back}] Back / Tab")
                })
                .style(Style::default().fg(colors.ink(7))),
            );
        } else if !compact && lines.len() < usize::from(body.height) {
            lines.push(Line::from(self.notice.clone()).style(Style::default().fg(colors.ink(7))));
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
                let stat = format!(
                    "{}L{}v{}",
                    ["Sw", "Fl", "Ru", "Pw", "St"][index],
                    stat.level,
                    stat.value
                );
                let balance = format!("{}{} {stat}", "$", crate::hud::counter(self.game.coins));
                pages.push(if balance.len() <= usize::from(width) {
                    balance
                } else {
                    stat
                });
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
        let area = screen.area();
        screen.render_widget(Clear, area);
        let selected = self.menu_selected.min(5);
        let title = if area.width < 20 {
            format!("{} T{}/6", self.detail_tag(), selected + 1)
        } else {
            format!(
                " TRAINING / {} / {} / Lv/value ",
                self.detail_label(),
                self.name()
            )
        };
        let block = self.menu_panel(&title, colors, renderer == Renderer::Ascii);
        let inner = block.inner(area);
        screen.render_widget(block, area);
        let micro = inner.height < 10 || inner.width < 28;
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
        let window = crate::hud::menu_window(selected, 6, list.height);
        let rows = window.count;
        let start = window.start;
        self.menu_number_start.set(start);
        self.menu_number_count.set(rows.min(6 - start));
        self.menu_controls(controls);
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
                self.menu_targets.borrow_mut().push((
                    Rect::new(list.x, list.y + (index - start) as u16, list.width, 1),
                    index,
                ));
                let text = format!(
                    "{}{} {text}",
                    if index == selected { ">" } else { " " },
                    index - start + 1
                );
                Line::from(text).style(if index == selected {
                    selected_style(colors)
                } else {
                    Style::default().fg(colors.ink(2))
                })
            })
            .collect::<Vec<_>>();
        screen.render_widget(Paragraph::new(lines), list);
        let r = &config.game.training;
        let reason = if self.game.stage(&config.game) == crate::game::Stage::Egg {
            Some("Egg: wait")
        } else if self.game.health == 0 {
            Some("Pet died")
        } else if !self.game.progress.hatched {
            Some("Stats: wait")
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
                    "1..6 / Enter: train / Tab: next\nEscape: Back / L: training".to_owned()
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
            Paragraph::new(hints).style(Style::default().fg(colors.ink(7))),
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
        let area = screen.area();
        screen.render_widget(Clear, area);
        let title = if area.width < 24 {
            format!("{} ITEMS", self.detail_tag())
        } else {
            format!("{} HABITAT / ITEMS", self.detail_label())
        };
        let block = self.menu_panel(&title, colors, renderer == Renderer::Ascii);
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
                if (self.frame / 14).is_multiple_of(2) {
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
            Paragraph::new(self.notice.clone()).style(Style::default().fg(colors.ink(7))),
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
        let window = crate::hud::menu_window(self.menu_selected, entries.len(), list.height);
        let rows = window.count;
        let start = window.start;
        self.menu_number_start.set(start);
        self.menu_number_count
            .set(rows.min(entries.len().saturating_sub(start)));
        self.menu_controls(controls);
        let lines: Vec<Line> = entries
            .iter()
            .enumerate()
            .skip(start)
            .take(rows)
            .map(|(index, text)| {
                let selected = index == self.menu_selected;
                let text = text
                    .chars()
                    .take(usize::from(list.width.saturating_sub(3)))
                    .collect::<String>();
                self.menu_targets.borrow_mut().push((
                    Rect::new(list.x, list.y + (index - start) as u16, list.width, 1),
                    index,
                ));
                Line::from(format!(
                    "{}{} {text}",
                    if selected { ">" } else { " " },
                    index - start + 1
                ))
                .style(if selected {
                    selected_style(colors)
                } else {
                    Style::default().fg(colors.ink(2))
                })
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
            Paragraph::new(hints).style(Style::default().fg(colors.ink(7))),
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
                "{} {}/{} {}",
                self.detail_tag(),
                self.menu_selected + 1,
                self.catalogue.len(),
                character.name
            )
        } else {
            format!(
                " ROSTER {}/{} / {} ",
                self.menu_selected + 1,
                self.catalogue.len(),
                self.detail_label()
            )
        };
        let block = self
            .menu_panel(&title, colors, ascii)
            .title_bottom(Line::from(frame_label(
                &format!("{} / {}", character.species, character.archetype),
                area.width.saturating_sub(2),
                self.frame / 14,
            )));
        let inner = block.inner(area);
        screen.render_widget(block, area);
        let compact = inner.width < 50 || inner.height < 16;
        let [header, info, room, controls] = Layout::vertical([
            Constraint::Length(if micro && area.height <= 7 { 0 } else { 1 }),
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
        self.set_scene_for_stage(room, Some(Stage::Adult), SceneSubject::Roster);
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
                self.effective_style(),
                self.frame,
            )
            .recolored(character.body, character.accent),
            ViewMode::Voxel if self.world_enabled && self.camera_view == CameraView::Room => {
                Sprite::voxel_with_pose(
                    crate::game::Stage::Adult,
                    expression,
                    self.effective_style(),
                    self.frame,
                    (self.camera_yaw(self.preview_yaw()), self.camera_pitch()),
                    false,
                    appearance,
                )
            }
            ViewMode::Voxel => Sprite::voxel_with_pose(
                crate::game::Stage::Adult,
                expression,
                self.effective_style(),
                self.frame,
                (self.camera_yaw(self.preview_yaw()), self.camera_pitch()),
                false,
                appearance,
            ),
        };
        screen.render_widget(
            RoomWidget {
                world: Some(&config.world),
                detail: self.effective_style(),
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
                face_detail: Some((
                    &self.face_detail,
                    crate::render::DetailClock {
                        frame: self.frame,
                        frames: self.camera_transition_frames,
                        zoom: [self.zoom_percent, self.zoom_target],
                    },
                )),
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
            Paragraph::new(hints).style(Style::default().fg(colors.ink(7))),
            controls,
        );
        self.menu_controls(controls);
    }
    fn draw_reset(&self, screen: &mut Frame, colors: &Colors, ascii: bool) {
        if !self.confirm_reset {
            return;
        }
        self.care_targets.borrow_mut().clear();
        self.menu_targets.borrow_mut().clear();
        self.control_targets.borrow_mut().clear();
        self.seed_cursor_targets.borrow_mut().clear();
        self.seed_edit_targets.borrow_mut().clear();
        if let Some(code) = self.seed_pending {
            let modal = screen.area();
            screen.render_widget(Clear, modal);
            let block = panel(" NEW SEED? ", colors, ascii);
            let body = block.inner(modal);
            screen.render_widget(block, modal);
            let name = self
                .genetic_config
                .as_ref()
                .and_then(|config| {
                    let game = Game::new(&config.game, code.seed());
                    crate::genetics::Profile::for_game(&game, config).map(|profile| profile.name)
                })
                .unwrap_or_else(|| "New Mon".into());
            let controls = Rect::new(
                body.x,
                body.bottom().saturating_sub(2),
                body.width,
                body.height.min(2),
            );
            let info = Rect::new(body.x, body.y, body.width, body.height.saturating_sub(2));
            screen.render_widget(
                Paragraph::new(format!("{}\n{}\nNew paused egg", code.compact(), name))
                    .style(Style::default().fg(colors.ink(2))),
                info,
            );
            let hint = self.controller.as_ref().map_or_else(
                || "Enter: Start\nEsc: Keep pet".into(),
                |p| {
                    format!(
                        "{} Start\n{} Keep",
                        tiny_button(&p.accept),
                        tiny_button(&p.back)
                    )
                },
            );
            screen.render_widget(
                Paragraph::new(hint).style(Style::default().fg(colors.ink(7))),
                controls,
            );
            self.menu_controls(controls);
            return;
        }
        let modal = screen.area();
        screen.render_widget(Clear, modal);
        let block = panel(" NEW PET? ", colors, ascii);
        let body = block.inner(modal);
        screen.render_widget(block, modal);
        let question = self
            .adopt_character
            .and_then(|id| self.catalogue.iter().find(|character| character.id == id))
            .map_or_else(
                || "Replace pet?".to_owned(),
                |c| format!("Adopt {}?", c.name),
            );
        let question_area = Rect::new(body.x, body.y, body.width, body.height.saturating_sub(2));
        screen.render_widget(
            Paragraph::new(question)
                .style(Style::default().fg(colors.ink(2)).bg(colors.background)),
            question_area,
        );
        let controls = Rect::new(
            body.x,
            body.bottom().saturating_sub(2),
            body.width,
            2.min(body.height),
        );
        let prompt = self.controller.as_ref().map_or_else(
            || "Enter/Y: New\nEsc: Keep".to_owned(),
            |pad| format!("[{}] New pet / Y\n[{}] Keep / Esc", pad.accept, pad.back),
        );
        screen.render_widget(
            Paragraph::new(prompt).style(Style::default().fg(colors.ink(7)).bg(colors.background)),
            controls,
        );
        self.menu_controls(controls);
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
    fn identity_content(&self, config: &Config) -> (Option<String>, Vec<String>) {
        let Some(profile) = crate::genetics::Profile::for_game(&self.game, config) else {
            let reason = if self.game.genesis_seed.is_none() {
                "No birth seed was stored for this existing pet."
            } else {
                "The selected pet does not match its original birth seed."
            };
            return (
                None,
                vec![
                    reason.into(),
                    "A reproducible code cannot be recovered or invented.".into(),
                    "Your pet and its saved progress are unchanged.".into(),
                ],
            );
        };
        let mut lines = vec![
            format!("Name: {}", profile.name),
            format!("Species: {}", profile.species),
            format!("Personality: {}", profile.archetype),
            format!("Trait: {}", profile.trait_label),
            format!("Trope: {}", profile.trope),
            format!("Likes: {}", profile.likes.join(", ")),
            format!("Dislikes: {}", profile.dislikes.join(", ")),
            format!("Bonus: {}", profile.bonus_stat),
            format!("Colors: body {} / accent {}", profile.body, profile.accent),
            "Birth stats (current training is in Status):".into(),
        ];
        for (index, stat) in profile.hatch_stats.iter().enumerate() {
            lines.push(format!(
                "{}: {} / aptitude {}",
                crate::progression::TrainingRules::label(index),
                stat.value,
                stat.grade()
            ));
        }
        lines.push("This code reproduces birth identity, not current care or a save.".into());
        (Some(profile.code), lines)
    }

    fn draw_seed_entry(&self, screen: &mut Frame, colors: &Colors, renderer: Renderer) {
        let area = screen.area();
        screen.render_widget(Clear, area);
        let block = self.menu_panel("ENTER CODE", colors, renderer == Renderer::Ascii);
        let body = block.inner(area);
        screen.render_widget(block, area);
        self.seed_cursor_targets.borrow_mut().clear();
        self.seed_edit_targets.borrow_mut().clear();
        if body.is_empty() {
            return;
        }
        if !self.seed_import_allowed {
            let lines = wrap_dialogue(
                "Saved pet protected. Start --sample without --save to import a birth code.",
                usize::from(body.width),
            );
            screen.render_widget(
                Paragraph::new(lines.into_iter().map(Line::from).collect::<Vec<_>>())
                    .style(Style::default().fg(colors.ink(2))),
                Rect::new(body.x, body.y, body.width, body.height.saturating_sub(1)),
            );
            let footer = Rect::new(body.x, body.bottom() - 1, body.width, 1);
            screen.render_widget(
                Paragraph::new("Esc: Back").style(Style::default().fg(colors.ink(7))),
                footer,
            );
            self.control_targets
                .borrow_mut()
                .push((footer, Action::Cancel));
            return;
        }
        let text = self.seed_entry.text();
        let width = usize::from(body.width);
        let cursor = self.seed_entry.cursor().min(text.len());
        let start = cursor.saturating_sub(width.saturating_sub(1));
        let row = body.y + u16::from(body.height > 1);
        let mut spans = Vec::new();
        for column in 0..width {
            let index = start + column;
            let glyph = text
                .as_bytes()
                .get(index)
                .copied()
                .map(char::from)
                .unwrap_or(' ');
            let style = Style::default().fg(colors.ink(7));
            let style = if index == cursor {
                style.add_modifier(Modifier::REVERSED)
            } else {
                style
            };
            spans.push(Span::styled(glyph.to_string(), style));
            self.seed_cursor_targets.borrow_mut().push((
                Rect::new(body.x + column as u16, row, 1, 1),
                index.min(text.len()),
            ));
        }
        if body.height > 1 {
            screen.render_widget(
                Paragraph::new(self.controller.as_ref().map_or_else(
                    || "Paste / type code".into(),
                    |p| format!("{} Edit", tiny_button(&p.navigate)),
                ))
                .style(Style::default().fg(colors.ink(2))),
                Rect::new(body.x, body.y, body.width, 1),
            );
        }
        screen.render_widget(
            Paragraph::new(Line::from(spans)),
            Rect::new(body.x, row, body.width, 1),
        );
        if body.height >= 4 {
            let message = self.seed_entry.error().unwrap_or(&self.seed_message);
            screen.render_widget(
                Paragraph::new(frame_label(message, body.width, self.frame / 20))
                    .style(Style::default().fg(colors.ink(7))),
                Rect::new(body.x, row + 1, body.width, 1),
            );
        }
        if body.height >= 6 && body.width >= 17 {
            // Register exactly the painted buttons: no invisible targets in tiny panes.
            let wide = body.width >= 23;
            let buttons = [
                ("[<]", SeedEdit::Left),
                ("[>]", SeedEdit::Right),
                ("[-]", SeedEdit::Down),
                ("[+]", SeedEdit::Up),
                (if wide { "[Clear]" } else { "[Clr]" }, SeedEdit::Clear),
            ];
            let mut x = body.x;
            for (label, edit) in buttons {
                let button = Rect::new(x, body.bottom() - 3, label.len() as u16, 1);
                screen.render_widget(
                    Paragraph::new(label).style(Style::default().fg(colors.ink(7))),
                    button,
                );
                self.seed_edit_targets.borrow_mut().push((button, edit));
                x += button.width + u16::from(wide);
            }
        }
        let rows = body
            .height
            .saturating_sub(u16::from(body.height > 1) + 1)
            .min(if body.height >= 5 { 2 } else { 1 });
        if rows > 0 {
            let footer = Rect::new(body.x, body.bottom() - rows, body.width, rows);
            let hint = if rows == 1 {
                "Esc: Keep pet".into()
            } else {
                self.controller.as_ref().map_or_else(
                    || "Enter: Check\nEsc: Keep pet".into(),
                    |p| {
                        format!(
                            "{} Check\n{} Keep",
                            tiny_button(&p.accept),
                            tiny_button(&p.back)
                        )
                    },
                )
            };
            screen.render_widget(
                Paragraph::new(hint).style(Style::default().fg(colors.ink(7))),
                footer,
            );
            if rows == 2 {
                self.menu_controls(footer);
            } else {
                self.control_targets
                    .borrow_mut()
                    .push((footer, Action::Cancel));
            }
        }
    }

    fn draw_identity(
        &self,
        screen: &mut Frame,
        config: &Config,
        colors: &Colors,
        renderer: Renderer,
    ) {
        let area = screen.area();
        screen.render_widget(Clear, area);
        let block = self.menu_panel("I CODE", colors, renderer == Renderer::Ascii);
        let body = block.inner(area);
        screen.render_widget(block, area);
        if body.is_empty() {
            return;
        }
        let (code, details) = self.identity_content(config);
        let code = code.unwrap_or_else(|| {
            if self.game.genesis_seed.is_none() {
                "NO BIRTH SEED".into()
            } else {
                "NO MATCHING CODE".into()
            }
        });
        // A 17-character code fits in the 18 inner cells of a 20-column watch.
        // Keep every character fixed on screen while only metadata scrolls.
        let chars = code.chars().collect::<Vec<_>>();
        let code_lines = chars
            .chunks(usize::from(body.width.max(1)))
            .map(|part| Line::from(part.iter().collect::<String>()))
            .collect::<Vec<_>>();
        let footer_rows = if body.height >= 5 { 2 } else { 1 };
        let code_rows = (code_lines.len() as u16).min(body.height.saturating_sub(footer_rows));
        screen.render_widget(
            Paragraph::new(code_lines).style(
                Style::default()
                    .fg(colors.ink(7))
                    .add_modifier(Modifier::BOLD),
            ),
            Rect::new(body.x, body.y, body.width, code_rows),
        );
        let available = body
            .height
            .saturating_sub(code_rows)
            .saturating_sub(footer_rows);
        let wrapped = details
            .iter()
            .flat_map(|line| wrap_dialogue(line, usize::from(body.width)))
            .collect::<Vec<_>>();
        self.menu_scroll_limit.set(
            wrapped
                .len()
                .saturating_sub(usize::from(available.max(1)))
                .min(512),
        );
        self.menu_visible_rows.set(usize::from(available));
        self.menu_total_lines.set(wrapped.len());
        let start = self.menu_scroll.min(self.menu_scroll_limit.get());
        screen.render_widget(
            Paragraph::new(
                wrapped
                    .into_iter()
                    .skip(start)
                    .take(usize::from(available))
                    .map(Line::from)
                    .collect::<Vec<_>>(),
            )
            .style(Style::default().fg(colors.ink(2))),
            Rect::new(body.x, body.y + code_rows, body.width, available),
        );
        let footer = Rect::new(
            body.x,
            body.bottom().saturating_sub(footer_rows),
            body.width,
            footer_rows,
        );
        self.menu_footer.set(footer);
        let hint = if footer_rows == 1 {
            "^v Esc".into()
        } else if let Some(pad) = &self.controller {
            format!(
                "{} More\n{} Back",
                tiny_button(&self.controller_navigation(pad)),
                tiny_button(&pad.back)
            )
        } else {
            "Up/Down: More\nK Seed / Esc Back".into()
        };
        screen.render_widget(
            Paragraph::new(hint).style(Style::default().fg(colors.ink(7))),
            footer,
        );
        self.control_targets.borrow_mut().push((
            Rect::new(footer.x, footer.bottom().saturating_sub(1), footer.width, 1),
            Action::Cancel,
        ));
    }

    fn draw_choice_menu(
        &self,
        screen: &mut Frame,
        menu: Menu,
        entries: &[&str],
        config: &Config,
        colors: &Colors,
        ascii: bool,
    ) {
        let bounds = screen.area();
        let labels: Vec<_> = entries
            .iter()
            .enumerate()
            .map(|(index, entry)| {
                let entry = *entry;
                let short = if bounds.width < 32 {
                    if menu == Menu::Care {
                        ["Feed", "Clean", "Rest", "Med", "Back"][index]
                    } else {
                        compact_menu_entry(menu, index).unwrap_or(entry)
                    }
                } else {
                    entry
                };
                let binding = menu_binding(menu, index).map_or(String::new(), |key| {
                    if bounds.width < 32 {
                        format!("{key} ")
                    } else {
                        format!("[{key}] ")
                    }
                });
                format!("{binding}{short}")
            })
            .collect();
        // Keep the full Enter/Back captions when an 18-cell footer fits. A
        // 17-cell body unnecessarily shortened Enter at the supported 20x8 size.
        let minimum_width = bounds.width.saturating_sub(2).min(18);
        let widths: Vec<_> = labels
            .iter()
            .map(|label| {
                (Line::from(label.as_str())
                    .width()
                    .min(usize::from(u16::MAX)) as u16)
                    .saturating_add(4) // two number cells and two focus edges
                    .max(minimum_width)
            })
            .collect();
        let description = self.menu_description(menu);
        let description_rows = u16::from(bounds.height >= 10 && description.is_some());
        let geometry = crate::hud::menu_geometry(
            bounds,
            &widths,
            self.menu_selected,
            2 + description_rows,
            1,
            1,
            entries.len().min(usize::from(u16::MAX)) as u16,
            config.hud_golden_ratio,
        );
        let numbered = crate::hud::menu_window(
            self.menu_selected.saturating_sub(geometry.first),
            geometry.visible,
            9,
        );
        let number_start = geometry.first + numbered.start;
        self.menu_number_start.set(number_start);
        self.menu_number_count.set(numbered.count);
        self.menu_visible_rows.set(geometry.visible);
        crate::hud::paint_menu_panel(screen.buffer_mut(), geometry.panel, colors, ascii);
        screen.render_widget(
            Paragraph::new(self.menu_caption(menu_title(menu), geometry.panel.width))
                .style(Style::default().fg(colors.ink(7)).bg(colors.background)),
            Rect::new(
                geometry.panel.x + 1,
                geometry.panel.y,
                geometry.panel.width.saturating_sub(2),
                1,
            ),
        );
        for item in &geometry.items {
            let selected = item.index == self.menu_selected;
            crate::hud::paint_menu_tile(
                screen.buffer_mut(),
                item.area,
                colors,
                if selected {
                    crate::hud::TileState::Focused
                } else {
                    crate::hud::TileState::Idle
                },
                ascii,
            );
            let number = if (number_start..number_start + numbered.count).contains(&item.index) {
                format!("{} ", item.index - number_start + 1)
            } else {
                "  ".into()
            };
            screen.render_widget(
                Paragraph::new(format!("{number}{}", labels[item.index])).style(if selected {
                    selected_style(colors)
                } else {
                    Style::default().fg(colors.ink(2)).bg(colors.background)
                }),
                Rect::new(
                    item.area.x + 1,
                    item.area.y,
                    item.area.width.saturating_sub(2),
                    geometry.cell_height,
                ),
            );
            self.menu_targets.borrow_mut().push((item.area, item.index));
        }
        let controls = Rect::new(
            geometry.footer.x,
            geometry.footer.y + description_rows,
            geometry.footer.width,
            geometry.footer.height.saturating_sub(description_rows),
        );
        if let Some(description) = description.filter(|_| description_rows > 0) {
            screen.render_widget(
                Paragraph::new(description).style(Style::default().fg(colors.ink(7))),
                Rect::new(
                    geometry.footer.x,
                    geometry.footer.y,
                    geometry.footer.width,
                    1,
                ),
            );
        }
        let hint = self.controller.as_ref().map_or_else(
            || "Enter: OK\nEsc: Back".into(),
            |pad| {
                format!(
                    "{}: OK\n{}: Back",
                    tiny_button(&pad.accept),
                    tiny_button(&pad.back)
                )
            },
        );
        screen.render_widget(
            Paragraph::new(hint).style(Style::default().fg(colors.ink(7))),
            controls,
        );
        self.menu_controls(controls);
        *self.choice_geometry.borrow_mut() = Some((menu, geometry));
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
            self.care_targets.borrow_mut().clear();
            self.control_targets.borrow_mut().clear();
            self.draw_talk_menu(screen, config, colors, renderer);
            return;
        }
        // Opaque menu owns all clicks; never activate care through its background.
        self.care_targets.borrow_mut().clear();
        self.control_targets.borrow_mut().clear();
        if menu == Menu::Identity {
            self.draw_identity(screen, config, colors, renderer);
            return;
        }
        if menu == Menu::Seed {
            self.draw_seed_entry(screen, colors, renderer);
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
            let expression = expressions[self.menu_selected % expressions.len()];
            let stage = PREVIEW_STAGES[self.gallery_stage % PREVIEW_STAGES.len()];
            let area = screen.area();
            screen.render_widget(Clear, area);
            let micro = area.width < 18 || area.height <= 7;
            let title = if micro {
                format!(
                    "{} {}",
                    self.detail_tag(),
                    frame_label(
                        &format!("{stage:?} {expression:?}"),
                        area.width.saturating_sub(6),
                        self.frame / 14
                    )
                )
            } else {
                format!(" SPRITE STUDIO / {} ", self.detail_label())
            };
            let block = self
                .menu_panel(&title, colors, ascii)
                .title_bottom(Line::from(if micro {
                    "<> mood ^v".to_string()
                } else {
                    "PREVIEW / pet unchanged".to_string()
                }));
            let inner = block.inner(area);
            screen.render_widget(block, area);
            let [label, room, controls] = Layout::vertical([
                Constraint::Length(if micro { u16::from(area.height > 7) } else { 2 }),
                Constraint::Min(1),
                Constraint::Length(2),
            ])
            .areas(inner);
            screen.render_widget(
                Paragraph::new(format!(
                    "{:?} / {:?} / {:?}\nPreview only. Your pet is unchanged.",
                    stage,
                    expression,
                    self.effective_style()
                ))
                .style(Style::default().fg(colors.ink(7))),
                label,
            );
            self.set_scene_for_stage(room, Some(stage), SceneSubject::Studio);
            let sprite = match self.view_mode {
                ViewMode::Pixel => {
                    Sprite::animated(stage, expression, self.effective_style(), self.frame)
                        .recolored(self.appearance().body, self.appearance().accent)
                }
                ViewMode::Voxel if self.world_enabled && self.camera_view == CameraView::Room => {
                    Sprite::voxel_with_pose(
                        stage,
                        expression,
                        self.effective_style(),
                        self.frame,
                        (self.camera_yaw(self.preview_yaw()), self.camera_pitch()),
                        false,
                        self.appearance(),
                    )
                }
                ViewMode::Voxel => Sprite::voxel_with_pose(
                    stage,
                    expression,
                    self.effective_style(),
                    self.frame,
                    (self.camera_yaw(self.preview_yaw()), self.camera_pitch()),
                    false,
                    self.appearance(),
                ),
            };
            screen.render_widget(
                RoomWidget {
                    world: Some(&config.world),
                    detail: self.effective_style(),
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
                    face_detail: Some((
                        &self.face_detail,
                        crate::render::DetailClock {
                            frame: self.frame,
                            frames: self.camera_transition_frames,
                            zoom: [self.zoom_percent, self.zoom_target],
                        },
                    )),
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
                    self.controller_navigation(pad),
                    pad.accept,
                    pad.back
                )
            } else {
                "Left/Right: reaction   Up/Down: life stage\nEnter/Space: 8-bit / 16-bit   Esc: return".into()
            };
            screen.render_widget(
                Paragraph::new(hint).style(Style::default().fg(colors.ink(7))),
                controls,
            );
            self.menu_controls(controls);
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
                    "Identity / code",
                    "Import birth code",
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
            Menu::Status => (" STATUS ", vec!["Back"]),
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
                    "Camera / Room",
                    "Camera / Mon body",
                    "Camera / Face close-up",
                    "Zoom in (+)",
                    "Zoom out (-)",
                    "Reset zoom (0)",
                    "Back",
                    "Automatic detail / D toggle",
                    "Orbit left ([)",
                    "Orbit right (])",
                    "Reset orbit / default azimuth",
                    "Pan camera left",
                    "Pan camera right",
                    "Pan camera up",
                    "Pan camera down",
                    "Tilt camera up",
                    "Tilt camera down",
                    "Reset camera pose",
                ],
            ),
            Menu::Help => (" HOW TO PLAY ", vec!["Back"]),
            Menu::Gallery
            | Menu::Talk
            | Menu::Roster
            | Menu::Habitat
            | Menu::Training
            | Menu::Identity
            | Menu::Seed => {
                unreachable!("special menus draw their own view")
            }
        };
        if choice_menu(menu) {
            self.draw_choice_menu(screen, menu, &entries, config, colors, ascii);
            return;
        }
        let modal = screen.area();
        screen.render_widget(Clear, modal);
        let menu_title = if menu == Menu::Graphics {
            if screen.area().width < 24 {
                format!("V {}", self.detail_tag())
            } else {
                format!(
                    " GRAPHICS / {} / {:?} {}% / orbit {}deg ",
                    self.detail_label(),
                    self.camera_view,
                    self.zoom_target,
                    self.orbit_target
                )
            }
        } else if screen.area().width < 24 {
            format!("{} {title}", self.detail_tag())
        } else {
            format!("{} {title}", self.detail_label())
        };
        let block = self.menu_panel(&menu_title, colors, ascii);
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
            lines.push(Line::from(self.controller_description()));
            lines.push(Line::from(""));
        } else if menu == Menu::Help {
            lines.push(Line::from(format!(
                "Global detail: {}",
                self.detail_label()
            )));
            lines.push(Line::from(
                "D toggles Auto detail; Graphics 8/16 chooses Manual.",
            ));
            lines.push(Line::from("A8/A16 means Auto; M8/M16 means Manual."));
            lines.push(Line::from(
                "Viewport, font cells and zoom select one shared scene/face style.",
            ));
            lines.push(Line::from(self.controller_description()));
            if let Some(pad) = &self.controller {
                lines.push(Line::from(format!("{} / focused controller", pad.family)));
                lines.push(Line::from(format!(
                    "{} Select [{}] Choose  [{}] Back",
                    self.controller_navigation(pad),
                    pad.accept,
                    pad.back
                )));
                lines.push(Line::from(format!(
                    "[{}] Next action. Back in the room opens Menu.",
                    pad.next
                )));
                lines.push(Line::from(format!(
                    "{} release (native Inputd)",
                    pad.release
                )));
                if let Some(quit) = &pad.quit {
                    lines.push(Line::from(format!("{quit}: Quit (mapped Ctrl+C)")));
                }
                if let Some(hint) = &self.controller_camera {
                    lines.push(Line::from("In Room/Pet/Face and preview camera views:"));
                    lines.push(Line::from(hint.clone()));
                }
                lines.push(Line::from(
                    "Pad chooses menus/care/camera; never moves the pet.",
                ));
                lines.push(Line::from(
                    "Keyboard shortcuts stay active with a focused pad.",
                ));
            }
            for line in [
                "Feed, clean, rest and play to raise a happy adult.",
                "Watch three arrows, then repeat after they disappear.",
                "F Feed  G Memory  C Clean  R Rest  M Medicine",
                "P Pause/resume  S Save  N New pet (asks first)",
                "T Talk  B Roster preview  H Habitat/items  L Training",
                "L Training: Swim / Flight / Run / Power / Stamina.",
                "Hatch rolls name, stats and aptitude. Care funds training.",
                "Arrows / Tab select; Enter / Space activates.",
                "O / F1 Menu  I Code  K Seed  V Graphics  ? Help",
                "Esc: back/cancel; in the room, opens Home.",
                "Q / Ctrl+C: save and quit, including tiny views.",
                "1..9: activate the numbered visible menu row.",
                "0 resets zoom only; arrows still answer memory.",
                "Click an action/menu row to choose it.",
                "Click the pet: Talk. Right-click: Home/context.",
                "Click footer Choose/Back or O Menu / ? Help.",
                "Wheel: scene zoom; Shift tilts; sideways orbits.",
                "Wheel outside the scene scrolls menu choices.",
                "Menus freeze care time; animation keeps moving.",
                "Compact HUD: *=100. Tiny frames page all needs.",
                "Header E/B/C/A/D: Egg/Baby/Child/Adult/Dead.",
                "P paused, R resting, L live; ILL needs medicine.",
                "Touch [<][>] selects; Ent acts; Esc returns.",
                "PgUp/PgDn pages. Home/End first/last.",
                "Inputd arrows/Enter/Tab/Back operate menus and care.",
                "+ / - or mouse wheel: zoom. 0: reset. Z: camera.",
                "Camera: 4/6/8/2 or Shift+arrows pan; 7/9 orbit.",
                "1/3 or { } tilt; 5 reset pose and zoom.",
                "Roster/Studio: Shift+arrows, braces or Alt+digits.",
                "[ left / ] right: coarse orbit. Graphics has pose controls.",
                "Room/Mon follow world yaw; Face starts front, then [/] orbits.",
                "Camera controls never steer your pet.",
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
        let footer_rows = 2.min(body.height);
        let visible = usize::from(body.height.saturating_sub(footer_rows));
        self.menu_visible_rows.set(visible);
        let mut page = Vec::new();
        if matches!(menu, Menu::Status | Menu::Help) {
            self.menu_total_lines.set(lines.len());
            self.menu_scroll_limit
                .set(lines.len().saturating_sub(visible.max(1)).min(512));
            let start = self.menu_scroll.min(self.menu_scroll_limit.get());
            page.extend(lines.into_iter().skip(start).take(visible));
        } else {
            let window = crate::hud::menu_window(self.menu_selected, entries.len(), visible as u16);
            let visible = window.count;
            let start = window.start;
            self.menu_number_start.set(start);
            self.menu_number_count
                .set(visible.min(entries.len().saturating_sub(start)));
            for (row, (index, entry)) in entries
                .iter()
                .enumerate()
                .skip(start)
                .take(visible)
                .enumerate()
            {
                let selected = index == self.menu_selected;
                let binding = menu_binding(menu, index);
                let short = if body.width < 18 && menu == Menu::Care {
                    ["Feed", "Clean", "Rest", "Med", "Back"][index]
                } else {
                    entry
                };
                let key = binding.map_or(String::new(), |key| {
                    if body.width < 18 {
                        format!("{key} ")
                    } else {
                        format!("[{key}] ")
                    }
                });
                let prefix = format!("{}{} {key}", if selected { ">" } else { " " }, row + 1);
                let short = if body.width < 32 {
                    compact_menu_entry(menu, index).unwrap_or(short)
                } else {
                    short
                };
                let label = short
                    .chars()
                    .take(usize::from(body.width.saturating_sub(prefix.len() as u16)))
                    .collect::<String>();
                let text = format!("{prefix}{label}");
                page.push(
                    Line::from(
                        text.chars()
                            .take(usize::from(body.width))
                            .collect::<String>(),
                    )
                    .style(if selected {
                        selected_style(colors)
                    } else {
                        Style::default().fg(colors.ink(2))
                    }),
                );
                self.menu_targets
                    .borrow_mut()
                    .push((Rect::new(body.x, body.y + row as u16, body.width, 1), index));
            }
        }
        let controls = Rect::new(
            body.x,
            body.bottom().saturating_sub(footer_rows),
            body.width,
            footer_rows,
        );
        let hint = if let Some(pad) = &self.controller {
            if body.width < 32 {
                format!(
                    "{}: OK\n{}: Back",
                    if body.width < 18 {
                        tiny_button(&pad.accept)
                    } else {
                        compact_button(&pad.accept)
                    },
                    if body.width < 18 {
                        tiny_button(&pad.back)
                    } else {
                        compact_button(&pad.back)
                    }
                )
            } else {
                format!(
                    "{} [{}] Choose / 1..9 rows\n[{}] Back / Q Quit / ? Help",
                    self.controller_navigation(pad),
                    pad.accept,
                    pad.back
                )
            }
        } else if body.width < 32 {
            "Enter: OK\nEsc: Back".into()
        } else {
            "Arrows/Tab / Enter: choose / 1..9 rows\nEsc: Back / Q: Quit / ?: Help".into()
        };
        screen.render_widget(
            Paragraph::new(page).style(Style::default().bg(colors.background)),
            Rect::new(
                body.x,
                body.y,
                body.width,
                body.height.saturating_sub(footer_rows),
            ),
        );
        if let Some(description) = self.menu_description(menu) {
            let spare = body
                .height
                .saturating_sub(footer_rows)
                .saturating_sub(self.menu_number_count.get() as u16);
            if spare > 0 && body.width > 0 {
                let lines = wrap_dialogue(description, usize::from(body.width))
                    .into_iter()
                    .take(usize::from(spare))
                    .map(Line::from)
                    .collect::<Vec<_>>();
                let rows = lines.len() as u16;
                screen.render_widget(
                    Paragraph::new(lines).style(Style::default().fg(colors.ink(7))),
                    Rect::new(body.x, controls.y.saturating_sub(rows), body.width, rows),
                );
            }
        }
        screen.render_widget(
            Paragraph::new(hint).style(Style::default().fg(colors.ink(7))),
            controls,
        );
        self.menu_controls(controls);
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
fn selected_birth_seed(seed: u64, id: u8) -> u64 {
    if id >= 16 {
        return seed.max(1);
    }
    let selected = (seed & !0x107) | u64::from(id % 8) | (u64::from(id / 8) << 8);
    // Bit9 retains character0 while avoiding the forbidden zero birth code.
    if selected == 0 {
        512
    } else {
        selected
    }
}
fn selected_style(colors: &Colors) -> Style {
    if colors
        .palette
        .iter()
        .all(|color| *color == ratatui::style::Color::Reset)
    {
        Style::default().add_modifier(Modifier::BOLD | Modifier::REVERSED)
    } else {
        Style::default()
            .fg(colors.ink(0))
            .bg(colors.ink(7))
            .add_modifier(Modifier::BOLD)
    }
}

fn choice_menu(menu: Menu) -> bool {
    matches!(menu, Menu::Home | Menu::Care | Menu::Games | Menu::Graphics)
}

/// Navigate the complete painted grid, including positions outside its current
/// page. Wrap only the requested axis and retain the nearest cross-axis match.
fn spatial_menu_neighbor(
    positions: &[(usize, i32, i32)],
    selected: usize,
    direction: Direction,
) -> usize {
    let Some(&(_, x, y)) = positions.iter().find(|entry| entry.0 == selected) else {
        return selected;
    };
    let horizontal = matches!(direction, Direction::Left | Direction::Right);
    let positive = matches!(direction, Direction::Right | Direction::Down);
    let axis = |px: i32, py: i32| if horizontal { px } else { py };
    let cross = |px: i32, py: i32| if horizontal { py } else { px };
    let origin = axis(x, y);
    let across = cross(x, y);
    let nearest = positions
        .iter()
        .filter(|entry| entry.0 != selected)
        .filter_map(|&(index, px, py)| {
            let delta = i128::from(axis(px, py)) - i128::from(origin);
            let primary = if positive { delta } else { -delta };
            let perpendicular = i128::from(cross(px, py)) - i128::from(across);
            (primary > 0).then_some((
                perpendicular.abs() > primary,
                primary * primary + perpendicular * perpendicular,
                perpendicular.abs(),
                index,
            ))
        })
        .min();
    if let Some((_, _, _, index)) = nearest {
        return index;
    }
    let edge = if positive {
        positions.iter().map(|entry| axis(entry.1, entry.2)).min()
    } else {
        positions.iter().map(|entry| axis(entry.1, entry.2)).max()
    };
    let Some(edge) = edge.filter(|edge| *edge != origin) else {
        return selected;
    };
    positions
        .iter()
        .filter(|entry| entry.0 != selected && axis(entry.1, entry.2) == edge)
        .map(|&(index, px, py)| {
            (
                (i128::from(cross(px, py)) - i128::from(across)).abs(),
                index,
            )
        })
        .min()
        .map_or(selected, |(_, index)| index)
}

fn menu_binding(menu: Menu, index: usize) -> Option<&'static str> {
    match menu {
        Menu::Home => [
            None,
            Some("G"),
            Some("T"),
            Some("B"),
            None,
            Some("V"),
            None,
            Some("?"),
            Some("S"),
            Some("Q"),
            Some("H"),
            Some("L"),
            Some("I"),
            Some("K"),
        ]
        .get(index)
        .copied()
        .flatten(),
        Menu::Care => ["F", "C", "R", "M", "Esc"].get(index).copied(),
        Menu::Games => ["G", "Esc"].get(index).copied(),
        Menu::Graphics => match index {
            15 => Some("+/="),
            16 => Some("-"),
            17 => Some("0"),
            18 => Some("Esc"),
            19 => Some("D"),
            20 => Some("["),
            21 => Some("]"),
            _ => None,
        },
        _ => None,
    }
}

fn menu_title(menu: Menu) -> &'static str {
    match menu {
        Menu::Home => "MAIN MENU",
        Menu::Care => "CARE",
        Menu::Games => "GAMES",
        Menu::Status => "STATUS",
        Menu::Graphics => "GRAPHICS",
        Menu::Help => "HELP / CONTROLS",
        Menu::Gallery => "SPRITE STUDIO",
        Menu::Talk => "TALK",
        Menu::Roster => "ROSTER",
        Menu::Habitat => "HABITAT / ITEMS",
        Menu::Training => "TRAINING",
        Menu::Identity => "I CODE",
        Menu::Seed => "ENTER CODE",
    }
}
fn compact_menu_entry(menu: Menu, index: usize) -> Option<&'static str> {
    match menu {
        Menu::Home => [
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
            "Habitat/items",
            "Training",
            "Identity/code",
            "Import code",
        ]
        .get(index)
        .copied(),
        Menu::Graphics => [
            "Voxel 3D",
            "Pixel sprite",
            "8-bit Manual",
            "16-bit Manual",
            "Half-block",
            "ASCII",
            "Quadrant",
            "Braille",
            "Sprite studio",
            "HUD Auto",
            "HUD Minimal",
            "HUD Full",
            "Room",
            "Mon body",
            "Face close-up",
            "Zoom in",
            "Zoom out",
            "Reset zoom",
            "Back",
            "Auto detail",
            "Orbit left",
            "Orbit right",
            "Reset orbit",
            "Pan left",
            "Pan right",
            "Pan up",
            "Pan down",
            "Tilt up",
            "Tilt down",
            "Reset camera",
        ]
        .get(index)
        .copied(),
        Menu::Games if index == 0 => Some("Memory"),
        _ => None,
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
    let cells = |text: &str| Line::from(text).width();
    let mut out = Vec::new();
    let mut line = String::new();
    for word in text.split_whitespace() {
        if !line.is_empty() && cells(&line) + 1 + cells(word) > width {
            out.push(std::mem::take(&mut line));
        }
        if !line.is_empty() {
            line.push(' ');
        }
        for glyph in word.chars() {
            let glyph = glyph.to_string();
            let glyph = if cells(&glyph) > width {
                "?".to_string()
            } else {
                glyph
            };
            if cells(&line) + cells(&glyph) > width {
                out.push(std::mem::take(&mut line));
            }
            line.push_str(&glyph);
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
        .split(" / ")
        .map(|label| {
            let label = label
                .split_once(": ")
                .filter(|(families, _)| {
                    families
                        .split('+')
                        .all(|family| matches!(family, "Sony" | "Xbox" | "Nintendo" | "Controller"))
                })
                .map_or(label, |(_, binding)| binding);
            label.strip_prefix("Tap ").unwrap_or(label)
        })
        .collect::<Vec<_>>()
        .join("/")
        .replace("D-pad Up", "DU")
        .replace("D-pad Down", "DD")
        .replace("D-pad Left", "DL")
        .replace("D-pad Right", "DR")
        .replace("Square", "[]")
        .replace("Triangle", "^")
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
        let capture = Self;
        crossterm::execute!(io::stdout(), EnableMouseCapture, EnableBracketedPaste)?;
        Ok(capture)
    }
}
impl Drop for MouseCapture {
    fn drop(&mut self) {
        let _ = crossterm::execute!(io::stdout(), DisableBracketedPaste, DisableMouseCapture);
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
                ui.open(Menu::Identity);
                ui.notice = "No save file is written. I Code shows this pet's identity.".into();
            }
        }
        Effect::Continue => {}
    }
    Ok(false)
}
/// Hit-test a left click against the frame at the terminal's current size.
/// Click targets belong to the last drawn frame, but tmux (`window-size
/// latest`) resizes the window to the clicking client before writing the
/// click, and the click can be read ahead of the Resize event. Without a
/// repaint the click lands on the previous size's layout and is lost.
fn click_at_current_size<B: Backend>(
    terminal: &mut Terminal<B>,
    drawn: &mut Size,
    ui: &mut Ui,
    (column, row): (u16, u16),
    paint: impl FnOnce(&Ui, &mut Frame),
    rules: &Rules,
) -> Result<Effect, B::Error> {
    if terminal.size()? != *drawn {
        *drawn = terminal.draw(|screen| paint(ui, screen))?.area.as_size();
    }
    Ok(ui.click(column, row, rules))
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
    ui.seed_import_allowed = store.is_none();
    let mut controller = ControllerMonitor::new(config);
    let mut native = NativeDialogs::new(config);
    let _mouse = MouseCapture::enable()?;
    let mut last_tick = Instant::now();
    let mut last_save = Instant::now();
    let mut last_camera_instant = Instant::now();
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
        if ui.game.challenge.is_some() && matches!(ui.native_request, Some(NativeKind::Thought)) {
            ui.native_request = None;
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
        let now = Instant::now();
        let dt_ms = now.duration_since(last_camera_instant).as_secs_f64() * 1000.0;
        last_camera_instant = now;
        ui.step_camera_realtime(dt_ms);

        let frame =
            (animation.elapsed().as_millis() / u128::from(config.animation_frame_ms)) as usize;
        ui.animate(frame);
        ui.controller = controller.poll();
        ui.controller_camera = controller.camera();
        ui.controller_menu_navigation = controller.menu_navigation();
        ui.controller_status = controller.status().to_owned();
        let mut drawn = terminal
            .draw(|screen| ui.draw(screen, config, renderer, colors, frame, store.is_some()))?
            .area
            .as_size();
        if event::poll(Duration::from_millis(ui.camera_poll_interval_ms(config)))? {
            let incoming = event::read()?;
            let was_paused = ui.game.paused;
            let was_overlay = ui.overlay_open();
            let old_age = ui.game.age_ticks;
            let effect = match incoming {
                Event::Key(key) => ui.handle(key, &config.game),
                Event::Paste(text) => {
                    ui.paste_seed(&text);
                    Effect::Continue
                }
                Event::Mouse(mouse)
                    if ui.camera_scope()
                        && mouse.column >= ui.pet_area.get().x
                        && mouse.column < ui.pet_area.get().right()
                        && mouse.row >= ui.pet_area.get().y
                        && mouse.row < ui.pet_area.get().bottom()
                        && matches!(
                            mouse.kind,
                            MouseEventKind::ScrollUp
                                | MouseEventKind::ScrollDown
                                | MouseEventKind::ScrollLeft
                                | MouseEventKind::ScrollRight
                        ) =>
                {
                    use crate::input::CameraCommand;
                    let command = match mouse.kind {
                        MouseEventKind::ScrollLeft => CameraCommand::Orbit(-1),
                        MouseEventKind::ScrollRight => CameraCommand::Orbit(1),
                        MouseEventKind::ScrollUp
                            if mouse
                                .modifiers
                                .contains(crossterm::event::KeyModifiers::SHIFT) =>
                        {
                            CameraCommand::Tilt(1)
                        }
                        MouseEventKind::ScrollDown
                            if mouse
                                .modifiers
                                .contains(crossterm::event::KeyModifiers::SHIFT) =>
                        {
                            CameraCommand::Tilt(-1)
                        }
                        MouseEventKind::ScrollUp => CameraCommand::Zoom(true),
                        _ => CameraCommand::Zoom(false),
                    };
                    ui.camera_command(command);
                    Effect::Continue
                }
                Event::Mouse(mouse) if mouse.kind == MouseEventKind::ScrollUp => {
                    if ui.menu.is_some() {
                        ui.menu_input(Action::Previous, &config.game)
                    } else if !ui.confirm_reset {
                        ui.zoom_by(true);
                        Effect::Continue
                    } else {
                        Effect::Continue
                    }
                }
                Event::Mouse(mouse) if mouse.kind == MouseEventKind::ScrollDown => {
                    if ui.menu.is_some() {
                        ui.menu_input(Action::Next, &config.game)
                    } else if !ui.confirm_reset {
                        ui.zoom_by(false);
                        Effect::Continue
                    } else {
                        Effect::Continue
                    }
                }
                Event::Mouse(mouse) if mouse.kind == MouseEventKind::Down(MouseButton::Right) => {
                    if ui.menu == Some(Menu::Seed) || ui.seed_pending.is_some() {
                        continue;
                    }
                    ui.open(Menu::Home);
                    ui.native_request = Some(NativeKind::Context);
                    Effect::Continue
                }
                Event::Mouse(mouse) if mouse.kind == MouseEventKind::Down(MouseButton::Left) => {
                    click_at_current_size(
                        terminal,
                        &mut drawn,
                        &mut ui,
                        (mouse.column, mouse.row),
                        |ui, screen| {
                            ui.draw(screen, config, renderer, colors, frame, store.is_some())
                        },
                        &config.game,
                    )?
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
    use ratatui::{backend::TestBackend, style::Color, Terminal};

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
        assert_eq!(ui.menu, Some(Menu::Home));
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
                let heading: String = (0..width).map(|x| buffer[(x, 0)].symbol()).collect();
                assert!(
                    heading.contains(ui.name())
                        && heading.contains(&format!("{:?}", ui.game.stage(&config.game))),
                    "{heading}"
                );
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
            assert!(terminal
                .backend()
                .buffer()
                .content
                .iter()
                .all(|c| c.symbol().is_ascii()));
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
        assert!(text.contains("[Q] Quit"));
        assert!(ui
            .menu_targets
            .borrow()
            .iter()
            .any(|(_, index)| *index == 9));
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
        assert!(text.contains("[Cross] Act"));
        assert!(text.contains("[Circle] Menu"));
        assert!(text.contains("[F] Feed"));
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
        assert_eq!(ui.handle(key(KeyCode::Esc), &config.game), Effect::Continue);
        assert_eq!(ui.menu, Some(Menu::Home));
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
            let expected = if config.characters[index].id == saved.character {
                crate::genetics::Profile::for_game(&saved, &config)
                    .unwrap()
                    .name
            } else {
                config.characters[index].name.clone()
            };
            assert!(text.contains(&expected), "{expected}: {text}");
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
        ui.camera_view = CameraView::Pet;
        ui.camera_from_view = CameraView::Pet;
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
        assert!(text.contains("Ent use") && text.contains("PLACE"), "{text}");
        assert!(
            text.contains("[<][>]") && text.contains("Esc Back"),
            "{text}"
        );
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
        assert!(text.contains("Enter: More"), "{text}");
        assert!(
            text.contains("[<][>]") && text.contains("Back / Tab"),
            "{text}"
        );
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
    use ratatui::{backend::TestBackend, Terminal};
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
                // A two-row head may omit both parentheses at an occluding
                // edge; its two eyes and mouth must still form one text run.
                let core = ["^_^", "o_o"].into_iter().find(|face| text.contains(face));
                assert!(
                    core.is_some(),
                    "{menu:?}: complete native face core missing: {text}"
                );
                let core = core.unwrap();
                assert_eq!(
                    text.contains(&format!("({core}")),
                    text.contains(&format!("{core})")),
                    "{menu:?}: a compact expression must keep both parentheses or neither: {text}"
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
    #[test]
    fn compact_auto_face_uses_the_scene_budget_and_micro_talk_clears_old_sprite_cells() {
        let (mut config, ui) = pet();
        config.camera_view = CameraView::Pet;
        let mut ui = Ui::configured(ui.game, &config);
        draw(&ui, &config, 40, 12, 0);
        assert_eq!(ui.camera().view, CameraView::Face);
        assert_eq!(ui.pet_area.get().height, 6);
        assert!(ui.game.progress.hatched);
        let id = ui.game.character;
        let character = ui.catalogue.iter_mut().find(|c| c.id == id).unwrap();
        character.hatch = "Hi".into();
        character.dialogue = vec!["Hi".into(), "Hi".into()];
        let saved = ui.game.clone();
        ui.open(Menu::Talk);
        ui.native_request = None;
        let colors = Colors::from_config(&config, Depth::Indexed).unwrap();
        let mut terminal = Terminal::new(TestBackend::new(12, 6)).unwrap();
        terminal
            .draw(|f| ui.draw(f, &config, Renderer::Ascii, &colors, 0, false))
            .unwrap();
        let dialogue = ui.dialog_area.get();
        assert!(
            ui.pet_area.get().bottom() <= dialogue.y,
            "thoughts must lie below the pet"
        );
        let buffer = terminal.backend().buffer();
        let row = (dialogue.x..dialogue.right())
            .map(|x| buffer[(x, dialogue.y)].symbol())
            .collect::<String>();
        assert_eq!(
            row, "Hi        ",
            "short dialog must clear any old kaomoji/room glyphs"
        );
        let text = buffer
            .content
            .iter()
            .map(|c| c.symbol())
            .collect::<String>();
        assert!(
            text.contains("Ent:More") && text.contains("Esc:Back"),
            "{text}"
        );
        assert_eq!(ui.game, saved);
    }
    #[test]
    fn landscape_portrait_resize_blends_camera_without_resetting_zoom_or_game() {
        let (mut config, ui) = pet();
        config.camera_view = CameraView::Pet;
        let mut ui = Ui::configured(ui.game, &config);
        ui.hud_mode = HudMode::Minimal;
        ui.game.paused = true;
        let saved = ui.game.clone();
        draw(&ui, &config, 80, 24, 0);
        assert_eq!(ui.camera().view, CameraView::Pet);
        ui.zoom_by(true);
        ui.zoom_by(true);
        ui.animate(config.zoom_transition_frames);
        assert_eq!(ui.zoom_percent, 150);
        let start = ui.frame;
        draw(&ui, &config, 40, 12, start);
        assert_eq!(ui.camera().view, CameraView::Face);
        assert_eq!(ui.camera().from_view, CameraView::Pet);
        assert_eq!(ui.camera().transition_percent, 0);
        ui.animate(start + config.zoom_transition_frames / 2);
        assert!((1..100).contains(&ui.camera().transition_percent));
        ui.animate(start + config.zoom_transition_frames);
        assert_eq!(ui.camera().transition_percent, 100);
        let start = ui.frame;
        draw(&ui, &config, 30, 40, start);
        assert_eq!(ui.camera().view, CameraView::Pet);
        assert_eq!(ui.camera().from_view, CameraView::Face);
        assert_eq!(ui.camera().transition_percent, 0);
        ui.animate(start + config.zoom_transition_frames / 2);
        assert!((1..100).contains(&ui.camera().transition_percent));
        ui.animate(start + config.zoom_transition_frames);
        draw(&ui, &config, 80, 24, ui.frame);
        assert_eq!(ui.zoom_percent, 150);
        assert_eq!(ui.game, saved);
    }
}

// Complete test module for tools/native/mios-tamagotchi/src/ui.rs.
// Run only in real MiOS-DEV. This tests navigation without training or altering the pet.
#[cfg(test)]
mod progression_training_navigation_review {
    use super::*;
    use crate::capabilities::Depth;
    use crossterm::event::{KeyCode, KeyModifiers};
    use ratatui::{backend::TestBackend, Terminal};

    fn key(code: KeyCode) -> KeyEvent {
        KeyEvent::new(code, KeyModifiers::NONE)
    }

    #[test]
    fn every_training_choice_is_visible_and_back_preserves_pet_on_compact_screens() {
        let config = Config::sample().unwrap();
        let mut game = Game::new(&config.game, 711);
        for _ in 0..config.game.hatch_ticks {
            game.tick(&config.game);
        }
        for (width, height) in [(12, 6), (20, 8), (40, 14), (80, 24)] {
            let mut ui = Ui::configured(game.clone(), &config);
            let before = ui.game.clone();
            let colors = Colors::from_config(&config, Depth::Indexed).unwrap();
            let mut terminal = Terminal::new(TestBackend::new(width, height)).unwrap();
            ui.handle(key(KeyCode::Char('l')), &config.game);
            for selected in 0..6 {
                assert_eq!(ui.menu_selected, selected);
                terminal
                    .draw(|frame| ui.draw(frame, &config, Renderer::Ascii, &colors, 0, false))
                    .unwrap();
                let text = terminal
                    .backend()
                    .buffer()
                    .content
                    .iter()
                    .map(|cell| cell.symbol())
                    .collect::<String>();
                let labels = [
                    ("Swim", "Swim"),
                    ("Fly", "Flight"),
                    ("Run", "Run"),
                    ("Pwr", "Power"),
                    ("Stam", "Stamina"),
                    ("Back", "Back"),
                ];
                let (short, long) = labels[selected];
                assert!(
                    text.contains(short) || text.contains(long),
                    "{width}x{height}, choice{selected}: {text}"
                );
                assert_eq!(ui.game, before);
                if selected < 5 {
                    ui.handle(key(KeyCode::Down), &config.game);
                }
            }
            ui.handle(key(KeyCode::Enter), &config.game);
            assert_eq!(ui.menu, None);
            assert_eq!(ui.game, before);
        }
    }
}

#[cfg(test)]
mod memory_thought_interruption_tests {
    use super::*;
    #[test]
    fn automatic_thoughts_cannot_steal_a_live_memory_round() {
        let mut config = Config::sample().unwrap();
        config.floating_thoughts = true;
        let mut game = Game::new(&config.game, 734);
        for _ in 0..config.game.hatch_ticks {
            game.tick(&config.game);
        }
        let mut ui = Ui::configured(game, &config);
        ui.thought_ticks = config.thought_interval_ticks - 1;
        ui.care(Care::Play, &config.game);
        assert!(ui.game.challenge.is_some());
        let before = ui.game.clone();
        ui.simulate_behavior(&config);
        assert!(
            ui.native_request.is_none(),
            "no automatic modal during a memory challenge"
        );
        assert_eq!(ui.game, before);
        ui.game.challenge = None;
        ui.simulate_behavior(&config);
        assert!(
            matches!(ui.native_request, Some(NativeKind::Thought)),
            "idle automatic thoughts still work after the round"
        );
    }
}

#[cfg(test)]
mod portrait_density_tests {
    use super::*;
    use crate::capabilities::Depth;
    use ratatui::{backend::TestBackend, Terminal};
    #[test]
    fn phone_care_values_are_named_in_the_information_pane_before_training() {
        let config = Config::sample().unwrap();
        let mut game = Game::new(&config.game, 913);
        for _ in 0..config.game.hatch_ticks {
            game.tick(&config.game);
        }
        game.paused = true;
        game.fullness = 61;
        game.happiness = 42;
        game.hygiene = 73;
        game.energy = 84;
        game.health = 95;
        let saved = game.clone();
        let mut ui = Ui::configured(game, &config);
        ui.hud_mode = HudMode::Minimal;
        let area = Rect::new(1, 1, 57, 47);
        let info = crate::hud::split(
            area,
            config.hud_golden_ratio,
            config.hud_landscape_columns,
            config.hud_portrait_ratio_percent,
        )
        .info;
        for depth in [Depth::Mono, Depth::Rgb] {
            let colors = Colors::from_config(&config, depth).unwrap();
            for renderer in [
                Renderer::Ascii,
                Renderer::HalfBlock,
                Renderer::Quadrant,
                Renderer::Braille,
            ] {
                for frame in [0, 28, 56] {
                    let mut terminal = Terminal::new(TestBackend::new(59, 49)).unwrap();
                    terminal
                        .draw(|f| ui.draw(f, &config, renderer, &colors, frame, false))
                        .unwrap();
                    let footer = ui.info_footer.get();
                    assert!(footer.y > info.y);
                    let rows = (info.y..footer.y)
                        .map(|y| {
                            (info.x..info.right())
                                .map(|x| terminal.backend().buffer()[(x, y)].symbol())
                                .collect::<String>()
                        })
                        .collect::<Vec<_>>();
                    let care_row = rows.iter().position(|row| row.contains("Food: 61")).expect(
                        "care must be inside the information pane, not only the outer border",
                    );
                    for metric in ["Happy: 42", "Clean: 73", "Energy: 84", "Health: 95"] {
                        assert!(
                            rows[care_row].contains(metric),
                            "{renderer:?} {depth:?}: {rows:?}"
                        );
                    }
                    let training_row = rows
                        .iter()
                        .position(|row| row.contains("Swim"))
                        .expect("phone retains training details too");
                    assert!(care_row < training_row);
                    assert_eq!(ui.game, saved);
                }
            }
        }
    }

    #[test]
    fn tall_views_show_all_skills_needs_currency_and_controls_without_paging_them_away() {
        let config = Config::sample().unwrap();
        let mut game = Game::new(&config.game, 913);
        for _ in 0..config.game.hatch_ticks {
            game.tick(&config.game);
        }
        game.paused = true;
        let saved = game.clone();
        let mut ui = Ui::configured(game, &config);
        ui.hud_mode = HudMode::Minimal;
        let colors = Colors::from_config(&config, Depth::Mono).unwrap();
        for (width, height) in [(30, 40), (40, 50), (153, 17)] {
            let mut terminal = Terminal::new(TestBackend::new(width, height)).unwrap();
            terminal
                .draw(|f| ui.draw(f, &config, Renderer::Ascii, &colors, 0, false))
                .unwrap();
            let text = terminal
                .backend()
                .buffer()
                .content
                .iter()
                .map(|c| c.symbol())
                .collect::<String>();
            for label in ["Swim", "Flight", "Run", "Power", "Stamina", "Menu"] {
                assert!(text.contains(label), "{width}x{height}: {label}: {text}");
            }
            assert!(text.contains("W0") && text.contains("$10"), "{text}");
            assert_eq!(ui.game, saved);
        }
    }
}

#[cfg(test)]
mod direct_navigation_regressions {
    use super::*;
    use crate::capabilities::Depth;
    use crossterm::event::{KeyCode, KeyModifiers};
    use ratatui::{backend::TestBackend, Terminal};

    fn key(code: KeyCode) -> KeyEvent {
        KeyEvent::new(code, KeyModifiers::NONE)
    }
    fn pet() -> (Config, Ui) {
        let config = Config::sample().unwrap();
        let mut game = Game::new(&config.game, 3730);
        for _ in 0..config.game.hatch_ticks {
            game.tick(&config.game);
        }
        game.fullness = 30;
        (config.clone(), Ui::configured(game, &config))
    }
    fn draw(ui: &Ui, config: &Config, width: u16, height: u16) -> String {
        let colors = Colors::from_config(config, Depth::Mono).unwrap();
        let mut terminal = Terminal::new(TestBackend::new(width, height)).unwrap();
        terminal
            .draw(|frame| ui.draw(frame, config, Renderer::Ascii, &colors, 0, false))
            .unwrap();
        terminal
            .backend()
            .buffer()
            .content
            .iter()
            .map(|cell| cell.symbol())
            .collect()
    }
    /// Care open, painted at `stolen` (the client that sized the window last),
    /// and the Back cell as the tapping client sees it at `tapping`.
    fn care_with_back_cell(tapping: (u16, u16)) -> (Config, Ui, (u16, u16)) {
        let (config, mut ui) = pet();
        ui.open(Menu::Home);
        // Numbered choices belong to the painted page, as in a real terminal.
        draw(&ui, &config, tapping.0, tapping.1);
        ui.handle(key(KeyCode::Char('1')), &config.game);
        assert_eq!(ui.menu, Some(Menu::Care));
        let text = draw(&ui, &config, tapping.0, tapping.1);
        let lines: Vec<String> = text
            .chars()
            .collect::<Vec<_>>()
            .chunks(usize::from(tapping.0))
            .map(|line| line.iter().collect())
            .collect();
        let (row, line) = lines
            .iter()
            .enumerate()
            .rev()
            .find(|(_, line)| line.contains("Esc: Back"))
            .unwrap_or_else(|| panic!("no Back hint at {tapping:?}: {text}"));
        let byte = line.find("Esc: Back").unwrap();
        let column = line[..byte].chars().count();
        // The middle of "Esc", where a finger lands.
        (config, ui, (column as u16 + 1, row as u16))
    }

    #[test]
    fn a_tap_read_before_its_resize_hits_the_frame_at_the_current_size() {
        for (stolen, tapping) in [
            ((80, 14), (20, 9)),
            ((123, 30), (20, 9)),
            ((20, 9), (80, 14)),
        ] {
            let (config, mut ui, cell) = care_with_back_cell(tapping);
            let colors = Colors::from_config(&config, Depth::Mono).unwrap();
            let paint = |ui: &Ui, screen: &mut Frame| {
                ui.draw(screen, &config, Renderer::Ascii, &colors, 0, false)
            };
            let mut terminal = Terminal::new(TestBackend::new(stolen.0, stolen.1)).unwrap();
            let mut drawn = terminal
                .draw(|screen| paint(&ui, screen))
                .unwrap()
                .area
                .as_size();
            // tmux resizes the window to the tapping client, then writes the tap.
            terminal.backend_mut().resize(tapping.0, tapping.1);
            let before = ui.game.clone();
            let effect = click_at_current_size(
                &mut terminal,
                &mut drawn,
                &mut ui,
                cell,
                paint,
                &config.game,
            )
            .unwrap();
            assert_eq!(effect, Effect::Continue);
            assert_eq!(
                ui.menu,
                Some(Menu::Home),
                "{stolen:?} -> {tapping:?} at {cell:?}"
            );
            assert_eq!(drawn, Size::new(tapping.0, tapping.1));
            assert_eq!(ui.game, before, "menu navigation must not change the pet");
        }
    }

    #[test]
    fn stale_click_targets_lose_the_tap_without_a_repaint() {
        // The defect the repaint fixes: hit-testing the frame of the previous size.
        let (config, mut ui, cell) = care_with_back_cell((20, 9));
        draw(&ui, &config, 80, 14);
        ui.click(cell.0, cell.1, &config.game);
        assert_eq!(ui.menu, Some(Menu::Care));
    }

    #[test]
    fn back_never_quits_and_nested_back_returns_parent_without_changing_pet() {
        for (width, height) in [(12, 6), (61, 26), (62, 30), (61, 45), (4, 2)] {
            let (config, mut ui) = pet();
            let before = ui.game.clone();
            draw(&ui, &config, width, height);
            assert_eq!(ui.handle(key(KeyCode::Esc), &config.game), Effect::Continue);
            if width >= 12 {
                assert_eq!(ui.menu, Some(Menu::Home));
                ui.handle(key(KeyCode::Enter), &config.game);
                assert_eq!(ui.menu, Some(Menu::Care));
                ui.handle(key(KeyCode::Esc), &config.game);
                assert_eq!(ui.menu, Some(Menu::Home));
                ui.handle(key(KeyCode::Esc), &config.game);
                assert_eq!(ui.menu, None);
            }
            assert_eq!(ui.game, before);
            assert_eq!(
                ui.handle(key(KeyCode::Char('Q')), &config.game),
                Effect::Quit
            );
        }
    }
    #[test]
    fn numbered_menu_and_full_width_mouse_rows_charge_once_and_do_not_click_through() {
        let (config, mut ui) = pet();
        ui.open(Menu::Home);
        draw(&ui, &config, 62, 30);
        ui.handle(key(KeyCode::Char('1')), &config.game);
        assert_eq!(ui.menu, Some(Menu::Care));
        let text = draw(&ui, &config, 62, 30);
        assert!(text.contains("[F] Feed"));
        let before = ui.game.clone();
        let target = ui
            .menu_targets
            .borrow()
            .iter()
            .find(|(_, index)| *index == 0)
            .unwrap()
            .0;
        ui.click(target.right() - 1, target.y, &config.game);
        assert_eq!(ui.game.coins, before.coins - config.game.feed_cost);
        let charged = ui.game.clone();
        let mut repeated = key(KeyCode::Char('1'));
        repeated.kind = KeyEventKind::Repeat;
        ui.handle(repeated, &config.game);
        repeated.kind = KeyEventKind::Release;
        ui.handle(repeated, &config.game);
        assert_eq!(ui.game, charged);
        ui.open(Menu::Home);
        draw(&ui, &config, 62, 30);
        // A point outside the opaque menu must not reach a retained care target.
        ui.care_targets
            .borrow_mut()
            .push((Rect::new(0, 0, 62, 30), 0));
        ui.click(0, 0, &config.game);
        assert_eq!(ui.game, charged);
    }
    #[test]
    fn paused_phone_controls_explain_resume_and_mouse_resume_does_not_feed() {
        for (width, height) in [(12, 6), (61, 26), (62, 30), (61, 45)] {
            let (config, mut ui) = pet();
            ui.hud_mode = HudMode::Minimal;
            ui.game.paused = true;
            ui.controller_status = "Inputd: no controller assigned to this nested session.".into();
            let saved = ui.game.clone();
            let text = draw(&ui, &config, width, height);
            assert!(text.contains("P Resume"), "{width}x{height}: {text}");
            if width > 12 {
                for label in [
                    "F Feed",
                    "G Memory",
                    "C Clean",
                    "M Medicine",
                    "S Save",
                    "N New",
                    "care paused",
                    "Inputd:",
                ] {
                    assert!(text.contains(label), "{width}x{height}: {label}: {text}");
                }
            }
            let target = ui
                .control_targets
                .borrow()
                .iter()
                .find(|(_, action)| *action == Action::Pause)
                .unwrap()
                .0;
            ui.click(target.x, target.y, &config.game);
            let mut resumed = saved;
            resumed.paused = false;
            assert_eq!(ui.game, resumed);
        }
    }

    #[test]
    fn hud_quit_and_paused_save_targets_match_the_printed_captions() {
        for (width, height, paused) in [
            (61, 26, false),
            (61, 26, true),
            (62, 30, false),
            (62, 30, true),
            (61, 45, false),
            (61, 45, true),
        ] {
            let (config, mut ui) = pet();
            ui.hud_mode = HudMode::Minimal;
            ui.game.paused = paused;
            let text = draw(&ui, &config, width, height);
            let before = ui.game.clone();
            for (caption, index) in [("S Save", 6), ("N New", 7)] {
                let position = text.find(caption).unwrap();
                let point = (
                    (position % usize::from(width)) as u16,
                    (position / usize::from(width)) as u16,
                )
                    .into();
                assert!(
                    ui.care_targets
                        .borrow()
                        .iter()
                        .any(|(area, action)| *action == index && area.contains(point)),
                    "{caption}: {text}"
                );
            }
            let target = ui
                .control_targets
                .borrow()
                .iter()
                .find(|(_, action)| *action == Action::Quit)
                .unwrap()
                .0;
            assert!(ui
                .care_targets
                .borrow()
                .iter()
                .all(|(area, _)| area.intersection(target).is_empty()));
            assert_eq!(ui.click(target.x, target.y, &config.game), Effect::Quit);
            assert_eq!(ui.game, before);
        }
    }

    #[test]
    fn watch_help_pages_all_input_methods_and_memory_arrows_keep_their_meaning() {
        let (config, mut ui) = pet();
        ui.controller_status = "Inputd: no controller assigned to this nested session.".into();
        ui.open(Menu::Help);
        let saved = ui.game.clone();
        let mut pages = String::new();
        for page in 0..192 {
            ui.menu_scroll = page;
            pages.push_str(&draw(&ui, &config, 12, 6));
        }
        for label in ["Inputd:", "Click", "Wheel:", "Ctrl+C:", "1..9:", "camera"] {
            assert!(pages.contains(label), "{label}");
        }
        assert_eq!(ui.game, saved);
        ui.menu = None;
        ui.care(Care::Play, &config.game);
        let challenge = ui.game.challenge.as_mut().unwrap();
        challenge.reveal = 0;
        let direction = challenge.pattern[0];
        let code = match direction {
            Direction::Up => KeyCode::Up,
            Direction::Down => KeyCode::Down,
            Direction::Left => KeyCode::Left,
            Direction::Right => KeyCode::Right,
        };
        let before = ui.game.clone();
        let mut repeated = key(code);
        repeated.kind = KeyEventKind::Repeat;
        ui.handle(repeated, &config.game);
        ui.handle(key(KeyCode::Char('1')), &config.game);
        assert_eq!(ui.game, before);
        ui.handle(key(code), &config.game);
        assert_eq!(ui.game.challenge.as_ref().unwrap().entered, 1);
        assert_eq!(ui.handle(key(KeyCode::Esc), &config.game), Effect::Continue);
        assert!(ui.game.challenge.is_none());
        assert!(ui.menu.is_none());
    }
}

#[cfg(test)]
mod global_detail_regressions {
    use super::*;
    use crate::capabilities::Depth;
    use crossterm::event::{KeyCode, KeyModifiers};
    use ratatui::{backend::TestBackend, Terminal};

    fn fixture() -> (Config, Ui) {
        let mut config = Config::sample().unwrap();
        config.presentation.automatic = true;
        config.presentation.min_width = 64;
        config.presentation.min_height = 48;
        config.presentation.hysteresis_percent = 15;
        config.sprite_style = SpriteStyle::EightBit;
        let mut game = Game::new(&config.game, 3734);
        for _ in 0..config.game.hatch_ticks {
            game.tick(&config.game);
        }
        game.age_ticks = config.game.adult_ticks;
        game.paused = true;
        let mut ui = Ui::configured(game, &config);
        ui.hud_mode = HudMode::Minimal;
        ui.view_mode = ViewMode::Voxel;
        (config, ui)
    }
    fn key(code: KeyCode) -> KeyEvent {
        KeyEvent::new(code, KeyModifiers::NONE)
    }
    fn draw(ui: &Ui, config: &Config, width: u16, height: u16) -> Terminal<TestBackend> {
        let colors = Colors::from_config(config, Depth::Indexed).unwrap();
        let mut terminal = Terminal::new(TestBackend::new(width, height)).unwrap();
        terminal
            .draw(|frame| ui.draw(frame, config, Renderer::HalfBlock, &colors, 0, false))
            .unwrap();
        terminal
    }
    fn text(terminal: &Terminal<TestBackend>) -> String {
        terminal
            .backend()
            .buffer()
            .content
            .iter()
            .map(|cell| cell.symbol())
            .collect()
    }

    #[test]
    fn same_pet_changes_global_canvas_with_font_cells_zoom_and_all_menu_views() {
        let (config, mut ui) = fixture();
        let game = ui.game.clone();
        for (width, height, zoom, expected) in [
            (153, 27, 100, SpriteStyle::SixteenBit),
            (76, 13, 100, SpriteStyle::EightBit),
            (153, 27, 100, SpriteStyle::SixteenBit),
            (153, 27, 50, SpriteStyle::EightBit),
            (153, 27, 100, SpriteStyle::SixteenBit),
            (54, 26, 100, SpriteStyle::EightBit),
            (12, 6, 100, SpriteStyle::EightBit),
        ] {
            ui.zoom_percent = zoom;
            ui.zoom_target = zoom;
            for menu in [
                None,
                Some(Menu::Home),
                Some(Menu::Gallery),
                Some(Menu::Roster),
                Some(Menu::Habitat),
            ] {
                ui.menu = menu;
                ui.menu_selected = 0;
                let terminal = draw(&ui, &config, width, height);
                assert_eq!(
                    ui.effective_style(),
                    expected,
                    "{width}x{height} zoom{zoom} {menu:?}"
                );
                assert_eq!(
                    ui.pet_sprite(&config.game, 0).width(),
                    if expected == SpriteStyle::EightBit {
                        24
                    } else {
                        64
                    }
                );
                let mode = ui.detail_tag();
                assert!(
                    text(&terminal).contains(&mode),
                    "{width}x{height} {menu:?}: {}",
                    text(&terminal)
                );
                assert_eq!(ui.game, game);
            }
        }
    }

    #[test]
    fn roster_and_studio_render_the_same_scene_as_the_actual_manual_style() {
        for menu in [Menu::Gallery, Menu::Roster] {
            let (config, mut automatic) = fixture();
            let mut manual = Ui::configured(automatic.game.clone(), &config);
            manual.hud_mode = automatic.hud_mode;
            manual.view_mode = automatic.view_mode;
            automatic.menu = Some(menu);
            manual.menu = Some(menu);
            manual.manual_style(SpriteStyle::SixteenBit);
            let auto_frame = draw(&automatic, &config, 153, 27);
            let manual_frame = draw(&manual, &config, 153, 27);
            assert_eq!(automatic.effective_style(), SpriteStyle::SixteenBit);
            assert_eq!(
                automatic.sprite_style,
                SpriteStyle::EightBit,
                "manual preference is retained"
            );
            assert_eq!(automatic.pet_area.get(), manual.pet_area.get());
            let scene = automatic.pet_area.get();
            for y in scene.y..scene.bottom() {
                for x in scene.x..scene.right() {
                    assert_eq!(
                        auto_frame.backend().buffer()[(x, y)],
                        manual_frame.backend().buffer()[(x, y)],
                        "{menu:?} {x},{y}"
                    );
                }
            }
            assert_eq!(automatic.game, manual.game);
        }
    }

    #[test]
    fn manual_legacy_indices_and_auto_row_are_reachable_without_watch_care_changes() {
        let (config, mut ui) = fixture();
        let game = ui.game.clone();
        ui.open(Menu::Graphics);
        ui.menu_selected = 2;
        ui.handle(key(KeyCode::Enter), &config.game);
        draw(&ui, &config, 153, 27);
        assert!(!ui.automatic_detail());
        assert_eq!(ui.effective_style(), SpriteStyle::EightBit);
        ui.menu_selected = 3;
        ui.handle(key(KeyCode::Enter), &config.game);
        draw(&ui, &config, 12, 6);
        assert!(!ui.automatic_detail());
        assert_eq!(ui.effective_style(), SpriteStyle::SixteenBit);
        ui.open(Menu::Graphics);
        for _ in 0..19 {
            ui.handle(key(KeyCode::Down), &config.game);
        }
        let terminal = draw(&ui, &config, 12, 6);
        assert_eq!(ui.menu_selected, 19);
        assert!(ui
            .menu_targets
            .borrow()
            .iter()
            .any(|(_, index)| *index == 19));
        assert!(text(&terminal).contains("Enter: OK") && text(&terminal).contains("Esc: Back"));
        ui.handle(key(KeyCode::Enter), &config.game);
        assert!(ui.automatic_detail());
        let terminal = draw(&ui, &config, 12, 6);
        assert_eq!(ui.effective_style(), SpriteStyle::EightBit);
        assert!(text(&terminal).contains("A8"));
        ui.handle(key(KeyCode::Char('D')), &config.game);
        assert!(!ui.automatic_detail());
        ui.handle(key(KeyCode::Char('d')), &config.game);
        assert!(ui.automatic_detail());
        assert_eq!(ui.game, game);
    }

    #[test]
    fn resize_hysteresis_retains_mode_inside_the_band_without_changing_simulation() {
        let (config, mut ui) = fixture();
        let game = ui.game.clone();
        draw(&ui, &config, 153, 27);
        assert_eq!(ui.effective_style(), SpriteStyle::SixteenBit);
        for height in [26, 25, 26, 25, 27] {
            draw(&ui, &config, 153, height);
            assert_eq!(ui.effective_style(), SpriteStyle::SixteenBit);
        }
        draw(&ui, &config, 153, 21);
        assert_eq!(ui.effective_style(), SpriteStyle::EightBit);
        draw(&ui, &config, 153, 25);
        assert_eq!(ui.effective_style(), SpriteStyle::EightBit);
        draw(&ui, &config, 153, 27);
        assert_eq!(ui.effective_style(), SpriteStyle::SixteenBit);
        ui.manual_style(SpriteStyle::EightBit);
        assert_eq!(ui.game, game);
    }
}

#[cfg(test)]
mod world_camera_identity_regressions {
    use super::*;
    use crate::capabilities::Depth;
    use crossterm::event::{KeyCode, KeyModifiers};
    use ratatui::{backend::TestBackend, Terminal};

    fn key(code: KeyCode) -> KeyEvent {
        KeyEvent::new(code, KeyModifiers::NONE)
    }
    fn fixture() -> (Config, Ui) {
        let config = Config::sample().unwrap();
        let mut game = Game::new(&config.game, 711);
        game.paused = true;
        let ui = Ui::configured(game, &config);
        (config, ui)
    }
    fn draw(ui: &Ui, config: &Config, renderer: Renderer, width: u16, height: u16) -> String {
        let colors = Colors::from_config(config, Depth::Indexed).unwrap();
        let mut terminal = Terminal::new(TestBackend::new(width, height)).unwrap();
        terminal
            .draw(|frame| ui.draw(frame, config, renderer, &colors, 0, false))
            .unwrap();
        terminal
            .backend()
            .buffer()
            .content
            .iter()
            .map(|cell| cell.symbol())
            .collect()
    }

    #[test]
    fn orbit_crosses_zero_smoothly_and_preserves_game_habitat_rng_and_position() {
        let (config, mut ui) = fixture();
        ui.camera_view = CameraView::Room;
        ui.camera_from_view = CameraView::Room;
        ui.camera_transition_frames = 8;
        ui.orbit_degrees = 350;
        ui.orbit_target = 350;
        ui.orbit_origin = 350;
        ui.orbit_step = 45;
        ui.yaw = 73;
        let game = ui.game.clone();
        let position = ui.position;
        let rng = ui.visual_rng;
        assert_eq!(ui.camera_yaw(ui.yaw), 63);
        ui.handle(key(KeyCode::Char(']')), &config.game);
        assert_eq!(ui.orbit_target, 35);
        assert_eq!(ui.orbit_degrees, 350);
        ui.animate(4);
        assert_eq!(ui.orbit_degrees, 12);
        ui.animate(8);
        assert_eq!(ui.orbit_degrees, 35);
        ui.handle(key(KeyCode::Char('[')), &config.game);
        ui.animate(16);
        assert_eq!(ui.orbit_degrees, 350);
        assert_eq!(ui.game, game);
        assert_eq!(ui.position, position);
        assert_eq!(ui.visual_rng, rng);
        assert_eq!(ui.yaw, 73);
    }

    #[test]
    fn graphics_orbit_and_zoom_resets_are_separate_read_only_actions() {
        let (config, mut ui) = fixture();
        let game = ui.game.clone();
        let rng = ui.visual_rng;
        ui.open(Menu::Graphics);
        ui.menu_selected = 21;
        ui.handle(key(KeyCode::Enter), &config.game);
        let orbit = ui.orbit_target;
        ui.handle(key(KeyCode::Char('+')), &config.game);
        ui.handle(key(KeyCode::Char('0')), &config.game);
        assert_eq!(ui.orbit_target, orbit);
        assert_eq!(ui.zoom_target, 100);
        ui.handle(key(KeyCode::Char('+')), &config.game);
        let zoom = ui.zoom_target;
        ui.menu_selected = 22;
        ui.handle(key(KeyCode::Enter), &config.game);
        assert_eq!(ui.orbit_target, ui.orbit_default);
        assert_eq!(ui.zoom_target, zoom);
        ui.menu_selected = 20;
        ui.handle(key(KeyCode::Enter), &config.game);
        assert_eq!(
            ui.orbit_target,
            (i32::from(ui.orbit_default) - i32::from(ui.orbit_step)).rem_euclid(360) as u16
        );
        assert_eq!(ui.game, game);
        assert_eq!(ui.visual_rng, rng);
    }

    #[test]
    fn orbit_ignores_release_repeat_modified_input_and_reset_confirmation() {
        let (config, mut ui) = fixture();
        let orbit = ui.orbit_target;
        let game = ui.game.clone();
        for kind in [KeyEventKind::Repeat, KeyEventKind::Release] {
            let mut event = key(KeyCode::Char(']'));
            event.kind = kind;
            ui.handle(event, &config.game);
        }
        for modifiers in [
            KeyModifiers::CONTROL,
            KeyModifiers::ALT,
            KeyModifiers::SUPER,
        ] {
            ui.handle(KeyEvent::new(KeyCode::Char(']'), modifiers), &config.game);
        }
        ui.confirm_reset = true;
        ui.handle(key(KeyCode::Char(']')), &config.game);
        assert_eq!(ui.orbit_target, orbit);
        assert_eq!(ui.game, game);
    }

    #[test]
    fn identity_keeps_complete_code_visible_on_watch_and_all_packed_renderers() {
        let (config, mut ui) = fixture();
        let profile = crate::genetics::Profile::for_game(&ui.game, &config).unwrap();
        let game = ui.game.clone();
        let rng = ui.visual_rng;
        ui.handle(key(KeyCode::Char('I')), &config.game);
        assert_eq!(ui.menu, Some(Menu::Identity));
        for renderer in [
            Renderer::Ascii,
            Renderer::HalfBlock,
            Renderer::Quadrant,
            Renderer::Braille,
        ] {
            for (width, height) in [(20, 8), (32, 48), (80, 24), (96, 30)] {
                for scroll in [0, 2, 10, 512] {
                    ui.menu_scroll = scroll;
                    let text = draw(&ui, &config, renderer, width, height);
                    assert!(text.contains(&profile.code), "{width}x{height}: {text}");
                    if renderer == Renderer::Ascii {
                        assert!(text.is_ascii());
                    }
                }
            }
        }
        for (width, height) in [(12, 6), (10, 4), (2, 2)] {
            draw(&ui, &config, Renderer::Ascii, width, height);
            assert_eq!(
                ui.handle(key(KeyCode::Char('q')), &config.game),
                Effect::Quit
            );
        }
        assert_eq!(ui.game, game);
        assert_eq!(ui.visual_rng, rng);
    }

    #[test]
    fn identity_metadata_comes_from_same_birth_profile_and_legacy_never_invents_code() {
        let (config, mut ui) = fixture();
        let profile = crate::genetics::Profile::for_game(&ui.game, &config).unwrap();
        let game = ui.game.clone();
        let rng = ui.visual_rng;
        let (code, details) = ui.identity_content(&config);
        assert_eq!(code.as_deref(), Some(profile.code.as_str()));
        let text = details.join("\n");
        for value in [
            &profile.name,
            &profile.species,
            &profile.archetype,
            &profile.trait_label,
            &profile.trope,
            &profile.bonus_stat,
        ] {
            assert!(text.contains(value));
        }
        for value in profile.likes.iter().chain(&profile.dislikes) {
            assert!(text.contains(value));
        }
        ui.open(Menu::Identity);
        for code in ['p', 'n', 'f', 'c', 'm', 'r', 'g'] {
            ui.handle(key(KeyCode::Char(code)), &config.game);
            assert_eq!(ui.menu, Some(Menu::Identity));
            assert!(!ui.confirm_reset);
        }
        assert_eq!(ui.game, game);
        assert_eq!(ui.visual_rng, rng);
        ui.game.genesis_seed = None;
        assert!(ui.identity_content(&config).0.is_none());
        let text = draw(&ui, &config, Renderer::Ascii, 20, 8);
        assert!(text.contains("No birth seed"), "{text}");
        assert!(!text.contains(&profile.code));
    }

    #[test]
    fn save_free_save_action_opens_identity_without_writing_or_changing_game() {
        let (config, mut ui) = fixture();
        let game = ui.game.clone();
        let rng = ui.visual_rng;
        let effect = ui.handle(key(KeyCode::Char('s')), &config.game);
        assert_eq!(effect, Effect::Save);
        assert!(!apply_effect(effect, &mut ui, None, &mut Instant::now()).unwrap());
        assert_eq!(ui.menu, Some(Menu::Identity));
        assert_eq!(ui.game, game);
        assert_eq!(ui.visual_rng, rng);
    }
}

#[cfg(test)]
mod birth_catalogue_regressions {
    use super::*;
    use crossterm::event::{KeyCode, KeyModifiers};
    fn confirm(ui: &mut Ui, config: &Config, id: u8) {
        ui.viewport.set((80, 24));
        ui.adopt_character = Some(id);
        ui.confirm_reset = true;
        ui.handle(
            KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE),
            &config.game,
        );
    }

    #[test]
    fn selected_birth_codes_preserve_all_original_species_and_zero_boundary() {
        let config = Config::sample().unwrap();
        assert_eq!(selected_birth_seed(1, 0), 512);
        for seed in [1, 256, 257, u64::MAX] {
            for id in 0..16 {
                let selected = selected_birth_seed(seed, id);
                assert_ne!(selected, 0);
                let game = Game::new(&config.game, selected);
                assert_eq!(game.character, id);
                let profile = crate::genetics::Profile::for_game(&game, &config).unwrap();
                assert_eq!(
                    profile
                        .code
                        .parse::<crate::genetics::MonCode>()
                        .unwrap()
                        .seed(),
                    selected
                );
            }
        }
    }

    #[test]
    fn confirmed_adoption_refreshes_generated_name_palette_personality_and_hobby() {
        let config = Config::sample().unwrap();
        for id in 0..16 {
            let mut ui = Ui::configured(Game::new(&config.game, 1), &config);
            confirm(&mut ui, &config, id);
            assert!(!ui.confirm_reset);
            assert_eq!(ui.game.character, id);
            let profile = crate::genetics::Profile::for_game(&ui.game, &config).unwrap();
            let current = ui.current_character().unwrap();
            assert_eq!(current.name, profile.name);
            assert_eq!(current.archetype, profile.archetype);
            assert_eq!(current.hobby, profile.likes.join(" and "));
            assert_eq!(ui.appearance().body, profile.body);
            assert_eq!(ui.appearance().accent, profile.accent);
            assert_eq!(ui.name(), profile.name);
        }
    }

    #[test]
    fn presentation_does_not_refresh_direct_catalogue_fixture_changes() {
        let config = Config::sample().unwrap();
        let mut ui = Ui::configured(Game::new(&config.game, 23), &config);
        ui.game.paused = true;
        let game = ui.game.clone();
        let rng = ui.visual_rng;
        let index = ui
            .catalogue
            .iter()
            .position(|c| c.id == ui.game.character)
            .unwrap();
        ui.catalogue[index].name = "Local fixture".into();
        ui.catalogue[index].accent = 3;
        ui.animate(8);
        ui.pet_sprite(&config.game, 8);
        assert_eq!(ui.current_character().unwrap().name, "Local fixture");
        assert_eq!(ui.appearance().accent, 3);
        assert_eq!(ui.game, game);
        assert_eq!(ui.visual_rng, rng);
    }

    #[test]
    fn extended_roster_selection_does_not_claim_an_unreproducible_birth_code() {
        let mut config = Config::sample().unwrap();
        let mut extra = config.characters[0].clone();
        extra.id = 16;
        extra.name = "Extended fixture".into();
        config.characters.push(extra);
        let mut ui = Ui::configured(Game::new(&config.game, 23), &config);
        confirm(&mut ui, &config, 16);
        assert_eq!(ui.game.character, 16);
        assert_eq!(ui.game.genesis_seed, None);
        assert!(ui.identity_content(&config).0.is_none());
        assert!(ui.current_character().is_some());
    }
}

#[cfg(test)]
mod seed_import_ui_regressions {
    use super::*;
    use crate::capabilities::Depth;
    use crossterm::event::{KeyCode, KeyModifiers};
    use ratatui::{backend::TestBackend, Terminal};
    fn key(code: KeyCode) -> KeyEvent {
        KeyEvent::new(code, KeyModifiers::NONE)
    }
    fn fixture() -> (Config, Ui) {
        let config = Config::sample().unwrap();
        let mut game = Game::new(&config.game, 912);
        for _ in 0..config.game.hatch_ticks {
            game.tick(&config.game);
        }
        let ui = Ui::configured(game, &config);
        (config, ui)
    }
    fn draw(ui: &Ui, config: &Config, w: u16, h: u16, renderer: Renderer) -> String {
        let colors = Colors::from_config(config, Depth::Indexed).unwrap();
        let mut terminal = Terminal::new(TestBackend::new(w, h)).unwrap();
        terminal
            .draw(|f| ui.draw(f, config, renderer, &colors, 0, false))
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
    fn typing_care_and_quit_letters_is_editor_data_then_cancel_preserves_pet_and_rng() {
        let (config, mut ui) = fixture();
        let before = ui.game.clone();
        let rng = ui.visual_rng;
        let position = ui.position;
        ui.handle(key(KeyCode::Char('K')), &config.game);
        for c in "qfpnsgcrm".chars() {
            assert_eq!(
                ui.handle(key(KeyCode::Char(c)), &config.game),
                Effect::Continue
            );
        }
        assert_eq!(ui.seed_entry.text(), "QFPNSGCRM");
        assert_eq!(ui.game, before);
        assert_eq!(ui.visual_rng, rng);
        assert_eq!(ui.position, position);
        ui.handle(key(KeyCode::Esc), &config.game);
        assert!(!ui.confirm_reset);
        assert!(ui.seed_pending.is_none());
        assert_eq!(ui.game, before);
    }
    #[test]
    fn validated_code_requires_separate_confirmation_and_creates_the_exact_paused_birth() {
        let (config, mut ui) = fixture();
        draw(&ui, &config, 20, 8, Renderer::Ascii);
        let before = ui.game.clone();
        let rng = ui.visual_rng;
        let code = MonCode::new(513).unwrap();
        ui.handle(key(KeyCode::Char('K')), &config.game);
        ui.paste_seed(&code.to_string());
        ui.handle(key(KeyCode::Enter), &config.game);
        assert!(ui.confirm_reset);
        assert_eq!(ui.seed_pending, Some(code));
        assert_eq!(ui.game, before);
        assert_eq!(ui.visual_rng, rng);
        let preview = draw(&ui, &config, 20, 8, Renderer::Ascii);
        assert!(preview.contains(&code.compact()), "{preview}");
        assert!(
            preview.contains("Enter: Start") && preview.contains("Esc: Keep"),
            "{preview}"
        );
        ui.handle(key(KeyCode::Enter), &config.game);
        let mut expected = Game::new(&config.game, code.seed());
        expected.paused = true;
        assert_eq!(ui.game, expected);
        assert!(ui.menu.is_none());
        assert!(!ui.confirm_reset);
        assert_eq!(
            crate::genetics::Profile::for_game(&ui.game, &config)
                .unwrap()
                .code,
            code.compact()
        );
    }
    #[test]
    fn cancel_confirmation_and_shrinking_viewport_never_create_an_unrelated_random_pet() {
        let (config, mut ui) = fixture();
        draw(&ui, &config, 20, 8, Renderer::Ascii);
        let before = ui.game.clone();
        let rng = ui.visual_rng;
        ui.open_seed();
        ui.paste_seed(&MonCode::new(177).unwrap().compact());
        ui.handle(key(KeyCode::Enter), &config.game);
        ui.handle(key(KeyCode::Esc), &config.game);
        assert_eq!(ui.game, before);
        assert_eq!(ui.visual_rng, rng);
        assert!(ui.seed_pending.is_none());
        ui.handle(key(KeyCode::Enter), &config.game);
        assert!(ui.confirm_reset);
        draw(&ui, &config, 12, 6, Renderer::Ascii);
        for _ in 0..3 {
            ui.handle(key(KeyCode::Enter), &config.game);
        }
        assert_eq!(ui.game, before);
        assert_eq!(ui.visual_rng, rng);
        assert!(!ui.confirm_reset);
        assert!(ui.seed_pending.is_none());
    }
    #[test]
    fn releases_repeats_modified_keys_and_oversized_paste_do_not_escape_the_editor() {
        let (config, mut ui) = fixture();
        let before = ui.game.clone();
        for kind in [KeyEventKind::Release, KeyEventKind::Repeat] {
            let mut event = key(KeyCode::Char('K'));
            event.kind = kind;
            ui.handle(event, &config.game);
            assert!(ui.menu.is_none());
        }
        ui.open_seed();
        ui.paste_seed(&MonCode::new(512).unwrap().compact());
        let draft = ui.seed_entry.text().to_string();
        ui.paste_seed(&"q".repeat(1_000_000));
        assert_eq!(ui.seed_entry.text(), draft);
        ui.paste_seed("\x1b[2J");
        assert_eq!(ui.seed_entry.text(), draft);
        for kind in [KeyEventKind::Release, KeyEventKind::Repeat] {
            let mut event = key(KeyCode::Char('q'));
            event.kind = kind;
            assert_eq!(ui.handle(event, &config.game), Effect::Continue);
        }
        assert_eq!(ui.seed_entry.text(), draft);
        assert_eq!(ui.game, before);
        assert_eq!(
            ui.handle(
                KeyEvent::new(KeyCode::Char('c'), KeyModifiers::CONTROL),
                &config.game
            ),
            Effect::Quit
        );
    }
    #[test]
    fn saved_pets_are_protected_and_paste_outside_the_editor_is_ignored() {
        let (config, mut ui) = fixture();
        let before = ui.game.clone();
        ui.paste_seed("QFPN");
        assert!(ui.seed_entry.text().is_empty());
        ui.seed_import_allowed = false;
        ui.open_seed();
        ui.paste_seed(&MonCode::new(512).unwrap().compact());
        ui.handle(key(KeyCode::Enter), &config.game);
        assert!(!ui.confirm_reset);
        assert_eq!(ui.game, before);
        let t = draw(&ui, &config, 20, 8, Renderer::Ascii);
        assert!(t.contains("Saved pet"), "{t}");
        ui.handle(key(KeyCode::Esc), &config.game);
        assert_eq!(ui.game, before);
    }
    #[test]
    fn watch_editor_controller_wheel_mouse_and_confirmation_have_their_own_controls() {
        let (config, mut ui) = fixture();
        let before = ui.game.clone();
        ui.controller = Some(ControllerPrompts {
            family: "fixture".into(),
            navigate: "D-pad".into(),
            accept: "A".into(),
            back: "B".into(),
            next: "R1".into(),
            release: "Menu".into(),
            quit: None,
        });
        ui.open(Menu::Home);
        ui.menu_selected = 13;
        ui.menu_input(Action::Accept, &config.game);
        assert_eq!(ui.menu, Some(Menu::Seed));
        ui.handle(key(KeyCode::Up), &config.game);
        assert_eq!(ui.seed_entry.text().len(), 17);
        ui.handle(key(KeyCode::Right), &config.game);
        ui.handle(key(KeyCode::Right), &config.game);
        assert_eq!(ui.seed_entry.cursor(), 4);
        assert_eq!(ui.game, before);
        draw(&ui, &config, 20, 8, Renderer::Ascii);
        let wheel_target = ui.seed_cursor_targets.borrow()[3].0;
        ui.click(wheel_target.x, wheel_target.y, &config.game);
        assert_eq!(ui.seed_entry.cursor(), 3);
        ui.paste_seed(&MonCode::new(512).unwrap().compact());
        for renderer in [
            Renderer::Ascii,
            Renderer::HalfBlock,
            Renderer::Quadrant,
            Renderer::Braille,
        ] {
            let t = draw(&ui, &config, 20, 8, renderer);
            assert!(
                t.contains("M100000000000G0NK") && t.contains("A Check") && t.contains("B Keep"),
                "{t}"
            );
        }
        let (field, _) = ui.seed_cursor_targets.borrow()[3];
        ui.click(field.x, field.y, &config.game);
        assert_eq!(ui.seed_entry.cursor(), 3);
        let accept = ui
            .control_targets
            .borrow()
            .iter()
            .find(|(_, action)| *action == Action::Accept)
            .unwrap()
            .0;
        ui.click(accept.x, accept.y, &config.game);
        assert!(ui.confirm_reset);
        assert_eq!(ui.game, before);
        let t = draw(&ui, &config, 20, 8, Renderer::Ascii);
        assert!(t.contains("A Start") && t.contains("B Keep"), "{t}");
        let cancel = ui
            .control_targets
            .borrow()
            .iter()
            .find(|(_, action)| *action == Action::Cancel)
            .unwrap()
            .0;
        ui.click(cancel.x, cancel.y, &config.game);
        assert_eq!(ui.game, before);
        assert!(!ui.confirm_reset);
    }
    #[test]
    fn modal_scroll_tiny_abort_and_unrelated_reset_cannot_consume_a_hidden_seed() {
        let (config, mut ui) = fixture();
        let before = ui.game.clone();
        let rng = ui.visual_rng;
        draw(&ui, &config, 20, 8, Renderer::Ascii);
        ui.open_seed();
        ui.paste_seed(&MonCode::new(512).unwrap().compact());
        ui.handle(key(KeyCode::Enter), &config.game);
        let draft = ui.seed_entry.text().to_string();
        for action in [
            Action::Next,
            Action::Previous,
            Action::Direction(Direction::Up),
            Action::Direction(Direction::Down),
        ] {
            ui.menu_input(action, &config.game);
            assert_eq!(ui.seed_entry.text(), draft);
            assert_eq!(ui.seed_pending, Some(MonCode::new(512).unwrap()));
        }
        let tiny = draw(&ui, &config, 10, 5, Renderer::Ascii);
        assert!(
            tiny.contains("Esc back") && tiny.contains("^C quit"),
            "{tiny}"
        );
        assert!(!tiny.contains("Q quits"));
        ui.handle(key(KeyCode::Enter), &config.game);
        assert!(!ui.confirm_reset);
        assert!(ui.seed_pending.is_none());
        draw(&ui, &config, 20, 8, Renderer::Ascii);
        ui.handle(key(KeyCode::Esc), &config.game);
        ui.handle(key(KeyCode::Char('n')), &config.game);
        assert!(ui.confirm_reset);
        assert!(ui.seed_pending.is_none());
        assert_eq!(ui.game, before);
        assert_eq!(ui.visual_rng, rng);
        ui.handle(key(KeyCode::Esc), &config.game);
        assert_eq!(ui.game, before);
    }
    #[test]
    fn seed_touch_controls_fit_watch_portrait_and_landscape_without_changing_the_pet() {
        let (config, mut ui) = fixture();
        let before = ui.game.clone();
        let camera = (ui.zoom_target, ui.orbit_target, ui.camera_view);
        let rng = ui.visual_rng;
        ui.open_seed();
        for (w, h) in [(20, 8), (28, 18), (80, 24), (120, 12)] {
            for renderer in [
                Renderer::Ascii,
                Renderer::HalfBlock,
                Renderer::Quadrant,
                Renderer::Braille,
            ] {
                let text = draw(&ui, &config, w, h, renderer);
                assert!(
                    text.contains("[<]")
                        && text.contains("[>]")
                        && text.contains("[-]")
                        && text.contains("[+]"),
                    "{text}"
                );
                assert!(
                    text.contains(if w < 25 { "[Clr]" } else { "[Clear]" }),
                    "{text}"
                );
                let targets = ui.seed_edit_targets.borrow().clone();
                assert_eq!(targets.len(), 5);
                for ((area, _), label) in targets.iter().zip([
                    "[<]",
                    "[>]",
                    "[-]",
                    "[+]",
                    if w < 25 { "[Clr]" } else { "[Clear]" },
                ]) {
                    assert!(area.x >= 1 && area.right() < w && area.y > 2 && area.bottom() < h - 2);
                    let painted: String = text
                        .chars()
                        .skip(usize::from(area.y) * usize::from(w) + usize::from(area.x))
                        .take(usize::from(area.width))
                        .collect();
                    assert_eq!(painted, label);
                }
                let code = MonCode::new(512).unwrap().compact();
                ui.paste_seed(&code);
                let tap = |ui: &mut Ui, index: usize| {
                    let area = targets[index].0;
                    assert_eq!(ui.click(area.x, area.y, &config.game), Effect::Continue);
                };
                tap(&mut ui, 0);
                assert_eq!(ui.seed_entry.cursor(), 16);
                assert_eq!(ui.seed_entry.text(), code);
                tap(&mut ui, 1);
                assert_eq!(ui.seed_entry.cursor(), 17);
                tap(&mut ui, 3);
                assert_ne!(ui.seed_entry.text(), code);
                assert_eq!(ui.seed_entry.cursor(), 16);
                tap(&mut ui, 2);
                assert_eq!(ui.seed_entry.text(), code);
                tap(&mut ui, 4);
                assert!(ui.seed_entry.text().is_empty());
                assert_eq!(ui.seed_entry.cursor(), 0);
                assert!(ui.seed_entry.error().is_none());
                assert_eq!(ui.game, before);
                assert_eq!((ui.zoom_target, ui.orbit_target, ui.camera_view), camera);
                assert_eq!(ui.visual_rng, rng);
            }
        }
    }

    #[test]
    fn touch_editor_checks_cancels_and_starts_the_selected_birth() {
        let (config, mut ui) = fixture();
        let before = ui.game.clone();
        ui.open_seed();
        let expected = MonCode::new(512).unwrap();
        ui.paste_seed(&expected.compact());
        draw(&ui, &config, 20, 8, Renderer::Ascii);
        let plus = ui.seed_edit_targets.borrow()[3].0;
        let minus = ui.seed_edit_targets.borrow()[2].0;
        ui.click(plus.x, plus.y, &config.game);
        assert!(ui.seed_entry.validate().is_none());
        assert_eq!(ui.game, before);
        ui.click(minus.x, minus.y, &config.game);
        assert_eq!(ui.seed_entry.validate(), Some(expected));
        let check = ui
            .control_targets
            .borrow()
            .iter()
            .find(|(_, a)| *a == Action::Accept)
            .unwrap()
            .0;
        ui.click(check.x, check.y, &config.game);
        let preview = draw(&ui, &config, 20, 8, Renderer::Ascii);
        assert!(preview.contains(&expected.compact()), "{preview}");
        let draft = ui.seed_entry.text().to_string();
        // A previously painted editor control is inert behind confirmation.
        ui.click(plus.x, plus.y, &config.game);
        assert_eq!(ui.seed_entry.text(), draft);
        assert_eq!(ui.game, before);
        let cancel = ui
            .control_targets
            .borrow()
            .iter()
            .find(|(_, a)| *a == Action::Cancel)
            .unwrap()
            .0;
        ui.click(cancel.x, cancel.y, &config.game);
        assert_eq!(ui.game, before);
        draw(&ui, &config, 20, 8, Renderer::Ascii);
        let check = ui
            .control_targets
            .borrow()
            .iter()
            .find(|(_, a)| *a == Action::Accept)
            .unwrap()
            .0;
        ui.click(check.x, check.y, &config.game);
        draw(&ui, &config, 20, 8, Renderer::Ascii);
        let start = ui
            .control_targets
            .borrow()
            .iter()
            .find(|(_, a)| *a == Action::Accept)
            .unwrap()
            .0;
        ui.click(start.x, start.y, &config.game);
        assert_eq!(ui.game.genesis_seed, Some(expected.seed()));
        assert!(ui.game.paused);
        assert_eq!(ui.game.age_ticks, 0);
    }

    #[test]
    fn seed_clear_is_press_only_and_hidden_touch_targets_are_discarded() {
        let (config, mut ui) = fixture();
        let before = ui.game.clone();
        ui.open_seed();
        ui.paste_seed(&MonCode::new(512).unwrap().compact());
        let mut clear = KeyEvent::new(KeyCode::Char('u'), KeyModifiers::CONTROL);
        for kind in [KeyEventKind::Release, KeyEventKind::Repeat] {
            clear.kind = kind;
            ui.handle(clear, &config.game);
            assert!(!ui.seed_entry.text().is_empty());
        }
        clear.kind = KeyEventKind::Press;
        ui.handle(clear, &config.game);
        assert!(ui.seed_entry.text().is_empty());
        ui.paste_seed(&MonCode::new(512).unwrap().compact());
        draw(&ui, &config, 20, 8, Renderer::Ascii);
        assert_eq!(ui.seed_edit_targets.borrow().len(), 5);
        let former_clear = ui.seed_edit_targets.borrow()[4].0;
        let draft = ui.seed_entry.text().to_string();
        let cursor = ui.seed_entry.cursor();
        let camera = (ui.zoom_target, ui.orbit_target, ui.camera_view);
        let rng = ui.visual_rng;
        draw(&ui, &config, 12, 5, Renderer::Ascii);
        assert!(ui.seed_edit_targets.borrow().is_empty());
        ui.click(former_clear.x, former_clear.y, &config.game);
        assert_eq!(ui.seed_entry.text(), draft);
        assert_eq!(ui.seed_entry.cursor(), cursor);
        ui.seed_import_allowed = false;
        draw(&ui, &config, 20, 8, Renderer::Ascii);
        assert!(ui.seed_edit_targets.borrow().is_empty());
        ui.click(former_clear.x, former_clear.y, &config.game);
        ui.handle(clear, &config.game);
        assert_eq!(ui.seed_entry.text(), draft);
        assert_eq!(ui.seed_entry.cursor(), cursor);
        assert_eq!((ui.zoom_target, ui.orbit_target, ui.camera_view), camera);
        assert_eq!(ui.visual_rng, rng);
        assert_eq!(ui.game, before);
        assert_eq!(
            ui.handle(
                KeyEvent::new(KeyCode::Char('c'), KeyModifiers::CONTROL),
                &config.game
            ),
            Effect::Quit
        );
    }
    #[test]
    fn every_watch_menu_has_painted_touch_navigation_without_care_or_camera_actions() {
        for menu in [
            Menu::Home,
            Menu::Care,
            Menu::Games,
            Menu::Graphics,
            Menu::Training,
            Menu::Habitat,
            Menu::Roster,
            Menu::Status,
            Menu::Help,
            Menu::Identity,
            Menu::Gallery,
            Menu::Talk,
        ] {
            let (config, mut ui) = fixture();
            let before = ui.game.clone();
            let camera = (ui.zoom_target, ui.orbit_target, ui.camera_view);
            let rng = ui.visual_rng;
            ui.open(menu);
            for (w, h) in [(20, 8), (28, 24), (80, 24)] {
                let screen = draw(&ui, &config, w, h, Renderer::Ascii);
                let footer = ui.menu_footer.get();
                for (action, label) in [(Action::Previous, "[<]"), (Action::Next, "[>]")] {
                    let rect = ui
                        .control_targets
                        .borrow()
                        .iter()
                        .find(|(_, a)| *a == action)
                        .unwrap_or_else(|| panic!("{menu:?} {w}x{h}: {screen}"))
                        .0;
                    let painted: String = screen
                        .chars()
                        .skip(usize::from(rect.y) * usize::from(w) + usize::from(rect.x))
                        .take(usize::from(rect.width))
                        .collect();
                    assert_eq!(painted, label);
                    assert!(footer.contains((rect.x, rect.y).into()));
                }
                let next = ui
                    .control_targets
                    .borrow()
                    .iter()
                    .find(|(_, a)| *a == Action::Next)
                    .unwrap()
                    .0;
                ui.click(next.x, next.y, &config.game);
                if matches!(menu, Menu::Status | Menu::Help | Menu::Identity) {
                    assert_eq!(
                        ui.menu_scroll,
                        ui.menu_scroll_limit.get().min(1),
                        "{menu:?} {w}x{h}"
                    );
                } else {
                    assert_eq!(ui.menu_selected, 1, "{menu:?} {w}x{h}");
                }
                draw(&ui, &config, w, h, Renderer::Ascii);
                let prev = ui
                    .control_targets
                    .borrow()
                    .iter()
                    .find(|(_, a)| *a == Action::Previous)
                    .unwrap()
                    .0;
                ui.click(prev.x, prev.y, &config.game);
                assert_eq!(ui.menu_selected, 0);
                assert_eq!(ui.menu_scroll, 0);
                assert_eq!(ui.game, before);
                assert_eq!((ui.zoom_target, ui.orbit_target, ui.camera_view), camera);
                assert_eq!(ui.visual_rng, rng);
            }
        }
    }

    #[test]
    fn touch_reaches_offscreen_home_choices_and_gallery_stages() {
        let (config, mut ui) = fixture();
        let before = ui.game.clone();
        ui.open(Menu::Home);
        for index in 1..14 {
            draw(&ui, &config, 20, 8, Renderer::Ascii);
            let next = ui
                .control_targets
                .borrow()
                .iter()
                .find(|(_, a)| *a == Action::Next)
                .unwrap()
                .0;
            ui.click(next.x, next.y, &config.game);
            assert_eq!(ui.menu_selected, index);
            assert_eq!(ui.game, before);
        }
        draw(&ui, &config, 20, 8, Renderer::Ascii);
        let accept = ui
            .control_targets
            .borrow()
            .iter()
            .find(|(_, a)| *a == Action::Accept)
            .unwrap()
            .0;
        ui.click(accept.x, accept.y, &config.game);
        assert_eq!(ui.menu, Some(Menu::Seed));
        assert_eq!(ui.game, before);
        ui.handle(key(KeyCode::Esc), &config.game);
        ui.open(Menu::Gallery);
        let original_stage = ui.gallery_stage;
        for (action, expected) in [
            (Action::Direction(Direction::Up), (original_stage + 4) % 5),
            (Action::Direction(Direction::Down), original_stage),
        ] {
            draw(&ui, &config, 20, 8, Renderer::Ascii);
            let rect = ui
                .control_targets
                .borrow()
                .iter()
                .find(|(_, a)| *a == action)
                .unwrap()
                .0;
            ui.click(rect.x, rect.y, &config.game);
            assert_eq!(ui.gallery_stage, expected);
            assert_eq!(ui.game, before);
        }
    }

    #[test]
    fn terminal_paging_keys_are_navigation_only_and_seed_caret_keeps_its_contract() {
        let (config, mut ui) = fixture();
        let before = ui.game.clone();
        let rng = ui.visual_rng;
        ui.open(Menu::Graphics);
        draw(&ui, &config, 20, 8, Renderer::Ascii);
        let page = ui.menu_number_count.get();
        ui.handle(key(KeyCode::PageDown), &config.game);
        assert_eq!(ui.menu_selected, page);
        ui.handle(key(KeyCode::End), &config.game);
        assert_eq!(ui.menu_selected, ui.menu_len(Menu::Graphics) - 1);
        ui.handle(key(KeyCode::PageUp), &config.game);
        assert_eq!(ui.menu_selected, ui.menu_len(Menu::Graphics) - 1 - page);
        ui.handle(key(KeyCode::Home), &config.game);
        assert_eq!(ui.menu_selected, 0);
        for kind in [KeyEventKind::Release, KeyEventKind::Press] {
            let mut event = key(KeyCode::End);
            event.kind = kind;
            if kind == KeyEventKind::Press {
                event.modifiers = KeyModifiers::CONTROL;
            }
            ui.handle(event, &config.game);
            assert_eq!(ui.menu_selected, 0);
        }
        let mut repeat = key(KeyCode::PageDown);
        repeat.kind = KeyEventKind::Repeat;
        ui.handle(repeat, &config.game);
        assert_eq!(ui.menu_selected, page);
        ui.open(Menu::Help);
        ui.handle(key(KeyCode::End), &config.game);
        assert_eq!(ui.menu_scroll, 512);
        ui.handle(key(KeyCode::PageUp), &config.game);
        assert!(ui.menu_scroll < 512);
        ui.handle(key(KeyCode::Home), &config.game);
        assert_eq!(ui.menu_scroll, 0);
        assert_eq!(ui.game, before);
        assert_eq!(ui.visual_rng, rng);
        ui.open_seed();
        ui.paste_seed("512");
        ui.handle(key(KeyCode::Home), &config.game);
        assert_eq!(ui.seed_entry.cursor(), 0);
        ui.handle(key(KeyCode::End), &config.game);
        assert_eq!(ui.seed_entry.cursor(), 3);
    }

    #[test]
    fn compact_touch_controls_keep_mapped_pad_labels_and_no_hidden_targets() {
        let (config, mut ui) = fixture();
        ui.controller = Some(ControllerPrompts {
            family: "Sony / Xbox".into(),
            navigate: "D-pad".into(),
            accept: "Sony: Tap Cross / Xbox: Tap A".into(),
            back: "Sony: Hold Circle / Xbox: Hold B".into(),
            next: "Tap Square / Tap X".into(),
            release: "Hold L3+R3".into(),
            quit: None,
        });
        ui.open(Menu::Home);
        let screen = draw(&ui, &config, 20, 8, Renderer::Ascii);
        assert!(
            screen.contains("Cross/A: OK") && screen.contains("H-O/H-B: Back"),
            "{screen}"
        );
        let old = ui
            .control_targets
            .borrow()
            .iter()
            .find(|(_, a)| *a == Action::Next)
            .unwrap()
            .0;
        let before = ui.game.clone();
        draw(&ui, &config, 8, 4, Renderer::Ascii);
        assert!(ui.control_targets.borrow().is_empty());
        ui.click(old.x, old.y, &config.game);
        assert_eq!(ui.game, before);
        draw(&ui, &config, 20, 8, Renderer::Ascii);
        ui.ask_reset();
        assert!(ui.confirm_reset);
        draw(&ui, &config, 20, 8, Renderer::Ascii);
        assert!(!ui
            .control_targets
            .borrow()
            .iter()
            .any(|(_, a)| matches!(a, Action::Next | Action::Previous)));
    }
    #[test]
    fn information_touch_scroll_stops_at_content_and_reverses_immediately() {
        let (config, mut ui) = fixture();
        for menu in [Menu::Help, Menu::Status, Menu::Identity] {
            ui.open(menu);
            draw(&ui, &config, 20, 8, Renderer::Ascii);
            let limit = ui.menu_scroll_limit.get();
            assert!(limit > 0 && limit < 512);
            for _ in 0..(limit + 8) {
                ui.menu_input(Action::Next, &config.game);
            }
            assert_eq!(ui.menu_scroll, limit);
            let last = draw(&ui, &config, 20, 8, Renderer::Ascii);
            ui.menu_input(Action::Previous, &config.game);
            assert_eq!(ui.menu_scroll, limit - 1);
            let previous = draw(&ui, &config, 20, 8, Renderer::Ascii);
            assert_ne!(last, previous);
            ui.handle(key(KeyCode::End), &config.game);
            assert_eq!(ui.menu_scroll, limit);
        }
        assert_eq!(
            compact_button("Controller+Nintendo: R / Sony: R1 / Xbox: RB"),
            "R/R1/RB"
        );
        assert_eq!(tiny_button("Sony: Hold Circle / Xbox: Tap B"), "H-O/B");
    }
}

#[cfg(test)]
mod console_shell_regressions {
    use super::*;
    use crate::capabilities::Depth;
    use crossterm::event::{KeyCode, KeyModifiers};
    use ratatui::{backend::TestBackend, Terminal};

    const MENUS: [Menu; 13] = [
        Menu::Home,
        Menu::Care,
        Menu::Games,
        Menu::Status,
        Menu::Graphics,
        Menu::Help,
        Menu::Gallery,
        Menu::Talk,
        Menu::Roster,
        Menu::Habitat,
        Menu::Training,
        Menu::Identity,
        Menu::Seed,
    ];
    fn fixture() -> (Config, Ui) {
        let config = Config::sample().unwrap();
        let mut game = Game::new(&config.game, 711);
        for _ in 0..config.game.hatch_ticks {
            game.tick(&config.game);
        }
        let ui = Ui::configured(game, &config);
        (config, ui)
    }
    fn key(code: KeyCode) -> KeyEvent {
        KeyEvent::new(code, KeyModifiers::NONE)
    }
    fn draw(ui: &Ui, config: &Config, width: u16, height: u16, renderer: Renderer) -> Vec<String> {
        let colors = Colors::from_config(config, Depth::Mono).unwrap();
        let mut terminal = Terminal::new(TestBackend::new(width, height)).unwrap();
        terminal
            .draw(|frame| ui.draw(frame, config, renderer, &colors, ui.frame, false))
            .unwrap();
        let buffer = terminal.backend().buffer();
        (0..height)
            .map(|y| (0..width).map(|x| buffer[(x, y)].symbol()).collect())
            .collect()
    }
    fn painted(lines: &[String], rect: Rect) -> String {
        lines[usize::from(rect.y)]
            .chars()
            .skip(usize::from(rect.x))
            .take(usize::from(rect.width))
            .collect()
    }

    #[test]
    fn all_thirteen_menu_shells_resize_in_ascii_mono_with_visible_selection_and_ranges() {
        let (config, mut ui) = fixture();
        let game = ui.game.clone();
        let rng = ui.visual_rng;
        for menu in MENUS {
            ui.open(menu);
            let count = ui.menu_len(menu);
            for selected in [0, count / 2, count - 1] {
                ui.menu_selected = selected;
                for (width, height) in [(20, 8), (32, 18), (80, 24), (20, 8)] {
                    let lines = draw(&ui, &config, width, height, Renderer::Ascii);
                    let text = lines.join("\n");
                    assert!(text.is_ascii(), "{menu:?} {width}x{height}: {text}");
                    assert!(
                        lines[0].starts_with('+') && lines[0].ends_with('+'),
                        "{text}"
                    );
                    assert!(!lines[0].contains("PET ROOM"), "{text}");
                    assert!(ui.care_targets.borrow().is_empty(), "{menu:?}");
                    assert!(
                        !lines[usize::from(height - 1)]
                            .trim_matches(['+', '-', ' '])
                            .is_empty(),
                        "{text}"
                    );
                    let footer = ui.menu_footer.get();
                    for (rect, _) in ui.menu_targets.borrow().iter() {
                        assert!(rect.x >= 1 && rect.right() < width);
                        assert!(
                            rect.y >= 1 && rect.bottom() <= footer.y,
                            "{menu:?}: {rect:?}"
                        );
                    }
                    if matches!(
                        menu,
                        Menu::Home
                            | Menu::Care
                            | Menu::Games
                            | Menu::Graphics
                            | Menu::Talk
                            | Menu::Habitat
                            | Menu::Training
                    ) {
                        let (rect, _) = *ui
                            .menu_targets
                            .borrow()
                            .iter()
                            .find(|(_, index)| *index == selected)
                            .unwrap_or_else(|| {
                                panic!("{menu:?} {width}x{height} selected {selected}: {text}")
                            });
                        assert!(painted(&lines, rect).starts_with('>'), "{text}");
                        assert!(selected >= ui.menu_number_start.get());
                        assert!(selected < ui.menu_number_start.get() + ui.menu_number_count.get());
                        assert!(
                            lines[usize::from(height - 1)].contains(&format!(
                                "{}/{}",
                                selected + 1,
                                count
                            )),
                            "{text}"
                        );
                    }
                }
            }
        }
        assert_eq!(ui.game, game);
        assert_eq!(ui.visual_rng, rng);
    }

    #[test]
    fn essential_navigation_is_static_and_targets_match_painted_rows_across_frames() {
        let (config, mut ui) = fixture();
        ui.controller = Some(ControllerPrompts {
            family: "Controller".into(),
            navigate: "Remapped shoulders".into(),
            accept: "Sony: Hold D-pad Left / Xbox: Hold D-pad Right".into(),
            back: "Sony: Hold Circle / Xbox: Hold B".into(),
            next: "Tap West".into(),
            release: "Hold L3+R3".into(),
            quit: None,
        });
        for menu in MENUS.into_iter().filter(|menu| *menu != Menu::Seed) {
            ui.open(menu);
            let mut previous = None;
            for frame in [0, 140, 940] {
                ui.frame = frame;
                let lines = draw(&ui, &config, 20, 8, Renderer::Ascii);
                let controls = ui.control_targets.borrow().clone();
                let snapshot = controls
                    .iter()
                    .map(|(rect, action)| (*rect, *action, painted(&lines, *rect)))
                    .collect::<Vec<_>>();
                for action in [
                    Action::Previous,
                    Action::Next,
                    Action::Accept,
                    Action::Cancel,
                ] {
                    assert!(
                        snapshot
                            .iter()
                            .any(|(_, a, text)| *a == action && !text.trim().is_empty()),
                        "{menu:?} {snapshot:?}"
                    );
                }
                if let Some(previous) = &previous {
                    assert_eq!(&snapshot, previous, "{menu:?}");
                }
                previous = Some(snapshot);
            }
        }
    }

    #[test]
    fn information_ranges_and_paging_use_actual_visible_lines_after_resize() {
        let (config, mut ui) = fixture();
        for menu in [Menu::Help, Menu::Status, Menu::Identity] {
            ui.open(menu);
            for (width, height) in [(20, 8), (32, 18), (80, 24), (20, 8)] {
                draw(&ui, &config, width, height, Renderer::Ascii);
                ui.handle(key(KeyCode::Home), &config.game);
                let rows = ui.menu_visible_rows.get();
                ui.handle(key(KeyCode::PageDown), &config.game);
                assert_eq!(ui.menu_scroll, rows.min(ui.menu_scroll_limit.get()));
                ui.handle(key(KeyCode::End), &config.game);
                let lines = draw(&ui, &config, width, height, Renderer::Ascii);
                let total = ui.menu_total_lines.get();
                assert!(
                    lines[usize::from(height - 1)].contains(&format!("/{total}")),
                    "{lines:?}"
                );
                let text = lines.join("\n");
                assert!(!text.contains("L0-"));
                ui.handle(key(KeyCode::PageUp), &config.game);
                assert_eq!(
                    ui.menu_scroll,
                    ui.menu_scroll_limit.get().saturating_sub(rows)
                );
            }
        }
    }

    #[test]
    fn old_accept_hitbox_cannot_charge_again_before_repaint_and_native_modal_owns_input() {
        let (config, mut ui) = fixture();
        ui.game.coins = config.game.feed_cost * 3;
        ui.open(Menu::Care);
        draw(&ui, &config, 20, 8, Renderer::Ascii);
        let rect = ui
            .control_targets
            .borrow()
            .iter()
            .find(|(_, a)| *a == Action::Accept)
            .unwrap()
            .0;
        let coins = ui.game.coins;
        ui.click(rect.right() - 1, rect.y, &config.game);
        assert_eq!(ui.game.coins, coins - config.game.feed_cost);
        let after = ui.game.clone();
        ui.click(rect.right() - 1, rect.y, &config.game);
        assert_eq!(ui.game, after);
        ui.native_modal = true;
        draw(&ui, &config, 80, 24, Renderer::Ascii);
        assert!(ui.care_targets.borrow().is_empty());
        assert!(ui.menu_targets.borrow().is_empty());
        assert!(ui.control_targets.borrow().is_empty());
        ui.control_targets
            .borrow_mut()
            .push((Rect::new(0, 0, 80, 24), Action::Pause));
        ui.click(1, 1, &config.game);
        for code in [
            KeyCode::Char('f'),
            KeyCode::Char('p'),
            KeyCode::Char('n'),
            KeyCode::Enter,
        ] {
            ui.handle(key(code), &config.game);
        }
        assert_eq!(ui.game, after);
        assert!(!ui.confirm_reset);
        assert_eq!(
            ui.handle(key(KeyCode::Char('q')), &config.game),
            Effect::Quit
        );
    }

    #[test]
    fn tiny_q_quits_and_escape_backs_out_of_every_inline_menu() {
        let (config, mut ui) = fixture();
        let game = ui.game.clone();
        for menu in MENUS {
            ui.open(menu);
            draw(&ui, &config, 8, 3, Renderer::Ascii);
            assert_eq!(
                ui.handle(key(KeyCode::Char('q')), &config.game),
                Effect::Quit,
                "{menu:?}"
            );
            assert_eq!(ui.handle(key(KeyCode::Esc), &config.game), Effect::Continue);
            assert_ne!(ui.menu, Some(menu), "{menu:?}");
            assert!(!ui.confirm_reset);
            assert_eq!(ui.game, game);
        }
    }

    #[test]
    fn face_orbit_is_relative_on_entry_zoom_preserves_it_and_exit_restores_world_yaw() {
        let (_, mut ui) = fixture();
        let game = ui.game.clone();
        let rng = ui.visual_rng;
        let position = (ui.position, ui.destination);
        ui.camera_transition_frames = 8;
        ui.camera_view = CameraView::Pet;
        ui.camera_from_view = CameraView::Pet;
        ui.yaw = 91;
        ui.orbit_degrees = 350;
        ui.orbit_target = 350;
        ui.orbit_origin = 350;
        ui.change_camera(CameraView::Face, 100);
        ui.animate(8);
        assert_eq!(ui.camera_yaw(ui.yaw), 0);
        ui.orbit_step = 45;
        ui.orbit_by(true);
        ui.animate(12);
        assert_eq!(ui.camera_yaw(ui.yaw), 22);
        ui.animate(16);
        assert_eq!(ui.camera_yaw(ui.yaw), 45);
        ui.zoom_by(true);
        ui.animate(24);
        assert_eq!(ui.camera_yaw(ui.yaw), 45);
        ui.change_camera(CameraView::Room, ui.zoom_target);
        ui.animate(28);
        let middle = ui.camera_yaw(ui.yaw);
        ui.animate(32);
        assert_eq!(ui.camera_yaw(ui.yaw), (ui.yaw + ui.orbit_degrees) % 360);
        assert_ne!(middle, ui.camera_yaw(ui.yaw));
        ui.change_camera(CameraView::Face, ui.zoom_target);
        ui.animate(40);
        assert_eq!(ui.camera_yaw(ui.yaw), 0, "reentry starts front");
        assert_eq!(ui.game, game);
        assert_eq!(ui.visual_rng, rng);
        assert_eq!((ui.position, ui.destination), position);
    }

    #[test]
    fn unicode_wrapping_preserves_text_and_counts_terminal_cells() {
        for width in [2, 3, 8, 18] {
            let lines = wrap_dialogue("éclair 猫猫 hello", width);
            assert!(lines
                .iter()
                .all(|line| Line::from(line.as_str()).width() <= width));
            assert!(!lines.join("").contains('�'));
            assert_eq!(lines.join("").replace(' ', ""), "éclair猫猫hello");
        }
    }
    #[test]
    fn headers_prioritize_name_stage_and_health_before_optional_rendering_details() {
        let (config, mut ui) = fixture();
        ui.game.paused = true;
        ui.game.ill = true;
        let game = ui.game.clone();
        let rng = ui.visual_rng;
        for hud in [HudMode::Auto, HudMode::Minimal, HudMode::Full] {
            ui.hud_mode = hud;
            for (width, height) in [(20, 10), (32, 18), (43, 34), (65, 21), (120, 40)] {
                let lines = draw(&ui, &config, width, height, Renderer::Ascii);
                let heading = &lines[0];
                assert!(
                    heading.contains("Baby") && heading.contains(" P") && heading.contains("ILL"),
                    "{width}x{height} {hud:?}: {heading}"
                );
                assert!(heading.contains(ui.name()), "{heading}");
                assert!(
                    !heading.contains("AUTO") && !heading.contains("PET ROOM"),
                    "{heading}"
                );
                assert!(heading.matches(&ui.detail_tag()).count() <= 1, "{heading}");
            }
        }
        assert_eq!(ui.game, game);
        assert_eq!(ui.visual_rng, rng);
    }

    #[test]
    fn menu_descriptions_use_spare_cells_without_becoming_hidden_actions() {
        let (config, mut ui) = fixture();
        ui.open(Menu::Home);
        draw(&ui, &config, 80, 24, Renderer::Ascii);
        ui.handle(key(KeyCode::Enter), &config.game);
        let lines = draw(&ui, &config, 80, 24, Renderer::Ascii);
        assert!(
            lines
                .iter()
                .any(|line| line.contains("Home > CARE") && !line.contains("AUTO")),
            "{}",
            lines.join("\n")
        );
        let (row, line) = lines
            .iter()
            .enumerate()
            .find(|(_, line)| line.contains("restore fullness"))
            .expect("care description");
        assert_eq!(ui.menu_number_count.get(), 5);
        let game = ui.game.clone();
        let rng = ui.visual_rng;
        let column = line.find("restore fullness").unwrap() as u16;
        ui.click(column, row as u16, &config.game);
        assert_eq!(ui.menu, Some(Menu::Care));
        assert_eq!(ui.menu_selected, 0);
        assert_eq!(ui.game, game);
        assert_eq!(ui.visual_rng, rng);
        let tiny = draw(&ui, &config, 20, 8, Renderer::Ascii).join("\n");
        assert!(!tiny.contains("restore fullness"), "{tiny}");
        assert!(
            tiny.contains("Enter: OK") && tiny.contains("Esc: Back"),
            "{tiny}"
        );
        let back = ui
            .control_targets
            .borrow()
            .iter()
            .find(|(_, action)| *action == Action::Cancel)
            .unwrap()
            .0;
        ui.click(back.x, back.y, &config.game);
        assert_eq!(ui.menu, Some(Menu::Home));
        assert_eq!(ui.game, game);
    }

    #[test]
    fn tiny_room_views_focus_the_mon_then_restore_the_preferred_room_without_state_changes() {
        let (config, mut ui) = fixture();
        ui.camera_view = CameraView::Room;
        ui.camera_from_view = CameraView::Room;
        let game = ui.game.clone();
        let rng = ui.visual_rng;
        draw(&ui, &config, 20, 10, Renderer::Ascii);
        assert_eq!(ui.camera().view, CameraView::Face);
        assert_eq!(ui.camera_view, CameraView::Room);
        draw(&ui, &config, 120, 40, Renderer::Ascii);
        assert_eq!(ui.camera().view, CameraView::Room);
        assert_eq!(ui.camera_view, CameraView::Room);
        assert_eq!(ui.game, game);
        assert_eq!(ui.visual_rng, rng);
    }
    #[test]
    fn saved_pet_menu_descriptions_match_save_on_quit_and_protected_import() {
        let (config, mut ui) = fixture();
        ui.seed_import_allowed = false;
        let game = ui.game.clone();
        let rng = ui.visual_rng;
        ui.open(Menu::Home);
        ui.menu_selected = 9;
        let quit = draw(&ui, &config, 80, 24, Renderer::Ascii).join("\n");
        assert!(quit.contains("Save current progress"), "{quit}");
        assert!(
            !quit.contains("Progress needs an explicit save path"),
            "{quit}"
        );
        ui.menu_selected = 13;
        let import = draw(&ui, &config, 80, 24, Renderer::Ascii).join("\n");
        assert!(import.contains("import is disabled"), "{import}");
        ui.handle(key(KeyCode::Enter), &config.game);
        let protected = draw(&ui, &config, 80, 24, Renderer::Ascii).join("\n");
        assert!(protected.contains("Saved pet protected"), "{protected}");
        ui.handle(key(KeyCode::Char('M')), &config.game);
        assert!(ui.seed_entry.text().is_empty());
        assert_eq!(ui.game, game);
        assert_eq!(ui.visual_rng, rng);
    }

    #[test]
    fn compact_headers_keep_stage_clock_illness_and_one_detail_badge() {
        let (config, mut ui) = fixture();
        ui.game.paused = true;
        ui.game.ill = true;
        let game = ui.game.clone();
        for hud in [HudMode::Auto, HudMode::Minimal, HudMode::Full] {
            ui.hud_mode = hud;
            for (w, h) in [(12, 6), (16, 8)] {
                let lines = draw(&ui, &config, w, h, Renderer::Ascii);
                let title = &lines[0];
                assert!(title.contains("B PI"), "{w}x{h} {hud:?}: {title}");
                assert_eq!(title.matches(&ui.detail_tag()).count(), 1, "{title}");
                assert!(!title.contains("AUTO"), "{title}");
            }
        }
        assert_eq!(ui.game, game);
    }

    #[test]
    fn tiny_room_uses_pet_framing_for_an_unhatched_egg_at_default_zoom() {
        let config = Config::sample().unwrap();
        let game = Game::new(&config.game, 513);
        let mut ui = Ui::configured(game.clone(), &config);
        ui.camera_view = CameraView::Room;
        ui.camera_from_view = CameraView::Room;
        draw(&ui, &config, 20, 10, Renderer::Ascii);
        assert_eq!(ui.camera().view, CameraView::Pet);
        assert_eq!(ui.camera_view, CameraView::Room);
        assert_eq!(ui.game, game);
    }

    #[test]
    fn monochrome_menu_highlight_moves_with_selection_without_an_action() {
        let (config, mut ui) = fixture();
        ui.open(Menu::Home);
        let game = ui.game.clone();
        let colors = Colors::from_config(&config, Depth::Mono).unwrap();
        let mut terminal = Terminal::new(TestBackend::new(80, 24)).unwrap();
        terminal
            .draw(|f| ui.draw(f, &config, Renderer::Ascii, &colors, 0, false))
            .unwrap();
        let target = |ui: &Ui, index| {
            ui.menu_targets
                .borrow()
                .iter()
                .find(|(_, item)| *item == index)
                .expect("painted menu target")
                .0
        };
        let first = target(&ui, 0);
        let idle = target(&ui, 1);
        assert!(terminal.backend().buffer()[(first.x + 1, first.y)]
            .modifier
            .contains(Modifier::REVERSED));
        assert!(!terminal.backend().buffer()[(idle.x + 1, idle.y)]
            .modifier
            .contains(Modifier::REVERSED));
        ui.handle(key(KeyCode::Down), &config.game);
        terminal
            .draw(|f| ui.draw(f, &config, Renderer::Ascii, &colors, 1, false))
            .unwrap();
        assert_ne!(ui.menu_selected, 0, "directional focus must move");
        let next = target(&ui, ui.menu_selected);
        let first = target(&ui, 0);
        assert!(!terminal.backend().buffer()[(first.x + 1, first.y)]
            .modifier
            .contains(Modifier::REVERSED));
        assert!(terminal.backend().buffer()[(next.x + 1, next.y)]
            .modifier
            .contains(Modifier::REVERSED));
        assert_eq!(ui.game, game);
    }
    #[test]
    fn tiny_preview_pixels_and_framing_follow_the_drawn_stage_not_the_live_pet() {
        let (mut config, _) = fixture();
        config.world.enabled = false;
        for menu in [Menu::Roster, Menu::Gallery] {
            for (width, height) in [(20, 10), (12, 6)] {
                let (_, base) = fixture();
                let mut views = [
                    Ui::configured(base.game.clone(), &config),
                    Ui::configured(base.game.clone(), &config),
                ];
                views[1].game.age_ticks = 0;
                let mut scenes = Vec::new();
                for ui in &mut views {
                    ui.camera_view = CameraView::Room;
                    ui.camera_from_view = CameraView::Room;
                    ui.open(menu);
                    ui.gallery_stage = 0;
                    let game = ui.game.clone();
                    let rng = ui.visual_rng;
                    draw(ui, &config, width, height, Renderer::Ascii);
                    ui.frame += ui.camera_transition_frames + 1;
                    let lines = draw(ui, &config, width, height, Renderer::Ascii);
                    let scene = ui.pet_area.get();
                    assert!(scene.width > 0 && scene.height > 0);
                    assert_eq!(
                        ui.camera().view,
                        if menu == Menu::Gallery {
                            CameraView::Pet
                        } else {
                            CameraView::Face
                        },
                        "{menu:?} {width}x{height}"
                    );
                    scenes.push(
                        (scene.y..scene.bottom())
                            .map(|y| painted(&lines, Rect::new(scene.x, y, scene.width, 1)))
                            .collect::<Vec<_>>(),
                    );
                    assert_eq!(ui.game, game);
                    assert_eq!(ui.visual_rng, rng);
                }
                assert_eq!(scenes[0], scenes[1], "{menu:?} {width}x{height}");
                assert!(scenes[0].iter().any(|row| !row.trim().is_empty()));
            }
        }
    }

    #[test]
    fn native_preview_modal_keeps_the_visible_live_scene_context() {
        let (config, mut ui) = fixture();
        ui.camera_view = CameraView::Room;
        ui.camera_from_view = CameraView::Room;
        ui.open(Menu::Gallery);
        ui.gallery_stage = 0;
        ui.native_modal = true;
        let game = ui.game.clone();
        draw(&ui, &config, 20, 10, Renderer::Ascii);
        assert_eq!(ui.camera_stage.get(), Some(Stage::Baby));
        assert_eq!(ui.camera_subject.get(), Some(SceneSubject::Live(0)));
        assert_eq!(ui.camera().view, CameraView::Face);
        assert!(ui.pet_area.get().width > 0);
        assert_eq!(ui.game, game);
    }

    #[test]
    fn automatic_hatch_and_resize_transitions_start_from_the_previous_drawn_stage() {
        let config = Config::sample().unwrap();
        for next in [Rect::new(0, 0, 20, 8), Rect::new(0, 0, 18, 7)] {
            let mut ui = Ui::configured(Game::new(&config.game, 513), &config);
            ui.camera_view = CameraView::Room;
            ui.camera_from_view = CameraView::Room;
            ui.camera_transition_frames = 8;
            ui.set_scene(Rect::new(0, 0, 20, 8));
            assert_eq!(ui.camera().view, CameraView::Pet);
            for _ in 0..config.game.hatch_ticks {
                ui.game.tick(&config.game);
            }
            let game = ui.game.clone();
            let rng = ui.visual_rng;
            ui.frame = 16;
            ui.set_scene(next);
            let start = ui.camera();
            assert_eq!(
                (start.from_view, start.view),
                (CameraView::Pet, CameraView::Face)
            );
            assert_eq!(start.transition_percent, 0);
            ui.frame = 20;
            ui.set_scene(next);
            let middle = ui.camera();
            assert_eq!(
                (middle.from_view, middle.view),
                (CameraView::Pet, CameraView::Face)
            );
            assert!((1..100).contains(&middle.transition_percent));
            ui.frame = 24;
            ui.set_scene(next);
            assert_eq!(ui.camera().transition_percent, 100);
            assert_eq!(ui.game, game);
            assert_eq!(ui.visual_rng, rng);
        }
    }

    #[test]
    fn four_color_menu_selection_keeps_readable_contrast_when_navigation_moves() {
        let (mut config, mut ui) = fixture();
        config.palette = ["#0f380f", "#306230", "#8bac0f", "#9bbc0f"]
            .map(String::from)
            .to_vec();
        config.background = "#0f380f".into();
        let colors = Colors::from_config(&config, Depth::Rgb).unwrap();
        ui.open(Menu::Home);
        let game = ui.game.clone();
        let mut terminal = Terminal::new(TestBackend::new(80, 24)).unwrap();
        let luminance = |color: ratatui::style::Color| {
            let ratatui::style::Color::Rgb(r, g, b) = color else {
                panic!("RGB fixture changed color depth");
            };
            let linear = |v: u8| {
                let v = f64::from(v) / 255.0;
                if v <= 0.04045 {
                    v / 12.92
                } else {
                    ((v + 0.055) / 1.055).powf(2.4)
                }
            };
            0.2126 * linear(r) + 0.7152 * linear(g) + 0.0722 * linear(b)
        };
        for (index, label) in [(0, "Care"), (1, "Games")] {
            terminal
                .draw(|f| ui.draw(f, &config, Renderer::Ascii, &colors, 0, false))
                .unwrap();
            let buffer = terminal.backend().buffer();
            assert_eq!(ui.menu_selected, index);
            let target = ui
                .menu_targets
                .borrow()
                .iter()
                .find(|(_, item)| *item == index)
                .expect("selected painted target")
                .0;
            let line = (target.x..target.right())
                .map(|x| buffer[(x, target.y)].symbol())
                .collect::<String>();
            let column = target.x + line.find(label).expect("selected label is visible") as u16;
            let cell = &buffer[(column, target.y)];
            let (a, b) = (luminance(cell.fg), luminance(cell.bg));
            let contrast = (a.max(b) + 0.05) / (a.min(b) + 0.05);
            assert!(contrast >= 4.5, "{label}: selected contrast {contrast}");
            if index == 1 {
                let previous = ui
                    .menu_targets
                    .borrow()
                    .iter()
                    .find(|(_, item)| *item == 0)
                    .expect("previous painted target")
                    .0;
                assert_ne!(buffer[(previous.x + 1, previous.y)].bg, cell.bg);
            }
            ui.handle(key(KeyCode::Tab), &config.game);
        }
        assert_eq!(ui.game, game);
    }

    #[test]
    fn tiny_preview_subject_switches_use_their_own_framing_on_the_first_frame() {
        let config = Config::sample().unwrap();
        for live_egg in [false, true] {
            for menu in [Menu::Roster, Menu::Gallery] {
                let (_, base) = fixture();
                let mut ui = Ui::configured(base.game, &config);
                if live_egg {
                    ui.game.age_ticks = 0;
                }
                ui.camera_view = CameraView::Room;
                ui.camera_from_view = CameraView::Room;
                ui.camera_transition_frames = 8;
                let live = if live_egg {
                    CameraView::Pet
                } else {
                    CameraView::Face
                };
                draw(&ui, &config, 20, 10, Renderer::Ascii);
                assert_eq!(ui.camera().view, live);
                let game = ui.game.clone();
                let rng = ui.visual_rng;
                ui.frame = 16;
                ui.open(menu);
                ui.gallery_stage = 0;
                draw(&ui, &config, 20, 10, Renderer::Ascii);
                let preview = if menu == Menu::Gallery {
                    CameraView::Pet
                } else {
                    CameraView::Face
                };
                let camera = ui.camera();
                assert_eq!(
                    (camera.from_view, camera.view, camera.transition_percent),
                    (preview, preview, 100),
                    "{live_egg} {menu:?}: entry"
                );
                assert!(ui.camera_resize.get().is_none());
                if menu == Menu::Gallery {
                    // A life-stage change within the same Studio subject still eases.
                    ui.gallery_stage = 1;
                    ui.frame += 1;
                    draw(&ui, &config, 20, 10, Renderer::Ascii);
                    let camera = ui.camera();
                    assert_eq!(
                        (camera.from_view, camera.view, camera.transition_percent),
                        (CameraView::Pet, CameraView::Face, 0)
                    );
                }
                ui.handle(key(KeyCode::Esc), &config.game);
                draw(&ui, &config, 20, 10, Renderer::Ascii);
                let camera = ui.camera();
                assert_eq!(
                    (camera.from_view, camera.view, camera.transition_percent),
                    (live, live, 100),
                    "{live_egg} {menu:?}: return"
                );
                assert!(ui.camera_resize.get().is_none());
                assert_eq!(ui.game, game);
                assert_eq!(ui.visual_rng, rng);
            }
        }
    }

    #[test]
    fn new_pet_subject_clears_the_dead_pets_face_framing_before_its_first_draw() {
        let (config, mut ui) = fixture();
        ui.game.health = 0;
        ui.camera_view = CameraView::Room;
        ui.camera_from_view = CameraView::Room;
        draw(&ui, &config, 20, 10, Renderer::Ascii);
        assert_eq!(ui.game.stage(&config.game), Stage::Dead);
        assert_eq!(ui.camera().view, CameraView::Face);
        let previous = ui.camera_subject.get();
        ui.frame = 16;
        ui.handle(key(KeyCode::Char('n')), &config.game);
        assert!(ui.confirm_reset);
        ui.handle(key(KeyCode::Enter), &config.game);
        assert!(!ui.confirm_reset);
        assert_eq!(ui.game.stage(&config.game), Stage::Egg);
        draw(&ui, &config, 20, 10, Renderer::Ascii);
        assert_ne!(ui.camera_subject.get(), previous);
        let camera = ui.camera();
        assert_eq!(
            (camera.from_view, camera.view, camera.transition_percent),
            (CameraView::Pet, CameraView::Pet, 100)
        );
        assert!(ui.camera_resize.get().is_none());
    }

    #[test]
    fn changing_subject_discards_a_manual_camera_blend_but_new_requests_still_ease() {
        let (config, mut ui) = fixture();
        ui.camera_view = CameraView::Room;
        ui.camera_from_view = CameraView::Room;
        ui.camera_transition_frames = 8;
        draw(&ui, &config, 80, 32, Renderer::Ascii);
        let game = ui.game.clone();
        let rng = ui.visual_rng;
        ui.frame = 16;
        ui.change_camera(CameraView::Face, 100);
        assert_eq!(ui.camera().transition_percent, 0);
        ui.open(Menu::Roster);
        draw(&ui, &config, 80, 32, Renderer::Ascii);
        let camera = ui.camera();
        assert_eq!(
            (camera.from_view, camera.view, camera.transition_percent),
            (CameraView::Face, CameraView::Face, 100)
        );
        // A new camera request belongs to the displayed subject.
        ui.change_camera(CameraView::Pet, 100);
        draw(&ui, &config, 80, 32, Renderer::Ascii);
        let camera = ui.camera();
        assert_eq!(
            (camera.from_view, camera.view, camera.transition_percent),
            (CameraView::Face, CameraView::Pet, 0)
        );
        let next = ui.frame + 4;
        ui.animate(next);
        assert!((1..100).contains(&ui.camera().transition_percent));
        assert_eq!(ui.game, game);
        assert_eq!(ui.visual_rng, rng);
    }

    #[test]
    fn four_color_live_dock_keeps_all_care_labels_readable_when_selection_moves() {
        let (mut config, mut ui) = fixture();
        config.palette = ["#0f380f", "#306230", "#8bac0f", "#9bbc0f"]
            .map(String::from)
            .to_vec();
        config.background = "#0f380f".into();
        let colors = Colors::from_config(&config, Depth::Rgb).unwrap();
        ui.hud_mode = HudMode::Full;
        let game = ui.game.clone();
        let mut terminal = Terminal::new(TestBackend::new(80, 32)).unwrap();
        let luminance = |color: ratatui::style::Color| {
            let ratatui::style::Color::Rgb(r, g, b) = color else {
                panic!("RGB fixture changed color depth");
            };
            let linear = |v: u8| {
                let v = f64::from(v) / 255.0;
                if v <= 0.04045 {
                    v / 12.92
                } else {
                    ((v + 0.055) / 1.055).powf(2.4)
                }
            };
            0.2126 * linear(r) + 0.7152 * linear(g) + 0.0722 * linear(b)
        };
        for _ in 0..2 {
            terminal
                .draw(|f| ui.draw(f, &config, Renderer::Ascii, &colors, 0, false))
                .unwrap();
            let buffer = terminal.backend().buffer();
            let targets = ui.care_targets.borrow();
            assert_eq!(targets.len(), 8);
            let selected = targets
                .iter()
                .find(|(_, index)| *index == ui.selected)
                .unwrap()
                .0;
            for (rect, index) in targets.iter() {
                let cell = &buffer[(rect.x, rect.y)];
                assert_eq!(cell.symbol(), "[");
                let (a, b) = (luminance(cell.fg), luminance(cell.bg));
                let contrast = (a.max(b) + 0.05) / (a.min(b) + 0.05);
                assert!(contrast >= 4.5, "dock{index}: contrast{contrast}");
                if *index != ui.selected {
                    assert_ne!(cell.bg, buffer[(selected.x, selected.y)].bg);
                }
            }
            drop(targets);
            let previous_selection = ui.selected;
            ui.handle(key(KeyCode::Tab), &config.game);
            assert_ne!(ui.selected, previous_selection);
        }
        assert_eq!(ui.game, game);
    }

    #[test]
    fn subject_camera_cut_includes_voxel_yaw_and_roster_cursor_keeps_new_camera_requests() {
        let (config, mut ui) = fixture();
        ui.camera_view = CameraView::Pet;
        ui.camera_from_view = CameraView::Pet;
        ui.camera_transition_frames = 8;
        ui.yaw = 91;
        draw(&ui, &config, 80, 32, Renderer::Ascii);
        let game = ui.game.clone();
        let rng = ui.visual_rng;
        let world_yaw = (ui.yaw + ui.orbit_degrees) % 360;
        ui.frame = 16;
        ui.change_camera(CameraView::Face, 100);
        assert_ne!(ui.camera_yaw(ui.yaw), 0);
        ui.open(Menu::Roster);
        draw(&ui, &config, 80, 32, Renderer::Ascii);
        assert_eq!(ui.camera().transition_percent, 100);
        assert_eq!(ui.camera_yaw(ui.yaw), 0);
        ui.change_camera(CameraView::Pet, 100);
        ui.handle(key(KeyCode::Down), &config.game);
        draw(&ui, &config, 80, 32, Renderer::Ascii);
        assert_eq!(ui.camera().transition_percent, 0);
        ui.animate(20);
        assert!((1..100).contains(&ui.camera().transition_percent));
        assert!((1..world_yaw).contains(&ui.camera_yaw(ui.yaw)));
        ui.handle(key(KeyCode::Esc), &config.game);
        draw(&ui, &config, 80, 32, Renderer::Ascii);
        assert_eq!(ui.camera_yaw(ui.yaw), world_yaw);
        assert_eq!(ui.game, game);
        assert_eq!(ui.visual_rng, rng);
    }

    #[test]
    fn four_color_navigation_hints_stay_readable_in_live_and_menu_layouts() {
        let (mut config, mut ui) = fixture();
        config.palette = ["#0f380f", "#306230", "#8bac0f", "#9bbc0f"]
            .map(String::from)
            .to_vec();
        config.background = "#0f380f".into();
        let colors = Colors::from_config(&config, Depth::Rgb).unwrap();
        let game = ui.game.clone();
        let luminance = |color: ratatui::style::Color| {
            let ratatui::style::Color::Rgb(r, g, b) = color else {
                panic!("RGB fixture changed depth");
            };
            let linear = |v: u8| {
                let v = f64::from(v) / 255.0;
                if v <= 0.04045 {
                    v / 12.92
                } else {
                    ((v + 0.055) / 1.055).powf(2.4)
                }
            };
            0.2126 * linear(r) + 0.7152 * linear(g) + 0.0722 * linear(b)
        };
        let mut checked = 0;
        for menu in [
            None,
            Some(Menu::Home),
            Some(Menu::Graphics),
            Some(Menu::Gallery),
        ] {
            for (width, height) in [(20, 10), (80, 32)] {
                ui.menu = menu;
                ui.hud_mode = HudMode::Full;
                let mut terminal = Terminal::new(TestBackend::new(width, height)).unwrap();
                terminal
                    .draw(|f| ui.draw(f, &config, Renderer::Ascii, &colors, 0, false))
                    .unwrap();
                let buffer = terminal.backend().buffer();
                let mut case_checked = 0;
                for y in 0..height {
                    let line = (0..width)
                        .map(|x| buffer[(x, y)].symbol())
                        .collect::<String>();
                    for label in [
                        "O Menu",
                        "Esc Back",
                        "Esc: Back",
                        "Enter: OK",
                        "Esc: return",
                        "Esc: back",
                    ] {
                        if let Some(x) = line.find(label) {
                            let cell = &buffer[(x as u16, y)];
                            let (a, b) = (luminance(cell.fg), luminance(cell.bg));
                            let contrast = (a.max(b) + 0.05) / (a.min(b) + 0.05);
                            assert!(
                                contrast >= 4.5,
                                "{menu:?} {width}x{height} {label}: contrast{contrast}"
                            );
                            checked += 1;
                            case_checked += 1;
                        }
                    }
                }
                assert!(
                    case_checked > 0,
                    "{menu:?} {width}x{height}: no visible hint inspected"
                );
            }
        }
        assert!(checked >= 8);
        assert_eq!(ui.game, game);
    }
}

// task3755 mid-blend continuity, same-frame heading and the information
// footer background. Presentation only: no case may touch the pet.
#[cfg(test)]
mod camera_continuity_regressions {
    use super::*;
    use crate::capabilities::Depth;
    use crossterm::event::{KeyCode, KeyModifiers};
    use ratatui::{backend::TestBackend, style::Color, Terminal};

    fn fixture() -> (Config, Ui) {
        let config = Config::sample().unwrap();
        let mut game = Game::new(&config.game, 513);
        for _ in 0..config.game.hatch_ticks {
            game.tick(&config.game);
        }
        let mut ui = Ui::configured(game, &config);
        ui.camera_view = CameraView::Room;
        ui.camera_from_view = CameraView::Room;
        ui.camera_transition_frames = 8;
        (config, ui)
    }
    fn draw(ui: &Ui, config: &Config, width: u16, height: u16) -> Vec<String> {
        let colors = Colors::from_config(config, Depth::Mono).unwrap();
        let mut terminal = Terminal::new(TestBackend::new(width, height)).unwrap();
        terminal
            .draw(|frame| ui.draw(frame, config, Renderer::Ascii, &colors, ui.frame, false))
            .unwrap();
        let buffer = terminal.backend().buffer();
        (0..height)
            .map(|y| (0..width).map(|x| buffer[(x, y)].symbol()).collect())
            .collect()
    }
    /// Register a scene and prove the drawn mix does not jump across it.
    fn rescene(ui: &mut Ui, frame: usize, area: Rect) -> [u16; 3] {
        ui.frame = frame;
        let before = ui.camera_mix();
        ui.set_scene(area);
        assert_eq!(ui.camera_mix(), before, "frame {frame}: {area:?}");
        before
    }

    #[test]
    fn resize_back_mid_blend_starts_from_the_interpolated_mix() {
        let (_, mut ui) = fixture();
        let game = ui.game.clone();
        let rng = ui.visual_rng;
        rescene(&mut ui, 0, Rect::new(0, 0, 40, 20));
        assert_eq!(ui.camera().view, CameraView::Room);
        rescene(&mut ui, 16, Rect::new(0, 0, 20, 8));
        assert_eq!(ui.camera().view, CameraView::Face);
        assert_eq!(
            ui.camera_resize.get(),
            Some(([1000, 0, 0], CameraView::Face, 16))
        );
        // Frame 20 is eased 50 percent of the Room -> Face resize.
        let mix = rescene(&mut ui, 20, Rect::new(0, 0, 40, 20));
        assert_eq!(mix, [500, 0, 500]);
        assert_eq!(
            ui.camera_resize.get(),
            Some(([500, 0, 500], CameraView::Room, 20))
        );
        let camera = ui.camera();
        assert_eq!(camera.view, CameraView::Room);
        assert_eq!(camera.from_weights, Some([500, 0, 500]));
        assert_eq!(camera.transition_percent, 0);
        ui.frame = 28;
        assert_eq!(ui.camera_mix(), [1000, 0, 0]);
        assert_eq!(ui.game, game);
        assert_eq!(ui.visual_rng, rng);
    }

    #[test]
    fn third_size_mid_blend_and_manual_change_mid_resize_are_continuous() {
        let (config, mut ui) = fixture();
        let game = ui.game.clone();
        rescene(&mut ui, 0, Rect::new(0, 0, 40, 20));
        rescene(&mut ui, 8, Rect::new(0, 0, 20, 8));
        // A third size mid-blend: the new source is the mix drawn at frame 12.
        let mix = rescene(&mut ui, 12, Rect::new(0, 0, 60, 30));
        assert_eq!(mix, [500, 0, 500]);
        assert_eq!(ui.camera_resize.get(), Some((mix, CameraView::Room, 12)));
        // Z mid-resize: the manual blend starts from the mix at frame 14.
        ui.frame = 14;
        let before = ui.camera_mix();
        assert!(before[0] > 500 && before[0] < 1000, "{before:?}");
        ui.handle(
            KeyEvent::new(KeyCode::Char('z'), KeyModifiers::NONE),
            &config.game,
        );
        assert_eq!(ui.camera_view, CameraView::Pet);
        assert!(ui.camera_resize.get().is_none());
        assert_eq!(ui.camera_mix(), before);
        assert_eq!(ui.camera().from_weights, Some(before));
        // The manual blend then eases monotonically to the Pet view.
        let mut previous = before[1];
        for frame in 15..=22 {
            ui.animate(frame);
            let pet = ui.camera_mix()[1];
            assert!(pet >= previous, "frame {frame}: {pet} < {previous}");
            previous = pet;
        }
        assert_eq!(ui.camera_mix(), [0, 1000, 0]);
        assert_eq!(ui.game, game);
    }

    #[test]
    fn zoom_during_resize_keeps_the_resize_carrying_the_view() {
        let (_, mut ui) = fixture();
        ui.camera_view = CameraView::Pet;
        ui.camera_from_view = CameraView::Pet;
        let game = ui.game.clone();
        rescene(&mut ui, 0, Rect::new(0, 0, 40, 20));
        rescene(&mut ui, 30, Rect::new(0, 0, 20, 8));
        ui.frame = 32;
        let before = ui.camera_mix();
        assert!(before[2] > 0 && before[2] < 1000, "{before:?}");
        ui.zoom_by(true);
        assert_eq!(ui.camera_mix(), before);
        // The Face weight never falls back when the resize ends before the
        // zoom's own transition does.
        let mut previous = before[2];
        for frame in 33..=44 {
            ui.animate(frame);
            let face = ui.camera_mix()[2];
            assert!(face >= previous, "frame {frame}: {face} < {previous}");
            previous = face;
        }
        assert_eq!(ui.camera_mix(), [0, 0, 1000]);
        assert_eq!(ui.game, game);
    }

    #[test]
    fn same_target_reregistration_keeps_the_blend_in_flight() {
        let (_, mut ui) = fixture();
        rescene(&mut ui, 0, Rect::new(0, 0, 40, 20));
        rescene(&mut ui, 8, Rect::new(0, 0, 20, 8));
        let registered = ui.camera_resize.get();
        assert_eq!(registered, Some(([1000, 0, 0], CameraView::Face, 8)));
        // Another size with the same Face target must not restart the blend.
        rescene(&mut ui, 10, Rect::new(0, 0, 18, 7));
        rescene(&mut ui, 11, Rect::new(0, 0, 20, 8));
        assert_eq!(ui.camera_resize.get(), registered);
    }

    #[test]
    fn room_heading_names_the_view_drawn_in_the_same_frame() {
        let (config, base) = fixture();
        let mut found = None;
        'search: for height in 10..=18 {
            for width in (40..=86).step_by(2) {
                let mut ui = Ui::configured(base.game.clone(), &config);
                ui.camera_view = CameraView::Room;
                ui.camera_from_view = CameraView::Room;
                let wide = draw(&ui, &config, 120, 40);
                assert_eq!(ui.camera().view, CameraView::Room);
                assert!(
                    wide[0].contains(&format!("Room {}%", ui.zoom_percent)),
                    "{}",
                    wide[0]
                );
                ui.frame += 1;
                let lines = draw(&ui, &config, width, height);
                if ui.camera().view != CameraView::Face {
                    continue;
                }
                // The first draw after the resize already names the new view.
                let label = format!("{:?} {}%", ui.camera().view, ui.zoom_percent);
                assert!(
                    !lines[0].contains("Room "),
                    "{width}x{height}: {}",
                    lines[0]
                );
                if lines[0].contains(&label) {
                    found = Some((width, height));
                    break 'search;
                }
            }
        }
        assert!(
            found.is_some(),
            "no scene under 28x10 printed its own view in the heading"
        );
    }

    #[test]
    fn information_footer_paints_the_configured_background_80x24_auto() {
        let vendor = Config::sample().unwrap();
        assert!(
            80 < vendor.hud_minimal_width,
            "80x24 must be the minimal HUD"
        );
        let light = {
            let mut config = vendor.clone();
            config.background = "#F4F1E8".into();
            config.palette = ["#1B1B22", "#2B3A55", "#3E2C23", "#14213D"]
                .map(String::from)
                .to_vec();
            config
        };
        for config in [vendor, light] {
            for depth in [Depth::Rgb, Depth::Indexed, Depth::Ansi] {
                let colors = Colors::from_config(&config, depth).unwrap();
                let (_, base) = fixture();
                let mut ui = Ui::configured(base.game, &config);
                ui.hud_mode = HudMode::Auto;
                let mut terminal = Terminal::new(TestBackend::new(80, 24)).unwrap();
                terminal
                    .draw(|frame| ui.draw(frame, &config, Renderer::Ascii, &colors, 0, false))
                    .unwrap();
                let footer = ui.info_footer.get();
                assert!(
                    footer.width > 0 && footer.height > 0,
                    "{depth:?}: draw_information did not run"
                );
                let buffer = terminal.backend().buffer();
                for y in footer.y..footer.bottom() {
                    for x in footer.x..footer.right() {
                        let bg = buffer[(x, y)].bg;
                        assert_ne!(
                            bg,
                            Color::Reset,
                            "{} {depth:?} ({x},{y})",
                            config.background
                        );
                        assert_eq!(
                            bg, colors.background,
                            "{} {depth:?} ({x},{y})",
                            config.background
                        );
                    }
                }
            }
        }
    }
}

#[cfg(test)]
mod interrupted_yaw_audit {
    use super::*;
    use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};

    #[test]
    fn interrupted_manual_view_preserves_drawn_yaw_and_sprite() {
        let config = Config::sample().unwrap();
        let mut game = Game::new(&config.game, 513);
        for _ in 0..config.game.hatch_ticks {
            game.tick(&config.game);
        }
        let mut ui = Ui::configured(game, &config);
        ui.world_enabled = false;
        ui.camera_transition_frames = 8;
        ui.camera_view = CameraView::Pet;
        ui.camera_from_view = CameraView::Pet;
        ui.yaw = 135;
        ui.target_yaw = 135;
        ui.set_scene(Rect::new(0, 0, 120, 40));
        let game_before = ui.game.clone();
        let rng_before = ui.visual_rng;
        ui.handle(
            KeyEvent::new(KeyCode::Char('z'), KeyModifiers::NONE),
            &config.game,
        );
        assert_eq!(ui.camera_view, CameraView::Face);
        ui.animate(4);
        let before_yaw = ui.camera_yaw(ui.yaw);
        let before_sprite = ui.pet_sprite(&config.game, ui.frame);
        let before_mix = ui.camera_mix();
        assert!(ui.camera_blend > 0 && ui.camera_blend < 100);
        ui.handle(
            KeyEvent::new(KeyCode::Char('z'), KeyModifiers::NONE),
            &config.game,
        );
        assert_eq!(ui.camera_view, CameraView::Room);
        assert_eq!(ui.camera_mix(), before_mix);
        assert_eq!(
            ui.camera_yaw(ui.yaw),
            before_yaw,
            "An interrupted switch must start from the currently drawn yaw"
        );
        assert_eq!(
            ui.pet_sprite(&config.game, ui.frame),
            before_sprite,
            "The actual projected voxel subject must not jump on that frame"
        );
        assert_eq!(ui.game, game_before);
        assert_eq!(ui.visual_rng, rng_before);
    }
}

#[cfg(test)]
mod camera_rig_regressions {
    use super::*;
    use crossterm::event::{KeyCode, KeyEvent, KeyEventKind, KeyModifiers};
    use ratatui::{backend::TestBackend, Terminal};
    fn fixture() -> (Config, Ui) {
        let config = Config::sample().unwrap();
        let mut game = Game::new(&config.game, 513);
        for _ in 0..config.game.hatch_ticks {
            game.tick(&config.game);
        }
        let ui = Ui::configured(game, &config);
        (config, ui)
    }
    #[test]
    fn camera_keys_are_bounded_and_cannot_edit_seed_menu_or_game() {
        let (config, mut ui) = fixture();
        ui.set_scene(Rect::new(0, 0, 120, 40));
        let game = ui.game.clone();
        let rng = ui.visual_rng;
        for _ in 0..100 {
            ui.handle(
                KeyEvent::new(KeyCode::Char('6'), KeyModifiers::ALT),
                &config.game,
            );
        }
        assert!(ui.camera_pan_target.get().0 > 0);
        assert!(
            ui.camera_pan_target.get().0
                <= (ui.camera_rules.pan_step_permille * u16::from(ui.camera_rules.max_lead_steps))
                    as i16
        );
        ui.animate(100);
        let pan = ui.camera_pan.get();
        assert!(pan.0 > 0 && pan.0 <= ui.camera_rules.pan_max_permille as i16);
        ui.handle(
            KeyEvent::new(KeyCode::Char('3'), KeyModifiers::ALT),
            &config.game,
        );
        ui.animate(200);
        assert!(ui.camera_tilt > 0);
        for _ in 0..100 {
            ui.handle(
                KeyEvent::new(KeyCode::Char('9'), KeyModifiers::ALT),
                &config.game,
            );
        }
        let turn = (i32::from(ui.orbit_target) - i32::from(ui.orbit_degrees) + 540) % 360 - 180;
        assert!(
            turn.abs()
                <= i32::from(ui.camera_rules.orbit_fine_step_degrees)
                    * i32::from(ui.camera_rules.max_lead_steps)
        );
        ui.animate(300);
        assert_ne!(ui.orbit_degrees, ui.orbit_default);
        ui.handle(
            KeyEvent::new(KeyCode::Char('5'), KeyModifiers::ALT),
            &config.game,
        );
        ui.animate(400);
        assert_eq!(ui.camera_pan.get(), (0, 0));
        assert_eq!(ui.camera_tilt, 0);
        assert_eq!(ui.orbit_degrees, ui.orbit_default);
        assert_eq!(ui.zoom_percent, 100);
        ui.change_orbit(120);
        ui.animate(500);
        ui.change_camera(CameraView::Face, 100);
        ui.animate(600);
        assert_eq!(ui.camera_yaw(ui.yaw), 0);
        ui.camera_command(crate::input::CameraCommand::Reset);
        assert_eq!(ui.camera_yaw(ui.yaw), 0);
        ui.animate(700);
        assert_eq!(ui.camera_yaw(ui.yaw), 0);
        assert_eq!(ui.game, game);
        assert_eq!(ui.visual_rng, rng);
        ui.open(Menu::Home);
        let selection = ui.menu_selected;
        ui.handle(
            KeyEvent::new(KeyCode::Char('6'), KeyModifiers::ALT),
            &config.game,
        );
        assert_eq!(ui.menu_selected, selection);
        assert_eq!(ui.camera_pan_target.get(), (0, 0));
        ui.open_seed();
        let seed_before = format!("{:?}", ui.seed_entry);
        ui.handle(
            KeyEvent::new(KeyCode::Char('6'), KeyModifiers::ALT),
            &config.game,
        );
        assert_eq!(format!("{:?}", ui.seed_entry), seed_before);
        assert_eq!(ui.game, game);
    }
    #[test]
    fn camera_release_repeat_and_foreign_modifiers_are_inert() {
        let (config, mut ui) = fixture();
        for kind in [KeyEventKind::Release, KeyEventKind::Repeat] {
            let mut key = KeyEvent::new(KeyCode::Char('6'), KeyModifiers::ALT);
            key.kind = kind;
            ui.handle(key, &config.game);
        }
        ui.handle(
            KeyEvent::new(KeyCode::Char('6'), KeyModifiers::CONTROL),
            &config.game,
        );
        assert_eq!(ui.camera_pan_target.get(), (0, 0));
    }
    #[test]
    fn previews_share_the_rig_but_subject_changes_clear_only_pan() {
        let (config, mut ui) = fixture();
        ui.open(Menu::Roster);
        ui.set_scene_for_stage(
            Rect::new(0, 0, 80, 30),
            Some(Stage::Adult),
            SceneSubject::Roster,
        );
        ui.handle(
            KeyEvent::new(KeyCode::Char('9'), KeyModifiers::ALT),
            &config.game,
        );
        let held = ui.preview_yaw();
        ui.animate(40);
        assert_eq!(ui.preview_yaw(), held);
        ui.handle(
            KeyEvent::new(KeyCode::Char('6'), KeyModifiers::ALT),
            &config.game,
        );
        ui.animate(80);
        assert!(ui.camera_pan.get().0 > 0);
        ui.handle(
            KeyEvent::new(KeyCode::Char('3'), KeyModifiers::ALT),
            &config.game,
        );
        ui.animate(120);
        let tilt = ui.camera_tilt;
        ui.set_scene_for_stage(
            Rect::new(0, 0, 80, 30),
            Some(Stage::Child),
            SceneSubject::Studio,
        );
        assert_eq!(ui.camera_pan.get(), (0, 0));
        assert_eq!(ui.camera_tilt, tilt);
        assert!(ui.preview_angle.get().is_none());
    }
    #[test]
    fn auto_hud_keeps_scene_space_on_short_wide_and_phone_frames() {
        let (config, mut ui) = fixture();
        let colors = Colors::from_config(&config, crate::capabilities::Depth::Mono).unwrap();
        let game = ui.game.clone();
        for (width, height) in [(130, 28), (100, 24), (59, 49), (20, 8)] {
            ui.hud_mode = HudMode::Auto;
            let mut terminal = Terminal::new(TestBackend::new(width, height)).unwrap();
            terminal
                .draw(|f| ui.draw(f, &config, Renderer::Ascii, &colors, ui.frame, false))
                .unwrap();
            let scene = ui.pet_area.get();
            assert!(
                scene.height >= if height >= 12 { height / 2 } else { 1 },
                "{width}x{height}: {scene:?}"
            );
            let lines = (0..height)
                .map(|y| {
                    (0..width)
                        .map(|x| terminal.backend().buffer()[(x, y)].symbol())
                        .collect::<String>()
                })
                .collect::<Vec<_>>()
                .join("\n");
            assert!(lines.contains("Menu"), "{lines}");
        }
        ui.hud_mode = HudMode::Full;
        let mut terminal = Terminal::new(TestBackend::new(130, 28)).unwrap();
        terminal
            .draw(|f| ui.draw(f, &config, Renderer::Ascii, &colors, ui.frame, false))
            .unwrap();
        assert!(ui.pet_area.get().height < 14);
        assert_eq!(ui.game, game);
    }
}

#[cfg(test)]
mod controller_alias_precedence_tests {
    use super::*;
    use crossterm::event::{KeyCode, KeyModifiers};

    fn fixture() -> (Config, Ui) {
        let config = Config::sample().unwrap();
        let mut game = Game::new(&config.game, 513);
        for _ in 0..config.game.hatch_ticks {
            game.tick(&config.game);
        }
        (config.clone(), Ui::configured(game, &config))
    }

    fn alias(digit: char) -> KeyEvent {
        KeyEvent::new(KeyCode::Char(digit), KeyModifiers::ALT)
    }

    #[test]
    fn menu_stick_aliases_open_games_and_return_without_changing_pet_or_camera() {
        let (config, mut ui) = fixture();
        ui.open(Menu::Home);
        let game = ui.game.clone();
        let rig = (
            ui.camera_pan_target.get(),
            ui.camera_tilt_target,
            ui.orbit_degrees,
            ui.zoom_target,
        );
        ui.handle(alias('2'), &config.game);
        assert_eq!(ui.menu_selected, 1);
        assert_eq!(ui.menu, Some(Menu::Home));
        ui.handle(
            KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE),
            &config.game,
        );
        assert_eq!(ui.menu, Some(Menu::Games));
        ui.handle(
            KeyEvent::new(KeyCode::Esc, KeyModifiers::NONE),
            &config.game,
        );
        assert_eq!(ui.menu, Some(Menu::Home));
        ui.handle(alias('2'), &config.game);
        ui.handle(alias('8'), &config.game);
        assert_eq!(ui.menu_selected, 0);
        assert_eq!(ui.game, game);
        assert_eq!(
            (
                ui.camera_pan_target.get(),
                ui.camera_tilt_target,
                ui.orbit_degrees,
                ui.zoom_target
            ),
            rig
        );
    }

    #[test]
    fn menu_aliases_preserve_unpainted_columns_and_special_input_scopes() {
        let (config, mut ui) = fixture();
        let game = ui.game.clone();
        for menu in [
            Menu::Home,
            Menu::Care,
            Menu::Games,
            Menu::Graphics,
            Menu::Training,
            Menu::Habitat,
        ] {
            ui.open(menu);
            ui.handle(alias('2'), &config.game);
            assert_eq!(ui.menu_selected, 1, "{menu:?}");
            ui.handle(alias('8'), &config.game);
            assert_eq!(ui.menu_selected, 0, "{menu:?}");
            for digit in ['1', '3', '4', '5', '6', '7', '9'] {
                ui.handle(alias(digit), &config.game);
                assert_eq!(ui.menu_selected, 0, "{menu:?} Alt{digit}");
            }
            for kind in [KeyEventKind::Release, KeyEventKind::Repeat] {
                let mut key = alias('2');
                key.kind = kind;
                ui.handle(key, &config.game);
                assert_eq!(ui.menu_selected, 0);
            }
            let mut foreign = alias('2');
            foreign.modifiers |= KeyModifiers::CONTROL;
            ui.handle(foreign, &config.game);
            assert_eq!(ui.menu_selected, 0);
            assert_eq!(ui.camera_pan_target.get(), (0, 0));
            assert_eq!(ui.game, game);
        }
    }

    #[test]
    fn preview_pan_remains_camera_owned_and_seed_reset_memory_are_isolated() {
        let (config, mut ui) = fixture();
        for menu in [Menu::Roster, Menu::Gallery] {
            ui.open(menu);
            let selection = ui.menu_selected;
            let pan = ui.camera_pan_target.get();
            ui.handle(alias('2'), &config.game);
            assert_ne!(ui.camera_pan_target.get(), pan, "{menu:?}");
            assert_eq!(ui.menu_selected, selection);
            ui.camera_command(crate::input::CameraCommand::Reset);
        }
        ui.menu = None;
        ui.viewport.set((80, 24));
        ui.ask_reset();
        assert!(ui.confirm_reset);
        let game = ui.game.clone();
        for digit in ['8', '2'] {
            ui.handle(alias(digit), &config.game);
            assert!(ui.confirm_reset);
            assert_eq!(ui.game, game);
        }
        ui.confirm_reset = false;
        ui.open_seed();
        let entry = format!("{:?}", ui.seed_entry);
        for digit in ['8', '2'] {
            ui.handle(alias(digit), &config.game);
            assert_eq!(format!("{:?}", ui.seed_entry), entry);
            assert_eq!(ui.game, game);
        }
        ui.menu = None;
        ui.game.paused = false;
        ui.care(Care::Play, &config.game);
        assert!(ui.game.challenge.is_some());
        let challenge = ui.game.clone();
        for digit in ['8', '2'] {
            ui.handle(alias(digit), &config.game);
            assert_eq!(ui.game, challenge);
        }
    }
}

#[cfg(test)]
mod controller_hint_scope_tests {
    use super::*;
    use crate::capabilities::Depth;
    use ratatui::{backend::TestBackend, Terminal};

    fn fixture() -> (Config, Ui) {
        let config = Config::sample().unwrap();
        let game = Game::new(&config.game, 513);
        let mut ui = Ui::configured(game, &config);
        ui.controller = Some(ControllerPrompts {
            family: "Sony".into(),
            navigate: "D-pad".into(),
            accept: "Cross".into(),
            back: "Circle".into(),
            next: "R1".into(),
            release: "Hold L1+R1".into(),
            quit: None,
        });
        ui.controller_camera = Some("LS pan RS orbit/tilt L2/R2 zoom R3 reset".into());
        ui.controller_menu_navigation = Some("LS Up/Down".into());
        ui.controller_status = "Inputd: assigned pad focused here.".into();
        (config, ui)
    }

    #[test]
    fn controller_hint_roles_follow_menu_camera_and_dialog_ownership() {
        let (_config, mut ui) = fixture();
        assert!(ui.controller_description().contains("LS pan"));
        ui.open(Menu::Home);
        assert_eq!(
            ui.controller_navigation(ui.controller.as_ref().unwrap()),
            "D-pad + LS Up/Down"
        );
        assert!(ui.controller_description().contains("LS Up/Down"));
        assert!(!ui.controller_description().contains("LS pan"));
        ui.open(Menu::Roster);
        assert!(ui.controller_description().contains("LS pan"));
        assert_eq!(
            ui.controller_navigation(ui.controller.as_ref().unwrap()),
            "D-pad"
        );
        ui.native_modal = true;
        assert!(!ui.controller_description().contains("LS pan"));
        assert!(!ui.controller_description().contains("LS Up/Down"));
        ui.native_modal = false;
        ui.open(Menu::Seed);
        assert!(!ui.controller_description().contains("LS Up/Down"));
        ui.menu = None;
        ui.controller = None;
        assert!(!ui.controller_description().contains("LS pan"));
    }

    #[test]
    fn narrow_focused_menus_keep_the_actual_back_button() {
        let (config, mut ui) = fixture();
        ui.open(Menu::Home);
        let colors = Colors::from_config(&config, Depth::Mono).unwrap();
        for width in [34, 40, 59] {
            let mut terminal = Terminal::new(TestBackend::new(width, 24)).unwrap();
            terminal
                .draw(|f| ui.draw(f, &config, Renderer::Ascii, &colors, 0, false))
                .unwrap();
            let area = ui.menu_footer.get();
            let bottom = (area.x..area.right())
                .map(|x| terminal.backend().buffer()[(x, area.bottom() - 1)].symbol())
                .collect::<String>();
            assert!(bottom.contains("Circle"), "{width}: {bottom}");
            assert!(!bottom.contains("Esc Back"), "{width}: {bottom}");
        }
    }
}

#[cfg(test)]
mod camera_plain_fallthrough_tests {
    use super::*;
    use crossterm::event::{KeyCode, KeyModifiers};
    use ratatui::{backend::TestBackend, Terminal};

    fn fixture() -> (Config, Ui) {
        let config = Config::sample().unwrap();
        let mut game = Game::new(&config.game, 513);
        for _ in 0..config.game.hatch_ticks {
            game.tick(&config.game);
        }
        (config.clone(), Ui::configured(game, &config))
    }

    fn shifted(code: KeyCode) -> KeyEvent {
        KeyEvent::new(code, KeyModifiers::SHIFT)
    }

    fn draw(ui: &Ui, config: &Config, width: u16, height: u16) -> String {
        let colors = Colors::from_config(config, crate::capabilities::Depth::Mono).unwrap();
        let mut terminal = Terminal::new(TestBackend::new(width, height)).unwrap();
        terminal
            .draw(|f| ui.draw(f, config, Renderer::Ascii, &colors, ui.frame, false))
            .unwrap();
        (0..height)
            .map(|y| {
                (0..width)
                    .map(|x| terminal.backend().buffer()[(x, y)].symbol())
                    .collect::<String>()
            })
            .collect::<Vec<_>>()
            .join("\n")
    }

    #[test]
    fn shifted_arrows_reach_menu_scroll_and_seed_editors() {
        let (config, mut ui) = fixture();
        let game = ui.game.clone();
        for menu in [
            Menu::Home,
            Menu::Care,
            Menu::Games,
            Menu::Graphics,
            Menu::Training,
            Menu::Habitat,
        ] {
            ui.open(menu);
            ui.handle(shifted(KeyCode::Down), &config.game);
            assert_eq!(ui.menu_selected, 1, "{menu:?} must receive Shift+Down");
            ui.handle(shifted(KeyCode::Up), &config.game);
            assert_eq!(ui.menu_selected, 0, "{menu:?} must receive Shift+Up");
            assert_eq!(ui.camera_pan_target.get(), (0, 0));
            assert_eq!(ui.game, game);
        }
        ui.open(Menu::Help);
        draw(&ui, &config, 40, 16);
        ui.handle(shifted(KeyCode::Down), &config.game);
        assert_eq!(ui.menu_scroll, 1);
        ui.handle(shifted(KeyCode::Up), &config.game);
        assert_eq!(ui.menu_scroll, 0);

        ui.open_seed();
        ui.seed_entry.edit(SeedEdit::Clear);
        for c in ['4', '6'] {
            ui.handle(
                KeyEvent::new(KeyCode::Char(c), KeyModifiers::NONE),
                &config.game,
            );
        }
        assert_eq!(ui.seed_entry.cursor(), 2);
        ui.handle(shifted(KeyCode::Left), &config.game);
        assert_eq!(ui.seed_entry.cursor(), 1);
        ui.handle(shifted(KeyCode::Left), &config.game);
        assert_eq!(ui.seed_entry.cursor(), 0);
        ui.handle(shifted(KeyCode::Right), &config.game);
        assert_eq!(ui.seed_entry.cursor(), 1);
        assert_eq!(ui.camera_pan_target.get(), (0, 0));
        assert_eq!(ui.game, game);
    }

    #[test]
    fn shifted_memory_answer_is_a_guess_while_alt_camera_aliases_stay_inert() {
        let (config, mut ui) = fixture();
        ui.care(Care::Play, &config.game);
        let challenge = ui.game.challenge.as_mut().unwrap();
        challenge.reveal = 0;
        let code = match challenge.pattern[0] {
            Direction::Up => KeyCode::Up,
            Direction::Down => KeyCode::Down,
            Direction::Left => KeyCode::Left,
            Direction::Right => KeyCode::Right,
        };
        let game = ui.game.clone();
        for digit in ['2', '6', '9'] {
            ui.handle(
                KeyEvent::new(KeyCode::Char(digit), KeyModifiers::ALT),
                &config.game,
            );
        }
        for kind in [KeyEventKind::Repeat, KeyEventKind::Release] {
            let mut key = shifted(code);
            key.kind = kind;
            ui.handle(key, &config.game);
        }
        assert_eq!(ui.game, game);
        ui.handle(shifted(code), &config.game);
        assert_eq!(ui.game.challenge.as_ref().unwrap().entered, 1);
        assert_eq!(ui.camera_pan_target.get(), (0, 0));
    }

    #[test]
    fn shifted_camera_press_owns_live_view_but_not_native_dialog() {
        let (config, mut ui) = fixture();
        ui.set_scene(Rect::new(0, 0, 80, 24));
        let game = ui.game.clone();
        ui.handle(shifted(KeyCode::Down), &config.game);
        let pan = ui.camera_pan_target.get();
        assert!(pan.1 > 0);
        for kind in [KeyEventKind::Repeat, KeyEventKind::Release] {
            let mut key = shifted(KeyCode::Down);
            key.kind = kind;
            ui.handle(key, &config.game);
            assert_eq!(ui.camera_pan_target.get(), pan);
        }
        ui.native_modal = true;
        ui.handle(shifted(KeyCode::Up), &config.game);
        assert_eq!(ui.camera_pan_target.get(), pan);
        assert_eq!(ui.game, game);
        assert_eq!(
            ui.handle(
                KeyEvent::new(KeyCode::Char('c'), KeyModifiers::CONTROL),
                &config.game
            ),
            Effect::Quit
        );
    }

    #[test]
    fn rendered_help_describes_implemented_camera_and_wheel_controls() {
        let (config, mut ui) = fixture();
        ui.open(Menu::Help);
        let text = draw(&ui, &config, 130, 70);
        for hint in [
            "4/6/8/2",
            "Shift+arrows",
            "7/9 orbit",
            "1/3",
            "5 reset",
            "Shift tilts",
            "sideways orbits",
            "Roster/Studio",
            "Alt+digits",
        ] {
            assert!(text.contains(hint), "missing {hint}: {text}");
        }
        for stale in ["no tilt controls", "resets orbit only"] {
            assert!(!text.contains(stale), "{text}");
        }
    }

    #[test]
    fn camera_follower_smooth_integrator_and_shortest_yaw() {
        let (_config, mut ui) = fixture();
        ui.camera_rules.follow_half_life_ms = 90;
        ui.camera_rules.rest_epsilon_permille = 2;
        ui.camera_orbit_deg = 350.0;
        ui.orbit_target = 10;
        assert!(ui.camera_unsettled());

        // Shortest wrapped yaw across 0/360: +20 deg, not -340 deg
        ui.step_camera_realtime(90.0);
        let diff = ((f64::from(ui.orbit_target) - ui.camera_orbit_deg + 540.0) % 360.0) - 180.0;
        assert!(diff.abs() < 15.0 && diff.abs() > 0.0);

        for _ in 0..20 {
            ui.step_camera_realtime(50.0);
        }
        assert!(!ui.camera_unsettled());
        assert_eq!(ui.orbit_degrees, 10);
    }

    #[test]
    fn camera_dt_spike_clamped_and_sim_frozen_during_motion() {
        let (_config, mut spike) = fixture();
        let (_config, mut bounded) = fixture();
        let game = spike.game.clone();
        spike.orbit_target = 180;
        bounded.orbit_target = 180;
        spike.step_camera_realtime(1000.0);
        bounded.step_camera_realtime(250.0);
        spike.animate(spike.frame);
        bounded.animate(bounded.frame);
        assert_eq!(spike.camera_orbit_deg, bounded.camera_orbit_deg);
        assert_eq!(spike.orbit_degrees, bounded.orbit_degrees);
        assert_eq!(spike.game, game);
    }

    #[test]
    fn camera_reset_glide_zero_is_instant_cut() {
        let (_config, mut ui) = fixture();
        ui.camera_rules.reset_glide_ms = 0;
        ui.camera_orbit_deg = 180.0;
        ui.orbit_degrees = 180;
        ui.camera_tilt_deg = 20.0;
        ui.camera_tilt = 20;
        ui.camera_command(crate::input::CameraCommand::Reset);
        ui.step_camera_realtime(0.0);
        ui.animate(ui.frame);
        assert!(!ui.camera_unsettled());
        assert_eq!(ui.orbit_degrees, ui.orbit_default);
        assert_eq!(ui.camera_tilt, 0);
    }

    #[test]
    fn realtime_pose_survives_animate_and_live_draw_at_the_same_pet_frame() {
        use crate::input::CameraCommand;
        let (config, mut ui) = fixture();
        let before = draw(&ui, &config, 100, 40);
        let yaw_before = ui.camera_yaw(ui.subject_yaw());
        let pitch_before = ui.camera_pitch();
        let game = ui.game.clone();
        let frame = ui.frame;
        let initial = (
            ui.orbit_degrees,
            ui.camera_tilt,
            ui.camera_pan.get(),
            ui.zoom_percent,
        );
        ui.camera_command(CameraCommand::Orbit(1));
        ui.camera_command(CameraCommand::Tilt(1));
        ui.camera_command(CameraCommand::Pan(1, 1));
        ui.camera_command(CameraCommand::Zoom(true));
        ui.step_camera_realtime(33.0);
        let moved = (
            ui.orbit_degrees,
            ui.camera_tilt,
            ui.camera_pan.get(),
            ui.zoom_percent,
        );
        assert_ne!(moved.0, initial.0);
        assert_ne!(moved.1, initial.1);
        assert_ne!(moved.2, initial.2);
        assert_ne!(moved.3, initial.3);
        ui.animate(frame);
        let after = draw(&ui, &config, 100, 40);
        assert_eq!(
            (
                ui.orbit_degrees,
                ui.camera_tilt,
                ui.camera_pan.get(),
                ui.zoom_percent
            ),
            moved,
            "the rendered camera lost its realtime pose to the pet frame clock"
        );
        assert_ne!(
            before, after,
            "realtime camera motion must reach the live draw"
        );
        assert_ne!(ui.camera_yaw(ui.subject_yaw()), yaw_before);
        assert!((ui.camera_pitch() - pitch_before).abs() > 0.001);
        assert_eq!(ui.game, game);
        assert_eq!(ui.frame, frame);
    }

    #[test]
    fn extra_camera_frames_preserve_active_paused_and_sleeping_pet_clocks() {
        for (paused, sleeping) in [(false, false), (true, false), (false, true)] {
            let (_config, mut moving) = fixture();
            let (_config, mut reference) = fixture();
            moving.game.paused = paused;
            moving.game.sleeping = sleeping;
            reference.game = moving.game.clone();
            moving.destination = (800, 800);
            reference.destination = moving.destination;
            moving.camera_command(crate::input::CameraCommand::Orbit(1));
            for frame in 0..=4 {
                for _ in 0..4 {
                    moving.step_camera_realtime(33.0);
                    moving.animate(frame);
                }
                reference.animate(frame);
                assert_eq!(moving.game, reference.game);
                assert_eq!(moving.position, reference.position);
                assert_eq!(moving.yaw, reference.yaw);
                assert_eq!(moving.frame, reference.frame);
            }
        }
    }

    #[test]
    fn default_reset_finishes_by_its_deadline_and_returns_to_idle_cadence() {
        let (config, mut ui) = fixture();
        assert_eq!(config.camera.reset_glide_ms, 350);
        let game = ui.game.clone();
        let frame = ui.frame;
        ui.camera_orbit_deg = f64::from((ui.orbit_default + 120) % 360);
        ui.orbit_degrees = ui.camera_orbit_deg as u16;
        ui.camera_pan_current.set((120.0, -80.0));
        ui.camera_pan.set((120, -80));
        ui.camera_tilt_deg = 20.0;
        ui.camera_tilt = 20;
        ui.camera_zoom_pct = 200.0;
        ui.zoom_percent = 200;
        // Slow rates cannot make a reset exceed its explicit maximum duration.
        ui.camera_rules.orbit_rate_deg_s = 10;
        ui.camera_command(crate::input::CameraCommand::Reset);
        for _ in 0..10 {
            ui.step_camera_realtime(33.0);
            ui.animate(frame);
            assert!(ui.camera_unsettled());
            assert_eq!(
                ui.camera_poll_interval_ms(&config),
                config.tick_ms.min(u64::from(config.camera.motion_frame_ms))
            );
        }
        ui.step_camera_realtime(20.0);
        ui.animate(frame);
        assert!(
            !ui.camera_unsettled(),
            "default reset left motion cadence running forever"
        );
        assert_eq!(ui.orbit_degrees, ui.orbit_default);
        assert_eq!(ui.camera_pan.get(), (0, 0));
        assert_eq!(ui.camera_tilt, 0);
        assert_eq!(ui.zoom_percent, 100);
        assert_eq!(
            ui.camera_poll_interval_ms(&config),
            config.tick_ms.min(config.animation_frame_ms)
        );
        assert_eq!(ui.game, game);
        assert_eq!(ui.frame, frame);
    }

    #[test]
    fn stop_threshold_scales_each_axis_and_cannot_bypass_its_rate_budget() {
        let (_config, mut ui) = fixture();
        ui.camera_rules.rest_epsilon_permille = 50;
        ui.camera_orbit_deg = 0.0;
        ui.orbit_degrees = 0;
        ui.orbit_target = ui.camera_rules.orbit_fine_step_degrees;
        ui.camera_pan_target.set((1, 0));
        assert!(
            ui.camera_unsettled(),
            "fifty per-mille is not fifty degrees"
        );
        let pan_before = ui.camera_pan_current.get();
        for dt in [0.0, -1.0, f64::NAN] {
            ui.step_camera_realtime(dt);
            assert_eq!(ui.camera_orbit_deg, 0.0);
            assert_eq!(ui.camera_pan_current.get(), pan_before);
        }
        ui.step_camera_realtime(0.001);
        assert!(ui.camera_orbit_deg > 0.0);
        assert!(ui.camera_orbit_deg < f64::from(ui.orbit_target));
        assert!(
            ui.camera_pan_current.get().0
                <= f64::from(ui.camera_rules.pan_rate_permille_s) / 1_000_000.0 + 1e-12
        );
        assert!(ui.camera_unsettled());
    }

    #[test]
    fn subject_change_clears_the_realtime_pan_before_the_next_draw() {
        let (config, mut ui) = fixture();
        ui.set_scene(Rect::new(0, 0, 100, 40));
        ui.camera_command(crate::input::CameraCommand::Pan(1, 1));
        ui.step_camera_realtime(33.0);
        ui.animate(ui.frame);
        assert_ne!(ui.camera_pan.get(), (0, 0));
        ui.set_scene_for_stage(
            Rect::new(0, 0, 100, 40),
            Some(Stage::Adult),
            SceneSubject::Roster,
        );
        ui.step_camera_realtime(33.0);
        ui.animate(ui.frame);
        assert_eq!(ui.camera_pan_current.get(), (0.0, 0.0));
        assert_eq!(ui.camera_pan_target.get(), (0, 0));
        assert_eq!(ui.camera().pan_permille, (0, 0));
        assert!(!draw(&ui, &config, 100, 40).is_empty());
    }

    #[test]
    fn interrupted_reset_does_not_cut_a_new_camera_target_at_the_old_deadline() {
        let (_config, mut ui) = fixture();
        ui.camera_orbit_deg = f64::from((ui.orbit_default + 120) % 360);
        ui.orbit_degrees = ui.camera_orbit_deg as u16;
        ui.camera_rules.orbit_rate_deg_s = 10;
        ui.camera_command(crate::input::CameraCommand::Reset);
        ui.step_camera_realtime(33.0);
        ui.camera_command(crate::input::CameraCommand::Pan(1, 0));
        for _ in 0..4 {
            ui.step_camera_realtime(250.0);
            ui.animate(ui.frame);
        }
        assert!(!ui.camera_reset_in_flight);
        assert!(ui.camera_unsettled());
        assert_ne!(ui.orbit_degrees, ui.orbit_target);
    }

    #[test]
    fn camera_motion_cadence_negative_control_instant_snap_fails() {
        let (_config, mut ui) = fixture();
        ui.camera_rules.follow_half_life_ms = 90;
        ui.orbit_target = 90;
        ui.camera_orbit_deg = 0.0;
        ui.step_camera_realtime(10.0);
        assert!(
            ui.camera_unsettled(),
            "camera must remain unsettled during flight"
        );
        assert_ne!(ui.orbit_degrees, 90, "instant snap regression detected");
    }
}

#[cfg(test)]
mod choice_menu_geometry_tests {
    use super::*;
    use crate::capabilities::Depth;
    use crossterm::event::{KeyCode, KeyModifiers};
    use ratatui::{backend::TestBackend, buffer::Buffer};

    fn fixture() -> (Config, Ui) {
        let config = Config::sample().unwrap();
        let mut game = Game::new(&config.game, 3778);
        for _ in 0..config.game.hatch_ticks {
            game.tick(&config.game);
        }
        (config.clone(), Ui::configured(game, &config))
    }

    fn key(code: KeyCode) -> KeyEvent {
        KeyEvent::new(code, KeyModifiers::NONE)
    }

    fn draw(ui: &Ui, config: &Config, width: u16, height: u16, depth: Depth) -> Buffer {
        let colors = Colors::from_config(config, depth).unwrap();
        let mut terminal = Terminal::new(TestBackend::new(width, height)).unwrap();
        terminal
            .draw(|frame| ui.draw(frame, config, Renderer::Ascii, &colors, 0, false))
            .unwrap();
        terminal.backend().buffer().clone()
    }

    fn geometry(ui: &Ui) -> crate::hud::MenuGeometry {
        ui.choice_geometry
            .borrow()
            .as_ref()
            .expect("painted choice geometry")
            .1
            .clone()
    }

    fn text(buffer: &Buffer) -> String {
        buffer.content.iter().map(|cell| cell.symbol()).collect()
    }

    #[test]
    fn real_painted_choices_publish_cardinal_navigation_without_charging_gameplay() {
        let (config, mut ui) = fixture();
        ui.open(Menu::Home);
        let first = draw(&ui, &config, 127, 29, Depth::Mono);
        let layout = geometry(&ui);
        assert!(layout.columns > 1);
        assert!(layout.panel.height < first.area.height);
        assert!(
            ui.pet_area.get().height > 0,
            "scene must be painted behind the menu"
        );
        let game = ui.game.clone();
        let rng = ui.visual_rng;
        let camera = (
            ui.camera_pan_target.get(),
            ui.camera_tilt_target,
            ui.orbit_target,
            ui.zoom_target,
        );
        ui.handle(
            KeyEvent::new(KeyCode::Char('6'), KeyModifiers::ALT),
            &config.game,
        );
        assert_eq!(ui.menu_selected, 1, "right follows the actual painted row");
        ui.handle(key(KeyCode::Down), &config.game);
        assert_eq!(ui.menu_selected, 1 + usize::from(layout.columns));
        ui.handle(
            KeyEvent::new(KeyCode::Char('4'), KeyModifiers::ALT),
            &config.game,
        );
        assert_eq!(ui.menu_selected, usize::from(layout.columns));
        ui.handle(key(KeyCode::Tab), &config.game);
        assert_eq!(ui.menu_selected, usize::from(layout.columns) + 1);
        let second = draw(&ui, &config, 127, 29, Depth::Mono);
        assert_ne!(first, second, "focus must change the actual buffer");
        assert_eq!(ui.game, game);
        assert_eq!(ui.visual_rng, rng);
        assert_eq!(
            (
                ui.camera_pan_target.get(),
                ui.camera_tilt_target,
                ui.orbit_target,
                ui.zoom_target
            ),
            camera
        );
    }

    #[test]
    fn full_geometry_reaches_off_page_choices_and_reveals_the_new_selection() {
        let (config, mut ui) = fixture();
        ui.open(Menu::Graphics);
        draw(&ui, &config, 80, 24, Depth::Mono);
        let first = geometry(&ui);
        assert!(first.visible < ui.menu_len(Menu::Graphics));
        assert_eq!(first.navigation.len(), ui.menu_len(Menu::Graphics));
        let game = ui.game.clone();
        for _ in 0..=first.visible {
            ui.handle(key(KeyCode::Down), &config.game);
            if !first
                .items
                .iter()
                .any(|item| item.index == ui.menu_selected)
            {
                break;
            }
        }
        assert!(!first
            .items
            .iter()
            .any(|item| item.index == ui.menu_selected));
        draw(&ui, &config, 80, 24, Depth::Mono);
        let next = geometry(&ui);
        assert!(next.items.iter().any(|item| item.index == ui.menu_selected));
        ui.handle(key(KeyCode::End), &config.game);
        draw(&ui, &config, 80, 24, Depth::Mono);
        assert!(geometry(&ui).items.iter().any(|item| item.index == 29));
        assert_eq!(ui.game, game);
    }

    #[test]
    fn repaint_and_menu_changes_invalidate_old_spatial_geometry() {
        let (config, mut ui) = fixture();
        ui.open(Menu::Home);
        draw(&ui, &config, 127, 29, Depth::Mono);
        assert!(geometry(&ui).columns > 1);
        draw(&ui, &config, 20, 8, Depth::Mono);
        assert_eq!(geometry(&ui).columns, 1);
        ui.handle(key(KeyCode::Right), &config.game);
        assert_eq!(
            ui.menu_selected, 0,
            "portrait must discard the old wide row"
        );
        ui.handle(key(KeyCode::Down), &config.game);
        assert_eq!(ui.menu_selected, 1);
        ui.open(Menu::Care);
        assert!(ui.choice_geometry.borrow().is_none());
        ui.handle(key(KeyCode::Right), &config.game);
        assert_eq!(
            ui.menu_selected, 0,
            "an unpainted menu has only a safe column"
        );
        draw(&ui, &config, 80, 24, Depth::Mono);
        assert!(geometry(&ui).columns > 1);
        ui.handle(key(KeyCode::Right), &config.game);
        assert_eq!(ui.menu_selected, 1);
        ui.back();
        assert!(ui.choice_geometry.borrow().is_none());
        ui.open(Menu::Help);
        draw(&ui, &config, 80, 24, Depth::Mono);
        assert!(
            ui.choice_geometry.borrow().is_none(),
            "documents own their existing layout"
        );
    }

    #[test]
    fn shared_painted_hit_boxes_and_numbered_choices_return_without_gameplay_charges() {
        let (config, mut ui) = fixture();
        for numeric in [false, true] {
            ui.open(Menu::Home);
            ui.open(Menu::Care);
            draw(&ui, &config, 80, 24, Depth::Mono);
            let layout = geometry(&ui);
            let back = layout.items.iter().find(|item| item.index == 4).unwrap();
            assert!(ui.menu_targets.borrow().contains(&(back.area, 4)));
            let before = ui.game.clone();
            let effect = if numeric {
                let number = 4 - ui.menu_number_start.get();
                assert!(number < ui.menu_number_count.get() && number < 9);
                ui.handle(
                    key(KeyCode::Char(char::from(b'1' + number as u8))),
                    &config.game,
                )
            } else {
                ui.click(back.area.x, back.area.y, &config.game)
            };
            assert_eq!(effect, Effect::Continue);
            assert_eq!(ui.menu, Some(Menu::Home));
            assert_eq!(ui.game, before);
        }
    }

    #[test]
    fn tiny_ascii_mono_keeps_selected_focus_and_both_control_rows() {
        let (config, mut ui) = fixture();
        ui.open(Menu::Home);
        ui.menu_selected = 9;
        let buffer = draw(&ui, &config, 12, 6, Depth::Mono);
        let content = text(&buffer);
        assert!(content.is_ascii());
        for caption in ["Quit", "Enter: OK", "Esc: Back"] {
            assert!(content.contains(caption), "{caption}: {content}");
        }
        let layout = geometry(&ui);
        let selected = layout.items.iter().find(|item| item.index == 9).unwrap();
        assert_eq!(buffer[(selected.area.x, selected.area.y)].symbol(), ">");
        assert_eq!(layout.footer.height, 2);
        let before = ui.game.clone();
        assert_eq!(
            ui.handle(key(KeyCode::Char('1')), &config.game),
            Effect::Quit
        );
        assert_eq!(ui.game, before);
    }

    #[test]
    fn choice_panels_share_nonoverlapping_in_bounds_targets_across_depths_and_sizes() {
        let (config, mut ui) = fixture();
        for depth in [Depth::Rgb, Depth::Indexed, Depth::Ansi, Depth::Mono] {
            for menu in [Menu::Home, Menu::Care, Menu::Games, Menu::Graphics] {
                ui.open(menu);
                ui.menu_selected = ui.menu_len(menu) - 1;
                for (width, height) in [(12, 6), (20, 8), (59, 49), (127, 29), (320, 90)] {
                    let buffer = draw(&ui, &config, width, height, depth);
                    let layout = geometry(&ui);
                    assert!(text(&buffer).is_ascii());
                    assert_eq!(layout.panel.intersection(buffer.area), layout.panel);
                    assert_eq!(layout.navigation.len(), ui.menu_len(menu));
                    assert!(layout
                        .items
                        .iter()
                        .any(|item| item.index == ui.menu_selected));
                    for (index, item) in layout.items.iter().enumerate() {
                        assert_eq!(item.area.intersection(layout.body), item.area);
                        assert!(item.area.intersection(layout.footer).is_empty());
                        assert!(ui.menu_targets.borrow().contains(&(item.area, item.index)));
                        for other in &layout.items[index + 1..] {
                            assert!(item.area.intersection(other.area).is_empty());
                        }
                    }
                    assert!(ui.menu_number_count.get() <= 9);
                    assert!(ui.menu_selected >= ui.menu_number_start.get());
                    assert!(
                        ui.menu_selected < ui.menu_number_start.get() + ui.menu_number_count.get()
                    );
                }
            }
        }
    }
}

// F3: the UI keeps one face-detail memory per scene subject. Presentation
// only: no case may touch the pet.
#[cfg(test)]
mod face_detail_memory_tests {
    use super::*;
    use crate::capabilities::Depth;
    use crate::render::DetailMemo;
    use ratatui::{backend::TestBackend, Terminal};

    #[test]
    fn live_draws_record_face_detail_and_a_new_subject_starts_fresh() {
        let config = Config::sample().unwrap();
        let mut game = Game::new(&config.game, 513);
        for _ in 0..config.game.hatch_ticks {
            game.tick(&config.game);
        }
        let mut ui = Ui::configured(game, &config);
        ui.view_mode = ViewMode::Voxel;
        ui.camera_view = CameraView::Face;
        ui.camera_from_view = CameraView::Face;
        ui.camera_transition_frames = 8;
        let game = ui.game.clone();
        let colors = Colors::from_config(&config, Depth::Rgb).unwrap();
        let draw = |ui: &Ui, width: u16, height: u16| {
            let mut terminal = Terminal::new(TestBackend::new(width, height)).unwrap();
            terminal
                .draw(|f| ui.draw(f, &config, Renderer::HalfBlock, &colors, ui.frame, false))
                .unwrap();
        };
        assert_eq!(ui.face_detail.get(), DetailMemo::default());
        for (width, height) in [(130, 29), (59, 49), (40, 14)] {
            ui.face_detail.set(DetailMemo::default());
            ui.animate(ui.frame + 1);
            draw(&ui, width, height);
            assert_ne!(
                ui.face_detail.get(),
                DetailMemo::default(),
                "{width}x{height}: the live draw recorded no face detail"
            );
        }
        // The same subject resized keeps its memory: the blend starts from it.
        ui.animate(ui.frame + 1);
        let live = ui.face_detail.get();
        draw(&ui, 130, 29);
        assert_ne!(ui.face_detail.get(), DetailMemo::default());
        assert_ne!(ui.face_detail.get(), live, "a resize must step the memory");
        // A preview is a new subject: it never inherits the live face's blend.
        ui.set_scene_for_stage(
            Rect::new(0, 0, 80, 30),
            Some(Stage::Adult),
            SceneSubject::Roster,
        );
        assert_eq!(
            ui.face_detail.get(),
            DetailMemo::default(),
            "a new subject inherited the face detail"
        );
        assert_eq!(ui.game, game);
    }
}
