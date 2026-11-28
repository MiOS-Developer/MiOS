// AI-hint: `mios-bake-plan cache key|restore|save`: the compiled-output cache of the slow from-source bakes ([build.bake_cache]). The key hashes every input, a restore proves every file first, and any fault is a miss, never a failed bake.
// AI-related: usr/share/mios/mios.toml, automation/65-bake-hyprland.sh, automation/66-bake-quickshell.sh, automation/69-bake-lookingglass-client.sh, .github/workflows/mios-ci.yml
// AI-functions: dispatch, Config::load, material, key_of, save, restore, explain_miss
//! A bake that compiles for an hour must still compile when the cache is cold,
//! missing or damaged, so nothing here can fail a build: a key that cannot be
//! computed means "no cache", a restore that cannot prove its entry is a miss,
//! and a save that cannot complete leaves the cache as it was.
//!
//! An entry is `<dir>/<unit>/<key>/` holding `manifest.json` (the key's inputs,
//! the script's metadata and every path with its type, mode and sha256) and
//! `files/`, the output tree. The key is the sha256 of the inputs, so a restore
//! re-hashes the stored inputs as well as the files: an entry renamed under
//! another key, or edited in place, is refused and deleted.

use serde_json::{json, Value as Json};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::io::{self, Read};
use std::path::{Component, Path, PathBuf};
use std::process::Command;
use toml::Value;

const MANIFEST: &str = "manifest.json";
const FILES: &str = "files";
const LOG: &str = "[bake-cache]";

/// One cached bake output, as [build.bake_cache.units.<name>] declares it.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Unit {
    pub script: String,
    pub inputs: Vec<String>,
    pub inputs_from: Vec<String>,
    pub ssot: Vec<String>,
    pub rpms: Vec<String>,
    pub rpms_from: Vec<String>,
}

/// [build.bake_cache] as the bake reads it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Config {
    pub dir: PathBuf,
    pub schema: i64,
    pub toolchain: Vec<String>,
    pub units: BTreeMap<String, Unit>,
}

fn lookup<'a>(v: &'a Value, dotted: &str) -> Option<&'a Value> {
    dotted.split('.').try_fold(v, |acc, k| acc.get(k))
}

fn strings(v: Option<&Value>, what: &str) -> Result<Vec<String>, String> {
    match v {
        None => Ok(Vec::new()),
        Some(Value::Array(a)) => a
            .iter()
            .map(|x| {
                x.as_str()
                    .map(String::from)
                    .ok_or(format!("{what} holds a non-string"))
            })
            .collect(),
        Some(_) => Err(format!("{what} is not an array of strings")),
    }
}

fn valid_unit_name(name: &str) -> bool {
    !name.is_empty()
        && name.len() <= 64
        && name
            .bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
        && !name.starts_with('-')
}

fn valid_key(key: &str) -> bool {
    key.len() == 64 && key.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
}

impl Config {
    pub fn load(ssot: &Value) -> Result<Config, String> {
        let t = lookup(ssot, "build.bake_cache").ok_or("mios.toml has no [build.bake_cache]")?;
        let dir = t
            .get("dir")
            .and_then(Value::as_str)
            .filter(|d| d.starts_with('/'))
            .ok_or("[build.bake_cache].dir must be an absolute path")?;
        let schema = t
            .get("schema")
            .and_then(Value::as_integer)
            .ok_or("[build.bake_cache].schema must be an integer")?;
        let toolchain = strings(t.get("toolchain"), "[build.bake_cache].toolchain")?;
        let mut units = BTreeMap::new();
        for (name, u) in t
            .get("units")
            .and_then(Value::as_table)
            .into_iter()
            .flatten()
        {
            if !valid_unit_name(name) {
                return Err(format!(
                    "[build.bake_cache.units.{name}]: names are [a-z0-9-]"
                ));
            }
            let what = |k: &str| format!("[build.bake_cache.units.{name}].{k}");
            let script = u
                .get("script")
                .and_then(Value::as_str)
                .filter(|s| !s.is_empty())
                .ok_or(format!("{} is required", what("script")))?;
            units.insert(
                name.clone(),
                Unit {
                    script: script.to_string(),
                    inputs: strings(u.get("inputs"), &what("inputs"))?,
                    inputs_from: strings(u.get("inputs_from"), &what("inputs_from"))?,
                    ssot: strings(u.get("ssot"), &what("ssot"))?,
                    rpms: strings(u.get("rpms"), &what("rpms"))?,
                    rpms_from: strings(u.get("rpms_from"), &what("rpms_from"))?,
                },
            );
        }
        Ok(Config {
            dir: PathBuf::from(dir),
            schema,
            toolchain,
            units,
        })
    }
}

fn sha256_hex(bytes: &[u8]) -> String {
    let mut h = Sha256::new();
    h.update(bytes);
    format!("{:x}", h.finalize())
}

fn sha256_file(path: &Path) -> io::Result<(String, u64)> {
    let mut f = fs::File::open(path)?;
    let mut h = Sha256::new();
    let mut buf = vec![0u8; 1 << 20];
    let mut size = 0u64;
    loop {
        let n = f.read(&mut buf)?;
        if n == 0 {
            break;
        }
        size += n as u64;
        h.update(&buf[..n]);
    }
    Ok((format!("{:x}", h.finalize()), size))
}

/// A stable rendering of an SSOT value: table keys sorted, no whitespace.
fn canon(v: &Value) -> String {
    match v {
        Value::String(s) => Json::String(s.clone()).to_string(),
        Value::Integer(i) => i.to_string(),
        Value::Float(f) => format!("{f:?}"),
        Value::Boolean(b) => b.to_string(),
        Value::Datetime(d) => Json::String(d.to_string()).to_string(),
        Value::Array(a) => format!("[{}]", a.iter().map(canon).collect::<Vec<_>>().join(",")),
        Value::Table(t) => {
            let mut kv: Vec<_> = t.iter().collect();
            kv.sort_by(|a, b| a.0.cmp(b.0));
            let body: Vec<String> = kv
                .into_iter()
                .map(|(k, v)| format!("{}:{}", Json::String(k.clone()), canon(v)))
                .collect();
            format!("{{{}}}", body.join(","))
        }
    }
}

