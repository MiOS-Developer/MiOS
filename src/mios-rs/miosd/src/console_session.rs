// AI-hint: Authenticated greetd PAM session handoff of an actual controlling Linux VT, so unprivileged GPM clients can connect; restore only this session's witnessed ownership on logout.
// AI-related: etc/pam.d/greetd, usr/share/mios/mios.toml [terminal.console_login], tools/native/mios-inputd/src/console.rs

use std::ffi::CString;
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Seek, Write};
use std::os::fd::AsRawFd;
use std::os::unix::fs::{DirBuilderExt, FileTypeExt, MetadataExt, OpenOptionsExt};
use std::path::{Path, PathBuf};

#[derive(Clone, Debug, PartialEq, serde::Serialize, serde::Deserialize)]
struct Lease {
    session: i32,
    start: u64,
    uid: u32,
    device: u64,
    inode: u64,
    gid: u32,
    mode: u32,
}

fn proc_identity(text: &str) -> Result<(i32, u64, u64, i32), String> {
    let fields: Vec<_> = text
        .rsplit_once(") ")
        .ok_or("invalid process stat")?
        .1
        .split_whitespace()
        .collect();
    let parse = |index: usize| -> Result<u64, String> {
        fields
            .get(index)
            .ok_or("incomplete process stat")?
            .parse()
            .map_err(|_| "invalid process stat field".into())
    };
    Ok((
        i32::try_from(parse(3)?).map_err(|_| "invalid process session")?,
        parse(4)?,
        parse(19)?,
        i32::try_from(parse(5)?).map_err(|_| "invalid terminal foreground group")?,
    ))
}

fn vt_number(tty: &str) -> Result<u32, String> {
    let name = tty.strip_prefix("/dev/").unwrap_or(tty);
    let number: u32 = name
        .strip_prefix("tty")
        .ok_or("PAM terminal is not a Linux VT")?
        .parse()
        .map_err(|_| "invalid PAM VT")?;
    if !(1..=63).contains(&number)
        || (tty != format!("tty{number}") && tty != format!("/dev/tty{number}"))
    {
        return Err("PAM terminal must name an actual numbered Linux VT".into());
    }
    Ok(number)
}

fn private_directory(path: &Path) -> Result<File, String> {
    if !path.starts_with("/run/")
        || path
            .components()
            .any(|part| matches!(part, std::path::Component::ParentDir))
    {
        return Err("console session state must be beneath /run".into());
    }
    // No user-writable parent or symlink may redirect a privileged create/open.
    let parent = path.parent().ok_or("missing state parent")?;
    if parent.canonicalize().map_err(|e| e.to_string())? != parent {
        return Err("console session state parent is not canonical".into());
    }
    // MiOS's source checkout can delegate writes on /. /run is a distinct,
    // root-owned mount: unprivileged rename cannot replace this mountpoint.
    // Anchor the protected ancestry there instead of trusting a writable /.
    let run = fs::symlink_metadata("/run").map_err(|e| e.to_string())?;
    let root = fs::symlink_metadata("/").map_err(|e| e.to_string())?;
    if !run.is_dir() || run.uid() != 0 || run.mode() & 0o022 != 0 || run.dev() == root.dev() {
        return Err("console state requires a trusted /run mount".into());
    }
    let mut ancestor = Some(parent);
    while let Some(path) = ancestor {
        let metadata = fs::symlink_metadata(path).map_err(|e| e.to_string())?;
        if !metadata.is_dir() || metadata.uid() != 0 || metadata.mode() & 0o022 != 0 {
            return Err("console session state parent is not root-owned and private".into());
        }
        if path == Path::new("/run") {
            break;
        }
        ancestor = path.parent();
    }
    match fs::DirBuilder::new().mode(0o700).create(path) {
        Ok(()) => {}
        Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {}
        Err(error) => return Err(error.to_string()),
    }
    let directory = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_DIRECTORY | libc::O_NOFOLLOW | libc::O_CLOEXEC)
        .open(path)
        .map_err(|e| e.to_string())?;
    let metadata = directory.metadata().map_err(|e| e.to_string())?;
    if metadata.uid() != 0 || metadata.mode() & 0o7777 != 0o700 {
        return Err("console session state directory must be root-owned mode0700".into());
    }
    Ok(directory)
}

fn lease_file(directory: &File, vt: u32) -> Result<File, String> {
    let path = PathBuf::from(format!(
        "/proc/self/fd/{}/tty{vt}.json",
        directory.as_raw_fd()
    ));
    let file = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
        .open(path)
        .map_err(|e| e.to_string())?;
    let metadata = file.metadata().map_err(|e| e.to_string())?;
    if !metadata.is_file()
        || metadata.uid() != 0
        || metadata.nlink() != 1
        || metadata.mode() & 0o7777 != 0o600
    {
        return Err("unsafe console session lease; preserved".into());
    }
    if unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } != 0 {
        return Err(std::io::Error::last_os_error().to_string());
    }
    Ok(file)
}

