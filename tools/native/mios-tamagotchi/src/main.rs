// AI-hint: Playable local virtual pet with explicit sample mode, read-only snapshots and single-writer saves.
// AI-related: ui.rs, game.rs, save.rs, usr/share/mios/mios.toml [tamagotchi], task_3707
#![forbid(unsafe_code)]
use clap::Parser;
use mios_tamagotchi::{
    capabilities::{self, ColorMode, Renderer},
    config::Config,
    game::{Care, Game},
    render::{snapshot, Colors},
    save::{self, Store},
    sprite::Sprite,
    ui::{self, Ui},
};
use std::{
    io::{self, Write},
    path::PathBuf,
    process::ExitCode,
    time::{SystemTime, UNIX_EPOCH},
};

#[derive(Parser)]
#[command(
    version,
    about = "Playable MiOS virtual pet: care, growth and a memory game"
)]
struct Args {
    #[arg(long)]
    root: Option<PathBuf>,
    /// Use embedded SSOT settings; save-free unless an explicit --save PATH is supplied.
    #[arg(long, conflicts_with_all=["root","ephemeral"])]
    sample: bool,
    /// Scripted deterministic care/memory demonstration; never reads or writes a pet save.
    #[arg(long, conflicts_with_all=["root","save","ephemeral"])]
    demo: bool,
    /// Read one snapshot without entering raw mode or changing state.
    #[arg(long)]
    snapshot: bool,
    /// Use live/layered configuration, but keep a fresh pet only in memory.
    #[arg(long, conflicts_with = "save")]
    ephemeral: bool,
    /// Explicit per-node/per-instance save file; default is the user's state home.
    #[arg(long)]
    save: Option<PathBuf>,
    #[arg(long, value_enum)]
    renderer: Option<Renderer>,
    #[arg(long, value_enum)]
    color: Option<ColorMode>,
    #[arg(long)]
    scale: Option<u16>,
}
fn main() -> ExitCode {
    match run(Args::parse()) {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            eprintln!("mios-tamagotchi: {e}");
            ExitCode::FAILURE
        }
    }
}
fn run(args: Args) -> Result<(), Box<dyn std::error::Error>> {
    let mut config = if args.sample || args.demo {
        Config::sample()?
    } else {
        Config::load(args.root.as_deref())?
    };
    if let Some(v) = args.renderer {
        config.renderer = v;
    }
    if let Some(v) = args.color {
        config.color = v;
    }
    if let Some(v) = args.scale {
        config.scale = v;
    }
    config.validate()?;
    if args.demo {
        return demo(&config);
    }
    let caps = capabilities::detect(config.renderer, config.color);
    let ephemeral = args.ephemeral || (args.sample && args.save.is_none());
    let path = if ephemeral {
        None
    } else {
        args.save
            .or_else(|| save::default_path(&config.state_directory).ok())
    };
    if args.snapshot || !caps.interactive {
        let game = if let Some(p) = &path {
            save::load(p)?
        } else {
            None
        }
        .unwrap_or_else(|| Game::new(&config.game, 1));
        let status = Ui::new(game.clone()).summary(&config.game);
        let art = snapshot(&Sprite::pet(game.stage(&config.game), 0), &config)?;
        writeln!(io::stdout().lock(), "MiOS Tamagotchi\n{status}\n{art}\nSnapshot only; no clock or save writes. Use a terminal to play.")?;
        return Ok(());
    }
    if !ephemeral && path.is_none() {
        return Err("No writable state home: use --save PATH, --ephemeral or --sample".into());
    }
    let store = path.as_deref().map(Store::open).transpose()?;
    let seed = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos() as u64;
    let game = store
        .as_ref()
        .map(Store::load)
        .transpose()?
        .flatten()
        .unwrap_or_else(|| Game::new(&config.game, seed));
    // Validate before entering raw mode; an existing corrupt save is never silently replaced.
    game.validate()?;
    let colors = Colors::from_config(&config, caps.depth)?;
    ratatui::run(|terminal| {
        ui::run(
            terminal,
            &config,
            caps.renderer,
            &colors,
            store.as_ref(),
            game,
        )
    })?;
    Ok(())
}
fn demo(config: &Config) -> Result<(), Box<dyn std::error::Error>> {
    let r = &config.game;
    let mut game = Game::new(r, 7);
    for _ in 0..r.hatch_ticks {
        game.tick(r);
    }
    let mut output = io::stdout().lock();
    writeln!(
        output,
        "MiOS Tamagotchi playable demo\nHatched: {:?}",
        game.stage(r)
    )?;
    for c in [Care::Feed, Care::Clean, Care::Rest, Care::Rest, Care::Play] {
        writeln!(output, "{}", game.care(c, r))?;
    }
    let pattern = game
        .challenge
        .as_ref()
        .ok_or("demo could not start memory game")?
        .pattern;
    for _ in 0..r.memory_reveal_ticks {
        game.tick(r);
    }
    for d in pattern {
        writeln!(output, "{}", game.guess(d, r))?;
    }
    writeln!(
        output,
        "{}\nDemo complete: no save or live configuration was changed.",
        Ui::new(game).summary(r)
    )?;
    Ok(())
}
