// AI-hint: SSOT projector and verifier for metal-vs-hosted.md (ADR-0021 gen category).
// AI-related: usr/share/doc/mios/reference/metal-vs-hosted.md, usr/share/mios/mios.toml, tools/native/mios-gen/src/metal_vs_hosted.rs

use std::collections::{BTreeMap, HashSet};
use std::fs;
use std::path::Path;
use std::process::Command;
use toml::Value;

pub const TOML: &str = "usr/share/mios/mios.toml";
pub const OUT: &str = "usr/share/doc/mios/reference/metal-vs-hosted.md";
pub const SEAT: &str = "endpoint";

#[derive(Debug, Clone)]
pub struct PlaneRow {
    pub name: String,
    pub role: String,
    pub owner: String,
    pub markers: Vec<String>,
    pub missing: Vec<String>,
    pub wired_by: String,
    pub wired: bool,
    pub required: bool,
}

pub fn all_packages(data: &Value) -> HashSet<String> {
    let mut out = HashSet::new();

    fn walk(node: &Value, out: &mut HashSet<String>) {
        match node {
            Value::Table(tbl) => {
                if let Some(Value::Array(pkgs)) = tbl.get("pkgs") {
                    for p in pkgs {
                        if let Value::String(s) = p {
                            out.insert(s.trim().to_string());
                        }
                    }
                }
                for (k, v) in tbl {
                    if k != "pkgs" {
                        walk(v, out);
                    }
                }
            }
            Value::Array(arr) => {
                for item in arr {
                    match item {
                        Value::String(s) => {
                            out.insert(s.trim().to_string());
                        }
                        Value::Table(_) | Value::Array(_) => {
                            walk(item, out);
                        }
                        _ => {}
                    }
                }
            }
            _ => {}
        }
    }

    if let Some(packages) = data.get("packages") {
        walk(packages, &mut out);
    }
    out
}

pub fn get_tracked_set(root: &Path) -> Option<HashSet<String>> {
    let output = Command::new("git")
        .args(["-c", "core.ignorecase=false", "ls-files", "-z"])
        .current_dir(root)
        .output()
        .ok()?;

    if !output.status.success() {
        return None;
    }

    let paths: HashSet<String> = output
        .stdout
        .split(|&b| b == 0)
        .filter(|chunk| !chunk.is_empty())
        .map(|chunk| String::from_utf8_lossy(chunk).to_string())
        .collect();

    if paths.is_empty() {
        None
    } else {
        Some(paths)
    }
}

pub fn plane_rows(root: &Path, data: &Value) -> Vec<PlaneRow> {
    let have = all_packages(data);
    let tracked = get_tracked_set(root);
    let blade = data.get("blade").and_then(|b| b.as_table());
    let planes = blade
        .and_then(|b| b.get("planes"))
        .and_then(|p| p.as_table());
    let optional: HashSet<String> = blade
        .and_then(|b| b.get("optional_planes"))
        .and_then(|o| o.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|v| v.as_str())
                .map(|s| s.to_string())
                .collect()
        })
        .unwrap_or_default();

    let mut rows = Vec::new();
    if let Some(planes_tbl) = planes {
        let mut sorted_keys: Vec<&String> = planes_tbl.keys().collect();
        sorted_keys.sort();

        for name in sorted_keys {
            let spec = planes_tbl.get(name).and_then(|s| s.as_table());
            let role = spec
                .and_then(|s| s.get("role"))
                .and_then(|r| r.as_str())
                .unwrap_or("")
                .to_string();
            let owner = spec
                .and_then(|s| s.get("owner"))
                .and_then(|o| o.as_str())
                .unwrap_or("")
                .to_string();
            let markers: Vec<String> = spec
                .and_then(|s| s.get("markers"))
                .and_then(|m| m.as_array())
                .map(|arr| {
                    arr.iter()
                        .filter_map(|v| v.as_str())
                        .map(|s| s.to_string())
                        .collect()
                })
                .unwrap_or_default();
            let missing: Vec<String> = markers
                .iter()
                .filter(|m| !have.contains(*m))
                .cloned()
                .collect();
            let wired_by = spec
                .and_then(|s| s.get("wired_by"))
                .and_then(|w| w.as_str())
                .unwrap_or("")
                .trim()
                .to_string();
            let wired = if wired_by.is_empty() {
                false
            } else if let Some(ref tr) = tracked {
                tr.contains(&wired_by)
            } else {
                root.join(&wired_by).exists()
            };
            let required = owner == "mini" && !optional.contains(name);

            rows.push(PlaneRow {
                name: name.clone(),
                role,
                owner,
                markers,
                missing,
                wired_by,
                wired,
                required,
            });
        }
    }
    rows
}

