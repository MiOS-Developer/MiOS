// AI-hint: Model tiers of the inference lanes. `mios-gen light-tiers` generates the CPU-tier llama-swap model map from the light lane's source map ([llamacpp].config): [llamacpp.light_cpu_tier].peer_model and its aliases move to a llama-swap peer on [ports].cpu_node, the entries in drop are removed, everything else is carried over (--check fails when the committed map is stale). `mios-gen model-tier --lane heavy|cpu-node` measures the host (its spec'd RAM as MemTotal, dGPU VRAM, APU VRAM+GTT), picks the lane's [[llamacpp.tiers.<lane>]] row that fits its budget and writes it to the lane's EnvironmentFile; for [ai].heavy_engine = "auto" it detects the GPU ([llamacpp.heavy_auto]: NVIDIA -> CUDA, amdgpu render node -> Vulkan) and `--probe` exits 1 when there is none. `mios-gen tool-safe --check` fails when any lane, tier row or model-map entry is below the [llamacpp.tool_safe] floor.
// AI-related: usr/share/mios/mios.toml, usr/share/mios/llamacpp/mios-llm-light.yaml, usr/share/mios/llamacpp/mios-llm-light-cpu.yaml, tools/native/mios-gen/src/gpu_topology.rs, usr/share/doc/mios/manual/ch10-local-inference-lanes-and-llama-cpp.md
use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};

use serde_json::{json, Value as Json};
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

// ===== Model tiers and the tool-safe floor ==========================================

fn at<'a>(v: &'a Value, path: &[&str]) -> Option<&'a Value> {
    path.iter().try_fold(v, |cur, key| cur.get(*key))
}

fn number(v: &Value) -> Option<f64> {
    v.as_float().or_else(|| v.as_integer().map(|i| i as f64))
}

fn text(t: &Value, key: &str, ctx: &str) -> Result<String, String> {
    t.get(key)
        .and_then(Value::as_str)
        .filter(|s| !s.is_empty())
        .map(str::to_owned)
        .ok_or_else(|| format!("{ctx}.{key} must be a nonempty string"))
}

fn real(t: &Value, key: &str, ctx: &str) -> Result<f64, String> {
    t.get(key)
        .and_then(number)
        .ok_or_else(|| format!("{ctx}.{key} must be a number"))
}

fn count(t: &Value, key: &str, ctx: &str) -> Result<u64, String> {
    t.get(key)
        .and_then(Value::as_integer)
        .filter(|v| *v >= 0)
        .map(|v| v as u64)
        .ok_or_else(|| format!("{ctx}.{key} must be a non-negative integer"))
}

fn strings(t: &Value, key: &str, ctx: &str) -> Result<Vec<String>, String> {
    let bad = || format!("{ctx}.{key} must be a string array");
    t.get(key)
        .and_then(Value::as_array)
        .ok_or_else(bad)?
        .iter()
        .map(|v| v.as_str().map(str::to_owned).ok_or_else(bad))
        .collect()
}

/// [llamacpp.tool_safe]: the floor every lane that serves tools must meet.
#[derive(Debug, Clone)]
pub struct Floor {
    pub chat_kv: Vec<String>,
    pub embed_kv: Vec<String>,
    pub quant_rank: BTreeMap<String, i64>,
    pub native: Vec<String>,
    pub large_min_params_b: f64,
    pub small_max_active_b: f64,
    pub large_floor_rank: i64,
    pub small_floor_rank: i64,
    pub embed_floor_rank: i64,
}

impl Floor {
    pub fn from_ssot(data: &Value) -> Result<Floor, String> {
        const CTX: &str = "llamacpp.tool_safe";
        let t = at(data, &["llamacpp", "tool_safe"]).ok_or("missing [llamacpp.tool_safe]")?;
        let ranks = t
            .get("quant_rank")
            .and_then(Value::as_table)
            .ok_or("llamacpp.tool_safe.quant_rank must map quant names to ranks")?;
        let mut quant_rank = BTreeMap::new();
        for (quant, rank) in ranks {
            let rank = rank
                .as_integer()
                .filter(|r| *r > 0)
                .ok_or_else(|| format!("{CTX}.quant_rank.{quant} must be a positive integer"))?;
            quant_rank.insert(quant.clone(), rank);
        }
        let rank = |key: &str| -> Result<i64, String> {
            t.get(key)
                .and_then(Value::as_integer)
                .filter(|v| *v > 0)
                .ok_or_else(|| format!("{CTX}.{key} must be a positive integer"))
        };
        Ok(Floor {
            chat_kv: strings(t, "chat_kv", CTX)?,
            embed_kv: strings(t, "embed_kv", CTX)?,
            quant_rank,
            native: strings(t, "native", CTX)?,
            large_min_params_b: real(t, "large_min_params_b", CTX)?,
            small_max_active_b: real(t, "small_max_active_b", CTX)?,
            large_floor_rank: rank("large_floor_rank")?,
            small_floor_rank: rank("small_floor_rank")?,
            embed_floor_rank: rank("embed_floor_rank")?,
        })
    }

    /// The lowest quant rank this model may serve tools at.
    pub fn required_rank(&self, f: &Facts) -> i64 {
        if f.kind == "embedding" {
            return self.embed_floor_rank;
        }
        let moe = f.active_b < f.params_b;
        let small =
            f.params_b < self.large_min_params_b || (moe && f.active_b <= self.small_max_active_b);
        if small && !f.reasoning {
            self.small_floor_rank
        } else {
            self.large_floor_rank
        }
    }

    /// None when the weights meet the floor, else why they do not.
    pub fn quant_violation(&self, file: &str, f: &Facts) -> Option<String> {
        if self.native.iter().any(|n| *n == f.quant) {
            return None;
        }
        let need = self.required_rank(f);
        match self.quant_rank.get(&f.quant) {
            None => Some(format!(
                "{file}: quant {} is not on the tool-safe ladder ([llamacpp.tool_safe].quant_rank)",
                f.quant
            )),
            Some(rank) if *rank < need => Some(format!(
                "{file}: quant {} (rank {rank}) is below the tool-safe floor (rank {need}) for a {} {}B{} model",
                f.quant,
                if f.reasoning { "reasoning" } else { "non-reasoning" },
                f.params_b,
                if f.active_b < f.params_b { format!(" ({}B active)", f.active_b) } else { String::new() }
            )),
            _ => None,
        }
    }
}

/// [llamacpp.gguf."<file>"]: what the floor is judged on.
#[derive(Debug, Clone, PartialEq)]
pub struct Facts {
    pub quant: String,
    pub params_b: f64,
    pub active_b: f64,
    pub reasoning: bool,
    pub kind: String,
    /// The base-model lineage; the cpu-node mixes it against the heavy lane's pick.
    pub family: String,
}

pub fn gguf_facts(data: &Value) -> Result<BTreeMap<String, Facts>, String> {
    let rows = at(data, &["llamacpp", "gguf"])
        .and_then(Value::as_table)
        .ok_or("missing [llamacpp.gguf] facts")?;
    let mut out = BTreeMap::new();
    for (file, row) in rows {
        let ctx = format!("llamacpp.gguf.\"{file}\"");
        let params_b = real(row, "params_b", &ctx)?;
        out.insert(
            file.clone(),
            Facts {
                quant: text(row, "quant", &ctx)?,
                params_b,
                active_b: row.get("active_b").and_then(number).unwrap_or(params_b),
                reasoning: row
                    .get("reasoning")
                    .and_then(Value::as_bool)
                    .ok_or_else(|| format!("{ctx}.reasoning must be a boolean"))?,
                kind: text(row, "kind", &ctx)?,
                family: text(row, "family", &ctx)?,
            },
        );
    }
    Ok(out)
}

/// One row of [[llamacpp.tiers.<lane>]].
#[derive(Debug, Clone, PartialEq)]
pub struct TierEntry {
    pub name: String,
    pub model_file: String,
    pub context: u64,
    pub cache_type: String,
    pub need_mib: u64,
    pub min_ram_mib: u64,
    pub enable: bool,
    /// [llamacpp.gguf."<model_file>"].family.
    pub family: String,
}

