// AI-hint: Renders a disk artifact's bootc-image-builder config from the layered SSOT, builds and converts it, and boot-tests it under QEMU; the credential is the operator's, never a recipe's.
// AI-related: usr/share/mios/mios.toml, config/artifacts/iso.toml, Justfile, src/mios-rs/miosd/src/main.rs, .github/workflows/mios-ci.yml
//
// One path for every bootc-image-builder format, driven by [deploy.formats.<f>]:
// the BIB type is the format's name unless it says `bib_type`, the type must be
// enabled in [deployment].target_<type>, and when the type is not the format
// (vhd for vhdx) the disk is converted to the format's name. Nothing here bakes
// a default: a disk that no operator credential opens is refused before podman
// runs (P1-4).

use serde::Serialize;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

/// The token every placeholder credential in a recipe carried. A recipe that
/// still holds one would bake it into the disk.
pub const PLACEHOLDER: &str = "REPLACE";

/// Kickstart commands that set a credential. A committed recipe never carries
/// them; the renderer appends them from the operator's credential.
pub const KICKSTART_CREDENTIALS: [&str; 3] = ["user", "sshkey", "rootpw"];

fn at<'a>(config: &'a toml::Value, key: &str) -> Option<&'a toml::Value> {
    key.split('.').try_fold(config, |node, part| node.get(part))
}

fn text<'a>(config: &'a toml::Value, key: &str) -> Result<&'a str, String> {
    at(config, key)
        .and_then(toml::Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .ok_or_else(|| format!("SSOT {key} must be a nonempty string"))
}

fn optional<'a>(config: &'a toml::Value, key: &str) -> Option<&'a str> {
    at(config, key)
        .and_then(toml::Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
}

/// A declared [deploy.formats.<name>] that bootc-image-builder produces.
#[derive(Debug)]
pub struct DiskFormat<'a> {
    pub name: String,
    pub spec: &'a toml::Value,
    pub bib_type: String,
}

pub fn disk_format<'a>(config: &'a toml::Value, name: &str) -> Result<DiskFormat<'a>, String> {
    let spec = at(config, "deploy.formats")
        .and_then(|formats| formats.get(name))
        .filter(|spec| spec.is_table())
        .ok_or_else(|| format!("[deploy.formats.{name}] is not a declared format"))?;
    let bib_type = spec
        .get("bib_type")
        .and_then(toml::Value::as_str)
        .unwrap_or(name)
        .to_string();
    let gate = format!("target_{}", bib_type.replace('-', "_"));
    match at(config, "deployment")
        .and_then(|d| d.get(&gate))
        .and_then(toml::Value::as_bool)
    {
        Some(true) => Ok(DiskFormat {
            name: name.into(),
            spec,
            bib_type,
        }),
        Some(false) => Err(format!(
            "[deployment].{gate} = false: the operator has disabled {bib_type} disks"
        )),
        None => Err(format!(
            "[deploy.formats.{name}] is not a bootc-image-builder disk: [deployment] declares no {gate}"
        )),
    }
}

/// The operator's credential for the disk's account. Either half may be absent,
/// never both.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Credential {
    pub password_hash: Option<String>,
    pub ssh_key: Option<String>,
}

impl Credential {
    /// The same shape with the values withheld, for `--plan` output.
    pub fn redacted(&self) -> Self {
        Credential {
            password_hash: self.password_hash.as_ref().map(|_| "<redacted>".into()),
            ssh_key: self.ssh_key.as_ref().map(|_| "<redacted>".into()),
        }
    }
}

/// A crypt(3) hash: `$id$[params$]salt$digest`, one token, no placeholder.
pub fn crypt_hash(value: &str) -> Result<String, String> {
    let v = value.trim();
    if !v.starts_with('$')
        || v.split('$').count() < 4
        || v.chars().any(|c| c.is_whitespace() || c == ':' || c == '"')
        || v.contains(PLACEHOLDER)
    {
        return Err(
            "is not a crypt(3) hash such as `openssl passwd -6` prints, or still carries a placeholder"
                .into(),
        );
    }
    Ok(v.into())
}

/// One OpenSSH public key line: `<type> <base64> [comment]`.
pub fn public_key(value: &str) -> Result<String, String> {
    let line = value
        .lines()
        .map(str::trim)
        .find(|l| !l.is_empty() && !l.starts_with('#'))
        .unwrap_or("");
    let mut parts = line.split_whitespace();
    let kind = parts.next().unwrap_or("");
    let body = parts.next().unwrap_or("");
    let typed = ["ssh-", "ecdsa-", "sk-"]
        .iter()
        .any(|p| kind.starts_with(p));
    let encoded = body.len() >= 16
        && body
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '+' | '/' | '='));
    if !typed || !encoded || line.contains(PLACEHOLDER) || line.contains('"') {
        return Err(
            "is not an OpenSSH public key line (`ssh-ed25519 AAAA... comment`), or still carries a placeholder"
                .into(),
        );
    }
    Ok(line.into())
}

/// Resolves the credential the disk is built with. The build environment wins
/// over the layered SSOT; [auth] keys count only under the policy that names
/// them. No credential at all is an error, never a default password.
pub fn credential(
    config: &toml::Value,
    env: &dyn Fn(&str) -> Option<String>,
) -> Result<Credential, String> {
    let hash_env = text(config, "deploy.identity.password_hash_env")?;
    let key_env = text(config, "deploy.identity.ssh_key_env")?;
    let from_env = |name: &str| env(name).filter(|v| !v.trim().is_empty());

    let password_hash = match from_env(hash_env) {
        Some(hash) => Some(crypt_hash(&hash).map_err(|e| format!("{hash_env} {e}"))?),
        None if optional(config, "auth.password_policy") == Some("hashed") => {
            optional(config, "auth.password_hash")
                .map(|hash| crypt_hash(hash).map_err(|e| format!("[auth].password_hash {e}")))
                .transpose()?
        }
        None => None,
    };

    let ssh_key = match from_env(key_env) {
        Some(key) => Some(public_key(&key).map_err(|e| format!("{key_env} {e}"))?),
        None if optional(config, "auth.ssh_key_action") == Some("existing") => {
            match optional(config, "auth.existing_ssh_key") {
                Some(value) => Some(existing_key(value, env)?),
                None => None,
            }
        }
        None => None,
    };

    if password_hash.is_none() && ssh_key.is_none() {
        return Err(format!(
            "no credential for the disk's account, and there is no default password: export \
             {hash_env} (`openssl passwd -6`) or {key_env} (an OpenSSH public key), or set \
             [auth].password_policy = \"hashed\" with [auth].password_hash, or \
             [auth].ssh_key_action = \"existing\" with [auth].existing_ssh_key, in your layered mios.toml"
        ));
    }
    Ok(Credential {
        password_hash,
        ssh_key,
    })
}

