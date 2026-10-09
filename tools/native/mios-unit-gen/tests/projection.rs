// AI-hint: Tests systemd unit projection against the shipped units in usr/lib/systemd/system.

//! The projection contract: `[units.*]` in the SSOT must RENDER the units the
//! tree ships. What replaced `golden_master.rs`, which diffed
//! `usr/lib/systemd/system` against `tests/golden/` -- a byte copy of that same
//! tree. It could only fail when someone forgot to update the copy, which is
//! what eventually happened, and it never once called the renderer.

use mios_unit_gen::{
    drift_register, project, project_deployment, render_blade_dropins, render_blade_karg,
    render_cockpit, render_ipa_enroll, render_uki_cmdline, DeploymentKind, BLADE_KARG,
    COCKPIT_CONF, IPA_ENROLL_ENV, UKI_CMDLINE,
};
use std::fs;
use std::path::{Path, PathBuf};

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../..")
}

fn ssot() -> String {
    std::fs::read_to_string(root().join("usr/share/mios/mios.toml")).expect("SSOT is readable")
}

#[test]
fn test_declared_units_render_or_are_registered() {
    let p = project(&root()).expect("projection runs");
    let register = drift_register(&ssot()).expect("register parses");

    let unexpected: Vec<&String> = p.drifted.iter().filter(|f| !register.contains(f)).collect();
    assert!(
        unexpected.is_empty(),
        "[units.*] no longer renders these units, and they are not in \
         [unit_projection].drift -- either fix the declaration or register the debt: {unexpected:#?}"
    );
}

#[test]
fn test_the_register_only_shrinks() {
    let p = project(&root()).expect("projection runs");
    let register = drift_register(&ssot()).expect("register parses");

    let stale: Vec<&String> = register.iter().filter(|f| !p.drifted.contains(f)).collect();
    assert!(
        stale.is_empty(),
        "these units render faithfully now -- drop them from [unit_projection].drift. \
         A register that keeps entries it no longer needs stops measuring the debt \
         and starts hiding the next one: {stale:#?}"
    );
}

#[test]
fn test_every_declared_unit_exists_on_disk() {
    let p = project(&root()).expect("projection runs");
    assert!(
        p.missing.is_empty(),
        "[units.*] declares units the tree does not ship: {:#?}",
        p.missing
    );
}

/// The gate must not be able to pass over an empty set. `[units.*]` covering
/// nothing would make every other assertion here vacuously true.
#[test]
fn test_the_projection_is_not_empty() {
    let p = project(&root()).expect("projection runs");
    let declared = p.faithful.len() + p.drifted.len() + p.missing.len();
    assert!(declared > 0, "[units.*] declares no units at all");
    assert!(
        !p.faithful.is_empty(),
        "not one declared unit renders faithfully -- the renderer is broken, not the SSOT"
    );
}

#[test]
fn test_undeclared_unit_inventory_includes_only_top_level_units() {
    let temp = tempfile::tempdir().unwrap();
    write_fixture(
        temp.path(),
        "usr/share/mios/mios.toml",
        "[units.\"declared.service\".Service]\nExecStart = \"/bin/true\"\n",
    );
    write_fixture(
        temp.path(),
        "usr/lib/systemd/system/declared.service",
        "[Service]\nExecStart=/bin/true\n",
    );
    let names = [
        "authored.mount",
        "authored.service",
        "authored.slice",
        "authored.socket",
        "authored.target",
        "authored.timer",
        "authored.path",
    ];
    for name in names {
        write_fixture(
            temp.path(),
            &format!("usr/lib/systemd/system/{name}"),
            "authored\n",
        );
    }
    write_fixture(
        temp.path(),
        "usr/lib/systemd/system/ignored.conf",
        "not a unit\n",
    );
    write_fixture(
        temp.path(),
        "usr/lib/systemd/system/nested/ignored.service",
        "not top level\n",
    );
    let projection = project(temp.path()).unwrap();
    let mut expected = names.map(str::to_owned).to_vec();
    expected.sort();
    assert_eq!(projection.undeclared, expected);
    assert_eq!(projection.faithful, ["declared.service"]);
    assert!(projection.missing.is_empty());
}