pub fn tier_entries(data: &Value, lane: &str) -> Result<Vec<TierEntry>, String> {
    let rows = at(data, &["llamacpp", "tiers", lane])
        .and_then(Value::as_array)
        .ok_or_else(|| format!("missing [[llamacpp.tiers.{lane}]]"))?;
    let facts = gguf_facts(data)?;
    rows.iter()
        .enumerate()
        .map(|(i, row)| {
            let ctx = format!("llamacpp.tiers.{lane}[{i}]");
            let model_file = text(row, "model_file", &ctx)?;
            if !model_file.ends_with(".gguf")
                || model_file.starts_with('.')
                || !model_file
                    .bytes()
                    .all(|c| c.is_ascii_alphanumeric() || b"._-".contains(&c))
            {
                return Err(format!("{ctx}.model_file must name one GGUF file"));
            }
            let family = facts
                .get(&model_file)
                .map(|f| f.family.clone())
                .ok_or_else(|| format!("{ctx}: {model_file} has no [llamacpp.gguf] facts row"))?;
            Ok(TierEntry {
                name: text(row, "name", &ctx)?,
                model_file,
                context: count(row, "context", &ctx)?,
                cache_type: text(row, "cache_type", &ctx)?,
                need_mib: count(row, "need_mib", &ctx)?,
                min_ram_mib: match row.get("min_ram_mib") {
                    Some(_) => count(row, "min_ram_mib", &ctx)?,
                    None => 0,
                },
                enable: row.get("enable").and_then(Value::as_bool).unwrap_or(true),
                family,
            })
        })
        .collect()
}

/// [llamacpp.tier_policy]: reserves and shares the budgets are computed with.
#[derive(Debug, Clone)]
pub struct Policy {
    pub host_reserve_mib: u64,
    pub vram_reserve_mib: u64,
    pub apu_gpu_ram_fraction: f64,
    pub apu_max_vram_mib: u64,
    pub env_dir: PathBuf,
    pub nvidia_smi: Vec<String>,
}

