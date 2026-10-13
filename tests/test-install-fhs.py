#!/usr/bin/env python3
# AI-hint: Verify Linux overlay refusal, apply plans and core installer routing with positive and negative controls.
# AI-doc: usr/share/doc/mios/manual/tests.md
"""Linux installer failure reporting and safe command-routing regression tests."""

from __future__ import annotations
import os
import re
import pathlib
import shlex
import subprocess
import sys
import tempfile

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


def test_linux_install_failure_propagation():
    source = pathlib.Path(_ROOT, "installation/mios-install.sh").read_text()
    trigger = _function(source, "trigger_mios_install")
    # Redirect file probes and mutation commands into an isolated fixture.
    trigger = trigger.replace('"/.git"', '"$MIOS_TEST_ROOT/.git"')
    for path in ("/usr/libexec/mios/system-sync-env.sh",
                 "/automation/15-render-quadlets.sh"):
        trigger = trigger.replace(path, "$MIOS_TEST_ROOT" + path)
    trigger = trigger.replace("cd /automation", 'cd "$MIOS_TEST_ROOT/automation"')
    harness = r"""
set -euo pipefail
log_info() { printf '[INFO] %s\n' "$*"; }
log_ok() { printf '[ OK ] %s\n' "$*"; }
log_err() { printf '[FAIL] %s\n' "$*" >&2; }
log_warn() { printf '[WARN] %s\n' "$*" >&2; }
log_phase() { :; }
spin_start() { :; }
spin_stop() { :; }
git() { printf 'git %s\n' "$*" >> "$MIOS_TEST_TRACE"; }
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
        "quadlet": "Quadlet rendering failed",
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
            ("automation/15-render-quadlets.sh", "quadlet"),
        ):
            script = root / name
            script.write_text('#!/bin/bash\n[[ "$MIOS_TEST_FAIL" != ' + failure + ' ]]\n')
            script.chmod(0o755)
        trace = root / "trace"
        for failure in ("", *failures):
            trace.write_text("")
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
                for name, mode in (("config.toml", 0o644), ("fixture-tool", 0o755)):
                    assert (root / "usr/libexec/mios" / name).stat().st_mode & 0o777 == mode
        print("PASS fixture overlay/package/init path and 12 injected failures; modes preserved")


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
    test_linux_install_failure_propagation()
    test_legacy_bootstrap_failures()
    print("[test-install-fhs] PASS: overlay refusal, four substrate plans, fail-closed apply and Linux core dispatch.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