/// A repo-relative path with no way out of the root.
fn relative(p: &str) -> Option<PathBuf> {
    let path = Path::new(p);
    (!p.is_empty() && path.components().all(|c| matches!(c, Component::Normal(_))))
        .then(|| path.to_path_buf())
}

fn wildcard(pattern: &str, name: &str) -> bool {
    let parts: Vec<&str> = pattern.split('*').collect();
    if parts.len() == 1 {
        return pattern == name;
    }
    let (first, last) = (parts[0], parts[parts.len() - 1]);
    if name.len() < first.len() + last.len() || !name.starts_with(first) || !name.ends_with(last) {
        return false;
    }
    let mut rest = &name[first.len()..name.len() - last.len()];
    for mid in &parts[1..parts.len() - 1] {
        match rest.find(mid) {
            Some(i) => rest = &rest[i + mid.len()..],
            None => return false,
        }
    }
    true
}

/// `inputs` entries: a path, or a `*` pattern in the final segment only.
fn expand(root: &Path, entry: &str) -> Result<(Vec<String>, Option<String>), String> {
    let rel = relative(entry).ok_or(format!("input {entry:?} is not a relative path"))?;
    let name = rel.file_name().and_then(|n| n.to_str()).unwrap_or_default();
    let parent = rel.parent().unwrap_or(Path::new(""));
    if parent.to_string_lossy().contains('*') {
        return Err(format!(
            "input {entry:?}: only the final segment may hold *"
        ));
    }
    if !name.contains('*') {
        return Ok((vec![entry.to_string()], None));
    }
    let mut hits = Vec::new();
    if let Ok(rd) = fs::read_dir(root.join(parent)) {
        for e in rd.flatten() {
            let n = e.file_name().to_string_lossy().into_owned();
            if wildcard(name, &n) && e.path().is_file() {
                hits.push(parent.join(&n).to_string_lossy().replace('\\', "/"));
            }
        }
    }
    hits.sort();
    let line = format!("glob {entry} {}", hits.len());
    Ok((hits, Some(line)))
}

/// The installed NEVRA(s) providing `name`, or `absent`.
pub fn rpm_nevra(name: &str) -> String {
    match Command::new("rpm")
        .args(["-q", "--whatprovides", "--qf", "%{NEVRA}\\n", name])
        .output()
    {
        Ok(o) if o.status.success() => {
            let mut v: Vec<String> = String::from_utf8_lossy(&o.stdout)
                .lines()
                .map(str::trim)
                .filter(|l| !l.is_empty())
                .map(String::from)
                .collect();
            v.sort();
            v.dedup();
            v.join(",")
        }
        Ok(_) => "absent".into(),
        Err(_) => "rpm-unavailable".into(),
    }
}

/// Everything a unit's output depends on, one `kind name value` line each,
/// in a fixed order. The key is the sha256 of these lines.
pub fn material(
    cfg: &Config,
    name: &str,
    ssot: &Value,
    root: &Path,
    refs: &[(String, String)],
    rpm: &dyn Fn(&str) -> String,
) -> Result<Vec<String>, String> {
    let unit = cfg
        .units
        .get(name)
        .ok_or(format!("no [build.bake_cache.units.{name}]"))?;
    let mut out = vec![
        format!("schema {} {}", name, cfg.schema),
        format!("arch {} {}", name, std::env::consts::ARCH),
    ];
    let mut files = BTreeSet::new();
    let mut globs = Vec::new();
    for entry in std::iter::once(&unit.script).chain(&unit.inputs) {
        let (hits, glob) = expand(root, entry)?;
        files.extend(hits);
        globs.extend(glob);
    }
    for key in &unit.inputs_from {
        let listed = strings(lookup(ssot, key), key)?;
        if listed.is_empty() {
            globs.push(format!("glob {key} 0"));
        }
        for p in listed {
            relative(&p).ok_or(format!("{key} lists {p:?}, not a relative path"))?;
            files.insert(p);
        }
    }
    out.extend(globs);
    for f in &files {
        let (sum, _) = sha256_file(&root.join(f))
            .map_err(|e| format!("input {f} under {}: {e}", root.display()))?;
        out.push(format!("file {f} {sum}"));
    }
    for key in &unit.ssot {
        let v = lookup(ssot, key)
            .map(canon)
            .unwrap_or_else(|| "null".into());
        out.push(format!("ssot {key} {v}"));
    }
    let mut packages: BTreeSet<String> = cfg.toolchain.iter().chain(&unit.rpms).cloned().collect();
    for key in &unit.rpms_from {
        packages.extend(strings(lookup(ssot, key), key)?);
    }
    for p in &packages {
        out.push(format!("rpm {p} {}", rpm(p)));
    }
    let mut refs: Vec<_> = refs.to_vec();
    refs.sort();
    for (k, v) in refs {
        out.push(format!("ref {k} {v}"));
    }
    Ok(out)
}

pub fn key_of(material: &[String]) -> String {
    let mut body = material.join("\n");
    body.push('\n');
    sha256_hex(body.as_bytes())
}

fn unit_dir(dir: &Path, unit: &str) -> PathBuf {
    dir.join(unit)
}

fn pending_path(dir: &Path, unit: &str, key: &str) -> PathBuf {
    unit_dir(dir, unit).join(format!(".pending-{key}.json"))
}

/// Remember the inputs behind `key` so `save` can store them and a miss can
/// name what changed. Only when a cache is mounted.
pub fn record_pending(dir: &Path, unit: &str, key: &str, material: &[String]) -> io::Result<()> {
    if !dir.is_dir() {
        return Ok(());
    }
    fs::create_dir_all(unit_dir(dir, unit))?;
    let body = serde_json::to_vec_pretty(&json!({ "key": key, "material": material }))?;
    fs::write(pending_path(dir, unit, key), body)
}

