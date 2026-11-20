// AI-hint: MiOS user SSH CA signer: init the CA key, print its public key, and serve tailnet-only certificate issuance to verified cloud nodes.
// AI-related: tools/native/mios-ssh-ca/src/lib.rs, usr/share/mios/mios.toml [ssh.ca], usr/lib/systemd/system/mios-ssh-ca.service, usr/lib/mios/mios_credentials.py
// AI-functions: main, run, init, pubkey, serve, handle, issue, accepted_key, sign, respond, DeadlineReader

#![forbid(unsafe_code)]

use mios_ssh_ca::{
    authorize, load_config, next_serial, parse_request_body, parse_self, parse_whois, read_body,
    read_head, run_bounded, ApiError, Config, Tailscale, ROUTE,
};
use serde_json::{json, Value};
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Command, ExitCode};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

const USAGE: &str = "usage: mios-ssh-ca [--root DIR] <init|pubkey|serve>";

fn main() -> ExitCode {
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            eprintln!("mios-ssh-ca: {e}");
            ExitCode::FAILURE
        }
    }
}

fn run() -> Result<(), String> {
    let mut args: Vec<String> = std::env::args().skip(1).collect();
    let mut root = None;
    if args.first().map(String::as_str) == Some("--root") {
        if args.len() < 2 {
            return Err(USAGE.into());
        }
        root = Some(PathBuf::from(args.remove(1)));
        args.remove(0);
    }
    let [command] = args.as_slice() else {
        return Err(USAGE.into());
    };
    let cfg = load_config(root.as_deref())?;
    match command.as_str() {
        "init" => init(&cfg),
        "pubkey" => pubkey(&cfg),
        "serve" => serve(&cfg),
        _ => Err(USAGE.into()),
    }
}

fn keygen_timeout(cfg: &Config) -> Duration {
    cfg.limits.read_timeout
}

/// Create the CA key if it is absent. It is generated beside key_file and
/// hard-linked into place, which fails rather than replaces: an existing key
/// (or anything else at that path) is never overwritten.
fn init(cfg: &Config) -> Result<(), String> {
    let key = &cfg.key_file;
    if key.symlink_metadata().is_ok() {
        println!("mios-ssh-ca: {} exists; left untouched", key.display());
        return Ok(());
    }
    let dir = key.parent().filter(|d| d.is_dir()).ok_or_else(|| {
        format!(
            "{} has no directory (declared in tmpfiles.d)",
            key.display()
        )
    })?;
    let tmp = tempfile::Builder::new()
        .prefix(".mios-ssh-ca-")
        .tempdir_in(dir)
        .map_err(|e| format!("{}: {e}", dir.display()))?;
    let fresh = tmp.path().join("key");
    run_bounded(
        Command::new("ssh-keygen")
            .args(["-q", "-t", "ed25519", "-N", "", "-C"])
            .arg(format!("mios-user-ca@{}", cfg.signer))
            .arg("-f")
            .arg(&fresh),
        keygen_timeout(cfg),
    )?;
    match std::fs::hard_link(&fresh, key) {
        Ok(()) => {
            println!("mios-ssh-ca: created {}", key.display());
            Ok(())
        }
        Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {
            println!("mios-ssh-ca: {} exists; left untouched", key.display());
            Ok(())
        }
        Err(e) => Err(format!("{}: {e}", key.display())),
    }
}

fn pubkey(cfg: &Config) -> Result<(), String> {
    let out = run_bounded(
        Command::new("ssh-keygen")
            .arg("-y")
            .arg("-f")
            .arg(&cfg.key_file),
        keygen_timeout(cfg),
    )
    .map_err(|e| {
        format!(
            "{}: {e} (run `mios-ssh-ca init` first)",
            cfg.key_file.display()
        )
    })?;
    let line = String::from_utf8_lossy(&out);
    println!("{}", line.trim());
    Ok(())
}

