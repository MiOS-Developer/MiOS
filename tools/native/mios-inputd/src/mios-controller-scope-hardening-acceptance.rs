// AI-hint: Actual-worker single-guard regressions; private C95 and compositor sockets only.
// AI-related: SharedLogin, ActionScope, SessionWorker, ActionWorker, ipc_bounded

fn hardening_result(expected: &str) -> serde_json::Value {
    acceptance_observer::wait_for_results(1);
    let mut results = acceptance_observer::take();
    assert_eq!(results.len(), 1);
    assert_eq!(results[0]["ok"], false);
    assert_eq!(results[0]["error"], expected);
    results.remove(0)
}
fn hardening_listener() -> std::io::Result<std::os::unix::net::UnixListener> {
    let runtime = std::env::var_os("XDG_RUNTIME_DIR")
        .ok_or_else(|| std::io::Error::other("private runtime missing"))?;
    let sig = signature().map_err(std::io::Error::other)?;
    let directory = PathBuf::from(runtime).join("hypr").join(sig);
    std::fs::create_dir_all(&directory)?;
    let listener = std::os::unix::net::UnixListener::bind(directory.join(".socket.sock"))?;
    listener.set_nonblocking(true)?;
    Ok(listener)
}
fn hardening_request(
    stream: &mut std::os::unix::net::UnixStream,
    suffix: &str,
) -> std::io::Result<String> {
    use std::io::Read;
    let until = Instant::now() + Duration::from_secs(2);
    let mut request = Vec::new();
    loop {
        if request.ends_with(suffix.as_bytes()) {
            return String::from_utf8(request).map_err(std::io::Error::other);
        }
        let left = until.saturating_duration_since(Instant::now());
        if left.is_zero() || request.len() >= 8192 {
            return Err(std::io::Error::new(
                std::io::ErrorKind::TimedOut,
                "owned Lua request incomplete or too large",
            ));
        }
        stream.set_read_timeout(Some(left.min(Duration::from_millis(150))))?;
        let mut bytes = [0u8; 512];
        let available = bytes.len().min(8192 - request.len());
        match stream.read(&mut bytes[..available]) {
            Ok(0) => {
                return Err(std::io::Error::new(
                    std::io::ErrorKind::UnexpectedEof,
                    "owned Lua request ended before command suffix",
                ))
            }
            Ok(n) => request.extend_from_slice(&bytes[..n]),
            Err(e)
                if matches!(
                    e.kind(),
                    std::io::ErrorKind::Interrupted
                        | std::io::ErrorKind::WouldBlock
                        | std::io::ErrorKind::TimedOut
                ) => {}
            Err(e) => return Err(e),
        }
    }
}
fn hardening_scope(client: Option<TmuxClient>) -> ContextSnapshot {
    let mut focus = ContextSnapshot::new("desktop-2d");
    focus.address = Some("0xowned-a".into());
    focus.pid = Some(42);
    focus.tmux_client = client;
    focus
}

