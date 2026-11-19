// AI-hint: End-to-end proof of mios-ssh-ca: init never overwrites, and `serve` (fake tailscale on PATH, real ssh-keygen, 127.0.0.1) issues agent-only 8 h permit-pty certificates to allowed nodes and refuses the rest.
// AI-related: tools/native/mios-ssh-ca/src/main.rs, tools/native/mios-ssh-ca/src/lib.rs, usr/share/mios/mios.toml [ssh.ca]
// AI-functions: Fixture, Server, post, keygen, inspect, epoch_utc, init_never_overwrites_a_key, allowed_nodes_get_agent_certificates, disallowed_requests_are_refused, serve_stays_down_unless_enabled_on_the_signer

use serde_json::{json, Value};
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

const BIN: &str = env!("CARGO_BIN_EXE_mios-ssh-ca");
const OWNER: i64 = 7;
const FOREIGN: i64 = 99;

fn repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../..")
}

/// The fake tailscale's body. `--socket=PATH` is recorded and dropped; `nc HOST
/// PORT` records its arguments and pipes stdin/stdout to 127.0.0.1:PORT, where
/// the signer under test listens (unless `nc.fail` exists: it fails; or
/// `nc.hang`: it accepts and never answers).
const FAKE_TAILSCALE: &str = r#"case "$1" in
  --socket=*) printf '%s\n' "${1#--socket=}" > "$ts/socket-arg"; shift ;;
esac
case "$1" in
  ip) [ "$2" = -4 ] && echo 127.0.0.1 ;;
  status) [ "$2" = --json ] && cat "$ts/status.json" ;;
  whois) [ "$2" = --json ] && [ "$3" = 127.0.0.1 ] && cat "$ts/whois.json" ;;
  nc) printf '%s %s\n' "$2" "$3" > "$ts/nc-args"
      [ -e "$ts/nc.fail" ] && exit 1
      [ -e "$ts/nc.hang" ] && exec sleep 60
      exec python3 -I -c '
import os, socket, sys, threading
s = socket.create_connection(("127.0.0.1", int(sys.argv[1])))
def up():
    while True:
        d = os.read(0, 65536)
        if not d:
            break
        s.sendall(d)
threading.Thread(target=up, daemon=True).start()
while True:
    d = s.recv(65536)
    if not d:
        break
    os.write(1, d)
' "$3" ;;
  *) exit 2 ;;
esac
"#;

/// A MiOS root whose vendor tier is the real SSOT and whose host tier enables
/// the CA with a key under the fixture, plus a fake tailscale on PATH.
struct Fixture {
    dir: tempfile::TempDir,
    port: u16,
}

impl Fixture {
    fn new(host_ca: &str) -> Fixture {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path().join("root");
        std::fs::create_dir_all(root.join("usr/share/mios")).unwrap();
        std::fs::create_dir_all(root.join("etc/mios")).unwrap();
        std::os::unix::fs::symlink(
            repo_root()
                .join("usr/share/mios/mios.toml")
                .canonicalize()
                .unwrap(),
            root.join("usr/share/mios/mios.toml"),
        )
        .unwrap();
        let state = dir.path().join("state");
        std::fs::create_dir(&state).unwrap();
        std::fs::set_permissions(&state, std::fs::Permissions::from_mode(0o700)).unwrap();
        std::fs::write(
            root.join("etc/mios/mios.toml"),
            format!(
                "[ssh.ca]\nkey_file = \"{}\"\n{host_ca}\n",
                state.join("mios-user-ca").display()
            ),
        )
        .unwrap();
        let bin = dir.path().join("bin");
        std::fs::create_dir(&bin).unwrap();
        let fake = bin.join("tailscale");
        std::fs::write(
            &fake,
            format!("#!/bin/sh\nts='{}'\n{FAKE_TAILSCALE}", dir.path().display()),
        )
        .unwrap();
        std::fs::set_permissions(&fake, std::fs::Permissions::from_mode(0o755)).unwrap();
        let port = TcpListener::bind("127.0.0.1:0")
            .unwrap()
            .local_addr()
            .unwrap()
            .port();
        let f = Fixture { dir, port };
        f.status("mios.tail0.ts.net.");
        f
    }

