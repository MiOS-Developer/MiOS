// AI-hint: Heavy regen-and-diff projection checks for miosd drift runner.
// AI-related: tools/native/mios-gen/src/pod_quadlets.rs, tools/native/mios-gen/src/main.rs, automation/98-drift-checks.sh

use super::regen::regen_and_diff_shell;
use super::{Check, DriftCtx, Verdict};

pub struct PodQuadletsCheck;
impl Check for PodQuadletsCheck {
    fn id(&self) -> &'static str {
        "check_pod_quadlets"
    }
    fn describe(&self) -> &'static str {
        "Assert generated pod quadlets match committed quadlet files"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        // Quadlets are emitted into usr/share/containers/systemd (29 units), the
        // path automation/98-drift-checks.sh has always checked. The old
        // usr/share/mios/quadlets has never existed, so this check hard-failed
        // the in-image `miosd drift-check` on every build.
        let output = crate::native_generator::command(&ctx.root, "pod-quadlets", true)
            .and_then(|mut command| command.output().map_err(|e| e.to_string()));
        match output {
            Ok(out) if out.status.success() => {
                Verdict::Pass("Native Quadlet projection matches SSOT".into())
            }
            Ok(out) => Verdict::Fail(format!(
                "Native Quadlet check failed ({}): {} {}",
                out.status,
                String::from_utf8_lossy(&out.stdout).trim(),
                String::from_utf8_lossy(&out.stderr).trim()
            )),
            Err(error) => Verdict::Fail(error),
        }
    }
}

pub struct EgressFirewallCheck;
impl Check for EgressFirewallCheck {
    fn id(&self) -> &'static str {
        "check_egress_firewall"
    }
    fn describe(&self) -> &'static str {
        "Assert generated egress firewall rules match committed egress.nft"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        match crate::native_generator::command(&ctx.root, "egress-firewall", true)
            .and_then(|mut command| command.output().map_err(|e| e.to_string()))
        {
            Ok(out) if out.status.success() => {
                Verdict::Pass("Native egress firewall matches SSOT".into())
            }
            Ok(out) => Verdict::Fail(format!(
                "Native egress firewall check failed ({}): {} {}",
                out.status,
                String::from_utf8_lossy(&out.stdout).trim(),
                String::from_utf8_lossy(&out.stderr).trim()
            )),
            Err(error) => Verdict::Fail(error),
        }
    }
}

pub struct BladeDropinsCheck;
impl Check for BladeDropinsCheck {
    fn id(&self) -> &'static str {
        "check_blade_dropins"
    }
    fn describe(&self) -> &'static str {
        "Assert generated blade systemd dropins match committed files"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        deployment_check(ctx, mios_unit_gen::DeploymentKind::BladeDropins)
    }
}

pub struct KargsProjectionCheck;
impl Check for KargsProjectionCheck {
    fn id(&self) -> &'static str {
        "check_kargs_projection"
    }
    fn describe(&self) -> &'static str {
        "Assert kernel args projection matches committed kargs"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        regen_and_diff_shell(
            ctx,
            "automation/75-kargs-render.sh",
            "KARGS_DIR",
            "usr/lib/bootc/kargs.d",
        )
    }
}

pub struct ChronyProjectionCheck;
impl Check for ChronyProjectionCheck {
    fn id(&self) -> &'static str {
        "check_chrony_projection"
    }
    fn describe(&self) -> &'static str {
        "Assert chrony config projection matches committed file"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        regen_and_diff_shell(
            ctx,
            "automation/42-chrony-render.sh",
            "CHRONY_CONF",
            "etc/chrony.conf",
        )
    }
}

pub struct NutProjectionCheck;
impl Check for NutProjectionCheck {
    fn id(&self) -> &'static str {
        "check_nut_projection"
    }
    fn describe(&self) -> &'static str {
        "Assert NUT config projection matches committed file"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        regen_and_diff_shell(
            ctx,
            "automation/43-nut-render.sh",
            "UPS_CONF_DIR",
            "etc/ups",
        )
    }
}

pub struct IPAEnrollProjectionCheck;
impl Check for IPAEnrollProjectionCheck {
    fn id(&self) -> &'static str {
        "check_ipa_enroll_projection"
    }
    fn describe(&self) -> &'static str {
        "Assert IPA enroll projection matches committed script"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        deployment_check(ctx, mios_unit_gen::DeploymentKind::IpaEnroll)
    }
}

pub struct BootcInstallProjectionCheck;
impl Check for BootcInstallProjectionCheck {
    fn id(&self) -> &'static str {
        "check_bootc_install_projection"
    }
    fn describe(&self) -> &'static str {
        "Assert the bootc install config projection matches mios.toml [bootc_install]"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        deployment_check(ctx, mios_unit_gen::DeploymentKind::BootcInstall)
    }
}

pub struct UKICmdlineProjectionCheck;
impl Check for UKICmdlineProjectionCheck {
    fn id(&self) -> &'static str {
        "check_uki_cmdline_projection"
    }
    fn describe(&self) -> &'static str {
        "Assert UKI kernel cmdline projection matches committed file"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        deployment_check(ctx, mios_unit_gen::DeploymentKind::UkiCmdline)
    }
}

fn deployment_check(ctx: &DriftCtx, kind: mios_unit_gen::DeploymentKind) -> Verdict {
    match mios_unit_gen::project_deployment(&ctx.root, kind, true, None) {
        Ok(count) => Verdict::Pass(format!("{kind:?}: {count} projection(s) match SSOT")),
        Err(error) => Verdict::Fail(error.to_string()),
    }
}

pub struct CockpitProjectionCheck;
impl Check for CockpitProjectionCheck {
    fn id(&self) -> &'static str {
        "check_cockpit_projection"
    }
    fn describe(&self) -> &'static str {
        "Assert Cockpit projection matches committed file"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        deployment_check(ctx, mios_unit_gen::DeploymentKind::Cockpit)
    }
}
