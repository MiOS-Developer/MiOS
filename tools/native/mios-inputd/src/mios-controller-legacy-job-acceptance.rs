// AI-hint: Actual worker revocation and positive controls on private C95 clients and owned compositor sockets.
// AI-related: runtime::ActionScope, runtime::ActionWorker, mios-controller-runtime-acceptance.rs

fn revoke_legacy_scope(mode: &str, selection: &SharedLogin, focus: &Arc<Mutex<ContextSnapshot>>) {
    let mut current = focus.lock().unwrap();
    match mode {
        "login-revoked" => {
            // Release focus before taking selection: retain the production lock order.
            drop(current);
            selection.lock().unwrap().generation = 1;
            current = focus.lock().unwrap();
        }
        "focus-aba" => {
            let original = current.clone();
            let mut other = original.clone();
            other.address = Some("0xowned-other".into());
            advance_focus(&original, &mut other);
            advance_focus(&other, &mut current);
            assert!(current.focus_epoch > original.focus_epoch);
        }
        "expired" => {}
        _ => panic!("unknown revocation case"),
    }
    current.sampled = Instant::now();
}

fn legacy_scope(mode: &str, focus: &ContextSnapshot, cfg: &GlobalConfig) -> ActionScope {
    let mut scope = ActionScope::new(focus, cfg.controller.context_stale_ms);
    if mode == "expired" {
        scope.queued -= Duration::from_millis(cfg.controller.context_stale_ms + 1);
    }
    scope
}

#[test]
fn actual_legacy_tmux_worker_rejects_revoked_expired_and_aba_jobs() {
    if isolated() {
        return;
    }
    for mode in ["login-revoked", "focus-aba", "expired"] {
        let server = PrivateServer::new();
        server.receiver("alpha", "alpha", None);
        server.receiver("beta", "beta", None);
        let client = PrivateClient::attach(&server, "alpha");
        let cfg = capture_config();
        let accepted = captured_tmux(&cfg, client.process.id()).unwrap().unwrap();
        run_tmux(&cfg, &accepted.client, "session-next", true).unwrap();
        assert_ne!(
            captured_tmux(&cfg, client.process.id())
                .unwrap()
                .unwrap()
                .session,
            accepted.session
        );
        run_tmux(&cfg, &accepted.client, "session-prev", true).unwrap();
        let policy = Arc::new(Mutex::new(cfg.clone()));
        let selection: SharedLogin = Default::default();
        let mut initial = ContextSnapshot::new("console");
        initial.tmux_client = Some(accepted.client.clone());
        let focus = Arc::new(Mutex::new(initial));
        let scope = legacy_scope(mode, &focus.lock().unwrap(), &cfg);
        let barrier = policy.lock().unwrap();
        let worker = ActionWorker::spawn(policy.clone(), selection.clone(), focus.clone()).unwrap();
        assert!(worker.submit(Job::Tmux {
            verb: "session-next".into(),
            client: accepted.client.clone(),
            console: true,
            scope
        }));
        revoke_legacy_scope(mode, &selection, &focus);
        drop(barrier);
        acceptance_observer::wait_for_results(1);
        drop(worker);
        let observed = captured_tmux(&cfg, client.process.id()).unwrap().unwrap();
        assert_eq!(
            observed.session, accepted.session,
            "revoked queued legacy job changed the old login client: {mode}"
        );
        let rejected = acceptance_observer::take();
        assert_eq!(rejected.len(), 1);
        assert_eq!(rejected[0]["ok"], false);
        assert_eq!(
            rejected[0]["error"],
            match mode {
                "login-revoked" => "input login generation changed",
                "focus-aba" => "input focus visit changed or expired",
                _ => "queued input action expired",
            }
        );

        {
            let generation = selection.lock().unwrap().generation;
            let mut fresh = focus.lock().unwrap();
            fresh.sampled = Instant::now();
            fresh.routing_generation = generation;
        }
        let fresh = ActionScope::new(&focus.lock().unwrap(), cfg.controller.context_stale_ms);
        let worker = ActionWorker::spawn(policy, selection, focus).unwrap();
        assert!(worker.submit(Job::Tmux {
            verb: "session-next".into(),
            client: accepted.client,
            console: true,
            scope: fresh
        }));
        acceptance_observer::wait_for_results(1);
        drop(worker);
        assert_ne!(
            captured_tmux(&cfg, client.process.id())
                .unwrap()
                .unwrap()
                .session,
            accepted.session,
            "fresh queued action must still switch the private client"
        );
        let delivered = acceptance_observer::take();
        assert_eq!(delivered.len(), 1);
        assert_eq!(delivered[0]["ok"], true);
        trace(
            &format!("legacy-tmux-{mode}"),
            serde_json::json!({"rejected":rejected,"fresh":delivered}),
        );
    }
}

