// AI-hint: Asserts mios.toml [profiles] (ADR-0025) is closed: every profile resolves without a cycle, names only registered phases and real [packages] sections, contains the one floor = true profile, every profile's targets is a list of image kinds, and every package the floor declares (its sections' pkgs plus their requires_sections closure) is one every other profile's image installs -- so the core image ([image].core_ref) is a subset of the full one.
// AI-related: usr/share/mios/mios.toml, src/mios-rs/mios-build/src/lib.rs, automation/lib/packages.sh, automation/98-drift-checks.sh, tests/drift-gate-negatives.sh
// AI-functions: check, Sections::closure, declared_packages, installed_packages

use crate::Report;
use mios_build::{PhaseRegistry, Profiles, ResolvedProfile};
use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;

const CHECK: &str = "profile-integrity";

fn cannot_run(why: impl Into<String>) -> Report {
    Report {
        check: CHECK.to_string(),
        ok: false,
        could_not_run: Some(why.into()),
        summary: String::new(),
        findings: Vec::new(),
    }
}

/// `[packages]` as automation/lib/packages.sh reads it: a section's `pkgs` plus
/// the `pkgs` of every section its `requires_sections` names, transitively.
/// `enable = false` is the one switch that keeps a declared section out of an image.
struct Sections<'a> {
    table: Option<&'a toml::value::Table>,
}

impl Sections<'_> {
    fn section(&self, name: &str) -> Option<&toml::value::Table> {
        self.table
            .and_then(|t| t.get(name))
            .and_then(|v| v.as_table())
    }

    /// Every declared section (a sub-table; `[packages].sections` is a list, not one).
    fn names(&self) -> Vec<String> {
        self.table
            .map(|t| {
                t.iter()
                    .filter(|(_, v)| v.is_table())
                    .map(|(k, _)| k.clone())
                    .collect()
            })
            .unwrap_or_default()
    }

    fn enabled(&self, name: &str) -> bool {
        self.section(name)
            .and_then(|s| s.get("enable"))
            .and_then(|v| v.as_bool())
            != Some(false)
    }

    fn strings(&self, name: &str, key: &str) -> Vec<String> {
        self.section(name)
            .and_then(|s| s.get(key))
            .and_then(|v| v.as_array())
            .map(|a| {
                a.iter()
                    .filter_map(|x| x.as_str().map(str::to_string))
                    .collect()
            })
            .unwrap_or_default()
    }

    /// The packages `name` installs, each with the section that declares it, over
    /// its `requires_sections` closure. An unknown section, or a required one that
    /// is `enable = false`, is an error (packages.sh refuses both and the bake
    /// stops); a cycle is stopped, never looped.
    fn closure(
        &self,
        name: &str,
        out: &mut BTreeMap<String, String>,
        seen: &mut BTreeSet<String>,
    ) -> Result<(), String> {
        if !seen.insert(name.to_string()) {
            return Ok(());
        }
        if self.section(name).is_none() {
            return Err(format!(
                "package section {name:?} is absent from [packages]"
            ));
        }
        for pkg in self.strings(name, "pkgs") {
            out.entry(pkg).or_insert_with(|| name.to_string());
        }
        for dep in self.strings(name, "requires_sections") {
            if !self.enabled(&dep) {
                return Err(format!(
                    "package section {name:?} requires disabled section {dep:?} (enable = false): no image can install it"
                ));
            }
            self.closure(&dep, out, seen)?;
        }
        Ok(())
    }
}

/// What a profile DECLARES: every package of every section it names, enabled or not.
fn declared_packages(
    prof: &ResolvedProfile,
    sections: &Sections,
) -> Result<BTreeMap<String, String>, String> {
    let mut out = BTreeMap::new();
    let mut seen = BTreeSet::new();
    for s in &prof.package_sections {
        sections.closure(s, &mut out, &mut seen)?;
    }
    Ok(out)
}

/// What a profile's image INSTALLS: the same closure over the enabled sections
/// only (`install_packages*` skip `enable = false`); `all` takes every enabled section.
fn installed_packages(
    prof: &ResolvedProfile,
    sections: &Sections,
) -> Result<BTreeSet<String>, String> {
    let roots: Vec<String> = if prof.all {
        sections.names()
    } else {
        prof.package_sections.iter().cloned().collect()
    };
    let mut out = BTreeMap::new();
    let mut seen = BTreeSet::new();
    for s in roots.iter().filter(|s| sections.enabled(s)) {
        sections.closure(s, &mut out, &mut seen)?;
    }
    Ok(out.into_keys().collect())
}