#[test]
fn actual_login_worker_obeys_authoritative_generation_while_manifest_stays_old() {
    if isolated() {
        return;
    }
    let server = PrivateServer::new();
    let pane = server.receiver("alpha", "ledger", None);
    let client = PrivateClient::attach(&server, "alpha");
    let mut cfg = capture_config();
    cfg.controller.context_stale_ms = 10_000;
    let captured = captured_tmux(&cfg, client.process.id()).unwrap().unwrap();
    let mut target =
        crate::tmux_capture::observe(&captured.client, Duration::from_secs(1)).unwrap();
    target.login_generation = 7;
    let runtime = PathBuf::from(std::env::var_os("XDG_RUNTIME_DIR").unwrap());
    update_active_manifest(
        &runtime,
        &BTreeMap::new(),
        &BTreeMap::new(),
        true,
        cfg.controller.scan_ms,
        ManifestFocus {
            frontend: None,
            stale_ms: 10_000,
            target: Some(&target),
            owned: false,
            remote: true,
            focus: None,
        },
    );
    let selection: SharedLogin = Default::default();
    let witness = crate::tmux_handoff::LoginWitness {
        uid: unsafe { libc::getuid() },
        session_id: "owned-synthetic-login".into(),
        leader: std::process::id(),
        leader_start: proc_start(Path::new("/proc"), std::process::id())
            .unwrap()
            .1,
        client: target.client(),
        client_start: target.client_start,
    };
    {
        let mut state = selection.lock().unwrap();
        state.generation = 7;
        state.remote = Some(AuthorizedLogin {
            generation: 7,
            witness,
        });
    }
    let worker = SessionWorker::login(
        target.clone(),
        Path::new("/dev/input/by-id/mios-test-inert-pad"),
        cfg,
        selection.clone(),
    )
    .unwrap();
    assert!(worker.submit("key:A".into()));
    acceptance_observer::wait_for_results(1);
    assert_eq!(acceptance_observer::take()[0]["ok"], true);
    PrivateServer::wait_named("fresh authenticated login key", || {
        server.bytes("ledger") == b"a"
    });

    let (entered, ready) = std::sync::mpsc::channel();
    let (release, go) = std::sync::mpsc::channel();
    let go = Arc::new(Mutex::new(go));
    acceptance_observer::set_effect_hook(Some(Arc::new(move |phase| {
        if phase == "session-before-job" {
            entered.send(()).unwrap();
            go.lock().unwrap().recv().unwrap();
        }
    })));
    assert!(worker.submit("key:B".into()));
    ready.recv_timeout(Duration::from_secs(2)).unwrap();
    selection.lock().unwrap().detach(7).unwrap(); // Only authoritative login changes.
    assert!(
        crate::tmux_capture::live_target(&target, false),
        "manifest must still authorize old target for this test"
    );
    release.send(()).unwrap();
    let rejected = hardening_result("session login generation changed");
    acceptance_observer::set_effect_hook(None);
    drop(worker);
    assert_eq!(
        server.bytes("ledger"),
        b"a",
        "revoked queued B reached the old login"
    );
    assert_eq!(pane, target.pane);
    trace("authoritative-login-only", rejected);
}

#[test]
fn actual_legacy_worker_checks_client_swap_without_epoch_or_generation_change() {
    if isolated() {
        return;
    }
    let server = PrivateServer::new();
    server.receiver("alpha", "alpha", None);
    server.receiver("beta", "beta", None);
    let client = PrivateClient::attach_reusable(&server, "alpha", true);
    let mut cfg = capture_config();
    cfg.controller.context_stale_ms = 10_000;
    let accepted = captured_tmux(&cfg, client.process.id()).unwrap().unwrap();
    let policy = Arc::new(Mutex::new(cfg.clone()));
    let selection: SharedLogin = Default::default();
    let focus = Arc::new(Mutex::new(hardening_scope(Some(accepted.client.clone()))));
    let scope = ActionScope::new(&focus.lock().unwrap(), 10_000);
    let barrier = policy.lock().unwrap();
    let worker = ActionWorker::spawn(policy.clone(), selection.clone(), focus.clone()).unwrap();
    assert!(worker.submit(Job::Tmux {
        verb: "session-next".into(),
        client: accepted.client.clone(),
        console: true,
        scope
    }));
    server.tmux(&["detach-client", "-t", &accepted.client.tty]);
    let mut replacement = None;
    PrivateServer::wait_named("same-pty replacement for scope guard", || {
        replacement = captured_tmux(&cfg, client.process.id())
            .ok()
            .flatten()
            .filter(|next| {
                next.client.pid != accepted.client.pid && next.client.tty == accepted.client.tty
            });
        replacement.is_some()
    });
    let replacement = replacement.unwrap();
    focus.lock().unwrap().tmux_client = Some(replacement.client.clone()); // Only this guard changes.
    assert_eq!(selection.lock().unwrap().generation, 0);
    assert_eq!(focus.lock().unwrap().focus_epoch, 0);
    drop(barrier);
    let rejected = hardening_result("queued tmux client focus changed");
    drop(worker);
    assert_eq!(
        captured_tmux(&cfg, client.process.id())
            .unwrap()
            .unwrap()
            .session,
        accepted.session
    );
    trace("legacy-client-swap-only", rejected);
}