fn serve(cfg: &Config) -> Result<(), String> {
    if !cfg.enabled {
        eprintln!("mios-ssh-ca: [ssh.ca].enabled is false; not serving");
        return Ok(());
    }
    let ts = Tailscale {
        timeout: cfg.limits.read_timeout,
    };
    let (me, self_user) = parse_self(&ts.status()?)?;
    if !me.eq_ignore_ascii_case(&cfg.signer) {
        eprintln!(
            "mios-ssh-ca: this node is {me:?}, not the signer {:?}; not serving",
            cfg.signer
        );
        return Ok(());
    }
    if !cfg.key_file.is_file() {
        return Err(format!(
            "{} is absent; run `mios-ssh-ca init` on the signer",
            cfg.key_file.display()
        ));
    }
    let ip = ts.ipv4()?;
    let listener = TcpListener::bind(SocketAddr::from((ip, cfg.port)))
        .map_err(|e| format!("bind {ip}:{}: {e}", cfg.port))?;
    let bound = listener
        .local_addr()
        .map_err(|e| format!("bind {ip}:{}: {e}", cfg.port))?;
    eprintln!("mios-ssh-ca: serving {ROUTE} on {bound}");
    for stream in listener.incoming() {
        match stream {
            Ok(stream) => handle(stream, cfg, &ts, self_user),
            Err(e) => eprintln!("mios-ssh-ca: accept: {e}"),
        }
    }
    Ok(())
}

/// Bounds the whole request, not each read: a peer trickling one byte per
/// interval still runs out of time.
struct DeadlineReader<'a> {
    stream: &'a TcpStream,
    deadline: Instant,
}

impl Read for DeadlineReader<'_> {
    fn read(&mut self, buf: &mut [u8]) -> std::io::Result<usize> {
        let left = self.deadline.saturating_duration_since(Instant::now());
        if left.is_zero() {
            return Err(std::io::ErrorKind::TimedOut.into());
        }
        self.stream.set_read_timeout(Some(left))?;
        (&mut &*self.stream).read(buf)
    }
}

fn handle(stream: TcpStream, cfg: &Config, ts: &Tailscale, self_user: i64) {
    let _ = stream.set_write_timeout(Some(cfg.limits.read_timeout));
    let mut node = None;
    let result = issue(&stream, cfg, ts, self_user, &mut node);
    let who = node.as_deref().unwrap_or("an unidentified peer");
    let (status, body) = match result {
        Ok((body, key_id)) => {
            eprintln!("mios-ssh-ca: issued {key_id} to {who}");
            (200, body)
        }
        Err(e) => {
            eprintln!("mios-ssh-ca: refused {who}: {}", e.code);
            (e.status, e.body())
        }
    };
    respond(stream, status, &body, cfg.limits.max_body_bytes);
}

fn issue(
    stream: &TcpStream,
    cfg: &Config,
    ts: &Tailscale,
    self_user: i64,
    node: &mut Option<String>,
) -> Result<(Value, String), ApiError> {
    let mut reader = DeadlineReader {
        stream,
        deadline: Instant::now() + cfg.limits.read_timeout,
    };
    let head = read_head(&mut reader, cfg.limits.max_header_bytes)?;
    if head.path != ROUTE {
        return Err(ApiError::new(
            404,
            "not_found",
            format!("only {ROUTE} is served"),
        ));
    }
    if head.method != "POST" {
        return Err(ApiError::new(405, "method_not_allowed", "use POST"));
    }
    let peer_ip = stream
        .peer_addr()
        .map_err(|_| ApiError::new(403, "unidentified_peer", "the caller's address is unknown"))?
        .ip()
        .to_string();
    let unidentified = || {
        ApiError::new(
            403,
            "unidentified_peer",
            "the caller is not a node of this tailnet",
        )
    };
    let peer = ts
        .whois(&peer_ip)
        .ok()
        .and_then(|w| parse_whois(&w).ok())
        .ok_or_else(unidentified)?;
    if mios_ssh_ca::valid_label(&peer.name) {
        *node = Some(peer.name.clone());
    }
    authorize(&peer, self_user, &cfg.hostname_glob, &cfg.trusted_tags)?;
    let body = read_body(&mut reader, &head, cfg.limits.max_body_bytes)?;
    let key = parse_request_body(&body)?;

    let work = tempfile::Builder::new()
        .prefix("mios-ssh-ca-")
        .tempdir()
        .map_err(|_| ApiError::new(500, "server_error", "no scratch directory"))?;
    let key_path = work.path().join("key.pub");
    std::fs::write(&key_path, format!("{key}\n"))
        .map_err(|_| ApiError::new(500, "server_error", "could not stage the key"))?;
    accepted_key(&key_path, cfg)?;

    let state_dir = cfg.key_file.parent().unwrap_or(Path::new("/"));
    let serial = next_serial(state_dir).map_err(|e| {
        eprintln!("mios-ssh-ca: serial: {e}");
        ApiError::new(500, "server_error", "the serial could not be reserved")
    })?;
    let key_id = format!("{}-{serial}", peer.name);
    let issued_at = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let certificate = sign(cfg, &key_path, &key_id, serial)?;
    Ok((
        json!({
            "certificate": certificate,
            "principals": [cfg.principal],
            "valid_before": issued_at + cfg.validity_s,
            "key_id": key_id,
        }),
        key_id,
    ))
}

