// AI-hint: Client half of the MiOS user SSH CA: a cloud node keeps its own ed25519 key, asks the [ssh.ca].signer over `tailscale nc` for an [ssh].user certificate, verifies it and installs it with an ssh_config that renews before each login.
// AI-related: tools/native/mios-ssh-ca/src/main.rs, tools/native/mios-ssh-ca/src/lib.rs, .devcontainer/cloud-shell/bootstrap.sh, usr/share/mios/mios.toml [ssh.ca]
// AI-functions: Options, run, public_line, fingerprint, listing, installed_until, exchange, install_certificate, write_atomic

use crate::generate_key;
use mios_ssh_ca::{
    check_issued, fingerprint_token, needs_renewal, parse_cert_listing, parse_http_response,
    render_request, render_ssh_config, renew_invocation, run_bounded, Config, CARRIED_ENV,
};
use serde_json::json;
use std::io::{Read, Write};
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::mpsc;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

pub struct Options {
    pub dir: PathBuf,
    pub socket: Option<PathBuf>,
    pub force: bool,
    pub quiet: bool,
}

impl Options {
    pub fn parse(args: &[String]) -> Option<Options> {
        let (mut dir, mut socket, mut force, mut quiet) = (None, None, false, false);
        let mut it = args.iter();
        while let Some(a) = it.next() {
            match a.as_str() {
                "--dir" => dir = Some(PathBuf::from(it.next()?)),
                "--socket" => socket = Some(PathBuf::from(it.next()?)),
                "--force" => force = true,
                "--quiet" => quiet = true,
                _ => return None,
            }
        }
        Some(Options {
            dir: dir?,
            socket,
            force,
            quiet,
        })
    }
}

const KEY: &str = "id_ed25519";
const CERT: &str = "id_ed25519-cert.pub";

/// Everything goes to stderr: inside ssh's ProxyCommand, stdout is the connection.
pub fn run(cfg: &Config, root: Option<&Path>, opts: &Options) -> Result<(), String> {
    let dir = &opts.dir;
    if !dir.is_dir() {
        return Err(format!("{} is not a directory", dir.display()));
    }
    let timeout = cfg.limits.read_timeout;
    let key = dir.join(KEY);
    let cert = dir.join(CERT);
    if generate_key(&key, "mios-ssh-ca-client", timeout)? {
        eprintln!("mios-ssh-ca: created {}", key.display());
    }
    let public = public_line(&key, timeout)?;
    let own_fp = fingerprint(&public, timeout)?;
    let ca_fp = match cfg.ca_public_key.as_str() {
        "" => None,
        ca => Some(fingerprint(ca, timeout)?),
    };
    let now = now();

    let binary = std::env::current_exe().map_err(|e| format!("own path: {e}"))?;
    let carried: Vec<(String, String)> = CARRIED_ENV
        .iter()
        .filter_map(|k| {
            std::env::var(k)
                .ok()
                .filter(|v| !v.is_empty())
                .map(|v| (k.to_string(), v))
        })
        .collect();
    let root = root
        .map(std::path::absolute)
        .transpose()
        .map_err(|e| e.to_string())?;
    let renew = renew_invocation(&binary, root.as_deref(), &carried)?;
    let ssh_config = render_ssh_config(cfg, &renew, dir, opts.socket.as_deref())?;
    write_atomic(&dir.join("ssh_config"), ssh_config.as_bytes(), 0o644)?;

    let current = installed_until(&cert, &own_fp, cfg, ca_fp.as_deref(), now, timeout);
    if !opts.force && !needs_renewal(current, now, cfg.renew_before_s) {
        if !opts.quiet {
            eprintln!(
                "mios-ssh-ca: {} is valid for {} s more; not renewed",
                cert.display(),
                current.unwrap_or(now) - now
            );
        }
        return Ok(());
    }

    let body = json!({ "public_key": public }).to_string();
    let raw = exchange(
        cfg,
        opts.socket.as_deref(),
        &render_request(&cfg.signer, &body),
    )?;
    let limit = cfg.limits.max_header_bytes + cfg.limits.max_body_bytes;
    let (status, reply) = parse_http_response(&raw, limit)?;
    if status != 200 {
        let e = &reply["error"];
        return Err(format!(
            "the signer refused ({status} {}): {}",
            e["code"].as_str().unwrap_or("?"),
            e["message"].as_str().unwrap_or("no message")
        ));
    }
    let line = reply["certificate"]
        .as_str()
        .filter(|l| !l.is_empty() && !l.chars().any(|c| c.is_control()))
        .ok_or("the signer's reply carries no one-line certificate")?;
    let info = install_certificate(dir, line, &own_fp, cfg, ca_fp.as_deref(), now, timeout)?;
    eprintln!(
        "mios-ssh-ca: installed {} ({}), valid until {}",
        cert.display(),
        info.key_id,
        info.valid_to
    );
    Ok(())
}

fn now() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}

/// The public key, derived from the private key itself rather than a .pub file
/// that could disagree with it.
fn public_line(key: &Path, timeout: Duration) -> Result<String, String> {
    let out = run_bounded(
        Command::new("ssh-keygen").arg("-y").arg("-f").arg(key),
        timeout,
    )?;
    let line = String::from_utf8_lossy(&out).trim().to_string();
    let mut fields = line.split_whitespace();
    match (fields.next(), fields.next()) {
        (Some(kind), Some(blob)) => Ok(format!("{kind} {blob}")),
        _ => Err(format!("{}: ssh-keygen -y printed no key", key.display())),
    }
}

