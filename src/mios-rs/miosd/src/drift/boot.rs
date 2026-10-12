// AI-hint: Boot-integrity and firstboot degrade-open checks for miosd drift runner.
// AI-related: usr/libexec/mios, automation/firstboot, automation/78-greenboot.sh, etc/greenboot

use super::audit::{self, Audit};
use super::{Check, DriftCtx, Verdict};
use regex::Regex;
use std::collections::BTreeMap;
use std::process::Command;

pub struct FirstbootDegradeOpenCheck;
impl Check for FirstbootDegradeOpenCheck {
    fn id(&self) -> &'static str {
        "check_firstboot_degrade_open"
    }
    fn describe(&self) -> &'static str {
        "Assert all firstboot scripts implement explicit degrade-open fallback handling"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        audit::verdict(degrade_open(ctx))
    }
}

pub struct GreenbootEnablementCheck;
impl Check for GreenbootEnablementCheck {
    fn id(&self) -> &'static str {
        "check_greenboot_enablement"
    }
    fn describe(&self) -> &'static str {
        "Assert greenboot health check scripts are correctly registered and enabled"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        audit::verdict(greenboot(ctx))
    }
}

// Law 12 scan roots, ported from tools/check-runtime.py fdo_SCAN_GLOBS
// ("usr/libexec/mios/*firstboot*", "automation/firstboot/*.sh"): (dir, infix, suffix).
// Both roots are the gate's subject, so a missing one fails instead of shrinking it.
const FIRSTBOOT_ROOTS: [(&str, &str, &str); 2] = [
    ("usr/libexec/mios", "firstboot", ""),
    ("automation/firstboot", "", ".sh"),
];
const FIRSTBOOT_SKIP: [&str; 5] = [".pyc", ".bak", ".keep", ".orig", ".rej"];

fn firstboot_subjects(ctx: &DriftCtx) -> Result<Vec<String>, String> {
    let mut out = Vec::new();
    for (dir, infix, suffix) in FIRSTBOOT_ROOTS {
        let entries = std::fs::read_dir(ctx.root.join(dir)).map_err(|e| format!("{dir}: firstboot scan root unreadable ({e}); restore it or update the Law 12 scan roots"))?;
        for entry in entries {
            let entry = entry.map_err(|e| format!("{dir}: {e}"))?;
            let name = entry.file_name().to_string_lossy().into_owned();
            // glob '*' never matches a leading dot; isfile() follows symlinks.
            if name.starts_with('.')
                || !name.contains(infix)
                || !name.ends_with(suffix)
                || FIRSTBOOT_SKIP.iter().any(|s| name.ends_with(s))
                || !entry.path().is_file()
            {
                continue;
            }
            out.push(format!("{dir}/{name}"));
        }
    }
    out.sort();
    Ok(out)
}

/// Join backslash continuations and parenthesised runs into one logical line: the guard
/// usually lands after a continuation or a closing paren, so a physical scan reports
/// guarded calls as unguarded.
fn logical_lines(text: &str) -> Vec<(usize, String)> {
    let lines: Vec<&str> = text.split('\n').collect();
    let (mut out, mut buf, mut start, mut depth) = (Vec::new(), String::new(), None, 0usize);
    for (index, line) in lines.iter().enumerate() {
        let first = *start.get_or_insert(index + 1);
        let stripped = line.trim_end();
        let continued = stripped.ends_with('\\');
        buf.push_str(if continued {
            &stripped[..stripped.len() - 1]
        } else {
            line
        });
        depth = (depth + line.matches('(').count()).saturating_sub(line.matches(')').count());
        if continued || depth > 0 {
            buf.push(' ');
            continue;
        }
        out.push((first, std::mem::take(&mut buf)));
        start = None;
    }
    if !buf.is_empty() {
        out.push((start.unwrap_or(lines.len()), buf));
    }
    out
}

struct Egress {
    call: Regex,
    guard: Regex,
    errexit_on: Regex,
    errexit_off: Regex,
    narration: Regex,
}