impl Policy {
    pub fn from_ssot(data: &Value) -> Result<Policy, String> {
        const CTX: &str = "llamacpp.tier_policy";
        let t = at(data, &["llamacpp", "tier_policy"]).ok_or("missing [llamacpp.tier_policy]")?;
        let fraction = real(t, "apu_gpu_ram_fraction", CTX)?;
        if !(0.0..=1.0).contains(&fraction) {
            return Err(format!("{CTX}.apu_gpu_ram_fraction must be within 0..1"));
        }
        let env_dir = PathBuf::from(text(t, "env_dir", CTX)?);
        if !env_dir.is_absolute()
            || env_dir
                .components()
                .any(|c| matches!(c, std::path::Component::ParentDir))
        {
            return Err(format!("{CTX}.env_dir must be an absolute path"));
        }
        Ok(Policy {
            host_reserve_mib: count(t, "host_reserve_mib", CTX)?,
            vram_reserve_mib: count(t, "vram_reserve_mib", CTX)?,
            apu_gpu_ram_fraction: fraction,
            apu_max_vram_mib: count(t, "apu_max_vram_mib", CTX)?,
            env_dir,
            nvidia_smi: strings(t, "nvidia_smi", CTX)?,
        })
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum GpuKind {
    Cuda,
    Vulkan,
}

/// The GPU the heavy lane's engine serves on, as measured at start.
#[derive(Debug, Clone, PartialEq)]
pub struct GpuMeasure {
    pub kind: GpuKind,
    pub vram_mib: u64,
    /// GPU-mappable system RAM (amdgpu GTT); 0 for a dGPU.
    pub gtt_mib: u64,
    /// Shares the host's RAM pool (an APU): its use comes out of the CPU lanes' budget.
    pub apu: bool,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Measured {
    pub ram_mib: u64,
    pub gpu: Option<GpuMeasure>,
}

/// MemTotal of /proc/meminfo, in MiB.
pub fn meminfo_total_mib(meminfo: &str) -> Option<u64> {
    meminfo.lines().find_map(|line| {
        let mut words = line.strip_prefix("MemTotal:")?.split_whitespace();
        let kib: u64 = words.next()?.parse().ok()?;
        (words.next() == Some("kB")).then_some(kib / 1024)
    })
}

/// The largest memory.total `nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits` prints (MiB).
pub fn nvidia_total_mib(output: &str) -> Option<u64> {
    output
        .lines()
        .filter_map(|l| l.trim().parse::<u64>().ok())
        .max()
}

/// A systemd memory bound ("65%", "512M", "8G", bytes) resolved against the host's RAM, in MiB.
pub fn bound_mib(bound: &str, ram_mib: u64) -> Option<u64> {
    let b = bound.trim();
    if let Some(pct) = b.strip_suffix('%') {
        let pct: f64 = pct.trim().parse().ok()?;
        return (pct > 0.0 && pct <= 100.0).then(|| (ram_mib as f64 * pct / 100.0) as u64);
    }
    let split = b.find(|c: char| !c.is_ascii_digit()).unwrap_or(b.len());
    let (digits, unit) = b.split_at(split);
    let n: u64 = digits.parse().ok()?;
    let bytes = match unit {
        "" => n,
        "K" => n << 10,
        "M" => n << 20,
        "G" => n << 30,
        "T" => n << 40,
        _ => return None,
    };
    Some(bytes >> 20)
}

/// How a llama.cpp heavy engine finds its GPU ([llamacpp.tier_lanes.heavy].engines).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum HeavyBackend {
    /// A pinned engine: it serves on this GPU kind or not at all.
    Fixed(GpuKind),
    /// "detect" ([ai].heavy_engine = "auto"): the first [llamacpp.heavy_auto].prefer kind the host has.
    Detect,
}

fn gpu_kind(name: &str) -> Option<GpuKind> {
    match name {
        "cuda" => Some(GpuKind::Cuda),
        "vulkan" => Some(GpuKind::Vulkan),
        _ => None,
    }
}

fn engines(data: &Value) -> Result<&toml::map::Map<String, Value>, String> {
    at(data, &["llamacpp", "tier_lanes", "heavy", "engines"])
        .and_then(Value::as_table)
        .ok_or_else(|| {
            "llamacpp.tier_lanes.heavy.engines must map heavy engines to cuda, vulkan or detect"
                .to_string()
        })
}

/// [llamacpp.tier_lanes.heavy].engines: how a heavy engine finds its GPU (None: not a llama.cpp engine).
pub fn heavy_engine_backend(data: &Value, engine: &str) -> Result<Option<HeavyBackend>, String> {
    match engines(data)?.get(engine).and_then(Value::as_str) {
        None => Ok(None),
        Some("detect") => Ok(Some(HeavyBackend::Detect)),
        Some(other) => gpu_kind(other)
            .map(|k| Some(HeavyBackend::Fixed(k)))
            .ok_or_else(|| {
                format!(
                    "llamacpp.tier_lanes.heavy.engines.{engine} = {other:?}: use cuda, vulkan or detect"
                )
            }),
    }
}

/// The pinned engine that serves a GPU kind (the one [llamacpp.tier_lanes.heavy].engines maps to it).
pub fn engine_for_kind(data: &Value, kind: GpuKind) -> Result<String, String> {
    engines(data)?
        .iter()
        .find(|(_, v)| v.as_str().and_then(gpu_kind) == Some(kind))
        .map(|(k, _)| k.clone())
        .ok_or_else(|| format!("no [llamacpp.tier_lanes.heavy].engines entry serves {kind:?}"))
}

fn heavy_engine(data: &Value) -> &str {
    at(data, &["ai", "heavy_engine"])
        .and_then(Value::as_str)
        .unwrap_or("")
}

/// [llamacpp.heavy_auto]: the GPU kinds "detect" tries, in order, and the bare-metal CUDA
/// signal; WSL's comes from [llamacpp.heavy_wsl] (its node and its libcuda both exist).
#[derive(Debug, Clone, PartialEq)]
pub struct HeavyAuto {
    pub prefer: Vec<GpuKind>,
    /// Bare metal: any of these exists (the NVIDIA driver's control node).
    pub cuda_nodes: Vec<String>,
    pub wsl: crate::gpu_topology::HeavyWsl,
}

impl HeavyAuto {
    pub fn from_ssot(data: &Value) -> Result<HeavyAuto, String> {
        const CTX: &str = "llamacpp.heavy_auto";
        let t = at(data, &["llamacpp", "heavy_auto"]).ok_or("missing [llamacpp.heavy_auto]")?;
        let prefer = strings(t, "prefer", CTX)?
            .iter()
            .map(|k| {
                gpu_kind(k).ok_or_else(|| format!("{CTX}.prefer: {k:?} is not cuda or vulkan"))
            })
            .collect::<Result<Vec<_>, _>>()?;
        if prefer.is_empty() {
            return Err(format!("{CTX}.prefer must name at least one GPU kind"));
        }
        let paths = |key: &str| -> Result<Vec<String>, String> {
            let list = strings(t, key, CTX)?;
            if list.iter().any(|p| !p.starts_with('/') || p.contains("..")) {
                return Err(format!("{CTX}.{key} must list absolute paths"));
            }
            Ok(list)
        };
        Ok(HeavyAuto {
            prefer,
            cuda_nodes: paths("cuda_nodes")?,
            wsl: crate::gpu_topology::heavy_wsl(data)?,
        })
    }
}

fn exists_under(root: &Path, path: &str) -> bool {
    root.join(path.trim_start_matches('/')).exists()
}

/// The first [llamacpp.heavy_auto].prefer GPU kind this host has: CUDA when a bare-metal NVIDIA
/// control node exists or WSL's GPU node and Windows CUDA library both do, Vulkan when an
/// [llamacpp.heavy_vulkan] render node does. None: the "detect" engine has no GPU here.
/// Presence only: the admission that follows proves the device.
pub fn detect_heavy_kind(
    root: &Path,
    data: &Value,
    host: &crate::gpu_topology::Host,
) -> Result<Option<GpuKind>, String> {
    let auto = HeavyAuto::from_ssot(data)?;
    for kind in &auto.prefer {
        let has = match kind {
            GpuKind::Cuda => {
                let libcuda = auto.wsl.lib_dir.join(&auto.wsl.libcuda);
                auto.cuda_nodes.iter().any(|p| exists_under(root, p))
                    || (exists_under(root, &auto.wsl.node.to_string_lossy())
                        && exists_under(root, &libcuda.to_string_lossy()))
            }
            GpuKind::Vulkan => crate::gpu_topology::heavy_vulkan_gpu(root, data, host)?.is_some(),
        };
        if has {
            return Ok(Some(*kind));
        }
    }
    Ok(None)
}

/// The llama.cpp heavy engine this host runs and its GPU kind: a pinned engine as configured,
/// "detect" as the engine of the GPU kind found. None: [ai].heavy_engine is not a llama.cpp
/// engine, or it detects and the host has no GPU it can serve on.
pub fn resolve_heavy_engine_on(
    root: &Path,
    data: &Value,
    host: &crate::gpu_topology::Host,
) -> Result<Option<(String, GpuKind)>, String> {
    let engine = heavy_engine(data);
    match heavy_engine_backend(data, engine)? {
        None => Ok(None),
        Some(HeavyBackend::Fixed(kind)) => Ok(Some((engine.to_owned(), kind))),
        Some(HeavyBackend::Detect) => match detect_heavy_kind(root, data, host)? {
            Some(kind) => Ok(Some((engine_for_kind(data, kind)?, kind))),
            None => Ok(None),
        },
    }
}

/// [`resolve_heavy_engine_on`] against the root's own /dev/dri and /sys/class/drm.
pub fn resolve_heavy_engine(
    root: &Path,
    data: &Value,
) -> Result<Option<(String, GpuKind)>, String> {
    let dri = root.join("dev/dri");
    let sys = root.join("sys/class/drm");
    resolve_heavy_engine_on(
        root,
        data,
        &crate::gpu_topology::Host {
            dri: &dri,
            sys: &sys,
        },
    )
}

/// `mios-gen model-tier --lane heavy --probe` (the auto unit's ExecCondition): the engine and GPU
/// kind the heavy lane would serve on, or None when this host has no GPU for it.
pub fn probe_heavy(root: &Path) -> Result<Option<(String, GpuKind)>, String> {
    let data = mios_resolver::resolve_merged(Some(root), false).map_err(|e| e.to_string())?;
    resolve_heavy_engine(root, &data)
}

fn sysfs_mib(path: &Path) -> Option<u64> {
    fs::read_to_string(path)
        .ok()?
        .trim()
        .parse::<u64>()
        .ok()
        .map(|bytes| bytes >> 20)
}

/// NVIDIA VRAM from nvidia-smi (on the live root only: it asks the driver).
fn cuda_measure(root: &Path, policy: &Policy) -> Option<GpuMeasure> {
    if root != Path::new("/") {
        return None;
    }
    for smi in &policy.nvidia_smi {
        let Ok(out) = std::process::Command::new(smi)
            .args(["--query-gpu=memory.total", "--format=csv,noheader,nounits"])
            .output()
        else {
            continue;
        };
        if !out.status.success() {
            continue;
        }
        if let Some(mib) = nvidia_total_mib(&String::from_utf8_lossy(&out.stdout)) {
            return Some(GpuMeasure {
                kind: GpuKind::Cuda,
                vram_mib: mib,
                gtt_mib: 0,
                apu: false,
            });
        }
    }
    None
}

/// The AMD device the Vulkan engine would serve on, sized from amdgpu sysfs (VRAM carve-out + GTT).
fn vulkan_measure(
    root: &Path,
    data: &Value,
    policy: &Policy,
) -> Result<Option<GpuMeasure>, String> {
    let dri = root.join("dev/dri");
    let sys = root.join("sys/class/drm");
    let host = crate::gpu_topology::Host {
        dri: &dri,
        sys: &sys,
    };
    let Some(gpu) = crate::gpu_topology::heavy_vulkan_gpu(root, data, &host)? else {
        return Ok(None);
    };
    let card = gpu
        .card
        .file_name()
        .ok_or("the Vulkan GPU has no DRM card name")?;
    let device = sys.join(card).join("device");
    let vram_mib = sysfs_mib(&device.join("mem_info_vram_total"))
        .ok_or_else(|| format!("{}: amdgpu mem_info_vram_total is unreadable", gpu.pci))?;
    let gtt_mib = sysfs_mib(&device.join("mem_info_gtt_total")).unwrap_or(0);
    Ok(Some(GpuMeasure {
        kind: GpuKind::Vulkan,
        vram_mib,
        gtt_mib,
        apu: vram_mib <= policy.apu_max_vram_mib,
    }))
}

/// The host's spec'd RAM, and the GPU of the heavy engine it resolves to. RAM is MemTotal: the
/// RAM MiOS specs for the host (a Blade's installed RAM, a WSL VM's rendered .wslconfig memory=),
/// never MemAvailable, so a pick follows the spec and the lanes' memory bounds keep it safe.
pub fn measure(root: &Path, data: &Value, policy: &Policy) -> Result<Measured, String> {
    let meminfo = fs::read_to_string(root.join("proc/meminfo"))
        .map_err(|e| format!("cannot read /proc/meminfo: {e}"))?;
    let ram_mib = meminfo_total_mib(&meminfo).ok_or("/proc/meminfo has no MemTotal in kB")?;
    let gpu = match resolve_heavy_engine(root, data)?.map(|(_, kind)| kind) {
        Some(GpuKind::Cuda) => cuda_measure(root, policy),
        Some(GpuKind::Vulkan) => vulkan_measure(root, data, policy)?,
        None => None,
    };
    Ok(Measured { ram_mib, gpu })
}

/// One lane's pick, with the arithmetic that led to it.
#[derive(Debug, Clone, PartialEq)]
pub struct LaneChoice {
    pub lane: String,
    pub entry: Option<TierEntry>,
    pub budget_mib: u64,
    pub pinned: bool,
    pub notes: Vec<String>,
}

impl LaneChoice {
    pub fn to_json(&self) -> Json {
        json!({"lane": self.lane, "budget_mib": self.budget_mib, "pinned": self.pinned,
            "entry": self.entry.as_ref().map(|e| json!({"name": e.name, "model_file": e.model_file,
                "context": e.context, "cache_type": e.cache_type, "need_mib": e.need_mib})),
            "arithmetic": self.notes})
    }
}

#[allow(clippy::too_many_arguments)]
fn choose(
    lane: &str,
    entries: &[TierEntry],
    budget: u64,
    ram: u64,
    present: &dyn Fn(&str) -> bool,
    beside: Option<&TierEntry>,
    pin: &str,
    mut notes: Vec<String>,
) -> LaneChoice {
    let done = |entry: Option<TierEntry>, pinned: bool, notes: Vec<String>| LaneChoice {
        lane: lane.to_owned(),
        entry,
        budget_mib: budget,
        pinned,
        notes,
    };
    if !pin.is_empty() {
        match entries.iter().find(|e| e.name == pin) {
            Some(e) if e.enable && present(&e.model_file) => {
                notes.push(format!("pinned to {} by [llamacpp.tier_pin]", e.name));
                return done(Some(e.clone()), true, notes);
            }
            Some(e) => notes.push(format!(
                "pin {} ignored: {}",
                e.name,
                if e.enable {
                    "its GGUF is not on disk"
                } else {
                    "the row is disabled"
                }
            )),
            None => notes.push(format!(
                "pin {pin} names no [[llamacpp.tiers.{lane}]] row; ignored"
            )),
        }
    }
    let fits: Vec<&TierEntry> = entries
        .iter()
        .filter(|e| {
            e.enable && e.min_ram_mib <= ram && e.need_mib <= budget && present(&e.model_file)
        })
        .collect();
    // Beside another lane's pick: the last fitting row of a different family, else the last
    // that at least avoids its GGUF, else the last that fits.
    let other_family = |e: &&&TierEntry| beside.is_none_or(|b| e.family != b.family);
    let other_file = |e: &&&TierEntry| beside.is_none_or(|b| e.model_file != b.model_file);
    let pick = fits
        .iter()
        .rev()
        .find(other_family)
        .or_else(|| fits.iter().rev().find(other_file))
        .or_else(|| fits.last())
        .map(|e| TierEntry::clone(e));
    if let Some(e) = pick {
        if let Some(b) = beside {
            let skipped: Vec<&str> = fits
                .iter()
                .rev()
                .take_while(|f| f.name != e.name)
                .map(|f| f.name.as_str())
                .collect();
            if !skipped.is_empty() {
                notes.push(format!(
                    "mixing families beside {} ({}): passed over {}",
                    b.name,
                    b.family,
                    skipped.join(", ")
                ));
            }
        }
        notes.push(format!(
            "picked {} ({}, {}, {} ctx, {} KV): needs {} of {budget} MiB",
            e.name, e.model_file, e.family, e.context, e.cache_type, e.need_mib
        ));
        return done(Some(e), false, notes);
    }
    let floor = entries
        .iter()
        .find(|e| e.enable && present(&e.model_file))
        .cloned();
    match &floor {
        Some(e) => notes.push(format!(
            "no row fits {budget} MiB; serving the tool-safe floor {} ({} MiB) so the lane keeps tools",
            e.name, e.need_mib
        )),
        None => notes.push("no enabled row's GGUF is on disk; the lane keeps its SSOT keys".into()),
    }
    done(floor, false, notes)
}

/// Both single-model lanes' picks for a measured host.
#[derive(Debug, Clone)]
pub struct Plan {
    pub measured: Measured,
    pub heavy: LaneChoice,
    pub cpu_node: LaneChoice,
}

pub fn plan(
    data: &Value,
    measured: &Measured,
    present: &dyn Fn(&str) -> bool,
) -> Result<Plan, String> {
    let policy = Policy::from_ssot(data)?;
    let llama = data.get("llamacpp").ok_or("missing [llamacpp]")?;
    let pin = |lane: &str| {
        at(llama, &["tier_pin", lane])
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_owned()
    };
    let ram = measured.ram_mib;
    let mut notes = Vec::new();
    let budget = match &measured.gpu {
        Some(g) if g.apu => {
            let mappable = g.vram_mib + g.gtt_mib;
            let share = (ram as f64 * policy.apu_gpu_ram_fraction) as u64;
            notes.push(format!(
                "APU: GPU-mappable {mappable} MiB (VRAM {} + GTT {}); RAM share {} x {ram} = {share} MiB",
                g.vram_mib, g.gtt_mib, policy.apu_gpu_ram_fraction
            ));
            Some(mappable.min(share))
        }
        Some(g) => {
            let usable = g.vram_mib.saturating_sub(policy.vram_reserve_mib);
            notes.push(format!(
                "dGPU: VRAM {} - reserve {} = {usable} MiB",
                g.vram_mib, policy.vram_reserve_mib
            ));
            Some(usable)
        }
        None => {
            notes.push(format!(
                "no GPU for heavy engine {:?}: the heavy lane does not serve",
                heavy_engine(data)
            ));
            None
        }
    };
    let heavy = match budget {
        Some(budget) => choose(
            "heavy",
            &tier_entries(data, "heavy")?,
            budget,
            ram,
            present,
            None,
            &pin("heavy"),
            notes,
        ),
        None => LaneChoice {
            lane: "heavy".into(),
            entry: None,
            budget_mib: 0,
            pinned: false,
            notes,
        },
    };
    let high = bound_mib(&text(llama, "cpu_node_memory_high", "llamacpp")?, ram)
        .ok_or("llamacpp.cpu_node_memory_high is not a memory bound")?;
    let light_max = at(llama, &["light_backends", "cpu", "memory_max"])
        .and_then(Value::as_str)
        .and_then(|b| bound_mib(b, ram))
        .unwrap_or(0);
    let gpu_use = match (&measured.gpu, &heavy.entry) {
        (Some(g), Some(e)) if g.apu => e.need_mib,
        _ => 0,
    };
    let left = ram
        .saturating_sub(light_max)
        .saturating_sub(policy.host_reserve_mib)
        .saturating_sub(gpu_use);
    let budget = high.min(left);
    let notes = vec![format!(
        "RAM {ram} MiB: cpu_node_memory_high {high} MiB; RAM - light lane max {light_max} - host reserve {} - APU GPU lane {gpu_use} = {left} MiB; budget {budget} MiB",
        policy.host_reserve_mib
    )];
    let cpu_node = choose(
        "cpu_node",
        &tier_entries(data, "cpu_node")?,
        budget,
        ram,
        present,
        heavy.entry.as_ref(),
        &pin("cpu_node"),
        notes,
    );
    Ok(Plan {
        measured: measured.clone(),
        heavy,
        cpu_node,
    })
}

/// [llamacpp.tier_lanes.<lane>].keys: the SSOT keys a lane's row overrides.
fn lane_keys(data: &Value, lane: &str) -> Result<BTreeMap<String, String>, String> {
    let keys = at(data, &["llamacpp", "tier_lanes", lane, "keys"])
        .and_then(Value::as_table)
        .ok_or_else(|| format!("missing llamacpp.tier_lanes.{lane}.keys"))?;
    keys.iter()
        .map(|(field, dotted)| {
            dotted
                .as_str()
                .map(|d| (field.clone(), d.to_owned()))
                .ok_or_else(|| {
                    format!("llamacpp.tier_lanes.{lane}.keys.{field} must be a dotted SSOT key")
                })
        })
        .collect()
}

fn set_dotted(data: &mut Value, dotted: &str, value: Value) -> Result<(), String> {
    let mut parts: Vec<&str> = dotted.split('.').collect();
    let last = parts.pop().ok_or("empty SSOT key")?;
    let mut cur = data;
    for part in parts {
        cur = cur
            .get_mut(part)
            .ok_or_else(|| format!("{dotted}: [{part}] is missing"))?;
    }
    cur.as_table_mut()
        .ok_or_else(|| format!("{dotted}: parent is not a table"))?
        .insert(last.to_owned(), value);
    Ok(())
}

/// The lane's env file: the row's values under the canonical MIOS_* names of the keys it overrides.
pub fn env_contents(
    data: &Value,
    choice: &LaneChoice,
    extra: &[(&str, String)],
) -> Result<String, String> {
    let keys = lane_keys(data, &choice.lane)?;
    let mut out = format!(
        "# AI-hint: Model tier of the {} lane, written by `mios-gen model-tier` from [[llamacpp.tiers.{}]] at every start; never edit.\n",
        choice.lane, choice.lane
    );
    let key = |field: &str| {
        keys.get(field)
            .map(|d| mios_resolver::names::canonical_name(d))
            .ok_or_else(|| {
                format!(
                    "llamacpp.tier_lanes.{}.keys.{field} is missing",
                    choice.lane
                )
            })
    };
    if let Some(e) = &choice.entry {
        for (field, value) in [
            ("model_file", e.model_file.clone()),
            ("context", e.context.to_string()),
            ("cache_type", e.cache_type.clone()),
        ] {
            out.push_str(&format!("{}={value}\n", key(field)?));
        }
    }
    for (field, value) in extra.iter() {
        let field: &str = field;
        // Law 10 bare value: an image reference (":", "@") and a CDI name ("=") are safe under
        // systemd EnvironmentFile=, bash source and podman --env-file; whitespace, quotes,
        // "$", "`" and "#" never are.
        if value.is_empty()
            || !value
                .bytes()
                .all(|c| c.is_ascii_alphanumeric() || b"/._-:=@".contains(&c))
        {
            return Err(format!("{field} value {value:?} is not a bare env value"));
        }
        out.push_str(&format!("{}={value}\n", key(field)?));
    }
    Ok(out)
}

fn write_atomic(target: &Path, contents: &str) -> Result<bool, String> {
    use std::io::Write;
    if fs::read_to_string(target).ok().as_deref() == Some(contents) {
        return Ok(false);
    }
    let parent = target.parent().ok_or("env file has no parent")?;
    fs::create_dir_all(parent).map_err(|e| format!("{}: {e}", parent.display()))?;
    let mut tmp = tempfile::NamedTempFile::new_in(parent).map_err(|e| e.to_string())?;
    tmp.write_all(contents.as_bytes())
        .map_err(|e| e.to_string())?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        tmp.as_file()
            .set_permissions(fs::Permissions::from_mode(0o644))
            .map_err(|e| e.to_string())?;
    }
    tmp.as_file().sync_all().map_err(|e| e.to_string())?;
    tmp.persist(target).map_err(|e| e.to_string())?;
    Ok(true)
}

/// `mios-gen model-tier --lane heavy|cpu-node [--apply]`: measure, pick, admit (heavy), write.
pub fn run_model_tier(root: &Path, lane: &str, apply: bool) -> Result<Json, String> {
    let lane = match lane {
        "heavy" => "heavy",
        "cpu-node" | "cpu_node" => "cpu_node",
        other => {
            return Err(format!(
                "unknown tier lane {other:?}: use heavy or cpu-node"
            ))
        }
    };
    let data = mios_resolver::resolve_merged(Some(root), false).map_err(|e| e.to_string())?;
    let policy = Policy::from_ssot(&data)?;
    let measured = measure(root, &data, &policy)?;
    let models_dir = text(
        data.get("llamacpp").ok_or("missing [llamacpp]")?,
        "models_dir",
        "llamacpp",
    )?;
    let models = root.join(models_dir.trim_start_matches('/'));
    let present = |file: &str| models.join(file).is_file();
    let plan = plan(&data, &measured, &present)?;
    let choice = if lane == "heavy" {
        &plan.heavy
    } else {
        &plan.cpu_node
    };
    let mut extra: Vec<(&str, String)> = Vec::new();
    let mut admission = Json::Null;
    if lane == "heavy" {
        let entry = choice.entry.as_ref().ok_or_else(|| {
            format!(
                "the heavy lane has no tool-safe tier for this host ({}); it does not start and the CPU lanes keep serving",
                choice.notes.join("; ")
            )
        })?;
        let keys = lane_keys(&data, "heavy")?;
        let mut overlaid = data.clone();
        for (field, value) in [
            ("model_file", Value::String(entry.model_file.clone())),
            ("context", Value::Integer(entry.context as i64)),
            ("cache_type", Value::String(entry.cache_type.clone())),
        ] {
            let dotted = keys
                .get(field)
                .ok_or_else(|| format!("llamacpp.tier_lanes.heavy.keys.{field} is missing"))?;
            set_dotted(&mut overlaid, dotted, value)?;
        }
        admission = crate::gpu_topology::heavy_backend_plan(root, &overlaid)?;
        // What the "auto" unit's placeholders need, whichever backend was admitted: the device
        // Podman adds (the verified render node, or the exact CUDA CDI name), its group,
        // llama.cpp's --device and the engine image.
        let field = |key: &str| admission.get(key).and_then(Json::as_str).map(str::to_owned);
        let device = field("render_node")
            .or_else(|| field("cdi_device"))
            .ok_or("the heavy admission named no device")?;
        // A device every user may open (WSL's world read/write dxg) needs no group: the
        // unit then adds the SSOT default, the unprivileged nobody group.
        let gid = match admission.get("device_gid") {
            Some(Json::Null) => {
                let dotted = keys
                    .get("device_gid")
                    .ok_or("llamacpp.tier_lanes.heavy.keys.device_gid is missing")?;
                at(&data, &dotted.split('.').collect::<Vec<_>>())
                    .and_then(Value::as_str)
                    .ok_or_else(|| format!("{dotted} must be a group id string"))?
                    .to_owned()
            }
            other => other
                .and_then(Json::as_u64)
                .ok_or("the heavy admission named no device group")?
                .to_string(),
        };
        let llama_device =
            field("device").ok_or("the heavy admission named no llama.cpp device")?;
        let image = field("image").ok_or("the heavy admission named no engine image")?;
        // --n-gpu-layers (all, a count, or auto for --fit) and --fit's free-memory margin.
        let layers = field("gpu_layers").ok_or("the heavy admission named no GPU layer setting")?;
        let fit_target = admission
            .get("fit_target_mib")
            .and_then(Json::as_u64)
            .ok_or("the heavy admission named no --fit-target margin")?;
        extra.push(("device", device));
        extra.push(("device_gid", gid));
        extra.push(("llama_device", llama_device));
        extra.push(("image", image));
        extra.push(("gpu_layers", layers));
        extra.push(("fit_target_mib", fit_target.to_string()));
    }
    let contents = env_contents(&data, choice, &extra)?;
    let target = root.join(
        policy
            .env_dir
            .join(format!("{}.env", lane.replace('_', "-")))
            .strip_prefix("/")
            .map_err(|e| e.to_string())?,
    );
    let changed = apply && write_atomic(&target, &contents)?;
    Ok(
        json!({"lane": lane, "measured": {"ram_mib": measured.ram_mib,
        "gpu": measured.gpu.as_ref().map(|g| json!({"kind": format!("{:?}", g.kind),
            "vram_mib": g.vram_mib, "gtt_mib": g.gtt_mib, "apu": g.apu}))},
        "choice": choice.to_json(), "admission": admission, "env_file": target,
        "contents": contents, "changed": changed}),
    )
}

/// Every KV type, quant and fetch the [llamacpp.tool_safe] floor rules on, judged.
pub fn tool_safe_violations(data: &Value, source_map: &str) -> Result<Vec<String>, String> {
    let floor = Floor::from_ssot(data)?;
    let facts = gguf_facts(data)?;
    let llama = data.get("llamacpp").ok_or("missing [llamacpp]")?;
    let mut out = Vec::new();
    for key in ["cpu_node_cache_type", "heavy_cache_type"] {
        let kv = text(llama, key, "llamacpp")?;
        if !floor.chat_kv.contains(&kv) {
            out.push(format!(
                "llamacpp.{key} = {kv:?} is below the chat KV floor {:?}",
                floor.chat_kv
            ));
        }
    }
    let mut fetched: Vec<String> = at(data, &["ai", "firstboot_models"])
        .and_then(Value::as_array)
        .map(|rows| {
            rows.iter()
                .filter_map(|r| r.get("name").and_then(Value::as_str).map(str::to_owned))
                .collect()
        })
        .unwrap_or_default();
    if let Some(spec) = llama.get("bake_models").and_then(Value::as_str) {
        fetched.extend(
            spec.split(',')
                .filter_map(|e| e.split('=').next())
                .map(|n| n.trim().to_owned())
                .filter(|n| !n.is_empty()),
        );
    }
    let judge =
        |out: &mut Vec<String>, what: &str, file: &str, embedding: bool| match facts.get(file) {
            None => out.push(format!("{what}: {file} has no [llamacpp.gguf] facts row")),
            Some(f) => {
                if (f.kind == "embedding") != embedding {
                    out.push(format!("{what}: {file} is a {} model", f.kind));
                }
                if let Some(v) = floor.quant_violation(file, f) {
                    out.push(format!("{what}: {v}"));
                }
            }
        };
    let lanes = at(data, &["llamacpp", "tiers"])
        .and_then(Value::as_table)
        .ok_or("missing [llamacpp.tiers]")?;
    for lane in lanes.keys() {
        let embedding = lane == "embed";
        let allowed = if embedding {
            &floor.embed_kv
        } else {
            &floor.chat_kv
        };
        for e in tier_entries(data, lane)? {
            let what = format!("llamacpp.tiers.{lane}.{}", e.name);
            if !allowed.contains(&e.cache_type) {
                out.push(format!(
                    "{what}: KV {} is below the floor {allowed:?}",
                    e.cache_type
                ));
            }
            judge(&mut out, &what, &e.model_file, embedding);
            if e.enable && !fetched.contains(&e.model_file) {
                out.push(format!(
                    "{what}: {} is in neither [[ai.firstboot_models]] nor [llamacpp].bake_models, so no host can select it",
                    e.model_file
                ));
            }
        }
    }
    let map: Yaml =
        serde_yaml::from_str(source_map).map_err(|e| format!("source model map: {e}"))?;
    let models = map
        .get("models")
        .and_then(Yaml::as_mapping)
        .ok_or("source model map has no models mapping")?;
    for (name, model) in models {
        let name = name.as_str().unwrap_or("?");
        let Some(cmd) = model.get("cmd").and_then(Yaml::as_str) else {
            out.push(format!("model map {name}: no cmd"));
            continue;
        };
        let argv: Vec<&str> = cmd.split_whitespace().collect();
        let arg = |flag: &str| {
            argv.iter()
                .position(|a| *a == flag)
                .and_then(|i| argv.get(i + 1))
                .copied()
        };
        let embedding = argv.contains(&"--embedding");
        let (k, v) = (arg("--cache-type-k"), arg("--cache-type-v"));
        if k != v {
            out.push(format!("model map {name}: asymmetric KV {k:?}/{v:?}"));
        }
        if !embedding {
            match k {
                Some(kv) if floor.chat_kv.iter().any(|a| a == kv) => {}
                other => out.push(format!(
                    "model map {name}: KV {other:?} is below the chat floor {:?}",
                    floor.chat_kv
                )),
            }
        } else if let Some(kv) = k {
            if !floor.embed_kv.iter().any(|a| a == kv) {
                out.push(format!(
                    "model map {name}: embedding KV {kv} is below {:?}",
                    floor.embed_kv
                ));
            }
        }
        match arg("--model").and_then(|m| m.rsplit('/').next()) {
            Some(file) => judge(&mut out, &format!("model map {name}"), file, embedding),
            None => out.push(format!("model map {name}: no --model")),
        }
    }
    Ok(out)
}

/// `mios-gen tool-safe --check`: Err lists every violation (the vendor tree; the floor is vendor policy).
pub fn run_tool_safe(root: &Path) -> Result<usize, String> {
    let ssot = root.join("usr/share/mios/mios.toml");
    let data: Value =
        toml::from_str(&fs::read_to_string(&ssot).map_err(|e| format!("{}: {e}", ssot.display()))?)
            .map_err(|e| format!("{}: {e}", ssot.display()))?;
    let config = text(
        data.get("llamacpp").ok_or("missing [llamacpp]")?,
        "config",
        "llamacpp",
    )?;
    let map_path = root.join(config.trim_start_matches('/'));
    let source =
        fs::read_to_string(&map_path).map_err(|e| format!("{}: {e}", map_path.display()))?;
    let violations = tool_safe_violations(&data, &source)?;
    if violations.is_empty() {
        let entries = at(&data, &["llamacpp", "tiers"])
            .and_then(Value::as_table)
            .map(|t| t.values().filter_map(Value::as_array).map(Vec::len).sum())
            .unwrap_or(0);
        Ok(entries)
    } else {
        Err(violations.join("\n"))
    }
}

#[cfg(test)]
mod tier_tests {
    use super::*;

