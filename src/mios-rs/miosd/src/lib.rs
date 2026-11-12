// AI-hint: Library module exports for miosd crate.
// AI-related: src/mios-rs/miosd/src/main.rs, tools/native/mios-gen, automation/33-generate-quadlets.sh, usr/libexec/mios/mios-keyring-autounlock, usr/share/mios/mios.toml, usr/share/mios/configurator/mios.html, /usr/libexec/mios/mios-configurator-launch

// Same robustness lints as the binary target: without these the lint only
// covered `mod drift` reached via main.rs, so lib-only modules were never
// checked.
#![warn(clippy::unwrap_used, clippy::panic, clippy::todo)]

pub mod cli;
pub mod daemon;
pub mod drift;
pub mod fhs_sync;
pub mod rde;
#[cfg(unix)]
pub mod wallpaper;
pub mod native_generator {
    use std::path::{Path, PathBuf};
    use std::process::Command;

    fn default_candidates(root: &Path, name: &str, executable: Option<&Path>) -> Vec<PathBuf> {
        let installed = [
            root.join("usr/bin").join(name),
            root.join("usr/libexec/mios").join(name),
        ];
        let checkout = root.join("tools/native/target/release").join(name);
        let system_root = !cfg!(windows)
            && root
                .canonicalize()
                .is_ok_and(|resolved| resolved == Path::new("/"));
        let mut candidates = Vec::new();
        if system_root {
            candidates.extend(installed);
            candidates.push(checkout);
        } else {
            candidates.push(checkout);
            candidates.extend(installed);
        }
        if let Some(parent) = executable.and_then(Path::parent) {
            candidates.push(parent.join(name));
        }
        candidates.extend([
            Path::new("/usr/bin").join(name),
            Path::new("/usr/libexec/mios").join(name),
        ]);
        candidates
    }

    fn command_with_paths(
        root: &Path,
        verb: &str,
        check: bool,
        explicit: Option<PathBuf>,
        catalog: Option<PathBuf>,
        executable: Option<PathBuf>,
    ) -> Result<Command, String> {
        let name = if cfg!(windows) {
            "mios-gen.exe"
        } else {
            "mios-gen"
        };
        let (candidates, configured) = if let Some(explicit) = explicit {
            (vec![explicit], Some("MIOS_GEN_BIN"))
        } else if let Some(catalog) = catalog {
            (vec![catalog.join(name)], Some("MIOS_NATIVE_BIN_DIR"))
        } else {
            (default_candidates(root, name, executable.as_deref()), None)
        };
        let binary = candidates.into_iter().find(|path| path.is_file()).ok_or_else(|| {
            if let Some(source) = configured {
                format!("{verb}: configured {source} native {name} is missing; no fallback is permitted")
            } else {
                format!("{verb}: native {name} is required; build/install the SSOT release catalog")
            }
        })?;
        let mut command = Command::new(binary);
        command
            .arg(verb)
            .arg("--root")
            .arg(root)
            .env("MIOS_ROOT", root);
        if check {
            command.arg("--check");
        }
        Ok(command)
    }

    pub fn command(root: &Path, verb: &str, check: bool) -> Result<Command, String> {
        command_with_paths(
            root,
            verb,
            check,
            std::env::var_os("MIOS_GEN_BIN").map(PathBuf::from),
            std::env::var_os("MIOS_NATIVE_BIN_DIR")
                .filter(|path| !path.is_empty())
                .map(PathBuf::from),
            std::env::current_exe().ok(),
        )
    }

    pub fn run(root: &Path, verb: &str, check: bool) -> Result<(), Box<dyn std::error::Error>> {
        let status = command(root, verb, check)?.status()?;
        if !status.success() {
            return Err(format!("native mios-gen {verb} failed: {status}").into());
        }
        Ok(())
    }

    #[cfg(all(test, unix))]
    mod tests {
        use super::*;
        use std::os::unix::fs::PermissionsExt;

        fn tool(path: &Path, marker: &str) {
            std::fs::create_dir_all(path.parent().expect("generator fixture parent"))
                .expect("create generator fixture directory");
            std::fs::write(
                path,
                format!("#!/bin/sh\nprintf '%s\\n' '{marker}'\nprintf '%s\\n' \"$@\"\n"),
            )
            .expect("write generator fixture");
            std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o755))
                .expect("make generator fixture executable");
        }

        #[test]
        fn fresh_catalog_dispatch_beats_owned_stale_checkout_and_missing_catalog_refuses() {
            let fixture = tempfile::tempdir().unwrap();
            let root = fixture.path().join("selected source");
            let stale = root.join("tools/native/target/release/mios-gen");
            tool(&stale, "stale-checkout");
            let catalog = fixture.path().join("fresh catalog");
            let fresh = catalog.join("mios-gen");
            tool(&fresh, "fresh-catalog");
            let mut command =
                command_with_paths(&root, "render-greetd", true, None, Some(catalog), None)
                    .unwrap();
            assert_eq!(command.get_program(), fresh.as_os_str());
            assert!(command
                .get_envs()
                .any(|(key, value)| key == "MIOS_ROOT" && value == Some(root.as_os_str())));
            let output = command.output().unwrap();
            assert!(output.status.success());
            assert_eq!(
                String::from_utf8(output.stdout).unwrap(),
                format!(
                    "fresh-catalog\nrender-greetd\n--root\n{}\n--check\n",
                    root.display()
                )
            );
            let error = command_with_paths(
                &root,
                "render-greetd",
                false,
                None,
                Some(fixture.path().join("missing catalog")),
                None,
            )
            .unwrap_err();
            assert!(error.contains("MIOS_NATIVE_BIN_DIR") && error.contains("no fallback"));
            assert!(stale.is_file());
        }

        #[test]
        fn explicit_generator_keeps_highest_exclusive_precedence_over_catalog() {
            let fixture = tempfile::tempdir().unwrap();
            let root = fixture.path().join("source");
            tool(&root.join("tools/native/target/release/mios-gen"), "stale");
            let catalog = fixture.path().join("catalog");
            tool(&catalog.join("mios-gen"), "catalog");
            let explicit = fixture.path().join("chosen-generator");
            tool(&explicit, "explicit");
            let mut selected = command_with_paths(
                &root,
                "tmux-os",
                false,
                Some(explicit.clone()),
                Some(catalog.clone()),
                None,
            )
            .unwrap();
            assert_eq!(selected.get_program(), explicit.as_os_str());
            let output = selected.output().unwrap();
            assert!(output.status.success());
            assert!(String::from_utf8(output.stdout)
                .unwrap()
                .starts_with("explicit\n"));
            let error = command_with_paths(
                &root,
                "tmux-os",
                false,
                Some(fixture.path().join("missing-explicit")),
                Some(catalog),
                None,
            )
            .unwrap_err();
            assert!(error.contains("MIOS_GEN_BIN") && error.contains("no fallback"));
        }

        #[test]
        fn non_system_checkout_and_current_executable_fallback_order_is_preserved() {
            let fixture = tempfile::tempdir().unwrap();
            let root = fixture.path().join("source");
            let stale = root.join("tools/native/target/release/mios-gen");
            tool(&stale, "development");
            let installed = root.join("usr/bin/mios-gen");
            tool(&installed, "root-installed");
            let sibling = fixture.path().join("sibling/mios-gen");
            tool(&sibling, "executable-sibling");
            let executable = sibling.parent().unwrap().join("miosd");
            assert_eq!(
                command_with_paths(&root, "sync", false, None, None, Some(executable.clone()))
                    .unwrap()
                    .get_program(),
                stale.as_os_str()
            );
            std::fs::remove_file(&stale).unwrap();
            assert_eq!(
                command_with_paths(&root, "sync", false, None, None, Some(executable.clone()))
                    .unwrap()
                    .get_program(),
                installed.as_os_str()
            );
            std::fs::remove_file(&installed).unwrap();
            assert_eq!(
                command_with_paths(&root, "sync", false, None, None, Some(executable))
                    .unwrap()
                    .get_program(),
                sibling.as_os_str()
            );
        }

        #[test]
        fn actual_system_root_and_its_alias_put_installed_paths_before_checkout() {
            let root = Path::new("/");
            let paths = default_candidates(root, "mios-gen", None);
            assert_eq!(
                &paths[..3],
                &[
                    root.join("usr/bin/mios-gen"),
                    root.join("usr/libexec/mios/mios-gen"),
                    root.join("tools/native/target/release/mios-gen")
                ]
            );
            let fixture = tempfile::tempdir().unwrap();
            let alias = fixture.path().join("system-alias");
            std::os::unix::fs::symlink("/", &alias).unwrap();
            let paths = default_candidates(&alias, "mios-gen", None);
            assert_eq!(
                &paths[..3],
                &[
                    alias.join("usr/bin/mios-gen"),
                    alias.join("usr/libexec/mios/mios-gen"),
                    alias.join("tools/native/target/release/mios-gen")
                ]
            );
        }
    }
}
pub mod secret {
    use std::fs;
    #[cfg(unix)]
    use std::io::BufRead;
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
            assert!(res_set.is_ok(), "secret storage should succeed");

            let res_get = get("test-service", "my-api-key");
            assert!(
                res_get.is_ok_and(|value| value == "super-secret-token-123"),
                "retrieved secret should match without logging its contents"
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
}
pub mod server {
    use std::collections::HashMap;
    use std::fs;
    use std::path::{Path, PathBuf};
    use std::sync::Arc;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    use tokio::net::{TcpListener, TcpStream};

    #[derive(Debug, Clone)]
    pub struct ConfigServerConfig {
        pub bind_addr: String,
        pub html_path: PathBuf,
        /// The user tier operator saves land in (resolver's user path).
        pub user_toml_path: PathBuf,
        /// [portal].config_max_body_bytes.
        pub max_body_bytes: usize,
        /// Root the six-tier SSOT resolves under; None is the installed FHS tiers
        /// (or the source tree's vendor file when MiOS is not installed).
        pub ssot_root: Option<PathBuf>,
        /// [security].allowlist_hosts from SSOT.
        pub allowlist_hosts: Vec<String>,
        /// [portal].require_login.
        pub require_login: bool,
        /// Per-launch session token.
        pub session_token: Option<String>,
    }

    impl ConfigServerConfig {
        /// The listen port is `[ports].agent_pipe` (the route agent-pipe's portal
        /// serves) unless given; an unresolvable port is an error, never a literal.
        pub fn resolve(bind: Option<String>, port: Option<u16>) -> Result<Self, String> {
            Self::resolve_with_auth(bind, port, None, None)
        }

        pub fn resolve_with_auth(
            bind: Option<String>,
            port: Option<u16>,
            token: Option<String>,
            token_file: Option<PathBuf>,
        ) -> Result<Self, String> {
            let bind_addr = match bind.or_else(|| std::env::var("MIOS_CONFIG_BIND_ADDR").ok()) {
                Some(b) => b,
                None => {
                    let port = match port {
                        Some(p) => p,
                        None => mios_resolver::runtime::require_port("MIOS_PORTS_AGENT_PIPE")
                            .map_err(|e| e.to_string())?,
                    };
                    format!("127.0.0.1:{port}")
                }
            };

            let root = std::env::var("MIOS_ROOT").unwrap_or_else(|_| ".".to_string());
            let root_path = Path::new(&root);

            let html_path = if let Ok(custom) = std::env::var("MIOS_CONFIGURATOR_HTML") {
                PathBuf::from(custom)
            } else if Path::new("/usr/share/mios/configurator/mios.html").is_file() {
                PathBuf::from("/usr/share/mios/configurator/mios.html")
            } else if root_path
                .join("usr/share/mios/configurator/mios.html")
                .is_file()
            {
                root_path.join("usr/share/mios/configurator/mios.html")
            } else {
                PathBuf::from("usr/share/mios/configurator/mios.html")
            };

            let ssot_root = std::env::var("MIOS_ROOT")
                .ok()
                .filter(|r| !r.is_empty())
                .map(PathBuf::from);
            let user_toml_path = mios_resolver::user_toml_path(ssot_root.as_deref());
            let raw_max = mios_resolver::runtime::require("MIOS_PORTAL_CONFIG_MAX_BODY_BYTES")
                .map_err(|e| e.to_string())?;
            let max_body_bytes = raw_max.parse::<usize>().map_err(|_| {
                format!("MIOS_PORTAL_CONFIG_MAX_BODY_BYTES={raw_max} is not a byte count")
            })?;

            let merged = mios_resolver::resolve_merged(ssot_root.as_deref(), false).ok();

            let allowlist_hosts = merged
                .as_ref()
                .and_then(|m| m.get("security"))
                .and_then(|s| s.get("allowlist_hosts"))
                .and_then(|v| v.as_array())
                .map(|arr| {
                    arr.iter()
                        .filter_map(|v| v.as_str().map(|s| s.to_string()))
                        .collect::<Vec<_>>()
                })
                .filter(|v| !v.is_empty())
                .unwrap_or_else(|| {
                    vec![
                        "localhost".to_string(),
                        "127.0.0.1".to_string(),
                        "::1".to_string(),
                        "host.containers.internal".to_string(),
                        "mios-llm-light".to_string(),
                        "mios-open-webui".to_string(),
                        "mios-hermes".to_string(),
                        "mios-pgvector".to_string(),
                        "mios-forge".to_string(),
                        "mios-searxng".to_string(),
                        "mios-crawl4ai".to_string(),
                        "mios-code-server".to_string(),
                    ]
                });

            let require_login = merged
                .as_ref()
                .and_then(|m| m.get("portal"))
                .and_then(|p| p.get("require_login"))
                .and_then(|v| v.as_bool())
                .unwrap_or(true);

            let session_token = token
                .or_else(|| std::env::var("MIOS_PORTAL_SESSION_TOKEN").ok())
                .or_else(|| std::env::var("MIOS_PORTAL_TOKEN").ok())
                .or_else(|| {
                    token_file
                        .and_then(|p| fs::read_to_string(p).ok().map(|s| s.trim().to_string()))
                })
                .or_else(|| {
                    let runtime_dir =
                        std::env::var("XDG_RUNTIME_DIR").unwrap_or_else(|_| "/tmp".to_string());
                    let path = PathBuf::from(runtime_dir).join("mios/portal-token");
                    fs::read_to_string(path).ok().map(|s| s.trim().to_string())
                })
                .or_else(|| {
                    if require_login {
                        Some(generate_session_token())
                    } else {
                        None
                    }
                });

            Ok(Self {
                bind_addr,
                html_path,
                user_toml_path,
                max_body_bytes,
                ssot_root,
                allowlist_hosts,
                require_login,
                session_token,
            })
        }
    }

    fn generate_session_token() -> String {
        use std::time::SystemTime;
        let now = SystemTime::now()
            .duration_since(SystemTime::UNIX_EPOCH)
            .unwrap_or_default();
        let pid = std::process::id();
        let mut state: u64 = now.as_nanos() as u64 ^ ((pid as u64) << 32);
        if state == 0 {
            state = 0x9e3779b97f4a7c15;
        }
        let mut token = String::with_capacity(64);
        for _ in 0..4 {
            state ^= state << 13;
            state ^= state >> 7;
            state ^= state << 17;
            token.push_str(&format!("{:016x}", state));
        }
        token
    }

    #[derive(Debug, Clone, PartialEq, Eq)]
    pub struct HttpRequest {
        pub method: String,
        pub path: String,
        pub query: Option<String>,
        pub headers: HashMap<String, String>,
        pub body: Vec<u8>,
    }

    #[derive(Debug, Clone)]
    pub struct HttpResponse {
        pub status_code: u16,
        pub status_text: &'static str,
        pub headers: Vec<(String, String)>,
        pub body: Vec<u8>,
    }

