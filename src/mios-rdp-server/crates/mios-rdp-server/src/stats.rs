//! stats.json (same contract as the RFB bridge: attached, output, target_hz, output_hz, pacing, fps,
//! plus server = "native", codec, bitrate, frames in flight, latencies and the negotiation record),
//! written atomically every 2 s. The delivered rate is EGFX frame acks per second.
#![forbid(unsafe_code)]

use std::path::Path;

pub const WINDOW_SECS: f64 = 2.0;

pub fn median(v: &[f64]) -> f64 {
    percentile(v, 50.0)
}

pub fn percentile(v: &[f64], p: f64) -> f64 {
    if v.is_empty() {
        return 0.0;
    }
    let mut s = v.to_vec();
    s.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    let idx = ((p / 100.0) * (s.len() - 1) as f64).round() as usize;
    s[idx.min(s.len() - 1)]
}

pub fn percentile_u64(v: &[u64], p: f64) -> f64 {
    let f: Vec<f64> = v.iter().map(|x| *x as f64).collect();
    percentile(&f, p)
}

pub fn write_atomic(path: &Path, doc: &serde_json::Value) {
    let Some(dir) = path.parent() else { return };
    if std::fs::create_dir_all(dir).is_err() {
        return;
    }
    let tmp = path.with_extension("json.tmp");
    let body = match serde_json::to_vec(doc) {
        Ok(b) => b,
        Err(_) => return,
    };
    if std::fs::write(&tmp, body).is_ok() {
        let _ = std::fs::rename(&tmp, path);
    }
}

/// Own process CPU time (utime+stime) in seconds, for the per-window CPU share.
pub fn process_cpu_seconds() -> f64 {
    let Ok(s) = std::fs::read_to_string("/proc/self/stat") else { return 0.0 };
    let Some(rest) = s.rsplit_once(')').map(|(_, r)| r) else { return 0.0 };
    let f: Vec<&str> = rest.split_whitespace().collect();
    let tick = 100.0; // CLK_TCK on Linux x86_64
    let ut: f64 = f.get(11).and_then(|v| v.parse().ok()).unwrap_or(0.0);
    let st: f64 = f.get(12).and_then(|v| v.parse().ok()).unwrap_or(0.0);
    (ut + st) / tick
}

pub fn rss_mb() -> f64 {
    let Ok(s) = std::fs::read_to_string("/proc/self/status") else { return 0.0 };
    s.lines()
        .find_map(|l| l.strip_prefix("VmRSS:"))
        .and_then(|v| v.split_whitespace().next())
        .and_then(|k| k.parse::<f64>().ok())
        .map_or(0.0, |k| k / 1024.0)
}

/// amdgpu gpu_busy_percent of the first card that has it (gpu_guard input).
pub fn gpu_busy_percent() -> Option<u32> {
    let rd = std::fs::read_dir("/sys/class/drm").ok()?;
    for e in rd.flatten() {
        let name = e.file_name();
        let n = name.to_string_lossy();
        if !n.starts_with("card") || n.contains('-') {
            continue;
        }
        if let Ok(v) = std::fs::read_to_string(e.path().join("device/gpu_busy_percent")) {
            if let Ok(x) = v.trim().parse() {
                return Some(x);
            }
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn percentiles() {
        let v: Vec<f64> = (1..=100).map(f64::from).collect();
        assert_eq!(median(&v), 51.0);
        assert_eq!(percentile(&v, 95.0), 95.0);
        assert_eq!(percentile(&[], 50.0), 0.0);
    }
}
