// AI-hint: Projects declared terminal-client interaction settings and provider capabilities from the SSOT
// into owned client settings under a home directory. Provider-specific values apply only to the selected
// provider, or to the declared fresh-home default, preserving other providers and MCP configuration.
// Only the declared keys are merged; everything else in the file (operator preferences, credentials, key order,
// TOML comments) is kept, and a file is rewritten only when a declared value differs. Without --home the verb
// validates the SSOT table (the generation.sync step); --runtime reads the layered SSOT (vendor < host < user)
// as an agent launch does (mios-mcp-server --launch-agent).
// AI-related: usr/share/mios/mios.toml [agent_cli.interaction], usr/libexec/mios/mios-mcp-server
// (_project_agent_clients), tools/native/mios-gen/src/main.rs, inline projection tests
//! Agent CLI interaction-settings projection.

use std::fs;
use std::io::Write;
use std::os::unix::fs::{DirBuilderExt, PermissionsExt};
use std::path::{Component, Path, PathBuf};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Format {
    Json,
    Toml,
}

#[derive(Debug, Clone)]
pub struct Client {
    pub name: String,
    pub path: PathBuf,
    pub format: Format,
    pub values: toml::value::Table,
    pub provider_path: Vec<String>,
    pub default_provider: Option<String>,
    pub provider_values: toml::value::Table,
}

#[derive(Debug, Default)]
pub struct Outcome {
    pub declared: usize,
    pub changed: Vec<PathBuf>,
    pub unchanged: Vec<PathBuf>,
    /// Client files left untouched because they could not be read or parsed (name, reason).
    pub skipped: Vec<(String, String)>,
}

/// The declared clients of [agent_cli.interaction]; empty when the table is absent or disabled.
pub fn clients(ssot: &toml::Value) -> Result<Vec<Client>, String> {
    let Some(table) = ssot.get("agent_cli").and_then(|a| a.get("interaction")) else {
        return Ok(Vec::new());
    };
    match table.get("enabled") {
        None | Some(toml::Value::Boolean(true)) => {}
        Some(toml::Value::Boolean(false)) => return Ok(Vec::new()),
        Some(_) => return Err("[agent_cli.interaction].enabled must be a boolean".into()),
    }
    let Some(declared) = table.get("clients") else {
        return Ok(Vec::new());
    };
    let declared = declared
        .as_table()
        .ok_or("[agent_cli.interaction.clients] must be a table")?;
    let mut out = Vec::new();
    for (name, row) in declared {
        let at = format!("[agent_cli.interaction.clients.{name}]");
        let path = row
            .get("path")
            .and_then(toml::Value::as_str)
            .ok_or(format!("{at}.path must be a string"))?;
        let path = PathBuf::from(path);
        if path.as_os_str().is_empty()
            || !path.components().all(|c| matches!(c, Component::Normal(_)))
        {
            return Err(format!(
                "{at}.path must be relative to the home directory without '.' or '..': {}",
                path.display()
            ));
        }
        let format = match row.get("format").and_then(toml::Value::as_str) {
            Some("json") => Format::Json,
            Some("toml") => Format::Toml,
            other => {
                return Err(format!(
                    "{at}.format must be \"json\" or \"toml\", not {other:?}"
                ))
            }
        };
        let values = row
            .get("values")
            .and_then(toml::Value::as_table)
            .ok_or(format!("{at}.values must be a table"))?
            .clone();
        let provider_path = match row.get("provider_path") {
            None => Vec::new(),
            Some(value) => value
                .as_array()
                .ok_or(format!(
                    "{at}.provider_path must be an array of nonempty strings"
                ))?
                .iter()
                .map(|part| {
                    part.as_str()
                        .filter(|s| !s.is_empty())
                        .map(str::to_owned)
                        .ok_or(format!(
                            "{at}.provider_path must be an array of nonempty strings"
                        ))
                })
                .collect::<Result<Vec<_>, _>>()?,
        };
        let default_provider = match row.get("default_provider") {
            None => None,
            Some(value) => Some(
                value
                    .as_str()
                    .filter(|s| !s.is_empty())
                    .ok_or(format!("{at}.default_provider must be a nonempty string"))?
                    .to_owned(),
            ),
        };
        let provider_values = match row.get("provider_values") {
            None => toml::value::Table::new(),
            Some(value) => value
                .as_table()
                .ok_or(format!("{at}.provider_values must be a table"))?
                .clone(),
        };
        if provider_values
            .iter()
            .any(|(name, value)| name.is_empty() || !value.is_table())
        {
            return Err(format!("{at}.provider_values entries must be named tables"));
        }
        if provider_path.is_empty() && (!provider_values.is_empty() || default_provider.is_some()) {
            return Err(format!(
                "{at}.provider_path is required for provider settings"
            ));
        }
        out.push(Client {
            name: name.clone(),
            path,
            format,
            values,
            provider_path,
            default_provider,
            provider_values,
        });
    }
    Ok(out)
}

