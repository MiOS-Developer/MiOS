// AI-hint: `mios-gen light-tiers` -- generates the CPU-tier llama-swap model map from the light lane's source map ([llamacpp].config): [llamacpp.light_cpu_tier].peer_model and its aliases move to a llama-swap peer on [ports].cpu_node (the resident mios-cpu-node), the entries in drop are removed, and every other model, group and top-level key is carried over unchanged. --check fails when the committed map is stale.
// AI-related: usr/share/mios/mios.toml, usr/share/mios/llamacpp/mios-llm-light.yaml, usr/share/mios/llamacpp/mios-llm-light-cpu.yaml, tools/native/mios-gen/src/gpu_topology.rs
use std::fs;
use std::path::{Path, PathBuf};

use serde_yaml::{Mapping, Value as Yaml};
use toml::Value;

/// What the CPU tier changes, from [llamacpp.light_cpu_tier] and [ports].
#[derive(Debug, Clone, PartialEq)]
pub struct Tier {
    pub source: PathBuf,
    pub output: PathBuf,
    pub peer: String,
    pub peer_model: String,
    pub peer_port: i64,
    pub drop: Vec<String>,
}

fn rel(p: &str, key: &str) -> Result<PathBuf, String> {
    let path = Path::new(p);
    if !path.is_absolute()
        || path
            .components()
            .any(|c| matches!(c, std::path::Component::ParentDir))
        || path.extension().and_then(|e| e.to_str()) != Some("yaml")
    {
        return Err(format!("{key} must be an absolute .yaml path"));
    }
    Ok(path.strip_prefix("/").map_err(|e| e.to_string())?.into())
}

impl Tier {
    pub fn from_ssot(data: &Value) -> Result<Tier, String> {
        let llama = data.get("llamacpp").ok_or("missing [llamacpp]")?;
        let s = |t: &Value, k: &str, name: &str| {
            t.get(k)
                .and_then(Value::as_str)
                .filter(|v| !v.is_empty())
                .map(str::to_owned)
                .ok_or_else(|| format!("{name}.{k} must be a nonempty string"))
        };
        let tier = llama
            .get("light_cpu_tier")
            .ok_or("missing [llamacpp.light_cpu_tier]")?;
        let cpu = llama
            .get("light_backends")
            .and_then(|b| b.get("cpu"))
            .ok_or("missing [llamacpp.light_backends.cpu]")?;
        let source = s(llama, "config", "llamacpp")?;
        let output = s(cpu, "config", "llamacpp.light_backends.cpu")?;
        if source == output {
            return Err("the CPU tier must not overwrite its own source map".into());
        }
        let drop = tier
            .get("drop")
            .and_then(Value::as_array)
            .ok_or("llamacpp.light_cpu_tier.drop must be a string array")?
            .iter()
            .map(|v| {
                v.as_str()
                    .map(str::to_owned)
                    .ok_or("llamacpp.light_cpu_tier.drop must be a string array".to_string())
            })
            .collect::<Result<Vec<_>, _>>()?;
        let peer_port = data
            .get("ports")
            .and_then(|p| p.get("cpu_node"))
            .and_then(Value::as_integer)
            .filter(|p| (1..=65535).contains(p))
            .ok_or("ports.cpu_node must be a TCP port")?;
        let peer = s(tier, "peer", "llamacpp.light_cpu_tier")?;
        if !peer
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || c == b'-' || c == b'_')
        {
            return Err("llamacpp.light_cpu_tier.peer must be a plain identifier".into());
        }
        Ok(Tier {
            source: rel(&source, "llamacpp.config")?,
            output: rel(&output, "llamacpp.light_backends.cpu.config")?,
            peer,
            peer_model: s(tier, "peer_model", "llamacpp.light_cpu_tier")?,
            peer_port,
            drop,
        })
    }
}

fn key(s: &str) -> Yaml {
    Yaml::String(s.to_owned())
}

