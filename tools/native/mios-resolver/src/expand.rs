// AI-hint: The one brace-counting expander for ${VAR} and ${VAR:-default}; map-only, leaves systemd's $$ escape alone.
// AI-related: tools/native/mios-resolver/src/emit.rs, tools/native/mios-render-quadlets/src/main.rs

use std::collections::BTreeMap;

/// How many times a resolved value may itself be expanded before we call it a
/// cycle. `MIOS_AI_ENDPOINT` -> `http://localhost:${MIOS_PORTS_AGENT_PIPE}/v1`
/// is one level; nothing legitimate in the tree needs many.
pub const MAX_DEPTH: usize = 16;

#[derive(Debug, PartialEq, Eq)]
pub struct Expansion {
    pub text: String,
    /// Names that appeared but resolved to nothing and carried no default.
    pub unresolved: Vec<String>,
}

/// Map-only by design. Reading the process environment in here would let a
/// stray export in the builder shell silently change an emitted value, and
/// nothing would record it. Callers that want an environment layer compose it
/// into the map first, where it is visible and testable.
fn lookup(name: &str, ssot: &BTreeMap<String, String>) -> Option<String> {
    match ssot.get(name) {
        Some(v) if !v.is_empty() => Some(v.clone()),
        _ => None,
    }
}

/// Find the index of the `}` closing the `${` that starts at `open`, counting
/// nested `${` ... `}` pairs. A regex cannot do this: `[^}]*` stops at the
/// first brace, which is the whole task_977 defect.
fn matching_brace(bytes: &[u8], open: usize) -> Option<usize> {
    let mut depth = 0usize;
    let mut i = open;
    while i < bytes.len() {
        if bytes[i] == b'$' && i + 1 < bytes.len() && bytes[i + 1] == b'{' {
            depth += 1;
            i += 2;
            continue;
        }
        if bytes[i] == b'}' {
            depth -= 1;
            if depth == 0 {
                return Some(i);
            }
        }
        i += 1;
    }
    None
}

/// Split `NAME:-default` at the FIRST `:-` that is not inside a nested `${}`.
fn split_default(inner: &str) -> (&str, Option<&str>) {
    let b = inner.as_bytes();
    let mut depth = 0usize;
    let mut i = 0usize;
    while i < b.len() {
        if b[i] == b'$' && i + 1 < b.len() && b[i + 1] == b'{' {
            depth += 1;
            i += 2;
            continue;
        }
        if b[i] == b'}' {
            depth = depth.saturating_sub(1);
            i += 1;
            continue;
        }
        if depth == 0 && b[i] == b':' && i + 1 < b.len() && b[i + 1] == b'-' {
            return (&inner[..i], Some(&inner[i + 2..]));
        }
        i += 1;
    }
    (inner, None)
}

/// Only `MIOS_*` is ours. `${WORKER_MODEL}` in mios-llm-worker@.container comes
/// from `EnvironmentFile=-/run/mios/swarm/%i.env` at runtime; expanding it would
/// bake a per-instance value into a template. The bash renderer never touched a
/// non-MIOS name either, because every entry on its allowlist was MIOS_*.
fn is_name(s: &str) -> bool {
    s.starts_with("MIOS_") && s.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'_')
}

/// The `MIOS_*` name a value consists of, when the WHOLE value is one
/// `${NAME}` or `${NAME:-default}` -- a reference that carries NAME's value
/// whenever NAME is set. Anything around or after it makes it a composition.
pub fn sole_reference(text: &str) -> Option<&str> {
    if !text.starts_with("${") || matching_brace(text.as_bytes(), 0)? != text.len() - 1 {
        return None;
    }
    let (name, _) = split_default(&text[2..text.len() - 1]);
    is_name(name).then_some(name)
}

/// What a pass is allowed to touch.
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Mode {
    /// Every placeholder.
    All,
    /// ONLY the `${NAME:-default}` form. systemd cannot expand that form -- see
    /// `replace_env_full(.., flags=0)` in systemd's own env-util.c, which treats
    /// `:` as unsupported syntax and does no replacement -- so it must be baked
    /// even on a line whose bare refs the unit owns at runtime.
    DefaultsOnly,
}

/// Expand every `${...}`; `$$` is systemd's escape and passes through (task_977).
pub fn expand(text: &str, ssot: &BTreeMap<String, String>) -> Expansion {
    expand_mode(text, ssot, Mode::All)
}

pub fn expand_mode(text: &str, ssot: &BTreeMap<String, String>, mode: Mode) -> Expansion {
    let mut unresolved = Vec::new();
    let out = expand_inner(text, ssot, 0, &mut unresolved, mode);
    unresolved.sort();
    unresolved.dedup();
    Expansion {
        text: out,
        unresolved,
    }
}