    fn vendor() -> Value {
        include_str!("../../../../usr/share/mios/mios.toml")
            .parse()
            .unwrap()
    }

    fn all_present(_: &str) -> bool {
        true
    }

    fn pick(plan: &Plan) -> (Option<String>, Option<String>) {
        (
            plan.heavy.entry.as_ref().map(|e| e.name.clone()),
            plan.cpu_node.entry.as_ref().map(|e| e.name.clone()),
        )
    }

    fn with_engine(engine: &str) -> Value {
        let mut d = vendor();
        d["ai"]["heavy_engine"] = Value::String(engine.into());
        d
    }

    fn gpu(kind: GpuKind, vram_mib: u64, gtt_mib: u64, apu: bool) -> Option<GpuMeasure> {
        Some(GpuMeasure {
            kind,
            vram_mib,
            gtt_mib,
            apu,
        })
    }

    #[test]
    fn the_shipped_ssot_and_model_map_meet_the_tool_safe_floor() {
        let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../..");
        let n = run_tool_safe(&root).unwrap_or_else(|v| panic!("tool-safe violations:\n{v}"));
        assert!(n > 0);
    }

    #[test]
    fn floor_rules_by_model_class() {
        let floor = Floor::from_ssot(&vendor()).unwrap();
        let f = |quant: &str, params_b: f64, active_b: f64, reasoning: bool| Facts {
            quant: quant.into(),
            params_b,
            active_b,
            reasoning,
            kind: "chat".into(),
            family: "x".into(),
        };
        // sub-4B non-reasoning and <= 2B-active MoE: Q8_0
        assert!(floor
            .quant_violation("x", &f("Q4_K_M", 1.5, 1.5, false))
            .is_some());
        assert!(floor
            .quant_violation("x", &f("Q8_0", 1.5, 1.5, false))
            .is_none());
        assert!(floor
            .quant_violation("x", &f("Q6_K", 8.5, 1.5, false))
            .is_some());
        // dense >= 4B or reasoning: Q4_K_M; never IQ2/Q3_K
        assert!(floor
            .quant_violation("x", &f("Q4_K_M", 8.8, 8.8, false))
            .is_none());
        assert!(floor
            .quant_violation("x", &f("Q4_K_M", 4.2, 4.2, true))
            .is_none());
        assert!(floor
            .quant_violation("x", &f("Q3_K_M", 8.8, 8.8, false))
            .is_some());
        assert!(floor
            .quant_violation("x", &f("IQ2_M", 35.0, 3.0, true))
            .is_some());
        // a model's native format meets any floor
        assert!(floor
            .quant_violation("x", &f("MXFP4", 20.9, 3.6, true))
            .is_none());
    }

