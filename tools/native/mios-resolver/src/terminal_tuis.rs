// AI-hint: The one Apps and TUIs catalogue ([terminal.tui_launcher] + [[terminal.tuis]]) shared by the tmux-os launcher (mios-gen tmux-menu / tui-spec) and the miosd sessions selector: strict validation, console variants and the installed-only filter.
// AI-related: /usr/share/mios/mios.toml [terminal.tui_launcher] [[terminal.tuis]], tools/native/mios-gen/src/tmux_os.rs, src/mios-rs/miosd/src/sessions.rs, usr/libexec/mios/mios-terminal
// AI-functions: launcher, catalog, visible, is_installed, Tui::argv_for

use toml::Value;

/// How a TUI opens inside tmux-os.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Mode {
    /// Transient `display-popup` (Esc closes), run under the SSOT popup scope.
    Popup,
    /// A new window: long-lived, and targetable by MCP and the controller.
    Window,
    /// A native floating pane (tmux 3.7 `new-pane`).
    Float,
}

impl Mode {
    pub fn as_str(self) -> &'static str {
        match self {
            Mode::Popup => "popup",
            Mode::Window => "window",
            Mode::Float => "float",
        }
    }
}

#[derive(Clone, Debug)]
pub struct Tui {
    pub id: String,
    pub label: String,
    pub section: String,
    pub mode: Mode,
    pub vt_ok: bool,
    pub hold: bool,
    pub argv: Vec<String>,
    pub argv_vt: Option<Vec<String>>,
}

impl Tui {
    /// The argv to run: `argv_vt` on a Linux console when the row declares one.
    pub fn argv_for(&self, console: bool) -> &[String] {
        match (&self.argv_vt, console) {
            (Some(vt), true) => vt,
            _ => &self.argv,
        }
    }
}

/// `[terminal.tui_launcher]`.
#[derive(Clone, Debug)]
pub struct Launcher {
    pub enabled: bool,
    pub title: String,
    /// `session`: a window in the bare `[terminal.sessions].new_session_name` session; `new`: a new bare session.
    pub app_target: String,
    pub popup_width: String,
    pub popup_height: String,
    pub sections: Vec<String>,
    pub popup_scope: Vec<String>,
    /// Shown when nothing fits the client (or the launcher is disabled).
    pub empty_message: String,
}

fn get<'a>(v: &'a Value, path: &str) -> Result<&'a Value, String> {
    path.split('.')
        .try_fold(v, |cur, key| cur.get(key))
        .ok_or_else(|| format!("missing SSOT key {path}"))
}

fn string(v: &Value, path: &str) -> Result<String, String> {
    get(v, path)?.as_str().map(str::to_owned).ok_or_else(|| format!("{path} must be a string"))
}

fn strings(v: &Value, path: &str) -> Result<Vec<String>, String> {
    get(v, path)?
        .as_array()
        .ok_or_else(|| format!("{path} must be an array of strings"))?
        .iter()
        .map(|s| s.as_str().map(str::to_owned).ok_or_else(|| format!("{path} must contain only strings")))
        .collect()
}

/// Shown text: printable ASCII (the Linux console font and every tmux quoting level) without quotes,
/// `#`, braces, `$` or backslash.
fn plain(s: &str) -> bool {
    !s.is_empty() && s.len() <= 60 && s.bytes().all(|b| (0x20..0x7f).contains(&b) && !b"'\"#{}$\\".contains(&b))
}

fn percent(s: &str) -> bool {
    s.strip_suffix('%')
        .filter(|n| (1..=3).contains(&n.len()) && n.bytes().all(|b| b.is_ascii_digit()))
        .and_then(|n| n.parse::<u32>().ok())
        .is_some_and(|n| (1..=100).contains(&n))
}

fn argv_ok(argv: &[String]) -> bool {
    !argv.is_empty() && !argv[0].is_empty() && argv.iter().all(|a| !a.chars().any(char::is_control))
}

