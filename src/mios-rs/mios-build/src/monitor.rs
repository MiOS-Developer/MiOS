// AI-hint: Native build log discovery, status and byte-preserving follow-by-name monitor.
// AI-related: usr/share/mios/mios.toml, usr/libexec/mios/mios-build-driver, src/mios-rs/miosd/src/main.rs
// AI-functions: run, latest, tail_start, follow
use chrono::{DateTime, Local};
use regex::Regex;
use serde::Deserialize;
use std::fs::{self, File, Metadata};
use std::io::{self, Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::time::Duration;

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Settings {
    pub log_directories: Vec<PathBuf>,
    pub user_log_directory: PathBuf,
    pub log_patterns: Vec<String>,
    pub process_directory: PathBuf,
    pub process_pattern: String,
    pub status_lines: u64,
    pub tail_lines: u64,
    pub failure_scan_lines: u64,
    pub failure_pattern: String,
    pub max_processes: usize,
    pub max_images: usize,
    pub image_command_prefix: Vec<String>,
    pub image_format: String,
    pub image_scope: ImageScope,
    pub poll_interval_ms: u64,
}
#[derive(Debug, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum ImageScope {
    Repository,
    Exact,
}

struct Policy {
    settings: Settings,
    engine: String,
    image: String,
}

fn policy(root: &Path) -> Result<Policy, String> {
    let document = mios_resolver::resolve_merged(Some(root), false).map_err(|e| e.to_string())?;
    let settings: Settings = document
        .get("build")
        .and_then(|v| v.get("monitor"))
        .ok_or("SSOT [build.monitor] is required")?
        .clone()
        .try_into()
        .map_err(|e| format!("build.monitor: {e}"))?;
    if settings.log_directories.is_empty()
        || settings.log_patterns.is_empty()
        || settings.poll_interval_ms == 0
        || settings.max_processes == 0
        || settings.max_images == 0
        || !settings.process_directory.is_absolute()
        || settings.log_directories.iter().any(|p| !p.is_absolute())
        || settings.user_log_directory.is_absolute()
        || settings
            .user_log_directory
            .components()
            .any(|c| !matches!(c, std::path::Component::Normal(_)))
    {
        return Err("invalid build monitor directories, limits or polling interval".into());
    }
    for pattern in &settings.log_patterns {
        if pattern.contains('/') || pattern.contains('\\') {
            return Err("build monitor patterns must name files, not paths".into());
        }
        glob::Pattern::new(pattern).map_err(|e| format!("build monitor pattern: {e}"))?;
    }
    Regex::new(&settings.failure_pattern).map_err(|e| e.to_string())?;
    Regex::new(&settings.process_pattern).map_err(|e| e.to_string())?;
    let string = |section: &str, key: &str| -> Result<String, String> {
        let value = section
            .split('.')
            .try_fold(&document, |v, part| v.get(part))
            .and_then(|v| v.get(key))
            .and_then(toml::Value::as_str)
            .filter(|v| !v.is_empty())
            .ok_or_else(|| format!("SSOT {section}.{key} is required"))?;
        Ok(value.to_owned())
    };
    Ok(Policy {
        settings,
        engine: string("build.images", "engine")?,
        image: string("image", "local_tag")?,
    })
}

fn directories(settings: &Settings) -> Vec<PathBuf> {
    let mut directories = settings.log_directories.clone();
    if !settings.user_log_directory.as_os_str().is_empty() {
        let state = std::env::var_os("XDG_STATE_HOME")
            .filter(|s| !s.is_empty())
            .map(PathBuf::from)
            .or_else(|| {
                std::env::var_os("HOME").map(|home| PathBuf::from(home).join(".local/state"))
            });
        if let Some(state) = state {
            directories.push(state.join(&settings.user_log_directory));
        }
    }
    directories
}

