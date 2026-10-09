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

        // task_982. This used to test whether a DEBUG BUILD of the generator
        // existed and, if so, return Pass("Names registry projection matches
        // SSOT") -- without running it and without comparing anything. If the
        // binary was absent it skipped instead, so on an ordinary tree the
        // check was silent and on a developer's tree it lied. Both halves of
        // Skip-as-Pass in one function.
        //
        // task_981/task_953 lineage: the generator was ported to the native
        // tools/native/generate-names-registry binary and the Python script
        // strangler-deleted (task_1713); this caller kept the stale .py path
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
    #![allow(clippy::unwrap_used)]
    use super::super::regen::{regen_and_compare_native, resolve_native_generator};
    use super::super::{DriftCtx, Verdict};
    use std::fs;
    #[test]
    fn resolution_uses_current_platform_and_prefers_release() {
        let root = tempfile::tempdir().unwrap();
        let ctx = DriftCtx {
            root: root.path().into(),
            soft: false,
            in_image: false,
            git_ok: true,
            incomplete_tree: false,
        };
        let dir = root.path().join("tools/native/target/release");
        fs::create_dir_all(&dir).unwrap();
        // Even with both formats present, the current platform determines selection.
        fs::write(dir.join("fixture-native"), b"ELF").unwrap();
        fs::write(dir.join("fixture-native.exe"), b"PE").unwrap();
        assert_eq!(
            resolve_native_generator(&ctx, "fixture-native"),
            Some(dir.join(format!("fixture-native{}", std::env::consts::EXE_SUFFIX)))
        );
    }
    #[test]
    fn absent_generator_is_a_failure_without_mutation() {
        let root = tempfile::tempdir().unwrap();
        let ctx = DriftCtx {
            root: root.path().into(),
            soft: false,
            in_image: false,
            git_ok: true,
            incomplete_tree: false,
        };
        fs::write(root.path().join("artifact"), b"before").unwrap();
        let verdict =
            regen_and_compare_native(&ctx, "mios-fixture-deliberately-absent-0710", &["artifact"]);
        assert!(
            matches!(verdict, Verdict::Fail(ref text) if text.contains("native generator not built"))
        );
        assert_eq!(fs::read(root.path().join("artifact")).unwrap(), b"before");
    }
}
