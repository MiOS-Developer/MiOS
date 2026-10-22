// AI-hint: Minimal HTTP/1.1-over-Unix-socket JSON client for the ventulusd API (no async runtime, no HTTP crate).
// AI-related: tools/native/mios-fanctl/src/main.rs, /run/ventulus/ventulus.sock
// AI-functions: Client::request, Client::get, Client::post, Client::delete, parse_response, decode_chunked

use serde_json::Value;
use std::io::{Read, Write};
use std::os::unix::net::UnixStream;
use std::path::PathBuf;
use std::time::Duration;

pub struct Client {
    socket: PathBuf,
    timeout: Duration,
}

impl Client {
    pub fn new(socket: &str, timeout_ms: u64) -> Self {
        Self {
            socket: PathBuf::from(socket),
            timeout: Duration::from_millis(timeout_ms.max(100)),
        }
    }

    pub fn request(&self, method: &str, path: &str, body: Option<&Value>) -> Result<(u16, Value), String> {
        let mut s = UnixStream::connect(&self.socket).map_err(|e| {
            format!(
                "cannot connect to {}: {e} (is ventulusd running? is this account in the wheel group?)",
                self.socket.display()
            )
        })?;
        s.set_read_timeout(Some(self.timeout)).map_err(|e| e.to_string())?;
        s.set_write_timeout(Some(self.timeout)).map_err(|e| e.to_string())?;
        let payload = body.map(|b| b.to_string()).unwrap_or_default();
        let ctype = if body.is_some() { "Content-Type: application/json\r\n" } else { "" };
        let req = format!(
            "{method} {path} HTTP/1.1\r\nHost: localhost\r\nUser-Agent: mios-fanctl/{}\r\nAccept: application/json\r\nConnection: close\r\n{ctype}Content-Length: {}\r\n\r\n{payload}",
            env!("CARGO_PKG_VERSION"),
            payload.len()
        );
        s.write_all(req.as_bytes()).map_err(|e| format!("write {path}: {e}"))?;
        let mut buf = Vec::new();
        s.read_to_end(&mut buf).map_err(|e| format!("read {path}: {e}"))?;
        parse_response(&buf)
    }

    fn checked(&self, method: &str, path: &str, body: Option<&Value>) -> Result<Value, String> {
        let (status, v) = self.request(method, path, body)?;
        if (200..300).contains(&status) {
            Ok(v)
        } else {
            let msg = v
                .pointer("/error/message")
                .and_then(Value::as_str)
                .map(str::to_string)
                .unwrap_or_else(|| v.to_string());
            let detail = v
                .pointer("/error/details/field_violations")
                .and_then(Value::as_array)
                .map(|a| {
                    a.iter()
                        .filter_map(|x| x.get("description").and_then(Value::as_str))
                        .collect::<Vec<_>>()
                        .join("; ")
                })
                .filter(|s| !s.is_empty())
                .map(|s| format!(" ({s})"))
                .unwrap_or_default();
            Err(format!("{method} {path}: HTTP {status}: {msg}{detail}"))
        }
    }

    pub fn get(&self, path: &str) -> Result<Value, String> {
        self.checked("GET", path, None)
    }

    pub fn post(&self, path: &str, body: &Value) -> Result<Value, String> {
        self.checked("POST", path, Some(body))
    }

    pub fn delete(&self, path: &str, body: &Value) -> Result<Value, String> {
        self.checked("DELETE", path, Some(body))
    }
}

/// Parse a complete HTTP/1.1 response (connection closed by the server).
pub fn parse_response(buf: &[u8]) -> Result<(u16, Value), String> {
    let split = buf
        .windows(4)
        .position(|w| w == b"\r\n\r\n")
        .ok_or("malformed HTTP response: no header terminator")?;
    let head = std::str::from_utf8(&buf[..split]).map_err(|_| "non-UTF-8 HTTP headers")?;
    let mut lines = head.split("\r\n");
    let status_line = lines.next().unwrap_or_default();
    let status: u16 = status_line
        .split_whitespace()
        .nth(1)
        .and_then(|s| s.parse().ok())
        .ok_or_else(|| format!("malformed status line: {status_line:?}"))?;
    let mut chunked = false;
    let mut length: Option<usize> = None;
    for l in lines {
        if let Some((k, v)) = l.split_once(':') {
            let k = k.trim().to_ascii_lowercase();
            let v = v.trim();
            if k == "transfer-encoding" && v.to_ascii_lowercase().contains("chunked") {
                chunked = true;
            } else if k == "content-length" {
                length = v.parse().ok();
            }
        }
    }
    let raw = &buf[split + 4..];
    let body: Vec<u8> = if chunked {
        decode_chunked(raw)?
    } else if let Some(n) = length {
        raw.get(..n).ok_or("truncated HTTP body")?.to_vec()
    } else {
        raw.to_vec()
    };
    if body.iter().all(u8::is_ascii_whitespace) {
        return Ok((status, Value::Null));
    }
    let v = serde_json::from_slice(&body).map_err(|e| format!("invalid JSON body (HTTP {status}): {e}"))?;
    Ok((status, v))
}

pub fn decode_chunked(mut b: &[u8]) -> Result<Vec<u8>, String> {
    let mut out = Vec::new();
    loop {
        let eol = b.windows(2).position(|w| w == b"\r\n").ok_or("malformed chunk header")?;
        let size_s = std::str::from_utf8(&b[..eol]).map_err(|_| "bad chunk size")?;
        let size_s = size_s.split(';').next().unwrap_or("").trim();
        let size = usize::from_str_radix(size_s, 16).map_err(|_| format!("bad chunk size {size_s:?}"))?;
        b = &b[eol + 2..];
        if size == 0 {
            return Ok(out);
        }
        let chunk = b.get(..size).ok_or("truncated chunk")?;
        out.extend_from_slice(chunk);
        b = b.get(size + 2..).ok_or("truncated chunk trailer")?;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_content_length() {
        let r = b"HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: 11\r\n\r\n{\"a\":1}    ";
        let (s, v) = parse_response(r).unwrap();
        assert_eq!(s, 200);
        assert_eq!(v["a"], 1);
    }

    #[test]
    fn parses_chunked() {
        let r = b"HTTP/1.1 400 Bad Request\r\nTransfer-Encoding: chunked\r\n\r\n4\r\n{\"x\"\r\n5\r\n:true\r\n1\r\n}\r\n0\r\n\r\n";
        let (s, v) = parse_response(r).unwrap();
        assert_eq!(s, 400);
        assert_eq!(v["x"], true);
    }

    #[test]
    fn empty_body_is_null() {
        let (s, v) = parse_response(b"HTTP/1.1 204 No Content\r\n\r\n").unwrap();
        assert_eq!(s, 204);
        assert!(v.is_null());
    }

    #[test]
    fn rejects_garbage() {
        assert!(parse_response(b"nonsense").is_err());
        assert!(decode_chunked(b"zz\r\n").is_err());
    }
}
