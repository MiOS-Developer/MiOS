#!/usr/bin/env python3
# AI-hint: Verify Linux overlay refusal, apply plans and core installer routing with positive and negative controls.
# AI-doc: usr/share/doc/mios/manual/tests.md
"""Linux installer failure reporting and safe command-routing regression tests."""

from __future__ import annotations
import json
import os
import re
import pathlib
import shlex
import stat
import subprocess
import sys
import tempfile
import tomllib

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_INSTALL_FHS = os.path.join(_ROOT, "automation", "install-fhs.sh")

def test_empty_dir_failure():
    with tempfile.TemporaryDirectory(prefix="mios-test-fhs-") as tmpdir:
        # Create empty automation subdir inside tmpdir
        auto_dir = os.path.join(tmpdir, "automation")
        os.makedirs(auto_dir, exist_ok=True)
        script_copy = os.path.join(auto_dir, "install-fhs.sh")
        with open(_INSTALL_FHS, "r", encoding="utf-8") as src, open(script_copy, "w", encoding="utf-8") as dst:
            dst.write(src.read())
        os.chmod(script_copy, 0o755)

        # Run non-root check (should hit EUID check or empty dir fail if mocked)
        proc = subprocess.run(["bash", script_copy], capture_output=True, text=True)
        assert proc.returncode != 0, f"Expected non-zero exit code when run on empty dir/non-root, got 0\n{proc.stdout}"

