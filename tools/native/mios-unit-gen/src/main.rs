// AI-hint: CLI driver for mios-unit-gen rendering systemd unit files from SSOT.
use mios_unit_gen::{
    drift_register, import_unit, project, project_deployment, project_user, projection_ceiling,
    projection_list, render_keybindings, render_units, render_user_units,
    strict_without_write_grant, unexpandable_placeholders, write_projection, DeploymentKind,
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
        for name in (if args.iter().any(|a| a == "--user") {
            render_user_units(&read_ssot(&root))?
        } else {
            render_units(&read_ssot(&root))?
        })
        .keys()
        {
            println!("{name}");
        }
        return Ok(());
    }
    if let Some(i) = args.iter().position(|a| a == "--render") {
        let want = args.get(i + 1).map(String::as_str).unwrap_or("");
        let rendered = if args.iter().any(|a| a == "--user") {
            render_user_units(&read_ssot(&root))?
        } else {
            render_units(&read_ssot(&root))?
        };
        match rendered.get(want) {
            Some(body) => print!("{body}"),
            None => {
                eprintln!("mios-unit-gen: [units.\"{want}\"] is not declared in the SSOT");
                std::process::exit(1);
            }
        }
        return Ok(());
    }

    // The inverse: emit the [units.*] declaration that renders a shipped unit,
    // proven by rendering it back. `--all` covers every undeclared unit plus
    // every registered (drifting) one -- the file on disk is what boots.
    if let Some(i) = args.iter().position(|a| a == "--import") {
        let user = args.iter().any(|a| a == "--user");
        let ssot = read_ssot(&root);
        let p = if user {
            project_user(&root)?
        } else {
            project(&root)?
        };
        let mut names: Vec<String> = args[i + 1..]
            .iter()
            .filter(|a| !a.starts_with("--"))
            .filter(|a| args.windows(2).all(|w| !(w[0] == "--root" && &w[1] == *a)))
            .cloned()
            .collect();
        if args.iter().any(|a| a == "--all") {
            names.extend(p.undeclared.iter().cloned());
            names.extend(p.drifted.iter().cloned());
            names.sort();
            names.dedup();
        }
        let dir = root.join(if user {
            "usr/lib/systemd/user"
        } else {
            "usr/lib/systemd/system"
        });
        let table = if user { "user_units" } else { "units" };
        let mut failed = 0;
        for name in &names {
            let body = fs::read_to_string(dir.join(name))?;
            match import_unit(&ssot, table, name, &body) {
                Ok((decl, changes)) => {
                    if changes {
                        eprintln!("  RESOLVES     {name}: its placeholders resolve from SSOT; --write rewrites the file");
                    }
                    print!("{decl}")
                }
                Err(e) => {
                    eprintln!("  NOT-IMPORTED {e}");
                    failed += 1;
                }
            }
        }
        eprintln!(
            "[mios-unit-gen] imported {} of {} unit(s) into [{table}.*]",
            names.len() - failed,
            names.len()
        );
        if failed > 0 {
            std::process::exit(2);
        }
        return Ok(());
    }

    // The generator half: write [units.*] (or [user_units.*] with --user) onto
    // the tree. `--check` is the gate that proves nobody edited the output.
    if args.iter().any(|a| a == "--write") {
        let user = args.iter().any(|a| a == "--user");
        let changed = write_projection(&root, user)?;
        for name in &changed {
            println!("  wrote {name}");
        }
        println!(
            "[mios-unit-gen] {} unit(s) written from [{}units.*]",
            changed.len(),
            if user { "user_" } else { "" }
        );
        return Ok(());
    }

    // A drift check must COMPARE. This one used to render into memory and
    // return; --selftest diffed the tree against a copy of itself.
    if args.iter().any(|a| a == "--check" || a == "--selftest") {
        let p = if args.iter().any(|a| a == "--user") {
            project_user(&root)?
        } else {
            project(&root)?
        };
        let declared = p.faithful.len() + p.drifted.len() + p.missing.len();
        if declared == 0 {
            eprintln!("mios-unit-gen: rendered 0 units from [units.*] -- SSOT empty or unreadable");
            std::process::exit(1);
        }

        let register = if args.iter().any(|a| a == "--user") {
            Vec::new()
        } else {
            drift_register(&read_ssot(&root))?
        };
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

        // Coverage ratchet: a shipped unit or drop-in the SSOT does not declare
        // is outside the projection entirely. The ceiling only comes down, so a
        // hand-authored unit fails here instead of widening the hole.
        let ceiling_key = if args.iter().any(|a| a == "--user") {
            "max_undeclared_user"
        } else {
            "max_undeclared"
        };
        let mut coverage_failed = false;
        match projection_ceiling(&read_ssot(&root), ceiling_key)? {
            None => {
                eprintln!("  UNBOUNDED  [unit_projection].{ceiling_key} is unset -- nothing stops an undeclared unit");
                coverage_failed = true;
            }
            Some(max) if p.undeclared.len() > max => {
                for f in &p.undeclared {
                    eprintln!("  UNDECLARED {f}  (ships without a [units.*] declaration)");
                }
                eprintln!(
                    "  CEILING    {} undeclared > [unit_projection].{ceiling_key} = {max}: declare the unit, do not raise the ceiling",
                    p.undeclared.len()
                );
                coverage_failed = true;
            }
            Some(max) if p.undeclared.len() < max => {
                eprintln!(
                    "  CEILING    {} undeclared < [unit_projection].{ceiling_key} = {max}: lower the ceiling so the ground is held",
                    p.undeclared.len()
                );
                coverage_failed = true;
            }
            Some(_) => {}
        }

        // XW51B8MG / 43B32AST at the source: a projected unit may not carry what
        // systemd leaves unexpanded -- `${X:-d}` anywhere, `${X}` outside Exec*=.
        let rendered = if args.iter().any(|a| a == "--user") {
            render_user_units(&read_ssot(&root))?
        } else {
            render_units(&read_ssot(&root))?
        };
        let mut placeholder_failed = false;
        for (name, body) in &rendered {
            for (line, text) in unexpandable_placeholders(body) {
                eprintln!("  UNEXPANDABLE {name}:{line}  {}  (systemd never expands this; declare it so the projection resolves it)", text.trim());
                placeholder_failed = true;
            }
        }

        // 6S2G69Q2: a ProtectSystem=strict unit granting no writable path is
        // listed deliberately in [unit_projection].strict_no_write[_user], or
        // it fails; a listed unit that gained its grant must leave the list.
        let list_key = if args.iter().any(|a| a == "--user") {
            "strict_no_write_user"
        } else {
            "strict_no_write"
        };
        let listed = projection_list(&read_ssot(&root), list_key)?;
        let strict = strict_without_write_grant(&rendered);
        let mut strict_failed = false;
        for u in strict.iter().filter(|u| !listed.contains(u)) {
            eprintln!("  NO-WRITE     {u}  (ProtectSystem=strict and no ReadWritePaths=/StateDirectory=/...: declare what it writes, or list it in [unit_projection].{list_key})");
            strict_failed = true;
        }
        for u in listed.iter().filter(|u| !strict.contains(u)) {
            eprintln!("  NO-LONGER    {u}  (grants a writable path or is no longer strict: drop it from [unit_projection].{list_key})");
            strict_failed = true;
        }

        if unexpected.is_empty()
            && stale.is_empty()
            && !strict_failed
            && p.missing.is_empty()
            && !coverage_failed
            && !placeholder_failed
        {
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

    println!("mios-unit-gen: --check | --write | --import <unit>|--all | --list | --render <unit> [--user] | blade-dropins | blade-karg | uki-cmdline | cockpit | ipa-enroll | bootc-install [--root DIR] [--toml FILE] [--check]");
    Ok(())
}
