// AI-hint: Rust bake-plan generator (AGY-139 / Law 14). Projects plan.d/*.list and bound-images.tsv from mios.toml SSOT, and resolves floating refs at bake time (latest-image / latest-git).
mod latest;

use regex::Regex;
use std::collections::{BTreeMap, BTreeSet};
use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::process;
use toml::Value;
use walkdir::WalkDir;

fn quadlet_paths(dir: &Path, max_depth: usize) -> Vec<PathBuf> {
    let mut paths: Vec<_> = WalkDir::new(dir)
        .max_depth(max_depth)
        .into_iter()
        .filter_map(Result::ok)
        .filter(|entry| entry.file_type().is_file())
        .map(|entry| entry.into_path())
        .filter(|p| {
            p.extension()
                .is_some_and(|ext| ext == "container" || ext == "image")
        })
        .collect();
    paths.sort();
    paths
}

fn get_root() -> PathBuf {
    if let Ok(r) = env::var("MIOS_ROOT") {
        return PathBuf::from(r);
    }
    let current_exe = env::current_exe().unwrap_or_default();
    let mut p = current_exe.as_path();
    while let Some(parent) = p.parent() {
        if parent.join("usr/share/mios/mios.toml").is_file() {
            return parent.to_path_buf();
        }
        p = parent;
    }
    PathBuf::from(".")
}

/// Resolve `${VAR}` in an `Image=` line: env, then SSOT, then sidecars, then
/// the literal fallback. The SSOT layer is the one this binary lacked (T-1057).
fn resolve_image_val(
    val: &str,
    sidecars: &BTreeMap<String, String>,
    ssot: &BTreeMap<String, String>,
) -> String {
    if val.is_empty() {
        return String::new();
    }
    // Ported from the miosd mirror before deleting it: these say WHY the panic
    // is unreachable rather than just panicking.
    let var_fallback_re = Regex::new(r"\$\{([A-Za-z0-9_]+):-([^}]*)\}").expect(
        "bake_plan: ${VAR:-default} pattern is a compile-time literal and must be valid regex",
    );
    let var_simple_re = Regex::new(r"\$\{([A-Za-z0-9_]+)\}")
        .expect("bake_plan: ${VAR} pattern is a compile-time literal and must be valid regex");

    let s1 = var_fallback_re.replace_all(val, |caps: &regex::Captures| {
        let var_name = &caps[1];
        let fallback = &caps[2];
        if let Ok(v) = env::var(var_name) {
            return v;
        }
        if let Some(v) = ssot.get(var_name) {
            if !v.is_empty() {
                return v.clone();
            }
        }
        if var_name.starts_with("MIOS_") && var_name.ends_with("_IMAGE") {
            let key = var_name[5..var_name.len() - 6].to_lowercase();
            if let Some(sc) = sidecars.get(&key) {
                return sc.clone();
            }
        }
        fallback.to_string()
    });

    let s2 = var_simple_re.replace_all(&s1, |caps: &regex::Captures| {
        let var_name = &caps[1];
        if let Ok(v) = env::var(var_name) {
            return v;
        }
        if let Some(v) = ssot.get(var_name) {
            if !v.is_empty() {
                return v.clone();
            }
        }
        if var_name.starts_with("MIOS_") && var_name.ends_with("_IMAGE") {
            let key = var_name[5..var_name.len() - 6].to_lowercase();
            if let Some(sc) = sidecars.get(&key) {
                return sc.clone();
            }
        }
        caps[0].to_string()
    });

    s2.trim().to_string()
}

/// Every `Image=` an engine overlay declares, `[<kind>s.<name>.engine.<engine>.<Section>].Image`.
/// mios-gen renders only the overlay its selector names (the heavy lane: vLLM or
/// SGLang), so an unselected engine's image has no Quadlet. It stays in
/// [build.bake].core so switching engines is one SSOT edit, never two.
fn engine_overlay_images(parsed: &Value) -> Vec<String> {
    let mut out = Vec::new();
    for kind in ["containers", "images"] {
        let specs = parsed.get(kind).and_then(Value::as_table);
        for spec in specs.into_iter().flat_map(|t| t.values()) {
            let Some(engines) = spec.get("engine").and_then(Value::as_table) else {
                continue;
            };
            for overlay in engines.values().filter_map(Value::as_table) {
                for section in overlay.values().filter_map(Value::as_table) {
                    if let Some(img) = section.get("Image").and_then(Value::as_str) {
                        out.push(img.to_string());
                    }
                }
            }
        }
    }
    out
}

