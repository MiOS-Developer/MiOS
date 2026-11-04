// AI-hint: Runnable Tamagotchi sprite preview; keyboard/Inputd navigation and safe noninteractive snapshots.
// AI-related: usr/share/mios/mios.toml [tamagotchi], specs/tamagotchi-tui, task_3702
#![forbid(unsafe_code)]

use std::{
    io::{self, Write},
    path::PathBuf,
    process::ExitCode,
    time::{Duration, Instant},
};

use clap::Parser;
use crossterm::event::{self, Event};
use mios_tamagotchi::{
    capabilities::{self, ColorMode, Renderer},
    config::Config,
    input::{action, Action},
    render::{snapshot, Colors, SpriteWidget},
    sprite::Sprite,
};
use ratatui::{
    layout::{Constraint, Layout},
    widgets::Paragraph,
    DefaultTerminal,
};

#[derive(Parser)]
#[command(
    version,
    about = "Tamagotchi terminal rendering/input foundation (sprite preview)"
)]
struct Args {
    /// MiOS root for layered TOML resolution; installed FHS paths are used otherwise.
    #[arg(long)]
    root: Option<PathBuf>,
    /// Print one bounded ASCII preview without entering terminal raw mode.
    #[arg(long)]
    snapshot: bool,
    /// Override glyph heuristics; font coverage must be verified on the target terminal.
    #[arg(long, value_enum)]
    renderer: Option<Renderer>,
    #[arg(long, value_enum)]
    color: Option<ColorMode>,
    /// Integer scale: 0=auto, or 1/2/4/8/16. An oversized fixed scale displays a no-fit message.
    #[arg(long)]
    scale: Option<u16>,
}

fn main() -> ExitCode {
    match run(Args::parse()) {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("mios-tamagotchi: {error}");
            ExitCode::FAILURE
        }
    }
}

fn run(args: Args) -> Result<(), Box<dyn std::error::Error>> {
    let mut config = Config::load(args.root.as_deref())?;
    if let Some(value) = args.renderer {
        config.renderer = value;
    }
    if let Some(value) = args.color {
        config.color = value;
    }
    if let Some(value) = args.scale {
        config.scale = value;
    }
    config.validate()?;
    let caps = capabilities::detect(config.renderer, config.color);
    if args.snapshot || !caps.interactive {
        write!(
            io::stdout().lock(),
            "Tamagotchi sprite preview\n{}",
            snapshot(&Sprite::preview(0), &config)?
        )?;
        return Ok(());
    }
    let colors = Colors::from_config(&config, caps.depth)?;
    // Ratatui's lifecycle installs panic restoration and restores normal errors too.
    ratatui::run(|terminal| preview(terminal, &config, caps.renderer, &colors))?;
    Ok(())
}

fn preview(
    terminal: &mut DefaultTerminal,
    config: &Config,
    renderer: Renderer,
    colors: &Colors,
) -> io::Result<()> {
    let interval = Duration::from_millis(config.tick_ms);
    let mut last_tick = Instant::now();
    let mut frame = 0usize;
    let mut paused = false;
    loop {
        terminal.draw(|screen| {
            let [header, body, footer] = Layout::vertical([
                Constraint::Length(2), Constraint::Min(0), Constraint::Length(2),
            ]).areas(screen.area());
            screen.render_widget(Paragraph::new(format!("Tamagotchi sprite preview | {renderer:?} | {}", if paused { "paused" } else { "animated" })), header);
            screen.render_widget(SpriteWidget { sprite: &Sprite::preview(frame), renderer, scale: config.scale, colors }, body);
            screen.render_widget(Paragraph::new("Arrows/Tab: frame  Enter/P: pause  Q/Esc: quit\nRendering/input foundation; gameplay is not implemented."), footer);
        })?;
        if event::poll(interval.saturating_sub(last_tick.elapsed()))? {
            if let Event::Key(key) = event::read()? {
                match action(key) {
                    Some(Action::Quit) => return Ok(()),
                    Some(Action::Pause) => paused = !paused,
                    Some(Action::Previous | Action::Next) => {
                        frame ^= 1;
                        paused = true;
                    }
                    None => {}
                }
            }
        }
        // Check the clock after every input, so continuous input cannot starve animation ticks.
        if last_tick.elapsed() >= interval {
            if !paused {
                frame ^= 1;
            }
            last_tick = Instant::now();
        }
    }
}
