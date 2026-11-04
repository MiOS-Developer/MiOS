// AI-hint: Playable pet menu, memory challenge and accelerated clock; save failures unwind through Ratatui restoration.
// AI-related: game.rs, input.rs, save.rs, render.rs
use crate::{
    capabilities::Renderer,
    config::Config,
    game::{Care, Direction, Game, Rules},
    input::{action, Action},
    render::{Colors, SpriteWidget},
    save::Store,
    sprite::Sprite,
};
use crossterm::event::{self, Event, KeyEvent, KeyEventKind};
use ratatui::{
    layout::{Constraint, Layout},
    widgets::Paragraph,
    DefaultTerminal, Frame,
};
use std::{
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

pub struct Ui {
    pub game: Game,
    pub selected: usize,
    pub confirm_reset: bool,
    pub notice: String,
}
impl Ui {
    pub fn new(game: Game) -> Self {
        Self {
            game,
            selected: 0,
            confirm_reset: false,
            notice: "Raise a healthy, happy adult. Offline time is frozen.".into(),
        }
    }
    pub fn handle(&mut self, key: KeyEvent, rules: &Rules) -> Effect {
        let Some(a) = action(key) else {
            return Effect::Continue;
        };
        if a == Action::Quit {
            return Effect::Quit;
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
                }
                Action::Cancel => {
                    self.confirm_reset = false;
                    self.notice = "Kept your current pet.".into();
                }
                _ => {}
            }
            return Effect::Continue;
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
                    self.notice = self.game.guess(d, rules).into();
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
            Action::Care(c) => self.notice = self.game.care(c, rules).into(),
            Action::Accept => match self.selected {
                0..=4 => {
                    self.notice = self
                        .game
                        .care(
                            [
                                Care::Feed,
                                Care::Play,
                                Care::Clean,
                                Care::Rest,
                                Care::Medicine,
                            ][self.selected],
                            rules,
                        )
                        .into()
                }
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
    fn ask_reset(&mut self) {
        self.confirm_reset = true;
        self.notice = "Replace this pet? Enter/Y confirms; Escape keeps it.".into();
    }
    pub fn summary(&self, rules: &Rules) -> String {
        format!("Mio - {:?} | age {} ticks | coins {} | memory wins {}\nFull {}  Happy {}  Clean {}  Energy {}  Health {}\n{}{}{}",
            self.game.stage(rules),self.game.age_ticks,self.game.coins,self.game.wins,
            self.game.fullness,self.game.happiness,self.game.hygiene,self.game.energy,self.game.health,
            if self.game.paused { "PAUSED " } else { "" },if self.game.sleeping { "RESTING " } else { "" },
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
        let [header, stats, body, memory, menu, footer] = Layout::vertical([
            Constraint::Length(2),
            Constraint::Length(3),
            Constraint::Min(0),
            Constraint::Length(3),
            Constraint::Length(3),
            Constraint::Length(3),
        ])
        .areas(screen.area());
        let goal = if self.game.thriving(&config.game) {
            "Goal reached: a thriving adult! Keep caring, or start a new pet."
        } else if self.game.health == 0 {
            "Your pet has passed away. New pet asks before replacing it."
        } else {
            "Goal: raise a healthy, happy adult. Feed, clean, rest and play!"
        };
        screen.render_widget(
            Paragraph::new(format!(
                "MiOS Tamagotchi | {}\n{goal}",
                if persistent {
                    "local save"
                } else {
                    "sample - no save file"
                }
            )),
            header,
        );
        screen.render_widget(Paragraph::new(self.summary(&config.game)), stats);
        let sprite = Sprite::pet(self.game.stage(&config.game), frame);
        screen.render_widget(
            SpriteWidget {
                sprite: &sprite,
                renderer,
                scale: config.scale,
                colors,
            },
            body,
        );
        let challenge = match &self.game.challenge {
            Some(c) if c.reveal > 0 => format!("MEMORY - watch ({} ticks left)\n{}\nWait for the pattern to disappear.",c.reveal,c.pattern.iter().map(|d| d.label()).collect::<Vec<_>>().join("  ")),
            Some(c) => format!("MEMORY - repeat the hidden pattern\n{} of 3 arrows correct\nEscape cancels. P pauses.",c.entered),
            None => self.notice.clone(),
        };
        screen.render_widget(Paragraph::new(challenge), memory);
        let labels: Vec<_> = LABELS
            .iter()
            .enumerate()
            .map(|(i, label)| format!("{}{label}", if i == self.selected { ">" } else { " " }))
            .collect();
        screen.render_widget(
            Paragraph::new(format!(
                "{}\n{}",
                labels[..4].join(" | "),
                labels[4..].join(" | ")
            )),
            menu,
        );
        screen.render_widget(Paragraph::new("Arrows/Tab: select  Enter/Space: act  P: pause  S: save\nF: feed G: memory C: clean R: rest M: medicine N: new pet\nQ: save and quit  Esc: back/quit | offline time is frozen"),footer);
    }
}

pub fn run(
    terminal: &mut DefaultTerminal,
    config: &Config,
    renderer: Renderer,
    colors: &Colors,
    store: Option<&Store>,
    game: Game,
) -> io::Result<()> {
    let mut ui = Ui::new(game);
    let mut last_tick = Instant::now();
    let mut last_save = Instant::now();
    let mut frame = 0;
    loop {
        terminal
            .draw(|screen| ui.draw(screen, config, renderer, colors, frame, store.is_some()))?;
        if event::poll(Duration::from_millis(config.tick_ms))? {
            if let Event::Key(key) = event::read()? {
                let was_paused = ui.game.paused;
                let old_age = ui.game.age_ticks;
                let effect = ui.handle(key, &config.game);
                if ui.game.paused != was_paused || ui.game.age_ticks < old_age {
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
        if last_tick.elapsed() >= Duration::from_secs(config.game.tick_seconds) {
            ui.game.tick(&config.game);
            last_tick = Instant::now();
        }
        if last_save.elapsed() >= Duration::from_secs(config.autosave_seconds) {
            if let Some(s) = store {
                s.save(&ui.game).map_err(io::Error::other)?;
            }
            last_save = Instant::now();
        }
        frame ^= 1;
    }
}
