// AI-hint: Golden capture harness (insta + trycmd) for build phase artifacts (AGY-963).
// AI-related: src/mios-rs/mios-build/src/lib.rs, automation/34-render-quadlets.sh, automation/35-render-ports.sh, automation/42-chrony-render.sh, automation/43-nut-render.sh, automation/75-kargs-render.sh, automation/76-uki-render.sh, automation/85-bake-plan.sh

use insta::assert_snapshot;
use mios_build::PhaseRegistry;
use std::fs;
use std::path::PathBuf;
use std::process::Command;

fn get_python_exe() -> &'static str {
    if Command::new("py").arg("--version").output().is_ok() {
        "py"
    } else if Command::new("python3").arg("--version").output().is_ok() {
        "python3"
    } else {
        "python"
    }
}

fn fixture_path() -> PathBuf {
    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    manifest_dir
        .join("tests")
        .join("fixtures")
        .join("mios.toml")
}

fn normalize_newlines(s: &str) -> String {
    s.replace("\r\n", "\n")
}

fn ssot_path() -> PathBuf {
    // CARGO_MANIFEST_DIR is src/mios-rs/mios-build; the SSOT is three levels up.
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("..")
        .join("..")
        .join("usr/share/mios/mios.toml")
}

/// The registry is whatever mios.toml says it is.
///
/// This test used to snapshot `default_registry()` -- a six-phase hardcoded
/// list -- and call it golden, which blessed the very fallback that made a
/// six-of-seventy-one-phase build look complete. A golden over a constant
/// proves the constant has not changed, not that the loader works.
#[test]
fn test_phase_registry_loads_from_ssot() {
    let registry = PhaseRegistry::load_from_toml(&ssot_path()).expect("SSOT phase registry loads");
    let phases = registry.phases();
    assert!(
        phases.len() > 60,
        "expected the full pipeline from SSOT, got {} phases -- a short list is the \
         silent-degradation shape this test exists to catch",
        phases.len()
    );
    assert_eq!(phases[0].script, "01-system-files-overlay.sh");
    assert_eq!(
        phases[phases.len() - 1].script,
        "99-postcheck.sh",
        "postcheck must be last; it is the bake-time law gate"
    );
    // Ordinals identify scripts. Execution follows the SSOT dependency order:
    // the sandbox consumes installed native tools and the verified font archive.
    // Sorting IDs would undo the required native -> fonts -> sandbox sequence.
    let mut scripts = std::collections::BTreeSet::new();
    for phase in phases {
        assert!(scripts.insert(&phase.script), "duplicate selected script {}", phase.script);
    }
    let positions: Vec<_> = ["55-native-build.sh", "56-fonts.sh", "54-bake-coderun-sandbox.sh"]
        .iter().map(|script| phases.iter().position(|phase| phase.script == *script).expect("required build dependency is registered")).collect();
    assert!(positions.windows(2).all(|pair| pair[0] < pair[1]), "native tools and fonts must precede the sandbox: {positions:?}");
}

/// Each way of not having a registry is an error, not a short build.
#[test]
fn test_phase_registry_refuses_rather_than_defaults() {
    let dir = tempfile::tempdir().expect("create temp dir");

    let absent = dir.path().join("nonexistent.toml");
    assert!(
        PhaseRegistry::load_from_toml(&absent).is_err(),
        "a missing manifest must be an error"
    );

    let unparseable = dir.path().join("bad.toml");
    fs::write(&unparseable, "this is not [ valid toml").expect("write fixture");
    assert!(
        PhaseRegistry::load_from_toml(&unparseable).is_err(),
        "an unparseable manifest must be an error"
    );

    let silent = dir.path().join("no-phases.toml");
    fs::write(&silent, "[build]\nrechunk_max_layers = 67\n").expect("write fixture");
    assert!(
        PhaseRegistry::load_from_toml(&silent).is_err(),
        "a manifest with no [build.phases].list must be an error"
    );

    let empty = dir.path().join("empty-list.toml");
    fs::write(&empty, "[build.phases]\nlist = []\n").expect("write fixture");
    assert!(
        PhaseRegistry::load_from_toml(&empty).is_err(),
        "an empty phase list must be an error"
    );
}

