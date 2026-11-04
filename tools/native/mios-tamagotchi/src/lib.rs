// AI-hint: Reusable terminal capability, sprite packing and input foundation for the Tamagotchi TUI.
// AI-related: specs/tamagotchi-tui, usr/share/mios/mios.toml [tamagotchi]
#![forbid(unsafe_code)]

pub mod capabilities;
pub mod config;
pub mod input;
pub mod render;
pub mod sprite;

#[cfg(test)]
mod tests;
