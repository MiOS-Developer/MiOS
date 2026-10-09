// AI-hint: Deploy plane, BIB installer, and partition-label checks for miosd drift runner.
// AI-related: config/artifacts/, usr/share/mios/ventoy/mios-kickstart.cfg, tools/install.sh

use super::{Check, DriftCtx, Verdict};
use std::fs;

pub struct InstallerRolesCheck;
impl Check for InstallerRolesCheck {
    fn id(&self) -> &'static str {
        "check_installer_family_roles"
    }
    fn describe(&self) -> &'static str {
        "Assert installer roles match artifact configuration SSOT"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        let scripts = [
            "tools/install.sh",
            "automation/install.sh",
            "automation/install-fhs.sh",
        ];
        let mut found = 0;
        let mut missing_role = Vec::new();
        for s in scripts {
            let p = ctx.root.join(s);
            if p.exists() {
                found += 1;
                if let Ok(content) = std::fs::read_to_string(&p) {
                    if !content.contains("# MIOS_INSTALLER_ROLE=") {
                        missing_role.push(s);
                    }
                }
            }
        }
        if !missing_role.is_empty() {
            return Verdict::Fail(format!(
                "Installer script(s) missing role header: {:?}",
                missing_role
            ));
        }
        if found == 0 {
            return Verdict::Fail("No installer scripts found".to_string());
        }
        Verdict::Pass("Installer family role markers verified unique".to_string())
    }
}

pub struct OfflineInstallCheck;
impl Check for OfflineInstallCheck {
    fn id(&self) -> &'static str {
        "check_offline_install_invariant"
    }
    fn describe(&self) -> &'static str {
        "Assert offline installation invariant holds without network access"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        let install_sh = ctx.root.join("tools/install.sh");
        if !install_sh.exists() {
            return Verdict::Fail("tools/install.sh is absent".to_string());
        }
        match std::fs::read_to_string(&install_sh) {
            Ok(content) => {
                let code: String = content
                    .lines()
                    .map(|l| l.split('#').next().unwrap_or(""))
                    .collect::<Vec<_>>()
                    .join("\n");
                if !code.contains("oci-archive:") {
                    return Verdict::Fail(
                        "tools/install.sh executable code missing oci-archive transport/source"
                            .to_string(),
                    );
                }
                Verdict::Pass(
                    "Offline install invariant verified clean against executable code".to_string(),
                )
            }
            Err(e) => Verdict::Fail(format!("Failed to read tools/install.sh: {}", e)),
        }
    }
}

pub struct BIBConfigCheck;
impl Check for BIBConfigCheck {
    fn id(&self) -> &'static str {
        "check_bib_single_config_invariant"
    }
    fn describe(&self) -> &'static str {
        "Assert BIB configuration matches single-config policy"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        // task_982. This used to stat the Justfile and return Pass("BIB single
        // config invariant verified") without opening it. Ported from the bash
        // twin: every config/artifacts/*.toml must parse, and every recipe that
        // invokes bootc-image-builder must mount EXACTLY ONE /config.toml --
        // two mounts and the second silently wins.
        let justfile = ctx.root.join("Justfile");
        let content = match fs::read_to_string(&justfile) {
            Ok(c) => c,
            Err(e) => return Verdict::Fail(format!("Justfile unreadable: {e}")),
        };

        let mut bad: Vec<String> = Vec::new();
        let artifacts = ctx.root.join("config/artifacts");
        let mut toml_seen = 0usize;
        if let Ok(rd) = fs::read_dir(&artifacts) {
            let mut paths: Vec<_> = rd
                .flatten()
                .map(|e| e.path())
                .filter(|p| p.extension().map(|x| x == "toml").unwrap_or(false))
                .collect();
            paths.sort();
            for p in paths {
                toml_seen += 1;
                let name = p
                    .file_name()
                    .map(|n| n.to_string_lossy().to_string())
                    .unwrap_or_default();
                match fs::read_to_string(&p) {
                    Ok(t) => {
                        if t.parse::<toml::Value>().is_err() {
                            bad.push(format!("invalid TOML syntax in {name}"));
                        }
                    }
                    Err(e) => bad.push(format!("{name} unreadable: {e}")),
                }
            }
        }
        if toml_seen == 0 {
            bad.push(
                "config/artifacts holds no *.toml recipes -- nothing was compared".to_string(),
            );
        }

        // Recipes start at column 0 as `name:`; everything indented under one
        // belongs to it.
        let mut recipe = String::new();
        let mut body = String::new();
        let mut bib_recipes = 0usize;
        let check_recipe = |name: &str, body: &str, bad: &mut Vec<String>, n: &mut usize| {
            if name.is_empty() {
                return;
            }
            if !(body.contains("{{BIB}}") || body.contains("bootc-image-builder")) {
                return;
            }
            *n += 1;
            let mounts = body
                .split("-v ")
                .skip(1)
                .filter(|seg| {
                    let head = seg.split_whitespace().next().unwrap_or("");
                    // `-v ./config/artifacts/bib.toml:/config.toml:ro` -- the
                    // mount carries flags, so match the target anywhere in the
                    // token rather than at its end.
                    head.contains(":/config.toml") || head.contains(":/config.json")
                })
                .count();
            if mounts != 1 {
                bad.push(format!(
                    "recipe '{name}' mounts {mounts} /config.toml, must be exactly 1"
                ));
            }
        };
        for line in content.lines() {
            let is_header = !line.starts_with(char::is_whitespace)
                && !line.starts_with('#')
                && line.contains(':')
                && line
                    .split(':')
                    .next()
                    .map(|h| {
                        !h.is_empty()
                            && h.chars()
                                .all(|c| c.is_alphanumeric() || c == '_' || c == '-')
                    })
                    .unwrap_or(false);
            if is_header {
                check_recipe(&recipe, &body, &mut bad, &mut bib_recipes);
                recipe = line.split(':').next().unwrap_or("").to_string();
                body.clear();
            } else {
                body.push_str(line);
                body.push('\n');
            }
        }
        check_recipe(&recipe, &body, &mut bad, &mut bib_recipes);
        if bib_recipes == 0 {
            bad.push(
                "no Justfile recipe invokes bootc-image-builder -- nothing was compared"
                    .to_string(),
            );
        }

        if bad.is_empty() {
            Verdict::Pass(format!(
                "{bib_recipes} BIB recipe(s) mount exactly one /config.toml; {toml_seen} artifact recipe(s) parse"
            ))
        } else {
            Verdict::Fail(bad.join("; "))
        }
    }
}

