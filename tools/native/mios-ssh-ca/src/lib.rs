// AI-hint: Policy, bounded HTTP parsing, SSOT config and ssh-keygen signing for the MiOS user SSH CA signer.
// AI-related: tools/native/mios-ssh-ca/src/main.rs, usr/share/mios/mios.toml [ssh.ca], usr/lib/systemd/system/mios-ssh-ca.service
// AI-functions: load_config, glob_match, first_label, valid_label, parse_whois, parse_self, authorize, read_head, read_body, parse_request_body, check_key_line, next_serial, run_bounded, Tailscale, ApiError

#![forbid(unsafe_code)]

use serde_json::{json, Value};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

/// The one route this signer serves.
pub const ROUTE: &str = "/v1/ssh/certificates";

/// The SSOT values the signer runs on. Every field comes from the vendor and
/// host tiers ([ssh.ca], [ssh].user, [ports].ssh_ca); nothing is compiled in.
#[derive(Debug, Clone)]
pub struct Config {
    pub enabled: bool,
    pub signer: String,
    pub key_file: PathBuf,
    pub validity_s: u64,
    pub hostname_glob: String,
    pub trusted_tags: Vec<String>,
    pub principal: String,
    pub port: u16,
    pub limits: Limits,
}

#[derive(Debug, Clone, Copy)]
pub struct Limits {
    pub max_header_bytes: usize,
    pub max_body_bytes: usize,
    pub read_timeout: Duration,
}

fn table<'a>(doc: &'a toml::Value, path: &str) -> Result<&'a toml::Value, String> {
    path.split('.').try_fold(doc, |v, k| {
        v.get(k)
            .ok_or_else(|| format!("[{path}] is not declared in the SSOT"))
    })
}

fn req<'a>(doc: &'a toml::Value, path: &str, key: &str) -> Result<&'a toml::Value, String> {
    table(doc, path)?
        .get(key)
        .ok_or_else(|| format!("[{path}].{key} is not declared in the SSOT"))
}

fn req_str(doc: &toml::Value, path: &str, key: &str) -> Result<String, String> {
    req(doc, path, key)?
        .as_str()
        .map(str::to_string)
        .ok_or_else(|| format!("[{path}].{key} must be a string"))
}

fn req_pos(doc: &toml::Value, path: &str, key: &str) -> Result<u64, String> {
    req(doc, path, key)?
        .as_integer()
        .filter(|n| *n > 0)
        .map(|n| n as u64)
        .ok_or_else(|| format!("[{path}].{key} must be a positive integer"))
}

/// Build the config from an already-merged SSOT document. `env_port` is the
/// process environment's MIOS_PORTS_SSH_CA, which wins as it does for every
/// resolver consumer; otherwise [ports].ssh_ca plus the stack offset.
pub fn config_from(doc: &toml::Value, env_port: Option<String>) -> Result<Config, String> {
    let ca = "ssh.ca";
    let enabled = req(doc, ca, "enabled")?
        .as_bool()
        .ok_or("[ssh.ca].enabled must be a boolean")?;
    let trusted_tags = req(doc, ca, "trusted_tags")?
        .as_array()
        .ok_or("[ssh.ca].trusted_tags must be an array")?
        .iter()
        .map(|t| {
            t.as_str()
                .filter(|s| s.starts_with("tag:") && s.len() > 4)
                .map(str::to_string)
                .ok_or("[ssh.ca].trusted_tags entries must be \"tag:<name>\" strings")
        })
        .collect::<Result<Vec<_>, _>>()?;
    let key_file = PathBuf::from(req_str(doc, ca, "key_file")?);
    if !key_file.is_absolute() {
        return Err("[ssh.ca].key_file must be an absolute path".into());
    }
    let port = match env_port.filter(|v| !v.is_empty()) {
        Some(raw) => raw
            .parse::<u16>()
            .map_err(|_| format!("MIOS_PORTS_SSH_CA={raw} is not a port"))?,
        None => {
            let base = req(doc, "ports", "ssh_ca")?
                .as_integer()
                .ok_or("[ports].ssh_ca must be an integer")?;
            let port = base + mios_resolver::stack_offset_of(doc);
            u16::try_from(port).map_err(|_| format!("[ports].ssh_ca resolves to {port}"))?
        }
    };
    let principal = req_str(doc, "ssh", "user")?;
    if !valid_label(&principal) {
        return Err("[ssh].user must be a plain login name".into());
    }
    let hostname_glob = req_str(doc, ca, "hostname_glob")?;
    if hostname_glob.is_empty() {
        return Err("[ssh.ca].hostname_glob is empty".into());
    }
    Ok(Config {
        enabled,
        signer: req_str(doc, ca, "signer")?,
        key_file,
        validity_s: req_pos(doc, ca, "validity_s")?,
        hostname_glob,
        trusted_tags,
        principal,
        port,
        limits: Limits {
            max_header_bytes: req_pos(doc, ca, "max_header_bytes")? as usize,
            max_body_bytes: req_pos(doc, ca, "max_body_bytes")? as usize,
            read_timeout: Duration::from_secs(req_pos(doc, ca, "read_timeout_s")?),
        },
    })
}