fn classify(img: &str, groups: &[String], group_members: &BTreeMap<String, Vec<String>>) -> String {
    for g in groups {
        if let Some(members) = group_members.get(g) {
            for tok in members {
                if !tok.is_empty() && img.contains(tok) {
                    return g.clone();
                }
            }
        }
    }
    groups
        .last()
        .cloned()
        .unwrap_or_else(|| "extra".to_string())
}

fn main() {
    let args: Vec<String> = env::args().collect();
    let root = get_root();
    if let Some(code) = latest::dispatch(&args, &root) {
        process::exit(code);
    }
    let check = args.iter().any(|a| a == "--check");

    let toml_path = env::var("MIOS_TOML")
        .map(PathBuf::from)
        .unwrap_or_else(|_| root.join("usr/share/mios/mios.toml"));
    let out_dir = env::var("MIOS_PLAN_OUT")
        .map(PathBuf::from)
        .unwrap_or_else(|_| root.join("usr/lib/mios/bake/plan.d"));

    // The same canonical MIOS_* map every other consumer resolves through.
    // Degrading to an empty map is deliberate and safe: an unresolved
    // placeholder is then REPORTED by name below rather than silently dropping
    // the Quadlet that carries it.
    let ssot_vars: BTreeMap<String, String> = {
        let fig = mios_resolver::layers::create_figment(Some(root.as_path()));
        match fig.extract::<toml::Value>() {
            Ok(mut merged) => {
                mios_resolver::db_overlay::maybe_apply_db_overlay(&mut merged, false);
                let offset = merged
                    .get("ports")
                    .and_then(|p| p.get("stack_id"))
                    .and_then(|v| {
                        v.as_integer()
                            .or_else(|| v.as_str().and_then(|s| s.parse::<i64>().ok()))
                    })
                    .map(|id| id * 10000)
                    .unwrap_or(0);
                mios_resolver::emit::build_exports_map(&merged, offset)
            }
            Err(e) => {
                eprintln!("[bake-plan-gen] WARNING: SSOT exports unavailable ({e}); floated tags will be reported unresolved");
                BTreeMap::new()
            }
        }
    };

    let content = match fs::read_to_string(&toml_path) {
        Ok(c) => c,
        Err(e) => {
            eprintln!(
                "[bake-plan-gen] ERROR: cannot read {}: {}",
                toml_path.display(),
                e
            );
            process::exit(1);
        }
    };

    let parsed: Value = match content.parse() {
        Ok(v) => v,
        Err(e) => {
            eprintln!(
                "[bake-plan-gen] ERROR: cannot parse {}: {}",
                toml_path.display(),
                e
            );
            process::exit(1);
        }
    };

    let build_bake = parsed.get("build").and_then(|b| b.get("bake"));
    let core: BTreeSet<String> = build_bake
        .and_then(|b| b.get("core"))
        .and_then(|c| c.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|v| v.as_str().map(String::from))
                .collect()
        })
        .unwrap_or_default();

    let groups: Vec<String> = build_bake
        .and_then(|b| b.get("groups"))
        .and_then(|g| g.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|v| v.as_str().map(String::from))
                .collect()
        })
        .unwrap_or_else(|| {
            // No built-in group list: a default here would be a second SSOT.
            eprintln!("[bake-plan-gen] ERROR: [build.bake].groups is missing in mios.toml");
            process::exit(1);
        });

    let mut group_members: BTreeMap<String, Vec<String>> = BTreeMap::new();
    if let Some(gm) = build_bake
        .and_then(|b| b.get("group_members"))
        .and_then(|m| m.as_table())
    {
        for (k, v) in gm {
            if let Some(arr) = v.as_array() {
                let members: Vec<String> = arr
                    .iter()
                    .filter_map(|s| s.as_str().map(String::from))
                    .collect();
                group_members.insert(k.clone(), members);
            }
        }
    }

    let firstboot_tokens: Vec<String> = build_bake
        .and_then(|b| b.get("firstboot_tokens"))
        .and_then(|t| t.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|v| v.as_str().map(String::from))
                .collect()
        })
        .unwrap_or_default();

    let is_firstboot = |img: &str| -> bool {
        firstboot_tokens
            .iter()
            .any(|tok| !tok.is_empty() && img.contains(tok))
    };

    let mut enabled_map: BTreeMap<String, bool> = BTreeMap::new();
    if let Some(q) = parsed
        .get("quadlets")
        .and_then(|q| q.get("enable"))
        .and_then(|e| e.as_table())
    {
        for (k, v) in q {
            if let Some(b) = v.as_bool() {
                enabled_map.insert(k.clone(), b);
            }
        }
    }

    let mut sidecars: BTreeMap<String, String> = BTreeMap::new();
    if let Some(sc) = parsed
        .get("image")
        .and_then(|i| i.get("sidecars"))
        .and_then(|s| s.as_table())
    {
        for (k, v) in sc {
            if let Some(s) = v.as_str() {
                sidecars.insert(k.to_lowercase(), s.to_string());
            }
        }
    }

    let quadlet_dir = root.join("usr/share/containers/systemd");
    let mut images_to_bake: Vec<(String, String)> = Vec::new();
    let mut unresolved: Vec<(String, String)> = Vec::new();
    let max_depth = parsed
        .get("build")
        .and_then(|b| b.get("quadlet_render"))
        .and_then(|q| q.get("max_depth"))
        .and_then(Value::as_integer)
        .and_then(|n| usize::try_from(n).ok())
        .unwrap_or(2);

    if quadlet_dir.is_dir() {
        for path in quadlet_paths(&quadlet_dir, max_depth) {
            let base_name = path
                .file_stem()
                .unwrap_or_default()
                .to_string_lossy()
                .to_string();
            let mut img = String::new();
            if let Ok(fc) = fs::read_to_string(&path) {
                for line in fc.lines() {
                    let trimmed = line.trim();
                    if let Some(stripped) = trimmed.strip_prefix("Image=") {
                        img = stripped.trim().to_string();
                        break;
                    }
                }
            }
            if img.is_empty() {
                continue;
            }
            let resolved = resolve_image_val(&img, &sidecars, &ssot_vars);
            if resolved.is_empty() {
                continue;
            }
            if resolved.contains('$') {
                // Dropping this quietly is how a floated tag became "core
                // image is not referenced by any Quadlet". Name the variable
                // that did not resolve instead.
                unresolved.push((base_name.clone(), img.clone()));
                continue;
            }
            let first = resolved.split('/').next().unwrap_or("");
            if first == "localhost" {
                continue;
            }
            let is_core = core.contains(&resolved);
            if is_core || enabled_map.get(&base_name) != Some(&false) {
                images_to_bake.push((resolved, base_name));
            }
        }
    }

    // T-1039. The Quadlet scan above skips every `localhost/*` image, because a
    // locally built image is not discovered from a Quadlet's Image= line. The
    // python generator then RE-ADDS every localhost image declared in `core`;
    // this port never did, so every `localhost/*` entry of `[build.bake].core`
    // -- the mios-sys and mios-cuda bases and the webtools crawl4ai and
    // firecrawl images -- was dropped from the plan lists. Law 12: an image
    // missing from the plan is an image the bake does not carry.
    for core_img in &core {
        if core_img.starts_with("localhost/")
            && !images_to_bake.iter().any(|(img, _)| img == core_img)
        {
            images_to_bake.push((core_img.clone(), "core-localhost".to_string()));
        }
    }

    let mut group_lists: BTreeMap<String, Vec<String>> = BTreeMap::new();
    for g in &groups {
        group_lists.insert(g.clone(), Vec::new());
    }

    let mut firstboot_images: Vec<String> = Vec::new();
    for (img, _base_name) in &images_to_bake {
        if is_firstboot(img) {
            if !firstboot_images.contains(img) {
                firstboot_images.push(img.clone());
            }
            continue;
        }
        let g = classify(img, &groups, &group_members);
        if let Some(list) = group_lists.get_mut(&g) {
            if !list.contains(img) {
                list.push(img.clone());
            }
        }
    }

    let mut errors: Vec<String> = Vec::new();

    // Reported BEFORE the core/Quadlet set difference below, because an
    // unresolved placeholder is the cause and "not referenced by any Quadlet"
    // is only its downstream symptom. Naming the symptom first sent readers to
    // the SSOT to add an image that was already there.
    let unresolved_var_re = Regex::new(r"\$\{([A-Za-z0-9_]+)").ok();
    for (quadlet, raw) in &unresolved {
        let var = unresolved_var_re
            .as_ref()
            .and_then(|re| re.captures(raw).map(|c| c[1].to_string()))
            .unwrap_or_else(|| raw.clone());
        errors.push(format!(
            "Quadlet '{quadlet}' floats its image tag on ${{{var}}}, which resolved to nothing \
             -- the variable is unset in the environment and absent from the SSOT exports, so \
             the image reference '{raw}' could not be compared against [build.bake].core"
        ));
    }
    for tok in &firstboot_tokens {
        if !tok.is_empty() && !core.iter().any(|img| img.contains(tok)) {
            errors.push(format!(
                "Firstboot token '{}' matches no image in core bake list",
                tok
            ));
        }
    }
    for img in &firstboot_images {
        if !core.contains(img) {
            errors.push(format!(
                "Firstboot image '{}' is missing from core bake list",
                img
            ));
        }
    }

    let discovered_non_localhost: BTreeSet<String> = images_to_bake
        .iter()
        .filter(|(img, _)| !img.starts_with("localhost/"))
        .map(|(img, _)| img.clone())
        .collect();
    let core_non_localhost: BTreeSet<String> = core
        .iter()
        .filter(|img| !img.starts_with("localhost/"))
        .cloned()
        .collect();

    for img in discovered_non_localhost.difference(&core_non_localhost) {
        errors.push(format!(
            "Quadlet image '{}' is missing from [build.bake].core",
            img
        ));
    }
    let mut engine_images: BTreeSet<String> = BTreeSet::new();
    for raw in engine_overlay_images(&parsed) {
        let img = resolve_image_val(&raw, &sidecars, &ssot_vars);
        if img.is_empty() || img.starts_with("localhost/") {
            continue;
        }
        if !core.contains(&img) {
            errors.push(format!(
                "Engine overlay image '{img}' is missing from [build.bake].core, so selecting that engine would fail the bake"
            ));
        }
        engine_images.insert(img);
    }
    for img in core_non_localhost.difference(&discovered_non_localhost) {
        if engine_images.contains(img) {
            continue; // an unselected engine's image: referenced by its overlay
        }
        errors.push(format!(
            "Core image '{}' is not referenced by any Quadlet",
            img
        ));
    }

    for img in &core {
        let first = img.split('/').next().unwrap_or("");
        if !(first.contains('.') || first.contains(':') || first == "localhost") {
            errors.push(format!(
                "Core image '{}' is not fully-qualified (missing registry prefix)",
                img
            ));
        }
    }

    for (img, base_name) in &images_to_bake {
        if img.starts_with("systemd-") {
            continue;
        }
        let first = img.split('/').next().unwrap_or("");
        if !(first.contains('.') || first.contains(':') || first == "localhost") {
            errors.push(format!(
                "Referenced image '{}' in {} is not fully-qualified",
                img, base_name
            ));
        }
    }

    if !errors.is_empty() {
        for err in &errors {
            eprintln!("[bake-plan-gen] VALIDATION ERROR: {}", err);
        }
        process::exit(2);
    }

    if !check {
        let _ = fs::create_dir_all(&out_dir);
        if let Ok(entries) = fs::read_dir(&out_dir) {
            for entry in entries.flatten() {
                if entry.path().extension().is_some_and(|e| e == "list") {
                    let _ = fs::remove_file(entry.path());
                }
            }
        }

        let sbom_dir = env::var("MIOS_SBOM_DIR")
            .map(PathBuf::from)
            .unwrap_or_else(|_| root.join("usr/share/mios/artifacts/sbom"));
        let _ = fs::create_dir_all(&sbom_dir);
        let sbom_file = sbom_dir.join("bound-images.tsv");

        let mut existing_digests: BTreeMap<String, String> = BTreeMap::new();
        let mut existing_sizes: BTreeMap<String, String> = BTreeMap::new();
        if sbom_file.is_file() {
            if let Ok(c) = fs::read_to_string(&sbom_file) {
                for line in c.lines() {
                    let parts: Vec<&str> = line.trim().split('\t').collect();
                    if parts.len() >= 3 && parts[0] != "image" {
                        existing_digests.insert(parts[0].to_string(), parts[1].to_string());
                        if parts.len() >= 4 {
                            existing_sizes.insert(parts[0].to_string(), parts[3].to_string());
                        }
                    }
                }
            }
        }

        let mut seen_images: BTreeSet<String> = BTreeSet::new();
        // T-1039: the size_gb column. Omitting it made `drift-checks.py
        // bake-budget` print "bound-images.tsv missing size_gb column" -- and
        // exit 0, so the day-0 size budget was never actually computed.
        let mut sbom_content = String::from("image\tdigest\tgroup\tsize_gb\n");
        for (base_img, grp) in [
            ("localhost/mios-sys:latest", "sys"),
            ("localhost/mios-cuda:latest", "cuda"),
        ] {
            let digest = existing_digests
                .get(base_img)
                .cloned()
                .unwrap_or_else(|| "local".to_string());
            let size = existing_sizes.get(base_img).cloned().unwrap_or_else(|| {
                if grp == "sys" {
                    "2.5".to_string()
                } else {
                    "4.0".to_string()
                }
            });
            sbom_content.push_str(&format!("{}\t{}\t{}\t{}\n", base_img, digest, grp, size));
            seen_images.insert(base_img.to_string());
        }

        for (img, _base_name) in &images_to_bake {
            if !seen_images.contains(img) {
                let g = classify(img, &groups, &group_members);
                let digest = existing_digests
                    .get(img)
                    .cloned()
                    .unwrap_or_else(|| "local".to_string());
                let size = existing_sizes
                    .get(img)
                    .cloned()
                    .unwrap_or_else(|| "1.0".to_string());
                sbom_content.push_str(&format!("{}\t{}\t{}\t{}\n", img, digest, g, size));
                seen_images.insert(img.clone());
            }
        }

        // A discarded write result under an unconditional "wrote" line is the
        // Swallowed Failure shape this audit found in five other stages.
        if let Err(e) = fs::write(&sbom_file, sbom_content) {
            eprintln!(
                "[bake-plan-gen] ERROR: cannot write {}: {}",
                sbom_file.display(),
                e
            );
            std::process::exit(1);
        }
        println!("[bake-plan-gen] wrote {}", sbom_file.display());
    }

    let mut drift_detected = false;

    for (idx, g) in groups.iter().enumerate() {
        let prefix = format!("{:02}", idx + 1);
        let plan_file = out_dir.join(format!("{}-{}.list", prefix, g));
        let list = group_lists.get(g).cloned().unwrap_or_default();
        let mut content = String::new();
        for img in &list {
            content.push_str(img);
            content.push('\n');
        }

        if check {
            let cur = fs::read_to_string(&plan_file).unwrap_or_default();
            if cur != content {
                eprintln!(
                    "[bake-plan-gen] DRIFT: {} does not match projected plan",
                    plan_file.display()
                );
                drift_detected = true;
            }
        } else {
            let _ = fs::write(&plan_file, &content);
            println!("[bake-plan-gen] wrote {}", plan_file.display());
        }
    }

    let fb_file = out_dir.join("firstboot.list");
    let mut fb_content = String::new();
    for img in &firstboot_images {
        fb_content.push_str(img);
        fb_content.push('\n');
    }

    if check {
        let cur_fb = fs::read_to_string(&fb_file).unwrap_or_default();
        if cur_fb != fb_content {
            eprintln!(
                "[bake-plan-gen] DRIFT: {} does not match projected plan",
                fb_file.display()
            );
            drift_detected = true;
        }
    } else {
        let _ = fs::write(&fb_file, &fb_content);
        println!("[bake-plan-gen] wrote {}", fb_file.display());
    }

    if drift_detected {
        process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn user_quadlets_participate_at_the_declared_discovery_depth() {
        let dir = env::temp_dir().join(format!(
            "mios-bake-plan-{}-{}",
            process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap_or_default()
                .as_nanos()
        ));
        assert!(fs::create_dir_all(dir.join("users")).is_ok());
        let system = dir.join("system.container");
        let user = dir.join("users/sunshine.container");
        assert!(fs::write(&system, "[Container]\n").is_ok());
        assert!(fs::write(&user, "[Container]\n").is_ok());
        assert_eq!(quadlet_paths(&dir, 1), vec![system.clone()]);
        assert_eq!(quadlet_paths(&dir, 2), vec![system, user]);
        assert!(fs::remove_dir_all(dir).is_ok());
    }

    #[test]
    fn test_resolve_image_val() {
        let sidecars = BTreeMap::new();
        let ssot = BTreeMap::new();
        assert_eq!(
            resolve_image_val("ghcr.io/org/app:latest", &sidecars, &ssot),
            "ghcr.io/org/app:latest"
        );
        assert_eq!(
            resolve_image_val(
                "${MIOS_CUSTOM_IMAGE:-docker.io/library/redis:alpine}",
                &sidecars,
                &ssot
            ),
            "docker.io/library/redis:alpine"
        );
    }

    /// The defect this layer exists for: a floated tag with no env var and no
    /// sidecar must come from the SSOT map, not survive as a literal.
    #[test]
    fn a_floated_tag_resolves_from_ssot_when_the_environment_is_bare() {
        let sidecars = BTreeMap::new();
        let mut ssot = BTreeMap::new();
        let var_name = "MIOS_VERSION_CEPH_TEST_PROBE";
        unsafe { env::remove_var(var_name) };
        ssot.insert(var_name.to_string(), "v19".to_string());
        assert_eq!(
            resolve_image_val(
                &format!("quay.io/ceph/ceph:${{{var_name}}}"),
                &sidecars,
                &ssot
            ),
            "quay.io/ceph/ceph:v19"
        );
        // Absent from SSOT too: the placeholder SURVIVES so the caller can name
        // the variable, rather than being silently dropped.
        let empty = BTreeMap::new();
        assert!(resolve_image_val(
            &format!("quay.io/ceph/ceph:${{{var_name}}}"),
            &sidecars,
            &empty
        )
        .contains('$'));
    }

    #[test]
    fn the_environment_still_outranks_the_ssot() {
        let sidecars = BTreeMap::new();
        let mut ssot = BTreeMap::new();
        ssot.insert(
            "MIOS_VERSION_PARITY_PROBE".to_string(),
            "from-ssot".to_string(),
        );
        // SAFETY: single-threaded test process, and the var is unique to it.
        unsafe { env::set_var("MIOS_VERSION_PARITY_PROBE", "from-env") };
        let got = resolve_image_val("x/y:${MIOS_VERSION_PARITY_PROBE}", &sidecars, &ssot);
        unsafe { env::remove_var("MIOS_VERSION_PARITY_PROBE") };
        assert_eq!(got, "x/y:from-env");
    }

    #[test]
    fn every_engine_overlay_image_is_collected_selected_or_not() {
        let parsed: Value = toml::from_str(
            r#"
            [containers.lane.Container]
            Image = "ignored/base:1"
            [containers.lane.engine]
            select = "ai.heavy_engine"
            [containers.lane.engine.vllm.Container]
            Image = "${MIOS_VLLM_IMAGE:-docker.io/vllm/vllm-openai:latest}"
            [containers.lane.engine.sglang.Container]
            Image = "docker.io/lmsysorg/sglang:latest"
            [images.lane.engine.sglang.Image]
            Image = "docker.io/lmsysorg/sglang:latest"
            [containers.plain.Container]
            Image = "docker.io/library/redis:alpine"
        "#,
        )
        .unwrap();
        let mut got = engine_overlay_images(&parsed);
        got.sort();
        assert_eq!(
            got,
            vec![
                "${MIOS_VLLM_IMAGE:-docker.io/vllm/vllm-openai:latest}",
                "docker.io/lmsysorg/sglang:latest",
                "docker.io/lmsysorg/sglang:latest",
            ]
        );
        assert!(engine_overlay_images(&toml::from_str("[containers]\n").unwrap()).is_empty());
    }

    #[test]
    fn test_classify() {
        let groups = vec![
            "vllm".to_string(),
            "sglang".to_string(),
            "extra".to_string(),
        ];
        let mut members = BTreeMap::new();
        members.insert("vllm".to_string(), vec!["vllm".to_string()]);
        members.insert("sglang".to_string(), vec!["sglang".to_string()]);

        assert_eq!(
            classify("quay.io/vllm/vllm:v0.6.0", &groups, &members),
            "vllm"
        );
        assert_eq!(
            classify("docker.io/sglang:latest", &groups, &members),
            "sglang"
        );
        assert_eq!(classify("docker.io/other:1.0", &groups, &members), "extra");
    }
}
