// AI-hint: Fast native static CLI launcher for browser dispatch based on [browser.family] and [browser.flags] (task_948).
// AI-related: usr/share/mios/mios.toml, usr/libexec/mios/mios-open-url
// AI-functions: main, resolve_family, resolve_flags, build_browser_command

use clap::Parser;
use serde::{Deserialize, Serialize};
use std::path::Path;
use std::process::{Command, ExitCode};
use toml::Value;

#[derive(Parser, Debug, Clone)]
#[command(
    name = "mios-browser",
    about = "Browser launcher resolving browser families and flags from MiOS SSOT (task_948)",
    version
)]
pub struct Cli {
    /// URL to open (e.g. https://example.com, about:blank, file://...)
    #[arg(value_name = "URL", default_value = "")]
    pub url: String,

    /// Browser binary or shortname override (e.g. firefox, zen, chrome, brave)
    #[arg(short = 'b', long = "browser", value_name = "BROWSER")]
    pub browser: Option<String>,

    /// Presentation mode: tab, window, new-window, private
    #[arg(short = 'm', long = "mode", value_name = "MODE", default_value = "tab")]
    pub mode: String,

    /// Open in private/incognito window (overrides mode to private)
    #[arg(short = 'p', long = "private", default_value_t = false)]
    pub private: bool,

    /// Reuse running browser instance (if false, opens new window)
    #[arg(short = 'r', long = "reuse", default_value_t = true)]
    pub reuse: bool,

    /// Browser profile name (optional)
    #[arg(long = "profile", value_name = "PROFILE")]
    pub profile: Option<String>,

    /// Dry run: resolve and print structured command JSON without executing
    #[arg(long = "dry-run", default_value_t = false)]
    pub dry_run: bool,

    /// Explain resolution details (browser, family, flags, URL)
    #[arg(long = "explain", default_value_t = false)]
    pub explain: bool,

    /// Expand and dispatch a main desktop entry, preserving its complete Exec argv.
    #[arg(long, value_name = "FILE")]
    pub desktop_file: Option<String>,

    /// Arguments supplied to the desktop entry, after --.
    #[arg(last = true)]
    pub desktop_args: Vec<String>,
}

#[derive(Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct ResolvedCommand {
    pub browser: String,
    pub family: String,
    pub mode: String,
    pub flags: Vec<String>,
    pub url: String,
    pub command: Vec<String>,
}

/// Determine browser family ("firefox", "chromium", "epiphany", etc.) by matching
/// candidate browser name against [browser.family] in SSOT.
pub fn resolve_family(browser_name: &str, ssot: &Value) -> Option<String> {
    let lower_target = browser_name.to_lowercase();
    let file_stem = Path::new(&lower_target)
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or(&lower_target);

    let browser_table = ssot.get("browser")?;
    let family_table = browser_table.get("family")?.as_table()?;

    for (family_name, members_val) in family_table {
        if let Some(members) = members_val.as_array() {
            for m in members {
                if let Some(m_str) = m.as_str() {
                    let m_lower = m_str.to_lowercase();
                    if file_stem == m_lower
                        || file_stem.contains(&m_lower)
                        || m_lower.contains(file_stem)
                    {
                        return Some(family_name.clone());
                    }
                }
            }
        }
    }
    None
}

/// Look up CLI flags for a given family and mode from [browser.flags.<family>.<mode>].
pub fn resolve_flags(family: &str, mode: &str, ssot: &Value) -> Vec<String> {
    let empty_flags = Vec::new();
    let browser_table = match ssot.get("browser") {
        Some(b) => b,
        None => return empty_flags,
    };
    let flags_table = match browser_table.get("flags") {
        Some(f) => f,
        None => return empty_flags,
    };
    let fam_flags = match flags_table.get(family) {
        Some(ff) => ff,
        None => return empty_flags,
    };

    let flag_str = fam_flags.get(mode).and_then(|v| v.as_str()).unwrap_or("");

    flag_str.split_whitespace().map(|s| s.to_string()).collect()
}

