// AI-hint: Positive/negative controls for stream policy, sprite validation, cell geometry and projected keys.
// AI-related: capabilities.rs, config.rs, input.rs, render.rs, sprite.rs
use crate::{
    capabilities::{ColorMode, Depth, Probe, Renderer},
    config::{parse_rgb, Config},
    input::{action, Action},
    render::{braille, quadrant, quantize, snapshot, Colors, SpriteWidget},
    sprite::{cell_size, fit_scale, Sprite},
};
use crossterm::event::{KeyCode, KeyEvent, KeyEventKind, KeyModifiers};
use ratatui::{buffer::Buffer, layout::Rect, style::Color, widgets::Widget};

fn config() -> Config {
    Config::from_value(
        &include_str!("../../../../usr/share/mios/mios.toml")
            .parse()
            .unwrap(),
    )
    .unwrap()
}
fn probe() -> Probe<'static> {
    Probe {
        input_tty: true,
        output_tty: true,
        term: "xterm-256color",
        unicode: true,
        color: Depth::Indexed,
        no_color: false,
    }
}
#[test]
fn glyph_and_color_selection_are_independent() {
    let mut p = probe();
    p.unicode = false;
    let c = p.select(Renderer::Auto, ColorMode::Auto);
    assert_eq!((c.renderer, c.depth), (Renderer::Ascii, Depth::Indexed));
    p.unicode = true;
    p.color = Depth::Mono;
    let c = p.select(Renderer::Braille, ColorMode::Auto);
    assert_eq!((c.renderer, c.depth), (Renderer::Braille, Depth::Mono));
    p.color = Depth::Rgb;
    assert_eq!(p.select(Renderer::Auto, ColorMode::Auto).depth, Depth::Rgb);
}
#[test]
fn redirected_streams_and_dumb_terminals_never_enter_tui() {
    for (input_tty, output_tty, term) in [
        (true, false, "xterm"),
        (false, true, "xterm"),
        (true, true, "dumb"),
        (true, true, ""),
    ] {
        let p = Probe {
            input_tty,
            output_tty,
            term,
            ..probe()
        };
        let c = p.select(Renderer::Braille, ColorMode::Rgb);
        assert!(!c.interactive);
        assert_eq!((c.renderer, c.depth), (Renderer::Ascii, Depth::Mono));
    }
}
#[test]
fn console_and_no_color_constraints_win_over_inherited_truecolor() {
    let mut p = probe();
    p.term = "linux";
    p.color = Depth::Rgb;
    let c = p.select(Renderer::Auto, ColorMode::Rgb);
    assert_eq!((c.renderer, c.depth), (Renderer::Ascii, Depth::Ansi));
    assert_eq!(
        p.select(Renderer::Braille, ColorMode::Auto).renderer,
        Renderer::Braille
    );
    for term in ["vt100", "vt220"] {
        p.term = term;
        assert_eq!(
            p.select(Renderer::Auto, ColorMode::Auto).renderer,
            Renderer::Ascii
        );
        assert_eq!(p.select(Renderer::Ascii, ColorMode::Rgb).depth, Depth::Mono);
    }
    p.term = "xterm";
    p.no_color = true;
    assert_eq!(
        p.select(Renderer::HalfBlock, ColorMode::Rgb).depth,
        Depth::Mono
    );
}
#[test]
fn invalid_configuration_fails_before_terminal_initialization() {
    let mut c = config();
    c.tick_ms = 0;
    assert!(c.validate().is_err());
    let mut c = config();
    c.scale = 3;
    assert!(c.validate().is_err());
    let mut c = config();
    c.palette.pop();
    assert!(c.validate().is_err());
    let mut c = config();
    c.background = "#zzzzzz".into();
    assert!(c.validate().is_err());
    assert!(parse_rgb("#é1234").is_err());
    assert_eq!(parse_rgb("#00aAFF").unwrap(), [0, 170, 255]);
    assert!(Config::from_value(&"[tamagotchi]\nrenderer='unknown'".parse().unwrap()).is_err());
}
#[test]
fn sprite_validation_preserves_safe_indexing() {
    assert!(Sprite::new(0, 1, vec![]).is_err());
    assert!(Sprite::new(257, 1, vec![None; 257]).is_err());
    assert!(Sprite::new(2, 2, vec![Some(0)]).is_err());
    assert!(Sprite::new(1, 1, vec![Some(4)]).is_err());
    let s = Sprite::new(1, 1, vec![Some(3)]).unwrap();
    assert_eq!(s.pixel(1, 1, 2), Some(3));
    assert_eq!(s.pixel(2, 0, 2), None);
    assert_eq!(s.pixel(u32::MAX, u32::MAX, 1), None);
    assert_eq!(s.pixel(0, 0, 0), None);
}
#[test]
fn quadrant_and_braille_orientation_match_unicode() {
    for (pixels, expected) in [
        ([true, false, false, false], '▘'),
        ([false, true, false, false], '▝'),
        ([false, false, true, false], '▖'),
        ([false, false, false, true], '▗'),
        ([true, true, false, false], '▀'),
        ([false, false, true, true], '▄'),
        ([true, false, false, true], '▚'),
        ([false, true, true, false], '▞'),
        ([true; 4], '█'),
    ] {
        assert_eq!(quadrant(pixels), expected);
    }
    for (i, expected) in [
        0x2801, 0x2808, 0x2802, 0x2810, 0x2804, 0x2820, 0x2840, 0x2880,
    ]
    .into_iter()
    .enumerate()
    {
        let mut dots = [false; 8];
        dots[i] = true;
        assert_eq!(braille(dots) as u32, expected);
    }
    assert_eq!(braille([true; 8]), '\u{28ff}');
}
#[test]
fn fitting_uses_both_dimensions_packing_and_requested_scale() {
    assert_eq!(fit_scale(8, 8, 320, 25, Renderer::Ascii, 0), Some(2));
    assert_eq!(fit_scale(8, 8, 320, 25, Renderer::Ascii, 8), None);
    assert_eq!(fit_scale(8, 8, 8, 4, Renderer::HalfBlock, 1), Some(1));
    assert_eq!(fit_scale(8, 8, 4, 2, Renderer::Braille, 1), Some(1));
    assert_eq!(cell_size(3, 5, 1, Renderer::Braille), (2, 2));
    for (cols, rows) in [(0, 40), (40, 0), (7, 7)] {
        assert_eq!(fit_scale(8, 8, cols, rows, Renderer::Ascii, 0), None);
    }
    assert_eq!(
        cell_size(u16::MAX, u16::MAX, 16, Renderer::Ascii),
        (1_048_560, 1_048_560)
    );
    assert_eq!(
        fit_scale(u16::MAX, u16::MAX, u16::MAX, u16::MAX, Renderer::Ascii, 0),
        Some(1)
    );
}
#[test]
fn half_block_keeps_independent_colors_and_odd_height_padding() {
    let sprite = Sprite::new(1, 3, vec![Some(0), Some(1), Some(2)]).unwrap();
    let colors = Colors {
        palette: [Color::Red, Color::Blue, Color::Green, Color::White],
        background: Color::Black,
    };
    let area = Rect::new(0, 0, 1, 2);
    let mut buf = Buffer::empty(area);
    SpriteWidget {
        sprite: &sprite,
        renderer: Renderer::HalfBlock,
        scale: 1,
        colors: &colors,
    }
    .render(area, &mut buf);
    assert_eq!(buf[(0, 0)].symbol(), "▀");
    assert_eq!((buf[(0, 0)].fg, buf[(0, 0)].bg), (Color::Red, Color::Blue));
    assert_eq!(
        (buf[(0, 1)].fg, buf[(0, 1)].bg),
        (Color::Green, Color::Black)
    );
}
#[test]
fn widget_clips_to_buffer_and_preserves_fully_transparent_cells() {
    let colors = Colors::from_config(&config(), Depth::Rgb).unwrap();
    let sprite = Sprite::new(1, 1, vec![None]).unwrap();
    let area = Rect::new(2, 2, 1, 1);
    let mut buf = Buffer::empty(area);
    buf[(2, 2)].set_char('x');
    SpriteWidget {
        sprite: &sprite,
        renderer: Renderer::Ascii,
        scale: 1,
        colors: &colors,
    }
    .render(Rect::new(0, 0, 10, 10), &mut buf);
    assert_eq!(buf[(2, 2)].symbol(), "x");
    SpriteWidget {
        sprite: &sprite,
        renderer: Renderer::Ascii,
        scale: 1,
        colors: &colors,
    }
    .render(Rect::new(12, 12, 10, 10), &mut buf);
    assert_eq!(buf[(2, 2)].symbol(), "x");
}
#[test]
fn quantization_and_snapshot_respect_monochrome_and_palette_limits() {
    assert_eq!(quantize([255, 0, 0], Depth::Ansi), Color::LightRed);
    assert_eq!(quantize([255, 0, 0], Depth::Indexed), Color::Indexed(196));
    assert_eq!(
        quantize([128, 128, 128], Depth::Indexed),
        Color::Indexed(244)
    );
    assert_eq!(quantize([1, 2, 3], Depth::Rgb), Color::Rgb(1, 2, 3));
    assert_eq!(quantize([1, 2, 3], Depth::Mono), Color::Reset);
    let text = snapshot(&Sprite::preview(0), &config()).unwrap();
    assert!(text.is_ascii());
    assert!(!text.contains('\x1b'));
    assert_eq!(text.lines().count(), 8);
    assert!(text.lines().all(|s| s.len() == 8));
}
#[test]
fn inputd_projected_keys_navigate_without_release_or_repeat_toggles() {
    for (code, expected) in [
        (KeyCode::Up, Action::Previous),
        (KeyCode::Down, Action::Next),
        (KeyCode::Enter, Action::Pause),
        (KeyCode::Tab, Action::Next),
        (KeyCode::Esc, Action::Quit),
    ] {
        let mut key = KeyEvent::new(code, KeyModifiers::NONE);
        assert_eq!(action(key), Some(expected));
        key.kind = KeyEventKind::Release;
        assert_eq!(action(key), None);
    }
    let mut key = KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE);
    key.kind = KeyEventKind::Repeat;
    assert_eq!(action(key), None);
    key.code = KeyCode::Down;
    assert_eq!(action(key), Some(Action::Next));
    assert_eq!(
        action(KeyEvent::new(KeyCode::Char('c'), KeyModifiers::CONTROL)),
        Some(Action::Quit)
    );
    assert_eq!(
        action(KeyEvent::new(KeyCode::Char('p'), KeyModifiers::ALT)),
        None
    );
}