fn read_pending(dir: &Path, unit: &str, key: &str) -> Option<Vec<String>> {
    let v: Json = serde_json::from_slice(&fs::read(pending_path(dir, unit, key)).ok()?).ok()?;
    (v.get("key")?.as_str()? == key).then_some(())?;
    v.get("material")?
        .as_array()?
        .iter()
        .map(|l| l.as_str().map(String::from))
        .collect()
}

#[derive(Debug, Clone, PartialEq, Eq)]
enum Kind {
    Dir,
    File { sha256: String, size: u64 },
    Symlink { target: String },
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct Entry {
    path: PathBuf,
    mode: u32,
    kind: Kind,
}

#[cfg(unix)]
fn mode_of(m: &fs::Metadata) -> u32 {
    use std::os::unix::fs::PermissionsExt;
    m.permissions().mode() & 0o7777
}

#[cfg(not(unix))]
fn mode_of(m: &fs::Metadata) -> u32 {
    if m.is_dir() {
        0o755
    } else {
        0o644
    }
}

#[cfg(unix)]
fn set_mode(p: &Path, mode: u32) -> io::Result<()> {
    use std::os::unix::fs::PermissionsExt;
    fs::set_permissions(p, fs::Permissions::from_mode(mode))
}

#[cfg(not(unix))]
fn set_mode(_p: &Path, _mode: u32) -> io::Result<()> {
    Err(io::Error::new(
        io::ErrorKind::Unsupported,
        "the bake cache needs a unix host",
    ))
}

#[cfg(unix)]
fn make_symlink(target: &str, link: &Path) -> io::Result<()> {
    std::os::unix::fs::symlink(target, link)
}

#[cfg(not(unix))]
fn make_symlink(_target: &str, _link: &Path) -> io::Result<()> {
    Err(io::Error::new(
        io::ErrorKind::Unsupported,
        "the bake cache needs a unix host",
    ))
}

fn entry_json(e: &Entry) -> Json {
    let path = e.path.to_string_lossy().replace('\\', "/");
    match &e.kind {
        Kind::Dir => json!({ "path": path, "type": "dir", "mode": e.mode }),
        Kind::File { sha256, size } => {
            json!({ "path": path, "type": "file", "mode": e.mode, "sha256": sha256, "size": size })
        }
        Kind::Symlink { target } => json!({ "path": path, "type": "symlink", "target": target }),
    }
}

fn entry_from(v: &Json) -> Option<Entry> {
    let path = relative(v.get("path")?.as_str()?)?;
    let mode = v.get("mode").and_then(Json::as_u64).unwrap_or(0) as u32 & 0o7777;
    let kind = match v.get("type")?.as_str()? {
        "dir" => Kind::Dir,
        "file" => {
            let sha256 = v.get("sha256")?.as_str()?.to_string();
            valid_key(&sha256).then_some(())?;
            Kind::File {
                sha256,
                size: v.get("size")?.as_u64()?,
            }
        }
        "symlink" => Kind::Symlink {
            target: v.get("target")?.as_str()?.to_string(),
        },
        _ => return None,
    };
    Some(Entry { path, mode, kind })
}

/// Total bytes of the files in `entries`, for the log.
fn bytes(entries: &[Entry]) -> u64 {
    entries
        .iter()
        .map(|e| match e.kind {
            Kind::File { size, .. } => size,
            _ => 0,
        })
        .sum()
}

fn mib(b: u64) -> String {
    format!("{:.1} MiB", b as f64 / (1024.0 * 1024.0))
}

/// Drop every other entry of `unit` and any half-written ones: the cache keeps
/// the newest output per unit, so it never grows with superseded pins.
fn prune(dir: &Path, unit: &str, keep: &str) {
    let Ok(rd) = fs::read_dir(unit_dir(dir, unit)) else {
        return;
    };
    for e in rd.flatten() {
        let n = e.file_name().to_string_lossy().into_owned();
        if n == keep || n == format!(".pending-{keep}.json") {
            continue;
        }
        let p = e.path();
        let _ = if p.is_dir() {
            fs::remove_dir_all(&p)
        } else {
            fs::remove_file(&p)
        };
    }
}

/// Store the tree at `src` as `unit`/`key`. The inputs must have been recorded
/// by `key` in this cache; the entry appears only once it is complete.
pub fn save(
    dir: &Path,
    unit: &str,
    key: &str,
    src: &Path,
    meta: &BTreeMap<String, String>,
) -> Result<String, String> {
    if !dir.is_dir() {
        return Ok(format!("no cache is mounted at {}", dir.display()));
    }
    let material = read_pending(dir, unit, key).ok_or(format!(
        "no inputs were recorded for {key} (cache key runs first)"
    ))?;
    if key_of(&material) != key {
        return Err(format!("the recorded inputs do not hash to {key}"));
    }
    if !src.is_dir() {
        return Err(format!("{} is not a directory", src.display()));
    }
    let tmp = unit_dir(dir, unit).join(format!(".tmp-{key}-{}", std::process::id()));
    let _ = fs::remove_dir_all(&tmp);
    let result = (|| -> Result<Vec<Entry>, String> {
        let files = tmp.join(FILES);
        fs::create_dir_all(&files).map_err(|e| e.to_string())?;
        let mut entries = Vec::new();
        let walk = walkdir::WalkDir::new(src)
            .min_depth(1)
            .follow_links(false)
            .sort_by_file_name();
        for item in walk {
            let item = item.map_err(|e| e.to_string())?;
            let rel = item.path().strip_prefix(src).map_err(|e| e.to_string())?;
            let md = fs::symlink_metadata(item.path()).map_err(|e| e.to_string())?;
            let ft = md.file_type();
            let kind = if ft.is_symlink() {
                let target = fs::read_link(item.path()).map_err(|e| e.to_string())?;
                Kind::Symlink {
                    target: target.to_string_lossy().into_owned(),
                }
            } else if ft.is_dir() {
                fs::create_dir_all(files.join(rel)).map_err(|e| e.to_string())?;
                Kind::Dir
            } else if ft.is_file() {
                let stored = files.join(rel);
                fs::copy(item.path(), &stored).map_err(|e| format!("{}: {e}", rel.display()))?;
                let _ = set_mode(&stored, 0o644);
                let (sha256, size) = sha256_file(&stored).map_err(|e| e.to_string())?;
                Kind::File { sha256, size }
            } else {
                return Err(format!(
                    "{} is not a file, directory or symlink",
                    rel.display()
                ));
            };
            entries.push(Entry {
                path: rel.to_path_buf(),
                mode: mode_of(&md),
                kind,
            });
        }
        let manifest = json!({
            "unit": unit,
            "key": key,
            "material": material,
            "meta": meta,
            "entries": entries.iter().map(entry_json).collect::<Vec<_>>(),
        });
        let body = serde_json::to_vec_pretty(&manifest).map_err(|e| e.to_string())?;
        fs::write(tmp.join(MANIFEST), body).map_err(|e| e.to_string())?;
        let dest = unit_dir(dir, unit).join(key);
        let _ = fs::remove_dir_all(&dest);
        fs::rename(&tmp, &dest).map_err(|e| e.to_string())?;
        Ok(entries)
    })();
    match result {
        Ok(entries) => {
            prune(dir, unit, key);
            let _ = fs::remove_file(pending_path(dir, unit, key));
            let n = entries
                .iter()
                .filter(|e| matches!(e.kind, Kind::File { .. }))
                .count();
            Ok(format!("saved {key}: {n} files, {}", mib(bytes(&entries))))
        }
        Err(e) => {
            let _ = fs::remove_dir_all(&tmp);
            Err(e)
        }
    }
}

struct Loaded {
    entries: Vec<Entry>,
    meta: BTreeMap<String, String>,
}

/// Read an entry and prove it: its inputs hash to `key` and every stored file
/// has the size and sha256 its manifest records.
fn load_verified(entry: &Path, unit: &str, key: &str) -> Result<Loaded, String> {
    let raw = fs::read(entry.join(MANIFEST)).map_err(|e| format!("manifest unreadable: {e}"))?;
    let m: Json = serde_json::from_slice(&raw).map_err(|e| format!("manifest is not JSON: {e}"))?;
    if m.get("unit").and_then(Json::as_str) != Some(unit)
        || m.get("key").and_then(Json::as_str) != Some(key)
    {
        return Err("manifest names another unit or key".into());
    }
    let material: Vec<String> = m
        .get("material")
        .and_then(Json::as_array)
        .and_then(|a| a.iter().map(|l| l.as_str().map(String::from)).collect())
        .ok_or("manifest has no inputs")?;
    if key_of(&material) != key {
        return Err("its recorded inputs do not hash to its key".into());
    }
    let entries: Vec<Entry> = m
        .get("entries")
        .and_then(Json::as_array)
        .ok_or("manifest lists no paths")?
        .iter()
        .map(|v| entry_from(v).ok_or(format!("malformed path record {v}")))
        .collect::<Result<_, _>>()?;
    for e in &entries {
        if let Kind::File { sha256, size } = &e.kind {
            let (got, got_size) = sha256_file(&entry.join(FILES).join(&e.path))
                .map_err(|err| format!("{}: {err}", e.path.display()))?;
            if &got != sha256 || got_size != *size {
                return Err(format!("{} does not match its sha256", e.path.display()));
            }
        }
    }
    let meta = m
        .get("meta")
        .and_then(Json::as_object)
        .map(|o| {
            o.iter()
                .filter_map(|(k, v)| v.as_str().map(|s| (k.clone(), s.to_string())))
                .collect()
        })
        .unwrap_or_default();
    Ok(Loaded { entries, meta })
}

/// Lay the stored tree over `dest`. A directory that already exists keeps its
/// own mode and owner (restoring into / must never touch /usr); files and
/// symlinks are replaced.
fn materialize(files: &Path, entries: &[Entry], dest: &Path) -> io::Result<()> {
    fs::create_dir_all(dest)?;
    for e in entries {
        let target = dest.join(&e.path);
        match &e.kind {
            Kind::Dir => match fs::metadata(&target) {
                Ok(m) if m.is_dir() => {}
                Ok(_) => {
                    return Err(io::Error::other(format!(
                        "{} exists and is not a directory",
                        target.display()
                    )))
                }
                Err(err) if err.kind() == io::ErrorKind::NotFound => {
                    fs::create_dir(&target)?;
                    set_mode(&target, e.mode)?;
                }
                Err(err) => return Err(err),
            },
            Kind::File { .. } => {
                let name = target.file_name().unwrap_or_default().to_string_lossy();
                let staged = target.with_file_name(format!(".{name}.mios-bake-cache"));
                fs::copy(files.join(&e.path), &staged)?;
                set_mode(&staged, e.mode)?;
                fs::rename(&staged, &target)?;
            }
            Kind::Symlink { target: to } => {
                if let Ok(m) = fs::symlink_metadata(&target) {
                    if m.is_dir() {
                        return Err(io::Error::other(format!(
                            "{} is a directory, not a symlink",
                            target.display()
                        )));
                    }
                    fs::remove_file(&target)?;
                }
                make_symlink(to, &target)?;
            }
        }
    }
    Ok(())
}

fn split3(line: &str) -> (String, String) {
    let mut it = line.splitn(3, ' ');
    let kind = it.next().unwrap_or_default();
    let name = it.next().unwrap_or_default();
    (
        format!("{kind} {name}"),
        it.next().unwrap_or_default().to_string(),
    )
}

/// Name what separates `want` from the closest stored entry of `unit`.
pub fn explain_miss(dir: &Path, unit: &str, key: &str) -> String {
    let mut stored: Vec<(String, Vec<String>)> = Vec::new();
    for e in fs::read_dir(unit_dir(dir, unit))
        .into_iter()
        .flatten()
        .flatten()
    {
        let n = e.file_name().to_string_lossy().into_owned();
        if !valid_key(&n) || n == key {
            continue;
        }
        let material = fs::read(e.path().join(MANIFEST))
            .ok()
            .and_then(|raw| serde_json::from_slice::<Json>(&raw).ok())
            .and_then(|m| {
                m.get("material")?
                    .as_array()?
                    .iter()
                    .map(|l| l.as_str().map(String::from))
                    .collect::<Option<Vec<_>>>()
            });
        if let Some(material) = material {
            stored.push((n, material));
        }
    }
    if stored.is_empty() {
        return format!("no {unit} output is stored yet");
    }
    let Some(want) = read_pending(dir, unit, key) else {
        return format!(
            "stored {unit} output {} has another key",
            &stored[0].0[..12]
        );
    };
    let index =
        |m: &[String]| -> BTreeMap<String, String> { m.iter().map(|l| split3(l)).collect() };
    let now = index(&want);
    let mut best: Option<(usize, String, Vec<String>)> = None;
    for (k, material) in &stored {
        let then = index(material);
        let mut diff = Vec::new();
        for (name, v) in &now {
            match then.get(name) {
                None => diff.push(format!("{name} (new)")),
                Some(old) if old != v => diff.push(format!("{name} ({old} -> {v})")),
                _ => {}
            }
        }
        diff.extend(
            then.keys()
                .filter(|n| !now.contains_key(*n))
                .map(|n| format!("{n} (gone)")),
        );
        if best.as_ref().is_none_or(|b| diff.len() < b.0) {
            best = Some((diff.len(), k.clone(), diff));
        }
    }
    let (_, k, mut diff) = best.unwrap_or_default();
    let more = diff.len().saturating_sub(8);
    diff.truncate(8);
    let tail = if more > 0 {
        format!(" and {more} more")
    } else {
        String::new()
    };
    format!(
        "stored output {} differs in {}{tail}",
        &k[..12.min(k.len())],
        diff.join("; ")
    )
}

pub enum Restored {
    Hit {
        files: usize,
        bytes: u64,
        meta: BTreeMap<String, String>,
    },
    Miss(String),
}

/// Install `unit`/`key` into `dest`, or say why not. A damaged entry is deleted.
pub fn restore(dir: &Path, unit: &str, key: &str, dest: &Path) -> Restored {
    if !dir.is_dir() {
        return Restored::Miss(format!("no cache is mounted at {}", dir.display()));
    }
    let entry = unit_dir(dir, unit).join(key);
    if !entry.join(MANIFEST).is_file() {
        return Restored::Miss(explain_miss(dir, unit, key));
    }
    let loaded = match load_verified(&entry, unit, key) {
        Ok(l) => l,
        Err(e) => {
            let _ = fs::remove_dir_all(&entry);
            return Restored::Miss(format!("stored entry refused and removed: {e}"));
        }
    };
    if let Err(e) = materialize(&entry.join(FILES), &loaded.entries, dest) {
        return Restored::Miss(format!("restore into {} failed: {e}", dest.display()));
    }
    prune(dir, unit, key);
    Restored::Hit {
        files: loaded
            .entries
            .iter()
            .filter(|e| matches!(e.kind, Kind::File { .. }))
            .count(),
        bytes: bytes(&loaded.entries),
        meta: loaded.meta,
    }
}

const USAGE: &str = "usage: mios-bake-plan cache key <unit> [--ref NAME=VALUE]...\n\
       mios-bake-plan cache restore <unit> <key> <dest>\n\
       mios-bake-plan cache save <unit> <key> <src> [--meta NAME=VALUE]...\n\
       (each takes --root <source tree> and --dir <cache dir>, which overrides [build.bake_cache].dir)";

struct Args {
    verb: String,
    positional: Vec<String>,
    root: Option<PathBuf>,
    dir: Option<PathBuf>,
    refs: Vec<(String, String)>,
    meta: BTreeMap<String, String>,
}

fn pair(s: &str) -> Option<(String, String)> {
    let (k, v) = s.split_once('=')?;
    (!k.is_empty() && !k.contains(char::is_whitespace) && !v.contains('\n'))
        .then(|| (k.to_string(), v.to_string()))
}

fn parse(args: &[String]) -> Result<Args, String> {
    let mut it = args.iter();
    let verb = it.next().ok_or("missing verb")?.clone();
    let mut a = Args {
        verb,
        positional: Vec::new(),
        root: None,
        dir: None,
        refs: Vec::new(),
        meta: BTreeMap::new(),
    };
    while let Some(arg) = it.next() {
        let mut value = |flag: &str| it.next().cloned().ok_or(format!("{flag} needs a value"));
        match arg.as_str() {
            "--root" => a.root = Some(PathBuf::from(value("--root")?)),
            "--dir" => a.dir = Some(PathBuf::from(value("--dir")?)),
            "--ref" => a
                .refs
                .push(pair(&value("--ref")?).ok_or("--ref takes NAME=VALUE")?),
            "--meta" => {
                let (k, v) = pair(&value("--meta")?).ok_or("--meta takes NAME=VALUE")?;
                a.meta.insert(k, v);
            }
            s if s.starts_with("--") => return Err(format!("unknown option {s}")),
            s => a.positional.push(s.to_string()),
        }
    }
    let want = match a.verb.as_str() {
        "key" => 1,
        "restore" | "save" => 3,
        v => return Err(format!("unknown verb {v}")),
    };
    if a.positional.len() != want {
        return Err(format!("{} takes {want} argument(s)", a.verb));
    }
    if !valid_unit_name(&a.positional[0]) {
        return Err(format!("unit {:?} is not [a-z0-9-]", a.positional[0]));
    }
    if want == 3 && !valid_key(&a.positional[1]) {
        return Err(format!("key {:?} is not a sha256", a.positional[1]));
    }
    Ok(a)
}

fn resolve(root: &Path) -> Result<(Value, Config), String> {
    let ssot = mios_resolver::resolve_merged(Some(root), false).map_err(|e| e.to_string())?;
    let cfg = Config::load(&ssot)?;
    Ok((ssot, cfg))
}

fn run(args: &[String], default_root: &Path) -> i32 {
    let a = match parse(args) {
        Ok(a) => a,
        Err(e) => {
            eprintln!("mios-bake-plan cache: {e}\n{USAGE}");
            return 2;
        }
    };
    let root = a.root.clone().unwrap_or_else(|| default_root.to_path_buf());
    let unit = a.positional[0].as_str();
    let resolved = resolve(&root);
    let dir = match (&a.dir, &resolved) {
        (Some(d), _) => Some(d.clone()),
        (None, Ok((_, cfg))) => Some(cfg.dir.clone()),
        (None, Err(_)) => None,
    };
    match a.verb.as_str() {
        "key" => {
            let (ssot, cfg) = match resolved {
                Ok(r) => r,
                Err(e) => {
                    eprintln!("{LOG} {unit}: no key, the bake builds from source: {e}");
                    return 1;
                }
            };
            let material = match material(&cfg, unit, &ssot, &root, &a.refs, &rpm_nevra) {
                Ok(m) => m,
                Err(e) => {
                    eprintln!("{LOG} {unit}: no key, the bake builds from source: {e}");
                    return 1;
                }
            };
            let key = key_of(&material);
            for line in &material {
                eprintln!("{LOG} {unit} input: {line}");
            }
            eprintln!("{LOG} {unit} key {key}");
            if let Some(dir) = &dir {
                if let Err(e) = record_pending(dir, unit, &key, &material) {
                    eprintln!(
                        "{LOG} {unit}: inputs not recorded in {}: {e}",
                        dir.display()
                    );
                }
            }
            println!("{key}");
            0
        }
        "restore" => {
            let key = a.positional[1].as_str();
            let Some(dir) = dir else {
                eprintln!("{LOG} {unit} MISS {key}: [build.bake_cache] did not resolve; building from source");
                return 1;
            };
            match restore(&dir, unit, key, Path::new(&a.positional[2])) {
                Restored::Hit { files, bytes, meta } => {
                    for (k, v) in &meta {
                        println!("{k}={v}");
                    }
                    eprintln!(
                        "{LOG} {unit} HIT {key}: restored {files} files ({}) into {}",
                        mib(bytes),
                        a.positional[2]
                    );
                    0
                }
                Restored::Miss(why) => {
                    eprintln!("{LOG} {unit} MISS {key}: {why}; building from source");
                    1
                }
            }
        }
        _ => {
            let key = a.positional[1].as_str();
            let Some(dir) = dir else {
                eprintln!("{LOG} {unit}: not saved, [build.bake_cache] did not resolve");
                return 0;
            };
            match save(&dir, unit, key, Path::new(&a.positional[2]), &a.meta) {
                Ok(msg) => eprintln!("{LOG} {unit}: {msg}"),
                Err(e) => eprintln!("{LOG} {unit}: not saved: {e}"),
            }
            0
        }
    }
}

/// `mios-bake-plan cache ...`; None for any other subcommand.
pub fn dispatch(args: &[String], root: &Path) -> Option<i32> {
    (args.get(1).map(String::as_str) == Some("cache")).then(|| run(&args[2..], root))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};

