// AI-hint: mios-controls devices: connected keyboards, mice and controllers read from /proc/bus/input/devices (name, USB ids, event node, KEY/ABS/REL capability bitmaps) and the controller family ([desktop.controls.pad_families]); read-only (no open, no grab), so mios-inputd's exclusive controller grab is never disturbed.
// AI-related: tools/native/mios-inputd/src/linux.rs, tools/native/mios-controls/src/layout.rs, usr/share/mios/mios.toml
// AI-functions: scan, parse, Device::family, Family::from_ssot
use crate::model::{at, str_list};
use std::collections::{BTreeMap, BTreeSet};
use toml::Value;

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum Kind {
    Keyboard,
    Mouse,
    Controller,
}

impl Kind {
    pub fn word(self) -> &'static str {
        match self {
            Kind::Keyboard => "keyboard",
            Kind::Mouse => "mouse",
            Kind::Controller => "controller",
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Device {
    pub name: String,
    pub kind: Kind,
    pub handlers: String,
    pub vendor: u16,
    pub product: u16,
    /// KEY capability codes (keys and buttons).
    pub keys: BTreeSet<u16>,
    pub abs: BTreeSet<u16>,
    pub rel: BTreeSet<u16>,
}

pub const KEY_A: u16 = 30;
pub const BTN_LEFT: u16 = 0x110;
pub const BTN_SOUTH: u16 = 0x130;
pub const REL_X: u16 = 0;
/// mios-inputd's virtual output devices (VIRTUAL_PREFIX in mios-inputd/src/config.rs).
pub const VIRTUAL_PREFIX: &str = "mios-vctl";
/// Private test fixtures: mios-inputd's live bridge refuses them, so only their owner (and this editor) reads them.
pub const FIXTURE_PREFIX: &str = "mios-input-fixture-";

impl Device {
    /// The evdev node name (eventN) from the handlers line.
    pub fn event(&self) -> Option<&str> {
        self.handlers.split_whitespace().find(|h| h.starts_with("event") && h[5..].bytes().all(|b| b.is_ascii_digit()))
    }
    pub fn is_virtual(&self) -> bool {
        self.name.starts_with(VIRTUAL_PREFIX)
    }
    pub fn is_fixture(&self) -> bool {
        self.name.starts_with(FIXTURE_PREFIX)
    }
    pub fn has(&self, code: u16) -> bool {
        self.keys.contains(&code)
    }
    pub fn family<'a>(&self, families: &'a [Family]) -> &'a Family {
        let lname = self.name.to_ascii_lowercase();
        families
            .iter()
            .find(|f| f.vendors.contains(&self.vendor) && self.vendor != 0)
            .or_else(|| families.iter().find(|f| f.name_hints.iter().any(|h| !h.is_empty() && lname.contains(h.as_str()))))
            .or_else(|| families.iter().find(|f| f.id == "generic"))
            .unwrap_or(&families[0])
    }
}

/// A controller family: its glyphs (by button position) and drawing layout.
#[derive(Clone, Debug, PartialEq)]
pub struct Family {
    pub id: String,
    pub title: String,
    pub vendors: Vec<u16>,
    pub name_hints: Vec<String>,
    /// "offset" (left stick above the d-pad) or "symmetric" (d-pad above both sticks)
    pub layout: String,
    pub labels: BTreeMap<String, String>,
    pub vt_labels: BTreeMap<String, String>,
}

impl Family {
    pub fn generic() -> Self {
        let labels = [
            ("BTN_SOUTH", "S"),
            ("BTN_EAST", "E"),
            ("BTN_WEST", "W"),
            ("BTN_NORTH", "N"),
            ("BTN_TL", "L1"),
            ("BTN_TR", "R1"),
            ("BTN_TL2", "L2"),
            ("BTN_TR2", "R2"),
            ("BTN_SELECT", "Select"),
            ("BTN_START", "Start"),
            ("BTN_MODE", "Guide"),
            ("BTN_THUMBL", "L3"),
            ("BTN_THUMBR", "R3"),
        ];
        Self {
            id: "generic".into(),
            title: "Generic".into(),
            vendors: Vec::new(),
            name_hints: Vec::new(),
            layout: "offset".into(),
            labels: labels.iter().map(|(k, v)| (k.to_string(), v.to_string())).collect(),
            vt_labels: BTreeMap::new(),
        }
    }

