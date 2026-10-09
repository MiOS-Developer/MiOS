// AI-hint: Generates systemd Quadlet files (.pod, .container, .network, .volume, .image) from mios.toml SSOT (WS-7 pods-as-SSOT).
// AI-doc: usr/share/doc/mios/manual/tools.md

use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::{Path, PathBuf};

pub struct PodQuadletResult {
    pub active_units: usize,
    pub wrote: usize,
    pub out_dir: PathBuf,
    pub listed_files: Vec<String>,
    pub written_files: Vec<PathBuf>,
    pub removed_files: Vec<PathBuf>,
}

pub fn shlex_split(s: &str) -> Vec<String> {
    let mut words = Vec::new();
    let mut cur = String::new();
    let mut in_single = false;
    let mut in_double = false;
    let mut escape = false;

    for c in s.chars() {
        if escape {
            cur.push(c);
            escape = false;
        } else if c == '\\' && !in_single {
            escape = true;
        } else if c == '\'' && !in_double {
            in_single = !in_single;
        } else if c == '"' && !in_single {
            in_double = !in_double;
        } else if c.is_whitespace() && !in_single && !in_double {
            if !cur.is_empty() {
                words.push(cur);
                cur = String::new();
            }
        } else {
            cur.push(c);
        }
    }
    if !cur.is_empty() {
        words.push(cur);
    }
    words
}

pub fn load_placeholders(doc: &toml::Value) -> BTreeSet<String> {
    let mut phs = BTreeSet::new();
    phs.insert("FEDORA_VERSION".to_string());
    phs.insert("MIOS_VERSION".to_string());
    if let Some(gen_tbl) = doc.get("generator").and_then(|v| v.as_table()) {
        if let Some(toml::Value::Array(arr)) = gen_tbl.get("placeholders") {
            for item in arr {
                if let Some(s) = item.as_str() {
                    phs.insert(s.to_string());
                }
            }
        }
    }
    phs
}

pub fn load_sidecars(doc: &toml::Value) -> BTreeMap<String, String> {
    let mut sidecars = BTreeMap::new();
    if let Some(img_tbl) = doc.get("image").and_then(|v| v.as_table()) {
        if let Some(sc_tbl) = img_tbl.get("sidecars").and_then(|v| v.as_table()) {
            for (k, v) in sc_tbl {
                if let Some(s) = v.as_str() {
                    sidecars.insert(k.clone(), s.to_string());
                }
            }
        }
    }
    sidecars
}

pub fn load_ports(doc: &toml::Value) -> BTreeMap<String, toml::Value> {
    doc.get("ports")
        .and_then(|v| v.as_table())
        .map(|t| t.iter().map(|(k, v)| (k.clone(), v.clone())).collect())
        .unwrap_or_default()
}

pub fn load_enabled_quadlets(doc: &toml::Value) -> BTreeMap<String, bool> {
    let mut map = BTreeMap::new();
    if let Some(q_tbl) = doc.get("quadlets").and_then(|v| v.as_table()) {
        if let Some(en_tbl) = q_tbl.get("enable").and_then(|v| v.as_table()) {
            for (k, v) in en_tbl {
                if let Some(b) = v.as_bool() {
                    map.insert(k.clone(), b);
                }
            }
        }
    }
    map
}

pub fn load_user_scope(doc: &toml::Value) -> BTreeSet<String> {
    let mut set = BTreeSet::new();
    if let Some(q_tbl) = doc.get("quadlets").and_then(|v| v.as_table()) {
        if let Some(scope_tbl) = q_tbl.get("scope").and_then(|v| v.as_table()) {
            if let Some(toml::Value::Array(arr)) = scope_tbl.get("user") {
                for item in arr {
                    if let Some(s) = item.as_str() {
                        set.insert(s.to_string());
                    }
                }
            }
        }
    }
    set
}

pub fn load_privileged_root(doc: &toml::Value) -> BTreeSet<String> {
    let mut allowed = BTreeSet::new();
    if let Some(sec_tbl) = doc.get("security").and_then(|v| v.as_table()) {
        if let Some(priv_tbl) = sec_tbl
            .get("privileged_quadlets")
            .and_then(|v| v.as_table())
        {
            if let Some(toml::Value::Array(arr)) = priv_tbl.get("root") {
                for item in arr {
                    let raw = match item {
                        toml::Value::String(s) => s.as_str(),
                        _ => continue,
                    };
                    let cleaned = raw.split('#').next().unwrap_or("").trim();
                    if !cleaned.is_empty() {
                        allowed.insert(cleaned.to_string());
                        if let Some(rest) = cleaned.strip_suffix(".container") {
                            allowed.insert(rest.to_string());
                        }
                    }
                }
            }
        }
    }
    allowed
}

pub fn load_grandfathered_credentials(doc: &toml::Value) -> BTreeSet<String> {
    let mut set = BTreeSet::new();
    if let Some(sec_tbl) = doc.get("security").and_then(|v| v.as_table()) {
        if let Some(cred_tbl) = sec_tbl
            .get("credential_literals")
            .and_then(|v| v.as_table())
        {
            if let Some(toml::Value::Array(arr)) = cred_tbl.get("grandfathered") {
                for item in arr {
                    if let Some(s) = item.as_str() {
                        let trimmed = s.trim();
                        if !trimmed.is_empty() {
                            set.insert(trimmed.to_string());
                        }
                    }
                }
            }
        }
    }
    set
}

pub fn load_secret_keys(doc: &toml::Value) -> BTreeSet<String> {
    let mut set = BTreeSet::new();
    if let Some(sec_tbl) = doc.get("security").and_then(|v| v.as_table()) {
        if let Some(sk_tbl) = sec_tbl.get("secret_keys").and_then(|v| v.as_table()) {
            if let Some(toml::Value::Array(arr)) = sk_tbl.get("keys") {
                for item in arr {
                    if let Some(s) = item.as_str() {
                        let trimmed = s.trim();
                        if !trimmed.is_empty() {
                            set.insert(trimmed.to_string());
                        }
                    }
                }
            }
        }
    }
    set
}

pub fn sidecar_image<'a>(
    var_name: &str,
    sidecars: &'a BTreeMap<String, String>,
) -> Option<&'a str> {
    if let Some(rest) = var_name.strip_prefix("MIOS_") {
        if let Some(base) = rest.strip_suffix("_IMAGE") {
            let key = base.to_lowercase();
            if let Some(val) = sidecars.get(&key) {
                if !val.is_empty() {
                    return Some(val.as_str());
                }
            }
        }
    }
    None
}