    static SEQ: AtomicUsize = AtomicUsize::new(0);

    fn scratch(tag: &str) -> PathBuf {
        let p = std::env::temp_dir().join(format!(
            "mios-bake-cache-test-{tag}-{}-{}",
            std::process::id(),
            SEQ.fetch_add(1, Ordering::SeqCst)
        ));
        let _ = fs::remove_dir_all(&p);
        fs::create_dir_all(&p).unwrap();
        p
    }

    fn ssot() -> Value {
        r#"
[build.bake_cache]
dir = "/var/tmp/mios-bake-cache"
schema = 1
toolchain = ["gcc"]
[build.bake_cache.units.demo]
script = "automation/42-demo.sh"
inputs = ["src/demo/*.patch"]
inputs_from = ["demo.patches"]
ssot = ["demo.pin"]
rpms = ["qt6-qtbase"]
rpms_from = ["packages.demo-build.pkgs"]
[demo]
pin = "latest"
patches = ["src/demo/listed.diff"]
[packages.demo-build]
pkgs = ["mesa-libgbm-devel"]
"#
        .parse()
        .unwrap()
    }

    fn tree() -> PathBuf {
        let root = scratch("root");
        fs::create_dir_all(root.join("automation")).unwrap();
        fs::create_dir_all(root.join("src/demo")).unwrap();
        fs::write(root.join("automation/42-demo.sh"), "make\n").unwrap();
        fs::write(root.join("src/demo/a.patch"), "a\n").unwrap();
        fs::write(root.join("src/demo/b.patch"), "b\n").unwrap();
        fs::write(root.join("src/demo/listed.diff"), "l\n").unwrap();
        root
    }

