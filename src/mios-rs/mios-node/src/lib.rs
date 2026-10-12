// AI-hint: Library entry point for the mios-node edge micro-node daemon.
// AI-related: src/mios-rs/mios-node/src/main.rs, src/mios-rs/mios-node/src/node.rs, tools/native/mios-resolver/src/lib.rs
//! MiOS ("My OS" / "MyOS") Distributed Edge Micro-Node Library

pub mod crypto;
pub mod device;
pub mod exec;
pub mod heartbeat;
pub mod mesh;
pub mod node;
pub mod state_sync;
pub mod wire;

pub use device::{capabilities, hardware, watchdog};
pub use exec::{cgroups, executor, scheduler};
pub use mesh::{ble, overlay};
pub use wire::{buffer_pool, net, protocol};

/// Runtime SSOT lookup, shared with every native program (mios-resolver).
pub use mios_resolver::runtime as ssot;
