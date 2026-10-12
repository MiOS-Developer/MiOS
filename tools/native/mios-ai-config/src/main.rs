// AI-hint: Projects etc/mios/ai/config.json and the vendor usr/share/mios/ai/v1/config.json (the OpenAI-client connection config) from [ai] endpoint/agent_model/embed_model and [ports], so neither can drift to a retired port.
// AI-related: usr/share/mios/mios.toml, etc/mios/ai/config.json, usr/share/mios/ai/v1/config.json, automation/98-drift-checks.sh, tools/sync-generated.sh, usr/libexec/mios/mios-mcp-server, usr/share/mios/windows/mios-native-client-setup.ps1

#![forbid(unsafe_code)]
#![warn(clippy::unwrap_used, clippy::expect_used, clippy::panic)]

use std::path::{Path, PathBuf};
use std::process::ExitCode;
mod runtime {
    use serde_json::{json, Value};
    use std::{collections::BTreeMap, path::Path, process::Command};

    pub fn connection(root: &Path) -> Result<(String, String), String> {
        let doc = mios_resolver::resolve_merged(Some(root), false).map_err(|e| e.to_string())?;
        let mut exports =
            mios_resolver::emit::build_exports_map(&doc, mios_resolver::stack_offset_of(&doc));
        mios_resolver::emit::resolve_cross_references(&mut exports);
        // The agent-pipe front door advertises ai.agent_model, not the inference
        // backend's ai.model. An explicit client model still has to pass /models.
        select(
            &exports,
            std::env::var("MIOS_AI_ENDPOINT").ok(),
            std::env::var("MIOS_AI_GATEWAY_MODEL").ok(),
        )
    }

    fn select(
        exports: &BTreeMap<String, String>,
        endpoint: Option<String>,
        model: Option<String>,
    ) -> Result<(String, String), String> {
        let get = |key: &str, override_value: Option<String>| {
            override_value
                .or_else(|| exports.get(key).cloned())
                .filter(|v| {
                    !v.trim().is_empty() && !v.contains("${") && !v.chars().any(char::is_control)
                })
                .ok_or_else(|| format!("{key} is missing, empty or unresolved"))
        };
        let endpoint = get("MIOS_AI_ENDPOINT", endpoint)?;
        let model = get("MIOS_AI_AGENT_MODEL", model)?;
        validate_endpoint(&endpoint)?;
        Ok((endpoint.trim_end_matches('/').into(), model))
    }

    fn validate_endpoint(endpoint: &str) -> Result<(), String> {
        let rest = endpoint
            .strip_prefix("http://")
            .or_else(|| endpoint.strip_prefix("https://"))
            .ok_or("MIOS_AI_ENDPOINT must use http or https")?;
        let (authority, path) = rest
            .split_once('/')
            .ok_or("MIOS_AI_ENDPOINT must end in /v1")?;
        if authority.is_empty()
            || authority.contains('@')
            || authority.chars().any(char::is_whitespace)
            || path != "v1"
            || endpoint.contains(['?', '#', '$'])
        {
            return Err("MIOS_AI_ENDPOINT must name the local OpenAI-compatible /v1 surface without credentials or placeholders".into());
        }
        // An endpoint is an explicit operator setting, including private blade DNS.
        // Reject public vendor surfaces; never choose an alternate endpoint here.
        let host = authority
            .split(':')
            .next()
            .unwrap_or_default()
            .to_ascii_lowercase();
        for domain in [
            "openai.com",
            "anthropic.com",
            "googleapis.com",
            "groq.com",
            "deepseek.com",
            "openrouter.ai",
            "together.xyz",
        ] {
            if host == domain || host.ends_with(&format!(".{domain}")) {
                return Err("MIOS_AI_ENDPOINT references a prohibited cloud API".into());
            }
        }
        Ok(())
    }