/// ssh-keygen must read the line as a plain public key, not a certificate.
fn accepted_key(path: &Path, cfg: &Config) -> Result<(), ApiError> {
    let out = run_bounded(
        Command::new("ssh-keygen").arg("-l").arg("-f").arg(path),
        keygen_timeout(cfg),
    )
    .map_err(|_| {
        ApiError::new(
            400,
            "invalid_public_key",
            "ssh-keygen does not read public_key as a key",
        )
    })?;
    if String::from_utf8_lossy(&out).contains("-CERT)") {
        return Err(ApiError::new(
            400,
            "certificate_not_key",
            "a certificate was sent; send the plain public key",
        ));
    }
    Ok(())
}

fn sign(cfg: &Config, key_path: &Path, key_id: &str, serial: u64) -> Result<String, ApiError> {
    let failed = |e: String| {
        eprintln!("mios-ssh-ca: signing failed: {e}");
        ApiError::new(500, "server_error", "the certificate could not be signed")
    };
    run_bounded(
        Command::new("ssh-keygen")
            .arg("-q")
            .arg("-s")
            .arg(&cfg.key_file)
            .args(["-I", key_id, "-n", &cfg.principal])
            .arg("-V")
            .arg(format!("+{}s", cfg.validity_s))
            .args(["-O", "clear", "-O", "permit-pty", "-z"])
            .arg(serial.to_string())
            .arg(key_path),
        keygen_timeout(cfg),
    )
    .map_err(failed)?;
    let cert_path = key_path.with_file_name("key-cert.pub");
    let cert = std::fs::read_to_string(&cert_path).map_err(|e| failed(e.to_string()))?;
    Ok(cert.trim().to_string())
}

fn respond(mut stream: TcpStream, status: u16, body: &Value, drain_limit: usize) {
    let reason = match status {
        200 => "OK",
        400 => "Bad Request",
        403 => "Forbidden",
        404 => "Not Found",
        405 => "Method Not Allowed",
        411 => "Length Required",
        413 => "Content Too Large",
        431 => "Request Header Fields Too Large",
        _ => "Internal Server Error",
    };
    let body = body.to_string();
    let _ = write!(
        stream,
        "HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
        body.len()
    );
    let _ = stream.flush();
    // Half-close, then drain what the peer is still sending (bounded): closing
    // with unread bytes makes the kernel answer RST, and a refused client
    // would lose the error object before reading it.
    let _ = stream.shutdown(std::net::Shutdown::Write);
    let _ = stream.set_read_timeout(Some(Duration::from_millis(250)));
    let mut sink = [0u8; 4096];
    let mut drained = 0usize;
    while drained < drain_limit {
        match stream.read(&mut sink) {
            Ok(0) | Err(_) => break,
            Ok(n) => drained += n,
        }
    }
}
