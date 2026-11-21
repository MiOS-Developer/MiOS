// AI-hint: mios-probe storage -- flags a USB-attached disk whose USB 3 link fell back to 480 Mb/s while its controller has a SuperSpeed bus, names its bridge, driver (uas or usb-storage) and the root/swap/journal roles it carries, and fails when a degraded link carries one of [hardware.storage_link].critical_roles.
// AI-related: usr/share/mios/mios.toml, usr/lib/systemd/system/mios-storage-link.service, src/mios-rs/mios-probe/src/main.rs

use crate::report::{Probe, Verdict};
use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::{Path, PathBuf};

/// The lowest USB link speed (Mb/s) that is SuperSpeed.
const SUPERSPEED_MBPS: f64 = 5000.0;
/// Where journald keeps a persistent journal.
const JOURNAL_DIR: &str = "/var/log/journal";

/// A USB-to-NVMe bridge from [hardware.storage_link].nvme_bridges.
pub struct Bridge {
    pub id: String,
    pub name: String,
    pub smartctl: String,
}

pub struct Policy {
    pub bridges: Vec<Bridge>,
    pub critical_roles: BTreeSet<String>,
}

impl Policy {
    pub fn from_ssot(cfg: &toml::Value) -> Result<Policy, String> {
        let t = cfg
            .get("hardware")
            .and_then(|h| h.get("storage_link"))
            .ok_or("mios.toml is missing [hardware.storage_link]")?;
        let mut bridges = Vec::new();
        for b in t
            .get("nvme_bridges")
            .and_then(|v| v.as_array())
            .ok_or("[hardware.storage_link].nvme_bridges is missing")?
        {
            let field = |k: &str| {
                b.get(k)
                    .and_then(|v| v.as_str())
                    .map(str::to_string)
                    .ok_or(format!(
                        "[hardware.storage_link].nvme_bridges entry lacks {k}"
                    ))
            };
            bridges.push(Bridge {
                id: field("id")?.to_ascii_lowercase(),
                name: field("name")?,
                smartctl: field("smartctl")?,
            });
        }
        let critical_roles = t
            .get("critical_roles")
            .and_then(|v| v.as_array())
            .ok_or("[hardware.storage_link].critical_roles is missing")?
            .iter()
            .filter_map(|v| v.as_str().map(str::to_string))
            .collect();
        Ok(Policy {
            bridges,
            critical_roles,
        })
    }
}

/// What sysfs says about one USB-attached disk.
#[derive(Debug, PartialEq)]
pub struct UsbDisk {
    pub disk: String,
    pub speed_mbps: f64,
    pub superspeed_bus_free: bool,
    pub driver: String,
    pub vid_pid: String,
    pub bcd_device: String,
    pub product: String,
}

fn read(p: &Path) -> Option<String> {
    fs::read_to_string(p).ok().map(|s| s.trim().to_string())
}

fn is_root_hub(name: &str) -> bool {
    name.strip_prefix("usb")
        .is_some_and(|n| !n.is_empty() && n.bytes().all(|b| b.is_ascii_digit()))
}

/// Walk from a disk's real sysfs path up to its USB device, the interface
/// driver and the controller's other root hubs. None for a disk not on USB.
fn usb_disk(disk: &str, real: &Path) -> Option<UsbDisk> {
    let mut driver = String::new();
    let mut device: Option<PathBuf> = None;
    let mut controller: Option<PathBuf> = None;
    let mut root_hub = String::new();
    for dir in real.ancestors() {
        if driver.is_empty() {
            if let Ok(t) = fs::read_link(dir.join("driver")) {
                let name = t
                    .file_name()
                    .map(|n| n.to_string_lossy().into_owned())
                    .unwrap_or_default();
                if name == "uas" || name == "usb-storage" {
                    driver = name;
                }
            }
        }
        if device.is_none() && dir.join("idVendor").is_file() && dir.join("speed").is_file() {
            device = Some(dir.to_path_buf());
        }
        let name = dir
            .file_name()
            .map(|n| n.to_string_lossy().into_owned())
            .unwrap_or_default();
        if is_root_hub(&name) {
            root_hub = name;
            controller = dir.parent().map(Path::to_path_buf);
            break;
        }
    }
    let device = device?;
    let controller = controller?;
    let speed_mbps = read(&device.join("speed"))?.parse::<f64>().ok()?;
    // A controller registers one root hub per bus: a USB 2.0 bus and a
    // SuperSpeed bus share the same ports, so a SuperSpeed hub beside the one
    // this disk hangs off means its port could have trained USB 3.
    let superspeed_bus_free = fs::read_dir(&controller)
        .ok()?
        .flatten()
        .filter(|e| {
            let n = e.file_name().to_string_lossy().into_owned();
            is_root_hub(&n) && n != root_hub
        })
        .any(|e| {
            read(&e.path().join("speed"))
                .and_then(|s| s.parse::<f64>().ok())
                .is_some_and(|s| s >= SUPERSPEED_MBPS)
        });
    let vid = read(&device.join("idVendor")).unwrap_or_default();
    let pid = read(&device.join("idProduct")).unwrap_or_default();
    Some(UsbDisk {
        disk: disk.to_string(),
        speed_mbps,
        superspeed_bus_free,
        driver: if driver.is_empty() {
            "unbound".to_string()
        } else {
            driver
        },
        vid_pid: format!("{vid}:{pid}").to_ascii_lowercase(),
        bcd_device: read(&device.join("bcdDevice")).unwrap_or_default(),
        product: read(&device.join("product")).unwrap_or_default(),
    })
}

