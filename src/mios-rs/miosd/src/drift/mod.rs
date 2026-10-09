// AI-hint: Native Rust drift-check harness and Check trait registry for miosd.
// AI-related: automation/98-drift-checks.sh, tools/native/mios-drift-runner
use regex::Regex;
use std::collections::HashSet;
use std::fmt;
use std::fs;
use std::path::PathBuf;
use std::process::Command;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Verdict {
    Pass(String),
    Fail(String),
    Skip(String),
}

impl fmt::Display for Verdict {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Verdict::Pass(msg) => write!(f, "[PASS] {}", msg),
            Verdict::Fail(msg) => write!(f, "[FAIL] {}", msg),
            Verdict::Skip(msg) => write!(f, "[SKIP] {}", msg),
        }
    }
}

pub struct DriftCtx {
    pub root: PathBuf,
    pub soft: bool,
    pub in_image: bool,
    pub git_ok: bool,
    pub incomplete_tree: bool,
}

impl DriftCtx {
    pub fn new(root: PathBuf, soft: bool) -> Self {
        let in_image = std::env::var("CTX").is_ok()
            || root.to_string_lossy().starts_with("/tmp/build")
            || root.to_string_lossy().starts_with("C:\\tmp\\build");

        let git_status = Command::new("git")
            .arg("-C")
            .arg(&root)
            .arg("status")
            .arg("--porcelain")
            .output();

        let (git_ok, incomplete_tree) = match git_status {
            Ok(output) if output.status.success() => {
                let stdout = String::from_utf8_lossy(&output.stdout);
                let has_deleted = stdout
                    .lines()
                    .any(|l| l.starts_with(" D") || l.starts_with("D "));
                (true, has_deleted)
            }
            _ => (false, false),
        };

        Self {
            root,
            soft,
            in_image,
            git_ok,
            incomplete_tree,
        }
    }
}

pub trait Check: Send + Sync {
    fn id(&self) -> &'static str;
    fn describe(&self) -> &'static str;
    fn run(&self, ctx: &DriftCtx) -> Verdict;
}

pub struct SSOTParseCheck;
impl Check for SSOTParseCheck {
    fn id(&self) -> &'static str {
        "check_ssot_parse"
    }
    fn describe(&self) -> &'static str {
        "Assert usr/share/mios/mios.toml exists and parses as valid TOML"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        let p = ctx.root.join("usr/share/mios/mios.toml");
        if !p.exists() {
            return Verdict::Fail(format!("SSOT file not found at {}", p.display()));
        }
        match fs::read_to_string(&p) {
            Ok(content) => match content.parse::<toml::Value>() {
                // task_980: an EMPTY file is valid TOML, so "parses" was true of a
                // zero-byte mios.toml and this reported Pass on a tree with no
                // SSOT in it at all. An SSOT with no tables is not an SSOT.
                Ok(v) => match v.as_table().map(|t| t.len()).unwrap_or(0) {
                    0 => Verdict::Fail(format!(
                        "SSOT TOML at {} parses but declares no tables",
                        p.display()
                    )),
                    n => Verdict::Pass(format!(
                        "SSOT TOML at {} parses successfully ({} top-level tables)",
                        p.display(),
                        n
                    )),
                },
                Err(e) => Verdict::Fail(format!("SSOT TOML parse error: {}", e)),
            },
            Err(e) => Verdict::Fail(format!("Failed to read SSOT file: {}", e)),
        }
    }
}

