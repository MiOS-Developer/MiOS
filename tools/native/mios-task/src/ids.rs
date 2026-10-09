// AI-hint: Post-purge task ids (ADR-0028) -- opaque task_<ULID> id, typed task_<number> form, legacy schema reader, and token scanner for tree rewrites and the old-id gate.
// AI-related: /usr/lib/mios/schemas/task-record.schema.json, /usr/share/mios/mios.toml [tasks.store], tools/native/mios-task/src/purge.rs, /usr/share/doc/mios/adr/0028-one-canonical-task-list.md
// AI-functions: ulid, ulid_ms, day_ms, Ids::from_schema, Ids::typed, Ids::opaque, Ids::ms_of, legacy_record_schema, Shapes::from_ids, Shapes::from_list, Shapes::matches, scan, Hit, tracked_files, is_text

use regex::Regex;
use serde_json::Value;
use std::path::Path;

/// Crockford base32, the ULID alphabet (no I, L, O, U).
const CROCKFORD: &[u8; 32] = b"0123456789ABCDEFGHJKMNPQRSTVWXYZ";

/// A ULID: 48 bits of milliseconds since the Unix epoch, then the first 80 bits of `rand`, as 26 characters.
pub fn ulid(ms: u64, rand: &[u8]) -> String {
    let mut v: u128 = ((ms as u128) & ((1u128 << 48) - 1)) << 80;
    let mut r: u128 = 0;
    for b in rand.iter().take(10) {
        r = (r << 8) | (*b as u128);
    }
    v |= r;
    let mut out = [b'0'; 26];
    for c in out.iter_mut().rev() {
        *c = CROCKFORD[(v & 31) as usize];
        v >>= 5;
    }
    String::from_utf8(out.to_vec()).expect("ascii")
}

/// The millisecond timestamp a ULID carries, or None when `s` is not one.
pub fn ulid_ms(s: &str) -> Option<u64> {
    if s.len() != 26 || s.as_bytes()[0] > b'7' {
        return None;
    }
    let mut v: u128 = 0;
    for c in s.bytes() {
        let d = CROCKFORD.iter().position(|x| *x == c)? as u128;
        v = (v << 5) | d;
    }
    Some((v >> 80) as u64)
}