fn read_lease(file: &mut File) -> Result<Option<Lease>, String> {
    if file.metadata().map_err(|e| e.to_string())?.len() > 4096 {
        return Err("oversized console session lease; preserved".into());
    }
    file.rewind().map_err(|e| e.to_string())?;
    let mut data = String::new();
    file.read_to_string(&mut data).map_err(|e| e.to_string())?;
    if data.is_empty() {
        return Ok(None);
    }
    serde_json::from_str(&data)
        .map(Some)
        .map_err(|_| "invalid console session lease; preserved".into())
}

fn write_lease(file: &mut File, lease: Option<&Lease>) -> Result<(), String> {
    let data = lease
        .map(serde_json::to_vec)
        .transpose()
        .map_err(|e| e.to_string())?
        .unwrap_or_default();
    file.rewind().map_err(|e| e.to_string())?;
    file.write_all(&data).map_err(|e| e.to_string())?;
    file.set_len(data.len() as u64).map_err(|e| e.to_string())?;
    file.sync_all().map_err(|e| e.to_string())
}

fn same_session(left: &Lease, right: &Lease) -> bool {
    (left.session, left.start, left.uid, left.device, left.inode)
        == (
            right.session,
            right.start,
            right.uid,
            right.device,
            right.inode,
        )
}

fn current_owner_action(
    open: bool,
    prior: Option<&Lease>,
    current: &Lease,
    owner: u32,
) -> Result<Option<u32>, String> {
    if open {
        if ![0, current.uid].contains(&owner) {
            return Err("VT belongs to another user; preserved".into());
        }
        if prior.is_some_and(|lease| {
            lease.session == current.session
                && lease.start == current.start
                && !same_session(lease, current)
        }) {
            return Err("current session lease identity changed; preserved".into());
        }
        Ok(Some(current.uid))
    } else if prior.is_some_and(|lease| same_session(lease, current)) && owner == current.uid {
        Ok(Some(0))
    } else {
        // A delayed logout cannot reclaim a newer session or changed device.
        Ok(None)
    }
}

fn parent_identity(parent: i32, stat: &str) -> Result<(i32, u64, u64), String> {
    let (session, tty, start, foreground) = proc_identity(stat)?;
    if parent <= 1 || session != parent || tty == 0 || foreground != session {
        return Err("greetd parent is not the foreground controlling VT session".into());
    }
    Ok((session, tty, start))
}

fn state_directory(root: &Path) -> Result<PathBuf, String> {
    // Privileged PAM policy is pinned to vendor+host paths; never import the
    // authenticated user's HOME, MIOS_* loader pointers or database overlay.
    let config = mios_resolver::resolve_projection(root).map_err(|e| e.to_string())?;
    config
        .get("terminal")
        .and_then(|t| t.get("console_login"))
        .and_then(|t| t.get("state_directory"))
        .and_then(|v| v.as_str())
        .map(PathBuf::from)
        .ok_or_else(|| "missing [terminal.console_login].state_directory".into())
}