    impl HttpResponse {
        pub fn new(status_code: u16, status_text: &'static str) -> Self {
            Self {
                status_code,
                status_text,
                headers: vec![
                    ("Server".to_string(), "miosd-config-server".to_string()),
                    ("Connection".to_string(), "close".to_string()),
                ],
                body: Vec::new(),
            }
        }

        pub fn with_header(mut self, key: &str, value: &str) -> Self {
            self.headers.push((key.to_string(), value.to_string()));
            self
        }

        pub fn with_body(mut self, content_type: &str, body: Vec<u8>) -> Self {
            self.headers
                .push(("Content-Type".to_string(), content_type.to_string()));
            self.headers
                .push(("Content-Length".to_string(), body.len().to_string()));
            self.body = body;
            self
        }

        pub fn html(status_code: u16, status_text: &'static str, html: String) -> Self {
            Self::new(status_code, status_text)
                .with_body("text/html; charset=utf-8", html.into_bytes())
        }

        pub fn json(status_code: u16, status_text: &'static str, json_str: String) -> Self {
            Self::new(status_code, status_text)
                .with_body("application/json; charset=utf-8", json_str.into_bytes())
        }

        pub fn toml(status_code: u16, status_text: &'static str, toml_str: String) -> Self {
            Self::new(status_code, status_text)
                .with_body("application/toml; charset=utf-8", toml_str.into_bytes())
        }

        pub fn redirect(location: &str) -> Self {
            Self::new(302, "Found")
                .with_header("Location", location)
                .with_body(
                    "text/plain",
                    format!("Redirecting to {}", location).into_bytes(),
                )
        }

        pub fn to_bytes(&self) -> Vec<u8> {
            let mut out = Vec::new();
            let status_line = format!("HTTP/1.1 {} {}\r\n", self.status_code, self.status_text);
            out.extend_from_slice(status_line.as_bytes());

            for (k, v) in &self.headers {
                let header_line = format!("{}: {}\r\n", k, v);
                out.extend_from_slice(header_line.as_bytes());
            }
            out.extend_from_slice(b"\r\n");
            out.extend_from_slice(&self.body);
            out
        }
    }

    pub fn parse_http_request(raw: &[u8]) -> Option<HttpRequest> {
        let mut headers_end = 0;
        for i in 0..raw.len().saturating_sub(3) {
            if &raw[i..i + 4] == b"\r\n\r\n" {
                headers_end = i;
                break;
            }
        }
        if headers_end == 0 {
            return None;
        }

        let header_str = std::str::from_utf8(&raw[..headers_end]).ok()?;
        let mut lines = header_str.lines();
        let request_line = lines.next()?;
        let mut req_parts = request_line.split_whitespace();
        let method = req_parts.next()?.to_string();
        let raw_path = req_parts.next()?.to_string();

        let (path, query) = if let Some(idx) = raw_path.find('?') {
            (
                raw_path[..idx].to_string(),
                Some(raw_path[idx + 1..].to_string()),
            )
        } else {
            (raw_path, None)
        };

        let mut headers = HashMap::new();
        for line in lines {
            if let Some((k, v)) = line.split_once(':') {
                headers.insert(k.trim().to_ascii_lowercase(), v.trim().to_string());
            }
        }

        let body_start = headers_end + 4;
        let mut body = if body_start < raw.len() {
            raw[body_start..].to_vec()
        } else {
            Vec::new()
        };

        if let Some(len_str) = headers.get("content-length") {
            if let Ok(expected_len) = len_str.parse::<usize>() {
                if body.len() > expected_len {
                    body.truncate(expected_len);
                }
            }
        }

        Some(HttpRequest {
            method,
            path,
            query,
            headers,
            body,
        })
    }

    /// The live configuration: the resolver's six-tier merge (vendor < vendor.d <
    /// host < host.d < user < user.d, [ports] derived), serialized as one valid
    /// TOML document. Splicing the tier files' text together produced duplicate
    /// tables whenever a host override repeated a vendor table.
    pub fn read_layered_toml(config: &ConfigServerConfig) -> Result<String, String> {
        let mut merged = mios_resolver::resolve_merged(config.ssot_root.as_deref(), false)
            .map_err(|_| "the layered mios.toml did not resolve".to_string())?;
        redact_secrets(&mut merged);
        toml::to_string(&merged).map_err(|_| "the merged config did not serialize".to_string())
    }

    const INVALID_POSTED_TOML: &str = "Invalid TOML: the posted document did not parse";

    /// What GET /portal/config serves in place of every secret value.
    pub const REDACTED: &str = "<redacted>";

    /// The error a save carrying [`REDACTED`] gets when the current tiers do not resolve.
    pub const UNRESOLVED_REFUSAL: &str = "Refusing the save: it carries <redacted> markers but the current config did not resolve, so the values they stand for are unknown; nothing was written";

    /// The error a save gets when a marker sits inside an array that does not
    /// exactly echo the redacted destination prefix, so no element can be
    /// attributed to the secret it stands for. Nothing is written, no index
    /// identity is guessed and no inherited credential is copied upward.
    pub const AMBIGUOUS_ARRAY_REFUSAL: &str = "Refusing the save: it carries <redacted> markers inside an array that does not exactly echo the redacted destination configuration, so no element can be attributed to the secret it stands for; nothing was written";

    pub fn redact_secrets(merged: &mut toml::Value) {
        if let Some(portal) = merged.get_mut("portal").and_then(toml::Value::as_table_mut) {
            if portal.contains_key("password") {
                portal.insert(
                    "password".to_string(),
                    toml::Value::String(REDACTED.to_string()),
                );
            }
            if portal.contains_key("secret") {
                portal.insert(
                    "secret".to_string(),
                    toml::Value::String(REDACTED.to_string()),
                );
            }
        }

        if let Some(ai) = merged.get_mut("ai").and_then(toml::Value::as_table_mut) {
            if ai.contains_key("api_key") {
                ai.insert(
                    "api_key".to_string(),
                    toml::Value::String(REDACTED.to_string()),
                );
            }
        }

        let mut secret_keys: Vec<String> = Vec::new();
        let mut secret_paths: Vec<String> = Vec::new();
        if let Some(sec) = merged.get("security") {
            if let Some(sk) = sec.get("secret_keys") {
                for (name, sink) in [("keys", &mut secret_keys), ("paths", &mut secret_paths)] {
                    if let Some(arr) = sk.get(name).and_then(|v| v.as_array()) {
                        for item in arr {
                            if let Some(s) = item.as_str() {
                                sink.push(s.to_string());
                            }
                        }
                    }
                }
            }
        }

        // [security.secret_keys].paths: a bare key name such as default_password
        // is too common to match, so those secrets are named by dotted path --
        // the same list, in the same order, as agent-pipe's redact_secrets.
        for dotted in &secret_paths {
            redact_path(merged, dotted);
        }
        redact_table_recursive(merged, &secret_keys);
    }

    /// Redact the value at a dotted path when it is present; a path that is
    /// absent, or runs through a scalar, creates and changes nothing. A segment
    /// naming an array of tables ([[autounattend.accounts]]) applies the rest of
    /// the path to every row -- the same walk as agent-pipe's _redact_path.
    fn redact_path(root: &mut toml::Value, dotted: &str) {
        let parts: Vec<&str> = dotted.split('.').collect();
        redact_parts(root, &parts);
    }

    fn redact_parts(node: &mut toml::Value, parts: &[&str]) {
        let Some((first, rest)) = parts.split_first() else {
            return;
        };
        match node {
            toml::Value::Array(rows) => {
                for row in rows.iter_mut() {
                    redact_parts(row, parts);
                }
            }
            toml::Value::Table(table) => {
                if let Some(next) = table.get_mut(*first) {
                    if rest.is_empty() {
                        *next = toml::Value::String(REDACTED.to_string());
                    } else {
                        redact_parts(next, rest);
                    }
                }
            }
            _ => {}
        }
    }

    fn redact_table_recursive(val: &mut toml::Value, secret_keys: &[String]) {
        match val {
            toml::Value::Table(table) => {
                for (k, v) in table.iter_mut() {
                    if k == "keys" && v.is_array() {
                        continue;
                    }
                    let matches_secret = secret_keys.iter().any(|sk| {
                        sk.eq_ignore_ascii_case(k)
                            || sk
                                .strip_prefix("MIOS_")
                                .is_some_and(|stripped| stripped.eq_ignore_ascii_case(k))
                    });
                    if matches_secret {
                        *v = toml::Value::String(REDACTED.to_string());
                    } else {
                        redact_table_recursive(v, secret_keys);
                    }
                }
            }
            toml::Value::Array(arr) => {
                for item in arr {
                    redact_table_recursive(item, secret_keys);
                }
            }
            _ => {}
        }
    }

    /// True when `value` holds a [`REDACTED`] marker anywhere, as a save that
    /// echoes GET /portal/config does.
    pub fn contains_redacted(value: &toml::Value) -> bool {
        match value {
            toml::Value::String(s) => s == REDACTED,
            toml::Value::Table(table) => table.values().any(contains_redacted),
            toml::Value::Array(items) => items.iter().any(contains_redacted),
            _ => false,
        }
    }

    /// Fail-closed preflight for [`restore_redacted`] on the save path: an
    /// array carrying [`REDACTED`] anywhere is attributable only when it equals
    /// `redacted_destination` element for element -- the exact redacted
    /// destination prefix -- so a validated restore may take each marker's value
    /// by position. Any other marker array (an edit, a reorder, an insertion, a
    /// nested change, or an array with no live counterpart) is ambiguous and
    /// refused. Scalar markers keep their compatible take-or-drop semantics.
    pub fn validate_marker_arrays(
        posted: &toml::Value,
        redacted_destination: &toml::Value,
    ) -> Result<(), String> {
        validate_markers_at(posted, Some(redacted_destination))
    }

    fn validate_markers_at(posted: &toml::Value, live: Option<&toml::Value>) -> Result<(), String> {
        match posted {
            toml::Value::Table(table) => {
                for (k, v) in table {
                    validate_markers_at(v, live.and_then(|l| l.get(k.as_str())))?;
                }
                Ok(())
            }
            toml::Value::Array(items) => {
                if !items.iter().any(contains_redacted) {
                    // An ordinary array edit without markers needs no attribution.
                    return Ok(());
                }
                match live {
                    Some(toml::Value::Array(expected)) if items == expected => Ok(()),
                    _ => Err(AMBIGUOUS_ARRAY_REFUSAL.to_string()),
                }
            }
            _ => Ok(()),
        }
    }

    /// Each [`REDACTED`] marker takes back the `live` value at its path, or is
    /// dropped where `live` has none. [`save_user_tier`] passes vendor..host.d
    /// plus the user tier it writes, so each secret stays in its own layer, and
    /// preflights every marker array with [`validate_marker_arrays`]: only an
    /// exact echo of the redacted destination prefix restores, everything else
    /// is refused there before any write.
    pub fn restore_redacted(posted: &mut toml::Value, live: &toml::Value) {
        restore_at(posted, Some(live));
    }

    fn restore_at(posted: &mut toml::Value, live: Option<&toml::Value>) {
        match posted {
            toml::Value::Table(table) => {
                let keys: Vec<String> = table.keys().cloned().collect();
                for k in keys {
                    let live_v = live.and_then(|l| l.get(k.as_str()));
                    if table.get(&k).and_then(toml::Value::as_str) == Some(REDACTED) {
                        match live_v {
                            Some(v) => {
                                table.insert(k, v.clone());
                            }
                            None => {
                                table.remove(&k);
                            }
                        }
                    } else if let Some(v) = table.get_mut(&k) {
                        restore_at(v, live_v);
                    }
                }
            }
            toml::Value::Array(items) => {
                let live_items = live.and_then(toml::Value::as_array);
                let mut kept = Vec::with_capacity(items.len());
                for (i, mut item) in std::mem::take(items).into_iter().enumerate() {
                    let live_item = live_items.and_then(|l| l.get(i));
                    if item.as_str() == Some(REDACTED) {
                        kept.extend(live_item.cloned());
                        continue;
                    }
                    restore_at(&mut item, live_item);
                    kept.push(item);
                }
                *items = kept;
            }
            _ => {}
        }
    }

    pub fn validate_host(host_header: &str, allowlist: &[String]) -> bool {
        let host_trimmed = host_header.trim();
        if host_trimmed.is_empty() {
            return false;
        }
        let hostname = if host_trimmed.starts_with('[') {
            if let Some(end_bracket) = host_trimmed.find(']') {
                &host_trimmed[1..end_bracket]
            } else {
                return false;
            }
        } else {
            host_trimmed.split(':').next().unwrap_or(host_trimmed)
        };

        let hostname_lower = hostname.to_ascii_lowercase();
        for allowed in allowlist {
            if hostname_lower == allowed.to_ascii_lowercase() {
                return true;
            }
        }
        false
    }

    pub fn validate_origin(origin_header: &str, host_header: &str, allowlist: &[String]) -> bool {
        let origin = origin_header.trim();
        let rest = if let Some(r) = origin.strip_prefix("http://") {
            r
        } else if let Some(r) = origin.strip_prefix("https://") {
            r
        } else {
            return false;
        };

        if rest.eq_ignore_ascii_case(host_header.trim()) {
            return true;
        }

        let origin_host = if rest.starts_with('[') {
            if let Some(end) = rest.find(']') {
                &rest[1..end]
            } else {
                return false;
            }
        } else {
            rest.split(':').next().unwrap_or(rest)
        };

        allowlist
            .iter()
            .any(|allowed| allowed.eq_ignore_ascii_case(origin_host))
    }

    pub fn extract_cookie(cookie_header: &str, name: &str) -> Option<String> {
        for part in cookie_header.split(';') {
            if let Some((k, v)) = part.trim().split_once('=') {
                if k.trim() == name {
                    return Some(v.trim().to_string());
                }
            }
        }
        None
    }

    pub fn is_authenticated(req: &HttpRequest, config: &ConfigServerConfig) -> bool {
        if !config.require_login {
            return true;
        }
        let expected = match &config.session_token {
            Some(t) if !t.is_empty() => t.as_str(),
            _ => return false,
        };
        if let Some(cookie_hdr) = req.headers.get("cookie") {
            if let Some(token) = extract_cookie(cookie_hdr, "mios_portal") {
                if token == expected {
                    return true;
                }
            }
        }
        if let Some(auth_hdr) = req.headers.get("authorization") {
            if let Some(token) = auth_hdr
                .strip_prefix("Bearer ")
                .or_else(|| auth_hdr.strip_prefix("bearer "))
            {
                if token.trim() == expected {
                    return true;
                }
            }
        }
        if let Some(token) = req.headers.get("x-portal-token") {
            if token == expected {
                return true;
            }
        }
        if let Some(query) = &req.query {
            for param in query.split('&') {
                if let Some((k, v)) = param.split_once('=') {
                    if k == "token" && v == expected {
                        return true;
                    }
                }
            }
        }
        false
    }

    /// Sections whose loss bricks a deploy: a save may not drop one the live
    /// config has. Mirrors mios_pipe.kernel.config._VALIDATE_CRITICAL_SECTIONS.
    const CRITICAL_SECTIONS: [&str; 2] = ["identity", "ports"];

