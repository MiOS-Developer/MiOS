//! Client of the display service: `mios-rdp-rfb-bridge --display-service`, run as a child with
//! JSON lines on stdin/stdout. It owns the remote output policy the RFB path already proved
//! (headless output, attach below the primary, [desktop.scaling] scale, refresh pacing, workspace
//! rule, re-apply after a Hyprland config reload, cursor hand-back, device rules), so the native
//! server and the xrdp fallback share one implementation.
#![forbid(unsafe_code)]

use std::collections::HashMap;
use std::io::{BufRead, BufReader, Write};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::mpsc::{Sender, channel};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use anyhow::{Context as _, Result, anyhow, bail};
use serde_json::{Value, json};
use tracing::{info, warn};

pub struct DisplayClient {
    stdin: Mutex<Option<ChildStdin>>,
    child: Mutex<Option<Child>>,
    pending: Arc<Mutex<HashMap<u64, Sender<Value>>>>,
    next: AtomicU64,
}

#[derive(Debug, Clone, Default, serde::Serialize)]
pub struct Applied {
    pub output: String,
    pub width: u32,
    pub height: u32,
    pub scale: f64,
    pub x: i64,
    pub y: i64,
    pub hz: u32,
}

impl Applied {
    fn from(v: &Value) -> Self {
        Self {
            output: v["output"].as_str().unwrap_or_default().to_owned(),
            width: v["width"].as_u64().unwrap_or(0) as u32,
            height: v["height"].as_u64().unwrap_or(0) as u32,
            scale: v["scale"].as_f64().unwrap_or(1.0),
            x: v["x"].as_i64().unwrap_or(0),
            y: v["y"].as_i64().unwrap_or(0),
            hz: v["hz"].as_u64().unwrap_or(0) as u32,
        }
    }
}

impl DisplayClient {
    pub fn spawn(argv: &[String], config: &std::path::Path) -> Result<Arc<Self>> {
        let (prog, args) = argv.split_first().ok_or_else(|| anyhow!("empty display_service argv"))?;
        let mut child = Command::new(prog)
            .args(args)
            .arg("--config")
            .arg(config)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit())
            .spawn()
            .with_context(|| format!("spawn display service {prog}"))?;
        let stdin = child.stdin.take().ok_or_else(|| anyhow!("no stdin"))?;
        let stdout = child.stdout.take().ok_or_else(|| anyhow!("no stdout"))?;
        let pending: Arc<Mutex<HashMap<u64, Sender<Value>>>> = Arc::default();
        let (etx, _erx) = channel::<Value>();
        let p2 = Arc::clone(&pending);
        std::thread::Builder::new()
            .name("mios-rdp-display".into())
            .spawn(move || {
                for line in BufReader::new(stdout).lines() {
                    let Ok(line) = line else { break };
                    let Ok(v) = serde_json::from_str::<Value>(&line) else {
                        warn!("display service: bad line {line:?}");
                        continue;
                    };
                    if let Some(id) = v.get("id").and_then(Value::as_u64) {
                        if let Some(tx) = p2.lock().expect("pending").remove(&id) {
                            let _ = tx.send(v);
                        }
                    } else {
                        let _ = etx.send(v);
                    }
                }
                warn!("display service exited");
                p2.lock().expect("pending").clear();
            })
            .context("spawn display reader")?;
        let me = Arc::new(Self {
            stdin: Mutex::new(Some(stdin)),
            child: Mutex::new(Some(child)),
            pending,
            next: AtomicU64::new(1),
        });
        let hello = me.request("hello", json!({}), Duration::from_secs(10))?;
        info!(service = %hello, "display service ready");
        Ok(me)
    }

    pub fn request(&self, op: &str, mut args: Value, timeout: Duration) -> Result<Value> {
        let id = self.next.fetch_add(1, Ordering::SeqCst);
        let (tx, rx) = channel();
        self.pending.lock().expect("pending").insert(id, tx);
        args["id"] = json!(id);
        args["op"] = json!(op);
        {
            let mut g = self.stdin.lock().expect("stdin");
            let w = g.as_mut().ok_or_else(|| anyhow!("display service closed"))?;
            writeln!(w, "{args}").context("write to display service")?;
            w.flush()?;
        }
        let v = rx.recv_timeout(timeout).map_err(|_| {
            self.pending.lock().expect("pending").remove(&id);
            anyhow!("display service did not answer {op} within {timeout:?}")
        })?;
        if v.get("ok").and_then(Value::as_bool) != Some(true) {
            bail!("display service {op}: {}", v.get("error").and_then(Value::as_str).unwrap_or("refused"));
        }
        Ok(v)
    }

    /// Create (or reuse) the remote output sized for the client and save the local cursor.
    pub fn attach(&self, width: u32, height: u32) -> Result<Applied> {
        self.request("attach", json!({"width": width, "height": height}), Duration::from_secs(15)).map(|v| Applied::from(&v))
    }

    pub fn layout(&self, width: u32, height: u32, dpi: Option<f64>) -> Result<Applied> {
        self.request("layout", json!({"width": width, "height": height, "dpi": dpi}), Duration::from_secs(15))
            .map(|v| Applied::from(&v))
    }

    /// One stats window of delivered frames/s for the Pacer; returns the refresh it runs at.
    pub fn pace(&self, fps: f64) -> Result<u32> {
        self.request("pace", json!({"fps": fps}), Duration::from_secs(5)).map(|v| v["hz"].as_u64().unwrap_or(0) as u32)
    }

    pub fn refresh(&self, hz: u32, why: &str) -> Result<u32> {
        self.request("refresh", json!({"hz": hz, "why": why}), Duration::from_secs(5))
            .map(|v| v["hz"].as_u64().unwrap_or(0) as u32)
    }

    /// hl.device({name = <name>, output = <remote output>}); true when Hyprland accepted it.
    pub fn device_rule(&self, name: &str) -> Result<bool> {
        self.request("device_rule", json!({"name": name}), Duration::from_secs(5))
            .map(|v| v["applied"].as_bool().unwrap_or(false))
    }

    pub fn detach(&self) -> Result<()> {
        self.request("detach", json!({}), Duration::from_secs(10)).map(|_| ())
    }

    pub fn shutdown(&self) {
        let _ = self.request("quit", json!({}), Duration::from_secs(10));
        self.stdin.lock().expect("stdin").take();
        if let Some(mut c) = self.child.lock().expect("child").take() {
            for _ in 0..50 {
                if let Ok(Some(_)) = c.try_wait() {
                    return;
                }
                std::thread::sleep(Duration::from_millis(100));
            }
            let _ = c.kill();
            let _ = c.wait();
        }
    }
}
