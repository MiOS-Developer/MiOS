// AI-hint: Runtime FHS Root Overlay and Windows/Linux host-guest topology synchronization engine for miosd.
// AI-related: usr/share/mios/mios.toml, usr/lib/systemd/system/mios-fhs-sync.service, /run/mios/fhs-sync.json

use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};
use std::process::Command;

pub const REQUIRED_FHS_DIRS: [&str; 18] = [
    "bin", "dev", "etc", "home", "lib", "lib64", "media", "mnt", "opt", "proc", "root", "run",
    "sbin", "srv", "sys", "tmp", "usr", "var",
];

pub const WINDOWS_HOST_NAMESPACES: [&str; 14] = [
    "Windows",
    "Program Files",
    "Program Files (x86)",
    "ProgramData",
    "Users",
    "$Recycle.Bin",
    "Recovery",
    "System Volume Information",
    "Riot Games",
    "XboxGames",
    "TEMP",
    "inetpub",
    "Documents and Settings",
    "PerfLogs",
];

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum HostRole {
    WindowsHost,
    WindowsGuest,
    LinuxHost,
    LinuxGuest,
}

impl HostRole {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::WindowsHost => "windows-host",
            Self::WindowsGuest => "windows-guest",
            Self::LinuxHost => "linux-host",
            Self::LinuxGuest => "linux-guest",
        }
    }
}

