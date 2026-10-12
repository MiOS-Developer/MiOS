// AI-hint: Entry point for mios-probe, the native host-readiness prober; resolves every threshold from mios.toml rather than from code.
// AI-related: usr/share/mios/mios.toml, Justfile, usr/share/doc/mios/adr/0021-rust-static-binary-consolidation.md

#![forbid(unsafe_code)]
#![warn(clippy::unwrap_used, clippy::expect_used, clippy::panic)]

mod probes {
    use crate::report::{Probe, Report, Verdict};
    use std::path::Path;

    /// The resolved six-tier SSOT under `root` (vendor < vendor.d < host <
    /// host.d < user < user.d), so an operator's [preflight] override is the
    /// threshold probed. A missing vendor file is still a hard stop.
    fn ssot(root: &Path) -> Result<toml::Value, String> {
        let p = root.join("usr/share/mios/mios.toml");
        if !p.is_file() {
            return Err(format!(
                "{} is missing, so no threshold could be read",
                p.display()
            ));
        }
        mios_resolver::resolve_merged(Some(root), false).map_err(|e| {
            format!(
                "the layered mios.toml under {} did not parse: {e}",
                root.display()
            )
        })
    }

    fn cannot_run(why: String) -> Report {
        Report {
            title: String::new(),
            probes: Vec::new(),
            could_not_run: Some(why),
        }
    }

    fn version(root: &Path) -> String {
        std::fs::read_to_string(root.join("VERSION"))
            .map(|s| s.trim().to_string())
            .unwrap_or_else(|_| "?".to_string())
    }

    /// Free whole gigabytes, or None when undeterminable. None is never "enough".
    /// `df -BG` matches the retired shell probe byte for byte; it rounds up (task_963).
    fn free_gib(path: &Path) -> Option<u64> {
        let out = std::process::Command::new("df")
            .arg("-BG")
            .arg(path)
            .output()
            .ok()?;
        if !out.status.success() {
            return None;
        }
        let text = String::from_utf8_lossy(&out.stdout);
        let line = text.lines().nth(1)?;
        let field = line.split_whitespace().nth(3)?;
        field.trim_end_matches('G').parse().ok()
    }

    fn on_path(tool: &str) -> bool {
        let Ok(path) = std::env::var("PATH") else {
            return false;
        };
        std::env::split_paths(&path).any(|d| {
            let c = d.join(tool);
            c.is_file() || c.with_extension("exe").is_file()
        })
    }

