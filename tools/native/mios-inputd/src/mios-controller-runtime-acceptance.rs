// AI-hint: Unapplied cfg(test) controller acceptance module; no device discovery or physical input.
// AI-related: runtime::emit, OwnershipWorker, ActionWorker, SessionWorker, frozen controller vectors
use super::*;
use crate::router::{Axis, Event, EV_ABS, EV_KEY};
use std::sync::{Arc, Mutex};

fn isolated() -> bool {
    let hash = Command::new("/usr/bin/sha256sum")
        .arg("/usr/bin/tmux")
        .output()
        .unwrap();
    assert!(hash.status.success());
    assert_eq!(String::from_utf8(hash.stdout).unwrap().split_whitespace().next(),
        Some("95b479aafe4c4d454cbe029fa22677a9e7043de28def816ea4ff2d7470af900e"),
        "acceptance requires the exact sealed C95 backend in isolated DEV; never run against host C0");
    let name = std::thread::current().name().unwrap().to_owned();
    if std::env::var("MIOS_CONTROLLER_ACCEPTANCE_CHILD")
        .ok()
        .as_deref()
        == Some(&name)
    {
        return false;
    }
    let runtime = tempfile::Builder::new()
        .prefix("mios-controller-runtime-")
        .tempdir()
        .unwrap();
    let output = Command::new(std::env::current_exe().unwrap())
        .args(["--exact", &name, "--nocapture", "--test-threads=1"])
        .env("MIOS_CONTROLLER_ACCEPTANCE_CHILD", &name)
        .env("XDG_RUNTIME_DIR", runtime.path())
        .env("HYPRLAND_INSTANCE_SIGNATURE", "mios-controller-acceptance")
        .env_remove("TMUX")
        .env_remove("TMUX_PANE")
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "{}{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(
        String::from_utf8_lossy(&output.stdout).contains("1 passed"),
        "child must execute requested test"
    );
    true
}
fn data(name: &str) -> serde_json::Value {
    serde_json::from_str(match name {
        "pending" => include_str!("mios-controller-acceptance-pending-cancellation.json"),
        "neutral" => include_str!("mios-controller-acceptance-neutralization.json"),
        "witness" => include_str!("mios-controller-acceptance-verified-target.json"),
        _ => panic!("unknown frozen data"),
    })
    .unwrap()
}
fn trace(id: &str, value: serde_json::Value) {
    crate::runtime::acceptance_observer::trace(id, value);
}
fn axes() -> BTreeMap<u16, Axis> {
    [0, 1, 2, 3, 4, 5]
        .into_iter()
        .map(|code| {
            (
                code,
                Axis {
                    min: if [2, 5].contains(&code) { 0 } else { -32768 },
                    max: if [2, 5].contains(&code) { 255 } else { 32767 },
                    value: 0,
                },
            )
        })
        .collect()
}
struct Harness {
    dir: tempfile::TempDir,
    device: RunningDevice,
    sinks: Sinks,
    focus: ContextSnapshot,
    now: u64,
    requests: Vec<String>,
}
impl Harness {
    fn new(context: &str, captured_nav: bool) -> Self {
        let (cfg, legacy) = crate::config::tests::fixture_mon();
        Self::configured(context, captured_nav, cfg, legacy)
    }
    fn configured(
        context: &str,
        captured_nav: bool,
        mut cfg: GlobalConfig,
        legacy: LegacyConfig,
    ) -> Self {
        // The older camera fixtures deliberately keep the legacy global policy.
        // These shared-control cases use the actual vendor chords and singles,
        // retaining each fixture's exact app/process and transport witnesses.
        let vendor: toml::Value = include_str!("../../../../usr/share/mios/mios.toml")
            .parse()
            .unwrap();
        let (vendor, _) = GlobalConfig::from_value(&vendor).unwrap();
        cfg.controller.global_capture_toggle = vendor.controller.global_capture_toggle;
        cfg.controller.window_zoom_chord = vendor.controller.window_zoom_chord;
        cfg.controller.nav.start_options = vendor.controller.nav.start_options;
        for (name, profile) in &mut cfg.controller.nav.profiles {
            for code in [0x13a, 0x13b] {
                let input = crate::nav::Input::Button(code);
                profile.remove(&input);
                if let Some(value) = vendor
                    .controller
                    .nav
                    .profiles
                    .get(name)
                    .and_then(|p| p.get(&input))
                {
                    profile.insert(input, value.clone());
                }
            }
        }
        let dir = tempfile::Builder::new()
            .prefix("mios-controller-sinks-")
            .tempdir()
            .unwrap();
        let mut router = Router::new(cfg, legacy, axes());
        router.set_focus(context, Some(("focus-a".into(), 42)), false);
        if captured_nav {
            router.set_capture_navigation_context(Some("terminal"));
        }
        router.set_tmux(true);
        let mut focus = ContextSnapshot::new(context);
        focus.address = Some("focus-a".into());
        focus.pid = Some(42);
        let device = RunningDevice {
            device: Device::acceptance_file(&dir.path().join("source"), axes()).unwrap(),
            output: Some(VirtualDevice::acceptance_file(&dir.path().join("app")).unwrap()),
            router,
            dropped: false,
            session: None,
            session_error: None,
            session_worker: None,
            capture: None,
            remote_input: None,
            login_generation: 0,
            capture_recovery_at: Instant::now(),
            suspended: false,
            neutral_rearming: false,
        };
        let sinks = Sinks {
            pointer: Some(VirtualDevice::acceptance_file(&dir.path().join("pointer")).unwrap()),
            keyboard: Some(VirtualDevice::acceptance_file(&dir.path().join("keyboard")).unwrap()),
            remote_pointer: None,
            ownership_generation: 0,
            children: vec![],
            ownership: None,
            actions: None,
            bridge: true,
            execute_actions: true,
            project: false,
            console_sink: Some(std::fs::File::create(dir.path().join("console")).unwrap()),
            nested: false,
        };
        Self {
            dir,
            device,
            sinks,
            focus,
            now: 1,
            requests: vec![],
        }
    }
    fn dispatch(&mut self, output: Output) {
        // Guard fixture side effects before executing the real dispatcher. All
        // keyboard paths terminate in private regular files; no device constructor runs.
        for a in &output.actions {
            assert!(
                a.starts_with("input:")
                    || a.starts_with("key:")
                    || a.starts_with("tmux:")
                    || a == "none",
                "unexpected host-global action in isolated fixture: {a}"
            );
            if a.starts_with("input:")
                && !a.starts_with("input:window ")
                && a != "input:window-zoom"
                && !a.starts_with("input:app-key ")
            {
                assert!(
                    configured_command(a, self.device.router.policy())
                        .unwrap()
                        .is_empty(),
                    "fixture refuses a configured external input dispatcher"
                );
            }
        }
        self.requests.extend(output.actions.iter().cloned());
        emit(output, &mut self.device, &mut self.sinks, &self.focus).unwrap();
        assert!(
            self.sinks.children.is_empty(),
            "fixture must spawn no generic command"
        );
    }
    fn event(&mut self, kind: u16, code: u16, value: i32) {
        self.now += 1;
        let output = self
            .device
            .router
            .event(Event::new(kind, code, value), self.now);
        self.dispatch(output);
    }
    fn advance(&mut self, millis: u64) {
        // Router clamps one tick to 100ms. Drive the actual elapsed clock in
        // bounded pieces instead of mistaking a clamped 5s tick for 5s elapsed.
        let mut remaining = millis;
        while remaining > 0 {
            let step = remaining.min(100);
            self.now += step;
            let out = self.device.router.tick(step as f64 / 1000.0);
            self.dispatch(out);
            remaining -= step;
        }
    }
    fn bytes(&self, sink: &str) -> Vec<u8> {
        std::fs::read(self.dir.path().join(sink)).unwrap()
    }
    fn events(&self, sink: &str) -> Vec<Event> {
        crate::linux::acceptance_events(&self.dir.path().join(sink)).unwrap()
    }
    fn no_keyboard(&self) {
        assert!(self.bytes("keyboard").is_empty(), "raw keyboard fallback");
        assert!(self.bytes("console").is_empty(), "console/PTY fallback");
    }
    fn record(&self, id: &str) {
        let events = |name| {
            self.events(name)
                .iter()
                .map(|e| serde_json::json!([e.kind, e.code, e.value]))
                .collect::<Vec<_>>()
        };
        trace(
            id,
            serde_json::json!({"requests":self.requests,
            "app":events("app"),"pointer":events("pointer"),"keyboard":events("keyboard"),
            "console_bytes":self.bytes("console"),"physical_reader":false}),
        );
    }
    fn action_worker(&mut self, selection: SharedLogin, current: ContextSnapshot) {
        self.sinks.actions = Some(
            ActionWorker::spawn(
                Arc::new(Mutex::new(self.device.router.policy().clone())),
                selection,
                Arc::new(Mutex::new(current)),
            )
            .unwrap(),
        );
    }
    fn finish_actions(&mut self, expected: usize) -> Vec<serde_json::Value> {
        crate::runtime::acceptance_observer::wait_for_results(expected);
        drop(self.sinks.actions.take()); // Completion is observed before cancellation/Drop.
        crate::runtime::acceptance_observer::take()
    }
    fn boundary(&mut self, boundary: &str) {
        let output = match boundary {
            "focus-change" | "client-loss" | "client_loss" => {
                self.device.router.set_focus("terminal", None, false)
            }
            "login-loss" | "login_loss" => begin_login_handoff(
                &mut self.device.router,
                &mut self.device.login_generation,
                1,
                &mut self.device.neutral_rearming,
            )
            .unwrap(),
            "syn-dropped" => self
                .device
                .router
                .event(Event::new(EV_SYN, SYN_DROPPED, 0), self.now),
            "router-reset" | "device-disconnect" | "disconnect" => self.device.router.reset(),
            _ => panic!("unimplemented frozen boundary {boundary}"),
        };
        self.dispatch(output);
        self.device.router.set_app(None, None);
        self.focus.app = None;
        self.focus.app_target = None;
        self.focus.app_process = None;
        if matches!(boundary, "login-loss" | "login_loss") {
            self.focus.routing_generation = 1;
        }
    }
}
fn assert_balanced(events: &[Event], code: u16) {
    let key = events
        .iter()
        .filter(|e| e.kind == EV_KEY && e.code == code)
        .collect::<Vec<_>>();
    assert!(
        key.iter().any(|e| e.value == 1),
        "no actual forwarded hold for {code}"
    );
    assert_eq!(
        key.last().unwrap().value,
        0,
        "held {code} not released at sink"
    );
    let mut held = false;
    for e in key {
        match e.value {
            1 => {
                assert!(!held);
                held = true;
            }
            0 => held = false,
            _ => {}
        }
    }
    assert!(!held);
}