/// Select scoped settings using the client's existing provider, without changing its identity.
fn effective_values(client: &Client, current: &str) -> Result<toml::value::Table, String> {
    let mut values = client.values.clone();
    if client.provider_values.is_empty() {
        return Ok(values);
    }
    let document = if current.trim().is_empty() {
        serde_json::json!({})
    } else {
        match client.format {
            Format::Json => {
                serde_json::from_str(current).map_err(|e| format!("invalid JSON: {e}"))?
            }
            Format::Toml => {
                let document: toml::Value =
                    current.parse().map_err(|e| format!("invalid TOML: {e}"))?;
                serde_json::to_value(document).map_err(|e| e.to_string())?
            }
        }
    };
    let mut selected = Some(&document);
    for key in &client.provider_path {
        selected = selected.and_then(|value| value.get(key));
    }
    let provider = match selected {
        None => client.default_provider.as_deref(),
        Some(value) => Some(
            value
                .as_str()
                .filter(|s| !s.is_empty())
                .ok_or("the client provider selector must be a nonempty string")?,
        ),
    };
    if let Some(overrides) = provider
        .and_then(|name| client.provider_values.get(name))
        .and_then(toml::Value::as_table)
    {
        mios_resolver::merge::deep_merge_table(&mut values, overrides.clone());
    }
    Ok(values)
}

fn merge_json(dst: &mut serde_json::Value, src: &toml::Value) -> Result<(), String> {
    match (dst, src) {
        (serde_json::Value::Object(d), toml::Value::Table(s)) => {
            for (k, v) in s {
                match d.get_mut(k) {
                    Some(existing) if existing.is_object() && v.is_table() => {
                        merge_json(existing, v)?
                    }
                    _ => {
                        d.insert(
                            k.clone(),
                            serde_json::to_value(v).map_err(|e| e.to_string())?,
                        );
                    }
                }
            }
            Ok(())
        }
        _ => Err("the client file's top level is not an object".into()),
    }
}

fn merge_toml(dst: &mut toml_edit::Table, src: &toml::value::Table) -> Result<(), String> {
    for (k, v) in src {
        if let toml::Value::Table(sub) = v {
            let item = dst.entry(k).or_insert_with(toml_edit::table);
            if item.as_table_like().is_none() {
                *item = toml_edit::table();
            }
            match item {
                toml_edit::Item::Table(t) => merge_toml(t, sub)?,
                other => {
                    // An inline table: rewrite it as a standard table, then merge.
                    let mut t = toml_edit::Table::new();
                    if let Some(inline) = other.as_inline_table() {
                        for (ik, iv) in inline.iter() {
                            t.insert(ik, toml_edit::Item::Value(iv.clone()));
                        }
                    }
                    merge_toml(&mut t, sub)?;
                    *other = toml_edit::Item::Table(t);
                }
            }
        } else {
            let value: toml_edit::Value = v.to_string().parse().map_err(|e| format!("{k}: {e}"))?;
            let same = dst
                .get(k)
                .and_then(toml_edit::Item::as_value)
                .map(|cur| cur.to_string().trim() == value.to_string().trim());
            if same != Some(true) {
                dst.insert(k, toml_edit::value(value));
            }
        }
    }
    Ok(())
}

