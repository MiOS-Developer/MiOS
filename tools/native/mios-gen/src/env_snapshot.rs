// AI-hint: Native env-baseline projection (usr/share/mios/reference/env-baseline.txt): the MIOS_* environment a sealed login shell gets from userenv.sh over the vendor tiers of a tree, one escaped KEY=value per line, byte-sorted -- the Rust port of the former bash body of usr/libexec/mios/mios-env-snapshot.
// AI-related: usr/libexec/mios/mios-env-snapshot, usr/lib/mios/userenv.sh, tools/native/mios-resolver/src/emit.rs (emit_shell::shell_exports), usr/share/mios/mios.toml [generation.sync], automation/98-drift-checks.sh (check_resolved_env_lossless), tests/golden/README.md
// AI-functions: snapshot, render, run
//
// What the bash adapter computed, restated: `env -i` with a fixed, sealed
// environment; `source userenv.sh`, whose native tier evals
// `mios-resolver --emit=shell`; `env -0`; keep MIOS_*; escape `\` then newline;
// drop the location-derived names; `LC_ALL=C sort`. Every step is reproduced
// here in process, from the same resolver library userenv.sh executes, so the
// two cannot drift -- and the snapshot no longer depends on which mios-resolver
// happened to be first on PATH (the adapter's PATH was inherited).
//
// One deliberate difference: only the VENDOR tiers are read. The adapter left
// MIOS_HOST_TOML unset, so the resolver fell back to <root>/etc/mios/mios.toml
// and <root>/etc/mios/mios.d -- which on a deployed host, where the root is
// `/`, is the operator's live admin tier. A committed baseline must depend on
// the tree alone; tests/test-env-snapshot-native-parity.py plants a host
// fragment to keep that difference visible, and replays the frozen bash body
// against this port for byte parity everywhere else.

use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};

/// Names whose value is where the tree happens to sit, or the resolver's own
/// inputs: they differ between a contributor checkout and a CI runner.
const LOCATION_NAMES: &[&str] = &[
    "MIOS_ROOT",
    "MIOS_TOML_ROOT",
    "MIOS_VENDOR_TOML",
    "MIOS_CONFIG_DIR",
    "MIOS_USER_TOML",
    "MIOS_USER_TOML_D",
    "MIOS_HOST_TOML_D",
    "MIOS_REPO_URL",
    "MIOS_REPO_ROOT",
    "MIOS_REPO_DIR",
];

/// The sealed environment the adapter handed `env -i`. MIOS_* members are
/// resolver inputs AND exported names, exactly as there.
fn sealed_env(root: &Path, vendor: &Path) -> BTreeMap<String, String> {
    let root = root.to_string_lossy().into_owned();
    BTreeMap::from([
        ("HOME".into(), "/nonexistent".into()),
        ("XDG_CONFIG_HOME".into(), "/nonexistent/.config".into()),
        ("MIOS_ROOT".into(), root.clone()),
        ("MIOS_TOML_ROOT".into(), root),
        ("MIOS_VENDOR_TOML".into(), vendor.to_string_lossy().into_owned()),
        ("MIOS_USER_TOML".into(), "/nonexistent/user.toml".into()),
        ("MIOS_USER_TOML_D".into(), "/nonexistent/user.d".into()),
    ])
}

/// `${name:-default}`: an empty value counts as unset.
fn or_default<'a>(env: &'a BTreeMap<String, String>, name: &str, default: &'a str) -> &'a str {
    env.get(name).map(String::as_str).filter(|v| !v.is_empty()).unwrap_or(default)
}

/// The exported MIOS_* environment after `source userenv.sh`, values raw.
pub fn snapshot(root: &Path, vendor: &Path) -> Result<BTreeMap<String, String>, String> {
    if !vendor.is_file() {
        return Err(format!("vendor SSOT not found: {}", vendor.display()));
    }
    let mut layers: Vec<PathBuf> = vec![vendor.to_path_buf()];
    layers.extend(mios_resolver::layers::fragments(&root.join("usr/lib/mios/mios.d")));
    let mut merged = mios_resolver::layers::merge_layer_files(&layers).map_err(|e| e.to_string())?;
    mios_resolver::ports::derive_ports(&mut merged);

    let sealed = sealed_env(root, vendor);
    mios_resolver::names::overlay_inputs(&mut merged, |key| sealed.get(key).cloned())?;
    let stack_offset = mios_resolver::stack_offset_of(&merged);
    let exports = mios_resolver::emit_shell::shell_exports(&merged, stack_offset);

    // The shell environment: the sealed inputs, then every `export` the eval ran.
    let mut env: BTreeMap<String, String> = sealed;
    for (name, value) in exports {
        // userenv.sh strips every CR from the export block before eval.
        env.insert(name, value.replace('\r', ""));
    }
    // userenv.sh's own derivation, after the resolver's.
    let bind = match or_default(&env, "MIOS_PGVECTOR_LISTEN_LOOPBACK", "true") {
        "false" | "False" | "FALSE" | "0" | "no" | "off" => or_default(&env, "MIOS_NETWORK_BIND_ALL", "0.0.0.0"),
        _ => or_default(&env, "MIOS_NETWORK_LOOPBACK", "127.0.0.1"),
    }
    .to_string();
    env.insert("MIOS_PG_BIND_ADDR".into(), bind);
    env.retain(|name, _| name.starts_with("MIOS_"));
    Ok(env)
}