#[test]
fn frozen_six_pending_boundaries_cancel_old_members() {
    if isolated() {
        return;
    }
    let v = data("pending");
    let cases = v["cases"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|c| c["harness"] == "native-runtime-fixture")
        .collect::<Vec<_>>();
    assert_eq!(cases.len(), 6);
    for case in cases {
        let id = case["id"].as_str().unwrap();
        let mut h = Harness::new("terminal", false);
        let target = ("focus-a".into(), 42);
        h.device.router.set_app(Some("mon"), Some(&target));
        for step in case["steps"].as_array().unwrap() {
            if let Some(op) = step["operation"].as_str() {
                h.boundary(op);
            } else if let Some(ms) = step["tick_ms"].as_u64() {
                h.advance(ms);
            } else {
                h.event(
                    step["kind"].as_u64().unwrap() as u16,
                    step["code"].as_u64().unwrap() as u16,
                    step["value"].as_i64().unwrap() as i32,
                );
            }
        }
        h.record(id);
        assert!(
            !h.requests
                .iter()
                .any(|a| a.starts_with("input:window") || a.starts_with("input:app-key")),
            "old member/profile escaped boundary: {:?}",
            h.requests
        );
        h.no_keyboard();
    }
}
#[test]
fn frozen_six_neutralization_boundaries_balance_real_sinks() {
    if isolated() {
        return;
    }
    let v = data("neutral");
    assert_eq!(v["cases"].as_array().unwrap().len(), 6);
    for case in v["cases"].as_array().unwrap() {
        let mut h = Harness::new("app-capture", false);
        h.device.router.cursor = true; // actual half-capture: app buttons + pointer trigger.
        h.event(EV_KEY, 304, 1);
        // Use the actual configured legacy pointer trigger, not a test-only hold mutation.
        let (_, legacy) = crate::config::tests::fixture_mon();
        let pointer_button = crate::config::button_code(&legacy.grab_button).unwrap();
        h.event(EV_KEY, pointer_button, 1);
        for (code, value) in [
            (0, 23000),
            (1, -23000),
            (3, 18000),
            (4, -18000),
            (2, 255),
            (5, 255),
        ] {
            h.event(EV_ABS, code, value);
        }
        h.event(EV_KEY, 314, 1);
        let start = h.events("app").len();
        h.boundary(case["boundary"].as_str().unwrap());
        h.record(case["id"].as_str().unwrap());
        assert_balanced(&h.events("app"), 304);
        assert_balanced(&h.events("pointer"), 0x110);
        let app = h.events("app");
        for code in [0, 1, 2, 3, 4, 5] {
            let axis = app[start..]
                .iter()
                .rev()
                .find(|e| e.kind == EV_ABS && e.code == code)
                .unwrap();
            assert_eq!(axis.value, 0, "axis {code} remains deflected");
        }
        let pad = &h.device.router.pad;
        assert_eq!((pad.lx, pad.ly, pad.rx, pad.ry), (0.0, 0.0, 0.0, 0.0));
        assert!(!pad.lt && !pad.rt && !pad.south && !pad.west);
        let after_app = h.bytes("app").len();
        let after_pointer = h.bytes("pointer").len();
        h.advance(1000);
        assert!(
            h.events("pointer")
                .iter()
                .skip(after_pointer / std::mem::size_of::<libc::input_event>())
                .all(|e| e.kind != crate::router::EV_REL || e.value == 0),
            "cached pointer motion after boundary"
        );
        assert!(
            h.events("app")
                .iter()
                .skip(after_app / std::mem::size_of::<libc::input_event>())
                .all(|e| e.kind != EV_KEY || e.value == 0),
            "new app press after boundary"
        );
        h.no_keyboard();
        assert!(h.requests.is_empty(), "pending single/chord leaked");
        let mut rearming = true;
        assert!(step_neutral_rearming(
            &mut rearming,
            &mut h.device.router,
            Ok((BTreeSet::from([304]), axes())),
            0.2,
            0.5
        ));
        assert!(!step_neutral_rearming(
            &mut rearming,
            &mut h.device.router,
            Ok((BTreeSet::new(), axes())),
            0.2,
            0.5
        ));
    }
}
fn valid_app_fixture() -> (PrivateServer, tempfile::TempDir, PrivateClient, Harness) {
    let server = PrivateServer::new();
    let (keep, sleep) = private_binary();
    new_pane(&server, "alpha", &[sleep.to_str().unwrap(), "120"]);
    let client = PrivateClient::attach(&server, "alpha");
    let mut cfg = mon_config(&sleep);
    cfg.controller.nav.tmux_sockets.push("tmux".into());
    let (_, legacy) = crate::config::tests::fixture_mon();
    let mut h = Harness::configured("terminal", false, cfg.clone(), legacy);
    h.focus.address = Some("0xacceptance".into());
    h.focus.pid = Some(u64::from(client.process.id()));
    h.focus.tmux_client = tmux_client_under(
        Path::new("/proc"),
        client.process.id(),
        &cfg.controller.nav.tmux_sockets,
        unsafe { libc::getuid() },
    );
    assert!(h.focus.tmux_client.is_some());
    verify_app(
        &cfg,
        &mut h.focus,
        "terminal",
        None,
        &mut AppCache::default(),
        unsafe { libc::getuid() },
    );
    assert_eq!(h.focus.app.as_deref(), Some("mon"));
    h.focus.focus_epoch = 1;
    let target = h.focus.address.clone().zip(h.focus.pid).unwrap();
    h.device
        .router
        .set_focus("terminal", Some(target.clone()), false);
    h.device.router.set_app(Some("mon"), Some(&target));
    (server, keep, client, h)
}
fn failed_app_action(key: &str, id: &str) {
    let (_server, _keep, _client, mut h) = valid_app_fixture();
    let runtime = PathBuf::from(std::env::var_os("XDG_RUNTIME_DIR").unwrap());
    let process = h.focus.app_process.clone().unwrap();
    // Positive process/foreground verification, followed by an absent private
    // application IPC endpoint. No fake executable identity or socket authority.
    assert_eq!(
        app_input_process(
            h.device.router.policy(),
            &h.focus,
            "mon",
            Duration::from_secs(1)
        )
        .unwrap(),
        process.pid
    );
    assert!(runtime.is_dir());
    h.action_worker(Default::default(), h.focus.clone());
    let code = if key == "o" { 314 } else { 315 };
    h.event(EV_KEY, code, 1);
    h.advance(50);
    h.event(EV_KEY, code, 0);
    let token = format!("input:app-key mon key:{key}");
    assert_eq!(h.requests, vec![token]);
    let results = h.finish_actions(1);
    trace(id, serde_json::json!({"worker_results":results}));
    h.record(id);
    h.no_keyboard();
    assert!(
        results
            .iter()
            .any(|r| r["kind"] == "app-key" && r["ok"] == false && r["error"].is_string()),
        "actual private IPC failure must be explicit"
    );
    assert!(!results.iter().any(|r| r["ok"] == true));
    assert!(
        results.iter().any(|r| r["error"]
            .as_str()
            .is_some_and(|e| e.contains("No such file") || e.contains("os error 2"))),
        "expected actual missing private IPC endpoint"
    );
}
#[test]
fn frozen_mon_menu_failed_ipc_has_no_fallback() {
    if isolated() {
        return;
    }
    failed_app_action("o", "v3-mon-menu-ipc-failure");
}
#[test]
fn frozen_mon_pause_failed_ipc_has_no_fallback() {
    if isolated() {
        return;
    }
    failed_app_action("p", "v3-mon-pause-ipc-failure");
}
#[test]
fn unsupported_app_control_has_no_pty_or_keyboard_fallback() {
    if isolated() {
        return;
    }
    let (_server, _keep, _client, mut h) = valid_app_fixture();
    h.action_worker(Default::default(), h.focus.clone());
    h.dispatch(Output {
        actions: vec!["input:app-key mon key:ENTER".into()],
        ..Default::default()
    });
    let results = h.finish_actions(1);
    h.record("unsupported-app-control");
    h.no_keyboard();
    assert!(results
        .iter()
        .any(|r| r["kind"] == "app-key" && r["ok"] == false));
}
#[test]
fn frozen_stale_application_and_process_witnesses_reject_late_jobs() {
    if isolated() {
        return;
    }
    for (id, mode) in [
        ("witness-stale-app-reply", 0),
        ("witness-target-reused-with-new-start", 1),
        ("witness-login-switched-before-commit", 2),
    ] {
        let (_server, _keep, _client, mut h) = valid_app_fixture();
        let accepted = h.focus.clone();
        let mut current = accepted.clone();
        let selection: SharedLogin = Default::default();
        match mode {
            0 => {
                current.app = None;
                advance_focus(&accepted, &mut current);
            }
            1 => {
                current.app_process.as_mut().unwrap().start += 1;
                advance_focus(&accepted, &mut current);
            }
            _ => {
                selection.lock().unwrap().generation = 1;
                current.routing_generation = 1;
                advance_focus(&accepted, &mut current);
            }
        }
        h.action_worker(selection, current);
        // Real emit captures the original receipt; the real worker subsequently
        // checks the changed shared scope before process verification or send.
        h.dispatch(Output {
            actions: vec!["input:app-key mon key:o".into()],
            ..Default::default()
        });
        let results = h.finish_actions(1);
        trace(id, serde_json::json!({"worker_results":results}));
        h.record(id);
        h.no_keyboard();
        assert!(results
            .iter()
            .any(|r| r["kind"] == "app-key" && r["ok"] == false));
        assert!(!results.iter().any(|r| r["ok"] == true));
    }
}
#[test]
fn frozen_capture_transaction_failure_and_login_revocation_have_explicit_errors() {
    if isolated() {
        return;
    }
    for id in [
        "ownership-login-revocation",
        "witness-capture-transaction-fails",
    ] {
        let mut h = Harness::new("console", false);
        let selection: SharedLogin = Default::default();
        if id == "ownership-login-revocation" {
            selection.lock().unwrap().generation = 1;
        }
        h.sinks.ownership = Some(
            OwnershipWorker::spawn(
                Arc::new(Mutex::new(h.device.router.policy().clone())),
                selection,
            )
            .unwrap(),
        );
        for (code, value) in [(317, 1), (318, 1), (317, 0), (318, 0)] {
            h.event(EV_KEY, code, value);
        }
        assert_eq!(h.device.router.ownership(), Some(true));
        let worker = h.sinks.ownership.as_ref().unwrap();
        let (seq, mode, result) = worker.results.recv_timeout(Duration::from_secs(2)).unwrap();
        trace(
            id,
            serde_json::json!({"sequence":seq,"last":worker.last,"mode":mode,
            "error":result.as_ref().err(),"scope":"actual worker generation or private IPC failure; not post-success receipt reconciliation"}),
        );
        assert!(result.is_err());
        if id == "witness-capture-transaction-fails" {
            assert_ne!(
                result.as_ref().err().map(String::as_str),
                Some("ownership request expired")
            );
        }
        h.no_keyboard();
        h.record(id);
        let out = h.device.router.ownership_failed();
        h.dispatch(out);
        assert!(!h.device.router.app_owned());
    }
}
#[test]
fn same_pad_late_overlap_both_orders_global_contexts_and_capture() {
    if isolated() {
        return;
    }
    for (context, captured_nav) in [
        ("desktop-2d", false),
        ("desktop-3d", false),
        ("terminal", false),
        ("console", false),
        ("app-capture", true),
    ] {
        for (left, right, token) in [
            (314, 315, "input:window-zoom"),
            (
                317,
                318,
                if context == "app-capture" {
                    "input:window off"
                } else {
                    "input:window on-pinned"
                },
            ),
        ] {
            for reverse in [false, true] {
                let (first, second) = if reverse {
                    (right, left)
                } else {
                    (left, right)
                };
                let mut h = Harness::new(context, captured_nav);
                h.event(EV_KEY, first, 1);
                h.advance(5000);
                assert!(h.requests.is_empty(), "late solo escaped");
                h.event(EV_KEY, first, 2);
                h.event(EV_KEY, second, 1);
                h.advance(1000);
                assert!(h.requests.is_empty(), "commit before releases");
                h.event(EV_KEY, first, 0);
                h.advance(1000);
                assert!(h.requests.is_empty(), "commit with one held");
                h.event(EV_KEY, second, 2);
                h.event(EV_KEY, second, 0);
                let expected = if token == "input:window off" {
                    vec![token, "input:cursor on"]
                } else {
                    vec![token]
                };
                assert_eq!(h.requests, expected);
                if token == "input:window off" {
                    assert!(h.device.router.cursor && !h.device.router.app_owned());
                }
                h.advance(1000);
                assert_eq!(h.requests, expected, "repeated commit");
                h.no_keyboard();
                h.record(&format!("late-overlap-{context}-{left}-{reverse}"));
            }
        }
    }
}
#[test]
fn split_pad_stick_clicks_never_form_a_global_capture_chord() {
    if isolated() {
        return;
    }
    for reverse in [false, true] {
        let mut a = Harness::new("terminal", false);
        let mut b = Harness::new("terminal", false);
        let (left, right) = if reverse { (318, 317) } else { (317, 318) };
        a.event(EV_KEY, left, 1);
        a.advance(5000);
        b.event(EV_KEY, right, 1);
        a.event(EV_KEY, left, 0);
        b.event(EV_KEY, right, 0);
        for h in [&a, &b] {
            assert!(!h.requests.iter().any(|x| x.starts_with("input:window ")));
            h.no_keyboard();
        }
        a.record("split-pad-a");
        b.record("split-pad-b");
    }
}
#[test]
fn strict_on_pinned_capture_rejects_a_to_b_using_actual_native_worker() {
    if isolated() {
        return;
    }
    let server = PrivateServer::new();
    let left = server.receiver("alpha", "left", None);
    let right = server.receiver("alpha", "right", Some(&left));
    server.tmux(&["select-pane", "-t", &left]);
    let client = PrivateClient::attach(&server, "alpha");
    let cfg = capture_config();
    let found = tmux_client_under(
        Path::new("/proc"),
        client.process.id(),
        &cfg.controller.nav.tmux_sockets,
        unsafe { libc::getuid() },
    )
    .unwrap();
    let old = crate::tmux_capture::observe(&found, Duration::from_secs(1)).unwrap();
    assert_eq!(old.pane, left);
    let mut h = Harness::new("console", false);
    h.focus.address = Some(old.address());
    h.focus.pid = Some(u64::from(old.foreground));
    h.focus.native_target = Some(old.clone());
    h.device
        .router
        .set_focus("console", h.focus.address.clone().zip(h.focus.pid), false);
    // Qualify through the actual Router, retain its output until the controlled
    // A->B barrier, then submit via the unchanged real emit dispatcher.
    let mut qualified = Output::default();
    for (code, value) in [(317, 1), (318, 1), (317, 0), (318, 0)] {
        h.now += 1;
        qualified.extend(
            h.device
                .router
                .event(Event::new(EV_KEY, code, value), h.now),
        );
    }
    assert_eq!(qualified.actions, vec!["input:window on-pinned"]);
    server.tmux(&["select-pane", "-t", &right]);
    let new = crate::tmux_capture::observe(&found, Duration::from_secs(1)).unwrap();
    assert_eq!(new.pane, right);
    assert_ne!(old.address(), new.address());
    h.sinks.ownership =
        Some(OwnershipWorker::spawn(Arc::new(Mutex::new(cfg)), Default::default()).unwrap());
    h.dispatch(qualified);
    let (_, _, result) = h
        .sinks
        .ownership
        .as_ref()
        .unwrap()
        .results
        .recv_timeout(Duration::from_secs(2))
        .unwrap();
    trace(
        "strict-on-pinned-a-to-b",
        serde_json::json!({"error":result.as_ref().err(),"current_pane":new.pane}),
    );
    assert!(result.is_err(), "strict capture must not adopt current B");
    assert!(server.bytes("left").is_empty() && server.bytes("right").is_empty());
    let (_, owned) = crate::tmux_capture::current(&found, Duration::from_secs(1)).unwrap();
    assert!(!owned, "failed strict capture granted ownership to B");
    let out = h.device.router.ownership_failed();
    h.dispatch(out);
    // Positive control uses the fresh exact B witness, still via real emit.
    h.focus.address = Some(new.address());
    h.focus.pid = Some(u64::from(new.foreground));
    h.focus.native_target = Some(new);
    h.dispatch(Output {
        actions: vec!["input:window on-pinned".into()],
        ..Default::default()
    });
    let (_, _, result) = h
        .sinks
        .ownership
        .as_ref()
        .unwrap()
        .results
        .recv_timeout(Duration::from_secs(2))
        .unwrap();
    let native = result.unwrap().native.unwrap();
    h.focus.address = Some(native.address());
    h.focus.native_target = Some(native);
    h.dispatch(Output {
        actions: vec!["input:window off".into()],
        ..Default::default()
    });
    h.sinks
        .ownership
        .as_ref()
        .unwrap()
        .results
        .recv_timeout(Duration::from_secs(2))
        .unwrap()
        .2
        .unwrap();
    h.no_keyboard();
    h.record("witness-stale-target-reply");
}

#[test]
fn actual_assigned_session_worker_refuses_failed_app_ipc_without_shell_fallback() {
    if isolated() {
        return;
    }
    let server = PrivateServer::new();
    let pane = server.receiver("alpha", "shell", None);
    let mut h = Harness::new("console", false);
    let route = server.route("alpha", &pane);
    h.device.session = Some(route.clone());
    h.device.session_worker =
        Some(SessionWorker::spawn(route, h.device.router.policy().clone()).unwrap());
    assert!(h
        .device
        .session_worker
        .as_ref()
        .unwrap()
        .submit("key:A".into()));
    PrivateServer::wait(|| server.bytes("shell") == b"a");
    h.dispatch(Output {
        actions: vec![
            "input:app-key mon key:o".into(),
            "input:app-key mon key:p".into(),
        ],
        ..Default::default()
    });
    drained(h.device.session_worker.take().unwrap());
    assert!(
        server.bytes("shell") == b"a",
        "unsupported app action reached shell PTY"
    );
    h.no_keyboard();
    h.record("assigned-route-app-ipc-refusal");
}

include!("mios-controller-late-job-acceptance.rs");

include!("mios-controller-legacy-job-acceptance.rs");

include!("mios-controller-scope-hardening-acceptance.rs");