    #[test]
    fn a_planted_q4_kv_lane_or_sub_floor_quant_is_named() {
        let mut d = vendor();
        let map = include_str!("../../../../usr/share/mios/llamacpp/mios-llm-light.yaml");
        d["llamacpp"]["cpu_node_cache_type"] = Value::String("q4_0".into());
        // a sub-1B non-reasoning micro at 4-bit: below its Q8_0 floor
        d["llamacpp"]["gguf"]["lfm2-700m.gguf"]["quant"] = Value::String("Q4_K_M".into());
        let v = tool_safe_violations(&d, map).unwrap();
        assert!(v.iter().any(|m| m.contains("cpu_node_cache_type")), "{v:?}");
        assert!(
            v.iter()
                .any(|m| m.contains("lfm2-700m.gguf") && m.contains("below")),
            "{v:?}"
        );
        let planted = map.replace(
            "--cache-type-k q8_0 --cache-type-v q8_0",
            "--cache-type-k q4_0 --cache-type-v q4_0",
        );
        let v = tool_safe_violations(&vendor(), &planted).unwrap();
        assert!(
            v.iter()
                .any(|m| m.starts_with("model map") && m.contains("q4_0")),
            "{v:?}"
        );
    }

    #[test]
    fn bounds_and_meters_parse() {
        assert_eq!(bound_mib("65%", 13931), Some(9055));
        assert_eq!(bound_mib("512M", 1), Some(512));
        assert_eq!(bound_mib("8G", 1), Some(8192));
        assert_eq!(bound_mib("lots", 1), None);
        assert_eq!(
            meminfo_total_mib("MemTotal:       14266132 kB\nMemFree: 1 kB\n"),
            Some(13931)
        );
        assert_eq!(nvidia_total_mib("8192\n"), Some(8192));
        assert_eq!(nvidia_total_mib("24564\n8192\n"), Some(24564));
    }