fn blade_ssot(kind: &str) -> String {
    format!("[blade]\ntype = {kind:?}\n[blade.archetypes]\nhybrid = []\nendpoint = []\n")
}

#[test]
fn blade_karg_is_a_single_bare_array_and_marks_its_producer() {
    let body = render_blade_karg(&blade_ssot("endpoint")).unwrap();
    let doc: toml::Value = body.parse().unwrap();
    assert_eq!(doc.as_table().unwrap().len(), 1);
    assert_eq!(
        doc["kargs"].as_array().unwrap(),
        &[toml::Value::String("mios.blade=endpoint".into())]
    );
    assert!(body.contains("DO NOT EDIT"));
    assert!(body.contains("mios-unit-gen blade-karg"));
}

#[test]
fn blade_karg_rejects_empty_missing_or_undeclared_type() {
    for input in [
        "".to_owned(),
        "[blade.archetypes]\nhybrid = []\n".to_owned(),
        blade_ssot(""),
        blade_ssot("   "),
        blade_ssot("nosucharchetype"),
    ] {
        assert!(render_blade_karg(&input).is_err(), "accepted {input:?}");
    }
}

#[test]
fn committed_blade_karg_matches_ssot_and_reader_contract() {
    let body = render_blade_karg(&ssot()).unwrap();
    assert_eq!(
        fs::read_to_string(root().join(BLADE_KARG))
            .unwrap()
            .replace("\r\n", "\n"),
        body
    );
    let doc: toml::Value = ssot().parse().unwrap();
    assert!(body.contains(&format!(
        "mios.blade={}",
        doc["blade"]["type"].as_str().unwrap()
    )));
    let resolver = fs::read_to_string(root().join("usr/lib/mios/blade.sh")).unwrap();
    assert!(resolver.contains("_cmdline_tok mios.blade"));
    assert!(resolver.contains("\"${key}=\"*)"));
    assert!(
        fs::read_to_string(root().join("usr/libexec/mios/role-apply"))
            .unwrap()
            .contains("blade.sh")
    );
}

#[test]
fn capability_projection_preserves_selectors_tolerations_and_and_rules() {
    let input = "[blade.requires]\nzed = [\" gpu-serving \", \"service-plane\", \"\"]\nalpha = \"controller\"\nempty = []\n";
    let out = render_blade_dropins(input).unwrap();
    assert_eq!(out.len(), 5);
    assert!(out["usr/share/mios/dropins/blade-gpu-serving.conf"]
        .contains("ConditionPathExists=/etc/mios/blade.d/gpu-serving"));
    let yaml = &out["usr/share/mios/dropins/k3s-node-selectors.yaml"];
    assert!(yaml.find("  alpha:").unwrap() < yaml.find("  zed:").unwrap());
    assert!(yaml.contains("mios.capability/gpu-serving: \"true\""));
    assert!(yaml.contains("key: \"mios.capability/service-plane\"\n        operator: \"Exists\"\n        effect: \"NoSchedule\""));
    let rules = &out["usr/share/mios/dropins/pcs-location-rules.pcs"];
    assert!(rules.contains(
        "zed rule score=100 mios-cap-gpu-serving eq true and mios-cap-service-plane eq true"
    ));
    assert!(!rules.contains("location empty"));
}

#[test]
fn capability_projection_rejects_paths_and_non_string_values() {
    for input in [
        "[blade.requires]\nx = [42]",
        "[blade.requires]\nx = false",
        "[blade.requires]\nx = [\"../../outside\"]",
    ] {
        assert!(render_blade_dropins(input).is_err(), "accepted {input:?}");
    }
}

