// AI-hint: Wallpaper liveness monitoring and crash supervision daemon for MiOS living wallpaper.
// AI-related: usr/lib/systemd/user/mios-wallpaper.service, tools/native/mios-wallpaperd, /usr/share/mios/wallpaper/Host.qml

use std::os::unix::net::UnixStream;
use std::path::{Path, PathBuf};
use std::process::{Child, Command};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

/// Exponential backoff tracker for wallpaper supervision.
#[derive(Debug, Clone, PartialEq)]
pub struct WallpaperBackoff {
    pub min_secs: u64,
    pub max_secs: u64,
    pub current_secs: u64,
    pub stable_threshold_secs: u64,
    pub crash_count: u32,
}

impl Default for WallpaperBackoff {
    fn default() -> Self {
        Self::new(2, 30, 60)
    }
}

impl WallpaperBackoff {
    pub fn new(min_secs: u64, max_secs: u64, stable_threshold_secs: u64) -> Self {
        Self {
            min_secs,
            max_secs,
            current_secs: min_secs,
            stable_threshold_secs,
            crash_count: 0,
        }
    }

    /// Record a crash, increment crash counter, and compute the sleep duration.
    /// Uses exponential backoff (2x) capped at max_secs.
    pub fn on_crash(&mut self) -> Duration {
        self.crash_count = self.crash_count.saturating_add(1);
        let sleep_dur = Duration::from_secs(self.current_secs);
        self.current_secs = self.current_secs.saturating_mul(2).min(self.max_secs);
        sleep_dur
    }

    /// Reset backoff after child has run stably for >= stable_threshold_secs.
    pub fn on_stable(&mut self) {
        self.current_secs = self.min_secs;
        self.crash_count = 0;
    }

    /// Check if a given runtime duration qualifies as stable.
    pub fn is_stable(&self, elapsed: Duration) -> bool {
        elapsed >= Duration::from_secs(self.stable_threshold_secs)
    }
}

/// Verify that the Wayland display socket is present and connectable.
pub fn check_wayland_socket(runtime_dir: Option<&Path>) -> bool {
    let rt = match runtime_dir {
        Some(p) => p.to_path_buf(),
        None => match std::env::var_os("XDG_RUNTIME_DIR") {
            Some(v) => PathBuf::from(v),
            None => return false,
        },
    };

    let display_names: Vec<String> = if let Ok(val) = std::env::var("WAYLAND_DISPLAY") {
        if !val.trim().is_empty() {
            vec![val.trim().to_string()]
        } else {
            vec!["wayland-1".to_string(), "wayland-0".to_string()]
        }
    } else {
        vec!["wayland-1".to_string(), "wayland-0".to_string()]
    };

    for name in display_names {
        let sock_path = rt.join(&name);
        if sock_path.exists() {
            // Test connection to ensure compositor is actually accepting connections
            if UnixStream::connect(&sock_path).is_ok() {
                return true;
            }
        }
    }

    false
}

/// Wait up to `timeout` for Wayland socket to become ready, polling every 250ms.
pub fn wait_for_wayland(runtime_dir: Option<&Path>, timeout: Duration) -> bool {
    let start = Instant::now();
    while start.elapsed() < timeout {
        if check_wayland_socket(runtime_dir) {
            return true;
        }
        std::thread::sleep(Duration::from_millis(250));
    }
    check_wayland_socket(runtime_dir)
}

/// Spawn the wallpaper runner process under flock lock.
pub fn spawn_wallpaper_runner(
    runner: &str,
    host: &str,
    url: &str,
    runtime: &Path,
) -> Result<Child, String> {
    let lock_path = runtime.join("mios-wallpaper.lock");

    Command::new("flock")
        .args(["--nonblock", "--close"])
        .arg(&lock_path)
        .arg(runner)
        .arg(host)
        .arg("--")
        .arg(url)
        .env("QT_QPA_PLATFORM", "wayland")
        .env("QT_WAYLAND_SHELL_INTEGRATION", "layer-shell")
        .env_remove("TMUX")
        .env_remove("TMUX_PANE")
        .spawn()
        .map_err(|e| format!("Failed to spawn wallpaper runner '{runner}': {e}"))
}