    /// The configurator-save safety net, the same rules agent-pipe's
    /// validate_config applies: parseable TOML, no dropped critical section, a
    /// non-empty [identity].mios_user when present, and every scalar [ports]
    /// value an integer in 1..=65535 (stack_id and nested values excepted).
    /// The size ceiling is enforced while the request is read.
    pub fn validate_portal_save(body: &str, config: &ConfigServerConfig) -> Vec<String> {
        let posted = match body.parse::<toml::Value>() {
            Ok(v) => v,
            Err(_) => return vec![INVALID_POSTED_TOML.to_string()],
        };
        let live = mios_resolver::resolve_merged(config.ssot_root.as_deref(), false).ok();
        let mut errors = Vec::new();
        for sec in CRITICAL_SECTIONS {
            let live_has = live
                .as_ref()
                .and_then(|l| l.get(sec))
                .and_then(toml::Value::as_table)
                .is_some_and(|t| !t.is_empty());
            let posted_has = posted
                .get(sec)
                .and_then(toml::Value::as_table)
                .is_some_and(|t| !t.is_empty());
            if live_has && !posted_has {
                errors.push(format!(
                "Refusing to drop critical [{sec}] section -- it is present in the live config and losing it bricks the deploy."
            ));
            }
        }
        if let Some(mu) = posted.get("identity").and_then(|i| i.get("mios_user")) {
            if mu.as_str().is_none_or(|s| s.trim().is_empty()) {
                errors.push("[identity].mios_user must be a non-empty string.".to_string());
            }
        }
        if let Some(ports) = posted.get("ports").and_then(toml::Value::as_table) {
            for (k, v) in ports {
                if k == "stack_id" || v.is_table() || v.is_array() {
                    continue;
                }
                match v.as_integer() {
                    Some(p) if (1..=65535).contains(&p) => {}
                    Some(_) => {
                        errors.push(format!("[ports].{k} is out of the valid 1-65535 range."))
                    }
                    None => errors.push(format!("[ports].{k} must be an integer 1-65535.")),
                }
            }
        }
        errors
    }

    /// Synchronize the complete semantic delta, retaining formatting on surviving
    /// values/tables. Keys absent from the delta must be removed, including a
    /// former user override that was explicitly reset to a vendor value.
    fn sync_user_delta(existing: &mut toml_edit::Table, desired: &toml_edit::Table) {
        let removed: Vec<String> = existing
            .iter()
            .filter(|(key, _)| !desired.contains_key(key))
            .map(|(key, _)| key.to_string())
            .collect();
        for key in removed {
            existing.remove(&key);
        }
        for (key, value) in desired.iter() {
            if let (Some(table), Some(old)) = (
                value.as_table(),
                existing
                    .get_mut(key)
                    .and_then(toml_edit::Item::as_table_mut),
            ) {
                sync_user_delta(old, table);
                continue;
            }
            let decor = existing
                .get(key)
                .and_then(toml_edit::Item::as_value)
                .map(|value| value.decor().clone());
            let mut next = value.clone();
            if let (Some(decor), Some(value)) = (decor, next.as_value_mut()) {
                *value.decor_mut() = decor;
            }
            existing.insert(key, next);
        }
    }

    /// Persist a configurator save into the user tier: only what differs from
    /// vendor..host.d (ports derived), written atomically -- the same contract as
    /// agent-pipe's write_user_config. Redaction markers are restored before
    /// the diff (see [`restore_redacted`]). Returns the path and bytes written.
    pub fn save_user_tier(
        config: &ConfigServerConfig,
        body: &str,
    ) -> Result<(PathBuf, usize), String> {
        let mut posted = body
            .parse::<toml::Value>()
            .map_err(|_| INVALID_POSTED_TOML.to_string())?;
        if contains_redacted(&posted) {
            // Unresolved tiers leave a marker's value unknown: refuse, keep the tier.
            let root = config.ssot_root.as_deref();
            mios_resolver::resolve_merged(root, false)
                .map_err(|_| UNRESOLVED_REFUSAL.to_string())?;
            let mut own_paths = mios_resolver::layers::resolve_layer_paths_below_user(root);
            own_paths.push(config.user_toml_path.clone());
            let own = mios_resolver::layers::merge_layer_files(&own_paths)
                .map_err(|_| UNRESOLVED_REFUSAL.to_string())?;
            // A marker inside an array is attributable only when the whole array
            // exactly echoes the redacted destination prefix (vendor..host.d plus
            // this user tier, never user.d). Anything else -- an edit, a reorder,
            // an insertion, a nested change or an array with no live counterpart
            // -- is ambiguous and refused before any restore, file or temp write.
            let mut expected = own.clone();
            redact_secrets(&mut expected);
            validate_marker_arrays(&posted, &expected)?;
            restore_redacted(&mut posted, &own);
        }
        let base = mios_resolver::resolve_below_user(config.ssot_root.as_deref())
            .map_err(|_| "the lower tiers did not resolve".to_string())?;
        let delta = mios_resolver::merge::diff_against(&posted, &base)
            .unwrap_or_else(|| toml::Value::Table(toml::Table::new()));
        let dest = &config.user_toml_path;
        if let Some(parent) = dest.parent() {
            fs::create_dir_all(parent)
                .map_err(|_| "Unable to create the configuration directory".to_string())?;
        }

        let serialized =
            toml::to_string(&delta).map_err(|_| "the delta did not serialize".to_string())?;
        // Preserve surviving comments without retaining stale overrides. A
        // per-key insertion loop left old arrays behind when a reset-to-base
        // removed them from the semantic delta.
        let text = if dest.exists() {
            let existing = fs::read_to_string(dest)
                .map_err(|_| "Unable to read the destination configuration".to_string())?;
            if let Ok(mut doc) = existing.parse::<toml_edit::DocumentMut>() {
                let desired = serialized
                    .parse::<toml_edit::DocumentMut>()
                    .map_err(|_| "the delta did not serialize".to_string())?;
                sync_user_delta(doc.as_table_mut(), desired.as_table());
                doc.to_string()
            } else {
                serialized
            }
        } else {
            serialized
        };

        let tmp_path = dest.with_extension("toml.tmp");
        let mut options = fs::OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let mut temporary = options
            .open(&tmp_path)
            .map_err(|_| "Unable to create the configuration temporary file".to_string())?;
        if std::io::Write::write_all(&mut temporary, text.as_bytes())
            .and_then(|()| temporary.sync_all())
            .is_err()
        {
            let _ = fs::remove_file(&tmp_path);
            return Err("Unable to write the configuration temporary file".to_string());
        }
        drop(temporary);
        if fs::rename(&tmp_path, dest).is_err() {
            let _ = fs::remove_file(&tmp_path);
            return Err("Unable to persist the configuration".to_string());
        }
        Ok((dest.clone(), text.len()))
    }

    pub fn handle_request(req: &HttpRequest, config: &ConfigServerConfig) -> HttpResponse {
        let sec_resp = |resp: HttpResponse| {
            resp.with_header("X-Content-Type-Options", "nosniff")
                .with_header("X-Frame-Options", "SAMEORIGIN")
        };

        let host_hdr = match req.headers.get("host") {
            Some(h) if !h.trim().is_empty() => h.trim(),
            _ => {
                return sec_resp(HttpResponse::new(403, "Forbidden").with_body(
                    "text/plain",
                    b"Forbidden: Missing or empty Host header".to_vec(),
                ));
            }
        };

        if !validate_host(host_hdr, &config.allowlist_hosts) {
            return sec_resp(HttpResponse::new(403, "Forbidden").with_body(
                "text/plain",
                format!("Forbidden: Host '{}' not in allowlist", host_hdr).into_bytes(),
            ));
        }

        if req.method == "OPTIONS" {
            return sec_resp(
                HttpResponse::new(204, "No Content").with_header("Content-Length", "0"),
            );
        }

        let mut resp = match (req.method.as_str(), req.path.as_str()) {
            ("GET", "/") | ("HEAD", "/") => sec_resp(HttpResponse::redirect("/configure")),

            ("GET", "/configure")
            | ("HEAD", "/configure")
            | ("GET", "/portal/configurator")
            | ("HEAD", "/portal/configurator") => {
                let has_valid_token = req
                    .query
                    .as_deref()
                    .and_then(|q| {
                        q.split('&')
                            .find_map(|p| p.split_once('='))
                            .filter(|(k, _)| *k == "token")
                            .map(|(_, v)| v)
                    })
                    .map(|tok| {
                        config
                            .session_token
                            .as_deref()
                            .map(|expected| tok == expected)
                            .unwrap_or(false)
                    })
                    .unwrap_or(false);

                let authenticated =
                    !config.require_login || is_authenticated(req, config) || has_valid_token;
                if !authenticated {
                    return sec_resp(HttpResponse::html(
                        403,
                        "Forbidden",
                        "<h1>403 Forbidden</h1><p>Authentication required: valid session token or cookie required.</p>".to_string(),
                    ));
                }

                let mut r = if config.html_path.is_file() {
                    match fs::read_to_string(&config.html_path) {
                        Ok(content) => sec_resp(HttpResponse::html(200, "OK", content)),
                        Err(e) => sec_resp(HttpResponse::html(
                            500,
                            "Internal Server Error",
                            format!("<h1>Error reading configurator</h1><pre>{}</pre>", e),
                        )),
                    }
                } else {
                    sec_resp(HttpResponse::html(
                        404,
                        "Not Found",
                        format!(
                            "<h1>Configurator Not Found</h1><p>Expected mios.html at {:?}</p>",
                            config.html_path
                        ),
                    ))
                };

                if has_valid_token {
                    if let Some(token) = &config.session_token {
                        r = r.with_header(
                            "Set-Cookie",
                            &format!("mios_portal={token}; Path=/; HttpOnly; SameSite=Strict"),
                        );
                    }
                }
                r
            }

            // The same [portal].require_login gate as /configure and POST: the
            // layered config is not served to an unauthenticated caller.
            ("GET", "/portal/config") | ("HEAD", "/portal/config") => {
                if config.require_login && !is_authenticated(req, config) {
                    sec_resp(HttpResponse::new(403, "Forbidden").with_body(
                        "text/plain",
                        b"Forbidden: Missing or invalid session token".to_vec(),
                    ))
                } else {
                    match read_layered_toml(config) {
                        Ok(toml_data) => sec_resp(HttpResponse::toml(200, "OK", toml_data)),
                        Err(e) => sec_resp(HttpResponse::json(
                            500,
                            "Internal Server Error",
                            serde_json::json!({ "errors": [e] }).to_string(),
                        )),
                    }
                }
            }

            ("POST", "/portal/config") => {
                let origin = match req.headers.get("origin") {
                    Some(o) if !o.trim().is_empty() => o.trim(),
                    _ => {
                        return sec_resp(HttpResponse::new(403, "Forbidden").with_body(
                            "text/plain",
                            b"Forbidden: Missing or invalid Origin header".to_vec(),
                        ));
                    }
                };
                if !validate_origin(origin, host_hdr, &config.allowlist_hosts) {
                    return sec_resp(HttpResponse::new(403, "Forbidden").with_body(
                        "text/plain",
                        b"Forbidden: Cross-origin request rejected".to_vec(),
                    ));
                }

                let content_type =
                    match req.headers.get("content-type") {
                        Some(ct) => ct.to_ascii_lowercase(),
                        _ => {
                            return sec_resp(HttpResponse::new(403, "Forbidden").with_body(
                                "text/plain",
                                b"Forbidden: Missing Content-Type".to_vec(),
                            ));
                        }
                    };
                if !content_type.starts_with("application/json") {
                    return sec_resp(HttpResponse::new(403, "Forbidden").with_body(
                        "text/plain",
                        b"Forbidden: Content-Type must be application/json".to_vec(),
                    ));
                }

                if config.require_login && !is_authenticated(req, config) {
                    return sec_resp(HttpResponse::new(403, "Forbidden").with_body(
                        "text/plain",
                        b"Forbidden: Missing or invalid session token".to_vec(),
                    ));
                }

                let body_str = match std::str::from_utf8(&req.body) {
                    Ok(s) => s,
                    Err(_) => {
                        return sec_resp(HttpResponse::json(
                            422,
                            "Unprocessable Entity",
                            r#"{"errors":["Request body must be valid UTF-8"]}"#.to_string(),
                        ));
                    }
                };

                let json_val: serde_json::Value = match serde_json::from_str(body_str) {
                    Ok(v) => v,
                    Err(_) => {
                        return sec_resp(HttpResponse::json(
                            422,
                            "Unprocessable Entity",
                            serde_json::json!({ "errors": ["Invalid JSON payload"] }).to_string(),
                        ));
                    }
                };

                let toml_str = if let Some(t) = json_val.get("toml").and_then(|v| v.as_str()) {
                    t.to_string()
                } else if let Some(c) = json_val.get("config") {
                    if let Some(s) = c.as_str() {
                        s.to_string()
                    } else {
                        match toml::to_string(c) {
                            Ok(s) => s,
                            Err(_) => {
                                return sec_resp(HttpResponse::json(
                                    422,
                                    "Unprocessable Entity",
                                    serde_json::json!({ "errors": ["Failed to convert config to TOML"] }).to_string(),
                                ));
                            }
                        }
                    }
                } else {
                    match toml::to_string(&json_val) {
                        Ok(s) => s,
                        Err(_) => {
                            return sec_resp(HttpResponse::json(
                                422,
                                "Unprocessable Entity",
                                serde_json::json!({ "errors": ["Failed to convert payload to TOML".to_string()] }).to_string(),
                            ));
                        }
                    }
                };

                let errors = validate_portal_save(&toml_str, config);
                if !errors.is_empty() {
                    return sec_resp(HttpResponse::json(
                        422,
                        "Unprocessable Entity",
                        serde_json::json!({ "errors": errors }).to_string(),
                    ));
                }

                match save_user_tier(config, &toml_str) {
                    Ok((path, bytes)) => sec_resp(HttpResponse::json(
                        200,
                        "OK",
                        serde_json::json!({
                            "status": "ok",
                            "message": format!("Saved to {}", path.display()),
                            "bytes": bytes,
                        })
                        .to_string(),
                    )),
                    Err(e) => {
                        // Retain the existing 500 for unresolved source tiers.
                        // Only known static refusal messages may reach the
                        // response; unexpected writer diagnostics stay private.
                        let invalid_input =
                            e == AMBIGUOUS_ARRAY_REFUSAL || e == INVALID_POSTED_TOML;
                        let status = if invalid_input { 422 } else { 500 };
                        let reason = if invalid_input {
                            "Unprocessable Entity"
                        } else {
                            "Internal Server Error"
                        };
                        let message = if invalid_input || e == UNRESOLVED_REFUSAL {
                            e
                        } else {
                            "Unable to save user configuration".to_string()
                        };
                        sec_resp(HttpResponse::json(
                            status,
                            reason,
                            serde_json::json!({ "errors": [message] }).to_string(),
                        ))
                    }
                }
            }

            ("GET", "/health")
            | ("HEAD", "/health")
            | ("GET", "/healthz")
            | ("HEAD", "/healthz") => sec_resp(HttpResponse::json(
                200,
                "OK",
                r#"{"status":"ok","engine":"miosd-config-server"}"#.to_string(),
            )),

            _ => sec_resp(HttpResponse::new(404, "Not Found").with_body(
                "text/plain",
                format!("Path not found: {}", req.path).into_bytes(),
            )),
        };

        if req.method == "HEAD" {
            resp.body.clear();
        }
        resp
    }