/// [auth].existing_ssh_key is the key itself or the path of its .pub file.
fn existing_key(value: &str, env: &dyn Fn(&str) -> Option<String>) -> Result<String, String> {
    if public_key(value).is_ok() {
        return public_key(value);
    }
    let path = match value.strip_prefix("~/") {
        Some(rest) => PathBuf::from(env("HOME").unwrap_or_default()).join(rest),
        None => PathBuf::from(value),
    };
    let body = std::fs::read_to_string(&path)
        .map_err(|e| format!("[auth].existing_ssh_key {}: {e}", path.display()))?;
    public_key(&body).map_err(|e| format!("[auth].existing_ssh_key {} {e}", path.display()))
}

fn string_list(config: &toml::Value, key: &str) -> Result<Vec<String>, String> {
    let Some(value) = at(config, key) else {
        return Ok(Vec::new());
    };
    value
        .as_array()
        .ok_or_else(|| format!("SSOT {key} must be an array"))?
        .iter()
        .map(|v| {
            v.as_str()
                .map(String::from)
                .ok_or_else(|| format!("SSOT {key} must hold strings"))
        })
        .collect()
}

/// The bootc-image-builder config for one build, as TOML text. The account is
/// [identity].username in [identity].groups (BIB modifies it when the image
/// already has it, creates it when not) plus the credential. A disk's root floor is
/// [bootc_install].root_min_gb; kernel arguments are the image's own kargs.d,
/// which bootc installs, so none are restated here.
pub fn render(
    config: &toml::Value,
    root: &Path,
    format: &DiskFormat,
    credential: &Credential,
) -> Result<String, String> {
    let user = text(config, "identity.username")?;
    let groups = string_list(config, "identity.groups")?;
    let recipe = format
        .spec
        .get("recipe")
        .and_then(toml::Value::as_str)
        .unwrap_or("");
    let mut doc = if recipe.is_empty() {
        toml::Table::new()
    } else {
        let body = std::fs::read_to_string(root.join(recipe))
            .map_err(|e| format!("recipe {recipe}: {e}"))?;
        if body.contains(PLACEHOLDER) {
            return Err(format!(
                "recipe {recipe} carries a {PLACEHOLDER} placeholder; credentials are rendered, never committed"
            ));
        }
        body.parse::<toml::Table>()
            .map_err(|e| format!("recipe {recipe}: {e}"))?
    };

    let customizations = doc
        .entry("customizations")
        .or_insert_with(|| toml::Value::Table(toml::Table::new()))
        .as_table_mut()
        .ok_or_else(|| format!("recipe {recipe}: customizations is not a table"))?;

    let kickstart = customizations
        .get_mut("installer")
        .and_then(|i| i.get_mut("kickstart"))
        .and_then(|k| k.as_table_mut())
        .filter(|k| k.contains_key("contents"));
    if let Some(kickstart) = kickstart {
        // BIB #528: with a kickstart present [customizations.user] is ignored,
        // so the account goes into the kickstart itself.
        let contents = kickstart
            .get("contents")
            .and_then(toml::Value::as_str)
            .ok_or_else(|| format!("recipe {recipe}: kickstart contents is not a string"))?;
        for line in contents.lines() {
            let command = line.split_whitespace().next().unwrap_or("");
            if KICKSTART_CREDENTIALS.contains(&command) {
                return Err(format!(
                    "recipe {recipe} kickstart already sets `{command}`; credentials are rendered, never committed"
                ));
            }
        }
        let mut out = contents.replace("\r\n", "\n").trim_end().to_string();
        out.push('\n');
        let mut account = format!("user --name={user}");
        if !groups.is_empty() {
            account.push_str(&format!(" --groups={}", groups.join(",")));
        }
        if let Some(hash) = &credential.password_hash {
            account.push_str(&format!(" --iscrypted --password={hash}"));
        }
        out.push_str(&account);
        out.push('\n');
        if let Some(key) = &credential.ssh_key {
            out.push_str(&format!("sshkey --username={user} \"{key}\"\n"));
        }
        kickstart.insert("contents".into(), toml::Value::String(out));
    } else {
        if customizations.contains_key("user") {
            return Err(format!(
                "recipe {recipe} declares [[customizations.user]]; the account is [identity], rendered"
            ));
        }
        let mut account = toml::Table::new();
        account.insert("name".into(), toml::Value::String(user.into()));
        if let Some(hash) = &credential.password_hash {
            account.insert("password".into(), toml::Value::String(hash.clone()));
        }
        if let Some(key) = &credential.ssh_key {
            account.insert("key".into(), toml::Value::String(key.clone()));
        }
        if !groups.is_empty() {
            account.insert(
                "groups".into(),
                toml::Value::Array(groups.into_iter().map(toml::Value::String).collect()),
            );
        }
        customizations.insert(
            "user".into(),
            toml::Value::Array(vec![toml::Value::Table(account)]),
        );

        let floor = at(config, "bootc_install.root_min_gb")
            .and_then(toml::Value::as_integer)
            .filter(|gb| *gb > 0)
            .ok_or("SSOT bootc_install.root_min_gb must be a positive integer")?;
        let filesystems = customizations
            .entry("filesystem")
            .or_insert_with(|| toml::Value::Array(Vec::new()))
            .as_array_mut()
            .ok_or_else(|| format!("recipe {recipe}: filesystem is not an array"))?;
        if filesystems
            .iter()
            .any(|fs| fs.get("mountpoint").and_then(toml::Value::as_str) == Some("/"))
        {
            return Err(format!(
                "recipe {recipe} sets the root floor; it is [bootc_install].root_min_gb"
            ));
        }
        let mut rootfs = toml::Table::new();
        rootfs.insert("mountpoint".into(), toml::Value::String("/".into()));
        rootfs.insert(
            "minsize".into(),
            toml::Value::String(format!("{floor} GiB")),
        );
        filesystems.push(toml::Value::Table(rootfs));
    }
    toml::to_string(&doc).map_err(|e| e.to_string())
}

