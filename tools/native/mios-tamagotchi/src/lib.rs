// AI-hint: Reusable terminal capability, sprite packing and input foundation for the Tamagotchi TUI.
// AI-related: specs/tamagotchi-tui, usr/share/mios/mios.toml [tamagotchi]
#![forbid(unsafe_code)]

pub mod capabilities;
pub mod characters;
pub mod config;
pub mod dialogs;
pub mod game;
pub mod habitat;
pub mod input;
pub mod render;
pub mod save;
pub mod sprite;
pub mod ui;

#[cfg(test)]
mod game_tests;
#[cfg(test)]
mod tests;

pub mod hud;

pub mod progression;

pub mod geometry;

pub mod lighting;

pub mod world;

pub mod genetics;

pub mod seed_entry;