    /// Read one whole request: headers, then Content-Length bytes of body. A
    /// single read() truncated any body past one TCP segment, so a configurator
    /// save of the full config arrived as invalid TOML. The body ceiling is
    /// [portal].config_max_body_bytes.
    async fn read_request(
        stream: &mut TcpStream,
        max_body: usize,
    ) -> std::io::Result<Option<Vec<u8>>> {
        let mut raw = Vec::new();
        let mut chunk = [0u8; 65536];
        loop {
            let n = stream.read(&mut chunk).await?;
            if n == 0 {
                return Ok((!raw.is_empty()).then_some(raw));
            }
            raw.extend_from_slice(&chunk[..n]);
            let Some(end) = raw.windows(4).position(|w| w == b"\r\n\r\n") else {
                if raw.len() > max_body {
                    return Ok(None);
                }
                continue;
            };
            let head = String::from_utf8_lossy(&raw[..end]).to_ascii_lowercase();
            let want = head
                .lines()
                .find_map(|l| l.strip_prefix("content-length:"))
                .and_then(|v| v.trim().parse::<usize>().ok())
                .unwrap_or(0);
            if want > max_body {
                return Ok(None);
            }
            if raw.len() >= end + 4 + want {
                return Ok(Some(raw));
            }
        }
    }

    pub async fn handle_stream(
        mut stream: TcpStream,
        config: Arc<ConfigServerConfig>,
    ) -> Result<(), std::io::Error> {
        let raw = match read_request(&mut stream, config.max_body_bytes).await? {
            Some(raw) => raw,
            None => {
                let resp = HttpResponse::new(413, "Payload Too Large").with_body(
                    "text/plain",
                    b"Request exceeds [portal].config_max_body_bytes".to_vec(),
                );
                stream.write_all(&resp.to_bytes()).await?;
                let _ = stream.shutdown().await;
                return Ok(());
            }
        };

        let resp = match parse_http_request(&raw) {
            Some(req) => handle_request(&req, &config),
            None => HttpResponse::new(400, "Bad Request")
                .with_body("text/plain", b"Malformed HTTP request".to_vec()),
        };

        let resp_bytes = resp.to_bytes();
        stream.write_all(&resp_bytes).await?;
        stream.flush().await?;
        let _ = stream.shutdown().await;
        Ok(())
    }

    pub async fn run_config_server(
        config: ConfigServerConfig,
        mut shutdown_rx: Option<tokio::sync::oneshot::Receiver<()>>,
    ) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
        let listener = TcpListener::bind(&config.bind_addr).await?;
        let config_arc = Arc::new(config);

        loop {
            tokio::select! {
                accept_res = listener.accept() => {
                    match accept_res {
                        Ok((stream, _peer)) => {
                            let cfg = Arc::clone(&config_arc);
                            tokio::spawn(async move {
                                let _ = handle_stream(stream, cfg).await;
                            });
                        }
                        Err(e) => {
                            eprintln!("[miosd-server] Accept error: {}", e);
                        }
                    }
                }
                _ = async {
                    match &mut shutdown_rx {
                        Some(rx) => {
                            let _ = rx.await;
                        }
                        None => {
                            std::future::pending::<()>().await;
                        }
                    }
                } => {
                    break;
                }
            }
        }

        Ok(())
    }

    #[cfg(test)]
    mod tests {
        // Test code: a panic here IS the assertion. Scoped so production code stays under the lint.
        #![allow(clippy::panic)]

        use super::*;

        #[test]
        fn test_parse_http_request_get() {
            let raw =
                b"GET /configure HTTP/1.1\r\nHost: localhost:8700\r\nUser-Agent: test\r\n\r\n";
            let req = parse_http_request(raw);
            assert!(req.is_some());
            let r = req.as_ref().unwrap_or_else(|| unreachable!());
            assert_eq!(r.method, "GET");
            assert_eq!(r.path, "/configure");
            assert_eq!(
                r.headers.get("host").map(String::as_str),
                Some("localhost:8700")
            );
            assert!(r.body.is_empty());
        }

        #[test]
        fn test_parse_http_request_post_with_body() {
            let raw = b"POST /portal/config HTTP/1.1\r\nHost: localhost:8700\r\nContent-Length: 12\r\n\r\nhello=world\n";
            let req = parse_http_request(raw);
            assert!(req.is_some());
            let r = req.as_ref().unwrap_or_else(|| unreachable!());
            assert_eq!(r.method, "POST");
            assert_eq!(r.path, "/portal/config");
            assert_eq!(r.body, b"hello=world\n");
        }

        #[test]
        fn test_response_to_bytes() {
            let resp = HttpResponse::html(200, "OK", "<h1>Test</h1>".to_string());
            let bytes = resp.to_bytes();
            let s = String::from_utf8_lossy(&bytes);
            assert!(s.starts_with("HTTP/1.1 200 OK\r\n"));
            assert!(s.contains("Content-Type: text/html; charset=utf-8\r\n"));
            assert!(s.ends_with("\r\n\r\n<h1>Test</h1>"));
        }

        #[test]
        fn test_get_config_is_the_merged_ssot_as_valid_toml() {
            let tmp_dir = tempfile::tempdir().unwrap_or_else(|e| panic!("{}", e));
            let root = tmp_dir.path();
            fs::create_dir_all(root.join("usr/share/mios")).unwrap_or_else(|e| panic!("{}", e));
            fs::create_dir_all(root.join("etc/mios")).unwrap_or_else(|e| panic!("{}", e));
            fs::write(
                root.join("usr/share/mios/mios.toml"),
                "[ai]\nendpoint = \"vendor\"\nagent_model = \"m\"\n",
            )
            .unwrap_or_else(|e| panic!("{}", e));
            // The host tier repeats [ai]: spliced text would be a duplicate table.
            fs::write(
                root.join("etc/mios/mios.toml"),
                "[ai]\nendpoint = \"host\"\n",
            )
            .unwrap_or_else(|e| panic!("{}", e));
            let config = ConfigServerConfig {
                bind_addr: String::new(),
                html_path: root.join("mios.html"),
                user_toml_path: root.join("home/.config/mios/mios.toml"),
                max_body_bytes: 1 << 20,
                ssot_root: Some(root.to_path_buf()),
                allowlist_hosts: vec!["localhost".to_string(), "127.0.0.1".to_string()],
                require_login: false,
                session_token: None,
            };
            let mut headers = HashMap::new();
            headers.insert("host".to_string(), "localhost:8700".to_string());
            let req = HttpRequest {
                method: "GET".to_string(),
                path: "/portal/config".to_string(),
                query: None,
                headers,
                body: Vec::new(),
            };
            let resp = handle_request(&req, &config);
            assert_eq!(resp.status_code, 200);
            let body = String::from_utf8_lossy(&resp.body);
            let parsed: toml::Value = body.parse().unwrap_or_else(|e| panic!("{e}: {body}"));
            assert_eq!(parsed["ai"]["endpoint"].as_str(), Some("host"));
            assert_eq!(parsed["ai"]["agent_model"].as_str(), Some("m"));
        }

        #[test]
        fn test_post_config_saves_only_the_delta_to_the_user_tier() {
            let tmp_dir = tempfile::tempdir().unwrap_or_else(|e| panic!("{}", e));
            let root = tmp_dir.path();
            fs::create_dir_all(root.join("usr/share/mios")).unwrap_or_else(|e| panic!("{}", e));
            fs::write(
                root.join("usr/share/mios/mios.toml"),
                "[ai]\nendpoint = \"vendor\"\nagent_model = \"m\"\n",
            )
            .unwrap_or_else(|e| panic!("{}", e));
            let user = root.join("home/.config/mios/mios.toml");
            let config = ConfigServerConfig {
                bind_addr: String::new(),
                html_path: root.join("mios.html"),
                user_toml_path: user.clone(),
                max_body_bytes: 1 << 20,
                ssot_root: Some(root.to_path_buf()),
                allowlist_hosts: vec!["localhost".to_string(), "127.0.0.1".to_string()],
                require_login: false,
                session_token: None,
            };
            let (path, _) = save_user_tier(
                &config,
                "[ai]\nendpoint = \"operator\"\nagent_model = \"m\"\n",
            )
            .unwrap_or_else(|e| panic!("{e}"));
            assert_eq!(path, user);
            let saved: toml::Value = fs::read_to_string(&user)
                .unwrap_or_else(|e| panic!("{e}"))
                .parse()
                .unwrap_or_else(|e| panic!("{e}"));
            assert_eq!(saved["ai"]["endpoint"].as_str(), Some("operator"));
            assert!(
                saved["ai"].get("agent_model").is_none(),
                "unchanged keys stay in lower tiers: {saved}"
            );
        }

        async fn received(req: Vec<u8>, max_body: usize) -> Option<Vec<u8>> {
            let listener = TcpListener::bind("127.0.0.1:0")
                .await
                .unwrap_or_else(|e| panic!("{}", e));
            let addr = listener.local_addr().unwrap_or_else(|e| panic!("{}", e));
            let srv = tokio::spawn(async move {
                let (mut stream, _) = listener.accept().await.unwrap_or_else(|e| panic!("{}", e));
                read_request(&mut stream, max_body)
                    .await
                    .unwrap_or_else(|e| panic!("{}", e))
            });
            let mut client = TcpStream::connect(addr)
                .await
                .unwrap_or_else(|e| panic!("{}", e));
            client
                .write_all(&req)
                .await
                .unwrap_or_else(|e| panic!("{}", e));
            srv.await.unwrap_or_else(|e| panic!("{}", e))
        }

        fn post(body_len: usize) -> Vec<u8> {
            let body = "x".repeat(body_len);
            format!(
            "POST /portal/config HTTP/1.1\r\nHost: x\r\nContent-Length: {body_len}\r\n\r\n{body}"
        )
            .into_bytes()
        }

        #[test]
        fn test_portal_save_rules_match_agent_pipe() {
            let tmp_dir = tempfile::tempdir().unwrap_or_else(|e| panic!("{}", e));
            let root = tmp_dir.path();
            fs::create_dir_all(root.join("usr/share/mios")).unwrap_or_else(|e| panic!("{}", e));
            fs::write(
                root.join("usr/share/mios/mios.toml"),
                "[identity]\nmios_user = \"u\"\n[ports]\na = 1\n",
            )
            .unwrap_or_else(|e| panic!("{}", e));
            let config = ConfigServerConfig {
                bind_addr: String::new(),
                html_path: root.join("mios.html"),
                user_toml_path: root.join("u.toml"),
                max_body_bytes: 1 << 20,
                ssot_root: Some(root.to_path_buf()),
                allowlist_hosts: vec!["localhost".to_string(), "127.0.0.1".to_string()],
                require_login: false,
                session_token: None,
            };
            let ok = "[identity]\nmios_user = \"u\"\n[ports]\nstack_id = 0\na = 9\nlist = [1]\n[ports.categories.x]\nbase = 0\n";
            assert!(validate_portal_save(ok, &config).is_empty());
            assert_eq!(
                validate_portal_save("[ports]\na = 1\n", &config).len(),
                1,
                "dropped [identity]"
            );
            assert_eq!(
                validate_portal_save("[identity]\nmios_user = \" \"\n[ports]\na = 1\n", &config)
                    .len(),
                1
            );
            assert_eq!(
                validate_portal_save(
                    "[identity]\nmios_user = \"u\"\n[ports]\na = 70000\nb = \"x\"\n",
                    &config
                )
                .len(),
                2
            );
            assert_eq!(validate_portal_save("not = = toml", &config).len(), 1);
        }

        #[tokio::test]
        async fn test_a_body_larger_than_one_read_arrives_whole() {
            let req = post(200_000);
            let got = received(req.clone(), 1 << 20).await;
            assert_eq!(got.map(|r| r.len()), Some(req.len()));
        }

        #[tokio::test]
        async fn test_a_body_over_the_ssot_ceiling_is_refused() {
            assert_eq!(received(post(5_000), 4_096).await, None);
        }

        #[tokio::test]
        async fn test_server_loopback_health() {
            let listener = TcpListener::bind("127.0.0.1:0")
                .await
                .unwrap_or_else(|e| panic!("{}", e));
            let local_addr = listener.local_addr().unwrap_or_else(|e| panic!("{}", e));

            let tmp_dir = tempfile::tempdir().unwrap_or_else(|e| panic!("{}", e));
            let html_file = tmp_dir.path().join("mios.html");
            fs::write(&html_file, "<html><body>MiOS Settings</body></html>")
                .unwrap_or_else(|e| panic!("{}", e));

            let config = ConfigServerConfig {
                bind_addr: local_addr.to_string(),
                html_path: html_file,
                user_toml_path: tmp_dir.path().join("user.toml"),
                max_body_bytes: 1 << 20,
                ssot_root: Some(tmp_dir.path().to_path_buf()),
                allowlist_hosts: vec!["127.0.0.1".to_string(), "localhost".to_string()],
                require_login: false,
                session_token: None,
            };

            let cfg_arc = Arc::new(config);
            let cfg_clone = Arc::clone(&cfg_arc);

            let (shutdown_tx, shutdown_rx) = tokio::sync::oneshot::channel();

            let srv_handle = tokio::spawn(async move {
                tokio::select! {
                    res = listener.accept() => {
                        if let Ok((stream, _)) = res {
                            let _ = handle_stream(stream, cfg_clone).await;
                        }
                    }
                    _ = shutdown_rx => {}
                }
            });

            let mut client_stream = TcpStream::connect(local_addr)
                .await
                .unwrap_or_else(|e| panic!("{}", e));
            let req = b"GET /health HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n";
            client_stream
                .write_all(req)
                .await
                .unwrap_or_else(|e| panic!("{}", e));

            let mut resp_buf = [0u8; 4096];
            let n = client_stream
                .read(&mut resp_buf)
                .await
                .unwrap_or_else(|e| panic!("{}", e));
            let resp_str = String::from_utf8_lossy(&resp_buf[..n]);

            assert!(resp_str.contains("HTTP/1.1 200 OK"));
            assert!(resp_str.contains("miosd-config-server"));

            let _ = shutdown_tx.send(());
            let _ = srv_handle.await;
        }

