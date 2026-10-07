// AI-hint: Names registry drift check using the native generate-names-registry binary.
// AI-related: tools/native/generate-names-registry, usr/share/mios/names.generated.txt, usr/share/mios/referenced_names.txt

use super::{Check, DriftCtx, Verdict};

pub struct NamesRegistryCheck;
impl Check for NamesRegistryCheck {
    fn id(&self) -> &'static str {
        "check_names_registry"
    }
    fn describe(&self) -> &'static str {
        "Assert generated names registry matches SSOT referenced names"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        if !ctx.git_ok && ctx.incomplete_tree {
            return Verdict::Skip("Incomplete git work tree".to_string());
        }

        // T-1045. This used to test whether a DEBUG BUILD of the generator
        // existed and, if so, return Pass("Names registry projection matches
        // SSOT") -- without running it and without comparing anything. If the
        // binary was absent it skipped instead, so on an ordinary tree the
        // check was silent and on a developer's tree it lied. Both halves of
        // Skip-as-Pass in one function.
        //
        // T-1044/T-1009 lineage: the generator was ported to the native
        // tools/native/generate-names-registry binary and the Python script
        // strangler-deleted (AGY-1073); this caller kept the stale .py path
        // and the python3 interpreter assumption, so Windows-invoked image
        // builds failed with "Generator not found: tools/generate-names-registry.py".
        // The native generator writes BOTH projections (names.generated.txt
        // and referenced_names.txt), so both are snapshotted and restored --
        // comparing one while leaving the other rewritten was half a verdict.
        super::regen::regen_and_compare_native(
            ctx,
            "generate-names-registry",
            &[
                "usr/share/mios/names.generated.txt",
                "usr/share/mios/referenced_names.txt",
            ],
        )
    }
}

#[cfg(test)]
mod tests {
    // Test code: unwrap() is the intended failure mode -- a panic here IS the
    // assertion.
    #![allow(clippy::unwrap_used, clippy::expect_used, clippy::panic)]

    use super::super::regen::{regen_and_compare_native, resolve_native_generator};
    use super::super::{DriftCtx, Verdict};
    use std::fs;
    use std::path::PathBuf;
    use tempfile::TempDir;

    fn ctx_at(root: &std::path::Path) -> DriftCtx {
        DriftCtx {
            root: root.to_path_buf(),
            soft: false,
            in_image: false,
            git_ok: true,
            incomplete_tree: false,
        }
    }

    #[test]
    fn resolve_finds_release_exe_first() {
        let dir = TempDir::new().unwrap();
        let root = dir.path().to_path_buf();
        let rel = root.join("tools/native/target/release/generate-names-registry.exe");
        let dbg = root.join("tools/native/target/debug/generate-names-registry.exe");
        fs::create_dir_all(rel.parent().unwrap()).unwrap();
        fs::create_dir_all(dbg.parent().unwrap()).unwrap();
        fs::write(&rel, b"").unwrap();
        fs::write(&dbg, b"").unwrap();
        let got = resolve_native_generator(&ctx_at(&root), "generate-names-registry");
        assert_eq!(got, Some(rel), "release .exe must win over debug");
    }

    #[test]
    fn resolve_missing_binary_is_none() {
        let dir = TempDir::new().unwrap();
        assert!(resolve_native_generator(&ctx_at(dir.path()), "generate-names-registry").is_none());
    }

    #[test]
    fn unbuilt_generator_fails_with_build_hint() {
        // The exact class that broke image builds: the generator is absent,
        // and the verdict must name the build command, never Skip or Pass.
        let dir = TempDir::new().unwrap();
        let root = dir.path().to_path_buf();
        fs::create_dir_all(root.join("usr/share/mios")).unwrap();
        fs::write(
            root.join("usr/share/mios/names.generated.txt"),
            "x  MIOS_X\n",
        )
        .unwrap();
        fs::write(root.join("usr/share/mios/referenced_names.txt"), "MIOS_X\n").unwrap();
        match regen_and_compare_native(
            &ctx_at(&root),
            "generate-names-registry",
            &[
                "usr/share/mios/names.generated.txt",
                "usr/share/mios/referenced_names.txt",
            ],
        ) {
            Verdict::Fail(msg) => {
                assert!(msg.contains("native generator not built"), "msg: {msg}");
                assert!(
                    msg.contains("cargo build -p generate-names-registry"),
                    "msg: {msg}"
                );
            }
            other => panic!("expected Fail, got {other:?}"),
        }
    }

    #[test]
    fn unreadable_target_fails_naming_it() {
        let dir = TempDir::new().unwrap();
        let root = dir.path().to_path_buf();
        // A stub binary satisfies resolution (existence only -- it is never
        // executed), so the verdict must come from the unreadable target.
        let bin = root.join("tools/native/target/release/generate-names-registry.exe");
        fs::create_dir_all(bin.parent().unwrap()).unwrap();
        fs::write(&bin, b"").unwrap();
        // No usr/share/mios tree at all: every target is unreadable.
        match regen_and_compare_native(
            &ctx_at(&root),
            "generate-names-registry",
            &["usr/share/mios/names.generated.txt"],
        ) {
            Verdict::Fail(msg) => {
                assert!(msg.contains("names.generated.txt unreadable"), "msg: {msg}");
            }
            other => panic!("expected Fail, got {other:?}"),
        }
    }

    #[test]
    fn clean_tree_pass_executing_the_real_generator_when_built() {
        // Runs the actual generator binary when the workspace has been built
        // (dev machines and the Linux builder both have it); on a fresh
        // checkout with no tools/native build the resolution test above
        // carries the coverage and this reports itself as skipped.
        let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .unwrap()
            .parent()
            .unwrap()
            .parent()
            .unwrap()
            .to_path_buf();
        if resolve_native_generator(&ctx_at(&repo), "generate-names-registry").is_none() {
            eprintln!("skip: tools/native not built in this checkout; see resolve_* tests");
            return;
        }
        let verdict = regen_and_compare_native(
            &ctx_at(&repo),
            "generate-names-registry",
            &[
                "usr/share/mios/names.generated.txt",
                "usr/share/mios/referenced_names.txt",
            ],
        );
        match &verdict {
            Verdict::Pass(msg) => assert!(msg.contains("bytes total"), "msg: {msg}"),
            Verdict::Fail(msg) => panic!("real tree should be clean: {msg}"),
            other => panic!("unexpected verdict: {other:?}"),
        }
    }
}