/// One container invocation of a build.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Step {
    pub label: String,
    pub program: String,
    pub args: Vec<String>,
}

fn mount(host: &Path, guest: &str) -> String {
    format!("{}:{guest}", host.display())
}

/// bootc-image-builder writing `bib_type` from `image` into `output`.
pub fn bib_step(
    config: &toml::Value,
    format: &DiskFormat,
    image: &str,
    output: &Path,
    config_file: &Path,
    graphroot: &Path,
) -> Result<Step, String> {
    let args = [
        "run",
        "--rm",
        "--privileged",
        "--security-opt",
        "label=type:unconfined_t",
        "-v",
        &mount(output, "/output"),
        "-v",
        &mount(graphroot, "/var/lib/containers/storage"),
        "-v",
        &format!("{}:ro", mount(config_file, "/config.toml")),
        text(config, "image.bib")?,
        "build",
        "--type",
        &format.bib_type,
        "--rootfs",
        text(config, "bootc_install.root_fs_type")?,
        "--output",
        "/output",
        image,
    ];
    Ok(Step {
        label: format!("bootc-image-builder --type {}", format.bib_type),
        program: text(config, "build.images.engine")?.into(),
        args: args.iter().map(|a| a.to_string()).collect(),
    })
}

/// The disk the build ships: BIB's own file, or the format's name converted
/// from it with the qemu-img bootc-image-builder carries.
pub fn shipped_name(format: &DiskFormat) -> Option<String> {
    (format.bib_type != format.name).then(|| format!("disk.{}", format.name))
}

pub fn convert_step(
    config: &toml::Value,
    format: &DiskFormat,
    output: &Path,
    produced: &Path,
) -> Result<Option<Step>, String> {
    let Some(shipped) = shipped_name(format) else {
        return Ok(None);
    };
    let relative = produced
        .strip_prefix(output)
        .map_err(|_| format!("{} is outside {}", produced.display(), output.display()))?;
    let mut args: Vec<String> = [
        "run",
        "--rm",
        "--security-opt",
        "label=type:unconfined_t",
        "-v",
        &mount(output, "/output"),
        "--entrypoint",
        "qemu-img",
        text(config, "image.bib")?,
        "convert",
        "-p",
        "-O",
        &format.name,
    ]
    .iter()
    .map(|a| a.to_string())
    .collect();
    if let Some(sub) = format.spec.get("subformat").and_then(toml::Value::as_str) {
        args.extend(["-o".into(), format!("subformat={sub}")]);
    }
    args.extend([
        format!("/output/{}", relative.to_string_lossy().replace('\\', "/")),
        format!("/output/{shipped}"),
    ]);
    Ok(Some(Step {
        label: format!("qemu-img convert {} -> {}", format.bib_type, format.name),
        program: text(config, "build.images.engine")?.into(),
        args,
    }))
}

fn run(step: &Step) -> Result<(), String> {
    println!("[miosd] {}", step.label);
    let status = Command::new(&step.program)
        .args(&step.args)
        .status()
        .map_err(|e| format!("{}: {e}", step.program))?;
    if status.success() {
        Ok(())
    } else {
        Err(format!("{} failed: {status}", step.label))
    }
}

fn capture(program: &str, args: &[&str]) -> Result<String, String> {
    let out = Command::new(program)
        .args(args)
        .stderr(Stdio::inherit())
        .output()
        .map_err(|e| format!("{program}: {e}"))?;
    if !out.status.success() {
        return Err(format!(
            "{program} {} failed: {}",
            args.join(" "),
            out.status
        ));
    }
    Ok(String::from_utf8_lossy(&out.stdout).trim().to_string())
}

/// The newest file under `dir` with extension `ext`, depth-first.
fn produced(dir: &Path, ext: &str) -> Option<PathBuf> {
    let mut best: Option<(std::time::SystemTime, PathBuf)> = None;
    let mut stack = vec![dir.to_path_buf()];
    while let Some(d) = stack.pop() {
        for entry in std::fs::read_dir(&d).into_iter().flatten().flatten() {
            let path = entry.path();
            if path.is_dir() {
                stack.push(path);
            } else if path.extension().and_then(|e| e.to_str()) == Some(ext) {
                let when = entry
                    .metadata()
                    .and_then(|m| m.modified())
                    .unwrap_or(std::time::UNIX_EPOCH);
                if best.as_ref().is_none_or(|(t, _)| when > *t) {
                    best = Some((when, path));
                }
            }
        }
    }
    best.map(|(_, p)| p)
}

fn write_private(path: &Path, body: &str) -> Result<(), String> {
    std::fs::write(path, body).map_err(|e| format!("{}: {e}", path.display()))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600))
            .map_err(|e| format!("{}: {e}", path.display()))?;
    }
    Ok(())
}

/// Where a build writes, by default [build.artifacts].output_dir/<format>.
pub fn output_dir(config: &toml::Value, root: &Path, name: &str) -> Result<PathBuf, String> {
    Ok(root
        .join(text(config, "build.artifacts.output_dir")?)
        .join(name))
}

/// What `--plan` prints: the config as it would be rendered, values withheld,
/// and the builder invocation.
#[derive(Debug, Serialize)]
pub struct Plan {
    pub format: String,
    pub image: String,
    pub output: PathBuf,
    pub config: String,
    pub steps: Vec<Step>,
}

pub fn plan(
    config: &toml::Value,
    root: &Path,
    name: &str,
    image: Option<&str>,
    output: Option<&Path>,
    env: &dyn Fn(&str) -> Option<String>,
) -> Result<Plan, String> {
    let format = disk_format(config, name)?;
    let rendered = render(config, root, &format, &credential(config, env)?.redacted())?;
    let image = image
        .map(String::from)
        .unwrap_or(text(config, "image.local_tag")?.into());
    let output = match output {
        Some(o) => o.to_path_buf(),
        None => output_dir(config, root, name)?,
    };
    let bib = bib_step(
        config,
        &format,
        &image,
        &output,
        Path::new("<rendered config, 0600>"),
        Path::new("<podman graphroot>"),
    )?;
    Ok(Plan {
        format: name.into(),
        image,
        output,
        config: rendered,
        steps: vec![bib],
    })
}