pub fn latest(settings: &Settings) -> io::Result<Option<PathBuf>> {
    let patterns = settings
        .log_patterns
        .iter()
        .map(|p| glob::Pattern::new(p).map_err(|e| io::Error::new(io::ErrorKind::InvalidInput, e)))
        .collect::<io::Result<Vec<_>>>()?;
    let mut latest: Option<(std::time::SystemTime, PathBuf)> = None;
    for directory in directories(settings) {
        let entries = match fs::read_dir(directory) {
            Ok(entries) => entries,
            Err(e)
                if matches!(
                    e.kind(),
                    io::ErrorKind::NotFound | io::ErrorKind::PermissionDenied
                ) =>
            {
                continue
            }
            Err(e) => return Err(e),
        };
        for entry in entries {
            let entry = entry?;
            if !patterns
                .iter()
                .any(|p| p.matches(&entry.file_name().to_string_lossy()))
            {
                continue;
            }
            let metadata = match fs::metadata(entry.path()) {
                Ok(metadata) => metadata,
                Err(_) => continue,
            };
            if !metadata.is_file() {
                continue;
            }
            let modified = metadata.modified()?;
            let path = entry.path();
            if latest.as_ref().is_none_or(|(time, previous)| {
                modified > *time || (modified == *time && path > *previous)
            }) {
                latest = Some((modified, path));
            }
        }
    }
    Ok(latest.map(|(_, path)| path))
}

#[derive(Debug, Clone, Copy)]
pub enum Lines {
    Last(u64),
    From(u64),
}

pub fn parse_lines(value: &str) -> Result<Lines, String> {
    let (from, value) = if let Some(v) = value.strip_prefix('+') {
        (true, v)
    } else {
        (false, value.strip_prefix('-').unwrap_or(value))
    };
    let end = value
        .find(|c: char| !c.is_ascii_digit())
        .unwrap_or(value.len());
    if end == 0 {
        return Err("invalid number of lines".into());
    }
    let mut count = value[..end]
        .parse::<u64>()
        .map_err(|_| "invalid number of lines")?;
    let suffix = &value[end..];
    let factor = if suffix.is_empty() {
        1
    } else if suffix == "b" {
        512
    } else {
        let unit = suffix.as_bytes()[0].to_ascii_uppercase();
        let power = b"KMGTPEZYRQ"
            .iter()
            .position(|b| *b == unit)
            .ok_or("invalid number of lines")?
            + 1;
        let rest = &suffix[1..];
        let base = match rest {
            "" | "iB" => 1024_u64,
            "B" => 1000,
            _ => return Err("invalid number of lines".into()),
        };
        base.checked_pow(power as u32)
            .ok_or("number of lines is too large")?
    };
    count = count
        .checked_mul(factor)
        .ok_or("number of lines is too large")?;
    Ok(if from {
        Lines::From(count)
    } else {
        Lines::Last(count)
    })
}

pub fn tail_start(file: &mut File, lines: Lines) -> io::Result<u64> {
    let len = file.metadata()?.len();
    let mut buffer = [0_u8; 8192];
    match lines {
        Lines::Last(0) => Ok(len),
        Lines::Last(mut remaining) => {
            let mut end = len;
            while end > 0 {
                let start = end.saturating_sub(buffer.len() as u64);
                file.seek(SeekFrom::Start(start))?;
                let size = (end - start) as usize;
                file.read_exact(&mut buffer[..size])?;
                for index in (0..size).rev() {
                    let offset = start + index as u64;
                    if buffer[index] == b'\n' && offset != len - 1 {
                        remaining -= 1;
                        if remaining == 0 {
                            return Ok(offset + 1);
                        }
                    }
                }
                end = start;
            }
            Ok(0)
        }
        Lines::From(n) => {
            file.seek(SeekFrom::Start(0))?;
            let mut remaining = n.saturating_sub(1);
            let mut offset = 0;
            while remaining > 0 {
                let size = file.read(&mut buffer)?;
                if size == 0 {
                    return Ok(offset);
                }
                for byte in &buffer[..size] {
                    offset += 1;
                    if *byte == b'\n' {
                        remaining -= 1;
                        if remaining == 0 {
                            return Ok(offset);
                        }
                    }
                }
            }
            Ok(0)
        }
    }
}

fn copy_tail(path: &Path, lines: Lines, out: &mut dyn Write) -> io::Result<()> {
    let mut file = File::open(path)?;
    let start = tail_start(&mut file, lines)?;
    file.seek(SeekFrom::Start(start))?;
    io::copy(&mut file, out)?;
    out.flush()
}

#[cfg(unix)]
fn identity(metadata: &Metadata) -> (u64, u64) {
    use std::os::unix::fs::MetadataExt;
    (metadata.dev(), metadata.ino())
}