/// Every block name that lives on a disk: the disk, its partitions and the
/// device-mapper devices stacked on any of them.
fn names_on(sys: &Path, disk: &str) -> BTreeSet<String> {
    let mut out = BTreeSet::new();
    let mut todo = vec![sys.join("block").join(disk)];
    while let Some(dir) = todo.pop() {
        let Some(name) = dir.file_name().map(|n| n.to_string_lossy().into_owned()) else {
            continue;
        };
        if !out.insert(name) {
            continue;
        }
        if let Ok(rd) = fs::read_dir(&dir) {
            for e in rd.flatten() {
                if e.path().join("partition").is_file() {
                    todo.push(e.path());
                }
            }
        }
        if let Ok(rd) = fs::read_dir(dir.join("holders")) {
            for e in rd.flatten() {
                todo.push(sys.join("block").join(e.file_name()));
            }
        }
    }
    out
}

/// The block name a mount or swap source refers to.
fn source_name(sys: &Path, src: &str) -> Option<String> {
    if let Some(dm) = src.strip_prefix("/dev/mapper/") {
        for e in fs::read_dir(sys.join("block")).ok()?.flatten() {
            if read(&e.path().join("dm/name")).as_deref() == Some(dm) {
                return Some(e.file_name().to_string_lossy().into_owned());
            }
        }
        return None;
    }
    src.strip_prefix("/dev/")
        .map(|s| s.rsplit('/').next().unwrap_or(s).to_string())
}

/// The /proc/mounts escape for a space, tab or newline in a path.
fn unescape(s: &str) -> String {
    s.replace("\\040", " ")
        .replace("\\011", "\t")
        .replace("\\012", "\n")
        .replace("\\134", "\\")
}

/// The source of the mount that holds `path`: the longest mount point that
/// prefixes it (the last such mount wins, as the kernel stacks them).
fn mount_source<'a>(mounts: &'a [(String, String)], path: &str) -> Option<&'a str> {
    let mut best: Option<(&str, usize)> = None;
    for (src, mp) in mounts {
        let under = path == mp
            || mp == "/"
            || path
                .strip_prefix(mp.as_str())
                .is_some_and(|r| r.starts_with('/'));
        if under && best.is_none_or(|(_, len)| mp.len() >= len) {
            best = Some((src.as_str(), mp.len()));
        }
    }
    best.map(|(s, _)| s)
}

/// Block names carrying root, swap and the persistent journal.
fn roles(sys: &Path, proc_: &Path) -> BTreeMap<String, BTreeSet<String>> {
    let mounts: Vec<(String, String)> = fs::read_to_string(proc_.join("mounts"))
        .unwrap_or_default()
        .lines()
        .filter_map(|l| {
            let mut f = l.split_whitespace();
            Some((unescape(f.next()?), unescape(f.next()?)))
        })
        .collect();
    let mut out: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    let mut add = |role: &str, src: &str| {
        if let Some(n) = source_name(sys, src) {
            out.entry(n).or_default().insert(role.to_string());
        }
    };
    // An ostree/composefs root mounts an overlay at /; the disk is /sysroot.
    for mp in ["/", "/sysroot"] {
        for (src, m) in &mounts {
            if m == mp {
                add("root", src);
            }
        }
    }
    if let Some(src) = mount_source(&mounts, JOURNAL_DIR) {
        add("journal", src);
    }
    for line in fs::read_to_string(proc_.join("swaps"))
        .unwrap_or_default()
        .lines()
        .skip(1)
    {
        let mut f = line.split_whitespace();
        let (Some(file), Some(kind)) = (f.next(), f.next()) else {
            continue;
        };
        let file = unescape(file);
        if kind == "file" {
            if let Some(src) = mount_source(&mounts, &file) {
                add("swap", src);
            }
        } else {
            add("swap", &file);
        }
    }
    out
}

