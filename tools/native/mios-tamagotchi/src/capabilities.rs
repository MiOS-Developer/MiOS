// AI-hint: Independent glyph, color and stream policies; terminal heuristics never certify font coverage.
// AI-related: config.rs, render.rs, specs/tamagotchi-tui/tamagotchi-graphics-spec.md
use std::io::{self, IsTerminal};

use clap::ValueEnum;
use serde::Deserialize;

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Eq, ValueEnum)]
#[serde(rename_all = "kebab-case")]
pub enum Renderer {
    Auto,
    Ascii,
    HalfBlock,
    Quadrant,
    Braille,
}

impl Renderer {
    pub fn packing(self) -> (u16, u16) {
        match self {
            Self::Auto | Self::Ascii => (1, 1),
            Self::HalfBlock => (1, 2),
            Self::Quadrant => (2, 2),
            Self::Braille => (2, 4),
        }
    }
}

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Eq, ValueEnum)]
#[serde(rename_all = "kebab-case")]
pub enum ColorMode {
    Auto,
    Mono,
    Ansi,
    Indexed,
    Rgb,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum Depth {
    Mono,
    Ansi,
    Indexed,
    Rgb,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Capabilities {
    pub interactive: bool,
    pub renderer: Renderer,
    pub depth: Depth,
}

/// Injectable observations keep policy tests independent of process-wide environment mutation.
pub struct Probe<'a> {
    pub input_tty: bool,
    pub output_tty: bool,
    pub term: &'a str,
    pub unicode: bool,
    pub color: Depth,
    pub no_color: bool,
}

impl Probe<'_> {
    pub fn select(&self, renderer: Renderer, color: ColorMode) -> Capabilities {
        let interactive =
            self.input_tty && self.output_tty && !self.term.is_empty() && self.term != "dumb";
        if !interactive {
            return Capabilities {
                interactive,
                renderer: Renderer::Ascii,
                depth: Depth::Mono,
            };
        }
        // Explicit glyph selection overrides the font/locale heuristic, independently of colors.
        let renderer = match renderer {
            Renderer::Auto if matches!(self.term, "linux" | "vt100" | "vt220") || !self.unicode => {
                Renderer::Ascii
            }
            Renderer::Auto => Renderer::HalfBlock,
            explicit => explicit,
        };
        let mut depth = match color {
            ColorMode::Auto => self.color,
            ColorMode::Mono => Depth::Mono,
            ColorMode::Ansi => Depth::Ansi,
            ColorMode::Indexed => Depth::Indexed,
            ColorMode::Rgb => Depth::Rgb,
        };
        if self.no_color || matches!(self.term, "vt100" | "vt220") {
            depth = Depth::Mono;
        } else if self.term == "linux" {
            depth = depth.min(Depth::Ansi);
        }
        Capabilities {
            interactive,
            renderer,
            depth,
        }
    }
}

pub fn detect(renderer: Renderer, color: ColorMode) -> Capabilities {
    let term = std::env::var("TERM").unwrap_or_default();
    let color_depth = match supports_color::on(supports_color::Stream::Stdout) {
        Some(c) if c.has_16m => Depth::Rgb,
        Some(c) if c.has_256 => Depth::Indexed,
        Some(c) if c.has_basic => Depth::Ansi,
        _ => Depth::Mono,
    };
    Probe {
        input_tty: io::stdin().is_terminal(),
        output_tty: io::stdout().is_terminal(),
        term: &term,
        unicode: supports_unicode::supports_unicode(),
        color: color_depth,
        no_color: std::env::var_os("NO_COLOR").is_some_and(|v| !v.is_empty()),
    }
    .select(renderer, color)
}