#[cfg(unix)]
fn follow(
    path: &Path,
    lines: Lines,
    interval: Duration,
    out: &mut dyn Write,
    err: &mut dyn Write,
) -> io::Result<()> {
    let mut notified = false;
    let mut file = loop {
        match File::open(path) {
            Ok(file) => break file,
            Err(error) => {
                if !notified {
                    writeln!(
                        err,
                        "tail: cannot open '{}' for reading: {error}",
                        path.display()
                    )?;
                }
                notified = true;
                std::thread::sleep(interval);
            }
        }
    };
    let start = tail_start(&mut file, lines)?;
    file.seek(SeekFrom::Start(start))?;
    let mut missing = false;
    loop {
        let position = file.stream_position()?;
        if file.metadata()?.len() < position {
            writeln!(err, "tail: {}: file truncated", path.display())?;
            file.seek(SeekFrom::Start(0))?;
        }
        io::copy(&mut file, out)?;
        out.flush()?;
        std::thread::sleep(interval);
        match File::open(path) {
            Ok(mut replacement) => {
                if identity(&replacement.metadata()?) != identity(&file.metadata()?) {
                    writeln!(
                        err,
                        "tail: '{}' has been replaced;  following new file",
                        path.display()
                    )?;
                    replacement.seek(SeekFrom::Start(0))?;
                    file = replacement;
                }
                missing = false;
            }
            Err(e) => {
                if !missing {
                    writeln!(
                        err,
                        "tail: cannot open '{}' for reading: {e}",
                        path.display()
                    )?;
                }
                missing = true;
            }
        }
    }
}
#[cfg(not(unix))]
fn follow(_: &Path, _: Lines, _: Duration, _: &mut dyn Write, _: &mut dyn Write) -> io::Result<()> {
    Err(io::Error::new(
        io::ErrorKind::Unsupported,
        "follow-by-name requires Unix file identity",
    ))
}

fn processes(settings: &Settings) -> Vec<(u32, String)> {
    let Ok(pattern) = Regex::new(&settings.process_pattern) else {
        return vec![];
    };
    let Ok(entries) = fs::read_dir(&settings.process_directory) else {
        return vec![];
    };
    let mut matches = Vec::new();
    for entry in entries.flatten() {
        let Some(pid) = entry
            .file_name()
            .to_str()
            .and_then(|s| s.parse::<u32>().ok())
        else {
            continue;
        };
        if pid == std::process::id() {
            continue;
        }
        if let Ok(bytes) = fs::read(entry.path().join("cmdline")) {
            let command = String::from_utf8_lossy(&bytes)
                .replace('\0', " ")
                .trim_end()
                .to_string();
            if pattern.is_match(&command) {
                matches.push((pid, command));
            }
        }
    }
    matches.sort_by_key(|(pid, _)| *pid);
    matches
}

fn emit_images(policy: &Policy, out: &mut dyn Write) -> io::Result<()> {
    let reference = match policy.settings.image_scope {
        ImageScope::Exact => policy.image.clone(),
        ImageScope::Repository => {
            let digestless = policy.image.split('@').next().unwrap_or(&policy.image);
            let slash = digestless.rfind('/').unwrap_or(0);
            match digestless.rfind(':').filter(|index| *index > slash) {
                Some(index) => digestless[..index].to_string(),
                None => digestless.to_string(),
            }
        }
    };
    let mut command =
        if let Some((program, args)) = policy.settings.image_command_prefix.split_first() {
            let mut command = std::process::Command::new(program);
            command.args(args).arg(&policy.engine);
            command
        } else {
            std::process::Command::new(&policy.engine)
        };
    let mut child = match command
        .args([
            "images",
            &reference,
            "--format",
            &policy.settings.image_format,
        ])
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::null())
        .spawn()
    {
        Ok(child) => child,
        Err(_) => return Ok(()),
    };
    let result = (|| {
        let Some(mut pipe) = child.stdout.take() else {
            return Ok(());
        };
        let mut buffer = [0_u8; 8192];
        let mut remaining = policy.settings.max_images;
        while remaining > 0 {
            let size = match pipe.read(&mut buffer) {
                Ok(0) => break,
                Ok(size) => size,
                Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
                Err(_) => break,
            };
            let mut end = size;
            for (index, byte) in buffer[..size].iter().enumerate() {
                if *byte == b'\n' {
                    remaining -= 1;
                    if remaining == 0 {
                        end = index + 1;
                        break;
                    }
                }
            }
            out.write_all(&buffer[..end])?;
        }
        Ok(())
    })();
    // Like head, stop consuming after the configured row count. Always reap
    // this diagnostic child, including when the caller closes its output pipe.
    let _ = child.kill();
    let _ = child.wait();
    result
}