/// Milliseconds at 00:00 UTC of a YYYY-MM-DD date (days-from-civil, no calendar crate).
pub fn day_ms(date: &str) -> Option<u64> {
    if !crate::record::is_iso_date(date) {
        return None;
    }
    let (y, m, d): (i64, i64, i64) = (
        date[0..4].parse().ok()?,
        date[5..7].parse().ok()?,
        date[8..10].parse().ok()?,
    );
    let y = if m <= 2 { y - 1 } else { y };
    let era = y.div_euclid(400);
    let yoe = y - era * 400;
    let mp = (m + 9) % 12;
    let doy = (153 * mp + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    let days = era * 146_097 + doe - 719_468;
    (days >= 0).then(|| days as u64 * 86_400_000)
}

/// The two spellings of a task id. The object word comes from the schema's `object` enum, its single home.
#[derive(Clone)]
pub struct Ids {
    pub object: String,
}

impl Ids {
    pub fn from_schema(record: &Value) -> Ids {
        let object = record
            .pointer("/properties/object/enum/0")
            .and_then(|x| x.as_str())
            .unwrap_or("task")
            .to_string();
        Ids { object }
    }

    /// The typed id people and every file outside the structured fields use: task_<number>.
    pub fn typed(&self, n: u64) -> String {
        format!("{}_{n}", self.object)
    }

    /// The opaque id: task_<ULID>.
    pub fn opaque(&self, ulid: &str) -> String {
        format!("{}_{ulid}", self.object)
    }

    pub fn ms_of(&self, s: &str) -> Option<u64> {
        s.strip_prefix(&self.object)
            .and_then(|x| x.strip_prefix('_'))
            .and_then(ulid_ms)
    }
}

/// The record schema a list not yet migrated by `mios-task migrate-ids` is read with: ids kept verbatim from
/// the retired lists, provenance.key and provenance.aliases, no object or number. Frozen here, because the
/// schema file describes the migrated shape.
pub const LEGACY_RECORD: &str = r##"{"type":"object","additionalProperties":false,"required":["id","type","title","status","owner","epic","goal","priority","size","workstream","domain","depends_on","related","acceptance_criteria","verification","verification_evidence","details","links","notes","extra","created","updated","provenance"],"properties":{"id":{"type":"string","pattern":"^[A-Z][A-Z0-9]*(?:[- ][A-Z0-9]+)*(?:\\.\\.(?:[A-Z][A-Z0-9]*-)?[0-9]+)?(?:#[0-9]+)?$","description":"Unique in the file; kept verbatim from the retired lists and never reminted. A '#n' suffix marks a second, different task that carried the same id in a retired list."},"type":{"type":"string","enum":["task","bug","epic"],"description":"The retired store type roadmap_item maps to task; the original type is kept in provenance.type_raw."},"title":{"type":"string"},"status":{"type":"string","enum":["pending","in_progress","completed","incomplete","cancelled"]},"owner":{"type":"string","description":"Lane claim such as claude-code, lane-b or Codex. An empty string means unclaimed. Never free-form 'Who' text."},"epic":{"type":"string","description":"The id of an epic record, or an empty string."},"goal":{"type":"string"},"priority":{"type":["string","null"],"enum":["P0","P1","P2","P3",null]},"size":{"type":["string","null"],"enum":["S","M","L","XL",null]},"workstream":{"type":["string","null"]},"domain":{"type":["string","null"]},"depends_on":{"type":"array","items":{"type":"string"},"description":"Blocking edges only. Every entry must resolve to an id in the file."},"related":{"type":"array","items":{"type":"object","additionalProperties":false,"required":["id","type"],"properties":{"id":{"type":"string"},"type":{"type":"string","enum":["related","converted_to","converted_from","references","archived"],"description":"archived: an edge the dev-loop toolkit took out of depends_on (archived_dependencies) because its target was archived; history, never a block."}}}},"acceptance_criteria":{"type":"array","items":{"type":"string"}},"verification":{"type":"object","additionalProperties":false,"required":["positive_cmd","negative_control_cmd","negative_expect","statements"],"properties":{"positive_cmd":{"type":["string","null"]},"negative_control_cmd":{"type":["string","null"]},"negative_expect":{"type":["string","null"]},"statements":{"type":"array","items":{"type":"string"}}}},"verification_evidence":{"type":"string","description":"Must be non-empty when status is completed."},"details":{"type":"object","additionalProperties":false,"required":["what_how","where","why","do_not"],"properties":{"what_how":{"type":["string","null"]},"where":{"type":["string","null"]},"why":{"type":["string","null"]},"do_not":{"type":["string","null"]}}},"links":{"type":"array","items":{"type":"string"}},"notes":{"type":"string"},"extra":{"type":"array","items":{"type":"object","additionalProperties":false,"required":["key","value"],"properties":{"key":{"type":"string"},"value":{"type":"string"}}}},"created":{"type":["string","null"],"pattern":"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"},"updated":{"type":["string","null"],"pattern":"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"},"provenance":{"type":["object","null"],"description":"null for a record created in tasks.jsonl; such a record must have non-empty acceptance_criteria plus verification.positive_cmd and verification.negative_control_cmd. A migrated record carries where it came from and every byte it owned in the retired lists.","additionalProperties":false,"required":["origin","key","aliases","type_raw","status_raw","owner_raw","title_raw","also_in","conflicts","classification","sources"],"properties":{"origin":{"type":"string","description":"For example MiOS:AGY-TASKS.md or MiOS:.devloop/tasks.jsonl"},"key":{"type":"string","description":"The record's key in the retired TASKS.jsonl, or its id in the lane file"},"aliases":{"type":"array","items":{"type":"string"},"description":"Former ids this record answered to; a '#n' id names the id it was renamed from. Frozen with key, id and the slice set by [tasks.store].migrated_sha256."},"type_raw":{"type":["string","null"]},"status_raw":{"type":["string","null"]},"owner_raw":{"type":["string","null"],"description":"The store's 'Who' text; never a lane claim"},"title_raw":{"type":["string","null"]},"also_in":{"type":"array","items":{"type":"string"}},"conflicts":{"type":"array","items":{"type":"object","additionalProperties":false,"required":["field","origin","value"],"properties":{"field":{"type":"string"},"origin":{"type":"string"},"value":{"type":"string"}}}},"classification":{"type":"string","enum":["mios-source","mios-classified","lane"]},"sources":{"type":"array","description":"The verbatim slices of the retired lists this record owned, in file order. Concatenated by offset, the slices of every record rebuild each file of mios.toml [tasks.store].frozen byte for byte (mios-task source).","items":{"type":"object","additionalProperties":false,"required":["file","kind","offset","length","sha256","after","text"],"properties":{"file":{"type":"string","description":"Repo-qualified retired list, e.g. MiOS:AGY-TASKS.md"},"kind":{"type":"string","enum":["section","table-row","roadmap-item","list-item","jsonl","folded","banner","passthrough"]},"offset":{"type":"integer","description":"Byte offset of the slice in its file"},"length":{"type":"integer","description":"Byte length of text (UTF-8)"},"sha256":{"type":"string","pattern":"^[0-9a-f]{64}$","description":"SHA-256 of text"},"after":{"type":"string","description":"The id of the record that owns the slice just before this one in the same file; empty for the first slice. Names a dropped record."},"text":{"type":"string"}}}}}}}}"##;

pub fn legacy_record_schema() -> Value {
    serde_json::from_str(LEGACY_RECORD).expect("the frozen legacy record schema is JSON")
}

/// The old id shapes: one regex over a whole candidate token. Each shape is a prefix and a digit count
/// (`T-[0-9]{3,4}`), or a literal id without trailing digits. Built from the ids of a not-yet-migrated list,
/// or read back from mios.toml [tasks.store].purged once the list is migrated.
pub struct Shapes {
    pub list: Vec<String>,
    rx: Option<Regex>,
}

impl Shapes {
    pub fn from_list(list: Vec<String>) -> Result<Shapes, String> {
        let rx = if list.is_empty() {
            None
        } else {
            Some(
                Regex::new(&format!("^(?:{})(?:#[0-9]+)?$", list.join("|")))
                    .map_err(|e| format!("[tasks.store].purged: {e}"))?,
            )
        };
        Ok(Shapes { list, rx })
    }

    /// The shapes of `ids`: per prefix, the narrowest digit-count range that covers every id with that prefix.
    /// A range id ("P-1..9") counts by its first number; '#n' is part of every shape; `skip` ids name no shape.
    pub fn from_ids<'a>(ids: impl Iterator<Item = &'a str>, skip: &dyn Fn(&str) -> bool) -> Shapes {
        use std::collections::BTreeMap;
        let mut widths: BTreeMap<String, (usize, usize)> = BTreeMap::new();
        let mut literal: Vec<String> = Vec::new();
        for id in ids {
            if skip(id) {
                continue;
            }
            let base = id.split('#').next().unwrap_or(id);
            let base = base.split("..").next().unwrap_or(base);
            let digits = base
                .bytes()
                .rev()
                .take_while(|c| c.is_ascii_digit())
                .count();
            let prefix = &base[..base.len() - digits];
            if digits == 0 || prefix.is_empty() {
                // A literal id with its hyphens escaped: the shape list in the SSOT must not spell the id it
                // forbids, or the gate would find it there.
                literal.push(esc(base).replace('-', "\\-"));
                continue;
            }
            let w = widths.entry(prefix.to_string()).or_insert((digits, digits));
            w.0 = w.0.min(digits);
            w.1 = w.1.max(digits);
        }
        let mut list: Vec<String> = widths
            .into_iter()
            .map(|(p, (a, b))| {
                if a == b {
                    format!("{}[0-9]{{{a}}}", esc(&p))
                } else {
                    format!("{}[0-9]{{{a},{b}}}", esc(&p))
                }
            })
            .collect();
        literal.sort();
        literal.dedup();
        list.extend(literal);
        // Longest first, so an alternation never stops at a shorter prefix of a longer shape.
        list.sort_by(|a, b| b.len().cmp(&a.len()).then(a.cmp(b)));
        Shapes::from_list(list).expect("shapes built from escaped literals compile")
    }

    pub fn matches(&self, tok: &str) -> bool {
        self.rx.as_ref().is_some_and(|r| r.is_match(tok))
    }
}