pub fn policy_rows(data: &Value) -> Vec<(String, String, String)> {
    let b = data.get("blade").and_then(|v| v.as_table());
    let spec: [(&str, &str, &str); 13] = [
        (
            "blade.hardware",
            "min_interfaces",
            "the whole floor -- the LAN is uplink AND downlink",
        ),
        (
            "blade.hardware",
            "min_ap_capable",
            "AP-capable interfaces required; 0 means an AP is optional",
        ),
        (
            "blade.cluster",
            "k3s_servers",
            "k3s-native HA: 3 servers on embedded etcd, one per localhost host",
        ),
        (
            "blade.cluster",
            "control_plane_ha",
            "quorum tolerates one member loss -- and works on a single box",
        ),
        (
            "blade.fencing",
            "method",
            "how a member is fenced -- self-fence, so none must be reached",
        ),
        (
            "blade.fencing",
            "diskless",
            "watchdog driven by quorum, no shared block device",
        ),
        (
            "blade.storage",
            "replication",
            "data classes that shadow-copy Mini-to-Mini",
        ),
        (
            "blade.storage",
            "at_rest",
            "Ceph-native: dm-crypt OSDs, key in the MON config-key store",
        ),
        (
            "blade.uplink",
            "failover",
            "where the DEFAULT ROUTE goes when the WAN dies (the plane stays)",
        ),
        (
            "blade.cluster",
            "localhost_hosts",
            "logical hosts one Mini serves itself as -- \"its own cluster\"",
        ),
        (
            "blade.mesh",
            "blocks_boot",
            "Law 12 -- enrolment never gates a boot",
        ),
        (
            "blade.mesh",
            "federate",
            "peers join by each system's OWN mechanism, never by hand",
        ),
        (
            "blade.hardware",
            "max_radios",
            "radios a Mini uses; 0 is a supported build",
        ),
    ];

    let mut out = Vec::new();
    if let Some(blade_tbl) = b {
        for (table, key, what) in spec {
            let subtable = table.split('.').nth(1).unwrap_or("");
            if let Some(node) = blade_tbl.get(subtable).and_then(|n| n.as_table()) {
                if let Some(val) = node.get(key) {
                    let val_str = match val {
                        Value::Boolean(b) => b.to_string(),
                        Value::Integer(i) => i.to_string(),
                        Value::String(s) => s.clone(),
                        Value::Array(arr) => {
                            let items: Vec<String> = arr
                                .iter()
                                .map(|item| match item {
                                    Value::String(s) => format!("'{}'", s),
                                    _ => item.to_string(),
                                })
                                .collect();
                            format!("[{}]", items.join(", "))
                        }
                        _ => val.to_string(),
                    };
                    out.push((format!("[{}].{}", table, key), val_str, what.to_string()));
                }
            }
        }
    }
    out
}

pub fn shed_split(rows: &[PlaneRow]) -> (Vec<String>, Vec<String>) {
    let mut movable = Vec::new();
    let mut fixed = Vec::new();
    for r in rows {
        if r.owner == "either" {
            movable.push(r.name.clone());
        } else {
            fixed.push(r.name.clone());
        }
    }
    movable.sort();
    fixed.sort();
    (movable, fixed)
}

fn get_caps(v: Option<&Value>) -> Vec<String> {
    match v {
        Some(Value::String(s)) => vec![s.clone()],
        Some(Value::Array(arr)) => arr
            .iter()
            .filter_map(|x| x.as_str())
            .map(|s| s.to_string())
            .collect(),
        _ => Vec::new(),
    }
}

