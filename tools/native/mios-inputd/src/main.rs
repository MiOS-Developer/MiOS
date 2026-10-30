// AI-hint: Global input policy validation, observation and explicitly enabled native controller bridge.
// AI-related: usr/share/mios/mios.toml [keybindings.global], tools/native/mios-inputd
use clap::{Parser, Subcommand};
use mios_inputd::GlobalConfig;
use std::path::PathBuf;
#[derive(Parser)]
#[command(about = "MiOS native global input router")]
struct Cli {
    #[arg(long, global = true)]
    root: Option<PathBuf>,
    #[command(subcommand)]
    command: Operation,
}
#[derive(Subcommand)]
enum Operation {
    Check,
    Context {
        /// Print the compositor query (Lua) instead of running it; parity tests evaluate it against fixtures.
        #[arg(long)]
        lua: bool,
    },
    Serve,
    Dispatch { action: String },
    VmAction { action: String },
    Map,
    ResolveAction {
        action: String,
        /// tmux: verbs resolve against this client tty (on --tmux-socket); discovery finds both on the desktop.
        #[arg(long)]
        tmux_client: Option<String>,
        #[arg(long, default_value = "/tmp/tmux-socket")]
        tmux_socket: PathBuf,
    },
    Replay,
    Normalize {
        chord: String,
        #[arg(long, default_value = " ")]
        separator: String,
    },
    Observe {
        #[arg(long)]
        device: Option<PathBuf>,
        #[arg(long)]
        seconds: Option<u64>,
    },
    Bridge {
        #[arg(long)]
        device: Option<PathBuf>,
        #[arg(long)]
        fixture_only: bool,
        #[arg(long)]
        seconds: Option<u64>,
        /// Fixture tier: follow the compositor named by HYPRLAND_INSTANCE_SIGNATURE (an owned nested session), run
        /// ownership transactions and the capture OSD there, and project nav output into it (key: as send_key_state to
        /// its focused window, hypr:, and tmux: for a client found under it). quickshell:, exec: and mios: stay printed.
        #[arg(long, requires = "fixture_only")]
        live_context: bool,
        /// Fixture tier: write the room packet to this private path (never the live one).
        #[arg(long, requires = "fixture_only")]
        pad_path: Option<PathBuf>,
    },
    /// Capture (on, fullscreen) or release (off) the focused window: the same transaction North and a held
    /// toggle_chord run, for every window kind.
    Window { mode: String },
    Fixture,
    #[command(hide = true)]
    FixtureGrab {
        device: PathBuf,
    },
    /// Private proof of the key: projection: the bridge's virtual keyboard under a fixture name, read by an exclusive grab.
    #[command(hide = true)]
    FixtureKeys {
        chords: Vec<String>,
    },
    /// Private uinput pad for tests ("mios-input-fixture-<layout>"); plays JSON lines from stdin, never a real device.
    #[command(hide = true)]
    FixturePad {
        #[arg(long, default_value = "dualsense")]
        layout: String,
    },
}
fn run() -> Result<(), String> {
    let args = Cli::parse();
    if let Operation::Normalize { chord, separator } = args.command {
        println!("{}", mios_inputd::normalize_chord(&chord, &separator)?);
        return Ok(());
    }
    #[cfg(target_os = "linux")]
    if matches!(args.command, Operation::Fixture) {
        println!("{}", mios_inputd::linux::private_fixture()?);
        return Ok(());
    }
    #[cfg(target_os = "linux")]
    if let Operation::FixtureGrab { device } = &args.command {
        return mios_inputd::linux::fixture_grab(device);
    }
    #[cfg(target_os = "linux")]
    if let Operation::FixtureKeys { chords } = &args.command {
        println!("{}", mios_inputd::linux::fixture_keys(chords)?);
        return Ok(());
    }
    #[cfg(target_os = "linux")]
    if let Operation::FixturePad { layout } = &args.command {
        return mios_inputd::linux::fixture_pad(layout);
    }
    let (global, legacy) = GlobalConfig::load(args.root.as_deref())?;
    let (bridge, fixture_only, live_context, pad_override) = match &args.command {
        Operation::Bridge { fixture_only, live_context, pad_path, .. } => (true, *fixture_only, *live_context, pad_path.clone()),
        _ => (false, false, false, None),
    };
    match args.command {
        Operation::Context { lua: true } => println!("{}",serde_json::json!({"lua":mios_inputd::runtime::context_lua(&global),"executed":false})),
        Operation::Context { lua: false } => {
            let c=mios_inputd::runtime::context(&global)?;
            println!("{}",serde_json::json!({"context":c.name,"address":c.address,"pid":c.pid,"room_capture":c.room_capture,"released":c.released,"controller_owner":if mios_inputd::router::Router::owns_controller(&c.name) {"app"} else {"mios"}}));
        }
        Operation::Window { mode } => {
            let (address,class)=mios_inputd::runtime::window_transaction(&mode,&global,None)?;
            println!("{}",serde_json::json!({"window":address,"class":class,"mode":mode,"ok":true}));
        }
        Operation::Dispatch { action } => {
            use std::os::unix::process::CommandExt;
            let argv=mios_inputd::runtime::configured_command(&action,&global)?;
            let (program,rest)=argv.split_first().ok_or("action has no external dispatcher")?;
            return Err(std::process::Command::new(program).args(rest).exec().to_string());
        }
        Operation::Serve => return mios_inputd::runtime::serve(global, legacy, args.root),
        Operation::VmAction { action } => mios_inputd::runtime::vm_action(&action, &global)?,
        #[cfg(target_os = "linux")]
        Operation::ResolveAction { action, tmux_client, tmux_socket } => {
            let argv=resolve(&global,&action,tmux_client.map(|tty| mios_inputd::runtime::TmuxClient{pid:0,tty,socket:tmux_socket}).as_ref())?;
            println!("{}",serde_json::json!({"action":action,"argv":argv,"executed":false}));
        }
        Operation::Map => println!("{}", serde_json::json!({"global":global,"hypr3d":legacy})),
        Operation::Replay => {
            use std::io::Read;
            #[derive(serde::Deserialize)]
            #[serde(untagged)]
            enum Step {
                Event(mios_inputd::router::Event),
                Tick { tick_ms: u64 },
                Context { context: String },
            }
            #[derive(serde::Deserialize)]
            struct Input {
                context: String,
                #[serde(default)]
                axes: std::collections::BTreeMap<u16, mios_inputd::router::Axis>,
                #[serde(default)]
                events: Vec<mios_inputd::router::Event>,
                /// Timed script: events, {"tick_ms": N} (time passes in 10 ms ticks) and {"context": "..."} focus moves.
                #[serde(default)]
                script: Vec<Step>,
                #[serde(default)]
                tick_ms: u64,
                /// A MiOS tmux client under the focused terminal (C-09): its tty; tmux: actions resolve against it.
                #[serde(default)]
                tmux_client: Option<String>,
                #[serde(default)]
                analog_triggers: bool,
            }
            let mut text = String::new();
            std::io::stdin()
                .take(1_048_577)
                .read_to_string(&mut text)
                .map_err(|e| e.to_string())?;
            if text.len() > 1_048_576 {
                return Err("replay input exceeds 1 MiB".into());
            }
            let input: Input = serde_json::from_str(&text).map_err(|e| e.to_string())?;
            let contexts_ok=std::iter::once(&input.context).chain(input.script.iter().filter_map(|s| match s {Step::Context{context}=>Some(context),_=>None}))
                .all(|c| mios_inputd::config::CONTEXTS.contains(&c.as_str()));
            let ticks:u64=input.script.iter().map(|s| match s {Step::Tick{tick_ms}=>*tick_ms,_=>0}).sum();
            if !contexts_ok || input.events.len()+input.script.len() > 4096 || input.tick_ms > 1000 || ticks > 600_000 {
                return Err("invalid replay context/count/tick".into());
            }
            let client=input.tmux_client.clone().map(|tty| mios_inputd::runtime::TmuxClient{pid:0,tty,socket:"/tmp/tmux-socket".into()});
            let mut router = mios_inputd::router::Router::new(global.clone(), legacy, input.axes);
            router.analog_triggers=input.analog_triggers;
            router.tmux=client.is_some();
            router.set_context(&input.context);
            let mut output = mios_inputd::router::Output::default();
            let mut now=1000u64;
            for event in input.events {
                output.extend(router.event(event, now));
                now+=10;
            }
            for step in input.script {
                match step {
                    Step::Event(e) => { output.extend(router.event(e, now)); now+=1; }
                    Step::Tick{tick_ms} => { for _ in 0..tick_ms/10 { output.extend(router.tick(0.01)); now+=10; } if tick_ms%10>0 { output.extend(router.tick((tick_ms%10) as f64/1000.0)); now+=tick_ms%10; } }
                    Step::Context{context} => output.extend(router.set_focus(&context, Some((format!("0x{context}"),1)), false)),
                }
            }
            output.extend(router.tick(input.tick_ms as f64 / 1000.0));
            // Each action's projection: argv, or the key strokes of a key: chord. Nothing is executed.
            let resolved:Vec<_>=output.actions.iter().map(|a| match resolve(&global,a,client.as_ref()) {
                Ok(v)=>serde_json::json!({"action":a,"projection":v}),
                Err(e)=>serde_json::json!({"action":a,"error":e}),
            }).collect();
            println!(
                "{}",
                serde_json::json!({"output":output,"pad":router.pad,"cursor":router.cursor,"profile":router.nav_profile(),"resolved":resolved,"executed":false})
            );
        }
        Operation::Check => println!(
            "{}",
            serde_json::json!({"valid":true,"bindings":global.bindings.len(),"controller":global.controller.enabled,"exclusive_grab":global.controller.exclusive_grab,"unresolved_actions":unresolved_actions(&global)})
        ),
        #[cfg(target_os = "linux")]
        Operation::Observe { device, seconds }
        | Operation::Bridge {
            device, seconds, ..
        } => {
            let write_pad = bridge && (!fixture_only || pad_override.is_some());
            let pad_path = pad_override.unwrap_or_else(|| global.controller.legacy_pad_state_path.clone());
            mios_inputd::runtime::run(
                global,
                legacy,
                mios_inputd::runtime::Options {
                    root: args.root,
                    bridge,
                    fixture_only,
                    device,
                    context: None,
                    duration: seconds.map(std::time::Duration::from_secs),
                    pad_path,
                    live_context,
                    write_pad,
                },
            )?;
        }
        _ => return Err("operation unsupported on this platform".into()),
    }
    Ok(())
}
fn main() -> std::process::ExitCode {
    match run() {
        Ok(()) => std::process::ExitCode::SUCCESS,
        Err(e) => {
            eprintln!("mios-inputd: {e}");
            std::process::ExitCode::FAILURE
        }
    }
}