impl Egress {
    fn new() -> Result<Self, String> {
        let compile = |p: &str| Regex::new(p).map_err(|e| e.to_string());
        Ok(Self {
            call: compile(
                r"\b(curl|wget|podman\s+pull|skopeo\s+copy|dnf\s+(install|upgrade)|git\s+clone|bootc\s+(switch|upgrade)|pip\s+install|hf\s+download|huggingface-cli\s+download|rpm-ostree|flatpak\s+install)\b",
            )?,
            // errexit does not fire on a condition or on the left of a && / || list.
            guard: compile(
                r"\|\||^\s*(if|while|until|elif)\s|&&\s*(true|:|return|exit)|\|\|\s*(return|exit)",
            )?,
            // Column 0 only: an indented 'set +e' is inside a function or subshell and must
            // not exempt later top-level lines. 'trap ... EXIT' is deliberately no escape.
            errexit_on: compile(r"^set\s+-[a-zA-Z]*e|^set\s+-o\s+errexit")?,
            errexit_off: compile(r"^set\s+\+[a-zA-Z]*e|^set\s+\+o\s+errexit")?,
            // A fetch named inside a log/echo string is documentation, not a call.
            narration: compile(r"^\s*(_?log\w*|echo|printf|cat|#)\b")?,
        })
    }

    /// Egress calls reached with errexit active and no fallback on their own logical line.
    fn scan(&self, text: &str) -> Vec<(usize, String)> {
        let text = text.replace("\r\n", "\n").replace('\r', "\n");
        let (mut errexit, mut bad) = (false, Vec::new());
        for (number, line) in logical_lines(&text) {
            if line.trim_start().starts_with('#') || self.narration.is_match(&line) {
                continue;
            }
            if self.errexit_on.is_match(&line) {
                errexit = true;
            }
            if self.errexit_off.is_match(&line) {
                errexit = false;
            }
            if errexit && self.call.is_match(&line) && !self.guard.is_match(&line) {
                bad.push((
                    number,
                    line.split_whitespace()
                        .collect::<Vec<_>>()
                        .join(" ")
                        .chars()
                        .take(100)
                        .collect(),
                ));
            }
        }
        bad
    }
}

// The legacy check grepped the whole FILE for '|| true' (or set +e / trap / exit 0) and
// called that degrade-open: one unrelated cleanup guard certified a script that really
// aborted firstboot on an unreachable API. The question is scoped to each egress call.
fn degrade_open(ctx: &DriftCtx) -> Audit {
    let egress = Egress::new()?;
    let subjects = firstboot_subjects(ctx)?;
    let mut errors = Vec::new();
    for path in &subjects {
        match std::fs::read(ctx.root.join(path)) {
            Ok(bytes) => {
                for (number, text) in egress.scan(&String::from_utf8_lossy(&bytes)) {
                    errors.push(format!("{path}:{number}: does not degrade open (Law 12): egress call runs under active set -e with no fallback -- {text}; add '|| true'/'|| return', test it in an 'if', or drop errexit around it"));
                }
            }
            Err(e) => errors.push(format!("{path}:0: unreadable firstboot script: {e}")),
        }
    }
    audit::finish(
        subjects.len(),
        errors,
        "firstboot scripts whose egress calls all degrade open (Law 12)",
    )
}

const GREENBOOT_PHASE: &str = "automation/78-greenboot.sh";
const GREENBOOT_UNITS: [&str; 2] = [
    "greenboot-healthcheck.service",
    "greenboot-set-rollback-trigger.service",
];
const GREENBOOT_DIR: &str = "etc/greenboot";

/// Index modes from `git ls-files -s`: the mode git checks out, which a Windows
/// filesystem cannot express and which the image COPY inherits.
fn git_modes(ctx: &DriftCtx, under: &str) -> Result<BTreeMap<String, String>, String> {
    let output = Command::new("git")
        .arg("-C")
        .arg(&ctx.root)
        .args(["ls-files", "-s", "-z", "--", under])
        .output()
        .map_err(|e| format!("git ls-files -s: {e}"))?;
    if !output.status.success() {
        return Err(format!(
            "git ls-files -s {under} {}: {}",
            output.status,
            String::from_utf8_lossy(&output.stderr)
        ));
    }
    let text = String::from_utf8(output.stdout).map_err(|e| format!("git ls-files -s: {e}"))?;
    Ok(text
        .split('\0')
        .filter_map(|record| {
            let (meta, path) = record.split_once('\t')?;
            Some((path.to_owned(), meta.split_whitespace().next()?.to_owned()))
        })
        .collect())
}

#[cfg(unix)]
fn executable(path: &std::path::Path) -> Result<bool, String> {
    use std::os::unix::fs::PermissionsExt;
    Ok(std::fs::metadata(path)
        .map_err(|e| format!("{}: {e}", path.display()))?
        .permissions()
        .mode()
        & 0o111
        != 0)
}