    /// Replace the host tier's extra [ssh.ca] lines (key_file stays).
    fn host(&self, host_ca: &str) {
        std::fs::write(
            self.dir.path().join("root/etc/mios/mios.toml"),
            format!(
                "[ssh.ca]\nkey_file = \"{}\"\n{host_ca}\n",
                self.key_file().display()
            ),
        )
        .unwrap();
    }

    fn key_file(&self) -> PathBuf {
        self.dir.path().join("state/mios-user-ca")
    }

    fn status(&self, dns: &str) {
        let doc = json!({"Self": {"DNSName": dns, "HostName": "ignored", "UserID": OWNER}});
        std::fs::write(self.dir.path().join("status.json"), doc.to_string()).unwrap();
    }

    fn whois(&self, name: &str, user: i64, tags: &[&str]) {
        let doc = json!({"Node": {"Name": format!("{name}.tail0.ts.net."), "User": user, "Tags": tags},
                         "UserProfile": {"ID": user}});
        std::fs::write(self.dir.path().join("whois.json"), doc.to_string()).unwrap();
    }

    fn command(&self, sub: &str) -> Command {
        let mut c = Command::new(BIN);
        c.arg("--root").arg(self.dir.path().join("root")).arg(sub);
        for var in [
            "MIOS_TOML_ROOT",
            "MIOS_VENDOR_TOML",
            "MIOS_TOML",
            "MIOS_HOST_TOML",
        ] {
            c.env_remove(var);
        }
        let path = format!(
            "{}:{}",
            self.dir.path().join("bin").display(),
            std::env::var("PATH").unwrap_or_default()
        );
        c.env("PATH", path)
            .env("MIOS_PORTS_SSH_CA", self.port.to_string());
        c
    }

    /// Run to completion; a command still running after 20 s fails the test
    /// (a `serve` that should have exited but listens instead).
    fn run(&self, sub: &str) -> std::process::Output {
        self.run_with(sub, &[])
    }

    fn run_with(&self, sub: &str, args: &[&str]) -> std::process::Output {
        let mut child = self
            .command(sub)
            .args(args)
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .unwrap();
        let deadline = Instant::now() + Duration::from_secs(20);
        while child.try_wait().unwrap().is_none() {
            if Instant::now() > deadline {
                let _ = child.kill();
                let _ = child.wait();
                panic!("mios-ssh-ca {sub} did not exit");
            }
            std::thread::sleep(Duration::from_millis(50));
        }
        child.wait_with_output().unwrap()
    }

    fn serve(&self) -> Server {
        let log = self.dir.path().join("serve.log");
        let child = self
            .command("serve")
            .stdout(Stdio::null())
            .stderr(std::fs::File::create(&log).unwrap())
            .spawn()
            .unwrap();
        let deadline = Instant::now() + Duration::from_secs(20);
        while TcpStream::connect(("127.0.0.1", self.port)).is_err() {
            assert!(
                Instant::now() < deadline,
                "serve never listened: {}",
                std::fs::read_to_string(&log).unwrap()
            );
            std::thread::sleep(Duration::from_millis(50));
        }
        Server { child, log }
    }
}

struct Server {
    child: Child,
    log: PathBuf,
}

impl Server {
    fn log(&self) -> String {
        std::fs::read_to_string(&self.log).unwrap()
    }
}