#[test]
fn actual_legacy_lua_worker_rejects_revoked_expired_and_aba_jobs() {
    if isolated() {
        return;
    }
    use std::io::Write;
    let runtime = PathBuf::from(std::env::var_os("XDG_RUNTIME_DIR").unwrap());
    let directory = runtime.join("hypr").join(signature().unwrap());
    std::fs::create_dir_all(&directory).unwrap();
    let listener = std::os::unix::net::UnixListener::bind(directory.join(".socket.sock")).unwrap();
    listener.set_nonblocking(true).unwrap();
    for mode in ["login-revoked", "focus-aba", "expired"] {
        let cfg = capture_config();
        let policy = Arc::new(Mutex::new(cfg.clone()));
        let selection: SharedLogin = Default::default();
        let focus = Arc::new(Mutex::new({
            let mut focus = ContextSnapshot::new("desktop-2d");
            focus.address = Some("0xowned".into());
            focus.pid = Some(42);
            focus
        }));
        let scope = legacy_scope(mode, &focus.lock().unwrap(), &cfg);
        let barrier = policy.lock().unwrap();
        let worker = ActionWorker::spawn(policy.clone(), selection.clone(), focus.clone()).unwrap();
        assert!(worker.submit(Job::Lua {
            action: "key:ENTER".into(),
            lua: "return 'owned-stale'".into(),
            scope
        }));
        revoke_legacy_scope(mode, &selection, &focus);
        drop(barrier);
        acceptance_observer::wait_for_results(1);
        drop(worker);
        assert!(
            matches!(listener.accept(), Err(error) if error.kind()==std::io::ErrorKind::WouldBlock),
            "revoked queued Lua job opened the compositor connection: {mode}"
        );
        let rejected = acceptance_observer::take();
        assert_eq!(rejected.len(), 1);
        assert_eq!(rejected[0]["ok"], false);
        assert_eq!(
            rejected[0]["error"],
            match mode {
                "login-revoked" => "input login generation changed",
                "focus-aba" => "input focus visit changed or expired",
                _ => "queued input action expired",
            }
        );

        {
            let generation = selection.lock().unwrap().generation;
            let mut fresh = focus.lock().unwrap();
            fresh.sampled = Instant::now();
            fresh.routing_generation = generation;
        }
        let fresh = ActionScope::new(&focus.lock().unwrap(), cfg.controller.context_stale_ms);
        let worker = ActionWorker::spawn(policy, selection, focus).unwrap();
        assert!(worker.submit(Job::Lua {
            action: "key:ENTER".into(),
            lua: "return 'owned-fresh'".into(),
            scope: fresh
        }));
        let mut incoming = None;
        PrivateServer::wait_named("fresh owned compositor connection", || {
            incoming = listener.accept().ok();
            incoming.is_some()
        });
        let (mut stream, _) = incoming.unwrap();
        stream
            .set_read_timeout(Some(Duration::from_millis(500)))
            .unwrap();
        let request = hardening_request(&mut stream, "return 'owned-fresh'").unwrap();
        assert!(request.contains("queued compositor focus changed"));
        stream.write_all(b"ok").unwrap();
        drop(stream);
        acceptance_observer::wait_for_results(1);
        drop(worker);
        let delivered = acceptance_observer::take();
        assert_eq!(delivered.len(), 1);
        assert_eq!(delivered[0]["ok"], true);
        trace(
            &format!("legacy-lua-{mode}"),
            serde_json::json!({"rejected":rejected,"fresh":delivered}),
        );
    }
}