#[cfg(not(unix))]
fn executable(path: &std::path::Path) -> Result<bool, String> {
    Err(format!(
        "{}: no git index entry and this platform has no executable bit; run from a git checkout",
        path.display()
    ))
}

fn greenboot(ctx: &DriftCtx) -> Audit {
    let text = audit::read(&ctx.root, GREENBOOT_PHASE)?;
    // A unit named only in a comment is not enabled: the legacy grep accepted that.
    let code: Vec<&str> = text
        .lines()
        .filter(|line| !line.trim_start().starts_with('#'))
        .collect();
    let enable =
        Regex::new(r"\bsystemctl\b.*\benable\b|\bln\s+-[A-Za-z]*s").map_err(|e| e.to_string())?;
    let mut errors = Vec::new();
    if !code.iter().any(|line| enable.is_match(line)) {
        errors.push(format!("{GREENBOOT_PHASE}:1: no enablement command (systemctl enable, or ln -s into a .wants directory) outside comments; greenboot services are never enabled"));
    }
    for unit in GREENBOOT_UNITS {
        if !code.iter().any(|line| line.contains(unit)) {
            errors.push(format!("{GREENBOOT_PHASE}:1: {unit} is not enabled outside comments; add it to the greenboot enablement commands"));
        }
    }
    let scripts: Vec<String> = audit::files(&ctx.root, GREENBOOT_DIR)?
        .into_iter()
        .filter(|p| p.ends_with(".sh"))
        .collect();
    if scripts.is_empty() {
        errors.push(format!(
            "{GREENBOOT_DIR}: no **/*.sh health-check scripts; greenboot would certify every boot"
        ));
    }
    let modes = if ctx.git_ok {
        git_modes(ctx, GREENBOOT_DIR)?
    } else {
        BTreeMap::new()
    };
    for path in &scripts {
        match modes.get(path) {
            Some(mode) if mode == "100755" => {}
            Some(mode) => errors.push(format!("{path}:1: git mode {mode} (expected 100755); greenboot skips non-executable checks -- run 'git update-index --chmod=+x {path}'")),
            // Untracked, or no git (the bake is a work tree, so this is the rare path).
            None => {
                if !executable(&ctx.root.join(path))? {
                    errors.push(format!("{path}:1: not executable (expected mode 0755); greenboot skips non-executable checks -- chmod +x {path}"));
                }
            }
        }
    }
    audit::finish(
        scripts.len() + GREENBOOT_UNITS.len(),
        errors,
        "greenboot unit enablement and health-check script modes",
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    fn write(
        root: &std::path::Path,
        rel: &str,
        body: &str,
    ) -> Result<(), Box<dyn std::error::Error>> {
        let path = root.join(rel);
        fs::create_dir_all(path.parent().ok_or("no parent")?)?;
        fs::write(path, body)?;
        Ok(())
    }

    #[test]
    fn logical_lines_join_continuations_and_parenthesised_runs() {
        let lines = logical_lines("a \\\n  b\n(c\nd)\ne\n");
        assert_eq!(
            lines.iter().map(|(n, _)| *n).collect::<Vec<_>>(),
            vec![1, 3, 5, 6]
        );
        assert!(lines[0].1.starts_with("a ") && lines[0].1.contains('b'));
        assert!(lines[1].1.contains('c') && lines[1].1.contains("d)"));
    }

    #[test]
    fn degrade_open_scopes_the_question_to_each_egress_call(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let temp = tempfile::tempdir()?;
        let root = temp.path();
        let ctx = DriftCtx::new(root.into(), false);
        assert!(degrade_open(&ctx).is_err_and(|e| e.contains("scan root")));
        fs::create_dir_all(root.join("usr/libexec/mios"))?;
        fs::create_dir_all(root.join("automation/firstboot"))?;
        assert!(degrade_open(&ctx).is_err_and(|e| e.contains("no subjects")));
        let guarded = "#!/bin/bash\nset -euo pipefail\ncurl -fsS https://x || true\nif podman pull img; then :; fi\ngit clone \\\n  https://x/repo dir \\\n  || exit 0\nlog \"curl https://x\"\n# wget https://x\n";
        write(root, "usr/libexec/mios/forge-firstboot.sh", guarded)?;
        write(
            root,
            "usr/libexec/mios/forge-firstboot.sh.bak",
            "set -e\ncurl https://x\n",
        )?;
        write(
            root,
            "automation/firstboot/setup.sh",
            "#!/bin/bash\ncurl https://x\nset -e\nset +e\nwget https://x\n",
        )?;
        write(
            root,
            "automation/firstboot/notes.txt",
            "set -e\ncurl https://x\n",
        )?;
        assert!(degrade_open(&ctx).is_ok_and(|m| m.contains("2 subject")));
        // A file-global '|| true' elsewhere must not certify an unguarded fetch.
        write(
            root,
            "usr/libexec/mios/mios-ai-firstboot",
            &format!("{guarded}rm -f /tmp/x || true\ndnf install -y pkg\n"),
        )?;
        assert!(degrade_open(&ctx).is_err_and(|e| e
            .contains("usr/libexec/mios/mios-ai-firstboot:11: does not degrade open")
            && e.contains("dnf install -y pkg")
            && !e.contains("forge-firstboot")));
        // An indented 'set +e' (inside a function) does not end top-level errexit.
        write(
            root,
            "usr/libexec/mios/mios-ai-firstboot",
            "set -e\nf() {\n  set +e\n}\nbootc switch ref\n",
        )?;
        assert!(degrade_open(&ctx).is_err_and(|e| e.contains("mios-ai-firstboot:5:")));
        write(
            root,
            "usr/libexec/mios/mios-ai-firstboot",
            "set -e\nbootc switch ref && true\n",
        )?;
        assert!(degrade_open(&ctx).is_ok());
        Ok(())
    }

    #[test]
    fn greenboot_requires_enablement_code_and_executable_scripts(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let temp = tempfile::tempdir()?;
        let root = temp.path();
        let ctx = DriftCtx::new(root.into(), false);
        assert!(greenboot(&ctx).is_err_and(|e| e.contains(GREENBOOT_PHASE)));
        let phase = "set -e\nWANTS=/usr/lib/systemd/system/multi-user.target.wants\nfor unit in greenboot-healthcheck.service greenboot-set-rollback-trigger.service; do\n  ln -sf \"../${unit}\" \"${WANTS}/${unit}\"\ndone\n";
        write(root, GREENBOOT_PHASE, phase)?;
        fs::create_dir_all(root.join("etc/greenboot/check/required.d"))?;
        write(root, "etc/greenboot/greenboot.conf", "x=1\n")?;
        assert!(greenboot(&ctx).is_err_and(|e| e.contains("no **/*.sh")));
        write(
            root,
            "etc/greenboot/check/required.d/50-core.sh",
            "#!/bin/sh\n",
        )?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let script = root.join("etc/greenboot/check/required.d/50-core.sh");
            fs::set_permissions(&script, fs::Permissions::from_mode(0o644))?;
            assert!(greenboot(&ctx).is_err_and(|e| e.contains("50-core.sh:1: not executable")));
            fs::set_permissions(&script, fs::Permissions::from_mode(0o755))?;
            assert!(greenboot(&ctx).is_ok_and(|m| m.contains("3 subject")));
            write(
                root,
                GREENBOOT_PHASE,
                &phase.replace(
                    "greenboot-set-rollback-trigger.service",
                    "\n# greenboot-set-rollback-trigger.service\n",
                ),
            )?;
            assert!(greenboot(&ctx).is_err_and(
                |e| e.contains("greenboot-set-rollback-trigger.service is not enabled")
            ));
            write(root, GREENBOOT_PHASE, &phase.replace("ln -sf", "echo"))?;
            assert!(greenboot(&ctx).is_err_and(|e| e.contains("no enablement command")));
            // A git index entry is authoritative over the working-tree bit.
            write(root, GREENBOOT_PHASE, phase)?;
            let git = |args: &[&str]| Command::new("git").arg("-C").arg(root).args(args).output();
            assert!(git(&["init", "-q"])?.status.success());
            assert!(git(&["add", "-A"])?.status.success());
            assert!(git(&[
                "update-index",
                "--chmod=-x",
                "etc/greenboot/check/required.d/50-core.sh"
            ])?
            .status
            .success());
            let tracked = DriftCtx::new(root.into(), false);
            assert!(tracked.git_ok);
            assert!(greenboot(&tracked).is_err_and(|e| e.contains("50-core.sh:1: git mode 100644")));
            assert!(git(&[
                "update-index",
                "--chmod=+x",
                "etc/greenboot/check/required.d/50-core.sh"
            ])?
            .status
            .success());
            assert!(greenboot(&tracked).is_ok());
        }
        Ok(())
    }
}