    pub fn opencode(mut config: Value, endpoint: &str, model: &str) -> Result<Value, String> {
        validate_endpoint(endpoint)?;
        let object = config
            .as_object_mut()
            .ok_or("OpenCode config must be an object")?;
        let providers = object
            .entry("provider")
            .or_insert_with(|| json!({}))
            .as_object_mut()
            .ok_or("OpenCode provider must be an object")?;
        if let Some(existing) = providers.get("local") {
            if existing.get("name").and_then(Value::as_str) != Some("Local MiOS") {
                return Err(
                    "OpenCode provider.local is operator-owned; refusing to replace it".into(),
                );
            }
        }
        providers.insert("local".into(), json!({"name":"Local MiOS","npm":"@ai-sdk/openai-compatible","options":{"baseURL":endpoint},"models":{model:{"name":model}}}));
        if let Some(legacy) = object.get_mut("providers").and_then(Value::as_object_mut) {
            if legacy
                .get("local")
                .and_then(|v| v.get("name"))
                .and_then(Value::as_str)
                == Some("Local MiOS")
            {
                legacy.remove("local");
            }
            if legacy.is_empty() {
                object.remove("providers");
            }
        }
        if object
            .get("model")
            .and_then(Value::as_str)
            .is_none_or(|m| m.starts_with("local/"))
        {
            object.insert("model".into(), json!(format!("local/{model}")));
        }
        Ok(config)
    }

    pub fn validate_models(body: &[u8], model: &str) -> Result<usize, String> {
        let value: Value = serde_json::from_slice(body)
            .map_err(|e| format!("/v1/models returned invalid JSON: {e}"))?;
        let rows = value
            .get("data")
            .and_then(Value::as_array)
            .ok_or("/v1/models did not return an OpenAI data array")?;
        if !rows
            .iter()
            .any(|v| v.get("id").and_then(Value::as_str) == Some(model))
        {
            return Err(format!("selected model {model:?} is not advertised by MIOS_AI_ENDPOINT ({} model(s)); model substitution refused", rows.len()));
        }
        Ok(rows.len())
    }