    fn rpm(name: &str) -> String {
        format!("{name}-1.0-1.fc45.x86_64")
    }

    fn key(
        root: &Path,
        ssot: &Value,
        refs: &[(String, String)],
        rpm: &dyn Fn(&str) -> String,
    ) -> (String, Vec<String>) {
        let cfg = Config::load(ssot).unwrap();
        let m = material(&cfg, "demo", ssot, root, refs, rpm).unwrap();
        (key_of(&m), m)
    }

    fn commit(c: &str) -> Vec<(String, String)> {
        vec![("commit".to_string(), c.to_string())]
    }

    #[test]
    fn every_input_moves_the_key() {
        let root = tree();
        let base = ssot();
        let (k0, m0) = key(&root, &base, &commit("aaa"), &rpm);
        assert_eq!(
            k0,
            key(&root, &base, &commit("aaa"), &rpm).0,
            "the key is deterministic"
        );
        for want in [
            "file automation/42-demo.sh",
            "file src/demo/a.patch",
            "file src/demo/listed.diff",
            "glob src/demo/*.patch 2",
            "ssot demo.pin \"latest\"",
            "rpm gcc gcc-1.0",
            "rpm qt6-qtbase",
            "rpm mesa-libgbm-devel",
            "ref commit aaa",
        ] {
            assert!(
                m0.iter().any(|l| l.starts_with(want)),
                "{want} is an input: {m0:?}"
            );
        }
        // The upstream commit the script resolved its pin to.
        assert_ne!(k0, key(&root, &base, &commit("bbb"), &rpm).0);
        // The SSOT pin itself.
        let mut bumped = base.clone();
        bumped["demo"]["pin"] = Value::String("v2".into());
        assert_ne!(k0, key(&root, &bumped, &commit("aaa"), &rpm).0);
        // A compiler or library update.
        let newer = |n: &str| {
            if n == "gcc" {
                "gcc-2.0-1.fc45.x86_64".into()
            } else {
                rpm(n)
            }
        };
        assert_ne!(k0, key(&root, &base, &commit("aaa"), &newer).0);
        // The bake script, a glob input, a listed input, and a new glob match.
        for (path, body) in [
            ("automation/42-demo.sh", "make -j1\n"),
            ("src/demo/a.patch", "a2\n"),
            ("src/demo/listed.diff", "l2\n"),
            ("src/demo/c.patch", "c\n"),
        ] {
            let before = fs::read(root.join(path)).ok();
            fs::write(root.join(path), body).unwrap();
            assert_ne!(
                k0,
                key(&root, &base, &commit("aaa"), &rpm).0,
                "{path} changes the key"
            );
            match before {
                Some(b) => fs::write(root.join(path), b).unwrap(),
                None => fs::remove_file(root.join(path)).unwrap(),
            }
        }
        assert_eq!(
            k0,
            key(&root, &base, &commit("aaa"), &rpm).0,
            "restored tree, same key"
        );
    }

