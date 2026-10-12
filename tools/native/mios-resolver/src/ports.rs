/* AI-hint: Runtime port allocator -- derives every [ports] value from the [ports.categories] schema (base + index*stride) after layer merging, so operator/OEM overrides re-derive live. Mirrors mios_toml.derive_ports. */
/* AI-doc: usr/share/doc/mios/manual/src.md */
use toml::Value;

pub fn derive_ports(merged: &mut Value) {
    let cats: Vec<(String, Value)> = match merged
        .get("ports")
        .and_then(|p| p.get("categories"))
        .and_then(|c| c.as_table())
    {
        Some(table) => table.iter().map(|(k, v)| (k.clone(), v.clone())).collect(),
        None => return,
    };

    let ports = match merged.get_mut("ports").and_then(|p| p.as_table_mut()) {
        Some(table) => table,
        None => return,
    };

    let mut names: Vec<&String> = cats.iter().map(|(k, _)| k).collect();
    names.sort();

    for cat_name in names {
        let cfg = match cats.iter().find(|(k, _)| k == cat_name).map(|(_, v)| v) {
            Some(v) => v,
            None => continue,
        };
        let base = cfg.get("base").and_then(|v| v.as_integer()).unwrap_or(0);
        let stride = cfg.get("stride").and_then(|v| v.as_integer()).unwrap_or(1);

        if let Some(members) = cfg.get("members").and_then(|v| v.as_array()) {
            for (idx, member) in members.iter().enumerate() {
                if let Some(name) = member.as_str() {
                    if !name.is_empty() {
                        ports.insert(name.to_string(), Value::Integer(base + idx as i64 * stride));
                    }
                }
            }
        }

        if let Some(pinned) = cfg.get("pinned").and_then(|v| v.as_table()) {
            for (name, value) in pinned {
                if let Some(n) = value.as_integer() {
                    ports.insert(name.clone(), Value::Integer(n));
                }
            }
        }
    }
}

/// `ports.<name>` -> the `[ports.categories]` key its value is copied from, in
/// [`derive_ports`] order: a pinned entry, or the base at offset 0.
pub fn port_origins(merged: &Value) -> std::collections::BTreeMap<String, String> {
    let mut origins = std::collections::BTreeMap::new();
    let Some(cats) = merged
        .get("ports")
        .and_then(|p| p.get("categories"))
        .and_then(|c| c.as_table())
    else {
        return origins;
    };
    let mut names: Vec<&String> = cats.keys().collect();
    names.sort();
    for cat_name in names {
        let cfg = &cats[cat_name];
        let stride = cfg.get("stride").and_then(|v| v.as_integer()).unwrap_or(1);
        if let Some(members) = cfg.get("members").and_then(|v| v.as_array()) {
            for (idx, member) in members.iter().enumerate() {
                let Some(name) = member.as_str().filter(|n| !n.is_empty()) else {
                    continue;
                };
                let key = format!("ports.{name}");
                if idx as i64 * stride == 0 {
                    origins.insert(key, format!("ports.categories.{cat_name}.base"));
                } else {
                    origins.remove(&key);
                }
            }
        }
        if let Some(pinned) = cfg.get("pinned").and_then(|v| v.as_table()) {
            for (name, value) in pinned {
                if value.as_integer().is_some() {
                    origins.insert(
                        format!("ports.{name}"),
                        format!("ports.categories.{cat_name}.pinned.{name}"),
                    );
                }
            }
        }
    }
    origins
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture() -> Value {
        toml::from_str(
            r#"
[ports]
agent_pipe = 1
hermes = 2
adguard_dns = 9999

[ports.categories.agent]
base = 8700
stride = 10
members = ["agent_pipe", "prefilter", "hermes"]

[ports.categories.edge]
base = 8050
stride = 1
members = ["adguard_ui"]
pinned = { adguard_dns = 53 }
"#,
        )
        .unwrap()
    }

    #[test]
    fn derives_from_base_and_stride() {
        let mut v = fixture();
        derive_ports(&mut v);
        let p = v.get("ports").unwrap();
        assert_eq!(p.get("agent_pipe").unwrap().as_integer(), Some(8700));
        assert_eq!(p.get("prefilter").unwrap().as_integer(), Some(8710));
        assert_eq!(p.get("hermes").unwrap().as_integer(), Some(8720));
        assert_eq!(p.get("adguard_ui").unwrap().as_integer(), Some(8050));
    }

    #[test]
    fn derivation_overrides_the_flat_table() {
        let mut v = fixture();
        derive_ports(&mut v);
        // flat table said 1 / 2 / 9999 -- the schema must win
        let p = v.get("ports").unwrap();
        assert_eq!(p.get("agent_pipe").unwrap().as_integer(), Some(8700));
        assert_eq!(p.get("adguard_dns").unwrap().as_integer(), Some(53));
    }

    #[test]
    fn origins_name_the_category_key_a_port_is_copied_from() {
        let o = port_origins(&fixture());
        assert_eq!(o["ports.agent_pipe"], "ports.categories.agent.base");
        assert_eq!(o["ports.adguard_ui"], "ports.categories.edge.base");
        assert_eq!(
            o["ports.adguard_dns"],
            "ports.categories.edge.pinned.adguard_dns"
        );
        // base + n*stride is computed, not copied: no single source.
        assert!(!o.contains_key("ports.prefilter"));
        assert!(!o.contains_key("ports.hermes"));
    }

    #[test]
    fn retargeting_a_base_moves_the_whole_category() {
        let mut v = fixture();
        v["ports"]["categories"]["agent"]["base"] = Value::Integer(9200);
        derive_ports(&mut v);
        let p = v.get("ports").unwrap();
        assert_eq!(p.get("agent_pipe").unwrap().as_integer(), Some(9200));
        assert_eq!(p.get("prefilter").unwrap().as_integer(), Some(9210));
        assert_eq!(p.get("hermes").unwrap().as_integer(), Some(9220));
        // an untouched category must not move
        assert_eq!(p.get("adguard_ui").unwrap().as_integer(), Some(8050));
    }
}