pub fn ssot_expand(text: &str, ssot_exports: &BTreeMap<String, String>) -> String {
    let mut out = String::new();
    let mut i = 0;
    while let Some(start) = text[i..].find("${") {
        let abs_start = i + start;
        let mut depth = 0;
        let mut j = abs_start;
        let bytes = text.as_bytes();
        while j < bytes.len() {
            if text[j..].starts_with("${") {
                depth += 1;
                j += 2;
                continue;
            }
            if bytes[j] == b'}' {
                depth -= 1;
                if depth == 0 {
                    break;
                }
            }
            j += 1;
        }
        out.push_str(&text[i..abs_start]);
        if depth != 0 {
            out.push_str("${");
            i = abs_start + 2;
            continue;
        }
        let inner = &text[abs_start + 2..j];
        let (name, default) = match inner.find(":-") {
            Some(idx) => (&inner[..idx], Some(&inner[idx + 2..])),
            None => (inner, None),
        };
        if let Some(val) = ssot_exports.get(name) {
            out.push_str(val);
        } else if let Some(def) = default {
            out.push_str(&ssot_expand(def, ssot_exports));
        } else {
            out.push_str(&format!("${{{inner}}}"));
        }
        i = j + 1;
    }
    out.push_str(&text[i..]);
    out
}

pub fn resolve_one(
    inner: &str,
    placeholders: &BTreeSet<String>,
    ssot_exports: &BTreeMap<String, String>,
    sidecars: &BTreeMap<String, String>,
) -> Result<String, String> {
    let (name, sep, default) = match inner.find(":-") {
        Some(idx) => (&inner[..idx], true, &inner[idx + 2..]),
        None => (inner, false, ""),
    };
    if name.is_empty() || !name.chars().all(|c| c.is_ascii_alphanumeric() || c == '_') {
        return Ok(format!("${{{inner}}}"));
    }
    if name.starts_with("MIOS_PORTS_")
        || name.starts_with("MIOS_PORT_")
        || placeholders.contains(name)
    {
        let def_expanded = if sep {
            format!(
                ":-{}",
                expand_str(default, placeholders, ssot_exports, sidecars)?
            )
        } else {
            String::new()
        };
        return Ok(format!("${{{name}{def_expanded}}}"));
    }
    let ssot = ssot_exports.get(name).cloned().unwrap_or_default();
    if !ssot.is_empty()
        && sep
        && (default.contains("${MIOS_PORTS_") || default.contains("${MIOS_PORT_"))
    {
        let expanded_default = ssot_expand(default, ssot_exports);
        if expanded_default != ssot {
            return Err(format!(
                "{name}: SSOT {ssot:?} != template {default:?} ({expanded_default:?})"
            ));
        }
        return expand_str(default, placeholders, ssot_exports, sidecars);
    }
    if !ssot.is_empty() {
        return Ok(ssot);
    }
    if let Ok(env_val) = std::env::var(name) {
        if !env_val.is_empty() {
            return Ok(env_val);
        }
    }
    if let Some(img) = sidecar_image(name, sidecars) {
        return expand_str(img, placeholders, ssot_exports, sidecars);
    }
    if sep {
        expand_str(default, placeholders, ssot_exports, sidecars)
    } else {
        Ok(format!("${{{name}}}"))
    }
}

pub fn expand_str(
    text: &str,
    placeholders: &BTreeSet<String>,
    ssot_exports: &BTreeMap<String, String>,
    sidecars: &BTreeMap<String, String>,
) -> Result<String, String> {
    let mut out = String::new();
    let mut i = 0;
    while let Some(start) = text[i..].find("${") {
        let abs_start = i + start;
        let mut depth = 0;
        let mut j = abs_start;
        let bytes = text.as_bytes();
        while j < bytes.len() {
            if text[j..].starts_with("${") {
                depth += 1;
                j += 2;
                continue;
            }
            if bytes[j] == b'}' {
                depth -= 1;
                if depth == 0 {
                    break;
                }
            }
            j += 1;
        }
        out.push_str(&text[i..abs_start]);
        if depth != 0 {
            out.push_str("${");
            i = abs_start + 2;
            continue;
        }
        let inner = &text[abs_start + 2..j];
        out.push_str(&resolve_one(inner, placeholders, ssot_exports, sidecars)?);
        i = j + 1;
    }
    out.push_str(&text[i..]);
    Ok(out)
}

pub fn wrap_doc(doc: &str, width: usize) -> Vec<String> {
    let mut out = Vec::new();
    for para in doc.split('\n') {
        let words: Vec<&str> = para.split_whitespace().collect();
        if words.is_empty() {
            out.push("#".to_string());
            continue;
        }
        let mut line = "#".to_string();
        for w in words {
            if line.len() + 1 + w.len() > width && line != "#" {
                out.push(line);
                line = format!("# {w}");
            } else if line != "#" {
                line.push(' ');
                line.push_str(w);
            } else {
                line = format!("# {w}");
            }
        }
        out.push(line);
    }
    out
}

pub fn resolve_port(port_str: &str, ports: &BTreeMap<String, toml::Value>) -> String {
    let parts: Vec<&str> = port_str.split(':').collect();
    let mut resolved_parts = Vec::new();
    for p in parts {
        let p_clean = p.trim();
        let target = if p_clean.starts_with("${") && p_clean.ends_with('}') {
            let inner = &p_clean[2..p_clean.len() - 1];
            if let Some(rest) = inner.strip_prefix("ports.") {
                rest
            } else {
                inner
            }
        } else {
            p_clean
        };
        if let Some(val) = ports.get(target) {
            resolved_parts.push(match val {
                toml::Value::Integer(i) => i.to_string(),
                toml::Value::String(s) => s.clone(),
                other => other.to_string(),
            });
        } else {
            resolved_parts.push(p.to_string());
        }
    }
    resolved_parts.join(":")
}

pub fn is_credential_key(key: &str, explicit_secrets: Option<&BTreeSet<String>>) -> bool {
    if let Some(explicit) = explicit_secrets {
        if explicit.contains(key) {
            return true;
        }
    }
    let k_upper = key.to_uppercase();
    let looks = k_upper.contains("PASSWORD")
        || k_upper.contains("SECRET")
        || k_upper.contains("APIKEY")
        || k_upper.contains("API_KEY")
        || k_upper.contains("TOKEN");
    if !looks {
        return false;
    }
    let not_cred = k_upper.contains("MAX_TOKENS")
        || k_upper.ends_with("_TOKENS")
        || k_upper.starts_with("ENABLE_")
        || k_upper.ends_with("_ENABLED")
        || k_upper.contains("NUM_")
        || k_upper.ends_with("_LIMIT");
    !not_cred
}

pub fn is_literal_value(val: &str) -> bool {
    if val.is_empty() {
        return false;
    }
    let clean = val.trim_matches(|c| c == '\'' || c == '"');
    if clean.is_empty() || clean.starts_with("${") || clean.starts_with('%') {
        return false;
    }
    let lower = clean.to_lowercase();
    if lower == "true" || lower == "false" {
        return false;
    }
    if clean.chars().all(|c| c.is_ascii_digit()) {
        return false;
    }
    true
}

pub fn is_root_id(val: &str) -> bool {
    let s = val.trim().to_lowercase();
    s == "0" || s == "root" || s.starts_with("0:") || s.starts_with("root:")
}

