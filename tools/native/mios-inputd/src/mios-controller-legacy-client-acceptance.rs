// AI-hint: Private actual-worker client identity and remaining IPC deadline regressions; no hardware or live session access.
// AI-related: runtime::TmuxClient, runtime::ActionWorker, tmux_capture::observe

#[test]
fn actual_legacy_tmux_worker_rejects_reused_tty_start_and_instance_before_cached_focus_refresh() {
    if isolated() {
        return;
    }
    for mode in [
        "tty-reused",
        "process-start",
        "client-instance",
        "socket-inode",
    ] {
        let server = PrivateServer::new();
        server.receiver("alpha", "alpha", None);
        server.receiver("beta", "beta", None);
        let client = PrivateClient::attach_reusable(&server, "alpha", mode == "tty-reused");
        let mut cfg = capture_config();
        cfg.controller.context_stale_ms = 10_000;
        let raw = captured_tmux(&cfg, client.process.id()).unwrap().unwrap();
        let accepted = crate::tmux_capture::observe(&raw.client, Duration::from_secs(1)).unwrap();
        assert!(accepted.client_start > 0 && accepted.client_instance > 0);
        let mut stale = accepted.client();
        match mode {
            "process-start" => stale.start += 1,
            "client-instance" => stale.instance += 1,
            "socket-inode" => stale.socket_ino += 1,
            _ => {}
        }
        let queued_client = stale.clone();
        let policy = Arc::new(Mutex::new(cfg.clone()));
        let selection: SharedLogin = Default::default();
        let mut initial = ContextSnapshot::new("console");
        initial.tmux_client = Some(stale.clone());
        let focus = Arc::new(Mutex::new(initial));
        let scope = ActionScope::new(&focus.lock().unwrap(), cfg.controller.context_stale_ms);
        let barrier = policy.lock().unwrap();
        let worker = ActionWorker::spawn(policy.clone(), selection.clone(), focus.clone()).unwrap();
        assert!(worker.submit(Job::Tmux {
            verb: "session-next".into(),
            client: stale,
            console: true,
            scope,
        }));
        let mut replacement = None;
        if mode == "tty-reused" {
            // The shell and owned master stay alive; exactly the next tmux
            // child attaches to the SAME slave. The published cache is kept old.
            server.tmux(&["detach-client", "-t", &accepted.tty]);
            PrivateServer::wait_named("replacement client on the same private tty", || {
                replacement = captured_tmux(&cfg, client.process.id())
                    .ok()
                    .flatten()
                    .filter(|next| {
                        next.client.pid != accepted.client && next.client.tty == accepted.tty
                    });
                replacement.is_some()
            });
        }
        drop(barrier);
        acceptance_observer::wait_for_results(1);
        drop(worker);
        let current = replacement
            .unwrap_or_else(|| captured_tmux(&cfg, client.process.id()).unwrap().unwrap());
        let observed =
            crate::tmux_capture::observe(&current.client, Duration::from_secs(1)).unwrap();
        assert_eq!(
            observed.session, accepted.session,
            "cached legacy action reached the replacement/wrong client: {mode}"
        );
        let rejected = acceptance_observer::take();
        assert_eq!(rejected.len(), 1);
        assert_eq!(rejected[0]["ok"], false);
        let error = rejected[0]["error"].as_str().unwrap();
        let expected_error = match mode {
            "process-start" => "tmux client process start changed",
            "client-instance" => "tmux client instance changed",
            "socket-inode" => "tmux client socket identity changed",
            _ => "",
        };
        if !expected_error.is_empty() {
            assert_eq!(error, expected_error);
        } else {
            assert!(
                !error.contains("expired"),
                "identity refusal must not be masked by TTL: {error}"
            );
        }
        assert_eq!(
            focus.lock().unwrap().tmux_client.as_ref(),
            Some(&queued_client),
            "the stale focus cache must remain unchanged until the negative assertion"
        );

        let fresh_client = observed.client();
        let mut refreshed = focus.lock().unwrap().clone();
        refreshed.tmux_client = Some(fresh_client.clone());
        refreshed.sampled = Instant::now();
        let previous = focus.lock().unwrap().clone();
        advance_focus(&previous, &mut refreshed);
        *focus.lock().unwrap() = refreshed;
        let scope = ActionScope::new(&focus.lock().unwrap(), cfg.controller.context_stale_ms);
        let worker = ActionWorker::spawn(policy, selection, focus).unwrap();
        assert!(worker.submit(Job::Tmux {
            verb: "session-next".into(),
            client: fresh_client,
            console: true,
            scope,
        }));
        acceptance_observer::wait_for_results(1);
        drop(worker);
        let delivered = acceptance_observer::take();
        assert_eq!(delivered.len(), 1);
        assert_eq!(delivered[0]["ok"], true);
        let after = crate::tmux_capture::observe(&current.client, Duration::from_secs(1)).unwrap();
        assert_ne!(
            after.session, accepted.session,
            "fresh positive did not switch: {mode}"
        );
        trace(
            &format!("legacy-client-pin-{mode}"),
            serde_json::json!({
                "old_pid":accepted.client,"actual_pid":observed.client,"same_tty":accepted.tty==observed.tty,
                "rejected":rejected,"fresh":delivered,
            }),
        );
    }
}