/// A literal for a regex: the metacharacters that matter outside a class, escaped; '-' and ' ' stay readable.
fn esc(s: &str) -> String {
    let mut o = String::with_capacity(s.len());
    for c in s.chars() {
        if "\\.+*?()|[]{}^$".contains(c) {
            o.push('\\');
        }
        o.push(c);
    }
    o
}

fn word(c: u8) -> bool {
    c.is_ascii_alphanumeric() || c == b'_'
}

/// The ends of every candidate token starting at `i`, longest first. A candidate follows the legacy id grammar:
/// `[A-Z][A-Z0-9]*`, then `[- ][A-Z0-9]+` segments, then an optional `..[P-]n` range, then an optional `#n`.
fn candidate_ends(b: &[u8], i: usize) -> Vec<usize> {
    let up = |c: u8| c.is_ascii_uppercase() || c.is_ascii_digit();
    let mut ends = Vec::new();
    let mut e = i + 1;
    while e < b.len() && up(b[e]) {
        e += 1;
    }
    ends.push(e);
    // Segments, at most 64 bytes in all: a token never runs across a sentence.
    loop {
        if e + 1 < b.len() && (b[e] == b'-' || b[e] == b' ') && up(b[e + 1]) && e - i < 64 {
            let mut f = e + 1;
            while f < b.len() && up(b[f]) {
                f += 1;
            }
            e = f;
            ends.push(e);
        } else {
            break;
        }
    }
    let base = ends.clone();
    let mut more = Vec::new();
    for &e0 in &base {
        let mut e = e0;
        if b[e.min(b.len())..].starts_with(b"..") {
            let mut f = e + 2;
            let mut g = f;
            while g < b.len() && (b[g].is_ascii_uppercase() || (g > f && b[g].is_ascii_digit())) {
                g += 1;
            }
            if g > f && g < b.len() && b[g] == b'-' {
                f = g + 1;
            }
            let mut h = f;
            while h < b.len() && b[h].is_ascii_digit() {
                h += 1;
            }
            if h > f {
                e = h;
                more.push(e);
            }
        }
        if e < b.len() && b[e] == b'#' {
            let mut h = e + 1;
            while h < b.len() && b[h].is_ascii_digit() {
                h += 1;
            }
            if h > e + 1 {
                more.push(h);
            }
        }
    }
    ends.extend(more);
    ends.sort_unstable();
    ends.dedup();
    ends.reverse();
    ends
}