    pub fn probe(endpoint: &str, model: &str) -> Result<usize, String> {
        validate_endpoint(endpoint)?;
        let executable = if cfg!(windows) { "curl.exe" } else { "curl" };
        let mut command = Command::new(executable);
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x08000000);
        }
        command.args([
            "--silent",
            "--show-error",
            "--fail",
            "--connect-timeout",
            "2",
            "--max-time",
            "5",
            "--max-filesize",
            "1048576",
            "--noproxy",
            "*",
            "--proto",
            "=http,https",
            "--url",
            &format!("{endpoint}/models"),
        ]);
        // No redirect following: bearer material cannot leave the selected surface.
        // Supply credentials through curl's stdin config, never argv or diagnostics.
        use std::io::Write;
        use std::process::Stdio;
        command
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null());
        if std::env::var_os("MIOS_AI_KEY").is_some() {
            command.args(["--config", "-"]);
        }
        let mut child = command
            .spawn()
            .map_err(|e| format!("local AI readiness probe could not start {executable}: {e}"))?;
        if let Some(mut input) = child.stdin.take() {
            if let Ok(key) = std::env::var("MIOS_AI_KEY") {
                if key.chars().any(char::is_control) {
                    let _ = child.kill();
                    let _ = child.wait();
                    return Err("MIOS_AI_KEY contains invalid control characters".into());
                }
                let key = key.replace('\\', "\\\\").replace('"', "\\\"");
                if let Err(e) = writeln!(input, "header = \"Authorization: Bearer {key}\"") {
                    let _ = child.kill();
                    let _ = child.wait();
                    return Err(format!("local AI probe input failed: {e}"));
                }
            }
        }
        let output = child
            .wait_with_output()
            .map_err(|e| format!("local AI readiness probe failed: {e}"))?;
        if !output.status.success() {
            return Err(format!("MIOS_AI_ENDPOINT {endpoint} is unavailable or rejected /models (curl exit {:?}); check agent-pipe and its configured inference backend", output.status.code()));
        }
        validate_models(&output.stdout, model)
    }

    #[cfg(test)]
    mod tests {
        use super::*;
        #[test]
        fn explicit_route_and_model_win_without_a_lane_fallback() -> Result<(), String> {
            let exports = BTreeMap::from([
                ("MIOS_AI_ENDPOINT".into(), "http://localhost:9100/v1".into()),
                ("MIOS_AI_AGENT_MODEL".into(), "brain".into()),
                ("MIOS_AI_MODEL".into(), "backend-only".into()),
            ]);
            assert_eq!(
                select(&exports, None, None)?,
                ("http://localhost:9100/v1".into(), "brain".into())
            );
            assert_eq!(
                select(
                    &exports,
                    Some("http://blade.mesh:9200/v1".into()),
                    Some("chosen".into())
                )?,
                ("http://blade.mesh:9200/v1".into(), "chosen".into())
            );
            for value in [
                "",
                "${MIOS_AI_ENDPOINT}",
                "http://localhost:1/v1\n",
                "https://api.openai.com/v1",
                "ftp://localhost/v1",
                "http://secret@localhost/v1",
                "http://localhost/v1?q=1",
            ] {
                assert!(
                    select(&exports, Some(value.into()), None).is_err(),
                    "{value}"
                );
            }
            assert!(select(&BTreeMap::new(), None, None).is_err());
            Ok(())
        }
        #[test]
        fn projects_only_owned_provider_and_preserves_operator_model_and_mcp() -> Result<(), String>
        {
            let input = json!({"model":"custom/user-model","mcp":{"custom":{"enabled":true}},"provider":{"custom":{"name":"mine"}},"providers":{"local":{"name":"Local MiOS"},"other":{}}});
            let rendered = opencode(input.clone(), "http://localhost:9200/v1", "chosen")?;
            assert_eq!(
                rendered["provider"]["local"]["options"]["baseURL"],
                "http://localhost:9200/v1"
            );
            assert_eq!(
                rendered["provider"]["local"]["models"]["chosen"]["name"],
                "chosen"
            );
            assert_eq!(rendered["model"], input["model"]);
            assert_eq!(rendered["mcp"], input["mcp"]);
            assert_eq!(rendered["provider"]["custom"], input["provider"]["custom"]);
            assert!(rendered["providers"].get("local").is_none());
            assert!(rendered["providers"].get("other").is_some());
            assert_eq!(
                opencode(rendered.clone(), "http://localhost:9200/v1", "chosen")?,
                rendered
            );
            assert_eq!(
                opencode(
                    json!({"model":"local/old"}),
                    "http://localhost:9200/v1",
                    "chosen"
                )?["model"],
                "local/chosen"
            );
            assert!(opencode(
                json!({"provider":{"local":{"name":"Operator"}}}),
                "http://localhost:9200/v1",
                "chosen"
            )
            .is_err());
            assert!(
                opencode(json!({"provider":[]}), "http://localhost:9200/v1", "chosen").is_err()
            );
            Ok(())
        }
        #[test]
        fn refuses_wrong_model_empty_and_malformed_catalogs() -> Result<(), String> {
            assert_eq!(
                validate_models(br#"{"data":[{"id":"brain"},{"id":"other"}]}"#, "brain")?,
                2
            );
            for body in [
                br#"{"data":[{"id":"mios-igpu"}]}"#.as_slice(),
                br#"{"data":[]}"#,
                br#"{"models":[{"name":"brain"}]}"#,
                br#"not json"#,
            ] {
                assert!(validate_models(body, "brain").is_err());
            }
            Ok(())
        }
    }
}

const SSOT: &str = "usr/share/mios/mios.toml";
/// Every projected copy: the admin-layer client config, and the vendor ai/v1
/// manifest copy (same four keys plus a descriptive `x-mios` block).
const OUTPUTS: [(&str, Shape); 2] = [
    ("etc/mios/ai/config.json", Shape::Client),
    ("usr/share/mios/ai/v1/config.json", Shape::Manifest),
];

#[derive(Debug, Clone, Copy, PartialEq)]
enum Shape {
    Client,
    Manifest,
}

/// Descriptive only: nothing here is operator-tunable (the bearer's value is
/// never in the SSOT or this file, only where it lives).
const MANIFEST_X_MIOS: &str = "\"x-mios\":{\"ssot\":\"[ai] in /usr/share/mios/mios.toml\",\
\"served_by\":\"mios-agent-pipe.service\",\
\"auth\":{\"scheme\":\"Bearer\",\"env\":\"MIOS_AI_KEY\",\"source\":\"/etc/mios/hermes/api.env\"},\
\"note\":\"Minimal connection config for OpenAI-API-compatible clients. The agent surface refines the prompt, \
routes to a sub-agent, then polishes the reply -- clients see a single /v1 surface.\"}";