/// The projection of one action without executing it: key: strokes, a tmux verb's argv (needs a client), or argv.
#[cfg(target_os = "linux")]
fn resolve(global: &GlobalConfig, action: &str, client: Option<&mios_inputd::runtime::TmuxClient>) -> Result<serde_json::Value, String> {
    if let Some(chord) = action.strip_prefix("key:") {
        let c = mios_inputd::nav::parse_key_chord(chord)?;
        return Ok(serde_json::json!({"keyboard":"mios-vctl-keyboard","events":mios_inputd::nav::stroke(&c),"keysym":mios_inputd::nav::keysym(&c)}));
    }
    if let Some(verb) = action.strip_prefix("tmux:") {
        let c = client.ok_or("tmux: needs a tmux client (none under the focused terminal: the *_plain profile applies)")?;
        let mut argv = serde_json::json!(mios_inputd::runtime::tmux_argv(global, c, verb)?);
        if let Some(d) = verb.strip_prefix("focus ") {
            let k = match d { "left" => "h", "down" => "j", "up" => "k", _ => "l" };
            argv = serde_json::json!({"probe": argv, "at_edge": format!("hypr:movefocus {d}"), "inside": ["send-keys","-c",c.tty,"-K",global.tmux_prefix,k]});
        }
        return Ok(argv);
    }
    if action.starts_with("pointer:") {
        return Ok(serde_json::json!({"pointer":"mios-vctl-pointer","analog":true}));
    }
    Ok(serde_json::json!(mios_inputd::runtime::configured_command(action, global)?))
}

fn unresolved_actions(global: &GlobalConfig) -> Vec<String> {
    #[cfg(target_os = "linux")]
    {
        mios_inputd::runtime::unsupported_actions(global)
    }
    #[cfg(not(target_os = "linux"))]
    {
        let _ = global;
        vec![]
    }
}
