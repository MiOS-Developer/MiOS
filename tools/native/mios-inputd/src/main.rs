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
    Map {
        /// The readable controller map (the tmux:keymap popup's text) instead of JSON.
        #[arg(long)]
        text: bool,
    },
    ResolveAction {
        action: String,
        /// tmux: verbs resolve against this client tty (on --tmux-socket); discovery finds both on the desktop.
        #[arg(long)]
        tmux_client: Option<String>,
        #[arg(long, default_value = "/tmp/tmux-socket")]
        tmux_socket: PathBuf,
    },
    Replay,
    /// I3 (C-18): run a VT tmux client on a pty; while it has mouse mode on, gpm (or evdev pointer) events reach it as
    /// SGR 1006 reports. The VT itself never enters mouse reporting.
    ConsoleBridge {
        /// The VT number gpm routes by (default: fd 0's /dev/tty<N>).
        #[arg(long)]
        vt: Option<u32>,
        /// Pointer source override: gpm, evdev or off (default: the SSOT's controller.console.mouse).
        #[arg(long)]
        mouse: Option<String>,
        #[arg(long)]
        gpm_socket: Option<PathBuf>,
        /// evdev source: this node (default: the first device named in controller.console.pointer_names).
        #[arg(long)]
        pointer_device: Option<PathBuf>,
        /// JSON-lines log for tests (never the terminal).
        #[arg(long)]
        log: Option<PathBuf>,
        #[arg(last = true, required = true)]
        argv: Vec<String>,
    },
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
        /// Fixture tier: read the seat view from this private copy of logind's runtime state (the physical bridge
        /// reads /run/systemd): console context, the graphical VT, and INV-8 release when another session is active.
        #[arg(long, requires = "fixture_only")]
        logind_state: Option<PathBuf>,
        /// Fixture tier: the tty that stands in for the active VT (e.g. a test pty).
        #[arg(long, requires = "fixture_only")]
        console_tty: Option<PathBuf>,
        /// Fixture tier: an inherited fd (a pty master standing in for the VT) that receives console key: strokes as
        /// the bytes the Linux console keymap delivers.
        #[arg(long, requires = "fixture_only")]
        console_sink_fd: Option<i32>,
        /// Fixture tier: re-select the compositor when the followed socket dies (use a private XDG_RUNTIME_DIR).
        #[arg(long, requires = "fixture_only")]
        rediscover: bool,
        /// I4: route the controllers assigned to the nested session of this seat (from the [desktop.nested]
        /// registry) into that session: its compositor, its private room packet. With --fixture-only only fixture
        /// pads; without it any assigned pad. The host bridge never owns an assigned node.
        #[arg(long, conflicts_with_all = ["device", "live_context", "pad_path", "logind_state", "console_tty", "console_sink_fd", "rediscover"])]
        nested: Option<String>,
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
    /// I4: the agent virtual controller: a per-session uinput gamepad driven with eased analog ramps (JSON out; the
    /// same argv is the [verbs.virtual_controller] tool). ops: create, stick, trigger, button, dpad, release, wait,
    /// status, info, assign, unassign, destroy, list.
    Vpad(VpadArgs),
}
#[derive(clap::Args)]
struct VpadArgs {
    op: String,
    /// The pad: a seat label (`create --seat`) or a pad id.
    #[arg(long, default_value = "agent")]
    pad: String,
    /// create: a seat's pad, named by [desktop.nested.devices].virtual_pad_name so `mios-gen nested-session --seat
    /// <seat> --device virtual-pad` binds it; the pad key is the seat. assign: the session's seat (default: the pad's).
    #[arg(long)]
    seat: Option<String>,
    #[arg(long)]
    stick: Option<String>,
    #[arg(long, allow_hyphen_values = true)]
    x: Option<f64>,
    #[arg(long, allow_hyphen_values = true)]
    y: Option<f64>,
    #[arg(long)]
    side: Option<String>,
    #[arg(long)]
    value: Option<f64>,
    #[arg(long)]
    button: Option<String>,
    #[arg(long)]
    action: Option<String>,
    #[arg(long)]
    dir: Option<String>,
    /// Ramp (or tap) length; stretched to [agent_pad].min_ms at least.
    #[arg(long)]
    ms: Option<u64>,
    /// Hold the target this long, then ease back to rest over the same ramp.
    #[arg(long)]
    hold_ms: Option<u64>,
    #[arg(long)]
    ease: Option<String>,
    /// Return only when every ramp has finished.
    #[arg(long, num_args = 0..=1, default_missing_value = "true", value_parser = clap::builder::BoolishValueParser::new())]
    wait: Option<bool>,
    /// Test tier: name the pad mios-input-fixture-vpad-<pad> (only fixture bridges open it; assign runs a fixture bridge).
    #[arg(long, hide = true)]
    fixture: bool,
    /// hold: the pad is a seat's pad (internal; create passes it).
    #[arg(long, hide = true)]
    seat_pad: bool,
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
    #[cfg(target_os = "linux")]
    if let Operation::Vpad(v) = &args.command {
        return vpad(&global, args.root.as_deref(), v);
    }
    #[cfg(target_os = "linux")]
    if let Operation::ConsoleBridge { vt, mouse, gpm_socket, pointer_device, log, argv } = &args.command {
        let c = &global.controller.console;
        let gpm_socket = gpm_socket.clone().unwrap_or_else(|| c.gpm_socket.clone());
        let mouse = match mouse.as_deref().unwrap_or(&c.mouse) {
            "auto" if gpm_socket.exists() => "gpm".to_owned(),
            "auto" => "evdev".to_owned(),
            m @ ("gpm" | "evdev" | "off") => m.to_owned(),
            m => return Err(format!("--mouse {m}: gpm, evdev or off")),
        };
        let code = mios_inputd::console::run(mios_inputd::console::Options {
            argv: argv.clone(), vt: *vt, mouse, gpm_socket, pointer_device: pointer_device.clone(),
            pointer_names: c.pointer_names.clone(), cell_px: c.cell_px, log: log.clone(),
        })?;
        std::process::exit(code);
    }
    let (bridge, fixture_only, live_context, pad_override) = match &args.command {
        Operation::Bridge { fixture_only, live_context, pad_path, .. } => (true, *fixture_only, *live_context, pad_path.clone()),
        _ => (false, false, false, None),
    };
    let (logind_state, console_tty, console_sink_fd, rediscover) = match &args.command {
        Operation::Bridge { logind_state, console_tty, console_sink_fd, rediscover, .. } => (logind_state.clone(), console_tty.clone(), *console_sink_fd, *rediscover),
        _ => (None, None, None, false),
    };
    // I4 --nested: resolve the session in the host runtime, then follow it (its runtime, compositor and display).
    #[cfg(target_os = "linux")]
    let (nested, pad_override) = match &args.command {
        Operation::Bridge { nested: Some(seat), .. } => {
            let rt = std::env::var_os("XDG_RUNTIME_DIR").map(PathBuf::from).ok_or("XDG_RUNTIME_DIR missing")?;
            let s = mios_inputd::nested::session(&rt.join(&global.nested_registry), std::path::Path::new("/proc"), seat)?;
            // Single-threaded here: no other thread reads the environment yet. Action children (wpctl, playerctl)
            // keep the host's PipeWire and user bus: the session runtime has neither (D3).
            for (k, v) in mios_inputd::nested::host_service_env(&rt, |k| std::env::var(k).ok()) {
                std::env::set_var(k, v);
            }
            std::env::set_var("XDG_RUNTIME_DIR", &s.runtime);
            std::env::set_var("HYPRLAND_INSTANCE_SIGNATURE", &s.instance);
            std::env::set_var("WAYLAND_DISPLAY", &s.wayland_display);
            std::env::remove_var("DISPLAY");
            eprintln!("{}", serde_json::json!({"nested":"follow","seat":seat,"runtime":s.runtime,"instance":s.instance,"pad_state":s.pad_state,"controllers":s.controllers}));
            let pad = Some(s.pad_state.clone());
            (Some(s), pad)
        }
        _ => (None, pad_override),
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
        Operation::Map { text: true } => print!("{}", global.controller.nav.text(global.controller.capture_hold_ms)),
        Operation::Map { text: false } => println!("{}", serde_json::json!({"global":global,"hypr3d":legacy})),
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
            let devices = device.into_iter().collect();
            let pad_path = pad_override.unwrap_or_else(|| global.controller.legacy_pad_state_path.clone());
            mios_inputd::runtime::run(
                global,
                legacy,
                mios_inputd::runtime::Options {
                    root: args.root,
                    bridge,
                    fixture_only,
                    devices,
                    context: None,
                    duration: seconds.map(std::time::Duration::from_secs),
                    pad_path,
                    live_context,
                    write_pad,
                    logind_state,
                    console_tty,
                    console_sink_fd,
                    rediscover,
                    nested,
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
    if action == "mios:graphical-vt" {
        // C-17: resolved at fire time from logind (this user's graphical session's VTNr); none -> nothing is sent.
        return Ok(serde_json::json!({"keyboard":"mios-vctl-keyboard","vt_switch":"CTRL+ALT+F<VTNr of this user's graphical session>"}));
    }
    if action == mios_inputd::nav::CURSOR {
        return Ok(serde_json::json!({"cursor_mode":"toggle","pointer":"mios-vctl-pointer"}));
    }
    if action.starts_with("pointer:") {
        return Ok(serde_json::json!({"pointer":"mios-vctl-pointer","analog":true}));
    }
    Ok(serde_json::json!(mios_inputd::runtime::configured_command(action, global)?))
}

/// `mios-inputd vpad <op>`: every op prints one JSON object (the MCP tool reads it as its result).
#[cfg(target_os = "linux")]
fn vpad(global: &GlobalConfig, root: Option<&std::path::Path>, v: &VpadArgs) -> Result<(), String> {
    use mios_inputd::vpad;
    let key = v.seat.clone().filter(|_| v.op == "create").unwrap_or_else(|| v.pad.clone());
    let out = match v.op.as_str() {
        "hold" => return vpad::hold(global.clone(), root.map(Into::into), &v.pad, v.seat_pad, v.fixture),
        "create" => {
            let r = vpad::create(global, root, &key, v.seat.is_some(), v.fixture)?;
            serde_json::json!({"ok": true, "created": r, "next": if v.seat.is_some() {
                format!("mios-gen nested-session --layout pip --seat {key} --device {}; then mios-inputd vpad assign --pad {key}", if v.fixture { format!("name:{}", r["name"].as_str().unwrap_or("")) } else { "virtual-pad".into() })
            } else if v.fixture { "a fixture pad: only fixture-tier bridges open it".into() } else { "the session's own bridge routes this pad like any controller".into() }})
        }
        "list" => vpad::list(global)?,
        op @ ("stick" | "trigger" | "button" | "dpad" | "release" | "status" | "info" | "assign" | "unassign" | "destroy" | "wait") => {
            let mut req = serde_json::json!({"op": op});
            let o = req.as_object_mut().unwrap();
            let mut put = |k: &str, val: serde_json::Value| { if !val.is_null() { o.insert(k.into(), val); } };
            put("stick", serde_json::json!(v.stick)); put("x", serde_json::json!(v.x)); put("y", serde_json::json!(v.y));
            put("side", serde_json::json!(v.side)); put("value", serde_json::json!(v.value)); put("button", serde_json::json!(v.button));
            put("action", serde_json::json!(v.action)); put("dir", serde_json::json!(v.dir)); put("ms", serde_json::json!(v.ms));
            put("hold_ms", serde_json::json!(v.hold_ms)); put("ease", serde_json::json!(v.ease)); put("seat", serde_json::json!(v.seat));
            let mut r = vpad::request(global, &v.pad, &req)?;
            if v.wait == Some(true) && !["wait", "destroy", "status", "info"].contains(&op) {
                r = serde_json::json!({"reply": r, "wait": vpad::request(global, &v.pad, &serde_json::json!({"op": "wait"}))?});
            }
            r
        }
        op => return Err(format!("vpad {op}: create, stick, trigger, button, dpad, release, wait, status, info, assign, unassign, destroy or list")),
    };
    println!("{out}");
    Ok(())
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