/// Builds the disk and returns the file that ships.
pub fn build(
    config: &toml::Value,
    root: &Path,
    name: &str,
    image: Option<&str>,
    output: Option<&Path>,
    env: &dyn Fn(&str) -> Option<String>,
) -> Result<PathBuf, String> {
    let format = disk_format(config, name)?;
    let rendered = render(config, root, &format, &credential(config, env)?)?;
    let image = image
        .map(String::from)
        .unwrap_or(text(config, "image.local_tag")?.into());
    let output = match output {
        Some(o) => o.to_path_buf(),
        None => output_dir(config, root, name)?,
    };
    std::fs::create_dir_all(&output).map_err(|e| format!("{}: {e}", output.display()))?;
    let output = output
        .canonicalize()
        .map_err(|e| format!("{}: {e}", output.display()))?;
    let engine = text(config, "build.images.engine")?;

    // The credential lives only in this private directory, for one run.
    let scratch = tempfile::tempdir().map_err(|e| format!("temporary directory: {e}"))?;
    let config_file = scratch.path().join("config.toml");
    write_private(&config_file, &rendered)?;

    let present = Command::new(engine)
        .args(["image", "exists", &image])
        .status()
        .map(|s| s.success())
        .unwrap_or(false);
    if !present {
        run(&Step {
            label: format!("pull {image}"),
            program: engine.into(),
            args: vec!["pull".into(), image.clone()],
        })?;
    }
    let graphroot = PathBuf::from(capture(
        engine,
        &["info", "--format", "{{.Store.GraphRoot}}"],
    )?);
    run(&bib_step(
        config,
        &format,
        &image,
        &output,
        &config_file,
        &graphroot,
    )?)?;

    let made = produced(&output, &format.bib_type).ok_or_else(|| {
        format!(
            "bootc-image-builder wrote no .{} under {}",
            format.bib_type,
            output.display()
        )
    })?;
    let shipped = match convert_step(config, &format, &output, &made)? {
        Some(step) => {
            run(&step)?;
            let _ = std::fs::remove_file(&made);
            if let Some(parent) = made.parent().filter(|p| *p != output) {
                let _ = std::fs::remove_dir(parent);
            }
            output.join(shipped_name(&format).unwrap_or_default())
        }
        None => made,
    };
    let floor = at(config, "deploy.verify.min_bytes")
        .and_then(toml::Value::as_integer)
        .filter(|n| *n > 0)
        .ok_or("SSOT deploy.verify.min_bytes must be a positive integer")?;
    let size = std::fs::metadata(&shipped)
        .map_err(|e| format!("{}: {e}", shipped.display()))?
        .len();
    if size < floor as u64 {
        return Err(format!(
            "{} is {size} bytes, under [deploy.verify].min_bytes {floor}: a truncated write, not a disk",
            shipped.display()
        ));
    }
    Ok(shipped)
}

/// The virtual machine a format's disk is made for, [deploy.formats.<f>.vm].
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct VmShape {
    pub generation: i64,
    pub secure_boot_template: String,
    pub processors: i64,
    pub memory: String,
    pub memory_mib: u64,
    pub dynamic_memory: bool,
    pub switch: String,
}

/// `8GB`, `8192MB`: PowerShell's binary multipliers, as New-VM reads them.
pub fn memory_mib(value: &str) -> Result<u64, String> {
    let v = value.trim();
    let split = v.find(|c: char| !c.is_ascii_digit()).unwrap_or(v.len());
    let (digits, unit) = v.split_at(split);
    let n: u64 = digits
        .parse()
        .map_err(|_| format!("memory {value:?} is not <number><MB|GB|TB>"))?;
    let scale = match unit.to_ascii_uppercase().as_str() {
        "MB" => 1,
        "GB" => 1024,
        "TB" => 1024 * 1024,
        _ => return Err(format!("memory {value:?} is not <number><MB|GB|TB>")),
    };
    Ok(n * scale)
}

pub fn vm_shape(config: &toml::Value, name: &str) -> Result<VmShape, String> {
    let key = format!("deploy.formats.{name}.vm");
    let vm = at(config, &key)
        .filter(|v| v.is_table())
        .ok_or_else(|| format!("[{key}] is not declared: the format names no VM to boot in"))?;
    let int = |k: &str| {
        vm.get(k)
            .and_then(toml::Value::as_integer)
            .ok_or_else(|| format!("[{key}].{k} must be an integer"))
    };
    let generation = int("generation")?;
    if generation != 2 {
        return Err(format!(
            "[{key}].generation = {generation}: a bootc disk is UEFI and GPT, which only a Generation 2 VM boots"
        ));
    }
    let processors = int("processors")?;
    if processors < 1 {
        return Err(format!("[{key}].processors must be at least 1"));
    }
    let memory = text(config, &format!("{key}.memory"))?.to_string();
    let mib = memory_mib(&memory)?;
    let floor = at(config, "preflight.min_ram_gb")
        .and_then(toml::Value::as_integer)
        .filter(|gb| *gb > 0)
        .ok_or("SSOT preflight.min_ram_gb must be a positive integer")?;
    if mib < floor as u64 * 1024 {
        return Err(format!(
            "[{key}].memory {memory} is under MiOS's own floor, [preflight].min_ram_gb = {floor}"
        ));
    }
    Ok(VmShape {
        generation,
        secure_boot_template: optional(config, &format!("{key}.secure_boot_template"))
            .unwrap_or("")
            .into(),
        processors,
        memory,
        memory_mib: mib,
        dynamic_memory: vm
            .get("dynamic_memory")
            .and_then(toml::Value::as_bool)
            .unwrap_or(false),
        switch: text(config, &format!("{key}.switch"))?.into(),
    })
}

/// `90s`, `25m`, `2h`.
pub fn duration(value: &str) -> Result<Duration, String> {
    let v = value.trim();
    let (digits, unit) = v.split_at(v.len().saturating_sub(1));
    let n: u64 = digits
        .parse()
        .map_err(|_| format!("duration {value:?} is not <number><s|m|h>"))?;
    let seconds = match unit {
        "s" => n,
        "m" => n * 60,
        "h" => n * 3600,
        _ => return Err(format!("duration {value:?} is not <number><s|m|h>")),
    };
    Ok(Duration::from_secs(seconds))
}