        #[test]
        fn config_server_positive_same_origin_post_and_redacted_get() {
            let tmp_dir = tempfile::tempdir().unwrap_or_else(|e| panic!("{}", e));
            let root = tmp_dir.path();
            fs::create_dir_all(root.join("usr/share/mios")).unwrap_or_else(|e| panic!("{}", e));
            fs::create_dir_all(root.join("home/.config/mios")).unwrap_or_else(|e| panic!("{}", e));

            let ssot_content = r#"
[identity]
mios_user = "initial_user"

[portal]
require_login = true
password = "supersecret_portal_password"
secret = "portal_cookie_secret_value"

[ai]
endpoint = "host"
agent_model = "m"
api_key = "sk-super-secret-api-key"

[security]
allowlist_hosts = ["localhost", "127.0.0.1", "::1"]

[security.secret_keys]
min_keys = 1
keys = ["CUSTOM_TOKEN", "MIOS_BACKUP_KEY"]

[env]
CUSTOM_TOKEN = "my_custom_secret_123"
MIOS_BACKUP_KEY = "backup_pass_456"

[ports]
agent_pipe = 8700
"#;
            fs::write(root.join("usr/share/mios/mios.toml"), ssot_content)
                .unwrap_or_else(|e| panic!("{}", e));

            let user_toml = root.join("home/.config/mios/mios.toml");
            let config = ConfigServerConfig {
                bind_addr: "127.0.0.1:8700".to_string(),
                html_path: root.join("mios.html"),
                user_toml_path: user_toml.clone(),
                max_body_bytes: 1 << 20,
                ssot_root: Some(root.to_path_buf()),
                allowlist_hosts: vec![
                    "localhost".to_string(),
                    "127.0.0.1".to_string(),
                    "::1".to_string(),
                ],
                require_login: true,
                session_token: Some("valid-session-token-abc".to_string()),
            };

            // 1. Negative GET: require_login gates the layered config itself.
            let mut get_headers = HashMap::new();
            get_headers.insert("host".to_string(), "localhost:8700".to_string());
            let anon_req = HttpRequest {
                method: "GET".to_string(),
                path: "/portal/config".to_string(),
                query: None,
                headers: get_headers.clone(),
                body: Vec::new(),
            };
            let anon_resp = handle_request(&anon_req, &config);
            assert_eq!(anon_resp.status_code, 403);
            let anon_body = String::from_utf8_lossy(&anon_resp.body);
            assert!(
                !anon_body.contains("[identity]") && !anon_body.contains("initial_user"),
                "an unauthenticated GET served the config: {anon_body}"
            );

            // 2. Positive GET (session cookie): redacts all secret values
            get_headers.insert(
                "cookie".to_string(),
                "mios_portal=valid-session-token-abc".to_string(),
            );
            let get_req = HttpRequest {
                method: "GET".to_string(),
                path: "/portal/config".to_string(),
                query: None,
                headers: get_headers,
                body: Vec::new(),
            };
            let get_resp = handle_request(&get_req, &config);
            assert_eq!(get_resp.status_code, 200);
            let get_body = String::from_utf8_lossy(&get_resp.body);

            // Verify secret values are absent
            assert!(
                !get_body.contains("supersecret_portal_password"),
                "GET /portal/config leaked portal.password: {get_body}"
            );
            assert!(
                !get_body.contains("portal_cookie_secret_value"),
                "GET /portal/config leaked portal.secret: {get_body}"
            );
            assert!(
                !get_body.contains("sk-super-secret-api-key"),
                "GET /portal/config leaked ai.api_key: {get_body}"
            );
            assert!(
                !get_body.contains("my_custom_secret_123"),
                "GET /portal/config leaked CUSTOM_TOKEN: {get_body}"
            );
            assert!(
                !get_body.contains("backup_pass_456"),
                "GET /portal/config leaked MIOS_BACKUP_KEY: {get_body}"
            );
            assert!(
                get_body.contains("<redacted>"),
                "GET /portal/config should contain redacted placeholder: {get_body}"
            );

            // 3. Positive POST: Same-origin JSON POST with session cookie succeeds and persists edits
            let mut post_headers = HashMap::new();
            post_headers.insert("host".to_string(), "localhost:8700".to_string());
            post_headers.insert("origin".to_string(), "http://localhost:8700".to_string());
            post_headers.insert("content-type".to_string(), "application/json".to_string());
            post_headers.insert(
                "cookie".to_string(),
                "mios_portal=valid-session-token-abc".to_string(),
            );

            let post_json = serde_json::json!({
                "identity": {
                    "mios_user": "updated_operator"
                },
                "ports": {
                    "agent_pipe": 8700
                }
            })
            .to_string();

            let post_req = HttpRequest {
                method: "POST".to_string(),
                path: "/portal/config".to_string(),
                query: None,
                headers: post_headers,
                body: post_json.into_bytes(),
            };
            let post_resp = handle_request(&post_req, &config);
            assert_eq!(
                post_resp.status_code,
                200,
                "POST failed: {}",
                String::from_utf8_lossy(&post_resp.body)
            );

            // Verify user tier was persisted
            assert!(user_toml.exists(), "User toml was not written");
            let saved_content = fs::read_to_string(&user_toml).unwrap_or_else(|e| panic!("{}", e));
            assert!(
                saved_content.contains("updated_operator"),
                "Persisted user toml does not contain updated_operator: {saved_content}"
            );
        }

        fn request(
            method: &str,
            path: &str,
            query: Option<&str>,
            headers: &[(&str, &str)],
            body: &[u8],
        ) -> HttpRequest {
            HttpRequest {
                method: method.to_string(),
                path: path.to_string(),
                query: query.map(str::to_string),
                headers: headers
                    .iter()
                    .map(|(k, v)| (k.to_string(), v.to_string()))
                    .collect(),
                body: body.to_vec(),
            }
        }

        fn gated_config(root: &Path, user: PathBuf, require_login: bool) -> ConfigServerConfig {
            ConfigServerConfig {
                bind_addr: "127.0.0.1:8700".to_string(),
                html_path: root.join("mios.html"),
                user_toml_path: user,
                max_body_bytes: 1 << 20,
                ssot_root: Some(root.to_path_buf()),
                allowlist_hosts: vec!["localhost".to_string(), "127.0.0.1".to_string()],
                require_login,
                session_token: Some("gate-token-7f3a".to_string()),
            }
        }

        #[test]
        fn config_server_get_portal_config_requires_login_like_configure() {
            let tmp_dir = tempfile::tempdir().unwrap_or_else(|e| panic!("{}", e));
            let root = tmp_dir.path();
            fs::create_dir_all(root.join("usr/share/mios")).unwrap_or_else(|e| panic!("{}", e));
            fs::write(
                root.join("usr/share/mios/mios.toml"),
                "[identity]\nmios_user = \"gate-user\"\n[ports]\na = 1\n",
            )
            .unwrap_or_else(|e| panic!("{}", e));
            let config = gated_config(root, root.join("u.toml"), true);
            let host = ("host", "localhost:8700");

            for method in ["GET", "HEAD"] {
                for (headers, query) in [
                    (vec![host], None),
                    (vec![host, ("cookie", "mios_portal=wrong")], None),
                    (vec![host, ("authorization", "Bearer wrong")], None),
                    (vec![host], Some("token=wrong")),
                ] {
                    let resp = handle_request(
                        &request(method, "/portal/config", query, &headers, b""),
                        &config,
                    );
                    assert_eq!(resp.status_code, 403, "{method} {headers:?} {query:?}");
                    let body = String::from_utf8_lossy(&resp.body);
                    assert!(
                        !body.contains("gate-user"),
                        "{method} without a valid session served the config: {body}"
                    );
                    if method == "HEAD" {
                        assert!(resp.body.is_empty(), "HEAD carried a body: {body}");
                    }
                }
                for (headers, query) in [
                    (vec![host, ("cookie", "mios_portal=gate-token-7f3a")], None),
                    (
                        vec![host, ("authorization", "Bearer gate-token-7f3a")],
                        None,
                    ),
                    (vec![host, ("x-portal-token", "gate-token-7f3a")], None),
                    (vec![host], Some("token=gate-token-7f3a")),
                ] {
                    let resp = handle_request(
                        &request(method, "/portal/config", query, &headers, b""),
                        &config,
                    );
                    assert_eq!(resp.status_code, 200, "{method} {headers:?} {query:?}");
                    if method == "GET" {
                        assert!(String::from_utf8_lossy(&resp.body).contains("gate-user"));
                    } else {
                        assert!(resp.body.is_empty());
                    }
                }
            }

            // require_login = false opens it, exactly as it opens /configure.
            let open = gated_config(root, root.join("u.toml"), false);
            let resp = handle_request(&request("GET", "/portal/config", None, &[host], b""), &open);
            assert_eq!(resp.status_code, 200);
        }

        #[test]
        /// The stricter array contract, split out of the scalar take-or-drop
        /// test above so nothing is silently weakened: a marker inside an ARRAY
        /// is attributable only when the whole array equals the redacted
        /// destination prefix. The raw helper keeps its historical take-or-drop
        /// for validated callers (preflighted by `validate_marker_arrays` on
        /// the save path), but `save_user_tier` refuses an ambiguous marker
        /// array outright -- an edit, a reorder, an insertion, a nested change
        /// or an array with no live counterpart never restores, drops or
        /// copies anything. Scalar orphan markers still simply drop.
        fn restore_redacted_arrays_refuse_ambiguity_on_save_but_scalar_orphans_still_drop() {
            let live: toml::Value = r#"
[[remotes]]
name = "a"
github_token = "ghp_live_a"
[[remotes]]
name = "b"
github_token = "ghp_live_b"
[env]
list = ["x"]
"#
            .parse()
            .unwrap_or_else(|e| panic!("{e}"));
            let mut expected = live.clone();
            redact_secrets(&mut expected);

            // An unchanged echo of the whole redacted array validates.
            let echo = expected.clone();
            assert_eq!(validate_marker_arrays(&echo, &expected), Ok(()));

            // Edited, reordered, inserted and orphan arrays all refuse before
            // any write; so does a marker array nested inside another array.
            for bad in [
                // one element edited next to the markers
                r#"
[[remotes]]
name = "a2"
github_token = "<redacted>"
[[remotes]]
name = "b"
github_token = "<redacted>"
"#,
                // insertion of a third entry
                r#"
[[remotes]]
name = "a"
github_token = "<redacted>"
[[remotes]]
name = "b"
github_token = "<redacted>"
[[remotes]]
name = "c"
github_token = "<redacted>"
"#,
                // orphan array: no live counterpart at all
                r#"fresh = ["<redacted>", "kept"]"#,
                // nested marker array
                r#"nested = [["<redacted>"], "kept"]"#,
                // scalar orphan markers still drop (helper), never refuse
                r#"[ai]
api_key = "<redacted>"
endpoint = "e"
"#,
            ] {
                let mut posted: toml::Value = bad.parse().unwrap_or_else(|e| panic!("{e}"));
                let verdict = validate_marker_arrays(&posted, &expected);
                if bad.starts_with("[ai]") {
                    assert_eq!(verdict, Ok(()), "scalar markers keep take-or-drop");
                    restore_redacted(&mut posted, &expected);
                    assert!(posted["ai"].get("api_key").is_none());
                    assert_eq!(posted["ai"]["endpoint"].as_str(), Some("e"));
                } else {
                    assert_eq!(
                        verdict,
                        Err(AMBIGUOUS_ARRAY_REFUSAL.to_string()),
                        "must refuse: {bad}"
                    );
                }
            }

            // The raw helper itself keeps take-or-drop for validated callers;
            // the list below (two markers against a one-element live array)
            // would only ever reach it after the preflight above refused.
            let mut helper: toml::Value = r#"[env]
list = ["<redacted>", "<redacted>", "kept"]
"#
            .parse()
            .unwrap_or_else(|e| panic!("{e}"));
            restore_redacted(&mut helper, &live);
            let list: Vec<&str> = helper["env"]["list"]
                .as_array()
                .unwrap_or_else(|| panic!("{helper}"))
                .iter()
                .filter_map(toml::Value::as_str)
                .collect();
            assert_eq!(list, ["x", "kept"]);
        }

        #[test]
        fn redact_secrets_redacts_the_dotted_secret_paths() {
            const SSOT: &str = r#"
[identity]
username = "op"
default_password = "fixture-operator-credential"
[ttyd]
auth_user = "kept"
auth_pass = "fixture-ttyd-credential"
[auth]
password_hash = "$6$fixture$hash"
password = "fixture-auth-password"
luks_passphrase = "fixture-luks-passphrase"
[field.sysrescue]
username = "kept"
password = "fixture-sysrescue-password"
wipe_passphrase = "fixture-wipe-passphrase"
[pgvector]
user = "kept"
pass = "fixture-pgvector-pass"
[adguard]
admin_password_bcrypt = "fixture-adguard-bcrypt"
[scalar]
leaf = "not-a-table"
[[autounattend.accounts]]
name = "kept-a"
password = "fixture-account-a"
[[autounattend.accounts]]
name = "kept-b"
password = "fixture-account-b"
[security.secret_keys]
keys = ["MIOS_GITHUB_TOKEN"]
paths = ["identity.default_password", "ttyd.auth_pass", "auth.password_hash", "auth.password", "auth.luks_passphrase", "field.sysrescue.password", "autounattend.accounts.password", "pgvector.pass", "field.sysrescue.wipe_passphrase", "adguard.admin_password_bcrypt", "absent.table.key", "scalar.leaf.deeper", ""]
"#;
            let mut merged: toml::Value = SSOT.parse().unwrap_or_else(|e| panic!("{e}"));
            redact_secrets(&mut merged);
            for row in merged["autounattend"]["accounts"]
                .as_array()
                .unwrap_or_else(|| panic!("accounts is an array"))
            {
                assert_eq!(
                    row["password"].as_str(),
                    Some(REDACTED),
                    "an account row password was served"
                );
                assert!(row["name"].as_str().is_some_and(|n| n.starts_with("kept-")));
            }
            assert_eq!(
                merged["field"]["sysrescue"]["username"].as_str(),
                Some("kept")
            );
            for (table, key) in [
                ("identity", "default_password"),
                ("ttyd", "auth_pass"),
                ("auth", "password_hash"),
                ("auth", "password"),
                ("auth", "luks_passphrase"),
            ] {
                assert_eq!(
                    merged[table][key].as_str(),
                    Some(REDACTED),
                    "{table}.{key} was served"
                );
            }
            assert_eq!(merged["identity"]["username"].as_str(), Some("op"));
            assert_eq!(merged["ttyd"]["auth_user"].as_str(), Some("kept"));
            assert_eq!(merged["scalar"]["leaf"].as_str(), Some("not-a-table"));
            assert!(
                merged.get("absent").is_none(),
                "a secret path created a table"
            );
            let served = toml::to_string(&merged).unwrap_or_else(|e| panic!("{e}"));
            for secret in [
                "fixture-operator-credential",
                "fixture-ttyd-credential",
                "fixture-auth-password",
                "fixture-luks-passphrase",
                "fixture-sysrescue-password",
                "fixture-account-a",
                "fixture-account-b",
                "fixture-wipe-passphrase",
                "fixture-pgvector-pass",
                "fixture-adguard-bcrypt",
            ] {
                assert!(!served.contains(secret), "{secret} was served");
            }
            assert_eq!(merged["pgvector"]["user"].as_str(), Some("kept"));

            // Negative control: without `paths` (the bare-key matcher alone) the
            // operator credential is served, which is the defect the list closes.
            let mut keyed: toml::Value = SSOT
                .replace("paths = [", "unused = [")
                .parse()
                .unwrap_or_else(|e| panic!("{e}"));
            redact_secrets(&mut keyed);
            assert_eq!(
                keyed["identity"]["default_password"].as_str(),
                Some("fixture-operator-credential"),
                "the bare-key matcher redacted it: this control no longer proves the path list"
            );
        }

        #[test]
        fn restore_redacted_takes_the_live_value_or_drops_the_marker() {
            let live: toml::Value = r#"
[portal]
password = "live-pw"
user = "op"
[[remotes]]
name = "a"
github_token = "ghp_live_a"
[[remotes]]
name = "b"
github_token = "ghp_live_b"
[env]
list = ["x"]
"#
            .parse()
            .unwrap_or_else(|e| panic!("{e}"));
            let mut posted: toml::Value = r#"
[portal]
password = "<redacted>"
secret = "<redacted>"
user = "edited"
[[remotes]]
name = "a"
github_token = "<redacted>"
[[remotes]]
name = "b"
github_token = "<redacted>"
[[remotes]]
name = "c"
github_token = "<redacted>"
[env]
list = ["<redacted>", "<redacted>", "kept"]
"#
            .parse()
            .unwrap_or_else(|e| panic!("{e}"));
            assert!(contains_redacted(&posted));
            restore_redacted(&mut posted, &live);
            assert!(!contains_redacted(&posted), "{posted}");
            assert_eq!(posted["portal"]["password"].as_str(), Some("live-pw"));
            assert!(posted["portal"].get("secret").is_none(), "{posted}");
            assert_eq!(posted["portal"]["user"].as_str(), Some("edited"));

            // A live config with nothing at those paths: every marker is dropped,
            // nothing else is.
            let mut orphan: toml::Value = "[ai]\napi_key = \"<redacted>\"\nendpoint = \"e\"\n"
                .parse()
                .unwrap_or_else(|e| panic!("{e}"));
            restore_redacted(&mut orphan, &toml::Value::Table(toml::Table::new()));
            assert!(orphan["ai"].get("api_key").is_none());
            assert_eq!(orphan["ai"]["endpoint"].as_str(), Some("e"));
            assert!(!contains_redacted(&orphan));
        }

