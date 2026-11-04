// AI-hint: Playable local virtual pet with explicit sample mode, read-only snapshots and single-writer saves.
// AI-related: ui.rs, game.rs, save.rs, usr/share/mios/mios.toml [tamagotchi], task_3707
#![forbid(unsafe_code)]
use clap::Parser;
use mios_tamagotchi::{
    capabilities::{self, ColorMode, Renderer},
    config::{Config, HudMode, ViewMode},
    dialogs::{self, Choice, DialogMode},
    game::{Care, Game},
    render::{CameraView, Colors, snapshot},
    save::{self, Store},
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
    /// Original low-resolution or detailed pixel-art sprite set.
    #[arg(long, value_enum)]
    sprite_style: Option<mios_tamagotchi::sprite::SpriteStyle>,
    /// CPU voxel 3D or classic pixel sprites; headless output remains ASCII.
    #[arg(long, value_enum)]
    view: Option<ViewMode>,
    /// Automatic, full, or minimal HUD for consoles and remote terminal views.
    #[arg(long, value_enum)]
    hud: Option<HudMode>,
    /// Frame the room, the complete pet, or a close-up of its face.
    #[arg(long, value_enum)]
    camera: Option<CameraView>,
    /// Camera magnification50..400 percent; zoom crops rather than silently refitting.
    #[arg(long)]
    zoom: Option<u16>,
    /// Native floating dialogues when eligible, or inline only.
    #[arg(long, value_enum)]
    dialogs: Option<DialogMode>,
    #[arg(long, hide=true, conflicts_with_all=["sample","demo","snapshot","save","root","dialog_reply"])]
    dialog: Option<PathBuf>,
    #[arg(long, hide=true, requires="dialog_choice", conflicts_with_all=["sample","demo","snapshot","save","root","dialog"])]
    dialog_reply: Option<PathBuf>,
    #[arg(long, hide = true, value_enum, requires = "dialog_reply")]
    dialog_choice: Option<Choice>,
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
    if let Some(path) = &args.dialog {
        return dialogs::popup_helper(path).map_err(Into::into);
    }
    if let (Some(path), Some(choice)) = (&args.dialog_reply, args.dialog_choice) {
        return dialogs::reply_helper(path, choice).map_err(Into::into);
    }
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
    if let Some(style) = args.sprite_style {
        config.sprite_style = style;
    }
    if let Some(view) = args.view {
        config.view_mode = view;
    }
    if let Some(hud) = args.hud {
        config.hud_mode = hud;
    }
    if let Some(camera) = args.camera {
        config.camera_view = camera;
    }
    if let Some(zoom) = args.zoom {
        config.zoom_percent = zoom;
    }
    if let Some(mode) = args.dialogs {
        config.dialog_mode = mode;
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
        let mut game = if let Some(p) = &path {
            save::load_with_rules(p, &config.habitat)?
        } else {
            None
        }
        .unwrap_or_else(|| Game::new(&config.game, 1));
        game.prepare_progress(&config.game)?;
        if !config.characters.iter().any(|c| c.id == game.character) {
            return Err("saved character is missing from this catalogue".into());
        }
        let snapshot_ui = Ui::configured(game.clone(), &config);
        let status = snapshot_ui.summary(&config.game);
        let art = snapshot(&snapshot_ui.pet_sprite(&config.game, 0), &config)?;
        let character = mios_tamagotchi::characters::character(&config.characters, game.character);
        let thought = mios_tamagotchi::characters::thought(character, &game, &config.game, 0);
        let speech = mios_tamagotchi::characters::speech(character, &game, &config.game, 0);
        game.validate_habitat(&config.habitat)?;
        let theme = config
            .habitat
            .theme(game.habitat.theme)
            .ok_or("saved room theme is missing")?;
        let placed = config
            .habitat
            .items
            .iter()
            .filter(|item| game.habitat.placed & (1u64 << item.id) != 0)
            .map(|item| item.name.as_str())
            .collect::<Vec<_>>()
            .join(", ");
        writeln!(
            io::stdout().lock(),
            "Habitat: {} | seed {} | placed: {}",
            theme.name,
            game.habitat.seed,
            if placed.is_empty() {
                "none"
            } else {
                placed.as_str()
            }
        )?;
        writeln!(
            io::stdout().lock(),
            "MiOS Tamagotchi\n{status}\n{art}\n{} thinks: {thought}\n{} says: {speech}\nSnapshot only; no clock or save writes. Use a terminal to play.",
            snapshot_ui.name(),
            snapshot_ui.name()
        )?;
        if game.progress.hatched {
            for (index, stat) in game.progress.stats.iter().enumerate() {
                writeln!(
                    io::stdout().lock(),
                    "{}: value {} | level {} | XP {}/{} | aptitude {}",
                    mios_tamagotchi::progression::TrainingRules::label(index),
                    stat.value,
                    stat.level,
                    stat.xp,
                    config.game.training.xp_needed(stat.level),
                    stat.grade()
                )?;
            }
        }
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
    let mut game = store
        .as_ref()
        .map(|store| store.load_with_rules(&config.habitat))
        .transpose()?
        .flatten()
        .unwrap_or_else(|| Game::new(&config.game, seed));
    game.prepare_progress(&config.game)?;
    // Validate before entering raw mode; an existing corrupt save is never silently replaced.
    game.validate_habitat(&config.habitat)?;
    if !config.characters.iter().any(|c| c.id == game.character) {
        return Err("saved character is missing from this catalogue".into());
    }
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
        Ui::configured(game, config).summary(r)
    )?;
    Ok(())
}