pub fn validate_environment_entry(
    name: &str,
    unit_type: &str,
    env_str: &str,
    grandfathered: Option<&BTreeSet<String>>,
    explicit_secrets: Option<&BTreeSet<String>>,
) -> Result<(), String> {
    if !env_str.contains('=') {
        return Ok(());
    }
    let (k, v) = match env_str.split_once('=') {
        Some((k, v)) => (k.trim(), v.trim()),
        None => return Ok(()),
    };
    if is_credential_key(k, explicit_secrets) && is_literal_value(v) {
        let unit_target = format!("usr/share/containers/systemd/{name}.{unit_type}:{k}={v}");
        let alt_target = format!("{name}.{unit_type}:{k}={v}");
        let clean_v = v.trim_matches(|c| c == '\'' || c == '"');
        let unit_target_clean =
            format!("usr/share/containers/systemd/{name}.{unit_type}:{k}={clean_v}");
        let alt_target_clean = format!("{name}.{unit_type}:{k}={clean_v}");

        if let Some(gf) = grandfathered {
            if gf.contains(&unit_target)
                || gf.contains(&alt_target)
                || gf.contains(&unit_target_clean)
                || gf.contains(&alt_target_clean)
            {
                return Ok(());
            }
        }
        return Err(format!(
            "Quadlet '{name}.{unit_type}' attempts to emit non-placeholder password literal '{k}={v}' into /usr/share/containers/systemd/ (Law 11). Quadlets must emit secret references (e.g. EnvironmentFile=/etc/mios/secrets.env) or placeholder tokens rather than plaintext in /usr."
        ));
    }
    Ok(())
}

pub fn render_pod_quadlet(
    name: &str,
    spec: &toml::Value,
    ports: Option<&BTreeMap<String, toml::Value>>,
    placeholders: &BTreeSet<String>,
    ssot_exports: &BTreeMap<String, String>,
    sidecars: &BTreeMap<String, String>,
) -> Result<String, String> {
    let desc = match spec.get("description").and_then(|v| v.as_str()) {
        Some(s) => expand_str(s, placeholders, ssot_exports, sidecars)?,
        None => format!("MiOS {name} pod"),
    };
    let network = match spec.get("network").and_then(|v| v.as_str()) {
        Some(s) => expand_str(s, placeholders, ssot_exports, sidecars)?,
        None => "host".to_string(),
    };

    let mut after = Vec::new();
    if let Some(toml::Value::Array(arr)) = spec.get("after") {
        for item in arr {
            if let Some(s) = item.as_str() {
                after.push(expand_str(s, placeholders, ssot_exports, sidecars)?);
            }
        }
    }

    let mut wants = Vec::new();
    if let Some(toml::Value::Array(arr)) = spec.get("wants") {
        for item in arr {
            if let Some(s) = item.as_str() {
                wants.push(expand_str(s, placeholders, ssot_exports, sidecars)?);
            }
        }
    }

    let mut wanted_by = Vec::new();
    if let Some(toml::Value::Array(arr)) = spec.get("wanted_by") {
        for item in arr {
            if let Some(s) = item.as_str() {
                wanted_by.push(expand_str(s, placeholders, ssot_exports, sidecars)?);
            }
        }
    } else {
        wanted_by.push("multi-user.target".to_string());
    }

    let mut publish_ports = Vec::new();
    if let Some(toml::Value::Array(arr)) = spec.get("publish_ports") {
        for item in arr {
            if let Some(s) = item.as_str() {
                let expanded = expand_str(s, placeholders, ssot_exports, sidecars)?;
                let resolved = if let Some(p) = ports {
                    resolve_port(&expanded, p)
                } else {
                    expanded
                };
                publish_ports.push(resolved);
            }
        }
    }

    let mut members = Vec::new();
    if let Some(toml::Value::Array(arr)) = spec.get("members") {
        for item in arr {
            let raw = match item {
                toml::Value::String(s) => s.as_str(),
                _ => continue,
            };
            let cleaned = raw.split('#').next().unwrap_or("").trim();
            if !cleaned.is_empty() {
                members.push(cleaned.to_string());
            }
        }
    }

    let mut lines = Vec::new();
    lines.push(format!(
        "# AI-hint: GENERATED Quadlet pod for the co-resident group '{name}' \
        (WS-7 pods-as-SSOT). DO NOT EDIT -- regenerate via \
        tools/generate-pod-quadlets.py from [pods.{name}] in mios.toml. \
        Members ({}): {}.",
        members.len(),
        members.join(", ")
    ));
    let related: Vec<String> = members.iter().map(|m| format!("{m}.container")).collect();
    lines.push(format!(
        "# AI-related: usr/share/mios/mios.toml, tools/generate-pod-quadlets.py, {}",
        related.join(", ")
    ));
    lines.push(format!("# /usr/share/containers/systemd/{name}.pod"));
    if let Some(doc) = spec.get("doc").and_then(|v| v.as_str()) {
        lines.extend(wrap_doc(doc, 76));
    }
    if !members.is_empty() {
        lines.push(format!(
            "# Members (each member .container declares Pod={name}.pod):"
        ));
        for m in &members {
            lines.push(format!("#   {m}"));
        }
    }
    lines.push("[Unit]".to_string());
    lines.push(format!("Description={desc}"));
    if !after.is_empty() {
        lines.push(format!("After={}", after.join(" ")));
    }
    if !wants.is_empty() {
        lines.push(format!("Wants={}", wants.join(" ")));
    }
    lines.push(String::new());
    lines.push("[Pod]".to_string());
    lines.push(format!("PodName={name}"));
    lines.push(format!("Network={network}"));
    for p in &publish_ports {
        lines.push(format!("PublishPort={p}"));
    }
    lines.push(String::new());
    lines.push("[Install]".to_string());
    lines.push(format!("WantedBy={}", wanted_by.join(" ")));
    Ok(lines.join("\n") + "\n")
}

