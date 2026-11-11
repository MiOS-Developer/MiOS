// AI-hint: Pure bounded ASCII shared-code editor; validation delegates to MonCode.
// AI-related: genetics.rs, ui.rs, input.rs
use crate::genetics::MonCode;
use std::str::FromStr;

// These are wire-format bounds, not operator preferences.
const MAX_INPUT: usize = 21;
const COMPACT_LEN: usize = 17;
const PREFIX_LEN: usize = 2;
const ALPHABET: &[u8; 32] = b"0123456789ABCDEFGHJKMNPQRSTVWXYZ";

#[derive(Clone, Debug, Default)]
pub struct SeedEntry {
    text: String,
    cursor: usize,
    error: Option<&'static str>,
    wheel_active: bool,
}

#[derive(Clone, Copy, Debug)]
pub enum Edit {
    Character(char),
    Left,
    Right,
    Up,
    Down,
    Home,
    End,
    Backspace,
    Delete,
    Clear,
}

impl SeedEntry {
    pub fn text(&self) -> &str {
        &self.text
    }

    /// ASCII caret offset; in wheel mode this selects one payload character.
    pub fn cursor(&self) -> usize {
        self.cursor
    }

    pub fn error(&self) -> Option<&str> {
        self.error
    }

    /// All stored characters are ASCII, so every caret offset is a UTF-8 boundary.
    pub fn edit(&mut self, edit: Edit) {
        self.error = None;
        match edit {
            Edit::Character(c) => {
                if !(c.is_ascii_alphanumeric() || c == '-') {
                    return;
                }
                if self.text.len() == MAX_INPUT {
                    self.error = Some("Code is limited to 21 characters");
                    return;
                }
                self.text.insert(self.cursor, c.to_ascii_uppercase());
                self.cursor += 1;
                self.wheel_active = false;
            }
            Edit::Left => {
                let first = if self.wheel_active { PREFIX_LEN } else { 0 };
                self.cursor = self.cursor.saturating_sub(1).max(first);
            }
            Edit::Right => {
                let last = if self.wheel_active {
                    COMPACT_LEN - 1
                } else {
                    self.text.len()
                };
                self.cursor = (self.cursor + 1).min(last);
            }
            Edit::Home => {
                self.cursor = if self.wheel_active { PREFIX_LEN } else { 0 };
            }
            Edit::End => {
                self.cursor = if self.wheel_active {
                    COMPACT_LEN - 1
                } else {
                    self.text.len()
                };
            }
            Edit::Backspace => {
                if self.cursor > 0 {
                    self.cursor -= 1;
                    self.text.remove(self.cursor);
                }
                self.wheel_active = false;
            }
            Edit::Delete => {
                if self.cursor < self.text.len() {
                    self.text.remove(self.cursor);
                }
                self.wheel_active = false;
            }
            Edit::Clear => {
                self.text.clear();
                self.cursor = 0;
                self.wheel_active = false;
            }
            Edit::Up => self.cycle(true),
            Edit::Down => self.cycle(false),
        }
    }

