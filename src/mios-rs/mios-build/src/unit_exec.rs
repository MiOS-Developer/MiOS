// AI-hint: Image gate: every boot-reachable systemd unit (enable links, timer/socket/path triggers, Wants=/Requires= edges) must name Exec*= programs the image root carries; resolves symlinks inside the root only.
// AI-related: automation/99-postcheck.sh, src/mios-rs/miosd/src/main.rs, tests/test-postcheck-unit-execs.sh
//! Boot-reachable systemd units must be able to exec what they name.
//!
//! A unit that ships enabled (a `.wants`/`.requires`/`.upholds` link, a timer,
//! socket or path that triggers it, or a `Wants=`/`Requires=` edge from such a
//! unit) whose `Exec*=` program is absent from the image fails at boot with
//! status 203/EXEC, forever, on every host that carries it. This check walks
//! the image root, follows symlinks inside that root only, and reports every
//! such command before the image is sealed.
//!
//! A missing program is not a failure when systemd itself skips the command
//! cleanly: the unit carries a non-negated `ConditionPathExists=` or
//! `ConditionFileIsExecutable=` naming exactly that program, or the command is
//! `-`-prefixed (systemd records the failure and continues). The latter is
//! reported as a warning.

use std::collections::{BTreeMap, BTreeSet, VecDeque};
use std::path::{Path, PathBuf};

const EXEC_KEYS: &[&str] = &[
    "ExecCondition",
    "ExecStartPre",
    "ExecStart",
    "ExecStartPost",
    "ExecReload",
    "ExecStop",
    "ExecStopPost",
];
const GUARD_KEYS: &[&str] = &["ConditionPathExists", "ConditionFileIsExecutable"];
const DEP_KEYS: &[&str] = &["Wants", "Requires", "Upholds", "BindsTo", "Requisite"];
/// systemd's fixed lookup for a bare command name (systemd.service(5)).
const SEARCH_PATH: &[&str] = &[
    "/usr/local/sbin",
    "/usr/local/bin",
    "/usr/sbin",
    "/usr/bin",
    "/sbin",
    "/bin",
];

struct Scope {
    name: &'static str,
    /// Highest precedence first.
    dirs: &'static [&'static str],
}

const SCOPES: &[Scope] = &[
    Scope {
        name: "system",
        dirs: &[
            "etc/systemd/system",
            "usr/local/lib/systemd/system",
            "usr/lib/systemd/system",
        ],
    },
    Scope {
        name: "user",
        dirs: &[
            "etc/systemd/user",
            "usr/local/lib/systemd/user",
            "usr/lib/systemd/user",
        ],
    },
];

#[derive(Debug, Default)]
pub struct Report {
    pub units: usize,
    pub commands: usize,
    pub failures: Vec<String>,
    pub warnings: Vec<String>,
}

/// Resolve `path` (absolute, image-relative) inside `root`, following symlinks
/// without ever leaving the root. Returns the final in-root path.
fn resolve_in_root(root: &Path, path: &str) -> Option<PathBuf> {
    let mut pending: VecDeque<String> = path
        .split('/')
        .filter(|c| !c.is_empty())
        .map(str::to_string)
        .collect();
    let mut current: Vec<String> = Vec::new();
    let mut hops = 0;
    while let Some(component) = pending.pop_front() {
        match component.as_str() {
            "." => continue,
            ".." => {
                current.pop();
                continue;
            }
            _ => {}
        }
        let mut candidate = root.to_path_buf();
        candidate.extend(&current);
        candidate.push(&component);
        let meta = std::fs::symlink_metadata(&candidate).ok()?;
        if meta.file_type().is_symlink() {
            hops += 1;
            if hops > 40 {
                return None;
            }
            let target = std::fs::read_link(&candidate).ok()?;
            let target = target.to_string_lossy().to_string();
            let mut rest: VecDeque<String> = target
                .split('/')
                .filter(|c| !c.is_empty())
                .map(str::to_string)
                .collect();
            if target.starts_with('/') {
                current.clear();
            }
            rest.extend(pending.drain(..));
            pending = rest;
        } else {
            current.push(component);
        }
    }
    let mut out = root.to_path_buf();
    out.extend(&current);
    Some(out)
}

fn is_executable_file(root: &Path, path: &str) -> bool {
    let Some(resolved) = resolve_in_root(root, path) else {
        return false;
    };
    let Ok(meta) = std::fs::metadata(&resolved) else {
        return false;
    };
    if !meta.is_file() {
        return false;
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        meta.permissions().mode() & 0o111 != 0
    }
    #[cfg(not(unix))]
    {
        true
    }
}