#[test]
fn actual_legacy_lua_worker_uses_remaining_queue_budget_for_reply() {
    if isolated() {
        return;
    }
    use std::io::Write;
    let runtime = PathBuf::from(std::env::var_os("XDG_RUNTIME_DIR").unwrap());
    let directory = runtime.join("hypr").join(signature().unwrap());
    std::fs::create_dir_all(&directory).unwrap();
    let listener = std::os::unix::net::UnixListener::bind(directory.join(".socket.sock")).unwrap();
    listener.set_nonblocking(true).unwrap();
    let mut cfg = capture_config();
    cfg.controller.context_stale_ms = 500;
    let policy = Arc::new(Mutex::new(cfg.clone()));
    let selection: SharedLogin = Default::default();
    let focus = Arc::new(Mutex::new({
        let mut focus = ContextSnapshot::new("desktop-2d");
        focus.address = Some("0xowned".into());
        focus.pid = Some(42);
        focus
    }));
    let mut scope = ActionScope::new(&focus.lock().unwrap(), cfg.controller.context_stale_ms);
    scope.queued -= Duration::from_millis(400);
    let worker = ActionWorker::spawn(policy, selection, focus).unwrap();
    assert!(worker.submit(Job::Lua {
        action: "key:ENTER".into(),
        lua: "return 'owned-deadline'".into(),
        scope,
    }));
    let mut incoming = None;
    PrivateServer::wait_named("owned deadline compositor connection", || {
        incoming = listener.accept().ok();
        incoming.is_some()
    });
    let (mut stream, _) = incoming.unwrap();
    stream
        .set_read_timeout(Some(Duration::from_millis(500)))
        .unwrap();
    let request = hardening_request(&mut stream, "return 'owned-deadline'").unwrap();
    assert!(request.contains("queued compositor focus changed"));
    let late = std::thread::spawn(move || {
        // The old independent150ms read deadline accepts this response;
        // only100ms of the actual queued scope remained at submission.
        std::thread::sleep(Duration::from_millis(120));
        let _ = stream.write_all(b"ok");
    });
    acceptance_observer::wait_for_results(1);
    drop(worker);
    late.join().unwrap();
    let results = acceptance_observer::take();
    assert_eq!(results.len(), 1);
    assert_eq!(
        results[0]["ok"], false,
        "late reply escaped the remaining queue budget"
    );
    assert!(results[0]["error"].is_string());
    trace(
        "legacy-lua-remaining-budget",
        serde_json::json!({"results":results}),
    );
}