fn write_fixture(root: &Path, relative: &str, body: &str) {
    let path = root.join(relative);
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    fs::write(path, body).unwrap();
}

#[test]
fn deployment_check_names_mutated_and_missing_files_without_writing() {
    let temp = tempfile::tempdir().unwrap();
    write_fixture(
        temp.path(),
        "usr/share/mios/mios.toml",
        &blade_ssot("endpoint"),
    );
    assert_eq!(
        project_deployment(temp.path(), DeploymentKind::BladeKarg, false, None).unwrap(),
        1
    );
    project_deployment(temp.path(), DeploymentKind::BladeKarg, true, None).unwrap();
    write_fixture(temp.path(), BLADE_KARG, "kargs = [\"mios.blade=wrong\"]\n");
    let err = project_deployment(temp.path(), DeploymentKind::BladeKarg, true, None)
        .unwrap_err()
        .to_string();
    assert!(err.contains(BLADE_KARG) && err.contains("drifted"), "{err}");
    assert!(fs::read_to_string(temp.path().join(BLADE_KARG))
        .unwrap()
        .contains("wrong"));
    fs::remove_file(temp.path().join(BLADE_KARG)).unwrap();
    let err = project_deployment(temp.path(), DeploymentKind::BladeKarg, true, None)
        .unwrap_err()
        .to_string();
    assert!(
        err.contains(BLADE_KARG) && err.contains("cannot read"),
        "{err}"
    );
}

#[test]
fn deployment_output_root_and_ssot_input_are_independent() {
    let input = tempfile::tempdir().unwrap();
    let output = tempfile::tempdir().unwrap();
    write_fixture(
        input.path(),
        "choice.toml",
        "[blade.requires]\nx = [\"controller\"]\n",
    );
    let path = input.path().join("choice.toml");
    assert_eq!(
        project_deployment(
            output.path(),
            DeploymentKind::BladeDropins,
            false,
            Some(&path)
        )
        .unwrap(),
        3
    );
    project_deployment(
        output.path(),
        DeploymentKind::BladeDropins,
        true,
        Some(&path),
    )
    .unwrap();
    assert!(!input.path().join("usr/share/mios/dropins").exists());
}

#[test]
fn uki_cmdline_preserves_filename_and_token_order_including_duplicates() {
    let temp = tempfile::tempdir().unwrap();
    write_fixture(
        temp.path(),
        "usr/lib/bootc/kargs.d/20-role.toml",
        "kargs = [\"mios.blade=endpoint\", \"quiet\"]\n",
    );
    write_fixture(
        temp.path(),
        "usr/lib/bootc/kargs.d/01-base.toml",
        "kargs = [\n    \"quiet\", \"mios.blade=hybrid\",\n    \"randomize_kstack_offset=on\", \"pti=on\", \"vsyscall=none\", \"spectre_v2=on\", \"spectre_bhi=on\", \"spec_store_bypass_disable=on\", \"l1tf=full,force\", \"gather_data_sampling=force\", \"tsx=off\", \"kvm.nx_huge_pages=force\"\n]\n",
    );
    write_fixture(temp.path(), "usr/lib/bootc/kargs.d/ignored.txt", "not toml");
    assert_eq!(
        render_uki_cmdline(temp.path()).unwrap(),
        "quiet mios.blade=hybrid randomize_kstack_offset=on pti=on vsyscall=none spectre_v2=on spectre_bhi=on spec_store_bypass_disable=on l1tf=full,force gather_data_sampling=force tsx=off kvm.nx_huge_pages=force mios.blade=endpoint quiet\n"
    );
    project_deployment(temp.path(), DeploymentKind::UkiCmdline, false, None).unwrap();
    project_deployment(temp.path(), DeploymentKind::UkiCmdline, true, None).unwrap();
}