        /// Names the fixture root inside a [`user_tier_child`] process.
        const USER_TIER_FIXTURE: &str = "MIOS_TEST_USER_TIER_FIXTURE";

        /// The user tier the resolver reads inside a [`user_tier_child`] process.
        fn child_user_tier(root: &Path) -> PathBuf {
            root.join("home/.config/mios/mios.toml")
        }

        /// In the parent, runs this module's `test` in a child test process whose
        /// resolver user tier is a fresh temp fixture, and returns None. In that
        /// child, returns the fixture root, so no other test reads that tier.
        fn user_tier_child(test: &str) -> Option<PathBuf> {
            if let Some(root) = std::env::var_os(USER_TIER_FIXTURE) {
                return Some(PathBuf::from(root));
            }
            let tmp_dir = tempfile::tempdir().unwrap_or_else(|e| panic!("{e}"));
            let user = child_user_tier(tmp_dir.path());
            let exe = std::env::current_exe().unwrap_or_else(|e| panic!("{e}"));
            let mut child = std::process::Command::new(exe);
            let filter = format!("server::tests::{test}");
            child.args(["--exact", filter.as_str(), "--test-threads=1"]);
            for var in [
                "MIOS_ROOT",
                "MIOS_TOML",
                "MIOS_TOML_ROOT",
                "MIOS_VENDOR_TOML",
                "MIOS_VENDOR_TOML_D",
                "MIOS_HOST_TOML",
                "MIOS_HOST_TOML_D",
            ] {
                child.env_remove(var);
            }
            let out = child
                .env(USER_TIER_FIXTURE, tmp_dir.path())
                .env("MIOS_USER_TOML", &user)
                .env("MIOS_USER_TOML_D", user.with_file_name("mios.d"))
                .output()
                .unwrap_or_else(|e| panic!("{e}"));
            let stdout = String::from_utf8_lossy(&out.stdout);
            assert!(
                out.status.success() && stdout.contains("test result: ok. 1 passed"),
                "{test} failed in its user-tier child:\n{stdout}\n{}",
                String::from_utf8_lossy(&out.stderr)
            );
            None
        }

        /// The vendor and host tiers of the user-tier echo tests.
        fn plant_echo_tiers(root: &Path) {
            fs::create_dir_all(root.join("usr/share/mios")).unwrap_or_else(|e| panic!("{e}"));
            fs::create_dir_all(root.join("etc/mios")).unwrap_or_else(|e| panic!("{e}"));
            fs::write(
                root.join("usr/share/mios/mios.toml"),
                r#"
[identity]
mios_user = "echo-user"
[portal]
require_login = true
password = ""
secret = ""
session_ttl = 604800
[ai]
api_key = ""
endpoint = "vendor"
[git]
user_name = "vendor-git-user"
[security.secret_keys]
keys = ["MIOS_GITHUB_TOKEN"]
[ports]
a = 1
"#,
            )
            .unwrap_or_else(|e| panic!("{e}"));
            fs::write(
                root.join("etc/mios/mios.toml"),
                "[portal]\nsecret = \"host-tier-cookie-secret\"\n",
            )
            .unwrap_or_else(|e| panic!("{e}"));
        }

        /// A user tier holding a secret beside every key the echo tests edit.
        const ECHO_USER_TIER: &str = "[portal]\npassword = \"user-tier-portal-pw\"\n\n[ai]\napi_key = \"user-tier-api-key\"\n\n[git]\ngithub_token = \"user-tier-gh-token\"\n";

        fn get_config(config: &ConfigServerConfig) -> String {
            let get = handle_request(
                &request(
                    "GET",
                    "/portal/config",
                    None,
                    &[
                        ("host", "localhost:8700"),
                        ("cookie", "mios_portal=gate-token-7f3a"),
                    ],
                    b"",
                ),
                config,
            );
            assert_eq!(get.status_code, 200);
            String::from_utf8_lossy(&get.body).to_string()
        }

        fn post_toml(config: &ConfigServerConfig, toml_text: &str) -> HttpResponse {
            let body = serde_json::json!({ "toml": toml_text }).to_string();
            handle_request(
                &request(
                    "POST",
                    "/portal/config",
                    None,
                    &[
                        ("host", "localhost:8700"),
                        ("cookie", "mios_portal=gate-token-7f3a"),
                        ("origin", "http://localhost:8700"),
                        ("content-type", "application/json"),
                    ],
                    body.as_bytes(),
                ),
                config,
            )
        }

        fn redacted_fixture(root: &Path, arrays: bool) -> ConfigServerConfig {
            plant_echo_tiers(root);
            let user = child_user_tier(root);
            fs::create_dir_all(user.parent().unwrap_or_else(|| panic!("missing parent")))
                .unwrap_or_else(|e| panic!("{e}"));
            if arrays {
                let vendor = root.join("usr/share/mios/mios.toml");
                let mut text = fs::read_to_string(&vendor).unwrap_or_else(|e| panic!("{e}"));
                text.push_str(REDACTED_VENDOR_ARRAY);
                fs::write(vendor, text).unwrap_or_else(|e| panic!("{e}"));
            }
            gated_config(root, user, true)
        }

        const REDACTED_VENDOR_ARRAY: &str = r#"
[[remotes]]
name = "alpha"
github_token = "fixture-vendor-array-a"
rank = 1
[[remotes]]
name = "beta"
github_token = "fixture-vendor-array-b"
rank = 2
[limits]
width = 10
"#;
        const REDACTED_OWN_ARRAY: &str = r#"
[[remotes]]
name = "alpha"
github_token = "fixture-own-array-a"
rank = 1
[[remotes]]
name = "beta"
github_token = "fixture-own-array-b"
rank = 2
"#;

        fn redacted_posted(config: &ConfigServerConfig) -> toml::Value {
            get_config(config).parse().unwrap_or_else(|e| panic!("{e}"))
        }

        fn redacted_entries(posted: &mut toml::Value) -> &mut Vec<toml::Value> {
            posted
                .get_mut("remotes")
                .and_then(toml::Value::as_array_mut)
                .unwrap_or_else(|| panic!("missing fixture array"))
        }

        fn redacted_response(config: &ConfigServerConfig, posted: &toml::Value) -> HttpResponse {
            post_toml(
                config,
                &toml::to_string(posted).unwrap_or_else(|e| panic!("{e}")),
            )
        }

        fn redacted_user(config: &ConfigServerConfig) -> toml::Value {
            fs::read_to_string(&config.user_toml_path)
                .unwrap_or_else(|e| panic!("{e}"))
                .parse()
                .unwrap_or_else(|e| panic!("{e}"))
        }

        #[test]
        fn config_server_redacted_arrays_unchanged_lower_and_own_keep_their_layers() {
            let Some(root) = user_tier_child(
                "config_server_redacted_arrays_unchanged_lower_and_own_keep_their_layers",
            ) else {
                return;
            };
            let config = redacted_fixture(&root, true);
            let posted = redacted_posted(&config);
            assert_eq!(redacted_response(&config, &posted).status_code, 200);
            assert!(redacted_user(&config).get("remotes").is_none());
            let text = fs::read_to_string(&config.user_toml_path).unwrap_or_else(|e| panic!("{e}"));
            assert!(!text.contains("fixture-vendor-array"));
            fs::write(&config.user_toml_path, REDACTED_OWN_ARRAY).unwrap_or_else(|e| panic!("{e}"));
            let posted = redacted_posted(&config);
            assert_eq!(redacted_response(&config, &posted).status_code, 200);
            let saved = redacted_user(&config);
            assert_eq!(
                saved["remotes"]
                    .as_array()
                    .unwrap_or_else(|| panic!("array"))[0]["github_token"]
                    .as_str(),
                Some("fixture-own-array-a")
            );
            assert!(!fs::read_to_string(&config.user_toml_path)
                .unwrap_or_else(|e| panic!("{e}"))
                .contains(REDACTED));
        }

        #[test]
        fn config_server_redacted_arrays_ambiguous_requests_and_direct_writers_refuse() {
            let Some(root) = user_tier_child(
                "config_server_redacted_arrays_ambiguous_requests_and_direct_writers_refuse",
            ) else {
                return;
            };
            let config = redacted_fixture(&root, true);
            fs::write(&config.user_toml_path, ECHO_USER_TIER).unwrap_or_else(|e| panic!("{e}"));
            let before = fs::read(&config.user_toml_path).unwrap_or_else(|e| panic!("{e}"));
            let served = redacted_posted(&config);
            for case in 0..7 {
                let mut posted = served.clone();
                let entries = redacted_entries(&mut posted);
                match case {
                    0 => {
                        entries[0]["name"] = toml::Value::String("changed".into());
                    }
                    1 => entries.reverse(),
                    2 => entries.push(entries[0].clone()),
                    3 => {
                        entries.pop();
                    }
                    4 => {
                        entries[0]
                            .as_table_mut()
                            .unwrap_or_else(|| panic!("table"))
                            .insert(
                                "hints".into(),
                                toml::Value::Array(vec![toml::Value::Array(vec![
                                    toml::Value::String(REDACTED.into()),
                                ])]),
                            );
                    }
                    5 => {
                        entries[0]["rank"] = toml::Value::Boolean(true);
                    }
                    _ => {
                        posted
                            .as_table_mut()
                            .unwrap_or_else(|| panic!("table"))
                            .insert(
                                "fresh".into(),
                                toml::Value::Array(vec![
                                    toml::Value::String(REDACTED.into()),
                                    toml::Value::String("kept".into()),
                                ]),
                            );
                    }
                }
                assert_eq!(
                    redacted_response(&config, &posted).status_code,
                    422,
                    "case {case}"
                );
                let body = toml::to_string(&posted).unwrap_or_else(|e| panic!("{e}"));
                assert_eq!(
                    save_user_tier(&config, &body).err().as_deref(),
                    Some(AMBIGUOUS_ARRAY_REFUSAL),
                    "direct case {case}"
                );
                assert_eq!(
                    fs::read(&config.user_toml_path).unwrap_or_else(|e| panic!("{e}")),
                    before
                );
                assert!(!config.user_toml_path.with_extension("toml.tmp").exists());
            }
        }

        #[test]
        fn config_server_redacted_arrays_user_d_never_supplies_destination_secrets() {
            let Some(root) = user_tier_child(
                "config_server_redacted_arrays_user_d_never_supplies_destination_secrets",
            ) else {
                return;
            };
            let config = redacted_fixture(&root, true);
            fs::write(&config.user_toml_path, REDACTED_OWN_ARRAY).unwrap_or_else(|e| panic!("{e}"));
            let directory = config.user_toml_path.with_file_name("mios.d");
            fs::create_dir_all(&directory).unwrap_or_else(|e| panic!("{e}"));
            let fragment = directory.join("upper.toml");
            let upper = REDACTED_OWN_ARRAY.replace("fixture-own-array", "fixture-upper-array");
            fs::write(&fragment, &upper).unwrap_or_else(|e| panic!("{e}"));
            assert_eq!(
                redacted_response(&config, &redacted_posted(&config)).status_code,
                200
            );
            let saved =
                fs::read_to_string(&config.user_toml_path).unwrap_or_else(|e| panic!("{e}"));
            assert!(saved.contains("fixture-own-array-a"));
            assert!(!saved.contains("fixture-upper-array"));
            assert_eq!(
                fs::read_to_string(&fragment).unwrap_or_else(|e| panic!("{e}")),
                upper
            );
            let changed =
                "[[remotes]]\nname=\"only-upper\"\ngithub_token=\"fixture-upper-only\"\nrank=9\n";
            fs::write(&fragment, changed).unwrap_or_else(|e| panic!("{e}"));
            let before = fs::read(&config.user_toml_path).unwrap_or_else(|e| panic!("{e}"));
            assert_eq!(
                redacted_response(&config, &redacted_posted(&config)).status_code,
                422
            );
            assert_eq!(
                fs::read(&config.user_toml_path).unwrap_or_else(|e| panic!("{e}")),
                before
            );
            assert_eq!(
                fs::read_to_string(fragment).unwrap_or_else(|e| panic!("{e}")),
                changed
            );
        }

        #[test]
        fn config_server_redacted_arrays_only_user_d_cannot_materialize() {
            let Some(root) =
                user_tier_child("config_server_redacted_arrays_only_user_d_cannot_materialize")
            else {
                return;
            };
            let config = redacted_fixture(&root, false);
            let directory = config.user_toml_path.with_file_name("mios.d");
            fs::create_dir_all(&directory).unwrap_or_else(|e| panic!("{e}"));
            let fragment = directory.join("upper.toml");
            fs::write(&fragment, REDACTED_OWN_ARRAY).unwrap_or_else(|e| panic!("{e}"));
            let posted = redacted_posted(&config);
            assert_eq!(redacted_response(&config, &posted).status_code, 422);
            assert_eq!(
                save_user_tier(
                    &config,
                    &toml::to_string(&posted).unwrap_or_else(|e| panic!("{e}"))
                )
                .err()
                .as_deref(),
                Some(AMBIGUOUS_ARRAY_REFUSAL)
            );
            assert!(!config.user_toml_path.exists());
            assert!(!config.user_toml_path.with_extension("toml.tmp").exists());
            assert_eq!(
                fs::read_to_string(fragment).unwrap_or_else(|e| panic!("{e}")),
                REDACTED_OWN_ARRAY
            );
        }

        #[test]
        fn config_server_redacted_arrays_explicit_values_retain_types_and_reset_base() {
            let Some(root) = user_tier_child(
                "config_server_redacted_arrays_explicit_values_retain_types_and_reset_base",
            ) else {
                return;
            };
            let config = redacted_fixture(&root, true);
            let own = format!("{REDACTED_OWN_ARRAY}\n[ui]\nscale=2 # ui-scale-comment\n[limits]\nwidth=20\nextra=7 # extra-comment\n");
            fs::write(&config.user_toml_path, own).unwrap_or_else(|e| panic!("{e}"));
            let base =
                mios_resolver::resolve_below_user(Some(&root)).unwrap_or_else(|e| panic!("{e}"));
            let mut posted = redacted_posted(&config);
            posted["remotes"] = base["remotes"].clone();
            redacted_entries(&mut posted)[0]["rank"] = toml::Value::Boolean(true);
            assert_eq!(redacted_response(&config, &posted).status_code, 200);
            assert_eq!(
                redacted_user(&config)["remotes"]
                    .as_array()
                    .unwrap_or_else(|| panic!("array"))[0]["rank"]
                    .as_bool(),
                Some(true)
            );
            let mut posted = redacted_posted(&config);
            posted["remotes"] = base["remotes"].clone();
            posted["limits"]["width"] = toml::Value::Integer(10);
            assert_eq!(redacted_response(&config, &posted).status_code, 200);
            let saved = redacted_user(&config);
            assert!(
                saved.get("remotes").is_none(),
                "reset must remove the stale user array"
            );
            assert!(
                saved["limits"].get("width").is_none(),
                "nested reset must remove the stale override"
            );
            assert_eq!(saved["limits"]["extra"].as_integer(), Some(7));
            assert_eq!(saved["ui"]["scale"].as_integer(), Some(2));
            let text = fs::read_to_string(&config.user_toml_path).unwrap_or_else(|e| panic!("{e}"));
            assert!(text.contains("ui-scale-comment"));
            assert!(text.contains("extra-comment"));
        }