const USAGE: &str = "usage: mios-ai-config [--root DIR] [--check]\n";

fn die(msg: &str) -> ExitCode {
    eprintln!("mios-ai-config: {msg}");
    ExitCode::from(2)
}

#[derive(Debug, PartialEq)]
struct AiConfig {
    base_url: String,
    default_model: String,
    embed_model: String,
}

/// JSON string escaping for the handful of characters a TOML string can carry
/// that JSON cannot carry verbatim.
fn json_str(s: &str) -> String {
    let mut out = String::with_capacity(s.len() + 2);
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
    out
}

/// The shape the existing consumers read: four keys, one line. `api_key` is
/// always empty: the bearer lives in /etc/mios/hermes/api.env, not the SSOT.
fn render(cfg: &AiConfig, shape: Shape) -> String {
    let extra = match shape {
        Shape::Client => String::new(),
        Shape::Manifest => format!(",{MANIFEST_X_MIOS}"),
    };
    format!(
        "{{\"base_url\":{},\"default_model\":{},\"embed_model\":{},\"api_key\":\"\"{extra}}}\n",
        json_str(&cfg.base_url),
        json_str(&cfg.default_model),
        json_str(&cfg.embed_model)
    )
}

/// Expands canonical `${MIOS_PORTS_<KEY>}` and legacy port inputs against [ports].<key>. Any other
/// placeholder, or a port key [ports] does not declare, is refused: a literal
/// `${...}` in a URL a client dials is a broken config, not a default.
fn expand_ports(s: &str, ports: &toml::value::Table) -> Result<String, String> {
    let mut out = String::with_capacity(s.len());
    let mut rest = s;
    while let Some(start) = rest.find("${") {
        out.push_str(&rest[..start]);
        let after = &rest[start + 2..];
        let end = after
            .find('}')
            .ok_or_else(|| format!("unterminated placeholder in '{s}'"))?;
        let name = &after[..end];
        let key = name
            .strip_prefix("MIOS_PORTS_")
            .or_else(|| name.strip_prefix("MIOS_PORT_"))
            .ok_or_else(|| format!("placeholder ${{{name}}} in '{s}' is not a [ports] reference"))?
            .to_ascii_lowercase();
        let port = ports
            .get(&key)
            .and_then(|v| v.as_integer())
            .ok_or_else(|| {
                format!("[ports].{key} (from ${{{name}}}) is not declared as an integer")
            })?;
        out.push_str(&port.to_string());
        rest = &after[end + 1..];
    }
    out.push_str(rest);
    Ok(out)
}

fn str_key(ai: &toml::Value, key: &str) -> Result<String, String> {
    let v = ai
        .get(key)
        .and_then(|v| v.as_str())
        .ok_or_else(|| format!("[ai].{key} is missing or not a string"))?
        .trim()
        .to_string();
    if v.is_empty() {
        return Err(format!(
            "[ai].{key} is empty -- refusing to project a config no client can use"
        ));
    }
    Ok(v)
}

fn read_config(root: &Path) -> Result<AiConfig, String> {
    let path = root.join(SSOT);
    let text = std::fs::read_to_string(&path)
        .map_err(|e| format!("{SSOT} could not be read ({e}) -- nothing was projected"))?;
    let doc: toml::Value = text
        .parse()
        .map_err(|e| format!("{SSOT} did not parse ({e}) -- nothing was projected"))?;
    let ai = doc
        .get("ai")
        .ok_or_else(|| format!("{SSOT} has no [ai] -- nothing was projected"))?;
    let ports = doc
        .get("ports")
        .and_then(|p| p.as_table())
        .ok_or_else(|| format!("{SSOT} has no [ports] -- nothing was projected"))?;

    let base_url = expand_ports(&str_key(ai, "endpoint")?, ports)?;
    Ok(AiConfig {
        base_url,
        default_model: str_key(ai, "agent_model")?,
        embed_model: str_key(ai, "embed_model")?,
    })
}

