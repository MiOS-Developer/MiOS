// AI-hint: miosd shaderglass release: resolve the newest ShaderGlass release of the SSOT channel, download and accept it only against GitHub's sha256 digest, extract the named files, build WineCap from the same tag's verified source with the MiOS patches (rootless podman, heavy lock, memory caps), and install the staged tree atomically with INSTALLED.json.
// AI-related: src/mios-rs/miosd/src/shaderglass.rs, src/mios-rs/miosd/src/shaderglass/zip.rs, usr/share/mios/shaderglass/winecap-build/Containerfile, usr/share/mios/shaderglass/patches/, usr/share/mios/mios.toml [desktop.shaderglass]
//! Two steps, so root never runs the network or the builder: `install` (as the desktop user) resolves,
//! downloads, verifies, extracts, builds WineCap and stages the complete tree with its INSTALLED.json;
//! it then installs the stage when it can write install_dir, else prints the root step,
//! `install --from-stage <dir>`, which re-verifies every staged file against that record before it
//! swaps the tree in (the previous one is kept as <install_dir>.prev).

use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

use regex::Regex;
use serde_json::{json, Value as Json};
use sha2::{Digest, Sha256};

use super::{zip, Channel, Config, WinecapSource};

#[derive(Clone, Debug, PartialEq)]
pub struct Asset {
    pub name: String,
    pub url: String,
    pub size: u64,
    pub sha256: String,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Release {
    pub tag: String,
    pub published_at: String,
    pub prerelease: bool,
    pub html_url: String,
    pub asset: Asset,
}

impl Release {
    pub fn to_json(&self) -> Json {
        json!({
            "tag": self.tag, "published_at": self.published_at, "prerelease": self.prerelease,
            "html_url": self.html_url,
            "asset": {"name": self.asset.name, "url": self.asset.url, "size": self.asset.size, "sha256": self.asset.sha256},
        })
    }
}

/// owner/repo of a github.com repository URL.
pub fn github_repo(url: &str) -> Result<String, String> {
    let rest = url
        .strip_prefix("https://github.com/")
        .ok_or_else(|| format!("{url}: not an https://github.com/ repository"))?;
    let rest = rest.trim_end_matches('/').trim_end_matches(".git");
    let parts: Vec<&str> = rest.split('/').collect();
    let ok = |s: &str| !s.is_empty() && s.bytes().all(|c| c.is_ascii_alphanumeric() || b"-_.".contains(&c));
    if parts.len() != 2 || !ok(parts[0]) || !ok(parts[1]) {
        return Err(format!("{url}: not a github.com owner/repo URL"));
    }
    Ok(rest.to_owned())
}

/// The newest release of `channel` (by published_at; drafts never count) and its one asset matching
/// `pattern`, which must carry GitHub's sha256 digest and download from this repository's release URL.
/// "Latest + fail loudly": a newest release without such an asset is an error, never a silent fallback
/// to an older one.
pub fn select(releases: &Json, channel: &Channel, pattern: &Regex, repo: &str) -> Result<Release, String> {
    let list = releases.as_array().ok_or("releases: expected a JSON array (GitHub /releases)")?;
    let newest = list
        .iter()
        .filter(|r| r.get("draft").and_then(Json::as_bool) != Some(true))
        .filter(|r| *channel == Channel::Newest || r.get("prerelease").and_then(Json::as_bool) == Some(false))
        .filter(|r| r.get("tag_name").and_then(Json::as_str).is_some_and(|t| !t.is_empty()))
        .max_by(|a, b| {
            let p = |r: &Json| r.get("published_at").and_then(Json::as_str).unwrap_or("").to_owned();
            p(a).cmp(&p(b))
        })
        .ok_or_else(|| format!("{repo}: no published release in channel {channel:?}"))?;
    let tag = newest["tag_name"].as_str().unwrap_or_default().to_owned();
    if !tag.bytes().all(|c| c.is_ascii_alphanumeric() || b"-_.+".contains(&c)) {
        return Err(format!("{repo}: release tag {tag:?} has unexpected characters"));
    }
    let matching: Vec<&Json> = newest
        .get("assets")
        .and_then(Json::as_array)
        .into_iter()
        .flatten()
        .filter(|a| a.get("name").and_then(Json::as_str).is_some_and(|n| pattern.is_match(n)))
        .collect();
    let [a] = matching.as_slice() else {
        return Err(format!(
            "{repo} {tag}: {} assets match {} (exactly one is required)",
            matching.len(),
            pattern.as_str()
        ));
    };
    let name = a["name"].as_str().unwrap_or_default().to_owned();
    let sha256 = a
        .get("digest")
        .and_then(Json::as_str)
        .and_then(|d| d.strip_prefix("sha256:"))
        .filter(|d| d.len() == 64 && d.bytes().all(|c| c.is_ascii_hexdigit()))
        .map(str::to_ascii_lowercase)
        .ok_or_else(|| format!("{repo} {tag}: GitHub publishes no sha256 digest for {name}; refusing to download it"))?;
    let url = a.get("browser_download_url").and_then(Json::as_str).unwrap_or_default().to_owned();
    let expected = format!("https://github.com/{repo}/releases/download/{tag}/");
    if !url.starts_with(&expected) || url.len() == expected.len() || url.contains("..") {
        return Err(format!("{repo} {tag}: asset URL {url:?} is not under {expected}"));
    }
    Ok(Release {
        tag,
        published_at: newest.get("published_at").and_then(Json::as_str).unwrap_or("").to_owned(),
        prerelease: newest.get("prerelease").and_then(Json::as_bool).unwrap_or(false),
        html_url: newest.get("html_url").and_then(Json::as_str).unwrap_or("").to_owned(),
        asset: Asset { name, url, size: a.get("size").and_then(Json::as_u64).unwrap_or(0), sha256 },
    })
}

pub fn sha256_hex(data: &[u8]) -> String {
    let mut h = Sha256::new();
    h.update(data);
    h.finalize().iter().map(|b| format!("{b:02x}")).collect()
}

fn curl(url: &str, out: &Path, api: bool) -> Result<(), String> {
    let mut c = Command::new("curl");
    c.args(["-fsSL", "--proto", "=https", "--tlsv1.2", "--retry", "3", "--connect-timeout", "20"]);
    if api {
        c.args(["-H", "Accept: application/vnd.github+json", "-H", "X-GitHub-Api-Version: 2022-11-28"]);
    }
    let st = c
        .arg("-o")
        .arg(out)
        .arg(url)
        .stdin(Stdio::null())
        .status()
        .map_err(|e| format!("curl: {e}"))?;
    if st.success() {
        Ok(())
    } else {
        Err(format!("download failed ({st}): {url}"))
    }
}

pub fn fetch_releases(cfg: &Config, work: &Path) -> Result<Json, String> {
    let repo = github_repo(&cfg.source_repository)?;
    let file = work.join("releases.json");
    curl(&format!("{}/repos/{repo}/releases?per_page=30", cfg.github_api.trim_end_matches('/')), &file, true)?;
    serde_json::from_slice(&std::fs::read(&file).map_err(|e| e.to_string())?).map_err(|e| format!("releases JSON: {e}"))
}

/// The installed tag's peeled commit as upstream advertises it.
fn advertised_commit(repo_url: &str, tag: &str) -> Result<String, String> {
    let out = Command::new("git")
        .args(["ls-remote", repo_url, &format!("refs/tags/{tag}^{{}}"), &format!("refs/tags/{tag}")])
        .stderr(Stdio::inherit())
        .output()
        .map_err(|e| format!("git ls-remote: {e}"))?;
    let text = String::from_utf8_lossy(&out.stdout);
    let peeled = text.lines().find(|l| l.ends_with(&format!("refs/tags/{tag}^{{}}")));
    let line = peeled.or_else(|| text.lines().next()).ok_or_else(|| format!("{repo_url}: tag {tag} not advertised"))?;
    let sha = line.split_whitespace().next().unwrap_or("");
    if sha.len() != 40 || !sha.bytes().all(|c| c.is_ascii_hexdigit()) {
        return Err(format!("{repo_url}: bad commit id for {tag}"));
    }
    Ok(sha.to_owned())
}

fn run(cmd: &mut Command, what: &str) -> Result<String, String> {
    let out = cmd.stderr(Stdio::inherit()).output().map_err(|e| format!("{what}: {e}"))?;
    if !out.status.success() {
        return Err(format!("{what} failed ({})", out.status));
    }
    Ok(String::from_utf8_lossy(&out.stdout).into_owned())
}

/// ELF64 x86-64 shared object exporting __wine_spec_nt_header and linking libpipewire: what ShaderGlass
/// loads with LoadLibrary("WineCap.dll") under Wine (load_so_dll).
pub fn check_winecap(data: &[u8]) -> Result<(), String> {
    if data.len() < 64 || &data[..4] != b"\x7fELF" || data[4] != 2 || data[5] != 1 {
        return Err("WineCap.dll is not a 64-bit little-endian ELF object".into());
    }
    if u16::from_le_bytes([data[16], data[17]]) != 3 || u16::from_le_bytes([data[18], data[19]]) != 62 {
        return Err("WineCap.dll is not an x86-64 shared object".into());
    }
    let has = |needle: &[u8]| data.windows(needle.len()).any(|w| w == needle);
    if !has(b"__wine_spec_nt_header") {
        return Err("WineCap.dll does not export __wine_spec_nt_header (not a Winelib library)".into());
    }
    if !has(b"libpipewire-0.3.so") {
        return Err("WineCap.dll does not link libpipewire-0.3".into());
    }
    Ok(())
}

/// The Fedora release this host is built on, for the builder's base image: PLATFORM_ID=platform:f<N>
/// (MiOS rebrands NAME/ID/VERSION_ID, so VERSION_ID is MiOS' own version), else VERSION_ID on Fedora itself.
pub fn fedora_release(os_release: &str) -> Result<String, String> {
    let field = |k: &str| {
        os_release
            .lines()
            .find_map(|l| l.strip_prefix(k).and_then(|v| v.strip_prefix('=')))
            .map(|v| v.trim().trim_matches('"').to_owned())
    };
    let digits = |v: &str| !v.is_empty() && v.bytes().all(|c| c.is_ascii_digit());
    if let Some(n) = field("PLATFORM_ID").and_then(|p| p.strip_prefix("platform:f").map(str::to_owned)).filter(|n| digits(n)) {
        return Ok(n);
    }
    if field("ID").as_deref() == Some("fedora") {
        if let Some(v) = field("VERSION_ID").filter(|v| digits(v)) {
            return Ok(v);
        }
    }
    Err("os-release names no Fedora release (PLATFORM_ID=platform:f<N>)".into())
}

fn host_fedora_release() -> Result<String, String> {
    let text = std::fs::read_to_string("/etc/os-release")
        .or_else(|_| std::fs::read_to_string("/usr/lib/os-release"))
        .map_err(|e| format!("os-release: {e}"))?;
    fedora_release(&text)
}

/// Clone the tag (commit checked against ls-remote), apply the MiOS patches (failing loudly), build
/// WineCap/ in the builder image. Returns (WineCap.dll bytes, commit, toolchain lines).
fn build_winecap(cfg: &Config, tag: &str, base: &Path, work: &Path) -> Result<(Vec<u8>, String, Vec<String>), String> {
    if unsafe { libc::geteuid() } == 0 {
        return Err("WineCap is built by rootless podman: run `miosd shaderglass install` as the desktop user (it stages the tree and prints the root step)".into());
    }
    let b = &cfg.winecap.builder;
    let want = advertised_commit(&cfg.source_repository, tag)?;
    let src = work.join("src");
    run(
        Command::new("git")
            .args(["-c", "advice.detachedHead=false", "clone", "--quiet", "--depth", "1", "--branch", tag])
            .arg(&cfg.source_repository)
            .arg(&src),
        "git clone",
    )?;
    let got = run(Command::new("git").arg("-C").arg(&src).args(["rev-parse", "HEAD"]), "git rev-parse")?;
    if got.trim() != want {
        return Err(format!("INTEGRITY: cloned {} but upstream advertises {want} for {tag}", got.trim()));
    }
    for p in &cfg.winecap.patches {
        let path = super::mios_root().join(p);
        let check = Command::new("git").arg("-C").arg(&src).args(["apply", "--check"]).arg(&path).status();
        if !check.is_ok_and(|s| s.success()) {
            return Err(format!("MiOS patch {p} no longer applies to ShaderGlass {tag} ({want}): rebase it"));
        }
        run(Command::new("git").arg("-C").arg(&src).arg("apply").arg(&path), "git apply")?;
    }
    let base_image = b.base_image.replace("{fedora_release}", &host_fedora_release()?);
    let store = base.join("storage");
    let runroot = super::runtime_root()?.join("winecap-build");
    std::fs::create_dir_all(&store).map_err(|e| format!("{}: {e}", store.display()))?;
    super::ensure_private_dir(&runroot)?;
    let lock = heavy_lock(std::env::var("MIOS_HEAVY_LOCK").ok().as_deref(), &b.heavy_lock, base);
    let scoped = |args: &[String]| -> Command {
        let mut c = Command::new("flock");
        c.arg(&lock)
            .args(["systemd-run", "--user", "--scope", "--quiet", "--collect"])
            .args(["-p", &format!("MemoryMax={}", b.scope_memory_max), "-p", "MemorySwapMax=0", "--"])
            .args(["env", "-u", "CONTAINER_HOST", "-u", "DOCKER_HOST", "podman", "--root"])
            .arg(&store)
            .arg("--runroot")
            .arg(&runroot)
            .args(args);
        c
    };
    let mut build: Vec<String> = vec!["build".into(), "--layers".into(), "--memory".into(), b.container_memory.clone()];
    build.extend(["--build-arg".into(), format!("MIOS_WINECAP_BUILD_BASE={base_image}")]);
    build.extend(["--build-arg".into(), format!("MIOS_WINECAP_BUILD_PACKAGES={}", b.packages.join(" "))]);
    build.extend(["-t".into(), b.image.clone(), b.containerfile_dir.display().to_string()]);
    run(&mut scoped(&build), "podman build (WineCap builder)")?;
    let wc = src.join("WineCap");
    let uid = unsafe { libc::getuid() };
    let gid = unsafe { libc::getgid() };
    let runargs: Vec<String> = vec![
        "run".into(), "--rm".into(), "--network=none".into(), "--memory".into(), b.container_memory.clone(),
        "--cpus".into(), b.container_cpus.to_string(), "--userns=keep-id".into(), "--user".into(), format!("{uid}:{gid}"),
        "-v".into(), format!("{}:/src:Z", wc.display()), b.image.clone(),
    ];
    let log = run(&mut scoped(&runargs), "WineCap make (builder)")?;
    let dll = std::fs::read(wc.join("WineCap.dll")).map_err(|e| format!("built WineCap.dll: {e}"))?;
    let toolchain = log
        .lines()
        .filter(|l| l.starts_with("winegcc: ") || l.starts_with("pipewire: "))
        .map(str::to_owned)
        .collect();
    Ok((dll, want, toolchain))
}

/// The flock file heavy builder steps serialize on: MIOS_HEAVY_LOCK, else the SSOT's heavy_lock, else (that
/// lock's directory does not exist on this host, so no other heavy job can hold it) <work_root>/heavy.lock.
pub fn heavy_lock(env: Option<&str>, ssot: &str, work_root: &Path) -> String {
    if let Some(e) = env.filter(|e| e.starts_with('/')) {
        return e.to_owned();
    }
    if Path::new(ssot).parent().is_some_and(Path::is_dir) {
        return ssot.to_owned();
    }
    let fallback = work_root.join("heavy.lock");
    eprintln!("[miosd shaderglass] {ssot}: no such directory on this host; serializing on {}", fallback.display());
    fallback.display().to_string()
}

pub struct Opts {
    pub channel: Option<Channel>,
    pub releases_json: Option<PathBuf>,
    pub archive: Option<PathBuf>,
    pub winecap: Option<PathBuf>,
    pub dest: Option<PathBuf>,
    pub stage_only: bool,
    pub dry_run: bool,
    pub force: bool,
}

/// The writable work root: work_root (MIOS_SHADERGLASS_WORK overrides it for one run), or
/// $XDG_CACHE_HOME/mios/shaderglass when this user cannot write there. It holds the per-run stages and
/// the builder's own image store.
fn work_base(cfg: &Config) -> PathBuf {
    let preferred = std::env::var_os("MIOS_SHADERGLASS_WORK")
        .map(PathBuf::from)
        .filter(|p| p.is_absolute())
        .unwrap_or_else(|| cfg.winecap.builder.work_root.clone());
    if std::fs::create_dir_all(&preferred).is_ok() && writable(&preferred) {
        preferred
    } else {
        super::cache_home().join("mios/shaderglass")
    }
}

/// A fresh run directory under `base`.
fn work_dir(base: &Path) -> Result<PathBuf, String> {
    let dir = base.join("stage").join(format!("run-{}-{}", super::picker::now_ms(), std::process::id()));
    std::fs::create_dir_all(&dir).map_err(|e| format!("{}: {e}", dir.display()))?;
    Ok(dir)
}

/// Whether this process can create files in `dir` (probe file created and removed).
fn writable(dir: &Path) -> bool {
    let probe = dir.join(format!(".miosd-shaderglass-probe-{}", std::process::id()));
    let ok = std::fs::OpenOptions::new().write(true).create_new(true).open(&probe).is_ok();
    if ok {
        let _ = std::fs::remove_file(&probe);
    }
    ok
}

fn write_file(path: &Path, data: &[u8], mode: u32) -> Result<(), String> {
    use std::io::Write;
    use std::os::unix::fs::PermissionsExt;
    let mut f = std::fs::File::create(path).map_err(|e| format!("{}: {e}", path.display()))?;
    f.write_all(data).and_then(|_| f.sync_all()).map_err(|e| format!("{}: {e}", path.display()))?;
    std::fs::set_permissions(path, std::fs::Permissions::from_mode(mode)).map_err(|e| format!("{}: {e}", path.display()))
}

/// Resolve, verify and stage (and install when possible). Returns a human summary.
pub fn install(cfg: &Config, o: &Opts) -> Result<String, String> {
    let channel = o.channel.clone().unwrap_or_else(|| cfg.channel.clone());
    let repo = github_repo(&cfg.source_repository)?;
    let dest = o.dest.clone().unwrap_or_else(|| cfg.install_dir.clone());
    let base = work_base(cfg);
    let work = work_dir(&base)?;
    let releases = match &o.releases_json {
        Some(p) => serde_json::from_slice(&std::fs::read(p).map_err(|e| format!("{}: {e}", p.display()))?)
            .map_err(|e| format!("{}: {e}", p.display()))?,
        None => fetch_releases(cfg, &work)?,
    };
    let rel = select(&releases, &channel, &cfg.asset_pattern, &repo)?;
    let source = match cfg.winecap.source {
        WinecapSource::Auto if cfg.winecap.patches.is_empty() => WinecapSource::Release,
        WinecapSource::Auto => WinecapSource::Build,
        ref s => s.clone(),
    };
    let patches: Vec<Json> = if source == WinecapSource::Build {
        cfg.winecap
            .patches
            .iter()
            .map(|p| {
                let data = std::fs::read(super::mios_root().join(p)).map_err(|e| format!("{p}: {e}"))?;
                Ok(json!({"path": p, "sha256": sha256_hex(&data)}))
            })
            .collect::<Result<_, String>>()?
    } else {
        Vec::new()
    };
    if !o.force {
        let installed = std::fs::read(dest.join("INSTALLED.json")).ok().and_then(|b| serde_json::from_slice::<Json>(&b).ok());
        if let Some(i) = installed {
            let same = i.pointer("/release/tag").and_then(Json::as_str) == Some(rel.tag.as_str())
                && i.pointer("/release/asset/sha256").and_then(Json::as_str) == Some(rel.asset.sha256.as_str())
                && i.pointer("/winecap/source").and_then(Json::as_str) == Some(source.as_str())
                && i.pointer("/winecap/patches") == Some(&Json::Array(patches.clone()));
            if same {
                return Ok(format!("ShaderGlass {} is already installed at {} (use --force to reinstall)", rel.tag, dest.display()));
            }
        }
    }
    eprintln!("[miosd shaderglass] resolved {repo} {} ({}, published {}, channel {channel:?}): {} sha256 {}", rel.tag,
        if rel.prerelease { "pre-release" } else { "release" }, rel.published_at, rel.asset.name, rel.asset.sha256);
    if o.dry_run {
        return Ok(serde_json::to_string_pretty(&json!({"release": rel.to_json(), "winecap": source.as_str(), "patches": patches, "dest": dest}))
            .unwrap_or_default());
    }
    let archive = match &o.archive {
        Some(p) => p.clone(),
        None => {
            let f = work.join(&rel.asset.name);
            curl(&rel.asset.url, &f, false)?;
            f
        }
    };
    let data = std::fs::read(&archive).map_err(|e| format!("{}: {e}", archive.display()))?;
    let got = sha256_hex(&data);
    if got != rel.asset.sha256 {
        if o.archive.is_none() {
            let _ = std::fs::remove_file(&archive);
        }
        return Err(format!("INTEGRITY: {} sha256 {got} != GitHub's {}", rel.asset.name, rel.asset.sha256));
    }
    let list = zip::entries(&data)?;
    let tree = work.join("tree");
    std::fs::create_dir_all(&tree).map_err(|e| e.to_string())?;
    let mut files = serde_json::Map::new();
    for name in &cfg.install_files {
        let required = name == &cfg.exe || name == "LICENSE";
        let Some(e) = zip::find(&list, name)? else {
            if required {
                return Err(format!("{} has no {name}", rel.asset.name));
            }
            continue;
        };
        let body = zip::read(&data, e)?;
        if name == &cfg.exe && !body.starts_with(b"MZ") {
            return Err(format!("{name} is not a Windows executable"));
        }
        if name == "LICENSE" {
            let text = String::from_utf8_lossy(&body);
            if let Some(m) = cfg.license_markers.iter().find(|m| !text.contains(m.as_str())) {
                return Err(format!("LICENSE no longer contains {m:?}; refusing to install (review the licence change)"));
            }
        }
        write_file(&tree.join(name), &body, 0o644)?;
        files.insert(name.clone(), json!(sha256_hex(&body)));
    }
    let (dll, commit, toolchain) = match (&source, &o.winecap) {
        (_, Some(p)) => (std::fs::read(p).map_err(|e| format!("{}: {e}", p.display()))?, Json::Null, vec!["--winecap supplied".to_owned()]),
        (WinecapSource::Release, None) => {
            let e = zip::find(&list, "WineCap.dll")?.ok_or_else(|| format!("{} ships no WineCap.dll (set winecap.source = \"build\")", rel.asset.name))?;
            (zip::read(&data, e)?, Json::Null, vec![format!("prebuilt in {}", rel.asset.name)])
        }
        _ => {
            let (d, c, t) = build_winecap(cfg, &rel.tag, &base, &work)?;
            (d, json!(c), t)
        }
    };
    check_winecap(&dll)?;
    write_file(&tree.join("WineCap.dll"), &dll, 0o644)?;
    files.insert("WineCap.dll".into(), json!(sha256_hex(&dll)));
    let record = json!({
        "resolved_by": "miosd shaderglass install",
        "installed_ms": super::picker::now_ms(),
        "repository": cfg.source_repository,
        "channel": channel.as_str(),
        "release": rel.to_json(),
        "source_commit": commit,
        "winecap": {"source": source.as_str(), "patches": patches, "toolchain": toolchain},
        "files": files,
    });
    write_file(&tree.join("INSTALLED.json"), &serde_json::to_vec_pretty(&record).map_err(|e| e.to_string())?, 0o644)?;
    if o.stage_only {
        return Ok(format!("ShaderGlass {} staged at {}", rel.tag, tree.display()));
    }
    let parent = dest.parent().ok_or("install_dir has no parent")?;
    if !writable(parent) {
        return Ok(format!(
            "ShaderGlass {} verified and staged at {}.\nInstall it (root, no network): sudo {} shaderglass install --from-stage {}",
            rel.tag,
            tree.display(),
            std::env::current_exe().map(|p| p.display().to_string()).unwrap_or_else(|_| "miosd".into()),
            tree.display()
        ));
    }
    install_stage(&tree, &dest)
}

/// Re-verify a staged tree against its INSTALLED.json and swap it into `dest` (previous tree kept as .prev).
pub fn install_stage(tree: &Path, dest: &Path) -> Result<String, String> {
    use std::os::unix::fs::PermissionsExt;
    let record: Json = serde_json::from_slice(&std::fs::read(tree.join("INSTALLED.json")).map_err(|e| format!("stage: {e}"))?)
        .map_err(|e| format!("stage INSTALLED.json: {e}"))?;
    let files = record.get("files").and_then(Json::as_object).ok_or("stage INSTALLED.json has no files")?;
    if !files.contains_key("WineCap.dll") {
        return Err("stage has no WineCap.dll".into());
    }
    let parent = dest.parent().ok_or("install_dir has no parent")?;
    let base = dest.file_name().and_then(|n| n.to_str()).ok_or("install_dir has no name")?;
    let new = parent.join(format!(".{base}.new-{}", std::process::id()));
    std::fs::create_dir_all(&new).map_err(|e| format!("{}: {e}", new.display()))?;
    std::fs::set_permissions(&new, std::fs::Permissions::from_mode(0o755)).map_err(|e| e.to_string())?;
    let mut names: Vec<&String> = files.keys().collect();
    names.sort();
    for name in names {
        if name.contains('/') || name.starts_with('.') {
            return Err(format!("stage lists an unsafe name {name:?}"));
        }
        let data = std::fs::read(tree.join(name)).map_err(|e| format!("stage {name}: {e}"))?;
        if Some(sha256_hex(&data).as_str()) != files[name].as_str() {
            let _ = std::fs::remove_dir_all(&new);
            return Err(format!("INTEGRITY: staged {name} does not match its INSTALLED.json digest"));
        }
        write_file(&new.join(name), &data, 0o644)?;
    }
    write_file(&new.join("INSTALLED.json"), &std::fs::read(tree.join("INSTALLED.json")).map_err(|e| e.to_string())?, 0o644)?;
    let prev = parent.join(format!("{base}.prev"));
    if dest.exists() {
        if prev.exists() {
            std::fs::remove_dir_all(&prev).map_err(|e| format!("{}: {e}", prev.display()))?;
        }
        std::fs::rename(dest, &prev).map_err(|e| format!("{} -> {}: {e}", dest.display(), prev.display()))?;
    }
    if let Err(e) = std::fs::rename(&new, dest) {
        if prev.exists() {
            let _ = std::fs::rename(&prev, dest);
        }
        return Err(format!("{} -> {}: {e}", new.display(), dest.display()));
    }
    Ok(format!(
        "installed ShaderGlass {} into {} (previous tree: {})",
        record.pointer("/release/tag").and_then(Json::as_str).unwrap_or("?"),
        dest.display(),
        if prev.exists() { prev.display().to_string() } else { "none".into() }
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    const REPO: &str = "mausimus/ShaderGlass";

    fn pattern() -> Regex {
        Regex::new(r"^ShaderGlass-[0-9][0-9A-Za-z.+-]*-win-x64\.zip$").unwrap()
    }

    fn rel(tag: &str, pre: bool, published: &str, asset: &str, digest: Option<&str>) -> Json {
        json!({
            "tag_name": tag, "prerelease": pre, "draft": false, "published_at": published,
            "html_url": format!("https://github.com/{REPO}/releases/tag/{tag}"),
            "assets": [
                {"name": asset, "size": 47047313, "digest": digest.map(|d| format!("sha256:{d}")),
                 "browser_download_url": format!("https://github.com/{REPO}/releases/download/{tag}/{asset}")},
                {"name": "ShaderGlass-src.tar.gz", "digest": "sha256:00", "browser_download_url": "x"},
            ]
        })
    }

    fn fixture() -> Json {
        let d = "1677879ad8c20405e1022a26679bda9af07ee11d99a45c0734760c4bd88c70d5";
        json!([
            rel("v1.3.0", false, "2026-03-19T03:14:06Z", "ShaderGlass-1.3.0-win-x64.zip", Some(&"d6".repeat(32))),
            rel("v1.3.1", true, "2026-10-18T07:37:59Z", "ShaderGlass-1.3.1-beta-win-x64.zip", Some(d)),
            {"tag_name": "v9.9.9", "draft": true, "prerelease": false, "published_at": "2027-01-01T00:00:00Z", "assets": []},
            rel("v1.2.3", false, "2025-12-07T06:23:05Z", "ShaderGlass-1.2.3.1-win-x64.zip", Some(&"92".repeat(32))),
        ])
    }

    #[test]
    fn newest_takes_prereleases_stable_does_not_drafts_never() {
        let n = select(&fixture(), &Channel::Newest, &pattern(), REPO).unwrap();
        assert_eq!((n.tag.as_str(), n.prerelease, n.asset.name.as_str()), ("v1.3.1", true, "ShaderGlass-1.3.1-beta-win-x64.zip"));
        assert_eq!(n.asset.sha256, "1677879ad8c20405e1022a26679bda9af07ee11d99a45c0734760c4bd88c70d5");
        let s = select(&fixture(), &Channel::Stable, &pattern(), REPO).unwrap();
        assert_eq!(s.tag, "v1.3.0", "stable skips the pre-release");
        assert!(s.asset.url.starts_with("https://github.com/mausimus/ShaderGlass/releases/download/v1.3.0/"));
    }

    #[test]
    fn newest_release_without_a_verifiable_asset_fails_loudly() {
        let mut f = fixture();
        f.as_array_mut().unwrap().push(rel("v1.4.0", true, "2026-11-01T00:00:00Z", "ShaderGlass-1.4.0-win-x64.zip", None));
        let e = select(&f, &Channel::Newest, &pattern(), REPO).unwrap_err();
        assert!(e.contains("v1.4.0") && e.contains("no sha256 digest"), "{e}");
        // ...and does not fall back to v1.3.1.
        let mut two = fixture();
        let mut r = rel("v1.4.0", false, "2026-11-01T00:00:00Z", "ShaderGlass-1.4.0-win-x64.zip", Some(&"ab".repeat(32)));
        r["assets"].as_array_mut().unwrap().push(json!({"name": "ShaderGlass-1.4.0-arm-win-x64.zip", "digest": "sha256:00",
            "browser_download_url": format!("https://github.com/{REPO}/releases/download/v1.4.0/b.zip")}));
        two.as_array_mut().unwrap().push(r);
        assert!(select(&two, &Channel::Stable, &pattern(), REPO).unwrap_err().contains("2 assets match"));
        let mut foreign = fixture();
        foreign[1]["assets"][0]["browser_download_url"] = json!("https://evil.example/ShaderGlass-1.3.1-beta-win-x64.zip");
        assert!(select(&foreign, &Channel::Newest, &pattern(), REPO).unwrap_err().contains("not under"));
        assert!(select(&json!({"message": "API rate limit exceeded"}), &Channel::Newest, &pattern(), REPO).is_err());
        assert!(select(&json!([]), &Channel::Newest, &pattern(), REPO).unwrap_err().contains("no published release"));
    }

    #[test]
    fn heavy_lock_prefers_the_override_then_the_ssot_then_the_work_root() {
        let d = tempfile::tempdir().unwrap();
        let ssot = d.path().join("heavy.lock").display().to_string();
        let work = d.path().join("work");
        assert_eq!(heavy_lock(Some("/run/x.lock"), &ssot, &work), "/run/x.lock");
        assert_eq!(heavy_lock(Some("relative.lock"), &ssot, &work), ssot, "a relative override is ignored");
        assert_eq!(heavy_lock(None, &ssot, &work), ssot);
        let missing = d.path().join("no/such/dir/heavy.lock").display().to_string();
        assert_eq!(heavy_lock(None, &missing, &work), work.join("heavy.lock").display().to_string());
    }

    #[test]
    fn the_builder_follows_the_hosts_fedora_release_not_the_mios_version() {
        let mios = "NAME=\"MiOS\"\nVERSION=\"0.3.0\"\nID=mios\nID_LIKE=\"fedora coreos\"\nVERSION_ID=\"0.3.0\"\nPLATFORM_ID=\"platform:f44\"\n";
        assert_eq!(fedora_release(mios).unwrap(), "44");
        assert_eq!(fedora_release("ID=fedora\nVERSION_ID=45\n").unwrap(), "45");
        assert_eq!(fedora_release("ID=fedora\nVERSION_ID=45\nPLATFORM_ID=\"platform:f45\"\n").unwrap(), "45");
        // Negative controls: a rebranded VERSION_ID alone, another distribution, a malformed platform.
        assert!(fedora_release("ID=mios\nVERSION_ID=\"0.3.0\"\n").is_err());
        assert!(fedora_release("ID=ubuntu\nVERSION_ID=\"22.04\"\n").is_err());
        assert!(fedora_release("PLATFORM_ID=\"platform:el9\"\nID=rhel\n").is_err());
        assert!(fedora_release("PLATFORM_ID=platform:f4x\n").is_err());
    }

    #[test]
    fn github_repo_urls() {
        assert_eq!(github_repo("https://github.com/mausimus/ShaderGlass").unwrap(), REPO);
        assert_eq!(github_repo("https://github.com/mausimus/ShaderGlass.git/").unwrap(), REPO);
        for bad in ["http://github.com/a/b", "https://gitlab.com/a/b", "https://github.com/a", "https://github.com/a/b/c", "https://github.com/a/b;x"] {
            assert!(github_repo(bad).is_err(), "{bad}");
        }
    }

    #[test]
    fn winecap_must_be_a_winelib_x86_64_object() {
        let mut elf = vec![0u8; 128];
        elf[..4].copy_from_slice(b"\x7fELF");
        elf[4] = 2;
        elf[5] = 1;
        elf[16] = 3;
        elf[18] = 62;
        let mut good = elf.clone();
        good.extend_from_slice(b"\0libpipewire-0.3.so.0\0__wine_spec_nt_header\0");
        assert!(check_winecap(&good).is_ok());
        assert!(check_winecap(&elf).unwrap_err().contains("__wine_spec_nt_header"));
        let mut pe = good.clone();
        pe[..2].copy_from_slice(b"MZ");
        assert!(check_winecap(&pe).is_err(), "a PE dll is not what WineCap.dll must be");
        let mut arm = good;
        arm[18] = 183;
        assert!(check_winecap(&arm).is_err());
    }

    #[test]
    fn stage_install_reverifies_and_keeps_the_previous_tree() {
        let d = tempfile::tempdir().unwrap();
        let tree = d.path().join("tree");
        std::fs::create_dir_all(&tree).unwrap();
        let files = [("ShaderGlass.exe", b"MZexe".as_slice()), ("WineCap.dll", b"elf".as_slice()), ("LICENSE", b"GPL".as_slice())];
        let mut map = serde_json::Map::new();
        for (n, b) in files {
            std::fs::write(tree.join(n), b).unwrap();
            map.insert(n.into(), json!(sha256_hex(b)));
        }
        std::fs::write(tree.join("INSTALLED.json"), json!({"release": {"tag": "v1"}, "files": map}).to_string()).unwrap();
        let dest = d.path().join("shaderglass");
        std::fs::create_dir_all(&dest).unwrap();
        std::fs::write(dest.join("old"), b"old").unwrap();
        let msg = install_stage(&tree, &dest).unwrap();
        assert!(msg.contains("v1"), "{msg}");
        assert_eq!(std::fs::read(dest.join("ShaderGlass.exe")).unwrap(), b"MZexe");
        assert!(d.path().join("shaderglass.prev/old").exists(), "previous tree kept");
        use std::os::unix::fs::PermissionsExt;
        assert_eq!(std::fs::metadata(dest.join("WineCap.dll")).unwrap().permissions().mode() & 0o777, 0o644);
        assert_eq!(std::fs::metadata(&dest).unwrap().permissions().mode() & 0o777, 0o755);
        // A staged file changed after staging: refused, the installed tree stays.
        std::fs::write(tree.join("WineCap.dll"), b"tampered").unwrap();
        assert!(install_stage(&tree, &dest).unwrap_err().contains("INTEGRITY"));
        assert_eq!(std::fs::read(dest.join("WineCap.dll")).unwrap(), b"elf");
    }
}
