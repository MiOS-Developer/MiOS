// AI-hint: Opt-in native kernel proof of controller return. Only uniquely named private uinput fixtures are opened;
// parent/child bridges use isolated runtimes, explicit context and the production Claim/control/evdev paths.
// AI-related: nested_session.rs, nested_devices.rs, mios-inputd/src/runtime.rs, task_01M6VYCEBMPNBPJT5DJBVZSDC3
use super::*;
use mios_inputd::linux::{GrabProbe, VirtualDevice};
use mios_inputd::router::{Event, EV_ABS, EV_KEY, EV_SYN, SYN_REPORT};

const HELPER: &str = "nested_session::controller_return_live::owned_bridge_helper";
struct OwnedBridge(std::process::Child, PathBuf);
impl Drop for OwnedBridge {
    fn drop(&mut self) {
        if thread::panicking() {
            eprintln!(
                "owned bridge {} log: {}",
                self.0.id(),
                fs::read_to_string(&self.1).unwrap_or_default()
            );
        }
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}
fn wait_for(mut ready: impl FnMut() -> bool, description: &str) {
    let deadline = Instant::now() + Duration::from_secs(12);
    while !ready() {
        assert!(Instant::now() < deadline, "timed out: {description}");
        thread::sleep(Duration::from_millis(20));
    }
}
fn manifest(runtime: &Path) -> Json {
    devices::read_return_json(&runtime.join("mios-inputd-active.json")).unwrap_or(Json::Null)
}
fn has_node(pid: u32, node: &Path) -> bool {
    fs::read_dir(format!("/proc/{pid}/fd"))
        .into_iter()
        .flatten()
        .flatten()
        .any(|fd| fs::read_link(fd.path()).is_ok_and(|path| path == node))
}
fn bridge(root: &Path, runtime: &Path, device: &Path, nested: bool, empty: &Path) -> OwnedBridge {
    let log =
        fs::File::create(runtime.join(if nested { "child.log" } else { "parent.log" })).unwrap();
    let mut command = Command::new(std::env::current_exe().unwrap());
    command
        .args(["--exact", HELPER, "--ignored", "--nocapture"])
        .env(
            "MIOS_RETURN_KERNEL_HELPER",
            if nested { "child" } else { "parent" },
        )
        .env("MIOS_RETURN_KERNEL_DEVICE", device)
        .env("XDG_RUNTIME_DIR", runtime)
        .env("MIOS_VENDOR_TOML", root.join("usr/share/mios/mios.toml"))
        .env("MIOS_VENDOR_TOML_D", empty)
        .env("MIOS_HOST_TOML", empty.join("host.toml"))
        .env("MIOS_HOST_TOML_D", empty)
        .env("MIOS_USER_TOML", empty.join("user.toml"))
        .env("MIOS_USER_TOML_D", empty)
        .env_remove("MIOS_TOML")
        .env_remove("HYPRLAND_INSTANCE_SIGNATURE")
        .env_remove("WAYLAND_DISPLAY")
        .stdin(Stdio::null())
        .stdout(log.try_clone().unwrap())
        .stderr(log);
    OwnedBridge(
        command.spawn().unwrap(),
        runtime.join(if nested { "child.log" } else { "parent.log" }),
    )
}

#[test]
#[ignore = "private subprocess entry point; invoked only by controller_return_owned_kernel"]
fn owned_bridge_helper() {
    let mode = env::var("MIOS_RETURN_KERNEL_HELPER").expect("owned test helper only");
    assert!(["parent", "child"].contains(&mode.as_str()));
    let runtime = PathBuf::from(env::var_os("XDG_RUNTIME_DIR").unwrap());
    let device = PathBuf::from(env::var_os("MIOS_RETURN_KERNEL_DEVICE").unwrap());
    let (global, legacy) = mios_inputd::GlobalConfig::load(None).unwrap();
    let nested = (mode == "child").then(|| {
        mios_inputd::nested::session(
            &runtime.join(&global.nested_registry),
            Path::new("/proc"),
            "kernel-return",
        )
        .unwrap()
    });
    let active_runtime = nested
        .as_ref()
        .map(|s| s.runtime.clone())
        .unwrap_or(runtime);
    env::set_var("XDG_RUNTIME_DIR", &active_runtime);
    mios_inputd::runtime::run(
        global,
        legacy,
        mios_inputd::runtime::Options {
            root: None,
            bridge: true,
            fixture_only: true,
            devices: vec![device],
            context: Some("desktop-3d".into()),
            duration: Some(Duration::from_secs(45)),
            pad_path: active_runtime.join("unused-pad.bin"),
            live_context: false,
            write_pad: false,
            logind_state: None,
            console_tty: None,
            console_sink_fd: None,
            rediscover: false,
            nested,
        },
    )
    .unwrap();
}

#[test]
#[ignore = "requires MIOS_LIVE_CONTROLLER_RETURN=1 and the native host's /dev/uinput; never a physical pad"]
fn controller_return_owned_kernel() {
    assert_eq!(env::var("MIOS_LIVE_CONTROLLER_RETURN").as_deref(), Ok("1"));
    let root_path = env::var_os("MIOS_CONTROLLER_RETURN_ROOT")
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            Path::new(env!("CARGO_MANIFEST_DIR"))
                .ancestors()
                .nth(3)
                .unwrap()
                .to_owned()
        });
    let root = root_path.as_path();
    let temp = tempfile::Builder::new()
        .prefix("mios-return-")
        .tempdir()
        .unwrap();
    let parent = temp.path().join("parent");
    let owned = temp.path().join("child");
    let empty = temp.path().join("empty");
    for dir in [&parent, &owned, &empty] {
        fs::create_dir(dir).unwrap();
        fs::set_permissions(dir, fs::Permissions::from_mode(0o700)).unwrap();
    }
    let name = format!("mios-input-fixture-return-{}", std::process::id());
    let mut pad = VirtualDevice::agent_pad(&name, 0x054c, 0x0ce6).unwrap();
    let mut node = None;
    wait_for(
        || {
            node = pad.event_path().ok();
            node.is_some()
        },
        "owned uinput enumeration",
    );
    let node = node.unwrap();
    wait_for(|| fs::File::open(&node).is_ok(), "owned uinput read ACL");
    let probe = GrabProbe::open(&node).unwrap();
    let mut parent_bridge = bridge(root, &parent, &node, false, &empty);
    wait_for(
        || {
            manifest(&parent)["devices"].as_array().is_some_and(|ds| {
                ds.iter()
                    .any(|d| d["path"] == json!(node) && d["rearmed"] == true)
            })
        },
        "parent neutral grab",
    );
    assert_eq!(
        manifest(&parent)["controller_scan_ms"],
        2000,
        "exercise the vendor cadence unchanged"
    );
    let registry = parent.join("mios/nested-sessions");
    let device = Device {
        path: node.clone(),
        spec: "owned-kernel-fixture".into(),
        name,
        kind: "controller".into(),
        virtual_node: true,
        siblings: vec![],
    };
    let descriptor = owned.join("session.json");
    fs::write(
        &descriptor,
        json!({"seat":"kernel-return","devices":[device.to_json()]}).to_string(),
    )
    .unwrap();
    let listener = UnixListener::bind(owned.join("control.sock")).unwrap();
    let mut claim = devices::Claim::take(&registry, "kernel-return", &[device], true,
        json!({"descriptor":descriptor,"runtime":owned,"state":"running","instance":"owned-kernel-context",
        "wayland_display":"owned-kernel-context","pad_state":owned.join("unused-pad.bin"),
        "control_socket":owned.join("control.sock"),"control_protocol_version":1})).unwrap();
    wait_for(
        || !has_node(parent_bridge.0.id(), &node),
        "parent releases claimed node",
    );
    let mut child_bridge = bridge(root, &parent, &node, true, &empty);
    wait_for(
        || {
            owned.join("inputd-control.sock").exists()
                && has_node(child_bridge.0.id(), &node)
                && probe.held_elsewhere()
        },
        "child kernel grab and control socket",
    );
    let original = fs::read(registry.join("kernel-return.json")).unwrap();
    let original_descriptor = fs::read(&descriptor).unwrap();
    // Actual evdev snapshots must defer for buttons, sticks and analog triggers.
    for (name, kind, code, held) in [
        ("button", EV_KEY, 0x130, 1),
        ("stick", EV_ABS, 0, 22_000),
        ("trigger", EV_ABS, 2, 255),
    ] {
        probe
            .emit(|| {
                pad.write(&[
                    Event::new(kind, code, held),
                    Event::new(EV_SYN, SYN_REPORT, 0),
                ])
            })
            .unwrap();
        let (mut client, socket) = UnixStream::pair().unwrap();
        writeln!(client, "{}", json!({"action":"return_controller","nonce":format!("held-kernel-{name}"),"seat":"kernel-return"})).unwrap();
        handle_control_connection(socket, &mut claim, &registry, &parent, &owned).unwrap();
        let deferred = read_control_frame(&mut client, Duration::from_secs(1)).unwrap();
        assert_eq!(deferred["ok"], false, "{name}: {deferred}");
        assert_eq!(deferred["reason"], "controls_held", "{name}: {deferred}");
        assert_eq!(
            fs::read(registry.join("kernel-return.json")).unwrap(),
            original
        );
        assert_eq!(fs::read(&descriptor).unwrap(), original_descriptor);
        assert!(has_node(child_bridge.0.id(), &node) && probe.held_elsewhere());
        probe
            .emit(|| pad.write(&[Event::new(kind, code, 0), Event::new(EV_SYN, SYN_REPORT, 0)]))
            .unwrap();
    }
    // Recovery ACK only schedules acquisition. Hold the actual kernel grab across
    // abort_release and prove the child survives contention, then reacquires.
    let (_, child_peer) =
        child_return_exchange(&owned, claim.entry(), &node, "return_capabilities", None).unwrap();
    let (released, _) = child_return_exchange(
        &owned,
        claim.entry(),
        &node,
        "release_device",
        Some(child_peer),
    )
    .unwrap();
    assert_eq!(released["ok"], true, "{released}");
    let mut raw = mios_inputd::linux::Device::open(&node, true).unwrap();
    raw.grab().unwrap();
    let (armed, _) = child_return_exchange(
        &owned,
        claim.entry(),
        &node,
        "abort_release",
        Some(child_peer),
    )
    .unwrap();
    assert_eq!(armed["ok"], true, "{armed}");
    thread::sleep(Duration::from_millis(4200));
    assert!(
        child_bridge.0.try_wait().unwrap().is_none(),
        "child must survive a raw consumer during recovery"
    );
    assert!(raw.grabbed());
    raw.release();
    drop(raw);
    wait_for(
        || has_node(child_bridge.0.id(), &node) && probe.held_elsewhere(),
        "child actually reacquires after raw holder exits",
    );
    assert_eq!(
        fs::read(registry.join("kernel-return.json")).unwrap(),
        original
    );
    let request_parent = parent.clone();
    let request_registry = registry.clone();
    let client = thread::spawn(move || {
        return_controller(
            &request_parent,
            &request_registry,
            &Options {
                seat: Some("kernel-return".into()),
                ..Default::default()
            },
        )
    });
    let (socket, _) = listener.accept().unwrap();
    handle_control_connection(socket, &mut claim, &registry, &parent, &owned).unwrap();
    let response = client.join().unwrap().unwrap();
    assert_eq!(response["parent_acknowledged"], true, "{response}");
    assert_eq!(response["rearmed"], true);
    assert!(
        !has_node(child_bridge.0.id(), &node),
        "child's actual evdev fd must be closed"
    );
    assert!(has_node(parent_bridge.0.id(), &node) && probe.held_elsewhere());
    assert_eq!(
        manifest_identity(&manifest(&parent)),
        Some((
            parent_bridge.0.id(),
            devices::start_time(parent_bridge.0.id()).unwrap()
        ))
    );
    for record in [registry.join("kernel-return.json"), descriptor] {
        let record = devices::read_return_json(&record).unwrap();
        assert_eq!(record["seat"], "kernel-return");
        assert!(record["devices"].as_array().unwrap().is_empty());
    }
    assert!(parent_bridge.0.try_wait().unwrap().is_none());
    assert!(child_bridge.0.try_wait().unwrap().is_none());
    println!(
        "kernel-controller-return: {}",
        json!({"scope":"owned uinput; no compositor, game, PiP or physical-pad acceptance", "node":node,
        "parent_pid":parent_bridge.0.id(),"child_pid":child_bridge.0.id(),"held_control_deferred":true,"held_controls_verified":["button","stick","analog_trigger"],"raw_recovery_reacquired":true,"return":response})
    );
}