    /// Build-host readiness: what `just build` needs before it starts.
    /// Output is byte-identical to the shell probe it replaces.
    pub fn build(root: &Path) -> Report {
        let cfg = match ssot(root) {
            Ok(v) => v,
            Err(e) => return cannot_run(e),
        };
        let Some(b) = cfg.get("preflight").and_then(|p| p.get("build")) else {
            return cannot_run("mios.toml is missing [preflight.build]".to_string());
        };

        let mut probes = Vec::new();

        let Some(tools) = b.get("required_tools").and_then(|v| v.as_array()) else {
            return cannot_run("[preflight.build].required_tools is missing".to_string());
        };
        for t in tools.iter().filter_map(|v| v.as_str()) {
            let (verdict, msg) = if on_path(t) {
                (Verdict::Ok, format!("{t} found"))
            } else {
                (Verdict::Fail, format!("{t} not found"))
            };
            probes.push(Probe {
                key: format!("tool.{t}"),
                verdict,
                message: msg,
            });
        }

        let Some(min_gb) = b.get("min_disk_free_gb").and_then(|v| v.as_integer()) else {
            return cannot_run("[preflight.build].min_disk_free_gb is missing".to_string());
        };
        match free_gib(root) {
            Some(avail) => {
                let verdict = if avail as i64 >= min_gb {
                    Verdict::Ok
                } else {
                    Verdict::Warn
                };
                probes.push(Probe {
                    key: "disk_free_gb".to_string(),
                    verdict,
                    message: format!("Disk space: {avail}G available"),
                });
            }
            None => probes.push(Probe {
                key: "disk_free_gb".to_string(),
                // Unknown is not enough. The shell script read a `df` field with no
                // error path, so an unreadable df printed "Disk space: G available"
                // and compared an empty string as zero.
                verdict: Verdict::Fail,
                message: "Disk space: could not be determined".to_string(),
            }),
        }

        // Every image target's Containerfile is required by the build that reads
        // it, and [build.images.<target>].containerfile is where it is declared:
        // listing it again under required_files was a second declaration that
        // would keep probing the old name after a rename. required_files names only
        // what the build needs beyond its targets.
        let images = cfg.get("build").and_then(|b| b.get("images"));
        let Some(order) = images
            .and_then(|i| i.get("order"))
            .and_then(|v| v.as_array())
        else {
            return cannot_run("[build.images].order is missing".to_string());
        };
        let mut files: Vec<String> = Vec::new();
        for target in order.iter().filter_map(|v| v.as_str()) {
            match images
                .and_then(|i| i.get(target))
                .and_then(|t| t.get("containerfile"))
                .and_then(|v| v.as_str())
            {
                Some(f) => files.push(f.to_string()),
                None => {
                    return cannot_run(format!("[build.images.{target}].containerfile is missing"))
                }
            }
        }
        files.extend(
            b.get("required_files")
                .and_then(|v| v.as_array())
                .into_iter()
                .flatten()
                .filter_map(|v| v.as_str())
                .map(str::to_string),
        );
        for f in &files {
            let (verdict, msg) = if root.join(f).is_file() {
                (Verdict::Ok, format!("{f} present"))
            } else {
                (Verdict::Fail, format!("{f} missing"))
            };
            probes.push(Probe {
                key: format!("file.{f}"),
                verdict,
                message: msg,
            });
        }

        Report {
            title: format!("'MiOS' v{} -- Pre-flight Check", version(root)),
            probes,
            could_not_run: None,
        }
    }

    /// Host-install thresholds: what a machine needs before MiOS is installed onto
    /// it. Each threshold reports NOT APPLICABLE rather than passing on a platform
    /// where it cannot be evaluated -- a Windows build number on Linux is not a
    /// satisfied requirement.
    pub fn host(root: &Path) -> Report {
        let cfg = match ssot(root) {
            Ok(v) => v,
            Err(e) => return cannot_run(e),
        };
        let Some(p) = cfg.get("preflight") else {
            return cannot_run("mios.toml is missing [preflight]".to_string());
        };
        let windows = cfg!(target_os = "windows");
        let mut probes = Vec::new();

        match p.get("min_ram_gb").and_then(|v| v.as_integer()) {
            Some(min) => {
                let got = total_ram_gib();
                match got {
                    Some(gb) => probes.push(Probe {
                        key: "min_ram_gb".to_string(),
                        verdict: if gb as i64 >= min {
                            Verdict::Ok
                        } else {
                            Verdict::Fail
                        },
                        message: format!("RAM: {gb}G present, {min}G required"),
                    }),
                    None => probes.push(Probe {
                        key: "min_ram_gb".to_string(),
                        verdict: Verdict::Fail,
                        message: format!("RAM: could not be determined, {min}G required"),
                    }),
                }
            }
            None => return cannot_run("[preflight].min_ram_gb is missing".to_string()),
        }

        match p.get("min_disk_free_gb").and_then(|v| v.as_integer()) {
            Some(min) => match free_gib(root) {
                Some(avail) => probes.push(Probe {
                    key: "min_disk_free_gb".to_string(),
                    verdict: if avail as i64 >= min {
                        Verdict::Ok
                    } else {
                        Verdict::Fail
                    },
                    message: format!("Disk: {avail}G free, {min}G required"),
                }),
                None => probes.push(Probe {
                    key: "min_disk_free_gb".to_string(),
                    verdict: Verdict::Fail,
                    message: format!("Disk: could not be determined, {min}G required"),
                }),
            },
            None => return cannot_run("[preflight].min_disk_free_gb is missing".to_string()),
        }

        for (key, label) in [
            ("min_windows_build", "Windows build"),
            ("require_virt", "Virtualization"),
            ("require_admin", "Administrator"),
        ] {
            if p.get(key).is_none() {
                return cannot_run(format!("[preflight].{key} is missing"));
            }
            probes.push(Probe {
                key: key.to_string(),
                verdict: if windows {
                    Verdict::Warn
                } else {
                    Verdict::NotApplicable
                },
                message: if windows {
                    format!("{label}: probed by the Windows installer, not by this binary yet")
                } else {
                    format!("{label}: not applicable on this platform")
                },
            });
        }

        Report {
            title: format!("'MiOS' v{} -- Host Readiness", version(root)),
            probes,
            could_not_run: None,
        }
    }

