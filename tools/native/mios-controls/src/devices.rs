// AI-hint: mios-controls devices: the connected keyboards and controllers, read from /proc/bus/input/devices capability bitmaps; read-only (no open, no grab), so mios-inputd's exclusive controller grab is never disturbed.
// AI-related: tools/native/mios-inputd, tools/native/mios-resolver/src/keybindings.rs (button_code)
// AI-functions: scan, parse
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Kind {
    Keyboard,
    Controller,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Device {
    pub name: String,
    pub kind: Kind,
    pub handlers: String,
}

const KEY_A: usize = 30;
const BTN_SOUTH: usize = 0x130;

/// Is `bit` set in a /proc KEY= bitmap (space-separated hex longs, most significant first)?
fn bit(bitmap: &str, bit: usize) -> bool {
    let words: Vec<&str> = bitmap.split_whitespace().collect();
    let idx = bit / 64;
    if idx >= words.len() {
        return false;
    }
    let word = words[words.len() - 1 - idx];
    u64::from_str_radix(word, 16).map(|w| w >> (bit % 64) & 1 == 1).unwrap_or(false)
}

pub fn parse(text: &str) -> Vec<Device> {
    let mut out = Vec::new();
    for block in text.split("\n\n") {
        let mut name = String::new();
        let mut handlers = String::new();
        let mut keys = String::new();
        for line in block.lines() {
            if let Some(n) = line.strip_prefix("N: Name=") {
                name = n.trim().trim_matches('"').to_owned();
            } else if let Some(h) = line.strip_prefix("H: Handlers=") {
                handlers = h.trim().to_owned();
            } else if let Some(k) = line.strip_prefix("B: KEY=") {
                keys = k.trim().to_owned();
            }
        }
        if name.is_empty() || keys.is_empty() {
            continue;
        }
        let kind = if bit(&keys, BTN_SOUTH) {
            Kind::Controller
        } else if bit(&keys, KEY_A) && handlers.split_whitespace().any(|h| h == "kbd") {
            Kind::Keyboard
        } else {
            continue;
        };
        out.push(Device { name, kind, handlers });
    }
    out
}

pub fn scan() -> Vec<Device> {
    std::fs::read_to_string("/proc/bus/input/devices").map(|t| parse(&t)).unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;

    const SAMPLE: &str = "I: Bus=0003 Vendor=045e Product=028e Version=0114\nN: Name=\"Microsoft X-Box 360 pad\"\nH: Handlers=event20 js0\nB: EV=20000b\nB: KEY=7cdb000000000000 0 0 0 0\n\nI: Bus=0011\nN: Name=\"AT Translated Set 2 keyboard\"\nH: Handlers=sysrq kbd event3 leds\nB: EV=120013\nB: KEY=402000000 3803078f800d001 feffffdfffefffff fffffffffffffffe\n\nI: Bus=0019\nN: Name=\"Power Button\"\nH: Handlers=kbd event1\nB: EV=3\nB: KEY=10000000000000 0\n\nN: Name=\"Mouse\"\nH: Handlers=mouse0 event5\nB: KEY=1f0000 0 0 0 0\n";

    #[test]
    fn classifies_pads_and_keyboards_and_skips_buttons_and_mice() {
        let d = parse(SAMPLE);
        assert_eq!(d.len(), 2, "{d:#?}");
        assert_eq!(d[0].kind, Kind::Controller);
        assert_eq!(d[0].name, "Microsoft X-Box 360 pad");
        assert_eq!(d[1].kind, Kind::Keyboard);
    }

    #[test]
    fn bitmap_indexing_is_most_significant_word_first() {
        assert!(bit("1 0", 64));
        assert!(!bit("1 0", 0));
        assert!(bit("0 1", 0));
        assert!(!bit("1", 64), "past the map is unset");
    }
}