fn is_masked(root: &Path, file: &Path) -> bool {
    match std::fs::read_link(file) {
        Ok(target) => target == Path::new("/dev/null"),
        Err(_) => {
            // A link chain inside the root can also end at /dev/null.
            let rel = file.strip_prefix(root).unwrap_or(file);
            let resolved = resolve_in_root(root, &format!("/{}", rel.to_string_lossy()));
            resolved.is_some_and(|p| p == root.join("dev/null"))
        }
    }
}

/// Logical lines: comments dropped, backslash continuations joined.
fn logical_lines(text: &str) -> Vec<String> {
    let mut out = Vec::new();
    let mut acc = String::new();
    for raw in text.lines() {
        let line = raw.trim_end();
        if acc.is_empty() {
            let t = line.trim_start();
            if t.starts_with('#') || t.starts_with(';') {
                continue;
            }
        }
        if let Some(head) = line.strip_suffix('\\') {
            acc.push_str(head);
            acc.push(' ');
            continue;
        }
        acc.push_str(line);
        out.push(std::mem::take(&mut acc));
    }
    if !acc.is_empty() {
        out.push(acc);
    }
    out
}

#[derive(Default)]
struct Parsed {
    /// key -> ordered (value, source file) list after systemd's empty-reset rule.
    exec: BTreeMap<String, Vec<(String, String)>>,
    guards: BTreeSet<String>,
    deps: Vec<String>,
    unit_key: Option<String>,
}

fn parse_into(parsed: &mut Parsed, text: &str, source: &str) {
    for line in logical_lines(text) {
        let Some((key, value)) = line.split_once('=') else {
            continue;
        };
        let key = key.trim();
        let value = value.trim();
        if EXEC_KEYS.contains(&key) {
            let slot = parsed.exec.entry(key.to_string()).or_default();
            if value.is_empty() {
                slot.clear();
            } else {
                slot.push((value.to_string(), source.to_string()));
            }
        } else if GUARD_KEYS.contains(&key) {
            // "|" makes it a triggering condition; "!" negates it, which never
            // guards a missing program.
            let v = value.trim_start_matches('|');
            if !v.starts_with('!') && !v.is_empty() {
                parsed.guards.insert(v.to_string());
            }
        } else if DEP_KEYS.contains(&key) {
            parsed
                .deps
                .extend(value.split_whitespace().map(str::to_string));
        } else if key == "Unit" {
            parsed.unit_key = Some(value.to_string());
        }
    }
}

struct ScopeIndex<'a> {
    root: &'a Path,
    dirs: Vec<PathBuf>,
}

impl ScopeIndex<'_> {
    /// The unit file systemd would load for `name` (instance -> template).
    fn unit_file(&self, name: &str) -> Option<PathBuf> {
        let mut names = vec![name.to_string()];
        if let Some(template) = template_of(name) {
            names.push(template);
        }
        for n in &names {
            for dir in &self.dirs {
                let p = dir.join(n);
                if std::fs::symlink_metadata(&p).is_ok() {
                    return Some(p);
                }
            }
        }
        None
    }

    /// Drop-ins for `name` and, for an instance, its template; lower
    /// precedence first, so later entries win as in systemd.
    fn dropins(&self, name: &str, canonical: &str) -> Vec<PathBuf> {
        let mut bases = vec![name.to_string()];
        if canonical != name {
            bases.push(canonical.to_string());
        }
        if let Some(template) = template_of(name) {
            bases.push(template);
        }
        let mut by_name: BTreeMap<String, PathBuf> = BTreeMap::new();
        for dir in self.dirs.iter().rev() {
            for base in &bases {
                let d = dir.join(format!("{base}.d"));
                let Ok(entries) = std::fs::read_dir(&d) else {
                    continue;
                };
                for e in entries.flatten() {
                    let file = e.file_name().to_string_lossy().to_string();
                    if file.ends_with(".conf") {
                        by_name.insert(file, e.path());
                    }
                }
            }
        }
        by_name.into_values().collect()
    }

    /// Every unit named by a .wants/.requires/.upholds directory in the scope.
    fn linked(&self) -> BTreeSet<String> {
        let mut out = BTreeSet::new();
        for dir in &self.dirs {
            let Ok(entries) = std::fs::read_dir(dir) else {
                continue;
            };
            for e in entries.flatten() {
                let n = e.file_name().to_string_lossy().to_string();
                if !(n.ends_with(".wants") || n.ends_with(".requires") || n.ends_with(".upholds")) {
                    continue;
                }
                if let Ok(links) = std::fs::read_dir(e.path()) {
                    for l in links.flatten() {
                        out.insert(l.file_name().to_string_lossy().to_string());
                    }
                }
            }
        }
        out
    }

    fn rel(&self, p: &Path) -> String {
        format!("/{}", p.strip_prefix(self.root).unwrap_or(p).display())
    }
}

