// AI-hint: Full-viewport pet room, responsive framed menus and animated care reactions; schema-1 game state stays independent of presentation.
// AI-related: sprite.rs, render.rs, input.rs, config.rs, specs/tamagotchi-tui
use crate::{
    capabilities::Renderer,
    config::Config,
    game::{Care, Direction, Game, Rules},
    input::{action, Action},
    render::{Colors, RoomWidget},
    save::Store,
    sprite::{Expression, Sprite, SpriteStyle},
};
use crossterm::event::{self, Event, KeyEvent, KeyEventKind};
use ratatui::{
    layout::{Constraint, Layout, Rect},
    style::{Modifier, Style},
    text::{Line, Span},
    widgets::{Block, Borders, Clear, Paragraph, Wrap},
    DefaultTerminal, Frame,
};
use std::{
    cell::Cell,
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
}

pub struct Ui {
    pub game: Game,
    pub selected: usize,
    pub confirm_reset: bool,
    pub notice: String,
    pub menu: Option<Menu>,
    pub menu_selected: usize,
    gallery_stage: usize,
    pub sprite_style: SpriteStyle,
    pub renderer: Option<Renderer>,
    frame: usize,
    reaction: Option<Expression>,
    reaction_until: usize,
    reaction_started: usize,
    reaction_frames: usize,
    viewport: Cell<(u16, u16)>,
}
impl Ui {
    pub fn new(game: Game) -> Self {
        Self {
            game,
            selected: 0,
            confirm_reset: false,
            notice: "Welcome home. Raise a healthy, happy adult.".into(),
            menu: None,
            menu_selected: 0,
            gallery_stage: 3,
            sprite_style: SpriteStyle::EightBit,
            renderer: None,
            frame: 0,
            reaction: None,
            reaction_until: 0,
            reaction_started: 0,
            reaction_frames: 0,
            viewport: Cell::new((u16::MAX, u16::MAX)),
        }
    }
    pub fn configured(game: Game, config: &Config) -> Self {
        let mut ui = Self::new(game);
        ui.sprite_style = config.sprite_style;
        ui.reaction_frames = config.reaction_frames;
        ui
    }
    pub fn animate(&mut self, frame: usize) {
        self.frame = frame;
    }
    pub fn overlay_open(&self) -> bool {
        self.menu.is_some() || self.confirm_reset
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
        if (width < 34 || height < 12) && !matches!(a, Action::Save | Action::Pause) {
            self.confirm_reset = false;
            self.notice = "Enlarge the viewport to confirm a new pet.".into();
            return Effect::Continue;
        }
        if self.confirm_reset {
            match a {
                Action::Accept | Action::Confirm => {
                    self.game = Game::new(
                        rules,
                        self.game.age_ticks.wrapping_add(u64::from(self.game.wins)),
                    );
                    self.confirm_reset = false;
                    self.notice = "A new egg! The next save replaces the previous pet.".into();
                    self.react(Expression::Surprised);
                }
                Action::Cancel => {
                    self.confirm_reset = false;
                    self.notice = "Kept your current pet.".into();
                }
                _ => {}
            }
            return Effect::Continue;
        }
        match a {
            Action::Menu => {
                if self.menu.is_some() {
                    self.menu = None;
                } else {
                    self.open(Menu::Home);
                }
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
        let count = match menu {
            Menu::Home => 8,
            Menu::Care => 5,
            Menu::Games => 2,
            Menu::Graphics => 8,
            Menu::Status | Menu::Help => 1,
            Menu::Gallery => 16,
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
                    2 => self.open(Menu::Status),
                    3 => self.open(Menu::Graphics),
                    4 => self.open(Menu::Help),
                    5 => self.open(Menu::Gallery),
                    6 => {
                        self.menu = None;
                        return Effect::Save;
                    }
                    _ => return Effect::Quit,
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
                    0 => self.sprite_style = SpriteStyle::EightBit,
                    1 => self.sprite_style = SpriteStyle::SixteenBit,
                    2 => self.renderer = Some(Renderer::HalfBlock),
                    3 => self.renderer = Some(Renderer::Ascii),
                    4 => self.renderer = Some(Renderer::Quadrant),
                    5 => self.renderer = Some(Renderer::Braille),
                    6 => self.open(Menu::Gallery),
                    _ => self.menu = None,
                },
                Menu::Status | Menu::Help => self.menu = None,
                Menu::Gallery => {}
            },
            _ => {}
        }
        Effect::Continue
    }
    fn ask_reset(&mut self) {
        self.confirm_reset = true;
        self.notice = "Replace this pet? Enter/Y confirms; Escape keeps it.".into();
    }
    pub fn summary(&self, rules: &Rules) -> String {
        format!("Mio - {:?} | age {} ticks | coins {} | memory wins {}\nFull {}  Happy {}  Clean {}  Energy {}  Health {}\n{}{}{}",
            self.game.stage(rules), self.game.age_ticks, self.game.coins, self.game.wins,
            self.game.fullness, self.game.happiness, self.game.hygiene, self.game.energy, self.game.health,
            if self.game.paused { "PAUSED " } else { "" }, if self.game.sleeping { "RESTING " } else { "" },
            if self.game.ill { "ILL - clean, then give medicine" } else { "" })
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
        let outer = panel(" MIOS / PET ROOM ", colors, ascii);
        let inner = outer.inner(area);
        self.viewport.set((inner.width, inner.height));
        screen.render_widget(outer, area);
        if inner.width < 34 || inner.height < 12 {
            screen.render_widget(
                Paragraph::new(format!(
                    "Mio / {:?}\n{}\nTiny viewport: Q quits safely",
                    self.game.stage(&config.game),
                    if self.game.paused { "PAUSED" } else { "LIVE" }
                ))
                .style(Style::default().fg(colors.ink(2))),
                inner,
            );
            return;
        }
        let [header, content, dock] = Layout::vertical([
            Constraint::Length(3),
            Constraint::Min(1),
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
                        " MIO ",
                        Style::default()
                            .fg(colors.ink(0))
                            .bg(colors.ink(3))
                            .add_modifier(Modifier::BOLD),
                    ),
                    Span::raw(format!("  {style}  /  {clock}  /  {status}")),
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
        let (room, side) = if content.width >= 88 && content.height >= 15 {
            let [room, side] =
                Layout::horizontal([Constraint::Min(1), Constraint::Length(28)]).areas(content);
            (room, Some(side))
        } else {
            (content, None)
        };
        let expression = self.expression(&config.game, frame);
        let room_title = format!(" ROOM / {:?} ", expression);
        let border = panel(&room_title, colors, ascii);
        let room_inner = border.inner(room);
        screen.render_widget(border, room);
        let sprite = Sprite::animated(
            self.game.stage(&config.game),
            expression,
            self.sprite_style,
            if self.reaction.is_some() && frame < self.reaction_until {
                frame.saturating_sub(self.reaction_started)
            } else {
                frame
            },
        );
        screen.render_widget(
            RoomWidget {
                sprite: &sprite,
                renderer,
                scale: config.scale,
                colors,
                expression,
                frame,
            },
            room_inner,
        );
        if let Some(side) = side {
            self.draw_status(screen, side, colors, ascii);
        }
        let border = panel(" QUICK CARE ", colors, ascii);
        let actions = border.inner(dock);
        screen.render_widget(border, dock);
        let mut lines = Vec::new();
        let shortcuts = ["F", "G", "C", "R", "M", "P", "S", "N"];
        for row in 0..2 {
            let mut spans = Vec::new();
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
                spans.push(Span::styled(
                    format!(" [{}] {:<8} ", shortcuts[i], label),
                    style,
                ));
                spans.push(Span::raw(" "));
            }
            lines.push(Line::from(spans));
        }
        lines.push(
            Line::from(" O Menu  I Status  V Graphics  ? Help  |  Arrows/Tab + Enter  |  Q Quit")
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
                    "MEMORY / REPEAT\n{} of 3 arrows correct\nEscape cancels. P pauses.",
                    challenge.entered
                )
            };
            self.bubble(screen, room, &text, colors, ascii);
        } else if !self.notice.is_empty() {
            let text = self.notice.clone();
            let strip = Rect::new(
                room_inner.x,
                room_inner.bottom().saturating_sub(1),
                room_inner.width,
                1,
            );
            screen.render_widget(
                Paragraph::new(text)
                    .style(Style::default().fg(colors.ink(2)).bg(colors.background)),
                strip,
            );
        }
        if let Some(menu) = self.menu {
            self.draw_menu(screen, menu, colors, renderer);
        }
        if self.confirm_reset {
            let modal = center(area, 62, 7);
            screen.render_widget(Clear, modal);
            screen.render_widget(Paragraph::new("Replace this pet with a new egg?\nYour current save changes only after confirmation.\n\nEnter / Y: new pet    Escape: keep current pet").block(panel(" NEW PET? ",colors,ascii)).style(Style::default().fg(colors.ink(2)).bg(colors.background)).wrap(Wrap{trim:true}),modal);
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
    fn draw_menu(&self, screen: &mut Frame, menu: Menu, colors: &Colors, renderer: Renderer) {
        let ascii = renderer == Renderer::Ascii;
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
            let block = panel(" SPRITE STUDIO / PREVIEW ", colors, ascii);
            let inner = block.inner(area);
            screen.render_widget(block, area);
            let [label, room, controls] = Layout::vertical([
                Constraint::Length(2),
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
            let sprite = Sprite::animated(stage, expression, self.sprite_style, self.frame);
            screen.render_widget(
                RoomWidget {
                    sprite: &sprite,
                    renderer,
                    scale: 0,
                    colors,
                    expression,
                    frame: self.frame,
                },
                room,
            );
            screen.render_widget(Paragraph::new("Left/Right: reaction   Up/Down: life stage\nEnter/Space: 8-bit / 16-bit   Esc: return").style(Style::default().fg(colors.ink(13))),controls);
            return;
        }
        let (title, entries) = match menu {
            Menu::Home => (
                " MAIN MENU ",
                vec![
                    "Care",
                    "Games",
                    "Status",
                    "Graphics",
                    "Help",
                    "Sprite studio",
                    "Save",
                    "Quit",
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
                    "8-bit classic sprites",
                    "16-bit detailed sprites",
                    "Half-block pixels",
                    "ASCII characters",
                    "Quadrant pixels",
                    "Braille pixels",
                    "Sprite studio / reactions",
                    "Back",
                ],
            ),
            Menu::Help => (" HOW TO PLAY ", vec!["Back"]),
            Menu::Gallery => unreachable!("gallery draws its own full viewport"),
        };
        let extra = if matches!(menu, Menu::Status | Menu::Help) {
            12
        } else {
            4
        };
        let modal = center(screen.area(), 64, (entries.len() + extra) as u16);
        screen.render_widget(Clear, modal);
        let block = panel(title, colors, ascii);
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
            for line in [
                "Feed, clean, rest and play to raise a happy adult.",
                "Watch three arrows, then repeat after they disappear.",
                "F Feed  G Memory  C Clean  R Rest  M Medicine",
                "P Pause  S Save  N New pet (asks first)",
                "Arrows / Tab select; Enter / Space activates.",
                "O / F1 Menu  I Status  V Graphics  ? Help",
                "Escape closes a menu; Q saves and quits.",
                "Menus freeze care time; animation keeps moving.",
                "Graphics choices affect this session only.",
                "",
            ] {
                lines.push(Line::from(line));
            }
        }
        for (i, entry) in entries.iter().enumerate() {
            let selected = i == self.menu_selected;
            lines.push(
                Line::from(format!(" {} {} ", if selected { ">" } else { " " }, entry)).style(
                    if selected {
                        Style::default()
                            .fg(colors.ink(0))
                            .bg(colors.ink(7))
                            .add_modifier(Modifier::BOLD)
                    } else {
                        Style::default().fg(colors.ink(2))
                    },
                ),
            );
        }
        lines.push(Line::from(""));
        lines.push(
            Line::from("Arrows / Tab: select   Enter: choose   Esc: back")
                .style(Style::default().fg(colors.ink(13))),
        );
        screen.render_widget(
            Paragraph::new(lines)
                .style(Style::default().bg(colors.background))
                .wrap(Wrap { trim: true }),
            body,
        );
    }
    fn summary_for_menu(&self) -> String {
        format!("MIO\nAge {} game ticks / {} coins / {} wins\n\nFullness {}%     Happiness {}%\nHygiene  {}%     Energy    {}%\nHealth   {}%\n\n{}{}",
            self.game.age_ticks,self.game.coins,self.game.wins,self.game.fullness,self.game.happiness,self.game.hygiene,self.game.energy,self.game.health,
            if self.game.paused {"Clock paused. "} else {""},if self.game.ill {"Needs medicine."} else {"Healthy."})
    }
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
pub fn run(
    terminal: &mut DefaultTerminal,
    config: &Config,
    renderer: Renderer,
    colors: &Colors,
    store: Option<&Store>,
    game: Game,
) -> io::Result<()> {
    let mut ui = Ui::configured(game, config);
    let mut last_tick = Instant::now();
    let mut last_save = Instant::now();
    let animation = Instant::now();
    loop {
        let frame =
            (animation.elapsed().as_millis() / u128::from(config.animation_frame_ms)) as usize;
        ui.animate(frame);
        terminal
            .draw(|screen| ui.draw(screen, config, renderer, colors, frame, store.is_some()))?;
        if event::poll(Duration::from_millis(
            config.tick_ms.min(config.animation_frame_ms),
        ))? {
            if let Event::Key(key) = event::read()? {
                let was_paused = ui.game.paused;
                let was_overlay = ui.overlay_open();
                let old_age = ui.game.age_ticks;
                let effect = ui.handle(key, &config.game);
                if ui.game.paused != was_paused
                    || ui.overlay_open() != was_overlay
                    || ui.game.age_ticks < old_age
                {
                    last_tick = Instant::now();
                }
                match effect {
                    Effect::Quit => {
                        if let Some(s) = store {
                            s.save(&ui.game).map_err(io::Error::other)?;
                        }
                        return Ok(());
                    }
                    Effect::Save => {
                        if let Some(s) = store {
                            s.save(&ui.game).map_err(io::Error::other)?;
                            ui.notice = "Saved locally.".into();
                            last_save = Instant::now();
                        } else {
                            ui.notice = "Sample mode: no save file is written.".into();
                        }
                    }
                    Effect::Continue => {}
                }
            }
        }
        if ui.overlay_open() {
            last_tick = Instant::now();
        } else if last_tick.elapsed() >= Duration::from_secs(config.game.tick_seconds) {
            let stage = ui.game.stage(&config.game);
            ui.game.tick(&config.game);
            if ui.game.stage(&config.game) != stage {
                ui.react(Expression::Surprised);
            }
            last_tick = Instant::now();
        }
        if last_save.elapsed() >= Duration::from_secs(config.autosave_seconds) {
            if let Some(s) = store {
                s.save(&ui.game).map_err(io::Error::other)?;
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
        assert_eq!(ui.menu, None);
        assert_eq!(ui.game, saved);
    }
    #[test]
    fn visual_choices_and_reactions_are_separate_from_saved_state() {
        let (config, mut ui) = pet();
        let saved = ui.game.clone();
        ui.handle(key(KeyCode::Char('v')), &config.game);
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
        let (config, mut ui) = pet();
        let colors = Colors::from_config(&config, Depth::Indexed).unwrap();
        for (width, height) in [
            (80, 24),
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
            if width >= 80 {
                let text: String = buffer.content.iter().map(|c| c.symbol()).collect();
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
        ui.menu_selected = 5;
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
        ui.menu_selected = 6;
        assert_eq!(ui.handle(key(KeyCode::Enter), &config.game), Effect::Save);
        ui.open(Menu::Home);
        ui.menu_selected = 7;
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
        for (width, height) in [(12, 3), (34, 12), (35, 13)] {
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
}