    // Acceptance hosts. MiB throughout; the arithmetic is in each plan's notes.

    #[test]
    fn x570_mios_dev_at_its_mios_spec_16gb_vm() {
        // MiOS specs the X570's WSL VM at memory=16GB: 32 GB host - max(8, 50% of 32) =
        // 16 ([bootstrap.dev_vm.host_reserve]); such a VM reads MemTotal ~15970 MiB.
        let d = with_engine("llamacpp");
        let m = Measured {
            ram_mib: 15970,
            gpu: gpu(GpuKind::Cuda, 8192, 0, false),
        };
        let p = plan(&d, &m, &all_present).unwrap();
        // heavy: 8192 - 1792 = 6400 -> gpu-6g, Qwen3.5-4B Q4_K_M 64k (4847), family qwen
        // cpu: min(65% = 10380, 15970 - 25% 3992 - 2560) = 9418. Fitting rows: ram-floor (lfm),
        //      ram-6g and ram-12g (granite), ram-16g (qwen, 7318); the 8B rows (9482, 9514) need
        //      more than a 16 GB spec. ram-16g is qwen like the heavy lane, so the family mix
        //      takes ram-12g, Granite 4.2 3B Q8_0 64k (6945).
        assert_eq!(
            pick(&p),
            (Some("gpu-6g".into()), Some("ram-12g".into())),
            "{:?}",
            p
        );
        assert_eq!(p.heavy.budget_mib, 6400);
        assert_eq!(p.cpu_node.budget_mib, 9418);
        let (heavy, cpu) = (
            p.heavy.entry.as_ref().unwrap(),
            p.cpu_node.entry.as_ref().unwrap(),
        );
        assert_eq!(
            (cpu.model_file.as_str(), cpu.context, cpu.family.as_str()),
            ("granite-4.2-3b-Q8_0.gguf", 65536, "granite")
        );
        assert_eq!(heavy.family, "qwen");
        assert!(
            p.cpu_node
                .notes
                .iter()
                .any(|n| n.contains("passed over ram-16g")),
            "{:?}",
            p.cpu_node.notes
        );
    }