/// The floor's declared packages that `other`'s image never installs, with the
/// floor section that declares each and why it is missing.
fn floor_extras(
    floor: &ResolvedProfile,
    other: &ResolvedProfile,
    sections: &Sections,
) -> Result<Vec<String>, String> {
    let declared = declared_packages(floor, sections)?;
    let installed = installed_packages(other, sections)?;
    Ok(declared
        .iter()
        .filter(|(pkg, _)| !installed.contains(*pkg))
        .map(|(pkg, section)| {
            let why = if sections.enabled(section) {
                "outside the profile".to_string()
            } else {
                "enable = false".to_string()
            };
            format!("{pkg} (section {section}, {why})")
        })
        .collect())
}

pub fn check(root: &Path) -> Report {
    let ssot = root.join("usr/share/mios/mios.toml");
    let Ok(text) = std::fs::read_to_string(&ssot) else {
        return cannot_run(format!("{} could not be read", ssot.display()));
    };
    let Ok(val) = text.parse::<toml::Value>() else {
        return cannot_run("usr/share/mios/mios.toml did not parse");
    };
    let registry = match PhaseRegistry::load_from_toml(&ssot) {
        Ok(r) => r,
        Err(e) => return cannot_run(e.to_string()),
    };
    let profiles = match Profiles::from_toml_str(&text) {
        Ok(p) => p,
        Err(e) => return cannot_run(e),
    };
    let sections = val.get("packages").and_then(|v| v.as_table());
    let packages = Sections { table: sections };
    let mut findings = Vec::new();

    let names = profiles.names();
    let mut resolved = Vec::new();
    for name in &names {
        match profiles.resolve(name) {
            Ok(r) => {
                if let Err(e) = registry.for_profile(&r) {
                    findings.push(e);
                }
                for s in &r.package_sections {
                    if !sections.is_some_and(|t| t.contains_key(s)) {
                        findings.push(format!(
                            "profile {name:?} names package section {s:?} absent from [packages]"
                        ));
                    }
                }
                resolved.push(r);
            }
            Err(e) => findings.push(e),
        }
    }

    match profiles.table_str("default") {
        Some(n) if names.contains(&n) => {}
        Some(n) => findings.push(format!(
            "[profiles].default = {n:?} is not a declared profile"
        )),
        None => findings.push("[profiles].default is absent".to_string()),
    }

    let floor_name = profiles.floor().map_err(|e| findings.push(e)).ok();
    if let Some(floor) = floor_name.and_then(|f| resolved.iter().find(|r| r.name == f).cloned()) {
        for r in resolved.iter().filter(|r| !r.all && r.name != floor.name) {
            let missing: Vec<&String> = floor
                .phases
                .iter()
                .filter(|x| !r.phases.contains(*x))
                .chain(
                    floor
                        .package_sections
                        .iter()
                        .filter(|x| !r.package_sections.contains(*x)),
                )
                .collect();
            if !missing.is_empty() {
                findings.push(format!(
                    "profile {:?} does not contain the floor profile {:?}: missing {}",
                    r.name,
                    floor.name,
                    missing
                        .iter()
                        .map(|s| s.as_str())
                        .collect::<Vec<_>>()
                        .join(", ")
                ));
            }
        }
        // Package level, `all` profiles included: the core image ([image].core_ref)
        // must be a subset of every other image, so a package the floor declares
        // that another profile's image never installs is drift, not a bigger core.
        for r in resolved.iter().filter(|r| r.name != floor.name) {
            match floor_extras(&floor, r, &packages) {
                Ok(extras) if extras.is_empty() => {}
                Ok(extras) => findings.push(format!(
                    "profile {:?} declares package(s) the profile {:?} image never installs: {}",
                    floor.name,
                    r.name,
                    extras.join(", ")
                )),
                Err(e) => findings.push(e),
            }
        }
    }

    if let Err(e) = profiles.targets() {
        findings.push(e);
    }

    Report {
        check: CHECK.to_string(),
        ok: findings.is_empty(),
        could_not_run: None,
        summary: format!(
            "{} profile(s) resolve over registered phases and real package sections; each contains the one floor, whose declared packages every other profile's image installs; every targets list parses",
            names.len()
        ),
        findings,
    }
}