pub fn get_requires(data: &Value) -> BTreeMap<String, Vec<String>> {
    let mut out = BTreeMap::new();
    if let Some(req) = data
        .get("blade")
        .and_then(|b| b.get("requires"))
        .and_then(|r| r.as_table())
    {
        for (k, v) in req {
            out.insert(k.clone(), get_caps(Some(v)));
        }
    }
    out
}

pub fn archetype_rows(data: &Value) -> Vec<(String, Vec<String>, usize, usize)> {
    let blade = data.get("blade").and_then(|b| b.as_table());
    let arche = blade
        .and_then(|b| b.get("archetypes"))
        .and_then(|a| a.as_table());
    let req = get_requires(data);
    let seat_n = blade
        .and_then(|b| b.get("seat_side"))
        .and_then(|s| s.as_array())
        .map(|a| a.len())
        .unwrap_or(0);

    let mut rows = Vec::new();
    if let Some(arche_tbl) = arche {
        let mut sorted_names: Vec<&String> = arche_tbl.keys().collect();
        sorted_names.sort();

        for name in sorted_names {
            let have_vec = get_caps(arche_tbl.get(name));
            let have_set: HashSet<&String> = have_vec.iter().collect();
            let started = req
                .values()
                .filter(|caps| caps.iter().all(|c| have_set.contains(c)))
                .count();
            let mut have_sorted = have_vec;
            have_sorted.sort();
            rows.push((name.clone(), have_sorted, started, started + seat_n));
        }
    }
    rows
}

pub fn seat_units(data: &Value) -> Vec<String> {
    let mut out = Vec::new();
    if let Some(seat) = data
        .get("blade")
        .and_then(|b| b.get("seat_side"))
        .and_then(|s| s.as_array())
    {
        for u in seat {
            if let Some(s) = u.as_str() {
                out.push(s.to_string());
            }
        }
    }
    out.sort();
    out
}

pub fn gated_off_on_seat(data: &Value) -> Vec<(String, Vec<String>)> {
    let endpoint_val = data
        .get("blade")
        .and_then(|b| b.get("archetypes"))
        .and_then(|a| a.get(SEAT));
    let have_set: HashSet<String> = get_caps(endpoint_val).into_iter().collect();
    let req = get_requires(data);

    let mut out = Vec::new();
    for (unit, caps) in req {
        let mut missing: Vec<String> = caps.into_iter().filter(|c| !have_set.contains(c)).collect();
        missing.sort();
        missing.dedup();
        if !missing.is_empty() {
            out.push((unit, missing));
        }
    }
    out
}

pub fn greenboot_rows(data: &Value) -> Vec<(String, String, bool, Vec<String>)> {
    let gb = data.get("greenboot").and_then(|g| g.as_table());
    let probe = gb.and_then(|g| g.get("probe")).and_then(|p| p.as_table());
    let req = get_requires(data);

    let endpoint_val = data
        .get("blade")
        .and_then(|b| b.get("archetypes"))
        .and_then(|a| a.get(SEAT));
    let have_set: HashSet<String> = get_caps(endpoint_val).into_iter().collect();
    let seat_set: HashSet<String> = seat_units(data).into_iter().collect();

    let mut rows = Vec::new();
    if let Some(crit) = gb
        .and_then(|g| g.get("critical_services"))
        .and_then(|c| c.as_array())
    {
        for svc in crit {
            let svc_str = svc.as_str().unwrap_or("").to_string();
            let spec = probe
                .and_then(|p| p.get(&svc_str.replace('-', "_")))
                .or_else(|| probe.and_then(|p| p.get(&svc_str)))
                .and_then(|s| s.as_table());
            let unit = spec
                .and_then(|s| s.get("unit"))
                .and_then(|u| u.as_str())
                .map(|s| s.to_string())
                .unwrap_or_else(|| format!("mios-{}.service", svc_str));
            let stem = if unit.ends_with(".service") {
                &unit[..unit.len() - 8]
            } else {
                &unit
            };
            let caps = req
                .get(stem)
                .or_else(|| req.get(&format!("mios-{}", svc_str)))
                .cloned()
                .unwrap_or_default();
            let mut sorted_caps = caps.clone();
            sorted_caps.sort();
            let probed = seat_set.contains(stem)
                || seat_set.contains(&format!("mios-{}", svc_str))
                || caps.iter().all(|c| have_set.contains(c));
            rows.push((svc_str, unit, probed, sorted_caps));
        }
    }
    rows
}