/// The CPU-tier map derived from the source map.
pub fn derive(tier: &Tier, source: &str) -> Result<String, String> {
    let mut doc: Yaml =
        serde_yaml::from_str(source).map_err(|e| format!("source model map: {e}"))?;
    let root = doc
        .as_mapping_mut()
        .ok_or("source model map is not a mapping")?;
    if root.contains_key(key("peers")) {
        return Err("the source model map already declares peers; merge them by hand".into());
    }
    let models = root
        .get_mut(key("models"))
        .and_then(Yaml::as_mapping_mut)
        .ok_or("source model map has no models mapping")?;
    let moved = models.remove(key(&tier.peer_model)).ok_or_else(|| {
        format!(
            "peer_model {:?} is not a model in the source map",
            tier.peer_model
        )
    })?;
    let mut names = vec![key(&tier.peer_model)];
    if let Some(aliases) = moved.get("aliases").and_then(Yaml::as_sequence) {
        names.extend(aliases.iter().cloned());
    }
    let mut removed = vec![tier.peer_model.clone()];
    for d in &tier.drop {
        models
            .remove(key(d))
            .ok_or_else(|| format!("drop entry {d:?} is not a model in the source map"))?;
        removed.push(d.clone());
    }
    if let Some(groups) = root.get_mut(key("groups")).and_then(Yaml::as_mapping_mut) {
        for (_, g) in groups.iter_mut() {
            if let Some(members) = g.get_mut("members").and_then(Yaml::as_sequence_mut) {
                members.retain(|m| m.as_str().is_none_or(|m| !removed.iter().any(|r| r == m)));
            }
        }
    }
    let mut peer = Mapping::new();
    peer.insert(
        key("proxy"),
        key(&format!("http://127.0.0.1:{}", tier.peer_port)),
    );
    peer.insert(key("models"), Yaml::Sequence(names));
    let mut peers = Mapping::new();
    peers.insert(key(&tier.peer), Yaml::Mapping(peer));
    root.insert(key("peers"), Yaml::Mapping(peers));
    let body = serde_yaml::to_string(&doc).map_err(|e| e.to_string())?;
    Ok(format!(
        "# AI-hint: CPU-tier mios-llm-light model map. GENERATED by `mios-gen light-tiers` from /{} and [llamacpp.light_cpu_tier] -- do not edit; edit the source map or the SSOT.\n\
         # AI-related: usr/share/mios/mios.toml, /{}, tools/native/mios-gen/src/light_tiers.rs\n{body}",
        tier.source.display(),
        tier.source.display()
    ))
}

/// Write (or with `check`, compare) the CPU-tier map under `root`.
/// Returns whether the file changed (or, with `check`, is stale).
pub fn run(root: &Path, check: bool) -> Result<bool, String> {
    let ssot = root.join("usr/share/mios/mios.toml");
    let data: Value =
        toml::from_str(&fs::read_to_string(&ssot).map_err(|e| format!("{}: {e}", ssot.display()))?)
            .map_err(|e| format!("{}: {e}", ssot.display()))?;
    let tier = Tier::from_ssot(&data)?;
    let src = root.join(&tier.source);
    let want = derive(
        &tier,
        &fs::read_to_string(&src).map_err(|e| format!("{}: {e}", src.display()))?,
    )?;
    let out = root.join(&tier.output);
    let have = fs::read_to_string(&out).ok();
    if have.as_deref() == Some(want.as_str()) {
        return Ok(false);
    }
    if check {
        return Err(format!(
            "{} is stale against /{} -- run mios-gen light-tiers",
            tier.output.display(),
            tier.source.display()
        ));
    }
    fs::write(&out, want).map_err(|e| format!("{}: {e}", out.display()))?;
    Ok(true)
}

#[cfg(test)]
mod tests {
    use super::*;

    const SRC: &str = r#"
logToStdout: "proxy,http"
models:
  "small":
    cmd: /app/llama-server --model /models/small.gguf --port ${PORT}
    ttl: 300
  "big:8b":
    aliases:
      - agent
      - agent-cpu
    cmd: /app/llama-server --model /models/big.gguf --port ${PORT}
  "vision:7b":
    cmd: /app/llama-server --model /models/vision.gguf --port ${PORT}
  "embed":
    cmd: /app/llama-server --model /models/embed.gguf --embedding --port ${PORT}
groups:
  resident:
    swap: false
    members:
      - "big:8b"
      - "small"
      - "embed"
"#;

    fn tier() -> Tier {
        Tier {
            source: "usr/share/mios/llamacpp/mios-llm-light.yaml".into(),
            output: "usr/share/mios/llamacpp/mios-llm-light-cpu.yaml".into(),
            peer: "cpu-node".into(),
            peer_model: "big:8b".into(),
            peer_port: 8510,
            drop: vec!["vision:7b".into()],
        }
    }

    fn parse(s: &str) -> Yaml {
        serde_yaml::from_str(s).unwrap()
    }