/// A token boundary after `e`: not a word character, and not a '#n' that would continue the token.
fn ends_token(b: &[u8], e: usize) -> bool {
    match b.get(e) {
        None => true,
        Some(&c) if word(c) => false,
        Some(b'#') => !b.get(e + 1).is_some_and(|c| c.is_ascii_digit()),
        _ => true,
    }
}

/// One token the scanner settled: its byte span, its text, and what `decide` made of it.
pub struct Hit<T> {
    pub start: usize,
    pub end: usize,
    pub token: String,
    pub what: T,
}

/// Every token in `b` that `decide` accepts, scanning left to right; `decide` sees the candidates at one start
/// longest first and returns the first it accepts, so a range id wins over its first id and 'X#2' over 'X'.
/// A start needs a non-word byte (or nothing) before it, an end a token boundary after it.
pub fn scan<T>(b: &[u8], decide: &mut dyn FnMut(&str) -> Option<T>) -> Vec<Hit<T>> {
    let mut out = Vec::new();
    let mut i = 0usize;
    while i < b.len() {
        if !b[i].is_ascii_uppercase() || (i > 0 && word(b[i - 1])) {
            i += 1;
            continue;
        }
        let mut hit = None;
        for e in candidate_ends(b, i) {
            if !ends_token(b, e) {
                continue;
            }
            let tok = std::str::from_utf8(&b[i..e]).expect("ascii token");
            if let Some(t) = decide(tok) {
                hit = Some((e, tok.to_string(), t));
                break;
            }
        }
        match hit {
            Some((e, token, what)) => {
                out.push(Hit {
                    start: i,
                    end: e,
                    token,
                    what,
                });
                i = e;
            }
            None => i += 1,
        }
    }
    out
}

