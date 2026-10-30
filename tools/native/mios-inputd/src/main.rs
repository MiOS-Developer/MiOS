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
    Context,
    Serve,
    Dispatch { action: String },
    VmAction { action: String },
    Map,
    ResolveAction {
        action: String,
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
    },
    Fixture,
    #[command(hide = true)]
    FixtureGrab {
        device: PathBuf,
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
    let (global, legacy) = GlobalConfig::load(args.root.as_deref())?;
    let (bridge, fixture_only) = match &args.command {
        Operation::Bridge { fixture_only, .. } => (true, *fixture_only),
        _ => (false, false),
    };
    match args.command {
        Operation::Context => {
            let c=mios_inputd::runtime::context(&global)?;
            println!("{}",serde_json::json!({"context":c.name,"address":c.address,"pid":c.pid,"room_capture":c.room_capture}));
        }
        Operation::Dispatch { action } => {
            use std::os::unix::process::CommandExt;
            let argv=mios_inputd::runtime::configured_command(&action,&global)?;
            let (program,rest)=argv.split_first().ok_or("action has no external dispatcher")?;
            return Err(std::process::Command::new(program).args(rest).exec().to_string());
        }
        Operation::Serve => {
            use std::os::unix::process::CommandExt;
            let fallback=global.controller.recovery_argv.clone();
            if global.enabled && global.controller.enabled && global.controller.exclusive_grab {
                let pad_path=global.controller.legacy_pad_state_path.clone();
                let result=mios_inputd::runtime::run(global,legacy,mios_inputd::runtime::Options {root:args.root,bridge:true,fixture_only:false,device:None,context:None,duration:None,pad_path});
                if result.is_ok() { return Ok(()); }
                eprintln!("native controller recovery: {}",result.unwrap_err());
            }
            let (program, rest) = fallback.split_first().ok_or("controller recovery_argv is empty")?;
            return Err(std::process::Command::new(program).args(rest).exec().to_string());
        }
        Operation::VmAction { action } => mios_inputd::runtime::vm_action(&action, &global)?,
        #[cfg(target_os = "linux")]
        Operation::ResolveAction { action } => println!(
            "{}",
            serde_json::json!({"action":action,"argv":mios_inputd::runtime::configured_command(&action,&global)?,"executed":false})
        ),
        Operation::Map => println!("{}", serde_json::json!({"global":global,"hypr3d":legacy})),
        Operation::Replay => {
            use std::io::Read;
            #[derive(serde::Deserialize)]
            struct Input {
                context: String,
                #[serde(default)]
                axes: std::collections::BTreeMap<u16, mios_inputd::router::Axis>,
                events: Vec<mios_inputd::router::Event>,
                #[serde(default)]
                tick_ms: u64,
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
            if !mios_inputd::config::CONTEXTS.contains(&input.context.as_str())
                || input.events.len() > 4096
                || input.tick_ms > 1000
            {
                return Err("invalid replay context/count/tick".into());
            }
            let mut router = mios_inputd::router::Router::new(global, legacy, input.axes);
            router.set_context(&input.context);
            let mut output = mios_inputd::router::Output::default();
            for (i, event) in input.events.into_iter().enumerate() {
                output.extend(router.event(event, 1000 + i as u64 * 10));
            }
            // The router clamps one tick to 100 ms (a stalled loop never jumps), so a longer replay clock advances in
            // 100 ms steps: a [tap, hold] hold or North hold can be replayed (tick_ms <= 100 is one tick, as before).
            let mut left = input.tick_ms;
            loop {
                let step = left.min(100);
                output.extend(router.tick(step as f64 / 1000.0));
                left -= step;
                if left == 0 {
                    break;
                }
            }
            println!(
                "{}",
                serde_json::json!({"output":output,"pad":router.pad,"cursor":router.cursor,"executed":false})
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
            let pad_path = global.controller.legacy_pad_state_path.clone();
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