pub struct BackfillCoverageCheck;
impl Check for BackfillCoverageCheck {
    fn id(&self) -> &'static str {
        "check_backfill_coverage"
    }
    fn describe(&self) -> &'static str {
        "Assert all schema emb vector tables are covered in embed_backfill.py"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        let schema_path = ctx.root.join("usr/share/mios/postgres/schema-init.sql");
        let backfill_path = ctx
            .root
            .join("usr/lib/mios/agent-pipe/mios_pipe/memory/embed_backfill.py");

        let schema = match fs::read_to_string(&schema_path) {
            Ok(s) => s,
            Err(e) => return Verdict::Fail(format!("Failed to read schema-init.sql: {}", e)),
        };
        let backfill = match fs::read_to_string(&backfill_path) {
            Ok(b) => b,
            Err(e) => return Verdict::Fail(format!("Failed to read embed_backfill.py: {}", e)),
        };

        let table_re =
            Regex::new(r"(?i)CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([a-zA-Z0-9_.]+)")
                .expect("valid regex");
        let emb_re = Regex::new(r"(?i)emb\s+vector\s*\(").expect("valid regex");
        let alter_re = Regex::new(r"(?i)ALTER\s+TABLE\s+([a-zA-Z0-9_]+)\s+ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?emb\s+vector").expect("valid regex");

        let mut emb_tables = HashSet::new();
        let mut current_table = String::new();

        for line in schema.lines() {
            if let Some(caps) = table_re.captures(line) {
                current_table = caps[1].to_string();
            }
            if let Some(caps) = alter_re.captures(line) {
                let t = caps[1].to_string();
                if !t.contains('.') {
                    emb_tables.insert(t);
                }
                continue;
            }
            if emb_re.is_match(line) && !current_table.is_empty() && !current_table.contains('.') {
                emb_tables.insert(current_table.clone());
            }
        }

        let mut mapped_tables = HashSet::new();
        let pk_map_re = Regex::new(r#""([a-zA-Z0-9_]+)"\s*:"#).expect("valid regex");
        let exempt_re = Regex::new(r#""([a-zA-Z0-9_]+)""#).expect("valid regex");

        let mut in_pk_map = false;
        let mut in_exempt = false;

        for line in backfill.lines() {
            if line.contains("PK_MAP = {") {
                in_pk_map = true;
                continue;
            }
            if line.contains('}') && in_pk_map {
                in_pk_map = false;
            }
            if line.contains("_BACKFILL_EXEMPT = [") {
                in_exempt = true;
                continue;
            }
            if line.contains(']') && in_exempt {
                in_exempt = false;
            }

            if in_pk_map {
                if let Some(caps) = pk_map_re.captures(line) {
                    mapped_tables.insert(caps[1].to_string());
                }
            }
            if in_exempt {
                if let Some(caps) = exempt_re.captures(line) {
                    mapped_tables.insert(caps[1].to_string());
                }
            }
        }

        let mut errors = Vec::new();
        for table in &emb_tables {
            if !mapped_tables.contains(table) {
                errors.push(format!(
                    "Table '{}' has 'emb vector' but is not in PK_MAP or _BACKFILL_EXEMPT",
                    table
                ));
            }
        }

        // task_980: zero embeddable tables made "all covered" vacuously true, so
        // an empty schema-init.sql reported Pass. The shipped schema always has
        // them; finding none means the scan failed, not that the work is done.
        if emb_tables.is_empty() {
            return Verdict::Fail(format!(
                "no 'emb vector' table found in {} -- nothing was compared",
                schema_path.display()
            ));
        }
        if !errors.is_empty() {
            Verdict::Fail(errors.join("\n"))
        } else {
            Verdict::Pass(format!(
                "all {} emb vector table(s) covered",
                emb_tables.len()
            ))
        }
    }
}

pub mod aiplane;
pub mod audit;
pub mod bake;
pub mod boot;
pub mod converge;
pub mod db;
pub mod deploy;
pub mod governance;
pub mod hardcode;
pub mod laws;
pub mod modules;
pub mod names;
pub mod numbering;
pub mod ports;
pub mod projections;
pub mod regen;
pub mod resolver;
pub mod security;
pub mod selftest;
pub mod ssot_lint;
pub mod supply_chain;
pub mod templates;
pub mod version;

use aiplane::{
    AgentPipeBudgetsCheck, CapabilityManifestCheck, HintCoverageCheck, StructuredAIManifestCheck,
    VLLMNameCanonicalCheck,
};
use bake::{BakeBudgetCheck, BakePlanCheck};
use boot::{FirstbootDegradeOpenCheck, GreenbootEnablementCheck};
use converge::{ConvergeSSOTCheck, GuacamoleConsistencyCheck, RouterParityCheck};
use db::{CLISQLSafetyCheck, DBSeedCoverageCheck, DriftProjectionCheck, RBACTiersCheck};
use deploy::{
    BIBConfigCheck, DeployPlaneCheck, InstallerRolesCheck, OCIArchivePathCheck,
    OfflineInstallCheck, Win11VMTemplateCheck,
};
use governance::{LintIsFinalCheck, TargetLanguagesCheck};
use hardcode::{HardcodeLintCheck, HardcodeVersionCheck, HardcodedSSOTLiteralCheck};
use laws::{
    CouncilGateSSOTCheck, EtcDuplicatesCheck, LawEnforcersCheck, NoMkdirInVarCheck,
    QuadletPrivilegeCheck, UsrOverEtcCheck, VarClosureCheck,
};
use modules::{ModuleBoundaryCheck, ModuleLengthCheck, UnwiredModulesCheck};
use names::NamesRegistryCheck;
use numbering::{DAGIntegrityCheck, PipelineNumberingCheck, RoadmapIndexCheck};
use ports::{BarePortLiteralsCheck, ContainerPortsCheck};
use projections::{
    BladeDropinsCheck, BootcInstallProjectionCheck, ChronyProjectionCheck, CockpitProjectionCheck,
    EgressFirewallCheck, IPAEnrollProjectionCheck, KargsProjectionCheck, NutProjectionCheck,
    PodQuadletsCheck, UKICmdlineProjectionCheck,
};
use resolver::{GlobalsImageParityCheck, GlobalsPortsCheck, ResolverParityCheck};
use security::{CLIEvalSafetyCheck, PythonCompileLintCheck, ShellcheckLintCheck};
use selftest::{NegativeTestCoverageCheck, SoftModeNotCommittedCheck};
use ssot_lint::SSOTLintEquivalenceCheck;
use supply_chain::{
    BakeRefDefaultsCheck, ContainerfilePinnedClonesCheck, SBOMMetadataCheck, VendorURLsCheck,
    VendoredAssetsNonStubCheck,
};
use templates::{TemplateConformanceCheck, VerbBackendsCheck, VerbTemplatesCheck};
use version::{RootTomlSubsetCheck, VersionSSOTCheck};