fn expand_inner(
    text: &str,
    ssot: &BTreeMap<String, String>,
    depth: usize,
    unresolved: &mut Vec<String>,
    mode: Mode,
) -> String {
    if depth > MAX_DEPTH {
        return text.to_string();
    }
    let b = text.as_bytes();
    let mut out = String::with_capacity(text.len());
    let mut i = 0usize;
    while i < b.len() {
        if b[i] == b'$' && i + 1 < b.len() && b[i + 1] == b'$' {
            // systemd's escaped dollar: pass both bytes through untouched.
            out.push_str("$$");
            i += 2;
            continue;
        }
        if b[i] == b'$' && i + 1 < b.len() && b[i + 1] == b'{' {
            if let Some(close) = matching_brace(b, i) {
                let inner = &text[i + 2..close];
                let (name, default) = split_default(inner);
                // In DefaultsOnly a bare reference belongs to the unit at
                // runtime: leave it, and do not report it.
                if is_name(name) && !(mode == Mode::DefaultsOnly && default.is_none()) {
                    let replacement = match lookup(name, ssot) {
                        Some(v) => expand_inner(&v, ssot, depth + 1, unresolved, Mode::All),
                        None => match default {
                            Some(d) => expand_inner(d, ssot, depth + 1, unresolved, Mode::All),
                            None => {
                                unresolved.push(name.to_string());
                                text[i..=close].to_string()
                            }
                        },
                    };
                    out.push_str(&replacement);
                    i = close + 1;
                    continue;
                }
            }
        }
        // Not a placeholder we own: copy the byte.
        let ch_len = utf8_len(b[i]);
        out.push_str(&text[i..i + ch_len]);
        i += ch_len;
    }
    out
}

fn utf8_len(first: u8) -> usize {
    match first {
        0x00..=0x7F => 1,
        0xC0..=0xDF => 2,
        0xE0..=0xEF => 3,
        _ => 4,
    }
}

