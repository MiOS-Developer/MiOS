// AI-hint: Mock podman executable for PowerShell pipe-deadlock tests; emits configurable stdout/stderr volumes.
use std::env;
use std::fs::OpenOptions;
use std::io::{self, Write};
use std::thread;
use std::time::Duration;

fn main() {
    let args: Vec<String> = env::args().collect();
    let mode = env::var("MOCK_PODMAN_MODE").unwrap_or_default();
    let log_path = env::var("MOCK_PODMAN_LOG").unwrap_or_default();

    if !log_path.is_empty() {
        if let Ok(mut f) = OpenOptions::new().create(true).append(true).open(&log_path) {
            let _ = writeln!(f, "{}", args[1..].join(" "));
        }
    }

    if args.len() < 2 {
        std::process::exit(0);
    }

    let subcmd = &args[1];

    if subcmd == "create" {
        println!("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
        std::process::exit(0);
    }

    if subcmd == "export" {
        if mode == "deadlock" {
            let err_data = vec![b'E'; 131072];
            let _ = io::stderr().write_all(&err_data);
            std::process::exit(1);
        } else if mode == "hang" {
            let chunk = vec![b'A'; 4096];
            let _ = io::stdout().write_all(&chunk);
            let _ = io::stdout().flush();
            thread::sleep(Duration::from_secs(60));
            std::process::exit(0);
        } else {
            let chunk = vec![b'A'; 32768];
            let _ = io::stdout().write_all(&chunk);
            let _ = io::stdout().flush();
            eprintln!("Error: simulated storage corruption in layer sha256:abc1234");
            std::process::exit(125);
        }
    }

    if subcmd == "rm" {
        std::process::exit(0);
    }

    std::process::exit(0);
}