#[test]
fn actual_page_up_worker_rechecks_login_between_query_and_effect() {
    if isolated() {
        return;
    }
    let server = PrivateServer::new();
    let pane = server.receiver("alpha", "ledger", None);
    let client = PrivateClient::attach(&server, "alpha");
    let mut cfg = capture_config();
    cfg.controller.context_stale_ms = 10_000;
    let captured = captured_tmux(&cfg, client.process.id()).unwrap().unwrap();
    let selection: SharedLogin = Default::default();
    let focus = Arc::new(Mutex::new(hardening_scope(Some(captured.client.clone()))));
    let scope = ActionScope::new(&focus.lock().unwrap(), 10_000);
    let changed = selection.clone();
    let calls = Arc::new(std::sync::atomic::AtomicUsize::new(0));
    let count = calls.clone();
    acceptance_observer::set_effect_hook(Some(Arc::new(move |phase| {
        if phase == "tmux-after-step" && count.fetch_add(1, Ordering::SeqCst) == 0 {
            changed.lock().unwrap().generation = 1;
        }
    })));
    let worker = ActionWorker::spawn(Arc::new(Mutex::new(cfg)), selection, focus).unwrap();
    assert!(worker.submit(Job::Tmux {
        verb: "page-up".into(),
        client: captured.client,
        console: true,
        scope
    }));
    let rejected = hardening_result("input login generation changed");
    acceptance_observer::set_effect_hook(None);
    drop(worker);
    assert_eq!(
        calls.load(Ordering::SeqCst),
        1,
        "first nonmutating query must run, copy-mode effect must not"
    );
    assert_eq!(
        server
            .tmux(&["display-message", "-p", "-t", &pane, "#{pane_in_mode}"])
            .trim(),
        "0"
    );
    trace("page-up-revoke-between-steps", rejected);
}

#[test]
fn actual_window_lua_and_edge_focus_refuse_live_compositor_window_or_pid_change() {
    if isolated() {
        return;
    }
    use std::io::Write;
    let server = PrivateServer::new();
    server.receiver("alpha", "ledger", None);
    let client = PrivateClient::attach(&server, "alpha");
    let mut cfg = capture_config();
    cfg.controller.context_stale_ms = 10_000;
    let captured = captured_tmux(&cfg, client.process.id()).unwrap().unwrap();
    let listener = hardening_listener().unwrap();
    for edge in [false, true] {
        for (address, pid, expected) in [
            ("0xowned-b", 42, 0),
            ("0xowned-a", 43, 0),
            ("0xowned-a", 42, 1),
        ] {
            let focus = Arc::new(Mutex::new(hardening_scope(Some(captured.client.clone()))));
            let scope = ActionScope::new(&focus.lock().unwrap(), 10_000);
            let worker =
                ActionWorker::spawn(Arc::new(Mutex::new(cfg.clone())), Default::default(), focus)
                    .unwrap();
            let raw = command_for(if edge {
                "hypr:movefocus left"
            } else {
                "hypr:killactive"
            })
            .unwrap()
            .remove(2);
            let job = if edge {
                Job::Tmux {
                    verb: "focus left".into(),
                    client: captured.client.clone(),
                    console: false,
                    scope,
                }
            } else {
                Job::Lua {
                    action: "hypr:killactive".into(),
                    lua: raw.clone(),
                    scope,
                }
            };
            assert!(worker.submit(job));
            let mut incoming = None;
            PrivateServer::wait_named("owned compositor action", || {
                incoming = listener.accept().ok();
                incoming.is_some()
            });
            let (mut stream, _) = incoming.unwrap();
            let request = hardening_request(&mut stream, &raw).unwrap();
            let lua = request.strip_prefix("/eval ").unwrap();
            let prelude=format!("local effects=0;hl={{get_active_window=function() return {{address={:?},pid={pid}}} end,dsp={{window={{close=function() return 'close' end}},focus=function() return 'focus' end}},dispatch=function() effects=effects+1 end}};local ok=pcall(function() {lua} end);print((ok and 'OK:' or 'REFUSED:')..effects)",address);
            let mut child = Command::new("/usr/bin/lua")
                .arg("-")
                .stdin(Stdio::piped())
                .stdout(Stdio::piped())
                .spawn()
                .unwrap();
            child
                .stdin
                .take()
                .unwrap()
                .write_all(prelude.as_bytes())
                .unwrap();
            let result = child.wait_with_output().unwrap();
            assert!(result.status.success());
            let actual = String::from_utf8(result.stdout).unwrap();
            assert_eq!(
                actual.trim(),
                if expected == 1 { "OK:1" } else { "REFUSED:0" }
            );
            stream
                .write_all(if expected == 1 {
                    b"ok"
                } else {
                    b"focus refused"
                })
                .unwrap();
            drop(stream);
            acceptance_observer::wait_for_results(1);
            let results = acceptance_observer::take();
            assert_eq!(results.len(), 1);
            assert_eq!(results[0]["ok"], expected == 1);
            if expected == 0 {
                assert_eq!(
                    results[0]["error"],
                    "queued compositor action was not acknowledged"
                );
            }
            drop(worker);
            trace(
                "server-window-pid-boundary",
                serde_json::json!({"edge":edge,"effects":expected,"worker":results}),
            );
        }
    }
}

