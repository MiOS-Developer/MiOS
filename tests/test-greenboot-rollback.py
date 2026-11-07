#!/usr/bin/env python3
# AI-hint: Unit test for greenboot required health check ordering and rollback logic simulation.
# AI-doc: usr/share/doc/mios/manual/tests.md
"""Unit test verifying greenboot required health checks and rollback triggering logic."""

from __future__ import annotations
import itertools
import os
import shutil
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_REQ_DIR = os.path.join(_ROOT, "usr/lib/greenboot/check/required.d")

def test_greenboot_required_checks_exist():
    assert os.path.isdir(_REQ_DIR), f"Required greenboot check directory {_REQ_DIR} missing"
    files = sorted(os.listdir(_REQ_DIR))
    assert len(files) > 0, "No required greenboot check scripts found"
    for f in files:
        if f.endswith(".sh"):
            script_path = os.path.join(_REQ_DIR, f)
            with open(script_path, "r", encoding="utf-8") as fh:
                content = fh.read()
            assert "#!/usr/bin/" in content or "#!/bin/" in content, f"Script {f} missing shebang"

# The composefs check (10-mios-composefs.sh) folds the former 15-composefs-verity.sh into the root
# integrity check. The absolute paths it reads are rewritten to a fixture so every branch runs unprivileged.
_COMPOSEFS = os.path.join(_REQ_DIR, "10-mios-composefs.sh")
_COMPOSEFS_PATHS = ("/run/ostree-booted", "/usr/lib/ostree/prepare-root.conf", "/usr/libexec/mios/verify-root.sh")
_COMPOSEFS_INPUTS = ("booted", "verify_fails", "verity", "mounted", "info", "sealed")


def _stub(path, body):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("#!/usr/bin/bash\n" + body + "\n")
    os.chmod(path, 0o755)


def _run_composefs(text, booted, verify_fails, verity, mounted, info, sealed):
    """(exit status, stdout) of the check over one fixture host."""
    with tempfile.TemporaryDirectory(prefix="mios-composefs-") as tmp:
        fixture = dict(zip(_COMPOSEFS_PATHS, (os.path.join(tmp, n) for n in ("ostree-booted", "prepare-root.conf", "verify-root.sh"))))
        bindir = os.path.join(tmp, "bin")
        os.mkdir(bindir)
        os.symlink(shutil.which("grep"), os.path.join(bindir, "grep"))
        if booted:
            open(fixture["/run/ostree-booted"], "w").close()
        with open(fixture["/usr/lib/ostree/prepare-root.conf"], "w", encoding="utf-8") as fh:
            fh.write("[composefs]\nenabled = %s\n" % ("verity" if verity else "yes"))
        _stub(fixture["/usr/libexec/mios/verify-root.sh"], 'echo "[verify-root] probed"; exit %d' % verify_fails)
        _stub(os.path.join(bindir, "mount"), 'echo "x on / type %s (ro)"' % ("composefs" if mounted else "xfs"))
        _stub(os.path.join(bindir, "fsverity"), "exit %d" % (not sealed))
        if info:
            _stub(os.path.join(bindir, "composefs-info"), "exit 0")
        for real, fake in fixture.items():
            assert real in text, f"10-mios-composefs.sh no longer reads {real}; the fixture would not apply"
            text = text.replace(real, fake)
        script = os.path.join(tmp, "check.sh")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write(text)
        res = subprocess.run([shutil.which("bash"), script], capture_output=True, text=True, env={"PATH": bindir})
        return res.returncode, res.stdout


