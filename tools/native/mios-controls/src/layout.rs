// AI-hint: mios-controls layout: the device drawings -- a full ANSI keyboard (main block, navigation cluster, numpad) with shared-border key caps, a mouse (buttons, wheel, tilt, side buttons) and a controller per family layout (face buttons, d-pad, sticks with directions, triggers, shoulders, Select/Guide/Start) -- painted into a ratatui buffer with junction-correct box drawing, plain ASCII on the Linux VT, and the hit rectangles the mouse uses; plus the key-name tables (US shifted symbols, tmux key names, Hyprland mouse codes).
// AI-related: tools/native/mios-controls/src/tui.rs, tools/native/mios-controls/src/bindmap.rs, tools/native/mios-controls/src/devices.rs (Family), tools/native/mios-resolver/src/keybindings.rs (normalize_chord)
// AI-functions: keyboard_caps, mouse_elements, pad_elements, paint, paint_flat, key_alias, shifted, tmux_to_key, key_to_tmux, Glyphs::for_term
use crate::devices::Family;
use ratatui::buffer::Buffer;
use ratatui::layout::Rect;
use ratatui::style::Style;

/// Drawing glyph policy: box-drawing lines, or ASCII (`+ - |`) where the font may lack them.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Glyphs {
    pub ascii_lines: bool,
    /// ASCII labels (arrows as ^ v < >, the family's vt_labels): the Linux VT font has no PlayStation shapes.
    pub ascii_labels: bool,
}

impl Glyphs {
    /// [desktop.controls].glyphs = auto | unicode | ascii. auto keeps box drawing (every console font has it) but uses
    /// ASCII labels on TERM=linux.
    pub fn for_term(setting: &str, term: &str) -> Self {
        match setting {
            "ascii" => Glyphs { ascii_lines: true, ascii_labels: true },
            "unicode" => Glyphs { ascii_lines: false, ascii_labels: false },
            _ => Glyphs { ascii_lines: false, ascii_labels: term == "linux" },
        }
    }
    pub fn arrow(&self, dir: &str) -> &'static str {
        match (dir, self.ascii_labels) {
            ("up", true) => "^",
            ("down", true) => "v",
            ("left", true) => "<",
            ("right", true) => ">",
            ("up", false) => "\u{2191}",
            ("down", false) => "\u{2193}",
            ("left", false) => "\u{2190}",
            _ => "\u{2192}",
        }
    }
}

/// One drawable element: an input (empty = decoration such as a body outline) in a box with inclusive corners.
#[derive(Clone, Debug, PartialEq)]
pub struct El {
    pub input: String,
    pub label: String,
    pub x0: u16,
    pub y0: u16,
    pub x1: u16,
    pub y1: u16,
}

impl El {
    fn new(input: &str, label: &str, x0: u16, y0: u16, x1: u16, y1: u16) -> Self {
        Self { input: input.into(), label: label.into(), x0, y0, x1, y1 }
    }
}

/// How one element is drawn; `prio` decides which element owns a shared border cell.
#[derive(Clone, Copy, Debug, Default)]
pub struct Look {
    pub border: Style,
    pub label: Style,
    pub prio: u8,
}

const U: u8 = 1;
const D: u8 = 2;
const L: u8 = 4;
const R: u8 = 8;

fn line_glyph(mask: u8, ascii: bool) -> &'static str {
    if ascii {
        return match mask {
            m if m == L | R || m == L || m == R => "-",
            m if m == U | D || m == U || m == D => "|",
            0 => " ",
            _ => "+",
        };
    }
    match mask {
        m if m == L | R || m == L || m == R => "\u{2500}",
        m if m == U | D || m == U || m == D => "\u{2502}",
        m if m == R | D => "\u{250c}",
        m if m == L | D => "\u{2510}",
        m if m == U | R => "\u{2514}",
        m if m == U | L => "\u{2518}",
        m if m == U | D | R => "\u{251c}",
        m if m == U | D | L => "\u{2524}",
        m if m == L | R | D => "\u{252c}",
        m if m == L | R | U => "\u{2534}",
        0 => " ",
        _ => "\u{253c}",
    }
}

