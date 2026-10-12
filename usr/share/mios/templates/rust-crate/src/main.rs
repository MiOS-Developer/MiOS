// AI-hint: Main entrypoint for {{name}} native tool.
// AI-related: usr/share/mios/mios.toml, tools/native/Cargo.toml

#![forbid(unsafe_code)]

use std::process::ExitCode;

fn main() -> ExitCode {
    println!("{{name}} v{}", env!("CARGO_PKG_VERSION"));
    ExitCode::SUCCESS
}