/// UEFI firmware for one boot: code, a variable store, and whether it is the
/// Secure Boot build (which needs SMM).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Firmware {
    pub code: PathBuf,
    pub vars: PathBuf,
    pub secure_boot: bool,
}

/// The first [testing.boot].firmware entry present on this host whose
/// `secure_boot` matches [testing.boot].secure_boot. The Secure Boot entries
/// enroll Microsoft's UEFI CA, the trust the Hyper-V template names.
pub fn firmware(config: &toml::Value, exists: &dyn Fn(&Path) -> bool) -> Result<Firmware, String> {
    let secure_boot = at(config, "testing.boot.secure_boot")
        .and_then(toml::Value::as_bool)
        .ok_or("SSOT testing.boot.secure_boot must be a boolean")?;
    let entries = at(config, "testing.boot.firmware")
        .and_then(toml::Value::as_array)
        .filter(|a| !a.is_empty())
        .ok_or(
            "SSOT testing.boot.firmware must be a nonempty array of { code, vars, secure_boot }",
        )?;
    for entry in entries {
        let code = entry.get("code").and_then(toml::Value::as_str);
        let vars = entry.get("vars").and_then(toml::Value::as_str);
        let secure = entry.get("secure_boot").and_then(toml::Value::as_bool);
        let (Some(code), Some(vars), Some(secure)) = (code, vars, secure) else {
            return Err(
                "SSOT testing.boot.firmware entries need code, vars and secure_boot".into(),
            );
        };
        let (code, vars) = (PathBuf::from(code), PathBuf::from(vars));
        if secure == secure_boot && exists(&code) && exists(&vars) {
            return Ok(Firmware {
                code,
                vars,
                secure_boot,
            });
        }
    }
    Err(format!(
        "no [testing.boot].firmware entry with secure_boot = {secure_boot} is installed on this host (install OVMF / edk2-ovmf)"
    ))
}

/// The QEMU command line for one boot of `disk` in `shape` on `fw` (its vars
/// a private copy). The disk is opened with snapshot=on: the boot test never
/// changes the artifact it ships.
pub fn qemu_args(
    shape: &VmShape,
    disk: &Path,
    disk_format: &str,
    fw: &Firmware,
    ssh_port: u16,
    serial_log: &Path,
) -> Vec<String> {
    let (secure, code, vars) = (fw.secure_boot, fw.code.as_path(), fw.vars.as_path());
    let mut args = vec![
        "-machine".to_string(),
        format!("q35,smm={},accel=kvm", if secure { "on" } else { "off" }),
        "-cpu".into(),
        "host".into(),
        "-smp".into(),
        shape.processors.to_string(),
        "-m".into(),
        shape.memory_mib.to_string(),
    ];
    if secure {
        args.extend([
            "-global".into(),
            "driver=cfi.pflash01,property=secure,value=on".into(),
        ]);
    }
    // Newer edk2 ships its 4M images as qcow2; the rest are raw.
    let flash = |p: &Path| {
        if p.extension().is_some_and(|e| e == "qcow2") {
            "qcow2"
        } else {
            "raw"
        }
    };
    args.extend([
        "-drive".into(),
        format!(
            "if=pflash,format={},unit=0,readonly=on,file={}",
            flash(code),
            code.display()
        ),
        "-drive".into(),
        format!(
            "if=pflash,format={},unit=1,file={}",
            flash(vars),
            vars.display()
        ),
        "-drive".into(),
        format!(
            "file={},format={disk_format},if=virtio,snapshot=on",
            disk.display()
        ),
        "-netdev".into(),
        format!("user,id=net0,hostfwd=tcp:127.0.0.1:{ssh_port}-:22"),
        "-device".into(),
        "virtio-net-pci,netdev=net0".into(),
        "-serial".into(),
        format!("file:{}", serial_log.display()),
        "-display".into(),
        "none".into(),
        "-no-reboot".into(),
    ]);
    args
}

pub fn ssh_args(identity: &Path, port: u16, user: &str, command: &str) -> Vec<String> {
    let mut args: Vec<String> = vec!["-i".into(), identity.display().to_string()];
    for option in [
        "BatchMode=yes",
        "StrictHostKeyChecking=no",
        "UserKnownHostsFile=/dev/null",
        "ConnectTimeout=10",
        "LogLevel=ERROR",
    ] {
        args.extend(["-o".into(), option.into()]);
    }
    args.extend([
        "-p".into(),
        port.to_string(),
        format!("{user}@127.0.0.1"),
        command.into(),
    ]);
    args
}

fn tail(path: &Path, lines: usize) -> String {
    let body = std::fs::read(path).unwrap_or_default();
    let body = String::from_utf8_lossy(&body);
    let all: Vec<&str> = body.lines().collect();
    all[all.len().saturating_sub(lines)..].join("\n")
}

/// Runs `ssh`, killing it at `deadline`; returns its exit success and output.
fn ssh(args: &[String], deadline: Instant, scratch: &Path) -> Result<(bool, String), String> {
    let log = scratch.join("ssh.out");
    let file = std::fs::File::create(&log).map_err(|e| e.to_string())?;
    let err = file.try_clone().map_err(|e| e.to_string())?;
    let mut child = Command::new("ssh")
        .args(args)
        .stdin(Stdio::null())
        .stdout(file)
        .stderr(err)
        .spawn()
        .map_err(|e| format!("ssh: {e}"))?;
    loop {
        if let Some(status) = child.try_wait().map_err(|e| e.to_string())? {
            let out = std::fs::read_to_string(&log).unwrap_or_default();
            return Ok((status.success(), out.trim().to_string()));
        }
        if Instant::now() >= deadline {
            let _ = child.kill();
            let _ = child.wait();
            return Ok((false, "timed out".into()));
        }
        std::thread::sleep(Duration::from_millis(500));
    }
}

/// One probe's evidence: the command and what the guest printed.
#[derive(Debug, Serialize)]
pub struct Probe {
    pub command: String,
    pub output: String,
}