/// Paint boxed elements (offset by `area`, clipped to it) with shared borders resolved into proper junctions.
/// Returns the screen rectangle of every input element (for mouse hits).
pub fn paint(buf: &mut Buffer, area: Rect, els: &[El], g: Glyphs, look: &dyn Fn(&str) -> Look) -> Vec<(Rect, String)> {
    let (w, h) = (area.width as usize, area.height as usize);
    if w == 0 || h == 0 {
        return Vec::new();
    }
    let mut mask = vec![0u8; w * h];
    let mut owner: Vec<Option<(u8, Style)>> = vec![None; w * h];
    let mut hits = Vec::new();
    let mut mark = |x: u16, y: u16, m: u8, l: &Look| {
        let (x, y) = (x as usize, y as usize);
        if x < w && y < h {
            mask[y * w + x] |= m;
            let o = &mut owner[y * w + x];
            if o.is_none_or(|(p, _)| l.prio >= p) {
                *o = Some((l.prio, l.border));
            }
        }
    };
    let looks: Vec<Look> = els.iter().map(|e| look(&e.input)).collect();
    for (e, l) in els.iter().zip(&looks) {
        if e.x1 <= e.x0 || e.y1 <= e.y0 {
            continue;
        }
        for x in e.x0..=e.x1 {
            let hm = if x == e.x0 { R } else if x == e.x1 { L } else { L | R };
            mark(x, e.y0, hm | if x == e.x0 || x == e.x1 { D } else { 0 }, l);
            mark(x, e.y1, hm | if x == e.x0 || x == e.x1 { U } else { 0 }, l);
        }
        for y in e.y0 + 1..e.y1 {
            mark(e.x0, y, U | D, l);
            mark(e.x1, y, U | D, l);
        }
    }
    for y in 0..h {
        for x in 0..w {
            let m = mask[y * w + x];
            if m == 0 {
                continue;
            }
            let style = owner[y * w + x].map(|(_, s)| s).unwrap_or_default();
            if let Some(cell) = buf.cell_mut((area.x + x as u16, area.y + y as u16)) {
                cell.set_symbol(line_glyph(m, g.ascii_lines)).set_style(style);
            }
        }
    }
    for (e, l) in els.iter().zip(&looks) {
        if e.x1 <= e.x0 + 1 || e.y1 <= e.y0 + 1 {
            continue;
        }
        let inner_w = (e.x1 - e.x0 - 1) as usize;
        let label: String = e.label.chars().take(inner_w).collect();
        let pad = (inner_w - label.chars().count()) / 2;
        let ly = e.y0 + (e.y1 - e.y0) / 2;
        for y in e.y0 + 1..e.y1 {
            for x in e.x0 + 1..e.x1 {
                if let Some(cell) = buf.cell_mut((area.x.saturating_add(x), area.y.saturating_add(y))) {
                    if (x as usize) < w && (y as usize) < h {
                        cell.set_symbol(" ").set_style(l.label);
                    }
                }
            }
        }
        if (ly as usize) < h {
            let text = format!("{}{}", " ".repeat(pad), label);
            let max = w.saturating_sub(e.x0 as usize + 1);
            let text: String = text.chars().take(max).collect();
            buf.set_string(area.x + e.x0 + 1, area.y + ly, text, l.label);
        }
        if !e.input.is_empty() {
            let x0 = area.x + e.x0;
            let y0 = area.y + e.y0;
            let rw = (e.x1 - e.x0 + 1).min(area.width.saturating_sub(e.x0));
            let rh = (e.y1 - e.y0 + 1).min(area.height.saturating_sub(e.y0));
            if rw > 0 && rh > 0 {
                hits.push((Rect::new(x0, y0, rw, rh), e.input.clone()));
            }
        }
    }
    hits
}

/// A flat drawing (no borders) for tiny areas: each element's label at its top-left, styled as its look.
pub fn paint_flat(buf: &mut Buffer, area: Rect, els: &[El], look: &dyn Fn(&str) -> Look) -> Vec<(Rect, String)> {
    let mut hits = Vec::new();
    for e in els.iter().filter(|e| !e.input.is_empty()) {
        if e.x0 >= area.width || e.y0 >= area.height {
            continue;
        }
        let width = (e.x1.saturating_sub(e.x0)).max(1).min(area.width - e.x0);
        let label: String = e.label.chars().take(width as usize).collect();
        let text = format!("{label:<width$}", width = width as usize);
        let l = look(&e.input);
        buf.set_string(area.x + e.x0, area.y + e.y0, text, l.label);
        hits.push((Rect::new(area.x + e.x0, area.y + e.y0, width, 1), e.input.clone()));
    }
    hits
}

// ---- keyboard -------------------------------------------------------------------------------------------------