// Nine context parameters is the projection's real shape (SSOT-derived sets
// plus the render sidecars); grouping them would obscure the call sites.
#[allow(clippy::too_many_arguments)]
pub fn render_nested_quadlet(
    name: &str,
    spec: &toml::Value,
    unit_type: &str,
    allowed_root: &BTreeSet<String>,
    grandfathered_creds: &BTreeSet<String>,
    secret_keys: &BTreeSet<String>,
    placeholders: &BTreeSet<String>,
    ssot_exports: &BTreeMap<String, String>,
    sidecars: &BTreeMap<String, String>,
) -> Result<String, String> {
    let is_auth_root = allowed_root.contains(name)
        || allowed_root.contains(&format!("{name}.{unit_type}"))
        || allowed_root.contains(&format!("{name}.container"))
        || (name.contains('@') && {
            let base = name.split('@').next().unwrap_or(name);
            allowed_root.contains(&format!("{base}@"))
                || allowed_root.contains(&format!("{base}@.container"))
        });

    let mut lines = Vec::new();
    let mut declares_user = false;
    let desc = spec
        .get("description")
        .and_then(|v| v.as_str())
        .map(|s| s.to_string())
        .unwrap_or_else(|| format!("MiOS {name} {unit_type}"));
    lines.push(format!("# AI-hint: {desc}. (WS-7 pods-as-SSOT)."));
    lines.push(format!(
        "# DO NOT EDIT -- regenerate via \
        tools/generate-pod-quadlets.py from [{unit_type}s.{name}] in mios.toml."
    ));
    lines.push(format!(
        "# /usr/share/containers/systemd/{name}.{unit_type}"
    ));
    if name == "mios-llm-heavy-alt" && unit_type == "container" {
        lines.push("# DEPRECATED (Part 10): retire by setting [converge.inference].retire_heavy_alt = true and running the migration guide at usr/share/doc/mios/guides/inference-consolidation.md.".to_string());
    }

    let main_section = match unit_type {
        "container" => "Container",
        "network" => "Network",
        "volume" => "Volume",
        "image" => "Image",
        other => other,
    };

    let mut sections: Vec<(&String, &toml::Value)> = match spec.as_table() {
        Some(t) => t.iter().collect(),
        None => Vec::new(),
    };
    sections.sort_by(|(a, _), (b, _)| {
        let key_a = match a.to_lowercase().as_str() {
            "unit" => (0, a.as_str()),
            s if s == main_section.to_lowercase() => (1, a.as_str()),
            "install" => (2, a.as_str()),
            _ => (3, a.as_str()),
        };
        let key_b = match b.to_lowercase().as_str() {
            "unit" => (0, b.as_str()),
            s if s == main_section.to_lowercase() => (1, b.as_str()),
            "install" => (2, b.as_str()),
            _ => (3, b.as_str()),
        };
        key_a.cmp(&key_b)
    });

    for (sec, sec_data_val) in sections {
        let sec_data = match sec_data_val.as_table() {
            Some(t) => t,
            None => continue,
        };
        lines.push(String::new());
        lines.push(format!("[{sec}]"));
        let mut keys: Vec<(&String, &toml::Value)> = sec_data.iter().collect();
        keys.sort_by_key(|(k, _)| *k);
        for (k, val) in keys {
            match val {
                toml::Value::Array(arr) => {
                    for item in arr {
                        let item_str = match item {
                            toml::Value::String(s) => s.clone(),
                            toml::Value::Boolean(b) => {
                                if *b {
                                    "true".to_string()
                                } else {
                                    "false".to_string()
                                }
                            }
                            toml::Value::Integer(i) => i.to_string(),
                            other => other.to_string(),
                        };
                        let resolved_item =
                            expand_str(&item_str, placeholders, ssot_exports, sidecars)?;
                        if (k == "User" || k == "Group")
                            && is_root_id(&resolved_item)
                            && unit_type == "container"
                            && !is_auth_root
                        {
                            return Err(format!(
                                "Container '{name}' attempts {k}={resolved_item} but is not listed in [security.privileged_quadlets].root in mios.toml (Law 6)"
                            ));
                        }
                        if k == "Environment" && unit_type == "container" {
                            validate_environment_entry(
                                name,
                                unit_type,
                                &resolved_item,
                                Some(grandfathered_creds),
                                Some(secret_keys),
                            )?;
                        }
                        if k == "User" && sec == "Container" && !resolved_item.trim().is_empty() {
                            declares_user = true;
                        }
                        lines.push(format!("{k}={resolved_item}"));
                    }
                }
                toml::Value::Boolean(b) => {
                    lines.push(format!("{k}={}", if *b { "true" } else { "false" }));
                }
                other => {
                    let val_str = match other {
                        toml::Value::String(s) => s.clone(),
                        toml::Value::Integer(i) => i.to_string(),
                        toml::Value::Float(f) => f.to_string(),
                        _ => other.to_string(),
                    };
                    let resolved_val = expand_str(&val_str, placeholders, ssot_exports, sidecars)?;
                    if k == "Image" && resolved_val.is_empty() {
                        continue;
                    }
                    if (k == "User" || k == "Group") && resolved_val.is_empty() {
                        continue;
                    }
                    if (k == "User" || k == "Group")
                        && is_root_id(&resolved_val)
                        && unit_type == "container"
                        && !is_auth_root
                    {
                        return Err(format!(
                            "Container '{name}' attempts {k}={resolved_val} but is not listed in [security.privileged_quadlets].root in mios.toml (Law 6)"
                        ));
                    }
                    if k == "Environment" && unit_type == "container" {
                        validate_environment_entry(
                            name,
                            unit_type,
                            &resolved_val,
                            Some(grandfathered_creds),
                            Some(secret_keys),
                        )?;
                    }
                    if k == "User" && sec == "Container" {
                        declares_user = true;
                    }
                    lines.push(format!("{k}={resolved_val}"));
                }
            }
        }
    }

    if unit_type == "container" && !declares_user && !is_auth_root {
        return Err(format!(
            "Container '{name}' declares no User=/Group= and is not listed in [security.privileged_quadlets].root (Law 6)"
        ));
    }

    Ok(lines.join("\n").trim().to_string() + "\n")
}

