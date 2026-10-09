// AI-hint: Integration tests for mios-gate's drift-stubs check -- a Check that never reads the tree must not claim a verdict.
// AI-related: src/mios-rs/mios-gate/src/stubs.rs, src/mios-rs/miosd/src/drift/, usr/share/mios/mios.toml

use std::fs;
use std::path::Path;
use std::process::Command;

fn bin() -> &'static str {
    env!("CARGO_BIN_EXE_mios-gate")
}

/// `impls` is (struct, id, body-of-run).
fn tree(dir: &Path, impls: &[(&str, &str, &str)], listed: &[&str], ceiling: i64) {
    fs::create_dir_all(dir.join("src/mios-rs/miosd/src/drift")).unwrap();
    fs::create_dir_all(dir.join("usr/share/mios")).unwrap();
    let mut src = String::new();
    for (s, id, body) in impls {
        src.push_str(&format!(
            "impl Check for {s} {{\n    fn id(&self) -> &str {{\n        \"{id}\"\n    }}\n    {body}\n}}\n\n"
        ));
    }
    fs::write(dir.join("src/mios-rs/miosd/src/drift/probe.rs"), src).unwrap();
    let rows: String = listed.iter().map(|r| format!("  \"{r}\",\n")).collect();
    fs::write(
        dir.join("usr/share/mios/mios.toml"),
        format!("[drift.unimplemented]\nmax_unimplemented = {ceiling}\nchecks = [\n{rows}]\n"),
    )
    .unwrap();
}

fn run(dir: &Path) -> (i32, String) {
    let out = Command::new(bin())
        .args(["drift-stubs", "--root"])
        .arg(dir)
        .output()
        .unwrap();
    let mut t = String::from_utf8_lossy(&out.stdout).to_string();
    t.push_str(&String::from_utf8_lossy(&out.stderr));
    (out.status.code().unwrap_or(-1), t)
}

const STUB: &str =
    "fn run(&self, _ctx: &DriftCtx) -> Verdict {\n        Verdict::Skip(\"NOT IMPLEMENTED: probe\".to_string())\n    }";
const LIAR: &str =
    "fn run(&self, _ctx: &DriftCtx) -> Verdict {\n        Verdict::Pass(\"probe verified\".to_string())\n    }";
/// A real check OPENS something. task_982: `ctx.root.is_dir()` and
/// `ctx.root.join(..).exists()` are not reads, and four checks claimed a verdict
/// after doing only that.
const REAL: &str =
    "fn run(&self, ctx: &DriftCtx) -> Verdict {\n        let _ = fs::read_to_string(ctx.root.join(\"x\"));\n        Verdict::Pass(\"probe verified\".to_string())\n    }";
/// Delegation is the other legitimate shape: everything in projections.rs hands
/// ctx to a helper that does the reading.
const DELEGATES: &str =
    "fn run(&self, ctx: &DriftCtx) -> Verdict {\n        super::regen::regen_and_diff(ctx, \"g.py\", &[\"t\"], &[\"--check\"])\n    }";

#[test]
fn a_registered_stub_and_a_real_check_are_clean() {
    let d = tempfile::tempdir().unwrap();
    tree(
        d.path(),
        &[("A", "check_a", STUB), ("B", "check_b", REAL)],
        &["check_a"],
        1,
    );
    let (code, out) = run(d.path());
    assert_eq!(code, 0, "{out}");
}

/// The defect: 54 impls took `_ctx`, never read the tree, and returned a
/// constant Pass whose message claimed verification.
#[test]
fn a_check_that_cannot_look_must_not_claim() {
    let d = tempfile::tempdir().unwrap();
    tree(d.path(), &[("A", "check_a", LIAR)], &[], 0);
    let (code, out) = run(d.path());
    assert_eq!(code, 1, "{out}");
    assert!(out.contains("must not claim"), "{out}");
}

/// task_980, and a bug in this gate's first predicate: naming the parameter `ctx`
/// instead of `_ctx` proves nothing. check_pipeline_numbering read ctx.in_image
/// for an early skip and then returned a constant Pass, so a parameter-name test
/// classified it as implemented and it kept claiming a verdict.
#[test]
fn a_blind_check_named_ctx_is_still_blind() {
    const SNEAKY: &str = "fn run(&self, ctx: &DriftCtx) -> Verdict {\n        if ctx.in_image {\n            return Verdict::Skip(\"in image\".to_string());\n        }\n        Verdict::Pass(\"probe verified\".to_string())\n    }";
    let d = tempfile::tempdir().unwrap();
    tree(d.path(), &[("A", "check_a", SNEAKY)], &[], 0);
    let (code, out) = run(d.path());
    assert_eq!(code, 1, "{out}");
    assert!(out.contains("never consults ctx.root"), "{out}");
}