pub fn run(
    root: &Path,
    kind: mios_resolver::native_aliases::Command,
    program: &str,
    args: &[String],
    out: &mut dyn Write,
    err: &mut dyn Write,
) -> i32 {
    let status = kind == mios_resolver::native_aliases::Command::BuildStatus;
    let mut chars = program.chars();
    let label = chars
        .next()
        .map(|c| c.to_uppercase().collect::<String>() + chars.as_str())
        .unwrap_or_default();
    let help = if status {
        format!("Usage: {program} [N]\n")
    } else {
        format!("Usage: {program} [-f] [-n N]\n  -f       follow the log live\n  -n N     print last N lines and exit\n")
    };
    let mut help_arguments = args.iter();
    let mut requested_help = false;
    while let Some(argument) = help_arguments.next() {
        if !status && argument == "-n" {
            let _ = help_arguments.next();
            continue;
        }
        if matches!(argument.as_str(), "-h" | "--help") {
            requested_help = true;
        }
        if status {
            break;
        }
    }
    if requested_help {
        return if out.write_all(help.as_bytes()).is_ok() {
            0
        } else {
            1
        };
    }
    let policy = match policy(root) {
        Ok(policy) => policy,
        Err(error) => {
            let _ = writeln!(err, "{label}: {error}");
            return 1;
        }
    };
    let mut lines = Lines::Last(if status {
        policy.settings.status_lines
    } else {
        policy.settings.tail_lines
    });
    let mut display_lines = policy.settings.status_lines.to_string();
    let mut following = false;
    let mut arguments = args.iter();
    while let Some(argument) = arguments.next() {
        if !status && matches!(argument.as_str(), "-f" | "--follow") {
            following = true;
            continue;
        }
        let value = if !status && argument == "-n" {
            match arguments.next() {
                Some(value) => value,
                None => {
                    let _ = writeln!(err, "{label}: -n requires a value");
                    return 2;
                }
            }
        } else {
            argument
        };
        lines = match parse_lines(value) {
            Ok(lines) => lines,
            Err(error) => {
                let _ = writeln!(err, "tail: {error}: '{value}'");
                return 1;
            }
        };
        if status {
            display_lines = value.clone();
            break;
        }
    }
    let path = match latest(&policy.settings) {
        Ok(Some(path)) => path,
        Ok(None) => {
            if status {
                let primary = &policy.settings.log_directories[0];
                if !primary.is_dir() {
                    let _ = writeln!(out, "{label}: {} does not exist", primary.display());
                } else {
                    let _ = writeln!(
                        out,
                        "{label}: no {} found under {}",
                        policy.settings.log_patterns[0],
                        policy
                            .settings
                            .log_directories
                            .iter()
                            .map(|p| p.display().to_string())
                            .collect::<Vec<_>>()
                            .join(" or ")
                    );
                }
                return 0;
            }
            let _ = writeln!(err, "{label}: no {} found", policy.settings.log_patterns[0]);
            return 1;
        }
        Err(error) => {
            let _ = writeln!(err, "{label}: {error}");
            return 1;
        }
    };
    let result = (|| -> io::Result<()> {
        if !status {
            writeln!(err, "── tailing: {} ──", path.display())?;
            return if following {
                follow(
                    &path,
                    lines,
                    Duration::from_millis(policy.settings.poll_interval_ms),
                    out,
                    err,
                )
            } else {
                copy_tail(&path, lines, out)
            };
        }
        let metadata = fs::metadata(&path)?;
        let modified: DateTime<Local> = metadata.modified()?.into();
        writeln!(
            out,
            "── most recent build log ──\n  path:  {}\n  size:  {} bytes\n  mtime: {}",
            path.display(),
            metadata.len(),
            modified.format("%Y-%m-%d %H:%M:%S")
        )?;
        let processes = processes(&policy.settings);
        if processes.is_empty() {
            let mut bytes = Vec::new();
            copy_tail(
                &path,
                Lines::Last(policy.settings.failure_scan_lines),
                &mut bytes,
            )?;
            let pattern = Regex::new(&policy.settings.failure_pattern).map_err(io::Error::other)?;
            let failed = String::from_utf8_lossy(&bytes)
                .lines()
                .any(|line| pattern.is_match(line));
            writeln!(
                out,
                "  state: {}",
                if failed { "FAILED" } else { "completed" }
            )?;
        } else {
            writeln!(out, "  state: RUNNING")?;
            for (pid, command) in processes.into_iter().take(policy.settings.max_processes) {
                writeln!(out, "    {pid} {command}")?;
            }
        }
        writeln!(out, "\n── most recent OCI image ──")?;
        emit_images(&policy, out)?;
        writeln!(out, "\n── last {display_lines} lines of the log ──")?;
        copy_tail(&path, lines, out)
    })();
    match result {
        Ok(()) => 0,
        Err(error) if error.kind() == io::ErrorKind::BrokenPipe => 141,
        Err(error) => {
            let _ = writeln!(err, "{label}: {error}");
            1
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp() -> tempfile::TempDir {
        tempfile::Builder::new()
            .prefix("mios-build-monitor-")
            .tempdir()
            .unwrap()
    }

    fn settings() -> Settings {
        let document: toml::Value = include_str!("../../../../usr/share/mios/mios.toml")
            .parse()
            .unwrap();
        document["build"]["monitor"].clone().try_into().unwrap()
    }

    #[test]
    fn tail_preserves_raw_bytes_partial_lines_zero_and_chunk_boundaries() {
        let directory = temp();
        let path = directory.path().join("driver.log");
        for (data, cases) in [
            (
                b"a\nb\nc\n".to_vec(),
                vec![
                    ("1", b"c\n".to_vec()),
                    ("2", b"b\nc\n".to_vec()),
                    ("0", vec![]),
                    ("+2", b"b\nc\n".to_vec()),
                ],
            ),
            (
                b"a\n\xff\0partial".to_vec(),
                vec![
                    ("1", b"\xff\0partial".to_vec()),
                    ("+2", b"\xff\0partial".to_vec()),
                    ("+9", vec![]),
                ],
            ),
            (vec![], vec![("1", vec![]), ("+0", vec![])]),
        ] {
            fs::write(&path, data).unwrap();
            for (count, expected) in cases {
                let mut actual = Vec::new();
                copy_tail(&path, parse_lines(count).unwrap(), &mut actual).unwrap();
                assert_eq!(actual, expected, "{count}");
            }
        }
        let mut data = vec![b'x'; 8192 * 3 + 19];
        data.extend_from_slice(b"\nlast\n");
        fs::write(&path, &data).unwrap();
        let mut actual = Vec::new();
        copy_tail(&path, Lines::Last(1), &mut actual).unwrap();
        assert_eq!(actual, b"last\n");
        actual.clear();
        copy_tail(&path, Lines::Last(u64::MAX), &mut actual).unwrap();
        assert_eq!(actual, data);
    }

    #[test]
    fn count_syntax_is_checked_without_saturating_overflow_or_dropping_signs() {
        assert!(matches!(parse_lines("2K").unwrap(), Lines::Last(2048)));
        assert!(matches!(parse_lines("+2kB").unwrap(), Lines::From(2000)));
        assert!(matches!(parse_lines("-5").unwrap(), Lines::Last(5)));
        for invalid in [
            "",
            "bad",
            "--5",
            "1.5",
            "1foo",
            "18446744073709551616",
            "9999999999999999999Q",
        ] {
            assert!(parse_lines(invalid).is_err(), "{invalid}");
        }
    }

    #[test]
    fn discovery_uses_configured_patterns_mtime_and_regular_symlink_targets() {
        let directory = temp();
        let mut settings = settings();
        settings.log_directories = vec![directory.path().to_owned()];
        settings.user_log_directory = PathBuf::new();
        settings.log_patterns = vec!["driver-*.log".into()];
        fs::write(directory.path().join("unrelated.log"), b"ignored").unwrap();
        assert!(latest(&settings).unwrap().is_none());
        for (name, time) in [("driver-old.log", 1), ("driver-new.log", 2)] {
            let path = directory.path().join(name);
            fs::write(&path, name).unwrap();
            File::options()
                .write(true)
                .open(&path)
                .unwrap()
                .set_times(
                    fs::FileTimes::new()
                        .set_modified(std::time::UNIX_EPOCH + Duration::from_secs(time)),
                )
                .unwrap();
        }
        assert_eq!(
            latest(&settings).unwrap().unwrap().file_name().unwrap(),
            "driver-new.log"
        );
        #[cfg(unix)]
        {
            let target = directory.path().join("actual.log");
            fs::write(&target, b"latest target").unwrap();
            std::os::unix::fs::symlink(&target, directory.path().join("driver-link.log")).unwrap();
            assert_eq!(
                latest(&settings).unwrap().unwrap().file_name().unwrap(),
                "driver-link.log"
            );
        }
    }

    #[cfg(unix)]
    #[test]
    fn follow_retries_an_initially_missing_selected_name_and_returns_broken_pipe() {
        use std::sync::mpsc;
        struct Notify(mpsc::Sender<Vec<u8>>, bool);
        impl Write for Notify {
            fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
                let _ = self.0.send(bytes.to_vec());
                if self.1 {
                    Err(io::Error::new(
                        io::ErrorKind::BrokenPipe,
                        "closed test consumer",
                    ))
                } else {
                    Ok(bytes.len())
                }
            }
            fn flush(&mut self) -> io::Result<()> {
                Ok(())
            }
        }
        let directory = temp();
        let path = directory.path().join("selected.log");
        let (data_tx, data_rx) = mpsc::channel();
        let (error_tx, error_rx) = mpsc::channel();
        let (done_tx, done_rx) = mpsc::channel();
        let worker_path = path.clone();
        let worker = std::thread::spawn(move || {
            let result = follow(
                &worker_path,
                Lines::Last(1),
                Duration::from_millis(1),
                &mut Notify(data_tx, true),
                &mut Notify(error_tx, false),
            );
            let _ = done_tx.send(result.map_err(|e| e.kind()));
        });
        error_rx.recv_timeout(Duration::from_secs(5)).unwrap();
        fs::write(&path, b"created\n").unwrap();
        assert_eq!(
            data_rx.recv_timeout(Duration::from_secs(5)).unwrap(),
            b"created\n"
        );
        assert_eq!(
            done_rx.recv_timeout(Duration::from_secs(5)).unwrap(),
            Err(io::ErrorKind::BrokenPipe)
        );
        worker.join().unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn follow_preserves_bytes_across_append_truncate_rotation_and_missing_name() {
        use std::sync::{
            atomic::{AtomicBool, Ordering},
            mpsc, Arc,
        };
        struct Output {
            sender: mpsc::Sender<Vec<u8>>,
            closed: Arc<AtomicBool>,
        }
        impl Write for Output {
            fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
                self.flush()?;
                self.sender.send(bytes.to_vec()).map_err(io::Error::other)?;
                Ok(bytes.len())
            }
            fn flush(&mut self) -> io::Result<()> {
                if self.closed.load(Ordering::SeqCst) {
                    Err(io::Error::new(
                        io::ErrorKind::BrokenPipe,
                        "closed test reader",
                    ))
                } else {
                    Ok(())
                }
            }
        }
        struct Worker(
            Arc<AtomicBool>,
            Option<std::thread::JoinHandle<io::Result<()>>>,
        );
        impl Drop for Worker {
            fn drop(&mut self) {
                self.0.store(true, Ordering::SeqCst);
                if let Some(worker) = self.1.take() {
                    let _ = worker.join();
                }
            }
        }
        fn expect(receiver: &mpsc::Receiver<Vec<u8>>, bytes: &mut Vec<u8>, expected: &[u8]) {
            let deadline = std::time::Instant::now() + Duration::from_secs(5);
            while bytes.len() < expected.len() {
                bytes.extend(
                    receiver
                        .recv_timeout(deadline.saturating_duration_since(std::time::Instant::now()))
                        .unwrap(),
                );
            }
            assert_eq!(bytes, expected);
        }
        let directory = temp();
        let path = directory.path().join("selected.log");
        fs::write(&path, b"start\n").unwrap();
        let (data_tx, data_rx) = mpsc::channel();
        let (error_tx, error_rx) = mpsc::channel();
        let closed = Arc::new(AtomicBool::new(false));
        let worker_closed = closed.clone();
        let worker_path = path.clone();
        let mut worker = Worker(
            closed.clone(),
            Some(std::thread::spawn(move || {
                follow(
                    &worker_path,
                    Lines::Last(1),
                    Duration::from_millis(1),
                    &mut Output {
                        sender: data_tx,
                        closed: worker_closed,
                    },
                    &mut Output {
                        sender: error_tx,
                        closed: Arc::new(AtomicBool::new(false)),
                    },
                )
            })),
        );
        let mut bytes = Vec::new();
        expect(&data_rx, &mut bytes, b"start\n");
        File::options()
            .append(true)
            .open(&path)
            .unwrap()
            .write_all(b"append\n")
            .unwrap();
        expect(&data_rx, &mut bytes, b"start\nappend\n");
        fs::write(&path, b"T\n").unwrap();
        expect(&data_rx, &mut bytes, b"start\nappend\nT\n");
        fs::rename(&path, directory.path().join("rotated.log")).unwrap();
        fs::write(&path, b"replacement\n").unwrap();
        expect(&data_rx, &mut bytes, b"start\nappend\nT\nreplacement\n");
        fs::remove_file(&path).unwrap();
        let deadline = std::time::Instant::now() + Duration::from_secs(5);
        let mut errors = Vec::new();
        while !String::from_utf8_lossy(&errors).contains("cannot open") {
            errors.extend(
                error_rx
                    .recv_timeout(deadline.saturating_duration_since(std::time::Instant::now()))
                    .unwrap(),
            );
        }
        fs::write(&path, b"retry\xff\0partial").unwrap();
        expect(
            &data_rx,
            &mut bytes,
            b"start\nappend\nT\nreplacement\nretry\xff\0partial",
        );
        closed.store(true, Ordering::SeqCst);
        assert_eq!(
            worker.1.take().unwrap().join().unwrap().unwrap_err().kind(),
            io::ErrorKind::BrokenPipe
        );
    }

    #[cfg(unix)]
    #[test]
    fn image_probe_limits_rows_and_reaps_its_child_on_success_and_broken_pipe() {
        struct Closed;
        impl Write for Closed {
            fn write(&mut self, _: &[u8]) -> io::Result<usize> {
                Err(io::Error::new(
                    io::ErrorKind::BrokenPipe,
                    "closed test output",
                ))
            }
            fn flush(&mut self) -> io::Result<()> {
                Ok(())
            }
        }
        for broken in [false, true] {
            let directory = temp();
            let pid = directory.path().join("image.pid");
            let script = directory.path().join("image.sh");
            fs::write(
                &script,
                format!(
                    "echo $$ > '{}'\nwhile :; do printf 'image\\n'; done\n",
                    pid.display()
                ),
            )
            .unwrap();
            let mut settings = settings();
            settings.image_command_prefix = vec!["/bin/sh".into()];
            settings.max_images = 3;
            let policy = Policy {
                settings,
                engine: script.display().to_string(),
                image: "registry.example/operator:tag".into(),
            };
            if broken {
                assert_eq!(
                    emit_images(&policy, &mut Closed).unwrap_err().kind(),
                    io::ErrorKind::BrokenPipe
                );
            } else {
                let mut output = Vec::new();
                emit_images(&policy, &mut output).unwrap();
                assert_eq!(output, b"image\nimage\nimage\n");
            }
            let child: u32 = fs::read_to_string(pid).unwrap().trim().parse().unwrap();
            assert!(
                !Path::new("/proc").join(child.to_string()).exists(),
                "owned image child was not reaped"
            );
        }
    }

    #[test]
    fn process_inventory_uses_selected_directory_and_pattern() {
        let directory = temp();
        let mut settings = settings();
        settings.process_directory = directory.path().to_owned();
        settings.process_pattern = "operator-build".into();
        for (pid, command) in [
            ("4294967294", b"operator-build\0--fixture\0".as_slice()),
            ("4294967293", b"unrelated\0".as_slice()),
        ] {
            fs::create_dir(directory.path().join(pid)).unwrap();
            fs::write(directory.path().join(pid).join("cmdline"), command).unwrap();
        }
        assert_eq!(
            processes(&settings),
            vec![(4294967294, "operator-build --fixture".into())]
        );
    }
}