    fn total_ram_gib() -> Option<u64> {
        let text = std::fs::read_to_string("/proc/meminfo").ok()?;
        for line in text.lines() {
            if let Some(rest) = line.strip_prefix("MemTotal:") {
                let kb: u64 = rest.split_whitespace().next()?.parse().ok()?;
                return Some(kb / 1024 / 1024);
            }
        }
        None
    }
}
mod report {
    /// What a single probe concluded.
    ///
    /// `NotApplicable` exists so a probe that cannot run on this platform SAYS so.
    /// Folding it into `Ok` would be Skip-as-Pass: a Windows-only threshold would
    /// read as satisfied on Linux.
    #[derive(Clone, Copy, PartialEq, Eq)]
    pub enum Verdict {
        Ok,
        Warn,
        Fail,
        NotApplicable,
    }

    impl Verdict {
        fn tag(self) -> &'static str {
            match self {
                Verdict::Ok => "ok",
                Verdict::Warn => "warn",
                Verdict::Fail => "fail",
                Verdict::NotApplicable => "not_applicable",
            }
        }
    }

    pub struct Probe {
        pub key: String,
        pub verdict: Verdict,
        pub message: String,
    }

    pub struct Report {
        pub title: String,
        pub probes: Vec<Probe>,
        /// Set when the probe set could not run at all -- exit 2, never 0.
        pub could_not_run: Option<String>,
    }

    const GREEN: &str = "\x1b[0;32m";
    const RED: &str = "\x1b[0;31m";
    const YELLOW: &str = "\x1b[1;33m";
    const NC: &str = "\x1b[0m";

    impl Report {
        pub fn failures(&self) -> usize {
            self.probes
                .iter()
                .filter(|p| p.verdict == Verdict::Fail)
                .count()
        }

        pub fn code(&self) -> u8 {
            if self.could_not_run.is_some() {
                crate::EXIT_CANNOT_RUN
            } else if self.failures() > 0 {
                crate::EXIT_VIOLATIONS
            } else {
                crate::EXIT_CLEAN
            }
        }

        /// Byte-identical to the lines the retired shell probe printed, so its
        /// callers and their logs are unchanged by the port.
        pub fn render_text(&self, color: bool) -> String {
            let (g, r, y, n) = if color {
                (GREEN, RED, YELLOW, NC)
            } else {
                ("", "", "", "")
            };
            let mut out = String::new();
            if let Some(why) = &self.could_not_run {
                out.push_str(&format!("{r}[FAIL]{n} {why}\n"));
                return out;
            }
            out.push_str(&format!("{}\n", self.title));
            for p in &self.probes {
                let line = match p.verdict {
                    Verdict::Ok => format!("{g}[OK]{n}  {}\n", p.message),
                    Verdict::Warn => format!("{y}[WARN]{n} {}\n", p.message),
                    Verdict::Fail => format!("{r}[FAIL]{n} {}\n", p.message),
                    Verdict::NotApplicable => format!("{y}[N/A]{n}  {}\n", p.message),
                };
                out.push_str(&line);
            }
            let failures = self.failures();
            if failures > 0 {
                out.push('\n');
                out.push_str(&format!(
                "{r}[FAIL]{n} Pre-flight failed with {failures} error(s). Resolve above before building.\n"
            ));
            } else {
                out.push('\n');
                out.push_str(&format!("{g}[OK]{n}  All pre-flight checks passed.\n"));
            }
            out
        }

        /// OpenAI-format structured output, so the agent plane reads a verdict
        /// rather than parsing coloured text.
        pub fn render_json(&self) -> String {
            let probes: Vec<_> = self
                .probes
                .iter()
                .map(|p| {
                    serde_json::json!({
                        "key": p.key,
                        "verdict": p.verdict.tag(),
                        "message": p.message,
                    })
                })
                .collect();
            let value = serde_json::json!({
                "probe": "preflight",
                "status": if self.could_not_run.is_some() { "could_not_run" }
                          else if self.failures() > 0 { "failed" } else { "ready" },
                "summary": self.could_not_run.clone().unwrap_or_else(|| self.title.clone()),
                "probes": probes,
            });
            serde_json::to_string_pretty(&value).unwrap_or_else(|_| {
                String::from("{\"probe\":\"preflight\",\"status\":\"could_not_run\",\"probes\":[]}")
            })
        }
    }
}