/// Boots `disk` under QEMU in the format's VM shape, waits for SSH with
/// `identity` (the private half of the key the disk was built with), and runs
/// every [testing.boot].probes command in the guest. The first failing probe,
/// a guest that never answers, or a QEMU that exits early fails the test.
pub fn boot_test(
    config: &toml::Value,
    name: &str,
    disk: &Path,
    identity: &Path,
    serial_log: &Path,
) -> Result<Vec<Probe>, String> {
    let shape = vm_shape(config, name)?;
    let mut fw = firmware(config, &|p: &Path| p.is_file())?;
    let timeout = duration(text(config, "testing.boot.timeout")?)?;
    let probes = string_list(config, "testing.boot.probes")?;
    if probes.is_empty() {
        return Err(
            "SSOT testing.boot.probes is empty: a boot test that asserts nothing passes every disk"
                .into(),
        );
    }
    let user = text(config, "identity.username")?;
    if !disk.is_file() {
        return Err(format!("{} does not exist", disk.display()));
    }
    if !Path::new("/dev/kvm").exists() {
        return Err("no /dev/kvm: the boot test needs hardware virtualisation".into());
    }

    let scratch = tempfile::tempdir().map_err(|e| format!("temporary directory: {e}"))?;
    // The guest writes its variable store; it gets a private copy, same name.
    let vars = scratch.path().join(
        fw.vars
            .file_name()
            .unwrap_or(std::ffi::OsStr::new("vars.fd")),
    );
    std::fs::copy(&fw.vars, &vars).map_err(|e| format!("{}: {e}", fw.vars.display()))?;
    fw.vars = vars;
    let port = std::net::TcpListener::bind(("127.0.0.1", 0))
        .and_then(|l| l.local_addr())
        .map(|a| a.port())
        .map_err(|e| format!("no free loopback port: {e}"))?;
    let qemu = format!("qemu-system-{}", std::env::consts::ARCH);
    let args = qemu_args(&shape, disk, name, &fw, port, serial_log);
    println!("[miosd] {qemu} {}", args.join(" "));
    let mut child = Command::new(&qemu)
        .args(&args)
        .stdin(Stdio::null())
        .spawn()
        .map_err(|e| format!("{qemu}: {e}"))?;

    let deadline = Instant::now() + timeout;
    let result = (|| {
        let started = Instant::now();
        loop {
            if let Some(status) = child.try_wait().map_err(|e| e.to_string())? {
                return Err(format!(
                    "{qemu} exited {status} before the guest answered SSH; serial log tail:\n{}",
                    tail(serial_log, 60)
                ));
            }
            let attempt = Instant::now() + Duration::from_secs(30);
            let (ok, _) = ssh(
                &ssh_args(identity, port, user, "true"),
                attempt.min(deadline),
                scratch.path(),
            )?;
            if ok {
                println!(
                    "[miosd] guest answered SSH after {}s",
                    started.elapsed().as_secs()
                );
                break;
            }
            if Instant::now() >= deadline {
                return Err(format!(
                    "the guest did not answer SSH within {}s; serial log tail:\n{}",
                    timeout.as_secs(),
                    tail(serial_log, 60)
                ));
            }
            std::thread::sleep(Duration::from_secs(10));
        }
        let mut evidence = Vec::new();
        for probe in &probes {
            let (ok, output) = ssh(
                &ssh_args(identity, port, user, probe),
                deadline,
                scratch.path(),
            )?;
            println!(
                "[miosd] probe `{probe}`: {}",
                if ok { "ok" } else { "FAILED" }
            );
            if !output.is_empty() {
                println!("{output}");
            }
            if !ok {
                return Err(format!("probe `{probe}` failed in the guest: {output}"));
            }
            evidence.push(Probe {
                command: probe.clone(),
                output,
            });
        }
        Ok(evidence)
    })();
    let _ = child.kill();
    let _ = child.wait();
    result
}

#[cfg(test)]
mod tests {
    use super::*;

    const HASH: &str = "$6$saltsalt$0123456789abcdefABCDEF./";
    const KEY: &str = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOperatorKeyBody0123456789 op@host";

    fn ssot() -> toml::Value {
        toml::from_str(
            r#"
            [identity]
            username = "operator"
            groups = ["wheel", "video"]
            [auth]
            password_policy = "plain"
            password_hash = ""
            ssh_key_action = "generate"
            existing_ssh_key = ""
            [bootc_install]
            root_fs_type = "ext4"
            root_min_gb = 80
            [deployment]
            target_vhd = true
            target_qcow2 = true
            target_iso = true
            target_raw = false
            [image]
            bib = "registry.example/bib:pinned"
            local_tag = "localhost/os:latest"
            [build.images]
            engine = "podman"
            [build.artifacts]
            output_dir = "build"
            [deploy.verify]
            min_bytes = 4194304
            [deploy.identity]
            password_hash_env = "OP_HASH"
            ssh_key_env = "OP_KEY"
            [deploy.formats.vhdx]
            recipe = ""
            bib_type = "vhd"
            subformat = "dynamic"
            [deploy.formats.vhdx.vm]
            generation = 2
            secure_boot_template = "MicrosoftUEFICertificateAuthority"
            processors = 4
            memory = "8GB"
            dynamic_memory = false
            switch = "Default Switch"
            [deploy.formats.qcow2]
            recipe = ""
            [deploy.formats.raw]
            recipe = ""
            [deploy.formats.iso]
            recipe = "iso.toml"
            [deploy.formats.wsl2]
            recipe = ""
            [preflight]
            min_ram_gb = 8
            [testing.boot]
            timeout = "25m"
            secure_boot = true
            firmware = [
              { code = "/a/CODE.secboot.fd", vars = "/a/VARS.ms.fd", secure_boot = true },
              { code = "/b/CODE.secboot.fd", vars = "/b/VARS.ms.fd", secure_boot = true },
              { code = "/b/CODE.fd", vars = "/b/VARS.fd", secure_boot = false },
            ]
            probes = ["systemctl is-active multi-user.target"]
            "#,
        )
        .unwrap()
    }

    fn env(pairs: &'static [(&'static str, &'static str)]) -> impl Fn(&str) -> Option<String> {
        move |name| {
            pairs
                .iter()
                .find(|(k, _)| *k == name)
                .map(|(_, v)| v.to_string())
        }
    }