pub struct Registry {
    pub checks: Vec<Box<dyn Check>>,
}

impl Default for Registry {
    fn default() -> Self {
        Self::new()
    }
}

impl Registry {
    pub fn new() -> Self {
        let checks: Vec<Box<dyn Check>> = vec![
            Box::new(SSOTParseCheck),
            Box::new(BackfillCoverageCheck),
            Box::new(PodQuadletsCheck),
            Box::new(EgressFirewallCheck),
            Box::new(BladeDropinsCheck),
            Box::new(KargsProjectionCheck),
            Box::new(ChronyProjectionCheck),
            Box::new(NutProjectionCheck),
            Box::new(IPAEnrollProjectionCheck),
            Box::new(BootcInstallProjectionCheck),
            Box::new(UKICmdlineProjectionCheck),
            Box::new(CockpitProjectionCheck),
            Box::new(LawEnforcersCheck),
            Box::new(QuadletPrivilegeCheck),
            Box::new(CouncilGateSSOTCheck),
            Box::new(UsrOverEtcCheck),
            Box::new(EtcDuplicatesCheck),
            Box::new(NoMkdirInVarCheck),
            Box::new(VarClosureCheck),
            Box::new(ContainerfilePinnedClonesCheck),
            Box::new(SBOMMetadataCheck),
            Box::new(VendoredAssetsNonStubCheck),
            Box::new(BakeRefDefaultsCheck),
            Box::new(VendorURLsCheck),
            Box::new(NamesRegistryCheck),
            Box::new(ResolverParityCheck),
            Box::new(GlobalsPortsCheck),
            Box::new(GlobalsImageParityCheck),
            Box::new(PipelineNumberingCheck),
            Box::new(DAGIntegrityCheck),
            Box::new(RoadmapIndexCheck),
            Box::new(InstallerRolesCheck),
            Box::new(OfflineInstallCheck),
            Box::new(BIBConfigCheck),
            Box::new(DeployPlaneCheck),
            Box::new(OCIArchivePathCheck),
            Box::new(Win11VMTemplateCheck),
            Box::new(TemplateConformanceCheck),
            Box::new(VerbTemplatesCheck),
            Box::new(VerbBackendsCheck),
            Box::new(HardcodeLintCheck),
            Box::new(HardcodeVersionCheck),
            Box::new(HardcodedSSOTLiteralCheck),
            Box::new(ContainerPortsCheck),
            Box::new(BarePortLiteralsCheck),
            Box::new(VersionSSOTCheck),
            Box::new(RootTomlSubsetCheck),
            Box::new(ConvergeSSOTCheck),
            Box::new(GuacamoleConsistencyCheck),
            Box::new(RouterParityCheck),
            Box::new(DBSeedCoverageCheck),
            Box::new(RBACTiersCheck),
            Box::new(CLISQLSafetyCheck),
            Box::new(DriftProjectionCheck),
            Box::new(ModuleBoundaryCheck),
            Box::new(ModuleLengthCheck),
            Box::new(UnwiredModulesCheck),
            Box::new(AgentPipeBudgetsCheck),
            Box::new(VLLMNameCanonicalCheck),
            Box::new(HintCoverageCheck),
            Box::new(StructuredAIManifestCheck),
            Box::new(CapabilityManifestCheck),
            Box::new(CLIEvalSafetyCheck),
            Box::new(ShellcheckLintCheck),
            Box::new(PythonCompileLintCheck),
            Box::new(TargetLanguagesCheck),
            Box::new(LintIsFinalCheck),
            Box::new(FirstbootDegradeOpenCheck),
            Box::new(GreenbootEnablementCheck),
            Box::new(BakePlanCheck),
            Box::new(BakeBudgetCheck),
            Box::new(SSOTLintEquivalenceCheck),
            Box::new(NegativeTestCoverageCheck),
            Box::new(SoftModeNotCommittedCheck),
        ];
        Self { checks }
    }