#[test]
fn invalid_uki_input_never_erases_the_existing_cmdline() {
    let temp = tempfile::tempdir().unwrap();
    write_fixture(temp.path(), UKI_CMDLINE, "known-good\n");
    for input in ["kargs = [", "kargs = false", "kargs = [42]"] {
        write_fixture(temp.path(), "usr/lib/bootc/kargs.d/01-bad.toml", input);
        let err = project_deployment(temp.path(), DeploymentKind::UkiCmdline, false, None)
            .unwrap_err()
            .to_string();
        assert!(err.contains("01-bad.toml"), "{err}");
        assert_eq!(
            fs::read_to_string(temp.path().join(UKI_CMDLINE)).unwrap(),
            "known-good\n"
        );
    }
}

#[test]
fn ten_cpu_mitigations_survive_clean_uki_cmdline_render() {
    let temp = tempfile::tempdir().unwrap();
    let hardening = r#"kargs = [
    "slab_nomerge",
    "randomize_kstack_offset=on",
    "pti=on",
    "vsyscall=none",
    "spectre_v2=on",
    "spectre_bhi=on",
    "spec_store_bypass_disable=on",
    "l1tf=full,force",
    "gather_data_sampling=force",
    "tsx=off",
    "kvm.nx_huge_pages=force"
]
match-architectures = ["x86_64"]
"#;
    write_fixture(
        temp.path(),
        "usr/lib/bootc/kargs.d/01-mios-hardening.toml",
        hardening,
    );
    let rendered = render_uki_cmdline(temp.path()).unwrap();
    for mitigation in mios_unit_gen::REQUIRED_CPU_MITIGATIONS {
        assert!(
            rendered.contains(mitigation),
            "expected rendered cmdline to contain mitigation argument {mitigation}"
        );
    }
    project_deployment(temp.path(), DeploymentKind::UkiCmdline, false, None).unwrap();
    project_deployment(temp.path(), DeploymentKind::UkiCmdline, true, None).unwrap();
}

#[test]
fn corrupt_uki_dropin_fails_check_mode() {
    let temp = tempfile::tempdir().unwrap();
    write_fixture(temp.path(), UKI_CMDLINE, "known-good\n");
    write_fixture(
        temp.path(),
        "usr/lib/bootc/kargs.d/01-bad.toml",
        "kargs = [ unclosed",
    );
    let err = project_deployment(temp.path(), DeploymentKind::UkiCmdline, true, None)
        .unwrap_err()
        .to_string();
    assert!(err.contains("01-bad.toml"), "{err}");
}

#[test]
fn missing_cpu_mitigations_fail_check_mode() {
    let temp = tempfile::tempdir().unwrap();
    let hardening = r#"kargs = [
    "slab_nomerge",
    "randomize_kstack_offset=on"
]
"#;
    write_fixture(
        temp.path(),
        "usr/lib/bootc/kargs.d/01-mios-hardening.toml",
        hardening,
    );
    write_fixture(
        temp.path(),
        UKI_CMDLINE,
        "slab_nomerge randomize_kstack_offset=on\n",
    );
    let err = project_deployment(temp.path(), DeploymentKind::UkiCmdline, true, None)
        .unwrap_err()
        .to_string();
    assert!(err.contains("missing required CPU mitigation"), "{err}");
}

#[test]
fn custom_dropin_with_contradictory_mitigation_fails_closed() {
    let temp = tempfile::tempdir().unwrap();
    write_fixture(temp.path(), UKI_CMDLINE, "known-good\n");
    write_fixture(
        temp.path(),
        "usr/lib/bootc/kargs.d/02-custom-hardening.toml",
        "kargs = [\"pti=off\"]\n",
    );
    let err = render_uki_cmdline(temp.path()).unwrap_err().to_string();
    assert!(
        err.contains("contradictory CPU mitigation argument forbidden: pti=off"),
        "{err}"
    );
    let deploy_err = project_deployment(temp.path(), DeploymentKind::UkiCmdline, false, None)
        .unwrap_err()
        .to_string();
    assert!(deploy_err.contains("pti=off"), "{deploy_err}");
    assert_eq!(
        fs::read_to_string(temp.path().join(UKI_CMDLINE)).unwrap(),
        "known-good\n"
    );
    let check_err = project_deployment(temp.path(), DeploymentKind::UkiCmdline, true, None)
        .unwrap_err()
        .to_string();
    assert!(check_err.contains("pti=off"), "{check_err}");
}