    /// [[desktop.controls.pad_families]]; a missing or empty list degrades to the built-in generic family.
    pub fn from_ssot(merged: &Value) -> Vec<Family> {
        let mut out = Vec::new();
        for f in at(merged, "desktop.controls.pad_families").and_then(Value::as_array).into_iter().flatten() {
            let s = |k: &str| f.get(k).and_then(Value::as_str).unwrap_or("").to_owned();
            let map = |k: &str| -> BTreeMap<String, String> {
                f.get(k)
                    .and_then(Value::as_table)
                    .map(|t| t.iter().filter_map(|(k, v)| v.as_str().map(|v| (k.clone(), v.to_owned()))).collect())
                    .unwrap_or_default()
            };
            let id = s("id");
            if id.is_empty() {
                continue;
            }
            out.push(Family {
                title: if s("title").is_empty() { id.clone() } else { s("title") },
                id,
                vendors: str_list(f.get("vendors")).iter().filter_map(|v| u16::from_str_radix(v.trim_start_matches("0x"), 16).ok()).collect(),
                name_hints: str_list(f.get("name_hints")).into_iter().map(|h| h.to_ascii_lowercase()).collect(),
                layout: if s("layout") == "symmetric" { "symmetric".into() } else { "offset".into() },
                labels: map("labels"),
                vt_labels: map("vt_labels"),
            });
        }
        if !out.iter().any(|f| f.id == "generic") {
            out.push(Family::generic());
        }
        out
    }

    /// The label of a button in this family; `ascii` picks the VT-safe label and refuses non-ASCII glyphs.
    pub fn label(&self, button: &str, ascii: bool) -> String {
        if ascii {
            if let Some(v) = self.vt_labels.get(button) {
                return v.clone();
            }
        }
        match self.labels.get(button) {
            Some(v) if !ascii || v.is_ascii() => v.clone(),
            _ => Family::generic().labels.get(button).cloned().unwrap_or_else(|| button.trim_start_matches("BTN_").to_owned()),
        }
    }
}

/// Every set bit of a /proc capability bitmap (space-separated hex longs, most significant first).
pub fn bits(bitmap: &str) -> BTreeSet<u16> {
    let words: Vec<&str> = bitmap.split_whitespace().collect();
    let mut out = BTreeSet::new();
    for (idx, word) in words.iter().rev().enumerate() {
        let Ok(w) = u64::from_str_radix(word, 16) else { continue };
        for b in 0..64 {
            if w >> b & 1 == 1 {
                if let Ok(code) = u16::try_from(idx * 64 + b) {
                    out.insert(code);
                }
            }
        }
    }
    out
}

pub fn parse(text: &str) -> Vec<Device> {
    let mut out = Vec::new();
    for block in text.split("\n\n") {
        let (mut name, mut handlers, mut keys, mut abs, mut rel) = (String::new(), String::new(), String::new(), String::new(), String::new());
        let (mut vendor, mut product) = (0u16, 0u16);
        // A device name may carry a newline (USB product strings such as "OBINS OBINS\nAnnePro2 Keyboard"): the
        // quoted value then continues on the next lines until its closing quote; they join with a space.
        let mut open_name = false;
        for line in block.lines() {
            if open_name {
                let part = line.trim();
                open_name = !part.ends_with('"');
                name = format!("{name} {}", part.trim_end_matches('"')).trim().to_owned();
            } else if let Some(n) = line.strip_prefix("N: Name=") {
                let n = n.trim();
                open_name = n.starts_with('"') && (n.len() == 1 || !n.ends_with('"'));
                name = n.trim_matches('"').to_owned();
            } else if let Some(h) = line.strip_prefix("H: Handlers=") {
                handlers = h.trim().to_owned();
            } else if let Some(k) = line.strip_prefix("B: KEY=") {
                keys = k.trim().to_owned();
            } else if let Some(k) = line.strip_prefix("B: ABS=") {
                abs = k.trim().to_owned();
            } else if let Some(k) = line.strip_prefix("B: REL=") {
                rel = k.trim().to_owned();
            } else if let Some(i) = line.strip_prefix("I: ") {
                for part in i.split_whitespace() {
                    if let Some(v) = part.strip_prefix("Vendor=") {
                        vendor = u16::from_str_radix(v, 16).unwrap_or(0);
                    } else if let Some(v) = part.strip_prefix("Product=") {
                        product = u16::from_str_radix(v, 16).unwrap_or(0);
                    }
                }
            }
        }
        if name.is_empty() || keys.is_empty() {
            continue;
        }
        let keys = bits(&keys);
        let rel = bits(&rel);
        let abs = bits(&abs);
        let kbd = handlers.split_whitespace().any(|h| h == "kbd");
        let mut kinds = Vec::new();
        if keys.contains(&BTN_SOUTH) {
            kinds.push(Kind::Controller);
        } else {
            if keys.contains(&KEY_A) && kbd {
                kinds.push(Kind::Keyboard);
            }
            if keys.contains(&BTN_LEFT) && (rel.contains(&REL_X) || handlers.split_whitespace().any(|h| h.starts_with("mouse"))) {
                kinds.push(Kind::Mouse);
            }
        }
        for kind in kinds {
            out.push(Device { name: name.clone(), kind, handlers: handlers.clone(), vendor, product, keys: keys.clone(), abs: abs.clone(), rel: rel.clone() });
        }
    }
    out
}