    pub fn list(&self) {
        println!("[miosd:drift] Registered checks ({}):", self.checks.len());
        for (i, c) in self.checks.iter().enumerate() {
            println!("  {:3}. {:<35} - {}", i + 1, c.id(), c.describe());
        }
    }

    pub fn run_all(&self, ctx: &DriftCtx, filter: Option<&str>) -> bool {
        println!(
            "[miosd:drift] Running native drift checks at root: {}",
            ctx.root.display()
        );
        let mut failed = 0;
        let mut passed = 0;
        let mut skipped = 0;
        let mut executed = 0;
        let mut missing = 0;
        let started = std::time::Instant::now();
        let total = self
            .checks
            .iter()
            .filter(|check| filter.is_none_or(|f| check.id() == f || check.id().contains(f)))
            .count();

        for check in &self.checks {
            if let Some(f) = filter {
                if check.id() != f && !check.id().contains(f) {
                    continue;
                }
            }
            executed += 1;

            let verdict = check.run(ctx);
            match &verdict {
                Verdict::Pass(msg) => {
                    println!(
                        "[miosd:drift] [{executed}/{total} PASS +{}s] {}: {}",
                        started.elapsed().as_secs(),
                        check.id(),
                        msg.replace('\n', "\n    ")
                    );
                    passed += 1;
                }
                Verdict::Fail(msg) => {
                    eprintln!(
                        "[miosd:drift] [{executed}/{total} FAIL +{}s] {}: {}",
                        started.elapsed().as_secs(),
                        check.id(),
                        msg.replace('\n', "\n    ")
                    );
                    failed += 1;
                }
                Verdict::Skip(msg) => {
                    if msg.starts_with("NOT IMPLEMENTED:") {
                        eprintln!(
                            "[miosd:drift] [{executed}/{total} MISSING +{}s] {}: {}",
                            started.elapsed().as_secs(),
                            check.id(),
                            msg
                        );
                        missing += 1;
                    } else {
                        println!(
                            "[miosd:drift] [{executed}/{total} SKIP +{}s] {}: {}",
                            started.elapsed().as_secs(),
                            check.id(),
                            msg
                        );
                        skipped += 1;
                    }
                }
            }
        }

        if let Some(f) = filter {
            if executed == 0 {
                eprintln!(
                    "[miosd:drift] [FAIL] Filter '{}' matched 0 registered checks",
                    f
                );
                return false;
            }
        }

        println!(
            "[miosd:drift] Summary: {executed}/{total} | PASS {passed} FAIL {failed} MISSING {missing} SKIP {skipped} | elapsed {}s | {}",
            started.elapsed().as_secs(), if failed + missing > 0 { if ctx.soft { "ADVISORY: INCOMPLETE/FAILED" } else { "INCOMPLETE/FAILED" } } else if skipped > 0 { "COMPLETE WITH EXCLUSIONS" } else { "COMPLETE" }
        );

        if (failed > 0 || missing > 0 || executed == 0) && !ctx.soft {
            return false;
        }
        true
    }
}