    #[test]
    fn the_disk_type_comes_from_the_format_and_must_be_enabled() {
        let mut config = ssot();
        let vhdx = disk_format(&config, "vhdx").unwrap();
        assert_eq!(vhdx.bib_type, "vhd");
        assert_eq!(shipped_name(&vhdx).as_deref(), Some("disk.vhdx"));
        let qcow2 = disk_format(&config, "qcow2").unwrap();
        assert_eq!(qcow2.bib_type, "qcow2");
        assert_eq!(shipped_name(&qcow2), None);
        assert!(disk_format(&config, "raw")
            .unwrap_err()
            .contains("disabled"));
        assert!(disk_format(&config, "wsl2")
            .unwrap_err()
            .contains("target_wsl2"));
        assert!(disk_format(&config, "nope")
            .unwrap_err()
            .contains("not a declared"));
        config["deployment"]["target_vhd"] = toml::Value::Boolean(false);
        assert!(disk_format(&config, "vhdx")
            .unwrap_err()
            .contains("target_vhd"));
    }

    #[test]
    fn no_credential_fails_closed_and_the_vendor_password_is_never_used() {
        let mut config = ssot();
        // The vendor [auth].password is plain text "mios"; it must not count.
        config["auth"]
            .as_table_mut()
            .unwrap()
            .insert("password".into(), toml::Value::String("mios".into()));
        let error = credential(&config, &env(&[])).unwrap_err();
        assert!(error.contains("no default password"), "{error}");
        assert!(
            error.contains("OP_HASH") && error.contains("OP_KEY"),
            "{error}"
        );
        // A hash in [auth] counts only under the policy that names it.
        config["auth"]["password_hash"] = toml::Value::String(HASH.into());
        assert!(credential(&config, &env(&[])).is_err());
        config["auth"]["password_policy"] = toml::Value::String("hashed".into());
        let found = credential(&config, &env(&[])).unwrap();
        assert_eq!(found.password_hash.as_deref(), Some(HASH));
        assert_eq!(found.ssh_key, None);
    }

    #[test]
    fn placeholders_and_malformed_credentials_are_refused() {
        let config = ssot();
        for bad in [
            "$6$REPLACEME_WITH_SHA512_HASH$REPLACEME",
            "mios",
            "$6$salt",
            "$6$salt$has space",
        ] {
            let pairs: &'static [(&str, &str)] =
                Box::leak(vec![("OP_HASH", bad)].into_boxed_slice());
            assert!(credential(&config, &env(pairs)).is_err(), "{bad}");
        }
        for bad in [
            "ssh-ed25519 AAAA_REPLACE_WITH_REAL_PUBKEY mios@operator",
            "not-a-key AAAAC3NzaC1lZDI1NTE5AAAAIOperator",
            "ssh-ed25519 short",
        ] {
            let pairs: &'static [(&str, &str)] =
                Box::leak(vec![("OP_KEY", bad)].into_boxed_slice());
            assert!(credential(&config, &env(pairs)).is_err(), "{bad}");
        }
        let ok = credential(&config, &env(&[("OP_KEY", KEY)])).unwrap();
        assert_eq!(ok.ssh_key.as_deref(), Some(KEY));
    }

    #[test]
    fn the_existing_key_may_be_a_pub_file() {
        let dir = tempfile::tempdir().unwrap();
        let pubfile = dir.path().join("id.pub");
        std::fs::write(&pubfile, format!("{KEY}\n")).unwrap();
        let mut config = ssot();
        config["auth"]["ssh_key_action"] = toml::Value::String("existing".into());
        config["auth"]["existing_ssh_key"] = toml::Value::String(pubfile.display().to_string());
        assert_eq!(
            credential(&config, &env(&[])).unwrap().ssh_key.as_deref(),
            Some(KEY)
        );
        config["auth"]["existing_ssh_key"] = toml::Value::String(KEY.into());
        assert_eq!(
            credential(&config, &env(&[])).unwrap().ssh_key.as_deref(),
            Some(KEY)
        );
    }

    #[test]
    fn a_disk_config_is_identity_plus_credential_plus_the_root_floor() {
        let dir = tempfile::tempdir().unwrap();
        let mut config = ssot();
        let credential = credential(&config, &env(&[("OP_HASH", HASH), ("OP_KEY", KEY)])).unwrap();
        let format = disk_format(&config, "vhdx").unwrap();
        let rendered: toml::Value =
            toml::from_str(&render(&config, dir.path(), &format, &credential).unwrap()).unwrap();
        let user = &rendered["customizations"]["user"][0];
        assert_eq!(user["name"].as_str(), Some("operator"));
        assert_eq!(user["password"].as_str(), Some(HASH));
        assert_eq!(user["key"].as_str(), Some(KEY));
        assert_eq!(user["groups"].as_array().unwrap().len(), 2);
        let fs = &rendered["customizations"]["filesystem"][0];
        assert_eq!(fs["minsize"].as_str(), Some("80 GiB"));
        // Kernel arguments are the image's kargs.d, never restated here.
        assert!(rendered["customizations"].get("kernel").is_none());
        // An operator edit of the floor is what the disk gets.
        config["bootc_install"]["root_min_gb"] = toml::Value::Integer(96);
        let format = disk_format(&config, "vhdx").unwrap();
        let again = render(&config, dir.path(), &format, &credential).unwrap();
        assert!(again.contains("96 GiB"), "{again}");
        // Only the half the operator supplied is rendered.
        let key_only = Credential {
            password_hash: None,
            ssh_key: Some(KEY.into()),
        };
        let rendered = render(&config, dir.path(), &format, &key_only).unwrap();
        assert!(!rendered.contains("password"), "{rendered}");
    }