pub fn overlay_keys() -> Vec<(&'static str, &'static str, &'static str)> {
    vec![
        (
            "[ai].endpoint",
            "MIOS_AI_ENDPOINT",
            "the AI front door every client dials",
        ),
        ("[search].endpoint", "MIOS_SEARCH_ENDPOINT", "web search"),
        (
            "[nodes.<name>].endpoint",
            "-",
            "a compute lane in the fan-out pool",
        ),
        (
            "[blades.<name>]",
            "-",
            "a remote machine's capacity envelope",
        ),
        (
            "[urls].<tile>",
            "MIOS_URLS_<TILE>",
            "a browser-openable tile only",
        ),
    ]
}

pub fn baked_payloads(data: &Value) -> Vec<(String, String)> {
    let mut out = Vec::new();
    let llamacpp = data.get("llamacpp").and_then(|l| l.as_table());
    let spec = llamacpp
        .and_then(|l| l.get("bake_models"))
        .and_then(|b| b.as_str())
        .unwrap_or("")
        .trim();
    for entry in spec.split(',') {
        let entry = entry.trim();
        if entry.is_empty() || !entry.contains('=') {
            continue;
        }
        if let Some((local, remote)) = entry.split_once('=') {
            out.push((local.trim().to_string(), remote.trim().to_string()));
        }
    }
    let vllm = data
        .get("ai")
        .and_then(|a| a.get("vllm"))
        .and_then(|v| v.as_table());
    let model = vllm
        .and_then(|v| v.get("bake_model"))
        .and_then(|b| b.as_str())
        .unwrap_or("")
        .trim();
    if !model.is_empty() {
        out.push(("vLLM snapshot".to_string(), model.to_string()));
    }
    out
}