#[test]
fn renamed_dropin_missing_mitigations_fails_check_mode() {
    let temp = tempfile::tempdir().unwrap();
    write_fixture(
        temp.path(),
        "usr/lib/bootc/kargs.d/02-custom.toml",
        "kargs = [\"slab_nomerge\"]\n",
    );
    write_fixture(temp.path(), UKI_CMDLINE, "slab_nomerge\n");
    let err = project_deployment(temp.path(), DeploymentKind::UkiCmdline, true, None)
        .unwrap_err()
        .to_string();
    assert!(err.contains("missing required CPU mitigation"), "{err}");
}

#[test]
fn override_dropin_with_contradictory_mitigation_fails_closed() {
    let temp = tempfile::tempdir().unwrap();
    write_fixture(
        temp.path(),
        "usr/lib/bootc/kargs.d/01-mios-hardening.toml",
        "kargs = [\"randomize_kstack_offset=on\", \"pti=on\", \"vsyscall=none\", \"spectre_v2=on\", \"spectre_bhi=on\", \"spec_store_bypass_disable=on\", \"l1tf=full,force\", \"gather_data_sampling=force\", \"tsx=off\", \"kvm.nx_huge_pages=force\"]\n",
    );
    write_fixture(
        temp.path(),
        "usr/lib/bootc/kargs.d/99-override.toml",
        "kargs = [\"spectre_v2=off\"]\n",
    );
    let err = render_uki_cmdline(temp.path()).unwrap_err().to_string();
    assert!(err.contains("spectre_v2=off"), "{err}");
    let check_err = project_deployment(temp.path(), DeploymentKind::UkiCmdline, true, None)
        .unwrap_err()
        .to_string();
    assert!(check_err.contains("spectre_v2=off"), "{check_err}");
}

#[test]
fn deployment_cli_writes_then_checks_the_requested_root() {
    let temp = tempfile::tempdir().unwrap();
    write_fixture(
        temp.path(),
        "usr/share/mios/mios.toml",
        &blade_ssot("endpoint"),
    );
    let invoke = |check: bool| {
        let mut cmd = std::process::Command::new(env!("CARGO_BIN_EXE_mios-unit-gen"));
        cmd.args(["blade-karg", "--root"]).arg(temp.path());
        if check {
            cmd.arg("--check");
        }
        cmd.output().unwrap()
    };
    assert!(invoke(false).status.success());
    assert!(invoke(true).status.success());
    write_fixture(temp.path(), BLADE_KARG, "corrupt\n");
    let result = invoke(true);
    assert!(!result.status.success());
    assert!(String::from_utf8_lossy(&result.stderr).contains(BLADE_KARG));
}

#[test]
fn deployment_cli_advertises_modes_and_rejects_incomplete_options() {
    let binary = env!("CARGO_BIN_EXE_mios-unit-gen");
    let advertised = std::process::Command::new(binary)
        .arg("--list-projections")
        .output()
        .unwrap();
    assert!(advertised.status.success());
    assert_eq!(
        String::from_utf8(advertised.stdout).unwrap(),
        "blade-dropins\nblade-karg\nuki-cmdline\ncockpit\nipa-enroll\nbootc-install\nkeybindings\n"
    );
    let temp = tempfile::tempdir().unwrap();
    for options in [vec!["--root"], vec!["--toml", "--check"], vec!["--unknown"]] {
        let result = std::process::Command::new(binary)
            .arg("blade-karg")
            .args(&options)
            .current_dir(temp.path())
            .output()
            .unwrap();
        assert!(!result.status.success(), "accepted {options:?}");
        assert!(String::from_utf8_lossy(&result.stderr).contains(options[0]));
        assert!(!temp.path().join(BLADE_KARG).exists());
    }
}

