// AI-hint: Native Linux secret management, desktop prompts, Keyrings interface, and automated pipeline safety scanning for miosd.
// AI-related: src/mios-rs/miosd/src/main.rs, usr/libexec/mios/mios-keyring-autounlock, usr/share/mios/mios.toml

use std::fs;
use std::io::Write;
use std::path::Path;
use std::process::{Command, Stdio};

/// Securely prompts for a password/secret using GUI (zenity/pinentry) when available,
/// or console TTY with terminal echo disabled.
pub fn prompt(
    title: &str,
    message: &str,
    gui: bool,
    tty: bool,
) -> Result<String, Box<dyn std::error::Error>> {
    let has_gui_env =
        std::env::var_os("DISPLAY").is_some() || std::env::var_os("WAYLAND_DISPLAY").is_some();
    let try_gui = gui || (!tty && has_gui_env);

    if try_gui {
        if let Ok(secret) = prompt_gui(title, message) {
            return Ok(secret);
        }
    }

    prompt_tty(message)
}

/// Prompt via GUI dialog (Quickshell, zenity, or pinentry).
fn prompt_gui(title: &str, message: &str) -> Result<String, Box<dyn std::error::Error>> {
    // 1. Try Quickshell native Wayland prompt if available
    let qml_path = "/usr/share/mios/quickshell/SecretPrompt.qml";
    if Path::new(qml_path).exists() && std::env::var_os("WAYLAND_DISPLAY").is_some() {
        if let Ok(child) = Command::new("quickshell")
            .arg("-p")
            .arg(qml_path)
            .env("MIOS_SECRET_PROMPT_TITLE", title)
            .env("MIOS_SECRET_PROMPT_MSG", message)
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
        {
            if let Ok(output) = child.wait_with_output() {
                if output.status.success() && !output.stdout.is_empty() {
                    let s = String::from_utf8_lossy(&output.stdout);
                    let trimmed = s.trim_end_matches(['\r', '\n']).to_string();
                    if !trimmed.is_empty() {
                        return Ok(trimmed);
                    }
                }
            }
        }
    }

    // 2. Try zenity
    if let Ok(child) = Command::new("zenity")
        .arg("--password")
        .arg(format!("--title={}", title))
        .arg(format!("--text={}", message))
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
    {
        let output = child.wait_with_output()?;
        if output.status.success() {
            let s = String::from_utf8(output.stdout)?;
            let trimmed = s.trim_end_matches(['\r', '\n']).to_string();
            return Ok(trimmed);
        }
    }

    // 2. Try pinentry
    for pinentry_bin in &["pinentry-gnome3", "pinentry"] {
        if let Ok(mut child) = Command::new(pinentry_bin)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
        {
            if let Some(mut stdin) = child.stdin.take() {
                let script = format!(
                    "SETTITLE {}\nSETDESC {}\nSETPROMPT Password:\nGETPIN\nBYE\n",
                    title, message
                );
                let _ = stdin.write_all(script.as_bytes());
            }
            let output = child.wait_with_output()?;
            if output.status.success() {
                let text = String::from_utf8_lossy(&output.stdout);
                for line in text.lines() {
                    if let Some(pin) = line.strip_prefix("D ") {
                        return Ok(pin.to_string());
                    }
                }
            }
        }
    }

    Err("no GUI prompt available or prompt dismissed".into())
}

/// Prompt via console TTY with terminal echo disabled.
fn prompt_tty(message: &str) -> Result<String, Box<dyn std::error::Error>> {
    eprint!("{}", message);
    let _ = std::io::stderr().flush();

    let secret = read_password_no_echo()?;
    eprintln!(); // newline after secret input
    Ok(secret)
}

/// Read password from stdin or /dev/tty with echo disabled via termios.
fn read_password_no_echo() -> Result<String, Box<dyn std::error::Error>> {
    #[cfg(unix)]
    {
        use std::os::unix::io::AsRawFd;

        let tty_file = fs::OpenOptions::new()
            .read(true)
            .write(true)
            .open("/dev/tty");

        let (fd, mut reader): (i32, Box<dyn BufRead>) = match tty_file {
            Ok(file) => {
                let raw_fd = file.as_raw_fd();
                (raw_fd, Box::new(std::io::BufReader::new(file)))
            }
            Err(_) => {
                let stdin = std::io::stdin();
                (stdin.as_raw_fd(), Box::new(stdin.lock()))
            }
        };

        let mut termios = std::mem::MaybeUninit::<libc::termios>::uninit();
        let is_tty = unsafe { libc::isatty(fd) == 1 };

        if is_tty {
            unsafe {
                if libc::tcgetattr(fd, termios.as_mut_ptr()) == 0 {
                    let mut raw = termios.assume_init();
                    let orig = raw;
                    raw.c_lflag &= !(libc::ECHO | libc::ECHONL);
                    libc::tcsetattr(fd, libc::TCSANOW, &raw);

                    struct TermiosReset {
                        fd: i32,
                        orig: libc::termios,
                    }
                    impl Drop for TermiosReset {
                        fn drop(&mut self) {
                            unsafe {
                                libc::tcsetattr(self.fd, libc::TCSANOW, &self.orig);
                            }
                        }
                    }

                    let _guard = TermiosReset { fd, orig };
                    let mut input = String::new();
                    reader.read_line(&mut input)?;
                    return Ok(input.trim_end_matches(['\r', '\n']).to_string());
                }
            }
        }

        let mut input = String::new();
        reader.read_line(&mut input)?;
        Ok(input.trim_end_matches(['\r', '\n']).to_string())
    }

    #[cfg(not(unix))]
    {
        let mut input = String::new();
        std::io::stdin().read_line(&mut input)?;
        Ok(input.trim_end_matches(['\r', '\n']).to_string())
    }
}