fn main() -> ExitCode {
    let mut root = PathBuf::from(".");
    let mut check = false;
    let mut runtime_mode = None;
    let mut args = std::env::args().skip(1);
    while let Some(a) = args.next() {
        match a.as_str() {
            "--root" => match args.next() {
                Some(v) => root = PathBuf::from(v),
                None => return die("--root needs a directory"),
            },
            "--check" => check = true,
            "--probe" | "--opencode-stdin" => runtime_mode = Some(a),
            "-h" | "--help" => {
                print!("{USAGE}");
                return ExitCode::SUCCESS;
            }
            other => return die(&format!("unknown argument '{other}'\n{USAGE}")),
        }
    }

    if let Some(mode) = runtime_mode {
        let (endpoint, model) = match runtime::connection(&root) {
            Ok(v) => v,
            Err(e) => return die(&e),
        };
        if mode == "--probe" {
            return match runtime::probe(&endpoint, &model) {
                Ok(count) => {
                    println!("mios-ai-config: route ready: {endpoint}; selected model {model}; {count} advertised model(s); inference not tested");
                    ExitCode::SUCCESS
                }
                Err(e) => die(&e),
            };
        }
        let input = match serde_json::from_reader(std::io::stdin()) {
            Ok(v) => v,
            Err(e) => return die(&format!("invalid OpenCode input: {e}")),
        };
        return match runtime::opencode(input, &endpoint, &model) {
            Ok(value) => {
                println!("{value}");
                ExitCode::SUCCESS
            }
            Err(e) => die(&e),
        };
    }
    let cfg = match read_config(&root) {
        Ok(c) => c,
        Err(e) => return die(&e),
    };
    let mut stale = 0u8;
    for (rel, shape) in OUTPUTS {
        let want = render(&cfg, shape);
        let out = root.join(rel);
        if check {
            match std::fs::read_to_string(&out) {
                Ok(have) if have == want => println!(
                    "mios-ai-config: OK: {rel} matches [ai] + [ports] (base_url {})",
                    cfg.base_url
                ),
                Ok(have) => {
                    eprintln!(
                        "mios-ai-config: {rel} differs from its projection -- it was hand-edited, \
                         or the SSOT moved and it was not regenerated"
                    );
                    eprintln!("mios-ai-config:   have: {}", have.trim_end());
                    eprintln!("mios-ai-config:   want: {}", want.trim_end());
                    stale = 1;
                }
                Err(e) => {
                    eprintln!(
                        "mios-ai-config: {rel} is missing or unreadable ({e}) -- regenerate it"
                    );
                    stale = 1;
                }
            }
            continue;
        }
        if let Some(parent) = out.parent() {
            if let Err(e) = std::fs::create_dir_all(parent) {
                return die(&format!("{} could not be created ({e})", parent.display()));
            }
        }
        if let Err(e) = std::fs::write(&out, &want) {
            return die(&format!("{rel} could not be written ({e})"));
        }
        println!(
            "mios-ai-config: projected {rel} (base_url {})",
            cfg.base_url
        );
    }
    ExitCode::from(stale)
}