fn service_ssot() -> &'static str {
    "[cockpit]\nallow_unencrypted = false\nlogin_to = true\nidle_timeout = 15\n\n[identity.ipa]\nenabled = true\nrealm = 'EXAMPLE.INTERNAL'\nserver = 'ipa.example.internal'\ndomain = 'example.internal'\nenroll_principal = 'admin'\notp_file = '/etc/mios/secrets.env'\notp_key = 'MIOS_IPA_OTP'\n"
}

#[test]
fn cockpit_projects_every_declared_setting() {
    let body = render_cockpit(service_ssot()).unwrap();
    assert!(body.contains(
        "[WebService]\nAllowUnencrypted = false\nLoginTo = true\n\n[Session]\nIdleTimeout = 15\n"
    ));
    assert!(body.contains("regenerate with mios-unit-gen cockpit"));
}

#[test]
fn cockpit_rejects_missing_malformed_and_wrongly_typed_settings() {
    for input in [
        String::new(),
        "[cockpit".into(),
        service_ssot().replace("allow_unencrypted = false", "allow_unencrypted = 'false'"),
        service_ssot().replace("idle_timeout = 15", "idle_timeout = -1"),
        service_ssot().replace("idle_timeout = 15", "idle_timeout = '15'"),
        service_ssot().replace("login_to = true\n", ""),
    ] {
        assert!(
            render_cockpit(&input).is_err(),
            "accepted invalid input: {input}"
        );
    }
}

#[test]
fn ipa_projects_all_enrollment_fields_without_a_credential_value() {
    let body = render_ipa_enroll(service_ssot()).unwrap();
    for line in [
        "MIOS_IPA_ENABLED=\"true\"",
        "MIOS_IPA_REALM=\"EXAMPLE.INTERNAL\"",
        "MIOS_IPA_SERVER=\"ipa.example.internal\"",
        "MIOS_IPA_DOMAIN=\"example.internal\"",
        "MIOS_IPA_ENROLL_PRINCIPAL=\"admin\"",
        "MIOS_IPA_OTP_FILE=\"/etc/mios/secrets.env\"",
        "MIOS_IPA_OTP_KEY=\"MIOS_IPA_OTP\"",
    ] {
        assert!(body.lines().any(|s| s == line), "missing {line}");
    }
    assert!(!body.contains("MIOS_IPA_PASSWORD="));
}

#[test]
fn ipa_values_roundtrip_through_bash_without_expanding_shell_syntax() {
    let realm = "quote\" slash\\ dollar$HOME $(printf injected) `printf injected`";
    let input = service_ssot().replace(
        "realm = 'EXAMPLE.INTERNAL'",
        &format!("realm = {}", toml::Value::String(realm.into())),
    );
    let temp = tempfile::tempdir().unwrap();
    write_fixture(
        temp.path(),
        "enroll.env",
        &render_ipa_enroll(&input).unwrap(),
    );
    let result = std::process::Command::new("bash")
        .args([
            "-c",
            "source \"$1\"; printf '%s' \"$MIOS_IPA_REALM\"",
            "mios-test",
        ])
        .arg(temp.path().join("enroll.env"))
        .output()
        .unwrap();
    assert!(
        result.status.success(),
        "{}",
        String::from_utf8_lossy(&result.stderr)
    );
    assert_eq!(String::from_utf8(result.stdout).unwrap(), realm);
}