pub fn apply_bound_image_store(
    containers: &mut toml::map::Map<String, toml::Value>,
    bake: &toml::Value,
    user_scope: &BTreeSet<String>,
    placeholders: &BTreeSet<String>,
    ssot_exports: &BTreeMap<String, String>,
    sidecars: &BTreeMap<String, String>,
) -> Result<(), String> {
    // Absent or "" disables the projection. Any other non-string (`false`) is a
    // malformed setting, not a disabled one: silently treating it as missing
    // dropped the store from every bound unit.
    let store = match bake.get("additional_image_store") {
        None => "",
        Some(toml::Value::String(s)) => s.as_str(),
        Some(_) => {
            return Err(
                "[build.bake].additional_image_store must be an absolute path".to_string(),
            )
        }
    };
    if store.is_empty() {
        return Ok(());
    }
    if !store.starts_with('/') || store.chars().any(|c| c.is_whitespace()) {
        return Err("[build.bake].additional_image_store must be an absolute path".to_string());
    }
    let tokens: Vec<String> = match bake.get("firstboot_tokens") {
        Some(toml::Value::Array(arr)) => {
            let mut list = Vec::new();
            for item in arr {
                match item.as_str() {
                    Some(s) => list.push(s.to_string()),
                    None => {
                        return Err(
                            "[build.bake].firstboot_tokens must be a string array".to_string()
                        )
                    }
                }
            }
            list
        }
        Some(_) => return Err("[build.bake].firstboot_tokens must be a string array".to_string()),
        None => Vec::new(),
    };
    let wanted = format!("--storage-opt=additionalimagestore={store}");

    for (name, spec_val) in containers.iter_mut() {
        let spec = match spec_val.as_table_mut() {
            Some(t) => t,
            None => continue,
        };
        let section = match spec.get_mut("Container").and_then(|v| v.as_table_mut()) {
            Some(t) => t,
            None => continue,
        };
        let image_raw = match section.get("Image").and_then(|v| v.as_str()) {
            Some(s) => s,
            None => continue,
        };
        let image = expand_str(image_raw, placeholders, ssot_exports, sidecars)?;

        let mut args: Vec<String> = Vec::new();
        if let Some(current) = section.get("GlobalArgs") {
            match current {
                toml::Value::String(s) => args.push(s.clone()),
                toml::Value::Array(arr) => {
                    for item in arr {
                        match item.as_str() {
                            Some(s) => args.push(s.to_string()),
                            None => {
                                return Err(format!(
                                    "{name}: GlobalArgs must be a string or string array"
                                ))
                            }
                        }
                    }
                }
                _ => {
                    return Err(format!(
                        "{name}: GlobalArgs must be a string or string array"
                    ))
                }
            }
        }

        let words: Vec<String> = args.iter().flat_map(|arg| shlex_split(arg)).collect();
        let mut existing: Vec<String> = Vec::new();
        let mut idx = 0;
        while idx < words.len() {
            let word = &words[idx];
            if let Some(rest) = word.strip_prefix("--storage-opt=additionalimagestore=") {
                existing.push(rest.to_string());
            } else if word == "--storage-opt" && idx + 1 < words.len() {
                let opt = &words[idx + 1];
                if let Some(rest) = opt.strip_prefix("additionalimagestore=") {
                    existing.push(rest.to_string());
                }
            }
            idx += 1;
        }

        if tokens
            .iter()
            .any(|tok| !tok.is_empty() && image.contains(tok))
        {
            if !existing.is_empty() {
                return Err(format!(
                    "{name}: firstboot image cannot use bootc additional image store"
                ));
            }
            continue;
        }
        if user_scope.contains(name) {
            if !existing.is_empty() {
                return Err(format!(
                    "{name}: user-scope unit cannot use bootc additional image store"
                ));
            }
            continue;
        }
        if !existing.is_empty() && existing != vec![store.to_string()] {
            return Err(format!(
                "{name}: conflicting bootc additional image store {:?}",
                existing
            ));
        }
        if existing.is_empty() {
            args.push(wanted.clone());
            section.insert(
                "GlobalArgs".to_string(),
                toml::Value::Array(args.into_iter().map(toml::Value::String).collect()),
            );
        }
    }
    Ok(())
}