/// Vendor and host tiers only: the signer's policy is a system trust decision,
/// so no user tier (not even root's) takes part.
pub fn load_config(root: Option<&Path>) -> Result<Config, String> {
    let doc = mios_resolver::resolve_below_user(root).map_err(|e| e.to_string())?;
    config_from(&doc, std::env::var("MIOS_PORTS_SSH_CA").ok())
}

/// Case-insensitive glob with `*` (any run) and `?` (one character).
pub fn glob_match(pattern: &str, text: &str) -> bool {
    let p: Vec<char> = pattern.to_ascii_lowercase().chars().collect();
    let t: Vec<char> = text.to_ascii_lowercase().chars().collect();
    let (mut pi, mut ti) = (0, 0);
    let (mut star, mut mark) = (None, 0);
    while ti < t.len() {
        if pi < p.len() && (p[pi] == '?' || p[pi] == t[ti]) {
            pi += 1;
            ti += 1;
        } else if pi < p.len() && p[pi] == '*' {
            star = Some(pi);
            mark = ti;
            pi += 1;
        } else if let Some(s) = star {
            pi = s + 1;
            mark += 1;
            ti = mark;
        } else {
            return false;
        }
    }
    p[pi..].iter().all(|c| *c == '*')
}

/// The first DNS label of a MagicDNS name ("mios-cloud-1.tail.ts.net." -> "mios-cloud-1").
pub fn first_label(fqdn: &str) -> String {
    fqdn.trim_end_matches('.')
        .split('.')
        .next()
        .unwrap_or("")
        .to_ascii_lowercase()
}

/// A DNS label safe to put in a certificate key id and a log line.
pub fn valid_label(label: &str) -> bool {
    !label.is_empty()
        && label.len() <= 63
        && !label.starts_with('-')
        && label
            .bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
}

/// The caller as `tailscale whois --json` describes it.
#[derive(Debug, Clone, PartialEq)]
pub struct Peer {
    pub name: String,
    pub user_id: i64,
    pub tags: Vec<String>,
    pub shared: bool,
}

pub fn parse_whois(doc: &Value) -> Result<Peer, String> {
    let node = doc.get("Node").ok_or("whois has no Node")?;
    let name = first_label(node.get("Name").and_then(Value::as_str).unwrap_or(""));
    let user_id = node
        .get("User")
        .and_then(Value::as_i64)
        .ok_or("whois Node has no User")?;
    let tags = node
        .get("Tags")
        .and_then(Value::as_array)
        .map(|a| {
            a.iter()
                .filter_map(Value::as_str)
                .map(str::to_string)
                .collect()
        })
        .unwrap_or_default();
    let shared = node.get("Sharer").and_then(Value::as_i64).unwrap_or(0) != 0;
    Ok(Peer {
        name,
        user_id,
        tags,
        shared,
    })
}