impl Drop for Server {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

/// Send raw request bytes; (status, JSON body).
fn raw(port: u16, request: &[u8]) -> (u16, Value) {
    let mut s = TcpStream::connect(("127.0.0.1", port)).unwrap();
    s.set_read_timeout(Some(Duration::from_secs(30))).unwrap();
    let _ = s.write_all(request);
    let mut out = Vec::new();
    let _ = s.read_to_end(&mut out);
    let text = String::from_utf8_lossy(&out).to_string();
    let status = text
        .split(' ')
        .nth(1)
        .and_then(|c| c.parse().ok())
        .unwrap_or_else(|| panic!("no HTTP response: {text:?}"));
    let body = text.split_once("\r\n\r\n").map(|(_, b)| b).unwrap_or("");
    (status, serde_json::from_str(body).unwrap_or(Value::Null))
}

fn post(port: u16, body: &str) -> (u16, Value) {
    let req = format!(
        "POST /v1/ssh/certificates HTTP/1.1\r\nHost: signer\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{body}",
        body.len()
    );
    raw(port, req.as_bytes())
}

fn post_key(port: u16, key: &str) -> (u16, Value) {
    post(port, &json!({"public_key": key}).to_string())
}

/// A fresh ed25519 key pair; returns the public key line.
fn keygen(dir: &Path, name: &str) -> String {
    let path = dir.join(name);
    let ok = Command::new("ssh-keygen")
        .args(["-q", "-t", "ed25519", "-N", "", "-C", "cloud-node", "-f"])
        .arg(&path)
        .status()
        .expect("ssh-keygen must be installed: the signer shells out to it");
    assert!(ok.success());
    std::fs::read_to_string(path.with_extension("pub"))
        .unwrap()
        .trim()
        .to_string()
}

/// `ssh-keygen -L` of a certificate line, in UTC.
fn inspect(dir: &Path, cert: &str) -> String {
    let path = dir.join("inspect-cert.pub");
    std::fs::write(&path, format!("{cert}\n")).unwrap();
    let out = Command::new("ssh-keygen")
        .env("TZ", "UTC")
        .arg("-L")
        .arg("-f")
        .arg(&path)
        .output()
        .unwrap();
    assert!(
        out.status.success(),
        "ssh-keygen -L: {}",
        String::from_utf8_lossy(&out.stderr)
    );
    String::from_utf8_lossy(&out.stdout).to_string()
}

/// "2026-11-19T21:05:07" (UTC) -> unix seconds.
fn epoch_utc(stamp: &str) -> i64 {
    let n: Vec<i64> = stamp
        .split(['-', 'T', ':'])
        .map(|p| p.parse().unwrap())
        .collect();
    let (y, m, d) = (n[0] - i64::from(n[1] <= 2), n[1], n[2]);
    let era = y.div_euclid(400);
    let yoe = y - era * 400;
    let doy = (153 * (m + if m > 2 { -3 } else { 9 }) + 2) / 5 + d - 1;
    let days = era * 146097 + yoe * 365 + yoe / 4 - yoe / 100 + doy - 719468;
    days * 86400 + n[3] * 3600 + n[4] * 60 + n[5]
}

/// The lines of the block under `heading` in `ssh-keygen -L` output.
fn block(listing: &str, heading: &str) -> Vec<String> {
    let mut lines = listing
        .lines()
        .skip_while(|l| !l.trim_start().starts_with(heading));
    let first = lines
        .next()
        .unwrap_or_else(|| panic!("no {heading} in {listing}"));
    let inline = first.split_once(':').map(|(_, v)| v.trim()).unwrap_or("");
    if !inline.is_empty() {
        return vec![inline.to_string()];
    }
    lines
        .take_while(|l| l.starts_with("                "))
        .map(|l| l.trim().to_string())
        .collect()
}

fn now() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_secs() as i64
}

const ENABLED: &str = "enabled = true";