/// Store secret in native Linux Keyring via secret-tool (Secret Service) or safe fallback.
pub fn set(service: &str, key: &str, value: &str) -> Result<(), Box<dyn std::error::Error>> {
    let child = Command::new("secret-tool")
        .arg("store")
        .arg(format!("--label=MiOS Secret [{}/{}]", service, key))
        .arg("service")
        .arg(service)
        .arg("key")
        .arg(key)
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .spawn();

    if let Ok(mut c) = child {
        if let Some(mut stdin) = c.stdin.take() {
            stdin.write_all(value.as_bytes())?;
        }
        let status = c.wait()?;
        if status.success() {
            return Ok(());
        }
    }

    // Fallback: secure local keyring store in /run/user/<uid>/mios/keyring
    let keyring_dir = get_keyring_fallback_dir(service)?;
    fs::create_dir_all(&keyring_dir)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = fs::set_permissions(&keyring_dir, fs::Permissions::from_mode(0o700));
    }

    let secret_file = keyring_dir.join(key);
    fs::write(&secret_file, value)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = fs::set_permissions(&secret_file, fs::Permissions::from_mode(0o600));
    }

    Ok(())
}

/// Retrieve secret from native Linux Keyring via secret-tool or safe fallback.
pub fn get(service: &str, key: &str) -> Result<String, Box<dyn std::error::Error>> {
    let output = Command::new("secret-tool")
        .arg("lookup")
        .arg("service")
        .arg(service)
        .arg("key")
        .arg(key)
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .output();

    if let Ok(out) = output {
        if out.status.success() && !out.stdout.is_empty() {
            let val = String::from_utf8(out.stdout)?;
            return Ok(val.trim_end_matches(['\r', '\n']).to_string());
        }
    }

    // Fallback lookup
    let keyring_dir = get_keyring_fallback_dir(service)?;
    let secret_file = keyring_dir.join(key);
    if secret_file.is_file() {
        let content = fs::read_to_string(&secret_file)?;
        return Ok(content.trim_end_matches(['\r', '\n']).to_string());
    }

    Err(format!("secret not found for service='{}', key='{}'", service, key).into())
}

fn get_keyring_fallback_dir(
    service: &str,
) -> Result<std::path::PathBuf, Box<dyn std::error::Error>> {
    let mut cand = None;
    if let Ok(run_dir) = std::env::var("XDG_RUNTIME_DIR") {
        if !run_dir.is_empty() {
            cand = Some(std::path::PathBuf::from(run_dir));
        }
    }
    if cand.is_none() {
        #[cfg(unix)]
        let uid = unsafe { libc::getuid() };
        #[cfg(not(unix))]
        let uid = 1000u32;
        let run_user = std::path::PathBuf::from(format!("/run/user/{}", uid));
        if run_user.is_dir() {
            cand = Some(run_user);
        } else if let Ok(home) = std::env::var("HOME") {
            cand = Some(std::path::PathBuf::from(home).join(".local").join("share"));
        } else {
            cand = Some(std::env::temp_dir().join(format!("mios-{}", uid)));
        }
    }
    let base = cand.unwrap_or_else(std::env::temp_dir);
    Ok(base.join("mios").join("keyring").join(service))
}

