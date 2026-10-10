#!/usr/bin/env python3
# AI-hint: Two-sided controls for task_3432's second half -- mios-agents-firstboot.sh runs as root but must seed the coder home as that home's owner, never root.
# AI-related: usr/libexec/mios/mios-agents-firstboot.sh, usr/lib/tmpfiles.d/50-mios.conf, usr/lib/systemd/system/mios-agents.service
# AI-functions: run_firstboot_seeders, verify_seeded_ownership, TestAgentsFirstbootOwnership
"""mios-agents.service runs its ExecStartPre (mios-agents-firstboot.sh) as root,
and that script seeds code-server settings and an extension into
/var/lib/mios/agents -- the container's coder home, owned by uid 1000 per
tmpfiles.d/mios-agents.conf. Created as root, those paths leave code-server
(running as the home's owner) unable to write its own User/ state.

Positive control: seeding a uid-1000 scratch home as root leaves every seeded
path owned 1000:1000. Negative control: the pre-fix seeding (plain install -d,
install and cp as root) is planted in a scratch copy and the same check must
name a root-owned path. The script is sourced -- it returns before its build
step when sourced -- with its home, settings and extension sources pointed at
the scratch fixture.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_FIRSTBOOT = os.path.join(_HERE, "mios-agents-firstboot.sh")
_OWNER = 1000


def run_firstboot_seeders(script: str, home: str, ctx: str, ext: str) -> None:
    """Source the firstboot script and run only its seeders against a scratch home."""
    driver = (
        'set -euo pipefail; source "$1"; '
        'AGENTS_HOME="$2"; SETTINGS_SOURCE="$3/code-server-mobile-settings.json"; EXT_SOURCE="$4"; '
        "logger() { :; }; seed_code_server_settings; seed_code_server_extensions"
    )
    subprocess.run(["bash", "-c", driver, "seed", script, home, ctx, ext],
                   check=True, capture_output=True, text=True, timeout=60)


def verify_seeded_ownership(home: str) -> list[str]:
    """Every path seeded under home must carry home's owner."""
    want = os.stat(home)
    errors: list[str] = []
    seeded = 0
    for dirpath, dirnames, filenames in os.walk(home):
        for name in dirnames + filenames:
            path = os.path.join(dirpath, name)
            st = os.lstat(path)
            seeded += 1
            if (st.st_uid, st.st_gid) != (want.st_uid, want.st_gid):
                errors.append(f"{os.path.relpath(path, home)} owned by {st.st_uid}:{st.st_gid}, "
                              f"home owner is {want.st_uid}:{want.st_gid}")
    if seeded == 0:
        errors.append("firstboot seeded nothing into the home (fixture is vacuous)")
    return errors


@unittest.skipUnless(os.geteuid() == 0, "the ownership fixture chowns a scratch home, which needs root")
class TestAgentsFirstbootOwnership(unittest.TestCase):
    def setUp(self) -> None:
        with open(_FIRSTBOOT, "rb") as f:
            self.before = hashlib.sha256(f.read()).hexdigest()
        self.scratch = tempfile.TemporaryDirectory(prefix="t1139_seed_")

    def tearDown(self) -> None:
        self.scratch.cleanup()
        with open(_FIRSTBOOT, "rb") as f:
            self.assertEqual(hashlib.sha256(f.read()).hexdigest(), self.before, "real tree mutated")

    def _seed(self, script: str) -> str:
        root = self.scratch.name
        home, ctx, ext = (os.path.join(root, d) for d in ("home", "ctx", "ext"))
        for d in (home, ctx, ext):
            os.makedirs(d)
        with open(os.path.join(ctx, "code-server-mobile-settings.json"), "w", encoding="utf-8") as f:
            f.write('{"window.zoomLevel": 0}\n')
        with open(os.path.join(ext, "package.json"), "w", encoding="utf-8") as f:
            f.write("{}\n")
        os.chown(home, _OWNER, _OWNER)
        run_firstboot_seeders(script, home, ctx, ext)
        return home

    def test_positive_seeds_as_home_owner(self) -> None:
        home = self._seed(_FIRSTBOOT)
        self.assertEqual(verify_seeded_ownership(home), [])
        for rel in (".local/share/code-server/User/settings.json",
                    ".local/share/code-server/extensions/be5invis.vscode-custom-css/package.json"):
            self.assertTrue(os.path.isfile(os.path.join(home, rel)), rel)

    def test_negative_root_owned_seeding_is_named(self) -> None:
        with open(_FIRSTBOOT, "r", encoding="utf-8") as f:
            text = f.read()
        planted = text.replace(' -o "${owner%:*}" -g "${owner#*:}"', "")
        planted = planted.replace('        chown -R "$(home_owner)" "$ext_target"\n', "")
        self.assertNotEqual(planted, text, "plant did not apply")
        script = os.path.join(self.scratch.name, "firstboot.sh")
        with open(script, "w", encoding="utf-8") as f:
            f.write(planted)
        errs = verify_seeded_ownership(self._seed(script))
        self.assertIn(".local owned by 0:0, home owner is 1000:1000", "\n".join(errs))


def main() -> int:
    """A skipped run is exit 77, never a pass: run-suites.sh fails it unless registered."""
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.TestLoader().loadTestsFromTestCase(TestAgentsFirstbootOwnership))
    if not result.wasSuccessful():
        return 1
    return 77 if result.skipped else 0


if __name__ == "__main__":
    sys.exit(main())