/// One `KEY=value` line per name: `\` doubled, then each newline written `\n`
/// (reversible), location names dropped, lines in byte order.
pub fn render(env: &BTreeMap<String, String>) -> String {
    let mut lines: Vec<String> = env
        .iter()
        .filter(|(name, _)| !LOCATION_NAMES.contains(&name.as_str()))
        .map(|(name, value)| format!("{name}={}", value.replace('\\', "\\\\").replace('\n', "\\n")))
        .collect();
    lines.sort_unstable_by(|a, b| a.as_bytes().cmp(b.as_bytes()));
    let mut out = lines.join("\n");
    if !out.is_empty() {
        out.push('\n');
    }
    out
}

/// `mios-gen env-snapshot`: print the snapshot, write it with `out`, or with
/// `check` compare it to a committed file without writing anything.
pub fn run(root: &Path, vendor: Option<&Path>, out: Option<&Path>, check: Option<&Path>) -> Result<String, String> {
    let vendor = vendor.map(Path::to_path_buf).unwrap_or_else(|| root.join("usr/share/mios/mios.toml"));
    let body = render(&snapshot(root, &vendor)?);
    if body.is_empty() {
        return Err("environment snapshot produced no values".into());
    }
    if let Some(path) = check {
        let committed = fs::read(path).map_err(|e| format!("cannot read {}: {e}", path.display()))?;
        if committed != body.as_bytes() {
            return Err(format!("{} is stale: it differs from a fresh environment snapshot", path.display()));
        }
        return Ok(format!("{} matches the resolved environment ({} values)\n", path.display(), body.lines().count()));
    }
    if let Some(path) = out {
        crate::greeter::write_atomic(path, &body)?;
        return Ok(String::new());
    }
    Ok(body)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tree(vendor: &str) -> tempfile::TempDir {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("usr/share/mios/mios.toml");
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, vendor).unwrap();
        dir
    }

    #[test]
    fn values_escape_reversibly_and_sort_as_bytes() {
        let dir = tree("[a]\nmulti = \"x\\ny\"\nslash = 'C:\\n'\n[a_b]\nk = 1\n");
        let body = run(dir.path(), None, None, None).unwrap();
        let lines: Vec<&str> = body.lines().collect();
        assert!(lines.contains(&"MIOS_A_MULTI=x\\ny"), "{body}");
        // A literal backslash-n stays distinguishable from a newline.
        assert!(lines.contains(&"MIOS_A_SLASH=C:\\\\n"), "{body}");
        let mut sorted = lines.clone();
        sorted.sort_by(|a, b| a.as_bytes().cmp(b.as_bytes()));
        assert_eq!(lines, sorted);
        assert!(lines.iter().all(|l| l.starts_with("MIOS_")));
        assert!(!lines.iter().any(|l| l.starts_with("MIOS_ROOT=") || l.starts_with("MIOS_VENDOR_TOML=")));
    }

    #[test]
    fn pg_bind_follows_userenv_sh() {
        let open = tree("[pgvector]\nlisten_loopback = false\n[network]\nbind_all = \"10.0.0.1\"\n");
        assert!(run(open.path(), None, None, None).unwrap().contains("MIOS_PG_BIND_ADDR=10.0.0.1\n"));
        let closed = tree("[pgvector]\nlisten_loopback = true\n");
        assert!(run(closed.path(), None, None, None).unwrap().contains("MIOS_PG_BIND_ADDR=127.0.0.1\n"));
    }

    #[test]
    fn only_the_vendor_tiers_are_read() {
        let dir = tree("[a]\nv = \"vendor\"\n");
        let frag = dir.path().join("usr/lib/mios/mios.d/10-x.toml");
        fs::create_dir_all(frag.parent().unwrap()).unwrap();
        fs::write(&frag, "[a]\nf = \"fragment\"\n").unwrap();
        fs::write(frag.with_file_name(".hidden.toml"), "[a]\nf = \"hidden\"\n").unwrap();
        let host = dir.path().join("etc/mios/mios.d/70-host.toml");
        fs::create_dir_all(host.parent().unwrap()).unwrap();
        fs::write(&host, "[a]\nv = \"host\"\n").unwrap();
        let body = run(dir.path(), None, None, None).unwrap();
        assert!(body.contains("MIOS_A_V=vendor\n") && body.contains("MIOS_A_F=fragment\n"), "{body}");
    }

    #[test]
    fn check_reports_a_stale_baseline_and_writes_nothing() {
        let dir = tree("[a]\nv = 1\n");
        let base = dir.path().join("baseline.txt");
        run(dir.path(), None, Some(&base), None).unwrap();
        assert!(run(dir.path(), None, None, Some(&base)).is_ok());
        fs::write(&base, "MIOS_A_V=2\n").unwrap();
        let err = run(dir.path(), None, None, Some(&base)).unwrap_err();
        assert!(err.contains("is stale"), "{err}");
        assert_eq!(fs::read_to_string(&base).unwrap(), "MIOS_A_V=2\n");
        assert!(run(&dir.path().join("absent"), None, None, None).unwrap_err().contains("vendor SSOT not found"));
    }
}