pub fn launcher(merged: &Value) -> Result<Launcher, String> {
    let l = Launcher {
        enabled: get(merged, "terminal.tui_launcher.enabled")?
            .as_bool()
            .ok_or("terminal.tui_launcher.enabled must be a boolean")?,
        title: string(merged, "terminal.tui_launcher.title")?,
        app_target: string(merged, "terminal.tui_launcher.app_target")?,
        popup_width: string(merged, "terminal.tui_launcher.popup_width")?,
        popup_height: string(merged, "terminal.tui_launcher.popup_height")?,
        sections: strings(merged, "terminal.tui_launcher.sections")?,
        popup_scope: strings(merged, "terminal.tui_launcher.popup_scope")?,
        empty_message: string(merged, "terminal.tui_launcher.empty_message")?,
    };
    if !plain(&l.empty_message) {
        return Err("terminal.tui_launcher.empty_message must be plain ASCII text".into());
    }
    if !plain(&l.title) {
        return Err("terminal.tui_launcher.title must be plain ASCII text".into());
    }
    if !matches!(l.app_target.as_str(), "session" | "new") {
        return Err("terminal.tui_launcher.app_target must be session or new".into());
    }
    if !percent(&l.popup_width) || !percent(&l.popup_height) {
        return Err("terminal.tui_launcher.popup_width/popup_height must be percentages 1%..100%".into());
    }
    if l.sections.is_empty() || l.sections.iter().any(|s| !valid_id(s)) {
        return Err("terminal.tui_launcher.sections must be non-empty [a-z0-9-] ids".into());
    }
    if !argv_ok(&l.popup_scope) {
        return Err("terminal.tui_launcher.popup_scope must be a non-empty argv".into());
    }
    Ok(l)
}

fn valid_id(s: &str) -> bool {
    !s.is_empty() && s.len() <= 32 && s.bytes().all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
}

/// Every `[[terminal.tuis]]` row, validated, in SSOT order.
pub fn catalog(merged: &Value) -> Result<Vec<Tui>, String> {
    let sections = strings(merged, "terminal.tui_launcher.sections")?;
    let rows = get(merged, "terminal.tuis")?.as_array().ok_or("[[terminal.tuis]] must be an array of tables")?;
    let mut out: Vec<Tui> = Vec::new();
    for (i, r) in rows.iter().enumerate() {
        let id = string(r, "id").map_err(|e| format!("terminal.tuis[{i}]: {e}"))?;
        let at = |e: String| format!("terminal.tuis {id}: {e}");
        if !valid_id(&id) || out.iter().any(|t| t.id == id) {
            return Err(format!("terminal.tuis[{i}]: duplicate or invalid id {id:?}"));
        }
        let label = string(r, "label").map_err(at)?;
        if !plain(&label) {
            return Err(at(format!("label {label:?} must be plain ASCII text")));
        }
        let section = string(r, "section").map_err(at)?;
        if !sections.contains(&section) {
            return Err(at(format!("section {section:?} is not in terminal.tui_launcher.sections")));
        }
        let mode = match string(r, "mode").map_err(at)?.as_str() {
            "popup" => Mode::Popup,
            "window" => Mode::Window,
            "float" => Mode::Float,
            other => return Err(at(format!("mode {other:?} must be popup, window or float"))),
        };
        let vt_ok = get(r, "vt_ok").ok().map(|v| v.as_bool().ok_or("vt_ok must be a boolean")).transpose().map_err(|e| at(e.into()))?.unwrap_or(true);
        let hold = get(r, "hold").ok().map(|v| v.as_bool().ok_or("hold must be a boolean")).transpose().map_err(|e| at(e.into()))?.unwrap_or(false);
        if hold && mode != Mode::Popup {
            return Err(at("hold is only valid for mode = \"popup\"".into()));
        }
        let argv = strings(r, "argv").map_err(at)?;
        if !argv_ok(&argv) {
            return Err(at("argv must be a non-empty argv without control characters".into()));
        }
        let argv_vt = match r.get("argv_vt") {
            None => None,
            Some(_) => {
                let v = strings(r, "argv_vt").map_err(at)?;
                if !argv_ok(&v) {
                    return Err(at("argv_vt must be a non-empty argv without control characters".into()));
                }
                if !vt_ok {
                    return Err(at("argv_vt requires vt_ok = true".into()));
                }
                Some(v)
            }
        };
        out.push(Tui { id, label, section, mode, vt_ok, hold, argv, argv_vt });
    }
    Ok(out)
}

