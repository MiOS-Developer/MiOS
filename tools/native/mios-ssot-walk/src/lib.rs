// AI-hint: Reads the SSOT walk rules ([generation.walk]: excluded_sections, mostly_dead_sections, emit_keep) from the tree being projected, so every MIOS_* export projection and both resolver twins apply one copy of them.
// AI-related: usr/share/mios/mios.toml [generation.walk], usr/lib/mios/mios_toml.py (walk_rules), tools/native/mios-resolver/src/emit.rs, tests/test-resolver-dropin-parity.py
//
// The lists used to be compiled in here AND restated in mios_toml.py, with a
// test that parsed the Python source to keep the two copies equal. They live
// in the SSOT now and nowhere else; this crate only reads them. A tree with no
// [generation.walk] has no walk rules: every section is walked and nothing is
// suppressed, in both twins alike.
//
// Malformed shapes are read identically by mios_toml.walk_rules(): a value that
// is not a table or not an array contributes nothing, and a non-string member is
// skipped. tests/test-resolver-dropin-parity.py fuzzes those shapes across both.

use std::collections::BTreeSet;
use toml::Value;

/// The SSOT table, as a path from the document root.
pub const TABLE: [&str; 2] = ["generation", "walk"];

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct WalkRules {
    excluded_sections: BTreeSet<String>,
    mostly_dead_sections: BTreeSet<String>,
    emit_keep: BTreeSet<String>,
}

fn strings(table: Option<&toml::Table>, key: &str) -> BTreeSet<String> {
    table
        .and_then(|t| t.get(key))
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(Value::as_str)
                .map(str::to_string)
                .collect()
        })
        .unwrap_or_default()
}

impl WalkRules {
    /// The rules `data` declares under [generation.walk].
    pub fn from_ssot(data: &Value) -> Self {
        let table = data
            .get(TABLE[0])
            .and_then(Value::as_table)
            .and_then(|g| g.get(TABLE[1]))
            .and_then(Value::as_table);
        WalkRules {
            excluded_sections: strings(table, "excluded_sections"),
            mostly_dead_sections: strings(table, "mostly_dead_sections"),
            emit_keep: strings(table, "emit_keep"),
        }
    }

    /// A section a renderer or direct TOML reader owns: never exported.
    pub fn is_excluded_section(&self, section: &str) -> bool {
        self.excluded_sections.contains(section)
    }

    /// The canonical name of a key in `section` is withheld (its legacy
    /// aliases still emit): the section is mostly dead and the name is not kept.
    pub fn suppresses_canonical(&self, section: &str, canonical: &str) -> bool {
        self.mostly_dead_sections.contains(section) && !self.emit_keep.contains(canonical)
    }

    pub fn excluded_sections(&self) -> &BTreeSet<String> {
        &self.excluded_sections
    }

    pub fn mostly_dead_sections(&self) -> &BTreeSet<String> {
        &self.mostly_dead_sections
    }

    pub fn emit_keep(&self) -> &BTreeSet<String> {
        &self.emit_keep
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn rules(text: &str) -> WalkRules {
        WalkRules::from_ssot(&toml::from_str::<Value>(text).unwrap())
    }

    #[test]
    fn vendor_ssot_declares_the_walk() {
        let vendor: Value =
            toml::from_str(include_str!("../../../../usr/share/mios/mios.toml")).unwrap();
        let r = WalkRules::from_ssot(&vendor);
        for section in ["containers", "units", "generation", "ci", "tests"] {
            assert!(r.is_excluded_section(section), "{section}");
        }
        assert!(!r.is_excluded_section("ports"));
        assert!(r.suppresses_canonical("ai", "MIOS_AI_RANDOM_SETTING"));
        assert!(!r.suppresses_canonical("ai", "MIOS_AI_ENDPOINT"));
        assert!(!r.suppresses_canonical("ports", "MIOS_PORTS_HTTP"));
    }

    #[test]
    fn an_edit_to_the_table_is_the_only_change_needed() {
        let base = "[generation.walk]\nexcluded_sections=['a']\nmostly_dead_sections=['m']\nemit_keep=['MIOS_M_KEEP']\n";
        let r = rules(base);
        assert!(r.is_excluded_section("a") && !r.is_excluded_section("b"));
        assert!(r.suppresses_canonical("m", "MIOS_M_OTHER"));
        assert!(!r.suppresses_canonical("m", "MIOS_M_KEEP"));
        let edited = rules(&base.replace("['a']", "['b']").replace("'MIOS_M_KEEP'", "'MIOS_M_OTHER'"));
        assert!(!edited.is_excluded_section("a") && edited.is_excluded_section("b"));
        assert!(!edited.suppresses_canonical("m", "MIOS_M_OTHER"));
        assert!(edited.suppresses_canonical("m", "MIOS_M_KEEP"));
    }

    #[test]
    fn no_table_means_no_rules() {
        assert_eq!(rules("[ports]\nhttp = 80\n"), WalkRules::default());
        assert_eq!(rules("generation = 3\n"), WalkRules::default());
        assert_eq!(rules("[generation]\nwalk = 'x'\n"), WalkRules::default());
    }

    #[test]
    fn malformed_members_contribute_nothing() {
        let r = rules("[generation.walk]\nexcluded_sections = 'containers'\nmostly_dead_sections = ['ai', 3, true]\nemit_keep = [['MIOS_AI_X']]\n");
        assert!(r.excluded_sections().is_empty());
        assert_eq!(r.mostly_dead_sections().iter().collect::<Vec<_>>(), ["ai"]);
        assert!(r.emit_keep().is_empty());
    }
}