/// The new text for one client file, or None when the declared values are already present.
pub fn merged_text(
    format: Format,
    current: &str,
    values: &toml::value::Table,
) -> Result<Option<String>, String> {
    let src = toml::Value::Table(values.clone());
    match format {
        Format::Json => {
            let mut doc: serde_json::Value = if current.trim().is_empty() {
                serde_json::Value::Object(serde_json::Map::new()) // an empty client file is a fresh one
            } else {
                serde_json::from_str(current).map_err(|e| format!("invalid JSON: {e}"))?
            };
            let before = doc.clone();
            merge_json(&mut doc, &src)?;
            if doc == before && !current.trim().is_empty() {
                return Ok(None);
            }
            let mut text = serde_json::to_string_pretty(&doc).map_err(|e| e.to_string())?;
            text.push('\n');
            Ok(Some(text))
        }
        Format::Toml => {
            let mut doc: toml_edit::Document =
                current.parse().map_err(|e| format!("invalid TOML: {e}"))?;
            merge_toml(doc.as_table_mut(), values)?;
            let text = doc.to_string();
            Ok((text != current).then_some(text))
        }
    }
}

fn write_atomic(target: &Path, text: &str) -> Result<(), String> {
    let dir = target
        .parent()
        .ok_or("client path has no parent directory")?;
    if !dir.exists() {
        fs::DirBuilder::new()
            .recursive(true)
            .mode(0o700)
            .create(dir)
            .map_err(|e| format!("{}: {e}", dir.display()))?;
    }
    // Client files can hold credentials: keep an existing file's mode, create new ones 0600.
    let mode = fs::metadata(target)
        .map(|m| m.permissions().mode() & 0o7777)
        .unwrap_or(0o600);
    let mut tmp =
        tempfile::NamedTempFile::new_in(dir).map_err(|e| format!("{}: {e}", dir.display()))?;
    tmp.write_all(text.as_bytes()).map_err(|e| e.to_string())?;
    tmp.as_file().sync_all().map_err(|e| e.to_string())?;
    fs::set_permissions(tmp.path(), fs::Permissions::from_mode(mode)).map_err(|e| e.to_string())?;
    tmp.persist(target)
        .map_err(|e| format!("{}: {e}", target.display()))?;
    Ok(())
}

/// Projects every declared client into `home`, or only validates the table when `home` is None.
/// `check` writes nothing and reports the files that would change.
pub fn project(clients: &[Client], home: Option<&Path>, check: bool) -> Result<Outcome, String> {
    let mut out = Outcome {
        declared: clients.len(),
        ..Outcome::default()
    };
    let Some(home) = home else {
        return Ok(out);
    };
    if !home.is_dir() {
        return Err(format!("home directory {} does not exist", home.display()));
    }
    for c in clients {
        let target = home.join(&c.path);
        if fs::symlink_metadata(&target)
            .map(|m| m.file_type().is_symlink())
            .unwrap_or(false)
        {
            out.skipped
                .push((c.name.clone(), format!("{} is a symlink", target.display())));
            continue;
        }
        let current = match fs::read_to_string(&target) {
            Ok(s) => s,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => String::new(),
            Err(e) => {
                out.skipped
                    .push((c.name.clone(), format!("{}: {e}", target.display())));
                continue;
            }
        };
        match effective_values(c, &current)
            .and_then(|values| merged_text(c.format, &current, &values))
        {
            Ok(None) => out.unchanged.push(target),
            Ok(Some(text)) => {
                if !check {
                    write_atomic(&target, &text)?;
                }
                out.changed.push(target);
            }
            // A malformed client file is the operator's: leave it untouched and say so.
            Err(e) => out
                .skipped
                .push((c.name.clone(), format!("{}: {e}", target.display()))),
        }
    }
    Ok(out)
}