def _composefs_defects(text):
    """Every fixture host where the check disagrees with the two checks it folds; [] when it is faithful.

    Either half failing fails the check, and both halves always report: a failed integrity probe
    still shows the verity state.
    """
    defects = []
    for case in itertools.product((False, True), repeat=len(_COMPOSEFS_INPUTS)):
        c = dict(zip(_COMPOSEFS_INPUTS, case))
        rc, out = _run_composefs(text, **c)
        fails = (c["booted"] and c["verify_fails"]) or (
            c["verity"] and (not c["mounted"] or (c["info"] and not c["sealed"])))
        expect = ["[verify-root] probed" if c["booted"] else "skipping ostree composefs check",
                  "composefs verity is enabled" if c["verity"] else "composefs verity not requested"]
        if c["verity"]:
            expect.append("root is mounted as composefs" if c["mounted"] else "composefs requested but not active")
        if c["verity"] and c["mounted"] and c["info"]:
            expect.append("fsverity is active" if c["sealed"] else "fsverity missing on critical binary")
        missing = [m for m in expect if m not in out]
        if (rc != 0) != bool(fails) or missing:
            defects.append(f"{c}: exit {rc}, expected {'failure' if fails else 'success'}; missing {missing}")
    return defects


def test_greenboot_verity_check_failure_behavior():
    assert os.path.isfile(_COMPOSEFS), "10-mios-composefs.sh missing"
    with open(_COMPOSEFS, "r", encoding="utf-8") as fh:
        content = fh.read()
    assert "exit 1" in content, "10-mios-composefs.sh must be capable of exiting non-zero on fault"


def test_greenboot_composefs_fold_two_sided():
    """GREEN: the folded check fails exactly where the root or verity check did. RED: planted folds that
    swallow the integrity failure, stop before the verity half, or pass an inactive composefs."""
    with open(_COMPOSEFS, encoding="utf-8") as fh:
        text = fh.read()
    defects = _composefs_defects(text)
    assert defects == [], "\n".join(defects)
    planted = {
        "swallowed integrity failure": ("|| rc=$?", "|| true"),
        "verity half skipped": ("/usr/libexec/mios/verify-root.sh || rc=$?", "exec /usr/libexec/mios/verify-root.sh"),
        "inactive composefs passes": ('requested but not active"\n    exit 1', 'requested but not active"\n    exit "$rc"'),
    }
    for name, (old, new) in planted.items():
        assert text.count(old) == 1, f"planted {name}: {old!r} not found once"
        assert _composefs_defects(text.replace(old, new)), f"planted {name} passed the fold check"


def test_greenboot_composefs_lints_clean():
    """[drift.lint] does not cover usr/lib/greenboot: the check is held to the changed-shell bar here."""
    shellcheck = shutil.which("shellcheck")
    if not shellcheck:
        assert os.environ.get("MIOS_DRIFT_REQUIRE_TOOLS") != "1", "shellcheck is required under MIOS_DRIFT_REQUIRE_TOOLS=1"
        print("[test-greenboot-rollback] SKIP: shellcheck is not installed; the composefs check was not linted")
        return
    with open(_COMPOSEFS, encoding="utf-8") as fh:
        text = fh.read()
    with tempfile.TemporaryDirectory(prefix="mios-composefs-lint-") as tmp:
        for body, clean in ((text, True), (text.replace("rc=0\n", "rc=0\nplanted_unused=1\n", 1), False)):
            path = os.path.join(tmp, "check.sh")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(body)
            res = subprocess.run([shellcheck, "--severity=warning", path], capture_output=True, text=True)
            assert (res.returncode == 0) == clean, res.stdout + res.stderr
            assert clean or "SC2034" in res.stdout, res.stdout

def test_greenboot_max_attempts_config():
    conf_path = os.path.join(_ROOT, "usr/lib/greenboot/greenboot.conf")
    assert os.path.isfile(conf_path), "greenboot.conf missing"
    with open(conf_path, "r", encoding="utf-8") as fh:
        content = fh.read()
    assert "GREENBOOT_MAX_BOOT_ATTEMPTS=3" in content, "GREENBOOT_MAX_BOOT_ATTEMPTS not set to 3"

def main() -> int:
    print("[test-greenboot-rollback] Running greenboot health check & rollback verification...")
    test_greenboot_required_checks_exist()
    test_greenboot_verity_check_failure_behavior()
    test_greenboot_composefs_fold_two_sided()
    test_greenboot_composefs_lints_clean()
    test_greenboot_max_attempts_config()
    print("[test-greenboot-rollback] PASS: Verified greenboot health check setup.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
