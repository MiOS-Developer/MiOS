// AI-hint: Memory admission for heavy work (`mios-gen mem-admit`, nested-session launches): refuse, or wait, while /proc/meminfo MemAvailable is below [system.memory_admission].min_available_mb, so agent builds and nested sessions cannot drive the operator's host into the OOM killer.
// AI-related: usr/share/mios/mios.toml [system.memory_admission], tools/native/mios-gen/src/nested_session.rs, usr/lib/systemd/system/user-.slice.d/10-mios-memory.conf
use std::path::Path;
use std::thread::sleep;
use std::time::{Duration, Instant};

use serde_json::json;
use toml::Value;

/// The admission floor and wait policy from [system.memory_admission].
#[derive(Debug, Clone, PartialEq)]
pub struct Policy {
    pub min_available_mb: u64,
    pub poll_ms: u64,
    pub wait_timeout_s: u64,
}

impl Policy {
    pub fn from_ssot(data: &Value) -> Result<Self, String> {
        let t = data.get("system").and_then(|s| s.get("memory_admission"));
        let num = |k: &str| {
            t.and_then(|t| t.get(k))
                .and_then(Value::as_integer)
                .and_then(|v| u64::try_from(v).ok())
                .ok_or_else(|| format!("system.memory_admission.{k} must be a non-negative integer"))
        };
        let p = Policy { min_available_mb: num("min_available_mb")?, poll_ms: num("poll_ms")?, wait_timeout_s: num("wait_timeout_s")? };
        if p.poll_ms == 0 {
            return Err("system.memory_admission.poll_ms must be at least 1".into());
        }
        Ok(p)
    }
}

/// MemAvailable in MiB from a /proc/meminfo-format text.
pub fn parse_available_mb(meminfo: &str) -> Result<u64, String> {
    for line in meminfo.lines() {
        if let Some(rest) = line.strip_prefix("MemAvailable:") {
            let mut it = rest.split_whitespace();
            let kb: u64 = it.next().and_then(|v| v.parse().ok()).ok_or("MemAvailable is not a number")?;
            return match it.next() {
                Some("kB") | None => Ok(kb / 1024),
                Some(u) => Err(format!("MemAvailable has an unknown unit {u}")),
            };
        }
    }
    Err("MemAvailable missing from meminfo".into())
}

fn available_mb(meminfo: &Path) -> Result<u64, String> {
    parse_available_mb(&std::fs::read_to_string(meminfo).map_err(|e| format!("{}: {e}", meminfo.display()))?)
}

/// Admit when MemAvailable >= the floor. With `wait`, poll until admitted or the timeout passes.
/// Returns the final reading; Err carries a message naming the floor and the reading.
pub fn admit(policy: &Policy, meminfo: &Path, wait: bool, what: &str) -> Result<u64, String> {
    let start = Instant::now();
    let mut announced = false;
    loop {
        let avail = available_mb(meminfo)?;
        if avail >= policy.min_available_mb {
            return Ok(avail);
        }
        let msg = format!(
            "{what}: MemAvailable {avail} MiB is below system.memory_admission.min_available_mb {} MiB",
            policy.min_available_mb
        );
        if !wait || start.elapsed() >= Duration::from_secs(policy.wait_timeout_s) {
            return Err(if wait { format!("{msg} (waited {} s)", policy.wait_timeout_s) } else { msg });
        }
        if !announced {
            eprintln!("{msg}; waiting (poll {} ms, up to {} s)", policy.poll_ms, policy.wait_timeout_s);
            announced = true;
        }
        sleep(Duration::from_millis(policy.poll_ms));
    }
}

/// `mios-gen mem-admit`: print the reading and the verdict as JSON; exit 0 admitted, 3 refused.
pub fn run(root: &Path, wait: bool, meminfo: Option<&Path>) -> Result<(), (String, i32)> {
    let data = mios_resolver::resolve_merged(Some(root), false).map_err(|e| (e.to_string(), 1))?;
    let policy = Policy::from_ssot(&data).map_err(|e| (e, 1))?;
    let meminfo = meminfo.unwrap_or(Path::new("/proc/meminfo"));
    match admit(&policy, meminfo, wait, "mem-admit") {
        Ok(avail) => {
            println!("{}", json!({"admitted": true, "available_mb": avail, "min_available_mb": policy.min_available_mb}));
            Ok(())
        }
        Err(e) => {
            println!("{}", json!({"admitted": false, "min_available_mb": policy.min_available_mb, "reason": e}));
            Err((e, 3))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn policy(min: u64) -> Policy {
        Policy { min_available_mb: min, poll_ms: 10, wait_timeout_s: 0 }
    }

    fn meminfo(dir: &tempfile::TempDir, avail_kb: u64) -> std::path::PathBuf {
        let p = dir.path().join("meminfo");
        std::fs::write(&p, format!("MemTotal:       47800000 kB\nMemFree:          100000 kB\nMemAvailable:   {avail_kb} kB\n")).unwrap();
        p
    }

    #[test]
    fn parses_mem_available_in_mib() {
        assert_eq!(parse_available_mb("MemTotal: 1 kB\nMemAvailable:   2097152 kB\n").unwrap(), 2048);
        assert!(parse_available_mb("MemTotal: 1 kB\n").is_err());
        assert!(parse_available_mb("MemAvailable: x kB\n").is_err());
    }

    #[test]
    fn admits_at_or_above_the_floor_and_refuses_below() {
        let d = tempfile::tempdir().unwrap();
        assert_eq!(admit(&policy(4096), &meminfo(&d, 4096 * 1024), false, "t").unwrap(), 4096);
        let e = admit(&policy(4096), &meminfo(&d, 4095 * 1024), false, "t").unwrap_err();
        assert!(e.contains("below system.memory_admission.min_available_mb 4096"), "{e}");
    }

    #[test]
    fn wait_gives_up_after_the_timeout_with_a_reason() {
        let d = tempfile::tempdir().unwrap();
        let e = admit(&policy(1 << 30), &meminfo(&d, 1024), true, "t").unwrap_err();
        assert!(e.contains("waited 0 s"), "{e}");
    }

    #[test]
    fn policy_comes_from_the_ssot_and_rejects_bad_values() {
        let t = |s: &str| -> Value { toml::from_str(s).unwrap() };
        let ok = t("[system.memory_admission]\nmin_available_mb = 12288\npoll_ms = 2000\nwait_timeout_s = 1800\n");
        assert_eq!(Policy::from_ssot(&ok).unwrap(), Policy { min_available_mb: 12288, poll_ms: 2000, wait_timeout_s: 1800 });
        assert!(Policy::from_ssot(&t("")).is_err());
        assert!(Policy::from_ssot(&t("[system.memory_admission]\nmin_available_mb = 1\npoll_ms = 0\nwait_timeout_s = 1\n")).is_err());
        assert!(Policy::from_ssot(&t("[system.memory_admission]\nmin_available_mb = -1\npoll_ms = 1\nwait_timeout_s = 1\n")).is_err());
    }

    #[test]
    fn the_vendor_ssot_declares_a_policy() {
        let ssot: Value = toml::from_str(include_str!("../../../../usr/share/mios/mios.toml")).unwrap();
        assert!(Policy::from_ssot(&ssot).unwrap().min_available_mb > 0);
    }
}