#[test]
fn init_never_overwrites_a_key() {
    let f = Fixture::new(ENABLED);
    let out = f.run("init");
    assert!(
        out.status.success(),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    let key = f.key_file();
    let first = std::fs::read(&key).unwrap();
    assert_eq!(
        std::fs::metadata(&key).unwrap().permissions().mode() & 0o777,
        0o600
    );
    assert!(String::from_utf8_lossy(&first).contains("OPENSSH PRIVATE KEY"));

    let out = f.run("init");
    assert!(out.status.success());
    assert_eq!(
        std::fs::read(&key).unwrap(),
        first,
        "a second init replaced the CA key"
    );

    // Whatever sits at key_file is left alone, even when it is not a key.
    std::fs::write(&key, b"operator's own bytes").unwrap();
    assert!(f.run("init").status.success());
    assert_eq!(std::fs::read(&key).unwrap(), b"operator's own bytes");

    // No scratch key is left behind beside it.
    let stray: Vec<_> = std::fs::read_dir(key.parent().unwrap())
        .unwrap()
        .map(|e| e.unwrap().file_name())
        .filter(|n| n != "mios-user-ca")
        .collect();
    assert!(stray.is_empty(), "{stray:?}");
}

#[test]
fn allowed_nodes_get_agent_certificates() {
    let f = Fixture::new(ENABLED);
    assert!(f.run("init").status.success());
    let pubkey = f.run("pubkey");
    assert!(pubkey.status.success());
    let ca_line = String::from_utf8_lossy(&pubkey.stdout).trim().to_string();
    assert!(ca_line.starts_with("ssh-ed25519 "), "{ca_line}");
    let ca_fpr = {
        let p = f.dir.path().join("ca.pub");
        std::fs::write(&p, format!("{ca_line}\n")).unwrap();
        let o = Command::new("ssh-keygen")
            .arg("-l")
            .arg("-f")
            .arg(&p)
            .output()
            .unwrap();
        String::from_utf8_lossy(&o.stdout)
            .split_whitespace()
            .nth(1)
            .unwrap()
            .to_string()
    };

    let server = f.serve();
    let client = keygen(f.dir.path(), "cloud1");

    // The signer's own user, untagged.
    f.whois("mios-cloud-1", OWNER, &[]);
    let before = now();
    let (status, body) = post_key(f.port, &client);
    assert_eq!(status, 200, "{body}");
    assert_eq!(body["principals"], json!(["agent"]));
    assert_eq!(body["key_id"], "mios-cloud-1-1");
    let valid_before = body["valid_before"].as_i64().unwrap();
    assert!(
        (valid_before - before - 28800).abs() <= 5,
        "valid_before {valid_before}"
    );
    let cert = body["certificate"].as_str().unwrap().to_string();
    assert!(cert.starts_with("ssh-ed25519-cert-v01@openssh.com "));

    let listing = inspect(f.dir.path(), &cert);
    assert!(
        listing.contains("Type: ssh-ed25519-cert-v01@openssh.com user certificate"),
        "{listing}"
    );
    assert_eq!(
        block(&listing, "Signing CA:")[0].split_whitespace().nth(1),
        Some(ca_fpr.as_str())
    );
    assert_eq!(block(&listing, "Key ID:"), vec!["\"mios-cloud-1-1\""]);
    assert_eq!(block(&listing, "Serial:"), vec!["1"]);
    assert_eq!(block(&listing, "Principals:"), vec!["agent"], "{listing}");
    assert_eq!(
        block(&listing, "Critical Options:"),
        vec!["(none)"],
        "{listing}"
    );
    assert_eq!(
        block(&listing, "Extensions:"),
        vec!["permit-pty"],
        "{listing}"
    );
    let valid = block(&listing, "Valid:")[0].clone();
    let parts: Vec<&str> = valid.split_whitespace().collect();
    assert_eq!((parts[0], parts[2]), ("from", "to"), "{valid}");
    let (from, to) = (epoch_utc(parts[1]), epoch_utc(parts[3]));
    // ssh-keygen backdates the start to the minute, so the expiry is what is exact.
    assert!((to - before - 28800).abs() <= 5, "{valid}");
    assert!(
        (to - valid_before).abs() <= 5,
        "{valid} vs valid_before {valid_before}"
    );
    assert!(from <= before && before - from < 120, "{valid}");

    // A foreign user's node carrying a trusted tag; the serial moves on.
    f.whois("mios-cloud-2", FOREIGN, &["tag:mios"]);
    let other = keygen(f.dir.path(), "cloud2");
    let (status, body) = post_key(f.port, &other);
    assert_eq!(status, 200, "{body}");
    assert_eq!(body["key_id"], "mios-cloud-2-2");
    assert_eq!(
        block(
            &inspect(f.dir.path(), body["certificate"].as_str().unwrap()),
            "Serial:"
        ),
        vec!["2"]
    );
    assert_eq!(
        std::fs::read_to_string(f.dir.path().join("state/serial"))
            .unwrap()
            .trim(),
        "2"
    );

    let log = server.log();
    assert!(
        log.contains("issued mios-cloud-1-1 to mios-cloud-1"),
        "{log}"
    );
    assert!(
        log.contains("issued mios-cloud-2-2 to mios-cloud-2"),
        "{log}"
    );
    for secret in [&client, &other, &cert] {
        let blob = secret.split_whitespace().nth(1).unwrap();
        assert!(!log.contains(blob), "key material reached the log: {log}");
    }
}

#[test]
fn disallowed_requests_are_refused() {
    let f = Fixture::new(ENABLED);
    assert!(f.run("init").status.success());
    let server = f.serve();
    let client = keygen(f.dir.path(), "cloud");
    let refused = |status: u16, body: &Value, want_status: u16, code: &str| {
        assert_eq!(status, want_status, "{body}");
        assert_eq!(body["error"]["code"], code, "{body}");
        assert!(
            body["error"]["message"]
                .as_str()
                .is_some_and(|m| !m.is_empty()),
            "{body}"
        );
        assert!(body["error"]["type"].is_string(), "{body}");
        assert!(body.get("certificate").is_none());
    };

    // Wrong hostname, though owned by the signer's user.
    f.whois("mios-blade-1", OWNER, &[]);
    let (s, b) = post_key(f.port, &client);
    refused(s, &b, 403, "hostname_not_allowed");
    assert_eq!(b["error"]["type"], "permission_error");

    // Right hostname, foreign owner, no tag; and a foreign owner with an untrusted tag.
    f.whois("mios-cloud-9", FOREIGN, &[]);
    let (s, b) = post_key(f.port, &client);
    refused(s, &b, 403, "owner_not_trusted");
    f.whois("mios-cloud-9", FOREIGN, &["tag:other"]);
    let (s, b) = post_key(f.port, &client);
    refused(s, &b, 403, "owner_not_trusted");

    // A peer the tailnet does not know.
    std::fs::remove_file(f.dir.path().join("whois.json")).unwrap();
    let (s, b) = post_key(f.port, &client);
    refused(s, &b, 403, "unidentified_peer");

    // From here the caller is allowed: only the request itself is wrong.
    f.whois("mios-cloud-1", OWNER, &[]);
    let (s, b) = post_key(f.port, &client);
    assert_eq!(s, 200, "{b}");
    let cert = b["certificate"].as_str().unwrap().to_string();

    // A certificate offered as the key, as sent and with its type relabelled
    // so only ssh-keygen can tell.
    let (s, b) = post_key(f.port, &cert);
    refused(s, &b, 400, "certificate_not_key");
    let disguised = format!("ssh-ed25519 {}", cert.split_whitespace().nth(1).unwrap());
    let (s, b) = post_key(f.port, &disguised);
    assert_eq!(s, 400, "{b}");
    assert!(
        b["error"]["code"] == "invalid_public_key" || b["error"]["code"] == "certificate_not_key",
        "{b}"
    );

    // Not a key: base64 that ssh-keygen cannot read, and plain text.
    let (s, b) = post_key(f.port, "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGFiYw==");
    refused(s, &b, 400, "invalid_public_key");
    let (s, b) = post_key(f.port, "not a key");
    refused(s, &b, 400, "invalid_public_key");

    // An oversized body is refused from its Content-Length, before it is read.
    let max = 16384;
    let big = format!("{{\"public_key\": \"{}\"}}", "A".repeat(max + 1));
    let (s, b) = post(f.port, &big);
    refused(s, &b, 413, "body_too_large");
    // So is an oversized head.
    let head = format!(
        "POST /v1/ssh/certificates HTTP/1.1\r\nX-Pad: {}\r\n\r\n",
        "a".repeat(9000)
    );
    let (s, b) = raw(f.port, head.as_bytes());
    refused(s, &b, 431, "header_too_large");
    // And a request on another route or method.
    let (s, b) = raw(f.port, b"GET /v1/ssh/certificates HTTP/1.1\r\n\r\n");
    refused(s, &b, 405, "method_not_allowed");
    let (s, b) = raw(
        f.port,
        b"POST /v1/models HTTP/1.1\r\nContent-Length: 0\r\n\r\n",
    );
    refused(s, &b, 404, "not_found");

    // Only one certificate was issued, and the serial says so.
    assert_eq!(
        std::fs::read_to_string(f.dir.path().join("state/serial"))
            .unwrap()
            .trim(),
        "1"
    );
    let log = server.log();
    assert!(
        log.contains("refused mios-blade-1: hostname_not_allowed"),
        "{log}"
    );
    assert!(
        log.contains("refused mios-cloud-9: owner_not_trusted"),
        "{log}"
    );
    assert!(
        log.contains("refused mios-cloud-1: certificate_not_key"),
        "{log}"
    );
    let blob = client.split_whitespace().nth(1).unwrap();
    assert!(
        !log.contains(blob) && !log.contains(cert.split_whitespace().nth(1).unwrap()),
        "{log}"
    );
}

#[test]
fn serve_stays_down_unless_enabled_on_the_signer() {
    // Disabled: exits 0 without listening.
    let f = Fixture::new("enabled = false");
    let out = f.run("serve");
    assert!(
        out.status.success(),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    assert!(String::from_utf8_lossy(&out.stderr).contains("not serving"));

    // Enabled on a node that is not the signer: exits 0 too.
    let f = Fixture::new(ENABLED);
    assert!(f.run("init").status.success());
    f.status("mios-other.tail0.ts.net.");
    let out = f.run("serve");
    assert!(
        out.status.success(),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    assert!(String::from_utf8_lossy(&out.stderr).contains("not the signer"));
    assert!(TcpStream::connect(("127.0.0.1", f.port)).is_err());

    // The signer without a key fails, so systemd retries rather than serving nothing.
    let f = Fixture::new(ENABLED);
    let out = f.run("serve");
    assert!(!out.status.success());
    assert!(String::from_utf8_lossy(&out.stderr).contains("mios-ssh-ca init"));
}

/// A cloud node's client state directory and the fake daemon socket it names.
fn client_dir(f: &Fixture) -> (PathBuf, PathBuf) {
    let dir = f.dir.path().join("client");
    std::fs::create_dir(&dir).unwrap();
    std::fs::set_permissions(&dir, std::fs::Permissions::from_mode(0o700)).unwrap();
    (dir, f.dir.path().join("tailscaled.sock"))
}

fn request(f: &Fixture, dir: &Path, sock: &Path, extra: &[&str]) -> std::process::Output {
    let mut args = vec![
        "--dir",
        dir.to_str().unwrap(),
        "--socket",
        sock.to_str().unwrap(),
    ];
    args.extend_from_slice(extra);
    f.run_with("request", &args)
}

fn stderr(o: &std::process::Output) -> String {
    String::from_utf8_lossy(&o.stderr).to_string()
}

fn fingerprint(path: &Path) -> String {
    let o = Command::new("ssh-keygen")
        .arg("-l")
        .arg("-f")
        .arg(path)
        .output()
        .unwrap();
    assert!(o.status.success(), "{}", String::from_utf8_lossy(&o.stderr));
    String::from_utf8_lossy(&o.stdout)
        .split_whitespace()
        .nth(1)
        .unwrap()
        .to_string()
}

#[test]
fn a_cloud_node_installs_and_renews_its_agent_certificate() {
    let f = Fixture::new(ENABLED);
    assert!(f.run("init").status.success());
    let server = f.serve();
    f.whois("mios-cloud-1", OWNER, &[]);
    let (dir, sock) = client_dir(&f);

    let out = request(&f, &dir, &sock, &[]);
    assert!(out.status.success(), "{}", stderr(&out));
    assert!(
        out.stdout.is_empty(),
        "request wrote to stdout, which is ssh's channel inside ProxyCommand"
    );
    // It went over `tailscale --socket=<sock> nc <signer> <[ports].ssh_ca>`.
    let nc = std::fs::read_to_string(f.dir.path().join("nc-args")).unwrap();
    assert_eq!(nc.trim(), format!("mios {}", f.port));
    assert_eq!(
        std::fs::read_to_string(f.dir.path().join("socket-arg"))
            .unwrap()
            .trim(),
        sock.to_str().unwrap()
    );

    let key = dir.join("id_ed25519");
    let cert = dir.join("id_ed25519-cert.pub");
    assert_eq!(
        std::fs::metadata(&key).unwrap().permissions().mode() & 0o777,
        0o600
    );
    let key_bytes = std::fs::read(&key).unwrap();
    let listing = inspect(f.dir.path(), std::fs::read_to_string(&cert).unwrap().trim());
    assert_eq!(block(&listing, "Principals:"), vec!["agent"]);
    assert_eq!(block(&listing, "Key ID:"), vec!["\"mios-cloud-1-1\""]);
    // The certificate is for this node's own key.
    let own = Command::new("ssh-keygen")
        .arg("-y")
        .arg("-f")
        .arg(&key)
        .output()
        .unwrap();
    let pubf = f.dir.path().join("own.pub");
    std::fs::write(&pubf, &own.stdout).unwrap();
    assert_eq!(
        block(&listing, "Public key:")[0]
            .split_whitespace()
            .nth(1)
            .unwrap(),
        fingerprint(&pubf)
    );

    // The ssh_config it writes logs in as agent with this certificate, through tailscale nc.
    let cfg = std::fs::read_to_string(dir.join("ssh_config")).unwrap();
    assert!(cfg.contains("\nHost mios mios-*\n"), "{cfg}");
    assert!(
        cfg.contains("    User agent\n") && cfg.contains("    Port 8100\n"),
        "{cfg}"
    );
    assert!(
        cfg.contains(&format!("    CertificateFile {}\n", cert.display())),
        "{cfg}"
    );
    assert!(
        cfg.contains(&format!(
            "    ProxyCommand tailscale --socket={} nc %h %p\n",
            sock.display()
        )),
        "{cfg}"
    );
    assert!(
        cfg.contains(&format!(
            "Match host mios,mios-* exec \"env MIOS_PORTS_SSH_CA={} {BIN} --root {} request --dir {} --socket {} --quiet >&2; true\"\nHost mios mios-*\n",
            f.port,
            f.dir.path().join("root").display(),
            dir.display(),
            sock.display()
        )),
        "{cfg}"
    );

    // Still valid for longer than renew_before_s: nothing is asked, nothing changes.
    let before = std::fs::read(&cert).unwrap();
    let out = request(&f, &dir, &sock, &[]);
    assert!(out.status.success(), "{}", stderr(&out));
    assert!(stderr(&out).contains("not renewed"), "{}", stderr(&out));
    assert_eq!(std::fs::read(&cert).unwrap(), before);
    assert_eq!(
        std::fs::read_to_string(f.dir.path().join("state/serial"))
            .unwrap()
            .trim(),
        "1"
    );
    // --quiet says nothing while it is valid (ssh runs it before every login).
    let out = request(&f, &dir, &sock, &["--quiet"]);
    assert!(
        out.status.success() && out.stderr.is_empty(),
        "{}",
        stderr(&out)
    );

    // Inside renew_before_s it renews: a new serial on the same key.
    f.host(&format!("{ENABLED}\nrenew_before_s = 30000"));
    let out = request(&f, &dir, &sock, &[]);
    assert!(out.status.success(), "{}", stderr(&out));
    let renewed = std::fs::read_to_string(&cert).unwrap();
    assert_eq!(
        block(&inspect(f.dir.path(), renewed.trim()), "Serial:"),
        vec!["2"]
    );
    assert_eq!(
        std::fs::read(&key).unwrap(),
        key_bytes,
        "renewal replaced the node's key"
    );

    // Pinned to the CA the signer really uses, it still installs.
    let ca = String::from_utf8(f.run("pubkey").stdout).unwrap();
    f.host(&format!("{ENABLED}\npublic_key = \"{}\"", ca.trim()));
    let out = request(&f, &dir, &sock, &["--force"]);
    assert!(out.status.success(), "{}", stderr(&out));
    assert!(
        server
            .log()
            .contains("issued mios-cloud-1-3 to mios-cloud-1"),
        "{}",
        server.log()
    );
}

#[test]
fn a_cloud_node_installs_nothing_it_should_not_trust() {
    let f = Fixture::new(ENABLED);
    assert!(f.run("init").status.success());
    let _server = f.serve();
    let (dir, sock) = client_dir(&f);
    let cert = dir.join("id_ed25519-cert.pub");

    // The signer refuses this node: the error object reaches the operator, no certificate.
    f.whois("mios-blade-1", OWNER, &[]);
    let out = request(&f, &dir, &sock, &[]);
    assert!(!out.status.success());
    assert!(
        stderr(&out).contains("403 hostname_not_allowed"),
        "{}",
        stderr(&out)
    );
    assert!(!cert.exists());

    // An allowed node gets one.
    f.whois("mios-cloud-1", OWNER, &[]);
    assert!(request(&f, &dir, &sock, &[]).status.success());
    let good = std::fs::read(&cert).unwrap();

    // Pinned to a different CA, the signer's certificate is refused and the
    // installed one is kept.
    let other = keygen(f.dir.path(), "other-ca");
    f.host(&format!("{ENABLED}\npublic_key = \"{other}\""));
    let out = request(&f, &dir, &sock, &["--force"]);
    assert!(!out.status.success());
    assert!(
        stderr(&out).contains("not signed by [ssh.ca].public_key"),
        "{}",
        stderr(&out)
    );
    assert_eq!(std::fs::read(&cert).unwrap(), good);
    // ...and a certificate from that other CA does not count as installed, so
    // every run asks again rather than trusting it.
    let out = request(&f, &dir, &sock, &[]);
    assert!(!out.status.success(), "{}", stderr(&out));

    // A signer that cannot be reached fails promptly and keeps the certificate.
    f.host(ENABLED);
    std::fs::write(f.dir.path().join("nc.fail"), "").unwrap();
    let started = Instant::now();
    let out = request(&f, &dir, &sock, &["--force"]);
    assert!(!out.status.success());
    assert!(
        stderr(&out).contains("closed without a reply") || stderr(&out).contains("no reply"),
        "{}",
        stderr(&out)
    );
    assert!(started.elapsed() < Duration::from_secs(15));
    assert_eq!(std::fs::read(&cert).unwrap(), good);

    // A signer that accepts and never answers is given read_timeout_s, not forever.
    std::fs::remove_file(f.dir.path().join("nc.fail")).unwrap();
    std::fs::write(f.dir.path().join("nc.hang"), "").unwrap();
    f.host(&format!("{ENABLED}\nread_timeout_s = 2"));
    let started = Instant::now();
    let out = request(&f, &dir, &sock, &["--force"]);
    assert!(!out.status.success());
    assert!(stderr(&out).contains("no reply"), "{}", stderr(&out));
    assert!(
        started.elapsed() < Duration::from_secs(10),
        "{:?}",
        started.elapsed()
    );
    assert_eq!(std::fs::read(&cert).unwrap(), good);

    // Nothing but the expected files is left in the client directory.
    let mut names: Vec<String> = std::fs::read_dir(&dir)
        .unwrap()
        .map(|e| e.unwrap().file_name().into_string().unwrap())
        .collect();
    names.sort();
    assert_eq!(names, ["id_ed25519", "id_ed25519-cert.pub", "ssh_config"]);
}