/// A key cap in quarter-key units: x and width in quarters of a 1u key, row 0 = function row.
#[derive(Clone, Debug, PartialEq)]
pub struct Cap {
    pub label: &'static str,
    /// the chord key token (normalize_chord lowercase) or a modifier name (SUPER CTRL ALT SHIFT)
    pub token: &'static str,
    pub xq: u16,
    pub row: u16,
    pub wq: u16,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum KbdSize {
    Full,
    Tenkeyless,
    Main,
}

impl KbdSize {
    pub fn quarters(self) -> u16 {
        match self {
            KbdSize::Full => 92,
            KbdSize::Tenkeyless => 74,
            KbdSize::Main => 60,
        }
    }
}

/// The ANSI keyboard: main block (60q), navigation cluster (62..74q) and numpad (76..92q).
pub fn keyboard_caps(size: KbdSize) -> Vec<Cap> {
    let mut v = Vec::new();
    let mut put = |label: &'static str, token: &'static str, xq: u16, row: u16, wq: u16| v.push(Cap { label, token, xq, row, wq });
    put("Esc", "escape", 0, 0, 4);
    let fkeys = [("F1", "f1"), ("F2", "f2"), ("F3", "f3"), ("F4", "f4"), ("F5", "f5"), ("F6", "f6"), ("F7", "f7"), ("F8", "f8"), ("F9", "f9"), ("F10", "f10"), ("F11", "f11"), ("F12", "f12")];
    for (i, (l, t)) in fkeys.iter().enumerate() {
        let group = i as u16 / 4;
        put(l, t, 8 + group * 18 + (i as u16 % 4) * 4, 0, 4);
    }
    let r1 = [("`", "grave"), ("1", "1"), ("2", "2"), ("3", "3"), ("4", "4"), ("5", "5"), ("6", "6"), ("7", "7"), ("8", "8"), ("9", "9"), ("0", "0"), ("-", "minus"), ("=", "equal")];
    for (i, (l, t)) in r1.iter().enumerate() {
        put(l, t, i as u16 * 4, 1, 4);
    }
    put("Bksp", "backspace", 52, 1, 8);
    put("Tab", "tab", 0, 2, 6);
    let r2 = [("Q", "q"), ("W", "w"), ("E", "e"), ("R", "r"), ("T", "t"), ("Y", "y"), ("U", "u"), ("I", "i"), ("O", "o"), ("P", "p"), ("[", "bracketleft"), ("]", "bracketright")];
    for (i, (l, t)) in r2.iter().enumerate() {
        put(l, t, 6 + i as u16 * 4, 2, 4);
    }
    put("\\", "backslash", 54, 2, 6);
    put("Caps", "caps_lock", 0, 3, 7);
    let r3 = [("A", "a"), ("S", "s"), ("D", "d"), ("F", "f"), ("G", "g"), ("H", "h"), ("J", "j"), ("K", "k"), ("L", "l"), (";", "semicolon"), ("'", "apostrophe")];
    for (i, (l, t)) in r3.iter().enumerate() {
        put(l, t, 7 + i as u16 * 4, 3, 4);
    }
    put("Enter", "return", 51, 3, 9);
    put("Shift", "SHIFT", 0, 4, 9);
    let r4 = [("Z", "z"), ("X", "x"), ("C", "c"), ("V", "v"), ("B", "b"), ("N", "n"), ("M", "m"), (",", "comma"), (".", "period"), ("/", "slash")];
    for (i, (l, t)) in r4.iter().enumerate() {
        put(l, t, 9 + i as u16 * 4, 4, 4);
    }
    put("Shift", "SHIFT", 49, 4, 11);
    put("Ctrl", "CTRL", 0, 5, 5);
    put("Super", "SUPER", 5, 5, 5);
    put("Alt", "ALT", 10, 5, 5);
    put("Space", "space", 15, 5, 25);
    put("Alt", "ALT", 40, 5, 5);
    put("Super", "SUPER", 45, 5, 5);
    put("Menu", "menu", 50, 5, 5);
    put("Ctrl", "CTRL", 55, 5, 5);
    if size != KbdSize::Main {
        put("PrtSc", "print", 62, 0, 4);
        put("ScrLk", "scroll_lock", 66, 0, 4);
        put("Pause", "pause", 70, 0, 4);
        put("Ins", "insert", 62, 1, 4);
        put("Home", "home", 66, 1, 4);
        put("PgUp", "prior", 70, 1, 4);
        put("Del", "delete", 62, 2, 4);
        put("End", "end", 66, 2, 4);
        put("PgDn", "next", 70, 2, 4);
        put("Up", "up", 66, 4, 4);
        put("Left", "left", 62, 5, 4);
        put("Down", "down", 66, 5, 4);
        put("Right", "right", 70, 5, 4);
    }
    if size == KbdSize::Full {
        put("Num", "num_lock", 76, 1, 4);
        put("/", "kp_divide", 80, 1, 4);
        put("*", "kp_multiply", 84, 1, 4);
        put("-", "kp_subtract", 88, 1, 4);
        put("7", "kp_7", 76, 2, 4);
        put("8", "kp_8", 80, 2, 4);
        put("9", "kp_9", 84, 2, 4);
        put("+", "kp_add", 88, 2, 4);
        put("4", "kp_4", 76, 3, 4);
        put("5", "kp_5", 80, 3, 4);
        put("6", "kp_6", 84, 3, 4);
        put("1", "kp_1", 76, 4, 4);
        put("2", "kp_2", 80, 4, 4);
        put("3", "kp_3", 84, 4, 4);
        put("Ent", "kp_enter", 88, 4, 4);
        put("0", "kp_0", 76, 5, 8);
        put(".", "kp_decimal", 84, 5, 4);
    }
    v
}

/// The keyboard input id of a cap (modifiers by name, keys by token).
pub fn cap_input(c: &Cap) -> String {
    format!("key:{}", c.token)
}

/// The cap label for the current glyph policy (arrows drawn as arrows unless ASCII).
fn cap_label(c: &Cap, g: Glyphs) -> String {
    match c.token {
        "up" | "down" | "left" | "right" => g.arrow(c.token).into(),
        _ => c.label.into(),
    }
}

/// The keyboard as boxed elements fitted into `w` x `h` cells, or None when even the main block cannot be boxed.
/// Wider areas get the navigation cluster and numpad. Rows share borders (13 rows for 6 key rows).
pub fn keyboard_elements(w: u16, h: u16, g: Glyphs) -> Option<(Vec<El>, KbdSize)> {
    if h < 13 {
        return None;
    }
    for size in [KbdSize::Full, KbdSize::Tenkeyless, KbdSize::Main] {
        let q = size.quarters() as f32;
        let s = ((w.saturating_sub(1)) as f32 / q).min(1.5);
        // the larger layouts only when a 1u key keeps a 3-character label; the main block down to 2 characters
        let min_unit = if size == KbdSize::Main { 3.0 } else { 4.0 };
        if s * 4.0 < min_unit {
            continue;
        }
        let col = |xq: u16| (xq as f32 * s).round() as u16;
        let els = keyboard_caps(size)
            .iter()
            .map(|c| El::new(&cap_input(c), &cap_label(c, g), col(c.xq), c.row * 2, col(c.xq + c.wq), c.row * 2 + 2))
            .collect();
        return Some((els, size));
    }
    None
}

/// The keyboard without borders for very small areas: the main block, one row per key row, labels truncated.
pub fn keyboard_flat(w: u16, h: u16, g: Glyphs) -> Vec<El> {
    let s = (w as f32 / 60.0).max(0.25);
    let gap = if h >= 12 { 2 } else { 1 };
    keyboard_caps(KbdSize::Main)
        .iter()
        .map(|c| {
            let x0 = (c.xq as f32 * s).floor() as u16;
            let x1 = ((c.xq + c.wq) as f32 * s).floor() as u16;
            El::new(&cap_input(c), &cap_label(c, g), x0, c.row * gap, x1.saturating_sub(1).max(x0 + 1), c.row * gap)
        })
        .collect()
}

/// X keysym-style names for the punctuation keys (Hyprland and the SSOT spell keys this way).
pub fn key_alias(token: &str) -> String {
    match token {
        "`" => "grave",
        "-" => "minus",
        "=" => "equal",
        "[" => "bracketleft",
        "]" => "bracketright",
        "\\" => "backslash",
        ";" => "semicolon",
        "'" => "apostrophe",
        "," => "comma",
        "." => "period",
        "/" => "slash",
        "enter" => "return",
        "esc" => "escape",
        "page_up" | "pageup" | "pgup" => "prior",
        "page_down" | "pagedown" | "pgdn" => "next",
        "del" => "delete",
        "ins" => "insert",
        "bspace" => "backspace",
        "caps" | "capslock" => "caps_lock",
        "super_l" | "super_r" | "meta_l" | "meta_r" => "SUPER",
        "control_l" | "control_r" => "CTRL",
        "alt_l" | "alt_r" => "ALT",
        "shift_l" | "shift_r" => "SHIFT",
        other => other,
    }
    .to_owned()
}

/// US layout: a shifted symbol -> (base key token, true). Letters: uppercase -> (lowercase, true).
pub fn shifted(c: char) -> Option<(String, bool)> {
    let base = match c {
        '!' => "1",
        '@' => "2",
        '#' => "3",
        '$' => "4",
        '%' => "5",
        '^' => "6",
        '&' => "7",
        '*' => "8",
        '(' => "9",
        ')' => "0",
        '_' => "minus",
        '+' => "equal",
        '{' => "bracketleft",
        '}' => "bracketright",
        '|' => "backslash",
        ':' => "semicolon",
        '"' => "apostrophe",
        '<' => "comma",
        '>' => "period",
        '?' => "slash",
        '~' => "grave",
        c if c.is_ascii_uppercase() => return Some((c.to_ascii_lowercase().to_string(), true)),
        c if c.is_ascii_graphic() => return Some((key_alias(&c.to_ascii_lowercase().to_string()), false)),
        ' ' => return Some(("space".into(), false)),
        _ => return None,
    };
    Some((base.into(), true))
}

/// The character a US keyboard types for (base token, shift), the inverse of `shifted` for single characters.
pub fn typed_char(token: &str, shift: bool) -> Option<char> {
    let plain = match token {
        "grave" => '`',
        "minus" => '-',
        "equal" => '=',
        "bracketleft" => '[',
        "bracketright" => ']',
        "backslash" => '\\',
        "semicolon" => ';',
        "apostrophe" => '\'',
        "comma" => ',',
        "period" => '.',
        "slash" => '/',
        t if t.len() == 1 && t.as_bytes()[0].is_ascii_alphanumeric() => t.chars().next().unwrap(),
        _ => return None,
    };
    if !shift {
        return Some(plain);
    }
    if plain.is_ascii_lowercase() {
        return Some(plain.to_ascii_uppercase());
    }
    "!@#$%^&*()_+{}|:\"<>?~".chars().find(|c| shifted(*c).is_some_and(|(b, s)| s && b == token))
}

/// A tmux key name as (prefix modifiers, base token, shift): `H` -> ([], h, true), `C-x` -> ([CTRL], x, false).
pub fn tmux_to_key(key: &str) -> Option<(Vec<&'static str>, String, bool)> {
    let mut mods = Vec::new();
    let mut rest = key;
    let mut shift = false;
    loop {
        match rest.get(..2) {
            Some("C-") if rest.len() > 2 => mods.push("CTRL"),
            Some("M-") if rest.len() > 2 => mods.push("ALT"),
            Some("S-") if rest.len() > 2 => shift = true,
            _ => break,
        }
        rest = &rest[2..];
    }
    let named = match rest {
        "Space" => Some(("space", false)),
        "Enter" => Some(("return", false)),
        "Tab" => Some(("tab", false)),
        "BTab" => Some(("tab", true)),
        "BSpace" => Some(("backspace", false)),
        "Escape" => Some(("escape", false)),
        "Up" => Some(("up", false)),
        "Down" => Some(("down", false)),
        "Left" => Some(("left", false)),
        "Right" => Some(("right", false)),
        "PPage" | "PageUp" => Some(("prior", false)),
        "NPage" | "PageDown" => Some(("next", false)),
        "Home" => Some(("home", false)),
        "End" => Some(("end", false)),
        "IC" => Some(("insert", false)),
        "DC" => Some(("delete", false)),
        _ => None,
    };
    if let Some((t, s)) = named {
        return Some((mods, t.into(), s || shift));
    }
    if let Some(n) = rest.strip_prefix('F').and_then(|n| n.parse::<u8>().ok()).filter(|n| (1..=12).contains(n)) {
        return Some((mods, format!("f{n}"), shift));
    }
    let mut chars = rest.chars();
    let c = chars.next()?;
    if chars.next().is_some() {
        return None;
    }
    let (base, s) = shifted(c)?;
    Some((mods, base, s || shift))
}

/// The tmux prefix-table key name for a key (no modifiers except shift), or None when tmux-os cannot bind it.
pub fn key_to_tmux(token: &str, shift: bool) -> Option<String> {
    let named = match (token, shift) {
        ("space", false) => Some("Space"),
        ("return", false) => Some("Enter"),
        ("tab", false) => Some("Tab"),
        ("tab", true) => Some("BTab"),
        ("backspace", false) => Some("BSpace"),
        ("up", false) => Some("Up"),
        ("down", false) => Some("Down"),
        ("left", false) => Some("Left"),
        ("right", false) => Some("Right"),
        ("prior", false) => Some("PPage"),
        ("next", false) => Some("NPage"),
        _ => None,
    };
    if let Some(n) = named {
        return Some(n.into());
    }
    typed_char(token, shift).map(|c| c.to_string())
}

/// A chord key token for a mouse button or wheel ("mouse:272", "mouse_up") -> the mouse input id.
pub fn mouse_input(token: &str) -> Option<String> {
    match token {
        "mouse_up" => Some("mouse:wheel_up".into()),
        "mouse_down" => Some("mouse:wheel_down".into()),
        "mouse_left" => Some("mouse:wheel_left".into()),
        "mouse_right" => Some("mouse:wheel_right".into()),
        t => t.strip_prefix("mouse:").filter(|n| n.parse::<u16>().is_ok()).map(|n| format!("mouse:{n}")),
    }
}

/// The chord key token for a mouse input id (the inverse of mouse_input).
pub fn mouse_token(input: &str) -> Option<String> {
    let rest = input.strip_prefix("mouse:")?;
    Some(match rest {
        "wheel_up" => "mouse_up".into(),
        "wheel_down" => "mouse_down".into(),
        "wheel_left" => "mouse_left".into(),
        "wheel_right" => "mouse_right".into(),
        n => format!("mouse:{n}"),
    })
}

/// Linux mouse button codes (Hyprland's mouse:<code>) with their drawing labels.
pub const MOUSE_BUTTONS: &[(u16, &str, &str)] = &[
    (272, "Left", "left button"),
    (273, "Right", "right button"),
    (274, "Mid", "middle button (wheel click)"),
    (275, "Side", "side button (BTN_SIDE, usually back)"),
    (276, "Extra", "extra button (BTN_EXTRA, usually forward)"),
    (277, "Fwd", "forward button (BTN_FORWARD)"),
    (278, "Back", "back button (BTN_BACK)"),
    (279, "Task", "task button (BTN_TASK)"),
];

pub fn mouse_name(input: &str) -> String {
    match input.strip_prefix("mouse:") {
        Some("wheel_up") => "wheel up".into(),
        Some("wheel_down") => "wheel down".into(),
        Some("wheel_left") => "wheel tilt left".into(),
        Some("wheel_right") => "wheel tilt right".into(),
        Some(n) => n.parse::<u16>().ok().and_then(|c| MOUSE_BUTTONS.iter().find(|b| b.0 == c)).map(|b| b.2.to_string()).unwrap_or_else(|| format!("button {n}")),
        None => input.into(),
    }
}

/// The mouse: buttons, wheel (up/down/click/tilt) and side buttons on a 30 x 17 grid; `sx` widens it.
pub fn mouse_elements(sx: u16, g: Glyphs) -> Vec<El> {
    let x = |v: u16| v * sx;
    vec![
        El::new("", "", x(5), 8, x(29), 16),
        El::new("mouse:272", "Left", x(5), 0, x(14), 8),
        El::new("mouse:wheel_up", g.arrow("up"), x(14), 0, x(20), 2),
        El::new("mouse:274", "Mid", x(14), 2, x(20), 4),
        El::new("mouse:wheel_down", g.arrow("down"), x(14), 4, x(20), 6),
        El::new("mouse:wheel_left", g.arrow("left"), x(14), 6, x(17), 8),
        El::new("mouse:wheel_right", g.arrow("right"), x(17), 6, x(20), 8),
        El::new("mouse:273", "Right", x(20), 0, x(29), 8),
        El::new("mouse:276", "Extra", x(0), 9, x(5), 11),
        El::new("mouse:275", "Side", x(0), 11, x(5), 13),
        El::new("mouse:277", "Fwd", x(8), 11, x(14), 13),
        El::new("mouse:278", "Back", x(14), 11, x(20), 13),
        El::new("mouse:279", "Task", x(20), 11, x(26), 13),
    ]
}

pub const MOUSE_W: u16 = 30;
pub const MOUSE_H: u16 = 17;

// ---- controller ----------------------------------------------------------------------------------------------

pub const PAD_W: u16 = 56;
pub const PAD_H: u16 = 17;

/// Every controller input the drawing can select: buttons, d-pad and stick directions.
pub const PAD_INPUTS: &[&str] = &[
    "BTN_TL2", "BTN_TR2", "BTN_TL", "BTN_TR", "BTN_SELECT", "BTN_MODE", "BTN_START", "BTN_NORTH", "BTN_WEST", "BTN_EAST", "BTN_SOUTH", "BTN_DPAD_UP", "BTN_DPAD_LEFT",
    "BTN_DPAD_RIGHT", "BTN_DPAD_DOWN", "BTN_THUMBL", "LS_UP", "LS_LEFT", "LS_RIGHT", "LS_DOWN", "BTN_THUMBR", "RS_UP", "RS_LEFT", "RS_RIGHT", "RS_DOWN",
];

/// A plus-shaped cluster of 5x3 boxes around (cx, cy): up, left, (center), right, down.
fn cluster(out: &mut Vec<El>, cx: u16, cy: u16, ids: [&str; 5], labels: [String; 5]) {
    let pos = [(cx - 2, cy - 2, cx + 2, cy), (cx - 6, cy, cx - 2, cy + 2), (cx - 2, cy, cx + 2, cy + 2), (cx + 2, cy, cx + 6, cy + 2), (cx - 2, cy + 2, cx + 2, cy + 4)];
    for (i, (x0, y0, x1, y1)) in pos.into_iter().enumerate() {
        if ids[i].is_empty() && labels[i].is_empty() {
            continue;
        }
        out.push(El::new(ids[i], &labels[i], x0, y0, x1, y1));
    }
}

/// The controller drawing for a family: "offset" puts the left stick above the d-pad, "symmetric" the d-pad above
/// both sticks. `sx` widens it (1 or 2).
pub fn pad_elements(fam: &Family, sx: u16, g: Glyphs) -> Vec<El> {
    let l = |b: &str| fam.label(b, g.ascii_labels);
    let a = |d: &str| g.arrow(d).to_string();
    let mut v = vec![
        El::new("", "", 0, 4, 55, 16),
        El::new("BTN_TL2", &l("BTN_TL2"), 2, 0, 9, 2),
        El::new("BTN_TL", &l("BTN_TL"), 2, 2, 9, 4),
        El::new("BTN_TR2", &l("BTN_TR2"), 46, 0, 53, 2),
        El::new("BTN_TR", &l("BTN_TR"), 46, 2, 53, 4),
        El::new("BTN_SELECT", &l("BTN_SELECT"), 16, 5, 24, 7),
        El::new("BTN_MODE", &l("BTN_MODE"), 24, 5, 31, 7),
        El::new("BTN_START", &l("BTN_START"), 31, 5, 40, 7),
    ];
    let (stick_l, dpad, stick_r) = if fam.layout == "symmetric" { ((19, 10), (10, 7), (36, 10)) } else { ((10, 7), (19, 10), (36, 10)) };
    cluster(&mut v, stick_l.0, stick_l.1, ["LS_UP", "LS_LEFT", "BTN_THUMBL", "LS_RIGHT", "LS_DOWN"], [a("up"), a("left"), l("BTN_THUMBL"), a("right"), a("down")]);
    cluster(&mut v, dpad.0, dpad.1, ["BTN_DPAD_UP", "BTN_DPAD_LEFT", "", "BTN_DPAD_RIGHT", "BTN_DPAD_DOWN"], [a("up"), a("left"), String::new(), a("right"), a("down")]);
    cluster(&mut v, stick_r.0, stick_r.1, ["RS_UP", "RS_LEFT", "BTN_THUMBR", "RS_RIGHT", "RS_DOWN"], [a("up"), a("left"), l("BTN_THUMBR"), a("right"), a("down")]);
    cluster(&mut v, 46, 7, ["BTN_NORTH", "BTN_WEST", "", "BTN_EAST", "BTN_SOUTH"], [l("BTN_NORTH"), l("BTN_WEST"), String::new(), l("BTN_EAST"), l("BTN_SOUTH")]);
    if sx > 1 {
        for e in &mut v {
            e.x0 *= sx;
            e.x1 *= sx;
        }
    }
    v
}

/// A small controller layout (no outline) for areas under PAD_W x PAD_H: one labelled row per group.
pub fn pad_flat(fam: &Family, g: Glyphs) -> Vec<El> {
    let l = |b: &str| fam.label(b, g.ascii_labels);
    let a = |d: &str| g.arrow(d).to_string();
    let mut v = Vec::new();
    let mut row = |y: u16, items: Vec<(&str, String)>| {
        let mut x = 0u16;
        for (id, label) in items {
            let w = label.chars().count().max(1) as u16 + 1;
            v.push(El::new(id, &label, x, y, x + w, y));
            x += w + 1;
        }
    };
    row(0, vec![("BTN_TL2", l("BTN_TL2")), ("BTN_TL", l("BTN_TL")), ("BTN_TR", l("BTN_TR")), ("BTN_TR2", l("BTN_TR2"))]);
    row(1, vec![("BTN_SELECT", l("BTN_SELECT")), ("BTN_MODE", l("BTN_MODE")), ("BTN_START", l("BTN_START"))]);
    row(2, vec![("BTN_DPAD_UP", format!("D{}", a("up"))), ("BTN_DPAD_LEFT", format!("D{}", a("left"))), ("BTN_DPAD_RIGHT", format!("D{}", a("right"))), ("BTN_DPAD_DOWN", format!("D{}", a("down")))]);
    row(3, vec![("BTN_NORTH", l("BTN_NORTH")), ("BTN_WEST", l("BTN_WEST")), ("BTN_EAST", l("BTN_EAST")), ("BTN_SOUTH", l("BTN_SOUTH"))]);
    row(4, vec![("BTN_THUMBL", l("BTN_THUMBL")), ("LS_UP", format!("L{}", a("up"))), ("LS_LEFT", format!("L{}", a("left"))), ("LS_RIGHT", format!("L{}", a("right"))), ("LS_DOWN", format!("L{}", a("down")))]);
    row(5, vec![("BTN_THUMBR", l("BTN_THUMBR")), ("RS_UP", format!("R{}", a("up"))), ("RS_LEFT", format!("R{}", a("left"))), ("RS_RIGHT", format!("R{}", a("right"))), ("RS_DOWN", format!("R{}", a("down")))]);
    v.into_iter().map(|mut e| {
        e.input = format!("pad:{}", e.input);
        e
    }).collect()
}

/// Prefix pad element inputs with `pad:` (the drawing uses bare evdev names).
pub fn pad_ids(mut els: Vec<El>) -> Vec<El> {
    for e in &mut els {
        if !e.input.is_empty() {
            e.input = format!("pad:{}", e.input);
        }
    }
    els
}

/// The nearest element in a direction (spatial navigation of a drawing by arrows, d-pad or stick).
pub fn neighbour(els: &[El], from: &str, dx: i32, dy: i32) -> Option<String> {
    let center = |e: &El| ((e.x0 as i32 + e.x1 as i32) * 5, (e.y0 as i32 + e.y1 as i32) * 5);
    let cur = els.iter().find(|e| e.input == from)?;
    let (cx, cy) = center(cur);
    els.iter()
        .filter(|e| !e.input.is_empty() && e.input != from)
        .filter_map(|e| {
            let (x, y) = center(e);
            let (ddx, ddy) = (x - cx, y - cy);
            let along = ddx * dx + ddy * dy;
            if along <= 0 {
                return None;
            }
            // an element that shares the current one's span across the move is straight ahead and always wins over a
            // diagonal one: rows of staggered keys (q -> a, not q -> Super below it; a -> Caps, never up to q) and
            // columns of pad buttons. Only when nothing is straight ahead does the nearest diagonal element count.
            let overlaps = if dy != 0 { e.x0 < cur.x1 && cur.x0 < e.x1 } else { e.y0 < cur.y1 && cur.y0 < e.y1 };
            let off = (ddx * dy).abs() + (ddy * dx).abs();
            let across = if overlaps { 0 } else { off };
            // ties (two keys straight ahead) go to the one whose centre is closest across the move
            Some(((!overlaps, along + across * 3, off), e.input.clone()))
        })
        .min_by_key(|(score, _)| *score)
        .map(|(_, id)| id)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn text(buf: &Buffer) -> String {
        let a = buf.area;
        (0..a.height).map(|y| (0..a.width).map(|x| buf[(x, y)].symbol().to_string()).collect::<String>() + "\n").collect()
    }

    #[test]
    fn every_cap_token_is_a_chord_key_or_modifier() {
        for c in keyboard_caps(KbdSize::Full) {
            if ["SUPER", "CTRL", "ALT", "SHIFT"].contains(&c.token) {
                continue;
            }
            let n = mios_resolver::keybindings::normalize_chord(&format!("SUPER+{}", c.token), "\0").unwrap();
            assert_eq!(n, format!("SUPER+{}", c.token), "the drawing token is the normalized key");
        }
    }

    #[test]
    fn main_block_rows_are_fifteen_units_wide() {
        let caps = keyboard_caps(KbdSize::Main);
        for row in 0..6 {
            let end = caps.iter().filter(|c| c.row == row).map(|c| c.xq + c.wq).max().unwrap();
            assert_eq!(end, 60, "row {row}");
        }
        assert_eq!(keyboard_caps(KbdSize::Full).len(), 104, "ANSI 104 keys");
    }

    #[test]
    fn keyboard_fits_and_degrades_by_width() {
        let g = Glyphs { ascii_lines: false, ascii_labels: false };
        assert_eq!(keyboard_elements(140, 13, g).unwrap().1, KbdSize::Full);
        assert_eq!(keyboard_elements(80, 13, g).unwrap().1, KbdSize::Tenkeyless);
        assert_eq!(keyboard_elements(50, 13, g).unwrap().1, KbdSize::Main);
        assert!(keyboard_elements(40, 13, g).is_none());
        assert!(keyboard_elements(140, 12, g).is_none(), "boxes need 13 rows");
    }

    #[test]
    fn shared_borders_become_junctions_and_ascii_on_request() {
        let els = vec![El::new("a", "A", 0, 0, 4, 2), El::new("b", "B", 4, 0, 8, 2), El::new("c", "C", 0, 2, 4, 4)];
        let mut buf = Buffer::empty(Rect::new(0, 0, 9, 5));
        let area = buf.area;
        let hits = paint(&mut buf, area, &els, Glyphs { ascii_lines: false, ascii_labels: false }, &|_| Look::default());
        let t = text(&buf);
        assert_eq!(t.lines().next().unwrap(), "\u{250c}\u{2500}\u{2500}\u{2500}\u{252c}\u{2500}\u{2500}\u{2500}\u{2510}", "{t}");
        assert!(t.lines().nth(2).unwrap().starts_with("\u{251c}\u{2500}\u{2500}\u{2500}\u{253c}"), "{t}");
        assert_eq!(hits.len(), 3);
        let mut buf = Buffer::empty(Rect::new(0, 0, 9, 5));
        let area = buf.area;
        paint(&mut buf, area, &els, Glyphs { ascii_lines: true, ascii_labels: true }, &|_| Look::default());
        assert!(text(&buf).is_ascii(), "{}", text(&buf));
    }

    #[test]
    fn tmux_names_round_trip_through_keys() {
        for k in ["h", "H", "$", "?", "Space", "Enter", "Tab", "BTab", "BSpace", "Up", "PPage", "NPage", "`", "*", "!", "(", "{", "=", ",", "0", "&"] {
            let (mods, token, shift) = tmux_to_key(k).unwrap_or_else(|| panic!("{k}"));
            assert!(mods.is_empty());
            assert_eq!(key_to_tmux(&token, shift).as_deref(), Some(k), "{k} -> {token} {shift}");
        }
        assert_eq!(tmux_to_key("C-b").unwrap(), (vec!["CTRL"], "b".into(), false));
        assert_eq!(tmux_to_key("S-Enter").unwrap(), (vec![], "return".into(), true));
        assert_eq!(key_to_tmux("f5", false), None, "tmux-os prefix keys have no function keys");
    }

    #[test]
    fn mouse_ids_and_tokens_round_trip() {
        for t in ["mouse:272", "mouse:275", "mouse_up", "mouse_down", "mouse_left", "mouse_right"] {
            assert_eq!(mouse_token(&mouse_input(t).unwrap()).as_deref(), Some(t));
        }
        assert!(mouse_input("a").is_none());
        for e in mouse_elements(1, Glyphs { ascii_lines: false, ascii_labels: false }) {
            assert!(e.x1 < MOUSE_W && e.y1 < MOUSE_H);
        }
    }

    #[test]
    fn pad_layouts_place_every_input_inside_the_grid_and_differ_by_family() {
        let g = Glyphs { ascii_lines: false, ascii_labels: true };
        let mut ps = Family::generic();
        ps.layout = "symmetric".into();
        for fam in [Family::generic(), ps.clone()] {
            let els = pad_elements(&fam, 1, g);
            for id in PAD_INPUTS {
                let e = els.iter().find(|e| e.input == *id).unwrap_or_else(|| panic!("{id}"));
                assert!(e.x1 < PAD_W && e.y1 < PAD_H, "{id} {e:?}");
            }
        }
        let a = pad_elements(&Family::generic(), 1, g);
        let b = pad_elements(&ps, 1, g);
        let pos = |els: &[El], id: &str| els.iter().find(|e| e.input == id).map(|e| (e.x0, e.y0)).unwrap();
        assert!(pos(&a, "BTN_THUMBL").1 < pos(&a, "BTN_DPAD_UP").1, "offset: left stick above the d-pad");
        assert!(pos(&b, "BTN_THUMBL").1 > pos(&b, "BTN_DPAD_UP").1, "symmetric: d-pad above the sticks");
        let flat = pad_flat(&Family::generic(), g);
        assert_eq!(flat.len(), PAD_INPUTS.len());
    }

    #[test]
    fn spatial_navigation_moves_to_the_nearest_key_in_a_direction() {
        let g = Glyphs { ascii_lines: false, ascii_labels: false };
        let (els, _) = keyboard_elements(140, 13, g).unwrap();
        assert_eq!(neighbour(&els, "key:q", 1, 0).as_deref(), Some("key:w"));
        assert_eq!(neighbour(&els, "key:q", 0, 1).as_deref(), Some("key:a"));
        // q sits half under 1 and half under 2: up picks the closer centre, and down from there comes back to q
        let up = neighbour(&els, "key:q", 0, -1).expect("q has a key above it");
        assert!(up == "key:1" || up == "key:2", "{up}");
        assert_eq!(neighbour(&els, &up, 0, 1).as_deref(), Some("key:q"), "up then down returns to q");
        assert_eq!(neighbour(&els, "key:a", 0, 1).as_deref(), Some("key:z"), "staggered rows: straight down, not diagonal");
        assert_eq!(neighbour(&els, "key:a", -1, 0).as_deref(), Some("key:caps_lock"));
        assert_eq!(neighbour(&els, "key:w", 0, 1).as_deref(), Some("key:s"));
        assert_eq!(neighbour(&els, "key:escape", -1, 0), None);
    }
}
