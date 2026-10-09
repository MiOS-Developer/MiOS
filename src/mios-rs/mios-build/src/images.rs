// AI-hint: Build the operator's current source tree using SSOT image targets and verify installed runtime commands; never fetch, reset or reap it.
// AI-related: build-mios.ps1, Containerfile, .devcontainer/Containerfile, usr/share/mios/mios.toml
use serde::Serialize;
use std::path::{Component, Path, PathBuf};

#[derive(Debug, Serialize)]
pub struct ImageCommand {
    pub target: String,
    pub engine: String,
    pub root: PathBuf,
    pub args: Vec<String>,
}

fn string_at<'a>(config: &'a toml::Value, key: &str) -> Result<&'a str, String> {
    let value = key.split('.').try_fold(config, |node, part| node.get(part));
    value.and_then(toml::Value::as_str).filter(|s| !s.trim().is_empty())
        .ok_or_else(|| format!("SSOT {key} must be a nonempty string"))
}

pub fn image_plan(root: &Path, config: &toml::Value, selected: &str) -> Result<Vec<ImageCommand>, String> {
    let root = root.canonicalize().map_err(|e| format!("source root: {e}"))?;
    let settings = config.get("build").and_then(|b| b.get("images"))
        .ok_or("SSOT [build.images] is missing")?;
    let engine = string_at(config, "build.images.engine")?;
    let order = settings.get("order").and_then(toml::Value::as_array)
        .ok_or("SSOT build.images.order must be an array")?;
    if order.is_empty() { return Err("SSOT build.images.order is empty".into()); }
    let mut names = std::collections::BTreeSet::new();
    let mut plan = Vec::new();
    for name in order {
        let name = name.as_str().ok_or("SSOT image target must be a string")?;
        if !names.insert(name) { return Err(format!("duplicate image target {name}")); }
        if selected != "all" && selected != name { continue; }
        let target = settings.get(name).ok_or_else(|| format!("missing image target {name}"))?;
        let file = target.get("containerfile").and_then(toml::Value::as_str)
            .ok_or_else(|| format!("image target {name} has no containerfile"))?;
        let path = Path::new(file);
        if path.is_absolute() || path.components().any(|c| !matches!(c, Component::Normal(_) | Component::CurDir)) {
            return Err(format!("containerfile {file} must stay inside the source root"));
        }
        let resolved = root.join(path).canonicalize().map_err(|e| format!("containerfile {file}: {e}"))?;
        if !resolved.starts_with(&root) || !resolved.is_file() { return Err(format!("invalid containerfile {file}")); }
        let tag_key = target.get("tag_key").and_then(toml::Value::as_str).ok_or("image tag_key missing")?;
        let tag = string_at(config, tag_key)?;
        let mut args = vec!["build".into(), "--network".into(), string_at(config, "build.images.network")?.into()];
        let retries = settings.get("retries").and_then(toml::Value::as_integer).filter(|n| *n >= 0)
            .ok_or("SSOT build.images.retries must be a nonnegative integer")?;
        args.extend(["--retry".into(), retries.to_string(), "--retry-delay".into(), string_at(config, "build.images.retry_delay")?.into()]);
        if let Some(build_args) = target.get("build_args") {
            for (arg, key) in build_args.as_table().ok_or("image build_args must be a table")? {
                let key = key.as_str().ok_or("image build argument must reference an SSOT key")?;
                args.extend(["--build-arg".into(), format!("{arg}={}", string_at(config, key)?)]);
            }
        }
        args.extend(["-f".into(), file.into(), "-t".into(), tag.into(), ".".into()]);
        plan.push(ImageCommand { target: name.into(), engine: engine.into(), root: root.clone(), args });
        let probes = config.get("packages").and_then(|p| p.get("mcp")).and_then(|p| p.get("verify_probes"))
            .and_then(toml::Value::as_array).filter(|v| !v.is_empty()).ok_or("SSOT packages.mcp.verify_probes is empty or missing")?;
        let mut args: Vec<String> = ["run", "--rm", "--entrypoint", "/bin/sh", tag, "-c",
            "for cmd; do command -v \"$cmd\" || exit 1; done", "mios-runtime-check"].into_iter().map(String::from).collect();
        for probe in probes {
            let probe = probe.as_str().filter(|p| !p.is_empty()).ok_or("runtime command probe must be a nonempty string")?;
            args.push(probe.into());
        }
        plan.push(ImageCommand { target: format!("{name}:runtime"), engine: engine.into(), root: root.clone(), args });
    }
    if plan.is_empty() { return Err(format!("unknown image target {selected}")); }
    Ok(plan)
}

pub fn execute_images(plan: &[ImageCommand], mut run: impl FnMut(&ImageCommand) -> Result<(), String>) -> Result<(), String> {
    if plan.is_empty() { return Err("empty image build plan".into()); }
    for command in plan { run(command).map_err(|e| format!("{} failed: {e}", command.target))?; }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    fn fixture() -> (tempfile::TempDir, toml::Value) {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("Containerfile"), "FROM scratch\n").unwrap();
        let config = toml::from_str(r#"
            [build.images]
            engine="podman"
            order=["os"]
            network="host"
            retries=5
            retry_delay="3s"
            [build.images.os]
            containerfile="Containerfile"
            tag_key="image.local_tag"
            build_args={BASE_IMAGE="image.base"}
            [image]
            local_tag="localhost/operator-edited:latest"
            base="registry.example/operator-base:custom"
            [packages.mcp]
            verify_probes=["btop", "fastfetch"]
        "#).unwrap();
        (dir, config)
    }
    #[test]
    fn operator_edits_drive_build_and_runtime_commands() {
        let (dir, config) = fixture();
        let plan = image_plan(dir.path(), &config, "all").unwrap();
        assert_eq!(plan.len(), 2);
        assert!(plan[0].args.contains(&"BASE_IMAGE=registry.example/operator-base:custom".into()));
        assert_eq!(&plan[1].args[plan[1].args.len()-2..], &["btop", "fastfetch"]);
        let mut ran = Vec::new();
        execute_images(&plan, |c| { ran.push(c.target.clone()); Ok(()) }).unwrap();
        assert_eq!(ran, ["os", "os:runtime"]);
    }
    #[test]
    fn missing_dependencies_fail_and_prevent_later_targets() {
        let (dir, mut config) = fixture();
        let plan = image_plan(dir.path(), &config, "all").unwrap();
        let error = execute_images(&plan, |c| if c.target.ends_with(":runtime") { Err("btop missing".into()) } else { Ok(()) }).unwrap_err();
        assert!(error.contains("os:runtime failed: btop missing"));
        config["packages"]["mcp"]["verify_probes"] = toml::Value::Array(vec![]);
        assert!(image_plan(dir.path(), &config, "all").unwrap_err().contains("verify_probes"));
        config["build"]["images"]["os"]["containerfile"] = toml::Value::String("../outside".into());
        assert!(image_plan(dir.path(), &config, "all").unwrap_err().contains("inside"));
    }
}