#[test]
fn actual_worker_cancel_after_connect_prevents_lua_and_zoom_request_bytes() {
    if isolated() {
        return;
    }
    use std::io::Read;
    let listener = hardening_listener().unwrap();
    for zoom in [false, true] {
        let mut cfg = capture_config();
        cfg.controller.context_stale_ms = 10_000;
        let focus = Arc::new(Mutex::new(hardening_scope(None)));
        let scope = ActionScope::new(&focus.lock().unwrap(), cfg.controller.context_stale_ms);
        let (entered, ready) = std::sync::mpsc::channel();
        let (release, go) = std::sync::mpsc::channel();
        let go = Arc::new(Mutex::new(go));
        acceptance_observer::set_effect_hook(Some(Arc::new(move |phase| {
            if phase == "compositor-before-write" {
                entered.send(()).unwrap();
                go.lock().unwrap().recv().unwrap();
            }
        })));
        let mut worker =
            ActionWorker::spawn(Arc::new(Mutex::new(cfg)), Default::default(), focus).unwrap();
        assert!(worker.submit(if zoom {
            Job::WindowZoom {
                native: None,
                window: Some(("0xowned-a".into(), 42)),
                generation: 0,
                epoch: 0,
                queued: Instant::now(),
            }
        } else {
            Job::Lua {
                action: "hypr:killactive".into(),
                lua: command_for("hypr:killactive").unwrap().remove(2),
                scope,
            }
        }));
        ready.recv_timeout(Duration::from_secs(2)).unwrap();
        let (mut stream, _) = listener.accept().unwrap();
        stream
            .set_read_timeout(Some(Duration::from_secs(1)))
            .unwrap();
        worker.cancel();
        release.send(()).unwrap();
        let result = hardening_result(if zoom {
            "zoom login generation or request expired"
        } else {
            "input action worker stopped"
        });
        acceptance_observer::set_effect_hook(None);
        drop(worker);
        let mut byte = [0u8; 1];
        assert_eq!(
            stream.read(&mut byte).unwrap(),
            0,
            "cancelled worker wrote compositor request bytes"
        );
        trace(
            "worker-cancel-before-write",
            serde_json::json!({"zoom":zoom,"result":result}),
        );
    }
}

#[test]
fn actual_worker_reload_cannot_extend_the_enqueued_scope_deadline() {
    if isolated() {
        return;
    }
    let listener = hardening_listener().unwrap();
    for (accepted, reloaded) in [(50, 10_000), (10_000, 50)] {
        let mut cfg = capture_config();
        cfg.controller.context_stale_ms = accepted;
        let policy = Arc::new(Mutex::new(cfg));
        let focus = Arc::new(Mutex::new(hardening_scope(None)));
        let mut scope = ActionScope::new(&focus.lock().unwrap(), accepted);
        scope.queued -= Duration::from_millis(60);
        let mut barrier = policy.lock().unwrap();
        let worker = ActionWorker::spawn(policy.clone(), Default::default(), focus).unwrap();
        assert!(worker.submit(Job::Lua {
            action: "hypr:killactive".into(),
            lua: command_for("hypr:killactive").unwrap().remove(2),
            scope
        }));
        barrier.controller.context_stale_ms = reloaded;
        drop(barrier);
        let rejected = hardening_result("queued input action expired");
        drop(worker);
        assert!(matches!(listener.accept(),Err(e) if e.kind()==std::io::ErrorKind::WouldBlock));
        trace("enqueue-deadline-reload", rejected);
    }
}