fn template_of(name: &str) -> Option<String> {
    let (stem, suffix) = name.rsplit_once('.')?;
    let (prefix, instance) = stem.split_once('@')?;
    if instance.is_empty() {
        return None;
    }
    Some(format!("{prefix}@.{suffix}"))
}

/// The name a unit file is loaded under once alias links are followed.
fn canonical_name(root: &Path, file: &Path) -> Option<String> {
    let rel = file.strip_prefix(root).ok()?;
    let resolved = resolve_in_root(root, &format!("/{}", rel.to_string_lossy()))?;
    Some(resolved.file_name()?.to_string_lossy().to_string())
}

/// Split an Exec value into (prefix flags, program token).
fn split_command(value: &str) -> (String, String) {
    let flags: String = value
        .chars()
        .take_while(|c| matches!(c, '-' | '@' | ':' | '+' | '!' | '|'))
        .collect();
    let rest = value[flags.len()..].trim_start();
    let program = if let Some(q) = rest.strip_prefix('"') {
        q.split('"').next().unwrap_or("").to_string()
    } else if let Some(q) = rest.strip_prefix('\'') {
        q.split('\'').next().unwrap_or("").to_string()
    } else {
        rest.split_whitespace().next().unwrap_or("").to_string()
    };
    (flags, program)
}

fn check_scope(root: &Path, scope: &Scope, report: &mut Report) {
    let index = ScopeIndex {
        root,
        dirs: scope.dirs.iter().map(|d| root.join(d)).collect(),
    };
    let mut queue: VecDeque<String> = index.linked().into_iter().collect();
    let mut seen: BTreeSet<String> = BTreeSet::new();
    while let Some(name) = queue.pop_front() {
        if !seen.insert(name.clone()) {
            continue;
        }
        let Some(file) = index.unit_file(&name) else {
            continue;
        };
        if is_masked(root, &file) {
            continue;
        }
        let canonical = canonical_name(root, &file).unwrap_or_else(|| name.clone());
        let mut parsed = Parsed::default();
        let mut sources = vec![file.clone()];
        sources.extend(index.dropins(&name, &canonical));
        for src in &sources {
            let Some(resolved) = resolve_in_root(root, &index.rel(src)) else {
                continue;
            };
            if let Ok(text) = std::fs::read_to_string(&resolved) {
                parse_into(&mut parsed, &text, &index.rel(src));
            }
        }
        report.units += 1;
        for dep in &parsed.deps {
            queue.push_back(dep.clone());
        }
        if let Some((stem, kind)) = name.rsplit_once('.') {
            if matches!(kind, "timer" | "socket" | "path") {
                let triggered = parsed.unit_key.clone().unwrap_or_else(|| {
                    // An Accept=yes socket spawns instances of stem@.service.
                    let plain = format!("{stem}.service");
                    if index.unit_file(&plain).is_some() {
                        plain
                    } else {
                        format!("{stem}@.service")
                    }
                });
                queue.push_back(triggered);
            }
        }
        for key in EXEC_KEYS {
            for (value, source) in parsed.exec.get(*key).into_iter().flatten() {
                report.commands += 1;
                let (flags, program) = split_command(value);
                let at = format!("{} {name} {key}= ({source})", scope.name);
                if program.is_empty() {
                    report.failures.push(format!("{at}: empty command"));
                    continue;
                }
                if program.contains('$') || program.contains('%') {
                    report
                        .warnings
                        .push(format!("{at}: program {program} is resolved at runtime"));
                    continue;
                }
                let candidates: Vec<String> = if program.starts_with('/') {
                    vec![program.clone()]
                } else {
                    SEARCH_PATH
                        .iter()
                        .map(|d| format!("{d}/{program}"))
                        .collect()
                };
                if candidates.iter().any(|c| is_executable_file(root, c)) {
                    continue;
                }
                if candidates.iter().any(|c| parsed.guards.contains(c)) {
                    continue;
                }
                if flags.contains('-') {
                    report
                        .warnings
                        .push(format!("{at}: {program} is missing (ignored by '-')"));
                    continue;
                }
                report.failures.push(format!(
                    "{at}: {program} is not an executable file in the image"
                ));
            }
        }
    }
}