/// Pipeline safety net: recursively scans files for unshielded credentials, private keys, or tokens.
pub fn scan(target: &Path, strict: bool) -> Result<usize, Box<dyn std::error::Error>> {
    let mut findings = 0;
    let mut files = Vec::new();

    collect_files(target, &mut files)?;

    let priv_key_re = regex::Regex::new(r"-----BEGIN (?:[A-Z0-9_-]+ )?PRIVATE KEY-----")?;
    let ghp_token_re = regex::Regex::new(r"\bgh[pousr]_[A-Za-z0-9_]{36,}\b")?;
    let openai_re = regex::Regex::new(r"\bsk-(?:proj-|live-)?[a-zA-Z0-9_-]{32,}\b")?;
    let aws_re = regex::Regex::new(r"\bAKIA[0-9A-Z]{16}\b")?;
    let hardcoded_pwd_re = regex::Regex::new(
        r#"(?i)^\s*(?:export\s+)?([A-Za-z0-9_]*(?:PASSWORD|SECRET|API_KEY))\s*=\s*["']([^"'$%{\s]{6,})["']"#,
    )?;

    for path in files {
        let content = match fs::read_to_string(&path) {
            Ok(c) => c,
            Err(_) => continue, // ignore non-UTF-8 binary files
        };

        for (lineno, line) in content.lines().enumerate() {
            if line.trim_start().starts_with('#') || line.trim_start().starts_with("//") {
                continue;
            }

            if priv_key_re.is_match(line) {
                eprintln!(
                    "[pipeline-safety-net] {}:{} UNSHIELDED PRIVATE KEY DETECTED",
                    path.display(),
                    lineno + 1
                );
                findings += 1;
            } else if ghp_token_re.is_match(line) {
                eprintln!(
                    "[pipeline-safety-net] {}:{} PLAINTEXT GITHUB TOKEN DETECTED",
                    path.display(),
                    lineno + 1
                );
                findings += 1;
            } else if openai_re.is_match(line) {
                eprintln!(
                    "[pipeline-safety-net] {}:{} PLAINTEXT API TOKEN DETECTED",
                    path.display(),
                    lineno + 1
                );
                findings += 1;
            } else if aws_re.is_match(line) {
                eprintln!(
                    "[pipeline-safety-net] {}:{} PLAINTEXT AWS KEY DETECTED",
                    path.display(),
                    lineno + 1
                );
                findings += 1;
            } else if strict {
                if let Some(caps) = hardcoded_pwd_re.captures(line) {
                    let key = caps.get(1).map_or("", |m| m.as_str());
                    eprintln!(
                        "[pipeline-safety-net] {}:{} HARDCODED CREDENTIAL '{}' DETECTED -- store via miosd secret set",
                        path.display(),
                        lineno + 1,
                        key
                    );
                    findings += 1;
                }
            }
        }
    }

    Ok(findings)
}

fn collect_files(dir: &Path, out: &mut Vec<std::path::PathBuf>) -> Result<(), std::io::Error> {
    if dir.is_file() {
        out.push(dir.to_path_buf());
        return Ok(());
    }

    if !dir.is_dir() {
        return Ok(());
    }

    for entry in fs::read_dir(dir)? {
        let entry = entry?;
        let p = entry.path();
        let fname = entry.file_name().to_string_lossy().to_string();

        if fname.starts_with('.') || fname == "target" || fname == "node_modules" {
            continue;
        }

        if p.is_dir() {
            collect_files(&p, out)?;
        } else if p.is_file() {
            out.push(p);
        }
    }

    Ok(())
}

#[cfg(test)]
mod tests {
    // Test code: a panic here IS the assertion. Scoped so production code stays under the lint.
    #![allow(clippy::unwrap_used)]

    use super::*;
    use tempfile::tempdir;

    #[test]
    fn test_secret_fallback_store_and_get() {
        let tmp = tempdir().unwrap();
        let old_run = std::env::var("XDG_RUNTIME_DIR").ok();
        std::env::set_var("XDG_RUNTIME_DIR", tmp.path());

        let res_set = set("test-service", "my-api-key", "super-secret-token-123");
        assert!(res_set.is_ok(), "set should succeed: {:?}", res_set);

        let res_get = get("test-service", "my-api-key");
        assert_eq!(
            res_get.unwrap(),
            "super-secret-token-123",
            "retrieved secret should match"
        );

        if let Some(r) = old_run {
            std::env::set_var("XDG_RUNTIME_DIR", r);
        }
    }

    #[test]
    fn test_scan_detects_unshielded_private_key() {
        let tmp = tempdir().unwrap();
        let test_file = tmp.path().join("leaked_key.pem");
        fs::write(
            &test_file,
            "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA...\n-----END RSA PRIVATE KEY-----\n",
        )
        .unwrap();

        let count = scan(tmp.path(), false).unwrap();
        assert_eq!(count, 1, "scanner must catch unshielded RSA private key");
    }

    #[test]
    fn test_scan_detects_tokens() {
        let tmp = tempdir().unwrap();
        let test_file = tmp.path().join("config.sh");
        fs::write(
            &test_file,
            format!(
                "export GITHUB_TOKEN=ghp_{}\n",
                "abcdefghijklmnopqrstuvwxyz0123456789"
            ),
        )
        .unwrap();

        let count = scan(tmp.path(), false).unwrap();
        assert_eq!(count, 1, "scanner must catch plaintext GitHub token");
    }

    #[test]
    fn test_scan_strict_detects_hardcoded_secret() {
        let tmp = tempdir().unwrap();
        let test_file = tmp.path().join("service.conf");
        fs::write(
            &test_file,
            "DATABASE_PASSWORD=\"very_secret_cleartext_password\"\n",
        )
        .unwrap();

        let count_strict = scan(tmp.path(), true).unwrap();
        assert_eq!(
            count_strict, 1,
            "strict scanner must catch hardcoded cleartext password"
        );

        let count_normal = scan(tmp.path(), false).unwrap();
        assert_eq!(
            count_normal, 0,
            "normal scanner should ignore without strict"
        );
    }
}
