// AI-hint: mios-controls validation: renders the draft's compositor Lua through mios-gen (stdout only, the draft as a private user tier), runs the gate's keybind-collision analysis over the merged draft, adds controller system-layer checks, and reports only findings the draft introduces.
// AI-related: src/mios-rs/mios-gate/src/keybinds.rs, tools/native/mios-gen/src/global_bindings.rs, tools/native/mios-controls/src/model.rs
// AI-functions: Validator::run, render_lua, controller_findings, Outcome::new_findings
use crate::model::{at, str_list, Layers};
use std::collections::BTreeSet;
use std::io::Write;
use std::os::unix::fs::OpenOptionsExt;
use std::path::PathBuf;
use std::process::{Command, Stdio};
use toml::Value;

#[derive(Clone, Debug, Default)]
pub struct Outcome {
    pub findings: Vec<String>,
    pub baseline: BTreeSet<String>,
    pub summary: String,
}

impl Outcome {
    /// Findings the draft introduces; a defect already present below the draft never blocks an unrelated edit.
    pub fn new_findings(&self) -> Vec<&String> {
        self.findings.iter().filter(|f| !self.baseline.contains(*f)).collect()
    }
    pub fn mentions(&self, id: &str) -> Vec<&String> {
        let needle = [format!("{id}:"), format!("{id} ["), format!("{id}#")];
        self.findings.iter().filter(|f| needle.iter().any(|n| f.contains(n.as_str()))).collect()
    }
}

#[derive(Clone)]
pub struct Validator {
    pub mios_gen: PathBuf,
}

impl Validator {
    /// `MIOS_GEN` (a candidate generator under test, as mios-sync-theme reads it) wins over
    /// [desktop.controls].mios_gen.
    pub fn from_ssot(merged: &Value) -> Self {
        if let Some(p) = std::env::var_os("MIOS_GEN").filter(|p| !p.is_empty()) {
            return Self { mios_gen: PathBuf::from(p) };
        }
        let mios_gen = at(merged, "desktop.controls.mios_gen")
            .and_then(Value::as_str)
            .filter(|s| !s.is_empty())
            .unwrap_or("/usr/bin/mios-gen");
        Self { mios_gen: PathBuf::from(mios_gen) }
    }

    /// The Lua session the compositor would load for `user_text` as the user tier. mios-gen prints it with
    /// --runtime and writes nothing; the draft lives in a private 0600 file only for the call.
    pub fn render_lua(&self, layers: &Layers, user_text: &str) -> Result<String, String> {
        let dir = std::env::var_os("XDG_RUNTIME_DIR").map(PathBuf::from).filter(|p| p.is_dir()).unwrap_or_else(std::env::temp_dir);
        let path = dir.join(format!(".mios-controls-draft-{}.toml", std::process::id()));
        let _cleanup = Remove(path.clone());
        let mut f = std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(&path)
            .map_err(|e| format!("{}: {e}", path.display()))?;
        f.write_all(user_text.as_bytes()).map_err(|e| e.to_string())?;
        drop(f);
        let root = layers.root.display().to_string();
        let mut cmd = Command::new(&self.mios_gen);
        if crate::model::rooted(&layers.root) {
            // the explicit root's vendor tier, never the session's MIOS_TOML / MIOS_VENDOR_TOML
            cmd.env_remove("MIOS_TOML").env_remove("MIOS_VENDOR_TOML_D").env("MIOS_VENDOR_TOML", layers.root.join("usr/share/mios/mios.toml"));
        }
        let out = cmd
            .args(["render-hyprland", "--root", &root, "--runtime"])
            .env("MIOS_USER_TOML", &path)
            .env("MIOS_TOML_ROOT", &root)
            .env("MIOS_HOST_TOML", layers.root.join("etc/mios/mios.toml"))
            .stdin(Stdio::null())
            .output()
            .map_err(|e| format!("{}: {e}", self.mios_gen.display()))?;
        if !out.status.success() {
            let err = String::from_utf8_lossy(&out.stderr);
            return Err(format!("render-hyprland rejected the draft: {}", err.lines().last().unwrap_or("").trim()));
        }
        String::from_utf8(out.stdout).map_err(|_| "render-hyprland printed non-UTF-8 Lua".into())
    }

    fn analyze(&self, layers: &Layers, user_text: &str) -> Result<(Vec<String>, String), String> {
        let user = crate::model::parse_value(user_text)?;
        let merged = layers.merged_with(user);
        let lua = self.render_lua(layers, user_text)?;
        let a = crate::keybinds::analyze(&merged, Some(&lua))?;
        let mut findings = a.findings;
        findings.extend(controller_findings(&merged));
        let summary = format!("{} binding(s), {} compositor bind line(s), {} finding(s)", a.bindings, a.lua_literal, findings.len());
        Ok((findings, summary))
    }