/// Every USB-attached disk under `sys`, with the roles it carries.
pub fn scan(sys: &Path, proc_: &Path) -> Vec<(UsbDisk, BTreeSet<String>)> {
    let by_name = roles(sys, proc_);
    let mut disks = Vec::new();
    let Ok(rd) = fs::read_dir(sys.join("block")) else {
        return disks;
    };
    let mut entries: Vec<_> = rd.flatten().collect();
    entries.sort_by_key(|e| e.file_name());
    for e in entries {
        let name = e.file_name().to_string_lossy().into_owned();
        let Ok(real) = fs::canonicalize(e.path()) else {
            continue;
        };
        let Some(d) = usb_disk(&name, &real) else {
            continue;
        };
        let mut carried = BTreeSet::new();
        for n in names_on(sys, &name) {
            if let Some(r) = by_name.get(&n) {
                carried.extend(r.iter().cloned());
            }
        }
        disks.push((d, carried));
    }
    disks
}

fn speed_label(mbps: f64) -> String {
    if mbps >= 1000.0 {
        format!("{} Gb/s", mbps / 1000.0)
    } else {
        format!("{mbps} Mb/s")
    }
}

/// One verdict per USB-attached disk.
pub fn judge(policy: &Policy, disks: &[(UsbDisk, BTreeSet<String>)]) -> Vec<Probe> {
    if disks.is_empty() {
        return vec![Probe {
            key: "storage_link".to_string(),
            verdict: Verdict::Ok,
            message: "Storage: no USB-attached block device".to_string(),
        }];
    }
    let mut probes = Vec::new();
    for (d, carried) in disks {
        let bridge = policy.bridges.iter().find(|b| b.id == d.vid_pid);
        let what = match bridge {
            Some(b) => format!(
                "{} ({}, NVMe behind a {} bridge, firmware bcdDevice {})",
                d.disk, d.vid_pid, b.name, d.bcd_device
            ),
            None if d.product.is_empty() => format!("{} ({})", d.disk, d.vid_pid),
            None => format!("{} ({} {})", d.disk, d.vid_pid, d.product),
        };
        let carries = if carried.is_empty() {
            String::from("carries no root, swap or journal")
        } else {
            format!(
                "carries {}",
                carried.iter().cloned().collect::<Vec<_>>().join(", ")
            )
        };
        let critical: Vec<_> = carried
            .intersection(&policy.critical_roles)
            .cloned()
            .collect();
        let degraded = d.speed_mbps < SUPERSPEED_MBPS && d.superspeed_bus_free;
        let (verdict, mut message) = if degraded {
            let mut m = format!(
                "Storage: {what} is on a {} link although its controller has a SuperSpeed bus -- \
                 the USB 3 link did not train (cable, port, enclosure or bridge firmware); \
                 it runs {} and {carries}",
                speed_label(d.speed_mbps),
                d.driver
            );
            if bridge.is_some() {
                m.push_str(
                    "; a USB 2.0 port budgets 500 mA of bus power (USB 3: 900 mA), \
                     less than an NVMe SSD draws in a write burst",
                );
            }
            if critical.is_empty() {
                (Verdict::Warn, m)
            } else {
                m.push_str(&format!(
                    "; move {} off it or restore the USB 3 link",
                    critical.join(", ")
                ));
                (Verdict::Fail, m)
            }
        } else if d.speed_mbps >= SUPERSPEED_MBPS && d.driver == "usb-storage" {
            (
                Verdict::Warn,
                format!(
                    "Storage: {what} is on a {} link but bound to usb-storage (bulk-only), not uas; \
                     it {carries}",
                    speed_label(d.speed_mbps)
                ),
            )
        } else {
            (
                Verdict::Ok,
                format!(
                    "Storage: {what} on a {} link, {}; it {carries}",
                    speed_label(d.speed_mbps),
                    d.driver
                ),
            )
        };
        if let Some(b) = bridge {
            message.push_str(&format!(
                " (SMART: smartctl -a -d {} /dev/{})",
                b.smartctl, d.disk
            ));
        }
        probes.push(Probe {
            key: format!("storage_link.{}", d.disk),
            verdict,
            message,
        });
    }
    probes
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::symlink;

    fn w(p: &Path, s: &str) {
        fs::create_dir_all(p.parent().unwrap()).unwrap();
        fs::write(p, s).unwrap();
    }

    fn policy() -> Policy {
        Policy {
            bridges: vec![Bridge {
                id: "0bda:9210".into(),
                name: "Realtek RTL9210".into(),
                smartctl: "sntrealtek".into(),
            }],
            critical_roles: ["root", "swap", "journal"]
                .iter()
                .map(|s| s.to_string())
                .collect(),
        }
    }

    /// A controller with a USB 2.0 hub (usb1) and a SuperSpeed hub (usb2); sda
    /// hangs off `hub` at `speed` with `driver`, and has partitions sda1..sda3.
    fn fixture(hub: &str, speed: &str, ss_hub: bool, driver: &str) -> tempfile::TempDir {
        let t = tempfile::tempdir().unwrap();
        let sys = t.path().join("sys");
        let ctl = sys.join("devices/pci0000:00/0000:20:00.4");
        w(&ctl.join("usb1/speed"), "480\n");
        w(&ctl.join("usb1/idVendor"), "1d6b\n");
        if ss_hub {
            w(&ctl.join("usb2/speed"), "10000\n");
            w(&ctl.join("usb2/idVendor"), "1d6b\n");
        }
        let dev = ctl.join(hub).join("1-1");
        w(&dev.join("speed"), &format!("{speed}\n"));
        w(&dev.join("idVendor"), "0bda\n");
        w(&dev.join("idProduct"), "9210\n");
        w(&dev.join("bcdDevice"), "2100\n");
        w(&dev.join("product"), "RTL9210\n");
        let drivers = sys.join("bus/usb/drivers");
        fs::create_dir_all(drivers.join(driver)).unwrap();
        let intf = dev.join("1-1:1.0");
        fs::create_dir_all(&intf).unwrap();
        symlink(drivers.join(driver), intf.join("driver")).unwrap();
        let blk = intf.join("host0/target0:0:0/0:0:0:0/block/sda");
        for p in ["sda1", "sda2", "sda3"] {
            w(&blk.join(p).join("partition"), "1\n");
        }
        fs::create_dir_all(blk.join("holders")).unwrap();
        fs::create_dir_all(sys.join("block")).unwrap();
        symlink(&blk, sys.join("block/sda")).unwrap();
        // A non-USB disk, which the probe must skip.
        let nvme = sys.join("devices/pci0000:00/0000:01:00.0/nvme/nvme0/nvme0n1");
        fs::create_dir_all(&nvme).unwrap();
        symlink(&nvme, sys.join("block/nvme0n1")).unwrap();
        t
    }

    fn mounts(t: &tempfile::TempDir, mounts: &str, swaps: &str) {
        w(&t.path().join("proc/mounts"), mounts);
        w(
            &t.path().join("proc/swaps"),
            &format!("Filename\tType\tSize\tUsed\tPriority\n{swaps}"),
        );
    }

    fn run(t: &tempfile::TempDir) -> Vec<Probe> {
        judge(
            &policy(),
            &scan(&t.path().join("sys"), &t.path().join("proc")),
        )
    }

    const ROOT_ON_SDA: &str = "composefs / overlay ro 0 0\n/dev/sda2 /sysroot ext4 rw 0 0\n\
                               /dev/sda2 /var ext4 rw 0 0\n";

    #[test]
    fn degraded_link_under_root_and_swap_fails() {
        let t = fixture("usb1", "480", true, "usb-storage");
        mounts(&t, ROOT_ON_SDA, "/dev/sda3 partition 8G 0 -2\n");
        let p = run(&t);
        assert_eq!(p.len(), 1, "the internal NVMe disk is not a USB disk");
        assert!(p[0].verdict == Verdict::Fail, "{}", p[0].message);
        for want in [
            "480 Mb/s link",
            "SuperSpeed bus",
            "usb-storage",
            "journal, root, swap",
            "500 mA",
            "smartctl -a -d sntrealtek /dev/sda",
            "Realtek RTL9210",
            "bcdDevice 2100",
        ] {
            assert!(
                p[0].message.contains(want),
                "{want:?} not in {}",
                p[0].message
            );
        }
    }

    #[test]
    fn degraded_link_carrying_nothing_only_warns() {
        let t = fixture("usb1", "480", true, "usb-storage");
        mounts(
            &t,
            "/dev/nvme0n1p2 / ext4 rw 0 0\n",
            "/dev/zram0 partition 8G 0 100\n",
        );
        let p = run(&t);
        assert!(p[0].verdict == Verdict::Warn, "{}", p[0].message);
        assert!(p[0].message.contains("carries no root, swap or journal"));
    }

    #[test]
    fn swap_file_on_the_disk_counts_as_swap() {
        let t = fixture("usb1", "480", true, "usb-storage");
        mounts(
            &t,
            "/dev/nvme0n1p2 / ext4 rw 0 0\n/dev/sda1 /mnt/usb ext4 rw 0 0\n",
            "/mnt/usb/swapfile file 4G 0 -2\n",
        );
        let p = run(&t);
        assert!(p[0].verdict == Verdict::Fail, "{}", p[0].message);
        assert!(p[0].message.contains("carries swap"), "{}", p[0].message);
    }

    #[test]
    fn root_on_device_mapper_over_the_disk_counts() {
        let t = fixture("usb1", "480", true, "usb-storage");
        let sys = t.path().join("sys");
        let dm = sys.join("devices/virtual/block/dm-0");
        w(&dm.join("dm/name"), "luks-root\n");
        symlink(&dm, sys.join("block/dm-0")).unwrap();
        let holders = fs::canonicalize(sys.join("block/sda"))
            .unwrap()
            .join("sda2/holders");
        fs::create_dir_all(&holders).unwrap();
        symlink(&dm, holders.join("dm-0")).unwrap();
        mounts(&t, "/dev/mapper/luks-root / ext4 rw 0 0\n", "");
        let p = run(&t);
        assert!(p[0].verdict == Verdict::Fail, "{}", p[0].message);
        assert!(
            p[0].message.contains("carries journal, root"),
            "{}",
            p[0].message
        );
    }

    #[test]
    fn high_speed_only_controller_is_not_degraded() {
        // No SuperSpeed bus on the controller: 480 Mb/s is the best it can do.
        let t = fixture("usb1", "480", false, "usb-storage");
        mounts(&t, ROOT_ON_SDA, "");
        let p = run(&t);
        assert!(p[0].verdict == Verdict::Ok, "{}", p[0].message);
    }

    #[test]
    fn superspeed_uas_is_ok() {
        let t = fixture("usb2", "10000", true, "uas");
        mounts(&t, ROOT_ON_SDA, "");
        let p = run(&t);
        assert!(p[0].verdict == Verdict::Ok, "{}", p[0].message);
        assert!(
            p[0].message.contains("10 Gb/s link, uas"),
            "{}",
            p[0].message
        );
    }

    #[test]
    fn superspeed_bulk_only_warns() {
        let t = fixture("usb2", "5000", true, "usb-storage");
        mounts(&t, ROOT_ON_SDA, "");
        let p = run(&t);
        assert!(p[0].verdict == Verdict::Warn, "{}", p[0].message);
        assert!(p[0].message.contains("not uas"), "{}", p[0].message);
    }

    #[test]
    fn no_usb_disk_is_ok() {
        let t = tempfile::tempdir().unwrap();
        fs::create_dir_all(t.path().join("sys/block")).unwrap();
        mounts(&t, "/dev/nvme0n1p2 / ext4 rw 0 0\n", "");
        let p = run(&t);
        assert!(p.len() == 1 && p[0].verdict == Verdict::Ok);
    }

    #[test]
    fn policy_reads_the_ssot() {
        let cfg: toml::Value = toml::from_str(
            "[hardware.storage_link]\ncritical_roles = [\"root\"]\n\
             nvme_bridges = [{ id = \"0BDA:9210\", name = \"R\", smartctl = \"sntrealtek\" }]\n",
        )
        .unwrap();
        let p = Policy::from_ssot(&cfg).unwrap();
        assert_eq!(p.bridges[0].id, "0bda:9210");
        assert!(p.critical_roles.contains("root"));
        assert!(Policy::from_ssot(&toml::Value::Table(Default::default())).is_err());
    }
}