/// Resolve candidate browser name from override, SSOT configuration, or PATH discovery.
pub fn resolve_browser(
    explicit: Option<&str>,
    ssot: &Value,
    candidate_check: impl Fn(&str) -> bool,
) -> String {
    if let Some(b) = explicit {
        if !b.trim().is_empty() {
            return b.trim().to_string();
        }
    }

    // 1. [browser].default
    if let Some(br_default) = ssot
        .get("browser")
        .and_then(|b| b.get("default"))
        .and_then(|d| d.as_str())
    {
        if !br_default.is_empty() {
            return br_default.to_string();
        }
    }

    // 2. [aliases].browser, the ONE default-browser key
    if let Some(alias_val) = ssot
        .get("aliases")
        .and_then(|a| a.get("browser"))
        .and_then(|v| v.as_str())
    {
        if !alias_val.is_empty() {
            return alias_val.to_string();
        }
    }

    // 3. Scan PATH for family members in priority order
    let search_order = [
        "zen",
        "firefox",
        "google-chrome",
        "chromium",
        "brave",
        "epiphany",
        "microsoft-edge",
    ];
    for candidate in search_order {
        if candidate_check(candidate) {
            return candidate.to_string();
        }
    }

    // Default fallback
    "firefox".to_string()
}

/// Build the full command invocation and metadata
pub fn build_browser_command(
    cli: &Cli,
    ssot: &Value,
    candidate_check: impl Fn(&str) -> bool,
) -> ResolvedCommand {
    let browser = resolve_browser(cli.browser.as_deref(), ssot, candidate_check);
    let family = resolve_family(&browser, ssot).unwrap_or_else(|| "firefox".to_string());

    let effective_mode = if cli.private {
        "private"
    } else if !cli.reuse && cli.mode == "tab" {
        "new-window"
    } else {
        cli.mode.as_str()
    };

    let flags = resolve_flags(&family, effective_mode, ssot);

    let mut command = Vec::new();
    command.push(browser.clone());
    command.extend(flags.clone());
    if !cli.url.is_empty() {
        command.push(cli.url.clone());
    }

    ResolvedCommand {
        browser,
        family,
        mode: effective_mode.to_string(),
        flags,
        url: cli.url.clone(),
        command,
    }
}

fn path_candidate_exists(name: &str) -> bool {
    if Path::new(name).is_file() {
        return true;
    }
    if let Ok(path_var) = std::env::var("PATH") {
        for dir in std::env::split_paths(&path_var) {
            let candidate = dir.join(name);
            if candidate.is_file() {
                return true;
            }
            #[cfg(windows)]
            {
                let candidate_exe = dir.join(format!("{}.exe", name));
                if candidate_exe.is_file() {
                    return true;
                }
            }
        }
    }
    false
}

fn desktop_tokens(exec: &str) -> Result<Vec<String>, String> {
    let mut tokens = Vec::new();
    let mut token = String::new();
    let mut quoted = false;
    let mut started = false;
    let mut escaped = false;
    for ch in exec.chars() {
        if ch.is_control() {
            return Err("Desktop Exec contains control characters".into());
        }
        if escaped {
            token.push(ch);
            escaped = false;
        } else if ch == '\\' {
            escaped = true;
            started = true;
        } else if ch == '"' {
            quoted = !quoted;
            started = true;
        } else if ch.is_whitespace() && !quoted {
            if started {
                tokens.push(std::mem::take(&mut token));
                started = false;
            }
        } else {
            token.push(ch);
            started = true;
        }
    }
    if quoted || escaped {
        return Err("Desktop Exec has incomplete quoting".into());
    }
    if started {
        tokens.push(token);
    }
    Ok(tokens)
}