#[test]
fn test_golden_chrony_render_42() {
    let temp_dir = tempfile::tempdir().expect("create temp dir");
    let conf_path = temp_dir.path().join("chrony.conf");
    let py_exe = get_python_exe();
    let fixture = fixture_path();

    let script = concat!(
        "import os, sys, tomllib\n",
        "toml_path, conf_path = sys.argv[1], sys.argv[2]\n",
        "with open(toml_path, 'rb') as f:\n",
        "    config = tomllib.load(f)\n",
        "servers = config.get('network', {}).get('ntp', {}).get('servers', [])\n",
        "lines = [\n",
        "    '# AI-hint: NTP configuration for Chrony. Generated from mios.toml [network.ntp] SSOT.',\n",
        "    '# DO NOT EDIT -- edit mios.toml [network.ntp] and run automation/42-chrony-render.sh',\n",
        "    ''\n",
        "]\n",
        "for s in servers:\n",
        "    lines.append(f'server {s} iburst')\n",
        "lines.extend([\n",
        "    '',\n",
        "    '# Record the rate at which the system clock gains/losses time.',\n",
        "    'driftfile /var/lib/chrony/drift',\n",
        "    '',\n",
        "    '# Allow the system clock to be stepped in the first three updates',\n",
        "    '# if its offset is larger than 1 second. (Disabled in WSL2 where Hyper-V handles coarse sync)',\n",
        "    'makestep 0 0',\n",
        "    'maxslewrate 500',\n",
        "    '',\n",
        "    '# Hyper-V PTP clock reference when available (WSL2 / VM container host)',\n",
        "    'refclock PHC /dev/ptp0 poll 3 dpoll -2 offset 0 minsamples 4 prefer trust',\n",
        "    '',\n",
        "    '# Enable kernel synchronization of the real-time clock (RTC).',\n",
        "    'rtcsync',\n",
        "    '',\n",
        "    '# Specify directory for log files.',\n",
        "    'logdir /var/log/chrony'\n",
        "])\n",
        "with open(conf_path, 'w', encoding='utf-8') as fh:\n",
        "    fh.write('\\n'.join(lines) + '\\n')\n"
    );

    let output = Command::new(py_exe)
        .arg("-c")
        .arg(script)
        .arg(&fixture)
        .arg(&conf_path)
        .output()
        .expect("run python chrony render");

    assert!(
        output.status.success(),
        "chrony render failed: {:?}",
        output
    );
    let rendered = fs::read_to_string(&conf_path).expect("read chrony.conf");
    let normalized = normalize_newlines(&rendered);
    assert_snapshot!(normalized, @r#"
    # AI-hint: NTP configuration for Chrony. Generated from mios.toml [network.ntp] SSOT.
    # DO NOT EDIT -- edit mios.toml [network.ntp] and run automation/42-chrony-render.sh

    server time.cloudflare.com iburst
    server pool.ntp.org iburst

    # Record the rate at which the system clock gains/losses time.
    driftfile /var/lib/chrony/drift

    # Allow the system clock to be stepped in the first three updates
    # if its offset is larger than 1 second. (Disabled in WSL2 where Hyper-V handles coarse sync)
    makestep 0 0
    maxslewrate 500

    # Hyper-V PTP clock reference when available (WSL2 / VM container host)
    refclock PHC /dev/ptp0 poll 3 dpoll -2 offset 0 minsamples 4 prefer trust

    # Enable kernel synchronization of the real-time clock (RTC).
    rtcsync

    # Specify directory for log files.
    logdir /var/log/chrony
    "#);
}

#[test]
fn test_golden_nut_render_43() {
    let temp_dir = tempfile::tempdir().expect("create temp dir");
    let py_exe = get_python_exe();
    let fixture = fixture_path();

    let script = concat!(
        "import os, sys, tomllib\n",
        "toml_path, conf_dir = sys.argv[1], sys.argv[2]\n",
        "with open(toml_path, 'rb') as f:\n",
        "    config = tomllib.load(f)\n",
        "ups_conf = config.get('power', {}).get('ups', {})\n",
        "name = str(ups_conf.get('name', '')).strip()\n",
        "driver = str(ups_conf.get('driver', 'usbhid-ups')).strip()\n",
        "port = str(ups_conf.get('port', 'auto')).strip()\n",
        "desc = str(ups_conf.get('desc', 'MiOS Uninterruptible Power Supply')).strip()\n",
        "os.makedirs(conf_dir, exist_ok=True)\n",
        "mode = 'standalone' if name else 'none'\n",
        "with open(os.path.join(conf_dir, 'nut.conf'), 'w', encoding='utf-8') as f:\n",
        "    f.write('# AI-hint: NUT framework mode. Generated from mios.toml [power.ups] SSOT.\\nMODE=' + mode + '\\n')\n",
        "with open(os.path.join(conf_dir, 'ups.conf'), 'w', encoding='utf-8') as f:\n",
        "    f.write('# AI-hint: NUT drivers configuration. Generated from mios.toml [power.ups] SSOT.\\n\\n[' + name + ']\\n    driver = ' + driver + '\\n    port = ' + port + '\\n    desc = \"' + desc + '\"\\n')\n"
    );

    let output = Command::new(py_exe)
        .arg("-c")
        .arg(script)
        .arg(&fixture)
        .arg(temp_dir.path())
        .output()
        .expect("run python nut render");

    assert!(output.status.success(), "nut render failed: {:?}", output);
    let nut_conf = fs::read_to_string(temp_dir.path().join("nut.conf")).expect("read nut.conf");
    let ups_conf = fs::read_to_string(temp_dir.path().join("ups.conf")).expect("read ups.conf");

    assert_snapshot!(normalize_newlines(&nut_conf), @r#"
    # AI-hint: NUT framework mode. Generated from mios.toml [power.ups] SSOT.
    MODE=standalone
    "#);
    assert_snapshot!(normalize_newlines(&ups_conf), @r#"
    # AI-hint: NUT drivers configuration. Generated from mios.toml [power.ups] SSOT.

    [mios-ups]
        driver = usbhid-ups
        port = auto
        desc = "MiOS Uninterruptible Power Supply"
    "#);
}

#[test]
fn test_golden_kargs_render_75() {
    let temp_dir = tempfile::tempdir().expect("create temp dir");
    let py_exe = get_python_exe();
    let fixture = fixture_path();

    let script = concat!(
        "import os, sys, tomllib\n",
        "toml_path, kargs_dir = sys.argv[1], sys.argv[2]\n",
        "with open(toml_path, 'rb') as f:\n",
        "    config = tomllib.load(f)\n",
        "kargs_conf = config.get('kargs', {})\n",
        "custom_kargs = []\n",
        "mapping = [('hugepages', 'hugepages='), ('isolcpus', 'isolcpus='), ('nohz_full', 'nohz_full='), ('rcu_nocbs', 'rcu_nocbs='), ('THP', 'transparent_hugepage=')]\n",
        "for key, flag in mapping:\n",
        "    v = str(kargs_conf.get(key, '')).strip()\n",
        "    if v:\n",
        "        custom_kargs.append(flag + v)\n",
        "lines = ['# AI-hint: Configures custom kernel arguments from mios.toml [kargs] SSOT.', 'kargs = [']\n",
        "for k in custom_kargs:\n",
        "    lines.append('    \"' + k + '\",')\n",
        "if lines[-1].endswith(','):\n",
        "    lines[-1] = lines[-1][:-1]\n",
        "lines.append(']')\n",
        "with open(os.path.join(kargs_dir, '99-mios-kargs.toml'), 'w', encoding='utf-8') as f:\n",
        "    f.write('\\n'.join(lines) + '\\n')\n"
    );

    let output = Command::new(py_exe)
        .arg("-c")
        .arg(script)
        .arg(&fixture)
        .arg(temp_dir.path())
        .output()
        .expect("run python kargs render");

    assert!(output.status.success(), "kargs render failed: {:?}", output);
    let rendered =
        fs::read_to_string(temp_dir.path().join("99-mios-kargs.toml")).expect("read kargs");
    assert_snapshot!(normalize_newlines(&rendered), @r#"
    # AI-hint: Configures custom kernel arguments from mios.toml [kargs] SSOT.
    kargs = [
        "hugepages=1024",
        "isolcpus=2-7",
        "nohz_full=2-7",
        "rcu_nocbs=2-7",
        "transparent_hugepage=never"
    ]
    "#);
}