    #[test]
    fn a_16gb_spec_without_a_qwen_heavy_lane_serves_qwen35_on_the_cpu_node() {
        // The same 16 GB spec with no heavy lane: nothing to mix against, so the biggest
        // fitting row wins.
        let d = with_engine("vllm");
        let m = Measured {
            ram_mib: 15970,
            gpu: None,
        };
        let p = plan(&d, &m, &all_present).unwrap();
        assert_eq!(pick(&p), (None, Some("ram-16g".into())), "{:?}", p);
    }

    fn row(name: &str, file: &str, family: &str, need_mib: u64) -> TierEntry {
        TierEntry {
            name: name.into(),
            model_file: file.into(),
            context: 32768,
            cache_type: "q8_0".into(),
            need_mib,
            min_ram_mib: 0,
            enable: true,
            family: family.into(),
        }
    }

    #[test]
    fn the_cpu_node_mixes_families_then_avoids_the_gguf_then_takes_what_fits() {
        let (a, b, c) = (
            row("a", "lfm.gguf", "lfm", 1000),
            row("b", "q8.gguf", "qwen", 2000),
            row("c", "q4.gguf", "qwen", 3000),
        );
        let heavy = row("h", "q4.gguf", "qwen", 1);
        let name = |entries: &[TierEntry], beside: Option<&TierEntry>, pin: &str| {
            choose(
                "cpu_node",
                entries,
                9999,
                99999,
                &all_present,
                beside,
                pin,
                vec![],
            )
            .entry
            .map(|e| e.name)
        };
        let all = [a.clone(), b.clone(), c.clone()];
        // Nothing beside: the last fitting row.
        assert_eq!(name(&all, None, ""), Some("c".into()));
        // Beside a qwen heavy pick: the last fitting row of another family.
        assert_eq!(name(&all, Some(&heavy), ""), Some("a".into()));
        // Only qwen rows fit: the last one that is not the heavy lane's own GGUF.
        assert_eq!(
            name(&[b.clone(), c.clone()], Some(&heavy), ""),
            Some("b".into())
        );
        // Only the heavy lane's own GGUF fits: still served, the lane keeps tools.
        assert_eq!(name(&[c.clone()], Some(&heavy), ""), Some("c".into()));
        // A pin wins over the mix.
        assert_eq!(name(&all, Some(&heavy), "b"), Some("b".into()));
        // A row too big for the budget is never the mix's answer.
        let big = [row("a", "lfm.gguf", "lfm", 99_999), b, c];
        assert_eq!(name(&big, Some(&heavy), ""), Some("b".into()));
    }

    #[test]
    fn every_tier_row_has_a_family() {
        let d = vendor();
        for lane in ["heavy", "cpu_node", "micro", "embed"] {
            for e in tier_entries(&d, lane).unwrap() {
                assert!(!e.family.is_empty(), "{lane}.{}", e.name);
            }
        }
    }

    #[test]
    fn x570_mios_dev_over_its_spec_at_24gb() {
        // The live VM is hand-set to 24 GB, over the 16 GB MiOS spec: the pick follows the
        // VM it is given, which is why the spec is applied on the Windows host.
        let d = with_engine("llamacpp");
        let m = Measured {
            ram_mib: 24034,
            gpu: gpu(GpuKind::Cuda, 8192, 0, false),
        };
        let p = plan(&d, &m, &all_present).unwrap();
        // cpu: min(15622, 24034-6008-2560) = 15466 -> ram-20g LFM2.5-8B-A1B (9514)
        assert_eq!(
            pick(&p),
            (Some("gpu-6g".into()), Some("ram-20g".into())),
            "{:?}",
            p
        );
        assert_eq!(p.cpu_node.budget_mib, 15466);
    }

    #[test]
    fn ps5_cpu_only_never_selects_the_dense_8b() {
        let d = with_engine("vllm");
        let m = Measured {
            ram_mib: 13931,
            gpu: None,
        };
        let p = plan(&d, &m, &all_present).unwrap();
        // cpu: min(9055, 13931-3482-2560) = 7889 -> ram-12g (6945); ram-16g and up need a 16 GB spec
        assert_eq!(pick(&p), (None, Some("ram-12g".into())), "{:?}", p);
        assert!(p.cpu_node.entry.as_ref().unwrap().need_mib <= bound_mib("65%", 13931).unwrap());
    }

