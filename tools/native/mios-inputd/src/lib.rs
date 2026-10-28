// AI-hint: Shared global binding policy used by the input daemon and collision gate.
// AI-related: usr/share/mios/mios.toml [keybindings.global], src/mios-rs/mios-gate
pub mod config;
#[cfg(target_os = "linux")]
pub mod linux;
pub mod router;
#[cfg(target_os = "linux")]
pub mod runtime;
pub use config::{normalize_chord, Binding, GlobalConfig};
pub mod vm;