/// Tracked files under `root` (git ls-files), relative.
pub fn tracked_files(root: &Path) -> Result<Vec<String>, String> {
    let o = std::process::Command::new("git")
        .arg("-C")
        .arg(root)
        .args(["ls-files", "-z"])
        .output()
        .map_err(|e| format!("git ls-files under {}: {e}", root.display()))?;
    if !o.status.success() {
        return Err(format!(
            "git ls-files under {} failed: {}",
            root.display(),
            String::from_utf8_lossy(&o.stderr).trim()
        ));
    }
    o.stdout
        .split(|c| *c == 0)
        .filter(|s| !s.is_empty())
        .map(|s| {
            String::from_utf8(s.to_vec()).map_err(|e| {
                format!("git listed a non-UTF-8 migration path; refusing a lossy rewrite: {e}")
            })
        })
        .collect()
}

/// Text, as git decides it: no NUL byte in the first 8 KiB.
pub fn is_text(b: &[u8]) -> bool {
    !b[..b.len().min(8192)].contains(&0)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ulid_round_trips_its_timestamp_and_sorts_by_time() {
        let a = ulid(1_700_000_000_123, &[0xff; 10]);
        let b = ulid(1_700_000_000_124, &[0; 10]);
        assert_eq!(a.len(), 26);
        assert_eq!(ulid_ms(&a), Some(1_700_000_000_123));
        assert!(
            a < b,
            "a later millisecond sorts later whatever the random bits"
        );
        assert_eq!(ulid(0, &[0; 10]), "00000000000000000000000000");
        assert!(
            ulid_ms("80000000000000000000000000").is_none(),
            "over 128 bits"
        );
        assert!(
            ulid_ms("0000000000000000000000000I").is_none(),
            "not Crockford"
        );
        assert_eq!(day_ms("1969-12-31"), None);
        assert_eq!(day_ms("1970-01-02"), Some(86_400_000));
        assert_eq!(day_ms("2026-10-09").map(|m| m / 86_400_000), Some(20_735));
    }

    #[test]
    fn typed_and_opaque_ids() {
        let ids = Ids {
            object: "task".into(),
        };
        assert_eq!(ids.typed(12), "task_12");
        let o = ids.opaque(&ulid(5, &[1; 10]));
        assert_eq!(ids.ms_of(&o), Some(5));
        for bad in ["task_12", "tasks_00000000000000000000000000", "K-001"] {
            assert_eq!(ids.ms_of(bad), None, "{bad}");
        }
    }

    #[test]
    fn shapes_cover_their_prefixes_and_leave_look_alikes() {
        let ids = [
            "K-001",
            "K-1215",
            "K-031#2",
            "Q-001",
            "QM-01",
            "KAG-9",
            "KAG-1692",
            "KAG-40..44",
            "QG-TASK 1",
            "QREPO-FIX",
            "WS-GUP",
        ];
        let s = Shapes::from_ids(ids.iter().copied(), &|i| i.starts_with("WS-"));
        for hit in [
            "K-002",
            "K-1104#2",
            "Q-035",
            "QM-16",
            "KAG-1",
            "KAG-12345"[..8].as_ref(),
            "QG-TASK 4",
            "QREPO-FIX",
        ] {
            assert!(s.matches(hit), "{hit}");
        }
        for miss in [
            "Q-35",
            "QM-2",
            "UTF-8",
            "K-12345",
            "CVE-2024-1234",
            "WS-GUP",
            "SHA-256",
            "K-31",
        ] {
            assert!(!s.matches(miss), "{miss}");
        }
        // The list itself never spells an id it forbids: a literal id is kept with its hyphens escaped.
        assert!(s.list.contains(&"QREPO\\-FIX".to_string()), "{:?}", s.list);
        assert!(!s.list.iter().any(|x| x.contains("QREPO-FIX")));
    }

    #[test]
    fn the_scanner_takes_the_longest_candidate_at_a_word_start() {
        let known = [
            "K-1104",
            "K-1104#2",
            "KAG-503..KAG-510",
            "KAG-503",
            "QG-TASK 1",
            "KAG-961",
            "KAG-1560",
        ];
        let text = "see K-1104#2, (K-1104) KAG-503..KAG-510 and QG-TASK 1; xK-1104 K-1104x K-1104#3 KAG-961..1560 pre-K-1104";
        let hits = scan(text.as_bytes(), &mut |t| known.contains(&t).then_some(()));
        let got: Vec<&str> = hits.iter().map(|h| h.token.as_str()).collect();
        assert_eq!(
            got,
            [
                "K-1104#2",
                "K-1104",
                "KAG-503..KAG-510",
                "QG-TASK 1",
                "KAG-961",
                "K-1104"
            ]
        );
    }
}