    #[test]
    fn a_missing_recipe_has_no_key() {
        let root = tree();
        fs::remove_file(root.join("automation/42-demo.sh")).unwrap();
        let s = ssot();
        let cfg = Config::load(&s).unwrap();
        assert!(material(&cfg, "demo", &s, &root, &[], &rpm).is_err());
        assert!(material(&cfg, "nope", &s, &root, &[], &rpm).is_err());
    }

    #[test]
    fn unsafe_paths_are_refused() {
        assert!(relative("../etc/shadow").is_none());
        assert!(relative("/etc/shadow").is_none());
        assert!(relative("usr/bin/x").is_some());
        let mut s = ssot();
        s["demo"]["patches"] = Value::Array(vec![Value::String("../x".into())]);
        let cfg = Config::load(&s).unwrap();
        assert!(material(&cfg, "demo", &s, &tree(), &[], &rpm).is_err());
        assert!(!valid_key("../../x"));
        assert!(!valid_unit_name("../x"));
    }

    fn output() -> PathBuf {
        let out = scratch("out");
        fs::create_dir_all(out.join("usr/bin")).unwrap();
        fs::create_dir_all(out.join("usr/lib64/qt6/qml/Quickshell")).unwrap();
        fs::write(out.join("usr/bin/quickshell"), b"\x7fELF binary").unwrap();
        fs::write(
            out.join("usr/lib64/qt6/qml/Quickshell/qmldir"),
            "module Quickshell\n",
        )
        .unwrap();
        #[cfg(unix)]
        {
            set_mode(&out.join("usr/bin/quickshell"), 0o755).unwrap();
            make_symlink("quickshell", &out.join("usr/bin/qs")).unwrap();
        }
        out
    }