/// rustfmt splits `ctx\n    .root`, so a literal "ctx.root" test misses a real
/// reader. My first audit of this suite reported a false positive for exactly
/// that reason.
#[test]
fn a_reader_whose_ctx_root_is_line_split_is_not_a_stub() {
    const SPLIT: &str = "fn run(&self, ctx: &DriftCtx) -> Verdict {\n        let t = fs::read_to_string(ctx\n            .root\n            .join(\"x\"));\n        let _ = t;\n        Verdict::Pass(\"probe verified\".to_string())\n    }";
    let d = tempfile::tempdir().unwrap();
    tree(d.path(), &[("A", "check_a", SPLIT)], &[], 0);
    let (code, out) = run(d.path());
    assert_eq!(
        code, 0,
        "a line-split ctx.root must read as implemented: {out}"
    );
}

/// task_982: the shape four real checks had -- build a path, stat it, claim.
#[test]
fn a_stat_only_check_is_a_stub() {
    const STAT_ONLY: &str = "fn run(&self, ctx: &DriftCtx) -> Verdict {\n        if !ctx.root.join(\"x\").exists() {\n            return Verdict::Fail(\"missing\".to_string());\n        }\n        Verdict::Pass(\"probe verified\".to_string())\n    }";
    let d = tempfile::tempdir().unwrap();
    tree(d.path(), &[("A", "check_a", STAT_ONLY)], &[], 0);
    let (code, out) = run(d.path());
    assert_eq!(code, 1, "{out}");
    assert!(out.contains("never consults ctx.root"), "{out}");
}

/// Delegating ctx to a helper must NOT read as blind: eleven checks do this.
#[test]
fn a_delegating_check_is_not_a_stub() {
    let d = tempfile::tempdir().unwrap();
    tree(d.path(), &[("A", "check_a", DELEGATES)], &[], 0);
    let (code, out) = run(d.path());
    assert_eq!(code, 0, "{out}");
}

/// Handing the root itself to a native generator's --check is delegation too
/// (check_pod_quadlets, check_egress_firewall); the stat-only shape above stays a stub.
#[test]
fn a_check_delegating_the_root_is_not_a_stub() {
    const ROOT_ARG: &str = "fn run(&self, ctx: &DriftCtx) -> Verdict {\n        match crate::native_generator::command(&ctx.root, \"pod-quadlets\", true)\n            .and_then(|mut c| c.output().map_err(|e| e.to_string()))\n        {\n            Ok(o) if o.status.success() => Verdict::Pass(\"matches\".into()),\n            _ => Verdict::Fail(\"drift\".into()),\n        }\n    }";
    let d = tempfile::tempdir().unwrap();
    tree(d.path(), &[("A", "check_a", ROOT_ARG)], &[], 0);
    let (code, out) = run(d.path());
    assert_eq!(code, 0, "{out}");
}

#[test]
fn an_unregistered_stub_fails() {
    let d = tempfile::tempdir().unwrap();
    tree(d.path(), &[("A", "check_a", STUB)], &[], 0);
    let (code, out) = run(d.path());
    assert_eq!(code, 1, "{out}");
    assert!(out.contains("not on [drift.unimplemented]"), "{out}");
}

#[test]
fn a_raised_ceiling_does_not_absorb_an_unregistered_stub() {
    let d = tempfile::tempdir().unwrap();
    tree(d.path(), &[("A", "check_a", STUB)], &[], 999);
    let (code, out) = run(d.path());
    assert_eq!(code, 1, "{out}");
    assert!(out.contains("not on [drift.unimplemented]"), "{out}");
}

#[test]
fn a_ceiling_above_the_measurement_is_itself_a_finding() {
    let d = tempfile::tempdir().unwrap();
    tree(d.path(), &[("A", "check_a", REAL)], &[], 3);
    let (code, out) = run(d.path());
    assert_eq!(code, 1, "{out}");
    assert!(out.contains("lower the ceiling"), "{out}");
}

#[test]
fn a_stale_register_entry_fails() {
    let d = tempfile::tempdir().unwrap();
    tree(
        d.path(),
        &[("A", "check_a", STUB)],
        &["check_a", "check_gone"],
        2,
    );
    let (code, out) = run(d.path());
    assert_eq!(code, 1, "{out}");
    assert!(out.contains("check_gone"), "{out}");
}

/// Empty-Set Pass guards: nothing to scan, and no register, must each be
/// could-not-run rather than clean.
#[test]
fn an_empty_scan_or_absent_register_cannot_run() {
    let d = tempfile::tempdir().unwrap();
    tree(d.path(), &[], &[], 0);
    let (code, out) = run(d.path());
    assert_eq!(code, 2, "{out}");
    assert!(out.contains("nothing was compared"), "{out}");

    let d2 = tempfile::tempdir().unwrap();
    tree(d2.path(), &[("A", "check_a", STUB)], &["check_a"], 1);
    fs::write(
        d2.path().join("usr/share/mios/mios.toml"),
        "[other]\nk = 1\n",
    )
    .unwrap();
    let (code2, out2) = run(d2.path());
    assert_eq!(code2, 2, "{out2}");
}

#[test]
fn a_missing_root_cannot_run() {
    let d = tempfile::tempdir().unwrap();
    let (code, _) = run(d.path());
    assert_eq!(code, 2);
}