/// `true` when argv[0] can run: an absolute path must be an executable file; a bare name is searched in PATH.
pub fn is_installed(argv0: &str) -> bool {
    fn executable(p: &std::path::Path) -> bool {
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::metadata(p).is_ok_and(|m| m.is_file() && m.permissions().mode() & 0o111 != 0)
        }
        #[cfg(not(unix))]
        {
            p.is_file()
        }
    }
    if argv0.contains('/') {
        return argv0.starts_with('/') && executable(std::path::Path::new(argv0));
    }
    std::env::var_os("PATH")
        .map(|path| std::env::split_paths(&path).any(|d| executable(&d.join(argv0))))
        .unwrap_or(false)
}

/// Rows a client may launch, in SSOT order: installed (per `is_exec` on the argv actually run) and, on a
/// Linux console, only `vt_ok` rows.
pub fn visible<'a>(tuis: &'a [Tui], console: bool, is_exec: impl Fn(&str) -> bool) -> Vec<&'a Tui> {
    tuis.iter()
        .filter(|t| !console || t.vt_ok)
        .filter(|t| is_exec(&t.argv_for(console)[0]))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn doc(rows: &str) -> Value {
        format!(
            "[terminal.tui_launcher]\nenabled = true\ntitle = \"Apps\"\napp_target = \"session\"\npopup_width = \"90%\"\npopup_height = \"80%\"\nsections = [\"mios\", \"system\"]\npopup_scope = [\"systemd-run\", \"--user\", \"--scope\", \"--\"]\nempty_message = \"Nothing\"\n{rows}"
        )
        .parse()
        .unwrap()
    }

    const OK: &str = "[[terminal.tuis]]\nid = \"btop\"\nlabel = \"btop\"\nsection = \"system\"\nmode = \"window\"\nargv = [\"btop\"]\nargv_vt = [\"btop\", \"-t\"]\n[[terminal.tuis]]\nid = \"gone\"\nlabel = \"Missing\"\nsection = \"mios\"\nmode = \"popup\"\nvt_ok = false\nargv = [\"/nonexistent/x\"]\n";

    #[test]
    fn the_shipped_ssot_catalogue_validates() {
        let ssot: Value = include_str!("../../../../usr/share/mios/mios.toml").parse().unwrap();
        let cat = catalog(&ssot).unwrap();
        assert!(cat.len() >= 10);
        assert!(launcher(&ssot).unwrap().enabled);
        // Heavy TUIs never default to a popup (popups share no per-pane OOM scope).
        for heavy in ["btop", "ncdu", "nvtop", "yazi", "ranger", "mc"] {
            assert_ne!(cat.iter().find(|t| t.id == heavy).unwrap().mode, Mode::Popup, "{heavy}");
        }
    }

    #[test]
    fn console_selection_and_installed_filter() {
        let d = doc(OK);
        let cat = catalog(&d).unwrap();
        assert_eq!(cat[0].argv_for(true), ["btop", "-t"]);
        assert_eq!(cat[0].argv_for(false), ["btop"]);
        let all = |_: &str| true;
        assert_eq!(visible(&cat, false, all).len(), 2);
        assert_eq!(visible(&cat, true, all).iter().map(|t| t.id.as_str()).collect::<Vec<_>>(), ["btop"]);
        let only_btop = |a: &str| a == "btop";
        assert_eq!(visible(&cat, false, only_btop).len(), 1);
        assert!(!is_installed("/nonexistent/x") && !is_installed("relative/x"));
        assert!(is_installed("/bin/sh") || is_installed("sh"));
    }

    #[test]
    fn validation_errors_name_the_row() {
        for (bad, why) in [
            (OK.replace("id = \"gone\"", "id = \"btop\""), "duplicate"),
            (OK.replace("label = \"Missing\"", "label = \"it's\""), "plain ASCII"),
            (OK.replace("section = \"mios\"", "section = \"nope\""), "not in"),
            (OK.replace("mode = \"popup\"", "mode = \"tab\""), "popup, window or float"),
            (OK.replace("argv = [\"/nonexistent/x\"]", "argv = []"), "non-empty"),
            (OK.replace("mode = \"window\"", "mode = \"window\"\nhold = true"), "hold"),
            (OK.replace("argv_vt = [\"btop\", \"-t\"]", "argv_vt = [\"btop\"]\nvt_ok = false"), "vt_ok"),
        ] {
            let err = catalog(&doc(&bad)).unwrap_err();
            assert!(err.contains(why), "{why}: {err}");
        }
        let mut d = doc(OK);
        d["terminal"]["tui_launcher"]["app_target"] = "default".into();
        assert!(launcher(&d).unwrap_err().contains("app_target"));
    }
}
