// AI-hint: The render-* projectors of mios-gen: .desktop launchers, automation/lib/globals.{sh,ps1} in full, man pages, and the [ports] table with its fallbacks, all from mios.toml (ADR-0021, Law 14).
// AI-doc: usr/share/doc/mios/manual/tools.md
// AI-related: tools/native/mios-gen/src/main.rs, usr/share/mios/mios.toml, automation/98-drift-checks.sh, automation/35-render-ports.sh

pub mod render_desktop {
    use std::collections::BTreeMap;
    use std::fs;
    use std::path::{Path, PathBuf};
    use toml::Value;

    const DEFAULT_TOML_PATH: &str = "usr/share/mios/mios.toml";
    const APPLICATIONS_DIR: &str = "usr/share/applications";

    type DesktopSsot = (BTreeMap<String, i64>, BTreeMap<String, Value>);

    pub fn load_ssot(root: &Path) -> Result<DesktopSsot, String> {
        let toml_path = match std::env::var("MIOS_TOML") {
            Ok(v) if !v.trim().is_empty() => PathBuf::from(v.trim()),
            _ => root.join(DEFAULT_TOML_PATH),
        };

        let content = fs::read_to_string(&toml_path).map_err(|e| {
            format!(
                "render-desktop: {} could not be read: {e}",
                toml_path.display()
            )
        })?;

        let parsed: Value = content
            .parse()
            .map_err(|e| format!("render-desktop: {} did not parse: {e}", toml_path.display()))?;

        let mut ports = BTreeMap::new();
        if let Some(ports_table) = parsed.get("ports").and_then(|p| p.as_table()) {
            for (k, v) in ports_table {
                if let Some(i) = v.as_integer() {
                    ports.insert(k.clone(), i);
                }
            }
        }

        let mut launchers = BTreeMap::new();
        if let Some(desktop) = parsed.get("desktop").and_then(|d| d.as_table()) {
            if let Some(launchers_table) = desktop.get("launchers").and_then(|l| l.as_table()) {
                for (k, v) in launchers_table {
                    launchers.insert(k.clone(), v.clone());
                }
            }
        }

        Ok((ports, launchers))
    }

    pub fn render_launcher(name: &str, cfg_val: &Value, ports: &BTreeMap<String, i64>) -> String {
        let _ = name;
        let cfg = cfg_val.as_table();

        let port_key = cfg
            .and_then(|t| t.get("port_key"))
            .and_then(|v| v.as_str())
            .unwrap_or("");

        let port = if !port_key.is_empty() {
            ports.get(port_key).copied()
        } else {
            None
        };

        let exec_cmd =
            if let Some(cmd) = cfg.and_then(|t| t.get("exec_cmd")).and_then(|v| v.as_str()) {
                cmd.to_string()
            } else if let Some(p) = port {
                let scheme = cfg
                    .and_then(|t| t.get("scheme"))
                    .and_then(|v| v.as_str())
                    .unwrap_or("http");
                let path = cfg
                    .and_then(|t| t.get("path"))
                    .and_then(|v| v.as_str())
                    .unwrap_or("/");
                format!("xdg-open {scheme}://localhost:{p}{path}")
            } else {
                String::new()
            };

        let comment_raw = cfg
            .and_then(|t| t.get("comment"))
            .and_then(|v| v.as_str())
            .unwrap_or("");
        let comment = if let Some(p) = port {
            comment_raw.replace("{port}", &p.to_string())
        } else {
            comment_raw.to_string()
        };

        let ai_hint_raw = cfg
            .and_then(|t| t.get("ai_hint"))
            .and_then(|v| v.as_str())
            .unwrap_or("");
        let ai_hint = if let Some(p) = port {
            ai_hint_raw.replace("{port}", &p.to_string())
        } else {
            ai_hint_raw.to_string()
        };

        let ai_related_raw = cfg
            .and_then(|t| t.get("ai_related"))
            .and_then(|v| v.as_str())
            .unwrap_or("");
        let ai_related = if ai_related_raw.is_empty() {
            port.map(|p| format!("localhost:{p}")).unwrap_or_default()
        } else {
            ai_related_raw.to_string()
        };

        let mut lines = Vec::new();
        if !ai_hint.is_empty() {
            lines.push(format!("# AI-hint: {ai_hint}"));
        }
        if !ai_related.is_empty() {
            lines.push(format!("# AI-related: {ai_related}"));
        }

        lines.push("[Desktop Entry]".to_string());
        lines.push("Type=Application".to_string());
        lines.push("Version=1.0".to_string());

        let title = cfg
            .and_then(|t| t.get("title"))
            .and_then(|v| v.as_str())
            .unwrap_or("");
        lines.push(format!("Name={title}"));

        if let Some(gn) = cfg
            .and_then(|t| t.get("generic_name"))
            .and_then(|v| v.as_str())
        {
            lines.push(format!("GenericName={gn}"));
        }
        if !comment.is_empty() {
            lines.push(format!("Comment={comment}"));
        }
        if !exec_cmd.is_empty() {
            lines.push(format!("Exec={exec_cmd}"));
        }
        if let Some(icon) = cfg.and_then(|t| t.get("icon")).and_then(|v| v.as_str()) {
            lines.push(format!("Icon={icon}"));
        }
        if let Some(cat) = cfg
            .and_then(|t| t.get("categories"))
            .and_then(|v| v.as_str())
        {
            lines.push(format!("Categories={cat}"));
        }
        if let Some(kw) = cfg.and_then(|t| t.get("keywords")).and_then(|v| v.as_str()) {
            lines.push(format!("Keywords={kw}"));
        }

        let terminal = cfg
            .and_then(|t| t.get("terminal"))
            .and_then(|v| v.as_bool())
            .unwrap_or(false);
        lines.push(format!("Terminal={terminal}"));

        let startup_notify = cfg
            .and_then(|t| t.get("startup_notify"))
            .and_then(|v| v.as_bool())
            .unwrap_or(true);
        lines.push(format!("StartupNotify={startup_notify}"));

        if let Some(wm) = cfg
            .and_then(|t| t.get("startup_wm_class"))
            .and_then(|v| v.as_str())
        {
            lines.push(format!("StartupWMClass={wm}"));
        }
        if let Some(nd) = cfg
            .and_then(|t| t.get("no_display"))
            .and_then(|v| v.as_bool())
        {
            lines.push(format!("NoDisplay={nd}"));
        }
        if let Some(mt) = cfg
            .and_then(|t| t.get("mime_type"))
            .and_then(|v| v.as_str())
        {
            lines.push(format!("MimeType={mt}"));
        }
        // [[actions]] = { id, name, exec }: desktop-entry-spec additional
        // application actions (dock/drawer right-click entries). Listed in the
        // main group as Actions=, each rendered as its own [Desktop Action id]
        // group after the main group.
        let actions: Vec<(&str, &str, &str)> = cfg
            .and_then(|t| t.get("actions"))
            .and_then(|v| v.as_array())
            .map(|a| {
                a.iter()
                    .filter_map(|x| {
                        let t = x.as_table()?;
                        let field = |k: &str| t.get(k).and_then(|v| v.as_str());
                        Some((field("id")?, field("name")?, field("exec")?))
                    })
                    .collect()
            })
            .unwrap_or_default();
        if !actions.is_empty() {
            let ids: String = actions.iter().map(|(id, _, _)| format!("{id};")).collect();
            lines.push(format!("Actions={ids}"));
        }

        if let Some(tc) = cfg
            .and_then(|t| t.get("trailing_comments"))
            .and_then(|v| v.as_array())
        {
            for comment_line in tc {
                if let Some(s) = comment_line.as_str() {
                    lines.push(s.to_string());
                }
            }
        }

        for (id, action_name, action_exec) in &actions {
            lines.push(String::new());
            lines.push(format!("[Desktop Action {id}]"));
            lines.push(format!("Name={action_name}"));
            lines.push(format!("Exec={action_exec}"));
        }

        let mut out = lines.join("\n");
        out.push('\n');
        out
    }

    #[cfg(test)]
    mod tests {
        use super::render_launcher;
        use std::collections::BTreeMap;

        #[test]
        fn mime_type_and_actions_render_as_spec_groups() {
            let cfg: toml::Value = toml::from_str(
                r#"
                title = "Browser"
                exec_cmd = "browser %u"
                mime_type = "text/html;x-scheme-handler/https;"
                actions = [
                  { id = "new-window", name = "New Window", exec = "browser --new-window" },
                  { id = "bad" },
                ]
                "#,
            )
            .unwrap_or_else(|e| unreachable!("fixture parses: {e}"));
            let out = render_launcher("browser", &cfg, &BTreeMap::new());
            assert!(out.contains("\nMimeType=text/html;x-scheme-handler/https;\n"), "{out}");
            assert!(out.contains("\nActions=new-window;\n"), "{out}");
            assert!(
                out.ends_with("\n\n[Desktop Action new-window]\nName=New Window\nExec=browser --new-window\n"),
                "{out}"
            );
            let plain: toml::Value = toml::from_str("title = \"x\"")
                .unwrap_or_else(|e| unreachable!("fixture parses: {e}"));
            let out = render_launcher("x", &plain, &BTreeMap::new());
            assert!(!out.contains("MimeType=") && !out.contains("Actions=") && !out.contains("[Desktop Action"), "{out}");
        }
    }

    pub fn run_render_desktop(root: &Path, check: bool) -> Result<(String, i32), (String, i32)> {
        let (ports, launchers) = load_ssot(root).map_err(|e| (e, 1))?;
        let apps_dir = root.join(APPLICATIONS_DIR);

        let mut on_disk = Vec::new();
        if apps_dir.is_dir() {
            if let Ok(entries) = fs::read_dir(&apps_dir) {
                for entry in entries.flatten() {
                    let name = entry.file_name().to_string_lossy().to_string();
                    if name.ends_with(".desktop") {
                        on_disk.push(name);
                    }
                }
            }
        }
        on_disk.sort();

        if launchers.is_empty() {
            let msg = format!(
            "[render-desktop] mios.toml [desktop.launchers] is empty or absent, but {} .desktop file(s) ship in usr/share/applications. Nothing would be compared.",
            on_disk.len()
        );
            return Err((msg, 1));
        }

        let mut unmanaged = Vec::new();
        for f in &on_disk {
            let base = &f[..f.len() - 8];
            if !launchers.contains_key(base) {
                unmanaged.push(f.clone());
            }
        }

        if !unmanaged.is_empty() && check {
            let mut lines = Vec::new();
            for f in &unmanaged {
                let base = &f[..f.len() - 8];
                lines.push(format!(
                "[render-desktop] DRIFT: {f} ships but no [desktop.launchers.{base}] declares it"
            ));
            }
            return Err((lines.join("\n"), 1));
        }

        let mut drifted = Vec::new();
        for (name, cfg) in &launchers {
            let rendered = render_launcher(name, cfg, &ports);
            let to_write = rendered.replace("\r\n", "\n");
            let target_path = apps_dir.join(format!("{name}.desktop"));

            if check {
                if !target_path.is_file() {
                    drifted.push(format!("{name}.desktop missing"));
                    continue;
                }
                let current = fs::read_to_string(&target_path).unwrap_or_default();
                if current != to_write {
                    drifted.push(format!("{name}.desktop content drifted"));
                }
            } else {
                if let Some(parent) = target_path.parent() {
                    let _ = fs::create_dir_all(parent);
                }
                #[cfg(unix)]
                {
                    use std::os::unix::fs::PermissionsExt;
                    if target_path.exists() {fs::set_permissions(&target_path,fs::Permissions::from_mode(0o644)).map_err(|e|(e.to_string(),1))?;}
                }
                if let Err(e) = fs::write(&target_path, to_write.as_bytes()) {
                    return Err((
                        format!(
                            "render-desktop: write {} failed: {e}",
                            target_path.display()
                        ),
                        1,
                    ));
                }
                #[cfg(unix)]
                {
                    use std::os::unix::fs::PermissionsExt;
                    fs::set_permissions(&target_path,fs::Permissions::from_mode(0o644)).map_err(|e|(e.to_string(),1))?;
                }
            }
        }

        if check {
            if !drifted.is_empty() {
                let mut lines = Vec::new();
                for d in drifted {
                    lines.push(format!("[render-desktop] DRIFT: {d}"));
                }
                return Err((lines.join("\n"), 1));
            }
            Ok((
                "[render-desktop] All .desktop launchers match SSOT".to_string(),
                0,
            ))
        } else {
            Ok((
                "[render-desktop] Rendered .desktop launchers from SSOT".to_string(),
                0,
            ))
        }
    }
}

pub mod render_globals {
    use regex::Regex;
    use std::collections::{BTreeMap, BTreeSet};
    use std::fs;
    use std::path::Path;
    use std::sync::OnceLock;

    const HEADER_SH: &str = r#"#!/usr/bin/env bash
# GENERATED IN FULL from usr/share/mios/mios.toml by mios-gen render-globals. Zero hand-written constants; DO NOT EDIT -- re-run the renderer.
# AI-related: usr/share/mios/mios.toml, automation/lib/globals.ps1, tools/native/mios-gen/src/render.rs
# AI-functions: _mios_resolve_version
#
# Shell sibling of automation/lib/globals.ps1 -- both are rendered from the same
# SSOT by the same generator, so they cannot diverge. Dot-source from any entry
# point; every constant uses `:=` so an environment variable exported BEFORE
# sourcing still wins.

_mios_resolve_version() {
    local v=""
    if   [[ -n "${MIOS_VERSION:-}" ]];        then v="$MIOS_VERSION"
    elif [[ -f /ctx/VERSION ]];               then v="$(cat /ctx/VERSION)"
    elif [[ -f /usr/share/mios/VERSION ]];    then v="$(cat /usr/share/mios/VERSION)"
    else
        local _root
        _root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." 2>/dev/null && pwd)"
        if [[ -n "$_root" && -f "${_root}/VERSION" ]]; then
            v="$(cat "${_root}/VERSION")"
        fi
    fi
    printf '%s' "${v:-VERSION_FALLBACK}" | tr -d '[:space:]'
}
: "${MIOS_VERSION:=$(_mios_resolve_version)}"
export MIOS_VERSION
"#;

    const HEADER_PS: &str = r#"# GENERATED IN FULL from usr/share/mios/mios.toml by mios-gen render-globals. Zero hand-written constants; DO NOT EDIT -- re-run the renderer.
# AI-related: usr/share/mios/mios.toml, automation/lib/globals.sh, tools/native/mios-gen/src/render.rs
# AI-functions: Resolve-MiosVersion
#
# PowerShell sibling of automation/lib/globals.sh -- both are rendered from the
# same SSOT by the same generator, so they cannot diverge. Dot-source from any
# entry point:
#
#     . (Join-Path $PSScriptRoot 'automation/lib/globals.ps1')
#
# Override any constant with an environment variable BEFORE dot-sourcing -- e.g.
# `$env:MIOS_VERSION = ' - rc1'; . globals.ps1`.

function Resolve-MiosVersion {
    if ($env:MIOS_VERSION) { return ([string]$env:MIOS_VERSION).Trim() }
    foreach ($p in @(
        '/ctx/VERSION',
        '/usr/share/mios/VERSION',
        (Join-Path $PSScriptRoot '..\..\VERSION')
    )) {
        if ($p -and (Test-Path $p)) {
            $v = (Get-Content $p -EA SilentlyContinue | Out-String).Trim()
            if ($v) { return $v }
        }
    }
    return 'VERSION_FALLBACK'
}
$script:MIOS_VERSION = Resolve-MiosVersion

function Resolve-MiosDistro {
    param([string]$Default = 'podman-MiOS-DEV')
    if ($env:MIOS_WSL_DISTRO) { return $env:MIOS_WSL_DISTRO }
    try {
        $lxss = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss'
        if (Test-Path $lxss) {
            $all = @(Get-ChildItem $lxss -ErrorAction SilentlyContinue |
                     ForEach-Object { (Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue).DistributionName } |
                     Where-Object { $_ })
            $resolved = ($all | Where-Object { $_ -match 'MiOS' } | Select-Object -First 1)
            if ($resolved) { return $resolved }
            $defGuid = (Get-ItemProperty $lxss -Name DefaultDistribution -ErrorAction SilentlyContinue).DefaultDistribution
            if ($defGuid) {
                $defName = (Get-ItemProperty (Join-Path $lxss $defGuid) -ErrorAction SilentlyContinue).DistributionName
                if ($defName) { return $defName }
            }
            if ($all.Count -gt 0) { return $all[0] }
        }
    } catch {}
    return $Default
}
$script:MIOS_WSL_DISTRO = Resolve-MiosDistro
"#;

    const PS_HOST_PATHS: &str = r#"
# ── IMAGE DEFAULT (asserted by the image-parity drift check) ─────────
$defaultImageName = 'IMAGE_NAME_LITERAL'
"#;

    const INPUT_SH: &str = r#"
# Legacy environment inputs are accepted only when they have one SSOT owner.
_mios_input() {
    local canonical="$1" name value selected='' chosen=''
    shift
    [[ -n "${!canonical:-}" ]] && return 0
    for name do
        value="${!name:-}"
        [[ -n "$value" ]] || continue
        if [[ -n "$chosen" && "$selected" != "$value" ]]; then
            printf 'conflicting legacy inputs %s and %s; set %s\n' "$chosen" "$name" "$canonical" >&2
            return 1
        fi
        selected="$value"; chosen="$name"
    done
    if [[ -n "$chosen" ]]; then printf -v "$canonical" '%s' "$selected"; fi
    return 0
}
"#;

    const INPUT_PS: &str = r#"
function Resolve-MiosInput {
    param([string]$Canonical, [string[]]$Aliases, [object]$Default)
    $selected = $null
    $chosen = $null
    foreach ($name in $Aliases) {
        $value = [Environment]::GetEnvironmentVariable($name)
        if ([string]::IsNullOrEmpty($value)) { continue }
        if ($null -ne $chosen -and $selected -cne $value) {
            throw "conflicting legacy inputs $chosen and $name; set $Canonical"
        }
        $selected = $value
        $chosen = $name
    }
    if ($null -ne $chosen) { return $selected }
    return $Default
}
"#;

    const PS_HOST_PATHS_TAIL: &str = r#"# ── WINDOWS HOST PATHS (resolved from the live environment) ──────────
$script:MIOS_WIN_APPDATA_DIR = if ($env:APPDATA)     { $env:APPDATA }     else { "$HOME/AppData/Roaming" }
$script:MIOS_WIN_DOCS_DIR    = if ($env:USERPROFILE) { "$env:USERPROFILE/Documents" } else { "$HOME/Documents" }
$script:MIOS_WIN_REPO_DIR    = if ($env:MIOS_WIN_REPO_DIR) { $env:MIOS_WIN_REPO_DIR } else { "$HOME/MiOS" }
"#;

    fn template_re() -> &'static Regex {
        static RE: OnceLock<Regex> = OnceLock::new();
        RE.get_or_init(|| Regex::new(r"\$\{(MIOS_[A-Z0-9_]+)\}").unwrap())
    }

    fn sh_unsafe_re() -> &'static Regex {
        static RE: OnceLock<Regex> = OnceLock::new();
        RE.get_or_init(|| Regex::new(r#"['"`$\\{}\r\n]"#).unwrap())
    }

    /// globals.sh / globals.ps1 are committed source projections: only the
    /// selected root's vendor tier feeds them (root/etc is a deployed host's live
    /// admin tier, e.g. a saved monitor layout). The developer's user
    /// tier (~/.config/mios/mios.toml) and loader pointers (MIOS_TOML,
    /// MIOS_VENDOR_TOML) must not change what --check compares against.
    pub fn build_exports(root: &Path) -> Result<BTreeMap<String, String>, String> {
        let merged = mios_resolver::resolve_vendor_projection(root)
            .map_err(|e| format!("failed to resolve merged mios.toml: {e}"))?;
        Ok(mios_resolver::emit::build_globals_map(
            &merged,
            mios_resolver::stack_offset_of(&merged),
        ))
    }

    pub fn ordered_names(exports: &BTreeMap<String, String>) -> Vec<String> {
        let re = template_re();
        let mut deps: BTreeMap<&str, BTreeSet<&str>> = BTreeMap::new();
        for (k, v) in exports {
            let mut d = BTreeSet::new();
            for cap in re.captures_iter(v) {
                let name = cap.get(1).unwrap().as_str();
                if exports.contains_key(name) {
                    d.insert(name);
                }
            }
            deps.insert(k.as_str(), d);
        }

        let mut res = Vec::new();
        let mut visited = BTreeSet::new();
        let mut visiting = BTreeSet::new();

        fn visit<'a>(
            node: &'a str,
            deps: &BTreeMap<&str, BTreeSet<&'a str>>,
            visited: &mut BTreeSet<&'a str>,
            visiting: &mut BTreeSet<&'a str>,
            res: &mut Vec<String>,
        ) {
            if visited.contains(node) {
                return;
            }
            if visiting.contains(node) {
                visited.insert(node);
                res.push(node.to_string());
                return;
            }
            visiting.insert(node);
            if let Some(d_set) = deps.get(node) {
                for dep in d_set {
                    visit(dep, deps, visited, visiting, res);
                }
            }
            visiting.remove(node);
            if !visited.contains(node) {
                visited.insert(node);
                res.push(node.to_string());
            }
        }

        for name in exports.keys() {
            visit(name.as_str(), &deps, &mut visited, &mut visiting, &mut res);
        }

        res
    }

    pub fn sh_squote(text: &str) -> String {
        format!("'{}'", text.replace('\'', "'\"'\"'"))
    }

    enum Part<'a> {
        Lit(&'a str),
        Placeholder(&'a str),
    }

    fn split_template<'a>(re: &Regex, text: &'a str) -> Vec<Part<'a>> {
        let mut parts = Vec::new();
        let mut last_end = 0;
        for cap in re.captures_iter(text) {
            let m = cap.get(0).unwrap();
            if m.start() > last_end {
                parts.push(Part::Lit(&text[last_end..m.start()]));
            }
            let var_name = cap.get(1).unwrap().as_str();
            parts.push(Part::Placeholder(var_name));
            last_end = m.end();
        }
        if last_end < text.len() {
            parts.push(Part::Lit(&text[last_end..]));
        }
        if parts.is_empty() {
            parts.push(Part::Lit(""));
        }
        parts
    }

    pub fn sh_assign(name: &str, value: &str) -> String {
        let re = template_re();
        let parts = split_template(re, value);
        let has_placeholders = parts.iter().any(|p| matches!(p, Part::Placeholder(_)));

        if !has_placeholders && !sh_unsafe_re().is_match(value) {
            return format!(": \"${{{name}:={value}}}\"");
        }

        let rendered = if !has_placeholders {
            sh_squote(value)
        } else {
            let mut chunks = Vec::new();
            for part in parts {
                match part {
                    Part::Placeholder(var) => chunks.push(format!("\"${{{var}:-}}\"")),
                    Part::Lit(lit) => {
                        if !lit.is_empty() {
                            chunks.push(sh_squote(lit));
                        }
                    }
                }
            }
            if chunks.is_empty() {
                "''".to_string()
            } else {
                chunks.join("")
            }
        };

        format!("[ -n \"${{{name}+x}}\" ] || {name}={rendered}")
    }

    pub fn ps_assign(
        name: &str,
        value: &str,
        exports: Option<&BTreeMap<String, String>>,
    ) -> String {
        ps_assign_with_inputs(name, value, exports, &[])
    }

    fn ps_assign_with_inputs(
        name: &str,
        value: &str,
        exports: Option<&BTreeMap<String, String>>,
        aliases: &[String],
    ) -> String {
        let re = template_re();
        let parts = split_template(re, value);
        let has_placeholders = parts.iter().any(|p| matches!(p, Part::Placeholder(_)));

        let rendered = if !has_placeholders {
            if !value.is_empty() && value.chars().all(|c| c.is_ascii_digit()) {
                value.to_string()
            } else {
                format!("'{}'", value.replace('\'', "''"))
            }
        } else {
            let has_live_parts = parts.iter().any(|p| match p {
                Part::Placeholder(p_name) => {
                    exports.map(|e| e.contains_key(*p_name)).unwrap_or(true)
                }
                _ => false,
            });
            if !has_live_parts {
                format!("'{}'", value.replace('\'', "''"))
            } else {
                let mut chunks = Vec::new();
                for part in parts {
                    match part {
                        Part::Placeholder(p_name) => {
                            if exports.map(|e| e.contains_key(p_name)).unwrap_or(true) {
                                chunks.push(format!("$($script:{p_name})"));
                            } else {
                                chunks.push(format!("${{{p_name}}}"));
                            }
                        }
                        Part::Lit(lit) => {
                            if !lit.is_empty() {
                                chunks.push(
                                    lit.replace('`', "``")
                                        .replace('"', "`\"")
                                        .replace('$', "`$"),
                                );
                            }
                        }
                    }
                }
                format!("\"{}\"", chunks.join(""))
            }
        };

        let fallback = if aliases.is_empty() {
            rendered
        } else {
            let aliases = aliases
                .iter()
                .map(|alias| format!("'{alias}'"))
                .collect::<Vec<_>>()
                .join(", ");
            format!(
                "Resolve-MiosInput -Canonical '{name}' -Aliases @({aliases}) -Default ({rendered})"
            )
        };
        format!("$script:{name} = if ($env:{name}) {{ $env:{name} }} else {{ {fallback} }}")
    }

    /// Credential constants (the [identity]/[auth]/[autounattend.service]/[field.sysrescue]
    /// password plane and any other `*_PASSWORD` / `*_PASSPHRASE` / `*_PASSWORD_HASH` key) are
    /// NEVER baked into globals.sh / globals.ps1: those files are committed source and are
    /// sourced by every script. The renderers emit only the NAME (an environment passthrough
    /// with an empty default) and point at the one fail-closed resolver.
    pub fn is_credential_name(name: &str) -> bool {
        const SUFFIXES: [&str; 4] = ["_PASSWORD", "_PASSPHRASE", "_PASSWORD_HASH", "_PASSWD"];
        SUFFIXES.iter().any(|s| name.ends_with(s))
    }

    const CREDENTIAL_NOTE_SH: &str = "# credential -- NOT baked from the SSOT (env passthrough only); resolve at use: usr/libexec/mios/mios-credential";
    const CREDENTIAL_NOTE_PS: &str = "# credential -- NOT baked from the SSOT (env passthrough only); resolve at use: Resolve-MiOSCredentials (MiOS-Provision.lib.ps1) or MIOS_RESOLVED_JSON";

    pub fn render_sh(
        exports: &BTreeMap<String, String>,
        names: &[String],
        version_fallback: &str,
        inputs: &BTreeMap<String, Vec<String>>,
    ) -> String {
        let mut lines = Vec::new();
        lines.push(HEADER_SH.replace("VERSION_FALLBACK", version_fallback));
        lines.push(INPUT_SH.to_string());
        for name in names {
            if name == "MIOS_VERSION" {
                continue;
            }
            if let Some(val) = exports.get(name) {
                let credential = is_credential_name(name);
                if credential {
                    lines.push(CREDENTIAL_NOTE_SH.to_string());
                }
                if let Some(aliases) = inputs.get(name).filter(|aliases| !aliases.is_empty()) {
                    lines.push(format!(
                        "_mios_input {name} {} || {{ return 1 2>/dev/null || exit 1; }}",
                        aliases.join(" ")
                    ));
                }
                lines.push(sh_assign(name, if credential { "" } else { val }));
            }
        }
        lines.push("unset -f _mios_input\n".to_string());
        lines.join("\n")
    }

    pub fn render_ps1(
        exports: &BTreeMap<String, String>,
        names: &[String],
        version_fallback: &str,
        inputs: &BTreeMap<String, Vec<String>>,
    ) -> String {
        let mut lines = Vec::new();
        lines.push(HEADER_PS.replace("VERSION_FALLBACK", version_fallback));
        lines.push(INPUT_PS.to_string());
        for name in names {
            if name == "MIOS_VERSION" {
                continue;
            }
            if let Some(val) = exports.get(name) {
                let credential = is_credential_name(name);
                if credential {
                    lines.push(CREDENTIAL_NOTE_PS.to_string());
                }
                let val = if credential { "" } else { val.as_str() };
                let aliases = inputs.get(name).map(Vec::as_slice).unwrap_or(&[]);
                lines.push(if aliases.is_empty() {
                    ps_assign(name, val, Some(exports))
                } else {
                    ps_assign_with_inputs(name, val, Some(exports), aliases)
                });
            }
        }
        let image_name = exports
            .get("MIOS_IMAGE_NAME")
            .map(|s| s.as_str())
            .unwrap_or("ghcr.io/mios-dev/mios")
            .replace('\'', "''");
        lines.push(PS_HOST_PATHS.replace("IMAGE_NAME_LITERAL", &image_name));
        lines.push(PS_HOST_PATHS_TAIL.to_string());
        lines.join("\n")
    }

    pub fn check_globals_parity(sh_body: &str, ps_body: &str) -> Vec<String> {
        static SH_RE: OnceLock<Regex> = OnceLock::new();
        let sh_re = SH_RE.get_or_init(|| {
            Regex::new(r#"(?::\s*"\$\{|\[\s*-n\s*"\$\{|export\s+)(MIOS_[A-Z0-9_]+)"#).unwrap()
        });

        static PS_RE: OnceLock<Regex> = OnceLock::new();
        let ps_re = PS_RE.get_or_init(|| Regex::new(r"\$script:(MIOS_[A-Z0-9_]+)\s*=").unwrap());

        let sh_keys: BTreeSet<&str> = sh_re
            .captures_iter(sh_body)
            .map(|c| c.get(1).unwrap().as_str())
            .collect();

        let ps_keys: BTreeSet<&str> = ps_re
            .captures_iter(ps_body)
            .map(|c| c.get(1).unwrap().as_str())
            .collect();

        let ps_keys_common: BTreeSet<&str> = ps_keys
            .into_iter()
            .filter(|k| !k.starts_with("MIOS_WIN_") || sh_keys.contains(k))
            .collect();

        let sh_keys_common = sh_keys;

        let mut problems = Vec::new();
        let missing_in_ps: Vec<&str> = sh_keys_common
            .difference(&ps_keys_common)
            .copied()
            .collect();
        if !missing_in_ps.is_empty() {
            problems.push(format!(
                "keys in globals.sh but missing in globals.ps1: {}",
                missing_in_ps.join(", ")
            ));
        }

        let missing_in_sh: Vec<&str> = ps_keys_common
            .difference(&sh_keys_common)
            .copied()
            .collect();
        if !missing_in_sh.is_empty() {
            problems.push(format!(
                "keys in globals.ps1 but missing in globals.sh: {}",
                missing_in_sh.join(", ")
            ));
        }

        problems
    }

    /// globals.sh / globals.ps1 are sourced by login-path scripts: a reset mid-write must leave
    /// the old file or the new one. Temp file beside the target, fsync, keep mode, rename, fsync dir.
    fn write_atomic(target: &Path, content: &[u8]) -> std::io::Result<()> {
        use std::io::Write;
        let dir = target
            .parent()
            .ok_or_else(|| std::io::Error::other("no parent directory"))?;
        fs::create_dir_all(dir)?;
        let perms = fs::metadata(target).ok().map(|m| m.permissions());
        let mut tmp = tempfile::Builder::new()
            .prefix(".mios-globals-")
            .tempfile_in(dir)?;
        tmp.write_all(content)?;
        tmp.as_file().sync_all()?;
        let perms = perms.unwrap_or_else(|| {
            use std::os::unix::fs::PermissionsExt;
            fs::Permissions::from_mode(0o644)
        });
        tmp.as_file().set_permissions(perms)?;
        tmp.persist(target).map_err(|e| e.error)?;
        fs::File::open(dir)?.sync_all()
    }

    pub fn run_render_globals(root: &Path, check: bool) -> Result<(String, usize), (String, i32)> {
        let toml_path = root.join("usr/share/mios/mios.toml");
        if !toml_path.is_file() {
            return Err((
                format!("render-globals: {} does not exist", toml_path.display()),
                1,
            ));
        }

        let mut exports = build_exports(root).map_err(|e| (e, 1))?;
        let merged = mios_resolver::resolve_vendor_projection(root).map_err(|e| (e.to_string(), 1))?;
        let inputs = mios_resolver::names::registry(&merged)
            .map_err(|e| (e, 1))?
            .input_aliases();
        for (canonical, aliases) in &inputs {
            if !exports.contains_key(canonical) {
                continue;
            }
            for alias in aliases {
                if exports.contains_key(alias) {
                    exports.insert(alias.clone(), format!("${{{canonical}}}"));
                }
            }
        }
        let version_fallback = exports
            .get("MIOS_META_VERSION")
            .or_else(|| exports.get("MIOS_VERSION"))
            .map(|s| s.as_str())
            .unwrap_or("0.3.0");

        let names = ordered_names(&exports);
        let sh_body = render_sh(&exports, &names, version_fallback, &inputs);
        let ps_body = render_ps1(&exports, &names, version_fallback, &inputs);

        let parity_problems = check_globals_parity(&sh_body, &ps_body);
        if !parity_problems.is_empty() {
            let msg = format!(
                "[render-globals] globals.sh / globals.ps1 parity check failed:\n{}",
                parity_problems
                    .iter()
                    .map(|p| format!("    {p}"))
                    .collect::<Vec<_>>()
                    .join("\n")
            );
            return Err((msg, 1));
        }

        let sh_path = root.join("automation/lib/globals.sh");
        let ps_path = root.join("automation/lib/globals.ps1");

        let mut drifted = Vec::new();

        // Check sh_path
        let sh_existing = if sh_path.is_file() {
            fs::read_to_string(&sh_path).ok()
        } else {
            None
        };
        let sh_norm = sh_body.replace("\r\n", "\n");
        if sh_existing.as_deref() != Some(&sh_norm) {
            drifted.push("automation/lib/globals.sh".to_string());
            if !check {
                if let Err(e) = write_atomic(&sh_path, sh_norm.as_bytes()) {
                    return Err((format!("failed to write {}: {e}", sh_path.display()), 1));
                }
            }
        }

        // Check ps_path
        let ps_bytes = if ps_path.is_file() {
            fs::read(&ps_path).ok()
        } else {
            None
        };
        let ps_norm = ps_body.replace("\r\n", "\n");
        let mut expected_ps_bytes = vec![0xEF, 0xBB, 0xBF];
        let crlf_body = ps_norm.replace('\n', "\r\n");
        expected_ps_bytes.extend_from_slice(crlf_body.as_bytes());

        if ps_bytes.as_deref() != Some(&expected_ps_bytes) {
            drifted.push("automation/lib/globals.ps1".to_string());
            if !check {
                if let Err(e) = write_atomic(&ps_path, &expected_ps_bytes) {
                    return Err((format!("failed to write {}: {e}", ps_path.display()), 1));
                }
            }
        }

        let count = names.len();

        if check {
            if !drifted.is_empty() {
                let drift_list = drifted
                    .iter()
                    .map(|p| format!("    {p}"))
                    .collect::<Vec<_>>()
                    .join("\n");
                let msg = format!(
                "[render-globals] resolvers are stale vs SSOT:\n{drift_list}\n    run: mios-gen render-globals"
            );
                return Err((msg, 1));
            }
            Ok((
                format!("[render-globals] both resolvers match SSOT ({count} constants)"),
                count,
            ))
        } else {
            Ok((
            format!(
                "[render-globals] generated {count} constants into globals.sh + globals.ps1 (no hand-written literals remain)"
            ),
            count,
        ))
        }
    }

    #[cfg(test)]
    mod tests {
        use super::*;

        #[test]
        fn test_sh_assign_simple_value() {
            assert_eq!(
                sh_assign("MIOS_PORTS_SSH", "8100"),
                ": \"${MIOS_PORTS_SSH:=8100}\""
            );
        }

        #[test]
        fn test_sh_assign_value_with_brace() {
            let out = sh_assign("MIOS_MSG", "hello {name}");
            assert!(!out.contains(":="));
            assert!(out.contains("MIOS_MSG+x"));
        }

        #[test]
        fn test_sh_assign_value_with_apostrophe() {
            let out = sh_assign("MIOS_JOB", "the operator's phone");
            assert!(!out.contains(":="));
            assert!(out.contains("'\"'\"'"));
        }

        #[test]
        fn test_sh_assign_template_stays_live() {
            let out = sh_assign(
                "MIOS_URLS_FORGE",
                "http://localhost:${MIOS_PORTS_FORGE_HTTP}",
            );
            assert!(out.contains("\"${MIOS_PORTS_FORGE_HTTP:-}\""));
        }

        #[test]
        fn test_sh_assign_assignment_is_conditional() {
            for value in ["8100", "has }brace", "has 'quote"] {
                let out = sh_assign("MIOS_X", value);
                assert!(out.contains(":=") || out.contains("+x"));
            }
        }

        #[test]
        fn test_ps_assign_numeric_is_bare() {
            let out = ps_assign("MIOS_PORTS_SSH", "8100", None);
            assert!(out.contains("else { 8100 }"));
        }

        #[test]
        fn test_ps_assign_string_is_single_quoted() {
            let out = ps_assign("MIOS_IDENTITY_USERNAME", "mios", None);
            assert!(out.contains("else { 'mios' }"));
        }

        #[test]
        fn test_ps_assign_embedded_quote_is_doubled() {
            let out = ps_assign("MIOS_JOB", "operator's phone", None);
            assert!(out.contains("''"));
        }

        #[test]
        fn test_ps_assign_template_becomes_subexpression() {
            let mut exports = BTreeMap::new();
            exports.insert("MIOS_PORTS_FORGE_HTTP".to_string(), "8400".to_string());
            let out = ps_assign(
                "MIOS_URLS_FORGE",
                "http://localhost:${MIOS_PORTS_FORGE_HTTP}",
                Some(&exports),
            );
            assert!(out.contains("$($script:MIOS_PORTS_FORGE_HTTP)"));
        }

        #[test]
        fn test_ps_assign_dollar_in_template_is_escaped() {
            let mut exports = BTreeMap::new();
            exports.insert("MIOS_PORTS_SSH".to_string(), "8100".to_string());
            let out = ps_assign("MIOS_X", "a $literal and ${MIOS_PORTS_SSH}", Some(&exports));
            assert!(out.contains("`$literal"));
        }

        #[test]
        fn test_ps_assign_env_override_wins() {
            let out = ps_assign("MIOS_PORTS_SSH", "8100", None);
            assert!(out.contains("if ($env:MIOS_PORTS_SSH)"));
        }

        #[test]
        fn test_ordered_names_topology() {
            let mut exports = BTreeMap::new();
            exports.insert(
                "MIOS_URLS_FORGE".to_string(),
                "http://localhost:${MIOS_PORTS_FORGE_HTTP}".to_string(),
            );
            exports.insert("MIOS_PORTS_FORGE_HTTP".to_string(), "8400".to_string());

            let names = ordered_names(&exports);
            let idx_dep = names
                .iter()
                .position(|n| n == "MIOS_PORTS_FORGE_HTTP")
                .unwrap();
            let idx_ref = names.iter().position(|n| n == "MIOS_URLS_FORGE").unwrap();
            assert!(idx_dep < idx_ref);
        }

        #[test]
        fn test_credentials_are_never_baked() {
            let secret = "fixture-not-a-real-secret";
            let mut exports = BTreeMap::new();
            exports.insert("MIOS_IDENTITY_DEFAULT_PASSWORD".to_string(), secret.to_string());
            exports.insert("MIOS_FIELD_SYSRESCUE_WIPE_PASSPHRASE".to_string(), secret.to_string());
            exports.insert("MIOS_IDENTITY_USERNAME".to_string(), "fixture-user".to_string());
            exports.insert("MIOS_AUTH_PASSWORD_POLICY".to_string(), "interactive".to_string());
            let mut inputs = BTreeMap::new();
            inputs.insert(
                "MIOS_IDENTITY_DEFAULT_PASSWORD".to_string(),
                vec!["MIOS_DEFAULT_PASSWORD".to_string()],
            );
            let names = ordered_names(&exports);
            let sh = render_sh(&exports, &names, "0.0.0", &inputs);
            let ps = render_ps1(&exports, &names, "0.0.0", &inputs);
            // negative: the secret value never reaches either projection
            assert!(!sh.contains(secret), "credential baked into globals.sh");
            assert!(!ps.contains(secret), "credential baked into globals.ps1");
            // positive: the names are still declared (env passthrough) and parity holds
            assert!(sh.contains(": \"${MIOS_IDENTITY_DEFAULT_PASSWORD:=}\""));
            assert!(sh.contains("_mios_input MIOS_IDENTITY_DEFAULT_PASSWORD MIOS_DEFAULT_PASSWORD"));
            assert!(ps.contains("$script:MIOS_FIELD_SYSRESCUE_WIPE_PASSPHRASE = if ($env:MIOS_FIELD_SYSRESCUE_WIPE_PASSPHRASE) { $env:MIOS_FIELD_SYSRESCUE_WIPE_PASSPHRASE } else { '' }"));
            assert!(sh.contains("mios-credential"));
            // non-credential values are still baked
            assert!(sh.contains("MIOS_IDENTITY_USERNAME:=fixture-user"));
            assert!(sh.contains("MIOS_AUTH_PASSWORD_POLICY:=interactive"));
            // parity: every credential name is declared in BOTH projections
            for n in ["MIOS_IDENTITY_DEFAULT_PASSWORD", "MIOS_FIELD_SYSRESCUE_WIPE_PASSPHRASE"] {
                assert!(sh.contains(&format!(": \"${{{n}:=}}\"")), "{n} missing in sh");
                assert!(ps.contains(&format!("$script:{n} = ")), "{n} missing in ps1");
            }
            assert!(is_credential_name("MIOS_AUTOUNATTEND_SERVICE_SVC_PASSWORD"));
            assert!(is_credential_name("MIOS_AUTH_PASSWORD_HASH"));
            assert!(!is_credential_name("MIOS_AUTH_PASSWORD_POLICY"));
            assert!(!is_credential_name("MIOS_WINDOWS_XBOX_USERS_DESKTOP_PASSWORD_REF"));
        }

        #[test]
        fn test_globals_parity() {
            let sh_body = ": \"${MIOS_TEST_KEY:=1234}\"\n";
            let ps_body = "$script:MIOS_TEST_KEY = 1234\n";
            let problems = check_globals_parity(sh_body, ps_body);
            assert!(
                problems.is_empty(),
                "expected 0 problems, got: {problems:?}"
            );

            let ps_missing = "$script:MIOS_OTHER_KEY = 1234\n";
            let problems_missing_ps = check_globals_parity(sh_body, ps_missing);
            assert!(problems_missing_ps
                .iter()
                .any(|p| p.contains("missing in globals.ps1")));

            let sh_missing = ": \"${MIOS_OTHER_KEY:=1234}\"\n";
            let problems_missing_sh = check_globals_parity(sh_missing, ps_body);
            assert!(problems_missing_sh
                .iter()
                .any(|p| p.contains("missing in globals.sh")));
        }
    }
}

pub mod render_manpages {
    use std::collections::BTreeMap;
    use std::fs;
    use std::path::Path;
    use std::process::Command;
    use toml::Value;

    const MAN: &str = "usr/share/man";
    const DASH: &str = "\\-";

    pub fn roff(text: &str) -> String {
        let mut out = Vec::new();
        for raw_line in text.split('\n') {
            let mut line = raw_line.replace('\\', "\\e");
            if line.starts_with('.') || line.starts_with('\'') {
                line = format!("\\&{line}");
            }
            let chars: Vec<char> = line.chars().collect();
            let mut buf = String::with_capacity(chars.len() + 8);
            for i in 0..chars.len() {
                if chars[i] == '-' {
                    let prev_not_bs_or_w = if i == 0 {
                        true
                    } else {
                        chars[i - 1] != '\\' && chars[i - 1] != 'w'
                    };
                    let next_is_word = if i + 1 < chars.len() {
                        chars[i + 1].is_ascii_alphanumeric() || chars[i + 1] == '_'
                    } else {
                        false
                    };
                    if prev_not_bs_or_w && next_is_word {
                        buf.push('\\');
                        buf.push('-');
                        continue;
                    }
                }
                buf.push(chars[i]);
            }
            out.push(buf);
        }
        out.join("\n")
    }

    fn th(name: &str, section: &str, version: &str, title: &str) -> String {
        format!(
            ".TH {} {} \"\" \"MiOS {}\" \"{}\"\n",
            roff(&name.to_uppercase()),
            section,
            roff(version),
            roff(title)
        )
    }

    fn version_of(root: &Path) -> String {
        let version_file = root.join("VERSION");
        if let Ok(raw) = fs::read_to_string(&version_file) {
            let cleaned: String = raw.chars().filter(|c| !c.is_whitespace()).collect();
            let stripped = cleaned.trim_start_matches(['v', 'V']);
            if !stripped.is_empty() {
                return stripped.to_string();
            }
        }
        "0.0.0".to_string()
    }

    fn prose(path: &Path, limit: usize) -> Vec<String> {
        let Ok(text) = fs::read_to_string(path) else {
            return Vec::new();
        };
        let mut paras = Vec::new();
        let mut buf: Vec<&str> = Vec::new();

        for line in text.split('\n') {
            let s = line.trim();
            if s.is_empty() || s.starts_with('#') || s.starts_with("<!--") {
                if !buf.is_empty() {
                    paras.push(buf.join(" "));
                    buf.clear();
                }
                continue;
            }
            buf.push(s);
        }
        if !buf.is_empty() {
            paras.push(buf.join(" "));
        }

        if paras.len() > limit {
            paras.truncate(limit);
        }
        paras
    }

    pub fn render_pages(root: &Path, ssot: &Value) -> BTreeMap<String, String> {
        let v = version_of(root);
        let mut out = BTreeMap::new();
        let empty_table = toml::value::Table::new();

        let verbs_table = ssot
            .get("verbs")
            .and_then(|v| v.as_table())
            .unwrap_or(&empty_table);

        let mut names: Vec<&str> = verbs_table.keys().map(|k| k.as_str()).collect();
        names.sort_unstable();

        // 1. usr/share/man/man1/mios.1
        let mut idx = vec![
        th("mios", "1", &v, "MiOS Manual"),
        format!(".SH NAME\nmios {DASH} the MiOS verb dispatcher\n"),
        ".SH SYNOPSIS\n.B mios\n.I verb\n".to_string(),
        ".SH DESCRIPTION\n".to_string(),
        format!(
            "MiOS exposes its capabilities as verbs. Every verb below is declared\nin the single source of truth and has its own page: run\n.B man mios{DASH}verb\nfor any of them.\n"
        ),
        ".SH VERBS\n".to_string(),
    ];

        for &n in &names {
            let desc = verbs_table
                .get(n)
                .and_then(|spec| spec.get("description"))
                .and_then(|d| d.as_str())
                .unwrap_or("")
                .trim();
            let fallback_desc = if desc.is_empty() {
                "(no description)"
            } else {
                desc
            };
            idx.push(format!(".TP\n.B {}\n{}\n", roff(n), roff(fallback_desc)));
        }

        idx.push(
        ".SH FILES\n.TP\n.I /usr/share/mios/mios.toml\nThe single source of truth.\n.TP\n.I /usr/share/doc/mios/manual/\nThe distilled prose manual.\n".to_string(),
    );
        idx.push(".SH SEE ALSO\n.BR mios.toml (5),\n.BR mios (7)\n".to_string());
        out.insert(format!("{MAN}/man1/mios.1"), idx.concat());

        // 2. usr/share/man/man1/mios-<n>.1
        for &n in &names {
            let spec = verbs_table.get(n).and_then(|s| s.as_table());
            let desc = spec
                .and_then(|s| s.get("description"))
                .and_then(|d| d.as_str())
                .unwrap_or("")
                .trim();
            let d = if desc.is_empty() {
                "A MiOS verb."
            } else {
                desc
            };

            let d_trimmed_dot = d.trim_end_matches('.');
            let mut body = vec![
                th(&format!("mios-{n}"), "1", &v, "MiOS Verbs"),
                format!(
                    ".SH NAME\nmios{DASH}{} {DASH} {}\n",
                    roff(n),
                    roff(d_trimmed_dot)
                ),
                format!(".SH SYNOPSIS\n.B mios {}\n", roff(n)),
                format!(".SH DESCRIPTION\n{}\n", roff(d)),
            ];

            let surface = spec
                .and_then(|s| s.get("surface"))
                .and_then(|s| s.as_str())
                .unwrap_or("")
                .trim();
            if !surface.is_empty() {
                body.push(format!(
                    ".SH SURFACE\nThis verb runs on the\n.B {}\nsurface.\n",
                    roff(surface)
                ));
            }

            body.push(
            ".SH FILES\n.TP\n.I /usr/share/mios/mios.toml\nDeclares this verb and the description shown above.\n".to_string(),
        );
            body.push(
                ".SH SEE ALSO\n.BR mios (1),\n.BR mios.toml (5),\n.BR mios (7)\n".to_string(),
            );
            out.insert(format!("{MAN}/man1/mios-{n}.1"), body.concat());
        }

        // 3. usr/share/man/man5/mios.toml.5
        let mut cfg = vec![
        th("mios.toml", "5", &v, "MiOS Configuration"),
        format!(".SH NAME\nmios.toml {DASH} the MiOS single source of truth\n"),
        ".SH DESCRIPTION\nEvery MiOS surface is projected from this file: units, ports, themes,\ncontainer definitions, verbs and the installers. Editing a\nprojection by hand is undone by the next build; edit the table\nhere instead.\n.SH SECTIONS\n".to_string(),
    ];

        if let Some(ssot_table) = ssot.as_table() {
            let mut section_keys: Vec<&str> = ssot_table.keys().map(|k| k.as_str()).collect();
            section_keys.sort_unstable();
            for key in section_keys {
                let val = &ssot_table[key];
                let kind = if let Some(t) = val.as_table() {
                    format!("table with {} key(s)", t.len())
                } else if let Some(a) = val.as_array() {
                    format!("array of {} entry(ies)", a.len())
                } else {
                    "scalar".to_string()
                };
                cfg.push(format!(".TP\n.B [{}]\n{}\n", roff(key), roff(&kind)));
            }
        }

        cfg.push(
        ".SH FILES\n.TP\n.I /usr/share/mios/mios.toml\nThe vendor copy that ships in the image.\n.TP\n.I /etc/mios/mios.toml\nHost overrides, layered over the vendor copy.\n".to_string(),
    );
        cfg.push(".SH SEE ALSO\n.BR mios (1),\n.BR mios (7)\n".to_string());
        out.insert(format!("{MAN}/man5/mios.toml.5"), cfg.concat());

        // 4. usr/share/man/man7/mios.7
        let mut con = vec![
            th("mios", "7", &v, "MiOS Concepts"),
            format!(".SH NAME\nmios {DASH} what MiOS is and how its pieces relate\n"),
            ".SH DESCRIPTION\n".to_string(),
        ];
        let ch01_path = root
            .join("usr/share/doc/mios/manual")
            .join("ch01-introduction-and-core-concepts.md");
        let mut ps = prose(&ch01_path, 14);
        if ps.is_empty() {
            ps.push(
                "The distilled manual is installed under /usr/share/doc/mios/manual/.".to_string(),
            );
        }
        for p in ps {
            con.push(format!(".PP\n{}\n", roff(&p)));
        }
        con.push(
            ".SH FILES\n.TP\n.I /usr/share/doc/mios/manual/\nThe full distilled manual.\n"
                .to_string(),
        );
        con.push(".SH SEE ALSO\n.BR mios (1),\n.BR mios.toml (5)\n".to_string());
        out.insert(format!("{MAN}/man7/mios.7"), con.concat());

        // 5. usr/share/man/man7/mios-variants.7 (if [variants.entries] exists)
        if let Some(var_entries) = ssot
            .get("variants")
            .and_then(|v| v.get("entries"))
            .and_then(|e| e.as_table())
        {
            if !var_entries.is_empty() {
                let mut vp = vec![
                th("mios-variants", "7", &v, "MiOS Variants"),
                format!(".SH NAME\nmios{DASH}variants {DASH} the MiOS product line\n"),
                ".SH DESCRIPTION\nEvery variant below is one entry in the single source of truth.\nThe status is measured, not aspirational: shipping means\nbuilt, published and observed; partial means the machinery\nruns but does not yet do the whole job; design means\nspecified with no artifact yet.\n.SH VARIANTS\n".to_string(),
            ];

                let mut var_keys: Vec<&str> = var_entries.keys().map(|k| k.as_str()).collect();
                var_keys.sort_unstable();

                for k in var_keys {
                    let e = var_entries[k].as_table();
                    let title = e
                        .and_then(|t| t.get("title"))
                        .and_then(|v| v.as_str())
                        .unwrap_or(k);
                    let status = e
                        .and_then(|t| t.get("status"))
                        .and_then(|v| v.as_str())
                        .unwrap_or("?");
                    let summary = e
                        .and_then(|t| t.get("summary"))
                        .and_then(|v| v.as_str())
                        .unwrap_or("");
                    let target = e
                        .and_then(|t| t.get("target"))
                        .and_then(|v| v.as_str())
                        .unwrap_or("?");

                    vp.push(format!(
                        ".TP\n.B {} ({})\n{}\n.br\nRuns on: {}.\n",
                        roff(title),
                        roff(status),
                        roff(summary),
                        roff(target)
                    ));
                }

                if let Some(naming) = ssot
                    .get("variants")
                    .and_then(|v| v.get("naming"))
                    .and_then(|n| n.as_table())
                {
                    let title_pattern = naming
                        .get("title_pattern")
                        .and_then(|v| v.as_str())
                        .unwrap_or("");
                    let key_pattern = naming
                        .get("key_pattern")
                        .and_then(|v| v.as_str())
                        .unwrap_or("");
                    vp.push(format!(
                    ".SH NAMING\nTitles read {} and keys read {}: the same name in two registers.\nA suffix names the job, not the size.\n",
                    roff(title_pattern),
                    roff(key_pattern)
                ));
                }

                vp.push(
                    ".SH SEE ALSO\n.BR mios (1),\n.BR mios.toml (5),\n.BR mios (7)\n".to_string(),
                );
                out.insert(format!("{MAN}/man7/mios-variants.7"), vp.concat());
            }
        }

        out
    }

    pub fn validate_man_page_content(rel: &str, content: &str) -> Result<(), String> {
        if !content.starts_with(".TH ") {
            return Err(format!("{rel} missing .TH header"));
        }

        let has_name = content.contains(".SH NAME");
        let has_synopsis_or_desc =
            content.contains(".SH SYNOPSIS") || content.contains(".SH DESCRIPTION");
        if !has_name || !has_synopsis_or_desc {
            return Err(format!(
                "{rel} missing required .SH NAME / SYNOPSIS / DESCRIPTION section"
            ));
        }
        Ok(())
    }

    pub fn validate_man_page(full_path: &Path) -> Result<(), String> {
        let content = fs::read_to_string(full_path)
            .map_err(|e| format!("cannot read {}: {e}", full_path.display()))?;

        validate_man_page_content(&full_path.to_string_lossy(), &content)?;

        // Unix-only external command check if available
        #[cfg(unix)]
        {
            if which_cmd("man") {
                if let Ok(res) = Command::new("man")
                    .args(["-l", &full_path.to_string_lossy()])
                    .output()
                {
                    if !res.status.success() {
                        let err = String::from_utf8_lossy(&res.stderr);
                        return Err(format!(
                            "man -l {} exited {:?}: {err}",
                            full_path.display(),
                            res.status.code()
                        ));
                    }
                    if String::from_utf8_lossy(&res.stdout).trim().is_empty() {
                        return Err(format!(
                            "man -l {} produced empty output",
                            full_path.display()
                        ));
                    }
                }
            }

            if which_cmd("groff") {
                if let Ok(res) = Command::new("groff")
                    .args(["-mandoc", "-Tutf8", &full_path.to_string_lossy()])
                    .output()
                {
                    if !res.status.success() {
                        return Err(format!(
                            "groff -mandoc {} exited {:?}",
                            full_path.display(),
                            res.status.code()
                        ));
                    }
                    if String::from_utf8_lossy(&res.stdout).trim().is_empty() {
                        return Err(format!(
                            "groff -mandoc {} produced empty output",
                            full_path.display()
                        ));
                    }
                }
            }
        }

        Ok(())
    }

    #[cfg(unix)]
    fn which_cmd(name: &str) -> bool {
        Command::new("which")
            .arg(name)
            .output()
            .map(|o| o.status.success())
            .unwrap_or(false)
    }

    pub fn verify(
        first: &BTreeMap<String, String>,
        second: &BTreeMap<String, String>,
        ssot: &Value,
    ) -> Result<(), String> {
        if first != second {
            return Err(
                "render-manpages: non-deterministic render detected across passes".to_string(),
            );
        }

        let mut missing_declared = Vec::new();
        let empty_table = toml::value::Table::new();
        let verbs_table = ssot
            .get("verbs")
            .and_then(|v| v.as_table())
            .unwrap_or(&empty_table);
        for n in verbs_table.keys() {
            let p = format!("{MAN}/man1/mios-{n}.1");
            if !first.contains_key(&p) {
                missing_declared.push(format!("{p} (declared in [verbs.{n}])"));
            }
        }

        if let Some(sc_manpages) = ssot
            .get("testing")
            .and_then(|t| t.get("smoke_components"))
            .and_then(|s| s.get("manpages"))
            .and_then(|m| m.as_array())
        {
            for item in sc_manpages {
                if let Some(p) = item.as_str() {
                    if !first.contains_key(p) {
                        missing_declared.push(format!(
                            "{p} (declared in [testing.smoke_components].manpages)"
                        ));
                    }
                }
            }
        }

        if let Some(var_entries) = ssot
            .get("variants")
            .and_then(|v| v.get("entries"))
            .and_then(|e| e.as_table())
        {
            if !var_entries.is_empty() {
                let vp = format!("{MAN}/man7/mios-variants.7");
                if !first.contains_key(&vp) && !missing_declared.iter().any(|m| m.starts_with(&vp))
                {
                    missing_declared.push(format!("{vp} (declared in [variants.entries])"));
                }
            }
        }

        if !missing_declared.is_empty() {
            let mut lines = vec!["missing declared man page(s):".to_string()];
            for m in missing_declared.iter().take(10) {
                lines.push(format!("  {m}"));
            }
            if missing_declared.len() > 10 {
                lines.push(format!("  ... and {} more", missing_declared.len() - 10));
            }
            return Err(lines.join("\n"));
        }

        Ok(())
    }

    pub fn run_render_manpages(
        root: &Path,
        check: bool,
        validate: bool,
        out_dir: Option<&Path>,
        list: bool,
    ) -> Result<(String, i32), (String, i32)> {
        let toml_path = root.join("usr/share/mios/mios.toml");
        let toml_str = fs::read_to_string(&toml_path).map_err(|e| {
            (
                format!(
                    "render-manpages: {} could not be read: {e}",
                    toml_path.display()
                ),
                1,
            )
        })?;

        let ssot: Value = toml_str.parse().map_err(|e| {
            (
                format!(
                    "render-manpages: failed to parse {}: {e}",
                    toml_path.display()
                ),
                1,
            )
        })?;

        let rendered = render_pages(root, &ssot);

        if list {
            let lines: Vec<String> = rendered.keys().cloned().collect();
            return Ok((lines.join("\n"), 0));
        }

        if let Some(od) = out_dir {
            for (rel, body) in &rendered {
                let full = od.join(rel);
                if let Some(parent) = full.parent() {
                    let _ = fs::create_dir_all(parent);
                }
                if let Err(e) = fs::write(&full, body.as_bytes()) {
                    return Err((
                        format!("render-manpages: write {} failed: {e}", full.display()),
                        1,
                    ));
                }
            }
            return Ok((
                format!("[render-manpages] {} page(s) rendered", rendered.len()),
                0,
            ));
        }

        // ADR-0030 Decision 4 / task_3653: Double-render determinism and registry coverage checks
        if check {
            let second = match (|| -> Result<BTreeMap<String, String>, String> {
                let temp_dir = tempfile::tempdir().map_err(|e| e.to_string())?;
                let exe = std::env::current_exe().map_err(|e| e.to_string())?;
                let child_output = Command::new(&exe)
                    .args([
                        "render-manpages",
                        "--root",
                        &root.to_string_lossy(),
                        "--out-dir",
                        &temp_dir.path().to_string_lossy(),
                    ])
                    .env("TZ", "Asia/Tokyo")
                    .env("LC_ALL", "C.UTF-8")
                    .env("SOURCE_DATE_EPOCH", "1234567890")
                    .current_dir(temp_dir.path())
                    .output()
                    .map_err(|e| e.to_string())?;

                if !child_output.status.success() {
                    return Err(format!(
                        "child process exited {:?}",
                        child_output.status.code()
                    ));
                }

                let mut second_map = BTreeMap::new();
                for rel in rendered.keys() {
                    let file_path = temp_dir.path().join(rel);
                    let content = fs::read_to_string(&file_path).map_err(|e| e.to_string())?;
                    second_map.insert(rel.clone(), content);
                }
                Ok(second_map)
            })() {
                Ok(sec) => sec,
                Err(_) => render_pages(root, &ssot),
            };

            if let Err(err_msg) = verify(&rendered, &second, &ssot) {
                return Err((err_msg, 1));
            }
        }

        let mut drift = Vec::new();

        for (rel, body) in &rendered {
            let full = root.join(rel);
            let body_lf = body.replace("\r\n", "\n");
            let current = fs::read_to_string(&full).ok();
            let matches = match &current {
                Some(curr) => curr == &body_lf,
                None => false,
            };

            if matches {
                continue;
            }

            if check {
                drift.push(rel.clone());
                continue;
            }

            if let Some(parent) = full.parent() {
                let _ = fs::create_dir_all(parent);
            }
            if let Err(e) = fs::write(&full, body_lf.as_bytes()) {
                return Err((
                    format!("render-manpages: write {} failed: {e}", full.display()),
                    1,
                ));
            }
        }

        let mut orphans = Vec::new();
        let man_root = root.join(MAN);
        if man_root.is_dir() {
            for entry in walkdir::WalkDir::new(&man_root)
                .into_iter()
                .filter_map(|e| e.ok())
            {
                if entry.file_type().is_file() {
                    let p = entry.path();
                    let file_name = p.file_name().and_then(|f| f.to_str()).unwrap_or("");
                    if file_name.starts_with("mios") {
                        if let Ok(rel) = p.strip_prefix(root) {
                            let rel_norm = rel.to_string_lossy().replace('\\', "/");
                            if !rendered.contains_key(&rel_norm) {
                                orphans.push(rel_norm);
                            }
                        }
                    }
                }
            }
        }

        if check && (!drift.is_empty() || !orphans.is_empty()) {
            let mut lines = vec![
                "man pages out of sync with the SSOT -- run mios-gen render-manpages:".to_string(),
            ];
            for d in drift.iter().take(10) {
                lines.push(format!("  {d}"));
            }
            for o in orphans.iter().take(5) {
                lines.push(format!("  {o} (no verb declares it)"));
            }
            return Err((lines.join("\n"), 1));
        }

        for o in &orphans {
            let _ = fs::remove_file(root.join(o));
        }

        let mut messages = Vec::new();

        if validate {
            let mut valid_errors = Vec::new();
            let mut validated = 0;
            let temp_val_dir = tempfile::tempdir().ok();

            for (rel, body) in &rendered {
                let full = root.join(rel);
                validated += 1;
                let res = if full.is_file() {
                    validate_man_page(&full)
                } else if let Some(ref tvd) = temp_val_dir {
                    let temp_file = tvd.path().join(
                        Path::new(rel)
                            .file_name()
                            .unwrap_or(std::ffi::OsStr::new("page.1")),
                    );
                    if fs::write(&temp_file, body.as_bytes()).is_ok() {
                        validate_man_page(&temp_file)
                    } else {
                        validate_man_page_content(rel, body)
                    }
                } else {
                    validate_man_page_content(rel, body)
                };
                if let Err(msg) = res {
                    valid_errors.push(msg);
                }
            }
            if validated == 0 {
                valid_errors.push(
                    "no man page was validated, so a clean result here means nothing".to_string(),
                );
            }
            if !valid_errors.is_empty() {
                let mut lines = vec!["man page validation failed:".to_string()];
                for ve in valid_errors.iter().take(10) {
                    lines.push(format!("  {ve}"));
                }
                if valid_errors.len() > 10 {
                    lines.push(format!("  ... and {} more", valid_errors.len() - 10));
                }
                return Err((lines.join("\n"), 1));
            }
            messages.push(format!(
                "[render-manpages] {validated} page(s) rendered and validated"
            ));
        }

        let action = if check { "verified" } else { "rendered" };
        messages.push(format!(
            "[render-manpages] {} page(s) {action}",
            rendered.len()
        ));

        Ok((messages.join("\n"), 0))
    }

    #[cfg(test)]
    mod tests {
        use super::*;

        #[test]
        fn test_verify_identical_passes() {
            let mut pages = BTreeMap::new();
            pages.insert(
                "usr/share/man/man1/mios.1".to_string(),
                "content".to_string(),
            );
            pages.insert(
                "usr/share/man/man1/mios-build.1".to_string(),
                "content".to_string(),
            );
            let ssot_str = r#"
[verbs.build]
description = "build verb"
"#;
            let ssot: Value = ssot_str.parse().unwrap();
            assert!(verify(&pages, &pages, &ssot).is_ok());
        }

        #[test]
        fn test_verify_non_deterministic_fails() {
            let mut first = BTreeMap::new();
            first.insert(
                "usr/share/man/man1/mios.1".to_string(),
                "render A".to_string(),
            );
            let mut second = BTreeMap::new();
            second.insert(
                "usr/share/man/man1/mios.1".to_string(),
                "render B".to_string(),
            );
            let ssot: Value = "".parse().unwrap();
            let err = verify(&first, &second, &ssot).unwrap_err();
            assert!(err.contains("non-deterministic render detected across passes"));
        }

        #[test]
        fn test_verify_missing_declared_page_fails() {
            let pages = BTreeMap::new();
            let ssot_str = r#"
[verbs.update]
description = "update verb"
"#;
            let ssot: Value = ssot_str.parse().unwrap();
            let err = verify(&pages, &pages, &ssot).unwrap_err();
            assert!(err.contains("missing declared man page(s):"));
            assert!(err.contains("usr/share/man/man1/mios-update.1 (declared in [verbs.update])"));
        }

        #[test]
        fn test_foreign_page_survives_check_and_write() {
            let temp_dir = tempfile::tempdir().unwrap();
            let root = temp_dir.path();
            let toml_dir = root.join("usr/share/mios");
            fs::create_dir_all(&toml_dir).unwrap();
            let ssot_content = r#"
[verbs.test]
description = "test"
[testing.smoke_components]
manpages = ["usr/share/man/man1/mios.1", "usr/share/man/man1/mios-test.1"]
"#;
            fs::write(toml_dir.join("mios.toml"), ssot_content).unwrap();

            // First render into root
            let res = run_render_manpages(root, false, false, None, false);
            assert!(res.is_ok());

            // Plant foreign man page: usr/share/man/man1/ls.1
            let foreign_page = root.join("usr/share/man/man1/ls.1");
            fs::write(&foreign_page, "foreign man page content").unwrap();

            // Check mode should succeed without flagging foreign page as orphan
            let check_res = run_render_manpages(root, true, false, None, false);
            assert!(
                check_res.is_ok(),
                "check mode failed with foreign page: {:?}",
                check_res
            );

            // Write mode should not delete foreign page
            let write_res = run_render_manpages(root, false, false, None, false);
            assert!(write_res.is_ok());
            assert!(
                foreign_page.exists(),
                "foreign page ls.1 was deleted by write mode!"
            );
        }
    }
}

pub mod render_ports {
    use regex::Regex;
    use std::collections::BTreeMap;
    use std::fs;
    use std::path::{Path, PathBuf};
    use toml::Value;
    use walkdir::WalkDir;

    pub const DEFAULT_TOML_PATH: &str = "usr/share/mios/mios.toml";

    const SWEEP_PATHS: &[&str] = &["automation", "usr", "etc", "tools"];
    const SWEEP_SKIP: &[&str] = &[
        "manifest.json",
        ".tsv",
        "/reference/",
        "/knowledge/",
        "/target/",
        "/.git/",
        "node_modules",
        "/tools/test_",
        "/tests/",
    ];

    pub fn derive_ports(data: &Value) -> BTreeMap<String, i64> {
        let mut out = BTreeMap::new();
        let categories = match data
            .get("ports")
            .and_then(|p| p.get("categories"))
            .and_then(|c| c.as_table())
        {
            Some(c) => c,
            None => return out,
        };

        let mut cat_keys: Vec<&String> = categories.keys().collect();
        cat_keys.sort();

        for cat in cat_keys {
            let cfg = match categories.get(cat).and_then(|v| v.as_table()) {
                Some(t) => t,
                None => continue,
            };
            let base = cfg.get("base").and_then(|v| v.as_integer()).unwrap_or(0);
            let stride = cfg.get("stride").and_then(|v| v.as_integer()).unwrap_or(1);

            if let Some(members) = cfg.get("members").and_then(|v| v.as_array()) {
                for (idx, member) in members.iter().enumerate() {
                    if let Some(name) = member.as_str() {
                        let trimmed = name.trim();
                        if !trimmed.is_empty() {
                            out.insert(trimmed.to_string(), base + (idx as i64) * stride);
                        }
                    }
                }
            }

            if let Some(pinned) = cfg.get("pinned").and_then(|v| v.as_table()) {
                let mut pinned_keys: Vec<&String> = pinned.keys().collect();
                pinned_keys.sort();
                for name in pinned_keys {
                    if let Some(val) = pinned.get(name).and_then(|v| v.as_integer()) {
                        out.insert(name.clone(), val);
                    }
                }
            }
        }

        out
    }

    pub fn category_band(cfg: &Value) -> (i64, i64) {
        let base = cfg.get("base").and_then(|v| v.as_integer()).unwrap_or(0);
        let stride = cfg.get("stride").and_then(|v| v.as_integer()).unwrap_or(1);
        let n = cfg
            .get("members")
            .and_then(|v| v.as_array())
            .map(|a| a.len())
            .unwrap_or(0);
        if n == 0 {
            (base, base)
        } else {
            (base, base + ((n - 1) as i64) * stride)
        }
    }

    pub fn find_violations(data: &Value) -> Vec<String> {
        let mut problems = Vec::new();
        let categories = match data
            .get("ports")
            .and_then(|p| p.get("categories"))
            .and_then(|c| c.as_table())
        {
            Some(c) => c,
            None => return problems,
        };

        let flat: BTreeMap<String, i64> = match data.get("ports").and_then(|p| p.as_table()) {
            Some(p) => p
                .iter()
                .filter_map(|(k, v)| {
                    if k != "stack_id" && k != "categories" {
                        v.as_integer().map(|i| (k.clone(), i))
                    } else {
                        None
                    }
                })
                .collect(),
            None => BTreeMap::new(),
        };

        // 1. every port declared in exactly one category
        let mut seen: BTreeMap<String, String> = BTreeMap::new();
        let mut cat_keys: Vec<&String> = categories.keys().collect();
        cat_keys.sort();

        for cat in cat_keys {
            let cfg = match categories.get(cat).and_then(|v| v.as_table()) {
                Some(t) => t,
                None => continue,
            };
            let mut names = Vec::new();
            if let Some(members) = cfg.get("members").and_then(|v| v.as_array()) {
                for m in members {
                    if let Some(s) = m.as_str() {
                        names.push(s.to_string());
                    }
                }
            }
            if let Some(pinned) = cfg.get("pinned").and_then(|v| v.as_table()) {
                let mut pk: Vec<&String> = pinned.keys().collect();
                pk.sort();
                for k in pk {
                    names.push(k.clone());
                }
            }

            for name in names {
                let trimmed = name.trim();
                if trimmed.is_empty() {
                    continue;
                }
                if let Some(prev_cat) = seen.get(trimmed) {
                    problems.push(format!(
                    "port '{trimmed}' is claimed by both [ports.categories.{prev_cat}] and [ports.categories.{cat}]"
                ));
                } else {
                    seen.insert(trimmed.to_string(), cat.clone());
                }
            }
        }

        for name in flat.keys() {
            if !seen.contains_key(name) {
                problems.push(format!(
                    "port '{name}' is in the flat [ports] table but belongs to no category"
                ));
            }
        }
        for name in seen.keys() {
            if !flat.contains_key(name) {
                problems.push(format!(
                "port '{name}' is declared in [ports.categories.{}] but missing from the flat [ports] table",
                seen[name]
            ));
            }
        }

        // 2. no two ports share a value
        let derived = derive_ports(data);
        let mut by_value: BTreeMap<i64, Vec<String>> = BTreeMap::new();
        for (name, val) in &derived {
            by_value.entry(*val).or_default().push(name.clone());
        }
        for (val, names) in &by_value {
            if names.len() > 1 {
                problems.push(format!("port collision at {val}: {}", names.join(", ")));
            }
        }

        // 3. category bands must not overlap
        let mut bands = Vec::new();
        for (cat, cfg) in categories {
            if let Some(cfg_tbl) = cfg.as_table() {
                if let Some(members) = cfg_tbl.get("members").and_then(|v| v.as_array()) {
                    if !members.is_empty() {
                        let (lo, hi) = category_band(cfg);
                        bands.push((lo, hi, cat.clone()));
                    }
                }
            }
        }
        bands.sort_by_key(|b| (b.0, b.1, b.2.clone()));
        for pair in bands.windows(2) {
            let (lo1, hi1, ref c1) = pair[0];
            let (lo2, hi2, ref c2) = pair[1];
            if lo2 <= hi1 {
                problems.push(format!(
                    "category band overlap: {c1} [{lo1}-{hi1}] overlaps {c2} [{lo2}-{hi2}]"
                ));
            }
        }

        // 4. the rendered projection must equal the flat table
        for (name, der_val) in &derived {
            if let Some(flat_val) = flat.get(name) {
                if der_val != flat_val {
                    let cat_name = seen.get(name).map(|s| s.as_str()).unwrap_or("unknown");
                    problems.push(format!(
                    "[ports].{name} = {flat_val} but [ports.categories.{cat_name}] derives {der_val} -- run tools/render-ports.py"
                ));
                }
            }
        }

        problems
    }

    pub fn render_table(text: &str, derived: &BTreeMap<String, i64>) -> Result<String, String> {
        let port_line = Regex::new(r"^(\s*)([a-z0-9_]+)(\s*)=(\s*)(\d+)(\s*)(#.*)?$")
            .map_err(|e| format!("regex compile failed: {e}"))?;

        let mut lines: Vec<String> = text.split('\n').map(|s| s.to_string()).collect();
        let mut start = None;
        for (i, line) in lines.iter().enumerate() {
            if line.trim() == "[ports]" {
                start = Some(i);
                break;
            }
        }

        let start_idx = match start {
            Some(s) => s,
            None => return Err("render-ports: no [ports] table found".to_string()),
        };

        for line in lines.iter_mut().skip(start_idx + 1) {
            let stripped = line.trim();
            if stripped.starts_with('[') {
                break;
            }
            let line_without_cr = line.trim_end_matches('\r');
            let has_cr = line.ends_with('\r');

            if let Some(caps) = port_line.captures(line_without_cr) {
                let indent = caps.get(1).map(|m| m.as_str()).unwrap_or("");
                let key = caps.get(2).map(|m| m.as_str()).unwrap_or("");
                let sp1 = caps.get(3).map(|m| m.as_str()).unwrap_or("");
                let sp2 = caps.get(4).map(|m| m.as_str()).unwrap_or("");
                let old = caps.get(5).map(|m| m.as_str()).unwrap_or("");
                let sp3 = caps.get(6).map(|m| m.as_str()).unwrap_or("");
                let comment = caps.get(7).map(|m| m.as_str());

                if key == "stack_id" || !derived.contains_key(key) {
                    continue;
                }

                let new = derived[key].to_string();
                let mut pad = format!("{}{}", sp2, " ".repeat(old.len().saturating_sub(new.len())));
                if new.len() > old.len() {
                    let cut = sp2.len().saturating_sub(new.len() - old.len()).max(1);
                    pad = sp2[..cut].to_string();
                }

                let mut rebuilt = format!("{indent}{key}{sp1}={pad}{new}");
                if let Some(c) = comment {
                    rebuilt.push_str(&format!("{sp3}{c}"));
                }
                if has_cr {
                    rebuilt.push('\r');
                }
                *line = rebuilt;
            }
        }

        Ok(lines.join("\n"))
    }

    pub fn sweep_files(root: &Path) -> Vec<PathBuf> {
        let mut out = Vec::new();
        for top in SWEEP_PATHS {
            let top_dir = root.join(top);
            if !top_dir.exists() {
                continue;
            }
            for entry in WalkDir::new(top_dir)
                .into_iter()
                .filter_entry(|e| {
                    let name = e.file_name().to_string_lossy();
                    name != ".git"
                        && name != "target"
                        && name != "node_modules"
                        && name != "__pycache__"
                })
                .filter_map(|e| e.ok())
            {
                if entry.file_type().is_file() {
                    let path = entry.path();
                    let path_str = path.to_string_lossy().replace('\\', "/");
                    let rel = path
                        .strip_prefix(root)
                        .unwrap_or(path)
                        .to_string_lossy()
                        .replace('\\', "/");
                    let rel_with_slash = format!("/{rel}");

                    if SWEEP_SKIP.iter().any(|s| {
                        path_str.contains(s) || rel.contains(s) || rel_with_slash.contains(s)
                    }) {
                        continue;
                    }
                    out.push(path.to_path_buf());
                }
            }
        }
        out
    }

    pub fn sync_fallbacks(
        root: &Path,
        derived: &BTreeMap<String, i64>,
        apply: bool,
    ) -> Vec<String> {
        let fallback_re = match Regex::new(r"\$\{(MIOS_PORTS?_([A-Z0-9_]+)):-(\d+)\}") {
            Ok(r) => r,
            Err(_) => return Vec::new(),
        };

        let mut upper: BTreeMap<String, i64> = BTreeMap::new();
        for (k, v) in derived {
            upper.insert(k.to_uppercase(), *v);
        }

        let mut problems = Vec::new();
        for path in sweep_files(root) {
            let text = match fs::read_to_string(&path) {
                Ok(t) => t,
                Err(_) => continue,
            };
            if !text.contains("MIOS_PORT_") && !text.contains("MIOS_PORTS_") {
                continue;
            }

            let mut changed: Vec<(String, String, i64)> = Vec::new();
            let new_text = fallback_re
                .replace_all(&text, |caps: &regex::Captures| {
                    let variable = caps.get(1).map(|m| m.as_str()).unwrap_or("");
                    let key = caps.get(2).map(|m| m.as_str()).unwrap_or("");
                    let lit = caps.get(3).map(|m| m.as_str()).unwrap_or("");
                    let name = if key == "GUACAMOLE" {
                        "GUACAMOLE_WEB"
                    } else {
                        key
                    };
                    if let Some(&want) = upper.get(name) {
                        if want.to_string() != lit {
                            changed.push((variable.to_string(), lit.to_string(), want));
                            return format!("${{{variable}:-{want}}}");
                        }
                    }
                    caps.get(0).map(|m| m.as_str()).unwrap_or("").to_string()
                })
                .to_string();

            if !changed.is_empty() {
                let rel = path
                    .strip_prefix(root)
                    .unwrap_or(&path)
                    .to_string_lossy()
                    .replace('\\', "/");
                for (key, lit, want) in &changed {
                    problems.push(format!("{rel}: {key} fallback :-{lit} != SSOT {want}"));
                }
                if apply {
                    let _ = fs::write(&path, new_text);
                }
            }
        }

        problems
    }

    pub fn run_render_ports(
        root: &Path,
        toml_override: Option<&Path>,
        check: bool,
        print_ports: bool,
    ) -> Result<(String, i32), (String, i32)> {
        let toml_path = match toml_override {
            Some(p) => p.to_path_buf(),
            None => match std::env::var("MIOS_TOML") {
                Ok(v) if !v.trim().is_empty() => PathBuf::from(v.trim()),
                _ => root.join(DEFAULT_TOML_PATH),
            },
        };

        let content = fs::read_to_string(&toml_path).map_err(|e| {
            (
                format!(
                    "render-ports: {} could not be read: {e}",
                    toml_path.display()
                ),
                1,
            )
        })?;

        let parsed: Value = content.parse().map_err(|e| {
            (
                format!("render-ports: {} did not parse: {e}", toml_path.display()),
                1,
            )
        })?;

        let derived = derive_ports(&parsed);

        if print_ports {
            let mut by_val_name: Vec<(&String, &i64)> = derived.iter().collect();
            by_val_name.sort_by_key(|&(name, &val)| (val, name));
            let mut out = String::new();
            for (name, val) in by_val_name {
                out.push_str(&format!("{val:>6}  {name}\n"));
            }
            return Ok((out, 0));
        }

        let mut problems = find_violations(&parsed);
        let fallback_problems = sync_fallbacks(root, &derived, !check);

        let num_categories = parsed
            .get("ports")
            .and_then(|p| p.get("categories"))
            .and_then(|c| c.as_table())
            .map(|t| t.len())
            .unwrap_or(0);

        if check {
            problems.extend(fallback_problems);
            if !problems.is_empty() {
                let mut msg = "[render-ports] port schema drift:\n".to_string();
                for p in &problems {
                    msg.push_str(&format!("    {p}\n"));
                }
                return Err((msg, 1));
            }
            let msg = format!(
                "[render-ports] {} ports derive cleanly from {} categories\n",
                derived.len(),
                num_categories
            );
            return Ok((msg, 0));
        }

        // Apply mode
        let fatal: Vec<&String> = problems
            .iter()
            .filter(|p| {
                !p.contains("run tools/render-ports.py") && !p.contains("run mios-gen render-ports")
            })
            .collect();

        if !fatal.is_empty() {
            let mut msg = "[render-ports] cannot render, fix the schema first:\n".to_string();
            for p in fatal {
                msg.push_str(&format!("    {p}\n"));
            }
            return Err((msg, 1));
        }

        let mut out_msg = String::new();
        if !fallback_problems.is_empty() {
            out_msg.push_str(&format!(
                "[render-ports] re-synced {} stale ${{MIOS_PORT_*:-N}} fallback(s) from SSOT\n",
                fallback_problems.len()
            ));
        }

        let new_toml = render_table(&content, &derived).map_err(|e| (e, 1))?;
        fs::write(&toml_path, new_toml).map_err(|e| {
            (
                format!("render-ports: failed to write {}: {e}", toml_path.display()),
                1,
            )
        })?;

        out_msg.push_str(&format!(
            "[render-ports] rendered {} ports into the flat [ports] table\n",
            derived.len()
        ));

        Ok((out_msg, 0))
    }

    #[cfg(test)]
    mod tests {
        use super::*;

        #[test]
        fn canonical_and_legacy_fallback_drift_is_detected_and_repaired() {
            let root = tempfile::tempdir().unwrap();
            fs::create_dir(root.path().join("automation")).unwrap();
            let path = root.path().join("automation/fixture.sh");
            let canonical = "MIOS_PORTS_AGENT_PIPE";
            let legacy = "MIOS_PORT_AGENT_PIPE";
            let previous = format!(
                "a=${{{canonical}:-1}}\nb=${{{legacy}:-2}}\nc=${{MIOS_PORTS_UNKNOWN:-3}}\n"
            );
            fs::write(&path, &previous).unwrap();
            let ports = BTreeMap::from([("agent_pipe".into(), 8700)]);
            assert_eq!(sync_fallbacks(root.path(), &ports, false).len(), 2);
            assert_eq!(fs::read_to_string(&path).unwrap(), previous);
            assert_eq!(sync_fallbacks(root.path(), &ports, true).len(), 2);
            assert_eq!(fs::read_to_string(&path).unwrap(), "a=${MIOS_PORTS_AGENT_PIPE:-8700}\nb=${MIOS_PORT_AGENT_PIPE:-8700}\nc=${MIOS_PORTS_UNKNOWN:-3}\n");
            assert!(sync_fallbacks(root.path(), &ports, false).is_empty());
        }
    }
}

/// Native Lua projection for current Hyprland; legacy hyprlang remains available for older images.
/// Boot/session coupling uses only administrator-owned projection layers.
pub mod desktop_boot {
    use std::{fs, os::unix::fs::MetadataExt, path::Path, process::Command};
    use toml::Value;

    pub fn enabled(data: &Value) -> Result<bool, String> {
        match data.get("desktop").and_then(|d| d.get("hyprland"))
            .and_then(|h| h.get("boot_sync")).and_then(|b| b.get("enabled")) {
            None => Ok(false),
            Some(v) => v.as_bool().ok_or_else(|| "desktop.hyprland.boot_sync.enabled must be boolean".into()),
        }
    }

    pub fn boot_profile(data: &Value, cmdline: &str) -> Result<String, String> {
        let count = cmdline.split_whitespace().filter(|v| v.starts_with("mios.desktop.gpu=")).count();
        if count > 1 { return Err("Ambiguous desktop GPU boot profile".into()); }
        super::render_hyprland::profile_name(data, None, cmdline)
    }

    fn validate_entry(profile: &str, source: &str) -> Result<Vec<String>, String> {
        if !matches!(profile, "igpu" | "dgpu") ||
            !source.starts_with("# AI-hint: Generated MiOS desktop hardware boot profile;") ||
            source.lines().any(|l| l.trim_start().starts_with("efi ")) {
            return Err("GPU switching requires a managed BLS linux/initrd entry".into());
        }
        let opts: Vec<_> = source.lines().filter_map(|l| l.strip_prefix("options ")).collect();
        if opts.len() != 1 || opts[0].split_whitespace()
            .filter(|v| v.starts_with("mios.desktop.gpu=")).collect::<Vec<_>>() !=
            vec![format!("mios.desktop.gpu={profile}").as_str()] {
            return Err("Boot entry GPU identity does not match the requested session".into());
        }
        let kernels: Vec<_> = source.lines().filter_map(|l| l.strip_prefix("linux ")).collect();
        let initrds: Vec<_> = source.lines().filter_map(|l| l.strip_prefix("initrd ")).collect();
        if kernels.len() != 1 || initrds.is_empty() { return Err("Boot entry lacks a kernel or initrd".into()); }
        let files: Vec<String> = kernels.into_iter().chain(initrds).flat_map(str::split_whitespace)
            .filter(|v| !v.starts_with('$')).map(str::to_string).collect();
        if files.len() < 2 || files.iter().any(|v| !v.starts_with('/') ||
            Path::new(v).components().any(|c| c == std::path::Component::ParentDir)) {
            return Err("Boot entry contains an invalid kernel or initrd path".into());
        }
        Ok(files)
    }

    /// The privileged verb accepts no root, executable, environment or entry path
    /// from the caller. User TOML is never read by this operation.
    pub fn select(profile: &str, prepare_only: bool, dry_run: bool) -> Result<String, String> {
        let data = mios_resolver::resolve_projection(Path::new("/")).map_err(|e| e.to_string())?;
        if !enabled(&data)? { return Err("Desktop GPU boot synchronization is disabled".into()); }
        if !matches!(profile, "igpu" | "dgpu") { return Err("Unsupported desktop GPU profile".into()); }
        let entry = format!("mios-desktop-{profile}");
        let path = format!("/boot/loader/entries/{entry}.conf");
        let source = fs::read_to_string(&path).map_err(|e| format!("{path}: {e}"))?;
        for file in validate_entry(profile, &source)? {
            let path = Path::new("/boot").join(file.trim_start_matches('/'));
            if !path.is_file() { return Err(format!("Boot artifact missing: {}", path.display())); }
        }
        let current = boot_profile(&data, &fs::read_to_string("/proc/cmdline").map_err(|e| e.to_string())?)?;
        let reboot = current != profile;
        if dry_run {
            return Ok(serde_json::json!({"current_profile":current,"requested_profile":profile,
                "grub_entry":entry,"reboot_required":reboot,"prepare_only":prepare_only}).to_string());
        }
        if fs::metadata("/proc/self").map_err(|e| e.to_string())?.uid() != 0 {
            return Err("Changing the GPU boot profile requires root".into());
        }
        let run = |program: &str, args: &[&str]| -> Result<(),String> {
            let result = Command::new(program).args(args).env_clear()
                .env("PATH", "/usr/sbin:/usr/bin:/sbin:/bin").output().map_err(|e| e.to_string())?;
            if result.status.success() { Ok(()) }
            else { Err(format!("{program}: {}",String::from_utf8_lossy(&result.stderr).trim())) }
        };
        run("/usr/sbin/grub2-set-default", &[&entry])?;
        run("/usr/sbin/grub2-reboot", &[&entry])?;
        let env = Command::new("/usr/sbin/grub2-editenv").arg("list").env_clear()
            .env("PATH", "/usr/sbin:/usr/bin:/sbin:/bin").output().map_err(|e| e.to_string())?;
        let env = String::from_utf8_lossy(&env.stdout);
        if !env.lines().any(|v| v == format!("saved_entry={entry}")) ||
            !env.lines().any(|v| v == format!("next_entry={entry}")) {
            return Err("GRUB did not retain the selected GPU boot entry".into());
        }
        if reboot && !prepare_only { run("/usr/bin/systemctl", &["reboot"])?; }
        Ok(format!("GRUB selected {entry}; reboot required: {reboot}"))
    }

    #[cfg(test)]
    mod tests {
        use super::*;
        #[test]
        fn managed_entries_reject_wrong_profile_uki_and_path_traversal() {
            let src = "# AI-hint: Generated MiOS desktop hardware boot profile;\nlinux /vmlinuz\ninitrd /initrd $tuned_initrd\noptions root=UUID=keep mios.desktop.gpu=dgpu\n";
            assert_eq!(validate_entry("dgpu", src).unwrap(), vec!["/vmlinuz", "/initrd"]);
            for bad in [src.replace("=dgpu", "=igpu"), src.replace("/initrd", "/../initrd"),
                format!("{src}efi /signed.efi\n"), format!("{src}options duplicate\n"),
                src.replace("# AI-hint:", "# unmanaged:")] {
                assert!(validate_entry("dgpu", &bad).is_err(), "{bad}");
            }
            assert!(validate_entry("../../escape", src).is_err());
        }
        #[test]
        fn boot_choice_overrides_config_and_rejects_ambiguous_command_line() {
            let data: Value = "[desktop.hyprland]\nhardware_profile='igpu'\n[desktop.hyprland.hardware_profiles.igpu]\n[desktop.hyprland.hardware_profiles.dgpu]\n".parse().unwrap();
            assert_eq!(boot_profile(&data,"quiet mios.desktop.gpu=dgpu").unwrap(),"dgpu");
            assert_eq!(boot_profile(&data,"quiet").unwrap(),"igpu");
            assert!(boot_profile(&data,"mios.desktop.gpu=dgpu mios.desktop.gpu=igpu").is_err());
            assert!(boot_profile(&data,"mios.desktop.gpu=missing").is_err());
        }
    }
}

pub mod render_hyprland {
    use std::{
        fs,
        path::{Path, PathBuf},
    };
    use toml::Value;

    /// Lua string literal. JSON escaping is not Lua: `\u00XX` is a Lua 5.3+ escape that
    /// LuaJIT/5.1 reject, and JSON's `\/` is no Lua escape at all. Emit only escapes every Lua
    /// dialect reads: `\\ \" \n \r \t`, fixed-width decimal `\ddd` for the other control bytes and
    /// DEL (three digits, so a following digit is never absorbed); UTF-8 passes through unchanged.
    fn lua_string(s: &str) -> String {
        let mut out = String::with_capacity(s.len() + 2);
        out.push('"');
        for c in s.chars() {
            match c {
                '\\' => out.push_str("\\\\"),
                '"' => out.push_str("\\\""),
                '\n' => out.push_str("\\n"),
                '\r' => out.push_str("\\r"),
                '\t' => out.push_str("\\t"),
                c if (c as u32) < 0x20 || c as u32 == 0x7f => {
                    out.push_str(&format!("\\{:03}", c as u32))
                }
                c => out.push(c),
            }
        }
        out.push('"');
        out
    }

    fn lua(value: &Value) -> Result<String, String> {
        Ok(match value {
            Value::String(s) => lua_string(s),
            Value::Integer(n) => n.to_string(),
            Value::Float(n) if n.is_finite() => n.to_string(),
            Value::Boolean(b) => b.to_string(),
            Value::Array(items) => format!(
                "{{{}}}",
                items
                    .iter()
                    .map(lua)
                    .collect::<Result<Vec<_>, _>>()?
                    .join(", ")
            ),
            Value::Table(items) => format!(
                "{{{}}}",
                items
                    .iter()
                    .map(|(k, v)| Ok(format!(
                        "[{}] = {}",
                        lua(&Value::String(k.clone()))?,
                        lua(v)?
                    )))
                    .collect::<Result<Vec<_>, String>>()?
                    .join(", ")
            ),
            _ => {
                return Err(
                    "Hyprland Lua projection requires finite scalar/table/array values".into(),
                )
            }
        })
    }
    fn at<'a>(value: &'a Value, path: &str) -> Result<&'a Value, String> {
        path.split('.').try_fold(value, |v, k| {
            v.get(k).ok_or_else(|| format!("Missing SSOT {path}"))
        })
    }
    fn string(value: &Value, path: &str) -> Result<String, String> {
        lua(at(value, path)?)
    }
    /// The plain (unquoted) string at `path`.
    fn string_value(value: &Value, path: &str) -> Result<String, String> {
        at(value, path)?
            .as_str()
            .filter(|s| !s.is_empty())
            .map(str::to_owned)
            .ok_or_else(|| format!("SSOT {path} must be a nonempty string"))
    }

    /// [desktop.screenshot] (mios-shot) -> its keybinds (hl.bind exec "<binary> <args>"), the no-animation layer rules for
    /// its freeze / selector / flash surfaces and -- only while [desktop.hyprland.config.ecosystem].enforce_permissions is
    /// on -- screencopy permissions for the binary, grim and xdg-desktop-portal-hyprland (XDPH runs grim itself). Absent
    /// or enabled = false: nothing. One mios_section, so a compositor that rejects a rule keeps the rest of the session.
    fn screenshot_lua(data: &Value) -> Result<String, String> {
        let Some(shot) = data.get("desktop").and_then(|d| d.get("screenshot")) else {
            return Ok(String::new());
        };
        if shot.get("enabled").and_then(Value::as_bool) == Some(false) {
            return Ok(String::new());
        }
        let binary = string_value(shot, "binary")?;
        if !binary.starts_with('/') || binary.chars().any(|c| c.is_whitespace() || "'\"\\$`;&|<>(){}*?[]~#".contains(c)) {
            return Err("desktop.screenshot.binary must be an absolute path without shell metacharacters".into());
        }
        let mut body = String::new();
        if let Some(binds) = shot.get("bindings") {
            for b in binds.as_array().ok_or("desktop.screenshot.bindings must be an array")? {
                let key = b
                    .get("key")
                    .and_then(Value::as_str)
                    .filter(|k| !k.trim().is_empty())
                    .ok_or("desktop.screenshot.bindings entries need a nonempty key")?;
                let args = b.get("args").and_then(Value::as_str).unwrap_or("");
                if !args
                    .split_whitespace()
                    .all(|w| w.bytes().all(|c| c.is_ascii_alphanumeric() || b"-_=.,:".contains(&c)))
                {
                    return Err(format!("desktop.screenshot.bindings {key:?}: args must be plain mios-shot words"));
                }
                let cmd = std::iter::once(binary.as_str()).chain(args.split_whitespace()).collect::<Vec<_>>().join(" ");
                body += &format!(
                    " hl.bind({}, hl.dsp.exec_cmd({}))\n",
                    lua(&Value::String(key.into()))?,
                    lua(&Value::String(cmd))?
                );
            }
        }
        if let Some(ns) = shot.get("no_anim_namespaces") {
            for n in ns.as_array().ok_or("desktop.screenshot.no_anim_namespaces must be an array")? {
                let n = n
                    .as_str()
                    .filter(|n| !n.is_empty() && n.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'-' || c == b'_'))
                    .ok_or("desktop.screenshot.no_anim_namespaces entries must be [A-Za-z0-9_-] layer namespaces")?;
                body += &format!(" hl.layer_rule({{[\"match\"] = {{[\"namespace\"] = \"^({n})$\"}}, [\"no_anim\"] = true}})\n");
            }
        }
        let enforce = data
            .get("desktop")
            .and_then(|d| d.get("hyprland"))
            .and_then(|h| h.get("config"))
            .and_then(|c| c.get("ecosystem"))
            .and_then(|e| e.get("enforce_permissions"))
            .and_then(Value::as_bool)
            == Some(true);
        if enforce {
            let mode = shot.get("hyprland_permission").and_then(Value::as_str).unwrap_or("allow");
            if !matches!(mode, "allow" | "ask" | "deny") {
                return Err("desktop.screenshot.hyprland_permission must be allow, ask or deny".into());
            }
            let exe: String = binary
                .chars()
                .flat_map(|c| if ".+*?()[]{}|^$\\".contains(c) { vec!['\\', c] } else { vec![c] })
                .collect();
            for re in [exe.as_str(), "/usr/(bin|local/bin)/grim", "/usr/(lib|libexec|lib64)/xdg-desktop-portal-hyprland"] {
                body += &format!(
                    " hl.permission({}, \"screencopy\", {})\n",
                    lua(&Value::String(re.into()))?,
                    lua(&Value::String(mode.into()))?
                );
            }
        }
        if body.is_empty() {
            return Ok(String::new());
        }
        Ok(format!("mios_section(\"screenshot\", function()\n{body}end)\n"))
    }

    /// Words [desktop.shaderglass.window].suppress_event may carry, and the ones it must carry: the pane never
    /// takes focus by activation and never goes fullscreen or maximized (Wine asks for fullscreen when a
    /// borderless window covers its monitor, which `-f` does at start).
    const SHADERGLASS_SUPPRESS: &[&str] = &["fullscreen", "maximize", "activate", "activatefocus", "fullscreenoutput"];
    const SHADERGLASS_SUPPRESS_REQUIRED: &[&str] = &["fullscreen", "maximize", "activate", "activatefocus"];

    /// One key combination in a comparable form: "+"-separated parts, trimmed, upper-cased, sorted.
    fn key_combo(key: &str) -> String {
        let mut parts: Vec<String> = key.split('+').map(|p| p.trim().to_ascii_uppercase()).filter(|p| !p.is_empty()).collect();
        parts.sort();
        parts.join("+")
    }

    /// Every key combination the rest of the projection binds (desktop bindings, terminal and launcher keys,
    /// [keybindings] actions, the workspace keys, mios-shot and the Hypr3D toggle while they are enabled).
    fn bound_key_combos(data: &Value) -> Result<std::collections::BTreeSet<String>, String> {
        let mut out = std::collections::BTreeSet::new();
        let h = at(data, "desktop.hyprland")?;
        let main = string_value(h, "main_modifier")?;
        for key in ["Q", "Return"] {
            out.insert(key_combo(&format!("{main} + {key}")));
        }
        for key in h.get("launcher_keys").and_then(Value::as_array).into_iter().flatten().filter_map(Value::as_str) {
            out.insert(key_combo(&format!("{main} + {key}")));
        }
        if let (Some(modifier), Some(actions)) = (
            data.get("keybindings").and_then(|k| k.get("desktop_modifier")).and_then(Value::as_str),
            data.get("keybindings").and_then(|k| k.get("actions")).and_then(Value::as_array),
        ) {
            let modifier = modifier.split_whitespace().collect::<Vec<_>>().join(" + ");
            for key in actions.iter().filter_map(|a| a.get("key").and_then(Value::as_str)) {
                out.insert(key_combo(&format!("{modifier} + {key}")));
            }
        }
        for key in h.get("bindings").and_then(Value::as_array).into_iter().flatten().filter_map(|b| b.get("key").and_then(Value::as_str)) {
            out.insert(key_combo(key));
        }
        let count = h.get("workspace_count").and_then(Value::as_integer).unwrap_or(10).clamp(1, 10);
        for i in 1..=count {
            out.insert(key_combo(&format!("{main} + {}", i % 10)));
            out.insert(key_combo(&format!("{main} + SHIFT + {}", i % 10)));
        }
        if let Some(shot) = data.get("desktop").and_then(|d| d.get("screenshot")).filter(|s| s.get("enabled").and_then(Value::as_bool) != Some(false)) {
            for key in shot.get("bindings").and_then(Value::as_array).into_iter().flatten().filter_map(|b| b.get("key").and_then(Value::as_str)) {
                out.insert(key_combo(key));
            }
        }
        if let Some(key) = h
            .get("hypr3d")
            .filter(|p| p.get("enabled").and_then(Value::as_bool) == Some(true))
            .and_then(|p| p.get("toggle_key"))
            .and_then(Value::as_str)
        {
            out.insert(key_combo(key));
        }
        Ok(out)
    }

    /// [desktop.shaderglass] (`miosd shaderglass`: ShaderGlass under Wine as a pinned, click-through,
    /// Hyprglass-decorated live clone over one target window) -> its window rules and keybinds. Absent or
    /// enabled != true: nothing. One mios_section, so a compositor that rejects a rule keeps the session.
    /// Rules, all scoped to the anchored ShaderGlass class/title patterns or the pane's tag:
    /// - the pane: float + pin (pinned floating windows draw after every workspace window and open special
    ///   workspace, so the pane sits above every window and glass pane and below the shell layers);
    ///   no_initial_focus, focus_on_activate = false and suppress_event (never steals focus, never goes
    ///   fullscreen/maximized); no_anim (follows its target without the move animation); its pane tag; and,
    ///   only with exclude_from_captures, no_screen_share (Hyprland 0.56 blacks it out of output captures);
    /// - the glass preset tag: the desktop's one preset ([desktop.hyprland.hyprglass.config].default_preset),
    ///   while Hyprglass is enabled;
    /// - passthrough: windows carrying passthrough_tag get no_focus (hit-testing skips them, so pointer and
    ///   keyboard reach the target underneath); miosd adds the tag to an overlay pane and drops it in setup;
    /// - the tool windows (shader browser, parameters): float, centred and pinned, above the pinned pane.
    ///
    /// Self-capture never happens by construction: the pane captures one target window (xdph window capture
    /// renders only that window) and miosd refuses a ShaderGlass window as a target.
    pub fn shaderglass_lua(data: &Value) -> Result<String, String> {
        let Some(sg) = data.get("desktop").and_then(|d| d.get("shaderglass")) else {
            return Ok(String::new());
        };
        if sg.get("enabled").and_then(Value::as_bool) != Some(true) {
            return Ok(String::new());
        }
        let binary = string_value(sg, "binary")?;
        if !binary.starts_with('/') || binary.chars().any(|c| c.is_whitespace() || "'\"\\$`;&|<>(){}*?[]~#".contains(c)) {
            return Err("desktop.shaderglass.binary must be an absolute path without shell metacharacters".into());
        }
        let w = at(sg, "window").map_err(|_| "[desktop.shaderglass.window] is required".to_string())?;
        let pattern = |k: &str| -> Result<String, String> {
            let p = string_value(w, k).map_err(|_| format!("desktop.shaderglass.window.{k} must be a nonempty regex"))?;
            if !(p.starts_with('^') && p.ends_with('$')) {
                return Err(format!("desktop.shaderglass.window.{k} must be anchored (^...$) so it matches only ShaderGlass windows"));
            }
            regex::Regex::new(&p).map_err(|e| format!("desktop.shaderglass.window.{k}: {e}"))?;
            Ok(p)
        };
        let (class, title, tools) = (pattern("class")?, pattern("title")?, pattern("tools_title")?);
        let tag = |k: &str| -> Result<String, String> {
            w.get(k)
                .and_then(Value::as_str)
                .filter(|t| !t.is_empty() && t.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'-' || c == b'_'))
                .map(str::to_owned)
                .ok_or(format!("desktop.shaderglass.window.{k} must be a [A-Za-z0-9_-] tag"))
        };
        let (pane_tag, pass_tag) = (tag("pane_tag")?, tag("passthrough_tag")?);
        if pane_tag == pass_tag {
            return Err("desktop.shaderglass.window.pane_tag and passthrough_tag must differ".into());
        }
        let layer = string_value(w, "layer")?;
        if layer != "pinned" {
            return Err(format!("desktop.shaderglass.window.layer {layer:?}: only \"pinned\" (float + pin: above every window and glass pane, below the shell layers) is supported"));
        }
        let suppress = string_value(w, "suppress_event")?;
        let words: Vec<&str> = suppress.split_whitespace().collect();
        if let Some(bad) = words.iter().find(|e| !SHADERGLASS_SUPPRESS.contains(e)) {
            return Err(format!("desktop.shaderglass.window.suppress_event: unknown event {bad:?} (one of {SHADERGLASS_SUPPRESS:?})"));
        }
        if let Some(missing) = SHADERGLASS_SUPPRESS_REQUIRED.iter().find(|e| !words.contains(e)) {
            return Err(format!("desktop.shaderglass.window.suppress_event must suppress {missing:?}: the pane never takes focus or the whole output"));
        }
        let flag = |k: &str| -> Result<bool, String> {
            w.get(k).and_then(Value::as_bool).ok_or(format!("desktop.shaderglass.window.{k} must be a boolean"))
        };
        let (no_anim, exclude) = (flag("no_anim")?, flag("exclude_from_captures")?);
        // One material: the pane takes the preset every other window draws with.
        let preset = string_value(w, "glass_preset")?;
        let glass = data
            .get("desktop")
            .and_then(|d| d.get("hyprland"))
            .and_then(|h| h.get("hyprglass"))
            .filter(|g| g.get("enabled").and_then(Value::as_bool) == Some(true));
        if let Some(g) = glass {
            if g.get("presets").and_then(|p| p.get(&preset)).is_none() {
                return Err(format!("desktop.shaderglass.window.glass_preset: hyprglass preset {preset} not found"));
            }
            let default = g.get("config").and_then(|c| c.get("default_preset")).and_then(Value::as_str);
            if default != Some(preset.as_str()) {
                return Err(format!(
                    "desktop.shaderglass.window.glass_preset {preset:?} must be the desktop's default_preset {default:?}: one glass material for every window"
                ));
            }
        }
        let str_v = |s: &str| Value::String(s.to_owned());
        let matcher = |pairs: &[(&str, &str)]| {
            Value::Table(pairs.iter().map(|(k, v)| ((*k).to_owned(), str_v(v))).collect())
        };
        let mut pane = toml::map::Map::new();
        pane.insert("name".into(), str_v("mios-shaderglass"));
        pane.insert("match".into(), matcher(&[("class", &class), ("title", &title)]));
        pane.insert("float".into(), Value::Boolean(true));
        pane.insert("pin".into(), Value::Boolean(true));
        pane.insert("no_initial_focus".into(), Value::Boolean(true));
        pane.insert("focus_on_activate".into(), Value::Boolean(false));
        pane.insert("suppress_event".into(), str_v(&words.join(" ")));
        pane.insert("tag".into(), str_v(&format!("+{pane_tag}")));
        pane.insert("hyprbars:no_bar".into(), Value::Boolean(true));
        if no_anim {
            pane.insert("no_anim".into(), Value::Boolean(true));
        }
        if exclude {
            pane.insert("no_screen_share".into(), Value::Boolean(true));
        }
        let mut body = format!(" hl.window_rule({})\n", lua(&Value::Table(pane))?);
        if glass.is_some() {
            let mut rule = toml::map::Map::new();
            rule.insert("name".into(), str_v("mios-shaderglass-glass"));
            rule.insert("match".into(), matcher(&[("class", &class), ("title", &title)]));
            rule.insert("tag".into(), str_v(&format!("+hyprglass_preset_{preset}")));
            body += &format!(" hl.window_rule({})\n", lua(&Value::Table(rule))?);
        }
        let mut pass = toml::map::Map::new();
        pass.insert("name".into(), str_v("mios-shaderglass-passthrough"));
        pass.insert("match".into(), matcher(&[("tag", &pass_tag)]));
        pass.insert("no_focus".into(), Value::Boolean(true));
        body += &format!(" hl.window_rule({})\n", lua(&Value::Table(pass))?);
        let mut tool = toml::map::Map::new();
        tool.insert("name".into(), str_v("mios-shaderglass-tools"));
        tool.insert("match".into(), matcher(&[("class", &class), ("title", &tools)]));
        tool.insert("float".into(), Value::Boolean(true));
        tool.insert("center".into(), Value::Boolean(true));
        tool.insert("pin".into(), Value::Boolean(true));
        body += &format!(" hl.window_rule({})\n", lua(&Value::Table(tool))?);
        let taken = bound_key_combos(data)?;
        let mut mine = std::collections::BTreeSet::new();
        for b in sg.get("bindings").and_then(Value::as_array).ok_or("desktop.shaderglass.bindings must be an array")? {
            let key = b
                .get("key")
                .and_then(Value::as_str)
                .filter(|k| !k.trim().is_empty())
                .ok_or("desktop.shaderglass.bindings entries need a nonempty key")?;
            let args = b.get("args").and_then(Value::as_str).unwrap_or("");
            if args.trim().is_empty()
                || !args
                    .split_whitespace()
                    .all(|w| w.bytes().all(|c| c.is_ascii_alphanumeric() || b"-_=.,:".contains(&c)))
            {
                return Err(format!("desktop.shaderglass.bindings {key:?}: args must be plain mios-shaderglass words"));
            }
            let combo = key_combo(key);
            if taken.contains(&combo) || !mine.insert(combo) {
                return Err(format!("desktop.shaderglass.bindings {key:?} is already bound"));
            }
            let cmd = std::iter::once(binary.as_str()).chain(args.split_whitespace()).collect::<Vec<_>>().join(" ");
            body += &format!(" hl.bind({}, hl.dsp.exec_cmd({}))\n", lua(&str_v(key))?, lua(&Value::String(cmd))?);
        }
        Ok(format!("mios_section(\"shaderglass\", function()\n{body}end)\n"))
    }

    /// [desktop.hyprland.hyprglass.config] keys only the MiOS hyprglass patch registers (hyprglass-mios-glass.patch).
    pub const HYPRGLASS_MIOS_KEYS: &[&str] = &["edge_pass", "edge_pass_width"];
    /// hyprbars config keys only the MiOS hyprbars patch registers beyond its long-standing overlay keys: the
    /// hyprglass-drawn tab (glass_plugin, button_glass) and the mobile tabs (mobile_*).
    pub const HYPRBARS_MIOS_KEYS: &[&str] = &["glass_plugin", "button_glass"];

    /// [desktop.hyprland.hyprbars.mobile] -> the plugin's mobile_* keys (MiOS mobile tabs). Absent = none.
    /// touch_target >= 44 logical px, palettes are [colors] keys, close_button indexes a [[buttons]] entry whose
    /// action closes the window, and menu_action opens the shell's window menu (`call mobile toggleControls`).
    pub fn hyprbars_mobile_keys(bars: &Value, data: &Value) -> Result<toml::map::Map<String, Value>, String> {
        let mut out = toml::map::Map::new();
        let Some(m) = bars.get("mobile") else {
            return Ok(out);
        };
        let m = m.as_table().ok_or("[desktop.hyprland.hyprbars.mobile] must be a table")?;
        let touch = m
            .get("touch_target")
            .and_then(Value::as_integer)
            .filter(|v| (44..=256).contains(v))
            .ok_or("hyprbars.mobile.touch_target must be 44..=256 logical px (a finger-sized target)")?;
        let buttons = bars.get("buttons").and_then(Value::as_array).ok_or("hyprbars.buttons must be an array")?;
        let close = m
            .get("close_button")
            .and_then(Value::as_integer)
            .filter(|i| (0..buttons.len() as i64).contains(i))
            .ok_or("hyprbars.mobile.close_button must index a [[desktop.hyprland.hyprbars.buttons]] entry")?;
        let closes = buttons[close as usize].get("action").and_then(Value::as_str).unwrap_or("");
        if !(closes.contains("window.close") || closes.contains("killactive")) {
            return Err("hyprbars.mobile.close_button must name the button whose action closes the window".into());
        }
        let colour = |k: &str| -> Result<Value, String> {
            let name = m.get(k).and_then(Value::as_str).ok_or(format!("hyprbars.mobile.{k} must name a [colors] key"))?;
            let hex = at(data, &format!("colors.{name}"))?
                .as_str()
                .and_then(|c| c.strip_prefix('#'))
                .filter(|h| h.len() == 6 && h.bytes().all(|c| c.is_ascii_hexdigit()))
                .ok_or(format!("hyprbars.mobile.{k}: [colors] {name} is not #RRGGBB"))?;
            Ok(Value::String(format!("rgba({hex}FF)")))
        };
        let icon = m.get("menu_icon").and_then(Value::as_str).ok_or("hyprbars.mobile.menu_icon must be a string")?;
        let action = m
            .get("menu_action")
            .and_then(Value::as_str)
            .filter(|a| a.trim_end().ends_with("call mobile toggleControls"))
            .ok_or("hyprbars.mobile.menu_action must open the shell's window menu (... call mobile toggleControls)")?;
        out.insert("mobile_touch_target".into(), Value::Integer(touch));
        out.insert("mobile_close_button".into(), Value::Integer(close));
        out.insert("mobile_menu_color".into(), colour("menu_palette")?);
        out.insert("mobile_menu_icon_color".into(), colour("menu_icon_palette")?);
        out.insert("mobile_menu_icon".into(), Value::String(icon.into()));
        out.insert("mobile_menu_action".into(), Value::String(action.into()));
        Ok(out)
    }

    fn glass_palette(value: &Value, data: &Value) -> Result<Value, String> {
        let mut result = value.clone();
        if let Some(table) = result.as_table_mut() {
            for (_, item) in table.iter_mut() {
                *item = glass_palette(item, data)?;
            }
            for (role, target) in [
                ("tint", "tint_color"),
                ("fresnel", "fresnel_color"),
                ("bevel", "bevel_color"),
            ] {
                let Some(key) = table.remove(&format!("palette_{role}")) else {
                    continue;
                };
                let key = key
                    .as_str()
                    .ok_or("Glass palette reference must name a color")?;
                let color = at(data, &format!("colors.{key}"))?
                    .as_str()
                    .ok_or("Glass color must be a string")?;
                let hex = color
                    .strip_prefix('#')
                    .filter(|h| h.len() == 6 && h.bytes().all(|c| c.is_ascii_hexdigit()))
                    .ok_or("Invalid glass palette color")?;
                let alpha = table
                    .remove(&format!("{role}_alpha"))
                    .and_then(|v| v.as_float())
                    .filter(|a| a.is_finite() && (0.0..=1.0).contains(a))
                    .ok_or("Glass alpha must be 0..1")?;
                let rgba = (i64::from_str_radix(hex, 16).map_err(|e| e.to_string())? << 8)
                    | (alpha * 255.0).round() as i64;
                table.insert(target.into(), Value::Integer(rgba));
            }
        }
        Ok(result)
    }

    /// Per-output monitor rules from [desktop.hyprland.monitors.<output>] (the table
    /// mios-displays saves into a mios.d drop-in). Each entry becomes
    /// `hl.monitor({output = "<output>", ...})` after the catch-all
    /// [desktop.hyprland.monitor] rule, so a named output overrides it. `primary = true`
    /// is not a monitor-rule field: it is returned separately and projects to
    /// cursor.default_monitor. An absent table keeps the single catch-all rule.
    pub fn monitor_rules(h: &Value) -> Result<(Vec<Value>, Option<String>), String> {
        let Some(table) = h.get("monitors") else {
            return Ok((Vec::new(), None));
        };
        let table = table
            .as_table()
            .ok_or("desktop.hyprland.monitors must be a table keyed by output name")?;
        let mut rules = Vec::new();
        let mut primary = None;
        for (output, spec) in table {
            if output.is_empty()
                || !output
                    .bytes()
                    .all(|c| c.is_ascii_alphanumeric() || c == b'-' || c == b'_')
            {
                return Err(format!(
                    "desktop.hyprland.monitors.{output}: output must be a connector name [A-Za-z0-9_-]"
                ));
            }
            let mut spec = spec
                .as_table()
                .ok_or(format!("desktop.hyprland.monitors.{output} must be a table"))?
                .clone();
            if let Some(v) = spec.get("mode") {
                let m = v
                    .as_str()
                    .ok_or(format!("desktop.hyprland.monitors.{output}.mode must be a string"))?;
                let named = matches!(m, "preferred" | "highres" | "highrr" | "maxwidth");
                let sized = m
                    .split_once('x')
                    .map(|(w, rest)| {
                        let (hgt, hz) = rest.split_once('@').unwrap_or((rest, "60"));
                        w.parse::<u32>().is_ok_and(|w| w > 0)
                            && hgt.parse::<u32>().is_ok_and(|h| h > 0)
                            && hz.parse::<f64>().is_ok_and(|r| r.is_finite() && r > 0.0)
                    })
                    .unwrap_or(false);
                if !named && !sized {
                    return Err(format!(
                        "desktop.hyprland.monitors.{output}.mode must be preferred|highres|highrr|maxwidth or WxH[@Hz]"
                    ));
                }
            }
            if let Some(v) = spec.get("scale") {
                let s = v.as_float().or_else(|| v.as_integer().map(|i| i as f64));
                if !s.is_some_and(|s| s.is_finite() && (0.25..=4.0).contains(&s)) {
                    return Err(format!(
                        "desktop.hyprland.monitors.{output}.scale must be a number in 0.25..4"
                    ));
                }
            }
            if let Some(v) = spec.get("transform") {
                if !v.as_integer().is_some_and(|t| (0..=7).contains(&t)) {
                    return Err(format!(
                        "desktop.hyprland.monitors.{output}.transform must be 0..7"
                    ));
                }
            }
            for key in ["disabled", "primary"] {
                if spec.get(key).is_some_and(|v| v.as_bool().is_none()) {
                    return Err(format!(
                        "desktop.hyprland.monitors.{output}.{key} must be boolean"
                    ));
                }
            }
            if spec.remove("primary").and_then(|v| v.as_bool()) == Some(true) {
                if let Some(other) = primary.replace(output.clone()) {
                    return Err(format!(
                        "desktop.hyprland.monitors: {other} and {output} are both primary"
                    ));
                }
            }
            spec.remove("output");
            let mut rule = toml::map::Map::new();
            rule.insert("output".into(), Value::String(output.clone()));
            rule.extend(spec);
            rules.push(Value::Table(rule));
        }
        Ok((rules, primary))
    }

    /// Chunk preamble. `mios_section` runs one optional section (a compositor plugin) under pcall:
    /// a plugin that fails to load or rejects its config is logged to stderr and recorded in
    /// MIOS_SESSION.errors for session.lua to surface, and the rest of the session still loads.
    const HEADER: &str = "-- AI-hint: Generated by mios-gen render-hyprland from layered mios.toml; edit the SSOT.\n-- AI-related: usr/share/mios/mios.toml, tools/native/mios-gen/src/render.rs\nlocal mios_state = rawget(_G, \"MIOS_SESSION\") or {}\nlocal function mios_section(name, fn)\n local ok, err = pcall(fn)\n if not ok then\n  local m = \"MiOS: Hyprland \" .. name .. \" section failed: \" .. tostring(err)\n  if io and io.stderr then io.stderr:write(m .. \"\\n\") end\n  mios_state.errors = mios_state.errors or {}\n  table.insert(mios_state.errors, m)\n end\nend\n";

    /// The session entry (usr/share/mios/hyprland/session.lua, also the skel user entry). It must
    /// never leave an empty session: when the projection fails (mios-gen error, bad SSOT layer,
    /// non-Lua output) or the chunk errors part way, it falls back to a minimal built-in desktop --
    /// terminal and launcher binds, mios-desktop.target start/stop -- for whatever the chunk did not
    /// already register, and surfaces the reason on stderr and as a compositor notification.
    pub const LOADER: &str = r#"-- AI-hint: Load the native MiOS projection with vendor, host and user SSOT layers; degrade to a minimal session when it fails.
-- AI-related: tools/native/mios-gen/src/render.rs (render_hyprland::LOADER), usr/share/mios/hyprland/hyprland.lua
MIOS_SESSION = {}
local function mios_quote(s)
 return "'" .. tostring(s):gsub("'", "'\\''") .. "'"
end
local function mios_load()
 local errfile = (os.getenv("XDG_RUNTIME_DIR") or "/tmp") .. "/mios-hyprland-projection.err"
 local p = io.popen("/usr/bin/mios-gen render-hyprland --root / --runtime 2>" .. mios_quote(errfile), "r")
 if not p then
  return false, "cannot run /usr/bin/mios-gen"
 end
 local config = p:read("*a") or ""
 -- Hyprland can reap the child before pclose; validate the returned Lua instead.
 p:close()
 if not config:match("^%-%- AI%-hint:") then
  local f = io.open(errfile, "r")
  local detail = f and f:read("*a") or ""
  if f then f:close() end
  detail = detail:gsub("%s+$", "")
  return false, "projection failed: " .. (detail ~= "" and detail or "no Lua output")
 end
 local chunk, err = load(config, "=MiOS SSOT")
 if not chunk then
  return false, "projection is not valid Lua: " .. tostring(err)
 end
 return pcall(chunk)
end
local ok, err = mios_load()
local state = MIOS_SESSION
local problems = state.errors or {}
if not ok then
 table.insert(problems, 1, "MiOS: Hyprland SSOT " .. tostring(err) .. " -- minimal session active")
 if io and io.stderr then io.stderr:write(problems[1] .. "\n") end
 if not state.binds then
  local terminal = "alacritty -e env -u TMUX -u TMUX_PANE -u TMUX_TMPDIR /usr/libexec/mios/mios-terminal"
  local launcher = "quickshell ipc --path /usr/share/mios/quickshell/Config.qml --any-display call drawer toggle"
  for _, key in ipairs({ "Q", "Return" }) do
   pcall(hl.bind, "SUPER + " .. key, hl.dsp.exec_cmd(terminal))
  end
  for _, key in ipairs({ "SPACE", "R" }) do
   pcall(hl.bind, "SUPER + " .. key, hl.dsp.exec_cmd(launcher))
  end
 end
 if not state.hooks then
  hl.on("hyprland.start", function()
   hl.exec_cmd("dbus-update-activation-environment --systemd WAYLAND_DISPLAY DISPLAY HYPRLAND_INSTANCE_SIGNATURE XDG_CURRENT_DESKTOP QT_QPA_PLATFORMTHEME MIOS_THEME_BRIDGE GTK_MODULES XDG_SESSION_DESKTOP XDG_SESSION_TYPE AQ_DRM_DEVICES WLR_DRM_DEVICES DRI_PRIME MIOS_DESKTOP_HARDWARE_PROFILE && systemctl --user start mios-desktop.target")
  end)
  hl.on("hyprland.shutdown", function()
   hl.exec_cmd("systemctl --user stop mios-desktop.target graphical-session.target")
  end)
 end
end
if #problems > 0 then
 local message = table.concat(problems, " | "):sub(1, 900)
 hl.on("hyprland.start", function()
  hl.exec_cmd("hyprctl notify 3 60000 0 " .. mios_quote(message))
 end)
end
"#;

    /// The projection with the default login inputs (no power profile known, mains, not gaming): deterministic, the
    /// tracked usr/share/mios/hyprland/hyprland.lua. `--runtime` (session.lua at login) passes the host's inputs.
    pub fn project(data: &Value) -> Result<String, String> {
        project_login(data, &RateInputs::default())
    }

    /// The projection with `login` as the frame-rate inputs ([desktop.framerate]) when miosd's state file is absent.
    pub fn project_login(data: &Value, login: &RateInputs) -> Result<String, String> {
        let rate_policy = framerate_policy(data)?;
        let h = at(data, "desktop.hyprland")?;
        let mut config = at(h, "config")?.clone();
        let acrylic = at(data, "theme.acrylic")?
            .as_bool()
            .ok_or("theme.acrylic must be boolean")?;
        let percent = at(data, "theme.windows.opacity_pct")?
            .as_integer()
            .filter(|n| (1..=100).contains(n))
            .ok_or("theme.windows.opacity_pct must be 1..100")?;
        let opacity = if acrylic { percent as f64 / 100.0 } else { 1.0 };
        let decoration = config
            .get_mut("decoration")
            .and_then(Value::as_table_mut)
            .ok_or("decoration must be a table")?;
        for key in ["active_opacity", "inactive_opacity", "fullscreen_opacity"] {
            decoration.insert(key.into(), Value::Float(opacity));
        }
        for (key, source) in [
            ("gaps_in", "wm_gaps_inner_px"),
            ("gaps_out", "wm_gaps_outer_px"),
            ("border_size", "wm_border_px"),
        ] {
            let v = at(data, &format!("theme.edge.{source}"))?;
            if v.as_integer().is_none_or(|n| n < 0) {
                return Err(format!("Invalid theme.edge.{source}"));
            }
            config
                .get_mut("general")
                .and_then(Value::as_table_mut)
                .ok_or("general must be a table")?
                .insert(key.into(), v.clone());
        }
        let color = |key: &str, alpha: &str| -> Result<Value, String> {
            let raw = at(data, &format!("colors.{key}"))?
                .as_str()
                .ok_or("Color must be a string")?;
            let hex = raw.strip_prefix('#').ok_or("Color must start with #")?;
            if hex.len() != 6 || !hex.bytes().all(|c| c.is_ascii_hexdigit()) {
                return Err("Invalid palette color".into());
            }
            Ok(Value::String(format!("rgba({hex}{alpha})")))
        };
        config
            .get_mut("misc")
            .and_then(Value::as_table_mut)
            .ok_or("Missing misc desktop settings")?
            .insert("background_color".into(), color("bg", "ff")?);
        let mut borders = toml::map::Map::new();
        borders.insert(
            "active_border".into(),
            Value::Array(vec![
                color("cursor", "ee")?,
                color("accent", "ee")?,
                color("success", "ee")?,
            ]),
        );
        // Lua gradients use an explicit colors table.
        let colors = borders
            .remove("active_border")
            .ok_or("Missing border colors")?;
        let mut active = toml::map::Map::new();
        active.insert("colors".into(), colors);
        borders.insert("active_border".into(), Value::Table(active));
        borders.insert("inactive_border".into(), color("bg", "aa")?);
        config
            .get_mut("general")
            .and_then(Value::as_table_mut)
            .ok_or("general must be a table")?
            .insert("col".into(), Value::Table(borders));
        let (monitor_rules, primary) = monitor_rules(h)?;
        if let Some(primary) = primary {
            config
                .as_table_mut()
                .ok_or("desktop.hyprland.config must be a table")?
                .entry("cursor")
                .or_insert_with(|| Value::Table(toml::map::Map::new()))
                .as_table_mut()
                .ok_or("desktop.hyprland.config.cursor must be a table")?
                .insert("default_monitor".into(), Value::String(primary));
        }
        let mut out = String::from(HEADER);
        // Session lifecycle first: the hyprland.start hook is what starts mios-desktop.target (shell,
        // portals, agents), so nothing later in the chunk -- a rejected config key, a plugin that
        // fails to load -- can take it down with it. session.lua reads MIOS_SESSION.hooks/.binds to
        // decide what its minimal fallback still has to provide.
        for (event, field) in [
            ("hyprland.start", "startup"),
            ("hyprland.shutdown", "shutdown"),
        ] {
            out += &format!("hl.on({}, function()\n", lua(&Value::String(event.into()))?);
            for cmd in at(h, field)?
                .as_array()
                .ok_or_else(|| format!("{field} must be an array"))?
            {
                if cmd.as_str().is_none_or(|s| s.trim().is_empty()) {
                    return Err(format!("{field} commands must be nonempty strings"));
                }
                out += &format!(" hl.exec_cmd({})\n", lua(cmd)?);
            }
            out += "end)\n";
        }
        out += "mios_state.hooks = true\n";
        out += &format!("hl.config({})\nhl.monitor({})\n", lua(&config)?, lua(at(h, "monitor")?)?);
        for rule in &monitor_rules {
            out += &format!("hl.monitor({})\n", lua(rule)?);
        }
        // [desktop.framerate] (miosd framerate): the tier's rates for the sections below (hyprglass) and the
        // compositor cap; after the monitor rules, so a mode miosd switched an output to survives a reload.
        if let Some(policy) = &rate_policy {
            out += &framerate_lua(data, policy, login)?;
        }
        // Float-first placement ([desktop.hyprland.placement], miosd placement): emitted before
        // the SSOT window_rules so the PiP/dialog rules after it override its size and position.
        // `float = false` keeps windows Hyprland already floats (parented dialogs, fixed-size
        // windows) out of its static size, which is how `miosd placement serve` tells them apart.
        // The size string mirrors `miosd placement rule-size` (placement::rule_size_expression).
        if let Some(p) = h
            .get("placement")
            .filter(|p| p.get("enabled").and_then(Value::as_bool) == Some(true))
        {
            let n = |k: &str| -> Result<String, String> {
                let v = p
                    .get(k)
                    .and_then(|v| v.as_float().or_else(|| v.as_integer().map(|i| i as f64)))
                    .filter(|v| v.is_finite() && *v > 0.0)
                    .ok_or(format!("desktop.hyprland.placement.{k} must be a positive number"))?;
                let s = format!("{v:.6}");
                Ok(s.trim_end_matches('0').trim_end_matches('.').to_owned())
            };
            let (r, a) = (n("width_ratio")?, n("aspect")?);
            let (minw, minh, maxw) = (n("min_width_px")?, n("min_height_px")?, n("max_width_px")?);
            let (mwr, mhr) = (n("max_width_ratio")?, n("max_height_ratio")?);
            let cap_w = format!("min(monitor_w*{mwr},{maxw})");
            let cap_h = format!("monitor_h*{mhr}");
            let land_w = format!("min(max(monitor_w*{r},{minw}),{cap_w})");
            let land_h = format!("min(max(({land_w})/{a},{minh}),{cap_h})");
            let port_h = format!("min(max(monitor_h*{r},{minh}),{cap_h})");
            let port_w = format!("min(max(({port_h})/{a},{minw}),{cap_w})");
            let tag = at(p, "open_tag")?
                .as_str()
                .filter(|t| {
                    !t.is_empty()
                        && t.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'-' || c == b'_')
                })
                .ok_or("desktop.hyprland.placement.open_tag must be [A-Za-z0-9_-]")?;
            if let Some(opening) = mios_resolver::window_opening::Policy::from_ssot(data)? {
                out += &format!("hl.window_rule({{[\"name\"] = \"mios-placement\", [\"match\"] = {{[\"float\"] = false}}, [\"float\"] = true, [\"center\"] = true, [\"size\"] = {}, [\"tag\"] = \"+{tag}\"}})\n", lua(&Value::String(opening.rule_expression()))?);
            } else {
            out += &format!(
                "hl.window_rule({{[\"name\"] = \"mios-placement\", [\"match\"] = {{[\"float\"] = false}}, [\"float\"] = true, [\"center\"] = true, [\"size\"] = \"(monitor_w>=monitor_h?min({land_w},monitor_w):min({port_w},monitor_w)) (monitor_w>=monitor_h?min({land_h},monitor_h):min({port_h},monitor_h))\", [\"tag\"] = \"+{tag}\"}})\n"
            );
            }
        }
        for rule in at(h, "window_rules")?.as_array().ok_or("window_rules must be an array")? {
            if rule.as_table().is_none() {
                return Err("window_rules entries must be tables".into());
            }
            out += &format!("hl.window_rule({})\n", lua(rule)?);
        }
        for (key, value) in at(h, "environment")?
            .as_table()
            .ok_or("environment must be a table")?
        {
            if matches!(key.as_str(), "AQ_DRM_DEVICES" | "WLR_DRM_DEVICES") {
                continue;
            }
            out += &format!(
                "hl.env({}, {})\n",
                lua(&Value::String(key.clone()))?,
                lua(value)?
            );
        }
        out += &crate::global_bindings::prelude(data)?;
        let actions = at(data, "keybindings.actions")?
            .as_array()
            .ok_or("keybindings.actions must be an array")?;
        let terminal = actions
            .iter()
            .find(|a| a.get("id").and_then(Value::as_str) == Some("terminal"))
            .ok_or("Missing terminal action")?;
        for key in ["Q", "Return"] {
            let fallback=format!("{} + {key}",at(h,"main_modifier")?.as_str().ok_or("modifier missing")?);
            let chord=if key=="Return" {crate::global_bindings::key(data,"terminal",&fallback)} else {fallback};
            out+=&format!("hl.bind({}, hl.dsp.exec_cmd({}))\n",lua(&Value::String(chord))?,string(terminal,"desktop_command")?);
        }
        for key in at(h,"launcher_keys")?.as_array().ok_or("launcher_keys must be an array")? {
            let key=key.as_str().ok_or("launcher key missing")?;
            let fallback=format!("{} + {key}",at(h,"main_modifier")?.as_str().ok_or("modifier missing")?);
            let chord=if key.eq_ignore_ascii_case("space") {crate::global_bindings::key(data,"app-drawer",&fallback)} else {fallback};
            out+=&format!("hl.bind({}, hl.dsp.exec_cmd({}))\n",lua(&Value::String(chord))?,string(h,"launcher")?);
        }
        for action in actions {
            let modifier = at(data, "keybindings.desktop_modifier")?
                .as_str()
                .ok_or("Invalid desktop modifier")?
                .split_whitespace()
                .collect::<Vec<_>>()
                .join(" + ");
            let key = at(action, "key")?.as_str().ok_or("Invalid action key")?;
            out += &format!(
                "hl.bind({}, hl.dsp.exec_cmd({}))\n",
                lua(&Value::String(format!("{modifier} + {key}")))?,
                string(action, "desktop_command")?
            );
        }
        for bind in at(h, "bindings")?
            .as_array()
            .ok_or("bindings must be an array")?
        {
            let dispatcher = at(bind, "dispatcher")?
                .as_str()
                .ok_or("Invalid dispatcher")?;
            if dispatcher.split('.').any(|part| {
                part.is_empty() || !part.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'_')
            }) {
                return Err("Invalid dispatcher identifier".into());
            }
            let args = bind.get("args").map(lua).transpose()?.unwrap_or_default();
            let flags = bind
                .get("flags")
                .map(lua)
                .transpose()?
                .map(|s| format!(", {s}"))
                .unwrap_or_default();
            out += &format!(
                "hl.bind({}, hl.dsp.{dispatcher}({args}){flags})\n",
                lua(&Value::String(crate::global_bindings::key(data, match (dispatcher, bind.get("args").and_then(Value::as_str)) {
                    ("window.close", _) => "close-window",
                    ("window.fullscreen", _) if bind.get("args").and_then(|a|a.get("mode")).and_then(Value::as_str)==Some("fullscreen") => "fullscreen",
                    ("exec_cmd", Some("/usr/bin/miosd placement toggle")) => "toggle-placement",
                    ("exec_cmd", Some(cmd)) if cmd.ends_with("call keymap toggle") => "keymap",
                    _ => "",
                }, at(bind,"key")?.as_str().ok_or("invalid bind key")?)))?
            );
        }
        // [desktop.screenshot] (mios-shot): keybinds, no-animation layer rules, screencopy permissions.
        out += &screenshot_lua(data)?;
        // [desktop.shaderglass] (miosd shaderglass): the overlay pane's window rules and keybinds.
        out += &shaderglass_lua(data)?;
        let count = at(h, "workspace_count")?
            .as_integer()
            .filter(|n| (1..=10).contains(n))
            .ok_or("workspace_count must be 1..10")?;
        out += &format!("for i = 1, {count} do\n local key = i % 10\n hl.bind({} .. \" + \" .. key, hl.dsp.focus({{ workspace = i }}))\n hl.bind({} .. \" + SHIFT + \" .. key, hl.dsp.window.move({{ workspace = i }}))\nend\n",string(h,"main_modifier")?,string(h,"main_modifier")?);
        out += "mios_state.binds = true\n";
        out += &hypr3d_profile(data)?;
        if let Some(glass) = h
            .get("hyprglass")
            .filter(|v| v.get("enabled").and_then(Value::as_bool) == Some(true))
        {
            let path = string(glass, "path")?;
            out += &format!("mios_section(\"hyprglass\", function()\nhl.plugin.load({path})\nif hl.plugin.hyprglass then\n local hg = hl.plugin.hyprglass\n");
            if let Some(presets) = glass.get("presets").and_then(Value::as_table) {
                for (name, preset) in presets {
                    out += &format!(
                        " hg.preset({}, {})\n",
                        lua(&Value::String(name.clone()))?,
                        lua(&glass_palette(preset, data)?)?
                    );
                }
            }
            // Keys only the MiOS hyprglass patch knows (hyprglass-mios-glass.patch: the edge pass over every
            // window) go through their own pcall after everything else, so a plugin without the patch keeps
            // the rest of its config and the failure is reported, not fatal.
            let mut config = glass_palette(at(glass, "config")?, data)?;
            let mut mios_keys = toml::map::Map::new();
            if let Some(t) = config.as_table_mut() {
                for k in HYPRGLASS_MIOS_KEYS {
                    if let Some(v) = t.remove(*k) {
                        mios_keys.insert((*k).into(), v);
                    }
                }
            }
            if let Some(gain) = mios_keys.get("edge_pass") {
                let gain = gain.as_float().or_else(|| gain.as_integer().map(|i| i as f64));
                if !gain.is_some_and(|g| (0.0..=4.0).contains(&g)) {
                    return Err("[desktop.hyprland.hyprglass.config] edge_pass must be a gain in 0..=4 (0 = off)".into());
                }
            }
            // live_resample_fps (windows and layers): the only content-driven throttle on the glass behind a pane;
            // 0 = one re-sample per output frame at each output's own refresh, else a cap in fps. With
            // [desktop.framerate] enabled the policy's glass rate G is the one source (mios_rate below): a fixed SSOT
            // value would be silently overridden, so it is an error.
            for group in ["windows", "layers"] {
                if let Some(fps) = config.get(group).and_then(|g| g.get("live_resample_fps")) {
                    if rate_policy.is_some() {
                        return Err(format!(
                            "[desktop.hyprland.hyprglass.config.{group}] live_resample_fps is superseded by [desktop.framerate] (glass rate = desktop rate / glass_divisor); remove it"
                        ));
                    }
                    if !fps.as_integer().is_some_and(|f| (0..=1000).contains(&f)) {
                        return Err(format!(
                            "[desktop.hyprland.hyprglass.config.{group}] live_resample_fps must be 0 (every output frame) or a cap in 1..=1000 fps"
                        ));
                    }
                }
            }
            if let Some(width) = mios_keys.get("edge_pass_width") {
                let width = width.as_float().or_else(|| width.as_integer().map(|i| i as f64));
                if !width.is_some_and(|w| (0.0..=256.0).contains(&w)) {
                    return Err("[desktop.hyprland.hyprglass.config] edge_pass_width must be 0..=256 logical px (0 = edge_thickness x the window's short side)".into());
                }
            }
            out += &format!(" hg.config({})\n", lua(&config)?);
            if rate_policy.is_some() {
                // The glass rate G of the running tier (framerate_lua's mios_rate; miosd re-applies it live).
                out += " hg.config({[\"layers\"] = {[\"live_resample_fps\"] = mios_rate.glass_fps}, [\"windows\"] = {[\"live_resample_fps\"] = mios_rate.glass_fps}})\n";
            }
            for namespace in at(glass, "layers")?
                .as_array()
                .ok_or("hyprglass.layers must be an array")?
            {
                out += &format!(" hg.layer({})\n", lua(namespace)?);
            }
            if !mios_keys.is_empty() {
                out += &format!(
                    " local mios_ok, mios_err = pcall(hg.config, {})\n mios_state.hyprglass_mios = mios_ok\n if not mios_ok then error(\"MiOS hyprglass keys (edge pass): \" .. tostring(mios_err)) end\n",
                    lua(&Value::Table(mios_keys))?
                );
            }
            out += "end\nend)\n";
        }
        if let Some(bars) = h
            .get("hyprbars")
            .filter(|v| v.get("enabled").and_then(Value::as_bool) == Some(true))
        {
            let path = string(bars, "path")?;
            let mut config = at(bars, "config")?.clone();
            if let Some(cfg) = config.as_table_mut() {
                // glass_preset = "<name>": the controls tab is the window's own glass. Its frost, tone and
                // lights come from [desktop.hyprland.hyprglass.presets.<name>] (and that preset's
                // <default_theme> table), the same values hyprglass draws the window body with; keys the
                // preset leaves out fall back to hyprglass' own global / theme defaults.
                if let Some(name) = cfg.remove("glass_preset") {
                    let name = name.as_str().ok_or("hyprbars glass_preset must name a hyprglass preset")?;
                    let glass = h.get("hyprglass").ok_or("hyprbars glass_preset needs [desktop.hyprland.hyprglass]")?;
                    // The tab and its buttons are drawn BY hyprglass (MiOS patches: mios_hyprglass_draw_shape_v1)
                    // with the window's preset; hyprbars finds the loaded plugin by this path.
                    if glass.get("enabled").and_then(Value::as_bool) == Some(true) {
                        cfg.insert("glass_plugin".into(), Value::String(string_value(glass, "path")?));
                    }
                    let preset = glass
                        .get("presets")
                        .and_then(|p| p.get(name))
                        .ok_or_else(|| format!("hyprbars glass_preset: hyprglass preset {name} not found"))?;
                    let preset = glass_palette(preset, data)?;
                    let theme = glass.get("config").and_then(|c| c.get("default_theme")).and_then(Value::as_str).unwrap_or("dark");
                    let dark = theme != "light";
                    let themed = preset.get(theme);
                    let num = |t: Option<&Value>, k: &str| {
                        t.and_then(|t| t.get(k)).and_then(|v| v.as_float().or_else(|| v.as_integer().map(|i| i as f64)))
                    };
                    let shared = |k: &str, d: f64| num(Some(&preset), k).unwrap_or(d);
                    let toned = |k: &str, d: f64| num(themed, k).or_else(|| num(Some(&preset), k)).unwrap_or(d);
                    let colour = |k: &str, d: i64| {
                        themed.and_then(|t| t.get(k)).or_else(|| preset.get(k)).and_then(Value::as_integer).unwrap_or(d)
                    };
                    let r3 = |v: f64| Value::Float((v * 1000.0).round() / 1000.0);
                    let pct = |v: f64| Value::Integer((v * 100.0).round().clamp(0.0, 100.0) as i64);
                    let rgba = |c: i64| Value::String(format!("rgba({:08X})", c & 0xFFFF_FFFF));
                    // hyprglass' kernel: radius blur_strength x 12 px, sigma radius / 3, blur_iterations
                    // H+V passes -> one gaussian of sigma 4 x blur_strength x sqrt(blur_iterations)
                    let (strength, iterations) = (shared("blur_strength", 2.0), shared("blur_iterations", 3.0).clamp(1.0, 5.0));
                    cfg.insert("pill_blur_sigma".into(), r3(4.0 * strength * iterations.sqrt()));
                    cfg.insert("pill_sheen".into(), pct(shared("specular_strength", 0.8)));
                    cfg.insert("pill_edge".into(), pct(shared("bevel_strength", 0.0)));
                    cfg.insert("pill_fresnel".into(), pct(shared("fresnel_strength", 0.6)));
                    cfg.insert("pill_dim".into(), pct(toned("adaptive_dim", if dark { 0.4 } else { 0.0 })));
                    cfg.insert("pill_brightness".into(), r3(toned("brightness", if dark { 0.82 } else { 1.12 })));
                    cfg.insert("pill_contrast".into(), r3(toned("contrast", if dark { 0.90 } else { 0.92 })));
                    cfg.insert("pill_saturation".into(), r3(toned("saturation", if dark { 0.80 } else { 0.85 })));
                    cfg.insert("pill_vibrancy".into(), r3(toned("vibrancy", if dark { 0.15 } else { 0.12 })));
                    cfg.insert("pill_bevel_size".into(), r3(shared("bevel_size", 6.0)));
                    cfg.insert("pill_edge_thickness".into(), r3(shared("edge_thickness", 0.06)));
                    cfg.insert("pill_glass_tint".into(), rgba(colour("tint_color", 0x8899_aa22)));
                    cfg.insert("pill_rim_color".into(), rgba(colour("fresnel_color", 0xffff_ff00)));
                    cfg.insert("pill_bevel_color".into(), rgba(colour("bevel_color", 0xffff_ff00)));
                }
                // pill_alpha may name an SSOT percentage (e.g. "theme.opacity", the window colour's opacity).
                if let Some(path) = cfg.get("pill_alpha").and_then(Value::as_str).map(str::to_owned) {
                    let v = at(data, &path)?;
                    let pctv = v
                        .as_integer()
                        .map(|i| i as f64)
                        .or_else(|| v.as_float())
                        .filter(|v| (0.0..=100.0).contains(v))
                        .ok_or("hyprbars pill_alpha reference must name a 0..100 percentage")?;
                    cfg.insert("pill_alpha".into(), Value::Float(pctv / 100.0));
                }
                // Palette references -> plugin colors: <role>_palette + <role>_alpha -> <role>_color.
                for role in ["pill", "pill_rim"] {
                    let Some(name) = cfg.remove(&format!("{role}_palette")) else { continue };
                    let name = name.as_str().ok_or("hyprbars palette reference must name a color")?;
                    let hex = at(data, &format!("colors.{name}"))?
                        .as_str()
                        .and_then(|c| c.strip_prefix('#'))
                        .filter(|h| h.len() == 6 && h.bytes().all(|c| c.is_ascii_hexdigit()))
                        .ok_or("Invalid hyprbars palette color")?
                        .to_owned();
                    let alpha = cfg
                        .remove(&format!("{role}_alpha"))
                        .and_then(|v| v.as_float())
                        .filter(|a| a.is_finite() && (0.0..=1.0).contains(a))
                        .ok_or("hyprbars alpha must be 0..1")?;
                    cfg.insert(format!("{role}_color"), Value::String(format!("rgba({hex}{:02X})", (alpha * 255.0).round() as u8)));
                }
            }
            // Keys only the MiOS hyprbars patch knows: their own pcall'd hl.config after the buttons.
            let mut mios_keys = toml::map::Map::new();
            if let Some(t) = config.as_table_mut() {
                for k in HYPRBARS_MIOS_KEYS {
                    if let Some(v) = t.remove(*k) {
                        mios_keys.insert((*k).into(), v);
                    }
                }
            }
            if let Some(g) = mios_keys.get("button_glass") {
                let g = g.as_float().or_else(|| g.as_integer().map(|i| i as f64));
                if !g.is_some_and(|g| (0.0..=1.0).contains(&g)) {
                    return Err("[desktop.hyprland.hyprbars.config] button_glass must be 0..=1 (0 = flat buttons)".into());
                }
            }
            mios_keys.extend(hyprbars_mobile_keys(bars, data)?);
            let mut table = toml::map::Map::new();
            table.insert("hyprbars".into(), config);
            let mut plugin = toml::map::Map::new();
            plugin.insert("plugin".into(), Value::Table(table));
            out += &format!(
                "mios_section(\"hyprbars\", function()\nhl.plugin.load({path})\nif hl.plugin.hyprbars then\n hl.config({})\n",
                lua(&Value::Table(plugin))?
            );
            let rgb = |key: &str| -> Result<Value, String> {
                let color = at(data, &format!("colors.{key}"))?.as_str().ok_or("Bar color must be a string")?;
                let hex = color
                    .strip_prefix('#')
                    .filter(|h| h.len() == 6 && h.bytes().all(|c| c.is_ascii_hexdigit()))
                    .ok_or("Invalid hyprbars palette color")?;
                Ok(Value::String(format!("rgb({hex})")))
            };
            for button in at(bars, "buttons")?.as_array().ok_or("hyprbars.buttons must be an array")? {
                let mut b = toml::map::Map::new();
                let name = |k: &str| at(button, k)?.as_str().ok_or_else(|| format!("hyprbars button {k} must be a palette name"));
                b.insert("bg_color".into(), rgb(name("palette")?)?);
                b.insert("fg_color".into(), rgb(name("icon_palette")?)?);
                b.insert("size".into(), at(button, "size")?.clone());
                b.insert("icon".into(), at(button, "icon")?.clone());
                b.insert("action".into(), at(button, "action")?.clone());
                out += &format!(" hl.plugin.hyprbars.add_button({})\n", lua(&Value::Table(b))?);
            }
            if !mios_keys.is_empty() {
                let mut table = toml::map::Map::new();
                table.insert("hyprbars".into(), Value::Table(mios_keys));
                let mut plugin = toml::map::Map::new();
                plugin.insert("plugin".into(), Value::Table(table));
                // mios_state.hyprbars_mobile: the plugin took the mobile-tab keys, so the mobile profile may
                // put its windows on hyprbars:mobile tabs (else the shell draws them).
                out += &format!(
                    " local mios_ok, mios_err = pcall(hl.config, {})\n mios_state.hyprbars_mobile = mios_ok\n if not mios_ok then error(\"MiOS hyprbars keys (glass tab, mobile tabs): \" .. tostring(mios_err)) end\n",
                    lua(&Value::Table(plugin))?
                );
            }
            out += "end\nend)\n";
        }
        // Named bezier curves ([desktop.hyprland].curves) the animation leaves below may reference.
        for curve in h.get("curves").and_then(Value::as_array).into_iter().flatten() {
            let name = curve
                .get("name")
                .and_then(Value::as_str)
                .filter(|s| !s.is_empty() && s.bytes().all(|c| c.is_ascii_alphanumeric()))
                .ok_or("desktop.hyprland.curves name must be alphanumeric")?;
            let pts = curve
                .get("points")
                .and_then(Value::as_array)
                .filter(|p| {
                    p.len() == 2
                        && p.iter().all(|xy| {
                            xy.as_array().is_some_and(|v| v.len() == 2 && v.iter().all(|n| n.as_float().or(n.as_integer().map(|i| i as f64)).is_some_and(f64::is_finite)))
                        })
                })
                .ok_or(format!("desktop.hyprland.curves {name} needs points = [[x1, y1], [x2, y2]]"))?;
            let mut c = toml::map::Map::new();
            c.insert("type".into(), Value::String("bezier".into()));
            c.insert("points".into(), Value::Array(pts.clone()));
            out += &format!("hl.curve({}, {})\n", lua(&Value::String(name.into()))?, lua(&Value::Table(c))?);
        }
        // Animation tree leaves ([desktop.hyprland].animations): one hl.animation per entry, so the
        // compositor's own fades and window motion are timed to the SSOT window effects.
        for anim in h.get("animations").and_then(Value::as_array).into_iter().flatten() {
            let leaf = anim
                .get("leaf")
                .and_then(Value::as_str)
                .filter(|s| !s.is_empty() && s.bytes().all(|c| c.is_ascii_alphanumeric()))
                .ok_or("desktop.hyprland.animations leaf must name an animation tree leaf")?;
            let mut a = toml::map::Map::new();
            a.insert("leaf".into(), Value::String(leaf.into()));
            a.insert("enabled".into(), anim.get("enabled").cloned().unwrap_or(Value::Boolean(true)));
            for key in ["speed", "bezier", "spring", "style"] {
                if let Some(v) = anim.get(key) {
                    a.insert(key.into(), v.clone());
                }
            }
            if !a.contains_key("speed") {
                return Err(format!("desktop.hyprland.animations {leaf} needs a speed"));
            }
            out += &format!("hl.animation({})\n", lua(&Value::Table(a))?);
        }
        // Window open/close shader animations ([desktop.hyprland.windowshade], HyprWindowShade).
        // `// @palette <key>` lines in the vendor shaders are rewritten from [colors] into
        // $XDG_RUNTIME_DIR at login; a global `_default` tag pair gives every window the SSOT
        // effect and each override's specific tag outranks it. Layer shells are never animated.
        if let Some(fx) = h
            .get("windowshade")
            .filter(|v| v.get("enabled").and_then(Value::as_bool) == Some(true))
        {
            let name_ok = |s: &&str| !s.is_empty() && s.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'-' || c == b'_');
            let effect = |v: &Value, k: &str| -> Result<String, String> {
                v.get(k)
                    .and_then(Value::as_str)
                    .filter(name_ok)
                    .map(str::to_owned)
                    .ok_or(format!("desktop.hyprland.windowshade {k} must name a shader in shader_dir"))
            };
            let (open, close) = (effect(fx, "open")?, effect(fx, "close")?);
            let overrides = fx.get("overrides").and_then(Value::as_array).cloned().unwrap_or_default();
            let mut names = vec![open.clone(), close.clone()];
            for o in &overrides {
                for k in ["open", "close"] {
                    if o.get(k).is_some() {
                        names.push(effect(o, k)?);
                    }
                }
            }
            names.sort();
            names.dedup();
            let mut palette = toml::map::Map::new();
            for (k, v) in at(data, "colors")?.as_table().ok_or("colors must be a table")? {
                if let Some(hex) = v
                    .as_str()
                    .and_then(|c| c.strip_prefix('#'))
                    .filter(|h| h.len() == 6 && h.bytes().all(|c| c.is_ascii_hexdigit()))
                {
                    palette.insert(k.clone(), Value::String(hex.to_owned()));
                }
            }
            out += &format!(
                "mios_section(\"windowshade\", function()\nhl.plugin.load({})\nlocal mios_fx = (os.getenv(\"XDG_RUNTIME_DIR\") or \"/tmp\") .. \"/mios-shader-\"\ndo\n local palette = {}\n local src = {} .. \"/\"\n for _, name in ipairs({}) do\n  local i = io.open(src .. name .. \".glsl\", \"r\")\n  if i then\n   local s = i:read(\"a\")\n   i:close()\n   s = s:gsub(\"vec3%b()(;%s*// @palette ([%w_]+))\", function(rest, key)\n    local h = palette[key]\n    if not h then return nil end\n    return string.format(\"vec3(%.4f, %.4f, %.4f)%s\", tonumber(h:sub(1, 2), 16) / 255, tonumber(h:sub(3, 4), 16) / 255, tonumber(h:sub(5, 6), 16) / 255, rest)\n   end)\n   local o = io.open(mios_fx .. name .. \".glsl\", \"w\")\n   if o then o:write(s) o:close() end\n  end\n end\nend\n",
                string(fx, "path")?,
                lua(&Value::Table(palette))?,
                string(fx, "shader_dir")?,
                lua(&Value::Array(names.into_iter().map(Value::String).collect()))?
            );
            for (kind, eff) in [("open", &open), ("close", &close)] {
                out += &format!("hl.window_rule({{[\"name\"] = \"mios-fx-{kind}\", [\"match\"] = {{[\"class\"] = \".*\"}}, [\"tag\"] = \"+shader_{kind}_default:\" .. mios_fx .. \"{eff}.glsl\"}})\n");
            }
            for o in &overrides {
                let name = o
                    .get("name")
                    .and_then(Value::as_str)
                    .filter(name_ok)
                    .ok_or("windowshade override name must be [A-Za-z0-9_-]")?;
                let m = lua(o.get("match").filter(|m| m.is_table()).ok_or("windowshade overrides need a match table")?)?;
                for kind in ["open", "close"] {
                    if o.get(kind).is_some() {
                        let eff = effect(o, kind)?;
                        out += &format!("hl.window_rule({{[\"name\"] = \"{name}-{kind}\", [\"match\"] = {m}, [\"tag\"] = \"+shader_{kind}:\" .. mios_fx .. \"{eff}.glsl\"}})\n");
                    }
                }
            }
            out += "end)\n";
        }
        // Wobbly floating windows ([desktop.hyprland.mios-wobbly]): every window carries `tag`,
        // `exclude` matches carry `exclude_tag`; the plugin only deforms floating windows.
        if let Some(wob) = h
            .get("mios-wobbly")
            .filter(|v| v.get("enabled").and_then(Value::as_bool) == Some(true))
        {
            let tag_of = |k: &str| -> Result<String, String> {
                wob.get(k)
                    .and_then(Value::as_str)
                    .filter(|s| !s.is_empty() && s.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'-' || c == b'_'))
                    .map(str::to_owned)
                    .ok_or(format!("desktop.hyprland.mios-wobbly.{k} must be [A-Za-z0-9_-]"))
            };
            let (tag, skip) = (tag_of("tag")?, tag_of("exclude_tag")?);
            let mut cfg = wob.get("config").and_then(Value::as_table).cloned().unwrap_or_default();
            cfg.insert("tag".into(), Value::String(tag.clone()));
            cfg.insert("exclude_tag".into(), Value::String(skip.clone()));
            let mut plugin = toml::map::Map::new();
            plugin.insert("mios_wobbly".into(), Value::Table(cfg));
            let mut table = toml::map::Map::new();
            table.insert("plugin".into(), Value::Table(plugin));
            out += &format!(
                "mios_section(\"mios-wobbly\", function()\nhl.plugin.load({})\npcall(hl.config, {})\nhl.window_rule({{[\"name\"] = \"mios-wobbly\", [\"match\"] = {{[\"class\"] = \".*\"}}, [\"tag\"] = \"+{tag}\"}})\n",
                string(wob, "path")?,
                lua(&Value::Table(table))?
            );
            for (n, ex) in wob.get("exclude").and_then(Value::as_array).into_iter().flatten().enumerate() {
                let m = lua(ex.get("match").filter(|m| m.is_table()).ok_or("mios-wobbly exclude entries need a match table")?)?;
                out += &format!("hl.window_rule({{[\"name\"] = \"mios-wobbly-skip-{n}\", [\"match\"] = {m}, [\"tag\"] = \"+{skip}\"}})\n");
            }
            out += "end)\n";
        }
        // Magic lamp ([desktop.hyprland.lamp], inside the mios-wobbly plugin); additive, see lamp_profile below.
        out += &lamp_profile(data)?;
        // Mobile layout profile ([desktop.mobile]); additive, see mobile_profile below.
        out += &crate::global_bindings::finish(data)?;
        out += &mobile_profile(data)?;
        Ok(out)
    }

    // ---- Mobile layout profile ([desktop.mobile]) -- mobile lane, additive -----------------------
    // Self-contained: only `project` calls it (one line, last section). It touches no other section,
    // escaper or session code and reuses only `lua` for literals.

    /// Runtime chunk start/end markers; tests and tools cut the profile out of a full projection by them.
    pub const MOBILE_BEGIN: &str = "-- MiOS mobile layout profile ([desktop.mobile])";
    pub const MOBILE_END: &str = "-- end MiOS mobile layout profile";

    /// Lua body of the profile; `@@KEY@@` placeholders are filled by [`mobile_profile`].
    const MOBILE_LUA: &str = r#"-- Every output (local or remote per applies_to; remote = connector name starts with a
-- remote_name_prefixes entry, the RDP virtual display being a Hyprland headless output) is run
-- through the global scale policy ([desktop.scaling], mios_scale_policy below: density from the
-- client DPI hint / EDID mm / resolution x viewing distance x orientation). Its scale is applied to
-- outputs without an operator-saved scale when apply_to allows (deferred to a one-shot timer, never
-- inside the event), and its mobile flag (logical short side under the breakpoint) selects the
-- phone profile: a per-output workspace rule (one-window layout, no top gap so the window meets the
-- shell's shoulders) and a window rule that tiles new windows there (never a window class whose own rule
-- floats and pins it: the ShaderGlass pane; this rule is made later, so it would win). Mobile windows keep the GLOBAL
-- decoration (rounding, border, shadow, hyprglass) exactly like desktop windows. Their controls:
-- hyprbars_tab = "mobile" puts them on the window's own glass split into two touch tabs (hyprbars:mobile,
-- when the plugin took the mobile keys: mios_state.hyprbars_mobile; otherwise the shell draws them),
-- false hides the hyprbars tab (the shell draws the controls), true keeps the desktop tab. Without
-- [desktop.scaling] the legacy rule applies (portrait, or narrower than breakpoint_px). Rules are
-- made when such an output appears and enabled/disabled as outputs come, go, rotate or rescale
-- (monitor.added/removed/layout_changed); nothing is keyed to an output name except the operator's
-- saved scales (EXPLICIT). Windows on outputs of an origin in remote_effects.applies_to (the RDP
-- display) are tagged with the remote glass preset FX (a copy of the desktop preset with fewer blur
-- passes and no specular/fresnel/chromatic extras: cheaper to render and capture). The verdict is
-- published for Quickshell (Mobile.qml) in $XDG_RUNTIME_DIR/mios-mobile-outputs.json; remote DPI
-- hints are read from $XDG_RUNTIME_DIR/mios-display-hints.json ({"outputs": {"<name>": {"dpi": N}}}).
do
 local ok, err = pcall(function()
  local M = {enabled = @@ENABLED@@, portrait = @@PORTRAIT@@, breakpoint = @@BREAKPOINT@@, applies = @@APPLIES@@,
   prefixes = @@PREFIXES@@, layout = @@LAYOUT@@, gaps_in = @@GAPS_IN@@, gaps_out = @@GAPS_OUT@@,
   hyprbars_tab = @@HYPRBARS_TAB@@}
  local config_json = @@JSON@@
  local S = @@SCALING@@
  -- Global so `mios-gen session-scale` can mark an output it scaled explicit in the live session.
  MIOS_SCALE_EXPLICIT = @@EXPLICIT@@
  local EXPLICIT = MIOS_SCALE_EXPLICIT
  local FX = @@FX@@
@@POLICY_FN@@
  local rules, fxrules, applied, timers = {}, {}, {}, {}
  -- The remote glass preset exists only if hyprglass is loaded; without it nothing is tagged.
  if FX then
   local hg = hl.plugin and hl.plugin.hyprglass
   local okp, e = false, "hyprglass not loaded"
   if hg and type(hg.preset) == "function" then okp, e = pcall(hg.preset, FX.preset, FX.spec) end
   if not okp then
    if hg and io and io.stderr then io.stderr:write("MiOS: remote glass preset: " .. tostring(e) .. "\n") end
    FX = nil
   elseif type(hg.layer) == "function" then
    -- shell layers opt in by namespace (<ns>-remote, Mobile.layerNamespace): hyprglass layer presets
    -- are per namespace, not per output
    for _, ns in ipairs(FX.layers) do pcall(hg.layer, ns .. "-remote", {preset = FX.preset}) end
   end
  end
  local bars = hl.plugin ~= nil and hl.plugin.hyprbars ~= nil
  local MS = rawget(_G, "MIOS_SESSION") or {}
  -- the plugin's split touch tabs need the MiOS patch's mobile keys to have been accepted
  local mobiletabs = M.hyprbars_tab == "mobile" and bars and MS.hyprbars_mobile == true
  local nobar = M.hyprbars_tab ~= true and bars
  local tabs_live = false
  local function origin(name)
   for _, p in ipairs(M.prefixes) do
    if name:sub(1, #p) == p then return "remote" end
   end
   return "local"
  end
  local function logical(m)
   local w, h, s = tonumber(m.width) or 0, tonumber(m.height) or 0, tonumber(m.scale) or 1
   if s <= 0 then s = 1 end
   if (tonumber(m.transform) or 0) % 2 == 1 then w, h = h, w end
   return math.floor(w / s + 0.5), math.floor(h / s + 0.5)
  end
  local function is_mobile(name, w, h)
   if not M.enabled or w <= 0 or h <= 0 or not M.applies[origin(name)] then return false end
   return (M.portrait and h > w) or w < M.breakpoint
  end
  local function dpi_hints()
   local hints, dir = {}, os and os.getenv and os.getenv("XDG_RUNTIME_DIR")
   local f = dir and io and io.open and io.open(dir .. "/mios-display-hints.json")
   if not f then return hints end
   local j = f:read("*a") or ""
   f:close()
   local body = j:match('"outputs"%s*:%s*(%b{})') or ""
   for name, o in body:gmatch('"([%w_%-]+)"%s*:%s*(%b{})') do
    local d = tonumber(o:match('"dpi"%s*:%s*([%d%.]+)'))
    if d and d > 0 then hints[name] = d end
   end
   return hints
  end
  -- Policy verdict for one output; the legacy breakpoint rule when [desktop.scaling] is absent.
  local function decide(m, name, lw, lh, hints)
   local cur = tonumber(m.scale) or 1
   if not S then
    return {mobile = is_mobile(name, lw, lh), scale = cur, scale_120 = math.floor(cur * 120 + 0.5),
     logical_width = lw, logical_height = lh, explicit = false, auto_apply = false, source = "legacy", dpi = 0}
   end
   local d = mios_scale_policy(S, {width = m.width, height = m.height, pw = m.physical_width, ph = m.physical_height,
    dpi = hints[name], transform = m.transform, remote = origin(name) == "remote", explicit = EXPLICIT[name]})
   d.mobile = (M.enabled and M.applies[origin(name)] and d.mobile) and true or false
   return d
  end
  -- Apply the policy scale to a non-explicit output: once per (mode, transform, scale), so a scale
  -- Hyprland substitutes can never loop, and from a one-shot timer, never inside the event itself.
  local function schedule(m, name, d)
   local cur = tonumber(m.scale) or 1
   if not d.auto_apply or math.abs(cur * 120 - d.scale_120) < 0.5 then return end
   local key = string.format("%dx%d:%d:%d", tonumber(m.width) or 0, tonumber(m.height) or 0, tonumber(m.transform) or 0, d.scale_120)
   if applied[name] == key or type(hl.timer) ~= "function" or type(hl.monitor) ~= "function" then return end
   applied[name] = key
   local spec = {output = name, scale = d.scale, transform = tonumber(m.transform) or 0,
    mode = string.format("%dx%d@%.3f", tonumber(m.width) or 0, tonumber(m.height) or 0, tonumber(m.refresh_rate) or 60),
    position = string.format("%dx%d", tonumber(m.x) or 0, tonumber(m.y) or 0)}
   timers[name] = hl.timer(function()
    timers[name] = nil
    local oka, e = pcall(hl.monitor, spec)
    if not oka and io and io.stderr then io.stderr:write("MiOS: scale policy: " .. tostring(e) .. "\n") end
   end, {timeout = 1, type = "oneshot"})
  end
  local function apply(name, on)
   local r = rules[name]
   if on and not r then
    local sel = "m[" .. name .. "]"
    -- layout and gaps only: mobile windows inherit the global decoration (rounding, border, glass)
    rules[name] = {
     hl.workspace_rule({workspace = sel, layout = M.layout, gaps_in = M.gaps_in,
      gaps_out = {top = 0, right = M.gaps_out, bottom = M.gaps_out, left = M.gaps_out}}),
     hl.window_rule({name = "mios-mobile-" .. name, match = @@TILE_MATCH@@, tile = true}),
    }
    local tabbed = false
    if mobiletabs then
     local okt, rt = pcall(hl.window_rule, {name = "mios-mobile-tabs-" .. name, match = {workspace = sel}, ["hyprbars:mobile"] = true})
     if okt and rt then table.insert(rules[name], rt) tabbed = true end
    end
    if tabbed then
     tabs_live = true
    elseif nobar then
     local okb, rb = pcall(hl.window_rule, {name = "mios-mobile-nobar-" .. name, match = {workspace = sel}, ["hyprbars:no_bar"] = true})
     if okb and rb then table.insert(rules[name], rb) end
    end
   elseif r then
    for _, rule in ipairs(r) do pcall(rule.set_enabled, rule, on) end
   end
  end
  -- Remote glass preset on every window of a remote_effects output (dynamic tag: a window moved to
  -- a local output loses it).
  local function apply_fx(name, on)
   if not FX then return end
   local r = fxrules[name]
   if on and not r then
    local okr, rr = pcall(hl.window_rule, {name = "mios-remote-fx-" .. name, match = {workspace = "m[" .. name .. "]"},
     tag = "+hyprglass_preset_" .. FX.preset})
    if okr and rr then fxrules[name] = rr end
   elseif r then
    pcall(r.set_enabled, r, on)
   end
  end
  local function jstr(s)
   return '"' .. (tostring(s):gsub('[%c"\\]', function(c) return string.format("\\u%04x", c:byte()) end)) .. '"'
  end
  local function publish(state)
   local dir = os and os.getenv and os.getenv("XDG_RUNTIME_DIR")
   if not dir or not io or not io.open then return end
   local path = dir .. "/mios-mobile-outputs.json"
   local names, parts = {}, {}
   for name in pairs(state) do names[#names + 1] = name end
   table.sort(names)
   for _, name in ipairs(names) do
    local s = state[name]
    local d = s.d
    parts[#parts + 1] = string.format('%s: {"mobile": %s, "origin": %s, "logical_width": %d, "logical_height": %d, '
     .. '"scale": %.6g, "scale_120": %d, "applied_scale": %.6g, "policy_logical_width": %d, "policy_logical_height": %d, '
     .. '"explicit": %s, "auto_apply": %s, "source": %s, "dpi": %.1f, "glass_preset": %s}',
     jstr(name), tostring(s.mobile), jstr(s.origin), s.w, s.h,
     d.scale, d.scale_120, s.applied, d.logical_width, d.logical_height,
     tostring(d.explicit), tostring(d.auto_apply), jstr(d.source), tonumber(d.dpi) or 0, jstr(s.fx or ""))
   end
   local f = io.open(path .. ".new", "w")
   if not f then return end
   -- hyprbars_tabs: the plugin draws the mobile controls (hyprbars:mobile rule live); the shell then draws none
   f:write('{"version": 1, "hyprbars_tabs": ' .. tostring(tabs_live) .. ', "outputs": {' .. table.concat(parts, ", ") .. '}, "config": ' .. config_json .. '}\n')
   f:close()
   os.rename(path .. ".new", path)
  end
  local function reconcile(added)
   local state, list = {}, hl.get_monitors() or {}
   if type(list) ~= "table" then list = {} end
   if added ~= nil then list[#list + 1] = added end
   local hints = S and dpi_hints() or {}
   for i = 1, #list do
    local m = list[i]
    local okm, name, w, h = pcall(function() local lw, lh = logical(m) return m.name, lw, lh end)
    if okm and type(name) == "string" and name:match("^[%w_%-]+$") and not state[name] then
     local okd, d = pcall(decide, m, name, w, h, hints)
     if not okd then
      if io and io.stderr then io.stderr:write("MiOS: scale policy: " .. tostring(d) .. "\n") end
      d = {mobile = is_mobile(name, w, h), scale = tonumber(m.scale) or 1, scale_120 = 120, logical_width = w,
       logical_height = h, explicit = false, auto_apply = false, source = "error", dpi = 0}
     end
     local fx = FX and FX.applies[origin(name)] and FX.preset or nil
     state[name] = {mobile = d.mobile, origin = origin(name), w = w, h = h, applied = tonumber(m.scale) or 1, d = d, fx = fx}
     apply(name, d.mobile)
     apply_fx(name, fx ~= nil)
     pcall(schedule, m, name, d)
    end
   end
   for name in pairs(rules) do
    if not (state[name] and state[name].mobile) then apply(name, false) end
   end
   for name in pairs(fxrules) do
    if not (state[name] and state[name].fx) then apply_fx(name, false) end
   end
   publish(state)
  end
  local function guarded(fn)
   return function(...)
    local okc, e = pcall(fn, ...)
    if not okc and io and io.stderr then io.stderr:write("MiOS: mobile profile: " .. tostring(e) .. "\n") end
   end
  end
  hl.on("monitor.added", guarded(function(m) reconcile(m) end))
  hl.on("monitor.removed", guarded(function() reconcile() end))
  hl.on("monitor.layout_changed", guarded(function() reconcile() end))
  reconcile()
 end)
 if not ok and io and io.stderr then io.stderr:write("MiOS: Hyprland mobile section failed: " .. tostring(err) .. "\n") end
end
"#;

    /// Project [desktop.mobile] into the runtime per-output mobile profile (see [`MOBILE_LUA`]).
    /// An absent table projects nothing; every key is validated here, including the ones only the
    /// shell reads (they reach Quickshell inside the published JSON).
    pub fn mobile_profile(data: &Value) -> Result<String, String> {
        let Some(m) = data.get("desktop").and_then(|d| d.get("mobile")) else {
            return Ok(String::new());
        };
        let t = m.as_table().ok_or("desktop.mobile must be a table")?;
        let flag = |k: &str| -> Result<bool, String> {
            t.get(k).and_then(Value::as_bool).ok_or(format!("desktop.mobile.{k} must be a boolean"))
        };
        let int = |k: &str, lo: i64, hi: i64| -> Result<i64, String> {
            t.get(k)
                .and_then(Value::as_integer)
                .filter(|v| (lo..=hi).contains(v))
                .ok_or(format!("desktop.mobile.{k} must be an integer in {lo}..={hi}"))
        };
        let strings = |k: &str| -> Result<Vec<String>, String> {
            t.get(k)
                .and_then(Value::as_array)
                .ok_or(format!("desktop.mobile.{k} must be an array of strings"))?
                .iter()
                .map(|v| v.as_str().map(str::to_owned).ok_or(format!("desktop.mobile.{k} must be an array of strings")))
                .collect()
        };
        let ident = |s: &str| !s.is_empty() && s.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'-' || c == b'_');
        let (enabled, portrait) = (flag("enabled")?, flag("portrait")?);
        let breakpoint = int("breakpoint_px", 1, 16384)?;
        let (gaps_in, gaps_out) = (int("gaps_in_px", 0, 256)?, int("gaps_out_px", 0, 256)?);
        // Mobile windows follow the GLOBAL decoration (rounding, border, shadow, hyprglass): the old
        // per-profile overrides squared phone windows off. Absent or "inherit" only.
        for k in ["window_border", "window_rounding", "window_decorations"] {
            match t.get(k) {
                None => {}
                Some(Value::String(s)) if s == "inherit" => {}
                Some(_) => {
                    return Err(format!(
                        "desktop.mobile.{k}: mobile windows use the global [desktop.hyprland] decoration (rounding, border, glass); remove the key or set \"inherit\""
                    ))
                }
            }
        }
        // "mobile" = the window's own glass split into two touch tabs (hyprbars:mobile), false = no hyprbars tab
        // (the shell draws the controls), true = the desktop tab on phones too.
        let hyprbars_tab = match t.get("hyprbars_tab") {
            None => "false".to_string(),
            Some(Value::Boolean(b)) => b.to_string(),
            Some(Value::String(s)) if s == "mobile" => "\"mobile\"".to_string(),
            Some(_) => return Err("desktop.mobile.hyprbars_tab must be \"mobile\", true or false".into()),
        };
        let fx = remote_effects(data, t)?;
        int("notch_gap_px", 0, 256)?;
        int("shoulder_dot_px", 2, 128)?;
        flag("app_menu")?;
        let applies = strings("applies_to")?;
        if applies.is_empty() || applies.iter().any(|s| s != "local" && s != "remote") {
            return Err("desktop.mobile.applies_to must list \"local\" and/or \"remote\"".into());
        }
        let prefixes = strings("remote_name_prefixes")?;
        if prefixes.iter().any(|p| !ident(p)) {
            return Err("desktop.mobile.remote_name_prefixes entries must be [A-Za-z0-9_-]".into());
        }
        let layout = t
            .get("layout")
            .and_then(Value::as_str)
            .filter(|s| matches!(*s, "monocle" | "dwindle" | "master" | "scrolling"))
            .ok_or("desktop.mobile.layout must be monocle|dwindle|master|scrolling")?;
        for (i, c) in t
            .get("window_controls")
            .and_then(Value::as_array)
            .ok_or("desktop.mobile.window_controls must be an array of tables")?
            .iter()
            .enumerate()
        {
            for k in ["label", "icon", "action"] {
                c.get(k)
                    .and_then(Value::as_str)
                    .filter(|s| !s.trim().is_empty())
                    .ok_or(format!("desktop.mobile.window_controls[{i}].{k} must be a nonempty string"))?;
            }
        }
        let mut applies_t = toml::map::Map::new();
        for a in &applies {
            applies_t.insert(a.clone(), Value::Boolean(true));
        }
        let json = serde_json::to_string(m).map_err(|e| e.to_string())?;
        // Global scale policy ([desktop.scaling]); absent = the legacy breakpoint rule above.
        let (scaling, policy_fn) = if data.get("desktop").and_then(|d| d.get("scaling")).is_some() {
            let p = crate::display_scale::Policy::from_ssot(data)?;
            (p.lua_table(), crate::display_scale::LUA.to_string())
        } else {
            ("nil".to_string(), String::new())
        };
        let explicit = explicit_scales(data)?;
        // The tile rule is made when an output appears, after every static rule, so its `tile` would override
        // the float + pin the ShaderGlass pane rule sets ([desktop.shaderglass.window].class): while the overlay
        // is enabled (its rules are projected), that class is never matched (Hyprland's "negative:" regex).
        let tile_match = match data
            .get("desktop")
            .and_then(|d| d.get("shaderglass"))
            .filter(|s| s.get("enabled").and_then(Value::as_bool) == Some(true))
            .and_then(|s| s.get("window"))
            .and_then(|w| w.get("class"))
            .and_then(Value::as_str)
        {
            Some(class) if !class.is_empty() => {
                regex::Regex::new(class).map_err(|e| format!("desktop.shaderglass.window.class: {e}"))?;
                format!("{{workspace = sel, float = false, class = {}}}", lua(&Value::String(format!("negative:{class}")))?)
            }
            _ => "{workspace = sel, float = false}".to_string(),
        };
        let body = MOBILE_LUA
            .replace("@@TILE_MATCH@@", &tile_match)
            .replace("@@ENABLED@@", &enabled.to_string())
            .replace("@@PORTRAIT@@", &portrait.to_string())
            .replace("@@BREAKPOINT@@", &breakpoint.to_string())
            .replace("@@APPLIES@@", &lua(&Value::Table(applies_t))?)
            .replace(
                "@@PREFIXES@@",
                &lua(&Value::Array(prefixes.into_iter().map(Value::String).collect()))?,
            )
            .replace("@@LAYOUT@@", &lua(&Value::String(layout.into()))?)
            .replace("@@GAPS_IN@@", &gaps_in.to_string())
            .replace("@@GAPS_OUT@@", &gaps_out.to_string())
            .replace("@@HYPRBARS_TAB@@", &hyprbars_tab)
            .replace("@@FX@@", &fx)
            .replace("@@JSON@@", &lua(&Value::String(json))?)
            .replace("@@SCALING@@", &scaling)
            .replace("@@EXPLICIT@@", &lua(&Value::Table(explicit))?)
            .replace("@@POLICY_FN@@\n", &policy_fn);
        Ok(format!("{MOBILE_BEGIN}\n{body}{MOBILE_END}\n"))
    }

    /// Preset keys of [desktop.mobile.remote_effects].overrides: only knobs that make the remote glass
    /// cheaper or plainer (fewer blur passes, weaker optics). Colours stay the base preset's (parity).
    const REMOTE_FX_KEYS: [&str; 9] = [
        "blur_iterations",
        "blur_strength",
        "specular_strength",
        "fresnel_strength",
        "chromatic_aberration",
        "refraction_strength",
        "lens_distortion",
        "bevel_strength",
        "edge_thickness",
    ];

    /// [desktop.mobile.remote_effects] -> Lua literal `{preset, applies, spec}` (or `nil`): the remote
    /// glass preset is the base hyprglass preset ([desktop.hyprland.hyprglass.presets.<base_preset>],
    /// palette-resolved exactly as the hyprglass section projects it) with `overrides` on top, so it
    /// keeps the desktop glass' colours and blur strength while doing less work per frame.
    pub fn remote_effects(data: &Value, mobile: &toml::map::Map<String, Value>) -> Result<String, String> {
        let Some(fx) = mobile.get("remote_effects") else {
            return Ok("nil".into());
        };
        let fx = fx.as_table().ok_or("desktop.mobile.remote_effects must be a table")?;
        let enabled = fx
            .get("enabled")
            .and_then(Value::as_bool)
            .ok_or("desktop.mobile.remote_effects.enabled must be a boolean")?;
        let ident = |s: &str| !s.is_empty() && s.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'-' || c == b'_');
        let preset = fx
            .get("preset")
            .and_then(Value::as_str)
            .filter(|s| ident(s))
            .ok_or("desktop.mobile.remote_effects.preset must name a preset [A-Za-z0-9_-]")?;
        let base_name = fx
            .get("base_preset")
            .and_then(Value::as_str)
            .filter(|s| ident(s))
            .ok_or("desktop.mobile.remote_effects.base_preset must name a [desktop.hyprland.hyprglass.presets] entry")?;
        if preset == base_name {
            return Err("desktop.mobile.remote_effects.preset must differ from base_preset (it would replace the desktop glass)".into());
        }
        let applies: Vec<&str> = fx
            .get("applies_to")
            .and_then(Value::as_array)
            .ok_or("desktop.mobile.remote_effects.applies_to must list \"local\" and/or \"remote\"")?
            .iter()
            .map(|v| v.as_str().filter(|s| *s == "local" || *s == "remote"))
            .collect::<Option<Vec<_>>>()
            .filter(|v| !v.is_empty())
            .ok_or("desktop.mobile.remote_effects.applies_to must list \"local\" and/or \"remote\"")?;
        let overrides = fx
            .get("overrides")
            .and_then(Value::as_table)
            .ok_or("desktop.mobile.remote_effects.overrides must be a table")?;
        // shell layer namespaces whose `<ns>-remote` variant takes the remote preset (optional)
        let layers: Vec<&str> = match fx.get("layer_namespaces") {
            None => Vec::new(),
            Some(v) => v
                .as_array()
                .and_then(|a| a.iter().map(|x| x.as_str().filter(|s| ident(s))).collect::<Option<Vec<_>>>())
                .ok_or("desktop.mobile.remote_effects.layer_namespaces must list layer namespaces [A-Za-z0-9_-]")?,
        };
        let glass = data.get("desktop").and_then(|d| d.get("hyprland")).and_then(|h| h.get("hyprglass"));
        let base = glass
            .and_then(|g| g.get("presets"))
            .and_then(|p| p.get(base_name))
            .and_then(Value::as_table)
            .ok_or_else(|| format!("desktop.mobile.remote_effects.base_preset: hyprglass preset {base_name} not found"))?;
        let num = |v: &Value| v.as_float().or_else(|| v.as_integer().map(|i| i as f64)).filter(|x| x.is_finite());
        let mut spec = base.clone();
        for (k, v) in overrides {
            if !REMOTE_FX_KEYS.contains(&k.as_str()) {
                return Err(format!(
                    "desktop.mobile.remote_effects.overrides.{k}: not a remote glass knob ({})",
                    REMOTE_FX_KEYS.join(", ")
                ));
            }
            let x = num(v).filter(|x| *x >= 0.0).ok_or(format!("desktop.mobile.remote_effects.overrides.{k} must be a number >= 0"))?;
            // cheaper, never costlier than the desktop glass
            if let Some(b) = base.get(k).and_then(num) {
                if x > b + 1e-9 {
                    return Err(format!("desktop.mobile.remote_effects.overrides.{k} = {x} exceeds the base preset's {b}"));
                }
            }
            if k == "blur_iterations" {
                if x < 1.0 || x.fract() != 0.0 {
                    return Err("desktop.mobile.remote_effects.overrides.blur_iterations must be an integer >= 1".into());
                }
                spec.insert(k.clone(), Value::Integer(x as i64));
            } else {
                spec.insert(k.clone(), Value::Float(x));
            }
        }
        let glass_on = glass.and_then(|g| g.get("enabled")).and_then(Value::as_bool) == Some(true);
        if !enabled || !glass_on {
            return Ok("nil".into());
        }
        let spec = glass_palette(&Value::Table(spec), data)?;
        let mut applies_t = toml::map::Map::new();
        for a in applies {
            applies_t.insert(a.to_string(), Value::Boolean(true));
        }
        let layers_v = Value::Array(layers.iter().map(|s| Value::String((*s).into())).collect());
        Ok(format!(
            "{{preset = {}, applies = {}, layers = {}, spec = {}}}",
            lua(&Value::String(preset.into()))?,
            lua(&Value::Table(applies_t))?,
            lua(&layers_v)?,
            lua(&spec)?
        ))
    }

    /// Outputs whose scale the operator fixed: [desktop.hyprland.monitors.<output>].scale (the
    /// mios-displays save, e.g. the host tier) and a numeric [remote.rdp].virtual_scale for the
    /// remote display. The policy never auto-scales these; the saved value is their scale.
    pub fn explicit_scales(data: &Value) -> Result<toml::map::Map<String, Value>, String> {
        let ident = |s: &str| !s.is_empty() && s.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'-' || c == b'_');
        let num = |v: &Value| v.as_float().or_else(|| v.as_integer().map(|i| i as f64)).filter(|s| s.is_finite() && *s > 0.0);
        let mut out = toml::map::Map::new();
        if let Some(mons) = data
            .get("desktop")
            .and_then(|d| d.get("hyprland"))
            .and_then(|h| h.get("monitors"))
            .and_then(Value::as_table)
        {
            for (name, rule) in mons {
                if let Some(s) = rule.get("scale").and_then(num) {
                    if !ident(name) {
                        return Err(format!("desktop.hyprland.monitors.{name}: output must be [A-Za-z0-9_-]"));
                    }
                    out.insert(name.clone(), Value::Float(s));
                }
            }
        }
        if let Some(rdp) = data.get("remote").and_then(|r| r.get("rdp")) {
            let fixed = rdp.get("virtual_scale").and_then(|v| match v {
                Value::String(s) => s.trim().parse::<f64>().ok().filter(|s| s.is_finite() && *s > 0.0),
                other => num(other),
            });
            if let (Some(s), Some(name)) = (fixed, rdp.get("virtual_output").and_then(Value::as_str).filter(|n| ident(n))) {
                out.entry(name.to_string()).or_insert(Value::Float(s));
            }
        }
        Ok(out)
    }

    #[cfg(test)]
    mod mobile_tests {
        use super::*;

        fn vendor() -> Value {
            let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../usr/share/mios/mios.toml");
            fs::read_to_string(path).unwrap().parse().unwrap()
        }

        #[test]
        fn vendor_profile_projects_runtime_rules_without_output_names() {
            let data = vendor();
            let chunk = mobile_profile(&data).unwrap();
            assert!(chunk.starts_with(MOBILE_BEGIN) && chunk.ends_with(&format!("{MOBILE_END}\n")), "{chunk}");
            for needle in [
                "hl.on(\"monitor.added\"",
                "hl.on(\"monitor.removed\"",
                "hl.on(\"monitor.layout_changed\"",
                "hl.workspace_rule({workspace = sel",
                "tile = true",
                "breakpoint = 900",
                "mios-mobile-outputs.json",
                "local function mios_scale_policy(P, m)",
                "local S = {enabled = true",
                "hl.timer(function()",
                "mios-display-hints.json",
            ] {
                assert!(chunk.contains(needle), "missing {needle}: {chunk}");
            }
            // Positive: the profile is part of the full projection, once, after every other section.
            let full = project(&data).unwrap();
            assert_eq!(full.matches(MOBILE_BEGIN).count(), 1);
            assert!(full.trim_end().ends_with(MOBILE_END), "mobile profile must close the projection");
            // Negative: no connector is hardcoded (vendor saves no monitor scales).
            for name in ["HDMI-A-1", "HEADLESS-MIOS-RDP"] {
                assert!(!chunk.contains(name), "{name} hardcoded in the mobile profile");
            }
            assert!(chunk.contains("MIOS_SCALE_EXPLICIT = {}") && chunk.contains("local EXPLICIT = MIOS_SCALE_EXPLICIT"), "{chunk}");
            assert!(!chunk.contains("@@"), "unfilled placeholder: {chunk}");
            // The tile rule never takes the ShaderGlass pane (its own rule floats and pins it).
            assert!(
                chunk.contains(r#"match = {workspace = sel, float = false, class = "negative:^(shaderglass\\.exe)$"}, tile = true"#),
                "{chunk}"
            );
            // Negative control: without the ShaderGlass table the rule tiles every non-floating window (the old rule).
            let mut no_sg = vendor();
            no_sg["desktop"].as_table_mut().unwrap().remove("shaderglass");
            assert!(mobile_profile(&no_sg).unwrap().contains("match = {workspace = sel, float = false}, tile = true"));
            // A disabled overlay projects no ShaderGlass rule, and the tile rule names no ShaderGlass class either.
            let mut off = vendor();
            off["desktop"]["shaderglass"].as_table_mut().unwrap().insert("enabled".into(), Value::Boolean(false));
            assert!(!mobile_profile(&off).unwrap().contains("shaderglass"));
            // Without [desktop.scaling] the legacy rule is projected (no policy function).
            let mut legacy = vendor();
            legacy["desktop"].as_table_mut().unwrap().remove("scaling");
            let chunk = mobile_profile(&legacy).unwrap();
            assert!(chunk.contains("local S = nil") && !chunk.contains("function mios_scale_policy"), "{chunk}");
        }

        #[test]
        fn absent_table_projects_nothing_and_bad_values_are_rejected() {
            let mut data = vendor();
            data["desktop"].as_table_mut().unwrap().remove("mobile");
            assert_eq!(mobile_profile(&data).unwrap(), "");
            assert!(!project(&data).unwrap().contains(MOBILE_BEGIN));
            for bad in [
                ("breakpoint_px", Value::Integer(0)),
                ("breakpoint_px", Value::String("900".into())),
                ("applies_to", Value::Array(vec![Value::String("phone".into())])),
                ("applies_to", Value::Array(vec![])),
                ("remote_name_prefixes", Value::Array(vec![Value::String("HEADLESS]".into())])),
                ("layout", Value::String("bogus".into())),
                ("enabled", Value::String("yes".into())),
                ("window_controls", Value::Array(vec![Value::Table(toml::map::Map::new())])),
            ] {
                let mut d = vendor();
                d["desktop"]["mobile"].as_table_mut().unwrap().insert(bad.0.into(), bad.1.clone());
                assert!(mobile_profile(&d).is_err(), "{bad:?} accepted");
            }
            // A broken [desktop.scaling] is rejected too, not silently ignored.
            let mut d = vendor();
            d["desktop"]["scaling"].as_table_mut().unwrap().insert("max_scale".into(), Value::Float(99.0));
            assert!(mobile_profile(&d).is_err());
        }

        #[test]
        fn explicit_scales_come_from_saved_monitors_and_a_fixed_rdp_scale() {
            let mut d = vendor();
            assert!(explicit_scales(&d).unwrap().is_empty());
            let layer: Value = toml::from_str("[monitors.HDMI-A-1]\nmode = \"1920x1080@144\"\nscale = 1\n[monitors.DP-2]\nmode = \"preferred\"\n").unwrap();
            d["desktop"]["hyprland"]["monitors"] = layer["monitors"].clone();
            let e = explicit_scales(&d).unwrap();
            assert_eq!(e.get("HDMI-A-1").and_then(Value::as_float), Some(1.0));
            assert!(!e.contains_key("DP-2"), "a saved mode without a scale is not explicit: {e:?}");
            assert!(!e.contains_key("HEADLESS-MIOS-RDP"), "virtual_scale = auto is not explicit");
            d["remote"]["rdp"].as_table_mut().unwrap().insert("virtual_scale".into(), Value::String("2".into()));
            assert_eq!(explicit_scales(&d).unwrap().get("HEADLESS-MIOS-RDP").and_then(Value::as_float), Some(2.0));
            assert!(mobile_profile(&d).unwrap().contains("[\"HDMI-A-1\"] = 1"));
        }

        /// Runs the projected chunk under a recording `hl` stub. `script` drives monitors through
        /// MONS / hooks / flush() (runs pending timers; hl.monitor updates the stub monitor like
        /// Hyprland and fires layout_changed) and calls report(tag).
        fn simulate_with(chunk: &str, script: &str) -> String {
            const HARNESS: &str = r#"
local ws, win, hooks, timers, monitor_calls, presets = {}, {}, {}, {}, {}, {}
MONS = {{name = "HDMI-A-1", width = 1920, height = 1080, scale = 1, transform = 0, physical_width = 530, physical_height = 300, x = 0, y = 0, refresh_rate = 144.001}}
hl = {
 get_monitors = function() local t = {} for i, m in ipairs(MONS) do t[i] = m end return t end,
 plugin = {hyprglass = {preset = function(n, spec) assert(type(spec) == "table") table.insert(presets, n .. ":iter" .. tostring(spec.blur_iterations) .. ":spec" .. tostring(spec.specular_strength) .. ":tint" .. tostring(spec.dark and spec.dark.tint_color)) end,
  layer = function(ns, o) table.insert(presets, "layer:" .. ns .. "=" .. tostring(o and o.preset)) end}, hyprbars = {}},
 workspace_rule = function(s) s.enabled = true table.insert(ws, s) return {set_enabled = function(_, v) s.enabled = v end} end,
 window_rule = function(s) s.enabled = true table.insert(win, s) return {set_enabled = function(_, v) s.enabled = v end} end,
 on = function(ev, fn) hooks[ev] = fn end,
 timer = function(fn, opts) assert(opts.type == "oneshot" and opts.timeout >= 1) table.insert(timers, fn) return {} end,
 monitor = function(spec)
  table.insert(monitor_calls, spec.output .. "@" .. string.format("%.4f", spec.scale) .. ":" .. spec.mode .. ":" .. spec.position)
  for _, m in ipairs(MONS) do if m.name == spec.output then m.scale = spec.scale end end
 end,
}
dofile(os.getenv("CHUNK"))
function flush()
 while #timers > 0 do local t = table.remove(timers, 1) t() hooks["monitor.layout_changed"]() end
end
function add(m) table.insert(MONS, m) hooks["monitor.added"](m) end
function remove(name) for i, m in ipairs(MONS) do if m.name == name then table.remove(MONS, i) break end end hooks["monitor.removed"]() end
function changed() hooks["monitor.layout_changed"]() end
function report(tag)
 local out = {}
 for _, s in ipairs(ws) do out[#out + 1] = "ws " .. s.workspace .. "=" .. tostring(s.enabled) .. ":" .. s.layout .. ":top" .. s.gaps_out.top
  .. ":deco" .. tostring(s.no_border) .. "/" .. tostring(s.no_rounding) .. "/" .. tostring(s.decorate) .. "/" .. tostring(s.rounding) .. "/" .. tostring(s.border_size) end
 for _, s in ipairs(win) do out[#out + 1] = "win " .. s.name .. "=" .. tostring(s.enabled) .. ":" .. s.match.workspace .. ":tile" .. tostring(s.tile)
  .. ":tag" .. tostring(s.tag) .. ":nobar" .. tostring(s["hyprbars:no_bar"]) .. ":rounding" .. tostring(s.rounding) .. ":border" .. tostring(s.border_size) end
 for _, n in ipairs(presets) do out[#out + 1] = "preset " .. n end
 for _, c in ipairs(monitor_calls) do out[#out + 1] = "mon " .. c end
 local f = io.open(os.getenv("XDG_RUNTIME_DIR") .. "/mios-mobile-outputs.json")
 local j = f and f:read("*a") or "none"
 if f then f:close() end
 print(tag .. " | " .. table.concat(out, " ; ") .. " | " .. (j:gsub("\n", "")))
end
dofile(os.getenv("SCRIPT"))
"#;
            let lua = ["/usr/bin/luajit", "/usr/bin/lua"]
                .into_iter()
                .find(|p| Path::new(p).is_file())
                .expect("a Lua interpreter (luajit or lua) is required for the mobile profile test");
            let dir = tempfile::tempdir().unwrap();
            let (c, h, s) = (dir.path().join("chunk.lua"), dir.path().join("harness.lua"), dir.path().join("script.lua"));
            fs::write(&c, chunk).unwrap();
            fs::write(&h, HARNESS).unwrap();
            fs::write(&s, script).unwrap();
            let out = std::process::Command::new(lua)
                .arg(&h)
                .env("CHUNK", &c)
                .env("SCRIPT", &s)
                .env("XDG_RUNTIME_DIR", dir.path())
                .output()
                .unwrap();
            let text = format!("{}{}", String::from_utf8_lossy(&out.stdout), String::from_utf8_lossy(&out.stderr));
            assert!(out.status.success(), "{text}");
            assert!(
                !text.contains("mobile profile:") && !text.contains("mobile section failed") && !text.contains("scale policy:"),
                "{text}"
            );
            for l in text.lines().filter(|l| l.contains(" | ")) {
                let js = l.rsplit(" | ").next().unwrap();
                serde_json::from_str::<serde_json::Value>(js).unwrap_or_else(|e| panic!("bad JSON {e}: {js}"));
            }
            text
        }
        /// RDP attach -> phone resize -> rescale -> landscape phone -> desktop-sized -> detach,
        /// then a local rotation.
        fn simulate(chunk: &str) -> String {
            simulate_with(
                chunk,
                r#"
report("boot")
local rdp = {name = "HEADLESS-MIOS-RDP", width = 1280, height = 720, scale = 1, transform = 0, physical_width = 0, physical_height = 0, x = 1920, y = 0, refresh_rate = 60}
add(rdp) flush()
report("attach")
rdp.width, rdp.height = 1100, 2080 changed() flush()
report("phone")
rdp.scale = 2 changed() flush()
report("scaled")
rdp.width, rdp.height, rdp.scale = 2080, 1100, 1 changed() flush()
report("landscape")
rdp.width, rdp.height = 1920, 1080 changed() flush()
report("desktop")
remove("HEADLESS-MIOS-RDP") flush()
report("detach")
MONS[1].transform = 1 changed() flush()
report("rotated")
"#,
            )
        }
        fn line<'a>(out: &'a str, tag: &str) -> &'a str {
            out.lines().find(|l| l.starts_with(&format!("{tag} | "))).unwrap_or_else(|| panic!("no {tag}: {out}"))
        }

        #[test]
        fn phone_sized_output_gets_mobile_rules_and_desktop_output_does_not() {
            let out = simulate(&mobile_profile(&vendor()).unwrap());
            let rdp_ws = "ws m[HEADLESS-MIOS-RDP]";
            // Negative controls: the 1920x1080 desktop output and a landscape 1280x720 remote display
            // get no rules at all, and the desktop output is never given any.
            let l = line(&out, "boot");
            assert!(!l.contains("ws ") && !l.contains("win ") && !l.contains("mon "), "{l}");
            let l = line(&out, "attach");
            assert!(!l.contains("ws ") && !l.contains("win mios-mobile-") && !l.contains("mon "), "{l}");
            // ONE glass (operator "Unify the designs"): no window on any output gets a preset of its own
            assert!(!l.contains("hyprglass_preset_") && !l.contains("mios-remote-fx-"), "{l}");
            assert!(
                line(&out, "boot").contains("\"HDMI-A-1\": {\"mobile\": false, \"origin\": \"local\", \"logical_width\": 1920, \"logical_height\": 1080, \"scale\": 1,"),
                "{out}"
            );
            assert!(line(&out, "boot").contains("\"source\": \"edid\""), "{out}");
            assert!(line(&out, "attach").contains("\"HEADLESS-MIOS-RDP\": {\"mobile\": false, \"origin\": \"remote\""), "{out}");
            // Positive: the 1100x2080 phone display is scaled 2.5 by the policy (no DPI hint, 0 mm:
            // the handheld fallback), is mobile, and gets the one-window layout and the tiling rule.
            let phone = line(&out, "phone");
            assert!(phone.contains(&format!("{rdp_ws}=true:monocle:top0")), "{phone}");
            assert!(phone.contains("win mios-mobile-HEADLESS-MIOS-RDP=true:m[HEADLESS-MIOS-RDP]:tiletrue"), "{phone}");
            assert!(
                phone.contains("\"HEADLESS-MIOS-RDP\": {\"mobile\": true, \"origin\": \"remote\", \"logical_width\": 1100, \"logical_height\": 2080, \"scale\": 2.5, \"scale_120\": 300, \"applied_scale\": 1, \"policy_logical_width\": 440, \"policy_logical_height\": 832, \"explicit\": false, \"auto_apply\": false, \"source\": \"fallback-handheld\""),
                "{phone}"
            );
            // Remote outputs are not rescaled by default (apply_to = ["local"]: wayvnc 0.9.1).
            assert!(!phone.contains("mon "), "{phone}");
            assert!(phone.contains("\"config\": {\"enabled\": true") || phone.contains("\"config\": {\"enabled\":true"), "{phone}");
            assert!(!phone.contains("ws m[HDMI-A-1]"), "{phone}");
            // Scale 2 keeps it mobile (550x1040 logical) without registering the rules twice.
            let scaled = line(&out, "scaled");
            assert!(scaled.contains("\"logical_width\": 550, \"logical_height\": 1040"), "{scaled}");
            assert_eq!(scaled.matches(rdp_ws).count(), 1, "{scaled}");
            // The phone rotated to landscape is still a phone (logical short side 440).
            assert!(line(&out, "landscape").contains(&format!("{rdp_ws}=true")), "{out}");
            // Resized to a desktop client, then detached: the rules switch off, not pile up.
            for tag in ["desktop", "detach"] {
                let l = line(&out, tag);
                assert!(l.contains(&format!("{rdp_ws}=false")) && l.contains("mios-mobile-HEADLESS-MIOS-RDP=false"), "{l}");
                assert_eq!(l.matches(rdp_ws).count(), 1, "{l}");
            }
            // A 24" desktop monitor rotated to portrait is NOT a phone (logical short side 1080).
            let rotated = line(&out, "rotated");
            assert!(!rotated.contains("ws m[HDMI-A-1]"), "{rotated}");
            assert!(rotated.contains("\"HDMI-A-1\": {\"mobile\": false"), "{rotated}");
        }

        #[test]
        fn applies_to_portrait_and_enabled_gate_the_rules() {
            let mut d = vendor();
            d["desktop"]["mobile"]["applies_to"] = Value::Array(vec![Value::String("local".into())]);
            d["desktop"]["scaling"]["portrait_is_mobile"] = Value::Boolean(true);
            let out = simulate(&mobile_profile(&d).unwrap());
            assert!(!line(&out, "phone").contains("ws m[HEADLESS-MIOS-RDP]"), "remote matched with applies_to = [local]: {out}");
            assert!(line(&out, "rotated").contains("ws m[HDMI-A-1]=true"), "portrait_is_mobile: {out}");
            let mut d = vendor();
            d["desktop"]["mobile"]["enabled"] = Value::Boolean(false);
            let out = simulate(&mobile_profile(&d).unwrap());
            assert!(!out.contains("ws m["), "disabled profile made rules: {out}");
            assert!(line(&out, "phone").contains("\"mobile\": false"), "{out}");
            // Legacy rule (no [desktop.scaling]): portrait remote is mobile, never rescaled.
            let mut d = vendor();
            d["desktop"].as_table_mut().unwrap().remove("scaling");
            let out = simulate(&mobile_profile(&d).unwrap());
            assert!(line(&out, "phone").contains("ws m[HEADLESS-MIOS-RDP]=true") && line(&out, "phone").contains("\"source\": \"legacy\""), "{out}");
            assert!(!out.contains("mon "), "{out}");
        }

        #[test]
        fn policy_scales_local_non_explicit_outputs_and_never_explicit_ones() {
            // HDMI-A-1 is saved at scale 1 (host tier) and claims a phone-dense EDID; DP-9 is an
            // unsaved 27" 4K panel. The policy rescales DP-9 once and never touches HDMI-A-1.
            let mut d = vendor();
            let layer: Value = toml::from_str("[monitors.HDMI-A-1]\nmode = \"1920x1080@144\"\nposition = \"0x0\"\nscale = 1\n").unwrap();
            d["desktop"]["hyprland"]["monitors"] = layer["monitors"].clone();
            let script = r#"
MONS[1].physical_width, MONS[1].physical_height = 86, 48
report("boot")
add({name = "DP-9", width = 3840, height = 2160, scale = 1, transform = 0, physical_width = 597, physical_height = 336, x = 1920, y = 0, refresh_rate = 60})
report("added")
flush()
report("applied")
changed() flush()
report("again")
"#;
            let out = simulate_with(&mobile_profile(&d).unwrap(), script);
            let boot = line(&out, "boot");
            assert!(boot.contains("\"HDMI-A-1\": {\"mobile\": false, \"origin\": \"local\", \"logical_width\": 1920, \"logical_height\": 1080, \"scale\": 1, \"scale_120\": 120, \"applied_scale\": 1"), "{boot}");
            assert!(boot.contains("\"explicit\": true, \"auto_apply\": false, \"source\": \"explicit\""), "{boot}");
            let applied = line(&out, "applied");
            assert!(applied.contains("mon DP-9@1.6667:3840x2160@60.000:1920x0"), "{applied}");
            assert!(!applied.contains("mon HDMI-A-1"), "{applied}");
            assert!(applied.contains("\"DP-9\": {\"mobile\": false, \"origin\": \"local\", \"logical_width\": 2304, \"logical_height\": 1296, \"scale\": 1.66667, \"scale_120\": 200"), "{applied}");
            // Exactly one hl.monitor call in total: no re-apply loop on later layout changes.
            assert_eq!(line(&out, "again").matches("mon ").count(), 1, "{out}");
            // Positive control for the explicit list: without the saved scale HDMI-A-1 IS rescaled.
            let out = simulate_with(&mobile_profile(&vendor()).unwrap(), script);
            assert!(line(&out, "applied").contains("mon HDMI-A-1@"), "{out}");
        }

        /// The MiOS-only plugin keys (hyprglass edge pass; hyprbars glass tab + mobile tabs) are projected in their
        /// own pcall'd config calls AFTER the plugin's stock keys, so a plugin without the MiOS patch keeps its
        /// config; the hyprbars tab is pointed at the loaded hyprglass (glass_plugin = its SSOT path).
        #[test]
        fn mios_plugin_keys_are_isolated_and_validated() {
            let mut d = vendor();
            let h = &mut d["desktop"]["hyprland"];
            h["hyprglass"]["config"]["edge_pass"] = Value::Float(1.5);
            h["hyprglass"]["config"]["edge_pass_width"] = Value::Integer(8);
            h["hyprbars"]["config"]["button_glass"] = Value::Float(0.8);
            let out = project(&d).unwrap();
            let glass = &out[out.find("mios_section(\"hyprglass\"").unwrap()..];
            let glass = &glass[..glass.find("end\nend)").unwrap()];
            let stock = glass.find(" hg.config(").unwrap();
            let mios = glass.find("pcall(hg.config, {[\"edge_pass\"] = 1.5, [\"edge_pass_width\"] = 8})").unwrap_or_else(|| panic!("{glass}"));
            assert!(stock < mios && !glass[stock..mios].contains("edge_pass"), "edge keys leaked into the stock config: {glass}");
            assert!(glass.contains("mios_state.hyprglass_mios = mios_ok"), "{glass}");
            let bars = &out[out.find("mios_section(\"hyprbars\"").unwrap()..];
            let bars = &bars[..bars.find("end\nend)").unwrap()];
            let path = d["desktop"]["hyprland"]["hyprglass"]["path"].as_str().unwrap().to_string();
            let ext = bars.find("pcall(hl.config, {[\"plugin\"] = {[\"hyprbars\"] = {").unwrap_or_else(|| panic!("{bars}"));
            assert!(bars[..ext].contains("add_button"), "MiOS keys must follow the buttons: {bars}");
            assert!(bars[ext..].contains(&format!("[\"glass_plugin\"] = \"{path}\"")) && bars[ext..].contains("[\"button_glass\"] = 0.8"), "{bars}");
            assert!(bars[ext..].contains("[\"mobile_touch_target\"] = 44") && bars[ext..].contains("call mobile toggleControls"), "{bars}");
            assert!(!bars[..ext].contains("glass_plugin") && !bars[..ext].contains("mobile_"), "{bars}");
            assert!(bars.contains("mios_state.hyprbars_mobile = mios_ok"), "{bars}");
            // negative controls: out-of-range values and a mobile table that would break the touch tabs
            for (k, v) in [("edge_pass", Value::Float(-1.0)), ("edge_pass", Value::Float(9.0)), ("edge_pass_width", Value::Integer(-2))] {
                let mut bad = d.clone();
                bad["desktop"]["hyprland"]["hyprglass"]["config"][k] = v.clone();
                assert!(project(&bad).is_err(), "{k} = {v:?} accepted");
            }
            let mut bad = d.clone();
            bad["desktop"]["hyprland"]["hyprbars"]["config"]["button_glass"] = Value::Float(1.5);
            assert!(project(&bad).is_err());
            for (k, v) in [("touch_target", Value::Integer(30)), ("close_button", Value::Integer(1)), ("menu_palette", Value::String("nope".into())),
                           ("menu_action", Value::String("true".into()))] {
                let mut bad = d.clone();
                bad["desktop"]["hyprland"]["hyprbars"]["mobile"][k] = v.clone();
                assert!(project(&bad).is_err(), "hyprbars.mobile.{k} = {v:?} accepted");
            }
        }

        /// hyprbars_tab = "mobile": the controls are the window's own glass split into the plugin's two touch tabs
        /// (hyprbars:mobile) -- only when the plugin accepted the MiOS mobile keys (MIOS_SESSION.hyprbars_mobile);
        /// otherwise the desktop tab is hidden and the shell draws the tabs (published hyprbars_tabs = false).
        #[test]
        fn mobile_windows_get_the_plugin_touch_tabs_when_hyprbars_took_the_mobile_keys() {
            let mut d = vendor();
            d["desktop"]["mobile"]["hyprbars_tab"] = Value::String("mobile".into());
            let chunk = mobile_profile(&d).unwrap();
            let with = simulate(&format!("MIOS_SESSION = {{hyprbars_mobile = true}}\n{chunk}"));
            let phone = line(&with, "phone");
            assert!(phone.contains("win mios-mobile-tabs-HEADLESS-MIOS-RDP=true:m[HEADLESS-MIOS-RDP]"), "{phone}");
            assert!(!phone.contains("mios-mobile-nobar-"), "plugin tabs AND no_bar: {phone}");
            assert!(phone.contains("\"hyprbars_tabs\": true"), "{phone}");
            assert!(line(&with, "boot").contains("\"hyprbars_tabs\": false"), "tabs published before any mobile output: {with}");
            // negative: a plugin without the MiOS mobile keys -> no hyprbars:mobile rule, the desktop tab hidden
            let without = simulate(&chunk);
            let phone = line(&without, "phone");
            assert!(!phone.contains("mios-mobile-tabs-"), "{phone}");
            assert!(phone.contains("win mios-mobile-nobar-HEADLESS-MIOS-RDP=true:m[HEADLESS-MIOS-RDP]"), "{phone}");
            assert!(phone.contains("\"hyprbars_tabs\": false"), "{phone}");
            for bad in [Value::String("yes".into()), Value::Integer(1)] {
                d["desktop"]["mobile"]["hyprbars_tab"] = bad.clone();
                assert!(mobile_profile(&d).is_err(), "hyprbars_tab = {bad:?} accepted");
            }
        }

        #[test]
        fn mobile_windows_inherit_the_global_decoration() {
            let chunk = mobile_profile(&vendor()).unwrap();
            // Negative (regression guard): no mobile rule may square windows off or strip their border/glass.
            for bad in ["no_rounding", "no_border", "decorate =", "rounding = 0", "border_size = 0"] {
                assert!(!chunk.contains(bad), "mobile profile overrides the global decoration ({bad}): {chunk}");
            }
            let out = simulate(&chunk);
            let phone = line(&out, "phone");
            // Positive: the phone workspace rule sets layout/gaps only; every decoration key is unset (nil).
            assert!(phone.contains("ws m[HEADLESS-MIOS-RDP]=true:monocle:top0:deconil/nil/nil/nil/nil"), "{phone}");
            for l in out.lines().filter(|l| l.contains("win ")) {
                assert!(!l.contains(":roundingfalse") && !l.contains(":rounding0") && !l.contains(":border0"), "{l}");
            }
            // Only the hyprbars tab is hidden on phones (the shoulders carry the controls) ...
            assert!(phone.contains("win mios-mobile-nobar-HEADLESS-MIOS-RDP=true:m[HEADLESS-MIOS-RDP]:tilenil:tagnil:nobartrue"), "{phone}");
            assert!(line(&out, "detach").contains("win mios-mobile-nobar-HEADLESS-MIOS-RDP=false"), "{out}");
            // ... unless hyprbars_tab = true, and never without the hyprbars plugin loaded.
            let mut d = vendor();
            d["desktop"]["mobile"]["hyprbars_tab"] = Value::Boolean(true);
            assert!(!simulate(&mobile_profile(&d).unwrap()).contains("nobar-"), "hyprbars_tab = true still hid the tab");
            // The old overrides are rejected (a regression would square phone windows again); "inherit" is fine.
            for k in ["window_rounding", "window_border", "window_decorations"] {
                for v in [Value::Boolean(false), Value::Boolean(true), Value::Integer(0)] {
                    let mut d = vendor();
                    d["desktop"]["mobile"].as_table_mut().unwrap().insert(k.into(), v.clone());
                    let e = mobile_profile(&d).expect_err(&format!("{k} = {v:?} accepted"));
                    assert!(e.contains("global [desktop.hyprland] decoration"), "{e}");
                }
                let mut d = vendor();
                d["desktop"]["mobile"].as_table_mut().unwrap().insert(k.into(), Value::String("inherit".into()));
                assert!(mobile_profile(&d).is_ok(), "{k} = \"inherit\" rejected");
            }
            // The vendor SSOT carries none of them.
            for k in ["window_rounding", "window_border", "window_decorations"] {
                assert!(vendor()["desktop"]["mobile"].get(k).is_none(), "vendor [desktop.mobile].{k} is back");
            }
        }

        #[test]
        fn remote_outputs_get_the_cheaper_glass_preset_in_parity_with_the_desktop_one() {
            // The shipped SSOT keeps it OFF: remote and desktop windows and layers use the SAME glass (operator
            // 2026-10-16, "Unify the designs"); a vendor projection that tags any window with another preset fails.
            let shipped = vendor();
            assert_eq!(shipped["desktop"]["mobile"]["remote_effects"]["enabled"].as_bool(), Some(false), "remote glass diverges from the desktop");
            let unified = simulate(&mobile_profile(&shipped).unwrap());
            assert!(!unified.contains("hyprglass_preset_") && !unified.contains("preset mios-remote") && !unified.contains("preset layer:"), "{unified}");
            // The mechanism itself, switched on in a fixture:
            let mut data = vendor();
            data["desktop"]["mobile"]["remote_effects"]["enabled"] = Value::Boolean(true);
            let chunk = mobile_profile(&data).unwrap();
            let full = project(&data).unwrap();
            // Parity: the remote preset is the projected desktop preset (same palette-resolved tint) ...
            let tint = |text: &str, from: &str| -> String {
                let i = text.find(from).unwrap_or_else(|| panic!("no {from}"));
                let rest = &text[i..];
                let j = rest.find("[\"tint_color\"] = ").expect("tint_color") + "[\"tint_color\"] = ".len();
                rest[j..].split(|c: char| !c.is_ascii_digit()).next().unwrap().to_string()
            };
            let base = data["desktop"]["mobile"]["remote_effects"]["base_preset"].as_str().unwrap();
            assert_eq!(tint(&full, &format!("hg.preset(\"{base}\"")), tint(&chunk, "local FX = "), "remote glass tint differs from the desktop preset");
            // ... with fewer blur passes and no specular/fresnel/chromatic extras.
            let out = simulate(&chunk);
            let phone = line(&out, "phone");
            assert_eq!(out.matches("preset mios-remote:iter1:spec0").count(), out.lines().filter(|l| l.contains(" | ")).count(), "{out}");
            assert!(phone.contains("win mios-remote-fx-HEADLESS-MIOS-RDP=true:m[HEADLESS-MIOS-RDP]:tilenil:tag+hyprglass_preset_mios-remote"), "{phone}");
            assert!(phone.contains("\"glass_preset\": \"mios-remote\""), "{phone}");
            // Shell layers on the phone opt in by namespace (<ns>-remote) -> the same remote preset.
            for ns in ["mios-topbar", "mios-dock"] {
                assert!(out.contains(&format!("preset layer:{ns}-remote=mios-remote")), "{ns}: {out}");
            }
            assert!(!out.contains("preset layer:mios-topbar=") && !out.contains("preset layer:mios-dock="), "desktop namespaces remapped: {out}");
            for tag in ["boot", "rotated"] {
                let l = line(&out, tag);
                assert!(!l.contains("m[HDMI-A-1]") && l.contains("\"HDMI-A-1\": {") && l.contains("\"glass_preset\": \"\"}"), "local output tagged: {l}");
            }
            assert!(line(&out, "detach").contains("win mios-remote-fx-HEADLESS-MIOS-RDP=false"), "{out}");
            // Negative controls: disabled -> no preset, no tag; hyprglass not loaded -> nothing tagged.
            let mut d = data.clone();
            d["desktop"]["mobile"]["remote_effects"]["enabled"] = Value::Boolean(false);
            let off = simulate(&mobile_profile(&d).unwrap());
            assert!(!off.contains("mios-remote-fx-") && !off.contains("preset mios-remote") && !off.contains("preset layer:"), "{off}");
            let none = simulate_with(&format!("hl.plugin = {{}}\n{chunk}"), "report(\"boot\")\nadd({name = \"HEADLESS-MIOS-RDP\", width = 1100, height = 2080, scale = 1, transform = 0, physical_width = 0, physical_height = 0, x = 0, y = 1080, refresh_rate = 90})\nreport(\"phone\")\n");
            assert!(!none.contains("mios-remote-fx-") && !none.contains("nobar-"), "{none}");
            assert!(line(&none, "phone").contains("ws m[HEADLESS-MIOS-RDP]=true"), "{none}");
            // Bad values are rejected: costlier than the desktop glass, unknown knob, own base, missing base.
            for (k, v) in [
                ("overrides", toml::from_str::<Value>("x = { blur_iterations = 3 }").unwrap()["x"].clone()),
                ("overrides", toml::from_str::<Value>("x = { blur_iterations = 0 }").unwrap()["x"].clone()),
                ("overrides", toml::from_str::<Value>("x = { tint_alpha = 0.0 }").unwrap()["x"].clone()),
                ("overrides", toml::from_str::<Value>("x = { specular_strength = -1.0 }").unwrap()["x"].clone()),
                ("preset", Value::String(base.into())),
                ("preset", Value::String("bad name".into())),
                ("base_preset", Value::String("no-such-preset".into())),
                ("applies_to", Value::Array(vec![Value::String("phone".into())])),
                ("enabled", Value::String("yes".into())),
                ("layer_namespaces", Value::Array(vec![Value::String("bad ns".into())])),
                ("layer_namespaces", Value::String("mios-dock".into())),
            ] {
                let mut d = data.clone();
                d["desktop"]["mobile"]["remote_effects"].as_table_mut().unwrap().insert(k.into(), v.clone());
                assert!(mobile_profile(&d).is_err(), "remote_effects.{k} = {v:?} accepted");
            }
        }
    }
    // ---- end mobile layout profile ---------------------------------------------------------------

    // ---- Magic lamp ([desktop.hyprland.lamp]) -- lamp lane, additive ---------------------------------------------
    // Self-contained like mobile_profile: `project` calls it once, right after the mios-wobbly section, because the
    // lamp lives in that plugin (one compositor mesh backend). It touches no other section and reuses only `lua`.
    // The plugin registers plugin:mios_lamp:* and hl.plugin.mios_lamp only once Hyprland has loaded it (the config
    // is evaluated again after plugin loads), so the body waits for hl.plugin.mios_lamp exactly like the hyprbars
    // section waits for hl.plugin.hyprbars. Precedence rules key off the plugin's anim_tag: a lamp-launched window gets
    // HyprWindowShade's windowshade_open shader (pass-through) instead of the global open effect, and no hyprbars tab
    // while the lamp runs (hyprbars draws it rigidly at the real window box, outside the deformed pass).

    /// Lua for [desktop.hyprland.lamp]; empty when the table is absent or enabled = false.
    pub fn lamp_profile(data: &Value) -> Result<String, String> {
        let Some(h) = data.get("desktop").and_then(|d| d.get("hyprland")) else {
            return Ok(String::new());
        };
        let Some(lamp) = h.get("lamp") else {
            return Ok(String::new());
        };
        let t = lamp.as_table().ok_or("desktop.hyprland.lamp must be a table")?;
        if !t.get("enabled").and_then(Value::as_bool).ok_or("desktop.hyprland.lamp.enabled must be a boolean")? {
            return Ok(String::new());
        }
        let ident = |s: &str| !s.is_empty() && s.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'-' || c == b'_');
        let int = |k: &str, lo: i64, hi: i64| -> Result<i64, String> {
            t.get(k)
                .and_then(Value::as_integer)
                .filter(|v| (lo..=hi).contains(v))
                .ok_or(format!("desktop.hyprland.lamp.{k} must be an integer in {lo}..={hi}"))
        };
        let num = |v: &Value| v.as_float().or(v.as_integer().map(|i| i as f64)).filter(|f| f.is_finite());
        let word = |k: &str, allowed: &[&str]| -> Result<String, String> {
            t.get(k)
                .and_then(Value::as_str)
                .filter(|s| allowed.contains(s))
                .map(str::to_owned)
                .ok_or(format!("desktop.hyprland.lamp.{k} must be one of {allowed:?}"))
        };
        let mut events = Vec::new();
        for e in t.get("events").and_then(Value::as_array).ok_or("desktop.hyprland.lamp.events must be an array")? {
            let e = e
                .as_str()
                .filter(|e| ["launch", "minimize", "restore"].contains(e))
                .ok_or("desktop.hyprland.lamp.events entries must be \"launch\", \"minimize\" or \"restore\"")?;
            if !events.contains(&e) {
                events.push(e);
            }
        }
        // Easing: a [desktop.hyprland].curves name or inline points [[x1, y1], [x2, y2]] (CSS cubic-bezier).
        let points = match t.get("curve") {
            Some(Value::String(name)) => h
                .get("curves")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
                .find(|c| c.get("name").and_then(Value::as_str) == Some(name.as_str()))
                .and_then(|c| c.get("points"))
                .ok_or(format!("desktop.hyprland.lamp.curve names no [desktop.hyprland] curves entry \"{name}\""))?,
            Some(v) => v,
            None => return Err("desktop.hyprland.lamp.curve is required".into()),
        };
        let bez: Vec<f64> = points
            .as_array()
            .filter(|p| p.len() == 2)
            .and_then(|p| p.iter().filter_map(Value::as_array).flat_map(|xy| xy.iter().map(num)).collect::<Option<Vec<f64>>>())
            .filter(|v| v.len() == 4 && (0.0..=1.0).contains(&v[0]) && (0.0..=1.0).contains(&v[2]) && (-2.0..=3.0).contains(&v[1]) && (-2.0..=3.0).contains(&v[3]))
            .ok_or("desktop.hyprland.lamp.curve must be [[x1, y1], [x2, y2]] with x in 0..=1 (or a curves name)")?;
        let pane = t
            .get("pane_alpha")
            .and_then(num)
            .filter(|v| (0.05..=1.0).contains(v))
            .ok_or("desktop.hyprland.lamp.pane_alpha must be a number in 0.05..=1")?;
        let ws = t
            .get("minimized_workspace")
            .and_then(Value::as_str)
            .filter(|s| s.strip_prefix("special:").is_some_and(ident))
            .ok_or("desktop.hyprland.lamp.minimized_workspace must be special:<name>")?;
        let tag = t.get("anim_tag").and_then(Value::as_str).filter(|s| ident(s)).ok_or("desktop.hyprland.lamp.anim_tag must be [A-Za-z0-9_-]")?;
        // One effect on every output, local or remote/mobile: the only value (operator: unified design).
        if t.get("remote").and_then(Value::as_str) != Some("same") {
            return Err("desktop.hyprland.lamp.remote must be \"same\" (every output, local or remote, gets the same lamp)".into());
        }
        let hide_bars = t.get("hide_hyprbars").and_then(Value::as_bool).ok_or("desktop.hyprland.lamp.hide_hyprbars must be a boolean")?;
        let restore_tree = t.get("restore_tree").and_then(Value::as_bool).ok_or("desktop.hyprland.lamp.restore_tree must be a boolean")?;
        let shade_open = match t.get("windowshade_open") {
            None => None,
            Some(v) => Some(v.as_str().filter(|s| ident(s)).ok_or("desktop.hyprland.lamp.windowshade_open must name a shader in windowshade.shader_dir")?),
        };
        let mut cfg = toml::map::Map::new();
        cfg.insert("enabled".into(), Value::Boolean(true));
        cfg.insert("events".into(), Value::String(events.join(",")));
        for (k, lo, hi) in [("duration_open_ms", 50, 5000), ("duration_minimize_ms", 50, 5000), ("duration_restore_ms", 50, 5000), ("mesh_columns", 1, 64), ("mesh_rows", 4, 128), ("launch_timeout_ms", 500, 60000)] {
            cfg.insert(k.into(), Value::Integer(int(k, lo, hi)?));
        }
        cfg.insert("curve".into(), Value::String(bez.iter().map(|v| v.to_string()).collect::<Vec<_>>().join(" ")));
        cfg.insert("hidden_dock".into(), Value::String(word("hidden_dock", &["edge", "icon"])?));
        cfg.insert("fallback_edge".into(), Value::String(word("fallback_edge", &["bottom", "top", "left", "right"])?));
        cfg.insert("pane_alpha".into(), Value::Float(pane));
        cfg.insert("restore_tree".into(), Value::Boolean(restore_tree));
        cfg.insert("minimized_workspace".into(), Value::String(ws.into()));
        cfg.insert("anim_tag".into(), Value::String(tag.into()));
        let mut plugin = toml::map::Map::new();
        plugin.insert("mios_lamp".into(), Value::Table(cfg));
        let mut table = toml::map::Map::new();
        table.insert("plugin".into(), Value::Table(plugin));

        let wob = h.get("mios-wobbly").ok_or("desktop.hyprland.lamp needs [desktop.hyprland.mios-wobbly] (its plugin is the lamp's backend)")?;
        let wob_on = wob.get("enabled").and_then(Value::as_bool) == Some(true);
        let path = wob.get("path").ok_or("desktop.hyprland.mios-wobbly.path is required for the lamp")?;
        let mut out = String::from("mios_section(\"mios-lamp\", function()\n");
        if !wob_on {
            // the wobble is off: load its plugin for the lamp alone (no window carries the wobble tag)
            out += &format!("if not hl.plugin.mios_lamp then hl.plugin.load({}) end\n", lua(path)?);
        }
        out += &format!("if hl.plugin.mios_lamp then\n hl.config({})\n", lua(&Value::Table(table))?);
        let on = |k: &str| h.get(k).and_then(|v| v.get("enabled")).and_then(Value::as_bool) == Some(true);
        if let (Some(name), true) = (shade_open, on("windowshade")) {
            let dir = h.get("windowshade").and_then(|w| w.get("shader_dir")).ok_or("desktop.hyprland.windowshade.shader_dir is required")?;
            out += &format!(
                " local fx = (os.getenv(\"XDG_RUNTIME_DIR\") or \"/tmp\") .. \"/mios-shader-{name}.glsl\"\n do\n  local i = io.open(fx, \"r\")\n  if i then i:close() else\n   local s = io.open({} .. \"/{name}.glsl\", \"r\")\n   if s then local src = s:read(\"a\") s:close() local o = io.open(fx, \"w\") if o then o:write(src) o:close() end end\n  end\n end\n hl.window_rule({{[\"name\"] = \"mios-lamp-shade\", [\"match\"] = {{[\"tag\"] = {}}}, [\"tag\"] = \"+shader_open:\" .. fx}})\n",
                lua(dir)?,
                lua(&Value::String(tag.into()))?
            );
        }
        if hide_bars && on("hyprbars") {
            out += &format!(" hl.window_rule({{[\"name\"] = \"mios-lamp-bars\", [\"match\"] = {{[\"tag\"] = {}}}, [\"hyprbars:no_bar\"] = true}})\n", lua(&Value::String(tag.into()))?);
        }
        out += "end\nend)\n";
        Ok(out)
    }

    #[cfg(test)]
    mod lamp_tests {
        use super::*;

        fn vendor() -> Value {
            let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../usr/share/mios/mios.toml");
            fs::read_to_string(path).unwrap().parse().unwrap()
        }

        #[test]
        fn vendor_lamp_projects_after_the_wobble_and_waits_for_the_plugin() {
            let data = vendor();
            let full = project(&data).unwrap();
            let wob = full.find("mios_section(\"mios-wobbly\"").expect("wobble section");
            let lamp = full.find("mios_section(\"mios-lamp\"").expect("lamp section");
            assert!(lamp > wob, "the lamp section must follow the plugin load");
            let l = lamp_profile(&data).unwrap();
            assert!(l.contains("if hl.plugin.mios_lamp then"), "{l}");
            assert!(l.contains("[\"events\"] = \"launch,minimize,restore\""), "{l}");
            assert!(l.contains("[\"minimized_workspace\"] = \"special:minimized\""), "{l}");
            assert!(l.contains("[\"restore_tree\"] = true"), "{l}");
            assert!(l.contains("\"+shader_open:\" .. fx") && l.contains("mios-shader-none-open.glsl"), "{l}");
            assert!(l.contains("[\"hyprbars:no_bar\"] = true"), "{l}");
            assert!(!l.contains("hl.plugin.load"), "the enabled wobble section already loads the plugin: {l}");
            // the curve reaches the plugin as four numbers
            assert!(regex_free_curve(&l).is_some(), "{l}");
        }

        fn regex_free_curve(l: &str) -> Option<Vec<f64>> {
            let at = l.find("[\"curve\"] = \"")? + 13;
            let end = l[at..].find('"')? + at;
            let v: Vec<f64> = l[at..end].split(' ').map(|s| s.parse().ok()).collect::<Option<_>>()?;
            (v.len() == 4).then_some(v)
        }

        #[test]
        fn lamp_off_or_absent_projects_nothing_and_wobble_off_loads_the_plugin() {
            let mut data = vendor();
            data["desktop"]["hyprland"]["lamp"]["enabled"] = Value::Boolean(false);
            assert_eq!(lamp_profile(&data).unwrap(), "");
            data["desktop"]["hyprland"].as_table_mut().unwrap().remove("lamp");
            assert_eq!(lamp_profile(&data).unwrap(), "");
            let mut data = vendor();
            data["desktop"]["hyprland"]["mios-wobbly"]["enabled"] = Value::Boolean(false);
            let l = lamp_profile(&data).unwrap();
            assert!(l.contains("if not hl.plugin.mios_lamp then hl.plugin.load(\"/usr/lib64/mios/mios-wobbly.so\") end"), "{l}");
            // windowshade off: no shader rule; hyprbars off: no tab rule
            let mut data = vendor();
            data["desktop"]["hyprland"]["windowshade"]["enabled"] = Value::Boolean(false);
            data["desktop"]["hyprland"]["hyprbars"]["enabled"] = Value::Boolean(false);
            let l = lamp_profile(&data).unwrap();
            assert!(!l.contains("shader_open") && !l.contains("no_bar"), "{l}");
        }

        #[test]
        fn lamp_curve_by_name_and_inline() {
            let mut data = vendor();
            data["desktop"]["hyprland"]["lamp"]["curve"] = Value::String("miosGlassIn".into());
            let v = regex_free_curve(&lamp_profile(&data).unwrap()).unwrap();
            assert_eq!(v, vec![0.75, 0.0, 0.9, 0.2]);
            data["desktop"]["hyprland"]["lamp"]["curve"] = Value::String("noSuchCurve".into());
            assert!(lamp_profile(&data).unwrap_err().contains("noSuchCurve"));
        }

        #[test]
        fn lamp_projection_parses_as_lua() {
            let data = vendor();
            let chunk = format!(
                "local function mios_section(n, f) f() end\nhl = {{ plugin = {{ mios_lamp = {{}} }}, config = function() end, window_rule = function() end }}\n{}",
                lamp_profile(&data).unwrap()
            );
            let dir = tempfile::tempdir().unwrap();
            let path = dir.path().join("lamp.lua");
            fs::write(&path, chunk).unwrap();
            let lua = ["/usr/bin/luajit", "/usr/bin/lua", "/usr/sbin/lua"].into_iter().find(|p| Path::new(p).is_file()).expect("a Lua interpreter");
            let out = std::process::Command::new(lua).arg(&path).env("XDG_RUNTIME_DIR", dir.path()).output().unwrap();
            assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
            // the pass-through shader was materialised for the rule (copied from shader_dir when present)
            let shader = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../usr/share/mios/hyprland/shaders/none-open.glsl");
            if shader.is_file() && Path::new("/usr/share/mios/hyprland/shaders/none-open.glsl").is_file() {
                assert!(dir.path().join("mios-shader-none-open.glsl").is_file());
            }
        }

        #[test]
        fn lamp_rejects_bad_values() {
            let data = vendor();
            for (k, v) in [
                ("enabled", Value::String("yes".into())),
                ("events", Value::Array(vec![Value::String("close".into())])),
                ("events", Value::String("minimize".into())),
                ("duration_minimize_ms", Value::Integer(0)),
                ("duration_open_ms", Value::Integer(60000)),
                ("mesh_rows", Value::Integer(2)),
                ("mesh_columns", Value::Integer(0)),
                ("curve", Value::Array(vec![Value::Array(vec![Value::Float(1.5), Value::Float(0.0)]), Value::Array(vec![Value::Float(0.2), Value::Float(1.0)])])),
                ("curve", Value::Array(vec![Value::Float(0.4)])),
                ("hidden_dock", Value::String("nowhere".into())),
                ("fallback_edge", Value::String("middle".into())),
                ("pane_alpha", Value::Float(0.0)),
                ("pane_alpha", Value::Float(1.5)),
                ("minimized_workspace", Value::String("minimized".into())),
                ("minimized_workspace", Value::String("special:bad name".into())),
                ("anim_tag", Value::String("bad tag".into())),
                ("remote", Value::String("lighter".into())),
                ("hide_hyprbars", Value::String("true".into())),
                ("restore_tree", Value::String("true".into())),
                ("restore_tree", Value::Integer(1)),
                ("windowshade_open", Value::String("../x".into())),
                ("launch_timeout_ms", Value::Integer(10)),
            ] {
                let mut d = data.clone();
                d["desktop"]["hyprland"]["lamp"].as_table_mut().unwrap().insert(k.into(), v.clone());
                assert!(lamp_profile(&d).is_err(), "lamp.{k} = {v:?} accepted");
            }
            // negative control: the untouched vendor table is accepted (the loop above is not vacuous)
            assert!(!lamp_profile(&data).unwrap().is_empty());
            // the lamp without its backend table fails loudly
            let mut d = data.clone();
            d["desktop"]["hyprland"].as_table_mut().unwrap().remove("mios-wobbly");
            assert!(lamp_profile(&d).unwrap_err().contains("mios-wobbly"));
        }
    }
    // ---- end magic lamp ------------------------------------------------------------------------------------------

    /// The Lua load API declares configuration; live loading uses native IPC.
    fn hypr3d_profile(data: &Value) -> Result<String, String> {
        let Some(plugin) = hardware_profile_table(data)?.get("hypr3d") else {
            return Ok(String::new());
        };
        let table = plugin.as_table().ok_or("desktop.hyprland.hypr3d must be a table")?;
        if !table.get("enabled").and_then(Value::as_bool)
            .ok_or("desktop.hyprland.hypr3d.enabled must be boolean")? {
            return Ok(String::new());
        }
        let path = table.get("path").and_then(Value::as_str)
            .filter(|s| Path::new(s).is_absolute() && !s.chars().any(char::is_control)
                && !Path::new(s).components().any(|c| c == std::path::Component::ParentDir))
            .ok_or("desktop.hyprland.hypr3d.path must be an absolute FHS path")?;
        let key = table.get("toggle_key").and_then(Value::as_str)
            .filter(|s| !s.trim().is_empty() && !s.chars().any(char::is_control))
            .ok_or("desktop.hyprland.hypr3d.toggle_key must be a nonempty key chord")?;
        let config = table.get("config").filter(|v| v.is_table())
            .ok_or("desktop.hyprland.hypr3d.config must be a table")?;
        let _ = (path, lua(config)?); // Validate operator settings before projecting the binding.
        if let Some(player) = config.get("player") {
            let player_tbl = player.as_table().ok_or("desktop.hyprland.hypr3d.config.player must be a table")?;
            if let Some(wb) = player_tbl.get("walk_bob") {
                wb.as_bool().ok_or("desktop.hyprland.hypr3d.config.player.walk_bob must be boolean")?;
            }
        }
        // [desktop.hyprland.hypr3d.spawn] is read at SUPER+G: check it here so render-hyprland --check catches it.
        crate::hypr3d_spawn::Policy::from_ssot(data)?;
        if let Some(ctrl) = table.get("controller") {
            let ctrl_tbl = ctrl.as_table().ok_or("desktop.hyprland.hypr3d.controller must be a table")?;
            if let Some(en) = ctrl_tbl.get("enabled") {
                en.as_bool().ok_or("desktop.hyprland.hypr3d.controller.enabled must be boolean")?;
            }
            if let Some(dz) = ctrl_tbl.get("deadzone") {
                let val = dz.as_float().or_else(|| dz.as_integer().map(|i| i as f64))
                    .ok_or("desktop.hyprland.hypr3d.controller.deadzone must be a number")?;
                if !(0.0..=1.0).contains(&val) {
                    return Err("desktop.hyprland.hypr3d.controller.deadzone must be in 0.0..=1.0".into());
                }
            }
            if let Some(chord) = ctrl_tbl.get("toggle_chord") {
                chord.as_array().ok_or("desktop.hyprland.hypr3d.controller.toggle_chord must be an array")?;
            }
            for str_key in [
                "view_mode_button",
                "cursor_mode_button",
                "jump_button",
                "crouch_button",
                "fullscreen_button",
                "shaderglass_toggle_button",
                "shaderglass_cycle_button",
            ] {
                if let Some(v) = ctrl_tbl.get(str_key) {
                    v.as_str().ok_or(format!("desktop.hyprland.hypr3d.controller.{str_key} must be a string"))?;
                }
            }
            if let Some(dp) = ctrl_tbl.get("double_press_ms") {
                let val = dp.as_integer().ok_or("desktop.hyprland.hypr3d.controller.double_press_ms must be an integer")?;
                if val <= 0 {
                    return Err("desktop.hyprland.hypr3d.controller.double_press_ms must be > 0".into());
                }
            }
        }
        if framerate_policy(data)?.is_some() {
            for (group, key) in HYPR3D_RATE_KEYS {
                if config.get(group).and_then(|g| g.get(key)).is_some() {
                    return Err(format!(
                        "desktop.hyprland.hypr3d.config.{group}.{key} is superseded by [desktop.framerate] (SUPER+G and miosd send the running tier's rate); remove it"
                    ));
                }
            }
        }
        Ok(format!(
            "mios_section(\"hypr3d\", function()\n hl.unbind({})\n hl.bind({}, hl.dsp.exec_cmd(\"/usr/bin/mios-gen hyprland-3d-toggle\"))\nend)\n",
            lua(&Value::String(key.into()))?, lua(&Value::String(crate::global_bindings::key(data,"hypr3d-toggle",key)))?,
        ))
    }

    // ---- desktop frame rate ([desktop.framerate], miosd framerate) ------------------------------------------------
    // The policy table and precedence live in mios_resolver::framerate (one copy for miosd, this projection and the
    // tests). Here: the login/reload projection and the Hypr3D keys SUPER+G sends.
    use mios_resolver::framerate::{Inputs as RateInputs, Policy as RatePolicy, State as RateState};

    /// Hypr3D's rate keys (hypr3d-mios-frame-rate.patch): render.max_fps = D, render.glass_fps = G, world.live_fps = W.
    const HYPR3D_RATE_KEYS: [(&str, &str); 3] = [("render", "max_fps"), ("render", "glass_fps"), ("world", "live_fps")];

    /// The enabled policy; None when [desktop.framerate] is absent or `enabled = false` (every plugin then keeps its
    /// own default or the SSOT key written for it).
    pub fn framerate_policy(data: &Value) -> Result<Option<RatePolicy>, String> {
        Ok(RatePolicy::from_ssot(data)?.filter(|p| p.enabled))
    }

    /// `local mios_rate` for every section after it (hyprglass reads mios_rate.glass_fps) plus the compositor cap.
    /// The running tier comes from miosd's state file when its writer still runs (a config reload keeps the live rates
    /// and the modes miosd switched outputs to, so nothing bounces), else from the login plan computed here (`login`:
    /// tuned-ppd base profile, sysfs battery, SSOT unlocked; never gaming at login). The unlocked tier caps nothing and
    /// derives G and W from the local outputs' largest advertised refresh (none known yet at startup: 0 = every output
    /// frame until miosd applies).
    fn framerate_lua(data: &Value, policy: &RatePolicy, login: &RateInputs) -> Result<String, String> {
        let r = policy.plan(login, None);
        let unlocked = policy.tiers["unlocked"];
        let prefixes = Value::Array(policy.exclude_prefixes.iter().cloned().map(Value::String).collect());
        let mut seed = toml::map::Map::new();
        seed.insert("tier".into(), Value::String(r.tier.clone()));
        for (k, v) in [("desktop_hz", r.desktop_hz), ("cap_hz", r.cap_hz), ("glass_fps", r.glass_fps), ("wallpaper_fps", r.wallpaper_fps)] {
            seed.insert(k.into(), Value::Integer(i64::from(v)));
        }
        // Login: nothing paces the wallpaper yet (miosd turns the compositor's wallpaper throttle on together with the
        // state file Host.qml follows; a throttle without it would halve the wallpaper's visible rate).
        seed.insert("wallpaper_paced".into(), Value::Boolean(false));
        seed.insert("source".into(), Value::String("login".into()));
        let mut out = format!(
            "local mios_rate = {seed}\nmios_section(\"framerate\", function()\n local excluded = {prefixes}\n local function local_output(name)\n  for _, p in ipairs(excluded) do if name:sub(1, #p) == p then return false end end\n  return true\n end\n local runtime = os.getenv(\"XDG_RUNTIME_DIR\")\n local f = runtime and io.open(runtime .. {state}, \"r\")\n local s = \"\"\n if f then s = f:read(\"*a\") or \"\" f:close() end\n local pid = s:match('\"pid\"%s*:%s*(%d+)')\n local c = pid and io.open(\"/proc/\" .. pid .. \"/comm\", \"r\")\n local comm = \"\"\n if c then comm = c:read(\"*l\") or \"\" c:close() end\n local function n(k) return tonumber(s:match('\"' .. k .. '\"%s*:%s*(%d+)')) end\n local tier = s:match('\"tier\"%s*:%s*\"([%w%-]+)\"')\n if comm == \"miosd\" and tier and n(\"desktop_hz\") and n(\"cap_hz\") and n(\"glass_fps\") and n(\"wallpaper_fps\") then\n  mios_rate = {{tier = tier, desktop_hz = n(\"desktop_hz\"), cap_hz = n(\"cap_hz\"), glass_fps = n(\"glass_fps\"), wallpaper_fps = n(\"wallpaper_fps\"), wallpaper_paced = s:match('\"wallpaper_paced\"%s*:%s*true') ~= nil, source = \"state\"}}\n  for name, mode in s:gmatch('\"mode%.([%w%-_%.]+)\"%s*:%s*\"([^\"]+)\"') do\n   local ok = mode:match('^%d+x%d+@[%d%.]+$') or mode:match('^modeline [%d%. ]+ [%+%-]hsync [%+%-]vsync$')\n   local m = ok and local_output(name) and hl.get_monitor(name)\n   if m then hl.monitor({{output = name, mode = mode, position = m.x .. \"x\" .. m.y, scale = m.scale, transform = m.transform}}) end\n  end\n elseif mios_rate.cap_hz == 0 then\n  local max = 0\n  for _, m in ipairs(hl.get_monitors() or {{}}) do\n   if local_output(m.name) then\n    for _, mode in ipairs(m.available_modes or {{}}) do max = math.max(max, math.floor(mode.refresh_rate + 0.5)) end\n   end\n  end\n  mios_rate.desktop_hz = max\n  mios_rate.glass_fps = math.floor(max / {gdiv})\n  mios_rate.wallpaper_fps = math.floor(max / {wdiv})\n end\n mios_state.framerate = mios_rate\nend)\n",
            seed = lua(&Value::Table(seed))?,
            prefixes = lua(&prefixes)?,
            state = lua(&Value::String(format!("/{}", policy.state_file)))?,
            gdiv = unlocked.glass_divisor,
            wdiv = unlocked.wallpaper_divisor,
        );
        // The compositor cap (mios-pacing plugin): mios_rate.cap_hz on every local output with active VRR (0 = none).
        let h = at(data, "desktop.hyprland")?;
        if let Some(p) = h.get(&policy.pacing_plugin).filter(|p| p.get("enabled").and_then(Value::as_bool) == Some(true)) {
            let path = p
                .get("path")
                .and_then(Value::as_str)
                .filter(|s| Path::new(s).is_absolute() && !s.chars().any(char::is_control)
                    && !Path::new(s).components().any(|c| c == std::path::Component::ParentDir))
                .ok_or_else(|| format!("desktop.hyprland.{}.path must be an absolute FHS path", policy.pacing_plugin))?;
            let mut statics = toml::map::Map::new();
            statics.insert("mios_pacing".into(), Value::Table(pacing_config(p, &policy.pacing_plugin)?));
            out += &format!(
                "mios_section({}, function()\nhl.plugin.load({})\nhl.config({{[\"plugin\"] = {}}})\nhl.config({{[\"plugin\"] = {{[\"mios_pacing\"] = {{[\"max_fps\"] = mios_rate.cap_hz, [\"exclude_prefixes\"] = {}, [\"wallpaper_fps\"] = (mios_rate.wallpaper_paced and mios_rate.wallpaper_fps or 0)}}}}}})\nend)\n",
                lua(&Value::String(policy.pacing_plugin.clone()))?,
                lua(&Value::String(path.into()))?,
                lua(&Value::Table(statics))?,
                lua(&Value::String(policy.exclude_prefixes.join(",")))?,
            );
        }
        Ok(out)
    }

    /// `[desktop.hyprland.<pacing>.config]`: the plugin's static options, validated. The rate keys (max_fps,
    /// exclude_prefixes, wallpaper_fps) belong to the running tier and are refused here.
    fn pacing_config(plugin: &Value, name: &str) -> Result<toml::map::Map<String, Value>, String> {
        let Some(cfg) = plugin.get("config") else { return Ok(Default::default()) };
        let cfg = cfg.as_table().ok_or_else(|| format!("desktop.hyprland.{name}.config must be a table"))?;
        let mut out = toml::map::Map::new();
        for (k, v) in cfg {
            let ok = match k.as_str() {
                "enabled" | "require_vrr" => v.is_bool(),
                "slack_us" => v.as_integer().is_some_and(|n| (0..=20_000).contains(&n)),
                "wallpaper_namespaces" => v.as_str().is_some_and(|s| {
                    s.split(',').map(str::trim).all(|n| n.is_empty() || n.bytes().all(|b| b.is_ascii_alphanumeric() || b"._-".contains(&b)))
                }),
                "max_fps" | "exclude_prefixes" | "wallpaper_fps" => {
                    return Err(format!("desktop.hyprland.{name}.config.{k} is the running tier's ([desktop.framerate], set by miosd framerate); remove it"))
                }
                _ => return Err(format!("desktop.hyprland.{name}.config.{k} is not a plugin option (enabled, require_vrr, slack_us, wallpaper_namespaces)")),
            };
            if !ok {
                return Err(format!(
                    "desktop.hyprland.{name}.config.{k} is invalid (enabled/require_vrr: boolean; slack_us: 0..=20000; wallpaper_namespaces: comma-separated [A-Za-z0-9._-] layer namespaces)"
                ));
            }
            out.insert(k.clone(), v.clone());
        }
        Ok(out)
    }

    /// The running tier's Hypr3D keys into `config` (the table SUPER+G sends): miosd's live state, else the login plan.
    /// render.max_fps = D (0 = every output frame: unlocked), render.glass_fps = G, world.live_fps = W.
    fn hypr3d_rate_overlay(config: &mut Value, policy: &RatePolicy, live: Option<&RateState>, login: &RateInputs) -> Result<(), String> {
        let (desktop, cap, glass, wallpaper) = match live {
            Some(s) => (s.desktop_hz, s.cap_hz, s.glass_fps, s.wallpaper_fps),
            None => {
                let r = policy.plan(login, None);
                (r.desktop_hz, r.cap_hz, r.glass_fps, r.wallpaper_fps)
            }
        };
        let max_fps = if cap == 0 { 0 } else { desktop };
        let table = config.as_table_mut().ok_or("desktop.hyprland.hypr3d.config must be a table")?;
        for ((group, key), v) in HYPR3D_RATE_KEYS.iter().zip([max_fps, glass, wallpaper]) {
            table
                .entry(*group)
                .or_insert_with(|| Value::Table(Default::default()))
                .as_table_mut()
                .ok_or_else(|| format!("desktop.hyprland.hypr3d.config.{group} must be a table"))?
                .insert((*key).into(), Value::Integer(i64::from(v)));
        }
        Ok(())
    }

    /// `hyprland-session` exports the running hardware profile to the compositor, so every
    /// binding the compositor runs (SUPER+G among them) inherits it.
    const SESSION_PROFILE_ENV: &str = "MIOS_DESKTOP_HARDWARE_PROFILE";

    /// `[desktop.hyprland.hardware_profiles.<p>].hypr3d_render_scale`: a number in the plugin's 1..4 range.
    fn hypr3d_render_scale(value: &Value, key: &str) -> Result<f64, String> {
        let scale = match value {
            Value::Float(f) => *f,
            Value::Integer(i) => *i as f64,
            _ => f64::NAN,
        };
        if (1.0..=4.0).contains(&scale) { Ok(scale) } else { Err(format!("{key} must be a number in 1..4")) }
    }

    /// Hyprglass' fallback values (hyprglass src/BuiltInPresets.hpp): the theme defaults for the toned keys and
    /// the global defaults for the rest, and the built-in `glass` preset presets usually inherit from.
    const HYPRGLASS_DARK_DEFAULTS: &[(&str, f64)] = &[("brightness", 0.82), ("contrast", 0.90), ("saturation", 0.80),
        ("vibrancy", 0.15), ("vibrancy_darkness", 0.0), ("adaptive_dim", 0.4), ("adaptive_boost", 0.0)];
    const HYPRGLASS_LIGHT_DEFAULTS: &[(&str, f64)] = &[("brightness", 1.12), ("contrast", 0.92), ("saturation", 0.85),
        ("vibrancy", 0.12), ("vibrancy_darkness", 0.0), ("adaptive_dim", 0.0), ("adaptive_boost", 0.4)];
    const HYPRGLASS_GLOBAL_DEFAULTS: &[(&str, f64)] = &[("blur_strength", 2.0), ("blur_iterations", 3.0),
        ("refraction_strength", 0.6), ("chromatic_aberration", 0.5), ("fresnel_strength", 0.6), ("specular_strength", 0.8),
        ("glass_opacity", 1.0), ("edge_thickness", 0.06), ("tint_color", 2_291_772_962.0 /* 0x8899aa22 */),
        ("lens_distortion", 0.5), ("refraction_flow", 0.0), ("refraction_spread", 1.0), ("fresnel_tint", 0.0),
        ("bevel_strength", 0.0), ("bevel_size", 6.0), ("fresnel_color", 4_294_967_040.0 /* 0xffffff00 */),
        ("bevel_color", 4_294_967_040.0), ("bevel_tint", 0.0), ("bevel_angle", 315.0), ("bevel_shadow", 0.0),
        ("specular_angle", 0.0)];
    const HYPRGLASS_BUILTIN_GLASS: &[(&str, f64)] = &[("blur_strength", 1.0), ("blur_iterations", 2.0),
        ("lens_distortion", 0.3), ("refraction_strength", 8.0), ("chromatic_aberration", 0.5), ("fresnel_strength", 0.4),
        ("specular_strength", 0.8), ("glass_opacity", 1.0), ("edge_thickness", 0.06), ("tint_color", 4_294_967_040.0)];
    /// Hypr3D's glass parameters (hypr3d GLScene::GLASS_PARAMETERS keys) that come from a hyprglass preset.
    const HYPR3D_GLASS_KEYS: &[&str] = &["refraction_strength", "chromatic_aberration", "fresnel_strength",
        "specular_strength", "glass_opacity", "edge_thickness", "lens_distortion", "brightness", "contrast", "saturation",
        "vibrancy", "vibrancy_darkness", "adaptive_dim", "adaptive_boost", "refraction_flow", "refraction_spread",
        "fresnel_tint", "bevel_strength", "bevel_size", "bevel_tint", "bevel_angle", "bevel_shadow", "specular_angle"];

    /// `[desktop.hyprland.hypr3d.config].glass_preset`: the hyprglass preset `name` resolved the way hyprglass
    /// resolves it (PluginConfig.cpp resolvePreset*): the default_theme table, the preset, its `inherits` chain
    /// (SSOT presets, then hyprglass' built-in `glass`), hyprglass' config (theme table, then global), then
    /// hyprglass' defaults. Projected as Hypr3D's glass table: every material key, the tint/fresnel/bevel colours
    /// (0xRRGGBBAA), the frost (blur_sigma = 4 x blur_strength x sqrt(blur_iterations): hyprglass' kernel radius
    /// blur_strength x 12 px, sigma radius / 3, blur_iterations H+V passes) and the edge pass (edge_pass,
    /// edge_pass_width from [desktop.hyprland.hyprglass.config]).
    fn hypr3d_glass(data: &Value, name: &str) -> Result<Value, String> {
        let h = hardware_profile_table(data)?;
        let glass = h.get("hyprglass").ok_or("hypr3d glass_preset needs [desktop.hyprland.hyprglass]")?;
        let presets = glass.get("presets").and_then(Value::as_table)
            .ok_or("hypr3d glass_preset needs [desktop.hyprland.hyprglass.presets]")?;
        if !presets.contains_key(name) {
            return Err(format!("hypr3d glass_preset: hyprglass preset {name} not found"));
        }
        let config = glass_palette(glass.get("config").unwrap_or(&Value::Table(Default::default())), data)?;
        let theme = config.get("default_theme").and_then(Value::as_str).unwrap_or("dark");
        let dark = theme != "light";
        let number = |v: &Value| v.as_float().or_else(|| v.as_integer().map(|i| i as f64));
        // the inherits chain, preset tables palette-resolved (SSOT presets; anything else is a built-in)
        let mut chain: Vec<Value> = Vec::new();
        let mut builtin_glass = false;
        let mut next = Some(name.to_owned());
        while let Some(n) = next.take() {
            if chain.len() >= 8 {
                return Err(format!("hypr3d glass_preset {name}: inherits chain too deep"));
            }
            match presets.get(&n) {
                Some(p) => {
                    let p = glass_palette(p, data)?;
                    next = p.get("inherits").and_then(Value::as_str).map(str::to_owned);
                    chain.push(p);
                }
                None => builtin_glass = n == "glass",
            }
        }
        let resolve = |key: &str| -> Option<f64> {
            for p in &chain {
                if let Some(v) = p.get(theme).and_then(|t| t.get(key)).and_then(number) { return Some(v); }
                if let Some(v) = p.get(key).and_then(number) { return Some(v); }
            }
            if builtin_glass {
                let theme_value = match (dark, key) { (true, "adaptive_dim") => Some(0.3), (false, "adaptive_boost") => Some(0.3), _ => None };
                if let Some(v) = theme_value.or_else(|| HYPRGLASS_BUILTIN_GLASS.iter().find(|(k, _)| *k == key).map(|(_, v)| *v)) {
                    return Some(v);
                }
            }
            if let Some(v) = config.get(theme).and_then(|t| t.get(key)).and_then(number) { return Some(v); }
            if let Some(v) = config.get(key).and_then(number) { return Some(v); }
            let toned = if dark { HYPRGLASS_DARK_DEFAULTS } else { HYPRGLASS_LIGHT_DEFAULTS };
            toned.iter().chain(HYPRGLASS_GLOBAL_DEFAULTS).find(|(k, _)| *k == key).map(|(_, v)| *v)
        };
        let r4 = |v: f64| Value::Float((v * 10_000.0).round() / 10_000.0);
        let mut out = toml::map::Map::new();
        for key in HYPR3D_GLASS_KEYS {
            let v = resolve(key).ok_or_else(|| format!("hypr3d glass_preset {name}: no value for {key}"))?;
            out.insert((*key).into(), r4(v));
        }
        for key in ["tint_color", "fresnel_color", "bevel_color"] {
            let v = resolve(key).ok_or_else(|| format!("hypr3d glass_preset {name}: no value for {key}"))?;
            out.insert(key.into(), Value::Integer((v as i64) & 0xFFFF_FFFF));
        }
        let strength = resolve("blur_strength").unwrap_or(2.0).max(0.0);
        let iterations = resolve("blur_iterations").unwrap_or(3.0).clamp(1.0, 5.0);
        out.insert("blur_sigma".into(), r4(4.0 * strength * iterations.sqrt()));
        let edge = |key: &str, d: f64| config.get(key).and_then(number).unwrap_or(d);
        out.insert("edge_pass".into(), r4(edge("edge_pass", 0.0).clamp(0.0, 4.0)));
        out.insert("edge_pass_width".into(), r4(edge("edge_pass_width", 0.0).clamp(0.0, 256.0)));
        Ok(Value::Table(out))
    }

    /// The Hypr3D config for the running session. The active hardware profile is the one the
    /// session exported, else the kernel's `mios.desktop.gpu=`, else the SSOT default. Its
    /// `hypr3d_render_scale` replaces `config.render.scale`; an unknown profile, or a profile
    /// without the key, keeps the global value. An invalid value is an error.
    fn hypr3d_session_config(data: &Value, session: Option<&str>, cmdline: &str) -> Result<Value, String> {
        let h = hardware_profile_table(data)?;
        let mut config = h.get("hypr3d").and_then(|p| p.get("config")).filter(|v| v.is_table()).cloned()
            .ok_or("desktop.hyprland.hypr3d.config must be a table")?;
        // glass_preset = "<name>": the room's glass IS the desktop's (one glass look, MiOS rule): the named
        // [desktop.hyprland.hyprglass.presets] entry resolved as hyprglass resolves it, projected into the
        // plugin's glass table at toggle time (no hand-tuned copy). Explicit config.glass keys still win.
        if let Some(name) = config.as_table_mut().expect("checked table").remove("glass_preset") {
            let name = name.as_str().ok_or("desktop.hyprland.hypr3d.config.glass_preset must name a hyprglass preset")?;
            let mut glass = hypr3d_glass(data, name)?;
            if let Some(explicit) = config.get("glass") {
                let explicit = explicit.as_table().ok_or("desktop.hyprland.hypr3d.config.glass must be a table")?;
                let table = glass.as_table_mut().expect("table");
                for (k, v) in explicit {
                    table.insert(k.clone(), v.clone());
                }
            }
            config.as_table_mut().expect("checked table").insert("glass".into(), glass);
        }
        let profile = match profile_name(data, session.map(str::trim).filter(|s| !s.is_empty()), cmdline) {
            Ok(profile) => profile,
            Err(e) => {
                eprintln!("Hypr3DGlass: {e}; using the global render.scale");
                return Ok(config);
            }
        };
        let Some(value) = h.get("hardware_profiles").and_then(|p| p.get(&profile))
            .and_then(|p| p.get("hypr3d_render_scale")) else { return Ok(config) };
        let scale = hypr3d_render_scale(value,
            &format!("desktop.hyprland.hardware_profiles.{profile}.hypr3d_render_scale"))?;
        config.as_table_mut().expect("checked table").entry("render")
            .or_insert_with(|| Value::Table(Default::default()))
            .as_table_mut().ok_or("desktop.hyprland.hypr3d.config.render must be a table")?
            .insert("scale".into(), Value::Float(scale));
        Ok(config)
    }

    pub fn toggle_3d(root: &Path) -> Result<String, String> {
        use std::process::Command;
        let data = hardware_profile_data(root)?;
        if hypr3d_profile(&data)?.is_empty() { return Err("Hypr3DGlass is disabled".into()); }
        let mut config = hypr3d_session_config(&data, std::env::var(SESSION_PROFILE_ENV).ok().as_deref(),
            &fs::read_to_string("/proc/cmdline").unwrap_or_default())?;
        // [desktop.framerate]: the running tier's rates, so SUPER+G never resets what miosd applied.
        if let Some(policy) = framerate_policy(&data)? {
            let runtime = std::env::var_os("XDG_RUNTIME_DIR").map(std::path::PathBuf::from);
            let live = runtime.as_deref().and_then(|r| RateState::read_live(&policy.state_path(r), Path::new("/")));
            hypr3d_rate_overlay(&mut config, &policy, live.as_ref(), &mios_resolver::framerate::login_inputs(root))?;
        }
        // [desktop.hyprland.hypr3d.spawn]: Retina window opening, planned for the room's output (hyprctl monitors:
        // mode, scale, transform, reserved bars; the [desktop.mobile] verdict). Explicit [config.windows] keys win.
        if crate::hypr3d_spawn::Policy::from_ssot(&data)?.is_some() {
            match Command::new("/usr/bin/hyprctl").args(["-j", "monitors"]).output() {
                Ok(out) if out.status.success() => {
                    let monitors: serde_json::Value = serde_json::from_slice(&out.stdout).unwrap_or(serde_json::Value::Null);
                    let published: Option<serde_json::Value> = std::env::var_os("XDG_RUNTIME_DIR")
                        .and_then(|r| fs::read(Path::new(&r).join("mios-mobile-outputs.json")).ok())
                        .and_then(|b| serde_json::from_slice(&b).ok());
                    if crate::hypr3d_spawn::overlay(&data, &mut config, &monitors, published.as_ref())?.is_none() {
                        eprintln!("Hypr3DGlass: no target output in the monitor list; window placement keeps the plugin defaults");
                    }
                }
                _ => eprintln!("Hypr3DGlass: no monitor list from the compositor; window placement keeps the plugin defaults"),
            }
        }
        let plugin = &data["desktop"]["hyprland"]["hypr3d"];
        let path = plugin["path"].as_str().ok_or("Missing 3D engine path")?;
        let ipc = |args: &[&str]| Command::new("/usr/bin/hyprctl").args(args).output()
            .map_err(|e| format!("Compositor IPC failed: {e}"));
        let probe = "assert(hl.plugin.hypr3d, 'Hypr3DGlass is not loaded')";
        if !ipc(&["eval", probe])?.status.success() {
            let loaded = ipc(&["plugin", "load", path])?;
            // Another concurrent toggle may have completed the same load.
            if !loaded.status.success() && !ipc(&["eval",probe])?.status.success() {
                return Err(format!("Cannot load Hypr3DGlass: {}{}",
                    String::from_utf8_lossy(&loaded.stdout), String::from_utf8_lossy(&loaded.stderr)));
            }
        }
        let expr = format!("(function() local p=assert(hl.plugin.hypr3d); p.config({}); p.toggle(); return p.active() end)()",
            lua(&config)?);
        let result = ipc(&["eval", &expr])?;
        if !result.status.success() {
            return Err(format!("Cannot toggle Hypr3DGlass: {}{}",
                String::from_utf8_lossy(&result.stdout), String::from_utf8_lossy(&result.stderr)));
        }
        Ok(String::from_utf8_lossy(&result.stdout).trim().into())
    }

    fn hardware_profile_data(root: &Path) -> Result<Value,String> {
        if root == Path::new("/") {
            crate::terminal::tmux_theme::tmux_runtime::load_layered(root)
        } else {
            fs::read_to_string(root.join("usr/share/mios/mios.toml"))
                .map_err(|e| e.to_string())?.parse::<Value>().map_err(|e| e.to_string())
        }
    }

    fn hardware_profile_table(data: &Value) -> Result<&Value,String> {
        data.get("desktop").and_then(|d| d.get("hyprland"))
            .filter(|h| h.is_table()).ok_or_else(|| "Missing or invalid desktop.hyprland table".into())
    }

    pub(super) fn profile_name(data: &Value, requested: Option<&str>, cmdline: &str) -> Result<String, String> {
        let h = hardware_profile_table(data)?;
        let name = requested.or_else(|| cmdline.split_whitespace()
            .find_map(|arg| arg.strip_prefix("mios.desktop.gpu=")))
            .or_else(|| h.get("hardware_profile").and_then(Value::as_str))
            .unwrap_or("igpu");
        if name.is_empty() || !name.bytes().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == b'-') {
            return Err("Invalid desktop hardware profile".into());
        }
        if (name != "igpu" && h.get("hardware_profiles").is_none()) ||
            h.get("hardware_profiles").is_some_and(|p| p.get(name).is_none()) {
            return Err(format!("Unknown desktop hardware profile: {name}"));
        }
        Ok(name.into())
    }

    fn order_session_cards(
        profile: &str, cards: &[(PathBuf, String)], pci: Option<&str>,
        alias: Option<&Path>, include_others: bool,
    ) -> Result<Vec<PathBuf>, String> {
        let selected = if let Some(pci) = pci.filter(|p| !p.is_empty()) {
            if pci.len() != 12 || !pci.bytes().enumerate().all(|(i,c)| match i {
                4 | 7 => c == b':', 10 => c == b'.', _ => c.is_ascii_hexdigit(),
            }) {
                return Err("Invalid GPU PCI identity".into());
            }
            cards.iter().find(|(_, id)| id == pci).map(|(p,_)| p.clone())
        } else {
            alias.and_then(|alias| cards.iter().find(|(p,_)| p == alias).map(|(p,_)| p.clone()))
        }.ok_or_else(|| format!(
            "Hardware profile {profile} has no matching DRM device; select its native-driver boot profile first. A GPU assigned to a VM cannot render this desktop."
        ))?;
        let mut ordered = vec![selected.clone()];
        if include_others {
            ordered.extend(cards.iter().filter(|(p,_)| p != &selected).map(|(p,_)| p.clone()));
        }
        Ok(ordered)
    }

    fn session_entries(data: &Value) -> Result<Vec<(String,String)>, String> {
        let Some(profiles) = hardware_profile_table(data)?.get("hardware_profiles") else { return Ok(vec![]) };
        let profiles = profiles.as_table().ok_or("hardware_profiles must be a table")?;
        let mut entries = Vec::new();
        let mut files = std::collections::BTreeSet::new();
        for (id, cfg) in profiles {
            profile_name(data, Some(id), "")?;
            let file = cfg.get("session_file").and_then(Value::as_str).ok_or("hardware profile needs session_file")?;
            if !file.ends_with(".desktop") || file.contains('/') || file.contains("..") ||
                !file.bytes().all(|c| c.is_ascii_alphanumeric() || b"-_.".contains(&c)) ||
                !files.insert(file.to_string()) {
                return Err("Invalid or duplicate hardware profile session_file".into());
            }
            let name = cfg.get("name").and_then(Value::as_str).ok_or("hardware profile needs name")?;
            if name.is_empty() || name.chars().any(char::is_control) {
                return Err("Invalid desktop session name".into());
            }
            // Read at SUPER+G, not at login: check it here so render-hyprland --check catches it.
            if let Some(scale) = cfg.get("hypr3d_render_scale") {
                hypr3d_render_scale(scale, &format!("desktop.hyprland.hardware_profiles.{id}.hypr3d_render_scale"))?;
            }
            entries.push((format!("usr/share/wayland-sessions/{file}"), format!(
                "# AI-hint: Generated MiOS desktop session; hardware profile is the only session difference.\n# AI-related: /usr/share/mios/mios.toml, /usr/share/mios/hyprland/session.lua\n[Desktop Entry]\nName={name}\nComment=MiOS Hyprland and Quickshell; {id} hardware profile\nExec=/usr/bin/mios-gen hyprland-session --hardware-profile {id}\nTryExec=/usr/bin/mios-gen\nType=Application\nDesktopNames=Hyprland\n"
            )));
        }
        Ok(entries)
    }

    fn boot_entries(data: &Value, source: &str) -> Result<Vec<(String, String)>, String> {
        // Existing BLS type-1 hosts retain their kernel/initrd/root options. Signed UKIs
        // must be rebuilt through the image pipeline; never mutate their signed command line.
        if source.lines().any(|l| l.trim_start().starts_with("efi ")) ||
            source.lines().filter(|l| l.starts_with("title ")).count() != 1 ||
            source.lines().filter(|l| l.starts_with("linux ")).count() != 1 ||
            source.lines().filter(|l| l.starts_with("options ")).count() != 1 ||
            !source.lines().any(|l| l.starts_with("initrd ")) {
            return Err("Boot profiles require an existing BLS type-1 linux/initrd entry; signed UKIs use the image build pipeline".into());
        }
        let h = hardware_profile_table(data)?;
        let profiles = h.get("hardware_profiles").and_then(Value::as_table).ok_or("Missing desktop hardware profiles")?;
        let options = source.lines().find_map(|l| l.strip_prefix("options ")).unwrap();
        let mut entries = Vec::new();
        for (id,cfg) in profiles {
            profile_name(data,Some(id),"")?;
            let title = cfg.get("boot_title").and_then(Value::as_str).ok_or("hardware profile needs boot_title")?;
            if title.is_empty() || title.chars().any(char::is_control) { return Err("Invalid boot profile title".into()); }
            let args = |key: &str| -> Result<Vec<&str>,String> {
                cfg.get(key).and_then(Value::as_array).ok_or_else(|| format!("hardware profile needs {key}"))?
                    .iter().map(|v| v.as_str().filter(|s| !s.is_empty() && !s.chars().any(char::is_whitespace) && !s.chars().any(char::is_control))
                        .ok_or_else(|| format!("Invalid hardware boot argument in {key}"))).collect()
            };
            let remove = args("boot_remove")?;
            let append = args("boot_append")?;
            let mut options: Vec<&str> = options.split_whitespace().filter(|arg| !remove.iter().any(|rule|
                if rule.ends_with('=') { arg.starts_with(rule) } else { arg == rule }
            )).collect();
            options.extend(append);
            let mut out = "# AI-hint: Generated MiOS desktop hardware boot profile; kernel and initrd retained from the selected host entry.\n".to_string();
            for line in source.lines() {
                if line.starts_with("title ") { out += &format!("title {title}\n"); }
                else if line.starts_with("options ") { out += &format!("options {}\n",options.join(" ")); }
                else { out += line; out.push('\n'); }
            }
            entries.push((format!("boot/loader/entries/mios-desktop-{id}.conf"),out));
        }
        Ok(entries)
    }

    pub fn run_boot_profiles(root: &Path, entry: &Path, check: bool) -> Result<(String,i32),(String,i32)> {
        let result = (|| -> Result<String,String> {
            let data = hardware_profile_data(root)?;
            let source = fs::read_to_string(entry).map_err(|e| e.to_string())?;
            for (path,content) in boot_entries(&data,&source)? {
                let target = root.join(&path);
                if check {
                    if fs::read_to_string(&target).map_err(|e| format!("{path}: {e}"))? != content {
                        return Err(format!("Desktop hardware boot profile drift: {path}"));
                    }
                } else {
                    if let Ok(old) = fs::read_to_string(&target) {
                        if !old.starts_with("# AI-hint: Generated MiOS desktop hardware boot profile;") {
                            return Err(format!("Refusing to replace an unowned boot entry: {path}"));
                        }
                    }
                    write_atomic(&target,&content).map_err(|e| format!("{path}: {e}"))?;
                }
            }
            Ok("[render-hyprland] Desktop hardware boot profiles match SSOT\n".into())
        })();
        result.map(|s| (s,0)).map_err(|s| (s,1))
    }

    /// Start the canonical desktop with only the selected hardware device order changed.
    #[cfg(target_os = "linux")]
    pub fn session(root: &Path, hardware_profile: Option<&str>, dry_run: bool) -> Result<String, String> {
        use std::{env, os::unix::{fs::FileTypeExt, process::CommandExt}, process::Command};
        let data = hardware_profile_data(root)?;
        let h = hardware_profile_table(&data)?;
        let cmdline = fs::read_to_string("/proc/cmdline").unwrap_or_default();
        let profile = profile_name(&data, hardware_profile, &cmdline)?;
        if !dry_run && root == Path::new("/") && super::desktop_boot::enabled(&data)? {
            let current = super::desktop_boot::boot_profile(&data, &cmdline)?;
            // This operation only changes managed boot choices. Device ownership
            // changes at boot, before a compositor opens either DRM device.
            if profile != current {
                let status = Command::new("/usr/bin/sudo")
                    .args(["-n", "--", "/usr/bin/mios-gen", "hyprland-boot-profile",
                        "--hardware-profile", &profile])
                    .status().map_err(|e| format!("GPU boot selection failed: {e}"))?;
                if !status.success() {
                    return Err("GPU boot selection requires administrator authorization; the desktop was not started".into());
                }
                return Ok(format!("Rebooting into the {profile} desktop hardware profile\n"));
            }
        }
        let cfg = h.get("hardware_profiles").and_then(|p| p.get(&profile));
        let mut cards = fs::read_dir("/dev/dri").map_err(|e| e.to_string())?
            .filter_map(Result::ok).map(|e| e.path())
            .filter(|p| p.file_name().and_then(|s| s.to_str()).is_some_and(|s| {
                s.strip_prefix("card").is_some_and(|s| !s.is_empty() && s.bytes().all(|c| c.is_ascii_digit()))
            }))
            .filter(|p| p.metadata().is_ok_and(|m| m.file_type().is_char_device()))
            .map(|p| {
                let pci = fs::canonicalize(Path::new("/sys/class/drm").join(p.file_name().unwrap()).join("device"))
                    .ok().and_then(|p| p.file_name().and_then(|s| s.to_str()).map(str::to_string)).unwrap_or_default();
                (p,pci)
            }).collect::<Vec<_>>();
        cards.sort();
        let identity_file = cfg.and_then(|c| c.get("pci_file")).and_then(Value::as_str)
            .unwrap_or("/etc/mios/hyprland/igpu-pci");
        if !identity_file.starts_with('/') || Path::new(identity_file).components().any(|c| c == std::path::Component::ParentDir) {
            return Err("GPU identity file must be an absolute FHS path".into());
        }
        let identity = cfg.and_then(|c| c.get("pci")).and_then(Value::as_str)
            .filter(|s| !s.is_empty()).map(str::to_string)
            .or_else(|| fs::read_to_string(root.join(identity_file.trim_start_matches('/'))).ok().map(|s| s.trim().to_string()));
        let alias = cfg.and_then(|c| c.get("device")).and_then(Value::as_str)
            .or_else(|| h.get("environment").and_then(|e| e.get("AQ_DRM_DEVICES")).and_then(Value::as_str))
            .and_then(|s| fs::canonicalize(s).ok());
        let ordered = order_session_cards(&profile, &cards, identity.as_deref(), alias.as_deref(),
            cfg.and_then(|c| c.get("include_other_devices")).and_then(Value::as_bool).unwrap_or(false))?;
        let selected = ordered[0].clone();
        let devices = ordered.iter().map(|p| p.to_string_lossy()).collect::<Vec<_>>().join(":");
        if dry_run {
            return serde_json::to_string_pretty(&serde_json::json!({
                "hardware_profile": profile, "devices": ordered,
                "compositor": "start-hyprland", "config": "/usr/share/mios/hyprland/session.lua",
                "desktop_target": "mios-desktop.target"
            })).map_err(|e| e.to_string());
        }
        let runtime = env::var("XDG_RUNTIME_DIR").map_err(|_| "A logind session is required")?;
        let state = env::var_os("XDG_STATE_HOME")
            .map(PathBuf::from)
            .or_else(|| env::var_os("HOME").map(|p| PathBuf::from(p).join(".local/state")))
            .ok_or("No user state directory")?;
        let bridge_dir = state.join("mios/theme");
        let status = Command::new("/usr/libexec/mios/mios-sync-theme")
            .arg("--bridge-only")
            .env("MIOS_THEME_OUT_DIR", &bridge_dir)
            .status()
            .map_err(|e| e.to_string())?;
        if !status.success() {
            return Err("Shell theme projection failed".into());
        }
        let status = Command::new("/usr/libexec/mios/mios-dotfiles-render")
            .args(["apply", "rofi"])
            .status()
            .map_err(|e| e.to_string())?;
        if !status.success() {
            return Err("Launcher theme projection failed".into());
        }
        // Keep GTK/libadwaita on their supported settings/CSS path.
        let status = Command::new("/usr/libexec/mios/mios-theme-render")
            .args(["--gtk", "--qt"])
            .status()
            .map_err(|e| e.to_string())?;
        if !status.success() {
            return Err("Desktop theme projection failed".into());
        }
        for (key, value) in [
            ("color-scheme", data["desktop"]["color_scheme"].as_str()),
            ("gtk-theme", data["appearance"]["gtk_theme"].as_str()),
        ] {
            let status = Command::new("gsettings")
                .args([
                    "set",
                    "org.gnome.desktop.interface",
                    key,
                    value.ok_or("Missing desktop appearance setting")?,
                ])
                .status()
                .map_err(|e| e.to_string())?;
            if !status.success() {
                return Err(format!("Failed to activate {key}"));
            }
        }
        let mut command = Command::new("flock");
        command
            .args(["--nonblock", "--close"])
            .arg(Path::new(&runtime).join("mios-hyprland.lock"))
            .args([
                "start-hyprland",
                "--",
                "--config",
                "/usr/share/mios/hyprland/session.lua",
            ])
            .env("MIOS_THEME_BRIDGE", bridge_dir.join("theme.json"))
            .env("AQ_DRM_DEVICES", &devices)
            .env("WLR_DRM_DEVICES", &devices)
            .env("XDG_CURRENT_DESKTOP", "Hyprland")
            .env("XDG_SESSION_DESKTOP", "Hyprland")
            .env("XDG_SESSION_TYPE", "wayland");
        for key in [
            "TMUX",
            "TMUX_PANE",
            "TMUX_TMPDIR",
            "GTK_THEME",
            "__NV_PRIME_RENDER_OFFLOAD",
            "__GLX_VENDOR_LIBRARY_NAME",
            "__VK_LAYER_NV_optimus",
            "LIBGL_ALWAYS_SOFTWARE",
            "MESA_LOADER_DRIVER_OVERRIDE",
            "QT_QUICK_BACKEND",
        ] {
            command.env_remove(key);
        }
        // Drop the bulk SSOT exports (bwrap argument overflow for every flatpak) except the allowlist.
        let keep: Vec<&str> = data["desktop"]["hyprland"]["session_env_keep"]
            .as_array()
            .ok_or("desktop.hyprland.session_env_keep must be an array")?
            .iter()
            .filter_map(Value::as_str)
            .collect();
        for (key, _) in std::env::vars_os() {
            let Some(key) = key.to_str() else { continue };
            if key.starts_with("MIOS_") && key != "MIOS_THEME_BRIDGE" && !keep.contains(&key) {
                command.env_remove(key);
            }
        }
        command.env(SESSION_PROFILE_ENV, &profile);
        if let Ok(pci) = fs::canonicalize(
            Path::new("/sys/class/drm")
                .join(selected.file_name().unwrap_or_default())
                .join("device"),
        ) {
            if let Some(pci) = pci.file_name().and_then(|s| s.to_str()) {
                command.env("DRI_PRIME", format!("pci-{}", pci.replace([':', '.'], "_")));
            }
        }
        Err(format!("Failed to start the desktop: {}", command.exec()))
    }
    #[cfg(not(target_os = "linux"))]
    pub fn session(_: &Path, _: Option<&str>, _: bool) -> Result<String, String> {
        Err("Hyprland sessions require Linux".into())
    }

    /// The session entry is read at every login: a reset mid-write must leave the old file or the
    /// new one, never a truncated one. Temp file beside the target, fsync, rename, fsync the dir.
    fn write_atomic(target: &Path, content: &str) -> std::io::Result<()> {
        use std::io::Write;
        let dir = target.parent().ok_or_else(|| std::io::Error::other("no parent directory"))?;
        fs::create_dir_all(dir)?;
        let mode = fs::metadata(target).ok().map(|m| m.permissions());
        let mut tmp = tempfile::Builder::new().prefix(".mios-render-").tempfile_in(dir)?;
        tmp.write_all(content.as_bytes())?;
        tmp.as_file().sync_all()?;
        let perms = mode.unwrap_or_else(|| {
            use std::os::unix::fs::PermissionsExt;
            fs::Permissions::from_mode(0o644)
        });
        tmp.as_file().set_permissions(perms)?;
        tmp.persist(target).map_err(|e| e.error)?;
        fs::File::open(dir)?.sync_all()
    }

    pub fn run(root: &Path, check: bool, runtime: bool) -> Result<(String, i32), (String, i32)> {
        let result = (|| -> Result<String, String> {
            let data = if runtime {
                crate::terminal::tmux_theme::tmux_runtime::load_layered(root)?
            } else {
                fs::read_to_string(root.join("usr/share/mios/mios.toml"))
                    .map_err(|e| e.to_string())?
                    .parse::<Value>()
                    .map_err(|e| e.to_string())?
            };
            if runtime {
                // Login (session.lua): the host's frame-rate inputs -- tuned-ppd base profile and sysfs battery
                // under `root` -- for when miosd's state file is absent; the static file below uses the defaults.
                return project_login(&data, &mios_resolver::framerate::login_inputs(root));
            }
            let output = project(&data)?;
            for (path, content) in [
                ("usr/share/mios/hyprland/hyprland.lua", output.as_str()),
                ("etc/skel/.config/hypr/hyprland.lua", LOADER),
                ("usr/share/mios/hyprland/session.lua", LOADER),
            ] {
                let target = root.join(path);
                if check {
                    if fs::read_to_string(&target).map_err(|e| format!("{path}: {e}"))? != content {
                        return Err(format!("Hyprland SSOT drift: {path}"));
                    }
                } else {
                    write_atomic(&target, content).map_err(|e| format!("{path}: {e}"))?;
                }
            }
            for (path, content) in session_entries(&data)? {
                let target = root.join(&path);
                if check {
                    if fs::read_to_string(&target).map_err(|e| format!("{path}: {e}"))? != content {
                        return Err(format!("Desktop hardware session drift: {path}"));
                    }
                } else {
                    write_atomic(&target, &content).map_err(|e| format!("{path}: {e}"))?;
                }
            }
            Ok("[render-hyprland] Lua projection and desktop hardware sessions match SSOT\n".into())
        })();
        result.map(|msg| (msg, 0)).map_err(|e| (e, 1))
    }

    #[cfg(test)]
    mod tests {
        use super::*;
        /// A Lua interpreter to prove generated chunks parse: LuaJIT (5.1 syntax, the strictest
        /// about escapes) on the host, PUC Lua in podman-MiOS-DEV.
        fn interpreter() -> &'static str {
            ["/usr/bin/luajit", "/usr/bin/lua", "/usr/sbin/lua"]
                .into_iter()
                .find(|p| Path::new(p).is_file())
                .expect("a Lua interpreter (luajit or lua) is required for the projection parse tests")
        }
        fn run_lua(script: &str, env: &[(&str, &str)]) -> (bool, String) {
            let dir = tempfile::tempdir().unwrap();
            let path = dir.path().join("t.lua");
            fs::write(&path, script).unwrap();
            let out = std::process::Command::new(interpreter())
                .arg(&path)
                .envs(env.iter().copied())
                .output()
                .unwrap();
            (
                out.status.success(),
                format!("{}{}", String::from_utf8_lossy(&out.stdout), String::from_utf8_lossy(&out.stderr)),
            )
        }
        fn real_ssot() -> Value {
            let path =
                Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../usr/share/mios/mios.toml");
            fs::read_to_string(path).unwrap().parse().unwrap()
        }
        const NASTY: &str = "a\u{1}2\u{7f}\u{1b}[0m\t\"q\\\r\n\u{0}9 é ✓";

        #[test]
        fn desktop_profile_explicit_root_is_isolated_and_invalid_tables_return_errors() {
            let dir=tempfile::tempdir().unwrap();
            let path=dir.path().join("usr/share/mios/mios.toml");
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            fs::write(&path,"[desktop.hyprland]\nhardware_profile='fixture'\n").unwrap();
            assert_eq!(hardware_profile_data(dir.path()).unwrap()["desktop"]["hyprland"]["hardware_profile"].as_str(),Some("fixture"));
            let source="title Test\nlinux /kernel\ninitrd /initrd\noptions root=UUID=keep\n";
            for text in ["", "desktop='invalid'", "[desktop]\nhyprland='invalid'"] {
                let d: Value=text.parse().unwrap();
                assert!(profile_name(&d,None,"").is_err());
                assert!(session_entries(&d).is_err());
                assert!(boot_entries(&d,source).is_err());
            }
        }

        #[test]
        fn desktop_boot_profiles_keep_root_and_kernel_and_reject_signed_uki() {
            let d=real_ssot();
            let src="title Fedora\nversion test\nlinux /vmlinuz-test\ninitrd /initramfs-test.img $tuned_initrd\noptions root=UUID=keep ro $tuned_params amd_iommu=on rd.driver.pre=vfio-pci vfio-pci.ids=10de:2684,10de:22ba\n";
            let entries=boot_entries(&d,src).unwrap();
            assert_eq!(entries.len(),2);
            let igpu=&entries.iter().find(|(p,_)| p.ends_with("-igpu.conf")).unwrap().1;
            let dgpu=&entries.iter().find(|(p,_)| p.ends_with("-dgpu.conf")).unwrap().1;
            for output in [igpu,dgpu] {
                assert!(output.contains("linux /vmlinuz-test\n"));
                assert!(output.contains("initrd /initramfs-test.img $tuned_initrd\n"));
                assert!(output.contains("root=UUID=keep ro $tuned_params amd_iommu=on"));
            }
            assert!(igpu.contains("vfio-pci.ids=10de:2684,10de:22ba"));
            assert!(!dgpu.contains("vfio-pci.ids=10de") && !dgpu.contains("rd.driver.pre=vfio-pci"));
            assert!(dgpu.contains("vfio-pci.ids= rd.driver.blacklist=vfio-pci"));
            assert!(dgpu.contains("nvidia-drm.modeset=1"));
            assert!(!dgpu.contains("nouveau.modeset=1"));
            assert!(boot_entries(&d,"title UKI\nefi /EFI/Linux/signed.efi\noptions quiet\n").is_err());
            let mut bad=d.clone();
            bad["desktop"]["hyprland"]["hardware_profiles"]["dgpu"]["boot_append"]=Value::Array(vec![Value::String("arg\ninitrd /bad".into())]);
            assert!(boot_entries(&bad,src).is_err());
            assert!(boot_entries(&d,&format!("{src}options duplicate\n")).is_err());
        }

        #[test]
        fn desktop_hardware_profiles_share_session_and_select_devices_strictly() {
            let d = real_ssot();
            assert_eq!(profile_name(&d, None, "").unwrap(), "igpu");
            assert_eq!(profile_name(&d, None, "quiet mios.desktop.gpu=dgpu").unwrap(), "dgpu");
            assert_eq!(profile_name(&d, Some("igpu"), "mios.desktop.gpu=dgpu").unwrap(), "igpu");
            assert!(profile_name(&d, Some("missing"), "").is_err());
            let mut legacy=d.clone();
            legacy["desktop"]["hyprland"].as_table_mut().unwrap().remove("hardware_profiles");
            assert!(profile_name(&legacy, Some("dgpu"), "").is_err());
            assert_eq!(profile_name(&legacy, Some("igpu"), "").unwrap(),"igpu");
            assert!(profile_name(&d, Some("dgpu;exec"), "").is_err());
            let cards = vec![(PathBuf::from("/dev/dri/card1"),"0000:10:00.0".into()),
                (PathBuf::from("/dev/dri/card2"),"0000:01:00.0".into())];
            assert_eq!(order_session_cards("igpu",&cards,Some("0000:10:00.0"),None,false).unwrap(),
                vec![PathBuf::from("/dev/dri/card1")]);
            assert_eq!(order_session_cards("dgpu",&cards,Some("0000:01:00.0"),None,true).unwrap(),
                vec![PathBuf::from("/dev/dri/card2"),PathBuf::from("/dev/dri/card1")]);
            assert!(order_session_cards("dgpu",&cards,Some("0000:02:00.0"),Some(Path::new("/dev/dri/card1")),true).is_err());
            assert!(order_session_cards("dgpu",&cards,Some("../../bad!"),None,true).is_err());
            assert!(order_session_cards("dgpu",&cards,None,None,true).is_err());
            let entries = session_entries(&d).unwrap();
            assert_eq!(entries.len(),2);
            for (_,text) in &entries {
                assert!(text.contains("Exec=/usr/bin/mios-gen hyprland-session --hardware-profile "));
                assert!(text.contains("DesktopNames=Hyprland\n"));
            }
            let mut bad = d.clone();
            bad["desktop"]["hyprland"]["hardware_profiles"]["dgpu"]["session_file"] = Value::String("../escape.desktop".into());
            assert!(session_entries(&bad).is_err());
            bad = d.clone();
            bad["desktop"]["hyprland"]["hardware_profiles"]["dgpu"]["session_file"] = Value::String("hyprland.desktop".into());
            assert!(session_entries(&bad).is_err());
            bad = d.clone();
            bad["desktop"]["hyprland"]["hardware_profiles"]["dgpu"]["name"] = Value::String("GPU\nExec=bad".into());
            assert!(session_entries(&bad).is_err());
        }

        #[test]
        fn hypr3d_binding_dispatches_native_toggle_without_loading_during_config() {
            let chunk = hypr3d_profile(&real_ssot()).unwrap();
            let preamble = r#"
local bindings, failures = 0, 0
local bound = {}
hl = {plugin = {}, unbind = function(k) bound[k] = nil end,
 bind = function(k, dispatcher) bindings = bindings + 1; bound[k] = dispatcher end,
 dsp = {exec_cmd = function(cmd)
  assert(cmd == '/usr/bin/mios-gen hyprland-3d-toggle')
  return {command = cmd}
 end}}
hl.plugin.load = function() error('configuration must not load the live engine') end
local function mios_section(name, fn)
 assert(name == 'hypr3d')
 if not pcall(fn) then failures = failures + 1 end
end
"#;
            let script = preamble.to_owned() + &chunk + &chunk
                + "assert(bindings == 2 and failures == 0)\nassert(bound['SUPER + F12'].command == '/usr/bin/mios-gen hyprland-3d-toggle')\nio.write('desktop survives')";
            let (ok, out) = run_lua(&script, &[]);
            assert!(ok && out == "desktop survives", "{out}");
        }

        #[test]
        fn hypr3d_disabled_and_invalid_settings_are_handled() {
            let mut data = real_ssot();
            data["desktop"]["hyprland"]["hypr3d"]["enabled"] = Value::Boolean(false);
            assert!(hypr3d_profile(&data).unwrap().is_empty());
            for (key, value) in [
                ("enabled", Value::String("yes".into())),
                ("path", Value::String("../hypr3d.so".into())),
                ("toggle_key", Value::String("\n".into())),
                ("config", Value::String("bad".into())),
            ] {
                let mut bad = real_ssot();
                bad["desktop"]["hyprland"]["hypr3d"][key] = value;
                assert!(hypr3d_profile(&bad).is_err(), "accepted invalid {key}");
            }
        }

        fn hypr3d_scale(data: &Value, session: Option<&str>, cmdline: &str) -> Option<f64> {
            hypr3d_session_config(data, session, cmdline).unwrap()["render"]["scale"].as_float()
        }

        #[test]
        fn hypr3d_render_scale_follows_the_running_hardware_profile() {
            let d = real_ssot();
            // The operator's per-GPU values in the vendor SSOT.
            assert_eq!(hypr3d_scale(&d, Some("igpu"), ""), Some(1.5));
            assert_eq!(hypr3d_scale(&d, Some("dgpu"), ""), Some(2.0));
            // The session export wins over the kernel command line, which wins over the default profile.
            assert_eq!(hypr3d_scale(&d, Some("igpu"), "quiet mios.desktop.gpu=dgpu"), Some(1.5));
            assert_eq!(hypr3d_scale(&d, Some(" dgpu\n"), "mios.desktop.gpu=igpu"), Some(2.0));
            assert_eq!(hypr3d_scale(&d, None, "quiet mios.desktop.gpu=dgpu"), Some(2.0));
            assert_eq!(hypr3d_scale(&d, Some(""), "quiet mios.desktop.gpu=dgpu"), Some(2.0));
            assert_eq!(hypr3d_scale(&d, None, "quiet"), Some(1.5));
            let mut dgpu_default = d.clone();
            dgpu_default["desktop"]["hyprland"]["hardware_profile"] = Value::String("dgpu".into());
            assert_eq!(hypr3d_scale(&dgpu_default, None, "quiet"), Some(2.0));
            // Only render.scale changes (and glass_preset resolves into glass); the other settings pass through.
            let mut global = d["desktop"]["hyprland"]["hypr3d"]["config"].clone();
            let mut igpu = hypr3d_session_config(&d, Some("igpu"), "").unwrap();
            igpu["render"]["scale"] = global["render"]["scale"].clone();
            if global.as_table_mut().unwrap().remove("glass_preset").is_some() {
                assert!(igpu.as_table_mut().unwrap().remove("glass").is_some(), "glass_preset projects a glass table");
            }
            assert_eq!(igpu, global);
            assert!(lua(&hypr3d_session_config(&d, Some("igpu"), "").unwrap()).unwrap().contains("[\"scale\"] = 1.5"));
            // A render table is created when the global config has none.
            let mut bare = d.clone();
            bare["desktop"]["hyprland"]["hypr3d"]["config"] = Value::Table(Default::default());
            assert_eq!(hypr3d_scale(&bare, Some("dgpu"), ""), Some(2.0));
        }

        #[test]
        fn hypr3d_glass_preset_is_the_desktop_preset() {
            // One glass look (MiOS rule): the room's glass is the hyprglass preset the desktop draws windows with,
            // resolved like hyprglass (theme table, preset, inherits, config, defaults), never a copy.
            let mut d = real_ssot();
            d["desktop"]["hyprland"]["hypr3d"]["config"]
                .as_table_mut().unwrap().insert("glass_preset".into(), Value::String("mios".into()));
            let c = hypr3d_session_config(&d, Some("igpu"), "").unwrap();
            assert!(c.get("glass_preset").is_none(), "the preset name is resolved, not passed to the plugin");
            let g = &c["glass"];
            let preset = &d["desktop"]["hyprland"]["hyprglass"]["presets"]["mios"];
            for k in ["refraction_strength", "lens_distortion", "fresnel_strength", "specular_strength",
                      "bevel_strength", "bevel_shadow", "bevel_size", "edge_thickness", "glass_opacity"] {
                assert_eq!(g[k].as_float(), preset[k].as_float().or(preset[k].as_integer().map(|i| i as f64)), "{k}");
            }
            for k in ["brightness", "contrast", "saturation", "adaptive_dim"] {
                assert_eq!(g[k].as_float(), preset["dark"][k].as_float(), "{k}");
            }
            // tint/fresnel/bevel: palette-resolved exactly as hg.preset() receives them in hyprland.lua
            let projected = glass_palette(preset, &d).unwrap();
            for k in ["tint_color", "fresnel_color", "bevel_color"] {
                assert_eq!(g[k].as_integer(), projected["dark"][k].as_integer(), "{k}");
            }
            // frost: 4 x blur_strength x sqrt(blur_iterations), as hyprbars' pill_blur_sigma
            let sigma = 4.0 * preset["blur_strength"].as_float().unwrap() * (preset["blur_iterations"].as_integer().unwrap() as f64).sqrt();
            assert!((g["blur_sigma"].as_float().unwrap() - sigma).abs() < 1e-3);
            // edge pass from [desktop.hyprland.hyprglass.config]; keys the preset leaves out are hyprglass' defaults
            let hg = &d["desktop"]["hyprland"]["hyprglass"]["config"];
            assert_eq!(g["edge_pass"].as_float(), hg["edge_pass"].as_float().or(hg["edge_pass"].as_integer().map(|i| i as f64)));
            assert_eq!(g["edge_pass_width"].as_float(), hg["edge_pass_width"].as_float().or(hg["edge_pass_width"].as_integer().map(|i| i as f64)));
            assert_eq!(g["bevel_angle"].as_float(), Some(315.0));
            assert_eq!(g["vibrancy"].as_float(), Some(0.15));
            assert_eq!(g.as_table().unwrap().len(), HYPR3D_GLASS_KEYS.len() + 6);
            // explicit glass keys win; an unknown preset is an error
            d["desktop"]["hyprland"]["hypr3d"]["config"].as_table_mut().unwrap().insert("glass".into(),
                toml::toml! { bevel_size = 4.0 }.into());
            assert_eq!(hypr3d_session_config(&d, None, "").unwrap()["glass"]["bevel_size"].as_float(), Some(4.0));
            d["desktop"]["hyprland"]["hypr3d"]["config"]["glass_preset"] = Value::String("missing".into());
            assert!(hypr3d_session_config(&d, None, "").is_err());
            // the Lua the toggle sends carries the table
            d["desktop"]["hyprland"]["hypr3d"]["config"]["glass_preset"] = Value::String("mios".into());
            let lua_text = lua(&hypr3d_session_config(&d, None, "").unwrap()).unwrap();
            assert!(lua_text.contains("[\"glass\"] = {") && lua_text.contains("[\"blur_sigma\"] = 1.4142"), "{lua_text}");
        }

        #[test]
        fn hypr3d_render_scale_falls_back_to_the_global_value() {
            let mut d = real_ssot();
            // A global value no profile uses, so a fallback cannot pass by accident.
            d["desktop"]["hyprland"]["hypr3d"]["config"]["render"]["scale"] = Value::Float(3.25);
            assert_eq!(hypr3d_scale(&d, Some("dgpu"), ""), Some(2.0), "negative control: the profile value applies");
            for (session, cmdline) in [(Some("missing"), "mios.desktop.gpu=dgpu"), (Some("dgpu;exec"), ""),
                (None, "quiet mios.desktop.gpu=missing"), (Some("DGPU"), "")] {
                assert_eq!(hypr3d_scale(&d, session, cmdline), Some(3.25), "{session:?} {cmdline:?}");
            }
            let mut no_key = d.clone();
            no_key["desktop"]["hyprland"]["hardware_profiles"]["dgpu"].as_table_mut().unwrap().remove("hypr3d_render_scale");
            assert_eq!(hypr3d_scale(&no_key, Some("dgpu"), ""), Some(3.25));
            assert_eq!(hypr3d_scale(&no_key, Some("igpu"), ""), Some(1.5));
            let mut no_profiles = d.clone();
            no_profiles["desktop"]["hyprland"].as_table_mut().unwrap().remove("hardware_profiles");
            assert_eq!(hypr3d_scale(&no_profiles, Some("igpu"), ""), Some(3.25));
            assert_eq!(hypr3d_scale(&no_profiles, None, ""), Some(3.25));
        }

        #[test]
        fn hypr3d_render_scale_rejects_invalid_values() {
            let d = real_ssot();
            for value in [Value::String("2.0".into()), Value::Boolean(true), Value::Float(0.5), Value::Float(4.01),
                Value::Float(f64::NAN), Value::Float(f64::INFINITY), Value::Integer(0), Value::Integer(5),
                Value::Array(vec![Value::Float(2.0)])] {
                let mut bad = d.clone();
                bad["desktop"]["hyprland"]["hardware_profiles"]["dgpu"]["hypr3d_render_scale"] = value.clone();
                let err = hypr3d_session_config(&bad, Some("dgpu"), "").unwrap_err();
                assert!(err.contains("hardware_profiles.dgpu.hypr3d_render_scale"), "{value:?}: {err}");
                assert!(session_entries(&bad).is_err(), "render-hyprland accepted {value:?}");
                // Only the running profile's value is read at toggle time.
                assert_eq!(hypr3d_scale(&bad, Some("igpu"), ""), Some(1.5));
            }
            // A render value that is not a table cannot take the profile scale.
            let mut bad = d.clone();
            bad["desktop"]["hyprland"]["hypr3d"]["config"]["render"] = Value::Float(2.0);
            assert!(hypr3d_session_config(&bad, Some("dgpu"), "").is_err());
            // Negative control: the range ends and integers are accepted.
            for (value, want) in [(Value::Float(1.0), 1.0), (Value::Float(4.0), 4.0), (Value::Integer(3), 3.0)] {
                let mut ok = d.clone();
                ok["desktop"]["hyprland"]["hardware_profiles"]["dgpu"]["hypr3d_render_scale"] = value;
                assert_eq!(hypr3d_scale(&ok, Some("dgpu"), ""), Some(want));
                assert!(session_entries(&ok).is_ok());
            }
        }

        #[test]
        fn screenshot_section_projects_and_parses() {
            let data = real_ssot();
            let chunk = project(&data).unwrap();
            assert!(chunk.contains("mios_section(\"screenshot\", function()\n"), "no screenshot section");
            assert!(chunk.contains(" hl.bind(\"Print\", hl.dsp.exec_cmd(\"/usr/bin/mios-shot region\"))\n"), "Print bind");
            assert!(chunk.contains(" hl.bind(\"SUPER + SHIFT + S\", hl.dsp.exec_cmd(\"/usr/bin/mios-shot region\"))\n"));
            assert!(chunk.contains(" hl.layer_rule({[\"match\"] = {[\"namespace\"] = \"^(mios-shot-freeze)$\"}, [\"no_anim\"] = true})\n"));
            assert!(!chunk.contains("hl.permission("), "permissions are projected only under enforce_permissions");
            let dir = tempfile::tempdir().unwrap();
            let path = dir.path().join("hyprland.lua");
            fs::write(&path, &chunk).unwrap();
            let (ok, out) = run_lua(
                "local f, e = loadfile(os.getenv('CHUNK')) assert(f, e) io.write('parsed')",
                &[("CHUNK", path.to_str().unwrap())],
            );
            assert!(ok && out == "parsed", "{out}");
        }

        #[test]
        fn screenshot_section_rejects_unsafe_input_and_honours_switches() {
            let base = real_ssot();
            let with = |f: &dyn Fn(&mut Value)| {
                let mut d = base.clone();
                f(&mut d);
                project(&d)
            };
            let bad_args: Value = toml::from_str("b = [{ key = \"Print\", args = \"region; rm -rf ~\" }]").unwrap();
            assert!(with(&|d| d["desktop"]["screenshot"]["bindings"] = bad_args["b"].clone()).is_err(), "shell metacharacters in args");
            assert!(with(&|d| d["desktop"]["screenshot"]["binary"] = Value::String("mios-shot".into())).is_err(), "relative binary");
            assert!(with(&|d| d["desktop"]["screenshot"]["binary"] = Value::String("/usr/bin/x $(id)".into())).is_err());
            let bad_ns: Value = toml::from_str("n = [\"a b\"]").unwrap();
            assert!(with(&|d| d["desktop"]["screenshot"]["no_anim_namespaces"] = bad_ns["n"].clone()).is_err(), "namespace");
            let off = with(&|d| d["desktop"]["screenshot"]["enabled"] = Value::Boolean(false)).unwrap();
            assert!(!off.contains("mios_section(\"screenshot\"") && !off.contains("/usr/bin/mios-shot region"), "enabled = false projects nothing");
            let gone = with(&|d| {
                d["desktop"].as_table_mut().unwrap().remove("screenshot");
            })
            .unwrap();
            assert!(!gone.contains("mios_section(\"screenshot\""), "absent table projects nothing");
            let enforced = with(&|d| {
                let eco: Value = toml::from_str("enforce_permissions = true").unwrap();
                d["desktop"]["hyprland"]["config"].as_table_mut().unwrap().insert("ecosystem".into(), eco);
            })
            .unwrap();
            assert_eq!(enforced.matches(" hl.permission(").count(), 3, "binary, grim, XDPH");
            assert!(enforced.contains(" hl.permission(\"/usr/bin/mios-shot\", \"screencopy\", \"allow\")\n"), "{enforced}");
            assert!(with(&|d| {
                let eco: Value = toml::from_str("enforce_permissions = true").unwrap();
                d["desktop"]["hyprland"]["config"].as_table_mut().unwrap().insert("ecosystem".into(), eco);
                d["desktop"]["screenshot"]["hyprland_permission"] = Value::String("maybe".into());
            })
            .is_err());
        }

        #[test]
        fn lua_string_uses_only_portable_escapes() {
            assert_eq!(lua_string("a\u{1}2"), "\"a\\0012\"");
            assert_eq!(lua_string("\u{7f}\u{1f}"), "\"\\127\\031\"");
            assert_eq!(lua_string("é✓"), "\"é✓\"");
            let escaped = lua_string(NASTY);
            assert!(!escaped.contains("\\u"), "{escaped}");
            // Positive control: every Lua dialect reads the literal back byte for byte.
            let (ok, out) = run_lua(&format!("io.write({escaped})"), &[]);
            assert!(ok, "{out}");
            assert_eq!(out, NASTY);
            // Negative control: the old JSON escaping of the same string is rejected.
            let json = serde_json::to_string(NASTY).unwrap();
            assert!(json.contains("\\u0001"));
            let (ok, out) = run_lua(&format!("io.write({json})"), &[]);
            assert!(!ok, "JSON escapes unexpectedly parsed: {out}");
        }

        #[test]
        fn generated_chunk_with_control_bytes_parses() {
            let mut data = real_ssot();
            let rule: Value = toml::from_str(&format!(
                "r = {{ name = \"ctl\", match = {{ title = {} }}, float = true }}",
                toml::Value::String(NASTY.into())
            ))
            .unwrap();
            data["desktop"]["hyprland"]["window_rules"]
                .as_array_mut()
                .unwrap()
                .push(rule["r"].clone());
            let chunk = project(&data).unwrap();
            assert!(chunk.contains("\\001"), "control byte not escaped");
            let dir = tempfile::tempdir().unwrap();
            let path = dir.path().join("hyprland.lua");
            fs::write(&path, &chunk).unwrap();
            let (ok, out) = run_lua(
                "local f, e = loadfile(os.getenv('CHUNK')) assert(f, e) io.write('parsed')",
                &[("CHUNK", path.to_str().unwrap())],
            );
            assert!(ok && out == "parsed", "{out}");
            // Negative control: the same chunk with JSON string escapes does not parse.
            let json = chunk.replace(&lua_string(NASTY), &serde_json::to_string(NASTY).unwrap());
            assert_ne!(json, chunk);
            fs::write(&path, &json).unwrap();
            let (ok, _) = run_lua(
                "assert(loadfile(os.getenv('CHUNK')))",
                &[("CHUNK", path.to_str().unwrap())],
            );
            assert!(!ok, "JSON-escaped chunk unexpectedly parsed");
        }

        /// Runs the session entry (LOADER) under a stub `hl` with the projection command replaced
        /// by `cmd`; reports binds, hook registrations and the commands the start hooks run.
        fn run_session(cmd: &str, fail_plugin: bool) -> String {
            const STUB: &str = r#"
local function auto()
 return setmetatable({}, { __index = function(t, k) local v = auto() rawset(t, k, v) return v end, __call = function() return auto() end })
end
local binds, hooks, execs = {}, {}, {}
hl = auto()
hl.bind = function(key) table.insert(binds, key) end
hl.on = function(event, fn) table.insert(hooks, { event = event, fn = fn }) end
hl.exec_cmd = function(cmd) table.insert(execs, cmd) end
if os.getenv("FAIL_PLUGIN") == "1" then
 hl.plugin.load = function(path) error("cannot load " .. path) end
end
dofile(os.getenv("LOADER"))
for _, b in ipairs(binds) do print("bind " .. b) end
for _, h in ipairs(hooks) do
 print("on " .. h.event)
 if h.event == "hyprland.start" then h.fn() end
end
for _, c in ipairs(execs) do print("exec " .. c) end
"#;
            let entry = "/usr/bin/mios-gen render-hyprland --root / --runtime";
            assert_eq!(LOADER.matches(entry).count(), 1);
            let dir = tempfile::tempdir().unwrap();
            let loader = dir.path().join("session.lua");
            fs::write(&loader, LOADER.replace(entry, cmd)).unwrap();
            let (ok, out) = run_lua(
                STUB,
                &[
                    ("LOADER", loader.to_str().unwrap()),
                    ("XDG_RUNTIME_DIR", dir.path().to_str().unwrap()),
                    ("FAIL_PLUGIN", if fail_plugin { "1" } else { "0" }),
                ],
            );
            assert!(ok, "session entry raised: {out}");
            out
        }

        #[test]
        fn session_falls_back_to_a_minimal_desktop_when_the_projection_fails() {
            let mut data = real_ssot();
            let dir = tempfile::tempdir().unwrap();
            let chunk = dir.path().join("chunk.lua");
            // Positive control: a good projection loads in full, starts the target once, no fallback.
            fs::write(&chunk, project(&data).unwrap()).unwrap();
            let good = run_session(&format!("cat '{}'", chunk.display()), false);
            assert!(good.contains("bind CTRL + ALT + SHIFT + t"), "{good}");
            assert_eq!(good.matches("on hyprland.start").count(), 1, "{good}");
            assert_eq!(good.matches("start mios-desktop.target").count(), 1, "{good}");
            assert!(!good.contains("minimal session") && !good.contains("hyprctl notify"), "{good}");
            // Negative control: an invalid monitor scale fails the projection; the session still
            // gets terminal + launcher binds, starts mios-desktop.target and surfaces the error.
            let layer: Value = toml::from_str("[monitors.HDMI-A-1]\nscale = 9\n").unwrap();
            data["desktop"]["hyprland"]["monitors"] = layer["monitors"].clone();
            let err = project(&data).unwrap_err();
            assert!(err.contains("scale"), "{err}");
            let errfile = dir.path().join("err.txt");
            fs::write(&errfile, &err).unwrap();
            let bad = run_session(&format!("cat '{}' >&2; exit 1", errfile.display()), false);
            for key in ["bind SUPER + Q", "bind SUPER + Return", "bind SUPER + SPACE", "bind SUPER + R"] {
                assert!(bad.contains(key), "missing {key}: {bad}");
            }
            assert!(bad.contains("on hyprland.shutdown"), "{bad}");
            assert_eq!(bad.matches("start mios-desktop.target").count(), 1, "{bad}");
            assert!(bad.contains("exec hyprctl notify 3"), "{bad}");
            assert!(bad.contains("scale must be a number"), "error not surfaced: {bad}");
            // Garbage (non-Lua) output degrades the same way.
            let junk = run_session("echo '-- AI-hint: x' ; echo 'this is not lua ('", false);
            assert!(junk.contains("bind SUPER + Q") && junk.contains("start mios-desktop.target"), "{junk}");
        }

        #[test]
        fn failing_plugin_keeps_binds_and_lifecycle_hooks() {
            let data = real_ssot();
            let output = project(&data).unwrap();
            let hooks = output.find("hl.on(\"hyprland.start\"").unwrap();
            for plugin in ["hyprglass", "hyprbars", "windowshade", "mios-wobbly"] {
                let at = output
                    .find(&format!("mios_section(\"{plugin}\", function()\nhl.plugin.load("))
                    .unwrap_or_else(|| panic!("{plugin} not wrapped in mios_section"));
                assert!(hooks < at, "lifecycle hooks must precede {plugin}");
            }
            let dir = tempfile::tempdir().unwrap();
            let chunk = dir.path().join("chunk.lua");
            fs::write(&chunk, &output).unwrap();
            let out = run_session(&format!("cat '{}'", chunk.display()), true);
            // Full SSOT binds, not the fallback: the chunk itself survived the plugin failures.
            assert!(out.contains("bind CTRL + ALT + SHIFT + t"), "{out}");
            assert_eq!(out.matches("start mios-desktop.target").count(), 1, "{out}");
            assert!(out.contains("hyprglass section failed") && out.contains("cannot load"), "{out}");
            assert!(out.contains("exec hyprctl notify 3"), "{out}");
            assert!(!out.contains("minimal session"), "{out}");
        }

        /// Runs a full projection under a stub `hl` that records every window rule (sorted key=value list, nested
        /// tables in braces) and every exec bind (key + command), plus any mios_section error.
        fn record_rules(data: &Value) -> String {
            const STUB: &str = r#"
local function auto()
 return setmetatable({}, { __index = function(t, k) local v = auto() rawset(t, k, v) return v end, __call = function() return auto() end })
end
MIOS_SESSION = {}
hl = auto()
local out = {}
local function flat(t)
 local keys = {}
 for k in pairs(t) do table.insert(keys, k) end
 table.sort(keys)
 local parts = {}
 for _, k in ipairs(keys) do
  local v = t[k]
  if type(v) == "table" then table.insert(parts, k .. "={" .. flat(v) .. "}") else table.insert(parts, k .. "=" .. tostring(v)) end
 end
 return table.concat(parts, ",")
end
hl.window_rule = function(r) table.insert(out, "rule " .. tostring(r.name) .. " " .. flat(r)) return { set_enabled = function() end } end
hl.dsp.exec_cmd = function(cmd) return { cmd = cmd } end
hl.bind = function(key, d) if type(d) == "table" and rawget(d, "cmd") then table.insert(out, "bind " .. key .. " " .. d.cmd) end end
dofile(os.getenv("CHUNK"))
table.sort(out)
for _, l in ipairs(out) do print(l) end
for _, e in ipairs(MIOS_SESSION.errors or {}) do print("error " .. e) end
"#;
            let chunk = project(data).unwrap();
            let dir = tempfile::tempdir().unwrap();
            let path = dir.path().join("chunk.lua");
            fs::write(&path, &chunk).unwrap();
            let (ok, out) = run_lua(STUB, &[("CHUNK", path.to_str().unwrap())]);
            assert!(ok, "projection raised under the stub: {out}");
            out
        }

        fn rule_line(out: &str, name: &str) -> Option<String> {
            out.lines().find(|l| l.starts_with(&format!("rule {name} "))).map(str::to_owned)
        }

        fn shaderglass_enabled(mut d: Value) -> Value {
            d["desktop"]["shaderglass"]["enabled"] = Value::Boolean(true);
            d
        }

        #[test]
        fn shaderglass_rules_and_binds_project_only_while_enabled() {
            let vendor = real_ssot();
            assert_eq!(vendor["desktop"]["shaderglass"]["enabled"].as_bool(), Some(true));
            let mut disabled = vendor.clone();
            disabled["desktop"]["shaderglass"]["enabled"] = Value::Boolean(false);
            let mut missing = vendor.clone();
            missing["desktop"].as_table_mut().unwrap().remove("shaderglass");
            for d in [&disabled, &missing] {
                assert_eq!(shaderglass_lua(d).unwrap(), "");
                let chunk = project(d).unwrap();
                assert!(!chunk.contains("shaderglass"), "disabled ShaderGlass leaked into the projection");
            }
            // Positive control: enabled, one section with the four rules and both binds.
            let on = shaderglass_enabled(vendor.clone());
            assert_eq!(project(&on).unwrap().matches("mios_section(\"shaderglass\", function()").count(), 1);
            let out = record_rules(&on);
            assert!(!out.contains("error "), "{out}");
            let pane = rule_line(&out, "mios-shaderglass").unwrap_or_else(|| panic!("no pane rule: {out}"));
            for kv in [
                "float=true", "pin=true", "no_initial_focus=true", "focus_on_activate=false", "no_anim=true",
                "tag=+mios-shaderglass", "hyprbars:no_bar=true", "suppress_event=fullscreen maximize activate activatefocus",
                "match={class=^(shaderglass\\.exe)$,title=^ShaderGlass( \\(.*\\))?$}",
            ] {
                assert!(pane.contains(kv), "pane rule lacks {kv}: {pane}");
            }
            // Click-through is the tag rule's job (miosd toggles the tag), never the static rule's; captures keep it.
            assert!(!pane.contains("no_focus") && !pane.contains("no_screen_share"), "{pane}");
            let glass = rule_line(&out, "mios-shaderglass-glass").unwrap_or_else(|| panic!("no glass rule: {out}"));
            assert!(glass.contains("tag=+hyprglass_preset_mios"), "{glass}");
            let pass = rule_line(&out, "mios-shaderglass-passthrough").unwrap_or_else(|| panic!("no passthrough rule: {out}"));
            assert!(pass.contains("match={tag=mios-shaderglass-passthrough}") && pass.contains("no_focus=true"), "{pass}");
            let tools = rule_line(&out, "mios-shaderglass-tools").unwrap_or_else(|| panic!("no tools rule: {out}"));
            for kv in ["float=true", "pin=true", "center=true", "title=^(Choose Shader|Shader Parameters(: .*)?)$"] {
                assert!(tools.contains(kv), "tools rule lacks {kv}: {tools}");
            }
            assert!(out.contains("bind SUPER + G /usr/bin/mios-shaderglass toggle"), "{out}");
            assert!(out.contains("bind SUPER + SHIFT + G /usr/bin/mios-shaderglass cycle"), "{out}");
            assert!(out.contains("bind SUPER + ALT + G /usr/bin/mios-shaderglass setup"), "{out}");
            // exclude_from_captures adds no_screen_share to the pane rule only.
            let mut ex = on.clone();
            ex["desktop"]["shaderglass"]["window"]["exclude_from_captures"] = Value::Boolean(true);
            let out = record_rules(&ex);
            assert!(rule_line(&out, "mios-shaderglass").unwrap().contains("no_screen_share=true"), "{out}");
            assert!(!rule_line(&out, "mios-shaderglass-tools").unwrap().contains("no_screen_share"), "{out}");
            // Hyprglass disabled: no preset tag rule, the rest stays.
            let mut plain = on.clone();
            plain["desktop"]["hyprland"]["hyprglass"]["enabled"] = Value::Boolean(false);
            let out = record_rules(&plain);
            assert!(rule_line(&out, "mios-shaderglass-glass").is_none(), "{out}");
            assert!(rule_line(&out, "mios-shaderglass-passthrough").is_some(), "{out}");
        }

        #[test]
        fn shaderglass_projection_rejects_unsafe_or_ambiguous_values() {
            let on = shaderglass_enabled(real_ssot());
            assert!(shaderglass_lua(&on).is_ok(), "positive control");
            let s = |x: &str| Value::String(x.into());
            let window = |k: &str, v: Value| {
                let mut d = on.clone();
                d["desktop"]["shaderglass"]["window"][k] = v;
                d
            };
            let binding = |key: &str, args: &str| {
                let mut d = on.clone();
                let b: Value = toml::from_str(&format!("b = [{{ key = {:?}, args = {:?}, label = \"x\" }}]", key, args)).unwrap();
                d["desktop"]["shaderglass"]["bindings"] = b["b"].clone();
                d
            };
            let mut other_preset = on.clone();
            let mios = other_preset["desktop"]["hyprland"]["hyprglass"]["presets"]["mios"].clone();
            other_preset["desktop"]["hyprland"]["hyprglass"]["presets"].as_table_mut().unwrap().insert("other".into(), mios);
            other_preset["desktop"]["shaderglass"]["window"]["glass_preset"] = s("other");
            let mut relative = on.clone();
            relative["desktop"]["shaderglass"]["binary"] = s("mios-shaderglass");
            let cases: Vec<(Value, &str)> = vec![
                (window("class", s("shaderglass")), "anchored"),
                (window("class", s("^(shaderglass$")), "window.class"),
                (window("title", s("")), "window.title"),
                (window("pane_tag", s("bad tag")), "pane_tag"),
                (window("passthrough_tag", s("mios-shaderglass")), "differ"),
                (window("layer", s("top")), "pinned"),
                (window("suppress_event", s("fullscreen maximize activate")), "activatefocus"),
                (window("suppress_event", s("fullscreen maximize activate activatefocus bogus")), "unknown event"),
                (window("glass_preset", s("missing")), "not found"),
                (other_preset, "default_preset"),
                (window("no_anim", s("yes")), "no_anim"),
                (relative, "absolute path"),
                (binding("SUPER + SHIFT + G", "toggle;rm"), "plain"),
                (binding("SUPER + SHIFT + G", ""), "plain"),
                (binding("SUPER + F12", "toggle"), "already bound"),
                (binding("C + SUPER", "toggle"), "already bound"),
                (binding("SUPER + SHIFT + 1", "toggle"), "already bound"),
            ];
            for (d, needle) in cases {
                let e = shaderglass_lua(&d).expect_err(needle);
                assert!(e.contains(needle), "expected {needle:?} in: {e}");
                assert!(project(&d).is_err(), "project accepted what shaderglass_lua rejects ({needle})");
            }
        }

        #[test]
        fn hyprglass_live_resample_fps_reaches_the_plugin_options() {
            // The fixed SSOT keys apply only while [desktop.framerate] is off (with it on, the policy's glass rate is
            // the one source: framerate_projects_the_cap_and_glass_rate_of_each_login_tier).
            let without_policy = || {
                let mut d = real_ssot();
                d["desktop"].as_table_mut().unwrap().remove("framerate");
                d
            };
            let mut d = without_policy();
            let cfg = &mut d["desktop"]["hyprland"]["hyprglass"]["config"];
            cfg["layers"].as_table_mut().unwrap().insert("live_resample_fps".into(), Value::Integer(0));
            let windows: Value = toml::from_str("live_resample_fps = 0").unwrap();
            cfg.as_table_mut().unwrap().insert("windows".into(), windows);
            // Under a stub that maps nested tables to option paths the way hyprglass' hg.config does
            // (copyValidatedTable: plugin:hyprglass:<group>:<key>).
            const STUB: &str = r#"
local function auto()
 return setmetatable({}, { __index = function(t, k) local v = auto() rawset(t, k, v) return v end, __call = function() return auto() end })
end
MIOS_SESSION = {}
hl = auto()
local opts = {}
local function walk(t, prefix)
 for k, v in pairs(t) do
  local key = prefix .. ":" .. k
  if type(v) == "table" then walk(v, key) else opts[key] = v end
 end
end
hl.plugin.load = function() end
hl.plugin.hyprglass = setmetatable({ config = function(t) walk(t, "plugin:hyprglass") end }, { __index = function() return function() end end })
dofile(os.getenv("CHUNK"))
print("windows=" .. tostring(opts["plugin:hyprglass:windows:live_resample_fps"]))
print("layers=" .. tostring(opts["plugin:hyprglass:layers:live_resample_fps"]))
for _, e in ipairs(MIOS_SESSION.errors or {}) do print("error " .. e) end
"#;
            let run = |d: &Value| {
                let dir = tempfile::tempdir().unwrap();
                let path = dir.path().join("chunk.lua");
                fs::write(&path, project(d).unwrap()).unwrap();
                let (ok, out) = run_lua(STUB, &[("CHUNK", path.to_str().unwrap())]);
                assert!(ok, "{out}");
                out
            };
            let out = run(&d);
            assert!(out.contains("windows=0\n") && out.contains("layers=0\n") && !out.contains("error "), "{out}");
            // Negative control: without the keys the plugin keeps its own default (nothing is sent).
            let mut base = without_policy();
            base["desktop"]["hyprland"]["hyprglass"]["config"]["layers"].as_table_mut().unwrap().remove("live_resample_fps");
            base["desktop"]["hyprland"]["hyprglass"]["config"].as_table_mut().unwrap().remove("windows");
            let out = run(&base);
            assert!(out.contains("windows=nil") && out.contains("layers=nil"), "{out}");
            // Out-of-range or non-integer values fail the projection.
            for bad in [Value::Integer(-1), Value::Integer(1001), Value::Float(0.5), Value::String("0".into())] {
                let mut x = d.clone();
                x["desktop"]["hyprland"]["hyprglass"]["config"]["windows"]["live_resample_fps"] = bad.clone();
                let e = project(&x).expect_err("bad fps accepted");
                assert!(e.contains("live_resample_fps"), "{bad:?}: {e}");
            }
        }

        #[test]
        fn lua_escapes_quotes_and_rejects_unsupported_values() {
            assert_eq!(
                lua(&Value::String("a\"b\n".into())).unwrap(),
                "\"a\\\"b\\n\""
            );
            assert!(lua(&Value::Float(f64::NAN)).is_err());
        }
        #[test]
        fn curves_project_and_reject_bad_points() {
            let path =
                Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../usr/share/mios/mios.toml");
            let mut data: Value = fs::read_to_string(path).unwrap().parse().unwrap();
            data["desktop"]["hyprland"]["curves"] =
                toml::from_str::<Value>("a = [{ name = \"glassIn\", points = [[0.7, 0.0], [0.84, 0.0]] }]").unwrap()["a"].clone();
            let output = project(&data).unwrap();
            assert!(output.contains("hl.curve(\"glassIn\", {[\"points\"] = {{0.7, 0}, {0.84, 0}}, [\"type\"] = \"bezier\"})")
                || (output.contains("hl.curve(\"glassIn\"") && output.contains("{0.7, 0")), "{output}");
            data["desktop"]["hyprland"]["curves"] =
                toml::from_str::<Value>("a = [{ name = \"bad\", points = [[0.7, 0.0]] }]").unwrap()["a"].clone();
            assert!(project(&data).is_err());
        }
        #[test]
        fn per_output_monitors_project_after_the_catch_all() {
            let path =
                Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../usr/share/mios/mios.toml");
            let mut data: Value = fs::read_to_string(path).unwrap().parse().unwrap();
            // Backwards compatible: the vendor SSOT ships only the catch-all rule.
            let vendor = project(&data).unwrap();
            // (static rules only: the [desktop.framerate] section re-applies, at run time, a mode miosd switched to)
            assert_eq!(vendor.lines().filter(|l| l.starts_with("hl.monitor(")).count(), 1, "{vendor}");
            assert!(!vendor.contains("default_monitor"), "{vendor}");
            let layer: Value = toml::from_str(
                "[monitors.HDMI-A-1]\nmode = \"1920x1080@144\"\nposition = \"0x0\"\nscale = 1.25\ntransform = 0\nprimary = true\n[monitors.DP-2]\nmode = \"preferred\"\nposition = \"auto-right\"\nscale = 1\ndisabled = true\n",
            )
            .unwrap();
            data["desktop"]["hyprland"]["monitors"] = layer["monitors"].clone();
            let output = project(&data).unwrap();
            let lines: Vec<&str> = output.lines().filter(|l| l.starts_with("hl.monitor(")).collect();
            assert_eq!(lines.len(), 3, "{output}");
            assert!(lines[0].contains("[\"output\"] = \"\""), "{output}");
            let hdmi = lines
                .iter()
                .find(|l| l.contains("[\"output\"] = \"HDMI-A-1\""))
                .expect(&output);
            for field in [
                "[\"mode\"] = \"1920x1080@144\"",
                "[\"position\"] = \"0x0\"",
                "[\"scale\"] = 1.25",
                "[\"transform\"] = 0",
            ] {
                assert!(hdmi.contains(field), "{hdmi}");
            }
            assert!(output.contains("[\"output\"] = \"DP-2\""), "{output}");
            assert!(output.contains("[\"disabled\"] = true"), "{output}");
            assert!(!output.contains("primary"), "{output}");
            assert!(output.contains("[\"default_monitor\"] = \"HDMI-A-1\""), "{output}");
            for bad in [
                "[monitors.HDMI-A-1]\nmode = \"huge\"\n",
                "[monitors.HDMI-A-1]\nscale = 9\n",
                "[monitors.HDMI-A-1]\ntransform = 8\n",
                "[monitors.\"desc:LG 24\"]\nmode = \"preferred\"\n",
                "[monitors.A]\nprimary = true\n[monitors.B]\nprimary = true\n",
            ] {
                let layer: Value = toml::from_str(bad).unwrap();
                data["desktop"]["hyprland"]["monitors"] = layer["monitors"].clone();
                assert!(project(&data).is_err(), "{bad}");
            }
        }
        #[test]
        fn animation_leaves_project_and_reject_bad_leaves() {
            let path =
                Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../usr/share/mios/mios.toml");
            let mut data: Value = fs::read_to_string(path).unwrap().parse().unwrap();
            let leaves: Value = toml::from_str(
                "a = [{ leaf = \"fadeOut\", speed = 5.5, bezier = \"linear\" }, { leaf = \"windowsOut\", speed = 5.5, bezier = \"linear\", style = \"popin 100%\" }]",
            )
            .unwrap();
            data["desktop"]["hyprland"]["animations"] = leaves["a"].clone();
            let output = project(&data).unwrap();
            assert!(output.contains("hl.animation({[\"bezier\"] = \"linear\", [\"enabled\"] = true, [\"leaf\"] = \"fadeOut\", [\"speed\"] = 5.5})")
                || output.contains("[\"leaf\"] = \"fadeOut\""), "{output}");
            assert!(output.contains("popin 100%"));
            data["desktop"]["hyprland"]["animations"] = toml::from_str::<Value>("a = [{ leaf = \"fade out\", speed = 1.0 }]").unwrap()["a"].clone();
            assert!(project(&data).is_err());
            data["desktop"]["hyprland"]["animations"] = toml::from_str::<Value>("a = [{ leaf = \"fadeIn\" }]").unwrap()["a"].clone();
            assert!(project(&data).is_err());
        }
        #[test]
        fn glass_alpha_applies_without_floating_filters_and_acrylic_off_is_opaque() {
            let path =
                Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../usr/share/mios/mios.toml");
            let mut data: Value = fs::read_to_string(path).unwrap().parse().unwrap();
            data["theme"]["windows"]["opacity_pct"] = Value::Integer(73);
            let output = project(&data).unwrap();
            assert!(output.contains("[\"active_opacity\"] = 0.73"));
            assert!(output.contains("[\"fullscreen_opacity\"] = 0.73"));
            data["theme"]["acrylic"] = Value::Boolean(false);
            assert!(project(&data).unwrap().contains("[\"active_opacity\"] = 1"));
            data["theme"]["windows"]["opacity_pct"] = Value::Integer(101);
            assert!(project(&data).unwrap_err().contains("opacity_pct"));
        }
        #[test]
        fn session_lifecycle_projects_stop_and_rejects_invalid_commands() {
            let path =
                Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../usr/share/mios/mios.toml");
            let mut data: Value = fs::read_to_string(path).unwrap().parse().unwrap();
            let output = project(&data).unwrap();
            assert!(output.contains("hyprland.shutdown"));
            assert!(output.contains("systemctl --user stop mios-desktop.target"));
            data["desktop"]["hyprland"]["startup"] = Value::Array(vec![Value::Boolean(true)]);
            assert!(project(&data).unwrap_err().contains("nonempty strings"));
        }

        #[test]
        fn real_ssot_projects_and_rejects_invalid_geometry_and_dispatchers() {
            let path =
                Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../usr/share/mios/mios.toml");
            let mut data: Value = fs::read_to_string(path).unwrap().parse().unwrap();
            let output = project(&data).unwrap();
            assert!(output.contains("hl.config("));
            assert!(output.contains("CTRL + ALT + SHIFT + t"));
            assert!(!output.contains("autogenerated = true"));
            data["theme"]["edge"]["wm_border_px"] = Value::Integer(-1);
            assert!(project(&data).unwrap_err().contains("wm_border_px"));
            data["theme"]["edge"]["wm_border_px"] = Value::Integer(0);
            data["desktop"]["hyprland"]["bindings"]
                .as_array_mut()
                .unwrap()[0]["dispatcher"] = Value::String("exec_cmd);bad(".into());
            assert!(project(&data).unwrap_err().contains("dispatcher"));
        }

        // ---- [desktop.framerate] ---------------------------------------------------------------------------------

        /// Runs a projection under a stub `hl` that records what reaches the compositor for the frame rate: plugin
        /// loads, the mios_pacing options, the hyprglass live_resample_fps options and hl.monitor rules for outputs.
        /// `monitors` is the hl.get_monitors() result (Lua table source); `env` adds XDG_RUNTIME_DIR etc.
        fn record_rates(chunk: &str, monitors: &str, env: &[(&str, &str)]) -> String {
            let stub = format!(
                r#"
local function auto()
 return setmetatable({{}}, {{ __index = function(t, k) local v = auto() rawset(t, k, v) return v end, __call = function() return auto() end }})
end
MIOS_SESSION = {{}}
hl = auto()
local out = {{}}
local function walk(t, prefix)
 for k, v in pairs(t) do
  local key = prefix .. ":" .. k
  if type(v) == "table" then walk(v, key) else out[#out + 1] = key .. "=" .. tostring(v) end
 end
end
local monitors = {monitors}
hl.get_monitors = function() return monitors end
hl.get_monitor = function(name) for _, m in ipairs(monitors) do if m.name == name then return m end end end
hl.plugin.load = function(p) out[#out + 1] = "load " .. p end
hl.config = function(t) walk(t, "") end
hl.monitor = function(r) if r.output and r.output ~= "" then out[#out + 1] = "monitor " .. r.output .. " " .. tostring(r.mode) .. " " .. tostring(r.position) .. " " .. tostring(r.scale) end end
hl.plugin.hyprglass = setmetatable({{ config = function(t) walk(t, ":plugin:hyprglass") end }}, {{ __index = function() return function() end end }})
dofile(os.getenv("CHUNK"))
table.sort(out)
for _, l in ipairs(out) do print(l) end
local r = MIOS_SESSION.framerate or {{}}
print("rate " .. tostring(r.tier) .. " " .. tostring(r.desktop_hz) .. " " .. tostring(r.cap_hz) .. " " .. tostring(r.glass_fps) .. " " .. tostring(r.wallpaper_fps) .. " " .. tostring(r.source))
for _, e in ipairs(MIOS_SESSION.errors or {{}}) do print("error " .. e) end
"#
            );
            let dir = tempfile::tempdir().unwrap();
            let path = dir.path().join("chunk.lua");
            fs::write(&path, chunk).unwrap();
            let empty_rt = tempfile::tempdir().unwrap();
            let has_rt = env.iter().any(|(k, _)| *k == "XDG_RUNTIME_DIR");
            let mut all = vec![("CHUNK", path.to_str().unwrap())];
            if !has_rt {
                all.push(("XDG_RUNTIME_DIR", empty_rt.path().to_str().unwrap()));
            }
            all.extend_from_slice(env);
            let (ok, out) = run_lua(&stub, &all);
            assert!(ok, "projection raised under the stub: {out}");
            out
        }

        const DP3_144: &str = r#"{ { name = "DP-3", x = 0, y = 0, width = 1920, height = 1080, physical_width = 530, physical_height = 300, refresh_rate = 143.998, scale = 1, transform = 0, available_modes = { { width = 1920, height = 1080, refresh_rate = 143.998 }, { width = 1920, height = 1080, refresh_rate = 60.0 } } }, { name = "HEADLESS-MIOS-RDP", x = 1920, y = 0, width = 1170, height = 2532, physical_width = 0, physical_height = 0, refresh_rate = 240.0, scale = 2, transform = 0, available_modes = { { width = 1170, height = 2532, refresh_rate = 240.0 } } } }"#;

        fn framerate_ssot() -> Value {
            let d = real_ssot();
            assert!(d["desktop"].get("framerate").is_some(), "the SSOT carries [desktop.framerate] (the staged framerate edit)");
            d
        }

        fn rate_line(out: &str) -> &str {
            out.lines().find(|l| l.starts_with("rate ")).unwrap_or("")
        }

        fn inputs(profile: Option<&str>, battery: bool) -> RateInputs {
            RateInputs { gaming: false, on_battery: battery, profile: profile.map(str::to_owned) }
        }

        #[test]
        fn framerate_projects_the_cap_and_glass_rate_of_each_login_tier() {
            let d = framerate_ssot();
            let path = d["desktop"]["hyprland"]["mios-pacing"]["path"].as_str().unwrap().to_owned();
            // login inputs -> (tier, D, cap, G, W): DESIGN.md section 0 on a 144 Hz output
            for (login, tier, desk, cap, glass, wall) in [
                (inputs(Some("power-saver"), false), "power-saver", 60, 60, 20, 30),
                (inputs(Some("balanced"), false), "balanced", 60, 60, 20, 30),
                (inputs(Some("performance"), false), "performance", 90, 90, 30, 45),
                (inputs(Some("performance"), true), "battery", 60, 60, 20, 30),
                (inputs(None, false), "balanced", 60, 60, 20, 30),
            ] {
                let out = record_rates(&project_login(&d, &login).unwrap(), DP3_144, &[]);
                assert!(!out.contains("error "), "{out}");
                assert!(out.contains(&format!("load {path}\n")), "pacing plugin loaded: {out}");
                assert!(out.contains(&format!(":plugin:mios_pacing:max_fps={cap}\n")), "{tier}: {out}");
                assert!(out.contains(":plugin:mios_pacing:exclude_prefixes=HEADLESS-\n"), "{out}");
                // the SSOT's static plugin options reach it; no state file: the wallpaper keeps its timer (no throttle)
                let cfg = d["desktop"]["hyprland"]["mios-pacing"]["config"].as_table().unwrap();
                assert!(cfg.contains_key("wallpaper_namespaces"), "the staged SSOT names the wallpaper namespace");
                for (k, v) in cfg {
                    let v = v.as_str().map(str::to_owned).unwrap_or_else(|| v.to_string());
                    assert!(out.contains(&format!(":plugin:mios_pacing:{k}={v}\n")), "{k}: {out}");
                }
                assert!(out.contains(":plugin:mios_pacing:wallpaper_fps=0\n"), "{tier}: {out}");
                for group in ["layers", "windows"] {
                    assert!(out.contains(&format!(":plugin:hyprglass:{group}:live_resample_fps={glass}\n")), "{tier} {group}: {out}");
                }
                assert_eq!(rate_line(&out), format!("rate {tier} {desk} {cap} {glass} {wall} login"), "{out}");
                assert!(!out.contains("monitor DP-3"), "no state file: no mode rule");
            }
            // unlocked (SSOT/user-tier opt-in): no cap; G and W from the local outputs' largest mode (the 240 Hz
            // headless output is excluded), whatever the profile.
            let mut u = d.clone();
            u["desktop"]["framerate"]["unlocked"] = Value::Boolean(true);
            let out = record_rates(&project_login(&u, &inputs(Some("power-saver"), true)).unwrap(), DP3_144, &[]);
            assert!(out.contains(":plugin:mios_pacing:max_fps=0\n"), "{out}");
            assert!(out.contains(":plugin:hyprglass:layers:live_resample_fps=72\n"), "{out}");
            assert_eq!(rate_line(&out), "rate unlocked 144 0 72 144 login", "{out}");
            // no outputs known yet (compositor start): every output frame until miosd applies
            let out = record_rates(&project_login(&u, &RateInputs::default()).unwrap(), "{}", &[]);
            assert_eq!(rate_line(&out), "rate unlocked 0 0 0 0 login", "{out}");
        }

        #[test]
        fn framerate_reads_the_live_state_of_a_running_miosd_and_ignores_a_stale_file() {
            // (the state's wallpaper_paced moves the compositor's wallpaper throttle: on with it, off without)
            let d = framerate_ssot();
            let rt = tempfile::tempdir().unwrap();
            let state_dir = rt.path().join("mios/framerate");
            fs::create_dir_all(&state_dir).unwrap();
            // a process whose comm is "miosd" stands for the running daemon
            let bin = rt.path().join("miosd");
            fs::copy("/usr/bin/sleep", &bin).unwrap();
            let mut child = std::process::Command::new(&bin).arg("30").spawn().unwrap();
            let write = |pid: u32, paced: bool| {
                let mut outputs = std::collections::BTreeMap::new();
                outputs.insert("DP-3".to_string(), mios_resolver::framerate::OutputState {
                    mechanism: "mode".into(), cap_hz: 0, max_hz: 144, mode: Some("1920x1080@119.98".into()), refresh_hz: 119.98 });
                let s = RateState { version: mios_resolver::framerate::STATE_VERSION, pid, seq: 7, tier: "gaming".into(), desktop_hz: 120,
                    cap_hz: 120, glass_fps: 60, wallpaper_fps: 60, max_hz: 144, unlocked: false, wallpaper_paced: paced,
                    inputs: RateInputs { gaming: true, on_battery: false, profile: Some("performance".into()) }, outputs };
                fs::write(state_dir.join("state.json"), s.to_json()).unwrap();
            };
            write(child.id(), true);
            let chunk = project_login(&d, &inputs(Some("power-saver"), false)).unwrap();
            let env = [("XDG_RUNTIME_DIR", rt.path().to_str().unwrap())];
            let out = record_rates(&chunk, DP3_144, &env);
            assert_eq!(rate_line(&out), "rate gaming 120 120 60 60 state", "{out}");
            assert!(out.contains(":plugin:mios_pacing:wallpaper_fps=60\n"), "paced: the wallpaper's callbacks at W: {out}");
            write(child.id(), false);
            let out = record_rates(&chunk, DP3_144, &env);
            assert!(out.contains(":plugin:mios_pacing:wallpaper_fps=0\n"), "not paced: no throttle: {out}");
            write(child.id(), true);
            let out = record_rates(&chunk, DP3_144, &env);
            assert!(out.contains(":plugin:mios_pacing:max_fps=120\n") && out.contains(":plugin:hyprglass:windows:live_resample_fps=60\n"), "{out}");
            assert!(out.contains("monitor DP-3 1920x1080@119.98 0x0 1\n"), "the switched mode survives a reload: {out}");
            // negative control: the same file from a dead (or reused, non-miosd) pid is stale
            write(1, true);
            let out = record_rates(&chunk, DP3_144, &env);
            assert_eq!(rate_line(&out), "rate power-saver 60 60 20 30 login", "{out}");
            assert!(!out.contains("monitor DP-3"), "{out}");
            assert!(out.contains(":plugin:mios_pacing:wallpaper_fps=0\n"), "a stale file never throttles the wallpaper: {out}");
            child.kill().ok();
            child.wait().ok();
        }

        #[test]
        fn framerate_absent_or_disabled_projects_nothing_and_keeps_the_plugin_keys() {
            let mut off = framerate_ssot();
            off["desktop"]["framerate"]["enabled"] = Value::Boolean(false);
            let mut gone = framerate_ssot();
            gone["desktop"].as_table_mut().unwrap().remove("framerate");
            for d in [&off, &gone] {
                let chunk = project(d).unwrap();
                assert!(!chunk.contains("mios_section(\"framerate\"") && !chunk.contains("mios_rate") && !chunk.contains("mios_pacing"), "no frame-rate projection");
                let out = record_rates(&chunk, DP3_144, &[]);
                assert!(!out.contains("live_resample_fps") && !out.contains("mios_pacing") && !out.contains("error "), "plugin defaults: {out}");
                // the legacy SSOT keys pass through as written while the policy is off
                let mut legacy = d.clone();
                legacy["desktop"]["hyprland"]["hyprglass"]["config"]["layers"].as_table_mut().unwrap().insert("live_resample_fps".into(), Value::Integer(25));
                let out = record_rates(&project(&legacy).unwrap(), DP3_144, &[]);
                assert!(out.contains(":plugin:hyprglass:layers:live_resample_fps=25\n"), "{out}");
            }
            // pacing plugin disabled: rates still projected, no plugin load
            let mut nopace = framerate_ssot();
            nopace["desktop"]["hyprland"]["mios-pacing"]["enabled"] = Value::Boolean(false);
            let out = record_rates(&project(&nopace).unwrap(), DP3_144, &[]);
            assert!(!out.contains("mios_pacing") && out.contains(":plugin:hyprglass:layers:live_resample_fps=20\n"), "{out}");
        }

        #[test]
        fn framerate_rejects_superseded_fixed_rates_and_bad_policies() {
            let d = framerate_ssot();
            for group in ["layers", "windows"] {
                let mut x = d.clone();
                let cfg = x["desktop"]["hyprland"]["hyprglass"]["config"].as_table_mut().unwrap();
                cfg.entry(group).or_insert_with(|| Value::Table(Default::default()))
                    .as_table_mut().unwrap().insert("live_resample_fps".into(), Value::Integer(0));
                let e = project(&x).unwrap_err();
                assert!(e.contains("superseded by [desktop.framerate]") && e.contains(group), "{e}");
            }
            for (group, key) in HYPR3D_RATE_KEYS {
                let mut x = d.clone();
                x["desktop"]["hyprland"]["hypr3d"]["config"].as_table_mut().unwrap()
                    .entry(group).or_insert_with(|| Value::Table(Default::default()))
                    .as_table_mut().unwrap().insert(key.into(), Value::Integer(30));
                let e = project(&x).unwrap_err();
                assert!(e.contains(&format!("{group}.{key}")) && e.contains("superseded"), "{e}");
            }
            let mut x = d.clone();
            x["desktop"]["framerate"]["tiers"]["performance"]["desktop_hz"] = Value::Integer(0);
            assert!(project(&x).unwrap_err().contains("desktop_hz"));
            let mut x = d.clone();
            x["desktop"]["hyprland"]["mios-pacing"]["path"] = Value::String("lib/../x.so".into());
            assert!(project(&x).unwrap_err().contains("mios-pacing.path"));
            // the plugin's static options: the rate keys belong to the tier; unknown keys and bad values are refused
            for (k, v, needle) in [
                ("max_fps", Value::Integer(90), "running tier"),
                ("wallpaper_fps", Value::Integer(30), "running tier"),
                ("exclude_prefixes", Value::String("X-".into()), "running tier"),
                ("turbo", Value::Boolean(true), "not a plugin option"),
                ("slack_us", Value::Integer(-1), "invalid"),
                ("require_vrr", Value::String("yes".into()), "invalid"),
                ("wallpaper_namespaces", Value::String("mios wallpaper".into()), "invalid"),
            ] {
                let mut x = d.clone();
                x["desktop"]["hyprland"]["mios-pacing"]["config"].as_table_mut().unwrap().insert(k.into(), v);
                let e = project(&x).unwrap_err();
                assert!(e.contains(&format!("mios-pacing.config.{k}")) && e.contains(needle), "{k}: {e}");
            }
        }

        #[test]
        fn framerate_projection_parses_as_lua() {
            let d = framerate_ssot();
            let chunk = project_login(&d, &inputs(Some("performance"), false)).unwrap();
            let start = chunk.find("local mios_rate = ").expect("rate seed");
            let end = chunk[start..].find("mios_section(\"mios-pacing\"").map(|e| start + e).expect("pacing section");
            let body = &chunk[start..end];
            let dir = tempfile::tempdir().unwrap();
            let path = dir.path().join("rate.lua");
            fs::write(&path, format!("local mios_state = {{}}\nlocal function mios_section(n, f) f() end\nhl = {{ get_monitors = function() return {{}} end }}\n{body}\nprint(mios_rate.tier, mios_rate.desktop_hz, mios_rate.glass_fps)\n")).unwrap();
            let out = std::process::Command::new(interpreter()).arg(&path).env_remove("XDG_RUNTIME_DIR").output().unwrap();
            assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
            assert_eq!(String::from_utf8_lossy(&out.stdout).split_whitespace().collect::<Vec<_>>(), ["performance", "90", "30"]);
        }

        #[test]
        fn hypr3d_toggle_carries_the_running_tier() {
            let d = framerate_ssot();
            let policy = framerate_policy(&d).unwrap().unwrap();
            let base = hypr3d_session_config(&d, None, "").unwrap();
            let keys = |c: &Value| HYPR3D_RATE_KEYS.map(|(g, k)| c[g][k].as_integer().unwrap());
            let mut c = base.clone();
            hypr3d_rate_overlay(&mut c, &policy, None, &inputs(Some("performance"), false)).unwrap();
            assert_eq!(keys(&c), [90, 30, 45]);
            assert_eq!(c["render"]["scale"], base["render"]["scale"], "the rest of the config is untouched");
            let mut c = base.clone();
            hypr3d_rate_overlay(&mut c, &policy, None, &inputs(Some("balanced"), true)).unwrap();
            assert_eq!(keys(&c), [60, 20, 30]);
            let live = RateState { version: mios_resolver::framerate::STATE_VERSION, pid: 1, seq: 1, tier: "unlocked".into(), desktop_hz: 144,
                cap_hz: 0, glass_fps: 72, wallpaper_fps: 144, max_hz: 144, unlocked: true, wallpaper_paced: true, inputs: RateInputs::default(), outputs: Default::default() };
            let mut c = base.clone();
            hypr3d_rate_overlay(&mut c, &policy, Some(&live), &inputs(Some("power-saver"), false)).unwrap();
            assert_eq!(keys(&c), [0, 72, 144], "unlocked: every output frame, glass D/2, dome at D");
            // negative control: without the policy nothing is added
            let mut off = d.clone();
            off["desktop"]["framerate"]["enabled"] = Value::Boolean(false);
            assert!(framerate_policy(&off).unwrap().is_none());
            assert!(base["render"].get("max_fps").is_none() && base["world"].get("live_fps").is_none());
        }
    }
}

/// Build Hyprglass against the installed compositor SDK, including its hidden Lua ABI.
pub mod hyprglass_build {
    use std::{
        fs,
        path::{Path, PathBuf},
        process::{Command, Stdio},
    };
    use toml::Value;
    fn command(program: &str, args: &[String], cwd: &Path) -> Result<(), String> {
        let status = Command::new(program)
            .args(args)
            .current_dir(cwd)
            .status()
            .map_err(|e| format!("{program}: {e}"))?;
        if !status.success() {
            return Err(format!("{program} failed: {status}"));
        }
        Ok(())
    }
    fn output(program: &str, args: &[&str], cwd: &Path) -> Result<String, String> {
        let out = Command::new(program)
            .args(args)
            .current_dir(cwd)
            .output()
            .map_err(|e| e.to_string())?;
        if !out.status.success() {
            return Err(format!(
                "{program}: {}",
                String::from_utf8_lossy(&out.stderr)
            ));
        }
        Ok(String::from_utf8_lossy(&out.stdout).trim().to_string())
    }
    fn value<'a>(v: &'a Value, key: &str) -> Result<&'a str, String> {
        v.get(key)
            .and_then(Value::as_str)
            .filter(|s| !s.is_empty())
            .ok_or_else(|| format!("Missing plugin key {key}"))
    }
    fn find(dir: &Path, prefix: &str, suffix: &str, directory: bool) -> Result<PathBuf, String> {
        let paths = fs::read_dir(dir)
            .map_err(|e| e.to_string())?
            .filter_map(Result::ok)
            .map(|e| e.path())
            .filter(|p| p.is_dir() == directory)
            .filter(|p| {
                p.file_name()
                    .and_then(|s| s.to_str())
                    .is_some_and(|s| s.starts_with(prefix) && s.ends_with(suffix))
            })
            .collect::<Vec<_>>();
        if paths.len() != 1 {
            return Err(format!(
                "Expected one SDK {prefix}*{suffix}, found {}",
                paths.len()
            ));
        }
        Ok(paths[0].clone())
    }
    // The upstream layer path masks content alpha but computes optical
    // highlights against a square. Reuse the compositor's live SSOT radius.
    fn layer_geometry_source(source: &str) -> Result<String, String> {
        let include = "#include <hyprland/src/render/OpenGL.hpp>";
        let old = "    float cornerRadius  = 0.0f;\n    float roundingPower = 2.0f;";
        let replacement = r#"    float cornerRadius = 0.0f;
    const auto radiusOption = Config::mgr()->getConfigValue("decoration:rounding");
    if (radiusOption.dataptr && radiusOption.type && *radiusOption.type == typeid(Config::INTEGER)) {
        const auto radius = reinterpret_cast<Config::INTEGER* const*>(radiusOption.dataptr);
        cornerRadius = std::clamp(static_cast<float>(**radius) * monitor->m_scale, 0.0f,
                                 static_cast<float>(std::min(transformBox.w, transformBox.h) * 0.5));
    }
    float roundingPower = 2.0f;"#;
        if source.matches(include).count() != 1 || source.matches(old).count() != 1 {
            return Err("MiOS layer geometry fix (render.rs layer_geometry_source) no longer matches upstream src/GlassLayerSurface.cpp; update its anchor".into());
        }
        Ok(source
            .replacen(include, "#include <hyprland/src/config/ConfigManager.hpp>\n#include <hyprland/src/render/OpenGL.hpp>", 1)
            .replacen(old, replacement, 1))
    }

    #[cfg(test)]
    mod geometry_tests {
        use super::*;
        #[test]
        fn pinned_layer_fix_reads_live_radius_and_rejects_unreviewed_sources() {
            let fixture = "#include <hyprland/src/render/OpenGL.hpp>\n    float cornerRadius  = 0.0f;\n    float roundingPower = 2.0f;";
            let fixed = layer_geometry_source(fixture).unwrap();
            assert!(fixed.contains("getConfigValue(\"decoration:rounding\")"));
            assert!(fixed.contains("monitor->m_scale"));
            assert!(fixed.contains("transformBox.h) * 0.5"));
            assert!(layer_geometry_source(&fixed).is_err());
            assert!(layer_geometry_source(&fixture.replace("cornerRadius  = 0.0f", "cornerRadius  = 5.0f")).is_err());
            assert!(layer_geometry_source(&(fixture.to_string() + fixture)).is_err());
        }
    }

    /// Upstream repository of a cloned plugin. `source_revision` pins are refused: the policy is
    /// latest upstream (resolved and logged at build time), so a stale pin cannot silently hold a
    /// plugin back or be mistaken for what was built.
    fn upstream_source<'a>(p: &'a Value, plugin: &str) -> Result<&'a str, String> {
        if p.get("source_revision").is_some() {
            return Err(format!(
                "[desktop.hyprland.{plugin}] source_revision is not allowed: plugins build from upstream default-branch HEAD (remove the pin)"
            ));
        }
        value(p, "source_repository")
    }

    /// Fixed prefix the plugin SDK temp dir is mapped to (`-ffile-prefix-map`), so two builds of the
    /// same source against the same SDK are byte-identical whatever temp dir each one used.
    const PLUGIN_SDK_PREFIX: &str = "/usr/src/mios-hyprland-sdk";

    fn sdk_prefix_map(sdk: &Path) -> String {
        format!("-ffile-prefix-map={}={PLUGIN_SDK_PREFIX}", sdk.display())
    }

    fn plugin_build_system<'a>(p: &'a Value, plugin: &str) -> Result<&'a str, String> {
        match p.get("build_system") {
            None => Ok("make"),
            Some(Value::String(system)) if system == "make" || system == "cmake" => Ok(system),
            _ => Err(format!("{plugin} build_system must be make or cmake")),
        }
    }

    /// The plugin commit hyprpm.toml ([repository] commit_pins = [[hyprland_commit, plugin_commit], ...])
    /// maps `hyprland` to, None when it has no entry. A malformed hyprpm.toml fails the build.
    fn hyprpm_pin(text: &str, hyprland: &str) -> Result<Option<String>, String> {
        let doc: Value = text.parse().map_err(|e| format!("hyprpm.toml: {e}"))?;
        let Some(pins) = doc.get("repository").and_then(|r| r.get("commit_pins")) else {
            return Ok(None);
        };
        let hex = |s: &str| s.len() == 40 && s.bytes().all(|c| c.is_ascii_hexdigit());
        let mut found = None;
        for pin in pins.as_array().ok_or("hyprpm.toml commit_pins must be an array")? {
            let pair = pin
                .as_array()
                .filter(|p| p.len() == 2)
                .and_then(|p| Some((p[0].as_str()?, p[1].as_str()?)))
                .filter(|(a, b)| hex(a) && hex(b))
                .ok_or("hyprpm.toml commit_pins entries must be [hyprland_commit, plugin_commit]")?;
            if pair.0.eq_ignore_ascii_case(hyprland) {
                found = Some(pair.1.to_ascii_lowercase()); // the last entry wins, as in hyprpm
            }
        }
        Ok(found)
    }

    /// Commit of the installed Hyprland release, resolved from its v<version> tag in
    /// [desktop.hyprland.hyprglass] hyprland_repository (peeled for annotated tags).
    fn hyprland_commit(h: &Value, installed: &str, cwd: &Path) -> Result<String, String> {
        let url = value(h, "hyprland_repository")
            .map_err(|_| "[desktop.hyprland.hyprglass] hyprland_repository is needed to read a plugin's hyprpm.toml".to_string())?;
        let tag = format!("refs/tags/v{installed}");
        let listing = output("git", &["ls-remote", url, &tag, &format!("{tag}^{{}}")], cwd)?;
        let commit = |suffix: &str| {
            listing.lines().find_map(|l| {
                let (sha, name) = l.split_once('\t')?;
                (name == format!("{tag}{suffix}")).then(|| sha.to_string())
            })
        };
        commit("^{}")
            .or_else(|| commit(""))
            .ok_or_else(|| format!("Hyprland tag v{installed} not found in {url}"))
    }

    #[cfg(test)]
    mod hyprpm_tests {
        use super::hyprpm_pin;
        const HL: &str = "efb50993780079460b0cbed1363e2166a2de1d9f";
        const PIN: &str = "7644cecdb947060682891a0db2a0cdc5c0b9e704";
        #[test]
        fn hyprpm_pins_pick_the_installed_release_or_head() {
            let text = format!(
                "[repository]\nname = \"x\"\ncommit_pins = [\n  [\"36b2e0cfe0c6094dbc47bd42a437431315bb3087\", \"{PIN}\"], # 0.56.0\n  [\"{HL}\", \"{PIN}\"]  # 0.56.2\n]\n[p]\nbuild = [\"make\"]\n"
            );
            assert_eq!(hyprpm_pin(&text, HL).unwrap().as_deref(), Some(PIN));
            // a Hyprland commit with no entry builds HEAD
            assert_eq!(hyprpm_pin(&text, "0000000000000000000000000000000000000000").unwrap(), None);
            // no commit_pins at all: HEAD
            assert_eq!(hyprpm_pin("[repository]\nname = \"x\"\n", HL).unwrap(), None);
            // malformed entries fail loudly instead of silently building HEAD
            assert!(hyprpm_pin("[repository]\ncommit_pins = [[\"abc\", \"def\"]]\n", HL).is_err());
            assert!(hyprpm_pin("[repository]\ncommit_pins = \"x\"\n", HL).is_err());
            assert!(hyprpm_pin("not toml [", HL).is_err());
        }
    }

    /// Reviewed MiOS patches from the tree, applied in SSOT order. A patch that no longer applies to
    /// the upstream commit fails the build naming the patch and the commit it was tried against.
    fn apply_patches(root: &Path, repo: &Path, p: &Value, plugin: &str, revision: &str) -> Result<(), String> {
        for patch in p.get("patches").and_then(Value::as_array).into_iter().flatten() {
            let rel = patch.as_str().ok_or("patches entries must be strings")?.trim_start_matches('/');
            let file = root.join(rel);
            if !file.is_file() {
                return Err(format!("{plugin}: MiOS patch {rel} not found under {}", root.display()));
            }
            let path = file.display().to_string();
            output("git", &["apply", "--check", &path], repo).map_err(|e| {
                format!(
                    "{plugin}: MiOS patch {rel} no longer applies to upstream {revision}; rebase the patch onto upstream HEAD.\n{e}"
                )
            })?;
            output("git", &["apply", &path], repo)
                .map_err(|e| format!("{plugin}: applying MiOS patch {rel} failed: {e}"))?;
            eprintln!("[build-hyprland-plugin] {plugin}: applied {rel}");
        }
        Ok(())
    }

    #[cfg(test)]
    mod upstream_tests {
        use super::*;
        #[test]
        fn sdk_prefix_map_hides_the_temp_dir_behind_a_fixed_prefix() {
            let a = sdk_prefix_map(Path::new("/tmp/mios-hyprglass-build-AAAAAA"));
            let b = sdk_prefix_map(Path::new("/tmp/mios-hyprglass-build-BBBBBB"));
            assert_eq!(a, "-ffile-prefix-map=/tmp/mios-hyprglass-build-AAAAAA=/usr/src/mios-hyprland-sdk");
            // both builds name the same mapped prefix; only the (discarded) source side differs
            assert_eq!(a.rsplit('=').next(), b.rsplit('=').next());
            assert_ne!(a, b);
        }
        #[test]
        fn plugin_build_system_accepts_cmake_and_rejects_invalid_configuration() {
            let data: Value = toml::from_str("[default]\n[make]\nbuild_system = 'make'\n[cmake]\nbuild_system = 'cmake'\n[unknown]\nbuild_system = 'other'\n[wrong_type]\nbuild_system = 1\n").unwrap();
            assert_eq!(plugin_build_system(&data["default"], "test").unwrap(), "make");
            assert_eq!(plugin_build_system(&data["make"], "test").unwrap(), "make");
            assert_eq!(plugin_build_system(&data["cmake"], "hypr3d").unwrap(), "cmake");
            for key in ["unknown", "wrong_type"] {
                assert!(plugin_build_system(&data[key], "hypr3d").unwrap_err().contains("hypr3d build_system must be make or cmake"));
            }
        }
        fn git(dir: &Path, args: &[&str]) {
            let ok = Command::new("git")
                .args(["-c", "user.name=t", "-c", "user.email=t@t", "-c", "init.defaultBranch=main"])
                .args(args)
                .current_dir(dir)
                .status()
                .unwrap()
                .success();
            assert!(ok, "git {args:?}");
        }
        #[test]
        fn plugins_build_latest_upstream_and_refuse_pins() {
            let ssot: Value = toml::from_str(
                "[a]\nsource_repository = \"https://example.invalid/a.git\"\n[b]\nsource_repository = \"https://example.invalid/b.git\"\nsource_revision = \"0ec4118fa7f6ddda73a90a52066c8a9b2fbf844d\"\n",
            )
            .unwrap();
            assert_eq!(upstream_source(&ssot["a"], "a").unwrap(), "https://example.invalid/a.git");
            assert!(upstream_source(&ssot["b"], "b").unwrap_err().contains("source_revision is not allowed"));
            // The shipped SSOT follows the policy for every cloned plugin.
            let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../usr/share/mios/mios.toml");
            let data: Value = fs::read_to_string(path).unwrap().parse().unwrap();
            for plugin in ["hyprglass", "hyprbars", "windowshade"] {
                upstream_source(&data["desktop"]["hyprland"][plugin], plugin).unwrap();
            }
        }
        #[test]
        fn stale_patch_fails_loudly_naming_the_patch() {
            let root = tempfile::tempdir().unwrap();
            let repo = root.path().join("repo");
            fs::create_dir_all(&repo).unwrap();
            fs::write(repo.join("f.txt"), "one\ntwo\n").unwrap();
            git(&repo, &["init", "-q"]);
            git(&repo, &["add", "."]);
            git(&repo, &["commit", "-qm", "init"]);
            fs::create_dir_all(root.path().join("p")).unwrap();
            fs::write(root.path().join("p/good.patch"), "--- a/f.txt\n+++ b/f.txt\n@@ -1,2 +1,2 @@\n one\n-two\n+TWO\n").unwrap();
            fs::write(root.path().join("p/stale.patch"), "--- a/f.txt\n+++ b/f.txt\n@@ -1,2 +1,2 @@\n one\n-three\n+THREE\n").unwrap();
            let good: Value = toml::from_str("patches = [\"p/good.patch\"]").unwrap();
            apply_patches(root.path(), &repo, &good, "demo", "abc123").unwrap();
            assert_eq!(fs::read_to_string(repo.join("f.txt")).unwrap(), "one\nTWO\n");
            git(&repo, &["checkout", "-q", "--", "f.txt"]);
            let stale: Value = toml::from_str("patches = [\"p/stale.patch\"]").unwrap();
            let err = apply_patches(root.path(), &repo, &stale, "demo", "abc123").unwrap_err();
            assert!(err.contains("MiOS patch p/stale.patch no longer applies to upstream abc123"), "{err}");
            assert_eq!(fs::read_to_string(repo.join("f.txt")).unwrap(), "one\ntwo\n");
            let missing: Value = toml::from_str("patches = [\"p/none.patch\"]").unwrap();
            assert!(apply_patches(root.path(), &repo, &missing, "demo", "abc123").unwrap_err().contains("p/none.patch not found"));
        }
    }

    pub fn run(root: &Path, srpm: Option<&Path>) -> Result<String, String> {
        run_plugin(root, srpm, "hyprglass")
    }

    /// Build the compositor plugin [desktop.hyprland.<plugin>] against the installed SDK.
    /// The ABI hold (hyprland_version, lua_version, build_jobs) is [desktop.hyprland.hyprglass]'s,
    /// shared by every plugin loaded into the same compositor.
    pub fn run_plugin(root: &Path, srpm: Option<&Path>, plugin: &str) -> Result<String, String> {
        if !Path::new("/run/.containerenv").exists() {
            return Err("Run this build inside podman-MiOS-DEV".into());
        }
        if plugin.is_empty() || !plugin.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'-') {
            return Err("Invalid plugin name".into());
        }
        let data = crate::terminal::tmux_theme::tmux_runtime::load_layered(root)?;
        let h = &data["desktop"]["hyprland"]["hyprglass"];
        let p = data["desktop"]["hyprland"]
            .get(plugin)
            .ok_or_else(|| format!("No [desktop.hyprland.{plugin}] in SSOT"))?;
        if p.get("enabled").and_then(Value::as_bool) != Some(true) {
            return Ok(format!("{plugin} disabled in SSOT\n"));
        }
        let build_system = plugin_build_system(p, plugin)?;
        let installed = output("rpm", &["-q", "--qf", "%{VERSION}", "hyprland"], root)?;
        if installed != value(h, "hyprland_version")? {
            return Err(format!(
                "Hyprglass ABI hold: installed Hyprland {installed} does not match SSOT"
            ));
        }
        let work = tempfile::Builder::new()
            .prefix("mios-hyprglass-build-")
            .tempdir()
            .map_err(|e| e.to_string())?;
        let sdk = work.path();
        let release = output("rpm", &["-E", "%fedora"], root)?;
        let source = if let Some(path) = srpm {
            path.canonicalize().map_err(|e| e.to_string())?
        } else {
            let package = output("rpm", &["-q", "hyprland"], root)?;
            command(
                "dnf",
                &[
                    format!("--releasever={release}"),
                    "download".into(),
                    "--source".into(),
                    "--destdir".into(),
                    sdk.display().to_string(),
                    package,
                ],
                sdk,
            )?;
            find(sdk, "hyprland-", ".src.rpm", false)?
        };
        command(
            "dnf",
            &[
                format!("--releasever={release}"),
                "-y".into(),
                "builddep".into(),
                source.display().to_string(),
            ],
            sdk,
        )?;
        let mut rpm = Command::new("rpm2cpio")
            .arg(&source)
            .stdout(Stdio::piped())
            .spawn()
            .map_err(|e| e.to_string())?;
        let status = Command::new("cpio")
            .args(["-id", "--no-absolute-filenames"])
            .current_dir(sdk)
            .stdin(rpm.stdout.take().ok_or("No rpm2cpio stream")?)
            .status()
            .map_err(|e| e.to_string())?;
        if !status.success() || !rpm.wait().map_err(|e| e.to_string())?.success() {
            return Err("SDK source extraction failed".into());
        }
        let mut includes = vec![
            "-I/usr/include".to_string(),
            "-I/usr/include/hyprland".into(),
            "-I/usr/include/hyprland/protocols".into(),
            "-I/usr/include/hyprland/src".into(),
        ];
        for name in [
            "hyprutils",
            "hyprlang",
            "hyprcursor",
            "hyprgraphics",
            "aquamarine",
        ] {
            let archive = find(sdk, &format!("{name}-"), ".tar.gz", false)?;
            command(
                "tar",
                &[
                    "-xzf".into(),
                    archive.display().to_string(),
                    "-C".into(),
                    sdk.display().to_string(),
                ],
                sdk,
            )?;
            includes.push(format!(
                "-I{}",
                find(sdk, &format!("{name}-"), "", true)?
                    .join("include")
                    .display()
            ));
        }
        let lua_name = format!("lua-{}", value(h, "lua_version")?);
        let archive = sdk.join(format!("{lua_name}.tar.gz"));
        if !archive.is_file() {
            return Err(format!("Matching {lua_name} missing from compositor SRPM"));
        }
        command(
            "tar",
            &[
                "-xzf".into(),
                archive.display().to_string(),
                "-C".into(),
                sdk.display().to_string(),
            ],
            sdk,
        )?;
        let lua_dir = sdk.join(lua_name);
        let jobs = h
            .get("build_jobs")
            .and_then(Value::as_integer)
            .filter(|n| (1..=64).contains(n))
            .ok_or("Invalid hyprglass.build_jobs")?;
        command(
            "make",
            &[
                "MYCFLAGS=-fPIC -fvisibility=hidden".into(),
                "linux".into(),
                format!("-j{jobs}"),
            ],
            &lua_dir,
        )?;
        includes.push(format!("-I{}", lua_dir.join("src").display()));
        includes.extend(
            output(
                "pkg-config",
                &[
                    "--cflags",
                    "pixman-1",
                    "libdrm",
                    "cairo",
                    "xkbcommon",
                    "libinput",
                    "wayland-server",
                    "pango",
                    "libzip",
                    "librsvg-2.0",
                    "libdisplay-info",
                ],
                sdk,
            )?
            .split_whitespace()
            .map(str::to_string),
        );
        // Reproducible plugins: header paths reach the binary through __FILE__ (assert/log macros); the
        // SDK lives in a random temp dir, so map it to a fixed prefix or every build hashes differently.
        includes.push(sdk_prefix_map(sdk));
        let repo = sdk.join("source");
        // Local-source mode: a MiOS-original plugin kept in the tree (`source_path`, repo-relative
        // under --root) is copied instead of cloned; the image commit is its pin.
        let local = p
            .get("source_path")
            .and_then(Value::as_str)
            .map(|s| s.trim_start_matches('/').to_owned());
        let revision: String = if let Some(rel) = &local {
            if rel.is_empty() || Path::new(rel).components().any(|c| !matches!(c, std::path::Component::Normal(_))) {
                return Err(format!("Unsafe {plugin} source_path"));
            }
            command(
                "cp",
                &["-a".into(), root.join(rel).display().to_string(), repo.display().to_string()],
                sdk,
            )?;
            format!("tree:{rel}")
        } else {
            // Policy (operator, 2026-10-16): latest upstream, fail loudly. The SSOT names the
            // repository only; the build takes its default-branch HEAD, logs the resolved commit,
            // and the reviewed MiOS patches below are the drift gate.
            let url = upstream_source(p, plugin)?;
            command(
                "git",
                &[
                    "clone".into(),
                    "--depth=1".into(),
                    "--single-branch".into(),
                    url.into(),
                    repo.display().to_string(),
                ],
                sdk,
            )?;
            let head = output("git", &["rev-parse", "HEAD"], &repo)?;
            eprintln!("[build-hyprland-plugin] {plugin}: {url} default-branch HEAD resolved to {head}");
            // Latest upstream that builds against the INSTALLED compositor: a plugin repository that
            // publishes hyprpm.toml commit_pins (Hyprland commit -> plugin commit, the map hyprpm itself
            // uses; HEAD follows hyprland-git) is moved to the entry for the installed Hyprland release,
            // whose commit is resolved now from its tag. No hyprpm.toml or no entry keeps HEAD. Nothing is
            // pinned in the SSOT: the resolved commit is only logged.
            match fs::read_to_string(repo.join("hyprpm.toml")) {
                Ok(text) => match hyprpm_pin(&text, &hyprland_commit(h, &installed, sdk)?)? {
                    Some(pin) if pin != head => {
                        command("git", &["fetch".into(), "-q".into(), "--depth=1".into(), "origin".into(), pin.clone()], &repo)
                            .map_err(|e| format!("{plugin}: fetching hyprpm.toml commit {pin} for Hyprland {installed}: {e}"))?;
                        command("git", &["checkout".into(), "-q".into(), pin.clone()], &repo)?;
                        eprintln!("[build-hyprland-plugin] {plugin}: hyprpm.toml maps Hyprland {installed} to {pin}; building that (latest for this compositor)");
                        pin
                    }
                    Some(_) => head,
                    None => {
                        eprintln!("[build-hyprland-plugin] {plugin}: hyprpm.toml has no entry for Hyprland {installed}; building HEAD");
                        head
                    }
                },
                Err(_) => head,
            }
        };
        if plugin == "hyprglass" {
            let layer_source = repo.join("src/GlassLayerSurface.cpp");
            let patched = layer_geometry_source(&fs::read_to_string(&layer_source).map_err(|e| e.to_string())?)
                .map_err(|e| format!("hyprglass at {revision}: {e}"))?;
            fs::write(&layer_source, patched).map_err(|e| e.to_string())?;
        }
        // Reviewed MiOS patches from the tree, applied in SSOT order; any drift fails the build.
        apply_patches(root, &repo, p, plugin, &revision)?;
        let build = match p.get("source_subdir").and_then(Value::as_str) {
            Some(sub) if !sub.is_empty() => {
                if Path::new(sub).components().any(|c| !matches!(c, std::path::Component::Normal(_))) {
                    return Err("Unsafe source_subdir".into());
                }
                repo.join(sub)
            }
            _ => repo.clone(),
        };
        // The COPR compositor embeds hidden Lua 5.5. Linking system Lua 5.4 corrupts its state.
        // Keep Lua private; do not bind compositor singleton symbols to plugin-local copies.
        let libs = format!(
            "{} -lm -ldl {}",
            lua_dir.join("src/liblua.a").display(),
            output(
                "pkg-config",
                &[
                    "--libs",
                    "pixman-1",
                    "libdrm",
                    "egl",
                    "glesv2",
                    "wayland-server"
                ],
                sdk
            )?
        );
        let artifact = match build_system {
            "make" => {
                command(
                    "make",
                    &[
                        format!("-j{jobs}"),
                        format!("INCLUDES={}", includes.join(" ")),
                        format!("LIBS={libs}"),
                        "LDFLAGS=-shared".into(),
                    ],
                    &build,
                )?;
                build.join(format!("{plugin}.so"))
            }
            "cmake" => {
                let directory = sdk.join("cmake-build");
                command(
                    "cmake",
                    &[
                        "-S".into(), build.display().to_string(),
                        "-B".into(), directory.display().to_string(),
                        "-DCMAKE_BUILD_TYPE=Release".into(),
                        "-DHYPRLAND_HEADERS=/usr".into(),
                        format!("-DHYPRLAND_LUA_ROOT={}", lua_dir.display()),
                        "-DHYPRLAND_USE_COMPOSITOR_GRAPHICS=ON".into(),
                        format!("-DCMAKE_CXX_FLAGS={}", includes.join(" ")),
                    ],
                    sdk,
                )?;
                command("cmake", &[
                    "--build".into(), directory.display().to_string(),
                    "--parallel".into(), jobs.to_string(),
                ], sdk)?;
                command("ctest", &[
                    "--test-dir".into(), directory.display().to_string(),
                    "--output-on-failure".into(), "--no-tests=error".into(),
                ], sdk)?;
                directory.join(format!("{plugin}.so"))
            }
            other => return Err(format!("Unsupported {plugin} build_system {other}")),
        };
        let dynamic = output(
            "readelf",
            &["-d", artifact.to_str().ok_or("Invalid artifact path")?],
            sdk,
        )?;
        if dynamic
            .lines()
            .any(|line| line.contains("NEEDED") && line.contains("liblua"))
        {
            return Err(format!("{plugin} must not depend on the incompatible system Lua"));
        }
        let rel = value(p, "path")?.trim_start_matches('/');
        if Path::new(rel)
            .components()
            .any(|c| !matches!(c, std::path::Component::Normal(_)))
        {
            return Err(format!("Unsafe {plugin} install path"));
        }
        let target = root.join(rel);
        fs::create_dir_all(target.parent().ok_or("No plugin parent")?)
            .map_err(|e| e.to_string())?;
        // Stage beside the target and rename: a running compositor keeps its mapped inode intact.
        let staged = target.with_extension("so.new");
        fs::copy(&artifact, &staged).map_err(|e| e.to_string())?;
        fs::rename(&staged, &target).map_err(|e| e.to_string())?;
        Ok(format!(
            "{plugin} {revision} built for Hyprland {installed}: {}\n",
            target.display()
        ))
    }
}

/// Projects [host.tuning.profiles.<name>.<section>] to native TuneD INI profiles at
/// usr/lib/tuned/profiles/<name>/tuned.conf. Section and key order follow the SSOT.
pub mod render_tuned {
    use std::fs;
    use std::path::{Path, PathBuf};
    use toml_edit::{Document, Item, Value};

    const HEADER: &str = "# AI-hint: Generated by mios-gen render-tuned from mios.toml [host.tuning.profiles].\n# AI-related: usr/share/mios/mios.toml, tools/native/mios-gen/src/render.rs\n";

    fn scalar(v: &Value) -> Result<String, String> {
        match v {
            Value::String(s) => Ok(s.value().clone()),
            Value::Integer(i) => Ok(i.value().to_string()),
            Value::Float(f) => Ok(f.value().to_string()),
            Value::Boolean(b) => Ok(b.value().to_string()),
            other => Err(format!("unsupported value {}", other.to_string().trim())),
        }
    }

    /// Returns (relative path, rendered file) for every declared profile.
    pub fn render(toml_text: &str) -> Result<Vec<(PathBuf, String)>, String> {
        let doc: Document = toml_text.parse().map_err(|e| format!("mios.toml: {e}"))?;
        let profiles = doc
            .get("host")
            .and_then(|h| h.get("tuning"))
            .and_then(|t| t.get("profiles"))
            .and_then(Item::as_table_like)
            .ok_or("[host.tuning.profiles] is not declared")?;
        let mut out = Vec::new();
        for (name, profile) in profiles.iter() {
            let sections = profile
                .as_table_like()
                .ok_or(format!("[host.tuning.profiles.{name}] is not a table"))?;
            let mut text = String::from(HEADER);
            for (section, keys) in sections.iter() {
                let keys = keys
                    .as_table_like()
                    .ok_or(format!("[host.tuning.profiles.{name}.{section}] is not a table"))?;
                text.push_str(&format!("\n[{section}]\n"));
                for (key, item) in keys.iter() {
                    let value = item
                        .as_value()
                        .ok_or(format!("{name}.{section}.{key} is not a value"))
                        .and_then(|v| scalar(v).map_err(|e| format!("{name}.{section}.{key}: {e}")))?;
                    text.push_str(&format!("{key}={value}\n"));
                }
            }
            out.push((PathBuf::from(format!("usr/lib/tuned/profiles/{name}/tuned.conf")), text));
        }
        Ok(out)
    }

    pub fn run_render_tuned(root: &Path, check: bool) -> Result<String, (String, i32)> {
        let toml_path = root.join("usr/share/mios/mios.toml");
        let text = fs::read_to_string(&toml_path)
            .map_err(|e| (format!("render-tuned: {}: {e}", toml_path.display()), 1))?;
        let files = render(&text).map_err(|e| (format!("render-tuned: {e}"), 1))?;
        let mut drift = Vec::new();
        for (rel, body) in &files {
            let path = root.join(rel);
            if fs::read_to_string(&path).ok().as_deref() == Some(body.as_str()) {
                continue;
            }
            if check {
                drift.push(rel.display().to_string());
                continue;
            }
            if let Some(dir) = path.parent() {
                fs::create_dir_all(dir).map_err(|e| (format!("render-tuned: {}: {e}", dir.display()), 1))?;
            }
            fs::write(&path, body).map_err(|e| (format!("render-tuned: {}: {e}", path.display()), 1))?;
        }
        if !drift.is_empty() {
            return Err((format!("[render-tuned] DRIFT: {}", drift.join(", ")), 1));
        }
        Ok(format!("[render-tuned] {} TuneD profile(s) {}", files.len(), if check { "match" } else { "rendered" }))
    }

    #[cfg(test)]
    mod tests {
        use super::render;

        #[test]
        fn renders_sections_in_ssot_order() {
            let files = render(
                "[host.tuning.profiles.p.main]\nsummary = \"S\"\ninclude = \"virtual-host\"\n[host.tuning.profiles.p.sysctl]\n\"vm.swappiness\" = 10\n",
            )
            .unwrap();
            assert_eq!(files.len(), 1);
            assert_eq!(files[0].0.to_str().unwrap(), "usr/lib/tuned/profiles/p/tuned.conf");
            assert!(files[0].1.ends_with("\n[main]\nsummary=S\ninclude=virtual-host\n\n[sysctl]\nvm.swappiness=10\n"));
        }

        #[test]
        fn rejects_missing_table_and_nested_values() {
            assert!(render("[host]\n").is_err());
            assert!(render("[host.tuning.profiles.p.cpu]\nx = [1]\n").is_err());
        }
    }
}