/// Loads the SSOT (layered for `runtime`, else the vendor file under `root`) and projects it.
pub fn run(
    root: &Path,
    home: Option<&Path>,
    runtime: bool,
    check: bool,
) -> Result<Outcome, String> {
    let ssot = if runtime {
        crate::terminal::tmux_theme::tmux_runtime::load_layered(root)?
    } else {
        let vendor = root.join("usr/share/mios/mios.toml");
        fs::read_to_string(&vendor)
            .map_err(|e| format!("{}: {e}", vendor.display()))?
            .parse::<toml::Value>()
            .map_err(|e| format!("{}: {e}", vendor.display()))?
    };
    project(&clients(&ssot)?, home, check)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ssot(text: &str) -> toml::Value {
        text.parse().unwrap()
    }

    const DECL: &str = r#"
[agent_cli.interaction]
enabled = true
[agent_cli.interaction.clients.agy]
path = ".gemini/antigravity-cli/settings.json"
format = "json"
[agent_cli.interaction.clients.agy.values]
altScreenMode = "always"
[agent_cli.interaction.clients.gemini]
path = ".gemini/settings.json"
format = "json"
[agent_cli.interaction.clients.gemini.values.ui]
useAlternateBuffer = true
[agent_cli.interaction.clients.codex]
path = ".codex/config.toml"
format = "toml"
[agent_cli.interaction.clients.codex.values.tui]
alternate_screen = "always"
"#;

    #[test]
    fn projects_into_fresh_home_with_private_modes() {
        let home = tempfile::tempdir().unwrap();
        let out = project(&clients(&ssot(DECL)).unwrap(), Some(home.path()), false).unwrap();
        assert_eq!(out.declared, 3);
        assert_eq!(out.changed.len(), 3);
        let agy =
            fs::read_to_string(home.path().join(".gemini/antigravity-cli/settings.json")).unwrap();
        assert_eq!(
            serde_json::from_str::<serde_json::Value>(&agy).unwrap()["altScreenMode"],
            "always"
        );
        let codex = fs::read_to_string(home.path().join(".codex/config.toml")).unwrap();
        assert!(
            codex.contains("[tui]") && codex.contains("alternate_screen = \"always\""),
            "{codex}"
        );
        let mode = fs::metadata(home.path().join(".codex/config.toml"))
            .unwrap()
            .permissions()
            .mode()
            & 0o777;
        assert_eq!(mode, 0o600);
        let dmode = fs::metadata(home.path().join(".codex"))
            .unwrap()
            .permissions()
            .mode()
            & 0o777;
        assert_eq!(dmode, 0o700);
    }

    #[test]
    fn keeps_unrelated_keys_order_comments_and_mode() {
        let home = tempfile::tempdir().unwrap();
        let j = home.path().join(".gemini/settings.json");
        fs::create_dir_all(j.parent().unwrap()).unwrap();
        fs::write(
            &j,
            "{\n  \"zeta\": 1,\n  \"ui\": {\"theme\": \"dark\"},\n  \"alpha\": \"secret\"\n}\n",
        )
        .unwrap();
        fs::set_permissions(&j, fs::Permissions::from_mode(0o640)).unwrap();
        let t = home.path().join(".codex/config.toml");
        fs::create_dir_all(t.parent().unwrap()).unwrap();
        fs::write(
            &t,
            "# operator comment\nmodel = \"x\"\n\n[tui]\n# keep me\nnotifications = true\n",
        )
        .unwrap();
        project(&clients(&ssot(DECL)).unwrap(), Some(home.path()), false).unwrap();
        let json = fs::read_to_string(&j).unwrap();
        let v: serde_json::Value = serde_json::from_str(&json).unwrap();
        assert_eq!(v["ui"]["theme"], "dark");
        assert_eq!(v["ui"]["useAlternateBuffer"], true);
        assert_eq!(v["alpha"], "secret");
        assert!(
            json.find("zeta").unwrap() < json.find("alpha").unwrap(),
            "key order kept: {json}"
        );
        assert_eq!(
            fs::metadata(&j).unwrap().permissions().mode() & 0o777,
            0o640
        );
        let toml_text = fs::read_to_string(&t).unwrap();
        for keep in [
            "# operator comment",
            "model = \"x\"",
            "# keep me",
            "notifications = true",
            "alternate_screen = \"always\"",
        ] {
            assert!(toml_text.contains(keep), "{keep} missing in {toml_text}");
        }
    }

    #[test]
    fn idempotent_second_run_writes_nothing() {
        let home = tempfile::tempdir().unwrap();
        let cs = clients(&ssot(DECL)).unwrap();
        project(&cs, Some(home.path()), false).unwrap();
        let again = project(&cs, Some(home.path()), false).unwrap();
        assert!(again.changed.is_empty(), "{:?}", again.changed);
        assert_eq!(again.unchanged.len(), 3);
        assert!(project(&cs, Some(home.path()), true)
            .unwrap()
            .changed
            .is_empty());
    }

    #[test]
    fn check_reports_without_writing() {
        let home = tempfile::tempdir().unwrap();
        let out = project(&clients(&ssot(DECL)).unwrap(), Some(home.path()), true).unwrap();
        assert_eq!(out.changed.len(), 3);
        assert!(!home.path().join(".gemini").exists());
    }

    #[test]
    fn negative_malformed_client_file_is_left_untouched() {
        let home = tempfile::tempdir().unwrap();
        let j = home.path().join(".gemini/settings.json");
        fs::create_dir_all(j.parent().unwrap()).unwrap();
        fs::write(&j, "{ not json").unwrap();
        let out = project(&clients(&ssot(DECL)).unwrap(), Some(home.path()), false).unwrap();
        assert_eq!(fs::read_to_string(&j).unwrap(), "{ not json");
        assert!(
            out.skipped.iter().any(|(n, _)| n == "gemini"),
            "{:?}",
            out.skipped
        );
        assert_eq!(out.changed.len(), 2);
    }

    #[test]
    fn negative_path_escaping_home_is_rejected() {
        for bad in ["../x.json", "/etc/x.json", "a/../../x.json", "./x.json"] {
            let decl = format!("[agent_cli.interaction.clients.bad]\npath = \"{bad}\"\nformat = \"json\"\n[agent_cli.interaction.clients.bad.values]\nk = 1\n");
            assert!(clients(&ssot(&decl)).is_err(), "{bad} accepted");
        }
        let fmt = "[agent_cli.interaction.clients.bad]\npath = \"x.yaml\"\nformat = \"yaml\"\n[agent_cli.interaction.clients.bad.values]\nk = 1\n";
        assert!(clients(&ssot(fmt)).is_err());
    }

    #[test]
    fn negative_symlinked_client_file_is_skipped() {
        let home = tempfile::tempdir().unwrap();
        let outside = tempfile::NamedTempFile::new().unwrap();
        fs::write(outside.path(), "{}").unwrap();
        let j = home.path().join(".gemini/antigravity-cli/settings.json");
        fs::create_dir_all(j.parent().unwrap()).unwrap();
        std::os::unix::fs::symlink(outside.path(), &j).unwrap();
        let out = project(&clients(&ssot(DECL)).unwrap(), Some(home.path()), false).unwrap();
        assert_eq!(fs::read_to_string(outside.path()).unwrap(), "{}");
        assert!(out.skipped.iter().any(|(n, _)| n == "agy"));
    }

    #[test]
    fn disabled_or_absent_table_projects_nothing() {
        assert!(clients(&ssot("[agent_cli]\nenabled = true\n"))
            .unwrap()
            .is_empty());
        let off = DECL.replace("enabled = true", "enabled = false");
        assert!(clients(&ssot(&off)).unwrap().is_empty());
    }

    const SCOPED: &str = r#"
[agent_cli.interaction.clients.terminal]
path = ".agent/config.toml"
format = "toml"
provider_path = ["model_provider"]
default_provider = "local"
[agent_cli.interaction.clients.terminal.values.tui]
alternate_screen = "always"
[agent_cli.interaction.clients.terminal.provider_values.local]
web_search = "disabled"
"#;

    #[test]
    fn provider_capabilities_preserve_identity_mcp_and_other_providers() {
        let home = tempfile::tempdir().unwrap();
        let cs = clients(&ssot(SCOPED)).unwrap();
        project(&cs, Some(home.path()), false).unwrap();
        let path = home.path().join(".agent/config.toml");
        let fresh: toml::Value = fs::read_to_string(&path).unwrap().parse().unwrap();
        assert_eq!(fresh["web_search"].as_str(), Some("disabled"));
        assert!(fresh.get("model_provider").is_none());
        assert_eq!(
            fs::metadata(&path).unwrap().permissions().mode() & 0o777,
            0o600
        );
        for provider in ["local", "other"] {
            fs::write(&path, format!(
                "# keep operator preferences\nmodel_provider = \"{provider}\"\nmodel = \"selected\"\nweb_search = \"live\"\n[mcp_servers.control]\ncommand = \"/usr/libexec/mios/mios-mcp-server\"\nenabled = true\n[profiles.explicit]\nweb_search = \"live\"\n"
            )).unwrap();
            let out = project(&cs, Some(home.path()), false).unwrap();
            assert!(out.skipped.is_empty());
            let text = fs::read_to_string(&path).unwrap();
            let value: toml::Value = text.parse().unwrap();
            assert!(text.contains("# keep operator preferences"));
            assert_eq!(value["model_provider"].as_str(), Some(provider));
            assert_eq!(value["model"].as_str(), Some("selected"));
            assert_eq!(
                value["web_search"].as_str(),
                Some(if provider == "local" {
                    "disabled"
                } else {
                    "live"
                })
            );
            assert_eq!(
                value["mcp_servers"]["control"]["enabled"].as_bool(),
                Some(true)
            );
            assert_eq!(
                value["profiles"]["explicit"]["web_search"].as_str(),
                Some("live")
            );
            assert_eq!(value["tui"]["alternate_screen"].as_str(), Some("always"));
            assert!(project(&cs, Some(home.path()), false)
                .unwrap()
                .changed
                .is_empty());
        }
    }

    #[test]
    fn layered_operator_provider_opt_in_is_preserved() {
        let mut config = ssot(SCOPED);
        mios_resolver::merge::deep_merge(&mut config, ssot(
            "[agent_cli.interaction.clients.terminal.provider_values.local]\nweb_search = \"live\"\n"
        ));
        let home = tempfile::tempdir().unwrap();
        project(&clients(&config).unwrap(), Some(home.path()), false).unwrap();
        let value: toml::Value = fs::read_to_string(home.path().join(".agent/config.toml"))
            .unwrap()
            .parse()
            .unwrap();
        assert_eq!(value["web_search"].as_str(), Some("live"));
    }

    #[test]
    fn nested_json_provider_selector_is_generic() {
        let config = SCOPED
            .replace(".agent/config.toml", ".agent/settings.json")
            .replace("format = \"toml\"", "format = \"json\"")
            .replace("[\"model_provider\"]", "[\"connection\", \"provider\"]");
        let home = tempfile::tempdir().unwrap();
        let path = home.path().join(".agent/settings.json");
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(&path, r#"{"connection":{"provider":"other"},"web_search":"live","mcpServers":{"control":{"command":"kept"}}}"#).unwrap();
        let cs = clients(&ssot(&config)).unwrap();
        project(&cs, Some(home.path()), false).unwrap();
        let other: serde_json::Value =
            serde_json::from_str(&fs::read_to_string(&path).unwrap()).unwrap();
        assert_eq!(other["web_search"], "live");
        assert_eq!(other["mcpServers"]["control"]["command"], "kept");
        let mut local = other;
        local["connection"]["provider"] = serde_json::json!("local");
        fs::write(&path, serde_json::to_string(&local).unwrap()).unwrap();
        project(&cs, Some(home.path()), false).unwrap();
        let local: serde_json::Value =
            serde_json::from_str(&fs::read_to_string(&path).unwrap()).unwrap();
        assert_eq!(local["web_search"], "disabled");
        assert_eq!(local["connection"]["provider"], "local");
    }

    #[test]
    fn invalid_provider_declarations_and_selector_fail_closed() {
        for replacement in [
            "provider_path = []",
            "provider_path = [1]",
            "provider_path = [\"\"]",
            "provider_path = \"provider\"",
        ] {
            assert!(clients(&ssot(
                &SCOPED.replace("provider_path = [\"model_provider\"]", replacement)
            ))
            .is_err());
        }
        assert!(clients(&ssot(
            &SCOPED.replace("default_provider = \"local\"", "default_provider = 1")
        ))
        .is_err());
        assert!(clients(&ssot(&SCOPED.replace(
            "[agent_cli.interaction.clients.terminal.provider_values.local]\nweb_search = \"disabled\"",
            "[agent_cli.interaction.clients.terminal.provider_values]\nlocal = 1"
        ))).is_err());
        let home = tempfile::tempdir().unwrap();
        let path = home.path().join(".agent/config.toml");
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(&path, "model_provider = 42\n").unwrap();
        let out = project(&clients(&ssot(SCOPED)).unwrap(), Some(home.path()), false).unwrap();
        assert_eq!(out.skipped.len(), 1);
        assert!(out.skipped[0].1.contains("provider selector"));
        assert_eq!(fs::read_to_string(path).unwrap(), "model_provider = 42\n");
    }

    #[test]
    fn validate_only_without_home() {
        let out = project(&clients(&ssot(DECL)).unwrap(), None, false).unwrap();
        assert_eq!(out.declared, 3);
        assert!(out.changed.is_empty() && out.skipped.is_empty());
    }
}