#[test]
fn invalid_service_settings_do_not_overwrite_existing_outputs() {
    let temp = tempfile::tempdir().unwrap();
    for (kind, path, input) in [
        (
            DeploymentKind::Cockpit,
            COCKPIT_CONF,
            service_ssot().replace("idle_timeout = 15", "idle_timeout = false"),
        ),
        (
            DeploymentKind::IpaEnroll,
            IPA_ENROLL_ENV,
            service_ssot().replace("otp_key = 'MIOS_IPA_OTP'", "otp_key = 'bad-key'"),
        ),
        (
            DeploymentKind::IpaEnroll,
            IPA_ENROLL_ENV,
            service_ssot().replace("enabled = true", "enabled = 1"),
        ),
        (
            DeploymentKind::IpaEnroll,
            IPA_ENROLL_ENV,
            service_ssot().replace("realm = 'EXAMPLE.INTERNAL'", "realm = \"bad\\nrealm\""),
        ),
        (
            DeploymentKind::IpaEnroll,
            IPA_ENROLL_ENV,
            service_ssot().replace("domain = 'example.internal'\n", ""),
        ),
    ] {
        write_fixture(temp.path(), path, "known-good\n");
        write_fixture(temp.path(), "usr/share/mios/mios.toml", &input);
        assert!(project_deployment(temp.path(), kind, false, None).is_err());
        assert_eq!(
            fs::read_to_string(temp.path().join(path)).unwrap(),
            "known-good\n"
        );
    }
}

#[test]
fn service_cli_projects_independent_roots_and_checks_corruption() {
    let temp = tempfile::tempdir().unwrap();
    write_fixture(temp.path(), "source.toml", service_ssot());
    for (mode, path) in [("cockpit", COCKPIT_CONF), ("ipa-enroll", IPA_ENROLL_ENV)] {
        let invoke = |check: bool| {
            let mut command = std::process::Command::new(env!("CARGO_BIN_EXE_mios-unit-gen"));
            command
                .arg(mode)
                .arg("--root")
                .arg(temp.path().join("output"))
                .arg("--toml")
                .arg(temp.path().join("source.toml"));
            if check {
                command.arg("--check");
            }
            command.output().unwrap()
        };
        assert!(invoke(false).status.success());
        assert!(invoke(true).status.success());
        write_fixture(&temp.path().join("output"), path, "corrupt\n");
        let result = invoke(true);
        assert!(!result.status.success());
        assert!(String::from_utf8_lossy(&result.stderr).contains(path));
        assert_eq!(
            fs::read_to_string(temp.path().join("output").join(path)).unwrap(),
            "corrupt\n"
        );
    }
}

#[test]
fn committed_service_configs_match_ssot() {
    for kind in [DeploymentKind::Cockpit, DeploymentKind::IpaEnroll] {
        assert_eq!(project_deployment(&root(), kind, true, None).unwrap(), 1);
    }
}

#[test]
fn bootc_install_config_projects_install_and_rejects_unknown_filesystems() {
    let ok =
        mios_unit_gen::render_bootc_install("[bootc_install]\nroot_fs_type = \"btrfs\"\n").unwrap();
    assert!(ok.contains("[install]\nroot-fs-type = \"btrfs\"\n"), "{ok}");
    assert!(
        mios_unit_gen::render_bootc_install("[bootc_install]\nroot_fs_type = \"zfs\"\n").is_err()
    );
    assert!(mios_unit_gen::render_bootc_install("[image]\nref = \"x\"\n").is_err());
    let repart = mios_unit_gen::render_repart_root(
        "[bootc_install]\nroot_fs_type = \"xfs\"\nroot_min_gb = 90\nroot_padding_gb = 2\n",
    )
    .unwrap();
    assert!(
        repart.contains("SizeMinBytes=90G\nPaddingMinBytes=2G\nFormat=xfs\n"),
        "{repart}"
    );
    assert!(mios_unit_gen::render_repart_root(
        "[bootc_install]\nroot_fs_type = \"xfs\"\nroot_min_gb = 0\nroot_padding_gb = 2\n"
    )
    .is_err());
}