pub fn run() -> Result<(), String> {
    if std::env::var("PAM_SERVICE").ok().as_deref() != Some("greetd") {
        return Ok(());
    }
    let open = match std::env::var("PAM_TYPE").ok().as_deref() {
        Some("open_session") => true,
        Some("close_session") => false,
        _ => return Err("console handoff is a PAM session hook only".into()),
    };
    if unsafe { libc::getuid() } != 0 || unsafe { libc::geteuid() } != 0 {
        return Err("console handoff requires the privileged greetd PAM session".into());
    }
    let vt = vt_number(&std::env::var("PAM_TTY").map_err(|_| "missing PAM terminal")?)?;
    let user = CString::new(std::env::var("PAM_USER").map_err(|_| "missing PAM user")?)
        .map_err(|_| "invalid PAM user")?;
    let account = unsafe { libc::getpwnam(user.as_ptr()) };
    if account.is_null() {
        return Err("PAM account does not exist".into());
    }
    let uid = unsafe { (*account).pw_uid };
    // pam_exec calls setsid() before exec: its child deliberately has no
    // controlling tty. Authenticate the direct PAM caller, not the child SID.
    let parent = unsafe { libc::getppid() };
    let leader = PathBuf::from(format!("/proc/{parent}"));
    let (session, controlling, start) = parent_identity(
        parent,
        &fs::read_to_string(leader.join("stat")).map_err(|e| e.to_string())?,
    )?;
    let executable = fs::read_link(leader.join("exe")).map_err(|e| e.to_string())?;
    if session <= 1
        || fs::metadata(&leader).map_err(|e| e.to_string())?.uid() != 0
        || executable != Path::new("/usr/bin/greetd")
    {
        return Err("PAM helper is not in the authenticated greetd controlling session".into());
    }
    let arguments = fs::read(leader.join("cmdline")).map_err(|e| e.to_string())?;
    if !arguments
        .split(|byte| *byte == 0)
        .any(|arg| arg == b"--session-worker")
    {
        return Err("PAM caller is not a greetd session worker".into());
    }
    let path = PathBuf::from(format!("/dev/tty{vt}"));
    let terminal = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_PATH | libc::O_NOFOLLOW | libc::O_CLOEXEC)
        .open(path)
        .map_err(|e| e.to_string())?;
    let metadata = terminal.metadata().map_err(|e| e.to_string())?;
    if !metadata.file_type().is_char_device()
        || libc::major(metadata.rdev()) != 4
        || libc::minor(metadata.rdev()) != vt
        || metadata.rdev() != controlling
        || metadata.mode() & 0o7000 != 0
    {
        return Err("PAM terminal is not the witnessed controlling Linux VT".into());
    }
    // /proc's tpgid is the kernel's current foreground group on the
    // parent's actual controlling tty. Re-read it while holding our lease.
    let foreground = || {
        (unsafe { libc::getppid() }) == parent
            && fs::read_to_string(leader.join("stat"))
                .ok()
                .and_then(|stat| parent_identity(parent, &stat).ok())
                == Some((session, controlling, start))
    };
    if !foreground() {
        return Err("greetd is no longer the foreground VT session".into());
    }
    let state = state_directory(Path::new("/"))?;
    let directory = private_directory(&state)?;
    let mut file = lease_file(&directory, vt)?;
    let prior = read_lease(&mut file)?;
    let current = Lease {
        session,
        start,
        uid,
        device: metadata.rdev(),
        inode: metadata.ino(),
        gid: metadata.gid(),
        mode: metadata.mode() & 0o7777,
    };
    if let Some(owner) = current_owner_action(open, prior.as_ref(), &current, metadata.uid())? {
        if !foreground() {
            return Err("foreground VT changed during handoff; preserved".into());
        }
        // Persist open ownership before mutation; a failed chown can safely retry.
        if open {
            write_lease(&mut file, Some(&current))?;
        }
        let result = unsafe {
            libc::fchownat(
                terminal.as_raw_fd(),
                c"".as_ptr(),
                owner,
                u32::MAX,
                libc::AT_EMPTY_PATH | libc::AT_SYMLINK_NOFOLLOW,
            )
        };
        if result != 0 {
            return Err(std::io::Error::last_os_error().to_string());
        }
        if !open {
            write_lease(&mut file, None)?;
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn session() -> Lease {
        Lease {
            session: 100,
            start: 200,
            uid: 1000,
            device: 1025,
            inode: 7,
            gid: 5,
            mode: 0o600,
        }
    }

    #[test]
    fn only_canonical_numbered_vts_are_accepted() {
        for tty in ["tty1", "/dev/tty1", "tty63"] {
            assert!(vt_number(tty).is_ok());
        }
        for tty in [
            "tty0",
            "tty64",
            "tty01",
            "tty+1",
            "/dev/pts/1",
            "../tty1",
            "/dev/tty",
            "/dev/tty1/../tty2",
        ] {
            assert!(vt_number(tty).is_err(), "{tty}");
        }
    }

    #[test]
    fn authenticated_open_and_matching_close_preserve_mode_and_group() -> Result<(), String> {
        let current = session();
        assert_eq!(current_owner_action(true, None, &current, 0)?, Some(1000));
        assert_eq!(
            current_owner_action(true, Some(&current), &current, 1000)?,
            Some(1000)
        );
        assert_eq!(
            current_owner_action(false, Some(&current), &current, 1000)?,
            Some(0)
        );
        assert_eq!((current.mode, current.gid), (0o600, 5));
        Ok(())
    }

    #[test]
    fn delayed_logout_cannot_steal_a_new_session_or_changed_device() -> Result<(), String> {
        let current = session();
        for field in 0..5 {
            let mut different = current.clone();
            match field {
                0 => different.session += 1,
                1 => different.start += 1,
                2 => different.uid += 1,
                3 => different.device += 1,
                _ => different.inode += 1,
            }
            assert_eq!(
                current_owner_action(false, Some(&different), &current, 1000)?,
                None
            );
        }
        assert_eq!(current_owner_action(false, None, &current, 1000)?, None);
        assert_eq!(
            current_owner_action(false, Some(&current), &current, 1001)?,
            None
        );
        assert!(current_owner_action(true, None, &current, 1001).is_err());
        Ok(())
    }

    #[test]
    fn changed_current_session_record_is_refused_before_chown() {
        let current = session();
        let mut changed = current.clone();
        changed.inode += 1;
        assert!(current_owner_action(true, Some(&changed), &current, 1000).is_err());
    }

    #[test]
    fn stat_comm_parentheses_do_not_shift_identity() -> Result<(), String> {
        let mut fields = vec!["0"; 20];
        fields[0] = "S";
        fields[3] = "100";
        fields[4] = "1025";
        fields[5] = "100";
        fields[19] = "200";
        assert_eq!(
            proc_identity(&format!("55 (greetd (worker)) {}", fields.join(" ")))?,
            (100, 1025, 200, 100)
        );
        assert!(proc_identity("55 invalid").is_err());
        assert!(proc_identity("55 (worker) S 0").is_err());
        Ok(())
    }

    #[test]
    fn corrupt_and_oversized_leases_are_preserved() -> Result<(), Box<dyn std::error::Error>> {
        let mut file = tempfile::tempfile()?;
        write_lease(&mut file, Some(&session()))?;
        assert_eq!(read_lease(&mut file)?, Some(session()));
        write_lease(&mut file, None)?;
        assert_eq!(read_lease(&mut file)?, None);
        file.write_all(b"{invalid")?;
        assert!(read_lease(&mut file).is_err());
        file.set_len(4097)?;
        assert!(read_lease(&mut file).is_err());
        assert_eq!(file.metadata()?.len(), 4097);
        Ok(())
    }
    #[test]
    fn privileged_policy_ignores_environment_loader_overrides(
    ) -> Result<(), Box<dyn std::error::Error>> {
        if let Ok(root) = std::env::var("MIOS_CONSOLE_POLICY_FIXTURE") {
            assert_eq!(
                state_directory(Path::new(&root))?,
                PathBuf::from("/run/root-policy")
            );
            return Ok(());
        }
        let root = tempfile::tempdir()?;
        fs::create_dir_all(root.path().join("usr/share/mios"))?;
        fs::create_dir_all(root.path().join("etc/mios"))?;
        fs::write(
            root.path().join("usr/share/mios/mios.toml"),
            "[terminal.console_login]\nstate_directory='/run/vendor-policy'\n",
        )?;
        fs::write(
            root.path().join("etc/mios/mios.toml"),
            "[terminal.console_login]\nstate_directory='/run/root-policy'\n",
        )?;
        let hostile = root.path().join("user-selected.toml");
        fs::write(
            &hostile,
            "[terminal.console_login]\nstate_directory='/run/user-policy'\n",
        )?;
        let result = std::process::Command::new(std::env::current_exe()?)
            .args([
                "--exact",
                "console_session::tests::privileged_policy_ignores_environment_loader_overrides",
                "--nocapture",
            ])
            .env("MIOS_CONSOLE_POLICY_FIXTURE", root.path())
            .env("MIOS_VENDOR_TOML", &hostile)
            .env("MIOS_HOST_TOML", &hostile)
            .env("MIOS_USER_TOML", &hostile)
            .output()?;
        assert!(
            result.status.success(),
            "privileged policy accepted a caller loader override: {}",
            String::from_utf8_lossy(&result.stdout)
        );
        Ok(())
    }
    #[test]
    fn detached_pam_child_uses_only_the_live_parent_vt_identity() -> Result<(), String> {
        let mut fields = vec!["0"; 20];
        fields[0] = "S";
        fields[3] = "100";
        fields[4] = "1025";
        fields[5] = "100";
        fields[19] = "200";
        let parent = format!("100 (greetd) {}", fields.join(" "));
        assert_eq!(parent_identity(100, &parent)?, (100, 1025, 200));
        // The real pam_exec fixture creates a detached helper SID with tty0.
        fields[3] = "300";
        fields[4] = "0";
        fields[5] = "0";
        assert!(parent_identity(100, &format!("300 (miosd) {}", fields.join(" "))).is_err());
        fields[3] = "100";
        fields[4] = "1025";
        fields[5] = "101";
        assert!(parent_identity(100, &format!("100 (greetd) {}", fields.join(" "))).is_err());
        assert!(parent_identity(101, &parent).is_err());
        Ok(())
    }
    #[test]
    fn logout_preserves_user_changed_mode_and_group_while_reclaiming_owner() -> Result<(), String> {
        let prior = session();
        let mut current = prior.clone();
        current.mode = 0o620;
        current.gid = 1000;
        assert_eq!(
            current_owner_action(false, Some(&prior), &current, 1000)?,
            Some(0)
        );
        assert_eq!(
            current_owner_action(true, Some(&prior), &current, 1000)?,
            Some(1000)
        );
        assert_eq!((current.mode, current.gid), (0o620, 1000));
        Ok(())
    }
}