/// Expand the main desktop entry without invoking a shell or losing wrapper operands.
fn desktop_command(source: &str, file: &str, args: &[String]) -> Result<Vec<String>, String> {
    let mut main = false;
    let mut fields = std::collections::BTreeMap::new();
    for line in source.lines() {
        if line.starts_with('[') {
            main = line == "[Desktop Entry]";
        } else if main {
            if let Some((key, value)) = line.split_once('=') {
                fields.entry(key).or_insert(value);
            }
        }
    }
    if fields.get("Hidden") == Some(&"true") {
        return Err("Desktop entry is hidden".into());
    }
    let tokens = desktop_tokens(fields.get("Exec").ok_or("Desktop entry has no Exec")?)?;
    let mut out = Vec::new();
    let mut used = false;
    let mut index = 0;
    while index < tokens.len() {
        let token = &tokens[index];
        // Flatpak forwarding markers surround only the URI; browser flags belong
        // after the app ID and before the marker rather than in Flatpak options.
        if matches!(token.as_str(), "@@u" | "@@")
            && tokens.get(index + 2).is_some_and(|t| t == "@@")
            && tokens
                .get(index + 1)
                .is_some_and(|t| matches!(t.as_str(), "%u" | "%U" | "%f" | "%F"))
        {
            if used {
                return Err("Desktop Exec repeats its argument field".into());
            }
            used = true;
            if let Some((last, prefix)) = args.split_last() {
                out.extend(prefix.iter().cloned());
                out.push(token.clone());
                out.push(last.clone());
                out.push("@@".into());
            }
            index += 3;
            continue;
        }
        match token.as_str() {
            "%u" | "%U" | "%f" | "%F" => {
                if used {
                    return Err("Desktop Exec repeats its argument field".into());
                }
                used = true;
                out.extend(args.iter().cloned());
            }
            "%i" => {
                if let Some(icon) = fields.get("Icon") {
                    out.extend(["--icon".into(), (*icon).into()]);
                }
            }
            "%c" => out.push(fields.get("Name").copied().unwrap_or("").into()),
            "%k" => out.push(file.into()),
            "%d" | "%D" | "%n" | "%N" | "%v" | "%m" => {}
            _ => {
                let mut literal = String::new();
                let mut chars = token.chars();
                while let Some(ch) = chars.next() {
                    if ch == '%' && chars.next() != Some('%') {
                        return Err("Desktop Exec has an unsupported embedded field".into());
                    }
                    literal.push(ch);
                }
                out.push(literal);
            }
        }
        index += 1;
    }
    if !used {
        out.extend(args.iter().cloned());
    }
    if out.first().is_none_or(String::is_empty) {
        return Err("Desktop Exec is empty".into());
    }
    Ok(out)
}

fn run_command(command: &[String], timeout: std::time::Duration) -> ExitCode {
    use std::process::Stdio;
    use std::time::Instant;
    let mut launch = Command::new(&command[0]);
    launch
        .args(&command[1..])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        launch.process_group(0);
    }
    let mut child = match launch.spawn() {
        Ok(child) => child,
        Err(error) => {
            eprintln!("mios-browser: desktop dispatch failed: {error}");
            return ExitCode::from(1);
        }
    };
    let deadline = Instant::now() + timeout;
    loop {
        match child.try_wait() {
            Ok(Some(status)) => return ExitCode::from(status.code().unwrap_or(1) as u8),
            Err(_) => return ExitCode::from(1),
            Ok(None) if Instant::now() >= deadline => {
                eprintln!("mios-browser: dispatched; application remains running, presentation unverified");
                return ExitCode::from(3);
            }
            Ok(None) => std::thread::sleep(std::time::Duration::from_millis(10)),
        }
    }
}