    fn primed(cache: &Path) -> (String, Vec<String>) {
        let (k, m) = key(&tree(), &ssot(), &commit("aaa"), &rpm);
        record_pending(cache, "demo", &k, &m).unwrap();
        (k, m)
    }

    #[cfg(unix)]
    #[test]
    fn save_then_restore_is_the_same_tree() {
        let cache = scratch("cache");
        let (k, _) = primed(&cache);
        let mut meta = BTreeMap::new();
        meta.insert("version".to_string(), "B7-896-g1b485305d9".to_string());
        save(&cache, "demo", &k, &output(), &meta).unwrap();
        assert!(
            !pending_path(&cache, "demo", &k).exists(),
            "the pending inputs are folded into the entry"
        );

        // Restore over an existing root whose /usr is 0700: it must stay 0700.
        let dest = scratch("dest");
        fs::create_dir_all(dest.join("usr/bin")).unwrap();
        set_mode(&dest.join("usr"), 0o700).unwrap();
        fs::write(dest.join("usr/bin/quickshell"), "stale").unwrap();
        match restore(&cache, "demo", &k, &dest) {
            Restored::Hit { files, meta, .. } => {
                assert_eq!(files, 2);
                assert_eq!(
                    meta.get("version").map(String::as_str),
                    Some("B7-896-g1b485305d9")
                );
            }
            Restored::Miss(why) => panic!("expected a hit: {why}"),
        }
        assert_eq!(
            fs::read(dest.join("usr/bin/quickshell")).unwrap(),
            b"\x7fELF binary"
        );
        let m = |p: &str| mode_of(&fs::symlink_metadata(dest.join(p)).unwrap());
        assert_eq!(m("usr"), 0o700, "an existing directory keeps its mode");
        assert_eq!(m("usr/bin/quickshell"), 0o755);
        assert_eq!(m("usr/lib64/qt6/qml/Quickshell/qmldir"), 0o644);
        assert_eq!(
            fs::read_link(dest.join("usr/bin/qs")).unwrap(),
            Path::new("quickshell")
        );
        assert!(
            !dest.join("usr/bin/.quickshell.mios-bake-cache").exists(),
            "no staging file is left behind"
        );
    }

    #[cfg(unix)]
    #[test]
    fn a_damaged_entry_is_a_miss_and_is_removed() {
        let cache = scratch("cache");
        let (k, _) = primed(&cache);
        save(&cache, "demo", &k, &output(), &BTreeMap::new()).unwrap();
        fs::write(
            cache.join("demo").join(&k).join("files/usr/bin/quickshell"),
            b"tampered",
        )
        .unwrap();
        let dest = scratch("dest");
        match restore(&cache, "demo", &k, &dest) {
            Restored::Miss(why) => assert!(why.contains("usr/bin/quickshell"), "{why}"),
            Restored::Hit { .. } => panic!("a tampered file restored"),
        }
        assert!(!cache.join("demo").join(&k).exists());
        assert!(
            !dest.join("usr/bin/quickshell").exists(),
            "nothing is written before the proof"
        );
    }

