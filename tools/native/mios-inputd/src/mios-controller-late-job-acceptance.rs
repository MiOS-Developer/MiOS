// AI-hint: Additional cfg(test) late-job coverage; no hardware or live login operations.
#[test]
fn actual_action_workers_drop_expired_and_aba_jobs_before_any_sink() {
    if isolated() {
        return;
    }
    for mode in ["expired", "focus-aba", "login-revoked"] {
        let (_server, _keep, _client, mut h) = valid_app_fixture();
        let accepted = h.focus.clone();
        let mut current = accepted.clone();
        let selection: SharedLogin = Default::default();
        match mode {
            "focus-aba" => {
                let mut b = accepted.clone();
                b.address = Some("0xother".into());
                advance_focus(&accepted, &mut b);
                advance_focus(&b, &mut current);
                assert!(current.focus_epoch > accepted.focus_epoch);
            }
            "login-revoked" => {
                selection.lock().unwrap().generation = 1;
                current.routing_generation = 1;
            }
            _ => {}
        }
        h.action_worker(selection, current);
        let queued = if mode == "expired" {
            Instant::now()
                - Duration::from_millis(h.device.router.policy().controller.context_stale_ms + 1)
        } else {
            Instant::now()
        };
        let worker = h.sinks.actions.as_ref().unwrap();
        assert!(worker.submit(Job::AppKey {
            app: "mon".into(),
            action: "key:o".into(),
            focus: Box::new(accepted.clone()),
            queued
        }));
        assert!(worker.submit(Job::WindowZoom {
            native: None,
            window: accepted.address.clone().zip(accepted.pid),
            generation: accepted.routing_generation,
            epoch: accepted.focus_epoch,
            queued
        }));
        let results = h.finish_actions();
        trace(
            &format!("late-job-{mode}"),
            serde_json::json!({"worker_results":results}),
        );
        assert_eq!(results.len(), 2);
        assert!(results
            .iter()
            .all(|r| r["ok"] == false && r["error"].is_string()));
        h.no_keyboard();
    }
}
#[test]
fn actual_session_worker_drops_expired_jobs_but_delivers_fresh_positive_control() {
    if isolated() {
        return;
    }
    let server = PrivateServer::new();
    let pane = server.receiver("alpha", "ledger", None);
    let cfg = config();
    let age = Duration::from_millis(cfg.controller.context_stale_ms + 1);
    let worker = SessionWorker::spawn(server.route("alpha", &pane), cfg).unwrap();
    worker
        .jobs
        .as_ref()
        .unwrap()
        .send(SessionJob {
            repeat: 1,
            queued: Instant::now() - age,
            app_control: None,
            action: "key:X".into(),
        })
        .unwrap();
    assert!(worker.submit("key:A".into()));
    drained(worker);
    PrivateServer::wait(|| server.bytes("ledger") == b"a");
    trace(
        "late-session-job",
        serde_json::json!({"pty_bytes":server.bytes("ledger"),
        "expired_x_delivered":false,"fresh_a_delivered":true}),
    );
}