fn fingerprint(line: &str, timeout: Duration) -> Result<String, String> {
    let tmp = tempfile::NamedTempFile::new().map_err(|e| e.to_string())?;
    std::fs::write(tmp.path(), format!("{line}\n")).map_err(|e| e.to_string())?;
    let out = run_bounded(
        Command::new("ssh-keygen")
            .arg("-l")
            .arg("-f")
            .arg(tmp.path()),
        timeout,
    )?;
    fingerprint_token(&String::from_utf8_lossy(&out))
        .ok_or_else(|| "ssh-keygen -l printed no fingerprint".into())
}

fn listing(path: &Path, timeout: Duration) -> Result<mios_ssh_ca::CertInfo, String> {
    let out = run_bounded(
        Command::new("ssh-keygen")
            .env("TZ", "UTC")
            .arg("-L")
            .arg("-f")
            .arg(path),
        timeout,
    )?;
    parse_cert_listing(&String::from_utf8_lossy(&out))
}

/// The installed certificate's expiry, when it is still one this node would
/// accept from the signer; anything else counts as no certificate.
fn installed_until(
    cert: &Path,
    own_fp: &str,
    cfg: &Config,
    ca_fp: Option<&str>,
    now: i64,
    timeout: Duration,
) -> Option<i64> {
    let info = listing(cert, timeout).ok()?;
    check_issued(&info, own_fp, &cfg.principal, ca_fp, now).ok()?;
    Some(info.valid_to)
}

/// One request over `tailscale nc <signer> <port>`: a userspace node has no
/// TUN, so the tailnet is reached through the daemon itself, and the daemon
/// authenticates the far end as the node named signer. stdin stays open until
/// the reply is in (the signer closes after one response); the whole exchange
/// is bounded in time and size.
fn exchange(cfg: &Config, socket: Option<&Path>, request: &str) -> Result<Vec<u8>, String> {
    let mut cmd = Command::new("tailscale");
    if let Some(s) = socket {
        cmd.arg(format!("--socket={}", s.display()));
    }
    cmd.args(["nc", &cfg.signer, &cfg.port.to_string()])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    let mut child = cmd.spawn().map_err(|e| format!("tailscale nc: {e}"))?;
    let mut stdin = child.stdin.take().ok_or("tailscale nc: no stdin")?;
    let mut stdout = child.stdout.take().ok_or("tailscale nc: no stdout")?;
    let limit = cfg.limits.max_header_bytes + cfg.limits.max_body_bytes;
    let (tx, rx) = mpsc::channel();
    std::thread::spawn(move || {
        let mut buf = Vec::new();
        let _ = (&mut stdout).take(limit as u64 + 1).read_to_end(&mut buf);
        let _ = tx.send(buf);
    });
    let deadline = Instant::now() + cfg.limits.read_timeout;
    let sent = stdin
        .write_all(request.as_bytes())
        .and_then(|_| stdin.flush());
    let reply = rx.recv_timeout(deadline.saturating_duration_since(Instant::now()));
    drop(stdin);
    let _ = child.kill();
    let _ = child.wait();
    sent.map_err(|e| format!("tailscale nc to {}: {e}", cfg.signer))?;
    let raw = reply.map_err(|_| format!("no reply from {}:{} in time", cfg.signer, cfg.port))?;
    if raw.is_empty() {
        return Err(format!(
            "{}:{} closed without a reply",
            cfg.signer, cfg.port
        ));
    }
    Ok(raw)
}

/// Verify the returned certificate with ssh-keygen before it replaces the
/// installed one; a rejected reply leaves the old certificate in place.
fn install_certificate(
    dir: &Path,
    line: &str,
    own_fp: &str,
    cfg: &Config,
    ca_fp: Option<&str>,
    now: i64,
    timeout: Duration,
) -> Result<mios_ssh_ca::CertInfo, String> {
    let staged = tempfile::Builder::new()
        .prefix(".cert-")
        .suffix(".pub")
        .tempfile_in(dir)
        .map_err(|e| e.to_string())?;
    std::fs::write(staged.path(), format!("{line}\n")).map_err(|e| e.to_string())?;
    let info =
        listing(staged.path(), timeout).map_err(|e| format!("the signer's certificate: {e}"))?;
    check_issued(&info, own_fp, &cfg.principal, ca_fp, now)
        .map_err(|e| format!("refusing to install the signer's certificate: {e}"))?;
    std::fs::set_permissions(staged.path(), std::fs::Permissions::from_mode(0o644))
        .map_err(|e| e.to_string())?;
    staged
        .persist(dir.join(CERT))
        .map_err(|e| format!("{}: {}", dir.join(CERT).display(), e.error))?;
    Ok(info)
}

fn write_atomic(path: &Path, bytes: &[u8], mode: u32) -> Result<(), String> {
    let dir = path.parent().ok_or("no parent directory")?;
    let staged = tempfile::Builder::new()
        .prefix(".staged-")
        .tempfile_in(dir)
        .map_err(|e| e.to_string())?;
    std::fs::write(staged.path(), bytes).map_err(|e| e.to_string())?;
    std::fs::set_permissions(staged.path(), std::fs::Permissions::from_mode(mode))
        .map_err(|e| e.to_string())?;
    staged
        .persist(path)
        .map_err(|e| format!("{}: {}", path.display(), e.error))?;
    Ok(())
}