#[test]
fn actual_login_worker_rechecks_generation_after_inspect_before_key_send() {
    if isolated() {
        return;
    }
    let server = PrivateServer::new();
    let pane = server.receiver("alpha", "ledger", None);
    let client = PrivateClient::attach(&server, "alpha");
    let mut cfg = capture_config();
    cfg.controller.context_stale_ms = 10_000;
    let captured = captured_tmux(&cfg, client.process.id()).unwrap().unwrap();
    let mut target =
        crate::tmux_capture::observe(&captured.client, Duration::from_secs(1)).unwrap();
    target.login_generation = 7;
    let runtime = PathBuf::from(std::env::var_os("XDG_RUNTIME_DIR").unwrap());
    update_active_manifest(
        &runtime,
        &BTreeMap::new(),
        &BTreeMap::new(),
        true,
        cfg.controller.scan_ms,
        ManifestFocus {
            frontend: None,
            stale_ms: 10_000,
            target: Some(&target),
            owned: false,
            remote: true,
            focus: None,
        },
    );
    let selection: SharedLogin = Default::default();
    let witness = crate::tmux_handoff::LoginWitness {
        uid: unsafe { libc::getuid() },
        session_id: "owned-synthetic-login".into(),
        leader: std::process::id(),
        leader_start: proc_start(Path::new("/proc"), std::process::id())
            .unwrap()
            .1,
        client: target.client(),
        client_start: target.client_start,
    };
    {
        let mut state = selection.lock().unwrap();
        state.generation = 7;
        state.remote = Some(AuthorizedLogin {
            generation: 7,
            witness,
        });
    }
    let worker = SessionWorker::login(
        target.clone(),
        Path::new("/dev/input/by-id/mios-test-inert-pad"),
        cfg,
        selection.clone(),
    )
    .unwrap();
    assert!(worker.submit("key:A".into()));
    acceptance_observer::wait_for_results(1);
    assert_eq!(acceptance_observer::take()[0]["ok"], true);
    PrivateServer::wait_named("fresh authenticated login key", || {
        server.bytes("ledger") == b"a"
    });

    let calls = Arc::new(std::sync::atomic::AtomicUsize::new(0));
    let count = calls.clone();
    let authority = selection.clone();
    acceptance_observer::set_effect_hook(Some(Arc::new(move |phase| {
        if phase == "session-after-step" && count.fetch_add(1, Ordering::SeqCst) == 0 {
            authority.lock().unwrap().detach(7).unwrap();
        }
    })));
    assert!(worker.submit("key:B".into()));
    let rejected = hardening_result("session login generation changed");
    acceptance_observer::set_effect_hook(None);
    drop(worker);
    assert_eq!(
        calls.load(Ordering::SeqCst),
        1,
        "readonly inspection ran, key-send subprocess must not"
    );
    assert!(
        crate::tmux_capture::live_target(&target, false),
        "manifest remains valid while authority alone revokes"
    );
    assert_eq!(
        server.bytes("ledger"),
        b"a",
        "per-effect guard did not block queued key B"
    );
    assert_eq!(pane, target.pane);
    trace("login-revoked-after-inspect-before-key", rejected);
}