pub fn run_pod_quadlets(
    root: &Path,
    check: bool,
    list_mode: bool,
) -> Result<PodQuadletResult, String> {
    let out_dir = match std::env::var("MIOS_POD_OUT") {
        Ok(p) => PathBuf::from(p),
        Err(_) => root.join("usr/share/containers/systemd"),
    };

    let vendor = mios_resolver::layers::resolve_tier_dirs(Some(root)).0;
    if !vendor.is_file() {
        return Err(format!(
            "cannot read {}: vendor SSOT is missing",
            vendor.display()
        ));
    }
    let doc = mios_resolver::resolve_merged(Some(root), false).map_err(|e| e.to_string())?;

    let placeholders = load_placeholders(&doc);
    let sidecars = load_sidecars(&doc);
    let ports = load_ports(&doc);
    let enabled_map = load_enabled_quadlets(&doc);
    let user_scope = load_user_scope(&doc);
    let privileged_root = load_privileged_root(&doc);
    let grandfathered_creds = load_grandfathered_credentials(&doc);
    let secret_keys = load_secret_keys(&doc);

    let stack_offset = mios_resolver::stack_offset_of(&doc);
    let mut ssot_exports = mios_resolver::emit::build_exports_map(&doc, stack_offset);
    mios_resolver::emit::resolve_cross_references(&mut ssot_exports);

    let mut pods = doc
        .get("pods")
        .and_then(|v| v.as_table())
        .cloned()
        .unwrap_or_default();
    let mut containers = doc
        .get("containers")
        .and_then(|v| v.as_table())
        .cloned()
        .unwrap_or_default();
    let networks = doc
        .get("networks")
        .or_else(|| doc.get("network"))
        .and_then(|v| v.as_table())
        .cloned()
        .unwrap_or_default();
    let volumes = doc
        .get("volumes")
        .or_else(|| doc.get("volume"))
        .and_then(|v| v.as_table())
        .cloned()
        .unwrap_or_default();
    let images = doc
        .get("images")
        .or_else(|| doc.get("image"))
        .and_then(|v| v.as_table())
        .cloned()
        .unwrap_or_default();

    for name in enabled_map.keys() {
        if !containers.contains_key(name) {
            return Err(format!(
                "[pod-gen] ERROR: key '{name}' in [quadlets.enable] does not map to any container in [containers]"
            ));
        }
    }

    if pods.is_empty()
        && containers.is_empty()
        && networks.is_empty()
        && volumes.is_empty()
        && images.is_empty()
    {
        println!("[pod-gen] no Quadlets in SSOT -- nothing to do");
        return Ok(PodQuadletResult {
            active_units: 0,
            wrote: 0,
            out_dir,
            listed_files: Vec::new(),
            written_files: Vec::new(),
            removed_files: Vec::new(),
        });
    }

    for (_, pod_spec) in pods.iter_mut() {
        if let Some(toml::Value::Array(members_arr)) = pod_spec.get_mut("members") {
            let mut filtered = Vec::new();
            for m in members_arr.iter() {
                let m_name = match m.as_str() {
                    Some(s) => s.split('#').next().unwrap_or("").trim(),
                    None => continue,
                };
                if enabled_map.get(m_name) != Some(&false) {
                    filtered.push(m.clone());
                }
            }
            members_arr.clear();
            members_arr.extend(filtered);
        }
    }

    if let Some(bake) = doc.get("build").and_then(|v| v.get("bake")) {
        apply_bound_image_store(
            &mut containers,
            bake,
            &user_scope,
            &placeholders,
            &ssot_exports,
            &sidecars,
        )?;
    }

    if !check && !list_mode {
        fs::create_dir_all(&out_dir)
            .map_err(|e| format!("cannot create {}: {e}", out_dir.display()))?;
    }

    let mut drift = 0;
    let mut wrote = 0;
    let mut member_miss = 0;
    let mut active_units = 0;
    let mut generated_files = BTreeSet::new();
    let mut written_files = Vec::new();
    let mut removed_files = Vec::new();

    let sorted_pods: Vec<(&String, &toml::Value)> = {
        let mut p: Vec<_> = pods.iter().collect();
        p.sort_by_key(|(k, _)| *k);
        p
    };

    for (name, spec) in sorted_pods {
        let text = render_pod_quadlet(
            name,
            spec,
            Some(&ports),
            &placeholders,
            &ssot_exports,
            &sidecars,
        )?;
        let out = out_dir.join(format!("{name}.pod"));
        generated_files.insert(format!("{name}.pod"));

        if let Some(toml::Value::Array(members)) = spec.get("members") {
            for item in members {
                let m = match item.as_str() {
                    Some(s) => s.split('#').next().unwrap_or("").trim(),
                    None => continue,
                };
                if !m.is_empty() && !out_dir.join(format!("{m}.container")).exists() {
                    if !list_mode {
                        eprintln!("[pod-gen] WARN {name}: member {m}.container missing");
                    }
                    member_miss += 1;
                }
            }
        }
        active_units += 1;

        if check {
            let cur = if out.exists() {
                fs::read_to_string(&out).unwrap_or_default()
            } else {
                String::new()
            };
            if cur.replace("\r\n", "\n") != text.replace("\r\n", "\n") {
                eprintln!(
                    "[pod-gen] DRIFT {} (regenerate via tools/generate-pod-quadlets.py)",
                    out.display()
                );
                drift += 1;
            }
            continue;
        }

        if !list_mode {
            fs::write(&out, &text)
                .map_err(|e| format!("failed to write {}: {e}", out.display()))?;
            wrote += 1;
            written_files.push(out);
        }
    }

    let categories: [(&toml::map::Map<String, toml::Value>, &str); 4] = [
        (&containers, "container"),
        (&networks, "network"),
        (&volumes, "volume"),
        (&images, "image"),
    ];

    for (specs, unit_type) in categories {
        let sorted_specs: Vec<(&String, &toml::Value)> = {
            let mut s: Vec<_> = specs.iter().collect();
            s.sort_by_key(|(k, _)| *k);
            s
        };

        for (name, spec) in sorted_specs {
            if unit_type == "container" && enabled_map.get(name) == Some(&false) {
                let out = out_dir.join(format!("{name}.{unit_type}"));
                if check {
                    if out.exists() {
                        eprintln!(
                            "[pod-gen] DRIFT {} should not exist (disabled in SSOT)",
                            out.display()
                        );
                        drift += 1;
                    }
                } else if out.exists() && !list_mode {
                    let _ = fs::remove_file(&out);
                    removed_files.push(out);
                }
                continue;
            }

            let text = render_nested_quadlet(
                name,
                spec,
                unit_type,
                &privileged_root,
                &grandfathered_creds,
                &secret_keys,
                &placeholders,
                &ssot_exports,
                &sidecars,
            )?;
            let mut out = out_dir.join(format!("{name}.{unit_type}"));
            if name.as_str() == "mios-sunshine" || user_scope.contains(name) {
                if !check && !list_mode && out.exists() {
                    let _ = fs::remove_file(&out);
                    removed_files.push(out);
                }
                out = out_dir.join("users").join(format!("{name}.{unit_type}"));
                if !check && !list_mode {
                    let _ = fs::create_dir_all(out.parent().unwrap());
                }
            } else {
                generated_files.insert(format!("{name}.{unit_type}"));
            }
            active_units += 1;

            if check {
                let cur = if out.exists() {
                    fs::read_to_string(&out).unwrap_or_default()
                } else {
                    String::new()
                };
                if cur.replace("\r\n", "\n") != text.replace("\r\n", "\n") {
                    eprintln!(
                        "[pod-gen] DRIFT {} (regenerate via tools/generate-pod-quadlets.py)",
                        out.display()
                    );
                    drift += 1;
                }
                continue;
            }

            if !list_mode {
                fs::write(&out, &text)
                    .map_err(|e| format!("failed to write {}: {e}", out.display()))?;
                wrote += 1;
                written_files.push(out);
            }
        }
    }

    let listed_files: Vec<String> = generated_files.iter().cloned().collect();

    if list_mode {
        return Ok(PodQuadletResult {
            active_units,
            wrote,
            out_dir,
            listed_files,
            written_files,
            removed_files,
        });
    }

    if check {
        let shipped: BTreeSet<String> = if out_dir.is_dir() {
            fs::read_dir(&out_dir)
                .map_err(|e| format!("cannot read {}: {e}", out_dir.display()))?
                .filter_map(|e| e.ok())
                .filter(|e| e.path().is_file())
                .filter_map(|e| {
                    let name = e.file_name().to_string_lossy().to_string();
                    if name.ends_with(".container")
                        || name.ends_with(".pod")
                        || name.ends_with(".network")
                        || name.ends_with(".volume")
                        || name.ends_with(".image")
                    {
                        Some(name)
                    } else {
                        None
                    }
                })
                .collect()
        } else {
            BTreeSet::new()
        };

        let orphans: BTreeSet<_> = shipped.difference(&generated_files).cloned().collect();
        if !orphans.is_empty() {
            for orphan in &orphans {
                eprintln!(
                    "[pod-gen] DRIFT: un-generated orphan Quadlet unit in SSOT dir: {orphan}"
                );
            }
            drift += orphans.len();
        }

        if drift > 0 {
            eprintln!("[pod-gen] {drift} Quadlet unit(s) DRIFTED from SSOT");
            return Err(format!(
                "[pod-gen] {drift} Quadlet unit(s) DRIFTED from SSOT"
            ));
        }
        if member_miss > 0 {
            return Err(format!("[pod-gen] {member_miss} member unit(s) missing"));
        }
    }

    Ok(PodQuadletResult {
        active_units,
        wrote,
        out_dir,
        listed_files,
        written_files,
        removed_files,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_shlex_split() {
        let words = shlex_split("--storage-opt=additionalimagestore=/usr/lib/bootc/storage");
        assert_eq!(
            words,
            vec!["--storage-opt=additionalimagestore=/usr/lib/bootc/storage"]
        );

        let words = shlex_split("--foo 'bar baz' \"qux quux\"");
        assert_eq!(words, vec!["--foo", "bar baz", "qux quux"]);
    }

    #[test]
    fn test_wrap_doc() {
        let doc = "Line one rationale that is reasonably long so wrapping engages across the width boundary deterministically.";
        let wrapped = wrap_doc(doc, 76);
        assert!(!wrapped.is_empty());
        assert!(wrapped[0].starts_with("# Line one rationale"));
    }

    #[test]
    fn test_is_root_id() {
        assert!(is_root_id("0"));
        assert!(is_root_id("root"));
        assert!(is_root_id("0:0"));
        assert!(is_root_id("root:root"));
        assert!(!is_root_id("1000"));
        assert!(!is_root_id("826"));
    }

    #[test]
    fn test_credential_checks() {
        assert!(is_credential_key("POSTGRES_PASSWORD", None));
        assert!(is_credential_key("API_KEY", None));
        assert!(!is_credential_key("MAX_TOKENS", None));
        assert!(is_literal_value("my-secret-value"));
        assert!(!is_literal_value("${MIOS_SECRET}"));
        assert!(!is_literal_value("true"));
        assert!(!is_literal_value("123"));
    }

    #[test]
    fn test_law_6_privilege_enforcement() {
        let mut allowed_root = BTreeSet::new();
        allowed_root.insert("mios-ceph".to_string());
        allowed_root.insert("mios-ceph.container".to_string());

        let grandfathered_creds = BTreeSet::new();
        let secret_keys = BTreeSet::new();
        let placeholders = BTreeSet::new();
        let sidecars = BTreeMap::new();
        let ssot_exports = BTreeMap::new();

        // Positive Control: authorized container in privileged_quadlets.root allows User=0 and Group=0
        let auth_spec: toml::Value = toml::from_str(
            r#"
            [Container]
            Image = "ceph"
            User = "0"
            Group = "0"
        "#,
        )
        .unwrap();
        let auth_out = render_nested_quadlet(
            "mios-ceph",
            &auth_spec,
            "container",
            &allowed_root,
            &grandfathered_creds,
            &secret_keys,
            &placeholders,
            &ssot_exports,
            &sidecars,
        );
        assert!(
            auth_out.is_ok(),
            "Authorized container mios-ceph should succeed"
        );
        let text_out = auth_out.unwrap();
        assert!(text_out.contains("User=0") && text_out.contains("Group=0"));

        // Positive Control: unprivileged container with standard non-zero UID passes
        let unpriv_spec: toml::Value = toml::from_str(
            r#"
            [Container]
            Image = "adguard"
            User = "825"
            Group = "825"
        "#,
        )
        .unwrap();
        let unpriv_out = render_nested_quadlet(
            "mios-adguard",
            &unpriv_spec,
            "container",
            &allowed_root,
            &grandfathered_creds,
            &secret_keys,
            &placeholders,
            &ssot_exports,
            &sidecars,
        );
        assert!(
            unpriv_out.is_ok(),
            "Unprivileged container with User=825 should succeed"
        );

        // Positive Control: allowlisted container omitting User= (implicit root) passes
        let implicit_spec: toml::Value = toml::from_str(
            r#"
            [Container]
            Image = "ceph"
        "#,
        )
        .unwrap();
        let implicit_out = render_nested_quadlet(
            "mios-ceph",
            &implicit_spec,
            "container",
            &allowed_root,
            &grandfathered_creds,
            &secret_keys,
            &placeholders,
            &ssot_exports,
            &sidecars,
        );
        assert!(
            implicit_out.is_ok(),
            "Allowlisted container without User= should pass"
        );

        // Negative Control 1: Unauthorized container declaring User=0 is rejected
        let unauth_u0: toml::Value = toml::from_str(
            r#"
            [Container]
            Image = "alpine"
            User = "0"
        "#,
        )
        .unwrap();
        let err = render_nested_quadlet(
            "test-unauth-root",
            &unauth_u0,
            "container",
            &allowed_root,
            &grandfathered_creds,
            &secret_keys,
            &placeholders,
            &ssot_exports,
            &sidecars,
        );
        assert!(err.is_err(), "Unauthorized User=0 must fail");
        assert!(err.unwrap_err().contains("Law 6"));

        // Negative Control 2: Unauthorized container declaring User=root is rejected
        let unauth_uroot: toml::Value = toml::from_str(
            r#"
            [Container]
            Image = "alpine"
            User = "root"
        "#,
        )
        .unwrap();
        let err = render_nested_quadlet(
            "test-unauth-root",
            &unauth_uroot,
            "container",
            &allowed_root,
            &grandfathered_creds,
            &secret_keys,
            &placeholders,
            &ssot_exports,
            &sidecars,
        );
        assert!(err.is_err(), "Unauthorized User=root must fail");
        assert!(err.unwrap_err().contains("Law 6"));

        // Negative Control 3: Unauthorized container declaring Group=0 is rejected
        let unauth_g0: toml::Value = toml::from_str(
            r#"
            [Container]
            Image = "alpine"
            Group = "0"
            User = "1000"
        "#,
        )
        .unwrap();
        let err = render_nested_quadlet(
            "test-unauth-root",
            &unauth_g0,
            "container",
            &allowed_root,
            &grandfathered_creds,
            &secret_keys,
            &placeholders,
            &ssot_exports,
            &sidecars,
        );
        assert!(err.is_err(), "Unauthorized Group=0 must fail");
        assert!(err.unwrap_err().contains("Law 6"));

        // Negative Control 4: Un-allowlisted container declaring no User= is rejected
        let unauth_nouser: toml::Value = toml::from_str(
            r#"
            [Container]
            Image = "alpine"
        "#,
        )
        .unwrap();
        let err = render_nested_quadlet(
            "zz-planted",
            &unauth_nouser,
            "container",
            &allowed_root,
            &grandfathered_creds,
            &secret_keys,
            &placeholders,
            &ssot_exports,
            &sidecars,
        );
        assert!(
            err.is_err(),
            "Un-allowlisted container without User= must fail"
        );
        let msg = err.unwrap_err();
        assert!(msg.contains("declares no User=") && msg.contains("Law 6"));
    }

    #[test]
    fn test_law_11_secrets_enforcement() {
        let allowed_root = BTreeSet::new();
        let mut grandfathered_creds = BTreeSet::new();
        grandfathered_creds.insert(
            "usr/share/containers/systemd/mios-pgvector.container:POSTGRES_PASSWORD=mios"
                .to_string(),
        );

        let secret_keys = BTreeSet::new();
        let placeholders = BTreeSet::new();
        let sidecars = BTreeMap::new();
        let ssot_exports = BTreeMap::new();

        // Positive Control: grandfathered placeholder password literal passes
        let gf_spec: toml::Value = toml::from_str(
            r#"
            [Container]
            Image = "pgvector"
            User = "826"
            Group = "826"
            Environment = ["POSTGRES_PASSWORD=mios"]
        "#,
        )
        .unwrap();
        let gf_out = render_nested_quadlet(
            "mios-pgvector",
            &gf_spec,
            "container",
            &allowed_root,
            &grandfathered_creds,
            &secret_keys,
            &placeholders,
            &ssot_exports,
            &sidecars,
        );
        assert!(gf_out.is_ok(), "Grandfathered credential must pass");
        assert!(gf_out
            .unwrap()
            .contains("Environment=POSTGRES_PASSWORD=mios"));

        // Positive Control: secret reference via EnvironmentFile passes
        let ref_spec: toml::Value = toml::from_str(
            r#"
            [Container]
            Image = "pgvector"
            User = "826"
            Group = "826"
            EnvironmentFile = "/etc/mios/secrets.env"
        "#,
        )
        .unwrap();
        let ref_out = render_nested_quadlet(
            "mios-pgvector",
            &ref_spec,
            "container",
            &allowed_root,
            &grandfathered_creds,
            &secret_keys,
            &placeholders,
            &ssot_exports,
            &sidecars,
        );
        assert!(
            ref_out.is_ok(),
            "Secret reference via EnvironmentFile must pass"
        );
        assert!(ref_out
            .unwrap()
            .contains("EnvironmentFile=/etc/mios/secrets.env"));

        // Negative Control: Non-placeholder password literal in Environment is rejected
        let secret_spec: toml::Value = toml::from_str(
            r#"
            [Container]
            Image = "postgres"
            User = "826"
            Group = "826"
            Environment = ["POSTGRES_PASSWORD=my-super-secret-pw"]
        "#,
        )
        .unwrap();
        let err = render_nested_quadlet(
            "test-db",
            &secret_spec,
            "container",
            &allowed_root,
            &grandfathered_creds,
            &secret_keys,
            &placeholders,
            &ssot_exports,
            &sidecars,
        );
        assert!(err.is_err(), "Non-placeholder password literal must fail");
        assert!(err.unwrap_err().contains("Law 11"));
    }

    #[test]
    fn test_user_scope_and_bootc_store() {
        let bake: toml::Value = toml::from_str(
            r#"
            additional_image_store = "/usr/lib/bootc/storage"
            firstboot_tokens = []
        "#,
        )
        .unwrap();

        let mut containers = toml::map::Map::new();
        containers.insert(
            "sys".to_string(),
            toml::from_str(
                r#"
            [Container]
            Image = "example/sys:1"
        "#,
            )
            .unwrap(),
        );
        containers.insert(
            "usr".to_string(),
            toml::from_str(
                r#"
            [Container]
            Image = "example/usr:1"
        "#,
            )
            .unwrap(),
        );

        let mut user_scope = BTreeSet::new();
        user_scope.insert("usr".to_string());

        let placeholders = BTreeSet::new();
        let ssot_exports = BTreeMap::new();
        let sidecars = BTreeMap::new();

        let res = apply_bound_image_store(
            &mut containers,
            &bake,
            &user_scope,
            &placeholders,
            &ssot_exports,
            &sidecars,
        );
        assert!(res.is_ok());

        let sys_c = containers.get("sys").unwrap().get("Container").unwrap();
        assert!(
            sys_c.get("GlobalArgs").is_some(),
            "System unit must get GlobalArgs"
        );

        let usr_c = containers.get("usr").unwrap().get("Container").unwrap();
        assert!(
            usr_c.get("GlobalArgs").is_none(),
            "User-scope unit must NOT get GlobalArgs"
        );

        // Negative Control: user-scope unit declaring the bootc store is rejected
        let mut bad_containers = toml::map::Map::new();
        bad_containers.insert(
            "usr".to_string(),
            toml::from_str(
                r#"
            [Container]
            Image = "example/usr:1"
            GlobalArgs = "--storage-opt=additionalimagestore=/usr/lib/bootc/storage"
        "#,
            )
            .unwrap(),
        );

        let bad_res = apply_bound_image_store(
            &mut bad_containers,
            &bake,
            &user_scope,
            &placeholders,
            &ssot_exports,
            &sidecars,
        );
        assert!(
            bad_res.is_err(),
            "User-scope unit declaring bootc store must fail"
        );
    }

    // The bound-store contract tools/test_drift-checks.py held over the retired
    // generate-pod-quadlets.py, carried to the implementation that replaced it.
    const STORE: &str = "/usr/lib/bootc/storage";
    const BAKE: &str = "additional_image_store = \"/usr/lib/bootc/storage\"\nfirstboot_tokens = [\"floating\"]\n";

    /// One system unit `core` running `image`, with `args` as its GlobalArgs.
    fn project(
        bake: &str,
        args: Option<toml::Value>,
        image: &str,
    ) -> (Result<(), String>, toml::map::Map<String, toml::Value>) {
        let bake: toml::Value = toml::from_str(bake).unwrap();
        let mut section = toml::map::Map::new();
        section.insert("Image".into(), toml::Value::String(image.into()));
        if let Some(args) = args {
            section.insert("GlobalArgs".into(), args);
        }
        let mut unit = toml::map::Map::new();
        unit.insert("Container".into(), toml::Value::Table(section));
        let mut containers = toml::map::Map::new();
        containers.insert("core".into(), toml::Value::Table(unit));
        let res = apply_bound_image_store(
            &mut containers,
            &bake,
            &BTreeSet::new(),
            &BTreeSet::new(),
            &BTreeMap::new(),
            &BTreeMap::new(),
        );
        (res, containers)
    }

    fn global_args(containers: &toml::map::Map<String, toml::Value>) -> Option<toml::Value> {
        containers["core"]["Container"].get("GlobalArgs").cloned()
    }

    fn strings(items: &[&str]) -> toml::Value {
        toml::Value::Array(items.iter().map(|s| toml::Value::String((*s).into())).collect())
    }

    #[test]
    fn bound_store_preserves_other_args_and_is_idempotent() {
        let wanted = format!("--storage-opt=additionalimagestore={STORE}");
        let (res, mut containers) = project(BAKE, Some(strings(&["--log-level=debug"])), "example/core");
        res.unwrap();
        let expected = strings(&["--log-level=debug", &wanted]);
        assert_eq!(global_args(&containers), Some(expected.clone()));
        let bake: toml::Value = toml::from_str(BAKE).unwrap();
        let again = apply_bound_image_store(
            &mut containers,
            &bake,
            &BTreeSet::new(),
            &BTreeSet::new(),
            &BTreeMap::new(),
            &BTreeMap::new(),
        );
        again.unwrap();
        assert_eq!(global_args(&containers), Some(expected));
    }

    #[test]
    fn bound_store_keeps_a_split_option_verbatim() {
        let split = toml::Value::String(format!("--storage-opt additionalimagestore={STORE}"));
        let (res, containers) = project(BAKE, Some(split.clone()), "example/core");
        res.unwrap();
        assert_eq!(global_args(&containers), Some(split));
    }

    #[test]
    fn bound_store_conflicting_or_duplicate_store_fails() {
        let dup = format!("--storage-opt=additionalimagestore={STORE}");
        for args in [
            strings(&["--storage-opt=additionalimagestore=/other"]),
            strings(&[&dup, &dup]),
        ] {
            let (res, _) = project(BAKE, Some(args), "example/core");
            assert!(res.unwrap_err().contains("conflicting"));
        }
    }

    #[test]
    fn bound_store_malformed_args_fail() {
        for args in [
            toml::Value::Integer(0),
            toml::Value::Boolean(false),
            toml::Value::Array(vec![toml::Value::Integer(0)]),
        ] {
            let (res, _) = project(BAKE, Some(args), "example/core");
            assert!(res.unwrap_err().contains("GlobalArgs must"));
        }
    }

    #[test]
    fn bound_store_never_reaches_a_floating_image() {
        let (res, containers) =
            project(BAKE, Some(strings(&["--log-level=debug"])), "example/floating");
        res.unwrap();
        assert_eq!(global_args(&containers), Some(strings(&["--log-level=debug"])));
        let store = toml::Value::String(format!("--storage-opt=additionalimagestore={STORE}"));
        let (res, _) = project(BAKE, Some(store), "example/floating");
        assert!(res.unwrap_err().contains("firstboot image"));
    }

    #[test]
    fn bound_store_false_settings_are_not_treated_as_missing() {
        let (res, _) = project("additional_image_store = false\n", None, "example/core");
        assert!(res.unwrap_err().contains("absolute path"));
        let bake = format!("additional_image_store = \"{STORE}\"\nfirstboot_tokens = false\n");
        let (res, _) = project(&bake, None, "example/core");
        assert!(res.unwrap_err().contains("string array"));
        // Absent and "" remain the documented way to disable the projection.
        for bake in ["", "additional_image_store = \"\"\n"] {
            let (res, containers) = project(bake, None, "example/core");
            res.unwrap();
            assert_eq!(global_args(&containers), None);
        }
    }
}