    #[test]
    fn a_kickstart_recipe_gets_the_account_in_the_kickstart() {
        let dir = tempfile::tempdir().unwrap();
        let recipe = "[[customizations.filesystem]]\nmountpoint = \"/\"\nminsize = \"150 GiB\"\n\
             [customizations.installer.kickstart]\ncontents = \"\"\"\ntext\nreboot --eject\n\"\"\"\n";
        std::fs::write(dir.path().join("iso.toml"), recipe).unwrap();
        let config = ssot();
        let credential = credential(&config, &env(&[("OP_HASH", HASH), ("OP_KEY", KEY)])).unwrap();
        let format = disk_format(&config, "iso").unwrap();
        let rendered: toml::Value =
            toml::from_str(&render(&config, dir.path(), &format, &credential).unwrap()).unwrap();
        let ks = rendered["customizations"]["installer"]["kickstart"]["contents"]
            .as_str()
            .unwrap();
        assert!(ks.contains(&format!(
            "user --name=operator --groups=wheel,video --iscrypted --password={HASH}"
        )));
        assert!(ks.contains(&format!("sshkey --username=operator \"{KEY}\"")));
        assert!(rendered["customizations"].get("user").is_none());
        // The recipe's own floor stands; it is [deploy.artifacts.iso]'s projection.
        assert_eq!(
            rendered["customizations"]["filesystem"][0]["minsize"].as_str(),
            Some("150 GiB")
        );
        // A committed credential or placeholder in the recipe stops the build.
        std::fs::write(
            dir.path().join("iso.toml"),
            recipe.replace("text\n", "text\nrootpw --lock\n"),
        )
        .unwrap();
        assert!(render(&config, dir.path(), &format, &credential)
            .unwrap_err()
            .contains("rootpw"));
        std::fs::write(
            dir.path().join("iso.toml"),
            recipe.replace("text\n", "text\n# REPLACEME\n"),
        )
        .unwrap();
        assert!(render(&config, dir.path(), &format, &credential)
            .unwrap_err()
            .contains(PLACEHOLDER));
    }

    #[test]
    fn steps_take_every_value_from_the_ssot() {
        let config = ssot();
        let format = disk_format(&config, "vhdx").unwrap();
        let out = Path::new("/out");
        let step = bib_step(
            &config,
            &format,
            "ghcr.io/x/os@sha256:abc",
            out,
            Path::new("/tmp/c.toml"),
            Path::new("/mnt/store"),
        )
        .unwrap();
        assert_eq!(step.program, "podman");
        let joined = step.args.join(" ");
        for want in [
            "registry.example/bib:pinned build --type vhd --rootfs ext4",
            "/mnt/store:/var/lib/containers/storage",
            "/tmp/c.toml:/config.toml:ro",
        ] {
            assert!(joined.contains(want), "{joined}");
        }
        assert_eq!(
            step.args.last().map(String::as_str),
            Some("ghcr.io/x/os@sha256:abc")
        );
        // Exactly one /config.toml, the single-config invariant.
        assert_eq!(joined.matches(":/config.toml").count(), 1);
        let convert = convert_step(&config, &format, out, Path::new("/out/vpc/disk.vhd"))
            .unwrap()
            .unwrap();
        let joined = convert.args.join(" ");
        assert!(
            joined.ends_with(
                "convert -p -O vhdx -o subformat=dynamic /output/vpc/disk.vhd /output/disk.vhdx"
            ),
            "{joined}"
        );
        let qcow2 = disk_format(&config, "qcow2").unwrap();
        assert!(
            convert_step(&config, &qcow2, out, Path::new("/out/qcow2/disk.qcow2"))
                .unwrap()
                .is_none()
        );
    }

    #[test]
    fn the_vm_shape_is_validated_against_what_boots() {
        let mut config = ssot();
        let shape = vm_shape(&config, "vhdx").unwrap();
        assert_eq!(shape.memory_mib, 8192);
        assert_eq!(shape.processors, 4);
        assert!(vm_shape(&config, "qcow2").unwrap_err().contains("no VM"));
        config["deploy"]["formats"]["vhdx"]["vm"]["generation"] = toml::Value::Integer(1);
        assert!(vm_shape(&config, "vhdx")
            .unwrap_err()
            .contains("Generation 2"));
        config["deploy"]["formats"]["vhdx"]["vm"]["generation"] = toml::Value::Integer(2);
        config["deploy"]["formats"]["vhdx"]["vm"]["memory"] = toml::Value::String("4GB".into());
        assert!(vm_shape(&config, "vhdx")
            .unwrap_err()
            .contains("min_ram_gb"));
        assert_eq!(memory_mib("16384MB").unwrap(), 16384);
        assert!(memory_mib("8 gigs").is_err());
        assert_eq!(duration("25m").unwrap(), Duration::from_secs(1500));
        assert!(duration("25").is_err());
    }

    #[test]
    fn the_boot_command_matches_the_shape_and_never_writes_the_disk() {
        let config = ssot();
        let shape = vm_shape(&config, "vhdx").unwrap();
        // The first PRESENT entry of the requested kind wins.
        let fw = firmware(&config, &|p: &Path| p.starts_with("/b")).unwrap();
        assert_eq!(fw.code, Path::new("/b/CODE.secboot.fd"));
        assert!(fw.secure_boot);
        assert!(firmware(&config, &|_: &Path| false).is_err());
        let args = qemu_args(
            &shape,
            Path::new("/out/disk.vhdx"),
            "vhdx",
            &fw,
            2200,
            Path::new("/tmp/serial.log"),
        )
        .join(" ");
        for want in [
            "-smp 4",
            "-m 8192",
            "q35,smm=on",
            "property=secure,value=on",
            "file=/b/CODE.secboot.fd",
            "file=/out/disk.vhdx,format=vhdx,if=virtio,snapshot=on",
            "hostfwd=tcp:127.0.0.1:2200-:22",
        ] {
            assert!(args.contains(want), "{want} not in {args}");
        }
        // Where SMM cannot run (KVM nested under Hyper-V), the operator turns
        // Secure Boot off and the plain firmware is chosen, without SMM.
        let mut config = config;
        config["testing"]["boot"]["secure_boot"] = toml::Value::Boolean(false);
        let fw = firmware(&config, &|_: &Path| true).unwrap();
        assert_eq!(fw.code, Path::new("/b/CODE.fd"));
        let args = qemu_args(
            &shape,
            Path::new("/out/disk.vhdx"),
            "vhdx",
            &fw,
            2200,
            Path::new("/tmp/serial.log"),
        )
        .join(" ");
        assert!(
            args.contains("q35,smm=off") && !args.contains("property=secure"),
            "{args}"
        );
        let ssh = ssh_args(Path::new("/k"), 2200, "operator", "true").join(" ");
        assert!(ssh.ends_with("-p 2200 operator@127.0.0.1 true"), "{ssh}");
    }
}