        const PRIVATE_SENTINEL: &str = "fixture-private-diagnostic-value";

        #[test]
        fn config_server_redacted_private_source_failures_preserve_files() {
            let Some(root) =
                user_tier_child("config_server_redacted_private_source_failures_preserve_files")
            else {
                return;
            };
            let config = redacted_fixture(&root, true);
            fs::write(&config.user_toml_path, ECHO_USER_TIER).unwrap_or_else(|e| panic!("{e}"));
            let vendor = root.join("usr/share/mios/mios.toml");
            let directory = config.user_toml_path.with_file_name("mios.d");
            fs::create_dir_all(&directory).unwrap_or_else(|e| panic!("{e}"));
            let fragment = directory.join("broken.toml");
            let malformed = format!("token = \"{PRIVATE_SENTINEL}\" trailing\n");
            for path in [&vendor, &config.user_toml_path, &fragment] {
                let posted = get_config(&config);
                let original = fs::read(path).ok();
                fs::write(path, &malformed).unwrap_or_else(|e| panic!("{e}"));
                let before_user =
                    fs::read(&config.user_toml_path).unwrap_or_else(|e| panic!("{e}"));
                let get = handle_request(
                    &request(
                        "GET",
                        "/portal/config",
                        None,
                        &[
                            ("host", "localhost:8700"),
                            ("cookie", "mios_portal=gate-token-7f3a"),
                        ],
                        &[],
                    ),
                    &config,
                );
                assert_eq!(get.status_code, 500);
                assert!(!String::from_utf8_lossy(&get.body).contains(PRIVATE_SENTINEL));
                assert!(!read_layered_toml(&config)
                    .err()
                    .unwrap_or_else(|| panic!("must refuse"))
                    .contains(PRIVATE_SENTINEL));
                let response = post_toml(&config, &posted);
                assert_eq!(response.status_code, 500);
                assert!(!String::from_utf8_lossy(&response.body).contains(PRIVATE_SENTINEL));
                assert_eq!(
                    save_user_tier(&config, &posted).err().as_deref(),
                    Some(UNRESOLVED_REFUSAL)
                );
                if path == &vendor {
                    let plain = "[identity]\nmios_user=\"echo-user\"\n[ports]\na=1\n";
                    let response = post_toml(&config, plain);
                    assert_eq!(response.status_code, 500);
                    assert!(!String::from_utf8_lossy(&response.body).contains(PRIVATE_SENTINEL));
                    assert!(!save_user_tier(&config, plain)
                        .err()
                        .unwrap_or_else(|| panic!("must refuse"))
                        .contains(PRIVATE_SENTINEL));
                }
                assert_eq!(
                    fs::read_to_string(path).unwrap_or_else(|e| panic!("{e}")),
                    malformed
                );
                assert_eq!(
                    fs::read(&config.user_toml_path).unwrap_or_else(|e| panic!("{e}")),
                    before_user
                );
                assert!(!config.user_toml_path.with_extension("toml.tmp").exists());
                match original {
                    Some(bytes) => fs::write(path, bytes).unwrap_or_else(|e| panic!("{e}")),
                    None => fs::remove_file(path).unwrap_or_else(|e| panic!("{e}")),
                }
            }
        }

        #[test]
        fn config_server_redacted_private_post_validation_never_echoes_values() {
            let Some(root) = user_tier_child(
                "config_server_redacted_private_post_validation_never_echoes_values",
            ) else {
                return;
            };
            let config = redacted_fixture(&root, true);
            fs::write(&config.user_toml_path, ECHO_USER_TIER).unwrap_or_else(|e| panic!("{e}"));
            let before = fs::read(&config.user_toml_path).unwrap_or_else(|e| panic!("{e}"));
            let malformed = format!("token = \"{PRIVATE_SENTINEL}\" trailing\n");
            let response = post_toml(&config, &malformed);
            assert_eq!(response.status_code, 422);
            assert!(!String::from_utf8_lossy(&response.body).contains(PRIVATE_SENTINEL));
            assert!(!save_user_tier(&config, &malformed)
                .err()
                .unwrap_or_else(|| panic!("must refuse"))
                .contains(PRIVATE_SENTINEL));
            for (bad, hidden) in [
                (format!("a=\"{PRIVATE_SENTINEL}\""), PRIVATE_SENTINEL),
                ("a=987654321".to_string(), "987654321"),
            ] {
                let body = format!("[identity]\nmios_user=\"echo-user\"\n[ports]\n{bad}\n");
                let response = post_toml(&config, &body);
                assert_eq!(response.status_code, 422);
                assert!(!String::from_utf8_lossy(&response.body).contains(hidden));
                assert!(validate_portal_save(&body, &config)
                    .iter()
                    .all(|error| !error.contains(hidden)));
            }
            assert_eq!(
                fs::read(&config.user_toml_path).unwrap_or_else(|e| panic!("{e}")),
                before
            );
            assert!(!config.user_toml_path.with_extension("toml.tmp").exists());
        }

        #[test]
        fn config_server_redacted_scalar_orphans_keep_take_or_drop_compatibility() {
            let Some(root) = user_tier_child(
                "config_server_redacted_scalar_orphans_keep_take_or_drop_compatibility",
            ) else {
                return;
            };
            let config = redacted_fixture(&root, true);
            let mut posted = redacted_posted(&config);
            let mut orphan = toml::Table::new();
            orphan.insert("github_token".into(), toml::Value::String(REDACTED.into()));
            orphan.insert("note".into(), toml::Value::String("kept".into()));
            posted
                .as_table_mut()
                .unwrap_or_else(|| panic!("table"))
                .insert("orphan".into(), toml::Value::Table(orphan));
            assert_eq!(redacted_response(&config, &posted).status_code, 200);
            let saved = redacted_user(&config);
            assert!(saved["orphan"].get("github_token").is_none());
            assert_eq!(saved["orphan"]["note"].as_str(), Some("kept"));
        }

        #[cfg(unix)]
        #[test]
        fn config_server_redacted_writer_uses_private_files_and_refuses_existing_temporary() {
            use std::os::unix::fs::{MetadataExt, PermissionsExt};
            let Some(root) = user_tier_child(
                "config_server_redacted_writer_uses_private_files_and_refuses_existing_temporary",
            ) else {
                return;
            };
            let config = redacted_fixture(&root, true);
            assert_eq!(
                redacted_response(&config, &redacted_posted(&config)).status_code,
                200
            );
            assert_eq!(
                fs::metadata(&config.user_toml_path)
                    .unwrap_or_else(|e| panic!("{e}"))
                    .mode()
                    & 0o777,
                0o600
            );
            fs::write(&config.user_toml_path, REDACTED_OWN_ARRAY).unwrap_or_else(|e| panic!("{e}"));
            fs::set_permissions(&config.user_toml_path, fs::Permissions::from_mode(0o644))
                .unwrap_or_else(|e| panic!("{e}"));
            let posted = redacted_posted(&config);
            assert_eq!(redacted_response(&config, &posted).status_code, 200);
            assert_eq!(
                fs::metadata(&config.user_toml_path)
                    .unwrap_or_else(|e| panic!("{e}"))
                    .mode()
                    & 0o777,
                0o600
            );
            let before = fs::read(&config.user_toml_path).unwrap_or_else(|e| panic!("{e}"));
            let temporary = config.user_toml_path.with_extension("toml.tmp");
            fs::write(&temporary, "preserved-existing-fixture").unwrap_or_else(|e| panic!("{e}"));
            assert_eq!(redacted_response(&config, &posted).status_code, 500);
            assert_eq!(
                fs::read(&config.user_toml_path).unwrap_or_else(|e| panic!("{e}")),
                before
            );
            assert_eq!(
                fs::read_to_string(temporary).unwrap_or_else(|e| panic!("{e}")),
                "preserved-existing-fixture"
            );
        }

        #[test]
        fn config_server_redacted_writer_failure_diagnostics_are_private() {
            let Some(root) =
                user_tier_child("config_server_redacted_writer_failure_diagnostics_are_private")
            else {
                return;
            };
            let mut config = redacted_fixture(&root, false);
            let blocked = root.join(PRIVATE_SENTINEL);
            fs::write(&blocked, "retained").unwrap_or_else(|e| panic!("{e}"));
            config.user_toml_path = blocked.join("mios.toml");
            let body = "[identity]\nmios_user=\"echo-user\"\n[ports]\na=1\n";
            let error = save_user_tier(&config, body)
                .err()
                .unwrap_or_else(|| panic!("must refuse"));
            assert!(!error.contains(PRIVATE_SENTINEL));
            let response = post_toml(&config, body);
            assert_eq!(response.status_code, 500);
            assert!(!String::from_utf8_lossy(&response.body).contains(PRIVATE_SENTINEL));
            assert_eq!(
                fs::read_to_string(blocked).unwrap_or_else(|e| panic!("{e}")),
                "retained"
            );
        }

        #[test]
        fn config_server_echo_keeps_a_user_tier_secret_beside_an_edited_key() {
            let Some(root) =
                user_tier_child("config_server_echo_keeps_a_user_tier_secret_beside_an_edited_key")
            else {
                return;
            };
            plant_echo_tiers(&root);
            let user = child_user_tier(&root);
            fs::create_dir_all(user.parent().unwrap_or_else(|| panic!("{user:?}")))
                .unwrap_or_else(|e| panic!("{e}"));
            fs::write(&user, ECHO_USER_TIER).unwrap_or_else(|e| panic!("{e}"));
            let config = gated_config(&root, user.clone(), true);

            let served = get_config(&config);
            for secret in [
                "user-tier-portal-pw",
                "user-tier-api-key",
                "user-tier-gh-token",
                "host-tier-cookie-secret",
            ] {
                assert!(!served.contains(secret), "GET leaked {secret}: {served}");
            }
            // Edit a key in the same table as each user-tier secret, so the whole
            // [portal] and [git] tables reach the delta and replace the tier's.
            let edited = served
                .replace("session_ttl = 604800", "session_ttl = 3600")
                .replace(
                    "user_name = \"vendor-git-user\"",
                    "user_name = \"edited-git-user\"",
                );
            assert!(edited.contains("session_ttl = 3600"), "{edited}");
            assert!(edited.contains("edited-git-user"), "{edited}");
            let saved = post_toml(&config, &edited);
            assert_eq!(
                saved.status_code,
                200,
                "{}",
                String::from_utf8_lossy(&saved.body)
            );

            let text = fs::read_to_string(&user).unwrap_or_else(|e| panic!("{e}"));
            assert!(
                !text.contains(REDACTED),
                "the marker reached the user tier: {text}"
            );
            assert!(
                !text.contains("host-tier-cookie-secret"),
                "a lower-tier secret was copied up: {text}"
            );
            let tier: toml::Value = text.parse().unwrap_or_else(|e| panic!("{e}: {text}"));
            assert_eq!(
                tier["portal"]["session_ttl"].as_integer(),
                Some(3600),
                "{text}"
            );
            assert_eq!(
                tier["portal"]["password"].as_str(),
                Some("user-tier-portal-pw"),
                "{text}"
            );
            assert_eq!(tier["git"]["user_name"].as_str(), Some("edited-git-user"));
            assert_eq!(
                tier["git"]["github_token"].as_str(),
                Some("user-tier-gh-token"),
                "{text}"
            );
            assert_eq!(tier["ai"]["api_key"].as_str(), Some("user-tier-api-key"));
            assert!(tier["portal"].get("secret").is_none(), "{text}");
        }

        #[test]
        fn config_server_echo_restores_each_secret_from_its_own_layer() {
            let Some(root) =
                user_tier_child("config_server_echo_restores_each_secret_from_its_own_layer")
            else {
                return;
            };
            plant_echo_tiers(&root);
            let user = child_user_tier(&root);
            let user_d = user.with_file_name("mios.d");
            fs::create_dir_all(&user_d).unwrap_or_else(|e| panic!("{e}"));
            fs::write(&user, ECHO_USER_TIER).unwrap_or_else(|e| panic!("{e}"));
            // user.d outranks the user tier: the GET serves its secrets, but a save
            // writes the user tier, which must keep its own and never copy user.d's.
            fs::write(
                user_d.join("20-own.toml"),
                "[portal]\npassword = \"user-d-portal-pw\"\nsecret = \"user-d-cookie-secret\"\n",
            )
            .unwrap_or_else(|e| panic!("{e}"));
            let config = gated_config(&root, user.clone(), true);
            let edited = get_config(&config).replace("session_ttl = 604800", "session_ttl = 3600");
            assert!(edited.contains("session_ttl = 3600"), "{edited}");
            let saved = post_toml(&config, &edited);
            assert_eq!(
                saved.status_code,
                200,
                "{}",
                String::from_utf8_lossy(&saved.body)
            );
            let text = fs::read_to_string(&user).unwrap_or_else(|e| panic!("{e}"));
            for foreign in [
                "user-d-portal-pw",
                "user-d-cookie-secret",
                "host-tier-cookie-secret",
                REDACTED,
            ] {
                assert!(
                    !text.contains(foreign),
                    "{foreign} reached the user tier: {text}"
                );
            }
            let tier: toml::Value = text.parse().unwrap_or_else(|e| panic!("{e}: {text}"));
            assert_eq!(tier["portal"]["session_ttl"].as_integer(), Some(3600));
            assert_eq!(
                tier["portal"]["password"].as_str(),
                Some("user-tier-portal-pw"),
                "{text}"
            );
            let live = mios_resolver::resolve_merged(Some(root.as_path()), false)
                .unwrap_or_else(|e| panic!("{e}"));
            assert_eq!(
                live["portal"]["password"].as_str(),
                Some("user-d-portal-pw")
            );
            assert_eq!(
                live["portal"]["secret"].as_str(),
                Some("user-d-cookie-secret")
            );
        }

        #[test]
        fn config_server_refuses_an_echo_when_the_tiers_do_not_resolve() {
            let Some(root) =
                user_tier_child("config_server_refuses_an_echo_when_the_tiers_do_not_resolve")
            else {
                return;
            };
            plant_echo_tiers(&root);
            let user = child_user_tier(&root);
            fs::create_dir_all(user.parent().unwrap_or_else(|| panic!("{user:?}")))
                .unwrap_or_else(|e| panic!("{e}"));
            fs::write(&user, ECHO_USER_TIER).unwrap_or_else(|e| panic!("{e}"));
            let config = gated_config(&root, user.clone(), true);
            let edited = get_config(&config).replace("session_ttl = 604800", "session_ttl = 3600");

            // A malformed user.d override: the merged tiers no longer resolve,
            // while the tiers below the user tier still do.
            let broken = user.with_file_name("mios.d").join("10-broken.toml");
            let broken_bytes = b"[portal\nsession_ttl = \n";
            fs::create_dir_all(user.with_file_name("mios.d")).unwrap_or_else(|e| panic!("{e}"));
            fs::write(&broken, broken_bytes).unwrap_or_else(|e| panic!("{e}"));
            assert!(mios_resolver::resolve_merged(Some(root.as_path()), false).is_err());
            assert!(mios_resolver::resolve_below_user(Some(root.as_path())).is_ok());

            let refused = post_toml(&config, &edited);
            let reply = String::from_utf8_lossy(&refused.body).to_string();
            assert_eq!(refused.status_code, 500, "{reply}");
            assert!(reply.contains(UNRESOLVED_REFUSAL), "{reply}");
            assert_eq!(
                fs::read(&user).unwrap_or_else(|e| panic!("{e}")),
                ECHO_USER_TIER.as_bytes(),
                "the refused save rewrote the user tier"
            );
            assert_eq!(
                fs::read(&broken).unwrap_or_else(|e| panic!("{e}")),
                broken_bytes
            );
            assert!(!user.with_extension("toml.tmp").exists());

            // Planted-error control: the same echo saves once the override parses.
            fs::remove_file(&broken).unwrap_or_else(|e| panic!("{e}"));
            let saved = post_toml(&config, &edited);
            assert_eq!(
                saved.status_code,
                200,
                "{}",
                String::from_utf8_lossy(&saved.body)
            );
            let text = fs::read_to_string(&user).unwrap_or_else(|e| panic!("{e}"));
            let tier: toml::Value = text.parse().unwrap_or_else(|e| panic!("{e}: {text}"));
            assert_eq!(
                tier["portal"]["session_ttl"].as_integer(),
                Some(3600),
                "{text}"
            );
            assert_eq!(
                tier["portal"]["password"].as_str(),
                Some("user-tier-portal-pw"),
                "{text}"
            );
        }