#[test]
fn actual_action_worker_drop_cancels_received_and_queued_lua_without_dispatch() {
    if isolated() {
        return;
    }
    let listener = hardening_listener().unwrap();
    let mut cfg = capture_config();
    cfg.controller.context_stale_ms = 10_000;
    let focus = Arc::new(Mutex::new(hardening_scope(None)));
    let scope = ActionScope::new(&focus.lock().unwrap(), 10_000);
    let (entered, ready) = std::sync::mpsc::channel();
    let (release, go) = std::sync::mpsc::channel();
    let go = Arc::new(Mutex::new(go));
    let first = std::sync::atomic::AtomicBool::new(true);
    acceptance_observer::set_effect_hook(Some(Arc::new(move |phase| {
        if phase == "action-before-policy" && first.swap(false, Ordering::SeqCst) {
            entered.send(()).unwrap();
            go.lock().unwrap().recv().unwrap();
        }
    })));
    let worker = ActionWorker::spawn(Arc::new(Mutex::new(cfg)), Default::default(), focus).unwrap();
    let stop = worker.stop.clone();
    let lua = command_for("hypr:killactive").unwrap().remove(2);
    assert!(worker.submit(Job::Lua {
        action: "hypr:killactive".into(),
        lua: lua.clone(),
        scope: scope.clone()
    }));
    ready.recv_timeout(Duration::from_secs(2)).unwrap();
    assert!(worker.submit(Job::Lua {
        action: "hypr:killactive".into(),
        lua,
        scope
    }));
    let (dropping, started) = std::sync::mpsc::channel();
    let (done, finished) = std::sync::mpsc::channel();
    let dropper = std::thread::spawn(move || {
        dropping.send(()).unwrap();
        drop(worker);
        let _ = done.send(());
    });
    started.recv_timeout(Duration::from_secs(2)).unwrap();
    let until = Instant::now() + Duration::from_secs(2);
    while !stop.load(Ordering::Acquire) && Instant::now() < until {
        std::thread::yield_now();
    }
    let stopped_before_release = stop.load(Ordering::Acquire);
    let joined_before_release = finished.try_recv().is_ok();
    // Always unblock the owned worker before asserting, including a bad Drop
    // implementation, so a failed negative cannot leave its barrier parked.
    release.send(()).unwrap();
    let completed = if joined_before_release {
        true
    } else {
        finished.recv_timeout(Duration::from_secs(2)).is_ok()
    };
    acceptance_observer::set_effect_hook(None);
    if completed {
        dropper.join().unwrap();
    } else {
        drop(dropper);
    }
    assert!(
        completed,
        "owned worker Drop failed to complete after barrier release"
    );
    assert!(
        stopped_before_release,
        "Drop must set cancellation before waiting for an executing worker"
    );
    assert!(
        !joined_before_release,
        "Drop must still own worker completion while its barrier is held"
    );
    assert!(
        acceptance_observer::take().is_empty(),
        "Drop drained queued jobs instead of cancelling"
    );
    assert!(
        matches!(listener.accept(),Err(e) if e.kind()==std::io::ErrorKind::WouldBlock),
        "Drop dispatched a queued compositor request"
    );
    trace(
        "actual-drop-stops-before-barrier-release",
        serde_json::json!({"stop_observed":stopped_before_release,"completed":completed,"queued_lua_dispatches":0}),
    );
}

#[test]
fn actual_graphical_discovery_enriches_and_pins_the_c95_client_instance() {
    if isolated() {
        return;
    }
    let server = PrivateServer::new();
    server.receiver("alpha", "ledger", None);
    let client = PrivateClient::attach(&server, "alpha");
    let cfg = capture_config();
    let uid = unsafe { libc::getuid() };
    let raw = tmux_client_under(
        Path::new("/proc"),
        client.process.id(),
        &cfg.controller.nav.tmux_sockets,
        uid,
    )
    .unwrap();
    assert_eq!(
        raw.instance, 0,
        "/proc discovery cannot invent server client instances"
    );
    let witnessed =
        graphical_tmux_client(&cfg, client.process.id(), uid, Duration::from_secs(2)).unwrap();
    assert!(witnessed.instance > 0 && witnessed.start > 0 && witnessed.socket_ino > 0);
    let actual = crate::tmux_capture::observe(&raw, Duration::from_secs(1)).unwrap();
    assert_eq!(witnessed, actual.client());
    let mut wrong = witnessed.clone();
    wrong.instance += 1;
    assert_eq!(
        crate::tmux_capture::observe(&wrong, Duration::from_secs(1)).unwrap_err(),
        "tmux client instance changed"
    );
    assert!(graphical_tmux_client(&cfg, client.process.id(), uid, Duration::ZERO).is_none());
    trace(
        "graphical-discovery-pins-c95-instance",
        serde_json::json!({"raw_instance":raw.instance,"witnessed_instance":witnessed.instance,"socket_ino":witnessed.socket_ino}),
    );
}