/// Check every boot-reachable system and user unit under `root`.
pub fn check(root: &Path) -> Result<Report, String> {
    let root = root
        .canonicalize()
        .map_err(|e| format!("image root {}: {e}", root.display()))?;
    let mut report = Report::default();
    for scope in SCOPES {
        check_scope(&root, scope, &mut report);
    }
    if report.units == 0 {
        return Err(format!(
            "no enabled systemd unit under {}: presets were never applied",
            root.display()
        ));
    }
    Ok(report)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::{symlink, PermissionsExt};

    struct Root(tempfile::TempDir);

    impl Root {
        fn new() -> Self {
            let r = Root(tempfile::tempdir().unwrap());
            r.dir("etc/systemd/system/multi-user.target.wants");
            r.dir("etc/systemd/system/timers.target.wants");
            r.dir("usr/lib/systemd/system");
            r
        }
        fn path(&self, rel: &str) -> PathBuf {
            self.0.path().join(rel)
        }
        fn dir(&self, rel: &str) {
            std::fs::create_dir_all(self.path(rel)).unwrap();
        }
        fn write(&self, rel: &str, text: &str) {
            let p = self.path(rel);
            std::fs::create_dir_all(p.parent().unwrap()).unwrap();
            std::fs::write(p, text).unwrap();
        }
        fn exe(&self, rel: &str) {
            self.write(rel, "#!/bin/sh\n");
            std::fs::set_permissions(self.path(rel), std::fs::Permissions::from_mode(0o755))
                .unwrap();
        }
        fn unit(&self, name: &str, text: &str) {
            self.write(&format!("usr/lib/systemd/system/{name}"), text);
        }
        fn enable(&self, wants: &str, name: &str) {
            symlink(
                format!("/usr/lib/systemd/system/{name}"),
                self.path(&format!("etc/systemd/system/{wants}/{name}")),
            )
            .unwrap();
        }
        fn check(&self) -> Report {
            check(self.0.path()).unwrap()
        }
    }

    /// The shape found on the PS5 Blade: an enabled timer whose service names a
    /// program the image does not carry.
    fn tailscale_sync(root: &Root) {
        root.unit(
            "mios-tailscale-sync.service",
            "[Service]\nType=oneshot\nExecStart=/usr/libexec/mios/mios-tailscale-sync\n",
        );
        root.unit("mios-tailscale-sync.timer", "[Timer]\nOnBootSec=1min\n");
        root.enable("timers.target.wants", "mios-tailscale-sync.timer");
    }

    #[test]
    fn timer_triggered_service_with_missing_program_fails() {
        let r = Root::new();
        tailscale_sync(&r);
        let rep = r.check();
        assert_eq!(rep.failures.len(), 1, "{:?}", rep.failures);
        assert!(rep.failures[0].contains("mios-tailscale-sync.service"));
        assert!(rep.failures[0].contains("/usr/libexec/mios/mios-tailscale-sync"));
    }

    #[test]
    fn timer_triggered_service_with_program_passes() {
        let r = Root::new();
        tailscale_sync(&r);
        r.exe("usr/libexec/mios/mios-tailscale-sync");
        let rep = r.check();
        assert!(rep.failures.is_empty(), "{:?}", rep.failures);
        assert_eq!(rep.units, 2);
    }

    #[test]
    fn non_executable_program_fails() {
        let r = Root::new();
        tailscale_sync(&r);
        r.write("usr/libexec/mios/mios-tailscale-sync", "#!/bin/sh\n");
        assert_eq!(r.check().failures.len(), 1);
    }

    #[test]
    fn symlinked_program_is_resolved_inside_the_root() {
        let r = Root::new();
        r.exe("usr/bin/podman");
        r.dir("usr");
        symlink("bin", r.path("usr/sbin")).unwrap();
        r.unit("a.service", "[Service]\nExecStart=/usr/sbin/podman ps\n");
        r.enable("multi-user.target.wants", "a.service");
        assert!(r.check().failures.is_empty());
        // An absolute link resolves against the image root, never the build host.
        r.unit("b.service", "[Service]\nExecStart=/usr/libexec/mios/sh\n");
        r.dir("usr/libexec/mios");
        symlink("/bin/sh-not-in-root", r.path("usr/libexec/mios/sh")).unwrap();
        r.enable("multi-user.target.wants", "b.service");
        let rep = r.check();
        assert_eq!(rep.failures.len(), 1, "{:?}", rep.failures);
        assert!(rep.failures[0].contains("b.service"));
    }

    #[test]
    fn disabled_units_are_not_checked() {
        let r = Root::new();
        r.unit("off.service", "[Service]\nExecStart=/usr/bin/absent\n");
        r.unit("on.service", "[Service]\nExecStart=/usr/bin/present\n");
        r.exe("usr/bin/present");
        r.enable("multi-user.target.wants", "on.service");
        let rep = r.check();
        assert!(rep.failures.is_empty(), "{:?}", rep.failures);
        assert_eq!(rep.units, 1);
    }

    #[test]
    fn dependency_edges_and_dropins_are_followed() {
        let r = Root::new();
        r.unit("role.target", "[Unit]\nWants=dep.service\n");
        r.unit("dep.service", "[Service]\nExecStart=/usr/bin/present\n");
        r.exe("usr/bin/present");
        r.write(
            "usr/lib/systemd/system/dep.service.d/10-x.conf",
            "[Service]\nExecStartPre=/usr/bin/absent-pre\n",
        );
        r.enable("multi-user.target.wants", "role.target");
        let rep = r.check();
        assert_eq!(rep.failures.len(), 1, "{:?}", rep.failures);
        assert!(rep.failures[0].contains("ExecStartPre"));
        assert!(rep.failures[0].contains("10-x.conf"));
    }

    #[test]
    fn empty_assignment_resets_earlier_commands() {
        let r = Root::new();
        r.unit("a.service", "[Service]\nExecStart=/usr/bin/absent\n");
        r.write(
            "usr/lib/systemd/system/a.service.d/10.conf",
            "[Service]\nExecStart=\nExecStart=/usr/bin/present\n",
        );
        r.exe("usr/bin/present");
        r.enable("multi-user.target.wants", "a.service");
        assert!(r.check().failures.is_empty());
    }

    #[test]
    fn condition_guard_and_dash_prefix_do_not_fail() {
        let r = Root::new();
        r.unit(
            "g.service",
            "[Unit]\nConditionFileIsExecutable=/usr/bin/opt\n[Service]\nExecStart=/usr/bin/opt\n",
        );
        r.unit(
            "d.service",
            "[Service]\nExecStartPre=-/usr/bin/opt\nExecStart=/usr/bin/opt2\n",
        );
        r.unit(
            "n.service",
            "[Unit]\nConditionPathExists=!/usr/bin/opt\n[Service]\nExecStart=/usr/bin/opt\n",
        );
        r.exe("usr/bin/opt2");
        for u in ["g.service", "d.service", "n.service"] {
            r.enable("multi-user.target.wants", u);
        }
        let rep = r.check();
        assert_eq!(rep.warnings.len(), 1, "{:?}", rep.warnings);
        // A negated condition never guards a missing program.
        assert_eq!(rep.failures.len(), 1, "{:?}", rep.failures);
        assert!(rep.failures[0].contains("n.service"));
    }

    #[test]
    fn masked_and_template_units() {
        let r = Root::new();
        r.unit("m.service", "[Service]\nExecStart=/usr/bin/absent\n");
        symlink("/dev/null", r.path("etc/systemd/system/m.service")).unwrap();
        r.enable("multi-user.target.wants", "m.service");
        r.unit("t@.service", "[Service]\nExecStart=/usr/bin/absent-t %i\n");
        symlink(
            "/usr/lib/systemd/system/t@.service",
            r.path("etc/systemd/system/multi-user.target.wants/t@one.service"),
        )
        .unwrap();
        let rep = r.check();
        assert_eq!(rep.failures.len(), 1, "{:?}", rep.failures);
        assert!(rep.failures[0].contains("t@one.service"));
    }

    #[test]
    fn bare_name_uses_systemd_search_path() {
        let r = Root::new();
        r.unit("a.service", "[Service]\nExecStart=present --flag\n");
        r.exe("usr/bin/present");
        r.enable("multi-user.target.wants", "a.service");
        assert!(r.check().failures.is_empty());
    }

    #[test]
    fn root_without_enabled_units_is_an_error() {
        let d = tempfile::tempdir().unwrap();
        assert!(check(d.path()).is_err());
    }
}