/// This node's (MagicDNS first label, tailnet user id) from `tailscale status --json`.
pub fn parse_self(doc: &Value) -> Result<(String, i64), String> {
    let me = doc.get("Self").ok_or("status has no Self")?;
    let dns = me.get("DNSName").and_then(Value::as_str).unwrap_or("");
    let name = if dns.is_empty() {
        me.get("HostName")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_ascii_lowercase()
    } else {
        first_label(dns)
    };
    let user = me
        .get("UserID")
        .and_then(Value::as_i64)
        .ok_or("status Self has no UserID")?;
    Ok((name, user))
}

/// Why a node gets no certificate. The code is the OpenAI error object's `code`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Refusal {
    InvalidNodeName,
    SharedNode,
    HostnameNotAllowed,
    OwnerNotTrusted,
}

impl Refusal {
    pub fn code(self) -> &'static str {
        match self {
            Refusal::InvalidNodeName => "invalid_node_name",
            Refusal::SharedNode => "shared_node",
            Refusal::HostnameNotAllowed => "hostname_not_allowed",
            Refusal::OwnerNotTrusted => "owner_not_trusted",
        }
    }
}

/// The issuance policy: the node's name matches `glob` AND (it is an untagged
/// node of the signer's own user OR it carries a trusted tag). A node shared in
/// from another tailnet is refused outright: its tags and owner are that
/// tailnet's, not ours.
pub fn authorize(
    peer: &Peer,
    self_user: i64,
    glob: &str,
    trusted: &[String],
) -> Result<(), Refusal> {
    if !valid_label(&peer.name) {
        return Err(Refusal::InvalidNodeName);
    }
    if peer.shared {
        return Err(Refusal::SharedNode);
    }
    if !glob_match(glob, &peer.name) {
        return Err(Refusal::HostnameNotAllowed);
    }
    let owned = peer.tags.is_empty() && peer.user_id == self_user;
    let tagged = peer.tags.iter().any(|t| trusted.contains(t));
    if owned || tagged {
        Ok(())
    } else {
        Err(Refusal::OwnerNotTrusted)
    }
}

/// An error answered as the OpenAI error object.
#[derive(Debug, Clone, PartialEq)]
pub struct ApiError {
    pub status: u16,
    pub kind: &'static str,
    pub code: &'static str,
    pub message: String,
}

impl ApiError {
    pub fn new(status: u16, code: &'static str, message: impl Into<String>) -> Self {
        let kind = match status {
            400 | 411 | 413 | 431 => "invalid_request_error",
            401 | 403 => "permission_error",
            404 | 405 => "not_found_error",
            _ => "server_error",
        };
        ApiError {
            status,
            kind,
            code,
            message: message.into(),
        }
    }

    pub fn body(&self) -> Value {
        json!({"error": {"message": self.message, "type": self.kind, "code": self.code}})
    }
}

impl From<Refusal> for ApiError {
    fn from(r: Refusal) -> Self {
        let message = match r {
            Refusal::InvalidNodeName => "the calling node's name is not a plain DNS label",
            Refusal::SharedNode => {
                "nodes shared in from another tailnet are not issued certificates"
            }
            Refusal::HostnameNotAllowed => {
                "the calling node's name does not match [ssh.ca].hostname_glob"
            }
            Refusal::OwnerNotTrusted => {
                "the calling node is neither this tailnet user's own nor tagged with a trusted tag"
            }
        };
        ApiError::new(403, r.code(), message)
    }
}

/// A parsed request head plus any body bytes read past it.
#[derive(Debug)]
pub struct Head {
    pub method: String,
    pub path: String,
    pub content_length: Option<usize>,
    pub chunked: bool,
    pub rest: Vec<u8>,
}

