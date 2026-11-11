// AI-hint: Real native CLI and local HTTP controls; no inference or agent worker is launched.
use std::{
    fs,
    io::{Read, Write},
    net::TcpListener,
    process::{Command, Stdio},
    thread,
    time::Duration,
};

fn fixture() -> tempfile::TempDir {
    let root = tempfile::tempdir().unwrap();
    fs::create_dir_all(root.path().join("usr/share/mios")).unwrap();
    fs::create_dir_all(root.path().join("etc/mios")).unwrap();
    fs::write(root.path().join("usr/share/mios/mios.toml"), "[ai]\nendpoint='http://localhost:${MIOS_PORTS_AGENT_PIPE}/v1'\nmodel='vendor-backend'\nagent_model='vendor-agent'\n[ports]\nagent_pipe=8700\nstack_id=1\n[mcp.tmux]\ntimeout_s=3\n").unwrap();
    fs::write(
        root.path().join("etc/mios/mios.toml"),
        "[ai]\nmodel='host-backend'\nagent_model='host-agent'\n",
    )
    .unwrap();
    fs::write(
        root.path().join("user.toml"),
        "[ai]\nmodel='chosen-backend'\nagent_model='chosen'\n",
    )
    .unwrap();
    root
}
fn command(root: &std::path::Path) -> Command {
    let mut command = Command::new(env!("CARGO_BIN_EXE_mios-ai-config"));
    for (key, _) in std::env::vars().filter(|(k, _)| k.starts_with("MIOS_")) {
        command.env_remove(key);
    }
    command
        .args(["--root"])
        .arg(root)
        .env("MIOS_USER_TOML", root.join("user.toml"));
    command
}
#[test]
fn real_cli_projects_layered_values_and_preserves_input() {
    let root = fixture();
    let mut child = command(root.path())
        .env("MIOS_AI_MODEL", "ambient-backend")
        .arg("--opencode-stdin")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .spawn()
        .unwrap();
    child
        .stdin
        .take()
        .unwrap()
        .write_all(br#"{"mcp":{"user":{}},"model":"local/old"}"#)
        .unwrap();
    let output = child.wait_with_output().unwrap();
    assert!(output.status.success());
    let value: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(
        value["provider"]["local"]["options"]["baseURL"],
        "http://localhost:18700/v1"
    );
    assert_eq!(value["model"], "local/chosen");
    assert!(value["mcp"].get("user").is_some());
    let mut child = command(root.path())
        .env("MIOS_AI_GATEWAY_MODEL", "explicit-client")
        .arg("--opencode-stdin")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .spawn()
        .unwrap();
    child.stdin.take().unwrap().write_all(b"{}").unwrap();
    let output = child.wait_with_output().unwrap();
    assert!(output.status.success());
    let value: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(value["model"], "local/explicit-client");
}
#[test]
fn real_http_probe_accepts_only_selected_model_and_preserves_failures() {
    let root = fixture();
    for (status, body, pass) in [
        (200, r#"{"data":[{"id":"chosen"}]}"#, true),
        (200, r#"{"data":[{"id":"mios-igpu"}]}"#, false),
        (200, r#"{"data":[]}"#, false),
        (200, "not json", false),
        (401, "private-server-diagnostic", false),
    ] {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let endpoint = format!("http://{}/v1", listener.local_addr().unwrap());
        let server = thread::spawn(move || {
            let (mut connection, _) = listener.accept().unwrap();
            connection
                .set_read_timeout(Some(Duration::from_secs(3)))
                .unwrap();
            let mut request = Vec::new();
            while !request.ends_with(b"\r\n\r\n") {
                let mut byte = [0];
                assert_eq!(connection.read(&mut byte).unwrap(), 1);
                request.push(byte[0]);
            }
            let request = String::from_utf8(request).unwrap();
            assert!(request.starts_with("GET /v1/models HTTP/1.1\r\n"));
            assert!(request.contains("Authorization: Bearer test-private-key"));
            write!(connection, "HTTP/1.1 {status} Result\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len()).unwrap();
        });
        let output = command(root.path())
            .arg("--probe")
            .env("MIOS_AI_ENDPOINT", endpoint)
            .env("MIOS_AI_KEY", "test-private-key")
            .output()
            .unwrap();
        server.join().unwrap();
        assert_eq!(
            output.status.success(),
            pass,
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
        let receipt = format!(
            "{}{}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        assert!(
            !receipt.contains("test-private-key") && !receipt.contains("private-server-diagnostic")
        );
        if pass {
            assert!(receipt.contains("inference not tested"));
        }
    }
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let endpoint = format!("http://{}/v1", listener.local_addr().unwrap());
    drop(listener);
    let output = command(root.path())
        .arg("--probe")
        .env("MIOS_AI_ENDPOINT", endpoint)
        .output()
        .unwrap();
    assert!(!output.status.success());
    assert!(String::from_utf8_lossy(&output.stderr).contains("unavailable or rejected"));
}

#[test]
fn inference_readiness_requires_a_typed_completed_stream_with_text() {
    let root = fixture();
    let completed = serde_json::json!({
        "type": "response.completed", "sequence_number": 0,
        "response": {"status": "completed", "output": [{
            "type": "message", "content": [{"type": "output_text", "text": "OK"}]
        }]}
    });
    let empty = serde_json::json!({
        "type": "response.completed", "sequence_number": 0,
        "response": {"status": "completed", "output": []}
    });
    let failure = serde_json::json!({
        "type": "response.failed", "sequence_number": 0,
        "response": {"status": "failed", "error": {"message": "private-server-diagnostic"}}
    });
    for (body, pass) in [
        (format!("data: {completed}\n\n"), true),
        (format!("data: {empty}\n\n"), false),
        (format!("data: {failure}\n\n"), false),
        (r#"data: {"response":{"status":"completed"}}"#.into(), false),
        (
            r#"data: {"type":"response.created","sequence_number":0}"#.into(),
            false,
        ),
        ("data: not-json\n\n".into(), false),
    ] {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let endpoint = format!("http://{}/v1", listener.local_addr().unwrap());
        let server = thread::spawn(move || {
            for route in ["models", "responses"] {
                let (mut connection, _) = listener.accept().unwrap();
                connection
                    .set_read_timeout(Some(Duration::from_secs(5)))
                    .unwrap();
                let mut header = Vec::new();
                while !header.ends_with(b"\r\n\r\n") {
                    let mut byte = [0];
                    assert_eq!(connection.read(&mut byte).unwrap(), 1);
                    header.push(byte[0]);
                }
                let header = String::from_utf8(header).unwrap();
                assert!(header.contains("Authorization: Bearer test-private-key"));
                let reply = if route == "models" {
                    assert!(header.starts_with("GET /v1/models "));
                    r#"{"data":[{"id":"chosen"}]}"#.to_owned()
                } else {
                    assert!(header.starts_with("POST /v1/responses "));
                    let length: usize = header
                        .lines()
                        .find_map(|line| {
                            let (key, value) = line.split_once(':')?;
                            key.eq_ignore_ascii_case("content-length")
                                .then(|| value.trim().parse().unwrap())
                        })
                        .unwrap();
                    let mut payload = vec![0; length];
                    connection.read_exact(&mut payload).unwrap();
                    let payload: serde_json::Value = serde_json::from_slice(&payload).unwrap();
                    assert_eq!(payload["model"], "chosen");
                    assert_eq!(payload["stream"], true);
                    assert_eq!(payload["tool_choice"], "none");
                    assert_eq!(payload["store"], false);
                    body.clone()
                };
                write!(
                    connection,
                    "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{reply}",
                    reply.len()
                )
                .unwrap();
            }
        });
        let output = command(root.path())
            .arg("--probe-inference")
            .env("MIOS_AI_ENDPOINT", endpoint)
            .env("MIOS_AI_KEY", "test-private-key")
            .output()
            .unwrap();
        server.join().unwrap();
        let receipt = format!(
            "{}{}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        assert_eq!(output.status.success(), pass, "{receipt}");
        assert!(
            !receipt.contains("test-private-key") && !receipt.contains("private-server-diagnostic")
        );
        if pass {
            assert!(receipt.contains("completed Responses stream verified"));
        }
    }
}