    #[test]
    fn ps5_apu_serves_on_gpu_and_cpu_inside_its_ram() {
        let d = with_engine("llamacpp-vulkan");
        let m = Measured {
            ram_mib: 13931,
            gpu: gpu(GpuKind::Vulkan, 1024, 6965, true),
        };
        let p = plan(&d, &m, &all_present).unwrap();
        // GPU: min(7989, 0.40 x 13931 = 5572) -> gpu-6g (4847)
        // CPU: min(9055, 13931-3482-2560-4847 = 3042) -> ram-floor LFM3 (2273)
        assert_eq!(
            pick(&p),
            (Some("gpu-6g".into()), Some("ram-floor".into())),
            "{:?}",
            p
        );
        let total = 3482
            + p.heavy.entry.as_ref().unwrap().need_mib
            + p.cpu_node.entry.as_ref().unwrap().need_mib
            + 2560;
        assert!(total <= 13931, "{total}");
        assert_ne!(
            p.heavy.entry.unwrap().model_file,
            p.cpu_node.entry.unwrap().model_file
        );
    }

    #[test]
    fn b550_renoir_apu_serves_on_igpu_and_cpu() {
        let d = with_engine("llamacpp-vulkan");
        let m = Measured {
            ram_mib: 31416,
            gpu: gpu(GpuKind::Vulkan, 512, 15708, true),
        };
        let p = plan(&d, &m, &all_present).unwrap();
        // GPU: min(16220, 12566) -> gpu-10g (7318); CPU: min(20420, 31416-7854-2560-7318 = 13684) -> ram-32g (13288)
        assert_eq!(
            pick(&p),
            (Some("gpu-10g".into()), Some("ram-32g".into())),
            "{:?}",
            p
        );
    }

    #[test]
    fn a_64gb_cpu_host_serves_qwen36_at_q8() {
        let d = with_engine("vllm");
        let m = Measured {
            ram_mib: 64300,
            gpu: None,
        };
        let p = plan(&d, &m, &all_present).unwrap();
        assert_eq!(pick(&p), (None, Some("ram-64g".into())), "{:?}", p);
    }

    #[test]
    fn a_24gb_dgpu_host_avoids_loading_one_gguf_twice() {
        let d = with_engine("llamacpp");
        let m = Measured {
            ram_mib: 32100,
            gpu: gpu(GpuKind::Cuda, 24564, 0, false),
        };
        let p = plan(&d, &m, &all_present).unwrap();
        // heavy: 22772 -> gpu-24g (gpt-oss 128k); the cpu-node's biggest fit, ram-32g, is gpt-oss
        // too, so the family mix takes ram-20g (LFM2.5-8B-A1B, lfm)
        assert_eq!(
            pick(&p),
            (Some("gpu-24g".into()), Some("ram-20g".into())),
            "{:?}",
            p
        );
    }

    #[test]
    fn every_acceptance_pick_meets_the_floor() {
        let d = vendor();
        let floor = Floor::from_ssot(&d).unwrap();
        let facts = gguf_facts(&d).unwrap();
        for lane in ["heavy", "cpu_node"] {
            for e in tier_entries(&d, lane).unwrap() {
                assert!(floor.chat_kv.contains(&e.cache_type), "{}", e.name);
                assert!(
                    floor
                        .quant_violation(&e.model_file, &facts[&e.model_file])
                        .is_none(),
                    "{}",
                    e.name
                );
            }
        }
    }

    #[test]
    fn the_static_cpu_node_fallback_is_the_baked_tool_safe_floor() {
        let d = vendor();
        let llama = &d["llamacpp"];
        let floor = &tier_entries(&d, "cpu_node").unwrap()[0];
        assert_eq!(floor.name, "ram-floor");
        assert_eq!(
            llama["cpu_node_model_file"].as_str(),
            Some(floor.model_file.as_str())
        );
        assert_eq!(
            llama["cpu_node_context"].as_integer(),
            Some(floor.context as i64)
        );
        assert_eq!(
            llama["cpu_node_cache_type"].as_str(),
            Some(floor.cache_type.as_str())
        );
        let baked = llama["bake_models"].as_str().unwrap();
        assert!(
            baked
                .split(',')
                .any(|e| e.split('=').next() == Some(floor.model_file.as_str())),
            "{baked}"
        );
        // Fits a 14G Blade before its first fetch: inside cpu_node_memory_high.
        assert!(floor.need_mib <= bound_mib("65%", 13931).unwrap());
    }

    #[test]
    fn heavy_env_values_take_image_and_cdi_references_and_refuse_shell_text() {
        let d = vendor();
        let m = Measured {
            ram_mib: 15970,
            gpu: gpu(GpuKind::Cuda, 8192, 0, false),
        };
        let p = plan(&d, &m, &all_present).unwrap();
        let env = env_contents(
            &d,
            &p.heavy,
            &[
                (
                    "device",
                    "nvidia.com/gpu=GPU-11111111-2222-3333-4444-555555555555".into(),
                ),
                ("device_gid", "39".into()),
                ("llama_device", "CUDA0".into()),
                ("image", "ghcr.io/mostlygeek/llama-swap:cuda".into()),
                ("gpu_layers", "auto".into()),
                ("fit_target_mib", "1024".into()),
            ],
        )
        .unwrap();
        for line in [
            "MIOS_LLAMACPP_HEAVY_DEVICE=nvidia.com/gpu=GPU-11111111-2222-3333-4444-555555555555\n",
            "MIOS_LLAMACPP_HEAVY_DEVICE_GID=39\n",
            "MIOS_LLAMACPP_HEAVY_LLAMA_DEVICE=CUDA0\n",
            "MIOS_LLAMACPP_HEAVY_IMAGE=ghcr.io/mostlygeek/llama-swap:cuda\n",
            "MIOS_LLAMACPP_HEAVY_GPU_LAYERS=auto\n",
            "MIOS_LLAMACPP_HEAVY_FIT_TARGET_MIB=1024\n",
        ] {
            assert!(env.contains(line), "{env}");
        }
        for bad in ["$(id)", "a b", "x#y", "`id`", "\"q\""] {
            assert!(
                env_contents(&d, &p.heavy, &[("image", bad.into())]).is_err(),
                "{bad}"
            );
        }
    }

    #[test]
    fn missing_ggufs_fall_back_to_the_floor_then_to_the_ssot_keys() {
        let d = with_engine("vllm");
        let m = Measured {
            ram_mib: 13931,
            gpu: None,
        };
        let only_floor = |f: &str| f == "LFM3-1.7B-Q8_0.gguf";
        let p = plan(&d, &m, &only_floor).unwrap();
        assert_eq!(p.cpu_node.entry.as_ref().unwrap().name, "ram-floor");
        let none = |_: &str| false;
        let p = plan(&d, &m, &none).unwrap();
        assert!(p.cpu_node.entry.is_none());
        let env = env_contents(&d, &p.cpu_node, &[]).unwrap();
        assert!(!env.contains("MIOS_LLAMACPP_CPU_NODE_MODEL_FILE="), "{env}");
    }

    #[test]
    fn a_pin_wins_and_the_env_file_carries_canonical_names() {
        let mut d = with_engine("vllm");
        d["llamacpp"]["tier_pin"]["cpu_node"] = Value::String("ram-6g".into());
        let m = Measured {
            ram_mib: 64300,
            gpu: None,
        };
        let p = plan(&d, &m, &all_present).unwrap();
        assert!(p.cpu_node.pinned);
        let env = env_contents(&d, &p.cpu_node, &[]).unwrap();
        assert!(
            env.contains("MIOS_LLAMACPP_CPU_NODE_MODEL_FILE=granite-4.2-3b-Q8_0.gguf\n"),
            "{env}"
        );
        assert!(
            env.contains("MIOS_LLAMACPP_CPU_NODE_CONTEXT=32768\n"),
            "{env}"
        );
        assert!(
            env.contains("MIOS_LLAMACPP_CPU_NODE_CACHE_TYPE=q8_0\n"),
            "{env}"
        );
    }
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