#[cfg(test)]
// Fixture setup panics on failure by design; the crate bans that in production paths.
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;

    fn ssot(pairs: &[(&str, &str)]) -> BTreeMap<String, String> {
        pairs
            .iter()
            .map(|(k, v)| (k.to_string(), v.to_string()))
            .collect()
    }

    const KB: &str =
        "base_url = \"${MIOS_AI_ENDPOINT:-http://localhost:${MIOS_PORTS_AGENT_PIPE:-8700}/v1}\"";

    #[test]
    fn sole_reference_is_only_a_whole_value_reference() {
        assert_eq!(sole_reference("${MIOS_A}"), Some("MIOS_A"));
        assert_eq!(sole_reference("${MIOS_A:-x:${MIOS_B}}"), Some("MIOS_A"));
        // A composition, a trailing literal, two references or a foreign name
        // is not one declaration's value.
        assert_eq!(sole_reference("http://${MIOS_A}/v1"), None);
        assert_eq!(sole_reference("${MIOS_A}x"), None);
        assert_eq!(sole_reference("${MIOS_A}${MIOS_B}"), None);
        assert_eq!(sole_reference("${HOME}"), None);
        assert_eq!(sole_reference("${MIOS_A"), None);
    }

    /// The bash renderer produced FOUR different answers for this one line
    /// depending only on which variables were exported, one of which was
    /// accidentally correct. A single-permutation fixture is what made the
    /// defect look intermittent, so all four are pinned here.
    #[test]
    fn the_nested_default_renders_correctly_in_every_permutation() {
        let want = "base_url = \"http://localhost:8700/v1\"";
        assert_eq!(want, expand(KB, &ssot(&[])).text, "neither set");
        assert_eq!(
            want,
            expand(KB, &ssot(&[("MIOS_PORTS_AGENT_PIPE", "8700")])).text,
            "inner set -- bash deleted /v1 here"
        );
        assert_eq!(
            "base_url = \"http://localhost:9999/v1\"",
            expand(KB, &ssot(&[("MIOS_PORTS_AGENT_PIPE", "9999")])).text,
            "inner set to a non-default"
        );
        assert_eq!(
            "base_url = \"http://example/v1\"",
            expand(
                KB,
                &ssot(&[
                    ("MIOS_AI_ENDPOINT", "http://example/v1"),
                    ("MIOS_PORTS_AGENT_PIPE", "8700"),
                ])
            )
            .text,
            "both set -- bash produced a doubled path suffix here"
        );
    }

    /// The live defect: envsubst turned $$MIOS_PORTS_PGVECTOR into $8432, which
    /// /bin/sh then evaluates to 432 because $8 is an unset positional.
    #[test]
    fn systemd_escaped_dollars_are_never_touched() {
        let s = ssot(&[
            ("MIOS_PORTS_PGVECTOR", "8432"),
            ("MIOS_PGVECTOR_BACKUP_DIR", "/var/lib/mios/backups"),
        ]);
        let line = "PORT=\"$$MIOS_PORTS_PGVECTOR\"; DIR=\"$$MIOS_PGVECTOR_BACKUP_DIR\"";
        assert_eq!(line, expand(line, &s).text);
        // The braced escape survives too, as a literal ${...}.
        assert_eq!(
            "$${MIOS_PORTS_PGVECTOR}",
            expand("$${MIOS_PORTS_PGVECTOR}", &s).text
        );
    }

    /// A single $ before a name is NOT a placeholder for us. envsubst treats it
    /// as one, which is the other half of the $$ damage.
    #[test]
    fn a_bare_dollar_name_is_not_a_placeholder() {
        let s = ssot(&[("MIOS_FRONTIER_ORCH_MODEL", "x")]);
        assert_eq!(
            "$MIOS_FRONTIER_ORCH_MODEL",
            expand("$MIOS_FRONTIER_ORCH_MODEL", &s).text
        );
    }

    #[test]
    fn a_plain_reference_resolves_from_ssot() {
        let s = ssot(&[("MIOS_VERSIONS_CEPH", "v19")]);
        let e = expand("Image=quay.io/ceph/ceph:${MIOS_VERSIONS_CEPH}", &s);
        assert_eq!("Image=quay.io/ceph/ceph:v19", e.text);
        assert!(e.unresolved.is_empty());
    }

    /// Absent is NOT empty. envsubst renders an unset listed variable as the
    /// empty string and exits 0, which is how a wrong value ships silently.
    /// We keep the literal and NAME it so a gate can fail on it.
    #[test]
    fn an_unresolvable_name_is_kept_and_reported_not_blanked() {
        let e = expand("Image=x:${MIOS_VERSIONS_CEPH}", &ssot(&[]));
        assert_eq!("Image=x:${MIOS_VERSIONS_CEPH}", e.text);
        assert_eq!(vec!["MIOS_VERSIONS_CEPH".to_string()], e.unresolved);
    }

    /// A value that itself carries a reference must expand. This is exactly why
    /// install.env drops MIOS_AI_ENDPOINT: the resolver emitted it unexpanded,
    /// so emit() saw a `$` and skipped the variable entirely (task_997).
    #[test]
    fn a_resolved_value_that_carries_a_reference_is_expanded_too() {
        let s = ssot(&[
            (
                "MIOS_AI_ENDPOINT",
                "http://localhost:${MIOS_PORTS_AGENT_PIPE}/v1",
            ),
            ("MIOS_PORTS_AGENT_PIPE", "8700"),
        ]);
        let e = expand("url=${MIOS_AI_ENDPOINT}", &s);
        assert_eq!("url=http://localhost:8700/v1", e.text);
        assert!(e.unresolved.is_empty());
        assert!(!e.text.contains('$'), "no residual placeholder: {}", e.text);
    }

    /// A cycle must terminate rather than blow the stack.
    #[test]
    fn a_reference_cycle_terminates() {
        let s = ssot(&[("MIOS_A", "${MIOS_B}"), ("MIOS_B", "${MIOS_A}")]);
        let e = expand("x=${MIOS_A}", &s);
        assert!(e.text.starts_with("x="), "{}", e.text);
    }

    /// Deliberately NOT a real port name: tools/render-ports.py owns every
    /// `${MIOS_PORT_*:-<literal>}` in the tree and rewrote an earlier version of
    /// this fixture to the canonical value, leaving the assertion behind.
    #[test]
    fn an_empty_ssot_value_falls_through_to_the_default() {
        let s = ssot(&[("MIOS_FIXTURE_ONLY", "")]);
        assert_eq!(
            "p=fallback",
            expand("p=${MIOS_FIXTURE_ONLY:-fallback}", &s).text
        );
    }

    #[test]
    fn a_default_may_itself_be_empty() {
        assert_eq!("p=", expand("p=${MIOS_NOT_SET:-}", &ssot(&[])).text);
    }

    #[test]
    fn an_unclosed_brace_is_left_alone_rather_than_eating_the_file() {
        let s = ssot(&[("MIOS_X", "1")]);
        assert_eq!("a ${MIOS_X b", expand("a ${MIOS_X b", &s).text);
    }

    #[test]
    fn non_ascii_content_survives_byte_for_byte() {
        let s = ssot(&[("MIOS_X", "1")]);
        assert_eq!("héllo ✓ 1", expand("héllo ✓ ${MIOS_X}", &s).text);
    }

    /// mios-llm-worker@.container floats ${WORKER_MODEL} and ${WORKER_PORT},
    /// supplied at runtime by EnvironmentFile=-/run/mios/swarm/%i.env. Baking
    /// them would freeze a per-instance value into a template unit.
    #[test]
    fn a_non_mios_name_is_never_ours_to_expand() {
        let s = ssot(&[("WORKER_MODEL", "x.gguf"), ("WORKER_PORT", "9001")]);
        let e = expand(
            "Exec=--model /models/${WORKER_MODEL} --port ${WORKER_PORT}",
            &s,
        );
        assert_eq!(
            "Exec=--model /models/${WORKER_MODEL} --port ${WORKER_PORT}",
            e.text
        );
        assert!(
            e.unresolved.is_empty(),
            "and not reported either: {:?}",
            e.unresolved
        );
    }

    #[test]
    fn a_non_name_inside_braces_is_not_treated_as_a_variable() {
        let s = ssot(&[("MIOS_X", "1")]);
        assert_eq!("${9BAD}", expand("${9BAD}", &s).text);
    }
}