/// Main supervisor loop: monitors wallpaper liveness, handles Wayland socket
/// readiness, and performs exponential backoff on crashes.
pub fn supervise_loop(
    runner: &str,
    host: &str,
    url: &str,
    runtime: &Path,
    shutdown: Arc<AtomicBool>,
) -> Result<(), String> {
    let mut backoff = WallpaperBackoff::default();

    println!("[miosd::wallpaper] Starting wallpaper supervisor loop...");
    println!("[miosd::wallpaper] Runner: {runner}, Host: {host}");

    while !shutdown.load(Ordering::Relaxed) {
        // 1. Wayland socket readiness check
        if !wait_for_wayland(Some(runtime), Duration::from_secs(5)) {
            let sleep_dur = backoff.on_crash();
            eprintln!(
                "[miosd::wallpaper] Wayland compositor socket not ready; waiting {:?} before retry...",
                sleep_dur
            );
            std::thread::sleep(sleep_dur);
            continue;
        }

        // 2. Spawn wallpaper child process
        println!("[miosd::wallpaper] Launching wallpaper process...");
        let start_time = Instant::now();
        let mut child = match spawn_wallpaper_runner(runner, host, url, runtime) {
            Ok(c) => c,
            Err(e) => {
                let sleep_dur = backoff.on_crash();
                eprintln!("[miosd::wallpaper] {e}. Backing off for {:?}...", sleep_dur);
                std::thread::sleep(sleep_dur);
                continue;
            }
        };

        let pid = child.id();
        println!("[miosd::wallpaper] Wallpaper child process active (PID {pid})");

        // 3. Monitor child liveness
        let mut exit_status = None;
        while !shutdown.load(Ordering::Relaxed) {
            match child.try_wait() {
                Ok(Some(status)) => {
                    exit_status = Some(status);
                    break;
                }
                Ok(None) => {
                    // Process is still running; check if it has been running stably
                    if backoff.is_stable(start_time.elapsed()) && backoff.crash_count > 0 {
                        println!(
                            "[miosd::wallpaper] Process ran stably for {:?}; resetting crash backoff.",
                            start_time.elapsed()
                        );
                        backoff.on_stable();
                    }
                    std::thread::sleep(Duration::from_millis(500));
                }
                Err(e) => {
                    eprintln!("[miosd::wallpaper] Error polling child PID {pid}: {e}");
                    break;
                }
            }
        }

        // 4. Handle shutdown signal if received while child was running
        if shutdown.load(Ordering::Relaxed) {
            println!("[miosd::wallpaper] Shutdown requested; terminating child PID {pid}...");
            let _ = child.kill();
            let _ = child.wait();
            return Ok(());
        }

        // 5. Child exited: inspect status and apply backoff if crashed
        let elapsed = start_time.elapsed();
        if let Some(status) = exit_status {
            if backoff.is_stable(elapsed) {
                backoff.on_stable();
            }

            if status.success() {
                println!(
                    "[miosd::wallpaper] Child PID {pid} exited normally (code 0) after {:?}.",
                    elapsed
                );
                // Clean exit: sleep minimum backoff to prevent tight respawn loops
                std::thread::sleep(Duration::from_secs(backoff.min_secs));
            } else {
                let sleep_dur = backoff.on_crash();
                eprintln!(
                    "[miosd::wallpaper] Child PID {pid} crashed with {:?} (uptime: {:?}). Applying exponential backoff of {:?} before restart...",
                    status, elapsed, sleep_dur
                );
                std::thread::sleep(sleep_dur);
            }
        }
    }

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_backoff_progression_and_capping() {
        let mut backoff = WallpaperBackoff::new(2, 30, 60);
        assert_eq!(backoff.current_secs, 2);
        assert_eq!(backoff.crash_count, 0);

        // Crash 1: returns 2s, current becomes 4s
        let d1 = backoff.on_crash();
        assert_eq!(d1, Duration::from_secs(2));
        assert_eq!(backoff.current_secs, 4);
        assert_eq!(backoff.crash_count, 1);

        // Crash 2: returns 4s, current becomes 8s
        let d2 = backoff.on_crash();
        assert_eq!(d2, Duration::from_secs(4));
        assert_eq!(backoff.current_secs, 8);
        assert_eq!(backoff.crash_count, 2);

        // Crash 3: returns 8s, current becomes 16s
        let d3 = backoff.on_crash();
        assert_eq!(d3, Duration::from_secs(8));
        assert_eq!(backoff.current_secs, 16);

        // Crash 4: returns 16s, current becomes 30s (capped at max 30s)
        let d4 = backoff.on_crash();
        assert_eq!(d4, Duration::from_secs(16));
        assert_eq!(backoff.current_secs, 30);

        // Crash 5: returns 30s, current stays 30s
        let d5 = backoff.on_crash();
        assert_eq!(d5, Duration::from_secs(30));
        assert_eq!(backoff.current_secs, 30);
    }

    #[test]
    fn test_backoff_stability_reset() {
        let mut backoff = WallpaperBackoff::new(2, 30, 60);
        backoff.on_crash();
        backoff.on_crash();
        assert_eq!(backoff.current_secs, 8);
        assert_eq!(backoff.crash_count, 2);

        assert!(!backoff.is_stable(Duration::from_secs(59)));
        assert!(backoff.is_stable(Duration::from_secs(60)));
        assert!(backoff.is_stable(Duration::from_secs(120)));

        backoff.on_stable();
        assert_eq!(backoff.current_secs, 2);
        assert_eq!(backoff.crash_count, 0);
    }

    #[test]
    fn test_wayland_socket_check_missing_dir() {
        let tmp = tempfile::tempdir().expect("tempdir");
        let non_existent = tmp.path().join("does_not_exist");
        assert!(!check_wayland_socket(Some(&non_existent)));
    }

    #[test]
    fn test_wayland_socket_check_with_mock_socket() {
        use std::os::unix::net::UnixListener;
        let tmp = tempfile::tempdir().expect("tempdir");
        let sock_path = tmp.path().join("wayland-1");
        let _listener = UnixListener::bind(&sock_path).expect("bind unix socket");

        assert!(check_wayland_socket(Some(tmp.path())));
    }
}