    #[cfg(unix)]
    #[test]
    fn an_entry_renamed_under_another_key_is_refused() {
        let cache = scratch("cache");
        let (k, _) = primed(&cache);
        save(&cache, "demo", &k, &output(), &BTreeMap::new()).unwrap();
        let forged = "f".repeat(64);
        fs::rename(
            cache.join("demo").join(&k),
            cache.join("demo").join(&forged),
        )
        .unwrap();
        let p = cache.join("demo").join(&forged).join(MANIFEST);
        let body = fs::read_to_string(&p).unwrap().replace(&k, &forged);
        fs::write(&p, body).unwrap();
        assert!(matches!(
            restore(&cache, "demo", &forged, &scratch("dest")),
            Restored::Miss(_)
        ));
    }

    #[test]
    fn no_mounted_cache_is_a_miss_and_a_quiet_save() {
        let gone = scratch("gone").join("absent");
        let k = "a".repeat(64);
        match restore(&gone, "demo", &k, &scratch("dest")) {
            Restored::Miss(why) => assert!(why.contains("no cache is mounted"), "{why}"),
            Restored::Hit { .. } => panic!("hit without a cache"),
        }
        assert!(save(&gone, "demo", &k, &output(), &BTreeMap::new()).is_ok());
        assert!(!gone.exists(), "an unmounted cache is never created");
        record_pending(&gone, "demo", &k, &[]).unwrap();
        assert!(!gone.exists());
    }

    #[test]
    fn save_needs_the_inputs_key_recorded() {
        let cache = scratch("cache");
        let k = "b".repeat(64);
        assert!(save(&cache, "demo", &k, &output(), &BTreeMap::new()).is_err());
        assert!(
            fs::read_dir(cache.join("demo"))
                .map(|d| d.count())
                .unwrap_or(0)
                == 0
        );
    }

    #[cfg(unix)]
    #[test]
    fn a_miss_names_what_changed_and_a_new_output_replaces_the_old() {
        let cache = scratch("cache");
        let root = tree();
        let s = ssot();
        let (k1, m1) = key(&root, &s, &commit("aaa"), &rpm);
        record_pending(&cache, "demo", &k1, &m1).unwrap();
        save(&cache, "demo", &k1, &output(), &BTreeMap::new()).unwrap();

        fs::write(root.join("automation/42-demo.sh"), "make -j2\n").unwrap();
        let (k2, m2) = key(&root, &s, &commit("aaa"), &rpm);
        record_pending(&cache, "demo", &k2, &m2).unwrap();
        match restore(&cache, "demo", &k2, &scratch("dest")) {
            Restored::Miss(why) => {
                assert!(why.contains("file automation/42-demo.sh"), "{why}");
                assert!(!why.contains("ref commit"), "{why}");
            }
            Restored::Hit { .. } => panic!("an edited script hit"),
        }
        save(&cache, "demo", &k2, &output(), &BTreeMap::new()).unwrap();
        assert!(cache.join("demo").join(&k2).is_dir());
        assert!(
            !cache.join("demo").join(&k1).exists(),
            "the superseded output is pruned"
        );
    }

    #[test]
    fn cli_arguments_are_checked() {
        let v = |s: &[&str]| s.iter().map(|x| x.to_string()).collect::<Vec<_>>();
        assert!(parse(&v(&["key", "demo", "--ref", "commit=abc"])).is_ok());
        assert!(parse(&v(&["key", "demo", "--ref", "commit"])).is_err());
        assert!(parse(&v(&["restore", "demo", "short", "/"])).is_err());
        assert!(parse(&v(&["save", "demo", &"c".repeat(64)])).is_err());
        assert!(parse(&v(&["frob", "demo"])).is_err());
        assert_eq!(
            dispatch(&v(&["mios-bake-plan", "latest-git", "x"]), Path::new(".")),
            None
        );
        assert_eq!(
            dispatch(&v(&["mios-bake-plan", "cache", "key"]), Path::new(".")),
            Some(2)
        );
    }

    #[test]
    fn wildcard_matches_one_segment() {
        assert!(wildcard("*.patch", "a.patch"));
        assert!(wildcard("hypr*-x.patch", "hyprland-x.patch"));
        assert!(!wildcard("*.patch", "a.diff"));
        assert!(!wildcard("a*b*c", "acb"));
        assert!(expand(Path::new("."), "src/*/x.patch").is_err());
    }

    /// The units mios.toml ships key cleanly against this tree: a renamed
    /// script or input would silently turn every bake into a miss.
    #[test]
    fn the_shipped_units_resolve_against_this_tree() {
        let root = std::env::var_os("MIOS_TEST_ROOT")
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../.."));
        let body = fs::read_to_string(root.join("usr/share/mios/mios.toml")).unwrap();
        let shipped: Value = body.parse().unwrap();
        let cfg = Config::load(&shipped).unwrap();
        for want in ["hyprland", "quickshell", "lookingglass"] {
            assert!(
                cfg.units.contains_key(want),
                "[build.bake_cache.units.{want}] is declared"
            );
        }
        for name in cfg.units.keys() {
            let m = material(&cfg, name, &shipped, &root, &[], &rpm)
                .unwrap_or_else(|e| panic!("unit {name}: {e}"));
            assert!(
                m.iter().any(|l| l.starts_with("file automation/")),
                "{name}: {m:?}"
            );
            assert!(
                !m.iter()
                    .any(|l| l.starts_with("glob ") && l.ends_with(" 0")),
                "{name}: an input pattern matches nothing: {m:?}"
            );
            assert!(
                m.iter().any(|l| l.starts_with("rpm ")),
                "{name} names what it builds against"
            );
        }
    }
}