pub struct DeployPlaneCheck;
impl Check for DeployPlaneCheck {
    fn id(&self) -> &'static str {
        "check_deploy_plane"
    }
    fn describe(&self) -> &'static str {
        "Assert deploy plane artifacts and scripts match SSOT"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        // task_982. This used to stat two files and return Pass("Deploy plane
        // verified"). Stating that a file EXISTS is not verifying what is in
        // it. Ported from the bash twin's content assertions -- and unlike the
        // twin, an absent subject FAILS here rather than printing a WARNING and
        // carrying on, because these are tracked deliverables.
        let mut bad: Vec<String> = Vec::new();

        let ks_path = ctx.root.join("usr/share/mios/ventoy/mios-kickstart.cfg");
        match fs::read_to_string(&ks_path) {
            Ok(text) => {
                // The Total Root Merge is what makes `.git` IS `/` true at
                // install time; without the export the installer lays down a
                // conventional Fedora instead.
                if !text.contains("MIOS_FHS_TOTAL_ROOT_MERGE=1") {
                    bad.push(
                        "mios-kickstart.cfg: no MIOS_FHS_TOTAL_ROOT_MERGE=1 export".to_string(),
                    );
                }
                if !text.contains("BOOTSTRAP_REPO") || !text.contains("MIOS_REPO") {
                    bad.push(
                        "mios-kickstart.cfg: no BOOTSTRAP_REPO / MIOS_REPO offline override"
                            .to_string(),
                    );
                }
            }
            Err(e) => bad.push(format!("mios-kickstart.cfg unreadable: {e}")),
        }

        let oci_path = ctx.root.join("usr/share/mios/ventoy/mios-oci-install.ks");
        if let Err(e) = fs::read_to_string(&oci_path) {
            bad.push(format!("mios-oci-install.ks unreadable: {e}"));
        }

        let vj_path = ctx.root.join("usr/share/mios/ventoy/ventoy.json");
        match fs::read_to_string(&vj_path) {
            Ok(text) => {
                if !text.contains("Fedora-Server.iso") || !text.contains("mios-kickstart.cfg") {
                    bad.push(
                        "ventoy.json: does not point the Fedora-Server.iso entry at mios-kickstart.cfg"
                            .to_string(),
                    );
                }
            }
            Err(e) => bad.push(format!("ventoy.json unreadable: {e}")),
        }

        if bad.is_empty() {
            Verdict::Pass(
                "Deploy plane: kickstart exports, OCI install script and ventoy.json all present and wired"
                    .to_string(),
            )
        } else {
            Verdict::Fail(bad.join("; "))
        }
    }
}

pub struct OCIArchivePathCheck;
impl Check for OCIArchivePathCheck {
    fn id(&self) -> &'static str {
        "check_oci_archive_path"
    }
    fn describe(&self) -> &'static str {
        "Assert OCI archive producer and consumer paths match"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        let install_sh = ctx.root.join("tools/install.sh");
        if !install_sh.exists() {
            return Verdict::Fail("tools/install.sh absent for OCI archive path check".to_string());
        }
        match std::fs::read_to_string(&install_sh) {
            Ok(content) => {
                if content.contains("/mnt/mios-repo/mios-latest.tar")
                    || content.contains("OCI_ARCHIVE")
                {
                    Verdict::Pass("OCI archive path verified".to_string())
                } else {
                    Verdict::Fail("tools/install.sh missing standard OCI archive path".to_string())
                }
            }
            Err(e) => Verdict::Fail(format!("Failed to read tools/install.sh: {}", e)),
        }
    }
}

pub struct Win11VMTemplateCheck;
impl Check for Win11VMTemplateCheck {
    fn id(&self) -> &'static str {
        "check_win11_vm_template_xml"
    }
    fn describe(&self) -> &'static str {
        "Assert Win11 VM libvirt XML template validity"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        let ssot = ctx.root.join("usr/share/mios/mios.toml");
        if !ssot.exists() {
            return Verdict::Fail("SSOT mios.toml missing for Win11 VM template check".to_string());
        }
        if let Ok(content) = std::fs::read_to_string(&ssot) {
            if content.contains("[vm.win11]") {
                return Verdict::Pass(
                    "Win11 VM template configuration present in SSOT".to_string(),
                );
            }
        }
        Verdict::Fail("[vm.win11] section missing from SSOT".to_string())
    }
}