pub fn detect_role() -> HostRole {
    if let Ok(role_env) = std::env::var("MIOS_ROLE") {
        match role_env.to_lowercase().as_str() {
            "windows-guest" | "winguest" | "guest-win" => return HostRole::WindowsGuest,
            "windows-host" | "winhost" | "host-win" => return HostRole::WindowsHost,
            "linux-guest" | "linguest" | "guest-linux" => return HostRole::LinuxGuest,
            "linux-host" | "linhost" | "host-linux" => return HostRole::LinuxHost,
            _ => {}
        }
    }

    if cfg!(windows) {
        // Check if running inside a hypervisor/VM guest
        let is_guest = Path::new("C:\\Windows\\System32\\drivers\\vmicguestinterface.sys").exists()
            || Path::new("C:\\Windows\\System32\\drivers\\viostor.sys").exists()
            || std::env::var("MIOS_VIRTUALIZED").unwrap_or_default() == "1";
        if is_guest {
            HostRole::WindowsGuest
        } else {
            HostRole::WindowsHost
        }
    } else {
        // Check if running inside WSL or container/microVM guest
        let is_wsl = Path::new("/proc/sys/fs/binfmt_misc/WSLInterop").exists()
            || Path::new("/etc/wsl.conf").exists()
            || std::env::var("WSL_DISTRO_NAME").is_ok();
        if is_wsl {
            HostRole::LinuxGuest
        } else {
            HostRole::LinuxHost
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FhsSyncStatus {
    pub timestamp: String,
    pub platform: String,
    pub role: String,
    pub root: String,
    pub git_root_ok: bool,
    pub archive_ok: bool,
    pub fhs_dirs_verified: usize,
    pub fhs_dirs_total: usize,
    pub namespaces_isolated: usize,
    pub namespaces_total: usize,
    pub junctions_ok: bool,
    pub convergences_applied: usize,
    pub ok: bool,
    pub summary: String,
    pub findings: Vec<String>,
}

pub fn audit(root: &Path) -> FhsSyncStatus {
    let mut findings = Vec::new();
    let role = detect_role();
    let platform = std::env::consts::OS.to_string();

    // 1. Git root
    let git_dir = root.join(".git");
    let git_root_ok = if !git_dir.exists() {
        findings.push("root .git metadata directory is absent".to_string());
        false
    } else if let Ok(meta) = std::fs::symlink_metadata(&git_dir) {
        if meta.file_type().is_symlink() {
            findings.push("root .git is a symlink or junction instead of a native root directory".to_string());
            false
        } else {
            true
        }
    } else {
        true
    };

    // 2. .archive bare store
    let archive_dir = root.join(".archive");
    let archive_ok = if !archive_dir.is_dir() || !archive_dir.join("HEAD").exists() {
        findings.push("root .archive bare store is absent or invalid beside .git".to_string());
        false
    } else {
        true
    };

    // 3. FHS dirs
    let mut fhs_dirs_verified = 0;
    for dir in REQUIRED_FHS_DIRS {
        let p = root.join(dir);
        if p.is_dir() {
            fhs_dirs_verified += 1;
        } else {
            findings.push(format!("missing standard Linux FHS root directory: /{dir}"));
        }
    }

    // 4. Windows namespaces
    let gitignore = root.join(".gitignore");
    let mut namespaces_isolated = 0;
    if !gitignore.is_file() {
        findings.push("root .gitignore is absent".to_string());
    } else if let Ok(content) = std::fs::read_to_string(&gitignore) {
        if !content.lines().any(|l| l.trim() == "/*") {
            findings.push("root .gitignore lacks mandatory '/*' block-all root boundary".to_string());
        }
        for ns in WINDOWS_HOST_NAMESPACES {
            let pat = format!("/{ns}");
            if content.lines().any(|l| l.trim() == pat || l.trim() == ns) {
                namespaces_isolated += 1;
            } else {
                findings.push(format!("root .gitignore does not explicitly isolate Windows host namespace: {ns}"));
            }
        }
    }

    // 5. Junctions / Loopbacks
    let mut junctions_ok = true;
    if cfg!(windows) {
        let mnt_c = root.join("mnt").join("c");
        if !mnt_c.exists() {
            junctions_ok = false;
            findings.push("missing /mnt/c Windows filesystem loopback junction".to_string());
        }
    }

    let ok = findings.is_empty();
    let summary = format!(
        "{}/18 FHS dirs, {}/14 namespaces isolated, git_root={}, role={}",
        fhs_dirs_verified, namespaces_isolated, git_root_ok, role.as_str()
    );

    FhsSyncStatus {
        timestamp: chrono::Utc::now().to_rfc3339(),
        platform,
        role: role.as_str().to_string(),
        root: root.display().to_string(),
        git_root_ok,
        archive_ok,
        fhs_dirs_verified,
        fhs_dirs_total: REQUIRED_FHS_DIRS.len(),
        namespaces_isolated,
        namespaces_total: WINDOWS_HOST_NAMESPACES.len(),
        junctions_ok,
        convergences_applied: 0,
        ok,
        summary,
        findings,
    }
}

pub fn sync_once(root: &Path, verbose: bool) -> FhsSyncStatus {
    let mut convergences = 0;
    let role = detect_role();

    // 1. Ensure all 18 FHS directories exist
    for dir in REQUIRED_FHS_DIRS {
        let p = root.join(dir);
        if !p.exists() {
            if let Ok(()) = std::fs::create_dir_all(&p) {
                convergences += 1;
                if verbose {
                    println!("[mios-fhs-sync] created missing FHS root directory: /{dir}");
                }
            }
        }
    }

    // 2. Ensure /run/mios exists for daemon IPC and sockets
    let run_mios = root.join("run").join("mios");
    if !run_mios.exists() {
        let _ = std::fs::create_dir_all(&run_mios);
    }

    // 3. Reconcile Windows Host Namespaces in .gitignore if needed
    let gitignore = root.join(".gitignore");
    if gitignore.is_file() {
        if let Ok(content) = std::fs::read_to_string(&gitignore) {
            let mut missing = Vec::new();
            if !content.lines().any(|l| l.trim() == "/*") {
                missing.push("/*");
            }
            for ns in WINDOWS_HOST_NAMESPACES {
                let pat = format!("/{ns}");
                if !content.lines().any(|l| l.trim() == pat || l.trim() == ns) {
                    missing.push(ns);
                }
            }
            if !missing.is_empty() {
                use std::io::Write;
                if let Ok(mut f) = std::fs::OpenOptions::new().append(true).open(&gitignore) {
                    let _ = writeln!(f, "\n# MiOS Host Namespace Isolation (auto-reconciled by mios-fhs-sync)");
                    for item in missing {
                        let _ = writeln!(f, "/{item}");
                    }
                    convergences += 1;
                    if verbose {
                        println!("[mios-fhs-sync] reconciled root .gitignore host namespace exclusions");
                    }
                }
            }
        }
    }

    // 4. On Windows, ensure /mnt/c loopback junction exists
    if cfg!(windows) {
        let mnt_dir = root.join("mnt");
        let _ = std::fs::create_dir_all(&mnt_dir);
        let mnt_c = mnt_dir.join("c");
        if !mnt_c.exists() {
            let _ = Command::new("cmd")
                .args(["/c", "mklink", "/J", mnt_c.to_str().unwrap_or("C:\\mnt\\c"), "C:\\"])
                .output();
            if mnt_c.exists() {
                convergences += 1;
                if verbose {
                    println!("[mios-fhs-sync] created /mnt/c -> C:\\ loopback directory junction");
                }
            }
        }
    }

    // 5. Audit after reconciliation
    let mut status = audit(root);
    status.convergences_applied = convergences;

    // 6. Emit state heartbeat
    write_heartbeat(root, &status);

    if verbose {
        println!("[mios-fhs-sync] [{}] {}", role.as_str(), status.summary);
        if !status.ok {
            for f in &status.findings {
                println!("  [finding] {f}");
            }
        }
    }

    status
}

pub fn write_heartbeat(root: &Path, status: &FhsSyncStatus) {
    let run_dir = root.join("run").join("mios");
    let _ = std::fs::create_dir_all(&run_dir);
    let state_file = run_dir.join("fhs-sync.json");
    if let Ok(json) = serde_json::to_string_pretty(status) {
        let _ = std::fs::write(&state_file, json);
    }
}

pub fn print_status(root: &Path) {
    let state_file = root.join("run").join("mios").join("fhs-sync.json");
    if state_file.is_file() {
        if let Ok(content) = std::fs::read_to_string(&state_file) {
            println!("{content}");
            return;
        }
    }
    // Fallback to live audit
    let status = audit(root);
    println!("{}", serde_json::to_string_pretty(&status).unwrap_or_default());
}

pub async fn run_daemon(root: PathBuf, interval_secs: u64) {
    let role = detect_role();
    println!(
        "[mios-fhs-sync] Starting live runtime sync daemon for {} (role: {}, interval: {}s)...",
        root.display(),
        role.as_str(),
        interval_secs
    );

    // Initial sync
    sync_once(&root, true);

    let mut interval = tokio::time::interval(std::time::Duration::from_secs(interval_secs));
    let mut iteration: u64 = 0;

    loop {
        tokio::select! {
            _ = interval.tick() => {
                iteration = iteration.saturating_add(1);
                let status = sync_once(&root, false);
                if status.convergences_applied > 0 {
                    println!(
                        "[mios-fhs-sync] tick #{iteration}: converged {} drift item(s)",
                        status.convergences_applied
                    );
                }
            }
            _ = tokio::signal::ctrl_c() => {
                println!("[mios-fhs-sync] Received shutdown signal; stopping daemon gracefully.");
                break;
            }
        }
    }
}

pub fn install_service(root: &Path) {
    let exe = std::env::current_exe().unwrap_or_else(|_| PathBuf::from("miosd"));
    let exe_str = exe.display().to_string();

    if cfg!(windows) {
        println!("[mios-fhs-sync] Registering Windows Service 'MiOS-FhsSync'...");
        let bin_path = format!("\"{exe_str}\" fhs-sync --daemon --root \"{}\"", root.display());
        let status = Command::new("sc.exe")
            .args(["create", "MiOS-FhsSync", &format!("binPath={bin_path}"), "start=", "auto", "DisplayName=", "MiOS FHS Root Overlay Sync Service"])
            .status();

        match status {
            Ok(s) if s.success() => {
                println!("[mios-fhs-sync] Windows Service 'MiOS-FhsSync' registered successfully.");
                let _ = Command::new("sc.exe")
                    .args(["description", "MiOS-FhsSync", "Maintains runtime Linux FHS root overlay and Windows host namespace isolation across MiOS system domains."])
                    .status();
                let _ = Command::new("sc.exe").args(["start", "MiOS-FhsSync"]).status();
            }
            _ => {
                println!("[mios-fhs-sync] sc.exe service creation returned non-zero; configuring startup task fallback...");
                let _ = Command::new("schtasks.exe")
                    .args([
                        "/create",
                        "/tn",
                        "MiOS\\FhsSync",
                        "/tr",
                        &bin_path,
                        "/sc",
                        "onstart",
                        "/ru",
                        "SYSTEM",
                        "/f",
                    ])
                    .status();
                println!("[mios-fhs-sync] Scheduled task 'MiOS\\FhsSync' registered for boot startup.");
            }
        }
    } else {
        println!("[mios-fhs-sync] Installing systemd unit 'mios-fhs-sync.service'...");
        let unit_content = format!(
            "# AI-hint: Generated by miosd fhs-sync --install-service\n\
            [Unit]\n\
            Description='MiOS' FHS root overlay & host-guest topology runtime sync daemon\n\
            After=local-fs.target\n\
            Before=multi-user.target\n\n\
            [Service]\n\
            Type=simple\n\
            ExecStart={exe_str} fhs-sync --daemon --interval 15 --root {root_str}\n\
            Restart=always\n\
            RestartSec=5s\n\
            KillMode=process\n\n\
            [Install]\n\
            WantedBy=multi-user.target\n",
            root_str = root.display()
        );

        let target_path = Path::new("/etc/systemd/system/mios-fhs-sync.service");
        if let Ok(()) = std::fs::write(target_path, unit_content) {
            let _ = Command::new("systemctl").arg("daemon-reload").status();
            let _ = Command::new("systemctl").args(["enable", "--now", "mios-fhs-sync.service"]).status();
            println!("[mios-fhs-sync] systemd service enabled and started.");
        } else {
            eprintln!("[mios-fhs-sync] Failed to write /etc/systemd/system/mios-fhs-sync.service (insufficient permissions).");
        }
    }
}

pub fn uninstall_service() {
    if cfg!(windows) {
        println!("[mios-fhs-sync] Stopping and removing Windows Service 'MiOS-FhsSync'...");
        let _ = Command::new("sc.exe").args(["stop", "MiOS-FhsSync"]).status();
        let _ = Command::new("sc.exe").args(["delete", "MiOS-FhsSync"]).status();
        let _ = Command::new("schtasks.exe").args(["/delete", "/tn", "MiOS\\FhsSync", "/f"]).status();
        println!("[mios-fhs-sync] Windows service and tasks removed.");
    } else {
        println!("[mios-fhs-sync] Disabling and removing systemd service 'mios-fhs-sync.service'...");
        let _ = Command::new("systemctl").args(["disable", "--now", "mios-fhs-sync.service"]).status();
        let _ = std::fs::remove_file("/etc/systemd/system/mios-fhs-sync.service");
        let _ = Command::new("systemctl").arg("daemon-reload").status();
        println!("[mios-fhs-sync] systemd service uninstalled.");
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_fhs_dirs_coverage() {
        assert_eq!(REQUIRED_FHS_DIRS.len(), 18);
        assert!(REQUIRED_FHS_DIRS.contains(&"bin"));
        assert!(REQUIRED_FHS_DIRS.contains(&"usr"));
        assert!(REQUIRED_FHS_DIRS.contains(&"var"));
    }

    #[test]
    fn test_namespaces_coverage() {
        assert_eq!(WINDOWS_HOST_NAMESPACES.len(), 14);
        assert!(WINDOWS_HOST_NAMESPACES.contains(&"Windows"));
        assert!(WINDOWS_HOST_NAMESPACES.contains(&"Program Files"));
    }

    #[test]
    fn test_audit_and_sync_cycle() {
        let temp = tempfile::tempdir().expect("tempdir");
        let root = temp.path();

        // Initially empty
        let initial_audit = audit(root);
        assert!(!initial_audit.ok);
        assert_eq!(initial_audit.fhs_dirs_verified, 0);

        // Sync creates dirs and run/mios
        let synced = sync_once(root, false);
        assert_eq!(synced.fhs_dirs_verified, 18);
        assert!(root.join("run/mios/fhs-sync.json").is_file());

        // Verify all 18 FHS dirs exist
        for dir in REQUIRED_FHS_DIRS {
            assert!(root.join(dir).is_dir());
        }
    }

    #[test]
    fn test_role_detection() {
        let role = detect_role();
        assert!(!role.as_str().is_empty());
    }
}