#[cfg(test)]
// Fixture setup panics on failure by design; the crate-level bans exist
// to keep the production paths from doing that.
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;
    use std::fs;

    fn root_with(ssot: &str) -> tempfile::TempDir {
        let d = tempfile::tempdir().expect("tempdir");
        fs::create_dir_all(d.path().join("usr/share/mios")).expect("mkdir");
        fs::write(d.path().join(SSOT), ssot).expect("write ssot");
        d
    }

    const GOOD: &str = "[ai]\nendpoint = \"http://localhost:${MIOS_PORTS_AGENT_PIPE}/v1\"\n\
                        agent_model = \"MiOS AI\"\nembed_model = \"nomic-embed-text\"\n\
                        [ports]\nagent_pipe = 8700\n";

    #[test]
    fn a_good_ssot_projects_the_current_port() {
        let d = root_with(GOOD);
        let c = read_config(d.path()).expect("must read");
        assert_eq!("http://localhost:8700/v1", c.base_url);
        assert_eq!("MiOS AI", c.default_model);
        assert_eq!("nomic-embed-text", c.embed_model);
    }

    #[test]
    fn render_keeps_the_consumer_shape_and_never_carries_a_key() {
        let out = render(
            &AiConfig {
                base_url: "http://localhost:8700/v1".into(),
                default_model: "MiOS AI".into(),
                embed_model: "nomic-embed-text".into(),
            },
            Shape::Client,
        );
        assert_eq!(
            "{\"base_url\":\"http://localhost:8700/v1\",\"default_model\":\"MiOS AI\",\
             \"embed_model\":\"nomic-embed-text\",\"api_key\":\"\"}\n",
            out
        );
    }

    #[test]
    fn the_manifest_copy_is_the_client_keys_plus_x_mios_and_parses() {
        let cfg = AiConfig {
            base_url: "http://localhost:8700/v1".into(),
            default_model: "MiOS AI".into(),
            embed_model: "nomic-embed-text".into(),
        };
        let client = render(&cfg, Shape::Client);
        let manifest = render(&cfg, Shape::Manifest);
        let prefix = client.trim_end().trim_end_matches('}');
        assert!(manifest.starts_with(prefix), "{manifest}");
        let v: serde_json::Value = serde_json::from_str(&manifest).expect("well-formed JSON");
        assert_eq!("mios-agent-pipe.service", v["x-mios"]["served_by"]);
        assert_eq!("MiOS AI", v["default_model"]);
        assert!(!manifest.contains("8640") && !manifest.contains("8642"));
    }

    #[test]
    fn render_escapes_json_specials() {
        let out = render(
            &AiConfig {
                base_url: "u".into(),
                default_model: "a\"b\\c".into(),
                embed_model: "e".into(),
            },
            Shape::Client,
        );
        assert!(out.contains("\"a\\\"b\\\\c\""), "{out}");
    }

    #[test]
    fn a_port_key_ports_does_not_declare_is_refused() {
        let d = root_with(
            "[ai]\nendpoint = \"http://localhost:${MIOS_PORT_NOPE}/v1\"\n\
             agent_model = \"m\"\nembed_model = \"e\"\n[ports]\nagent_pipe = 1\n",
        );
        let e = read_config(d.path()).expect_err("must refuse");
        assert!(e.contains("[ports].nope"), "{e}");
    }

    #[test]
    fn canonical_and_legacy_port_references_preserve_the_same_endpoint() {
        let table: toml::Table = "agent_pipe=9100".parse().expect("fixture");
        for prefix in ["MIOS_PORTS_", "MIOS_PORT_"] {
            let endpoint = format!("http://localhost:${{{prefix}AGENT_PIPE}}/v1");
            assert_eq!(
                expand_ports(&endpoint, &table).expect("declared port"),
                "http://localhost:9100/v1"
            );
            let missing = format!("http://localhost:${{{prefix}MISSING}}/v1");
            assert!(expand_ports(&missing, &table)
                .expect_err("missing port")
                .contains("[ports].missing"));
        }
    }

    #[test]
    fn a_non_port_placeholder_is_refused() {
        let d = root_with(
            "[ai]\nendpoint = \"http://${HOST}/v1\"\nagent_model = \"m\"\n\
             embed_model = \"e\"\n[ports]\n",
        );
        let e = read_config(d.path()).expect_err("must refuse");
        assert!(e.contains("not a [ports] reference"), "{e}");
    }

    #[test]
    fn an_empty_model_is_refused() {
        let d = root_with(
            "[ai]\nendpoint = \"http://h/v1\"\nagent_model = \"\"\nembed_model = \"e\"\n[ports]\n",
        );
        let e = read_config(d.path()).expect_err("must refuse");
        assert!(e.contains("agent_model is empty"), "{e}");
    }

    /// Absent is not clean: a missing table is cannot-run, never a pass.
    #[test]
    fn a_missing_ai_table_cannot_run() {
        let d = root_with("[ports]\nagent_pipe = 1\n");
        let e = read_config(d.path()).expect_err("must refuse");
        assert!(e.contains("no [ai]"), "{e}");
    }

    #[test]
    fn an_absent_ssot_cannot_run() {
        let d = tempfile::tempdir().expect("tempdir");
        let e = read_config(d.path()).expect_err("must refuse");
        assert!(e.contains("could not be read"), "{e}");
    }
}