pub fn scan() -> Vec<Device> {
    std::fs::read_to_string("/proc/bus/input/devices").map(|t| parse(&t)).unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;

    pub const SAMPLE: &str = "I: Bus=0003 Vendor=045e Product=028e Version=0114\nN: Name=\"Microsoft X-Box 360 pad\"\nH: Handlers=event20 js0\nB: EV=20000b\nB: KEY=7cdb000000000000 0 0 0 0\nB: ABS=3003f\n\nI: Bus=0011\nN: Name=\"AT Translated Set 2 keyboard\"\nH: Handlers=sysrq kbd event3 leds\nB: EV=120013\nB: KEY=402000000 3803078f800d001 feffffdfffefffff fffffffffffffffe\n\nI: Bus=0019\nN: Name=\"Power Button\"\nH: Handlers=kbd event1\nB: EV=3\nB: KEY=10000000000000 0\n\nI: Bus=0003 Vendor=046d Product=c52b\nN: Name=\"Mouse\"\nH: Handlers=mouse0 event5\nB: KEY=1f0000 0 0 0 0\nB: REL=1943\n\nI: Bus=0003 Vendor=054c Product=0ce6\nN: Name=\"Sony Interactive Entertainment DualSense Wireless Controller\"\nH: Handlers=event21 js1\nB: KEY=7fdb000000000000 0 0 0 0\nB: ABS=3003f\n";

    #[test]
    fn classifies_pads_keyboards_and_mice_and_skips_buttons() {
        let d = parse(SAMPLE);
        let kinds: Vec<_> = d.iter().map(|d| (d.kind, d.name.as_str())).collect();
        assert_eq!(kinds.len(), 4, "{d:#?}");
        assert_eq!(d[0].kind, Kind::Controller);
        assert_eq!(d[0].event(), Some("event20"));
        assert_eq!((d[0].vendor, d[0].product), (0x045e, 0x028e));
        assert_eq!(d[1].kind, Kind::Keyboard);
        assert_eq!(d[2].kind, Kind::Mouse);
        assert!(d[2].has(0x113) && d[2].has(0x114) && !d[2].has(0x115), "side and extra buttons from the KEY map");
        assert!(d[2].rel.contains(&8), "REL_WHEEL");
        assert!(!kinds.iter().any(|(_, n)| *n == "Power Button"));
    }

    #[test]
    fn bitmap_indexing_is_most_significant_word_first() {
        assert!(bits("1 0").contains(&64));
        assert!(!bits("1 0").contains(&0));
        assert!(bits("0 1").contains(&0));
        assert!(bits("1").contains(&0) && !bits("1").contains(&64));
    }

    #[test]
    fn a_name_with_a_newline_keeps_its_second_line() {
        let text = "I: Bus=0003 Vendor=04d9 Product=a293\nN: Name=\"OBINS OBINS\nAnnePro2 Keyboard\"\nH: Handlers=kbd event11\nB: KEY=1000000000007 ff980000000007ff febeffdfffefffff fffffffffffffffe\n\nI: Bus=0011\nN: Name=\"AT Translated Set 2 keyboard\"\nH: Handlers=sysrq kbd event3 leds\nB: KEY=402000000 3803078f800d001 feffffdfffefffff fffffffffffffffe\n";
        let d = parse(text);
        assert_eq!(d.len(), 2, "{d:?}");
        assert_eq!(d[0].name, "OBINS OBINS AnnePro2 Keyboard");
        assert_eq!(d[0].kind, Kind::Keyboard);
        assert_eq!(d[1].name, "AT Translated Set 2 keyboard", "a one-line name is unchanged");
    }

    #[test]
    fn family_by_vendor_then_name_then_generic() {
        let fams = Family::from_ssot(&Value::Table(Default::default()));
        assert_eq!(fams.len(), 1, "no SSOT list degrades to generic");
        let ssot: Value = "[[desktop.controls.pad_families]]\nid=\"xbox\"\nvendors=[\"045e\"]\nname_hints=[\"xbox\"]\nlabels={BTN_SOUTH=\"A\"}\n[[desktop.controls.pad_families]]\nid=\"playstation\"\nvendors=[\"054c\"]\nlayout=\"symmetric\"\nlabels={BTN_SOUTH=\"\\u2715\"}\nvt_labels={BTN_SOUTH=\"X\"}\n".parse().unwrap();
        let fams = Family::from_ssot(&ssot);
        let d = parse(SAMPLE);
        assert_eq!(d[0].family(&fams).id, "xbox");
        let ps = d[3].family(&fams);
        assert_eq!(ps.id, "playstation");
        assert_eq!(ps.layout, "symmetric");
        assert_eq!(ps.label("BTN_SOUTH", false), "\u{2715}");
        assert_eq!(ps.label("BTN_SOUTH", true), "X", "the VT gets ASCII");
        assert_eq!(ps.label("BTN_EAST", true), "E", "unlabelled buttons fall back to generic");
        let mut generic_pad = d[0].clone();
        generic_pad.vendor = 0x2dc8;
        generic_pad.name = "8BitDo Pro 2".into();
        assert_eq!(generic_pad.family(&fams).id, "generic");
        generic_pad.name = "Some Xbox Wireless pad".into();
        assert_eq!(generic_pad.family(&fams).id, "xbox", "name hint");
    }
}