/// Read up to the blank line ending the head, never past `max` bytes.
pub fn read_head<R: Read>(r: &mut R, max: usize) -> Result<Head, ApiError> {
    let mut buf = Vec::with_capacity(1024.min(max));
    let mut chunk = [0u8; 1024];
    let end = loop {
        if let Some(i) = buf.windows(4).position(|w| w == b"\r\n\r\n") {
            break i;
        }
        if buf.len() >= max {
            return Err(ApiError::new(
                431,
                "header_too_large",
                "request head exceeds [ssh.ca].max_header_bytes",
            ));
        }
        let want = chunk.len().min(max + 4 - buf.len());
        let n = r.read(&mut chunk[..want]).map_err(io_error)?;
        if n == 0 {
            return Err(ApiError::new(
                400,
                "incomplete_request",
                "connection closed before the request head ended",
            ));
        }
        buf.extend_from_slice(&chunk[..n]);
    };
    if end > max {
        return Err(ApiError::new(
            431,
            "header_too_large",
            "request head exceeds [ssh.ca].max_header_bytes",
        ));
    }
    let text = std::str::from_utf8(&buf[..end])
        .map_err(|_| ApiError::new(400, "invalid_request", "request head is not UTF-8"))?;
    let mut lines = text.split("\r\n");
    let start = lines.next().unwrap_or("");
    let mut parts = start.split(' ');
    let (method, path, version) = (parts.next(), parts.next(), parts.next());
    let (Some(method), Some(path), Some(version), None) = (method, path, version, parts.next())
    else {
        return Err(ApiError::new(
            400,
            "invalid_request",
            "malformed request line",
        ));
    };
    if !version.starts_with("HTTP/1.") {
        return Err(ApiError::new(
            400,
            "invalid_request",
            "only HTTP/1.x is served",
        ));
    }
    let mut content_length = None;
    let mut chunked = false;
    for line in lines {
        let (name, value) = line
            .split_once(':')
            .ok_or_else(|| ApiError::new(400, "invalid_request", "malformed header line"))?;
        let value = value.trim();
        if name.eq_ignore_ascii_case("content-length") {
            let n = value
                .parse::<usize>()
                .map_err(|_| ApiError::new(400, "invalid_request", "invalid Content-Length"))?;
            if content_length.is_some_and(|c| c != n) {
                return Err(ApiError::new(
                    400,
                    "invalid_request",
                    "conflicting Content-Length headers",
                ));
            }
            content_length = Some(n);
        } else if name.eq_ignore_ascii_case("transfer-encoding") {
            chunked = true;
        }
    }
    Ok(Head {
        method: method.to_string(),
        path: path.to_string(),
        content_length,
        chunked,
        rest: buf[end + 4..].to_vec(),
    })
}

/// Read exactly Content-Length body bytes, refusing anything over `max`
/// before reading it.
pub fn read_body<R: Read>(r: &mut R, head: &Head, max: usize) -> Result<Vec<u8>, ApiError> {
    if head.chunked {
        return Err(ApiError::new(
            411,
            "length_required",
            "chunked bodies are not accepted; send Content-Length",
        ));
    }
    let len = head
        .content_length
        .ok_or_else(|| ApiError::new(411, "length_required", "Content-Length is required"))?;
    if len > max || head.rest.len() > max {
        return Err(ApiError::new(
            413,
            "body_too_large",
            "request body exceeds [ssh.ca].max_body_bytes",
        ));
    }
    if head.rest.len() > len {
        return Err(ApiError::new(
            400,
            "invalid_request",
            "more bytes than Content-Length",
        ));
    }
    let mut body = head.rest.clone();
    body.resize(len, 0);
    r.read_exact(&mut body[head.rest.len()..])
        .map_err(io_error)?;
    Ok(body)
}

fn io_error(e: std::io::Error) -> ApiError {
    match e.kind() {
        std::io::ErrorKind::WouldBlock | std::io::ErrorKind::TimedOut => ApiError::new(
            400,
            "request_timeout",
            "the request was not received within [ssh.ca].read_timeout_s",
        ),
        std::io::ErrorKind::UnexpectedEof => ApiError::new(
            400,
            "incomplete_request",
            "connection closed before the body ended",
        ),
        _ => ApiError::new(400, "invalid_request", "the request could not be read"),
    }
}

/// `{"public_key": "<one key line>"}` -> the key line. Unknown fields are refused.
pub fn parse_request_body(body: &[u8]) -> Result<String, ApiError> {
    let doc: Value = serde_json::from_slice(body)
        .map_err(|_| ApiError::new(400, "invalid_json", "the body is not a JSON object"))?;
    let obj = doc
        .as_object()
        .ok_or_else(|| ApiError::new(400, "invalid_json", "the body is not a JSON object"))?;
    if obj.keys().any(|k| k != "public_key") {
        return Err(ApiError::new(
            400,
            "unknown_field",
            "only public_key is accepted",
        ));
    }
    let key = obj
        .get("public_key")
        .and_then(Value::as_str)
        .ok_or_else(|| ApiError::new(400, "missing_public_key", "public_key must be a string"))?;
    check_key_line(key)?;
    Ok(key.trim().to_string())
}

