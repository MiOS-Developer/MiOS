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

        let mut out = lines.join("\n");
        out.push('\n');
        out
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
            let target_path = apps_dir.join(format!("{name}.desktop"));

            if check {
                if !target_path.is_file() {
                    drifted.push(format!("{name}.desktop missing"));
                    continue;
                }
                let current = fs::read_to_string(&target_path).unwrap_or_default();
                let current_norm = current.replace("\r\n", "\n");
                let rendered_norm = rendered.replace("\r\n", "\n");
                if current_norm != rendered_norm {
                    drifted.push(format!("{name}.desktop content drifted"));
                }
            } else {
                if let Some(parent) = target_path.parent() {
                    let _ = fs::create_dir_all(parent);
                }
                if let Err(e) = fs::write(&target_path, rendered.as_bytes()) {
                    return Err((
                        format!(
                            "render-desktop: write {} failed: {e}",
                            target_path.display()
                        ),
                        1,
                    ));
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

    pub fn build_exports(root: &Path) -> Result<BTreeMap<String, String>, String> {
        let merged = mios_resolver::resolve_merged(Some(root), false)
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
                if let Some(aliases) = inputs.get(name).filter(|aliases| !aliases.is_empty()) {
                    lines.push(format!(
                        "_mios_input {name} {} || {{ return 1 2>/dev/null || exit 1; }}",
                        aliases.join(" ")
                    ));
                }
                lines.push(sh_assign(name, val));
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
            .filter(|k| !k.starts_with("MIOS_WIN_"))
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

    pub fn run_render_globals(root: &Path, check: bool) -> Result<(String, usize), (String, i32)> {
        let toml_path = root.join("usr/share/mios/mios.toml");
        if !toml_path.is_file() {
            return Err((
                format!("render-globals: {} does not exist", toml_path.display()),
                1,
            ));
        }

        let mut exports = build_exports(root).map_err(|e| (e, 1))?;
        let merged =
            mios_resolver::resolve_merged(Some(root), false).map_err(|e| (e.to_string(), 1))?;
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
            fs::read_to_string(&sh_path)
                .ok()
                .map(|s| s.replace("\r\n", "\n"))
        } else {
            None
        };
        let sh_norm = sh_body.replace("\r\n", "\n");
        if sh_existing.as_deref() != Some(&sh_norm) {
            drifted.push("automation/lib/globals.sh".to_string());
            if !check {
                if let Some(parent) = sh_path.parent() {
                    let _ = fs::create_dir_all(parent);
                }
                if let Err(e) = fs::write(&sh_path, &sh_norm) {
                    return Err((format!("failed to write {}: {e}", sh_path.display()), 1));
                }
            }
        }

        // Check ps_path
        let ps_existing = if ps_path.is_file() {
            fs::read(&ps_path).ok().map(|bytes| {
                let slice = if bytes.starts_with(&[0xEF, 0xBB, 0xBF]) {
                    &bytes[3..]
                } else {
                    &bytes[..]
                };
                String::from_utf8_lossy(slice).replace("\r\n", "\n")
            })
        } else {
            None
        };
        let ps_norm = ps_body.replace("\r\n", "\n");
        if ps_existing.as_deref() != Some(&ps_norm) {
            drifted.push("automation/lib/globals.ps1".to_string());
            if !check {
                if let Some(parent) = ps_path.parent() {
                    let _ = fs::create_dir_all(parent);
                }
                let mut bytes = vec![0xEF, 0xBB, 0xBF];
                let crlf_body = ps_norm.replace('\n', "\r\n");
                bytes.extend_from_slice(crlf_body.as_bytes());
                if let Err(e) = fs::write(&ps_path, bytes) {
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
    #[cfg(unix)]
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

    pub fn run_render_manpages(
        root: &Path,
        check: bool,
        validate: bool,
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

        // ADR-0030 Decision 4 / task_3653: Double-render determinism and registry coverage checks
        if check {
            let rendered2 = render_pages(root, &ssot);
            if rendered != rendered2 {
                return Err((
                    "render-manpages: non-deterministic render detected across passes".to_string(),
                    1,
                ));
            }

            let mut missing_declared = Vec::new();
            let empty_table = toml::value::Table::new();
            let verbs_table = ssot
                .get("verbs")
                .and_then(|v| v.as_table())
                .unwrap_or(&empty_table);
            for n in verbs_table.keys() {
                let p = format!("{MAN}/man1/mios-{n}.1");
                if !rendered.contains_key(&p) {
                    missing_declared.push(format!("{p} (declared in [verbs.{n}])"));
                }
            }
            let core_pages = [
                format!("{MAN}/man1/mios.1"),
                format!("{MAN}/man5/mios.toml.5"),
                format!("{MAN}/man7/mios.7"),
            ];
            for cp in &core_pages {
                if !rendered.contains_key(cp) {
                    missing_declared.push(format!("{cp} (core manual page)"));
                }
            }
            if let Some(var_entries) = ssot
                .get("variants")
                .and_then(|v| v.get("entries"))
                .and_then(|e| e.as_table())
            {
                if !var_entries.is_empty() {
                    let vp = format!("{MAN}/man7/mios-variants.7");
                    if !rendered.contains_key(&vp) {
                        missing_declared.push(format!("{vp} (declared in [variants.entries])"));
                    }
                }
            }
            if !missing_declared.is_empty() {
                let mut lines = vec!["missing declared man page(s):".to_string()];
                for m in missing_declared.iter().take(10) {
                    lines.push(format!("  {m}"));
                }
                return Err((lines.join("\n"), 1));
            }
        }

        let mut drift = Vec::new();

        for (rel, body) in &rendered {
            let full = root.join(rel);
            let current = fs::read_to_string(&full).ok();
            let matches = match &current {
                Some(curr) => curr.replace("\r\n", "\n") == body.replace("\r\n", "\n"),
                None => false,
            };

            if matches {
                continue;
            }

            if check {
                if current.is_some() {
                    drift.push(rel.clone());
                }
                continue;
            }

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

        let mut orphans = Vec::new();
        let man_root = root.join(MAN);
        if man_root.is_dir() {
            for entry in walkdir::WalkDir::new(&man_root)
                .into_iter()
                .filter_map(|e| e.ok())
            {
                if entry.file_type().is_file() {
                    let p = entry.path();
                    if let Ok(rel) = p.strip_prefix(root) {
                        let rel_norm = rel.to_string_lossy().replace('\\', "/");
                        if !rendered.contains_key(&rel_norm) {
                            orphans.push(rel_norm);
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
            for (rel, body) in &rendered {
                let full = root.join(rel);
                validated += 1;
                let res = if full.is_file() {
                    validate_man_page(&full)
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
            messages.push(format!("[render-manpages] {validated} page(s) validated"));
        }

        let action = if check { "verified" } else { "rendered" };
        messages.push(format!(
            "[render-manpages] {} page(s) {action}",
            rendered.len()
        ));

        Ok((messages.join("\n"), 0))
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