    /// Replace atomically: rejected clipboard data never changes the draft or caret.
    pub fn paste(&mut self, input: &str) {
        self.error = None;
        // Reject Unicode even if it would otherwise disappear during trimming.
        if !input.is_ascii() {
            self.error = Some("Paste only ASCII letters, digits and hyphens");
            return;
        }
        let draft = input.trim();
        if draft.len() > MAX_INPUT {
            self.error = Some("Code is limited to 21 characters");
            return;
        }
        if !draft
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || c == b'-')
        {
            self.error = Some("Paste only letters, digits and hyphens");
            return;
        }
        self.text = draft.to_ascii_uppercase();
        self.cursor = self.text.len();
        self.wheel_active = false;
    }

    pub fn validate(&mut self) -> Option<MonCode> {
        match MonCode::from_str(&self.text) {
            Ok(code) => {
                self.text = code.compact();
                self.cursor = self.text.len();
                self.error = None;
                self.wheel_active = false;
                Some(code)
            }
            Err(reason) => {
                self.error = Some(if self.text.is_empty() {
                    "Enter a shared code or decimal seed"
                } else if reason.contains("checksum") {
                    "Checksum mismatch; check the shared code"
                } else if reason.contains("nonzero") {
                    "Seed must be nonzero"
                } else if reason.contains("64") {
                    "Seed exceeds 64 bits"
                } else if reason.contains("character") {
                    "Invalid code character"
                } else {
                    "Use decimal seed or M1-XXXX-XXXX-XXXX-XXX"
                });
                None
            }
        }
    }

    fn cycle(&mut self, up: bool) {
        let Some((mut compact, cursor)) = self.wheel_draft() else {
            self.error = Some("Wheel needs an M1 payload or a valid complete code");
            return;
        };
        // wheel_draft supplies exactly 17 ASCII bytes and a caret in 2..=16.
        let Some(selected) = compact.as_bytes().get(cursor) else {
            return;
        };
        let Some(index) = ALPHABET.iter().position(|c| c == selected) else {
            return;
        };
        let next = if up {
            (index + 1) % 32
        } else {
            (index + 31) % 32
        };
        compact.replace_range(cursor..cursor + 1, &char::from(ALPHABET[next]).to_string());
        self.text = compact;
        self.cursor = cursor;
        self.wheel_active = true;
    }

    /// Prepare without changing self: malformed drafts must survive wheel refusal.
    fn wheel_draft(&self) -> Option<(String, usize)> {
        if self.text.is_empty() {
            return Some(("M1000000000000000".into(), PREFIX_LEN));
        }
        if let Ok(code) = MonCode::from_str(&self.text) {
            let cursor = if self.text.starts_with("M1") {
                self.text
                    .bytes()
                    .take(self.cursor)
                    .filter(|&c| c != b'-')
                    .count()
                    .clamp(PREFIX_LEN, COMPACT_LEN - 1)
            } else {
                PREFIX_LEN
            };
            return Some((code.compact(), cursor));
        }
        // A structurally sound compact draft may have an unfinished or bad checksum.
        // Only missing trailing payload characters are padded; no symbols are dropped.
        let payload = self.text.strip_prefix("M1")?;
        if self.text.len() > COMPACT_LEN {
            return None;
        }
        let mut compact = String::from("M1");
        for c in payload.bytes() {
            let c = match c {
                b'O' => b'0',
                b'I' | b'L' => b'1',
                _ => c,
            };
            if !ALPHABET.contains(&c) {
                return None;
            }
            compact.push(char::from(c));
        }
        while compact.len() < COMPACT_LEN {
            compact.push('0');
        }
        Some((compact, self.cursor.clamp(PREFIX_LEN, COMPACT_LEN - 1)))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // Fixed shared birth code for seed 0x0123456789abcdef; no live state is consulted.
    const SHARED: &str = "M1-028T-5CY4-TQKF-FVP";

    fn draft(text: &str) -> SeedEntry {
        let mut entry = SeedEntry::default();
        entry.paste(text);
        assert!(entry.error().is_none());
        entry
    }

    fn assert_invariants(entry: &SeedEntry) {
        assert!(entry.text().is_ascii());
        assert!(entry.text().len() <= MAX_INPUT);
        assert!(entry.cursor() <= entry.text().len());
        assert!(entry.text().is_char_boundary(entry.cursor()));
        if let Some(error) = entry.error() {
            assert!(error.is_ascii());
            assert!(error.len() <= 64);
        }
    }

    #[test]
    fn grouped_lowercase_and_decimal_round_trip_to_the_actual_code() {
        let expected = MonCode::new(0x0123456789abcdef).unwrap();
        assert_eq!(expected.to_string(), SHARED);
        let mut entry = draft(&format!(" \t{}\r\n", SHARED.to_ascii_lowercase()));
        assert_eq!(entry.text(), SHARED);
        assert_eq!(entry.validate(), Some(expected));
        assert_eq!(entry.text(), expected.compact());
        assert_eq!(entry.cursor(), COMPACT_LEN);
        assert_eq!(entry.error(), None);
        let mut decimal = draft(&expected.seed().to_string());
        assert_eq!(decimal.validate(), Some(expected));
        assert_eq!(decimal.text(), entry.text());
        assert_invariants(&entry);
    }

    #[test]
    fn insertion_deletion_and_caret_boundaries() {
        let mut entry = SeedEntry::default();
        for edit in [
            Edit::Left,
            Edit::Right,
            Edit::Home,
            Edit::End,
            Edit::Backspace,
            Edit::Delete,
        ] {
            entry.edit(edit);
            assert_eq!((entry.text(), entry.cursor()), ("", 0));
        }
        for c in ['a', 'c'] {
            entry.edit(Edit::Character(c));
        }
        entry.edit(Edit::Left);
        entry.edit(Edit::Character('b'));
        assert_eq!((entry.text(), entry.cursor()), ("ABC", 2));
        entry.edit(Edit::Backspace);
        assert_eq!((entry.text(), entry.cursor()), ("AC", 1));
        entry.edit(Edit::Delete);
        assert_eq!((entry.text(), entry.cursor()), ("A", 1));
        entry.edit(Edit::End);
        entry.edit(Edit::Right);
        entry.edit(Edit::Delete);
        assert_eq!((entry.text(), entry.cursor()), ("A", 1));
        entry.edit(Edit::Home);
        entry.edit(Edit::Backspace);
        entry.edit(Edit::Left);
        assert_eq!((entry.text(), entry.cursor()), ("A", 0));
        entry.edit(Edit::Delete);
        assert_eq!((entry.text(), entry.cursor()), ("", 0));
        entry.edit(Edit::Character('-'));
        entry.edit(Edit::Clear);
        assert_eq!((entry.text(), entry.cursor()), ("", 0));
        assert_invariants(&entry);
    }

    #[test]
    fn independent_controller_can_enter_a_supplied_shared_code() {
        let expected = MonCode::from_str(SHARED).unwrap();
        let supplied = expected.compact();
        let mut entry = SeedEntry::default();
        // First wheel action creates the blank draft and cycles its selected zero.
        entry.edit(Edit::Up);
        entry.edit(Edit::Down);
        assert_eq!((entry.text(), entry.cursor()), ("M1000000000000000", 2));
        assert_eq!(entry.validate(), None);
        entry.edit(Edit::Up);
        entry.edit(Edit::Down);
        entry.edit(Edit::Home);
        for target in supplied.bytes().skip(PREFIX_LEN) {
            let steps = ALPHABET.iter().position(|&c| c == target).unwrap();
            for _ in 0..steps {
                entry.edit(Edit::Up);
            }
            entry.edit(Edit::Right);
            assert_invariants(&entry);
        }
        assert_eq!(entry.text(), supplied);
        assert_eq!(entry.cursor(), 16);
        assert_eq!(entry.validate(), Some(expected));
    }

    #[test]
    fn controller_wraps_and_keeps_caret_in_payload() {
        let mut entry = SeedEntry::default();
        entry.edit(Edit::Down);
        assert_eq!(entry.text(), "M1Z00000000000000");
        assert_eq!(entry.cursor(), 2);
        entry.edit(Edit::Up);
        assert_eq!(entry.text(), "M1000000000000000");
        entry.edit(Edit::Home);
        for _ in 0..30 {
            entry.edit(Edit::Left);
        }
        assert_eq!(entry.cursor(), 2);
        entry.edit(Edit::End);
        for _ in 0..30 {
            entry.edit(Edit::Right);
        }
        assert_eq!(entry.cursor(), 16);
        entry.edit(Edit::Down);
        assert_eq!(entry.text(), "M100000000000000Z");
        entry.edit(Edit::Up);
        assert_eq!(entry.text(), "M1000000000000000");
        assert_invariants(&entry);
    }

    #[test]
    fn wheel_normalizes_valid_grouped_and_decimal_codes() {
        let expected = MonCode::from_str(SHARED).unwrap();
        let mut grouped = draft(SHARED);
        grouped.edit(Edit::Home);
        for _ in 0..8 {
            grouped.edit(Edit::Right);
        }
        // Grouped offset 8 is compact offset 6 (two preceding separators).
        grouped.edit(Edit::Up);
        assert_eq!(grouped.cursor(), 6);
        assert_eq!(grouped.text().len(), 17);
        grouped.edit(Edit::Down);
        assert_eq!(grouped.text(), expected.compact());
        let mut decimal = draft(&expected.seed().to_string());
        decimal.edit(Edit::Down);
        assert_eq!(decimal.cursor(), 2);
        decimal.edit(Edit::Up);
        assert_eq!(decimal.text(), expected.compact());
    }

    #[test]
    fn wheel_pads_only_trailing_compact_payload_and_preserves_bad_drafts() {
        let mut partial = draft("m102");
        partial.edit(Edit::Home);
        partial.edit(Edit::Down);
        assert_eq!(partial.text(), "M1Z20000000000000");
        assert_eq!(partial.cursor(), 2);
        let mut alias = draft("M1OIL");
        alias.edit(Edit::Home);
        alias.edit(Edit::Up);
        assert_eq!(alias.text(), "M1111000000000000");
        for text in [
            "M",
            "M2",
            "M1-U",
            "M1U",
            "M1-0000",
            "0",
            "18446744073709551616",
            "M1-028T-5CY4-TQKF-FVQ",
        ] {
            let mut entry = draft(text);
            entry.edit(Edit::Home);
            entry.edit(Edit::Right);
            let before = (entry.text().to_owned(), entry.cursor());
            for action in [Edit::Up, Edit::Down] {
                entry.edit(action);
                assert_eq!(
                    (entry.text(), entry.cursor()),
                    (before.0.as_str(), before.1)
                );
                assert!(entry.error().unwrap().contains("Wheel"));
                assert_invariants(&entry);
            }
        }
    }

    #[test]
    fn validation_rejects_corrupt_checksum_zero_overflow_and_version() {
        for text in [
            "",
            "0",
            "M1000000000000000",
            "18446744073709551616",
            "M1ZZZZZZZZZZZZZZZ",
            "M2-028T-5CY4-TQKF-FVP",
        ] {
            let mut entry = draft(text);
            let before = (entry.text().to_owned(), entry.cursor());
            assert_eq!(entry.validate(), None, "{text}");
            assert_eq!(
                (entry.text(), entry.cursor()),
                (before.0.as_str(), before.1)
            );
            assert!(entry.error().is_some());
            assert_invariants(&entry);
        }
        let compact = MonCode::from_str(SHARED).unwrap().compact();
        for index in PREFIX_LEN..COMPACT_LEN {
            for &c in ALPHABET {
                if compact.as_bytes()[index] == c {
                    continue;
                }
                let mut damaged = compact.clone();
                damaged.replace_range(index..index + 1, &char::from(c).to_string());
                let mut entry = draft(&damaged);
                assert!(entry.validate().is_none(), "{damaged}");
                assert_eq!(entry.text(), damaged);
                assert_invariants(&entry);
            }
        }
    }

    #[test]
    fn rejected_pastes_preserve_draft_and_caret_atomically() {
        let mut entry = draft(SHARED);
        entry.edit(Edit::Left);
        let before = (entry.text().to_owned(), entry.cursor());
        for text in [
            "A".repeat(1_000_000),
            "M1\n0000".into(),
            "M1\t0000".into(),
            "M1\u{1b}[2J".into(),
            "M1\0".into(),
            "M1\u{7f}".into(),
            "M1é".into(),
            "\u{2003}123".into(),
            "123 456".into(),
            "M1_000".into(),
        ] {
            entry.paste(&text);
            assert_eq!(
                (entry.text(), entry.cursor()),
                (before.0.as_str(), before.1)
            );
            assert!(entry.error().is_some());
            assert_invariants(&entry);
        }
        entry.paste("  m1-abc  ");
        assert_eq!((entry.text(), entry.cursor()), ("M1-ABC", 6));
        assert_eq!(entry.error(), None);
    }

    #[test]
    fn editing_clears_errors_and_typing_never_exceeds_the_protocol_bound() {
        let mut entry = SeedEntry::default();
        assert_eq!(entry.validate(), None);
        entry.edit(Edit::Left);
        assert_eq!(entry.error(), None);
        for _ in 0..1000 {
            entry.edit(Edit::Character('q'));
            assert_invariants(&entry);
        }
        assert_eq!(entry.text(), "Q".repeat(MAX_INPUT));
        assert_eq!(entry.cursor(), MAX_INPUT);
        for c in ['\n', '\0', '\u{1b}', '\u{7f}', 'é', '🦀', ' ', '_'] {
            let before = (entry.text().to_owned(), entry.cursor());
            entry.edit(Edit::Character(c));
            assert_eq!(
                (entry.text(), entry.cursor()),
                (before.0.as_str(), before.1)
            );
            assert_eq!(entry.error(), None);
        }
        entry.edit(Edit::Backspace);
        assert_eq!(entry.error(), None);
        entry.edit(Edit::Character('n'));
        assert!(entry.text().ends_with('N'));
        entry.edit(Edit::Clear);
        assert_eq!((entry.text(), entry.cursor(), entry.error()), ("", 0, None));
        let mut other = entry.clone();
        other.paste(SHARED);
        assert_eq!(other.validate().unwrap().seed(), 0x0123456789abcdef);
        assert_eq!((entry.text(), entry.cursor(), entry.error()), ("", 0, None));
    }
}