use std::process::ExitCode;

/// The gate contract, shared with mios-gate: 0 clean, 1 violations,
/// 2 could-not-run. Never 0 on an input that could not be read.
pub const EXIT_CLEAN: u8 = 0;
pub const EXIT_VIOLATIONS: u8 = 1;
pub const EXIT_CANNOT_RUN: u8 = 2;

const USAGE: &str =
    "usage: mios-probe [build|host] [--root DIR] [--format text|json] [--no-color]\n\
                     build  build-host readiness  (default)\n\
                     host   host-install thresholds\n";

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let mut set = String::from("build");
    let mut root = std::env::var("MIOS_ROOT")
        .or_else(|_| std::env::var("MIOS_DRIFT_ROOT"))
        .ok();
    let mut json = false;
    let mut color = std::env::var("NO_COLOR").is_err();

    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "--root" => {
                i += 1;
                match args.get(i) {
                    Some(v) => root = Some(v.clone()),
                    None => {
                        eprint!("mios-probe: --root needs a directory\n{USAGE}");
                        return ExitCode::from(EXIT_CANNOT_RUN);
                    }
                }
            }
            "--format" => {
                i += 1;
                match args.get(i).map(String::as_str) {
                    Some("json") => json = true,
                    Some("text") => json = false,
                    _ => {
                        eprint!("mios-probe: --format takes text or json\n{USAGE}");
                        return ExitCode::from(EXIT_CANNOT_RUN);
                    }
                }
            }
            "--no-color" => color = false,
            "-h" | "--help" => {
                print!("{USAGE}");
                return ExitCode::from(EXIT_CLEAN);
            }
            other if !other.starts_with('-') => set = other.to_string(),
            other => {
                eprint!("mios-probe: unrecognised argument {other:?}\n{USAGE}");
                return ExitCode::from(EXIT_CANNOT_RUN);
            }
        }
        i += 1;
    }

    // Default to the directory the binary is run from, which for `just
    // preflight` is the repo root.
    let root = std::path::PathBuf::from(root.unwrap_or_else(|| ".".to_string()));

    let report = match set.as_str() {
        "build" => probes::build(&root),
        "host" => probes::host(&root),
        other => {
            eprint!("mios-probe: no such probe set {other:?}\n{USAGE}");
            return ExitCode::from(EXIT_CANNOT_RUN);
        }
    };

    if json {
        println!("{}", report.render_json());
    } else {
        let text = report.render_text(color);
        // The shell script it replaces sent [FAIL] to stderr and the rest to
        // stdout; a combined capture is byte-identical either way.
        if report.code() == EXIT_CLEAN {
            print!("{text}");
        } else {
            eprint!("{text}");
        }
    }
    ExitCode::from(report.code())
}
