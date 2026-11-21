// AI-hint: mios-probe ras -- counts this boot's hardware errors (EDAC memory-controller ce_count/ue_count and the kernel's "Machine check events logged" notices), warns at [hardware.ras].corrected_warn_per_boot and fails on any uncorrected error, pointing at rasdaemon's per-event record.
// AI-related: usr/share/mios/mios.toml, usr/lib/systemd/system/mios-ras-check.service, usr/lib/systemd/system/mios-ras-check.timer, rasdaemon.service

use crate::report::{Probe, Verdict};
use std::fs;
use std::path::Path;

/// The kernel's rate-limited notice that corrected machine-check events were
/// logged; each notice covers one or more events.
const MCE_NOTICE: &str = "Machine check events logged";

#[derive(Debug, Default, PartialEq)]
pub struct Counts {
    /// EDAC memory controllers registered.
    pub controllers: u64,
    pub edac_ce: u64,
    pub edac_ue: u64,
    /// None when the kernel log could not be read.
    pub mce_notices: Option<u64>,
}

fn count(p: &Path) -> u64 {
    fs::read_to_string(p)
        .ok()
        .and_then(|s| s.trim().parse().ok())
        .unwrap_or(0)
}

pub fn gather(sys: &Path, kernel_log: Option<&str>) -> Counts {
    // A booted kernel always logs; an empty answer (journalctl exits 0 when
    // there is no journal at all) is no evidence, not a clean boot.
    let kernel_log = kernel_log.filter(|l| !l.trim().is_empty());
    let mut c = Counts {
        mce_notices: kernel_log
            .map(|l| l.lines().filter(|x| x.contains(MCE_NOTICE)).count() as u64),
        ..Counts::default()
    };
    if let Ok(rd) = fs::read_dir(sys.join("devices/system/edac/mc")) {
        for e in rd.flatten() {
            let n = e.file_name().to_string_lossy().into_owned();
            if n.strip_prefix("mc")
                .is_some_and(|d| !d.is_empty() && d.bytes().all(|b| b.is_ascii_digit()))
            {
                c.controllers += 1;
                c.edac_ce += count(&e.path().join("ce_count"));
                c.edac_ue += count(&e.path().join("ue_count"));
            }
        }
    }
    c
}

pub fn warn_threshold(cfg: &toml::Value) -> Result<u64, String> {
    cfg.get("hardware")
        .and_then(|h| h.get("ras"))
        .and_then(|r| r.get("corrected_warn_per_boot"))
        .and_then(|v| v.as_integer())
        .filter(|n| *n >= 1)
        .map(|n| n as u64)
        .ok_or("[hardware.ras].corrected_warn_per_boot is missing or below 1".to_string())
}

pub fn judge(c: &Counts, warn_at: u64) -> Vec<Probe> {
    let mut probes = Vec::new();
    let record = "per-event record: ras-mc-ctl --errors";
    let edac = if c.controllers == 0 {
        "no EDAC memory controller is registered, so DIMM-level counts are unavailable".to_string()
    } else {
        format!(
            "EDAC: {} corrected, {} uncorrected across {} memory controller(s)",
            c.edac_ce, c.edac_ue, c.controllers
        )
    };
    if c.edac_ue > 0 {
        probes.push(Probe {
            key: "ras.uncorrected".to_string(),
            verdict: Verdict::Fail,
            message: format!("RAS: uncorrected memory errors this boot -- {edac}; {record}"),
        });
    }
    match c.mce_notices {
        None => probes.push(Probe {
            key: "ras.corrected".to_string(),
            verdict: Verdict::Warn,
            message: format!(
                "RAS: this boot's kernel log could not be read, so machine-check notices were not counted; {edac}"
            ),
        }),
        Some(n) => {
            let corrected = n.max(c.edac_ce);
            probes.push(Probe {
                key: "ras.corrected".to_string(),
                verdict: if corrected >= warn_at {
                    Verdict::Warn
                } else {
                    Verdict::Ok
                },
                message: format!(
                    "RAS: {n} kernel machine-check notice(s) this boot (warn at {warn_at}; \
                     each notice covers one or more corrected events); {edac}; {record}"
                ),
            });
        }
    }
    probes
}

#[cfg(test)]
mod tests {
    use super::*;

    fn edac(t: &Path, mc: &str, ce: &str, ue: &str) {
        let d = t.join("devices/system/edac/mc").join(mc);
        fs::create_dir_all(&d).unwrap();
        fs::write(d.join("ce_count"), ce).unwrap();
        fs::write(d.join("ue_count"), ue).unwrap();
    }

    const LOG: &str = "Linux version 7.2\n\
        mce: [Hardware Error]: Machine check events logged\n\
        usb 1-1: new high-speed USB device\n\
        mce: [Hardware Error]: Machine check events logged\n";

    #[test]
    fn corrected_notices_warn_at_the_threshold() {
        let t = tempfile::tempdir().unwrap();
        let c = gather(t.path(), Some(LOG));
        assert_eq!(c.mce_notices, Some(2));
        let p = judge(&c, 1);
        assert!(
            p.len() == 1 && p[0].verdict == Verdict::Warn,
            "{}",
            p[0].message
        );
        assert!(p[0].message.contains("no EDAC memory controller"));
        assert!(judge(&c, 3)[0].verdict == Verdict::Ok);
    }

    #[test]
    fn clean_boot_is_ok() {
        let t = tempfile::tempdir().unwrap();
        edac(t.path(), "mc0", "0\n", "0\n");
        let p = judge(&gather(t.path(), Some("Linux version 7.2\n")), 1);
        assert!(
            p.len() == 1 && p[0].verdict == Verdict::Ok,
            "{}",
            p[0].message
        );
        assert!(p[0].message.contains("0 corrected, 0 uncorrected across 1"));
    }

    #[test]
    fn edac_corrected_counts_without_a_notice() {
        let t = tempfile::tempdir().unwrap();
        edac(t.path(), "mc0", "3\n", "0\n");
        edac(t.path(), "mc1", "2\n", "0\n");
        // Not an mc<N> controller: ignored.
        edac(t.path(), "power", "9\n", "9\n");
        let c = gather(t.path(), Some("Linux version 7.2\n"));
        assert_eq!((c.controllers, c.edac_ce, c.edac_ue), (2, 5, 0));
        assert!(judge(&c, 5)[0].verdict == Verdict::Warn);
    }

    #[test]
    fn any_uncorrected_error_fails() {
        let t = tempfile::tempdir().unwrap();
        edac(t.path(), "mc0", "0\n", "1\n");
        let p = judge(&gather(t.path(), Some("Linux version 7.2\n")), 1);
        assert!(p.iter().any(|p| p.verdict == Verdict::Fail));
    }

    #[test]
    fn unreadable_log_is_not_a_pass() {
        let t = tempfile::tempdir().unwrap();
        let p = judge(&gather(t.path(), None), 1);
        assert!(p[0].verdict == Verdict::Warn, "{}", p[0].message);
        // journalctl exits 0 with no output when there is no journal at all.
        let p = judge(&gather(t.path(), Some("\n")), 1);
        assert!(p[0].verdict == Verdict::Warn, "{}", p[0].message);
    }

    #[test]
    fn threshold_comes_from_the_ssot() {
        let ok: toml::Value =
            toml::from_str("[hardware.ras]\ncorrected_warn_per_boot = 4\n").unwrap();
        assert_eq!(warn_threshold(&ok), Ok(4));
        let zero: toml::Value =
            toml::from_str("[hardware.ras]\ncorrected_warn_per_boot = 0\n").unwrap();
        assert!(warn_threshold(&zero).is_err());
    }
}