pub fn render(data: &Value, root: &Path) -> String {
    let rows = archetype_rows(data);
    let seat_row = rows
        .iter()
        .find(|r| r.0 == SEAT)
        .cloned()
        .unwrap_or_else(|| (SEAT.to_string(), Vec::new(), 0, 0));
    let full = rows
        .iter()
        .max_by_key(|r| r.3)
        .cloned()
        .unwrap_or_else(|| ("hybrid".to_string(), Vec::new(), 0, 0));
    let gated = gated_off_on_seat(data);
    let gb = greenboot_rows(data);
    let blade = data.get("blade").and_then(|b| b.as_table());

    let mut lines = vec![
        "<!-- AI-hint: GENERATED by mios-gen metal-vs-hosted from mios.toml. DO NOT EDIT -- re-run the generator. Part 1 compares the two PRODUCTS ([blade.planes]); Part 2 the two MODES ([blade.archetypes]). Every count is derived, so neither can go stale. ADR-0016 D10-D14. -->".to_string(),
        "<!-- AI-related: usr/share/mios/mios.toml, usr/share/doc/mios/adr/0016-blade-node-topology.md, tools/native/mios-gen/src/metal_vs_hosted.rs -->".to_string(),
        "".to_string(),
        "# MiOS-Metal vs hosted MiOS — the products, then the modes".to_string(),
        "".to_string(),
        "A MiOS-Metal boots the **entire** image, runs the AI plane, is an access point and a router at once, and is its own cluster (ADR-0016 D9). \"Offload\" describes what it *can do* — shed a workload across the mesh to a peer to scale or fail over — never something it lacks. Two earlier revisions of this page had that backwards and were wrong.".to_string(),
        "".to_string(),
        "Two different comparisons follow, and confusing them is what produced those revisions. **Part 1** compares the two *products* — a Mini against a hosted image, which differ by what metal they own. **Part 2** compares two *archetypes* — a posture any single node can boot into, which is not a product at all.".to_string(),
        "".to_string(),
    ];

    let planes = plane_rows(root, data);
    let (movable, fixed) = shed_split(&planes);
    lines.push("## Part 1 — the two products".to_string());
    lines.push("".to_string());
    lines.push("A **MiOS-Metal** is a box. A **hosted MiOS OCI image** is the same image in a different position: a container, a VM, or another machine, local or remote. They are not two builds — one artifact, one tag, one bake. What separates them is not what they *contain* but what they *own*.".to_string());
    lines.push("".to_string());
    lines.push(
        "`[blade.planes].owner` is that line, and it is the whole definition of offload:"
            .to_string(),
    );
    lines.push("".to_string());
    lines.push("- **`mini`** — the plane is bound to metal this box has and a guest does not: radios, the uplink NIC, the hypervisor itself, the bare-metal filesystem. It **cannot be shed**, because a hosted image has nothing to shed it onto.".to_string());
    lines.push("- **`either`** — the plane is a workload. A Mini runs it by default and may hand it to any peer; a hosted image can accept it.".to_string());
    lines.push("".to_string());

    if planes.is_empty() {
        lines.push("**`[blade.planes]` is empty**, so nothing declares which planes a Mini owns and the shed set cannot be derived. That is a defect in the SSOT, not an empty answer.".to_string());
        lines.push("".to_string());
    } else {
        let movable_str = movable
            .iter()
            .map(|m| format!("`{}`", m))
            .collect::<Vec<_>>()
            .join(", ");
        let fixed_str = fixed
            .iter()
            .map(|f| format!("`{}`", f))
            .collect::<Vec<_>>()
            .join(", ");
        lines.push(format!(
            "So \"offload all services to hosted MiOS OCI image(s)\" means exactly **{} of {} planes**: {}. The other {} ({}) are what make the box a Mini, and a Mini that shed them would stop being one.",
            movable.len(),
            planes.len(),
            movable_str,
            fixed.len(),
            fixed_str
        ));
        lines.push("".to_string());
        lines.push("| Plane | Owner | Can be shed | A Mini runs it | Baked | Wired |".to_string());
        lines.push("|---|---|---|---|---|---|".to_string());
        for p in &planes {
            let baked = if p.markers.is_empty() {
                "n/a — payload, not RPM".to_string()
            } else if !p.missing.is_empty() {
                format!("**no** — missing `{}`", p.missing.join("`, `"))
            } else {
                format!("yes — `{}`", p.markers.join("`, `"))
            };
            let wire = if p.wired_by.is_empty() {
                "**nothing declared**".to_string()
            } else if p.wired {
                format!("`{}`", p.wired_by)
            } else {
                format!("**missing** `{}`", p.wired_by)
            };
            let shed = if p.owner == "either" { "yes" } else { "**no**" };
            let runs = if p.required {
                "**always**"
            } else if p.owner == "mini" {
                "optional"
            } else {
                "by default"
            };
            lines.push(format!(
                "| `{}` | `{}` | {} | {} | {} | {} |",
                p.name, p.owner, shed, runs, baked, wire
            ));
        }
        lines.push("".to_string());
        lines.push("| Plane | What it does |".to_string());
        lines.push("|---|---|".to_string());
        for p in &planes {
            lines.push(format!("| `{}` | {} |", p.name, p.role));
        }
        lines.push("".to_string());
    }

    if !planes.is_empty() {
        let hw = blade
            .and_then(|b| b.get("hardware"))
            .and_then(|h| h.as_table());
        let opt: Vec<&str> = planes
            .iter()
            .filter(|r| r.owner == "mini" && !r.required)
            .map(|r| r.name.as_str())
            .collect();
        if let Some(hw_tbl) = hw {
            let nif = hw_tbl
                .get("min_interfaces")
                .and_then(|v| v.as_integer())
                .unwrap_or(1);
            let nif_suffix = if nif == 1 { "" } else { "s" };
            let max_radios = hw_tbl
                .get("max_radios")
                .and_then(|v| v.as_integer())
                .map(|i| i.to_string())
                .unwrap_or_else(|| "?".to_string());
            let min_ap = hw_tbl
                .get("min_ap_capable")
                .and_then(|v| v.as_integer())
                .map(|i| i.to_string())
                .unwrap_or_else(|| "?".to_string());
            lines.push(format!(
                "**MiOS boots on any hardware**, so these numbers gate a *plane*, never the boot (ADR-0016 D14). The floor is **{} interface{}** — the LAN is both uplink and downlink — and a radio is optional at **{}**, of which **{}** need be AP-capable. A box that misses one still boots; it simply does not run that plane.",
                nif, nif_suffix, max_radios, min_ap
            ));
            lines.push("".to_string());
        }

        if !opt.is_empty() {
            let opt_str = opt
                .iter()
                .map(|o| format!("`{}`", o))
                .collect::<Vec<_>>()
                .join(", ");
            let verb = if opt.len() == 1 { "is" } else { "are" };
            lines.push(format!(
                "That is why {} {} `owner = \"mini\"` but **not** required: a Mini with no radio is still a Mini, whereas one without a hypervisor, a router, a mesh or CephFS is not.",
                opt_str, verb
            ));
            lines.push("".to_string());
        }

        let pol = policy_rows(data);
        if !pol.is_empty() {
            lines.push("The axes the operator settled, as the SSOT now carries them (ADR-0016 D11 and D12):".to_string());
            lines.push("".to_string());
            lines.push("| Key | Value | What it settles |".to_string());
            lines.push("|---|---|---|".to_string());
            for (key, val, what) in pol {
                lines.push(format!("| `{}` | `{}` | {} |", key, val, what));
            }
            lines.push("".to_string());
        }

        lines.push("**Read the two right-hand columns narrowly.** *Baked* means every marker package is in `[packages]` — Law 12 satisfied, nothing to fetch at boot. *Wired* means the named file exists in the tree. Neither claims the plane is finished: `router` is baked and its forwarding sysctl is applied, and it still has no NAT ruleset or client DHCP (task_281). A plane is only complete when a gate proves it end to end.".to_string());
        lines.push("".to_string());

        let unbaked: Vec<&PlaneRow> = planes
            .iter()
            .filter(|r| !r.markers.is_empty() && !r.missing.is_empty())
            .collect();
        let unwired: Vec<&PlaneRow> = planes.iter().filter(|r| r.wired_by.is_empty()).collect();

        if !unbaked.is_empty() || !unwired.is_empty() {
            lines
                .push("What that leaves open right now, derived rather than asserted:".to_string());
            lines.push("".to_string());
            for p in &unbaked {
                lines.push(format!(
                    "- `{}` (`{}`) is **not baked** — `{}` absent from `[packages]`, so the plane would have to be fetched at runtime, which Law 12 forbids.",
                    p.name, p.owner, p.missing.join("`, `")
                ));
            }
            for p in &unwired {
                lines.push(format!(
                    "- `{}` (`{}`) has **no wiring declared** — nothing in the tree activates it.",
                    p.name, p.owner
                ));
            }
            lines.push("".to_string());

            let mut open_owners: HashSet<String> = HashSet::new();
            for p in &unbaked {
                open_owners.insert(p.owner.clone());
            }
            for p in &unwired {
                open_owners.insert(p.owner.clone());
            }

            if open_owners.len() == 1 && open_owners.contains("mini") {
                lines.push("Every one of those is an `owner = \"mini\"` plane, and that is the finding: the planes a hosted image was never going to provide are exactly the ones the Mini does not have yet — and the only ones adding a peer cannot supply.".to_string());
            } else {
                let mut non_mini: Vec<String> =
                    open_owners.into_iter().filter(|o| o != "mini").collect();
                non_mini.sort();
                lines.push(format!(
                    "Not all of those are `owner = \"mini\"`. The `{}` ones can be supplied by adding a peer; the `mini` ones cannot.",
                    non_mini.join("`, `")
                ));
            }
            lines.push("".to_string());
        }
    }

    lines.push("## Part 2 — the two modes".to_string());
    lines.push("".to_string());
    lines.push(format!(
        "Part 1 asked what a machine *owns*. This asks what a machine *starts*. The two archetypes are `{}`, which grants no capabilities, against the widest one. Both are the same OCI image, byte for byte — no separate Containerfile, tag or conditional bake — so every difference below is a *runtime* difference.",
        SEAT
    ));
    lines.push("".to_string());
    lines.push(format!(
        "| Surface | `{}` (grants nothing) | `{}` (widest) |",
        SEAT, full.0
    ));
    lines.push("|---|---|---|".to_string());
    lines.push("| Image | identical OCI image and tag | identical |".to_string());
    lines.push("| Bake | every payload baked, including model weights | identical |".to_string());
    lines.push(format!(
        "| Units started | **{}** | **{}** |",
        seat_row.3, full.3
    ));
    lines.push(format!(
        "| Capabilities granted | *(none)* | `{}` |",
        full.1.join("`, `")
    ));
    lines.push(format!(
        "| Capability-gated units it starts | {} | {} |",
        seat_row.2, full.2
    ));
    let seat_side_units = seat_units(data);
    lines.push(format!(
        "| Always-on units (`[blade].seat_side`) | {} | {} |",
        seat_side_units.len(),
        seat_side_units.len()
    ));
    let inference_lanes = gated
        .iter()
        .filter(|(u, _)| u.contains("llm") || u.ends_with("cpu-node"))
        .count();
    lines.push(format!(
        "| Local inference lanes | **0** | up to {} |",
        inference_lanes
    ));
    let probed_count = gb.iter().filter(|r| r.2).count();
    lines.push(format!(
        "| Greenboot probes | {} of {} critical services | {} of {} |",
        probed_count,
        gb.len(),
        gb.len(),
        gb.len()
    ));
    lines.push("| Addressing | `/etc/mios` overlay repoints the canonical keys | vendor defaults, all `localhost` |".to_string());
    lines.push("".to_string());

    lines.push("## What a seat runs, and why each one".to_string());
    lines.push("".to_string());
    lines.push("`[blade].seat_side` is a positive declaration, not debt: a seat runs what the **person** touches, a blade runs what the **work** needs.".to_string());
    lines.push("".to_string());
    lines.push("| Unit | Why a seat keeps it |".to_string());
    lines.push("|---|---|".to_string());
    for u in &seat_side_units {
        lines.push(format!("| `{}` | local I/O |", u));
    }
    lines.push("".to_string());

    lines.push("## What a seat does not run".to_string());
    lines.push("".to_string());
    lines.push(format!(
        "{} units are capability-gated off. A failed `ConditionPathExists` is a clean skip, not a failure — the unit is *baked and present*, it simply never starts.",
        gated.len()
    ));
    lines.push("".to_string());
    lines.push("| Withheld capability | Units it gates off |".to_string());
    lines.push("|---|---|".to_string());
    let mut by_cap: BTreeMap<String, Vec<&str>> = BTreeMap::new();
    for (unit, missing) in &gated {
        let key = missing.join(", ");
        by_cap.entry(key).or_default().push(unit.as_str());
    }
    for (cap, units) in &by_cap {
        lines.push(format!("| `{}` | {} |", cap, units.len()));
    }
    lines.push("".to_string());

    lines.push("## Health: what greenboot asks on each".to_string());
    lines.push("".to_string());
    lines.push("| Critical service | Unit probed | On a seat |".to_string());
    lines.push("|---|---|---|".to_string());
    for (svc, unit, probed, caps) in &gb {
        let status = if *probed {
            "probed".to_string()
        } else {
            format!("skipped (needs `{}`)", caps.join("`, `"))
        };
        lines.push(format!("| `{}` | `{}` | {} |", svc, unit, status));
    }
    lines.push("".to_string());
    let gb_crit = blade
        .and_then(|b| b.get("blade_reachability_critical"))
        .or_else(|| {
            data.get("greenboot")
                .and_then(|g| g.get("blade_reachability_critical"))
        })
        .and_then(|v| v.as_bool())
        .unwrap_or(false);
    lines.push(format!(
        "`[greenboot].blade_reachability_critical = {}` — a seat whose blade is unreachable does **not** roll itself back.",
        gb_crit
    ));
    lines.push("".to_string());

    lines.push("## Addressing: the only thing an operator changes".to_string());
    lines.push("".to_string());
    lines.push("A service's canonical address is the key its consumers already resolve (ADR-0016 Decision 1). \"local, localhost or remote\" are three *values* of one mechanism.".to_string());
    lines.push("".to_string());
    lines.push("| Overlay key | Canonical env | What it moves |".to_string());
    lines.push("|---|---|---|".to_string());
    for (key, env, what) in overlay_keys() {
        lines.push(format!("| `{}` | `{}` | {} |", key, env, what));
    }
    lines.push("".to_string());

    lines.push("## The seat's defining constraint".to_string());
    lines.push("".to_string());
    lines.push("A seat has **no local inference floor**. Every lane — heavy, alt, light and the CPU node — is capability-gated off, including the lane the resolver calls \"the always-on floor\". When the blade is unreachable a seat has a front door that can reach nothing. The model weights are baked regardless (Law 12), so a seat carries them and never loads them.".to_string());

    let payloads = baked_payloads(data);
    if !payloads.is_empty() {
        lines.push("".to_string());
        lines.push("Exactly what it carries and never loads — derived from `[llamacpp].bake_models` and `[ai.vllm].bake_model`, so this list cannot drift from what the image actually bakes:".to_string());
        lines.push("".to_string());
        lines.push("| Baked payload | Source |".to_string());
        lines.push("|---|---|".to_string());
        for (name, source) in &payloads {
            lines.push(format!("| `{}` | `{}` |", name, source));
        }

        let vllm = data
            .get("ai")
            .and_then(|a| a.get("vllm"))
            .and_then(|v| v.as_table());
        let bake_model = vllm
            .and_then(|v| v.get("bake_model"))
            .and_then(|b| b.as_str())
            .unwrap_or("")
            .trim();
        let vllm_enabled = vllm
            .and_then(|v| v.get("enable"))
            .and_then(|e| e.as_bool())
            .unwrap_or(false);
        if !bake_model.is_empty() && !vllm_enabled {
            lines.push("".to_string());
            lines.push("The vLLM snapshot is baked while `[ai.vllm].enable = false`: it ships on every image, seat and blade alike, and no archetype starts the lane that would load it. That is an unreviewed default rather than Law 12 discipline — see task_274.".to_string());
        }
    }

    lines.push("".to_string());
    lines.push("Whether that is right is an operator decision, recorded in ADR-0016 Decision 6, not a defect: giving a seat a micro local lane would trade \"offload *all* services\" for a degraded-but-alive floor.".to_string());
    lines.push("".to_string());

    format!("{}\n", lines.join("\n"))
}