fn main() -> ExitCode {
    let cli = Cli::parse();
    if let Some(file) = &cli.desktop_file {
        let command = std::fs::read_to_string(file)
            .map_err(|_| "Desktop entry cannot be read".to_string())
            .and_then(|source| desktop_command(&source, file, &cli.desktop_args));
        return match command {
            Ok(command) if cli.dry_run => {
                println!("{}", serde_json::to_string(&command).unwrap());
                ExitCode::SUCCESS
            }
            Ok(command) => {
                let timeout = mios_resolver::resolve_merged(None, false)
                    .ok()
                    .and_then(|ssot| {
                        ssot.get("desktop")?
                            .get("launch")?
                            .get("probe_timeout_ms")?
                            .as_integer()
                    })
                    .filter(|ms| (100..=30000).contains(ms));
                match timeout {
                    Some(ms) => run_command(&command, std::time::Duration::from_millis(ms as u64)),
                    None => {
                        eprintln!("mios-browser: desktop.launch.probe_timeout_ms is invalid");
                        ExitCode::from(2)
                    }
                }
            }
            Err(error) => {
                eprintln!("mios-browser: {error}");
                ExitCode::from(2)
            }
        };
    }

    let ssot = match mios_resolver::resolve_merged(None, false) {
        Ok(val) => val,
        Err(e) => {
            eprintln!(
                "mios-browser: warning: could not load merged SSOT ({e}); using vendor defaults"
            );
            let (vendor_path, _, _, _, _, _) = mios_resolver::layers::resolve_tier_dirs(None);
            if let Ok(text) = std::fs::read_to_string(&vendor_path) {
                text.parse::<Value>()
                    .unwrap_or_else(|_| Value::Table(toml::Table::new()))
            } else {
                Value::Table(toml::Table::new())
            }
        }
    };

    let resolved = build_browser_command(&cli, &ssot, path_candidate_exists);

    if cli.dry_run {
        let json_out = serde_json::to_string_pretty(&resolved).unwrap_or_default();
        println!("{json_out}");
        return ExitCode::SUCCESS;
    }

    if cli.explain {
        println!("Browser: {}", resolved.browser);
        println!("Family:  {}", resolved.family);
        println!("Mode:    {}", resolved.mode);
        println!("Flags:   {}", resolved.flags.join(" "));
        println!("URL:     {}", resolved.url);
        println!("Command: {}", resolved.command.join(" "));
        return ExitCode::SUCCESS;
    }

    if resolved.command.is_empty() {
        eprintln!("mios-browser: empty command generated");
        return ExitCode::from(2);
    }

    let program = &resolved.command[0];
    let args = &resolved.command[1..];

    match Command::new(program).args(args).status() {
        Ok(status) => {
            if let Some(code) = status.code() {
                ExitCode::from(code as u8)
            } else {
                ExitCode::SUCCESS
            }
        }
        Err(e) => {
            eprintln!("mios-browser: error executing '{program}': {e}");
            ExitCode::from(1)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn desktop_wrapper_preserves_app_operands_and_places_browser_args() {
        let source = "[Desktop Entry]\nName=Example Browser\nExec=/usr/libexec/mios/mios-flatpak-run --branch=stable --arch=x86_64 --command=launch-script.sh --file-forwarding org.example.Browser @@u %u @@\n[Desktop Action Private]\nExec=wrong-program\n";
        let args = vec![
            "--new-tab".into(),
            "https://example.invalid/a?x=1&y=two words".into(),
        ];
        assert_eq!(
            desktop_command(source, "/example.desktop", &args).unwrap(),
            vec![
                "/usr/libexec/mios/mios-flatpak-run",
                "--branch=stable",
                "--arch=x86_64",
                "--command=launch-script.sh",
                "--file-forwarding",
                "org.example.Browser",
                "--new-tab",
                "@@u",
                "https://example.invalid/a?x=1&y=two words",
                "@@"
            ]
        );
        assert_eq!(
            desktop_command(source, "/example.desktop", &[])
                .unwrap()
                .last()
                .unwrap(),
            "org.example.Browser"
        );
    }

    #[test]
    fn desktop_quotes_and_metacharacters_are_argv_never_shell() {
        let source = "[Desktop Entry]\nName=Example Browser\nExec=\"/path with spaces/browser\" --label \"declared value\" %% %U\n";
        let args = vec![
            "--new-window".into(),
            "https://example.invalid/$(false);quoted".into(),
        ];
        assert_eq!(
            desktop_command(source, "/example.desktop", &args).unwrap(),
            vec![
                "/path with spaces/browser",
                "--label",
                "declared value",
                "%",
                "--new-window",
                "https://example.invalid/$(false);quoted"
            ]
        );
        assert!(
            desktop_command("[Desktop Entry]\nHidden=true\nExec=anything %u", "x", &args).is_err()
        );
        for invalid in ["bad \"%u", "bad %u %U", "bad --arg=%u", "bad %Q"] {
            let source = format!("[Desktop Entry]\nExec={invalid}\n");
            assert!(desktop_command(&source, "x", &args).is_err(), "{invalid}");
        }
        assert!(desktop_tokens("bad\tcommand").is_err());
    }

    #[test]
    fn desktop_native_cli_keeps_post_separator_arguments() {
        let cli = Cli::try_parse_from([
            "mios-browser",
            "--desktop-file",
            "/example.desktop",
            "--",
            "--new-tab",
            "https://example.invalid/",
        ])
        .unwrap();
        assert_eq!(
            cli.desktop_args,
            vec!["--new-tab", "https://example.invalid/"]
        );
        assert!(cli.url.is_empty());
    }

    #[test]
    #[cfg(unix)]
    fn desktop_dispatch_is_bounded_and_preserves_child_failure() {
        use std::time::{Duration, Instant};
        assert_eq!(
            run_command(
                &["/bin/sh".into(), "-c".into(), "exit 17".into()],
                Duration::from_secs(1)
            ),
            ExitCode::from(17)
        );
        let start = Instant::now();
        assert_eq!(
            run_command(
                &["/bin/sleep".into(), "0.2".into()],
                Duration::from_millis(20)
            ),
            ExitCode::from(3)
        );
        assert!(start.elapsed() < Duration::from_millis(150));
    }

    fn sample_ssot() -> Value {
        let toml_str = r#"
[browser.family]
firefox  = ["firefox", "mozilla", "librewolf", "waterfox", "zen", "floorp"]
chromium = ["chrome", "chromium", "brave", "edge", "vivaldi", "opera"]
epiphany = ["epiphany", "gnome.web", "gnome.epiphany"]

[browser.flags.firefox]
tab          = "--new-tab"
window       = "--new-window"
new-window   = "--new-window --new-instance"
private      = "--private-window"

[browser.flags.chromium]
tab          = ""
window       = "--new-window"
new-window   = "--new-window"
private      = "--incognito"

[browser.flags.epiphany]
tab          = "--new-tab"
window       = "--new-window"
new-window   = "--new-window"
private      = "--incognito-mode"
"#;
        toml_str.parse::<Value>().unwrap()
    }

    #[test]
    fn test_resolve_family_standard() {
        let ssot = sample_ssot();
        assert_eq!(resolve_family("firefox", &ssot), Some("firefox".into()));
        assert_eq!(resolve_family("zen", &ssot), Some("firefox".into()));
        assert_eq!(
            resolve_family("/usr/bin/zen", &ssot),
            Some("firefox".into())
        );
        assert_eq!(
            resolve_family("google-chrome", &ssot),
            Some("chromium".into())
        );
        assert_eq!(resolve_family("brave", &ssot), Some("chromium".into()));
        assert_eq!(resolve_family("epiphany", &ssot), Some("epiphany".into()));
        assert_eq!(resolve_family("unknown-browser", &ssot), None);
    }

    #[test]
    fn test_planted_browser_in_ssot() {
        // task_948 positive control:
        // Adding a browser binary name to [browser.family.<f>] makes the launcher
        // recognise it with NO code change.
        let mut ssot = sample_ssot();
        let family_table = ssot
            .get_mut("browser")
            .unwrap()
            .get_mut("family")
            .unwrap()
            .as_table_mut()
            .unwrap();

        // Plant "super-custom-fox" in firefox family
        let ff_list = family_table
            .get_mut("firefox")
            .unwrap()
            .as_array_mut()
            .unwrap();
        ff_list.push(Value::String("super-custom-fox".into()));

        // Plant "hyper-chrome" in chromium family
        let cr_list = family_table
            .get_mut("chromium")
            .unwrap()
            .as_array_mut()
            .unwrap();
        cr_list.push(Value::String("hyper-chrome".into()));

        assert_eq!(
            resolve_family("super-custom-fox", &ssot),
            Some("firefox".into())
        );
        assert_eq!(
            resolve_family("hyper-chrome", &ssot),
            Some("chromium".into())
        );
    }

    #[test]
    fn test_resolve_flags() {
        let ssot = sample_ssot();
        assert_eq!(resolve_flags("firefox", "tab", &ssot), vec!["--new-tab"]);
        assert_eq!(
            resolve_flags("firefox", "private", &ssot),
            vec!["--private-window"]
        );
        assert_eq!(
            resolve_flags("chromium", "tab", &ssot),
            Vec::<String>::new()
        );
        assert_eq!(
            resolve_flags("chromium", "private", &ssot),
            vec!["--incognito"]
        );
        assert_eq!(
            resolve_flags("epiphany", "private", &ssot),
            vec!["--incognito-mode"]
        );
    }

    #[test]
    fn test_build_browser_command() {
        let ssot = sample_ssot();
        let cli = Cli {
            url: "https://example.org".into(),
            browser: Some("zen".into()),
            desktop_file: None,
            desktop_args: Vec::new(),
            mode: "tab".into(),
            private: false,
            reuse: true,
            profile: None,
            dry_run: true,
            explain: false,
        };

        let cmd = build_browser_command(&cli, &ssot, |_| true);
        assert_eq!(cmd.browser, "zen");
        assert_eq!(cmd.family, "firefox");
        assert_eq!(cmd.flags, vec!["--new-tab"]);
        assert_eq!(cmd.command, vec!["zen", "--new-tab", "https://example.org"]);
    }

    #[test]
    fn test_private_mode_override() {
        let ssot = sample_ssot();
        let cli = Cli {
            url: "https://example.org".into(),
            browser: Some("chrome".into()),
            desktop_file: None,
            desktop_args: Vec::new(),
            mode: "tab".into(),
            private: true, // overrides mode
            reuse: true,
            profile: None,
            dry_run: true,
            explain: false,
        };

        let cmd = build_browser_command(&cli, &ssot, |_| true);
        assert_eq!(cmd.browser, "chrome");
        assert_eq!(cmd.family, "chromium");
        assert_eq!(cmd.mode, "private");
        assert_eq!(cmd.flags, vec!["--incognito"]);
        assert_eq!(
            cmd.command,
            vec!["chrome", "--incognito", "https://example.org"]
        );
    }
}