pub fn run_checks_cli(root: &str, soft: bool, list: bool, only: Option<&str>, parity: bool) {
    let ctx = DriftCtx::new(PathBuf::from(root), soft);
    let reg = Registry::new();

    if list {
        reg.list();
        return;
    }

    if parity {
        eprintln!("[miosd:drift] [MISSING] Differential parity requires comparing independent check receipts; running one implementation cannot certify parity");
        std::process::exit(2);
    }

    if !reg.run_all(&ctx, only) && !soft {
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    // Test code: unwrap() is the intended failure mode -- a panic here IS the
    // assertion. Scoped to this module so production unwraps stay lint errors.
    #![allow(clippy::unwrap_used)]

    use super::*;
    use std::fs;
    use tempfile::TempDir;

    struct DummyPassCheck;
    impl Check for DummyPassCheck {
        fn id(&self) -> &'static str {
            "dummy_pass"
        }
        fn describe(&self) -> &'static str {
            "Dummy passing check for unit tests"
        }
        fn run(&self, _ctx: &DriftCtx) -> Verdict {
            Verdict::Pass("OK".into())
        }
    }

    struct DummyFailCheck;
    impl Check for DummyFailCheck {
        fn id(&self) -> &'static str {
            "dummy_fail"
        }
        fn describe(&self) -> &'static str {
            "Dummy failing check for unit tests"
        }
        fn run(&self, _ctx: &DriftCtx) -> Verdict {
            Verdict::Fail("Simulated error".into())
        }
    }

    struct DummyMissingCheck;
    impl Check for DummyMissingCheck {
        fn id(&self) -> &'static str {
            "dummy_missing"
        }
        fn describe(&self) -> &'static str {
            "Unimplemented control"
        }
        fn run(&self, _ctx: &DriftCtx) -> Verdict {
            Verdict::Skip("NOT IMPLEMENTED: control".into())
        }
    }
    #[test]
    fn missing_implementation_and_empty_registry_cannot_report_success(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let temp = tempfile::tempdir()?;
        let ctx = DriftCtx::new(temp.path().into(), false);
        assert!(!Registry {
            checks: vec![Box::new(DummyMissingCheck)]
        }
        .run_all(&ctx, None));
        assert!(!Registry { checks: vec![] }.run_all(&ctx, None));
        Ok(())
    }

    #[test]
    fn test_verdict_display() {
        assert_eq!(
            format!("{}", Verdict::Pass("all good".into())),
            "[PASS] all good"
        );
        assert_eq!(
            format!("{}", Verdict::Fail("broken".into())),
            "[FAIL] broken"
        );
        assert_eq!(
            format!("{}", Verdict::Skip("skipped".into())),
            "[SKIP] skipped"
        );
    }

    #[test]
    fn test_drift_ctx_new() {
        let temp = TempDir::new().unwrap();
        let ctx = DriftCtx::new(temp.path().to_path_buf(), false);
        assert_eq!(ctx.root, temp.path());
        assert!(!ctx.soft);
    }

    #[test]
    fn test_registry_run_all_success() {
        let temp = TempDir::new().unwrap();
        let ctx = DriftCtx::new(temp.path().to_path_buf(), false);
        let reg = Registry {
            checks: vec![Box::new(DummyPassCheck)],
        };
        assert!(reg.run_all(&ctx, None));
    }

    #[test]
    fn test_registry_run_all_failure() {
        let temp = TempDir::new().unwrap();
        let ctx = DriftCtx::new(temp.path().to_path_buf(), false);
        let reg = Registry {
            checks: vec![Box::new(DummyPassCheck), Box::new(DummyFailCheck)],
        };
        assert!(!reg.run_all(&ctx, None));
    }

    #[test]
    fn test_registry_soft_mode_returns_true_on_failure() {
        let temp = TempDir::new().unwrap();
        let ctx = DriftCtx::new(temp.path().to_path_buf(), true);
        let reg = Registry {
            checks: vec![Box::new(DummyFailCheck)],
        };
        assert!(reg.run_all(&ctx, None));
    }

    #[test]
    fn test_registry_unmatched_filter_returns_false() {
        let temp = TempDir::new().unwrap();
        let ctx = DriftCtx::new(temp.path().to_path_buf(), false);
        let reg = Registry {
            checks: vec![Box::new(DummyPassCheck)],
        };
        assert!(!reg.run_all(&ctx, Some("nonexistent_check_id_12345")));
    }

    #[test]
    fn test_registry_matched_filter() {
        let temp = TempDir::new().unwrap();
        let ctx = DriftCtx::new(temp.path().to_path_buf(), false);
        let reg = Registry {
            checks: vec![Box::new(DummyPassCheck), Box::new(DummyFailCheck)],
        };
        assert!(reg.run_all(&ctx, Some("dummy_pass")));
        assert!(!reg.run_all(&ctx, Some("dummy_fail")));
    }

    #[test]
    fn test_ssot_parse_check_with_hermetic_tempdir() {
        let temp = TempDir::new().unwrap();
        let mios_dir = temp.path().join("usr/share/mios");
        fs::create_dir_all(&mios_dir).unwrap();
        let mios_toml = mios_dir.join("mios.toml");

        let ctx = DriftCtx::new(temp.path().to_path_buf(), false);
        let check = SSOTParseCheck;

        // Failing case: file missing
        assert!(matches!(check.run(&ctx), Verdict::Fail(_)));

        // Passing case: valid TOML
        fs::write(&mios_toml, "[meta]\nmios_version = \"0.3.0\"\n").unwrap();
        assert!(matches!(check.run(&ctx), Verdict::Pass(_)));

        // Failing case: invalid TOML
        fs::write(&mios_toml, "invalid_toml = [[[").unwrap();
        assert!(matches!(check.run(&ctx), Verdict::Fail(_)));
    }
}
