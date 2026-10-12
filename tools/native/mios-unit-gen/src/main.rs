// AI-hint: CLI driver for mios-unit-gen rendering systemd unit files from SSOT.
use mios_unit_gen::{
    drift_register, project, project_deployment, render_keybindings, render_units, DeploymentKind,
};
use std::env;
use std::fs;
use std::path::{Path, PathBuf};

/// Repo root: MIOS_ROOT when the caller sets it (the build and the drift-gate
/// both do), else relative to the crate, else the working directory. The
/// installed binary has no CARGO_MANIFEST_DIR, so it cannot be the only source.
fn repo_root() -> PathBuf {
    if let Ok(r) = env::var("MIOS_ROOT") {
        if !r.is_empty() {
            return PathBuf::from(r);
        }
    }
    match env::var("CARGO_MANIFEST_DIR") {
        Ok(d) => Path::new(&d).join("../../.."),
        Err(_) => PathBuf::from("."),
    }
}

fn read_ssot(root: &Path) -> String {
    let ssot_path = root.join("usr/share/mios/mios.toml");
    match fs::read_to_string(&ssot_path) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("mios-unit-gen: cannot read {}: {e}", ssot_path.display());
            std::process::exit(1);
        }
    }
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<String> = env::args().collect();
    if args.get(1).is_some_and(|arg| arg == "keybindings")
        && args.iter().any(|arg| arg == "--emit-json")
    {
        if args.len() != 5 || args[2] != "--from-json" || args[4] != "--emit-json" {
            return Err("keybindings --from-json FILE --emit-json required".into());
        }
        let doc: serde_json::Value = serde_json::from_str(&fs::read_to_string(&args[3])?)?;
        println!(
            "{}",
            serde_json::to_string(&render_keybindings(&toml::to_string(&doc)?)?)?
        );
        return Ok(());
    }
    for option in ["--root", "--toml", "--render"] {
        if let Some(i) = args.iter().position(|arg| arg == option) {
            if args.get(i + 1).is_none_or(|value| value.starts_with("--")) {
                return Err(format!("mios-unit-gen: {option} requires a value").into());
            }
        }
    }
    let root = args
        .windows(2)
        .find(|pair| pair[0] == "--root")
        .map(|pair| PathBuf::from(&pair[1]))
        .unwrap_or_else(repo_root);
    if args.iter().any(|arg| arg == "--list-projections") {
        println!("blade-dropins\nblade-karg\nuki-cmdline\ncockpit\nipa-enroll\nbootc-install\nkeybindings");
        return Ok(());
    }
    let deployment = match args.get(1).map(String::as_str) {
        Some("blade-dropins") => Some(DeploymentKind::BladeDropins),
        Some("blade-karg") => Some(DeploymentKind::BladeKarg),
        Some("uki-cmdline") => Some(DeploymentKind::UkiCmdline),
        Some("cockpit") => Some(DeploymentKind::Cockpit),
        Some("ipa-enroll") => Some(DeploymentKind::IpaEnroll),
        Some("bootc-install") => Some(DeploymentKind::BootcInstall),
        Some("keybindings") => Some(DeploymentKind::Keybindings),
        _ => None,
    };
    if let Some(kind) = deployment {
        let mut i = 2;
        while i < args.len() {
            match args[i].as_str() {
                "--root" | "--toml" => i += 2,
                "--check" => i += 1,
                other => {
                    return Err(
                        format!("mios-unit-gen: unknown projection option {other:?}").into(),
                    )
                }
            }
        }
        let toml = args
            .windows(2)
            .find(|pair| pair[0] == "--toml")
            .map(|pair| PathBuf::from(&pair[1]));
        let check = args.iter().any(|arg| arg == "--check");
        let count = project_deployment(&root, kind, check, toml.as_deref())?;
        println!(
            "[mios-unit-gen] {}: {count} projection(s) {}",
            args[1],
            if check { "match SSOT" } else { "written" }
        );
        return Ok(());
    }

    // Render one unit, or list what [units.*] covers. This is how a drift entry
    // is diagnosed and drained: `--render x.service | diff - usr/lib/systemd/system/x.service`.
    if args.iter().any(|a| a == "--list") {
        for name in render_units(&read_ssot(&root))?.keys() {
            println!("{name}");
        }
        return Ok(());
    }
    if let Some(i) = args.iter().position(|a| a == "--render") {
        let want = args.get(i + 1).map(String::as_str).unwrap_or("");
        let rendered = render_units(&read_ssot(&root))?;
        match rendered.get(want) {
            Some(body) => print!("{body}"),
            None => {
                eprintln!("mios-unit-gen: [units.\"{want}\"] is not declared in the SSOT");
                std::process::exit(1);
            }
        }
        return Ok(());
    }

    // A drift check must COMPARE. This one used to render into memory and
    // return; --selftest diffed the tree against a copy of itself.
    if args.iter().any(|a| a == "--check" || a == "--selftest") {
        let p = project(&root)?;
        let declared = p.faithful.len() + p.drifted.len() + p.missing.len();
        if declared == 0 {
            eprintln!("mios-unit-gen: rendered 0 units from [units.*] -- SSOT empty or unreadable");
            std::process::exit(1);
        }

        let register = drift_register(&read_ssot(&root))?;
        let unexpected: Vec<&String> = p.drifted.iter().filter(|f| !register.contains(f)).collect();
        // Shrink-only: an entry that no longer drifts must LEAVE the register,
        // or the register stops measuring anything and starts hiding the next one.
        let stale: Vec<&String> = register.iter().filter(|f| !p.drifted.contains(f)).collect();

        println!(
            "[mios-unit-gen] [units.*] declares {declared} of {} shipped units: {} faithful, \
             {} drifted (register: {}), {} missing on disk, {} undeclared.",
            declared + p.undeclared.len(),
            p.faithful.len(),
            p.drifted.len(),
            register.len(),
            p.missing.len(),
            p.undeclared.len()
        );

        if unexpected.is_empty() && stale.is_empty() && p.missing.is_empty() {
            return Ok(());
        }
        for f in &unexpected {
            eprintln!("  DRIFTED    {f}  (not in [unit_projection].drift)");
        }
        for f in &stale {
            eprintln!("  NO-LONGER  {f}  (drop it from [unit_projection].drift -- the register only shrinks)");
        }
        for f in &p.missing {
            eprintln!("  MISSING    {f}  ([units.*] declares it; no such file on disk)");
        }
        std::process::exit(1);
    }

    println!("mios-unit-gen: --check | --list | --render <unit> | blade-dropins | blade-karg | uki-cmdline | cockpit | ipa-enroll | bootc-install [--root DIR] [--toml FILE] [--check]");
    Ok(())
}