    #[test]
    fn the_big_model_and_its_aliases_move_to_the_peer() {
        let out = parse(&derive(&tier(), SRC).unwrap());
        let models = out["models"].as_mapping().unwrap();
        assert!(!models.contains_key("big:8b"), "the 8B still loads locally");
        assert!(
            !models.contains_key("vision:7b"),
            "a dropped entry survived"
        );
        let peer = &out["peers"]["cpu-node"];
        assert_eq!(peer["proxy"], key("http://127.0.0.1:8510"));
        let names: Vec<_> = peer["models"]
            .as_sequence()
            .unwrap()
            .iter()
            .map(|v| v.as_str().unwrap())
            .collect();
        assert_eq!(names, ["big:8b", "agent", "agent-cpu"]);
    }

    #[test]
    fn everything_else_is_carried_over_unchanged() {
        let src = parse(SRC);
        let out = parse(&derive(&tier(), SRC).unwrap());
        for m in ["small", "embed"] {
            assert_eq!(out["models"][m], src["models"][m], "{m} changed");
        }
        assert_eq!(out["logToStdout"], key("proxy,http"));
        let members: Vec<_> = out["groups"]["resident"]["members"]
            .as_sequence()
            .unwrap()
            .iter()
            .map(|v| v.as_str().unwrap())
            .collect();
        assert_eq!(members, ["small", "embed"]);
        assert_eq!(out["groups"]["resident"]["swap"], Yaml::Bool(false));
    }

    #[test]
    fn output_is_marked_generated_from_its_source() {
        let out = derive(&tier(), SRC).unwrap();
        assert!(out.starts_with("# AI-hint: CPU-tier"));
        assert!(out.contains(
            "GENERATED by `mios-gen light-tiers` from /usr/share/mios/llamacpp/mios-llm-light.yaml"
        ));
    }

    #[test]
    fn stale_ssot_names_fail_instead_of_silently_passing() {
        let mut t = tier();
        t.peer_model = "gone".into();
        assert!(derive(&t, SRC).unwrap_err().contains("peer_model"));
        let mut t = tier();
        t.drop = vec!["gone".into()];
        assert!(derive(&t, SRC).unwrap_err().contains("drop entry"));
        let with_peers = format!("{SRC}peers:\n  x:\n    proxy: http://h\n    models: [a]\n");
        assert!(derive(&tier(), &with_peers).is_err());
    }

    #[test]
    fn check_reports_a_stale_map_and_write_fixes_it() {
        let d = tempfile::tempdir().unwrap();
        let r = d.path();
        fs::create_dir_all(r.join("usr/share/mios/llamacpp")).unwrap();
        fs::write(
            r.join("usr/share/mios/mios.toml"),
            "[ports]\ncpu_node = 8510\n[llamacpp]\nconfig = \"/usr/share/mios/llamacpp/mios-llm-light.yaml\"\n\
             [llamacpp.light_backends.cpu]\nconfig = \"/usr/share/mios/llamacpp/mios-llm-light-cpu.yaml\"\n\
             [llamacpp.light_cpu_tier]\npeer = \"cpu-node\"\npeer_model = \"big:8b\"\ndrop = [\"vision:7b\"]\n",
        )
        .unwrap();
        fs::write(r.join("usr/share/mios/llamacpp/mios-llm-light.yaml"), SRC).unwrap();
        assert!(run(r, true).is_err(), "a missing map passed --check");
        assert!(run(r, false).unwrap());
        assert!(!run(r, true).unwrap());
        assert!(!run(r, false).unwrap(), "a second write changed the map");
        fs::write(
            r.join("usr/share/mios/llamacpp/mios-llm-light-cpu.yaml"),
            "models: {}\n",
        )
        .unwrap();
        assert!(run(r, true).unwrap_err().contains("stale"));
    }

    #[test]
    fn ssot_rejects_a_tier_that_overwrites_its_source() {
        let data: Value = toml::from_str(
            "[ports]\ncpu_node = 8510\n[llamacpp]\nconfig = \"/a.yaml\"\n[llamacpp.light_backends.cpu]\nconfig = \"/a.yaml\"\n\
             [llamacpp.light_cpu_tier]\npeer = \"p\"\npeer_model = \"m\"\ndrop = []\n",
        )
        .unwrap();
        assert!(Tier::from_ssot(&data).is_err());
    }

    #[test]
    fn the_shipped_map_matches_the_shipped_source() {
        let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../..");
        assert!(
            !run(&root, true).unwrap(),
            "usr/share/mios/llamacpp/mios-llm-light-cpu.yaml is stale"
        );
    }
}