pub fn run_metal_vs_hosted(root: &Path, check: bool, json_mode: bool) -> Result<(), (String, i32)> {
    let toml_path = root.join(TOML);
    let toml_content = fs::read_to_string(&toml_path).map_err(|e| {
        (
            format!("generate-metal-vs-hosted: cannot read the SSOT: {}", e),
            1,
        )
    })?;

    let data: Value = toml::from_str(&toml_content).map_err(|e| {
        (
            format!("generate-metal-vs-hosted: failed to parse TOML: {}", e),
            1,
        )
    })?;

    let want = render(&data, root);
    let path = root.join(OUT);

    if check {
        let have = fs::read_to_string(&path).map_err(|_| {
            (
                format!(
                    "generate-metal-vs-hosted: {} is missing -- run the generator",
                    OUT
                ),
                1,
            )
        })?;

        let to_write = want.replace("\r\n", "\n");
        if have != to_write {
            return Err((
                format!("generate-metal-vs-hosted: {} has drifted from the SSOT -- re-run mios-gen metal-vs-hosted", OUT),
                1,
            ));
        }

        if !json_mode {
            println!("[generate-metal-vs-hosted] {} matches the SSOT", OUT);
        }
        return Ok(());
    }

    if let Some(parent) = path.parent() {
        let _ = fs::create_dir_all(parent);
    }

    let to_write = want.replace("\r\n", "\n");
    fs::write(&path, to_write.as_bytes()).map_err(|e| {
        (
            format!(
                "generate-metal-vs-hosted: failed to write {}: {}",
                path.display(),
                e
            ),
            1,
        )
    })?;

    if !json_mode {
        println!("[generate-metal-vs-hosted] wrote {}", OUT);
    }
    Ok(())
}
