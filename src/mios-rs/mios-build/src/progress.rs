// AI-hint: One persisted, validated build progress ledger drives native Linux and Windows console events.
// AI-related: /usr/share/mios/templates/rust, automation/build.sh, src/mios-rs/miosd/src/main.rs
use serde::{Deserialize, Serialize};
use std::fs::{self, OpenOptions};
use std::io::{Read, Write};
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Status {
    Pass,
    Fail,
    Warn,
    Skip,
    Missing,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Completed {
    pub name: String,
    pub status: Status,
    pub seconds: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Progress {
    pub schema: String,
    pub plan: Vec<String>,
    pub completed: Vec<Completed>,
    pub active_since: Option<u64>,
    pub started: u64,
    pub notes: Vec<(Status, String)>,
    pub width: usize,
    pub bar_width: usize,
}

fn label(text: &str) -> Result<(), String> {
    if text.trim().is_empty() || text.len() > 4096 || text.chars().any(char::is_control) {
        return Err("Progress identity is empty, oversized or contains control characters".into());
    }
    Ok(())
}

impl Progress {
    pub fn new(
        plan: Vec<String>,
        now: u64,
        width: usize,
        bar_width: usize,
    ) -> Result<Self, String> {
        let value = Self {
            schema: "mios.build.progress.v1".into(),
            plan,
            completed: Vec::new(),
            active_since: None,
            started: now,
            notes: Vec::new(),
            width,
            bar_width,
        };
        value.validate(now)?;
        Ok(value)
    }

    fn validate(&self, now: u64) -> Result<(), String> {
        if self.schema != "mios.build.progress.v1"
            || self.plan.is_empty()
            || self.plan.len() > 4096
            || !(64..=240).contains(&self.width)
            || !(8..=40).contains(&self.bar_width)
        {
            return Err("Invalid build progress schema, plan or console dimensions".into());
        }
        let mut seen = std::collections::BTreeSet::new();
        for name in &self.plan {
            label(name)?;
            if !seen.insert(name) {
                return Err(format!("Duplicate build stage {name}"));
            }
        }
        if self.completed.len() > self.plan.len()
            || self.started > now
            || self
                .active_since
                .is_some_and(|time| time < self.started || time > now)
        {
            return Err("Invalid build progress count or clock".into());
        }
        for (result, planned) in self.completed.iter().zip(&self.plan) {
            if &result.name != planned {
                return Err("Build progress outcomes do not match the execution plan".into());
            }
        }
        if self.active_since.is_some() && self.completed.len() == self.plan.len() {
            return Err("Completed build cannot have an active stage".into());
        }
        for (_, message) in &self.notes {
            label(message)?;
        }
        Ok(())
    }

    pub fn start(&mut self, name: &str, now: u64) -> Result<(), String> {
        self.validate(now)?;
        if self.active_since.is_some()
            || self.plan.get(self.completed.len()).map(String::as_str) != Some(name)
        {
            return Err(format!("Out-of-order or duplicate stage start: {name}"));
        }
        self.active_since = Some(now);
        Ok(())
    }

    pub fn complete(&mut self, name: &str, status: Status, now: u64) -> Result<(), String> {
        self.validate(now)?;
        if self.plan.get(self.completed.len()).map(String::as_str) != Some(name) {
            return Err(format!("Unexpected build result: {name}"));
        }
        let since = self
            .active_since
            .take()
            .ok_or("Build result has no matching start")?;
        self.completed.push(Completed {
            name: name.into(),
            status,
            seconds: now - since,
        });
        Ok(())
    }

    pub fn note(&mut self, status: Status, message: String, now: u64) -> Result<(), String> {
        self.validate(now)?;
        label(&message)?;
        if status == Status::Pass {
            return Err("A finding must not increment stage pass counts".into());
        }
        self.notes.push((status, message));
        Ok(())
    }

    pub fn successful(&self) -> bool {
        self.completed.len() == self.plan.len()
            && self.active_since.is_none()
            && !self
                .completed
                .iter()
                .any(|r| matches!(r.status, Status::Fail | Status::Missing))
            && !self
                .notes
                .iter()
                .any(|(status, _)| matches!(status, Status::Fail | Status::Missing))
    }

    pub fn render(&self, marker: &str, name: &str, now: u64) -> Result<String, String> {
        self.validate(now)?;
        label(marker)?;
        label(name)?;
        let done = self.completed.len();
        let filled = done * self.bar_width / self.plan.len();
        let bar = format!(
            "{}{}",
            "=".repeat(filled),
            ".".repeat(self.bar_width - filled)
        );
        let duration = |seconds: u64| {
            format!(
                "{:02}:{:02}:{:02}",
                seconds / 3600,
                seconds / 60 % 60,
                seconds % 60
            )
        };
        let stage = self
            .active_since
            .map(|t| now - t)
            .or_else(|| self.completed.last().map(|r| r.seconds))
            .unwrap_or(0);
        let counts = [
            Status::Pass,
            Status::Fail,
            Status::Warn,
            Status::Skip,
            Status::Missing,
        ]
        .map(|status| self.completed.iter().filter(|r| r.status == status).count());
        let findings = [Status::Fail, Status::Warn, Status::Skip, Status::Missing]
            .map(|status| self.notes.iter().filter(|(s, _)| *s == status).count());
        let mut text = String::new();
        let header = format!(
            "[{marker}] {done}/{} {:3}% [{bar}] total {} stage {}",
            self.plan.len(),
            done * 100 / self.plan.len(),
            duration(now - self.started),
            duration(stage)
        );
        for chunk in header.chars().collect::<Vec<_>>().chunks(self.width) {
            text.extend(chunk);
            text.push('\n');
        }
        // Wrap instead of silently truncating stage identities or diagnostics.
        for content in [
            name.to_owned(),
            format!(
                "Stages: PASS {} FAIL {} WARN {} SKIP {} MISSING {} PENDING {}",
                counts[0],
                counts[1],
                counts[2],
                counts[3],
                counts[4],
                self.plan.len() - done
            ),
            format!(
                "Findings: FAIL {} WARN {} SKIP {} MISSING {}",
                findings[0], findings[1], findings[2], findings[3]
            ),
        ] {
            let chars: Vec<_> = content.chars().collect();
            for chunk in chars.chunks(self.width - 2) {
                text.push_str("  ");
                text.extend(chunk);
                text.push('\n');
            }
        }
        Ok(text)
    }
}

/// The post-build roster is editable SSOT; action identifiers select fixed,
/// reviewed adapter functions and can never inject a shell command.
pub fn post_plan(root: &Path) -> Result<Vec<(String, String)>, String> {
    let config = mios_resolver::resolve_merged(Some(root), false).map_err(|e| e.to_string())?;
    let steps = config
        .get("pipeline")
        .and_then(|v| v.get("console"))
        .and_then(|v| v.get("post_steps"))
        .and_then(toml::Value::as_array)
        .ok_or("Missing SSOT pipeline.console.post_steps")?;
    if steps.is_empty() || steps.len() > 128 {
        return Err("Post-build plan must contain 1..128 steps".into());
    }
    let mut names = std::collections::BTreeSet::new();
    let mut actions = std::collections::BTreeSet::new();
    let mut plan: Vec<(String, String)> = Vec::new();
    for step in steps {
        let name = step
            .get("name")
            .and_then(toml::Value::as_str)
            .ok_or("Post-build step name missing")?;
        let action = step
            .get("action")
            .and_then(toml::Value::as_str)
            .ok_or("Post-build step action missing")?;
        if name.is_empty()
            || !name
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"-_.".contains(&b))
            || !names.insert(name)
        {
            return Err(format!("Invalid or duplicate post-build identity: {name}"));
        }
        if !matches!(
            action,
            "bloat"
                | "package_health"
                | "invariants"
                | "ssot"
                | "drift"
                | "agent_tests"
                | "libexec_tests"
                | "image_digests"
                | "log_chain"
                | "finalize"
        ) || !actions.insert(action)
        {
            return Err(format!("Invalid or duplicate post-build action: {action}"));
        }
        plan.push((name.into(), action.into()));
    }
    // These gates/finalization are execution contracts, not optional UI rows.
    for required in [
        "package_health",
        "invariants",
        "ssot",
        "drift",
        "agent_tests",
        "libexec_tests",
        "image_digests",
        "log_chain",
        "finalize",
    ] {
        if !actions.contains(required) {
            return Err(format!("Required post-build action missing: {required}"));
        }
    }
    if plan.last().map(|(_, action)| action.as_str()) != Some("finalize") {
        return Err("Post-build finalize must be last".into());
    }
    let chain = plan
        .iter()
        .position(|(_, action)| action == "log_chain")
        .ok_or("Post-build log chain missing")?;
    if chain + 2 != plan.len() {
        return Err("Post-build log chain must follow every validation gate".into());
    }
    Ok(plan)
}

pub fn run(
    root: &Path,
    path: &Path,
    event: &str,
    name: Option<&str>,
    status: Option<Status>,
) -> Result<bool, String> {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|e| e.to_string())?
        .as_secs();
    let lock_path = path.with_extension("lock");
    let lock = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&lock_path)
        .map_err(|e| format!("Progress lock {}: {e}", lock_path.display()))?;
    struct Guard<'a> {
        path: &'a Path,
        file: Option<fs::File>,
    }
    impl Drop for Guard<'_> {
        fn drop(&mut self) {
            // Windows cannot unlink an open lock file. Close on every return,
            // including malformed input and refused duplicate events.
            self.file.take();
            let _ = fs::remove_file(self.path);
        }
    }
    let _guard = Guard {
        path: &lock_path,
        file: Some(lock),
    };
    let mut state = if event == "init" {
        if path.exists() {
            return Err("Refusing to overwrite an existing build progress receipt".into());
        }
        let mut input = String::new();
        std::io::stdin()
            .take(1024 * 1024 + 1)
            .read_to_string(&mut input)
            .map_err(|e| e.to_string())?;
        if input.len() > 1024 * 1024 {
            return Err("Build plan exceeds 1 MiB".into());
        }
        let plan = input.lines().map(str::to_owned).collect();
        let config = mios_resolver::resolve_merged(Some(root), false).map_err(|e| e.to_string())?;
        let console = config
            .get("pipeline")
            .and_then(|value| value.get("console"))
            .ok_or("Missing SSOT pipeline.console")?;
        let dimension = |key: &str| {
            console
                .get(key)
                .and_then(toml::Value::as_integer)
                .and_then(|v| usize::try_from(v).ok())
                .ok_or_else(|| format!("Missing SSOT pipeline.console.{key}"))
        };
        Progress::new(plan, now, dimension("width")?, dimension("bar_width")?)?
    } else {
        let data = fs::read(path).map_err(|e| format!("Build progress {}: {e}", path.display()))?;
        if data.len() > 8 * 1024 * 1024 {
            return Err("Build progress receipt exceeds 8 MiB".into());
        }
        serde_json::from_slice::<Progress>(&data).map_err(|e| e.to_string())?
    };
    state.validate(now)?;
    let marker;
    let display;
    match event {
        "init" => {
            marker = "PLAN";
            display = "MiOS build execution plan".into();
        }
        "start" => {
            let name = name.ok_or("Stage start requires a name")?;
            state.start(name, now)?;
            marker = "RUN";
            display = name.to_owned();
        }
        "result" => {
            let status = status.ok_or("Stage result requires a status")?;
            marker = match status {
                Status::Pass => "PASS",
                Status::Fail => "FAIL",
                Status::Warn => "WARN",
                Status::Skip => "SKIP",
                Status::Missing => "MISSING",
            };
            let name = name.ok_or("Stage result requires a name")?;
            state.complete(name, status, now)?;
            display = name.to_owned();
        }
        "note" => {
            let status = status.ok_or("Finding requires a status")?;
            let message = name.ok_or("Finding requires a message")?.to_owned();
            state.note(status, message.clone(), now)?;
            marker = "NOTE";
            display = message;
        }
        "finish" => {
            marker = if !state.successful() {
                "FAIL"
            } else if state.completed.iter().any(|r| r.status != Status::Pass)
                || !state.notes.is_empty()
            {
                "DEGRADED"
            } else {
                "PASS"
            };
            display = "MiOS build final receipt".into();
        }
        _ => return Err(format!("Unknown build progress event {event}")),
    }
    let output = state.render(marker, &display, now)?;
    let mut receipt =
        tempfile::NamedTempFile::new_in(path.parent().ok_or("Progress receipt requires a parent")?)
            .map_err(|e| e.to_string())?;
    serde_json::to_writer_pretty(&mut receipt, &state).map_err(|e| e.to_string())?;
    receipt.flush().map_err(|e| e.to_string())?;
    receipt.as_file().sync_all().map_err(|e| e.to_string())?;
    receipt.persist(path).map_err(|e| e.to_string())?;
    print!("{output}");
    Ok(event != "finish" || state.successful())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn one_counter_covers_every_status_and_retains_long_stage_identity() -> Result<(), String> {
        let names: Vec<_> = (0..5)
            .map(|i| format!("stage-{i}-{}", "x".repeat(100)))
            .collect();
        let mut state = Progress::new(names.clone(), 100, 80, 24)?;
        for (i, status) in [
            Status::Pass,
            Status::Warn,
            Status::Skip,
            Status::Missing,
            Status::Fail,
        ]
        .into_iter()
        .enumerate()
        {
            state.start(&names[i], 101 + i as u64 * 3)?;
            state.complete(&names[i], status, 103 + i as u64 * 3)?;
        }
        state.note(
            Status::Skip,
            "runtime DB test requires live database".into(),
            116,
        )?;
        let rendered = state.render("FAIL", &names[4], 116)?;
        assert!(rendered.contains("5/5 100%"));
        assert!(rendered.contains("PASS 1 FAIL 1 WARN 1 SKIP 1 MISSING 1 PENDING 0"));
        assert!(rendered.contains("Findings: FAIL 0 WARN 0 SKIP 1 MISSING 0"));
        assert!(rendered.replace(['\n', ' '], "").contains(&names[4]));
        assert!(!state.successful());
        state.width = 64;
        assert!(state
            .render("MISSING", &names[4], 116)?
            .lines()
            .all(|line| line.chars().count() <= 64));
        Ok(())
    }
    #[test]
    fn empty_duplicate_out_of_order_missing_results_and_clock_rollback_fail() -> Result<(), String>
    {
        assert!(Progress::new(vec![], 100, 80, 24).is_err());
        assert!(Progress::new(vec!["a".into(), "a".into()], 100, 80, 24).is_err());
        let mut state = Progress::new(vec!["a".into(), "b".into()], 100, 80, 24)?;
        assert!(state.start("b", 101).is_err());
        assert!(state.complete("a", Status::Pass, 101).is_err());
        state.start("a", 101)?;
        assert!(state.start("a", 102).is_err());
        assert!(state.complete("a", Status::Pass, 99).is_err());
        state.complete("a", Status::Pass, 103)?;
        assert!(!state.successful());
        state.start("b", 104)?;
        state.complete("b", Status::Pass, 105)?;
        assert!(state.successful());
        assert!(state.complete("b", Status::Pass, 106).is_err());
        state.note(Status::Missing, "required compiler absent".into(), 106)?;
        assert!(!state.successful());
        Ok(())
    }
}