    /// Validate `draft_text` (the projected user tier) against the current user tier as the baseline.
    pub fn run(&self, layers: &Layers, draft_text: &str) -> Result<Outcome, String> {
        let (baseline, _) = self.analyze(layers, &layers.user_text)?;
        let (findings, summary) = if draft_text == layers.user_text {
            let s = format!("{} finding(s) (no changes)", baseline.len());
            (baseline.clone(), s)
        } else {
            self.analyze(layers, draft_text)?
        };
        Ok(Outcome { findings, baseline: baseline.into_iter().collect(), summary })
    }
}

struct Remove(PathBuf);
impl Drop for Remove {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.0);
    }
}

/// The controller system layer: real evdev button names, never the guide button itself (it is the layer's
/// modifier), and actions under the same namespace/implemented rules the keyboard bindings follow.
pub fn controller_findings(merged: &Value) -> Vec<String> {
    let mut out = Vec::new();
    let Some(layer) = at(merged, "keybindings.global.controller.system_layer").and_then(Value::as_table) else {
        return out;
    };
    let guide = at(merged, "keybindings.global.controller.guide_button").and_then(Value::as_str).unwrap_or("BTN_MODE");
    let namespaces: BTreeSet<String> = str_list(at(merged, "keybindings.global.action_namespaces")).into_iter().collect();
    let planned: BTreeSet<String> = str_list(at(merged, "keybindings.global.planned_actions")).into_iter().collect();
    let implemented: BTreeSet<String> = at(merged, "keybindings.global.action_argv")
        .and_then(Value::as_table)
        .map(|t| t.keys().cloned().collect())
        .unwrap_or_default();
    let native: BTreeSet<String> = at(merged, "keybindings.global.native_actions")
        .and_then(Value::as_table)
        .map(|t| t.keys().cloned().collect())
        .unwrap_or_default();
    for (button, action) in layer {
        let tag = format!("controller.system_layer.{button}");
        if let Err(e) = mios_resolver::keybindings::button_code(button) {
            out.push(format!("{tag}: {e}"));
        }
        if button == guide {
            out.push(format!("{tag}: the guide button {guide} is the system layer's modifier and cannot carry an action"));
        }
        let Some(action) = action.as_str() else {
            out.push(format!("{tag}: action must be a string"));
            continue;
        };
        match action.split_once(':') {
            Some((ns, verb)) if !verb.is_empty() && namespaces.contains(ns) => {
                if (ns == "quickshell" || ns == "mios") && !implemented.contains(action) && !planned.contains(action) && !native.contains(action) {
                    out.push(format!("{tag}: action {action:?} is neither in [keybindings.global.action_argv] nor planned_actions"));
                }
            }
            _ => out.push(format!("{tag}: action {action:?} must be <namespace>:<verb> with a namespace in action_namespaces")),
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn merged(layer: &str) -> Value {
        format!(
            "[keybindings.global]\naction_namespaces=[\"hypr\",\"exec\",\"mios\"]\n[keybindings.global.action_argv]\n\"mios:ok\"=[\"/bin/true\"]\n[keybindings.global.controller]\nguide_button=\"BTN_MODE\"\n[keybindings.global.controller.system_layer]\n{layer}"
        )
        .parse()
        .unwrap()
    }

    #[test]
    fn valid_system_layer_has_no_findings() {
        assert!(controller_findings(&merged("BTN_SOUTH=\"hypr:togglefloating\"\nBTN_WEST=\"mios:ok\"\n")).is_empty());
    }

    #[test]
    fn system_layer_rejects_bad_buttons_guide_namespaces_and_unimplemented_actions() {
        let f = controller_findings(&merged("BTN_BOGUS=\"hypr:x\"\nBTN_MODE=\"hypr:x\"\nBTN_SOUTH=\"nope:x\"\nBTN_WEST=\"mios:missing\"\nBTN_EAST=\"hypr:\"\n"));
        assert_eq!(f.len(), 5, "{f:#?}");
        assert!(f.iter().any(|x| x.contains("BTN_BOGUS") && x.contains("unknown controller button")));
        assert!(f.iter().any(|x| x.contains("BTN_MODE") && x.contains("modifier")));
        assert!(f.iter().any(|x| x.contains("BTN_SOUTH") && x.contains("namespace")));
        assert!(f.iter().any(|x| x.contains("BTN_WEST") && x.contains("planned_actions")));
        assert!(f.iter().any(|x| x.contains("BTN_EAST")));
    }

    #[test]
    fn new_findings_exclude_the_baseline() {
        let o = Outcome {
            findings: vec!["old".into(), "new".into()],
            baseline: ["old".to_string()].into_iter().collect(),
            summary: String::new(),
        };
        assert_eq!(o.new_findings(), vec![&"new".to_string()]);
    }
}