        #[test]
        fn config_server_save_echoing_the_redacted_get_never_writes_the_marker() {
            let Some(root) = user_tier_child(
                "config_server_save_echoing_the_redacted_get_never_writes_the_marker",
            ) else {
                return;
            };
            let root = root.as_path();
            fs::create_dir_all(root.join("usr/share/mios")).unwrap_or_else(|e| panic!("{}", e));
            fs::create_dir_all(root.join("etc/mios")).unwrap_or_else(|e| panic!("{}", e));
            fs::write(
                root.join("usr/share/mios/mios.toml"),
                r#"
[identity]
mios_user = "echo-user"
[portal]
require_login = true
password = "vendor-portal-pw"
secret = ""
[ai]
api_key = "vendor-api-key"
endpoint = "vendor"
[security.secret_keys]
keys = ["MIOS_GITHUB_TOKEN"]
[[remotes]]
name = "origin"
github_token = "ghp_vendor_array"
[ports]
a = 1
"#,
            )
            .unwrap_or_else(|e| panic!("{}", e));
            fs::write(
                root.join("etc/mios/mios.toml"),
                "[ai]\napi_key = \"host-api-key\"\n",
            )
            .unwrap_or_else(|e| panic!("{}", e));
            let user = child_user_tier(root);
            let config = gated_config(root, user.clone(), true);
            let cookie = ("cookie", "mios_portal=gate-token-7f3a");
            let host = ("host", "localhost:8700");

            let secrets = [
                "vendor-portal-pw",
                "vendor-api-key",
                "host-api-key",
                "ghp_vendor_array",
            ];

            let get = handle_request(
                &request("GET", "/portal/config", None, &[host, cookie], b""),
                &config,
            );
            assert_eq!(get.status_code, 200);
            let served = String::from_utf8_lossy(&get.body).to_string();
            for secret in secrets {
                assert!(!served.contains(secret), "GET leaked {secret}: {served}");
            }
            assert!(served.contains(REDACTED));

            let post = |toml_text: &str| {
                let body = serde_json::json!({ "toml": toml_text }).to_string();
                handle_request(
                    &request(
                        "POST",
                        "/portal/config",
                        None,
                        &[
                            host,
                            cookie,
                            ("origin", "http://localhost:8700"),
                            ("content-type", "application/json"),
                        ],
                        body.as_bytes(),
                    ),
                    &config,
                )
            };

            // A no-edit save of the echo writes nothing: every marker resolves
            // to the live value, which equals the tiers below the user tier.
            let unchanged = post(&served);
            assert_eq!(
                unchanged.status_code,
                200,
                "{}",
                String::from_utf8_lossy(&unchanged.body)
            );
            let reply: serde_json::Value =
                serde_json::from_slice(&unchanged.body).unwrap_or_else(|e| panic!("{e}"));
            assert_eq!(reply["bytes"].as_u64(), Some(0), "{reply}");
            assert_eq!(
                fs::read_to_string(&user).unwrap_or_else(|e| panic!("{e}")),
                ""
            );

            // One edit in the echo persists exactly that edit.
            let edited = served.replace("mios_user = \"echo-user\"", "mios_user = \"echo-edited\"");
            assert_ne!(edited, served);
            let saved = post(&edited);
            assert_eq!(
                saved.status_code,
                200,
                "{}",
                String::from_utf8_lossy(&saved.body)
            );
            let text = fs::read_to_string(&user).unwrap_or_else(|e| panic!("{e}"));
            assert!(
                !text.contains(REDACTED),
                "the marker reached the user tier: {text}"
            );
            for secret in secrets {
                assert!(
                    !text.contains(secret),
                    "{secret} was copied into the user tier: {text}"
                );
            }
            let tier: toml::Value = text.parse().unwrap_or_else(|e| panic!("{e}: {text}"));
            assert_eq!(tier["identity"]["mios_user"].as_str(), Some("echo-edited"));
            for unchanged_table in ["portal", "ai", "remotes", "ports", "security"] {
                assert!(
                    tier.get(unchanged_table).is_none(),
                    "{unchanged_table}: {text}"
                );
            }
        }

        #[test]
        fn config_server_rejects_cross_origin() {
            let tmp_dir = tempfile::tempdir().unwrap_or_else(|e| panic!("{}", e));
            let root = tmp_dir.path();
            fs::create_dir_all(root.join("usr/share/mios")).unwrap_or_else(|e| panic!("{}", e));
            fs::write(
                root.join("usr/share/mios/mios.toml"),
                "[identity]\nmios_user = \"u\"\n[security]\nallowlist_hosts = [\"localhost\", \"127.0.0.1\"]\n",
            )
            .unwrap_or_else(|e| panic!("{}", e));

            let config = ConfigServerConfig {
                bind_addr: "127.0.0.1:8700".to_string(),
                html_path: root.join("mios.html"),
                user_toml_path: root.join("user.toml"),
                max_body_bytes: 1 << 20,
                ssot_root: Some(root.to_path_buf()),
                allowlist_hosts: vec!["localhost".to_string(), "127.0.0.1".to_string()],
                require_login: true,
                session_token: Some("secret-token-xyz".to_string()),
            };

            // Subtest 1: Cross-origin text/plain POST with Origin: http://evil.example returns 403
            {
                let mut headers = HashMap::new();
                headers.insert("host".to_string(), "localhost:8700".to_string());
                headers.insert("origin".to_string(), "http://evil.example".to_string());
                headers.insert("content-type".to_string(), "text/plain".to_string());
                headers.insert(
                    "cookie".to_string(),
                    "mios_portal=secret-token-xyz".to_string(),
                );

                let req = HttpRequest {
                    method: "POST".to_string(),
                    path: "/portal/config".to_string(),
                    query: None,
                    headers,
                    body: b"identity.mios_user = evil".to_vec(),
                };
                let resp = handle_request(&req, &config);
                assert_eq!(
                    resp.status_code, 403,
                    "Expected 403 for cross-origin text/plain POST"
                );
                let body_str = String::from_utf8_lossy(&resp.body);
                assert!(
                    body_str.contains("Forbidden") || body_str.contains("rejected"),
                    "Body was: {body_str}"
                );
            }

            // Subtest 2: Host outside allowlist returns 403
            {
                let mut headers = HashMap::new();
                headers.insert("host".to_string(), "evil.example:8700".to_string());
                headers.insert("origin".to_string(), "http://localhost:8700".to_string());
                headers.insert("content-type".to_string(), "application/json".to_string());
                headers.insert(
                    "cookie".to_string(),
                    "mios_portal=secret-token-xyz".to_string(),
                );

                let req = HttpRequest {
                    method: "POST".to_string(),
                    path: "/portal/config".to_string(),
                    query: None,
                    headers,
                    body: b"{}".to_vec(),
                };
                let resp = handle_request(&req, &config);
                assert_eq!(
                    resp.status_code, 403,
                    "Expected 403 for Host outside allowlist"
                );
            }

            // Subtest 3: POST without token (when require_login is true) returns 403
            {
                let mut headers = HashMap::new();
                headers.insert("host".to_string(), "localhost:8700".to_string());
                headers.insert("origin".to_string(), "http://localhost:8700".to_string());
                headers.insert("content-type".to_string(), "application/json".to_string());
                // No cookie or auth token header

                let req = HttpRequest {
                    method: "POST".to_string(),
                    path: "/portal/config".to_string(),
                    query: None,
                    headers,
                    body: b"{}".to_vec(),
                };
                let resp = handle_request(&req, &config);
                assert_eq!(
                    resp.status_code, 403,
                    "Expected 403 for POST without session token"
                );
            }

            // Subtest 4: POST with wrong token returns 403
            {
                let mut headers = HashMap::new();
                headers.insert("host".to_string(), "localhost:8700".to_string());
                headers.insert("origin".to_string(), "http://localhost:8700".to_string());
                headers.insert("content-type".to_string(), "application/json".to_string());
                headers.insert("cookie".to_string(), "mios_portal=wrong-token".to_string());

                let req = HttpRequest {
                    method: "POST".to_string(),
                    path: "/portal/config".to_string(),
                    query: None,
                    headers,
                    body: b"{}".to_vec(),
                };
                let resp = handle_request(&req, &config);
                assert_eq!(
                    resp.status_code, 403,
                    "Expected 403 for POST with wrong session token"
                );
            }
        }

        #[test]
        fn config_server_omits_access_control_allow_origin_wildcard() {
            let tmp_dir = tempfile::tempdir().unwrap_or_else(|e| panic!("{}", e));
            let root = tmp_dir.path();
            let config = ConfigServerConfig {
                bind_addr: "127.0.0.1:8700".to_string(),
                html_path: root.join("mios.html"),
                user_toml_path: root.join("user.toml"),
                max_body_bytes: 1 << 20,
                ssot_root: Some(root.to_path_buf()),
                allowlist_hosts: vec!["localhost".to_string()],
                require_login: false,
                session_token: None,
            };

            let check_no_wildcard = |resp: &HttpResponse| {
                for (k, v) in &resp.headers {
                    if k.eq_ignore_ascii_case("Access-Control-Allow-Origin") {
                        assert_ne!(
                            v, "*",
                            "Response header must not contain Access-Control-Allow-Origin: *"
                        );
                    }
                }
            };

            let mut headers = HashMap::new();
            headers.insert("host".to_string(), "localhost:8700".to_string());

            // GET /health
            let req = HttpRequest {
                method: "GET".to_string(),
                path: "/health".to_string(),
                query: None,
                headers: headers.clone(),
                body: Vec::new(),
            };
            let resp = handle_request(&req, &config);
            check_no_wildcard(&resp);

            // OPTIONS /portal/config
            let mut opt_headers = headers.clone();
            opt_headers.insert("origin".to_string(), "http://localhost:8700".to_string());
            let req = HttpRequest {
                method: "OPTIONS".to_string(),
                path: "/portal/config".to_string(),
                query: None,
                headers: opt_headers,
                body: Vec::new(),
            };
            let resp = handle_request(&req, &config);
            check_no_wildcard(&resp);
        }

        #[test]
        fn config_server_token_exchange_sets_cookie() {
            let tmp_dir = tempfile::tempdir().unwrap_or_else(|e| panic!("{}", e));
            let root = tmp_dir.path();
            let html_file = root.join("mios.html");
            fs::write(&html_file, "<html><body>Settings</body></html>").unwrap();

            let config = ConfigServerConfig {
                bind_addr: "127.0.0.1:8700".to_string(),
                html_path: html_file,
                user_toml_path: root.join("user.toml"),
                max_body_bytes: 1 << 20,
                ssot_root: Some(root.to_path_buf()),
                allowlist_hosts: vec!["localhost".to_string()],
                require_login: true,
                session_token: Some("my-launch-token-99".to_string()),
            };

            let mut headers = HashMap::new();
            headers.insert("host".to_string(), "localhost:8700".to_string());

            // Request with query ?token=my-launch-token-99
            let req = HttpRequest {
                method: "GET".to_string(),
                path: "/configure".to_string(),
                query: Some("token=my-launch-token-99".to_string()),
                headers,
                body: Vec::new(),
            };
            let resp = handle_request(&req, &config);
            assert_eq!(resp.status_code, 200);

            let set_cookie = resp
                .headers
                .iter()
                .find(|(k, _)| k.eq_ignore_ascii_case("set-cookie"))
                .map(|(_, v)| v.as_str());
            assert!(set_cookie.is_some(), "Set-Cookie header missing");
            let cookie_val = set_cookie.unwrap();
            assert!(cookie_val.contains("mios_portal=my-launch-token-99"));
            assert!(cookie_val.contains("HttpOnly"));
            assert!(cookie_val.contains("SameSite=Strict"));
        }

        #[test]
        fn config_server_negative_missing_or_invalid_token() {
            let tmp_dir = tempfile::tempdir().unwrap_or_else(|e| panic!("{}", e));
            let root = tmp_dir.path();
            let html_file = root.join("mios.html");
            fs::write(&html_file, "<html><body>Settings</body></html>").unwrap();

            let config = ConfigServerConfig {
                bind_addr: "127.0.0.1:8700".to_string(),
                html_path: html_file,
                user_toml_path: root.join("user.toml"),
                max_body_bytes: 1 << 20,
                ssot_root: Some(root.to_path_buf()),
                allowlist_hosts: vec!["localhost".to_string()],
                require_login: true,
                session_token: Some("expected-token-123".to_string()),
            };

            // Negative 1: GET /configure with no token and no cookie -> 403 Forbidden
            let mut headers = HashMap::new();
            headers.insert("host".to_string(), "localhost:8700".to_string());
            let req_no_tok = HttpRequest {
                method: "GET".to_string(),
                path: "/configure".to_string(),
                query: None,
                headers: headers.clone(),
                body: Vec::new(),
            };
            let resp_no_tok = handle_request(&req_no_tok, &config);
            assert_eq!(
                resp_no_tok.status_code, 403,
                "Missing token must be 403 Forbidden"
            );

            // Negative 2: GET /configure with wrong token -> 403 Forbidden
            let req_bad_tok = HttpRequest {
                method: "GET".to_string(),
                path: "/configure".to_string(),
                query: Some("token=wrong-token-999".to_string()),
                headers,
                body: Vec::new(),
            };
            let resp_bad_tok = handle_request(&req_bad_tok, &config);
            assert_eq!(
                resp_bad_tok.status_code, 403,
                "Invalid token must be 403 Forbidden"
            );
        }

        #[test]
        fn config_server_resolve_with_auth_cli_token_and_file() {
            std::env::set_var("MIOS_PORTAL_CONFIG_MAX_BODY_BYTES", "1048576");
            let tmp_dir = tempfile::tempdir().unwrap_or_else(|e| panic!("{}", e));
            let token_path = tmp_dir.path().join("test-token-file");
            fs::write(&token_path, "token-from-file-456\n").unwrap();

            // 1. Direct CLI token argument takes highest precedence
            let cfg_token = ConfigServerConfig::resolve_with_auth(
                Some("127.0.0.1:8700".to_string()),
                Some(8700),
                Some("token-from-cli-123".to_string()),
                Some(token_path.clone()),
            )
            .unwrap();
            assert_eq!(
                cfg_token.session_token.as_deref(),
                Some("token-from-cli-123")
            );

            // 2. Token from token-file argument
            let cfg_file = ConfigServerConfig::resolve_with_auth(
                Some("127.0.0.1:8700".to_string()),
                Some(8700),
                None,
                Some(token_path),
            )
            .unwrap();
            assert_eq!(
                cfg_file.session_token.as_deref(),
                Some("token-from-file-456")
            );
        }
    }
}