def test_apply_stage_selection():
    apply = os.path.join(_ROOT, "automation", "mios-apply")
    skip = {"01-system-files-overlay.sh", "97-ssot-lint.sh",
            "98-drift-checks.sh", "99-postcheck.sh"}
    classes = {}
    for name in sorted(os.listdir(os.path.dirname(apply))):
        if re.fullmatch(r"[0-9][0-9]-.*\.sh", name) and name not in skip:
            with open(os.path.join(os.path.dirname(apply), name), encoding="utf-8") as src:
                match = re.search(r"^# MIOS_APPLY_CLASS=([a-z-]+)", src.read(), re.M)
            classes[name] = match.group(1) if match else "universal"
    assert "universal" in classes.values(), "fixture must contain universal stages"
    for substrate, allowed in (
        ("oci-bake", {"universal", "bake-only", "dev-only", "boot-only"}),
        ("bootc-live", {"universal", "boot-only"}),
        ("wsl-dev", {"universal", "dev-only"}),
        ("fhs-host", {"universal", "boot-only"}),
    ):
        env = dict(os.environ, MIOS_SUBSTRATE=substrate)
        result = subprocess.run(["bash", apply, "--list-stages"],
                                env=env, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        expected = [name for name, cls in classes.items() if cls in allowed]
        assert expected and result.stdout.splitlines() == expected, (
            substrate, expected, result.stdout)
        rejected = subprocess.run(["bash", apply], env=env,
                                  capture_output=True, text=True)
        assert rejected.returncode != 0, "unimplemented apply reported success"
        assert "[FAIL]" in rejected.stderr and "not implemented" in rejected.stderr
    for args, substrate in (
        (["--list-stages"], "unknown-substrate"),
        ([], "unknown-substrate"),
        (["--list-stages", "extra"], "fhs-host"),
        (["--unknown"], "fhs-host"),
    ):
        result = subprocess.run(["bash", apply, *args],
                                env=dict(os.environ, MIOS_SUBSTRATE=substrate),
                                capture_output=True, text=True)
        assert result.returncode == 2 and "[FAIL]" in result.stderr, result


def test_linux_core_dispatch():
    installer = pathlib.Path(_ROOT, "installation", "mios-install.sh")
    for mode in ("fhs", "bootc"):
        result = subprocess.run(
            ["bash", str(installer), "_install_core", "--type", mode, "--dry-run"],
            capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        assert f"do_install_core {mode}" in result.stdout
        assert f"INSTALL_MODE={mode}" in result.stdout
    rejected = subprocess.run(
        ["bash", str(installer), "_install_core", "--type", "invalid", "--dry-run"],
        capture_output=True, text=True)
    assert rejected.returncode != 0 and "must be fhs or bootc" in rejected.stderr

    # Exercise the real fedora -> internal re-entry -> function launch using a
    # fixture whose core writes a receipt, then fails. No host mutation occurs.
    with tempfile.TemporaryDirectory(prefix="mios-installer-dispatch-") as tmp:
        root = pathlib.Path(tmp)
        install_dir = root / "installation"
        install_dir.mkdir()
        common = installer.with_name("mios-common.sh").read_text()
        common = re.sub(r"mios_self_elevate\(\) \{.*?^}",
                        "mios_self_elevate() { return 0; }", common,
                        flags=re.M | re.S)
        (install_dir / "mios-common.sh").write_text(common)
        source = installer.read_text()
        fixture = re.sub(
            r"do_install_core\(\) \{.*?^}",
            'do_install_core() { printf "core=%s mode=%s\\n" "$1" "$INSTALL_MODE"; return 37; }',
            source, flags=re.M | re.S)
        assert fixture != source, "core fixture replacement must match"
        script = install_dir / "mios-install.sh"
        script.write_text(fixture)
        planned = subprocess.run(["bash", str(script), "fedora", "--dry-run"],
                                 capture_output=True, text=True)
        assert planned.returncode == 0, planned.stderr
        command_line = planned.stdout.split("Command:", 1)[1].strip()
        command = shlex.split(command_line)
        assert command[-2:] == ["_install_core", "fhs"], command
        result = subprocess.run(command, capture_output=True, text=True)
        assert result.returncode == 37, result
        assert "core=fhs mode=fhs" in result.stdout, result
        # Negative control: restore the undefined resolver call in the fixture.
        script.write_text(fixture.replace(
            '_install_core) resolve_install_core ',
            '_install_core) missing_install_core '))
        result = subprocess.run(command, capture_output=True, text=True)
        assert result.returncode == 127 and "missing_install_core" in result.stderr
        assert "core=fhs" not in result.stdout



def _function(source, name):
    match = re.search(r"^" + name + r"\(\) \{.*?^}", source, re.M | re.S)
    assert match, name
    return match.group(0)


def _operator_write(source, home, password):
    """Only the production writer runs; NSS/sudo select a private synthetic account."""
    stub = r"""
set -euo pipefail
FIXTURE_HOME="$1"
log_ok() { printf '[ OK ] %s\n' "$*"; }
log_err() { printf '[FAIL] %s\n' "$*" >&2; }
getent() {
    [[ "$1" == passwd && "$2" == fixop ]] || return 90
    printf 'fixop:x:1000:1000::%s:/bin/bash\n' "$FIXTURE_HOME"
}
sudo() {
    [[ "$1" == -u && "$2" == fixop ]] || return 91
    shift 2
    for arg; do
        [[ -z "$USER_PASSWORD" || "$arg" != *"$USER_PASSWORD"* ]] || return 92
    done
    "$@"
}
IFS= read -r -d '' USER_PASSWORD || :
"""
    return subprocess.run(
        ["bash", "-c", stub + _function(source, "write_operator_credential")
         + '\nwrite_operator_credential fixop\n', "_", str(home)],
        input=password, capture_output=True, text=True, timeout=30)


def test_password_prompt_preserves_literal_values():
    source = pathlib.Path(_ROOT, "installation/mios-install.sh").read_text()
    prompt = _function(source, "prompt_password")
    common = 'set -euo pipefail\n_BOLD=""; _RESET=""\nlog_warn() { echo "$*" >&2; }\n'
    for password in ("-n", "-e", "-E", " -n ", "fixture\\literal"):
        for preselected in (True, False):
            setup = 'IFS= read -r -d "" MIOS_PASSWORD || :\n' if preselected else 'MIOS_PASSWORD=""\n'
            data = password if preselected else password + "\n" + password + "\n"
            result = subprocess.run(["bash", "-c", common + prompt + setup + 'prompt_password Password\n'], input=data, capture_output=True, text=True, timeout=10)
            assert result.returncode == 0 and result.stdout == password + "\n", result
            assert password not in result.stderr
    print("PASS password prompt preserves literal echo options, whitespace and backslashes")


def test_operator_credential_safe_toml_update():
    source = pathlib.Path(_ROOT, "installation/mios-install.sh").read_text()
    password = 'fixture "typed" \\ credential\' café 猫'
    cards = [
        "",
        "[ identity ] # keep header\nfullname = 'MiOS User'\n[theme]\nname = 'keep'\n",
        "[identity]\ndefault_password = 'old' # keep selected note\nfullname = 'MiOS User'\n[other]\nkeep = 17\n",
        '[identity]\nfullname="MiOS User"\n[notes]\ntext="""\n[identity]\ndefault_password="decoy"\n"""\n',
        "identity = { fullname='MiOS User', default_password=" + json.dumps(password) + " }\n",
        "identity.default_password=" + json.dumps(password) + "\n[other]\nkeep=9\n",
    ]
    for card in cards:
        with tempfile.TemporaryDirectory(prefix="mios-installer-credential-") as tmp:
            home = pathlib.Path(tmp)
            tier = home / ".config/mios/mios.toml"
            tier.parent.mkdir(parents=True)
            tier.write_text(card)
            before = tomllib.loads(card)
            result = _operator_write(source, home, password)
            assert result.returncode == 0, result.stderr
            expected = dict(before)
            expected["identity"] = {**before.get("identity", {}), "default_password": password}
            assert tomllib.loads(tier.read_text()) == expected
            assert stat.S_IMODE(tier.stat().st_mode) == 0o600
            assert password not in result.stdout + result.stderr
            if "# keep selected note" in card:
                assert "# keep selected note" in tier.read_text()
            first = tier.read_bytes()
            assert _operator_write(source, home, password).returncode == 0
            assert tier.read_bytes() == first
    print("PASS installer credential quoting, spaced headers and unrelated TOML preservation")


def test_operator_credential_refusal_preserves_tier():
    source = pathlib.Path(_ROOT, "installation/mios-install.sh").read_text()
    cases = [
        '[notes]\ntext="""\n[identity]\ndefault_password="decoy"\n"""\n[identity]\nfullname="MiOS User"\n',
        'identity = { default_password="old", fullname="MiOS User" }\n',
        'identity.default_password="old"\n[other]\nkeep=1\n',
        '[identity]\ndefault_password="""old\nmultiline"""\n',
        '[identity]\ndefault_password="unterminated\n',
    ]
    for card in cases:
        with tempfile.TemporaryDirectory(prefix="mios-installer-refusal-") as tmp:
            home = pathlib.Path(tmp)
            tier = home / ".config/mios/mios.toml"
            tier.parent.mkdir(parents=True)
            tier.write_text(card)
            before, mode = tier.read_bytes(), tier.stat().st_mode
            result = _operator_write(source, home, "fixture-new-credential")
            assert result.returncode != 0 and "NOT recorded" in result.stderr, result
            assert "Operator credential recorded:" not in result.stdout
            assert tier.read_bytes() == before and tier.stat().st_mode == mode
            assert sorted(p.name for p in tier.parent.iterdir()) == ["mios.toml"]
            assert "fixture-new-credential" not in result.stdout + result.stderr
    for password in ("", "fixture\nnewline", "fixture\x7fcontrol"):
        with tempfile.TemporaryDirectory(prefix="mios-installer-value-") as tmp:
            home = pathlib.Path(tmp)
            result = _operator_write(source, home, password)
            assert result.returncode != 0
            assert not (home / ".config/mios/mios.toml").exists()
    print("PASS installer refuses unsupported or invalid TOML without replacing it")


def test_operator_credential_refuses_links_and_shadowing():
    source = pathlib.Path(_ROOT, "installation/mios-install.sh").read_text()
    for component in (".config", ".config/mios", ".config/mios/mios.toml", ".config/mios/mios.d"):
        with tempfile.TemporaryDirectory(prefix="mios-installer-link-") as tmp:
            base = pathlib.Path(tmp)
            home, outside = base / "home", base / "outside"
            home.mkdir()
            target = home / component
            target.parent.mkdir(parents=True, exist_ok=True)
            outside.mkdir()
            if component.endswith("mios.toml"):
                victim = outside / "victim.toml"
                victim.write_text('[identity]\ndefault_password="fixture-existing"\n')
                target.symlink_to(victim)
            else:
                victim = outside / "mios.toml"
                victim.write_text('[identity]\ndefault_password="fixture-existing"\n')
                target.symlink_to(outside, target_is_directory=True)
            before = victim.read_bytes()
            result = _operator_write(source, home, "fixture-new-credential")
            assert result.returncode != 0 and "NOT recorded" in result.stderr, result
            assert target.is_symlink() and victim.read_bytes() == before
            assert sorted(p.name for p in outside.iterdir()) == [victim.name]
    for fragment in ('[identity]\ndefault_password="fixture-shadow"\n', 'identity="scalar-shadow"\n', '[identity]\nbroken="unterminated\n'):
        with tempfile.TemporaryDirectory(prefix="mios-installer-fragment-") as tmp:
            home = pathlib.Path(tmp)
            tier = home / ".config/mios/mios.toml"
            fragments = tier.parent / "mios.d"
            fragments.mkdir(parents=True)
            tier.write_text('[identity]\nfullname="MiOS User"\ndefault_password="fixture-existing"\n')
            path = fragments / "90-operator.toml"
            path.write_text(fragment)
            before = tier.read_bytes()
            result = _operator_write(source, home, "fixture-new-credential")
            assert result.returncode != 0 and "90-operator.toml" in result.stderr, result
            assert tier.read_bytes() == before and path.read_text() == fragment
            assert "fixture-new-credential" not in result.stdout + result.stderr
    for kind in ("fifo", "hardlink", "directory"):
        with tempfile.TemporaryDirectory(prefix="mios-installer-file-type-") as tmp:
            home = pathlib.Path(tmp)
            tier = home / ".config/mios/mios.toml"
            tier.parent.mkdir(parents=True)
            victim = home / "victim.toml"
            victim.write_text('[identity]\ndefault_password="fixture-existing"\n')
            if kind == "fifo":
                os.mkfifo(tier)
            elif kind == "hardlink":
                os.link(victim, tier)
            else:
                tier.mkdir()
            before, mode = victim.read_bytes(), tier.lstat().st_mode
            result = _operator_write(source, home, "fixture-new-credential")
            assert result.returncode != 0 and "NOT recorded" in result.stderr, result
            assert victim.read_bytes() == before and tier.lstat().st_mode == mode
    for value in ("", "fixture-new-credential"):
        with tempfile.TemporaryDirectory(prefix="mios-installer-placeholder-") as tmp:
            home = pathlib.Path(tmp)
            tier = home / ".config/mios/mios.toml"
            fragment = tier.parent / "mios.d/90-operator.toml"
            fragment.parent.mkdir(parents=True)
            tier.write_text('[identity]\nfullname="MiOS User"\n')
            fragment.write_text('[identity]\ndefault_password=' + json.dumps(value) + '\n')
            original = fragment.read_bytes()
            result = _operator_write(source, home, "fixture-new-credential")
            assert result.returncode == 0, result.stderr
            assert tomllib.loads(tier.read_text())["identity"]["default_password"] == "fixture-new-credential"
            assert fragment.read_bytes() == original
    print("PASS installer rejects symlinks and effective fragment conflicts before replacement")


def test_operator_tier_survives_seeding_and_reports_failure():
    source = pathlib.Path(_ROOT, "installation/mios-install.sh").read_text()
    with tempfile.TemporaryDirectory(prefix="mios-installer-seed-") as tmp:
        root = pathlib.Path(tmp)
        home, skel = root / "home", root / "skel"
        tier = home / ".config/mios/mios.toml"
        tier.parent.mkdir(parents=True)
        config = home / ".config"
        config.chmod(0o700)
        tier.parent.chmod(0o700)
        fragment = tier.parent / "mios.d/90-operator.toml"
        fragment.parent.mkdir(mode=0o700)
        fragment.write_text('[identity]\nfullname="Private operator label"\n')
        fragment.chmod(0o644)
        private_before = (fragment.read_bytes(), fragment.stat().st_mode,
                          config.stat().st_mode, tier.parent.stat().st_mode,
                          config.stat().st_uid, tier.parent.stat().st_uid)
        skel.mkdir()
        tier.write_text('[identity]\ndefault_password="fixture-existing"\n[theme]\nname="custom"\n')
        (skel / "mios.toml").write_text('[identity]\nfullname="MiOS User"\n')
        passwd = root / "passwd"
        passwd.write_text(f'fixop:x:1000:1000::{home}:/bin/bash\n')
        before = tier.read_bytes()
        common = r"""
set -euo pipefail
FIXTURE_HOME="$1"
LINUX_USER=fixop
log_phase() { :; }
log_info() { :; }
log_warn() { printf '[WARN] %s\n' "$*" >&2; }
log_ok() { printf '[ OK ] %s\n' "$*"; }
log_err() { printf '[FAIL] %s\n' "$*" >&2; }
getent() { printf 'fixop:x:1000:1000::%s:/bin/bash\n' "$FIXTURE_HOME"; }
sudo() { shift 2; "$@"; }
install() {
    local args=()
    while (( $# )); do
        case "$1" in -o|-g) shift 2 ;; *) args+=("$1"); shift ;; esac
    done
    command install "${args[@]}"
}
write_operator_credential() { return 37; }
"""
        seeder = _function(source, "seed_user_skel_for_all_accounts")
        seeder = seeder.replace('/etc/skel/.config/${subdir}', str(root) + '/skel-${subdir}')
        seeder = seeder.replace('/etc/passwd', str(passwd))
        (root / "skel-mios").mkdir()
        (root / "skel-mios/mios.toml").write_text((skel / "mios.toml").read_text())
        stage = _function(source, "stage_user_profile_artifacts").replace('/etc/skel/.config/mios', str(skel))
        seeded = subprocess.run(["bash", "-c", common + seeder + '\nseed_user_skel_for_all_accounts\n', "_", str(home)], capture_output=True, text=True, timeout=30)
        assert seeded.returncode == 0 and tier.read_bytes() == before, seeded
        for _ in range(2):
            seeded = subprocess.run(["bash", "-c", common.replace('return 37;', 'return 0;') + seeder + stage + '\nstage_user_profile_artifacts\n', "_", str(home)], capture_output=True, text=True, timeout=30)
            assert seeded.returncode == 0 and tier.read_bytes() == before, seeded
            assert (fragment.read_bytes(), fragment.stat().st_mode,
                    config.stat().st_mode, tier.parent.stat().st_mode,
                    config.stat().st_uid, tier.parent.stat().st_uid) == private_before
        fresh = root / "fresh"
        fresh.mkdir()
        seeded = subprocess.run(["bash", "-c", common.replace('return 37;', 'return 0;') + seeder + stage + '\nstage_user_profile_artifacts\n', "_", str(fresh)], capture_output=True, text=True, timeout=30)
        assert seeded.returncode == 0, seeded
        assert (fresh / ".config/mios/mios.toml").read_bytes() == (skel / "mios.toml").read_bytes()
        assert stat.S_IMODE((fresh / ".config").stat().st_mode) == 0o700
        assert stat.S_IMODE((fresh / ".config/mios").stat().st_mode) == 0o700
        failed = subprocess.run(["bash", "-c", common + seeder + stage + '\nstage_user_profile_artifacts\n', "_", str(home)], capture_output=True, text=True, timeout=30)
        assert failed.returncode != 0 and "NOT recorded" in failed.stderr, failed
        assert "Linux login password was set" in failed.stderr and "mios.html" in failed.stderr
        assert tier.read_bytes() == before and "Operator credential recorded:" not in failed.stdout
        assert (fragment.read_bytes(), fragment.stat().st_mode,
                config.stat().st_mode, tier.parent.stat().st_mode,
                config.stat().st_uid, tier.parent.stat().st_uid) == private_before
        core = _function(source, "do_install_core")
        stub_core = r"""
initialize_dynamic_branding() { :; }
require_root() { :; }
detect_host_kind() { echo fixture; }
check_network() { :; }
install_prerequisites() { :; }
load_profile_defaults() { :; }
launch_configurator() { :; }
gather_user_choices() { :; }
print_summary() { :; }
trigger_mios_install() { :; }
deploy_system_prompt() { :; }
apply_user_profile() { echo LINUX-PASSWORD-SET; }
reboot_prompt() { echo INCORRECT-REBOOT; }
"""
        failed = subprocess.run(["bash", "-c", common + stub_core + seeder + stage + core + '\ndo_install_core fhs\n', "_", str(home)], capture_output=True, text=True, timeout=30)
        assert failed.returncode != 0 and "Installation incomplete: operator credential NOT recorded" in failed.stderr
        assert "LINUX-PASSWORD-SET" in failed.stdout and "INCORRECT-REBOOT" not in failed.stdout
    print("PASS caller seeding preserves operator tiers and credential failures stop the final install path")


def test_operator_skel_copy_remains_unprivileged_during_parent_swap():
    """A raced parent can redirect only the account's authority, never root's copy."""
    if os.geteuid() != 0:
        print("[SKIP] installer namespace boundary requires the private root fixture lane")
        return
    source = pathlib.Path(_ROOT, "installation/mios-install.sh").read_text()
    for selected in (False, True):
        for planted_root_copy in (False, True):
            # The container's /tmp is private. Its traversable parent allows a
            # real UID drop without changing the proof coordinator's TMPDIR.
            with tempfile.TemporaryDirectory(prefix="mios-installer-namespace-", dir="/tmp") as tmp:
                root = pathlib.Path(tmp)
                root.chmod(0o755)
                home, outside, skel = root / "home", root / "outside", root / "skel-mios"
                tier = home / ".config/mios"
                tier.mkdir(parents=True)
                for path in (home, home / ".config", tier):
                    os.chown(path, 1000, 1000)
                    path.chmod(0o700)
                outside.mkdir(mode=0o700)
                victim = outside / "system-prompt.md"
                victim.write_text("root-controlled fixture")
                victim.chmod(0o600)
                before = (victim.read_bytes(), victim.stat().st_uid, victim.stat().st_gid, victim.stat().st_mode)
                skel.mkdir(mode=0o755)
                (skel / victim.name).write_text("public default fixture")
                passwd = root / "passwd"
                passwd.write_text(f'fixop:x:1000:1000::{home}:/bin/bash\n')
                helper = root / "boundary.py"
                helper.write_text('''import os, sys
role, command = sys.argv[1], sys.argv[2:]
assert command[0] == "install"
if role == "user":
    os.setgroups([])
    os.setgid(1000)
    os.setuid(1000)
if "-d" not in command:
    os.rename(os.environ["FIXTURE_TIER"], os.environ["FIXTURE_TIER"] + ".before-swap")
    os.symlink(os.environ["FIXTURE_OUTSIDE"], os.environ["FIXTURE_TIER"])
os.execvp(command[0], command)
''')
                helper.chmod(0o644)
                common = r'''
set -euo pipefail
LINUX_USER=fixop
log_phase() { :; }
log_info() { :; }
log_warn() { :; }
log_ok() { :; }
log_err() { printf '[FAIL] %s\n' "$*" >&2; }
getent() { printf 'fixop:x:1000:1000::%s:/bin/bash\n' "$FIXTURE_HOME"; }
sudo() { [[ "$1" == -u && "$2" == fixop ]] || return 91; shift 2; "$FIXTURE_PYTHON" "$FIXTURE_HELPER" user "$@"; }
install() { "$FIXTURE_PYTHON" "$FIXTURE_HELPER" root install "$@"; }
write_operator_credential() { :; }
'''
                body = _function(source, "stage_user_profile_artifacts" if selected else "seed_user_skel_for_all_accounts")
                if selected:
                    body = body.replace('/etc/skel/.config/mios', str(skel))
                    body = 'seed_user_skel_for_all_accounts() { :; }\n' + body
                    call = 'stage_user_profile_artifacts'
                    prefix = 'sudo -u "${LINUX_USER}" install -m 0644'
                else:
                    body = body.replace('/etc/skel/.config/${subdir}', str(root) + '/skel-${subdir}').replace('/etc/passwd', str(passwd))
                    call = 'seed_user_skel_for_all_accounts'
                    prefix = 'sudo -u "$u" install -m 0644'
                assert prefix in body
                if planted_root_copy:
                    body = body.replace(prefix, 'install -o 1000 -g 1000 -m 0644')
                env = dict(os.environ, FIXTURE_HOME=str(home), FIXTURE_TIER=str(tier), FIXTURE_OUTSIDE=str(outside), FIXTURE_HELPER=str(helper), FIXTURE_PYTHON=sys.executable)
                result = subprocess.run(["bash", "-c", common + body + '\n' + call + '\n'], env=env, capture_output=True, text=True, timeout=30)
                assert tier.is_symlink(), "fixture must swap exactly at the copy boundary"
                after = (victim.read_bytes(), victim.stat().st_uid, victim.stat().st_gid, victim.stat().st_mode)
                if planted_root_copy:
                    assert result.returncode == 0 and after != before, result
                    assert victim.read_text() == "public default fixture" and victim.stat().st_uid == 1000
                else:
                    assert result.returncode != 0 and after == before, result
    print("PASS raced skel copies cannot replace/chown a root-owned target; planted root-copy control detects it")


def test_linux_install_failure_propagation():
    source = pathlib.Path(_ROOT, "installation/mios-install.sh").read_text()
    trigger = _function(source, "trigger_mios_install")
    # Redirect file probes and mutation commands into an isolated fixture.
    for path in ("/usr/libexec/mios/system-sync-env.sh",
                 "/usr/libexec/mios/miosd", '"/automation/'):
        assert path in trigger, path
        prefix = '"$MIOS_TEST_ROOT' if path.startswith('"') else "$MIOS_TEST_ROOT"
        trigger = trigger.replace(path, prefix + path.lstrip('"'))
    harness = r"""
set -euo pipefail
log_info() { printf '[INFO] %s\n' "$*"; }
log_ok() { printf '[ OK ] %s\n' "$*"; }
log_err() { printf '[FAIL] %s\n' "$*" >&2; }
log_warn() { printf '[WARN] %s\n' "$*" >&2; }
log_phase() { :; }
spin_start() { :; }
spin_stop() { :; }
git() {
    printf 'git %s\n' "$*" >> "$MIOS_TEST_TRACE"
    [[ "$*" != *ls-files* || "$MIOS_TEST_FAIL" != ssot-untracked ]]
}
mios_apply_unit_presets() {
    printf 'presets %s\n' "$*" >> "$MIOS_TEST_TRACE"
    [[ "$MIOS_TEST_FAIL" != presets ]] || return 50
}
mios_root_merge() {
    printf 'root-merge %s\n' "$*" >> "$MIOS_TEST_TRACE"
    [[ "$MIOS_TEST_FAIL" != merge ]] || return 49
}
cp() {
    printf 'copy %s\n' "$*" >> "$MIOS_TEST_TRACE"
    [[ "$MIOS_TEST_FAIL" != copy ]] || return 40
    command cp "$1" "$2" "$MIOS_TEST_ROOT$3"
}
dnf5() {
    printf 'dnf %s\n' "$*" >> "$MIOS_TEST_TRACE"
    case "$1 $*" in
        install*fixture-repo*) [[ "$MIOS_TEST_FAIL" != repos ]] || return 41 ;;
        install*fixture-system*) [[ "$MIOS_TEST_FAIL" != packages ]] || return 42 ;;
        makecache*) [[ "$MIOS_TEST_FAIL" != cache ]] || return 43 ;;
    esac
    printf 'Fixture transaction completed\n'
}
dnf() { dnf5 "$@"; }
systemd-sysusers() {
    printf 'sysusers\n' >> "$MIOS_TEST_TRACE"
    [[ "$MIOS_TEST_FAIL" != sysusers ]]
}
systemd-tmpfiles() {
    printf 'tmpfiles\n' >> "$MIOS_TEST_TRACE"
    [[ "$MIOS_TEST_FAIL" != tmpfiles ]]
}
systemctl() {
    printf 'systemctl %s\n' "$*" >> "$MIOS_TEST_TRACE"
    [[ "$1" != daemon-reload || "$MIOS_TEST_FAIL" != reload ]]
}
chmod() { printf 'CHMOD MUTATION\n' >> "$MIOS_TEST_TRACE"; return 45; }
find() { printf 'FIND MUTATION\n' >> "$MIOS_TEST_TRACE"; return 46; }
_resolve_mios_toml() { printf '%s/mios.toml\n' "$MIOS_TEST_ROOT"; }
get_packages_from_toml() {
    [[ "$MIOS_TEST_FAIL" != repos-resolve ]] || return 47
    printf 'fixture-repo\n'
}
get_all_packages_except() {
    [[ "$MIOS_TEST_FAIL" != packages-resolve ]] || return 48
    [[ "$MIOS_TEST_FAIL" != empty-packages ]] || return 0
    printf 'fixture-system\n'
}
INSTALL_MODE=fhs
MIOS_REPO=fixture-system-repository
BOOTSTRAP_REPO="$MIOS_TEST_ROOT/bootstrap"
DEFAULT_BRANCH=main
MIOS_FHS_TOTAL_ROOT_MERGE=1
"""
    failures = {
        "copy": "Failed to apply bootstrap overlay",
        "repos": "Repository package installation failed",
        "cache": "Repository metadata refresh failed",
        "packages": "System package installation failed",
        "repos-resolve": "Failed to resolve repository packages",
        "packages-resolve": "Failed to resolve system packages",
        "empty-packages": "No system packages resolved",
        "sysusers": "System user provisioning failed",
        "tmpfiles": "Persistent directory provisioning failed",
        "reload": "Systemd daemon reload failed",
        "environment": "Environment synchronization failed",
        "merge": "Failed to merge mios.git",
        "ssot-untracked": "does not track the SSOT",
        "native": "stage 55-native-build.sh failed",
        "quadlet-generate": "stage 33-generate-quadlets.sh failed",
        "quadlet": "stage 34-render-quadlets.sh failed",
        "runtime": "Native runtime catalog is incomplete",
        "stage-missing": "lacks /automation/34-render-quadlets.sh",
        "presets": "MiOS unit enablement failed",
    }
    with tempfile.TemporaryDirectory(prefix="mios-install-failures-") as tmp:
        root = pathlib.Path(tmp)
        for directory in (".git", "bootstrap/etc", "bootstrap/usr/libexec/mios",
                          "etc", "usr/libexec/mios", "automation"):
            (root / directory).mkdir(parents=True)
        for name, mode in (("config.toml", 0o644), ("fixture-tool", 0o755)):
            artifact = root / "bootstrap/usr/libexec/mios" / name
            artifact.write_text("fixture\n")
            artifact.chmod(mode)
        for name, failure in (
            ("usr/libexec/mios/system-sync-env.sh", "environment"),
            ("usr/libexec/mios/miosd", "runtime"),
            ("automation/55-native-build.sh", "native"),
            ("automation/33-generate-quadlets.sh", "quadlet-generate"),
            ("automation/34-render-quadlets.sh", "quadlet"),
        ):
            script = root / name
            script.write_text('#!/bin/bash\necho "' + name + ' $*" >> "$MIOS_TEST_TRACE"\n'
                              '[[ "$MIOS_TEST_FAIL" != ' + failure + ' ]]\n')
            script.chmod(0o755)
        trace = root / "trace"
        last_stage = root / "automation/34-render-quadlets.sh"
        stage_source = last_stage.read_text()
        for failure in ("", *failures):
            trace.write_text("")
            if failure == "stage-missing":
                last_stage.unlink()
            elif not last_stage.exists():
                last_stage.write_text(stage_source)
            env = dict(os.environ, MIOS_TEST_ROOT=tmp,
                       MIOS_TEST_TRACE=str(trace), MIOS_TEST_FAIL=failure)
            result = subprocess.run(
                ["bash", "-c", harness + trigger + "\ntrigger_mios_install\n"],
                env=env, capture_output=True, text=True)
            recorded = trace.read_text()
            assert "MUTATION" not in recorded, recorded
            assert "--skip-unavailable" not in recorded, recorded
            if failure:
                assert result.returncode != 0, (failure, result)
                assert failures[failure] in result.stderr, (failure, result)
                assert "FHS system init complete" not in result.stdout, result
            else:
                assert result.returncode == 0, result
                assert "FHS system init complete" in result.stdout, result
                assert "dnf install -y --best fixture-system" in recorded
                assert "sysusers" in recorded and "tmpfiles" in recorded
                assert "root-merge / fixture-system-repository main" in recorded
                order = [recorded.index(step) for step in (
                    "root-merge", "dnf install -y --best", "tmpfiles",
                    "automation/55-native-build.sh", "automation/33-generate-quadlets.sh",
                    "automation/34-render-quadlets.sh",
                    "usr/libexec/mios/miosd native-runtime-check --root /",
                    "presets /")]
                assert order == sorted(order), recorded
                for name, mode in (("config.toml", 0o644), ("fixture-tool", 0o755)):
                    assert (root / "usr/libexec/mios" / name).stat().st_mode & 0o777 == mode
        print(f"PASS fixture merge/overlay/package/init/native path and {len(failures)} injected failures; modes preserved")


def _roster(scope):
    units = {}
    for preset in sorted(pathlib.Path(_ROOT, f"usr/lib/systemd/{scope}-preset").glob("*mios*.preset")):
        for line in preset.read_text().splitlines():
            words = line.split()
            if len(words) >= 2 and words[0] in ("enable", "disable"):
                units.setdefault(words[1], words[0])
    return units


def test_unit_presets_enable_the_roster():
    """mios_apply_unit_presets enables the real preset roster in a hermetic root (real systemctl --root)."""
    source = pathlib.Path(_ROOT, "installation/mios-install.sh").read_text()
    harness = ("set -euo pipefail\nlog_info() { echo \"[INFO] $*\"; }\n"
               "log_ok() { echo \"[ OK ] $*\"; }\nlog_err() { echo \"[FAIL] $*\" >&2; }\n"
               + _function(source, "mios_apply_unit_presets") + "\nmios_apply_unit_presets \"$1\"\n")
    with tempfile.TemporaryDirectory(prefix="mios-unit-presets-") as tmp:
        root = pathlib.Path(tmp, "root")
        (root / "usr/lib/systemd").mkdir(parents=True)
        (root / "etc").mkdir()
        for sub in ("system", "user", "system-preset", "user-preset"):
            subprocess.run(["cp", "-a", os.path.join(_ROOT, "usr/lib/systemd", sub),
                            str(root / "usr/lib/systemd" / sub)], check=True)

        def states(scope, units):
            flag = ["--global"] if scope == "user" else []
            out = subprocess.run(["systemctl", f"--root={root}", *flag, "is-enabled", "--", *units],
                                 capture_output=True, text=True).stdout.split()
            return dict(zip(units, out))

        checked = {}
        for scope in ("system", "user"):
            roster = _roster(scope)
            present = [u for u in roster if (root / f"usr/lib/systemd/{scope}" / u).exists()]
            before = states(scope, present)
            # A template without DefaultInstance= has nothing to enable.
            checked[scope] = [u for u in present if before[u] in ("enabled", "disabled")
                              and "@." not in u]
            # Pre-fix control: a merged root with no preset step leaves every
            # roster unit disabled (the observed "MiOS services remain disabled").
            assert any(roster[u] == "enable" for u in checked[scope]), (scope, checked)
            assert all(before[u] == "disabled" for u in checked[scope]), (scope, before)
        result = subprocess.run(["bash", "-c", harness, "presets", str(root)],
                                capture_output=True, text=True, timeout=300)
        assert result.returncode == 0, result
        assert "MiOS unit presets applied" in result.stdout, result
        assert "Preset roster units not installed (system)" in result.stdout, result
        for scope in ("system", "user"):
            roster, after = _roster(scope), states(scope, checked[scope])
            wrong = {u: after[u] for u in checked[scope] if after[u] != roster[u] + "d"}
            assert not wrong, (scope, wrong)
        # Negative controls: a failing preset fails, and a root without any
        # roster unit is an error rather than an empty success.
        shims = pathlib.Path(tmp, "shims")
        shims.mkdir()
        (shims / "systemctl").write_text(
            '#!/usr/bin/bash\nfor a; do [ "$a" = preset ] && exit 1; done\nexec /usr/bin/systemctl "$@"\n')
        (shims / "systemctl").chmod(0o755)
        failed = subprocess.run(["bash", "-c", harness, "presets", str(root)], capture_output=True, text=True,
                                env=dict(os.environ, PATH=f"{shims}:{os.environ['PATH']}"), timeout=300)
        assert failed.returncode == 1 and "unit presets failed" in failed.stderr, failed
        assert "presets applied" not in failed.stdout, failed
        empty = pathlib.Path(tmp, "empty")
        (empty / "usr/lib/systemd/system-preset").mkdir(parents=True)
        (empty / "usr/lib/systemd/system-preset/90-mios.preset").write_text("enable mios-absent.service\n")
        none = subprocess.run(["bash", "-c", harness, "presets", str(empty)], capture_output=True, text=True)
        assert none.returncode == 1 and "No MiOS preset roster units" in none.stderr, none
    print(f"PASS unit presets: {len(checked['system'])} system + {len(checked['user'])} user roster units "
          "disabled before, at their preset after; failing preset and empty roster fail")


def test_native_runtime_check_rejects_dynamic_or_missing():
    """The installer's last native gate: miosd native-runtime-check over a disposable root.

    Every catalog path of the tree's SSOT points at one static miosd; then one
    entry becomes a dynamic ELF, then disappears. Both must fail the check.
    """
    import tomllib
    miosd = os.environ.get("MIOS_MIOSD_BIN") or "/usr/libexec/mios/miosd"
    assert os.access(miosd, os.X_OK), f"native miosd required ({miosd}); absence is not a skipped test"
    dynamic = next(p for p in ("/usr/bin/ls", "/bin/ls") if os.path.exists(p))
    ssot = pathlib.Path(_ROOT, "usr/share/mios/mios.toml")
    native = tomllib.loads(ssot.read_text())["build"]["native"]
    categories = native["categories"]
    env = {k: v for k, v in os.environ.items() if k not in ("MIOS_TOML", "MIOS_VENDOR_TOML")}
    with tempfile.TemporaryDirectory(prefix="mios-native-runtime-") as tmp:
        root = pathlib.Path(tmp)
        (root / "usr/share/mios").mkdir(parents=True)
        (root / "usr/share/mios/mios.toml").write_bytes(ssot.read_bytes())
        paths = []
        for category in categories.values():
            directory = root / category["install_dir"].lstrip("/")
            directory.mkdir(parents=True, exist_ok=True)
            for binary in category["binaries"]:
                (directory / binary).symlink_to(miosd)
                paths.append(directory / binary)
                # The production catalog also requires compatibility paths to
                # target the canonical FHS path, not the external fixture ELF.
                aliases = list(category.get("compat_dirs", []))
                if category.get("expose_bin", False):
                    aliases.append("/usr/bin")
                for alias in aliases:
                    if alias == category["install_dir"]:
                        continue
                    alias_directory = root / alias.lstrip("/")
                    alias_directory.mkdir(parents=True, exist_ok=True)
                    (alias_directory / binary).symlink_to(
                        pathlib.PurePosixPath(category["install_dir"]) / binary)

        for name, route in native.get("aliases", {}).items():
            category = next(group for group in categories.values() if route["binary"] in group["binaries"])
            directories = [category["install_dir"], *category.get("compat_dirs", [])]
            if category.get("expose_bin", False):
                directories.append("/usr/bin")
            for directory in set(directories):
                path = root / directory.lstrip("/") / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.symlink_to(pathlib.PurePosixPath(category["install_dir"]) / route["binary"])

        def check():
            return subprocess.run([miosd, "native-runtime-check", "--root", str(root)],
                                  env=env, capture_output=True, text=True, timeout=60)
        result = check()
        assert result.returncode == 0 and "native artifacts verified" in result.stdout, result
        alias = root / "usr/libexec/mios/mios-gen"
        expected = pathlib.PurePosixPath("/usr/bin/mios-gen")
        assert alias.readlink() == expected
        alias.unlink()
        alias.symlink_to("/usr/bin/not-the-canonical-generator")
        result = check()
        assert result.returncode == 1 and "native compatibility" in result.stderr, result
        alias.unlink()
        result = check()
        assert result.returncode == 1 and "native compatibility" in result.stderr, result
        alias.symlink_to(expected)
        assert check().returncode == 0
        victim = root / "usr/bin/mios-gen"
        assert victim in paths
        victim.unlink()
        victim.symlink_to(dynamic)
        result = check()
        assert result.returncode == 1 and "mios-gen" in result.stderr, result
        victim.unlink()
        result = check()
        assert result.returncode == 1 and "mios-gen" in result.stderr, result
    print(f"PASS native runtime check: {len(paths)} catalog paths verified; dynamic, missing and divergent compatibility artifacts fail")


def test_target_prerequisite_failures_stop_the_target():
    """A prerequisite dnf cannot install fails the dispatcher instead of being ignored."""
    source = pathlib.Path(_ROOT, "installation/mios-install.sh").read_text()
    prereqs = _function(source, "resolve_target_prereqs")
    harness = r"""
set -euo pipefail
log_info() { echo "[INFO] $*"; }
log_err() { echo "[FAIL] $*" >&2; }
die() { log_err "$*"; exit 1; }
rpm() { [[ "$2" != podman ]]; }
cargo() { :; }
sudo() { "$@"; }
dnf() { echo "dnf $*" >> "$MIOS_TEST_TRACE"; [[ "$MIOS_TEST_FAIL" != dnf ]]; }
"""
    with tempfile.TemporaryDirectory(prefix="mios-prereqs-") as tmp:
        trace = pathlib.Path(tmp, "trace")
        for failure in ("", "dnf"):
            trace.write_text("")
            result = subprocess.run(
                ["bash", "-c", harness + prereqs +
                 "\nresolve_target_prereqs fedora\necho TARGET-RAN\n"],
                env=dict(os.environ, MIOS_TEST_TRACE=str(trace), MIOS_TEST_FAIL=failure),
                capture_output=True, text=True)
            assert "dnf install -y podman" in trace.read_text(), result
            if failure:
                assert result.returncode == 1, result
                assert "Failed to install prerequisites for target 'fedora': podman" in result.stderr
                assert "TARGET-RAN" not in result.stdout, result
            else:
                assert result.returncode == 0 and "TARGET-RAN" in result.stdout, result
    print("PASS failed prerequisite install stops the target")


OS_RELEASE_FIXTURES = {
    "fedora": ('ID=fedora\nVERSION_ID=44\nPLATFORM_ID="platform:f44"\n', True),
    "mios-overlaid-fedora": ('ID=mios\nID_LIKE="fedora coreos"\nPLATFORM_ID="platform:f44"\n', True),
    "ubuntu": ('ID=ubuntu\nID_LIKE=debian\nVERSION_ID="24.04"\n', False),
    "rhel-family": ('ID="rhel"\nID_LIKE="fedora"\nPLATFORM_ID="platform:el9"\n', False),
    "fedora-substring": ('ID=notfedora\nPLATFORM_ID="platform:f44"\n', False),
    "no-platform": ('ID=fedora\n', False),
    "empty": ("", False),
    "missing": (None, False),
}


def test_unsupported_host_rejected_before_mutation():
    """do_install_core classifies os-release before any package manager or mutation runs."""
    source = pathlib.Path(_ROOT, "installation/mios-install.sh").read_text()
    functions = "\n".join(_function(source, name) for name in (
        "detect_host_kind", "mios_os_release_kind", "do_install_core"))
    assert functions.count("/etc/os-release") == 1, "one os-release reader"
    functions = functions.replace("/etc/os-release", '"$MIOS_TEST_OS_RELEASE"')
    steps = ("check_network", "install_prerequisites", "load_profile_defaults",
             "launch_configurator", "gather_user_choices", "print_summary",
             "trigger_mios_install", "deploy_system_prompt", "apply_user_profile",
             "stage_user_profile_artifacts", "reboot_prompt", "dnf", "dnf5", "git")
    harness = "set -euo pipefail\n" + "".join(
        f'{step}() {{ echo "MUTATION {step}"; exit 44; }}\n' for step in steps) + r"""
log_info() { echo "[INFO] $*"; }
log_err() { echo "[FAIL] $*" >&2; }
log_phase() { :; }
initialize_dynamic_branding() { :; }
require_root() { :; }
bootc() { return 1; }
"""
    with tempfile.TemporaryDirectory(prefix="mios-os-release-") as tmp:
        for name, (text, supported) in OS_RELEASE_FIXTURES.items():
            release = pathlib.Path(tmp, name)
            if text is not None:
                release.write_text(text)
            result = subprocess.run(
                ["bash", "-c", harness + functions + "\ndo_install_core fhs\n"],
                env=dict(os.environ, MIOS_TEST_OS_RELEASE=str(release)),
                capture_output=True, text=True)
            if supported:
                assert result.returncode == 44, (name, result)
                assert "Detected host: fhs-fedora" in result.stdout, (name, result)
                assert "MUTATION check_network" in result.stdout, (name, result)
            else:
                assert result.returncode == 1, (name, result)
                assert "Host is not Fedora" in result.stderr, (name, result)
                assert "MUTATION" not in result.stdout, (name, result)
    print(f"PASS os-release classification over {len(OS_RELEASE_FIXTURES)} fixtures; rejection precedes mutation")


def _bootstrap_root():
    """mios-bootstrap checkout (Law 15): MIOS_BOOTSTRAP_ROOT or the sibling directory."""
    for candidate in (os.environ.get("MIOS_BOOTSTRAP_ROOT"), os.path.join(_ROOT, "..", "mios-bootstrap")):
        if candidate and os.path.isfile(os.path.join(candidate, "field", "MiOS-Field.sh")):
            return pathlib.Path(candidate).resolve()
    return None


def test_bootstrap_launchers_reach_the_installer():
    """bootstrap.sh / install.sh reach installation/mios-install.sh, checked out or piped.

    A PATH shim bounds bash nesting so a launcher loop fails fast (exit 99)
    instead of recursing; a curl shim fails so no network is touched.
    """
    bootstrap = _bootstrap_root()
    if bootstrap is None:
        print("SKIP bootstrap launchers: no mios-bootstrap checkout (set MIOS_BOOTSTRAP_ROOT)")
        return
    with tempfile.TemporaryDirectory(prefix="mios-bootstrap-launch-") as tmp:
        tmp = pathlib.Path(tmp)
        tree, shims, elsewhere = tmp / "bootstrap", tmp / "shims", tmp / "elsewhere"
        subprocess.run(["git", "clone", "-q", str(bootstrap), str(tree)], check=True)
        for name in ("bootstrap.sh", "install.sh", "build-mios.sh", "field", "installation"):
            source = bootstrap / name
            if source.is_dir():
                subprocess.run(["cp", "-a", f"{source}/.", str(tree / name)], check=True)
            else:
                (tree / name).write_bytes(source.read_bytes())
        (tree / "installation/mios-install.sh").write_text(
            '#!/usr/bin/env bash\nprintf \'INSTALLER-REACHED target=%s\\n\' "$1"\nshift\nprintf \'ARG:%s\\n\' "$@"\nexit 37\n')
        for args in (["add", "-A"], ["-c", "user.name=MiOS", "-c", "user.email=mios@example.invalid",
                                     "commit", "-q", "-m", "fixture", "--allow-empty"]):
            subprocess.run(["git", "-C", str(tree), *args], check=True)
        shims.mkdir()
        elsewhere.mkdir()
        # Piped invocations must never adopt executable fragments from cwd.
        (elsewhere / "field").mkdir()
        for shadow in (elsewhere / "install.sh", elsewhere / "field/MiOS-Field.sh"):
            shadow.write_text('#!/usr/bin/bash\necho SHADOW-EXECUTED\nexit 88\n')
        (shims / "git").write_text(
            '#!/usr/bin/bash\ncase "$*" in *https://*|*ssh://*) exit 23;; esac\nexec /usr/bin/git "$@"\n')
        (shims / "bash").write_text(
            "#!/usr/bin/bash\nd=$(( ${MIOS_TEST_DEPTH:-0} + 1 )); export MIOS_TEST_DEPTH=$d\n"
            '[ "$d" -le 8 ] || { echo "RECURSION depth $d" >&2; exit 99; }\n'
            'exec /usr/bin/bash "$@"\n')
        (shims / "curl").write_text(
            '#!/usr/bin/bash\nif [[ -n "${MIOS_TEST_CURL_INSTALL_FILE:-}" && "$*" == *raw.githubusercontent.com/mios-dev/mios-bootstrap/main/install.sh* ]]; then\n'
            'cat "$MIOS_TEST_CURL_INSTALL_FILE"\nelse exit 22; fi\n')
        (shims / "bootc").write_text("#!/usr/bin/bash\nexit 1\n")
        for shim in shims.iterdir():
            shim.chmod(0o755)
        env = dict(os.environ, PATH=f"{shims}:{os.environ['PATH']}", MIOS_NO_COLOR="1")
        env.pop("INSTALL_MODE", None)
        for launcher in ("bootstrap.sh", "install.sh"):
            for mode, repo in (("checkout", None), ("piped", str(tree)),
                               ("piped-unreachable", str(tmp / "missing")),
                               ("piped-fetch-failed", "https://example.invalid/mios-bootstrap.git")):
                run_env = dict(env, MIOS_BOOTSTRAP_REPO=repo) if repo else env
                if mode == "checkout":
                    result = subprocess.run(["bash", str(tree / launcher)], cwd=elsewhere,
                                            env=run_env, capture_output=True, text=True, timeout=60)
                else:
                    result = subprocess.run(["bash"], input=(tree / launcher).read_text(), cwd=elsewhere,
                                            env=run_env, capture_output=True, text=True, timeout=60)
                label = (launcher, mode, result.returncode, result.stdout[-400:], result.stderr[-400:])
                assert "SHADOW-EXECUTED" not in result.stdout, label
                if mode in ("piped-unreachable", "piped-fetch-failed"):
                    assert result.returncode not in (0, 37, 99), label
                    assert "INSTALLER-REACHED" not in result.stdout, label
                else:
                    assert result.returncode == 37, label
                    assert "INSTALLER-REACHED target=fedora" in result.stdout, label
        # Exercise full-checkout acquisition/reuse offline with real Git. The
        # bootstrap alias receives its official install resolver through curl's
        # fixture transport; the selected repository remains the explicit URI.
        for launcher in ("bootstrap.sh", "install.sh"):
            clone = tmp / ("clone-" + launcher)
            clone_env = dict(env, MIOS_BOOTSTRAP_REPO=tree.as_uri(),
                             MIOS_BOOTSTRAP_ROOT=str(clone),
                             MIOS_TEST_CURL_INSTALL_FILE=str(tree / "install.sh"))
            for attempt in range(2):
                result = subprocess.run(["bash"], input=(tree / launcher).read_text(),
                                        cwd=elsewhere, env=clone_env, capture_output=True,
                                        text=True, timeout=60)
                assert result.returncode == 37, (launcher, attempt, result)
                assert "INSTALLER-REACHED target=fedora" in result.stdout, result
            subprocess.run(["git", "-C", str(clone), "config", "remote.origin.url",
                            "https://example.invalid/unrelated.git"], check=True)
            result = subprocess.run(["bash"], input=(tree / launcher).read_text(),
                                    cwd=elsewhere, env=clone_env, capture_output=True,
                                    text=True, timeout=60)
            assert result.returncode not in (0, 37, 99), result
            assert "INSTALLER-REACHED" not in result.stdout, result
        for launcher in ("bootstrap.sh", "install.sh"):
            result = subprocess.run(["bash", str(tree / launcher), "--fixture", "two words"],
                                    cwd=elsewhere, env=dict(env, INSTALL_MODE="bootc"),
                                    capture_output=True, text=True, timeout=60)
            assert result.returncode == 37, result
            assert "INSTALLER-REACHED target=bootc" in result.stdout, result
            assert "ARG:--fixture\nARG:two words\n" in result.stdout, result
        # Standalone launcher files cannot borrow a sibling's backend.
        orphan = tmp / "orphan"
        orphan.mkdir()
        for launcher in ("bootstrap.sh", "install.sh"):
            entry = orphan / launcher
            entry.write_bytes((tree / launcher).read_bytes())
            result = subprocess.run(["bash", str(entry)], cwd=elsewhere, env=env,
                                    capture_output=True, text=True, timeout=60)
            assert result.returncode not in (0, 37, 99), result
            assert "SHADOW-EXECUTED" not in result.stdout, result
        installer = tree / "installation/mios-install.sh"
        installer.unlink()
        outside = tmp / "outside-installer"
        outside.write_text('#!/usr/bin/bash\necho SHADOW-EXECUTED\nexit 88\n')
        installer.symlink_to(outside)
        for entry in (tree / "bootstrap.sh", tree / "install.sh", tree / "field/MiOS-Field.sh"):
            args = ["install"] if entry.name == "MiOS-Field.sh" else []
            result = subprocess.run(["bash", str(entry), *args], cwd=elsewhere, env=env,
                                    capture_output=True, text=True, timeout=60)
            assert result.returncode not in (0, 37, 88, 99), result
            assert "SHADOW-EXECUTED" not in result.stdout, result
        installer.unlink()
        for launcher in ("bootstrap.sh", "install.sh"):
            result = subprocess.run(["bash", str(tree / launcher)], cwd=elsewhere, env=env,
                                    capture_output=True, text=True, timeout=60)
            assert result.returncode not in (0, 37, 99), result
    print("PASS bootstrap checkout/piped entries reach the installer; fetch/missing/escape/sibling/cwd-shadow controls fail closed")



def test_offline_upgrade_runtime_producer():
    """The real phase publishes the canonical executable and compatibility alias."""
    source = pathlib.Path(_ROOT, "automation/50-uupd-installer.sh")
    harness = r"""
set -euo pipefail
source "$MIOS_TEST_PRODUCER_SOURCE"
install_packages() { :; }
systemctl() { :; }
_stage_args() {
    _stage=()
    for item in "$@"; do
        case "$item" in /usr|/usr/*) item="$MIOS_TEST_STAGE$item";; esac
        _stage+=("$item")
    done
}
install() {
    [[ "$MIOS_TEST_FAIL" != directories ]] || return 61
    _stage_args "$@"; command install "${_stage[@]}"
}
cp() {
    [[ "$MIOS_TEST_FAIL" != copy ]] || return 62
    _stage_args "$@"; command cp "${_stage[@]}"
    if [[ "$MIOS_TEST_FAIL" == corrupt ]]; then printf '\ncorrupt\n' >> "${_stage[-1]}"; fi
}
chmod() {
    [[ "$MIOS_TEST_FAIL" != permissions ]] || return 63
    _stage_args "$@"; command chmod "${_stage[@]}"
}
ln() {
    [[ "$MIOS_TEST_FAIL" != link ]] || return 64
    local -a args=("$@")
    local last=$(("${#args[@]}" - 1))
    args[$last]="$MIOS_TEST_STAGE${args[$last]}"
    if [[ "$MIOS_TEST_FAIL" == wrong-link ]]; then
        command ln -sf /not-the-updater "${args[$last]}"
    else
        command ln "${args[@]}"
    fi
}
cmp() { _stage_args "$@"; command cmp "${_stage[@]}"; }
readlink() { _stage_args "$@"; command readlink "${_stage[@]}"; }
install_uupd_subsystem
"""
    with tempfile.TemporaryDirectory(prefix="mios-offline-producer-") as tmp:
        stage = pathlib.Path(tmp)
        for failure in ("", "directories", "copy", "permissions", "link", "corrupt", "wrong-link"):
            root = stage / (failure or "good")
            root.mkdir()
            env = dict(os.environ, MIOS_TEST_STAGE=str(root), MIOS_TEST_FAIL=failure,
                       MIOS_TEST_PRODUCER_SOURCE=str(source))
            result = subprocess.run(["bash", "-c", harness], env=env, capture_output=True,
                                    text=True, timeout=60)
            if failure:
                assert result.returncode != 0, (failure, result)
                assert "Installed /usr/bin/mios-offline-upgrade and" not in result.stdout, (failure, result)
                continue
            assert result.returncode == 0, result
            binary = root / "usr/bin/mios-offline-upgrade"
            alias = root / "usr/libexec/mios/mios-offline-upgrade"
            assert binary.read_bytes() == source.read_bytes()
            assert binary.stat().st_mode & 0o777 == 0o755
            assert alias.readlink() == pathlib.PurePosixPath("/usr/bin/mios-offline-upgrade")
            help_result = subprocess.run(["bash", str(binary), "--help"],
                                         capture_output=True, text=True, timeout=60)
            assert help_result.returncode == 0 and "mios-offline-upgrade" in help_result.stdout, help_result
        unit = pathlib.Path(_ROOT, "usr/lib/systemd/system/mios-offline-upgrade.service").read_text()
        assert "ExecStart=/usr/bin/mios-offline-upgrade --reboot-mode auto" in unit
    print("PASS offline updater producer: exact canonical bytes/mode/link/help; six failed/corrupt publication controls reject success")

def test_legacy_bootstrap_failures():
    source = pathlib.Path(_ROOT, "automation/install-bootstrap.sh").read_text()
    assert source.endswith('main "$@"\n')
    definitions = source[:-len('main "$@"\n')]
    for kind, expected in (("Unsupported", 1), ("Fhs-fedora", 44), ("Bootc", 44)):
        result = subprocess.run(
            ["bash", "-c", definitions + '\nrequire_root() { :; }\n'
             'detect_host_kind() { echo "$MIOS_TEST_KIND"; }\n'
             'check_network() { echo network-reached; exit 44; }\nmain\n'],
            env=dict(os.environ, MIOS_TEST_KIND=kind),
            capture_output=True, text=True)
        assert result.returncode == expected, (kind, result)
        if kind == "Unsupported":
            assert "Host is not Fedora" in result.stderr, result
            assert "network-reached" not in result.stdout
        else:
            assert "network-reached" in result.stdout, result
    hostname = subprocess.run(
        ["bash", "-c", definitions + '\nrequire_root() { :; }\n'
         'detect_host_kind() { echo Fhs-fedora; }\ncheck_network() { :; }\n'
         'prompt_default() { [[ "$1" != Hostname ]] || { echo "hostname-default=$2" >&2; exit 45; }; echo fixture; }\n'
         'main\n'],
        capture_output=True, text=True)
    assert hostname.returncode == 45, hostname
    assert "unbound variable" not in hostname.stderr, hostname
    package_block = re.search(
        r'^    if \[\[ -n "\$\{all_pkgs// \}" \]\]; then.*?^    fi',
        source, re.M | re.S)
    assert package_block, "legacy package install fixture must match"
    for failure in ("", "packages"):
        harness = r"""
set -euo pipefail
all_pkgs=fixture-system
log_info() { :; }
log_ok() { echo "$*"; }
log_err() { echo "[FAIL] $*" >&2; }
dnf5() {
    [[ "$*" != *--skip-unavailable* ]] || exit 63
    [[ "$MIOS_TEST_FAIL" != packages ]] || return 62
}
dnf() { dnf5 "$@"; }
"""
        result = subprocess.run(
            ["bash", "-c", harness + "\nmain() {\n" + package_block.group(0)
             + "\n}\nmain\n"],
            env=dict(os.environ, MIOS_TEST_FAIL=failure),
            capture_output=True, text=True)
        if failure:
            assert result.returncode != 0 and "[FAIL]" in result.stderr, result
            assert "Package stack installation complete" not in result.stdout
        else:
            assert result.returncode == 0, result
            assert "Package stack installation complete" in result.stdout
    print("PASS legacy unsupported-host guard and failed package transaction")


def main() -> int:
    print("[test-install-fhs] Running install-fhs verification...")
    test_empty_dir_failure()
    test_apply_stage_selection()
    test_linux_core_dispatch()
    test_password_prompt_preserves_literal_values()
    test_operator_credential_safe_toml_update()
    test_operator_credential_refusal_preserves_tier()
    test_operator_credential_refuses_links_and_shadowing()
    test_operator_tier_survives_seeding_and_reports_failure()
    test_operator_skel_copy_remains_unprivileged_during_parent_swap()
    test_linux_install_failure_propagation()
    test_unsupported_host_rejected_before_mutation()
    test_unit_presets_enable_the_roster()
    test_native_runtime_check_rejects_dynamic_or_missing()
    test_target_prerequisite_failures_stop_the_target()
    test_bootstrap_launchers_reach_the_installer()
    test_offline_upgrade_runtime_producer()
    test_legacy_bootstrap_failures()
    print("[test-install-fhs] PASS: overlay refusal, four substrate plans, fail-closed apply, Linux core dispatch, failure propagation and host rejection.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