/// One OpenSSH public key line, never a certificate. ssh-keygen -l still has
/// to accept it before it is signed.
pub fn check_key_line(line: &str) -> Result<(), ApiError> {
    let line = line.trim();
    let bad = |m: &str| Err(ApiError::new(400, "invalid_public_key", m.to_string()));
    if line.is_empty() {
        return bad("public_key is empty");
    }
    if line.chars().any(|c| c.is_control()) {
        return bad("public_key must be exactly one line");
    }
    let mut fields = line.split_ascii_whitespace();
    let kind = fields.next().unwrap_or("");
    let blob = fields.next().unwrap_or("");
    if kind.contains("-cert-") {
        return Err(ApiError::new(
            400,
            "certificate_not_key",
            "a certificate was sent; send the plain public key",
        ));
    }
    if !(kind.starts_with("ssh-") || kind.starts_with("ecdsa-") || kind.starts_with("sk-")) {
        return bad("public_key is not an OpenSSH public key line");
    }
    if blob.is_empty()
        || !blob
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'+' || b == b'/' || b == b'=')
    {
        return bad("public_key has no base64 key blob");
    }
    Ok(())
}

/// Reserve the next certificate serial: read, increment and durably write it
/// back BEFORE signing, so a crash can skip a serial but never reuse one.
pub fn next_serial(state_dir: &Path) -> Result<u64, String> {
    let path = state_dir.join("serial");
    let last = match std::fs::read_to_string(&path) {
        Ok(text) => text.trim().parse::<u64>().map_err(|_| {
            format!(
                "{} is not a serial; refusing to sign rather than reuse one",
                path.display()
            )
        })?,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => 0,
        Err(e) => return Err(format!("{}: {e}", path.display())),
    };
    let next = last.checked_add(1).ok_or("serial space exhausted")?;
    let tmp = state_dir.join("serial.tmp");
    {
        let mut f = std::fs::File::create(&tmp).map_err(|e| format!("{}: {e}", tmp.display()))?;
        writeln!(f, "{next}").map_err(|e| e.to_string())?;
        f.sync_all().map_err(|e| e.to_string())?;
    }
    std::fs::rename(&tmp, &path).map_err(|e| format!("{}: {e}", path.display()))?;
    if let Ok(dir) = std::fs::File::open(state_dir) {
        let _ = dir.sync_all();
    }
    Ok(next)
}

/// Run a command with a wall-clock bound; stdout on success, a short reason otherwise.
pub fn run_bounded(cmd: &mut Command, limit: Duration) -> Result<Vec<u8>, String> {
    let mut child = cmd
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|e| format!("{:?}: {e}", cmd.get_program()))?;
    let mut out = child.stdout.take().ok_or("no stdout")?;
    let reader = std::thread::spawn(move || {
        let mut buf = Vec::new();
        let _ = out.read_to_end(&mut buf);
        buf
    });
    let deadline = Instant::now() + limit;
    let status = loop {
        if let Some(status) = child.try_wait().map_err(|e| e.to_string())? {
            break status;
        }
        if Instant::now() >= deadline {
            let _ = child.kill();
            let _ = child.wait();
            return Err(format!("{:?} timed out", cmd.get_program()));
        }
        std::thread::sleep(Duration::from_millis(20));
    };
    let buf = reader.join().map_err(|_| "reader panicked")?;
    if status.success() {
        Ok(buf)
    } else {
        Err(format!("{:?} exited {status}", cmd.get_program()))
    }
}

/// The tailscale CLI, found on PATH like every other MiOS tailscale consumer.
pub struct Tailscale {
    pub timeout: Duration,
}

impl Tailscale {
    fn json(&self, args: &[&str]) -> Result<Value, String> {
        let out = run_bounded(Command::new("tailscale").args(args), self.timeout)?;
        serde_json::from_slice(&out).map_err(|e| format!("tailscale {}: {e}", args.join(" ")))
    }

    pub fn ipv4(&self) -> Result<std::net::Ipv4Addr, String> {
        let out = run_bounded(Command::new("tailscale").args(["ip", "-4"]), self.timeout)?;
        String::from_utf8_lossy(&out)
            .lines()
            .next()
            .unwrap_or("")
            .trim()
            .parse()
            .map_err(|_| "tailscale ip -4 printed no IPv4 address".to_string())
    }

    pub fn status(&self) -> Result<Value, String> {
        self.json(&["status", "--json"])
    }

    pub fn whois(&self, ip: &str) -> Result<Value, String> {
        self.json(&["whois", "--json", ip])
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;

    fn peer(name: &str, user: i64, tags: &[&str]) -> Peer {
        Peer {
            name: name.into(),
            user_id: user,
            tags: tags.iter().map(|t| t.to_string()).collect(),
            shared: false,
        }
    }

    fn trusted() -> Vec<String> {
        vec!["tag:mios".into()]
    }

    #[test]
    fn glob_matches_prefix_star_and_question() {
        assert!(glob_match("mios-cloud*", "mios-cloud"));
        assert!(glob_match("mios-cloud*", "mios-cloud-7"));
        assert!(glob_match("mios-cloud*", "MIOS-Cloud-7"));
        assert!(glob_match("m?os-*-1", "mios-cloud-1"));
        assert!(!glob_match("mios-cloud*", "mios-clou"));
        assert!(!glob_match("mios-cloud*", "x-mios-cloud"));
        assert!(!glob_match("mios-cloud*", "mios-blade"));
        assert!(!glob_match("mios-cloud", "mios-cloud-1"));
    }

    #[test]
    fn owner_or_tag_is_required() {
        let g = "mios-cloud*";
        assert_eq!(
            authorize(&peer("mios-cloud-1", 7, &[]), 7, g, &trusted()),
            Ok(())
        );
        assert_eq!(
            authorize(&peer("mios-cloud-2", 99, &["tag:mios"]), 7, g, &trusted()),
            Ok(())
        );
        assert_eq!(
            authorize(&peer("mios-cloud-3", 99, &[]), 7, g, &trusted()),
            Err(Refusal::OwnerNotTrusted)
        );
        assert_eq!(
            authorize(&peer("mios-cloud-4", 99, &["tag:other"]), 7, g, &trusted()),
            Err(Refusal::OwnerNotTrusted)
        );
    }

    #[test]
    fn a_tagged_node_does_not_pass_as_owned() {
        // A signer that is itself tagged shares its UserID with every tagged
        // node; an untrusted tag must not ride that equality.
        assert_eq!(
            authorize(
                &peer("mios-cloud-5", 7, &["tag:other"]),
                7,
                "mios-cloud*",
                &trusted()
            ),
            Err(Refusal::OwnerNotTrusted)
        );
    }

    #[test]
    fn wrong_hostname_is_refused_even_when_trusted() {
        assert_eq!(
            authorize(&peer("mios-blade", 7, &[]), 7, "mios-cloud*", &trusted()),
            Err(Refusal::HostnameNotAllowed)
        );
        assert_eq!(
            authorize(
                &peer("cloud-1", 99, &["tag:mios"]),
                7,
                "mios-cloud*",
                &trusted()
            ),
            Err(Refusal::HostnameNotAllowed)
        );
    }

    #[test]
    fn shared_and_odd_names_are_refused() {
        let mut p = peer("mios-cloud-1", 7, &["tag:mios"]);
        p.shared = true;
        assert_eq!(
            authorize(&p, 7, "mios-cloud*", &trusted()),
            Err(Refusal::SharedNode)
        );
        assert_eq!(
            authorize(&peer("", 7, &[]), 7, "*", &trusted()),
            Err(Refusal::InvalidNodeName)
        );
        assert_eq!(
            authorize(&peer("mios cloud", 7, &[]), 7, "*", &trusted()),
            Err(Refusal::InvalidNodeName)
        );
    }

    #[test]
    fn whois_and_status_parse() {
        let w = json!({"Node": {"Name": "Mios-Cloud-1.tail1.ts.net.", "User": 42, "Tags": ["tag:mios"]}});
        assert_eq!(
            parse_whois(&w).unwrap(),
            peer("mios-cloud-1", 42, &["tag:mios"])
        );
        let shared = json!({"Node": {"Name": "mios-cloud-1.x.", "User": 42, "Sharer": 5}});
        assert!(parse_whois(&shared).unwrap().shared);
        assert!(parse_whois(&json!({"Node": {"Name": "a."}})).is_err());
        let s =
            json!({"Self": {"DNSName": "mios.tail1.ts.net.", "HostName": "Other", "UserID": 42}});
        assert_eq!(parse_self(&s).unwrap(), ("mios".to_string(), 42));
    }

    fn head(raw: &str, max: usize) -> Result<Head, ApiError> {
        read_head(&mut Cursor::new(raw.as_bytes().to_vec()), max)
    }

    #[test]
    fn head_limit_is_enforced() {
        let raw = format!(
            "POST {ROUTE} HTTP/1.1\r\nX-Pad: {}\r\n\r\n",
            "a".repeat(400)
        );
        assert_eq!(head(&raw, 256).unwrap_err().code, "header_too_large");
        assert!(head(&raw, 1024).is_ok());
        // A head that never ends stops at the limit instead of reading forever.
        let endless = "GET / HTTP/1.1\r\n".to_string() + &"X: y\r\n".repeat(200);
        assert_eq!(head(&endless, 256).unwrap_err().code, "header_too_large");
    }

    #[test]
    fn head_parses_and_rejects_garbage() {
        let h = head(
            &format!("POST {ROUTE} HTTP/1.1\r\nContent-Length: 3\r\n\r\nabc"),
            1024,
        )
        .unwrap();
        assert_eq!(
            (h.method.as_str(), h.path.as_str(), h.content_length),
            ("POST", ROUTE, Some(3))
        );
        assert_eq!(h.rest, b"abc");
        assert_eq!(
            head("garbage\r\n\r\n", 1024).unwrap_err().code,
            "invalid_request"
        );
        assert_eq!(
            head(
                "POST / HTTP/1.1\r\nContent-Length: 1\r\nContent-Length: 2\r\n\r\n",
                1024
            )
            .unwrap_err()
            .code,
            "invalid_request"
        );
    }

    #[test]
    fn body_limit_is_enforced_before_reading() {
        let h = head("POST / HTTP/1.1\r\nContent-Length: 5000\r\n\r\n", 1024).unwrap();
        assert_eq!(
            read_body(&mut Cursor::new(vec![]), &h, 4096)
                .unwrap_err()
                .code,
            "body_too_large"
        );
        let h = head("POST / HTTP/1.1\r\nContent-Length: 4\r\n\r\nab", 1024).unwrap();
        assert_eq!(
            read_body(&mut Cursor::new(b"cd".to_vec()), &h, 4096).unwrap(),
            b"abcd"
        );
        let h = head("POST / HTTP/1.1\r\n\r\n", 1024).unwrap();
        assert_eq!(
            read_body(&mut Cursor::new(vec![]), &h, 4096)
                .unwrap_err()
                .code,
            "length_required"
        );
        let h = head(
            "POST / HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n",
            1024,
        )
        .unwrap();
        assert_eq!(
            read_body(&mut Cursor::new(vec![]), &h, 4096)
                .unwrap_err()
                .status,
            411
        );
        let h = head("POST / HTTP/1.1\r\nContent-Length: 9\r\n\r\nab", 1024).unwrap();
        assert_eq!(
            read_body(&mut Cursor::new(vec![]), &h, 4096)
                .unwrap_err()
                .code,
            "incomplete_request"
        );
    }

    #[test]
    fn request_body_shape() {
        let key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAbc+/= cloud";
        assert_eq!(
            parse_request_body(json!({"public_key": key}).to_string().as_bytes()).unwrap(),
            key
        );
        let code = |b: Value| {
            parse_request_body(b.to_string().as_bytes())
                .unwrap_err()
                .code
        };
        assert_eq!(code(json!({"public_key": key, "x": 1})), "unknown_field");
        assert_eq!(code(json!({"public_key": 5})), "missing_public_key");
        assert_eq!(code(json!([key])), "invalid_json");
        assert_eq!(
            code(json!({"public_key": format!("{key}\n{key}")})),
            "invalid_public_key"
        );
        assert_eq!(
            code(json!({"public_key": "ssh-ed25519-cert-v01@openssh.com AAAAIGNlcnQ="})),
            "certificate_not_key"
        );
        assert_eq!(
            code(json!({"public_key": "hello world"})),
            "invalid_public_key"
        );
        assert_eq!(
            code(json!({"public_key": "ssh-ed25519 not*base64"})),
            "invalid_public_key"
        );
        assert!(parse_request_body(b"{").is_err());
    }

    #[test]
    fn errors_use_the_openai_shape() {
        let e: ApiError = Refusal::OwnerNotTrusted.into();
        let b = e.body();
        assert_eq!(e.status, 403);
        assert_eq!(b["error"]["type"], "permission_error");
        assert_eq!(b["error"]["code"], "owner_not_trusted");
        assert!(b["error"]["message"].is_string());
    }

    #[test]
    fn serial_only_increases_and_garbage_stops_signing() {
        let dir = tempfile::tempdir().unwrap();
        assert_eq!(next_serial(dir.path()).unwrap(), 1);
        assert_eq!(next_serial(dir.path()).unwrap(), 2);
        std::fs::write(dir.path().join("serial"), "41\n").unwrap();
        assert_eq!(next_serial(dir.path()).unwrap(), 42);
        std::fs::write(dir.path().join("serial"), "oops").unwrap();
        assert!(next_serial(dir.path()).is_err());
    }

    fn ssot() -> toml::Value {
        toml::from_str(
            r#"
            [ports]
            stack_id = 1
            ssh_ca = 8130
            [ssh]
            user = "agent"
            [ssh.ca]
            enabled = true
            signer = "mios"
            key_file = "/var/lib/mios/ssh-ca/mios-user-ca"
            validity_s = 28800
            hostname_glob = "mios-cloud*"
            trusted_tags = ["tag:mios"]
            max_header_bytes = 8192
            max_body_bytes = 16384
            read_timeout_s = 10
            "#,
        )
        .unwrap()
    }

    #[test]
    fn vendor_ssot_is_the_decided_policy() {
        let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../..");
        let doc = mios_resolver::resolve_vendor_projection(&root).unwrap();
        let c = config_from(&doc, None).unwrap();
        assert!(!c.enabled, "the vendor tier must not turn the CA on");
        assert_eq!(c.signer, "mios");
        assert_eq!(c.port, 8130);
        assert_eq!(c.principal, "agent");
        assert_eq!(c.validity_s, 8 * 3600);
        assert_eq!(c.hostname_glob, "mios-cloud*");
        assert_eq!(c.trusted_tags, vec!["tag:mios".to_string()]);
        assert_eq!(c.key_file, Path::new("/var/lib/mios/ssh-ca/mios-user-ca"));
    }

    #[test]
    fn config_reads_every_value_from_the_ssot() {
        let c = config_from(&ssot(), None).unwrap();
        assert_eq!(c.port, 18130);
        assert_eq!(c.principal, "agent");
        assert_eq!(c.validity_s, 28800);
        assert_eq!(c.limits.max_body_bytes, 16384);
        assert_eq!(
            config_from(&ssot(), Some("9000".into())).unwrap().port,
            9000
        );
        for key in [
            "key_file",
            "validity_s",
            "max_body_bytes",
            "trusted_tags",
            "hostname_glob",
        ] {
            let mut doc = ssot();
            doc["ssh"]["ca"].as_table_mut().unwrap().remove(key);
            assert!(config_from(&doc, None).unwrap_err().contains(key), "{key}");
        }
        let mut doc = ssot();
        doc["ssh"]["ca"]["trusted_tags"] = toml::Value::Array(vec!["mios".into()]);
        assert!(config_from(&doc, None).is_err());
    }
}
